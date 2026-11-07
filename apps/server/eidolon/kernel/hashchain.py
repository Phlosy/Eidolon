"""Per-company hash chain over facts (K3).

``hash = sha256(canonical_json(header ∪ payload ∪ prev_hash))``. The chain is
tamper-*evident*, not tamper-proof: an attacker with database access can rewrite
history, but cannot do so without breaking every later hash. ``verify`` finds the
first break. No signatures or external anchoring yet (kernel-plan WP-25).
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from eidolon.kernel.facts import Actor, Fact

GENESIS_HASH = "0" * 64


def canonical_json(value: Any) -> bytes:
    """Deterministic bytes: sorted keys, no whitespace, UTF-8, no non-JSON types."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("fact timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def digest(
    *,
    id: UUID,
    company_id: UUID,
    chain_seq: int,
    actor: Actor,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str,
    causation_id: UUID | None,
    correlation_id: UUID,
    decision_id: UUID | None,
    created_at: datetime,
    prev_hash: str,
) -> str:
    body = {
        "id": str(id),
        "company_id": str(company_id),
        "chain_seq": chain_seq,
        "actor": actor.as_dict(),
        "kind": kind,
        "payload": payload,
        "idempotency_key": idempotency_key,
        "causation_id": str(causation_id) if causation_id else None,
        "correlation_id": str(correlation_id),
        "decision_id": str(decision_id) if decision_id else None,
        "created_at": timestamp(created_at),
        "prev_hash": prev_hash,
    }
    return hashlib.sha256(canonical_json(body)).hexdigest()


def digest_of(fact: Fact) -> str:
    return digest(
        id=fact.id,
        company_id=fact.company_id,
        chain_seq=fact.chain_seq,
        actor=fact.actor,
        kind=fact.kind,
        payload=fact.payload,
        idempotency_key=fact.idempotency_key,
        causation_id=fact.causation_id,
        correlation_id=fact.correlation_id,
        decision_id=fact.decision_id,
        created_at=fact.created_at,
        prev_hash=fact.prev_hash,
    )


@dataclass(frozen=True, slots=True)
class ChainReport:
    company_id: UUID
    checked: int
    ok: bool
    broken_at: int | None = None  # chain_seq of the first bad fact
    reason: str | None = None


def verify(company_id: UUID, facts: Iterable[Fact]) -> ChainReport:
    """``facts`` must be the company's facts in ascending ``chain_seq`` order."""
    expected_prev = GENESIS_HASH
    expected_seq = 1
    checked = 0
    for fact in facts:
        if fact.company_id != company_id:
            return ChainReport(company_id, checked, False, fact.chain_seq, "foreign company")
        if fact.chain_seq != expected_seq:
            reason = f"gap: expected chain_seq {expected_seq}"
            return ChainReport(company_id, checked, False, fact.chain_seq, reason)
        if fact.prev_hash != expected_prev:
            return ChainReport(company_id, checked, False, fact.chain_seq, "prev_hash mismatch")
        if digest_of(fact) != fact.hash:
            return ChainReport(company_id, checked, False, fact.chain_seq, "hash mismatch")
        expected_prev = fact.hash
        expected_seq += 1
        checked += 1
    return ChainReport(company_id, checked, True)
