"""T2.5 Person-scoped Fit（docs/t2-talent-market-design.md §9 / plan §4.6）。

锁：
- **对拍**：同一 person 的 person 入口与 employee 入口结果逐字段一致（含 inputs_hash）——
  一套引擎两个入口，不是两套实现；
- 市场候选人（person-only，无 employee 行）可算 Fit；
- 缺证据 ⇒ UNRATED / INSUFFICIENT_DATA，**绝不伪造 0**；
- 市场 Fit 公开投影：score/confidence/coverage/missing 齐全，
  **无** inputs_hash / owner id / requirement_id / competency_definition_id；
- 市场搜索带 position_definition_id：附 Fit 摘要 + 按匹配度排序，**未知不剔除**（Unknown != Bad）；
- 公司边界：别家公司职位 / 别家公司 person 一律 404；非 active 挂牌不能算 Fit。
"""

from __future__ import annotations

import sqlalchemy as sa
from factories import make_employee

from app.models.competency import EmployeeCompetency
from app.models.cultivation import CharacterProfile
from app.models.enums import MarketParticipantKind
from app.models.organization import Company
from app.models.person import Person
from app.models.position import PositionDefinition
from app.repositories import market as market_repo
from app.repositories import persons as person_repo
from app.talent.fit import service as fit_service
from app.talent.fit.models import EvaluationStatus, FitStatus

_seq = 0


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


def _engineer_position(db, company_id: int) -> PositionDefinition:
    position = db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == company_id,
            PositionDefinition.code == "engineer",
        )
    )
    assert position is not None, "seed 应提供 engineer 职位"
    return position


def _set_competency(db, person_id: int, employee_id: int | None, code: str, score, confidence):
    db.add(
        EmployeeCompetency(
            employee_id=employee_id,
            person_id=person_id,
            competency_definition_id=_def_id(db, code),
            score=score,
            confidence=confidence,
            status="assessed" if score is not None else "unrated",
            evidence_count=3 if score is not None else 0,
        )
    )
    db.flush()


def _person(db, slug: str, *, company_id: int | None = None, ready: bool = False) -> Person:
    global _seq
    _seq += 1
    person = person_repo.create_person(db, slug=f"{slug}-{_seq}", name=f"{slug} {_seq}")
    if company_id is not None:
        db.add(
            CharacterProfile(
                person_id=int(person.id),
                identity_id=f"CH-T25{_seq:08d}",
                origin="trained",
                owner_company_id=company_id,
                lifecycle="ready" if ready else "cultivating",
            )
        )
        db.flush()
    return person


def _active_listing(db, person_id: int, company_id: int, *, tier: str | None = None):
    participant = market_repo.ensure_participant(
        db, kind=MarketParticipantKind.player_company.value, company_id=company_id
    )
    listing, _created = market_repo.create_active_listing(
        db, person_id=person_id, listed_by_participant_id=int(participant.id), quality_tier=tier
    )
    db.commit()
    return listing


# ---- 对拍：person 入口 ≡ employee 入口 ----


def test_person_and_employee_paths_agree(client, db, default_company_id):
    employee = make_employee(db, company_id=default_company_id, slug="t25-parity")
    db.commit()
    person_id = int(employee.person_id)
    for code, score, confidence in (
        ("analysis_problem_solving", 78, 0.8),
        ("execution", 62, 0.7),
    ):
        _set_competency(db, person_id, int(employee.id), code, score, confidence)
    db.commit()

    position = _engineer_position(db, default_company_id)
    employee_result = fit_service.calculate_fit(
        db, employee_id=int(employee.id), position_definition_id=int(position.id)
    )
    person_result = fit_service.calculate_person_fit(
        db,
        person_id=person_id,
        position_definition_id=int(position.id),
        company_id=default_company_id,
    )

    assert person_result.employee_id is None and person_result.person_id == person_id
    for field in (
        "fit_status",
        "qualification_status",
        "known_fit_score",
        "overall_fit_score",
        "fit_confidence",
        "requirement_coverage",
        "required_coverage",
        "preferred_coverage",
        "known_count",
        "total_count",
        "general_fit",
        "professional_fit",
        "inputs_hash",
    ):
        assert getattr(person_result, field) == getattr(employee_result, field), field
    assert [e.evaluation_status for e in person_result.requirement_evaluations] == [
        e.evaluation_status for e in employee_result.requirement_evaluations
    ]


