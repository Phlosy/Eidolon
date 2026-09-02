"""/git (v0.3 phase 2).

GET /git is the aggregate read: builtin Gitea status + all external
connections. Connections are external self-hosted platforms Eidolon connects
to (never installs); tokens are write-only and only ever returned as masks.
The builtin Gitea is optional and installed manually via /git/builtin/install
(background task; frontend polls GET /git for progress).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.git import (
    GitBuiltinActionOut,
    GitConnectionCreate,
    GitConnectionOut,
    GitConnectionPatch,
    GitConnectionTestOut,
    GitOverviewOut,
)
from app.services.git import git_service

router = APIRouter(prefix="/git", tags=["git"])


@router.get("", response_model=GitOverviewOut)
def git_overview(db: Session = Depends(get_db)) -> GitOverviewOut:
    return GitOverviewOut(
        builtin=git_service.builtin_status(),
        connections=git_service.list_connections(db),
    )


@router.post("/connections", response_model=GitConnectionOut, status_code=201)
def create_connection(
    payload: GitConnectionCreate, db: Session = Depends(get_db)
) -> GitConnectionOut:
    return git_service.create_connection(db, payload)


@router.patch("/connections/{connection_id}", response_model=GitConnectionOut)
def update_connection(
    connection_id: int, payload: GitConnectionPatch, db: Session = Depends(get_db)
) -> GitConnectionOut:
    return git_service.update_connection(db, connection_id, payload)


@router.delete("/connections/{connection_id}", status_code=204)
def delete_connection(connection_id: int, db: Session = Depends(get_db)) -> None:
    git_service.delete_connection(db, connection_id)


@router.post("/connections/{connection_id}/test", response_model=GitConnectionTestOut)
async def test_connection(
    connection_id: int, db: Session = Depends(get_db)
) -> GitConnectionTestOut:
    return await git_service.test_connection(db, connection_id)


@router.post("/builtin/install", response_model=GitBuiltinActionOut)
async def install_builtin() -> GitBuiltinActionOut:
    if not git_service.docker_available():
        raise HTTPException(status_code=409, detail="docker daemon is not available")
    status = git_service.request_install()
    return GitBuiltinActionOut(status=status.status)


@router.post("/builtin/start", response_model=GitBuiltinActionOut)
async def start_builtin() -> GitBuiltinActionOut:
    status = await git_service.start_builtin()
    return GitBuiltinActionOut(status=status.status)


@router.post("/builtin/stop", response_model=GitBuiltinActionOut)
async def stop_builtin() -> GitBuiltinActionOut:
    status = await git_service.stop_builtin()
    return GitBuiltinActionOut(status=status.status)
