"""The bridge consumes complete, correlated replies, never generic telemetry."""

import asyncio
import json

import httpx
import pytest

from app.runtimes.base import RuntimeInstance, RuntimeProtocolError, RuntimeSession
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.mock.adapter import MockAdapter
from app.runtimes.openclaw.adapter import OpenClawAdapter


def session(identifier="controlled-1"):
    return RuntimeSession(
        id=identifier,
        instance=RuntimeInstance(
            employee_id=1,
            profile="isolated",
            home_path="/unused",
            details={"base_url": "http://runtime.test", "api_key": "test-only"},
        ),
        task_id=1,
    )


def hermes_transport(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.runtimes.hermes.adapter.httpx.AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
    )


async def test_hermes_complete_turn_keeps_conversation_and_unique_request_keys(monkeypatch):
    submitted = []
    expected = ['{"kind":"read","key":"first"}', '{"kind":"finish","key":"last"}']

    def respond(request):
        if request.method == "POST":
            submitted.append((json.loads(request.content), request.headers["Idempotency-Key"]))
            return httpx.Response(202, json={"run_id": f"run-{len(submitted)}"})
        index = int(request.url.path.rsplit("-", 1)[1]) - 1
        return httpx.Response(
            200,
            json={"run_id": f"run-{index + 1}", "status": "completed", "output": expected[index]},
        )

    hermes_transport(monkeypatch, respond)
    adapter, current = HermesAdapter(), session()
    first = await adapter.complete_turn(current, "facts", request_id="round-1", timeout=1)
    second = await adapter.complete_turn(current, "receipt", request_id="round-2", timeout=1)
    assert [first.text, second.text] == expected
    assert [first.run_id, second.run_id] == ["run-1", "run-2"]
    assert [first.request_id, second.request_id] == ["round-1", "round-2"]
    assert first.session_id == second.session_id == current.id
    assert [item[0]["session_id"] for item in submitted] == [current.id, current.id]
    assert [item[0]["input"] for item in submitted] == ["facts", "receipt"]
    assert submitted[0][1] != submitted[1][1]
    assert current.status == "running"  # a turn finishing does not finish business work
    assert current.queue.empty()  # no synthetic tool telemetry is used as a command


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "failed", "output": '{"kind":"finish"}'},
        {"status": "completed", "summary": '{"kind":"finish"}'},
        {"status": "completed", "output": {"kind": "finish"}},
        {"status": "completed", "output": "x" * 65_537},
        {"run_id": "wrong-run", "status": "completed", "output": "{}"},
    ],
)
async def test_hermes_incomplete_or_unrelated_reply_is_rejected(monkeypatch, payload):
    from app.runtimes.base import RuntimeProtocolError

    def respond(request):
        if request.method == "POST":
            return httpx.Response(202, json={"run_id": "expected-run"})
        return httpx.Response(200, json=payload)

    hermes_transport(monkeypatch, respond)
    with pytest.raises(RuntimeProtocolError):
        await HermesAdapter().complete_turn(session(), "facts", request_id="one", timeout=1)


class Gateway:
    def __init__(self, *, state="final", wait_status="ok", matching=True, text='{"kind":"read"}'):
        self.events = asyncio.Queue()
        self.calls = []
        self.state, self.wait_status, self.matching, self.text = state, wait_status, matching, text
        self.matching_event = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def call(self, method, params, **kwargs):
        self.calls.append((method, params))
        if method == "chat.send":
            run_id = params["idempotencyKey"]
            # These tempting messages must never supply the controlled response.
            for bad in (
                {"event": "session.tool", "payload": {"tool": "create_task"}},
                {
                    "event": "chat",
                    "payload": {
                        "state": "final",
                        "runId": "other",
                        "sessionKey": params["sessionKey"],
                        "message": {"role": "assistant", "content": "FORGED"},
                    },
                },
                {
                    "event": "chat",
                    "payload": {
                        "state": "final",
                        "runId": run_id,
                        "sessionKey": "agent:main:other",
                        "message": {"role": "assistant", "content": "FORGED"},
                    },
                },
            ):
                await self.events.put(bad)
            if self.matching:
                self.matching_event = {
                    "event": "chat",
                    "payload": {
                        "state": self.state,
                        "runId": run_id,
                        "sessionKey": params["sessionKey"],
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": self.text}],
                        },
                    },
                }
                await self.events.put(self.matching_event)
            return {"runId": run_id, "status": "started"}
        if method == "agent.wait":
            return {"runId": params["runId"], "status": self.wait_status}
        if method == "chat.abort":
            return {"ok": True}
        raise AssertionError(f"unexpected RPC {method}")


