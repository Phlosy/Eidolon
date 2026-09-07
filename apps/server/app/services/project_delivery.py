"""Formal project lifecycle engine with human review gates."""

from __future__ import annotations

import json
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.core.request_context import get_request_identity
from app.events.bus import bus
from app.models.base import utcnow
from app.models.enums import (
    BaselineType,
    ChangeRequestStatus,
    DocumentCategory,
    EmployeeRole,
    ProjectPhaseStatus,
    ProjectPhaseType,
    ProjectStatus,
    ReviewDecision,
    ReviewStatus,
    ReviewType,
    TaskKind,
    TaskStatus,
)
from app.models.project import Project
from app.models.project_delivery import ProjectPhase, ReviewMeeting
from app.repositories import drive as drive_repo
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.repositories import project_delivery as delivery_repo
from app.schemas.project import ProjectCreate, ProjectOut
from app.schemas.project_delivery import (
    BaselineOut,
    ChangeRequestCreate,
    ChangeRequestDecisionCreate,
    ChangeRequestOut,
    CoverageOut,
    DeliveryPackageOut,
    DocumentArtifactOut,
    PendingUserAction,
    ProjectLifecycleOut,
    ProjectPhaseOut,
    RequirementOut,
    ReviewDecisionCreate,
    ReviewMeetingOut,
    ReviewPackageOut,
    TraceabilityOut,
)
from app.services import auth as auth_service
from app.services import drive as drive_service
from app.services import position_compat
from app.services.document_generation import (
    DocumentSection,
    DocumentSource,
    document_generation_service,
)

PHASE_DEFINITIONS = [
    (ProjectPhaseType.initiation, "Project Initiation", False),
    (ProjectPhaseType.requirements_analysis, "Requirements Analysis", False),
    (ProjectPhaseType.requirements_review, "Requirements Review", True),
    (ProjectPhaseType.system_design, "System Design", False),
    (ProjectPhaseType.system_design_review, "System Design Review", True),
    (ProjectPhaseType.development, "Development", False),
    (ProjectPhaseType.internal_testing, "Internal Testing", False),
    (ProjectPhaseType.user_acceptance_testing, "User Acceptance Testing", False),
    (ProjectPhaseType.acceptance_review, "Acceptance Review", True),
    (ProjectPhaseType.delivery, "Delivery", False),
    (ProjectPhaseType.project_archive, "Project Archive", False),
]

REVIEW_FOR_SOURCE = {
    ProjectPhaseType.requirements_analysis.value: (
        ProjectPhaseType.requirements_review.value,
        ReviewType.requirements_review.value,
        "Requirements Review",
        BaselineType.requirements.value,
    ),
    ProjectPhaseType.system_design.value: (
        ProjectPhaseType.system_design_review.value,
        ReviewType.design_review.value,
        "System Design Review",
        BaselineType.design.value,
    ),
    ProjectPhaseType.user_acceptance_testing.value: (
        ProjectPhaseType.acceptance_review.value,
        ReviewType.acceptance_review.value,
        "Acceptance Review",
        BaselineType.acceptance.value,
    ),
}


def create_structured_project(db: Session, payload: ProjectCreate) -> Project:
    company = org_repo.get_default_company(db)
    if company is None:
        raise HTTPException(status_code=409, detail="no company seeded")
    if payload.owner_id is not None:
        owner = org_repo.get_employee(db, payload.owner_id)
        if owner is None or owner.company_id != company.id:
            raise HTTPException(status_code=422, detail="project owner does not belong to company")
    code = (payload.code or _derive_code(payload.name)).upper()
    existing = next(
        (p for p in project_repo.list_projects(db, company.id) if (p.code or "").upper() == code),
        None,
    )
    if existing:
        raise HTTPException(status_code=409, detail="project code already exists")
    project = project_repo.create_project(
        db,
        company_id=company.id,
        name=payload.name.strip(),
        description=payload.background or payload.description,
        goal="\n".join(payload.objectives) or payload.goal,
        status=ProjectStatus.in_progress.value,
        owner_id=payload.owner_id or payload.participants.project_owner_employee_id,
        source_order_text=payload.background or payload.description,
        planned_end_at=payload.deadline,
        code=code,
        priority=payload.priority,
        customer=payload.customer,
        background=payload.background,
        objectives=payload.objectives,
        technical_requirements=payload.technical_requirements,
        constraints=payload.constraints,
        deliverables=payload.deliverables,
        review_configuration=payload.review_configuration.model_dump(),
        participants=payload.participants.model_dump(),
        tutorial_accelerated=payload.tutorial_accelerated,
    )
    for index, requirement in enumerate(payload.requirements, start=1):
        delivery_repo.create_requirement(
            db,
            project_id=project.id,
            code=(requirement.code or f"REQ-{index:03d}").upper(),
            title=requirement.title,
            description=requirement.description,
            priority=requirement.priority,
            acceptance_criteria=requirement.acceptance_criteria,
            sequence=index,
        )
    now = utcnow()
    phases = []
    for order, (phase_type, name, gate) in enumerate(PHASE_DEFINITIONS):
        status = ProjectPhaseStatus.pending.value
        started_at = None
        completed_at = None
        if phase_type == ProjectPhaseType.initiation:
            status = ProjectPhaseStatus.completed.value
            started_at = completed_at = now
        elif phase_type == ProjectPhaseType.requirements_analysis:
            status = ProjectPhaseStatus.in_progress.value
            started_at = now
        phases.append(
            delivery_repo.create_phase(
                db,
                project_id=project.id,
                phase_type=phase_type.value,
                name=name,
                order=order,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                gate_required=gate,
                owner_employee_id=project.owner_id,
                metadata_json={},
            )
        )
    drive_service.ensure_project_folders(db, project)
    _generate_project_charter(db, project, phases[0])
    _sync_tutorial_project(db, project)
    db.commit()
    db.refresh(project)
    bus.publish(
        "project.lifecycle_started",
        {"id": project.id, "code": project.code, "phase": "requirements_analysis"},
        company_id=company.id,
        project_id=project.id,
    )
    return project


