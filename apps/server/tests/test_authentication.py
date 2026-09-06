"""Authentication, membership, session and passkey security contracts."""

from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import settings
from app.models.auth import PasskeyCredential, User, UserSession, WebAuthnChallenge
from app.models.base import utcnow
from app.models.organization import Employee
from app.models.project import Project
from app.repositories import knowledge as knowledge_repo
from app.schemas.auth import RegisterRequest


def _register_and_verify(client, email: str = "founder@example.com") -> dict:
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "correct horse battery staple1",
            "display_name": "Founder",
            "locale": "zh-CN",
            "timezone": "Asia/Shanghai",
        },
    )
    assert registered.status_code == 201
    token = registered.json()["development_verification_token"]
    verified = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert verified.status_code == 200
    return verified.json()


def test_registration_verification_creates_empty_company_and_owner(client, db):
    payload = _register_and_verify(client)

    assert payload["user"]["email"] == "founder@example.com"
    assert payload["user"]["email_verified"] is True
    assert payload["company"]["stage"] == "FOUNDING"
    assert payload["membership"]["role"] == "OWNER"
    assert db.query(Employee).filter(Employee.company_id == payload["company"]["id"]).count() == 0
    assert client.get("/api/v1/projects").json() == []
    assert client.get("/api/v1/drive/tree").json() == []
    assert client.get("/api/v1/runtimes").json() == []
    assert client.get("/api/v1/providers").json() == []
    assert client.get("/api/v1/git").json()["connections"] == []

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["company"]["id"] == payload["company"]["id"]


def test_smtp_registration_delivers_token_without_returning_it(client, monkeypatch):
    delivered = {}
    monkeypatch.setattr(settings, "email_delivery_mode", "smtp")
    monkeypatch.setattr(
        "app.services.auth.send_verification_email",
        lambda email, token, *, locale: delivered.update(
            {"email": email, "token": token, "locale": locale}
        ),
    )
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "mail@example.com",
            "password": "correct horse battery staple1",
            "locale": "en-US",
        },
    )

    assert response.status_code == 201
    assert response.json()["development_verification_token"] is None
    assert delivered["email"] == "mail@example.com"
    assert len(delivered["token"]) >= 32


@pytest.mark.parametrize(
    "password", ["abcdefg1", "1234567!", "abcdefg!", "abcdefg😀", "abcdefg\ufeff"]
)
def test_registration_accepts_two_password_character_categories(password):
    request = RegisterRequest(email="valid-password@example.com", password=password)

    assert request.password == password


@pytest.mark.parametrize(
    "password", ["abc123!", "abcdefgh", "12345678", "!!!!!!!!", "abcdef😀", "abcdefg\u0085"]
)
def test_registration_rejects_short_or_single_category_passwords(password):
    with pytest.raises(ValidationError):
        RegisterRequest(email="invalid-password@example.com", password=password)


def test_registration_returns_validation_error_for_invalid_password(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "invalid-password@example.com", "password": "abcdefgh"},
    )

    assert response.status_code == 422


def test_registration_schema_documents_complete_password_policy():
    password_schema = RegisterRequest.model_json_schema()["properties"]["password"]

    assert password_schema["minLength"] == 8
    assert password_schema["description"] == (
        "Minimum 8 characters; include at least two: English letters, numbers, special characters."
    )


def test_duplicate_email_and_wrong_password_are_rejected(client):
    email = "duplicate@example.com"
    _register_and_verify(client, email)
    duplicate = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "another secure password1"},
    )
    assert duplicate.status_code == 409

    client.post("/api/v1/auth/logout")
    wrong = client.post("/api/v1/auth/login", json={"email": email, "password": "definitely wrong"})
    assert wrong.status_code == 401


