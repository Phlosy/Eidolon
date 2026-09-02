"""Formal project delivery lifecycle and review endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories import project as project_repo
from app.repositories import project_delivery as delivery_repo
from app.schemas.project_delivery import (
    ChangeRequestCreate,
    ChangeRequestDecisionCreate,
    ChangeRequestOut,
    DeliveryPackageOut,
    ProjectLifecycleOut,
    ReviewDecisionCreate,
    ReviewMeetingOut,
    TraceabilityOut,
)
from app.services import project_delivery as delivery_service

router = APIRouter(tags=["project-delivery"])


def _project(db: Session, project_id: int):
    project = project_repo.get_project(db, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@router.get("/projects/{project_id}/lifecycle", response_model=ProjectLifecycleOut)
def get_project_lifecycle(project_id: int, db: Session = Depends(get_db)):
    return delivery_service.get_lifecycle(db, _project(db, project_id))


@router.post(
    "/projects/{project_id}/phases/{phase_id}/complete", response_model=ProjectLifecycleOut
)
def complete_project_phase(project_id: int, phase_id: int, db: Session = Depends(get_db)):
    phase = delivery_repo.get_phase(db, phase_id)
    if phase is None:
        raise HTTPException(status_code=404, detail="phase not found")
    return delivery_service.complete_phase(db, _project(db, project_id), phase)


@router.get("/projects/{project_id}/traceability", response_model=TraceabilityOut)
def get_traceability(project_id: int, db: Session = Depends(get_db)):
    return delivery_service.traceability(db, _project(db, project_id))


@router.post(
    "/projects/{project_id}/change-requests", response_model=ChangeRequestOut, status_code=201
)
def create_change_request(
    project_id: int, payload: ChangeRequestCreate, db: Session = Depends(get_db)
):
    return delivery_service.create_change_request(db, _project(db, project_id), payload)


@router.get("/projects/{project_id}/change-requests", response_model=list[ChangeRequestOut])
def list_change_requests(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    return [
        ChangeRequestOut.model_validate(row)
        for row in delivery_repo.list_change_requests(db, project_id)
    ]


@router.get("/projects/{project_id}/delivery-packages", response_model=list[DeliveryPackageOut])
def list_delivery_packages(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    return [
        DeliveryPackageOut.model_validate(row)
        for row in delivery_repo.list_delivery_packages(db, project_id)
    ]


@router.post("/change-requests/{change_id}/analyze", response_model=ChangeRequestOut)
def analyze_change_request(change_id: int, db: Session = Depends(get_db)):
    return delivery_service.analyze_change_request(db, change_id)


@router.post("/change-requests/{change_id}/decision", response_model=ChangeRequestOut)
def decide_change_request(
    change_id: int,
    payload: ChangeRequestDecisionCreate,
    db: Session = Depends(get_db),
):
    return delivery_service.decide_change_request(db, change_id, payload)


@router.post("/change-requests/{change_id}/close", response_model=ChangeRequestOut)
def close_change_request(change_id: int, db: Session = Depends(get_db)):
    return delivery_service.close_change_request(db, change_id)


@router.get("/projects/{project_id}/reviews", response_model=list[ReviewMeetingOut])
def list_reviews(project_id: int, db: Session = Depends(get_db)):
    _project(db, project_id)
    return [
        delivery_service._review_out(db, row) for row in delivery_repo.list_reviews(db, project_id)
    ]


@router.get("/reviews/{review_id}", response_model=ReviewMeetingOut)
def get_review(review_id: int, db: Session = Depends(get_db)):
    review = delivery_repo.get_review(db, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    return delivery_service._review_out(db, review)


@router.post("/reviews/{review_id}/decision", response_model=ProjectLifecycleOut)
def decide_review(
    review_id: int,
    payload: ReviewDecisionCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    review = delivery_repo.get_review(db, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="review not found")
    return delivery_service.decide_review(db, review, payload, request=request)
