"""The sandboxed runner (R2-c): a real child process over JSON-RPC/stdio —
confinement, zero secrets, timeouts, truncation, the idempotency ledger across
restarts, crash tolerance, and the agent loop driving it end to end."""

import asyncio
import os
import signal
import sys
from uuid import uuid4

import pytest
from loop_support import new_spec

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobStore
from eidolon.agent.loop import AgentRunner
from eidolon.agent.providers import Completed, TextDelta, ToolCall, ToolCallEvent, ToolSpec, Usage
from eidolon.agent.runner import RUNNER_SRC, RunnerClient, RunnerDied, RunnerError, RunnerTools
from eidolon.agent.tools import Execution
from eidolon.agent.tools.registry import ToolDescriptor, ToolRegistry
from eidolon.db import SessionLocal
from eidolon.kernel.store import FactStore

store = FactStore()


@pytest.fixture()
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest.fixture()
async def tools(workspace):
    t = await RunnerTools.start(workspace, max_output_bytes=4096)
    yield t
    await t.close()


def ex(name, key="k", **arguments) -> Execution:
    return Execution(ToolCall(f"call-{key}", name, arguments), key, "run-1", 1)


# -- protocol & self-description -------------------------------------------------------


async def test_hello_describes_tools_with_side_effects(tools):
    hello = tools.client.hello
    assert hello["protocol"] == "eidolon.runner/1" and hello["pid"] == tools.client.pid
    assert hello["limits"]["max_output_bytes"] == 4096
    names = [s.name for s in tools.specs()]
    assert names == ["read_file", "write_file", "list_dir", "shell", "run_tests"]
    reg = tools.registry
    assert reg.get("read_file").side_effect == "READ"
    assert reg.get("write_file").side_effect == "WRITE"
    assert reg.get("shell").required_authority == "workspace.execute"
    assert [d.name for d in reg.with_side_effect("READ")] == ["read_file", "list_dir"]
    assert tools.specs()[1].input_schema["required"] == ["path", "content"]


async def test_malformed_requests_are_rpc_errors_not_tool_results(tools):
    with pytest.raises(RunnerError, match="-32601"):
        await tools.client.call("no.such.method", {})
    with pytest.raises(RunnerError, match="-32602"):
        await tools.client.call("tool.execute", {"name": 7})
    # still alive and serving afterwards
    result = await tools.execute(ex("list_dir"))
    assert result.content == "(empty)" and not result.is_error


def test_registry_validates_descriptors():
    spec = ToolSpec("x", "x", {"type": "object"})
    with pytest.raises(ValueError, match="side_effect"):
        ToolDescriptor(spec, "DESTROY", "a")
    reg = ToolRegistry().add(ToolDescriptor(spec, "HIGH_IMPACT", "a", "hire"))
    with pytest.raises(ValueError, match="already registered"):
        reg.add(ToolDescriptor(spec, "READ", "a"))
    assert reg.get("x").decision_semantics == "hire" and len(reg) == 1


# -- files & confinement ----------------------------------------------------------------


async def test_write_list_read_inside_workspace(tools, workspace):
    r = await tools.execute(ex("write_file", "w1", path="src/app.py", content="print('hi')\n"))
    assert r.content == "wrote 12 bytes to src/app.py" and not r.is_error
    assert (workspace / "src" / "app.py").read_text() == "print('hi')\n"
    r = await tools.execute(ex("list_dir"))
    assert r.content.splitlines() == ["src/", "src/app.py  (12 bytes)"]
    r = await tools.execute(ex("read_file", path="src/app.py"))
    assert r.content == "print('hi')\n"
    await tools.execute(
        ex("write_file", "w2", path="big.txt", content="\n".join(map(str, range(100))))
    )
    r = await tools.execute(ex("read_file", path="big.txt", offset=10, limit=3))
    assert r.content == "10\n11\n12\n"
    r = await tools.execute(ex("read_file", path="src"))
    assert r.is_error and "not a file" in r.content
    r = await tools.execute(ex("read_file", path="nope.txt"))
    assert r.is_error


