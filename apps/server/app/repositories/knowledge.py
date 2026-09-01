"""Knowledge/learning repositories.

Hard invariant (docs/architecture.md §3.4.1): memory_entries and scope=private
knowledge_items are only readable through the owner's own queries — every
function here filters by employee_id; there is intentionally no cross-employee read.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import KnowledgeScope
from app.models.knowledge import (
    KnowledgeItem,
    LearningPriority,
    LearningRecord,
    MemoryEntry,
    Skill,
)

# ---- memory (strictly per-employee) ----


def list_memory_entries(db: Session, employee_id: int) -> list[MemoryEntry]:
    return list(
        db.scalars(
            select(MemoryEntry)
            .where(MemoryEntry.employee_id == employee_id)
            .order_by(MemoryEntry.id.desc())
        )
    )


def create_memory_entry(db: Session, employee_id: int, **fields) -> MemoryEntry:
    entry = MemoryEntry(employee_id=employee_id, **fields)
    db.add(entry)
    db.flush()
    return entry


# ---- knowledge ----


def list_knowledge_items(
    db: Session,
    scope: str | None = None,
    topic: str | None = None,
    employee_id: int | None = None,
) -> list[KnowledgeItem]:
    stmt = select(KnowledgeItem).order_by(KnowledgeItem.id.desc())
    if scope is not None:
        stmt = stmt.where(KnowledgeItem.scope == scope)
    if topic is not None:
        stmt = stmt.where(KnowledgeItem.topic == topic)
    if scope == KnowledgeScope.private.value:
        # private knowledge is only ever queried per owner
        stmt = stmt.where(KnowledgeItem.owner_employee_id == employee_id)
    return list(db.scalars(stmt))


def get_knowledge_item(db: Session, item_id: int) -> KnowledgeItem | None:
    return db.get(KnowledgeItem, item_id)


def create_knowledge_item(db: Session, **fields) -> KnowledgeItem:
    item = KnowledgeItem(**fields)
    db.add(item)
    db.flush()
    return item


# ---- skills ----


def list_skills(db: Session, employee_id: int) -> list[Skill]:
    return list(
        db.scalars(select(Skill).where(Skill.employee_id == employee_id).order_by(Skill.id))
    )


def get_skill_by_name(db: Session, employee_id: int, name: str) -> Skill | None:
    return db.scalars(
        select(Skill).where(Skill.employee_id == employee_id, Skill.name == name)
    ).first()


def create_skill(db: Session, **fields) -> Skill:
    skill = Skill(**fields)
    db.add(skill)
    db.flush()
    return skill


# ---- learning ----


def list_learning_records(db: Session, employee_id: int) -> list[LearningRecord]:
    return list(
        db.scalars(
            select(LearningRecord)
            .where(LearningRecord.employee_id == employee_id)
            .order_by(LearningRecord.id.desc())
        )
    )


def create_learning_record(db: Session, **fields) -> LearningRecord:
    record = LearningRecord(**fields)
    db.add(record)
    db.flush()
    return record


def count_learning_records(db: Session, employee_id: int) -> int:
    return len(
        list(db.scalars(select(LearningRecord.id).where(LearningRecord.employee_id == employee_id)))
    )


def list_learning_priorities(db: Session, employee_id: int) -> list[LearningPriority]:
    return list(
        db.scalars(
            select(LearningPriority)
            .where(LearningPriority.employee_id == employee_id)
            .order_by(LearningPriority.score.desc())
        )
    )


def get_priority_by_topic(db: Session, employee_id: int, topic: str) -> LearningPriority | None:
    return db.scalars(
        select(LearningPriority).where(
            LearningPriority.employee_id == employee_id, LearningPriority.topic == topic
        )
    ).first()


def create_learning_priority(db: Session, **fields) -> LearningPriority:
    priority = LearningPriority(**fields)
    db.add(priority)
    db.flush()
    return priority
