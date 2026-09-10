"""P6 Assessment/Explanation API 契约（docs/evidence-pipeline.md §31~§33）。

锁：
- 档案列表/详情（criterion → 多能力映射 + contribution_weight 可读）；
- run 手动触发走引擎（不能提交最终分数）；返回带 results（criterion/contribution）；
- 考核历史列表（profile_version 被保留）；
- explanation 端点回答“为什么是这个分”（历史 run + 证据 + 来源分布 + criterion 贡献）；
- evidence 过滤（competency / source_type / run / 时间）；
- 公司边界 404；ADR-12：unrated explanation 不给 0 分。
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from factories import make_employee, person_id_of

from app.models.competency import CompetencyEvidence
from app.models.organization import Department, Employee
from app.models.position import PositionAssignment, PositionDefinition, PositionSlot

_seq = 0


def _hire(db, default_company_id: int) -> int:
    global _seq
    _seq += 1
    employee = make_employee(
        db,
        company_id=default_company_id,
        slug=f"api-tester-{_seq}",
        name=f"Api Tester {_seq}",
        workspace_path=f"/tmp/api-{_seq}-ws",
        memory_namespace=f"mem-api-{_seq}",
    )
    db.commit()
    return int(employee.id)


def _assign_engineer(db, employee_id: int) -> None:
    department = db.scalar(
        sa.select(Department).where(
            Department.company_id == db.get(Employee, employee_id).company_id,
            Department.slug == "engineering",
        )
    )
    definition = db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == db.get(Employee, employee_id).company_id,
            PositionDefinition.code == "engineer",
        )
    )
    slot = PositionSlot(
        company_id=definition.company_id,
        department_id=department.id,
        position_definition_id=definition.id,
        slot_code=f"API-{_seq}",
        headcount_index=9500 + _seq,
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
    db.flush()


def _def(db, code: str) -> int:
    return int(
        db.scalar(
            sa.text(
                "SELECT d.id FROM competency_definitions d"
                " JOIN competency_domains dm ON dm.id = d.domain_id"
                " WHERE d.code = :code AND dm.company_id IS NULL"
            ),
            {"code": code},
        )
    )


def _seed_evidence_and_run(client, db, default_company_id) -> tuple[int, int]:
    """造一名 engineer 员工 + 证据 + 手动 run；返回 (employee_id, run_id)。"""
    employee_id = _hire(db, default_company_id)
    _assign_engineer(db, employee_id)
    db.add(
        CompetencyEvidence(
            employee_id=employee_id,
            person_id=person_id_of(db, employee_id),
            competency_definition_id=_def(db, "execution"),
            source_kind="task",
            source_ref="T-1",
            signal=85,
        )
    )
    db.add(
        CompetencyEvidence(
            employee_id=employee_id,
            person_id=person_id_of(db, employee_id),
            competency_definition_id=_def(db, "quality_reliability"),
            source_kind="review",
            source_ref="R-1",
            signal=88,
        )
    )
    db.commit()
    response = client.post(f"/api/v1/employees/{employee_id}/assessments/run", json={})
    assert response.status_code == 200, response.text
    return employee_id, int(response.json()["id"])


def test_profile_endpoints_expose_criteria_and_multi_competency_mappings(client):
    profiles = client.get("/api/v1/assessment-profiles").json()
    codes = {profile["code"] for profile in profiles}
    assert {"software_engineer", "qa_engineer", "researcher", "manager"} <= codes
    engineer = next(profile for profile in profiles if profile["code"] == "software_engineer")
    detail = client.get(f"/api/v1/assessment-profiles/{engineer['id']}").json()
    assert detail["version"] == 1
    by_code = {criterion["code"]: criterion for criterion in detail["criteria"]}
    assert "delivery_reliability" in by_code
    mappings = {
        comp["code"]: comp["contribution_weight"]
        for comp in by_code["delivery_reliability"]["competencies"]
    }
    assert mappings.get("execution") == pytest.approx(0.6)
    assert mappings.get("quality_reliability") == pytest.approx(0.4)


def test_manual_run_endpoint_returns_auditable_run_with_results(client, db, default_company_id):
    employee_id, run_id = _seed_evidence_and_run(client, db, default_company_id)
    run = client.get(f"/api/v1/assessments/{run_id}").json()
    assert run["profile_code"] == "software_engineer"
    assert run["profile_version"] == 1
    assert run["evidence_count"] >= 2
    assert run["status"] == "completed"
    kinds = {result["kind"] for result in run["results"]}
    assert {"criterion", "contribution"} <= kinds
    assert run["outputs"], "run 必须带能力输出"


def test_employee_assessment_history_lists_runs(client, db, default_company_id):
    employee_id, _run_id = _seed_evidence_and_run(client, db, default_company_id)
    runs = client.get(f"/api/v1/employees/{employee_id}/assessments").json()
    assert runs and runs[0]["profile_version"] == 1
    assert "inputs_hash" in runs[0] and "assessment_type" in runs[0]
    # 列表是摘要，不带 outputs（详情才带）；也没有“提交最终分数”字段
    assert "outputs" not in runs[0]
    assert "score" not in runs[0] and "confidence" not in runs[0]


def test_manual_run_must_use_existing_profile_or_position(client, db, default_company_id):
    employee_id = _hire(db, default_company_id)
    missing = client.post(
        f"/api/v1/employees/{employee_id}/assessments/run",
        json={"profile_code": "no_such_profile"},
    )
    assert missing.status_code in (404, 422)
    # 未任职 → 无默认档案 → 422（不是静默跑出假分）
    unassigned = client.post(f"/api/v1/employees/{employee_id}/assessments/run", json={})
    assert unassigned.status_code == 422


def test_explanation_endpoint_answers_why_this_score(client, db, default_company_id):
    employee_id, _run_id = _seed_evidence_and_run(client, db, default_company_id)
    body = client.get(f"/api/v1/employees/{employee_id}/competencies/execution/explanation").json()
    assert body["code"] == "execution"
    assert body["score"] is not None and 0 <= body["score"] <= 100
    assert body["confidence"] is not None and 0 <= body["confidence"] <= 1
    assert body["evidence_count"] >= 1
    assert body["assessment_history"], "解释必须带考核历史"
    assert body["recent_evidence"], "解释必须带最近证据"
    assert body["source_distribution"], "解释必须带来源分布"
    # 可解释：有 criterion 贡献行（execution 被多个 criterion 贡献）
    assert any(item.get("contribution") is not None for item in body["recent_criterion_results"])


def test_unrated_explanation_never_serializes_zero(client, db, default_company_id):
    employee_id = _hire(db, default_company_id)
    body = client.get(
        f"/api/v1/employees/{employee_id}/competencies/communication/explanation"
    ).json()
    assert body["score"] is None
    assert body["confidence"] is None
    assert body["status"] == "unrated"
    assert body["trend"] is None and body["trend_direction"] == "unknown"


def test_evidence_endpoint_supports_filters(client, db, default_company_id):
    employee_id, _run_id = _seed_evidence_and_run(client, db, default_company_id)
    rows = client.get(f"/api/v1/employees/{employee_id}/competency-evidence").json()
    assert len(rows) == 2
    only_tasks = client.get(
        f"/api/v1/employees/{employee_id}/competency-evidence",
        params={"source_type": "task"},
    ).json()
    assert len(only_tasks) == 1 and only_tasks[0]["source_kind"] == "task"
    only_execution = client.get(
        f"/api/v1/employees/{employee_id}/competency-evidence",
        params={"competency": "execution"},
    ).json()
    assert len(only_execution) == 1 and only_execution[0]["competency_code"] == "execution"


def test_assessment_endpoints_respect_company_boundary(client, db, default_company_id):
    from app.models.organization import Company

    other = Company(name="OtherCo", slug=f"other-api-{_seq}", description="")
    db.add(other)
    db.flush()
    foreign = _hire(db, int(other.id))
    assert client.get(f"/api/v1/employees/{foreign}/assessments").status_code == 404
    assert client.post(f"/api/v1/employees/{foreign}/assessments/run", json={}).status_code == 404