def get_lifecycle(db: Session, project: Project) -> ProjectLifecycleOut:
    requirements = delivery_repo.list_requirements(db, project.id)
    phases = delivery_repo.list_phases(db, project.id)
    reviews = delivery_repo.list_reviews(db, project.id)
    documents = delivery_repo.list_documents(db, project.id)
    pending = next(
        (
            PendingUserAction(
                kind="review",
                title=phase.name,
                review_id=phase.review_id,
                phase_id=phase.id,
            )
            for phase in phases
            if phase.status == ProjectPhaseStatus.waiting_review.value and phase.review_id
        ),
        None,
    )
    # 同 projects.py：测试任务派给"占着 QA 编制的人"，不是镜像里写着 qa_engineer 的人
    qa = position_compat.employee_by_legacy_role(
        db, project.company_id, EmployeeRole.qa_engineer.value
    )
    return ProjectLifecycleOut(
        project=ProjectOut.model_validate(project),
        requirements=[RequirementOut.model_validate(row) for row in requirements],
        phases=[ProjectPhaseOut.model_validate(row) for row in phases],
        reviews=[_review_out(db, row) for row in reviews],
        documents=[DocumentArtifactOut.model_validate(row) for row in documents],
        baselines=[
            BaselineOut.model_validate(row) for row in delivery_repo.list_baselines(db, project.id)
        ],
        change_requests=[
            ChangeRequestOut.model_validate(row)
            for row in delivery_repo.list_change_requests(db, project.id)
        ],
        delivery_packages=[
            DeliveryPackageOut.model_validate(row)
            for row in delivery_repo.list_delivery_packages(db, project.id)
        ],
        pending_user_action=pending,
        coverage=_coverage(requirements),
        role_coverage_warning=(
            None if qa else "当前公司缺少独立 QA 角色；基础测试将由工程师承担。"
        ),
    )


def complete_phase(db: Session, project: Project, phase: ProjectPhase) -> ProjectLifecycleOut:
    if phase.project_id != project.id:
        raise HTTPException(status_code=404, detail="phase not found")
    allowed = {
        ProjectPhaseStatus.in_progress.value,
        ProjectPhaseStatus.changes_requested.value,
    }
    if phase.status not in allowed:
        raise HTTPException(status_code=409, detail="phase is not active")
    if phase.gate_required or phase.phase_type == ProjectPhaseType.project_archive.value:
        raise HTTPException(status_code=409, detail="this phase cannot be completed directly")
    phase.status = ProjectPhaseStatus.completed.value
    phase.completed_at = utcnow()
    for task in project_repo.list_tasks(db, project.id):
        if task.phase_id == phase.id and task.status != TaskStatus.done.value:
            task.status = TaskStatus.done.value
            task.actual_start_at = task.actual_start_at or phase.started_at or utcnow()
            task.actual_end_at = phase.completed_at
    _apply_traceability(db, project.id, phase.phase_type)

    if phase.phase_type in REVIEW_FOR_SOURCE:
        _prepare_review(db, project, phase)
    elif phase.phase_type == ProjectPhaseType.delivery.value:
        _generate_delivery_package(db, project, phase)
        archive = _phase_by_type(db, project.id, ProjectPhaseType.project_archive.value)
        archive.status = ProjectPhaseStatus.completed.value
        archive.started_at = archive.completed_at = utcnow()
        project.status = ProjectStatus.completed.value
    else:
        if phase.phase_type == ProjectPhaseType.development.value:
            _generate_development_outputs(db, project, phase)
        elif phase.phase_type == ProjectPhaseType.internal_testing.value:
            _generate_test_report(db, project, phase)
        _activate_next_phase(db, project.id, phase.order)
    db.commit()
    db.refresh(project)
    bus.publish(
        "project.phase_completed",
        {"project_id": project.id, "phase": phase.phase_type},
        company_id=project.company_id,
        project_id=project.id,
    )
    return get_lifecycle(db, project)


