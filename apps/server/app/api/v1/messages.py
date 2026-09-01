"""/messages"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.schemas.project import MessageCreate, MessageOut

router = APIRouter(prefix="/messages", tags=["messages"])


@router.get("", response_model=list[MessageOut])
def list_messages(project_id: int | None = None, db: Session = Depends(get_db)) -> list[MessageOut]:
    return [MessageOut.model_validate(m) for m in project_repo.list_messages(db, project_id)]


@router.post("", response_model=MessageOut, status_code=201)
def create_message(payload: MessageCreate, db: Session = Depends(get_db)) -> MessageOut:
    company = org_repo.get_default_company(db)
    if company is None:
        raise HTTPException(status_code=409, detail="no company seeded")
    if org_repo.get_employee(db, payload.sender_id) is None:
        raise HTTPException(status_code=404, detail="sender not found")
    message = project_repo.create_message(db, company_id=company.id, **payload.model_dump())
    db.commit()
    db.refresh(message)
    return MessageOut.model_validate(message)
