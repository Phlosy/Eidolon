"""EvaluationService —— 工作订单验收（M1.3，设计 §20）。

`Evaluation` 只记录**判定**（mode / criteria / score / verdict / bonuses），
**绝不改钱**：奖励发放一律走 `SettlementService` → `MonetaryAuthority` → `LedgerService.post()`。

- `auto` 模式：用**确定性、可复现**的规则判定（提交非空 + 交付物非空 + 交付期限内），
  不引入"AI 打分"这种不可复现的判定（那会让结算变成掷骰子）；
- `manual` 模式：由运维/管理面（CLI）给出 verdict/score/bonuses；
- 奖励 = `base + Σbonus`（设计 §20）；bonus 必须是非负整数（禁止负 bonus 变相扣钱）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.economy.contracts import EconomyContractError, validate_amount
from app.models.base import as_utc, utcnow
from app.models.economy import Evaluation, WorkOrder, WorkOrderSubmission
from app.models.enums import EvaluationMode, EvaluationVerdict
from app.repositories import economy as economy_repo


class EvaluationError(RuntimeError):
    """验收领域错误（reason code 机器可读）。"""

    def __init__(self, reason: str, http_status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


@dataclass(frozen=True)
class EvaluationOutcome:
    """一次验收的判定结果 + 由此得出的奖励金额。"""

    evaluation: Evaluation
    verdict: EvaluationVerdict
    reward_amount: int
    base_amount: int
    bonuses: dict = field(default_factory=dict)


def normalize_bonuses(bonuses: dict | None) -> dict[str, int]:
    """校验 bonus（非负整数；名字非空）—— 负 bonus 会让"奖励"变成扣钱。"""
    normalized: dict[str, int] = {}
    for name, value in (bonuses or {}).items():
        if not str(name).strip():
            raise EvaluationError("bonus_name_required", http_status=422)
        # 显式拒绝 float/bool/str：`int(1.5)` 会静默截断成 1 —— 金额不允许"差不多"
        if isinstance(value, bool) or not isinstance(value, int):
            raise EvaluationError("bonus_must_be_positive_int", http_status=422)
        try:
            validate_amount(value)
        except EconomyContractError as exc:
            raise EvaluationError("bonus_must_be_positive_int", http_status=422) from exc
        normalized[str(name)] = int(value)
    return normalized


def bonus_total(bonuses: dict | None) -> int:
    return sum(normalize_bonuses(bonuses).values())


class EvaluationService:
    """验收判定 + 验收记录（单一入口）。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def auto_verdict(
        self, order: WorkOrder, submission: WorkOrderSubmission
    ) -> tuple[EvaluationVerdict, dict, str, dict]:
        """确定性自动判定：交付物非空且满足必交项 ⇒ approved，否则 rejected。

        返回 `(verdict, bonuses, notes, criteria_extra)` —— **auto 模式不给 bonus**
        （bonus 是人工验收的语义，如 score≥90 / 提前交付；见 §20）；
        缺件等诊断信息进 `criteria`，绝不混进 bonuses（那会被当成金额解析）。
        """
        required = list((order.deliverables_json or {}).get("required_keys") or [])
        delivered = dict(submission.deliverables_json or {})
        missing = [key for key in required if not str(delivered.get(key, "")).strip()]
        if not submission.summary.strip() and not delivered and not submission.artifact_refs:
            return EvaluationVerdict.rejected, {}, "empty_submission", {}
        if missing:
            return (
                EvaluationVerdict.rejected,
                {},
                "missing_required_deliverables",
                {"missing_required": missing},
            )
        deadline = as_utc(order.deadline_at)
        submitted_at = as_utc(submission.created_at)
        if deadline is not None and submitted_at is not None and submitted_at > deadline:
            return EvaluationVerdict.approved, {}, "late_submission", {"late": True}
        return EvaluationVerdict.approved, {}, "auto_approved", {}

    def evaluate(
        self,
        order: WorkOrder,
        submission: WorkOrderSubmission,
        *,
        mode: EvaluationMode | None = None,
        verdict: EvaluationVerdict | None = None,
        score: int | None = None,
        bonuses: dict | None = None,
        criteria: dict | None = None,
        notes: str = "",
        evaluated_by: tuple[str, int] = ("system", 0),
    ) -> EvaluationOutcome:
        """记录一次验收并算出奖励金额（`base + Σbonus`）。**不动钱。**"""
        resolved_mode = mode or EvaluationMode(order.evaluation_mode)
        auto_criteria: dict = {}
        if resolved_mode is EvaluationMode.auto and verdict is None:
            verdict, _auto_bonuses, auto_notes, auto_criteria = self.auto_verdict(order, submission)
            notes = notes or auto_notes
        if verdict is None:
            raise EvaluationError("verdict_required", http_status=422)
        if score is not None and not 0 <= int(score) <= 100:
            raise EvaluationError("score_out_of_range", http_status=422)

        normalized = normalize_bonuses(bonuses)
        base = int(order.reward_amount)
        reward = base
        if verdict is EvaluationVerdict.approved:
            reward = base + bonus_total(normalized)
        else:
            # 未通过 ⇒ 本次不发钱（拒绝不是"部分付款"；重提通过后照常结算）
            normalized = {}

        evaluation = economy_repo.insert_evaluation(
            self.db,
            order_id=int(order.id),
            submission_id=int(submission.id),
            mode=resolved_mode.value,
            criteria_json={
                **dict(order.deliverables_json or {}),
                **(criteria or {}),
                **auto_criteria,
            },
            score=int(score) if score is not None else None,
            verdict=verdict.value,
            bonuses_json=normalized,
            evaluated_by_actor_kind=evaluated_by[0],
            evaluated_by_actor_ref=int(evaluated_by[1]),
            notes=notes,
        )
        return EvaluationOutcome(
            evaluation=evaluation,
            verdict=verdict,
            reward_amount=reward,
            base_amount=base,
            bonuses=normalized,
        )

    def reward_amount_for(self, order: WorkOrder) -> int:
        """已通过验收的应付金额（base + 最后一次验收的 bonuses）；没有验收记录 ⇒ 0。"""
        evaluation = economy_repo.latest_evaluation(self.db, order_id=int(order.id))
        if evaluation is None or evaluation.verdict != EvaluationVerdict.approved.value:
            return 0
        return int(order.reward_amount) + bonus_total(evaluation.bonuses_json)


def evaluation_is_fresh(evaluation: Evaluation | None, *, within_seconds: int = 0) -> bool:
    """验收记录是否"新鲜"（避免用旧验收给新提交结算）。"""
    if evaluation is None:
        return False
    if within_seconds <= 0:
        return True
    age = (utcnow() - evaluation.created_at).total_seconds()
    return age <= within_seconds
