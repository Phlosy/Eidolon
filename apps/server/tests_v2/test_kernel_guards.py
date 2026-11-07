"""K7/K8 plus the machine check that every K invariant has a living test anchor."""

import ast
import importlib
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory

from eidolon.db import UnsupportedDatabase, build_engine, require_postgres
from eidolon.kernel.contracts import (
    KERNEL_INVARIANTS,
    MONEY_INVARIANTS,
    TALENT_INVARIANTS,
    WORK_INVARIANTS,
)

KERNEL_DIR = Path(__file__).resolve().parents[1] / "eidolon" / "kernel"
UPPER_LAYERS = {
    "eidolon.identity",
    "eidolon.institution",
    "eidolon.work",
    "eidolon.agent",
    "eidolon.money",
    "eidolon.talent",
    "eidolon.api",
    "eidolon.extensions",
    "app",  # the legacy application is not a dependency of v2
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_kernel_has_no_upward_imports():
    offenders = {}
    for path in KERNEL_DIR.glob("*.py"):
        bad = {
            name
            for name in _imports(path)
            if any(name == layer or name.startswith(layer + ".") for layer in UPPER_LAYERS)
        }
        if bad:
            offenders[path.name] = sorted(bad)
    assert offenders == {}, f"kernel imports upward: {offenders}"


LAYERS = [
    "kernel",
    "identity",
    "institution",
    "work",
    "agent",
    "money",
    "talent",
    "extensions",
    "api",
]
EIDOLON_DIR = KERNEL_DIR.parent


def test_layers_only_import_downward():
    """Every ``eidolon.<layer>`` module imports only layers at or below its own
    (v2-architecture §2.1). Root-level process modules (cli, worker, ...) are exempt."""
    offenders = {}
    for path in EIDOLON_DIR.rglob("*.py"):
        rel = path.relative_to(EIDOLON_DIR).parts
        if len(rel) < 2 or rel[0] not in LAYERS or "migrations" in rel:
            continue
        own = LAYERS.index(rel[0])
        bad = set()
        for name in _imports(path):
            if name == "app" or name.startswith("app."):
                bad.add(name)
            parts = name.split(".")
            if len(parts) >= 2 and parts[0] == "eidolon" and parts[1] in LAYERS:
                if LAYERS.index(parts[1]) > own:
                    bad.add(name)
        if bad:
            offenders[str(path.relative_to(EIDOLON_DIR))] = sorted(bad)
    assert offenders == {}, f"upward imports: {offenders}"


def test_non_postgres_url_is_refused():
    for url in ("sqlite:///x.db", "mysql+pymysql://u:p@h/db"):
        with pytest.raises(UnsupportedDatabase):
            require_postgres(url)
        with pytest.raises(UnsupportedDatabase):
            build_engine(url)
    require_postgres("postgresql+psycopg://u:p@h/db")


def test_every_invariant_has_an_existing_anchor():
    missing = []
    for invariant in (*KERNEL_INVARIANTS, *WORK_INVARIANTS, *MONEY_INVARIANTS, *TALENT_INVARIANTS):
        module_name, test_name = invariant.anchor.split("::")
        module = importlib.import_module(module_name)
        if not callable(getattr(module, test_name, None)):
            missing.append(invariant.code)
    assert missing == [], f"invariants without a test: {missing}"
    codes = [
        i.code
        for i in (*KERNEL_INVARIANTS, *WORK_INVARIANTS, *MONEY_INVARIANTS, *TALENT_INVARIANTS)
    ]
    assert len(set(codes)) == len(codes)


def test_migrations_match_models(database):
    """`alembic check` against the migrated test database: models and migrations agree."""
    from conftest import alembic_config

    cfg = alembic_config()
    command.check(cfg)  # raises on drift
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1


def test_migration_round_trip(database):
    from conftest import alembic_config

    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    command.check(cfg)
