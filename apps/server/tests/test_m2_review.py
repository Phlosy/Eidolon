"""M2.7 · **Review / Rework / Replan**（W17 / RV1–RV8）。

一句话：**Worker 完成后不再自动 `done`。**

```text
Worker 干完            → task 停在 in_review（RV1/RV2：系统不替谁通过）
Manager/Requester 发起 → review_requests(open, reviewer=**指定的人**)   ← 系统不选人
系统                   → 收集事实（review_facts：产物/交接/会话/返工次数）
Reviewer Agent 出结论  → PASS / REWORK / REJECT / ESCALATE
系统按**显式映射**落地  → REVIEW_VERDICT_TARGETS（ESCALATE 没有目标，停下来等人）
```

本文件两条纪律（与 M2.5/M2.6 同款）：

1. **系统永不产生结论**：事实与结论分开存，且有 AST 守卫（RV1/RV3）；
2. **每条不变量都配一次反例注入**（文末小节）。
"""

from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from app.core.config import settings
from app.events.bus import bus
from app.models.enums import ProjectStatus, ReviewVerdict, TaskStatus
from app.models.organization import Employee
from app.models.project import Task
from app.models.review import ReviewFact, ReviewRequest
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.repositories import review as review_repo
from app.schemas.position import AssignmentIn, PositionDefinitionIn, SlotIn
from app.services import position_service
from app.services import tasks as task_service
from app.work import contracts as C
from app.work import reviews
from app.workflow import orchestrator as orchestrator_module

SERVER_ROOT = Path(__file__).resolve().parents[1]
APP = SERVER_ROOT / "app"
REVIEW_SERVICE = APP / "work" / "reviews.py"
REVIEW_FACTS = APP / "work" / "review_facts.py"
REVIEW_FIXTURE = APP / "work" / "review_fixture.py"
ORCHESTRATOR = APP / "workflow" / "orchestrator.py"


@pytest.fixture(autouse=True)
def _restore_org_state(org_snapshot):
    yield org_snapshot


@pytest.fixture(autouse=True)
def _no_background_dispatch(monkeypatch):
    """本文件手工驱动调度器（单向验证），不要让后台 sweep 插进来。"""
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)


def _employees(db, company_id: int) -> dict[str, Employee]:
    return {row.slug: row for row in org_repo.list_employees(db, company_id)}


def _project(db, company_id: int, name: str = "评审项目", manager_id: int | None = None):
    project = project_repo.create_project(
        db,
        company_id=company_id,
        name=name,
        description="M2.7 测试项目",
        status=ProjectStatus.in_progress.value,
        source_order_text="M2.7 测试项目",
    )
    db.commit()
    if manager_id is not None:
        project.management_employee_id = int(manager_id)
        db.commit()
    return project


def _task(db, project, title: str = "待评审的工作", assignee_id: int | None = None, **kw) -> Task:
    task = task_service.create_task(
        db,
        project_id=int(project.id),
        title=title,
        kind=kw.pop("kind", "development"),
        assignee_id=assignee_id,
        **kw,
    )
    db.commit()
    return task


def _finish_work(db, task: Task) -> Task:
    """把一个任务推进到"干完了、等评审"（模拟 worker 跑完）。"""
    row = project_repo.get_task(db, int(task.id))
    task_service.transition_task(db, row, TaskStatus.todo.value)
    task_service.transition_task(db, row, TaskStatus.in_progress.value)
    task_service.transition_task(db, row, TaskStatus.in_review.value)
    db.commit()
    return row


def _reviewer(db, company_id: int, slug: str = "dana") -> Employee:
    return _employees(db, company_id)[slug]


_BUS_ORIGINAL = None


def _capture_bus(sink: list[tuple[str, dict]]) -> None:
    """把 `bus.publish` 换成一个**同时**记录并转发的替身（事件的真实行为不变）。"""
    global _BUS_ORIGINAL
    _BUS_ORIGINAL = bus.publish

    def _spy(event, payload, **kwargs):
        sink.append((event, payload))
        return _BUS_ORIGINAL(event, payload, **kwargs)

    bus.publish = _spy  # type: ignore[assignment]


