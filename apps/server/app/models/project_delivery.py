"""Formal project delivery domain (v0.5).

Content remains in Drive; these rows provide lifecycle, version, review,
baseline, change and delivery semantics over exact Drive revisions.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    ChangeRequestStatus,
    ProjectPhaseStatus,
    ReviewStatus,
    TutorialStatus,
)


class ProjectRequirement(TimestampMixin, Base):
    __tablename__ = "project_requirements"
    __table_args__ = (UniqueConstraint("project_id", "code"),)

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    code: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(30), default="should")
    acceptance_criteria: Mapped[str] = mapped_column(Text, default="")
    sequence: Mapped[int] = mapped_column(default=0)
    design_refs: Mapped[list] = mapped_column(JSON, default=list)
    implementation_refs: Mapped[list] = mapped_column(JSON, default=list)
    test_refs: Mapped[list] = mapped_column(JSON, default=list)
    acceptance_refs: Mapped[list] = mapped_column(JSON, default=list)


class ProjectPhase(TimestampMixin, Base):
    __tablename__ = "project_phases"
    __table_args__ = (UniqueConstraint("project_id", "phase_type"),)

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    phase_type: Mapped[str] = mapped_column(String(60), index=True)
    name: Mapped[str] = mapped_column(String(200))
    order: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(
        String(40), default=ProjectPhaseStatus.pending.value, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    gate_required: Mapped[bool] = mapped_column(Boolean, default=False)
    review_id: Mapped[int | None] = mapped_column(nullable=True)
    baseline_id: Mapped[int | None] = mapped_column(nullable=True)
    owner_employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class DocumentArtifact(TimestampMixin, Base):
    __tablename__ = "document_artifacts"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    phase_id: Mapped[int | None] = mapped_column(ForeignKey("project_phases.id"), nullable=True)
    review_id: Mapped[int | None] = mapped_column(nullable=True)
    change_request_id: Mapped[int | None] = mapped_column(nullable=True)
    category: Mapped[str] = mapped_column(String(30), index=True)
    document_type: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(300))
    format: Mapped[str] = mapped_column(String(30))
    version_major: Mapped[int] = mapped_column(default=0)
    version_minor: Mapped[int] = mapped_column(default=1)
    version_label: Mapped[str] = mapped_column(String(30), default="v0.1")
    drive_node_id: Mapped[int] = mapped_column(ForeignKey("drive_nodes.id"))
    drive_revision_id: Mapped[int | None] = mapped_column(
        ForeignKey("drive_revisions.id"), nullable=True
    )
    author_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True
    )
    review_status: Mapped[str] = mapped_column(String(30), default="draft")
    baseline_status: Mapped[str] = mapped_column(String(30), default="none")
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_artifacts.id"), nullable=True
    )
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class ReviewMeeting(TimestampMixin, Base):
    __tablename__ = "review_meetings"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    phase_id: Mapped[int] = mapped_column(ForeignKey("project_phases.id"), index=True)
    source_phase_id: Mapped[int] = mapped_column(ForeignKey("project_phases.id"))
    review_type: Mapped[str] = mapped_column(String(60), index=True)
    subtype: Mapped[str | None] = mapped_column(String(80), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(
        String(40), default=ReviewStatus.preparing.value, index=True
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    presenter_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True
    )
    participants: Mapped[dict] = mapped_column(JSON, default=dict)
    decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    comments: Mapped[str] = mapped_column(Text, default="")
    action_items: Mapped[list] = mapped_column(JSON, default=list)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_version: Mapped[int] = mapped_column(default=0)


class ReviewPackage(TimestampMixin, Base):
    __tablename__ = "review_packages"

    review_id: Mapped[int] = mapped_column(ForeignKey("review_meetings.id"), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    document_artifact_ids: Mapped[list] = mapped_column(JSON, default=list)
    detailed_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_artifacts.id"), nullable=True
    )
    presentation_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_artifacts.id"), nullable=True
    )
    speaker_notes_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_artifacts.id"), nullable=True
    )
    agenda_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_artifacts.id"), nullable=True
    )
    traceability_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_artifacts.id"), nullable=True
    )
    version: Mapped[int] = mapped_column(default=1)
    content_hash: Mapped[str] = mapped_column(String(64), default="")


class Baseline(TimestampMixin, Base):
    __tablename__ = "baselines"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    phase_id: Mapped[int] = mapped_column(ForeignKey("project_phases.id"))
    review_id: Mapped[int] = mapped_column(ForeignKey("review_meetings.id"))
    baseline_type: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(300))
    version: Mapped[str] = mapped_column(String(30), default="v1.0")
    document_artifact_ids: Mapped[list] = mapped_column(JSON, default=list)
    supersedes_baseline_id: Mapped[int | None] = mapped_column(
        ForeignKey("baselines.id"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ChangeRequest(TimestampMixin, Base):
    __tablename__ = "change_requests"
    __table_args__ = (UniqueConstraint("project_id", "code"),)

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str] = mapped_column(Text, default="")
    requested_by: Mapped[str] = mapped_column(String(200), default="")
    priority: Mapped[str] = mapped_column(String(30), default="medium")
    status: Mapped[str] = mapped_column(
        String(40), default=ChangeRequestStatus.draft.value, index=True
    )
    decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    affected_requirements: Mapped[list] = mapped_column(JSON, default=list)
    affected_design: Mapped[list] = mapped_column(JSON, default=list)
    affected_tasks: Mapped[list] = mapped_column(JSON, default=list)
    affected_tests: Mapped[list] = mapped_column(JSON, default=list)
    impact_analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(nullable=True)


class DeliveryPackage(TimestampMixin, Base):
    __tablename__ = "delivery_packages"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[str] = mapped_column(String(30), default="v1.0")
    status: Mapped[str] = mapped_column(String(30), default="ready")
    manifest: Mapped[dict] = mapped_column(JSON, default=dict)
    drive_node_id: Mapped[int | None] = mapped_column(ForeignKey("drive_nodes.id"), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), default="")


class TutorialProgress(TimestampMixin, Base):
    __tablename__ = "tutorial_progress"

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), unique=True)
    status: Mapped[str] = mapped_column(String(30), default=TutorialStatus.not_started.value)
    current_step: Mapped[str] = mapped_column(String(80), default="company_setup")
    completed_steps: Mapped[list] = mapped_column(JSON, default=list)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)


__all__ = [
    "Baseline",
    "ChangeRequest",
    "DeliveryPackage",
    "DocumentArtifact",
    "ProjectPhase",
    "ProjectRequirement",
    "ReviewMeeting",
    "ReviewPackage",
    "TutorialProgress",
]
