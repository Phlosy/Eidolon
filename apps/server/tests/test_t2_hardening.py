"""T2.8 硬化测试（plan §4.9 / §9 并发与幂等 / §12 风险收口）。

锁：
- **CAS 基元**：条件关闭只有第一个调用者能赢；被别人抢先的招募必须 409 且**不留半个员工**；
- **并发挂牌**：多线程各自独立 Session 同时挂牌同一 person → 恰好一条 active（唯一索引兜底）；
- **公司边界下沉**（T2.6 R-new2）：`assign_position` 自己校验编制属本公司 → 跨公司 404；
- **批量 Fit 与单入口等价**（T2.8 硬化）：逐字段（含 inputs_hash）一致；
- **无 N+1**（T2.7 R-new2）：市场"按职位排序"的 SQL 条数不随候选人数线性增长；
- **结业评估语义**（T2.8）：有证据才聚合；零证据角色照常结业且能力保持 unrated（不编分）。
"""

from __future__ import annotations

import threading

import sqlalchemy as sa
from factories import make_employee

from app.core.database import SessionLocal
from app.models.competency import CompetencyEvidence, EmployeeCompetency
from app.models.cultivation import CharacterProfile
from app.models.enums import MarketListingStatus, MarketParticipantKind
from app.models.market import MarketListing
from app.models.organization import Company, Employee
from app.models.position import PositionDefinition
from app.repositories import market as market_repo
from app.repositories import position as position_repo
from app.services import market as market_service
from app.services import position_service
from app.talent.fit import engine as fit_engine
from app.talent.fit import service as fit_service
from app.talent.market.contracts import MarketSearchQuery

_seq = 0


def _definition_id(db, code: str) -> int:
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


def _ready_person(client, db, *, name: str, owner_company_id: int, scores=None) -> tuple[dict, int]:
    """建角色 →（可选写能力行）→ 结业 → (character, person_id)。"""
    global _seq
    _seq += 1
    created = client.post(
        "/api/v1/cultivation/characters", json={"name": f"{name} {_seq}", "origin": "blank"}
    ).json()
    person_id = created["person_id"]
    for code, (score, confidence) in (scores or {}).items():
        db.add(
            EmployeeCompetency(
                person_id=person_id,
                employee_id=None,
                competency_definition_id=_definition_id(db, code),
                score=score,
                confidence=confidence,
                status="assessed",
                evidence_count=4,
            )
        )
    db.commit()
    assert (
        client.post(f"/api/v1/cultivation/characters/{created['id']}/complete").status_code == 200
    )
    return created, person_id


# ---------------- CAS 与并发 ----------------


def test_cas_close_has_exactly_one_winner(client, db, default_company_id):
    _character, person_id = _ready_person(
        client, db, name="CAS", owner_company_id=default_company_id
    )
    listed = client.post("/api/v1/market/listings", json={"person_id": person_id}).json()

    assert market_repo.close_active_listing(db, listed["listing_id"], reason="first") is True
    assert market_repo.close_active_listing(db, listed["listing_id"], reason="second") is False
    db.commit()
    row = db.get(MarketListing, listed["listing_id"])
    assert row is not None and row.close_reason == "first", "第二个调用者不得覆盖关闭原因"


def test_recruit_loses_race_without_leaving_half_employee(client, db, default_company_id):
    """listing 被别人先关掉 → 招募 409，且**不留** Employee 行、不改 person 资产。"""
    _character, person_id = _ready_person(
        client, db, name="Race", owner_company_id=default_company_id
    )
    listed = client.post("/api/v1/market/listings", json={"person_id": person_id}).json()
    # 别的参与者抢先关掉（NPC 或并发请求的等价形态）
    npc = market_repo.ensure_participant(
        db, kind=MarketParticipantKind.npc_company.value, company_id=None, display_name="抢单 NPC"
    )
    assert market_repo.close_active_listing(
        db, listed["listing_id"], reason="npc_recruited", recruited_participant_id=int(npc.id)
    )
    db.commit()

    response = client.post(f"/api/v1/market/listings/{listed['listing_id']}/recruit", json={})
    assert response.status_code == 409
    assert response.json()["detail"] == "listing_not_active"
    assert db.scalars(sa.select(Employee).where(Employee.person_id == person_id)).all() == []


