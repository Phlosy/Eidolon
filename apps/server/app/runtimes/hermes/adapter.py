"""Hermes adapter (v0.2) — real implementation against the gateway API server.

Per the verified deployment facts (docs/research.md, v0.2 report §2): the
container runs the Hermes gateway with the OpenAI-compatible API server on
port 8642 (``API_SERVER_ENABLED=true``, bearer ``API_SERVER_KEY``). Tasks are
driven via ``POST /v1/runs`` (with ``Idempotency-Key``) and streamed over SSE
``GET /v1/runs/{id}/events``; cancellation is ``POST /v1/runs/{id}/stop`` and
liveness is ``GET /health``. Event shapes are mapped defensively — unknown
kinds degrade to status events instead of crashing the stream.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from urllib.parse import quote

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

logger = get_logger(__name__)

_HTTP_TIMEOUT = 15.0
_SSE_IDLE_TIMEOUT = 120.0
_DEFAULT_RUN_TIMEOUT = 900.0


class HermesAdapter(RuntimeAdapter):
    type = RuntimeType.hermes
    implemented = True
    supports_controlled_turn = True
    tested_min_version = "2026.8.0"
    tested_max_version = None  # no known incompatible upper bound yet

    def detect(self) -> bool:
        """Docker-backed runtime: usable when the daemon is reachable."""
        return get_docker_service().ping()

    def get_capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(
            chat=True,
            task=True,
            filesystem=True,
            terminal=True,
            web=True,
            memory=True,
            skills=True,
            scheduler=True,  # /api/jobs (cron) API
            streaming=True,  # SSE /v1/runs/{id}/events
            artifacts=True,  # bounded UTF-8 workspace manifest, not an HTTP download endpoint
            brain_projection=True,  # behavior block is appended to POST /v1/runs input
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
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            return refresh_status(db, instance)

    # ---- sessions / task driving ----

    async def create_session(self, instance: RuntimeInstance, task: TaskContext) -> RuntimeSession:
        return RuntimeSession(
            id=f"hermes-{uuid.uuid4().hex[:12]}",
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
        # 接缝 7 / §8 T1：投影在**真正发请求前**拼进 input，而不是写进未挂载的目录。
        prompt = append_behavior_block(prompt, context.get("behavior_policy"))
        timeout = float(config.get("run_timeout_seconds", _DEFAULT_RUN_TIMEOUT))
        session.background = asyncio.create_task(
            self._pump(session, prompt, timeout), name=f"hermes-run-{session.id}"
        )

    async def send_message(self, session: RuntimeSession, message: str) -> None:
        run_id = await self._create_run(
            session.instance, message, f"{session.id}-msg-{uuid.uuid4().hex[:8]}"
        )
        await session.queue.put(
            RuntimeEvent(
                RuntimeEventKind.message, {"role": "user", "content": message, "run_id": run_id}
            )
        )

    async def complete_turn(
        self, session: RuntimeSession, prompt: str, *, request_id: str, timeout: float = 60
    ) -> RuntimeReply:
        key = controlled_request_key(session, request_id, timeout)
        run_id = None
        submitted = False
        try:
            async with asyncio.timeout(timeout), session.turn_lock:
                controlled_request_key(session, request_id, timeout)
                cached = cached_controlled_reply(session, request_id, prompt)
                if cached is not None:
                    return cached
                submitted = True
                run_id = await self._create_run(
                    session.instance, prompt, key, session_id=session.id
                )
                if not isinstance(run_id, str) or not run_id:
                    raise RuntimeProtocolError("run_id_missing")
                accept_remote_run(session, run_id, session.id)
                controlled_request_key(session, request_id, timeout)
                url = f"{session.instance.details['base_url']}/v1/runs/{quote(run_id, safe='')}"
                async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, trust_env=False) as client:
                    while True:
                        response = await client.get(url, headers=self._headers(session.instance))
                        response.raise_for_status()
                        payload = response.json()
                        if not isinstance(payload, dict) or payload.get("run_id", run_id) != run_id:
                            raise RuntimeProtocolError("run_identity_mismatch")
                        status = payload.get("status")
                        if status == "completed":
                            observe_remote_terminal(session, "completed", "hermes.status")
                            reply = RuntimeReply(
                                session_id=session.id,
                                request_id=request_id,
                                run_id=run_id,
                                text=complete_reply_text(payload.get("output")),
                                usage=payload.get("usage")
                                if isinstance(payload.get("usage"), dict)
                                else None,
                            )
                            break
                        if status not in {"started", "running", "queued"}:
                            if status in {"failed", "error", "cancelled", "aborted"}:
                                observe_remote_terminal(session, "failed", "hermes.status")
                            raise RuntimeProtocolError("run_not_successful")
                        await asyncio.sleep(0.1)
                return remember_controlled_reply(session, prompt, reply)
        except (Exception, asyncio.CancelledError):
            if submitted:
                # A stop request is not proof that a remote model has terminated.
                if session.status == "running":
                    session.status = "failed"
                session.cancel_confirmed = False
            if run_id:
                try:
                    stop_path = f"/v1/runs/{quote(run_id, safe='')}/stop"
                    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                        await client.post(
                            session.instance.details["base_url"] + stop_path,
                            headers=self._headers(session.instance),
                        )
                except Exception:
                    logger.warning("controlled Hermes stop could not be confirmed")
            raise

    async def query_run(
        self, instance: RuntimeInstance, reference: RuntimeRunReference
    ) -> RuntimeRunStatus:
        path = f"/v1/runs/{quote(reference.remote_run_id, safe='')}"
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, trust_env=False) as client:
            response = await client.get(
                instance.details["base_url"] + path, headers=self._headers(instance)
            )
            response.raise_for_status()
            payload = response.json()
        if (
            not isinstance(payload, dict)
            or payload.get("run_id", payload.get("id", reference.remote_run_id))
            != reference.remote_run_id
        ):
            raise RuntimeProtocolError("run_identity_mismatch")
        status = payload.get("status")
        normalized = (
            "completed"
            if status == "completed"
            else "failed"
            if status in {"failed", "error", "cancelled", "aborted"}
            else "running"
            if status in {"started", "running", "queued"}
            else "unknown"
        )
        return RuntimeRunStatus(normalized, "hermes.status")

    async def stream_events(self, session: RuntimeSession) -> AsyncIterator[RuntimeEvent]:
        while True:
            event = await session.queue.get()
            yield event
            if event.kind in (RuntimeEventKind.completed, RuntimeEventKind.error):
                break

    async def cancel_task(self, session: RuntimeSession) -> None:
        session.status = "cancelled"
        session.cancel_confirmed = False
        background = session.background
        if background is not None and not background.done() and not background.cancelling():
            background.cancel()
        try:
            run_id = session.remote_run_id
            if not run_id or not session.remote_session_ref:
                return  # Never guess a target from a stale legacy cache.
            async with (
                asyncio.timeout(5),
                httpx.AsyncClient(timeout=5, trust_env=False) as client,
            ):
                response = await client.post(
                    f"{session.instance.details['base_url']}/v1/runs/{quote(run_id, safe='')}/stop",
                    headers=self._headers(session.instance),
                )
                response.raise_for_status()
        except Exception:
            logger.warning("hermes stop unconfirmed", extra={"task_id": session.task_id})
        finally:
            if background is not None:
                await asyncio.gather(background, return_exceptions=True)

    async def get_artifacts(self, session: RuntimeSession) -> list[ProducedArtifact]:
        return await asyncio.to_thread(workspace_artifacts.collect, session)

    async def get_runtime_info(self, instance: RuntimeInstance) -> RuntimeInfo:
        return RuntimeInfo(
            type=self.type,
            version=instance.details.get("runtime_version") or "unknown",
            details={
                "container": instance.details.get("container_name"),
                "base_url": instance.details.get("base_url"),
            },
        )

    # ---- internals ----

    @staticmethod
    def _headers(instance: RuntimeInstance) -> dict[str, str]:
        key = instance.details.get("api_key") or ""
        return {"Authorization": f"Bearer {key}"}

    async def _create_run(
        self,
        instance: RuntimeInstance,
        prompt: str,
        idempotency_key: str,
        *,
        session_id: str | None = None,
    ) -> str | None:
        base_url = instance.details.get("base_url")
        if not base_url:
            raise RuntimeError("hermes instance has no base_url (not provisioned?)")
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, trust_env=False) as client:
            response = await client.post(
                f"{base_url}/v1/runs",
                headers={**self._headers(instance), "Idempotency-Key": idempotency_key},
                json={"input": prompt, **({"session_id": session_id} if session_id else {})},
            )
            response.raise_for_status()
            payload = response.json()
        return payload.get("id") or payload.get("run_id")

    async def _pump(self, session: RuntimeSession, prompt: str, timeout: float) -> None:
        instance = session.instance
        try:
            key = session.attempt.idempotency_key if session.attempt is not None else session.id
            run_id = await self._create_run(instance, prompt, key, session_id=session.id)
            if not run_id:
                raise RuntimeError("hermes /v1/runs returned no run id")
            accept_remote_run(session, run_id, session.id)
            instance.details[f"run_id:{session.id}"] = run_id
            await session.queue.put(
                RuntimeEvent(RuntimeEventKind.status, {"detail": "run created", "run_id": run_id})
            )
            await self._stream_run(session, run_id, timeout)
        except asyncio.CancelledError:
            session.status = "cancelled"
            raise
        except Exception:
            session.status = "failed"
            logger.warning("hermes run failed", extra={"task_id": session.task_id})
            await session.queue.put(
                RuntimeEvent(RuntimeEventKind.error, {"message": "runtime_error"})
            )

    async def _stream_run(self, session: RuntimeSession, run_id: str, timeout: float) -> None:
        instance = session.instance
        url = f"{instance.details['base_url']}/v1/runs/{run_id}/events"
        async with (
            asyncio.timeout(timeout),
            httpx.AsyncClient(
                timeout=httpx.Timeout(_SSE_IDLE_TIMEOUT, connect=_HTTP_TIMEOUT)
            ) as client,
            client.stream("GET", url, headers=self._headers(instance)) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(payload, dict)
                    and payload.get("run_id", payload.get("runId", run_id)) != run_id
                ):
                    raise RuntimeProtocolError("run_identity_mismatch")
                event = self._map_event(payload)
                if event is not None and event.kind == RuntimeEventKind.completed:
                    observe_remote_terminal(session, "completed", "hermes.sse")
                    session.status = "completed"
                elif event is not None and event.kind == RuntimeEventKind.error:
                    observe_remote_terminal(session, "failed", "hermes.sse")
                    session.status = "failed"
                if event is not None:
                    await session.queue.put(event)
                if event is not None and event.kind in (
                    RuntimeEventKind.completed,
                    RuntimeEventKind.error,
                ):
                    return
        raise RuntimeProtocolError("stream_ended_without_terminal")

    @staticmethod
    def _map_event(payload: dict) -> RuntimeEvent | None:
        """Defensively map Hermes SSE payloads onto RuntimeEvent kinds."""
        if not isinstance(payload, dict):
            return None
        etype = str(payload.get("type") or payload.get("event") or "")
        status = str(payload.get("status") or "")
        text = payload.get("delta") or payload.get("text") or payload.get("content")

        if etype in ("run.completed", "completed") or (
            etype in ("", "run.status") and status == "completed"
        ):
            return RuntimeEvent(
                RuntimeEventKind.completed, {"summary": payload.get("summary") or ""}
            )
        if etype in ("run.failed", "run.error", "error") or (
            etype in ("", "run.status") and status in ("failed", "error")
        ):
            return RuntimeEvent(RuntimeEventKind.error, {"message": "remote_failed"})
        if "tool" in etype:
            return RuntimeEvent(
                RuntimeEventKind.tool_call,
                {"tool": payload.get("tool") or payload.get("name") or "unknown", "event": etype},
            )
        if etype in ("assistant.delta", "token", "message.delta", "message", "message.completed"):
            return RuntimeEvent(
                RuntimeEventKind.message,
                {
                    "role": "assistant",
                    "content": text or "",
                    "complete": etype == "message.completed",
                },
            )
        if etype in ("thinking", "reasoning"):
            return RuntimeEvent(RuntimeEventKind.thinking, {"message": text or ""})
        # unknown shapes degrade to status events (never crash the stream)
        return RuntimeEvent(RuntimeEventKind.status, {"detail": etype or "event"})