def decide_review(
    db: Session,
    review: ReviewMeeting,
    payload: ReviewDecisionCreate,
    *,
    request: Request | None = None,
) -> ProjectLifecycleOut:
    project = project_repo.get_project(db, review.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    decision = payload.decision.value
    if review.status == ReviewStatus.completed.value:
        if review.decision == decision:
            return get_lifecycle(db, project)
        raise HTTPException(status_code=409, detail="review already has a different decision")
    if review.status != ReviewStatus.waiting_for_customer.value:
        raise HTTPException(status_code=409, detail="review is not waiting for customer")
    if payload.expected_version is not None and payload.expected_version != review.decision_version:
        raise HTTPException(status_code=409, detail="review was updated; reload before deciding")
    review.status = ReviewStatus.completed.value
    review.decision = decision
    identity = get_request_identity()
    review.acted_by_user_id = identity.user_id if identity else None
    review.comments = payload.comments
    review.action_items = [*payload.conditions, *payload.action_items]
    review.completed_at = utcnow()
    review.decision_version += 1
    gate = delivery_repo.get_phase(db, review.phase_id)
    source_phase = delivery_repo.get_phase(db, review.source_phase_id)
    if gate is None or source_phase is None:
        raise HTTPException(status_code=409, detail="review phase relationship is invalid")
    _generate_review_minutes(db, project, gate, review)

    if payload.decision in {ReviewDecision.approved, ReviewDecision.conditionally_approved}:
        baseline = _create_baseline(db, project, source_phase, review)
        gate.baseline_id = baseline.id
        gate.status = ProjectPhaseStatus.completed.value
        gate.completed_at = utcnow()
        _activate_next_phase(db, project.id, gate.order)
        if gate.phase_type == ProjectPhaseType.system_design_review.value:
            _ensure_development_tasks(db, project)
    elif payload.decision == ReviewDecision.changes_requested:
        gate.status = ProjectPhaseStatus.pending.value
        gate.review_id = None
        gate.completed_at = None
        source_phase.status = ProjectPhaseStatus.changes_requested.value
        source_phase.completed_at = None
        source_phase.started_at = utcnow()
    else:
        gate.status = ProjectPhaseStatus.blocked.value
        project.status = ProjectStatus.rejected.value
    if request is not None and identity is not None:
        auth_service.record_audit_event(
            db,
            f"review.{decision}",
            request=request,
            user_id=identity.user_id,
            company_id=identity.company_id,
            metadata={"review_id": review.id, "project_id": project.id},
        )
    db.commit()
    bus.publish(
        "review.decision",
        {"review_id": review.id, "decision": decision},
        company_id=project.company_id,
        project_id=project.id,
    )
    return get_lifecycle(db, project)


def create_change_request(
    db: Session, project: Project, payload: ChangeRequestCreate
) -> ChangeRequestOut:
    sequence = delivery_repo.next_change_sequence(db, project.id)
    impact = {
        "summary": "Impact analysis started from the active traceability matrix.",
        "requirements": payload.affected_requirements,
        "design": payload.affected_design,
        "tasks": payload.affected_tasks,
        "tests": payload.affected_tests,
        "schedule_risk": "pending assessment",
    }
    row = delivery_repo.create_change_request(
        db,
        project_id=project.id,
        code=f"CR-{sequence:03d}",
        title=payload.title,
        reason=payload.reason,
        requested_by=payload.requested_by,
        priority=payload.priority,
        status=ChangeRequestStatus.impact_analysis.value,
        affected_requirements=payload.affected_requirements,
        affected_design=payload.affected_design,
        affected_tasks=payload.affected_tasks,
        affected_tests=payload.affected_tests,
        impact_analysis=impact,
    )
    db.commit()
    db.refresh(row)
    return ChangeRequestOut.model_validate(row)


def analyze_change_request(db: Session, change_id: int) -> ChangeRequestOut:
    row = _change_request(db, change_id)
    if row.status not in {
        ChangeRequestStatus.draft.value,
        ChangeRequestStatus.impact_analysis.value,
    }:
        raise HTTPException(status_code=409, detail="change request cannot be analyzed now")
    requirements = {item.code for item in delivery_repo.list_requirements(db, row.project_id)}
    unknown = set(row.affected_requirements or []) - requirements
    row.impact_analysis = {
        **(row.impact_analysis or {}),
        "unknown_requirements": sorted(unknown),
        "requires_design_update": bool(row.affected_requirements or row.affected_design),
        "requires_regression": True,
    }
    row.status = ChangeRequestStatus.waiting_approval.value
    db.commit()
    db.refresh(row)
    return ChangeRequestOut.model_validate(row)


def decide_change_request(
    db: Session, change_id: int, payload: ChangeRequestDecisionCreate
) -> ChangeRequestOut:
    row = _change_request(db, change_id)
    if row.status != ChangeRequestStatus.waiting_approval.value:
        raise HTTPException(status_code=409, detail="change request is not waiting for approval")
    row.decision = payload.decision
    row.decided_at = utcnow()
    row.status = (
        ChangeRequestStatus.implementing.value
        if payload.decision == "approved"
        else ChangeRequestStatus.rejected.value
    )
    analysis = dict(row.impact_analysis or {})
    if payload.comments:
        analysis["decision_comments"] = payload.comments
    row.impact_analysis = analysis
    db.commit()
    db.refresh(row)
    return ChangeRequestOut.model_validate(row)


def close_change_request(db: Session, change_id: int) -> ChangeRequestOut:
    row = _change_request(db, change_id)
    if row.status not in {
        ChangeRequestStatus.implementing.value,
        ChangeRequestStatus.regression_test.value,
    }:
        raise HTTPException(status_code=409, detail="only approved changes can be closed")
    row.status = ChangeRequestStatus.closed.value
    row.closed_at = utcnow()
    db.commit()
    db.refresh(row)
    return ChangeRequestOut.model_validate(row)


def traceability(db: Session, project: Project) -> TraceabilityOut:
    return TraceabilityOut(
        requirements=[
            RequirementOut.model_validate(row)
            for row in delivery_repo.list_requirements(db, project.id)
        ]
    )


def _prepare_review(db: Session, project: Project, source_phase: ProjectPhase) -> None:
    gate_type, review_type, title, _baseline_type = REVIEW_FOR_SOURCE[source_phase.phase_type]
    gate = _phase_by_type(db, project.id, gate_type)
    source = _source_for_phase(db, project, source_phase.phase_type)
    review = delivery_repo.create_review(
        db,
        project_id=project.id,
        phase_id=gate.id,
        source_phase_id=source_phase.id,
        review_type=review_type,
        title=title,
        status=ReviewStatus.preparing.value,
        presenter_employee_id=(project.participants or {}).get("presenter_employee_id")
        or project.owner_id,
        participants=project.participants or {},
        comments="",
        action_items=[],
    )
    document_type = {
        ReviewType.requirements_review.value: "requirements_analysis_report",
        ReviewType.design_review.value: "system_design_specification",
        ReviewType.acceptance_review.value: "acceptance_test_report",
    }[review_type]
    internal = document_generation_service.persist(
        db,
        project=project,
        phase=source_phase,
        category=DocumentCategory.internal.value,
        document_type=f"{document_type}_source",
        title=f"{source.title} Source",
        format="markdown",
        content=document_generation_service.generate_markdown(source),
        author_employee_id=review.presenter_employee_id,
        review_id=review.id,
    )
    detailed = document_generation_service.persist(
        db,
        project=project,
        phase=source_phase,
        category=DocumentCategory.formal.value,
        document_type=document_type,
        title=source.title,
        format="docx",
        content=document_generation_service.generate_docx(source),
        author_employee_id=review.presenter_employee_id,
        source_document_id=internal.id,
        review_id=review.id,
    )
    presentation_source = DocumentSource(
        title=title,
        summary=source.summary,
        sections=source.sections,
    )
    presentation = document_generation_service.persist(
        db,
        project=project,
        phase=gate,
        category=DocumentCategory.review.value,
        document_type=f"{review_type}_presentation",
        title=f"{title} Presentation",
        format="pptx",
        content=document_generation_service.generate_pptx(presentation_source),
        author_employee_id=review.presenter_employee_id,
        source_document_id=detailed.id,
        review_id=review.id,
    )
    notes = document_generation_service.persist(
        db,
        project=project,
        phase=gate,
        category=DocumentCategory.review.value,
        document_type="speaker_notes",
        title=f"{title} Speaker Notes",
        format="markdown",
        content=document_generation_service.generate_speaker_notes(presentation_source),
        author_employee_id=review.presenter_employee_id,
        source_document_id=presentation.id,
        review_id=review.id,
    )
    agenda_source = DocumentSource(
        title=f"{title} Agenda",
        summary="Review the formal package, resolve open issues, and record a customer decision.",
        sections=[
            DocumentSection("Agenda", "", ["Context", "Key findings", "Open issues", "Decision"])
        ],
    )
    agenda = document_generation_service.persist(
        db,
        project=project,
        phase=gate,
        category=DocumentCategory.review.value,
        document_type="review_agenda",
        title=f"{title} Agenda",
        format="markdown",
        content=document_generation_service.generate_markdown(agenda_source),
        author_employee_id=review.presenter_employee_id,
        review_id=review.id,
    )
    trace_source = _traceability_source(db, project)
    trace = document_generation_service.persist(
        db,
        project=project,
        phase=gate,
        category=DocumentCategory.review.value,
        document_type="traceability_matrix",
        title="Traceability Matrix",
        format="markdown",
        content=document_generation_service.generate_markdown(trace_source),
        author_employee_id=review.presenter_employee_id,
        review_id=review.id,
    )
    ids = [detailed.id, presentation.id, notes.id, agenda.id, trace.id]
    delivery_repo.create_review_package(
        db,
        review_id=review.id,
        title=f"{title} Package",
        document_artifact_ids=ids,
        detailed_document_id=detailed.id,
        presentation_id=presentation.id,
        speaker_notes_id=notes.id,
        agenda_id=agenda.id,
        traceability_document_id=trace.id,
        version=delivery_repo.next_review_package_version(db, project.id, review_type),
        content_hash=document_generation_service.content_hash(ids),
    )
    review.status = ReviewStatus.waiting_for_customer.value
    gate.status = ProjectPhaseStatus.waiting_review.value
    gate.started_at = utcnow()
    gate.review_id = review.id
    project.status = ProjectStatus.in_review.value


def _create_baseline(
    db: Session, project: Project, source_phase: ProjectPhase, review: ReviewMeeting
):
    _gate, _review_type, _title, baseline_type = REVIEW_FOR_SOURCE[source_phase.phase_type]
    package = delivery_repo.get_review_package(db, review.id)
    if package is None or package.detailed_document_id is None:
        raise HTTPException(status_code=409, detail="review package is incomplete")
    prior = [
        item
        for item in delivery_repo.list_baselines(db, project.id)
        if item.baseline_type == baseline_type and item.active
    ]
    for item in prior:
        item.active = False
    version = f"v1.{len(prior)}" if prior else "v1.0"
    documents = [delivery_repo.get_document(db, doc_id) for doc_id in package.document_artifact_ids]
    for document in documents:
        if document is not None:
            document.baseline_status = "active"
            if document.id == package.detailed_document_id:
                document.version_major = 1
                document.version_minor = len(prior)
                document.version_label = version
    baseline = delivery_repo.create_baseline(
        db,
        project_id=project.id,
        phase_id=source_phase.id,
        review_id=review.id,
        baseline_type=baseline_type,
        name=f"{baseline_type.title()} Baseline {version}",
        version=version,
        document_artifact_ids=package.document_artifact_ids,
        supersedes_baseline_id=prior[-1].id if prior else None,
        active=True,
    )
    source_phase.baseline_id = baseline.id
    project.status = ProjectStatus.in_progress.value
    return baseline


def _activate_next_phase(db: Session, project_id: int, order: int) -> None:
    phases = delivery_repo.list_phases(db, project_id)
    next_phase = next((phase for phase in phases if phase.order == order + 1), None)
    if next_phase is None:
        return
    next_phase.status = ProjectPhaseStatus.in_progress.value
    next_phase.started_at = utcnow()


def _generate_project_charter(db: Session, project: Project, phase: ProjectPhase) -> None:
    source = _project_charter_source(db, project)
    internal = document_generation_service.persist(
        db,
        project=project,
        phase=phase,
        category=DocumentCategory.internal.value,
        document_type="project_charter_source",
        title="Project Charter Source",
        format="markdown",
        content=document_generation_service.generate_markdown(source),
        author_employee_id=project.owner_id,
    )
    document_generation_service.persist(
        db,
        project=project,
        phase=phase,
        category=DocumentCategory.formal.value,
        document_type="project_charter",
        title="Project Charter",
        format="docx",
        content=document_generation_service.generate_docx(source),
        author_employee_id=project.owner_id,
        source_document_id=internal.id,
    )


def _generate_review_minutes(
    db: Session, project: Project, gate: ProjectPhase, review: ReviewMeeting
) -> None:
    source = DocumentSource(
        title=f"{review.title} Minutes",
        summary=f"Formal review decision: {review.decision}.",
        sections=[
            DocumentSection("Meeting", f"Presenter employee #{review.presenter_employee_id}", []),
            DocumentSection("Decision", review.comments, [str(x) for x in review.action_items]),
            DocumentSection("Sign-off", "Decision recorded in Eidolon project history.", []),
        ],
    )
    document_generation_service.persist(
        db,
        project=project,
        phase=gate,
        category=DocumentCategory.formal.value,
        document_type="review_minutes",
        title=f"{review.title} Minutes",
        format="docx",
        content=document_generation_service.generate_docx(source),
        author_employee_id=review.presenter_employee_id,
        review_id=review.id,
    )


def _generate_development_outputs(db: Session, project: Project, phase: ProjectPhase) -> None:
    source = DocumentSource(
        title=f"{project.name} Source Manifest",
        summary="Development output aligned to approved requirements and design baselines.",
        sections=[DocumentSection("Modules", "", ["Game engine", "Input", "UI", "Tests"])],
    )
    for category, doc_type, title in (
        (DocumentCategory.source.value, "source_manifest", "Source Code Manifest"),
        (DocumentCategory.build.value, "build_manifest", "Production Build Manifest"),
    ):
        document_generation_service.persist(
            db,
            project=project,
            phase=phase,
            category=category,
            document_type=doc_type,
            title=title,
            format="markdown",
            content=document_generation_service.generate_markdown(source),
            author_employee_id=phase.owner_employee_id,
        )
    if project.tutorial_accelerated:
        # The guided project must be independently runnable even when no external
        # agent runtime is configured. Ordinary projects retain their real runtime-
        # produced source/build artifacts instead of receiving this tutorial kit.
        document_generation_service.persist(
            db,
            project=project,
            phase=phase,
            category=DocumentCategory.source.value,
            document_type="source_code",
            title=f"{project.name} Source Code",
            format="zip",
            content=_source_code_archive(project),
            author_employee_id=phase.owner_employee_id,
        )
        document_generation_service.persist(
            db,
            project=project,
            phase=phase,
            category=DocumentCategory.build.value,
            document_type="production_build",
            title=f"{project.name} Production Build",
            format="zip",
            content=_production_build_archive(project),
            author_employee_id=phase.owner_employee_id,
        )


def _generate_test_report(db: Session, project: Project, phase: ProjectPhase) -> None:
    source = _source_for_phase(db, project, ProjectPhaseType.internal_testing.value)
    document_generation_service.persist(
        db,
        project=project,
        phase=phase,
        category=DocumentCategory.test.value,
        document_type="internal_test_report",
        title="Software Test Report",
        format="docx",
        content=document_generation_service.generate_docx(source),
        author_employee_id=phase.owner_employee_id,
    )


def _generate_delivery_package(db: Session, project: Project, phase: ProjectPhase) -> None:
    checklist = DocumentSource(
        title="Project Delivery Checklist",
        summary="Exact project artifacts and history included in this release.",
        sections=[
            DocumentSection(
                "Run", "", ["Read deployment guide", "Install dependencies", "Start build"]
            ),
            DocumentSection(
                "Contents", "", project.deliverables or ["Source", "Build", "Documents"]
            ),
        ],
    )
    formal_sources = (
        (
            "user_manual",
            "User Manual",
            "How to start, play, pause, restart, and verify the product.",
        ),
        (
            "deployment_manual",
            "Deployment Manual",
            "Extract the production build and serve index.html with any static web server.",
        ),
        (
            "release_notes",
            "Release Notes",
            "Initial accepted release based on the approved requirements, design, "
            "and acceptance baselines.",
        ),
        (
            "delivery_checklist",
            "Project Delivery Checklist",
            document_generation_service.generate_markdown(checklist),
        ),
    )
    for document_type, title, summary in formal_sources:
        source = DocumentSource(
            title=title,
            summary=summary,
            sections=[
                DocumentSection("Package", "Delivery v1", project.deliverables),
                DocumentSection(
                    "Verification",
                    "Validated against active baselines and the traceability matrix.",
                    ["Source included", "Build included", "Documents included"],
                ),
            ],
        )
        document_generation_service.persist(
            db,
            project=project,
            phase=phase,
            category=DocumentCategory.delivery.value,
            document_type=document_type,
            title=title,
            format="docx",
            content=document_generation_service.generate_docx(source),
            author_employee_id=project.owner_id,
        )
    documents = delivery_repo.list_documents(db, project.id)
    groups = {
        "source": [d.id for d in documents if d.category == DocumentCategory.source.value],
        "build": [d.id for d in documents if d.category == DocumentCategory.build.value],
        "documents": [d.id for d in documents if d.category == DocumentCategory.formal.value],
        "review_materials": [
            d.id for d in documents if d.category == DocumentCategory.review.value
        ],
        "project_history": {
            "baselines": [b.id for b in delivery_repo.list_baselines(db, project.id)],
            "reviews": [r.id for r in delivery_repo.list_reviews(db, project.id)],
            "change_requests": [c.id for c in delivery_repo.list_change_requests(db, project.id)],
        },
    }
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "README.md",
            f"# {project.name} Delivery\n\n"
            "Open build/Production Build.zip for the runnable product. "
            "See manifest.json for the complete evidence map.\n",
        )
        files = []
        category_folders = {
            DocumentCategory.source.value: "source",
            DocumentCategory.build.value: "build",
            DocumentCategory.review.value: "review-materials",
            DocumentCategory.delivery.value: "documents",
            DocumentCategory.formal.value: "documents",
            DocumentCategory.test.value: "documents",
            DocumentCategory.internal.value: "internal-source",
        }
        for document in documents:
            node = drive_repo.get_node(db, document.drive_node_id)
            if node is None:
                continue
            path = drive_service.abs_path(node)
            if not path.is_file():
                continue
            folder = category_folders.get(document.category, "documents")
            display_name = node.name
            if not display_name.lower().endswith(f".{document.format}"):
                display_name = f"{display_name}.{document.format}"
            archive_path = f"{folder}/{document.id:03d}-{display_name}"
            archive.write(path, archive_path)
            files.append(
                {
                    "artifact_id": document.id,
                    "version": document.version_label,
                    "path": archive_path,
                }
            )
        groups["files"] = files
        archive.writestr("manifest.json", json.dumps(groups, ensure_ascii=False, indent=2))
    package_document = document_generation_service.persist(
        db,
        project=project,
        phase=phase,
        category=DocumentCategory.delivery.value,
        document_type="delivery_package",
        title=f"{project.name} Delivery Package",
        format="zip",
        content=output.getvalue(),
        author_employee_id=project.owner_id,
    )
    package_version = f"v1.{len(delivery_repo.list_delivery_packages(db, project.id))}"
    delivery_repo.create_delivery_package(
        db,
        project_id=project.id,
        version=package_version,
        status="ready",
        manifest=groups,
        drive_node_id=package_document.drive_node_id,
        content_hash=document_generation_service.content_hash([d.id for d in documents]),
    )


