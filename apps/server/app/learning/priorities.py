"""Learning priorities (placeholder scoring, no real web research in MVP).

See docs/architecture.md §6.2.
"""

from sqlalchemy.orm import Session

from app.repositories import knowledge as knowledge_repo

FAILURE_PRIORITY_SCORE = 70


def record_failure(db: Session, employee_id: int, topic: str, reason: str) -> None:
    """Repeated failure → raise a learning priority (upsert by employee+topic)."""
    existing = knowledge_repo.get_priority_by_topic(db, employee_id, topic)
    if existing is not None:
        existing.score = min(100, existing.score + 10)
        existing.reason = f"重复失败：{reason}"
        db.flush()
        return
    knowledge_repo.create_learning_priority(
        db,
        employee_id=employee_id,
        topic=topic,
        score=FAILURE_PRIORITY_SCORE,
        reason=f"任务失败，需加强学习：{reason}",
    )
