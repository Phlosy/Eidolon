"""Rule-based reflection on task completion (no LLM in MVP).

See docs/architecture.md §6.1. On task done/failed: create a LearningRecord,
update the associated Skill, and auto-generate a scope=private KnowledgeItem
when confidence >= 0.8.

v1（docs/employee-brain-behavior-policy.md §7 接缝 4-5 / §11）：反思的**深度**（未解问题数、
失败备选假设数、延伸学习项、措辞风格）来自 BehaviorPolicy。原则是“人格改变工作方式，
不改变事实与能力”：本文件里的 confidence 计式、晋升条件、知识入库阈值全部与 trait 无关，
由 tests/test_evidence_invariants.py 守住。
"""

from datetime import UTC, datetime

from app.brain import BehaviorPolicy
from app.brain import policy_for as behavior_policy_for
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.events.bus import bus
from app.learning import priorities
from app.models.enums import (
    KnowledgeScope,
    LearningKind,
    SkillValidationStatus,
    TaskKind,
)
from app.repositories import knowledge as knowledge_repo
from app.repositories import project as project_repo
from app.repositories import runtimes as runtime_repo

logger = get_logger(__name__)

SKILL_BY_KIND: dict[str, str] = {
    TaskKind.order_review.value: "order-review",
    TaskKind.planning.value: "product-planning",
    TaskKind.research.value: "research",
    TaskKind.development.value: "software-development",
    TaskKind.testing.value: "quality-assurance",
    TaskKind.final_review.value: "release-review",
    TaskKind.general.value: "general",
}

KNOWLEDGE_CONFIDENCE_THRESHOLD = 0.8
SKILL_VALIDATION_MIN_ATTEMPTS = 3
SKILL_VALIDATION_SUCCESS_RATE = 0.8

# 未解问题模板：只**提问**，不断言。措辞不能包含任何关于原因/结果的结论，因为此时并无证据。
OPEN_QUESTION_TEMPLATES: tuple[str, ...] = (
    "本次 {kind} 任务的验收标准是否遗漏了边界情况？",
    "如果输入规模或并发提高一个数量级，当前做法还成立吗？",
    "这套流程里哪一步最依赖人工判断，能不能沉淀成可复查的步骤？",
)
INTEREST_QUESTION_TEMPLATE = "{interest} 方向的做法能不能迁移到 {kind} 任务里？"
# 失败备选假设：全部标注“待验证”，不写成结论。
HYPOTHESIS_TEMPLATES: tuple[str, ...] = (
    "假设：失败与信息不足有关（待验证：下一步先核对输入）",
    "假设：失败与流程步骤缺失有关（待验证：下一步先补齐中间产出）",
)
# topic_source=kind_map 时的延伸学习主题（固定映射，不从任务文本抽取 —— §13.2 防注入）
EXTENSION_TOPICS_BY_KIND: dict[str, tuple[str, ...]] = {
    TaskKind.research.value: ("source-verification",),
    TaskKind.development.value: ("code-review",),
    TaskKind.testing.value: ("regression-coverage",),
    TaskKind.order_review.value: ("risk-register",),
    TaskKind.planning.value: ("scope-baseline",),
    TaskKind.final_review.value: ("release-checklist",),
    TaskKind.general.value: (),
}


