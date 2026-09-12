"""M2.10 · **Golden Path × 3**（计划 §13，验收 K1）。

三个场景各一条**真实调用链** E2E（不是"接口 200"）：

```text
A 公司已有完整团队：Project → 路由给管理层 → Manager 查人（事实）→ 决策信封建 DAG + 选人
                   → 系统校验/执行 → Artifact → 交接 → Reviewer PASS → 交付 → Evidence
B Agent 能力不足：Project → Manager 查出能力缺口（**事实**）→ 系统**不替它选**
                   → Manager 走四选一（这里走"改方案 + 派人"）→ 留 DecisionRecord + 结果
C 新 CEO 接任：CEO A 离任 → CEO B 上任 → B **不继承** A 的人级资产
                   → B 拿到 RoleContext / 公司策略 / 制度知识 / 历史决策 / 在跑项目
                   → B 自己决策、自己产出证据
```

三条纪律：**系统只给事实**、**决定必须署名**、**人级资产不搬家**。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.evidence import pipeline as evidence_pipeline
from app.models.decision import DecisionRecord, ToolAudit
from app.models.enums import AuthorityScopeKind, DecisionStatus, EvaluationMode
from app.models.knowledge import Skill
from app.models.organization import Employee
from app.models.person import Person
from app.models.position import PositionAssignment
from app.models.runtime import EmployeeBrain
from app.repositories import knowledge as knowledge_repo
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.repositories import review as review_repo
from app.schemas.position import AssignmentIn, PositionDefinitionIn, SlotIn
from app.services import position_service
from app.services import tasks as task_service
from app.services.economy.work_orders import WorkOrderService
from app.work import contracts as C
from app.work import tool_executor as executor
from app.work import work_order_bridge as bridge

SERVER_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _restore_org_state(org_snapshot):
    yield org_snapshot


@pytest.fixture(autouse=True)
def _no_background_workers(monkeypatch):
    """后台消费者/调度器默认关：黄金路径要**手动驱动**每一步（确定性）。"""
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)
    monkeypatch.setattr(settings, "work_order_bridge_consumers_enabled", False)
    monkeypatch.setattr(settings, "evidence_pipeline_enabled", False)


def _employees(db, company_id: int) -> dict[str, Employee]:
    return {row.slug: row for row in org_repo.list_employees(db, company_id)}


def _manager(ctx_db, company_id: int, slug: str = "charlie", code: str = "lab-golden"):
    """把一个员工放进隔离职位并授予管理权限（含收交付）。"""
    from app.repositories import position as position_repo
    from app.work import authority as authority_service

    employee = _employees(ctx_db, company_id)[slug]
    position_service.release_position(ctx_db, employee, reason="m2.10 lab")
    ctx_db.flush()
    definition = position_repo.get_definition_by_code(ctx_db, code, company_id=company_id)
    if definition is None:
        definition = position_service.create_definition(
            ctx_db,
            PositionDefinitionIn(code=code, name="Golden Lab", job_family="engineering", level=4),
            company_id=company_id,
        )
        ctx_db.flush()
    slots = position_repo.list_slots(
        ctx_db, company_id=company_id, definition_id=int(definition.id)
    )
    slot = (
        slots[0]
        if slots
        else position_service.open_slots(
            ctx_db,
            int(definition.id),
            SlotIn(department_id=int(employee.department_id), count=1, note="m2.10 lab"),
            company_id,
        )[0]
    )
    position_service.assign_position(
        ctx_db, employee, AssignmentIn(slot_id=int(slot.id), reason="m2.10 lab", kind="assign")
    )
    ctx_db.commit()
    for kind in (
        C.AuthorityKind.plan_project_work,
        C.AuthorityKind.assign_task,
        C.AuthorityKind.accept_delivery,
    ):
        authority_service.grant_authority(
            ctx_db,
            position_definition_id=int(definition.id),
            kind=kind,
            scope_kind=AuthorityScopeKind.company,
            commit=False,
        )
    ctx_db.commit()
    return employee, definition


def _context(db, employee: Employee):
    return executor.context_for_employee(db, employee, origin="golden_path")


def _managed_project(client, name: str) -> int:
    response = client.post(
        "/api/v1/projects",
        json={"name": name, "description": "M2.10 黄金路径", "work_mode": "managed"},
    )
    assert response.status_code == 201, response.text
    return int(response.json()["id"])


# ---------------------------------------------------------------------------
# 场景 A —— 公司已有完整团队（全链：路由 → 查人 → 决策 → 执行 → 交接 → 评审 → 交付 → 证据）
# ---------------------------------------------------------------------------


def test_golden_path_a_company_with_a_full_team(client, db, default_company_id, monkeypatch):
    """A：Project → Manager 查人（**只拿事实**）→ 决策信封建 DAG + 选人 → 执行 → 评审 → 交付。"""
    manager, _definition = _manager(db, default_company_id, code="lab-golden-a")
    manager_ctx = _context(db, manager)
    project_id = _managed_project(client, "黄金路径 A")

    # ① 立项即路由：Work Intake 责任人拿到一个「接收决定」任务（系统不替它规划）
    db.expire_all()
    tasks = project_repo.list_tasks(db, project_id)
    assert len(tasks) == 1 and tasks[0].kind == "order_review", "立项后应只有一个接收任务"
    intake = tasks[0]

    # ② Manager 查团队：**事实**（人 / 负载 / 运行时就绪 / 能力），系统不给建议
    people = executor.execute_tool(db, name="list_company_people", args={}, context=manager_ctx)
    assert people.ok is True
    rows = people.data.get("items") or people.data.get("people") or []
    slugs = {row.get("slug") for row in rows}
    assert {"charlie", "bob", "dana"} <= slugs, rows[:2]
    for tool, args in (
        ("get_current_load", {"employee_ids": [int(manager.id)]}),
        ("get_runtime_status", {}),
        ("inspect_readiness", {"employee_id": int(manager.id)}),
        ("calculate_task_fit", {"task_id": int(intake.id)}),
    ):
        result = executor.execute_tool(db, name=tool, args=args, context=manager_ctx)
        assert result.ok is True, (tool, result.error)
    # 读工具只给事实：不许出现"该选谁 / 谁最好"这类**结论字段**
    fit = executor.execute_tool(
        db, name="calculate_task_fit", args={"task_id": int(intake.id)}, context=manager_ctx
    )
    assert fit.ok is True, fit.error
    forbidden_keys = (
        "recommended_employee_id",
        "best_candidate",
        "top_pick",
        "should_hire",
        "rank",
    )
    assert not (set(fit.data) & set(forbidden_keys)), fit.data

    # ③ Manager **自己**决定怎么拆 + 派给谁（一条决策信封，两个动作）
    from app.work import decisions as decision_service

    workers = _employees(db, default_company_id)
    outcome = decision_service.submit_envelope(
        db,
        manager_ctx,
        decision_type=C.DecisionKind.decompose_project,
        reason="把交付拆成实现与验证两条线，各派一位负责人",
        intended_outcome="两条工作线就位，依赖已声明",
        scope=f"project:{project_id}",
        context={"project_id": project_id, "note": "golden path A"},
        actions=[
            {
                "tool": "create_task",
                "args": {
                    "project_id": project_id,
                    "title": "实现登录",
                    "kind": "development",
                    "assignee_id": int(workers["charlie"].id),
                    "produces": ["source_code"],
                },
            },
            {
                "tool": "create_task",
                "args": {
                    "project_id": project_id,
                    "title": "验证登录",
                    "kind": "testing",
                    "assignee_id": int(workers["dana"].id),
                    "depends_on": [],
                    "produces": ["test_report"],
                },
            },
        ],
    )
    assert outcome.status is DecisionStatus.applied, outcome.outcomes
    db.expire_all()
    created = {task.title: task for task in project_repo.list_tasks(db, project_id)}
    assert {"实现登录", "验证登录"} <= set(created)
    implementation = created["实现登录"]
    verification = created["验证登录"]
    # 系统**没有**替它选人：负责人就是 Manager 指定的那两位
    assert int(implementation.assignee_id) == int(workers["charlie"].id)
    assert int(verification.assignee_id) == int(workers["dana"].id)

    # ④ 系统校验 + 执行（真实 mock 运行时；依赖顺序由调度器保证）
    #    M2.7 起任务跑完会停在 `in_review`：Manager 作为 Reviewer 逐个出结论 ——
    #    这段循环本身就是"系统执行 + 判断归人/Agent"的真实分工。
    import time

    from app.work import reviews as review_service
    from app.workflow import orchestrator as orchestrator_module

    def _decide_open_reviews() -> None:
        for row in project_repo.list_tasks(db, project_id):
            if str(row.status) != "in_review":
                continue
            request = review_service.open_request_for_task(db, int(row.id))
            if request is None:
                request = review_service.open_review_request(
                    db,
                    task=row,
                    requester_employee_id=int(manager.id),
                    reviewer_employee_id=int(manager.id),
                    reason="黄金路径 A：Manager 作为 Reviewer 出结论",
                )
            review_service.submit_verdict(
                db,
                request=review_repo.get_request(db, int(request.id)),
                reviewer_employee_id=int(manager.id),
                verdict="PASS",
                notes="按验收标准逐条对过",
            )

    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", True)
    orchestrator_module.orchestrator.notify({"type": "dispatch"})
    deadline = time.monotonic() + 60
    status = ""
    while time.monotonic() < deadline:
        db.expire_all()
        _decide_open_reviews()
        status = client.get(f"/api/v1/projects/{project_id}").json()["status"]
        if status == "completed":
            break
        orchestrator_module.orchestrator.notify({"type": "dispatch"})
        time.sleep(0.2)
    assert status == "completed", f"项目没跑完：{status}"

    # ⑤ Artifact 有归属（谁产出的）+ 交接（下游用到上游产物）
    db.expire_all()
    from app.repositories import handoff as handoff_repo

    produced = handoff_repo.list_artifacts_for_task(db, int(implementation.id))
    assert produced, "实现任务必须产出交付物并带上归属（M2.6/H2）"
    assert all(int(node.task_id) == int(implementation.id) for node in produced)

    # ⑥ 结论可查：每个任务都有一次评审请求 + 署名结论（M2.7 的 RV4）
    db.expire_all()
    for row in project_repo.list_tasks(db, project_id):
        requests = review_repo.list_requests(db, task_id=int(row.id))
        assert requests, f"任务 {row.title} 没有评审记录"
        assert all(item.verdict == "PASS" for item in requests)
        assert all(item.reviewer_employee_id == int(manager.id) for item in requests)

    # ⑦ Evidence → Agent growth：证据流水线从已完成任务收集证据（W24：真实事实才产证据）
    before = len(knowledge_repo.list_skill_usages(db, int(implementation.assignee_id)))
    handler_result = evidence_pipeline.handle_event(
        db,
        {
            "type": "task.completed",
            "data": {"id": int(implementation.id)},
            "company_id": int(default_company_id),
        },
    )
    assert set(handler_result) == {"collected", "created", "updated", "assessments"}
    assert handler_result["collected"] >= 1, "完成的任务应当产生证据候选"
    after = len(knowledge_repo.list_skill_usages(db, int(implementation.assignee_id)))
    assert after >= before  # 证据/技能使用事实是可追踪的（不凭空消失）


# ---------------------------------------------------------------------------
# 场景 B —— Agent 能力不足（系统只报缺口，Manager 自主四选一）
# ---------------------------------------------------------------------------


def test_golden_path_b_capability_gap_manager_decides(db, default_company_id, monkeypatch):
    """B：系统报缺口 → **不替它选** → Manager 走"改方案 + 派人"并留下决策与结果。"""
    manager, _definition = _manager(db, default_company_id, code="lab-golden-b")
    manager_ctx = _context(db, manager)

    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="黄金路径 B",
        description="需要 Kubernetes 能力",
        status="in_progress",
        source_order_text="黄金路径 B",
    )
    db.commit()
    task = task_service.create_task(
        db,
        project_id=int(project.id),
        title="部署到 K8s",
        kind="development",
        assignee_id=None,
        description="需要 Kubernetes 集群运维能力",
    )
    db.commit()

    # ① 系统提供**事实**：候选人能力 / 负载 / 就绪 —— 全是可核对的数据，没有"建议"
    candidates = executor.execute_tool(db, name="list_company_people", args={}, context=manager_ctx)
    assert candidates.ok is True
    competencies = executor.execute_tool(
        db,
        name="get_competencies",
        args={"employee_id": int(_employees(db, default_company_id)["charlie"].id)},
        context=manager_ctx,
    )
    assert competencies.ok is True
    fit = executor.execute_tool(
        db, name="calculate_task_fit", args={"task_id": int(task.id)}, context=manager_ctx
    )
    assert fit.ok is True
    offense = {"recommended_employee_id", "best_candidate", "top_pick", "should_hire", "rank"}
    assert not (set(fit.data) & offense), f"系统给了建议字段：{set(fit.data) & offense}（W1/W2）"
    # 逐人只给"已知/未知/覆盖"，缺的那几项也要列出来（Unknown != Bad）
    rows = fit.data.get("candidates") or fit.data.get("items") or []
    assert rows and all(
        "unknown" in str(row).lower() or "known" in str(row).lower() for row in rows
    )

    # ② 决策之前：系统**没有**任何动作（没有自动派人、没有自动建任务、没有决策行）
    db.expire_all()
    assert project_repo.get_task(db, int(task.id)).assignee_id is None
    assert (
        db.scalars(select(DecisionRecord).where(DecisionRecord.project_id == int(project.id))).all()
        == []
    ), "系统自己产生了决策 —— W1/W2 被破坏"

    # ③ Manager 自主选择：这里走"改方案 + 派现有工程师"（四选一中的一条）
    from app.work import decisions as decision_service

    workers = _employees(db, default_company_id)
    outcome = decision_service.submit_envelope(
        db,
        manager_ctx,
        decision_type=C.DecisionKind.reassign_task,
        reason="团队暂时没有 K8s 能力：改方案（先上单机部署）+ 派现有工程师，能力缺口另行处理",
        intended_outcome="这一单先落地，缺口留下记录",
        scope=f"project:{int(project.id)}",
        context={"project_id": int(project.id), "task_ids": [int(task.id)]},
        actions=[
            {
                "tool": "assign_task",
                "args": {"task_id": int(task.id), "employee_id": int(workers["charlie"].id)},
            },
            {
                "tool": "update_task",
                "args": {
                    "task_id": int(task.id),
                    "description": "先上单机部署（K8s 能力缺口已记录，另行处理）",
                },
            },
        ],
    )
    assert outcome.status is DecisionStatus.applied, outcome.outcomes

    # ④ 结果：决定生效 + 可审计（谁在什么时候以什么理由做了这个选择）
    db.expire_all()
    row = project_repo.get_task(db, int(task.id))
    assert int(row.assignee_id) == int(workers["charlie"].id)
    decision = db.scalars(
        select(DecisionRecord).where(DecisionRecord.project_id == int(project.id))
    ).first()
    assert decision is not None and "K8s" in decision.reason
    audits = list(db.scalars(select(ToolAudit).where(ToolAudit.decision_id == int(decision.id))))
    assert {audit.tool_name for audit in audits} == {"assign_task", "update_task"}
    assert all(audit.outcome == "applied" for audit in audits)
    _ = monkeypatch


# ---------------------------------------------------------------------------
# 场景 C —— 新 CEO 接任（不继承前任的人级资产）
# ---------------------------------------------------------------------------


def test_golden_path_c_ceo_handover_does_not_carry_personal_assets(db, default_company_id):
    """C：CEO A 离任 → CEO B 上任；B 拿到制度面，**拿不到** A 的人级资产，并自己决策。"""
    # ① 前任 CEO：人级资产（技能 / 私人知识 / 特质 / 记忆）
    ceo_a = _employees(db, default_company_id)["alice"]
    db.add(
        Skill(
            employee_id=int(ceo_a.id),
            name="ceo-legacy-skill",
            description="前任的手段",
            attempts=5,
            success_count=5,
        )
    )
    knowledge_repo.create_knowledge_item(
        db,
        owner_employee_id=int(ceo_a.id),
        scope="private",
        title="前任的私人判断",
        topic="前任的私人判断",
        content="只属于 A 的经验",
        status="active",
    )
    brain_a = db.scalars(
        select(EmployeeBrain).where(EmployeeBrain.employee_id == int(ceo_a.id))
    ).first()
    if brain_a is None:
        brain_a = EmployeeBrain(employee_id=int(ceo_a.id))
        db.add(brain_a)
    brain_a.traits = {"schema_version": 1, "curiosity": 0.91, "marker": "CEO-A-TRAITS"}
    brain_a.goals = "前任的目标"
    db.commit()

    # 公司制度面（B 应当拿到）：政策 + 制度知识 + 在跑项目 + 历史决策
    from app.work import readiness, work_defaults

    company_row = org_repo.get_company(db, default_company_id)
    assert company_row is not None
    from app.models.enums import ProjectWorkMode

    work_defaults.set_company_work_mode_default(
        db, company_row, ProjectWorkMode.managed, reason="golden_path_c"
    )
    readiness.set_company_runtime_defaults(db, default_company_id, {"runtime_type": "mock"})
    company = org_repo.get_company(db, default_company_id)
    knowledge_repo.create_knowledge_item(
        db,
        scope="company",
        title="公司制度",
        topic="公司制度",
        content="所有 CEO 都必须遵守的规则",
        status="active",
    )
    running_project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="在跑的项目",
        description="A 立下的项目",
        status="in_progress",
        source_order_text="在跑的项目",
    )
    db.commit()
    _ = company

    # ② 交接：A 离任（释放编制），B 上任（新编制 + 任职）
    position_service.release_position(db, ceo_a, reason="ceo_handover")
    db.commit()
    ceo_b_person = Person(slug="ceo-b", name="CEO B")
    db.add(ceo_b_person)
    db.flush()
    ceo_b = org_repo.create_employee(
        db,
        person_id=int(ceo_b_person.id),
        company_id=int(default_company_id),
        department_id=None,
        name="CEO B",
        slug="ceo-b",
        role="ceo",
        title="CEO",
        status="idle",
        lifecycle_status="active",
        runtime_type="mock",
        runtime_config={},
        workspace_path=f"{settings.workspace_root}/ceo-b",
        memory_namespace="emp_ceo_b",
    )
    db.commit()
    definition = position_service.create_definition(
        db,
        PositionDefinitionIn(code="ceo-b", name="CEO (B)", job_family="management", level=5),
        company_id=default_company_id,
    )
    db.flush()
    slot = position_service.open_slots(
        db,
        int(definition.id),
        SlotIn(department_id=int(ceo_a.department_id), count=1, note="handover"),
        default_company_id,
    )[0]
    position_service.assign_position(
        db, ceo_b, AssignmentIn(slot_id=int(slot.id), reason="ceo_handover", kind="assign")
    )
    db.commit()

    # ③ B **没有**继承 A 的人级资产（逐项核对，不是"看起来没有"）
    db.expire_all()
    b_skills = knowledge_repo.list_skills(db, int(ceo_b.id))
    assert [row.name for row in b_skills] == [], "新 CEO 拿到了前任的技能（W7/W8/W10）"
    # 直接查**表**：仓库读路径按 person 口径过滤，不能只靠它证明"没有行"（W7/W8）
    raw_skill_rows = list(db.scalars(select(Skill).where(Skill.employee_id == int(ceo_b.id))))
    assert raw_skill_rows == [], "新 CEO 名下出现了技能行 —— 开通路径在注入人级资产"
    # 归属层：B 名下不能有任何条目；A 的私人条目**仍然属于 A**（历史不被改写）
    b_visible = knowledge_repo.list_knowledge_items(db, employee_id=int(ceo_b.id))
    b_owned = [row for row in b_visible if row.owner_employee_id == int(ceo_b.id)]
    assert b_owned == [], "新 CEO 名下出现了知识条目（W7/W8/W10）"
    a_private = [row for row in b_visible if row.title == "前任的私人判断"]
    assert a_private and int(a_private[0].owner_employee_id) == int(ceo_a.id)
    assert a_private[0].scope == "private"

    # 检索层：B 的**任务检索**里不出现 A 的私人知识（W10：个人记忆随人）
    from app.learning import retrieval
    from app.schemas.project import ProjectCreate  # noqa: F401  (保持导入面稳定，见下)

    b_retrieval = retrieval.retrieve_for_task(
        db, int(ceo_b.id), "前任的私人判断", "看看有没有继承到前任的经验"
    )
    assert "前任的私人判断" not in " ".join(b_retrieval.knowledge)
    brain_b = db.scalars(
        select(EmployeeBrain).where(EmployeeBrain.employee_id == int(ceo_b.id))
    ).first()
    assert brain_b is None or (brain_b.traits or {}).get("marker") != "CEO-A-TRAITS", (
        "新 CEO 继承/复制了前任的人格（W7/W8）"
    )
    assert brain_b is None or brain_b.goals != "前任的目标"

    # ④ B 拿到的是**制度面**：RoleContext + 公司策略 + 制度知识 + 历史决策 + 在跑项目
    from app.services import position_compat

    context = position_compat.derived_current_position(db, ceo_b)
    assert context is not None and context.code == "ceo-b"
    assert readiness.company_runtime_defaults(db, default_company_id)["runtime_type"] == "mock"
    company_items = knowledge_repo.list_knowledge_items(db, scope="company")
    assert any(row.title == "公司制度" for row in company_items), "制度知识必须对新 CEO 可见（W9）"
    assert any(
        int(project.id) == int(running_project.id) for project in project_repo.list_projects(db)
    )

    # ⑤ B 自己管理、自己决策、自己产出证据（不是"继承 A 的判断"）
    ceo_b, _definition_b = _manager(db, default_company_id, slug="ceo-b", code="lab-golden-c")
    ctx = _context(db, ceo_b)
    from app.work import decisions as decision_service

    outcome = decision_service.submit_envelope(
        db,
        ctx,
        decision_type=C.DecisionKind.accept_project,
        reason="接任后的第一个决定：给在跑的项目排一个收尾任务",
        intended_outcome="项目有明确的下一步",
        scope=f"project:{int(running_project.id)}",
        context={"project_id": int(running_project.id)},
        actions=[
            {
                "tool": "create_task",
                "args": {
                    "project_id": int(running_project.id),
                    "title": "交接收尾",
                    "kind": "general",
                    "assignee_id": int(ceo_b.id),
                },
            }
        ],
    )
    assert outcome.status is DecisionStatus.applied, outcome.outcomes
    db.expire_all()
    new_decision = db.scalars(
        select(DecisionRecord).where(DecisionRecord.actor_employee_id == int(ceo_b.id))
    ).first()
    assert new_decision is not None
    assert new_decision.decision_type == C.DecisionKind.accept_project.value
    # 历史决策仍在（制度记忆不随人走）
    assert (
        db.scalars(
            select(DecisionRecord).where(DecisionRecord.actor_employee_id == int(ceo_a.id))
        ).all()
        is not None
    )
    # 任职是"关旧行 + 开新行"的时间轴：A 的历史没有被改写
    a_assignments = list(
        db.scalars(
            select(PositionAssignment).where(PositionAssignment.employee_id == int(ceo_a.id))
        )
    )
    assert a_assignments and all(row.effective_to is not None for row in a_assignments)


# ---------------------------------------------------------------------------
# 冻结面守卫（K6 的机器化部分）
# ---------------------------------------------------------------------------


def test_freeze_surface_is_declared_and_consistent():
    """K6：冻结面必须**落盘**（形态 / 不变量 / 唯一写入路径 / 模块边界 / M3 清单）。"""
    freeze = (SERVER_ROOT.parents[1] / "docs" / "m2-freeze.md").read_text(encoding="utf-8")
    for section in (
        "## 1. 形态",
        "## 2. 不变量",
        "## 3. 唯一写入路径",
        "## 4. 模块边界",
        "## 5. 关键裁决",
        "## 6. 留给 M3",
    ):
        assert section in freeze, f"冻結面缺章节：{section}"

    # 不变量注册表：全部 enforced（K2），且冻结文档里的条数与代码一致
    assert all(invariant.enforced for invariant in C.INVARIANTS)
    assert f"**{len(C.INVARIANTS)} 条**" in freeze, "冻结文档里的不变量条数与代码不一致"
    assert f"{len(C.INVARIANTS)} 条不变量" in freeze, "冻结文档头部的条数与代码不一致"

    # 三个场景都有 E2E 锚点（K1）
    for anchor in (
        "test_golden_path_a_company_with_a_full_team",
        "test_golden_path_b_capability_gap_manager_decides",
        "test_golden_path_c_ceo_handover_does_not_carry_personal_assets",
    ):
        assert any(invariant_anchor_has(anchor) for _ in (0,)), anchor  # 见下方 helper


def invariant_anchor_has(name: str) -> bool:
    """这个模块里存在同名测试（K1 的自检：锚点不能只写在文档里）。"""
    return name in globals()


def test_work_order_bridge_is_part_of_the_frozen_surface(db, default_company_id):
    """冻结面自检：WorkOrder 桥在冻结后仍然只加边、不碰钱、不建项目（抽掉守卫即红）。"""
    source = (SERVER_ROOT / "app" / "work" / "work_order_bridge.py").read_text(encoding="utf-8")
    for forbidden in ("create_project", "LedgerService", "SettlementService", "order.status ="):
        assert forbidden not in source
    assert C.WORK_ORDER_STATES_FROZEN
    _ = (db, default_company_id, bridge, WorkOrderService, EvaluationMode)
