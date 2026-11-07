"""Request-scoped dependencies: a database session, the signed-in user, and the
company the request is about (which the user must belong to)."""

from collections.abc import Iterator
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from eidolon.api.auth import AuthService, SessionRow
from eidolon.identity import EmploymentRow, PersonRow, UserRow


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.session_factory()
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def auth_service(request: Request) -> AuthService:
    return request.app.state.auth


@dataclass(frozen=True, slots=True)
class CurrentUser:
    user: UserRow
    session: SessionRow
    person_ids: tuple[str, ...]  # the user's human persons (platform-wide)


def current_user(
    request: Request, db: Session = Depends(get_db), auth: AuthService = Depends(auth_service)
) -> CurrentUser:
    token = request.cookies.get(request.app.state.cookie_name)
    session = auth.authenticate(db, token)
    if session is None:
        raise HTTPException(401, "not signed in")
    user = db.get(UserRow, session.user_id)
    if user is None:
        raise HTTPException(401, "account not projected yet; retry shortly")
    persons = tuple(
        db.scalars(select(PersonRow.person_id).where(PersonRow.user_id == user.user_id))
    )
    return CurrentUser(user, session, persons)


def memberships(db: Session, me: CurrentUser) -> list[EmploymentRow]:
    """The user's active employments across companies."""
    if not me.person_ids:
        return []
    return list(
        db.scalars(
            select(EmploymentRow)
            .where(EmploymentRow.person_id.in_(me.person_ids), EmploymentRow.status == "active")
            .order_by(EmploymentRow.started_at)
        )
    )


def member_of(
    company_id: UUID, db: Session = Depends(get_db), me: CurrentUser = Depends(current_user)
) -> EmploymentRow:
    """The employment that lets the user read this company; 403 otherwise."""
    for e in memberships(db, me):
        if e.company_id == company_id:
            return e
    raise HTTPException(403, "you are not employed by this company")


__all__ = [
    "CurrentUser",
    "auth_service",
    "current_user",
    "get_db",
    "member_of",
    "memberships",
]