def reflect(
    task_id: int,
    employee_id: int,
    success: bool,
    duration_sec: float,
    error: str | None = None,
) -> None:
    """Manages its own session: commits before publishing events (SQLite single-writer)."""
    with SessionLocal() as db:
        task = project_repo.get_task(db, task_id)
        if task is None:
            return
        project = project_repo.get_project(db, task.project_id)
        company_id = project.company_id if project else None
        # 接缝 5：反思只从策略里拿“深度”额度；下面的 confidence / 晋升 / 入库阈值与 trait 无关。
        policy = behavior_policy_for(db, employee_id, getattr(project, "company", None))
        skill_name = SKILL_BY_KIND.get(task.kind, "general")
        confidence = 0.9 if success else 0.6
        result = "done" if success else "failed"

        lesson = (
            f"按模板流程可稳定完成 {task.kind} 类任务"
            if success
            else f"{task.kind} 任务失败，需要复盘原因并改进（{error or '未知原因'}）"
        )
        observation = f"耗时 {duration_sec:.1f}s；{error or '流程各阶段正常'}"
        if policy.reflection.note_style == "exploratory":
            # 只改措辞/丰富度，不新增事实：本条说明另有若干未验证问题另记 kind=question。
            observation += (
                f"；另有 {policy.reflection.open_question_count} 个未解问题待验证（不计入本条结论）"
            )
        record = knowledge_repo.create_learning_record(
            db,
            employee_id=employee_id,
            project_id=task.project_id,
            task_id=task.id,
            kind=LearningKind.reflection.value,
            topic=skill_name,
            problem=f"任务「{task.title}」执行结果：{result}",
            observation=observation,
            lesson=lesson,
            solution="保持当前模板流程" if success else "重试前先复盘失败原因，必要时调整方案",
            confidence=confidence,
            sources=[f"task://{task.id}"],
        )

        # §11：未解问题与失败备选假设。它们 **不是结论**：solution 空、confidence 0.0，
        # 因此永远进不了知识入库（>=0.8）与技能晋升（看成功率）的通道。
        question_count = 0
        for question in _open_questions(task.kind, policy, db, employee_id):
            knowledge_repo.create_learning_record(
                db,
                employee_id=employee_id,
                project_id=task.project_id,
                task_id=task.id,
                kind=LearningKind.question.value,
                topic=skill_name,
                problem=question,
                observation="",
                lesson=question,
                solution="",  # 未解问题没有解法（§11）
                confidence=0.0,
                sources=[f"task://{task.id}"],
            )
            question_count += 1
        if not success:
            for hypothesis in _hypotheses(policy):
                knowledge_repo.create_learning_record(
                    db,
                    employee_id=employee_id,
                    project_id=task.project_id,
                    task_id=task.id,
                    kind=LearningKind.question.value,
                    topic=skill_name,
                    problem=hypothesis,
                    observation=f"失败上下文：{error or '未知原因'}",
                    lesson=hypothesis,
                    solution="",  # 假设待验证，不给解法
                    confidence=0.0,
                    sources=[f"task://{task.id}"],
                )
                question_count += 1

        # memory entry (private, feeds GET /employees/{id}/memory)
        knowledge_repo.create_memory_entry(
            db,
            employee_id,
            kind="summary",
            content=f"[{task.kind}] {lesson}",
            source_ref=f"task://{task.id}",
        )

        # skill stats
        skill = knowledge_repo.get_skill_by_name(db, employee_id, skill_name)
        skill_created = False
        if skill is None:
            skill = knowledge_repo.create_skill(
                db,
                employee_id=employee_id,
                name=skill_name,
                description=f"自动从 {task.kind} 任务中积累的技能",
            )
            skill_created = True
        prev_total = skill.avg_duration_sec * skill.attempts
        skill.attempts += 1
        if success:
            skill.success_count += 1
        skill.avg_duration_sec = (prev_total + duration_sec) / skill.attempts
        skill.last_used_at = datetime.now(UTC)
        success_rate = skill.success_count / skill.attempts
        skill_validated = False
        if (
            skill.validation_status == SkillValidationStatus.candidate.value
            and skill.attempts >= SKILL_VALIDATION_MIN_ATTEMPTS
            and success_rate >= SKILL_VALIDATION_SUCCESS_RATE
        ):
            skill.validation_status = SkillValidationStatus.validated.value
            skill_validated = True

        # high-confidence lesson → private knowledge
        knowledge_item = None
        if confidence >= KNOWLEDGE_CONFIDENCE_THRESHOLD:
            knowledge_item = knowledge_repo.create_knowledge_item(
                db,
                scope=KnowledgeScope.private.value,
                owner_employee_id=employee_id,
                title=f"经验：{skill_name}",
                content=lesson,
                topic=skill_name,
                confidence=confidence,
                sources=[f"learning_record://{record.id}"],
            )

        if not success:
            priorities.record_failure(db, employee_id, skill_name, error or "task failed")

        # 接缝 4：延伸学习主题来自固定映射 / interests（内容字段），绝不从任务文本抽取（§13.2）。
        extension_topics = _extension_topics(policy, db, employee_id, task.kind)
        for topic in extension_topics:
            priorities.record_extension(
                db,
                employee_id,
                topic,
                policy.learning.followup_priority_score,
                f"行为策略延伸：{topic}",
            )
        # 本次任务实际被取用的候选技能（派发时已写入 SkillUsage）——投影生效的硬证据。
        candidate_skill_count = len(
            [
                usage
                for usage in knowledge_repo.list_skill_usages_for_task(db, task_id)
                if usage.selection_reason == "policy_candidate"
            ]
        )

        db.commit()

    bus.publish(
        "learning.completed",
        {
            "record_id": record.id,
            "employee_id": employee_id,
            "topic": skill_name,
            "success": success,
            "confidence": confidence,
        },
        company_id=company_id,
        actor_employee_id=employee_id,
        project_id=task.project_id,
        task_id=task.id,
    )
    if skill_created:
        bus.publish(
            "skill.created",
            {"id": skill.id, "employee_id": employee_id, "name": skill.name},
            company_id=company_id,
            actor_employee_id=employee_id,
        )
    if skill_validated:
        bus.publish(
            "skill.validated",
            {"id": skill.id, "employee_id": employee_id, "name": skill.name},
            company_id=company_id,
            actor_employee_id=employee_id,
        )
    if knowledge_item is not None:
        bus.publish(
            "knowledge.created",
            {"id": knowledge_item.id, "scope": knowledge_item.scope, "topic": skill_name},
            company_id=company_id,
            actor_employee_id=employee_id,
        )
    logger.info(
        "reflection recorded",
        extra={"employee_id": employee_id, "project_id": task.project_id, "task_id": task_id},
    )
    # §3.4 验收条件 4：投影真的改变了学习产出，用这个事件证明，而不是靠文本描述。
    bus.publish(
        "behavior.applied",
        {
            "employee_id": employee_id,
            "task_id": task_id,
            "policy_version": policy.runtime.policy_version,
            "profile_revision": policy.runtime.profile_revision,
            "band": policy.runtime.band,
            "open_questions": question_count,
            "extension_topics": list(extension_topics),
            "candidate_skills_retrieved": candidate_skill_count,
        },
        company_id=company_id,
        actor_employee_id=employee_id,
        project_id=task.project_id,
        task_id=task_id,
    )


