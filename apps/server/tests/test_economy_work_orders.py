"""M1.3 官方工作市场（docs/m1-economy-design.md §17/§18/§20/§24；plan §4/M1.3）。

断言的是**发行渠道不变量**：
- 一个官方 bounty 走完 OPEN → ACCEPTED → IN_PROGRESS → SUBMITTED → REVIEWING → APPROVED →
  SETTLED，钱 mint 到承接公司且 `total_minted` 增加（主要发行渠道）；
- 奖励 = base + Σbonus；bonus 必须是非负整数；
- **重复结算幂等**：同 `settlement_key` 不重复发钱（E12），订单与 grant 都只有一份；
- **E8**：玩家（公司）不能 mint —— 只有官方 `system_mint` 订单能发行；
  玩家类 kind 在 M1.4（Escrow 锁资）之前不可发布；
- **预算内发行**：单笔 ≤ `official_max_reward`，未结算承诺额 ≤ `official_outstanding_budget`；
- 状态机：非法迁移一律拒绝（M1.0 冻结表）；过期订单不能领取；
- 执行复用既有项目体系：`project_id` 只是引用，不重造项目。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

from app.core.database import SessionLocal
from app.economy.contracts import EconomicActor
from app.economy.policy import economic_policy
from app.models.economy import Evaluation, LedgerTransaction, WorkOrder
from app.models.enums import (
    EconomicActorKind,
    EvaluationMode,
    EvaluationVerdict,
    FundingMode,
    RewardType,
    WorkOrderKind,
    WorkOrderStatus,
)
from app.models.organization import Company
from app.repositories import economy as economy_repo
from app.services.economy.accounts import AccountService
from app.services.economy.evaluations import EvaluationError
from app.services.economy.ledger import LedgerService
from app.services.economy.projection import verify_wallet_projection
from app.services.economy.settlement import SettlementError, SettlementRequest, SettlementService
from app.services.economy.work_orders import WorkOrderError, WorkOrderService

_seq = 0


def _company(db, name: str = "WorkCo") -> Company:
    global _seq
    _seq += 1
    company = Company(name=f"{name} {_seq}", slug=f"work-co-{_seq}", description="")
    db.add(company)
    db.commit()
    return company


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


def _account(db, company: Company) -> int:
    return int(AccountService(db).ensure_account(EconomicActor.company(company.id)).id)


def _supply(db):
    return LedgerService(db).supply()


def _publish(db, *, reward: int = 5_000, required: list[str] | None = None, **kwargs):
    return WorkOrderService(db).publish_official(
        title=kwargs.pop("title", "Build a demo"),
        reward_amount=reward,
        requirements={"skills": ["python"]},
        deliverables={"required_keys": required or ["readme"]},
        **kwargs,
    )


# ---------------------------------------------------------------- 生命周期


def test_official_bounty_full_lifecycle_mints_to_assignee(db):
    company = _company(db, "BountyCo")
    account_id = _account(db, company)
    service = WorkOrderService(db)
    before = _supply(db)

    published = service.publish_official(
        title="Write the onboarding doc",
        reward_amount=5_000,
        deliverables={"required_keys": ["readme"]},
    )
    order = published.order
    assert published.created is True
    assert order.status == WorkOrderStatus.open.value
    assert order.funding_mode == FundingMode.system_mint.value
    assert order.issuer_actor_kind == EconomicActorKind.system.value
    assert order.policy_version == economic_policy().version
    assert order.code.startswith("OB-")

    accepted = service.accept(order.id, company_id=company.id)
    assert accepted.status == WorkOrderStatus.accepted.value
    assert int(accepted.assignee_actor_ref) == company.id

    submission, submitted, evaluation = service.submit(
        order.id,
        company_id=company.id,
        summary="done",
        deliverables={"readme": "https://example.com/readme"},
    )
    assert submission.attempt == 1
    assert evaluation is not None and evaluation.verdict == EvaluationVerdict.approved.value
    assert submitted.status == WorkOrderStatus.settled.value  # auto 验收通过 ⇒ 自动结算

    # 钱到账、供给增加、账本可追溯
    assert _wallet(account_id)["available"] == 5_000
    after = _supply(db)
    assert after.minted - before.minted == 5_000
    assert after.supply == after.minted - after.burned

    transaction = db.get(LedgerTransaction, int(submitted.settlement_transaction_id))
    assert transaction is not None
    assert transaction.reference_type == "work_order"
    assert transaction.reference_id == str(order.id)
    assert transaction.idempotency_key == f"work_order:{order.id}"

    grants = economy_repo.list_reward_grants(
        db, reward_type=RewardType.official_bounty.value, actor_ref=company.id
    )
    assert len(grants) == 1
    assert grants[0].reference_key == f"work_order:{order.id}"
    assert int(grants[0].amount) == 5_000
    assert grants[0].ledger_transaction_id == transaction.id
    assert verify_wallet_projection(db).ok


def test_reward_is_base_plus_bonuses(db):
    company = _company(db, "BonusCo")
    account_id = _account(db, company)
    service = WorkOrderService(db)
    order = service.publish_official(
        title="Fast delivery",
        reward_amount=4_000,
        evaluation_mode=EvaluationMode.manual,
        deliverables={"required_keys": ["readme"]},
    ).order
    service.accept(order.id, company_id=company.id)
    service.submit(order.id, company_id=company.id, summary="ready", deliverables={"readme": "x"})

    # manual：提交后停在 SUBMITTED，等管理面验收（验收时才进入 REVIEWING）
    assert service.detail(order.id).status == WorkOrderStatus.submitted.value
    assert _wallet(account_id)["available"] == 0

    settled, evaluation = service.evaluate(
        order.id,
        verdict=EvaluationVerdict.approved,
        score=95,
        bonuses={"early_delivery": 1_000, "quality": 500},
        notes="good work",
    )
    assert evaluation.verdict == EvaluationVerdict.approved.value
    assert evaluation.bonuses_json == {"early_delivery": 1_000, "quality": 500}
    assert settled.status == WorkOrderStatus.settled.value
    assert _wallet(account_id)["available"] == 5_500
    assert service.payable_amount(service.detail(order.id)) == 5_500


def test_rejected_submission_can_be_resubmitted_and_pays_once(db):
    company = _company(db, "RetryCo")
    account_id = _account(db, company)
    service = WorkOrderService(db)
    order = service.publish_official(
        title="Needs both files",
        reward_amount=3_000,
        deliverables={"required_keys": ["readme", "report"]},
    ).order
    service.accept(order.id, company_id=company.id)

    # 缺件 ⇒ auto 判定 rejected（不发钱），订单回到可重提状态
    first, rejected_order, evaluation = service.submit(
        order.id, company_id=company.id, summary="only readme", deliverables={"readme": "x"}
    )
    assert evaluation.verdict == EvaluationVerdict.rejected.value
    assert evaluation.notes == "missing_required_deliverables"
    assert rejected_order.status == WorkOrderStatus.rejected.value
    assert _wallet(account_id)["available"] == 0

    # 重提（attempt 递增）⇒ 通过并结算
    second, settled_order, evaluation2 = service.submit(
        order.id,
        company_id=company.id,
        summary="both files",
        deliverables={"readme": "x", "report": "y"},
    )
    assert second.attempt == 2
    assert evaluation2.verdict == EvaluationVerdict.approved.value
    assert settled_order.status == WorkOrderStatus.settled.value
    assert _wallet(account_id)["available"] == 3_000
    assert len(economy_repo.list_submissions(db, order_id=order.id)) == 2


def test_manual_rejection_keeps_order_open_for_revision(db):
    company = _company(db, "ManualReject")
    service = WorkOrderService(db)
    order = service.publish_official(
        title="Manual review",
        reward_amount=2_000,
        evaluation_mode=EvaluationMode.manual,
    ).order
    service.accept(order.id, company_id=company.id)
    service.submit(order.id, company_id=company.id, summary="v1", deliverables={"a": "1"})
    rejected, evaluation = service.evaluate(
        order.id, verdict=EvaluationVerdict.rejected, notes="needs work", score=40
    )
    assert evaluation.verdict == EvaluationVerdict.rejected.value
    assert rejected.status == WorkOrderStatus.rejected.value
    assert service.payable_amount(rejected) == 0
    assert _wallet(_account(db, company))["available"] == 0

    # revise 判定同样是"不发钱 + 可重提"
    service.submit(order.id, company_id=company.id, summary="v2", deliverables={"a": "2"})
    revised, evaluation2 = service.evaluate(
        order.id, verdict=EvaluationVerdict.revise, score=60, notes="almost"
    )
    assert evaluation2.verdict == EvaluationVerdict.revise.value
    assert revised.status == WorkOrderStatus.rejected.value


# ---------------------------------------------------------------- 幂等与并发


def test_repeated_settlement_is_idempotent(db):
    company = _company(db, "IdempotentCo")
    service = WorkOrderService(db)
    order = service.publish_official(title="Once only", reward_amount=1_500).order
    service.accept(order.id, company_id=company.id)
    _, settled, _ = service.submit(
        order.id, company_id=company.id, summary="ok", deliverables={"x": "1"}
    )
    minted_before = _supply(db).minted
    available_before = _wallet(_account(db, company))["available"]

    # 直接重复调用 settle（模拟重试/重复事件）
    again = service.settle(order.id)
    assert again.status == WorkOrderStatus.settled.value
    assert _supply(db).minted == minted_before
    assert _wallet(_account(db, company))["available"] == available_before
    assert (
        len(
            economy_repo.list_reward_grants(
                db, reward_type=RewardType.official_bounty.value, actor_ref=company.id
            )
        )
        == 1
    )


def test_concurrent_accept_lets_one_company_win(db):
    first = _company(db, "RaceA")
    second = _company(db, "RaceB")
    service = WorkOrderService(db)
    order = service.publish_official(title="First come", reward_amount=2_500).order

    barrier = threading.Barrier(2)
    winners: list[int] = []
    losers: list[str] = []

    def claim(company: Company) -> None:
        with SessionLocal() as session:
            barrier.wait()
            try:
                accepted = WorkOrderService(session).accept(order.id, company_id=company.id)
                winners.append(int(accepted.assignee_actor_ref or 0))
            except Exception as exc:
                losers.append(f"{type(exc).__name__}:{exc}")

    threads = [threading.Thread(target=claim, args=(company,)) for company in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(winners) == 1, (winners, losers)
    assert winners[0] in {first.id, second.id}
    assert all("order_already_taken" in reason for reason in losers), losers
    assert len({int(winners[0])}) == 1


# ---------------------------------------------------------------- E8 / 预算 / 状态机


def test_official_publish_path_rejects_player_kinds(db):
    """E8：官方发行通道（`publish_official`，会 mint）只接受官方 kind。

    玩家类 kind 必须走 `publish_player_order`（Escrow 锁资、绝不 mint）；
    在官方通道里出现 ⇒ 明确拒绝，而不是"顺手 mint 给玩家市场"。
    """
    service = WorkOrderService(db)
    player_before = len(
        economy_repo.list_work_orders(
            db,
            kinds=(
                WorkOrderKind.player_bounty.value,
                WorkOrderKind.player_contract.value,
                WorkOrderKind.npc_contract.value,
            ),
        )
    )
    for kind in (
        WorkOrderKind.player_bounty,
        WorkOrderKind.player_contract,
        WorkOrderKind.npc_contract,
    ):
        with pytest.raises(WorkOrderError, match="kind_not_available_yet"):
            service.publish_official(title="Player order", reward_amount=1_000, kind=kind)
    # 官方通道没有产生任何玩家类订单（共享测试库：只看增量）
    assert (
        len(
            economy_repo.list_work_orders(
                db,
                kinds=(
                    WorkOrderKind.player_bounty.value,
                    WorkOrderKind.player_contract.value,
                    WorkOrderKind.npc_contract.value,
                ),
            )
        )
        == player_before
    )


def test_official_budget_limits_are_enforced(db):
    company = _company(db, "BudgetCo")
    policy = economic_policy()
    service = WorkOrderService(db)

    with pytest.raises(WorkOrderError, match="reward_exceeds_official_max_reward"):
        service.publish_official(title="Too big", reward_amount=policy.official_max_reward + 1)

    # 用满剩余预算后，再发布一律拒绝（预算内发行，§18）
    # 共享测试库：按"当前未结算承诺额"动态算剩余额度（其它用例也占了预算）
    spends = []
    while True:
        headroom = (
            policy.official_outstanding_budget - economy_repo.outstanding_official_commitment(db)
        )
        if headroom <= 0:
            break
        amount = min(policy.official_max_reward, headroom)
        spends.append(
            service.publish_official(title=f"Budget {len(spends)}", reward_amount=amount).order
        )
        if amount < policy.official_max_reward:
            break
    headroom = policy.official_outstanding_budget - economy_repo.outstanding_official_commitment(db)
    assert headroom < policy.official_max_reward
    with pytest.raises(WorkOrderError, match="official_budget_exhausted"):
        service.publish_official(title="Over budget", reward_amount=headroom + 1)

    # 结算完成 ⇒ 释放承诺额（终态不再占用预算）
    assert spends
    order = spends[-1]
    service.accept(order.id, company_id=company.id)
    service.submit(order.id, company_id=company.id, summary="ok")
    assert economy_repo.outstanding_official_commitment(db) < policy.official_outstanding_budget
    from dataclasses import replace as dataclass_replace

    # 预算口径是"未结算承诺额"的绝对值（共享测试库：按当前承诺额给一个刚好够用的额度）
    outstanding = economy_repo.outstanding_official_commitment(db)
    lenient = WorkOrderService(
        db,
        policy=dataclass_replace(
            policy, official_outstanding_budget=outstanding + policy.official_max_reward
        ),
    )
    assert lenient.publish_official(title="Fits again", reward_amount=1_000).created


def test_illegal_transitions_and_deadlines_are_rejected(db):
    company = _company(db, "StateCo")
    other = _company(db, "StateOther")
    service = WorkOrderService(db)
    order = service.publish_official(title="State machine", reward_amount=1_000).order

    # 未领取不能提交
    with pytest.raises(WorkOrderError, match="not_your_order"):
        service.submit(order.id, company_id=company.id, summary="x")
    # 未提交不能验收
    with pytest.raises(WorkOrderError, match="order_not_reviewable"):
        service.evaluate(order.id, verdict=EvaluationVerdict.approved)
    # 未通过不能结算
    with pytest.raises(WorkOrderError, match="order_not_settleable"):
        service.settle(order.id)

    service.accept(order.id, company_id=company.id)
    # 别人不能提交我的订单
    with pytest.raises(WorkOrderError, match="not_your_order"):
        service.submit(order.id, company_id=other.id, summary="steal")

    # 过期订单不能领取
    stale = service.publish_official(
        title="Stale",
        reward_amount=500,
        deadline_at=datetime.now(UTC) - timedelta(hours=1),
    ).order
    with pytest.raises(WorkOrderError, match="order_expired"):
        service.accept(stale.id, company_id=company.id)
    assert service.detail(stale.id).status == WorkOrderStatus.expired.value

    # 空提交被拒绝
    with pytest.raises(WorkOrderError, match="empty_submission"):
        service.submit(order.id, company_id=company.id, summary="  ")


def test_empty_submission_auto_rejects_and_bonus_validation(db):
    company = _company(db, "ValidationCo")
    service = WorkOrderService(db)
    order = service.publish_official(title="Validate", reward_amount=800).order
    service.accept(order.id, company_id=company.id)
    submission, rejected, evaluation = service.submit(
        order.id, company_id=company.id, summary="something", deliverables={}
    )
    # summary 非空 ⇒ auto 通过（empty_submission 只在全空时触发）
    assert evaluation.verdict == EvaluationVerdict.approved.value
    assert rejected.status == WorkOrderStatus.settled.value

    # 负 bonus / 非整数 bonus 一律拒绝
    other = service.publish_official(
        title="Bad bonus", reward_amount=900, evaluation_mode=EvaluationMode.manual
    ).order
    service.accept(other.id, company_id=company.id)
    service.submit(other.id, company_id=company.id, summary="v1")
    with pytest.raises(EvaluationError, match="bonus_must_be_positive_int"):
        service.evaluate(other.id, verdict=EvaluationVerdict.approved, bonuses={"oops": -100})
    with pytest.raises(EvaluationError, match="bonus_must_be_positive_int"):
        service.evaluate(other.id, verdict=EvaluationVerdict.approved, bonuses={"oops": 1.5})
    with pytest.raises(EvaluationError, match="score_out_of_range"):
        service.evaluate(other.id, verdict=EvaluationVerdict.approved, score=101)


def test_settlement_service_rejects_unsupported_funding_modes(db):
    """未实现的腿组合明确拒绝，而不是"退化成 mint"（E8）。

    M1.4 起 `player_escrow` 已实现（走 Escrow 放款，见 tests/test_economy_escrow.py）；
    这里只钉住尚未实现的 `npc_treasury`，以及 player_escrow 缺 escrow_id 时必须报错。
    """
    company = _company(db, "ModeCo")
    service = SettlementService(db)
    minted_before = _supply(db).minted
    with pytest.raises(SettlementError, match="funding_mode_not_supported"):
        service.settle(
            SettlementRequest(
                settlement_key="test:npc",
                amount=100,
                beneficiary=EconomicActor.company(company.id),
                reason="test",
                reference_type="test",
                reference_id="1",
                funding_mode=FundingMode.npc_treasury,
            )
        )
    with pytest.raises(SettlementError, match="escrow_id_required"):
        service.settle(
            SettlementRequest(
                settlement_key="test:escrow",
                amount=100,
                beneficiary=EconomicActor.company(company.id),
                reason="test",
                reference_type="test",
                reference_id="1",
                funding_mode=FundingMode.player_escrow,
            )
        )
    # 也没有任何交易落库
    assert _supply(db).minted == minted_before


def test_settlement_service_is_idempotent_by_key(db):
    company = _company(db, "SettleKeyCo")
    service = SettlementService(db)
    request = SettlementRequest(
        settlement_key="manual:1",
        amount=1_000,
        beneficiary=EconomicActor.company(company.id),
        reason="test",
        reference_type="manual",
        reference_id="1",
    )
    first = service.settle(request, commit=True)
    second = service.settle(request, commit=True)
    assert first.created is True
    assert second.created is False
    assert first.transaction.id == second.transaction.id
    assert _wallet(_account(db, company))["available"] == 1_000


def test_projection_and_ledger_stay_consistent_after_market_activity(db):
    companies = [_company(db, "ConsistencyCo") for _ in range(3)]
    service = WorkOrderService(db)
    minted_before = _supply(db).minted
    for index, company in enumerate(companies):
        order = service.publish_official(
            title=f"Task {index}", reward_amount=1_000 + index * 500
        ).order
        service.accept(order.id, company_id=company.id)
        service.submit(
            order.id, company_id=company.id, summary="done", deliverables={"x": str(index)}
        )
    snapshot = _supply(db)
    assert snapshot.supply == snapshot.minted - snapshot.burned
    assert snapshot.minted - minted_before == 1_000 + 1_500 + 2_000
    assert verify_wallet_projection(db).ok
    # 每家公司各一笔 grant（共享测试库：按本用例的公司过滤）
    for company in companies:
        grants = economy_repo.list_reward_grants(
            db, reward_type=RewardType.official_bounty.value, actor_ref=company.id
        )
        assert len(grants) == 1
        assert grants[0].reference_key.startswith("work_order:")


def test_execution_reuses_projects_without_copying(db):
    """执行复用既有 Project/Task/Artifact：订单只存 `project_id` 引用。"""
    from app.models.project import Project

    company = _company(db, "ProjectCo")
    project = Project(company_id=company.id, name="Delivery", description="", goal="")
    db.add(project)
    db.commit()
    service = WorkOrderService(db)
    order = service.publish_official(title="With project", reward_amount=1_200).order
    service.accept(order.id, company_id=company.id)
    _, settled, _ = service.submit(
        order.id,
        company_id=company.id,
        summary="delivered",
        deliverables={"x": "1"},
        project_id=project.id,
    )
    assert int(settled.project_id) == project.id
    # 订单表里没有项目字段的副本（title/description 各归各的）
    assert settled.title == "With project"
    assert project.name == "Delivery"


def test_order_detail_surfaces_submissions_and_evaluations(db):
    company = _company(db, "DetailCo")
    service = WorkOrderService(db)
    order = service.publish_official(
        title="Detail", reward_amount=1_000, evaluation_mode=EvaluationMode.manual
    ).order
    service.accept(order.id, company_id=company.id)
    service.submit(order.id, company_id=company.id, summary="v1", artifact_refs=["drive:1"])
    service.evaluate(order.id, verdict=EvaluationVerdict.approved, score=88)

    submissions = service.submissions(order.id)
    evaluations = service.evaluations(order.id)
    assert len(submissions) == 1
    assert submissions[0].artifact_refs == ["drive:1"]
    assert len(evaluations) == 1
    assert evaluations[0].score == 88
    assert isinstance(evaluations[0], Evaluation)
    assert isinstance(service.detail(order.id), WorkOrder)


# ---------------------------------------------------------------- 边界守卫


def test_player_api_never_publishes_evaluates_or_settles():
    """§32 三层边界：发布 / 验收 / 结算只在服务层与 CLI，玩家 router 不可达。

    两条互补的守卫：
    1. **功能守卫**（最硬）：OpenAPI 路由表里不存在发布/验收/结算端点；
    2. **AST 守卫**：`app/api/**` 不调用 `publish_official` / `record_external_grant`，
       也不 import `SettlementService`（字符串扫描会误伤文档，所以只认 AST）。
    """
    import ast
    from pathlib import Path as PathLib

    from app.main import app as fastapi_app

    paths = fastapi_app.openapi()["paths"]
    forbidden_paths = [
        path for path in paths if "/work-orders" in path and path.endswith(("/evaluate", "/settle"))
    ]
    assert not forbidden_paths, forbidden_paths
    # `POST /work-orders` 只能是**玩家发布**（Escrow 锁资）；官方发行仍只走 CLI —
    # AST 守卫在下面钉住"API 层不许调用 publish_official"。
    assert sorted(paths["/api/v1/work-orders"]) == ["get", "post"]

    server_root = PathLib(__file__).resolve().parents[1]
    forbidden_calls = {"publish_official", "record_external_grant"}
    offenders: list[str] = []
    for path in sorted((server_root / "app" / "api").rglob("*.py")):
        relative = str(path.relative_to(server_root))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in forbidden_calls:
                    offenders.append(f"{relative}:{node.lineno} calls {node.func.attr}()")
            if isinstance(node, ast.ImportFrom) and node.module and "settlement" in node.module:
                offenders.append(f"{relative}:{node.lineno} imports {node.module}")
    assert not offenders, offenders


def test_settlement_is_the_only_final_money_entry_for_work_orders():
    """工作订单结算只经 `SettlementService`：服务层不出现"直接 mint 给公司"的分支。"""
    from pathlib import Path as PathLib

    source = (
        PathLib(__file__).resolve().parents[1] / "app/services/economy/work_orders.py"
    ).read_text(encoding="utf-8")
    assert "SettlementService(" in source
    assert "MonetaryAuthority(" not in source  # 结算服务持有铸币能力，工作订单不自己铸
    assert "LedgerService(" not in source
