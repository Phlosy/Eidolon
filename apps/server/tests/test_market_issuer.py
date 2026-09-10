"""T2.4 发行方契约（docs/t2-talent-market-design.md §7 D11 / plan §4.5）。

锁：
- **D11**：发行走真实培养链 —— 产出 EducationEvent / CompetencyEvidence / AssessmentRun；
  档位只影响参数与概率分布，**能力分只能来自聚合器**（守卫：issuer 代码不得出现 score/confidence）；
- 同 seed 确定性（同 seed 同角色：证据 signal 逐值一致）；
- 三档产出分布可区分（统计断言：证据条数与平均 signal 单调）；
- `origin=issued` 是合法来源（发行方产出），但**玩家端点仍拒绝** issuance（不能自铸官方角色）；
- 发行角色 `owner_company_id IS NULL`（在市场，T1 语义），评估快照用部署上下文（D13）；
- 挂牌方 = system_issuer 参与者；挂牌后 market_state=listed、可被市场检索/详情读到。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import sqlalchemy as sa
from factories import make_person  # noqa: F401  （fixtures 依赖）

from app.models.competency import AssessmentRun, CompetencyEvidence, EmployeeCompetency
from app.models.cultivation import CharacterProfile
from app.models.enums import MarketParticipantKind, TalentOrigin
from app.models.market import MarketParticipant
from app.repositories import cultivation as cultivation_repo
from app.talent.market import person_axes
from app.talent.market.contracts import MarketState
from app.talent.market.issuer import TIERS, IssuerService, get_tier

_seq = 0


def _issue(db, company_id: int, **kwargs):
    global _seq
    _seq += 1
    kwargs.setdefault("seed", f"t24-{_seq}")
    return IssuerService().issue(db, owner_context_company_id=company_id, **kwargs)


# ---- 真实培养链 ----


def test_issue_runs_the_real_cultivation_chain(client, db, default_company_id):
    issued = _issue(db, default_company_id, tier="fine", name="发行甲")

    profile = cultivation_repo.get_profile_by_person(db, issued.person_id)
    assert profile is not None
    assert profile.origin == TalentOrigin.issued.value
    assert profile.lifecycle == "ready"
    assert profile.owner_company_id is None, "发行角色一生下来就在市场（T1 语义：NULL = 在市场）"

    programs = cultivation_repo.list_programs(db, issued.person_id)
    assert [p.status for p in programs] == ["completed"]
    assert programs[0].metadata_json["issuer"]["tier"] == "fine", "培养参数随实例落库（可审计）"

    events = cultivation_repo.list_education_events(db, issued.person_id)
    assert events, "发行必须产生真实履历事件"
    assert any(e.kind == "exam" for e in events), "学院派含考试"

    evidence = list(
        db.scalars(
            sa.select(CompetencyEvidence).where(CompetencyEvidence.person_id == issued.person_id)
        )
    )
    assert evidence and issued.evidence_count == len(evidence)
    assert all(row.environment == "education" for row in evidence)

    runs = list(
        db.scalars(sa.select(AssessmentRun).where(AssessmentRun.person_id == issued.person_id))
    )
    assert runs, "阶段评估必须真实跑过（能力分只来自聚合器）"
    assert all(run.company_id == default_company_id for run in runs), "评估快照 = 部署上下文（D13）"

    # 能力行：有分的地方必有证据背书（聚合器写，不是发行方写）
    rows = list(
        db.scalars(
            sa.select(EmployeeCompetency).where(EmployeeCompetency.person_id == issued.person_id)
        )
    )
    assert rows, "评估后应落能力行"
    assert all(row.evidence_count > 0 for row in rows if row.score is not None)


def test_issue_lists_on_market_as_system_issuer(client, db, default_company_id):
    issued = _issue(db, default_company_id, tier="normal", name="发行乙")
    assert issued.listing_id is not None

    participant = db.scalar(
        sa.select(MarketParticipant).where(
            MarketParticipant.kind == MarketParticipantKind.system_issuer.value
        )
    )
    assert participant is not None and participant.company_id is None

    axes = person_axes(db, issued.person_id)
    assert axes.market_state is MarketState.listed

    # 市场侧可检索/可读（公开投影），且不是本公司自有 person 读面
    listing = client.get(f"/api/v1/market/listings/{issued.listing_id}")
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["identity"]["origin"] == TalentOrigin.issued.value
    assert body["listing"]["listed_by"] == "Eidolon 发行方"
    assert client.get(f"/api/v1/persons/{issued.person_id}").status_code == 404


def test_issue_without_listing(client, db, default_company_id):
    issued = _issue(db, default_company_id, tier="normal", list_on_market=False)
    assert issued.listing_id is None
    assert person_axes(db, issued.person_id).market_state is MarketState.unlisted


def test_issuer_is_deterministic_by_seed(db, default_company_id):
    first = _issue(db, default_company_id, tier="fine", seed="same-seed", list_on_market=False)
    second = _issue(db, default_company_id, tier="fine", seed="same-seed", list_on_market=False)

    def _signals(person_id: int) -> list[int]:
        rows = cultivation_repo.list_education_events(db, person_id)
        return [value for row in rows for value in (row.outcome or {}).get("signals", [])]

    assert _signals(first.person_id) == _signals(second.person_id)
    assert first.evidence_count == second.evidence_count
    assert first.mean_signal == second.mean_signal
    assert first.identity_id != second.identity_id, "身份 ID 必须唯一（确定性不等于复用角色）"


def test_tiers_produce_distinguishable_distributions(db, default_company_id):
    """三档产出可区分：证据条数与平均 signal 随档位单调上升（同模板下参数生效）。"""
    stats: dict[str, tuple[float, float]] = {}
    for tier in ("normal", "fine", "rare"):
        issues = [
            _issue(
                db,
                default_company_id,
                tier=tier,
                template="vocational",  # 固定模板，只让**参数**说话
                seed=f"{tier}-{index}",
                list_on_market=False,
            )
            for index in range(4)
        ]
        evidence = sum(item.evidence_count for item in issues) / len(issues)
        mean_signal = sum(item.mean_signal or 0 for item in issues) / len(issues)
        stats[tier] = (evidence, mean_signal)

    assert stats["normal"][0] < stats["fine"][0], stats
    assert stats["fine"][0] < stats["rare"][0] or stats["rare"][0] > stats["normal"][0]
    assert stats["normal"][1] < stats["fine"][1] < stats["rare"][1], stats


def test_rare_covers_two_templates(db, default_company_id):
    issued = _issue(db, default_company_id, tier="rare", list_on_market=False)
    programs = cultivation_repo.list_programs(db, issued.person_id)
    assert [p.template for p in programs] == ["academic", "vocational"]
    assert issued.templates == ("academic", "vocational")


def test_unknown_tier_is_rejected(db, default_company_id):
    try:
        get_tier("legendary")
    except ValueError as exc:
        assert "unknown issuer tier" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("未知档位必须被拒绝")

    service = IssuerService()
    try:
        service.issue(db, tier="legendary", owner_context_company_id=default_company_id)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("未知档位发行必须失败")


# ---- 玩家端点不得自铸官方角色 ----


def test_player_endpoint_still_rejects_issued_origin(client, db, default_company_id):
    response = client.post(
        "/api/v1/cultivation/characters",
        json={"name": "自铸角色", "origin": "issued"},
    )
    assert response.status_code == 422
    assert "issued" in response.json()["detail"]


# ---- D11 守卫：发行路径不得出现能力分 ----


def test_issuer_code_never_touches_capability_scores():
    """发行方只准写培养参数与证据线索 —— 代码里不得出现 score/confidence。"""
    server_root = Path(__file__).resolve().parents[1]
    docstring_owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)

    def _tokens(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, docstring_owners):
                body = getattr(node, "body", [])
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                ):
                    if isinstance(body[0].value.value, str):
                        docstrings.add(id(body[0].value))
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                found.update(part for part in node.id.lower().split("_") if part)
            elif isinstance(node, ast.Attribute):
                found.update(part for part in node.attr.lower().split("_") if part)
            elif isinstance(node, ast.arg):
                found.update(part for part in node.arg.lower().split("_") if part)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                found.update(part for part in node.name.lower().split("_") if part)
            elif isinstance(node, ast.keyword) and node.arg:
                found.update(part for part in node.arg.lower().split("_") if part)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                found.update(re.findall(r"[a-zA-Z_]+", node.value.lower()))
        return found

    offenders: list[str] = []
    for relative in ("app/talent/market/issuer.py",):
        hit = _tokens(server_root / relative) & {"score", "confidence"}
        if hit:
            offenders.append(f"{relative}: {sorted(hit)}")
    assert not offenders, "发行方不得写能力分（D11）：\n" + "\n".join(f"  - {x}" for x in offenders)


def test_issuer_does_not_construct_competency_rows():
    """AST 守卫（app 级 D6 守卫的聚焦版）：issuer 不得构造 EmployeeCompetency / AssessmentRun。"""
    server_root = Path(__file__).resolve().parents[1]
    path = server_root / "app" / "talent" / "market" / "issuer.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not (calls & {"EmployeeCompetency", "AssessmentRun"}), calls


def test_issued_tier_is_listed_metadata_only(client, db, default_company_id):
    """档位是发行参数：只出现在 listing 的 quality_tier 与培养参数里，不改写角色画像。"""
    issued = _issue(db, default_company_id, tier="rare", name="发行丙")
    listing = client.get(f"/api/v1/market/listings/{issued.listing_id}").json()
    assert listing["listing"]["quality_tier"] == "rare"
    # 公开档案里没有任何"战力/品阶加成"字段
    flattened = str(listing)
    for word in ("rarity", "power", "combat", "bonus"):
        assert word not in flattened.lower()
    profile = cultivation_repo.get_profile_by_person(db, issued.person_id)
    assert profile is not None and profile.origin == "issued"


def test_tier_catalog_is_frozen():
    """档位集合与默认模板是**发行参数契约**（改动需走设计文档评审）。"""
    assert set(TIERS) == {"normal", "fine", "rare"}
    assert TIERS["normal"].templates == ("vocational",)
    assert TIERS["fine"].templates == ("academic",)
    assert TIERS["rare"].templates == ("academic", "vocational")
    assert TIERS["normal"].params.signal_bonus < TIERS["fine"].params.signal_bonus
    assert TIERS["fine"].params.signal_bonus < TIERS["rare"].params.signal_bonus


def test_character_profile_rows_stay_ready_and_unowned(db, default_company_id):
    """发行不产生"已被谁持有"的假象：owner NULL 且 lifecycle=ready。"""
    issued = _issue(db, default_company_id, tier="normal")
    profile = db.scalar(
        sa.select(CharacterProfile).where(CharacterProfile.person_id == issued.person_id)
    )
    assert profile is not None
    assert profile.owner_company_id is None and profile.lifecycle == "ready"
