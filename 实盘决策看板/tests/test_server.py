from __future__ import annotations

import json
import sys
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server


class ModelMatrixTests(unittest.TestCase):
    def test_matrix_comes_from_simulator_session_config(self) -> None:
        config_json = json.dumps({
            "maker_paper": {
                "enabled": True,
                "fill_modes": [],
                "super_windfall_enabled": True,
                "super_windfall_model_id": "maker_windfall_v2_0_candidate",
                "realtime_comparison_model_ids": [
                    "maker_priority_v1_37_candidate",
                    "maker_priority_v1_49_candidate_r2",
                    "maker_queue_v1_17_candidate",
                    "maker_queue_v1_18_candidate",
                ],
            },
        })
        self.assertEqual(
            server.model_ids_from_session_config(config_json),
            (
                "maker_windfall_v2_0_candidate",
                "maker_priority_v1_37_candidate",
                "maker_priority_v1_49_candidate_r2",
                "maker_queue_v1_17_candidate",
                "maker_queue_v1_18_candidate",
            ),
        )

    def test_enabled_baselines_keep_simulator_order(self) -> None:
        config_json = json.dumps({
            "maker_paper": {
                "enabled": True,
                "fill_modes": ["priority", "queue"],
                "super_windfall_enabled": False,
                "realtime_comparison_model_ids": [],
            },
        })
        self.assertEqual(server.model_ids_from_session_config(config_json), (
            "maker_priority_v1_1", "maker_queue_v1_0",
        ))


class RefreshWindowTests(unittest.TestCase):
    def test_weekday_boundaries(self) -> None:
        self.assertFalse(server.refresh_window_active(datetime(2026, 8, 20, 9, 24, 59)))
        self.assertTrue(server.refresh_window_active(datetime(2026, 8, 20, 9, 25, 0)))
        self.assertTrue(server.refresh_window_active(datetime(2026, 8, 20, 15, 29, 59)))
        self.assertFalse(server.refresh_window_active(datetime(2026, 8, 20, 15, 30, 0)))

    def test_weekend_is_inactive(self) -> None:
        self.assertFalse(server.refresh_window_active(datetime(2026, 8, 22, 10, 0, 0)))

    def test_manual_refresh_bypasses_stale_after_hours_model_cache(self) -> None:
        cache = server.SnapshotCache(Path("unused.sqlite3"))
        old_snapshot = {
            "marker": "11:29",
            "refresh": {"active": True, "label": "盘中自动刷新"},
        }
        latest_snapshot = {
            "marker": "15:29",
            "refresh": {"active": False, "label": "收盘快照"},
        }

        with (
            patch.object(server, "refresh_window_active", side_effect=[True, False, False]),
            patch.object(server.time, "monotonic", side_effect=[1.0, 100.0, 101.0]),
            patch.object(
                server,
                "load_snapshot",
                side_effect=[old_snapshot, latest_snapshot],
            ) as load_snapshot,
        ):
            seeded = cache.get("132026.SH", "maker_priority_v2_63_candidate")
            stale = cache.get("132026.SH", "maker_priority_v2_63_candidate")
            refreshed = cache.get(
                "132026.SH",
                "maker_priority_v2_63_candidate",
                force_refresh=True,
            )

        self.assertEqual(seeded["marker"], "11:29")
        self.assertEqual(stale["marker"], "11:29")
        self.assertEqual(refreshed["marker"], "15:29")
        self.assertEqual(load_snapshot.call_count, 2)
        load_snapshot.assert_called_with(
            Path("unused.sqlite3"),
            "132026.SH",
            action_model_id="maker_priority_v2_63_candidate",
        )


class ChartHistoryTests(unittest.TestCase):
    def test_zero_and_invalid_last_prices_do_not_flatten_intraday_chart(self) -> None:
        history = [
            {"ts": 1, "last": 0},
            {"ts": 2, "last": None},
            {"ts": 3, "last": 137.9},
            {"ts": 4, "last": 137.3},
            {"ts": 5, "last": 136.0},
            {"ts": 6, "last": 136.6},
        ]

        result = server.valid_chart_history(history)

        self.assertEqual([item["last"] for item in result], [137.9, 137.3, 136.0, 136.6])


