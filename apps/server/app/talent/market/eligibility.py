"""Market Eligibility（T2.2）—— 挂牌/招募资格的唯一判定处。

设计 §4 的三轴模型（正交，禁止一个 lifecycle 表达三个维度）：

```
Cultivation    cultivating → ready                    （落库 character_profiles.lifecycle）
Market         unavailable / unlisted / listed        （派生：listing 存在性 + 前两轴）
Employment     unemployed / employed                  （派生：employments 生效 primary 行）
```

**唯一入口**：`can_list` / `can_recruit`（DB 包装）+ 纯函数 `can_list_axes` / `can_recruit_axes`
（判定矩阵可脱离数据库测试）。任何 endpoint / service 都不得自己复制这套判断。

边界（T2 设计 D1/D10）：
- 结业与资格**不看任何能力分**：能力一般、证据不足、甚至全部 unrated 的人也允许成为人才
  （市场价值由买方自行判断）；
- 不做货币/价格判断（M1）；
- listing 表属 T2.3：在那之前「已挂牌」分支不可达（`listed` 需要一个 active listing 行）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from app.models.enums import CultivationState, EmploymentState
from app.repositories import cultivation as cultivation_repo
from app.repositories import organization as org_repo
from app.repositories import position as position_repo
from app.talent.market.contracts import MarketState


class EligibilityReason(StrEnum):
    """拒绝原因（机器可读）：API detail / UI 提示都用它，不靠自由文本。"""

    ok = "ok"
    not_ready = "not_ready"  # 培养未完成（cultivating 或没有角色档案）
    employed = "employed"  # 已有生效主职：不能再挂牌，也不能被招募
    already_listed = "already_listed"  # 已挂牌：不能重复挂牌
    not_listed = "not_listed"  # 未挂牌：不能通过市场招募


@dataclass(frozen=True)
class PersonAxes:
    """三轴快照（判定输入；纯数据，不依赖 Session）。"""

    cultivation_state: str | None  # None = 没有角色档案（纯入职员工）
    employment_state: EmploymentState
    market_state: MarketState


@dataclass(frozen=True)
class EligibilityDecision:
    allowed: bool
    reason: EligibilityReason
    axes: PersonAxes

    @property
    def reason_code(self) -> str:
        return self.reason.value


# ---- 纯判定（可脱离数据库测试的矩阵） ----


def can_list_axes(axes: PersonAxes) -> EligibilityDecision:
    """挂牌资格：养成完成 + 无生效主职 + 未在市。"""
    if axes.cultivation_state != CultivationState.ready.value:
        return EligibilityDecision(False, EligibilityReason.not_ready, axes)
    if axes.employment_state is EmploymentState.employed:
        return EligibilityDecision(False, EligibilityReason.employed, axes)
    if axes.market_state is MarketState.listed:
        return EligibilityDecision(False, EligibilityReason.already_listed, axes)
    return EligibilityDecision(True, EligibilityReason.ok, axes)


def can_recruit_axes(axes: PersonAxes) -> EligibilityDecision:
    """招募资格：在市（存在 active listing）+ 无生效主职。"""
    if axes.market_state is not MarketState.listed:
        return EligibilityDecision(False, EligibilityReason.not_listed, axes)
    if axes.employment_state is EmploymentState.employed:
        return EligibilityDecision(False, EligibilityReason.employed, axes)
    return EligibilityDecision(True, EligibilityReason.ok, axes)


# ---- 三轴读面（DB） ----


def cultivation_state(db: Session, person_id: int) -> str | None:
    profile = cultivation_repo.get_profile_by_person(db, person_id)
    return profile.lifecycle if profile is not None else None


def employment_state(db: Session, person_id: int) -> EmploymentState:
    """由 `employments` 的生效 primary 行派生（与 WorkforceStatusResolver 同源口径）。"""
    employee = org_repo.get_employee_by_person(db, person_id)
    if employee is None:
        return EmploymentState.unemployed
    assignment = position_repo.active_primary_assignment(db, int(employee.id))
    return EmploymentState.employed if assignment is not None else EmploymentState.unemployed


def market_state(db: Session, person_id: int) -> MarketState:
    """市场态派生（单轴读面）。

    T2.3 之前不存在 `market_listings` ⇒「listed」不可达；此处按前两轴给
    `unlisted`（有资格、未挂牌）或 `unavailable`（还不具备资格）。T2.3 落地后在
    **这里**加一个 active listing 分支即可，调用方（`person_axes`）无需改动。
    """
    cultivation = cultivation_state(db, person_id)
    employment = employment_state(db, person_id)
    if cultivation == CultivationState.ready.value and employment is EmploymentState.unemployed:
        return MarketState.unlisted
    return MarketState.unavailable


def person_axes(db: Session, person_id: int) -> PersonAxes:
    return PersonAxes(
        cultivation_state=cultivation_state(db, person_id),
        employment_state=employment_state(db, person_id),
        market_state=market_state(db, person_id),
    )


def can_list(db: Session, person_id: int) -> EligibilityDecision:
    return can_list_axes(person_axes(db, person_id))


def can_recruit(db: Session, person_id: int) -> EligibilityDecision:
    return can_recruit_axes(person_axes(db, person_id))
