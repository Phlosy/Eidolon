"""Authentication dependencies and request-local tenant scoping."""

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.request_context import RequestIdentity, reset_request_identity, set_request_identity
from app.models.auth import CompanyMembership, User, UserSession
from app.models.organization import Company
from app.services import auth as auth_service


@dataclass(frozen=True)
class CurrentAuth:
    user: User
    session: UserSession
    membership: CompanyMembership
    company: Company


def current_auth(request: Request, db: Session = Depends(get_db)) -> CurrentAuth:
    resolved = auth_service.session_from_token(
        db, request.cookies.get(settings.session_cookie_name)
    )
    if resolved is None:
        raise HTTPException(status_code=401, detail="authentication required")
    session, user = resolved
    membership, company = auth_service.primary_company(db, user.id)
    return CurrentAuth(user=user, session=session, membership=membership, company=company)


async def require_user(
    request: Request, db: Session = Depends(get_db)
) -> Iterator[CurrentAuth | None]:
    token = request.cookies.get(settings.session_cookie_name)
    if not token and not settings.auth_required:
        yield None
        return
    auth = current_auth(request, db)
    context_token = set_request_identity(
        RequestIdentity(
            user_id=auth.user.id,
            company_id=auth.company.id,
            membership_role=auth.membership.role,
            session_id=auth.session.id,
        )
    )
    try:
        yield auth
    finally:
        reset_request_identity(context_token)
