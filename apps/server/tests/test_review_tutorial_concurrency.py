"""Concurrent first reads must reuse one tutorial progress row."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.core.request_context import RequestIdentity, reset_request_identity, set_request_identity
from app.models.auth import User
from app.models.organization import Company
from app.models.project_delivery import TutorialProgress
from app.repositories import project_delivery as delivery_repo
from app.services import tutorial
from app.tutorials import PRACTICE_TUTORIAL_ID


def test_concurrent_first_progress_reads_return_the_same_persisted_row(db, monkeypatch):
    suffix = uuid4().hex
    user = User(email=f"tutorial-{suffix}@example.com", username=suffix, password_hash="unused")
    company = Company(name="Concurrent tutorial", slug=suffix)
    db.add_all([user, company])
    db.commit()
    user_id, company_id = int(user.id), int(company.id)
    barrier = Barrier(2)
    original_get = delivery_repo.get_tutorial

    def simultaneous_missing_read(session, selected_user_id, tutorial_id):
        row = original_get(session, selected_user_id, tutorial_id)
        if selected_user_id == user_id and row is None:
            barrier.wait(timeout=5)
        return row

    monkeypatch.setattr(delivery_repo, "get_tutorial", simultaneous_missing_read)

    def read_progress():
        token = set_request_identity(
            RequestIdentity(
                user_id=user_id, company_id=company_id, session_id=1, membership_role="OWNER"
            )
        )
        try:
            with SessionLocal() as session:
                return tutorial.get_progress(session, PRACTICE_TUTORIAL_ID)
        finally:
            reset_request_identity(token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(read_progress) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]

    assert results[0].id == results[1].id
    assert all(result.status == "not_started" for result in results)
    assert all(result.company_id == company_id for result in results)
    assert (
        db.scalar(
            select(func.count(TutorialProgress.id)).where(TutorialProgress.user_id == user_id)
        )
        == 1
    )


def test_duplicate_initialization_preserves_progress_and_outer_transaction(db, default_company_id):
    suffix = uuid4().hex
    user = User(email=f"tutorial-{suffix}@example.com", username=suffix, password_hash="unused")
    db.add(user)
    db.flush()
    existing = delivery_repo.create_tutorial(
        db,
        user_id=user.id,
        company_id=default_company_id,
        tutorial_id=PRACTICE_TUTORIAL_ID,
        status="paused",
        current_step="retained-step",
        context={"project_id": 123},
    )
    db.commit()
    user.display_name = "Keep this unrelated update"

    result = delivery_repo.create_tutorial_if_missing(
        db,
        user_id=user.id,
        company_id=default_company_id,
        tutorial_id=PRACTICE_TUTORIAL_ID,
        status="not_started",
        current_step="create_project",
        context={},
    )
    db.commit()
    db.refresh(user)
    db.refresh(result)

    assert result.id == existing.id
    assert result.status == "paused"
    assert result.current_step == "retained-step"
    assert result.context == {"project_id": 123}
    assert user.display_name == "Keep this unrelated update"


def test_initialization_reraises_integrity_errors_without_a_winning_row(db):
    with pytest.raises(IntegrityError):
        delivery_repo.create_tutorial_if_missing(
            db, user_id=-991, company_id=None, tutorial_id=PRACTICE_TUTORIAL_ID
        )
    assert (
        db.scalar(select(func.count(TutorialProgress.id)).where(TutorialProgress.user_id == -991))
        == 0
    )
