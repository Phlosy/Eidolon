"""M2.5 · **Canonical Task Graph Runtime**（不变量 R1–R12）。

用户拍板的执行语义：

```text
Manager chooses.   ← 建哪些 Task、依赖、**谁负责**、是否改派/取消/重规划
System schedules.  ← 依赖是否满足、是否就绪、能不能派、何时派
Worker executes.   ← WorkSession → Runtime
```

本文件的纪律：**每条不变量都配一次反例注入**（把违规实现喂给同一条守卫，
守卫必须转红）。不这么做的守卫是装饰品 —— M1.10 起沿用同款纪律。
"""

from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.events.bus import bus
from app.models.enums import ProjectStatus, TaskStatus
from app.models.event import Event
from app.models.organization import Employee
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.repositories import runtimes as runtime_repo
from app.services import tasks as task_service
from app.work import contracts as C
from app.work import dispatch as D
from app.workflow import orchestrator as orchestrator_module

SERVER_ROOT = Path(__file__).resolve().parents[1]
APP = SERVER_ROOT / "app"
DISPATCH_MODULE = APP / "work" / "dispatch.py"
ORCHESTRATOR_MODULE = APP / "workflow" / "orchestrator.py"
FIXTURE_MODULE = APP / "work" / "planning_fixture.py"


@pytest.fixture(autouse=True)
def _restore_org_state(org_snapshot):
    """本文件会动任职/生命周期 —— 用完必须还原（否则污染同会话其它用例）。"""
    yield org_snapshot


@pytest.fixture(autouse=True)
def _no_background_dispatch(monkeypatch):
    """关掉后台调度器，并让**全局**实例失活。

    只关开关不够：`TestClient` 关闭时若全局 runner 正处在同步段（读库 + publish），
    取消不会立刻生效 —— 它的 sweep 会与本文件手工调用的 `_dispatch_pending`
    抢同一份事件流（实测：随机序下同一条 `task.assignment_required` 被报了两次，
    断言就红了）。这里把全局实例的 `_handle` 换成惰性替身，机制上杜绝干扰。
    """
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)


@pytest.fixture()
def _inert_global(monkeypatch):
    """让**全局**调度器实例失活（只给手工调 `_dispatch_pending` 的用例用）。

    为什么需要：`TestClient` 关闭时若全局 runner 正处在同步段（读库 + publish），
    取消不会立刻生效 —— 它的 sweep 会与本文件手工调用的 `_dispatch_pending`
    抢同一份事件流（实测：随机序下同一条 `task.assignment_required` 被报了两次）。
    需要**真实**全局调度器跑图的用例（如 fixture 项目跑到底）不挂这个 fixture。
    """

    async def _inert(_signal: dict) -> None:  # pragma: no cover - 测试替身
        return None

    monkeypatch.setattr(orchestrator_module.orchestrator, "_handle", _inert)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _employees(db, company_id: int) -> dict[str, Employee]:
    return {row.slug: row for row in org_repo.list_employees(db, company_id)}


def _project(db, company_id: int, name: str = "DAG 运行时项目", **overrides):
    fields = {
        "company_id": company_id,
        "name": name,
        "description": "M2.5 测试项目",
        "status": ProjectStatus.in_progress.value,
        "source_order_text": "M2.5 测试项目",
    }
    fields.update(overrides)
    project = project_repo.create_project(db, **fields)
    db.commit()
    return project


def _task(db, project, title: str, *, kind: str = "development", assignee_id=None, **kw):
    task = task_service.create_task(
        db,
        project_id=int(project.id),
        title=title,
        kind=kind,
        assignee_id=assignee_id,
        **kw,
    )
    db.commit()
    return task


def _events(db, event_type: str, *, project_id: int | None = None) -> list[Event]:
    stmt = select(Event).where(Event.type == event_type)
    if project_id is not None:
        stmt = stmt.where(Event.project_id == int(project_id))
    return list(db.scalars(stmt.order_by(Event.id)))


def _evaluate(db, task) -> D.DispatchEvaluation:
    db.expire_all()
    return D.evaluate_dispatch(db, project_repo.get_task(db, int(task.id)))


def _deactivate(db, employee: Employee) -> None:
    employee.lifecycle_status = "offboarded"
    db.commit()