class QuietReferenceViewTests(unittest.TestCase):
    def test_v150_inherits_v149_quiet_reference_display(self) -> None:
        self.assertIn(
            "maker_priority_v1_50_candidate",
            server.QUIET_REFERENCE_MODEL_IDS,
        )

    def test_v147_replaces_previous_close_display_with_bounded_intraday_band(
        self,
    ) -> None:
        prior = {
            "reference_price": 136.300,
            "reference_low": 136.298,
            "reference_high": 136.887,
            "reference_source": "large_buy_breakout_support",
            "reference_confidence": 0.75,
            "evidence": [],
        }
        fallback = {
            "reference_price": 136.938,
            "reference_low": 136.923,
            "reference_high": 136.953,
            "reference_source": "previous_close",
            "reference_confidence": 0.25,
            "evidence": ["方向证据不足"],
            "_market_time": "15:29:30.000",
            "_last_market_trade_ts_ms": 300_000,
        }
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "view.sqlite3"
            database.touch()
            with patch.object(
                server, "_assessment_timeline",
                return_value=((200_000, 1_000_000), (prior, fallback)),
            ):
                parent = server._assessment_at(
                    database, "2026-08-24", "132024.SH", 1_000_000,
                    model_id="maker_priority_v1_46",
                    bid1=136.002, ask1=136.887,
                )
                candidate = server._assessment_at(
                    database, "2026-08-24", "132024.SH", 1_000_000,
                    model_id="maker_priority_v1_47",
                    bid1=136.002, ask1=136.887,
                )

        assert parent is not None and candidate is not None
        self.assertEqual(parent["reference_source"], "previous_close")
        self.assertEqual(candidate["reference_price"], 136.300)
        self.assertEqual(candidate["reference_low"], 136.298)
        self.assertEqual(candidate["reference_high"], 136.500)
        self.assertEqual(
            candidate["reference_source"],
            "retained_intraday_working_reference",
        )
        self.assertEqual(candidate["reference_confidence"], 0.35)

    def test_v147_display_requires_late_quiet_wide_straddling_market(self) -> None:
        prior = {
            "reference_price": 136.300,
            "reference_low": 136.298,
            "reference_high": 136.500,
            "reference_source": "intraday_trade_anchor",
            "reference_confidence": 0.75,
            "evidence": [],
        }
        fallback = {
            "reference_price": 136.938,
            "reference_low": 136.923,
            "reference_high": 136.953,
            "reference_source": "previous_close",
            "reference_confidence": 0.25,
            "evidence": [],
        }
        cases = {
            "before-1445": {
                "market_time": "14:44:59.999",
                "last_trade_ts_ms": 300_000,
                "bid1": 136.002,
                "ask1": 136.887,
            },
            "only-599-seconds-quiet": {
                "market_time": "15:29:30.000",
                "last_trade_ts_ms": 401_000,
                "bid1": 136.002,
                "ask1": 136.887,
            },
            "spread-below-040": {
                "market_time": "15:29:30.000",
                "last_trade_ts_ms": 300_000,
                "bid1": 136.100,
                "ask1": 136.499,
            },
            "market-no-longer-straddles-centre": {
                "market_time": "15:29:30.000",
                "last_trade_ts_ms": 300_000,
                "bid1": 137.100,
                "ask1": 137.600,
            },
        }
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "view.sqlite3"
            database.touch()
            for label, case in cases.items():
                current = {
                    **fallback,
                    "_market_time": case["market_time"],
                    "_last_market_trade_ts_ms": case["last_trade_ts_ms"],
                }
                with self.subTest(label=label), patch.object(
                    server, "_assessment_timeline",
                    return_value=(
                        (200_000, 1_000_000), (prior, current),
                    ),
                ):
                    result = server._assessment_at(
                        database, "2026-08-24", "132024.SH", 1_000_000,
                        model_id="maker_priority_v1_47",
                        bid1=case["bid1"], ask1=case["ask1"],
                    )
                assert result is not None
                self.assertEqual(result["reference_source"], "previous_close")


