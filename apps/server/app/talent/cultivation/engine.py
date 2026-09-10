"""培养引擎（T1.1，docs/cultivation-system-design.md §2 D3/D4）。

阶段推进 = 复用学习链路的 pre-hire 模式：

```
advance_program（模板阶段）
  → 确定性 RNG（rng_seed + 阶段序号派生）采样主题子集与 signal 噪声
  → 每主题一条 LearningSession（company/employee 空、person_id + program_id 挂培养实例）
  → produce_learning_outputs（与员工学习**完全共用**的产出原语）
  → 教育证据（upsert_evidence，source_kind=edu_* / environment=education，D4 分级）
  → education_events（履历事件流，回链 session 与证据）
  → 阶段序列走完：program.status=completed、profile.lifecycle=ready
```

自由养成（无模板）：`run_free_session` 逐次指定主题/途径/强度，走同一条产出路径。

确定性：同一 rng_seed 重跑同一阶段 ⇒ 主题子集与 signal 逐值一致（测试可注入 seed）。
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.evidence import normalize
from app.evidence.candidate import EvidenceCandidate
from app.evidence.policy import EDUCATION_ENVIRONMENT, POLICY
from app.models.cultivation import EducationEvent, TrainingProgram
from app.models.enums import CultivationState, LearningSessionStatus
from app.models.learning import LearningSession
from app.repositories import cultivation as cultivation_repo
from app.services import competency as competency_service
from app.services import learning as learning_service
from app.talent.cultivation.templates import TEMPLATES, FortuneEvent


class CultivationError(ValueError):
    pass


def _rng_for(program: TrainingProgram, stage_index: int) -> random.Random:
    """确定性 RNG：program.rng_seed + 阶段序号派生（同 seed 同结果）。"""
    return random.Random(f"{program.rng_seed}:{stage_index}")


def initialize_character_brain(
    db: Session, person_id: int, *, template_id: str | None, seed: str
) -> None:
    """人格成型基线（D5，愿景 §4.3）：注册表默认值 + 模板倾向求和 + 噪声。

    blank 自由养成 = 中性基线 + 噪声（际遇/会话偏移在此后累积）。brain 行
    person-only（employee_id NULL、person_id 挂接，v27 已放开）；traits 只经
    BrainTraits 双写纪律落库（权威 + curiosity 镜像）。幂等：已成型的不重置。
    """
    from app.brain.registry import TRAIT_REGISTRY
    from app.brain.traits import write_traits_to_brain
    from app.models.runtime import EmployeeBrain
    from app.repositories import runtimes as runtime_repo

    if runtime_repo.get_brain_by_person(db, person_id) is not None:
        return
    rng = random.Random(f"{seed}:traits")
    bias: dict[str, float] = {}
    template = TEMPLATES.get(template_id or "")
    if template is not None:
        for stage in template.stages:
            for key, delta in stage.trait_bias.items():
                bias[key] = bias.get(key, 0.0) + delta
    values = {
        key: spec.clamp(spec.default + bias.get(key, 0.0) + rng.uniform(-0.05, 0.05))
        for key, spec in TRAIT_REGISTRY.items()
    }
    brain = EmployeeBrain(employee_id=None, person_id=person_id)
    write_traits_to_brain(brain, values)
    db.add(brain)
    db.flush()


def _apply_trait_shifts(db: Session, person_id: int, triggered: list[FortuneEvent]) -> None:
    """际遇的人格偏移：触发即累加（clamp 到注册表值域；双写纪律同上）。"""
    shifts = [f.trait_shift for f in triggered if f.trait_shift]
    if not shifts:
        return
    from app.brain.registry import TRAIT_REGISTRY
    from app.brain.traits import BrainTraits, write_traits_to_brain
    from app.repositories import runtimes as runtime_repo

    brain = runtime_repo.get_brain_by_person(db, person_id)
    if brain is None:
        return
    current = BrainTraits.from_brain(brain).snapshot()
    for shift in shifts:
        for key, delta in shift.items():
            spec = TRAIT_REGISTRY.get(key)
            if spec is not None:
                current[key] = spec.clamp(current.get(key, spec.default) + delta)
    write_traits_to_brain(brain, current)
    db.flush()


def _definition_id_for(db: Session, competency_code: str) -> int:
    definition_id = db.scalar(
        sa.text(
            "SELECT d.id FROM competency_definitions d"
            " JOIN competency_domains dm ON dm.id = d.domain_id"
            " WHERE d.code = :code AND dm.company_id IS NULL"
        ),
        {"code": competency_code},
    )
    if definition_id is None:
        raise CultivationError(f"competency definition not found: {competency_code}")
    return int(definition_id)


def _run_cultivation_session(
    db: Session,
    program: TrainingProgram | None,
    person_id: int,
    *,
    topic: str,
    mode: str,
) -> tuple[LearningSession, dict, list[int]]:
    """一条培养期学习会话：建 LearningSession（person-only + program 挂接）→
    共用产出原语 → 状态机推进到 completed。预算/冷却不适用（无公司上下文）。"""
    session = LearningSession(
        company_id=None,
        employee_id=None,
        person_id=person_id,
        program_id=program.id if program else None,
        topic=topic[:500],
        reason="cultivation",
        source_type="cultivation",
        source_id=program.id if program else None,
        status=LearningSessionStatus.running.value,
        learning_mode=mode,
        runtime_type="mock",
        started_at=datetime.now(UTC),
    )
    db.add(session)
    db.flush()
    outputs, item_ids = learning_service.produce_learning_outputs(
        db, session, person_id=person_id, employee_id=None
    )
    session.status = LearningSessionStatus.completed.value
    session.completed_at = datetime.now(UTC)
    session.summary = (
        f"{outputs['knowledge']} knowledge · {outputs['skill_candidates']}"
        f" skill candidate · {outputs['questions']} question"
    )
    session.metadata_json = {**(session.metadata_json or {}), "outputs": outputs}
    db.flush()
    return session, outputs, item_ids


def _write_education_evidence(
    db: Session,
    person_id: int,
    *,
    session: LearningSession,
    evidence_kind: str,
    competency_code: str,
    signal: int,
    observation: str,
) -> int:
    """教育证据：走 upsert 的校验/幂等通道，D4 分级（reliability 由 policy 表给出）。"""
    row, _created = normalize.upsert_evidence(
        db,
        EvidenceCandidate(
            employee_id=None,
            person_id=person_id,
            source_type=evidence_kind,
            source_id=session.id,
            source_ref=f"learning_session://{session.id}",
            observation=observation,
            competency_definition_id=_definition_id_for(db, competency_code),
            signal=signal,
            strength=POLICY.reliability(evidence_kind),  # 档位即力度上限（粗粒度 v1）
            reliability=POLICY.reliability(evidence_kind),
            occurred_at=datetime.now(UTC),
            environment=EDUCATION_ENVIRONMENT,
            metadata={"cultivation": True},
        ),
    )
    return int(row.id)


def advance_program(db: Session, program_id: int) -> EducationEvent:
    """推进一个模板阶段：采样 → 学习产出 → 证据 → 履历事件；走完置 ready。"""
    program = db.get(TrainingProgram, program_id)
    if program is None:
        raise CultivationError("program not found")
    if program.status != "active":
        raise CultivationError(f"program is {program.status}, cannot advance")
    template = TEMPLATES.get(program.template)
    if template is None:
        raise CultivationError("自由养成（无模板）请用 run_free_session 逐次发起")
    if program.current_stage >= len(template.stages):
        raise CultivationError("program already at final stage")

    stage = template.stages[program.current_stage]
    # T1.2：人格基线在首个阶段推进时兜底成型（创建时服务层已初始化，幂等）
    initialize_character_brain(
        db, program.person_id, template_id=program.template, seed=program.rng_seed
    )
    rng = _rng_for(program, program.current_stage)
    # 知识覆盖采样：主题子集 + signal 噪声（分布而非定值，愿景 §3.1）
    covered = rng.sample(list(stage.topics), k=min(stage.intensity, len(stage.topics)))
    signals = [
        max(0, min(100, stage.signal_base + delta))
        for delta in (rng.randint(-stage.signal_spread, stage.signal_spread) for _ in covered)
    ]
    # 际遇（D5）：同一 RNG 流上按概率触发；扰动是修正不是替代 —— 基础产出照常，
    # 际遇在其上加减（signal 修正 / 额外主题 / traits 偏移）。
    triggered = [f for f in stage.fortune if rng.random() < f.probability]
    extra_topics = [topic for fortune in triggered for topic in fortune.extra_topics]
    for _topic in extra_topics:
        covered.append(_topic)
        signals.append(
            max(
                0,
                min(
                    100,
                    stage.signal_base + rng.randint(-stage.signal_spread, stage.signal_spread),
                ),
            )
        )
    if triggered:
        delta = sum(f.signal_delta for f in triggered)
        signals = [max(0, min(100, signal + delta)) for signal in signals]

    session_ids: list[int] = []
    evidence_ids: list[int] = []
    knowledge_total = 0
    for topic, signal in zip(covered, signals, strict=True):
        session, outputs, _item_ids = _run_cultivation_session(
            db, program, program.person_id, topic=topic, mode=stage.mode
        )
        knowledge_total += outputs["knowledge"]
        session_ids.append(int(session.id))
        evidence_ids.append(
            _write_education_evidence(
                db,
                program.person_id,
                session=session,
                evidence_kind=stage.evidence_kind,
                competency_code=stage.competency_code,
                signal=signal,
                observation=f"{stage.name}阶段完成「{topic}」",
            )
        )

    # 际遇的人格偏移：触发即累加进 brain traits（traits 只经 BrainTraits 双写纪律落库）
    _apply_trait_shifts(db, program.person_id, triggered)

    # 评估节点（D6）：复用聚合器的 person 入口；company_id 快照用角色 owner_company
    assessment_run_id = None
    if stage.assessment:
        profile = cultivation_repo.get_profile_by_person(db, program.person_id)
        if profile is None or profile.owner_company_id is None:
            raise CultivationError("角色无所属公司，无法做阶段评估（company 快照缺失）")
        run = competency_service.assess_person_competencies(
            db,
            program.person_id,
            owner_company_id=profile.owner_company_id,
            triggered_by=f"cultivation:program:{program.id}:stage:{program.current_stage}",
            commit=False,
        )
        db.flush()  # 聚合器 commit=False 时 run 还 pending —— flush 落 id 再入履历
        assessment_run_id = int(run.id)

    event = cultivation_repo.create_education_event(
        db,
        person_id=program.person_id,
        program_id=program.id,
        kind=stage.event_kind,
        topic=stage.name,
        outcome={
            "stage_id": stage.stage_id,
            "topics": covered,
            "signals": signals,
            "session_ids": session_ids,
            "knowledge_produced": knowledge_total,
            "duration_weeks": stage.duration_weeks,
            "fortunes": [f.key for f in triggered],
            "assessment_run_id": assessment_run_id,
        },
        evidence_id=evidence_ids[-1] if evidence_ids else None,
    )
    for fortune in triggered:
        cultivation_repo.create_education_event(
            db,
            person_id=program.person_id,
            program_id=program.id,
            kind="fortune",
            topic=stage.name,
            outcome={
                "fortune": fortune.key,
                "narrative": fortune.narrative,
                "signal_delta": fortune.signal_delta,
                "extra_topics": list(fortune.extra_topics),
                "trait_shift": fortune.trait_shift,
            },
        )
    program.current_stage += 1
    program.resource_used = {
        **(program.resource_used or {}),
        "sessions": int((program.resource_used or {}).get("sessions", 0)) + len(session_ids),
        "knowledge": int((program.resource_used or {}).get("knowledge", 0)) + knowledge_total,
    }
    if program.current_stage >= len(template.stages):
        program.status = "completed"
        profile = cultivation_repo.get_profile_by_person(db, program.person_id)
        if profile is not None:
            profile.lifecycle = CultivationState.ready.value
    db.commit()
    return event


def run_free_session(
    db: Session,
    person_id: int,
    *,
    topic: str,
    mode: str = "web_research",
    kind: str = "course",
    signal: int = 60,
    competency_code: str = "analysis_problem_solving",
) -> EducationEvent:
    """自由养成（无模板）：逐次指定主题/途径/强度，走同一产出路径。"""
    profile = cultivation_repo.get_profile_by_person(db, person_id)
    if profile is None:
        raise CultivationError("character not found")
    if profile.lifecycle == CultivationState.ready.value:
        raise CultivationError("角色已养成（ready），自由会话仅面向培养期")
    active = [
        program
        for program in cultivation_repo.list_programs(db, person_id)
        if program.status == "active"
    ]
    if active:
        raise CultivationError("角色有进行中的模板培养实例，请用 advance 推进")
    evidence_kind = f"edu_{kind}"
    session, outputs, _item_ids = _run_cultivation_session(
        db, None, person_id, topic=topic, mode=mode
    )
    evidence_id = _write_education_evidence(
        db,
        person_id,
        session=session,
        evidence_kind=evidence_kind,
        competency_code=competency_code,
        signal=max(0, min(100, int(signal))),
        observation=f"自由养成完成「{topic}」",
    )
    event = cultivation_repo.create_education_event(
        db,
        person_id=person_id,
        program_id=None,
        kind=kind,
        topic=topic,
        outcome={
            "session_ids": [int(session.id)],
            "knowledge_produced": outputs["knowledge"],
            "signal": signal,
        },
        evidence_id=evidence_id,
    )
    db.commit()
    return event
