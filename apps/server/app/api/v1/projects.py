"""/projects"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories import project as project_repo
from app.schemas.project import ProjectCreate, ProjectDetail, ProjectGraph, ProjectOut
from app.services import projects as project_service

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectOut]:
    return [ProjectOut.model_validate(p) for p in project_repo.list_projects(db)]


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> ProjectOut:
    return ProjectOut.model_validate(project_service.create_order(db, payload))


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: int, db: Session = Depends(get_db)) -> ProjectDetail:
    project = project_repo.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project_service.get_project_detail(db, project)


@router.get("/{project_id}/graph", response_model=ProjectGraph)
def get_project_graph(project_id: int, db: Session = Depends(get_db)) -> ProjectGraph:
    project = project_repo.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project_service.get_project_graph(db, project)
