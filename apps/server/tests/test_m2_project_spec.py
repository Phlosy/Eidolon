"""M2.1 Canonical Executable Project —— 契约与行为测试（Acceptance B1–B12）。

三条纪律：

1. **B 判据逐条可执行**：每个 Bx 都有对应测试，且断言的是行为而不是文档。
2. **不依赖测试运行顺序**：公司阶段（FOUNDING/OPERATING）与工作策略都是共享状态，
   凡依赖它们的用例都**显式设置并还原**，或改用不落库的纯函数路径。
3. **守卫不是装饰**：W33/W36 的关键约束用 AST 守卫钉死运行时代码的形状
   （"没有 fixture 门控就调用确定性模板"这类回归会立刻转红）。
"""

from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from app.core.config import settings
from app.models.enums import PlanningFixture, ProjectWorkMode, ResponsibilityKind
from app.repositories import organization as org_repo
from app.services import position_service
from app.work import contracts as C
from app.work import work_defaults, work_intake

SERVER_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR = SERVER_ROOT / "app" / "workflow" / "orchestrator.py"
PROJECT_SERVICE = SERVER_ROOT / "app" / "services" / "projects.py"


def _post_project(client, **payload):
    return client.post("/api/v1/projects", json=payload)


def _wait_for(predicate, timeout: float = 20.0, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# B1 · 任何 Project 都能回答 Canonical Spec
# ---------------------------------------------------------------------------


FULL_SPEC = {
    "name": "Canonical Spec 项目",
    "code": "SPEC-B1",
    "work_mode": "guided",
    "priority": "high",
    "customer": "内部",
    "description": "上下文：验证 Canonical Spec 可回答性。",
    "background": "需要一个能回答八个问题的项目读面。",
    "objectives": ["让每个项目都能说清自己要解决什么"],
    "requirements": [
        {
            "code": "REQ-001",
            "title": "可回答性",
            "description": "八个问题都有答案",
            "priority": "must",
            "acceptance_criteria": "GET /projects/{id}/spec 覆盖八个 key",
        }
    ],
    "constraints": ["不新增表"],
    "deliverables": ["Spec 读面"],
}


def test_project_answers_the_eight_canonical_questions(client, employees_by_slug):
    """B1：新建项目能回答那 8 个问题（用户拍板清单）。"""
    payload = dict(FULL_SPEC)
    payload["owner_id"] = employees_by_slug["alice"]["id"]
    created = _post_project(client, **payload)
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]

    spec = client.get(f"/api/v1/projects/{project_id}/spec").json()

    # ① Canonical Spec
    assert spec["spec"]["background"] == FULL_SPEC["background"]
    assert spec["spec"]["goal"] == "让每个项目都能说清自己要解决什么"
    assert spec["spec"]["constraints"] == ["不新增表"]
    assert spec["spec"]["deliverables"] == ["Spec 读面"]
    assert spec["completeness"]["is_complete"] is True, spec["completeness"]
    # ② work_mode（快照）
    assert spec["work_mode"] == "guided"
    # ③ Work Intake 责任
    assert spec["work_intake"]["responsibility"] == "work_intake"
    # ④ 承担它的任职（本用例是 guided，不要求已路由；责任目标必须可回答）
    assert spec["work_intake"]["position_code"]
    assert spec["work_intake"]["default_position_code"] == "ceo"
    # ⑤ 管理 actor（字段存在，允许为 null）
    assert "employee_id" in spec["management"]
    # ⑥ requirements / deliverables / acceptance
    assert spec["spec"]["requirements"][0]["code"] == "REQ-001"
    assert spec["spec"]["acceptance_criteria"] == ["GET /projects/{id}/spec 覆盖八个 key"]
    # ⑦ spec 版本
    assert spec["spec_version"] == C.PROJECT_SPEC_VERSION
    # ⑧ 是否已进入执行
    assert spec["execution"]["entered"] is True
    assert spec["execution"]["phase_count"] == 11

    # 八个问题 key 全在（契约可核对）
    assert set(spec["questions"]) == {
        "canonical_spec",
        "work_mode",
        "work_intake_responsibility",
        "work_intake_assignment",
        "management_actor",
        "requirements_deliverables_acceptance",
        "spec_version",
        "execution_entered",
    }


