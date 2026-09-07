"""P9 职位候选分析 —— 分组/排序/批量/隔离（docs/candidate-analysis.md §58 子集）。

锁：
- 候选按 Band 分组再组内排序（RECOMMENDED/VIABLE/DEVELOPMENTAL/NEEDS_EVIDENCE/CRITICAL_GAP）；
- Unknown 候选归 NEEDS_EVIDENCE，绝不当“最差”排在末尾（按 coverage 组内排序）；
- 使用 P8 引擎：同一员工/职位的 candidates fit 与直接 /position-fit 结果一致；
- 默认只分析 AVAILABLE；include_assigned 才加入已任职；
- 无自动任命端点、无全公司 ranking；
- 公司隔离 404；
- 100 员工批量 fit 查询数有界（共享 profile，不逐人重读）。
"""

from __future__ import annotations

import threading

import sqlalchemy as sa
from sqlalchemy import event as sa_event

from app.models.competency import EmployeeCompetency
from app.models.organization import Company, Employee
from app.models.position import PositionAssignment, PositionDefinition, PositionSlot
from app.services import candidate_analysis as candidates

_seq = 0


def _hire(db, company_id: int) -> int:
    global _seq
    _seq += 1
    employee = Employee(
        company_id=company_id,
        name=f"Cand {_seq}",
        slug=f"cand-{_seq}",
        workspace_path=f"/tmp/cand-{_seq}-ws",
        memory_namespace=f"mem-cand-{_seq}",
        lifecycle_status="active",
    )
    db.add(employee)
    db.commit()
    return int(employee.id)


def _set_comp(db, employee_id: int, code: str, score: int | None, confidence: float | None):
    def_id = db.scalar(
        sa.text(
            "SELECT d.id FROM competency_definitions d"
            " JOIN competency_domains dm ON dm.id = d.domain_id"
            " WHERE d.code = :code AND dm.company_id IS NULL"
        ),
        {"code": code},
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


def _engineer_position(db, default_company_id) -> PositionDefinition:
    return db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == default_company_id,
            PositionDefinition.code == "engineer",
        )
    )


def _assign(db, employee_id: int, definition: PositionDefinition) -> PositionSlot:
    from app.models.organization import Department

    department = db.scalar(
        sa.select(Department).where(
            Department.company_id == definition.company_id,
            Department.slug == "engineering",
        )
    )
    slot = PositionSlot(
        company_id=definition.company_id,
        department_id=department.id,
        position_definition_id=definition.id,
        slot_code=f"CAND-{_seq}",
        headcount_index=8800 + _seq,
        administrative_status="active",
    )
    db.add(slot)
    db.flush()
    db.add(
        PositionAssignment(
            employee_id=employee_id,
            position_slot_id=slot.id,
            department_id=department.id,
            assignment_type="primary",
            is_primary=True,
        )
    )
    db.commit()
    return slot


def _bands(client, position_id: int, **params) -> dict:
    return client.get(
        f"/api/v1/position-definitions/{position_id}/candidates", params=params
    ).json()


def test_candidate_bands_and_ordering_are_deterministic(client, db, default_company_id):
    position = _engineer_position(db, default_company_id)
    # 合格者（全部 required 达标且置信足）⇒ RECOMMENDED
    qualified = _hire(db, default_company_id)
    for code, score, conf in (
        ("execution", 90, 0.9),
        ("quality_reliability", 85, 0.8),
        ("analysis_problem_solving", 88, 0.85),
        ("backend_engineering", 86, 0.88),
        ("testing", 78, 0.8),  # ≥ target 75 ⇒ 无 TARGET_GAP
        ("code_quality", 80, 0.8),
    ):
        _set_comp(db, qualified, code, score, conf)
    # required gap 员工（证据足够但 testing 低于 min）⇒ DEVELOPMENTAL
    with_gap = _hire(db, default_company_id)
    _set_comp(db, with_gap, "execution", 90, 0.9)
    _set_comp(db, with_gap, "quality_reliability", 85, 0.8)
    _set_comp(db, with_gap, "analysis_problem_solving", 88, 0.85)
    _set_comp(db, with_gap, "code_quality", 82, 0.8)
    _set_comp(db, with_gap, "backend_engineering", 72, 0.8)
    _set_comp(db, with_gap, "testing", 40, 0.8)  # required testing 低于 min 60
    # critical gap 员工 ⇒ CRITICAL_GAP
    critical = _hire(db, default_company_id)
    _set_comp(db, critical, "backend_engineering", 40, 0.9)
    _set_comp(db, critical, "execution", 85, 0.9)
    # 无证据员工 ⇒ NEEDS_EVIDENCE
    unevaluated = _hire(db, default_company_id)
    db.commit()

    first = _bands(client, int(position.id))
    second = _bands(client, int(position.id))
    for body in (first, second):
        body["meta"]["calculated_at"] = None  # 时间戳不参与确定性比较
    assert first == second, "候选分组/排序必须确定性"
    band_of: dict[int, str] = {}
    for band_group in first["bands"]:
        for item in band_group["candidates"]:
            band_of[item["employee"]["employee_id"]] = band_group["band"]
    assert band_of[qualified] == "RECOMMENDED"
    assert band_of[with_gap] == "DEVELOPMENTAL"
    assert band_of[critical] == "CRITICAL_GAP"
    assert unevaluated in band_of and band_of[unevaluated] == "NEEDS_EVIDENCE"


