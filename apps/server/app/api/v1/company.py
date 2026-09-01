"""/company"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories import organization as org_repo
from app.schemas.organization import CompanyOut

router = APIRouter(tags=["company"])


@router.get("/company", response_model=CompanyOut)
def get_company(db: Session = Depends(get_db)) -> CompanyOut:
    company = org_repo.get_default_company(db)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    return CompanyOut.model_validate(company)
