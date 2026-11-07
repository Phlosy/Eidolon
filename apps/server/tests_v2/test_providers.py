"""Model providers (R2-a): protocol + retry, openai_compatible and anthropic over
mock transports, record/replay, pricing, the ``agent.model.called`` fact (real PG),
replay of real recorded fixtures, and live calls when a key is present."""

import dataclasses
import json
import os
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import httpx
import httpx2
import pytest
from anthropic import DefaultAsyncHttpxClient
from provider_cases import ANTHROPIC_MODEL, DEEPSEEK_MODEL, EXPECTATIONS, WEATHER_TOOL, cases
from sqlalchemy import select

from eidolon.agent.providers import (
    CallContext,
    ChatRequest,
    Completed,
    FixtureMissing,
    Ledgered,
    Message,
    ModelCallLedger,
    ProviderError,
    ProviderTimeout,
    Recording,
    Replaying,
    RetryPolicy,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    Usage,
    build_provider,
    fixture_dir,
    pricing,
)
from eidolon.agent.providers import anthropic as anthropic_provider
from eidolon.agent.providers.accounting import KIND, model_called_fact
from eidolon.agent.providers.base import event_from_dict, event_to_dict, with_retry
from eidolon.agent.providers.openai_compatible import OpenAICompatible
from eidolon.config import settings
from eidolon.db import SessionLocal
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.kernel.store import FactRow, FactStore

FIXTURES = fixture_dir(settings)
DEEPSEEK_KEY = settings.provider_openai_compat_api_key or os.environ.get("DEEPSEEK_API_KEY")
ANTHROPIC_KEY = settings.provider_anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")


async def collect(provider, request):
    text, calls, done = "", [], None
    async for event in provider.complete(request):
        if isinstance(event, TextDelta):
            text += event.text
        elif isinstance(event, ToolCallEvent):
            calls.append(event.call)
        elif isinstance(event, Completed):
            assert done is None, "two Completed events"
            done = event
    assert done is not None, "stream ended without Completed"
    return text, calls, done


class Scripted:
    """A provider that replays a script; for exercising wrappers without I/O."""

    name = "scripted"

    def __init__(self, events, *, vectors=None):
        self.events = list(events)
        self.vectors = vectors or []
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        for event in self.events:
            yield event

    async def embed(self, texts: Sequence[str], *, model=None):
        return [self.vectors[i % len(self.vectors)] for i in range(len(texts))]


def done(**kw) -> Completed:
    base = dict(
        stop_reason="end_turn",
        usage=Usage(10, 5),
        model="deepseek-flash",
        provider="scripted",
        request_id="req-1",
        latency_ms=7,
    )
    base.update(kw)
    return Completed(**base)


def ping(model=DEEPSEEK_MODEL, **kw) -> ChatRequest:
    return ChatRequest(model=model, messages=(Message.user("ping"),), max_tokens=16, **kw)


# -- protocol ----------------------------------------------------------------------


def test_messages_and_requests_validate():
    with pytest.raises(ValueError):
        Message("system", "x")
    with pytest.raises(ValueError):
        Message("tool", "x")
    with pytest.raises(ValueError):
        Message("user", "x", (ToolCall("1", "f", {}),))
    with pytest.raises(ValueError):
        ChatRequest(model="m", messages=())
    with pytest.raises(ValueError):
        ChatRequest(model="m", messages=(Message.user("x"),), max_tokens=0)
    with pytest.raises(ValueError):
        Completed("weird", Usage(), "m", "p")


def test_fingerprint_is_stable_and_ignores_context():
    a = ping()
    b = ping(context=CallContext(uuid4(), Actor.system()))
    assert a.fingerprint() == b.fingerprint()
    assert ping(system="x").fingerprint() != a.fingerprint()
    assert ChatRequest.from_dict(a.as_dict()).fingerprint() == a.fingerprint()
    full = cases(DEEPSEEK_MODEL)["tool_roundtrip"]
    assert ChatRequest.from_dict(full.as_dict()) == full


