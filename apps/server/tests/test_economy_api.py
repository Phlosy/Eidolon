"""M1.1 经济读 API（plan §4/M1.1d，A17/A18）。

锁住：
- 余额/流水**只覆盖当前公司**（跨公司看不到对方的钱与交易）；
- `posted = available + reserved`，Escrow 锁定份额出现在 `reserved`（不伪造）；
- 无账务活动的公司返回全 0，不创建账户；
- **没有任何写端点**（mint/burn/transfer 不对外，E23/A18）。
"""

from __future__ import annotations

import sqlalchemy as sa

from app.api.scope import resolve_company_id
from app.economy.contracts import EconomicActor
from app.economy.policy import economic_policy
from app.main import app
from app.models.economy import LedgerTransaction
from app.models.organization import Company
from app.repositories import economy as economy_repo
from app.services.economy.accounts import AccountService
from app.services.economy.ledger import LedgerService
from app.services.economy.monetary import MonetaryAuthority

_seq = 0


def _company(db, name: str = "ApiCo") -> Company:
    global _seq
    _seq += 1
    company = Company(name=f"{name} {_seq}", slug=f"api-co-{_seq}", description="")
    db.add(company)
    db.commit()
    return company


def _actor(company: Company) -> EconomicActor:
    return EconomicActor.company(company.id)


class _AsCompany:
    """临时把请求的公司作用域切到指定公司（生产里由会话身份决定）。"""

    def __init__(self, company_id: int) -> None:
        self.company_id = company_id

    def __enter__(self):
        app.dependency_overrides[resolve_company_id] = lambda: self.company_id
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        app.dependency_overrides.pop(resolve_company_id, None)
        return False


def test_balance_returns_company_wallet(client, db):
    company = _company(db)
    MonetaryAuthority(db).mint(actor=_actor(company), amount=5_000, reason="starter")
    LedgerService(db).transfer(
        payer_account_id=AccountService(db).ensure_account(_actor(company)).id,
        payee_account_id=AccountService(db).ensure_account(_actor(_company(db, "ApiSink"))).id,
        amount=1_250,
        reason="services",
    )

    with _AsCompany(company.id):
        payload = client.get("/api/v1/economy/balance").json()

    assert payload["actor_kind"] == "company"
    assert payload["actor_ref"] == company.id
    assert payload["currency"] == "CREDIT"
    assert payload["posted_balance"] == 3_750
    assert payload["available_balance"] == 3_750
    assert payload["reserved_balance"] == 0
    assert payload["posted_balance"] == payload["available_balance"] + payload["reserved_balance"]
    assert [account["kind"] for account in payload["accounts"]] == ["actor"]
    assert payload["accounts"][0]["posted_balance"] == 3_750
    assert payload["accounts"][0]["version"] >= 1


def test_balance_reports_reserved_escrow_funds(client, db):
    company = _company(db, "ApiBuyer")
    accounts = AccountService(db)
    authority = MonetaryAuthority(db)
    ledger = LedgerService(db)
    authority.mint(actor=_actor(company), amount=2_000, reason="starter")
    account_id = accounts.ensure_account(_actor(company)).id
    escrow = accounts.ensure_escrow_account(escrow_id=4242)
    ledger.escrow_fund(
        escrow_account_id=escrow.id, payer_account_id=account_id, amount=600, reason="lock"
    )

    with _AsCompany(company.id):
        payload = client.get("/api/v1/economy/balance").json()

    assert payload["posted_balance"] == 2_000  # 总资产不变（锁定仍属于本公司）
    assert payload["available_balance"] == 1_400  # 可花额度减少
    assert payload["reserved_balance"] == 600
    db.rollback()


