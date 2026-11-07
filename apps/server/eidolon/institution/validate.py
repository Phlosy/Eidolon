"""Template validation (§5.1): a template is accepted only if the kernel can
resolve every gate from it without guessing.

    every kernel gate has a rule with a decider
    agent/human deciders name an existing role of the matching kind
    a human decider is reachable: the role, or someone above it, is the Owner
    exactly one Owner (root human role); reports_to forms a tree, no cycles
    escalation targets exist (gate or role) and do not loop
    separation / quorum make sense for the decider (system decides alone)
    amount tiers are ascending and end unbounded; each tier is itself resolvable
    authority defaults name existing roles
"""

from eidolon.institution.schema import GATES, AmountTier, GateRule, InstitutionTemplate


def validate(template: InstitutionTemplate) -> list[str]:
    problems: list[str] = []
    roles = {r.code: r for r in template.roles}
    if len(roles) != len(template.roles):
        problems.append("duplicate role codes")

    owner = template.owner()
    if owner is None:
        problems.append("exactly one root human role (the Owner) is required")

    for role in template.roles:
        if role.reports_to is not None and role.reports_to not in roles:
            problems.append(f"role {role.code}: reports_to {role.reports_to!r} does not exist")
        chain = template.chain_of_command(role.code)
        last = roles.get(chain[-1])
        if last is not None and last.reports_to is not None:
            problems.append(f"role {role.code}: reports_to chain has a cycle")
        for gate in role.decides:
            if gate not in GATES:
                problems.append(f"role {role.code}: decides unknown gate {gate!r}")

    for gate in GATES:
        rule = template.gates.get(gate)
        if rule is None:
            problems.append(f"gate {gate}: no rule")
            continue
        problems.extend(f"gate {gate}: {p}" for p in _check_rule(template, rule, roles, owner))
    for gate in template.gates:
        if gate not in GATES:
            problems.append(f"gate {gate}: not a kernel gate")

    # escalation loops: gate -> gate edges
    for gate, rule in template.gates.items():
        seen = [gate]
        target = rule.escalate_to
        while target in template.gates:
            if target in seen:
                problems.append(f"gate {gate}: escalation loops ({' -> '.join(seen + [target])})")
                break
            seen.append(target)
            target = template.gates[target].escalate_to

    for code in template.authority_defaults:
        if code not in roles:
            problems.append(f"authority_defaults: unknown role {code!r}")
    if not 0 < template.budget_policy.warn_ratio <= 1:
        problems.append("budget_policy.warn_ratio must be in (0, 1]")
    if template.budget_policy.max_concurrent_runs < 1:
        problems.append("budget_policy.max_concurrent_runs must be >= 1")
    if template.budget_policy.daily_cap is not None and template.budget_policy.daily_cap <= 0:
        problems.append("budget_policy.daily_cap must be positive or null")
    if template.budget_policy.project_default <= 0 or template.budget_policy.run_default <= 0:
        problems.append("budget defaults must be positive")
    if template.review_policy.rework_limit < 0 or template.review_policy.escalate_after < 0:
        problems.append("review_policy limits must be >= 0")
    return problems


def _check_rule(template, rule: GateRule, roles, owner) -> list[str]:
    problems: list[str] = []
    problems.extend(_check_decider(template, rule.decider, rule.role, roles, owner))
    if rule.decider == "system":
        if rule.quorum != 1 or rule.separation:
            problems.append("system decides alone: no quorum or separation")
    if rule.escalate_to is not None and rule.escalate_to not in roles:
        if rule.escalate_to not in template.gates:
            problems.append(f"escalate_to {rule.escalate_to!r} is neither a gate nor a role")
    if rule.timeout_s is not None and rule.timeout_s <= 0:
        problems.append("timeout_s must be positive")
    if rule.timeout_s is not None and rule.escalate_to is None:
        problems.append("a timeout needs an escalate_to")
    tiers = rule.amount_tiers
    if tiers:
        if tiers[-1].max_amount is not None:
            problems.append("the last amount tier must be unbounded (max_amount null)")
        prev = -1
        for tier in tiers[:-1]:
            if tier.max_amount is None or tier.max_amount <= prev:
                problems.append("amount tiers must have ascending max_amount")
                break
            prev = tier.max_amount
        for tier in tiers:
            problems.extend(
                f"tier {tier.max_amount}: {p}"
                for p in _check_decider(template, tier.decider, tier.role, roles, owner)
            )
    return problems


def _check_decider(template, decider, role_code, roles, owner) -> list[str]:
    if decider == "system":
        return ["system decider does not take a role"] if role_code else []
    if role_code is None:
        return [f"{decider} decider needs a role"]
    role = roles.get(role_code)
    if role is None:
        return [f"role {role_code!r} does not exist"]
    if decider == "agent" and role.kind != "agent":
        return [f"agent decider but role {role_code} is {role.kind}"]
    if decider == "human":
        if role.kind != "human":
            return [f"human decider but role {role_code} is {role.kind}"]
        if owner is not None and owner.code not in template.chain_of_command(role_code):
            return [f"human role {role_code} cannot reach the Owner"]
    return []


def assert_valid(template: InstitutionTemplate) -> InstitutionTemplate:
    problems = validate(template)
    if problems:
        raise ValueError(
            f"template {template.id} v{template.version} invalid: " + "; ".join(problems)
        )
    return template


__all__ = ["AmountTier", "assert_valid", "validate"]
