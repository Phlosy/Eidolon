"""Startup data migration (v0.3): legacy ``artifacts`` rows → drive_nodes.

Files move from ``data/projects/{id}/...`` to
``data/drive/projects/{slug}/{docs|source|tests|release}/...``. The source tree
is backed up to ``data/projects.bak`` before anything moves. The migration is
idempotent: each migrated artifact leaves a revision marker
(``migrated from artifact #N``) and is skipped on later boots. The deprecated
``artifacts`` table itself is left untouched (dropped in a later version).
"""

import hashlib
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.drive import DriveNode, DriveRevision
from app.models.enums import DriveNodeKind, DriveZone
from app.models.project import Artifact, Project
from app.services import drive as drive_service

logger = get_logger(__name__)


def _marker(artifact_id: int) -> str:
    return f"migrated from artifact #{artifact_id}"


def _already_migrated(db: Session, artifact_id: int) -> bool:
    return (
        db.scalars(
            select(DriveRevision.id).where(DriveRevision.message == _marker(artifact_id))
        ).first()
        is not None
    )


def migrate_artifacts_to_drive(db: Session) -> None:
    artifacts = list(db.scalars(select(Artifact).order_by(Artifact.id)))
    pending = [a for a in artifacts if not _already_migrated(db, a.id)]
    if not pending:
        return

    data_root = Path(settings.data_root)
    src_root = data_root / "projects"
    bak_root = data_root / "projects.bak"
    if src_root.exists() and not bak_root.exists():
        shutil.copytree(src_root, bak_root)
        logger.info("drive migration: backed up %s -> %s", src_root, bak_root)

    migrated = 0
    for artifact in pending:
        project = db.get(Project, artifact.project_id)
        if project is None:
            logger.warning(
                "drive migration: artifact %s has no project %s, skipped",
                artifact.id,
                artifact.project_id,
            )
            continue
        folders = drive_service.ensure_project_folders(db, project)
        parent = folders[drive_service.TYPE_TO_DIR.get(artifact.type, "docs")]
        if artifact.path:
            filename = Path(artifact.path).name
        else:
            filename = f"{artifact.type}-{drive_service.slugify(artifact.title)}.md"
        path = f"{parent.path}/{filename}"
        if db.scalars(select(DriveNode.id).where(DriveNode.path == path)).first() is not None:
            path = drive_service._unique_path(db, path)

        content = artifact.content or ""
        src = Path(artifact.path) if artifact.path else None
        target = data_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if src is not None and src.exists():
            shutil.move(str(src), target)
            content = target.read_text(encoding="utf-8")
        else:
            target.write_text(content, encoding="utf-8")

        node = DriveNode(
            parent_id=parent.id,
            kind=DriveNodeKind.document.value,
            name=artifact.title,
            path=path,
            zone=DriveZone.projects.value,
            project_id=project.id,
            doc_type=artifact.type,
            owner_employee_id=artifact.author_id,
            current_version=artifact.version or 1,
            work_session_id=artifact.work_session_id,
            created_at=artifact.created_at,
            updated_at=artifact.updated_at,
        )
        db.add(node)
        db.flush()
        db.add(
            DriveRevision(
                node_id=node.id,
                version=node.current_version,
                sha256=artifact.sha256 or hashlib.sha256(content.encode("utf-8")).hexdigest(),
                author_employee_id=artifact.author_id,
                message=_marker(artifact.id),
                created_at=artifact.created_at,
            )
        )
        migrated += 1

    db.commit()
    logger.info("drive migration: migrated %s artifact(s) into drive_nodes", migrated)
