"""Bounded, domain-free cancellation with host-owned durable stop observations."""

import asyncio
from dataclasses import replace

from app.core.logging import get_logger
from app.runtimes.base import RuntimeRunReference, observe_remote_terminal

logger = get_logger(__name__)
STOP_TIMEOUT_SECONDS = 5


async def cancel_run(adapter, session, send_stop):
    """An acknowledgement is not a terminal. Drain even under repeated cancellation."""
    original = replace(
        session,
        instance=replace(session.instance, details=dict(session.instance.details)),
    )
    session.status, session.cancel_confirmed = "cancelled", False

    def confirm_original():
        # A caller may have installed another attempt while transport was pending.
        if (
            session.id == original.id
            and session.attempt is original.attempt
            and session.remote_run_id == original.remote_run_id
            and session.remote_session_ref == original.remote_session_ref
        ):
            session.cancel_confirmed = True

    async def cleanup():
        background = original.background
        if background is not None and not background.done() and not background.cancelling():
            background.cancel()
        try:
            if original.attempt is not None:
                try:
                    status = original.attempt.request_stop(original.stop_reason, session=original)
                except Exception:
                    logger.warning(
                        "runtime stop intent or identity unavailable",
                        extra={"task_id": session.task_id},
                    )
                    return
                if status in {"completed", "failed"}:
                    confirm_original()
                    return
            if not original.remote_run_id or not original.remote_session_ref:
                return
            reference = RuntimeRunReference(
                original.id, original.remote_run_id, original.remote_session_ref
            )
            try:
                async with asyncio.timeout(STOP_TIMEOUT_SECONDS):
                    await send_stop(original.instance, reference)
            except Exception:
                logger.warning(
                    "runtime stop response unavailable", extra={"task_id": session.task_id}
                )
            if original.attempt is None:
                return  # Legacy unrecorded handles retain an unconfirmed stop receipt.
            # The stop response may have been lost after the remote accepted it.
            try:
                async with asyncio.timeout(STOP_TIMEOUT_SECONDS):
                    observed = await adapter.query_run(original.instance, reference)
                if observed.status in {"completed", "failed"}:
                    observe_remote_terminal(original, observed.status, observed.source)
                    confirm_original()
            except Exception:
                logger.warning(
                    "runtime stop terminal unconfirmed", extra={"task_id": session.task_id}
                )
        finally:
            if background is not None:
                await asyncio.gather(background, return_exceptions=True)

    pending = asyncio.create_task(cleanup(), name=f"runtime-stop-{session.id}")
    interrupted = False
    while not pending.done():
        try:
            await asyncio.shield(pending)
        except asyncio.CancelledError:
            interrupted = True
    pending.result()
    if interrupted:
        raise asyncio.CancelledError
