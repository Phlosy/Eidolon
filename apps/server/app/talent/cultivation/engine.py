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
from app.models.enums import LearningSessionStatus
from app.models.learning import LearningSession
from app.repositories import cultivation as cultivation_repo
from app.services import learning as learning_service
from app.talent.cultivation.templates import TEMPLATES


class CultivationError(ValueError):
    pass


def _rng_for(program: TrainingProgram, stage_index: int) -> random.Random:
    """确定性 RNG：program.rng_seed + 阶段序号派生（同 seed 同结果）。"""
    return random.Random(f"{program.rng_seed}:{stage_index}")


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
    rng = _rng_for(program, program.current_stage)
    # 知识覆盖采样：主题子集 + signal 噪声（分布而非定值，愿景 §3.1）
    covered = rng.sample(list(stage.topics), k=min(stage.intensity, len(stage.topics)))
    signals = [
        max(0, min(100, stage.signal_base + rng.randint(-stage.signal_spread, stage.signal_spread)))
        for _topic in covered
    ]

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
        },
        evidence_id=evidence_ids[-1] if evidence_ids else None,
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
            profile.lifecycle = "ready"
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
    if profile.lifecycle == "ready":
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
