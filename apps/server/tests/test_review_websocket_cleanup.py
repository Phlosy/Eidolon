"""Exercise disconnects through ASGI without TestClient's forced task cleanup."""

from __future__ import annotations

import asyncio
import json

import pytest

from app import main
from app.core.config import settings
from app.events.bus import EventBus


class _Connection:
    def __init__(self, event_bus: EventBus) -> None:
        self.bus = event_bus
        self.incoming: asyncio.Queue = asyncio.Queue()
        self.outgoing: asyncio.Queue = asyncio.Queue()
        self.send_error: Exception | None = None
        self.block_send = False
        self.send_started = asyncio.Event()
        self.release_send = asyncio.Event()
        self.io_tasks: set[asyncio.Task] = set()
        self.handler: asyncio.Task | None = None
        self.subscription: asyncio.Queue | None = None

    async def receive(self):
        self.io_tasks.add(asyncio.current_task())
        message = await self.incoming.get()
        if isinstance(message, Exception):
            raise message
        return message

    async def send(self, message):
        self.io_tasks.add(asyncio.current_task())
        if message["type"] == "websocket.send":
            self.send_started.set()
            if self.send_error is not None:
                raise self.send_error
            if self.block_send:
                await self.release_send.wait()
        self.outgoing.put_nowait(message)

    def disconnect(self):
        self.incoming.put_nowait({"type": "websocket.disconnect", "code": 1000})

    async def assert_released(self):
        assert self.handler is not None
        done, _ = await asyncio.wait({self.handler}, timeout=0.2)
        assert (bool(done), len(self.bus._subscribers)) == (True, 0), (
            "A client disconnect must finish the handler and release its subscription"
        )
        assert all(task.done() for task in self.io_tasks)
        assert not self.incoming._getters, "The disconnect reader must not outlive the handler"
        assert self.subscription is not None
        assert not self.subscription._getters, "The event sender must not outlive the handler"


@pytest.fixture
async def connection(monkeypatch):
    event_bus = EventBus()
    event_bus.attach_loop()
    monkeypatch.setattr(main, "bus", event_bus)
    monkeypatch.setattr(settings, "auth_required", False)
    connection = _Connection(event_bus)
    connection.incoming.put_nowait({"type": "websocket.connect"})
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "scheme": "ws",
        "path": "/ws/events",
        "raw_path": b"/ws/events",
        "query_string": b"",
        "headers": [],
        "client": ("test", 1234),
        "server": ("test", 80),
        "subprotocols": [],
    }
    connection.handler = asyncio.create_task(main.app(scope, connection.receive, connection.send))
    try:
        accepted = await asyncio.wait_for(connection.outgoing.get(), timeout=1)
        assert accepted["type"] == "websocket.accept"
        assert len(event_bus._subscribers) == 1
        connection.subscription = next(iter(event_bus._subscribers))
        yield connection
    finally:
        connection.release_send.set()
        connection.handler.cancel()
        await asyncio.gather(connection.handler, return_exceptions=True)
        assert not event_bus._subscribers


async def test_idle_disconnect_releases_handler_and_subscription(connection):
    connection.disconnect()
    await connection.assert_released()
    await connection.handler
    assert connection.outgoing.empty(), "Disconnect cleanup needs no subsequent server event"


async def test_events_keep_streaming_after_client_frames_until_disconnect(connection):
    connection.incoming.put_nowait({"type": "websocket.receive", "text": "ignored"})
    connection.incoming.put_nowait({"type": "websocket.receive", "bytes": b"ignored"})
    for seq in range(2):
        connection.bus.publish("review.websocket", {"seq": seq})
        sent = await asyncio.wait_for(connection.outgoing.get(), timeout=1)
        assert sent["type"] == "websocket.send"
        assert json.loads(sent["text"])["data"] == {"seq": seq}
    connection.disconnect()
    await connection.assert_released()
    await connection.handler


async def test_disconnect_cancels_a_sender_waiting_for_the_transport(connection):
    connection.block_send = True
    connection.bus.publish("review.websocket", {"seq": 0})
    await asyncio.wait_for(connection.send_started.wait(), timeout=1)
    connection.disconnect()
    await connection.assert_released()
    await connection.handler
    assert not connection.release_send.is_set()


async def test_send_disconnect_releases_the_receiver_and_subscription(connection):
    connection.send_error = OSError("connection closed")
    connection.bus.publish("review.websocket", {"seq": 0})
    await asyncio.wait_for(connection.send_started.wait(), timeout=1)
    await connection.assert_released()
    await connection.handler


@pytest.mark.parametrize("direction", ["receive", "send"])
async def test_transport_errors_propagate_after_cleaning_both_tasks(connection, direction):
    error = RuntimeError("transport failed")
    if direction == "receive":
        connection.incoming.put_nowait(error)
    else:
        connection.send_error = error
        connection.bus.publish("review.websocket", {"seq": 0})
    await connection.assert_released()
    with pytest.raises(RuntimeError, match="transport failed"):
        await connection.handler


async def test_cancelling_the_handler_releases_both_waiters_and_subscription(connection):
    # Let a pending receive and event-queue wait begin before cancelling.
    await asyncio.sleep(0)
    connection.handler.cancel()
    await connection.assert_released()
    with pytest.raises(asyncio.CancelledError):
        await connection.handler
