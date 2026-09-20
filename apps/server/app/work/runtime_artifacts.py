"""Host-owned artifact intent, attached to original attempts.

Adapters only transport evidence. This module binds that evidence to immutable
execution facts; it never infers an old workspace from today's runtime settings.
"""

from dataclasses import asdict

from app.models.enums import ArtifactType
from app.runtimes.base import ArtifactWorkspace, RuntimeProtocolError
from app.runtimes.workspace_artifacts import capture_workspace

_TYPES = {kind.value for kind in ArtifactType}


def capture_context(work, session, runtime_row):
    if work.runtime_type == "mock":
        return None
    if runtime_row is None or runtime_row.workspace_path != session.instance.home_path:
        raise RuntimeProtocolError("artifact_workspace_changed")
    workspace = capture_workspace(runtime_row.workspace_path)
    snapshot = work.provenance_snapshot_json
    task = snapshot.get("task") if isinstance(snapshot, dict) else None
    types = task.get("produces") if isinstance(task, dict) else None
    if not isinstance(types, list) or any(
        not isinstance(item, str) or item not in _TYPES for item in types
    ):
        raise RuntimeProtocolError("artifact_requirements_unknown")
    return {
        "version": 1,
        "workspace": asdict(workspace),
        "required_types": list(dict.fromkeys(types)),
    }


def bind_context(context, session):
    if (
        not isinstance(context, dict)
        or context.keys() != {"version", "workspace", "required_types"}
        or type(context["version"]) is not int
        or context["version"] != 1
        or not isinstance(context["workspace"], dict)
        or context["workspace"].keys() != {"path", "device", "inode"}
        or not isinstance(context["required_types"], list)
        or any(
            not isinstance(item, str) or item not in _TYPES for item in context["required_types"]
        )
    ):
        raise RuntimeProtocolError("artifact_context_unknown")
    original = ArtifactWorkspace(**context["workspace"])
    if (
        not isinstance(original.path, str)
        or type(original.device) is not int
        or type(original.inode) is not int
        or original.device < 0
        or original.inode < 1
        or original.path != session.instance.home_path
        or capture_workspace(original.path) != original
    ):
        raise RuntimeProtocolError("artifact_workspace_changed")
    session.artifact_workspace = original
    session.artifact_required_types = tuple(context["required_types"])
