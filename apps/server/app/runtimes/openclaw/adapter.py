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
from app.runtimes import workspace_artifacts
from app.runtimes.base import (
    EmployeeRef,
    ProducedArtifact,
    RuntimeAdapter,
    RuntimeCapabilities,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeInfo,
    RuntimeInstance,
    RuntimeProtocolError,
    RuntimeReply,
    RuntimeRunReference,
    RuntimeRunStatus,
    RuntimeSession,
    RuntimeStatus,
    TaskContext,
    accept_remote_run,
    cached_controlled_reply,
    complete_reply_text,
    controlled_request_key,
    observe_remote_terminal,
    remember_controlled_reply,
)
from app.runtimes.container_common import refresh_status, resolve_container_instance
from app.runtimes.docker.service import get_docker_service
from app.runtimes.openclaw import artifacts as artifact_transport
from app.runtimes.openclaw.rpc import GatewayClient

logger = get_logger(__name__)

_HTTP_TIMEOUT = 10.0
_DEFAULT_RUN_TIMEOUT = 900.0
_SESSION_KEY = "agent:main:main"


class OpenClawAdapter(RuntimeAdapter):
    type = RuntimeType.openclaw
    implemented = True
    supports_controlled_turn = True
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
        if session.attempt is not None:
            task = context.get("task_context")
            prompt += workspace_artifacts.instructions(
                session,
                self.type.value,
                required_types=list(session.artifact_required_types)
                if session.artifact_required_types is not None
                else task.required_artifact_types
                if isinstance(task, TaskContext)
                else [],
            )
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

    async def complete_turn(
        self, session: RuntimeSession, prompt: str, *, request_id: str, timeout: float = 60
    ) -> RuntimeReply:
        key = controlled_request_key(session, request_id, timeout)
        session_key = f"agent:main:eidolon-{session.id}"
        async with asyncio.timeout(timeout), session.turn_lock:
            controlled_request_key(session, request_id, timeout)
            cached = cached_controlled_reply(session, request_id, prompt)
            if cached is not None:
                return cached
            async with self._connect(session.instance) as client:
                run_id = None
                reply_task = None
                try:
                    controlled_request_key(session, request_id, timeout)
                    # The fixed protocol uses our idempotency key as the run ID.
                    # Retain it even when the ACK is lost; never abort an ACK's foreign ID.
                    run_id = key
                    session.remote_run_id = run_id
                    session.cancel_confirmed = None
                    started = await client.call(
                        "chat.send",
                        {
                            "sessionKey": session_key,
                            "message": prompt,
                            "idempotencyKey": key,
                            "thinking": "off",
                            "timeoutMs": max(1, int(timeout * 1000)),
                        },
                        timeout=timeout,
                    )
                    if not isinstance(started, dict) or started.get("runId") != run_id:
                        raise RuntimeProtocolError("run_identity_mismatch")
                    accept_remote_run(session, run_id, session_key)
                    controlled_request_key(session, request_id, timeout)
                    reply_task = asyncio.create_task(
                        self._complete_chat_reply(client, session_key, run_id),
                        name=f"openclaw-controlled-{session.id}",
                    )
                    terminal = await client.call(
                        "agent.wait",
                        {"runId": run_id, "timeoutMs": max(1, int(timeout * 1000))},
                        timeout=timeout,
                    )
                    if isinstance(terminal, dict) and terminal.get("runId", run_id) == run_id:
                        if terminal.get("status") == "ok":
                            observe_remote_terminal(session, "completed", "openclaw.wait")
                        elif terminal.get("status") in {"failed", "error", "aborted"}:
                            observe_remote_terminal(session, "failed", "openclaw.wait")
                    if (
                        not isinstance(terminal, dict)
                        or terminal.get("status") != "ok"
                        or terminal.get("runId", run_id) != run_id
                    ):
                        raise RuntimeProtocolError("run_not_successful")
                    # terminalReply and chat.history can be truncated or belong to
                    # another turn. Only the final event with both identities qualifies.
                    text = await reply_task
                    reply = RuntimeReply(session.id, request_id, run_id, text)
                except (Exception, asyncio.CancelledError):
                    if run_id:
                        if session.status == "running":
                            session.status = "failed"
                        confirmed = False
                        try:
                            result = await client.call(
                                "chat.abort",
                                {"sessionKey": session_key, "runId": run_id},
                                timeout=5,
                            )
                            confirmed = (
                                isinstance(result, dict)
                                and result.get("aborted") is True
                                and run_id in result.get("runIds", [])
                            )
                        except Exception:
                            logger.warning("controlled OpenClaw abort could not be confirmed")
                        session.cancel_confirmed = confirmed
                    raise
                finally:
                    if reply_task is not None:
                        reply_task.cancel()
                        await asyncio.gather(reply_task, return_exceptions=True)
            return remember_controlled_reply(session, prompt, reply)

    async def query_run(
        self, instance: RuntimeInstance, reference: RuntimeRunReference
    ) -> RuntimeRunStatus:
        async with self._connect(instance) as client:
            payload = await client.call(
                "agent.wait", {"runId": reference.remote_run_id, "timeoutMs": 1}, timeout=5
            )
        if (
            not isinstance(payload, dict)
            or payload.get("runId", reference.remote_run_id) != reference.remote_run_id
        ):
            raise RuntimeProtocolError("run_identity_mismatch")
        state = payload.get("state") or payload.get("status")
        normalized = (
            "completed"
            if state in {"ok", "completed"}
            else "failed"
            if state in {"failed", "error", "aborted"}
            else "running"
            if state in {"running", "queued", "started"}
            else "unknown"
        )
        return RuntimeRunStatus(normalized, "openclaw.wait")

    @staticmethod
    async def _complete_chat_reply(client: GatewayClient, session_key: str, run_id: str) -> str:
        while True:
            event = await client.events.get()
            payload = event.get("payload")
            if (
                event.get("event") != "chat"
                or not isinstance(payload, dict)
                or payload.get("sessionKey") != session_key
                or payload.get("runId") != run_id
            ):
                continue
            if payload.get("state") in {"error", "aborted"}:
                raise RuntimeProtocolError("run_not_successful")
            if payload.get("state") != "final":
                continue
            message = payload.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise RuntimeProtocolError("assistant_reply_missing")
            projection = message.get("__openclaw") or {}
            if not isinstance(projection, dict) or projection.get("truncated"):
                raise RuntimeProtocolError("reply_truncated")
            content = message.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    block["text"]
                    for block in content
                    if isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                )
            return complete_reply_text(content)

    async def cancel_task(self, session: RuntimeSession) -> None:
        session.status = "cancelled"
        if session.background and not session.background.done():
            session.background.cancel()
        controlled = session.remote_session_ref != _SESSION_KEY and (
            session.remote_run_id is not None or session.turn_lock.locked()
        )
        run_id = (
            session.remote_run_id
            if controlled
            else session.instance.details.get(f"run_id:{session.id}")
        )
        if controlled and run_id is None:
            # A control connection has not submitted a run yet. Do not redirect
            # its cancellation to the ordinary shared chat session.
            return
        try:
            async with self._connect(session.instance) as client:
                await client.call(
                    "chat.abort",
                    {
                        "sessionKey": (
                            f"agent:main:eidolon-{session.id}" if controlled else _SESSION_KEY
                        ),
                        **({"runId": run_id} if run_id else {}),
                    },
                )
        except Exception:
            logger.exception("openclaw abort failed", extra={"task_id": session.task_id})

    async def get_artifacts(self, session: RuntimeSession) -> list[ProducedArtifact]:
        artifact_transport.require_completed_origin(session)
        try:
            async with self._connect(session.instance) as client:
                return await artifact_transport.collect_worker(client, session)
        except RuntimeProtocolError:
            raise
        except Exception:
            raise RuntimeProtocolError("artifact_transport_failed") from None

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
        waiter = None
        try:
            async with asyncio.timeout(timeout), self._connect(instance) as client:
                result = await client.call(
                    "chat.send",
                    {
                        "sessionKey": _SESSION_KEY,
                        "message": prompt,
                        "idempotencyKey": session.attempt.idempotency_key
                        if session.attempt is not None
                        else session.id,
                    },
                )
                run_id = (result or {}).get("runId") or (result or {}).get("run_id")
                accept_remote_run(session, run_id, _SESSION_KEY)
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
                    payload = message.get("params") or message.get("payload") or {}
                    if not isinstance(payload, dict) or payload.get("runId", run_id) != run_id:
                        continue
                    event = self._map_event(message)
                    if event is not None:
                        await session.queue.put(event)
                terminal = waiter.result() if waiter is not None and not waiter.cancelled() else {}
                if not isinstance(terminal, dict) or terminal.get("runId", run_id) != run_id:
                    raise RuntimeProtocolError("run_identity_mismatch")
                state = str((terminal or {}).get("state") or (terminal or {}).get("status") or "")
                if state in ("failed", "error", "aborted"):
                    observe_remote_terminal(session, "failed", "openclaw.wait")
                    session.status = "failed"
                    await session.queue.put(
                        RuntimeEvent(RuntimeEventKind.error, {"message": f"run {state}"})
                    )
                elif state in ("completed", "ok"):
                    observe_remote_terminal(session, "completed", "openclaw.wait")
                    session.status = "completed"
                    await session.queue.put(
                        RuntimeEvent(RuntimeEventKind.completed, {"summary": "run completed"})
                    )
                else:
                    raise RuntimeProtocolError("run_terminal_unknown")
        except asyncio.CancelledError:
            session.status = "cancelled"
            raise
        except Exception:
            session.status = "failed"
            logger.warning("openclaw run failed", extra={"task_id": session.task_id})
            await session.queue.put(
                RuntimeEvent(RuntimeEventKind.error, {"message": "runtime_error"})
            )
        finally:
            if waiter is not None:
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)

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
                    {"message": "remote_operation_failed"},
                )
            return RuntimeEvent(
                RuntimeEventKind.status, {"detail": f"operation {state or 'update'}"}
            )
        if name.startswith("session."):
            return RuntimeEvent(RuntimeEventKind.status, {"detail": name})
        return None  # ignore device.pair.*, sessions.changed, etc.