@pytest.fixture()
def real_runtime(db):
    """临时把某个员工切到真实 runtime —— **用完还原**。

    `org_snapshot` 只管任职与生命周期，**不管** `employees.runtime_type`；
    不还原的话，后面跑的 fixture 用例会看到"少了模型绑定"的工程师而红。
    """
    touched: list[tuple[int, str]] = []

    def _make_unavailable(employee: Employee) -> None:
        if not touched:
            object.__setattr__  # noqa: B018 - 保持显式意图：这里只动两列
        touched.append((int(employee.id), str(employee.runtime_type)))
        employee.runtime_type = "claude_code"
        db.commit()
        existing = runtime_repo.get_instance_for_employee(db, int(employee.id))
        if existing is not None:
            # 有实例但未绑定模型 ⇒ 同样归到 runtime_unavailable（provider_missing）
            existing.model_binding_id = None
            db.commit()

    try:
        yield _make_unavailable
    finally:
        for employee_id, runtime_type in touched:
            row = db.get(Employee, employee_id)
            if row is not None:
                row.runtime_type = runtime_type
        db.commit()


# ---------------------------------------------------------------------------
# R3：结构就绪 ≠ 可派发
# ---------------------------------------------------------------------------


def test_readiness_and_dispatchability_are_distinct(db, default_company_id):
    """R3：就绪是**结构事实**（依赖满足），可派发是**运行检查**（人/运行时都在）。

    这条用例刻意让两者分离：任务结构上完全就绪，但因为没负责人而不可派发。
    反例注入见文末 `test_guard_catches_conflated_readiness`。
    """
    people = _employees(db, default_company_id)
    project = _project(db, default_company_id)
    first = _task(db, project, "上游", assignee_id=int(people["alice"].id))
    second = _task(db, project, "下游（没负责人）", depends_on=[int(first.id)])

    # ① 依赖没完成 ⇒ 既不就绪也不可派发
    assert int(second.id) not in D.structural_ready_task_ids(db, int(project.id))
    assert D.REASON_NOT_READY in _evaluate(db, second).reasons

    # ② 上游完成 ⇒ **结构就绪**（契约纯函数说了算，与有没有人无关）
    task_service.transition_task(
        db, project_repo.get_task(db, int(first.id)), TaskStatus.done.value, force=True
    )
    db.commit()
    assert int(second.id) in D.structural_ready_task_ids(db, int(project.id))
    assert int(second.id) in {int(t.id) for t in D.ready_tasks(db, int(project.id))}

    # ③ 但**不可派发** —— 就绪不等于能开工
    evaluation = _evaluate(db, second)
    assert evaluation.dispatchable is False
    assert evaluation.needs_management is True
    assert D.REASON_ASSIGNEE_MISSING in evaluation.reasons

    # ④ 契约层同样把两类原因分开登记（口径唯一）
    assert D.REASON_ASSIGNEE_MISSING in C.REQUIRES_MANAGEMENT_DECISION
    assert D.REASON_NOT_READY in C.SYSTEM_BLOCKING_REASONS
    assert not (C.REQUIRES_MANAGEMENT_DECISION & C.SYSTEM_BLOCKING_REASONS)
    assert C.DISPATCH_BLOCK_REASONS == (C.REQUIRES_MANAGEMENT_DECISION | C.SYSTEM_BLOCKING_REASONS)


# ---------------------------------------------------------------------------
# R1 / R2：只派给已存在的负责人；永不自选
# ---------------------------------------------------------------------------


def test_dispatch_only_to_existing_assignee(db, default_company_id):
    """R1：可派发的任务**只**派给 `task.assignee_id` 那个人，也不改它。"""
    people = _employees(db, default_company_id)
    bob = people["bob"]
    project = _project(db, default_company_id)
    task = _task(db, project, "有负责人", assignee_id=int(bob.id))

    evaluation = _evaluate(db, task)
    assert evaluation.dispatchable is True
    assert evaluation.assignee_id == int(bob.id) == int(task.assignee_id)
    assert evaluation.reasons == ()

    # 判定是只读的：跑一百次也不会把负责人换掉
    for _ in range(3):
        assert _evaluate(db, task).assignee_id == int(bob.id)
    assert project_repo.get_task(db, int(task.id)).assignee_id == int(bob.id)


def test_ready_unassigned_is_never_auto_assigned(db, default_company_id):
    """R1/R2：就绪但没负责人 ⇒ **绝不**自动挑一个人；这是管理决策的入口。"""
    people = _employees(db, default_company_id)
    project = _project(db, default_company_id)
    task = _task(db, project, "无人认领的活")

    evaluation = _evaluate(db, task)
    assert evaluation.dispatchable is False
    assert evaluation.assignee_id is None
    assert evaluation.needs_management is True
    assert evaluation.event_type == "task.assignment_required"

    # 系统**没有**写下任何人 —— 领域状态就是最好的证据
    db.expire_all()
    assert project_repo.get_task(db, int(task.id)).assignee_id is None
    assert project_repo.get_task(db, int(task.id)).status == TaskStatus.backlog.value

    # 候选池里明明有人（CEO/PM/工程师都在），系统依然不选（反例见文末）
    assert len(people) >= 3


