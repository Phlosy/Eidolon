"""Creation-time execution facts; readers never reconstruct missing history."""

from copy import deepcopy

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.request_context import get_request_identity
from app.models.base import as_utc, utcnow
from app.models.enums import DeploymentMode, RuntimeType
from app.models.organization import Company, Department, Employee
from app.models.position import PositionDefinition, PositionSlot
from app.models.project import Milestone, Project, Task, WorkSession
from app.models.project_delivery import ProjectPhase
from app.models.runtime import RuntimeInstance
from app.repositories import persons, position, providers
from app.work.production import REAL_RUNTIME_TYPES, execution_mode


def _named(row) -> dict | None:
    return {"id": row.id, "name": row.name} if row is not None else None


def capture(db: Session, work: WorkSession) -> None:
    """Called solely by project.create_work_session before its first flush.

    The caller supplies the effective adapter and binding, not desired future
    configuration. External session IDs are allocated later and recorded once.
    """
    if work.runtime_type not in {RuntimeType.mock.value, *REAL_RUNTIME_TYPES}:
        raise ValueError("work session requires an implemented runtime type")
    employee = db.get(Employee, work.employee_id) if work.employee_id is not None else None
    task = db.get(Task, work.task_id) if work.task_id is not None else None
    project = db.get(Project, task.project_id) if task else None
    company = db.get(Company, project.company_id) if project else None
    identity = get_request_identity()
    if (
        employee is None
        or task is None
        or company is None
        or employee.company_id != company.id
        or (identity is not None and identity.company_id != company.id)
    ):
        raise ValueError("work session requires task and employee in the same company")
    person_id = persons.resolve_person_id(db, employee.id)
    person = persons.get_person(db, person_id) if person_id is not None else None
    instance = (
        db.get(RuntimeInstance, work.runtime_instance_id) if work.runtime_instance_id else None
    )
    if work.runtime_instance_id is not None and (
        instance is None
        or not persons.matches_owner(db, employee.id, instance.person_id, instance.employee_id)
        or instance.runtime_type != work.runtime_type
    ):
        raise ValueError("work session runtime does not match its executor")
    provider = (
        providers.get_provider_visible(db, work.provider_id, employee.id)
        if work.provider_id
        else None
    )
    if work.provider_id is not None and (
        provider is None or provider.company_id not in (None, company.id)
    ):
        raise ValueError("work session provider is not visible to its executor")
    if work.runtime_type == RuntimeType.mock.value and (work.provider_id is not None or work.model):
        raise ValueError("mock execution cannot claim a real model binding")

    active = position.active_assignments(db, employee.id)
    slot_ids = {a.position_slot_id for a in active if a.position_slot_id}
    slots = (
        {s.id: s for s in db.scalars(select(PositionSlot).where(PositionSlot.id.in_(slot_ids)))}
        if slot_ids
        else {}
    )
    definition_ids = {s.position_definition_id for s in slots.values()}
    definitions = (
        {
            d.id: d
            for d in db.scalars(
                select(PositionDefinition).where(PositionDefinition.id.in_(definition_ids))
            )
        }
        if definition_ids
        else {}
    )
    department_ids = {
        slots[a.position_slot_id].department_id if a.position_slot_id in slots else a.department_id
        for a in active
    } - {None}
    departments = (
        {d.id: d for d in db.scalars(select(Department).where(Department.id.in_(department_ids)))}
        if department_ids
        else {}
    )
    assignments = []
    for assignment in active:
        slot = slots.get(assignment.position_slot_id)
        definition = definitions.get(slot.position_definition_id) if slot else None
        department_id = slot.department_id if slot else assignment.department_id
        department = departments.get(department_id)
        if (slot and slot.company_id != company.id) or (
            department and department.company_id != company.id
        ):
            raise ValueError("work session assignment belongs to another company")
        if definition and definition.company_id not in (None, company.id):
            raise ValueError("work session position belongs to another company")
        assignments.append(
            {
                "id": assignment.id,
                "assignment_type": assignment.assignment_type,
                "is_primary": assignment.is_primary,
                "effective_from": as_utc(assignment.effective_from).isoformat(),
                "position_title_at_assignment": assignment.position_title_snapshot,
                "slot": {"id": slot.id, "code": slot.slot_code} if slot else None,
                "position": {
                    **_named(definition),
                    "code": definition.code,
                    "job_family": definition.job_family,
                    "level": definition.level,
                    "responsibilities": deepcopy(definition.responsibilities),
                }
                if definition
                else None,
                "department": _named(department),
            }
        )
    milestone = db.get(Milestone, task.milestone_id) if task.milestone_id else None
    phase = db.get(ProjectPhase, task.phase_id) if task.phase_id else None
    if (milestone and milestone.project_id != project.id) or (
        phase and phase.project_id != project.id
    ):
        raise ValueError("work session task scope belongs to another project")

    work.person_id = person.id if person else None
    work.company_id = company.id
    work.deployment_mode = (
        DeploymentMode.mock.value
        if work.runtime_type == RuntimeType.mock.value
        else instance.deployment_mode
        if instance
        else None
    )
    work.provenance_snapshot_json = {
        "version": 1,
        "captured_at": utcnow().isoformat(),
        "person": {**_named(person), "slug": person.slug} if person else None,
        "employee": {**_named(employee), "slug": employee.slug},
        "company": {**_named(company), "slug": company.slug},
        "assignments": assignments,
        "project": {**_named(project), "goal": project.goal, "work_mode": project.work_mode},
        "task": {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "kind": task.kind,
            "acceptance_criteria": task.acceptance_criteria,
            "produces": deepcopy(task.produces_json),
            "assignee_id": task.assignee_id,
            "milestone_id": task.milestone_id,
            "milestone": _named(milestone),
            "phase_id": task.phase_id,
            "phase": _named(phase),
        },
        "runtime": {
            "type": work.runtime_type,
            "execution_mode": execution_mode(work.runtime_type),
            "instance_id": work.runtime_instance_id,
            "deployment_mode": work.deployment_mode,
            "resource_key": instance.resource_key if instance else None,
            "version": instance.runtime_version if instance else None,
            "image_digest": instance.image_digest if instance else None,
        },
        "provider": {**_named(provider), "type": provider.provider_type} if provider else None,
        "model": work.model,
    }


def read(work: WorkSession) -> dict:
    """No joins to today's person/company/task/binding; absent history stays unknown."""
    snapshot = work.provenance_snapshot_json
    return {
        "state": "captured" if snapshot is not None else "unknown",
        "snapshot": deepcopy(snapshot),
        "runtime_session_ref": work.runtime_session_ref or None,
    }
