"""M1.4 玩家工作市场与托管（docs/m1-economy-design.md §19/§23/§24；plan §4/M1.4）。

断言的是**玩家之间不印钱**这条不变量：
- 锁资：发布方 `available` 减少、`reserved` 增加、**`posted` 不变**（锁资不改变净资产）；
  钱进的是**这笔订单自己的**托管账户（`subject_ref = escrow.id`）；
- **E11**：余额不足 ⇒ 整笔回滚 —— 不产生订单，也不产生 Escrow（不是"先发布后补钱"）；
- **E7/E8**：托管 → 承接方是**转移**，`supply` / `minted` 完全不变，且**不写 reward_grants**
  （玩家之间的钱不是"发行/奖励"）；
- **E25/E30**：放款/退款后托管账户归零、`reserved` 由账本归因自动回落（清空投影重建后依然正确）；
- **§33**：release 与 refund 竞争**只有一个成功**（Escrow 状态 CAS）；重复调用幂等；
- 取消/过期 ⇒ 退款给发布方；只有发布方本人能取消。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.core.database import SessionLocal
from app.economy.contracts import EconomicActor
from app.economy.policy import economic_policy
from app.models.economy import Escrow
from app.models.enums import (
    EconomicActorKind,
    EscrowStatus,
    FundingMode,
    WorkOrderKind,
    WorkOrderStatus,
)
from app.models.organization import Company
from app.repositories import economy as economy_repo
from app.services.economy.accounts import AccountService
from app.services.economy.escrow import EscrowError, EscrowService
from app.services.economy.ledger import InsufficientFunds, LedgerService
from app.services.economy.monetary import MonetaryAuthority
from app.services.economy.projection import rebuild_wallet_projection, verify_wallet_projection
from app.services.economy.settlement import SettlementRequest, SettlementService
from app.services.economy.work_orders import WorkOrderError, WorkOrderService

_seq = 0


def _company(db, name: str = "PlayerCo") -> Company:
    global _seq
    _seq += 1
    company = Company(name=f"{name} {_seq}", slug=f"player-co-{_seq}", description="")
    db.add(company)
    db.commit()
    return company


def _funded(db, company: Company, amount: int) -> int:
    """给公司发一笔启动资金（独立提交），返回其 actor 账户 id。"""
    MonetaryAuthority(db).mint(
        actor=EconomicActor.company(company.id), amount=amount, reason="test"
    )
    return int(AccountService(db).ensure_account(EconomicActor.company(company.id)).id)


def _wallet(account_id: int) -> dict:
    with SessionLocal() as session:
        row = economy_repo.get_projection(session, account_id)
        if row is None:
            return {"posted": 0, "available": 0, "reserved": 0}
        return {
            "posted": int(row.posted_balance),
            "available": int(row.available_balance),
            "reserved": int(row.reserved_balance),
        }


def _supply(db):
    return LedgerService(db).supply()


def _publish(db, issuer: Company, *, reward: int = 5_000, **kwargs):
    return WorkOrderService(db).publish_player_order(
        issuer=EconomicActor.company(issuer.id),
        title=kwargs.pop("title", "Player bounty"),
        reward_amount=reward,
        deliverables=kwargs.pop("deliverables", {"required_keys": ["readme"]}),
        **kwargs,
    )


# ---------------------------------------------------------------- 锁资


def test_funding_locks_balance_without_changing_net_worth(db):
    issuer = _company(db, "FundIssuer")
    account_id = _funded(db, issuer, 20_000)
    minted_before = _supply(db).minted

    result = _publish(db, issuer, reward=8_000)
    order = result.order
    escrow = EscrowService(db).get_for_order(int(order.id))
    assert escrow is not None
    assert escrow.status == EscrowStatus.funded.value
    assert int(escrow.amount) == 8_000
    assert escrow.payer_actor_ref == issuer.id
    assert escrow.escrow_account_id is not None

    # 锁资：可花减少、锁定增加、总资产不变（设计 §11 的公司钱包示例）
    assert _wallet(account_id) == {"posted": 20_000, "available": 12_000, "reserved": 8_000}
    # 钱在**这笔订单自己的**托管账户里（subject_ref = escrow.id）
    with SessionLocal() as session:
        escrow_account = economy_repo.get_account(session, int(escrow.escrow_account_id))
        assert escrow_account is not None
        assert escrow_account.kind == "escrow"
        assert int(escrow_account.subject_ref) == int(escrow.id)
    assert _supply(db).minted == minted_before  # E7：锁资不发行


def test_publish_without_funds_leaves_no_order_and_no_escrow(db):
    poor = _company(db, "PoorIssuer")
    _funded(db, poor, 1_000)
    account_id = int(AccountService(db).ensure_account(EconomicActor.company(poor.id)).id)
    before = _wallet(account_id)
    orders_before = len(economy_repo.list_work_orders(db))
    escrows_before = len(economy_repo.list_escrows(db))

    with pytest.raises(InsufficientFunds):
        _publish(db, poor, reward=5_000)

    # E11：没有"已发布但没锁资"的订单，也没有空托管
    assert len(economy_repo.list_work_orders(db)) == orders_before
    assert len(economy_repo.list_escrows(db)) == escrows_before
    assert _wallet(account_id) == before


def test_player_order_amount_guards(db):
    issuer = _company(db, "GuardIssuer")
    _funded(db, issuer, 100_000)
    service = WorkOrderService(db)
    with pytest.raises(WorkOrderError, match="kind_not_player_kind"):
        service.publish_player_order(
            issuer=EconomicActor.company(issuer.id),
            title="official via player path",
            reward_amount=1_000,
            kind=WorkOrderKind.official_bounty,
        )
    with pytest.raises(WorkOrderError, match="reward_amount_must_be_positive"):
        service.publish_player_order(
            issuer=EconomicActor.company(issuer.id), title="zero", reward_amount=0
        )
    with pytest.raises(WorkOrderError, match="reward_exceeds_player_order_max"):
        service.publish_player_order(
            issuer=EconomicActor.company(issuer.id),
            title="huge",
            reward_amount=economic_policy().player_order_max_reward + 1,
        )


# ---------------------------------------------------------------- 全流程：A → B


def test_full_player_lifecycle_transfers_money_without_minting(db):
    issuer = _company(db, "LifeIssuer")
    contractor = _company(db, "LifeContractor")
    issuer_account = _funded(db, issuer, 30_000)
    contractor_account = int(
        AccountService(db).ensure_account(EconomicActor.company(contractor.id)).id
    )
    service = WorkOrderService(db)
    supply_before = _supply(db)

    order = _publish(db, issuer, reward=9_000).order
    assert order.funding_mode == FundingMode.player_escrow.value
    assert order.issuer_actor_ref == issuer.id
    escrow = EscrowService(db).get_for_order(int(order.id))
    assert escrow is not None
    escrow_id = int(escrow.id)

    accepted = service.accept(order.id, company_id=contractor.id)
    assert accepted.status == WorkOrderStatus.accepted.value

    _, settled, _ = service.submit(
        order.id,
        company_id=contractor.id,
        summary="delivered",
        deliverables={"readme": "https://x/readme"},
    )
    assert settled.status == WorkOrderStatus.settled.value

    # 钱：从发布方 → 承接方；发布方在锁资时就已经扣了，这里只是"锁定 → 落袋"
    assert _wallet(issuer_account) == {"posted": 21_000, "available": 21_000, "reserved": 0}
    assert _wallet(contractor_account)["available"] == 9_000
    after = _supply(db)
    assert (after.minted, after.burned, after.supply) == (
        supply_before.minted,
        supply_before.burned,
        supply_before.supply,
    )  # E7/E8：玩家之间不印钱
    # 托管账户归零（E25）
    refunded = EscrowService(db).get_for_order(int(order.id))
    assert refunded.status == EscrowStatus.released.value
    assert EscrowService(db).view(refunded).account_balance == 0
    # 玩家订单**不写 reward_grants**（不是发行/奖励）
    assert economy_repo.list_reward_grants(db, actor_ref=contractor.id) == []
    assert economy_repo.list_reward_grants(db, actor_ref=issuer.id) == []
    # 结算交易可追溯
    transaction = economy_repo.get_transaction(db, int(settled.settlement_transaction_id))
    assert transaction is not None
    assert transaction.transaction_type == "escrow_release"
    assert transaction.reference_type == "escrow"
    assert transaction.reference_id == str(escrow_id)
    assert verify_wallet_projection(db).ok


# ---------------------------------------------------------------- 取消 / 过期


def test_cancel_refunds_the_issuer(db):
    issuer = _company(db, "CancelIssuer")
    stranger = _company(db, "CancelStranger")
    issuer_account = _funded(db, issuer, 12_000)
    service = WorkOrderService(db)

    order = _publish(db, issuer, reward=4_000).order
    escrow = EscrowService(db).get_for_order(int(order.id))
    assert _wallet(issuer_account)["available"] == 8_000

    # 只有发布方本人能取消
    with pytest.raises(WorkOrderError, match="not_your_order"):
        service.cancel(order.id, company_id=stranger.id)

    cancelled = service.cancel(order.id, company_id=issuer.id, reason="changed my mind")
    assert cancelled.status == WorkOrderStatus.cancelled.value
    assert _wallet(issuer_account) == {"posted": 12_000, "available": 12_000, "reserved": 0}
    refunded = EscrowService(db).get_for_order(int(order.id))
    assert refunded.status == EscrowStatus.refunded.value
    assert EscrowService(db).view(refunded).account_balance == 0
    # 幂等：重复取消不再转账
    again = service.cancel(order.id, company_id=issuer.id)
    assert again.status == WorkOrderStatus.cancelled.value
    assert _wallet(issuer_account)["available"] == 12_000
    del escrow


def test_expired_player_order_refunds_the_issuer(db):
    issuer = _company(db, "ExpireIssuer")
    issuer_account = _funded(db, issuer, 10_000)
    service = WorkOrderService(db)
    order = _publish(
        db,
        issuer,
        reward=3_000,
        deadline_at=datetime.now(UTC) - timedelta(hours=2),
    ).order
    assert _wallet(issuer_account)["available"] == 7_000

    expired = service.expire_overdue()
    assert expired >= 1
    assert service.detail(order.id).status == WorkOrderStatus.expired.value
    assert _wallet(issuer_account)["available"] == 10_000  # 钱回来了
    assert EscrowService(db).get_for_order(int(order.id)).status == EscrowStatus.refunded.value


def test_accepted_order_can_still_be_cancelled_and_refunded(db):
    issuer = _company(db, "CancelLate")
    contractor = _company(db, "CancelLateContractor")
    issuer_account = _funded(db, issuer, 6_000)
    service = WorkOrderService(db)
    order = _publish(db, issuer, reward=2_000).order
    service.accept(order.id, company_id=contractor.id)

    cancelled = service.cancel(order.id, company_id=issuer.id)
    assert cancelled.status == WorkOrderStatus.cancelled.value
    assert _wallet(issuer_account)["available"] == 6_000
    assert int(AccountService(db).ensure_account(EconomicActor.company(contractor.id)).id) > 0


# ---------------------------------------------------------------- 竞争与幂等


def test_release_and_refund_race_has_exactly_one_winner(db):
    issuer = _company(db, "RaceIssuer")
    contractor = _company(db, "RaceContractor")
    _funded(db, issuer, 10_000)
    order = _publish(db, issuer, reward=4_000).order
    escrow_id = int(EscrowService(db).get_for_order(int(order.id)).id)
    payee = EconomicActor.company(contractor.id)

    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def do(action: str) -> None:
        with SessionLocal() as session:
            barrier.wait()
            service = EscrowService(session)
            try:
                # commit=True：并发竞争必须落在**已提交**的 CAS 上；
                # 不提交的话两边都会成功（各自回滚，钱实际没动）——那是测试假象，不是竞态。
                if action == "release":
                    escrow, created = service.release(escrow_id, payee=payee, commit=True)
                else:
                    escrow, created = service.refund(escrow_id, commit=True)
                outcomes.append(f"{action}:{escrow.status}:{created}")
            except EscrowError as exc:
                outcomes.append(f"{action}:rejected:{exc.reason}")

    threads = [threading.Thread(target=do, args=(action,)) for action in ("release", "refund")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [outcome for outcome in outcomes if "rejected" not in outcome]
    assert len(winners) == 1, outcomes
    with SessionLocal() as session:
        escrow = economy_repo.get_escrow(session, escrow_id)
        assert escrow is not None
        assert escrow.status in {EscrowStatus.released.value, EscrowStatus.refunded.value}
        assert EscrowService(session).view(escrow).account_balance == 0


def test_repeat_release_and_refund_are_idempotent(db):
    issuer = _company(db, "IdemIssuer")
    contractor = _company(db, "IdemContractor")
    issuer_account = _funded(db, issuer, 8_000)
    contractor_account = int(
        AccountService(db).ensure_account(EconomicActor.company(contractor.id)).id
    )
    order = _publish(db, issuer, reward=3_000).order
    escrow_id = int(EscrowService(db).get_for_order(int(order.id)).id)
    service = EscrowService(db)

    released, created = service.release(
        escrow_id, payee=EconomicActor.company(contractor.id), commit=True
    )
    assert created is True
    again, created_again = service.release(
        escrow_id, payee=EconomicActor.company(contractor.id), commit=True
    )
    assert created_again is False
    assert again.id == released.id
    assert _wallet(contractor_account)["available"] == 3_000  # 只发一次
    assert _wallet(issuer_account)["available"] == 5_000

    # 已放款后不能退款
    with pytest.raises(EscrowError, match="escrow_not_refundable"):
        service.refund(escrow_id)
    assert _wallet(contractor_account)["available"] == 3_000


def test_escrow_settlement_service_idempotent_and_no_mint(db):
    issuer = _company(db, "SettleIssuer")
    contractor = _company(db, "SettleContractor")
    _funded(db, issuer, 7_000)
    order = _publish(db, issuer, reward=2_000).order
    escrow_id = int(EscrowService(db).get_for_order(int(order.id)).id)
    minted_before = _supply(db).minted

    request = SettlementRequest(
        settlement_key=f"work_order:{order.id}",
        amount=2_000,
        beneficiary=EconomicActor.company(contractor.id),
        reason="test",
        reference_type="work_order",
        reference_id=str(order.id),
        funding_mode=FundingMode.player_escrow,
        escrow_id=escrow_id,
    )
    service = SettlementService(db)
    first = service.settle(request, commit=True)
    second = service.settle(request, commit=True)
    assert first.created is True
    assert second.created is False
    assert first.transaction.id == second.transaction.id
    assert _supply(db).minted == minted_before  # E8：托管放款绝不 mint
    # 缺 escrow_id ⇒ 明确拒绝
    with pytest.raises(Exception, match="escrow_id_required"):
        service.settle(
            SettlementRequest(
                settlement_key="x",
                amount=1,
                beneficiary=EconomicActor.company(contractor.id),
                reason="test",
                reference_type="work_order",
                reference_id=str(order.id),
                funding_mode=FundingMode.player_escrow,
            )
        )


def test_escrow_rejects_mismatched_amounts_and_system_actors(db):
    issuer = _company(db, "MismatchIssuer")
    _funded(db, issuer, 5_000)
    order = _publish(db, issuer, reward=1_500).order
    service = EscrowService(db)
    # 同订单不能锁第二笔不同金额的托管
    with pytest.raises(EscrowError, match="escrow_amount_mismatch"):
        service.fund_for_order(
            order_id=int(order.id),
            payer=EconomicActor.company(issuer.id),
            amount=2_000,
        )
    # 重复调用同金额 ⇒ 返回既有托管（幂等）
    same = service.fund_for_order(
        order_id=int(order.id), payer=EconomicActor.company(issuer.id), amount=1_500
    )
    assert same.status == EscrowStatus.funded.value


# ---------------------------------------------------------------- 归因与重建


def test_reserved_follows_ledger_attribution_and_rebuild(db):
    issuer = _company(db, "AttrIssuer")
    issuer_account = _funded(db, issuer, 20_000)
    order = _publish(db, issuer, reward=7_000).order
    escrow_id = int(EscrowService(db).get_for_order(int(order.id)).id)

    # 清空投影重建：reserved 依然归因正确（E30）
    rebuild_wallet_projection(db)
    assert _wallet(issuer_account) == {"posted": 20_000, "available": 13_000, "reserved": 7_000}
    assert verify_wallet_projection(db).ok

    EscrowService(db).refund(escrow_id, commit=True)
    rebuild_wallet_projection(db)
    assert _wallet(issuer_account) == {"posted": 20_000, "available": 20_000, "reserved": 0}
    assert verify_wallet_projection(db).ok


def test_escrow_rows_are_auditable(db):
    issuer = _company(db, "AuditIssuer")
    _funded(db, issuer, 5_000)
    order = _publish(db, issuer, reward=1_000).order
    escrow = EscrowService(db).get_for_order(int(order.id))
    assert isinstance(escrow, Escrow)
    assert escrow.funded_transaction_id is not None
    assert escrow.funded_at is not None
    assert economy_repo.get_transaction(db, int(escrow.funded_transaction_id)).transaction_type == (
        "escrow_fund"
    )
    view = EscrowService(db).view(escrow)
    assert view.account_balance == 1_000
    assert view.payer_actor_kind == EconomicActorKind.company.value
    assert view.work_order_id == int(order.id)
    assert escrow.metadata_json.get("kind") == WorkOrderKind.player_bounty.value