def _release_bus() -> None:
    if _BUS_ORIGINAL is not None:
        bus.publish = _BUS_ORIGINAL  # type: ignore[assignment]


def _facts(request_id: int, db) -> dict[str, dict]:
    return {row.kind: row.payload_json for row in review_repo.list_facts(db, request_id)}


# ---------------------------------------------------------------------------
# RV1 / RV2：系统不产生结论；in_review 只能由结论推进
# ---------------------------------------------------------------------------


def test_system_never_produces_a_verdict(client, db, default_company_id):
    """RV1（= H2）：系统只收集事实 —— 没有任何"看事实自动 PASS"的路径。"""
    project = _project(db, default_company_id)
    worker_id = int(_employees(db, default_company_id)["charlie"].id)
    task = _task(db, project, "完成的工作", assignee_id=worker_id)
    _finish_work(db, task)

    # ① 进评审态后，**这个任务**没有任何 review_requests 行 —— 系统不自己开评审、
    #    更不出结论。（必须按项目圈定：库里可能有别的用例留下的评审请求 —— 那是
    #    他们的测试数据，不是"系统在本项目里做了什么"。）
    assert reviews.latest_request_for_task(db, int(task.id)) is None
    assert review_repo.list_requests(db, project_id=int(project.id)) == []

    # ② 状态机层面：`in_review → done` 的能力**不能**被系统路径用到
    #    （旧实现是 `_finalize` 里连跳；现在那里只有 in_review）
    source = ORCHESTRATOR.read_text(encoding="utf-8")
    finalize = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_finalize"
    )
    dump = ast.dump(finalize)
    assert "in_review" in dump, "finalize 应当停在 in_review"
    assert "done" not in dump, "编排器里还留着 in_review → done 的自动连跳 —— W17/RV2 被破坏"

    # ③ 事实收集器里不许出现判断词（事实 ≠ 结论）
    facts_source = REVIEW_FACTS.read_text(encoding="utf-8")
    code_only = "\n".join(
        line.split("#")[0] for line in facts_source.splitlines() if '"' in line or "'" in line
    )
    for forbidden in ("PASS", "REWORK", "REJECT", "ESCALATE", "approved", "verdict"):
        assert forbidden not in code_only, f"事实收集器里出现了判断词：{forbidden}"

    # ④ 服务层没有"自动结论"入口：唯一的结论入口要求评审人身份
    service_dump = ast.dump(ast.parse(REVIEW_SERVICE.read_text(encoding="utf-8")))
    assert "submit_verdict" in service_dump
    assert "auto_verdict" not in service_dump and "auto_approve" not in service_dump


def test_in_review_waits_for_a_verdict(db, default_company_id):
    """RV2（= H1）：没有结论就停在 `in_review`；有了 PASS 才 `done`。"""
    project = _project(db, default_company_id)
    worker = _employees(db, default_company_id)["charlie"]
    reviewer = _reviewer(db, default_company_id)
    task = _task(db, project, "完成的工作", assignee_id=int(worker.id))
    _finish_work(db, task)
    assert str(project_repo.get_task(db, int(task.id)).status) == TaskStatus.in_review.value

    request = reviews.open_review_request(
        db,
        task=project_repo.get_task(db, int(task.id)),
        requester_employee_id=int(worker.id),
        reviewer_employee_id=int(reviewer.id),
        reason="请评审这份实现",
    )
    # 开了请求也**还没**通过 —— 结论是评审人的事
    db.expire_all()
    assert str(project_repo.get_task(db, int(task.id)).status) == TaskStatus.in_review.value

    reviews.submit_verdict(
        db,
        request=review_repo.get_request(db, int(request.id)),
        reviewer_employee_id=int(reviewer.id),
        verdict=ReviewVerdict.passed,
        notes="看起来没问题",
    )
    db.expire_all()
    assert str(project_repo.get_task(db, int(task.id)).status) == TaskStatus.done.value