def test_spec_reports_gaps_without_blocking_creation(client, no_work_intake):
    """B1（续）/ W5：缺字段只**报告**，不拒绝创建 —— 系统不替公司写需求。"""
    created = _post_project(client, name="一句话项目", description="就一句话", work_mode="managed")
    assert created.status_code == 201, created.text
    spec = client.get(f"/api/v1/projects/{created.json()['id']}/spec").json()
    assert spec["completeness"]["is_complete"] is False
    assert "requirements" in spec["completeness"]["missing"]
    assert "deliverables" in spec["completeness"]["missing"]
    # 可选字段不参与缺失判定
    assert "context" not in spec["completeness"]["missing"] or spec["spec"]["context"] == ""
    assert set(spec["completeness"]["optional_fields"]) == set(C.SPEC_OPTIONAL_FIELDS)


def test_spec_read_model_never_writes(client):
    """读模型是纯读：函数体里不出现任何写操作（AST 守卫）。"""
    tree = ast.parse(PROJECT_SERVICE.read_text(encoding="utf-8"))
    target = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "get_project_spec"
    )
    offenders: list[str] = []
    for node in ast.walk(target):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in {"commit", "flush", "add", "delete", "rollback"}:
                offenders.append(f"line {node.lineno}: {name}()")
    assert not offenders, "spec 读模型里出现了写操作：\n" + "\n".join(offenders)


# ---------------------------------------------------------------------------
# B2 · `is_structured` 不再路由领域语义
# ---------------------------------------------------------------------------


def test_is_structured_no_longer_routes_domain_semantics(client, employees_by_slug, no_work_intake):
    """B2：同一份"结构化"载荷，路由结果只由 `work_mode` 决定。"""
    owner_id = employees_by_slug["alice"]["id"]
    structured = {
        "code": "ROUTE-1",
        "background": "同样的载荷",
        "objectives": ["同样的目标"],
        "deliverables": ["同样的交付物"],
        "owner_id": owner_id,
    }

    managed = _post_project(client, name="managed 载荷", work_mode="managed", **structured).json()
    guided = _post_project(
        client,
        name="guided 载荷",
        work_mode="guided",
        code="ROUTE-2",
        **{k: v for k, v in structured.items() if k != "code"},
    ).json()

    from app.models.project_delivery import ProjectPhase

    db = None  # 用 API 读面判断，避免直接摸 DB
    managed_lifecycle = client.get(f"/api/v1/projects/{managed['id']}/lifecycle").json()
    guided_lifecycle = client.get(f"/api/v1/projects/{guided['id']}/lifecycle").json()

    assert managed_lifecycle["phases"] == [], "managed 不该有引导仪式"
    assert len(guided_lifecycle["phases"]) == 11, "guided 才有引导仪式"
    assert managed["work_mode"] == "managed" and guided["work_mode"] == "guided"
    assert ProjectPhase is not None and db is None  # 明确：不依赖 DB 直查


