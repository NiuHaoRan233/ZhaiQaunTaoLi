from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from zhaiquant.opportunity_audit import (
    audit_queue_orders,
    build_branch_opportunity_diagnostics,
    compare_model_capture,
    discover_theoretical_pairs,
    optimize_nonoverlapping_inventory_path,
    summarize_local_turns,
    write_inventory_path_report,
    write_queue_order_audit,
)
from zhaiquant.tdx_tape import TdxOrderEvent, TdxTrade


def trade(time: str, price: float, hands: int, side: str, row: int) -> TdxTrade:
    return TdxTrade(
        market_date="2026-08-14",
        code="132026.SH",
        market_time=time,
        price=price,
        hands=hands,
        side=side,
        buy_order=100 if side == "B" else None,
        sell_order=100 if side == "S" else None,
        source_page="page_01.png",
        page_sequence=1,
        panel=1,
        row=row,
        time_inherited=False,
        ocr_confidence=0.99,
        side_confidence=0.99,
        review_required=False,
    )


def order_event(
    time: str, price: float, hands: int, event_type: str, row: int,
) -> TdxOrderEvent:
    return TdxOrderEvent(
        market_date="2026-08-14",
        code="132026.SH",
        market_time=time,
        price=price,
        hands=hands,
        event_type=event_type,
        source_page="orders_01.png",
        page_sequence=1,
        panel=1,
        row=row,
        time_inherited=False,
        ocr_confidence=0.99,
        event_confidence=0.99,
        event_source="ocr_text",
        review_required=False,
    )