async def test_paths_cannot_escape_the_workspace(tools, workspace, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    (workspace / "link").symlink_to(secret)
    (workspace / "dirlink").symlink_to(tmp_path)
    for path in (
        "../secret.txt",
        str(secret),
        "/etc/passwd",
        "link",
        "dirlink/secret.txt",
        "a/../../secret.txt",
        ".eidolon/runner-ledger.jsonl",
    ):
        r = await tools.execute(ex("read_file", path=path))
        assert r.is_error and ("path rejected" in r.content or "not a file" in r.content), path
        assert "top secret" not in r.content
    r = await tools.execute(ex("write_file", "esc", path="../evil.txt", content="x"))
    assert r.is_error and "path rejected" in r.content and not (tmp_path / "evil.txt").exists()
    r = await tools.execute(ex("write_file", "esc2", path=".eidolon/x", content="x"))
    assert r.is_error and "internal" in r.content
    r = await tools.execute(ex("write_file", "esc3", path="", content="x"))
    assert r.is_error


# -- processes: no secrets, timeouts, truncation --------------------------------------------


async def test_shell_runs_in_workspace_with_a_scrubbed_environment(workspace, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-leak-0000000000")
    monkeypatch.setenv("EIDOLON_V2_PROVIDER_ANTHROPIC_API_KEY", "sk-ant-leak-000000")
    monkeypatch.setenv("MY_PASSWORD", "hunter2")
    monkeypatch.setenv("HARMLESS_FLAG", "1")
    tools = await RunnerTools.start(workspace)
    try:
        r = await tools.execute(ex("shell", "s1", command="pwd; env | sort"))
        assert not r.is_error and r.content.endswith("[exit 0]")
        lines = r.content.splitlines()
        assert lines[0] == "/workspace"  # host paths never reach the model
        env_text = "\n".join(lines[1:])
        assert "sk-leak" not in env_text and "hunter2" not in env_text and "sk-ant" not in env_text
        assert "DEEPSEEK_API_KEY" not in env_text and "HARMLESS_FLAG" not in env_text  # allow-list
        assert "EIDOLON_WORKSPACE=/workspace" in env_text  # aliased in output too
        probe = "python -c 'import sys; print(sys.version_info[:2])'"
        r = await tools.execute(ex("shell", "py", command=probe))
        assert r.content.startswith(str(sys.version_info[:2]))  # same interpreter as the server
        assert "PATH=" in env_text
        assert "PYTHONPATH" in env_text  # the runner's own import path, nothing secret
        r = await tools.execute(ex("shell", "s2", command="echo out; echo err 1>&2; exit 3"))
        assert r.is_error and r.content == "out\n[stderr]\nerr\n[exit 3]"
    finally:
        await tools.close()


async def test_timeout_kills_the_whole_process_group_and_runner_survives(tools):
    r = await tools.execute(
        ex("shell", "t1", command="sleep 30 & sleep 30; echo late", timeout_s=1)
    )
    assert r.is_error and "timed out after 1s" in r.content and "late" not in r.content
    r = await tools.execute(ex("shell", "t2", command="echo still here"))
    assert r.content.startswith("still here")


async def test_output_is_truncated_at_the_limit(tools):
    r = await tools.execute(ex("shell", "big", command="head -c 20000 /dev/zero | tr '\\0' x"))
    assert "[truncated" in r.content and len(r.content.encode()) < 4096 + 100
    r = await tools.execute(ex("write_file", "bigw", path="big.txt", content="y" * 10000))
    r = await tools.execute(ex("read_file", path="big.txt"))
    assert r.content.startswith("yyyy") and r.content.endswith("[truncated 5904 bytes]")


async def test_run_tests_executes_pytest_in_the_workspace(tools, workspace):
    (workspace / "test_x.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    r = await tools.execute(ex("run_tests", "rt1"))
    assert not r.is_error and "1 passed in Xs" in r.content, r.content  # timing normalised
    (workspace / "test_y.py").write_text("def test_bad():\n    assert False\n")
    r = await tools.execute(ex("run_tests", "rt2", path="test_y.py"))
    assert r.is_error and "1 failed" in r.content


# -- idempotency ledger & crash tolerance ------------------------------------------------------


async def test_side_effects_replay_by_key_even_across_runner_restarts(workspace):
    tools = await RunnerTools.start(workspace)
    first = await tools.execute(ex("write_file", "same", path="f.txt", content="one"))
    again = await tools.execute(ex("write_file", "same", path="f.txt", content="TWO"))
    assert first.content == again.content and (workspace / "f.txt").read_text() == "one"
    r = await tools.execute(ex("shell", "cnt", command="echo run >> counter.txt; cat counter.txt"))
    assert r.content.startswith("run\n")
    r = await tools.execute(ex("shell", "cnt", command="echo run >> counter.txt; cat counter.txt"))
    assert r.content.startswith("run\n") and (workspace / "counter.txt").read_text() == "run\n"
    # reads are never replayed: a new key is not even needed
    (workspace / "f.txt").write_text("edited outside")
    assert (await tools.execute(ex("read_file", path="f.txt"))).content == "edited outside"
    await tools.close()

    restarted = await RunnerTools.start(workspace)
    try:
        assert restarted.client.hello["ledger_entries"] == 2  # "same", "cnt"; replays add nothing
        r = await restarted.execute(ex("write_file", "same", path="f.txt", content="THREE"))
        assert r.content == first.content
        assert (workspace / "f.txt").read_text() == "edited outside"
        r = await restarted.execute(ex("write_file", "new", path="f.txt", content="four"))
        assert (workspace / "f.txt").read_text() == "four"
    finally:
        await restarted.close()


async def test_runner_death_is_survived_by_a_restart(tools, workspace):
    (workspace / "x.txt").write_text("x")
    old_pid = tools.client.pid
    os.kill(old_pid, signal.SIGKILL)
    await asyncio.sleep(0.2)
    r = await tools.execute(ex("read_file", path="x.txt"))
    assert r.content == "x" and tools.client.pid != old_pid and tools.client.alive
    with pytest.raises(RunnerDied):
        dead = RunnerClient(workspace)
        await dead.call("runner.hello", {})


# -- the loop drives the runner -----------------------------------------------------------


class ScriptedEngineer:
    """What a model would do for 'write hello.py and run it': three scripted turns."""

    name = "scripted"

    def __init__(self):
        self.turn = 0

    async def complete(self, request):
        self.turn += 1
        if self.turn == 1:
            yield ToolCallEvent(
                ToolCall(
                    "c1", "write_file", {"path": "hello.py", "content": "print('hello, ledger')\n"}
                )
            )
            stop = "tool_use"
        elif self.turn == 2:
            last = request.messages[-1]
            assert last.role == "tool" and "wrote 23 bytes" in last.tool_result.content
            yield ToolCallEvent(ToolCall("c2", "shell", {"command": "python hello.py"}))
            stop = "tool_use"
        else:
            last = request.messages[-1]
            assert "hello, ledger" in last.tool_result.content
            yield TextDelta("Done: hello.py prints 'hello, ledger'.")
            stop = "end_turn"
        yield Completed(stop, Usage(10, 5), request.model, "scripted", request_id=f"s{self.turn}")

    async def embed(self, texts, *, model=None):
        raise NotImplementedError


async def test_agent_loop_drives_the_runner_end_to_end(db, tools, workspace, tmp_path):
    company = uuid4()
    runner = AgentRunner(SessionLocal, ScriptedEngineer(), BlobStore(tmp_path / "blobs"), tools)
    spec = new_spec(company, system="You are an engineer.", prompt="Write hello.py and run it.")
    outcome = await runner.start(spec)
    assert outcome.status == "completed" and outcome.steps == 3, outcome
    assert "hello, ledger" in outcome.final_text
    assert (workspace / "hello.py").exists()
    with SessionLocal() as s:
        facts = store.by_correlation(s, spec.run_id)
    started = facts[0]
    assert started.payload["tools"] == ["read_file", "write_file", "list_dir", "shell", "run_tests"]
    tool_facts = [f for f in facts if f.kind == R.TOOL_COMPLETED]
    assert [f.payload["name"] for f in tool_facts] == ["write_file", "shell"]
    assert tool_facts[1].payload["result_preview"] == "hello, ledger\n[exit 0]"
    # The runner's ledger holds exactly the loop's two idempotency keys.
    ledger = (workspace / ".eidolon" / "runner-ledger.jsonl").read_text().splitlines()
    keys = [__import__("json").loads(line)["key"] for line in ledger]
    assert keys == [R.tool_key(spec.run_id, 1, "c1"), R.tool_key(spec.run_id, 2, "c2")]


def test_runner_source_is_standalone():
    """The runner must stay importable without the server: no eidolon imports."""
    for path in (RUNNER_SRC / "eidolon_runner").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from eidolon." not in text and "import eidolon." not in text, path.name
        assert "import eidolon\n" not in text, path.name
