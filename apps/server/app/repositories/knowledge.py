"""Knowledge/learning repositories.

Hard invariant (docs/architecture.md §3.4.1): memory_entries and scope=private
knowledge_items are only readable through the owner's own queries — every
function here filters by owner; there is intentionally no cross-employee read.

Tenant boundary (概念架构 §4.8): company isolation is enforced here, not in
callers. HTTP 上下文用 request identity 的公司；非请求上下文（任务执行路径等
`get_request_identity()` 为 None 的场景）必须由调用方显式传 ``company_id``，
否则共享 scope（department/company）的查询不做公司过滤。

R1.1 切读（docs/person-core-migration.md D4 批次 1）：memory / skills /
skill_usages / learning_records / learning_priorities 的属主口径已从
employee_id（deprecated 镜像列）切到 person_id；入参仍是 employee_id，
经 app/repositories/persons.py 的单一入口换算（read_criterion / write_person_id），
解析不到时回落旧口径 + warning。

R1.2（批次 2）knowledge_items：只有 **owner 口径** person 化（owner_person_id），
scope / department_id 分层语义不动。公司隔离的 join 链**保留经 owner_employee_id
→ employees**：persons 表刻意没有 company_id（人是跨公司的），公司边界是成员身份
语义，由 employees/departments 提供；兼容期镜像列由双写维持，join 无需改道。
"""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.request_context import get_request_identity
from app.models.enums import KnowledgeScope
from app.models.knowledge import (
    KnowledgeItem,
    LearningPriority,
    LearningRecord,
    MemoryEntry,
    Skill,
    SkillUsage,
)
from app.models.organization import Department, Employee
from app.repositories import persons as person_repo

# ---- memory (strictly per-owner) ----


def list_memory_entries(db: Session, employee_id: int) -> list[MemoryEntry]:
    return list(
        db.scalars(
            select(MemoryEntry)
            .where(
                person_repo.read_criterion(
                    db, employee_id, MemoryEntry.person_id, MemoryEntry.employee_id
                )
            )
            .order_by(MemoryEntry.id.desc())
        )
    )


def create_memory_entry(db: Session, employee_id: int, **fields) -> MemoryEntry:
    # 双写：employee_id（deprecated 镜像）+ person_id（权威口径）
    entry = MemoryEntry(
        employee_id=employee_id,
        person_id=person_repo.write_person_id(db, employee_id),
        **fields,
    )
    db.add(entry)
    db.flush()
    return entry


# ---- knowledge ----


def list_knowledge_items(
    db: Session,
    scope: str | None = None,
    topic: str | None = None,
    employee_id: int | None = None,
    company_id: int | None = None,
) -> list[KnowledgeItem]:
    stmt = select(KnowledgeItem).order_by(KnowledgeItem.id.desc())
    identity = get_request_identity()
    # 请求内以 identity 为准；非请求上下文（identity None）回落到显式 company_id。
    effective_company_id = identity.company_id if identity is not None else company_id
    if effective_company_id is not None:
        stmt = (
            stmt.outerjoin(Employee, KnowledgeItem.owner_employee_id == Employee.id)
            .outerjoin(Department, KnowledgeItem.department_id == Department.id)
            .where(
                or_(
                    Employee.company_id == effective_company_id,
                    Department.company_id == effective_company_id,
                )
            )
        )
    if scope is not None:
        stmt = stmt.where(KnowledgeItem.scope == scope)
    if topic is not None:
        stmt = stmt.where(KnowledgeItem.topic == topic)
    if scope == KnowledgeScope.private.value:
        # private knowledge is only ever queried per owner
        # R1.2：owner 口径切 owner_person_id（单一入口换算，解析不到回落镜像列 + warning）
        stmt = stmt.where(
            person_repo.read_criterion(
                db, employee_id, KnowledgeItem.owner_person_id, KnowledgeItem.owner_employee_id
            )
        )
    return list(db.scalars(stmt))


