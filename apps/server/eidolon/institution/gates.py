"""Gate resolution (§1.2, §5): from a template and a situation, *who decides*.

    resolve(template, ctx) -> Resolution
        system  : the kernel proceeds on its own checks (never a judgment)
        agent   : an agent holding ``role`` (or a co-signer role) decides, ``quorum`` of them
        human   : a human holding ``role`` confirms the bound action

The resolution carries everything the decision envelope needs (quorum, who may
sign, separation of duties, reason required, timeout and where it escalates) and
the template fingerprint it came from, so a decision stays explainable after the
institution changes.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from eidolon.institution.schema import GATES, AmountTier, GateRule, InstitutionTemplate


class GateError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GateContext:
    gate: str
    company_id: UUID
    proposer: str | None = None  # person who raised the matter (separation: proposer!=approver)
    proposer_role: str | None = None
    author: str | None = None  # person whose work is judged (separation: author!=reviewer)
    amount: int | None = None  # money gates
    subject: Mapping[str, Any] = field(default_factory=dict)  # what is being decided
    facts: Mapping[str, Any] = field(default_factory=dict)  # tasks_count, has_purchase, scope, ...


@dataclass(frozen=True, slots=True)
class Resolution:
    gate: str
    decider: str
    role: str | None
    quorum: int
    allowed_roles: tuple[str, ...]  # roles whose holders may sign
    separation: tuple[str, ...]
    require_reason: bool
    timeout_s: int | None
    escalate_to: str | None
    template_id: str
    template_version: int
    template_fingerprint: str
    tier_max: int | None = None  # the amount tier that applied, if any
    branch: str = "rule"  # rule | tier | escalate_if | company_scope

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "decider": self.decider,
            "role": self.role,
            "quorum": self.quorum,
            "allowed_roles": list(self.allowed_roles),
            "separation": list(self.separation),
            "require_reason": self.require_reason,
            "timeout_s": self.timeout_s,
            "escalate_to": self.escalate_to,
            "template_id": self.template_id,
            "template_version": self.template_version,
            "template_fingerprint": self.template_fingerprint,
            "tier_max": self.tier_max,
            "branch": self.branch,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Resolution":
        return cls(
            gate=d["gate"],
            decider=d["decider"],
            role=d.get("role"),
            quorum=int(d.get("quorum", 1)),
            allowed_roles=tuple(d.get("allowed_roles", ())),
            separation=tuple(d.get("separation", ())),
            require_reason=bool(d.get("require_reason", False)),
            timeout_s=d.get("timeout_s"),
            escalate_to=d.get("escalate_to"),
            template_id=d["template_id"],
            template_version=int(d["template_version"]),
            template_fingerprint=d["template_fingerprint"],
            tier_max=d.get("tier_max"),
            branch=d.get("branch", "rule"),
        )


def resolve(template: InstitutionTemplate, ctx: GateContext) -> Resolution:
    if ctx.gate not in GATES:
        raise GateError(f"{ctx.gate!r} is not a kernel gate")
    rule = template.gates.get(ctx.gate)
    if rule is None:
        raise GateError(f"template {template.id} has no rule for {ctx.gate}")
    fp = template.fingerprint()

    def build(
        decider: str,
        role: str | None,
        *,
        quorum: int = rule.quorum,
        branch: str = "rule",
        tier_max: int | None = None,
        co_signers: tuple[str, ...] = (),
    ) -> Resolution:
        allowed = tuple(dict.fromkeys(((role,) if role else ()) + co_signers))
        return Resolution(
            gate=ctx.gate,
            decider=decider,
            role=role,
            quorum=quorum if decider != "system" else 1,
            allowed_roles=allowed,
            separation=rule.separation if decider != "system" else (),
            require_reason=rule.require_reason,
            timeout_s=rule.timeout_s,
            escalate_to=rule.escalate_to,
            template_id=template.id,
            template_version=template.version,
            template_fingerprint=fp,
            tier_max=tier_max,
            branch=branch,
        )

    conditions = dict(rule.conditions)
    co_signers = tuple(conditions.get("co_signers", ()))

    # 1. knowledge.promote to company scope (and similar scope-specific overrides)
    scope_rule = conditions.get("company_scope")
    if scope_rule and ctx.facts.get("scope") == "company":
        return build(
            scope_rule.get("decider", rule.decider),
            scope_rule.get("role", rule.role),
            quorum=int(scope_rule.get("quorum", rule.quorum)),
            branch="company_scope",
            co_signers=tuple(scope_rule.get("co_signers", ())),
        )

    # 2. escalate_if: a situation too big for the usual decider goes one level up
    escalate_if = conditions.get("escalate_if") or {}
    if _triggered(escalate_if, ctx.facts) and rule.escalate_to:
        target = template.role(rule.escalate_to)
        if target is not None:
            return build(target.kind, target.code, quorum=1, branch="escalate_if")

    # 3. amount tiers
    if rule.amount_tiers:
        if ctx.amount is None:
            raise GateError(f"gate {ctx.gate} needs an amount")
        if ctx.amount < 0:
            raise GateError("amount must be >= 0")
        tier = _tier_for(rule.amount_tiers, ctx.amount)
        return build(
            tier.decider,
            tier.role,
            quorum=rule.quorum if tier.decider == rule.decider else 1,
            branch="tier",
            tier_max=tier.max_amount,
            co_signers=co_signers if tier.decider == rule.decider else (),
        )

    # 4. the plain rule
    return build(rule.decider, rule.role, co_signers=co_signers)


def _tier_for(tiers: tuple[AmountTier, ...], amount: int) -> AmountTier:
    for tier in tiers:
        if tier.max_amount is None or amount <= tier.max_amount:
            return tier
    return tiers[-1]


def _triggered(escalate_if: Mapping[str, Any], facts: Mapping[str, Any]) -> bool:
    for key, threshold in escalate_if.items():
        if key.endswith("_gt"):
            value = facts.get(key[:-3])
            if value is not None and value > threshold:
                return True
        elif facts.get(key) == threshold:
            return True
    return False


def escalation_of(template: InstitutionTemplate, resolution: Resolution) -> Resolution | None:
    """Where an unanswered decision goes: the rule's ``escalate_to`` (a role → that
    role decides alone; a gate → that gate's own rule), or nowhere."""
    target = resolution.escalate_to
    if target is None:
        return None
    base = {
        "gate": resolution.gate,
        "template_id": resolution.template_id,
        "template_version": resolution.template_version,
        "template_fingerprint": resolution.template_fingerprint,
        "branch": "escalated",
    }
    role = template.role(target)
    if role is not None:
        return Resolution(
            decider=role.kind,
            role=role.code,
            quorum=1,
            allowed_roles=(role.code,),
            separation=resolution.separation,
            require_reason=resolution.require_reason,
            timeout_s=None,
            escalate_to=None,
            **base,
        )
    rule: GateRule | None = template.gates.get(target)
    if rule is None:
        return None
    return Resolution(
        decider=rule.decider,
        role=rule.role,
        quorum=rule.quorum,
        allowed_roles=tuple(
            dict.fromkeys(
                ((rule.role,) if rule.role else ()) + tuple(rule.conditions.get("co_signers", ()))
            )
        ),
        separation=rule.separation,
        require_reason=rule.require_reason,
        timeout_s=rule.timeout_s,
        escalate_to=rule.escalate_to,
        **base,
    )
