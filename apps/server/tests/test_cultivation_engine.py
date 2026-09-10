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
    assert any(e["kind"] == "course" for e in events)
    assert all(e["kind"] in {"course", "fortune"} for e in events)  # 际遇合法出现


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


# ===========================================================================
# T1.2：际遇事件 + 人格成型 + 阶段评估（方案 D5/D6）
# ===========================================================================

import dataclasses  # noqa: E402

from app.brain.registry import TRAIT_REGISTRY  # noqa: E402
from app.brain.traits import BrainTraits  # noqa: E402
from app.models.competency import EmployeeCompetency  # noqa: E402
from app.repositories import runtimes as runtime_repo  # noqa: E402
from app.services import competency as competency_service  # noqa: E402
from app.talent.cultivation.templates import TEMPLATES, FortuneEvent  # noqa: E402


def test_fortune_sequence_is_deterministic_by_seed(db, default_company_id):
    """同 seed ⇒ 际遇序列逐值一致（际遇与阶段采样同一确定性 RNG 体系）。"""
    seed = uuid.uuid4().hex
    events = []
    for _ in range(2):
        _person, _profile, program = _character(db, default_company_id, rng_seed=seed)
        engine.advance_program(db, program.id)  # stage 0（无际遇表）
        engine.advance_program(db, program.id)  # stage 1（side_obsession 0.10）
        events.append(
            [
                # 确定性语义在采样结果上（主题/信号/际遇），不在 DB 自增 id 上
                (
                    e.kind,
                    e.outcome.get("topics"),
                    e.outcome.get("signals"),
                    e.outcome.get("fortunes"),
                    e.outcome.get("fortune"),
                )
                for e in cultivation_repo.list_education_events(db, program.person_id)
            ]
        )
    assert events[0] == events[1]


def test_different_seeds_produce_different_distributions(db, default_company_id):
    """验收标准 1：不同 seed 的产出有差异（N 次采样，断言出现至少两种不同组合）。"""
    combos = set()
    for _ in range(8):
        _person, _profile, program = _character(db, default_company_id, rng_seed=uuid.uuid4().hex)
        event = engine.advance_program(db, program.id)
        combos.add((tuple(event.outcome["topics"]), tuple(event.outcome["signals"])))
    assert len(combos) >= 2, "8 个不同 seed 产出完全一致 —— 确定性体系失效"


def test_forced_fortune_perturbs_signals_traits_and_timeline(db, default_company_id):
    """强制触发（概率=1 的 patch 模板）：signal 修正 + traits 偏移 + fortune 事件落库。"""
    person, _profile, program = _character(db, default_company_id)
    # 人格基线先成型（创建不走 service 时由 advance 兜底，这里显式初始化以便对拍）
    engine.initialize_character_brain(db, person.id, template_id="academic", seed=program.rng_seed)
    brain = runtime_repo.get_brain_by_person(db, person.id)
    baseline = BrainTraits.from_brain(brain).snapshot()["conscientiousness"]

    boosted = dataclasses.replace(
        TEMPLATES["academic"].stages[2],
        fortune=(
            FortuneEvent(
                key="competition_win",
                narrative="在学科竞赛中获奖",
                probability=1.0,
                signal_delta=10,
                trait_shift={"conscientiousness": 0.03},
            ),
        ),
    )
    monkey_template = dataclasses.replace(
        TEMPLATES["academic"],
        stages=TEMPLATES["academic"].stages[:2] + (boosted,) + TEMPLATES["academic"].stages[3:],
    )
    import app.talent.cultivation.engine as engine_module

    original = engine_module.TEMPLATES["academic"]
    engine_module.TEMPLATES["academic"] = monkey_template
    try:
        program.current_stage = 2  # 跳到高三（评估节点 + 强制际遇）
        db.commit()
        event = engine.advance_program(db, program.id)
    finally:
        engine_module.TEMPLATES["academic"] = original

    assert event.outcome["fortunes"] == ["competition_win"]
    assert event.outcome["assessment_run_id"] is not None  # 评估节点同时接线
    fortune_events = [
        e for e in cultivation_repo.list_education_events(db, person.id) if e.kind == "fortune"
    ]
    assert len(fortune_events) == 1
    assert fortune_events[0].outcome["narrative"] == "在学科竞赛中获奖"
    # traits 偏移落 brain（累加 + clamp）
    after = BrainTraits.from_brain(runtime_repo.get_brain_by_person(db, person.id)).snapshot()[
        "conscientiousness"
    ]
    expected = TRAIT_REGISTRY["conscientiousness"].clamp(baseline + 0.03)
    assert abs(after - expected) < 1e-9


