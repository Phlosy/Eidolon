"""Kernel invariants (K-series). Each entry names the test that enforces it, and
tests_v2/test_kernel_guards.py checks that every named test exists — the legacy
repository learned the hard way that declarative lists drift unless machine-checked.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Invariant:
    code: str
    statement: str
    anchor: str  # "test_module::test_name"


KERNEL_INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        "K1",
        "facts are append-only: UPDATE and DELETE are rejected by the database itself",
        "test_kernel_facts::test_facts_cannot_be_updated_or_deleted",
    ),
    Invariant(
        "K2",
        "(company_id, idempotency_key) records at most one fact; a different payload "
        "under the same key is a conflict, not a silent overwrite",
        "test_kernel_facts::test_idempotent_append_returns_original_and_rejects_conflict",
    ),
    Invariant(
        "K3",
        "every company's facts form a hash chain from GENESIS; verify() reports the "
        "first altered, removed or reordered fact; concurrent appends never fork the chain",
        "test_kernel_facts::test_tampering_breaks_the_chain",
    ),
    Invariant(
        "K4",
        "payloads are plain canonical JSON under a size cap; non-JSON values are "
        "rejected at the boundary rather than coerced",
        "test_kernel_facts::test_payload_must_be_plain_json_and_bounded",
    ),
    Invariant(
        "K5",
        "fact kinds are namespaced 'layer.noun.verb' with a closed set of layers",
        "test_kernel_facts::test_kinds_are_namespaced",
    ),
    Invariant(
        "K6",
        "consumers advance a durable cursor in the same transaction as their effects; "
        "replay from the cursor is the only recovery mechanism and rebuild from zero "
        "reproduces a projection exactly",
        "test_kernel_consumers::test_crash_between_handle_and_cursor_is_replayed",
    ),
    Invariant(
        "K7",
        "the kernel imports nothing from identity/institution/work/agent/money/talent/"
        "api/extensions",
        "test_kernel_guards::test_kernel_has_no_upward_imports",
    ),
    Invariant(
        "K8",
        "v2 refuses any database backend other than PostgreSQL",
        "test_kernel_guards::test_non_postgres_url_is_refused",
    ),
    Invariant(
        "K9",
        "a fact's NOTIFY reaches listeners only when its transaction commits",
        "test_kernel_notify::test_notify_only_on_commit",
    ),
    Invariant(
        "K10",
        "any fact's provenance can be exported as a bundle that an independent, "
        "database-free verifier accepts, and that fails verification if any of its "
        "content is altered",
        "test_provenance::test_bundle_verifies_offline_and_any_change_is_detected",
    ),
)

# Work-domain invariants carried over from M2 (R1–R12 dispatch, H1–H8 handoff,
# RV1–RV8 review, RD1 readiness), folded into the W series. Each names its test.
WORK_INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        "W1",
        "R1/R2/R4/R10: the system dispatches only to an assignee named by an approved "
        "task.assign decision; a ready unassigned task is a management question",
        "test_work::test_assignment_requires_an_approved_decision_naming_the_assignee",
    ),
    Invariant(
        "W2",
        "R3/R8/RD1: readiness is derived from facts (todo + dependencies and input "
        "producers done), never stored, and is not a request for approval",
        "test_work::test_readiness_is_derived_and_distinct_from_dispatchability",
    ),
    Invariant(
        "W3",
        "R5/R10/R12: a failed task returns to work only through a decision; nothing "
        "retries, reassigns or plans on management's behalf",
        "test_work::test_failed_task_returns_only_through_a_decision",
    ),
    Invariant(
        "W4",
        "H1/H2/H3: artifacts are blob refs attributed to their producing task and run; "
        "consumption records the using task and run",
        "test_work::test_artifacts_are_attributed_and_consumption_is_recorded",
    ),
    Invariant(
        "W5",
        "H4/H5/H8: inputs are producer tasks that must be DAG ancestors; only finished "
        "work is consumable; the system resolves the artifacts at run time",
        "test_work::test_inputs_must_be_ancestors_and_only_done_work_is_consumable",
    ),
    Invariant(
        "W6",
        "H7: an artifact's lineage is walkable back through its producer's inputs",
        "test_work::test_lineage_walks_back_through_producers",
    ),
    Invariant(
        "W7",
        "RV1/RV2/RV3/RV4: a task leaves in_review only through a verdict signed by the "
        "named reviewer in a task.review decision; the system never produces one",
        "test_work::test_review_needs_a_signed_decision_and_maps_one_way",
    ),
    Invariant(
        "W8",
        "RV5/RV6/RV8: REWORK returns to todo and counts; ESCALATE blocks and waits; the "
        "decision status maps to the verdict one way only",
        "test_work::test_rework_counts_and_escalate_blocks",
    ),
    Invariant(
        "W9",
        "the task graph is a DAG: self loops, dangling and duplicate edges and cycles "
        "are rejected at creation and before plan approval",
        "test_work::test_graph_validation_rejects_bad_structure",
    ),
    Invariant(
        "W10",
        "the four work tables are projections: rebuilt from zero they equal the fold",
        "test_work::test_projection_matches_the_fold_and_rebuilds",
    ),
)


# Money invariants (R4-b): the ledger is double-entry, derived, and verifiable.
MONEY_INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        "M1",
        "an entry is ≥ 2 positive integer lines with Σdebit = Σcredit, posting only to "
        "accounts that exist in the entry's currency; the fact builder, the fold and "
        "the projection all refuse anything else",
        "test_money::test_entries_must_balance_and_post_to_known_accounts",
    ),
    Invariant(
        "M2",
        "every live, priced model call is metered once in the company's currency at a "
        "versioned book rate and booked debit compute / credit ap:<provider>; replays "
        "and unpriced models are recorded but never booked",
        "test_money::test_cost_meter_books_live_calls_and_records_the_rest",
    ),
    Invariant(
        "M3",
        "account balances and the journal are a projection of money.* facts: tampering "
        "with them is detected by ledger-verify and undone by rebuild",
        "test_money::test_ledger_verify_detects_tampering_and_rebuild_repairs",
    ),
    Invariant(
        "M4",
        "budget amounts cross from units (minor currency units, the institution's "
        "numbers) to micro (the ledger's) only through money.budgets; project spend is "
        "a query joining journal lines to tasks, never stored",
        "test_money::test_budgets_roll_up_spend_by_run_task_and_project",
    ),
    Invariant(
        "M5",
        "every run names the fact that called for it (causation on agent.run.started), so "
        "a ledger line walks back through cost, call, step, run, task and project by "
        "causation and correlation alone, and a project's spend is a query, never stored",
        "test_cost_trace::test_cost_trace_walks_from_entry_to_project",
    ),
)


# Talent invariants (R5-a): competence is read, never consulted.
TALENT_INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        "T1",
        "evidence and competencies are projections of facts other layers record; no "
        "module below talent imports it, so no selection, assignment or verdict can "
        "take a score as input — those remain decisions at gates",
        "test_talent::test_competency_is_a_projection_nothing_below_reads",
    ),
    Invariant(
        "T2",
        "every piece of evidence names its source fact and the person, task, project "
        "and run it is about; humans accrue none; rebuild reproduces it exactly",
        "test_talent::test_evidence_is_derived_from_facts_and_rebuilds",
    ),
)
