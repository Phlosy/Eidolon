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
import uuid
from collections.abc import AsyncIterator

import httpx

from app.brain.projection import append_behavior_block
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
from app.runtimes.openclaw.rpc import GatewayClient

logger = get_logger(__name__)

_HTTP_TIMEOUT = 10.0
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
            brain_projection=True,  # behavior block is appended to chat.send message
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
        # 接缝 7 / §8 T1：投影拼进 chat.send 的 message（brain 目录不挂载，写文件不会生效）。
        prompt = append_behavior_block(prompt, context.get("behavior_policy"))
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

    def _connect(self, instance: RuntimeInstance) -> "GatewayClient":
        base_url = instance.details.get("base_url")
        if not base_url:
            raise RuntimeError("openclaw instance has no base_url (not provisioned?)")
        url = base_url.replace("http://", "ws://").replace("https://", "wss://")
        return GatewayClient(
            url,
            instance.details.get("api_key") or "",
            device_key=instance.details.get("device_key"),
        )

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