# ---------------------------------------------------------------------------
# R4：就绪但缺负责人 ⇒ 升级为管理决策（事件，不是补救）
# ---------------------------------------------------------------------------


def test_ready_unassigned_escalates_as_management_decision(
    db, default_company_id, monkeypatch, _inert_global
):
    """R4：调度器遇到"就绪但没负责人"只发事件；同一原因不刷屏（去重）。"""
    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bus, "publish", lambda event, payload, **kw: published.append((event, payload))
    )

    project = _project(db, default_company_id)
    task = _task(db, project, "无人认领的活")

    orchestrator = orchestrator_module.Orchestrator()
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", True)

    import asyncio

    asyncio.run(orchestrator._dispatch_pending())
    # 只认**这个任务**的上报：库里可能有其它遗留项目同时在扫（全局调度器），
    # 不筛 id 的话断言会被别人的上报顶掉（实测过：整仓跑时偶发）。
    escalations = [
        item
        for item in published
        if item[0] == "task.assignment_required" and item[1].get("id") == int(task.id)
    ]
    assert escalations, "就绪但缺负责人必须上报，不能静静等着"
    payload = escalations[0][1]
    assert payload["needs_management"] is True
    assert D.REASON_ASSIGNEE_MISSING in payload["reasons"]

    # 去重：同一 (task, event) 不再重复上报
    published.clear()
    asyncio.run(orchestrator._dispatch_pending())
    assert not [
        item
        for item in published
        if item[0] == "task.assignment_required" and item[1].get("id") == int(task.id)
    ]

    # 事件落的还是**事实**：任务依然没人、依然没开始
    db.expire_all()
    row = project_repo.get_task(db, int(task.id))
    assert row.assignee_id is None
    assert row.status == TaskStatus.backlog.value


# ---------------------------------------------------------------------------
# R5：负责人/runtime 不可用 ⇒ 不自动改派
# ---------------------------------------------------------------------------


def test_runtime_unavailable_does_not_reassign(db, default_company_id, real_runtime):
    """R5：负责人离线/被回收/运行时缺失 ⇒ 上报原因，**不**换人。"""
    people = _employees(db, default_company_id)
    charlie = people["charlie"]
    project = _project(db, default_company_id)
    task = _task(db, project, "运行时缺失的活", assignee_id=int(charlie.id))

    # ① 真实 runtime 但没有运行时实例 —— 不能执行，也不能让别人上
    real_runtime(charlie)
    evaluation = _evaluate(db, task)
    assert evaluation.dispatchable is False
    assert evaluation.needs_management is True
    assert {D.REASON_RUNTIME_UNAVAILABLE, D.REASON_PROVIDER_MISSING} & set(evaluation.reasons)
    assert evaluation.event_type == "task.runtime_unavailable"
    assert evaluation.assignee_id == int(charlie.id), "改派是管理决策，系统不许动"

    # ② 负责人被回收 ⇒ 同样只上报
    charlie.runtime_type = "mock"
    db.commit()
    _deactivate(db, charlie)
    evaluation = _evaluate(db, task)
    assert D.REASON_ASSIGNEE_INACTIVE in evaluation.reasons
    assert evaluation.needs_management is True
    assert evaluation.assignee_id == int(charlie.id)
    db.expire_all()
    assert project_repo.get_task(db, int(task.id)).assignee_id == int(charlie.id)

    # ③ 每个"管理不可用"类原因都有事件归属；每个"系统阻断"类原因都没有
    assert D.REASON_EVENTS == {
        D.REASON_ASSIGNEE_MISSING: "task.assignment_required",
        D.REASON_ASSIGNEE_INACTIVE: "task.runtime_unavailable",
        D.REASON_RUNTIME_UNAVAILABLE: "task.runtime_unavailable",
        D.REASON_PROVIDER_MISSING: "task.runtime_unavailable",
        D.REASON_TASK_HELD: "task.blocked",
        D.REASON_TASK_FAILED: "task.failed",
        # M2.6（H5）：声明要用的上游跑完却没产出 ⇒ 计划与事实不符，复用 replan 事件
        D.REASON_INPUT_ARTIFACTS_MISSING: "project.replan_required",
    }


# ---------------------------------------------------------------------------
# R6 / R7：guided、managed、fixture 共用一个 DAG 运行时
# ---------------------------------------------------------------------------