def test_login_session_rotation_logout_and_revoke(client, db):
    email = "sessions@example.com"
    _register_and_verify(client, email)
    first_cookie = client.cookies.get("eidolon_session")
    client.post("/api/v1/auth/logout")

    logged_in = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "correct horse battery staple1"},
    )
    assert logged_in.status_code == 200
    assert client.cookies.get("eidolon_session") != first_cookie

    sessions = client.get("/api/v1/auth/sessions").json()
    current = next(item for item in sessions if item["current"])
    assert client.delete(f"/api/v1/auth/sessions/{current['id']}").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_expired_session_is_rejected(client, db):
    _register_and_verify(client, "expired@example.com")
    session = (
        db.query(UserSession)
        .filter(UserSession.revoked_at.is_(None))
        .order_by(UserSession.id.desc())
        .first()
    )
    session.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_passkey_challenges_are_one_time_and_expire(client, db, monkeypatch):
    _register_and_verify(client, "passkey@example.com")
    options = client.post("/api/v1/auth/passkeys/registration/options", json={"name": "MacBook"})
    assert options.status_code == 200
    assert options.json()["public_key"]["authenticatorSelection"]["userVerification"] == "required"
    challenge_id = options.json()["challenge_id"]

    challenge = db.get(WebAuthnChallenge, challenge_id)
    challenge.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    expired = client.post(
        "/api/v1/auth/passkeys/registration/verify",
        json={"challenge_id": challenge_id, "credential": {"id": "fake", "rawId": "fake"}},
    )
    assert expired.status_code == 410

    fresh = client.post(
        "/api/v1/auth/passkeys/registration/options", json={"name": "MacBook"}
    ).json()

    class Verification:
        credential_id = b"credential-id"
        credential_public_key = b"public-key"
        sign_count = 0
        credential_device_type = "multi_device"
        credential_backed_up = True

    monkeypatch.setattr(
        "app.services.auth.verify_registration_response", lambda **_: Verification()
    )
    request = {
        "challenge_id": fresh["challenge_id"],
        "credential": {"id": "Y3JlZGVudGlhbC1pZA", "rawId": "Y3JlZGVudGlhbC1pZA"},
    }
    assert client.post("/api/v1/auth/passkeys/registration/verify", json=request).status_code == 201
    assert client.post("/api/v1/auth/passkeys/registration/verify", json=request).status_code == 409
    stored = db.query(PasskeyCredential).filter_by(name="MacBook").one()
    assert not hasattr(stored, "private_key")


def test_passkey_management_and_discoverable_login(client, db, monkeypatch):
    verified = _register_and_verify(client, "discoverable@example.com")
    user_id = verified["user"]["id"]
    credential = PasskeyCredential(
        user_id=user_id,
        credential_id="ZGlzY292ZXJhYmxl",
        public_key="cHVibGlj",
        sign_count=0,
        transports=["internal"],
        device_type="multi_device",
        backed_up=True,
        name="Phone",
        metadata_json={},
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)

    renamed = client.patch(f"/api/v1/auth/passkeys/{credential.id}", json={"name": "Pixel"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Pixel"

    client.post("/api/v1/auth/logout")
    options = client.post("/api/v1/auth/passkeys/authentication/options").json()
    assert options["public_key"]["userVerification"] == "required"

    class Authentication:
        new_sign_count = 1

    monkeypatch.setattr(
        "app.services.auth.verify_authentication_response", lambda **_: Authentication()
    )
    login = client.post(
        "/api/v1/auth/passkeys/authentication/verify",
        json={
            "challenge_id": options["challenge_id"],
            "credential": {"id": credential.credential_id, "rawId": credential.credential_id},
        },
    )
    assert login.status_code == 200
    assert login.json()["user"]["id"] == user_id

    replay = client.post(
        "/api/v1/auth/passkeys/authentication/verify",
        json={
            "challenge_id": options["challenge_id"],
            "credential": {"id": credential.credential_id, "rawId": credential.credential_id},
        },
    )
    assert replay.status_code == 409
    assert client.delete(f"/api/v1/auth/passkeys/{credential.id}").status_code == 204


def test_cookie_authenticated_mutations_require_csrf(client):
    _register_and_verify(client, "csrf@example.com")
    old = settings.auth_required
    settings.auth_required = True
    try:
        denied = client.post("/api/v1/tutorial/start")
        assert denied.status_code == 403
        denied_logout = client.post("/api/v1/auth/logout")
        assert denied_logout.status_code == 403
        csrf = client.cookies.get("eidolon_csrf")
        allowed = client.post("/api/v1/tutorial/start", headers={"X-CSRF-Token": csrf})
        assert allowed.status_code == 200
        allowed_logout = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        assert allowed_logout.status_code == 204
    finally:
        settings.auth_required = old


def test_authenticated_project_reads_are_scoped_to_the_users_company(client, db):
    legacy = client.post(
        "/api/v1/projects", json={"name": "Legacy tenant project", "description": "scope"}
    )
    assert legacy.status_code == 201
    legacy_project = db.get(Project, legacy.json()["id"])
    artifact = client.post(
        "/api/v1/artifacts",
        json={
            "project_id": legacy_project.id,
            "type": "other",
            "title": "Legacy tenant artifact",
            "content": "private",
        },
    )
    assert artifact.status_code == 201
    legacy_employee = db.query(Employee).filter_by(company_id=legacy_project.company_id).first()
    knowledge_repo.create_knowledge_item(
        db,
        scope="company",
        owner_employee_id=legacy_employee.id,
        title="Legacy tenant knowledge",
        content="private",
        topic="tenant-boundary",
    )
    db.commit()
    message = client.post(
        "/api/v1/messages",
        json={
            "project_id": legacy_project.id,
            "sender_id": legacy_employee.id,
            "channel": "project",
            "content": "private",
        },
    )
    assert message.status_code == 201
    account = _register_and_verify(client, "tenant-scope@example.com")
    assert account["company"]["id"] != legacy_project.company_id

    assert client.get("/api/v1/projects").json() == []
    assert client.get("/api/v1/artifacts").json() == []
    assert client.get("/api/v1/messages").json() == []
    assert client.get("/api/v1/knowledge").json() == []
    assert client.get(f"/api/v1/projects/{legacy_project.id}").status_code == 404


def test_profile_update_persists_display_name(client):
    _register_and_verify(client, "rename@example.com")

    updated = client.patch("/api/v1/auth/me", json={"display_name": "New Name"})
    assert updated.status_code == 200
    assert updated.json()["user"]["display_name"] == "New Name"
    assert client.get("/api/v1/auth/me").json()["user"]["display_name"] == "New Name"


def test_delete_account_requires_email_confirmation(client):
    _register_and_verify(client, "leaver@example.com")

    requested = client.post("/api/v1/auth/me/delete")
    assert requested.status_code == 200
    token = requested.json()["development_verification_token"]
    assert token

    # 点确认链接之前：账号仍然活着
    assert client.get("/api/v1/auth/me").status_code == 200

    confirmed = client.post("/api/v1/auth/account-actions/confirm", json={"token": token})
    assert confirmed.status_code == 200
    assert confirmed.json()["action"] == "delete_account"

    # 确认后：会话立即失效，邮箱+密码也无法再登录
    assert client.get("/api/v1/auth/me").status_code == 401
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "leaver@example.com", "password": "correct horse battery staple1"},
    )
    assert login.status_code == 401