async def test_openclaw_complete_turn_matches_run_and_private_session(monkeypatch):
    clients = [Gateway(), Gateway(text='{"kind":"finish"}')]
    iterator = iter(clients)
    adapter, current = OpenClawAdapter(), session()
    monkeypatch.setattr(adapter, "_connect", lambda instance: next(iterator))
    first = await adapter.complete_turn(current, "facts", request_id="round-1", timeout=1)
    second = await adapter.complete_turn(current, "receipt", request_id="round-2", timeout=1)
    assert [first.text, second.text] == ['{"kind":"read"}', '{"kind":"finish"}']
    keys = [client.calls[0][1]["sessionKey"] for client in clients]
    assert keys[0] == keys[1] and keys[0] != "agent:main:main"
    assert current.id in keys[0]
    assert first.run_id != second.run_id
    assert first.session_id == second.session_id == current.id
    assert current.status == "running"
    assert current.queue.empty()


@pytest.mark.parametrize(
    "state,wait_status",
    [("error", "ok"), ("aborted", "ok"), ("final", "error"), ("final", "timeout")],
)
async def test_openclaw_requires_successful_run_and_final_reply(monkeypatch, state, wait_status):
    from app.runtimes.base import RuntimeProtocolError

    gateway, adapter = Gateway(state=state, wait_status=wait_status), OpenClawAdapter()
    monkeypatch.setattr(adapter, "_connect", lambda instance: gateway)
    with pytest.raises(RuntimeProtocolError):
        await adapter.complete_turn(session(), "facts", request_id="one", timeout=1)


async def test_openclaw_missing_correlated_final_times_out_without_using_history(monkeypatch):
    gateway, adapter = Gateway(matching=False), OpenClawAdapter()
    monkeypatch.setattr(adapter, "_connect", lambda instance: gateway)
    with pytest.raises(TimeoutError):
        await adapter.complete_turn(session(), "facts", request_id="one", timeout=0.03)
    assert not any(method == "chat.history" for method, _ in gateway.calls)
    assert any(method == "chat.abort" for method, _ in gateway.calls)


@pytest.mark.parametrize("adapter_cls", [HermesAdapter, OpenClawAdapter])
async def test_ended_session_rejects_new_controlled_turn(adapter_cls):
    from app.runtimes.base import RuntimeProtocolError

    current = session()
    current.status = "completed"
    with pytest.raises(RuntimeProtocolError, match="session_not_running"):
        await adapter_cls().complete_turn(current, "facts", request_id="one", timeout=1)


async def test_mock_does_not_claim_real_controlled_response_support():
    from app.runtimes.base import RuntimeProtocolError

    assert MockAdapter().supports_controlled_turn is False
    with pytest.raises(RuntimeProtocolError, match="controlled_turn_unsupported"):
        await MockAdapter().complete_turn(session(), "facts", request_id="one", timeout=1)


