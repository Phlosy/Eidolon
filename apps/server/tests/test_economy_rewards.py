"""M1.2 奖励系统（docs/m1-economy-design.md §14/§15/§16；plan §4/M1.2）。

锁住的是**奖励不变量**，不是 CRUD：
- 启动资金只发一次：重复调用返回既有 grant（`created=False`），供给不变；
- 并发重复领取：唯一约束裁定赢家，只 mint 一次（E10 + CAS/唯一约束纪律）；
- 资格**先有事实后有奖励**：个人资料/公司编制/教程/成就/救援金阈值与冷却；
- 官方类类型（悬赏/合同/资助/采购/…）**不可自助领取**（否则端点就是"随便领钱"）；
- 金额只能来自 `EconomicPolicy`（请求体没有金额入参），并落 `policy_version` 快照；
- 奖励过账可追溯：`grant.ledger_transaction_id` 指向真实交易，`supply = minted − burned`。
"""

from __future__ import annotations

import random
import threading
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from factories import make_employee

from app.core.database import SessionLocal
from app.economy.contracts import EconomicActor
from app.economy.policy import economic_policy
from app.models.auth import CompanyMembership, User
from app.models.economy import LedgerTransaction, RewardGrant
from app.models.enums import (
    Currency,
    EconomicActorKind,
    RewardStatus,
    RewardType,
    TutorialStatus,
)
from app.models.organization import Company
from app.models.position import PositionDefinition
from app.models.project import Project
from app.models.project_delivery import TutorialProgress
from app.repositories import economy as economy_repo
from app.services.economy.accounts import AccountService
from app.services.economy.ledger import LedgerService
from app.services.economy.projection import verify_wallet_projection
from app.services.economy.rewards import (
    ACHIEVEMENT_CODES,
    SELF_SERVICE_KINDS,
    RewardError,
    RewardService,
)

_seq = 0


def _company(db, name: str = "RewardCo") -> Company:
    global _seq
    _seq += 1
    company = Company(name=f"{name} {_seq}", slug=f"reward-co-{_seq}", description="")
    db.add(company)
    db.commit()
    return company


