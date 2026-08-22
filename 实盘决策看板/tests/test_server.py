from __future__ import annotations

import sys
import sqlite3
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server


class RefreshWindowTests(unittest.TestCase):
    def test_weekday_boundaries(self) -> None:
        self.assertFalse(server.refresh_window_active(datetime(2026, 8, 20, 9, 24, 59)))
        self.assertTrue(server.refresh_window_active(datetime(2026, 8, 20, 9, 25, 0)))
        self.assertTrue(server.refresh_window_active(datetime(2026, 8, 20, 15, 29, 59)))
        self.assertFalse(server.refresh_window_active(datetime(2026, 8, 20, 15, 30, 0)))

    def test_weekend_is_inactive(self) -> None:
        self.assertFalse(server.refresh_window_active(datetime(2026, 8, 22, 10, 0, 0)))


class OrderBoundaryViewTests(unittest.TestCase):
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
        self.assertEqual(len(snapshot["accounts"]), 6)
        model_ids = {account["model_id"] for account in snapshot["accounts"]}
        self.assertEqual(model_ids, set(server.MODEL_ORDER))
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