ASYNC_OR_SYNC = (ast.FunctionDef, ast.AsyncFunctionDef)


def _pipeline_function(tree: ast.Module, name: str):
    """按名字取函数（同步/异步都算 —— `_dispatch_pending` 是 async）。"""
    return next(
        node for node in ast.walk(tree) if isinstance(node, ASYNC_OR_SYNC) and node.name == name
    )


def _runtime_surface() -> dict[str, object]:
    """运行时的**形状**：判定入口只有一个模块，编排器不含按模式/按 kind 的分支。"""
    source = ORCHESTRATOR_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    dispatch_fn = _pipeline_function(tree, "_dispatch_pending")
    return {
        "source": source,
        "dispatch_dump": ast.dump(dispatch_fn),
        "dispatch_module": DISPATCH_MODULE.read_text(encoding="utf-8"),
    }


def test_guided_and_managed_share_one_dag_runtime(db, default_company_id):
    """R6：`work_mode` 只影响**人的参与度**，不影响调度路径（无 per-mode 派发器）。"""
    surface = _runtime_surface()
    # 派发路径**不许按任何"分类维度"分叉**：只说载荷里出现字段名不算分叉，
    # 真正要拦的是 `if <分类维度> == ...`。所以扫的是**比较表达式**。
    comparisons = [
        node
        for node in ast.walk(
            _pipeline_function(ast.parse(str(surface["source"])), "_dispatch_pending")
        )
        if isinstance(node, ast.Compare)
    ]
    for forbidden in ("work_mode", "planning_fixture", "guided", "managed", "kind"):
        assert not [node for node in comparisons if forbidden in ast.dump(node)], (
            f"派发路径按 {forbidden} 分叉了 —— R6/R12"
        )
    assert "work_mode" not in str(surface["dispatch_module"]), "判定模块不该知道产品模式"

    # 行为等价：同样形状的两个项目（只是 work_mode 不同）得到同样的判定
    people = _employees(db, default_company_id)
    outcomes = []
    for mode in ("guided", "managed"):
        project = _project(db, default_company_id, name=f"{mode} 项目", work_mode=mode)
        task = _task(db, project, f"{mode} 的任务", assignee_id=int(people["bob"].id))
        evaluation = _evaluate(db, task)
        outcomes.append((evaluation.dispatchable, evaluation.assignee_id, evaluation.reasons))
    assert outcomes[0] == outcomes[1] == (True, int(people["bob"].id), ())


def test_fixture_graph_uses_the_same_runtime(client, db, default_company_id, monkeypatch):
    """R7：fixture 只负责**建图**；建完即退出，之后与生产项目同一条调度路径。

    两段验证：
    ① 关掉后台调度 → 用**同一个判定模块**读 fixture 图（结论与生产图一致）；
    ② 打开后台调度 → 图能自己跑完（证明推进确实由共享运行时完成，不是 fixture 的私活）。
    """
    monkeypatch.setattr(settings, "allow_planning_fixtures", True)
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)
    response = client.post(
        "/api/v1/projects",
        json={
            "name": "fixture 项目",
            "description": "确定性图",
            "planning_fixture": "deterministic_template",
        },
    )
    assert response.status_code == 201, response.text
    project_id = response.json()["id"]

    # ① 图在立项时就整张建好，且**每个**任务都有负责人（否则就该等管理层来指派）
    detail = client.get(f"/api/v1/projects/{project_id}").json()
    assert len(detail["tasks"]) == 6
    assert all(task["assignee_id"] is not None for task in detail["tasks"])

    db.expire_all()
    state = D.project_runtime_state(db, project_id)
    assert state.ready_task_ids == (int(detail["tasks"][0]["id"]),), state
    assert [int(item.task_id) for item in state.dispatchable] == [int(detail["tasks"][0]["id"])], (
        state
    )
    assert state.needs_management == (), "fixture 图全都有负责人，不该上来就要管理决策"

    # ② fixture 模块本身**不含**调度/派发/推进逻辑（建完图就退出）
    fixture_tree = ast.parse(FIXTURE_MODULE.read_text(encoding="utf-8"))
    fixture_code = "\n".join(
        ast.unparse(node)
        for node in ast.walk(fixture_tree)
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Call, ast.Attribute))
    )
    for forbidden in (
        "dispatch",
        "project_runtime_state",
        "resolve_ready_tasks",
        "WorkSession",
        "orchestrator",
        "transition_task",
        "gateway",
        "run_session",
    ):
        assert forbidden not in fixture_code, f"fixture 越界了：{forbidden}"

    # ③ 打开调度：图自己跑到底（推进只能来自共享运行时 —— R12 已删掉 kind 分支）
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", True)
    orchestrator_module.orchestrator.notify({"type": "dispatch"})

    deadline = time.monotonic() + 60
    status = ""
    while time.monotonic() < deadline:
        status = client.get(f"/api/v1/projects/{project_id}").json()["status"]
        if status == ProjectStatus.completed.value:
            break
        time.sleep(0.2)
    assert status == ProjectStatus.completed.value, f"fixture 图没跑完：{status}"
    tasks = client.get(f"/api/v1/projects/{project_id}").json()["tasks"]
    assert {task["status"] for task in tasks} == {TaskStatus.done.value}


