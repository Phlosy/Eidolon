"""EvidencePolicy —— 证据权重/默认信号/环境打折的唯一配置处（docs/evidence-pipeline.md §六/§37）。

禁止业务代码散落 `if source == "test": weight=...`：一切来源质量、默认信号、角色强度、
环境（mock）惩罚都在这里集中。改策略 = 改这里，不应动 collector / engine。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import EvidenceSourceKind

#: 证据来源的固有可信度（reliability 默认值）—— assessment-system.md §4.1 固定表。
#: 同一证据还可以带显式 reliability 覆盖（normalizer 写入时按此回落）。
SOURCE_RELIABILITY: dict[str, float] = {
    EvidenceSourceKind.assessment.value: 1.0,
    EvidenceSourceKind.test.value: 0.9,
    EvidenceSourceKind.review.value: 0.85,
    EvidenceSourceKind.artifact.value: 0.8,
    EvidenceSourceKind.peer_review.value: 0.75,
    EvidenceSourceKind.project.value: 0.75,
    EvidenceSourceKind.task.value: 0.7,
    EvidenceSourceKind.skill_usage.value: 0.65,
    EvidenceSourceKind.user_feedback.value: 0.6,
    EvidenceSourceKind.learning.value: 0.5,
}
DEFAULT_SOURCE_RELIABILITY = 0.5

#: 期望角色的证明力度（strength 默认值）
ROLE_STRENGTH: dict[str, float] = {
    "primary": 1.0,
    "supporting": 0.7,
    "optional": 0.4,
}
DEFAULT_ROLE_STRENGTH = 0.5

#: 环境打折：mock/教程来源的证据可靠性乘数 —— 不能让 Tutorial 把能力刷高（§37）。
MOCK_ENVIRONMENT = "mock"
MOCK_RELIABILITY_FACTOR = 0.5

#: 默认信号（0-100 水平观测）：按 (source_kind, outcome) 给一个**策略默认值**。
#: 只有"确实发生过"且能排除明显伪造时 collectors 才会用它；宁缺勿编 —— 没有对应
#: 组合的 collectors 不得凭空猜数。
SIGNAL_DEFAULTS: dict[tuple[str, str], int] = {
    ("task", "done"): 80,
    ("task", "failed"): 30,
    ("test", "done"): 82,
    ("test", "failed"): 35,
    ("review", "approved"): 84,
    ("review", "conditionally_approved"): 80,
    ("review", "changes_requested"): 45,
    ("review", "rejected"): 25,
    ("skill_usage", "useful_success"): 80,
    ("skill_usage", "useful_failed"): 60,
    ("learning", "recorded"): 70,
    ("artifact", "approved"): 82,
}

#: 任务类型 → 能力提示（position 期望缺失时的兜底映射，集中声明而非散落）。
#: value: (competency_code, role)。`task_kind_hint` 在证据 metadata.reason 里标明。
TASK_KIND_HINTS: dict[str, list[tuple[str, str]]] = {
    "development": [
        ("execution", "primary"),
        ("backend_engineering", "supporting"),
        ("code_quality", "supporting"),
    ],
    "testing": [
        ("testing", "primary"),
        ("quality_reliability", "supporting"),
    ],
    "research": [
        ("analysis_problem_solving", "primary"),
        ("information_retrieval", "supporting"),
    ],
    "planning": [
        ("planning_organization", "primary"),
        ("execution", "supporting"),
    ],
    # order_review / final_review / general：不给提示 —— 没有可靠映射就不编证据
}

EVENT_DISPATCH: dict[str, str] = {
    "task.completed": "task",
    "task.failed": "task",
    "project.completed": "project",
    "learning.completed": "learning",
    "skill.validated": "skill_usage",
}


@dataclass(frozen=True)
class EvidencePolicy:
    """读取策略的入口（常量即策略，当前无公司级覆盖）。"""

    reliability_of: dict[str, float] = field(default_factory=lambda: dict(SOURCE_RELIABILITY))
    role_strength: dict[str, float] = field(default_factory=lambda: dict(ROLE_STRENGTH))
    mock_factor: float = MOCK_RELIABILITY_FACTOR

    def reliability(self, source_type: str, environment: str = "") -> float:
        base = self.reliability_of.get(source_type, DEFAULT_SOURCE_RELIABILITY)
        if environment == MOCK_ENVIRONMENT:
            return round(base * self.mock_factor, 4)
        return base

    def strength_for_role(self, role: str) -> float:
        return self.role_strength.get(role, DEFAULT_ROLE_STRENGTH)

    def signal_default(self, source_type: str, outcome: str) -> int | None:
        return SIGNAL_DEFAULTS.get((source_type, outcome))


POLICY = EvidencePolicy()
