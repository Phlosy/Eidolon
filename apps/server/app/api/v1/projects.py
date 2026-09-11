"""/projects"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.events.bus import bus
from app.repositories import project as project_repo
from app.schemas.project import (
    ArtifactOut,
    MilestoneOut,
    MilestonePatch,
    ProjectCreate,
    ProjectDetail,
    ProjectGraph,
    ProjectOut,
    ProjectPatch,
    ProjectSpecOut,
    ProjectTimeline,
)
from app.services import artifacts as artifact_service
from app.services import projects as project_service
from app.services.schedules import InvalidScheduleError, apply_schedule_patch

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectOut]:
    return [ProjectOut.model_validate(p) for p in project_repo.list_projects(db)]


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> ProjectOut:
    """立项（M2.1 唯一入口）。

    路由由两个**显式**维度决定（不再由 `is_structured` 隐式分叉）：
      - `work_mode`：`guided`（教学/协助，含人类确认点）| `managed`（Manager Agent 自主）；
      - `planning_fixture`：`none`（生产）| `deterministic_template`（测试/教程/CI，受部署门控）。

    不传 `work_mode` 时用公司默认（冷启动 guided → 成熟 managed）；无论用哪个值，
    它都会**快照**到项目行 —— 公司默认值之后变化不会改写本项目。
    """
    return ProjectOut.model_validate(project_service.create_project(db, payload))


@router.get("/portfolio", response_model=list[ProjectTimeline])
def get_project_portfolio(db: Session = Depends(get_db)) -> list[ProjectTimeline]:
    """Company-wide live planning view with each project's execution hierarchy."""
    return project_service.get_project_portfolio(db)


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project_id: int, db: Session = Depends(get_db)) -> ProjectDetail:
    project = project_repo.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project_service.get_project_detail(db, project)


@router.patch("/{project_id}", response_model=ProjectOut)
def patch_project(
    project_id: int, payload: ProjectPatch, db: Session = Depends(get_db)
) -> ProjectOut:
    project = project_repo.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        apply_schedule_patch(project, payload.model_dump(exclude_unset=True))
    except InvalidScheduleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(project)
    bus.publish(
        "project.updated",
        {"id": project.id, "schedule_changed": True, "owner_id": project.owner_id},
        company_id=project.company_id,
        project_id=project.id,
    )
    return ProjectOut.model_validate(project)


@router.patch("/{project_id}/milestones/{milestone_id}", response_model=MilestoneOut)
def patch_milestone(
    project_id: int,
    milestone_id: int,
    payload: MilestonePatch,
    db: Session = Depends(get_db),
) -> MilestoneOut:
    project = project_repo.get_project(db, project_id)
    milestone = project_repo.get_milestone(db, milestone_id)
    if project is None or milestone is None or milestone.project_id != project_id:
        raise HTTPException(status_code=404, detail="milestone not found")
    try:
        apply_schedule_patch(milestone, payload.model_dump(exclude_unset=True))
    except InvalidScheduleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(milestone)
    bus.publish(
        "project.milestone_updated",
        {"id": project.id, "milestone_id": milestone.id, "owner_id": milestone.owner_id},
        company_id=project.company_id,
        project_id=project.id,
    )
    return MilestoneOut.model_validate(milestone)


@router.get("/{project_id}/graph", response_model=ProjectGraph)
def get_project_graph(project_id: int, db: Session = Depends(get_db)) -> ProjectGraph:
    project = project_repo.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project_service.get_project_graph(db, project)


@router.get("/{project_id}/artifacts", response_model=list[ArtifactOut])
def get_project_artifacts(project_id: int, db: Session = Depends(get_db)) -> list[ArtifactOut]:
    """Compat view (v0.3): the project's drive documents in artifact shape."""
    project = project_repo.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    nodes = artifact_service.list_artifact_nodes(db, project_id=project_id)
    return [artifact_service.artifact_out(db, n) for n in nodes]


@router.get("/{project_id}/spec", response_model=ProjectSpecOut)
def get_project_spec(project_id: int, db: Session = Depends(get_db)) -> ProjectSpecOut:
    """Canonical Executable Project 读面（M2.1，设计 §11.5）。

    回答 8 个问题：Canonical Spec / work_mode / Work Intake 责任 / 承担它的任职 /
    管理 actor / requirements+deliverables+acceptance / spec 版本 / 是否已进入执行。

    纯只读：不改项目状态，也不给建议 —— 拆解与选人仍然由 Manager Agent 回答。
    """
    project = project_repo.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return ProjectSpecOut.model_validate(project_service.get_project_spec(db, project))
