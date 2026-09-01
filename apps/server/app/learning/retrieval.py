"""Learning retrieval (v0.2): inject an employee's own prior learning into TaskContext.

At dispatch time, the orchestrator retrieves the assignee's *private* knowledge
items whose topic/title keywords overlap the task title+description (top 5)
plus the names of their validated skills. The MockAdapter weaves these into
its simulated output ("using prior knowledge: <topic>") so the learning loop
is observable end-to-end.
"""

import re

from sqlalchemy.orm import Session

from app.models.enums import KnowledgeScope, KnowledgeStatus, SkillValidationStatus
from app.repositories import knowledge as knowledge_repo

TOP_KNOWLEDGE = 5
_MIN_TOKEN_LEN = 3


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9A-Za-z一-鿿]+", text.lower()) if len(t) >= _MIN_TOKEN_LEN}


def retrieve_for_task(
    db: Session, employee_id: int, title: str, description: str = ""
) -> tuple[list[str], list[str]]:
    """Return (prior_knowledge_topics, validated_skill_names) for a task."""
    task_tokens = _tokens(f"{title} {description}")
    knowledge: list[str] = []
    if task_tokens:
        scored: list[tuple[int, str]] = []
        items = knowledge_repo.list_knowledge_items(
            db, scope=KnowledgeScope.private.value, employee_id=employee_id
        )
        for item in items:
            if item.status != KnowledgeStatus.active.value:
                continue
            item_tokens = _tokens(f"{item.topic} {item.title}")
            overlap = len(task_tokens & item_tokens)
            if overlap:
                scored.append((overlap, item.topic or item.title))
        scored.sort(key=lambda pair: -pair[0])
        seen: set[str] = set()
        for _, topic in scored:
            if topic not in seen:
                seen.add(topic)
                knowledge.append(topic)
            if len(knowledge) >= TOP_KNOWLEDGE:
                break

    skills = [
        skill.name
        for skill in knowledge_repo.list_skills(db, employee_id)
        if skill.validation_status == SkillValidationStatus.validated.value
    ]
    return knowledge, skills
