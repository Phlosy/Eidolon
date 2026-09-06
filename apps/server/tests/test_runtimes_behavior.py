"""Runtime 侧的消费与生效证明（§17.3 / §8）：T1 wire boundary + T3 挂载镜像。

"投影写进了文件" 不等于 "投影进了 agent 上下文"。本文件直接断言**真正发出去的载荷**
里带 behavior 块，并用诚实能力位 `brain_projection` 标注哪些 runtime 真的消费它。
"""

import asyncio
from pathlib import Path

import pytest

from app.brain import BrainTraits, policy_for
from app.brain.projection import behavior_block, mirror_into_runtime_dir
from app.models.organization import Employee
from app.models.runtime import RuntimeInstance as RuntimeInstanceRow
from app.repositories import runtimes as runtime_repo
from app.runtimes.base import (
    RuntimeCapabilities,
    RuntimeInstance,
    RuntimeSession,
    RuntimeStatus,
    TaskContext,
)
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.manager.docker_manager import (
    DockerRuntimeInstanceManager,
    employee_dirs,
)
from app.runtimes.mock.adapter import MockAdapter
from app.runtimes.openclaw.adapter import OpenClawAdapter
from app.schemas.runtime import RuntimeCapabilitiesOut

# `_container_spec` 不碰 daemon（provider=None 时连 secret 都不取），所以无需 FakeDockerService。
_MANAGER = DockerRuntimeInstanceManager(docker=object())


def _brain_with(db, employee_id: int, curiosity: float):
    brain = runtime_repo.ensure_brain(db, employee_id)
    brain.traits = BrainTraits.build({"curiosity": curiosity})
    db.commit()
    return brain


def _task_context(task_id: int = 99001) -> TaskContext:
    return TaskContext(
        task_id=task_id,
        project_id=99002,
        title="投影生效验证",
        kind="development",
        description="验证 behavior 块进入真实载荷",
        acceptance_criteria="行为块出现在请求里",
        employee_name="Test",
        employee_role="engineer",
    )


def _policy_dict(db, employee_id: int) -> dict:
    return policy_for(db, employee_id, None).as_dict()


# ---- T1：hermes / openclaw 把块拼进真正发出的 input ----


@pytest.mark.parametrize("adapter_cls", [HermesAdapter, OpenClawAdapter])
def test_behavior_block_is_appended_to_the_outbound_payload(
    db, employees_by_slug, adapter_cls, monkeypatch
):
    alice = employees_by_slug["alice"]
    _brain_with(db, alice["id"], 0.9)
    policy = _policy_dict(db, alice["id"])

    adapter = adapter_cls.__new__(adapter_cls)
    captured: dict = {}

    async def fake_pump(session, prompt, timeout):
        captured["prompt"] = prompt

    monkeypatch.setattr(adapter, "_pump", fake_pump)
    session = RuntimeSession(
        id="s-1",
        instance=RuntimeInstance(
            employee_id=alice["id"], profile="p", home_path="/tmp/x", status=RuntimeStatus.running
        ),
        task_id=1,
    )

    async def run():
        await adapter.send_task(
            session,
            "任务：投影",
            {"task_context": _task_context(), "behavior_policy": policy},
        )

    asyncio.run(run())
    prompt = captured["prompt"]
    assert "## 行为方式" in prompt, prompt
    assert f"rev {policy['profile_revision']}" in prompt
    assert prompt.startswith("任务：投影")  # 原 prompt 不被改写，只追加


def test_no_policy_means_no_block_in_the_payload(db, employees_by_slug, monkeypatch):
    """DEFAULT_POLICY（无指令）时不得往 prompt 里塞空标题。"""
    alice = employees_by_slug["alice"]
    adapter = HermesAdapter.__new__(HermesAdapter)
    captured: dict = {}

    async def fake_pump(session, prompt, timeout):
        captured["prompt"] = prompt

    monkeypatch.setattr(adapter, "_pump", fake_pump)
    session = RuntimeSession(
        id="s-2",
        instance=RuntimeInstance(
            employee_id=alice["id"], profile="p", home_path="/tmp/x", status=RuntimeStatus.running
        ),
        task_id=2,
    )

    async def run():
        await adapter.send_task(session, "任务：干净", {"task_context": _task_context()})

    asyncio.run(run())
    assert captured["prompt"] == "任务：干净"


# ---- 能力位诚实性 ----


@pytest.mark.parametrize(
    ("adapter_cls", "expected"),
    [(MockAdapter, True), (HermesAdapter, True), (OpenClawAdapter, True)],
)
def test_brain_projection_capability_is_reported(adapter_cls, expected: bool):
    adapter = adapter_cls.__new__(adapter_cls)
    assert adapter.get_capabilities().brain_projection is expected


def test_default_capabilities_do_not_claim_projection():
    """基线必须是 False：不消费投影的 runtime 不能假装生效（§8 可验性）。"""
    assert RuntimeCapabilities().brain_projection is False
    assert RuntimeCapabilitiesOut(**vars(RuntimeCapabilities())).brain_projection is False


# ---- T3：镜像进已挂载的 runtime 目录，并在实例元数据里记 revision ----


def test_container_spec_mirrors_behavior_and_records_revision(db, employees_by_slug):
    alice = employees_by_slug["alice"]
    employee = db.get(Employee, alice["id"])
    _brain_with(db, alice["id"], 0.85)
    instance = RuntimeInstanceRow(employee_id=alice["id"], runtime_type="hermes", image="x")

    spec = _MANAGER._container_spec(db, instance, employee, None, None, "token-1")

    mirrored = Path(spec["runtime_dir"]) / "eidolon" / "behavior.md"
    assert mirrored.exists(), sorted(
        p.relative_to(spec["runtime_dir"]) for p in Path(spec["runtime_dir"]).rglob("*")
    )
    content = mirrored.read_text(encoding="utf-8")
    policy = policy_for(db, alice["id"], None)
    assert str(policy.runtime.profile_revision) in content
    assert instance.metadata_json["behavior_revision"] == policy.runtime.profile_revision
    assert instance.metadata_json["behavior_policy_version"] == policy.runtime.policy_version
    # HERMES_MOUNT 挂的就是 runtime_dir ⇒ 容器内路径可预期
    assert str(Path(spec["runtime_dir"]).resolve()) in spec["volumes"]


def test_mirror_is_idempotent_and_lands_in_its_own_directory(tmp_path):
    first = mirror_into_runtime_dir(tmp_path, "rev 1\n")
    second = mirror_into_runtime_dir(tmp_path, "rev 2\n")
    assert first == second == tmp_path / "eidolon" / "behavior.md"
    assert second.read_text(encoding="utf-8") == "rev 2\n"
    # 绝不与第三方运行时文件同目录混写（决策 3）
    assert first.parent.name == "eidolon"


def test_employee_dirs_brain_is_not_a_mount():
    """§8 的前提：brain 目录不在挂载路径里，所以只写 brain/ 不可能生效。"""
    dirs = employee_dirs(1, "hermes")
    assert dirs["brain"] != dirs["runtime"]
    assert str(dirs["brain"]) not in str(dirs["runtime"])


def test_behavior_block_shape_is_stable():
    block = behavior_block(
        {
            "policy_version": "behavior-v1",
            "profile_revision": 42,
            "band": "high",
            "work_directives": ["第一条", "第二条"],
        }
    )
    assert block.splitlines()[0] == "## 行为方式（behavior-v1 · rev 42 · 档位 high）"
    assert block.splitlines()[1:3] == ["- 第一条", "- 第二条"]
    assert behavior_block(None) == ""
    assert behavior_block({"work_directives": []}) == ""