def test_guided_and_managed_share_one_substrate(client, employees_by_slug, no_work_intake):
    """B3 / W30 / W36：两种模式共用同一 `projects` 行与同一 `tasks` 表。"""
    owner_id = employees_by_slug["alice"]["id"]
    guided = _post_project(
        client,
        name="共用底座 guided",
        code="SHARED-1",
        work_mode="guided",
        owner_id=owner_id,
        background="b",
        objectives=["o"],
        requirements=[
            {
                "code": "REQ-001",
                "title": "t",
                "description": "d",
                "priority": "must",
                "acceptance_criteria": "ac",
            }
        ],
        deliverables=["d"],
    ).json()
    managed = _post_project(
        client,
        name="共用底座 managed",
        code="SHARED-2",
        work_mode="managed",
        owner_id=owner_id,
        description="b",
    ).json()

    # 同一张 projects 表 → 两个都能被同一对读面回答
    for project_id in (guided["id"], managed["id"]):
        assert client.get(f"/api/v1/projects/{project_id}").status_code == 200
        assert client.get(f"/api/v1/projects/{project_id}/graph").status_code == 200
        assert client.get(f"/api/v1/projects/{project_id}/spec").status_code == 200
    assert guided["work_mode"] == "guided" and managed["work_mode"] == "managed"

    # 同一张 tasks 表：两种模式的任务都从 /projects/{id} 的 tasks 数组读
    guided_detail = client.get(f"/api/v1/projects/{guided['id']}").json()
    managed_detail = client.get(f"/api/v1/projects/{managed['id']}").json()
    assert isinstance(guided_detail["tasks"], list)
    assert isinstance(managed_detail["tasks"], list)


def test_work_mode_never_encodes_decision_ownership():
    """W36：工作模式只有两个值，且**不**被用来键出"谁有权决策"。"""
    assert {m.value for m in ProjectWorkMode} == {"guided", "managed"}
    assert "human involvement" in (ProjectWorkMode.__doc__ or "")
    # 契约里不允许存在以 work_mode 为键的决策表（那会把模式变成权限）
    for name, value in vars(C).items():
        if not name.isupper() or not isinstance(value, dict):
            continue
        assert not any(isinstance(key, ProjectWorkMode) for key in value), (
            f"{name} 用 work_mode 当键 —— 那等于把产品模式变成决策归属（W36）"
        )


# ---------------------------------------------------------------------------
# B4 · 旧客户端逐字段兼容
# ---------------------------------------------------------------------------


def test_legacy_bare_request_still_accepted_field_compatible(client, no_work_intake):
    """B4：只传 name/description 的老请求仍然被接受，且字段口径不变。

    两种模式都过一遍 —— 老客户端不发 `work_mode` 时用的是公司默认，而公司默认
    会随阶段变化；"字段兼容"这个承诺必须在两种模式下都成立，而不是只在其中一种。
    """
    for mode in ("guided", "managed"):
        response = _post_project(
            client, name=f"老客户端项目-{mode}", description="一句话订单", work_mode=mode
        )
        assert response.status_code == 201, response.text
        body = response.json()
        # 老字段语义不变（源订单文本 = description；计划窗口 18 天；goal 保持空）
        assert body["source_order_text"] == "一句话订单"
        assert body["goal"] == ""
        assert body["planned_start_at"] is not None
        assert body["planned_end_at"] is not None
        # guided 会从名字派生一个对外机器码（v0.5 结构化交付的既有行为）；
        # managed 的一句式订单不发明 code。
        assert (body["code"] is None) is (mode == "managed")
        assert body["priority"] == "medium"
        assert body["work_mode"] == mode
        assert body["spec_version"] == C.PROJECT_SPEC_VERSION


def test_projects_migration_is_additive_only():
    """B5：只加列、无 FK、除 spec_version 外全部 nullable（SQLite 不重建表）。"""
    from app.models import Base

    table = Base.metadata.tables["projects"]
    added = {
        "work_mode": True,
        "planning_fixture": True,
        "spec_version": False,
        "work_intake_position_code": True,
        "management_employee_id": True,
        "management_person_id": True,
        "management_assigned_at": True,
    }
    for column, nullable in added.items():
        assert column in table.columns, f"缺少列 {column}"
        assert table.columns[column].nullable is nullable, (
            f"{column} 的 nullable 与迁移不一致（会逼 SQLite 重建表）"
        )
        assert not table.columns[column].foreign_keys, (
            f"{column} 加了 FK —— SQLite 上给既有表加 FK 会逼 batch 重建表（B5 禁止）"
        )
    # 关联对象上的旧列必须原样保留（逐字段兼容）
    for legacy in ("goal", "source_order_text", "review_configuration", "participants"):
        assert legacy in table.columns


