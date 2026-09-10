"""P4b：人才名册 / 组织 / 分配 API 的契约验收。

选这几个断言的理由：这次重构的成败最终体现在"接口能不能诚实地说出两个轴"。
所以这里既测形状，也测三件容易被偷懒破坏的事：
  * 名册与详情页的状态必须出自同一个 resolver；
  * 派生态不能作为入参写进来（422）；
  * 招聘出来的人是 AVAILABLE，而不是被谁补了个坑。
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.organization import Employee
from app.models.position import PositionDefinition, PositionSlot
from app.repositories import position as position_repo
from app.services import lifecycle as lifecycle_service

ROSTER_KEYS = {
    "employee_id",
    "name",
    "slug",
    "avatar",
    "department_id",
    "department_name",
    "lifecycle_status",
    "workforce_status",
    "has_primary_assignment",
    "occupies_establishment",
    "current_position",
    "integrity",
}


def _hire(client: TestClient, slug: str, departments: dict, dept: str = "engineering") -> int:
    response = client.post(
        "/api/v1/employees/onboard",
        json={
            "name": slug.replace("-", " ").title(),
            "slug": slug,
            "title": "Backend Engineer",
            "role": "engineer",
            "department_id": departments[dept],
            "runtime_type": "mock",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["employee"]["id"]


def _dept_slots(db: Session, department_id: int) -> list[PositionSlot]:
    """断言消息用：失败时直接打出那部门的坑与行政态。"""
    return list(
        db.scalars(sa.select(PositionSlot).where(PositionSlot.department_id == department_id))
    )


@pytest.fixture()
def departments(client: TestClient) -> dict:
    """部门 id 从 /company 拿（v0.4 就把部门挂在公司信息里，没有独立列表端点）。"""
    company = client.get("/api/v1/company").json()
    return {row["slug"]: row["id"] for row in company["departments"]}


# ---------------------------------------------------------------- 名册（只读）


def test_roster_lists_every_person_with_both_axes(
    client: TestClient, db: Session, default_company_id: int
):
    # P9：默认列表排除已离职；这里断言“派生轴正确性”需要全量基准，显式纳入历史
    entries = client.get("/api/v1/talent-roster", params={"include_offboarded": "true"}).json()
    # 比较必须落在同一条公司边界内（ADR-11）：`/talent-roster` 是默认公司的读面，
    # 而裸 `select(Employee)` 是全库 —— dev 库与本会话里别的测试文件都会造别的公司的人，
    # 用全局数对scoped读面，会假失败（P4c 的桥测试就是这么把它撞出来的）。
    people = db.scalars(sa.select(Employee).where(Employee.company_id == default_company_id)).all()
    assert len(entries) == len(people), "名册不许漏人，也不许凭空造人"
    assert {entry["employee_id"] for entry in entries} == {person.id for person in people}
    for entry in entries:
        assert ROSTER_KEYS <= set(entry), set(entry)
        # 两个轴各自独立出现：lifecycle 是输入，workforce 是派生
        assert entry["lifecycle_status"] and entry["workforce_status"]
        if entry["workforce_status"] == "available":
            assert entry["occupies_establishment"] is False
            assert entry["current_position"] is None
        if entry["workforce_status"] == "assigned":
            assert entry["occupies_establishment"] is True
            assert entry["current_position"]["slot_code"]


def test_roster_status_filter_and_stats_are_consistent(client: TestClient):
    # 全量基准（含历史离职）与 stats 对齐；P9 默认列表排除已离职由单独用例断言
    everything = client.get("/api/v1/talent-roster", params={"include_offboarded": "true"}).json()
    available = client.get("/api/v1/talent-roster", params={"status": "available"}).json()
    assert {entry["workforce_status"] for entry in available} <= {"available"}
    assert len(available) <= len(everything)
    stats = client.get("/api/v1/talent-roster/stats").json()
    assert stats["total"] == len(everything)
    # available 是 on_roster 的子集；把 available+assigned 当在岗会漏掉 transferring
    assert stats["on_roster"] == (
        stats["by_status"].get("assigned", 0)
        + stats["by_status"].get("available", 0)
        + stats["by_status"].get("transferring", 0)
    )
    assert stats["slots_total"] >= stats["slots_vacant"]


def test_recruited_people_show_up_as_available_not_backfilled(
    client: TestClient, departments: dict, fake_gitea
):
    """P4b 的核心用户可见结果：招聘只造人，坑要显式分配。"""
    employee_id = _hire(client, f"p4b-free-{departments['engineering']}", departments)
    entry = next(
        item
        for item in client.get("/api/v1/talent-roster").json()
        if item["employee_id"] == employee_id
    )
    assert entry["workforce_status"] == "available"
    assert entry["current_position"] is None
    assert entry["has_primary_assignment"] is False, "招聘阶段就被塞了主职"
    assert entry["integrity"] == [], "没有职位不是数据问题，不该出现在诊断里"
    # 单人视角与名册必须同源
    single = client.get(f"/api/v1/talent-roster/{employee_id}").json()
    assert single["workforce_status"] == entry["workforce_status"]
    assert single["current_position"] is None


# ------------------------------------------------------------- 分配（唯一写口）


def test_assign_release_cycle_moves_occupancy(client: TestClient, db: Session, departments: dict):
    definition = db.scalars(
        sa.select(PositionDefinition).where(PositionDefinition.code == "engineer")
    ).first()
    assert definition is not None, "v12 回填后 engineer 定义必须存在"
    opened = client.post(
        f"/api/v1/organizations/definitions/{definition.id}/slots",
        json={"department_id": departments["engineering"], "note": "p4b api test"},
    )
    assert opened.status_code == 201, opened.text
    slot = opened.json()[0]
    assert slot["administrative_status"] == "planned"
    assert slot["occupancy_status"] == "vacant"

    employee_id = db.scalar(sa.text("SELECT id FROM employees WHERE slug='dana'"))
    assigned = client.post(
        f"/api/v1/talent-roster/{employee_id}/assignments",
        json={"slot_id": slot["id"], "reason": "api test appoint"},
    )
    assert assigned.status_code == 201, assigned.text
    body = assigned.json()
    assert body["position_slot_id"] == slot["id"]
    assert body["assignment_type"] == "primary" and body["is_primary"] is True
    assert body["occupied_slot"] is True

    assert client.get(f"/api/v1/organizations/slots/{slot['id']}").json()["occupancy_status"] == (
        "occupied"
    )
    # 坑被占住之后第二个人必须被拒（数据库唯一索引之外的友好 409）
    other_id = db.scalar(sa.text("SELECT id FROM employees WHERE slug='charlie'"))
    clash = client.post(
        f"/api/v1/talent-roster/{other_id}/assignments", json={"slot_id": slot["id"]}
    )
    assert clash.status_code == 409
    assert "已有在任者" in clash.json()["detail"]

    released = client.post(
        f"/api/v1/talent-roster/{employee_id}/unassign", params={"reason": "bench"}
    )
    assert released.status_code == 200, released.text
    assert client.get(f"/api/v1/organizations/slots/{slot['id']}").json()["occupancy_status"] == (
        "vacant"
    )
    assert (
        client.get(f"/api/v1/talent-roster/{employee_id}").json()["workforce_status"] == "available"
    )


def test_assignment_errors_are_actionable_not_500(client: TestClient, db: Session):
    employee_id = db.scalar(sa.text("SELECT id FROM employees WHERE slug='bob'"))
    missing = client.post(
        f"/api/v1/talent-roster/{employee_id}/assignments", json={"slot_id": 987_654}
    )
    assert missing.status_code == 404, missing.text
    assert "position slot" in missing.json()["detail"]
    neither = client.post(f"/api/v1/talent-roster/{employee_id}/assignments", json={})
    assert neither.status_code == 422


def test_frozen_slot_refuses_assignment_through_the_api(
    client: TestClient, db: Session, departments: dict
):
    """冻结的坑既不接收新人，也不算空缺。

    刻意**自己开坑**再冻结：早先的版本拿 `limit(1)` 抓了 CEO 坑，测试一失败就把
    全公司的执行层编制留在 FROZEN 上，后面的用例莫名其妙跟着红 —— 共享库里
    动别人的既成事实，等于把测试互相耦合起来。
    """
    definition = db.scalars(
        sa.select(PositionDefinition).where(PositionDefinition.code == "engineer")
    ).first()
    opened = client.post(
        f"/api/v1/organizations/definitions/{definition.id}/slots",
        json={"department_id": departments["engineering"], "note": "freeze test"},
    )
    assert opened.status_code == 201, opened.text
    slot = opened.json()[0]
    assert slot["occupancy_status"] == "vacant"

    frozen = client.post(
        f"/api/v1/organizations/slots/{slot['id']}/freeze", json={"reason": "hiring freeze"}
    )
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["administrative_status"] == "frozen"
    # 冻结之后它既不是 VACANT（招聘建议不该来填），也不可被分配
    assert frozen.json()["occupancy_status"] == "frozen"
    assert client.get("/api/v1/organizations/vacancies").json() == [] or all(
        item["id"] != slot["id"] for item in client.get("/api/v1/organizations/vacancies").json()
    ), "FROZEN 的坑不许出现在空缺列表里"

    employee_id = db.scalar(sa.text("SELECT id FROM employees WHERE slug='morgan'"))
    attempt = client.post(
        f"/api/v1/talent-roster/{employee_id}/assignments", json={"slot_id": slot["id"]}
    )
    assert attempt.status_code == 409, attempt.text
    assert "frozen" in attempt.json()["detail"]
    # 解冻还原：状态机是双向的，否则冻结就成了终态
    restored = client.post(
        f"/api/v1/organizations/slots/{slot['id']}/activate", json={"reason": "undo test"}
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["administrative_status"] == "active"
    assert restored.json()["occupancy_status"] == "vacant"


def test_occupancy_has_no_write_surface_at_all(client: TestClient, db: Session):
    """ADR-2 的 HTTP 表达：派生态连"门"都没有。

    三条一起测，因为单看任何一条都可能只是巧合：
      1. 没有 PATCH/PUT —— 坑不能被打字段，只能被动作（freeze/close/activate）改变；
      2. 没有 /occupy 这类动作 —— 占用态不是动作，是任职的结果；
      3. 动作端点的入参里只有 `reason` —— 就算加回 PATCH 也没有能写状态的字段。
    """
    slot = db.scalars(sa.select(PositionSlot).limit(1)).first()
    patch = client.patch(
        f"/api/v1/organizations/slots/{slot.id}", json={"occupancy_status": "occupied"}
    )
    assert patch.status_code == 405, patch.text
    put = client.put(
        f"/api/v1/organizations/slots/{slot.id}", json={"administrative_status": "active"}
    )
    assert put.status_code == 405, put.text
    for verb in (client.post,):
        for action in ("occupy", "vacate", "assign-occupancy"):
            missing = verb(f"/api/v1/organizations/slots/{slot.id}/{action}", json={})
            assert missing.status_code == 404, (action, missing.text)
    # 3. 所有*入参* schema 里都不许出现派生态字段
    #    （出参里有是诚实的：它标明了"这是算出来的"；入参里有就是第二个真相）
    from app.schemas import position as position_schemas

    derived_names = {"occupancy_status", "workforce_status"}
    for name, model in vars(position_schemas).items():
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            continue
        if name.endswith("Out"):
            continue
        leaked = derived_names & set(model.model_fields)
        assert not leaked, f"{name} 把派生态当入参：{leaked}"
    assert set(position_schemas.SlotActionIn.model_fields) == {"reason"}, (
        "动作端点一旦能传状态，行政态就变成任意可写字段"
    )


def test_positions_expose_slot_and_package_rollup(client: TestClient):
    rows = client.get("/api/v1/organizations/definitions").json()
    assert rows, "v12 回填 + seed 之后应当有职位模板"
    by_code = {row["code"]: row for row in rows}
    assert "engineer" in by_code and "ceo" in by_code
    ceo = by_code["ceo"]
    assert ceo["slot_count"] >= 1
    # `package_slugs` 的**内容**属于 P4d（职位权限包与 `ROLE_TO_PACKAGE_SLUG` 的接管）；
    # P4b 只保证这个字段存在且是列表 —— 提前断言非空会把"还没做"报成"做错了"。
    assert isinstance(ceo["package_slugs"], list)
    assert set(ceo) >= {"id", "code", "name", "job_family", "level", "legacy_role", "built_in"}


def test_create_position_rejects_duplicate_code(client: TestClient, departments: dict):
    payload = {"code": "p4b_api_lead", "name": "P4B Lead", "job_family": "engineering", "level": 4}
    created = client.post("/api/v1/organizations/definitions", json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["vacant_count"] == 0 and created.json()["slot_count"] == 0
    duplicate = client.post("/api/v1/organizations/definitions", json=payload)
    assert duplicate.status_code == 409


def test_organization_shows_establishment_with_incumbents(client: TestClient):
    tree = client.get("/api/v1/organizations/tree").json()
    assert "departments" in tree
    departments = tree["departments"]
    assert departments
    slots = [slot for department in departments for slot in department["slots"]]
    assert slots, "组织页必须显示真实编制，而不是空页"
    for slot in slots:
        assert {"administrative_status", "occupancy_status"} <= set(slot)
        assert slot["position_code"], f"坑必须能解析到职位定义：{slot}"
    occupied = [slot for slot in slots if slot["occupancy_status"] == "occupied"]
    assert occupied, "创始团队应当占着编制"
    for slot in occupied:
        assert len(slot["incumbents"]) == 1, "占着编制的坑恰好一个在任者（唯一索引的读面印证）"


def test_integrity_endpoint_is_read_only(client: TestClient, db: Session):
    before = db.scalar(sa.text("SELECT COUNT(*) FROM employments"))
    report = client.get("/api/v1/talent-roster/integrity").json()
    assert report["read_only"] is True
    assert isinstance(report["counts"], dict)
    assert isinstance(report["items"], list)
    for item in report["items"]:
        assert item["kind"] in {
            "NO_SLOT_HISTORICAL",
            "DANGLING_SLOT",
            "MISSING_DEFINITION",
            "SLOT_DEPARTMENT_MISMATCH",
            "MULTIPLE_ACTIVE_PRIMARY",
        }
    db.expire_all()
    assert db.scalar(sa.text("SELECT COUNT(*) FROM employments")) == before


def test_roster_marks_historical_rows_so_available_and_dangling_are_distinguishable(
    client: TestClient, db: Session
):
    """用户明确要求：正常待分配 与 历史悬空引用 必须能分开看。"""
    from app.models.enums import AssignmentType
    from app.models.position import PositionAssignment

    company_id = db.scalar(sa.text("SELECT id FROM companies ORDER BY id LIMIT 1"))
    # 部门要取自**同一家公司**的人，否则造出来的孤儿行本身就跨了边界
    department = (
        db.scalars(sa.select(Employee).where(Employee.company_id == company_id).limit(1))
        .first()
        .department_id
    )
    orphan = Employee(
        company_id=db.scalar(sa.text("SELECT id FROM companies ORDER BY id LIMIT 1")),
        department_id=department,
        name="P4B Orphan",
        slug="p4b-orphan",
        role="engineer",
        title="orphan",
        avatar="",
        status="idle",
        lifecycle_status="active",
        username="p4b-orphan",
        runtime_type="mock",
        runtime_config={},
        workspace_path="data/employees/p4b-orphan",
        memory_namespace="emp_p4b-orphan",
    )
    db.add(orphan)
    db.flush()
    db.add(
        PositionAssignment(
            employee_id=orphan.id,
            department_id=department,
            position_slot_id=999_990,
            employment_status="active",
            joined_at=orphan.created_at,
            effective_from=orphan.created_at,
            effective_to=None,
            metadata_json={},
            assignment_type=AssignmentType.primary.value,
            is_primary=True,
            reason="fixture",
            position_title_snapshot="",
        )
    )
    db.commit()

    entries = {entry["employee_id"]: entry for entry in client.get("/api/v1/talent-roster").json()}[
        orphan.id
    ]
    assert entries["workforce_status"] == "available"
    assert entries["has_primary_assignment"] is True, "确实有一条主职行（只是解析不到编制）"
    assert entries["occupies_establishment"] is False
    assert "DANGLING_SLOT" in entries["integrity"], (
        "悬空引用必须在名册上看得见，而不是和待分配混在一起"
    )
    # 清理：共享测试库里留一个坏引用会让别的用例解释不清
    assignment = db.scalars(
        sa.select(PositionAssignment).where(PositionAssignment.employee_id == orphan.id)
    ).first()
    db.delete(assignment)
    db.delete(orphan)
    db.commit()


def test_seed_bootstraps_an_establishment_for_every_builtin_department(
    client: TestClient, db: Session, departments: dict
):
    """全新库里 seed 必须保证"存在可分配的坑"，否则招聘永远分配不了职位。

    同时它必须幂等：seed 每次启动都跑，多跑一次不该多出坑或多出任职。
    """
    lifecycle_service.seed_lifecycle(db)
    db.commit()
    slots_after_first = db.scalar(sa.text("SELECT COUNT(*) FROM position_slots"))
    assignments_after_first = db.scalar(sa.text("SELECT COUNT(*) FROM employments"))

    legacy_positions = db.scalars(
        sa.select(__import__("app.models.lifecycle", fromlist=["Position"]).Position)
    ).all()
    markers = {
        position_repo.legacy_position_id_of_slot(slot)
        for slot in db.scalars(sa.select(PositionSlot))
    }
    unbootstrapped = [item.id for item in legacy_positions if item.id not in markers]
    assert not unbootstrapped, f"这些 v0.4 职位还没有编制：{unbootstrapped}"

    # 只查**内置**部门：其它用例建的部门本来就没有编制，那正是 AVAILABLE 与"无处可分"的区别
    from app.services.seed import DEPARTMENTS as BUILTIN_DEPARTMENTS

    builtin = {slug for _name, slug in BUILTIN_DEPARTMENTS}
    missing = builtin - set(departments)
    assert not missing, f"内置部门没出现在 /company 里：{missing}"
    for slug, department_id in departments.items():
        if slug not in builtin:
            continue
        fillable = [
            slot
            for slot in _dept_slots(db, department_id)
            if slot.administrative_status in {"planned", "active"}
        ]
        assert fillable, (
            f"部门 {slug} 没有可分配编制："
            f"{[(s.slot_code, s.administrative_status) for s in _dept_slots(db, department_id)]}"
        )

    lifecycle_service.seed_lifecycle(db)
    db.commit()
    assert db.scalar(sa.text("SELECT COUNT(*) FROM position_slots")) == slots_after_first
    assert db.scalar(sa.text("SELECT COUNT(*) FROM employments")) == assignments_after_first


def test_roster_survives_person_only_rows_after_recruitment_like_state(
    client: TestClient, db: Session, default_company_id: int
):
    """T2.2/T2.6 保护：person-only 行（employee_id IS NULL）不得打挂名册。

    培养角色（T1）的人格/能力行是 person-only；招募（T2.6）会给同一个 person 建
    employee 行 —— 此时名册的批量属主解析会同时看到「person_id 命中、employee_id 为
    NULL」的行。老实现直接 `derived[row.employee_id]` → KeyError(None) → /talent-roster 500。
    这里用同样的形态钉住：person-only brain + 后续 employee 行，名册仍返回且能读到 traits。
    """
    from factories import make_employee, make_person

    from app.models.runtime import EmployeeBrain

    person = make_person(db, slug="t22-roster-person-only")
    db.add(
        EmployeeBrain(
            employee_id=None,
            person_id=int(person.id),
            personality="person-only 人格（培养期生成）",
        )
    )
    db.commit()

    employee = make_employee(
        db, company_id=default_company_id, slug="t22-roster-emp", person=person
    )
    db.commit()

    # person-only 人格的默认 traits 全部 ≤0.65，traits_summary 为空是正常的；
    # 关键断言是「不 500」且该员工行仍在（属主还原没把它吞掉）。
    response = client.get("/api/v1/talent-roster")
    assert response.status_code == 200, response.text
    entry = next(row for row in response.json() if row["employee_id"] == int(employee.id))
    assert "traits_summary" in entry
