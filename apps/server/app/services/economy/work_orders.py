"""WorkOrderService —— 官方工作市场（M1.3，设计 §17/§18/§20/§24）。

玩家通过"创造价值"赚新钱（**主要发行渠道**）：

```
系统发布（official_bounty / official_contract / research_grant / system_procurement）
   ↓ 预算内发行：单笔 ≤ official_max_reward，未结算承诺额 ≤ official_outstanding_budget
公司领取（OPEN → ACCEPTED）→ 开工（→ IN_PROGRESS）
   ↓ 提交（→ SUBMITTED → REVIEWING）
验收（auto 确定性规则 / manual 管理面）→ APPROVED / REJECTED（可重提）
   ↓
结算（SettlementService：MonetaryAuthority.mint → 承接公司）→ SETTLED
```

**纪律**：
- 状态迁移一律走 M1.0 冻结状态机（`assert_transition`），迁移落库走 CAS（并发安全）；
- **E8：玩家绝不 mint** —— 只有官方（`issuer=system` + `funding_mode=system_mint`）能发布官方订单；
  公司发布玩家订单（Escrow 锁资、绝不 mint）在 M1.4 落地，这里明确拒绝；
- 执行复用既有 `Project/Task/Artifact`（只存 `project_id` 引用，不重造项目系统）；
- 奖励发放只经 `SettlementService`，`reward_grants` 只落审计行（不再 mint）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.economy.contracts import EconomicActor, StateMachine, assert_transition
from app.economy.policy import EconomicPolicy, economic_policy
from app.events.bus import bus
from app.models.base import as_utc, utcnow
from app.models.economy import Evaluation, WorkOrder, WorkOrderSubmission
from app.models.enums import (
    Currency,
    EconomicActorKind,
    EvaluationMode,
    EvaluationVerdict,
    FundingMode,
    RewardType,
    WorkOrderKind,
    WorkOrderStatus,
)
from app.repositories import economy as economy_repo
from app.services.economy.evaluations import EvaluationError, EvaluationService, bonus_total
from app.services.economy.rewards import RewardService
from app.services.economy.settlement import SettlementRequest, SettlementService

logger = get_logger(__name__)

#: 官方发行类订单 ↔ 奖励类型（结算落 `reward_grants` 用；与 M1.2 的 OFFICIAL_KINDS 对齐）
OFFICIAL_REWARD_TYPES: dict[WorkOrderKind, RewardType] = {
    WorkOrderKind.official_bounty: RewardType.official_bounty,
    WorkOrderKind.official_contract: RewardType.official_contract,
    WorkOrderKind.research_grant: RewardType.research_grant,
    WorkOrderKind.system_procurement: RewardType.system_procurement,
}

#: 可领（open）的状态
ACCEPTABLE_STATUSES = (WorkOrderStatus.open.value,)
#: 占用预算的未结算状态（与 repo 的 `outstanding_official_commitment` 口径一致）
OPEN_COMMITMENT_STATUSES = (
    WorkOrderStatus.open.value,
    WorkOrderStatus.accepted.value,
    WorkOrderStatus.in_progress.value,
    WorkOrderStatus.submitted.value,
    WorkOrderStatus.reviewing.value,
    WorkOrderStatus.approved.value,
)


class WorkOrderError(RuntimeError):
    """工作订单领域错误（reason code 机器可读；API 层转 HTTP）。"""

    def __init__(self, reason: str, http_status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


@dataclass(frozen=True)
class PublishResult:
    order: WorkOrder
    created: bool


class WorkOrderService:
    """官方工作订单的发布 / 领取 / 提交 / 验收 / 结算。"""

    def __init__(self, db: Session, *, policy: EconomicPolicy | None = None) -> None:
        self.db = db
        self.policy = policy or economic_policy()
        self.evaluation_service = EvaluationService(db)

    # ---------------------------------------------------------------- 发布（官方）

    def publish_official(
        self,
        *,
        title: str,
        reward_amount: int,
        kind: WorkOrderKind = WorkOrderKind.official_bounty,
        code: str | None = None,
        description: str = "",
        requirements: dict | None = None,
        deliverables: dict | None = None,
        evaluation_mode: EvaluationMode = EvaluationMode.auto,
        deadline_at: datetime | None = None,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> PublishResult:
        """发布官方订单（**内部能力**：CLI/系统面调用，玩家 router 不可达）。

        预算内发行（§18）：金额按政策系数缩放后不得超过 `official_max_reward`，
        且"未结算官方承诺额 + 本次"不得超过 `official_outstanding_budget`。
        """
        if kind not in OFFICIAL_REWARD_TYPES:
            # 玩家类（player_bounty/player_contract/npc_contract）需要 Escrow 锁资（M1.4/M1.8）
            raise WorkOrderError("kind_not_available_yet", http_status=422)
        if self.policy.official_reward_multiplier <= 0:
            raise WorkOrderError("official_reward_multiplier_must_be_positive", http_status=422)

        effective = int(reward_amount * self.policy.official_reward_multiplier)
        if effective <= 0:
            raise WorkOrderError("reward_amount_must_be_positive", http_status=422)
        if effective > self.policy.official_max_reward:
            raise WorkOrderError("reward_exceeds_official_max_reward", http_status=422)

        outstanding = economy_repo.outstanding_official_commitment(self.db)
        if outstanding + effective > self.policy.official_outstanding_budget:
            raise WorkOrderError("official_budget_exhausted", http_status=409)

        resolved_code = code or self._next_code(kind)
        existing = economy_repo.find_work_order_by_code(self.db, resolved_code)
        if existing is not None:
            return PublishResult(order=existing, created=False)

        order = economy_repo.insert_work_order(
            self.db,
            code=resolved_code,
            kind=kind.value,
            title=title.strip(),
            description=description,
            requirements_json=dict(requirements or {}),
            deliverables_json=dict(deliverables or {}),
            reward_amount=effective,
            currency=Currency.credit.value,
            policy_version=self.policy.version,
            issuer_actor_kind=EconomicActorKind.system.value,
            issuer_actor_ref=0,
            funding_mode=FundingMode.system_mint.value,
            evaluation_mode=evaluation_mode.value,
            status=WorkOrderStatus.open.value,
            deadline_at=deadline_at,
            metadata_json=dict(metadata or {}),
        )
        if commit:
            self.db.commit()
            self._publish_event(
                "work_order.published",
                order,
                {"reward_amount": int(order.reward_amount), "kind": order.kind},
            )
        return PublishResult(order=order, created=True)

    def _next_code(self, kind: WorkOrderKind) -> str:
        prefix = {
            WorkOrderKind.official_bounty: "OB",
            WorkOrderKind.official_contract: "OC",
            WorkOrderKind.research_grant: "RG",
            WorkOrderKind.system_procurement: "SP",
        }.get(kind, "WO")
        return f"{prefix}-{self._next_sequence():05d}"

    # ---------------------------------------------------------------- 领取 / 提交

    def accept(self, order_id: int, *, company_id: int, commit: bool = True) -> WorkOrder:
        """公司领取（OPEN → ACCEPTED；并发下只有一个公司能领到）。"""
        order = self._require(order_id)
        deadline = as_utc(order.deadline_at)
        if deadline is not None and utcnow() > deadline:
            # 过期订单不能领：先把 EXPIRED 落库（过期是既成事实，不因这次领取失败而回滚），
            # 再报 409 —— 状态与事实一致，且不会永远卡在 OPEN。
            self.expire_overdue(commit=commit)
            raise WorkOrderError("order_expired")
        if order.assignee_actor_ref is not None and (
            order.assignee_actor_kind != EconomicActorKind.company.value
            or int(order.assignee_actor_ref) != int(company_id)
        ):
            raise WorkOrderError("order_already_taken")

        assert_transition(
            StateMachine.work_order, WorkOrderStatus.open.value, WorkOrderStatus.accepted.value
        )
        rowcount = economy_repo.transition_work_order(
            self.db,
            order_id=order_id,
            from_statuses=ACCEPTABLE_STATUSES,
            to_status=WorkOrderStatus.accepted.value,
            assignee_actor_kind=EconomicActorKind.company.value,
            assignee_actor_ref=int(company_id),
            accepted_at=utcnow(),
        )
        if rowcount == 0:
            self.db.rollback() if commit else self.db.expire_all()
            current = economy_repo.get_work_order(self.db, order_id)
            if current is not None and int(current.assignee_actor_ref or 0) == int(company_id):
                return current  # 幂等：同一公司重复领取返回既有状态
            raise WorkOrderError("order_already_taken")
        self.db.expire_all()
        accepted = self._require(order_id)
        if commit:
            self.db.commit()
            self._publish_event("work_order.accepted", accepted, {"company_id": int(company_id)})
        return accepted

    def submit(
        self,
        order_id: int,
        *,
        company_id: int,
        summary: str = "",
        deliverables: dict | None = None,
        artifact_refs: list | None = None,
        project_id: int | None = None,
        submitted_by_user_id: int | None = None,
        commit: bool = True,
    ) -> tuple[WorkOrderSubmission, WorkOrder, Evaluation | None]:
        """提交交付物（ACCEPTED → IN_PROGRESS → SUBMITTED → REVIEWING）。

        `evaluation_mode=auto` 时在**同一事务**内完成验收（确定性规则）：
        通过 ⇒ APPROVED（等待结算）；不通过 ⇒ REJECTED（可重提）。
        """
        order = self._require(order_id)
        if order.assignee_actor_kind != EconomicActorKind.company.value or int(
            order.assignee_actor_ref or 0
        ) != int(company_id):
            raise WorkOrderError("not_your_order", http_status=404)
        if not summary.strip() and not deliverables and not artifact_refs:
            raise WorkOrderError("empty_submission", http_status=422)

        status = order.status
        if status == WorkOrderStatus.accepted.value:
            # 领取即开工：先把 ACCEPTED 推进到 IN_PROGRESS（冻结状态机不允许跳步）
            assert_transition(
                StateMachine.work_order,
                WorkOrderStatus.accepted.value,
                WorkOrderStatus.in_progress.value,
            )
            moved = economy_repo.transition_work_order(
                self.db,
                order_id=order_id,
                from_statuses=(WorkOrderStatus.accepted.value,),
                to_status=WorkOrderStatus.in_progress.value,
            )
            if moved == 0:
                raise WorkOrderError("order_not_acceptable")
            self.db.expire_all()
            order = self._require(order_id)
            status = order.status

        revisable = (WorkOrderStatus.in_progress.value, WorkOrderStatus.rejected.value)
        if status == WorkOrderStatus.rejected.value:
            # 被拒后重提：先真正回到 IN_PROGRESS（冻结状态机不允许 REJECTED → SUBMITTED 跳步）
            assert_transition(
                StateMachine.work_order,
                WorkOrderStatus.rejected.value,
                WorkOrderStatus.in_progress.value,
            )
            if (
                economy_repo.transition_work_order(
                    self.db,
                    order_id=order_id,
                    from_statuses=(WorkOrderStatus.rejected.value,),
                    to_status=WorkOrderStatus.in_progress.value,
                )
                == 0
            ):
                raise WorkOrderError("order_not_acceptable")
            self.db.expire_all()
            order = self._require(order_id)
            status = order.status
        if status not in revisable:
            raise WorkOrderError(f"order_not_submittable:{order.status}")

        attempts = economy_repo.list_submissions(self.db, order_id=order_id)
        submission = economy_repo.insert_submission(
            self.db,
            order_id=order_id,
            company_id=int(company_id),
            attempt=len(attempts) + 1,
            summary=summary,
            deliverables_json=dict(deliverables or {}),
            artifact_refs=list(artifact_refs or []),
            project_id=project_id,
            submitted_by_user_id=submitted_by_user_id,
        )
        assert_transition(StateMachine.work_order, status, WorkOrderStatus.submitted.value)
        rowcount = economy_repo.transition_work_order(
            self.db,
            order_id=order_id,
            from_statuses=(status,),
            to_status=WorkOrderStatus.submitted.value,
            submitted_at=utcnow(),
            project_id=project_id,
        )
        if rowcount == 0:
            raise WorkOrderError("submit_race")
        self.db.expire_all()
        order = self._require(order_id)

        evaluation: Evaluation | None = None
        if EvaluationMode(order.evaluation_mode) is EvaluationMode.auto:
            order, evaluation = self._run_evaluation(order, submission, commit=False)
        if commit:
            self.db.commit()
            self._publish_event(
                "work_order.submitted",
                order,
                {"submission_id": int(submission.id), "attempt": int(submission.attempt)},
            )
        return submission, order, evaluation

    # ---------------------------------------------------------------- 验收

    def evaluate(
        self,
        order_id: int,
        *,
        verdict: EvaluationVerdict | None = None,
        score: int | None = None,
        bonuses: dict | None = None,
        criteria: dict | None = None,
        notes: str = "",
        mode: EvaluationMode | None = None,
        evaluated_by: tuple[str, int] = ("system", 0),
        commit: bool = True,
    ) -> tuple[WorkOrder, Evaluation]:
        """验收（SUBMITTED → REVIEWING → APPROVED / REJECTED）。

        - `evaluation_mode=auto` 且未给 verdict：用确定性规则判定；
        - `manual`：必须给 verdict（管理面/CLI）；
        - **只判定不改钱**：APPROVED 之后由 `settle()` 发钱（auto 模式下 `submit`/`evaluate`
          会自动衔接结算，见 `_run_evaluation`）。
        """
        order = self._require(order_id)
        # SUBMITTED = 首次验收；REVIEWING = 上一次验收在校验阶段失败后的重试
        # （失败不应把订单锁死；M1.0 状态机允许 reviewing → approved/rejected/disputed）
        if order.status not in (
            WorkOrderStatus.submitted.value,
            WorkOrderStatus.reviewing.value,
        ):
            raise WorkOrderError(f"order_not_reviewable:{order.status}")
        submission = economy_repo.latest_submission(self.db, order_id=order_id)
        if submission is None:  # pragma: no cover - SUBMITTED 必然有提交
            raise WorkOrderError("submission_missing")

        order, evaluation = self._run_evaluation(
            order,
            submission,
            verdict=verdict,
            score=score,
            bonuses=bonuses,
            criteria=criteria,
            notes=notes,
            mode=mode,
            evaluated_by=evaluated_by,
            commit=False,
        )
        if commit:
            self.db.commit()
            self._publish_event(
                "submission.approved"
                if evaluation.verdict == EvaluationVerdict.approved.value
                else "submission.rejected",
                order,
                {
                    "evaluation_id": int(evaluation.id),
                    "verdict": evaluation.verdict,
                    "score": evaluation.score,
                    "bonuses": dict(evaluation.bonuses_json or {}),
                },
            )
        return order, evaluation

    def _run_evaluation(
        self,
        order: WorkOrder,
        submission: WorkOrderSubmission,
        *,
        verdict: EvaluationVerdict | None = None,
        score: int | None = None,
        bonuses: dict | None = None,
        criteria: dict | None = None,
        notes: str = "",
        mode: EvaluationMode | None = None,
        evaluated_by: tuple[str, int] = ("system", 0),
        commit: bool,
    ) -> tuple[WorkOrder, Evaluation]:
        if order.status == WorkOrderStatus.submitted.value:
            assert_transition(
                StateMachine.work_order,
                WorkOrderStatus.submitted.value,
                WorkOrderStatus.reviewing.value,
            )
            if (
                economy_repo.transition_work_order(
                    self.db,
                    order_id=int(order.id),
                    from_statuses=(WorkOrderStatus.submitted.value,),
                    to_status=WorkOrderStatus.reviewing.value,
                )
                == 0
            ):
                raise WorkOrderError("evaluation_race")
            self.db.expire_all()
            order = self._require(int(order.id))
        elif order.status != WorkOrderStatus.reviewing.value:
            raise WorkOrderError(f"order_not_reviewable:{order.status}")

        outcome = self.evaluation_service.evaluate(
            order,
            submission,
            mode=mode,
            verdict=verdict,
            score=score,
            bonuses=bonuses,
            criteria=criteria,
            notes=notes,
            evaluated_by=evaluated_by,
        )
        approved = outcome.verdict is EvaluationVerdict.approved
        assert_transition(
            StateMachine.work_order,
            WorkOrderStatus.reviewing.value,
            (WorkOrderStatus.approved if approved else WorkOrderStatus.rejected).value,
        )
        target = WorkOrderStatus.approved.value if approved else WorkOrderStatus.rejected.value
        if (
            economy_repo.transition_work_order(
                self.db,
                order_id=int(order.id),
                from_statuses=(WorkOrderStatus.reviewing.value,),
                to_status=target,
                completed_at=utcnow() if approved else None,
            )
            == 0
        ):
            raise WorkOrderError("evaluation_race")
        self.db.expire_all()
        order = self._require(int(order.id))
        if approved:
            # 官方订单：验收通过即结算（同一事务；结算幂等由 settlement_key 保证）
            order = self.settle(int(order.id), commit=False)
        return order, outcome.evaluation

    # ---------------------------------------------------------------- 结算

    def settle(self, order_id: int, *, commit: bool = True) -> WorkOrder:
        """结算（APPROVED → SETTLED）：把官方奖励 mint 给承接公司。**幂等。**

        幂等锚点 `settlement_key = work_order:<id>`；重复调用返回既有结算
        （`reward_grants` 与订单状态都不再变化）。
        """
        order = self._require(order_id)
        if order.status == WorkOrderStatus.settled.value:
            return order  # 幂等重放
        if order.status != WorkOrderStatus.approved.value:
            raise WorkOrderError(f"order_not_settleable:{order.status}")
        if order.assignee_actor_kind != EconomicActorKind.company.value:
            raise WorkOrderError("order_has_no_company_assignee")
        company_id = int(order.assignee_actor_ref or 0)
        amount = self.evaluation_service.reward_amount_for(order)
        if amount <= 0:  # pragma: no cover - APPROVED 必然有通过验收
            raise WorkOrderError("reward_amount_not_resolved")

        reward_type = OFFICIAL_REWARD_TYPES.get(WorkOrderKind(order.kind))
        if reward_type is None:  # pragma: no cover - publish 已限制 kind
            raise WorkOrderError("kind_not_settleable")

        settlement_key = f"work_order:{int(order.id)}"
        settlement = SettlementService(self.db).settle(
            SettlementRequest(
                settlement_key=settlement_key,
                amount=amount,
                beneficiary=EconomicActor.company(company_id),
                reason=f"work_order:{order.code}",
                reference_type="work_order",
                reference_id=str(int(order.id)),
                metadata={
                    "work_order_code": order.code,
                    "kind": order.kind,
                    "policy_version": order.policy_version,
                },
            ),
            commit=False,
        )
        # 奖励审计行（官方类；不再 mint —— 钱由 SettlementService 发）
        RewardService(self.db, policy=self.policy).record_external_grant(
            reward_type=reward_type,
            actor=EconomicActor.company(company_id),
            company_id=company_id,
            amount=amount,
            reference_key=settlement_key,
            ledger_transaction_id=int(settlement.transaction.id),
            reason=f"work_order:{order.code}",
            metadata={
                "work_order_id": int(order.id),
                "work_order_code": order.code,
                "evaluation_id": None,
            },
            commit=False,
        )

        assert_transition(
            StateMachine.work_order, WorkOrderStatus.approved.value, WorkOrderStatus.settled.value
        )
        if (
            economy_repo.transition_work_order(
                self.db,
                order_id=order_id,
                from_statuses=(WorkOrderStatus.approved.value,),
                to_status=WorkOrderStatus.settled.value,
                settlement_transaction_id=int(settlement.transaction.id),
                settled_at=utcnow(),
            )
            == 0
        ):
            # 并发结算：另一路已经推进到 SETTLED ⇒ 回滚本路（钱那一侧同 key 幂等，不会双发）
            self.db.rollback()
            current = self._require(order_id)
            if current.status == WorkOrderStatus.settled.value:
                return current
            raise WorkOrderError("settlement_race")
        self.db.expire_all()
        settled = self._require(order_id)
        if commit:
            self.db.commit()
            self._publish_event(
                "settlement.completed",
                settled,
                {
                    "settlement_key": settlement_key,
                    "amount": amount,
                    "transaction_id": int(settlement.transaction.id),
                    "created": settlement.created,
                },
            )
        return settled

    # ---------------------------------------------------------------- 过期

    def expire_overdue(self, *, now: datetime | None = None, commit: bool = True) -> int:
        """把过了 deadline 的未终态订单推进到 EXPIRED（幂等扫描，供 CLI/定时任务调用）。"""
        moment = now or utcnow()
        expired = 0
        for order in economy_repo.list_work_orders(self.db, statuses=OPEN_COMMITMENT_STATUSES):
            deadline = as_utc(order.deadline_at)
            if deadline is None or deadline >= moment:
                continue
            target = (
                WorkOrderStatus.expired.value
                if order.status in (WorkOrderStatus.open.value, WorkOrderStatus.accepted.value)
                else WorkOrderStatus.cancelled.value
            )
            assert_transition(StateMachine.work_order, order.status, target)
            if (
                economy_repo.transition_work_order(
                    self.db,
                    order_id=int(order.id),
                    from_statuses=(order.status,),
                    to_status=target,
                )
                > 0
            ):
                expired += 1
        if commit and expired:
            self.db.commit()
        return expired

    # ---------------------------------------------------------------- 读面

    def detail(self, order_id: int) -> WorkOrder:
        return self._require(order_id)

    def submissions(self, order_id: int) -> list[WorkOrderSubmission]:
        return economy_repo.list_submissions(self.db, order_id=order_id)

    def evaluations(self, order_id: int) -> list[Evaluation]:
        return economy_repo.list_evaluations(self.db, order_id=order_id)

    def payable_amount(self, order: WorkOrder) -> int:
        """当前应付金额（不含未通过验收的 bonus）。"""
        return self.evaluation_service.reward_amount_for(order)

    # ---------------------------------------------------------------- 内部

    def _require(self, order_id: int) -> WorkOrder:
        order = economy_repo.get_work_order(self.db, order_id)
        if order is None:
            raise WorkOrderError("order_not_found", http_status=404)
        return order

    def _next_sequence(self) -> int:
        """订单编码序号（`OB-00001`）：取当前最大 id + 1；重复时 publish 会回落到既有订单。"""
        from sqlalchemy import func, select

        return int(self.db.execute(select(func.max(WorkOrder.id))).scalar_one() or 0) + 1

    def _publish_event(self, event_type: str, order: WorkOrder, extra: dict) -> None:
        bus.publish(
            event_type,
            {
                "work_order_id": int(order.id),
                "code": order.code,
                "kind": order.kind,
                "status": order.status,
                "reward_amount": int(order.reward_amount),
                "assignee_company_id": int(order.assignee_actor_ref or 0) or None,
                **extra,
            },
            company_id=(
                int(order.assignee_actor_ref)
                if order.assignee_actor_kind == EconomicActorKind.company.value
                and order.assignee_actor_ref
                else None
            ),
        )


def official_bonus_total(bonuses: dict | None) -> int:
    return bonus_total(bonuses)


__all__ = [
    "ACCEPTABLE_STATUSES",
    "OFFICIAL_REWARD_TYPES",
    "OPEN_COMMITMENT_STATUSES",
    "EvaluationError",
    "PublishResult",
    "WorkOrderError",
    "WorkOrderService",
    "official_bonus_total",
]
