"""Regression guards for scripts/dev_clear_data.sh —— make dev-clear-data 的安全门禁。

破坏性脚本不真删开发者的数据：删除路径只在 tmp 目录上验证；对外部库的防误删、
确认门禁与 dry-run 语义单独锁死（与 test_devctl.py 同风格：破坏性路径只允许
显式确认 + dry-run）。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "dev_clear_data.sh"


def run(
    *args: str, data_target: Path, env_extra: dict | None = None
) -> subprocess.CompletedProcess:
    (data_target / "workspaces").mkdir(parents=True, exist_ok=True)
    (data_target / "eidolon.db").write_text("fake db", encoding="utf-8")
    env = {**os.environ, "DATA_TARGET": str(data_target), "DEV_CLEAR_DATA_ALLOW_OUTSIDE": "1"}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(  # noqa: S603
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )


def test_dry_run_lists_everything_and_never_modifies(tmp_path):
    target = tmp_path / "data"
    result = run("--dry-run", data_target=target)
    assert result.returncode == 0
    assert "NO DATA HAS BEEN MODIFIED." in result.stdout
    assert "workspaces" in result.stdout and "eidolon.db" in result.stdout
    assert (target / "eidolon.db").exists(), "dry-run 绝不能删除"


def test_without_confirm_refuses_when_not_interactive(tmp_path):
    target = tmp_path / "data"
    file = target / "eidolon.db"
    result = run(data_target=target)
    assert result.returncode != 0
    assert "DATA_CONFIRM" in result.stderr
    assert file.exists()


def test_confirm_clears_contents_but_keeps_the_directory(tmp_path):
    target = tmp_path / "data"
    file = target / "eidolon.db"
    result = run("--confirm=yes", data_target=target)
    assert result.returncode == 0, result.stderr
    assert "已清除本地开发数据" in result.stdout
    assert not file.exists()
    assert target.exists()


def test_default_target_refuses_external_database_url(tmp_path):
    """默认目标（apps/server/data）+ .env 指向仓库外 ⇒ 拒绝，绝不碰外部库。

    把脚本拷进一个假仓库（tmp repo/scripts + repo/.env + repo/apps/server/data），
    脚本以自身位置推导 ROOT —— 这样能安全地真跑默认目标分支。
    """
    fake_repo = tmp_path / "repo"
    (fake_repo / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, fake_repo / "scripts" / "dev_clear_data.sh")
    (fake_repo / "apps" / "server").mkdir(parents=True)
    data_dir = fake_repo / "apps" / "server" / "data"
    data_dir.mkdir()
    (data_dir / "eidolon.db").write_text("old", encoding="utf-8")
    (fake_repo / ".env").write_text(
        "EIDOLON_DATABASE_URL=postgresql://user@prod.example/db\n", encoding="utf-8"
    )
    result = subprocess.run(  # noqa: S603
        ["bash", str(fake_repo / "scripts" / "dev_clear_data.sh"), "--confirm=yes"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0
    assert "拒绝清除" in result.stderr
    assert (data_dir / "eidolon.db").exists(), "外部库场景绝不能动数据"


def test_default_target_clears_sqlite_under_repo(tmp_path):
    """默认目标 + 仓库内 sqlite（sqlite:///./data/eidolon.db）⇒ 允许清除。"""
    fake_repo = tmp_path / "repo2"
    (fake_repo / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, fake_repo / "scripts" / "dev_clear_data.sh")
    (fake_repo / "apps" / "server").mkdir(parents=True)
    data_dir = fake_repo / "apps" / "server" / "data"
    data_dir.mkdir()
    (data_dir / "eidolon.db").write_text("old", encoding="utf-8")
    result = subprocess.run(  # noqa: S603
        ["bash", str(fake_repo / "scripts" / "dev_clear_data.sh"), "--confirm=yes"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not (data_dir / "eidolon.db").exists()
