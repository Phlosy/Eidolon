"""Position Fit Policy —— 匹配公式的阈值/曲线唯一出处（P8，docs/position-fit.md）。

一切 magic number 集中在策略对象；未来可 Company override（当前不要求）。
P8 只读：不读写 EmployeeCompetency / Position / Assignment / Brain / Assessment。
"""

from __future__ import annotations

from dataclasses import dataclass

POSITION_FIT_ENGINE_VERSION = "v1"
POSITION_FIT_POLICY_VERSION = "v1"


@dataclass(frozen=True)
class PositionFitPolicy:
    """确定性匹配策略（v1）。

    - minimum_required_coverage：required 覆盖度低于它 ⇒ 主状态 INSUFFICIENT_DATA
      （不能给 73% 这种虚假精确感）。
    - below_ceiling / meet_floor：target 曲线两段的边界 —— BELOW_MINIMUM 映射到
      (0, below_ceiling)，MEETS_MINIMUM 从 meet_floor 线性升到 1.0(MEETS_TARGET)。
    - unknown_policy='exclude'：UNRATED / INSUFFICIENT_CONFIDENCE 不进 known fit 惩罚
      （Unknown != Bad），由 coverage / fit_confidence / status 单独表达。
    - critical 是 qualification gate（critical gap → NOT_QUALIFIED），但**不把 fit 置 0**
      —— 仍返回 known fit 以便看到"总体接近但有关键短板"。
    """

    minimum_required_coverage: float = 0.5
    below_ceiling: float = 0.69
    meet_floor: float = 0.70
    strong_match: float = 0.80
    partial_match: float = 0.60
    weight_normalization: str = "known_group"  # 只对 known requirements 在各自组内归一
    unknown_policy: str = "exclude"
    # fit_confidence = 加权 competency confidence × coverage_factor（0..1）
    coverage_factor_target: float = 1.0


POLICY = PositionFitPolicy()
