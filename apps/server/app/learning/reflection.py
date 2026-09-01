"""Rule-based reflection on task completion (no LLM in MVP).

See docs/architecture.md §6.1. On task done/failed: create a LearningRecord,
update the associated Skill, and auto-generate a scope=private KnowledgeItem
when confidence >= 0.8.
"""

from datetime import UTC, datetime

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
        skill_name = SKILL_BY_KIND.get(task.kind, "general")
        confidence = 0.9 if success else 0.6
        result = "done" if success else "failed"

        lesson = (
            f"按模板流程可稳定完成 {task.kind} 类任务"
            if success
            else f"{task.kind} 任务失败，需要复盘原因并改进（{error or '未知原因'}）"
        )
        record = knowledge_repo.create_learning_record(
            db,
            employee_id=employee_id,
            project_id=task.project_id,
            task_id=task.id,
            kind=LearningKind.reflection.value,
            topic=skill_name,
            problem=f"任务「{task.title}」执行结果：{result}",
            observation=f"耗时 {duration_sec:.1f}s；{error or '流程各阶段正常'}",
            lesson=lesson,
            solution="保持当前模板流程" if success else "重试前先复盘失败原因，必要时调整方案",
            confidence=confidence,
            sources=[f"task://{task.id}"],
        )

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