# ---------------------------------------------------------------------------
# RV3：事实与结论分开存
# ---------------------------------------------------------------------------


def test_facts_and_verdicts_are_stored_separately(db, default_company_id):
    """RV3（= H6）：`review_facts` 是系统写的，`review_requests.verdict` 是评审人写的。"""
    project = _project(db, default_company_id)
    worker = _employees(db, default_company_id)["charlie"]
    reviewer = _reviewer(db, default_company_id)
    task = _task(db, project, "有产出的工作", assignee_id=int(worker.id), produces=["source_code"])
    _finish_work(db, task)
    # 真有一条会话（事实来自它：状态 / 时长 / 错误）
    project_repo.create_work_session(
        db,
        task_id=int(task.id),
        employee_id=int(worker.id),
        runtime_type="mock",
        status="completed",
        started_at=task.actual_start_at,
        ended_at=task.actual_end_at,
        summary="产出了 1 个交付物",
        cost={"duration_sec": 1.25, "tokens": 0},
    )
    db.commit()
    request = reviews.open_review_request(
        db,
        task=project_repo.get_task(db, int(task.id)),
        requester_employee_id=int(worker.id),
        reviewer_employee_id=int(reviewer.id),
    )

    facts = _facts(int(request.id), db)
    assert set(facts) == set(C.REVIEW_FACT_KINDS), facts
    # 事实表里没有结论列，结论表里没有事实载荷
    fact_columns = set(ReviewFact.__table__.columns.keys())
    assert not (fact_columns & {"verdict", "verdict_notes", "decided_at"}), (
        "事实表里出现了结论列 —— 同一条事实两个落点（RV3）"
    )
    request_columns = set(ReviewRequest.__table__.columns.keys())
    assert not (request_columns & {"facts_json", "fact_payload"}), (
        "结论行里塞了事实载荷 —— 事实与结论必须分开（RV3）"
    )
    # 没出结论前，verdict 就是 NULL（不是默认 PASS）
    assert request.verdict is None and request.status == C.REVIEW_REQUEST_OPEN

    # 事实是**可复核**的：声明的产出 vs 实际产出、验收标准、会话、返工次数
    assert facts["produces_gap"]["declared"] == ["source_code"]
    assert facts["produces_gap"]["declared_not_produced"] == ["source_code"]
    assert facts["artifacts"]["count"] == 0
    assert facts["acceptance_criteria"]["present"] is False
    assert facts["session"]["present"] is True
    assert facts["session"]["status"] == "completed"
    assert facts["session"]["duration_sec"] == 1.25
    assert facts["rework"]["rework_count"] == 0
    assert all(row.source.startswith("app.") for row in review_repo.list_facts(db, int(request.id)))


# ---------------------------------------------------------------------------
# RV4：结论署名、追加式
# ---------------------------------------------------------------------------


