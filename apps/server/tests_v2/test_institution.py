"""Institution schema (R3-a): templates are data, validated; built-ins are sound;
adoption and customisation are facts that only a human or a decision can write."""

from uuid import uuid4

import pytest

from eidolon.institution import (
    ADOPTED,
    BUILTIN,
    CHANGED,
    DEFAULT_TEMPLATE_ID,
    GATES,
    AmountTier,
    BudgetPolicy,
    GateRule,
    InstitutionError,
    InstitutionTemplate,
    Role,
    SchemaError,
    adopt,
    assert_valid,
    current,
    customize,
    fold,
    get,
    validate,
)
from eidolon.institution.templates import INSTITUTIONAL, SOFTWARE_STUDIO_V1, STARTUP_FLAT
from eidolon.kernel.facts import Actor, ActorKind
from eidolon.kernel.store import FactStore

store = FactStore()
OWNER = Actor(ActorKind.human, "owner-1")
CEO = Actor(ActorKind.agent, "ceo-1")


def studio_with(**gate_overrides) -> InstitutionTemplate:
    data = SOFTWARE_STUDIO_V1.as_dict()
    for gate, rule in gate_overrides.items():
        data["gates"][gate] = {**data["gates"].get(gate, {}), **rule}
    return InstitutionTemplate.from_dict(data)


# -- built-ins -----------------------------------------------------------------------


@pytest.mark.parametrize("template_id", sorted(BUILTIN))
def test_builtin_templates_validate_and_round_trip(template_id):
    template = get(template_id)
    assert validate(template) == []
    again = InstitutionTemplate.from_dict(template.as_dict())
    assert again == template and again.fingerprint() == template.fingerprint()
    assert set(template.gates) == set(GATES)
    assert template.owner() is not None and template.owner().kind == "human"


def test_default_template_matches_the_decisions():
    t = SOFTWARE_STUDIO_V1
    assert DEFAULT_TEMPLATE_ID == t.id == "software-studio-v1"
    assert (
        t.gates["project.deliver"].decider == "human" and t.gates["project.deliver"].role == "owner"
    )  # V6
    assert t.gates["task.review"].separation == ("author!=reviewer",)
    assert t.review_policy.rework_limit == 2
    assert t.gates["plan.approve"].quorum == 2
    tiers = t.gates["spend.approve"].amount_tiers
    assert [x.decider for x in tiers] == ["system", "agent", "human"] and tiers[
        -1
    ].max_amount is None
    assert t.gates["hire"].decider == "human" and "proposer!=approver" in t.gates["hire"].separation
    assert t.chain_of_command("engineer") == ["engineer", "tech_lead", "ceo", "owner"]
    assert "workspace.execute" in t.authority_defaults["engineer"]
    assert STARTUP_FLAT.gates["mission.accept"].decider == "human"
    assert INSTITUTIONAL.gates["mission.accept"].quorum == 3
    with pytest.raises(KeyError, match="unknown template"):
        get("nope")


# -- validation -------------------------------------------------------------------------


def test_validation_catches_each_rule():
    def problems(**overrides):
        return validate(studio_with(**overrides))

    assert any(
        "no rule" in p
        for p in validate(
            InstitutionTemplate.from_dict(
                {
                    **SOFTWARE_STUDIO_V1.as_dict(),
                    "gates": {
                        k: v
                        for k, v in SOFTWARE_STUDIO_V1.as_dict()["gates"].items()
                        if k != "hire"
                    },
                }
            )
        )
    )
    assert any("does not exist" in p for p in problems(**{"task.assign": {"role": "ghost"}}))
    assert any(
        "agent decider but role owner is human" in p
        for p in problems(**{"task.assign": {"decider": "agent", "role": "owner"}})
    )
    assert any(
        "human decider but role ceo is agent" in p
        for p in problems(**{"hire": {"decider": "human", "role": "ceo"}})
    )
    assert any(
        "system decides alone" in p
        for p in problems(**{"task.assign": {"decider": "system", "role": None, "quorum": 2}})
    )
    assert any(
        "escalation loops" in p
        for p in problems(
            **{
                "task.assign": {"escalate_to": "task.review"},
                "task.review": {"escalate_to": "task.assign"},
            }
        )
    )
    assert any(
        "neither a gate nor a role" in p
        for p in problems(**{"task.assign": {"escalate_to": "nowhere"}})
    )
    assert any("needs an escalate_to" in p for p in problems(**{"hire": {"timeout_s": 10}}))
    assert any(
        "ascending" in p
        for p in problems(
            **{
                "spend.approve": {
                    "amount_tiers": [
                        {"max_amount": 10, "decider": "system"},
                        {"max_amount": 5, "decider": "agent", "role": "pm"},
                        {"max_amount": None, "decider": "human", "role": "owner"},
                    ]
                }
            }
        )
    )
    assert any(
        "unbounded" in p
        for p in problems(
            **{"spend.approve": {"amount_tiers": [{"max_amount": 10, "decider": "system"}]}}
        )
    )
    assert any(
        "not a kernel gate" in p for p in problems(**{"coffee.approve": {"decider": "system"}})
    )

    # Owner reachability and cycles in the org chart
    data = SOFTWARE_STUDIO_V1.as_dict()
    data["roles"].append(
        {"code": "auditor", "title": "Auditor", "kind": "human", "reports_to": None, "decides": []}
    )
    assert any("exactly one root human" in p for p in validate(InstitutionTemplate.from_dict(data)))
    data = SOFTWARE_STUDIO_V1.as_dict()
    data["roles"][0]["reports_to"] = "ceo"  # owner reports to ceo: cycle, and no root
    probs = validate(InstitutionTemplate.from_dict(data))
    assert any("cycle" in p for p in probs) and any("root human" in p for p in probs)
    data = SOFTWARE_STUDIO_V1.as_dict()
    data["roles"].append(
        {"code": "guest", "title": "Guest", "kind": "human", "reports_to": "x", "decides": []}
    )
    assert any(
        "reports_to 'x' does not exist" in p for p in validate(InstitutionTemplate.from_dict(data))
    )
    data = SOFTWARE_STUDIO_V1.as_dict()
    data["authority_defaults"]["ghost"] = ["*"]
    assert any("unknown role 'ghost'" in p for p in validate(InstitutionTemplate.from_dict(data)))
    data = SOFTWARE_STUDIO_V1.as_dict()
    data["budget_policy"]["warn_ratio"] = 0
    assert any("warn_ratio" in p for p in validate(InstitutionTemplate.from_dict(data)))

    with pytest.raises(SchemaError):
        GateRule("sometimes")
    with pytest.raises(SchemaError):
        GateRule("agent", "pm", separation=("friends!=enemies",))
    with pytest.raises(SchemaError):
        Role("x y", "Bad", "agent")
    with pytest.raises(SchemaError, match="malformed"):
        InstitutionTemplate.from_dict({"id": "x"})
    with pytest.raises(ValueError, match="invalid"):
        assert_valid(studio_with(**{"task.assign": {"role": "ghost"}}))


