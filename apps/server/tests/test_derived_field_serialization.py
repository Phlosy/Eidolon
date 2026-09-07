"""ADR-12 guard：派生字段禁止带"语义上合法"的假默认值（docs/architecture.md §17）。

`not computed != 0`、`not computed != []`、`not computed != false`。

两条防线：

1. **结构防线（本文件）**：维护派生字段名清单 `DERIVED_OUT_FIELDS`，逐一检查仓库里
   所有 Pydantic Out schema —— 名单上的字段**不允许**出现 `0 / False / "" / [] / {}`
   这类默认值。允许的只有"必填"或"默认 None"（缺失以 null 呈现，缺得明白）。
2. **行为防线（在各自领域测试里）**：端点返回的数字必须来自计算路径而不是 schema
   默认值（position_service 的 slots_out/definitions_out/assignment_out 已有对应断言）。

给未来加派生字段（competency_score / confidence / trend / assessment_result …）时：
先把字段名加进 `DERIVED_OUT_FIELDS`，再写 schema —— 忘加会被这条测试追着改。
"""

from __future__ import annotations

import importlib
import inspect
from typing import Any

import pytest
from pydantic import BaseModel

# 派生字段名清单。加新领域（能力/考核）时把名字补进来。
# 命名按"响应里的字段名"记；只查 Out（响应）schema。
DERIVED_OUT_FIELDS: frozenset[str] = frozenset(
    {
        # position（P4a/P4b，docs/position-system.md）
        "slot_count",
        "vacant_count",
        "occupied_slot",
        "package_slugs",
        "occupancy_status",
        "integrity",
        # workforce（P4a-2）
        "workforce_status",
        "has_primary_assignment",
        "occupies_establishment",
        "current_position",
        # 未来能力域（P5+，先锁名再写 schema）
        "score",
        "confidence",
        "evidence_count",
        "trend",
        "trend_window",
        "position_fit",
        "assessment_result",
        "runtime_health",
        "competency_score",
    }
)

# Out schema 所在模块（schema 层，不含 In/请求模型 —— 请求模型本来就要给默认值语义）
_SCHEMA_MODULES = (
    "app.schemas.position",
    "app.schemas.organization",
    "app.schemas.competency",
    "app.schemas.runtime",
    "app.schemas.lifecycle",
    "app.schemas.knowledge",
    "app.schemas.misc",
)


def _falsey_defaults() -> list[str]:
    """扫全部 Out schema：返回 `module.Class.field` 的违规清单（0/False/""/[]/{} 默认值）。"""
    offenders: list[str] = []
    seen_classes: set[type[BaseModel]] = set()
    for module_name in _SCHEMA_MODULES:
        module = importlib.import_module(module_name)
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if obj in seen_classes or not issubclass(obj, BaseModel) or obj is BaseModel:
                continue
            seen_classes.add(obj)
            model_fields = getattr(obj, "model_fields", {})
            for field_name, field in model_fields.items():
                if field_name not in DERIVED_OUT_FIELDS:
                    continue
                default = field.default
                if default is None:
                    continue  # 默认 null = 缺失可见，允许
                # Field(default_factory=list/dict) 也等价于 []/{} 假默认
                if field.default_factory is not None:
                    try:
                        factory_value = field.default_factory()
                    except Exception:  # noqa: BLE001 - factory 结果才是要看的
                        factory_value = None
                else:
                    factory_value = None
                if _is_falsey_placeholder(default) or _is_falsey_placeholder(factory_value):
                    offenders.append(
                        f"{module_name}.{obj.__name__}.{field_name}（default={default!r}）"
                    )
    return offenders


def _is_falsey_placeholder(value: Any) -> bool:
    if isinstance(value, bool):  # False 是假的 0，但单独区分语义
        return value is False
    if isinstance(value, (int, float)) and value == 0:
        return True
    if isinstance(value, str) and value == "":
        return True
    if isinstance(value, (list, dict)) and len(value) == 0:
        return True
    return False


def test_no_derived_out_field_carries_a_falsey_default():
    offenders = _falsey_defaults()
    assert not offenders, (
        "以下派生字段在 Out schema 上带了假默认值（ADR-12：not computed != 0/[]/false）。"
        "要么改成必填（由 serializer 显式填入），要么默认 None（缺失以 null 呈现）：\n"
        + "\n".join(f"  - {offender}" for offender in offenders)
    )


def test_serializer_must_be_the_only_exit_for_definition_derived_fields(db, default_company_id):
    """行为防线样例：definitions_out 的派生值来自计算，不是 schema 兜底。

    直接验证响应模型：把派生键从 payload 里删掉，schema 必须**拒绝**（漏算要报错，
    不能静默变成 0）—— 这正好说明默认值被删是有意为之。
    """
    from app.schemas.position import PositionDefinitionOut
    from app.services import position_service

    definitions = position_service.definitions_out(db, default_company_id)
    if not definitions:
        pytest.skip("测试库没有职位定义 —— 结构性断言改在单一 schema 上做")
    sample = dict(definitions[0])
    assert "slot_count" in sample and "vacant_count" in sample and "package_slugs" in sample
    PositionDefinitionOut.model_validate(sample)  # 完整 payload 能过
    for key in ("slot_count", "vacant_count", "package_slugs"):
        broken = {k: v for k, v in sample.items() if k != key}
        with pytest.raises(Exception, match=key):
            PositionDefinitionOut.model_validate(broken)


def test_derived_field_list_has_no_typo_vs_schema_fields():
    """清单里的每个名字都确实存在于某个 schema 字段 —— 防清单烂掉/笔误。

    `workforce_status` / `current_position` 这类由 enrich 挂上的名字在 RosterEntryOut；
    `score` 等未来字段此刻可以不在任何 schema 里 —— 但它们一旦出现就必须守规矩，
    所以这里只要求**名字本身不与任何 Out 字段名拼错**（宽松版：无字段则跳过）。
    """
    module = importlib.import_module("app.schemas.position")
    present: set[str] = set()
    for _name, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, BaseModel):
            present.update(getattr(obj, "model_fields", {}).keys())
    # 这些名字当前必须存在（P4 已落地）
    for name in ("slot_count", "vacant_count", "occupied_slot", "package_slugs", "integrity"):
        assert name in present, f"position Out schema 竟然没有派生字段 {name} —— schema 变了？"
