"""/events + /settings. (Runtime management moved to api/v1/runtimes.py in v0.2.)"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.repositories import events as event_repo
from app.schemas.knowledge import EventOut
from app.schemas.misc import SettingsOut

router = APIRouter(tags=["meta"])


@router.get("/events", response_model=list[EventOut])
def list_events(limit: int = 100, db: Session = Depends(get_db)) -> list[EventOut]:
    return [EventOut.model_validate(e) for e in event_repo.list_events(db, limit=limit)]


@router.get("/settings", response_model=SettingsOut)
def get_settings_echo() -> SettingsOut:
    return SettingsOut(
        runtime_mode=settings.runtime_mode,
        workspace_root=settings.workspace_root,
        api_host=settings.api_host,
        api_port=settings.api_port,
        web_port=settings.web_port,
        company_name=settings.company_name,
    )
