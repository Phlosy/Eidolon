"""Bounded recovery/admission of persisted management facts, without a runner.

Every mutating entry point owns a fresh short transaction. No caller-supplied bus
payload or runtime callback enters it. Budget availability is admission only; the
runner must independently enforce its per-turn and monetary limits after commit.
"""

from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.redaction import redact
from app.core.request_context import get_request_identity
from app.models.base import as_utc, utcnow
from app.models.dispatch import DispatchNotice
from app.models.event import Event
from app.models.management_discovery import ManagementDiscoveryCursor
from app.models.management_dispatch import ManagementDispatch
from app.models.organization import Company, Employee
from app.models.project import Project, Task, WorkSession
from app.models.provider import Provider
from app.models.review import ReviewRequest
from app.repositories import persons, providers, runtimes
from app.repositories import project as project_repo
from app.work import contracts, readiness
from app.work.dispatch_notices import begin_observation
from app.work.runtime_readiness import provider_state

SOURCE_EVENTS = contracts.DECISION_NEEDED_EVENTS | {"task.review_named"}
MAX_BATCH = 100
_TERMINAL_PROJECTS = {"completed", "cancelled", "rejected"}


@dataclass(frozen=True)
class DispatchView:
    id: int
    source_event_id: int
    source_type: str
    purpose: str
    company_id: int | None
    project_id: int | None
    task_id: int | None
    review_request_id: int | None
    recipient_employee_id: int | None
    work_session_id: int | None
    duplicate_of_id: int | None
    checked_at: datetime | None
    waiting_reason: str | None
    details: dict
    resolution: str | None
    resolution_reason: str | None
    # For a duplicate this is its canonical dispatch's session, with no new link.
    work_session: dict | None

    def as_dict(self) -> dict:
        value = asdict(self)
        value["checked_at"] = _time(self.checked_at)
        return value


@dataclass(frozen=True)
class ClaimResult:
    created: bool
    dispatch: DispatchView | None


def _time(value: datetime | None) -> str | None:
    return as_utc(value).isoformat() if value is not None else None


def _bound(limit: int) -> int:
    if type(limit) is not int or not 1 <= limit <= MAX_BATCH:
        raise ValueError(f"limit must be between 1 and {MAX_BATCH}")
    return limit


def _in_scope(company_id: int | None) -> bool:
    identity = get_request_identity()
    return identity is None or identity.company_id == company_id


def _scope_query(query, model):
    identity = get_request_identity()
    return query.where(model.company_id == identity.company_id) if identity else query


def _review_id(source: Event) -> int | None:
    payload = source.payload if isinstance(source.payload, dict) else {}
    value = payload.get("review_request_id")
    return value if type(value) is int and value > 0 else None


def _canonical(db: Session, source: Event) -> tuple[dict, bool]:
    """Read source columns and resolve only associations coherent with their company."""
    company = db.get(Company, source.company_id) if source.company_id else None
    project = db.get(Project, source.project_id) if source.project_id else None
    if project is not None and (company is None or project.company_id != company.id):
        project = None
    task = db.get(Task, source.task_id) if source.task_id else None
    if task is not None and (project is None or task.project_id != project.id):
        task = None
    is_review = source.type == "task.review_named"
    review_id = _review_id(source) if is_review else None
    review = db.get(ReviewRequest, review_id) if review_id else None
    if review is not None and (
        project is None
        or task is None
        or review.company_id != company.id
        or review.project_id != project.id
        or review.task_id != task.id
    ):
        review = None
    valid = (
        company is not None
        and project is not None
        and (source.task_id is None or task is not None)
        and (not source.type.startswith("task.") or task is not None)
        and (not is_review or review is not None)
    )
    return {
        "source_type": source.type,
        "purpose": "review" if is_review else "management",
        "company_id": company.id if company else None,
        "project_id": project.id if project else None,
        "task_id": task.id if task else None,
        "review_request_id": review.id if review else None,
    }, valid


def _observe(db: Session, source: Event) -> ManagementDispatch:
    row = db.scalar(
        select(ManagementDispatch).where(ManagementDispatch.source_event_id == source.id)
    )
    if row is None:
        target, valid = _canonical(db, source)
        row = ManagementDispatch(
            source_event_id=source.id, **target, details_json={}, checked_at=utcnow()
        )
        if not valid:
            row.resolution = "invalid"
            row.resolution_reason = "source_scope_invalid"
        db.add(row)
        db.flush()
    return row