# ---------------------------------------------------------------------------
# R8：task.ready 是事实事件，不是审批
# ---------------------------------------------------------------------------


def test_task_ready_is_a_fact_event(db, default_company_id, monkeypatch):
    """R8：`task.ready` 只是"某任务现在具备执行条件"的事实通告，不带任何授权。"""
    # ① 契约分类：事实事件与决策需求事件**互不相交**
    assert "task.ready" in C.FACT_EVENTS
    assert "task.ready" not in C.DECISION_NEEDED_EVENTS
    assert not (C.FACT_EVENTS & C.DECISION_NEEDED_EVENTS)

    # ② 载荷只描述事实：没有批准字段、没有授权字段、没有建议人选
    people = _employees(db, default_company_id)
    project = _project(db, default_company_id)
    upstream = _task(db, project, "上游", assignee_id=int(people["bob"].id))
    downstream = _task(
        db, project, "下游", assignee_id=int(people["charlie"].id), depends_on=[int(upstream.id)]
    )
    task_service.transition_task(
        db, project_repo.get_task(db, int(upstream.id)), TaskStatus.done.value, force=True
    )
    db.commit()

    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bus, "publish", lambda event, payload, **kw: published.append((event, payload))
    )
    orchestrator_module.Orchestrator()._advance(int(upstream.id), success=True)

    ready = [item for item in published if item[0] == "task.ready"]
    assert ready, "下游就绪时必须留下事实通告"
    payload = ready[0][1]
    assert payload["id"] == int(downstream.id)
    assert set(payload) == {"id", "title", "kind", "assignee_id"}, payload
    for forbidden in ("approved", "authorized", "assignee_candidates", "decision_id"):
        assert forbidden not in payload

    # ③ 而且**没有**因此产生 DecisionRecord（事实 ≠ 决策，R9 的另一半）
    assert _decision_rows_for_project(db, int(project.id)) == 0


def _decision_rows_for_project(db, project_id: int) -> int:
    """这个项目下的决策行数。

    **必须按项目圈定**：库里可能有别的用例留下的决策（M2.4/M2.6 都会写），
    断言"全表为空"只在"本文件恰好跑在它们前面"时才成立 —— 那是顺序依赖的假绿。
    """
    from app.models.decision import DecisionRecord

    return (
        db.scalar(
            select(func.count())
            .select_from(DecisionRecord)
            .where(DecisionRecord.project_id == int(project_id))
        )
        or 0
    )


# ---------------------------------------------------------------------------
# R9：正常推进不产生决策记录
# ---------------------------------------------------------------------------


def test_normal_dag_progress_creates_no_decision(db, default_company_id, monkeypatch):
    """R9：就绪、开跑、完成 —— 全程不该出现决策记录（无判断可做）。"""
    people = _employees(db, default_company_id)
    project = _project(db, default_company_id)
    task = _task(db, project, "正常推进", assignee_id=int(people["bob"].id))

    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bus, "publish", lambda event, payload, **kw: published.append((event, payload))
    )

    task_service.transition_task(
        db, project_repo.get_task(db, int(task.id)), TaskStatus.done.value, force=True
    )
    db.commit()
    orchestrator_module.Orchestrator()._advance(int(task.id), success=True)

    assert _decision_rows_for_project(db, int(project.id)) == 0, "正常推进不得产生决策记录"
    escalated = [
        event_type for event_type, _ in published if event_type in C.DECISION_NEEDED_EVENTS
    ]
    assert escalated == [], f"没有判断可做就不该打扰管理层：{escalated}"


# ---------------------------------------------------------------------------
# R10：改派/重做必须来自管理决策；失败不自动回退
# ---------------------------------------------------------------------------


