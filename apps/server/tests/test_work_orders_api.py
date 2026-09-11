"""M1.3 官方工作市场 API（plan §4/M1.3）。

锁住玩家的写面：
- 读：`GET /work-orders`（在招市场 / `mine=true` 本公司承接）、`GET /work-orders/{id}`（详情，
  提交/验收记录只对承接方返回 —— 别人的交付物不属于你）；
- 写：`POST /work-orders/{id}/accept`（并发只有一个赢家，重复领取幂等 200）、
  `POST /work-orders/{id}/submit`（auto 验收 ⇒ 结算 ⇒ 公司余额增加）；
- **发布/验收/结算没有玩家端点**（§32 三层边界：系统/管理面走 CLI）——
  `POST /work-orders`（发布）、`/evaluate`、`/settle` 一律 404/405；
- 过期订单不能领取（409 `order_expired`）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.api.scope import resolve_company_id
from app.core.database import SessionLocal
from app.economy.contracts import EconomicActor
from app.main import app
from app.models.enums import EvaluationMode, EvaluationVerdict, WorkOrderStatus
from app.models.organization import Company
from app.repositories import economy as economy_repo
from app.services.economy.accounts import AccountService
from app.services.economy.work_orders import WorkOrderService

_seq = 0


def _company(db, name: str = "WoApiCo") -> Company:
    global _seq
    _seq += 1
    company = Company(name=f"{name} {_seq}", slug=f"wo-api-{_seq}", description="")
    db.add(company)
    db.commit()
    return company


class _AsCompany:
    """切换请求公司作用域，并管理测试会话的事务边界。

    SQLite 下**测试会话要保持"无开放事务"**：HTTP 写入是另一个连接，
    测试侧挂着读事务会 `database is locked`；同时 rollback 让 identity map 过期，
    避免断言读到 API 写入前的陈旧对象。
    """

    def __init__(self, db=None, company_id: int | None = None) -> None:
        # 兼容两种调用：_AsCompany(company_id) / _AsCompany(db, company_id)
        if company_id is None:
            company_id = int(db)
            db = None
        self.db = db
        self.company_id = int(company_id)

    def __enter__(self):
        if self.db is not None:
            self.db.rollback()
        app.dependency_overrides[resolve_company_id] = lambda: self.company_id
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        app.dependency_overrides.pop(resolve_company_id, None)
        if self.db is not None:
            self.db.rollback()
        return False


def _publish(db, **kwargs):
    return (
        WorkOrderService(db)
        .publish_official(
            title=kwargs.pop("title", "API bounty"),
            reward_amount=kwargs.pop("reward_amount", 4_000),
            deliverables=kwargs.pop("deliverables", {"required_keys": ["readme"]}),
            **kwargs,
        )
        .order
    )


def _available(db, company: Company) -> int:
    """余额读**独立短会话**（HTTP 写入来自另一条连接，测试会话易读到陈旧快照）。"""
    with SessionLocal() as session:
        account = AccountService(session).ensure_account(EconomicActor.company(company.id))
        projection = economy_repo.get_projection(session, int(account.id))
        return int(projection.available_balance) if projection else 0


def _fund(db, company: Company, amount: int) -> int:
    """给公司发启动资金（独立提交），返回其 actor 账户 id。"""
    from app.services.economy.monetary import MonetaryAuthority

    MonetaryAuthority(db).mint(
        actor=EconomicActor.company(company.id), amount=amount, reason="test"
    )
    return int(AccountService(db).ensure_account(EconomicActor.company(company.id)).id)


def _fee(amount: int) -> int:
    """挂牌手续费（M1.5 §7：市场手续费 Sink）—— 断言里按政策算，不写死数字。"""
    from app.economy.policy import economic_policy

    return economic_policy().fee_for(amount)


def _order_status(order_id: int) -> str:
    with SessionLocal() as session:
        order = economy_repo.get_work_order(session, order_id)
        assert order is not None
        return order.status


def test_market_lists_open_orders_and_hides_other_companies_work(client, db):
    publisher_service = WorkOrderService(db)
    order = _publish(db, title="Public bounty")
    mine = _publish(db, title="Taken bounty", reward_amount=2_000)
    company = _company(db, "WoMarketMine")
    other = _company(db, "WoMarketOther")

    with _AsCompany(db, company.id):
        market = client.get("/api/v1/work-orders").json()
        assert order.id in {item["work_order_id"] for item in market["items"]}
        assert mine.id in {item["work_order_id"] for item in market["items"]}
        assert client.post(f"/api/v1/work-orders/{mine.id}/accept").status_code == 200

        # 领了之后：市场里不再出现（OPEN），mine=true 里能看到
        market_after = client.get("/api/v1/work-orders").json()
        assert mine.id not in {item["work_order_id"] for item in market_after["items"]}
        my_orders = client.get("/api/v1/work-orders", params={"mine": "true"}).json()
        assert {item["work_order_id"] for item in my_orders["items"]} == {mine.id}
        assert my_orders["items"][0]["is_mine"] is True

    # 另一家公司看详情：能看到公开字段，但看不到别人的提交/验收记录
    with _AsCompany(db, other.id):
        detail = client.get(f"/api/v1/work-orders/{mine.id}").json()
        assert detail["is_mine"] is False
        assert detail["submissions"] == []
        assert detail["evaluations"] == []
        # 别人（同一订单已被领）不能重复领取
        conflict = client.post(f"/api/v1/work-orders/{mine.id}/accept")
        assert conflict.status_code == 409
        assert conflict.json()["detail"] == "order_already_taken"
    del publisher_service


def test_accept_and_submit_flow_pays_the_company(client, db):
    company = _company(db, "WoFlow")
    order = _publish(db, title="Flow bounty", reward_amount=6_000)
    before = _available(db, company)

    with _AsCompany(db, company.id):
        accepted = client.post(f"/api/v1/work-orders/{order.id}/accept").json()
        assert accepted["status"] == WorkOrderStatus.accepted.value
        assert accepted["assignee_company_id"] == company.id
        # 重复领取幂等
        again = client.post(f"/api/v1/work-orders/{order.id}/accept").json()
        assert again["status"] == WorkOrderStatus.accepted.value

        submitted = client.post(
            f"/api/v1/work-orders/{order.id}/submit",
            json={"summary": "delivered", "deliverables": {"readme": "https://x/readme"}},
        ).json()
        assert submitted["status"] == WorkOrderStatus.settled.value  # auto 验收 ⇒ 自动结算
        assert submitted["payable_amount"] == 6_000
        assert len(submitted["submissions"]) == 1
        assert len(submitted["evaluations"]) == 1
        assert submitted["evaluations"][0]["verdict"] == "approved"

        balance = client.get("/api/v1/economy/balance").json()

    assert _available(db, company) - before == 6_000
    assert balance["available_balance"] >= 6_000
    grants = economy_repo.list_reward_grants(db, actor_ref=company.id)
    assert any(grant.reference_key == f"work_order:{order.id}" for grant in grants)


def test_manual_order_stays_unpaid_until_internal_approval(client, db):
    company = _company(db, "WoManual")
    order = _publish(
        db,
        title="Manual bounty",
        reward_amount=3_000,
        evaluation_mode=EvaluationMode.manual,
    )
    with _AsCompany(db, company.id):
        client.post(f"/api/v1/work-orders/{order.id}/accept")
        submitted = client.post(
            f"/api/v1/work-orders/{order.id}/submit",
            json={"summary": "v1", "deliverables": {"readme": "x"}},
        ).json()
        assert submitted["status"] == WorkOrderStatus.submitted.value
        assert submitted["evaluations"] == []
        # 玩家没有任何验收/结算入口
        assert client.post(f"/api/v1/work-orders/{order.id}/evaluate", json={}).status_code in (
            404,
            405,
        )
        assert client.post(f"/api/v1/work-orders/{order.id}/settle").status_code in (404, 405)
    assert _available(db, company) == 0  # 未验收 ⇒ 一分钱不发

    # 管理面（CLI/service）验收 ⇒ 通过后自动结算（独立会话：拿到 API 写入后的最新状态）
    with SessionLocal() as session:
        settled, evaluation = WorkOrderService(session).evaluate(
            order.id,
            verdict=EvaluationVerdict.approved,  # manual 模式必须给出判定（不做"默认通过"）
            score=90,
            bonuses={"quality": 500},
        )
    assert settled.status == WorkOrderStatus.settled.value
    assert _available(db, company) == 3_500
    assert evaluation.verdict == "approved"


def test_cross_company_cannot_submit_or_read_deliverables(client, db):
    owner = _company(db, "WoOwner")
    stranger = _company(db, "WoStranger")
    order = _publish(db, title="Private delivery", reward_amount=2_000)
    with _AsCompany(db, owner.id):
        client.post(f"/api/v1/work-orders/{order.id}/accept")
        client.post(
            f"/api/v1/work-orders/{order.id}/submit",
            json={"summary": "internal notes", "deliverables": {"readme": "secret-body"}},
        )
    with _AsCompany(db, stranger.id):
        detail = client.get(f"/api/v1/work-orders/{order.id}").json()
        assert detail["submissions"] == []
        assert "secret-body" not in str(detail)
        response = client.post(f"/api/v1/work-orders/{order.id}/submit", json={"summary": "steal"})
        assert response.status_code == 404
        assert response.json()["detail"] == "not_your_order"


def test_expired_and_unknown_orders_are_rejected(client, db):
    company = _company(db, "WoExpired")
    stale = _publish(
        db,
        title="Stale bounty",
        reward_amount=1_000,
        deadline_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    with _AsCompany(db, company.id):
        expired = client.post(f"/api/v1/work-orders/{stale.id}/accept")
        assert expired.status_code == 409
        assert expired.json()["detail"] == "order_expired"
        missing = client.get("/api/v1/work-orders/999999")
        assert missing.status_code == 404
        assert client.post("/api/v1/work-orders/999999/accept").status_code == 404
    assert _order_status(stale.id) == WorkOrderStatus.expired.value


def test_official_kinds_cannot_be_published_by_players(client, db):
    """`POST /work-orders` 只能发**玩家**订单（Escrow 锁资）；官方发行必须走 CLI（M1.3）。"""
    company = _company(db, "WoNoWrite")
    _fund(db, company, 10_000)
    official_before = len(economy_repo.list_work_orders(db, kinds=("OFFICIAL_BOUNTY",)))
    with _AsCompany(db, company.id):
        response = client.post(
            "/api/v1/work-orders",
            json={
                "title": "free money",
                "reward_amount": 9_999,
                "kind": "OFFICIAL_BOUNTY",  # 想印钱 ⇒ 明确拒绝
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"] == "kind_not_player_kind"
        assert client.get("/api/v1/work-orders").status_code == 200  # 读面确实存在
    # 也没有产生任何官方订单（共享测试库：只看增量），更没有任何 mint
    assert len(economy_repo.list_work_orders(db, kinds=("OFFICIAL_BOUNTY",))) == official_before


def test_market_pagination_and_status_filter(client, db):
    company = _company(db, "WoPaging")
    orders = [_publish(db, title=f"Paged {index}", reward_amount=1_000) for index in range(3)]
    with _AsCompany(db, company.id):
        page = client.get("/api/v1/work-orders", params={"limit": 2}).json()
        assert len(page["items"]) == 2
        assert page["limit"] == 2 and page["offset"] == 0
        assert page["total"] >= 3
        # 按状态过滤（OPEN 与终态）
        client.post(f"/api/v1/work-orders/{orders[0].id}/accept")
        settled = client.get(
            "/api/v1/work-orders", params={"status": "accepted", "mine": "true"}
        ).json()
        assert {item["work_order_id"] for item in settled["items"]} == {orders[0].id}


# ---------------------------------------------------------------- 玩家市场（M1.4）


def test_player_publish_locks_funds_and_exposes_escrow(client, db):
    issuer = _company(db, "PlayerApiIssuer")
    issuer_account = _fund(db, issuer, 20_000)
    with _AsCompany(db, issuer.id):
        created = client.post(
            "/api/v1/work-orders",
            json={
                "title": "Player task",
                "reward_amount": 6_000,
                "kind": "PLAYER_BOUNTY",
                "deliverables": {"required_keys": ["readme"]},
            },
        )
    assert created.status_code == 201
    payload = created.json()
    assert payload["funding_mode"] == "player_escrow"
    assert payload["status"] == "OPEN"
    assert payload["is_issuer"] is True
    assert payload["escrow"]["status"] == "FUNDED"
    assert payload["escrow"]["amount"] == 6_000
    assert payload["escrow"]["account_balance"] == 6_000

    # 钱已锁：可花减少（奖励 + 挂牌手续费）、锁定增加；总资产只减少手续费（真实支出）
    assert _available(db, issuer) == 20_000 - 6_000 - _fee(6_000)
    with SessionLocal() as session:
        account = economy_repo.get_account(session, issuer_account)
        projection = economy_repo.get_projection(session, int(account.id))
        assert int(projection.available_balance) == 20_000 - 6_000 - _fee(6_000)
        assert int(projection.reserved_balance) == 6_000
        assert int(projection.posted_balance) == 20_000 - _fee(6_000)


def test_player_publish_without_funds_is_rejected(client, db):
    issuer = _company(db, "PlayerApiPoor")
    _fund(db, issuer, 1_000)
    orders_before = len(economy_repo.list_work_orders(db))
    with _AsCompany(db, issuer.id):
        response = client.post(
            "/api/v1/work-orders", json={"title": "too expensive", "reward_amount": 50_000}
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "insufficient_funds"
    # E11：没有"已发布但没锁资"的订单，余额也没变
    assert len(economy_repo.list_work_orders(db)) == orders_before
    assert _available(db, issuer) == 1_000


def test_player_order_full_flow_between_companies(client, db):
    issuer = _company(db, "PlayerFlowIssuer")
    contractor = _company(db, "PlayerFlowContractor")
    _fund(db, issuer, 10_000)
    with _AsCompany(db, issuer.id):
        order_id = client.post(
            "/api/v1/work-orders", json={"title": "Deliver this", "reward_amount": 4_000}
        ).json()["work_order_id"]

    with _AsCompany(db, contractor.id):
        assert client.post(f"/api/v1/work-orders/{order_id}/accept").status_code == 200
        detail = client.post(
            f"/api/v1/work-orders/{order_id}/submit",
            json={"summary": "done", "deliverables": {"readme": "x"}},
        ).json()
    assert detail["status"] == "SETTLED"
    assert detail["escrow"]["status"] == "RELEASED"
    assert detail["escrow"]["account_balance"] == 0  # E25：托管归零
    assert _available(db, issuer) == 10_000 - 4_000 - _fee(4_000)  # 锁资时已扣
    assert _available(db, contractor) == 4_000  # 落袋

    # 发布方视角：is_issuer=True 且能取消（已结算 ⇒ 拒绝）
    with _AsCompany(db, issuer.id):
        mine = client.get(f"/api/v1/work-orders/{order_id}").json()
        assert mine["is_issuer"] is True
        cancel = client.post(f"/api/v1/work-orders/{order_id}/cancel")
        assert cancel.status_code == 409
        assert cancel.json()["detail"].startswith("order_not_cancellable")


def test_player_cancel_refunds_and_is_issuer_only(client, db):
    issuer = _company(db, "PlayerCancelIssuer")
    stranger = _company(db, "PlayerCancelStranger")
    _fund(db, issuer, 8_000)
    with _AsCompany(db, issuer.id):
        order_id = client.post(
            "/api/v1/work-orders", json={"title": "Cancel me", "reward_amount": 3_000}
        ).json()["work_order_id"]
    assert _available(db, issuer) == 8_000 - 3_000 - _fee(3_000)

    with _AsCompany(db, stranger.id):
        denied = client.post(f"/api/v1/work-orders/{order_id}/cancel")
        assert denied.status_code == 404
        assert denied.json()["detail"] == "not_your_order"

    with _AsCompany(db, issuer.id):
        cancelled = client.post(f"/api/v1/work-orders/{order_id}/cancel").json()
        assert cancelled["status"] == "CANCELLED"
        assert cancelled["escrow"]["status"] == "REFUNDED"
    assert _available(db, issuer) == 8_000 - _fee(3_000)  # 奖励退回；手续费不退（§7）


def test_player_deadline_expiry_refunds_via_cli_scan(client, db):
    from datetime import UTC, datetime, timedelta

    issuer = _company(db, "PlayerExpiryIssuer")
    _fund(db, issuer, 5_000)
    with _AsCompany(db, issuer.id):
        order_id = client.post(
            "/api/v1/work-orders",
            json={
                "title": "Expiring",
                "reward_amount": 2_000,
                "deadline_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            },
        ).json()["work_order_id"]
    assert _available(db, issuer) == 5_000 - 2_000 - _fee(2_000)

    with SessionLocal() as session:
        expired = WorkOrderService(session).expire_overdue()
    assert expired >= 1
    assert _order_status(order_id) == WorkOrderStatus.expired.value
    assert _available(db, issuer) == 5_000 - _fee(2_000)
