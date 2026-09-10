"""T1.1 · 培养引擎 + 教育证据分级（docs/cultivation-system-design.md §2 D3/D4）。

锁：模板推进端到端（学习产出 person-only 落库、证据分级、履历事件、ready 流转）；
确定性（同 seed 逐值一致）；自由养成；教育证据隔离（不进员工正式考核采集链）。
"""

import uuid

import pytest
import sqlalchemy as sa

from app.models.competency import CompetencyEvidence
from app.models.knowledge import KnowledgeItem, Skill
from app.models.learning import LearningSession
from app.repositories import cultivation as cultivation_repo
from app.services import cultivation as cultivation_service
from app.talent.cultivation import engine


def _character(db, default_company_id, *, template="academic", rng_seed=None):
    person, profile = cultivation_repo.create_character(
        db,
        name=f"Trainee {uuid.uuid4().hex[:6]}",
        origin="trained",
        owner_company_id=default_company_id,
    )
    program = cultivation_repo.create_program(
        db, person_id=person.id, template=template, rng_seed=rng_seed
    )
    db.commit()
    return person, profile, program


def test_template_advance_produces_person_only_outputs(db, default_company_id):
    """推进一个学院派阶段：会话/知识/技能/证据/事件全部按 person 口径落库。"""
    person, _profile, program = _character(db, default_company_id)

    event = engine.advance_program(db, program.id)

    program = cultivation_repo.list_programs(db, person.id)[0]
    assert program.current_stage == 1 and program.status == "active"
    assert event.kind == "course" and event.program_id == program.id
    stage_topics = event.outcome["topics"]
    assert len(stage_topics) == 2  # academic 小学 intensity=2

    sessions = list(
        db.scalars(sa.select(LearningSession).where(LearningSession.program_id == program.id))
    )
    assert len(sessions) == 2
    for session in sessions:
        # 培养会话：company/employee 空、person 挂接、状态机走完
        assert session.company_id is None and session.employee_id is None
        assert session.person_id == person.id
        assert session.status == "completed" and session.source_type == "cultivation"

    items = list(
        db.scalars(sa.select(KnowledgeItem).where(KnowledgeItem.owner_person_id == person.id))
    )
    assert items, "阶段产出知识条目"
    for item in items:
        assert item.owner_employee_id is None, "培养路径 person-only：镜像列为 NULL"
    # document_study 阶段只产知识条目；技能候选是 web_research 阶段的产出
    # （共用原语的模式语义，与员工路径一致）
    assert db.scalars(sa.select(Skill).where(Skill.person_id == person.id)).all() == []

    evidence = list(
        db.scalars(sa.select(CompetencyEvidence).where(CompetencyEvidence.person_id == person.id))
    )
    assert len(evidence) == 2
    for row in evidence:
        assert row.employee_id is None
        assert row.source_kind == "edu_course"  # 小学/初中阶段 = 课程档
        assert row.environment == "education"
        assert row.reliability == 0.5  # D4：edu_course ≈ mock 同档
    assert event.evidence_id == evidence[-1].id


def test_same_seed_produces_identical_stage_outputs(db, default_company_id):
    """确定性：同 rng_seed 的两个实例，首阶段采样逐值一致（主题子集 + signal）。"""
    seed = uuid.uuid4().hex
    _p1, _pr1, program_a = _character(db, default_company_id, rng_seed=seed)
    _p2, _pr2, program_b = _character(db, default_company_id, rng_seed=seed)

    event_a = engine.advance_program(db, program_a.id)
    event_b = engine.advance_program(db, program_b.id)
    assert event_a.outcome["topics"] == event_b.outcome["topics"]
    assert event_a.outcome["signals"] == event_b.outcome["signals"]


def test_advance_through_all_stages_marks_ready(db, default_company_id):
    """self_taught 只有一阶段：走完后 program completed + 角色 lifecycle=ready。"""
    person, profile, program = _character(db, default_company_id, template="self_taught")
    engine.advance_program(db, program.id)

    program = cultivation_repo.list_programs(db, person.id)[0]
    profile = cultivation_repo.get_profile_by_person(db, person.id)
    assert program.status == "completed" and profile.lifecycle == "ready"
    # self_taught 阶段是 web_research 模式 ⇒ 技能候选 person-only 落库
    skills = list(db.scalars(sa.select(Skill).where(Skill.person_id == person.id)))
    assert skills and all(skill.employee_id is None for skill in skills)
    with pytest.raises(engine.CultivationError):
        engine.advance_program(db, program.id)  # 已完成不可再推进


def test_free_session_for_blank_character(db, default_company_id):
    """自由养成：blank 角色逐次指定主题/途径/强度，走同一产出路径。"""
    person, _profile = cultivation_repo.create_character(
        db,
        name=f"Blank {uuid.uuid4().hex[:6]}",
        origin="blank",
        owner_company_id=default_company_id,
    )
    db.commit()

    event = engine.run_free_session(
        db, person.id, topic="线性代数自学", mode="web_research", kind="exam", signal=88
    )
    assert event.kind == "exam" and event.program_id is None
    evidence = db.scalar(
        sa.select(CompetencyEvidence).where(CompetencyEvidence.person_id == person.id)
    )
    assert evidence is not None
    assert evidence.source_kind == "edu_exam" and evidence.reliability == 0.8  # D4 考试档
    assert evidence.signal == 88


def test_free_session_rejected_while_template_program_active(db, default_company_id):
    person, _profile, _program = _character(db, default_company_id)
    with pytest.raises(engine.CultivationError):
        engine.run_free_session(db, person.id, topic="想两头跑")


def test_advance_via_api_and_timeline_visible(client, db, default_company_id):
    """API 全链：建角色（带模板）→ advance → 详情时间线出现事件。"""
    created = client.post(
        "/api/v1/cultivation/characters",
        json={
            "name": f"API {uuid.uuid4().hex[:6]}",
            "origin": "trained",
            "template": "self_taught",
        },
    )
    assert created.status_code == 201, created.text
    character = created.json()
    programs = cultivation_repo.list_programs(db, character["person_id"])
    response = client.post(f"/api/v1/cultivation/programs/{programs[0].id}/advance")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["program"]["status"] == "completed" and body["lifecycle"] == "ready"

    detail = client.get(f"/api/v1/cultivation/characters/{character['id']}")
    assert detail.status_code == 200
    events = detail.json()["events"]
    assert len(events) == 1 and events[0]["kind"] == "course"


def test_advance_other_companys_program_is_404(client, db, default_company_id):
    """公司隔离：别公司的 program 不能 advance（服务层 404）。"""
    _person, _profile, program = _character(db, default_company_id)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        cultivation_service.advance_program(db, program.id, default_company_id + 10_000)
    assert exc.value.status_code == 404


def test_v28_learning_sessions_program_id_column(tmp_path):
    """v28：learning_sessions.program_id nullable + 索引就位。"""
    from alembic import command

    from app.core.database import _alembic_config

    engine_db = sa.create_engine(f"sqlite:///{tmp_path / 'v28.db'}")
    config = _alembic_config()
    with engine_db.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    with engine_db.connect() as conn:
        columns = {
            row[1]: row for row in conn.execute(sa.text("PRAGMA table_info(learning_sessions)"))
        }
        assert "program_id" in columns and not columns["program_id"][3]  # notnull=0
        assert (
            conn.execute(
                sa.text(
                    "SELECT count(*) FROM sqlite_master WHERE type = 'index'"
                    " AND name = 'ix_learning_sessions_program_id'"
                )
            ).scalar_one()
            == 1
        )