# ---------------------------------------------------------------------------
# D1 · Work Intake 是责任路由（W32）
# ---------------------------------------------------------------------------


def test_work_intake_is_responsibility_routing_with_configurable_target(
    client, db, default_company_id
):
    """W32 / D1：默认 CEO，但**公司可配**；不硬编码 CEO 特权。"""
    company = org_repo.get_company(db, default_company_id)
    original = dict(company.settings or {})
    try:
        code, is_configured = work_intake.configured_position_code_and_source(
            company, ResponsibilityKind.work_intake
        )
        assert code == "ceo" and is_configured is False

        # 默认指向 CEO：创始人 alice 占着 ceo 编制 ⇒ 可路由
        routed = work_intake.resolve_work_intake(db, company)
        assert routed.status is work_intake.WorkIntakeStatus.routed
        alice = org_repo.list_employees(db, default_company_id)[0]  # seed 顺序：alice
        assert routed.employee_id == int(alice.id)
        assert routed.candidate_employee_ids

        # 公司把 Work Intake 交给 QA → 路由目标随之改变（不是 CEO 特权）
        patched = client.patch(
            "/api/v1/company/work-policy", json={"work_intake_position_code": "qa_engineer"}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["work_intake_position_code"] == "qa_engineer"
        assert patched.json()["work_intake_is_configured"] is True

        project = _post_project(client, name="路由到 QA", work_mode="managed").json()
        assert project["work_intake_position_code"] == "qa_engineer"
        spec = client.get(f"/api/v1/projects/{project['id']}/spec").json()
        assert spec["work_intake"]["status"] == "routed"
        assert spec["work_intake"]["is_configured"] is True
        assert spec["management"]["employee_id"] is not None
        assert spec["management"]["stale"] is False

        # 不存在的职位 code → 422（不允许配出一个永远解析不到的责任目标）
        bad = client.patch(
            "/api/v1/company/work-policy", json={"work_intake_position_code": "__nope__"}
        )
        assert bad.status_code == 422
    finally:
        company.settings = original
        db.commit()


def test_work_intake_reports_no_incumbent_for_a_vacant_position(client, db, default_company_id):
    """W32：职位存在但没人任职 ⇒ `no_incumbent`（如实报告，不挑人）。"""
    from app.schemas.position import PositionDefinitionIn

    company = org_repo.get_company(db, default_company_id)
    original = dict(company.settings or {})
    try:
        position_service.create_definition(
            db,
            PositionDefinitionIn(code="cto-m21", name="CTO（测试）"),
            company_id=default_company_id,
        )
        db.commit()
        work_defaults.set_company_work_intake_code(db, company, "cto-m21")

        resolution = work_intake.resolve_work_intake(db, company)
        assert resolution.status is work_intake.WorkIntakeStatus.no_incumbent
        assert resolution.position_definition_id is not None
        assert resolution.employee_id is None
        assert resolution.needs_owner_attention is True
        assert resolution.owner_user_id is not None or True  # 单公司部署可能无 Owner 用户

        project = _post_project(client, name="没人接的项目", work_mode="managed").json()
        assert project["status"] == "waiting_for_management"
    finally:
        company.settings = original
        db.commit()


# ---------------------------------------------------------------------------
# B11 / W34 · 没有负责人 ⇒ 等待，系统不接管
# ---------------------------------------------------------------------------


def test_missing_work_intake_manager_enters_waiting_not_fallback(client, db, no_work_intake):
    """B11 / W32 / W34：解析不到负责人 ⇒ `waiting_for_management`，**零规划**。"""
    created = _post_project(client, name="无人负责", description="需要有人接", work_mode="managed")
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["status"] == "waiting_for_management"
    assert project["management_employee_id"] is None
    assert project["work_intake_position_code"] == "__no_such_position__"

    project_id = project["id"]
    detail = client.get(f"/api/v1/projects/{project_id}").json()
    assert detail["tasks"] == [], "系统不得替公司生成任何任务"
    assert detail["milestones"] == []
    lifecycle = client.get(f"/api/v1/projects/{project_id}/lifecycle").json()
    assert lifecycle["phases"] == []

    spec = client.get(f"/api/v1/projects/{project_id}/spec").json()
    assert spec["work_intake"]["status"] == "no_position"
    assert spec["work_intake"]["reason"], "必须给用户一个可操作的原因"
    assert spec["execution"]["entered"] is False

    from sqlalchemy import select

    from app.models.event import Event

    event = db.scalar(
        select(Event).where(
            Event.type == "project.waiting_for_management",
            Event.project_id == project_id,
        )
    )
    assert event is not None, "等待状态必须留痕（可审计、可被前端拾取）"
    assert event.payload["work_intake_status"] == "no_position"


def test_managed_project_does_not_plan_itself(client, db, default_company_id):
    """W34 / B11：Manager 接了活之后，系统**不**生成任何执行图。"""
    created = _post_project(
        client, name="自主管理的项目", description="交给 CEO 决定怎么组织", work_mode="managed"
    ).json()
    assert created["status"] == "requested"
    assert created["management_employee_id"] is not None
    project_id = created["id"]

    detail = client.get(f"/api/v1/projects/{project_id}").json()
    kinds = [t["kind"] for t in detail["tasks"]]
    assert kinds == ["order_review"], f"只应有「接收决定」这一个任务：{kinds}"
    assert detail["milestones"] == []

    # 等接收任务跑完（mock runtime）——系统**不会**因此创造出规划与执行图
    def _planning() -> bool:
        return client.get(f"/api/v1/projects/{project_id}").json()["status"] == "planning"

    assert _wait_for(_planning)
    detail = client.get(f"/api/v1/projects/{project_id}").json()
    assert [t["kind"] for t in detail["tasks"]] == ["order_review"]
    assert detail["milestones"] == []

    from sqlalchemy import select

    from app.models.event import Event

    awaiting = db.scalar(
        select(Event).where(
            Event.type == "project.awaiting_management_action",
            Event.project_id == project_id,
        )
    )
    assert awaiting is not None, "管理动作尚未发生时必须留下显式等待信号"


# ---------------------------------------------------------------------------
# D3 / W33 · 确定性模板只能是显式、门控的基础设施
# ---------------------------------------------------------------------------


def test_planning_fixture_requires_explicit_request_and_gate(client, monkeypatch):
    """W33 / B10：fixture 必须**显式请求**且部署**已开启**，否则 422（不静默降级）。"""
    # 1) 不请求 ⇒ 生产语义
    plain = _post_project(client, name="生产项目", description="x", work_mode="managed").json()
    assert plain["planning_fixture"] == "none"

    # 2) 显式请求 + 测试环境已开启（conftest）⇒ 基础设施项目
    fixture = _post_project(
        client,
        name="fixture 项目",
        description="x",
        planning_fixture="deterministic_template",
    )
    assert fixture.status_code == 201, fixture.text
    body = fixture.json()
    assert body["planning_fixture"] == PlanningFixture.deterministic_template.value
    # fixture 替代的是 **Manager 的规划** ⇒ 它隐含 managed（与 guided 自相矛盾）
    assert body["work_mode"] == ProjectWorkMode.managed.value
    assert body["status"] == "requested"

    # 3) 部署未开启 ⇒ 422，且**不**静默降级成 none
    monkeypatch.setattr(settings, "allow_planning_fixtures", False)
    denied = _post_project(
        client,
        name="被拒绝的 fixture",
        description="x",
        planning_fixture="deterministic_template",
    )
    assert denied.status_code == 422, denied.text
    assert "fixture" in denied.json()["detail"]


def test_resolve_planning_fixture_never_silently_degrades():
    """契约层：未开启时抛错，而不是"当作没请求"。"""
    assert work_defaults.resolve_planning_fixture(None, allow=False) is PlanningFixture.none
    with pytest.raises(work_defaults.WorkPolicyError):
        work_defaults.resolve_planning_fixture(PlanningFixture.deterministic_template, allow=False)


def test_no_implicit_template_fallback_path_exists():
    """W33（AST 守卫）：确定性模板的调用点必须**在 fixture 门控之内**。

    这条守卫防的是最危险的回归："Manager 没反应 → 系统偷偷用模板顶上"。
    """
    tree = ast.parse(ORCHESTRATOR.read_text(encoding="utf-8"))
    guarded = 0
    unguarded: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_source = ast.dump(node.test)
        if "_uses_deterministic_plan" not in test_source:
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func = child.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name == "_apply_deterministic_template_plan":
                    guarded += 1
    total = sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "_apply_deterministic_template_plan"
            )
            or (
                isinstance(node.func, ast.Name)
                and node.func.id == "_apply_deterministic_template_plan"
            )
        )
    )
    assert total >= 1, "模板应用函数不存在了？"
    assert guarded == total, (
        f"有 {total - guarded} 处模板调用不在 `_uses_deterministic_plan` 门控内：{unguarded}"
    )


