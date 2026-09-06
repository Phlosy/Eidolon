"""§3.3 的架构守卫：阈值只准住在 resolver + config 里。

这条约束一旦破口，`curiosity` 就会重新变成"散落在十几个文件里的 if"——正是本特性要解决的
问题本身。所以它必须是测试，而不是 code review 时的口头约定。
"""

import ast
import re
from pathlib import Path

import pytest

from app.brain import DEFAULT_POLICY, BrainTraits, policy_for, resolve
from app.brain.registry import TRAIT_REGISTRY
from app.learning import retrieval

APP_ROOT = Path(retrieval.__file__).resolve().parent.parent

# 允许直接接触 trait 的文件：契约层、写入侧（API / 持久化 / seed）、投影层。
TRAIT_AWARE_ALLOWLIST = {
    "brain/traits.py",
    "brain/resolver.py",
    "brain/registry.py",
    "brain/config.py",
    "brain/policy.py",
    "brain/projection.py",
    "brain/__init__.py",
    "services/lifecycle.py",  # hire：写侧
    "services/runtimes.py",  # PATCH /brain：写侧 + 摘要输出
    "services/seed.py",  # demo workforce 的默认人格
    "repositories/runtimes.py",  # ensure_brain：创建时补 traits
    "repositories/knowledge.py",  # SkillUsage 只透传 profile_revision
    "models/runtime.py",  # 列定义
    "schemas/runtime.py",  # 入参校验
}

CONSUMING_LAYERS = (
    "learning/",
    "workflow/",
    "runtimes/",
    "api/",
    "lifecycle/",
    "events/",
    "providers/",
)
TRAIT_NAMES = set(TRAIT_REGISTRY)


def _python_files(prefixes: tuple[str, ...]):
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = str(path.relative_to(APP_ROOT))
        if relative.startswith(prefixes):
            yield relative, path


def test_business_layers_never_read_individual_traits():
    """消费层不允许出现 `brain.curiosity` / `traits["curiosity"]` —— 只能读 BehaviorPolicy。"""
    violations: list[str] = []
    for relative, path in _python_files(CONSUMING_LAYERS):
        if relative in TRAIT_AWARE_ALLOWLIST:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in TRAIT_NAMES:
                violations.append(f"{relative}:{node.lineno} 读了 trait `{node.attr}`")
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value in TRAIT_NAMES
            ):
                violations.append(f"{relative}:{node.lineno} 按键取了 trait `{node.slice.value}`")
    assert not violations, (
        "行为代码直接读了人格字段，请改用 app.brain.policy_for() 的结果（§3.3）：\n"
        + "\n".join(violations)
    )


def test_no_numeric_comparison_next_to_a_trait_outside_the_resolver():
    """`if curiosity > 0.7` 这类散落判断是本特性要消灭的东西。"""
    threshold = re.compile(r"(?:<=|>=|<|>)\s*[\d.]+|[\d.]+\s*(?:<=|>=|<|>)")
    violations: list[str] = []
    for relative, path in _python_files(CONSUMING_LAYERS):
        if relative in TRAIT_AWARE_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        if not any(name in text for name in TRAIT_NAMES):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if any(name in stripped for name in TRAIT_NAMES) and threshold.search(stripped):
                violations.append(f"{relative}:{line_number}: {stripped}")
    assert not violations, "trait 附近出现阈值比较：\n" + "\n".join(violations)


def test_behavior_thresholds_are_not_hardcoded_in_business_layers():
    """行为阈值（候选技能线 / 延伸分上限）只能定义在 app/brain/config.py。"""
    suspicious_floats = {"0.7", "0.6", "0.65", "0.75", "0.8"}
    violations: list[str] = []
    for relative, path in _python_files(("learning/", "workflow/", "runtimes/", "api/")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        lines = text.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, float):
                continue
            rendered = f"{node.value:g}"
            if rendered not in suspicious_floats:
                continue
            line = lines[node.lineno - 1]
            # 只拦"看起来是行为阈值"的用法：与策略/候选/人格同现
            if re.search(r"candidate|curiosity|trait|behavior|policy", line, re.IGNORECASE):
                violations.append(f"{relative}:{node.lineno}: {line.strip()}")
    assert not violations, (
        "业务层硬编码了行为阈值，请放进 BehaviorPolicyConfig（§6）：\n" + "\n".join(violations)
    )


def test_resolve_is_not_called_outside_the_brain_package():
    """`resolve()` 属于 app.brain 内部；业务侧唯一入口是 policy_for()。"""
    violations: list[str] = []
    for relative, path in _python_files(("",)):
        if relative.startswith("brain/"):
            continue
        source = path.read_text(encoding="utf-8")
        imports_resolve = bool(re.search(r"from app\.brain[^\n]*import [^\n]*\bresolve\b", source))
        if imports_resolve:
            violations.append(relative)
    assert not violations, f"业务层直接引用 resolve()：{violations}"


def test_consuming_modules_go_through_policy_for():
    """派发与反思都必须经由 policy_for 消费策略（否则等于没有单一入口）。"""
    assert callable(policy_for) and callable(resolve)
    for module in ("workflow/orchestrator.py", "learning/reflection.py"):
        source = (APP_ROOT / module).read_text(encoding="utf-8")
        assert "policy_for" in source, f"{module} 没有通过 policy_for 消费策略"


def test_retrieval_has_no_private_quota_constant_in_its_body():
    """retrieval 里不能再自带额度常量：TOP_KNOWLEDGE 只作为"与旧实现等价"的锚点存在。"""
    source = (APP_ROOT / "learning/retrieval.py").read_text(encoding="utf-8")
    body = source[source.index("def retrieve_for_task") :]
    assert "TOP_KNOWLEDGE" not in body
    assert "retrieval.knowledge_limit" in body
    assert "policy" in body
    assert retrieval.TOP_KNOWLEDGE == DEFAULT_POLICY.retrieval.knowledge_limit  # 锚点仍然成立


def test_policy_is_immutable_and_carries_no_truth_semantics():
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        DEFAULT_POLICY.retrieval.knowledge_limit = 99  # type: ignore[misc]
    snapshot = _policy_dict_keys()
    # 按 key 判断，而不是按子串：candidate_min_success_rate 是"准入门槛"，不是结果判定
    assert not snapshot & {"confidence", "success", "success_rate", "verdict", "accepted", "score"}
    assert set(snapshot) >= {
        "policy_version",
        "profile_revision",
        "band",
        "traits",
        "retrieval",
        "reflection",
        "learning",
    }


def _policy_dict_keys() -> set[str]:
    snapshot = DEFAULT_POLICY.as_dict()
    keys = set(snapshot)
    for section in ("retrieval", "reflection", "learning"):
        keys |= set(snapshot[section])
    return keys


def test_trait_registry_is_the_single_source_for_trait_names():
    """新 trait 只需要注册一次；消费侧的守卫靠这个集合自动扩展。"""
    assert "curiosity" in TRAIT_REGISTRY
    assert BrainTraits({}).snapshot().keys() == TRAIT_REGISTRY.keys()
