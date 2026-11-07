"""Memory facts (v2-architecture §4.3). Three kinds, all append-only:

    agent.memory.recorded   a new entry (episodic reflection or semantic knowledge)
    agent.memory.promoted   a semantic entry's scope widens — a *decision*, never the
                            system's own: needs a decision_id or a human actor
    agent.memory.forgotten  retrieval weight lowered; nothing is ever deleted

Everything readable (``memory_entries``) is a projection of these.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from eidolon.kernel.facts import Actor, ActorKind, NewFact

RECORDED = "agent.memory.recorded"
PROMOTED = "agent.memory.promoted"
FORGOTTEN = "agent.memory.forgotten"
KINDS = frozenset({RECORDED, PROMOTED, FORGOTTEN})

LAYERS = ("episodic", "semantic")
SCOPES = ("private", "department", "company")
SCOPE_RANK = {s: i for i, s in enumerate(SCOPES)}
MAX_TITLE = 200
MAX_BODY = 20_000


class MemoryError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Entry:
    """What ``agent.memory.recorded`` carries."""

    memory_id: UUID
    person_id: str
    layer: str
    scope: str
    kind: str  # reflection | note | lesson | fact | ...
    title: str
    body: str
    tags: tuple[str, ...] = ()
    source: Mapping[str, Any] = field(default_factory=dict)  # run_id / task_id / parsed ...
    importance: float = 0.5
    department_id: str | None = None

    def __post_init__(self) -> None:
        if self.layer not in LAYERS:
            raise MemoryError(f"layer must be one of {LAYERS}")
        if self.scope not in SCOPES:
            raise MemoryError(f"scope must be one of {SCOPES}")
        if self.layer == "episodic" and self.scope != "private":
            raise MemoryError("episodic memory is always private to its person")
        if self.scope == "department" and not self.department_id:
            raise MemoryError("department scope needs a department_id")
        if not self.title.strip() or len(self.title) > MAX_TITLE:
            raise MemoryError(f"title must be 1..{MAX_TITLE} characters")
        if not self.body.strip() or len(self.body) > MAX_BODY:
            raise MemoryError(f"body must be 1..{MAX_BODY} characters")
        if not 0.0 <= self.importance <= 1.0:
            raise MemoryError("importance must be within [0, 1]")
        if not self.person_id:
            raise MemoryError("person_id is required")

    def payload(self) -> dict[str, Any]:
        return {
            "memory_id": str(self.memory_id),
            "person_id": self.person_id,
            "layer": self.layer,
            "scope": self.scope,
            "department_id": self.department_id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "tags": list(self.tags),
            "source": dict(self.source),
            "importance": self.importance,
        }


def record(
    company_id: UUID,
    actor: Actor,
    entry: Entry,
    *,
    idempotency_key: str | None = None,
    causation_id: UUID | None = None,
    correlation_id: UUID | None = None,
) -> NewFact:
    return NewFact(
        company_id=company_id,
        actor=actor,
        kind=RECORDED,
        payload=entry.payload(),
        idempotency_key=idempotency_key or f"memory:{entry.memory_id}:recorded",
        causation_id=causation_id,
        correlation_id=correlation_id,
    )


def promote(
    company_id: UUID,
    actor: Actor,
    memory_id: UUID,
    *,
    to_scope: str,
    department_id: str | None = None,
    decision_id: UUID | None = None,
    reason: str | None = None,
    causation_id: UUID | None = None,
) -> NewFact:
    """Widening who can see knowledge is a management judgment (§1.2): the fact must
    name the decision that authorised it, or be written by a human directly."""
    if to_scope not in SCOPES:
        raise MemoryError(f"scope must be one of {SCOPES}")
    if to_scope == "department" and not department_id:
        raise MemoryError("department scope needs a department_id")
    if decision_id is None and actor.kind is not ActorKind.human:
        raise MemoryError(
            "promotion needs a decision_id (gate knowledge.promote) unless a human does it"
        )
    return NewFact(
        company_id=company_id,
        actor=actor,
        kind=PROMOTED,
        payload={
            "memory_id": str(memory_id),
            "to_scope": to_scope,
            "department_id": department_id,
            "reason": reason,
        },
        idempotency_key=f"memory:{memory_id}:promoted:{to_scope}:{department_id or ''}",
        decision_id=decision_id,
        causation_id=causation_id,
    )


def forget(
    company_id: UUID,
    actor: Actor,
    memory_id: UUID,
    *,
    weight: float,
    reason: str | None = None,
    causation_id: UUID | None = None,
) -> NewFact:
    """Lower (never raise above 1, never delete) an entry's retrieval weight."""
    if not 0.0 <= weight <= 1.0:
        raise MemoryError("weight must be within [0, 1]")
    return NewFact(
        company_id=company_id,
        actor=actor,
        kind=FORGOTTEN,
        payload={"memory_id": str(memory_id), "weight": weight, "reason": reason},
        idempotency_key=f"memory:{memory_id}:forgotten:{weight}:{uuid4()}",
        causation_id=causation_id,
    )


def new_id() -> UUID:
    return uuid4()


def tags_of(values: Iterable[Any]) -> tuple[str, ...]:
    seen: list[str] = []
    for v in values:
        s = str(v).strip().lower()[:40]
        if s and s not in seen:
            seen.append(s)
    return tuple(seen[:12])