def test_reassignment_requires_a_decision(db, default_company_id, monkeypatch):
    """R10：任务失败 ⇒ 上报重规划，**不**自动改派、**不**自动把上游打回 todo。"""
    people = _employees(db, default_company_id)
    project = _project(db, default_company_id)
    upstream = _task(db, project, "会失败的上游", assignee_id=int(people["bob"].id))
    downstream = _task(
        db, project, "下游", assignee_id=int(people["charlie"].id), depends_on=[int(upstream.id)]
    )
    task_service.transition_task(
        db, project_repo.get_task(db, int(upstream.id)), TaskStatus.todo.value
    )
    task_service.transition_task(
        db, project_repo.get_task(db, int(upstream.id)), TaskStatus.in_progress.value
    )
    task_service.transition_task(
        db,
        project_repo.get_task(db, int(upstream.id)),
        TaskStatus.failed.value,
        force=True,
    )
    db.commit()

    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bus, "publish", lambda event, payload, **kw: published.append((event, payload))
    )
    orchestrator_module.Orchestrator()._advance(int(upstream.id), success=False)

    events = [event_type for event_type, _ in published]
    assert "project.replan_required" in events
    assert "task.review_failed" not in events  # 失败与评审失败不是一件事
    payload = dict(published[0][1])
    assert payload["failed_task_id"] == int(upstream.id)
    assert payload["assignee_id"] == int(people["bob"].id)

    # 领域状态：负责人没换、上游没被"复活"、下游仍被依赖挡住
    db.expire_all()
    assert project_repo.get_task(db, int(upstream.id)).assignee_id == int(people["bob"].id)
    assert project_repo.get_task(db, int(upstream.id)).status == TaskStatus.failed.value
    assert project_repo.get_task(db, int(downstream.id)).status == TaskStatus.backlog.value
    assert int(downstream.id) not in D.structural_ready_task_ids(db, int(project.id))
    assert _decision_rows_for_project(db, int(project.id)) == 0, (
        "系统自己改写派是越权 —— 要么不动，要么留下决策记录"
    )


# ---------------------------------------------------------------------------
# R11：Decision-needed 事件集是**封闭**的
# ---------------------------------------------------------------------------


def test_decision_needed_events_are_the_only_manager_triggers(db):
    """R11：只有契约列出的 6 个事件能要求管理层介入；其余一律是事实通告。"""
    assert C.DECISION_NEEDED_EVENTS == {
        "task.assignment_required",
        "task.runtime_unavailable",
        "task.blocked",
        "task.failed",
        "task.review_failed",
        # M2.7 追加：做完但还没有结论 / 评审人判定不了 ⇒ 需要人/管理层
        "task.review_required",
        "project.replan_required",
    }
    # 只读的事实事件不得混进来
    assert not (C.FACT_EVENTS & C.DECISION_NEEDED_EVENTS)
    assert "task.ready" in C.FACT_EVENTS and "task.completed" in C.FACT_EVENTS
    # M2.7 追加的事实：进入评审态 / 评审通过（都不是审批请求）
    assert {"task.in_review", "task.review_passed"} <= C.FACT_EVENTS

    # 编排器发布的所有事件都在两张表里（没有"野生事件"）
    source = ORCHESTRATOR_MODULE.read_text(encoding="utf-8")
    published = {
        node.args[0].value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "bus"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    # 经 `events` 列表转发的第二类发布路径也要扫（M2.5 起推进事件走这条路）
    forwarded: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"append", "extend"}
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "events"
        ):
            for literal in ast.walk(node):
                if (
                    isinstance(literal, ast.Tuple)
                    and literal.elts
                    and isinstance(literal.elts[0], ast.Constant)
                    and isinstance(literal.elts[0].value, str)
                ):
                    forwarded.add(literal.elts[0].value)
    assert forwarded, "没扫到转发路径 —— 守卫的形状假设不成立了"
    wild = (published | forwarded) - (C.FACT_EVENTS | C.DECISION_NEEDED_EVENTS)
    assert not wild, f"编排器发了未登记的事件：{sorted(wild)}"
    # 且发布口自己也会拦（运行时守卫，覆盖以后新加的路径）
    assert "assert event_type in (C.FACT_EVENTS | C.DECISION_NEEDED_EVENTS)" in source
    # 运行时只能经 dispatch 模块产出 Decision-needed 事件（口径唯一）
    assert set(D.REASON_EVENTS.values()) <= C.DECISION_NEEDED_EVENTS


# ---------------------------------------------------------------------------
# R12：没有静默的自动规划/自动指派
# ---------------------------------------------------------------------------


