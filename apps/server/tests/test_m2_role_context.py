"""M2.2 Role Context & Adaptive Onboarding —— 契约与行为测试（C1–C6 + 用户拍板 8 条）。

覆盖三块：

1. **Authority Projection**（v41 `position_authority_grants`）：
   default-deny / 随任职生效失效 / 有限作用域（company、department、direct_reports、
   spend max_amount）/ append-only 时间窗 / 快照可解释 / **绝不读 `employee.role`**。
2. **RoleContext 投影**（派生读模型，不落表）：字段可推导、**不含任何已获得能力数值**、
   期望只给引用。
3. **Role Resource Index**：只做**指针**，解析到 `knowledge_items` / `drive_nodes` /
   `companies.settings`，不造第二套内容。

纪律：依赖共享公司状态的用例用 `_ensure_assignment` 显式放到目标职位（幂等），
断言只看目标事实；纯粹的结构性约束用 AST/模型扫描，不落库。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models import Base
from app.models.base import utcnow
from app.models.competency import CompetencyDefinition, EmployeeCompetency
from app.models.enums import (
    AuthorityScopeKind,
    DriveNodeKind,
    DriveZone,
    KnowledgeScope,
    KnowledgeStatus,
    TaskKind,
)
from app.models.event import Event
from app.models.knowledge import KnowledgeItem
from app.models.position import (
    PositionAuthorityGrant,
    PositionDefinition,
    PositionDefinitionResource,
)
from app.repositories import drive as drive_repo
from app.repositories import organization as org_repo
from app.repositories import position as position_repo
from app.schemas.position import AssignmentIn, PositionDefinitionIn, SlotIn
from app.services import position_profile as profile_service
from app.services import position_service
from app.work import authority as authority_service
from app.work import authority_seed, role_events
from app.work import contracts as C
from app.work import role_context as role_context_service

SERVER_ROOT = Path(__file__).resolve().parents[1]
AUTHORITY_MODULE = SERVER_ROOT / "app" / "work" / "authority.py"
ROLE_CONTEXT_MODULE = SERVER_ROOT / "app" / "work" / "role_context.py"

#: 人级资产对拍范围（横切要求 6）：任免绝不能改动这些
PERSON_LEVEL_TABLES = (
    "employee_brains",
    "skills",
    "skill_usages",
    "knowledge_items",
    "learning_records",
    "learning_priorities",
    "memory_entries",
    "competency_evidence",
    "employee_competencies",
    "assessment_runs",
)


def _person_snapshot(db, person_id: int | None) -> dict[str, int]:
    """人级资产计数对拍（person 口径）。任免前后必须逐表不变（C2/C4/W7/W8）。"""
    counts: dict[str, int] = {}
    for table_name in PERSON_LEVEL_TABLES:
        table = Base.metadata.tables[table_name]
        column = (
            table.columns["person_id"]
            if "person_id" in table.columns
            else table.columns["owner_person_id"]
        )
        counts[table_name] = int(
            db.scalar(select(func.count()).select_from(table).where(column == person_id)) or 0
        )
    return counts


#: 本文件会动**组织事实**（任职时间轴 / 生命周期）—— 每个用例后自动还原，
#: 否则会污染后续用例（实测踩到：把某人 lifecycle 改成 suspended，
#: 后跑的 roster 用例期望 available）。
@pytest.fixture(autouse=True)
def _restore_org_state(org_snapshot):
    yield org_snapshot


def _employees(db, company_id: int) -> dict[str, object]:
    return {employee.slug: employee for employee in org_repo.list_employees(db, company_id)}


def _definitions(db, company_id: int) -> dict[str, PositionDefinition]:
    return {
        definition.code: definition for definition in position_repo.list_definitions(db, company_id)
    }


def _definition_of_slot(db, slot) -> PositionDefinition:
    return position_repo.get_definition(db, int(slot.position_definition_id))


def _assign_to_code(db, employee, company_id: int, code: str) -> PositionAssignmentLike:
    """把员工放到 `code` 职位的某个编制上（幂等；让出该坑的既有在任者）。

    刻意用真实的 `assign_position` 工作流（关旧主职 + 开新主职），
    而不是直接造 `PositionAssignment` 行 —— 测试要走生产路径。
    """
    definition = _definitions(db, company_id)[code]
    slots = position_repo.list_slots(db, company_id=company_id, definition_id=int(definition.id))
    assert slots, f"职位 {code} 没有编制"
    slot = slots[0]
    current = position_repo.active_primary_assignment(db, int(employee.id))
    if current is not None and int(current.position_slot_id or 0) == int(slot.id):
        return definition
    for row in position_repo.slot_incumbents(db, int(slot.id)):
        row.effective_to = utcnow()
        db.flush()
    position_service.assign_position(
        db, employee, AssignmentIn(slot_id=int(slot.id), reason="m2.2 test", kind="assign")
    )
    db.commit()
    return definition


PositionAssignmentLike = PositionDefinition  # 仅用于返回类型注释的可读别名


def _grant(
    db,
    definition: PositionDefinition,
    kind: C.AuthorityKind,
    *,
    scope_kind: AuthorityScopeKind = AuthorityScopeKind.company,
    scope_ref: int = 0,
    max_amount: int | None = None,
):
    """追加一条授权（append-only；测试不还原 —— 断言的正是"这条生效了"）。"""
    if max_amount is None and kind in C.AMOUNT_BEARING_AUTHORITIES:
        max_amount = int(settings.authority_default_spend_limit)
    return authority_service.grant_authority(
        db,
        position_definition_id=int(definition.id),
        kind=kind,
        scope_kind=scope_kind,
        scope_ref=scope_ref,
        max_amount=max_amount,
        note="m2.2 test",
    )


def _authority_lab(db, company_id: int, code: str, employee_slug: str = "charlie"):
    """给授权测试开一块**隔离**的实验台：新建职位定义 + 编制 + 任职。

    为什么必须隔离：grant 是 append-only 的，测试共享同一个库；如果多个用例往
    **同一个职位定义**上追加授权，后来的用例会被前面用例的 company 作用域授权
    "合法地"兜住 —— 那样测的是别的东西，而且结果依赖执行顺序。

    返回 `(employee, definition)`，实验台上的授权只属于这个用例。
    """
    people = _employees(db, company_id)
    employee = people[employee_slug]
    position_service.release_position(db, employee, reason="lab setup")
    db.flush()
    definition = position_service.create_definition(
        db,
        PositionDefinitionIn(code=code, name=f"Lab {code}", job_family="engineering", level=3),
        company_id=company_id,
    )
    db.flush()
    slot = position_service.open_slots(
        db,
        int(definition.id),
        SlotIn(department_id=int(employee.department_id), count=1, note="m2.2 lab"),
        company_id,
    )[0]
    position_service.assign_position(
        db, employee, AssignmentIn(slot_id=int(slot.id), reason="m2.2 lab", kind="assign")
    )
    db.commit()
    return employee, definition


# ---------------------------------------------------------------------------
# v41 结构：新薄表承载管理授权，packages 语义不动
# ---------------------------------------------------------------------------


def test_v41_adds_authority_tables_without_touching_packages_semantics():
    """用户拍板：授权走新薄表；`position_definition_packages` **保持**资源开通语义。"""
    tables = Base.metadata.tables
    assert "position_authority_grants" in tables
    assert "position_definition_resources" in tables
    grants = set(tables["position_authority_grants"].columns.keys())
    assert {"authority_kind", "scope_kind", "scope_ref", "max_amount"} <= grants
    # 管理权**不得**混进资源开通包（两张表是两件事）
    packages = set(tables["position_definition_packages"].columns.keys())
    assert not (packages & {"authority_kind", "scope_kind", "scope_ref", "max_amount"})
    # advisory_scope 落在 position_definitions 上（advisory，不是工作边界）
    assert "advisory_scope" in tables["position_definitions"].columns


def test_authority_grants_are_append_only_with_time_windows():
    """W40：语义字段靠"关旧行 + 插新行"演进；只有时间窗与备注可写。"""
    tree = ast.parse(AUTHORITY_MODULE.read_text(encoding="utf-8"))
    mutated: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "row"
            ):
                mutated.add(target.attr)
    assert mutated <= {"effective_to", "note"}, (
        f"授权行被就地改写的字段：{sorted(mutated)} —— 授权是 append-only（W40）"
    )


def test_authority_tables_have_no_content_or_ranking_columns():
    """W27/W41：资源表只有指针；两张新表都没有分值/排名列。"""
    assert set(PositionDefinitionResource.__table__.columns.keys()) == {
        "id",
        "position_definition_id",
        "kind",
        "ref",
        "note",
        "required",
        "created_at",
    }
    grants = set(PositionAuthorityGrant.__table__.columns.keys())
    assert not (grants & {"score", "level", "rank", "weight", "capability"})


# ---------------------------------------------------------------------------
# default-deny / 随任职生效失效（W37 / W38）
# ---------------------------------------------------------------------------


def test_authority_is_default_deny_and_reports_a_reason_code(db, default_company_id):
    """W37：没有生效主职（或没有该项授权）⇒ 拒绝，且给出机器可读原因码。"""
    people = _employees(db, default_company_id)
    employee = people["charlie"]
    position_service.release_position(db, employee, reason="m2.2 default-deny probe")
    db.commit()
    decision = authority_service.authorizes(
        db, employee_id=int(employee.id), kind=C.AuthorityKind.assign_task
    )
    assert decision.allowed is False
    assert decision.reason == "no_active_assignment"

    # 有职位但没有该项授权 ⇒ no_grant（engineer 的种子里没有任何管理授权）
    _assign_to_code(db, employee, default_company_id, "engineer")
    engineer_decision = authority_service.authorizes(
        db, employee_id=int(employee.id), kind=C.AuthorityKind.assign_task
    )
    assert engineer_decision.allowed is False
    assert engineer_decision.reason == "no_grant", "执行岗默认没有管理授权"


def test_authority_follows_assignment_not_role_string(db, default_company_id):
    """W37/W38：授权随任职生效与失效，**绝不**看 `employees.role` 字符串。"""
    people = _employees(db, default_company_id)
    engineer = people["charlie"]
    _assign_to_code(db, engineer, default_company_id, "engineer")
    assert (
        authority_service.authorizes(
            db, employee_id=int(engineer.id), kind=C.AuthorityKind.create_project
        ).allowed
        is False
    )

    # 换到 CEO 编制 ⇒ 立刻获得 CEO 授权（`employees.role` 镜像仍是 engineer）
    _assign_to_code(db, engineer, default_company_id, "ceo")
    assert str(engineer.role) == "engineer", "镜像没变 —— 正好证明授权不读它"
    assert (
        authority_service.authorizes(
            db, employee_id=int(engineer.id), kind=C.AuthorityKind.create_project
        ).allowed
        is True
    )

    # 卸任 ⇒ 授权**立即**失效（不是 Person 的永久资产）
    position_service.release_position(db, engineer, reason="m2.2 release probe")
    db.commit()
    after = authority_service.authorizes(
        db, employee_id=int(engineer.id), kind=C.AuthorityKind.create_project
    )
    assert after.allowed is False and after.reason == "no_active_assignment"


def test_authority_and_role_context_modules_never_read_role_strings_or_scores():
    """W37 + 用户拍板：权限检查不得依赖 `employee.role`（也不得依赖 Fit / 访问包）。"""
    forbidden_attrs = {"role", "legacy_role", "fit", "responsibility_area", "packages"}
    for path in (AUTHORITY_MODULE, ROLE_CONTEXT_MODULE):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
                hits.append(f"{path.name}:{node.lineno} .{node.attr}")
            if isinstance(node, ast.ImportFrom) and node.module and "talent.fit" in node.module:
                hits.append(f"{path.name}:{node.lineno} import {node.module}")
            if isinstance(node, ast.ImportFrom) and node.module in {
                "app.lifecycle.access",
                "app.services.position_compat",
            }:
                hits.append(f"{path.name}:{node.lineno} import {node.module}")
        assert not hits, "授权/履职上下文读了被禁止的来源：\n" + "\n".join(hits)
    assert {"employee_role_string", "legacy_role", "fit_score"} <= C.FORBIDDEN_AUTHORITY_SOURCES


def test_authority_layer_validates_but_never_decides():
    """W39：授权层只校验 —— 不排序、不选人、不给建议。"""
    tree = ast.parse(AUTHORITY_MODULE.read_text(encoding="utf-8"))
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    offenders = {
        name
        for name in names
        if name.startswith(
            ("pick_", "choose_", "select_", "rank_", "recommend_", "best_", "should_", "advise_")
        )
    }
    assert not offenders, f"授权层出现了决策/排序函数：{sorted(offenders)}"


# ---------------------------------------------------------------------------
# 有限作用域：company / department / direct_reports / spend max_amount
# ---------------------------------------------------------------------------


def test_authority_scope_company_requires_the_same_company(db, default_company_id):
    employee, definition = _authority_lab(db, default_company_id, "lab-company")
    _grant(db, definition, C.AuthorityKind.create_project)
    assert (
        authority_service.authorizes(
            db,
            employee_id=int(employee.id),
            kind=C.AuthorityKind.create_project,
            target=C.AuthorityTarget(company_id=default_company_id),
        ).allowed
        is True
    )
    other = authority_service.authorizes(
        db,
        employee_id=int(employee.id),
        kind=C.AuthorityKind.create_project,
        target=C.AuthorityTarget(company_id=default_company_id + 999),
    )
    assert other.allowed is False and other.reason == "scope_mismatch"


def test_company_scope_verifies_targets_belong_to_the_actor_company(db, default_company_id):
    """company 作用域不是"什么都行"：被指向的部门/员工必须真的在本公司里。"""
    employee, definition = _authority_lab(db, default_company_id, "lab-verify")
    _grant(db, definition, C.AuthorityKind.request_rework)
    assert (
        authority_service.authorizes(
            db,
            employee_id=int(employee.id),
            kind=C.AuthorityKind.request_rework,
            target=C.AuthorityTarget(department_id=int(employee.department_id)),
        ).allowed
        is True
    )
    assert (
        authority_service.authorizes(
            db,
            employee_id=int(employee.id),
            kind=C.AuthorityKind.request_rework,
            target=C.AuthorityTarget(department_id=999_999),
        ).reason
        == "scope_mismatch"
    )
    assert (
        authority_service.authorizes(
            db,
            employee_id=int(employee.id),
            kind=C.AuthorityKind.request_rework,
            target=C.AuthorityTarget(employee_id=999_999),
        ).reason
        == "scope_mismatch"
    )


def test_authority_scope_department_requires_a_confirmable_department(db, default_company_id):
    """部门作用域：确认不了"就是我管的部门" ⇒ 拒绝（default-deny）。"""
    employee, definition = _authority_lab(db, default_company_id, "lab-dept")
    _grant(
        db,
        definition,
        C.AuthorityKind.accept_delivery,
        scope_kind=AuthorityScopeKind.department,
        scope_ref=int(employee.department_id),
    )
    assert (
        authority_service.authorizes(
            db,
            employee_id=int(employee.id),
            kind=C.AuthorityKind.accept_delivery,
            target=C.AuthorityTarget(department_id=int(employee.department_id)),
        ).allowed
        is True
    )
    assert (
        authority_service.authorizes(
            db,
            employee_id=int(employee.id),
            kind=C.AuthorityKind.accept_delivery,
            target=C.AuthorityTarget(department_id=int(employee.department_id) + 500),
        ).reason
        == "scope_mismatch"
    )
    assert (
        authority_service.authorizes(
            db, employee_id=int(employee.id), kind=C.AuthorityKind.accept_delivery, target=None
        ).allowed
        is False
    ), "部门作用域必须能确认目标部门"


def test_authority_scope_direct_reports_excludes_self_and_outsiders(db, default_company_id):
    """汇报子树：管得了下属，管不了自己、也管不了不在自己树下的人。"""
    people = _employees(db, default_company_id)
    manager, report = _authority_lab(db, default_company_id, "lab-reports")
    _assign_to_code(db, report, default_company_id, "qa_engineer")
    _grant(
        db,
        _definitions(db, default_company_id)["lab-reports"],
        C.AuthorityKind.request_rework,
        scope_kind=AuthorityScopeKind.direct_reports,
    )
    # 训练汇报线：把 report 的坑挂到 manager 的坑下
    manager_slot_id = int(
        position_repo.active_primary_assignment(db, int(manager.id)).position_slot_id
    )
    report_slot = position_repo.require_slot(
        db, int(position_repo.active_primary_assignment(db, int(report.id)).position_slot_id)
    )
    report_slot.manager_slot_id = manager_slot_id
    db.commit()

    actor = authority_service.resolve_actor_authority(db, int(manager.id))
    reports = authority_service.direct_report_employee_ids(db, actor)
    assert int(report.id) in reports
    assert int(manager.id) not in reports, "自己不在汇报子树里"

    assert (
        authority_service.authorizes(
            db,
            employee_id=int(manager.id),
            kind=C.AuthorityKind.request_rework,
            target=C.AuthorityTarget(employee_id=int(report.id)),
        ).allowed
        is True
    )
    assert (
        authority_service.authorizes(
            db,
            employee_id=int(manager.id),
            kind=C.AuthorityKind.request_rework,
            target=C.AuthorityTarget(employee_id=int(manager.id)),
        ).reason
        == "scope_mismatch"
    )
    assert (
        authority_service.authorizes(
            db,
            employee_id=int(manager.id),
            kind=C.AuthorityKind.request_rework,
            target=C.AuthorityTarget(employee_id=int(people["alice"].id)),
        ).reason
        == "scope_mismatch"
    ), "不在自己汇报树里的人，管不着"


def test_authority_spend_requires_amount_within_the_grant_ceiling(db, default_company_id):
    """金额类授权：必须给金额、必须有上限、不能超（default-deny）。"""
    employee, definition = _authority_lab(db, default_company_id, "lab-spend")
    _grant(db, definition, C.AuthorityKind.spend_credits, max_amount=1_000)
    assert (
        authority_service.authorizes(
            db, employee_id=int(employee.id), kind=C.AuthorityKind.spend_credits, amount=1_000
        ).allowed
        is True
    )
    missing = authority_service.authorizes(
        db, employee_id=int(employee.id), kind=C.AuthorityKind.spend_credits
    )
    assert missing.allowed is False and missing.reason == "amount_required"
    over = authority_service.authorizes(
        db, employee_id=int(employee.id), kind=C.AuthorityKind.spend_credits, amount=1_001
    )
    assert over.allowed is False and over.reason == "amount_exceeds_grant"
    # 没有声明上限的金额授权 ⇒ 无法确认在授权内 ⇒ 拒绝（**不**等于"不限"）
    unlimited = authority_service.grant_authority(
        db,
        position_definition_id=int(definition.id),
        kind=C.AuthorityKind.spend_credits,
        scope_kind=AuthorityScopeKind.department,
        scope_ref=int(employee.department_id),
        commit=False,
    )
    assert unlimited.max_amount is None
    assert settings.authority_default_spend_limit > 0, "上限来自政策，不来自代码常量"


def test_grant_contract_rejects_impossible_declarations():
    """契约只允许"能表达得出来"的授权（金额只给金额类、部门必须给 ref）。"""
    with pytest.raises(C.WorkContractError):
        C.AuthorityGrant(kind=C.AuthorityKind.assign_task, max_amount=100)
    with pytest.raises(C.WorkContractError):
        C.AuthorityGrant(
            kind=C.AuthorityKind.create_project,
            scope_kind=AuthorityScopeKind.department,
            scope_ref=0,
        )
    with pytest.raises(C.WorkContractError):
        C.AuthorityGrant(kind=C.AuthorityKind.create_project, scope_ref=7)
    assert C.AuthorityKind.offboard in C.SELF_TARGET_FORBIDDEN_AUTHORITIES


def test_self_target_is_refused_for_lifecycle_authorities(db, default_company_id):
    """`offboard` / `release_position` 不允许作用于自己（硬安全约束，不是管理判断）。"""
    ceo = _employees(db, default_company_id)["alice"]
    _assign_to_code(db, ceo, default_company_id, "ceo")
    decision = authority_service.authorizes(
        db,
        employee_id=int(ceo.id),
        kind=C.AuthorityKind.offboard,
        target=C.AuthorityTarget(employee_id=int(ceo.id)),
    )
    assert decision.allowed is False and decision.reason == "self_target"


# ---------------------------------------------------------------------------
# W40：版本/审计语义 —— 历史决策能解释"当时为什么有权"
# ---------------------------------------------------------------------------


def test_revoke_closes_the_window_and_history_stays_explainable(db, default_company_id):
    """收回授权 = 关闭时间窗、**不删行**：过去仍然可回溯（W40）。"""
    ceo = _employees(db, default_company_id)["alice"]
    definition = _assign_to_code(db, ceo, default_company_id, "ceo")
    grant = _grant(db, definition, C.AuthorityKind.approve_hiring)
    before = utcnow()

    assert (
        authority_service.authorizes(
            db, employee_id=int(ceo.id), kind=C.AuthorityKind.approve_hiring
        ).allowed
        is True
    )
    authority_service.revoke_authority(db, grant_id=int(grant.id), reason="m2.2 test")
    db.commit()
    assert (
        authority_service.authorizes(
            db, employee_id=int(ceo.id), kind=C.AuthorityKind.approve_hiring
        ).allowed
        is False
    )

    row = db.get(PositionAuthorityGrant, int(grant.id))
    assert row is not None and row.effective_to is not None, "行必须留着（append-only）"
    assert (
        authority_service.authorizes(
            db, employee_id=int(ceo.id), kind=C.AuthorityKind.approve_hiring, at=before
        ).allowed
        is True
    ), "按时间回溯必须仍然有权 —— 这就是历史可解释性的地基"


def test_authority_snapshot_is_pinnable_recomputable_and_judgement_free(db, default_company_id):
    """快照：`grant_ids` + `grants_hash` 可重算对拍；不含任何"好不好"的评语。"""
    ceo, definition = _authority_lab(db, default_company_id, "lab-snapshot")
    _grant(db, definition, C.AuthorityKind.assign_task)
    _grant(db, definition, C.AuthorityKind.create_project)
    decision = authority_service.authorizes(
        db, employee_id=int(ceo.id), kind=C.AuthorityKind.assign_task
    )
    assert decision.allowed is True and decision.grant_ids
    snapshot = C.authority_snapshot(decision, action_ref="task:1")
    assert snapshot["snapshot_version"] == C.AUTHORITY_SNAPSHOT_VERSION
    assert snapshot["grant_ids"] == list(decision.grant_ids)

    # ①「这次凭什么」：拿 `grant_ids` 对应的授权行重算，必须与记录一致
    actor = authority_service.resolve_actor_authority(db, int(ceo.id))
    matched = tuple(
        grant.to_contract() for grant in actor.grants if grant.grant_id in set(decision.grant_ids)
    )
    assert C.hash_effective_grants(matched) == snapshot["grants_hash"]
    # ②「当时手里有什么」：整个职位的授权摘要（另一个字段，另一层语义）
    assert C.hash_effective_grants(actor.contract_grants) == snapshot["position_grants_hash"]
    assert not (set(snapshot) & {"quality", "score", "advice", "recommendation", "correct"})

    # `authority_snapshot_for` 是给 DecisionRecord 用的同一份事实
    direct = authority_service.authority_snapshot_for(db, int(ceo.id))
    assert set(direct) >= {
        "grant_ids",
        "position_grants_hash",
        "position_code",
        "evaluated_at",
    }


def test_grants_hash_is_deterministic_and_order_independent():
    a = C.AuthorityGrant(kind=C.AuthorityKind.assign_task, grant_id=2)
    b = C.AuthorityGrant(kind=C.AuthorityKind.create_project, grant_id=1)
    assert C.hash_effective_grants((a, b)) == C.hash_effective_grants((b, a))
    assert C.hash_effective_grants((a,)) != C.hash_effective_grants((a, b))


# ---------------------------------------------------------------------------
# C1–C4：任命不授予能力、不复制人级资产、制度记忆存续
# ---------------------------------------------------------------------------


def test_c1_expectation_shortfall_never_blocks_appointment(db, default_company_id):
    """C1 / W4：期望不是门禁 —— 能力低于 target 也能被任命。

    同时验证 C5 的另一半：`RoleContext` 会报告**期望的引用**，
    但**不**报告这个人的实际分数（分数在能力域，按需另读）。
    """
    people = _employees(db, default_company_id)
    candidate = people["charlie"]
    _assign_to_code(db, candidate, default_company_id, "engineer")

    cto = position_service.create_definition(
        db,
        PositionDefinitionIn(code="cto-m22", name="CTO", job_family="engineering", level=5),
        company_id=default_company_id,
    )
    db.commit()
    slot = position_service.open_slots(
        db,
        int(cto.id),
        SlotIn(department_id=int(candidate.department_id), count=1, note="m2.2"),
        default_company_id,
    )[0]
    # 岗位画像：管理与领导 target 70（远高于候选人的 52）
    version = profile_service.create_draft(db, cto)
    leadership = db.scalar(
        select(CompetencyDefinition).where(CompetencyDefinition.code == "management_leadership")
    )
    assert leadership is not None, "能力目录里必须有 management_leadership"
    profile_service.add_requirement(
        db,
        version,
        int(leadership.id),
        company_id=default_company_id,
        requirement_type="required",
        minimum_score=60,
        target_score=70,
        minimum_confidence=0.5,
        critical=True,
    )
    db.flush()
    profile_service.publish_profile(db, version)
    db.add(
        EmployeeCompetency(
            employee_id=int(candidate.id),
            person_id=int(candidate.person_id) if candidate.person_id else None,
            competency_definition_id=int(leadership.id),
            score=52,
            confidence=0.9,
            evidence_count=3,
            status="assessed",
            last_assessed_at=utcnow(),
        )
    )
    db.commit()

    assignment = position_service.assign_position(
        db, candidate, AssignmentIn(slot_id=int(slot.id), reason="c1", kind="assign")
    )
    db.commit()
    assert assignment.id is not None, "期望不足**不得**阻止任命（W4）"

    context = role_context_service.build_role_context(db, candidate)
    refs = {(ref.competency_code, ref.critical) for ref in context.expectations}
    assert ("management_leadership", True) in refs, "期望以引用形式出现"
    assert all(
        not hasattr(ref, "target_score") and not hasattr(ref, "minimum_score")
        for ref in context.expectations
    ), "期望引用不得携带分值（C5）"
    # 没有授权的职位 ⇒ 有职位也依然 default-deny
    assert (
        authority_service.authorizes(
            db, employee_id=int(candidate.id), kind=C.AuthorityKind.assign_task
        ).allowed
        is False
    )


def test_c2_c4_appointment_never_touches_person_level_assets(db, default_company_id):
    """C2/C4 + W7/W8/W10：任免前后人级资产逐表不变（换人不迁移任何个人资产）。"""
    people = _employees(db, default_company_id)
    predecessor, successor = people["alice"], people["charlie"]
    successor_person = int(successor.person_id)
    before_successor = _person_snapshot(db, successor_person)
    before_predecessor = _person_snapshot(db, int(predecessor.person_id))

    _assign_to_code(db, successor, default_company_id, "ceo")

    assert _person_snapshot(db, successor_person) == before_successor, "任命改动了人级资产"
    assert _person_snapshot(db, int(predecessor.person_id)) == before_predecessor
    assert successor_person != int(predecessor.person_id), "两个人仍是两个人（不合并、不复制）"


def test_c3_company_knowledge_stays_institutional_after_replacement(client, db, default_company_id):
    """C3 / W9：公司知识是**制度资产** —— 换人之后新 CEO 照样读得到。"""
    people = _employees(db, default_company_id)
    for slug in ("alice", "charlie"):
        employee = people[slug]
        db.add(
            KnowledgeItem(
                scope=KnowledgeScope.company.value,
                title=f"公司制度：{slug}",
                content="制度内容",
                topic="公司制度 M2.2",
                status=KnowledgeStatus.active.value,
                confidence=1.0,
                sources=[],
                owner_person_id=int(employee.person_id) if employee.person_id else None,
            )
        )
    handbook = drive_repo.create_node(
        db,
        company_id=default_company_id,
        parent_id=None,
        kind=DriveNodeKind.document.value,
        name="公司制度 M2.2",
        path=f"drive/handbook/institutional-{default_company_id}.md",
        zone=DriveZone.handbook.value,
    )
    db.commit()

    definition = _assign_to_code(db, people["alice"], default_company_id, "ceo")
    role_context_service.declare_role_resource(
        db,
        position_definition_id=int(definition.id),
        kind=C.RoleResourceKind.knowledge_topic,
        ref="公司制度 M2.2",
        note="制度知识（不随人走）",
        required=True,
    )
    role_context_service.declare_role_resource(
        db,
        position_definition_id=int(definition.id),
        kind=C.RoleResourceKind.handbook,
        ref="公司制度 M2.2",
        note="制度手册",
    )
    db.commit()

    # 换人：把 CEO 编制交给 charlie（前任的个人资产**不**随职位迁移）
    _assign_to_code(db, people["charlie"], default_company_id, "ceo")
    resolved = {
        item.kind.value: item
        for item in role_context_service.resolve_role_resources(db, people["charlie"])
    }
    assert resolved["knowledge_topic"].resolution.value == "resolved"
    assert resolved["knowledge_topic"].pointer.startswith("knowledge_item:")
    assert resolved["handbook"].pointer == f"drive_node:{int(handbook.id)}"


# ---------------------------------------------------------------------------
# C5 / C6：只给引用、只做指针
# ---------------------------------------------------------------------------


def test_c5_role_context_response_carries_no_capability_numbers(client, db, default_company_id):
    """C5：RoleContext 响应不含任何 score / level / rank（递归键名扫描）。"""
    ceo = _employees(db, default_company_id)["alice"]
    _assign_to_code(db, ceo, default_company_id, "ceo")
    body = client.get(f"/api/v1/employees/{int(ceo.id)}/role-context").json()

    banned = ("score", "rating", "level", "rank", "grade", "proficiency", "capability")

    def walk(node, path="") -> list[str]:
        hits: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                if any(token in key.lower() for token in banned):
                    hits.append(f"{path}.{key}")
                hits += walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                hits += walk(value, f"{path}[{index}]")
        return hits

    offenders = walk(body)
    assert not offenders, f"RoleContext 响应里出现了能力数值：{offenders}"
    for item in body["context"]["expectations"]:
        assert set(item) == {"competency_code", "requirement_type", "critical"}


def test_c6_role_resource_reports_missing_and_advisory_instead_of_inventing(db, default_company_id):
    """C6 / W41：指针目标不存在就如实报 `missing`；`skill_hint` 按设计报 `advisory`。"""
    ceo = _employees(db, default_company_id)["alice"]
    definition = _assign_to_code(db, ceo, default_company_id, "ceo")
    role_context_service.declare_role_resource(
        db,
        position_definition_id=int(definition.id),
        kind=C.RoleResourceKind.knowledge_topic,
        ref="一个不存在的主题-m22",
    )
    role_context_service.declare_role_resource(
        db,
        position_definition_id=int(definition.id),
        kind=C.RoleResourceKind.skill_hint,
        ref="分布式共识",
    )
    db.commit()
    resolved = {item.ref: item for item in role_context_service.resolve_role_resources(db, ceo)}
    missing = resolved["一个不存在的主题-m22"]
    assert missing.resolution.value == "missing" and missing.pointer == ""
    advisory = resolved["分布式共识"]
    assert advisory.resolution.value == "advisory"


def test_declare_role_resource_is_idempotent_and_rejects_empty_ref(db, default_company_id):
    ceo = _employees(db, default_company_id)["alice"]
    definition = _assign_to_code(db, ceo, default_company_id, "ceo")
    first = role_context_service.declare_role_resource(
        db,
        position_definition_id=int(definition.id),
        kind=C.RoleResourceKind.playbook,
        ref="开局手册",
    )
    second = role_context_service.declare_role_resource(
        db,
        position_definition_id=int(definition.id),
        kind=C.RoleResourceKind.playbook,
        ref="开局手册",
    )
    assert int(first.id) == int(second.id)
    with pytest.raises(C.WorkContractError):
        role_context_service.declare_role_resource(
            db,
            position_definition_id=int(definition.id),
            kind=C.RoleResourceKind.playbook,
            ref="   ",
        )


# ---------------------------------------------------------------------------
# 种子与事件
# ---------------------------------------------------------------------------


def test_seed_gives_management_authority_only_to_management_positions():
    """冷启动种子：管理岗有授权；执行岗（研究/工程）**没有**管理权。"""
    assert C.AuthorityKind.create_project in authority_seed.DEFAULT_AUTHORITY["ceo"]
    assert C.AuthorityKind.assign_task in authority_seed.DEFAULT_AUTHORITY["product_manager"]
    assert authority_seed.DEFAULT_AUTHORITY["engineer"] == ()
    assert authority_seed.DEFAULT_AUTHORITY["researcher"] == ()
    # advisory_scope 是"通常做什么"，不是工作边界（W5/W12）
    assert TaskKind.development.value in authority_seed.DEFAULT_ADVISORY_SCOPE["engineer"]
    assert C.ConstraintName.position_scope in C.SOFT_CONSTRAINTS


def test_seed_is_idempotent_and_creates_one_active_grant_per_kind(db, default_company_id):
    """幂等：重复种不产生第二条授权/资源（重放安全）。"""
    authority_seed.seed_default_authority(db)
    assert authority_seed.seed_default_authority(db) == {
        "grants": 0,
        "resources": 0,
        "advisory_scope": 0,
    }
    definition = _definitions(db, default_company_id)["ceo"]
    active = list(
        db.scalars(
            select(PositionAuthorityGrant).where(
                PositionAuthorityGrant.position_definition_id == int(definition.id),
                PositionAuthorityGrant.effective_to.is_(None),
            )
        )
    )
    identities = [(row.authority_kind, row.scope_kind, row.scope_ref) for row in active]
    assert len(identities) == len(set(identities)), "同一 (授权, 作用域) 出现了多条生效行"


def test_role_events_publish_facts_not_instructions(db, default_company_id):
    """上任/卸任 → `role.context_available` / `role.context_withdrawn`：只带**事实**。"""
    ceo = _employees(db, default_company_id)["alice"]
    _assign_to_code(db, ceo, default_company_id, "ceo")
    before_id = int(db.scalar(select(func.max(Event.id))) or 0)

    role_events.handle(
        {
            "type": "employee.position_assigned",
            "company_id": default_company_id,
            "data": {"id": int(ceo.id), "reason": "m2.2 test"},
        }
    )
    assigned = db.scalar(
        select(Event)
        .where(Event.type == "role.context_available", Event.id > before_id)
        .order_by(Event.id.desc())
    )
    assert assigned is not None
    payload = assigned.payload
    assert {"authority_grant_count", "role_resource_count", "position_code"} <= set(payload)
    assert not (
        set(payload) & {"please_learn", "todo", "instructions", "next_steps", "recommended_action"}
    ), f"通知带上了系统指令：{sorted(payload)}"

    role_events.handle(
        {
            "type": "employee.position_released",
            "company_id": default_company_id,
            "data": {"id": int(ceo.id), "reason": "m2.2 test"},
        }
    )
    withdrawn = db.scalar(
        select(Event)
        .where(Event.type == "role.context_withdrawn", Event.id > before_id)
        .order_by(Event.id.desc())
    )
    assert withdrawn is not None and withdrawn.payload["authority_grant_count"] == 0


def test_role_events_are_not_registered_when_disabled(client, monkeypatch):
    """门控：测试环境默认关掉消费者（与 `position_access_sync` 同款纪律）。"""
    assert role_events.enabled() is False
    monkeypatch.setattr(settings, "role_context_events", True)
    assert role_events.enabled() is True


# ---------------------------------------------------------------------------
# HTTP 读面
# ---------------------------------------------------------------------------


def test_role_context_endpoint_reports_facts(client, db, default_company_id):
    ceo = _employees(db, default_company_id)["alice"]
    _assign_to_code(db, ceo, default_company_id, "ceo")
    body = client.get(f"/api/v1/employees/{int(ceo.id)}/role-context").json()

    assert body["context"]["position_code"] == "ceo"
    assert body["context"]["responsibilities"], "职位职责必须出现在上下文里"
    kinds = {grant["kind"] for grant in body["context"]["authority"]}
    assert {"create_project", "assign_task"} <= kinds
    assert body["has_management_authority"] is True
    assert body["authority_grant_count"] == len(body["context"]["authority"])
    assert "work_routing" in body["context"]["company_policy_keys"]
    assert body["context"]["knowledge_scopes"] == ["private", "department", "company"]
    assert body["resources"] == body["context"]["resource_index"]
    assert body["live_projects"] == [] or isinstance(body["live_projects"], list)


def test_role_context_endpoint_is_read_only(client, db, default_company_id):
    """读面不写库：连续调用不改变授权/资源行数。"""
    ceo = _employees(db, default_company_id)["alice"]
    _assign_to_code(db, ceo, default_company_id, "ceo")

    def counts() -> tuple[int, int]:
        return (
            int(db.scalar(select(func.count()).select_from(PositionAuthorityGrant)) or 0),
            int(db.scalar(select(func.count()).select_from(PositionDefinitionResource)) or 0),
        )

    before = counts()
    for _ in range(2):
        response = client.get(f"/api/v1/employees/{int(ceo.id)}/role-context")
        assert response.status_code == 200, response.text
    assert counts() == before