async def test_openclaw_truncated_projection_is_not_a_complete_reply(monkeypatch):
    from app.runtimes.base import RuntimeProtocolError

    gateway, adapter = Gateway(), OpenClawAdapter()
    send = gateway.call

    async def truncated(method, params, **kwargs):
        result = await send(method, params, **kwargs)
        if method == "chat.send":
            matching = gateway.matching_event
            matching["payload"]["message"]["__openclaw"] = {
                "truncated": True,
                "reason": "display-cap",
            }
        return result

    monkeypatch.setattr(gateway, "call", truncated)
    monkeypatch.setattr(adapter, "_connect", lambda instance: gateway)
    with pytest.raises(RuntimeProtocolError, match="reply_truncated"):
        await adapter.complete_turn(session(), "facts", request_id="one", timeout=1)


async def test_controlled_deadline_includes_openclaw_connection(monkeypatch):
    class SlowConnect(Gateway):
        async def __aenter__(self):
            await asyncio.sleep(0.1)
            return self

    adapter, gateway = OpenClawAdapter(), SlowConnect()
    monkeypatch.setattr(adapter, "_connect", lambda instance: gateway)
    with pytest.raises(TimeoutError):
        await adapter.complete_turn(session(), "facts", request_id="one", timeout=0.01)
    assert gateway.calls == []


@pytest.mark.parametrize("adapter_cls", [HermesAdapter, OpenClawAdapter])
@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), 901])
async def test_controlled_turn_rejects_unbounded_deadline(adapter_cls, timeout):
    from app.runtimes.base import RuntimeProtocolError

    with pytest.raises(RuntimeProtocolError, match="invalid_turn_timeout"):
        await adapter_cls().complete_turn(session(), "facts", request_id="one", timeout=timeout)


async def test_hermes_transport_failure_after_run_creation_requests_stop(monkeypatch):
    stopped = []

    def respond(request):
        if request.url.path.endswith("/stop"):
            stopped.append(request.url.path)
            return httpx.Response(200, json={"ok": True})
        if request.method == "POST":
            return httpx.Response(202, json={"run_id": "known-run"})
        raise httpx.ConnectError("synthetic control connection lost", request=request)

    hermes_transport(monkeypatch, respond)
    current = session()
    with pytest.raises(httpx.ConnectError):
        await HermesAdapter().complete_turn(current, "facts", request_id="one", timeout=1)
    assert stopped == ["/v1/runs/known-run/stop"]
    assert current.status == "failed"


async def test_openclaw_transport_failure_after_ack_requests_targeted_abort(monkeypatch):
    gateway, adapter, current = Gateway(), OpenClawAdapter(), session()
    call = gateway.call

    async def interrupted(method, params, **kwargs):
        if method == "agent.wait":
            raise ConnectionError("synthetic control connection lost")
        if method == "chat.abort":
            await call(method, params, **kwargs)
            return {"aborted": True, "runIds": [params["runId"]]}
        return await call(method, params, **kwargs)

    monkeypatch.setattr(gateway, "call", interrupted)
    monkeypatch.setattr(adapter, "_connect", lambda instance: gateway)
    with pytest.raises(ConnectionError):
        await adapter.complete_turn(current, "facts", request_id="one", timeout=1)
    aborts = [params for method, params in gateway.calls if method == "chat.abort"]
    assert len(aborts) == 1
    assert aborts[0]["runId"] == gateway.calls[0][1]["idempotencyKey"]
    assert current.status == "failed"
    assert current.cancel_confirmed is False  # Abort receipt is not a terminal observation.


async def test_openclaw_bad_ack_cannot_redirect_cancellation_to_another_run(monkeypatch):
    from app.runtimes.base import RuntimeProtocolError

    gateway, adapter, current = Gateway(), OpenClawAdapter(), session()
    call = gateway.call

    async def wrong_ack(method, params, **kwargs):
        result = await call(method, params, **kwargs)
        return {"runId": "someone-elses-run"} if method == "chat.send" else result

    monkeypatch.setattr(gateway, "call", wrong_ack)
    monkeypatch.setattr(adapter, "_connect", lambda instance: gateway)
    with pytest.raises(RuntimeProtocolError, match="run_identity_mismatch"):
        await adapter.complete_turn(current, "facts", request_id="one", timeout=1)
    aborts = [params for method, params in gateway.calls if method == "chat.abort"]
    assert aborts[0]["runId"] == gateway.calls[0][1]["idempotencyKey"]


