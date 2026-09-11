"""T2.6 招募契约（docs/t2-talent-market-design.md §3.2/§5 / plan §4.7）。

锁（I1–I5、I7、I8）：
- **身份不变**：`person_id` / `identity_id` / `persons` 行 / traits / knowledge / evidence /
  assessment / education **全部原地不动**（招募不复制、不改写）；
- **I3**：Employee 引用既有 Person —— 不新建 Person（行为 + AST 双守卫）；
- **I5**：历史 provenance 不改写（`assessment_runs.company_id` 等快照前后一致）；
- **I7/I8**：只有 active 挂牌可被招募；重复/并发招募被拒绝（条件关闭 rowcount + 唯一约束）；
- **验收 B**：入职后**真实 retrieval pipeline** 能召回该 Person 培养期知识（不复制数据）；
- 行为：listing 关闭并回填招募方；`career_events(joined)`；`person.recruited` 事件；
  可选编制 → 同事务建任职（部门随编制走）；跨公司部门/编制 → 404。
"""

from __future__ import annotations

import ast
import json

import sqlalchemy as sa

from app.learning import retrieval
from app.models.competency import AssessmentRun, CompetencyEvidence, EmployeeCompetency
from app.models.enums import LifecycleStatus, MarketListingStatus
from app.models.event import Event
from app.models.knowledge import KnowledgeItem
from app.models.organization import Company, Employee
from app.models.position import PositionSlot
from app.models.runtime import EmployeeBrain
from app.repositories import cultivation as cultivation_repo
from app.repositories import market as market_repo
from app.repositories import position as position_repo
from app.schemas.position import SlotIn
from app.services import position_service
from app.talent.market.issuer import IssuerService

_seq = 0


# ---- 夹具：走真实培养链造一个"在市人才" ----


def _market_talent(client, *, topic: str = "市场招募检索标记") -> dict:
    """建空白角色 → 自由学习（产出履历/知识/证据）→ 结业 → 挂牌。"""
    global _seq
    _seq += 1
    created = client.post(
        "/api/v1/cultivation/characters", json={"name": f"招募测试 {_seq}", "origin": "blank"}
    ).json()
    session = client.post(
        f"/api/v1/cultivation/characters/{created['id']}/sessions",
        json={"topic": topic, "mode": "web_research", "kind": "course", "signal": 72},
    )
    assert session.status_code == 201, session.text
    assert (
        client.post(f"/api/v1/cultivation/characters/{created['id']}/complete").status_code == 200
    )
    listing = client.post("/api/v1/market/listings", json={"person_id": created["person_id"]})
    assert listing.status_code == 201, listing.text
    return {**created, "listing": listing.json()}


def _departments(client) -> dict:
    company = client.get("/api/v1/company").json()
    return {row["slug"]: row["id"] for row in company["departments"]}


def _asset_snapshot(db, person_id: int) -> dict:
    """人级资产快照（id + 关键字段）：招募前后必须逐值一致。"""
    return {
        "person": db.scalar(
            sa.text("SELECT slug || '|' || name FROM persons WHERE id = :p"), {"p": person_id}
        ),
        "profile": db.scalar(
            sa.text(
                "SELECT identity_id || '|' || origin || '|' || lifecycle || '|'"
                " || COALESCE(owner_company_id, -1) FROM character_profiles WHERE person_id = :p"
            ),
            {"p": person_id},
        ),
        "knowledge": [
            (row.id, row.topic, row.scope, row.owner_person_id, row.owner_employee_id)
            for row in db.scalars(
                sa.select(KnowledgeItem)
                .where(KnowledgeItem.owner_person_id == person_id)
                .order_by(KnowledgeItem.id)
            )
        ],
        "evidence": [
            (row.id, row.source_kind, row.signal, row.person_id, row.employee_id)
            for row in db.scalars(
                sa.select(CompetencyEvidence)
                .where(CompetencyEvidence.person_id == person_id)
                .order_by(CompetencyEvidence.id)
            )
        ],
        "education": [
            (row.id, row.kind, row.topic)
            for row in cultivation_repo.list_education_events(db, person_id)
        ],
        "competencies": [
            (
                row.competency_definition_id,
                row.score,
                row.confidence,
                row.person_id,
                row.employee_id,
            )
            for row in db.scalars(
                sa.select(EmployeeCompetency)
                .where(EmployeeCompetency.person_id == person_id)
                .order_by(EmployeeCompetency.competency_definition_id)
            )
        ],
        "assessments": [
            (row.id, row.company_id, row.person_id, row.triggered_by)
            for row in db.scalars(
                sa.select(AssessmentRun)
                .where(AssessmentRun.person_id == person_id)
                .order_by(AssessmentRun.id)
            )
        ],
        "brain": [
            (row.person_id, row.employee_id, json.dumps(row.traits or {}, sort_keys=True))
            for row in db.scalars(
                sa.select(EmployeeBrain).where(EmployeeBrain.person_id == person_id)
            )
        ],
    }


