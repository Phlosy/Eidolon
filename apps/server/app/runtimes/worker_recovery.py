"""Finite, read-only recovery of an accepted Worker run, without domain access."""

import asyncio
import math
from dataclasses import replace

import httpx
from websockets.exceptions import ConnectionClosed

from app.core.logging import get_logger
from app.runtimes.base import (
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeProtocolError,
    RuntimeRunReference,
)
from app.runtimes.openclaw.rpc import GatewayError

logger = get_logger(__name__)
WAIT_RETRY_DELAYS = (0.1, 0.5)
QUERY_TIMEOUT = 5
POLL_INTERVAL = 0.5


def worker_timeout(config):
    try:
        timeout = float(config.get("run_timeout_seconds", 900))
    except (TypeError, ValueError, OverflowError):
        raise RuntimeProtocolError("invalid_run_timeout") from None
    if not math.isfinite(timeout) or not 0 < timeout <= 900:
        raise RuntimeProtocolError("invalid_run_timeout")
    return timeout


def worker_deadline(timeout):
    return asyncio.get_running_loop().time() + worker_timeout({"run_timeout_seconds": timeout})


def retryable_read(error):
    if isinstance(
        error,
        (
            httpx.NetworkError,
            httpx.TimeoutException,
            TimeoutError,
            ConnectionError,
            ConnectionClosed,
        ),
    ):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {408, 429, 500, 502, 503, 504}
    return isinstance(error, GatewayError) and error.code in {
        "connection_closed",
        "connection_failed",
    }


async def recover_worker_wait(adapter, session, error, *, deadline):
    """Keep the same request/run and original deadline; never synthesize a reply."""
    if (
        not retryable_read(error)
        or session.attempt is None
        or deadline <= asyncio.get_running_loop().time()
    ):
        return False
    original = replace(
        session, instance=replace(session.instance, details=dict(session.instance.details))
    )
    reference = RuntimeRunReference(
        original.id, original.remote_run_id, original.remote_session_ref
    )

    def still_original():
        return (
            session.status == "running"
            and session.id == original.id
            and session.attempt is original.attempt
            and session.remote_run_id == original.remote_run_id
            and session.remote_session_ref == original.remote_session_ref
        )

    async def backoff():
        count = original.attempt.check_worker_wait(session=original, reserve_retry=True)
        await asyncio.sleep(WAIT_RETRY_DELAYS[count - 1])

    try:
        async with asyncio.timeout_at(deadline):
            if not still_original():
                return False
            await backoff()
            while still_original():
                original.attempt.check_worker_wait(session=original)
                try:
                    async with asyncio.timeout(QUERY_TIMEOUT):
                        observed = await adapter.query_run(original.instance, reference)
                except Exception as exc:
                    if not retryable_read(exc):
                        raise
                    await backoff()
                    continue
                if observed.status in {"completed", "failed"}:
                    original.attempt.terminal(observed.status, observed.source, session=original)
                    if not still_original():
                        return False
                    session.status = observed.status
                    await session.queue.put(
                        RuntimeEvent(
                            RuntimeEventKind.completed
                            if observed.status == "completed"
                            else RuntimeEventKind.error,
                            {"summary": "original run completed"}
                            if observed.status == "completed"
                            else {"message": "remote_failed"},
                        )
                    )
                    return True
                # Running and unknown remain observations, never success. They
                # share the original finite deadline; successful polls are not retries.
                await asyncio.sleep(POLL_INTERVAL)
    except asyncio.CancelledError:
        session.status = "cancelled"
        raise
    except Exception:
        logger.warning("original worker wait could not recover", extra={"task_id": session.task_id})
    return False