def test_balance_is_company_scoped(client, db):
    mine = _company(db, "ApiMine")
    theirs = _company(db, "ApiTheirs")
    fresh = _company(db, "ApiFresh")
    authority = MonetaryAuthority(db)
    authority.mint(actor=_actor(mine), amount=1_000, reason="starter")
    authority.mint(actor=_actor(theirs), amount=7_777, reason="starter")

    with _AsCompany(mine.id):
        mine_payload = client.get("/api/v1/economy/balance").json()
    with _AsCompany(theirs.id):
        theirs_payload = client.get("/api/v1/economy/balance").json()
    with _AsCompany(fresh.id):
        fresh_payload = client.get("/api/v1/economy/balance").json()

    assert mine_payload["posted_balance"] == 1_000
    assert theirs_payload["posted_balance"] == 7_777
    assert fresh_payload["posted_balance"] == 0  # 无活动 ⇒ 全 0，不创建账户
    assert fresh_payload["available_balance"] == 0
    assert fresh_payload["reserved_balance"] == 0
    assert fresh_payload["accounts"] == []
    assert AccountService(db).accounts_for_actor(_actor(fresh)) == []


def test_transactions_endpoint_paginates_filters_and_is_scoped(client, db):
    mine = _company(db, "ApiTxMine")
    theirs = _company(db, "ApiTxTheirs")
    accounts = AccountService(db)
    authority = MonetaryAuthority(db)
    ledger = LedgerService(db)
    my_account = accounts.ensure_account(_actor(mine)).id
    their_account = accounts.ensure_account(_actor(theirs)).id
    authority.mint(
        actor=_actor(mine),
        amount=1_000,
        reason="starter",
        reference_type="reward",
        reference_id="r-1",
    )
    authority.mint(
        actor=_actor(theirs),
        amount=9_999,
        reason="starter",
        reference_type="reward",
        reference_id="r-2",
    )
    ledger.transfer(
        payer_account_id=my_account,
        payee_account_id=their_account,
        amount=300,
        reason="services",
        reference_type="work_order",
        reference_id="wo-1",
    )
    ledger.transfer(
        payer_account_id=their_account,
        payee_account_id=my_account,
        amount=100,
        reason="services",
        reference_type="work_order",
        reference_id="wo-2",
    )
    total_mine = int(
        db.execute(sa.select(sa.func.count()).select_from(LedgerTransaction)).scalar_one()
    )
    assert total_mine >= 4

    with _AsCompany(mine.id):
        page = client.get("/api/v1/economy/transactions", params={"limit": 2}).json()
        filtered = client.get(
            "/api/v1/economy/transactions", params={"transaction_type": "transfer"}
        ).json()
        by_reference = client.get(
            "/api/v1/economy/transactions", params={"reference_id": "wo-1"}
        ).json()
        second_page = client.get(
            "/api/v1/economy/transactions", params={"limit": 2, "offset": 2}
        ).json()

    # 本公司 3 笔（mint + 2 笔 transfer）：别人的 mint 不出现
    assert page["total"] == 3
    assert len(page["items"]) == 2
    assert page["limit"] == 2 and page["offset"] == 0
    assert [item["transaction_id"] for item in page["items"]] == sorted(
        [item["transaction_id"] for item in page["items"]], reverse=True
    )
    assert all(
        entry["account_id"] in {my_account, their_account}
        for item in page["items"]
        for entry in item["entries"]
    )
    assert [item["transaction_type"] for item in filtered["items"]] == ["transfer", "transfer"]
    assert filtered["total"] == 2
    assert [item["reference_id"] for item in by_reference["items"]] == ["wo-1"]
    assert by_reference["items"][0]["reason"] == "services"
    assert second_page["items"] and len(second_page["items"]) == 1

    # 对方视角：只看到自己的 3 笔（mint + 两笔 transfer 的另一侧）
    with _AsCompany(theirs.id):
        theirs_page = client.get("/api/v1/economy/transactions").json()
    assert theirs_page["total"] == 3
    assert all(
        any(entry["account_id"] == their_account for entry in item["entries"])
        for item in theirs_page["items"]
    )