class OrderBoundaryViewTests(unittest.TestCase):
    def test_shared_thousand_model_uses_combination_account_name(self) -> None:
        meta = server._default_model_meta(
            "maker_shared_1000_v0_1_candidate",
            "0.1-candidate",
            "priority",
        )
        self.assertEqual(meta["short"], "千张第一顺位0.1")
        self.assertEqual(meta["branch"], "千张第一顺位")

        candidate = server._default_model_meta(
            "maker_shared_1000_v0_13_candidate",
            "0.13-candidate",
            "priority",
        )
        self.assertEqual(candidate["short"], "千张第一顺位0.13")
        self.assertEqual(candidate["branch"], "千张第一顺位")
        arrival = server._default_model_meta(
            "maker_shared_1000_v0_16_candidate_r2", "0.16-candidate-r2", "priority",
        )
        self.assertEqual(arrival["short"], "千张第一顺位0.16（实时修订）")
        self.assertEqual(arrival["branch"], "千张第一顺位")
        dadao = server._default_model_meta("maker_dadao_v0_1_candidate_r2", "0.1-candidate-r2", "priority")
        self.assertEqual(dadao["short"], "大道至简0.1（实时修订）")
        self.assertEqual(dadao["branch"], "大道至简")

    def test_v149_internal_audit_id_uses_unified_public_name(self) -> None:
        meta = server._default_model_meta(
            "maker_priority_v1_49_candidate_r2",
            "1.49-candidate-r2",
            "priority",
        )
        self.assertEqual(meta["short"], "第一顺位1.49")

    def test_v150_uses_public_first_position_name(self) -> None:
        meta = server._default_model_meta(
            "maker_priority_v1_50_candidate",
            "1.50-candidate",
            "priority",
        )
        self.assertEqual(meta["short"], "第一顺位1.50")

    def test_revised_v2_models_keep_public_first_position_names(self) -> None:
        for model_id, version, expected in (
            (
                "maker_priority_v2_5_candidate_r2",
                "2.5-candidate-r2",
                "第一顺位2.5",
            ),
            (
                "maker_priority_v2_51_candidate_r3",
                "2.51-candidate-r3",
                "第一顺位2.51",
            ),
            (
                "maker_priority_v2_52_candidate_r2",
                "2.52-candidate-r2",
                "第一顺位2.52",
            ),
            (
                "maker_priority_v2_6_candidate_r3",
                "2.6-candidate-r3",
                "第一顺位2.6",
            ),
            (
                "maker_priority_v2_63_candidate",
                "2.63-candidate",
                "第一顺位2.63",
            ),
            (
                "maker_priority_v2_70_candidate_r2",
                "2.70-candidate-r2",
                "第一顺位2.70",
            ),
            (
                "maker_priority_v2_71_candidate",
                "2.71-candidate",
                "第一顺位2.71",
            ),
        ):
            with self.subTest(model_id=model_id):
                meta = server._default_model_meta(
                    model_id, version, "priority",
                )
                self.assertEqual(meta["short"], expected)

    def test_unknown_storage_id_uses_short_branch_version_name(self) -> None:
        meta = server._default_model_meta(
            "maker_priority_internal_identifier",
            "1.44",
            "priority",
        )
        self.assertEqual(meta["short"], "第一顺位1.44")
        self.assertEqual(meta["status"], "模拟盘")
        self.assertNotIn("maker_priority", meta["short"])

    def test_frozen_buy_ceiling_and_sell_floor_are_exposed(self) -> None:
        self.assertEqual(
            server.order_price_boundary_view({
                "price_boundary": 137.735,
                "price_boundary_kind": "buy_ceiling",
            }, "buy"),
            (137.735, "buy_ceiling", "最高买价"),
        )
        self.assertEqual(
            server.order_price_boundary_view({
                "price_boundary": 138.205,
                "price_boundary_kind": "sell_floor",
            }, "sell"),
            (138.205, "sell_floor", "最低卖价"),
        )

    def test_legacy_order_boundary_is_not_recomputed(self) -> None:
        self.assertEqual(
            server.order_price_boundary_view({}, "buy"),
            (None, None, "极限价"),
        )

    def test_live_priority_replenishment_is_not_presented_as_a_ceiling(
        self,
    ) -> None:
        self.assertEqual(
            server.order_price_boundary_view({
                "price_boundary": 136.516,
                "price_boundary_kind": "live_priority_price",
            }, "buy"),
            (136.516, "live_priority_price", "当前跟随价"),
        )