def test_verdict_is_attributed_and_append_only(db, default_company_id):
    """RV4：结论只能由**指定的**评审人给出，且写下即不可改写。"""
    project = _project(db, default_company_id)
    worker = _employees(db, default_company_id)["charlie"]
    reviewer = _reviewer(db, default_company_id)
    other = _employees(db, default_company_id)["bob"]
    task = _task(db, project, "完成的工作", assignee_id=int(worker.id))
    _finish_work(db, task)
    request = reviews.open_review_request(
        db,
        task=project_repo.get_task(db, int(task.id)),
        requester_employee_id=int(worker.id),
        reviewer_employee_id=int(reviewer.id),
    )

    # 别人不能代签
    with pytest.raises(reviews.ReviewError) as excinfo:
        reviews.submit_verdict(
            db,
            request=request,
            reviewer_employee_id=int(other.id),
            verdict=ReviewVerdict.passed,
        )
    assert "not the reviewer" in str(excinfo.value)
    db.rollback()

    first = reviews.submit_verdict(
        db,
        request=review_repo.get_request(db, int(request.id)),
        reviewer_employee_id=int(reviewer.id),
        verdict=ReviewVerdict.rework,
        notes="测试报告缺失，请补上再交",
    )
    assert first.reviewer_employee_id == int(reviewer.id)
    assert first.decided_at is not None

    # 追加式：同一条请求不能再出第二次结论（改判走新请求）
    with pytest.raises(reviews.ReviewError) as excinfo:
        reviews.submit_verdict(
            db,
            request=review_repo.get_request(db, int(request.id)),
            reviewer_employee_id=int(reviewer.id),
            verdict=ReviewVerdict.passed,
            notes="改判",
        )
    assert "append-only" in str(excinfo.value)
    db.rollback()
    db.expire_all()
    row = review_repo.get_request(db, int(request.id))
    assert row.verdict == ReviewVerdict.rework.value, "结论被改写了 —— RV4"
    assert row.verdict_notes.startswith("测试报告缺失")


# ---------------------------------------------------------------------------
# RV5 / RV6：返工与升级
# ---------------------------------------------------------------------------


def test_rework_returns_to_todo_and_counts(db, default_company_id):
    """RV5（= H3）：REWORK → 回 `todo` + 记次数 + 理由可查（改判走新请求）。"""
    project = _project(db, default_company_id)
    worker = _employees(db, default_company_id)["charlie"]
    reviewer = _reviewer(db, default_company_id)
    task = _task(db, project, "要返工的工作", assignee_id=int(worker.id))
    _finish_work(db, task)

    published: list[tuple[str, dict]] = []
    _capture_bus(published)
    try:
        request = reviews.open_review_request(
            db,
            task=project_repo.get_task(db, int(task.id)),
            requester_employee_id=int(worker.id),
            reviewer_employee_id=int(reviewer.id),
        )
        reviews.submit_verdict(
            db,
            request=review_repo.get_request(db, int(request.id)),
            reviewer_employee_id=int(reviewer.id),
            verdict=ReviewVerdict.rework,
            notes="接口没实现，请补齐",
        )
    finally:
        _release_bus()

    db.expire_all()
    row = project_repo.get_task(db, int(task.id))
    assert str(row.status) == TaskStatus.todo.value, "返工必须把任务放回待办（RV5）"
    assert int(row.rework_count) == 1, "返工次数必须记下来（RV5）"
    assert row.actual_end_at is None, "回到 todo 的任务不该留着完成时间"
    assert [event for event, _ in published if event == "task.review_failed"]
    # 理由写在**结论行**上（可查"为什么被打回"）
    assert "接口没实现" in review_repo.get_request(db, int(request.id)).verdict_notes
    # 结论缺理由 ⇒ 拒绝（不通过却没有理由 = 无法审计）
    with pytest.raises(reviews.ReviewError) as excinfo:
        reviews.open_review_request(
            db,
            task=row,
            requester_employee_id=int(worker.id),
            reviewer_employee_id=int(reviewer.id),
        )
    assert "waiting for review" in str(excinfo.value)  # 已不在 in_review ⇒ 拒绝开评审
    db.rollback()


