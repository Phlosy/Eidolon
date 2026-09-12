"""M2.8 · **Recruit → Ready-to-Work**（W31 / RD1–RD7）。

一句话：招募/购买得到的人**能立刻执行 Agent Task**。

```text
招募 → Employee → PositionAssignment → RoleContext
     → 环境编排（工作区目录 / 运行时实例 / 供应商绑定）
     → READY_TO_WORK（**派生量**，不是一列）
```

三条纪律：**派生不落库**（RD1）、**逐项可解释**（RD2）、
**失败不算就绪**（RD5）—— 系统绝不把"没配好"四舍五入成"可以干活了"。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.enums import LifecycleStatus, RuntimeType
from app.models.knowledge import Skill
from app.models.organization import Employee
from app.models.person import Person
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.repositories import runtimes as runtime_repo
from app.services import tasks as task_service
from app.work import contracts as C
from app.work import dispatch as D
from app.work import readiness

SERVER_ROOT = Path(__file__).resolve().parents[1]
APP = SERVER_ROOT / "app"
READINESS_MODULE = APP / "work" / "readiness.py"
RECRUITMENT = APP / "services" / "recruitment.py"


@pytest.fixture(autouse=True)
def _restore_org_state(org_snapshot):
    yield org_snapshot


@pytest.fixture(autouse=True)
def _no_background_dispatch(monkeypatch):
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)


def _employees(db, company_id: int) -> dict[str, Employee]:
    return {row.slug: row for row in org_repo.list_employees(db, company_id)}


def _grant_real_runtime(db, company_id: int, runtime_type: str = RuntimeType.claude_code.value):
    """把公司策略配成"真实运行时 + 有供应商"（测试用的最小可信环境）。"""
    from app.repositories import providers as provider_repo

    provider = provider_repo.create_provider(
        db,
        company_id=int(company_id),
        name="M2.8 Test Provider",
        provider_type="openai_compatible",
        enabled=True,
    )
    db.commit()
    readiness.set_company_runtime_defaults(
        db,
        company_id,
        {"runtime_type": runtime_type, "provider_id": int(provider.id), "model": "test-model"},
    )
    return provider


def _hire(db, company_id: int, slug: str, *, workspace: str | None = None) -> Employee:
    """直接建一个员工（招募路径之外的"新人"），用来做就绪与编排的单元验证。"""
    person = Person(slug=f"p-{slug}", name=f"Person {slug}")
    db.add(person)
    db.flush()
    employee = org_repo.create_employee(
        db,
        person_id=int(person.id),
        company_id=int(company_id),
        department_id=None,
        name=f"Person {slug}",
        slug=slug,
        role="engineer",
        title="Engineer",
        status="idle",
        lifecycle_status=LifecycleStatus.active.value,
        runtime_type=RuntimeType.mock.value,
        runtime_config={},
        workspace_path=workspace or f"{settings.workspace_root}/{slug}",
        memory_namespace=f"emp_{slug}",
    )
    db.commit()
    return employee


# ---------------------------------------------------------------------------
# RD1 / I3：READY_TO_WORK 是派生的
# ---------------------------------------------------------------------------


def test_ready_to_work_is_derived_not_stored(db, default_company_id):
    """RD1（= I3）：没有 `ready_to_work` 列、没有 `READY_TO_WORK` 枚举值。"""
    from app.models import Base

    employee_columns = set(Base.metadata.tables["employees"].columns.keys())
    for forbidden in ("ready_to_work", "is_ready", "readiness", "ready_at"):
        assert forbidden not in employee_columns, f"employees 表里出现了就绪列：{forbidden}"

    # 生命周期枚举里也没有 READY_TO_WORK（它是派生量，不是状态）
    lifecycle_values = {item.value for item in LifecycleStatus}
    assert "ready_to_work" not in lifecycle_values
    assert "READY_TO_WORK" not in lifecycle_values

    # 模块里不许把就绪写回任何列（只读事实 + 派生结论）
    source = READINESS_MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute) and target.attr == "ready_to_work"
            for target in node.targets
        )
    ]
    assert not assignments, "就绪被写进了某个对象 —— 它必须是派生量（RD1）"
    assert C.READINESS_ITEMS  # 四项事实在契约里登记


# ---------------------------------------------------------------------------
# RD2：逐项可解释
# ---------------------------------------------------------------------------


def test_readiness_is_per_item_and_names_the_gaps(client, db, default_company_id):
    """RD2：四项事实逐项可核对；未就绪时**一定**说得出差哪一项。"""
    employee = _hire(db, default_company_id, "ready-fresh")
    report = readiness.readiness_report(db, employee)
    assert tuple(item.item for item in report.items) == C.READINESS_ITEMS
    for item in report.items:
        assert item.source and item.source == C.READINESS_FACT_SOURCES[item.item]
        assert item.detail, "每一项都必须有一句可核对的事实说明"
        assert isinstance(item.facts, dict)

    # 新员工：mock 运行时 ⇒ 运行时就绪**不需要实例**（事实），工作区缺目录 ⇒ 有缺口
    items = {item.item: item for item in report.items}
    assert items["runtime"].ready is True and items["runtime"].facts["required"] is False
    assert items["workspace"].ready is False and "不存在" in items["workspace"].detail
    assert report.ready_to_work is False
    assert "workspace" in report.gaps

    # HTTP 读面与工具面是**同一份**事实
    body = client.get(f"/api/v1/employees/{employee.id}/readiness").json()
    assert body["ready_to_work"] is False
    assert [item["item"] for item in body["items"]] == list(C.READINESS_ITEMS)
    assert body["gaps"] == list(report.gaps)
    assert body["policy"]["source"] == C.RUNTIME_POLICY_SOURCES["company_default"]

    # 配好工作区 ⇒ 只剩 position 一项（这个新人没任职）：
    # `position` 是**软契约**（W5），它在 READY_TO_WORK 里，但**不在执行门禁里**
    Path(employee.workspace_path).mkdir(parents=True, exist_ok=True)
    db.expire_all()
    refreshed = readiness.readiness_report(db, org_repo.get_employee(db, int(employee.id)))
    assert refreshed.gaps == ("position",)
    assert refreshed.ready_to_work is False
    assert readiness.blocking_gaps(db, employee) == (), "mock 下执行门禁不该被 position 挡住"


# ---------------------------------------------------------------------------
# RD3 / I1：未就绪的人不会被派活；就绪的人能真的跑完一个会话
# ---------------------------------------------------------------------------


def test_not_ready_agent_is_never_dispatched(client, db, default_company_id):
    """RD3（= I1 的另一半）：执行环境没配齐 ⇒ 不派发，交管理层。"""
    # 真实运行时 + 工作区不存在 ⇒ 未就绪
    employee = _hire(db, default_company_id, "ready-real", workspace="/tmp/definitely-missing-ws")
    employee.runtime_type = RuntimeType.claude_code.value
    db.commit()

    project = project_repo.create_project(
        db,
        company_id=default_company_id,
        name="未就绪的人",
        description="M2.8",
        status="in_progress",
        source_order_text="M2.8",
    )
    db.commit()
    task = task_service.create_task(
        db,
        project_id=int(project.id),
        title="给没配好的人派活",
        kind="development",
        assignee_id=int(employee.id),
    )
    db.commit()

    db.expire_all()
    evaluation = D.evaluate_dispatch(db, project_repo.get_task(db, int(task.id)))
    assert evaluation.dispatchable is False
    assert evaluation.needs_management is True
    assert D.REASON_ASSIGNEE_NOT_READY in evaluation.reasons
    assert evaluation.event_type == "task.runtime_unavailable"

    # 配齐之后（公司策略给供应商 + 编排一次）⇒ 环境事实齐备，门禁放行
    _grant_real_runtime(db, default_company_id)
    result = readiness.provision_employee(db, employee)
    assert result.ok is True, result.steps
    db.expire_all()
    assert readiness.blocking_gaps(db, employee) == ()
    evaluation = D.evaluate_dispatch(db, project_repo.get_task(db, int(task.id)))
    assert evaluation.dispatchable is True, evaluation.as_dict()

    # 而**就绪的人真的能被派活**（I1）用的是 mock 运行时员工：
    # 真实运行时在测试环境里没有可用的执行后端，拉起来只会证明"环境没装"，
    # 不能证明"就绪的人能干活"。两条事实分开验证，谁都不替谁背书。
    mock_employee = _employees(db, default_company_id)["charlie"]
    mock_task = task_service.create_task(
        db,
        project_id=int(project.id),
        title="就绪的人可以被派活",
        kind="development",
        assignee_id=int(mock_employee.id),
    )
    db.commit()
    Path(mock_employee.workspace_path).mkdir(parents=True, exist_ok=True)
    db.commit()
    mock_report = readiness.readiness_report(db, mock_employee)
    # 执行门禁：mock 运行时下不要求工作区/实例/供应商 ⇒ 空缺口
    assert readiness.blocking_gaps(db, mock_employee) == ()
    # READY_TO_WORK（报告口径）额外包含软契约项 position —— 种子员工的任职事实
    # 由数据决定，所以这里只断言"门禁通过 + 逐项事实齐备"，不假设某个具体人的编制。
    assert {item.item for item in mock_report.items} == set(C.READINESS_ITEMS)
    assert mock_report.ready_to_work == (mock_report.gaps == ())
    db.expire_all()
    evaluation = D.evaluate_dispatch(db, project_repo.get_task(db, int(mock_task.id)))
    assert evaluation.dispatchable is True, evaluation.as_dict()


# ---------------------------------------------------------------------------
# RD4 / I6：策略只配环境
# ---------------------------------------------------------------------------


def test_runtime_policy_is_environment_only(client, db, default_company_id):
    """RD4（= I6）：公司策略只配环境；人格 / 提示词 / 工作流键一律拒绝。"""
    assert set(C.RUNTIME_POLICY_KEYS) == {
        "runtime_type",
        "deployment_mode",
        "provider_id",
        "model",
        "runtime_config",
    }
    assert not (set(C.RUNTIME_POLICY_KEYS) & C.FORBIDDEN_RUNTIME_POLICY_KEYS)

    # ① 允许的键：写入成功并可读回
    ok = client.patch(
        "/api/v1/company/work-policy",
        json={
            "runtime_defaults": {
                "runtime_type": "mock",
                "runtime_config": {"mock_task_seconds": 0.01},
            }
        },
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["runtime_defaults"]["runtime_type"] == "mock"
    # ② 禁止键：422（不是静默忽略）
    for bad in ({"persona": "严谨的工程师"}, {"system_prompt": "你是…"}, {"workflow": ["a", "b"]}):
        response = client.patch("/api/v1/company/work-policy", json={"runtime_defaults": bad})
        assert response.status_code == 422, response.text
        assert "must not configure how an agent works" in response.json()["detail"]
    # ③ 未知键同样 422（"我配了但没生效"不许成为谜）
    response = client.patch(
        "/api/v1/company/work-policy", json={"runtime_defaults": {"nonsense": 1}}
    )
    assert response.status_code == 422
    assert "unknown runtime policy keys" in response.json()["detail"]

    # ④ 策略里没有工作方式键：真源是契约常量（有守卫）
    source = READINESS_MODULE.read_text(encoding="utf-8")
    assert "FORBIDDEN_RUNTIME_POLICY_KEYS" in source


# ---------------------------------------------------------------------------
# RD5 / I2：失败 ⇒ partial + 明确原因（绝不四舍五入）
# ---------------------------------------------------------------------------


def test_provisioning_failure_is_partial_and_explicit(db, default_company_id):
    """RD5（= I2）：单步失败 ⇒ job `partial` + 明确原因；人就绪为 false。"""
    employee = _hire(db, default_company_id, "ready-noprovider", workspace="/tmp/m28-no-provider")
    employee.runtime_type = RuntimeType.claude_code.value
    db.commit()

    # 策略里指向一个**不存在**的供应商（显式写全，不依赖别的用例留下的值）
    readiness.set_company_runtime_defaults(
        db,
        default_company_id,
        {"runtime_type": "claude_code", "provider_id": 999_999, "model": "ghost-model"},
    )
    result = readiness.provision_employee(db, employee)
    assert result.ok is False
    assert result.job_status == "partial"
    assert result.failed_steps == ("provider",)
    db.expire_all()
    report = readiness.readiness_report(db, org_repo.get_employee(db, int(employee.id)))
    assert report.ready_to_work is False
    assert "provider" in report.gaps

    # job 行里能看到失败步骤与原因（可核对，不靠猜）
    from app.models.lifecycle import ProvisioningStep

    steps = list(
        db.scalars(select(ProvisioningStep).where(ProvisioningStep.job_id == int(result.job_id)))
    )
    failed = [step for step in steps if step.status == "failed"]
    assert failed and failed[0].error and "missing or disabled" in str(failed[0].error)

    # 换成一个**真的存在**的供应商 ⇒ 再跑一次就齐了（同一个员工，同一套事实）
    _grant_real_runtime(db, default_company_id)
    again = readiness.provision_employee(db, employee)
    assert again.ok is True, again.steps
    db.expire_all()
    report = readiness.readiness_report(db, org_repo.get_employee(db, int(employee.id)))
    assert report.ready_to_work is False  # 这个人没有任职 ⇒ 仍差 position（软契约）
    assert readiness.blocking_gaps(db, employee) == (), "执行门禁已放行"


# ---------------------------------------------------------------------------
# RD6：公司作用域
# ---------------------------------------------------------------------------


def test_readiness_is_company_scoped(db, default_company_id, monkeypatch):
    """RD6：别家公司的员工读不到、也编排不了。"""
    employee = _hire(db, default_company_id, "ready-scope")
    from app.models.organization import Company

    other_company = Company(name="别家公司", slug="m28-other-company", description="")
    db.add(other_company)
    db.commit()
    employee.company_id = int(other_company.id)
    db.commit()

    # 策略解析走**员工自己公司**的设置：两家公司各配一个独特标记，看解析结果跟谁走
    readiness.set_company_runtime_defaults(
        db, default_company_id, {"runtime_config": {"owner": "default-company"}}
    )
    readiness.set_company_runtime_defaults(
        db, int(other_company.id), {"runtime_config": {"owner": "other-company"}}
    )
    db.expire_all()
    other_policy = readiness.resolve_runtime_policy(
        db, company_id=int(other_company.id), employee=employee
    )
    default_policy = readiness.resolve_runtime_policy(
        db,
        company_id=default_company_id,
        employee=_employees(db, default_company_id)["charlie"],
    )
    assert other_policy.runtime_config.get("owner") == "other-company"
    assert default_policy.runtime_config.get("owner") == "default-company"
    assert other_policy.runtime_type == RuntimeType.mock.value
    assert other_policy.source in {
        C.RUNTIME_POLICY_SOURCES["company_default"],
        C.RUNTIME_POLICY_SOURCES["employee_override"],
    }
    # 只读工具面按公司隔离（别家公司的人读不到）
    from app.work import tool_executor

    reader = _employees(db, default_company_id)["alice"]
    ctx = tool_executor.context_for_employee(db, reader, origin="test")
    result = tool_executor.execute_tool(
        db, name="inspect_readiness", args={"employee_id": int(employee.id)}, context=ctx
    )
    assert result.ok is False
    assert "not found in this company" in result.error
    _ = monkeypatch


# ---------------------------------------------------------------------------
# RD7 / I5：人级资产逐行不变
# ---------------------------------------------------------------------------


def test_onboarding_leaves_person_assets_untouched(db, default_company_id):
    """RD7（= I5/W7/W8/W10）：开通前开通后，人级资产**逐行**不变。"""
    employee = _hire(db, default_company_id, "ready-assets")
    db.add(
        Skill(
            employee_id=int(employee.id),
            name="existing-skill",
            description="上岗前就有",
            attempts=3,
            success_count=2,
        )
    )
    db.commit()

    def _snapshot() -> dict:
        person = db.get(Person, int(employee.person_id))
        return {
            "person": (person.id, person.slug, person.name),
            "skills": sorted(
                (row.id, row.employee_id, row.name, row.attempts, row.success_count)
                for row in db.scalars(select(Skill).where(Skill.employee_id == int(employee.id)))
            ),
        }

    before = _snapshot()
    Path(employee.workspace_path).mkdir(parents=True, exist_ok=True)
    result = readiness.provision_employee(db, employee)
    assert result.ok is True, result.steps
    after = _snapshot()
    assert before == after, "开通路径改写了人级资产（W7/W8/W10）"

    # 开通也不许**新建**技能/知识（那是"注入"，明令禁止）
    assert len(after["skills"]) == len(before["skills"]) == 1


# ---------------------------------------------------------------------------
# I4：编排失败可以整笔回滚（招募路径的既有纪律）
# ---------------------------------------------------------------------------


def test_orchestration_is_transactional_on_rollback(db, default_company_id):
    """I4：`orchestrate` 不自己 commit ⇒ 调用方回滚后什么都不留（E13/E14/E15）。"""
    employee = _hire(db, default_company_id, "ready-rollback")
    Path(employee.workspace_path).mkdir(parents=True, exist_ok=True)
    project_repo.create_project(
        db,
        company_id=default_company_id,
        name="回滚前的写入",
        description="M2.8",
        status="in_progress",
        source_order_text="M2.8",
    )
    db.commit()

    from app.models.lifecycle import ProvisioningJob

    with SessionLocal() as other:
        jobs_before = len(list(other.scalars(select(ProvisioningJob))))
        instances_before = len(list(other.scalars(select(ProvisioningJob.id))))

    result = readiness.orchestrate(db, employee, commit=False)
    assert result.ok is True
    assert result.job_id is not None
    db.rollback()

    with SessionLocal() as other:
        jobs_after = len(list(other.scalars(select(ProvisioningJob))))
        assert jobs_after == jobs_before, "回滚后还留着编排 job —— 事务边界漏了"
        assert runtime_repo.get_instance_for_employee(other, int(employee.id)) is None, (
            "回滚后还留着运行时实例"
        )
    _ = instances_before


# ---------------------------------------------------------------------------
# 招募入口：策略 + 就绪摘要
# ---------------------------------------------------------------------------


def test_recruitment_uses_the_policy_and_reports_readiness(client, db, default_company_id):
    """招募路径：按公司策略建员工 + 环境编排 + 响应里带就绪摘要（**行为**验证）。"""
    # 公司策略：mock + 一个**独特的**环境参数标记（能被观察到的策略证据）
    marker = "m28-policy-marker"
    response = client.patch(
        "/api/v1/company/work-policy",
        json={
            "runtime_defaults": {
                "runtime_type": "mock",
                "runtime_config": {marker: True, "mock_task_seconds": 0.01},
            }
        },
    )
    assert response.status_code == 200, response.text

    # 造一个在市人才并招募（与 test_recruitment 同款：走培养链生成可挂牌的人）
    created = client.post(
        "/api/v1/cultivation/characters", json={"name": "M2.8 招募", "origin": "blank"}
    ).json()
    session = client.post(
        f"/api/v1/cultivation/characters/{created['id']}/sessions",
        json={"topic": "M2.8 就绪验证", "mode": "web_research", "kind": "course", "signal": 72},
    )
    assert session.status_code == 201, session.text
    assert (
        client.post(f"/api/v1/cultivation/characters/{created['id']}/complete").status_code == 200
    )
    listing = client.post("/api/v1/market/listings", json={"person_id": created["person_id"]})
    assert listing.status_code == 201, listing.text

    listing_id = listing.json()["listing_id"]
    recruited = client.post(f"/api/v1/market/listings/{listing_id}/recruit", json={})
    assert recruited.status_code == 200, recruited.text
    body = recruited.json()

    # ① 响应带就绪摘要（未达 READY 时能说清差什么）
    assert body["readiness"] is not None
    assert body["readiness"]["ok"] is True, body["readiness"]["steps"]
    assert body["readiness"]["job_status"] == "done"
    assert [item["item"] for item in body["readiness"]["readiness"]["items"]] == list(
        C.READINESS_ITEMS
    )

    # ② 员工行**真的**带着公司策略里的环境参数（策略被用上了，不是摆设）
    employee = org_repo.get_employee(db, int(body["employee_id"]))
    assert employee.runtime_type == RuntimeType.mock.value
    assert employee.runtime_config.get(marker) is True, employee.runtime_config

    # ③ 环境事实齐备：工作区真的在磁盘上、运行时实例真的建出来了、job 步骤全 done
    assert Path(employee.workspace_path).is_dir()
    assert runtime_repo.get_instance_for_employee(db, int(employee.id)) is not None
    from app.models.lifecycle import ProvisioningJob

    job = db.get(ProvisioningJob, int(body["readiness"]["job_id"]))
    assert job is not None and job.status == "done"

    # ④ 读面能看到同一个结论
    reported = client.get(f"/api/v1/employees/{employee.id}/readiness").json()
    assert reported["policy"]["runtime_type"] == "mock"
    assert {"position", "workspace", "runtime", "provider"} == {
        item["item"] for item in reported["items"]
    }


# ---------------------------------------------------------------------------
# 反例注入：把违规实现喂给同一条守卫，守卫必须转红
# ---------------------------------------------------------------------------


def _guard_ready_is_derived(columns: set[str]) -> None:
    assert not (columns & {"ready_to_work", "is_ready", "readiness"}), "就绪被落了列（RD1）"


def _guard_gaps_are_named(report_items: dict[str, bool]) -> None:
    gaps = [name for name, ready in report_items.items() if not ready]
    assert all(gaps), "有未就绪项却报不出缺口（RD2）"


def _guard_policy_is_environment_only(keys: set[str]) -> None:
    assert not (keys & C.FORBIDDEN_RUNTIME_POLICY_KEYS), "策略里夹带了工作方式（RD4）"


def test_guards_catch_the_counter_examples():
    """反例注入：每条守卫在收到违规输入时都必须转红。"""
    _guard_ready_is_derived({"slug", "workspace_path"})
    with pytest.raises(AssertionError, match="RD1"):
        _guard_ready_is_derived({"slug", "ready_to_work"})

    _guard_gaps_are_named({"position": True, "workspace": False})
    with pytest.raises(AssertionError):
        _guard_gaps_are_named({"position": False, "": False})

    _guard_policy_is_environment_only({"runtime_type", "model"})
    with pytest.raises(AssertionError, match="RD4"):
        _guard_policy_is_environment_only({"runtime_type", "system_prompt"})


def test_guard_catches_readiness_written_back(tmp_path):
    """反例注入：让就绪变成"写回列" ⇒ 守卫转红。"""

    def _guard(source: str) -> None:
        tree = ast.parse(source)
        writes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Attribute) and target.attr == "ready_to_work"
                for target in node.targets
            )
        ]
        assert not writes, "就绪被写进了某个对象 —— 必须是派生量（RD1）"

    _guard("def f(employee):\n    report = readiness_report(employee)\n    return report\n")
    with pytest.raises(AssertionError, match="RD1"):
        _guard("def f(employee):\n    employee.ready_to_work = True\n")


def test_guard_catches_policy_key_smuggling():
    """反例注入：允许键集合里混进禁止键 ⇒ 契约守卫（导入期）就会炸。"""
    allowed = set(C.RUNTIME_POLICY_KEYS)
    assert not (allowed & C.FORBIDDEN_RUNTIME_POLICY_KEYS)
    with pytest.raises(AssertionError, match="RD4"):
        _guard_policy_is_environment_only(allowed | {"persona"})
    # 契约里的不变量也必须登记（防止"只写在文档里"）
    ids = {invariant.id for invariant in C.INVARIANTS}
    assert {"RD1", "RD2", "RD3", "RD4", "RD5", "RD6", "RD7"} <= ids
