"""v57: fixed action confirmations with immutable proposal and execution receipts."""

from alembic import op

revision = "57c326fb5401"
down_revision = "56b215ea43f0"
branch_labels = None
depends_on = None

# Frozen revision-local schema and guards; no import from evolving models.
TABLE_SQL = """
CREATE TABLE action_confirmations (
    company_id INTEGER NOT NULL,
    employee_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    project_id INTEGER NOT NULL,
    work_session_id INTEGER NOT NULL,
    decision_id INTEGER NOT NULL,
    runtime_operation_id INTEGER NOT NULL,
    action_index INTEGER NOT NULL,
    tool_name VARCHAR(100) NOT NULL,
    policy_version VARCHAR(64) NOT NULL,
    arguments_json JSON NOT NULL,
    arguments_digest VARCHAR(64) NOT NULL,
    target_ref VARCHAR(256) NOT NULL,
    target_version VARCHAR(256) NOT NULL,
    amount INTEGER NOT NULL,
    expires_at DATETIME NOT NULL,
    status VARCHAR(16) NOT NULL,
    proposal_audit_id INTEGER,
    final_audit_id INTEGER,
    review_outcome VARCHAR(16),
    reviewer_user_id INTEGER,
    reviewer_session_id INTEGER,
    reviewed_at DATETIME,
    resolved_at DATETIME,
    id INTEGER NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT ck_confirmation_terms CHECK (amount >= 0 AND length(arguments_digest) = 64 AND
        action_index >= 0 AND length(target_ref) BETWEEN 1 AND 256 AND length(target_version)
        BETWEEN 1 AND 256 AND length(policy_version) BETWEEN 1 AND 64 AND expires_at >
        created_at),
    CONSTRAINT ck_confirmation_review CHECK ((review_outcome IS NULL AND reviewed_at IS NULL AND
        reviewer_user_id IS NULL AND reviewer_session_id IS NULL) OR (review_outcome IS NOT NULL
        AND review_outcome IN ('approved', 'rejected') AND reviewed_at IS NOT NULL AND
        reviewer_user_id IS NOT NULL AND reviewer_session_id IS NOT NULL)),
    CONSTRAINT ck_confirmation_state CHECK ((status = 'pending' AND review_outcome IS NULL AND
        resolved_at IS NULL AND final_audit_id IS NULL) OR (status = 'approved' AND
        review_outcome IS NOT NULL AND review_outcome = 'approved' AND resolved_at IS NULL AND
        final_audit_id IS NULL AND proposal_audit_id IS NOT NULL) OR (status IN ('executed',
        'failed', 'rejected', 'expired') AND resolved_at IS NOT NULL AND final_audit_id IS NOT
        NULL AND proposal_audit_id IS NOT NULL AND (status = 'expired' OR (review_outcome IS NOT
        NULL AND ((status IN ('executed', 'failed') AND review_outcome = 'approved') OR (status
        = 'rejected' AND review_outcome = 'rejected')))))),
    FOREIGN KEY(company_id) REFERENCES companies (id),
    FOREIGN KEY(employee_id) REFERENCES employees (id),
    FOREIGN KEY(person_id) REFERENCES persons (id),
    FOREIGN KEY(project_id) REFERENCES projects (id),
    FOREIGN KEY(work_session_id) REFERENCES work_sessions (id),
    FOREIGN KEY(decision_id) REFERENCES decision_records (id),
    FOREIGN KEY(runtime_operation_id) REFERENCES runtime_operations (id),
    UNIQUE (proposal_audit_id),
    FOREIGN KEY(proposal_audit_id) REFERENCES tool_audits (id),
    UNIQUE (final_audit_id),
    FOREIGN KEY(final_audit_id) REFERENCES tool_audits (id),
    FOREIGN KEY(reviewer_user_id) REFERENCES users (id),
    FOREIGN KEY(reviewer_session_id) REFERENCES user_sessions (id)
)
"""

INDEX_SQL = [
    "CREATE INDEX ix_confirmation_company_status ON action_confirmations (company_id, status, id)",
    "CREATE UNIQUE INDEX uq_confirmation_action ON action_confirmations (runtime_operation_id, "
    "action_index)",
]