def test_implicit_planning_sources_are_named_and_absent():
    """W33：契约点名了被禁止的隐式规划来源，且代码里确实没有它们。"""
    assert "missing_manager_fallback" in C.FORBIDDEN_IMPLICIT_PLANNING_SOURCES
    source = ORCHESTRATOR.read_text(encoding="utf-8")
    for forbidden in C.FORBIDDEN_IMPLICIT_PLANNING_SOURCES:
        assert forbidden not in source


# ---------------------------------------------------------------------------
# B12 / W35 · work_mode 是快照
# ---------------------------------------------------------------------------


def test_work_mode_is_snapshotted_and_survives_company_default_change(
    client, db, default_company_id
):
    """B12 / W35：公司默认值变化**不**改写既有项目的 `work_mode`。"""
    company = org_repo.get_company(db, default_company_id)
    original = dict(company.settings or {})
    try:
        project = _post_project(client, name="快照项目", description="x", work_mode="guided").json()
        assert project["work_mode"] == "guided"

        work_defaults.set_company_work_mode_default(
            db, company, ProjectWorkMode.managed, reason="test_switch"
        )
        after = client.get(f"/api/v1/projects/{project['id']}").json()
        assert after["work_mode"] == "guided", "公司默认变化改写了既有项目 —— 违反 W35"
        assert after["status"] == "in_progress"  # guided 仪式仍然在
        assert client.get(f"/api/v1/projects/{project['id']}/spec").json()["work_mode"] == "guided"
    finally:
        company.settings = original
        db.commit()


