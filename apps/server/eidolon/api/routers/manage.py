"""Commands on companies: found one, adopt / customize its institution, hire and
fire. All are a human's own acts, allowed by their grants in the institution."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from eidolon.api.commands import Commands, Conflict, DomainError, Forbidden, idem, person_in
from eidolon.api.deps import CurrentUser, current_user, get_db, member_of
from eidolon.api.secrets import ProviderCredentials, SecretsError
from eidolon.identity import EmploymentRow

router = APIRouter(tags=["manage"])
commands = Commands()


def guard(exc: Exception) -> HTTPException:
    if isinstance(exc, Forbidden):
        return HTTPException(403, str(exc))
    if isinstance(exc, Conflict):
        return HTTPException(409, str(exc))
    return HTTPException(422, str(exc))


class CompanyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=64)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    owner_person_id: str | None = None  # default: the user's first person
    template_id: str | None = "software-studio-v1"


class AdoptIn(BaseModel):
    template_id: str


class CustomizeIn(BaseModel):
    changes: dict[str, Any]
    reason: str = Field(min_length=1, max_length=500)


class HireIn(BaseModel):
    person_id: str = Field(min_length=1, max_length=200)
    role: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    department_id: str | None = Field(default=None, max_length=200)
    persona: dict[str, Any] | None = None


class FireIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


@router.post("/companies", status_code=201)
def create_company(
    body: CompanyIn,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    person_id = body.owner_person_id or (me.person_ids[0] if me.person_ids else None)
    if person_id is None or person_id not in me.person_ids:
        raise HTTPException(422, "owner_person_id must be one of your persons")
    try:
        company_id = commands.create_company(
            db,
            owner_user_id=me.user.user_id,
            owner_person_id=person_id,
            name=body.name,
            slug=body.slug,
            currency=body.currency,
            template_id=body.template_id,
            company_id=idem(me.user.user_id, idempotency_key),
        )
    except (Forbidden, Conflict, *DomainError) as exc:
        raise guard(exc) from exc
    return {"company_id": str(company_id), "owner_person_id": person_id}


@router.post("/companies/{company_id}/institution/adopt", status_code=201)
def adopt_institution(
    company_id: UUID,
    body: AdoptIn,
    db: Session = Depends(get_db),
    me: CurrentUser = Depends(current_user),
    employment: EmploymentRow = Depends(member_of),
):
    try:
        fact = commands.adopt(
            db, person_in(db, employment), body.template_id, owner_user_id=me.user.user_id
        )
    except (Forbidden, Conflict, *DomainError) as exc:
        raise guard(exc) from exc
    return {"fact_id": str(fact.id), "template_id": body.template_id}


@router.post("/companies/{company_id}/institution/customize", status_code=201)
def customize_institution(
    company_id: UUID,
    body: CustomizeIn,
    db: Session = Depends(get_db),
    employment: EmploymentRow = Depends(member_of),
):
    try:
        fact = commands.customize(db, person_in(db, employment), body.changes, reason=body.reason)
    except (Forbidden, Conflict, *DomainError) as exc:
        raise guard(exc) from exc
    return {"fact_id": str(fact.id), "version": fact.payload.get("version")}


@router.post("/companies/{company_id}/employees", status_code=201)
def hire(
    company_id: UUID,
    body: HireIn,
    db: Session = Depends(get_db),
    employment: EmploymentRow = Depends(member_of),
):
    try:
        fact = commands.hire(
            db,
            person_in(db, employment),
            person_id=body.person_id,
            role=body.role,
            name=body.name,
            persona=body.persona,
            title=body.title,
            department_id=body.department_id,
        )
    except (Forbidden, Conflict, *DomainError) as exc:
        raise guard(exc) from exc
    return {"employment_id": fact.payload["employment_id"], "person_id": body.person_id}


@router.post("/companies/{company_id}/employments/{employment_id}/end", status_code=200)
def fire(
    company_id: UUID,
    employment_id: UUID,
    body: FireIn,
    db: Session = Depends(get_db),
    employment: EmploymentRow = Depends(member_of),
):
    try:
        fact = commands.fire(
            db, person_in(db, employment), employment_id=employment_id, reason=body.reason
        )
    except (Forbidden, Conflict, *DomainError) as exc:
        raise guard(exc) from exc
    return {"fact_id": str(fact.id), "employment_id": str(employment_id)}


# -- provider credentials (R6-d) --------------------------------------------------------------


class ProviderIn(BaseModel):
    kind: str = Field(pattern="^(openai_compatible|anthropic)$")
    api_key: str = Field(min_length=8, max_length=500)
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)


def _provider_access(db: Session, employment: EmploymentRow) -> None:
    try:
        commands.require(db, person_in(db, employment), "provider.manage")
    except (Forbidden, Conflict) as exc:
        raise guard(exc) from exc


@router.get("/companies/{company_id}/provider")
def get_provider(
    company_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    employment: EmploymentRow = Depends(member_of),
):
    _provider_access(db, employment)
    row = request.app.state.credentials.get(db, company_id)
    return {"configured": row is not None, **(ProviderCredentials.masked(row) if row else {})}


@router.put("/companies/{company_id}/provider")
def set_provider(
    company_id: UUID,
    body: ProviderIn,
    request: Request,
    db: Session = Depends(get_db),
    employment: EmploymentRow = Depends(member_of),
):
    _provider_access(db, employment)
    try:
        row = request.app.state.credentials.set(
            db,
            company_id,
            kind=body.kind,
            api_key=body.api_key,
            base_url=body.base_url,
            model=body.model,
            by=employment.person_id,
        )
    except SecretsError as exc:
        raise HTTPException(409 if "SECRET_KEY" in str(exc) else 422, str(exc)) from exc
    return {"configured": True, **ProviderCredentials.masked(row)}


@router.delete("/companies/{company_id}/provider", status_code=204)
def clear_provider(
    company_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    employment: EmploymentRow = Depends(member_of),
):
    _provider_access(db, employment)
    request.app.state.credentials.clear(db, company_id)
    return Response(status_code=204)
