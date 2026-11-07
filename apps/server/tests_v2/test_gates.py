"""Gate engine + decision envelopes (R3-b): resolution branches on all three
templates, the sign/conclude/escalate trail, authority snapshots, action binding,
and the loop refusing an unauthorised tool call."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from loop_support import new_spec, weather_tools
from provider_cases import WEATHER_TOOL

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobStore
from eidolon.agent.loop import AgentRunner
from eidolon.agent.providers import Completed, TextDelta, ToolCall, ToolCallEvent, Usage
from eidolon.agent.tools.registry import ToolDescriptor, ToolRegistry
from eidolon.db import SessionLocal
from eidolon.institution import (
    ESCALATED,
    GATES,
    RECORDED,
    REQUESTED,
    SIGNED,
    DecisionError,
    GateContext,
    GateError,
    Resolution,
    authorized_tools,
    bind,
    conclude,
    escalate,
    escalation_of,
    has_grant,
    load,
    request,
    resolve,
    sign,
    snapshot,
    system_record,
    tool_gate,
)
from eidolon.institution.templates import INSTITUTIONAL, SOFTWARE_STUDIO_V1, STARTUP_FLAT
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.kernel.store import FactStore

store = FactStore()
T = SOFTWARE_STUDIO_V1
COMPANY = uuid4()


def ctx(gate, **kw) -> GateContext:
    return GateContext(gate, kw.pop("company_id", COMPANY), **kw)


def agent(person):
    return Actor(ActorKind.agent, person)


def human(person):
    return Actor(ActorKind.human, person)


# -- resolution -------------------------------------------------------------------------


def test_resolve_covers_rule_tier_escalation_and_scope_branches():
    r = resolve(T, ctx("task.assign"))
    assert (r.decider, r.role, r.quorum, r.branch) == ("agent", "tech_lead", 1, "rule")
    assert r.template_fingerprint == T.fingerprint() and r.escalate_to == "ceo"

    r = resolve(T, ctx("plan.approve", proposer="pm-1", proposer_role="pm"))
    assert r.quorum == 2 and set(r.allowed_roles) == {"pm", "tech_lead"}
    assert r.separation == ("proposer!=approver",)
    big = resolve(T, ctx("plan.approve", facts={"tasks_count": 25}))
    assert (big.decider, big.role, big.quorum, big.branch) == ("agent", "ceo", 1, "escalate_if")
    purchase = resolve(T, ctx("plan.approve", facts={"has_purchase": True}))
    assert purchase.role == "ceo"

    small = resolve(T, ctx("spend.approve", amount=4_000))
    assert (small.decider, small.branch, small.tier_max) == ("system", "tier", 5_000)
    assert small.quorum == 1 and small.separation == ()
    mid = resolve(T, ctx("spend.approve", amount=100_000))
    assert (mid.decider, mid.role, mid.tier_max) == ("agent", "pm", 160_000)
    top = resolve(T, ctx("spend.approve", amount=10**9))
    assert (top.decider, top.role, top.tier_max) == ("human", "owner", None)
    accept = resolve(T, ctx("mission.accept", amount=300_000))
    assert (accept.decider, accept.role) == ("agent", "ceo")
    assert resolve(T, ctx("mission.accept", amount=400_001)).role == "owner"
    with pytest.raises(GateError, match="needs an amount"):
        resolve(T, ctx("spend.approve"))
    with pytest.raises(GateError):
        resolve(T, ctx("spend.approve", amount=-1))

    dept = resolve(T, ctx("knowledge.promote", facts={"scope": "department"}))
    assert (dept.decider, dept.role, dept.quorum) == ("agent", "tech_lead", 1)
    company = resolve(T, ctx("knowledge.promote", facts={"scope": "company"}))
    assert (company.decider, company.role, company.quorum, company.branch) == (
        "agent",
        "ceo",
        2,
        "company_scope",
    )
    assert set(company.allowed_roles) == {"ceo", "pm", "tech_lead"}

    deliver = resolve(T, ctx("project.deliver"))
    assert (deliver.decider, deliver.role) == ("human", "owner")  # V6
    with pytest.raises(GateError, match="not a kernel gate"):
        resolve(T, ctx("coffee.approve"))
    assert Resolution.from_dict(deliver.as_dict()) == deliver


@pytest.mark.parametrize("template", [SOFTWARE_STUDIO_V1, STARTUP_FLAT, INSTITUTIONAL])
def test_every_gate_resolves_on_every_builtin(template):
    for gate in GATES:
        r = resolve(template, ctx(gate, amount=1_000, company_id=uuid4()))
        assert r.decider in ("system", "agent", "human")
        if r.decider != "system":
            assert r.role and template.role(r.role).kind == r.decider
    assert resolve(INSTITUTIONAL, ctx("mission.accept")).quorum == 3
    assert resolve(INSTITUTIONAL, ctx("spend.approve", amount=200_000)).role == "committee"
    assert resolve(STARTUP_FLAT, ctx("spend.approve", amount=5_000)).decider == "human"


def test_escalation_of_follows_roles_and_gates():
    r = resolve(T, ctx("task.review", author="eng-1"))
    nxt = escalation_of(T, r)
    assert (nxt.decider, nxt.role, nxt.branch) == ("agent", "tech_lead", "escalated")
    assert nxt.separation == ("author!=reviewer",) and nxt.escalate_to is None
    assert (
        escalation_of(T, resolve(T, ctx("hire", subject=bind({"a": 1})))) is None
    )  # no escalate_to
    via_gate = Resolution.from_dict({**r.as_dict(), "escalate_to": "escalation.resolve"})
    g = escalation_of(T, via_gate)
    assert g.role == "tech_lead" and g.escalate_to == "owner"


# -- the envelope ---------------------------------------------------------------------------


def write(db, fact):
    f = store.append(db, fact)
    db.commit()
    return f


def test_agent_quorum_decision_with_separation_and_snapshot(db):
    company = uuid4()
    c = ctx(
        "plan.approve",
        company_id=company,
        proposer="pm-1",
        proposer_role="pm",
        subject={"project": "p1"},
    )
    requested, res = request(T, c, agent("pm-1"))
    req = write(db, requested)
    did = req.correlation_id
    state = load(db, did)
    assert state.status == "pending" and state.gate == "plan.approve" and state.resolution == res

    with pytest.raises(DecisionError, match="proposer may not approve"):
        sign(T, state, agent("pm-1"), role="pm", verdict="approve", reason="mine")
    with pytest.raises(DecisionError, match="may not decide"):
        sign(T, state, agent("eng-1"), role="engineer", verdict="approve", reason="x")
    with pytest.raises(DecisionError, match="needs a agent"):
        sign(T, state, human("owner-1"), role="tech_lead", verdict="approve", reason="x")
    with pytest.raises(DecisionError, match="never signs"):
        sign(T, state, Actor.system(), role="tech_lead", verdict="approve")
    with pytest.raises(DecisionError, match="verdict"):
        sign(T, state, agent("tl-1"), role="tech_lead", verdict="maybe")

    s1 = write(
        db,
        sign(T, state, agent("tl-1"), role="tech_lead", verdict="approve", reason="plan is sound"),
    )
    assert s1.kind == SIGNED and s1.payload["authority"]["grants"] == list(
        T.authority_defaults["tech_lead"]
    )
    assert s1.payload["authority"]["template_fingerprint"] == T.fingerprint()
    state = load(db, did)
    assert conclude(state) is None  # quorum 2: one approval is not enough
    with pytest.raises(DecisionError, match="already signed"):
        sign(T, state, agent("tl-1"), role="tech_lead", verdict="approve", reason="again")
    write(db, sign(T, state, agent("pm-2"), role="pm", verdict="approve", reason="agreed"))
    state = load(db, did)
    recorded = write(db, conclude(state))
    assert recorded.kind == RECORDED and recorded.payload["verdict_status"] == "approved"
    assert recorded.payload["signatures"] == [s.fact_id.hex for s in state.signatures]
    state = load(db, did)
    assert state.decided and state.status == "approved" and state.recorded_fact_id == recorded.id
    with pytest.raises(DecisionError, match="already approved"):
        sign(T, state, agent("tl-2"), role="tech_lead", verdict="approve", reason="late")
    assert conclude(state) is None
    kinds = [f.kind for f in store.by_correlation(db, did)]
    assert kinds == [REQUESTED, SIGNED, SIGNED, RECORDED]
    assert all(f.correlation_id == did for f in store.by_correlation(db, did))


def test_review_rework_and_reject_end_the_decision_at_once(db):
    company = uuid4()
    req = write(
        db,
        request(
            T,
            ctx("task.review", company_id=company, author="eng-1", subject={"task": "t1"}),
            agent("eng-1"),
        )[0],
    )
    state = load(db, req.correlation_id)
    with pytest.raises(DecisionError, match="author may not review"):
        sign(T, state, agent("eng-1"), role="qa", verdict="approve", reason="looks fine")
    with pytest.raises(DecisionError, match="requires a reason"):
        sign(T, state, agent("qa-1"), role="qa", verdict="rework")
    write(db, sign(T, state, agent("qa-1"), role="qa", verdict="rework", reason="tests missing"))
    state = load(db, req.correlation_id)
    recorded = write(db, conclude(state))
    assert (
        recorded.payload["verdict_status"] == "rework"
        and "tests missing" in recorded.payload["reason"]
    )

    req2 = write(
        db,
        request(T, ctx("task.cancel", company_id=company, subject={"task": "t2"}), agent("pm-1"))[
            0
        ],
    )
    state = load(db, req2.correlation_id)
    write(db, sign(T, state, agent("pm-1"), role="pm", verdict="reject", reason="keep it"))
    assert conclude(load(db, req2.correlation_id)).payload["verdict_status"] == "rejected"


def test_human_decision_is_bound_to_the_exact_action(db):
    company = uuid4()
    action = {"kind": "hire", "person": "new-eng", "salary": 1000}
    with pytest.raises(DecisionError, match="bind the concrete action"):
        request(T, ctx("hire", company_id=company, proposer="ceo-1"), agent("ceo-1"))
    requested, res = request(
        T, ctx("hire", company_id=company, proposer="ceo-1", subject=bind(action)), agent("ceo-1")
    )
    assert res.decider == "human" and res.role == "owner"
    req = write(db, requested)
    state = load(db, req.correlation_id)
    with pytest.raises(DecisionError, match="needs a human"):
        sign(T, state, agent("ceo-1"), role="owner", verdict="approve", reason="x")
    with pytest.raises(DecisionError, match="exact bound action"):
        sign(
            T,
            state,
            human("owner-1"),
            role="owner",
            verdict="approve",
            reason="ok",
            action={**action, "salary": 9999},
        )
    with pytest.raises(DecisionError, match="exact bound action"):
        sign(T, state, human("owner-1"), role="owner", verdict="approve", reason="ok")
    signed = write(
        db,
        sign(
            T,
            state,
            human("owner-1"),
            role="owner",
            verdict="approve",
            reason="approved",
            action=action,
        ),
    )
    assert signed.actor == human("owner-1") and signed.payload["authority"]["grants"] == ["*"]
    assert conclude(load(db, req.correlation_id)).payload["verdict_status"] == "approved"


def test_system_gate_records_its_check_and_refuses_judgments(db):
    company = uuid4()
    requested, recorded = system_record(
        T,
        ctx("spend.approve", company_id=company, amount=1_000),
        check="amount 1000 <= run default 5000",
    )
    r = write(db, requested)
    rec = write(db, recorded)
    assert rec.actor == Actor.system() and rec.payload["reason"].startswith("system check")
    state = load(db, r.correlation_id)
    assert state.status == "approved" and state.signatures == []
    with pytest.raises(DecisionError, match="may not decide"):
        system_record(T, ctx("spend.approve", company_id=company, amount=100_000), check="x")
    with pytest.raises(DecisionError, match="may not decide"):
        system_record(T, ctx("task.assign", company_id=company), check="x")
    with pytest.raises(DecisionError, match="takes no signatures"):
        sign(T, state, agent("pm-1"), role="pm", verdict="approve")


def test_escalation_after_timeout_moves_to_the_next_decider(db):
    company = uuid4()
    req = write(
        db,
        request(T, ctx("task.assign", company_id=company, subject={"task": "t"}), agent("pm-1"))[0],
    )
    state = load(db, req.correlation_id)
    assert escalate(T, state, now=state.requested_at + timedelta(hours=1)) is None  # within 4h
    esc = write(db, escalate(T, state, now=state.requested_at + timedelta(hours=5)))
    assert esc.kind == ESCALATED and esc.payload["resolution"]["role"] == "ceo"
    state = load(db, req.correlation_id)
    assert (
        state.escalations == 1
        and state.resolution.role == "ceo"
        and state.resolution.branch == "escalated"
    )
    with pytest.raises(DecisionError, match="may not decide"):
        sign(T, state, agent("tl-1"), role="tech_lead", verdict="approve")  # no longer theirs
    write(db, sign(T, state, agent("ceo-1"), role="ceo", verdict="approve", reason="assign it"))
    assert conclude(load(db, req.correlation_id)).payload["verdict_status"] == "approved"
    # forced escalation of a gate without timeout but with a target
    req2 = write(db, request(T, ctx("task.review", company_id=company, author="e"), agent("e"))[0])
    forced = escalate(T, load(db, req2.correlation_id), force=True)
    assert (
        forced.payload["reason"] == "forced" and forced.payload["resolution"]["role"] == "tech_lead"
    )
    # nothing to escalate to
    req3 = write(
        db, request(T, ctx("hire", company_id=company, subject=bind({"a": 1})), agent("ceo-1"))[0]
    )
    assert escalate(T, load(db, req3.correlation_id), force=True) is None
    assert (
        escalate(T, load(db, req3.correlation_id), now=datetime.now(UTC) + timedelta(days=9))
        is None
    )


# -- authority & tools ---------------------------------------------------------------------------


def test_grants_snapshot_and_tool_gate():
    assert has_grant(("*",), "anything")
    assert has_grant(("workspace.*",), "workspace.write") and not has_grant(
        ("workspace.*",), "hire"
    )
    assert has_grant(("task.assign",), "task.assign") and not has_grant(
        ("task.assign",), "task.assign.all"
    )
    snap = snapshot(T, "eng-1", "engineer")
    assert snap.as_dict()["grants"] == list(T.authority_defaults["engineer"])
    assert snapshot(T, "x", None).grants == ()

    registry = ToolRegistry().extend(
        [
            ToolDescriptor(WEATHER_TOOL, "READ", "workspace.read"),
            ToolDescriptor(
                WEATHER_TOOL.__class__("write_file", "w", {}), "WRITE", "workspace.write"
            ),
            ToolDescriptor(
                WEATHER_TOOL.__class__("hire", "h", {}), "HIGH_IMPACT", "hire.propose", "hire"
            ),
        ]
    )
    assert [d.name for d in authorized_tools(T, "qa", registry)] == ["get_weather"]
    assert [d.name for d in authorized_tools(T, "ceo", registry)] == ["hire"]
    assert len(authorized_tools(T, "owner", registry)) == 3
    gate = tool_gate(T, "qa", registry)
    assert gate(ToolCall("1", "get_weather", {})) is None
    assert "requires 'workspace.write'" in gate(ToolCall("2", "write_file", {}))
    assert "not registered" in gate(ToolCall("3", "fire", {}))


class Scripted:
    name = "scripted"

    def __init__(self):
        self.turn = 0

    async def complete(self, request):
        self.turn += 1
        if self.turn == 1:
            yield ToolCallEvent(ToolCall("c1", "get_weather", {"city": "Beijing"}))
            yield Completed("tool_use", Usage(1, 1), request.model, "scripted", request_id="s1")
        else:
            last = request.messages[-1]
            assert last.tool_result.is_error and "not authorized" in last.tool_result.content
            yield TextDelta("I may not use that tool.")
            yield Completed("end_turn", Usage(1, 1), request.model, "scripted", request_id="s2")

    async def embed(self, texts, *, model=None):
        raise NotImplementedError


async def test_loop_refuses_tools_outside_the_role_authority(db, tmp_path):
    registry = ToolRegistry().add(ToolDescriptor(WEATHER_TOOL, "WRITE", "workspace.write"))
    calls = []
    runner = AgentRunner(
        SessionLocal,
        Scripted(),
        BlobStore(tmp_path),
        weather_tools(calls),
        tool_gate=tool_gate(T, "qa", registry),  # QA holds no workspace.write
    )
    spec = new_spec(uuid4(), person_id="qa-1")
    outcome = await runner.start(spec)
    assert outcome.status == "completed" and calls == []  # the tool never ran
    facts = store.by_correlation(db, spec.run_id)
    done = next(f for f in facts if f.kind == R.TOOL_COMPLETED)
    assert done.payload["is_error"] and done.payload["refused"] is True
    assert "requires 'workspace.write'" in done.payload["result_preview"]