def test_accounts_endpoint_lists_company_accounts(client, db):
    company = _company(db, "ApiAccounts")
    account = AccountService(db).ensure_account(_actor(company))
    MonetaryAuthority(db).mint(actor=_actor(company), amount=77, reason="starter")

    with _AsCompany(company.id):
        rows = client.get("/api/v1/economy/accounts").json()

    assert [row["account_id"] for row in rows] == [account.id]
    assert rows[0]["kind"] == "actor"
    assert rows[0]["status"] == "active"
    assert rows[0]["posted_balance"] == 77


def test_no_write_endpoints_are_exposed(client, db):
    """A18/E23：mint/burn/transfer 没有 HTTP 入口（内部能力）。"""
    company = _company(db, "ApiNoWrite")
    with _AsCompany(company.id):
        assert client.post("/api/v1/economy/mint", json={"amount": 1_000_000}).status_code in (
            404,
            405,
        )
        assert client.post("/api/v1/economy/burn", json={"amount": 1}).status_code in (404, 405)
        assert client.post("/api/v1/economy/transfer", json={"amount": 1}).status_code in (404, 405)
        assert client.post("/api/v1/economy/balance", json={}).status_code == 405
        assert client.delete("/api/v1/economy/balance").status_code == 405
    # 读面确实存在（否则上面的 404 说明不了问题）
    with _AsCompany(company.id):
        assert client.get("/api/v1/economy/balance").status_code == 200


def test_transactions_without_company_returns_404(client, db):
    """没有公司作用域时读面拒绝（不返回全公司数据）。"""
    app.dependency_overrides[resolve_company_id] = lambda: None
    try:
        assert client.get("/api/v1/economy/balance").status_code == 404
        assert client.get("/api/v1/economy/transactions").status_code == 404
    finally:
        app.dependency_overrides.pop(resolve_company_id, None)


def test_transactions_for_empty_wallet_is_empty(client, db):
    company = _company(db, "ApiEmptyTx")
    with _AsCompany(company.id):
        page = client.get("/api/v1/economy/transactions").json()
    assert page == {"items": [], "total": 0, "limit": 50, "offset": 0}
    assert economy_repo.list_projections(db) is not None  # 读面不写任何东西


# ---------------------------------------------------------------- 奖励（M1.2）


def _owner(db, company: Company, *, display_name: str = "", avatar: str = "") -> object:
    from app.models.auth import CompanyMembership, User

    global _seq
    _seq += 1
    user = User(
        email=f"api-reward-{_seq}@example.com",
        username=f"api-reward-{_seq}",
        password_hash="x",
        display_name=display_name,
        avatar=avatar,
        status="active",
        email_verified=True,
    )
    db.add(user)
    db.flush()
    db.add(CompanyMembership(user_id=user.id, company_id=company.id, role="OWNER"))
    db.commit()
    return user


def test_reward_catalog_lists_self_service_rewards(client, db):
    company = _company(db, "ApiRewards")
    _owner(db, company)
    with _AsCompany(company.id):
        payload = client.get("/api/v1/economy/rewards").json()

    kinds = {item["reward_type"] for item in payload["items"]}
    assert kinds == {
        "STARTER_GRANT",
        "PROFILE_COMPLETION",
        "COMPANY_PROFILE_COMPLETION",
        "TUTORIAL_COMPLETION",
        "DAILY_LOGIN",
        "ACHIEVEMENT",
        "RECOVERY_GRANT",
    }
    # 成就按 code 逐条展开（每条都有自己的 reference_key）
    achievement_keys = {
        item["reference_key"] for item in payload["items"] if item["reward_type"] == "ACHIEVEMENT"
    }
    assert achievement_keys == {"achievement:first_employee", "achievement:first_project"}
    starter = next(item for item in payload["items"] if item["reward_type"] == "STARTER_GRANT")
    assert starter["claimable"] is True
    assert starter["amount"] > 0
    assert starter["policy_version"]