def test_deleted_account_email_can_register_again(client):
    _register_and_verify(client, "comeback@example.com")
    token = client.post("/api/v1/auth/me/delete").json()["development_verification_token"]
    client.post("/api/v1/auth/account-actions/confirm", json={"token": token})

    # 同一邮箱可以重新注册并完成验证
    registered = client.post(
        "/api/v1/auth/register",
        json={"email": "comeback@example.com", "password": "fresh start pass 1"},
    )
    assert registered.status_code == 201
    verify_token = registered.json()["development_verification_token"]
    verified = client.post("/api/v1/auth/verify-email", json={"token": verify_token})
    assert verified.status_code == 200
    assert verified.json()["user"]["email"] == "comeback@example.com"


def test_registration_frees_email_from_pre_anonymization_deleted_account(client, db):
    """匿名化之前就注销的账号（email 未被改写）也不能挡新注册。"""
    _register_and_verify(client, "legacy@example.com")
    user = db.scalar(select(User).where(User.email == "legacy@example.com"))
    user.status = "deleted"
    db.commit()

    registered = client.post(
        "/api/v1/auth/register",
        json={"email": "legacy@example.com", "password": "fresh start pass 1"},
    )
    assert registered.status_code == 201
    verify_token = registered.json()["development_verification_token"]
    verified = client.post("/api/v1/auth/verify-email", json={"token": verify_token})
    assert verified.status_code == 200


def test_delete_account_requires_authentication(client):
    assert client.post("/api/v1/auth/me/delete").status_code == 401


