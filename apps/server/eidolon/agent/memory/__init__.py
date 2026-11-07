"""Memory (v2-architecture §4.3): episodic reflections and semantic knowledge, all
facts; ``memory_entries`` is their projection; retrieval enforces person isolation.
Procedural memory (skills) and vector search come later."""

from eidolon.agent.memory.facts import (
    FORGOTTEN,
    KINDS,
    PROMOTED,
    RECORDED,
    Entry,
    MemoryError,
    forget,
    promote,
    record,
)
from eidolon.agent.memory.projection import MemoryEntryRow, MemoryProjection
from eidolon.agent.memory.recall import MemoryRecall
from eidolon.agent.memory.reflection import Reflector
from eidolon.agent.memory.repository import Hit, MemoryRepository, Viewer

__all__ = [
    "FORGOTTEN",
    "KINDS",
    "PROMOTED",
    "RECORDED",
    "Entry",
    "Hit",
    "MemoryEntryRow",
    "MemoryError",
    "MemoryProjection",
    "MemoryRecall",
    "MemoryRepository",
    "Reflector",
    "Viewer",
    "forget",
    "promote",
    "record",
]
