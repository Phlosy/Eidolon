"""RecruitmentService（T2.6）—— 把既有 Person 变成某公司的 Employee。

这是 T2 最关键的一次领域事务（设计 §3.2/§5、plan §4.7）。**不复制任何人级资产**：

```
BEGIN
  校验 listing（active；未知 → 404，已关闭 → 409）
  eligibility.can_recruit（在市场 + 无生效主职）
  目标公司 / 部门 / 编制校验（跨公司一律 404，不泄露存在性）
  CAS 关闭 listing（条件 UPDATE rowcount —— 并发只能有一个赢家）
  创建 Employee(person_id = 既有 Person.id)
  回填 listing.recruited_company_id / recruited_employee_id
  （可选）position_service.assign_position(commit=False)（同一事务）
  career_events(joined) + audit
COMMIT
  发布 person.recruited（+ 分配过职位时补发 employee.position_assigned）
```

刻意的边界：
- **不新建 Person**（I1/I3）；`identity_id`、traits、knowledge、evidence、assessment、
  education_events 全部留在原地（I2/I4）；
- **不改写历史 provenance**（I5：`assessment_runs.company_id` 等公司快照不动）；
- **不做 runtime/provider 全链开通**（沿用既有员工 runtime 流程）—— 招募只建人与任职；
- 重复/并发招募被拒：listing 条件关闭（rowcount=0 → 409）+ `uq_employees_person_id`
  （一人一 employee）+ 任一部分唯一索引兜底（I8）。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.events.bus import bus
from app.lifecycle import audit
from app.lifecycle.naming import naming
from app.models.cultivation import CharacterProfile
from app.models.enums import (
    EmployeeRole,
    EmployeeStatus,
    LifecycleStatus,
    MarketListingStatus,
    RuntimeType,
)
from app.models.organization import Department
from app.models.position import PositionSlot
from app.repositories import cultivation as cultivation_repo
from app.repositories import market as market_repo
from app.repositories import organization as org_repo
from app.repositories import persons as person_repo
from app.schemas.position import AssignmentIn
from app.services import career as career_service
from app.services import position_service
from app.talent.market import eligibility


@dataclass(frozen=True)
class RecruitmentResult:
    person_id: int
    employee_id: int
    employee_slug: str
    identity_id: str | None
    company_id: int
    listing_id: int
    position_slot_id: int | None
    assignment_id: int | None


class RecruitmentError(RuntimeError):
    """招募领域错误：`reason` 是机器可读 code，API 层转 HTTP。"""

    def __init__(self, reason: str, http_status: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


def _unique_employee_slug(db: Session, base: str) -> str:
    """`employees.slug` 是**全局唯一**列（连带 workspace_path / memory_namespace）。

    人的 slug 已经是全局唯一的（persons.slug），但 employees 是另一张表 ——
    仍要判一次并加后缀，否则会在 INSERT 上撞成 500（不是 400）。
    """
    slug = naming.username(base) or "talent"
    if not org_repo.slug_taken_anywhere(db, slug):
        return slug
    index = 2
    while org_repo.slug_taken_anywhere(db, f"{slug}-{index}"):
        index += 1
    return f"{slug}-{index}"


class RecruitmentService:
    """把在市场（listed）的既有 Person 招募为本公司员工。"""

    def recruit_existing_person(
        self,
        db: Session,
        *,
        listing_id: int,
        company_id: int,
        department_id: int | None = None,
        position_slot_id: int | None = None,
        title: str | None = None,
        role: str | None = None,
        reason: str = "",
        actor_user_id: int | None = None,
    ) -> RecruitmentResult:
        # ---- 1. listing：未知 404 / 非 active 409（陈旧页面语义，plan §9）----
        listing = market_repo.get_listing(db, listing_id)
        if listing is None:
            raise RecruitmentError("listing_not_found", http_status=404)
        if listing.status != MarketListingStatus.active.value:
            raise RecruitmentError("listing_not_active")

        person_id = int(listing.person_id)
        person = person_repo.get_person(db, person_id)
        if person is None:  # 挂牌行指向的 person 缺失 = 数据损坏
            raise RecruitmentError("person_not_found", http_status=404)

        # ---- 2. eligibility（唯一判定处）：在市 + 无生效主职 ----
        decision = eligibility.can_recruit(db, person_id)
        if not decision.allowed:
            raise RecruitmentError(
                "already_employed" if decision.reason.value == "employed" else "not_recruitable"
            )

        # ---- 3. 目标公司 / 部门 / 编制（跨公司一律 404）----
        company = org_repo.get_company(db, company_id)
        if company is None:
            raise RecruitmentError("company_not_found", http_status=404)

        department: Department | None = None
        if department_id is not None:
            department = db.get(Department, department_id)
            if department is None or int(department.company_id) != int(company_id):
                raise RecruitmentError("department_not_found", http_status=404)

        slot: PositionSlot | None = None
        if position_slot_id is not None:
            slot = db.get(PositionSlot, position_slot_id)
            if slot is None or int(slot.company_id) != int(company_id):
                raise RecruitmentError("position_slot_not_found", http_status=404)

        # ---- 4. CAS 抢占 listing（并发只能一个赢家；失败即回滚，不留半个员工）----
        player_participant = market_repo.ensure_participant(
            db,
            kind="player_company",
            company_id=int(company_id),
            display_name=company.name,
        )
        claimed = market_repo.close_active_listing(
            db,
            listing_id,
            reason="recruited",
            recruited_company_id=int(company_id),
            recruited_participant_id=int(player_participant.id),
        )
        if not claimed:
            db.rollback()
            raise RecruitmentError("listing_not_active")

        try:
            # ---- 5. Employee(person_id = 既有 Person.id)：不新建 Person ----
            slug = _unique_employee_slug(db, person.slug)
            slot_definition = None
            if slot is not None:
                from app.repositories import position as position_repo

                slot_definition = position_repo.get_definition(db, int(slot.position_definition_id))
            resolved_title = (
                title
                or (slot_definition.name if slot_definition is not None else "")
                or person.name
            )
            resolved_role = role or (
                slot_definition.legacy_role
                if slot_definition is not None and slot_definition.legacy_role
                else EmployeeRole.engineer.value
            )
            resolved_department_id = (
                department.id if department is not None else (slot.department_id if slot else None)
            )
            employee = org_repo.create_employee(
                db,
                person_id=int(person.id),
                company_id=int(company_id),
                department_id=resolved_department_id,
                name=person.name,
                slug=slug,
                role=resolved_role,
                title=resolved_title,
                avatar=person.avatar or "",
                status=EmployeeStatus.idle.value,
                # 人已到位；运行时/工作区开通沿用既有员工流程（本阶段不做全链开通）
                lifecycle_status=LifecycleStatus.active.value,
                username=naming.username(slug),
                runtime_type=RuntimeType.mock.value,
                runtime_config={},
                workspace_path=f"{settings.workspace_root}/{slug}",
                memory_namespace=f"emp_{slug}",
            )
            # listing 自身记录 "被谁招走"（同一事务；行已由 CAS 归本事务）
            listing.recruited_employee_id = int(employee.id)

            # ---- 6.（可选）任职：同一事务，事件由本服务在 commit 后补发 ----
            assignment = None
            if slot is not None:
                assignment = position_service.assign_position(
                    db,
                    employee,
                    AssignmentIn(
                        slot_id=int(slot.id),
                        reason=reason or "market_recruitment",
                        assigned_by=actor_user_id,
                    ),
                    commit=False,
                )

            # ---- 7. 履历 + 审计 ----
            career_service.record_event(
                db,
                employee,
                "joined",
                position_definition_id=(
                    int(slot_definition.id) if slot_definition is not None else None
                ),
                slot_id=int(slot.id) if slot is not None else None,
                actor_user_id=actor_user_id,
                reason=reason,
                source_id=int(listing.id),
                metadata={
                    "source": "market_recruitment",
                    "listing_id": int(listing.id),
                    "identity_id": _identity_id(db, person_id),
                },
            )
            audit.record(
                db,
                action="person.recruited",
                employee_id=int(employee.id),
                after={
                    "person_id": person_id,
                    "listing_id": int(listing.id),
                    "company_id": int(company_id),
                    "position_slot_id": int(slot.id) if slot is not None else None,
                },
                reason=reason,
                actor="user",
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

        # ---- 8. 事件（只在事务成功提交后发）----
        company_id_int = int(company_id)
        identity_id = _identity_id(db, person_id)
        bus.publish(
            "person.recruited",
            {
                "person_id": person_id,
                "employee_id": int(employee.id),
                "company_id": company_id_int,
                "listing_id": int(listing.id),
                "identity_id": identity_id,
                "position_slot_id": int(slot.id) if slot is not None else None,
            },
            company_id=company_id_int,
            actor_employee_id=int(employee.id),
        )
        if assignment is not None:
            bus.publish(
                "employee.position_assigned",
                {
                    "id": int(employee.id),
                    "slot_id": int(slot.id),
                    "position_code": (slot_definition.code if slot_definition is not None else ""),
                    "department_id": int(slot.department_id),
                    "kind": "assign",
                },
                company_id=company_id_int,
                actor_employee_id=int(employee.id),
            )
        return RecruitmentResult(
            person_id=person_id,
            employee_id=int(employee.id),
            employee_slug=slug,
            identity_id=identity_id,
            company_id=company_id_int,
            listing_id=int(listing.id),
            position_slot_id=int(slot.id) if slot is not None else None,
            assignment_id=int(assignment.id) if assignment is not None else None,
        )


def _identity_id(db: Session, person_id: int) -> str | None:
    profile: CharacterProfile | None = cultivation_repo.get_profile_by_person(db, person_id)
    return profile.identity_id if profile is not None else None
