"""Behavior 投影：把策略渲染成 agent 能消费的文本（§8.3 的唯一渲染出口）。

三条通道（§8）：
* T1 —— `behavior_block()` / `append_behavior_block()`：内联进**真正发出的 prompt**，由
  adapter 在 wire boundary 调用。这是保证生效的主通道（brain 目录并不挂载进容器）。
* T2 —— `render_profile_markdown()`：写 `data/employees/{id}/brain/` 供人查看与审计。
* T3 —— `mirror_into_runtime_dir()`：镜像到**已挂载**的 `runtime/<type>/eidolon/behavior.md`，
  仅在创建/重建实例时写（同一个 Hermes profile 不允许并发改写，见 docs/research.md:13）。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.brain.traits import BrainTraits
from app.core.config import settings

logger = logging.getLogger(__name__)

BEHAVIOR_SECTION_TITLE = "行为方式"
_DISCLAIMER = "（以上只影响工作方式与探索深度，不改变验收标准，也不改变事实与能力判断。）"

# T3 镜像路径：runtime 目录下的 eidolon/ 子目录 —— 绝不碰 SOUL.md / IDENTITY.md /
# AGENTS.md / MEMORY.md 这些第三方运行时文件（决策 3）。
MIRROR_RELATIVE_PATH = ("eidolon", "behavior.md")


def behavior_block(policy: Mapping[str, Any] | None) -> str:
    """渲染内联提示块。空策略 / 无指令 → 返回空串（不污染 prompt）。"""
    if not policy:
        return ""
    directives = [str(line) for line in (policy.get("work_directives") or []) if str(line).strip()]
    if not directives:
        return ""
    version = policy.get("policy_version", "behavior")
    revision = policy.get("profile_revision", 0)
    band = policy.get("band", "moderate")
    header = f"## {BEHAVIOR_SECTION_TITLE}（{version} · rev {revision} · 档位 {band}）"
    return "\n".join([header, *[f"- {line}" for line in directives], _DISCLAIMER])


def append_behavior_block(prompt: str, policy: Mapping[str, Any] | None) -> str:
    """T1：adapter 在真正发请求前调用 —— 投影进上下文的唯一路径。"""
    block = behavior_block(policy)
    return f"{prompt}\n\n{block}" if block else prompt


def render_profile_markdown(
    employee_name: str, traits: Mapping[str, float], policy: Any, revision: int
) -> str:
    """T2 正文（PROFILE.md / behavior.md 共用）。写盘格式稳定，便于 diff 审计。"""
    lines = [
        f"# {employee_name} · 员工大脑（v1）",
        "",
        f"- 策略版本：`{policy.runtime.policy_version}`",
        f"- 投影修订：`{revision}`",
        f"- 好奇心档位：{policy.runtime.band}",
        "",
        "## 人格特质",
        "",
    ]
    lines += [f"- `{key}` = {value:.2f}" for key, value in sorted(traits.items())]
    lines += ["", "## 工作方式", ""]
    directives = list(policy.runtime.work_directives)
    lines += [f"- {line}" for line in directives] or ["- （默认：不额外改变工作方式）"]
    lines += ["", "## 本任务的行为额度", ""]
    lines += [
        f"- 相邻经验检索条数：{policy.retrieval.knowledge_limit}",
        f"- 允许试用未验证技能：{'是' if policy.retrieval.include_candidate_skills else '否'}",
        f"- 反思未解问题数：{policy.reflection.open_question_count}",
        f"- 失败备选假设数：{policy.reflection.alternative_hypotheses}",
        f"- 延伸学习主题数：{policy.learning.followup_topics_per_task}",
        "",
        _DISCLAIMER,
        "",
    ]
    return "\n".join(lines)


def write_text_atomic(path: Path, content: str) -> None:
    """原子写（tmp + rename），避免容器读到半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def mirror_into_runtime_dir(runtime_dir: Path, content: str) -> Path:
    """T3：写进已挂载的 runtime 目录（幂等覆盖，revision 相同则内容相同）。"""
    target = runtime_dir.joinpath(*MIRROR_RELATIVE_PATH)
    write_text_atomic(target, content)
    return target


def brain_dir(employee_id: int) -> Path:
    """T2 目录。注意：这个目录**不**挂载进容器（§8 的前提），所以它只服务审计。"""
    return Path(settings.data_root) / "employees" / str(employee_id) / "brain"


def project_brain(db, employee: Any, brain: Any = None) -> dict[str, Any]:
    """T2 的唯一入口：解析策略 → 写文件 → 失败只告警。

    投影文件是**审计面**（brain 目录不挂载进容器），所以磁盘异常绝不能把 PATCH / 招聘请求打成 500；
    真正保证生效的是 T1（adapter 内联），见 §8。
    """
    from app.brain.resolver import policy_for
    from app.repositories import runtimes as runtime_repo

    if brain is None:
        brain = runtime_repo.ensure_brain(db, employee.id)
    policy = policy_for(db, employee.id, getattr(employee, "company", None))
    try:
        return write_brain_projection(db, employee, brain, policy)
    except OSError as exc:
        logger.warning("行为投影文件写入失败（不影响 T1 生效）：%s", exc)
        return {
            "revision": policy.runtime.profile_revision,
            "policy_version": policy.runtime.policy_version,
            "band": policy.runtime.band,
            "paths": [],
            "projection_markdown": "",
        }


def write_brain_projection(db, employee: Any, brain: Any, policy: Any) -> dict[str, Any]:
    """写 T2 文件：`brain/PROFILE.md` + `brain/eidolon/behavior.md`。

    绝不写 SOUL.md / IDENTITY.md / AGENTS.md / MEMORY.md（决策 3）。
    revision 相同时内容相同，所以重复调用幂等。
    """
    traits = BrainTraits.from_brain(brain)
    revision = policy.runtime.profile_revision
    markdown = render_profile_markdown(employee.name, traits.snapshot(), policy, revision)
    target = brain_dir(employee.id)
    paths = []
    for relative in (Path("PROFILE.md"), Path("eidolon") / "behavior.md"):
        file_path = target / relative
        write_text_atomic(file_path, markdown)
        paths.append(str(file_path))
    return {
        "revision": revision,
        "policy_version": policy.runtime.policy_version,
        "band": policy.runtime.band,
        "projection_markdown": markdown,
        "paths": paths,
    }
