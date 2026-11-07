"""Missions, projects, tasks, artifacts. Read-only; state comes from the work
projection, artifact content from the blob store."""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from eidolon.agent.blobs import BlobMissing, BlobStore
from eidolon.api.deps import CurrentUser, current_user, get_db, member_of, memberships
from eidolon.identity import EmploymentRow
from eidolon.money.projection import project_spend_micro, task_spend_micro
from eidolon.work.projection import ArtifactRow, MissionRow, ProjectRow, TaskRow

router = APIRouter(tags=["work"])


def blobs(request: Request) -> BlobStore:
    cfg = request.app.state.settings
    root = Path(cfg.transcript_dir)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[3] / root
    return BlobStore(root)


def _mission(m: MissionRow) -> dict:
    return {
        "mission_id": str(m.mission_id),
        "title": m.title,
        "goal": m.goal,
        "success_criteria": m.success_criteria,
        "constraints": m.constraints,
        "budget": m.budget,
        "deadline": m.deadline,
        "status": m.status,
        "decision_id": str(m.decision_id) if m.decision_id else None,
        "created_at": m.created_at.isoformat(),
        "updated_at": m.updated_at.isoformat(),
    }


def _project(p: ProjectRow) -> dict:
    return {
        "project_id": str(p.project_id),
        "company_id": str(p.company_id),
        "mission_id": str(p.mission_id) if p.mission_id else None,
        "title": p.title,
        "budget": p.budget,
        "manager": p.manager,
        "status": p.status,
        "plan_decision_id": str(p.plan_decision_id) if p.plan_decision_id else None,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
    }


def _task(t: TaskRow) -> dict:
    return {
        "task_id": str(t.task_id),
        "project_id": str(t.project_id),
        "title": t.title,
        "description": t.description,
        "acceptance_criteria": t.acceptance_criteria,
        "kind": t.kind,
        "role": t.role,
        "status": t.status,
        "assignee": t.assignee,
        "depends_on": t.depends_on,
        "inputs": t.inputs,
        "rework_count": t.rework_count,
        "current_run_id": str(t.current_run_id) if t.current_run_id else None,
        "reviews": t.reviews,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


def _artifact(a: ArtifactRow) -> dict:
    return {
        "artifact_id": str(a.artifact_id),
        "project_id": str(a.project_id),
        "task_id": str(a.task_id),
        "run_id": str(a.run_id) if a.run_id else None,
        "path": a.path,
        "blob_ref": a.blob_ref,
        "size": a.size,
        "media_type": a.media_type,
        "uses": a.uses,
        "created_at": a.created_at.isoformat(),
    }


def _readable(db: Session, me: CurrentUser, company_id: UUID) -> None:
    if company_id not in {e.company_id for e in memberships(db, me)}:
        raise HTTPException(403, "you are not employed by this company")


@router.get("/companies/{company_id}/missions")
def missions(
    company_id: UUID, db: Session = Depends(get_db), _: EmploymentRow = Depends(member_of)
):
    rows = db.scalars(
        select(MissionRow)
        .where(MissionRow.company_id == company_id)
        .order_by(MissionRow.created_at)
    )
    return [_mission(m) for m in rows]


@router.get("/companies/{company_id}/projects")
def projects(
    company_id: UUID, db: Session = Depends(get_db), _: EmploymentRow = Depends(member_of)
):
    rows = db.scalars(
        select(ProjectRow)
        .where(ProjectRow.company_id == company_id)
        .order_by(ProjectRow.created_at)
    )
    return [_project(p) for p in rows]


@router.get("/projects/{project_id}")
def project(
    project_id: UUID, db: Session = Depends(get_db), me: CurrentUser = Depends(current_user)
):
    row = db.get(ProjectRow, project_id)
    if row is None:
        raise HTTPException(404, "no such project")
    _readable(db, me, row.company_id)
    tasks = db.scalars(
        select(TaskRow).where(TaskRow.project_id == project_id).order_by(TaskRow.created_at)
    )
    artifacts = db.scalars(
        select(ArtifactRow)
        .where(ArtifactRow.project_id == project_id)
        .order_by(ArtifactRow.created_at)
    )
    out = _project(row)
    out["tasks"] = [_task(t) for t in tasks]
    out["artifacts"] = [_artifact(a) for a in artifacts]
    out["spend_micro"] = project_spend_micro(db, project_id)
    return out


@router.get("/tasks/{task_id}")
def task(task_id: UUID, db: Session = Depends(get_db), me: CurrentUser = Depends(current_user)):
    row = db.get(TaskRow, task_id)
    if row is None:
        raise HTTPException(404, "no such task")
    _readable(db, me, row.company_id)
    out = _task(row)
    out["artifacts"] = [
        _artifact(a)
        for a in db.scalars(
            select(ArtifactRow)
            .where(ArtifactRow.task_id == task_id)
            .order_by(ArtifactRow.created_at)
        )
    ]
    out["spend_micro"] = task_spend_micro(db, task_id)
    return out


@router.get("/artifacts/{artifact_id}")
def artifact(
    artifact_id: UUID, db: Session = Depends(get_db), me: CurrentUser = Depends(current_user)
):
    row = db.get(ArtifactRow, artifact_id)
    if row is None:
        raise HTTPException(404, "no such artifact")
    _readable(db, me, row.company_id)
    return _artifact(row)


@router.get("/artifacts/{artifact_id}/content")
def artifact_content(
    artifact_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
):
    row = db.get(ArtifactRow, artifact_id)
    if row is None:
        raise HTTPException(404, "no such artifact")
    _readable(db, me, row.company_id)
    try:
        data = blobs(request).get_bytes(row.blob_ref)
    except BlobMissing as exc:
        raise HTTPException(410, "artifact content is no longer retained") from exc
    media = row.media_type or "application/octet-stream"
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": f'inline; filename="{Path(row.path).name}"'},
    )
