"""Persistent tutorial workflow endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.project_delivery import TutorialProgressOut, TutorialTemplateOut
from app.services import tutorial as tutorial_service

router = APIRouter(prefix="/tutorial", tags=["tutorial"])


@router.get("", response_model=TutorialProgressOut)
def get_tutorial(db: Session = Depends(get_db)):
    return tutorial_service.get_progress(db)


@router.get("/definition")
def get_tutorial_definition():
    return tutorial_service.definition()


@router.get("/center")
def get_tutorial_center():
    return tutorial_service.center()


@router.post("/start", response_model=TutorialProgressOut)
def start_tutorial(db: Session = Depends(get_db)):
    return tutorial_service.start(db)


@router.post("/pause", response_model=TutorialProgressOut)
def pause_tutorial(db: Session = Depends(get_db)):
    return tutorial_service.pause(db)


@router.post("/skip", response_model=TutorialProgressOut)
def skip_tutorial(db: Session = Depends(get_db)):
    return tutorial_service.skip(db)


@router.post("/resume", response_model=TutorialProgressOut)
def resume_tutorial(db: Session = Depends(get_db)):
    return tutorial_service.resume(db)


@router.post("/steps/hire-qa/defer", response_model=TutorialProgressOut)
def defer_qa(db: Session = Depends(get_db)):
    return tutorial_service.defer_qa(db)


@router.post("/steps/{step}/skip", response_model=TutorialProgressOut)
def skip_step(step: str, db: Session = Depends(get_db)):
    return tutorial_service.skip_step(db, step)


@router.post("/steps/{step}/complete", response_model=TutorialProgressOut)
def complete_step(step: str, db: Session = Depends(get_db)):
    return tutorial_service.complete_step(db, step)


@router.get("/templates/classic-snake", response_model=TutorialTemplateOut)
def classic_snake_template():
    return tutorial_service.classic_snake_template()