def _source_code_archive(project: Project) -> bytes:
    """Create a small but real React/Vite/TypeScript project for the guided delivery."""
    files = {
        "package.json": json.dumps(
            {
                "name": (project.code or "eidolon-project").lower(),
                "private": True,
                "version": "1.0.0",
                "type": "module",
                "scripts": {"dev": "vite", "build": "vite build"},
                "dependencies": {
                    "react": "^19.0.0",
                    "react-dom": "^19.0.0",
                },
                "devDependencies": {
                    "@types/react": "^19.0.0",
                    "@types/react-dom": "^19.0.0",
                    "typescript": "^5.9.0",
                    "vite": "^7.0.0",
                },
            },
            indent=2,
        ),
        "index.html": '<div id="root"></div><script type="module" src="/src/main.tsx"></script>\n',
        "src/main.tsx": """import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';

type Point = { x: number; y: number };
const size = 16;
function App() {
  const [snake, setSnake] = useState<Point[]>([{x: 8, y: 8}]);
  const [food, setFood] = useState<Point>({x: 3, y: 4});
  const direction = useRef<Point>({x: 1, y: 0});
  const [paused, setPaused] = useState(false);
  const [gameOver, setGameOver] = useState(false);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const dirs: Record<string, Point> = {
        ArrowUp: {x: 0, y: -1}, ArrowDown: {x: 0, y: 1},
        ArrowLeft: {x: -1, y: 0}, ArrowRight: {x: 1, y: 0}
      };
      if (dirs[event.key]) direction.current = dirs[event.key];
      if (event.key === ' ') setPaused(value => !value);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  useEffect(() => {
    if (paused || gameOver) return;
    const timer = window.setInterval(() => setSnake(current => {
      const head = current[0];
      const next = {
        x: (head.x + direction.current.x + size) % size,
        y: (head.y + direction.current.y + size) % size
      };
      if (current.some(point => point.x === next.x && point.y === next.y)) {
        setGameOver(true); return current;
      }
      const ate = next.x === food.x && next.y === food.y;
      if (ate) setFood({x:Math.floor(Math.random()*size),y:Math.floor(Math.random()*size)});
      return [next, ...current.slice(0, ate ? current.length : current.length - 1)];
    }), 140); return () => clearInterval(timer);
  }, [paused, gameOver, food]);
  return <main>
    <h1>Classic Snake</h1>
    <p>{gameOver ? 'Game over' : `Score ${snake.length - 1}`} · Arrow keys · Space to pause</p>
    <div className="board">{Array.from({length:size*size},(_,i)=>{
      const x=i%size,y=Math.floor(i/size);
      const kind=snake.some(p=>p.x===x&&p.y===y)
        ? 'snake' : food.x===x&&food.y===y ? 'food' : '';
      return <i className={kind} key={i}/>;
    })}</div>
    <button onClick={()=>location.reload()}>Restart</button>
  </main>;
}
createRoot(document.getElementById('root')!).render(<App />);
""",
        "src/styles.css": """* { box-sizing: border-box; }
body { margin: 0; background: #f4f7fb; color: #172033; font: 16px system-ui; }
main { max-width: 620px; margin: 48px auto; padding: 24px; text-align: center; }
.board { display: grid; grid-template-columns: repeat(16, 1fr); aspect-ratio: 1;
  border: 1px solid #d7deea; background: white; border-radius: 16px; overflow: hidden; }
.board i { border: 1px solid #f0f2f6; }
.snake { background: #3b82f6; }
.food { background: #fb7185; border-radius: 50%; }
button { margin-top: 18px; padding: 10px 18px; border: 0; border-radius: 10px;
  background: #2563eb; color: white; }
@media(max-width: 680px) { main { margin: 8px auto; padding: 16px; } }
""",
        "README.md": (
            f"# {project.name}\n\nRun `npm install && npm run dev`. Build with `npm run build`.\n"
        ),
        "docker-compose.yml": """services:
  web:
    image: node:22-alpine
    working_dir: /app
    volumes: ['.:/app']
    ports: ['4173:4173']
    command: sh -c 'npm install && npm run build && npx vite preview --host 0.0.0.0'
""",
    }
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return output.getvalue()


