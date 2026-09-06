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

logger = get_logger(__name__)

_HTTP_TIMEOUT = 15.0
_SSE_IDLE_TIMEOUT = 120.0
_DEFAULT_RUN_TIMEOUT = 900.0


class HermesAdapter(RuntimeAdapter):
    type = RuntimeType.hermes
    implemented = True
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
            artifacts=False,  # no artifacts-download endpoint in the API
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
        if run_id:
            try:
                async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                    await client.post(
                        f"{session.instance.details['base_url']}/v1/runs/{run_id}/stop",
                        headers=self._headers(session.instance),
                    )
            except Exception:
                logger.exception("hermes stop run failed", extra={"task_id": session.task_id})
        session.status = "cancelled"

    async def get_artifacts(self, session: RuntimeSession) -> list[ProducedArtifact]:
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

    # ---- internals ----

    @staticmethod
    def _headers(instance: RuntimeInstance) -> dict[str, str]:
        key = instance.details.get("api_key") or ""
        return {"Authorization": f"Bearer {key}"}

    async def _create_run(
        self, instance: RuntimeInstance, prompt: str, idempotency_key: str
    ) -> str | None:
        base_url = instance.details.get("base_url")
        if not base_url:
            raise RuntimeError("hermes instance has no base_url (not provisioned?)")
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.post(
                f"{base_url}/v1/runs",
                headers={**self._headers(instance), "Idempotency-Key": idempotency_key},
                json={"input": prompt},
            )
            response.raise_for_status()
            payload = response.json()
        return payload.get("id") or payload.get("run_id")

    async def _pump(self, session: RuntimeSession, prompt: str, timeout: float) -> None:
        instance = session.instance
        try:
            run_id = await self._create_run(instance, prompt, session.id)
            if not run_id:
                raise RuntimeError("hermes /v1/runs returned no run id")
            instance.details[f"run_id:{session.id}"] = run_id
            await session.queue.put(
                RuntimeEvent(RuntimeEventKind.status, {"detail": "run created", "run_id": run_id})
            )
            await self._stream_run(session, run_id, timeout)
        except asyncio.CancelledError:
            session.status = "cancelled"
            raise
        except Exception as exc:
            session.status = "failed"
            logger.exception("hermes run failed", extra={"task_id": session.task_id})
            await session.queue.put(
                RuntimeEvent(RuntimeEventKind.error, {"message": str(exc)[:500]})
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
                event = self._map_event(payload)
                if event is not None:
                    await session.queue.put(event)
                if event is not None and event.kind in (
                    RuntimeEventKind.completed,
                    RuntimeEventKind.error,
                ):
                    return

    @staticmethod
    def _map_event(payload: dict) -> RuntimeEvent | None:
        """Defensively map Hermes SSE payloads onto RuntimeEvent kinds."""
        if not isinstance(payload, dict):
            return None
        etype = str(payload.get("type") or payload.get("event") or "")
        status = str(payload.get("status") or "")
        text = payload.get("delta") or payload.get("text") or payload.get("content")

        if etype in ("run.completed", "completed") or status == "completed":
            return RuntimeEvent(
                RuntimeEventKind.completed, {"summary": payload.get("summary") or ""}
            )
        if etype in ("run.failed", "run.error", "error") or status in ("failed", "error"):
            message = payload.get("error") or payload.get("message") or "hermes run failed"
            return RuntimeEvent(RuntimeEventKind.error, {"message": str(message)[:500]})
        if "tool" in etype:
            return RuntimeEvent(
                RuntimeEventKind.tool_call,
                {"tool": payload.get("tool") or payload.get("name") or "unknown", "event": etype},
            )
        if etype in ("assistant.delta", "token", "message.delta", "message"):
            return RuntimeEvent(
                RuntimeEventKind.message, {"role": "assistant", "content": text or ""}
            )
        if etype in ("thinking", "reasoning"):
            return RuntimeEvent(RuntimeEventKind.thinking, {"message": text or ""})
        # unknown shapes degrade to status events (never crash the stream)
        return RuntimeEvent(RuntimeEventKind.status, {"detail": etype or "event"})
