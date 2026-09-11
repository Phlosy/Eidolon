"""RewardService —— 统一的奖励入口（M1.2，设计 §14/§15/§16）。

**唯一发钱给玩家的自助通道**：所有"发给玩家/公司"的奖励都从这里过（业务代码不得自己动钱包，E1），
内部一律走 `MonetaryAuthority.mint`（唯一发行）→ `LedgerService.post`（唯一 Posting Core，E27）。

本阶段（M1.2）**只开放自助可领的 7 类**；其余类型（官方悬赏/合同/资助/采购/里程碑/活动/周活跃）
在各自业务流落地前**不可领**（`SELF_SERVICE_KINDS`，否则这个端点就成了"随便领钱"）：

| 类型 | 主体 | 金额（政策） | 资格 |
| --- | --- | --- | --- |
| `STARTER_GRANT` | 公司 | `starter_grant` | 公司存在，一次性（§15） |
| `PROFILE_COMPLETION` | 个人 | `profile_reward` | 用户已设显示名与头像（两者都有可写入口） |
| `COMPANY_PROFILE_COMPLETION` | 公司 | `company_profile_reward` | 公司自建岗位编制 ≥1 |
|  |  |  | （v1 公司资料字段注册后不可编辑，见下方口径说明） |
| `TUTORIAL_COMPLETION` | 个人 | `tutorial_reward` | 教程完成（按教程 id 各一次） |
| `DAILY_LOGIN` | 个人 | `daily_reward` | 每个 UTC 自然日一次 |
| `ACHIEVEMENT` | 公司 | `achievement_reward` | 成就事实成立（`first_employee` / `first_project`）|
|  |  |  | **需指定成就 code**，不做"点击即得" |
| `RECOVERY_GRANT` | 公司 | `recovery_grant` | 可花余额低于阈值 + 冷却期已过（§16） |

**幂等**：`unique(reward_type, actor_kind, actor_ref, reference_key)` 兜底（E10）；
重复领取返回既有 grant（`created=False`），绝不再 mint 第二次。
**原子性**：grant 行、mint 过账、状态置 POSTED 在同一事务（E13/E28）。
**政策快照**：`amount` 与 `policy_version` 落库，日后调政策不改历史（§30）。
**先有事实，后有奖励**：资格判定全部读既有业务事实（编制/员工/项目/教程/资料字段），不做"点击即得"。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.economy.contracts import EconomicActor, StateMachine, assert_transition
from app.economy.policy import EconomicPolicy, economic_policy
from app.events.bus import bus
from app.models.base import utcnow
from app.models.economy import RewardGrant
from app.models.enums import (
    Currency,
    EconomicActorKind,
    LedgerAccountKind,
    RewardStatus,
    RewardType,
    TutorialStatus,
)
from app.models.organization import Employee
from app.models.position import PositionDefinition
from app.models.project import Project
from app.repositories import economy as economy_repo
from app.services.economy.ledger import LedgerService
from app.services.economy.monetary import MonetaryAuthority

logger = get_logger(__name__)

#: 本阶段唯一可自助领取的奖励类型（其余类型在各自业务流落地前不可领，见模块 docstring）
SELF_SERVICE_KINDS = frozenset(
    {
        RewardType.starter_grant,
        RewardType.profile_completion,
        RewardType.company_profile_completion,
        RewardType.tutorial_completion,
        RewardType.daily_login,
        RewardType.achievement,
        RewardType.recovery_grant,
    }
)

#: 成就目录（code → 说明）。**先有事实，后有奖励**：每个 code 绑定一个既有业务事实。
ACHIEVEMENT_CODES: dict[str, str] = {
    "first_employee": "公司拥有第一位成员",
    "first_project": "公司启动第一个项目",
}

#: 公司档案补全的等价信号（见模块 docstring）：公司自建岗位编制数阈值
COMPANY_PROFILE_MIN_POSITIONS = 1


class RewardError(RuntimeError):
    """奖励领域错误（reason code 机器可读；API 层转 HTTP）。"""

    def __init__(self, reason: str, http_status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


@dataclass(frozen=True)
class RewardEvaluation:
    """某类型奖励对某个主体的当前状态（读面 / claim 前的判定结果）。"""

    reward_type: RewardType
    actor_kind: str
    actor_ref: int
    amount: int
    currency: str
    reference_key: str
    claimable: bool
    reason: str
    policy_version: str
    next_eligible_at: datetime | None = None
    grant: RewardGrant | None = None
    label: str = ""
    metadata: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- 事实判定


def _has_position_definition(db: Session, company_id: int) -> bool:
    return (
        int(
            db.execute(
                select(func.count())
                .select_from(PositionDefinition)
                .where(PositionDefinition.company_id == company_id)
            ).scalar_one()
        )
        >= COMPANY_PROFILE_MIN_POSITIONS
    )


def _has_employee(db: Session, company_id: int) -> bool:
    return (
        int(
            db.execute(
                select(func.count()).select_from(Employee).where(Employee.company_id == company_id)
            ).scalar_one()
        )
        > 0
    )


def _has_project(db: Session, company_id: int) -> bool:
    return (
        int(
            db.execute(
                select(func.count()).select_from(Project).where(Project.company_id == company_id)
            ).scalar_one()
        )
        > 0
    )


#: 成就 code → 事实判定（只读既有业务表；不新增成就系统）
ACHIEVEMENT_FACTS: dict[str, Callable[[Session, int], bool]] = {
    "first_employee": _has_employee,
    "first_project": _has_project,
}


def _utc_day(moment: datetime | None = None) -> str:
    return (moment or utcnow()).astimezone(UTC).date().isoformat()


def next_daily_boundary(moment: datetime | None = None) -> datetime:
    """下一个 UTC 零点（每日奖励的"下次可领"时间）。"""
    current = (moment or utcnow()).astimezone(UTC)
    return (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)


def achievement_code(reference_key: str | None) -> str | None:
    """从 `first_employee` / `achievement:first_employee` 归一化出成就 code。"""
    if not reference_key:
        return None
    code = reference_key.removeprefix("achievement:").strip()
    return code or None


# --------------------------------------------------------------------------- 资格判定


def _starter_eligible(service: RewardService, **_: object) -> tuple[bool, str, datetime | None]:
    return True, "ok", None


def _profile_eligible(service: RewardService, *, actor: EconomicActor, **_: object):
    from app.models.auth import User

    user = service.db.get(User, int(actor.ref))
    if user is None:  # pragma: no cover - 主体来自身份/成员关系，必然存在
        return False, "not_eligible", None
    if user.display_name.strip() and user.avatar.strip():
        return True, "ok", None
    return False, "not_eligible", None


def _company_profile_eligible(service: RewardService, *, company_id: int, **_: object):
    if _has_position_definition(service.db, company_id):
        return True, "ok", None
    return False, "not_eligible", None


def _tutorial_reference_keys(service: RewardService, *, actor: EconomicActor) -> list[str]:
    """该用户"已完成"的教程 id（升序，稳定）。"""
    from app.models.project_delivery import TutorialProgress

    rows = service.db.scalars(
        select(TutorialProgress.tutorial_id)
        .where(
            TutorialProgress.user_id == int(actor.ref),
            TutorialProgress.status == TutorialStatus.completed.value,
        )
        .order_by(TutorialProgress.tutorial_id)
    )
    return [f"tutorial:{tutorial_id}" for tutorial_id in rows]


def _tutorial_eligible(
    service: RewardService, *, actor: EconomicActor, reference_key: str | None = None, **_: object
):
    keys = _tutorial_reference_keys(service, actor=actor)
    if not keys:
        return False, "not_eligible", None
    wanted = reference_key or keys[0]
    if wanted not in keys:
        return False, "not_eligible", None
    return True, "ok", None


def _daily_eligible(service: RewardService, **_: object):
    return True, "ok", None


def _achievement_eligible(
    service: RewardService, *, company_id: int, reference_key: str | None = None, **_: object
):
    code = achievement_code(reference_key)
    if code is None or code not in ACHIEVEMENT_FACTS:
        return False, "reference_required", None
    if ACHIEVEMENT_FACTS[code](service.db, company_id):
        return True, "ok", None
    return False, "not_eligible", None


def _recovery_eligible(
    service: RewardService, *, actor: EconomicActor, company_id: int, **_: object
):
    available = service.company_available_balance(company_id)
    if available >= service.policy.recovery_threshold:
        return False, "recovery_not_needed", None
    last = economy_repo.latest_posted_reward_grant(
        service.db,
        reward_type=RewardType.recovery_grant.value,
        actor_kind=actor.kind.value,
        actor_ref=economy_repo.actor_columns(actor)[1],
    )
    if last is not None:
        posted_at = last.posted_at or last.created_at
        next_at = posted_at + timedelta(hours=service.policy.recovery_cooldown_hours)
        if utcnow() < next_at:
            return False, "cooldown", next_at.astimezone(UTC)
    return True, "ok", None


# --------------------------------------------------------------------------- 引用键


def _simple_key(value: str) -> Callable[..., str]:
    def resolve(service: RewardService, *, reference_key: str | None, actor: EconomicActor) -> str:
        return value

    return resolve


def _tutorial_key(
    service: RewardService, *, reference_key: str | None, actor: EconomicActor
) -> str:
    """教程奖励的引用键：显式指定优先，否则**取第一个"已完成且未领过"的教程**。

    每个教程各一次（`tutorial:<id>`）；全部领完时回落到第一条，让判定显示
    `already_claimed`（而不是造出一个新的假 key）。
    """
    if reference_key:
        return (
            reference_key if reference_key.startswith("tutorial:") else f"tutorial:{reference_key}"
        )
    keys = _tutorial_reference_keys(service, actor=actor)
    if not keys:
        return "tutorial:none"
    claimed = {
        grant.reference_key
        for grant in economy_repo.list_reward_grants(
            service.db,
            actor_kind=actor.kind.value,
            actor_ref=economy_repo.actor_columns(actor)[1],
            reward_type=RewardType.tutorial_completion.value,
        )
    }
    for key in keys:
        if key not in claimed:
            return key
    return keys[0]


def _daily_key(service: RewardService, *, reference_key: str | None, actor: EconomicActor) -> str:
    return f"daily:{_utc_day()}"


def _achievement_key(
    service: RewardService, *, reference_key: str | None, actor: EconomicActor
) -> str:
    code = achievement_code(reference_key)
    return f"achievement:{code}" if code else "achievement:?"


def _recovery_key(
    service: RewardService, *, reference_key: str | None, actor: EconomicActor
) -> str:
    return f"recovery:{_utc_day()}"


def _no_metadata(service: RewardService, **_: object) -> dict:
    return {}


def _achievement_metadata(
    service: RewardService, *, company_id: int, reference_key: str | None = None, **_: object
) -> dict:
    code = achievement_code(reference_key)
    return {"achievement": code} if code else {}


# --------------------------------------------------------------------------- 规格表


@dataclass(frozen=True)
class _RewardSpec:
    """一种奖励的静态定义（金额来自政策；资格是判定函数，不是散落的阈值魔法）。"""

    actor_scope: EconomicActorKind
    label: str
    amount: Callable[[EconomicPolicy], int]
    eligible: Callable[..., tuple[bool, str, datetime | None]]
    reference_key: Callable[..., str]
    metadata: Callable[..., dict] = _no_metadata
    currency: Currency = Currency.credit


_SPECS: dict[RewardType, _RewardSpec] = {
    RewardType.starter_grant: _RewardSpec(
        actor_scope=EconomicActorKind.company,
        label="启动资金",
        amount=lambda policy: policy.starter_grant,
        eligible=_starter_eligible,
        reference_key=_simple_key("starter"),
    ),
    RewardType.profile_completion: _RewardSpec(
        actor_scope=EconomicActorKind.user,
        label="个人资料完善",
        amount=lambda policy: policy.profile_reward,
        eligible=_profile_eligible,
        reference_key=_simple_key("profile"),
    ),
    RewardType.company_profile_completion: _RewardSpec(
        actor_scope=EconomicActorKind.company,
        label="公司编制/档案建成",
        amount=lambda policy: policy.company_profile_reward,
        eligible=_company_profile_eligible,
        reference_key=_simple_key("profile"),
    ),
    RewardType.tutorial_completion: _RewardSpec(
        actor_scope=EconomicActorKind.user,
        label="教程完成",
        amount=lambda policy: policy.tutorial_reward,
        eligible=_tutorial_eligible,
        reference_key=_tutorial_key,
    ),
    RewardType.daily_login: _RewardSpec(
        actor_scope=EconomicActorKind.user,
        label="每日登录",
        amount=lambda policy: policy.daily_reward,
        eligible=_daily_eligible,
        reference_key=_daily_key,
    ),
    RewardType.achievement: _RewardSpec(
        actor_scope=EconomicActorKind.company,
        label="成就",
        amount=lambda policy: policy.achievement_reward,
        eligible=_achievement_eligible,
        reference_key=_achievement_key,
        metadata=_achievement_metadata,
    ),
    RewardType.recovery_grant: _RewardSpec(
        actor_scope=EconomicActorKind.company,
        label="破产兜底",
        amount=lambda policy: policy.recovery_grant,
        eligible=_recovery_eligible,
        reference_key=_recovery_key,
    ),
}


# --------------------------------------------------------------------------- 服务


class RewardService:
    """奖励的判定 / 领取（公司作用域 + 个人主体）。"""

    def __init__(self, db: Session, *, policy: EconomicPolicy | None = None) -> None:
        self.db = db
        self.policy = policy or economic_policy()

    # ---------------------------------------------------------------- 读面

    def catalog(self, *, company_id: int, user_id: int | None) -> list[RewardEvaluation]:
        """公司 + 当前个人的全部自助奖励及其可领状态。

        成就按 code 逐条展开；教程按"已完成 / 已领过"的 id 逐条展开 ——
        每一行都有明确的 `reference_key`，UI 与 API 不需要猜。
        """
        evaluations: list[RewardEvaluation] = []
        user_actor = self.resolve_user_actor(company_id=company_id, user_id=user_id)
        for reward_type in sorted(SELF_SERVICE_KINDS, key=lambda kind: kind.value):
            if _SPECS[reward_type].actor_scope is EconomicActorKind.user and user_actor is None:
                continue  # 没有个人主体（无会话且公司无 OWNER）：个人奖励不列出
            if reward_type is RewardType.achievement:
                for code in sorted(ACHIEVEMENT_CODES):
                    evaluations.append(
                        self.evaluate(
                            reward_type, company_id=company_id, user_id=user_id, reference_key=code
                        )
                    )
                continue
            if reward_type is RewardType.tutorial_completion and user_actor is not None:
                keys = _tutorial_reference_keys(self, actor=user_actor)
                if keys:
                    for key in keys:
                        evaluations.append(
                            self.evaluate(
                                reward_type,
                                company_id=company_id,
                                user_id=user_id,
                                reference_key=key,
                            )
                        )
                    continue
            evaluations.append(self.evaluate(reward_type, company_id=company_id, user_id=user_id))
        return evaluations

    def evaluate(
        self,
        reward_type: RewardType,
        *,
        company_id: int,
        user_id: int | None,
        reference_key: str | None = None,
    ) -> RewardEvaluation:
        """判定当前状态（纯读，不写库；`grant` 非空表示已领过）。"""
        if reward_type not in SELF_SERVICE_KINDS:
            raise RewardError("not_self_service", http_status=409)
        spec = _SPECS[reward_type]
        actor = self._actor_for(spec, company_id=company_id, user_id=user_id)
        actor_kind, actor_ref = economy_repo.actor_columns(actor)
        reference = spec.reference_key(self, reference_key=reference_key, actor=actor)

        existing = economy_repo.find_reward_grant(
            self.db,
            reward_type=reward_type.value,
            actor_kind=actor_kind,
            actor_ref=actor_ref,
            reference_key=reference,
        )
        if existing is not None:
            return RewardEvaluation(
                reward_type=reward_type,
                actor_kind=actor_kind,
                actor_ref=actor_ref,
                amount=int(existing.amount),
                currency=existing.currency,
                reference_key=reference,
                claimable=False,
                reason="already_claimed",
                policy_version=existing.policy_version,
                grant=existing,
                label=spec.label,
                metadata=dict(existing.metadata_json or {}),
            )

        ok, reason, next_eligible_at = spec.eligible(
            self, actor=actor, company_id=company_id, reference_key=reference
        )
        return RewardEvaluation(
            reward_type=reward_type,
            actor_kind=actor_kind,
            actor_ref=actor_ref,
            amount=spec.amount(self.policy),
            currency=spec.currency.value,
            reference_key=reference,
            claimable=ok,
            reason=reason,
            policy_version=self.policy.version,
            next_eligible_at=next_eligible_at,
            label=spec.label,
            metadata=spec.metadata(
                self, actor=actor, company_id=company_id, reference_key=reference
            ),
        )

    # ---------------------------------------------------------------- 领取

    def claim(
        self,
        reward_type: RewardType,
        *,
        company_id: int,
        user_id: int | None,
        reference_key: str | None = None,
        commit: bool = True,
    ) -> tuple[RewardGrant, bool]:
        """领取（幂等）：返回 `(grant, created)`。

        - 命中既有 grant（同类型/主体/reference_key）⇒ `created=False`，**不再 mint**；
        - 资格不足 ⇒ `RewardError`（机器可读 reason）；
        - 并发下唯一约束裁定赢家，输家回滚后复用赢家的 grant（与账本幂等同一纪律）。
        """
        evaluation = self.evaluate(
            reward_type, company_id=company_id, user_id=user_id, reference_key=reference_key
        )
        if evaluation.grant is not None:
            return evaluation.grant, False
        if not evaluation.claimable:
            raise RewardError(evaluation.reason, http_status=409)

        actor = self._actor_for(_SPECS[reward_type], company_id=company_id, user_id=user_id)
        try:
            grant = self._post_grant(evaluation, actor=actor, company_id=company_id)
        except IntegrityError:
            # 并发重复领取：唯一索引裁定赢家，输家回滚后复用赢家的 grant（不重复发钱）
            self.db.rollback()
            existing = economy_repo.find_reward_grant(
                self.db,
                reward_type=reward_type.value,
                actor_kind=evaluation.actor_kind,
                actor_ref=evaluation.actor_ref,
                reference_key=evaluation.reference_key,
            )
            if existing is None:  # pragma: no cover - 唯一冲突必然来自同 key 的另一笔
                raise
            return existing, False
        except Exception:
            if commit:
                self.db.rollback()
            raise

        if commit:
            try:
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
            self._publish(grant)
        return grant, True

    # ---------------------------------------------------------------- 内部

    def _post_grant(
        self, evaluation: RewardEvaluation, *, actor: EconomicActor, company_id: int
    ) -> RewardGrant:
        """同一事务：grant(CLAIMED) → mint → grant(POSTED) + 回填 ledger_transaction_id。

        状态迁移走 M1.0 冻结状态机（§37）：ELIGIBLE → CLAIMED → POSTED。
        """
        now = utcnow()
        assert_transition(
            StateMachine.reward, RewardStatus.eligible.value, RewardStatus.claimed.value
        )
        grant = economy_repo.insert_reward_grant(
            self.db,
            reward_type=evaluation.reward_type.value,
            actor_kind=evaluation.actor_kind,
            actor_ref=evaluation.actor_ref,
            company_id=company_id,
            amount=evaluation.amount,
            currency=evaluation.currency,
            reference_key=evaluation.reference_key,
            reason=evaluation.reward_type.value.lower(),
            policy_version=evaluation.policy_version,
            status=RewardStatus.claimed.value,
            claimed_at=now,
            metadata_json=dict(evaluation.metadata),
        )
        transaction = (
            MonetaryAuthority(self.db)
            .mint(
                actor=actor,
                amount=evaluation.amount,
                reason=f"reward:{evaluation.reward_type.value}",
                reference_type="reward",
                reference_id=f"{evaluation.reward_type.value}:{evaluation.reference_key}",
                idempotency_key=(
                    f"reward:{evaluation.reward_type.value}:"
                    f"{evaluation.actor_kind}:{evaluation.actor_ref}:{evaluation.reference_key}"
                ),
                metadata={
                    "reward_grant_id": int(grant.id),
                    "reward_type": evaluation.reward_type.value,
                    "policy_version": evaluation.policy_version,
                },
                commit=False,
            )
            .transaction
        )
        assert_transition(
            StateMachine.reward, RewardStatus.claimed.value, RewardStatus.posted.value
        )
        grant.status = RewardStatus.posted.value
        grant.ledger_transaction_id = int(transaction.id)
        grant.posted_at = utcnow()
        self.db.flush()
        return grant

    def _actor_for(
        self, spec: _RewardSpec, *, company_id: int, user_id: int | None
    ) -> EconomicActor:
        if spec.actor_scope is EconomicActorKind.company:
            return EconomicActor.company(company_id)
        actor = self.resolve_user_actor(company_id=company_id, user_id=user_id)
        if actor is None:
            raise RewardError("no_user_context", http_status=409)
        return actor

    def resolve_user_actor(self, *, company_id: int, user_id: int | None) -> EconomicActor | None:
        """个人奖励的主体解析：请求身份优先，其次公司 OWNER。

        与 `resolve_company_id` 同一纪律：不新增鉴权模型，沿用既有 seam（v1 单公司部署；
        生产环境由登录会话提供 `user_id`，测试/无会话时回落到 OWNER 成员关系）。
        """
        if user_id is not None:
            return EconomicActor(EconomicActorKind.user, int(user_id))
        from app.models.auth import CompanyMembership

        owner_id = self.db.scalars(
            select(CompanyMembership.user_id)
            .where(
                CompanyMembership.company_id == company_id,
                CompanyMembership.role == "OWNER",
            )
            .order_by(CompanyMembership.id)
        ).first()
        if owner_id is None:
            return None
        return EconomicActor(EconomicActorKind.user, int(owner_id))

    def company_available_balance(self, company_id: int) -> int:
        """公司可花余额（读投影缓存 = CAS 口径；救援金阈值判定用）。"""
        from app.services.economy.accounts import AccountService

        actor = EconomicActor.company(company_id)
        accounts = AccountService(self.db).accounts_for_actor(
            actor, kinds=(LedgerAccountKind.actor,)
        )
        total = 0
        for account in accounts:
            projection = economy_repo.get_projection(self.db, int(account.id))
            total += int(projection.available_balance) if projection is not None else 0
        return total

    def _publish(self, grant: RewardGrant) -> None:
        bus.publish(
            "reward.granted",
            {
                "grant_id": int(grant.id),
                "reward_type": grant.reward_type,
                "actor_kind": grant.actor_kind,
                "actor_ref": int(grant.actor_ref),
                "amount": int(grant.amount),
                "currency": grant.currency,
                "reference_key": grant.reference_key,
                "policy_version": grant.policy_version,
                "ledger_transaction_id": grant.ledger_transaction_id,
            },
            company_id=int(grant.company_id),
        )
        logger.info(
            "reward granted type=%s actor=%s:%s amount=%d",
            grant.reward_type,
            grant.actor_kind,
            grant.actor_ref,
            grant.amount,
        )


def ledger_supply_snapshot(db: Session):
    """供给快照（奖励发放后核对 `supply = minted − burned` 的口径来源）。"""
    return LedgerService(db).supply()
