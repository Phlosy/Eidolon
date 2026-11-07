"""K10: trace (depth, cycles, dangling causes) and bundles that verify offline with
an independent verifier, and fail it after any content change."""

import copy
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from eidolon.cli import main
from eidolon.db import SessionLocal
from eidolon.kernel import provenance
from eidolon.kernel.facts import Actor, NewFact
from eidolon.kernel.provenance import Edge, UnknownFact, bundle, trace
from eidolon.kernel.store import FactStore

store = FactStore()
VERIFY = Path(__file__).resolve().parents[3] / "scripts" / "verify_bundle.py"
SERVER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def add(db, company, key, *, cause=None, correlation=None, kind="work.task.created"):
    fact = store.append(
        db,
        NewFact(
            company,
            Actor.system(),
            kind,
            {"key": key},
            idempotency_key=key,
            causation_id=cause.id if cause else None,
            correlation_id=correlation
            if correlation
            else (cause.correlation_id if cause else None),
        ),
    )
    db.commit()
    return fact


def verify_file(path: Path) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, "-I", str(VERIFY), str(path)], capture_output=True, text=True, timeout=30
    )
    return result.returncode, result.stdout + result.stderr


def write(tmp_path: Path, data: dict, name="bundle.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


# -- trace -------------------------------------------------------------------------


def test_trace_follows_causation_and_collects_correlation(db):
    company = uuid4()
    root = add(db, company, "root")
    a = add(db, company, "a", cause=root)
    b = add(db, company, "b", cause=a)
    c = add(db, company, "c", cause=b)
    sibling = add(db, company, "sibling", cause=root)  # same correlation, other branch
    add(db, company, "unrelated")  # own correlation

    result = trace(db, c.id)
    assert result.root == c.id
    assert [f.idempotency_key for f in result.facts] == ["root", "a", "b", "c", "sibling"]
    assert set(result.edges) == {
        Edge(root.id, a.id),
        Edge(a.id, b.id),
        Edge(b.id, c.id),
        Edge(root.id, sibling.id),
    }
    assert not result.depth_reached and result.missing == []


def test_trace_depth_limits_ancestry_across_correlations(db):
    company = uuid4()
    x = add(db, company, "x")  # correlation X
    y = add(db, company, "y", cause=x, correlation=uuid4())  # new correlation, caused by x
    z = add(db, company, "z", cause=y)  # correlation Y

    shallow = trace(db, z.id, depth=1)
    assert [f.idempotency_key for f in shallow.facts] == ["y", "z"]
    assert shallow.depth_reached
    assert shallow.edges == [Edge(y.id, z.id)]  # y -> x is outside the collected set

    deep = trace(db, z.id, depth=2)
    assert [f.idempotency_key for f in deep.facts] == ["x", "y", "z"]
    assert not deep.depth_reached
    assert set(deep.edges) == {Edge(x.id, y.id), Edge(y.id, z.id)}

    assert [f.idempotency_key for f in trace(db, z.id, depth=0).facts] == ["y", "z"]
    with pytest.raises(ValueError):
        trace(db, z.id, depth=-1)


def test_trace_survives_cycles_and_reports_dangling_causes(db):
    company = uuid4()
    a = add(db, company, "a")
    b = add(db, company, "b", cause=a)
    # A cycle is impossible through the store; forge one behind the trigger.
    db.execute(text("ALTER TABLE facts DISABLE TRIGGER facts_no_update_delete"))
    db.execute(text("UPDATE facts SET causation_id = :b WHERE id = :a"), {"a": a.id, "b": b.id})
    db.execute(text("ALTER TABLE facts ENABLE TRIGGER facts_no_update_delete"))
    db.commit()
    result = trace(db, b.id, depth=100)
    assert [f.idempotency_key for f in result.facts] == ["a", "b"]
    assert set(result.edges) == {Edge(a.id, b.id), Edge(b.id, a.id)}

    ghost = uuid4()
    orphan = store.append(
        db,
        NewFact(company, Actor.system(), "work.task.created", {}, "orphan", causation_id=ghost),
    )
    db.commit()
    result = trace(db, orphan.id)
    assert result.missing == [ghost] and result.edges == []

    with pytest.raises(UnknownFact):
        trace(db, uuid4())


# -- bundle ------------------------------------------------------------------------


def test_bundle_verifies_offline_and_any_change_is_detected(db, tmp_path):
    company = uuid4()
    root = add(db, company, "root")
    add(db, company, "child", cause=root)
    add(db, company, "other")
    add(db, uuid4(), "foreign")

    data = bundle(db, company)
    assert data["format"] == provenance.BUNDLE_FORMAT
    assert data["selection"] == {"mode": "chain"}
    assert [f["chain_seq"] for f in data["facts"]] == [1, 2, 3]
    assert data["chain_head"]["last_hash"] == data["facts"][-1]["hash"]
    assert data["bundle_hash"] == provenance.seal(data)
    code, out = verify_file(write(tmp_path, data))
    assert code == 0 and "OK" in out and "2 links checked" in out, out

    def mutate(label, fn):
        altered = copy.deepcopy(data)
        fn(altered)
        code, out = verify_file(write(tmp_path, altered, f"{label}.json"))
        assert code == 1 and "BROKEN" in out, f"{label}: {out}"
        return out

    out = mutate("payload", lambda d: d["facts"][1]["payload"].update(key="forged"))
    assert "chain_seq=2: hash mismatch" in out
    out = mutate(
        "created_at", lambda d: d["facts"][0].update(created_at="2020-01-01T00:00:00.000000Z")
    )
    assert "chain_seq=1: hash mismatch" in out
    out = mutate("actor", lambda d: d["facts"][2]["actor"].update(kind="human", id="eve"))
    assert "chain_seq=3: hash mismatch" in out
    out = mutate("prev_hash", lambda d: d["facts"][2].update(prev_hash="f" * 64))
    assert "hash mismatch" in out
    out = mutate("remove_middle", lambda d: d["facts"].pop(1))
    assert "has gaps" in out and "bundle_hash" in out
    out = mutate("remove_last", lambda d: d["facts"].pop())
    assert "chain head" in out
    out = mutate("reorder", lambda d: d["facts"].reverse())
    assert "bundle_hash" in out  # content unchanged, file changed: only the seal catches it
    out = mutate("head", lambda d: d["chain_head"].update(last_hash="0" * 64))
    assert "chain head" in out
    out = mutate("exported_at", lambda d: d.update(exported_at="2020-01-01T00:00:00.000000Z"))
    assert "bundle_hash" in out
    out = mutate("company", lambda d: d.update(company_id=str(uuid4())))
    assert "belongs to" in out
    # Re-sealing a forged bundle does not help: the facts still fail.
    forged = copy.deepcopy(data)
    forged["facts"][0]["kind"] = "work.task.deleted"
    forged["bundle_hash"] = provenance.seal(forged)
    code, out = verify_file(write(tmp_path, forged, "resealed.json"))
    assert code == 1 and "chain_seq=1: hash mismatch" in out and "bundle_hash" not in out


def test_bundle_subset_by_correlation_and_ids(db, tmp_path):
    company = uuid4()
    root = add(db, company, "root")
    add(db, company, "noise1")
    child = add(db, company, "child", cause=root)
    add(db, company, "noise2")
    tail = add(db, company, "tail", cause=child)

    data = bundle(db, company, correlation_id=root.correlation_id)
    assert [f["chain_seq"] for f in data["facts"]] == [1, 3, 5]
    assert data["selection"] == {"mode": "subset", "correlation_id": str(root.correlation_id)}
    code, out = verify_file(write(tmp_path, data))
    assert code == 0 and "0 links checked" in out, out  # no adjacent pairs, head still matches

    data = bundle(db, company, fact_ids=[tail.id, child.id], correlation_id=root.correlation_id)
    assert [f["chain_seq"] for f in data["facts"]] == [1, 3, 5]
    assert data["selection"]["fact_ids"] == [str(tail.id), str(child.id)]

    data = bundle(db, company, fact_ids=[child.id, UUID(str(root.id))])
    assert [f["chain_seq"] for f in data["facts"]] == [1, 3]
    code, out = verify_file(write(tmp_path, data, "ids.json"))
    assert code == 0, out
    # Tamper with the head fact's hash of a subset that does not include the head: undetectable
    # by the head check but still caught by the per-fact hash.
    data["facts"][0]["payload"]["key"] = "x"
    assert verify_file(write(tmp_path, data, "ids2.json"))[0] == 1

    foreign = add(db, uuid4(), "foreign")
    with pytest.raises(ValueError, match="belongs to company"):
        bundle(db, company, fact_ids=[foreign.id])
    with pytest.raises(ValueError, match="another company"):
        bundle(db, company, correlation_id=foreign.correlation_id)
    with pytest.raises(UnknownFact):
        bundle(db, company, fact_ids=[uuid4()])


def test_empty_chain_bundle_verifies(db, tmp_path):
    data = bundle(db, uuid4())
    assert data["facts"] == [] and data["chain_head"]["last_seq"] == 0
    assert verify_file(write(tmp_path, data))[0] == 0


def test_verifier_rejects_unreadable_or_foreign_files(tmp_path):
    assert verify_file(tmp_path / "missing.json")[0] == 2
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert verify_file(bad)[0] == 2
    code, out = verify_file(write(tmp_path, {"format": "something/else"}, "fmt.json"))
    assert code == 1 and "unknown format" in out


# -- CLI ---------------------------------------------------------------------------


def test_cli_export_bundle_and_trace(db, tmp_path):
    company = uuid4()
    root = add(db, company, "root")
    child = add(db, company, "child", cause=root)

    out = io.StringIO()
    code = main(["export-bundle", "--company", str(company)], session_factory=SessionLocal, out=out)
    assert code == 0
    data = json.loads(out.getvalue())
    assert len(data["facts"]) == 2
    assert verify_file(write(tmp_path, data))[0] == 0

    target = tmp_path / "corr.json"
    code = main(
        [
            "export-bundle",
            "--company",
            str(company),
            "--correlation",
            str(root.correlation_id),
            "-o",
            str(target),
            "--pretty",
        ],
        session_factory=SessionLocal,
        out=io.StringIO(),
    )
    assert code == 0 and verify_file(target)[0] == 0

    out = io.StringIO()
    assert main(["trace", str(child.id)], session_factory=SessionLocal, out=out) == 0
    lines = out.getvalue().splitlines()
    assert lines[0] == f"root {child.id}"
    assert lines[1].startswith("  seq=") and str(root.id) in lines[1]
    assert lines[2].startswith("* seq=") and f"<- {root.id}" in lines[2]
    assert "2 facts, 1 edges" in lines[3]
    assert main(["trace", str(uuid4())], session_factory=SessionLocal, out=io.StringIO()) == 2


def test_real_entry_points_end_to_end(db, tmp_path):
    company = uuid4()
    add(db, company, "a")
    add(db, company, "b")
    target = tmp_path / "e2e.json"
    result = subprocess.run(
        [sys.executable, "-m", "eidolon", "export-bundle", "--company", str(company), "-o", target],
        cwd=SERVER_ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "2 facts" in result.stderr
    code, out = verify_file(target)
    assert code == 0 and "1 links checked" in out, out
