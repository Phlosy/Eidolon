"""Host-owned artifact intent and atomic acceptance of original run collections.

Adapters only transport evidence. This module binds that evidence to immutable
execution facts; it never infers an old workspace from today's runtime settings.
"""

import hashlib
import json
from dataclasses import asdict, dataclass

from sqlalchemy import select

from app.core.request_context import get_request_identity
from app.core.transaction_effects import ensure_sqlite_transaction
from app.models.drive import DriveNode, DriveRevision
from app.models.enums import ArtifactType
from app.models.project import Project, Task, WorkSession
from app.models.runtime_artifact import RuntimeArtifactCollection
from app.models.runtime_execution import RuntimeAttempt
from app.repositories import drive as drive_repo
from app.runtimes.artifacts import (
    MAX_ARTIFACT_BYTES,
    MAX_ARTIFACTS,
    MAX_COLLECTION_BYTES,
    require_completed_origin,
)
from app.runtimes.base import (
    ArtifactOrigin,
    ArtifactWorkspace,
    ProducedArtifact,
    RuntimeProtocolError,
)
from app.runtimes.workspace_artifacts import capture_workspace
from app.services import artifacts, drive
from app.services.drive_revisions import RevisionContentError
from app.work import telemetry

_TYPES = {kind.value for kind in ArtifactType}


def capture_context(work, session, runtime_row):
    if work.runtime_type == "mock":
        return None
    if runtime_row is None or runtime_row.workspace_path != session.instance.home_path:
        raise RuntimeProtocolError("artifact_workspace_changed")
    workspace = capture_workspace(runtime_row.workspace_path)
    snapshot = work.provenance_snapshot_json
    task = snapshot.get("task") if isinstance(snapshot, dict) else None
    types = task.get("produces") if isinstance(task, dict) else None
    if not isinstance(types, list) or any(
        not isinstance(item, str) or item not in _TYPES for item in types
    ):
        raise RuntimeProtocolError("artifact_requirements_unknown")
    return {
        "version": 1,
        "workspace": asdict(workspace),
        "required_types": list(dict.fromkeys(types)),
    }


def bind_context(context, session):
    if (
        not isinstance(context, dict)
        or context.keys() != {"version", "workspace", "required_types"}
        or type(context["version"]) is not int
        or context["version"] != 1
        or not isinstance(context["workspace"], dict)
        or context["workspace"].keys() != {"path", "device", "inode"}
        or not isinstance(context["required_types"], list)
        or any(
            not isinstance(item, str) or item not in _TYPES for item in context["required_types"]
        )
    ):
        raise RuntimeProtocolError("artifact_context_unknown")
    original = ArtifactWorkspace(**context["workspace"])
    if (
        not isinstance(original.path, str)
        or type(original.device) is not int
        or type(original.inode) is not int
        or original.device < 0
        or original.inode < 1
        or original.path != session.instance.home_path
        or capture_workspace(original.path) != original
    ):
        raise RuntimeProtocolError("artifact_workspace_changed")
    session.artifact_workspace = original
    session.artifact_required_types = tuple(context["required_types"])


@dataclass(frozen=True)
class _Artifact:
    type: str
    title: str
    content: str
    origin: ArtifactOrigin


def _preflight(attempt, produced):
    if not isinstance(produced, (list, tuple)) or len(produced) > MAX_ARTIFACTS:
        raise RuntimeProtocolError("artifact_collection_limit")
    items, sources, total = [], set(), 0
    for item in tuple(produced):
        if not isinstance(item, ProducedArtifact) or not isinstance(item.origin, ArtifactOrigin):
            raise RuntimeProtocolError("artifact_origin_missing")
        item = _Artifact(item.type, item.title, item.content, item.origin)
        origin = item.origin
        if (
            not isinstance(item.type, str)
            or item.type not in _TYPES
            or not isinstance(item.title, str)
            or not item.title.strip()
            or len(item.title) > 300
            or not isinstance(item.content, str)
            or "\0" in item.content
            or not isinstance(origin.source_ref, str)
            or not 1 <= len(origin.source_ref) <= 1024
            or any(ord(c) < 32 for c in origin.source_ref)
            or origin.source_ref in sources
        ):
            raise RuntimeProtocolError("artifact_collection_invalid")
        if (origin.local_session_ref, origin.remote_run_id, origin.remote_session_ref) != (
            attempt.local_session_ref,
            attempt.remote_run_id,
            attempt.remote_session_ref,
        ):
            raise RuntimeProtocolError("artifact_origin_mismatch")
        try:
            body = item.content.encode("utf-8")
        except UnicodeError:
            raise RuntimeProtocolError("artifact_format_unsupported") from None
        total += len(body)
        if len(body) > MAX_ARTIFACT_BYTES or total > MAX_COLLECTION_BYTES:
            raise RuntimeProtocolError("artifact_collection_limit")
        if (
            type(origin.size_bytes) is not int
            or origin.size_bytes != len(body)
            or origin.sha256 != hashlib.sha256(body).hexdigest()
        ):
            raise RuntimeProtocolError("artifact_integrity_mismatch")
        sources.add(origin.source_ref)
        items.append(item)
    items.sort(key=lambda item: item.origin.source_ref)
    manifest = [{"type": item.type, "title": item.title, **asdict(item.origin)} for item in items]
    digest = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return items, digest


