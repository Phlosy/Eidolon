"""`scripts/dev_cleanup_plan.py` 的守卫（规划器只做规划，不做删除）。

和 `test_dev_inventory.py` 同构的五组断言，但立场更进一步：**这个工具的整个存在理由**
就是"清理必须显式、可审、只读"，所以：

  A 显式目标：不给 `--company` 必须报错（拒绝默认全库）；传不存在的 id 必须报错列出
  B 免责声明：`NO DATA HAS BEEN MODIFIED.` 必须出现在文本与 JSON 输出尾部
  C 复用：规划里员工/编制数字与 `position_service` / dev_inventory 口径一致
  D 没有删除面：`--help` 不允许出现任何删除/修复类 flag；探针只是提示
  E 物理只读：SQLite 连接拒写（复用 dev_inventory 的 engine 构建）
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

SERVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parents[1]
SCRIPT = REPO_ROOT / "scripts" / "dev_cleanup_plan.py"


def _module():
    spec = importlib.util.spec_from_file_location("dev_cleanup_plan_for_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plan = _module()


@pytest.fixture(scope="module")
def default_id() -> int:
    """模块级拿默认公司 id（不能复用函数级 conftest fixture）。"""
    import sqlalchemy as sa

    from app.core.database import engine

    with sessionmaker(bind=engine)() as db:
        value = db.execute(sa.text("SELECT id FROM companies ORDER BY id LIMIT 1")).scalar()
    assert value is not None, "测试库里没有公司"
    return int(value)


@pytest.fixture(scope="module")
def report(default_id: int) -> dict:
    """对**测试库**的默认公司跑一次规划。conftest 已把库指到临时文件。"""
    from app.core.database import engine

    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_factory() as db:
        result = plan.collect(db, [default_id])
        assert not db.new and not db.dirty
        db.rollback()
    return result


# ---------------------------------------------------------------------- A 显式目标


def test_company_ids_are_required():
    help_text = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert "required" in help_text or "--company" in help_text
    # 不给 --company 必须非零退出，且报错要说清楚为什么（拒绝默认全库的语义要可见）
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "usage" in result.stderr.lower()


def test_unknown_company_id_is_refused(default_company_id: int):
    import sqlalchemy as sa

    from app.core.database import engine

    with sessionmaker(bind=engine)() as db:
        known = db.execute(sa.text("SELECT id FROM companies")).scalars().all()
    bogus = max(known) + 9999 if known else 9999
    with sessionmaker(bind=engine)() as db:
        with pytest.raises(ValueError, match="不存在"):
            plan.collect(db, [bogus])


# ------------------------------------------------------------------- B 免责声明


def test_no_data_modified_sentence_ends_both_outputs(report: dict):
    assert report["no_data_modified"] == plan.NO_DATA_MODIFIED
    text = plan.render_text(report)
    assert text.rstrip().endswith(plan.NO_DATA_MODIFIED), (
        "文本输出必须以 NO DATA HAS BEEN MODIFIED 收尾"
    )
    assert report["write_policy"].startswith("none")
    assert json.loads(json.dumps(report))["no_data_modified"] == plan.NO_DATA_MODIFIED


# ------------------------------------------------------------------------ C 复用


def test_plan_inventory_matches_roster_source(report: dict, default_company_id: int):
    from app.services import position_service

    section = report["companies"][0]
    assert section["company_id"] == default_company_id
    from app.core.database import engine

    with sessionmaker(bind=engine)() as db:
        entries = position_service.roster(db, default_company_id)
    assert section["inventory"]["employees"]["total"] == len(entries)


def test_required_categories_are_present(report: dict):
    """用户验收要求的输出分类一个都不能少。"""
    section = report["companies"][0]
    inventory = section["inventory"]
    for key in (
        "employees",
        "employments",
        "position_slots",
        "projects",
        "documents",
        "runtime_instances",
        "git_connections",
        "tutorial_progress",
        "external_resources",
    ):
        assert key in inventory, f"inventory 缺 {key}"
    dep = section["dependency_impact"]
    assert {"direct", "employee_owned", "project_owned"} <= set(dep)
    # 依赖影响里必须真的数到员工级子表（有 demo workforce 的情况下 employments > 0）
    employee_owned = dep["employee_owned"]
    assert "employments" in employee_owned
    assert "runtime_instances" in employee_owned
    assert "skills" in employee_owned
    external = inventory["external_resources"]
    assert "workspace_dirs_on_disk" in external
    assert "employee_data_dirs_on_disk" in external


# ------------------------------------------------------------------- D 没有删除面


def test_help_has_no_delete_or_cleanup_flags():
    help_text = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    forbidden = (
        "--delete",
        "--cleanup",
        "--fix",
        "--apply",
        "--prune",
        "--repair",
        "--force",
        "--auto-remove-probes",
    )
    for flag in forbidden:
        assert flag not in help_text, f"规划器不该出现 {flag}"
    assert "--company" in help_text


def test_probe_is_a_hint_and_never_a_target(report: dict):
    """suspected_probe 只在 probe_hints 里出现；即使为 True 也不影响任何目标决策。"""
    blob = json.dumps(report, ensure_ascii=False)
    for key in ('"should_delete"', '"verdict"', '"is_test"'):
        assert key not in blob, f"报告里不能有判决字段 {key}"
    for section in report["companies"]:
        hints = section["probe_hints"]
        assert "informational only" in hints["note"]
        assert "never auto-target" in hints["note"]


def test_json_has_no_mutation_action_fields(report: dict):
    blob = json.dumps(report, ensure_ascii=False)
    for key in ('"action": "delete"', '"deleted": true', '"removed": true'):
        assert key not in blob, f"只读规划输出不该携带删除动作：{key}"


# ------------------------------------------------------------------- E 物理只读


def test_sqlite_connection_is_actually_read_only(report: dict):
    from sqlalchemy.exc import OperationalError, ProgrammingError

    from app.core.config import settings

    url = plan._resolve_database_url(settings.database_url)
    if not url.startswith("sqlite"):  # pragma: no cover
        pytest.skip("mode=ro 只适用于 SQLite")
    engine = plan.build_engine(url)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("select count(*) from companies").scalar() is not None
        with pytest.raises((OperationalError, ProgrammingError)):
            conn.exec_driver_sql(
                "insert into companies (name, slug, settings) values ('x','x','{}')"
            )
