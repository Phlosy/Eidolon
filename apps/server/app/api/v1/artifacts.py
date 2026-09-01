"""/artifacts"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.events.bus import bus
from app.repositories import project as project_repo
from app.schemas.project import ArtifactCreate, ArtifactOut
from app.services import artifacts as artifact_service

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("", response_model=list[ArtifactOut])
def list_artifacts(
    project_id: int | None = None, type: str | None = None, db: Session = Depends(get_db)
) -> list[ArtifactOut]:
    artifacts = project_repo.list_artifacts(db, project_id=project_id, type=type)
    return [ArtifactOut.model_validate(a) for a in artifacts]


@router.post("", response_model=ArtifactOut, status_code=201)
def create_artifact(payload: ArtifactCreate, db: Session = Depends(get_db)) -> ArtifactOut:
    project = project_repo.get_project(db, payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    artifact = project_repo.create_artifact(
        db,
        company_id=project.company_id,
        project_id=payload.project_id,
        task_id=payload.task_id,
        type=payload.type.value,
        title=payload.title,
        content=payload.content,
        status=payload.status.value,
        author_id=payload.author_id,
    )
    artifact_service.materialize_artifact(db, artifact)
    db.commit()
    db.refresh(artifact)
    bus.publish(
        "artifact.created",
        {"id": artifact.id, "type": artifact.type, "title": artifact.title},
        company_id=project.company_id,
        actor_employee_id=artifact.author_id,
        project_id=artifact.project_id,
        task_id=artifact.task_id,
    )
    return ArtifactOut.model_validate(artifact)


@router.get("/{artifact_id}", response_model=ArtifactOut)
def get_artifact(artifact_id: int, db: Session = Depends(get_db)) -> ArtifactOut:
    artifact = project_repo.get_artifact(db, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="artifact not found")
    return ArtifactOut.model_validate(artifact)
