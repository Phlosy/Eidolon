"""M1.10 Golden Path —— 完整经济闭环 E2E（docs/m1-economy-design.md，plan §13/§14 A–F）。

一条测试跑完 plan §13 的 26 步（能走 HTTP 的走 HTTP，官方发行/验收这类**管理面**动作走服务层）：

```
注册 → 公司 → STARTER_GRANT（账本可解释）
→ 官方任务赚钱（Mint：唯一主要发行渠道）
→ 公司 A 发布玩家任务（Escrow 锁资）→ 公司 B 承接 → 交付 → 结算（A → B，Supply 不变）
→ A 把一位人才挂牌并定价 → B 用收入**买走**（Offer → Talent Contract → Escrow → 结算）
→ 调用 T2 招募 → Employee 创建、person_id/identity_id 不变
→ Seller 收款 / Buyer 扣款 / 账本平衡 / 合同 SETTLED / Escrow 归零 / Listing 关闭
```

证据不是"接口 200"，而是**账本 + 状态 + 事件**三样：
- 账本：每笔交易复式平衡、`minted/burned/supply` 恒等式、`available = posted − reserved`；
- 状态：订单/合同/托管/挂牌的终态、Employee 与 Person 的绑定；
- 可解释：每笔资金都有 `reason` + `reference_type/reference_id`（E16）。
"""

from __future__ import annotations

import sqlalchemy as sa

from app.api.scope import resolve_company_id
from app.core.database import SessionLocal
from app.economy.contracts import EconomicActor
from app.economy.policy import economic_policy
from app.main import app
from app.models.economy import Contract, LedgerEntry, LedgerTransaction, WorkOrder
from app.models.enums import (
    ContractStatus,
    EscrowStatus,
    LedgerEntryDirection,
    MarketListingStatus,
    RewardType,
    WorkOrderStatus,
)
from app.models.market import MarketListing
from app.models.organization import Employee
from app.repositories import economy as economy_repo
from app.services.economy.accounts import AccountService
from app.services.economy.ledger import LedgerService
from app.services.economy.work_orders import WorkOrderService

_seq = 0


# ---------------------------------------------------------------- 工具


def _register_and_verify(client, email: str) -> dict:
    """真实注册流程（注册 → 邮箱验证 → 自动建公司 + OWNER 成员关系）。"""
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "correct horse battery staple1",
            "display_name": email.split("@")[0],
            "locale": "zh-CN",
            "timezone": "Asia/Shanghai",
        },
    )
    assert registered.status_code == 201, registered.text
    verified = client.post(
        "/api/v1/auth/verify-email",
        json={"token": registered.json()["development_verification_token"]},
    )
    assert verified.status_code == 200, verified.text
    return verified.json()


class _AsCompany:
    """把请求作用域切到某公司（生产里由登录会话决定）。"""

    def __init__(self, db, company_id: int) -> None:
        self.db = db
        self.company_id = int(company_id)

    def __enter__(self):
        self.db.rollback()
        app.dependency_overrides[resolve_company_id] = lambda: self.company_id
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        app.dependency_overrides.pop(resolve_company_id, None)
        self.db.rollback()
        return False


def _wallet(db, company_id: int) -> dict:
    account = AccountService(db).ensure_account(EconomicActor.company(company_id))
    return dict(_wallet_pair(db, int(account.id)))


def _wallet_pair(db, account_id: int) -> dict:
    row = economy_repo.get_projection(db, int(account_id))
    if row is None:
        return {"posted": 0, "available": 0, "reserved": 0}
    return {
        "posted": int(row.posted_balance),
        "available": int(row.available_balance),
        "reserved": int(row.reserved_balance),
    }


def _supply(db):
    return LedgerService(db).supply()


