"""Original real-worker facts shared by Task Evidence collection and validation."""

from app.core.request_context import get_request_identity
from app.models.project import Project, Task, WorkSession
from app.work import artifact_versions
from app.work.production import REAL_RUNTIME_TYPES


def historical_worker(db, task: Task) -> WorkSession | None:
    """None is the legacy/mock path; unprovable real history raises ValueError."""
    project = db.get(Project, task.project_id)
    identity = get_request_identity()
    if project is None or (identity and identity.company_id != project.company_id):
        raise ValueError("task evidence source is outside company scope")
    work = artifact_versions.latest_worker(db, task.id)
    if not artifact_versions.is_real(work):
        return None
    snapshot = work.provenance_snapshot_json
    if (
        work.runtime_type not in REAL_RUNTIME_TYPES
        or work.person_id is None
        or work.company_id != project.company_id
        or work.ended_at is None
        or work.status not in {"completed", "failed", "cancelled"}
        or not isinstance(snapshot, dict)
        or snapshot.get("version") != 1
        or snapshot.get("purpose") != "worker"
    ):
        raise ValueError("task evidence history is unknown")
    expected = {
        "person": ("id", work.person_id),
        "employee": ("id", work.employee_id),
        "company": ("id", work.company_id),
        "project": ("id", task.project_id),
        "task": ("id", task.id),
        "runtime": ("type", work.runtime_type),
    }
    if (
        any(
            not isinstance(snapshot.get(section), dict) or snapshot[section].get(field) != value
            for section, (field, value) in expected.items()
        )
        or snapshot["task"].get("assignee_id") != work.employee_id
    ):
        raise ValueError("task evidence history does not match its source")
    if task.status == "done":
        try:
            approved = artifact_versions.formally_approved(db, task)
        except artifact_versions.LineageViolation as exc:
            raise ValueError("task evidence accepted output is unavailable") from exc
        if not approved:
            raise ValueError("task evidence requires matching formal approval")
    return work
