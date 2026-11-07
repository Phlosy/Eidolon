"""The work domain (§6): Mission → Project → Task graph → Review → Artifact.

Depends on institution and kernel. Everything is facts + two folds
(``ProjectState`` / ``MissionState``, strongly consistent) + one projection
(four tables, eventually consistent, rebuildable). Readiness is derived.
"""

from eidolon.work import facts, graph
from eidolon.work.facts import KINDS, REVIEW_VERDICTS, TASK_STATUSES, WorkError
from eidolon.work.graph import DagError, GraphReport, Node, dispatchable, lineage, resolve_ready
from eidolon.work.projection import ArtifactRow, MissionRow, ProjectRow, TaskRow, WorkProjection
from eidolon.work.service import WorkService
from eidolon.work.state import ArtifactState, MissionState, ProjectState, TaskState

__all__ = [
    "KINDS",
    "REVIEW_VERDICTS",
    "TASK_STATUSES",
    "ArtifactRow",
    "ArtifactState",
    "DagError",
    "GraphReport",
    "MissionRow",
    "MissionState",
    "Node",
    "ProjectRow",
    "ProjectState",
    "TaskRow",
    "TaskState",
    "WorkError",
    "WorkProjection",
    "WorkService",
    "dispatchable",
    "facts",
    "graph",
    "lineage",
    "resolve_ready",
]
