"""T2.7c NPC 市场参与者契约（docs/t2-talent-market-design.md §10e / plan §4.8）。

锁：
- NPC **不进 `companies`、不建 Employee**（D8）：只关闭挂牌 + 写 `recruited_participant_id`；
- 选拔复用同一个 Fit 引擎；阈值"分数 + 置信度"并列，**Unknown 只落选**（不当作最差买走）；
- CAS 关闭：并发/重复一轮不会重复成交；`dry_run` 不写任何东西；
- 被 NPC 招走的 person → 市场态 `unavailable`、`can_list` 原因 `consumed`（不可再挂牌），
  且玩家无法再从该挂牌招募（409）；
- `ensure_participants` 幂等（身份 + 全局职位模板 + 已发布画像都只建一次）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import sqlalchemy as sa

from app.models.competency import CompetencyDefinition, CompetencyDomain, EmployeeCompetency
from app.models.enums import MarketListingStatus, MarketParticipantKind
from app.models.event import Event
from app.models.market import MarketListing, MarketParticipant
from app.models.organization import Company, Employee
from app.models.position import PositionDefinition
from app.repositories import market as market_repo
from app.talent.market import can_list, person_axes
from app.talent.market.contracts import MarketState
from app.talent.market.npc import NPC_SPECS, NpcMarketService

_seq = 0


def _close_other_active_listings(db) -> None:
    """测试隔离：把此前用例留下的在市挂牌全部关闭（只认"招募式关闭"才算被消化）。

    本地市场是**全局跨公司**读面，NPC 一轮会看见所有在市候选人；为了让断言确定，
    本用例只保留自己造的候选人在市。用非招募式关闭（close_reason=test_isolation），
    不影响它们的 person 三轴。
    """
    db.execute(
        sa.update(MarketListing)
        .where(MarketListing.status == MarketListingStatus.active.value)
        .values(status=MarketListingStatus.closed.value, close_reason="test_isolation")
    )
    db.commit()


def _definition_id(db, code: str) -> int:
    return int(
        db.scalar(
            sa.select(CompetencyDefinition.id)
            .join(CompetencyDomain, CompetencyDomain.id == CompetencyDefinition.domain_id)
            .where(CompetencyDefinition.code == code, CompetencyDomain.company_id.is_(None))
        )
    )


def _listed_talent(client, db, *, scores: dict[str, tuple[int, float]] | None, name: str) -> dict:
    """建角色 →（可选）写 person-only 能力行 → 结业 → 挂牌。"""
    global _seq
    _seq += 1
    created = client.post(
        "/api/v1/cultivation/characters", json={"name": f"{name} {_seq}", "origin": "blank"}
    ).json()
    for code, (score, confidence) in (scores or {}).items():
        db.add(
            EmployeeCompetency(
                person_id=created["person_id"],
                employee_id=None,  # person-only：候选人没有 employee 行
                competency_definition_id=_definition_id(db, code),
                score=score,
                confidence=confidence,
                status="assessed",
                evidence_count=6,
            )
        )
    db.commit()
    assert (
        client.post(f"/api/v1/cultivation/characters/{created['id']}/complete").status_code == 200
    )
    listing = client.post("/api/v1/market/listings", json={"person_id": created["person_id"]})
    assert listing.status_code == 201, listing.text
    return {**created, "listing": listing.json()}


def _npc_event(db, person_id: int) -> list[Event]:
    rows = db.scalars(sa.select(Event).where(Event.type == "market.candidate_taken"))
    return [row for row in rows if (row.payload or {}).get("person_id") == person_id]


# ---- 身份与标准的幂等建立 ----


def test_ensure_participants_is_idempotent(client, db, default_company_id):
    service = NpcMarketService()
    first = service.ensure_participants(db, company_context_id=default_company_id)
    second = service.ensure_participants(db, company_context_id=default_company_id)

    assert [p.id for p in first] == [p.id for p in second], "重复 ensure 不得新建参与者"
    assert len(first) == len(NPC_SPECS)
    for participant in first:
        assert participant.kind == MarketParticipantKind.npc_company.value
        assert participant.company_id is None, "D8：NPC 不进 companies"
    # 全局职位模板（company_id NULL）+ 已发布画像
    definitions = list(
        db.scalars(
            sa.select(PositionDefinition).where(
                PositionDefinition.code.in_([spec.position_code for spec in NPC_SPECS])
            )
        )
    )
    assert len(definitions) == len(NPC_SPECS)
    assert all(definition.company_id is None for definition in definitions), "NPC 标准是全局模板"
    assert not db.scalars(sa.select(Employee).where(Employee.company_id.is_(None))).all()


# ---- 一轮市场活动 ----


def test_run_once_acquires_strong_candidate(client, db, default_company_id):
    _close_other_active_listings(db)
    strong = _listed_talent(
        client,
        db,
        scores={"analysis_problem_solving": (86, 0.82), "execution": (80, 0.7)},
        name="NPC 强候选",
    )

    reports = NpcMarketService().run_once(db, company_context_id=default_company_id)
    acquired = [item for report in reports for item in report.acquired]
    assert [item.listing_id for item in acquired] == [strong["listing"]["listing_id"]]
    assert acquired[0].fit_score is not None and acquired[0].fit_score >= 0.7
    assert acquired[0].fit_confidence is not None and acquired[0].fit_confidence >= 0.4

    listing = market_repo.get_listing(db, strong["listing"]["listing_id"])
    assert listing is not None
    assert listing.status == MarketListingStatus.closed.value
    assert listing.close_reason == "npc_recruited"
    assert listing.recruited_participant_id is not None
    assert listing.recruited_company_id is None and listing.recruited_employee_id is None, (
        "NPC 不伪造公司/员工归属"
    )

    # 事件（玩家可感知：'某人才已经被其他公司招募'）
    events = _npc_event(db, strong["person_id"])
    assert len(events) == 1
    assert events[0].payload["participant_name"]
    assert events[0].payload["listing_id"] == strong["listing"]["listing_id"]

    # 被消化：不可再挂牌、也不在市
    axes = person_axes(db, strong["person_id"])
    assert axes.market_state is MarketState.unavailable
    assert can_list(db, strong["person_id"]).reason.value == "consumed"

    # 玩家也无法再从这条挂牌招募（已关闭 → 409）
    assert (
        client.post(
            f"/api/v1/market/listings/{strong['listing']['listing_id']}/recruit", json={}
        ).status_code
        == 409
    )


def test_run_once_skips_unknown_and_weak_candidates(client, db, default_company_id):
    """Unknown != Bad：证据不足的人只是落选，不被"当成最差"买走。"""
    _close_other_active_listings(db)
    unknown = _listed_talent(client, db, scores=None, name="NPC 无证据")
    weak = _listed_talent(
        client,
        db,
        scores={"analysis_problem_solving": (40, 0.3), "execution": (35, 0.3)},
        name="NPC 弱候选",
    )

    reports = NpcMarketService().run_once(db, company_context_id=default_company_id)
    acquired_ids = {item.listing_id for report in reports for item in report.acquired}
    assert not acquired_ids, f"不该买走任何候选人：{acquired_ids}"

    for talent in (unknown, weak):
        listing = market_repo.get_listing(db, talent["listing"]["listing_id"])
        assert listing is not None and listing.status == MarketListingStatus.active.value
        assert _npc_event(db, talent["person_id"]) == []
        # 仍在市、仍可挂牌被玩家招募
        assert person_axes(db, talent["person_id"]).market_state is MarketState.listed


def test_dry_run_writes_nothing(client, db, default_company_id):
    _close_other_active_listings(db)
    strong = _listed_talent(
        client,
        db,
        scores={"analysis_problem_solving": (88, 0.85), "execution": (82, 0.72)},
        name="NPC dry-run",
    )
    reports = NpcMarketService().run_once(db, company_context_id=default_company_id, dry_run=True)
    assert any(report.acquired for report in reports), "dry-run 仍应报告会买谁"
    listing = market_repo.get_listing(db, strong["listing"]["listing_id"])
    assert listing is not None and listing.status == MarketListingStatus.active.value
    assert listing.recruited_participant_id is None
    assert _npc_event(db, strong["person_id"]) == []


def test_second_round_finds_nothing_to_buy(client, db, default_company_id):
    """成交后 listing 关闭 → 下一轮不再重复成交（CAS + 已在市过滤）。"""
    _close_other_active_listings(db)
    _listed_talent(
        client,
        db,
        scores={"analysis_problem_solving": (90, 0.9), "execution": (85, 0.8)},
        name="NPC 一次成交",
    )
    service = NpcMarketService()
    first = service.run_once(db, company_context_id=default_company_id)
    second = service.run_once(db, company_context_id=default_company_id)
    assert sum(len(report.acquired) for report in first) == 1
    assert sum(len(report.acquired) for report in second) == 0


def test_unknown_npc_key_is_rejected(db, default_company_id):
    try:
        NpcMarketService().run_once(
            db, company_context_id=default_company_id, only_npc_key="does-not-exist"
        )
    except ValueError as exc:
        assert "unknown npc key" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("未知 NPC key 必须被拒绝")


# ---- 守卫：NPC 不碰公司与员工域 ----


def test_npc_service_never_creates_companies_or_employees():
    """AST 守卫（D8）：NPC 模块不得构造 Company/Employee，也不得 import 组织写路径。"""
    server_root = Path(__file__).resolve().parents[1]
    path = server_root / "app" / "talent" / "market" / "npc.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))

    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not (calls & {"Company", "Employee", "create_employee"}), calls

    forbidden_prefixes = (
        "app.services.lifecycle",
        "app.services.recruitment",
        "app.repositories.organization",
    )
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    assert not [module for module in imports if module.startswith(forbidden_prefixes)], imports


def test_npc_participants_are_visible_in_company_registry(client, db, default_company_id):
    """NPC 只在市场参与者表里，不出现在公司表（否则会污染公司作用域读面）。"""
    NpcMarketService().ensure_participants(db, company_context_id=default_company_id)
    companies = db.scalars(sa.select(Company)).all()
    assert all(company.slug != "npc" for company in companies)
    npc_rows = db.scalars(
        sa.select(MarketParticipant).where(
            MarketParticipant.kind == MarketParticipantKind.npc_company.value
        )
    ).all()
    assert len(npc_rows) == len(NPC_SPECS)