def test_change_password_requires_current_password_and_email_confirmation(client):
    _register_and_verify(client, "pw@example.com")

    wrong = client.post(
        "/api/v1/auth/me/password",
        json={"current_password": "nope", "new_password": "new horse battery 1"},
    )
    assert wrong.status_code == 400

    weak = client.post(
        "/api/v1/auth/me/password",
        json={
            "current_password": "correct horse battery staple1",
            "new_password": "aaaaaaaa",
        },
    )
    assert weak.status_code == 422

    requested = client.post(
        "/api/v1/auth/me/password",
        json={
            "current_password": "correct horse battery staple1",
            "new_password": "new horse battery 1",
        },
    )
    assert requested.status_code == 200
    token = requested.json()["development_verification_token"]

    # 确认前：旧密码仍然有效
    assert client.get("/api/v1/auth/me").status_code == 200
    still_old = client.post(
        "/api/v1/auth/login",
        json={"email": "pw@example.com", "password": "correct horse battery staple1"},
    )
    assert still_old.status_code == 200

    confirmed = client.post("/api/v1/auth/account-actions/confirm", json={"token": token})
    assert confirmed.status_code == 200
    assert confirmed.json()["action"] == "change_password"

    # 确认后：所有会话被吊销，旧密码失效、新密码可登录
    assert client.get("/api/v1/auth/me").status_code == 401
    old = client.post(
        "/api/v1/auth/login",
        json={"email": "pw@example.com", "password": "correct horse battery staple1"},
    )
    assert old.status_code == 401
    new = client.post(
        "/api/v1/auth/login", json={"email": "pw@example.com", "password": "new horse battery 1"}
    )
    assert new.status_code == 200


def test_email_change_only_applies_after_verification(client):
    _register_and_verify(client, "old@example.com")

    requested = client.post("/api/v1/auth/me/email", json={"new_email": "new@example.com"})
    assert requested.status_code == 200
    token = requested.json()["development_verification_token"]
    assert token

    # 点链接之前，邮箱不变
    assert client.get("/api/v1/auth/me").json()["user"]["email"] == "old@example.com"

    confirmed = client.post("/api/v1/auth/account-actions/confirm", json={"token": token})
    assert confirmed.status_code == 200
    assert confirmed.json()["action"] == "change_email"
    assert client.get("/api/v1/auth/me").json()["user"]["email"] == "new@example.com"

    # token 一次性；旧邮箱登录失败、新邮箱成功
    again = client.post("/api/v1/auth/account-actions/confirm", json={"token": token})
    assert again.status_code == 409
    old = client.post(
        "/api/v1/auth/login",
        json={"email": "old@example.com", "password": "correct horse battery staple1"},
    )
    assert old.status_code == 401
    new = client.post(
        "/api/v1/auth/login",
        json={"email": "new@example.com", "password": "correct horse battery staple1"},
    )
    assert new.status_code == 200


def test_email_change_rejects_taken_email(client):
    _register_and_verify(client, "taken1@example.com")
    _register_and_verify(client, "taken2@example.com")

    response = client.post("/api/v1/auth/me/email", json={"new_email": "taken1@example.com"})
    assert response.status_code == 409


def test_avatar_upload_roundtrip(client):
    _register_and_verify(client, "avatar@example.com")

    bad = client.post(
        "/api/v1/auth/me/avatar", content=b"hello", headers={"Content-Type": "text/plain"}
    )
    assert bad.status_code == 415

    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    uploaded = client.post(
        "/api/v1/auth/me/avatar", content=png, headers={"Content-Type": "image/png"}
    )
    assert uploaded.status_code == 200
    url = uploaded.json()["user"]["avatar"]
    assert url.startswith("/api/v1/auth/avatars/")

    fetched = client.get(url)
    assert fetched.status_code == 200
    assert fetched.content == png

    # 路径穿越被拒
    traversal = client.get("/api/v1/auth/avatars/..%2F..%2Ftest.db")
    assert traversal.status_code in (400, 404, 422)


def test_resend_verification_invalidates_previous_link(client):
    registered = client.post(
        "/api/v1/auth/register",
        json={"email": "resend@example.com", "password": "correct horse battery staple1"},
    )
    assert registered.status_code == 201
    first_token = registered.json()["development_verification_token"]

    resent = client.post("/api/v1/auth/verify-email/resend", json={"email": "resend@example.com"})
    assert resent.status_code == 200
    second_token = resent.json()["development_verification_token"]
    assert second_token and second_token != first_token

    # 60 秒内不允许再次重发
    denied = client.post("/api/v1/auth/verify-email/resend", json={"email": "resend@example.com"})
    assert denied.status_code == 429

    # 旧链接作废，新链接可验证
    stale = client.post("/api/v1/auth/verify-email", json={"token": first_token})
    assert stale.status_code == 409
    verified = client.post("/api/v1/auth/verify-email", json={"token": second_token})
    assert verified.status_code == 200


def test_resend_verification_does_not_leak_unknown_email(client):
    response = client.post(
        "/api/v1/auth/verify-email/resend", json={"email": "ghost@example.com"}
    )
    assert response.status_code == 200
    assert response.json()["development_verification_token"] is None
