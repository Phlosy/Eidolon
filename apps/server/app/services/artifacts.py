"""Artifact materialization (v0.2): every artifact is also a real file on disk.

Files live under ``{data_root}/projects/{project_id}/{docs|source|tests|release}/``
(directory by artifact type) and are named ``{type}-{title-slug}.md``. The DB
row records the path, the sha256 of the content, and the producing work
session. The ``content`` column stays the source of truth for the API.
"""

import hashlib
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import ArtifactType
from app.models.project import Artifact

TYPE_TO_DIR = {
    ArtifactType.prd.value: "docs",
    ArtifactType.research_report.value: "docs",
    ArtifactType.architecture.value: "docs",
    ArtifactType.readme.value: "docs",
    ArtifactType.plan.value: "docs",
    ArtifactType.other.value: "docs",
    ArtifactType.source_code.value: "source",
    ArtifactType.test_report.value: "tests",
    ArtifactType.release.value: "release",
}


def _slugify(text: str, fallback: str = "artifact") -> str:
    slug = re.sub(r"[^0-9A-Za-z一-鿿]+", "-", text.lower()).strip("-")
    return slug[:60] or fallback


def artifact_dir(project_id: int, artifact_type: str) -> Path:
    return (
        Path(settings.data_root)
        / "projects"
        / str(project_id)
        / TYPE_TO_DIR.get(artifact_type, "docs")
    )


def materialize_artifact(
    db: Session, artifact: Artifact, work_session_id: int | None = None
) -> Artifact:
    """Write the artifact content to disk; set path/sha256/work_session_id."""
    directory = artifact_dir(artifact.project_id, artifact.type)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{artifact.type}-{_slugify(artifact.title)}.md"
    path = directory / filename
    path.write_text(artifact.content, encoding="utf-8")

    artifact.path = str(path)
    artifact.sha256 = hashlib.sha256(artifact.content.encode("utf-8")).hexdigest()
    if work_session_id is not None:
        artifact.work_session_id = work_session_id
    db.flush()
    return artifact
