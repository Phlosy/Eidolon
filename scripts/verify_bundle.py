#!/usr/bin/env python3
"""Verify an Eidolon provenance bundle offline.

    python scripts/verify_bundle.py bundle.json        exit 0 = intact, 1 = broken, 2 = unreadable

Standard library only, no database, and deliberately *not* importing the
application: an auditor must be able to check a bundle with nothing but this
file and the bundle. The hash rules are therefore restated here and the kernel's
tests prove the two implementations agree (tests_v2/test_provenance.py).

What is checked
  1. the seal: sha256(canonical JSON of the bundle without ``bundle_hash``)
  2. every fact: sha256(canonical JSON of its header ∪ payload ∪ prev_hash) == hash
  3. every fact belongs to the bundle's company; chain_seq values are unique
  4. links: for each pair of consecutive chain_seq values present, prev_hash == previous hash
  5. a whole-chain bundle starts at chain_seq 1 with the genesis prev_hash and ends
     at the chain head; a subset that includes the head fact must match it too

canonical JSON = sorted keys, separators (",", ":"), UTF-8, ensure_ascii=False.
"""

import hashlib
import json
import sys
from itertools import pairwise

FORMAT = "eidolon.provenance.bundle/1"
GENESIS = "0" * 64
HASHED_FIELDS = (
    "id",
    "company_id",
    "chain_seq",
    "actor",
    "kind",
    "payload",
    "idempotency_key",
    "causation_id",
    "correlation_id",
    "decision_id",
    "created_at",
    "prev_hash",
)


def canonical(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fact_hash(fact: dict) -> str:
    return sha256(canonical({k: fact[k] for k in HASHED_FIELDS}))


def verify(bundle: dict) -> list[str]:
    """Return the list of problems; empty means the bundle is intact."""
    problems: list[str] = []
    if bundle.get("format") != FORMAT:
        return [
            f"unknown format {bundle.get('format')!r}; this verifier knows {FORMAT}"
        ]

    expected_seal = sha256(
        canonical({k: v for k, v in bundle.items() if k != "bundle_hash"})
    )
    if bundle.get("bundle_hash") != expected_seal:
        problems.append("bundle_hash does not match the content (file altered)")

    company = bundle.get("company_id")
    facts = bundle.get("facts")
    if not isinstance(facts, list):
        return problems + ["facts is not a list"]

    by_chain_seq: dict[int, dict] = {}
    for i, fact in enumerate(facts):
        try:
            if fact["company_id"] != company:
                problems.append(
                    f"facts[{i}] {fact['id']} belongs to {fact['company_id']}"
                )
            if fact_hash(fact) != fact["hash"]:
                problems.append(
                    f"facts[{i}] chain_seq={fact['chain_seq']}: hash mismatch"
                )
            if fact["chain_seq"] in by_chain_seq:
                problems.append(f"facts[{i}]: duplicate chain_seq {fact['chain_seq']}")
            by_chain_seq[fact["chain_seq"]] = fact
        except (KeyError, TypeError) as exc:
            problems.append(f"facts[{i}]: malformed ({exc!r})")

    seqs = sorted(by_chain_seq)
    links = 0
    for prev_seq, seq in pairwise(seqs):
        if seq != prev_seq + 1:
            continue  # gap in the selection: nothing to link across
        if by_chain_seq[seq]["prev_hash"] != by_chain_seq[prev_seq]["hash"]:
            problems.append(
                f"chain_seq={seq}: prev_hash does not match chain_seq={prev_seq}"
            )
        links += 1

    head = bundle.get("chain_head") or {}
    mode = (bundle.get("selection") or {}).get("mode")
    if seqs:
        if mode == "chain":
            if seqs[0] != 1:
                problems.append(
                    f"whole-chain bundle starts at chain_seq {seqs[0]}, not 1"
                )
            if seqs != list(range(seqs[0], seqs[0] + len(seqs))):
                problems.append("whole-chain bundle has gaps")
            if seqs[-1] != head.get("last_seq"):
                problems.append(
                    f"whole-chain bundle ends at {seqs[-1]} but chain head is {head.get('last_seq')}"
                )
        if seqs[0] == 1 and by_chain_seq[1]["prev_hash"] != GENESIS:
            problems.append("chain_seq=1 does not start from the genesis hash")
        if seqs[-1] == head.get("last_seq") and by_chain_seq[seqs[-1]][
            "hash"
        ] != head.get("last_hash"):
            problems.append("last fact's hash does not match the chain head")
    elif mode == "chain" and head.get("last_seq", 0) != 0:
        problems.append("whole-chain bundle is empty but the chain head is not")

    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in {"-h", "--help"}:
        print(__doc__.strip().splitlines()[0], file=sys.stderr)
        print("usage: verify_bundle.py bundle.json", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as fh:
            bundle = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"cannot read bundle: {exc}", file=sys.stderr)
        return 2
    if not isinstance(bundle, dict):
        print("bundle is not a JSON object", file=sys.stderr)
        return 2

    problems = verify(bundle)
    facts = bundle.get("facts") if isinstance(bundle.get("facts"), list) else []
    seqs = sorted(f.get("chain_seq") for f in facts if isinstance(f, dict))
    links = sum(1 for a, b in pairwise(seqs) if b == a + 1)
    print(
        f"bundle {argv[1]}: company {bundle.get('company_id')}, {len(facts)} facts, "
        f"{links} links checked, exported {bundle.get('exported_at')}"
    )
    if problems:
        for problem in problems:
            print(f"  FAIL {problem}")
        print("BROKEN")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
