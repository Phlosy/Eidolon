"""Authentication, membership, session and passkey security contracts."""

from datetime import timedelta

from app.core.config import settings
from app.models.auth import PasskeyCredential, UserSession, WebAuthnChallenge
from app.models.base import utcnow
from app.models.organization import Employee
from app.models.project import Project
from app.repositories import knowledge as knowledge_repo


def _register_and_verify(client, email: str = "founder@example.com") -> dict:
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "correct horse battery staple",
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
            "password": "correct horse battery staple",
            "locale": "en-US",
        },
    )

    assert response.status_code == 201
    assert response.json()["development_verification_token"] is None
    assert delivered["email"] == "mail@example.com"
    assert len(delivered["token"]) >= 32


def test_duplicate_email_and_wrong_password_are_rejected(client):
    email = "duplicate@example.com"
    _register_and_verify(client, email)
    duplicate = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "another secure password"},
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
        json={"email": email, "password": "correct horse battery staple"},
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