# ---- Golden path ----


def test_recruit_existing_person_golden_path(client, db, default_company_id):
    talent = _market_talent(client)
    person_id = talent["person_id"]
    persons_before = db.scalar(sa.text("SELECT count(*) FROM persons"))
    snapshot = _asset_snapshot(db, person_id)
    assert snapshot["knowledge"], "培养期必须产出知识（验收 B 的前置）"

    response = client.post(
        f"/api/v1/market/listings/{talent['listing']['listing_id']}/recruit",
        json={"title": "市场招募工程师", "reason": "t26 golden path"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["person_id"] == person_id, "I1：还是同一个人"
    assert body["identity_id"] == talent["identity_id"], "I2：身份 ID 不变"
    assert body["listing_id"] == talent["listing"]["listing_id"]

    # I3：没有新建 Person
    assert db.scalar(sa.text("SELECT count(*) FROM persons")) == persons_before
    employee = db.get(Employee, body["employee_id"])
    assert employee is not None
    assert int(employee.person_id) == person_id, "Employee 引用既有 Person"
    assert int(employee.company_id) == int(default_company_id)
    assert employee.lifecycle_status == LifecycleStatus.active.value
    assert employee.title == "市场招募工程师"
    assert employee.slug and employee.workspace_path.endswith(employee.slug)

    # listing 关闭 + 回填招募方
    listing = market_repo.get_listing(db, talent["listing"]["listing_id"])
    assert listing is not None
    assert listing.status == MarketListingStatus.closed.value
    assert listing.close_reason == "recruited"
    assert int(listing.recruited_company_id) == int(default_company_id)
    assert int(listing.recruited_employee_id) == int(employee.id)

    # 履历 + 事件
    joined = db.scalar(
        sa.text(
            "SELECT count(*) FROM career_events WHERE employee_id = :e AND event_type = 'joined'"
        ),
        {"e": employee.id},
    )
    assert int(joined) == 1
    events = [
        row
        for row in db.scalars(sa.select(Event).where(Event.type == "person.recruited"))
        if (row.payload or {}).get("person_id") == person_id
    ]
    assert len(events) == 1
    assert events[0].payload["employee_id"] == int(employee.id)
    assert events[0].company_id == default_company_id

    # I4/I5：人级资产与历史 provenance 逐值不变
    assert _asset_snapshot(db, person_id) == snapshot

    # 招募后自有 person 读面可读（T2.1 的 employee 分支）
    assert client.get(f"/api/v1/persons/{person_id}").status_code == 200


def test_acceptance_b_recruited_employee_recalls_cultivation_knowledge(
    client, db, default_company_id
):
    """验收 B：入职后走**真实 retrieval pipeline** 召回培养期私有知识（无复制）。"""
    talent = _market_talent(client, topic="分布式共识特殊标记")
    person_id = talent["person_id"]
    item = db.scalars(
        sa.select(KnowledgeItem)
        .where(KnowledgeItem.owner_person_id == person_id)
        .order_by(KnowledgeItem.id)
    ).first()
    assert item is not None and item.topic, "培养期知识必须有可检索主题"

    employee_id = client.post(
        f"/api/v1/market/listings/{talent['listing']['listing_id']}/recruit", json={}
    ).json()["employee_id"]

    # 同一行知识仍然 owner_person_id-only（没有复制/改写）
    still = db.get(KnowledgeItem, item.id)
    assert still is not None and still.owner_person_id == person_id
    assert still.owner_employee_id is None

    # 真实检索链路（retrieve_for_task → repo → read_criterion 的人称换算）
    result = retrieval.retrieve_for_task(db, int(employee_id), item.topic, "")
    assert item.topic in result.knowledge, (
        f"入职后应能召回培养期知识：{item.topic!r} not in {result.knowledge}"
    )


def test_recruit_issued_talent_makes_person_readable_to_hiring_company(
    client, db, default_company_id
):
    """发行角色（owner=NULL，市场供给）：招募前 /persons 404（在市场），招募后可读（在职）。"""
    issued = IssuerService().issue(
        db, tier="normal", name="招募发行角色", owner_context_company_id=default_company_id
    )
    assert issued.listing_id is not None
    assert client.get(f"/api/v1/persons/{issued.person_id}").status_code == 404

    response = client.post(f"/api/v1/market/listings/{issued.listing_id}/recruit", json={})
    assert response.status_code == 200, response.text
    assert response.json()["identity_id"] == issued.identity_id
    assert client.get(f"/api/v1/persons/{issued.person_id}").status_code == 200


# ---- 幂等 / 并发 / 边界 ----


def test_double_recruit_is_rejected_and_creates_one_employee(client, db, default_company_id):
    talent = _market_talent(client)
    listing_id = talent["listing"]["listing_id"]

    first = client.post(f"/api/v1/market/listings/{listing_id}/recruit", json={})
    assert first.status_code == 200
    second = client.post(f"/api/v1/market/listings/{listing_id}/recruit", json={})
    assert second.status_code == 409
    assert second.json()["detail"] == "listing_not_active"

    employees = db.scalars(
        sa.select(Employee).where(Employee.person_id == talent["person_id"])
    ).all()
    assert len(employees) == 1, "I8：一人一 employee（uq_employees_person_id 兜底）"


def test_unknown_listing_is_404(client, db, default_company_id):
    assert client.post("/api/v1/market/listings/999999/recruit", json={}).status_code == 404


def test_recruit_with_slot_creates_assignment_in_same_transaction(client, db, default_company_id):
    """带编制招募：同一事务建任职；部门随编制走；role 按编制定义推导。"""
    talent = _market_talent(client)
    departments = _departments(client)
    from app.models.position import PositionDefinition

    definition = db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == default_company_id,
            PositionDefinition.code == "engineer",
        )
    )
    assert definition is not None
    slots = position_service.open_slots(
        db,
        int(definition.id),
        SlotIn(department_id=int(departments["engineering"]), count=1, note="t26"),
        default_company_id,
    )
    slot = slots[0]

    body = client.post(
        f"/api/v1/market/listings/{talent['listing']['listing_id']}/recruit",
        json={"position_slot_id": int(slot.id), "reason": "t26 slot"},
    ).json()
    assert body["position_slot_id"] == int(slot.id) and body["assignment_id"] is not None

    employee = db.get(Employee, body["employee_id"])
    assert int(employee.department_id) == int(slot.department_id), "部门随编制走"
    assert employee.role == (definition.legacy_role or "engineer")
    assignment = position_repo.active_primary_assignment(db, int(employee.id))
    assert assignment is not None and int(assignment.position_slot_id) == int(slot.id)


