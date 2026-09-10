"""Autonomous Learning（P11，docs/autonomous-learning.md）。

WorkSession = 为公司项目工作；LearningSession = 为员工自身成长学习 —— 分开。
学习产出走既有表（KnowledgeItem private / Skill candidate / OpenQuestion /
可转 LearningPriority），**学习不直接改 Competency**（守卫）；Learned ≠ Truth。

预算硬限制：公司/员工每日 token/cost/session/time 上限；扣减在公司
`settings.learning_usage`（按日期）原子进行（先行 UPDATE 取行锁，再读改写，
SQLite 单写者 ⇒ 并发安全）；超过 → `WAITING_BUDGET` 且不执行（绝不偷偷继续）。

默认 autonomous_learning_enabled=False（真实 Provider 会产生成本，UI 明确提示）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.competency import CompetencyDefinition
from app.models.enums import (
    KnowledgeFreshness,
    LearningMode,
    LearningSessionStatus,
    LearningSourceType,
)
from app.models.knowledge import LearningPriority
from app.models.learning import LearningSession
from app.models.organization import Company, Employee
from app.models.provider import ModelBinding
from app.models.runtime import EmployeeBrain, RuntimeInstance
from app.repositories import knowledge as knowledge_repo
from app.repositories import organization as org_repo
from app.repositories import persons as person_repo
from app.repositories import runtimes as runtime_repo

LEARNING_POLICY_VERSION = "v1"
AUTONOMOUS_LEARNING_VERSION = "v1"


class LearningError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompanyLearningPolicy:
    """公司学习政策（存 Company.settings.learning_policy；缺省用这些值）。"""

    autonomous_learning_enabled: bool = False
    daily_token_budget: int = 20_000
    daily_cost_budget: float = 0.50
    max_session_minutes: int = 20
    max_sessions_per_day: int = 3
    allow_web_research: bool = True
    allow_practice: bool = True
    idle_delay_minutes: int = 15
    cooldown_minutes: int = 60


DEFAULT_COMPANY_LEARNING_POLICY = CompanyLearningPolicy()


def company_learning_policy(db: Session, company: Company | None) -> CompanyLearningPolicy:
    """公司政策（Company.settings.learning_policy 覆盖缺省）。"""
    if company is None:
        return DEFAULT_COMPANY_LEARNING_POLICY
    raw = (company.settings or {}).get("learning_policy")
    if not isinstance(raw, dict):
        return DEFAULT_COMPANY_LEARNING_POLICY
    kwargs = {
        key: raw[key]
        for key in (
            "autonomous_learning_enabled",
            "daily_token_budget",
            "daily_cost_budget",
            "max_session_minutes",
            "max_sessions_per_day",
            "allow_web_research",
            "allow_practice",
            "idle_delay_minutes",
            "cooldown_minutes",
        )
        if key in raw and isinstance(raw[key], (bool, int, float))
    }
    try:
        return CompanyLearningPolicy(**kwargs)
    except TypeError:  # pragma: no cover
        return DEFAULT_COMPANY_LEARNING_POLICY


def employee_learning_policy(db: Session, employee: Employee, company: Company | None) -> dict:
    """员工政策：默认继承公司；EmployeeBrain.learning_policy 可覆盖 enabled/预算/模式。"""
    base = company_learning_policy(db, company)
    # R1.1：brain 读口径已切 person_id（repo 入口解析，带旧口径回落）
    brain = runtime_repo.get_brain(db, employee.id)
    override = (brain.learning_policy or {}) if brain else {}
    result = {
        "inherit_company_policy": not bool(override),
        "enabled": base.autonomous_learning_enabled,
        "personal_daily_budget_override": None,
        "idle_delay_override": None,
    }
    if override:
        result["enabled"] = bool(override.get("enabled", base.autonomous_learning_enabled))
        result["personal_daily_budget_override"] = override.get("daily_token_budget")
        result["idle_delay_override"] = override.get("idle_delay_minutes")
    # 默认关闭 + 提示：自主学习会用真实 Provider 额度
    result["autonomous_learning_warning"] = not result["enabled"]
    return result


# ---------------------------------------------------------------------------
# Budget —— 原子扣减（防并发超预算）
# ---------------------------------------------------------------------------


def _usage(db: Session, company: Company, day: date) -> dict:
    data: dict = {}
    if not company.settings:
        company.settings = {}
    usage = company.settings.setdefault("learning_usage", {}).setdefault(day.isoformat(), {})
    if not isinstance(usage, dict):
        usage = {}
    data["tokens"] = int(usage.get("tokens", 0))
    data["cost"] = float(usage.get("cost", 0.0))
    data["sessions"] = int(usage.get("sessions", 0))
    return data


def _persist_usage(db: Session, company: Company, day: date, usage: dict) -> None:
    company.settings.setdefault("learning_usage", {}).setdefault(day.isoformat(), {})
    company.settings["learning_usage"][day.isoformat()] = {
        "tokens": int(usage["tokens"]),
        "cost": round(usage["cost"], 6),
        "sessions": int(usage["sessions"]),
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    db.flush()


def _acquire_write_lock(db: Session, company: Company) -> None:
    """取公司行锁（SQLite 单写者 ⇒ 串行化预算读改写）。"""
    db.execute(select(Company).where(Company.id == company.id).with_for_update())


def budget_for_session(
    db: Session, company: Company, employee: Employee, minutes: int | None = None
) -> dict:
    policy = company_learning_policy(db, company)
    usage = _usage(db, company, date.today())
    daily = bool(
        usage["tokens"] < policy.daily_token_budget
        and usage["cost"] < policy.daily_cost_budget
        and usage["sessions"] < policy.max_sessions_per_day
    )
    tokens = policy.daily_token_budget - usage["tokens"]
    cost = policy.daily_cost_budget - usage["cost"]
    minutes_cap = minutes or policy.max_session_minutes
    return {
        "allowed": daily,
        "remaining_tokens": max(0, tokens),
        "remaining_cost": max(0.0, cost),
        "budget_minutes": minutes_cap,
        "sessions_today": usage["sessions"],
        "status": (
            LearningSessionStatus.running.value
            if daily
            else LearningSessionStatus.waiting_budget.value
        ),
    }


def reserve_budget(
    db: Session, company: Company, employee: Employee, session: LearningSession
) -> bool:
    """原子扣减：先把会话跑起来（running）再扣——此处返回是否允许（失败置 waiting_budget）。"""
    policy = company_learning_policy(db, company)
    _acquire_write_lock(db, company)
    usage = _usage(db, company, date.today())
    tokens = min(session.budget_tokens or 0, policy.daily_token_budget - usage["tokens"])
    cost = min(session.budget_cost or 0.0, policy.daily_cost_budget - usage["cost"])
    if usage["sessions"] >= policy.max_sessions_per_day:
        session.status = LearningSessionStatus.waiting_budget.value
        db.flush()
        return False
    usage["tokens"] += tokens
    usage["cost"] += cost
    usage["sessions"] += 1
    session.tokens_used += tokens
    session.cost_used += cost
    session.budget_tokens = tokens
    session.budget_cost = cost
    _persist_usage(db, company, date.today(), usage)
    session.status = LearningSessionStatus.running.value
    db.flush()
    return True


# ---------------------------------------------------------------------------
# Create / Execute（mock 全流程；真实 web 检索在 Hermes adapter 侧记录来源）
# ---------------------------------------------------------------------------


def _idempotent_key(employee_id: int, source_type: str, source_id: int | None, topic: str) -> int:
    payload = f"{employee_id}:{source_type}:{source_id}:{topic}"
    return int(hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8], 16)


def _active_duplicate(
    db: Session, employee_id: int, source_type: str, source_id: int | None, topic: str
) -> bool:
    return (
        db.scalar(
            select(func.count())
            .select_from(LearningSession)
            .where(
                person_repo.read_criterion(
                    db, employee_id, LearningSession.person_id, LearningSession.employee_id
                ),
                LearningSession.source_type == source_type,
                LearningSession.source_id == source_id,
                LearningSession.topic == topic,
                LearningSession.status.in_(
                    [
                        LearningSessionStatus.planned.value,
                        LearningSessionStatus.running.value,
                        LearningSessionStatus.waiting_budget.value,
                    ]
                ),
            )
        )
        > 0
    )


def _cooldown_ok(db: Session, employee_id: int, topic: str, policy: CompanyLearningPolicy) -> bool:
    recent = db.scalar(
        select(func.max(LearningSession.completed_at)).where(
            person_repo.read_criterion(
                db, employee_id, LearningSession.person_id, LearningSession.employee_id
            ),
            LearningSession.topic == topic,
            LearningSession.status == LearningSessionStatus.completed.value,
        )
    )
    if recent is None:
        return True
    if recent.tzinfo is None:
        recent = recent.replace(tzinfo=UTC)
    minutes = (datetime.now(UTC) - recent).total_seconds() / 60
    return minutes >= policy.cooldown_minutes


def create_session(
    db: Session,
    employee_id: int,
    topic: str,
    *,
    source_type: str = LearningSourceType.manual.value,
    source_id: int | None = None,
    learning_mode: str = LearningMode.web_research.value,
    reason: str = "",
    minutes: int | None = None,
    commit: bool = True,
) -> LearningSession:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise LearningError("employee not found")
    company = db.get(Company, employee.company_id)
    policy = company_learning_policy(db, company)
    if _active_duplicate(db, employee_id, source_type, source_id, topic):
        raise LearningError("已有同主题的进行中学习会话")
    if source_type != LearningSourceType.manual.value and not _cooldown_ok(
        db, employee_id, topic, policy
    ):
        raise LearningError("同主题处于冷却期")
    session = LearningSession(
        company_id=employee.company_id,
        employee_id=employee_id,
        # 双写（R1.1）：person_id 经单一入口解析；company_id 是公司上下文快照，不跟人走
        person_id=person_repo.write_person_id(db, employee_id),
        topic=topic,
        reason=reason,
        source_type=source_type,
        source_id=source_id,
        status=LearningSessionStatus.planned.value,
        learning_mode=learning_mode,
        priority=0,
        budget_minutes=minutes or policy.max_session_minutes,
        budget_tokens=min(5_000, policy.daily_token_budget),
        budget_cost=min(0.15, policy.daily_cost_budget),
        metadata_json={"dedup_key": _idempotent_key(employee_id, source_type, source_id, topic)},
    )
    db.add(session)
    db.flush()
    _attach_runtime(db, session, employee)
    if commit:
        db.commit()
    return session


def _attach_runtime(db: Session, session: LearningSession, employee: Employee) -> None:
    runtime = db.scalar(select(RuntimeInstance).where(RuntimeInstance.employee_id == employee.id))
    if runtime is not None:
        session.runtime_instance_id = runtime.id
        session.runtime_type = runtime.runtime_type
        binding = (
            db.get(ModelBinding, runtime.model_binding_id) if runtime.model_binding_id else None
        )
        if binding is not None:
            session.provider_name = binding.alias or binding.model or ""
            session.model_name = binding.model or ""
    else:
        session.runtime_type = "mock"


def run_session(db: Session, session: LearningSession, *, execute: bool = True) -> LearningSession:
    """执行一个会话：budget 校验 → 运行（mock 模拟产出）→ 完成/失败。
    execute=False 时只做前置校验（预算/幂等），不真正产出（供测试节省）。
    """
    company = db.get(Company, session.company_id)
    employee = db.get(Employee, session.employee_id)
    if not reserve_budget(db, company, employee, session):
        db.commit()
        return session  # waiting_budget
    if session.status != LearningSessionStatus.running.value:
        return session
    session.started_at = datetime.now(UTC)
    if not execute:
        session.status = LearningSessionStatus.completed.value
        session.completed_at = datetime.now(UTC)
        session.summary = "dry-run（未产出）"
        db.commit()
        return session
    try:
        outputs = _produce_outputs(db, session, employee, company)
    except Exception as exc:  # noqa: BLE001 - runtime crash 语义
        session.status = LearningSessionStatus.failed.value
        session.error = str(exc)[:500]
        session.completed_at = datetime.now(UTC)
        _persist_usage(db, company, date.today(), _usage(db, company, date.today()))  # 保留已消耗
        db.commit()
        return session
    from app.core.config import settings as _settings

    forced_failure = getattr(_settings, "learning_force_failure", False)
    if forced_failure:  # pragma: no cover - 仅测试注入
        raise RuntimeError("forced failure")
    session.status = LearningSessionStatus.completed.value
    session.completed_at = datetime.now(UTC)
    session.summary = (
        f"{outputs['knowledge']} knowledge · {outputs['skill_candidates']}"
        f" skill candidate · {outputs['questions']} question"
    )
    session.metadata_json = {**(session.metadata_json or {}), "outputs": outputs}
    db.commit()
    return session


def _produce_outputs(
    db: Session, session: LearningSession, employee: Employee, company: Company
) -> dict:
    """mock 学习产出（全部 environment=mock / practice 标识；不碰 Competency）。"""
    mode = session.learning_mode
    outputs = {"knowledge": 0, "skill_candidates": 0, "questions": 0}
    now = datetime.now(UTC)
    environment = "mock"
    if mode == LearningMode.web_research.value:
        for index in range(3):
            # 双写（R1.2）：走 repo 入口，owner_employee_id 镜像 + owner_person_id 权威一起落
            knowledge_repo.create_knowledge_item(
                db,
                scope="private",
                owner_employee_id=employee.id,
                title=f"Web Research #{index + 1}: {session.topic}",
                content=f"simulated research finding {index + 1} for {session.topic}",
                topic=session.topic[:200],
                status="active",
                confidence=0.4,
                sources=[
                    {
                        "learning_session_id": session.id,
                        "url": f"https://example-research.local/item/{index + 1}",
                        "title": f"Source {index + 1} about {session.topic}",
                        "retrieved_at": now.isoformat(),
                        "claim": f"finding {index + 1}",
                        "source_quality": "low",
                        "confidence": 0.4,
                        "environment": environment,
                    }
                ],
                freshness_status=KnowledgeFreshness.fresh.value,
                learned_at=now,
            )
            outputs["knowledge"] += 1
        # 双写（R1.1）：走 repo 入口，employee_id 镜像 + person_id 权威一起落
        knowledge_repo.create_skill(
            db,
            employee_id=employee.id,
            name=f"Candidate: {session.topic[:80]}",
            description="学习产出的技能候选（未验证）",
            validation_status="candidate",
        )
        outputs["skill_candidates"] += 1
        knowledge_repo.create_learning_record(
            db,
            employee_id=employee.id,
            kind="question",
            topic=session.topic[:200],
            observation=f"Open question from learning: {session.topic}",
        )
        outputs["questions"] += 1
    elif mode == LearningMode.knowledge_review.value:
        knowledge_repo.create_knowledge_item(
            db,
            scope="private",
            owner_employee_id=employee.id,
            title=f"Review: {session.topic}",
            content="simulated knowledge review note",
            topic=session.topic[:200],
            status="active",
            confidence=0.5,
            freshness_status=KnowledgeFreshness.fresh.value,
            learned_at=now,
        )
        outputs["knowledge"] += 1
    elif mode == LearningMode.document_study.value:
        knowledge_repo.create_knowledge_item(
            db,
            scope="private",
            owner_employee_id=employee.id,
            title=f"Document study: {session.topic}",
            content="simulated document study note",
            topic=session.topic[:200],
            status="active",
            confidence=0.5,
            freshness_status=KnowledgeFreshness.fresh.value,
            learned_at=now,
        )
        outputs["knowledge"] += 1
    # practice：产出标记 environment=practice 的 SkillUsage/Evidence 留给真实 practice 路径
    db.flush()
    return outputs


# ---------------------------------------------------------------------------
# LearningPlanner —— 优先级选择（deterministic）
# ---------------------------------------------------------------------------


def plan_candidates(
    db: Session, employee: Employee, policy: CompanyLearningPolicy, limit: int = 5
) -> list[dict]:
    """按价值排序（DevPlan > 项目将要需求 > 失败 > 既有 Priority > SkillCandidate > 兴趣/好奇）。
    Curiosity 只影响 Desire，不覆盖公司 Development Need。"""
    from app.models.career import DevelopmentPlan, DevelopmentPlanItem

    buckets: list[list[dict]] = [[], [], [], [], [], []]

    for item in db.scalars(
        select(DevelopmentPlanItem)
        .join(DevelopmentPlan, DevelopmentPlan.id == DevelopmentPlanItem.plan_id)
        .where(
            DevelopmentPlan.employee_id == employee.id,
            DevelopmentPlan.status.in_(["active", "draft"]),
            DevelopmentPlanItem.status != "completed",
        )
    ):
        definition = db.get(CompetencyDefinition, item.competency_definition_id)
        buckets[0].append(
            {
                "topic": f"{definition.name}（DevPlan）" if definition else item.objective,
                "source_type": LearningSourceType.development_plan.value,
                "source_id": item.id,
                "priority": 0,
            }
        )

    for priority in db.scalars(
        select(LearningPriority).where(
            person_repo.read_criterion(
                db, employee.id, LearningPriority.person_id, LearningPriority.employee_id
            )
        )
    ).all():
        buckets[3].append(
            {
                "topic": priority.topic,
                "source_type": LearningSourceType.learning_priority.value,
                "source_id": priority.id,
                "priority": 3,
            }
        )

    # 好奇/兴趣只贡献 Desire；没有具体兴趣也不为它开课（Learning Value=0 不占预算）
    # R1.1：brain 读口径已切 person_id（repo 入口解析，带旧口径回落）
    brain = runtime_repo.get_brain(db, employee.id)
    interests = (brain.interests if brain else []) or []
    if interests:
        buckets[5].extend(
            {
                "topic": f"兴趣延伸：{interest}",
                "source_type": LearningSourceType.curiosity.value,
                "source_id": None,
                "priority": 5,
            }
            for interest in list(interests)[:2]
        )

    flat = [item for bucket in buckets for item in bucket]
    flat.sort(key=lambda item: (item["priority"], item["topic"]))
    return flat[:limit]


# ---------------------------------------------------------------------------
# Scheduler —— idle 触发（不空闲必学；贵但有价值 + 预算）
# ---------------------------------------------------------------------------


def scheduler_trigger(db: Session, *, global_check: bool = True) -> int:
    """为满足条件的空闲员工创建学习会话（幂等）。返回新建数。"""
    if global_check and not settings.autonomous_learning_enabled:
        return 0
    created = 0
    companies = list(db.scalars(select(Company)))
    for company in companies:
        policy = company_learning_policy(db, company)
        if not policy.autonomous_learning_enabled:
            continue
        people = org_repo.list_employees(db, company.id)
        for employee in people:
            if not _is_idle_and_valid(db, employee, policy):
                continue
            candidates = plan_candidates(db, employee, policy, limit=1)
            if not candidates:
                continue
            candidate = candidates[0]
            try:
                session = create_session(
                    db,
                    employee.id,
                    candidate["topic"],
                    source_type=candidate["source_type"],
                    source_id=candidate["source_id"],
                    reason="autonomous scheduler",
                )
            except LearningError:
                continue
            run_session(db, session)
            created += 1
    return created


def _is_idle_and_valid(db: Session, employee: Employee, policy: CompanyLearningPolicy) -> bool:
    # 有进行中任务/评审就不学（粗校验：无待办任务 + 无 running learning）
    from app.models.project import Task

    pending = db.scalar(
        select(func.count())
        .select_from(Task)
        .where(Task.assignee_id == employee.id, Task.status.in_(["todo", "in_progress"]))
    )
    if pending:
        return False
    running = db.scalar(
        select(func.count())
        .select_from(LearningSession)
        .where(
            person_repo.read_criterion(
                db, employee.id, LearningSession.person_id, LearningSession.employee_id
            ),
            LearningSession.status == LearningSessionStatus.running.value,
        )
    )
    if running:
        return False
    return True


# ---------------------------------------------------------------------------
# Snapshot（可解释：为什么这次这样工作）
# ---------------------------------------------------------------------------


def capture_work_session_behavior(
    db: Session, work_session_id: int, brain: EmployeeBrain, context: dict | None = None
) -> None:
    from app.brain.resolver import explain_behavior
    from app.brain.trait_policies import BehaviorContext
    from app.models.project import WorkSession

    work = db.get(WorkSession, work_session_id)
    if work is None:
        raise LearningError("work session not found")
    ctx = BehaviorContext(
        **{
            key: context[key]
            for key in context
            if key
            in {
                "task_type",
                "task_priority",
                "is_tutorial",
                "risk_level",
                "customer_facing",
                "production_critical",
                "budget_pct",
            }
        }
        if context
        else {}
    )
    explanation = explain_behavior(brain, ctx)
    work.behavior_snapshot_json = explanation
    work.behavior_snapshot_hash = hashlib.sha256(
        json.dumps(explanation, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    db.flush()