def discover_sources(*, limit: int = 100, session_factory=SessionLocal) -> list[int]:
    """Inspect at most limit source IDs and atomically persist progress/observations.

    Backfill also advances through sources already observed by direct admission.
    Each type uses an ordered index seek; no idle anti-join scans retained history.
    """
    _bound(limit)
    with session_factory() as db:
        begin_observation(db)
        identity = get_request_identity()
        scope = identity.company_id if identity else 0
        cursor = db.get(ManagementDiscoveryCursor, scope)
        if cursor is None:
            cursor = ManagementDiscoveryCursor(scope_company_id=scope, last_event_id=0)
            db.add(cursor)
        candidates = []
        # IN(types) + ORDER BY id would sort every matching future source before
        # LIMIT. Separate seeks bound both index work and the merge to 8 * limit.
        for source_type in sorted(SOURCE_EVENTS):
            query = (
                select(Event.id)
                .where(Event.type == source_type, Event.id > cursor.last_event_id)
                .order_by(Event.id)
                .limit(limit)
            )
            candidates.extend(db.scalars(_scope_query(query, Event)))
        identifiers = sorted(candidates)[:limit]
        if not identifiers:
            db.commit()
            return []
        query = (
            select(Event)
            .where(
                Event.id.in_(identifiers),
                ~select(ManagementDispatch.id)
                .where(ManagementDispatch.source_event_id == Event.id)
                .exists(),
            )
            .order_by(Event.id)
        )
        sources = list(db.scalars(query))
        for source in sources:
            _observe(db, source)
        result = [source.id for source in sources]
        cursor.last_event_id = identifiers[-1]
        db.commit()
        return result


def pending_source_ids(*, limit: int = 100, session_factory=SessionLocal) -> list[int]:
    """Oldest observation/evaluation first; new arrivals cannot jump waiting rows."""
    with session_factory() as db:
        query = (
            select(ManagementDispatch.source_event_id)
            .where(
                ManagementDispatch.work_session_id.is_(None),
                ManagementDispatch.resolution.is_(None),
            )
            .order_by(ManagementDispatch.checked_at.asc().nulls_first(), ManagementDispatch.id)
            .limit(_bound(limit))
        )
        return list(db.scalars(_scope_query(query, ManagementDispatch)))


def _resolve(row: ManagementDispatch, resolution: str, reason: str) -> None:
    row.resolution = resolution
    row.resolution_reason = reason
    row.waiting_reason = None
    row.details_json = {}


def _wait(row: ManagementDispatch, reason: str, **details) -> None:
    row.waiting_reason = reason
    # Only known bounded facts, never Event payloads, provider config or probe text.
    row.details_json = details


def _environment(db: Session, row: ManagementDispatch, actor: Employee):
    if actor.runtime_type not in {"hermes", "openclaw"}:
        _wait(row, "real_runtime_required")
        return None
    instance = runtimes.get_instance_for_employee(db, actor.id)
    binding = providers.get_primary_binding(db, actor.id)
    if instance is None or binding is None:
        _wait(
            row,
            "environment_not_ready",
            blocking_gaps=["runtime" if instance is None else "provider"],
        )
        return None
    person_id = persons.resolve_person_id(db, actor.id)
    provider = db.get(Provider, binding.provider_id)
    if (
        instance.employee_id != actor.id
        or instance.person_id != person_id
        or binding.employee_id != actor.id
        or binding.person_id != person_id
        or provider is None
        or provider.company_id != row.company_id
        or (
            provider.scope == "employee"
            and (provider.owner_employee_id != actor.id or provider.owner_person_id != person_id)
        )
        or provider.scope not in {"company", "employee"}
    ):
        _wait(row, "runtime_identity_mismatch")
        return None
    if instance.runtime_type != actor.runtime_type or instance.deployment_mode != "docker":
        _wait(row, "real_runtime_required")
        return None
    state = provider_state(db, actor, instance)
    if (
        state.reason
        or state.provider is None
        or state.provider.id != provider.id
        or state.model != binding.model
    ):
        _wait(row, "environment_not_ready", blocking_gaps=["provider"])
        return None
    try:
        report = readiness.readiness_report(db, actor)
    except (ValueError, TypeError, KeyError):
        _wait(row, "environment_not_ready")
        return None
    if report.blocking_gaps:
        _wait(row, "environment_not_ready", blocking_gaps=list(report.blocking_gaps))
        return None
    if (
        report.runtime_type != instance.runtime_type
        or report.deployment_mode != instance.deployment_mode
    ):
        _wait(row, "runtime_identity_mismatch")
        return None
    return instance, provider, binding.model


