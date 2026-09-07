"""P4c：`role` 口径的两座桥 —— 批量镜像 `role_mirror`、按旧 role 找人 `employee_by_legacy_role`。

存在的意义是把"谁真的是 CEO / QA"从旧文本列搬到组织事实上，同时**不制造新的找不到人**：
职位问不到时照旧回退，行为与改之前一致。

每台测试自己建公司：默认公司里有 session 级共享的种子员工（employee #1 的 role 就是 `ceo`），
在它上面断言"找到的是我这个人"会假通过，也会因别人的数据而假失败。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from app.models.organization import Company
from app.schemas.position import AssignmentIn, SlotAdminIn
from app.services import position_compat, position_service
from test_position_service import _definition, _department, _employee, _slot, _uniq


@pytest.fixture()
def stage(db):
    company = Company(name="桥测试公司", slug=_uniq("role-bridge"))
    db.add(company)
    db.flush()
    department = _department(db, company.id)
    definition = _definition(db, company.id, legacy_role="ceo")
    slot = _slot(db, company.id, department, definition)
    incumbent = _employee(db, company.id, department)
    # 没任职、但旧列镜像写着 ceo：v0.4 遗留与"只招不派"都是这个样子
    mirror_only = _employee(db, company.id, department)
    mirror_only.role = "ceo"
    db.flush()
    return {
        "db": db,
        "company": company,
        "department": department,
        "definition": definition,
        "slot": slot,
        "incumbent": incumbent,
        "mirror_only": mirror_only,
    }


def _assign(db, employee, slot):
    return position_service.assign_position(db, employee, AssignmentIn(slot_id=slot.id))


def test_bridge_prefers_the_establishment_holder_over_the_mirror(stage):
    """`AVAILABLE` 但镜像写着 ceo 的人不算 CEO —— 占着编制的那位才算。"""
    db, s = stage["db"], stage
    assert position_compat.employee_by_legacy_role(db, s["company"].id, "ceo").id == s[
        "mirror_only"
    ].id, "前置：没人任职时回退旧列"

    _assign(db, s["incumbent"], s["slot"])
    assert position_compat.employee_by_legacy_role(db, s["company"].id, "ceo").id == s[
        "incumbent"
    ].id, "派活跟着编制走，不跟着旧文本列走"

    position_service.release_position(db, s["incumbent"], reason="编制收回")
    assert position_compat.employee_by_legacy_role(db, s["company"].id, "ceo").id == s[
        "mirror_only"
    ].id, "离坑之后才退回镜像口径"


def test_bridge_never_lets_an_available_person_lose_the_fallback(stage):
    """回退分支必须保留：删掉它 = 在 P6 之前让老公司派不出活。"""
    db, s = stage["db"], stage
    assert position_service.current_employment(db, s["mirror_only"].id) is None
    found = position_compat.employee_by_legacy_role(db, s["company"].id, "ceo")
    assert found is not None and found.id == s["mirror_only"].id


def test_bridge_is_scoped_to_the_company(db):
    """别家公司占了同一个 role 的编制，也不算自己家的人。"""
    other = Company(name="隔壁公司", slug=_uniq("next-door"))
    db.add(other)
    db.flush()
    department = _department(db, other.id)
    definition = _definition(db, other.id, legacy_role="ceo")
    slot = _slot(db, other.id, department, definition)
    carol = _employee(db, other.id, department)
    _assign(db, carol, slot)

    mine = Company(name="自家公司", slug=_uniq("home"))
    db.add(mine)
    db.flush()
    assert position_compat.employee_by_legacy_role(db, mine.id, "ceo") is None, (
        "自家没人任职、自家镜像里也没人写 ceo —— 宁缺不错，不借别家的人"
    )
    assert position_compat.employee_by_legacy_role(db, other.id, "ceo").id == carol.id


def test_freezing_a_slot_does_not_evict_its_holder(stage):
    """冻结只挡**新的**分配，不把在任者降级 —— 否则名册说 OCCUPIED、派活说没人。

    这条是给仓库里那把尺上锁的：`employee_id_holding_legacy_role` 与
    `employees_in_position` 共用 `_position_holder_query()`，判据只有"生效 PRIMARY"，
    不看坑的行政态（ADR-2：一把尺）。
    """
    db, s = stage["db"], stage
    _assign(db, s["incumbent"], s["slot"])
    position_service.set_slot_administrative_status(
        db, s["slot"].id, SlotAdminIn(administrative_status="frozen", reason="暂停招聘")
    )
    assert position_compat.employee_by_legacy_role(db, s["company"].id, "ceo").id == s[
        "incumbent"
    ].id
    assert position_compat.role_mirror(db, [s["incumbent"]])[s["incumbent"].id] == "ceo"
    with pytest.raises(HTTPException) as exc:
        _assign(db, s["mirror_only"], s["slot"])
    assert exc.value.status_code == 409, "行政态管的是能不能再派进来"


def test_bridge_returns_none_when_neither_axis_has_a_person(db):
    empty = Company(name="空公司", slug=_uniq("empty-co"))
    db.add(empty)
    db.flush()
    assert position_compat.employee_by_legacy_role(db, empty.id, "secretary") is None


def test_role_mirror_agrees_with_single_lookup_without_n_plus_one(stage):
    """批量镜像 == 逐个镜像，且语句数不随人数增长。"""
    db, s = stage["db"], stage
    people = [s["incumbent"], s["mirror_only"]] + [
        _employee(db, s["company"].id, s["department"]) for _ in range(4)
    ]
    expected = {p.id: position_compat.legacy_role_of(db, p) for p in people}

    engine = db.get_bind()
    statements: list[str] = []

    def _count(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    try:
        position_compat.role_mirror(db, people[:1])
        one = len(statements)
        statements.clear()
        batch = position_compat.role_mirror(db, people)
        many = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    assert batch == expected
    assert one == many, f"批量镜像退化成 N+1：1 人 {one} 条、{len(people)} 人 {many} 条"


def test_role_mirror_is_honest_about_custom_and_invalid(stage):
    """自定义职位不谎报成某个存在的 role；镜像非法值兜 engineer。"""
    db, s = stage["db"], stage
    custom = _definition(db, s["company"].id, legacy_role=None)
    slot = _slot(db, s["company"].id, s["department"], custom)
    person = _employee(db, s["company"].id, s["department"])
    _assign(db, person, slot)
    assert position_compat.role_mirror(db, [person])[person.id] == "engineer"
    view = position_compat.derived_current_position(db, person)
    assert view is not None and view.position_is_custom, "自定义职位要用字段说清楚，不能靠 role 猜"

    ghost = _employee(db, s["company"].id, s["department"])
    ghost.role = "wizard"
    db.flush()
    assert position_compat.role_mirror(db, [ghost])[ghost.id] == "engineer"


def test_role_mirror_keyed_by_id_survives_lifecycle_changes(stage):
    """镜像读的是列，离职态也读得出来（旧前端还要显示）；key 是 id，不是对象。"""
    db, s = stage["db"], stage
    gone = _employee(db, s["company"].id, s["department"], lifecycle="offboarded")
    gone.role = "qa_engineer"
    db.flush()
    assert position_compat.role_mirror(db, [gone, s["mirror_only"]]) == {
        gone.id: "qa_engineer",
        s["mirror_only"].id: "ceo",
    }


def test_compat_has_exactly_one_direct_read_of_the_legacy_column():
    """compat 自己也不许长出第二处直读（文档说它是唯一出口，就得能被机器验证）。"""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(position_compat))
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "role"
        and not isinstance(node.ctx, ast.Store)
        and isinstance(node.value, ast.Name)
        and node.value.id.startswith("employee")
    ]
    assert len(reads) == 1, f"compat 里出现 {len(reads)} 处直读：L{reads[0].lineno}"
    assert hasattr(position_compat, "role_mirror") and hasattr(
        position_compat, "legacy_role_of"
    )
