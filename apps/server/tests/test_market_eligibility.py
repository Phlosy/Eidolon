"""T2.2 结业与市场资格契约（docs/t2-talent-market-design.md §4/§7 D1 / plan §4.3）。

锁：
- 自由养成**玩家显式结业** → ready；模板培养进行中 → 409；
  重复 complete 幂等（200、无副作用、不重发事件）；
- 结业**没有任何能力阈值**：零证据、全 unrated 的角色照样能结业（D1 —— 市场价值由买方判断）；
- `cultivation.completed` 事件落 events 表（company 快照正确）；
- 三轴资格判定集中在一处：`can_list` / `can_recruit`（纯矩阵 + DB 包装），其他层不得复制；
- 已入职 person 不能挂牌；未在市的 person 不能被市场招募；跨公司角色 404。
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from factories import make_employee

from app.events.bus import bus  # noqa: F401  （确保 bus 已 import，事件走同一实例）
from app.models.cultivation import CharacterProfile
from app.models.enums import CultivationState, EmploymentState
from app.models.event import Event
from app.talent.market.contracts import MarketState
from app.talent.market.eligibility import (
    EligibilityReason,
    PersonAxes,
    can_list,
    can_list_axes,
    can_recruit_axes,
    cultivation_state,
    employment_state,
    person_axes,
)

_seq = 0


def _new_character(client, *, name: str, origin: str = "blank", template: str | None = None):
    global _seq
    _seq += 1
    payload: dict = {"name": f"{name} {_seq}", "origin": origin}
    if template is not None:
        payload["template"] = template
    return client.post("/api/v1/cultivation/characters", json=payload).json()


def _completed_events(db, person_id: int) -> list[Event]:
    """该 person 的结业事件（events 表跨用例累积，断言必须按属主过滤）。"""
    rows = db.scalars(sa.select(Event).where(Event.type == "cultivation.completed"))
    return [row for row in rows if (row.payload or {}).get("person_id") == person_id]


def _axes(cultivation: str | None, employment: EmploymentState, market: MarketState) -> PersonAxes:
    return PersonAxes(
        cultivation_state=cultivation, employment_state=employment, market_state=market
    )


# ---- 纯判定矩阵（不依赖数据库） ----


def test_can_list_matrix():
    unemployed, employed = EmploymentState.unemployed, EmploymentState.employed
    assert can_list_axes(_axes(None, unemployed, MarketState.unavailable)).reason is (
        EligibilityReason.not_ready
    )
    assert can_list_axes(_axes("cultivating", unemployed, MarketState.unavailable)).reason is (
        EligibilityReason.not_ready
    )
    assert can_list_axes(_axes("ready", employed, MarketState.unavailable)).reason is (
        EligibilityReason.employed
    )
    assert can_list_axes(_axes("ready", unemployed, MarketState.listed)).reason is (
        EligibilityReason.already_listed
    )
    decision = can_list_axes(_axes("ready", unemployed, MarketState.unlisted))
    assert decision.allowed and decision.reason is EligibilityReason.ok


def test_can_recruit_matrix():
    unemployed, employed = EmploymentState.unemployed, EmploymentState.employed
    assert can_recruit_axes(_axes("ready", unemployed, MarketState.unavailable)).reason is (
        EligibilityReason.not_listed
    )
    assert can_recruit_axes(_axes("ready", unemployed, MarketState.unlisted)).reason is (
        EligibilityReason.not_listed
    )
    assert can_recruit_axes(_axes("ready", unemployed, MarketState.listed)).allowed
    assert can_recruit_axes(_axes("ready", employed, MarketState.listed)).reason is (
        EligibilityReason.employed
    )


# ---- DB 三轴读面 ----


def test_axes_read_from_real_rows(client, db, default_company_id):
    created = _new_character(client, name="Axes", template="self_taught")
    person_id = created["person_id"]

    assert cultivation_state(db, person_id) == CultivationState.cultivating.value
    assert employment_state(db, person_id) is EmploymentState.unemployed
    axes = person_axes(db, person_id)
    assert axes.market_state is MarketState.unavailable  # 培养未完成
    assert can_list(db, person_id).reason is EligibilityReason.not_ready

    # 走完模板 → ready、无任职 → unlisted（有资格挂牌）
    program_id = client.get(f"/api/v1/cultivation/characters/{created['id']}").json()["programs"][
        0
    ]["id"]
    assert client.post(f"/api/v1/cultivation/programs/{program_id}/advance").status_code == 200

    axes = person_axes(db, person_id)
    assert axes.cultivation_state == CultivationState.ready.value
    assert axes.market_state is MarketState.unlisted
    assert can_list(db, person_id).allowed


def test_employed_person_cannot_be_listed(client, db, default_company_id):
    """已入职 person：employment 轴由 employments 的生效 primary 行派生（招募后的真实形态）。"""
    from app.models.enums import AssignmentType
    from app.models.position import PositionAssignment
    from app.repositories import persons as person_repo

    created = _new_character(client, name="Employed")
    person_id = created["person_id"]
    assert (
        client.post(f"/api/v1/cultivation/characters/{created['id']}/complete").status_code == 200
    )
    assert can_list(db, person_id).allowed  # ready + 无任职 → 有资格

    employee = make_employee(
        db,
        company_id=default_company_id,
        slug="t22-employed",
        person=person_repo.get_person(db, person_id),
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

    assert employment_state(db, person_id) is EmploymentState.employed
    decision = can_list(db, person_id)
    assert not decision.allowed and decision.reason is EligibilityReason.employed
    assert can_recruit_axes(person_axes(db, person_id)).reason is EligibilityReason.not_listed


# ---- 结业端点 ----


def test_free_cultivation_completes_without_any_threshold(client, db, default_company_id):
    """零证据 / 全 unrated 也能结业 —— 结业不看能力分（D1）。"""
    created = _new_character(client, name="NoEvidence")
    person_id = created["person_id"]

    profile = client.get(f"/api/v1/persons/{person_id}").json()
    assert profile["competencies"]["general"], "通用能力目录应当存在"
    assert all(row["score"] is None for row in profile["competencies"]["general"])

    response = client.post(f"/api/v1/cultivation/characters/{created['id']}/complete")
    assert response.status_code == 200, response.text
    assert response.json()["lifecycle"] == CultivationState.ready.value

    assert cultivation_state(db, person_id) == CultivationState.ready.value
    assert can_list(db, person_id).allowed

    events = _completed_events(db, person_id)
    assert len(events) == 1
    assert events[0].payload["reason"] == "free"
    assert events[0].company_id == default_company_id
    assert events[0].payload["identity_id"] == created["identity_id"]


def test_complete_is_idempotent(client, db, default_company_id):
    created = _new_character(client, name="Idempotent")
    first = client.post(f"/api/v1/cultivation/characters/{created['id']}/complete")
    assert first.status_code == 200

    second = client.post(f"/api/v1/cultivation/characters/{created['id']}/complete")
    assert second.status_code == 200
    assert second.json()["lifecycle"] == CultivationState.ready.value

    assert len(_completed_events(db, created["person_id"])) == 1, "重复结业不得重复发事件"


def test_complete_rejects_active_template_program(client, db, default_company_id):
    created = _new_character(client, name="Template", template="vocational")
    response = client.post(f"/api/v1/cultivation/characters/{created['id']}/complete")
    assert response.status_code == 409
    assert "模板培养进行中" in response.json()["detail"]

    profile = db.scalar(
        sa.select(CharacterProfile).where(CharacterProfile.person_id == created["person_id"])
    )
    assert profile is not None and profile.lifecycle == CultivationState.cultivating.value


def test_complete_template_character_after_final_stage_is_idempotent(
    client, db, default_company_id
):
    """模板走完后自动 ready：再调 complete 仍 200、不改状态、不发事件。"""
    created = _new_character(client, name="Auto", template="self_taught")
    program_id = client.get(f"/api/v1/cultivation/characters/{created['id']}").json()["programs"][
        0
    ]["id"]
    client.post(f"/api/v1/cultivation/programs/{program_id}/advance")

    response = client.post(f"/api/v1/cultivation/characters/{created['id']}/complete")
    assert response.status_code == 200
    assert response.json()["lifecycle"] == CultivationState.ready.value
    assert _completed_events(db, created["person_id"]) == [], (
        "模板自动结业不经过 complete 端点，因此不应有 free 结业事件"
    )


def test_complete_hides_other_company_characters(client, db, default_company_id):
    """公司边界：别家公司的角色 → 404（不泄露存在性），且不改状态。"""
    from app.models.organization import Company

    seq = db.scalar(sa.text("SELECT count(*) FROM companies"))
    rival = Company(name="Rival Co", slug=f"rival-t22-{seq}")
    db.add(rival)
    db.flush()
    other = CharacterProfile(
        person_id=make_person_rival(db, rival.id),
        identity_id=f"CH-RIVAL22{seq:04d}",
        origin="blank",
        owner_company_id=rival.id,
        lifecycle="cultivating",
    )
    db.add(other)
    db.commit()

    response = client.post(f"/api/v1/cultivation/characters/{other.id}/complete")
    assert response.status_code == 404
    db.refresh(other)
    assert other.lifecycle == "cultivating"


def make_person_rival(db, company_id: int) -> int:
    from factories import make_person

    person = make_person(db, slug=f"rival-t22-person-{company_id}")
    db.flush()
    return int(person.id)


# ---- D1 守卫：结业与资格判定路径不得出现能力分 ----

_COMPLETION_PATH_FILES = (
    "app/services/cultivation.py",
    "app/talent/market/eligibility.py",
)
_FORBIDDEN_TOKENS = {"score", "confidence", "evidence_count"}


def _code_tokens(tree):
    """标识符与字符串字面量的词元（docstring 除外：文档里说明"不看能力分"是允许的）。"""
    import ast

    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
    tokens: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            tokens.update(part for part in node.id.lower().split("_") if part)
        elif isinstance(node, ast.Attribute):
            tokens.update(part for part in node.attr.lower().split("_") if part)
        elif isinstance(node, ast.arg):
            tokens.update(part for part in node.arg.lower().split("_") if part)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            tokens.update(part for part in node.name.lower().split("_") if part)
        elif isinstance(node, ast.keyword) and node.arg:
            tokens.update(part for part in node.arg.lower().split("_") if part)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings or node.value == "evidence_count":
                continue
            tokens.update(part for part in re.findall(r"[a-zA-Z_]+", node.value.lower()) if part)
    return tokens


def test_completion_and_eligibility_never_reference_capability_scores():
    """D1：结业/资格不看能力分 —— 代码里不得出现 score / confidence / evidence_count。"""
    import ast
    from pathlib import Path

    server_root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for relative in _COMPLETION_PATH_FILES:
        tree = ast.parse((server_root / relative).read_text(encoding="utf-8"))
        hit = _code_tokens(tree) & _FORBIDDEN_TOKENS
        if hit:
            offenders.append(f"{relative}: {sorted(hit)}")
    assert not offenders, "结业与市场资格不得以能力阈值判定（设计 D1/plan §4.3）：\n" + "\n".join(
        f"  - {item}" for item in offenders
    )