def _production_build_archive(project: Project) -> bytes:
    """Create a dependency-free accepted build that runs from a static server."""
    title = project.name.replace("<", "&lt;").replace(">", "&gt;")
    html = f"""<!doctype html>
<html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{title}</title><link rel="stylesheet" href="styles.css">
<main><h1>{title}</h1><p id="score">Score 0 · Arrow keys · Space to pause</p>
<canvas width="512" height="512"></canvas>
<button onclick="location.reload()">Restart</button></main>
<script src="app.js"></script></html>"""
    css = """body { margin: 0; background: #f4f7fb; color: #172033; font: 16px system-ui; }
main { max-width: 560px; margin: 32px auto; text-align: center; }
canvas { width: min(90vw, 512px); background: #fff; border: 1px solid #d7deea;
  border-radius: 16px; }
button { padding: 10px 18px; border: 0; border-radius: 10px;
  background: #2563eb; color: #fff; }
"""
    script = """const c=document.querySelector('canvas'),x=c.getContext('2d'),n=16,s=32;
let snake=[[8,8]],food=[3,4],d=[1,0],paused=false,over=false;
onkeydown=e=>{const m={ArrowUp:[0,-1],ArrowDown:[0,1],
  ArrowLeft:[-1,0],ArrowRight:[1,0]};if(m[e.key])d=m[e.key];
  if(e.key===' ')paused=!paused};
setInterval(()=>{if(paused||over)return;let h=snake[0];
  let q=[(h[0]+d[0]+n)%n,(h[1]+d[1]+n)%n];
  let ate=q[0]===food[0]&&q[1]===food[1];
  if(snake.some(p=>p[0]===q[0]&&p[1]===q[1])){
    over=true;document.querySelector('#score').textContent='Game over · Restart to play again';
    return;
  }
  snake=[q,...snake.slice(0,ate?snake.length:snake.length-1)];
  if(ate)food=[Math.random()*n|0,Math.random()*n|0];
  x.clearRect(0,0,512,512);x.fillStyle='#fb7185';
  x.fillRect(food[0]*s,food[1]*s,s,s);x.fillStyle='#3b82f6';
  snake.forEach(p=>x.fillRect(p[0]*s,p[1]*s,s-1,s-1));
  document.querySelector('#score').textContent=
    `Score ${snake.length-1} · Arrow keys · Space to pause`},140);"""
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("index.html", html)
        archive.writestr("styles.css", css)
        archive.writestr("app.js", script)
        archive.writestr(
            "README.md", "Run `python3 -m http.server 8080` and open http://localhost:8080.\n"
        )
    return output.getvalue()


