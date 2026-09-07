"""P4d 第 2 段：`employee.position_*` 事件 → Desired State 收敛 → 权限工单。

要证明的是三件容易在"顺手加个同步调用"里丢掉的东西：

1. 任职先提交、权限后收敛 —— 分配成功不依赖开通成功；
2. 收敛是幂等的期望态计算，不是"一次动作"（事件重放不产生第二个工单）；
3. 事件名与消费者订阅的是一回事（两侧名字漂移 = 静默不干活，最难查的那类 bug）。
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

import pytest
from test_position_service import _department, _employee, _slot

from app.events import bus as bus_module
from app.lifecycle import access
from app.models.enums import PackageSource
from app.models.lifecycle import AccessPackage, AccessPackageItem
from app.models.position import PositionDefinition, PositionDefinitionPackage
from app.repositories import lifecycle as lifecycle_repo
from app.schemas.position import AssignmentIn
from app.services import position_service
from app.workforce import access as workforce_access

NOW = datetime.now(UTC)
_counter = itertools.count(1)


def _uniq(prefix: str) -> str:
    return f"{prefix}-{next(_counter)}-{int(NOW.timestamp())}"


@pytest.fixture()
def stage(db, default_company_id):
    """一个带默认权限包的职位 + 一个空坑 + 一个人（人级只有 base 包）。"""
    department = _department(db, default_company_id)
    code = _uniq("SEE")
    definition = PositionDefinition(
        company_id=default_company_id,
        template_scope="company",
        code=code,
        name="软件工程师",
        description="",
        job_family="engineering",
        level=3,
        responsibilities=[],
        career_path_metadata={},
        built_in=False,
        legacy_role="engineer",
    )
    db.add(definition)
    entitlement = lifecycle_repo.get_entitlement_by_key(db, "git:company-org-member")
    assert entitlement is not None, "种子缺 git:company-org-member"
    package = AccessPackage(slug=_uniq("pkgE"), name="工程组访问", built_in=False)
    db.add(package)
    db.flush()
    db.add(AccessPackageItem(package_id=package.id, entitlement_id=entitlement.id))
    db.add(PositionDefinitionPackage(position_definition_id=definition.id, package_id=package.id))
    db.flush()
    slot = _slot(db, default_company_id, department, definition)
    person = _employee(db, default_company_id, department)
    return {
        "db": db,
        "department": department,
        "definition": definition,
        "package": package,
        "slot": slot,
        "person": person,
    }


def _position_package_ids(db, employee_id) -> set[int]:
    return {
        row.package_id
        for row in lifecycle_repo.list_employee_packages(db, employee_id)
        if row.source == PackageSource.position.value
    }


# ---------------------------------------------------------------- 事件契约


def test_published_event_names_match_what_the_consumer_subscribes(stage):
    """事件名两侧漂移 = 消费者静默不干活，是最难查的一类 bug。"""
    db, s = stage["db"], stage
    seen: list[tuple[str, dict]] = []
    original = bus_module.bus.publish

    def _spy(type, data, **kwargs):
        seen.append((type, data))
        return original(type, data, **kwargs)

    bus_module.bus.publish = _spy  # type: ignore[method-assign]
    try:
        position_service.assign_position(db, s["person"], AssignmentIn(slot_id=s["slot"].id))
        position_service.release_position(db, s["person"], reason="项目结束")
    finally:
        bus_module.bus.publish = original  # type: ignore[method-assign]

    names = [type for type, _ in seen]
    assert "employee.position_assigned" in names and "employee.position_released" in names, names
    for type, data in seen:
        if type in workforce_access.POSITION_EVENTS:
            assert data.get("id") == s["person"].id, (
                "消费者靠 data['id'] 认人；载荷换键会静默跳过"
                "（handle 里有 warning，但别指望它被看见）"
            )


# ---------------------------------------------------------------- 收敛行为


def test_assignment_grants_position_package_through_convergence(stage):
    db, s = stage["db"], stage
    position_service.assign_position(db, s["person"], AssignmentIn(slot_id=s["slot"].id))
    assert _position_package_ids(db, s["person"].id) == set(), "收敛必须由事件驱动，不在分配里"

    outcome = workforce_access.converge_employee_access(db, s["person"].id)
    assert outcome is not None
    assert _position_package_ids(db, s["person"].id) == {s["package"].id}
    assert "git:company-org-member" in outcome["diff"]["add"], outcome
    assert outcome["diff"]["remove"] == []


def test_release_revokes_the_position_package(stage):
    db, s = stage["db"], stage
    position_service.assign_position(db, s["person"], AssignmentIn(slot_id=s["slot"].id))
    workforce_access.converge_employee_access(db, s["person"].id)
    position_service.release_position(db, s["person"], reason="项目结束")

    outcome = workforce_access.converge_employee_access(db, s["person"].id)
    assert outcome is not None and outcome["removed"] == [s["package"].id]
    assert _position_package_ids(db, s["person"].id) == set()
    assert "git:company-org-member" in outcome["diff"]["remove"]


def test_convergence_is_idempotent_and_creates_one_job(stage):
    """重放同一事件不能出现第二个工单（否则 retry 会对同一份变更操作两次）。"""
    db, s = stage["db"], stage
    position_service.assign_position(db, s["person"], AssignmentIn(slot_id=s["slot"].id))
    first = workforce_access.converge_employee_access(db, s["person"].id)
    second = workforce_access.converge_employee_access(db, s["person"].id)
    assert first is not None and first["added"] == [s["package"].id]
    assert second is None, "已经收敛到位还再生成工单 = 幂等失效"
    jobs = lifecycle_repo.list_jobs(db, employee_id=s["person"].id)
    assert len(jobs) == 1, f"产生了 {len(jobs)} 个权限工单"


def test_person_level_rows_survive_the_whole_cycle(stage):
    """人级（manual/role）行全程不被职位层碰过 —— §4「调岗绝不动人级」。"""
    db, s = stage["db"], stage
    base = lifecycle_repo.get_package_by_slug(db, access.BASE_PACKAGE_SLUG)
    lifecycle_repo.create_employee_package(
        db,
        employee_id=s["person"].id,
        package_id=base.id,
        source=PackageSource.manual.value,
    )
    db.commit()
    before = {
        (row.package_id, row.source)
        for row in lifecycle_repo.list_employee_packages(db, s["person"].id)
    }

    position_service.assign_position(db, s["person"], AssignmentIn(slot_id=s["slot"].id))
    workforce_access.converge_employee_access(db, s["person"].id)
    position_service.release_position(db, s["person"], reason="结束")
    workforce_access.converge_employee_access(db, s["person"].id)

    after = {
        (row.package_id, row.source)
        for row in lifecycle_repo.list_employee_packages(db, s["person"].id)
        if row.source != PackageSource.position.value
    }
    assert after == before, "职位循环结束时人级行必须一模一样"


async def test_convergence_survives_a_failing_provisioner(stage, monkeypatch):
    """开通失败只体现在工单上：任职与权限集合已经落库，不回滚。

    这是"分配成功 ≠ 开通成功"的可执行版本 —— gitea/文档挂了，人还在职位上、
    期望权限也还在，失败只落在 `job.status`，由 retry / reconcile 收拾。
    """
    db, s = stage["db"], stage
    position_service.assign_position(db, s["person"], AssignmentIn(slot_id=s["slot"].id))
    assert position_service.current_employment(db, s["person"].id) is not None

    async def _boom(*args, **kwargs):
        raise RuntimeError("gitea 挂了")

    monkeypatch.setattr(workforce_access.engine, "run", _boom)
    result = await workforce_access.consumer.handle(
        {"type": "employee.position_assigned", "data": {"id": s["person"].id}}
    )
    assert result is not None and result["added"] == [s["package"].id]
    assert position_service.current_employment(db, s["person"].id) is not None, (
        "开通失败把任职一起带走了"
    )
    assert _position_package_ids(db, s["person"].id) == {s["package"].id}, (
        "期望集被开通失败回滚了 —— Desired State 与执行结果必须分层"
    )
    job = lifecycle_repo.get_job(db, int(result["job_id"]))
    assert job is not None and job.status == "pending", (
        f"执行失败不该把工单状态写成收敛结果的一部分：{job.status if job else None}"
    )


async def test_consumer_ignores_unrelated_events():
    assert await workforce_access.consumer.handle({"type": "task.completed", "data": {}}) is None
    assert await workforce_access.consumer.handle("garbage") is None  # type: ignore[arg-type]


async def test_consumer_warns_and_skips_an_event_without_a_person(stage):
    assert (
        await workforce_access.consumer.handle({"type": "employee.position_assigned", "data": {}})
        is None
    )


def test_startup_sweep_fixes_a_missed_event(stage, monkeypatch):
    """进程死在"提交任职"与"处理事件"之间 ⇒ 启动补收敛把权限对上，第二次空转。

    这里显式把 publish 打掉来模拟"事件丢了"，而不是依赖消费者没启动 ——
    真实事故正是任职提交成功、事件却没被处理。
    """
    db, s = stage["db"], stage
    monkeypatch.setattr(bus_module.bus, "publish", lambda *a, **k: None)
    position_service.assign_position(db, s["person"], AssignmentIn(slot_id=s["slot"].id))
    assert _position_package_ids(db, s["person"].id) == set()

    results = workforce_access.converge_all(db)
    assert s["person"].id in {r["employee_id"] for r in results}, results
    assert workforce_access.converge_all(db) == [], "补收敛第二次必须无事可做"


def test_convergence_of_an_unknown_employee_is_a_noop(db):
    assert workforce_access.converge_employee_access(db, 999_999) is None


# ---------------------------------------------------------------- 读面（API）


def _access(client, employee_id) -> dict:
    response = client.get(f"/api/v1/employees/{employee_id}/access")
    assert response.status_code == 200, response.text
    return response.json()


def _keys(entries) -> set[str]:
    return {entry["entitlement"]["key"] for entry in entries}


def test_access_endpoint_shows_the_position_layer_after_convergence(client, stage):
    db, s = stage["db"], stage
    position_service.assign_position(db, s["person"], AssignmentIn(slot_id=s["slot"].id))
    workforce_access.converge_employee_access(db, s["person"].id)

    body = _access(client, s["person"].id)
    assert "git:company-org-member" in _keys(body["position"]), body["position"]
    assert "git:company-org-member" in _keys(body["effective"])
    entry = next(e for e in body["position"] if e["entitlement"]["key"] == "git:company-org-member")
    assert {src["layer"] for src in entry["sources"]} == {"position"}, (
        "职位层视图里出现别的层级标签，说明序列化没有强制层归属"
    )


def test_access_endpoint_separates_declared_from_granted(client, stage):
    """分配之后、收敛之前：`declared_by_position` 有、`position` 还没有。

    这是异步收敛的诚实表达方式。只返回"已拿到"的话，界面会把"正在收敛"
    显示成"这个职位没有权限"，用户看到的是一条假信息。
    """
    db, s = stage["db"], stage
    position_service.assign_position(db, s["person"], AssignmentIn(slot_id=s["slot"].id))
    body = _access(client, s["person"].id)
    assert body["declared_by_position"] == [s["package"].slug], body
    assert _keys(body["position"]) == set(), "还没收敛就说有了 = 假绿"
    assert body["pending_from_position"] == [s["package"].slug], body
    assert body["already_held_by_person"] == []

    workforce_access.converge_employee_access(db, s["person"].id)
    body = _access(client, s["person"].id)
    assert _keys(body["position"]) == {"git:company-org-member"}


def test_access_endpoint_person_layer_survives_the_whole_cycle(client, stage):
    db, s = stage["db"], stage
    base = lifecycle_repo.get_package_by_slug(db, access.BASE_PACKAGE_SLUG)
    lifecycle_repo.create_employee_package(
        db,
        employee_id=s["person"].id,
        package_id=base.id,
        source=PackageSource.role.value,
    )
    db.commit()
    before = _access(client, s["person"].id)
    base_keys = {"workspace:private", "docs:company-read", "git:company-org-member"}
    assert _keys(before["person"]) == base_keys, before["person"]

    position_service.assign_position(db, s["person"], AssignmentIn(slot_id=s["slot"].id))
    workforce_access.converge_employee_access(db, s["person"].id)
    position_service.release_position(db, s["person"], reason="项目结束")
    workforce_access.converge_employee_access(db, s["person"].id)

    after = _access(client, s["person"].id)
    assert _keys(after["person"]) == base_keys, "调岗/卸任不该动人级"
    assert _keys(after["position"]) == set(), "职位层没收回去"
    # 职位包与 base 包共享 `git:company-org-member`：卸任后人级仍然有它
    # （docs/position-system.md §4 特别要求覆盖的"共享 entitlement 不误撤"）
    assert "git:company-org-member" in _keys(after["effective"])


def test_declared_but_already_held_is_not_reported_as_pending(client, stage):
    """职位声明的包人级已经有了 ⇒ 进 `already_held_by_person`，不是"待开通"。

    真实 dev 库就是这个情形（5 个定义各声明 1 个包，全部命中人级已有 ⇒ 补收敛零变更），
    所以这条不是假想场景：混起来的话 UI 会永远转圈。
    """
    db, s = stage["db"], stage
    base = lifecycle_repo.get_package_by_slug(db, access.BASE_PACKAGE_SLUG)
    lifecycle_repo.create_employee_package(
        db,
        employee_id=s["person"].id,
        package_id=base.id,
        source=PackageSource.role.value,
    )
    # 让职位声明**同一个包**（dev 库的真实形状：定义声明 `engineer`，人级也发过 `engineer`）
    db.query(PositionDefinitionPackage).filter_by(
        position_definition_id=s["definition"].id
    ).delete()
    db.add(PositionDefinitionPackage(position_definition_id=s["definition"].id, package_id=base.id))
    db.commit()
    position_service.assign_position(db, s["person"], AssignmentIn(slot_id=s["slot"].id))
    # 不收敛：职位声明的 git:company-org-member 人级已经有
    body = _access(client, s["person"].id)
    assert body["declared_by_position"] == [base.slug], body
    assert body["already_held_by_person"] == [base.slug], body
    assert body["pending_from_position"] == [], "人级已有还报待开通 = 假等待"
    assert workforce_access.converge_employee_access(db, s["person"].id) is None, (
        "人级已有的包不该再生成职位行 / 工单"
    )


def test_access_endpoint_requires_a_real_employee(client):
    assert client.get("/api/v1/employees/999999/access").status_code == 404


def test_entitlements_endpoint_still_works_and_now_labels_layers(client, stage):
    """旧端点契约不破：还是扁平列表，只是多了一个带默认值的 `layer` 字段。"""
    db, s = stage["db"], stage
    position_service.assign_position(db, s["person"], AssignmentIn(slot_id=s["slot"].id))
    workforce_access.converge_employee_access(db, s["person"].id)
    rows = client.get(f"/api/v1/employees/{s['person'].id}/entitlements").json()
    assert "git:company-org-member" in {r["entitlement"]["key"] for r in rows}
    layers = {src["layer"] for r in rows for src in r["sources"]}
    assert layers == {"position"}, f"这个人只有职位层：{layers}"
