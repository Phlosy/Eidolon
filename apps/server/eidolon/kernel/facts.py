"""Fact value objects and the vocabulary rules for fact kinds (K4, K5).

Pure Python: no database, no I/O. Everything persistent goes through ``store``.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class ActorKind(StrEnum):
    """Who caused a fact. The decision boundary (v2-architecture §1.2) is built on this."""

    system = "system"
    agent = "agent"
    human = "human"


@dataclass(frozen=True, slots=True)
class Actor:
    kind: ActorKind
    id: str | None = None  # None only for ``system``

    def __post_init__(self) -> None:
        if self.kind is not ActorKind.system and not self.id:
            raise ValueError(f"{self.kind} actor needs an id")
        if self.kind is ActorKind.system and self.id:
            raise ValueError("system actor has no id")

    @classmethod
    def system(cls) -> "Actor":
        return cls(ActorKind.system)

    def as_dict(self) -> dict[str, str | None]:
        return {"kind": self.kind.value, "id": self.id}


# Kinds are ``<layer>.<noun>.<verb>`` with an optional fourth segment. The first
# segment must be a known layer so the vocabulary cannot sprawl (K5).
KIND_LAYERS = frozenset(
    {"kernel", "identity", "institution", "work", "agent", "money", "talent", "extension"}
)
_KIND = re.compile(r"^[a-z]+(\.[a-z][a-z0-9_]*){2,3}$")


class InvalidKind(ValueError):
    pass


def validate_kind(kind: str) -> str:
    if not isinstance(kind, str) or not _KIND.match(kind):
        raise InvalidKind(f"kind must look like 'layer.noun.verb': {kind!r}")
    layer = kind.split(".", 1)[0]
    if layer not in KIND_LAYERS:
        raise InvalidKind(
            f"unknown layer {layer!r} in kind {kind!r}; allowed: {sorted(KIND_LAYERS)}"
        )
    return kind


JsonScalar = str | int | float | bool | None
Json = JsonScalar | list[Any] | dict[str, Any]


class InvalidPayload(ValueError):
    pass


def validate_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Only plain JSON is allowed: no datetimes, Decimals, sets or NaN. Callers convert
    explicitly so that the canonical bytes are unambiguous (K4)."""
    if not isinstance(payload, Mapping):
        raise InvalidPayload("payload must be a mapping")
    _check(payload, "$")
    return dict(payload)


def _check(value: Any, path: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise InvalidPayload(f"{path}: NaN/Infinity are not JSON")
        return
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _check(item, f"{path}[{i}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise InvalidPayload(
                    f"{path}: object keys must be strings, got {type(key).__name__}"
                )
            _check(item, f"{path}.{key}")
        return
    raise InvalidPayload(f"{path}: {type(value).__name__} is not JSON; convert it first")


@dataclass(frozen=True, slots=True)
class NewFact:
    """What a writer supplies. The store adds identity, ordering, time and the chain."""

    company_id: UUID
    actor: Actor
    kind: str
    payload: Mapping[str, Any]
    idempotency_key: str
    causation_id: UUID | None = None
    correlation_id: UUID | None = None
    decision_id: UUID | None = None

    def __post_init__(self) -> None:
        validate_kind(self.kind)
        if not self.idempotency_key or len(self.idempotency_key) > 200:
            raise ValueError("idempotency_key must be 1..200 characters")
        object.__setattr__(self, "payload", validate_payload(self.payload))


@dataclass(frozen=True, slots=True)
class Fact:
    """A recorded fact. Immutable here and in the database (K1)."""

    id: UUID
    seq: int  # global order, consumer cursors use it
    chain_seq: int  # per-company order, part of the hash chain
    company_id: UUID
    actor: Actor
    kind: str
    payload: dict[str, Any] = field(repr=False)
    idempotency_key: str
    causation_id: UUID | None
    correlation_id: UUID
    decision_id: UUID | None
    prev_hash: str
    hash: str
    created_at: datetime
