"""EvidenceCollector 家族 —— 业务事实 → EvidenceCandidate（docs/evidence-pipeline.md §二/§三）。

业务系统只产生事实；Collector 负责解释“这个事实可以成为哪些能力的证据”。
registry 集中管理；新增来源 = 新注册 collector（不用改 orchestrator/业务 service）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.evidence import normalize
from app.evidence.candidate import EvidenceCandidate
from app.evidence.expectations import (
    expectations_for_scope,
    position_expectations,
)
from app.evidence.policy import EVENT_DISPATCH, POLICY, TASK_KIND_HINTS
from app.models.knowledge import LearningRecord, Skill, SkillUsage
from app.models.project import Project, Task, WorkSession
from app.models.project_delivery import ReviewMeeting


class EvidenceCollector(ABC):
    """把一个业务事实解释成能力证据候选。"""

    source_type: str = ""

    @abstractmethod
    def collect(self, db: Session, payload: dict) -> list[EvidenceCandidate]:
        raise NotImplementedError


def _session_runtime_types(db: Session, task_id: int) -> list[str]:
    return list(db.scalars(select(WorkSession.runtime_type).where(WorkSession.task_id == task_id)))


def _hints_to_expectations(db: Session, task_kind: str, employee_id: int) -> list[tuple[int, str]]:
    """任务类型提示 → (comp_def_id, role)。position 期望缺失时的兜底映射。"""
    from app.evidence.expectations import _definition_by_code_global

    result: list[tuple[int, str]] = []
    for code, role in TASK_KIND_HINTS.get(task_kind, []):
        definition = _definition_by_code_global(db, code)
        if definition is not None:
            result.append((int(definition.id), role))
    return result


class WorkItemCollector(EvidenceCollector):
    """任务完成/失败（含 testing 任务 → test 来源）。

    期望来源优先：task 显式期望 → 员工任职职位模板期望 → 任务类型提示（metadata
    里留 reason 标明来源）。测试类任务以 `test` 来源记账，其余以 `task` 来源。
    """

    source_type = "task"

    def _collect_one(self, db: Session, task: Task) -> list[EvidenceCandidate]:
        if task.assignee_id is None or task.status not in {"done", "failed"}:
            return []
        outcome = "done" if task.status == "done" else "failed"
        source_type = "test" if task.kind == "testing" else "task"
        signal = POLICY.signal_default(source_type, outcome)
        if signal is None:
            return []
        project = db.get(Project, task.project_id)
        environment = normalize.environment_for_runtime(_session_runtime_types(db, task.id))
        reliability = POLICY.reliability(source_type, environment)

        expectations = expectations_for_scope(db, "task", task.id)
        reason = "task_expectation"
        if not expectations:
            expectations = position_expectations(db, task.assignee_id)
            reason = "position_expectation"
        role_map: list[tuple[int, str]] = []
        if expectations:
            role_map = [(int(row.competency_definition_id), row.role) for row in expectations]
            reason = "task_expectation" if reason == "task_expectation" else "position_expectation"
        else:
            role_map = _hints_to_expectations(db, task.kind, task.assignee_id)
            reason = "task_kind_hint"
            if not role_map:
                return []

        occurred_at = task.actual_end_at or task.updated_at
        candidates: list[EvidenceCandidate] = []
        project_name = project.name if project is not None else ""
        for competency_id, role in role_map:
            strength = POLICY.strength_for_role(role)
            candidates.append(
                EvidenceCandidate(
                    employee_id=int(task.assignee_id),
                    source_type=source_type,
                    source_id=task.id,
                    source_ref=f"task:{task.id} ({task.title})",
                    observation=(
                        f"任务{'完成' if outcome == 'done' else '失败'}：{task.title}"
                        f"{'【' + project_name + '】' if project_name else ''}"
                    ),
                    competency_definition_id=competency_id,
                    signal=signal,
                    strength=strength,
                    reliability=reliability,
                    occurred_at=occurred_at,
                    environment=environment,
                    metadata={
                        "task_kind": task.kind,
                        "project_id": task.project_id,
                        "reason": reason,
                        "role": role,
                    },
                )
            )
        return candidates

    def collect(self, db: Session, payload: dict) -> list[EvidenceCandidate]:
        task = db.get(Task, payload["task_id"])
        if task is None:
            return []
        return self._collect_one(db, task)


class ReviewCollector(EvidenceCollector):
    """评审决策：被评审人（presenter）按期望获得 review 来源证据，信号随决策。"""

    source_type = "review"

    def collect(self, db: Session, payload: dict) -> list[EvidenceCandidate]:
        review = db.get(ReviewMeeting, payload["review_id"])
        if review is None or review.decision is None or review.presenter_employee_id is None:
            return []
        outcome = review.decision  # approved / conditionally_approved / …
        signal = POLICY.signal_default("review", outcome) or POLICY.signal_default(
            "review", "changes_requested"
        )
        expectations = position_expectations(db, review.presenter_employee_id)
        if not expectations:
            return []
        candidates: list[EvidenceCandidate] = []
        for row in expectations:
            candidates.append(
                EvidenceCandidate(
                    employee_id=int(review.presenter_employee_id),
                    source_type="review",
                    source_id=review.id,
                    source_ref=f"review:{review.id} ({review.title})",
                    observation=(
                        "评审"
                        + (
                            "通过"
                            if outcome in {"approved", "conditionally_approved"}
                            else "要求修改"
                        )
                        + f"：{review.title}"
                    ),
                    competency_definition_id=int(row.competency_definition_id),
                    signal=signal,
                    strength=POLICY.strength_for_role(row.role),
                    reliability=POLICY.reliability("review"),
                    occurred_at=review.completed_at or review.updated_at,
                    environment="",
                    metadata={
                        "decision": outcome,
                        "project_id": review.project_id,
                        "review_type": review.review_type,
                        "reason": "position_expectation",
                    },
                )
            )
        return candidates


class SkillUsageCollector(EvidenceCollector):
    """技能使用：人评 useful 且技能映射了能力 ⇒ 高质量证据。

    只读 `outcome`；`not_useful`/`failed` **不会**假装提升专业能力（§三十九）。
    """

    source_type = "skill_usage"

    def collect(self, db: Session, payload: dict) -> list[EvidenceCandidate]:
        usage = db.get(SkillUsage, payload["usage_id"])
        if usage is None or usage.outcome != "useful":
            return []
        skill = db.get(Skill, usage.skill_id)
        if skill is None or skill.competency_definition_id is None:
            return []
        signal = POLICY.signal_default(
            "skill_usage", "useful_success" if usage.success else "useful_failed"
        )
        if signal is None:
            return []
        return [
            EvidenceCandidate(
                employee_id=usage.employee_id,
                source_type="skill_usage",
                source_id=usage.id,
                source_ref=f"skill:{skill.name}#usage:{usage.id}",
                observation=f"技能被判定有用：{skill.name}",
                competency_definition_id=int(skill.competency_definition_id),
                signal=signal,
                strength=0.8,
                reliability=POLICY.reliability("skill_usage"),
                occurred_at=usage.updated_at,
                environment="",
                metadata={
                    "skill_id": skill.id,
                    "task_id": usage.task_id,
                    "outcome": usage.outcome,
                    "reason": "skill_usage_useful",
                },
            )
        ]


class LearningCollector(EvidenceCollector):
    """学习记录 → learning 来源证据（只贡献给 learning_growth，低频低权重）。

    学习次数 ≠ 学习能力高：这条证据可靠性低，仍要经过 Assessment。
    """

    source_type = "learning"

    def collect(self, db: Session, payload: dict) -> list[EvidenceCandidate]:
        record = db.get(LearningRecord, payload["record_id"])
        if record is None:
            return []
        from app.evidence.expectations import _definition_by_code_global

        definition = _definition_by_code_global(db, "learning_growth")
        if definition is None:
            return []
        return [
            EvidenceCandidate(
                employee_id=record.employee_id,
                source_type="learning",
                source_id=record.id,
                source_ref=f"learning:{record.id} ({record.kind}:{record.topic[:40]})",
                observation=f"学习记录：{record.topic or record.kind}",
                competency_definition_id=int(definition.id),
                signal=POLICY.signal_default("learning", "recorded"),
                strength=0.5,
                reliability=POLICY.reliability("learning"),
                occurred_at=record.updated_at,
                environment="",
                metadata={"kind": record.kind, "topic": record.topic, "reason": "learning_record"},
            )
        ]


class EvidenceCollectorRegistry:
    """source_type → collector 的集中注册/查询；新增来源在此登记。"""

    def __init__(self) -> None:
        self._collectors: dict[str, EvidenceCollector] = {}

    def register(self, collector: EvidenceCollector) -> None:
        assert collector.source_type, "collector 必须声明 source_type"
        self._collectors[collector.source_type] = collector

    def get(self, source_type: str) -> EvidenceCollector | None:
        return self._collectors.get(source_type)

    def event_collector(self, event_type: str) -> EvidenceCollector | None:
        source = EVENT_DISPATCH.get(event_type)
        return self._collectors.get(source) if source else None

    def all(self) -> list[EvidenceCollector]:
        return list(self._collectors.values())


REGISTRY = EvidenceCollectorRegistry()
REGISTRY.register(WorkItemCollector())
REGISTRY.register(ReviewCollector())
REGISTRY.register(SkillUsageCollector())
REGISTRY.register(LearningCollector())