def _evaluate(
    db: Session, source: Event, row: ManagementDispatch, *, enabled: bool, budget_available: bool
):
    # A completed or running session remains the same historical execution forever.
    if row.work_session_id is not None or row.resolution is not None:
        return None
    row.checked_at = utcnow()
    row.waiting_reason = None
    row.details_json = {}
    current, valid = _canonical(db, source)
    if not valid or any(getattr(row, name) != value for name, value in current.items()):
        _resolve(row, "invalid", "source_scope_changed")
        return None
    project = db.get(Project, row.project_id)
    task = db.get(Task, row.task_id) if row.task_id else None
    review = db.get(ReviewRequest, row.review_request_id) if row.review_request_id else None
    if review:
        previous = db.scalar(
            select(ManagementDispatch).where(
                ManagementDispatch.review_request_id == review.id,
                ManagementDispatch.work_session_id.is_not(None),
            )
        )
        if previous is not None:
            row.duplicate_of_id = previous.id
            _resolve(row, "duplicate", "review_already_claimed")
            return None
    if project.status in _TERMINAL_PROJECTS:
        _resolve(row, "obsolete", "project_terminal")
        return None
    if (
        db.scalar(
            select(DispatchNotice.id)
            .where(
                DispatchNotice.event_id == source.id,
                DispatchNotice.closed_at.is_not(None),
            )
            .limit(1)
        )
        is not None
    ):
        _resolve(row, "obsolete", "notice_closed")
        return None
    if review:
        latest_id = db.scalar(
            select(ReviewRequest.id)
            .where(ReviewRequest.task_id == task.id)
            .order_by(ReviewRequest.id.desc())
            .limit(1)
        )
        if (
            review.status != "open"
            or review.verdict is not None
            or latest_id != review.id
            or task.status != "in_review"
        ):
            _resolve(row, "obsolete", "review_no_longer_applicable")
            return None
    elif task is not None and task.status in {"done", "cancelled"}:
        _resolve(row, "obsolete", "task_terminal")
        return None
    recipient_id = review.reviewer_employee_id if review else project.management_employee_id
    actor = db.get(Employee, recipient_id) if recipient_id else None
    row.recipient_employee_id = actor.id if actor and actor.company_id == row.company_id else None
    if actor is None:
        _wait(row, "recipient_missing")
        return None
    if actor.company_id != row.company_id:
        _wait(row, "recipient_company_mismatch")
        return None
    if actor.lifecycle_status != "active":
        _wait(row, "recipient_inactive")
        return None
    person_id = persons.resolve_person_id(db, actor.id)
    if person_id is None or persons.get_person(db, person_id) is None:
        _wait(row, "recipient_person_missing")
        return None
    if (
        review is None
        and project.management_person_id is not None
        and project.management_person_id != person_id
    ):
        _wait(row, "recipient_person_mismatch")
        return None
    if project.planning_fixture not in (None, "", "none"):
        _wait(row, "fixture_project")
        return None
    if settings.runtime_mode != "auto":
        _wait(row, "runtime_override")
        return None
    if (
        actor.status != "idle"
        or project_repo.get_running_session_for_employee(db, actor.id) is not None
    ):
        _wait(row, "recipient_busy")
        return None
    if enabled is not True:
        _wait(row, "disabled")
        return None
    if budget_available is not True:
        _wait(row, "budget_unavailable")
        return None
    return _environment(db, row, actor)


def _view(row: ManagementDispatch, work: WorkSession | None) -> DispatchView:
    facts = None
    if work is not None and work.company_id == row.company_id and work.project_id == row.project_id:
        facts = {
            "id": work.id,
            "status": work.status,
            "started_at": _time(work.started_at),
            "ended_at": _time(work.ended_at),
            "error": redact(work.error)[:512] if work.error else None,
        }
    return DispatchView(
        id=row.id,
        source_event_id=row.source_event_id,
        source_type=row.source_type,
        purpose=row.purpose,
        company_id=row.company_id,
        project_id=row.project_id,
        task_id=row.task_id,
        review_request_id=row.review_request_id,
        recipient_employee_id=row.recipient_employee_id,
        work_session_id=row.work_session_id,
        duplicate_of_id=row.duplicate_of_id,
        checked_at=row.checked_at,
        waiting_reason=row.waiting_reason,
        details=dict(row.details_json or {}),
        resolution=row.resolution,
        resolution_reason=row.resolution_reason,
        work_session=facts,
    )