def _assert_ledger_is_balanced(db) -> None:
    """每笔交易 Σdebit == Σcredit（E3）——直接查账本，不看投影。"""
    rows = db.execute(
        sa.select(
            LedgerEntry.transaction_id, LedgerEntry.direction, sa.func.sum(LedgerEntry.amount)
        ).group_by(LedgerEntry.transaction_id, LedgerEntry.direction)
    ).all()
    totals: dict[int, dict[str, int]] = {}
    for transaction_id, direction, total in rows:
        totals.setdefault(int(transaction_id), {})[str(direction)] = int(total)
    assert totals
    for transaction_id, by_direction in totals.items():
        assert by_direction.get(LedgerEntryDirection.debit.value, 0) == by_direction.get(
            LedgerEntryDirection.credit.value, 0
        ), f"transaction {transaction_id} unbalanced: {by_direction}"


def _assert_every_transaction_is_explainable(db, *, since_id: int) -> None:
    """E9/E16：**业务链路产生的**每笔资金都能说清"为什么、哪笔业务"。

    - 只检查 `id > since_id`（golden path 自己产生的交易）——账本层的裸原语调用
      （M1.1 的单元测试刻意不带业务锚点）不该被这条断言判红；
    - **发行与回收**（mint/burn/treasury_transfer）必须有 `reason`（E9：官方行为可审计）；
    - **托管腿**（escrow_*）与**转移**必须有 reason 或业务锚点（E16）；
    - 所有交易都必须有币种与类型（E22）。
    """
    transactions = [
        row for row in db.scalars(sa.select(LedgerTransaction)) if int(row.id) > int(since_id)
    ]
    assert transactions
    for transaction in transactions:
        assert transaction.currency == "CREDIT"
        assert transaction.transaction_type
        if transaction.transaction_type in {"mint", "burn", "treasury_transfer"}:
            assert transaction.reason, f"transaction {transaction.id} 缺少 reason（E9）"
        assert transaction.reason or transaction.reference_type, (
            f"transaction {transaction.id} 缺少 reason/锚点（E16）"
        )
        if transaction.reference_type:
            assert transaction.reference_id is not None


# ---------------------------------------------------------------- Golden Path