def test_traits_baseline_direction_by_template(db, default_company_id):
    """人格成型方向：学院派 conscientiousness 均值 > 自学派；自学派 curiosity 均值更高。"""
    academic_consc, self_curiosity, academic_curiosity, self_consc = [], [], [], []
    for _ in range(10):
        for template_id, consc_out, curiosity_out in (
            ("academic", academic_consc, academic_curiosity),
            ("self_taught", self_consc, self_curiosity),
        ):
            person, _profile, program = _character(db, default_company_id, template=template_id)
            engine.initialize_character_brain(
                db, person.id, template_id=template_id, seed=program.rng_seed
            )
            traits = BrainTraits.from_brain(
                runtime_repo.get_brain_by_person(db, person.id)
            ).snapshot()
            consc_out.append(traits["conscientiousness"])
            curiosity_out.append(traits["curiosity"])
    assert sum(academic_consc) / 10 > sum(self_consc) / 10
    assert sum(self_curiosity) / 10 > sum(academic_curiosity) / 10


def test_blank_character_brain_is_neutral_baseline(db, default_company_id):
    """blank 自由养成：中性基线（无模板倾向）+ 噪声；person-only 行。"""
    person, _profile = cultivation_repo.create_character(
        db,
        name=f"Blank {uuid.uuid4().hex[:6]}",
        origin="blank",
        owner_company_id=default_company_id,
    )
    db.commit()
    engine.initialize_character_brain(db, person.id, template_id=None, seed="fixed-seed")
    brain = runtime_repo.get_brain_by_person(db, person.id)
    assert brain is not None and brain.employee_id is None
    traits = BrainTraits.from_brain(brain).snapshot()
    for spec in TRAIT_REGISTRY.values():
        assert abs(traits[spec.key] - 0.5) <= 0.05 + 1e-9, f"{spec.key} 偏离中性基线"


def test_stage_assessment_aggregates_education_evidence(db, default_company_id):
    """评估节点：assessment_runs 落库（person 口径 + owner_company 快照），
    能力画像来自证据聚合；edu 分级体现在 confidence（课程 0.5 vs 项目 0.9）。"""

    # 课程证据多的角色 vs 项目证据多的角色（同 signal）—— 分级体现在 confidence
    def _run_with(evidence_kind: str) -> float:
        person, _profile = cultivation_repo.create_character(
            db,
            name=f"Edu {uuid.uuid4().hex[:6]}",
            origin="trained",
            owner_company_id=default_company_id,
        )
        db.commit()
        session, _outputs, _ids = engine._run_cultivation_session(
            db, None, person.id, topic=f"主题 {uuid.uuid4().hex[:6]}", mode="web_research"
        )
        engine._write_education_evidence(
            db,
            person.id,
            session=session,
            evidence_kind=evidence_kind,
            competency_code="analysis_problem_solving",
            signal=80,
            observation="分级验证",
        )
        db.commit()
        run = competency_service.assess_person_competencies(
            db, person.id, owner_company_id=default_company_id, commit=False
        )
        db.commit()
        return run.outputs[str(_definition_id(db))]["confidence"]

    def _definition_id(db) -> int:
        return int(
            db.scalar(
                sa.text(
                    "SELECT d.id FROM competency_definitions d"
                    " JOIN competency_domains dm ON dm.id = d.domain_id"
                    " WHERE d.code = 'analysis_problem_solving' AND dm.company_id IS NULL"
                )
            )
        )

    course_conf = _run_with("edu_course")
    project_conf = _run_with("edu_project")
    assert project_conf > course_conf, "D4 分级必须体现在 confidence（项目 > 课程）"

    # 模板评估节点全链：跳到 vocational 实训结业阶段
    person, profile, program = _character(db, default_company_id, template="vocational")
    program.current_stage = 1  # 实训阶段（assessment=True）
    db.commit()
    event = engine.advance_program(db, program.id)
    run_id = event.outcome["assessment_run_id"]
    assert run_id is not None
    run_exists = db.scalar(
        sa.text("SELECT count(*) FROM assessment_runs WHERE id = :id"), {"id": run_id}
    )
    assert run_exists == 1
    rows = list(
        db.scalars(sa.select(EmployeeCompetency).where(EmployeeCompetency.person_id == person.id))
    )
    assert rows and all(r.employee_id is None for r in rows)
    assert all(r.status in ("provisional", "assessed") for r in rows)