def _process(
    source_event_id: int, *, enabled: bool, budget_available: bool, create: bool, session_factory
) -> ClaimResult:
    with session_factory() as db:
        # Must be the first SQL statement. Worker admission uses the same physical lock.
        begin_observation(db)
        source = db.get(Event, source_event_id)
        row = db.scalar(
            select(ManagementDispatch).where(ManagementDispatch.source_event_id == source_event_id)
        )
        if row is not None and not _in_scope(row.company_id):
            return ClaimResult(False, None)
        unavailable = source is None or source.type not in SOURCE_EVENTS
        if row is None:
            if unavailable or not _in_scope(source.company_id):
                return ClaimResult(False, None)
            row = _observe(db, source)
        if unavailable or source.company_id != row.company_id:
            if row.work_session_id is None and row.resolution is None:
                row.checked_at = utcnow()
                _resolve(row, "invalid", "source_unavailable")
            environment = None
        else:
            environment = _evaluate(
                db, source, row, enabled=enabled, budget_available=budget_available
            )
        created = False
        if create and environment is not None:
            instance, provider, model = environment
            try:
                # A failed provenance check rolls back both creation and linking.
                with db.begin_nested():
                    work = project_repo.create_work_session(
                        db,
                        purpose=row.purpose,
                        project_id=row.project_id,
                        review_request_id=row.review_request_id,
                        employee_id=row.recipient_employee_id,
                        runtime_type=instance.runtime_type,
                        runtime_instance_id=instance.id,
                        provider_id=provider.id,
                        model=model,
                        started_at=utcnow(),
                    )
                    row.work_session_id = work.id
                    db.flush()
                created = True
            except ValueError:
                _wait(row, "provenance_invalid")
        db.flush()
        linked = db.get(ManagementDispatch, row.duplicate_of_id) if row.duplicate_of_id else row
        work = (
            db.get(WorkSession, linked.work_session_id)
            if linked and linked.work_session_id
            else None
        )
        result = ClaimResult(created, _view(row, work))
        db.commit()
        return result


def inspect_source(
    source_event_id: int,
    *,
    enabled: bool = False,
    budget_available: bool = False,
    session_factory=SessionLocal,
) -> DispatchView | None:
    """Persist current eligibility/waiting facts without creating an execution."""
    return _process(
        source_event_id,
        enabled=enabled,
        budget_available=budget_available,
        create=False,
        session_factory=session_factory,
    ).dispatch


def claim_source(
    source_event_id: int,
    *,
    enabled: bool = False,
    budget_available: bool = False,
    session_factory=SessionLocal,
) -> ClaimResult:
    """Atomically claim one persisted source; only created=True permits a new run.

    No recovery, restart or substitution is authorized by an existing session link.
    The explicit admission booleans are not a monetary cap or reservation.
    """
    return _process(
        source_event_id,
        enabled=enabled,
        budget_available=budget_available,
        create=True,
        session_factory=session_factory,
    )


def list_dispatches(
    db: Session, *, company_id: int, project_id: int, limit: int = 100, before_id: int | None = None
) -> list[dict]:
    """Bounded authorized projection; no Event bodies or mutable runtime config."""
    _bound(limit)
    if not _in_scope(company_id):
        return []
    if (
        db.scalar(
            select(Project.id).where(Project.id == project_id, Project.company_id == company_id)
        )
        is None
    ):
        return []
    query = (
        select(ManagementDispatch)
        .where(
            ManagementDispatch.company_id == company_id,
            ManagementDispatch.project_id == project_id,
        )
        .order_by(ManagementDispatch.id.desc())
        .limit(limit)
    )
    if before_id is not None:
        query = query.where(ManagementDispatch.id < before_id)
    rows = list(db.scalars(query))
    linked = {row.id: row for row in rows}
    missing = {row.duplicate_of_id for row in rows if row.duplicate_of_id} - linked.keys()
    if missing:
        linked.update(
            (row.id, row)
            for row in db.scalars(
                select(ManagementDispatch).where(ManagementDispatch.id.in_(missing))
            )
        )
    session_ids = {row.work_session_id for row in linked.values() if row.work_session_id}
    sessions = (
        {
            work.id: work
            for work in db.scalars(select(WorkSession).where(WorkSession.id.in_(session_ids)))
        }
        if session_ids
        else {}
    )
    result = []
    for row in rows:
        canonical = linked.get(row.duplicate_of_id or row.id)
        work = sessions.get(canonical.work_session_id) if canonical else None
        result.append(_view(row, work).as_dict())
    return result
