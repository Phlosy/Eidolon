"""Shared task gap facts for the HTTP and Agent read surfaces.

Requirements come from Evidence policy; capabilities belong to Person. Unknown
is never zero, and load/readiness never choose an action for the manager.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.competency import CompetencyDefinition, EmployeeCompetency
from app.models.enums import TaskStatus, WorkSessionStatus
from app.models.organization import Employee
from app.models.project import Task, WorkSession
from app.repositories import organization as org_repo
from app.repositories import persons as person_repo
from app.repositories import project as project_repo
from app.work import readiness

OPEN_TASK_STATUSES = (
    TaskStatus.todo.value,
    TaskStatus.in_progress.value,
    TaskStatus.in_review.value,
    TaskStatus.blocked.value,
)


class GapFactsError(ValueError):
    pass


def company_employees(
    db: Session, company_id: int, identifiers: list[int] | None
) -> list[Employee]:
    if identifiers is None or not identifiers:
        return org_repo.list_employees(db, company_id)
    if len(identifiers) > 200:
        raise GapFactsError("at most 200 candidate employees may be inspected")
    employees = []
    for identifier in sorted(set(identifiers)):
        employee = org_repo.get_employee(db, identifier)
        if employee is None or employee.company_id != company_id:
            raise GapFactsError("employee not found in this company")
        employees.append(employee)
    return employees


def task_requirement_expectations(db: Session, task: Task) -> list[dict]:
    """任务隐含的能力需求 —— **复用证据流水线用的同一张映射表**。

    `evidence.policy.TASK_KIND_HINTS` 是"这种任务通常考验哪些能力"的**唯一**声明处；
    这里直接用它，而不是再发明一套任务能力需求表（那会立刻产生第二套真相）。
    它给的是 `(competency_code, role)`，**没有** minimum/target ——
    所以本工具输出的是"已知/未知 + 已知加权均值"，**不是**合格判定。
    """
    from app.evidence.policy import ROLE_STRENGTH, TASK_KIND_HINTS

    hints = TASK_KIND_HINTS.get(task.kind or "", [])
    requirements: list[dict] = []
    for code, role in hints:
        definition = db.scalar(
            select(CompetencyDefinition).where(CompetencyDefinition.code == code)
        )
        if definition is None:
            continue
        requirements.append(
            {
                "competency_code": code,
                "competency_definition_id": int(definition.id),
                "role": role,
                "weight": ROLE_STRENGTH.get(role, 0.5),
            }
        )
    return requirements


def task_fit(
    db: Session, *, company_id: int, task_id: int, candidate_employee_ids: list[int] | None = None
) -> dict:
    """**事实**版任务适配查询（T6 / W11）：逐人给已知/未知与加权已知均值，**不排名**。

    刻意不做的事：
      * 不返回 rank / recommended / best / 建议 —— 排序就是推荐，推荐就是替 Manager 选人；
      * 不把"未知"当 0 分（Unknown != Bad，与 Fit 引擎同一条规则）；
      * 不合并成一个"适配百分比"当结论 —— 它只是**已知**能力覆盖的加权均值，
        缺哪几项在同一行里列得清清楚楚。
    """
    from app.evidence.policy import ROLE_STRENGTH

    task = project_repo.get_task(db, int(task_id))
    if task is None:
        raise GapFactsError("task not found")
    project = project_repo.get_project(db, int(task.project_id))
    if project is None or int(project.company_id) != company_id:
        raise GapFactsError("task not found in this company")

    requirements = task_requirement_expectations(db, task)
    employees = company_employees(db, company_id, candidate_employee_ids)
    # Person is the capability owner; a recruited person's rows may have no
    # employee mirror. The repository criterion retains the legacy fallback.
    rows = (
        db.execute(
            select(Employee.id, EmployeeCompetency)
            .join(
                EmployeeCompetency,
                person_repo.company_read_criterion(
                    company_id, EmployeeCompetency.person_id, EmployeeCompetency.employee_id
                ),
            )
            .where(
                Employee.id.in_([employee.id for employee in employees]),
                EmployeeCompetency.competency_definition_id.in_(
                    [requirement["competency_definition_id"] for requirement in requirements]
                ),
            )
        ).all()
        if requirements and employees
        else []
    )
    by_employee: dict[int, dict[int, EmployeeCompetency]] = {}
    for identifier, row in rows:
        by_employee.setdefault(identifier, {})[row.competency_definition_id] = row

    results: list[dict] = []
    for employee in sorted(employees, key=lambda item: int(item.id)):
        known = 0
        weight_sum = 0.0
        weighted_score = 0.0
        per_competency: list[dict] = []
        for requirement in sorted(requirements, key=lambda item: item["competency_code"]):
            row = by_employee.get(int(employee.id), {}).get(
                int(requirement["competency_definition_id"])
            )
            score = int(row.score) if row is not None and row.score is not None else None
            confidence = (
                float(row.confidence) if row is not None and row.confidence is not None else None
            )
            weight = float(ROLE_STRENGTH.get(requirement["role"], 0.5))
            is_known = score is not None
            if is_known:
                known += 1
                weight_sum += weight
                weighted_score += weight * float(score)
            per_competency.append(
                {
                    "competency_code": requirement["competency_code"],
                    "role": requirement["role"],
                    "known": is_known,
                    "score": score,
                    "confidence": confidence,
                }
            )
        results.append(
            {
                "employee_id": int(employee.id),
                "person_id": int(employee.person_id) if employee.person_id else None,
                "lifecycle_status": employee.lifecycle_status,
                "requirements_total": len(requirements),
                "requirements_known": known,
                "known_weighted_score": (
                    round(weighted_score / weight_sum, 2) if weight_sum > 0 else None
                ),
                "competencies": per_competency,
            }
        )

    return {
        "task": {
            "task_id": int(task.id),
            "title": task.title,
            "kind": task.kind,
            "status": task.status,
            "current_assignee_id": task.assignee_id,
        },
        "requirement_source": "evidence.policy.TASK_KIND_HINTS",
        "requirements": [
            {
                "competency_code": r["competency_code"],
                "role": r["role"],
                "weight": r["weight"],
            }
            for r in requirements
        ],
        "candidates": results,
        #: 明确声明：这是事实，不是建议（T6 / W11）
        "result_kind": "facts_only",
        "note": (
            "No ranking and no recommendation: unknown competencies are reported as unknown, "
            "never as zero. Choosing an assignee is the manager agent's decision."
        ),
    }


def current_load(db: Session, *, company_id: int, employee_ids: list[int] | None = None) -> dict:
    employees = company_employees(db, company_id, employee_ids)
    ids = [int(employee.id) for employee in employees]

    task_rows = db.execute(
        select(Task.assignee_id, func.count(Task.id))
        .where(Task.assignee_id.in_(ids), Task.status.in_(OPEN_TASK_STATUSES))
        .group_by(Task.assignee_id)
    ).all()
    open_tasks = {int(assignee): int(count) for assignee, count in task_rows if assignee}

    session_rows = db.execute(
        select(WorkSession.employee_id, func.count(WorkSession.id))
        .where(
            WorkSession.employee_id.in_(ids),
            WorkSession.status == WorkSessionStatus.running.value,
        )
        .group_by(WorkSession.employee_id)
    ).all()
    running = {int(employee_id): int(count) for employee_id, count in session_rows if employee_id}

    return {
        "load": [
            {
                "employee_id": int(employee.id),
                "employee_status": employee.status,
                "lifecycle_status": employee.lifecycle_status,
                "open_task_count": open_tasks.get(int(employee.id), 0),
                "running_sessions": running.get(int(employee.id), 0),
                "current_task_id": employee.current_task_id,
            }
            for employee in sorted(employees, key=lambda item: int(item.id))
        ]
    }


def inspect_gap(
    db: Session, *, company_id: int, task_id: int, candidate_employee_ids: list[int] | None = None
) -> dict:
    facts = task_fit(
        db, company_id=company_id, task_id=task_id, candidate_employee_ids=candidate_employee_ids
    )
    loads = {
        row["employee_id"]: row
        for row in current_load(db, company_id=company_id, employee_ids=candidate_employee_ids)[
            "load"
        ]
    }
    for candidate in facts["candidates"]:
        identifier = candidate["employee_id"]
        candidate["load"] = loads[identifier]
        candidate["readiness"] = readiness.readiness_report(
            db, db.get(Employee, identifier)
        ).as_dict()
    return facts
