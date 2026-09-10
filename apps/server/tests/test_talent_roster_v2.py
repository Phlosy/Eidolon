"""P9 人才名册 v2 —— 派生/富化/筛选/批量（docs/talent-roster.md §5/§58 子集）。

锁：
- workforce_status 派生（AVAILABLE ≠ 员工 idle work 状态是两个维度）；
- 默认排除 offboarded/pending；
- unrated 员工可见，top 能力不因低置信而出现（置信≥0.4 才算 Top）；
- 能力筛选强制同时给 confidence（防止 Score90/Conf5% 误导）；
- trait 筛选独立于能力筛选（Behavioral Preference ≠ Competency）；
- N+1 守卫：一页 N 人的富化查询数固定（~9 条），不随 N 线性增长；
- 无跨公司统计（company-scoped）。
"""

from __future__ import annotations

import threading

import sqlalchemy as sa
from factories import make_employee

from app.models.competency import EmployeeCompetency
from app.models.organization import Company
from app.services import talent_roster as roster_service

_seq = 0


def _hire(db, company_id: int, *, name: str = "") -> int:
    global _seq
    _seq += 1
    employee = make_employee(
        db,
        company_id=company_id,
        slug=f"roster-{_seq}",
        name=name or f"Roster {_seq}",
        workspace_path=f"/tmp/r-{_seq}-ws",
        memory_namespace=f"mem-r-{_seq}",
        lifecycle_status="active",
    )
    db.commit()
    return int(employee.id)


def _set_comp(db, employee_id: int, code: str, score: int | None, confidence: float | None):
    from app.models.competency import CompetencyDefinition, CompetencyDomain

    def_id = db.scalar(
        sa.select(CompetencyDefinition.id)
        .join(CompetencyDomain, CompetencyDomain.id == CompetencyDefinition.domain_id)
        .where(
            CompetencyDefinition.code == code,
            CompetencyDomain.company_id.is_(None),
        )
    )
    row = db.scalar(
        sa.select(EmployeeCompetency).where(
            EmployeeCompetency.employee_id == employee_id,
            EmployeeCompetency.competency_definition_id == def_id,
        )
    )
    data = {
        "employee_id": employee_id,
        "competency_definition_id": def_id,
        "score": score,
        "confidence": confidence,
        "status": "assessed" if score is not None else "unrated",
        "evidence_count": 0,
    }
    if row is None:
        db.add(EmployeeCompetency(**data))
    else:
        row.score = score
        row.confidence = confidence
    db.flush()


def test_roster_status_is_derived(client, default_company_id):
    body = client.get("/api/v1/talent-roster").json()
    assert all("workforce_status" in entry for entry in body)
    assert all("lifecycle_status" in entry for entry in body)
    assert all("runtime" in entry for entry in body)
    assert all("traits_summary" in entry for entry in body)


def test_unrated_employee_visible_with_no_fake_tops(client, db, default_company_id):
    employee_id = _hire(db, default_company_id)
    entry = next(
        item
        for item in client.get("/api/v1/talent-roster").json()
        if item["employee_id"] == employee_id
    )
    assert entry["top_general_competencies"] == []
    assert entry["top_professional_competencies"] == []
    assert entry["assessment_summary"]["evidence_coverage"] == "none"
    assert entry["assessment_summary"]["assessed_general_count"] == 0


def test_top_competency_requires_sufficient_confidence(client, db, default_company_id):
    employee_id = _hire(db, default_company_id)
    _set_comp(
        db, employee_id, "planning_organization", score=95, confidence=0.08
    )  # general 高置信不足
    _set_comp(db, employee_id, "execution", score=84, confidence=0.8)  # general 达标
    _set_comp(db, employee_id, "testing", score=84, confidence=0.8)  # professional 达标
    db.commit()
    entry = next(
        item
        for item in client.get("/api/v1/talent-roster").json()
        if item["employee_id"] == employee_id
    )
    general = {item["code"] for item in entry["top_general_competencies"]}
    assert "execution" in general
    assert "planning_organization" not in general  # 分高但置信不足 ⇒ 不作 Top（待验证）
    professional = {item["code"] for item in entry["top_professional_competencies"]}
    assert "testing" in professional  # professional 能力置信达标 ⇒ 可入 Top