async def test_hermes_lost_submission_response_does_not_leave_session_reusable(monkeypatch):
    def lost_ack(request):
        raise httpx.ReadError("server may have started the request", request=request)

    hermes_transport(monkeypatch, lost_ack)
    current = session()
    with pytest.raises(httpx.ReadError):
        await HermesAdapter().complete_turn(current, "facts", request_id="one", timeout=1)
    assert current.status == "failed"
    assert current.cancel_confirmed is False


def replay_runtime(monkeypatch, adapter_cls):
    adapter, current, requests, seen = adapter_cls(), session(), [], {}
    if adapter_cls is HermesAdapter:

        def respond(request):
            if request.method == "POST":
                key = request.headers["Idempotency-Key"]
                requests.append(key)
                seen.setdefault(key, f"run-{len(seen) + 1}")
                return httpx.Response(202, json={"run_id": seen[key]})
            return httpx.Response(200, json={"status": "completed", "output": '{"kind":"read"}'})

        hermes_transport(monkeypatch, respond)
    else:

        class ReplayGateway(Gateway):
            async def call(self, method, params, **kwargs):
                if method == "chat.send":
                    key = params["idempotencyKey"]
                    requests.append(key)
                    if key in seen:
                        # The pinned gateway replays its ACK, not the final event.
                        return {"runId": key, "status": "ok"}
                    seen[key] = True
                return await super().call(method, params, **kwargs)

        monkeypatch.setattr(adapter, "_connect", lambda instance: ReplayGateway())
    return adapter, current, requests


@pytest.mark.parametrize("adapter_cls", [HermesAdapter, OpenClawAdapter])
@pytest.mark.parametrize("concurrent", [False, True])
async def test_controlled_replay_reuses_reply_before_transport(
    monkeypatch, adapter_cls, concurrent
):
    adapter, current, requests = replay_runtime(monkeypatch, adapter_cls)

    async def turn():
        return await adapter.complete_turn(current, "facts", request_id="one", timeout=0.1)

    if concurrent:
        first, replay = await asyncio.gather(turn(), turn())
    else:
        first, replay = await turn(), await turn()
    assert replay == first
    assert len(requests) == 1
    assert current.status == "running"
    next_reply = await adapter.complete_turn(current, "receipt", request_id="two", timeout=0.1)
    assert next_reply.run_id != first.run_id
    assert len(requests) == 2


@pytest.mark.parametrize("adapter_cls", [HermesAdapter, OpenClawAdapter])
async def test_controlled_replay_rejects_changed_prompt_without_poisoning_session(
    monkeypatch, adapter_cls
):
    adapter, current, requests = replay_runtime(monkeypatch, adapter_cls)
    await adapter.complete_turn(current, "facts", request_id="one", timeout=0.1)
    with pytest.raises(RuntimeProtocolError, match="request_reuse_conflict"):
        await adapter.complete_turn(current, "changed", request_id="one", timeout=0.1)
    assert len(requests) == 1
    assert current.status == "running"
    await adapter.complete_turn(current, "receipt", request_id="two", timeout=0.1)
    assert len(requests) == 2


