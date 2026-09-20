"""Canonical revision bytes survive edits; unknown history never borrows current text."""

import hashlib
import os
import stat
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.drive import DriveNode, DriveRevision
from app.repositories import drive as drive_repo
from app.services import drive

BODY = "# Original\r\n逐字交接\r\n"


def create(db, *, preserved=True, content=BODY):
    parent = drive_repo.get_node_by_path(db, "drive/knowledge")
    fields = {"preserve_revision": True} if preserved else {}
    node = drive.create_document(
        db, parent=parent, name="Original bytes", content=content, **fields
    )
    db.commit()
    return node, drive_repo.get_revision(db, node.id, 1)


def snapshot_path(revision):
    return Path(settings.data_root) / revision.content_path


def test_snapshot_retains_exact_bytes_after_edit_and_process_restart(db):
    node, revision = create(db)
    revision_id, node_id = revision.id, node.id
    assert snapshot_path(revision).read_bytes() == BODY.encode()
    assert revision.sha256 == hashlib.sha256(BODY.encode()).hexdigest()
    drive.update_document(db, node, content="A later edit\n")
    with SessionLocal() as fresh:
        old = fresh.get(DriveRevision, revision_id)
        current = fresh.get(DriveNode, node_id)
        assert current.current_version == 2
        assert drive.read_content(current) == "A later edit\n"
        assert drive.read_revision_content(current, old) == BODY


def test_old_revision_without_snapshot_remains_unknown(db):
    node, revision = create(db, preserved=False)
    drive.update_document(db, node, content="Current content is not old history")
    assert drive.read_revision_content(node, revision) is None


@pytest.mark.parametrize("damage", ["missing", "changed", "symlink", "hardlink", "oversize"])
def test_unavailable_or_modified_snapshot_never_falls_back_to_mutable_document(db, damage):
    from app.services.drive_revisions import RevisionContentError

    node, revision = create(db)
    path = snapshot_path(revision)
    if damage == "missing":
        path.unlink()
    elif damage == "changed":
        path.write_bytes(b"tampered")
    elif damage == "symlink":
        path.unlink()
        path.symlink_to(drive.abs_path(node))
    elif damage == "hardlink":
        os.link(path, path.with_suffix(".linked"))
    else:
        with path.open("wb") as stream:
            stream.truncate(256 * 1024 + 1)
    with pytest.raises(RevisionContentError):
        drive.read_revision_content(node, revision)


def test_reader_rejects_other_node_and_forged_path(db, tmp_path):
    from app.services.drive_revisions import RevisionContentError

    node, revision = create(db)
    other, _ = create(db)
    with pytest.raises(RevisionContentError):
        drive.read_revision_content(other, revision)
    outside = tmp_path / "outside.txt"
    outside.write_text(BODY)
    db.expunge(revision)
    revision.content_path = str(outside)
    with pytest.raises(RevisionContentError):
        drive.read_revision_content(node, revision)


@pytest.mark.parametrize("component", [".revisions", "node"])
def test_snapshot_parent_symlinks_are_not_followed(db, component):
    from app.services.drive_revisions import RevisionContentError

    node, revision = create(db)
    root = snapshot_path(revision).parent
    if component == ".revisions":
        root = root.parent
    original = root.with_name(root.name + "-original")
    root.rename(original)
    root.symlink_to(original, target_is_directory=True)
    with pytest.raises(RevisionContentError):
        drive.read_revision_content(node, revision)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE drive_revisions SET content_path=NULL WHERE id=:id",
        "UPDATE drive_revisions SET sha256=:sha WHERE id=:id",
        "UPDATE drive_revisions SET version=2 WHERE id=:id",
        "UPDATE drive_revisions SET author_person_id=9876 WHERE id=:id",
        "DELETE FROM drive_revisions WHERE id=:id",
        "INSERT OR REPLACE INTO drive_revisions (id,node_id,version,sha256,created_at) "
        "SELECT id,node_id,version,sha256,created_at FROM drive_revisions WHERE id=:id",
    ],
)
def test_saved_revision_identity_cannot_be_rewritten_or_deleted(db, statement):
    _, revision = create(db)
    with pytest.raises(IntegrityError, match="revision content"):
        db.execute(text(statement), {"id": revision.id, "sha": "a" * 64})
    db.rollback()