def _ensure_development_tasks(db: Session, project: Project) -> None:
    phase = _phase_by_type(db, project.id, ProjectPhaseType.development.value)
    if any(task.phase_id == phase.id for task in project_repo.list_tasks(db, project.id)):
        return
    engineer = position_compat.employee_by_legacy_role(
        db, project.company_id, EmployeeRole.engineer.value
    )
    for sequence, title in enumerate(
        ["Game Engine", "Input Control", "Score System", "User Interface", "Automated Tests"],
        start=1,
    ):
        project_repo.create_task(
            db,
            project_id=project.id,
            phase_id=phase.id,
            title=f"DEV-{sequence:03d} {title}",
            description=f"Implement {title} against approved baselines.",
            kind=TaskKind.development.value,
            status=TaskStatus.backlog.value,
            priority=10 - sequence,
            assignee_id=engineer.id if engineer else project.owner_id,
            acceptance_criteria="Traceable to requirement, reviewed, and tested.",
            sequence=100 + sequence,
        )


def _apply_traceability(db: Session, project_id: int, phase_type: str) -> None:
    for row in delivery_repo.list_requirements(db, project_id):
        suffix = row.code.split("-")[-1]
        if phase_type == ProjectPhaseType.system_design.value:
            row.design_refs = [f"DESIGN-{suffix}"]
        elif phase_type == ProjectPhaseType.development.value:
            row.implementation_refs = [f"DEV-{suffix}"]
        elif phase_type == ProjectPhaseType.internal_testing.value:
            row.test_refs = [f"TEST-{suffix}"]
        elif phase_type == ProjectPhaseType.user_acceptance_testing.value:
            row.acceptance_refs = [f"ACCEPTANCE-{suffix}"]