def get_knowledge_item(db: Session, item_id: int) -> KnowledgeItem | None:
    stmt = select(KnowledgeItem).where(KnowledgeItem.id == item_id)
    identity = get_request_identity()
    if identity is not None:
        stmt = (
            stmt.outerjoin(Employee, KnowledgeItem.owner_employee_id == Employee.id)
            .outerjoin(Department, KnowledgeItem.department_id == Department.id)
            .where(
                or_(
                    Employee.company_id == identity.company_id,
                    Department.company_id == identity.company_id,
                )
            )
        )
    return db.scalar(stmt)


def create_knowledge_item(db: Session, **fields) -> KnowledgeItem:
    # 双写（R1.2）：owner_employee_id 是 deprecated 镜像；owner_person_id 为权威口径。
    # owner 为 NULL 的条目（department/company scope）没有人称可解析，直接跳过。
    owner_employee_id = fields.get("owner_employee_id")
    if owner_employee_id is not None:
        fields.setdefault("owner_person_id", person_repo.write_person_id(db, owner_employee_id))
    item = KnowledgeItem(**fields)
    db.add(item)
    db.flush()
    return item


# ---- skills ----


def list_skills(db: Session, employee_id: int) -> list[Skill]:
    return list(
        db.scalars(
            select(Skill)
            .where(person_repo.read_criterion(db, employee_id, Skill.person_id, Skill.employee_id))
            .order_by(Skill.id)
        )
    )


def get_skill_by_name(db: Session, employee_id: int, name: str) -> Skill | None:
    return db.scalars(
        select(Skill).where(
            person_repo.read_criterion(db, employee_id, Skill.person_id, Skill.employee_id),
            Skill.name == name,
        )
    ).first()


def create_skill(db: Session, **fields) -> Skill:
    # 双写：fields 里的 employee_id 是 deprecated 镜像；person_id 由单一入口解析补齐
    fields.setdefault("person_id", person_repo.write_person_id(db, fields["employee_id"]))
    skill = Skill(**fields)
    db.add(skill)
    db.flush()
    return skill


# ---- skill usages（候选技能基准，§10）----


def create_skill_usage(db: Session, **fields) -> SkillUsage | None:
    """幂等写入：`uq_skill_usage(task_id, skill_id)` 意味着重跑/重派不会记两笔。"""
    task_id, skill_id = fields.get("task_id"), fields.get("skill_id")
    if task_id is not None and skill_id is not None:
        existing = get_skill_usage(db, task_id=task_id, skill_id=skill_id)
        if existing is not None:
            return existing
    # 双写：employee_id（deprecated 镜像）+ person_id（权威口径）
    fields.setdefault("person_id", person_repo.write_person_id(db, fields["employee_id"]))
    usage = SkillUsage(**fields)
    db.add(usage)
    db.flush()
    return usage


def get_skill_usage(
    db: Session,
    usage_id: int | None = None,
    task_id: int | None = None,
    skill_id: int | None = None,
) -> SkillUsage | None:
    stmt = select(SkillUsage)
    if usage_id is not None:
        stmt = stmt.where(SkillUsage.id == usage_id)
    if task_id is not None:
        stmt = stmt.where(SkillUsage.task_id == task_id)
    if skill_id is not None:
        stmt = stmt.where(SkillUsage.skill_id == skill_id)
    return db.scalars(stmt).first()


def list_skill_usages(db: Session, employee_id: int) -> list[SkillUsage]:
    return list(
        db.scalars(
            select(SkillUsage)
            .where(
                person_repo.read_criterion(
                    db, employee_id, SkillUsage.person_id, SkillUsage.employee_id
                )
            )
            .order_by(SkillUsage.id.desc())
        )
    )


def list_skill_usages_for_task(db: Session, task_id: int) -> list[SkillUsage]:
    """本次任务用过的技能（用于写入客观 success，与人的评价无关）。"""
    return list(db.scalars(select(SkillUsage).where(SkillUsage.task_id == task_id)))


