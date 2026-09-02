"""API contracts for project phases, reviews, baselines and delivery."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enums import ReviewDecision
from app.schemas.organization import ORMModel
from app.schemas.project import ProjectOut


class RequirementOut(ORMModel):
    id: int
    project_id: int
    code: str
    title: str
    description: str
    priority: str
    acceptance_criteria: str
    sequence: int
    design_refs: list
    implementation_refs: list
    test_refs: list
    acceptance_refs: list
    created_at: datetime
    updated_at: datetime


class ProjectPhaseOut(ORMModel):
    id: int
    project_id: int
    phase_type: str
    name: str
    order: int
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    gate_required: bool
    review_id: int | None
    baseline_id: int | None
    owner_employee_id: int | None
    metadata_json: dict
    created_at: datetime
    updated_at: datetime


class DocumentArtifactOut(ORMModel):
    id: int
    project_id: int
    phase_id: int | None
    review_id: int | None
    change_request_id: int | None
    category: str
    document_type: str
    title: str
    format: str
    version_label: str
    drive_node_id: int
    drive_revision_id: int | None
    author_employee_id: int | None
    review_status: str
    baseline_status: str
    source_document_id: int | None
    metadata_json: dict
    created_at: datetime
    updated_at: datetime


class ReviewPackageOut(ORMModel):
    id: int
    review_id: int
    title: str
    documents: list[DocumentArtifactOut]
    version: int
    content_hash: str


class ReviewMeetingOut(ORMModel):
    id: int
    project_id: int
    phase_id: int
    source_phase_id: int
    review_type: str
    subtype: str | None
    title: str
    status: str
    scheduled_at: datetime | None
    presenter_employee_id: int | None
    participants: dict
    decision: str | None
    comments: str
    action_items: list
    completed_at: datetime | None
    decision_version: int
    package: ReviewPackageOut | None = None
    created_at: datetime
    updated_at: datetime


class BaselineOut(ORMModel):
    id: int
    project_id: int
    phase_id: int
    review_id: int
    baseline_type: str
    name: str
    version: str
    document_artifact_ids: list
    supersedes_baseline_id: int | None
    active: bool
    created_at: datetime
    updated_at: datetime


class ChangeRequestCreate(BaseModel):
    title: str
    reason: str
    requested_by: str = "Customer"
    priority: str = "medium"
    affected_requirements: list[str] = Field(default_factory=list)
    affected_design: list[str] = Field(default_factory=list)
    affected_tasks: list[str] = Field(default_factory=list)
    affected_tests: list[str] = Field(default_factory=list)


class ChangeRequestOut(ORMModel):
    id: int
    project_id: int
    code: str
    title: str
    reason: str
    requested_by: str
    priority: str
    status: str
    decision: str | None
    affected_requirements: list
    affected_design: list
    affected_tasks: list
    affected_tests: list
    impact_analysis: dict
    decided_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ChangeRequestDecisionCreate(BaseModel):
    decision: str
    comments: str = ""

    @model_validator(mode="after")
    def validate_decision(self):
        if self.decision not in {"approved", "rejected"}:
            raise ValueError("decision must be approved or rejected")
        return self


class DeliveryPackageOut(ORMModel):
    id: int
    project_id: int
    version: str
    status: str
    manifest: dict
    drive_node_id: int | None
    content_hash: str
    created_at: datetime
    updated_at: datetime


class PendingUserAction(BaseModel):
    kind: str
    title: str
    review_id: int
    phase_id: int


class CoverageOut(BaseModel):
    requirements: int = 0
    design: int = 0
    implementation: int = 0
    tests: int = 0
    acceptance: int = 0


class ProjectLifecycleOut(BaseModel):
    project: ProjectOut
    requirements: list[RequirementOut]
    phases: list[ProjectPhaseOut]
    reviews: list[ReviewMeetingOut]
    documents: list[DocumentArtifactOut]
    baselines: list[BaselineOut]
    change_requests: list[ChangeRequestOut]
    delivery_packages: list[DeliveryPackageOut]
    pending_user_action: PendingUserAction | None = None
    coverage: CoverageOut
    role_coverage_warning: str | None = None


class ReviewDecisionCreate(BaseModel):
    decision: ReviewDecision
    comments: str = ""
    conditions: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)
    expected_version: int | None = None

    @model_validator(mode="after")
    def validate_explanation(self):
        if self.decision in {ReviewDecision.changes_requested, ReviewDecision.rejected} and not (
            self.comments.strip()
        ):
            raise ValueError("comments are required for changes requested or rejected")
        if self.decision == ReviewDecision.conditionally_approved and not (
            self.conditions or self.action_items or self.comments.strip()
        ):
            raise ValueError("conditions or action items are required")
        return self


class TraceabilityOut(BaseModel):
    requirements: list[RequirementOut]


class TutorialProgressOut(ORMModel):
    id: int
    company_id: int
    status: str
    current_step: str
    completed_steps: list
    context: dict
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TutorialTemplateOut(BaseModel):
    name: str
    intake: dict
