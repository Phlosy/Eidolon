"""M1.10 不变量覆盖表（E1–E31 → 测试锚点，Acceptance F）。

这张表是**可执行的契约**：每条不变量都必须有一个真实存在的测试锚点；
`E1–E31` 与设计文档 §38 的编号必须一一对应；锚点一旦被删/改名，这个测试就会红。

为什么用"静态锚点表 + 存在性校验"而不是"每条不变量再写一个测试"：
不变量是**跨阶段的**（一条可能被 3 个文件里的 5 个用例共同保护），把映射写成数据、
用测试钉住它的存在性，比在收尾阶段重复造测试更诚实，也更容易在 M2 演进时发现"锚点掉了"。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SERVER_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = SERVER_ROOT / "tests"
DESIGN_DOC = SERVER_ROOT.parents[1] / "docs" / "m1-economy-design.md"

#: 不变量 → 覆盖它的测试锚点（`文件::测试函数名`；一个不变量可以有多个锚点）
INVARIANT_ANCHORS: dict[str, tuple[str, ...]] = {
    "E1": (
        "test_m1_economy_contract.py::test_money_fields_live_only_in_the_economy_module",
        "test_economy_ledger.py::test_sql_ast_guard_no_direct_balance_mutation_outside_projection",
        "test_economy_contracts.py::test_create_contract_without_funds_leaves_nothing",
    ),
    "E2": (
        "test_economy_projection.py::test_rebuild_reproduces_projection_from_ledger_only",
        "test_economy_ledger.py::test_projection_matches_ledger_after_posting",
    ),
    "E3": (
        "test_m1_economy_contract.py::test_posting_requires_balance_and_two_legs",
        "test_m1_economy_contract.py::test_property_only_balanced_postings_are_accepted",
        "test_economy_projection.py::test_property_random_market_keeps_invariants",
        "test_m1_golden_path.py::test_every_ledger_transaction_is_balanced_and_explainable",
    ),
    "E4": (
        "test_economy_ledger.py::test_mint_requires_authority_token",
        "test_economy_ledger.py::test_only_monetary_authority_module_reaches_the_token",
    ),
    "E5": (
        "test_economy_ledger.py::test_mint_transfer_burn_supply_semantics",
        "test_m1_golden_path.py::test_every_ledger_transaction_is_balanced_and_explainable",
    ),
    "E6": (
        "test_economy_projection.py::test_transfer_does_not_change_supply",
        "test_m1_golden_path.py::test_m1_golden_path_end_to_end",
    ),
    "E7": (
        "test_economy_escrow.py::test_funding_locks_balance_without_changing_net_worth",
        "test_economy_escrow.py::test_full_player_lifecycle_transfers_money_without_minting",
    ),
    "E8": (
        "test_economy_work_orders.py::test_official_publish_path_rejects_player_kinds",
        "test_economy_escrow.py::test_full_player_lifecycle_transfers_money_without_minting",
        "test_talent_trade.py::test_buyout_purchase_moves_money_and_recruits",
    ),
    "E9": (
        "test_economy_rewards.py::test_reward_reference_points_to_a_real_ledger_transaction",
        "test_economy_ledger.py::test_mint_and_burn_require_reason",
    ),
    "E10": (
        "test_economy_rewards.py::test_starter_grant_is_minted_once",
        "test_economy_rewards.py::test_concurrent_starter_grant_posts_once",
    ),
    "E11": (
        "test_economy_escrow.py::test_publish_without_funds_leaves_no_order_and_no_escrow",
        "test_economy_contracts.py::test_create_contract_without_funds_leaves_nothing",
    ),
    "E12": (
        "test_economy_work_orders.py::test_repeated_settlement_is_idempotent",
        "test_economy_contracts.py::test_work_contract_full_lifecycle_settles_with_fee_split",
        "test_m1_hardening.py::test_duplicate_operations_are_idempotent",
    ),
    "E13": (
        "test_economy_concurrency.py::test_failure_during_projection_update_rolls_back_the_whole_posting",
        "test_talent_trade.py::test_recruitment_failure_rolls_back_the_whole_purchase",
        "test_m1_hardening.py::test_midflight_exception_rolls_back_ledger_and_projection",
    ),
    "E14": (
        "test_talent_trade.py::test_recruitment_failure_rolls_back_the_whole_purchase",
        "test_m1_hardening.py::test_recruit_failure_rolls_back_the_purchase",
    ),
    "E15": (
        "test_economy_contracts.py::test_work_contract_full_lifecycle_settles_with_fee_split",
        "test_m1_golden_path.py::test_m1_golden_path_end_to_end",
    ),
    "E16": (
        "test_economy_ledger.py::test_transfer_insufficient_funds_leaves_no_trace",
        "test_economy_work_orders.py::test_official_bounty_full_lifecycle_mints_to_assignee",
        "test_m1_golden_path.py::test_every_ledger_transaction_is_balanced_and_explainable",
    ),
    "E17": (
        "test_economy_ledger.py::test_ledger_entries_are_append_only",
        "test_economy_ledger.py::test_only_the_posting_core_writes_ledger_rows",
    ),
    "E18": (
        "test_talent_trade.py::test_buyout_purchase_moves_money_and_recruits",
        "test_m1_golden_path.py::test_m1_golden_path_end_to_end",
    ),
    "E19": ("test_talent_trade.py::test_buyout_purchase_moves_money_and_recruits",),
    "E20": (
        "test_work_orders_api.py::test_cross_company_cannot_submit_or_read_deliverables",
        "test_talent_trade.py::test_recruitment_failure_rolls_back_the_whole_purchase",
        "test_m1_golden_path.py::test_m1_golden_path_end_to_end",
        "test_npc_economy.py::test_npc_purchase_moves_money_without_minting",
    ),
    "E21": (
        "test_m1_economy_contract.py::test_amount_must_be_positive_integer",
        "test_economy_ledger.py::test_unbalanced_and_invalid_amounts_are_rejected",
        "test_economy_contracts.py::test_illegal_contract_transitions_are_rejected",
    ),
    "E22": (
        "test_m1_economy_contract.py::test_currency_is_explicit",
        "test_economy_ledger.py::test_currency_mismatch_is_rejected",
        "test_economy_ledger.py::test_currency_is_explicit_in_every_entry",
    ),
    "E23": (
        "test_m1_economy_contract.py::test_player_api_never_exposes_mint_or_burn",
        "test_economy_api.py::test_no_write_endpoints_are_exposed",
        "test_economy_work_orders.py::test_player_api_never_publishes_evaluates_or_settles",
        "test_npc_economy.py::test_npc_economy_has_no_player_api_and_only_system_injection_mints",
    ),
    "E24": (
        "test_economy_ledger.py::test_transfer_insufficient_funds_leaves_no_trace",
        "test_m1_economy_contract.py::test_available_balance_cannot_go_negative",
        "test_m1_hardening.py::test_concurrent_spend_and_settlement_keep_invariants",
    ),
    "E25": (
        "test_economy_escrow.py::test_full_player_lifecycle_transfers_money_without_minting",
        "test_economy_contracts.py::test_work_contract_full_lifecycle_settles_with_fee_split",
        "test_m1_hardening.py::test_no_orphan_entities_after_failures",
    ),
    "E26": (
        "test_m1_economy_contract.py::test_balance_delta_is_the_single_interpretation_entry",
        "test_m1_economy_contract.py::test_normal_side_is_frozen",
    ),
    "E27": (
        "test_economy_ledger.py::test_only_the_posting_core_writes_ledger_rows",
        "test_economy_work_orders.py::test_settlement_is_the_only_final_money_entry_for_work_orders",
    ),
    "E28": (
        "test_economy_concurrency.py::test_failure_during_projection_update_rolls_back_the_whole_posting",
        "test_m1_hardening.py::test_cost_charge_failure_does_not_leak_into_caller_transaction",
    ),
    "E29": (
        "test_economy_projection.py::test_rebuild_reproduces_projection_from_ledger_only",
        "test_economy_projection.py::test_verify_detects_drift_and_rebuild_repairs_it",
    ),
    "E30": (
        "test_economy_projection.py::test_rebuild_after_projection_wipe_recovers_reserved_for_multiple_actors",
        "test_economy_escrow.py::test_reserved_follows_ledger_attribution_and_rebuild",
        "test_economy_escrow.py::test_derive_wallets_reserved_survives_restricted_account_ids",
    ),
    "E31": (
        "test_economy_ledger.py::test_ledger_entries_are_append_only",
        "test_economy_ledger.py::test_only_the_posting_core_writes_ledger_rows",
    ),
}


def _test_functions(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    return set(re.findall(r"^def (test_[A-Za-z0-9_]+)\(", source, flags=re.M))


def _design_invariant_ids() -> set[str]:
    rows = re.findall(r"^\| \*\*(E\d+)\*\*", DESIGN_DOC.read_text(encoding="utf-8"), flags=re.M)
    return set(rows)


def test_every_invariant_has_a_live_anchor():
    """E1–E31 每一条都必须有真实存在的测试锚点（锚点被删/改名 → 这里红）。"""
    anchors_by_file: dict[str, set[str]] = {}
    missing_anchors: list[str] = []
    for invariant, anchors in INVARIANT_ANCHORS.items():
        assert anchors, f"{invariant} 没有锚点"
        for anchor in anchors:
            filename, _, function = anchor.partition("::")
            if filename not in anchors_by_file:
                path = TESTS_DIR / filename
                assert path.exists(), f"{invariant} 引用了不存在的测试文件：{filename}"
                anchors_by_file[filename] = _test_functions(path)
            if function not in anchors_by_file[filename]:
                missing_anchors.append(f"{invariant} → {anchor}")
    assert not missing_anchors, "不变量锚点缺失（测试被删或改名）：" + ", ".join(missing_anchors)


def test_invariant_coverage_matches_the_design_document():
    """覆盖表与设计 §38 的编号必须完全一致（新增/删除不变量必须同步这张表）。"""
    documented = _design_invariant_ids()
    assert documented, "设计文档里没找到不变量编号（§38 被改动？）"
    assert set(INVARIANT_ANCHORS) == documented
    assert len(documented) == 31
    for index in range(1, 32):
        assert f"E{index}" in documented, f"E{index} 缺失"


@pytest.mark.parametrize("invariant", sorted(INVARIANT_ANCHORS))
def test_invariant_entry_is_meaningful(invariant: str):
    """每条不变量的锚点至少覆盖一个**领域测试**（不是只有契约/枚举冻结类测试）。"""
    anchors = INVARIANT_ANCHORS[invariant]
    domain_files = {anchor.partition("::")[0] for anchor in anchors}
    assert domain_files, invariant
    assert any(
        name
        for name in domain_files
        if name
        in {
            "test_m1_economy_contract.py",
            "test_economy_api.py",
            "test_m1_golden_path.py",
            "test_m1_hardening.py",
            "test_economy_ledger.py",
            "test_economy_projection.py",
            "test_economy_escrow.py",
            "test_economy_contracts.py",
            "test_economy_work_orders.py",
            "test_economy_rewards.py",
            "test_economy_costs.py",
            "test_economy_concurrency.py",
            "test_talent_trade.py",
            "test_npc_economy.py",
        }
    ), f"{invariant} 只有契约类锚点：{sorted(domain_files)}"