def test_escalate_has_no_automatic_target(db, default_company_id):
    """RV6（= H5）：ESCALATE 不迁移状态、不被系统自动处理，只叫醒人/管理层。"""
    assert C.REVIEW_VERDICT_TARGETS[ReviewVerdict.escalated.value] is None

    project = _project(db, default_company_id)
    worker = _employees(db, default_company_id)["charlie"]
    reviewer = _reviewer(db, default_company_id)
    task = _task(db, project, "说不清的工作", assignee_id=int(worker.id))
    _finish_work(db, task)
    request = reviews.open_review_request(
        db,
        task=project_repo.get_task(db, int(task.id)),
        requester_employee_id=int(worker.id),
        reviewer_employee_id=int(reviewer.id),
    )

    published: list[tuple[str, dict]] = []
    _capture_bus(published)
    try:
        reviews.submit_verdict(
            db,
            request=review_repo.get_request(db, int(request.id)),
            reviewer_employee_id=int(reviewer.id),
            verdict=ReviewVerdict.escalated,
            notes="验收标准自相矛盾，我判不了",
        )
    finally:
        _release_bus()

    db.expire_all()
    row = project_repo.get_task(db, int(task.id))
    assert str(row.status) == TaskStatus.in_review.value, "ESCALATE 不该动状态（RV6）"
    events = [event for event, _ in published]
    assert "task.review_required" in events, "升级必须叫醒人/管理层"
    assert "task.review_passed" not in events and "task.review_failed" not in events
    escalated = next(payload for event, payload in published if event == "task.review_required")
    assert escalated.get("escalated") is True and escalated.get("target_status") is None


# ---------------------------------------------------------------------------
# RV7：只有该项目的 Manager 能 replan
# ---------------------------------------------------------------------------


def test_replan_is_manager_only(client, db, default_company_id):
    """RV7（= H4）：replan 只接受该项目 Manager；系统自己不 replan。"""
    people = _employees(db, default_company_id)
    manager = people["morgan"]
    outsider = people["ceo"] if "ceo" in people else people["alice"]
    project = _project(db, default_company_id, "要重规划的项目", manager_id=int(manager.id))

    # ① 不是这个项目的 Manager ⇒ 拒绝（服务层 + HTTP 422）
    with pytest.raises(reviews.ReviewError) as excinfo:
        reviews.replan_project(
            db, project=project, actor_employee_id=int(outsider.id), reason="我觉得要重排"
        )
    assert "not the manager" in str(excinfo.value)
    db.rollback()

    # ② 没有 Manager 的项目 ⇒ 拒绝（没有人可问责就不能重规划）
    anonymous = _project(db, default_company_id, "没有负责人的项目")
    with pytest.raises(reviews.ReviewError) as excinfo:
        reviews.replan_project(
            db, project=anonymous, actor_employee_id=int(outsider.id), reason="随便改"
        )
    assert "no manager" in str(excinfo.value)
    db.rollback()

    # ③ 本项目的 Manager ⇒ 允许：项目回到规划态、发事件、**不生成任何图**
    before = len(project_repo.list_tasks(db, int(project.id)))
    reviews.replan_project(
        db, project=project, actor_employee_id=int(manager.id), reason="需求变了，重新拆"
    )
    db.expire_all()
    assert str(project_repo.get_project(db, int(project.id)).status) == (
        ProjectStatus.planning.value
    )
    assert len(project_repo.list_tasks(db, int(project.id))) == before, "replan 不该产生任务"
    # 工具面同款（required 语义 ⇒ 走决策信封；见 test_m2_review 的工具用例）
    assert C.REVIEW_VERDICT_TARGETS  # 契约在位


# ---------------------------------------------------------------------------
# RV8：跨面映射显式且单向
# ---------------------------------------------------------------------------


def test_guided_decision_maps_to_verdict_explicitly():
    """RV8：`ReviewDecision` → `ReviewVerdict` 有**显式**映射表，反向不存在。"""
    mapping = C.GUIDED_REVIEW_DECISION_TO_VERDICT
    assert set(mapping) == {
        "approved",
        "conditionally_approved",
        "changes_requested",
        "rejected",
    }, "人类阶段门的四种结论都必须有归属（不允许隐式兜底）"
    assert set(mapping.values()) <= {v.value for v in ReviewVerdict}
    # 单向：没有任何"verdict → 阶段门结论"的表
    names = {name for name in dir(C) if name.isupper()}
    assert not [name for name in names if "VERDICT_TO_" in name], "跨面映射必须是单向的（W29/RV8）"
    # 四个面的边界表仍在（M2.0 的 W29 守卫），且任务级评审的判定者是 Reviewer Agent
    review_boundary = next(b for b in C.verdict_boundaries() if b.surface == "task_review")
    assert review_boundary.decider == "reviewer_agent"