def test_person_only_candidate_fit_works_without_employee(client, db, default_company_id):
    """市场候选人没有 employee 行：Fit 必须能算（这是 T2 的存在意义）。"""
    person = _person(db, "t25-candidate", company_id=default_company_id, ready=True)
    db.commit()
    assert (
        db.scalar(sa.text("SELECT count(*) FROM employees WHERE person_id = :p"), {"p": person.id})
        == 0
    )

    _set_competency(db, int(person.id), None, "analysis_problem_solving", 85, 0.9)
    db.commit()

    position = _engineer_position(db, default_company_id)
    result = fit_service.calculate_person_fit(
        db,
        person_id=int(person.id),
        position_definition_id=int(position.id),
        company_id=default_company_id,
    )
    assert result.configured is True and result.fit_status != FitStatus.NOT_EVALUABLE
    assert result.known_count >= 1 and result.known_fit_score is not None
    assert result.employee_id is None


def test_missing_evidence_is_unknown_not_zero(client, db, default_company_id):
    person = _person(db, "t25-unknown", company_id=default_company_id, ready=True)
    db.commit()
    position = _engineer_position(db, default_company_id)
    result = fit_service.calculate_person_fit(
        db,
        person_id=int(person.id),
        position_definition_id=int(position.id),
        company_id=default_company_id,
    )
    assert result.fit_status == FitStatus.INSUFFICIENT_DATA
    assert result.known_count == 0 and result.requirement_coverage == 0.0
    assert all(
        item.evaluation_status == EvaluationStatus.UNRATED
        for item in result.requirement_evaluations
    )
    assert all(item.employee_score is None for item in result.requirement_evaluations)


# ---- persons API ----