class ClosingPnlViewTests(unittest.TestCase):
    def test_customer_base_buyback_gets_sell_then_buy_profit(self) -> None:
        accounts = [{
            "strategy_id": "paper-priority",
            "fill_mode": "priority",
            "initial_inventory": 1_000,
        }]
        fills = [
            {
                "id": 1, "strategy_id": "paper-priority", "side": "sell",
                "price": 137.382, "quantity": 1_000, "market_ts_ms": 1_000,
                "lot_id": 10, "lot_kind": "base",
            },
            {
                "id": 2, "strategy_id": "paper-priority", "side": "buy",
                "price": 136.999, "quantity": 1_000, "market_ts_ms": 2_000,
                "lot_id": 11, "lot_kind": "base",
            },
        ]
        result = server.closing_pnl_by_fill(fills, accounts)
        self.assertNotIn(1, result)
        self.assertAlmostEqual(result[2], 383.0)

    def test_close_fill_gets_fifo_realized_profit(self) -> None:
        accounts = [{
            "strategy_id": "paper-priority",
            "fill_mode": "priority",
            "initial_inventory": 1_000,
        }]
        fills = [
            {
                "id": 1, "strategy_id": "paper-priority", "side": "buy",
                "price": 136.200, "quantity": 300, "market_ts_ms": 1_000,
                "lot_id": 10, "lot_kind": "low_bid_reversion",
            },
            {
                "id": 2, "strategy_id": "paper-priority", "side": "sell",
                "price": 136.650, "quantity": 200, "market_ts_ms": 2_000,
                "lot_id": 10, "lot_kind": "low_bid_reversion",
            },
        ]
        result = server.closing_pnl_by_fill(fills, accounts)
        self.assertNotIn(1, result)
        self.assertAlmostEqual(result[2], 90.0)