GUARDS = (
    (
        "confirmation_insert_valid",
        "INSERT",
        "NEW.status <> 'pending' OR NEW.review_outcome IS NOT NULL OR NEW.proposal_audit_id IS NOT "
        "NULL OR NEW.final_audit_id IS NOT NULL OR NOT EXISTS (SELECT 1 FROM runtime_operations o "
        "JOIN work_sessions w ON w.id = o.work_session_id JOIN decision_records d ON d.id = "
        "o.decision_id WHERE o.id = NEW.runtime_operation_id AND o.kind = 'decision' AND "
        "NEW.action_index < o.requested_action_count AND NEW.decision_id = d.id AND "
        "NEW.work_session_id = w.id AND w.purpose = 'management' AND w.status = 'running' AND "
        "w.ended_at IS NULL AND NEW.company_id = w.company_id AND NEW.company_id = "
        "d.company_id AND "
        "NEW.employee_id = w.employee_id AND NEW.employee_id = d.actor_employee_id AND "
        "NEW.person_id = w.person_id AND NEW.person_id = d.actor_person_id AND NEW.project_id = "
        "w.project_id AND NEW.project_id = d.project_id AND d.status = 'EXECUTING' AND "
        "d.resolved_at IS NULL AND d.superseded_by_id IS NULL)",
    ),
    (
        "confirmation_identity_immutable",
        "UPDATE",
        "NEW.id IS NOT OLD.id OR NEW.created_at IS NOT OLD.created_at OR NEW.company_id IS NOT "
        "OLD.company_id OR NEW.employee_id IS NOT OLD.employee_id OR NEW.person_id IS NOT "
        "OLD.person_id OR NEW.project_id IS NOT OLD.project_id OR NEW.work_session_id IS NOT "
        "OLD.work_session_id OR NEW.decision_id IS NOT OLD.decision_id OR NEW.runtime_operation_id "
        "IS NOT OLD.runtime_operation_id OR NEW.action_index IS NOT OLD.action_index OR "
        "NEW.tool_name IS NOT OLD.tool_name OR NEW.policy_version IS NOT OLD.policy_version OR "
        "NEW.arguments_json IS NOT OLD.arguments_json OR NEW.arguments_digest IS NOT "
        "OLD.arguments_digest OR NEW.target_ref IS NOT OLD.target_ref OR NEW.target_version IS NOT "
        "OLD.target_version OR NEW.amount IS NOT OLD.amount OR NEW.expires_at IS NOT "
        "OLD.expires_at",
    ),
    (
        "confirmation_review_once",
        "UPDATE",
        "OLD.review_outcome IS NOT NULL AND (NEW.review_outcome IS NOT OLD.review_outcome OR "
        "NEW.reviewer_user_id IS NOT OLD.reviewer_user_id OR NEW.reviewer_session_id IS NOT "
        "OLD.reviewer_session_id OR NEW.reviewed_at IS NOT OLD.reviewed_at)",
    ),
    (
        "confirmation_proposal_once",
        "UPDATE",
        "OLD.proposal_audit_id IS NOT NULL AND NEW.proposal_audit_id IS NOT OLD.proposal_audit_id",
    ),
    ("confirmation_terminal_immutable", "UPDATE", "OLD.resolved_at IS NOT NULL"),
    ("confirmation_no_delete", "DELETE", "1"),
    (
        "confirmation_receipts_insert_valid",
        "INSERT",
        "(NEW.proposal_audit_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM tool_audits a WHERE a.id "
        "= NEW.proposal_audit_id AND a.runtime_operation_id = NEW.runtime_operation_id AND "
        "a.action_index = NEW.action_index AND a.decision_id = NEW.decision_id AND "
        "a.arguments_digest = NEW.arguments_digest AND a.tool_name = NEW.tool_name AND "
        "a.actor_employee_id = NEW.employee_id AND a.actor_person_id = NEW.person_id AND "
        "a.actor_company_id = NEW.company_id AND a.work_session_id = NEW.work_session_id AND "
        "a.outcome = 'confirmation_pending')) OR (NEW.final_audit_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM tool_audits a WHERE a.id = NEW.final_audit_id AND "
        "a.runtime_operation_id IS "
        "NULL AND a.action_index IS NULL AND a.decision_id = NEW.decision_id AND "
        "a.arguments_digest "
        "= NEW.arguments_digest AND a.tool_name = NEW.tool_name AND a.actor_employee_id = "
        "NEW.employee_id AND a.actor_person_id = NEW.person_id AND a.actor_company_id = "
        "NEW.company_id AND a.work_session_id = NEW.work_session_id AND ((NEW.status = 'executed' "
        "AND a.outcome = 'applied') OR (NEW.status = 'rejected' AND a.outcome = "
        "'confirmation_rejected') OR (NEW.status = 'expired' AND a.outcome = "
        "'confirmation_expired') OR (NEW.status = 'failed' AND a.outcome NOT IN ('applied', "
        "'confirmation_pending', 'read')))))",
    ),
    (
        "confirmation_receipts_update_valid",
        "UPDATE",
        "(NEW.proposal_audit_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM tool_audits a WHERE a.id "
        "= NEW.proposal_audit_id AND a.runtime_operation_id = NEW.runtime_operation_id AND "
        "a.action_index = NEW.action_index AND a.decision_id = NEW.decision_id AND "
        "a.arguments_digest = NEW.arguments_digest AND a.tool_name = NEW.tool_name AND "
        "a.actor_employee_id = NEW.employee_id AND a.actor_person_id = NEW.person_id AND "
        "a.actor_company_id = NEW.company_id AND a.work_session_id = NEW.work_session_id AND "
        "a.outcome = 'confirmation_pending')) OR (NEW.final_audit_id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM tool_audits a WHERE a.id = NEW.final_audit_id AND "
        "a.runtime_operation_id IS "
        "NULL AND a.action_index IS NULL AND a.decision_id = NEW.decision_id AND "
        "a.arguments_digest "
        "= NEW.arguments_digest AND a.tool_name = NEW.tool_name AND a.actor_employee_id = "
        "NEW.employee_id AND a.actor_person_id = NEW.person_id AND a.actor_company_id = "
        "NEW.company_id AND a.work_session_id = NEW.work_session_id AND ((NEW.status = 'executed' "
        "AND a.outcome = 'applied') OR (NEW.status = 'rejected' AND a.outcome = "
        "'confirmation_rejected') OR (NEW.status = 'expired' AND a.outcome = "
        "'confirmation_expired') OR (NEW.status = 'failed' AND a.outcome NOT IN ('applied', "
        "'confirmation_pending', 'read')))))",
    ),
    (
        "confirmation_transition_valid",
        "UPDATE",
        "NOT ((OLD.status = 'pending' AND NEW.status IN ('pending', 'approved', 'rejected', "
        "'expired')) OR (OLD.status = 'approved' AND NEW.status IN ('approved', 'executed', "
        "'failed', 'expired')))",
    ),
)