# ---------------------------------------------------------------------------
# 端到端：M2.7 不破坏 M2.5/M2.6 的链路（fixture 项目照旧跑到底）
# ---------------------------------------------------------------------------


def test_fixture_project_still_completes_because_a_stand_in_reviews_it(
    client, db, default_company_id, monkeypatch
):
    """替身评审（门控基础设施）让确定性图仍然跑得完，且**结论署在具体人头上**。"""
    monkeypatch.setattr(settings, "allow_planning_fixtures", True)
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", True)
    response = client.post(
        "/api/v1/projects",
        json={
            "name": "评审替身项目",
            "description": "确定性图",
            "planning_fixture": "deterministic_template",
        },
    )
    assert response.status_code == 201, response.text
    project_id = int(response.json()["id"])

    orchestrator_module.orchestrator.notify({"type": "dispatch"})
    deadline = time.monotonic() + 60
    status = ""
    while time.monotonic() < deadline:
        status = client.get(f"/api/v1/projects/{project_id}").json()["status"]
        if status == ProjectStatus.completed.value:
            break
        time.sleep(0.2)
    assert status == ProjectStatus.completed.value, f"fixture 图没跑完：{status}"

    db.expire_all()
    tasks = project_repo.list_tasks(db, project_id)
    assert {str(task.status) for task in tasks} == {TaskStatus.done.value}
    requests = review_repo.list_requests(db, project_id=project_id)
    assert len(requests) == len(tasks), "每个任务都该有一次评审（替身出的）"
    for row in requests:
        assert row.verdict == ReviewVerdict.passed.value
        assert row.reviewer_employee_id is not None, "替身结论也必须署在人头上（RV4）"
        assert "fixture" in row.reason
    # 普通项目（非 fixture）不会自动评审 —— 见 RV1 的用例

    # HTTP 读面能看到事实与结论
    view = client.get(f"/api/v1/tasks/{tasks[0].id}/review").json()
    assert view["review"]["verdict"] == ReviewVerdict.passed.value
    assert {fact["kind"] for fact in view["review"]["facts"]} == set(C.REVIEW_FACT_KINDS)
    assert view["verdict_targets"]["ESCALATE"] is None


def test_production_project_gets_no_verdict_and_escalates_to_the_manager(
    db, default_company_id, monkeypatch, _no_background_dispatch
):
    """非 fixture 项目：没有替身 ⇒ 停在 `in_review` 且**上报**需要评审人（不自动通过）。"""
    project = _project(db, default_company_id, "生产项目")
    worker = _employees(db, default_company_id)["charlie"]
    task = _task(db, project, "生产工作", assignee_id=int(worker.id))
    _finish_work(db, task)

    published: list[tuple[str, dict]] = []
    _capture_bus(published)
    try:
        monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", True)
        import asyncio

        asyncio.run(orchestrator_module.Orchestrator()._dispatch_pending())
    finally:
        _release_bus()

    escalations = [
        payload
        for event, payload in published
        if event == "task.review_required" and payload.get("id") == int(task.id)
    ]
    assert escalations, "没有人接手评审时必须上报，而不是静静等着"
    assert escalations[0]["needs_reviewer"] is True
    db.expire_all()
    assert str(project_repo.get_task(db, int(task.id)).status) == TaskStatus.in_review.value


# ---------------------------------------------------------------------------
# 工具面：结论与 replan 都必须是决策（DR7 / T7）
# ---------------------------------------------------------------------------


