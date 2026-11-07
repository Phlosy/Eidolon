"""Secrets and Passkeys (R6-d): per-company provider keys encrypted at rest and
routed to runs, WebAuthn ceremonies with single-use challenges, and login rate limits."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from test_api import PASSWORD, PREFIX, register
from test_api_commands import owner_client, post

from eidolon.agent.providers import Completed, TextDelta, Usage
from eidolon.agent.providers.base import CallContext, ChatRequest, Message
from eidolon.agent.providers.routing import Resolved, RoutedProvider
from eidolon.api.app import create_app
from eidolon.api.passkeys import ChallengeRow, PasskeyError, PasskeyRow, PasskeyService
from eidolon.api.ratelimit import LoginLimiter
from eidolon.api.secrets import ProviderCredentials, SecretsError, fernet
from eidolon.db import SessionLocal
from eidolon.identity import IdentityProjection
from eidolon.kernel.consumers import process
from eidolon.kernel.facts import Actor


def test_provider_credentials_are_encrypted_at_rest_and_never_echoed(db):
    company = uuid4()
    creds = ProviderCredentials("a real passphrase")
    row = creds.set(
        db,
        company,
        kind="openai_compatible",
        api_key="sk-deepseek-1234abcd",
        base_url="https://api.deepseek.com",
        model="deepseek-flash",
        by="owner-1",
    )
    db.commit()
    assert "sk-deepseek" not in row.api_key_encrypted and row.key_tail == "abcd"
    assert (
        fernet("a real passphrase").decrypt(row.api_key_encrypted.encode())
        == b"sk-deepseek-1234abcd"
    )
    cfg = creds.config(db, company)
    assert cfg.api_key == "sk-deepseek-1234abcd" and cfg.kind == "openai_compatible"
    masked = ProviderCredentials.masked(row)
    assert masked["key_tail"] == "abcd" and "api_key" not in masked and "sk-" not in str(masked)
    # a different passphrase cannot read it; the placeholder passphrase is refused outright
    with pytest.raises(SecretsError, match="SECRET_KEY"):
        ProviderCredentials("another passphrase").config(db, company)
    with pytest.raises(SecretsError, match="unset or the placeholder"):
        ProviderCredentials("change-me-in-production").set(
            db,
            uuid4(),
            kind="anthropic",
            api_key="sk-ant-12345678",
            base_url=None,
            model=None,
            by="x",
        )
    with pytest.raises(SecretsError, match="kind"):
        creds.set(db, company, kind="ollama", api_key="12345678", base_url=None, model=None, by="x")
    # replacing keeps one row per company; clearing removes it
    creds.set(
        db, company, kind="anthropic", api_key="sk-ant-zzzz9999", base_url=None, model=None, by="o"
    )
    db.commit()
    assert creds.get(db, company).kind == "anthropic" and creds.get(db, company).key_tail == "9999"
    assert (
        creds.clear(db, company) and creds.get(db, company) is None and not creds.clear(db, company)
    )


class Scripted:
    def __init__(self, name):
        self.name = name
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        yield TextDelta(self.name)
        yield Completed(
            "end_turn",
            Usage(1, 1),
            request.model,
            self.name,
            request_id=f"{self.name}-{self.calls}",
        )

    async def embed(self, texts, *, model=None):
        return [[0.0] for _ in texts]


async def _drain(provider, request):
    out = []
    async for e in provider.complete(request):
        out.append(e)
    return out


def test_routed_provider_picks_the_company_s_provider_and_falls_back():
    default = Scripted("default")
    acme_id, other_id = uuid4(), uuid4()
    generation = {"v": 1}

    def resolve(company_id: UUID):
        if company_id != acme_id:
            return None
        return Resolved(Scripted(f"acme-g{generation['v']}"), generation["v"])

    routed = RoutedProvider(default, resolve)

    def req(company):
        return ChatRequest(
            model="m",
            messages=(Message.user("hi"),),
            max_tokens=8,
            context=CallContext(company, Actor.system()),
        )

    def texts(provider, request):
        return [e.text for e in asyncio.run(_drain(provider, request)) if isinstance(e, TextDelta)]

    assert texts(routed, req(acme_id)) == ["acme-g1"]
    assert texts(routed, req(other_id)) == ["default"]
    # cached while the generation holds; rebuilt when the credential changes
    first = routed.for_company(acme_id)
    assert routed.for_company(acme_id) is first
    generation["v"] = 2
    fresh = routed.for_company(acme_id)
    assert fresh is not first and fresh.name == "acme-g2"
    no_context = ChatRequest(model="m", messages=(Message.user("hi"),), max_tokens=8)
    assert texts(routed, no_context) == ["default"]
    assert asyncio.run(routed.embed(["x"])) == [[0.0]]


def test_login_limiter_slides_and_resets():
    t = {"now": 1000.0}
    limiter = LoginLimiter(limit=3, window_s=60, clock=lambda: t["now"])
    for _ in range(3):
        assert limiter.blocked("ip:1", "account:a") is None
        limiter.failed("ip:1", "account:a")
    assert limiter.blocked("ip:1") == pytest.approx(60.0)
    assert limiter.blocked("account:b") is None and limiter.blocked("account:a") is not None
    t["now"] += 61
    assert limiter.blocked("ip:1", "account:a") is None  # the window slid past
    limiter.failed("ip:1")
    limiter.failed("ip:1")
    limiter.failed("ip:1")
    assert limiter.blocked("ip:1") is not None
    limiter.succeeded("ip:1")
    assert limiter.blocked("ip:1") is None


def test_api_rate_limits_sign_in_and_manages_provider_credentials(db, monkeypatch):
    client, user_id = owner_client()
    company = UUID(post(client, "/companies", {"name": "S", "slug": "s9"}).json()["company_id"])
    process(SessionLocal, IdentityProjection())
    # the owner's own provider: masked on the way back out; a placeholder SECRET_KEY is refused
    r = client.get(f"{PREFIX}/companies/{company}/provider")
    assert r.status_code == 200 and r.json() == {"configured": False}
    body = {
        "kind": "openai_compatible",
        "api_key": "sk-test-key-98765432",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-flash",
    }
    r = client.put(
        f"{PREFIX}/companies/{company}/provider",
        json=body,
        headers={"X-CSRF-Token": client.cookies.get("eidolon_csrf")},
    )
    assert r.status_code == 409 and "SECRET_KEY" in r.text
    client.app.state.credentials = ProviderCredentials("test passphrase that is real")
    r = client.put(
        f"{PREFIX}/companies/{company}/provider",
        json=body,
        headers={"X-CSRF-Token": client.cookies.get("eidolon_csrf")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["configured"] and r.json()["key_tail"] == "5432" and "sk-test" not in r.text
    assert client.get(f"{PREFIX}/companies/{company}/provider").json()["model"] == "deepseek-flash"
    # a stranger cannot read or set it
    stranger = TestClient(client.app)
    stranger.__enter__()
    register(stranger, "s9@example.com", name="S")
    process(SessionLocal, IdentityProjection())
    assert stranger.get(f"{PREFIX}/companies/{company}/provider").status_code == 403
    r = client.delete(
        f"{PREFIX}/companies/{company}/provider",
        headers={"X-CSRF-Token": client.cookies.get("eidolon_csrf")},
    )
    assert r.status_code == 204 and client.get(f"{PREFIX}/companies/{company}/provider").json() == {
        "configured": False
    }

    # five wrong passwords lock the account for the window, even with the right password
    client.app.state.limiter = LoginLimiter(limit=5, window_s=900)
    fresh = TestClient(client.app)
    for _ in range(5):
        assert (
            fresh.post(
                f"{PREFIX}/auth/login", json={"email": "owner@example.com", "password": "nope"}
            ).status_code
            == 401
        )
    r = fresh.post(
        f"{PREFIX}/auth/login", json={"email": "owner@example.com", "password": PASSWORD}
    )
    assert r.status_code == 429 and "Retry-After" in r.headers
    client.app.state.limiter = LoginLimiter(limit=5, window_s=900)  # window over (fresh limiter)
    assert (
        fresh.post(
            f"{PREFIX}/auth/login", json={"email": "owner@example.com", "password": PASSWORD}
        ).status_code
        == 200
    )
    client.__exit__(None, None, None)
    stranger.__exit__(None, None, None)


def test_passkey_ceremonies_use_single_use_expiring_challenges(db):
    svc = PasskeyService(rp_id="localhost", rp_name="Eidolon", origin="http://localhost:26880")
    user_id = uuid4()
    opts = svc.registration_options(
        db, user_id=user_id, email="o@x.io", display_name="O", name="My key"
    )
    db.commit()
    pk = opts["public_key"]
    assert pk["rp"] == {"id": "localhost", "name": "Eidolon"} and pk["user"]["name"] == "o@x.io"
    assert pk["authenticatorSelection"]["userVerification"] == "required"
    row = db.get(ChallengeRow, opts["challenge"])
    assert row.purpose == "register" and row.user_id == user_id and row.name == "My key"
    # garbage from the browser is refused and the challenge is spent
    with pytest.raises(PasskeyError) as exc:
        svc.verify_registration(
            db, user_id=user_id, challenge=opts["challenge"], credential={"id": "x", "response": {}}
        )
    assert exc.value.status == 400
    db.commit()
    with pytest.raises(PasskeyError) as exc:
        svc.verify_registration(db, user_id=user_id, challenge=opts["challenge"], credential={})
    assert exc.value.status == 404  # single use
    # another user's challenge is not yours
    opts2 = svc.registration_options(
        db, user_id=user_id, email="o@x.io", display_name="O", name="k"
    )
    with pytest.raises(PasskeyError) as exc:
        svc.verify_registration(db, user_id=uuid4(), challenge=opts2["challenge"], credential={})
    assert exc.value.status == 404
    # expired challenges are gone
    expired = PasskeyService(
        rp_id="localhost", rp_name="E", origin="http://localhost:26880", ttl_minutes=0
    )
    opts3 = expired.authentication_options(db)
    db.commit()
    db.get(ChallengeRow, opts3["challenge"]).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    with pytest.raises(PasskeyError) as exc:
        expired.verify_authentication(db, challenge=opts3["challenge"], credential={"id": "nope"})
    assert exc.value.status == 410
    # login ceremony: an unknown credential is 401 (challenge spent); registered keys
    # list / rename / remove
    opts4 = svc.authentication_options(db)
    db.commit()
    with pytest.raises(PasskeyError) as exc:
        svc.verify_authentication(db, challenge=opts4["challenge"], credential={"id": "unknown"})
    assert exc.value.status == 401
    db.add(
        PasskeyRow(
            credential_id="cred-1",
            user_id=user_id,
            public_key="pk",
            sign_count=0,
            name="Laptop",
            transports=["internal"],
            device_type="multi_device",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()
    assert [p.name for p in svc.list(db, user_id)] == ["Laptop"]
    assert svc.rename(db, user_id, "cred-1", "Work laptop").name == "Work laptop"
    with pytest.raises(PasskeyError):
        svc.rename(db, uuid4(), "cred-1", "theirs")
    svc.remove(db, user_id, "cred-1")
    db.commit()
    assert svc.list(db, user_id) == []
    # options sweep expired challenges
    assert db.get(ChallengeRow, opts3["challenge"]) is None


def test_passkey_routes_require_a_session_except_the_login_ceremony(db):
    app = create_app(SessionLocal)
    with TestClient(app) as client:
        assert client.get(f"{PREFIX}/auth/passkeys").status_code == 401
        r = client.post(f"{PREFIX}/auth/passkeys/authentication/options")
        assert (
            r.status_code == 200
            and "challenge" in r.json()
            and r.json()["public_key"]["rpId"] == "localhost"
        )
        r = client.post(
            f"{PREFIX}/auth/passkeys/authentication/verify",
            json={"challenge": r.json()["challenge"], "credential": {"id": "nope"}},
        )
        assert r.status_code == 401
        register(client, "pk@example.com", person_id="pk-1")
        process(SessionLocal, IdentityProjection())
        csrf = {"X-CSRF-Token": client.cookies.get("eidolon_csrf")}
        r = client.post(
            f"{PREFIX}/auth/passkeys/registration/options", json={"name": "Phone"}, headers=csrf
        )
        assert r.status_code == 200 and r.json()["public_key"]["user"]["name"] == "pk@example.com"
        r = client.post(
            f"{PREFIX}/auth/passkeys/registration/verify",
            json={"challenge": r.json()["challenge"], "credential": {"id": "x", "response": {}}},
            headers=csrf,
        )
        assert r.status_code == 400
        assert client.get(f"{PREFIX}/auth/passkeys").json() == []
        assert client.delete(f"{PREFIX}/auth/passkeys/missing", headers=csrf).status_code == 404
