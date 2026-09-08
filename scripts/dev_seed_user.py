#!/usr/bin/env python3
"""`make dev-seed-user` —— 注入本地测试账号（幂等）。

默认 user@example.com / user（display_name=user，这是"名为 user、密码为 user"的账号；
登录走邮箱）。行为：
  · 账号不存在 → 创建（argon2 哈希，与注册同款 PasswordHasher）；已存在 → 重置密码为
    给定值、强制 email_verified/active（保证 user/user 清库后恒可登录）。
  · 自建测试公司（slug test-co，含标准部门）+ OWNER 成员关系；公司已存在则复用，
    成员关系已存在则不降级。全程不触碰 .env、不动其他表。
安全：只允许 SQLite（本地工具清/写本地文件）；其余 scheme 直接拒绝。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_DEPARTMENTS = [
    ("Executive", "executive"),
    ("Product", "product"),
    ("Research", "research"),
    ("Engineering", "engineering"),
    ("QA", "qa"),
]


def _username_from_email(email: str) -> str:
    """邮箱未配套 username 时按 local part 派生（与 app 注册同规则）。"""
    base = "".join(
        c if c.isalnum() or c in "-_" else "-" for c in email.split("@")[0].lower()
    )
    return base.strip("-") or "user"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="seed local test user (make dev-seed-user)"
    )
    parser.add_argument("--email", default="user@example.com", help="login email")
    parser.add_argument("--password", default="user", help="login password")
    parser.add_argument("--username", default="user", help="login username")
    parser.add_argument("--display-name", default="user")
    parser.add_argument("--company-slug", default="test-co")
    parser.add_argument("--company-name", default="TestCo")
    args = parser.parse_args()

    url = os.environ.get("EIDOLON_DATABASE_URL")
    if not url:
        print(
            "ERROR: 未设置 EIDOLON_DATABASE_URL —— 请用 make dev-seed-user 调用（自动补默认值）。",
            file=sys.stderr,
        )
        return 2
    if not url.startswith("sqlite://"):
        print(
            f"ERROR: EIDOLON_DATABASE_URL='{url}' —— 工具只允许本地 SQLite，拒绝写入其他数据库。",
            file=sys.stderr,
        )
        return 2

    if url.startswith("sqlite:///"):
        db_path = Path(url[len("sqlite:///") :].split("?", 1)[0])
        if str(db_path) != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)

    server_dir = Path(__file__).resolve().parents[1] / "apps" / "server"
    sys.path.insert(0, str(server_dir))

    from app.models.auth import CompanyMembership, User
    from app.models.organization import Company, Department
    from argon2 import PasswordHasher
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    engine = create_engine(url)
    hasher = PasswordHasher()

    with Session(engine) as db:
        email = args.email.strip().lower()
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(
                email=email,
                username=args.username.strip().lower() or _username_from_email(email),
                password_hash=hasher.hash(args.password),
                display_name=args.display_name,
                email_verified=True,
                status="active",
            )
            db.add(user)
            db.flush()
            action = "created"
        else:
            user.password_hash = hasher.hash(args.password)
            user.username = args.username.strip().lower() or _username_from_email(email)
            user.email_verified = True
            user.status = "active"
            db.flush()
            action = "reset"

        company = db.scalar(select(Company).where(Company.slug == args.company_slug))
        if company is None:
            company = Company(
                name=args.company_name,
                slug=args.company_slug,
                description="Local test company seeded by make dev-seed-user.",
                industry="software",
                settings={},
                stage="FOUNDING",
            )
            db.add(company)
            db.flush()
            for name, slug in _DEPARTMENTS:
                db.add(
                    Department(
                        company_id=company.id,
                        name=name,
                        slug=slug,
                        description=f"{name} department",
                    )
                )
            company_action = "created"
        else:
            company_action = "reused"

        membership = db.scalar(
            select(CompanyMembership).where(
                CompanyMembership.user_id == user.id,
                CompanyMembership.company_id == company.id,
            )
        )
        if membership is None:
            db.add(
                CompanyMembership(user_id=user.id, company_id=company.id, role="OWNER")
            )
            member_action = "added OWNER"
        else:
            member_action = f"kept {membership.role}"

        db.commit()
        print(
            f"ok: user={email} ({action}) | company={company.slug} ({company_action}) | "
            f"membership={member_action} | password reset to '{args.password}'"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
