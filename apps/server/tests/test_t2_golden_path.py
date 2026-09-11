"""T2.8 Golden Path（plan §13，26 步）与验收 A–D（plan §14）。

一个测试把 T2 的完整业务链走一遍（真实 API + 真实培养链 + 真实检索），
并在关键节点前后对拍照相 —— 这是 T2 冻结前的最后一道端到端证明：

    建角色 → 多轮自由学习 → 履历/知识/证据 → 结业 READY → 挂牌 →
    跨公司市场可见 → 读档案（时间线/能力/证据下钻）→ 选职位 Fit → 招募 →
    listing 关闭 → Employee(person_id 不变/identity_id 不变) →
    traits/evidence/assessment/knowledge 前后一致 → 入职即携带培养期知识（真实 retrieval）
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from factories import make_person

from app.learning import retrieval
from app.models.competency import AssessmentRun, CompetencyEvidence, EmployeeCompetency
from app.models.cultivation import CharacterProfile
from app.models.enums import MarketListingStatus, MarketParticipantKind
from app.models.knowledge import KnowledgeItem
from app.models.market import MarketListing
from app.models.organization import Company, Employee
from app.models.position import PositionDefinition
from app.models.runtime import EmployeeBrain
from app.repositories import cultivation as cultivation_repo
from app.repositories import market as market_repo
from app.talent.market.issuer import IssuerService

_seq = 0


def _departments(client) -> dict:
    return {row["slug"]: row["id"] for row in client.get("/api/v1/company").json()["departments"]}


def _snapshot(db, person_id: int) -> dict:
    return {
        "person": db.scalar(
            sa.text("SELECT slug || '|' || name FROM persons WHERE id = :p"), {"p": person_id}
        ),
        "profile": db.scalar(
            sa.text(
                "SELECT identity_id || '|' || origin || '|' || lifecycle FROM character_profiles"
                " WHERE person_id = :p"
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
            (row.competency_definition_id, row.score, row.confidence, row.evidence_count)
            for row in db.scalars(
                sa.select(EmployeeCompetency)
                .where(EmployeeCompetency.person_id == person_id)
                .order_by(EmployeeCompetency.competency_definition_id)
            )
        ],
        "assessments": [
            (row.id, row.company_id, row.triggered_by)
            for row in db.scalars(
                sa.select(AssessmentRun).where(AssessmentRun.person_id == person_id)
            )
        ],
        "brain": [
            (row.employee_id, json.dumps(row.traits or {}, sort_keys=True))
            for row in db.scalars(
                sa.select(EmployeeBrain).where(EmployeeBrain.person_id == person_id)
            )
        ],
    }


def _other_company_listing(db, *, name: str) -> tuple[Company, int]:
    """别家公司持有的候选人 + 该公司的挂牌（用于验证"跨公司可以发现"）。"""
    global _seq
    _seq += 1
    company = Company(name=name, slug=f"gp-rival-{_seq}")
    db.add(company)
    db.flush()
    person = make_person(db, slug=f"gp-rival-person-{_seq}", name=name)
    db.add(
        CharacterProfile(
            person_id=int(person.id),
            identity_id=f"CH-GPRIVAL{_seq:05d}",
            origin="trained",
            owner_company_id=int(company.id),
            lifecycle="ready",
        )
    )
    db.flush()
    participant = market_repo.ensure_participant(
        db,
        kind=MarketParticipantKind.player_company.value,
        company_id=int(company.id),
        display_name=company.name,
    )
    listing, _ = market_repo.create_active_listing(
        db, person_id=int(person.id), listed_by_participant_id=int(participant.id)
    )
    db.commit()
    return company, int(listing.id)


def test_t2_golden_path_end_to_end(client, db, default_company_id):
    # 1–4：建角色 → 多轮自由学习（真实履历/知识/证据）
    global _seq
    _seq += 1
    character = client.post(
        "/api/v1/cultivation/characters", json={"name": f"Golden {_seq}", "origin": "blank"}
    ).json()
    person_id = character["person_id"]
    topic = "T2 Golden Path 分布式共识"
    for extra in ("", "·进阶", "·实战"):
        response = client.post(
            f"/api/v1/cultivation/characters/{character['id']}/sessions",
            json={
                "topic": f"{topic}{extra}",
                "mode": "web_research",
                "kind": "course",
                "signal": 70,
            },
        )
        assert response.status_code == 201, response.text

    # 5：显式结业 → READY
    assert (
        client.post(f"/api/v1/cultivation/characters/{character['id']}/complete").status_code == 200
    )
    assert cultivation_repo.get_profile_by_person(db, person_id).lifecycle == "ready"

    # 6：挂牌
    listing = client.post("/api/v1/market/listings", json={"person_id": person_id})
    assert listing.status_code == 201, listing.text
    listing_id = listing.json()["listing_id"]

    # 7：跨公司可以发现（别家公司挂的牌，本会话读得到；本牌也在全市场列表里）
    _rival_company, rival_listing_id = _other_company_listing(db, name="Golden Rival Co")
    cross = client.get(f"/api/v1/market/listings/{rival_listing_id}")
    assert cross.status_code == 200, "跨公司市场读面必须能看见别家挂牌"
    market_total = client.get("/api/v1/market/listings", params={"limit": 200}).json()["total"]
    assert market_total >= 2

    # 8–12：档案 / 时间线 / 能力（score 与 confidence 并列）/ 证据下钻
    detail = client.get(f"/api/v1/market/listings/{listing_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["timeline"], "履历必须有事件"
    assert body["evidence"], "履历即证据链：必须有证据"
    scored = [row for row in body["competencies"]["general"] if row["score"] is not None]
    assert scored, "评估/聚合后的能力行"

    # 验收 A：score → confidence → 证据 → 履历/评估，可完整追溯
    top = scored[0]
    rows = [row for row in body["evidence"] if row["competency_code"] == top["code"]]
    assert rows, f"能力 {top['code']} 的分数必须有证据背书"
    assert all(row["confidence"] is not None for row in [top])
    assert all(row["source_ref"] for row in rows), "每条证据可反查来源"
    first_evidence = db.get(CompetencyEvidence, rows[0]["id"])
    assert first_evidence is not None and first_evidence.person_id == person_id

    # 13–14：选职位 → person-scoped Fit（市场公开投影）
    position = db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == default_company_id,
            PositionDefinition.code == "engineer",
        )
    )
    assert position is not None
    fit = client.get(
        f"/api/v1/market/listings/{listing_id}/fit",
        params={"position_definition_id": int(position.id)},
    )
    assert fit.status_code == 200, fit.text
    assert "known_fit_score" in fit.json() and "fit_confidence" in fit.json()
    assert "inputs_hash" not in fit.json(), "市场投影不含内部输入指纹"

    # 登记"招募前"的人级资产快照（I4/I5 的对拍基准）
    before = _snapshot(db, person_id)
    persons_before = db.scalar(sa.text("SELECT count(*) FROM persons"))

    # 15–17：招募 → listing 关闭 → Employee 创建
    recruit = client.post(
        f"/api/v1/market/listings/{listing_id}/recruit",
        json={"department_id": _departments(client)["engineering"], "title": "Backend Engineer"},
    )
    assert recruit.status_code == 200, recruit.text
    result = recruit.json()
    employee = db.get(Employee, result["employee_id"])
    assert employee is not None and employee.lifecycle_status == "active"

    # 18–19：Employee.person_id == 原 person；identity_id 完全不变
    assert int(employee.person_id) == person_id
    assert result["identity_id"] == character["identity_id"]
    assert db.scalar(sa.text("SELECT count(*) FROM persons")) == persons_before, "不新建 Person"

    # 20–23：traits / evidence / assessment / knowledge 全部原样（不复制、不改写）
    assert _snapshot(db, person_id) == before
    closed = db.get(MarketListing, listing_id)
    assert closed.status == MarketListingStatus.closed.value and closed.close_reason == "recruited"

    # 24–26：入职即携带培养期知识 —— 走**真实 retrieval pipeline**
    item = db.scalars(
        sa.select(KnowledgeItem)
        .where(KnowledgeItem.owner_person_id == person_id)
        .order_by(KnowledgeItem.id)
    ).first()
    assert item is not None and item.owner_employee_id is None, "知识挂人是 person 口径"
    recalled = retrieval.retrieve_for_task(db, int(employee.id), item.topic, "")
    assert item.topic in recalled.knowledge, f"入职后应召回培养期知识：{item.topic!r}"

    # 验收 D：市场域不引入任何货币/合同依赖
    columns = {column.name for column in MarketListing.__table__.columns}
    forbidden = {"price", "amount", "wallet", "balance", "escrow", "payment", "contract_id"}
    assert not (columns & forbidden), columns & forbidden


def test_acceptance_c_issued_and_trained_share_one_pipeline(client, db, default_company_id):
    """官方发行与玩家培养进入**完全相同**的 Evidence/Assessment/Market/Fit 体系。"""
    global _seq
    _seq += 1
    trained = client.post(
        "/api/v1/cultivation/characters", json={"name": f"Trained {_seq}", "origin": "blank"}
    ).json()
    for _ in range(2):
        client.post(
            f"/api/v1/cultivation/characters/{trained['id']}/sessions",
            json={"topic": "培养主题", "mode": "web_research", "kind": "course", "signal": 68},
        )
    client.post(f"/api/v1/cultivation/characters/{trained['id']}/complete")
    trained_listing = client.post(
        "/api/v1/market/listings", json={"person_id": trained["person_id"]}
    ).json()

    issued = IssuerService().issue(
        db, tier="fine", name="发行对照", owner_context_company_id=default_company_id
    )
    assert issued.listing_id is not None

    trained_body = client.get(f"/api/v1/market/listings/{trained_listing['listing_id']}").json()
    issued_body = client.get(f"/api/v1/market/listings/{issued.listing_id}").json()

    for body in (trained_body, issued_body):
        assert set(body) == {
            "listing",
            "identity",
            "traits",
            "competencies",
            "knowledge_summary",
            "timeline",
            "evidence",
            "market_state",
        }
        assert len(body["traits"]) == 8
        assert len(body["competencies"]["general"]) == 10
        assert body["timeline"], "两条来源都必须有真实履历"
        assert body["evidence"], "两条来源都必须有真实证据"

    # 证据同源：环境都是 education（教育证据分级同一套）
    for listing_id in (trained_listing["listing_id"], issued.listing_id):
        participant_rows = db.scalars(
            sa.select(CompetencyEvidence).where(
                CompetencyEvidence.person_id
                == db.scalar(
                    sa.text("SELECT person_id FROM market_listings WHERE id = :i"),
                    {"i": listing_id},
                )
            )
        ).all()
        assert participant_rows and {row.environment for row in participant_rows} == {"education"}


def test_acceptance_a_market_projection_is_whitelisted(client, db, default_company_id):
    """验收 A 的另一面：公开投影里没有 owner id / 知识正文 / 内部审计字段。"""
    _company, listing_id = _other_company_listing(db, name="Whitelist Co")
    body = client.get(f"/api/v1/market/listings/{listing_id}").json()
    flat = json.dumps(body)
    for forbidden in ("person_id", "owner_company_id", "employee_id", "inputs_hash", "content"):
        assert f'"{forbidden}"' not in flat, forbidden
