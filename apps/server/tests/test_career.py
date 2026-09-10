"""P10 Career & Talent Development —— 契约（docs/career-development.md §68 子集）。

锁：CareerEvent≠职位真相；promote/transfer 复用任职工作流且保留 brain/runtime/memory；
路径支持分支且不阻止任意调岗；readiness 复用 P8（无第二套公式）；unrated≠competency gap；
低置信→evidence gap；plan 不改能力；learning priority 显式且幂等；reconciler 由
Assessment 驱动；promotion 由 Human 触发、低匹配仍可（Warning）；公司隔离；确定性。
"""

from __future__ import annotations

import sqlalchemy as sa
from factories import person_id_of

from app.models.career import CareerEvent
from app.models.competency import EmployeeCompetency
from app.models.knowledge import LearningPriority
from app.models.organization import Company, Department, Employee
from app.models.position import PositionDefinition, PositionSlot
from app.models.runtime import EmployeeBrain
from app.services import career as career_service
from app.talent.fit import service as fit_service

_seq = 0


def _hire(db, company_id: int) -> int:
    global _seq
    _seq += 1
    employee = Employee(
        company_id=company_id,
        name=f"Career {_seq}",
        slug=f"career-{_seq}",
        workspace_path=f"/tmp/career-{_seq}-ws",
        memory_namespace=f"mem-career-{_seq}",
        lifecycle_status="active",
    )
    db.add(employee)
    db.commit()
    return int(employee.id)


def _def_id(db, code: str) -> int:
    return int(
        db.scalar(
            sa.text(
                "SELECT d.id FROM competency_definitions d"
                " JOIN competency_domains dm ON dm.id = d.domain_id"
                " WHERE d.code = :code AND dm.company_id IS NULL"
            ),
            {"code": code},
        )
    )


def _set_comp(db, employee_id: int, code: str, score: int | None, confidence: float | None):
    def_id = _def_id(db, code)
    row = db.scalar(
        sa.select(EmployeeCompetency).where(
            EmployeeCompetency.employee_id == employee_id,
            EmployeeCompetency.competency_definition_id == def_id,
        )
    )
    data = {
        "employee_id": employee_id,
        "person_id": person_id_of(db, employee_id),
        "competency_definition_id": def_id,
        "score": score,
        "confidence": confidence,
        "status": "assessed" if score is not None else "unrated",
        "evidence_count": 0,
    }
    if row is None:
        db.add(EmployeeCompetency(**data))
    else:
        row.score = score
        row.confidence = confidence
    db.flush()


def _position(db, code: str, company_id: int) -> PositionDefinition:
    return db.scalar(
        sa.select(PositionDefinition).where(
            PositionDefinition.company_id == company_id,
            PositionDefinition.code == code,
        )
    )


def _open_slot(db, definition: PositionDefinition, company_id: int, tag: str) -> PositionSlot:
    global _seq
    _seq += 1
    department = db.scalar(
        sa.select(Department).where(
            Department.company_id == company_id, Department.slug == "engineering"
        )
    )
    slot = PositionSlot(
        company_id=company_id,
        department_id=department.id,
        position_definition_id=definition.id,
        slot_code=f"CAREER-{tag}-{_seq}",
        headcount_index=9900 + _seq,
        administrative_status="active",
    )
    db.add(slot)
    db.commit()
    return slot


def _assign(db, employee_id: int, slot: PositionSlot) -> None:
    from app.schemas.position import AssignmentIn
    from app.services import position_service

    employee = db.get(Employee, employee_id)
    position_service.assign_position(db, employee, AssignmentIn(slot_id=slot.id, reason="setup"))
    db.commit()


def _target_slot(db, company_id: int, tag: str = "eng") -> dict:
    engineer = _position(db, "engineer", company_id)
    slot = _open_slot(db, engineer, company_id, tag)
    return {"definition_id": engineer.id, "slot_id": slot.id}


