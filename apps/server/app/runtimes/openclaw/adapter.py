"""OpenClaw adapter (v0.2) — real implementation over the gateway WS JSON-RPC.

Per the verified deployment facts (docs/research.md, v0.2 report §6/§7): the
gateway listens on container port 18789; the control plane is WebSocket
JSON-RPC with frames ``{type: "req"|"res"|"event"}``, token auth in
``connect.params.auth.token`` and ``client.mode: "backend"``. Tasks are driven
with ``chat.send`` (sessionKey ``agent:main:main`` → runId), streamed via
``session.message`` / ``session.tool`` events, awaited with ``agent.wait``,
aborted with ``chat.abort``. Liveness: ``GET /healthz``. Unknown event shapes
degrade to status events instead of crashing the stream.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import httpx
import websockets

from app.core.logging import get_logger
from app.models.enums import RuntimeType
from app.runtimes.base import (
    EmployeeRef,
    ProducedArtifact,
    RuntimeAdapter,
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeInfo,
    RuntimeInstance,
    RuntimeSession,
    RuntimeStatus,
    TaskContext,
)
from app.runtimes.container_common import refresh_status, resolve_container_instance
from app.runtimes.docker.service import get_docker_service

logger = get_logger(__name__)

_HTTP_TIMEOUT = 10.0
_HANDSHAKE_TIMEOUT = 15.0
_DEFAULT_RUN_TIMEOUT = 900.0
_SESSION_KEY = "agent:main:main"


class OpenClawAdapter(RuntimeAdapter):
    type = RuntimeType.openclaw
    implemented = True
    tested_min_version = "2026.8.1"
    tested_max_version = None  # no known incompatible upper bound yet

    def detect(self) -> bool:
        """Docker-backed runtime: usable when the daemon is reachable."""
        return get_docker_service().ping()

    def get_capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            chat=True,
            task=True,
            filesystem=True,  # per-agent workspace
            terminal=True,
            web=True,
            memory=True,  # MEMORY.md + daily logs
            skills=True,  # SKILL.md / ClawHub
            scheduler=True,  # cron methods
            streaming=True,  # session.* WS events
            artifacts=True,  # artifacts.list/get/download RPC
        )

    def supported_providers(self) -> list[str]:
        from app.providers.registry import supported_providers_for

        return supported_providers_for(self.type.value)

    # ---- instance lifecycle (provisioning lives in the manager) ----

    async def create_instance(self, employee: EmployeeRef, config: dict) -> RuntimeInstance:
        return resolve_container_instance(employee.id, self.type.value)

    async def start(self, instance: RuntimeInstance) -> None:
        instance.status = RuntimeStatus.running

    async def stop(self, instance: RuntimeInstance) -> None:
        instance.status = RuntimeStatus.stopped

    async def get_status(self, instance: RuntimeInstance) -> RuntimeStatus:
        base_url = instance.details.get("base_url")
        if base_url:
            try:
                async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                    response = await client.get(f"{base_url}/healthz")
                if response.status_code < 400:
                    return RuntimeStatus.running
            except Exception:
                pass
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            return refresh_status(db, instance)

    # ---- sessions / task driving ----

    async def create_session(self, instance: RuntimeInstance, task: TaskContext) -> RuntimeSession:
        return RuntimeSession(
            id=f"openclaw-{uuid.uuid4().hex[:12]}",
            instance=instance,
            task_id=task.task_id,
        )

    async def send_task(self, session: RuntimeSession, prompt: str, context: dict) -> None:
        config = context.get("runtime_config") or {}
        timeout = float(config.get("run_timeout_seconds", _DEFAULT_RUN_TIMEOUT))
        session.background = asyncio.create_task(
            self._pump(session, prompt, timeout), name=f"openclaw-run-{session.id}"
        )

    async def send_message(self, session: RuntimeSession, message: str) -> None:
        try:
            async with self._connect(session.instance) as client:
                await client.call(
                    "chat.send",
                    {
                        "sessionKey": _SESSION_KEY,
                        "message": message,
                        "idempotencyKey": f"{session.id}-msg-{uuid.uuid4().hex[:8]}",
                    },
                )
            await session.queue.put(
                RuntimeEvent(RuntimeEventKind.message, {"role": "user", "content": message})
            )
        except Exception as exc:
            await session.queue.put(
                RuntimeEvent(RuntimeEventKind.error, {"message": str(exc)[:500]})
            )

    async def stream_events(self, session: RuntimeSession) -> AsyncIterator[RuntimeEvent]:
        while True:
            event = await session.queue.get()
            yield event
            if event.kind in (RuntimeEventKind.completed, RuntimeEventKind.error):
                break

    async def cancel_task(self, session: RuntimeSession) -> None:
        if session.background and not session.background.done():
            session.background.cancel()
        run_id = session.instance.details.get(f"run_id:{session.id}")
        try:
            async with self._connect(session.instance) as client:
                await client.call(
                    "chat.abort",
                    {"sessionKey": _SESSION_KEY, **({"runId": run_id} if run_id else {})},
                )
        except Exception:
            logger.exception("openclaw abort failed", extra={"task_id": session.task_id})
        session.status = "cancelled"

    async def get_artifacts(self, session: RuntimeSession) -> list[ProducedArtifact]:
        run_id = session.instance.details.get(f"run_id:{session.id}")
        if not run_id:
            return list(session.artifacts)
        try:
            async with self._connect(session.instance) as client:
                result = await client.call(
                    "artifacts.list", {"sessionKey": _SESSION_KEY, "runId": run_id}
                )
                items = result.get("artifacts") if isinstance(result, dict) else result
                for item in items or []:
                    if not isinstance(item, dict):
                        continue
                    content = ""
                    if item.get("id"):
                        try:
                            got = await client.call(
                                "artifacts.get", {"sessionKey": _SESSION_KEY, "id": item["id"]}
                            )
                            content = str((got or {}).get("content") or "")
                        except Exception:
                            logger.exception(
                                "artifacts.get failed", extra={"task_id": session.task_id}
                            )
                    session.artifacts.append(
                        ProducedArtifact(
                            type=str(item.get("type") or "other"),
                            title=str(item.get("title") or item.get("name") or "artifact"),
                            content=content,
                        )
                    )
        except Exception:
            logger.exception("openclaw artifacts.list failed", extra={"task_id": session.task_id})
        return list(session.artifacts)

    async def get_runtime_info(self, instance: RuntimeInstance) -> RuntimeInfo:
        return RuntimeInfo(
            type=self.type,
            version=instance.details.get("runtime_version") or "unknown",
            details={
                "container": instance.details.get("container_name"),
                "base_url": instance.details.get("base_url"),
            },
        )

    # ---- run pump ----

    def _connect(self, instance: RuntimeInstance) -> "_RpcClient":
        base_url = instance.details.get("base_url")
        if not base_url:
            raise RuntimeError("openclaw instance has no base_url (not provisioned?)")
        url = base_url.replace("http://", "ws://").replace("https://", "wss://")
        return _RpcClient(url, instance.details.get("api_key") or "")

    async def _pump(self, session: RuntimeSession, prompt: str, timeout: float) -> None:
        instance = session.instance
        try:
            async with asyncio.timeout(timeout), self._connect(instance) as client:
                result = await client.call(
                    "chat.send",
                    {
                        "sessionKey": _SESSION_KEY,
                        "message": prompt,
                        "idempotencyKey": session.id,
                    },
                )
                run_id = (result or {}).get("runId") or (result or {}).get("run_id")
                if run_id:
                    instance.details[f"run_id:{session.id}"] = run_id
                await session.queue.put(
                    RuntimeEvent(
                        RuntimeEventKind.status, {"detail": "run started", "run_id": run_id}
                    )
                )
                waiter = (
                    asyncio.create_task(
                        client.call("agent.wait", {"runId": run_id}, timeout=timeout)
                    )
                    if run_id
                    else None
                )
                # Drain session.* events until agent.wait resolves (terminal snapshot).
                while True:
                    if waiter is not None and waiter.done() and client.events.empty():
                        break
                    try:
                        message = await asyncio.wait_for(client.events.get(), timeout=1.0)
                    except TimeoutError:
                        continue
                    event = self._map_event(message)
                    if event is not None:
                        await session.queue.put(event)
                terminal = waiter.result() if waiter is not None and not waiter.cancelled() else {}
                state = str((terminal or {}).get("state") or (terminal or {}).get("status") or "")
                if state in ("failed", "error", "aborted"):
                    session.status = "failed"
                    await session.queue.put(
                        RuntimeEvent(RuntimeEventKind.error, {"message": f"run {state}"})
                    )
                else:
                    session.status = "completed"
                    await session.queue.put(
                        RuntimeEvent(RuntimeEventKind.completed, {"summary": "run completed"})
                    )
        except asyncio.CancelledError:
            session.status = "cancelled"
            raise
        except Exception as exc:
            session.status = "failed"
            logger.exception("openclaw run failed", extra={"task_id": session.task_id})
            await session.queue.put(
                RuntimeEvent(RuntimeEventKind.error, {"message": str(exc)[:500]})
            )

    @staticmethod
    def _map_event(message: dict) -> RuntimeEvent | None:
        """Defensively map OpenClaw event frames onto RuntimeEvent kinds."""
        name = str(message.get("event") or message.get("method") or "")
        payload = message.get("params") or message.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        if name == "session.message":
            content = payload.get("content") or payload.get("text") or ""
            if isinstance(content, list):  # content blocks
                content = "".join(
                    block.get("text", "") for block in content if isinstance(block, dict)
                )
            role = payload.get("role") or "assistant"
            return RuntimeEvent(RuntimeEventKind.message, {"role": role, "content": str(content)})
        if name == "session.tool":
            return RuntimeEvent(
                RuntimeEventKind.tool_call,
                {"tool": payload.get("tool") or payload.get("name") or "unknown"},
            )
        if name == "session.operation":
            state = str(payload.get("state") or "")
            if state in ("failed", "error"):
                return RuntimeEvent(
                    RuntimeEventKind.error,
                    {"message": str(payload.get("error") or "operation failed")[:500]},
                )
            return RuntimeEvent(
                RuntimeEventKind.status, {"detail": f"operation {state or 'update'}"}
            )
        if name.startswith("session."):
            return RuntimeEvent(RuntimeEventKind.status, {"detail": name})
        return None  # ignore device.pair.*, sessions.changed, etc.


class _RpcClient:
    """One WS connection with token-auth handshake, a single reader task,
    res-frame dispatch to pending calls, and an event frame queue."""

    def __init__(self, url: str, token: str) -> None:
        self._url = url
        self._token = token
        self._ws = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self.events: asyncio.Queue = asyncio.Queue()

    async def __aenter__(self) -> "_RpcClient":
        self._ws = await websockets.connect(self._url, max_size=25 * 1024 * 1024)
        connect_req = {
            "type": "req",
            "id": "connect-0",
            "method": "connect",
            "params": {
                "auth": {"token": self._token},
                "client": {"mode": "backend", "name": "eidolon", "version": "0.2.0"},
            },
        }
        async with asyncio.timeout(_HANDSHAKE_TIMEOUT):
            # The server may push a pre-connect challenge event first; we always
            # answer with the connect req carrying the shared-secret token.
            await self._ws.send(json.dumps(connect_req))
            async for raw in self._ws:
                message = json.loads(raw)
                mtype = message.get("type")
                if mtype == "hello-ok":
                    break
                if mtype == "res" and message.get("id") == "connect-0":
                    if message.get("ok") is False or message.get("error"):
                        raise RuntimeError(
                            f"openclaw connect failed: {str(message.get('error'))[:300]}"
                        )
                    break
        self._reader = asyncio.create_task(self._read_loop(), name="openclaw-ws-reader")
        return self

    async def __aexit__(self, *exc) -> None:
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        if self._ws is not None:
            await self._ws.close()

    async def _read_loop(self) -> None:
        async for raw in self._ws:
            message = json.loads(raw)
            if message.get("type") == "res" and message.get("id") in self._pending:
                future = self._pending.pop(message["id"])
                if not future.done():
                    future.set_result(message)
            elif message.get("type") == "event":
                await self.events.put(message)

    async def call(self, method: str, params: dict, timeout: float = 60.0) -> dict:
        """Send one req frame and await its matching res frame."""
        request_id = f"eidolon-{uuid.uuid4().hex[:10]}"
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._ws.send(
            json.dumps({"type": "req", "id": request_id, "method": method, "params": params})
        )
        try:
            message = await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)
        if message.get("ok") is False or message.get("error"):
            raise RuntimeError(f"{method} failed: {str(message.get('error'))[:300]}")
        return message.get("result") or {}
