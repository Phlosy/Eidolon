"""P5 架构守卫：能力域不许“随机 / 人格耦合 / 第二写入口”。

docs/competency-system.md §7.3 / workforce-domain-refactor.md §8.3 的机器证明：

1. 能力写路径（app/services/competency.py 与 app/competency/）**不得 import random**；
2. 能力写路径**不得读人格**（BrainTraits / traits JSON / curiosity / behavior）；
3. 能力写路径**不得引用 position_fit**（Fit 只用于推荐，不碰评估）；
4. `EmployeeCompetency(...)` 构造（= 写行）只允许出现在聚合服务里 —— 防止
   “某 API 顺手 PATCH score”式的第二写入口。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parents[1]

#: 能力域源文件（写侧）。扫描到违规就在这些文件上红。
_COMPETENCY_WRITE_FILES = (
    "app/services/competency.py",
    "app/competency/catalog.py",
)

#: 允许构造 EmployeeCompetency 行的文件（单一写入口纪律）。
_EMPLOYEE_COMPETENCY_WRITER_FILES = {"app/services/competency.py"}

_FORBIDDEN_IMPORTS = {"random"}
_FORBIDDEN_NAMES = {"curiosity", "traits", "BrainTraits", "position_fit"}


def _python_files(prefixes: tuple[str, ...]) -> list[tuple[Path, str]]:
    root = SERVER_ROOT
    files = []
    for prefix in prefixes:
        path = root / prefix
        if path.is_dir():
            for child in sorted(path.rglob("*.py")):
                if "__pycache__" not in str(child):
                    files.append((child, str(child.relative_to(root))))
        else:
            files.append((path, prefix))
    return files


def _read_relative(relative: str) -> str:
    return (SERVER_ROOT / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize("relative", _COMPETENCY_WRITE_FILES)
def test_competency_write_paths_never_import_random(relative: str):
    source = _read_relative(relative)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name for alias in node.names}
            assert not names & _FORBIDDEN_IMPORTS, f"{relative} import 了 random"
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in _FORBIDDEN_IMPORTS:
                raise AssertionError(f"{relative} from-import 了 random")


@pytest.mark.parametrize("relative", _COMPETENCY_WRITE_FILES)
def test_competency_write_paths_never_read_personality(relative: str):
    """人格不改能力：写路径不得引用 traits / curiosity / BrainTraits / behavior。"""
    source = _read_relative(relative)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES - {"position_fit"}:
            raise AssertionError(f"{relative}:{node.lineno} 引用了人格参数 {node.id}")
        if isinstance(node, ast.Attribute) and node.attr in {"traits", "curiosity"}:
            raise AssertionError(f"{relative}:{node.lineno} 直接读 traits/curiosity")
        if isinstance(node, ast.ImportFrom) and node.module and "brain" in node.module:
            raise AssertionError(f"{relative}:{node.lineno} import 了 brain（人格域）")


@pytest.mark.parametrize("relative", _COMPETENCY_WRITE_FILES)
def test_competency_write_paths_never_use_position_fit(relative: str):
    """Fit 是推荐侧的，不参与能力评估（ADR-9）。"""
    source = _read_relative(relative)
    assert "position_fit" not in source, f"{relative} 里出现 position_fit"


def test_employee_competency_rows_are_written_by_the_aggregator_only():
    """全仓扫描：`EmployeeCompetency(` 构造只允许出现在聚合服务里。"""
    offenders = []
    for path, relative in _python_files(("app",)):
        if relative in _EMPLOYEE_COMPETENCY_WRITER_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                else:
                    continue
                if name == "EmployeeCompetency":
                    offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, (
        "employee_competencies 行只能由聚合服务创建（能力只能被证明）—— 第二写入口：\n"
        + "\n".join(f"  - {offender}" for offender in offenders)
    )
