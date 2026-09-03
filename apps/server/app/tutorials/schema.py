"""教程步骤的声明式 Schema。

这里只定义"一步长什么样"，不含任何业务判定：
- ``requirement`` 指向 :mod:`app.tutorials.requirements` 注册表里的门；
- ``kind`` 决定能不能跳过（REQUIRED_ACTION 永远不能）；
- ``target_id`` 是 React 侧 Target Registry 的 id，不是 CSS selector ——
  把步骤绑到 ``nth-child`` 这类脆弱选择器上，页面一改教程就瞎指。
"""

from __future__ import annotations

from typing import Any

REQUIRED_ACTION = "REQUIRED_ACTION"
OPTIONAL_ACTION = "OPTIONAL_ACTION"
INFORMATION = "INFORMATION"
PRACTICE = "PRACTICE"

# 定义文件里用短名，读起来更像产品语言（REQUIRED 不可跳过是类型层的性质）
REQUIRED = REQUIRED_ACTION
OPTIONAL = OPTIONAL_ACTION

KINDS = {REQUIRED_ACTION, OPTIONAL_ACTION, INFORMATION, PRACTICE}
INTERACTION_MODES = {"FOCUS_ONLY", "TARGET_ONLY", "NON_BLOCKING"}
PLACEMENTS = {"auto", "top", "right", "bottom", "left"}

# 只有信息类步骤可以"点了就算过"；动作类必须由业务状态兑现
INTERACTION_COMPLETES = {INFORMATION}


def step(
    step_id: str,
    *,
    kind: str = REQUIRED_ACTION,
    requirement: str,
    route: str,
    target_id: str | None = None,
    placement: str = "auto",
    interaction_mode: str = "TARGET_ONLY",
    allow_skip: bool | None = None,
    auto_advance: bool = True,
    order: int | None = None,
    title_key: str | None = None,
    description_key: str | None = None,
    why_key: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造一个步骤声明，缺省值按 kind 推导。

    ``allow_skip`` 不给时：REQUIRED_ACTION 不可跳，其余可跳 —— 让"必做"成为
    类型系统的性质，而不是每个定义里都要记得写一遍的 flag。
    """
    if kind not in KINDS:
        raise ValueError(f"unknown tutorial step kind: {kind}")
    if interaction_mode not in INTERACTION_MODES:
        raise ValueError(f"unknown interaction_mode: {interaction_mode}")
    if placement not in PLACEMENTS:
        raise ValueError(f"unknown placement: {placement}")
    return {
        "id": step_id,
        "kind": kind,
        "requirement": requirement,
        "route": route,
        "target_id": target_id,
        "placement": placement,
        "interaction_mode": interaction_mode,
        "allow_skip": kind != REQUIRED_ACTION if allow_skip is None else bool(allow_skip),
        "auto_advance": auto_advance,
        "order": order,
        "title_key": title_key or f"steps.{step_id}.label",
        "description_key": description_key or f"steps.{step_id}.explanation",
        # "为什么重要" 是可选的：渐进式披露，别每步都弹一大段
        "why_key": why_key or f"steps.{step_id}.why",
        "has_why": why_key is not None,
        "metadata": metadata or {},
    }


def flatten(definition: dict[str, Any]) -> list[dict[str, Any]]:
    """按 stage 顺序展开成线性步骤列表，并补上 order。"""
    out: list[dict[str, Any]] = []
    for stage in definition["stages"]:
        for item in stage["steps"]:
            step_copy = dict(item)
            step_copy["stage"] = stage["id"]
            if step_copy.get("order") is None:
                step_copy["order"] = len(out) * 10
            out.append(step_copy)
    return out


def validate(definition: dict[str, Any], known_requirements: set[str]) -> list[str]:
    """返回声明里的具体问题；服务启动/测试用它把错配炸出来。

    重点防四类事故：requirement 拼错（门永远过不去）、步骤 id 重复（进度会串）、
    必做步骤被误标成可跳过、以及字段缺失（前端只能渲染出一个没有聚光灯的空步骤）。
    """
    problems: list[str] = []
    seen: set[str] = set()
    for item in flatten(definition):
        step_id = item.get("id") or "<缺少 id>"
        if step_id in seen:
            problems.append(f"步骤 id 重复：{step_id}")
        seen.add(step_id)
        for field in ("kind", "requirement", "route", "target_id", "interaction_mode"):
            if not item.get(field):
                problems.append(f"步骤 {step_id} 缺少 {field}")
        kind = item.get("kind", REQUIRED_ACTION)
        if kind not in KINDS:
            problems.append(f"步骤 {step_id} 的 kind 未知：{kind}")
        mode = item.get("interaction_mode", "TARGET_ONLY")
        if mode not in INTERACTION_MODES:
            problems.append(f"步骤 {step_id} 的 interaction_mode 非法：{mode}")
        requirement = item.get("requirement")
        if requirement and requirement not in known_requirements:
            problems.append(f"步骤 {step_id} 的 requirement 未注册：{requirement}")
        if kind == REQUIRED_ACTION and item.get("allow_skip"):
            problems.append(f"步骤 {step_id} 是 REQUIRED_ACTION，不能允许跳过")
    return problems