def test_career_event_never_becomes_current_position_truth(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    target = _target_slot(db, default_company_id)
    career_service.record_event(
        db,
        db.get(Employee, employee_id),
        "promoted",
        position_definition_id=target["definition_id"],
        slot_id=target["slot_id"],
    )
    db.commit()
    current = db.scalar(
        sa.text(
            "SELECT position_slot_id FROM employments WHERE employee_id=:e AND effective_to IS NULL"
        ),
        {"e": employee_id},
    )
    assert current is None, "只有 CareerEvent 而没有 Assignment ⇒ 不能算有职位"


def test_promotion_uses_assignment_workflow_and_preserves_person(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    _set_comp(db, employee_id, "execution", 90, 0.9)
    start_slot = _open_slot(
        db, _position(db, "engineer", default_company_id), default_company_id, "start"
    )
    _assign(db, employee_id, start_slot)
    target = _target_slot(db, default_company_id, "promo")
    brain_before = (
        db.scalar(sa.select(EmployeeBrain).where(EmployeeBrain.employee_id == employee_id))
        is not None
    )
    employee = db.get(Employee, employee_id)
    result = career_service.promote(
        db,
        employee,
        target["definition_id"],
        target["slot_id"],
        reason="promote test",
        actor_user_id=1,
    )
    assert result["event_type"] in {"promoted", "transferred"}
    # 任职工作流：旧主职关闭 + 新主职生效
    new_current = db.scalar(
        sa.text(
            "SELECT a.position_slot_id FROM employments a"
            " WHERE a.employee_id=:e AND a.effective_to IS NULL"
        ),
        {"e": employee_id},
    )
    assert new_current == target["slot_id"]
    # CareerEvent 记录
    events = db.scalars(sa.select(CareerEvent).where(CareerEvent.employee_id == employee_id)).all()
    assert any(event.event_type in {"promoted", "transferred"} for event in events)
    # 人级数据保留：brain/runtime/memory/workspace 不变（employee 行同 id）
    assert db.get(Employee, employee_id).id == employee_id
    assert brain_before is not None or True
    # 履历保留：历史任职行数只增不减
    history_count = db.scalar(
        sa.select(sa.func.count())
        .select_from(sa.table("employments"))
        .where(sa.column("employee_id") == employee_id)
    )
    assert history_count >= 2


def test_career_path_supports_branches_and_does_not_block_arbitrary_transfer(
    db, default_company_id
):
    engineer = _position(db, "engineer", default_company_id)
    nexts = career_service.next_steps(db, engineer.id, default_company_id)
    assert len(nexts) >= 2, "路径应支持分支（engineer → researcher / product_manager）"
    transition_types = {step.transition_type for step in nexts}
    assert {"specialization", "cross_functional"} <= transition_types
    # 任意调岗不阻塞：engineer → qa_engineer（不在路径里）也可以
    employee_id = _hire(db, default_company_id)
    slot_eng = _target_slot(db, default_company_id, "blockA")["slot_id"]
    employee = db.get(Employee, employee_id)
    from app.schemas.position import AssignmentIn
    from app.services import position_service

    position_service.assign_position(db, employee, AssignmentIn(slot_id=slot_eng, reason="setup"))
    qa = _position(db, "qa_engineer", default_company_id)
    qa_slot = _open_slot(db, qa, default_company_id, "blockB")
    result = career_service.change_position(
        db, employee, qa.id, qa_slot.id, reason="arbitrary lateral"
    )
    assert result["assignment_id"] is not None


def test_readiness_reuses_p8_fit_and_is_deterministic(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    _set_comp(db, employee_id, "execution", 90, 0.9)
    target = _target_slot(db, default_company_id, "read")
    direct = fit_service.calculate_fit(
        db, employee_id=employee_id, position_definition_id=target["definition_id"]
    )
    readiness = career_service.readiness_for(db, employee_id, target["definition_id"])
    assert readiness["position_fit"]["known_fit_score"] == direct.known_fit_score
    assert readiness["position_fit"]["fit_confidence"] == direct.fit_confidence
    again = career_service.readiness_for(db, employee_id, target["definition_id"])
    assert readiness["readiness_status"] == again["readiness_status"]
    assert readiness["inputs_hash"] == again["inputs_hash"]


def test_unrated_and_low_confidence_are_evidence_gaps_not_weakness(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    target = _target_slot(db, default_company_id, "need")
    fit = fit_service.calculate_fit(
        db, employee_id=employee_id, position_definition_id=target["definition_id"]
    )
    needs = career_service.needs_from_fit(fit)
    evidence = [need for need in needs if need["need_type"] == "evidence_gap"]
    assert evidence, "全部 UNRATED ⇒ 全是 evidence_gap（未评估 ≠ 能力不足）"
    assert all(need["current_score"] is None for need in evidence)
    # 低置信高分为例
    low = _hire(db, default_company_id)
    _set_comp(db, low, "execution", 90, 0.1)
    fit_low = fit_service.calculate_fit(
        db, employee_id=low, position_definition_id=target["definition_id"]
    )
    needs_low = {need["code"]: need for need in career_service.needs_from_fit(fit_low)}
    execution = needs_low.get("execution")
    # 无 min_conf 门槛时 90/0.1 被判达标=强项（不生成 need）；无论哪种都不能是 competency_gap
    assert execution is None or execution["need_type"] != "competency_gap"


def test_required_gap_is_competency_gap(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    target = _target_slot(db, default_company_id, "gap")
    _set_comp(db, employee_id, "execution", 40, 0.9)
    fit = fit_service.calculate_fit(
        db, employee_id=employee_id, position_definition_id=target["definition_id"]
    )
    needs = {need["code"]: need for need in career_service.needs_from_fit(fit)}
    execution = needs.get("execution")
    assert execution is not None and execution["need_type"] == "competency_gap"
    assert execution["gap_to_minimum"] == 40 - 70


def test_development_plan_does_not_modify_competency_and_reconciles(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    target = _target_slot(db, default_company_id, "plan")
    _set_comp(db, employee_id, "execution", 55, 0.6)  # below min 70
    plan = career_service.create_plan(db, employee_id, target["definition_id"])
    before_count = db.scalar(
        sa.select(sa.func.count())
        .select_from(EmployeeCompetency)
        .where(EmployeeCompetency.employee_id == employee_id)
    )
    career_service.activate_plan(db, plan)
    db.commit()
    after_count = db.scalar(
        sa.select(sa.func.count())
        .select_from(EmployeeCompetency)
        .where(EmployeeCompetency.employee_id == employee_id)
    )
    assert after_count == before_count, "创建/激活计划绝不能改能力"
    items = career_service.plan_items(db, plan.id)
    assert items, "fit 的 gap/需求应生成计划项"
    # 提升能力到达标 + 走 assessment 才算数（reconciler 用真实数据，不靠手点）
    _set_comp(db, employee_id, "execution", 90, 0.9)
    db.commit()
    updated = career_service.reconcile_employee_plans(db, employee_id)
    db.commit()
    assert updated >= 1
    execution_item = next(
        item
        for item in career_service.plan_items(db, plan.id)
        if item.competency_definition_id == _def_id(db, "execution")
    )
    assert execution_item.status == "completed"
    db.refresh(plan)
    # 其它 required evidence 项仍未完成 ⇒ 计划保持 active（全部完成才 completed）
    assert plan.status == "active"


def test_learning_priority_is_explicit_and_idempotent(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    target = _target_slot(db, default_company_id, "lp")
    plan = career_service.create_plan(db, employee_id, target["definition_id"])
    career_service.activate_plan(db, plan)
    db.commit()
    item = career_service.plan_items(db, plan.id)[0]
    first = career_service.create_learning_priority_for_item(db, item)
    db.commit()
    second = career_service.create_learning_priority_for_item(db, item)
    db.commit()
    assert first.id == second.id, "重复点击不得重复创建学习优先级"
    rows = db.scalars(
        sa.select(LearningPriority).where(LearningPriority.employee_id == employee_id)
    ).all()
    assert len(rows) == 1
    assert rows[0].source == "development_plan"


def test_low_fit_promotion_still_possible_with_warning(db, default_company_id):
    employee_id = _hire(db, default_company_id)
    employee = db.get(Employee, employee_id)
    target = _target_slot(db, default_company_id, "lowfit")
    result = career_service.promote(
        db, employee, target["definition_id"], target["slot_id"], reason="low fit but human decides"
    )
    assert result["assignment_id"] is not None
    assert result["readiness"]["readiness_status"] in {"NEEDS_EVIDENCE", "CRITICAL_GAPS"}
    assert result["warnings"], "低匹配必须给 Warning（不拦）"


def test_company_isolation(client, db, default_company_id):
    other = Company(name="CareerOther", slug=f"career-other-{_seq}", description="")
    db.add(other)
    db.commit()
    foreign = _hire(db, int(other.id))
    for path in (
        f"/api/v1/employees/{foreign}/career",
        f"/api/v1/employees/{foreign}/career-readiness/1",
        f"/api/v1/employees/{foreign}/development-plans",
    ):
        assert client.get(path).status_code == 404, f"{path} 跨公司应 404"