def test_event_roundtrip():
    events = [
        TextDelta("hi"),
        ToolCallEvent(ToolCall("c1", "f", {"a": [1, {"b": None}]})),
        done(
            stop_reason="refusal",
            stop_detail="cyber",
            attempts=2,
            assistant_state={"reasoning_content": "r"},
        ),
    ]
    assert [event_from_dict(event_to_dict(e)) for e in events] == events
    with pytest.raises(ValueError):
        event_from_dict({"type": "nope"})


async def test_with_retry_retries_only_before_first_event():
    slept = []

    async def sleep(s):
        slept.append(s)

    attempts = {"n": 0}

    async def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ProviderError("429", provider="x", retryable=True, retry_after=0.1)
        return _gen([TextDelta("ok"), done()])

    events = [e async for e in with_retry(RetryPolicy(max_attempts=3), flaky, sleep=sleep)]
    assert events[0] == TextDelta("ok") and events[-1].attempts == 3
    assert slept == [0.1, 0.1]

    async def fatal():
        raise ProviderError("400", provider="x", retryable=False)

    with pytest.raises(ProviderError, match="400"):
        [e async for e in with_retry(RetryPolicy(), fatal, sleep=sleep)]

    async def exhausted():
        raise ProviderError("503", provider="x", retryable=True)

    with pytest.raises(ProviderError, match="503"):
        [
            e
            async for e in with_retry(
                RetryPolicy(max_attempts=2, base_delay=0), exhausted, sleep=sleep
            )
        ]

    async def mid_stream():
        async def gen():
            yield TextDelta("partial")
            raise ProviderError("broke", provider="x", retryable=True)

        return gen()

    calls = {"n": 0}

    async def counting():
        calls["n"] += 1
        return await mid_stream()

    with pytest.raises(ProviderError, match="broke"):
        [
            e
            async for e in with_retry(
                RetryPolicy(max_attempts=5, base_delay=0), counting, sleep=sleep
            )
        ]
    assert calls["n"] == 1  # never restarted after output began

    policy = RetryPolicy(base_delay=1, max_delay=4, jitter=0)
    assert [policy.delay(i) for i in (1, 2, 3, 4)] == [1, 2, 4, 4]
    assert policy.delay(1, retry_after=30) == 4


async def _gen(events):
    for e in events:
        yield e


# -- openai_compatible over a mock transport ---------------------------------------


def sse(*chunks) -> bytes:
    return (
        b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"
    )


def oa_chunk(**delta):
    return {
        "id": "chatcmpl-1",
        "model": "deepseek-flash",
        "choices": [{"index": 0, "delta": delta}],
    }


OA_STREAM = sse(
    oa_chunk(role="assistant", content=""),
    oa_chunk(reasoning_content="think "),
    oa_chunk(reasoning_content="hard"),
    oa_chunk(content="Sure"),
    oa_chunk(content=", checking."),
    oa_chunk(
        tool_calls=[
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_", "arguments": ""},
            }
        ]
    ),
    oa_chunk(tool_calls=[{"index": 0, "function": {"name": "weather", "arguments": '{"ci'}}]),
    oa_chunk(tool_calls=[{"index": 0, "function": {"arguments": 'ty": "Beijing"}'}}]),
    oa_chunk(
        tool_calls=[
            {
                "index": 1,
                "id": "call_2",
                "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
            }
        ]
    ),
    {**oa_chunk(), "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    {
        "id": "chatcmpl-1",
        "model": "deepseek-flash",
        "choices": [],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20, "prompt_cache_hit_tokens": 32},
    },
)


def oa_provider(handler, **kw) -> OpenAICompatible:
    return OpenAICompatible(
        base_url="https://mock.example",
        api_key="sk-test-not-real-0000",
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=3, base_delay=0, max_delay=0),
        **kw,
    )