def test_recruit_rejects_other_company_slot_and_department(client, db, default_company_id):
    talent = _market_talent(client)
    seq = db.scalar(sa.text("SELECT count(*) FROM companies"))
    rival = Company(name="Rival Recruit Co", slug=f"rival-recruit-{seq}")
    db.add(rival)
    db.flush()
    from app.models.organization import Department

    rival_department = Department(
        company_id=int(rival.id), name="Rival Eng", slug=f"rival-eng-{seq}"
    )
    db.add(rival_department)
    db.flush()
    rival_slot = PositionSlot(
        company_id=int(rival.id),
        department_id=int(rival_department.id),
        position_definition_id=int(
            db.scalar(
                sa.text("SELECT id FROM position_definitions WHERE company_id IS NULL LIMIT 1")
            )
            or 1
        ),
        slot_code=f"RIVAL-{seq}",
        headcount_index=1,
    )
    db.add(rival_slot)
    db.commit()

    listing_id = talent["listing"]["listing_id"]
    assert (
        client.post(
            f"/api/v1/market/listings/{listing_id}/recruit",
            json={"position_slot_id": int(rival_slot.id)},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/market/listings/{listing_id}/recruit",
            json={"department_id": int(rival_department.id)},
        ).status_code
        == 404
    )
    # 失败不得留下半个员工，也不得把挂牌关掉（事务回滚）
    assert (
        db.scalars(sa.select(Employee).where(Employee.person_id == talent["person_id"])).all() == []
    )
    listing = market_repo.get_listing(db, listing_id)
    assert listing is not None and listing.status == MarketListingStatus.active.value


# ---- D12 守卫：招募不得复制/改写人级资产 ----


def test_recruitment_service_does_not_touch_person_owned_assets():
    """AST 守卫：招募服务不 import 学习/知识/证据写路径，也不构造能力行/评估 run。"""
    from pathlib import Path

    server_root = Path(__file__).resolve().parents[1]
    path = server_root / "app" / "services" / "recruitment.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    forbidden_prefixes = (
        "app.services.learning",
        "app.repositories.knowledge",
        "app.evidence.normalize",
        "app.services.competency",
        "app.services.assessment",
    )
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    assert not [module for module in imports if module.startswith(forbidden_prefixes)], imports

    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not (calls & {"EmployeeCompetency", "AssessmentRun", "create_person"}), calls

    # 源码里不得出现"复制资产"式写入
    source = path.read_text(encoding="utf-8")
    for token in ("KnowledgeItem(", "CompetencyEvidence(", "EducationEvent("):
        assert token not in source, token
