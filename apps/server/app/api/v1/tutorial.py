"""Tutorial API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.project_delivery import TutorialProgressOut, TutorialTemplateOut
from app.services import tutorial as service
from app.tutorials import PRACTICE_TUTORIAL_ID

router = APIRouter(prefix="/tutorial", tags=["tutorial"])
practice_router = APIRouter(prefix="/practice", tags=["tutorial"])


@router.get("", response_model=TutorialProgressOut)
def get_tutorial(db: Session = Depends(get_db)) -> TutorialProgressOut:
    return service.get_progress(db)


@router.get("/definition")
def get_definition() -> dict:
    return service.definition()


@router.get("/library")
def get_library(db: Session = Depends(get_db)) -> dict:
    """教程中心数据：目录、每个教程的定义、以及"我"在其中的进度。

    进度只读不推 —— 列表页不该因为被打开就让教程前进。
    """
    return {
        "library": service.library(),
        "center": service.center(),
        "tutorials": [
            {
                "definition": row,
                "progress": service.get_progress(db, row["id"]).model_dump(),
            }
            for row in service.definitions()
        ],
    }


@router.post("/start", response_model=TutorialProgressOut)
def start_tutorial(db: Session = Depends(get_db)) -> TutorialProgressOut:
    return service.start(db)


@router.post("/pause", response_model=TutorialProgressOut)
def pause_tutorial(db: Session = Depends(get_db)) -> TutorialProgressOut:
    return service.pause(db)


@router.post("/resume", response_model=TutorialProgressOut)
def resume_tutorial(db: Session = Depends(get_db)) -> TutorialProgressOut:
    return service.resume(db)


@router.post("/skip", response_model=TutorialProgressOut)
def skip_tutorial(db: Session = Depends(get_db)) -> TutorialProgressOut:
    return service.skip(db)


@router.post("/steps/{step}/complete", response_model=TutorialProgressOut)
def complete_step(step: str, db: Session = Depends(get_db)) -> TutorialProgressOut:
    return service.complete_step(db, step)


@router.post("/steps/{step}/skip", response_model=TutorialProgressOut)
def skip_step(step: str, db: Session = Depends(get_db)) -> TutorialProgressOut:
    return service.skip_step(db, step)


@router.post("/defer-qa", response_model=TutorialProgressOut)
def defer_qa(db: Session = Depends(get_db)) -> TutorialProgressOut:
    return service.defer_qa(db)


@router.get("/templates/classic-snake", response_model=TutorialTemplateOut)
def classic_snake_template() -> TutorialTemplateOut:
    """保留既有路径：前端建项目页已经在用。"""
    return service.classic_snake_template()


# ---- First Project Practice（可整体跳过；跳过零成本） ----
@practice_router.get("")
def get_practice(db: Session = Depends(get_db)) -> dict:
    return {
        "definition": service.definition(PRACTICE_TUTORIAL_ID),
        "progress": service.get_progress(db, PRACTICE_TUTORIAL_ID).model_dump(),
    }


@practice_router.post("/start", response_model=TutorialProgressOut)
def start_practice(db: Session = Depends(get_db)) -> TutorialProgressOut:
    return service.start(db, PRACTICE_TUTORIAL_ID)


@practice_router.post("/skip", response_model=TutorialProgressOut)
def skip_practice(db: Session = Depends(get_db)) -> TutorialProgressOut:
    """跳过实战教程。

    这个端点的实现只允许写 TutorialProgress 一行状态：不建项目、不调 Agent、
    不启动 runtime、不生成文档。test_practice_skip_is_zero_cost 用行数差守住它。
    """
    return service.skip(db, PRACTICE_TUTORIAL_ID)


@practice_router.post("/resume", response_model=TutorialProgressOut)
def resume_practice(db: Session = Depends(get_db)) -> TutorialProgressOut:
    return service.start(db, PRACTICE_TUTORIAL_ID)


@practice_router.get("/preview")
def preview_practice(db: Session = Depends(get_db)) -> dict:
    return service.practice_preview(db)
