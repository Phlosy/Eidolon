"""Login supports username OR email. Unknown ≠ bad / probing-safe: identical 401.

注册不传 username → 自动从邮箱 local part 派生；传了则用显式值（小写清洗）。
登录 identifier：含 @ → 按 email；否则按 username（大小写不敏感）。
两个账号 local part 相同 → 派生 username 自动加后缀，保证 unique。
"""

from __future__ import annotations

import uuid


def register_and_verify(client, *, email: str | None = None, username: str | None = None) -> dict:
    """注册→验证→返回 verified payload。email 缺省则用唯一地址（测试共享 client/DB）。"""
    email = email or f"u{uuid.uuid4().hex[:10]}@example.com"
    payload = {
        "email": email,
        "password": "correct horse battery staple1",
        "display_name": "Founder",
    }
    if username is not None:
        payload["username"] = username
    registered = client.post("/api/v1/auth/register", json=payload)
    assert registered.status_code == 201, registered.text
    token = registered.json()["development_verification_token"]
    verified = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert verified.status_code == 200, verified.text
    return verified.json()


def login(client, identifier: str, password: str = "correct horse battery staple1"):
    return client.post("/api/v1/auth/login", json={"identifier": identifier, "password": password})


def test_login_by_username_and_email_after_register(client):
    email = f"founder-{uuid.uuid4().hex[:6]}@example.com"
    register_and_verify(client, email=email)
    by_email = login(client, email)
    by_username = login(client, email.split("@")[0])
    assert by_email.status_code == 200
    assert by_username.status_code == 200
    assert by_email.json()["user"]["email"] == email
    assert by_username.json()["user"]["email"] == email


def test_login_username_is_case_insensitive(client):
    email = f"founder-{uuid.uuid4().hex[:6]}@example.com"
    register_and_verify(client, email=email)
    assert login(client, email.split("@")[0].upper()).status_code == 200


def test_login_by_legacy_email_field_still_works(client):
    email = f"founder-{uuid.uuid4().hex[:6]}@example.com"
    register_and_verify(client, email=email)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "correct horse battery staple1"},
    )
    assert response.status_code == 200


def test_explicit_username_from_registration_is_used(client):
    register_and_verify(client, username="john_doe")
    assert login(client, "john_doe").status_code == 200
    assert login(client, "JOHN_DOE").status_code == 200
    # 不存在的用户名与错误密码同回报（探测无差别）
    assert login(client, "john").status_code == 401


def test_same_local_part_two_domains_yields_unique_username(client):
    register_and_verify(client, email="alice@example.com")
    register_and_verify(client, email="alice@elsewhere.org")
    assert login(client, "alice").status_code == 200
    assert login(client, "alice-2").status_code == 200


def test_unknown_username_and_wrong_password_share_401(client):
    email = f"founder-{uuid.uuid4().hex[:6]}@example.com"
    register_and_verify(client, email=email)
    unknown = login(client, "ghost-user")
    wrong = login(client, email.split("@")[0], password="nope nope nope nope1")
    assert unknown.status_code == 401
    assert wrong.status_code == 401


def test_login_requires_identifier(client):
    response = client.post("/api/v1/auth/login", json={"password": "whatever"})
    assert response.status_code == 422


def test_register_rejects_invalid_username_charset(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"badname-{uuid.uuid4().hex[:6]}@example.com",
            "password": "correct horse battery staple1",
            "username": "TeSt  NAME!",
        },
    )
    assert response.status_code == 422
    assert "用户名只允许" in response.text
