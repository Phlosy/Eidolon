"""P5 读 API 契约（docs/competency-system.md §8 的 P5 子集）。

锁：
- general = 10 维全量；未评估维度 score/confidence 必须是 null（**不能序列化成 0**），
  status=unrated、trend=null、trend_direction=unknown（不能假装 STABLE）；
- professional = 动态目录，只有被评估过的维度出现；
- traits 8 维数据契约（value 0..1 + display + affects_execution，只 curiosity 生效）；
- evidence 读面可反查（带 competency code/name）；
- 写面不存在：这些端点只读（score 没有 PATCH 入口，前面聚合器测试已证）；
- 公司边界：别人的员工 → 404。
"""

from __future__ import annotations

import sqlalchemy as sa
from factories import make_employee, person_id_of

from app.models.competency import CompetencyEvidence
from app.models.organization import Company
from app.services import competency as svc

GENERAL_CODES = {
    "analysis_problem_solving",
    "planning_organization",
    "execution",
    "communication",
    "collaboration",
    "management_leadership",
    "decision_making",
    "learning_growth",
    "quality_reliability",
    "efficiency_resource_awareness",
}


_seq = 0


def _hire(db, company_id: int) -> int:
    global _seq
    _seq += 1
    employee = make_employee(
        db,
        company_id=company_id,
        slug=f"read-api-{company_id}-{_seq}",
        name=f"Read API Tester {_seq}",
        workspace_path=f"/tmp/read-{company_id}-{_seq}-ws",
        memory_namespace=f"mem-read-{company_id}-{_seq}",
    )
    db.commit()
    return int(employee.id)


def _def_id(db, code: str = "testing") -> int:
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


def test_domain_and_definition_catalogs_are_readable(client):
    domains = client.get("/api/v1/competency-domains").json()
    kinds = {domain["code"]: domain["kind"] for domain in domains}
    assert kinds.get("general") == "general"
    for code in (
        "software_engineering",
        "research",
        "product",
        "management",
        "quality_assurance",
    ):
        assert kinds.get(code) == "professional"

    general_domain = next(domain for domain in domains if domain["code"] == "general")
    definitions = client.get(
        "/api/v1/competencies", params={"domain_id": general_domain["id"]}
    ).json()
    assert {item["code"] for item in definitions} == GENERAL_CODES
    missing = client.get("/api/v1/competencies", params={"domain_id": 999_999})
    assert missing.status_code == 404


def test_unrated_competencies_never_serialize_as_zero(client, db, default_company_id):
    employee_id = _hire(db, default_company_id)
    body = client.get(f"/api/v1/employees/{employee_id}/competencies").json()
    general = body["general"]
    assert len(general) == 10
    for item in general:
        assert item["score"] is None, f"{item['code']} 未评估不能编 0 分"
        assert item["confidence"] is None
        assert item["evidence_count"] == 0  # 真的没有证据（absence 是算出来的）
        assert item["status"] == "unrated"
        assert item["trend"] is None
        assert item["trend_direction"] == "unknown"  # 不是 STABLE
    assert body["professional"] == []  # 动态目录：没有被评估的维度


def test_assessed_competency_appears_under_professional(client, db, default_company_id):
    employee_id = _hire(db, default_company_id)
    definition_id = _def_id(db, "testing")  # software_engineering 域
    db.add(
        CompetencyEvidence(
            employee_id=employee_id,
            person_id=person_id_of(db, employee_id),
            competency_definition_id=definition_id,
            source_kind="test",
            source_ref="TEST-42 18/18 passed",
            signal=90,
        )
    )
    db.commit()
    svc.assess_employee_competencies(db, employee_id, commit=True)

    body = client.get(f"/api/v1/employees/{employee_id}/competencies").json()
    professional = body["professional"]
    assert len(professional) == 1
    entry = professional[0]
    assert entry["code"] == "testing"
    assert entry["domain_code"] == "software_engineering"
    assert entry["score"] is not None and 0 <= entry["score"] <= 100
    assert entry["confidence"] is not None
    assert entry["evidence_count"] == 1
    assert entry["status"] in {"assessed", "provisional"}
    # general 保持 10 维，且这条 professional 评估不污染通用能力
    assert len(body["general"]) == 10


def test_traits_endpoint_exposes_the_eight_dimension_contract(client, db, default_company_id):
    employee_id = _hire(db, default_company_id)
    body = client.get(f"/api/v1/employees/{employee_id}/traits").json()
    codes = [item["code"] for item in body]
    assert codes == [
        "curiosity",
        "warmth",
        "independence",
        "conscientiousness",
        "collaboration",
        "risk_tolerance",
        "adaptability",
        "creativity",
    ]
    for item in body:
        assert 0.0 <= item["value"] <= 1.0
        assert item["display"] == round(item["value"] * 100)
        assert item["label"] and item["description"]
    by_code = {item["code"]: item for item in body}
    assert by_code["curiosity"]["affects_execution"] is True
    for code in codes[1:]:
        assert by_code[code]["affects_execution"] is False, f"{code} 未接行为"


def test_evidence_readback_is_traceable(client, db, default_company_id):
    employee_id = _hire(db, default_company_id)
    definition_id = _def_id(db, "testing")
    evidence = CompetencyEvidence(
        employee_id=employee_id,
        person_id=person_id_of(db, employee_id),
        competency_definition_id=definition_id,
        source_kind="review",
        source_ref="REV-7 / design review",
        signal=85,
    )
    db.add(evidence)
    db.commit()

    rows = client.get(f"/api/v1/employees/{employee_id}/competency-evidence").json()
    assert len(rows) == 1
    assert rows[0]["source_ref"] == "REV-7 / design review"
    assert rows[0]["competency_code"] == "testing"
    assert rows[0]["competency_name"] == "测试工程"
    assert rows[0]["employee_id"] == employee_id


def test_read_endpoints_respect_the_company_boundary(client, db, default_company_id):
    other = Company(name="Other Co", slug="other-read-co", description="")
    db.add(other)
    db.flush()
    foreign_employee_id = _hire(db, int(other.id))
    for path in (
        f"/api/v1/employees/{foreign_employee_id}/competencies",
        f"/api/v1/employees/{foreign_employee_id}/traits",
        f"/api/v1/employees/{foreign_employee_id}/competency-evidence",
    ):
        assert client.get(path).status_code == 404, f"{path} 不该跨公司可读"
