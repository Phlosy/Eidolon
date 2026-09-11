"""Position Fit 领域模型（P8，docs/position-fit.md）。

这些都是**派生读模型**（dataclass / 字符串常量），不落库 —— Fit 是
EmployeeCompetency × PositionProfileVersion 的函数，不是第二真相。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


class EvaluationStatus:
    """Requirement 评估状态 —— Unknown != Bad 是这里最硬的规则。"""

    UNRATED = "UNRATED"  # 员工没有该能力/score null —— 不是 0 分
    INSUFFICIENT_CONFIDENCE = "INSUFFICIENT_CONFIDENCE"  # 分数高但证据不足，不能判成合格/不合格
    BELOW_MINIMUM = "BELOW_MINIMUM"
    MEETS_MINIMUM = "MEETS_MINIMUM"
    MEETS_TARGET = "MEETS_TARGET"
    EXCEEDS_TARGET = "EXCEEDS_TARGET"  # 预留（target + margin），v1 不用

    KNOWN = {BELOW_MINIMUM, MEETS_MINIMUM, MEETS_TARGET, EXCEEDS_TARGET}


class GapType:
    CRITICAL_GAP = "CRITICAL_GAP"  # critical + below minimum（真实短板）
    REQUIRED_GAP = "REQUIRED_GAP"  # required + below minimum
    TARGET_GAP = "TARGET_GAP"  # required + meets minimum + below target
    PREFERRED_GAP = "PREFERRED_GAP"  # preferred + below minimum
    CRITICAL_UNCERTAINTY = "CRITICAL_UNCERTAINTY"  # critical + unrated/insufficient —— 不是失败
    REQUIRED_UNCERTAINTY = "REQUIRED_UNCERTAINTY"  # required + unrated/insufficient
    UNRATED = "UNRATED"
    # 发展机会（preferred，meets minimum 但 below target，或未评估）用独立字段表达


class QualificationStatus:
    QUALIFIED = "QUALIFIED"
    QUALIFIED_WITH_GAPS = "QUALIFIED_WITH_GAPS"
    NOT_QUALIFIED = "NOT_QUALIFIED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class FitStatus:
    NOT_EVALUABLE = "NOT_EVALUABLE"  # 无 ACTIVE profile（不能默认 100%）
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # required 覆盖不足 / 关键 unknown
    EVALUABLE = "EVALUABLE"
    STRONG_MATCH = "STRONG_MATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    WEAK_MATCH = "WEAK_MATCH"
    CRITICAL_GAP = "CRITICAL_GAP"


@dataclass(frozen=True)
class FitOwner:
    """Fit 的 owner 口径（T2.5）：**person 优先**，employee 仅作回落/上下文。

    市场里的候选人没有 employee 行（`person_id` only）；在册员工两条都有。
    同一个人的两条路径必须产出同一份结果（含 `inputs_hash`）—— 哈希只吃
    person 口径，employee 只在 person 解析不到时（legacy 行）才进入哈希。
    """

    person_id: int | None = None
    employee_id: int | None = None

    @property
    def hash_payload(self) -> dict:
        return {
            "owner_person_id": self.person_id,
            "owner_employee_id": None if self.person_id is not None else self.employee_id,
        }


@dataclass
class RequirementEvaluation:
    """一个 PositionCompetencyRequirement × EmployeeCompetency 的评估（纯计算）。"""

    requirement_id: int
    competency_definition_id: int
    code: str
    name: str
    domain_code: str
    domain_name: str
    kind: str  # general | professional
    requirement_type: str  # required | preferred
    critical: bool
    minimum_score: int | None
    target_score: int | None
    minimum_confidence: float | None
    weight: float
    # 员工侧
    employee_score: int | None = None
    employee_confidence: float | None = None
    # 评估
    evaluation_status: str = EvaluationStatus.UNRATED
    reason_code: str = EvaluationStatus.UNRATED
    gap_type: str | None = None
    is_strength: bool = False
    is_development_opportunity: bool = False
    is_unknown: bool = True
    normalized_fit: float | None = None
    margin_to_minimum: int | None = None
    margin_to_target: int | None = None


@dataclass
class PositionFitResult:
    """一对一 Fit 分析结果（只读、确定性、版本化）。"""

    #: owner 口径（T2.5）：市场候选人为 person-only（employee_id=None）
    employee_id: int | None = None
    person_id: int | None = None
    position_definition_id: int = 0
    position_code: str = ""
    profile_version_id: int | None = None
    profile_version: int | None = None
    profile_status: str | None = None
    configured: bool = False
    assessment_profile_code: str | None = None
    fit_status: str = FitStatus.NOT_EVALUABLE
    qualification_status: str = QualificationStatus.INSUFFICIENT_DATA
    # 主数字（Unknown 排除，绝不把"没测"当 0）
    known_fit_score: float | None = None
    overall_fit_score: float | None = None
    fit_confidence: float | None = None
    # coverage 永远基于全部 requirements（unknown 不会让 coverage 变 100%）
    requirement_coverage: float = 0.0
    required_coverage: float = 0.0
    preferred_coverage: float = 0.0
    known_count: int = 0
    total_count: int = 0
    # 分类
    strengths: list[RequirementEvaluation] = field(default_factory=list)
    gaps: list[RequirementEvaluation] = field(default_factory=list)
    uncertainties: list[RequirementEvaluation] = field(default_factory=list)
    development_opportunities: list[RequirementEvaluation] = field(default_factory=list)
    requirement_evaluations: list[RequirementEvaluation] = field(default_factory=list)
    # 元数据
    general_fit: float | None = None
    professional_fit: float | None = None
    engine_version: str = ""
    policy_version: str = ""
    inputs_hash: str = ""
    calculated_at: datetime | None = None