async def test_openai_compatible_streams_text_tools_and_usage():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=OA_STREAM, headers={"x-request-id": "rid-9"})

    req = cases(DEEPSEEK_MODEL)["tool_roundtrip"]
    text, calls, finished = await collect(oa_provider(handler), req)
    assert text == "Sure, checking."
    assert calls == [
        ToolCall("call_1", "get_weather", {"city": "Beijing"}),
        ToolCall("call_2", "get_weather", {"city": "Paris"}),
    ]
    assert finished.stop_reason == "tool_use" and finished.request_id == "rid-9"
    assert finished.usage == Usage(50, 20, cache_read_tokens=32)
    assert finished.provider == "openai_compatible" and finished.attempts == 1
    assert finished.assistant_state == {"reasoning_content": "think hard"}
    body = seen["body"]
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert body["temperature"] == 0 and body["max_tokens"] == 64
    assert [m["role"] for m in body["messages"]] == ["user", "assistant", "tool"]
    assert body["messages"][1]["tool_calls"][0]["function"]["name"] == "get_weather"
    assert json.loads(body["messages"][1]["tool_calls"][0]["function"]["arguments"]) == {
        "city": "Beijing"
    }
    assert body["messages"][2]["tool_call_id"] == "call_fixture_1"
    assert body["messages"][1]["reasoning_content"].startswith("The user wants")  # echoed back
    assert body["tools"][0]["function"]["parameters"]["required"] == ["city"]
    assert seen["auth"] == "Bearer sk-test-not-real-0000"


async def test_openai_compatible_retries_429_then_succeeds_and_never_retries_400():
    n = {"calls": 0}

    def handler(request):
        n["calls"] += 1
        if n["calls"] == 1:
            return httpx.Response(429, text="slow down", headers={"retry-after": "0"})
        return httpx.Response(
            200,
            content=sse(
                oa_chunk(content="pong"),
                {**oa_chunk(), "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ),
        )

    text, _, finished = await collect(oa_provider(handler), ping())
    assert text == "pong" and finished.attempts == 2 and finished.stop_reason == "end_turn"
    assert finished.request_id == "chatcmpl-1"  # falls back to the chunk id

    def bad(request):
        n["calls"] += 1
        return httpx.Response(400, json={"error": {"message": "bad model"}})

    n["calls"] = 0
    with pytest.raises(ProviderError) as exc:
        await collect(oa_provider(bad), ping())
    assert exc.value.status == 400 and not exc.value.retryable and n["calls"] == 1

    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderTimeout):
        await collect(oa_provider(timeout), ping())

    def broken(request):
        return httpx.Response(200, content=b"data: {not json\n\n")

    with pytest.raises(ProviderError, match="bad SSE chunk"):
        await collect(oa_provider(broken), ping())

    def bad_args(request):
        return httpx.Response(
            200,
            content=sse(
                oa_chunk(
                    tool_calls=[
                        {
                            "index": 0,
                            "id": "c9",
                            "function": {
                                "name": "write_file",
                                "arguments": '{"path": "a", "content": "unterminated',
                            },
                        }
                    ]
                ),
                {
                    **oa_chunk(),
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                },
            ),
        )

    _, calls, finished = await collect(oa_provider(bad_args), ping())
    assert finished.stop_reason == "tool_use" and calls[0].name == "write_file"
    assert "_invalid_json" in calls[0].arguments and "_error" in calls[0].arguments


async def test_openai_compatible_embed():
    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == "/embeddings" and body["input"] == ["a", "b"]
        return httpx.Response(
            200,
            json={
                "data": [{"index": 1, "embedding": [0.3]}, {"index": 0, "embedding": [0.1, 0.2]}]
            },
        )

    assert await oa_provider(handler).embed(["a", "b"], model="e") == [[0.1, 0.2], [0.3]]


# -- anthropic over a mock transport (httpx2, the SDK's client) ----------------------


def anthropic_sse(events) -> bytes:
    return b"".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


ANTHROPIC_STREAM = anthropic_sse(
    [
        {
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5-5",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 40,
                    "output_tokens": 1,
                    "cache_creation_input_tokens": 8,
                    "cache_read_input_tokens": 16,
                },
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "hmm"},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "sig123"},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "Let me "},
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "text_delta", "text": "check."},
        },
        {"type": "content_block_stop", "index": 1},
        {
            "type": "content_block_start",
            "index": 2,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "get_weather",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": '{"city": "Bei'},
        },
        {
            "type": "content_block_delta",
            "index": 2,
            "delta": {"type": "input_json_delta", "partial_json": 'jing"}'},
        },
        {"type": "content_block_stop", "index": 2},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use", "stop_sequence": None},
            "usage": {"output_tokens": 23},
        },
        {"type": "message_stop"},
    ]
)