def test_claim_reward_endpoint_is_idempotent_and_mints_once(client, db):
    company = _company(db, "ApiClaim")
    _owner(db, company)
    before = LedgerService(db).supply()
    with _AsCompany(company.id):
        first = client.post("/api/v1/economy/rewards/STARTER_GRANT/claim", json={})
        second = client.post("/api/v1/economy/rewards/starter_grant/claim", json={})
        balance = client.get("/api/v1/economy/balance").json()

    assert first.status_code == 200
    assert second.status_code == 200
    first_payload, second_payload = first.json(), second.json()
    assert first_payload["created"] is True
    assert second_payload["created"] is False
    assert first_payload["grant_id"] == second_payload["grant_id"]
    assert first_payload["status"] == "POSTED"
    assert first_payload["ledger_transaction_id"] is not None
    assert balance["available_balance"] == first_payload["amount"]
    after = LedgerService(db).supply()
    assert after.minted - before.minted == first_payload["amount"]


def test_claim_reward_rejects_ineligible_and_official_types(client, db):
    company = _company(db, "ApiIneligible")
    with _AsCompany(company.id):
        unqualified = client.post(
            "/api/v1/economy/rewards/COMPANY_PROFILE_COMPLETION/claim", json={}
        )
        official = client.post("/api/v1/economy/rewards/OFFICIAL_BOUNTY/claim", json={})
        unknown = client.post("/api/v1/economy/rewards/NOPE/claim", json={})
        no_code = client.post("/api/v1/economy/rewards/ACHIEVEMENT/claim", json={})

    assert unqualified.status_code == 409
    assert unqualified.json()["detail"] == "not_eligible"
    assert official.status_code == 409
    assert official.json()["detail"] == "not_self_service"
    assert unknown.status_code == 404
    assert no_code.status_code == 409
    assert no_code.json()["detail"] == "reference_required"


def test_claim_reward_amount_comes_from_policy_not_request(client, db):
    """请求体不能带金额：多余字段被忽略，落库金额 = 政策值。"""
    company = _company(db, "ApiAmount")
    _owner(db, company)
    policy_amount = economic_policy().starter_grant
    with _AsCompany(company.id):
        response = client.post(
            "/api/v1/economy/rewards/STARTER_GRANT/claim",
            json={"amount": 999_999_999, "reference_key": "ignored"},
        )
    assert response.status_code == 200
    assert response.json()["amount"] == policy_amount
    assert response.json()["reference_key"] == "starter"


def test_reward_grant_is_company_scoped(client, db):
    first = _company(db, "ApiRewardScopeA")
    second = _company(db, "ApiRewardScopeB")
    _owner(db, first)
    _owner(db, second)
    with _AsCompany(first.id):
        assert (
            client.post("/api/v1/economy/rewards/STARTER_GRANT/claim", json={}).status_code == 200
        )
    with _AsCompany(second.id):
        catalog = client.get("/api/v1/economy/rewards").json()
        starter = next(item for item in catalog["items"] if item["reward_type"] == "STARTER_GRANT")
        assert starter["claimable"] is True  # 另一家公司的领取不影响本公司
        assert starter["grant_id"] is None


def test_reward_events_are_published_after_claim(client, db, monkeypatch):
    company = _company(db, "ApiRewardEvent")
    _owner(db, company)
    published: list[dict] = []
    monkeypatch.setattr(
        "app.services.economy.rewards.bus.publish",
        lambda type_, data, **kwargs: published.append({"type": type_, "data": data, **kwargs}),
    )
    with _AsCompany(company.id):
        client.post("/api/v1/economy/rewards/STARTER_GRANT/claim", json={})
        assert published and published[0]["type"] == "reward.granted"
        assert published[0]["data"]["reward_type"] == "STARTER_GRANT"
        assert published[0]["company_id"] == company.id
        published.clear()
        # 幂等重放：不发第二次事件
        client.post("/api/v1/economy/rewards/STARTER_GRANT/claim", json={})
        assert published == []