def test_unknown_candidate_is_not_worst_and_needs_evidence(client, db, default_company_id):
    position = _engineer_position(db, default_company_id)
    skilled = _hire(db, default_company_id)
    _set_comp(db, skilled, "execution", 95, 0.9)
    _set_comp(db, skilled, "quality_reliability", 80, 0.8)
    _set_comp(db, skilled, "analysis_problem_solving", 85, 0.8)
    _set_comp(db, skilled, "code_quality", 82, 0.8)
    # Emma 式：已知 fit 高但覆盖低（只 2 项）
    unknown = _hire(db, default_company_id)
    _set_comp(db, unknown, "execution", 95, 0.9)
    _set_comp(db, unknown, "testing", 90, 0.9)
    db.commit()
    body = _bands(client, int(position.id))
    needs = next(group for group in body["bands"] if group["band"] == "NEEDS_EVIDENCE")
    # 覆盖低归 NEEDS_EVIDENCE（Unknown != Bad，绝不当“最差”排末尾）
    assert any(item["employee"]["employee_id"] == unknown for item in needs["candidates"])
    # 组内按 coverage 有序且全部可解释
    coverage = [item["fit"]["required_required_coverage"] for item in needs["candidates"]]
    assert coverage == sorted(coverage, reverse=True)


def test_assigned_excluded_by_default_and_included_on_demand(client, db, default_company_id):
    position = _engineer_position(db, default_company_id)
    employee_id = _hire(db, default_company_id)
    _set_comp(db, employee_id, "execution", 90, 0.9)
    _assign(db, employee_id, position)
    default_body = _bands(client, int(position.id))
    ids_default = {
        item["employee"]["employee_id"]
        for group in default_body["bands"]
        for item in group["candidates"]
    }
    assert employee_id not in ids_default, "默认候选范围是 AVAILABLE"
    with_assigned = _bands(client, int(position.id), include_assigned=True)
    ids_assigned = {
        item["employee"]["employee_id"]
        for group in with_assigned["bands"]
        for item in group["candidates"]
    }
    assert employee_id in ids_assigned
    # 已任职者 current_position 必须可见（内部调岗语境）
    item = next(
        item
        for group in with_assigned["bands"]
        for item in group["candidates"]
        if item["employee"]["employee_id"] == employee_id
    )
    assert item["employee"]["current_position"] is not None


def test_no_auto_assignment_endpoint(client):
    from fastapi.routing import APIRoute

    for route in client.app.routes:
        if not isinstance(route, APIRoute):
            continue
        if "candidates" in route.path and "POST" in route.methods:
            raise AssertionError(f"候选只读端点不应有 POST 写面：{route.path}")


def test_company_isolation(client, db, default_company_id):
    other = Company(name="CandOther", slug=f"cand-other-{_seq}", description="")
    db.add(other)
    db.commit()
    foreign = _hire(db, int(other.id))
    _set_comp(db, foreign, "execution", 95, 0.9)
    position = _engineer_position(db, default_company_id)
    body = _bands(client, int(position.id))
    ids = {
        item["employee"]["employee_id"] for group in body["bands"] for item in group["candidates"]
    }
    assert foreign not in ids, "Company B 员工绝不能进入 Company A 的候选分析"


def test_candidate_batch_fit_matches_direct_engine(db, default_company_id):
    """候选 fit 必须与 P8 一对一同结果（禁止第二套公式）。"""
    position = _engineer_position(db, default_company_id)
    employee_id = _hire(db, default_company_id)
    _set_comp(db, employee_id, "execution", 90, 0.9)
    _set_comp(db, employee_id, "testing", 70, 0.8)
    db.commit()
    body = candidates.analyze(db, position_definition_id=int(position.id))
    item = next(
        item
        for group in body["bands"]
        for item in group["candidates"]
        if item["employee"]["employee_id"] == employee_id
    )
    from app.talent.fit import service as fit_service

    direct = fit_service.calculate_fit(
        db, employee_id=employee_id, position_definition_id=int(position.id)
    )
    assert item["fit"]["known_fit_score"] == direct.known_fit_score
    assert item["fit"]["fit_confidence"] == direct.fit_confidence
    assert item["fit"]["required_required_coverage"] == direct.required_coverage


def test_candidates_100_employees_query_bounded(client, db, default_company_id):
    """批量守卫：100 名候选的 fit 查询数有界（共享 profile，不随人数线性 N+1）。"""
    position = _engineer_position(db, default_company_id)
    ids = [_hire(db, default_company_id) for _ in range(100)]
    for index, employee_id in enumerate(ids):
        _set_comp(db, employee_id, "execution", 60 + (index % 30), 0.8)
    db.commit()

    def _analyze_count() -> int:
        owner = threading.get_ident()
        statements: list[str] = []
        engine = db.get_bind()

        def _count(conn, cursor, statement, parameters, context, executemany):
            if threading.get_ident() == owner:
                statements.append(statement)

        sa_event.listen(engine, "before_cursor_execute", _count)
        try:
            candidates.analyze(db, position_definition_id=int(position.id))
        finally:
            sa_event.remove(engine, "before_cursor_execute", _count)
        return len(statements)

    body = candidates.analyze(db, position_definition_id=int(position.id))
    total = sum(group["count"] for group in body["bands"])
    assert total >= 90
    base = _analyze_count()
    for _ in range(50):
        _hire(db, default_company_id)
    db.commit()
    grown = _analyze_count()
    # 员工数 +50% 查询增量 ≤ 6 ⇒ 不做逐候选 N+1（基地中有按编制数收敛的固定查询）
    assert grown <= base + 6, f"候选批量随员工数爆炸：{base} → {grown}"