def _sqlite_transaction():
    # sqlite3's legacy mode does not BEGIN for DDL. A refusal in a lower
    # revision must roll back these changes and the version marker together.
    connection = op.get_bind()
    if (
        connection.dialect.name == "sqlite"
        and not connection.connection.driver_connection.in_transaction
    ):
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def upgrade():
    _sqlite_transaction()
    op.execute(TABLE_SQL)
    for sql in INDEX_SQL:
        op.execute(sql)
    for name, timing, condition in GUARDS:
        op.execute(
            f"CREATE TRIGGER {name} BEFORE {timing} ON action_confirmations WHEN {condition} "
            "BEGIN SELECT RAISE(ABORT, 'invalid or immutable action confirmation'); END"
        )
    for timing in ("UPDATE", "DELETE"):
        op.execute(
            f"CREATE TRIGGER confirmation_final_receipt_{timing.lower()} BEFORE {timing} "
            "ON tool_audits WHEN EXISTS (SELECT 1 FROM action_confirmations c "
            "WHERE c.final_audit_id = OLD.id) "
            "BEGIN SELECT RAISE(ABORT, 'confirmation final receipt is immutable'); END"
        )


def downgrade():
    _sqlite_transaction()
    if op.get_bind().exec_driver_sql("SELECT COUNT(*) FROM action_confirmations").scalar():
        raise RuntimeError("v57 downgrade would erase durable action confirmations")
    for timing in ("update", "delete"):
        op.execute(f"DROP TRIGGER confirmation_final_receipt_{timing}")
    op.drop_table("action_confirmations")
