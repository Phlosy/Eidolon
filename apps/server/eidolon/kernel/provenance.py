"""Provenance primitives (K10): walk the causal graph of facts and export a
self-verifying bundle.

``trace``   from one fact, follow ``causation_id`` upward (bounded depth, cycle
            safe) and collect every fact sharing its ``correlation_id``; returns
            the facts in global order plus the cause→effect edges among them.
``bundle``  export a company's facts (all, by ids, or by correlation) as plain
            JSON with every hash, the chain head and a seal over the whole file.
            ``scripts/verify_bundle.py`` checks such a file with no database and
            no import of this package.

Pure reads: nothing here writes.
"""

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from eidolon.kernel import hashchain
from eidolon.kernel.facts import Fact
from eidolon.kernel.store import FactChainRow, FactRow, FactStore

BUNDLE_FORMAT = "eidolon.provenance.bundle/1"


@dataclass(frozen=True, slots=True)
class Edge:
    cause: UUID  # the fact named by ``effect``'s causation_id
    effect: UUID


@dataclass(frozen=True, slots=True)
class Trace:
    root: UUID
    facts: list[Fact]  # ascending seq; includes the root
    edges: list[Edge]  # only edges whose both ends are in ``facts``
    depth_reached: bool  # the ancestry walk stopped because of ``depth``
    missing: list[UUID]  # causation_ids that name no recorded fact

    def by_id(self) -> dict[UUID, Fact]:
        return {f.id: f for f in self.facts}


class UnknownFact(LookupError):
    pass


def trace(db: Session, fact_id: UUID, *, depth: int = 10) -> Trace:
    """Everything needed to answer "why does this fact exist?".

    Ancestry: up to ``depth`` hops of ``causation_id``. The store never lets a
    fact name a later fact as its cause, so a cycle can only be the result of
    tampering; the visited-set below makes the walk terminate regardless.
    Correlation: every fact with the root's ``correlation_id``, whatever company.
    """
    if depth < 0:
        raise ValueError("depth must be >= 0")
    root = db.get(FactRow, fact_id)
    if root is None:
        raise UnknownFact(f"no fact {fact_id}")

    collected: dict[UUID, FactRow] = {root.id: root}
    missing: list[UUID] = []
    depth_reached = False

    current = root
    hops = 0
    while current.causation_id is not None:
        if hops >= depth:
            depth_reached = True
            break
        parent_id = current.causation_id
        if parent_id in collected:
            break  # cycle (tampered log); stop rather than loop
        parent = db.get(FactRow, parent_id)
        if parent is None:
            missing.append(parent_id)
            break
        collected[parent.id] = parent
        current = parent
        hops += 1

    for row in db.scalars(select(FactRow).where(FactRow.correlation_id == root.correlation_id)):
        collected.setdefault(row.id, row)

    facts = sorted((row.to_fact() for row in collected.values()), key=lambda f: f.seq)
    edges = [
        Edge(cause=f.causation_id, effect=f.id)
        for f in facts
        if f.causation_id is not None and f.causation_id in collected
    ]
    for f in facts:
        if f.causation_id is not None and f.causation_id not in collected:
            if f.causation_id not in missing and db.get(FactRow, f.causation_id) is None:
                missing.append(f.causation_id)
    return Trace(root.id, facts, edges, depth_reached, missing)


# -- bundles ---------------------------------------------------------------------


def fact_to_json(fact: Fact) -> dict[str, Any]:
    """The wire form of a fact. Field names and encodings match ``hashchain.digest``
    exactly, so a verifier can rebuild the hash body from this dict alone."""
    return {
        "id": str(fact.id),
        "seq": fact.seq,
        "chain_seq": fact.chain_seq,
        "company_id": str(fact.company_id),
        "actor": fact.actor.as_dict(),
        "kind": fact.kind,
        "payload": fact.payload,
        "idempotency_key": fact.idempotency_key,
        "causation_id": str(fact.causation_id) if fact.causation_id else None,
        "correlation_id": str(fact.correlation_id),
        "decision_id": str(fact.decision_id) if fact.decision_id else None,
        "created_at": hashchain.timestamp(fact.created_at),
        "prev_hash": fact.prev_hash,
        "hash": fact.hash,
    }


def seal(bundle: dict[str, Any]) -> str:
    """sha256 over the canonical JSON of everything except ``bundle_hash``. This
    seals the *file*: metadata, selection and chain head included."""
    body = {k: v for k, v in bundle.items() if k != "bundle_hash"}
    return hashlib.sha256(hashchain.canonical_json(body)).hexdigest()


def bundle(
    db: Session,
    company_id: UUID,
    *,
    fact_ids: Iterable[UUID] | None = None,
    correlation_id: UUID | None = None,
    store: FactStore | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Export facts of ``company_id`` as a JSON-able dict.

    Selection: ``fact_ids`` and/or ``correlation_id`` (union); neither means the
    whole chain. Every selected fact must belong to ``company_id``. Facts are in
    ``chain_seq`` order so the verifier can check prev→hash links wherever the
    selection is contiguous.
    """
    store = store or FactStore()
    selection: dict[str, Any]
    rows: dict[UUID, FactRow] = {}

    if fact_ids is None and correlation_id is None:
        selection = {"mode": "chain"}
        for fact in store.chain(db, company_id, limit=10**9):
            rows[fact.id] = db.get(FactRow, fact.id)
    else:
        selection = {"mode": "subset"}
        if correlation_id is not None:
            selection["correlation_id"] = str(correlation_id)
            for row in db.scalars(select(FactRow).where(FactRow.correlation_id == correlation_id)):
                if row.company_id != company_id:
                    raise ValueError(
                        f"correlation {correlation_id} includes fact {row.id} of another company"
                    )
                rows[row.id] = row
        if fact_ids is not None:
            wanted = list(fact_ids)
            selection["fact_ids"] = [str(i) for i in wanted]
            for fid in wanted:
                row = db.get(FactRow, fid)
                if row is None:
                    raise UnknownFact(f"no fact {fid}")
                if row.company_id != company_id:
                    raise ValueError(f"fact {fid} belongs to company {row.company_id}")
                rows[row.id] = row

    head = db.get(FactChainRow, company_id)
    facts = sorted((row.to_fact() for row in rows.values()), key=lambda f: f.chain_seq)
    out: dict[str, Any] = {
        "format": BUNDLE_FORMAT,
        "exported_at": hashchain.timestamp((now or datetime.now(UTC)).astimezone(UTC)),
        "company_id": str(company_id),
        "chain_head": (
            {"last_seq": head.last_seq, "last_hash": head.last_hash}
            if head is not None
            else {"last_seq": 0, "last_hash": hashchain.GENESIS_HASH}
        ),
        "selection": selection,
        "facts": [fact_to_json(f) for f in facts],
    }
    out["bundle_hash"] = seal(out)
    return out