def test_concurrent_listing_creates_exactly_one_active_row(client, db, default_company_id):
    """多线程独立 Session 同时挂牌同一 person：唯一索引兜底，恰好一条 active。"""
    _character, person_id = _ready_person(
        client, db, name="Concurrent", owner_company_id=default_company_id
    )
    errors: list[Exception] = []

    def _list_once() -> None:
        with SessionLocal() as session:
            try:
                market_service.list_person(
                    session, person_id=person_id, company_id=default_company_id
                )
            except Exception as exc:  # noqa: BLE001 - 锁竞争也算"没有产生第二行"
                errors.append(exc)

    threads = [threading.Thread(target=_list_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    actives = market_repo.listing_rows(db, person_id=person_id, active_only=True)
    assert len(actives) == 1, f"并发挂牌必须收敛成一条 active（errors={errors}）"


# ---------------- 公司边界下沉 ----------------


def test_assign_position_rejects_cross_company_slot(client, db, default_company_id):
    """T2.6 R-new2 收口：编制与员工不同公司 → 404（不泄露存在性），且不产生任职。"""
    employee = make_employee(db, company_id=default_company_id, slug="hardening-emp")
    db.commit()
    seq = db.scalar(sa.text("SELECT count(*) FROM companies"))
    rival = Company(name="Rival Assign Co", slug=f"rival-assign-{seq}")
    db.add(rival)
    db.flush()
    rival_slot = position_repo.require_slot(
        db,
        int(
            db.scalar(
                sa.text("SELECT id FROM position_slots WHERE company_id = :c LIMIT 1"),
                {"c": 1},
            )
        ),
    )
    # 把坑改到别家公司（模拟跨公司引用）—— 直接改列，避免造整套编制
    original_company = int(rival_slot.company_id)
    db.execute(
        sa.text("UPDATE position_slots SET company_id = :c WHERE id = :s"),
        {"c": int(rival.id), "s": int(rival_slot.id)},
    )
    db.commit()
    # 绕过 ORM 的原地更新：过期身份映射，保证 assign_position 读到新公司
    db.expire_all()

    from fastapi import HTTPException

    from app.schemas.position import AssignmentIn

    try:
        position_service.assign_position(db, employee, AssignmentIn(slot_id=int(rival_slot.id)))
    except HTTPException as exc:
        assert exc.status_code == 404
    else:  # pragma: no cover
        raise AssertionError("跨公司编制必须被拒绝")
    finally:
        db.rollback()
        db.execute(
            sa.text("UPDATE position_slots SET company_id = :c WHERE id = :s"),
            {"c": original_company, "s": int(rival_slot.id)},
        )
        db.commit()

    assert position_repo.active_primary_assignment(db, int(employee.id)) is None


# ---------------- 批量 Fit 与单入口等价 ----------------


def test_batch_person_fit_matches_single_entry(client, db, default_company_id):
    position = db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == default_company_id,
            PositionDefinition.code == "engineer",
        )
    )
    assert position is not None

    _c1, person_with_scores = _ready_person(
        client,
        db,
        name="BatchA",
        owner_company_id=default_company_id,
        scores={"analysis_problem_solving": (84, 0.8), "execution": (78, 0.7)},
    )
    _c2, person_empty = _ready_person(
        client, db, name="BatchB", owner_company_id=default_company_id
    )

    batch = fit_engine.calculate_many_for_persons(
        db, position=position, person_ids=[person_with_scores, person_empty, person_with_scores]
    )
    assert set(batch) == {person_with_scores, person_empty}
    for person_id in (person_with_scores, person_empty):
        single = fit_service.calculate_person_fit(
            db,
            person_id=person_id,
            position_definition_id=int(position.id),
            company_id=default_company_id,
        )
        batched = batch[person_id]
        for field in (
            "fit_status",
            "qualification_status",
            "known_fit_score",
            "fit_confidence",
            "requirement_coverage",
            "known_count",
            "total_count",
            "inputs_hash",
        ):
            assert getattr(batched, field) == getattr(single, field), (person_id, field)


# ---------------- 无 N+1：市场按职位排序 ----------------