def test_existing_unknown_revision_cannot_be_backfilled(db):
    _, revision = create(db, preserved=False)
    with pytest.raises(IntegrityError, match="revision content"):
        db.execute(
            text("UPDATE drive_revisions SET content_path='drive/.revisions/fake' WHERE id=:id"),
            {"id": revision.id},
        )
    db.rollback()


def test_duplicate_version_cannot_shadow_a_preserved_revision(db):
    node, _ = create(db)
    with pytest.raises(IntegrityError, match="revision content"):
        drive_repo.create_revision(db, node_id=node.id, version=1, sha256="b" * 64)
    db.rollback()


def test_rollback_has_no_visible_revision_and_retry_reuses_verified_bytes(db):
    parent = drive_repo.get_node_by_path(db, "drive/knowledge")
    fields = dict(parent=parent, name="Rollback", content=BODY, preserve_revision=True)
    parent_id = parent.id
    node = drive.create_document(db, **fields)
    revision = drive_repo.get_revision(db, node.id, 1)
    path, node_id = snapshot_path(revision), node.id
    inode = path.stat().st_ino
    db.rollback()
    with SessionLocal() as fresh:
        assert fresh.get(DriveNode, node_id) is None
        assert not list(
            fresh.scalars(select(DriveRevision).where(DriveRevision.node_id == node_id))
        )
    with SessionLocal() as fresh:
        retried = drive.create_document(
            fresh, **{**fields, "parent": fresh.get(DriveNode, parent_id)}
        )
        fresh.commit()
        saved = drive_repo.get_revision(fresh, retried.id, 1)
        assert snapshot_path(saved) == path and path.stat().st_ino == inode
        assert drive.read_revision_content(retried, saved) == BODY
    assert not list(path.parent.glob(".pending-*"))


def test_fsync_failure_never_publishes_revision_or_leaves_temporary_file(db, monkeypatch):
    from app.services import drive_revisions

    original = os.fsync

    def fail(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("injected disk failure")
        original(fd)

    monkeypatch.setattr(drive_revisions.os, "fsync", fail)
    before = list(db.scalars(select(DriveRevision.id)))
    with pytest.raises(drive_revisions.RevisionContentError):
        create(db)
    db.rollback()
    assert list(db.scalars(select(DriveRevision.id))) == before
    root = Path(settings.data_root) / "drive" / ".revisions"
    assert not list(root.rglob(".pending-*"))


def test_revision_size_limit_is_checked_before_writing(db):
    from app.services.drive_revisions import RevisionContentError

    with pytest.raises(RevisionContentError, match="revision content"):
        create(db, content="x" * (256 * 1024 + 1))
    db.rollback()
    assert not list((Path(settings.data_root) / "drive" / ".revisions").rglob("*.txt"))


def test_retry_refuses_to_overwrite_existing_damaged_revision_file(db):
    from app.services.drive_revisions import RevisionContentError, preserve_content

    node, revision = create(db)
    path = snapshot_path(revision)
    path.write_bytes(b"damaged")
    inode = path.stat().st_ino
    with pytest.raises(RevisionContentError):
        preserve_content(node.id, 1, BODY)
    assert path.read_bytes() == b"damaged" and path.stat().st_ino == inode
    assert not list(path.parent.glob(".pending-*"))


def test_artifact_service_preserves_canonical_revision_and_ownership(db, default_company_id):
    from app.models.project import Project, Task
    from app.services.artifacts import record_project_artifact

    project = Project(company_id=default_company_id, name="Canonical bytes")
    db.add(project)
    db.flush()
    task = Task(project_id=project.id, title="Build", kind="development")
    db.add(task)
    db.flush()
    node = record_project_artifact(
        db,
        project,
        artifact_type="source_code",
        title="Canonical",
        content=BODY,
        task_id=task.id,
        preserve_revision=True,
    )
    db.commit()
    revision = drive_repo.get_revision(db, node.id, 1)
    assert node.company_id == default_company_id and node.task_id == task.id
    assert drive.read_revision_content(node, revision) == BODY
