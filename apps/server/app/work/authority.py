"""Authority Projection —— 管理授权的解析与**校验**（M2.2，W37–W40）。

这张模块只做两件事：

1. **解析**：`employee → 生效 PRIMARY 任职 → PositionSlot → PositionDefinition → grants`
   （`effective_grants`）；
2. **校验**：这次管理动作**在不在授权范围内**（`authorizes`）。

它**绝不做**的第三件事：判断这个动作**该不该做**。系统只回答
「你有没有被授权」，不回答「你要不要拆这个任务 / 该选谁 / 该不该返工」——
后者是 Manager Agent 或 Owner 的判断（W39 / 设计 §1）。

四条硬纪律（用户拍板 + 设计 §4.1）：

* **default-deny**（W37）：任何一步解析不出来（无在职、无 grant、作用域无法确认、
  金额缺失）一律**拒绝**，并给出机器可读的原因码。系统从不"默认允许"。
* **随任职生效/失效**（W38）：授权不落在 person 上。人一卸任，任职关闭 ⇒ 立即失效。
* **append-only + 时间窗**（W40）：改授权 = 关旧行 + 插新行；语义字段永不 UPDATE。
  因此 `at=` 可以回溯"当时这个职位有什么授权"，历史 DecisionRecord 可解释。
* **绝不读 `employee.role` 字符串**（也绝不读 `legacy_role` / Fit 分数 /
  访问包）判定权限 —— 由 `FORBIDDEN_AUTHORITY_SOURCES` + AST 守卫钉死。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.base import utcnow
from app.models.enums import AssignmentType, AuthorityScopeKind
from app.models.organization import Department, Employee
from app.models.position import PositionAssignment, PositionAuthorityGrant, PositionDefinition
from app.models.position import PositionSlot as SlotModel
from app.repositories import position as position_repo
from app.work import contracts as C


class AuthorityError(ValueError):
    """授权声明本身非法（不只是"这次动作被拒绝"）。"""


def _naive(moment: datetime) -> datetime:
    """统一成 **naive UTC** 再比较。

    `DateTime` 列没有时区：库里读回来是 naive，而 `utcnow()` 是 aware ——
    两者一比直接 `TypeError: can't compare offset-naive and offset-aware datetimes`
    （`position_service.py` 里已经踩过同一个坑）。所以时间在进入比较前抹平一次，
    不把雷埋给调用方。系统里所有时间都是 UTC，抹掉 tzinfo 不丢信息。
    """
    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


@dataclass(frozen=True)
class EffectiveGrant:
    """一条**生效中**的授权（解析结果，只读投影）。"""

    grant_id: int
    kind: C.AuthorityKind
    scope_kind: AuthorityScopeKind
    scope_ref: int
    max_amount: int | None
    effective_from: datetime
    position_definition_id: int

    def to_contract(self) -> C.AuthorityGrant:
        return C.AuthorityGrant(
            kind=self.kind,
            scope_kind=self.scope_kind,
            scope_ref=self.scope_ref,
            max_amount=self.max_amount,
            grant_id=self.grant_id,
        )


@dataclass(frozen=True)
class ActorAuthority:
    """ "这个人在此刻以什么职位持有哪些授权"的完整解析结果。"""

    employee_id: int
    company_id: int
    position_definition_id: int | None = None
    position_code: str | None = None
    slot_id: int | None = None
    department_id: int | None = None
    grants: tuple[EffectiveGrant, ...] = ()

    @property
    def contract_grants(self) -> tuple[C.AuthorityGrant, ...]:
        return tuple(grant.to_contract() for grant in self.grants)

    @property
    def grants_hash(self) -> str:
        return C.hash_effective_grants(self.contract_grants)


# ---------------------------------------------------------------------------
# 解析（只读）
# ---------------------------------------------------------------------------


def _active_primary_assignment(db: Session, employee_id: int) -> PositionAssignment | None:
    """生效中的主职 —— 与 `position_repo` 用同一把尺（不另写条件）。"""
    for assignment in position_repo.active_assignments(db, int(employee_id)):
        if (
            assignment.assignment_type == AssignmentType.primary.value
            and assignment.is_primary
            and assignment.effective_to is None
            and assignment.position_slot_id is not None
        ):
            return assignment
    return None


def _grant_rows(
    db: Session, definition_id: int, *, at: datetime | None
) -> list[PositionAuthorityGrant]:
    """某职位在 `at` 时刻生效的 grant 行（`at=None` = 现在）。

    时间窗语义（W40）：`effective_from <= at` 且（`effective_to IS NULL` 或 `effective_to > at`）。
    用 `>` 而不是 `>=`：关闭时刻与开新行时刻相同，旧行在关闭那一刻即失效。
    """
    moment = _naive(at or utcnow())
    stmt = (
        select(PositionAuthorityGrant)
        .where(
            PositionAuthorityGrant.position_definition_id == int(definition_id),
            PositionAuthorityGrant.effective_from <= moment,
        )
        .order_by(PositionAuthorityGrant.id)
    )
    rows = list(db.scalars(stmt))
    return [row for row in rows if row.effective_to is None or row.effective_to > moment]


def resolve_actor_authority(
    db: Session, employee_id: int, *, at: datetime | None = None
) -> ActorAuthority:
    """解析"这个人在 `at` 时刻以哪个职位持有哪些授权"（只读，无副作用）。"""
    employee = db.get(Employee, int(employee_id))
    if employee is None or employee.company_id is None:  # pragma: no cover - 防御
        raise AuthorityError(f"employee {employee_id} not found or has no company")
    company_id = int(employee.company_id)
    assignment = _active_primary_assignment(db, int(employee_id))
    if assignment is None:
        # W38：没有生效主职 ⇒ 没有职位 ⇒ 没有授权（不是"回溯到上一任"）
        return ActorAuthority(employee_id=int(employee_id), company_id=company_id)

    slot = db.get(SlotModel, int(assignment.position_slot_id))
    if slot is None:  # pragma: no cover - 防御：任职指向缺失编制
        return ActorAuthority(
            employee_id=int(employee_id),
            company_id=company_id,
            slot_id=int(assignment.position_slot_id),
        )
    definition = db.get(PositionDefinition, int(slot.position_definition_id))
    if definition is None:  # pragma: no cover - 防御
        return ActorAuthority(
            employee_id=int(employee_id),
            company_id=company_id,
            slot_id=int(slot.id),
            department_id=int(slot.department_id),
        )

    grants = tuple(
        EffectiveGrant(
            grant_id=int(row.id),
            kind=C.AuthorityKind(row.authority_kind),
            scope_kind=AuthorityScopeKind(row.scope_kind),
            scope_ref=int(row.scope_ref or 0),
            max_amount=row.max_amount,
            effective_from=row.effective_from,
            position_definition_id=int(definition.id),
        )
        for row in _grant_rows(db, int(definition.id), at=at)
    )
    return ActorAuthority(
        employee_id=int(employee_id),
        company_id=company_id,
        position_definition_id=int(definition.id),
        position_code=definition.code,
        slot_id=int(slot.id),
        department_id=int(slot.department_id),
        grants=grants,
    )


def effective_grants(
    db: Session, employee_id: int, *, at: datetime | None = None
) -> tuple[EffectiveGrant, ...]:
    """这个人在 `at` 时刻生效的授权（便捷入口；`at=None` = 现在）。"""
    return resolve_actor_authority(db, employee_id, at=at).grants


def authority_snapshot_for(db: Session, employee_id: int, *, at: datetime | None = None) -> dict:
    """给 `DecisionRecord.context_snapshot` 用的**授权事实快照**（W40）。

    只含事实，且可重算对拍：拿当时的 grant 行重跑 `hash_effective_grants` 即可。
    """
    actor = resolve_actor_authority(db, employee_id, at=at)
    return {
        "snapshot_version": C.AUTHORITY_SNAPSHOT_VERSION,
        "actor_employee_id": actor.employee_id,
        "position_definition_id": actor.position_definition_id,
        "position_code": actor.position_code,
        "grant_ids": [grant.grant_id for grant in actor.grants],
        "grant_scopes": [
            [
                grant.kind.value,
                grant.scope_kind.value,
                str(grant.scope_ref),
                str(grant.max_amount or ""),
            ]
            for grant in actor.grants
        ],
        "position_grants_hash": actor.grants_hash,
        "evaluated_at": _naive(at or utcnow()).isoformat(),
    }


# ---------------------------------------------------------------------------
# 校验（只回答"在不在授权内"）
# ---------------------------------------------------------------------------


def _scope_allows(
    db: Session,
    grant: EffectiveGrant,
    target: C.AuthorityTarget | None,
    *,
    actor: ActorAuthority,
) -> bool:
    """作用域判定。**刻意要求目标明确**：说不清作用域 = 拒绝（default-deny）。"""
    if grant.scope_kind is AuthorityScopeKind.company:
        if target is None:
            return True  # 公司级动作不指向具体对象（例如"创建项目"）
        if target.company_id is not None and int(target.company_id) != actor.company_id:
            return False
        # 作用域是"我的公司"，所以被指向的对象也必须真的在本公司里 ——
        # 只信 target 里传进来的 id 等于把隔离交给调用方，那不是隔离。
        if target.department_id is not None:
            department = db.get(Department, int(target.department_id))
            if department is None or int(department.company_id) != actor.company_id:
                return False
        if target.employee_id is not None:
            employee = db.get(Employee, int(target.employee_id))
            if employee is None or int(employee.company_id) != actor.company_id:
                return False
        return True
    if grant.scope_kind is AuthorityScopeKind.department:
        if target is None or target.department_id is None:
            return False  # 部门作用域必须能确认"就是我这个部门"
        return int(target.department_id) == grant.scope_ref
    if grant.scope_kind is AuthorityScopeKind.direct_reports:
        if target is None or target.employee_id is None:
            return False
        return int(target.employee_id) in direct_report_employee_ids(db, actor)
    return False  # pragma: no cover - 枚举封闭；未知作用域一律拒绝


def authorizes(
    db: Session,
    *,
    employee_id: int,
    kind: C.AuthorityKind,
    target: C.AuthorityTarget | None = None,
    amount: int | None = None,
    at: datetime | None = None,
) -> C.AuthorityDecision:
    """**唯一**的授权校验入口：这个人在此刻是否被授权执行 `kind`。

    返回 `AuthorityDecision`（allowed + 机器可读原因码 + 命中的授权行）。
    校验**只**看事实：任职、grant 行、作用域、额度。
    它不看 `employee.role`、不看 Fit、不看访问包、不看工作模式（W37/W39）。
    """
    moment = _naive(at or utcnow())
    actor = resolve_actor_authority(db, employee_id, at=moment)
    contract_grants = actor.contract_grants

    def decide(
        allowed: bool, reason: str, hits: tuple[EffectiveGrant, ...] = ()
    ) -> C.AuthorityDecision:
        chosen = tuple(hit.to_contract() for hit in hits) if hits else contract_grants
        return C.AuthorityDecision(
            allowed=allowed,
            kind=kind,
            reason=reason,
            actor_employee_id=int(employee_id),
            position_definition_id=actor.position_definition_id,
            grants=chosen,
            evaluated_at=moment,
            grants_hash=C.hash_effective_grants(chosen),
            position_grants_hash=C.hash_effective_grants(contract_grants),
        )

    if actor.position_definition_id is None:
        return decide(False, "no_active_assignment")

    candidate = [grant for grant in actor.grants if grant.kind is kind]
    if not candidate:
        return decide(False, "no_grant")

    # 作用域 + 额度：任一候选满足即通过；都不满足则按"最接近的原因"拒绝。
    scope_failed = False
    amount_failed = ""
    for grant in candidate:
        if kind in C.SELF_TARGET_FORBIDDEN_AUTHORITIES:
            if target is not None and target.employee_id == int(employee_id):
                return decide(False, "self_target", (grant,))
        if not _scope_allows(db, grant, target, actor=actor):
            scope_failed = True
            continue
        if kind in C.AMOUNT_BEARING_AUTHORITIES:
            if amount is None:
                amount_failed = "amount_required"
                continue
            if grant.max_amount is None:
                amount_failed = "grant_has_no_amount_ceiling"
                continue
            if int(amount) > int(grant.max_amount):
                amount_failed = "amount_exceeds_grant"
                continue
        return decide(True, "authorized", (grant,))

    if scope_failed:
        return decide(False, "scope_mismatch")
    return decide(False, amount_failed or "no_grant")


def requires(
    db: Session,
    *,
    employee_id: int,
    kind: C.AuthorityKind,
    target: C.AuthorityTarget | None = None,
    amount: int | None = None,
    at: datetime | None = None,
) -> C.AuthorityDecision:
    """`authorizes` 的**断言式**别名：调用方要在拒绝时抛错时用它。

    名字刻意不叫 `allow` / `can` —— 它不提供任何"可以就这么办"的暗示，
    只表达"必须有这项授权"（W39）。
    """
    decision = authorizes(
        db, employee_id=employee_id, kind=kind, target=target, amount=amount, at=at
    )
    if not decision.allowed:
        raise AuthorityError(f"not authorized: {kind.value} ({decision.reason})")
    return decision


# ---------------------------------------------------------------------------
# 组织树（作用域判定的地基；只读）
# ---------------------------------------------------------------------------


def slot_descendant_ids(db: Session, *, company_id: int, root_slot_id: int) -> tuple[int, ...]:
    """`root_slot_id` 的**汇报子树**（含自身）—— 由 `position_slots.manager_slot_id` 派生。

    迭代遍历 + 访问集去重：组织树里出现环（人工改库造出来的）时**不**死循环，
    只是把已访问的坑跳过。系统不因为数据坏了就拒绝服务，也不假装它是良构的。
    """
    slots = position_repo.list_slots(db, company_id=company_id)
    children: dict[int, list[int]] = {}
    for slot in slots:
        if slot.manager_slot_id is None:
            continue
        children.setdefault(int(slot.manager_slot_id), []).append(int(slot.id))

    seen: set[int] = set()
    queue = [int(root_slot_id)]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        queue.extend(children.get(current, ()))
    return tuple(sorted(seen))


def direct_report_employee_ids(db: Session, actor: ActorAuthority) -> tuple[int, ...]:
    """`actor` 的汇报子树里**在任**的 employee id（默认不含自己）。

    自己不在集合里：`direct_reports` 作用域表达的是"我管的人"，
    把自己算进去会让"CEO 可以对自己执行 offboard"从侧门溜进来。
    """
    if actor.slot_id is None or actor.position_definition_id is None:
        return ()
    subtree = slot_descendant_ids(db, company_id=actor.company_id, root_slot_id=int(actor.slot_id))
    if not subtree:
        return ()
    held = position_repo.incumbents_by_slot(db, list(subtree))
    return tuple(
        sorted(
            int(employee_id)
            for employee_ids in held.values()
            for employee_id in employee_ids
            if int(employee_id) != actor.employee_id
        )
    )


# ---------------------------------------------------------------------------
# 声明（append-only 写侧；M2.2 只有 seed 与测试用它，玩家面没有写端点）
# ---------------------------------------------------------------------------


def grant_authority(
    db: Session,
    *,
    position_definition_id: int,
    kind: C.AuthorityKind,
    scope_kind: AuthorityScopeKind = AuthorityScopeKind.company,
    scope_ref: int = 0,
    max_amount: int | None = None,
    granted_by_user_id: int | None = None,
    note: str = "",
    commit: bool = True,
) -> PositionAuthorityGrant:
    """声明一项授权（**append-only**：只插行，不改行）。

    幂等：同一 (职位, 授权, 作用域, scope_ref) 已有生效行时**直接返回该行**，
    不制造第二条（并发/重试由部分唯一索引 `uq_position_authority_active` 收敛）。

    合法性由 `AuthorityGrant.__post_init__` 与唯一索引共同保证：
    金额只能落在金额类授权上、部门作用域必须给 scope_ref、非部门作用域必须为 0。
    """
    # 契约校验（构造一次即校验）：不合法直接抛 WorkContractError
    C.AuthorityGrant(
        kind=kind, scope_kind=scope_kind, scope_ref=int(scope_ref), max_amount=max_amount
    )
    existing = db.scalar(
        select(PositionAuthorityGrant).where(
            PositionAuthorityGrant.position_definition_id == int(position_definition_id),
            PositionAuthorityGrant.authority_kind == kind.value,
            PositionAuthorityGrant.scope_kind == scope_kind.value,
            PositionAuthorityGrant.scope_ref == int(scope_ref),
            PositionAuthorityGrant.effective_to.is_(None),
        )
    )
    if existing is not None:
        return existing
    row = PositionAuthorityGrant(
        position_definition_id=int(position_definition_id),
        authority_kind=kind.value,
        scope_kind=scope_kind.value,
        scope_ref=int(scope_ref),
        max_amount=max_amount,
        effective_from=_naive(utcnow()),
        granted_by_user_id=granted_by_user_id,
        note=note,
    )
    db.add(row)
    db.flush()
    if commit:
        db.commit()
    return row


def revoke_authority(
    db: Session,
    *,
    grant_id: int,
    reason: str = "",
    commit: bool = True,
) -> PositionAuthorityGrant | None:
    """收回一项授权（**关闭时间窗，不删行** —— append-only，W40）。

    返回被关闭的行；行不存在或已关闭时返回 `None`（幂等）。
    """
    row = db.get(PositionAuthorityGrant, int(grant_id))
    if row is None or row.effective_to is not None:
        return None
    row.effective_to = _naive(utcnow())
    if reason:
        row.note = f"{row.note} | revoked: {reason}".strip(" |")
    db.flush()
    if commit:
        db.commit()
    return row


def grant_count(db: Session, position_definition_id: int, *, at: datetime | None = None) -> int:
    """某职位在 `at` 时刻的生效授权条数（观测/诊断用）。"""
    moment = at or utcnow()
    return int(
        db.scalar(
            select(func.count())
            .select_from(PositionAuthorityGrant)
            .where(
                PositionAuthorityGrant.position_definition_id == int(position_definition_id),
                PositionAuthorityGrant.effective_from <= moment,
            )
        )
        or 0
    )