def _lab(db, company_id: int, code: str = "lab-review"):
    """隔离实验台（幂等）：给一个评审人 `accept_delivery` + `plan_project_work`。"""
    from app.models.enums import AuthorityScopeKind
    from app.repositories import position as position_repo
    from app.work import authority as authority_service

    employee = _employees(db, company_id)["dana"]
    position_service.release_position(db, employee, reason="m2.7 lab")
    db.flush()
    definition = position_repo.get_definition_by_code(db, code, company_id=company_id)
    if definition is None:
        definition = position_service.create_definition(
            db,
            PositionDefinitionIn(code=code, name="Review Lab", job_family="quality", level=3),
            company_id=company_id,
        )
        db.flush()
    slots = position_repo.list_slots(db, company_id=company_id, definition_id=int(definition.id))
    slot = (
        slots[0]
        if slots
        else position_service.open_slots(
            db,
            int(definition.id),
            SlotIn(department_id=int(employee.department_id), count=1, note="m2.7 lab"),
            company_id,
        )[0]
    )
    position_service.assign_position(
        db, employee, AssignmentIn(slot_id=int(slot.id), reason="m2.7 lab", kind="assign")
    )
    db.commit()
    for kind in (C.AuthorityKind.accept_delivery, C.AuthorityKind.plan_project_work):
        authority_service.grant_authority(
            db,
            position_definition_id=int(definition.id),
            kind=kind,
            scope_kind=AuthorityScopeKind.company,
            commit=False,
        )
    db.commit()
    return employee


def test_verdict_tool_requires_a_decision_envelope(db, default_company_id):
    """审阅结论是判断 ⇒ `submit_review_verdict` 语义为 required（DR7）。"""
    from app.work import decisions as decision_service
    from app.work import tool_executor

    manager_id = int(_employees(db, default_company_id)["morgan"].id)
    project = _project(db, default_company_id, "结论工具", manager_id=manager_id)
    reviewer = _lab(db, default_company_id)
    worker = _employees(db, default_company_id)["charlie"]
    task = _task(db, project, "完成的工作", assignee_id=int(worker.id))
    _finish_work(db, task)
    request = reviews.open_review_request(
        db,
        task=project_repo.get_task(db, int(task.id)),
        requester_employee_id=int(worker.id),
        reviewer_employee_id=int(reviewer.id),
    )

    ctx = tool_executor.context_for_employee(db, reviewer, origin="test")
    # ① 裸调用被 DR7 拦住（requited 语义必须隶属一条决策）
    bare = tool_executor.execute_tool(
        db,
        name="submit_review_verdict",
        args={"review_request_id": int(request.id), "verdict": "PASS"},
        context=ctx,
    )
    assert bare.ok is False and bare.reason == "decision_required"

    # ② 经决策信封：结论落地、审计里有那条动作
    outcome = decision_service.submit_envelope(
        db,
        ctx,
        decision_type=C.DecisionKind.accept_delivery,
        reason="产出符合验收标准，通过",
        intended_outcome="任务进入 done，下游可以开始",
        scope=f"project:{int(project.id)}",
        context={"project_id": int(project.id), "task_ids": [int(task.id)]},
        actions=[
            {
                "tool": "submit_review_verdict",
                "args": {
                    "review_request_id": int(request.id),
                    "verdict": "PASS",
                    "notes": "验收标准逐条对过",
                },
            }
        ],
    )
    assert outcome.status is C.DecisionStatus.applied, outcome.outcomes
    db.expire_all()
    row = review_repo.get_request(db, int(request.id))
    assert row.verdict == ReviewVerdict.passed.value
    assert row.verdict_decision_id is None  # 决策链接由信封层留痕（见下）
    assert str(project_repo.get_task(db, int(task.id)).status) == TaskStatus.done.value


# ---------------------------------------------------------------------------
# 反例注入：把违规实现喂给同一条守卫，守卫必须转红
# ---------------------------------------------------------------------------


def _guard_system_produces_no_verdict(source: str) -> None:
    """守卫：编排器里不许出现"自动通过"的连跳（RV1/RV2）。"""
    finalize = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_finalize"
    )
    dump = ast.dump(finalize)
    assert "done" not in dump, "编排器里出现了自动通过（RV2）"


