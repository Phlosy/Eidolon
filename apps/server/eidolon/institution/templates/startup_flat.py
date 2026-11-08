"""``startup-flat`` — the Owner is also the CEO; one reviewer; few gates."""

from eidolon.institution.schema import (
    AmountTier,
    BudgetPolicy,
    GateRule,
    InstitutionTemplate,
    Retention,
    ReviewPolicy,
    Role,
)

STARTUP_FLAT = InstitutionTemplate(
    id="startup-flat",
    version=1,
    name="Startup (flat)",
    description="The Owner runs the company directly; a lead assigns and reviews; everyone builds.",
    roles=(
        Role("owner", "Owner / CEO", "human"),
        Role(
            "lead",
            "Lead",
            "agent",
            reports_to="owner",
            decides=("plan.approve", "task.assign", "task.review"),
        ),
        Role("builder", "Builder", "agent", reports_to="lead"),
    ),
    gates={
        "mission.accept": GateRule("human", "owner"),
        "plan.approve": GateRule("agent", "lead", escalate_to="owner", timeout_s=12 * 3600),
        "task.assign": GateRule("agent", "lead", escalate_to="owner", timeout_s=4 * 3600),
        "task.review": GateRule(
            "agent",
            "lead",
            separation=("author!=reviewer",),
            escalate_to="owner",
            timeout_s=8 * 3600,
        ),
        "task.rework": GateRule("agent", "lead", escalate_to="owner", timeout_s=8 * 3600),
        "task.cancel": GateRule(
            "agent", "lead", require_reason=True, escalate_to="owner", timeout_s=24 * 3600
        ),
        "project.deliver": GateRule("human", "owner"),
        "spend.approve": GateRule(
            "system",
            amount_tiers=(AmountTier(2_000, "system"), AmountTier(None, "human", "owner")),
        ),
        "hire": GateRule("human", "owner", require_reason=True),
        "fire": GateRule("human", "owner", require_reason=True),
        "purchase": GateRule("human", "owner", require_reason=True),
        "knowledge.promote": GateRule("agent", "lead"),
        "escalation.resolve": GateRule("human", "owner"),
    },
    authority_defaults={
        "owner": ("*",),
        "lead": (
            "plan.approve",
            "task.assign",
            "task.review",
            "task.rework",
            "task.cancel",
            "knowledge.promote",
            "workspace.execute",
            "workspace.read",
        ),
        "builder": ("workspace.read", "workspace.write", "workspace.execute", "task.complete"),
    },
    budget_policy=BudgetPolicy(
        project_default=50_000, run_default=2_000, warn_ratio=0.9, hard_stop=True
    ),
    review_policy=ReviewPolicy(rework_limit=1, escalate_after=1),
    retention=Retention(transcript_days=30),
)