def make_anthropic(handler, **kw):
    return anthropic_provider.Anthropic(
        api_key="sk-ant-test-not-real-0000",
        max_retries=0,
        http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
        **kw,
    )


async def test_anthropic_streams_text_tools_usage_and_folds_tool_results():
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        seen["path"] = request.url.path
        return httpx2.Response(
            200,
            content=ANTHROPIC_STREAM,
            headers={"content-type": "text/event-stream", "request-id": "req_abc"},
        )

    req = ChatRequest(
        model=ANTHROPIC_MODEL,
        system="sys",
        messages=(
            Message.user("weather?"),
            Message.assistant(
                "ok",
                ToolCall("t1", "get_weather", {"city": "A"}),
                ToolCall("t2", "get_weather", {"city": "B"}),
                provider_state={
                    "thinking_blocks": [
                        {"type": "thinking", "thinking": "prior", "signature": "s0"}
                    ]
                },
            ),
            Message.tool("t1", "sunny"),
            Message.tool("t2", "rain", is_error=True),
            Message.user("thanks"),
        ),
        tools=(WEATHER_TOOL,),
        max_tokens=100,
    )
    text, calls, finished = await collect(make_anthropic(handler), req)
    assert text == "Let me check."
    assert calls == [ToolCall("toolu_1", "get_weather", {"city": "Beijing"})]
    assert finished.stop_reason == "tool_use" and finished.request_id == "req_abc"
    assert finished.usage == Usage(40, 23, cache_read_tokens=16, cache_write_tokens=8)
    assert finished.model == "claude-opus-5-5" and finished.provider == "anthropic"
    assert finished.assistant_state == {
        "thinking_blocks": [{"type": "thinking", "thinking": "hmm", "signature": "sig123"}]
    }
    body = seen["body"]
    assert seen["path"] == "/v1/messages" and body["stream"] is True
    assert body["system"] == "sys" and "thinking" not in body and "fallbacks" not in body
    assert body["tools"] == [
        {
            "name": "get_weather",
            "description": WEATHER_TOOL.description,
            "input_schema": dict(WEATHER_TOOL.input_schema),
        }
    ]
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant", "user", "user"]  # both tool results in ONE user turn
    results = body["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"] and results[1]["is_error"] is True
    assistant = body["messages"][1]["content"]
    assert assistant[0] == {
        "type": "thinking",
        "thinking": "prior",
        "signature": "s0",
    }  # echoed first
    assert assistant[1] == {"type": "text", "text": "ok"}
    assert assistant[2]["type"] == "tool_use" and assistant[3]["type"] == "tool_use"


async def test_anthropic_fallbacks_use_beta_endpoint_and_errors_map():
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta")
        return httpx2.Response(
            200, content=ANTHROPIC_STREAM, headers={"content-type": "text/event-stream"}
        )

    await collect(make_anthropic(handler, fallbacks=True), ping(ANTHROPIC_MODEL))
    assert seen["body"]["fallbacks"] == "default"
    assert anthropic_provider.FALLBACK_BETA in (seen["beta"] or "")

    def overloaded(request):
        return httpx2.Response(
            529,
            json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}},
            headers={"retry-after": "3"},
        )

    with pytest.raises(ProviderError) as exc:
        await collect(make_anthropic(overloaded), ping(ANTHROPIC_MODEL))
    assert exc.value.status == 529 and exc.value.retryable and exc.value.retry_after == 3

    def bad(request):
        return httpx2.Response(
            400,
            json={"type": "error", "error": {"type": "invalid_request_error", "message": "nope"}},
        )

    with pytest.raises(ProviderError) as exc:
        await collect(make_anthropic(bad), ping(ANTHROPIC_MODEL))
    assert exc.value.status == 400 and not exc.value.retryable

    with pytest.raises(ProviderError, match="no embeddings"):
        await make_anthropic(bad).embed(["x"])


# -- record / replay ----------------------------------------------------------------


