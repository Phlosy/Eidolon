"""M2.9 · **WorkOrder → Project 绑定边**（W23 / WO1–WO6）。

一句话：**连接商业需求与执行载体，不把 WorkOrder 变成执行图**（W23）。

```text
WorkOrder ACCEPTED（M1 的经济事实，状态机不动）
        ↓ 事件 work_order.accepted
桥：投递给公司的 Work Intake 责任人（记 `routed`，系统只投递）
        ↓ 管理层决定
      bind_project（绑上某个 Project）／ decline_binding（明确不接，理由必填）
```

本文件的纪律：**绑定是管理动作**（系统不选执行载体、不建项目）、
**引用受校验**（W19）、**钱一分不动**（M1 的验收/结算路径原样）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.events.bus import bus
from app.models.drive import DriveNode
from app.models.economy import WorkOrderProjectLink
from app.models.enums import DriveNodeKind, DriveZone, EvaluationMode
from app.models.organization import Company, Employee
from app.models.project import Project
from app.repositories import economy as economy_repo
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.services.economy.work_orders import WorkOrderError, WorkOrderService
from app.work import contracts as C
from app.work import work_order_bridge as bridge

SERVER_ROOT = Path(__file__).resolve().parents[1]
APP = SERVER_ROOT / "app"
BRIDGE = APP / "work" / "work_order_bridge.py"
BRIDGE_EVENTS = APP / "work" / "work_order_events.py"
ECONOMY_SERVICE = APP / "services" / "economy" / "work_orders.py"

_seq = 0


@pytest.fixture(autouse=True)
def _restore_org_state(org_snapshot):
    yield org_snapshot


def _company(db, name: str) -> Company:
    global _seq
    _seq += 1
    company = Company(name=name, slug=f"m29-{name.lower()}-{_seq}", description="")
    db.add(company)
    db.commit()
    return company


def _project(db, company: Company, name: str = "Delivery", **extra) -> Project:
    project = Project(
        company_id=int(company.id),
        name=name,
        description="",
        goal="",
        status="in_progress",
        source_order_text=name,
        **extra,
    )
    db.add(project)
    db.commit()
    return project


def _artifact(db, company: Company, title: str = "deliverable") -> DriveNode:
    """建一个**真实**产物（节点 + 修订；引用要能追到内容哈希，W19）。"""
    import hashlib

    node = DriveNode(
        company_id=int(company.id),
        kind=DriveNodeKind.document.value,
        name=title,
        path=f"drive/m29/{company.id}-{title}.md",
        zone=DriveZone.projects.value,
        current_version=1,
    )
    db.add(node)
    db.flush()
    from app.models.drive import DriveRevision

    db.add(
        DriveRevision(
            node_id=int(node.id),
            version=1,
            sha256=hashlib.sha256(title.encode("utf-8")).hexdigest(),
            message="m2.9 test artifact",
        )
    )
    db.commit()
    return node


def _actor(db, company: Company, slug: str = "boss") -> Employee:
    existing = [
        row for row in org_repo.list_employees(db, int(company.id)) if str(row.slug) == slug
    ]
    if existing:
        return existing[0]
    employee = org_repo.create_employee(
        db,
        person_id=None,
        company_id=int(company.id),
        department_id=None,
        name=f"Actor {slug}",
        slug=f"{slug}-{company.id}",
        role="product_manager",
        title="Manager",
        status="idle",
        lifecycle_status="active",
        runtime_type="mock",
        runtime_config={},
        workspace_path=f"/tmp/m29-{company.id}-{slug}",
        memory_namespace=f"m29_{company.id}_{slug}",
    )
    db.commit()
    return employee


def _accepted_order(db, company: Company, *, mode: EvaluationMode = EvaluationMode.manual):
    service = WorkOrderService(db)
    order = service.publish_official(
        title="Bridge order", reward_amount=1_000, evaluation_mode=mode
    ).order
    service.accept(order.id, company_id=int(company.id))
    return service, economy_repo.get_work_order(db, int(order.id))


# ---------------------------------------------------------------------------
# WO1 / J5：状态机不动（只加一条边）
# ---------------------------------------------------------------------------


def test_work_order_state_machine_is_untouched():
    """WO1（= J5）：桥不新增状态、不改合法迁移；冻结快照与代码一致。"""
    from app.models.enums import WorkOrderStatus

    assert C.WORK_ORDER_STATES_FROZEN == tuple(
        item.value for item in sorted(WorkOrderStatus, key=lambda item: item.value)
    ), "WorkOrder 状态集变了 —— 桥只允许加边，不允许加状态（WO1/J5）"

    # 桥模块里不许出现状态迁移调用（它只写绑定边）
    names = {
        node.attr
        for node in ast.walk(ast.parse(BRIDGE.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute)
    } | {
        node.id
        for node in ast.walk(ast.parse(BRIDGE.read_text(encoding="utf-8")))
        if isinstance(node, ast.Name)
    }
    assert "assert_transition" not in names, "桥碰了状态机 —— 它只该写绑定边（WO1）"
    assert "transition_work_order" not in names
    # 订单状态只被**读**（比较），没有被赋值
    source = BRIDGE.read_text(encoding="utf-8")
    assert "order.status =" not in source


# ---------------------------------------------------------------------------
# WO2 / J1：接受之后要么被绑定、要么被显式拒绝（两条路径都有记录）
# ---------------------------------------------------------------------------


def test_accepted_order_is_either_bound_or_declined(db):
    """WO2（= J1）：投递是系统事实；绑定/拒绝是管理决定，两条路径都留痕。"""
    company = _company(db, "BoundCo")
    actor = _actor(db, company)
    service, order = _accepted_order(db, company)

    # ① 系统投递（幂等）
    routed = bridge.route_accepted_order(db, order)
    assert routed is not None and routed["action"] == bridge.ACTION_ROUTED
    assert routed["actor_employee_id"] is None, "投递不是决定：系统不署名（WO5）"
    assert bridge.route_accepted_order(db, order) is None, "重复投递不该写第二条"

    # ② 管理决定：绑定
    project = _project(db, company)
    bridge.bind_project(
        db, order, project_id=int(project.id), actor_employee_id=int(actor.id), reason="这单我们做"
    )
    db.expire_all()
    refreshed = economy_repo.get_work_order(db, int(order.id))
    assert int(refreshed.project_id) == int(project.id), "指针要指到绑定的项目"

    view = bridge.binding_view(db, refreshed)
    assert view["routed"]["action"] == "routed"
    assert view["bound"]["project_id"] == int(project.id)
    assert view["bound"]["actor_employee_id"] == int(actor.id)
    assert view["declined"] is None
    assert [edge["action"] for edge in view["edges"]] == ["routed", "bound"]

    # ③ 拒绝路径：另一份订单，理由必填
    _, other = _accepted_order(db, company)
    with pytest.raises(bridge.WorkOrderBridgeError) as excinfo:
        bridge.decline_binding(db, other, actor_employee_id=int(actor.id), reason="   ")
    assert excinfo.value.http_status == 422 and "reason" in str(excinfo.value)
    db.rollback()
    declined = bridge.decline_binding(
        db, other, actor_employee_id=int(actor.id), reason="人手不够，不接"
    )
    assert declined["action"] == "declined"
    assert declined["reason"] == "人手不够，不接"
    db.expire_all()
    assert (
        bridge.binding_view(db, economy_repo.get_work_order(db, int(other.id)))["declined"]
        is not None
    )
    # 拒绝之后不许再绑（决定是终局，先撤销再谈）
    project2 = _project(db, company, "Second")
    with pytest.raises(bridge.WorkOrderBridgeError) as excinfo:
        bridge.bind_project(
            db,
            other,
            project_id=int(project2.id),
            actor_employee_id=int(actor.id),
            reason="还是做吧",
        )
    assert excinfo.value.http_status == 409
    db.rollback()

    # ④ 事件：投递/绑定/拒绝各自只发一次事实通报
    published: list[str] = []
    original = bus.publish
    bus.publish = lambda event, payload, **kw: (
        published.append(event),
        original(event, payload, **kw),
    )[1]  # type: ignore[assignment]
    try:
        _, third = _accepted_order(db, company)
        bridge.route_accepted_order(db, third)
    finally:
        bus.publish = original  # type: ignore[assignment]
    assert "work_order.intake_routed" in published


# ---------------------------------------------------------------------------
# WO3 / J2 / J3：project_id 是受校验引用
# ---------------------------------------------------------------------------


def test_project_reference_is_validated(client, db, default_company_id):
    """WO3（= J2/J3）：不存在的项目 422；跨公司的项目 404（隔离不破）。"""
    company = _company(db, "RefCo")
    other_company = _company(db, "OtherCo")
    actor = _actor(db, company)
    _, order = _accepted_order(db, company)

    # ① 不存在 ⇒ 422
    with pytest.raises(bridge.WorkOrderBridgeError) as excinfo:
        bridge.bind_project(
            db, order, project_id=999_999, actor_employee_id=int(actor.id), reason="x"
        )
    assert excinfo.value.http_status == 422 and excinfo.value.reason == "project_not_found"
    db.rollback()

    # ② 别家公司的项目 ⇒ **404**（不泄露它存在）
    foreign = _project(db, other_company, "Foreign")
    with pytest.raises(bridge.WorkOrderBridgeError) as excinfo:
        bridge.bind_project(
            db, order, project_id=int(foreign.id), actor_employee_id=int(actor.id), reason="x"
        )
    assert excinfo.value.http_status == 404
    db.rollback()

    # ③ HTTP 面同款（人工管理动作）—— 用**当前登录公司**的订单（别家的订单是 404，隔离在起作用）
    home = org_repo.get_company(db, int(default_company_id))
    home_actor = _actor(db, home, "boss")
    _, home_order = _accepted_order(db, home)
    response = client.post(
        f"/api/v1/work-orders/{home_order.id}/binding",
        json={"project_id": 999_999, "actor_employee_id": int(home_actor.id)},
    )
    assert response.status_code == 422, response.text
    response = client.post(
        f"/api/v1/work-orders/{home_order.id}/binding",
        json={"project_id": int(foreign.id), "actor_employee_id": int(home_actor.id)},
    )
    assert response.status_code == 404, response.text

    # ④ 提交时的 project_id 同样受校验（J2/J3）
    service = WorkOrderService(db)
    with pytest.raises(WorkOrderError) as excinfo:
        service.submit(order.id, company_id=int(company.id), summary="x", project_id=999_999)
    assert excinfo.value.http_status == 422
    db.rollback()
    with pytest.raises(WorkOrderError) as excinfo:
        service.submit(
            order.id, company_id=int(company.id), summary="x", project_id=int(foreign.id)
        )
    assert excinfo.value.http_status == 404
    db.rollback()

    # ⑤ 本公司真实项目 ⇒ 通过，并且指针指向它
    mine = _project(db, company, "Mine")
    service.submit(order.id, company_id=int(company.id), summary="x", project_id=int(mine.id))
    db.expire_all()
    assert int(economy_repo.get_work_order(db, int(order.id)).project_id) == int(mine.id)


# ---------------------------------------------------------------------------
# WO4 / J2 / W19：交付物引用必须指向本公司的真实产物
# ---------------------------------------------------------------------------


def test_submitted_artifact_references_are_real(client, db):
    """WO4（= J2/W19）：引用要么是可解析的真实产物，要么被拒绝（自由字符串不是引用）。"""
    company = _company(db, "ArtifactCo")
    other_company = _company(db, "ForeignArtifactCo")
    service, order = _accepted_order(db, company)

    # ① 自由字符串 / 形式非法 ⇒ 422
    with pytest.raises(WorkOrderError) as excinfo:
        service.submit(order.id, company_id=int(company.id), summary="x", artifact_refs=["drive:1"])
    assert excinfo.value.http_status == 422 and "not found" in excinfo.value.reason
    db.rollback()
    with pytest.raises(WorkOrderError) as excinfo:
        service.submit(
            order.id, company_id=int(company.id), summary="x", artifact_refs=["external:https://x"]
        )
    assert excinfo.value.http_status == 422 and "must be an id" in excinfo.value.reason
    db.rollback()

    # ② 别家公司的产物 ⇒ 404（不泄露它存在）
    foreign_node = _artifact(db, other_company, "foreign")
    with pytest.raises(WorkOrderError) as excinfo:
        service.submit(
            order.id,
            company_id=int(company.id),
            summary="x",
            artifact_refs=[f"drive:{foreign_node.id}"],
        )
    assert excinfo.value.http_status == 404
    db.rollback()

    # ③ 本公司真实产物 ⇒ 通过（两种写法都认：int 与 "drive:<id>"）
    node = _artifact(db, company, "real")
    _, settled, _ = service.submit(
        order.id,
        company_id=int(company.id),
        summary="done",
        artifact_refs=[int(node.id), f"drive:{node.id}"],
    )
    assert int(settled.id) == int(order.id)
    submissions = service.submissions(int(order.id))
    assert submissions and submissions[0].artifact_refs == [
        int(node.id),
        f"drive:{node.id}",
    ]

    # ④ 引用解析出来的**事实**可核对（名字/类型/sha256）
    from app.services import artifacts as artifact_service

    facts = artifact_service.resolve_artifact_refs(
        db, company_id=int(company.id), refs=[f"drive:{node.id}"]
    )
    assert facts[0]["artifact_id"] == int(node.id)
    assert facts[0]["name"] == "real"
    assert facts[0]["sha256"], "引用必须能追到内容哈希（W19）"


# ---------------------------------------------------------------------------
# WO5：桥不创建、不规划项目
# ---------------------------------------------------------------------------


def test_bridge_never_creates_a_project(db):
    """WO5（W2/W34）：投递只告诉责任人；建项目是管理层的动作。"""
    company = _company(db, "NoCreateCo")
    _, order = _accepted_order(db, company)
    before = len(project_repo.list_projects(db))

    bridge.route_accepted_order(db, order)

    assert len(project_repo.list_projects(db)) == before, "桥自己建了项目 —— 越权（WO5）"
    source = BRIDGE.read_text(encoding="utf-8")
    for forbidden in ("create_project", "ProjectCreate", "planning_fixture", "build_deterministic"):
        assert forbidden not in source, f"桥里出现了建图/建项目路径：{forbidden}（WO5）"
    # 路由结果里记的是"该谁看"，不是"谁来干"
    routed = bridge.binding_view(db, economy_repo.get_work_order(db, int(order.id)))["routed"]
    assert "routed_employee_id" in routed["metadata"]


# ---------------------------------------------------------------------------
# WO6：不碰验收 / 结算 / 账本
# ---------------------------------------------------------------------------


def test_bridge_does_not_touch_evaluation_or_ledger(db):
    """WO6：桥只写绑定边；验收与结算仍走 M1 既有路径（E 系列不变量不动）。"""
    source = BRIDGE.read_text(encoding="utf-8")
    for forbidden in (
        "EvaluationService",
        "SettlementService",
        "LedgerService",
        "EscrowService",
        "ledger",
        "settle(",
        "evaluate(",
    ):
        assert forbidden not in source, f"桥里出现了钱/验收路径：{forbidden}（WO6）"

    from app.models.economy import LedgerTransaction

    company = _company(db, "NoMoneyCo")
    actor = _actor(db, company)
    service, order = _accepted_order(db, company)
    project = _project(db, company)

    # 账本行数**前后对比**（不能断言"全表为空"：别的用例也会写账本 —— 那是他们的事）
    ledger_before = len(list(db.scalars(select(LedgerTransaction))))
    bridge.route_accepted_order(db, order)
    bridge.bind_project(
        db, order, project_id=int(project.id), actor_employee_id=int(actor.id), reason="bind"
    )
    ledger_after = len(list(db.scalars(select(LedgerTransaction))))

    # 绑定之后，验收仍由 M1 服务产生，且绑定没有替它做任何事
    assert service.evaluations(int(order.id)) == []
    assert ledger_after == ledger_before, "桥写了账本 —— WO6 被破坏"


# ---------------------------------------------------------------------------
# 事件消费者（生产路径的接线）
# ---------------------------------------------------------------------------


def test_bridge_consumer_is_wired_and_gated(db):
    """`work_order.accepted` → 桥的消费者已注册入口，且受开关控制（测试默认关）。"""
    from app.core.config import settings

    source = BRIDGE_EVENTS.read_text(encoding="utf-8")
    assert 'CONSUMED_EVENTS = ("work_order.accepted",)' in source
    assert "settings.work_order_bridge_consumers_enabled" in source
    assert "route_accepted_order" in source
    assert settings.work_order_bridge_consumers_enabled is False, (
        "测试环境必须默认关（后台消费者会与手动驱动的路径抢同一份状态）"
    )

    # 处理器本身可被直接调用（确定性验证）：投递一条 routed 事实
    from app.work.work_order_events import handle_event

    company = _company(db, "ConsumerCo")
    service, order = _accepted_order(db, company)
    handle_event({"data": {"work_order_id": int(order.id)}, "company_id": int(company.id)})
    with SessionLocal() as other:
        rows = list(
            other.scalars(
                select(WorkOrderProjectLink).where(
                    WorkOrderProjectLink.work_order_id == int(order.id)
                )
            )
        )
    assert [row.action for row in rows] == ["routed"]


# ---------------------------------------------------------------------------
# 反例注入：把违规实现喂给同一条守卫，守卫必须转红
# ---------------------------------------------------------------------------


def _guard_states_unchanged(states: tuple[str, ...]) -> None:
    assert states == C.WORK_ORDER_STATES_FROZEN, "订单状态集被改了 —— 桥只许加边（WO1/J5）"


def _guard_bridge_does_not_create(source: str) -> None:
    assert "create_project" not in source, "桥自己建项目 —— 越权（WO5）"


def _guard_bridge_does_not_touch_money(source: str) -> None:
    for forbidden in ("LedgerService", "SettlementService", "EscrowService", "settle("):
        assert forbidden not in source, f"桥碰了钱：{forbidden}（WO6）"


def test_guards_catch_the_counter_examples():
    """反例注入：每条守卫在收到违规输入时都必须转红。"""
    _guard_states_unchanged(C.WORK_ORDER_STATES_FROZEN)
    with pytest.raises(AssertionError, match="WO1/J5"):
        _guard_states_unchanged(C.WORK_ORDER_STATES_FROZEN + ("PARKED",))

    clean = "def route(order):\n    return None\n"
    _guard_bridge_does_not_create(clean)
    with pytest.raises(AssertionError, match="WO5"):
        _guard_bridge_does_not_create("def route(order):\n    create_project(order)\n")

    _guard_bridge_does_not_touch_money(clean)
    with pytest.raises(AssertionError, match="WO6"):
        _guard_bridge_does_not_touch_money(
            "def bind(order):\n    SettlementService().settle(order)\n"
        )


def test_guard_catches_unvalidated_project_binding():
    """反例注入：绑定不校验就写指针 ⇒ 守卫转红。"""

    def _guard(validated: bool) -> None:
        assert validated, "绑定前没有校验项目引用（WO3）"

    _guard(True)
    with pytest.raises(AssertionError, match="WO3"):
        _guard(False)


def test_guard_catches_artifact_ref_bypass():
    """反例注入：提交路径把引用原样存下（不解析）⇒ 守卫转红。"""

    def _guard(resolved: bool) -> None:
        assert resolved, "交付物引用没有被解析/校验（WO4/W19）"

    _guard(True)
    with pytest.raises(AssertionError, match="WO4"):
        _guard(False)

    # 真源：经济服务的提交路径确实调用了解析器
    economy_source = ECONOMY_SERVICE.read_text(encoding="utf-8")
    assert "resolve_artifact_refs" in economy_source
    assert "ArtifactRefError" in economy_source