def _coverage(requirements) -> CoverageOut:
    total = len(requirements)

    def percent(field: str) -> int:
        if not total:
            return 0
        return round(sum(bool(getattr(item, field)) for item in requirements) / total * 100)

    return CoverageOut(
        requirements=100 if total else 0,
        design=percent("design_refs"),
        implementation=percent("implementation_refs"),
        tests=percent("test_refs"),
        acceptance=percent("acceptance_refs"),
    )


def _review_out(db: Session, review: ReviewMeeting) -> ReviewMeetingOut:
    package = delivery_repo.get_review_package(db, review.id)
    package_out = None
    if package is not None:
        docs = [delivery_repo.get_document(db, doc_id) for doc_id in package.document_artifact_ids]
        package_out = ReviewPackageOut(
            id=package.id,
            review_id=package.review_id,
            title=package.title,
            documents=[DocumentArtifactOut.model_validate(doc) for doc in docs if doc],
            version=package.version,
            content_hash=package.content_hash,
        )
    return ReviewMeetingOut(
        **{key: value for key, value in review.__dict__.items() if not key.startswith("_")},
        package=package_out,
    )


def _phase_by_type(db: Session, project_id: int, phase_type: str) -> ProjectPhase:
    phase = next(
        (
            item
            for item in delivery_repo.list_phases(db, project_id)
            if item.phase_type == phase_type
        ),
        None,
    )
    if phase is None:
        raise HTTPException(status_code=409, detail=f"missing phase: {phase_type}")
    return phase


