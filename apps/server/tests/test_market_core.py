"""T2.3 市场核心契约（docs/t2-talent-market-design.md §6/§7 D8 / plan §4.4）。

锁：
- 迁移 v29：两张新表 + 两个部分唯一索引（`uq_market_listing_active_person` 是
  "同一 person 至多一条 active"的地基），up/down/up 实测；
- **I6/I7**：只有 ready 且无生效主职的人能挂牌；下架后不可再被发现；
- 重复挂牌/重复下架**幂等收敛**（唯一索引 + 条件更新，不 500）；
- **I9**：跨公司可读的只有**公开投影** —— credential/memory/messages/drive/知识正文/
  `owner_company_id`/内部 `person_id` 一律不出现；
- 事件 `market.listed` / `market.delisted` 仅在真实发生时发布；
- 资格判定仍集中在 `eligibility`（挂牌后 market_state=listed、can_recruit=ok）。
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from factories import make_person

from app.events.bus import bus  # noqa: F401  （事件走同一 bus 实例）
from app.models.enums import MarketListingStatus, MarketParticipantKind
from app.models.event import Event
from app.models.organization import Company
from app.repositories import market as market_repo
from app.talent.market import can_list, can_recruit, person_axes
from app.talent.market.contracts import MarketState

_seq = 0


def _ready_character(client, *, name: str = "Market") -> dict:
    """建自由养成角色并结业 → ready（可挂牌）。"""
    global _seq
    _seq += 1
    created = client.post(
        "/api/v1/cultivation/characters", json={"name": f"{name} {_seq}", "origin": "blank"}
    ).json()
    assert (
        client.post(f"/api/v1/cultivation/characters/{created['id']}/complete").status_code == 200
    )
    return created


def _events(db, event_type: str) -> list[Event]:
    rows = db.scalars(sa.select(Event).where(Event.type == event_type))
    return list(rows)


def _events_for_person(db, event_type: str, person_id: int) -> list[Event]:
    return [
        row for row in _events(db, event_type) if (row.payload or {}).get("person_id") == person_id
    ]


def _forbidden_keys(payload) -> list[str]:
    """递归找出禁止出现在公开投影里的键（设计 §6.2 + 内部 id）。"""
    forbidden = {
        "credential",
        "credential_mask",
        "provider",
        "provider_id",
        "runtime",
        "runtime_type",
        "memory",
        "memory_entries",
        "messages",
        "drive",
        "artifacts",
        "content",
        "owner_company_id",
        "person_id",
        "inputs_hash",
        "outputs",
    }
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in forbidden:
                found.append(key)
            found.extend(_forbidden_keys(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_forbidden_keys(item))
    return found


# ---- 迁移 ----


def test_v29_market_tables_and_partial_indexes(tmp_path):
    """v29：两表存在；两个部分唯一索引就位（down 后清空）。"""
    from alembic import command

    from app.core.database import _alembic_config

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'v29.db'}")
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        tables = {
            row[0]
            for row in connection.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        }
        assert {"market_participants", "market_listings"} <= tables
        indexes = {
            row[0]: row[1]
            for row in connection.execute(
                sa.text("SELECT name, sql FROM sqlite_master WHERE type = 'index'")
            )
        }
        assert "uq_market_listing_active_person" in indexes
        assert "status = 'active'" in indexes["uq_market_listing_active_person"]
        assert "uq_market_participant_company" in indexes
        assert "company_id IS NOT NULL" in indexes["uq_market_participant_company"]

    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "a3b5c7d9e1f4")
        tables = {
            row[0]
            for row in connection.execute(
                sa.text("SELECT name FROM sqlite_master WHERE type = 'table'")
            )
        }
        assert not ({"market_participants", "market_listings"} & tables)
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")  # up → down → up 实测


# ---- 挂牌 / 下架 ----


def test_list_publish_delist_roundtrip(client, db, default_company_id):
    created = _ready_character(client)
    person_id = created["person_id"]
    assert can_list(db, person_id).allowed

    response = client.post("/api/v1/market/listings", json={"person_id": person_id})
    assert response.status_code == 201, response.text
    listing = response.json()
    assert listing["identity_id"] == created["identity_id"]
    assert listing["status"] == MarketListingStatus.active.value
    assert set(listing) == {
        "listing_id",
        "identity_id",
        "name",
        "avatar",
        "origin",
        "cultivation_state",
        "status",
        "quality_tier",
        "listed_at",
        "closed_at",
        "listed_by",
    }, "挂牌响应即公开契约：不得夹带内部字段"

    # 挂牌后三轴：listed；can_list=already_listed；can_recruit=ok
    axes = person_axes(db, person_id)
    assert axes.market_state is MarketState.listed
    assert can_list(db, person_id).reason.value == "already_listed"
    assert can_recruit(db, person_id).allowed

    # 在市场的人可被检索到
    page = client.get("/api/v1/market/listings", params={"text": created["name"]}).json()
    assert page["total"] == 1 and page["items"][0]["listing_id"] == listing["listing_id"]

    # 下架 → 幂等 204；下架后不可再被发现、资格恢复
    assert client.delete(f"/api/v1/market/listings/{listing['listing_id']}").status_code == 204
    assert client.delete(f"/api/v1/market/listings/{listing['listing_id']}").status_code == 204
    after = client.get("/api/v1/market/listings", params={"text": created["name"]}).json()
    assert after["total"] == 0
    assert client.get(f"/api/v1/market/listings/{listing['listing_id']}").status_code == 404
    axes = person_axes(db, person_id)
    assert axes.market_state is MarketState.unlisted
    assert can_list(db, person_id).allowed

    assert len(_events_for_person(db, "market.listed", person_id)) == 1
    assert len(_events_for_person(db, "market.delisted", person_id)) == 1


def test_duplicate_listing_is_idempotent_not_500(client, db, default_company_id):
    created = _ready_character(client)
    person_id = created["person_id"]
    first = client.post("/api/v1/market/listings", json={"person_id": person_id})
    second = client.post("/api/v1/market/listings", json={"person_id": person_id})
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["listing_id"] == second.json()["listing_id"]

    actives = market_repo.listing_rows(db, person_id=person_id, active_only=True)
    assert len(actives) == 1, "部分唯一索引必须保证只有一条 active"
    assert len(_events_for_person(db, "market.listed", person_id)) == 1, "幂等挂牌不重发事件"


def test_eligibility_gates_listing(client, db, default_company_id):
    # cultivating（未结业）→ 409 not_ready
    cultivating = client.post(
        "/api/v1/cultivation/characters", json={"name": "NotReady", "origin": "blank"}
    ).json()
    response = client.post("/api/v1/market/listings", json={"person_id": cultivating["person_id"]})
    assert response.status_code == 409
    assert "not_ready" in response.json()["detail"]

    # 已入职（有生效 primary 任职）→ 409 employed
    from factories import make_employee

    from app.models.enums import AssignmentType
    from app.models.position import PositionAssignment
    from app.repositories import persons as person_repo

    ready = _ready_character(client)
    employee = make_employee(
        db,
        company_id=default_company_id,
        slug="t23-employed",
        person=person_repo.get_person(db, ready["person_id"]),
    )
    db.add(
        PositionAssignment(
            employee_id=int(employee.id),
            assignment_type=AssignmentType.primary.value,
            is_primary=True,
            employment_status="active",
        )
    )
    db.commit()
    response = client.post("/api/v1/market/listings", json={"person_id": ready["person_id"]})
    assert response.status_code == 409
    assert "employed" in response.json()["detail"]


def test_listing_hides_other_company_persons(client, db, default_company_id):
    """公司边界：别家公司的 person 不能由本公司挂牌（404，不泄露存在性）。"""
    seq = db.scalar(sa.text("SELECT count(*) FROM companies"))
    rival = Company(name="Rival Market Co", slug=f"rival-market-{seq}")
    db.add(rival)
    db.flush()
    person = make_person(db, slug=f"rival-market-person-{seq}")
    from app.models.cultivation import CharacterProfile

    db.add(
        CharacterProfile(
            person_id=int(person.id),
            identity_id=f"CH-RIVALMK{seq:04d}",
            origin="trained",
            owner_company_id=int(rival.id),
            lifecycle="ready",
        )
    )
    db.commit()

    response = client.post("/api/v1/market/listings", json={"person_id": person.id})
    assert response.status_code == 404
    assert response.json()["detail"] == "person_not_found"


def test_delist_requires_listing_owner(client, db, default_company_id):
    """别家公司挂的牌，本公司下架 → 404（不泄露存在性），且挂牌仍 active。"""
    seq = db.scalar(sa.text("SELECT count(*) FROM companies"))
    rival = Company(name="Rival Delist Co", slug=f"rival-delist-{seq}")
    db.add(rival)
    db.flush()
    person = make_person(db, slug=f"rival-delist-person-{seq}")
    from app.models.cultivation import CharacterProfile

    db.add(
        CharacterProfile(
            person_id=int(person.id),
            identity_id=f"CH-RIVALDL{seq:04d}",
            origin="trained",
            owner_company_id=int(rival.id),
            lifecycle="ready",
        )
    )
    db.flush()
    participant = market_repo.ensure_participant(
        db, kind=MarketParticipantKind.player_company.value, company_id=int(rival.id)
    )
    listing, _ = market_repo.create_active_listing(
        db, person_id=int(person.id), listed_by_participant_id=int(participant.id)
    )
    db.commit()

    assert client.delete(f"/api/v1/market/listings/{listing.id}").status_code == 404
    db.refresh(listing)
    assert listing.status == MarketListingStatus.active.value


# ---- 公开投影（跨公司可读） ----


def test_market_is_cross_company_readable_via_public_projection(client, db, default_company_id):
    """市场跨公司可读，但**只有公开投影**：私有字段与内部 id 一律不出现。"""
    seq = db.scalar(sa.text("SELECT count(*) FROM companies"))
    rival = Company(name="Rival Public Co", slug=f"rival-public-{seq}")
    db.add(rival)
    db.flush()
    person = make_person(db, slug=f"rival-public-person-{seq}", name="Rival Talent")
    from app.models.cultivation import CharacterProfile

    identity_id = f"CH-RIVALPB{seq:04d}"
    db.add(
        CharacterProfile(
            person_id=int(person.id),
            identity_id=identity_id,
            origin="trained",
            owner_company_id=int(rival.id),
            lifecycle="ready",
        )
    )
    db.flush()
    participant = market_repo.ensure_participant(
        db,
        kind=MarketParticipantKind.player_company.value,
        company_id=int(rival.id),
        display_name="Rival Public Co",
    )
    listing, _ = market_repo.create_active_listing(
        db, person_id=int(person.id), listed_by_participant_id=int(participant.id)
    )
    db.commit()

    # 本公司会话（默认公司）能看到别家公司的挂牌
    page = client.get("/api/v1/market/listings", params={"text": "Rival Talent"})
    assert page.status_code == 200
    assert page.json()["total"] == 1
    assert page.json()["items"][0]["listed_by"] == "Rival Public Co"

    detail = client.get(f"/api/v1/market/listings/{listing.id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["identity"]["name"] == "Rival Talent"
    assert body["identity"]["identity_id"] == identity_id
    assert len(body["traits"]) == 8
    assert len(body["competencies"]["general"]) == 10
    assert body["market_state"] == MarketState.listed.value
    assert body["listing"]["listed_by"] == "Rival Public Co"

    leaked = _forbidden_keys(body)
    assert not leaked, f"公开投影泄露内部字段：{sorted(set(leaked))}"


def test_candidate_detail_redacts_internal_outcome_and_evidence_ids(client, db, default_company_id):
    """履历 outcome 去掉 session_ids 等内部引用；证据保留 source_ref 供追溯（验收 A）。"""
    # 真实顺序：先学（产生履历+证据）→ 再结业 → 再挂牌
    global _seq
    _seq += 1
    created = client.post(
        "/api/v1/cultivation/characters", json={"name": f"Redact {_seq}", "origin": "blank"}
    ).json()
    person_id = created["person_id"]
    session = client.post(
        f"/api/v1/cultivation/characters/{created['id']}/sessions",
        json={"topic": "可追溯主题", "mode": "document_study", "kind": "course", "signal": 70},
    )
    assert session.status_code == 201, session.text
    assert (
        client.post(f"/api/v1/cultivation/characters/{created['id']}/complete").status_code == 200
    )
    listing = client.post("/api/v1/market/listings", json={"person_id": person_id}).json()

    body = client.get(f"/api/v1/market/listings/{listing['listing_id']}").json()
    kinds = {event["kind"] for event in body["timeline"]}
    assert kinds == {"course"}
    assert "session_ids" not in body["timeline"][0]["outcome"]
    assert body["timeline"][0]["outcome"].get("knowledge_produced") is not None

    assert body["evidence"], "培养期证据应公开（履历=证据链）"
    evidence = body["evidence"][0]
    assert set(evidence) == {
        "id",
        "competency_code",
        "competency_name",
        "source_kind",
        "source_ref",
        "assessment_run_id",
        "signal",
        "quality",
        "occurred_at",
    }
    assert evidence["source_ref"].startswith("learning_session://")


def test_knowledge_summary_in_market_has_no_content(client, db, default_company_id):
    created = _ready_character(client, name="Knowledge")
    person_id = created["person_id"]
    secret = "市场不得泄露的知识正文"
    from app.repositories import knowledge as knowledge_repo

    knowledge_repo.create_knowledge_item(
        db,
        owner_person_id=person_id,
        topic="市场主题",
        title="标题",
        content=secret,
        scope="private",
    )
    db.commit()
    listing = client.post("/api/v1/market/listings", json={"person_id": person_id}).json()

    response = client.get(f"/api/v1/market/listings/{listing['listing_id']}")
    assert response.status_code == 200
    assert response.json()["knowledge_summary"]["total"] == 1
    assert secret not in response.text
    assert "标题" not in response.text


def test_unknown_listing_is_404_and_closed_listing_not_public(client, db, default_company_id):
    assert client.get("/api/v1/market/listings/999999").status_code == 404

    created = _ready_character(client)
    listing = client.post(
        "/api/v1/market/listings", json={"person_id": created["person_id"]}
    ).json()
    client.delete(f"/api/v1/market/listings/{listing['listing_id']}")
    assert client.get(f"/api/v1/market/listings/{listing['listing_id']}").status_code == 404


def test_market_module_has_no_economic_surface(client):
    """D10 复查：市场 API 的请求/响应里没有价格/报单/结算字段。"""
    schema = client.get("/openapi.json").json()
    market_paths = {path: item for path, item in schema["paths"].items() if "/market" in path}
    blob = str(market_paths).lower()
    for word in ("price", "amount", "bid", "ask", "escrow", "wallet", "ledger", "payment"):
        assert not re.search(rf"\b{word}\b", blob), f"市场 API 出现经济概念：{word}"