def _open_questions(task_kind: str, policy: BehaviorPolicy, db, employee_id: int) -> list[str]:
    count = int(policy.reflection.open_question_count)
    if count <= 0:
        return []
    templates = list(OPEN_QUESTION_TEMPLATES)
    if policy.learning.topic_source.endswith("interest"):
        # interests 是内容字段（不是 trait），但只能用**提问**的方式进上下文（§13.2）。
        brain = runtime_repo.get_brain(db, employee_id)
        for interest in list(getattr(brain, "interests", None) or [])[:count]:
            templates.append(INTEREST_QUESTION_TEMPLATE.format(interest=interest, kind=task_kind))
    return [t.format(kind=task_kind) for t in templates[:count]]


def _hypotheses(policy: BehaviorPolicy) -> list[str]:
    return list(HYPOTHESIS_TEMPLATES[: int(policy.reflection.alternative_hypotheses)])


def _extension_topics(policy: BehaviorPolicy, db, employee_id: int, task_kind: str) -> list[str]:
    count = int(policy.learning.followup_topics_per_task)
    if count <= 0:
        return []
    topics: list[str] = list(EXTENSION_TOPICS_BY_KIND.get(task_kind, ()))
    if policy.learning.topic_source.endswith("interest"):
        brain = runtime_repo.get_brain(db, employee_id)
        topics += [str(item) for item in (getattr(brain, "interests", None) or [])]
    return list(dict.fromkeys(topics))[:count]
