"""Institution-as-config (v2-architecture §5.1): how a company runs is data.

An ``InstitutionTemplate`` says which roles exist, who decides at each of the
kernel's fixed gates (and how: quorum, separation of duties, amount tiers,
timeouts, escalation), what authority each role holds by default, and the
budget / review / retention policies. The kernel resolves gates from this
(R3-b) and never hard-codes any one company's way of working.

Pure data: no database, no I/O. ``validate.py`` checks a template; ``templates``
holds the built-ins; ``adoption.py`` records which version a company runs.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

# The gate list is fixed by the kernel (§5.2); templates only decide *who* decides.
GATES: tuple[str, ...] = (
    "mission.accept",
    "plan.approve",
    "task.assign",
    "task.review",
    "task.rework",
    "task.cancel",
    "task.triage",  # R7-d: what to do with a failed / blocked task
    "project.deliver",
    "spend.approve",
    "hire",
    "fire",
    "purchase",
    "knowledge.promote",
    "escalation.resolve",
)
DECIDERS = ("system", "agent", "human")
SEPARATIONS = ("author!=reviewer", "proposer!=approver")
ROLE_KINDS = ("human", "agent")


class SchemaError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Role:
    code: str
    title: str
    kind: str  # human | agent
    reports_to: str | None = None
    decides: tuple[str, ...] = ()  # gates this role may decide (informational; rules below bind)

    def __post_init__(self) -> None:
        if not self.code or not self.code.replace("_", "").replace("-", "").isalnum():
            raise SchemaError(f"bad role code {self.code!r}")
        if self.kind not in ROLE_KINDS:
            raise SchemaError(f"role {self.code}: kind must be one of {ROLE_KINDS}")


@dataclass(frozen=True, slots=True)
class AmountTier:
    """Up to ``max_amount`` (inclusive; None = unbounded), ``decider``/``role`` decide."""

    max_amount: int | None
    decider: str
    role: str | None = None


@dataclass(frozen=True, slots=True)
class GateRule:
    decider: str  # system | agent | human
    role: str | None = None  # who (agent/human deciders)
    quorum: int = 1  # how many of that role must concur (会签)
    separation: tuple[str, ...] = ()  # duties that must be different people
    amount_tiers: tuple[AmountTier, ...] = ()  # money gates: decider by amount
    timeout_s: int | None = None
    escalate_to: str | None = None  # a gate name or a role code
    require_reason: bool = False
    conditions: Mapping[str, Any] = field(default_factory=dict)  # template-specific knobs
    advisor: str | None = None  # role that briefs a human decider (default: their direct report)

    def __post_init__(self) -> None:
        if self.decider not in DECIDERS:
            raise SchemaError(f"decider must be one of {DECIDERS}, got {self.decider!r}")
        if self.quorum < 1:
            raise SchemaError("quorum must be >= 1")
        for s in self.separation:
            if s not in SEPARATIONS:
                raise SchemaError(f"unknown separation rule {s!r}; known: {SEPARATIONS}")


# Budgets are in *units*: the minor unit (分 / cents) of the company's accounting
# currency. The ledger keeps micro-units (1 000 000 per 1.00); this is the only bridge.
MICRO_PER_UNIT = 10_000


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    project_default: int  # units (see MICRO_PER_UNIT)
    run_default: int  # units: one run's allowance, and the tranche spend is approved in
    warn_ratio: float = 0.8  # of the project budget: above it every tranche needs spend.approve
    hard_stop: bool = True  # False: decisions are still opened but never block a run
    daily_cap: int | None = None  # units per company per UTC day; None = no cap
    max_concurrent_runs: int = 4  # per company: tasks in progress at once


@dataclass(frozen=True, slots=True)
class ReviewPolicy:
    rework_limit: int = 2
    escalate_after: int = 2


@dataclass(frozen=True, slots=True)
class Retention:
    transcript_days: int = 90
    fact_forever: bool = True


@dataclass(frozen=True, slots=True)
class InstitutionTemplate:
    id: str
    version: int
    name: str
    roles: tuple[Role, ...]
    gates: Mapping[str, GateRule]
    authority_defaults: Mapping[str, tuple[str, ...]]  # role -> grants
    budget_policy: BudgetPolicy
    review_policy: ReviewPolicy = ReviewPolicy()
    retention: Retention = Retention()
    description: str = ""

    # -- lookups -----------------------------------------------------------------

    def role(self, code: str) -> Role | None:
        return next((r for r in self.roles if r.code == code), None)

    def advisor_for(self, gate: str, decider_role: str | None) -> str | None:
        """Who briefs a human decider at ``gate``: the rule's ``advisor``, else the
        first agent role reporting to the decider, else the CEO-like root agent role."""
        rule = self.gates.get(gate)
        if rule is not None and rule.advisor:
            return rule.advisor
        reports = [r for r in self.roles if r.kind == "agent" and r.reports_to == decider_role]
        if reports:
            return reports[0].code
        root_humans = {r.code for r in self.roles if r.kind == "human"}
        tops = [r for r in self.roles if r.kind == "agent" and r.reports_to in root_humans]
        return tops[0].code if tops else None

    def owner(self) -> Role | None:
        """The root human role: human kind, reports to nobody."""
        roots = [r for r in self.roles if r.kind == "human" and r.reports_to is None]
        return roots[0] if len(roots) == 1 else None

    def chain_of_command(self, code: str) -> list[str]:
        """``code`` and everyone above it, in order; stops at a cycle."""
        out: list[str] = []
        current: str | None = code
        while current is not None and current not in out:
            out.append(current)
            role = self.role(current)
            current = role.reports_to if role else None
        return out

    # -- serialisation ---------------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "name": self.name,
            "description": self.description,
            "roles": [
                {
                    "code": r.code,
                    "title": r.title,
                    "kind": r.kind,
                    "reports_to": r.reports_to,
                    "decides": list(r.decides),
                }
                for r in self.roles
            ],
            "gates": {
                name: {
                    "decider": g.decider,
                    "role": g.role,
                    "quorum": g.quorum,
                    "separation": list(g.separation),
                    "amount_tiers": [
                        {"max_amount": t.max_amount, "decider": t.decider, "role": t.role}
                        for t in g.amount_tiers
                    ],
                    "timeout_s": g.timeout_s,
                    "escalate_to": g.escalate_to,
                    "require_reason": g.require_reason,
                    "conditions": dict(g.conditions),
                    "advisor": g.advisor,
                }
                for name, g in self.gates.items()
            },
            "authority_defaults": {k: list(v) for k, v in self.authority_defaults.items()},
            "budget_policy": {
                "project_default": self.budget_policy.project_default,
                "run_default": self.budget_policy.run_default,
                "warn_ratio": self.budget_policy.warn_ratio,
                "hard_stop": self.budget_policy.hard_stop,
                "daily_cap": self.budget_policy.daily_cap,
                "max_concurrent_runs": self.budget_policy.max_concurrent_runs,
            },
            "review_policy": {
                "rework_limit": self.review_policy.rework_limit,
                "escalate_after": self.review_policy.escalate_after,
            },
            "retention": {
                "transcript_days": self.retention.transcript_days,
                "fact_forever": self.retention.fact_forever,
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "InstitutionTemplate":
        try:
            return cls(
                id=data["id"],
                version=int(data["version"]),
                name=data["name"],
                description=data.get("description", ""),
                roles=tuple(
                    Role(
                        r["code"],
                        r["title"],
                        r["kind"],
                        r.get("reports_to"),
                        tuple(r.get("decides", ())),
                    )
                    for r in data["roles"]
                ),
                gates={
                    name: GateRule(
                        decider=g["decider"],
                        role=g.get("role"),
                        quorum=int(g.get("quorum", 1)),
                        separation=tuple(g.get("separation", ())),
                        amount_tiers=tuple(
                            AmountTier(t.get("max_amount"), t["decider"], t.get("role"))
                            for t in g.get("amount_tiers", ())
                        ),
                        timeout_s=g.get("timeout_s"),
                        escalate_to=g.get("escalate_to"),
                        require_reason=bool(g.get("require_reason", False)),
                        conditions=dict(g.get("conditions", {})),
                        advisor=g.get("advisor"),
                    )
                    for name, g in data["gates"].items()
                },
                authority_defaults={
                    k: tuple(v) for k, v in data.get("authority_defaults", {}).items()
                },
                budget_policy=BudgetPolicy(**data["budget_policy"]),
                review_policy=ReviewPolicy(**data.get("review_policy", {})),
                retention=Retention(**data.get("retention", {})),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SchemaError(f"malformed template: {exc!r}") from exc

    def fingerprint(self) -> str:
        body = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def with_changes(self, changes: Mapping[str, Any], *, version: int | None = None):
        """A new version with ``changes`` merged in (gates / authority_defaults /
        policies merge per key; roles replace wholesale)."""
        data = self.as_dict()
        for key, value in changes.items():
            if key in {
                "gates",
                "authority_defaults",
                "budget_policy",
                "review_policy",
                "retention",
            }:
                merged = dict(data[key])
                if key == "gates":
                    for gate, rule in value.items():
                        merged[gate] = {**merged.get(gate, {}), **rule}
                else:
                    merged.update(value)
                data[key] = merged
            elif key in {"roles", "name", "description"}:
                data[key] = value
            else:
                raise SchemaError(f"cannot change {key!r}")
        data["version"] = version if version is not None else self.version + 1
        return InstitutionTemplate.from_dict(data)


def gate_names(extra: Sequence[str] = ()) -> tuple[str, ...]:
    return GATES + tuple(extra)
