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
