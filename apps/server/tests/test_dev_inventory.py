"""`scripts/dev_inventory.py` 的守卫（工具本身不在 apps/server 里，但纪律必须跟着测试跑）。

为什么值得给它写测试：这个工具存在的理由就是"别再拿跨公司的数字当业务事实"，
而这类纪律只要没有钉子，下一次改脚本的人就会顺手去掉。用户也明确要求
"全局合计只是诊断用"这条可以写进测试。

五组断言：
  A 公司口径：全局合计必须等于逐公司之和（不一致说明有人塞了全库查询）
  B 免责声明：JSON 与文本两份输出里都必须出现
  C 复用：integrity / workforce 的数字必须与 `position_service` 完全一致
  D 没有删除面：CLI 不接受 delete/cleanup/fix/apply，报告里不许有 verdict/is_test
  E 物理只读：SQLite 连接必须拒写
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import sessionmaker

SERVER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVER_ROOT.parents[1]
SCRIPT = REPO_ROOT / "scripts" / "dev_inventory.py"


def _module():
    spec = importlib.util.spec_from_file_location("dev_inventory_for_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


inv = _module()


@pytest.fixture(scope="module")
def report() -> dict:
    """对**测试库**跑一次清点。conftest 已经把 EIDOLON_DATABASE_URL 指到临时库。"""
    from app.core.database import engine

    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_factory() as db:
        result = inv.collect(db, None, 30)
        assert not db.new and not db.dirty
        db.rollback()
    return result


# ------------------------------------------------------------------- A 公司口径


def test_global_totals_are_the_sum_of_company_sections(report: dict):
    assert report["companies"], "测试库里至少有默认公司，空报告等于什么都没测"
    assert report["scope_rule"]
    assert report["global_totals"]["employee_count"] == sum(
        section["talent_roster"]["total"] for section in report["companies"]
    )
    assert report["global_totals"]["position_slot_count"] == sum(
        section["position_slots"]["total"] for section in report["companies"]
    )
    assert report["global_totals"]["occupied_slot_count"] == sum(
        section["position_slots"]["occupied"] for section in report["companies"]
    )


def test_every_figure_lives_under_a_company(report: dict):
    """不允许出现"公司段之外的人数字"。

    全局段是刻意存在的（它对应上一轮那个跨公司混算的诱惑），但它必须带免责声明，
    而且只能由逐公司结果相加得到 —— 不接受另写一条全库 SQL。
    """
    for section in report["companies"]:
        assert section["scope"] == "company"
        assert isinstance(section["company_id"], int)
        for key in ("talent_roster", "assignment_integrity", "position_slots", "resources"):
            assert key in section, f"{key} 缺失：公司 {section['company_id']}"


# ------------------------------------------------------------------- B 免责声明


def test_disclaimer_is_present_in_both_outputs(report: dict):
    assert report["global_note"] == inv.GLOBAL_NOTE
    assert "diagnostic only" in report["global_note"]
    text = inv.render_text(report)
    assert report["global_note"] in text, "人看的输出丢了口径声明"
    assert json.loads(json.dumps(report))["global_note"] == inv.GLOBAL_NOTE


# ---------------------------------------------------------------------- C 复用


def test_inventory_numbers_come_from_the_same_code_the_roster_uses(report: dict):
    from app.core.database import engine
    from app.services import position_service

    with sessionmaker(bind=engine)() as db:
        for section in report["companies"]:
            company_id = section["company_id"]
            entries = position_service.roster(db, company_id)
            by_status: dict[str, int] = {}
            for entry in entries:
                by_status[entry["workforce_status"]] = (
                    by_status.get(entry["workforce_status"], 0) + 1
                )
            assert section["talent_roster"]["by_status"] == by_status, company_id
            # 同一份诊断结果，不是脚本里另写的 JOIN
            integrity = position_service.integrity(db, company_id)
            assert section["assignment_integrity"]["counts"] == dict(integrity["counts"])
            assert section["assignment_integrity"]["source"].startswith(
                "app.services.position_service"
            )
            assert section["assignment_integrity"]["read_only"] is True


# ----------------------------------------------------------------- D 没有删除面


def test_cli_exposes_no_delete_fix_or_cleanup():
    help_text = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    forbidden = ("--delete", "--cleanup", "--fix", "--apply", "--prune", "--repair", "--force")
    assert help_text, "--help 没有输出"
    for flag in forbidden:
        assert flag not in help_text, f"只读工具不该出现 {flag}"
    assert "--format" in help_text and "--company" in help_text


def test_report_carries_no_verdict_fields(report: dict):
    """有理由，没判决。

    `is_test` / `verdict` / `should_delete` 这类字段一旦出现，下一步就一定有人写
    `if row["is_test"]: delete(row)` —— 那正是被明确推掉的 cleanup 语义。
    """
    blob = json.dumps(report, ensure_ascii=False)
    for key in ('"is_test"', '"verdict"', '"should_delete"', '"deleted"'):
        assert key not in blob, f"报告里出现了判决字段 {key}"
    for section in report["companies"]:
        suspicion = section["suspected_probe"]
        assert set(suspicion) >= {"suspected_probe", "reasons", "counter_evidence", "rule", "note"}
        if suspicion["suspected_probe"]:
            assert len(suspicion["reasons"]) >= 2, "单条弱证据不构成嫌疑（真实公司会被误伤）"
        assert "清理" in suspicion["note"] or "判定" in suspicion["note"]


def test_medium_confidence_always_explains_itself(report: dict):
    """标了嫌疑就必须说清为什么；空着理由的标记等于自动化判决。"""
    for section in report["companies"]:
        suspicion = section["suspected_probe"]
        if suspicion["confidence"] != "none":
            assert suspicion["reasons"], f"公司 {section['company_id']} 有置信度却没理由"


# ------------------------------------------------------------------- E 物理只读


def test_sqlite_connection_is_actually_read_only(report: dict):
    """只读不是口头承诺：驱动层必须拒写。"""
    from app.core.config import settings

    url = inv._resolve_database_url(settings.database_url)
    if not url.startswith("sqlite"):  # pragma: no cover - postgres 环境走另一条路
        pytest.skip("mode=ro 只适用于 SQLite")
    engine = inv.build_engine(url)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("select count(*) from companies").scalar() is not None
        with pytest.raises((OperationalError, ProgrammingError)):
            conn.exec_driver_sql(
                "insert into companies (name, slug, settings) values ('x','x','{}')"
            )


def test_relative_sqlite_url_is_anchored_to_the_server_root():
    """从仓库根跑也不能指错库（第一版就是靠 cwd 猜，导致 workspace 检查全部误报）。"""
    resolved = inv._resolve_database_url("sqlite:///./data/eidolon.db")
    assert resolved.endswith(str(SERVER_ROOT / "data" / "eidolon.db"))
    absolute = inv._resolve_database_url("sqlite:////tmp/other.db")
    assert absolute == "sqlite:////tmp/other.db"


def test_workspace_root_check_matches_how_dirs_are_actually_named(tmp_path, monkeypatch):
    """工作区按 slug 命名、员工数据目录按 id 命名 —— 两把键混用就会满屏假孤儿。

    第一版正是拿 slug 集合去比 `data/employees/{id}`，把 24 个正常目录全报成孤儿。
    所以这里刻意**反着放**：工作区根下放一个数字目录、数据根下放一个 slug 目录，
    两边都必须被认成多余；认不出来就说明键又用错了。
    """
    from app.core.config import settings
    from app.core.database import engine

    workspaces = tmp_path / "data" / "workspaces"
    employee_data = tmp_path / "data" / "employees"
    (workspaces / "4242").mkdir(parents=True)  # 数字出现在 slug 键的根下 ⇒ 多余
    (employee_data / "someone-slug").mkdir(parents=True)  # slug 出现在 id 键的根下 ⇒ 多余
    monkeypatch.setattr(settings, "workspace_root", str(workspaces), raising=False)
    monkeypatch.setattr(settings, "data_root", str(tmp_path / "data"), raising=False)

    with sessionmaker(bind=engine)() as db:
        result = inv._workspace_orphans(db)
    assert result["workspace_dir_without_employee"]["keyed_by"] == "slug"
    assert result["employee_data_dir_without_employee"]["keyed_by"] == "employee_id"
    stray_workspaces = {item["dir"] for item in result["workspace_dir_without_employee"]["samples"]}
    stray_data = {item["dir"] for item in result["employee_data_dir_without_employee"]["samples"]}
    assert "4242" in stray_workspaces, result["workspace_dir_without_employee"]
    assert "someone-slug" in stray_data, result["employee_data_dir_without_employee"]