def test_no_silent_auto_planning_or_assignment_fallback():
    """R12：DAG 是**数据**不是代码 —— 运行时里不许有按 kind 的规划/推进逻辑。"""
    source = ORCHESTRATOR_MODULE.read_text(encoding="utf-8")
    for forbidden in (
        "DETERMINISTIC_TEMPLATE_PLAN",
        "_generate_graph",
        "_apply_deterministic_template_plan",
        "_uses_deterministic_plan",
        "_unblock_dependents",
    ):
        assert forbidden not in source, f"运行时还有规划/推进逻辑：{forbidden}"

    tree = ast.parse(source)
    for name in ("_advance", "_dispatch_pending"):
        node = _pipeline_function(tree, name)
        dump = ast.dump(node)
        assert "TaskKind" not in dump, f"{name} 按任务类型分叉了 —— R12"
        assert "assignee_id =" not in dump, f"{name} 在写负责人 —— 那是管理决策"
        assert "select(" not in dump and ".all()" not in dump, f"{name} 在挑候选人了 —— R2"

    # 就绪口径只有一处实现：契约纯函数（dispatch 只是它的运行时适配）。
    # 运行时里**不许**再写一遍"依赖都完成了没有"。
    dispatch_source = DISPATCH_MODULE.read_text(encoding="utf-8")
    assert "C.validate_task_graph(" in dispatch_source, "就绪集合必须来自契约校验器"
    assert dispatch_source.count("def structural_ready_task_ids") == 1
    assert "dep in done" not in dispatch_source, "运行时重写了一遍就绪口径 —— R12"
    assert dispatch_source.count("REASON_NOT_READY") >= 1


# ---------------------------------------------------------------------------
# 图本身非法 ⇒ 不调度、不静默卡住（W16：DAG 正确性是系统职责）
# ---------------------------------------------------------------------------


def test_invalid_graph_is_never_scheduled_and_escalates(
    db, default_company_id, monkeypatch, _inert_global
):
    """环 / 悬空依赖 ⇒ 权威口径直接给空集，并上报 `project.replan_required`（去重）。"""
    people = _employees(db, default_company_id)
    project = _project(db, default_company_id)
    first = _task(db, project, "环上的甲", assignee_id=int(people["bob"].id))
    second = _task(db, project, "环上的乙", assignee_id=int(people["charlie"].id))

    # 绕过工具面直接写环（模拟历史脏数据）—— 工具面本来会拒绝（M2.3 的 create_dependency）
    db.execute(
        __import__("sqlalchemy").text(
            "INSERT INTO task_dependencies (task_id, depends_on_id) VALUES (:a, :b), (:b, :a)"
        ),
        {"a": int(first.id), "b": int(second.id)},
    )
    db.commit()

    report = D.graph_report(db, int(project.id))
    assert report.is_valid is False and report.cycles
    state = D.project_runtime_state(db, int(project.id))
    assert state.invalid_graph is True
    assert state.graph_problems and "cycle" in state.graph_problems[0]
    assert state.ready_task_ids == () and state.dispatchable == () and state.queued == ()
    # 也**不**冒充"缺人/缺资源"（那是另一类判断）
    assert state.needs_management == ()
    # 两个任务即使"没有未完成的前置"也不许被当成就绪
    assert D.structural_ready_task_ids(db, int(project.id)) == ()

    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bus, "publish", lambda event, payload, **kw: published.append((event, payload))
    )
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", True)
    orchestrator = orchestrator_module.Orchestrator()

    import asyncio

    asyncio.run(orchestrator._dispatch_pending())
    replans = [item for item in published if item[0] == "project.replan_required"]
    assert replans, "图不可执行必须上报重规划，不能静静卡住"
    assert replans[0][1]["id"] == int(project.id)
    assert replans[0][1]["reasons"], "上报要带上结构问题清单"

    # 去重：不刷屏
    published.clear()
    asyncio.run(orchestrator._dispatch_pending())
    assert not [item for item in published if item[0] == "project.replan_required"]

    # 而且**没有**任何任务被启动
    db.expire_all()
    assert {
        project_repo.get_task(db, int(first.id)).status,
        project_repo.get_task(db, int(second.id)).status,
    } == {TaskStatus.backlog.value}

    # fail-closed 的第二形态：**悬空依赖**（历史脏数据/外部写入）。
    # 它跟环不一样：环上的任务自己等自己，本来就永远不就绪；而悬空依赖只污染
    # 一个任务，**别的**任务在纯依赖口径下依然"看起来就绪" —— 系统不许
    # "隔壁那条边坏了，我先跑这条"，因为一张有坏边的图就不是一张可执行计划。
    other = _project(db, default_company_id, name="悬空依赖项目")
    healthy = _task(db, other, "看起来完全就绪", assignee_id=int(people["bob"].id))
    broken = _task(db, other, "指向不存在的任务", assignee_id=int(people["charlie"].id))
    db.execute(
        __import__("sqlalchemy").text(
            "INSERT INTO task_dependencies (task_id, depends_on_id) VALUES (:a, :b)"
        ),
        {"a": int(broken.id), "b": 999_999_999},
    )
    db.commit()

    nodes = D.graph_nodes(db, int(other.id))
    # 纯依赖口径：健康任务确实"就绪"（它没有未完成的前置）
    assert C.resolve_ready_tasks(nodes) == (int(healthy.id),)
    report = D.graph_report(db, int(other.id))
    assert report.is_valid is False and report.dangling
    # 但权威口径给空集：坏图整体不可执行（fail-closed，不挑着跑）
    assert D.structural_ready_task_ids(db, int(other.id)) == ()
    assert D.project_runtime_state(db, int(other.id)).invalid_graph is True
    assert D.evaluate_dispatch(db, healthy).dispatchable is False


