"""/drive (v0.3). Protected company-scoped documents and folders."""

import mimetypes

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.enums import DriveZone
from app.repositories import drive as drive_repo
from app.schemas.drive import (
    CreateDocumentRequest,
    DriveCollaboratorOut,
    DriveFolderCreate,
    DriveNodeDetail,
    DriveNodeOut,
    DriveNodePatch,
    DriveRevisionOut,
)
from app.services import drive as drive_service

router = APIRouter(prefix="/drive", tags=["drive"])
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _get_node_or_404(db: Session, node_id: int):
    node = drive_repo.get_node(db, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="drive node not found")
    return node


@router.get("/tree", response_model=list[DriveNodeOut])
def get_tree(zone: str | None = Query(default=None), db: Session = Depends(get_db)):
    """Flat node list (optionally per zone); the frontend builds the tree."""
    return [DriveNodeOut.model_validate(n) for n in drive_repo.list_nodes(db, zone=zone)]


@router.post("/documents", response_model=DriveNodeOut, status_code=201)
def create_document(
    payload: CreateDocumentRequest,
    db: Session = Depends(get_db),
):
    """原生「新建文档」（Markdown）—— 上传导入只是补充，不是唯一途径。"""
    node = drive_service.create_markdown_document(
        db,
        zone=payload.zone,
        name=payload.name,
        content=payload.content,
        parent_id=payload.parent_id,
        project_id=payload.project_id,
        actor_employee_id=payload.employee_id,
    )
    return DriveNodeOut.model_validate(node)


@router.post("/folders", response_model=DriveNodeOut, status_code=201)
def create_folder(
    payload: DriveFolderCreate,
    employee_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    node = drive_service.create_folder(
        db,
        zone=payload.zone.value,
        name=payload.name,
        parent_id=payload.parent_id,
        project_id=payload.project_id,
        actor_employee_id=employee_id,
    )
    return DriveNodeOut.model_validate(node)


@router.post("/files", response_model=DriveNodeOut, status_code=201)
async def upload_file(
    request: Request,
    zone: DriveZone = Query(),
    name: str = Query(min_length=1, max_length=255),
    parent_id: int | None = Query(default=None),
    project_id: int | None = Query(default=None),
    employee_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Upload a Markdown, DOCX, or PDF body without requiring multipart support."""
    declared_size = request.headers.get("content-length")
    if declared_size and int(declared_size) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file exceeds the 20 MB upload limit")
    content = await request.body()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file exceeds the 20 MB upload limit")
    node = drive_service.create_uploaded_file(
        db,
        zone=zone.value,
        name=name,
        content=content,
        parent_id=parent_id,
        project_id=project_id,
        actor_employee_id=employee_id,
    )
    return DriveNodeOut.model_validate(node)


@router.get("/nodes/{node_id}", response_model=DriveNodeDetail)
def get_node(node_id: int, db: Session = Depends(get_db)):
    node = _get_node_or_404(db, node_id)
    return DriveNodeDetail(
        **DriveNodeOut.model_validate(node).model_dump(),
        content=drive_service.read_content(node),
        collaborators=[
            DriveCollaboratorOut(employee_id=c.employee_id, role=c.role)
            for c in drive_repo.list_collaborators(db, node.id)
        ],
    )


@router.get("/nodes/{node_id}/content")
def get_node_content(node_id: int, db: Session = Depends(get_db)):
    node = _get_node_or_404(db, node_id)
    if node.kind != "document":
        raise HTTPException(status_code=400, detail="folders have no file content")
    path = drive_service.abs_path(node)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="document content not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/nodes/{node_id}/revisions", response_model=list[DriveRevisionOut])
def get_revisions(node_id: int, db: Session = Depends(get_db)):
    node = _get_node_or_404(db, node_id)
    return [
        DriveRevisionOut(
            version=r.version,
            sha256=r.sha256,
            author_employee_id=r.author_employee_id,
            message=r.message,
            created_at=r.created_at,
        )
        for r in drive_repo.list_revisions(db, node.id)
    ]


@router.patch("/nodes/{node_id}", response_model=DriveNodeOut)
def patch_node(
    node_id: int,
    payload: DriveNodePatch,
    employee_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    node = _get_node_or_404(db, node_id)
    node = drive_service.update_document(
        db, node, content=payload.content, message=payload.message, actor_employee_id=employee_id
    )
    return DriveNodeOut.model_validate(node)