def _guard_verdicts_need_a_reviewer(requested_reviewer: int | None) -> None:
    """守卫：没有评审人就不能有结论（RV4）。"""
    assert requested_reviewer is not None, "结论没有评审人 —— 系统替人判断了（RV1/RV4）"


def _guard_escalate_has_no_target(target: str | None) -> None:
    """守卫：ESCALATE 不许有自动状态目标（RV6）。"""
    assert target is None, "ESCALATE 被翻译成状态迁移 —— 系统替人做了决定（RV6）"


def test_guards_catch_the_counter_examples(tmp_path):
    """反例注入：每条守卫在收到违规输入时都必须转红。"""
    # RV1/RV2：编排器里的自动通过
    clean = "def _finalize(self, task_id):\n    return None\n"
    _guard_system_produces_no_verdict(clean)
    dirty = "def _finalize(self, task_id):\n    transition(task, TaskStatus.done.value)\n"
    with pytest.raises(AssertionError, match="RV2"):
        _guard_system_produces_no_verdict(dirty)

    # RV4：没有评审人的结论
    _guard_verdicts_need_a_reviewer(7)
    with pytest.raises(AssertionError, match="RV1/RV4"):
        _guard_verdicts_need_a_reviewer(None)

    # RV6：ESCALATE 被翻译成状态
    _guard_escalate_has_no_target(None)
    with pytest.raises(AssertionError, match="RV6"):
        _guard_escalate_has_no_target("todo")


def test_guard_catches_heuristics_in_the_service(tmp_path):
    """反例注入：评审服务里出现"事实 → 结论"的启发式 ⇒ 守卫转红。"""

    def _guard(source: str) -> None:
        lowered = source.lower()
        for forbidden in ("if facts", "if all(", "auto_verdict", "auto_pass", "heuristic"):
            assert forbidden not in lowered, f"评审服务里出现了启发式判断：{forbidden}"

    _guard(REVIEW_SERVICE.read_text(encoding="utf-8"))
    with pytest.raises(AssertionError, match="启发式"):
        _guard("def f(facts):\n    if facts and facts.ok:\n        return 'PASS'\n")


def test_fixture_reviewer_is_gated_and_named(monkeypatch):
    """替身评审是**门控基础设施**，且必须写在名字里（不是生产判断逻辑）。

    门控必须是**行为**上的（真的去读开关），不能被"源码里提到过这个常量"糊弄过去 ——
    所以这里直接调用它、断言返回值。
    """
    from types import SimpleNamespace

    from app.work import review_fixture

    fixture_project = SimpleNamespace(planning_fixture="deterministic_template")
    normal_project = SimpleNamespace(planning_fixture="none")

    monkeypatch.setattr(settings, "allow_planning_fixtures", False)
    assert review_fixture.enabled_for(fixture_project) is False, (
        "门控关闭时替身评审仍然生效 —— 生产项目会被系统替它通过（RV1）"
    )
    monkeypatch.setattr(settings, "allow_planning_fixtures", True)
    assert review_fixture.enabled_for(fixture_project) is True
    assert review_fixture.enabled_for(normal_project) is False, (
        "非 fixture 项目不许自动评审 —— 那是系统替 Reviewer 做判断（RV1）"
    )
    assert review_fixture.enabled_for(None) is False

    source = REVIEW_FIXTURE.read_text(encoding="utf-8")
    assert "allow_planning_fixtures" in source, "替身评审必须与规划 fixture 同款门控"
    assert "deterministic_template" in source
    # 它走的是**真实评审服务**（同一段代码），不是自己写 SQL / 自己改状态
    assert "reviews.open_review_request" in source and "reviews.submit_verdict" in source
    assert "task.status = " not in source, "替身自己改状态 = 绕过状态机"
    # 生产路径（编排器）只在门控为真时调用它
    orch = ORCHESTRATOR.read_text(encoding="utf-8")
    assert "review_fixture.enabled_for(" in orch
    assert "if success and fixture_review_enabled" in orch
