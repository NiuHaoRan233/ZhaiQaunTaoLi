from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from zhaiquant.database import SQLiteStore
from zhaiquant.maker_dashboard import (
    MakerDashboardReader,
    build_daily_trades,
    dashboard_refresh_active,
    next_dashboard_refresh_start,
    render_dashboard,
)
from zhaiquant.types import SHANGHAI

from .helpers import make_tick, test_config


class MakerDashboardTests(unittest.TestCase):
    def test_continuous_refresh_window_is_0925_to_1530_weekdays(self) -> None:
        def moment(hour: int, minute: int, second: int = 0) -> datetime:
            return datetime(
                2026, 8, 14, hour, minute, second, tzinfo=SHANGHAI,
            )

        self.assertFalse(dashboard_refresh_active(moment(9, 24, 59)))
        self.assertTrue(dashboard_refresh_active(moment(9, 25)))
        self.assertTrue(dashboard_refresh_active(moment(15, 29, 59)))
        self.assertFalse(dashboard_refresh_active(moment(15, 30)))
        self.assertFalse(dashboard_refresh_active(datetime(
            2026, 8, 15, 10, 0, tzinfo=SHANGHAI,
        )))
        self.assertEqual(
            next_dashboard_refresh_start(moment(15, 30)),
            datetime(2026, 8, 17, 9, 25, tzinfo=SHANGHAI),
        )

    def test_read_only_dashboard_shows_account_order_lot_and_fill(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "dashboard.sqlite3"
            config = test_config(database)
            store = SQLiteStore(config)
            moment = datetime(2026, 8, 13, 10, 8, 12, tzinfo=SHANGHAI)
            tick = make_tick(
                config.qmt.bond_code, moment,
                last=137.198, bid=137.100, ask=137.198, level_volume=500,
            )
            tick_id = store.insert_tick(tick)
            strategy = "maker_v01_priority"
            market_date = "2026-08-13"
            store.upsert_maker_account({
                "market_date": market_date,
                "strategy_id": strategy,
                "fill_mode": "priority",
                "initial_inventory": 1_000,
                "maximum_inventory": 2_000,
                "initial_cash": 136_800,
                "cash": 137_732,
                "inventory": 1_000,
                "last_market_ts_ms": tick.market_ts_ms,
                "last_tick_id": tick_id,
                "last_bid": tick.bid1,
                "last_ask": tick.ask1,
                "trading_pnl": 932,
                "fills": 1,
                "updated_at_utc": "2026-08-13T02:08:12+00:00",
            })
            lot_id = store.insert_maker_lot({
                "run_id": store.run_id,
                "market_date": market_date,
                "strategy_id": strategy,
                "kind": "base",
                "opened_market_ts_ms": 0,
                "entry_price": None,
                "original_quantity": 1_000,
                "remaining_quantity": 1_000,
                "target_price": None,
                "status": "open",
                "updated_market_ts_ms": tick.market_ts_ms,
            })
            order_id = store.insert_maker_order({
                "run_id": store.run_id,
                "market_date": market_date,
                "strategy_id": strategy,
                "side": "sell",
                "status": "open",
                "kind": "inventory_exit",
                "lot_id": lot_id,
                "created_market_ts_ms": tick.market_ts_ms,
                "updated_market_ts_ms": tick.market_ts_ms,
                "limit_price": 137.198,
                "quantity": 1_000,
                "filled_quantity": 0,
                "queue_ahead": 5_000,
                "target_price": 137.198,
                "cancel_reason": None,
                "metadata_json": "{}",
            })
            store.insert_maker_fill({
                "run_id": store.run_id,
                "market_date": market_date,
                "strategy_id": strategy,
                "order_id": order_id,
                "lot_id": lot_id,
                "market_ts_ms": tick.market_ts_ms,
                "received_ts_ns": tick.received_ts_ns,
                "side": "sell",
                "price": 137.198,
                "quantity": 1_000,
                "fill_reason": "passive_sell",
                "reference_tick_id": tick_id,
                "cash_after": 137_732,
                "inventory_after": 1_000,
            })
            store.close()

            reader = MakerDashboardReader(database)
            try:
                self.assertEqual(
                    reader.connection.execute("PRAGMA query_only").fetchone()[0], 1
                )
                snapshot = reader.snapshot(market_date, config.qmt.bond_code)
                self.assertEqual(reader.fill_marker(market_date), (1, 1))
                lightweight = reader.snapshot(
                    market_date, config.qmt.bond_code,
                    include_assessment=False,
                )
            finally:
                reader.close()
            self.assertIsNone(lightweight["assessment"])
            output = render_dashboard(snapshot, now=moment)
            self.assertIn("132026.SH 做市模拟盘实时看板", output)
            self.assertIn("底仓持平", output)
            self.assertIn("137.198", output)
            self.assertIn("[本模型：交易员思考与应对预案]", output)
            self.assertIn("合理定价", output)
            self.assertIn("当前状态", output)
            self.assertIn("本模型向上应对", output)
            self.assertIn("本模型向下应对", output)
            self.assertIn("[本模型：当前模拟挂单]", output)
            self.assertIn("底仓高卖", output)
            self.assertIn("5,000", output)
            self.assertIn("09:25-15:30内有新成交立即刷新", output)
            self.assertIn("模拟账户彼此独立", output)
            self.assertIn("第一顺位（乐观成交假设", output)
            self.assertIn("本账户：今日全部成交流水", output)
            self.assertIn("本账户：今日尚未闭环交易", output)
            self.assertIn("新一轮看板刷新", output)
            self.assertIn("本轮刷新结束", output)
            self.assertNotIn("[当前持仓批次]", output)

    def test_two_fill_modes_render_in_separate_account_sections(self) -> None:
        accounts = [
            {
                "market_date": "2026-08-13",
                "strategy_id": "maker_v01_priority",
                "fill_mode": "priority",
                "initial_inventory": 1_000,
                "maximum_inventory": 2_000,
                "initial_cash": 136_800,
                "cash": 136_800,
                "inventory": 1_000,
                "trading_pnl": 100,
                "fills": 0,
            },
            {
                "market_date": "2026-08-13",
                "strategy_id": "maker_v01_queue",
                "fill_mode": "queue",
                "initial_inventory": 1_000,
                "maximum_inventory": 2_000,
                "initial_cash": 136_800,
                "cash": 136_800,
                "inventory": 1_000,
                "trading_pnl": 80,
                "fills": 0,
            },
        ]
        snapshot = {
            "market_date": "2026-08-13",
            "bond_code": "132026.SH",
            "bond_name": "G三峡EB2",
            "market": None,
            "accounts": accounts,
            "orders": [
                {
                    "strategy_id": "maker_v01_priority",
                    "side": "buy", "kind": "low_bid_reversion",
                    "limit_price": 136.111, "quantity": 1_000,
                    "filled_quantity": 0, "queue_ahead": 0,
                    "target_price": None,
                },
                {
                    "strategy_id": "maker_v01_queue",
                    "side": "buy", "kind": "low_bid_reversion",
                    "limit_price": 136.222, "quantity": 1_000,
                    "filled_quantity": 0, "queue_ahead": 2_000,
                    "target_price": None,
                },
            ],
            "lots": [],
            "fills": [],
            "session": None,
        }

        output = render_dashboard(
            snapshot,
            now=datetime(2026, 8, 13, 10, 0, tzinfo=SHANGHAI),
        )

        self.assertIn("G三峡EB2（132026.SH）", output)

        priority = output.index(">>> 模型区块 1/2 开始  |  第一顺位")
        queue = output.index(">>> 模型区块 2/2 开始  |  排队成交")
        self.assertLess(priority, queue)
        self.assertEqual(output.count("[本账户：今日全部成交流水]"), 2)
        self.assertEqual(output.count("[本模型：交易员思考与应对预案]"), 2)
        self.assertEqual(output.count("[本模型：当前模拟挂单]"), 2)
        self.assertIn("改善一厘争取第一顺位", output[priority:queue])
        self.assertIn("先消耗真实前方队列", output[queue:])
        self.assertIn("136.111", output[priority:queue])
        self.assertNotIn("136.222", output[priority:queue])
        self.assertIn("136.222", output[queue:])
        self.assertIn("盯市毛收益 +100.00元", output[priority:queue])
        self.assertIn("盯市毛收益 +80.00元", output[queue:])
        self.assertNotIn("[当前持仓批次]", output)
        self.assertNotIn("\n[当前模拟挂单]", output)

    def test_daily_trade_pairing_handles_partial_replenishment_fifo(self) -> None:
        accounts = [{
            "strategy_id": "maker_v01_queue",
            "fill_mode": "queue",
            "initial_inventory": 1_000,
        }]
        fills = [
            self._fill(1, "sell", 136.983, 480, 100_000, "inventory_replenish"),
            self._fill(2, "sell", 136.984, 480, 190_000, "inventory_replenish"),
            self._fill(3, "buy", 136.601, 380, 300_000, "inventory_replenish"),
            self._fill(4, "buy", 136.601, 580, 400_000, "inventory_replenish"),
        ]

        completed, unfinished = build_daily_trades(fills, accounts)

        self.assertEqual(len(completed), 2)
        self.assertEqual(unfinished, [])
        self.assertEqual([item["quantity"] for item in completed], [480, 480])
        self.assertAlmostEqual(sum(item["gross_pnl"] for item in completed), 367.20)
        self.assertEqual(len(completed[0]["close_details"]), 2)
        self.assertEqual(completed[0]["holding_seconds"], 300)
        self.assertEqual(completed[1]["holding_seconds"], 210)

    def test_lot_aware_pairing_never_matches_base_sell_to_extra_buy(self) -> None:
        accounts = [{
            "strategy_id": "maker_v01_queue",
            "fill_mode": "queue",
            "initial_inventory": 1_000,
        }]
        fills = [
            {**self._fill(1, "sell", 137.650, 1_000, 100_000, "base"),
             "lot_id": 10},
            {**self._fill(2, "buy", 136.160, 1_000, 200_000, "base"),
             "lot_id": 11},
            {**self._fill(3, "buy", 137.053, 1_000, 300_000,
                          "low_bid_reversion"), "lot_id": 12},
            {**self._fill(4, "sell", 137.298, 1_000, 400_000,
                          "low_bid_reversion"), "lot_id": 12},
        ]

        completed, unfinished = build_daily_trades(fills, accounts)

        self.assertEqual(unfinished, [])
        self.assertEqual(len(completed), 2)
        self.assertAlmostEqual(sum(item["gross_pnl"] for item in completed), 1_735)
        self.assertTrue(all(item["gross_pnl"] > 0 for item in completed))

    def test_dashboard_filters_accounts_by_selected_bond(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "maker-dashboard-filter.sqlite3"
            config = test_config(database)
            store = SQLiteStore(config)
            moment = datetime(2026, 8, 14, 10, 0, tzinfo=SHANGHAI)
            tick = make_tick(
                "132024.SH", moment,
                last=136.8, bid=136.5, ask=136.8,
            )
            tick_id = store.insert_tick(tick)
            common = {
                "market_date": "2026-08-14",
                "fill_mode": "priority",
                "initial_inventory": 1_000,
                "maximum_inventory": 2_000,
                "initial_cash": 136_800,
                "cash": 136_800,
                "inventory": 1_000,
                "last_market_ts_ms": tick.market_ts_ms,
                "last_tick_id": tick_id,
                "last_bid": 136.5,
                "last_ask": 136.8,
                "trading_pnl": 0,
                "fills": 0,
                "updated_at_utc": moment.isoformat(),
            }
            store.upsert_maker_account({
                **common, "strategy_id": "maker_v01_priority",
            })
            store.upsert_maker_account({
                **common, "strategy_id": "maker_132024_v01_priority",
            })
            store.flush(force=True)
            store.close()

            reader = MakerDashboardReader(database)
            try:
                snapshot = reader.snapshot(
                    "2026-08-14", "132024.SH",
                    strategy_ids=("maker_132024_v01_priority",),
                )
            finally:
                reader.close()
            self.assertEqual(
                [account["strategy_id"] for account in snapshot["accounts"]],
                ["maker_132024_v01_priority"],
            )

    @staticmethod
    def _fill(
        fill_id: int, side: str, price: float, quantity: float,
        market_ts_ms: int, lot_kind: str,
    ) -> dict[str, object]:
        return {
            "id": fill_id,
            "strategy_id": "maker_v01_queue",
            "market_ts_ms": market_ts_ms,
            "side": side,
            "price": price,
            "quantity": quantity,
            "lot_kind": lot_kind,
        }


if __name__ == "__main__":
    unittest.main()