async def test_record_then_replay_is_byte_for_byte(tmp_path):
    events = [TextDelta("po"), TextDelta("ng"), ToolCallEvent(ToolCall("c", "f", {"k": 1})), done()]
    inner = Scripted(events, vectors=[[0.5, 0.25]])
    recording = Recording(inner, tmp_path)
    req = ping(context=CallContext(uuid4(), Actor.system()))
    assert [e async for e in recording.complete(req)] == events
    assert await recording.embed(["x", "y"], model="emb") == [[0.5, 0.25], [0.5, 0.25]]

    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == [f"chat-{req.replay_key()}.json", f"embed-{files[1][6:-5]}.json"]
    data = json.loads((tmp_path / files[0]).read_text())
    assert data["request"] == req.as_dict() and data["provider"] == "scripted"

    replay = Replaying(tmp_path)
    assert [e async for e in replay.complete(ping())] == events  # context-free: same fingerprint
    assert await replay.embed(["x", "y"], model="emb") == [[0.5, 0.25], [0.5, 0.25]]
    assert inner.calls == 1

    with pytest.raises(FixtureMissing, match="no recorded fixture"):
        [e async for e in replay.complete(ping(system="never recorded"))]
    with pytest.raises(FixtureMissing):
        await replay.embed(["other"], model="emb")


# -- pricing & the fact -------------------------------------------------------------


def test_pricing_estimates_in_micro_units():
    est = pricing.estimate("claude-opus-5-5", Usage(1_000_000, 100_000, cache_read_tokens=200_000))
    # 800k uncached * $4 + 200k read * $0.20 + 100k out * $20 = 3.2 + 0.04 + 2.0 = $5.24
    assert est.currency == "USD" and est.amount_micro == 5_240_000
    est = pricing.estimate("deepseek-flash", Usage(1000, 500, cache_read_tokens=400))
    # 600 * 2 + 400 * 0.04 + 500 * 8 = 0.0012 + 0.000016 + 0.004 CNY = 5216 micro
    assert est.currency == "CNY" and est.amount_micro == 5216
    assert pricing.estimate("unknown-model", Usage(1, 1)) is None
    assert est.pricing_version == pricing.PRICING_VERSION


