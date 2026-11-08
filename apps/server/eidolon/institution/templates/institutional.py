"""``institutional`` — 请示 → 审批 → 会签 → 批复 → 归档; "三重一大" needs a quorum of 3."""

from eidolon.institution.schema import (
    AmountTier,
    BudgetPolicy,
    GateRule,
    InstitutionTemplate,
    Retention,
    ReviewPolicy,
    Role,
)

INSTITUTIONAL = InstitutionTemplate(
    id="institutional",
    version=1,
    name="Institutional (request–approve–countersign–reply–archive)",
    description=(
        "Every decision is requested upward, approved by the department head, countersigned "
        "by the committee for major matters (quorum 3), replied and archived as facts."
    ),
    roles=(
        Role("director", "Director (Owner)", "human"),
        Role("deputy", "Deputy Director", "human", reports_to="director"),
        Role(
            "dept_head",
            "Department Head",
            "agent",
            reports_to="deputy",
            decides=("plan.approve", "task.assign", "task.review"),
        ),
        Role(
            "committee",
            "Committee Member",
            "agent",
            reports_to="deputy",
            decides=("mission.accept", "spend.approve"),
        ),
        Role("staff", "Staff", "agent", reports_to="dept_head"),
        Role("auditor", "Auditor", "agent", reports_to="director", decides=("task.review",)),
    ),
    gates={
        "mission.accept": GateRule(
            "agent",
            "committee",
            quorum=3,
            require_reason=True,
            escalate_to="deputy",
            timeout_s=72 * 3600,
        ),
        "plan.approve": GateRule(
            "agent",
            "dept_head",
            separation=("proposer!=approver",),
            require_reason=True,
            escalate_to="deputy",
            timeout_s=48 * 3600,
        ),
        "task.assign": GateRule("agent", "dept_head", escalate_to="deputy", timeout_s=24 * 3600),
        "task.review": GateRule(
            "agent",
            "auditor",
            separation=("author!=reviewer",),
            require_reason=True,
            escalate_to="deputy",
            timeout_s=48 * 3600,
        ),
        "task.rework": GateRule(
            "agent", "auditor", require_reason=True, escalate_to="dept_head", timeout_s=48 * 3600
        ),
        "task.cancel": GateRule("human", "deputy", require_reason=True),
        "task.triage": GateRule(
            "human", "deputy", require_reason=True, conditions={"proposer": "dept_head"}
        ),
        "project.deliver": GateRule("human", "director", require_reason=True),
        "spend.approve": GateRule(
            "agent",
            "dept_head",
            amount_tiers=(
                AmountTier(1_000, "system"),
                AmountTier(50_000, "agent", "dept_head"),
                AmountTier(500_000, "agent", "committee"),
                AmountTier(None, "human", "director"),
            ),
            conditions={"committee_quorum": 3},
        ),
        "hire": GateRule(
            "human", "director", separation=("proposer!=approver",), require_reason=True
        ),
        "fire": GateRule(
            "human", "director", separation=("proposer!=approver",), require_reason=True
        ),
        "purchase": GateRule(
            "agent",
            "committee",
            quorum=3,
            separation=("proposer!=approver",),
            require_reason=True,
            escalate_to="director",
            timeout_s=72 * 3600,
        ),
        "knowledge.promote": GateRule("agent", "dept_head", require_reason=True),
        "escalation.resolve": GateRule(
            "human", "deputy", timeout_s=48 * 3600, escalate_to="director"
        ),
    },
    authority_defaults={
        "director": ("*",),
        "deputy": ("*",),
        "dept_head": (
            "plan.approve",
            "task.assign",
            "task.review",
            "spend.approve",
            "knowledge.promote",
            "workspace.read",
        ),
        "committee": ("mission.accept", "spend.approve", "purchase"),
        "staff": ("workspace.read", "workspace.write", "workspace.execute", "task.complete"),
        "auditor": ("workspace.read", "task.review", "task.rework"),
    },
    budget_policy=BudgetPolicy(
        project_default=1_000_000, run_default=10_000, warn_ratio=0.7, hard_stop=True
    ),
    review_policy=ReviewPolicy(rework_limit=3, escalate_after=2),
    retention=Retention(transcript_days=365),
)
