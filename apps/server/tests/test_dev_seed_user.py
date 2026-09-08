"""Regression guards for scripts/dev_seed_user.py —— make dev-seed-user。

流程语义锁死：新建账号 argon2 哈希（与注册同源）；幂等重跑不重复；清库后
user/user 恒可登录（重置路径 force verified/active）；自建 test-co 公司 + OWNER；
只允许 SQLite（外部库拒绝）。破坏性面为 0 —— 只写 tmp sqlite。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "dev_seed_user.py"


def run_seed(
    tmp_path: Path,
    *args: str,
    db_url: str | None = None,
) -> subprocess.CompletedProcess:
    url = db_url or f"sqlite:///{tmp_path}/dev.db"
    env = {**os.environ, "EIDOLON_DATABASE_URL": url}
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )


def _rows(engine) -> dict:
    from sqlalchemy import text

    with engine.connect() as conn:
        users = conn.execute(
            text("SELECT email, password_hash, email_verified, status FROM users")
        ).all()
        memberships = conn.execute(
            text(
                "SELECT cm.user_id, cm.role, c.slug FROM company_memberships cm "
                "JOIN companies c ON c.id = cm.company_id"
            )
        ).all()
        companies = conn.execute(text("SELECT slug FROM companies")).all()
        departments = conn.execute(text("SELECT slug FROM departments")).all()
    return {
        "users": users,
        "memberships": memberships,
        "companies": [c[0] for c in companies],
        "departments": [d[0] for d in departments],
    }


@pytest.fixture()
def seeded_db(tmp_path):
    from sqlalchemy import create_engine

    from app.models import auth, organization  # noqa: F401  注册 metadata
    from app.models.base import Base

    engine = create_engine(f"sqlite:///{tmp_path}/dev.db")
    Base.metadata.create_all(engine)
    return engine, tmp_path


def test_seed_creates_user_with_argon2_hash_and_owner_membership(seeded_db):
    engine, tmp_path = seeded_db
    result = run_seed(tmp_path)
    assert result.returncode == 0, result.stderr

    from argon2 import PasswordHasher

    rows = _rows(engine)
    assert len(rows["users"]) == 1
    email, password_hash, verified, status = rows["users"][0]
    assert email == "user@example.com"
    assert status == "active" and verified == 1
    assert PasswordHasher().verify(password_hash, "user"), "哈希必须能与密码 user 校验"
    assert rows["companies"] == ["test-co"]
    assert rows["memberships"] == [(1, "OWNER", "test-co")]
    assert set(rows["departments"]) == {"executive", "product", "research", "engineering", "qa"}


def test_seed_is_idempotent_and_resets_password(seeded_db):
    engine, tmp_path = seeded_db
    assert run_seed(tmp_path).returncode == 0
    assert run_seed(tmp_path, "--password", "newpass").returncode == 0

    from argon2 import PasswordHasher

    rows = _rows(engine)
    assert len(rows["users"]) == 1, "重跑不得重复建账号"
    assert len(rows["companies"]) == 1, "重跑不得重复建公司"
    assert len(rows["memberships"]) == 1
    assert PasswordHasher().verify(rows["users"][0][1], "newpass"), "重置后旧密码作废新密码生效"
    from argon2.exceptions import VerifyMismatchError

    try:
        PasswordHasher().verify(rows["users"][0][1], "user")
        raise AssertionError("旧密码不应再通过校验")
    except VerifyMismatchError:
        pass


def test_seed_custom_email_and_password(seeded_db):
    engine, tmp_path = seeded_db
    result = run_seed(
        tmp_path, "--email", "qa@eidolon.local", "--password", "sd", "--company-slug", "qa-co"
    )
    assert result.returncode == 0, result.stderr

    from argon2 import PasswordHasher

    rows = _rows(engine)
    assert rows["users"][0][0] == "qa@eidolon.local"
    assert rows["companies"] == ["qa-co"]
    assert PasswordHasher().verify(rows["users"][0][1], "sd")


def test_seed_refuses_non_sqlite_database(tmp_path):
    result = run_seed(tmp_path, db_url="postgresql://user@prod.example/eidolon")
    assert result.returncode != 0
    assert "只允许本地 SQLite" in result.stderr
    assert not (tmp_path / "dev.db").exists()


def test_seed_requires_database_url(tmp_path):
    env = {**os.environ}
    env.pop("EIDOLON_DATABASE_URL", None)
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env=env,
    )
    assert result.returncode != 0
    assert "EIDOLON_DATABASE_URL" in result.stderr