@pytest.mark.parametrize("adapter_cls", [HermesAdapter, OpenClawAdapter])
@pytest.mark.parametrize("pending_cancel", [False, True])
async def test_controlled_reply_is_rejected_after_cancellation_starts(
    monkeypatch, adapter_cls, pending_cancel
):
    adapter, current = adapter_cls(), session()
    accepted, release_reply, stopping, release_stop = (asyncio.Event() for _ in range(4))
    stops = 0
    if not pending_cancel:
        release_stop.set()

    async def stop():
        nonlocal stops
        stops += 1
        stopping.set()
        if stops == 1:
            await release_stop.wait()

    if adapter_cls is HermesAdapter:

        async def respond(request):
            if request.url.path.endswith("/stop"):
                await stop()
                return httpx.Response(200, json={"ok": True})
            if request.method == "POST":
                return httpx.Response(202, json={"run_id": "known-run"})
            accepted.set()
            await release_reply.wait()
            return httpx.Response(200, json={"status": "completed", "output": '{"kind":"read"}'})

        hermes_transport(monkeypatch, respond)
    else:

        class DelayedGateway(Gateway):
            async def call(self, method, params, **kwargs):
                if method == "agent.wait":
                    accepted.set()
                    await release_reply.wait()
                if method == "chat.abort":
                    await stop()
                return await super().call(method, params, **kwargs)

        gateway = DelayedGateway()
        monkeypatch.setattr(adapter, "_connect", lambda instance: gateway)

    turn = asyncio.create_task(adapter.complete_turn(current, "facts", request_id="one", timeout=2))
    cancel = None
    try:
        await asyncio.wait_for(accepted.wait(), 1)
        cancel = asyncio.create_task(adapter.cancel_task(current))
        await asyncio.wait_for(stopping.wait(), 1)
        if not pending_cancel:
            await cancel
        release_reply.set()
        with pytest.raises(RuntimeProtocolError, match="session_not_running"):
            await asyncio.wait_for(asyncio.shield(turn), 1)
        assert current.status == "cancelled"
    finally:
        release_reply.set()
        release_stop.set()
        await asyncio.gather(
            turn, *([cancel] if cancel is not None else []), return_exceptions=True
        )


async def test_cancel_during_controlled_connection_never_aborts_shared_session(monkeypatch):
    adapter, current = OpenClawAdapter(), session()
    connecting, release_connection = asyncio.Event(), asyncio.Event()
    other = Gateway()

    class SlowConnection(Gateway):
        async def __aenter__(self):
            connecting.set()
            await release_connection.wait()
            return self

    clients = iter([SlowConnection(), other])
    monkeypatch.setattr(adapter, "_connect", lambda instance: next(clients))
    turn = asyncio.create_task(adapter.complete_turn(current, "facts", request_id="one", timeout=2))
    try:
        await asyncio.wait_for(connecting.wait(), 1)
        await adapter.cancel_task(current)
        assert other.calls == []
    finally:
        release_connection.set()
        result = (await asyncio.gather(turn, return_exceptions=True))[0]
        assert isinstance(result, RuntimeProtocolError)


@pytest.mark.parametrize("adapter_cls", [HermesAdapter, OpenClawAdapter])
async def test_controlled_turn_rejects_an_active_background_stream(adapter_cls):
    current = session()
    current.background = asyncio.create_task(asyncio.Event().wait())
    try:
        with pytest.raises(RuntimeProtocolError, match="session_already_streaming"):
            await adapter_cls().complete_turn(current, "facts", request_id="one", timeout=1)
    finally:
        current.background.cancel()
        await asyncio.gather(current.background, return_exceptions=True)


@pytest.mark.parametrize("adapter_cls", [HermesAdapter, OpenClawAdapter])
async def test_controlled_reply_cache_is_bounded_without_evicting_replay_protection(
    monkeypatch, adapter_cls
):
    adapter, current, requests = replay_runtime(monkeypatch, adapter_cls)
    first = await adapter.complete_turn(current, "facts-0", request_id="round-0", timeout=1)
    for index in range(1, 32):
        await adapter.complete_turn(
            current, f"facts-{index}", request_id=f"round-{index}", timeout=1
        )
    with pytest.raises(RuntimeProtocolError, match="controlled_turn_limit"):
        await adapter.complete_turn(current, "overflow", request_id="round-32", timeout=1)
    replay = await adapter.complete_turn(current, "facts-0", request_id="round-0", timeout=1)
    assert replay == first
    assert len(requests) == 32
    assert current.status == "running"