def test_with_changes_merges_per_key_and_bumps_version():
    t = SOFTWARE_STUDIO_V1
    new = t.with_changes(
        {
            "gates": {"project.deliver": {"decider": "agent", "role": "ceo"}},
            "review_policy": {"rework_limit": 3},
        }
    )
    assert new.version == 2 and new.id == t.id
    assert (
        new.gates["project.deliver"].decider == "agent"
        and new.gates["project.deliver"].role == "ceo"
    )
    assert new.gates["task.review"] == t.gates["task.review"]  # untouched gates survive
    assert new.review_policy.rework_limit == 3 and new.review_policy.escalate_after == 2
    assert validate(new) == [] and new.fingerprint() != t.fingerprint()
    with pytest.raises(SchemaError, match="cannot change"):
        t.with_changes({"id": "other"})


# -- adoption & customisation as facts ----------------------------------------------------


def test_adoption_and_customisation_need_a_mandate_and_fold_to_current(db):
    company = uuid4()
    with pytest.raises(InstitutionError, match="decision_id"):
        adopt(company, CEO, "software-studio-v1")
    with pytest.raises(InstitutionError, match="decision_id"):
        adopt(company, Actor.system(), SOFTWARE_STUDIO_V1)
    assert current(db, company) is None

    adopted = store.append(db, adopt(company, OWNER, "software-studio-v1"))
    db.commit()
    assert (
        adopted.kind == ADOPTED
        and adopted.payload["fingerprint"] == SOFTWARE_STUDIO_V1.fingerprint()
    )
    state = current(db, company)
    assert (
        state.template == SOFTWARE_STUDIO_V1 and state.changes == 0 and state.fact_id == adopted.id
    )
    # adopting again is the same fact (idempotent), not a reset
    assert store.append(db, adopt(company, OWNER, "software-studio-v1")).id == adopted.id

    with pytest.raises(InstitutionError, match="reason"):
        customize(db, company, OWNER, {"review_policy": {"rework_limit": 1}}, reason=" ")
    with pytest.raises(InstitutionError, match="decision_id"):
        customize(db, company, CEO, {"review_policy": {"rework_limit": 1}}, reason="x")
    with pytest.raises(ValueError, match="invalid"):  # the change must still validate
        customize(db, company, OWNER, {"gates": {"task.assign": {"role": "ghost"}}}, reason="oops")
    with pytest.raises(InstitutionError, match="cannot change"):
        customize(db, company, OWNER, {"id": "y"}, reason="rename")

    decision = uuid4()
    changed = store.append(
        db,
        customize(
            db,
            company,
            CEO,
            {"gates": {"project.deliver": {"decider": "agent", "role": "ceo"}}},
            reason="owner delegated delivery acceptance",
            decision_id=decision,
        ),
    )
    db.commit()
    assert changed.kind == CHANGED and changed.decision_id == decision
    assert changed.causation_id == adopted.id and changed.payload["version"] == 2
    assert changed.payload["previous_fingerprint"] == SOFTWARE_STUDIO_V1.fingerprint()
    state = current(db, company)
    assert state.template.version == 2 and state.changes == 1 and state.fact_id == changed.id
    assert state.template.gates["project.deliver"].role == "ceo"
    assert state.adopted_fact_id == adopted.id

    # another company is untouched; folding is pure
    assert current(db, uuid4()) is None
    assert fold([changed]) is None  # a change without an adoption is ignored
    assert fold([adopted, changed]).template == state.template


def test_unknown_builtin_id_is_rejected():
    with pytest.raises(InstitutionError, match="unknown template"):
        adopt(uuid4(), OWNER, "no-such-template")
    custom = InstitutionTemplate(
        id="my-co",
        version=1,
        name="Mine",
        roles=(Role("owner", "Owner", "human"), Role("dev", "Dev", "agent", reports_to="owner")),
        gates={g: GateRule("human", "owner") for g in GATES},
        authority_defaults={"owner": ("*",), "dev": ("workspace.read",)},
        budget_policy=BudgetPolicy(1000, 100),
    )
    assert validate(custom) == []
    fact = adopt(uuid4(), OWNER, custom)
    assert fact.payload["template_id"] == "my-co"
    tiers = GateRule(
        "system", amount_tiers=(AmountTier(10, "system"), AmountTier(None, "human", "owner"))
    )
    assert tiers.amount_tiers[1].max_amount is None
