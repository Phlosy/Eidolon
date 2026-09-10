"""Behavioral Policy v1 验收测试：逐条对应 docs/employee-brain-behavior-policy.md §3.4。

1. 改 curiosity 后，真正发出的 prompt 里行为块不同（T1）
2. 高 curiosity 时未验证技能可被交给 agent，且 SkillUsage 留下基准记录
3. 低 curiosity 时检索额度确实变小
4. 学习产出（question 记录 / 延伸优先级 / behavior.applied 事件）确实随之改变
5. 关掉开关时行为与旧实现**逐字相等**（回滚语义）
"""

import time
from pathlib import Path

import pytest
from sqlalchemy import select

from app.brain import DEFAULT_POLICY, BrainTraits, policy_for, resolve
from app.brain.config import DEFAULT_CONFIG, BehaviorPolicyConfig, config_for
from app.brain.policy import BehaviorPolicy
from app.brain.projection import behavior_block, write_brain_projection
from app.core.config import settings
from app.learning import retrieval
from app.models.enums import LearningKind
from app.models.knowledge import LearningRecord, SkillUsage
from app.models.organization import Employee
from app.repositories import events as event_repo
from app.repositories import knowledge as knowledge_repo
from app.repositories import runtimes as runtime_repo


class StubBrain:
    def __init__(self, curiosity: float = 0.5, learning_policy: dict | None = None):
        self.curiosity = curiosity
        self.traits = {"schema_version": 1, "curiosity": curiosity}
        self.learning_policy = learning_policy or {}
        self.interests: list[str] = []


def _policy(curiosity: float, config: BehaviorPolicyConfig = DEFAULT_CONFIG) -> BehaviorPolicy:
    return resolve(StubBrain(curiosity=curiosity), config)