def _saved_nodes(db, work, saved):
    nodes = []
    for entry in saved.entries_json:
        revision = db.get(DriveRevision, entry["revision_id"])
        node = db.get(DriveNode, revision.node_id) if revision is not None else None
        if (
            node is None
            or node.company_id != work.company_id
            or node.task_id != work.task_id
            or node.work_session_id != work.id
            or node.owner_person_id != work.person_id
            or revision.content_path is None
        ):
            raise RuntimeProtocolError("artifact_collection_unavailable")
        try:
            drive.read_revision_content(node, revision)
        except RevisionContentError:
            raise RuntimeProtocolError("artifact_revision_unavailable") from None
        nodes.append(node)
    return nodes


def _validate_current(db, work, attempt, session):
    db.flush()
    if session is None:
        raise RuntimeProtocolError("artifact_origin_missing")
    row = telemetry.validate_identity(db, work, session, fingerprint=attempt.instance_fingerprint)
    task = db.get(Task, work.task_id, populate_existing=True)
    project = db.get(Project, task.project_id) if task is not None else None
    latest = db.scalar(
        select(RuntimeAttempt.id)
        .where(RuntimeAttempt.work_session_id == work.id, RuntimeAttempt.kind == "worker")
        .order_by(RuntimeAttempt.id.desc())
        .limit(1)
    )
    if (
        work.status != "running"
        or task is None
        or task.status != "in_progress"
        or project is None
        or project.company_id != work.company_id
        or attempt.status != "completed"
        or attempt.stop_requested_at is not None
        or attempt.host_outcome not in {None, "completed"}
        or attempt.id != latest
        or attempt.runtime_instance_id != work.runtime_instance_id
        or attempt.local_session_ref != work.runtime_session_ref
        or getattr(session.attempt, "attempt_id", None) != attempt.id
        or session.remote_run_id != attempt.remote_run_id
        or session.remote_session_ref != attempt.remote_session_ref
        or row.workspace_path != session.instance.home_path
    ):
        raise RuntimeProtocolError("artifact_collection_not_current")
    require_completed_origin(session)
    bind_context(attempt.artifact_context_json, session)
    return project


def accept_collection(db, *, work_session_id, attempt_id, session, produced):
    """Validate everything before writes; caller commits receipt, documents and task together."""
    if type(work_session_id) is not int or type(attempt_id) is not int:
        raise RuntimeProtocolError("artifact_origin_missing")
    ensure_sqlite_transaction(db)
    # Refresh reads must not discard a cancellation already made by the caller
    # in this same transaction (SessionLocal deliberately disables autoflush).
    db.flush()
    work = db.get(WorkSession, work_session_id, populate_existing=True)
    attempt = db.get(RuntimeAttempt, attempt_id, populate_existing=True)
    identity = get_request_identity()
    if (
        work is None
        or attempt is None
        or attempt.work_session_id != work.id
        or attempt.kind != "worker"
        or work.runtime_type == "mock"
        or (identity is not None and identity.company_id != work.company_id)
    ):
        raise RuntimeProtocolError("artifact_collection_scope_unavailable")
    items, digest = _preflight(attempt, produced)
    saved = db.get(RuntimeArtifactCollection, attempt.id)
    if saved is not None:
        if saved.digest != digest:
            raise RuntimeProtocolError("artifact_collection_conflict")
        return _saved_nodes(db, work, saved)
    project = _validate_current(db, work, attempt, session)
    if not set(session.artifact_required_types).issubset({item.type for item in items}):
        raise RuntimeProtocolError("artifact_required_type_missing")
    previous = db.scalar(
        select(RuntimeArtifactCollection.attempt_id)
        .join(RuntimeAttempt)
        .where(RuntimeAttempt.work_session_id == work.id)
    )
    if previous is not None:
        raise RuntimeProtocolError("artifact_collection_conflict")
    nodes, entries = [], []
    # This savepoint also protects callers that catch an I/O exception and then
    # commit their enclosing transaction. Orphan bytes cannot become DB facts.
    with db.begin_nested():
        for item in items:
            node = artifacts.record_project_artifact(
                db,
                project,
                artifact_type=item.type,
                title=item.title,
                content=item.content,
                author_id=work.employee_id,
                work_session_id=work.id,
                task_id=work.task_id,
                preserve_revision=True,
            )
            revision = drive_repo.get_revision(db, node.id, 1)
            if (
                node.owner_person_id != work.person_id
                or revision.author_person_id != work.person_id
            ):
                raise RuntimeProtocolError("artifact_origin_mismatch")
            nodes.append(node)
            entries.append({"source_ref": item.origin.source_ref, "revision_id": revision.id})
        _validate_current(db, work, attempt, session)
        saved = RuntimeArtifactCollection(
            attempt_id=attempt.id, digest=digest, entries_json=entries
        )
        _saved_nodes(db, work, saved)
        db.add(saved)
        db.flush()
    return nodes