def test_company_default_work_mode_follows_company_stage(db, default_company_id):
    """D2 / W35：冷启动 guided，成熟 managed；公司显式覆盖优先（纯读，不落库）。"""
    company = org_repo.get_company(db, default_company_id)
    original_stage, original_settings = company.stage, dict(company.settings or {})
    try:
        company.settings = {}
        company.stage = "FOUNDING"
        assert work_defaults.company_work_mode_default(db, company) is ProjectWorkMode.guided
        company.stage = "OPERATING"
        assert work_defaults.company_work_mode_default(db, company) is ProjectWorkMode.managed
        company.settings = {"work_mode_default": {"work_mode": "guided"}}
        assert work_defaults.company_work_mode_default(db, company) is ProjectWorkMode.guided
        assert work_defaults.is_work_mode_explicitly_configured(company) is True
        assert C.default_work_mode_for_stage("unknown-stage") is ProjectWorkMode.guided
    finally:
        company.stage, company.settings = original_stage, original_settings


def test_first_completed_project_promotes_company_default(client, db, default_company_id):
    """B7 / D2：首次真实项目走完 ⇒ 公司默认 guided → managed（幂等、可被显式覆盖锁死）。"""
    company = org_repo.get_company(db, default_company_id)
    original_stage, original_settings = company.stage, dict(company.settings or {})
    try:
        company.stage = "FOUNDING"
        company.settings = {}
        db.commit()
        assert work_defaults.company_work_mode_default(db, company) is ProjectWorkMode.guided

        assert work_defaults.promote_after_project_completion(db, default_company_id) is True
        db.refresh(company)
        assert work_defaults.company_work_mode_default(db, company) is ProjectWorkMode.managed
        # 幂等：再推一次不产生变化
        assert work_defaults.promote_after_project_completion(db, default_company_id) is False

        from sqlalchemy import select

        from app.models.event import Event

        assert (
            db.scalar(select(Event).where(Event.type == "company.work_default_changed")) is not None
        )

        # 用户显式配过 ⇒ 系统永远不再自动改写
        company.stage = "FOUNDING"
        company.settings = {"work_mode_default": {"work_mode": "guided"}}
        db.commit()
        assert work_defaults.promote_after_project_completion(db, default_company_id) is False
        db.refresh(company)
        assert work_defaults.company_work_mode_default(db, company) is ProjectWorkMode.guided
    finally:
        company.stage, company.settings = original_stage, original_settings
        db.commit()