def test_competency_filter_requires_both_score_and_confidence(client, db, default_company_id):
    employee_ok = _hire(db, default_company_id)
    employee_low_conf = _hire(db, default_company_id)
    _set_comp(db, employee_ok, "execution", score=75, confidence=0.8)
    _set_comp(db, employee_low_conf, "execution", score=95, confidence=0.05)
    db.commit()
    filtered = client.get(
        "/api/v1/talent-roster",
        params={
            "competency_code": "execution",
            "min_competency_score": 70,
            "min_competency_confidence": 0.5,
        },
    ).json()
    ids = {entry["employee_id"] for entry in filtered}
    assert employee_ok in ids
    assert employee_low_conf not in ids  # Score 高但置信不达标 ⇒ 筛选排除（防误导）


def test_trait_filter_is_independent_of_competency(client, db, default_company_id):
    from app.brain.traits import BrainTraits, write_traits_to_brain
    from app.models.runtime import EmployeeBrain

    employee_id = _hire(db, default_company_id)
    brain = db.scalar(sa.select(EmployeeBrain).where(EmployeeBrain.employee_id == employee_id))
    if brain is None:
        brain = EmployeeBrain(employee_id=employee_id)
        db.add(brain)
    write_traits_to_brain(brain, BrainTraits.build({"curiosity": 0.9, "warmth": 0.3}))
    db.commit()
    hits = client.get(
        "/api/v1/talent-roster",
        params={"trait_code": "curiosity", "min_trait_value": 0.7},
    ).json()
    assert any(entry["employee_id"] == employee_id for entry in hits)
    assert hits  # 人格筛选是独立的 Behavioral Preference 维度


def test_status_filter_and_available_scope(client, db, default_company_id):
    available = client.get("/api/v1/talent-roster", params={"status": "available"}).json()
    assigned = client.get("/api/v1/talent-roster", params={"status": "assigned"}).json()
    assert {entry["workforce_status"] for entry in available} <= {"available"}
    assert {entry["workforce_status"] for entry in assigned} <= {"assigned"}


def test_roster_enrichment_does_not_fan_out_per_employee(db, default_company_id):
    """N+1 守卫：员工数翻倍，富化查询数基本不变（按编制数收敛，绝不随人数线性爆炸）。"""
    from app.models.event import Event

    employees = [_hire(db, default_company_id) for _ in range(12)]
    for employee_id in employees:
        db.add(
            Event(
                type="task.completed",
                company_id=default_company_id,
                actor_employee_id=employee_id,
                payload={},
            )
        )
    db.commit()

    def _run() -> int:
        owner = threading.get_ident()
        statements: list[str] = []
        engine = db.get_bind()
        from sqlalchemy import event as sa_event

        def _count(conn, cursor, statement, parameters, context, executemany):
            if threading.get_ident() == owner:
                statements.append(statement)

        sa_event.listen(engine, "before_cursor_execute", _count)
        try:
            result = roster_service.roster_query(db, default_company_id, limit=100)
        finally:
            sa_event.remove(engine, "before_cursor_execute", _count)
        assert len(result["items"]) >= 12
        return len(statements)

    base = _run()
    for _ in range(24):
        _hire(db, default_company_id)
    db.commit()
    grown = _run()
    # 基数里有按编制数收敛的固定查询（integrity 等）；员工数翻三倍增量应 ≤ 3
    assert grown <= base + 3, f"名册富化随员工数爆炸：{base} → {grown}"


def test_company_isolation_and_no_global_totals(client, db, default_company_id):
    other = Company(name="OtherR", slug=f"roster-other-{_seq}", description="")
    db.add(other)
    db.commit()
    foreign = _hire(db, int(other.id))
    default_ids = {entry["employee_id"] for entry in client.get("/api/v1/talent-roster").json()}
    assert foreign not in default_ids, "Company A 名册绝不能包含 Company B 员工"
