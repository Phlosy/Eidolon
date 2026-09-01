"""Secret redaction: registry, patterns, log path, WS/event path (v0.2)."""

import json
import logging

from app.core import redaction
from app.core.logging import RedactionFilter
from app.providers.secrets.store import LocalEncryptedSecretStore


def test_mask_secret_shape():
    assert redaction.mask_secret("sk-abcdef1234567890") == "sk-••••••••7890"
    assert redaction.mask_secret("short") == "••••••••"
    assert redaction.mask_secret(None) is None


def test_redact_registered_value():
    secret = "sk-live-zzz-uniquetoken123456"
    redaction.register_secret(secret)
    try:
        out = redaction.redact(f"the key is {secret} ok?")
        assert secret not in out
        assert "••••••••3456" in out
    finally:
        redaction.unregister_secret(secret)


def test_redact_patterns_without_registration():
    text = (
        "key=sk-proj-AbCdEfGh12345678 header=Bearer abcdef0123456789 OPENAI_API_KEY=hunter2hunter2"
    )
    out = redaction.redact(text)
    assert "sk-proj-AbCdEfGh12345678" not in out
    assert "abcdef0123456789" not in out
    assert "hunter2hunter2" not in out
    assert "OPENAI_API_KEY=••••••••" in out
    assert "Bearer ••••••••" in out


def test_redact_data_recursive():
    secret = "sk-nested-secret-value-9999"
    redaction.register_secret(secret)
    try:
        out = redaction.redact_data({"a": [secret, {"b": f"x {secret} y"}], "n": 42})
        assert secret not in json.dumps(out)
        assert out["n"] == 42
    finally:
        redaction.unregister_secret(secret)


def test_log_record_redaction():
    secret = "sk-log-leak-check-424242"
    redaction.register_secret(secret)
    try:
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "token %s", (secret,), None)
        RedactionFilter().filter(record)
        assert secret not in record.getMessage()
    finally:
        redaction.unregister_secret(secret)


def test_secret_store_roundtrip_and_masking(db):
    store = LocalEncryptedSecretStore("unit-test-key")
    value = "sk-roundtrip-value-010101"
    ref = store.store(db, value)
    db.commit()
    assert ref.startswith("local:")
    # ciphertext in the table is not plaintext
    from sqlalchemy import select

    from app.models.provider import Secret

    row = db.scalars(select(Secret).where(Secret.ref == ref)).one()
    assert value not in row.ciphertext
    assert store.retrieve(db, ref) == value
    # retrieving registers the value with the redaction registry
    assert value not in redaction.redact(f"leak: {value}")
    store.delete(db, ref)
    db.commit()
    assert store.retrieve(db, ref) is None


def test_ws_event_payloads_are_redacted(client):
    """Events reaching the WS feed go through the same redacted bus payload."""
    secret = "sk-ws-leak-check-777777"
    from app.events.bus import bus

    redaction.register_secret(secret)
    try:
        bus.publish("provider.tested", {"debug": secret})
        events = client.get("/api/v1/events?limit=50").json()
        assert secret not in json.dumps(events)
    finally:
        redaction.unregister_secret(secret)