def _wait_for(predicate, timeout=40.0, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _project(client, marker: int, description: str) -> dict:
    return client.post(
        "/api/v1/projects", json={"name": f"zz-bp-{marker}", "description": description}
    ).json()


# ---- 条件 5：默认策略 == 改造前 ----


def test_default_policy_carries_pre_v1_constants():
    assert DEFAULT_POLICY.retrieval.knowledge_limit == retrieval.TOP_KNOWLEDGE == 5
    assert DEFAULT_POLICY.retrieval.include_candidate_skills is False
    assert DEFAULT_POLICY.retrieval.novel_topic_ratio == 0.0
    assert DEFAULT_POLICY.retrieval.max_context_items == 8
    assert DEFAULT_POLICY.reflection.open_question_count == 0
    assert DEFAULT_POLICY.reflection.alternative_hypotheses == 0
    assert DEFAULT_POLICY.reflection.note_style == "standard"
    assert DEFAULT_POLICY.learning.followup_topics_per_task == 0
    assert DEFAULT_POLICY.learning.topic_source == "kind_map"
    assert DEFAULT_POLICY.runtime.work_directives == ()
    assert DEFAULT_POLICY.runtime.band == "moderate"


def test_default_policy_retrieval_matches_legacy_implementation(db, employees_by_slug):
    """与 pre-K1 版本的检索实现做差分对拍：只有私有知识命中时结果完全相同（含顺序）。

    K1 起生产路径还会查 department/company scope；本测试只造 private 条目且
    topic 带唯一 marker，共享 scope 的条目不可能命中这些 token，所以对拍仍成立。
    """
    from app.models.enums import KnowledgeScope, KnowledgeStatus

    alice = employees_by_slug["alice"]
    marker = int(time.time() * 1000)
    topics = [f"zz-diff-{marker}-{index}" for index in range(8)]
    for index, topic in enumerate(topics):
        knowledge_repo.create_knowledge_item(
            db,
            scope=KnowledgeScope.private.value,
            owner_employee_id=alice["id"],
            title=topic,
            content=f"body {index}",
            topic=topic,
            confidence=0.9,
            sources=[],
        )
    db.commit()
    title = " ".join(topics[:3])

    # pre-K1 实现（retrieval.py @ K1 之前）逐行搬来作对照：只查 private scope
    task_tokens = retrieval._tokens(title)
    expected: list[str] = []
    if task_tokens:
        scored: list[tuple[int, str]] = []
        for item in knowledge_repo.list_knowledge_items(
            db, scope=KnowledgeScope.private.value, employee_id=alice["id"]
        ):
            if item.status != KnowledgeStatus.active.value:
                continue
            if len(task_tokens & retrieval._tokens(f"{item.topic} {item.title}")):
                scored.append((1, item.topic or item.title))
        for _, topic in scored:
            if topic not in expected:
                expected.append(topic)
            if len(expected) >= 5:
                break

    result = retrieval.retrieve_for_task(db, alice["id"], title, "")
    assert result.knowledge[:5] == expected[:5]
    assert len(result.knowledge) <= 5
    assert not [skill for skill in result.skills if skill.is_candidate]


def test_kill_switch_restores_pre_v1_behavior(monkeypatch, db, employees_by_slug):
    alice = employees_by_slug["alice"]
    brain = runtime_repo.ensure_brain(db, alice["id"])
    brain.traits = BrainTraits.build({"curiosity": 0.95})
    db.commit()

    monkeypatch.setattr(settings, "behavior_policy_enabled", False)
    assert policy_for(db, alice["id"], None) == DEFAULT_POLICY
    monkeypatch.setattr(settings, "behavior_policy_enabled", True)
    assert policy_for(db, alice["id"], None).runtime.band == "high"


# ---- trait → 策略映射 ----


@pytest.mark.parametrize(
    ("low", "high"),
    [(0.0, 0.3), (0.3, 0.7), (0.7, 1.0), (0.05, 0.95), (0.5, 0.6)],
)
def test_quotas_are_monotonic_in_curiosity(low: float, high: float):
    a, b = _policy(low), _policy(high)
    assert a.retrieval.knowledge_limit <= b.retrieval.knowledge_limit
    assert a.retrieval.novel_topic_ratio <= b.retrieval.novel_topic_ratio
    assert a.retrieval.max_context_items <= b.retrieval.max_context_items
    assert a.reflection.open_question_count <= b.reflection.open_question_count
    assert a.learning.followup_topics_per_task <= b.learning.followup_topics_per_task
    assert a.learning.followup_priority_score <= b.learning.followup_priority_score
    assert a.runtime.profile_revision != b.runtime.profile_revision


def test_extension_priority_never_reaches_failure_score():
    """延伸项分数永远严格低于 FAILURE_PRIORITY_SCORE(70)。"""
    from app.learning.priorities import FAILURE_PRIORITY_SCORE

    for curiosity in [i / 20 for i in range(21)]:
        score = _policy(curiosity).learning.followup_priority_score
        assert score < FAILURE_PRIORITY_SCORE, curiosity


def test_candidate_skills_open_only_above_the_line():
    assert _policy(0.69).retrieval.include_candidate_skills is False
    assert _policy(0.70).retrieval.include_candidate_skills is True


def test_band_and_work_directives_track_the_trait():
    assert _policy(0.1).runtime.band == "low"
    assert _policy(0.5).runtime.band == "moderate"
    assert _policy(0.9).runtime.band == "high"
    assert _policy(0.1).runtime.work_directives != _policy(0.9).runtime.work_directives
    assert behavior_block(_policy(0.9).as_dict()) != behavior_block(_policy(0.1).as_dict())
    assert behavior_block(DEFAULT_POLICY.as_dict()) == ""


def test_company_policy_can_override_thresholds():
    company = type(
        "Company", (), {"settings": {"behavior_policy": {"candidate_skill_threshold": 0.95}}}
    )()
    config = config_for(company)
    assert config.candidate_skill_threshold == 0.95
    assert _policy(0.9, config).retrieval.include_candidate_skills is False
    assert _policy(0.95, config).retrieval.include_candidate_skills is True


def test_dirty_company_override_falls_back_entirely():
    bad = type("Company", (), {"settings": {"behavior_policy": {"open_question_max": "many"}}})()
    assert config_for(bad) == DEFAULT_CONFIG
    none = type("Company", (), {"settings": None})()
    assert config_for(none) == DEFAULT_CONFIG
    unknown = type("Company", (), {"settings": {"behavior_policy": {"confidence_boost": 0.9}}})()
    assert config_for(unknown) == DEFAULT_CONFIG  # 未知键被忽略，绝不引入新能力


def test_learning_off_is_runtime_only_and_yields_default_policy(db, employees_by_slug):
    """§13.4：学习关闭不改写持久人格，只让策略回到默认。"""
    alice = employees_by_slug["alice"]
    brain = runtime_repo.ensure_brain(db, alice["id"])
    brain.traits = BrainTraits.build({"curiosity": 0.9})
    brain.learning_policy = {"enabled": False, "source": "onboarding"}
    db.commit()
    frozen = dict(brain.traits)

    assert policy_for(db, alice["id"], None) == DEFAULT_POLICY
    db.refresh(brain)
    assert brain.traits == frozen, "学习关闭把 traits 改写了"

    brain.learning_policy = {"enabled": True}
    db.commit()
    assert policy_for(db, alice["id"], None).runtime.band == "high"


# ---- 条件 1-4：真实派发链路（mock runtime + 真 orchestrator + 真 reflection）----


def test_high_curiosity_projection_reaches_prompt_learning_and_usage(client, db, employees_by_slug):
    alice = employees_by_slug["alice"]
    marker = int(time.time() * 1000)
    patched = client.patch(f"/api/v1/employees/{alice['id']}/brain", json={"curiosity": 0.92})
    assert patched.status_code == 200, patched.text
    policy = patched.json()["behavior"]
    assert policy["band"] == "high"
    assert policy["retrieval"]["include_candidate_skills"] is True
    assert policy["reflection"]["open_question_count"] > 0

    skill = knowledge_repo.create_skill(
        db,
        employee_id=alice["id"],
        name=f"zz-candidate-{marker}",
        description="测试用候选技能",
        attempts=4,
        success_count=4,
    )
    db.commit()
    project = _project(client, marker, "验证行为投影端到端生效")

    def reflected():
        return bool(_question_rows(db, alice["id"], project["id"]))

    assert _wait_for(reflected), "没有产生人格派生的 question 记录 —— 投影链路未闭合"

    # 条件 1：投影进了真正发出的 prompt（mock 把它渲染进产出，可机器验证）
    detail = client.get(f"/api/v1/projects/{project['id']}").json()
    bodies = [artifact["content"] for artifact in detail["artifacts"]]
    assert any("## Behavior Projection" in body for body in bodies), bodies
    assert any(f"rev {policy['profile_revision']}" in body for body in bodies)
    assert any("档位 high" in body for body in bodies)

    # 条件 2：候选技能确实被交给 agent，并写下基准记录（含策略版本）
    usages = knowledge_repo.list_skill_usages(db, alice["id"])
    candidate_usages = [usage for usage in usages if usage.skill_id == skill.id]
    assert candidate_usages, [usage.selection_reason for usage in usages]
    usage = candidate_usages[0]
    assert usage.selection_reason == "policy_candidate"
    assert usage.policy_version == policy["policy_version"]
    assert usage.profile_revision == policy["profile_revision"]
    assert usage.task_id is not None

    # 条件 4：学习产出随之改变
    questions = _question_rows(db, alice["id"], project["id"])
    assert len(questions) >= policy["reflection"]["open_question_count"]
    for question in questions:
        assert question.solution == "" and question.confidence == 0.0  # §11
        assert question.task_id is not None  # task_id 不丢
    extensions = [
        priority
        for priority in knowledge_repo.list_learning_priorities(db, alice["id"])
        if priority.source == "behavior-extension"
    ]
    assert extensions, "延伸学习项未写入"
    assert all(item.score < 70 for item in extensions)

    applied = [
        event for event in event_repo.list_events(db, limit=300) if event.type == "behavior.applied"
    ]
    assert applied, "behavior.applied 事件缺失 ⇒ 无法机器证明投影生效"
    payload = applied[0].payload
    assert payload["policy_version"] == policy["policy_version"]
    assert payload["profile_revision"] == policy["profile_revision"]
    assert payload["open_questions"] > 0
    assert payload["candidate_skills_retrieved"] > 0

    # 基准指标接口可用（§10.2）
    benchmarks = client.get(f"/api/v1/employees/{alice['id']}/skill-usages/benchmarks").json()
    assert benchmarks["candidate_usages"] >= 1
    assert benchmarks["trial_rate"] is not None
    assert benchmarks["conversion_rate"] is not None
    # useful_rate 只由**已评价**的条目决定（没评过 ≠ 没用了），所以它可能是 None 或数值
    assert benchmarks["useful_rate"] is None or 0.0 <= benchmarks["useful_rate"] <= 1.0


def test_low_curiosity_shrinks_quotas_and_skips_questions(client, db, employees_by_slug):
    bob = employees_by_slug["bob"]
    marker = int(time.time() * 1000)
    patched = client.patch(f"/api/v1/employees/{bob['id']}/brain", json={"curiosity": 0.05})
    policy = patched.json()["behavior"]
    assert policy["band"] == "low"
    assert (
        policy["retrieval"]["knowledge_limit"] < DEFAULT_POLICY.retrieval.knowledge_limit
    )  # 条件 3
    assert policy["reflection"]["open_question_count"] == 0
    assert policy["retrieval"]["include_candidate_skills"] is False

    project = _project(client, marker, "低好奇心对照组")
    assert _wait_for(lambda: bool(_detail(client, project["id"])["tasks"]))

    def reflected():
        return bool(
            list(
                db.scalars(
                    select(LearningRecord).where(
                        LearningRecord.employee_id == bob["id"],
                        LearningRecord.kind == LearningKind.reflection.value,
                    )
                )
            )
        )

    assert _wait_for(reflected), "低好奇心员工没有产生反思记录"
    # 只看本项目产生的记录：其它用例可能给同一员工写过 question 行
    assert _question_rows(db, bob["id"], project["id"]) == [], "低档员工不应产生 question 记录"


def test_preview_endpoint_uses_server_side_thresholds(client):
    """UI 不得自己算档位：阈值只能从这里返回（§3.3）。"""
    high = client.get("/api/v1/behavior/preview?trait=curiosity&value=0.9")
    assert high.status_code == 200, high.text
    body = high.json()
    assert body["band"] == "high"
    assert body["retrieval"]["include_candidate_skills"] is True
    assert body["work_directives"]

    low = client.get("/api/v1/behavior/preview?trait=curiosity&value=0.1").json()
    assert low["band"] == "low"
    assert low["reflection"]["open_question_count"] == 0
    assert low["retrieval"]["knowledge_limit"] < body["retrieval"]["knowledge_limit"]

    # 学习关闭 ⇒ 预览同样回到默认额度（与运行时语义一致，§13.4）
    off = client.get(
        "/api/v1/behavior/preview?trait=curiosity&value=0.9&learning_enabled=false"
    ).json()
    assert off["retrieval"]["knowledge_limit"] == DEFAULT_POLICY.retrieval.knowledge_limit
    assert off["work_directives"] == []

    # 越界与未注册特质都必须明确失败，不能静默回落成默认档位
    assert client.get("/api/v1/behavior/preview?trait=curiosity&value=5").status_code == 422
    unknown = client.get("/api/v1/behavior/preview?trait=sass&value=0.9")
    assert unknown.status_code == 422
    assert "sass" in unknown.text


def _question_rows(db, employee_id: int, project_id: int) -> list[LearningRecord]:
    """按 project 过滤：其它用例可能往同一个（会话级）DB 里写过记录。"""
    return list(
        db.scalars(
            select(LearningRecord).where(
                LearningRecord.employee_id == employee_id,
                LearningRecord.kind == LearningKind.question.value,
                LearningRecord.project_id == project_id,
            )
        )
    )


def _detail(client, project_id: int) -> dict:
    return client.get(f"/api/v1/projects/{project_id}").json()


# ---- T2 投影文件 ----


def test_projection_files_contain_revision_and_are_idempotent(db, employees_by_slug):
    alice = employees_by_slug["alice"]
    employee = db.get(Employee, alice["id"])
    brain = runtime_repo.ensure_brain(db, employee.id)
    brain.traits = BrainTraits.build({"curiosity": 0.88})
    db.commit()
    policy = policy_for(db, employee.id, None)

    first = write_brain_projection(db, employee, brain, policy)
    second = write_brain_projection(db, employee, brain, policy)
    assert sorted(Path(path).name for path in first["paths"]) == ["PROFILE.md", "behavior.md"]
    for path in first["paths"]:
        file_path = Path(path)
        content = file_path.read_text(encoding="utf-8")
        assert str(policy.runtime.profile_revision) in content
        assert content == second["projection_markdown"]
    # 决策 3：只写自己的文件
    assert all(
        Path(path).name not in {"SOUL.md", "IDENTITY.md", "AGENTS.md", "MEMORY.md"}
        for path in first["paths"]
    )


def test_projection_revision_changes_with_the_trait(db, employees_by_slug):
    alice = employees_by_slug["alice"]
    employee = db.get(Employee, alice["id"])
    brain = runtime_repo.ensure_brain(db, employee.id)
    brain.traits = BrainTraits.build({"curiosity": 0.2})
    db.commit()
    low = write_brain_projection(db, employee, brain, policy_for(db, employee.id, None))
    brain.traits = BrainTraits.build({"curiosity": 0.9})
    db.commit()
    high = write_brain_projection(db, employee, brain, policy_for(db, employee.id, None))
    assert low["revision"] != high["revision"]


def test_projection_endpoint_reports_mirror_state(client, employees_by_slug):
    alice = employees_by_slug["alice"]
    client.patch(f"/api/v1/employees/{alice['id']}/brain", json={"curiosity": 0.8})
    body = client.get(f"/api/v1/employees/{alice['id']}/brain/projection").json()
    assert body["band"] == "high"
    assert body["revision"] > 0
    assert body["paths"]
    assert "## 人格特质" in body["projection_markdown"]
    # mock 员工没有 docker 容器 ⇒ T3 尚未镜像，这个位必须诚实为 False（§8 的可验性）
    assert body["mirrored_revision"] is None
    assert body["mirror_current"] is False


# ---- 结果评价接口（决策 2：v1 只做 API 面）----


def test_outcome_rating_never_touches_success(client, db, employees_by_slug):
    alice = employees_by_slug["alice"]
    marker = int(time.time() * 1000)
    skill = knowledge_repo.create_skill(
        db, employee_id=alice["id"], name=f"zz-rate-{marker}", description="评价用例"
    )
    usage = knowledge_repo.create_skill_usage(
        db,
        employee_id=alice["id"],
        skill_id=skill.id,
        task_id=None,
        selection_reason="policy_candidate",
        policy_version="behavior-v1",
        profile_revision=1,
        success=False,
    )
    db.commit()

    response = client.patch(
        f"/api/v1/employees/{alice['id']}/skill-usages/{usage.id}/outcome",
        json={"outcome": "useful"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "useful"
    assert body["outcome_source"] == "manual_rating"
    assert body["success"] is False  # 评价绝不改写客观事实（§10.2）

    bad = client.patch(
        f"/api/v1/employees/{alice['id']}/skill-usages/{usage.id}/outcome",
        json={"outcome": "great"},
    )
    assert bad.status_code == 422

    # 夹带 success 必须**明确报错**，不能静默丢弃后让调用方以为写成功了
    smuggled = client.patch(
        f"/api/v1/employees/{alice['id']}/skill-usages/{usage.id}/outcome",
        json={"outcome": "useful", "success": True},
    )
    assert smuggled.status_code == 422, smuggled.text
    db.expire_all()
    assert db.get(SkillUsage, usage.id).success is False