def test_model_called_fact_shape_and_context_requirement():
    with pytest.raises(ValueError, match="context"):
        model_called_fact(ping(), done())
    company = uuid4()
    ctx = CallContext(company, Actor(ActorKind.agent, "person-1"), run_id="run-9")
    fact = model_called_fact(ping(context=ctx), done(), mode="replay")
    assert fact.kind == KIND and fact.company_id == company and fact.actor.id == "person-1"
    assert fact.idempotency_key == "agent.model.called:scripted:req-1"
    caused = model_called_fact(
        ping(context=CallContext(company, Actor.system(), causation_id=ctx.company_id)), done()
    )
    assert caused.idempotency_key == f"agent.model.called:scripted:req-1:{ctx.company_id}"
    p = fact.payload
    assert p["usage"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    assert p["cost"] == {
        "amount_micro": 60,
        "currency": "CNY",
        "pricing_version": pricing.PRICING_VERSION,
    }
    assert p["mode"] == "replay" and p["run_id"] == "run-9" and p["request_id"] == "req-1"
    assert p["request_fingerprint"] == ping().fingerprint()


async def test_ledgered_provider_writes_one_fact_per_call(db):
    company = uuid4()
    root = FactStore().append(
        db,
        __import__("eidolon.kernel.facts", fromlist=["NewFact"]).NewFact(
            company, Actor.system(), "work.task.created", {}, "root"
        ),
    )
    db.commit()
    ctx = CallContext(
        company,
        Actor(ActorKind.agent, "eng-1"),
        correlation_id=root.correlation_id,
        causation_id=root.id,
    )
    provider = Ledgered(Scripted([TextDelta("pong"), done()]), ModelCallLedger(SessionLocal))

    text, _, finished = await collect(provider, ping(context=ctx))
    assert text == "pong"
    fact = provider.last_fact
    assert fact is not None and fact.kind == KIND and fact.causation_id == root.id
    assert (
        fact.correlation_id == root.correlation_id and fact.payload["usage"]["output_tokens"] == 5
    )

    await collect(provider, ping(context=ctx))  # same request id → same fact, not a second one
    assert provider.last_fact.id == fact.id
    rows = db.scalars(select(FactRow).where(FactRow.kind == KIND)).all()
    assert len(rows) == 1

    with pytest.raises(ValueError, match="context"):
        await collect(provider, ping())


def test_build_provider_modes(tmp_path, monkeypatch):
    cfg = settings.model_copy(update={"provider_fixture_dir": str(tmp_path)})
    assert isinstance(build_provider(cfg, mode="replay"), Replaying)
    live = build_provider(cfg, mode="live", kind="openai_compatible")
    assert isinstance(live, OpenAICompatible)
    rec = build_provider(cfg, mode="record", kind="anthropic")
    assert isinstance(rec, Recording) and rec.inner.name == "anthropic"
    led = build_provider(cfg, mode="replay", session_factory=SessionLocal)
    assert isinstance(led, Ledgered) and isinstance(led.inner, Replaying)
    with pytest.raises(ValueError):
        build_provider(cfg, mode="dream")
    with pytest.raises(ValueError):
        build_provider(cfg, kind="gpt")


# -- recorded real fixtures (deterministic, real distribution) -------------------------


@pytest.mark.parametrize("name", sorted(EXPECTATIONS))
async def test_replay_of_recorded_deepseek_fixture(name):
    request = cases(DEEPSEEK_MODEL)[name]
    path = FIXTURES / f"chat-{request.replay_key()}.json"
    assert path.exists(), (
        f"fixture for {name} missing ({path.name}); run scripts/record_provider_fixtures.py"
    )
    text, calls, finished = await collect(Replaying(FIXTURES), request)
    assert EXPECTATIONS[name](text, calls, finished), (name, text, calls, finished)
    assert finished.provider == "openai_compatible" and finished.model == DEEPSEEK_MODEL
    assert finished.usage.input_tokens > 0 and finished.usage.output_tokens > 0
    assert finished.request_id
    recorded = json.loads(path.read_text())
    assert recorded["request"] == request.as_dict()


# -- live (real model; skipped with a reason when no key, never counted as passed) ----


@pytest.mark.skipif(
    not DEEPSEEK_KEY,
    reason="no DeepSeek key (EIDOLON_V2_PROVIDER_OPENAI_COMPAT_API_KEY / DEEPSEEK_API_KEY)",
)
async def test_live_deepseek_call_lands_as_fact(db):
    company = uuid4()
    ctx = CallContext(company, Actor(ActorKind.agent, "eng-live"), run_id="live-smoke")
    provider = build_provider(
        settings, mode="live", kind="openai_compatible", session_factory=SessionLocal
    )
    text, calls, finished = await collect(
        provider, dataclasses.replace(cases(DEEPSEEK_MODEL)["ping"], context=ctx)
    )
    await provider.inner.aclose()
    assert EXPECTATIONS["ping"](text, calls, finished)
    assert finished.request_id and finished.usage.output_tokens > 0
    fact = provider.last_fact
    assert fact.payload["cost"]["currency"] == "CNY" and fact.payload["cost"]["amount_micro"] > 0
    assert fact.payload["mode"] == "live" and fact.payload["run_id"] == "live-smoke"


@pytest.mark.skipif(
    not ANTHROPIC_KEY,
    reason="no Anthropic key (EIDOLON_V2_PROVIDER_ANTHROPIC_API_KEY / ANTHROPIC_API_KEY)",
)
async def test_live_anthropic_call(db):
    provider = build_provider(settings, mode="live", kind="anthropic")
    text, calls, finished = await collect(provider, cases(ANTHROPIC_MODEL)["ping"])
    await provider.aclose()
    assert EXPECTATIONS["ping"](text, calls, finished) and finished.request_id
    assert pricing.estimate(finished.model, finished.usage) is not None


def test_fixture_dir_resolves_relative_to_server_root():
    assert fixture_dir(settings).is_absolute()
    assert fixture_dir(settings.model_copy(update={"provider_fixture_dir": "/abs"})) == Path("/abs")
