"""``software-studio-v1`` — the default institution that ships (§5.3).

Owner (human) · CEO · PM · Tech Lead · Engineer · QA (also Reviewer).
"""

from eidolon.institution.schema import (
    AmountTier,
    BudgetPolicy,
    GateRule,
    InstitutionTemplate,
    Retention,
    ReviewPolicy,
    Role,
)

PROJECT_DEFAULT = 200_000  # money units; see money layer (R4) for the currency
RUN_DEFAULT = 5_000

SOFTWARE_STUDIO_V1 = InstitutionTemplate(
    id="software-studio-v1",
    version=1,
    name="Software studio",
    description=(
        "A small software studio: the Owner is the only human; CEO accepts missions, PM and "
        "Tech Lead plan and assign, Engineers build, QA reviews. Delivery and money above the "
        "defaults go back to the Owner."
    ),
    roles=(
        Role("owner", "Owner", "human"),
        Role("ceo", "CEO", "agent", reports_to="owner", decides=("mission.accept", "hire")),
        Role(
            "pm",
            "Product Manager",
            "agent",
            reports_to="ceo",
            decides=("plan.approve", "task.assign"),
        ),
        Role(
            "tech_lead",
            "Tech Lead",
            "agent",
            reports_to="ceo",
            decides=("plan.approve", "task.assign"),
        ),
        Role("engineer", "Engineer", "agent", reports_to="tech_lead"),
        Role("qa", "QA / Reviewer", "agent", reports_to="tech_lead", decides=("task.review",)),
    ),
    gates={
        # CEO accepts; a budget above 2× the project default goes to the Owner.
        "mission.accept": GateRule(
            "agent",
            "ceo",
            amount_tiers=(
                AmountTier(2 * PROJECT_DEFAULT, "agent", "ceo"),
                AmountTier(None, "human", "owner"),
            ),
            require_reason=True,
            timeout_s=24 * 3600,
            escalate_to="owner",
        ),
        # PM proposes, Tech Lead countersigns (quorum 2 across pm + tech_lead); big plans → CEO.
        "plan.approve": GateRule(
            "agent",
            "tech_lead",
            quorum=2,
            separation=("proposer!=approver",),
            conditions={
                "co_signers": ["pm", "tech_lead"],
                "escalate_if": {"tasks_gt": 20, "has_purchase": True},
            },
            escalate_to="ceo",
            timeout_s=12 * 3600,
        ),
        # The responsible lead assigns; the system only checks readiness and authority.
        "task.assign": GateRule("agent", "tech_lead", timeout_s=4 * 3600, escalate_to="ceo"),
        # Reviewer must not be the author; REWORK limit 2 then Tech Lead, then Owner.
        "task.review": GateRule(
            "agent",
            "qa",
            separation=("author!=reviewer",),
            require_reason=True,
            escalate_to="tech_lead",
            timeout_s=8 * 3600,
        ),
        "task.rework": GateRule(
            "agent", "qa", require_reason=True, escalate_to="tech_lead", timeout_s=8 * 3600
        ),
        "task.cancel": GateRule(
            "agent", "pm", require_reason=True, escalate_to="ceo", timeout_s=24 * 3600
        ),
        # V6: the Owner accepts deliveries at launch; customisable to the CEO.
        "project.deliver": GateRule("human", "owner", require_reason=False),
        # Money: ≤ run default → system; ≤ 80% of project budget → PM; above → Owner.
        "spend.approve": GateRule(
            "system",
            amount_tiers=(
                AmountTier(RUN_DEFAULT, "system"),
                AmountTier(int(PROJECT_DEFAULT * 0.8), "agent", "pm"),
                AmountTier(None, "human", "owner"),
            ),
        ),
        "hire": GateRule("human", "owner", separation=("proposer!=approver",), require_reason=True),
        "fire": GateRule("human", "owner", separation=("proposer!=approver",), require_reason=True),
        "purchase": GateRule(
            "human", "owner", separation=("proposer!=approver",), require_reason=True
        ),
        # Department level: Tech Lead; company level: CEO plus one other manager.
        "knowledge.promote": GateRule(
            "agent",
            "tech_lead",
            conditions={
                "company_scope": {
                    "decider": "agent",
                    "role": "ceo",
                    "quorum": 2,
                    "co_signers": ["pm", "tech_lead"],
                }
            },
        ),
        # Overdue → the responsible lead; 24h silence → Owner.
        "escalation.resolve": GateRule(
            "agent", "tech_lead", timeout_s=24 * 3600, escalate_to="owner"
        ),
    },
    authority_defaults={
        "owner": ("*",),
        "ceo": (
            "mission.accept",
            "hire.propose",
            "fire.propose",
            "purchase.propose",
            "spend.approve",
            "knowledge.promote",
        ),
        "pm": ("plan.propose", "task.assign", "task.cancel", "spend.approve"),
        "tech_lead": (
            "plan.approve",
            "task.assign",
            "task.review",
            "knowledge.promote",
            "workspace.execute",
        ),
        "engineer": ("workspace.read", "workspace.write", "workspace.execute", "task.complete"),
        "qa": ("workspace.read", "workspace.execute", "task.review", "task.rework"),
    },
    budget_policy=BudgetPolicy(
        project_default=PROJECT_DEFAULT, run_default=RUN_DEFAULT, warn_ratio=0.8, hard_stop=True
    ),
    review_policy=ReviewPolicy(rework_limit=2, escalate_after=2),
    retention=Retention(transcript_days=90, fact_forever=True),
)
