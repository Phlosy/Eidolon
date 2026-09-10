"""Learning retrieval: inject relevant knowledge + skills into TaskContext.

At dispatch time, the orchestrator retrieves knowledge items whose topic/title
keywords overlap the task title+description (top N) plus the assignee's
validated skills. The MockAdapter weaves these into its simulated output
("using prior knowledge: <topic>") so the learning loop is observable
end-to-end.

K1（docs/talent-ecosystem-plan.md §3 / vision §5）检索 scope 分层：不再只查
员工的 *private* 知识，而是同时检索 private + department + company 三层。
scope 越高代表越权威/越共识，排序权重越高；命中排序为：

1. 主键：命中强度降序（相关性强者优先）；
2. 次键：scope 权重降序（company > department > private，见 ``_SCOPE_WEIGHT``）；
3. 同档 stale 条目排后（K2 freshness 降置信：stale ≠ 无效，不剔除）；
4. 同分保持 repo 的新到旧顺序（稳定排序）。

K2（v26）：「overlap 怎么算」换成 FTS5 全文检索——`knowledge_items_fts`
（trigram，子串语义）先从 title/topic/**content** 里找出候选，命中强度再用
Python 精确计数（查询 term 在条目全文里的子串命中数）。CJK 长 token 拆 3 字
滑窗（trigram 的最小匹配粒度），中文正文真正可检索。排序原则（强度 → scope
→ 稳定序）与 K1 完全一致；bm25 排名评估过但没进排序键——同分必须保持 repo
稳定序（对拍测试的硬约束），rank 会打破它。FTS 不可用（旧库未迁移/查询失败）
时整条匹配回落 K1 的 token-overlap 旧口径（只扫 title/topic），不炸。

freshness 驱动现状：`freshness_status` 目前只在创建时写 "fresh"
（services/learning.py 的产出点），**没有任何路径把它置 stale**（枚举与
last_validated_at 列在位，驱动留待「知识保鲜」专题）——检索侧已支持降权。

公司隔离在仓库层强制（概念架构 §4.8）：本函数跑在任务执行路径上，通常没有
request identity，因此显式把**执行员工的公司**传给 repo 做过滤——别家公司的
department/company 知识结构上进不来，私有知识仍只查 owner 本人。

v1 (docs/employee-brain-behavior-policy.md §7 接缝 2-3 / §9): 额度、相邻主题占比、是否连
候选技能一起交给 agent、上下文条目上限 —— 全部来自 `BehaviorPolicy.retrieval`，本模块**不读
任何人格字段**。默认策略（knowledge_limit=5、novel_topic_ratio=0、include_candidate_skills
=False）下配额语义不变：只有 private 命中时结果与 K1 之前逐字一致。
"""

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.brain import DEFAULT_POLICY
from app.models.enums import KnowledgeScope, KnowledgeStatus, SkillValidationStatus
from app.models.organization import Employee
from app.repositories import knowledge as knowledge_repo

# 文档锚点：DEFAULT_POLICY.retrieval.knowledge_limit 必须等于它（§6 等价性由测试断言）。
TOP_KNOWLEDGE = 5
_MIN_TOKEN_LEN = 3

# K1：scope 越高越权威（vision §5），作为命中排序的次键。
_SCOPE_WEIGHT = {
    KnowledgeScope.company.value: 2,
    KnowledgeScope.department.value: 1,
    KnowledgeScope.private.value: 0,
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^0-9A-Za-z一-鿿]+", text.lower()) if len(t) >= _MIN_TOKEN_LEN}


_CJK_RUN = re.compile(r"[一-鿿]")


def _query_terms(text: str) -> list[str]:
    """FTS/子串口径的查询 term 序列（K2）。

    在 `_tokens`（正则分词）之上：含 CJK 的长 token 拆成 3 字滑窗 —— trigram 的
    最小匹配粒度是 3 字符，「实现用户登录功能」整段当一个 token 时只能整串匹配；
    滑窗后「用户登录」能命中它，中文正文真正可检索。ASCII/数字 token 原样保留。
    """
    terms: list[str] = []
    for token in sorted(_tokens(text)):
        if len(token) > 3 and _CJK_RUN.search(token):
            terms.extend(token[i : i + 3] for i in range(len(token) - 2))
        else:
            terms.append(token)
    return terms


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
    """Assemble scoped knowledge + skill refs according to the employee's BehaviorPolicy."""
    retrieval = policy.retrieval
    limit = max(0, int(retrieval.knowledge_limit))
    task_text = f"{title} {description}"
    task_tokens = _tokens(task_text)
    query_terms = _query_terms(task_text)

    # K1：三层 scope 都查；任务执行路径没有 request identity，公司边界用
    # 执行员工自己的 company_id 显式下推到 repo（跨公司泄露在 SQL 层就不可能）。
    # 员工不存在 → 公司不可判定 → 共享 scope 一律不查，只回落到私有知识。
    employee = db.get(Employee, employee_id)
    company_id = employee.company_id if employee is not None else None
    items: list = []
    items += knowledge_repo.list_knowledge_items(
        db, scope=KnowledgeScope.private.value, employee_id=employee_id, company_id=company_id
    )
    if company_id is not None:
        for shared_scope in (KnowledgeScope.department.value, KnowledgeScope.company.value):
            items += knowledge_repo.list_knowledge_items(
                db, scope=shared_scope, company_id=company_id
            )

    # K2：FTS5 先出候选集（title/topic/content 全文）；None = 索引不可用 → 回落旧口径
    fts_ids = knowledge_repo.fts_match_ids(db, set(query_terms)) if query_terms else set()

    matched: list[str] = []
    adjacent: list[str] = []
    if task_tokens:
        hits: list[tuple[int, int, int, str]] = []
        near: list[str] = []
        for item in items:
            if item.status != KnowledgeStatus.active.value:
                continue
            topic = item.topic or item.title
            if fts_ids is None:
                # 回落：K1 的 token-overlap 旧口径（只扫 title/topic，正则分词集合交）
                strength = len(task_tokens & _tokens(f"{item.topic} {item.title}"))
            elif item.id not in fts_ids:
                strength = 0
            else:
                # 命中强度：查询 term 在条目全文（含正文）里的子串命中数。
                # 与 trigram 的匹配语义对齐（term ≥3 字符 ⇒ 子串命中 ⇔ FTS 命中）。
                body = f"{item.title} {item.topic} {item.content}".lower()
                strength = sum(1 for term in query_terms if term in body)
            stale = 1 if getattr(item, "freshness_status", "fresh") == "stale" else 0
            if strength:
                hits.append((strength, _SCOPE_WEIGHT.get(item.scope, 0), stale, topic))
            else:
                near.append(topic)
        # 主键命中强度降序、次键 scope 权重降序、同档 stale 排后（K2 降置信，不剔除）、
        # 同分保持 repo 的新到旧顺序（稳定排序）
        hits.sort(key=lambda hit: (-hit[0], -hit[1], hit[2]))
        matched = list(dict.fromkeys(topic for _, _, _, topic in hits))
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
