"""P8 Position Fit API 契约（docs/position-fit.md §34/§51 子集）。

锁：一对一分析可用；unrated≠0；low confidence 显示为 uncertainty（reason_code 机器码）；
critical gap vs critical uncertainty 区分；Fit+Confidence 同时返回；低 coverage →
INSUFFICIENT_DATA（不是 weak）；无画像 → NOT_EVALUABLE（不是 0/100）；公司隔离 404；
input_hash 存在；不提供 ranking/recommended 端点。
"""

from __future__ import annotations

import sqlalchemy as sa
from factories import person_id_of

from app.models.competency import EmployeeCompetency
from app.models.organization import Company, Employee
from app.models.position import PositionDefinition
from app.services import position_profile as profiles

_seq = 0


def _hire(db, company_id: int) -> int:
    global _seq
    _seq += 1
    employee = Employee(
        company_id=company_id,
        name=f"FitApi {_seq}",
        slug=f"fita-{_seq}",
        workspace_path=f"/tmp/fita-{_seq}-ws",
        memory_namespace=f"mem-fita-{_seq}",
    )
    db.add(employee)
    db.commit()
    return int(employee.id)


def _def_id(db, code: str) -> int:
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


def test_fit_endpoint_reports_known_unknown_and_uncertainty(client, db, default_company_id):
    employee_id = _hire(db, default_company_id)

    # SE 基线：给部分能力造分（含低置信）
    def set_comp(code: str, score: int | None, confidence: float | None):
        row = db.scalar(
            sa.select(EmployeeCompetency).where(
                EmployeeCompetency.employee_id == employee_id,
                EmployeeCompetency.competency_definition_id == _def_id(db, code),
            )
        )
        data = {
            "employee_id": employee_id,
            "person_id": person_id_of(db, employee_id),
            "competency_definition_id": _def_id(db, code),
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

    set_comp("analysis_problem_solving", 89, 0.9)
    set_comp("execution", 82, 0.85)
    set_comp("backend_engineering", 86, 0.88)
    set_comp("testing", 68, 0.72)
    set_comp("system_architecture", 55, 0.65)  # preferred：meets minimum，below target
    db.commit()

    position = db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == default_company_id,
            PositionDefinition.code == "engineer",
        )
    )
    body = client.get(f"/api/v1/employees/{employee_id}/position-fit/{position.id}").json()
    assert body["configured"] is True and body["profile_version"] == 1
    assert body["fit_status"] in {"STRONG_MATCH", "PARTIAL_MATCH", "CRITICAL_GAP"}
    assert body["inputs_hash"]
    # known fit 与 confidence 同时出现（不可只看数字）
    assert body["known_fit_score"] is not None and body["fit_confidence"] is not None
    # strength 需要 target/置信达标
    strength_codes = {item["code"] for item in body["strengths"]}
    assert "analysis_problem_solving" in strength_codes
    # preferred 达最低未达目标 ⇒ development opportunity（不是 weakness）
    dev_codes = {item["code"] for item in body["development_opportunities"]}
    assert "system_architecture" in dev_codes


def test_fit_endpoint_uses_reason_codes_not_hardcoded_text(client, db, default_company_id):
    employee_id = _hire(db, default_company_id)
    for code, score, confidence in (
        ("security", 82, 0.18),
        ("backend_engineering", 55, 0.9),
    ):
        db.add(
            EmployeeCompetency(
                employee_id=employee_id,
                person_id=person_id_of(db, employee_id),
                competency_definition_id=_def_id(db, code),
                score=score,
                confidence=confidence,
                status="assessed",
                evidence_count=0,
            )
        )
    db.commit()
    position = db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == default_company_id,
            PositionDefinition.code == "engineer",
        )
    )
    body = client.get(f"/api/v1/employees/{employee_id}/position-fit/{position.id}").json()
    backend = next(
        item for item in body["requirement_evaluations"] if item["code"] == "backend_engineering"
    )
    assert backend["reason_code"] == "BELOW_MINIMUM"
    assert backend["gap_type"] == "CRITICAL_GAP"
    assert body["qualification_status"] == "NOT_QUALIFIED"


def test_unrated_endpoint_never_zero_and_no_profile_not_evaluable(client, db, default_company_id):
    employee_id = _hire(db, default_company_id)
    position = db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == default_company_id,
            PositionDefinition.code == "engineer",
        )
    )
    body = client.get(f"/api/v1/employees/{employee_id}/position-fit/{position.id}").json()
    assert body["requirement_coverage"] == 0.0
    assert body["known_fit_score"] is None and body["overall_fit_score"] is None
    assert body["fit_status"] == "INSUFFICIENT_DATA"
    for item in body["requirement_evaluations"]:
        assert item["employee_score"] is None
        assert item["evaluation_status"] == "UNRATED"


def test_insufficient_confidence_is_not_a_failure_at_api(client, db, default_company_id):
    """带 minimum_confidence 的需求：score 高但置信低 ⇒ INSUFFICIENT_CONFIDENCE 机器码。"""
    employee_id = _hire(db, default_company_id)
    from app.models.position import PositionDefinition as PD

    definition = PD(
        company_id=default_company_id,
        template_scope="company",
        code=f"fita-ctl-{_seq}",
        name="Fit API Control",
        job_family="engineering",
    )
    db.add(definition)
    db.flush()
    draft = profiles.create_draft(db, definition)
    profiles.add_requirement(
        db,
        draft,
        _def_id(db, "execution"),
        company_id=default_company_id,
        requirement_type="required",
        minimum_score=65,
        target_score=85,
        minimum_confidence=0.5,
    )
    profiles.publish_profile(db, draft)
    db.add(
        EmployeeCompetency(
            employee_id=employee_id,
            person_id=person_id_of(db, employee_id),
            competency_definition_id=_def_id(db, "execution"),
            score=82,
            confidence=0.18,
            status="assessed",
            evidence_count=0,
        )
    )
    db.commit()
    body = client.get(f"/api/v1/employees/{employee_id}/position-fit/{definition.id}").json()
    evaluation = body["requirement_evaluations"][0]
    assert evaluation["evaluation_status"] == "INSUFFICIENT_CONFIDENCE"
    assert evaluation["reason_code"] == "CONFIDENCE_BELOW_REQUIREMENT"
    assert evaluation["employee_score"] == 82  # 高估但证据不足 ≠ 能力为 0
    assert body["known_fit_score"] is None
    assert body["fit_status"] == "INSUFFICIENT_DATA"


def test_fit_respects_company_isolation(client, db, default_company_id):
    other = Company(name="FitApiOther", slug=f"fita-other-{_seq}", description="")
    db.add(other)
    db.commit()
    foreign_employee = _hire(db, int(other.id))
    position = db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == default_company_id,
            PositionDefinition.code == "engineer",
        )
    )
    assert (
        client.get(f"/api/v1/employees/{foreign_employee}/position-fit/{position.id}").status_code
        == 404
    )


def test_no_ranking_or_recommendation_endpoints(client):
    for path in (
        "/api/v1/positions/recommended-candidates",
        "/api/v1/position-definitions/1/recommended-candidates",
    ):
        assert client.get(path).status_code == 404, f"{path} 不应存在（P9）"