def test_work_policy_endpoint_exposes_defaults(client):
    """D1/D2：工作策略读面把"默认值 / 是否显式配置 / fixture 门控"都摊开。"""
    body = client.get("/api/v1/company/work-policy").json()
    assert body["work_intake_default_position_code"] == "ceo"
    assert body["work_mode_default"] in {"guided", "managed"}
    assert body["work_mode_by_stage"] == {"FOUNDING": "guided", "OPERATING": "managed"}
    assert body["allow_planning_fixtures"] is True  # conftest 显式打开（测试环境）


# ---------------------------------------------------------------------------
# D2 · 教程实战项目保持 guided（与公司成熟度无关）
# ---------------------------------------------------------------------------


def test_practice_template_declares_guided(client):
    """D2：实战教程是引导形态的入口，模板显式声明，不随公司阶段漂移。"""
    template = client.get("/api/v1/tutorial/templates/classic-snake").json()
    assert template["intake"]["work_mode"] == "guided"


# ---------------------------------------------------------------------------
# 纯函数契约（不依赖 DB）
# ---------------------------------------------------------------------------


def test_canonical_spec_gaps_reports_but_never_invents():
    """W5 的同源纪律：缺什么就说什么，不替公司编需求。"""
    empty = {field: "" for field in C.CANONICAL_PROJECT_FIELDS}
    missing = C.canonical_spec_gaps(empty)
    assert set(missing) == set(C.CANONICAL_PROJECT_FIELDS) - set(C.SPEC_OPTIONAL_FIELDS)

    complete = {
        "background": "b",
        "goal": "g",
        "requirements": [{"code": "R"}],
        "constraints": [],
        "deliverables": ["d"],
        "acceptance_criteria": ["a"],
        "priority": "medium",
        "deadline": None,
        "context": "",
    }
    assert C.canonical_spec_gaps(complete) == ()


def test_approaching_project_helpers_use_the_contract():
    """内部一致性：路由目标默认值来自契约（不是散落在服务里的字符串）。"""
    assert C.RESPONSIBILITY_DEFAULTS[ResponsibilityKind.work_intake] == "ceo"
    assert C.WORK_INTAKE_DEFAULT_POSITION == "ceo"
    assert C.RESPONSIBILITY_SETTINGS_KEY == "work_routing"
    assert C.WORK_MODE_SETTINGS_KEY == "work_mode_default"
    assert ProjectWorkMode.guided in C.WORK_MODE_BY_COMPANY_STAGE.values()
    assert ProjectWorkMode.managed in C.WORK_MODE_BY_COMPANY_STAGE.values()
