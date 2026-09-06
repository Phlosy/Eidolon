"""Learning priorities (placeholder scoring, no real web research in MVP).

See docs/architecture.md §6.2.
"""

from sqlalchemy.orm import Session

from app.repositories import knowledge as knowledge_repo

FAILURE_PRIORITY_SCORE = 70
# §13.1：失败驱动与行为延伸两类优先级靠 source 区分，分数区间互不侵入。
FAILURE_SOURCE = "failure"
EXTENSION_SOURCE = "behavior-extension"


def record_failure(db: Session, employee_id: int, topic: str, reason: str) -> None:
    """Repeated failure → raise a learning priority (upsert by employee+topic)."""
    existing = knowledge_repo.get_priority_by_topic(db, employee_id, topic)
    if existing is not None:
        existing.score = min(100, existing.score + 10)
        existing.reason = f"重复失败：{reason}"
        existing.source = FAILURE_SOURCE
        db.flush()
        return
    knowledge_repo.create_learning_priority(
        db,
        employee_id=employee_id,
        topic=topic,
        score=FAILURE_PRIORITY_SCORE,
        reason=f"任务失败，需加强学习：{reason}",
        source=FAILURE_SOURCE,
    )


def record_extension(db: Session, employee_id: int, topic: str, score: int, reason: str) -> None:
    """行为策略派生的延伸学习项。分数被钉死在失败驱动项以下（§2：人格不能抢占事实的位置）。"""
    safe_score = max(0, min(int(score), FAILURE_PRIORITY_SCORE - 1))
    existing = knowledge_repo.get_priority_by_topic(db, employee_id, topic)
    if existing is not None:
        if existing.source != EXTENSION_SOURCE:
            return  # 失败 / 人工项优先，行为项不得上调也不得洗白
        if safe_score > existing.score:
            existing.score = safe_score
            existing.reason = reason
            db.flush()
        return
    knowledge_repo.create_learning_priority(
        db,
        employee_id=employee_id,
        topic=topic,
        score=safe_score,
        reason=reason,
        source=EXTENSION_SOURCE,
    )
