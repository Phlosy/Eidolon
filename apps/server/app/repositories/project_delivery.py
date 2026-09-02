"""Persistence helpers for the formal project delivery domain."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.request_context import get_request_identity
from app.models.project import Project
from app.models.project_delivery import (
    Baseline,
    ChangeRequest,
    DeliveryPackage,
    DocumentArtifact,
    ProjectPhase,
    ProjectRequirement,
    ReviewMeeting,
    ReviewPackage,
    TutorialProgress,
)


def _create(db: Session, model, **fields):
    row = model(**fields)
    db.add(row)
    db.flush()
    return row


def create_requirement(db: Session, **fields) -> ProjectRequirement:
    return _create(db, ProjectRequirement, **fields)


def list_requirements(db: Session, project_id: int) -> list[ProjectRequirement]:
    return list(
        db.scalars(
            select(ProjectRequirement)
            .where(ProjectRequirement.project_id == project_id)
            .order_by(ProjectRequirement.sequence)
        )
    )


def create_phase(db: Session, **fields) -> ProjectPhase:
    return _create(db, ProjectPhase, **fields)


def get_phase(db: Session, phase_id: int) -> ProjectPhase | None:
    stmt = select(ProjectPhase).where(ProjectPhase.id == phase_id)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.join(Project, ProjectPhase.project_id == Project.id).where(
            Project.company_id == identity.company_id
        )
    return db.scalar(stmt)


def list_phases(db: Session, project_id: int) -> list[ProjectPhase]:
    return list(
        db.scalars(
            select(ProjectPhase)
            .where(ProjectPhase.project_id == project_id)
            .order_by(ProjectPhase.order)
        )
    )


def create_document(db: Session, **fields) -> DocumentArtifact:
    return _create(db, DocumentArtifact, **fields)


def get_document(db: Session, document_id: int) -> DocumentArtifact | None:
    stmt = select(DocumentArtifact).where(DocumentArtifact.id == document_id)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.join(Project, DocumentArtifact.project_id == Project.id).where(
            Project.company_id == identity.company_id
        )
    return db.scalar(stmt)


def get_active_baseline_document_by_node(
    db: Session, drive_node_id: int
) -> DocumentArtifact | None:
    return db.scalar(
        select(DocumentArtifact).where(
            DocumentArtifact.drive_node_id == drive_node_id,
            DocumentArtifact.baseline_status == "active",
        )
    )


def list_documents(db: Session, project_id: int) -> list[DocumentArtifact]:
    return list(
        db.scalars(
            select(DocumentArtifact)
            .where(DocumentArtifact.project_id == project_id)
            .order_by(DocumentArtifact.id)
        )
    )


def list_documents_for_phase(db: Session, phase_id: int) -> list[DocumentArtifact]:
    return list(
        db.scalars(
            select(DocumentArtifact)
            .where(DocumentArtifact.phase_id == phase_id)
            .order_by(DocumentArtifact.id)
        )
    )


def next_document_minor(db: Session, project_id: int, document_type: str) -> int:
    value = db.scalar(
        select(func.max(DocumentArtifact.version_minor)).where(
            DocumentArtifact.project_id == project_id,
            DocumentArtifact.document_type == document_type,
        )
    )
    return int(value or 0) + 1


def create_review(db: Session, **fields) -> ReviewMeeting:
    return _create(db, ReviewMeeting, **fields)


def get_review(db: Session, review_id: int) -> ReviewMeeting | None:
    stmt = select(ReviewMeeting).where(ReviewMeeting.id == review_id)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.join(Project, ReviewMeeting.project_id == Project.id).where(
            Project.company_id == identity.company_id
        )
    return db.scalar(stmt)


def list_reviews(db: Session, project_id: int) -> list[ReviewMeeting]:
    return list(
        db.scalars(
            select(ReviewMeeting)
            .where(ReviewMeeting.project_id == project_id)
            .order_by(ReviewMeeting.id)
        )
    )


def next_review_package_version(db: Session, project_id: int, review_type: str) -> int:
    """Return the sequence number for the current review attempt.

    The review row is flushed before its package is assembled, so counting reviews
    of the same type includes the current attempt and naturally yields 1, 2, ... .
    """
    return int(
        db.scalar(
            select(func.count(ReviewMeeting.id)).where(
                ReviewMeeting.project_id == project_id,
                ReviewMeeting.review_type == review_type,
            )
        )
        or 1
    )


def create_review_package(db: Session, **fields) -> ReviewPackage:
    return _create(db, ReviewPackage, **fields)


def get_review_package(db: Session, review_id: int) -> ReviewPackage | None:
    return db.scalar(select(ReviewPackage).where(ReviewPackage.review_id == review_id))


def create_baseline(db: Session, **fields) -> Baseline:
    return _create(db, Baseline, **fields)


def list_baselines(db: Session, project_id: int) -> list[Baseline]:
    return list(
        db.scalars(select(Baseline).where(Baseline.project_id == project_id).order_by(Baseline.id))
    )


def create_change_request(db: Session, **fields) -> ChangeRequest:
    return _create(db, ChangeRequest, **fields)


def list_change_requests(db: Session, project_id: int) -> list[ChangeRequest]:
    return list(
        db.scalars(
            select(ChangeRequest)
            .where(ChangeRequest.project_id == project_id)
            .order_by(ChangeRequest.id)
        )
    )


def get_change_request(db: Session, change_request_id: int) -> ChangeRequest | None:
    stmt = select(ChangeRequest).where(ChangeRequest.id == change_request_id)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.join(Project, ChangeRequest.project_id == Project.id).where(
            Project.company_id == identity.company_id
        )
    return db.scalar(stmt)


def next_change_sequence(db: Session, project_id: int) -> int:
    return (
        int(
            db.scalar(
                select(func.count(ChangeRequest.id)).where(ChangeRequest.project_id == project_id)
            )
            or 0
        )
        + 1
    )


def create_delivery_package(db: Session, **fields) -> DeliveryPackage:
    return _create(db, DeliveryPackage, **fields)


def list_delivery_packages(db: Session, project_id: int) -> list[DeliveryPackage]:
    return list(
        db.scalars(
            select(DeliveryPackage)
            .where(DeliveryPackage.project_id == project_id)
            .order_by(DeliveryPackage.id.desc())
        )
    )


def get_tutorial(
    db: Session, user_id: int, tutorial_id: str = "company-founding"
) -> TutorialProgress | None:
    return db.scalar(
        select(TutorialProgress).where(
            TutorialProgress.user_id == user_id,
            TutorialProgress.tutorial_id == tutorial_id,
        )
    )


def create_tutorial(db: Session, **fields) -> TutorialProgress:
    return _create(db, TutorialProgress, **fields)
