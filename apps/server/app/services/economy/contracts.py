"""ContractService / OfferService —— 通用商业合同（M1.6，设计 §21/§22/§24）。

```
POST /contracts（发布方）         → PENDING_ACCEPTANCE + **对价锁进托管**（E11 的合同形态）
POST /contracts/{id}/accept（承接方）→ ACTIVE → FUNDED（资金已就位，开工授权）
POST /contracts/{id}/fulfill（承接方）→ FULFILLED → SETTLING → SETTLED（放款 + 手续费拆分）
POST /contracts/{id}/cancel          → CANCELLED + 退款（仅未开工/未验收）
POST /contracts/{id}/fail（承接方/内部）→ FAILED → settle(refund) 退款给出资人
过期扫描 expire_overdue()            → EXPIRED + 退款
```

**纪律**：
- 状态迁移一律走 M1.0 冻结状态机（`assert_transition`）+ CAS 条件更新（并发只有一个赢家）；
- **一条合同一个托管**（`escrows.contract_id` 权威指针，部分唯一索引兜底）；
- 资金终局只经 `SettlementService`（§24）：放款 = 受益方净额 + Treasury/Burn 手续费三腿（§7 Sink），
  退款 = 单腿回出资人；**绝不 mint**（玩家之间的钱不是发行，E8）；
- 手续费**从对价里扣**（合同手续费 `contract_fee_bps`）：受益方拿净额，不需要额外余额，
  结算不会因为"谁没钱"而失败；
- 幂等：`settlement_key = contract:<id>`；重复 fulfill/settle 复用既有终局（E12）。

`OfferService`（§22）：出价/申请（人才出价、合同申请、报价共用）—— **Offer 本身不产生资金流**，
被接受后生成 Contract（`contract_id` 回填）；状态 `OPEN → ACCEPTED|REJECTED|WITHDRAWN|EXPIRED`。
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
from app.models.economy import Contract, Offer
from app.models.enums import (
    ContractStatus,
    ContractType,
    Currency,
    EconomicActorKind,
    FundingMode,
    OfferStatus,
)
from app.repositories import economy as economy_repo
from app.services.economy.costs import FeeService
from app.services.economy.escrow import EscrowError, EscrowService
from app.services.economy.settlement import SettlementRequest, SettlementService

logger = get_logger(__name__)

#: 取消/退款可用的状态（开工前后、尚未交付）：funded 之后只能走 fulfill / fail
CANCELLABLE_STATUSES = (
    ContractStatus.draft.value,
    ContractStatus.pending_acceptance.value,
    ContractStatus.active.value,
)

#: 可结算的状态（通过验收，或判定失败后按退款结算）
SETTLEABLE_STATUSES = (
    ContractStatus.fulfilled.value,
    ContractStatus.failed.value,
    ContractStatus.disputed.value,
)


class ContractError(RuntimeError):
    """合同领域错误（reason code 机器可读）。"""

    def __init__(self, reason: str, http_status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


@dataclass(frozen=True)
class ContractSettlement:
    """一次合同结算的结果（金额、三腿拆分、交易）。"""

    contract_id: int
    gross: int
    fee: int
    net: int
    fee_treasury: int
    fee_burn: int
    refund: bool
    transaction_id: int
    created: bool


class ContractService:
    """合同生命周期（创建/接受/交付/取消/失败/结算/过期）。"""

    def __init__(self, db: Session, *, policy: EconomicPolicy | None = None) -> None:
        self.db = db
        self.policy = policy or economic_policy()

    # ---------------------------------------------------------------- 创建

    def create_contract(
        self,
        *,
        issuer: EconomicActor,
        title: str,
        consideration_amount: int,
        contract_type: ContractType = ContractType.work,
        contractor: EconomicActor | None = None,
        subject: str = "",
        terms: dict | None = None,
        reference_type: str = "",
        reference_id: str = "",
        expires_at: datetime | None = None,
        code: str | None = None,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> Contract:
        """创建合同并**锁资对价**（E11：没有资金就没有合同）。

        - 出资方必须是公司/用人单位（v1 玩家面是公司）；
        - 托管金额 = 对价，进入该合同自己的托管账户；余额不足 ⇒ 整笔回滚（不留合同）；
        - 有承接方 ⇒ `PENDING_ACCEPTANCE`（等对方接受）；无承接方 ⇒ `DRAFT`（待定）。
        """
        if issuer.kind is not EconomicActorKind.company:
            raise ContractError("issuer_must_be_company", http_status=422)
        if int(consideration_amount) <= 0:
            raise ContractError("consideration_must_be_positive", http_status=422)
        if contractor is not None and contractor.kind is EconomicActorKind.system:
            raise ContractError("contractor_must_be_user_actor", http_status=422)

        resolved_code = code or self._next_code(contract_type)
        existing = economy_repo.find_contract_by_code(self.db, resolved_code)
        if existing is not None:
            return existing

        issuer_kind, issuer_ref = economy_repo.actor_columns(issuer)
        contractor_kind, contractor_ref = (
            economy_repo.actor_columns(contractor) if contractor is not None else (None, None)
        )
        status = (
            ContractStatus.pending_acceptance if contractor is not None else ContractStatus.draft
        )
        contract = economy_repo.insert_contract(
            self.db,
            code=resolved_code,
            contract_type=contract_type.value,
            title=title.strip(),
            subject=subject,
            terms_json=dict(terms or {}),
            consideration_amount=int(consideration_amount),
            currency=Currency.credit.value,
            policy_version=self.policy.version,
            issuer_actor_kind=issuer_kind,
            issuer_actor_ref=issuer_ref,
            contractor_actor_kind=contractor_kind,
            contractor_actor_ref=contractor_ref,
            status=status.value,
            reference_type=reference_type,
            reference_id=reference_id,
            expires_at=expires_at,
            metadata_json=dict(metadata or {}),
        )
        try:
            EscrowService(self.db).fund_for_contract(
                contract_id=int(contract.id),
                payer=issuer,
                amount=int(consideration_amount),
                expires_at=expires_at,
                metadata={"contract_type": contract_type.value, "contract_code": resolved_code},
                commit=False,
            )
        except Exception:
            # 锁资失败（余额不足/账户冻结）⇒ 整笔回滚：不留下没钱的合同（E11）
            if commit:
                self.db.rollback()
            raise
        if commit:
            self.db.commit()
            self._publish(
                "contract.created", contract, {"gross": int(contract.consideration_amount)}
            )
        return contract

    # ---------------------------------------------------------------- 接受 / 开工

    def accept(
        self, contract_id: int, *, contractor: EconomicActor, commit: bool = True
    ) -> Contract:
        """承接方接受合同：`PENDING_ACCEPTANCE → ACTIVE → FUNDED`（资金已托管，开工授权）。

        两步迁移都走冻结状态机（不允许跳步）；重复接受幂等；非指定承接方 404。
        """
        contract = self._require(contract_id)
        if contract.status in (ContractStatus.active.value, ContractStatus.funded.value):
            return contract  # 幂等重放
        if contract.status != ContractStatus.pending_acceptance.value:
            raise ContractError(f"contract_not_acceptable:{contract.status}")
        expected_kind = contract.contractor_actor_kind
        expected_ref = contract.contractor_actor_ref
        kind, ref = economy_repo.actor_columns(contractor)
        if expected_kind is not None and (expected_kind, expected_ref) != (kind, ref):
            raise ContractError("not_the_named_contractor", http_status=404)

        assert_transition(
            StateMachine.contract,
            ContractStatus.pending_acceptance.value,
            ContractStatus.active.value,
        )
        if (
            economy_repo.transition_contract(
                self.db,
                contract_id=contract_id,
                from_statuses=(ContractStatus.pending_acceptance.value,),
                to_status=ContractStatus.active,
                contractor_actor_kind=kind,
                contractor_actor_ref=ref,
                effective_at=utcnow(),
            )
            == 0
        ):
            self.db.expire_all()
            current = self._require(contract_id)
            if current.status in (ContractStatus.active.value, ContractStatus.funded.value):
                return current
            raise ContractError(f"contract_not_acceptable:{current.status}")

        # 接受即生效：资金在创建时已托管 ⇒ ACTIVE → FUNDED（冻结状态机里的下一步）
        self._ensure_funded(contract_id)
        self.db.expire_all()
        accepted = self._require(contract_id)
        if commit:
            self.db.commit()
            self._publish("contract.accepted", accepted, {"contractor": kind})
        return accepted

    # ---------------------------------------------------------------- 交付 / 结算

    def fulfill(
        self, contract_id: int, *, contractor: EconomicActor | None = None, commit: bool = True
    ) -> tuple[Contract, ContractSettlement]:
        """承接方交付：`FUNDED → FULFILLED → SETTLING → SETTLED`（同事务放款）。

        结算 = 受益方净额 + Treasury/Burn 手续费（从对价里扣，§7）；重复调用幂等。
        """
        contract = self._require(contract_id)
        if contract.status == ContractStatus.settled.value:
            return contract, self._existing_settlement(contract)
        if contractor is not None:
            kind, ref = economy_repo.actor_columns(contractor)
            if (contract.contractor_actor_kind, contract.contractor_actor_ref) != (kind, ref):
                raise ContractError("not_the_contractor", http_status=404)
        if contract.status != ContractStatus.funded.value:
            raise ContractError(f"contract_not_fulfillable:{contract.status}")

        assert_transition(
            StateMachine.contract, ContractStatus.funded.value, ContractStatus.fulfilled.value
        )
        if (
            economy_repo.transition_contract(
                self.db,
                contract_id=contract_id,
                from_statuses=(ContractStatus.funded.value,),
                to_status=ContractStatus.fulfilled,
                fulfilled_at=utcnow(),
            )
            == 0
        ):
            self.db.expire_all()
            current = self._require(contract_id)
            if current.status == ContractStatus.settled.value:
                return current, self._existing_settlement(current)
            raise ContractError(f"contract_not_fulfillable:{current.status}")

        self.db.expire_all()
        contract = self._require(contract_id)
        contract, settlement = self.settle(contract_id, commit=False)
        if commit:
            self.db.commit()
            self._publish(
                "contract.fulfilled",
                contract,
                {"gross": settlement.gross, "net": settlement.net, "fee": settlement.fee},
            )
        return contract, settlement

    def settle(self, contract_id: int, *, refund: bool = False, commit: bool = True):
        """终局结算（**幂等**）：放款（含手续费拆分）或退款。

        - 放款：`FULFILLED → SETTLING → SETTLED`，`settlement_key = contract:<id>`；
        - 退款：`FAILED/DISPUTED → SETTLING → SETTLED`（钱回出资人，不抽手续费）；
        - 重复调用返回既有结果（`created=False`），绝不重复放款（E12）。
        """
        contract = self._require(contract_id)
        if contract.status == ContractStatus.settled.value:
            return contract, self._existing_settlement(contract)
        if contract.status not in SETTLEABLE_STATUSES:
            raise ContractError(f"contract_not_settleable:{contract.status}")

        assert_transition(StateMachine.contract, contract.status, ContractStatus.settling.value)
        if (
            economy_repo.transition_contract(
                self.db,
                contract_id=contract_id,
                from_statuses=(contract.status,),
                to_status=ContractStatus.settling,
            )
            == 0
        ):
            self.db.expire_all()
            current = self._require(contract_id)
            if current.status == ContractStatus.settled.value:
                return current, self._existing_settlement(current)
            raise ContractError(f"contract_not_settleable:{current.status}")

        escrow = EscrowService(self.db).get_for_contract(contract_id)
        if escrow is None:  # pragma: no cover - 创建时必锁资
            raise ContractError("escrow_missing_for_contract")

        contractor = (
            EconomicActor(
                kind=EconomicActorKind(contract.contractor_actor_kind),
                ref=int(contract.contractor_actor_ref or 0),
            )
            if contract.contractor_actor_kind and contract.contractor_actor_ref
            else None
        )
        gross = int(contract.consideration_amount)
        if not refund and contractor is None:
            raise ContractError("contract_without_contractor_cannot_release")

        quote = (
            FeeService(self.db, policy=self.policy).contract_quote(gross) if not refund else None
        )
        beneficiary = contractor if contractor is not None else self._issuer(contract)
        settlement_key = f"contract:{int(contract.id)}"
        settlement = SettlementService(self.db).settle(
            SettlementRequest(
                settlement_key=settlement_key,
                amount=gross,
                beneficiary=beneficiary,
                reason=f"contract:{contract.code}",
                reference_type="contract",
                reference_id=str(int(contract.id)),
                funding_mode=FundingMode.player_escrow,
                escrow_id=int(escrow.id),
                refund=refund,
                fee_treasury=quote.treasury if quote is not None else 0,
                fee_burn=quote.burn if quote is not None else 0,
                metadata={
                    "contract_code": contract.code,
                    "contract_type": contract.contract_type,
                    "policy_version": contract.policy_version,
                },
            ),
            commit=False,
        )

        assert_transition(
            StateMachine.contract, ContractStatus.settling.value, ContractStatus.settled.value
        )
        if (
            economy_repo.transition_contract(
                self.db,
                contract_id=contract_id,
                from_statuses=(ContractStatus.settling.value,),
                to_status=ContractStatus.settled,
                settled_at=utcnow(),
                settlement_transaction_id=int(settlement.transaction.id),
                metadata_json={
                    **(contract.metadata_json or {}),
                    "settlement": {
                        "refund": bool(refund),
                        "gross": gross,
                        "fee": quote.fee if quote is not None else 0,
                        "net": (gross - quote.fee) if quote is not None else gross,
                        "treasury": quote.treasury if quote is not None else 0,
                        "burn": quote.burn if quote is not None else 0,
                        "transaction_id": int(settlement.transaction.id),
                    },
                },
            )
            == 0
        ):
            self.db.rollback()
            current = self._require(contract_id)
            if current.status == ContractStatus.settled.value:
                return current, self._existing_settlement(current)
            raise ContractError(f"contract_not_settleable:{current.status}")

        self.db.expire_all()
        settled = self._require(contract_id)
        outcome = ContractSettlement(
            contract_id=int(settled.id),
            gross=gross,
            fee=quote.fee if quote is not None else 0,
            net=(gross - quote.fee) if quote is not None else gross,
            fee_treasury=quote.treasury if quote is not None else 0,
            fee_burn=quote.burn if quote is not None else 0,
            refund=bool(refund),
            transaction_id=int(settlement.transaction.id),
            created=bool(settlement.created),
        )
        if commit:
            self.db.commit()
            self._publish(
                "settlement.completed",
                settled,
                {
                    "settlement_key": settlement_key,
                    "amount": settlement.gross if hasattr(settlement, "gross") else gross,
                    "refund": bool(refund),
                    "transaction_id": int(settlement.transaction.id),
                    "created": bool(settlement.created),
                },
            )
        return settled, outcome

    # ---------------------------------------------------------------- 取消 / 失败 / 过期

    def cancel(
        self,
        contract_id: int,
        *,
        actor: EconomicActor,
        reason: str = "cancelled",
        commit: bool = True,
    ) -> Contract:
        """取消（仅发布方或承接方；**funded 之前**）：`→ CANCELLED` + 退款给出资人。"""
        contract = self._require(contract_id)
        if contract.status == ContractStatus.cancelled.value:
            return contract  # 幂等重放
        kind, ref = economy_repo.actor_columns(actor)
        parties = {
            (contract.issuer_actor_kind, int(contract.issuer_actor_ref)),
            (contract.contractor_actor_kind, int(contract.contractor_actor_ref or 0)),
        }
        if (kind, ref) not in parties:
            raise ContractError("not_a_contract_party", http_status=404)
        if contract.status not in CANCELLABLE_STATUSES:
            raise ContractError(f"contract_not_cancellable:{contract.status}")

        assert_transition(StateMachine.contract, contract.status, ContractStatus.cancelled.value)
        if (
            economy_repo.transition_contract(
                self.db,
                contract_id=contract_id,
                from_statuses=(contract.status,),
                to_status=ContractStatus.cancelled,
                metadata_json={**(contract.metadata_json or {}), "cancel_reason": reason},
            )
            == 0
        ):
            self.db.expire_all()
            current = self._require(contract_id)
            if current.status == ContractStatus.cancelled.value:
                return current
            raise ContractError(f"contract_not_cancellable:{current.status}")

        settlement = self._refund_escrow(contract, settlement_key=f"contract:{contract_id}:refund")
        self.db.expire_all()
        cancelled = self._require(contract_id)
        if commit:
            self.db.commit()
            self._publish(
                "contract.cancelled",
                cancelled,
                {"reason": reason, "refund_transaction_id": settlement},
            )
        return cancelled

    def fail(
        self,
        contract_id: int,
        *,
        actor: EconomicActor | None = None,
        reason: str = "failed",
        commit: bool = True,
    ) -> Contract:
        """判定失败（承接方/内部）：`FUNDED → FAILED`，随后由 `settle(refund=True)` 退款。"""
        contract = self._require(contract_id)
        if actor is not None:
            kind, ref = economy_repo.actor_columns(actor)
            if (contract.contractor_actor_kind, contract.contractor_actor_ref) != (kind, ref):
                raise ContractError("not_the_contractor", http_status=404)
        if contract.status == ContractStatus.failed.value:
            return contract
        if contract.status != ContractStatus.funded.value:
            raise ContractError(f"contract_not_failable:{contract.status}")

        assert_transition(
            StateMachine.contract, ContractStatus.funded.value, ContractStatus.failed.value
        )
        if (
            economy_repo.transition_contract(
                self.db,
                contract_id=contract_id,
                from_statuses=(ContractStatus.funded.value,),
                to_status=ContractStatus.failed,
                metadata_json={**(contract.metadata_json or {}), "fail_reason": reason},
            )
            == 0
        ):
            self.db.expire_all()
            current = self._require(contract_id)
            if current.status == ContractStatus.failed.value:
                return current
            raise ContractError(f"contract_not_failable:{current.status}")
        self.db.expire_all()
        failed = self._require(contract_id)
        if commit:
            self.db.commit()
            self._publish("contract.failed", failed, {"reason": reason})
        return failed

    def cancel_and_refund(
        self,
        contract_id: int,
        *,
        actor: EconomicActor,
        reason: str = "cancelled",
        commit: bool = True,
    ) -> Contract:
        """取消 + 退款（API 的 `cancel` 入口；与 `cancel()` 同义，语义更直白）。"""
        return self.cancel(contract_id, actor=actor, reason=reason, commit=commit)

    def settle_failed_with_refund(
        self, contract_id: int, *, commit: bool = True
    ) -> tuple[Contract, ContractSettlement]:
        """失败后的终局：退款（`FAILED → SETTLING → SETTLED`，不抽手续费）。"""
        return self.settle(contract_id, refund=True, commit=commit)

    def expire_overdue(self, *, now: datetime | None = None, commit: bool = True) -> int:
        """过 `expires_at` 且未开工/未验收的合同 → `EXPIRED` + 退款。"""
        moment = now or utcnow()
        expired = 0
        for contract in economy_repo.list_contracts(
            self.db, statuses=(ContractStatus.pending_acceptance.value, ContractStatus.active.value)
        ):
            expires_at = as_utc(contract.expires_at)
            if expires_at is None or expires_at >= moment:
                continue
            assert_transition(StateMachine.contract, contract.status, ContractStatus.expired.value)
            if (
                economy_repo.transition_contract(
                    self.db,
                    contract_id=int(contract.id),
                    from_statuses=(contract.status,),
                    to_status=ContractStatus.expired,
                )
                > 0
            ):
                self._refund_escrow(contract, settlement_key=f"contract:{contract.id}:expire")
                expired += 1
        if commit and expired:
            self.db.commit()
        return expired

    # ---------------------------------------------------------------- 读面

    def detail(self, contract_id: int) -> Contract:
        return self._require(contract_id)

    def escrow_view(self, contract_id: int):
        return EscrowService(self.db).get_for_contract(contract_id)

    # ---------------------------------------------------------------- 内部

    def _ensure_funded(self, contract_id: int) -> None:
        """`ACTIVE → FUNDED`（资金在创建时已托管；这里只是把状态推进到"可开工"）。"""
        contract = self._require(contract_id)
        if contract.status == ContractStatus.funded.value:
            return
        if contract.status != ContractStatus.active.value:  # pragma: no cover - 调用点保证
            raise ContractError(f"contract_not_fundable:{contract.status}")
        assert_transition(
            StateMachine.contract, ContractStatus.active.value, ContractStatus.funded.value
        )
        if (
            economy_repo.transition_contract(
                self.db,
                contract_id=contract_id,
                from_statuses=(ContractStatus.active.value,),
                to_status=ContractStatus.funded,
            )
            == 0
        ):  # pragma: no cover - CAS 竞态由调用方兜底
            raise ContractError("contract_funding_race")

    def _refund_escrow(self, contract: Contract, *, settlement_key: str) -> int | None:
        """把合同托管退给出资人（取消/过期/失败结算共用）。"""
        escrow = EscrowService(self.db).get_for_contract(int(contract.id))
        if escrow is None:  # pragma: no cover - 创建时必锁资
            return None
        settlement = SettlementService(self.db).settle(
            SettlementRequest(
                settlement_key=settlement_key,
                amount=int(escrow.amount),
                beneficiary=self._issuer(contract),
                reason=f"contract:{contract.code}:refund",
                reference_type="contract",
                reference_id=str(int(contract.id)),
                funding_mode=FundingMode.player_escrow,
                escrow_id=int(escrow.id),
                refund=True,
            ),
            commit=False,
        )
        return int(settlement.transaction.id)

    def _existing_settlement(self, contract: Contract) -> ContractSettlement:
        stored = (contract.metadata_json or {}).get("settlement") or {}
        gross = int(stored.get("gross", contract.consideration_amount))
        fee = int(stored.get("fee", 0))
        return ContractSettlement(
            contract_id=int(contract.id),
            gross=gross,
            fee=fee,
            net=int(stored.get("net", gross - fee)),
            fee_treasury=int(stored.get("treasury", 0)),
            fee_burn=int(stored.get("burn", 0)),
            refund=bool(stored.get("refund", False)),
            transaction_id=int(
                stored.get("transaction_id", contract.settlement_transaction_id or 0)
            ),
            created=False,
        )

    def _issuer(self, contract: Contract) -> EconomicActor:
        return EconomicActor(
            kind=EconomicActorKind(contract.issuer_actor_kind),
            ref=int(contract.issuer_actor_ref),
        )

    def _require(self, contract_id: int) -> Contract:
        contract = economy_repo.get_contract(self.db, contract_id)
        if contract is None:
            raise ContractError("contract_not_found", http_status=404)
        return contract

    def _next_code(self, contract_type: ContractType) -> str:
        prefix = {
            ContractType.work: "CW",
            ContractType.talent: "CT",
            ContractType.service: "CS",
            ContractType.procurement: "CP",
            ContractType.research: "CR",
        }[contract_type]
        from sqlalchemy import func, select

        sequence = int(self.db.execute(select(func.max(Contract.id))).scalar_one() or 0) + 1
        return f"{prefix}-{sequence:05d}"

    def _publish(self, event_type: str, contract: Contract, extra: dict) -> None:
        bus.publish(
            event_type,
            {
                "contract_id": int(contract.id),
                "code": contract.code,
                "contract_type": contract.contract_type,
                "status": contract.status,
                "consideration_amount": int(contract.consideration_amount),
                "issuer_company_id": int(contract.issuer_actor_ref),
                "contractor_ref": contract.contractor_actor_ref,
                **extra,
            },
            company_id=int(contract.issuer_actor_ref)
            if contract.issuer_actor_kind == EconomicActorKind.company.value
            else None,
        )


class OfferService:
    """出价/申请（§22）：**不产生资金流**；被接受后生成合同。"""

    def __init__(self, db: Session, *, policy: EconomicPolicy | None = None) -> None:
        self.db = db
        self.policy = policy or economic_policy()
        self.contracts = ContractService(db, policy=self.policy)

    def create_offer(
        self,
        *,
        from_actor: EconomicActor,
        amount: int,
        contract_type: ContractType = ContractType.work,
        to_actor: EconomicActor | None = None,
        work_order_id: int | None = None,
        listing_id: int | None = None,
        message: str = "",
        terms: dict | None = None,
        expires_at: datetime | None = None,
        commit: bool = True,
    ) -> Offer:
        """提出报价/申请（OPEN）。锚点可给 work_order / listing，或直接给 `to_actor`。"""
        if int(amount) <= 0:
            raise ContractError("offer_amount_must_be_positive", http_status=422)
        if work_order_id is None and listing_id is None and to_actor is None:
            raise ContractError("offer_anchor_required", http_status=422)
        kind, ref = economy_repo.actor_columns(from_actor)
        to_kind, to_ref = (
            economy_repo.actor_columns(to_actor) if to_actor is not None else (None, None)
        )
        offer = economy_repo.insert_offer(
            self.db,
            contract_type=contract_type.value,
            work_order_id=work_order_id,
            listing_id=listing_id,
            from_actor_kind=kind,
            from_actor_ref=ref,
            to_actor_kind=to_kind,
            to_actor_ref=to_ref,
            amount=int(amount),
            currency=Currency.credit.value,
            message=message,
            terms_json=dict(terms or {}),
            status=OfferStatus.open.value,
            expires_at=expires_at,
            metadata_json={},
        )
        if commit:
            self.db.commit()
            bus.publish(
                "offer.created",
                {
                    "offer_id": int(offer.id),
                    "contract_type": offer.contract_type,
                    "amount": int(offer.amount),
                    "work_order_id": work_order_id,
                    "listing_id": listing_id,
                },
                company_id=ref if kind == EconomicActorKind.company.value else None,
            )
        return offer

    def accept_offer(
        self,
        offer_id: int,
        *,
        issuer: EconomicActor,
        title: str | None = None,
        commit: bool = True,
    ) -> tuple[Offer, Contract | None]:
        """接受报价：`OPEN → ACCEPTED`，并生成合同（**由 issuer 出资锁资**）。

        佣金/对价 = offer.amount（§22：Offer 本身不产生资金流，钱在合同里锁）。
        重复接受幂等（返回既有合同）。
        """
        offer = economy_repo.get_offer(self.db, offer_id)
        if offer is None:
            raise ContractError("offer_not_found", http_status=404)
        if offer.status == OfferStatus.accepted.value and offer.contract_id is not None:
            contract = economy_repo.get_contract(self.db, int(offer.contract_id))
            return offer, contract
        if offer.status != OfferStatus.open.value:
            raise ContractError(f"offer_not_open:{offer.status}")

        contract = self.contracts.create_contract(
            issuer=issuer,
            title=title or f"Offer #{offer.id}",
            consideration_amount=int(offer.amount),
            contract_type=ContractType(offer.contract_type),
            contractor=EconomicActor(
                kind=EconomicActorKind(offer.from_actor_kind), ref=int(offer.from_actor_ref)
            ),
            terms=dict(offer.terms_json or {}),
            reference_type="offer",
            reference_id=str(int(offer.id)),
            commit=False,
        )
        if (
            economy_repo.transition_offer(
                self.db,
                offer_id=offer_id,
                from_statuses=(OfferStatus.open.value,),
                status=OfferStatus.accepted.value,
                responded_at=utcnow(),
                contract_id=int(contract.id),
            )
            == 0
        ):
            self.db.rollback()
            current = economy_repo.get_offer(self.db, offer_id)
            if current is not None and current.status == OfferStatus.accepted.value:
                return current, economy_repo.get_contract(self.db, int(current.contract_id or 0))
            raise ContractError(f"offer_not_open:{offer.status}")
        self.db.expire_all()
        accepted = economy_repo.get_offer(self.db, offer_id)
        assert accepted is not None
        if commit:
            self.db.commit()
            bus.publish(
                "offer.accepted",
                {
                    "offer_id": offer_id,
                    "contract_id": int(contract.id),
                    "amount": int(offer.amount),
                },
                company_id=int(contract.issuer_actor_ref),
            )
        return accepted, contract

    def reject_offer(self, offer_id: int, *, reason: str = "", commit: bool = True) -> Offer:
        return self._respond(offer_id, status=OfferStatus.rejected, reason=reason, commit=commit)

    def withdraw_offer(self, offer_id: int, *, commit: bool = True) -> Offer:
        return self._respond(offer_id, status=OfferStatus.withdrawn, commit=commit)

    def _respond(
        self,
        offer_id: int,
        *,
        status: OfferStatus,
        reason: str = "",
        commit: bool,
    ) -> Offer:
        offer = economy_repo.get_offer(self.db, offer_id)
        if offer is None:
            raise ContractError("offer_not_found", http_status=404)
        if offer.status == status.value:
            return offer
        if offer.status != OfferStatus.open.value:
            raise ContractError(f"offer_not_open:{offer.status}")
        if (
            economy_repo.transition_offer(
                self.db,
                offer_id=offer_id,
                from_statuses=(OfferStatus.open.value,),
                status=status.value,
                responded_at=utcnow(),
                metadata_json={**(offer.metadata_json or {}), "reason": reason}
                if reason
                else (offer.metadata_json or {}),
            )
            == 0
        ):
            raise ContractError(f"offer_not_open:{offer.status}")
        self.db.expire_all()
        updated = economy_repo.get_offer(self.db, offer_id)
        assert updated is not None
        if commit:
            self.db.commit()
        return updated

    def expire_overdue(self, *, now: datetime | None = None, commit: bool = True) -> int:
        moment = now or utcnow()
        expired = 0
        for offer in economy_repo.list_offers(self.db, statuses=(OfferStatus.open.value,)):
            expires_at = as_utc(offer.expires_at)
            if expires_at is None or expires_at >= moment:
                continue
            if (
                economy_repo.transition_offer(
                    self.db,
                    offer_id=int(offer.id),
                    from_statuses=(OfferStatus.open.value,),
                    status=OfferStatus.expired.value,
                    responded_at=moment,
                )
                > 0
            ):
                expired += 1
        if commit and expired:
            self.db.commit()
        return expired


__all__ = [
    "CANCELLABLE_STATUSES",
    "SETTLEABLE_STATUSES",
    "ContractError",
    "ContractService",
    "ContractSettlement",
    "EscrowError",
    "OfferService",
]
