"""RequirementEvaluator —— 单条 PositionRequirement × EmployeeCompetency 的纯判定。

确定性（纯函数，不触库）；顺序即规格 §四：UNRATED → INSUFFICIENT_CONFIDENCE →
BELOW_MINIMUM → MEETS_MINIMUM → MEETS_TARGET。Unknown != Bad。
"""

from __future__ import annotations

from app.talent.fit.models import (
    EvaluationStatus,
    GapType,
    RequirementEvaluation,
)


def evaluate(
    requirement: RequirementEvaluation,
    *,
    employee_score: int | None,
    employee_confidence: float | None,
) -> RequirementEvaluation:
    req = requirement
    req.employee_score = employee_score
    req.employee_confidence = employee_confidence

    if employee_score is None:
        req.evaluation_status = EvaluationStatus.UNRATED
        req.reason_code = EvaluationStatus.UNRATED
        req.is_unknown = True
        return req

    # Unknown != Bad：分数高但证据不足 ⇒ 不能判成合格，也不能判成不合格
    if req.minimum_confidence is not None and (
        employee_confidence is None or employee_confidence < req.minimum_confidence
    ):
        req.evaluation_status = EvaluationStatus.INSUFFICIENT_CONFIDENCE
        req.reason_code = "CONFIDENCE_BELOW_REQUIREMENT"
        req.is_unknown = True
        return req

    req.is_unknown = False
    minimum = req.minimum_score
    target = req.target_score
    if minimum is not None and employee_score < minimum:
        req.evaluation_status = EvaluationStatus.BELOW_MINIMUM
        req.reason_code = EvaluationStatus.BELOW_MINIMUM
        req.margin_to_minimum = employee_score - minimum
        if target is not None:
            req.margin_to_target = employee_score - target
        return req
    if target is not None and employee_score >= target:
        req.evaluation_status = EvaluationStatus.MEETS_TARGET
        req.reason_code = EvaluationStatus.MEETS_TARGET
        req.margin_to_target = employee_score - target
        return req
    # meets minimum 但 below target
    req.evaluation_status = EvaluationStatus.MEETS_MINIMUM
    req.reason_code = EvaluationStatus.MEETS_MINIMUM
    if minimum is not None:
        req.margin_to_minimum = employee_score - minimum
    if target is not None:
        req.margin_to_target = employee_score - target
    return req


def normalized_fit(req: RequirementEvaluation) -> float | None:
    """target 曲线：BELOW_MINIMUM → (0,.69)；MEETS_MINIMUM → .70..1；MEETS_TARGET → 1。"""
    if req.is_unknown:
        return None
    from app.talent.fit.policy import POLICY

    if req.evaluation_status == EvaluationStatus.MEETS_TARGET:
        return 1.0
    minimum = req.minimum_score
    target = req.target_score
    score = req.employee_score
    if req.evaluation_status == EvaluationStatus.BELOW_MINIMUM:
        if minimum is None or minimum <= 0 or score is None:
            return 0.0
        return round(min(1.0, max(0.0, (score / minimum) * POLICY.below_ceiling)), 4)
    # MEETS_MINIMUM（score 在 [minimum, target)）
    if minimum is None or target is None or target <= minimum or score is None:
        return POLICY.meet_floor
    progress = (score - minimum) / (target - minimum)
    return round(POLICY.meet_floor + (1.0 - POLICY.meet_floor) * progress, 4)


def classify(req: RequirementEvaluation) -> None:
    """把一条评估归入 gap / strength / development / uncertainty。"""
    status = req.evaluation_status
    if status == EvaluationStatus.MEETS_TARGET:
        req.is_strength = True
        return
    if req.is_unknown:
        # Unknown != Bad：key 的 unrated/insufficient 是 uncertainty，不是 gap
        if status == EvaluationStatus.INSUFFICIENT_CONFIDENCE:
            if req.critical:
                req.gap_type = GapType.CRITICAL_UNCERTAINTY
            elif req.requirement_type == "required":
                req.gap_type = GapType.REQUIRED_UNCERTAINTY
            else:
                req.gap_type = GapType.UNRATED
        else:  # UNRATED
            if req.critical:
                req.gap_type = GapType.CRITICAL_UNCERTAINTY
            elif req.requirement_type == "required":
                req.gap_type = GapType.REQUIRED_UNCERTAINTY
            else:
                # preferred 未评估 = development opportunity（Not Assessed），不是 weakness
                req.is_development_opportunity = True
                req.gap_type = GapType.UNRATED
        return
    if status == EvaluationStatus.BELOW_MINIMUM:
        if req.critical:
            req.gap_type = GapType.CRITICAL_GAP
        elif req.requirement_type == "required":
            req.gap_type = GapType.REQUIRED_GAP
        else:
            req.gap_type = GapType.PREFERRED_GAP
        return
    # MEETS_MINIMUM（below target）
    if req.requirement_type == "required":
        req.gap_type = GapType.TARGET_GAP
    else:
        # preferred：development opportunity（不是 weakness）
        req.is_development_opportunity = True


# 供序列化/审计引用
def reason_code_of(status: str) -> str:
    return status