def _project_charter_source(db: Session, project: Project) -> DocumentSource:
    requirements = delivery_repo.list_requirements(db, project.id)
    return DocumentSource(
        title=f"{project.name} · Project Charter",
        summary=project.background or project.description,
        sections=[
            DocumentSection("Objectives", "", list(project.objectives or [])),
            DocumentSection(
                "Scope & Requirements",
                "Structured requirements form the authoritative delivery scope.",
                [f"{r.code} · {r.title}: {r.acceptance_criteria}" for r in requirements],
            ),
            DocumentSection("Technical Indicators", "", list(project.technical_requirements or [])),
            DocumentSection("Constraints", "", list(project.constraints or [])),
            DocumentSection("Deliverables", "", list(project.deliverables or [])),
            DocumentSection(
                "Review Plan",
                "Three mandatory human gates protect requirements, design and acceptance.",
                ["Requirements Review", "System Design Review", "Acceptance Review"],
            ),
            DocumentSection(
                "Acceptance Strategy", "", [r.acceptance_criteria for r in requirements]
            ),
        ],
    )


def _source_for_phase(db: Session, project: Project, phase_type: str) -> DocumentSource:
    requirements = delivery_repo.list_requirements(db, project.id)
    req_points = [f"{r.code} · {r.title} — {r.acceptance_criteria}" for r in requirements]
    if phase_type == ProjectPhaseType.requirements_analysis.value:
        return DocumentSource(
            title="Requirements Analysis Report",
            summary=project.background or project.description,
            sections=[
                DocumentSection("Project Background", project.background, []),
                DocumentSection("Objectives", "", list(project.objectives or [])),
                DocumentSection("Functional Requirements", "", req_points),
                DocumentSection(
                    "Non-functional & Technical", "", list(project.technical_requirements or [])
                ),
                DocumentSection("Constraints & Deployment", "", list(project.constraints or [])),
                DocumentSection("Acceptance", "", [r.acceptance_criteria for r in requirements]),
                DocumentSection(
                    "Risks", "", ["Scope drift", "Role coverage", "Delivery readiness"]
                ),
                DocumentSection(
                    "Traceability Matrix",
                    "Every requirement keeps stable downstream references.",
                    req_points,
                ),
                DocumentSection(
                    "Open Questions", "Items raised in review become action items.", []
                ),
            ],
        )
    if phase_type == ProjectPhaseType.system_design.value:
        return DocumentSource(
            title="System Design Specification",
            summary="Design derived from the approved requirements baseline.",
            sections=[
                DocumentSection("System Goals", "", list(project.objectives or [])),
                DocumentSection(
                    "Architecture",
                    "Browser application with modular presentation, domain and "
                    "persistence boundaries.",
                    ["UI", "Domain", "State", "Build"],
                ),
                DocumentSection(
                    "Modules & Data",
                    "",
                    [f"DESIGN-{r.code.split('-')[-1]} maps {r.code}" for r in requirements],
                ),
                DocumentSection(
                    "Interfaces & Key Flows",
                    "",
                    ["Input → state transition → render", "Build → package → deploy"],
                ),
                DocumentSection("UI / UX", "Responsive, keyboard operable and accessible.", []),
                DocumentSection(
                    "Deployment",
                    "Static offline-capable production build.",
                    list(project.constraints or []),
                ),
                DocumentSection(
                    "Security / Performance / Errors",
                    "Fail visibly and preserve user state.",
                    list(project.technical_requirements or []),
                ),
                DocumentSection(
                    "Test Strategy & Requirement Map",
                    "Every design element remains traceable.",
                    req_points,
                ),
            ],
        )
    if phase_type == ProjectPhaseType.internal_testing.value:
        return DocumentSource(
            title="Software Test Report",
            summary="Internal verification against approved requirements and design.",
            sections=[
                DocumentSection(
                    "Plan",
                    "",
                    [
                        "Functionality",
                        "Performance",
                        "Compatibility",
                        "Install",
                        "Basic Security",
                        "Regression",
                    ],
                ),
                DocumentSection(
                    "Cases & Results",
                    "All critical cases passed in the accelerated tutorial run.",
                    req_points,
                ),
                DocumentSection("Bug List", "No open blocking defects.", []),
            ],
        )
    return DocumentSource(
        title="Acceptance Test Report",
        summary="Customer-facing validation against the intake acceptance criteria.",
        sections=[
            DocumentSection("Acceptance Plan", "", [r.acceptance_criteria for r in requirements]),
            DocumentSection(
                "Cases & Results", "All acceptance cases are ready for customer review.", req_points
            ),
            DocumentSection("Deliverables Preview", "", list(project.deliverables or [])),
        ],
    )


def _traceability_source(db: Session, project: Project) -> DocumentSource:
    requirements = delivery_repo.list_requirements(db, project.id)
    return DocumentSource(
        title="Requirements Traceability Matrix",
        summary="Requirement → Design → Implementation → Test → Acceptance",
        sections=[
            DocumentSection(
                "Traceability",
                "",
                [
                    f"{r.code} → {', '.join(r.design_refs) or 'pending'} → "
                    f"{', '.join(r.implementation_refs) or 'pending'} → "
                    f"{', '.join(r.test_refs) or 'pending'} → "
                    f"{', '.join(r.acceptance_refs) or 'pending'}"
                    for r in requirements
                ],
            )
        ],
    )


def _derive_code(name: str) -> str:
    letters = "".join(part[:1] for part in name.split() if part).upper()
    return (letters or "PROJECT")[:12]


def _change_request(db: Session, change_id: int):
    row = delivery_repo.get_change_request(db, change_id)
    if row is None:
        raise HTTPException(status_code=404, detail="change request not found")
    return row


def _sync_tutorial_project(db: Session, project: Project) -> None:
    tutorial = delivery_repo.get_tutorial(db, project.company_id)
    if tutorial is None or tutorial.status != "active":
        return
    context = dict(tutorial.context or {})
    context["project_id"] = project.id
    tutorial.context = context
    completed = list(tutorial.completed_steps or [])
    if "create_project" not in completed:
        completed.append("create_project")
    tutorial.completed_steps = completed
    tutorial.current_step = "requirements_review"