class OpportunityAuditTests(unittest.TestCase):
    def test_queue_audit_sidecars_follow_the_requested_report_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "排队订单逐笔执行审计_排队1.1候选.json"
            replay = {
                "market_date": "2026-08-14",
                "bond_code": "132026.SH",
                "source_database_opened_readonly": True,
                "temporary_replay_database": True,
                "accounts": [{
                    "strategy_id": "queue", "model_id": "queue_v1",
                    "fill_mode": "queue",
                }],
                "orders": [{
                    "id": 1, "strategy_id": "queue", "model_id": "queue_v1",
                    "side": "buy", "kind": "low_bid_reversion",
                    "limit_price": 136.000, "quantity": 1_000.0,
                    "created_market_ts_ms": 1_000,
                    "created_market_time": "10:00:00",
                    "updated_market_time": "10:01:00",
                    "status": "cancelled", "filled_quantity": 0.0,
                    "initial_queue_ahead_bonds": 1_000.0,
                    "queue_ahead": 1_000.0,
                }],
            }
            paths = write_queue_order_audit(
                output,
                trades_path=Path(temp) / "trades.csv",
                order_events_path=Path(temp) / "orders.csv",
                replay=replay,
                audits=audit_queue_orders(replay, [], []),
            )

            self.assertEqual(Path(paths["csv"]), output.with_suffix(".csv"))
            self.assertEqual(
                Path(paths["markdown"]), output.with_suffix(".md"),
            )
            self.assertTrue(output.with_suffix(".csv").exists())
            self.assertTrue(output.with_suffix(".md").exists())

    def test_queue_audit_groups_same_market_cohort_and_bounds_cancellations(self) -> None:
        replay = {
            "accounts": [{
                "strategy_id": "queue", "model_id": "queue_v1",
                "fill_mode": "queue",
            }],
            "orders": [
                {
                    "id": 1, "strategy_id": "queue", "model_id": "queue_v1",
                    "side": "sell", "kind": "inventory_exit",
                    "limit_price": 137.000, "quantity": 600.0,
                    "created_market_ts_ms": 1_000,
                    "created_market_time": "10:00:00",
                    "updated_market_time": "10:01:00",
                    "status": "cancelled", "filled_quantity": 0.0,
                    "initial_queue_ahead_bonds": 1_000.0, "queue_ahead": 1_000.0,
                },
                {
                    "id": 2, "strategy_id": "queue", "model_id": "queue_v1",
                    "side": "sell", "kind": "inventory_exit",
                    "limit_price": 137.000, "quantity": 400.0,
                    "created_market_ts_ms": 1_000,
                    "created_market_time": "10:00:00",
                    "updated_market_time": "10:01:00",
                    "status": "cancelled", "filled_quantity": 0.0,
                    "initial_queue_ahead_bonds": 1_000.0, "queue_ahead": 1_000.0,
                },
            ],
        }
        audits = audit_queue_orders(
            replay,
            [trade("10:00:30", 137.000, 150, "B", 1)],
            [order_event("10:00:20", 137.000, 20, "SC", 1)],
        )

        self.assertEqual(len(audits), 1)
        audit = audits[0]
        self.assertEqual(audit.cohort_model_order_ids, "1|2")
        self.assertEqual(audit.cohort_order_count, 2)
        self.assertEqual(audit.quantity_bonds, 1_000.0)
        self.assertEqual(audit.initial_queue_ahead_bonds, 1_000.0)
        self.assertEqual(audit.repeated_external_queue_bonds, 1_000.0)
        self.assertEqual(audit.same_price_trade_bonds, 1_500.0)
        self.assertEqual(audit.same_price_cancel_bonds, 200.0)
        self.assertEqual(audit.exact_fill_lower_bound_bonds, 500.0)
        self.assertEqual(audit.fill_upper_bound_bonds, 700.0)
        self.assertEqual(
            audit.execution_status, "simulation_underfills_tdx_lower_bound",
        )

    def test_queue_audit_excludes_creation_second_trade_from_strict_bound(self) -> None:
        replay = {
            "accounts": [{
                "strategy_id": "queue", "model_id": "queue_v1",
                "fill_mode": "queue",
            }],
            "orders": [{
                "id": 3, "strategy_id": "queue", "model_id": "queue_v1",
                "side": "buy", "kind": "low_bid_reversion",
                "limit_price": 136.000, "quantity": 1_000.0,
                "created_market_ts_ms": 2_000,
                "created_market_time": "10:00:00",
                "updated_market_time": "10:01:00",
                "status": "cancelled", "filled_quantity": 0.0,
                "initial_queue_ahead_bonds": 0.0, "queue_ahead": 0.0,
            }],
        }
        audits = audit_queue_orders(
            replay,
            [trade("10:00:00", 135.999, 100, "S", 1)],
            [],
        )

        self.assertEqual(audits[0].creation_second_eligible_trade_bonds, 1_000.0)
        self.assertEqual(audits[0].exact_fill_lower_bound_bonds, 0.0)
        self.assertEqual(audits[0].fill_upper_bound_bonds, 0.0)

    def test_queue_audit_accepts_validated_crossed_book_residual_capacity(
        self,
    ) -> None:
        replay = {
            "accounts": [{
                "strategy_id": "queue", "model_id": "queue_v112",
                "fill_mode": "queue",
            }],
            "orders": [{
                "id": 7, "strategy_id": "queue", "model_id": "queue_v112",
                "side": "sell", "kind": "inventory_exit",
                "limit_price": 137.197, "quantity": 1_000.0,
                "created_market_ts_ms": 1_000,
                "created_market_time": "10:19:54",
                "updated_market_time": "10:20:12",
                "status": "filled", "filled_quantity": 1_000.0,
                "initial_queue_ahead_bonds": 2_000.0,
                "queue_ahead": 0.0,
            }],
            "fills": [{
                "order_id": 7, "strategy_id": "queue",
                "model_id": "queue_v112", "market_time": "10:20:12",
                "side": "sell", "price": 137.197,
                "quantity_bonds": 1_000.0,
                "fill_reason": "queue_cleared_crossed_residual_fill",
                "inventory_after_bonds": 0.0,
                "reference_tick_id": 99,
                "crossed_book_residual_price": 137.197,
                "crossed_book_residual_bonds": 3_000.0,
            }],
        }

        audit = audit_queue_orders(replay, [], [])[0]

        self.assertEqual(audit.crossed_book_residual_fill_bonds, 1_000.0)
        self.assertEqual(audit.exact_fill_lower_bound_bonds, 1_000.0)
        self.assertEqual(audit.fill_upper_bound_bonds, 1_000.0)
        self.assertEqual(
            audit.execution_status, "exactly_consistent_with_tdx_bounds",
        )

    def test_queue_audit_rejects_residual_allocations_above_displayed_size(
        self,
    ) -> None:
        replay = {
            "accounts": [{
                "strategy_id": "queue", "model_id": "queue_v112",
                "fill_mode": "queue",
            }],
            "orders": [
                {
                    "id": order_id, "strategy_id": "queue",
                    "model_id": "queue_v112", "side": "sell",
                    "kind": "inventory_exit", "limit_price": price,
                    "quantity": 600.0, "created_market_ts_ms": order_id,
                    "created_market_time": "10:19:54",
                    "updated_market_time": "10:20:12",
                    "status": "filled", "filled_quantity": 600.0,
                    "initial_queue_ahead_bonds": 0.0,
                    "queue_ahead": 0.0,
                }
                for order_id, price in ((8, 137.196), (9, 137.197))
            ],
            "fills": [
                {
                    "order_id": order_id, "strategy_id": "queue",
                    "model_id": "queue_v112", "market_time": "10:20:12",
                    "side": "sell", "price": price,
                    "quantity_bonds": 600.0,
                    "fill_reason": "queue_cleared_crossed_residual_fill",
                    "inventory_after_bonds": 0.0,
                    "reference_tick_id": 100,
                    "crossed_book_residual_price": 137.197,
                    "crossed_book_residual_bonds": 1_000.0,
                }
                for order_id, price in ((8, 137.196), (9, 137.197))
            ],
        }

        audits = audit_queue_orders(replay, [], [])

        self.assertTrue(all(
            item.crossed_book_residual_fill_bonds == 0.0
            for item in audits
        ))
        self.assertTrue(all(
            item.execution_status == "simulated_fill_exceeds_tdx_upper_bound"
            for item in audits
        ))

    def test_inventory_path_uses_each_print_once_with_explicit_base_restore(self) -> None:
        path = optimize_nonoverlapping_inventory_path([
            trade("10:00:00", 138.000, 100, "B", 1),
            trade("10:00:01", 136.000, 100, "S", 2),
            trade("10:00:02", 135.000, 100, "S", 3),
            trade("10:00:03", 137.000, 100, "B", 4),
        ], terminal_inventory_hands=100)

        self.assertEqual(path.initial_inventory_hands, 100)
        self.assertEqual(path.terminal_inventory_hands, 100)
        self.assertEqual(path.maximum_inventory_hands, 200)
        self.assertTrue(path.terminal_inventory_forced)
        self.assertEqual(path.buy_hands, 200)
        self.assertEqual(path.sell_hands, 200)
        self.assertEqual(path.gross_cash_profit, 4_000.0)
        self.assertEqual(
            [(item.action, item.hands) for item in path.actions],
            [("sell", 100), ("buy", 100), ("buy", 100), ("sell", 100)],
        )

    def test_inventory_path_allows_and_marks_terminal_exposure_by_default(self) -> None:
        path = optimize_nonoverlapping_inventory_path([
            trade("10:00:00", 90.000, 100, "S", 1),
            trade("15:00:00", 100.000, 1, "S", 2),
        ])

        self.assertFalse(path.terminal_inventory_forced)
        self.assertEqual(path.terminal_inventory_hands, 200)
        self.assertEqual(path.terminal_mark_price, 100.000)
        self.assertEqual(path.gross_cash_flow, -90_000.0)
        self.assertEqual(path.gross_cash_profit, 10_000.0)
        self.assertEqual(
            [(item.action, item.hands) for item in path.actions],
            [("buy", 100)],
        )

    def test_inventory_path_cannot_reuse_more_than_print_capacity(self) -> None:
        path = optimize_nonoverlapping_inventory_path([
            trade("10:00:00", 136.000, 50, "S", 1),
            trade("10:01:00", 137.000, 100, "B", 2),
        ])

        self.assertEqual(path.buy_hands, 50)
        self.assertEqual(path.sell_hands, 50)
        self.assertEqual(path.gross_cash_profit, 500.0)

    def test_inventory_path_keeps_one_tick_theoretical_opportunity(self) -> None:
        path = optimize_nonoverlapping_inventory_path([
            trade("10:00:00", 137.000, 1, "S", 1),
            trade("10:00:01", 137.001, 1, "B", 2),
        ])

        self.assertEqual(path.gross_cash_profit, 0.01)

    def test_inventory_path_report_is_explicitly_noncausal(self) -> None:
        inventory_path = optimize_nonoverlapping_inventory_path([
            trade("10:00:00", 137.000, 1, "S", 1),
            trade("10:00:01", 137.001, 1, "B", 2),
        ])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            outputs = write_inventory_path_report(
                root / "inventory.json",
                trades_path=root / "trades.csv",
                inventory_path=inventory_path,
                manual_review_rows=2,
                manual_reviews_path=root / "reviews.csv",
            )

            payload = json.loads(Path(outputs["report"]).read_text(encoding="utf-8"))
            markdown = Path(outputs["markdown"]).read_text(encoding="utf-8")
            self.assertFalse(payload["causal_signal"])
            self.assertFalse(payload["mode_in_opportunity_count"])
            self.assertFalse(payload["may_be_used_as_capture_rate_denominator"])
            self.assertEqual(payload["summary"]["action_count"], 2)
            self.assertEqual(payload["summary"]["buy_bonds"], 10)
            self.assertEqual(payload["manual_reviews"]["applied_rows"], 2)
            self.assertIn("不是盘中因果信号", markdown)
            self.assertTrue(Path(outputs["actions_csv"]).exists())

    def test_every_ordered_opposite_side_positive_pair_is_kept(self) -> None:
        trades = [
            trade("10:00:00", 137.000, 1, "S", 1),
            trade("10:00:01", 137.001, 1, "B", 2),
            trade("10:00:02", 136.999, 1, "S", 3),
        ]

        pairs = discover_theoretical_pairs(trades)

        self.assertEqual(len(pairs), 2)
        self.assertEqual(
            [(item.direction, item.edge) for item in pairs],
            [("buy_then_sell", 0.001), ("sell_then_buy", 0.002)],
        )

    def test_theory_has_no_lookback_size_or_cluster_requirement(self) -> None:
        pairs = discover_theoretical_pairs([
            trade("09:30:00", 137.000, 1, "S", 1),
            trade("15:00:00", 137.001, 1, "B", 2),
        ])

        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].elapsed_seconds, 19_800)
        self.assertEqual(pairs[0].single_pair_capacity_hands, 1)

    def test_same_side_and_non_positive_pairs_are_not_opportunities(self) -> None:
        pairs = discover_theoretical_pairs([
            trade("10:00:00", 137.000, 100, "S", 1),
            trade("10:00:01", 137.000, 100, "B", 2),
            trade("10:00:02", 137.100, 100, "S", 3),
            trade("10:00:03", 137.200, 100, "S", 4),
        ])

        self.assertEqual(pairs, [])

    def test_low_confidence_ocr_row_is_excluded(self) -> None:
        uncertain = trade("10:01:00", 137.0, 100, "S", 2)
        uncertain = TdxTrade(**{
            **uncertain.__dict__,
            "review_required": True,
        })

        pairs = discover_theoretical_pairs([
            uncertain,
            trade("10:02:00", 138.0, 100, "B", 3),
        ])

        self.assertEqual(pairs, [])

    def test_adjacent_side_runs_are_readable_compression_not_filter(self) -> None:
        trades = [
            trade("10:00:00", 137.000, 5, "S", 1),
            trade("10:00:01", 136.999, 10, "S", 2),
            trade("10:00:02", 137.001, 7, "B", 3),
            trade("10:00:03", 137.002, 20, "B", 4),
        ]
        pairs = discover_theoretical_pairs(trades)

        turns = summarize_local_turns(trades, pairs)

        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].profitable_pair_count, 4)
        self.assertEqual(turns[0].minimum_positive_edge, 0.001)
        self.assertEqual(turns[0].maximum_edge, 0.003)
        self.assertEqual(turns[0].maximum_matchable_hands, 15)

    def test_model_best_pair_match_requires_both_legs_and_is_branch_specific(self) -> None:
        trades = [
            trade("10:00:00", 137.000, 100, "S", 1),
            trade("10:01:00", 138.000, 100, "B", 2),
        ]
        turns = summarize_local_turns(trades, discover_theoretical_pairs(trades))
        replay = {
            "accounts": [
                {"strategy_id": "priority", "model_id": "priority_v1"},
                {"strategy_id": "queue", "model_id": "queue_v1"},
            ],
            "fills": [
                {
                    "strategy_id": "priority",
                    "model_id": "priority_v1",
                    "market_time": "10:00:03",
                    "side": "buy",
                    "price": 137.001,
                    "quantity_bonds": 1_000.0,
                    "fill_reason": "passive_buy",
                    "inventory_after_bonds": 2_000.0,
                },
                {
                    "strategy_id": "priority",
                    "model_id": "priority_v1",
                    "market_time": "10:01:03",
                    "side": "sell",
                    "price": 137.999,
                    "quantity_bonds": 1_000.0,
                    "fill_reason": "passive_sell",
                    "inventory_after_bonds": 1_000.0,
                },
                {
                    "strategy_id": "queue",
                    "model_id": "queue_v1",
                    "market_time": "10:00:03",
                    "side": "buy",
                    "price": 137.000,
                    "quantity_bonds": 1_000.0,
                    "fill_reason": "passive_buy",
                    "inventory_after_bonds": 2_000.0,
                },
            ],
        }

        comparisons = compare_model_capture(turns, replay)
        statuses = {
            row["strategy_id"]: row["capture_status"]
            for row in comparisons[0]["branch_results"]
        }
        self.assertEqual(statuses["priority"], "best_pair_both_legs_matched")
        self.assertEqual(statuses["queue"], "best_pair_open_leg_matched_only")

    def test_branch_diagnostics_keep_capacity_and_economic_capture_separate(
        self,
    ) -> None:
        trades = [
            trade("10:00:00", 137.000, 100, "S", 1),
            trade("10:01:00", 138.000, 100, "B", 2),
        ]
        turns = summarize_local_turns(trades, discover_theoretical_pairs(trades))
        replay = {
            "accounts": [
                {
                    "strategy_id": "priority", "model_id": "priority_v1",
                    "fill_mode": "priority", "initial_inventory": 1_000.0,
                    "maximum_inventory": 2_000.0, "initial_cash": 200_000.0,
                },
                {
                    "strategy_id": "queue", "model_id": "queue_v1",
                    "fill_mode": "queue", "initial_inventory": 2_000.0,
                    "maximum_inventory": 2_000.0, "initial_cash": 0.0,
                },
            ],
            "fills": [
                {
                    "strategy_id": "priority", "model_id": "priority_v1",
                    "market_time": "10:00:10", "side": "buy",
                    "price": 137.100, "quantity_bonds": 1_000.0,
                    "fill_reason": "passive_buy", "inventory_after_bonds": 2_000.0,
                },
                {
                    "strategy_id": "priority", "model_id": "priority_v1",
                    "market_time": "10:00:50", "side": "sell",
                    "price": 137.900, "quantity_bonds": 1_000.0,
                    "fill_reason": "passive_sell", "inventory_after_bonds": 1_000.0,
                },
            ],
            "orders": [],
        }

        rows = build_branch_opportunity_diagnostics(turns, replay)
        by_strategy = {row["strategy_id"]: row for row in rows}

        self.assertEqual(
            by_strategy["priority"]["preliminary_status"],
            "economic_pair_within_turn_needs_overlap_review",
        )
        self.assertEqual(
            by_strategy["priority"]["opening_action_capacity_strict_bonds"],
            1_000,
        )
        self.assertEqual(
            by_strategy["queue"]["preliminary_status"],
            "opening_inventory_or_cash_capacity_blocked",
        )
        self.assertEqual(by_strategy["queue"]["causal_mode_in_status"], "unreviewed")

    def test_branch_diagnostics_do_not_call_preexisting_order_mode_in(self) -> None:
        trades = [
            trade("10:00:00", 138.000, 100, "B", 1),
            trade("10:01:00", 137.000, 100, "S", 2),
        ]
        turns = summarize_local_turns(trades, discover_theoretical_pairs(trades))
        replay = {
            "accounts": [{
                "strategy_id": "queue", "model_id": "queue_v1",
                "fill_mode": "queue", "initial_inventory": 1_000.0,
                "maximum_inventory": 2_000.0, "initial_cash": 200_000.0,
            }],
            "fills": [],
            "orders": [{
                "id": 1, "strategy_id": "queue", "side": "sell",
                "kind": "inventory_exit", "limit_price": 137.999,
                "quantity": 1_000.0, "filled_quantity": 0.0,
                "created_market_time": "09:59:30",
                "updated_market_time": "10:00:30",
                "status": "cancelled", "cancel_reason": "maker_reprice",
                "initial_queue_ahead_bonds": 2_000.0, "queue_ahead": 1_000.0,
            }],
        }

        row = build_branch_opportunity_diagnostics(turns, replay)[0]

        self.assertEqual(
            row["preliminary_status"],
            "preexisting_reaching_order_needs_execution_audit",
        )
        self.assertEqual(row["preexisting_reaching_open_order_count"], 1)
        self.assertEqual(row["causal_mode_in_status"], "unreviewed")

    def test_branch_diagnostics_find_cross_turn_inventory_path_without_calling_it_final(
        self,
    ) -> None:
        trades = [
            trade("10:00:00", 138.000, 100, "B", 1),
            trade("10:01:00", 137.000, 100, "S", 2),
        ]
        turns = summarize_local_turns(trades, discover_theoretical_pairs(trades))
        replay = {
            "accounts": [{
                "strategy_id": "priority", "model_id": "priority_v1",
                "fill_mode": "priority", "initial_inventory": 1_000.0,
                "maximum_inventory": 2_000.0, "initial_cash": 200_000.0,
            }],
            "fills": [
                {
                    "strategy_id": "priority", "model_id": "priority_v1",
                    "market_time": "10:00:00", "side": "sell",
                    "price": 138.000, "quantity_bonds": 1_000.0,
                    "fill_reason": "passive_sell", "inventory_after_bonds": 0.0,
                },
                {
                    "strategy_id": "priority", "model_id": "priority_v1",
                    "market_time": "10:08:00", "side": "buy",
                    "price": 136.900, "quantity_bonds": 1_000.0,
                    "fill_reason": "passive_buy", "inventory_after_bonds": 1_000.0,
                },
            ],
            "orders": [],
        }

        row = build_branch_opportunity_diagnostics(turns, replay)[0]

        self.assertEqual(
            row["preliminary_status"],
            "economic_pair_cross_turn_600s_needs_path_review",
        )
        self.assertEqual(row["final_capture_class"], "unreviewed")

    def test_branch_diagnostics_reject_cross_turn_pair_wholly_before_opportunity(
        self,
    ) -> None:
        trades = [
            trade("10:10:00", 138.000, 100, "B", 1),
            trade("10:11:00", 137.000, 100, "S", 2),
        ]
        turns = summarize_local_turns(trades, discover_theoretical_pairs(trades))
        replay = {
            "accounts": [{
                "strategy_id": "priority", "model_id": "priority_v1",
                "fill_mode": "priority", "initial_inventory": 1_000.0,
                "maximum_inventory": 2_000.0, "initial_cash": 200_000.0,
            }],
            "fills": [
                {
                    "strategy_id": "priority", "model_id": "priority_v1",
                    "market_time": "10:01:00", "side": "sell",
                    "price": 138.100, "quantity_bonds": 1_000.0,
                    "fill_reason": "passive_sell", "inventory_after_bonds": 0.0,
                },
                {
                    "strategy_id": "priority", "model_id": "priority_v1",
                    "market_time": "10:02:00", "side": "buy",
                    "price": 136.900, "quantity_bonds": 1_000.0,
                    "fill_reason": "passive_buy", "inventory_after_bonds": 1_000.0,
                },
            ],
            "orders": [],
        }

        row = build_branch_opportunity_diagnostics(turns, replay)[0]

        self.assertIsNone(row["economic_pair_cross_turn_600s"])
        self.assertEqual(row["preliminary_status"], "causal_mode_in_review_required")


if __name__ == "__main__":
    unittest.main()
