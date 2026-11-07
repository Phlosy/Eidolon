"""Memory (R2-d): facts → projection → isolated retrieval; reflection as a consumer
on a real replayed run; recall injected into the loop's context."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from loop_support import PROMPT, new_spec, replay_provider, weather_tools
from sqlalchemy import select, text

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobStore
from eidolon.agent.loop import AgentRunner
from eidolon.agent.memory import (
    RECORDED,
    Entry,
    MemoryEntryRow,
    MemoryError,
    MemoryProjection,
    MemoryRecall,
    MemoryRepository,
    Reflector,
    Viewer,
    forget,
    promote,
    record,
)
from eidolon.agent.memory.reflection import build_request, parse_reflection, reflection_id
from eidolon.agent.providers import Completed, TextDelta, Usage
from eidolon.agent.providers.accounting import KIND as MODEL_CALLED
from eidolon.db import SessionLocal
from eidolon.kernel.consumers import process, rebuild
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.kernel.store import FactStore

store = FactStore()
repo = MemoryRepository()
HUMAN = Actor(ActorKind.human, "owner")


def agent(person):
    return Actor(ActorKind.agent, person)


def entry(person, title, body, *, layer="semantic", scope="private", **kw) -> Entry:
    return Entry(uuid4(), person, layer, scope, kw.pop("kind", "note"), title, body, **kw)


def write(db, company, actor, *entries, now=None):
    facts = [store.append(db, record(company, actor, e), now=now) for e in entries]
    db.commit()
    return facts


def project(db):
    process(SessionLocal, MemoryProjection())
    db.expire_all()


def titles(hits):
    return [h.title for h in hits]


# -- facts -------------------------------------------------------------------------------


def test_entry_and_fact_validation():
    with pytest.raises(MemoryError, match="layer"):
        entry("p", "t", "b", layer="working")
    with pytest.raises(MemoryError, match="always private"):
        entry("p", "t", "b", layer="episodic", scope="company")
    with pytest.raises(MemoryError, match="department_id"):
        entry("p", "t", "b", scope="department")
    with pytest.raises(MemoryError, match="title"):
        entry("p", " ", "b")
    with pytest.raises(MemoryError, match="importance"):
        entry("p", "t", "b", importance=2)
    company, mid = uuid4(), uuid4()
    # The system never widens knowledge on its own: a decision or a human is required.
    with pytest.raises(MemoryError, match="decision_id"):
        promote(company, Actor.system(), mid, to_scope="company")
    with pytest.raises(MemoryError, match="decision_id"):
        promote(company, agent("p"), mid, to_scope="company")
    fact = promote(company, agent("p"), mid, to_scope="company", decision_id=uuid4())
    assert fact.decision_id is not None and fact.payload["to_scope"] == "company"
    assert (
        promote(company, HUMAN, mid, to_scope="department", department_id="eng").decision_id is None
    )
    with pytest.raises(MemoryError):
        forget(company, HUMAN, mid, weight=1.5)
    assert forget(company, HUMAN, mid, weight=0.2).payload["weight"] == 0.2


# -- projection ---------------------------------------------------------------------------


def test_projection_applies_record_promote_forget_and_rebuilds(db):
    company = uuid4()
    e = entry("alice", "Deploy checklist", "Run migrations before restarting the API.")
    write(db, company, agent("alice"), e)
    project(db)
    row = db.get(MemoryEntryRow, e.memory_id)
    assert row.scope == "private" and row.weight == 1.0 and row.person_id == "alice"
    assert row.search is not None

    store.append(
        db, promote(company, HUMAN, e.memory_id, to_scope="department", department_id="eng")
    )
    store.append(db, forget(company, HUMAN, e.memory_id, weight=0.3))
    store.append(db, forget(company, HUMAN, e.memory_id, weight=0.9))  # never raises weight
    db.commit()
    project(db)
    row = db.get(MemoryEntryRow, e.memory_id)
    assert row.scope == "department" and row.department_id == "eng" and row.weight == 0.3
    # narrowing is ignored: a later "promotion" to private does not shrink visibility
    store.append(db, promote(company, HUMAN, e.memory_id, to_scope="private"))
    db.commit()
    project(db)
    db.expire_all()
    assert db.get(MemoryEntryRow, e.memory_id).scope == "department"

    db.execute(text("UPDATE memory_entries SET title = 'corrupted', weight = 1"))
    db.commit()
    assert rebuild(SessionLocal, MemoryProjection()) == 5
    db.expire_all()
    row = db.get(MemoryEntryRow, e.memory_id)
    assert row.title == "Deploy checklist" and row.weight == 0.3 and row.scope == "department"


# -- retrieval & isolation ------------------------------------------------------------------


def test_visibility_is_enforced_in_the_repository(db):
    company, other_company = uuid4(), uuid4()
    write(
        db,
        company,
        agent("alice"),
        entry("alice", "Alice private", "secret alice plan for the rollout"),
        entry("alice", "Alice episode", "yesterday the rollout failed", layer="episodic"),
    )
    write(
        db,
        company,
        agent("bob"),
        entry("bob", "Bob private", "bob rollout notes"),
        entry(
            "bob", "Eng dept", "department rollout runbook", scope="department", department_id="eng"
        ),
        entry("bob", "Company wide", "company rollout policy", scope="company"),
    )
    write(db, other_company, agent("alice"), entry("alice", "Other company", "rollout elsewhere"))
    project(db)

    alice = Viewer(company, "alice")
    assert sorted(titles(repo.search(db, alice, "rollout", limit=20))) == [
        "Alice episode",
        "Alice private",
        "Company wide",
    ]
    alice_eng = Viewer(company, "alice", department_id="eng")
    assert "Eng dept" in titles(repo.search(db, alice_eng, "rollout", limit=20))
    bob = Viewer(company, "bob")
    assert sorted(titles(repo.search(db, bob, "rollout", limit=20))) == [
        "Bob private",
        "Company wide",
    ]
    assert titles(repo.search(db, bob, "secret alice", limit=20)) == []
    assert titles(repo.search(db, alice, "rollout failed", mode="all")) == ["Alice episode"]
    # get() goes through the same check
    alice_private = db.scalar(select(MemoryEntryRow).where(MemoryEntryRow.title == "Alice private"))
    assert repo.get(db, bob, alice_private.memory_id) is None
    assert repo.get(db, alice, alice_private.memory_id).title == "Alice private"
    # layers filter and empty query (recency listing)
    assert titles(repo.search(db, alice, None, layers=("episodic",))) == ["Alice episode"]
    assert len(repo.search(db, alice, None, limit=50)) == 3
    assert titles(repo.search(db, Viewer(other_company, "alice"), "rollout")) == ["Other company"]


def test_ranking_uses_relevance_weight_importance_and_decay(db):
    company = uuid4()
    now = datetime.now(UTC)
    old = now - timedelta(days=120)
    write(
        db,
        company,
        agent("p"),
        entry("p", "Old episode", "postgres connection pool exhausted", layer="episodic"),
        now=old,
    )
    write(
        db,
        company,
        agent("p"),
        entry("p", "Recent episode", "postgres connection pool exhausted again", layer="episodic"),
        entry("p", "Low importance", "postgres pool tuning", importance=0.1),
        entry("p", "High importance", "postgres pool tuning", importance=1.0),
        entry("p", "Unrelated", "frontend bundle size"),
        now=now,
    )
    project(db)
    viewer = Viewer(company, "p")
    hits = repo.search(db, viewer, "postgres pool", limit=10, now=now)
    assert "Unrelated" not in titles(hits)
    by = {h.title: h.score for h in hits}
    assert by["Recent episode"] > by["Old episode"]  # time decay on episodic
    assert by["High importance"] > by["Low importance"]  # importance factor
    # forgetting lowers the weight and therefore the rank
    high = db.scalar(select(MemoryEntryRow).where(MemoryEntryRow.title == "High importance"))
    store.append(db, forget(company, HUMAN, high.memory_id, weight=0.05))
    db.commit()
    project(db)
    by = {h.title: h.score for h in repo.search(db, viewer, "postgres pool", limit=10, now=now)}
    assert by["High importance"] < by["Low importance"]
    assert all(h.score > 0 for h in hits)


# -- reflection -------------------------------------------------------------------------


def test_parse_reflection_is_lenient():
    raw = '{"title": "T", "summary": "S", "lessons": ["a", ""], "tags": ["X", "x", "y"], '
    raw += '"importance": "0.7"}'
    fields, parsed = parse_reflection(f"```json\n{raw}\n```")
    assert parsed and fields == {
        "title": "T",
        "summary": "S",
        "lessons": ["a"],
        "tags": ["x", "y"],
        "importance": 0.7,
    }
    fields, parsed = parse_reflection(
        'Sure! Here you go: {"title": "T2", "summary": "S2"} hope it helps'
    )
    assert parsed and fields["title"] == "T2" and fields["importance"] == 0.5
    fields, parsed = parse_reflection("I cannot produce JSON.\nSecond line.")
    assert (
        not parsed
        and fields["title"] == "I cannot produce JSON."
        and "Second line" in fields["summary"]
    )
    fields, parsed = parse_reflection('{"title": "x", "summary": "y", "importance": 9}')
    assert fields["importance"] == 1.0


def test_reflector_records_a_diary_entry_for_a_replayed_run(db, tmp_path):
    """Sync test on purpose: the consumer runs where the worker runs (no event loop)."""
    import asyncio

    company = uuid4()
    blobs = BlobStore(tmp_path / "blobs")
    # The reflector starts at head when first registered (it spends a model call per
    # run), so register it before the run exists — as a deployment would.
    process(SessionLocal, Reflector(replay_provider(SessionLocal), blobs))
    runner = AgentRunner(SessionLocal, replay_provider(SessionLocal), blobs, weather_tools())
    spec = new_spec(company)
    outcome = asyncio.run(runner.start(spec))
    assert outcome.status == "completed"

    reflector = Reflector(replay_provider(SessionLocal), blobs)
    # The request is built only from the transcript: deterministic across runs.
    state = runner.load(spec.run_id)
    req = build_request(state, blobs, model=None, max_tokens=512)
    assert PROMPT in req.messages[0].content and "get_weather" in req.messages[0].content
    assert str(spec.run_id) not in req.messages[0].content

    results = process(SessionLocal, reflector)
    assert results[0].handled == 1
    with SessionLocal() as s:
        facts = store.by_correlation(s, spec.run_id)
    recorded = [f for f in facts if f.kind == RECORDED]
    assert len(recorded) == 1
    p = recorded[0].payload
    assert p["memory_id"] == str(reflection_id(spec.run_id))
    assert p["layer"] == "episodic" and p["scope"] == "private" and p["person_id"] == "eng-1"
    assert p["kind"] == "reflection" and p["source"]["run_id"] == str(spec.run_id)
    assert p["source"]["parsed"] is True, p
    assert (
        "beijing" in (p["title"] + p["body"]).lower()
        or "weather" in (p["title"] + p["body"]).lower()
    )
    assert recorded[0].causation_id == next(f.id for f in facts if f.kind == R.RUN_COMPLETED)
    # the reflection's own model call is a fact caused by run.completed, in this correlation
    model_calls = [f for f in facts if f.kind == MODEL_CALLED]
    assert len(model_calls) == 3 and model_calls[-1].causation_id == recorded[0].causation_id

    # Idempotent: processing again (cursor rewound to 0 — deleting the row would mean
    # "first registration", which starts at head) does not add a second entry.
    assert process(SessionLocal, reflector)[0].handled == 0
    db.execute(text("UPDATE fact_cursors SET last_seq = 0 WHERE consumer = 'memory_reflector'"))
    db.commit()
    assert process(SessionLocal, reflector)[0].handled == 1
    with SessionLocal() as s:
        assert len([f for f in store.by_correlation(s, spec.run_id) if f.kind == RECORDED]) == 1

    # Projected and retrievable by its owner only.
    project(db)
    assert titles(repo.search(db, Viewer(company, "eng-1"), "weather beijing")) == [p["title"]]
    assert repo.search(db, Viewer(company, "someone-else"), "weather beijing") == []


# -- recall into the context ---------------------------------------------------------------


class Echo:
    name = "echo"

    def __init__(self):
        self.systems = []

    async def complete(self, request):
        self.systems.append(request.system)
        yield TextDelta("ok")
        yield Completed("end_turn", Usage(1, 1), request.model, "echo", request_id=str(uuid4()))

    async def embed(self, texts, *, model=None):
        raise NotImplementedError


async def test_recall_injects_memory_sections_into_the_context(db, tmp_path):
    company = uuid4()
    write(
        db,
        company,
        agent("eng-1"),
        entry(
            "eng-1",
            "Weather API quirk",
            "The weather tool wants city names in English.",
            scope="company",
        ),
        entry(
            "eng-1",
            "Last weather task",
            "Called get_weather for Beijing; it returned 22C.",
            layer="episodic",
        ),
    )
    write(
        db, company, agent("spy"), entry("spy", "Spy secret", "weather secrets of another person")
    )
    project(db)
    recall = MemoryRecall(SessionLocal, repo)
    sections = recall.sections(Viewer(company, "eng-1"), "weather in Beijing")
    assert len(sections) == 2
    assert sections[0].startswith("Relevant knowledge:\n- [company] Weather API quirk:")
    assert sections[1].startswith("From your past work:\n- [private] Last weather task:")
    assert "Spy secret" not in "".join(sections)

    provider = Echo()
    runner = AgentRunner(
        SessionLocal, provider, BlobStore(tmp_path / "b"), weather_tools(), memory=recall
    )
    spec = new_spec(company)
    assert (await runner.start(spec)).status == "completed"
    system = provider.systems[0]
    assert "Weather API quirk" in system and "Last weather task" in system and "Spy" not in system
    assert (
        system.index("Available tools")
        < system.index("Relevant knowledge")
        < system.index("Work on the task")
    )

    # Nothing recalled → the request is exactly what it was without memory.
    assert recall.sections(Viewer(company, "nobody"), "quantum chromodynamics") == []
    provider2 = Echo()
    runner2 = AgentRunner(
        SessionLocal, provider2, BlobStore(tmp_path / "c"), weather_tools(), memory=recall
    )
    await runner2.start(new_spec(company, person_id="nobody", prompt="quantum chromodynamics"))
    plain = AgentRunner(SessionLocal, Echo(), BlobStore(tmp_path / "d"), weather_tools())
    assert (
        "Relevant knowledge" not in provider2.systems[0] and "past work" not in provider2.systems[0]
    )
    assert (
        plain.context_builder.system_text(new_spec(company).system, weather_tools().specs())
        == provider2.systems[0]
    )


def test_reflector_never_replays_history():
    """It spends a model call per completed run (kernel.consumers.starts_at_head)."""
    from eidolon.agent.memory.reflection import Reflector
    from eidolon.kernel.consumers import starts_at_head

    assert Reflector.start_at_head is True and starts_at_head(Reflector)