def test_person_fit_endpoint_is_company_scoped(client, db, default_company_id):
    person = _person(db, "t25-api", company_id=default_company_id, ready=True)
    db.commit()
    position = _engineer_position(db, default_company_id)

    response = client.get(
        f"/api/v1/persons/{person.id}/fit",
        params={"position_definition_id": int(position.id)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["person_id"] == person.id and body["employee_id"] is None
    assert "inputs_hash" in body, "自有 person 读面保留审计哈希（员工读面同构）"

    # 别家公司：person 不可见 → 404；别家公司职位 → 404
    seq = db.scalar(sa.text("SELECT count(*) FROM companies"))
    rival = Company(name="Rival Fit Co", slug=f"rival-fit-{seq}")
    db.add(rival)
    db.flush()
    rival_person = _person(db, "t25-rival", company_id=int(rival.id), ready=True)
    rival_position = PositionDefinition(
        company_id=int(rival.id), template_scope="company", code=f"rival-role-{seq}", name="Rival"
    )
    db.add(rival_position)
    db.commit()

    assert (
        client.get(
            f"/api/v1/persons/{rival_person.id}/fit",
            params={"position_definition_id": int(position.id)},
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/persons/{person.id}/fit",
            params={"position_definition_id": int(rival_position.id)},
        ).status_code
        == 404
    )


# ---- market API ----


def test_market_fit_public_projection(client, db, default_company_id):
    person = _person(db, "t25-market", company_id=default_company_id, ready=True)
    db.commit()
    _set_competency(db, int(person.id), None, "analysis_problem_solving", 80, 0.85)
    db.commit()
    listing = _active_listing(db, int(person.id), default_company_id)
    position = _engineer_position(db, default_company_id)

    response = client.get(
        f"/api/v1/market/listings/{listing.id}/fit",
        params={"position_definition_id": int(position.id)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["listing_id"] == listing.id
    assert body["position_definition_id"] == int(position.id)
    assert "known_fit_score" in body and "fit_confidence" in body
    # 公开投影：无 inputs_hash / owner id / 内部定义 id
    assert "inputs_hash" not in body
    assert "employee_id" not in body and "person_id" not in body
    for evaluation in body["requirement_evaluations"]:
        assert "requirement_id" not in evaluation
        assert "competency_definition_id" not in evaluation
        assert "candidate_score" in evaluation and "employee_score" not in evaluation


def test_market_fit_requires_active_listing_and_own_position(client, db, default_company_id):
    person = _person(db, "t25-closed", company_id=default_company_id, ready=True)
    db.commit()
    listing = _active_listing(db, int(person.id), default_company_id)
    position = _engineer_position(db, default_company_id)

    # 下架后不可再算（reading a closed listing = 404）
    market_repo.close_active_listing(db, int(listing.id), reason="delisted")
    db.commit()
    assert (
        client.get(
            f"/api/v1/market/listings/{listing.id}/fit",
            params={"position_definition_id": int(position.id)},
        ).status_code
        == 404
    )

    # 未知 listing / 别家公司职位 → 404
    assert (
        client.get(
            "/api/v1/market/listings/999999/fit",
            params={"position_definition_id": int(position.id)},
        ).status_code
        == 404
    )
    seq = db.scalar(sa.text("SELECT count(*) FROM companies"))
    rival = Company(name="Rival Position Co", slug=f"rival-pos-{seq}")
    db.add(rival)
    db.flush()
    rival_position = PositionDefinition(
        company_id=int(rival.id), template_scope="company", code=f"rival-pos-{seq}", name="Rival"
    )
    db.add(rival_position)
    db.commit()
    listing2 = _active_listing(db, int(person.id), default_company_id)
    assert (
        client.get(
            f"/api/v1/market/listings/{listing2.id}/fit",
            params={"position_definition_id": int(rival_position.id)},
        ).status_code
        == 404
    )


def test_market_search_annotates_and_sorts_but_never_filters(client, db, default_company_id):
    """带 position 时：附 Fit 摘要 + 排序；未知仍列出（Unknown != Bad）。"""
    position = _engineer_position(db, default_company_id)

    strong = _person(db, "t25-strong", company_id=default_company_id, ready=True)
    weak = _person(db, "t25-weak", company_id=default_company_id, ready=True)
    unknown = _person(db, "t25-nothing", company_id=default_company_id, ready=True)
    db.commit()
    _set_competency(db, int(strong.id), None, "analysis_problem_solving", 92, 0.9)
    _set_competency(db, int(weak.id), None, "analysis_problem_solving", 45, 0.9)
    db.commit()

    strong_listing = _active_listing(db, int(strong.id), default_company_id)
    weak_listing = _active_listing(db, int(weak.id), default_company_id)
    unknown_listing = _active_listing(db, int(unknown.id), default_company_id)

    # 市场是跨公司读面：同一测试会话里可能有别的挂牌 —— 断言只看本次三名
    # （本地市场规模小、无分页压力，因此这里显式取足够大的 limit）
    body = client.get(
        "/api/v1/market/listings",
        params={"position_definition_id": int(position.id), "limit": 200},
    ).json()
    ids = [item["listing_id"] for item in body["items"]]
    for listing_id in (strong_listing.id, weak_listing.id, unknown_listing.id):
        assert listing_id in ids, "不许筛掉任何在市候选人（含未评估）"
    assert body["total"] >= 3

    by_listing = {item["listing_id"]: item for item in body["items"]}
    assert by_listing[strong_listing.id]["fit"]["known_fit_score"] is not None
    assert by_listing[weak_listing.id]["fit"]["known_fit_score"] is not None
    assert by_listing[unknown_listing.id]["fit"]["known_fit_score"] is None
    assert by_listing[unknown_listing.id]["fit"]["fit_status"] == FitStatus.INSUFFICIENT_DATA

    # 全局排序不变量：非空分单调不增；未评估（None）都在有分之后
    scores = [item["fit"]["known_fit_score"] for item in body["items"]]
    known = [score for score in scores if score is not None]
    assert known == sorted(known, reverse=True)
    first_unknown = next(index for index, score in enumerate(scores) if score is None)
    assert all(score is None for score in scores[first_unknown:]), "未评估的人必须都排在最后"
    assert ids.index(strong_listing.id) < ids.index(weak_listing.id) < ids.index(unknown_listing.id)

    # 不带 position 时不出现 fit 字段（避免无意义算力）
    plain = client.get("/api/v1/market/listings").json()
    assert all(item["fit"] is None for item in plain["items"])


def test_fit_is_read_only(client, db, default_company_id):
    """Fit 只读：算完不改能力行、不建任何行。"""
    person = _person(db, "t25-readonly", company_id=default_company_id, ready=True)
    db.commit()
    _set_competency(db, int(person.id), None, "analysis_problem_solving", 70, 0.7)
    db.commit()
    position = _engineer_position(db, default_company_id)

    before = list(
        db.execute(
            sa.select(
                EmployeeCompetency.id, EmployeeCompetency.score, EmployeeCompetency.confidence
            ).order_by(EmployeeCompetency.id)
        ).all()
    )
    fit_service.calculate_person_fit(
        db,
        person_id=int(person.id),
        position_definition_id=int(position.id),
        company_id=default_company_id,
    )
    after = list(
        db.execute(
            sa.select(
                EmployeeCompetency.id, EmployeeCompetency.score, EmployeeCompetency.confidence
            ).order_by(EmployeeCompetency.id)
        ).all()
    )
    assert before == after


def test_market_search_fit_requires_own_position(client, db, default_company_id):
    """别家公司职位 → 404（不泄露存在性）；不带 position 的搜索不受影响。"""
    seq = db.scalar(sa.text("SELECT count(*) FROM companies"))
    rival = Company(name="Rival Search Co", slug=f"rival-search-{seq}")
    db.add(rival)
    db.flush()
    rival_position = PositionDefinition(
        company_id=int(rival.id), template_scope="company", code=f"rival-search-{seq}", name="Rival"
    )
    db.add(rival_position)
    db.commit()
    assert (
        client.get(
            "/api/v1/market/listings",
            params={"position_definition_id": int(rival_position.id)},
        ).status_code
        == 404
    )
    assert client.get("/api/v1/market/listings").status_code == 200
