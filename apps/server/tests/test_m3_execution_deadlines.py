"""Original request deadlines survive process death, replay and explicit retries."""

import json
import subprocess
import sys
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from test_m3_telemetry import bound as _bound
from test_m3_telemetry import prepare
from test_m3_telemetry import worker_world as _worker_world

from app.models.base import as_utc, utcnow
from app.models.runtime_execution import RuntimeAttempt
from app.runtimes.base import RuntimeProtocolError
from app.work import telemetry

bound = _bound
worker_world = _worker_world


def test_request_replay_and_retry_keep_first_deadline(db, bound, monkeypatch):
    now = utcnow()
    deadline = now + timedelta(seconds=40)
    first = prepare(bound, deadline_at=deadline)
    reused = prepare(bound, deadline_at=now + timedelta(seconds=900))
    assert reused.attempt_id == first.attempt_id
    first.accepted("original-run", bound.runtime.id)
    first.terminal("failed", "hermes.status")
    retried = prepare(bound, retry_of=first.attempt_id, deadline_at=now + timedelta(seconds=900))
    db.expire_all()
    attempts = list(db.scalars(select(RuntimeAttempt).order_by(RuntimeAttempt.id)))
    assert len(attempts) == 2
    assert all(as_utc(row.deadline_at) == deadline for row in attempts)
    monkeypatch.setattr(telemetry, "utcnow", lambda: now + timedelta(seconds=30))
    assert first.remaining_timeout(900) == retried.remaining_timeout(900) == 10
    assert retried.remaining_timeout(3) == 3
    monkeypatch.setattr(telemetry, "utcnow", lambda: deadline)
    with pytest.raises(TimeoutError, match="deadline_expired"):
        retried.remaining_timeout(900)


def test_legacy_unknown_is_not_backfilled_on_replay_or_retry(db, bound):
    first = prepare(bound)
    reused = prepare(bound, deadline_at=utcnow() + timedelta(seconds=30))
    assert reused.attempt_id == first.attempt_id
    first.accepted("legacy-run", bound.runtime.id)
    first.terminal("failed", "hermes.status")
    retried = prepare(
        bound, retry_of=first.attempt_id, deadline_at=utcnow() + timedelta(seconds=30)
    )
    db.expire_all()
    assert all(row.deadline_at is None for row in db.scalars(select(RuntimeAttempt)))
    for recorder in (first, reused, retried):
        with pytest.raises(RuntimeProtocolError, match="deadline_unknown"):
            recorder.remaining_timeout(30)


@pytest.mark.parametrize("deadline", [0, True, "2026-09-21", datetime(2026, 9, 21)])
def test_invalid_deadline_fails_before_persisting_request(db, bound, deadline):
    with pytest.raises(RuntimeProtocolError, match="invalid_execution_deadline"):
        prepare(bound, deadline_at=deadline)
    assert db.scalar(select(RuntimeAttempt)) is None


@pytest.mark.parametrize("maximum", [0, -1, 901, True, float("nan"), float("inf")])
def test_remaining_timeout_requires_finite_bounded_allowance(bound, maximum):
    recorder = prepare(bound, deadline_at=utcnow() + timedelta(seconds=30))
    with pytest.raises(RuntimeProtocolError, match="invalid_run_timeout"):
        recorder.remaining_timeout(maximum)


@pytest.mark.parametrize("known", [True, False])
def test_deadline_cannot_be_changed_or_backfilled_with_direct_sql(db, bound, known):
    recorder = prepare(bound, deadline_at=utcnow() + timedelta(seconds=30) if known else None)
    for value in ("2099-01-01 00:00:00", "not-a-time", None):
        if not known and value is None:
            continue
        with pytest.raises(IntegrityError):
            db.execute(
                text("UPDATE runtime_attempts SET deadline_at=:deadline WHERE id=:id"),
                {"deadline": value, "id": recorder.attempt_id},
            )
        db.rollback()


@pytest.mark.parametrize(
    "request_key,number,value",
    [("new", 1, "not-a-time"), ("worker", 2, "2099-01-01 00:00:00"), ("worker", 2, None)],
)
def test_direct_insert_cannot_create_invalid_or_extended_retry_deadlines(
    db, bound, request_key, number, value
):
    recorder = prepare(bound, deadline_at=utcnow() + timedelta(seconds=30))
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO runtime_attempts (work_session_id,request_key,attempt_number,"
                "idempotency_key,request_digest,local_session_ref,runtime_instance_id,"
                "instance_fingerprint,kind,remote_session_ref,status,last_progress_at,"
                "deadline_at,created_at,updated_at) SELECT work_session_id,:key,:number,"
                "'other-request-key',request_digest,local_session_ref,runtime_instance_id,"
                "instance_fingerprint,kind,remote_session_ref,'prepared',last_progress_at,"
                ":deadline,created_at,updated_at FROM runtime_attempts WHERE id=:id"
            ),
            {"key": request_key, "number": number, "deadline": value, "id": recorder.attempt_id},
        )
    db.rollback()
    assert list(db.scalars(select(RuntimeAttempt.id))) == [recorder.attempt_id]


PROCESS = """
import os, sys, json
from datetime import datetime, timedelta
from sqlalchemy import select
from app.core.database import SessionLocal
from app.models.project import WorkSession
from app.models.runtime_execution import RuntimeAttempt
from app.runtimes.base import RuntimeSession
from app.runtimes.container_common import resolve_container_instance
from app.work import telemetry
deadline = datetime.fromisoformat(sys.argv[2])
with SessionLocal() as db:
    work = db.get(WorkSession, int(sys.argv[1]))
    session = RuntimeSession(id=work.runtime_session_ref, task_id=work.task_id,
        instance=resolve_container_instance(work.employee_id, work.runtime_type))
recorder = telemetry.prepare_attempt(int(sys.argv[1]), session, "private prompt",
    request_key="worker", kind="controlled", deadline_at=deadline)
if sys.argv[3] == "crash":
    os._exit(23)
with SessionLocal() as db:
    saved = db.get(RuntimeAttempt, recorder.attempt_id).deadline_at
telemetry.utcnow = lambda: datetime.fromisoformat(sys.argv[4])
try:
    remaining = recorder.remaining_timeout(900)
except TimeoutError:
    remaining = "expired"
print(json.dumps({"deadline": saved.isoformat(), "remaining": remaining}))
"""


def test_process_death_after_prepare_never_resets_the_original_deadline(db, bound):
    deadline = utcnow() + timedelta(seconds=30)

    def child(mode, proposed, observed):
        return subprocess.run(
            [
                sys.executable,
                "-c",
                PROCESS,
                str(bound.work.id),
                proposed.isoformat(),
                mode,
                observed.isoformat(),
            ],
            text=True,
            capture_output=True,
            timeout=20,
        )

    crashed = child("crash", deadline, deadline)
    assert crashed.returncode == 23, crashed.stderr
    db.expire_all()
    row = db.scalar(select(RuntimeAttempt))
    assert row.status == "prepared" and row.remote_run_id is None
    assert as_utc(row.deadline_at) == deadline
    for observed, expected in (
        (deadline - timedelta(seconds=5), 5),
        (deadline + timedelta(seconds=1), "expired"),
    ):
        resumed = child("read", deadline + timedelta(seconds=900), observed)
        assert resumed.returncode == 0, resumed.stderr
        assert json.loads(resumed.stdout) == {
            "deadline": row.deadline_at.isoformat(),
            "remaining": expected,
        }
    db.expire_all()
    assert list(db.scalars(select(RuntimeAttempt.id))) == [row.id]
