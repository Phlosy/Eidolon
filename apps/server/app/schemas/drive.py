"""Drive API schemas (v0.3). Frozen contract — see docs/design-v0.3-workspace.md §4."""

from datetime import datetime

from pydantic import BaseModel

from app.models.enums import DriveZone
from app.schemas.organization import ORMModel


class DriveNodeOut(ORMModel):
    id: int
    parent_id: int | None
    kind: str  # folder | document
    name: str
    path: str  # relative to the data root
    zone: str  # projects | knowledge | skills | handbook
    project_id: int | None
    doc_type: str | None
    owner_employee_id: int | None
    current_version: int
    created_at: datetime
    updated_at: datetime


class DriveCollaboratorOut(BaseModel):
    employee_id: int
    role: str  # viewer | editor


class DriveNodeDetail(DriveNodeOut):
    content: str | None = None  # documents only
    collaborators: list[DriveCollaboratorOut] = []


class DriveRevisionOut(BaseModel):
    version: int
    sha256: str
    author_employee_id: int | None
    message: str | None
    created_at: datetime


class DriveNodePatch(BaseModel):
    content: str
    message: str | None = None


class DriveFolderCreate(BaseModel):
    zone: DriveZone
    parent_id: int | None = None
    name: str
    project_id: int | None = None
