"""教程引擎本身：需求注册表、声明校验、核心/实战拆分。

这些是"教程卡死"最常见的三个来源 —— requirement 拼错、步骤 id 撞车、
必做步骤被标成可跳过 —— 所以在单元层把它们钉住，而不是等浏览器里发现。
"""

import pytest

from app.tutorials import CORE_TUTORIAL_ID, DEFINITIONS, PRACTICE_TUTORIAL_ID
from app.tutorials import requirements as R
from app.tutorials.schema import flatten, validate

ALL_STEPS = {
    item["id"]: item for steps in DEFINITIONS.values() for item in flatten(steps)
}


def test_every_declared_requirement_is_registered():
    # 服务导入时已经会炸；这条测试让"是哪个步骤拼错了"直接出现在断言里
    missing = {
        f"{step_id} -> {item['requirement']}"
        for step_id, item in ALL_STEPS.items()
        if item["requirement"] not in R.known()
    }
    assert not missing


def test_evaluate_is_total_and_never_raises():
    assert R.evaluate("CEO_ACTIVE", R.Facts()) is False
    assert R.evaluate("NO_SUCH_GATE", R.Facts()) is False
    # facts 缺失（例如进度指向了一个已删除的项目）也只能得到"没过"，不能 500
    assert R.evaluate("COMPANY_CREATED", None) is False


def test_empty_facts_do_not_satisfy_any_business_gate():
    facts = R.Facts()
    unsatisfied = [name for name in R.REQUIREMENTS if R.evaluate(name, facts) is True]
    assert unsatisfied == [], f"空状态下就通过了这些门：{unsatisfied}"


def test_core_and_practice_are_separate_tutorials():
    core, practice = DEFINITIONS[CORE_TUTORIAL_ID], DEFINITIONS[PRACTICE_TUTORIAL_ID]
    assert core["sets_operating_stage"] is True
    assert practice["sets_operating_stage"] is False
    # 实战可以整体跳过（跳过只写状态，见 test_practice_skip_is_zero_cost）
    assert practice["allow_skip"] is True
    assert core["allow_skip"] is False
    core_ids = {item["id"] for item in flatten(core)}
    practice_ids = {item["id"] for item in flatten(practice)}
    assert not core_ids & practice_ids, "项目实战步骤必须完全移出核心教程"


def test_step_ids_are_globally_unique():
    # 服务靠 step id 反查所属教程，撞车会把进度记到错误的教程上
    ids = [item["id"] for steps in DEFINITIONS.values() for item in flatten(steps)]
    assert len(ids) == len(set(ids))


def test_every_step_points_at_a_route_and_target():
    for step_id, item in ALL_STEPS.items():
        assert item["route"].startswith("/"), step_id
        assert item["target_id"], step_id
        assert item["title_key"].startswith("steps."), step_id


def test_project_gates_are_project_scoped_only():
    for step_id, item in ALL_STEPS.items():
        scoped = R.project_scoped(item["requirement"])
        in_practice = step_id in {x["id"] for x in flatten(DEFINITIONS[PRACTICE_TUTORIAL_ID])}
        assert scoped == in_practice, f"{step_id} 的作用域与所属教程不一致"


# ------------------------------------------------------------------ 声明校验
def _definition_with(step: dict) -> dict:
    return {"id": "t", "stages": [{"id": "s", "steps": [step]}]}


def test_validate_catches_unknown_requirement():
    problems = validate(_definition_with({"id": "a", "requirement": "NOPE"}), {"YES"})
    assert any("NOPE" in p for p in problems)


def test_validate_catches_duplicate_step_ids():
    definition = {
        "id": "t",
        "stages": [
            {"id": "s1", "steps": [{"id": "dup", "requirement": "YES"}]},
            {"id": "s2", "steps": [{"id": "dup", "requirement": "YES"}]},
        ],
    }
    assert any("重复" in p for p in validate(definition, {"YES"}))


def test_validate_catches_skippable_required_step():
    step = {"id": "a", "requirement": "YES", "kind": "REQUIRED_ACTION", "allow_skip": True}
    problems = validate(_definition_with(step), {"YES"})
    assert any("REQUIRED_ACTION" in p for p in problems)


def test_validate_accepts_the_shipped_definitions():
    for tutorial_id, definition in DEFINITIONS.items():
        assert validate(definition, R.known()) == [], tutorial_id


def test_step_kind_drives_skip_permission():
    from app.tutorials.schema import step

    assert step("x", requirement="R", route="/r")["allow_skip"] is False  # 默认必做
    optional = step("y", kind="OPTIONAL_ACTION", requirement="R", route="/r")
    assert optional["allow_skip"] is True
    with pytest.raises(ValueError):
        step("z", kind="NONSENSE", requirement="R", route="/r")


def test_flatten_orders_steps_within_stages():
    steps = flatten(DEFINITIONS[CORE_TUTORIAL_ID])
    assert [item["order"] for item in steps] == sorted(item["order"] for item in steps)
    assert steps[0]["id"] == "company_setup"
    assert steps[-1]["id"] == "hire_qa"


def test_information_step_is_the_only_interaction_completing_kind():
    from app.tutorials.schema import INTERACTION_COMPLETES

    kinds = {item["kind"] for item in ALL_STEPS.values()}
    assert INTERACTION_COMPLETES == {"INFORMATION"}
    assert {
        step_id for step_id, item in ALL_STEPS.items() if item["kind"] in INTERACTION_COMPLETES
    } == {"company_setup"}
    assert kinds == {"REQUIRED_ACTION", "OPTIONAL_ACTION", "INFORMATION"}