def _owner(db, company: Company, *, display_name: str = "", avatar: str = "") -> User:
    """建一个公司 OWNER 用户（个人奖励的主体 seam：身份缺失时回落到 OWNER）。"""
    global _seq
    _seq += 1
    user = User(
        email=f"reward-user-{_seq}@example.com",
        username=f"reward-user-{_seq}",
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


def _account_id(db, actor: EconomicActor) -> int:
    return int(AccountService(db).ensure_account(actor).id)


def _supply(db):
    return LedgerService(db).supply()


def _grants(db, **filters) -> list[RewardGrant]:
    return economy_repo.list_reward_grants(db, **filters)


def _evaluation(service: RewardService, reward_type: RewardType, **kwargs):
    return {item.reward_type: item for item in service.catalog(**kwargs)}.get(reward_type)


# ---------------------------------------------------------------- 政策


def test_policy_keeps_recovery_in_check():
    """§16：救援金必须"非常小"且不超过阈值（救援不是收入来源）。"""
    policy = economic_policy()
    assert policy.recovery_grant < policy.starter_grant
    assert policy.recovery_grant < policy.achievement_reward
    assert policy.recovery_grant < policy.tutorial_reward
    assert policy.recovery_grant <= policy.recovery_threshold
    assert policy.recovery_cooldown_hours >= 24


# ---------------------------------------------------------------- Starter grant（§15）


def test_starter_grant_is_minted_once(db):
    company = _company(db)
    service = RewardService(db)
    before = _supply(db)

    grant, created = service.claim(RewardType.starter_grant, company_id=company.id, user_id=None)
    assert created is True
    assert grant.status == RewardStatus.posted.value
    assert grant.amount == service.policy.starter_grant
    assert grant.policy_version == service.policy.version
    assert grant.ledger_transaction_id is not None

    account_id = _account_id(db, EconomicActor.company(company.id))
    assert _wallet(account_id)["available"] == service.policy.starter_grant

    again, created_again = service.claim(
        RewardType.starter_grant, company_id=company.id, user_id=None
    )
    assert created_again is False
    assert again.id == grant.id
    assert _wallet(account_id)["available"] == service.policy.starter_grant  # 没有第二次
    after = _supply(db)
    assert after.minted - before.minted == service.policy.starter_grant
    assert after.supply == after.minted - after.burned
    assert _grants(db, reward_type=RewardType.starter_grant.value) != []
    assert verify_wallet_projection(db).ok


def test_starter_grant_per_company_not_global(db):
    first, second = _company(db, "StarterA"), _company(db, "StarterB")
    service = RewardService(db)
    grant_a, created_a = service.claim(RewardType.starter_grant, company_id=first.id, user_id=None)
    grant_b, created_b = service.claim(RewardType.starter_grant, company_id=second.id, user_id=None)
    assert created_a and created_b
    assert grant_a.id != grant_b.id
    assert (
        _wallet(_account_id(db, EconomicActor.company(second.id)))["available"]
        == service.policy.starter_grant
    )


def test_concurrent_starter_grant_posts_once(db):
    """并发领取：唯一索引裁定赢家，只有一个 grant、只 mint 一次。"""
    company = _company(db, "StarterRace")
    before = _supply(db)
    barrier = threading.Barrier(2)
    results: list[bool] = []
    failures: list[str] = []

    def claim() -> None:
        with SessionLocal() as session:
            barrier.wait()
            try:
                _, created = RewardService(session).claim(
                    RewardType.starter_grant, company_id=company.id, user_id=None
                )
                results.append(created)
            except Exception as exc:  # pragma: no cover - 失败时把真实原因带出来
                failures.append(f"{type(exc).__name__}:{exc}")

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not failures, failures
    assert results.count(True) == 1, results
    grants = _grants(db, reward_type=RewardType.starter_grant.value, actor_ref=company.id)
    assert len(grants) == 1
    after = _supply(db)
    assert after.minted - before.minted == economic_policy().starter_grant


# ---------------------------------------------------------------- 个人奖励


def test_profile_completion_requires_display_name_and_avatar(db):
    company = _company(db, "ProfileCo")
    user = _owner(db, company)
    service = RewardService(db)

    option = _evaluation(
        service, RewardType.profile_completion, company_id=company.id, user_id=user.id
    )
    assert option is not None
    assert option.claimable is False
    assert option.reason == "not_eligible"
    with pytest.raises(RewardError, match="not_eligible"):
        service.claim(RewardType.profile_completion, company_id=company.id, user_id=user.id)

    user.display_name = "Ada"
    user.avatar = "/api/v1/auth/avatars/ada.png"
    db.commit()

    grant, created = service.claim(
        RewardType.profile_completion, company_id=company.id, user_id=user.id
    )
    assert created
    assert grant.actor_kind == EconomicActorKind.user.value
    assert grant.actor_ref == user.id
    assert grant.amount == service.policy.profile_reward
    assert (
        _wallet(_account_id(db, EconomicActor(EconomicActorKind.user, user.id)))["available"]
        == service.policy.profile_reward
    )
    assert (
        _evaluation(
            service, RewardType.profile_completion, company_id=company.id, user_id=user.id
        ).reason
        == "already_claimed"
    )


def test_profile_completion_falls_back_to_company_owner(db):
    """无会话身份（dev/测试 seam）时，个人奖励的归当前公司 OWNER。"""
    company = _company(db, "OwnerFallback")
    owner = _owner(db, company, display_name="Owner", avatar="avatar.png")
    grant, created = RewardService(db).claim(
        RewardType.profile_completion, company_id=company.id, user_id=None
    )
    assert created
    assert grant.actor_ref == owner.id


def test_daily_login_is_per_utc_day(db):
    company = _company(db, "DailyCo")
    user = _owner(db, company)
    service = RewardService(db)

    grant, created = service.claim(RewardType.daily_login, company_id=company.id, user_id=user.id)
    assert created
    assert grant.reference_key.endswith(datetime.now(UTC).date().isoformat())
    assert grant.amount == service.policy.daily_reward

    option = _evaluation(service, RewardType.daily_login, company_id=company.id, user_id=user.id)
    assert option.reason == "already_claimed"
    _, created_again = service.claim(RewardType.daily_login, company_id=company.id, user_id=user.id)
    assert created_again is False
    assert len(_grants(db, reward_type=RewardType.daily_login.value)) == 1


def test_tutorial_completion_requires_completed_progress(db):
    company = _company(db, "TutorialCo")
    user = _owner(db, company)
    service = RewardService(db)

    option = _evaluation(
        service, RewardType.tutorial_completion, company_id=company.id, user_id=user.id
    )
    assert option.claimable is False and option.reason == "not_eligible"

    progress = TutorialProgress(
        user_id=user.id,
        company_id=company.id,
        tutorial_id="company-founding",
        status=TutorialStatus.active.value,
    )
    db.add(progress)
    db.commit()
    option = _evaluation(
        service, RewardType.tutorial_completion, company_id=company.id, user_id=user.id
    )
    assert option.claimable is False

    progress.status = TutorialStatus.completed.value
    progress.completed_at = datetime.now(UTC)
    db.commit()

    grant, created = service.claim(
        RewardType.tutorial_completion, company_id=company.id, user_id=user.id
    )
    assert created
    assert grant.reference_key == "tutorial:company-founding"
    assert grant.amount == service.policy.tutorial_reward

    # 第二个教程：同类型不同 reference_key ⇒ 可以再领一次（各教程各一次）
    second = TutorialProgress(
        user_id=user.id,
        company_id=company.id,
        tutorial_id="first-project-practice",
        status=TutorialStatus.completed.value,
        completed_at=datetime.now(UTC),
    )
    db.add(second)
    db.commit()
    grant2, created2 = service.claim(
        RewardType.tutorial_completion, company_id=company.id, user_id=user.id
    )
    assert created2
    assert grant2.reference_key == "tutorial:first-project-practice"
    # 再来一次 ⇒ 没有第三个教程可领：幂等返回既有 grant（绝不再发钱）
    third, created_third = service.claim(
        RewardType.tutorial_completion, company_id=company.id, user_id=user.id
    )
    assert created_third is False
    assert third.id in {grant.id, grant2.id}
    option = _evaluation(
        service, RewardType.tutorial_completion, company_id=company.id, user_id=user.id
    )
    assert option.reason == "already_claimed"
    assert option.next_eligible_at is None
    # 显式指定一个"未完成"的教程 ⇒ 拒绝（不能凭空领）
    with pytest.raises(RewardError, match="not_eligible"):
        service.claim(
            RewardType.tutorial_completion,
            company_id=company.id,
            user_id=user.id,
            reference_key="tutorial:not-a-real-tutorial",
        )


# ---------------------------------------------------------------- 成就 / 公司编制


def test_achievement_requires_fact_and_code(db):
    company = _company(db, "AchievementCo")
    service = RewardService(db)

    option = _evaluation(service, RewardType.achievement, company_id=company.id, user_id=None)
    assert option.amount == service.policy.achievement_reward
    assert option.claimable is False and option.reason == "not_eligible"

    # 没有 code 不能领（不做"点击即得"）：成就必须指定具体事实
    with pytest.raises(RewardError, match="reference_required"):
        service.claim(RewardType.achievement, company_id=company.id, user_id=None)
    assert "first_employee" in ACHIEVEMENT_CODES and "first_project" in ACHIEVEMENT_CODES

    # 事实 1：有员工
    make_employee(db, company_id=company.id, slug=f"reward-emp-{company.id}", role="engineer")
    db.commit()
    grant, created = service.claim(
        RewardType.achievement,
        company_id=company.id,
        user_id=None,
        reference_key="first_employee",
    )
    assert created
    assert grant.reference_key == "achievement:first_employee"
    assert grant.amount == service.policy.achievement_reward
    assert grant.metadata_json.get("achievement") == "first_employee"

    # 同一个成就不能领两次
    _, created_again = service.claim(
        RewardType.achievement,
        company_id=company.id,
        user_id=None,
        reference_key="first_employee",
    )
    assert created_again is False

    # 事实 2：有项目 ⇒ 另一个 code 可领
    db.add(Project(company_id=company.id, name="Proj", description="", goal=""))
    db.commit()
    project_grant, created_project = service.claim(
        RewardType.achievement,
        company_id=company.id,
        user_id=None,
        reference_key="achievement:first_project",
    )
    assert created_project
    assert project_grant.reference_key == "achievement:first_project"


def test_company_profile_completion_needs_a_position_definition(db):
    company = _company(db, "CompanyProfileCo")
    service = RewardService(db)
    option = _evaluation(
        service, RewardType.company_profile_completion, company_id=company.id, user_id=None
    )
    assert option.claimable is False and option.reason == "not_eligible"

    db.add(
        PositionDefinition(
            company_id=company.id,
            code=f"eng-{company.id}",
            name="Engineer",
            job_family="engineering",
        )
    )
    db.commit()
    grant, created = service.claim(
        RewardType.company_profile_completion, company_id=company.id, user_id=None
    )
    assert created
    assert grant.amount == service.policy.company_profile_reward
    assert grant.actor_kind == EconomicActorKind.company.value


# ---------------------------------------------------------------- 救援经济（§16）


def test_recovery_grant_needs_starter_grant_first(db):
    """零余额但还没领启动资金 = 还没进入经济，不算"破产"（§16）。"""
    company = _company(db, "RecoveryPrereq")
    service = RewardService(db)
    option = _evaluation(service, RewardType.recovery_grant, company_id=company.id, user_id=None)
    assert option.claimable is False
    assert option.reason == "starter_not_claimed"
    service.claim(RewardType.starter_grant, company_id=company.id, user_id=None)
    # 领了启动资金并且余额充足 ⇒ 仍然不发（阈值判定）
    option = _evaluation(service, RewardType.recovery_grant, company_id=company.id, user_id=None)
    assert option.reason == "recovery_not_needed"


def test_recovery_grant_threshold_and_cooldown(db):
    company = _company(db, "RecoveryCo")
    service = RewardService(db)
    # 先给一笔启动资金，便于观察阈值判定
    service.claim(RewardType.starter_grant, company_id=company.id, user_id=None)

    option = _evaluation(service, RewardType.recovery_grant, company_id=company.id, user_id=None)
    assert option.claimable is False
    assert option.reason == "recovery_not_needed"  # 余额充足时不发救援金

    # 把钱花掉（转到另一家公司账户）→ 低于阈值
    sink = _company(db, "RecoverySink")
    payer = _account_id(db, EconomicActor.company(company.id))
    payee = _account_id(db, EconomicActor.company(sink.id))
    LedgerService(db).transfer(
        payer_account_id=payer,
        payee_account_id=payee,
        amount=service.policy.starter_grant,
        reason="spend",
    )
    assert service.company_available_balance(company.id) == 0

    grant, created = service.claim(RewardType.recovery_grant, company_id=company.id, user_id=None)
    assert created
    assert grant.amount == service.policy.recovery_grant
    assert grant.amount < service.policy.starter_grant

    # 冷却期内不可再领
    option = _evaluation(service, RewardType.recovery_grant, company_id=company.id, user_id=None)
    assert option.claimable is False
    assert option.reason == "already_claimed"  # 当日 key 已存在（同日不再发）

    # 把已发的 grant 回拨一天，验证"冷却期"本身也在起作用
    grant.reference_key = "recovery:1970-01-01"
    db.commit()
    option = _evaluation(service, RewardType.recovery_grant, company_id=company.id, user_id=None)
    assert option.claimable is False
    assert option.reason == "cooldown"
    assert option.next_eligible_at is not None
    assert option.next_eligible_at > datetime.now(UTC)

    # 冷却期过后可再领
    grant.posted_at = datetime.now(UTC) - timedelta(
        hours=service.policy.recovery_cooldown_hours + 1
    )
    grant.status = RewardStatus.posted.value
    db.commit()
    again, created_again = service.claim(
        RewardType.recovery_grant, company_id=company.id, user_id=None
    )
    assert created_again
    assert again.id != grant.id


# ---------------------------------------------------------------- 边界与安全


def test_official_reward_types_are_not_self_service(db):
    company = _company(db, "NotSelfService")
    service = RewardService(db)
    for reward_type in (
        RewardType.official_bounty,
        RewardType.official_contract,
        RewardType.research_grant,
        RewardType.system_procurement,
        RewardType.milestone_reward,
        RewardType.event_reward,
        RewardType.weekly_activity,
    ):
        assert reward_type not in SELF_SERVICE_KINDS
        with pytest.raises(RewardError, match="not_self_service"):
            service.claim(reward_type, company_id=company.id, user_id=None)
    # 这些类型也没有产生任何 grant
    assert _grants(db, company_id=company.id) == []


def test_reward_reference_points_to_a_real_ledger_transaction(db):
    company = _company(db, "TraceCo")
    grant, _ = RewardService(db).claim(
        RewardType.starter_grant, company_id=company.id, user_id=None
    )
    db.expire_all()
    transaction = db.get(LedgerTransaction, int(grant.ledger_transaction_id))
    assert transaction is not None
    assert transaction.reference_type == "reward"
    assert transaction.reference_id == "STARTER_GRANT:starter"
    assert transaction.idempotency_key is not None
    entries = economy_repo.list_entries(db, transaction_id=int(transaction.id))
    debits = sum(e.amount for e in entries if e.direction == "debit")
    credits = sum(e.amount for e in entries if e.direction == "credit")
    assert debits == credits == grant.amount


def test_reward_amount_cannot_be_influenced_by_caller(db):
    """金额只能来自政策：`claim()` 没有金额入参，policy_version 落库（§30）。"""
    company = _company(db, "AmountCo")
    service = RewardService(db)
    grant, _ = service.claim(RewardType.starter_grant, company_id=company.id, user_id=None)
    assert grant.amount == service.policy.starter_grant
    assert grant.currency == Currency.credit.value
    assert grant.policy_version == service.policy.version
    import inspect

    signature = inspect.signature(RewardService.claim)
    assert "amount" not in signature.parameters


def test_supply_accounting_after_mixed_rewards(db):
    """随机顺序领取多种奖励：供给 = minted − burned，且投影与账本一致。"""
    company = _company(db, "MixedRewards")
    owner = _owner(db, company, display_name="Mixed", avatar="avatar.png")
    db.add(
        PositionDefinition(
            company_id=company.id, code=f"mixed-{company.id}", name="Mixed", job_family="ops"
        )
    )
    make_employee(db, company_id=company.id, slug=f"mixed-emp-{company.id}", role="engineer")
    db.add(Project(company_id=company.id, name="P", description="", goal=""))
    db.add(
        TutorialProgress(
            user_id=owner.id,
            company_id=company.id,
            tutorial_id="company-founding",
            status=TutorialStatus.completed.value,
            completed_at=datetime.now(UTC),
        )
    )
    db.commit()

    service = RewardService(db)
    kinds = [
        (RewardType.starter_grant, None),
        (RewardType.profile_completion, None),
        (RewardType.company_profile_completion, None),
        (RewardType.tutorial_completion, None),
        (RewardType.daily_login, None),
        (RewardType.achievement, "first_employee"),
        (RewardType.achievement, "first_project"),
    ]
    rng = random.Random(20260912)
    rng.shuffle(kinds)
    total = 0
    for reward_type, reference_key in kinds:
        grant, created = service.claim(
            reward_type,
            company_id=company.id,
            user_id=owner.id,
            reference_key=reference_key,
        )
        assert created, (reward_type, reference_key)
        total += int(grant.amount)

    company_account = _account_id(db, EconomicActor.company(company.id))
    user_account = _account_id(db, EconomicActor(EconomicActorKind.user, owner.id))
    assert _wallet(company_account)["available"] + _wallet(user_account)["available"] == total
    snapshot = _supply(db)
    assert snapshot.supply == snapshot.minted - snapshot.burned
    assert snapshot.minted >= total
    assert verify_wallet_projection(db).ok

    # 全量重放：全部返回 created=False，供给与余额完全不变
    for reward_type, reference_key in kinds:
        _, created = service.claim(
            reward_type,
            company_id=company.id,
            user_id=owner.id,
            reference_key=reference_key,
        )
        assert created is False
    assert _supply(db).supply == snapshot.supply
    assert _wallet(company_account)["available"] + _wallet(user_account)["available"] == total


def test_grants_carry_company_scope_and_status_index(db):
    first, second = _company(db, "ScopeA"), _company(db, "ScopeB")
    service = RewardService(db)
    service.claim(RewardType.starter_grant, company_id=first.id, user_id=None)
    service.claim(RewardType.starter_grant, company_id=second.id, user_id=None)
    first_grants = _grants(db, company_id=first.id)
    assert [grant.company_id for grant in first_grants] == [first.id]
    assert all(grant.status == RewardStatus.posted.value for grant in first_grants)
    # 唯一约束就位（迁移里）
    indexes = {
        row[0]
        for row in db.execute(
            sa.text(
                "select name from sqlite_master where type='index' and tbl_name='reward_grants'"
            )
        )
    }
    assert "sqlite_autoindex_reward_grants_1" in indexes
    assert {"ix_reward_grants_actor", "ix_reward_grants_company"} <= indexes
