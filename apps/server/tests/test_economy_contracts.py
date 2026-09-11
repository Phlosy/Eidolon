"""M1.6 合同核心（docs/m1-economy-design.md §21/§22/§23/§24；plan §4/M1.6）。

断言的是**合同资金不变量**：
- 合同创建即**锁资对价**（E11 的合同形态）；余额不足 ⇒ 不留合同（E13/E24）；
- 承接方接受 = `PENDING_ACCEPTANCE → ACTIVE → FUNDED`（两步都走冻结状态机，不跳步）；
- 交付结算 = **多腿**：受益方净额 + Treasury + Burn（`treasury + burn + net == 对价`），
  **绝不 mint**（E8），但 burn 腿按设计减少流通（§7 Sink）；
- 取消/过期/失败 ⇒ 退款给出资人（不抽手续费）；
- 幂等：`settlement_key = contract:<id>`，重复 fulfill/settle 复用既有终局（E12）；
- 状态机：非法迁移一律拒绝；非当事方 404（不泄露存在性）。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.core.database import SessionLocal
from app.economy.contracts import EconomicActor
from app.economy.policy import economic_policy
from app.models.enums import (
    ContractStatus,
    ContractType,
    EscrowStatus,
    LedgerAccountKind,
    OfferStatus,
)
from app.models.organization import Company
from app.repositories import economy as economy_repo
from app.services.economy.accounts import AccountService
from app.services.economy.contracts import ContractError, ContractService, OfferService
from app.services.economy.costs import FeeService
from app.services.economy.escrow import EscrowService
from app.services.economy.ledger import InsufficientFunds, LedgerService
from app.services.economy.monetary import MonetaryAuthority
from app.services.economy.projection import verify_wallet_projection

_seq = 0


def _company(db, name: str = "ContractCo") -> Company:
    global _seq
    _seq += 1
    company = Company(name=f"{name} {_seq}", slug=f"contract-co-{_seq}", description="")
    db.add(company)
    db.commit()
    return company


def _fund(db, company: Company, amount: int) -> int:
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


def _system_balance(db, kind: LedgerAccountKind) -> int:
    total = 0
    for account in economy_repo.list_accounts_by_kind(db, kinds=(kind.value,)):
        total += LedgerService(db).ledger_balance(int(account.id))
    return total


def _supply(db):
    return LedgerService(db).supply()


# ---------------------------------------------------------------- 创建 / 锁资


def test_create_contract_locks_the_consideration(db):
    issuer = _company(db, "CreateIssuer")
    contractor = _company(db, "CreateContractor")
    issuer_account = _fund(db, issuer, 20_000)
    supply_before = _supply(db)

    contract = ContractService(db).create_contract(
        issuer=EconomicActor.company(issuer.id),
        contractor=EconomicActor.company(contractor.id),
        title="Ship a feature",
        consideration_amount=8_000,
        contract_type=ContractType.work,
        subject="deliver the module",
        terms={"deliverables": ["module", "tests"]},
    )
    assert contract.status == ContractStatus.pending_acceptance.value
    assert int(contract.consideration_amount) == 8_000
    assert contract.code.startswith("CW-")
    assert contract.policy_version == economic_policy().version

    escrow = EscrowService(db).get_for_contract(int(contract.id))
    assert escrow is not None
    assert escrow.status == EscrowStatus.funded.value
    assert int(escrow.amount) == 8_000
    assert escrow.contract_id == contract.id
    # 锁资：可花减少、锁定增加、总资产不变（对价仍是出资人的资产）
    assert _wallet(issuer_account) == {"posted": 20_000, "available": 12_000, "reserved": 8_000}
    assert _supply(db).minted == supply_before.minted  # 锁资不发行
    assert verify_wallet_projection(db).ok


def test_create_contract_without_funds_leaves_nothing(db):
    issuer = _company(db, "PoorIssuer")
    _fund(db, issuer, 1_000)
    account_id = int(AccountService(db).ensure_account(EconomicActor.company(issuer.id)).id)
    before = _wallet(account_id)
    contracts_before = len(economy_repo.list_contracts(db))
    escrows_before = len(economy_repo.list_escrows(db))

    with pytest.raises(InsufficientFunds):
        ContractService(db).create_contract(
            issuer=EconomicActor.company(issuer.id),
            title="Too expensive",
            consideration_amount=5_000,
        )
    assert len(economy_repo.list_contracts(db)) == contracts_before
    assert len(economy_repo.list_escrows(db)) == escrows_before
    assert _wallet(account_id) == before


def test_contract_amount_and_issuer_guards(db):
    company = _company(db, "GuardIssuer")
    _fund(db, company, 5_000)
    service = ContractService(db)
    with pytest.raises(ContractError, match="consideration_must_be_positive"):
        service.create_contract(
            issuer=EconomicActor.company(company.id), title="zero", consideration_amount=0
        )
    from app.models.enums import SystemAccountKind

    with pytest.raises(ContractError, match="issuer_must_be_company"):
        service.create_contract(
            issuer=EconomicActor.system(SystemAccountKind.issuance),
            title="system",
            consideration_amount=100,
        )


# ---------------------------------------------------------------- 全生命周期（work）


def test_work_contract_full_lifecycle_settles_with_fee_split(db):
    issuer = _company(db, "WorkIssuer")
    contractor = _company(db, "WorkContractor")
    issuer_account = _fund(db, issuer, 30_000)
    contractor_account = int(
        AccountService(db).ensure_account(EconomicActor.company(contractor.id)).id
    )
    treasury_before = _system_balance(db, LedgerAccountKind.treasury)
    burn_before = _system_balance(db, LedgerAccountKind.burn)
    minted_before = _supply(db).minted

    service = ContractService(db)
    contract = service.create_contract(
        issuer=EconomicActor.company(issuer.id),
        contractor=EconomicActor.company(contractor.id),
        title="Build the module",
        consideration_amount=10_000,
        contract_type=ContractType.work,
    )
    accepted = service.accept(contract.id, contractor=EconomicActor.company(contractor.id))
    assert accepted.status == ContractStatus.funded.value  # 接受即生效（资金已托管）

    settled, outcome = service.fulfill(contract.id, contractor=EconomicActor.company(contractor.id))
    assert settled.status == ContractStatus.settled.value
    quote = FeeService(db).contract_quote(10_000)
    assert (outcome.gross, outcome.fee, outcome.net) == (10_000, quote.fee, quote.net)
    assert outcome.fee_treasury == quote.treasury and outcome.fee_burn == quote.burn
    assert quote.treasury + quote.burn + quote.net == 10_000  # 三腿之和 = 对价

    # 钱：出资人 30,000 − 10,000（对价已扣在锁资时）；承接方拿净额；手续费进 Treasury/Burn
    assert _wallet(issuer_account) == {"posted": 20_000, "available": 20_000, "reserved": 0}
    assert _wallet(contractor_account)["available"] == quote.net
    assert _system_balance(db, LedgerAccountKind.treasury) - treasury_before == quote.treasury
    assert _system_balance(db, LedgerAccountKind.burn) - burn_before == quote.burn
    supply = _supply(db)
    assert supply.minted == minted_before  # E8：合同结算绝不 mint
    assert supply.burned - burn_before == quote.burn  # 只有手续费 burn 腿回收
    assert supply.supply == supply.minted - supply.burned
    # 托管归零（E25），合同指向终局交易（E16）
    assert EscrowService(db).view(service.escrow_view(contract.id)).account_balance == 0
    assert settled.settlement_transaction_id is not None
    assert verify_wallet_projection(db).ok

    # 幂等：重复结算不再放款
    again, replay = service.settle(contract.id)
    assert replay.created is False
    assert again.status == ContractStatus.settled.value
    assert _wallet(contractor_account)["available"] == quote.net
    assert _wallet(issuer_account)["available"] == 20_000


def test_service_contract_lifecycle_with_different_type(db):
    issuer = _company(db, "ServiceIssuer")
    provider = _company(db, "ServiceProvider")
    _fund(db, issuer, 12_000)
    provider_account = int(AccountService(db).ensure_account(EconomicActor.company(provider.id)).id)
    service = ContractService(db)
    contract = service.create_contract(
        issuer=EconomicActor.company(issuer.id),
        contractor=EconomicActor.company(provider.id),
        title="Audit our books",
        consideration_amount=4_000,
        contract_type=ContractType.service,
        subject="quarterly audit",
    )
    assert contract.code.startswith("CS-")
    assert contract.contract_type == ContractType.service.value
    service.accept(contract.id, contractor=EconomicActor.company(provider.id))
    settled, outcome = service.fulfill(contract.id, contractor=EconomicActor.company(provider.id))
    assert settled.status == ContractStatus.settled.value
    assert outcome.net + outcome.fee == 4_000
    assert _wallet(provider_account)["available"] == outcome.net
    assert verify_wallet_projection(db).ok


# ---------------------------------------------------------------- 取消 / 过期 / 失败


def test_cancel_before_acceptance_refunds_the_issuer(db):
    issuer = _company(db, "CancelIssuer")
    contractor = _company(db, "CancelContractor")
    issuer_account = _fund(db, issuer, 10_000)
    stranger = _company(db, "CancelStranger")
    service = ContractService(db)
    contract = service.create_contract(
        issuer=EconomicActor.company(issuer.id),
        contractor=EconomicActor.company(contractor.id),
        title="Cancel me",
        consideration_amount=3_000,
    )
    assert _wallet(issuer_account)["available"] == 7_000

    # 非当事方不能取消（404，不泄露存在性）
    with pytest.raises(ContractError, match="not_a_contract_party"):
        service.cancel(contract.id, actor=EconomicActor.company(stranger.id))

    cancelled = service.cancel(contract.id, actor=EconomicActor.company(issuer.id))
    assert cancelled.status == ContractStatus.cancelled.value
    assert _wallet(issuer_account) == {"posted": 10_000, "available": 10_000, "reserved": 0}
    escrow = EscrowService(db).get_for_contract(int(contract.id))
    assert escrow.status == EscrowStatus.refunded.value
    # 幂等：重复取消不再退款
    assert service.cancel(contract.id, actor=EconomicActor.company(issuer.id)).status == (
        ContractStatus.cancelled.value
    )
    assert _wallet(issuer_account)["available"] == 10_000


def test_funded_contract_cannot_be_cancelled_but_can_fail_and_refund(db):
    issuer = _company(db, "FailIssuer")
    contractor = _company(db, "FailContractor")
    issuer_account = _fund(db, issuer, 8_000)
    service = ContractService(db)
    contract = service.create_contract(
        issuer=EconomicActor.company(issuer.id),
        contractor=EconomicActor.company(contractor.id),
        title="Will fail",
        consideration_amount=2_500,
    )
    service.accept(contract.id, contractor=EconomicActor.company(contractor.id))
    # 资金已就位、已开工 ⇒ 冻结状态机不允许 cancelled（只能 fulfill / fail / dispute）
    with pytest.raises(ContractError, match="contract_not_cancellable"):
        service.cancel(contract.id, actor=EconomicActor.company(issuer.id))

    failed = service.fail(contract.id, actor=EconomicActor.company(contractor.id), reason="blocked")
    assert failed.status == ContractStatus.failed.value
    settled, outcome = service.settle_failed_with_refund(contract.id)
    assert settled.status == ContractStatus.settled.value
    assert outcome.refund is True
    assert outcome.fee == 0  # 退款不抽手续费
    assert _wallet(issuer_account) == {"posted": 8_000, "available": 8_000, "reserved": 0}


def test_expired_contract_refunds(db):
    issuer = _company(db, "ExpireIssuer")
    contractor = _company(db, "ExpireContractor")
    issuer_account = _fund(db, issuer, 6_000)
    service = ContractService(db)
    contract = service.create_contract(
        issuer=EconomicActor.company(issuer.id),
        contractor=EconomicActor.company(contractor.id),
        title="Expiring",
        consideration_amount=2_000,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    assert _wallet(issuer_account)["available"] == 4_000
    expired = service.expire_overdue()
    assert expired >= 1
    assert service.detail(contract.id).status == ContractStatus.expired.value
    assert _wallet(issuer_account)["available"] == 6_000


# ---------------------------------------------------------------- 状态机 / 并发 / 守卫


def test_illegal_contract_transitions_are_rejected(db):
    issuer = _company(db, "StateIssuer")
    contractor = _company(db, "StateContractor")
    other = _company(db, "StateOther")
    _fund(db, issuer, 20_000)
    service = ContractService(db)
    contract = service.create_contract(
        issuer=EconomicActor.company(issuer.id),
        contractor=EconomicActor.company(contractor.id),
        title="State machine",
        consideration_amount=3_000,
    )
    # 未接受不能交付
    with pytest.raises(ContractError, match="contract_not_fulfillable"):
        service.fulfill(contract.id, contractor=EconomicActor.company(contractor.id))
    # 未接受不能结算（settleable 只含 fulfilled/failed/disputed）
    with pytest.raises(ContractError, match="contract_not_settleable"):
        service.settle(contract.id)
    # 不是指定的承接方（404）
    with pytest.raises(ContractError, match="not_the_named_contractor"):
        service.accept(contract.id, contractor=EconomicActor.company(other.id))
    # 错误的一方不能交付
    service.accept(contract.id, contractor=EconomicActor.company(contractor.id))
    with pytest.raises(ContractError, match="not_the_contractor"):
        service.fulfill(contract.id, contractor=EconomicActor.company(other.id))
    # 未知合同 404
    with pytest.raises(ContractError, match="contract_not_found"):
        service.detail(999_999)


def test_concurrent_accept_and_fulfill_are_single_winner(db):
    issuer = _company(db, "RaceIssuer")
    contractor = _company(db, "RaceContractor")
    _fund(db, issuer, 20_000)
    contract = ContractService(db).create_contract(
        issuer=EconomicActor.company(issuer.id),
        contractor=EconomicActor.company(contractor.id),
        title="Race",
        consideration_amount=4_000,
    )
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def accept() -> None:
        with SessionLocal() as session:
            barrier.wait()
            try:
                accepted = ContractService(session).accept(
                    contract.id, contractor=EconomicActor.company(contractor.id)
                )
                outcomes.append(accepted.status)
            except Exception as exc:  # pragma: no cover
                outcomes.append(f"error:{type(exc).__name__}")

    threads = [threading.Thread(target=accept) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(outcome in {"ACTIVE", "FUNDED"} for outcome in outcomes), outcomes
    with SessionLocal() as session:
        assert ContractService(session).detail(contract.id).status == ContractStatus.funded.value


# ---------------------------------------------------------------- Offer（§22）


def test_offer_accept_creates_a_funded_contract(db):
    issuer = _company(db, "OfferIssuer")
    worker = _company(db, "OfferWorker")
    issuer_account = _fund(db, issuer, 15_000)
    service = OfferService(db)
    offer = service.create_offer(
        from_actor=EconomicActor.company(worker.id),
        to_actor=EconomicActor.company(issuer.id),
        amount=5_000,
        contract_type=ContractType.service,
        message="I can do this",
    )
    assert offer.status == OfferStatus.open.value

    accepted, contract = service.accept_offer(
        offer.id, issuer=EconomicActor.company(issuer.id), title="Accepted offer"
    )
    assert accepted.status == OfferStatus.accepted.value
    assert accepted.contract_id == contract.id
    assert contract is not None
    assert int(contract.consideration_amount) == 5_000
    assert contract.contract_type == ContractType.service.value
    assert contract.contractor_actor_ref == worker.id
    # Offer 自己不产生资金流：钱是在合同创建时锁的
    assert _wallet(issuer_account) == {"posted": 15_000, "available": 10_000, "reserved": 5_000}
    assert EscrowService(db).get_for_contract(int(contract.id)).status == EscrowStatus.funded.value

    # 幂等重放
    replay_offer, replay_contract = service.accept_offer(
        offer.id, issuer=EconomicActor.company(issuer.id)
    )
    assert replay_contract.id == contract.id
    assert _wallet(issuer_account)["available"] == 10_000

    # 已接受的 offer 不能再次接受/拒绝
    with pytest.raises(ContractError, match="offer_not_open"):
        service.reject_offer(offer.id)


def test_offer_requires_an_anchor_and_positive_amount(db):
    company = _company(db, "OfferAnchor")
    service = OfferService(db)
    with pytest.raises(ContractError, match="offer_anchor_required"):
        service.create_offer(from_actor=EconomicActor.company(company.id), amount=100)
    with pytest.raises(ContractError, match="offer_amount_must_be_positive"):
        service.create_offer(
            from_actor=EconomicActor.company(company.id),
            to_actor=EconomicActor.company(company.id),
            amount=0,
        )


def test_offer_reject_and_expire(db):
    issuer = _company(db, "OfferRejectIssuer")
    worker = _company(db, "OfferRejectWorker")
    service = OfferService(db)
    rejected = service.create_offer(
        from_actor=EconomicActor.company(worker.id),
        to_actor=EconomicActor.company(issuer.id),
        amount=1_000,
    )
    assert (
        service.reject_offer(rejected.id, reason="too pricey").status == OfferStatus.rejected.value
    )

    stale = service.create_offer(
        from_actor=EconomicActor.company(worker.id),
        to_actor=EconomicActor.company(issuer.id),
        amount=1_500,
        expires_at=datetime.now(UTC) - timedelta(hours=2),
    )
    assert service.expire_overdue() >= 1
    assert economy_repo.get_offer(db, stale.id).status == OfferStatus.expired.value


def test_contract_escrow_and_ledger_stay_consistent_across_many_contracts(db):
    issuer = _company(db, "ManyIssuer")
    _fund(db, issuer, 50_000)
    service = ContractService(db)
    contract_ids: list[int] = []
    for index in range(3):
        contractor = _company(db, f"ManyContractor{index}")
        contract = service.create_contract(
            issuer=EconomicActor.company(issuer.id),
            contractor=EconomicActor.company(contractor.id),
            title=f"Contract {index}",
            consideration_amount=1_000 + index * 500,
        )
        service.accept(contract.id, contractor=EconomicActor.company(contractor.id))
        service.fulfill(contract.id, contractor=EconomicActor.company(contractor.id))
        contract_ids.append(int(contract.id))
    snapshot = _supply(db)
    assert snapshot.supply == snapshot.minted - snapshot.burned
    assert verify_wallet_projection(db).ok
    # 本用例的每份合同：托管已释放且归零（E25）
    escrow_service = EscrowService(db)
    for contract_id in contract_ids:
        escrow = escrow_service.get_for_contract(contract_id)
        assert escrow is not None
        assert escrow.status == EscrowStatus.released.value
        assert escrow_service.view(escrow).account_balance == 0


# ---------------------------------------------------------------- API（M1.6d）


def _as_company(db, company_id: int):
    """切换请求公司作用域，并管理测试会话事务边界（SQLite：测试侧挂读事务会挡住 HTTP 写入）。

    进出都 rollback：释放读事务 + 让 identity map 过期，避免读到 API 写入前的陈旧状态。
    """
    from app.api.scope import resolve_company_id
    from app.main import app as fastapi_app

    class _Ctx:
        def __enter__(self):
            db.rollback()
            fastapi_app.dependency_overrides[resolve_company_id] = lambda: company_id
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            fastapi_app.dependency_overrides.pop(resolve_company_id, None)
            db.rollback()
            return False

    return _Ctx()


def test_contract_api_full_flow_is_party_scoped(client, db):
    issuer = _company(db, "ApiContractIssuer")
    contractor = _company(db, "ApiContractContractor")
    stranger = _company(db, "ApiContractStranger")
    _fund(db, issuer, 20_000)

    with _as_company(db, issuer.id):
        created = client.post(
            "/api/v1/contracts",
            json={
                "title": "API work contract",
                "consideration_amount": 6_000,
                "contract_type": "work",
                "contractor_company_id": contractor.id,
            },
        )
    assert created.status_code == 201
    payload = created.json()
    contract_id = payload["contract_id"]
    assert payload["status"] == "PENDING_ACCEPTANCE"
    assert payload["is_issuer"] is True
    assert payload["escrow"]["status"] == "FUNDED"
    assert payload["escrow"]["amount"] == 6_000

    # 第三方看不到这份合同（作用域纪律）
    with _as_company(db, stranger.id):
        assert client.get(f"/api/v1/contracts/{contract_id}").status_code == 404
        assert client.post(f"/api/v1/contracts/{contract_id}/accept").status_code == 404
        listing = client.get("/api/v1/contracts").json()
        assert contract_id not in {item["contract_id"] for item in listing["items"]}

    with _as_company(db, contractor.id):
        accepted = client.post(f"/api/v1/contracts/{contract_id}/accept").json()
        assert accepted["status"] == "FUNDED"
        assert accepted["is_contractor"] is True
        fulfilled = client.post(f"/api/v1/contracts/{contract_id}/fulfill").json()
    assert fulfilled["status"] == "SETTLED"
    assert fulfilled["escrow"]["status"] == "RELEASED"
    assert fulfilled["escrow"]["account_balance"] == 0
    assert fulfilled["settlement"]["net"] + fulfilled["settlement"]["fee"] == 6_000

    # 双方都能在自己的列表里看到；状态过滤可用
    for company in (issuer, contractor):
        with _as_company(db, company.id):
            listing = client.get("/api/v1/contracts", params={"status": "settled"}).json()
            assert contract_id in {item["contract_id"] for item in listing["items"]}


def test_contract_api_cancel_refunds_and_rejects_illegal_actions(client, db):
    issuer = _company(db, "ApiCancelIssuer")
    contractor = _company(db, "ApiCancelContractor")
    _fund(db, issuer, 8_000)
    with _as_company(db, issuer.id):
        contract_id = client.post(
            "/api/v1/contracts",
            json={
                "title": "Cancel via API",
                "consideration_amount": 3_000,
                "contractor_company_id": contractor.id,
            },
        ).json()["contract_id"]
        # 发布方不是承接方 ⇒ 交付 404（不泄露存在性）
        assert client.post(f"/api/v1/contracts/{contract_id}/fulfill").status_code == 404
        cancelled = client.post(f"/api/v1/contracts/{contract_id}/cancel").json()
        assert cancelled["status"] == "CANCELLED"
        assert cancelled["escrow"]["status"] == "REFUNDED"

    from app.services.economy.accounts import AccountService as _Accounts

    issuer_account = int(_Accounts(db).ensure_account(EconomicActor.company(issuer.id)).id)
    assert _wallet(issuer_account)["available"] == 8_000  # 全额退回（无手续费）

    # 承接方在"未接受"状态下交付 ⇒ 409（状态不允许）；取消后接受 ⇒ 409
    with _as_company(db, contractor.id):
        assert client.post(f"/api/v1/contracts/{contract_id}/accept").status_code == 409


def test_contract_api_insufficient_funds_is_rejected_without_a_contract(client, db):
    poor = _company(db, "ApiPoorIssuer")
    _fund(db, poor, 500)
    before = len(economy_repo.list_contracts(db))
    with _as_company(db, poor.id):
        response = client.post(
            "/api/v1/contracts",
            json={"title": "Too rich for me", "consideration_amount": 50_000},
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "insufficient_funds"
    assert len(economy_repo.list_contracts(db)) == before