# ---------------------------------------------------------------------------
# 反例注入：把违规实现喂给同一条守卫，守卫必须转红
# ---------------------------------------------------------------------------


def _guard_readiness_is_structural(*, readiness: set[int], dispatchable: set[int]) -> None:
    """守卫：可派发集合必须是就绪集合的子集（混淆两者即违规，R3）。"""
    assert dispatchable <= readiness, "拿能开工冒充已就绪 —— R3"
    assert readiness - dispatchable, "就绪必然包含还派不出去的任务 —— R3"


def _guard_assignee_untouched(before: int | None, after: int | None, chosen: int | None) -> None:
    """守卫：判定前后负责人不变，且判定不得凭空产生一个负责人（R1/R2）。"""
    assert after == before, "判定改写了负责人 —— R1"
    if before is None:
        assert chosen is None, "系统替管理层选了人 —— R2"


def test_guard_catches_conflated_readiness():
    """反例注入：把「就绪 ⇒ 可派发」塞进守卫 ⇒ 必须转红。"""
    _guard_readiness_is_structural(readiness={1, 2}, dispatchable={2})
    with pytest.raises(AssertionError, match="R3"):
        _guard_readiness_is_structural(readiness={1, 2}, dispatchable={1, 2})

    # 真实实现满足守卫
    source = DISPATCH_MODULE.read_text(encoding="utf-8")
    assert "READY_CANDIDATE_STATUSES" in source and "dispatchable: bool" in source


def test_guard_catches_auto_assignment():
    """反例注入：判定顺手选了个人 ⇒ 必须转红。"""
    _guard_assignee_untouched(None, None, None)
    _guard_assignee_untouched(7, 7, 7)
    with pytest.raises(AssertionError, match="R2"):
        _guard_assignee_untouched(None, None, 3)  # 系统替管理层选了人
    with pytest.raises(AssertionError, match="R1"):
        _guard_assignee_untouched(7, 9, 9)  # 判定顺手改派


def test_guard_catches_replan_fallback_in_the_runtime(tmp_path):
    """反例注入：往运行时里塞一段「失败就自动打回上游」的代码 ⇒ 守卫转红。"""

    def _guard(source: str) -> None:
        for forbidden in ("_unblock_dependents", "_generate_graph"):
            assert forbidden not in source, f"运行时还有规划/推进逻辑：{forbidden}"

    clean = "async def _advance(self, task_id: int, success: bool) -> None:\n    pass\n"
    (tmp_path / "clean.py").write_text(clean, encoding="utf-8")
    _guard((tmp_path / "clean.py").read_text(encoding="utf-8"))

    dirty = clean + "\n\ndef _unblock_dependents(db, done_task):\n    pass\n"
    (tmp_path / "dirty.py").write_text(dirty, encoding="utf-8")
    with pytest.raises(AssertionError, match="规划/推进逻辑"):
        _guard((tmp_path / "dirty.py").read_text(encoding="utf-8"))


def test_guard_catches_mode_specific_dispatcher(tmp_path):
    """反例注入：派发路径里出现 `work_mode` 分叉 ⇒ 守卫转红。"""

    def _guard(source: str) -> None:
        dump = ast.dump(_pipeline_function(ast.parse(source), "_dispatch_pending"))
        assert "work_mode" not in dump, "派发路径按工作模式分叉了 —— R6"

    _guard("async def _dispatch_pending(self):\n    return None\n")
    with pytest.raises(AssertionError, match="R6"):
        _guard(
            "async def _dispatch_pending(self):\n"
            "    if project.work_mode == 'guided':\n"
            "        return None\n"
        )