class ReadOnlySnapshotTests(unittest.TestCase):
    @unittest.skipUnless(server.DEFAULT_DATABASE.exists(), "local paper database is absent")
    def test_current_matrix_and_units(self) -> None:
        snapshot = server.load_snapshot(server.DEFAULT_DATABASE, "132026.SH")
        self.assertTrue(snapshot["paper_only"])
        self.assertFalse(snapshot["approval_writes_database"])
        self.assertEqual(
            [account["model_id"] for account in snapshot["accounts"]],
            snapshot["model_source"]["model_ids"],
        )
        model_ids = {account["model_id"] for account in snapshot["accounts"]}
        self.assertNotIn("maker_priority_v1_43_candidate", model_ids)
        self.assertNotIn("maker_priority_v1_44", model_ids)
        self.assertTrue(all(account["status"] == "模拟盘" for account in snapshot["accounts"]))
        self.assertTrue(all("maker_" not in account["short"] for account in snapshot["accounts"]))
        self.assertTrue(all(level["quantity"] % 10 == 0 for level in snapshot["book"]["asks"]))
        self.assertTrue(all(order["paper_only"] for order in snapshot["open_orders"]))

    @unittest.skipUnless(server.DEFAULT_DATABASE.exists(), "local paper database is absent")
    def test_replay_excludes_future_market_and_fills(self) -> None:
        connection = sqlite3.connect(
            server.DEFAULT_DATABASE.resolve().as_uri() + "?mode=ro", uri=True
        )
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                """SELECT f.market_date,f.market_ts_ms,f.strategy_id,m.bond_code,m.model_id
                   FROM maker_paper_fills f
                   JOIN maker_paper_model_assignments m
                     ON m.market_date=f.market_date AND m.strategy_id=f.strategy_id
                   JOIN (
                       SELECT market_date,code,MIN(market_ts_ms) AS start_ts_ms
                       FROM raw_ticks GROUP BY market_date,code
                   ) r ON r.market_date=f.market_date AND r.code=m.bond_code
                   WHERE f.market_ts_ms>r.start_ts_ms
                   ORDER BY f.market_date DESC,f.market_ts_ms LIMIT 1"""
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            self.skipTest("no replayable paper fill")
        before = server.load_snapshot(
            server.DEFAULT_DATABASE,
            row["bond_code"],
            market_date=row["market_date"],
            target_ts_ms=int(row["market_ts_ms"]) - 1,
        )
        after = server.load_snapshot(
            server.DEFAULT_DATABASE,
            row["bond_code"],
            market_date=row["market_date"],
            target_ts_ms=int(row["market_ts_ms"]),
        )
        before_account = next(
            item for item in before["accounts"] if item["model_id"] == row["model_id"]
        )
        after_account = next(
            item for item in after["accounts"] if item["model_id"] == row["model_id"]
        )
        self.assertLess(before_account["fills"], after_account["fills"])
        self.assertLessEqual(
            max(item["ts"] for item in before["history"]), int(row["market_ts_ms"]) - 1
        )
        self.assertTrue(all(item["ts"] <= int(row["market_ts_ms"]) - 1 for item in before["actions"]))
        self.assertTrue(all(item["ts"] <= int(row["market_ts_ms"]) - 1 for item in before["market_trades"]))
        self.assertTrue(before["replay"]["causal_cutoff"])

    @unittest.skipUnless(server.DEFAULT_DATABASE.exists(), "local paper database is absent")
    def test_chart_history_covers_at_most_one_causal_hour(self) -> None:
        snapshot = server.load_snapshot(server.DEFAULT_DATABASE, "132026.SH")
        self.assertTrue(snapshot["history"])
        self.assertTrue(all(item["last"] > 0 for item in snapshot["history"]))
        self.assertGreaterEqual(
            snapshot["history"][0]["ts"], snapshot["market"]["market_ts_ms"] - 3_600_000
        )
        self.assertEqual(snapshot["history"][-1]["ts"], snapshot["market"]["market_ts_ms"])
        self.assertTrue(all(item["side_is_inferred"] for item in snapshot["market_trades"]))

    @unittest.skipUnless(server.DEFAULT_DATABASE.exists(), "local paper database is absent")
    def test_replay_order_lifecycle_is_reconstructed_at_target_time(self) -> None:
        connection = sqlite3.connect(
            server.DEFAULT_DATABASE.resolve().as_uri() + "?mode=ro", uri=True
        )
        connection.row_factory = sqlite3.Row
        try:
            row = connection.execute(
                """SELECT o.id,o.market_date,o.created_market_ts_ms,
                          o.updated_market_ts_ms,o.status,m.bond_code
                   FROM maker_paper_orders o
                   JOIN maker_paper_model_assignments m
                     ON m.market_date=o.market_date AND m.strategy_id=o.strategy_id
                   WHERE o.updated_market_ts_ms>o.created_market_ts_ms
                     AND o.status NOT IN ('open','partial')
                   ORDER BY o.market_date DESC,o.created_market_ts_ms DESC LIMIT 1"""
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            self.skipTest("no terminal paper order")
        before_creation = server.load_snapshot(
            server.DEFAULT_DATABASE,
            row["bond_code"],
            market_date=row["market_date"],
            target_ts_ms=int(row["created_market_ts_ms"]) - 1,
        )
        at_creation = server.load_snapshot(
            server.DEFAULT_DATABASE,
            row["bond_code"],
            market_date=row["market_date"],
            target_ts_ms=int(row["created_market_ts_ms"]),
        )
        self.assertNotIn(row["id"], {item["id"] for item in before_creation["lifecycle"]})
        visible = next(item for item in at_creation["lifecycle"] if item["id"] == row["id"])
        self.assertIn(visible["status"], {"open", "partial"})

    @unittest.skipUnless(server.DEFAULT_DATABASE.exists(), "local paper database is absent")
    def test_replay_reads_do_not_mutate_sqlite(self) -> None:
        connection = sqlite3.connect(
            server.DEFAULT_DATABASE.resolve().as_uri() + "?mode=ro", uri=True
        )
        before = tuple(
            connection.execute(
                "SELECT (SELECT COUNT(*) FROM maker_paper_orders),"
                "       (SELECT COUNT(*) FROM maker_paper_fills)"
            ).fetchone()
        )
        connection.close()
        meta = server.load_replay_metadata(server.DEFAULT_DATABASE, "132026.SH")
        day = next(item for item in meta["dates"] if item["has_accounts"])
        server.load_snapshot(
            server.DEFAULT_DATABASE,
            "132026.SH",
            market_date=day["date"],
            target_ts_ms=day["start_ts_ms"],
        )
        connection = sqlite3.connect(
            server.DEFAULT_DATABASE.resolve().as_uri() + "?mode=ro", uri=True
        )
        after = tuple(
            connection.execute(
                "SELECT (SELECT COUNT(*) FROM maker_paper_orders),"
                "       (SELECT COUNT(*) FROM maker_paper_fills)"
            ).fetchone()
        )
        connection.close()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
