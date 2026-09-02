"""/artifacts — v0.3 compat layer over drive documents.

Same response shape as v0.2, backed by drive_nodes (kind=document,
zone=projects). See docs/design-v0.3-workspace.md §2.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.events.bus import bus
from app.repositories import drive as drive_repo
from app.repositories import project as project_repo
from app.schemas.project import ArtifactCreate, ArtifactOut
from app.services import artifacts as artifact_service

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("", response_model=list[ArtifactOut])
def list_artifacts(
    project_id: int | None = None, type: str | None = None, db: Session = Depends(get_db)
) -> list[ArtifactOut]:
    nodes = artifact_service.list_artifact_nodes(db, project_id=project_id, artifact_type=type)
    return [artifact_service.artifact_out(db, n) for n in nodes]


@router.post("", response_model=ArtifactOut, status_code=201)
def create_artifact(payload: ArtifactCreate, db: Session = Depends(get_db)) -> ArtifactOut:
    project = project_repo.get_project(db, payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    node = artifact_service.record_project_artifact(
        db,
        project,
        artifact_type=payload.type.value,
        title=payload.title,
        content=payload.content,
        author_id=payload.author_id,
    )
    db.commit()
    db.refresh(node)
    bus.publish(
        "artifact.created",
        {"id": node.id, "type": node.doc_type, "title": node.name},
        company_id=project.company_id,
        actor_employee_id=node.owner_employee_id,
        project_id=node.project_id,
        task_id=payload.task_id,
    )
    return artifact_service.artifact_out(db, node)


@router.get("/{artifact_id}", response_model=ArtifactOut)
def get_artifact(artifact_id: int, db: Session = Depends(get_db)) -> ArtifactOut:
    node = drive_repo.get_node(db, artifact_id)
    if node is None or node.kind != "document" or node.zone != "projects":
        raise HTTPException(status_code=404, detail="artifact not found")
    return artifact_service.artifact_out(db, node)