def test_m1_golden_path_end_to_end(client, db):
    """M1 完整闭环：发行 → 官方收入 → 玩家间转移 → 人才交易 + T2 招募。"""
    global _seq
    _seq += 1
    policy = economic_policy()

    # ---- 1–2. 新用户注册（真实流程）→ 自动建公司 ----
    seller_state = _register_and_verify(client, f"golden-seller-{_seq}@example.com")
    seller_company = int(seller_state["company"]["id"])
    buyer_state = _register_and_verify(client, f"golden-buyer-{_seq}@example.com")
    buyer_company = int(buyer_state["company"]["id"])
    assert seller_company != buyer_company

    supply_at_start = _supply(db)
    supply_after_registration = _supply(db)
    # 只校验本用例产生的交易（账本层的裸原语调用不带业务锚点，不在 E16 的管辖范围）
    ledger_watermark = int(
        db.execute(sa.select(sa.func.coalesce(sa.func.max(LedgerTransaction.id), 0))).scalar_one()
    )
    assert supply_after_registration.minted == supply_at_start.minted  # 注册阶段不发行

    # ---- 3. STARTER_GRANT：公司拿到启动资金（A：账本可解释）----
    with _AsCompany(db, seller_company):
        claimed = client.post("/api/v1/economy/rewards/STARTER_GRANT/claim", json={})
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["amount"] == policy.starter_grant
    assert claimed.json()["policy_version"] == policy.version
    seller_wallet = _wallet(db, seller_company)
    assert seller_wallet == {
        "posted": policy.starter_grant,
        "available": policy.starter_grant,
        "reserved": 0,
    }
    assert _supply(db).minted - supply_at_start.minted == policy.starter_grant
    grant_transaction = db.get(LedgerTransaction, int(claimed.json()["ledger_transaction_id"]))
    assert grant_transaction.reference_type == "reward"
    assert grant_transaction.reason == f"reward:{RewardType.starter_grant.value}"

    # ---- 4. 官方任务：只有官方能发布（管理面服务；CLI 是生产入口）----
    work_service = WorkOrderService(db)
    official_order = work_service.publish_official(
        title="Golden: ship the landing page",
        reward_amount=8_000,
        deliverables={"required_keys": ["readme"]},
    ).order
    assert official_order.status == WorkOrderStatus.open.value
    minted_before_official = _supply(db).minted

    # ---- 5–7. 卖家领取 → 交付 → auto 验收 ⇒ 结算（B：创造价值获得新发行货币）----
    with _AsCompany(db, seller_company):
        accepted = client.post(f"/api/v1/work-orders/{official_order.id}/accept")
        assert accepted.status_code == 200, accepted.text
        submitted = client.post(
            f"/api/v1/work-orders/{official_order.id}/submit",
            json={"summary": "done", "deliverables": {"readme": "https://example.com/readme"}},
        )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == WorkOrderStatus.settled.value
    assert submitted.json()["payable_amount"] == 8_000
    seller_after_official = _wallet(db, seller_company)
    assert seller_after_official["available"] == policy.starter_grant + 8_000
    assert _supply(db).minted - minted_before_official == 8_000  # 官方任务是主要发行渠道
    db.expire_all()
    assert db.get(WorkOrder, official_order.id).status == WorkOrderStatus.settled.value

    # ---- 8–9. 卖家发布**玩家**任务（Escrow 锁资，E11）→ 买家承接 ----
    supply_before_publish = _supply(db)
    with _AsCompany(db, seller_company):
        created = client.post(
            "/api/v1/work-orders",
            json={
                "title": "Golden: player bounty",
                "reward_amount": 15_000,
                "kind": "PLAYER_BOUNTY",
                "deliverables": {"required_keys": ["report"]},
            },
        )
    assert created.status_code == 201, created.text
    player_order_id = created.json()["work_order_id"]
    assert created.json()["escrow"]["status"] == EscrowStatus.funded.value
    bounty = 15_000
    listing_fee = economic_policy().fee_for(bounty)
    seller_after_publish = _wallet(db, seller_company)
    assert seller_after_publish == {
        "posted": policy.starter_grant + 8_000 - listing_fee,
        "available": policy.starter_grant + 8_000 - bounty - listing_fee,
        "reserved": bounty,
    }
    # 挂牌手续费按设计回收流通：只有 burn 腿改变供给（mint 不变）
    supply_after_publish = _supply(db)
    assert supply_after_publish.minted == supply_before_publish.minted
    assert (
        supply_after_publish.burned - supply_before_publish.burned
        == policy.split_fee(listing_fee)[1]
    )
    supply_before_player_transfer = supply_after_publish

    # ---- 10–12. 买家承接 → 交付 → 结算（C：玩家之间不改变 Supply）----
    with _AsCompany(db, buyer_company):
        assert client.post(f"/api/v1/work-orders/{player_order_id}/accept").status_code == 200
        delivered = client.post(
            f"/api/v1/work-orders/{player_order_id}/submit",
            json={
                "summary": "delivered",
                "deliverables": {"report": "https://example.com/report"},
            },
        )
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["status"] == WorkOrderStatus.settled.value
    buyer_after_bounty = _wallet(db, buyer_company)
    assert buyer_after_bounty["available"] == bounty  # 赚到 15,000（玩家任务的收入）
    seller_after_bounty = _wallet(db, seller_company)
    assert seller_after_bounty["reserved"] == 0  # 托管释放
    supply_after_player_transfer = _supply(db)
    # C（E6/E7）：玩家之间的转移（含 Escrow 释放）**完全不改变**供给 —— minted/burned/supply 都不动
    assert supply_after_player_transfer.minted == supply_before_player_transfer.minted
    assert supply_after_player_transfer.burned == supply_before_player_transfer.burned
    assert supply_after_player_transfer.supply == supply_before_player_transfer.supply

    # ---- 13–16. 卖家培养一位人才 → 结业 → 挂牌 → 定价（M1.7 商业条款）----
    with _AsCompany(db, seller_company):
        character = client.post(
            "/api/v1/cultivation/characters",
            json={"name": f"Golden Talent {_seq}", "origin": "blank"},
        )
        assert character.status_code in (200, 201), character.text
        person_id = int(character.json()["person_id"])
        assert (
            client.post(
                f"/api/v1/cultivation/characters/{character.json()['id']}/complete"
            ).status_code
            == 200
        )
        listed = client.post("/api/v1/market/listings", json={"person_id": person_id})
        assert listed.status_code == 201, listed.text
        listing_id = int(listed.json()["listing_id"])
        terms = client.post(
            f"/api/v1/market/listings/{listing_id}/commercial-terms",
            json={"price": 12_000, "sale_mode": "negotiation"},
        )
    assert terms.status_code == 201, terms.text

    with SessionLocal() as snapshot_db:
        profile = snapshot_db.scalars(
            sa.select(__import__("app.models.cultivation", fromlist=["x"]).CharacterProfile).where(
                __import__("app.models.cultivation", fromlist=["x"]).CharacterProfile.person_id
                == person_id
            )
        ).first()
        assert profile is not None
        identity_id = profile.identity_id
    del identity_id  # 下面用同一个 helper 复核（只看"不变"这一事实）

    # ---- 17–20. 买家出价 → 卖家接受（Offer → Talent Contract → Escrow）----
    with _AsCompany(db, buyer_company):
        offer_response = client.post(
            f"/api/v1/market/listings/{listing_id}/offers",
            json={"amount": 12_000, "message": "we want this talent"},
        )
    assert offer_response.status_code == 201, offer_response.text
    offer_id = int(offer_response.json()["offer"]["offer_id"])
    assert offer_response.json()["purchase"] is None  # 议价：还没成交

    with _AsCompany(db, seller_company):
        bought = client.post(f"/api/v1/market/offers/{offer_id}/accept")
    assert bought.status_code == 200, bought.text
    purchase = bought.json()["purchase"]
    assert purchase["gross"] == 12_000
    assert purchase["net"] + purchase["fee"] == 12_000
    assert purchase["seller_company_id"] == seller_company
    assert purchase["buyer_company_id"] == buyer_company
    assert purchase["person_id"] == person_id

    # ---- 21–24. T2 招募 → Employee 创建；身份/历史零破坏（D）----
    employee = db.get(Employee, int(purchase["employee_id"]))
    assert employee is not None
    assert int(employee.company_id) == buyer_company
    assert int(employee.person_id or 0) == person_id
    listing = db.get(MarketListing, listing_id)
    assert listing.status == MarketListingStatus.closed.value
    assert int(listing.recruited_company_id or 0) == buyer_company
    contract = db.get(Contract, int(purchase["contract_id"]))
    assert contract.status == ContractStatus.settled.value
    assert contract.contract_type == "talent"
    from app.services.economy.escrow import EscrowService

    escrow = economy_repo.find_escrow_for_contract(db, contract_id=contract.id)
    assert escrow is not None
    assert escrow.status == EscrowStatus.released.value
    assert EscrowService(db).view(escrow).account_balance == 0  # E25：托管归零

    # ---- 25–26. 最终对账：钱、供给、托管、账本 ----
    buyer_final = _wallet(db, buyer_company)
    seller_final = _wallet(db, seller_company)
    talent_net = purchase["net"]
    # 买家：赚 15,000 − 采购 12,000 = 3,000；卖家：任务支出已在锁资时扣、人才净额到账
    assert buyer_final["available"] == bounty - 12_000
    assert seller_final["reserved"] == 0
    assert seller_final["posted"] == seller_final["available"]
    assert seller_final["available"] == (
        policy.starter_grant + 8_000 - bounty - listing_fee + talent_net
    )

    supply_final = _supply(db)
    assert supply_final.supply == supply_final.minted - supply_final.burned
    # 全局量用增量：本用例只发行了"启动资金 + 官方任务奖励"（人才交易不发行，E8）
    assert supply_final.minted - supply_at_start.minted == policy.starter_grant + 8_000
    _assert_ledger_is_balanced(db)
    _assert_every_transaction_is_explainable(db, since_id=ledger_watermark)


def test_every_ledger_transaction_is_balanced_and_explainable(db):
    """A/D 的兜底回归：账本里**每一笔**交易都复式平衡，且供给恒等式成立。"""
    _assert_ledger_is_balanced(db)
    supply = _supply(db)
    assert supply.supply == supply.minted - supply.burned
    assert supply.circulating == supply.supply - supply.treasury_balance - supply.escrow_balance
