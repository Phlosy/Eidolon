"""The durable loop (R2-b): facts before calls, replayed real fixtures, crash
recovery from the log with a real process dying (os._exit), cancel as a fact,
budgets, and the context/blob/tool building blocks."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from loop_support import PROMPT, SYSTEM, new_spec, replay_provider, weather_tools
from provider_cases import WEATHER_TOOL

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobMissing, BlobStore
from eidolon.agent.context import OUTPUT_CONTRACT, ContextBuilder
from eidolon.agent.loop import AgentRunner
from eidolon.agent.providers import Completed, TextDelta, ToolCall, ToolCallEvent, Usage
from eidolon.agent.providers.accounting import KIND as MODEL_CALLED
from eidolon.agent.tools import Execution, FunctionTools, ToolSpec
from eidolon.db import SessionLocal
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.kernel.store import FactStore

store = FactStore()
HERE = Path(__file__).resolve().parent


@pytest.fixture()
def blobs(tmp_path):
    return BlobStore(tmp_path / "blobs")


def facts_of(db, run_id):
    return store.by_correlation(db, run_id)


def kinds_of(db, run_id):
    return [f.kind for f in facts_of(db, run_id)]


class ScriptedLoop:
    """One scripted answer per model call; unique request ids so each lands as a fact."""

    name = "scripted"

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = 0
        self.last_request = None

    async def complete(self, request):
        self.calls += 1
        self.last_request = request
        answer = self.answers.pop(0)
        for event in answer:
            yield event
        yield Completed(
            stop_reason="tool_use"
            if any(isinstance(e, ToolCallEvent) for e in answer)
            else "end_turn",
            usage=Usage(10, 5),
            model=request.model,
            provider="scripted",
            request_id=f"scripted-{self.calls}",
        )

    async def embed(self, texts, *, model=None):
        raise NotImplementedError


def tool_answer(n):
    return [
        TextDelta("checking"),
        ToolCallEvent(ToolCall(f"c{n}", "get_weather", {"city": "Beijing"})),
    ]


# -- happy path on real recorded fixtures ---------------------------------------------


async def test_run_completes_with_tool_roundtrip_from_replayed_fixtures(db, blobs):
    company = uuid4()
    keys = []
    runner = AgentRunner(SessionLocal, replay_provider(SessionLocal), blobs, weather_tools(keys))
    spec = new_spec(company)
    outcome = await runner.start(spec)

    assert outcome.status == "completed" and outcome.steps == 2, outcome
    assert "22" in outcome.final_text
    assert outcome.usage.input_tokens > 0 and outcome.usage.output_tokens > 0
    assert kinds_of(db, spec.run_id) == [
        R.RUN_STARTED,
        R.STEP_REQUESTED,
        MODEL_CALLED,
        R.STEP_COMPLETED,
        R.TOOL_REQUESTED,
        R.TOOL_COMPLETED,
        R.STEP_REQUESTED,
        MODEL_CALLED,
        R.STEP_COMPLETED,
        R.RUN_COMPLETED,
    ]
    facts = facts_of(db, spec.run_id)
    assert all(f.correlation_id == spec.run_id for f in facts)
    assert all(f.actor == Actor(ActorKind.agent, "eng-1") for f in facts)
    # Causation: a straight chain through the run facts; each model call is caused by
    # the step.requested that preceded it.
    run_facts = [f for f in facts if f.kind != MODEL_CALLED]
    assert run_facts[0].causation_id is None
    assert [f.causation_id for f in run_facts[1:]] == [f.id for f in run_facts[:-1]]
    for model_fact in (f for f in facts if f.kind == MODEL_CALLED):
        cause = next(f for f in facts if f.id == model_fact.causation_id)
        assert cause.kind == R.STEP_REQUESTED
        assert model_fact.payload["run_id"] == str(spec.run_id)
        assert model_fact.payload["mode"] == "replay"
    # Facts hold refs + previews; the full content is in the blob store.
    requested = next(f for f in facts if f.kind == R.STEP_REQUESTED)
    ctx = blobs.get_json(requested.payload["context_ref"])
    assert ctx["system"].startswith(SYSTEM) and OUTPUT_CONTRACT in ctx["system"]
    assert ctx["messages"] == [{"role": "user", "content": PROMPT}]
    completed = [f for f in facts if f.kind == R.STEP_COMPLETED]
    assert completed[0].payload["tool_calls"][0]["name"] == "get_weather"
    assert completed[0].payload["stop_reason"] == "tool_use"
    assistant = blobs.get_json(completed[0].payload["response_ref"])
    assert assistant["tool_calls"][0]["arguments"] == {"city": "Beijing"}
    tool_done = next(f for f in facts if f.kind == R.TOOL_COMPLETED)
    assert tool_done.payload["result_preview"].startswith('{"temperature_c": 22')
    assert tool_done.payload["is_error"] is False
    assert keys == [R.tool_key(spec.run_id, 1, assistant["tool_calls"][0]["id"])]
    assert tool_done.payload["idempotency_key"] == keys[0]
    # The state folded back from the log matches what the loop saw.
    state = runner.load(spec.run_id)
    assert state.status == "completed" and state.steps_completed == 2
    roles = [m.role for m in state.messages(blobs)]
    assert roles == ["user", "assistant", "tool", "assistant"]
    first_turn = state.messages(blobs)[1]
    assert first_turn.tool_calls and first_turn.tool_calls[0].name == "get_weather"
    # provider_state (DeepSeek reasoning_content) is kept verbatim whenever the model sent one
    assert first_turn.provider_state == assistant.get("provider_state")
    assert state.usage_total == outcome.usage

    # Resuming a finished run changes nothing.
    again = await runner.resume(spec.run_id)
    assert again.status == "completed" and kinds_of(db, spec.run_id)[-1] == R.RUN_COMPLETED
    assert len(facts_of(db, spec.run_id)) == 10


# -- crash recovery with a real process -----------------------------------------------


def crash(point, blobs, company, run_id):
    proc = subprocess.run(
        [
            sys.executable,
            str(HERE / "loop_crash_child.py"),
            point,
            str(blobs.root),
            str(company),
            str(run_id),
        ],
        cwd=str(HERE.parent),
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 3, (proc.returncode, proc.stdout, proc.stderr)
    return proc.stdout


async def test_crash_after_step_requested_resumes_with_a_new_attempt(db, blobs):
    company, run_id = uuid4(), uuid4()
    out = crash("after_step_requested", blobs, company, run_id)
    assert "crash: after_step_requested" in out
    assert kinds_of(db, run_id) == [R.RUN_STARTED, R.STEP_REQUESTED]
    state = AgentRunner(SessionLocal, replay_provider(SessionLocal), blobs, weather_tools()).load(
        run_id
    )
    assert state.status == "running" and state.pending_step == (1, 1)

    keys = []
    runner = AgentRunner(SessionLocal, replay_provider(SessionLocal), blobs, weather_tools(keys))
    outcome = await runner.resume(run_id)
    assert outcome.status == "completed" and "22" in outcome.final_text
    kinds = kinds_of(db, run_id)
    assert kinds[:4] == [R.RUN_STARTED, R.STEP_REQUESTED, R.STEP_REQUESTED, MODEL_CALLED]
    assert kinds[-1] == R.RUN_COMPLETED
    requested = [f for f in facts_of(db, run_id) if f.kind == R.STEP_REQUESTED]
    assert [(f.payload["step_no"], f.payload["attempt"]) for f in requested] == [
        (1, 1),
        (1, 2),
        (2, 1),
    ]
    completed = [f for f in facts_of(db, run_id) if f.kind == R.STEP_COMPLETED]
    assert completed[0].payload["attempt"] == 2
    assert len(keys) == 1


async def test_crash_inside_tool_resumes_with_the_same_idempotency_key(db, blobs):
    company, run_id = uuid4(), uuid4()
    out = crash("in_tool", blobs, company, run_id)
    assert "crash: in_tool key=" in out
    child_key = out.split("key=")[1].split()[0]
    assert kinds_of(db, run_id) == [
        R.RUN_STARTED,
        R.STEP_REQUESTED,
        MODEL_CALLED,
        R.STEP_COMPLETED,
        R.TOOL_REQUESTED,
    ]
    state = AgentRunner(SessionLocal, replay_provider(SessionLocal), blobs, weather_tools()).load(
        run_id
    )
    assert [t.idempotency_key for t in state.pending_tools()] == [child_key]

    keys = []
    runner = AgentRunner(SessionLocal, replay_provider(SessionLocal), blobs, weather_tools(keys))
    outcome = await runner.resume(run_id)
    assert outcome.status == "completed" and "22" in outcome.final_text
    assert keys == [child_key]  # the executor was handed the very same key
    kinds = kinds_of(db, run_id)
    assert kinds.count(R.TOOL_REQUESTED) == 1 and kinds.count(R.TOOL_COMPLETED) == 1
    assert kinds[-1] == R.RUN_COMPLETED


# -- cancel, budget, failures (scripted provider; no I/O) ------------------------------


async def test_cancel_is_a_fact_and_stops_at_the_next_checkpoint(db, blobs):
    company = uuid4()
    provider = ScriptedLoop([tool_answer(1), tool_answer(2), [TextDelta("done")]])
    runner_ref = {}

    def cancel_from_inside(execution):
        runner_ref["r"].request_cancel(
            execution_run_id(execution), actor=Actor(ActorKind.human, "owner"), reason="stop it"
        )

    runner = AgentRunner(SessionLocal, provider, blobs, weather_tools(before=cancel_from_inside))
    runner_ref["r"] = runner
    spec = new_spec(company)
    outcome = await runner.start(spec)
    assert outcome.status == "cancelled" and outcome.steps == 1
    assert kinds_of(db, spec.run_id) == [
        R.RUN_STARTED,
        R.STEP_REQUESTED,
        R.STEP_COMPLETED,
        R.TOOL_REQUESTED,
        R.RUN_CANCEL_REQUESTED,
        R.TOOL_COMPLETED,  # the tool in flight finishes and is recorded
        R.RUN_CANCELLED,
    ]
    cancel = next(f for f in facts_of(db, spec.run_id) if f.kind == R.RUN_CANCEL_REQUESTED)
    assert cancel.actor == Actor(ActorKind.human, "owner") and cancel.payload["reason"] == "stop it"
    assert provider.calls == 1  # no second model call after the cancel
    assert (await runner.resume(spec.run_id)).status == "cancelled"


def execution_run_id(execution: Execution):
    from uuid import UUID

    return UUID(execution.run_id)


async def test_budget_max_steps_ends_the_run(db, blobs):
    company = uuid4()
    provider = ScriptedLoop([tool_answer(n) for n in range(1, 10)])
    runner = AgentRunner(SessionLocal, provider, blobs, weather_tools())
    spec = new_spec(company, budget=R.RunBudget(max_steps=2))
    outcome = await runner.start(spec)
    assert outcome.status == "budget_exceeded" and outcome.steps == 2 and provider.calls == 2
    last = facts_of(db, spec.run_id)[-1]
    assert last.kind == R.RUN_BUDGET_EXCEEDED and last.payload["reason"] == "max_steps"

    provider = ScriptedLoop([tool_answer(n) for n in range(1, 10)])
    runner = AgentRunner(SessionLocal, provider, blobs, weather_tools())
    spec = new_spec(company, budget=R.RunBudget(max_steps=50, max_total_tokens=20))
    outcome = await runner.start(spec)
    assert outcome.status == "budget_exceeded" and outcome.steps == 2  # 15 tokens/step


async def test_model_stop_reasons_and_provider_errors_fail_the_run(db, blobs):
    company = uuid4()

    class Truncating(ScriptedLoop):
        async def complete(self, request):
            yield TextDelta("half")
            yield Completed("max_tokens", Usage(1, 1), request.model, "scripted", request_id="t")

    runner = AgentRunner(SessionLocal, Truncating([]), blobs, weather_tools())
    spec = new_spec(company)
    outcome = await runner.start(spec)
    assert outcome.status == "failed" and "max_tokens" in outcome.error
    assert kinds_of(db, spec.run_id)[-1] == R.RUN_FAILED

    # Replay with no fixture for this prompt: ProviderError → run.failed, nothing raised.
    runner = AgentRunner(SessionLocal, replay_provider(SessionLocal), blobs, weather_tools())
    spec = new_spec(company, prompt="a prompt nobody recorded")
    outcome = await runner.start(spec)
    assert outcome.status == "failed" and "no recorded fixture" in outcome.error
    failed = facts_of(db, spec.run_id)[-1]
    assert failed.kind == R.RUN_FAILED and failed.payload["step_no"] == 1
    assert kinds_of(db, spec.run_id) == [R.RUN_STARTED, R.STEP_REQUESTED, R.RUN_FAILED]


async def test_tool_errors_are_results_not_crashes(db, blobs):
    company = uuid4()
    provider = ScriptedLoop([tool_answer(1), [TextDelta("sorry, no weather")]])
    tools = FunctionTools().add(WEATHER_TOOL, lambda city: 1 / 0)
    runner = AgentRunner(SessionLocal, provider, blobs, tools)
    spec = new_spec(company)
    outcome = await runner.start(spec)
    assert outcome.status == "completed"
    tool_done = next(f for f in facts_of(db, spec.run_id) if f.kind == R.TOOL_COMPLETED)
    assert tool_done.payload["is_error"] is True
    assert "ZeroDivisionError" in tool_done.payload["result_preview"]


# -- building blocks -------------------------------------------------------------------


def test_blob_store_is_content_addressed_and_tamper_evident(tmp_path):
    blobs = BlobStore(tmp_path)
    ref = blobs.put_json({"b": 1, "a": [1, 2]})
    assert ref == blobs.put_json({"a": [1, 2], "b": 1})  # canonical: key order irrelevant
    assert ref.startswith("sha256:") and blobs.exists(ref)
    assert blobs.get_json(ref) == {"a": [1, 2], "b": 1}
    with pytest.raises(BlobMissing):
        blobs.get_json("sha256:" + "0" * 64)
    with pytest.raises(ValueError):
        blobs.get_json("md5:abc")
    path = blobs._path(ref)
    path.write_bytes(b"{}")
    with pytest.raises(BlobMissing, match="corrupt"):
        blobs.get_bytes(ref)


def test_context_builder_has_fixed_order():
    builder = ContextBuilder(memory_sections=["Remember: be nice."])
    text = builder.system_text("You are X.", (WEATHER_TOOL,))
    parts = text.split("\n\n")
    assert parts[0] == "You are X."
    assert parts[1].startswith("Available tools:\n- get_weather:")
    assert parts[2] == "Remember: be nice."
    assert parts[3] == OUTPUT_CONTRACT
    assert ContextBuilder().system_text("Y", ()) == f"Y\n\n{OUTPUT_CONTRACT}"


async def test_function_tools_execution_semantics():
    seen = {}

    def echo(x, _execution: Execution):
        seen["key"] = _execution.idempotency_key
        return {"x": x}

    tools = FunctionTools().add(ToolSpec("echo", "echo", {"type": "object"}), echo)
    with pytest.raises(ValueError, match="already registered"):
        tools.add(ToolSpec("echo", "again", {}), echo)
    ex = Execution(ToolCall("c1", "echo", {"x": 1}), "k1", "run", 1)
    result = await tools.execute(ex)
    assert result.content == '{"x": 1}' and not result.is_error and seen["key"] == "k1"
    missing = await tools.execute(Execution(ToolCall("c2", "nope", {}), "k2", "run", 1))
    assert missing.is_error and "unknown tool" in missing.content

    async def boom(**kw):
        raise RuntimeError("bad")

    tools.add(ToolSpec("boom", "boom", {}), boom)
    failed = await tools.execute(Execution(ToolCall("c3", "boom", {}), "k3", "run", 1))
    assert failed.is_error and failed.content == "RuntimeError: bad"