def skill_usage_benchmarks(db: Session, employee_id: int) -> dict[str, float | int | None]:
    """§10.2 的三个指标（SQL 聚合，不捐全表）。

    * `trial_rate` —— 候选技能占全部技能投交的比例（人格只影响这个“曝光率”）
    * `conversion_rate` —— 候选技能参与的任务客观成功率（事实，来自 _finalize）
    * `useful_rate` —— 已评价里判为 useful 的比例；**pending 不入分母**（没评过 ≠ 没用了）
    分母为 0 时返回 None，而不是 0.0：没数据与数据为零是两回事。
    """
    owner = person_repo.read_criterion(
        db, employee_id, SkillUsage.person_id, SkillUsage.employee_id
    )
    rows = db.execute(
        select(
            SkillUsage.selection_reason,
            SkillUsage.success,
            SkillUsage.outcome,
            func.count(SkillUsage.id),
        )
        .where(owner)
        .group_by(SkillUsage.selection_reason, SkillUsage.success, SkillUsage.outcome)
    ).all()
    total = candidate = candidate_success = useful = not_useful = pending = 0
    for reason, success, outcome, count in rows:
        total += count
        if reason == "policy_candidate":
            candidate += count
            if success:
                candidate_success += count
        if outcome is None:
            pending += count
        elif outcome == "useful":
            useful += count
        elif outcome == "not_useful":
            not_useful += count
    rated = useful + not_useful
    skills = db.scalar(
        select(func.count(func.distinct(SkillUsage.skill_id))).where(
            owner,
            SkillUsage.selection_reason == "policy_candidate",
        )
    )
    return {
        "total_usages": total,
        "candidate_usages": candidate,
        "candidate_skills_tried": int(skills or 0),
        "rated_usages": rated,
        "pending_ratings": pending,
        "trial_rate": (candidate / total) if total else None,
        "conversion_rate": (candidate_success / candidate) if candidate else None,
        "useful_rate": (useful / rated) if rated else None,
    }


def get_skill_usage_for_employee(db: Session, usage_id: int, employee_id: int) -> SkillUsage | None:
    """跨员工/跨公司都取不到：评价候选技能属于该技能所有者的管理动作。

    归属通过 skills 连接判定（SkillUsage 上没有 employee 直达的鉴权语义），
    调用方传入的 employee 已经过公司作用域校验。R1.1：归属判定切到 Skill.person_id。
    """
    return db.scalars(
        select(SkillUsage)
        .join(Skill, SkillUsage.skill_id == Skill.id)
        .where(
            SkillUsage.id == usage_id,
            person_repo.read_criterion(db, employee_id, Skill.person_id, Skill.employee_id),
        )
    ).first()


# ---- learning ----


def list_learning_records(db: Session, employee_id: int) -> list[LearningRecord]:
    return list(
        db.scalars(
            select(LearningRecord)
            .where(
                person_repo.read_criterion(
                    db, employee_id, LearningRecord.person_id, LearningRecord.employee_id
                )
            )
            .order_by(LearningRecord.id.desc())
        )
    )


def create_learning_record(db: Session, **fields) -> LearningRecord:
    # 双写：employee_id（deprecated 镜像）+ person_id（权威口径）
    fields.setdefault("person_id", person_repo.write_person_id(db, fields["employee_id"]))
    record = LearningRecord(**fields)
    db.add(record)
    db.flush()
    return record


def count_learning_records(db: Session, employee_id: int) -> int:
    return len(
        list(
            db.scalars(
                select(LearningRecord.id).where(
                    person_repo.read_criterion(
                        db, employee_id, LearningRecord.person_id, LearningRecord.employee_id
                    )
                )
            )
        )
    )


def list_learning_priorities(db: Session, employee_id: int) -> list[LearningPriority]:
    return list(
        db.scalars(
            select(LearningPriority)
            .where(
                person_repo.read_criterion(
                    db, employee_id, LearningPriority.person_id, LearningPriority.employee_id
                )
            )
            .order_by(LearningPriority.score.desc())
        )
    )


def get_priority_by_topic(db: Session, employee_id: int, topic: str) -> LearningPriority | None:
    return db.scalars(
        select(LearningPriority).where(
            person_repo.read_criterion(
                db, employee_id, LearningPriority.person_id, LearningPriority.employee_id
            ),
            LearningPriority.topic == topic,
        )
    ).first()


def create_learning_priority(db: Session, **fields) -> LearningPriority:
    # 双写：employee_id（deprecated 镜像）+ person_id（权威口径）
    fields.setdefault("person_id", person_repo.write_person_id(db, fields["employee_id"]))
    priority = LearningPriority(**fields)
    db.add(priority)
    db.flush()
    return priority