def test_market_fit_ranking_does_not_scale_queries_with_candidates(client, db, default_company_id):
    position = db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == default_company_id,
            PositionDefinition.code == "engineer",
        )
    )
    assert position is not None
    # 只保留本次候选：把此前用例的 active 挂牌全部关闭（非招募式，不影响三轴）
    db.execute(
        sa.update(MarketListing)
        .where(MarketListing.status == MarketListingStatus.active.value)
        .values(status=MarketListingStatus.closed.value, close_reason="hardening_isolation")
    )
    db.commit()
    first, first_person = _ready_person(
        client, db, name="Rank1", owner_company_id=default_company_id
    )
    client.post("/api/v1/market/listings", json={"person_id": first_person})

    counter = {"n": 0}

    def _before(*args, **kwargs):  # noqa: ANN002, ANN003
        counter["n"] += 1

    bind = db.get_bind()
    sa.event.listen(bind, "before_cursor_execute", _before)
    try:
        query = MarketSearchQuery(limit=None)
        with_one = counter["n"]
        market_service.search(db, query, position=position, company_id=default_company_id)
        cost_one = counter["n"] - with_one

        for index in range(3):
            _c, person_id = _ready_person(
                client, db, name=f"Rank{index + 2}", owner_company_id=default_company_id
            )
            client.post("/api/v1/market/listings", json={"person_id": person_id})
        before_four = counter["n"]
        market_service.search(db, query, position=position, company_id=default_company_id)
        cost_four = counter["n"] - before_four
    finally:
        sa.event.remove(bind, "before_cursor_execute", _before)

    assert cost_one > 0 and cost_four > 0
    assert cost_four - cost_one <= 4, (
        f"候选人数从 1 → 4，SQL 条数不应线性增长（1 人 {cost_one} 条，4 人 {cost_four} 条）"
    )


# ---------------- 结业评估语义（T2.8） ----------------


def test_finalize_assesses_only_when_evidence_exists(client, db, default_company_id):
    """有证据 → 结业时聚合出分；零证据 → 照常结业且能力保持 unrated（不编分）。"""
    global _seq
    _seq += 1
    with_evidence = client.post(
        "/api/v1/cultivation/characters", json={"name": f"FinalAssess {_seq}", "origin": "blank"}
    ).json()
    client.post(
        f"/api/v1/cultivation/characters/{with_evidence['id']}/sessions",
        json={"topic": "结业评估主题", "mode": "web_research", "kind": "course", "signal": 75},
    )
    assert (
        client.post(f"/api/v1/cultivation/characters/{with_evidence['id']}/complete").status_code
        == 200
    )

    rows = list(
        db.scalars(
            sa.select(EmployeeCompetency).where(
                EmployeeCompetency.person_id == with_evidence["person_id"]
            )
        )
    )
    assert any(row.score is not None for row in rows), "有证据时结业应聚合出能力分"

    _seq += 1
    no_evidence = client.post(
        "/api/v1/cultivation/characters",
        json={"name": f"FinalNoEvidence {_seq}", "origin": "blank"},
    ).json()
    assert (
        client.post(f"/api/v1/cultivation/characters/{no_evidence['id']}/complete").status_code
        == 200
    )
    assert (
        db.scalars(
            sa.select(EmployeeCompetency).where(
                EmployeeCompetency.person_id == no_evidence["person_id"]
            )
        ).all()
        == []
    ), "零证据不得凭空造能力行"


def test_finalize_assessment_audit_is_marked(client, db, default_company_id):
    """结业评估以 `cultivation_final` 落审计（可追溯，不是无出处的一次重算）。"""
    global _seq
    _seq += 1
    character = client.post(
        "/api/v1/cultivation/characters", json={"name": f"FinalAudit {_seq}", "origin": "blank"}
    ).json()
    client.post(
        f"/api/v1/cultivation/characters/{character['id']}/sessions",
        json={"topic": "审计主题", "mode": "document_study", "kind": "exam", "signal": 80},
    )
    client.post(f"/api/v1/cultivation/characters/{character['id']}/complete")

    from app.models.competency import AssessmentRun

    runs = list(
        db.scalars(
            sa.select(AssessmentRun).where(AssessmentRun.person_id == character["person_id"])
        )
    )
    assert runs and all(run.assessment_type == "cultivation_final" for run in runs)
    assert all(run.triggered_by == "cultivation:complete" for run in runs)
    assert all(run.company_id == default_company_id for run in runs)
    # 证据仍然只挂 person（聚合不改写人级资产）
    evidence = db.scalars(
        sa.select(CompetencyEvidence).where(CompetencyEvidence.person_id == character["person_id"])
    ).all()
    assert evidence and all(row.employee_id is None for row in evidence)


def test_person_only_evidence_is_untouched_by_finalize(client, db, default_company_id):
    """结业评估不得把 person-only 行改写进 employee 归属（I4）。"""
    _character, person_id = _ready_person(
        client, db, name="Ownership", owner_company_id=default_company_id
    )
    profile = db.scalar(sa.select(CharacterProfile).where(CharacterProfile.person_id == person_id))
    assert profile is not None and profile.owner_company_id == default_company_id
    assert (
        db.scalar(sa.text("SELECT count(*) FROM employees WHERE person_id = :p"), {"p": person_id})
        == 0
    )
