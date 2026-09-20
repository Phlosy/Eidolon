"""Canonical versions selected by real-worker acceptance and formal review.

Drive remains the only content store. A task's current real output is exactly
its latest worker's accepted collection; older collections remain historical.
Ordinary documents and mock sessions keep the established mutable read path.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.drive import DriveNode, DriveRevision
from app.models.enums import ReviewVerdict
from app.models.project import Task, WorkSession
from app.models.runtime_artifact import RuntimeArtifactCollection
from app.models.runtime_execution import RuntimeAttempt
from app.repositories import handoff as handoff_repo
from app.repositories import review as review_repo
from app.services import drive
from app.services.drive_revisions import RevisionContentError
from app.work.contracts import WorkContractError


class LineageViolation(WorkContractError):
    """A missing, stale or unverified input; never an automated quality verdict."""


def latest_worker(db: Session, task_id: int) -> WorkSession | None:
    db.flush()
    return db.scalar(
        select(WorkSession)
        .where(WorkSession.task_id == task_id, WorkSession.purpose == "worker")
        .execution_options(populate_existing=True)
        .order_by(WorkSession.id.desc())
        .limit(1)
    )


def is_real(work: WorkSession | None) -> bool:
    return work is not None and work.runtime_type != "mock"


def requires_acceptance(db: Session, node: DriveNode) -> bool:
    original = db.get(WorkSession, node.work_session_id) if node.work_session_id else None
    current = latest_worker(db, node.task_id) if node.task_id else None
    return is_real(original) or is_real(current)


def _collection(db: Session, work: WorkSession) -> RuntimeArtifactCollection | None:
    return db.scalar(
        select(RuntimeArtifactCollection)
        .join(RuntimeAttempt, RuntimeAttempt.id == RuntimeArtifactCollection.attempt_id)
        .where(RuntimeAttempt.work_session_id == work.id, RuntimeAttempt.kind == "worker")
    )


def _revisions(db: Session, work: WorkSession) -> list[DriveRevision]:
    collection = _collection(db, work)
    if work.status != "completed" or collection is None:
        raise LineageViolation("current artifact collection is unavailable")
    revisions = []
    for entry in collection.entries_json:
        revision = db.get(DriveRevision, entry["revision_id"])
        node = db.get(DriveNode, revision.node_id) if revision else None
        if (
            revision is None
            or revision.content_path is None
            or node is None
            or node.work_session_id != work.id
            or node.task_id != work.task_id
            or node.company_id != work.company_id
            or node.owner_person_id != work.person_id
        ):
            raise LineageViolation("accepted artifact revision is unavailable")
        revisions.append(revision)
    return revisions


def pinned_revision(db: Session, node: DriveNode) -> DriveRevision | None:
    work = db.get(WorkSession, node.work_session_id) if node.work_session_id else None
    if not is_real(work):
        return None
    for revision in _revisions(db, work):
        if revision.node_id == node.id:
            return revision
    raise LineageViolation("artifact is not in its original accepted collection")


def read_content(db: Session, node: DriveNode) -> str:
    revision = pinned_revision(db, node)
    if revision is None:
        return drive.read_content(node) or ""
    try:
        content = drive.read_revision_content(node, revision)
    except RevisionContentError as exc:
        raise LineageViolation("accepted artifact content is unavailable or invalid") from exc
    if content is None:
        raise LineageViolation("accepted artifact content is unavailable")
    return content


def current_nodes(db: Session, task_id: int) -> list[DriveNode]:
    work = latest_worker(db, task_id)
    if is_real(work):
        if work.status != "completed" or _collection(db, work) is None:
            return []
        return [db.get(DriveNode, r.node_id) for r in _revisions(db, work)]
    # Old/mock tasks preserve their existing behavior, without resurrecting
    # outputs from an earlier real worker after a switch back to mock.
    return [
        node
        for node in handoff_repo.list_artifacts_for_task(db, task_id)
        if not is_real(db.get(WorkSession, node.work_session_id) if node.work_session_id else None)
    ]


def _snapshot_matches(db: Session, task: Task, request, work: WorkSession) -> bool:
    if (
        request.task_id != task.id
        or request.project_id != task.project_id
        or request.company_id != work.company_id
    ):
        return False
    facts = review_repo.list_facts(db, request.id)
    session_facts = [f.payload_json for f in facts if f.kind == "session" and f.task_id == task.id]
    artifact_facts = [
        f.payload_json for f in facts if f.kind == "artifacts" and f.task_id == task.id
    ]
    if len(session_facts) != 1 or len(artifact_facts) != 1:
        return False
    if (
        session_facts[0].get("work_session_id") != work.id
        or session_facts[0].get("status") != "completed"
    ):
        return False
    expected = {(r.node_id, r.id, r.version, r.sha256) for r in _revisions(db, work)}
    items = artifact_facts[0].get("items")
    if not isinstance(items, list) or artifact_facts[0].get("count") != len(expected):
        return False
    # Compare closed identity projections. Titles are mutable display metadata.
    if any(not isinstance(i, dict) for i in items):
        return False
    return len(items) == len(expected) and all(
        {"artifact_id": node_id, "revision_id": revision_id, "version": version, "sha256": digest}
        in [
            {k: i.get(k) for k in ("artifact_id", "revision_id", "version", "sha256")}
            for i in items
        ]
        for node_id, revision_id, version, digest in expected
    )


def validate_review_artifacts(db: Session, task: Task, request) -> None:
    work = latest_worker(db, task.id)
    if not is_real(work):
        return
    if not _snapshot_matches(db, task, request, work):
        raise LineageViolation("review artifact/session facts do not match the current collection")
    for node in current_nodes(db, task.id):
        read_content(db, node)


def formally_approved(db: Session, task: Task) -> bool:
    work = latest_worker(db, task.id)
    if not is_real(work):
        return True
    if work.status != "completed" or _collection(db, work) is None:
        return False
    return any(
        request.verdict == ReviewVerdict.passed.value and _snapshot_matches(db, task, request, work)
        for request in review_repo.list_requests(db, task_id=task.id, status="decided")
    )


def require_consumable(db: Session, node: DriveNode) -> None:
    db.flush()
    producer = db.get(Task, node.task_id, populate_existing=True) if node.task_id else None
    if producer is not None and producer.status != "done":
        raise LineageViolation(
            f"task {producer.id} is {producer.status}: only finished work is consumable"
        )
    if requires_acceptance(db, node):
        if producer is None or node.id not in {n.id for n in current_nodes(db, producer.id)}:
            raise LineageViolation("artifact is not part of the current accepted output")
        if not formally_approved(db, producer):
            raise LineageViolation("artifact requires a matching formal review")
        read_content(db, node)
