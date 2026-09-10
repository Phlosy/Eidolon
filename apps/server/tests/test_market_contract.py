"""T2.0 领域契约守卫（docs/t2-talent-market-design.md / docs/t2-implementation-plan.md）。

把 T2 的边界从"口头约定"变成可执行规则：

1. 状态轴与词表冻结：`CultivationState` / `TalentOrigin` / `MarketState` /
   `MarketListingStatus` / `MarketParticipantKind` 的值集不许静默改名
   （它们直接对应库中字符串与 API）；
2. `character_profiles.lifecycle` **只允许培养态**：`listed`/`hired` 已废弃 —— 市场态走
   `market_listings`、任职态走 `employments`（设计 §4，I11）；
3. `MarketAdapter` 表面冻结（设计 §8 / D9）：只做市场资源，**没有 recruit**；
4. 市场模块**不得出现 M1 经济概念**（D10）—— 检查标识符与字符串字面量（注释与 docstring 除外）；
5. 市场模块不得 import 请求公司上下文（`app/api/scope.py`）—— 市场是独立的跨公司读取域（设计 §8）。
"""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
MARKET_DIR = SERVER_ROOT / "app" / "talent" / "market"

#: M1（货币 / 合同 / 结算是 M1 的事，T2 禁止出现 —— 依赖只能 T2 → M1，不能倒挂）。
_FORBIDDEN_ECONOMIC_TOKENS = {
    "wallet",
    "ledger",
    "balance",
    "currency",
    "price",
    "payment",
    "settlement",
    "escrow",
    "bid",
    "ask",
    "invoice",
    "refund",
    "deposit",
    "checkout",
}


def _python_files(directory: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for path in sorted(directory.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        files.append((path, str(path.relative_to(SERVER_ROOT))))
    return files


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """收集 docstring 常量节点 id（守卫只扫真实代码，不扫说明文字）。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    ids.add(id(body[0].value))
    return ids


def _code_tokens(tree: ast.AST) -> set[str]:
    """标识符（按 snake_case 拆词）与字符串字面量的词元。"""
    docstrings = _docstring_nodes(tree)
    tokens: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            tokens.update(part for part in node.id.lower().split("_") if part)
        elif isinstance(node, ast.Attribute):
            tokens.update(part for part in node.attr.lower().split("_") if part)
        elif isinstance(node, ast.arg):
            tokens.update(part for part in node.arg.lower().split("_") if part)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            tokens.update(part for part in node.name.lower().split("_") if part)
        elif isinstance(node, ast.keyword) and node.arg:
            tokens.update(part for part in node.arg.lower().split("_") if part)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            tokens.update(re.findall(r"[a-zA-Z_]+", node.value.lower()))
    return tokens


# ---- 1. 词表与状态轴冻结 ----


def test_state_axis_vocabulary_is_frozen():
    from app.models.enums import CultivationState, TalentOrigin
    from app.talent.market.contracts import (
        MarketListingStatus,
        MarketParticipantKind,
        MarketState,
    )

    assert {state.value for state in CultivationState} == {"cultivating", "ready"}
    assert {origin.value for origin in TalentOrigin} == {"issued", "trained", "blank"}
    assert {state.value for state in MarketState} == {"unavailable", "unlisted", "listed"}
    assert {status.value for status in MarketListingStatus} == {"active", "closed"}
    assert {kind.value for kind in MarketParticipantKind} == {
        "player_company",
        "npc_company",
        "system_issuer",
    }


def test_character_lifecycle_column_is_cultivation_state_only():
    """I11：`character_profiles.lifecycle` 只允许 cultivating/ready。

    两件事：模型默认值必须来自枚举；全仓不得出现把 listed/hired 写进 lifecycle 的赋值。
    """
    from app.models.cultivation import CharacterProfile
    from app.models.enums import CultivationState

    assert CharacterProfile.__table__.c.lifecycle.default.arg == CultivationState.cultivating.value

    offenders: list[str] = []
    pattern = re.compile(r"""lifecycle\s*=\s*["'](listed|hired)["']""")
    for path, relative in _python_files(SERVER_ROOT / "app"):
        source = path.read_text(encoding="utf-8")
        if pattern.search(source):
            offenders.append(relative)
    assert not offenders, (
        "character_profiles.lifecycle 只表达培养态（T2 设计 §4）：市场态走 market_listings、"
        "任职态走 employments。违规写入：\n" + "\n".join(f"  - {item}" for item in offenders)
    )


# ---- 2. MarketAdapter 契约 ----


def test_market_adapter_surface_is_frozen_without_recruit():
    """适配器只做市场资源；招募是 RecruitmentService 的领域事务（D9）。"""
    from app.talent.market.adapter import MarketAdapter

    methods = {
        name
        for name, value in vars(MarketAdapter).items()
        if callable(value) and not name.startswith("_")
    }
    assert methods == {
        "list_candidate",
        "delist_candidate",
        "get_listing",
        "get_listing_for_person",
        "search_listings",
    }
    assert "recruit" not in methods
    for name in methods:
        assert callable(getattr(MarketAdapter, name))


def test_market_adapter_methods_are_synchronous():
    import inspect

    from app.talent.market.adapter import MarketAdapter

    for name in ("list_candidate", "delist_candidate", "get_listing", "search_listings"):
        assert not inspect.iscoroutinefunction(getattr(MarketAdapter, name)), name


# ---- 3. 只读投影字段冻结（公开投影的一部分，设计 §6.1/§8.3） ----


def test_listing_view_fields_are_frozen():
    from app.talent.market.contracts import MarketListingView, MarketSearchQuery

    listing_fields = tuple(field.name for field in dataclasses.fields(MarketListingView))
    assert listing_fields == (
        "listing_id",
        "person_id",
        "identity_id",
        "name",
        "origin",
        "cultivation_state",
        "status",
        "quality_tier",
        "listed_by_participant_id",
        "listed_at",
        "closed_at",
    )
    assert MarketListingView.__dataclass_params__.frozen  # type: ignore[attr-defined]
    # 索引级投影：不得携带人员档案字段（档案由 Person Read Model 提供，避免两套聚合）
    assert not {"traits", "competencies", "evidence", "knowledge"} & set(listing_fields)

    query_fields = tuple(field.name for field in dataclasses.fields(MarketSearchQuery))
    assert query_fields == (
        "text",
        "origin",
        "quality_tier",
        "position_definition_id",
        "limit",
        "offset",
    )


# ---- 4. M1 经济概念守卫 ----


def test_market_module_has_no_economic_vocabulary():
    offenders: list[str] = []
    for path, relative in _python_files(MARKET_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hit = _code_tokens(tree) & _FORBIDDEN_ECONOMIC_TOKENS
        if hit:
            offenders.append(f"{relative}: {sorted(hit)}")
    assert not offenders, (
        "T2 不引入任何真实经济依赖（设计 D10）：货币/账本/价格/结算属 M1。违规：\n"
        + "\n".join(f"  - {item}" for item in offenders)
    )


def test_market_module_does_not_use_request_company_scope():
    """市场是受控的跨公司读取域，不得借用请求公司上下文（设计 §8）。"""
    offenders: list[str] = []
    for path, relative in _python_files(MARKET_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.api"):
                offenders.append(f"{relative}:{node.lineno}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app.api"):
                        offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, "市场模块不得 import app.api.*（公司作用域不是市场边界）：\n" + "\n".join(
        f"  - {item}" for item in offenders
    )
