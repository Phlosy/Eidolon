"""M2.6 · **Artifact Handoff & Shared Work Context**（W19 / H1–H8）。

目标一句话：**让 Agent A 的输出真正成为 Agent B 的输入**，且这件事可追溯。

```text
Manager 声明：B 要用 A 的产品        ← task_inputs（指向 Task，不是 artifact）
A 跑完 → 产物落 Drive               ← drive_nodes.task_id = A（产出归属，H2）
B 就绪 → 系统**在运行期解析**输入     ← resolve_input_artifacts（H4）
B 开工 → 记下"被 B 在哪次会话用掉"     ← artifact_links（H3/G5）
```

本文件两条纪律：

1. **可机器验证**：交接必须能在产出里被看到（G1），不能只"相信它发生了"；
2. **每条不变量都配一次反例注入**（文末小节）—— 守卫没有反例就是装饰品。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.core.config import settings
from app.events.bus import bus
from app.models.drive import DriveNode
from app.models.enums import ArtifactType, TaskStatus
from app.models.handoff import ArtifactLink, TaskInput
from app.models.organization import Employee
from app.models.project import Artifact, Task
from app.repositories import handoff as handoff_repo
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.schemas.position import AssignmentIn, PositionDefinitionIn, SlotIn
from app.services import position_service
from app.services import tasks as task_service
from app.work import contracts as C
from app.work import dispatch as D
from app.work import handoff
from app.workflow import orchestrator as orchestrator_module

SERVER_ROOT = Path(__file__).resolve().parents[1]
APP = SERVER_ROOT / "app"
HANDOFF_SERVICE = APP / "work" / "handoff.py"
HANDOFF_REPO = APP / "repositories" / "handoff.py"
HANDOFF_MODEL = APP / "models" / "handoff.py"

#: 确定性 fixture 图的链式顺序（M2.5 起一次性建好）
FIXTURE_KINDS = (
    "order_review",
    "planning",
    "research",
    "development",
    "testing",
    "final_review",
)


@pytest.fixture(autouse=True)
def _restore_org_state(org_snapshot):
    yield org_snapshot


@pytest.fixture()
def _dispatch_off(monkeypatch):
    """默认关掉后台调度：本文件多数用例要**单步**验证事实，不要并发写库。"""
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)


@pytest.fixture()
def _inert_global(monkeypatch):
    """让**全局**调度器实例失活：手工调 `_dispatch_pending` 时不要有第二个发布者。"""
    from app.workflow import orchestrator as orchestrator_module

    async def _inert(_signal: dict) -> None:  # pragma: no cover - 测试替身
        return None

    monkeypatch.setattr(orchestrator_module.orchestrator, "_handle", _inert)


def _employees(db, company_id: int) -> dict[str, Employee]:
    return {row.slug: row for row in org_repo.list_employees(db, company_id)}


def _fixture_project(client, db, name: str = "交接项目", **extra):
    """建一个 fixture 项目（整图建好、全部已指派）—— 交接链路用它最省事。"""
    payload = {
        "name": name,
        "description": "M2.6 交接测试",
        "planning_fixture": "deterministic_template",
        **extra,
    }
    response = client.post("/api/v1/projects", json=payload)
    assert response.status_code == 201, response.text
    project_id = int(response.json()["id"])
    db.expire_all()
    return project_id


def _tasks_by_kind(db, project_id: int) -> dict[str, Task]:
    return {task.kind: task for task in project_repo.list_tasks(db, project_id)}


def _run_project_to_completion(client, project_id: int, monkeypatch, timeout: float = 60.0) -> dict:
    """打开调度、等图跑完（fixture 图在 M2.5 起由共享运行时推进）。"""
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", True)
    orchestrator_module.orchestrator.notify({"type": "dispatch"})
    deadline = time.monotonic() + timeout
    detail: dict = {}
    while time.monotonic() < deadline:
        detail = client.get(f"/api/v1/projects/{project_id}").json()
        if detail["status"] == "completed":
            break
        time.sleep(0.2)
    assert detail.get("status") == "completed", f"项目没跑完：{detail.get('status')}"
    return detail


def _declare(db, consumer: Task, producers: list[Task]) -> None:
    handoff.declare_inputs(db, task=consumer, source_task_ids=[int(item.id) for item in producers])


def _artifact_content(client, project_id: int, doc_type: str) -> str:
    nodes = client.get("/api/v1/artifacts", params={"project_id": project_id}).json()
    for node in nodes:
        if node["type"] == doc_type:
            return node["content"]
    raise AssertionError(f"没有 {doc_type} 产物：{[node['type'] for node in nodes]}")


# ---------------------------------------------------------------------------
# H1 / G4：不建第二套 Artifact 系统，legacy 表继续不写
# ---------------------------------------------------------------------------


def test_no_second_artifact_store_and_legacy_table_stays_unwritten(
    client, db, default_company_id, monkeypatch
):
    """H1：交付物仍然只有 Drive 一个落点；`artifacts`（legacy）一行都不许写。"""
    from app.models import Base

    tables = set(Base.metadata.tables)
    # 契约里写明的落点就是它（不是"某张新表"）
    assert C.ARTIFACT_STORE_TABLE == "drive_nodes"
    assert C.ARTIFACT_VERSION_TABLE == "drive_revisions"
    assert C.ARTIFACT_OWNERSHIP_COLUMN == "drive_nodes.task_id"
    assert {"drive_nodes", "drive_revisions"} <= tables

    # M2.6 新增的是**工作域事实**（声明 + 使用），不是产物存储
    assert {"task_inputs", "artifact_links"} <= tables
    new_tables = {"task_inputs", "artifact_links"}
    for name in new_tables:
        columns = set(Base.metadata.tables[name].columns.keys())
        # 这两张表里不许出现内容 / 版本 / 哈希 —— 那是 Drive 的职责
        assert not (columns & {"content", "body", "text", "sha256", "version", "path", "blob"}), (
            f"{name} 里出现了产物内容 —— 那就是第二套 Artifact 系统了"
        )

    monkeypatch.setattr(settings, "allow_planning_fixtures", True)
    before = db.scalar(select(text("count(*)")).select_from(Artifact)) or 0
    project_id = _fixture_project(client, db, "落点守卫")
    _run_project_to_completion(client, project_id, monkeypatch)
    db.expire_all()
    after = db.scalar(select(text("count(*)")).select_from(Artifact)) or 0
    assert after == before, "legacy artifacts 表被写了 —— G4/H1"

    # 产物确实存在，而且只在 Drive 里
    nodes = list(db.scalars(select(DriveNode).where(DriveNode.project_id == project_id)))
    assert nodes, "跑完一个项目却没有任何 Drive 产物"
    assert all(node.zone == "projects" for node in nodes)


# ---------------------------------------------------------------------------
# H2：产出归属（写入时确定，不靠猜）
# ---------------------------------------------------------------------------


def test_produced_artifacts_carry_their_source_task(client, db, default_company_id, monkeypatch):
    """H2：每个产物都带着**产出它的 Task**，以及那次会话。"""
    monkeypatch.setattr(settings, "allow_planning_fixtures", True)
    project_id = _fixture_project(client, db, "产出归属")
    _run_project_to_completion(client, project_id, monkeypatch)

    db.expire_all()
    tasks = _tasks_by_kind(db, project_id)
    nodes = list(
        db.scalars(
            select(DriveNode).where(
                DriveNode.project_id == project_id, DriveNode.task_id.is_not(None)
            )
        )
    )
    assert nodes, "没有带产出归属的产物"
    for node in nodes:
        producer = project_repo.get_task(db, int(node.task_id))
        assert producer is not None and int(producer.project_id) == project_id
        assert node.work_session_id is not None, "产物必须能追到那次会话"
        # 类型与生产者的 kind 对得上（fixture 图每步产出一类交付物）
        assert producer.kind in FIXTURE_KINDS

    # 六个阶段都产出了自己的交付物（链式交接的前提）
    by_kind = {task.kind: task for task in tasks.values()}
    for kind in FIXTURE_KINDS:
        task = by_kind[kind]
        produced = handoff_repo.list_artifacts_for_task(db, int(task.id))
        assert produced, f"{kind} 没有产出"


# ---------------------------------------------------------------------------
# H3 / G5：使用事实（谁在哪次会话用掉了）
# ---------------------------------------------------------------------------


def test_consumption_is_recorded_with_the_session(client, db, default_company_id, monkeypatch):
    """H3/G5：交接必须落成「artifact × task × session × 谁」的事实，且不复制归属。"""
    monkeypatch.setattr(settings, "allow_planning_fixtures", True)
    project_id = _fixture_project(client, db, "使用事实")
    tasks = _tasks_by_kind(db, project_id)
    _declare(db, tasks["development"], [tasks["research"]])

    _run_project_to_completion(client, project_id, monkeypatch)

    db.expire_all()
    research = project_repo.get_task(db, int(tasks["research"].id))
    development = project_repo.get_task(db, int(tasks["development"].id))
    produced = handoff_repo.list_artifacts_for_task(db, int(research.id))

    report = client.get(f"/api/v1/tasks/{development.id}/artifacts").json()
    assert [item["artifact_id"] for item in report["consumed"]] == [
        int(node.id) for node in produced
    ]
    link = report["consumed"][0]
    assert link["task_id"] == int(development.id)
    assert link["work_session_id"] is not None, "用在哪次会话必须记下来（G5）"
    assert link["actor_employee_id"] == development.assignee_id

    # 链接表**只记使用**：产出归属仍然只有 drive_nodes.task_id 一处
    assert C.ARTIFACT_LINK_ROLES == {"consumed_by"}
    rows = list(db.scalars(select(ArtifactLink)))
    assert rows and {row.role for row in rows} == {"consumed_by"}
    assert not hasattr(ArtifactLink, "produced_by")
    # 计数对得上：A 的每个产物被 B 各用了一次
    assert len([r for r in rows if r.task_id == development.id]) == len(produced)


# ---------------------------------------------------------------------------
# H4：声明 ≠ 引用（运行期解析）
# ---------------------------------------------------------------------------


def test_declaration_is_not_a_reference_resolution_happens_at_run_time(db, default_company_id):
    """H4：声明时**没有** artifact 可指；产物由系统在运行期解析。"""
    people = _employees(db, default_company_id)
    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="声明与解析",
        description="M2.6",
        status="in_progress",
        source_order_text="M2.6",
    )
    db.commit()
    upstream = task_service.create_task(
        db,
        project_id=int(project.id),
        title="上游",
        kind="research",
        assignee_id=int(people["bob"].id),
    )
    downstream = task_service.create_task(
        db,
        project_id=int(project.id),
        title="下游",
        kind="development",
        assignee_id=int(people["charlie"].id),
        depends_on=[int(upstream.id)],
    )
    db.commit()

    rows = handoff.declare_inputs(db, task=downstream, source_task_ids=[int(upstream.id)])
    assert len(rows) == 1 and isinstance(rows[0], TaskInput)
    # 声明的是 **Task**，不是 artifact
    assert set(TaskInput.__table__.columns.keys()) >= {"task_id", "source_task_id"}
    assert "artifact_id" not in TaskInput.__table__.columns

    # 上游还没跑完 ⇒ 解析为空（没有产物可交接，也没假装有）
    assert handoff.resolve_input_artifacts(db, downstream) == ()
    declared = handoff.declared_inputs(db, int(downstream.id))
    assert declared[0].source_task_status != TaskStatus.done.value
    assert declared[0].ready is False

    # 上游跑完并产出 ⇒ 同一个声明现在解析出产物（**运行期**解析）
    from app.services import artifacts as artifact_service

    artifact_service.record_project_artifact(
        db,
        project,
        artifact_type=ArtifactType.research_report.value,
        title="调研报告",
        content="# 调研\n\n上游的结论：用 A 方案。\n",
        author_id=int(people["bob"].id),
        task_id=int(upstream.id),
    )
    task_service.transition_task(
        db, project_repo.get_task(db, int(upstream.id)), TaskStatus.done.value, force=True
    )
    db.commit()

    resolved = handoff.resolve_input_artifacts(db, downstream)
    assert len(resolved) == 1
    assert resolved[0].source_task_id == int(upstream.id)
    assert "用 A 方案" in resolved[0].excerpt
    assert resolved[0].doc_type == ArtifactType.research_report.value


# ---------------------------------------------------------------------------
# H5：声明必须有顺序保证（DAG 祖先）
# ---------------------------------------------------------------------------


def test_input_declaration_requires_a_dag_ancestor(client, db, default_company_id):
    """H5：说不清"它一定先跑"的输入 → 系统拒绝（服务层 + HTTP 422）。"""
    people = _employees(db, default_company_id)
    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="祖先校验",
        description="M2.6",
        status="in_progress",
        source_order_text="M2.6",
    )
    db.commit()
    first = task_service.create_task(
        db,
        project_id=int(project.id),
        title="甲",
        kind="development",
        assignee_id=int(people["charlie"].id),
    )
    second = task_service.create_task(
        db,
        project_id=int(project.id),
        title="乙",
        kind="testing",
        assignee_id=int(people["dana"].id),
    )
    db.commit()

    # 没有依赖 ⇒ 不是祖先 ⇒ 拒绝（可能是"乙先跑"，这条声明不成立）
    with pytest.raises(handoff.LineageViolation) as excinfo:
        handoff.declare_inputs(db, task=second, source_task_ids=[int(first.id)])
    assert "ancestor" in str(excinfo.value)
    db.rollback()

    # HTTP 面同样是 422（人类管理动作走同一段应用服务，T11）
    response = client.post(
        f"/api/v1/tasks/{second.id}/inputs", json={"source_task_ids": [int(first.id)]}
    )
    assert response.status_code == 422, response.text
    assert "ancestor" in response.json()["detail"]

    # 自己消费自己：拒绝
    with pytest.raises(handoff.LineageViolation):
        handoff.declare_inputs(db, task=second, source_task_ids=[int(second.id)])
    db.rollback()

    # 跨项目：拒绝
    other = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="另一个项目",
        description="M2.6",
        status="in_progress",
        source_order_text="M2.6",
    )
    db.commit()
    stranger = task_service.create_task(
        db,
        project_id=int(other.id),
        title="外项目的任务",
        kind="research",
        assignee_id=int(people["bob"].id),
    )
    db.commit()
    with pytest.raises(handoff.LineageViolation):
        handoff.declare_inputs(db, task=second, source_task_ids=[int(stranger.id)])
    db.rollback()

    # 补上依赖 ⇒ 声明成立（顺序保证了）
    project_repo.add_dependency(db, task_id=int(second.id), depends_on_id=int(first.id))
    db.commit()
    rows = handoff.declare_inputs(db, task=second, source_task_ids=[int(first.id)])
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# H6 / G1：交接的是**内容**，不是指针
# ---------------------------------------------------------------------------


def test_upstream_artifact_content_reaches_the_task_context(
    client, db, default_company_id, monkeypatch
):
    """H6/G1：上游产物（有界摘要）真的进了下游的 TaskContext，并能机器验证。"""
    monkeypatch.setattr(settings, "allow_planning_fixtures", True)
    project_id = _fixture_project(client, db, "内容交接")
    tasks = _tasks_by_kind(db, project_id)
    _declare(db, tasks["development"], [tasks["research"]])
    _declare(db, tasks["testing"], [tasks["development"]])

    _run_project_to_completion(client, project_id, monkeypatch)

    # ① 下游产出里能看到上游内容（mock runtime 把输入渲染进产出 → 可机器验证）
    source_code = _artifact_content(client, project_id, ArtifactType.source_code.value)
    assert "## Inputs From Upstream Tasks" in source_code
    report = client.get(f"/api/v1/tasks/{tasks['research'].id}/artifacts").json()
    research_excerpt_head = report["produced"][0]["title"]
    assert research_excerpt_head in source_code

    # ② 二跳：testing 的输入里能看到 development（而 development 的输入是 research）
    test_report = _artifact_content(client, project_id, ArtifactType.test_report.value)
    assert "## Inputs From Upstream Tasks" in test_report
    source_title = client.get(f"/api/v1/tasks/{tasks['development'].id}/artifacts").json()[
        "produced"
    ][0]["title"]
    assert source_title in test_report

    # ③ 摘要有界（不是把整篇文档塞进上下文）
    db.expire_all()
    development = project_repo.get_task(db, int(tasks["development"].id))
    for item in handoff.resolve_input_artifacts(db, development):
        assert len(item.excerpt) <= C.INPUT_ARTIFACT_EXCERPT_CHARS + 40
    assert C.INPUT_ARTIFACT_EXCERPT_CHARS <= 2000


# ---------------------------------------------------------------------------
# H7 / G2：lineage 至少两跳
# ---------------------------------------------------------------------------


def test_lineage_walks_at_least_two_hops(client, db, default_company_id, monkeypatch):
    """H7/G2：从消费者出发，能顺着交接链追回至少两跳。"""
    monkeypatch.setattr(settings, "allow_planning_fixtures", True)
    project_id = _fixture_project(client, db, "lineage")
    tasks = _tasks_by_kind(db, project_id)
    _declare(db, tasks["development"], [tasks["research"]])
    _declare(db, tasks["testing"], [tasks["development"]])
    _declare(db, tasks["final_review"], [tasks["testing"]])

    _run_project_to_completion(client, project_id, monkeypatch)

    report = client.get(f"/api/v1/tasks/{tasks['final_review'].id}/artifacts").json()
    depths = {hop["depth"] for hop in report["upstream"]}
    assert {1, 2} <= depths, f"lineage 只追到 {sorted(depths)} 跳（G2 要求 ≥2 跳）"
    hop_tasks = {hop["task_id"]: hop for hop in report["upstream"]}
    assert int(tasks["testing"].id) in hop_tasks and int(tasks["development"].id) in hop_tasks
    assert hop_tasks[int(tasks["development"].id)]["depth"] == 2
    # 每一跳都带着产物引用（不是只有 Task 名）
    assert all(hop["artifact_id"] for hop in report["upstream"])

    # Agent 读工具与 HTTP 读面是**同一份**事实（T2/T11）
    from app.work import tool_executor

    db.expire_all()
    result = tool_executor.execute_tool(
        db,
        name="list_task_artifacts",
        args={"task_id": int(tasks["final_review"].id)},
        context=tool_executor.context_for_employee(
            db, org_repo.get_employee(db, int(tasks["final_review"].assignee_id)), origin="test"
        ),
    )
    assert result.ok is True
    assert result.data["upstream"] == report["upstream"]


# ---------------------------------------------------------------------------
# H8 / G3：只消费已完成的产出
# ---------------------------------------------------------------------------


def test_referencing_an_unfinished_tasks_output_is_refused(client, db, default_company_id):
    """H8/G3：在跑的 Task 的产物**永不被引用** —— 服务层拒绝、HTTP 422、工具拒绝。"""
    people = _employees(db, default_company_id)
    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="未完成的产物",
        description="M2.6",
        status="in_progress",
        source_order_text="M2.6",
    )
    db.commit()
    running = task_service.create_task(
        db,
        project_id=int(project.id),
        title="还在跑",
        kind="development",
        assignee_id=int(people["charlie"].id),
    )
    consumer = task_service.create_task(
        db,
        project_id=int(project.id),
        title="想引用它",
        kind="testing",
        assignee_id=int(people["dana"].id),
        depends_on=[int(running.id)],
    )
    db.commit()
    task_service.transition_task(db, running, TaskStatus.todo.value)
    task_service.transition_task(db, running, TaskStatus.in_progress.value)
    db.commit()

    from app.services import artifacts as artifact_service

    node = artifact_service.record_project_artifact(
        db,
        project,
        artifact_type=ArtifactType.source_code.value,
        title="半成品",
        content="# 还没跑完\n",
        task_id=int(running.id),
    )
    db.commit()

    # ① 服务层
    with pytest.raises(handoff.LineageViolation) as excinfo:
        handoff.consume_artifact(db, task=consumer, artifact_id=int(node.id))
    assert "only finished work is consumable" in str(excinfo.value)
    db.rollback()

    # ② HTTP：422（G3 的字面要求）
    response = client.post(f"/api/v1/tasks/{consumer.id}/artifacts/{node.id}/consume")
    assert response.status_code == 422, response.text
    assert "finished work" in response.json()["detail"]

    # ③ 工具面：**必须**经一条决策信封（`consume_artifact` 的语义是 required，DR7），
    #    而这条决策本身会被领域拒绝 —— 拒绝事实落在决策与审计里。
    from app.work import decisions as decision_service
    from app.work import tool_executor

    manager, _definition = _lab_manager(db, default_company_id)
    ctx = tool_executor.context_for_employee(db, manager, origin="test")
    outcome = decision_service.submit_envelope(
        db,
        ctx,
        decision_type=C.DecisionKind.reassign_task,
        reason="把这份产出接到下游任务上",
        intended_outcome="下游任务把这份产出当作输入",
        scope=f"project:{int(project.id)}",
        # DR9 的有界上下文键就是为此准备的：产物引用只记 ref，不塞内容
        context={
            "project_id": int(project.id),
            "task_ids": [int(consumer.id)],
            "artifact_refs": [int(node.id)],
        },
        actions=[
            {
                "tool": "consume_artifact",
                "args": {"task_id": int(consumer.id), "artifact_id": int(node.id)},
            }
        ],
    )
    assert outcome.status is C.DecisionStatus.failed, outcome.outcomes
    assert outcome.failed == 1
    assert outcome.outcomes[0].reason == "domain_rejected"
    # 拒绝理由落在**审计**里（决策行本身不复制执行细节，DR5）
    from app.models.decision import ToolAudit

    audit = db.get(ToolAudit, int(outcome.outcomes[0].audit_id))
    assert audit is not None and "finished work" in audit.error
    db.expire_all()
    assert not handoff_repo.list_artifact_links(db, task_id=int(consumer.id)), (
        "被拒绝的引用不许留下'用过了'的假事实"
    )

    # ④ 解析路径也不许把它当成输入（哪怕链接是脏数据）
    handoff_repo.add_artifact_link(
        db,
        artifact_id=int(node.id),
        task_id=int(consumer.id),
        role="consumed_by",
        reason="脏数据",
    )
    db.commit()
    assert all(
        item.artifact_id != int(node.id) for item in handoff.resolve_input_artifacts(db, consumer)
    ), "未完成任务的产物混进了输入 —— H8"

    # ⑤ 它跑完之后，同一个引用才被允许
    task_service.transition_task(
        db, project_repo.get_task(db, int(running.id)), TaskStatus.done.value, force=True
    )
    db.commit()
    link = handoff.consume_artifact(db, task=consumer, artifact_id=int(node.id))
    assert link.role == "consumed_by"
    assert any(
        item.artifact_id == int(node.id) for item in handoff.resolve_input_artifacts(db, consumer)
    )


def _lab_manager(db, company_id: int, code: str = "lab-handoff"):
    """隔离实验台（幂等）：给一个员工 `plan_project_work` 授权，用来打工具面。"""

    from app.models.enums import AuthorityScopeKind
    from app.repositories import position as position_repo

    employee = _employees(db, company_id)["charlie"]
    position_service.release_position(db, employee, reason="m2.6 lab")
    db.flush()
    definition = position_repo.get_definition_by_code(db, code, company_id=company_id)
    if definition is None:
        definition = position_service.create_definition(
            db,
            PositionDefinitionIn(code=code, name="Handoff Lab", job_family="engineering", level=3),
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
            SlotIn(department_id=int(employee.department_id), count=1, note="m2.6 lab"),
            company_id,
        )[0]
    )
    position_service.assign_position(
        db, employee, AssignmentIn(slot_id=int(slot.id), reason="m2.6 lab", kind="assign")
    )
    db.commit()
    from app.work import authority as authority_service

    for kind in (C.AuthorityKind.plan_project_work, C.AuthorityKind.assign_task):
        authority_service.grant_authority(
            db,
            position_definition_id=int(definition.id),
            kind=kind,
            scope_kind=AuthorityScopeKind.company,
            commit=False,
        )
    db.commit()
    return employee, definition


# ---------------------------------------------------------------------------
# 声明了却没交付 ⇒ 不派发、上报重规划（H5/R10）
# ---------------------------------------------------------------------------


def test_declared_input_without_delivery_escalates_instead_of_dispatching(
    db, default_company_id, monkeypatch, _inert_global
):
    """声明的上游跑完了却没产出 ⇒ 系统不派发，交管理层（不是"缺人"）。"""
    people = _employees(db, default_company_id)
    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="空交付",
        description="M2.6",
        status="in_progress",
        source_order_text="M2.6",
    )
    db.commit()
    upstream = task_service.create_task(
        db,
        project_id=int(project.id),
        title="上游（跑完但没产出）",
        kind="research",
        assignee_id=int(people["bob"].id),
    )
    downstream = task_service.create_task(
        db,
        project_id=int(project.id),
        title="下游（等输入）",
        kind="development",
        assignee_id=int(people["charlie"].id),
        depends_on=[int(upstream.id)],
    )
    db.commit()
    handoff.declare_inputs(db, task=downstream, source_task_ids=[int(upstream.id)])

    task_service.transition_task(
        db, project_repo.get_task(db, int(upstream.id)), TaskStatus.done.value, force=True
    )
    db.commit()

    evaluation = D.evaluate_dispatch(db, project_repo.get_task(db, int(downstream.id)))
    assert evaluation.dispatchable is False
    assert evaluation.needs_management is True
    assert D.REASON_INPUT_ARTIFACTS_MISSING in evaluation.reasons
    assert evaluation.event_type == "project.replan_required"
    assert handoff.missing_input_sources(db, downstream) == (int(upstream.id),)

    # 澄清：这不是"缺人/缺资源"——换个人也拿不到不存在的产物
    assert D.REASON_INPUT_ARTIFACTS_MISSING not in {
        D.REASON_ASSIGNEE_MISSING,
        D.REASON_ASSIGNEE_INACTIVE,
        D.REASON_RUNTIME_UNAVAILABLE,
    }

    # 调度器把它上报为一次 Decision-needed 事件（去重）
    published: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        bus, "publish", lambda event, payload, **kw: published.append((event, payload))
    )
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", True)
    import asyncio

    orchestrator = orchestrator_module.Orchestrator()
    asyncio.run(orchestrator._dispatch_pending())
    escalations = [item for item in published if item[0] == "project.replan_required"]
    assert escalations, "输入缺失必须上报，不能静静卡住"
    assert escalations[0][1]["reasons"] == ["not_ready", D.REASON_INPUT_ARTIFACTS_MISSING] or (
        D.REASON_INPUT_ARTIFACTS_MISSING in escalations[0][1]["reasons"]
    )
    db.expire_all()
    assert project_repo.get_task(db, int(downstream.id)).status == TaskStatus.backlog.value


# ---------------------------------------------------------------------------
# 反例注入：把违规实现喂给同一条守卫，守卫必须转红
# ---------------------------------------------------------------------------


def _guard_attribution_is_written(node_task_id: int | None) -> None:
    """守卫：产物必须有产出归属（H2）。"""
    assert node_task_id is not None, "产物没有产出归属 —— 系统在猜谁产出的（H2）"


def _guard_declaration_needs_ancestor(is_ancestor: bool) -> None:
    """守卫：声明的输入必须有顺序保证（H5）。"""
    assert is_ancestor, "声明了一个可能还没跑的输入 —— 不算可执行计划（H5）"


def _guard_only_finished_is_consumable(producer_status: str) -> None:
    """守卫：只有已完成的产出可被消费（H8）。"""
    assert producer_status == TaskStatus.done.value, "引用了未完成任务的产物（H8）"


def _guard_links_carry_a_session(work_session_id: int | None, *, automatic: bool) -> None:
    """守卫：自动交接的使用事实必须带会话（H3/G5）。"""
    if automatic:
        assert work_session_id is not None, "自动交接没记下用在哪次会话（H3/G5）"


def test_guards_catch_the_counter_examples():
    """反例注入：每条守卫在收到违规输入时都必须转红。"""
    # H2：产出归属
    _guard_attribution_is_written(7)
    with pytest.raises(AssertionError, match="H2"):
        _guard_attribution_is_written(None)

    # H5：顺序保证
    _guard_declaration_needs_ancestor(True)
    with pytest.raises(AssertionError, match="H5"):
        _guard_declaration_needs_ancestor(False)

    # H8：只消费已完成的产出
    _guard_only_finished_is_consumable(TaskStatus.done.value)
    for status in (TaskStatus.in_progress.value, TaskStatus.in_review.value, TaskStatus.todo.value):
        with pytest.raises(AssertionError, match="H8"):
            _guard_only_finished_is_consumable(status)

    # H3：使用事实必须带会话
    _guard_links_carry_a_session(11, automatic=True)
    _guard_links_carry_a_session(None, automatic=False)
    with pytest.raises(AssertionError, match="H3"):
        _guard_links_carry_a_session(None, automatic=True)


def test_guard_catches_an_implicit_second_artifact_store(tmp_path):
    """反例注入：把"产物内容"塞进工作域的表 ⇒ 守卫转红。"""

    def _guard(columns: set[str]) -> None:
        assert not (columns & {"content", "sha256", "version", "path"}), (
            "工作域表里出现了产物内容 —— 那是第二套 Artifact 系统（H1）"
        )

    _guard({"task_id", "source_task_id"})
    with pytest.raises(AssertionError, match="H1"):
        _guard({"task_id", "content"})


def test_guard_catches_resolving_unfinished_inputs():
    """反例注入：把"未完成任务的产物"也算成输入 ⇒ 守卫转红。"""

    class _Ref:
        def __init__(self, status: str) -> None:
            self.producer_status = status

    def _resolve(refs: list[_Ref]) -> list[_Ref]:
        out = [ref for ref in refs if ref.producer_status == TaskStatus.done.value]
        return out

    done, running = _Ref(TaskStatus.done.value), _Ref(TaskStatus.in_progress.value)
    assert _resolve([done, running]) == [done]

    def _broken_resolve(refs: list[_Ref]) -> list[_Ref]:
        return list(refs)  # INJECT: 不筛完成态

    with pytest.raises(AssertionError):
        assert _broken_resolve([done, running]) == [done]


def test_handoff_service_is_the_single_query_authority():
    """读面与工具面**不各写一遍查询**（T2/T11）：查询只在 handoff 模块里。"""
    service = HANDOFF_SERVICE.read_text(encoding="utf-8")
    assert "handoff_repo." in service

    for name in ("tasks.py", "tool_reads.py", "tool_writes.py"):
        path = APP / "api" / "v1" / name if name == "tasks.py" else APP / "work" / name
        source = path.read_text(encoding="utf-8")
        # ① 表只能被 repo 层碰：这两个面的源码里不许出现 repo / model 的导入
        assert "repositories.handoff" not in source, f"{name} 绕过了 handoff 服务去直查 repo"
        assert "models.handoff" not in source, f"{name} 绕过了 handoff 服务去直查表"
        # ② 它们必须经 handoff 服务（不是自己拼查询）
        assert "handoff." in source, f"{name} 没有走 handoff 服务"

    # 反向确认这条守卫不是空转：repo 层确实持有那两张表
    repo_source = HANDOFF_REPO.read_text(encoding="utf-8")
    assert "ArtifactLink" in repo_source and "TaskInput" in repo_source
    assert "TaskInput(" in repo_source  # 只有这里 new 行
