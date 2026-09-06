"""Learning retrieval (v0.2): inject an employee's own prior learning into TaskContext.

At dispatch time, the orchestrator retrieves the assignee's *private* knowledge
items whose topic/title keywords overlap the task title+description (top 5)
plus the names of their validated skills. The MockAdapter weaves these into
its simulated output ("using prior knowledge: <topic>") so the learning loop
is observable end-to-end.

v1 (docs/employee-brain-behavior-policy.md §7 接缝 2-3 / §9): 额度、相邻主题占比、是否连
候选技能一起交给 agent、上下文条目上限 —— 全部来自 `BehaviorPolicy.retrieval`，本模块**不读
任何人格字段**。默认策略（knowledge_limit=5、novel_topic_ratio=0、include_candidate_skills
=False）下的结果与改造前逐字一致。
"""

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.brain import DEFAULT_POLICY
from app.models.enums import KnowledgeScope, KnowledgeStatus, SkillValidationStatus
from app.repositories import knowledge as knowledge_repo

# 文档锚点：DEFAULT_POLICY.retrieval.knowledge_limit 必须等于它（§6 等价性由测试断言）。
TOP_KNOWLEDGE = 5
_MIN_TOKEN_LEN = 3


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9A-Za-z一-鿿]+", text.lower()) if len(t) >= _MIN_TOKEN_LEN}


@dataclass(frozen=True)
class SkillRef:
    """技能引用。旧实现只返回名字，导致 `SkillUsage.skill_id` 无处可取（§9）。"""

    id: int
    name: str
    validation_status: str
    reason: str  # matched_validated | policy_candidate

    @property
    def is_candidate(self) -> bool:
        return self.reason == "policy_candidate"


@dataclass(frozen=True)
class RetrievalResult:
    knowledge: list[str] = field(default_factory=list)
    skills: list[SkillRef] = field(default_factory=list)

    @property
    def skill_names(self) -> list[str]:
        return [skill.name for skill in self.skills]

    @property
    def candidate_skills(self) -> list[SkillRef]:
        return [skill for skill in self.skills if skill.is_candidate]


def retrieve_for_task(
    db: Session,
    employee_id: int,
    title: str,
    description: str = "",
    policy: Any = DEFAULT_POLICY,
) -> RetrievalResult:
    """Assemble private knowledge + skill refs according to the employee's BehaviorPolicy."""
    retrieval = policy.retrieval
    limit = max(0, int(retrieval.knowledge_limit))
    task_tokens = _tokens(f"{title} {description}")

    items = knowledge_repo.list_knowledge_items(
        db, scope=KnowledgeScope.private.value, employee_id=employee_id
    )
    matched: list[str] = []
    adjacent: list[str] = []
    if task_tokens:
        hits: list[tuple[int, str]] = []
        near: list[str] = []
        for item in items:
            if item.status != KnowledgeStatus.active.value:
                continue
            topic = item.topic or item.title
            overlap = len(task_tokens & _tokens(f"{item.topic} {item.title}"))
            if overlap:
                hits.append((overlap, topic))
            else:
                near.append(topic)
        # 直接命中按重叠度降序，相邻项保持 repo 的新到旧顺序
        hits.sort(key=lambda pair: -pair[0])
        matched = list(dict.fromkeys(topic for _, topic in hits))
        adjacent = list(dict.fromkeys(near))

    novel_quota = min(len(adjacent), int(limit * retrieval.novel_topic_ratio))
    knowledge = matched[: max(0, limit - novel_quota)] + adjacent[:novel_quota]

    skills: list[SkillRef] = []
    candidates: list[SkillRef] = []
    for skill in knowledge_repo.list_skills(db, employee_id):
        if skill.validation_status == SkillValidationStatus.validated.value:
            skills.append(
                SkillRef(skill.id, skill.name, skill.validation_status, "matched_validated")
            )
        elif (
            retrieval.include_candidate_skills
            and skill.validation_status == SkillValidationStatus.candidate.value
            and skill.attempts >= retrieval.candidate_min_attempts
            and (skill.success_count / skill.attempts if skill.attempts else 0.0)
            >= retrieval.candidate_min_success_rate
        ):
            candidates.append(
                SkillRef(skill.id, skill.name, skill.validation_status, "policy_candidate")
            )
    skills += candidates

    # 上下文条目硬上限：先保技能（已经代表能力），知识从尾部（最不相干）裁起。
    overflow = len(knowledge) + len(skills) - int(retrieval.max_context_items)
    if overflow > 0:
        knowledge = knowledge[: max(0, len(knowledge) - overflow)]

    return RetrievalResult(knowledge=knowledge, skills=skills)
