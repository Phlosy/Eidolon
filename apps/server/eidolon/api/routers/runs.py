"""Runs: a fact timeline per run, and the runs of a company or person.

What leaves the server is what the facts hold: previews, usage, tool names,
outcomes. Reasoning, full context and assistant turns live only in blobs and
are never served (the P guard: no chain-of-thought over the API). Blob refs are
stripped too — they are hashes, useless to a client and an invitation to ask.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from eidolon.agent import run as R
from eidolon.agent.providers.accounting import KIND as MODEL_CALLED
from eidolon.api.commands import Commands, Conflict, DomainError, Forbidden, person_in
from eidolon.api.deps import CurrentUser, current_user, get_db, member_of, memberships
from eidolon.api.routers.manage import guard
from eidolon.identity import EmploymentRow
from eidolon.kernel.facts import Fact
from eidolon.kernel.store import FactStore
from eidolon.money import COST_RECORDED, ENTRY_POSTED
from eidolon.money.projection import run_spend_micro

router = APIRouter(tags=["runs"])
store = FactStore()

SERVED = frozenset(R.RUN_KINDS | {MODEL_CALLED, COST_RECORDED, ENTRY_POSTED})
TERMINAL_KINDS = (R.RUN_COMPLETED, R.RUN_FAILED, R.RUN_CANCELLED, R.RUN_BUDGET_EXCEEDED)
STATUS_OF = {
    R.RUN_COMPLETED: "completed",
    R.RUN_FAILED: "failed",
    R.RUN_CANCELLED: "cancelled",
    R.RUN_BUDGET_EXCEEDED: "budget_exceeded",
}
NEVER = ("provider_state", "reasoning", "assistant_state", "messages", "system")


def sanitize(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in payload.items()
        if not k.endswith("_ref") and k not in NEVER and k != "request_fingerprint"
    }


def event(f: Fact) -> dict[str, Any]:
    return {
        "seq": f.seq,
        "fact_id": str(f.id),
        "kind": f.kind,
        "at": f.created_at.isoformat(),
        "actor": f.actor.as_dict(),
        "causation_id": str(f.causation_id) if f.causation_id else None,
        "payload": sanitize(f.payload),
    }


@router.get("/runs/{run_id}")
def run(run_id: UUID, db: Session = Depends(get_db), me: CurrentUser = Depends(current_user)):
    facts = store.by_correlation(db, run_id)
    started = next((f for f in facts if f.kind == R.RUN_STARTED), None)
    if started is None:
        raise HTTPException(404, "no such run")
    if started.company_id not in {e.company_id for e in memberships(db, me)}:
        raise HTTPException(403, "you are not employed by this company")
    # Summarise from the facts themselves rather than folding RunState: the view
    # must work for partial or older runs too, and it decides nothing.
    terminal = [f for f in facts if f.kind in TERMINAL_KINDS]
    last = terminal[-1] if terminal else None
    p = started.payload
    completed = next((f for f in facts if f.kind == R.STEP_COMPLETED), None)
    steps = sum(1 for f in facts if f.kind == R.STEP_COMPLETED)
    usage = (last.payload.get("usage") if last else None) or {}
    return {
        "run_id": str(run_id),
        "company_id": str(started.company_id),
        "person_id": p.get("person_id"),
        "task_id": p.get("task_id"),
        "model": p.get("model"),
        "status": STATUS_OF.get(last.kind, "running") if last else "running",
        "steps_completed": steps,
        "usage": usage,
        "error": (last.payload.get("error") if last and last.kind == R.RUN_FAILED else None),
        "stop_reason": completed.payload.get("stop_reason") if completed else None,
        "cause_fact_id": str(started.causation_id) if started.causation_id else None,
        "started_at": started.created_at.isoformat(),
        "ended_at": last.created_at.isoformat() if last else None,
        "spend_micro": run_spend_micro(db, run_id),
        "events": [event(f) for f in facts if f.kind in SERVED],
    }


@router.get("/companies/{company_id}/runs")
def company_runs(
    company_id: UUID,
    person_id: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: EmploymentRow = Depends(member_of),
):
    """Most recent runs first, from the ``agent.run.started`` facts."""
    started = store.stream(db, kinds=[R.RUN_STARTED], company_id=company_id, limit=100_000)
    out = []
    for f in reversed(started):
        p = f.payload
        if person_id and p.get("person_id") != person_id:
            continue
        run_id = UUID(p["run_id"])
        terminal = store.by_correlation(db, run_id, kinds=TERMINAL_KINDS)
        status = STATUS_OF.get(terminal[-1].kind, "running") if terminal else "running"
        out.append(
            {
                "run_id": str(run_id),
                "person_id": p.get("person_id"),
                "task_id": p.get("task_id"),
                "model": p.get("model"),
                "status": status,
                "started_at": f.created_at.isoformat(),
                "cause_fact_id": str(f.causation_id) if f.causation_id else None,
            }
        )
        if len(out) >= limit:
            break
    return out


class CancelIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: UUID,
    body: CancelIn,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
):
    """Ask a run to stop at its next checkpoint (a human act; needs ``run.cancel``)."""
    started = store.by_correlation(db, run_id, kinds=[R.RUN_STARTED])
    if not started:
        raise HTTPException(404, "no such run")
    employment = next(
        (e for e in memberships(db, me) if e.company_id == started[0].company_id), None
    )
    if employment is None:
        raise HTTPException(403, "you are not employed by this company")
    commands = Commands(store)
    try:
        fact = commands.cancel_run(db, person_in(db, employment), run_id=run_id, reason=body.reason)
    except (Forbidden, Conflict, *DomainError) as exc:
        raise guard(exc) from exc
    return {"fact_id": str(fact.id), "run_id": str(run_id), "kind": fact.kind}
