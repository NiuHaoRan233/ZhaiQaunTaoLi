from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from zhaiquant.config import MakerPaperConfig
from zhaiquant.database import SQLiteStore
from zhaiquant.maker import (
    AnchorState,
    MarketAssessment,
    Opportunity,
    ReplayTick,
)
from zhaiquant.maker_paper import (
    MakerPaperEngine,
    MakerPaperPortfolio,
    _floor_to_tick,
)
from zhaiquant.runner import MarketProcessor
from zhaiquant.types import SHANGHAI

from .helpers import make_tick, test_config


class MakerPaperTests(unittest.TestCase):
    def test_two_bonds_have_independent_maker_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-multi.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                bond_codes=(base.qmt.bond_code, "132024.SH"),
                fill_modes=("priority", "queue"),
            ))
            store = SQLiteStore(config)
            processor = MarketProcessor(config, store, preload_m0_history=False)
            self.assertIsInstance(processor.maker_paper, MakerPaperPortfolio)
            start = datetime(2026, 8, 14, 10, 0, tzinfo=SHANGHAI)

            processor.process(make_tick(
                config.qmt.stock_code, start,
                last=28.0, bid=27.99, ask=28.01,
            ))
            processor.process(make_tick(
                "132024.SH", start,
                last=136.8, bid=136.5, ask=136.8,
            ))

            accounts = processor.maker_paper.accounts
            self.assertEqual(set(accounts), {
                "maker_v01_priority", "maker_v01_queue",
                "maker_132024_v01_priority", "maker_132024_v01_queue",
            })
            self.assertEqual(accounts["maker_v01_priority"].last_bid, 0.0)
            self.assertEqual(
                accounts["maker_132024_v01_priority"].last_bid, 136.5
            )
            self.assertEqual(
                store.connection.execute(
                    "SELECT COUNT(*) FROM maker_paper_accounts"
                ).fetchone()[0],
                4,
            )
            summary = processor.maker_paper.runtime_summary()
            self.assertEqual(
                {account["bond_code"] for account in summary["accounts"]},
                {"132026.SH", "132024.SH"},
            )
            store.close()

    @staticmethod
    def _replay_tick(
        moment: datetime, *, last: float, bid: float, ask: float,
        trade_bonds: float = 0.0, inferred_side: str = "none",
        previous_close: float = 136.867, ask_bonds: float = 1_000.0,
        bid_bonds: float = 1_000.0,
    ) -> ReplayTick:
        return ReplayTick(
            tick_id=int(moment.timestamp()),
            code="132026.SH",
            market_ts_ms=int(moment.timestamp() * 1000),
            market_date=moment.date().isoformat(),
            market_time=moment.time().isoformat(timespec="milliseconds"),
            last_price=last,
            bids=((bid, bid_bonds),),
            asks=((ask, ask_bonds),),
            trade_bonds=trade_bonds,
            transaction_delta=1 if trade_bonds else 0,
            inferred_side=inferred_side,
            side_confidence="high" if trade_bonds else "none",
            previous_close=previous_close,
        )

    def test_opening_previous_close_and_deep_discount_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-open.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority", "queue"),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 13, 9, 30, tzinfo=SHANGHAI)

            # The opening ask is already 1.08 yuan above yesterday's close,
            # so both fill assumptions must have a sell resting before 137.950 trades.
            engine.on_replay_tick(self._replay_tick(
                start, last=136.867, bid=137.400, ask=137.949,
            ), persist=True)
            self.assertEqual(
                next(iter(
                    engine.accounts["maker_v01_priority"].sell_orders.values()
                )).limit_price,
                137.948,
            )
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=3), last=136.867,
                bid=137.400, ask=137.948,
            ), persist=True)
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=21), last=137.950,
                bid=137.400, ask=137.950, trade_bonds=2_000,
                inferred_side="buy", ask_bonds=5_000,
            ), persist=True)
            for account in engine.accounts.values():
                self.assertEqual(account.inventory, 0)

            # A displayed 135.200 ask is actively bought. A one-tick repeat is
            # not counted again; 135.114 is a distinct lower opportunity.
            t = start.replace(hour=9, minute=51, second=30)
            engine.on_replay_tick(self._replay_tick(
                t, last=137.950, bid=135.100, ask=135.200,
            ), persist=True)
            for account in engine.accounts.values():
                self.assertEqual(account.inventory, 1_000)
            engine.on_replay_tick(self._replay_tick(
                t + timedelta(seconds=3), last=137.950,
                bid=135.100, ask=135.199,
            ), persist=True)
            for account in engine.accounts.values():
                self.assertEqual(account.inventory, 1_000)
            engine.on_replay_tick(self._replay_tick(
                t + timedelta(seconds=15), last=135.200,
                bid=135.000, ask=135.114, trade_bonds=1_000,
                inferred_side="sell",
            ), persist=True)
            for account in engine.accounts.values():
                self.assertEqual(account.inventory, 2_000)

            fills = store.connection.execute(
                """SELECT strategy_id,side,price,fill_reason
                   FROM maker_paper_fills ORDER BY id"""
            ).fetchall()
            active_prices = [
                round(float(row["price"]), 3) for row in fills
                if row["fill_reason"] == "active_deep_discount"
            ]
            self.assertEqual(active_prices, [135.2, 135.2, 135.114, 135.114])

            # Exit quotes must follow the current ask instead of keeping the
            # historical anchor that existed when the lot was bought.
            engine.on_replay_tick(self._replay_tick(
                t + timedelta(seconds=16), last=135.200,
                bid=136.500, ask=136.800, ask_bonds=6_000,
            ), persist=True)
            self.assertEqual(
                {order.limit_price for order in
                 engine.accounts["maker_v01_priority"].sell_orders.values()},
                {136.799},
            )
            self.assertEqual(
                {order.limit_price for order in
                 engine.accounts["maker_v01_queue"].sell_orders.values()},
                {136.8},
            )
            engine.on_replay_tick(self._replay_tick(
                t + timedelta(seconds=17), last=135.200,
                bid=135.000, ask=135.150, ask_bonds=0,
            ), persist=True)
            for account in engine.accounts.values():
                self.assertEqual(account.sell_orders, {})

            # A later one-yuan downward repricing is classified as falling.
            # Priority 1.1 must not let the old 137.284 sale manufacture a
            # "cheap" buy. Queue 1.0 deliberately preserves the 2026-08-13
            # baseline behavior until that branch is upgraded separately.
            engine.on_replay_tick(self._replay_tick(
                t + timedelta(seconds=18), last=135.200,
                bid=137.000, ask=137.284, ask_bonds=0,
            ), persist=True)
            engine.on_replay_tick(self._replay_tick(
                t + timedelta(seconds=24), last=137.284,
                bid=137.000, ask=137.284, trade_bonds=1_000,
                inferred_side="buy", ask_bonds=0,
            ), persist=True)
            self.assertEqual(
                engine.accounts["maker_v01_priority"].inventory, 1_000,
            )
            self.assertEqual(
                engine.accounts["maker_v01_queue"].inventory, 1_000,
            )
            later = start.replace(hour=10, minute=7, second=45)
            engine.on_replay_tick(self._replay_tick(
                later, last=136.210, bid=136.000, ask=136.199,
                trade_bonds=3_000, inferred_side="sell",
            ), persist=True)
            self.assertEqual(
                engine.accounts["maker_v01_priority"].inventory, 1_000,
            )
            self.assertEqual(
                engine.accounts["maker_v01_queue"].inventory, 2_000,
            )
            active_prices = [
                round(float(row["price"]), 3)
                for row in store.connection.execute(
                    """SELECT price FROM maker_paper_fills
                       WHERE fill_reason='active_deep_discount' ORDER BY id"""
                )
            ]
            self.assertEqual(
                active_prices,
                [135.2, 135.2, 135.114, 135.114, 136.199],
            )

            assignments = {
                row["strategy_id"]: row["model_id"]
                for row in store.connection.execute(
                    "SELECT strategy_id,model_id "
                    "FROM maker_paper_model_assignments"
                )
            }
            self.assertEqual(
                assignments["maker_v01_priority"], "maker_priority_v1_1",
            )
            self.assertEqual(
                assignments["maker_v01_queue"], "maker_queue_v1_0",
            )

            store.close()

    def test_simulated_limit_prices_are_floored_to_exchange_tick(self) -> None:
        self.assertEqual(_floor_to_tick(137.291064, 0.001), 137.291)
        self.assertEqual(_floor_to_tick(136.999999, 0.001), 136.999)

    def test_priority_account_rolls_inventory_without_broker_orders(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-paper.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            processor = MarketProcessor(config, store, preload_m0_history=False)
            start = datetime(2026, 8, 13, 10, 0, tzinfo=SHANGHAI)

            # Establish a 137.000 active-buy anchor with 6,000 bonds.
            processor.process(make_tick(
                config.qmt.stock_code, start,
                last=28.0, bid=27.99, ask=28.01,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, start,
                last=137.0, bid=136.8, ask=137.0,
                volume=100, amount=137_000, transactions=1,
                level_volume=100,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=3),
                last=137.0, bid=136.8, ask=137.0,
                volume=700, amount=959_000, transactions=2,
                level_volume=100,
            ))

            # A low bid creates a priority buy at 136.401; the next sell fills it.
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=6),
                last=137.0, bid=136.4, ask=137.0,
                volume=700, amount=959_000, transactions=2,
                level_volume=100,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=9),
                last=136.401, bid=136.4, ask=137.0,
                volume=800, amount=1_095_401, transactions=3,
                level_volume=100,
            ))

            account = processor.maker_paper.accounts["maker_v01_priority"]
            self.assertEqual(account.inventory, 2_000)
            self.assertGreaterEqual(account.cash, 0)
            self.assertEqual(
                store.connection.execute(
                    "SELECT COUNT(*) FROM maker_paper_fills WHERE side='buy'"
                ).fetchone()[0],
                1,
            )

            # An active buy at the maker exits consumes both 1,000-bond sell lots.
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=12),
                last=137.0, bid=136.8, ask=137.0,
                volume=1_000, amount=1_369_401, transactions=5,
                level_volume=200,
            ))
            self.assertLessEqual(account.inventory, 2_000)
            self.assertGreater(
                store.connection.execute(
                    "SELECT COUNT(*) FROM maker_paper_fills WHERE side='sell'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                store.connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'xttrader%'"
                ).fetchone()[0],
                0,
            )
            store.close()

    def test_sold_base_inventory_keeps_an_exact_replenishment_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-replenish.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("queue",),
            ))
            store = SQLiteStore(config)
            processor = MarketProcessor(config, store, preload_m0_history=False)
            start = datetime(2026, 8, 13, 10, 0, tzinfo=SHANGHAI)

            processor.process(make_tick(
                config.qmt.stock_code, start,
                last=28.0, bid=27.99, ask=28.01,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, start,
                last=137.0, bid=136.8, ask=137.0,
                volume=100, amount=137_000, transactions=1,
                level_volume=100,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=3),
                last=137.0, bid=136.8, ask=137.0,
                volume=700, amount=959_000, transactions=2,
                level_volume=100,
            ))

            account = processor.maker_paper.accounts["maker_v01_queue"]
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=4),
                last=137.0, bid=136.95, ask=137.3,
                volume=700, amount=959_000, transactions=2,
                level_volume=600,
            ))
            base_order = next(iter(account.sell_orders.values()))
            sell_time = start + timedelta(seconds=5)
            sell_tick = ReplayTick(
                tick_id=999,
                code=config.qmt.bond_code,
                market_ts_ms=int(sell_time.timestamp() * 1000),
                market_date=sell_time.date().isoformat(),
                market_time=sell_time.time().isoformat(timespec="milliseconds"),
                last_price=base_order.limit_price,
                bids=((136.95, 1_000),),
                asks=((base_order.limit_price, 1_000),),
                trade_bonds=960,
                transaction_delta=1,
                inferred_side="buy",
                side_confidence="high",
            )
            processor.maker_paper._fill_sell(
                account, sell_tick, base_order, 960,
                int(sell_time.timestamp() * 1_000_000_000),
                persist=True,
            )

            # Refresh on a valid book whose normal entry edge is below 0.20.
            # Replenishment must still quote exactly the 960-bond deficit.
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=6),
                last=137.0, bid=136.95, ask=137.0,
                volume=700, amount=959_000, transactions=2,
                level_volume=100,
            ))
            self.assertEqual(account.inventory, 40)
            self.assertIsNotNone(account.buy_order)
            self.assertEqual(account.buy_order.kind, "inventory_replenish")
            self.assertEqual(account.buy_order.quantity, 960)
            self.assertEqual(account.buy_order.limit_price, 136.95)
            self.assertEqual(account.replenishment_quantity, 960)
            # The restored quantity is base inventory again, so it must not
            # carry the historical replenishment entry as an exit constraint.
            base_lots = [
                lot for lot in account.lots.values() if lot.kind == "base"
            ]
            self.assertTrue(base_lots)
            self.assertTrue(all(lot.entry_price is None for lot in base_lots))
            store.close()

    def test_small_edge_without_book_support_does_not_quote_buy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-thin-book.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            moment = datetime(2026, 8, 13, 10, 0, tzinfo=SHANGHAI)
            engine.on_replay_tick(self._replay_tick(
                moment, last=136.867, bid=136.600, ask=136.850,
                trade_bonds=1_000, inferred_side="sell",
                previous_close=136.867, ask_bonds=1_000,
            ), persist=True)
            account = engine.accounts["maker_v01_priority"]
            self.assertIsNone(account.buy_order)
            self.assertEqual(account.inventory, 1_000)
            engine.on_replay_tick(self._replay_tick(
                moment + timedelta(seconds=3),
                last=136.867, bid=136.600, ask=136.850,
                previous_close=136.867, bid_bonds=6_000,
            ), persist=True)
            self.assertIsNotNone(account.buy_order)
            self.assertEqual(account.buy_order.limit_price, 136.601)
            store.close()

    def test_persistent_early_book_allows_current_ask_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-early-book.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 14, 9, 40, 30, tzinfo=SHANGHAI)

            # The opening trade anchor is still weak, but the 136.003/136.687
            # market remains stable after actual selling begins.  This local
            # band must replace yesterday's close as the causal early reference.
            for seconds in range(0, 28, 3):
                trade_bonds = (
                    380 if seconds == 0 else 1_000 if seconds == 27 else 0
                )
                engine.on_replay_tick(self._replay_tick(
                    start + timedelta(seconds=seconds),
                    last=136.003 if trade_bonds else 136.835,
                    bid=136.003, ask=136.687,
                    bid_bonds=6_000, ask_bonds=920,
                    trade_bonds=trade_bonds,
                    inferred_side="sell" if trade_bonds else "none",
                    previous_close=136.867,
                ), persist=True)

            account = engine.accounts["maker_v01_priority"]
            self.assertEqual(account.inventory, 2_000)
            self.assertAlmostEqual(
                engine.analyzer.persistent_book_reference(
                    int((start + timedelta(seconds=27)).timestamp() * 1000)
                ) or 0.0,
                136.345,
            )
            sell_order = next(iter(account.sell_orders.values()))
            self.assertEqual(sell_order.limit_price, 136.686)
            store.close()

    def test_persistent_book_alone_cannot_trigger_active_buy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-book-no-active.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 14, 9, 50, tzinfo=SHANGHAI)

            for seconds in range(0, 19, 3):
                engine.on_replay_tick(self._replay_tick(
                    start + timedelta(seconds=seconds),
                    last=136.500, bid=136.000, ask=137.000,
                    trade_bonds=1_000 if seconds == 0 else 0,
                    inferred_side="buy" if seconds == 0 else "none",
                    previous_close=136.000,
                ), persist=True)
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=21),
                last=136.500, bid=135.500, ask=135.900,
                previous_close=136.000,
            ), persist=True)

            account = engine.accounts["maker_v01_priority"]
            self.assertEqual(account.inventory, 1_000)
            active_fills = store.connection.execute(
                """SELECT COUNT(*) FROM maker_paper_fills
                   WHERE fill_reason='active_deep_discount'"""
            ).fetchone()[0]
            self.assertEqual(active_fills, 0)
            store.close()

    def test_stable_wide_book_keeps_passive_t_bid_inside_the_spread(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-stable-wide.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 14, 11, 15, 27, tzinfo=SHANGHAI)

            for seconds in range(0, 22, 3):
                engine.on_replay_tick(self._replay_tick(
                    start + timedelta(seconds=seconds),
                    last=136.521, bid=136.521, ask=136.781,
                    bid_bonds=6_000,
                    trade_bonds=1_000 if seconds == 0 else 0,
                    inferred_side="sell" if seconds == 0 else "none",
                    previous_close=136.922,
                ), persist=True)

            account = engine.accounts["maker_v01_priority"]
            context = engine._decision_context(self._replay_tick(
                start + timedelta(seconds=21),
                last=136.521, bid=136.521, ask=136.781,
                bid_bonds=6_000, previous_close=136.922,
            ))
            self.assertEqual(
                context.reference_source, "persistent_inside_market"
            )
            self.assertLess(
                context.reference_price - 136.522,
                engine.parameters.minimum_entry_edge,
            )
            self.assertIsNotNone(account.buy_order)
            self.assertEqual(account.buy_order.limit_price, 136.522)
            store.close()

    def test_moderate_discount_with_support_remains_passive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-moderate-passive.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            moment = datetime(2026, 8, 14, 9, 51, tzinfo=SHANGHAI)

            engine.on_replay_tick(self._replay_tick(
                moment, last=136.690, bid=136.003, ask=136.690,
                bid_bonds=6_000, ask_bonds=1_000,
                trade_bonds=1_000, inferred_side="sell",
                previous_close=136.922,
            ), persist=True)

            account = engine.accounts["maker_v01_priority"]
            self.assertEqual(account.inventory, 1_000)
            self.assertIsNotNone(account.buy_order)
            active_fills = store.connection.execute(
                """SELECT COUNT(*) FROM maker_paper_fills
                   WHERE fill_reason='active_deep_discount'"""
            ).fetchone()[0]
            self.assertEqual(active_fills, 0)
            store.close()

    def test_cheap_buy_exits_at_current_wall_instead_of_old_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-current-wall.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority", "queue"),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 13, 10, 16, 36, tzinfo=SHANGHAI)
            engine.on_replay_tick(self._replay_tick(
                start, last=137.600, bid=137.200, ask=137.600,
                trade_bonds=6_000, inferred_side="buy", ask_bonds=3_000,
            ), persist=True)
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=36),
                last=137.200, bid=136.801, ask=137.000,
                ask_bonds=1_000,
            ), persist=True)
            for account in engine.accounts.values():
                self.assertEqual(account.inventory, 2_000)
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=45),
                last=137.000, bid=136.801, ask=137.660,
                ask_bonds=3_280,
            ), persist=True)
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=54),
                last=137.000, bid=136.801, ask=137.650,
                ask_bonds=1_000,
            ), persist=True)
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=60),
                last=137.660, bid=136.801, ask=137.660,
                trade_bonds=3_000, inferred_side="buy", ask_bonds=1_280,
            ), persist=True)
            fills = store.connection.execute(
                """SELECT strategy_id,side,price FROM maker_paper_fills
                   WHERE side='sell' ORDER BY strategy_id"""
            ).fetchall()
            exits = {
                row["strategy_id"]: round(float(row["price"]), 3)
                for row in fills
            }
            self.assertEqual(exits["maker_v01_priority"], 137.649)
            self.assertEqual(exits["maker_v01_queue"], 137.65)
            store.close()

    def test_base_inventory_is_not_sold_at_dynamic_fair_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-base-high-sell.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority", "queue"),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 13, 14, 10, tzinfo=SHANGHAI)

            # Establish 136.800 as the current reliable transaction anchor.
            engine.on_replay_tick(self._replay_tick(
                start, last=136.800, bid=136.700, ask=136.800,
                trade_bonds=10_000, inferred_side="buy", ask_bonds=6_000,
            ), persist=True)
            for account in engine.accounts.values():
                self.assertEqual(account.sell_orders, {})

            # A thick offer 0.30 above fair value is a genuine high-sale setup.
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=3), last=136.800,
                bid=136.900, ask=137.100, ask_bonds=6_000,
            ), persist=True)
            self.assertEqual(
                next(iter(
                    engine.accounts["maker_v01_priority"].sell_orders.values()
                )).limit_price,
                137.099,
            )
            self.assertEqual(
                next(iter(
                    engine.accounts["maker_v01_queue"].sell_orders.values()
                )).limit_price,
                137.1,
            )
            store.close()

    def test_large_buy_breakout_turns_swept_price_into_support(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-breakout-support.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 13, 14, 35, 39, tzinfo=SHANGHAI)
            engine.on_replay_tick(self._replay_tick(
                start - timedelta(seconds=3), last=136.900,
                bid=136.700, ask=137.000,
                trade_bonds=10_000, inferred_side="buy",
                ask_bonds=11_000,
            ), persist=True)
            anchor = AnchorState(
                support_price=136.900, exit_price=137.000,
                band_midpoint=136.950, reference_price=136.950,
                confidence=0.8, buy_effective_bonds=10_000,
                sell_effective_bonds=0, downside_pressure=0,
                stock_return_5m=None, stock_factor=1.0,
                buy_clusters=(), sell_reference_price=None,
            )
            opportunity = Opportunity(
                kind="sweep_tail", signal_ts_ms=int(start.timestamp() * 1000),
                market_time=start.time().isoformat(timespec="milliseconds"),
                entry_price=137.000, quantity_bonds=1_000,
                target_exit_price=137.288, priority_exit_price=137.287,
                theoretical_edge=0.287, anchor=anchor,
            )
            engine.analyzer.breakout_support_price = 137.000
            engine.analyzer.breakout_support_ts_ms = int(start.timestamp() * 1000)
            account = engine.accounts["maker_v01_priority"]

            # A full base position must not chase the breakout price into an
            # extra lot; buying near support is only for restoring a deficit.
            engine._active_sweep(account, self._replay_tick(
                start, last=137.000, bid=136.800, ask=137.000,
                ask_bonds=2_000,
            ), opportunity, persist=True)
            self.assertEqual(account.inventory, 1_000)

            # Base inventory is not sold back at 136.999/137.000 while the
            # 137 breakout support remains strong.
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=3), last=137.000,
                bid=136.700, ask=137.000, ask_bonds=12_000,
            ), persist=True)
            self.assertEqual(account.sell_orders, {})

            # Roughly 0.20 below support belongs on the passive bid; it is not
            # enough discount to cross the spread while the tape is strong.
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=5), last=137.000,
                bid=136.600, ask=136.804, ask_bonds=1_000,
            ), persist=True)
            self.assertEqual(account.inventory, 1_000)

            # A 136.708 ask is a roughly 0.30 discount to the new 137 support
            # and should be actively taken as the one extra-inventory lot.
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=6), last=136.708,
                bid=136.600, ask=136.708, ask_bonds=1_000,
            ), persist=True)
            self.assertEqual(account.inventory, 2_000)
            extra_lots = [
                lot for lot in account.lots.values()
                if lot.entry_price is not None and lot.remaining_quantity > 0
            ]
            self.assertEqual(len(extra_lots), 1)
            self.assertEqual(extra_lots[0].entry_price, 136.708)

            # That discounted extra lot may exit around the 137 support, but
            # the base lot still has no sell order there.
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=9), last=136.708,
                bid=136.800, ask=137.000, ask_bonds=12_000,
            ), persist=True)
            orders = account.sell_orders
            self.assertEqual(set(orders), {extra_lots[0].db_id})
            self.assertEqual(orders[extra_lots[0].db_id].limit_price, 136.999)

            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=12), last=137.000,
                bid=136.800, ask=137.000, ask_bonds=11_000,
                trade_bonds=1_000, inferred_side="buy",
            ), persist=True)
            self.assertEqual(account.inventory, 1_000)
            store.close()

    def test_near_flat_extra_inventory_exits_before_downside_book_vacuum(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-downside-risk.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 14, 11, 18, 30, tzinfo=SHANGHAI)

            def risk_tick(
                moment: datetime, *, ask: float,
                trade_bonds: float = 0.0,
            ) -> ReplayTick:
                return replace(
                    self._replay_tick(
                        moment, last=136.521, bid=136.520, ask=ask,
                        trade_bonds=trade_bonds,
                        inferred_side="sell" if trade_bonds else "none",
                        previous_close=136.867,
                    ),
                    bids=(
                        (136.520, 2_000),
                        (136.351, 1_000),
                        (136.350, 1_860),
                        (136.053, 4_000),
                        (136.052, 1_000),
                    ),
                    asks=(
                        (ask, 1_000),
                        (ask + 0.001, 2_000),
                        (ask + 0.100, 1_000),
                    ),
                )

            first = risk_tick(start, ask=136.907, trade_bonds=3_000)
            engine.on_replay_tick(first, persist=True)
            account = engine.accounts["maker_v01_priority"]

            # Reproduce the already-correct 136.522 low fill.  The new rule
            # evaluates what to do after that fill; it must not reject it.
            buy_order = engine._new_order(
                account, first, side="buy", kind="low_bid_reversion",
                lot_id=None, price=136.522, quantity=1_000,
                queue_ahead=0.0, target_price=None, persist=True,
            )
            account.buy_order = buy_order
            engine._fill_buy(
                account, first, buy_order, 1_000,
                first.market_ts_ms * 1_000_000,
                kind="low_bid_reversion", target_price=None,
                persist=True,
            )
            self.assertEqual(account.inventory, 2_000)

            engine.on_replay_tick(risk_tick(
                start + timedelta(seconds=9), ask=136.907,
                trade_bonds=3_000,
            ), persist=True)
            self.assertEqual(account.inventory, 2_000)

            # The offer has compressed by 0.107 in thirty seconds while the
            # 136.520 bid still exists above a two-step downside vacuum.
            engine.on_replay_tick(risk_tick(
                start + timedelta(seconds=33), ask=136.800,
            ), persist=True)

            self.assertEqual(account.inventory, 1_000)
            exit_fill = store.connection.execute(
                """SELECT side,price,quantity,fill_reason,inventory_after
                   FROM maker_paper_fills
                   WHERE fill_reason='active_downside_risk_exit'"""
            ).fetchone()
            self.assertIsNotNone(exit_fill)
            self.assertEqual(exit_fill["side"], "sell")
            self.assertEqual(float(exit_fill["price"]), 136.520)
            self.assertEqual(float(exit_fill["quantity"]), 1_000)
            self.assertEqual(float(exit_fill["inventory_after"]), 1_000)
            store.close()

    def test_downside_vacuum_does_not_force_a_large_loss_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-downside-loss.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 14, 11, 18, 30, tzinfo=SHANGHAI)

            def risk_tick(moment: datetime, ask: float) -> ReplayTick:
                return replace(
                    self._replay_tick(
                        moment, last=136.520, bid=136.520, ask=ask,
                        trade_bonds=3_000 if ask == 136.907 else 0,
                        inferred_side="sell" if ask == 136.907 else "none",
                    ),
                    bids=(
                        (136.520, 2_000), (136.351, 1_000),
                        (136.350, 1_860), (136.053, 4_000),
                    ),
                    asks=((ask, 1_000), (ask + 0.001, 1_000)),
                )

            first = risk_tick(start, 136.907)
            engine.on_replay_tick(first, persist=True)
            account = engine.accounts["maker_v01_priority"]
            order = engine._new_order(
                account, first, side="buy", kind="low_bid_reversion",
                lot_id=None, price=136.600, quantity=1_000,
                queue_ahead=0.0, target_price=None, persist=True,
            )
            account.buy_order = order
            engine._fill_buy(
                account, first, order, 1_000,
                first.market_ts_ms * 1_000_000,
                kind="low_bid_reversion", target_price=None, persist=True,
            )
            engine.on_replay_tick(risk_tick(
                start + timedelta(seconds=9), 136.907,
            ), persist=True)
            engine.on_replay_tick(risk_tick(
                start + timedelta(seconds=33), 136.800,
            ), persist=True)

            self.assertEqual(account.inventory, 2_000)
            risk_fills = store.connection.execute(
                """SELECT COUNT(*) FROM maker_paper_fills
                   WHERE fill_reason='active_downside_risk_exit'"""
            ).fetchone()[0]
            self.assertEqual(risk_fills, 0)
            store.close()

    def test_compressed_offer_is_a_second_profitable_exit_opportunity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-second-exit.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 14, 11, 18, 0, tzinfo=SHANGHAI)
            first = self._replay_tick(
                start, last=136.522, bid=136.520, ask=136.739,
                previous_close=136.867,
            )
            engine.on_replay_tick(first, persist=True)
            account = engine.accounts["maker_v01_priority"]
            order = engine._new_order(
                account, first, side="buy", kind="low_bid_reversion",
                lot_id=None, price=136.522, quantity=1_000,
                queue_ahead=0.0, target_price=None, persist=True,
            )
            account.buy_order = order
            engine._fill_buy(
                account, first, order, 1_000,
                first.market_ts_ms * 1_000_000,
                kind="low_bid_reversion", target_price=None, persist=True,
            )

            compressed = self._replay_tick(
                start + timedelta(seconds=81),
                last=136.521, bid=136.351, ask=136.699,
                ask_bonds=6_000, previous_close=136.867,
            )
            engine.on_replay_tick(compressed, persist=True)
            sell_order = next(iter(account.sell_orders.values()))
            self.assertEqual(sell_order.limit_price, 136.698)

            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=93),
                last=136.699, bid=136.351, ask=136.700,
                trade_bonds=1_000, inferred_side="buy",
                ask_bonds=6_000, previous_close=136.867,
            ), persist=True)
            self.assertEqual(account.inventory, 1_000)
            exit_fill = store.connection.execute(
                """SELECT price FROM maker_paper_fills
                   WHERE side='sell' ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            self.assertEqual(float(exit_fill["price"]), 136.698)
            store.close()

    def test_extra_inventory_can_exit_above_fair_despite_historical_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-fair-exit.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 14, 13, 7, 42, tzinfo=SHANGHAI)
            engine._start_date(start.date().isoformat())
            account = engine.accounts["maker_v01_priority"]
            entry_tick = self._replay_tick(
                start, last=136.381, bid=136.300, ask=136.400,
            )
            order = engine._new_order(
                account, entry_tick, side="buy", kind="low_bid_reversion",
                lot_id=None, price=136.381, quantity=1_000,
                queue_ahead=0.0, target_price=None, persist=True,
            )
            account.buy_order = order
            engine._fill_buy(
                account, entry_tick, order, 1_000,
                entry_tick.market_ts_ms * 1_000_000,
                kind="low_bid_reversion", target_price=None, persist=True,
            )
            for offset in (-9, -6, -3):
                engine.analyzer.on_tick(self._replay_tick(
                    start + timedelta(seconds=offset),
                    last=135.300, bid=135.000, ask=136.000,
                    trade_bonds=1_000, inferred_side="sell",
                ))
            engine.observed_market_trade = True

            current = self._replay_tick(
                start + timedelta(seconds=9),
                last=135.006, bid=135.006, ask=135.618,
                previous_close=136.922, ask_bonds=760,
            )
            engine.on_replay_tick(current, persist=True)

            context = engine._decision_context(current)
            self.assertEqual(context.reference_source, "current_midpoint")
            self.assertAlmostEqual(context.reference_price, 135.312)
            sell_order = next(iter(account.sell_orders.values()))
            self.assertEqual(sell_order.limit_price, 135.617)
            self.assertLess(sell_order.limit_price, 136.381)
            store.close()

    def test_fragile_last_bid_triggers_near_flat_active_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-fragile-bid.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            moment = datetime(2026, 8, 14, 13, 9, 39, tzinfo=SHANGHAI)
            seed = self._replay_tick(
                moment - timedelta(seconds=3),
                last=135.920, bid=135.921, ask=136.497,
            )
            engine.on_replay_tick(seed, persist=True)
            account = engine.accounts["maker_v01_priority"]
            order = engine._new_order(
                account, seed, side="buy", kind="low_bid_reversion",
                lot_id=None, price=135.922, quantity=1_000,
                queue_ahead=0.0, target_price=None, persist=True,
            )
            account.buy_order = order
            engine._fill_buy(
                account, seed, order, 1_000,
                seed.market_ts_ms * 1_000_000,
                kind="low_bid_reversion", target_price=None, persist=True,
            )
            fragile = replace(
                self._replay_tick(
                    moment, last=135.920,
                    bid=135.920, ask=136.497,
                ),
                bids=(
                    (135.920, 1_000),
                    (135.201, 4_000),
                    (135.050, 74_000),
                    (135.006, 2_000),
                    (135.000, 62_000),
                ),
            )
            engine.on_replay_tick(fragile, persist=True)

            self.assertEqual(account.inventory, 1_000)
            fill = store.connection.execute(
                """SELECT price,quantity,fill_reason FROM maker_paper_fills
                   WHERE fill_reason='active_downside_risk_exit'"""
            ).fetchone()
            self.assertEqual(float(fill["price"]), 135.920)
            self.assertEqual(float(fill["quantity"]), 1_000)
            store.close()

    def test_unconfirmed_fast_rise_does_not_buy_the_newly_lifted_bid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-unconfirmed-rise.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            moment = datetime(2026, 8, 14, 13, 8, 30, tzinfo=SHANGHAI)
            engine._start_date(moment.date().isoformat())
            engine.previous_close_reference = 136.000
            account = engine.accounts["maker_v01_priority"]
            account.inventory = 0.0
            account.replenishment_quantity = 1_000
            account.replenishment_sale_value = 136_334
            tick = self._replay_tick(
                moment, last=135.701, bid=135.700, ask=136.334,
                bid_bonds=6_000, previous_close=136.000,
            )
            assessment = MarketAssessment(
                reference_price=136.017,
                reference_low=135.700,
                reference_high=136.334,
                reference_source="current_midpoint",
                reference_confidence=0.35,
                state="possible_rise",
                state_score=2,
                state_confidence=0.62,
                recent_buy_bonds=1_000,
                recent_sell_bonds=0,
                midpoint_change=0.343,
                short_ask_change=0.0,
                largest_ask_gap=0.0,
                downside_book_vacuum=False,
                fragile_top_bid=False,
                iron_floor_price=None,
                iron_floor_bonds=0.0,
                evidence=("买卖中点快速上移但突破尚未确认",),
            )

            engine._refresh_orders(
                account, tick, assessment, persist=True,
            )
            self.assertIsNone(account.buy_order)

            # Once the upward state is genuinely confirmed, the guard no
            # longer blocks the same quote; the normal fair-price and depth
            # checks decide whether inventory should be restored.
            confirmed = replace(
                assessment, state="rising", state_score=3,
            )
            engine._refresh_orders(
                account, tick, confirmed, persist=True,
            )
            self.assertIsNotNone(account.buy_order)
            self.assertEqual(account.buy_order.limit_price, 135.701)
            store.close()

    def test_tight_market_takes_a_profitable_bid_for_fast_turnover(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-tight-turnover.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            moment = datetime(2026, 8, 14, 14, 5, 33, tzinfo=SHANGHAI)
            seed = self._replay_tick(
                moment, last=136.002, bid=136.001, ask=136.880,
                previous_close=136.400,
            )
            engine.on_replay_tick(seed, persist=True)
            account = engine.accounts["maker_v01_priority"]
            order = engine._new_order(
                account, seed, side="buy", kind="low_bid_reversion",
                lot_id=None, price=136.002, quantity=1_000,
                queue_ahead=0.0, target_price=None, persist=True,
            )
            account.buy_order = order
            engine._fill_buy(
                account, seed, order, 1_000,
                seed.market_ts_ms * 1_000_000,
                kind="low_bid_reversion", target_price=None, persist=True,
            )

            tight = replace(
                self._replay_tick(
                    moment + timedelta(minutes=2),
                    last=136.007, bid=136.291, ask=136.299,
                    bid_bonds=1_000, previous_close=136.400,
                ),
                bids=(
                    (136.291, 1_000),
                    (136.290, 1_000),
                    (136.031, 2_000),
                ),
            )
            engine.on_replay_tick(tight, persist=True)

            self.assertEqual(account.inventory, 1_000)
            fill = store.connection.execute(
                """SELECT price,quantity,fill_reason FROM maker_paper_fills
                   WHERE fill_reason='active_tight_spread_turnover'"""
            ).fetchone()
            self.assertEqual(float(fill["price"]), 136.291)
            self.assertEqual(float(fill["quantity"]), 1_000)
            store.close()

    def test_queue_v10_does_not_inherit_priority_v11_fast_turnover(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-queue-v10.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("queue",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            moment = datetime(2026, 8, 14, 14, 5, 33, tzinfo=SHANGHAI)
            seed = self._replay_tick(
                moment, last=136.002, bid=136.001, ask=136.880,
                previous_close=136.400,
            )
            engine.on_replay_tick(seed, persist=True)
            account = engine.accounts["maker_v01_queue"]
            order = engine._new_order(
                account, seed, side="buy", kind="low_bid_reversion",
                lot_id=None, price=136.002, quantity=1_000,
                queue_ahead=0.0, target_price=None, persist=True,
            )
            account.buy_order = order
            engine._fill_buy(
                account, seed, order, 1_000,
                seed.market_ts_ms * 1_000_000,
                kind="low_bid_reversion", target_price=None, persist=True,
            )

            tight = self._replay_tick(
                moment + timedelta(minutes=2),
                last=136.007, bid=136.291, ask=136.299,
                bid_bonds=1_000, previous_close=136.400,
            )
            engine.on_replay_tick(tight, persist=True)

            self.assertEqual(account.inventory, 2_000)
            self.assertEqual(account.policy.model_id, "maker_queue_v1_0")
            self.assertEqual(
                store.connection.execute(
                    "SELECT COUNT(*) FROM maker_paper_fills "
                    "WHERE fill_reason='active_tight_spread_turnover'"
                ).fetchone()[0],
                0,
            )
            store.close()

    def test_super_windfall_uses_an_independent_one_hand_credit_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-windfall.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                fill_modes=("priority",),
                super_windfall_enabled=True,
                super_windfall_quantity_bonds=10,
                super_windfall_credit_cny=2_000,
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 14, 13, 5, 33, tzinfo=SHANGHAI)
            anomaly = replace(
                self._replay_tick(
                    start, last=136.200, bid=136.094, ask=136.317,
                    trade_bonds=1_000, inferred_side="buy",
                    previous_close=136.867,
                ),
                bids=(
                    (136.094, 2_000), (136.092, 1_000),
                    (136.089, 1_000), (136.052, 1_000),
                    (134.061, 8_000),
                ),
                asks=((136.317, 1_000), (136.318, 1_000)),
            )
            engine.on_replay_tick(anomaly, persist=True)

            windfall = engine.accounts["maker_v01_super_windfall"]
            standard = engine.accounts["maker_v01_priority"]
            self.assertEqual(windfall.initial_inventory, 0)
            self.assertEqual(windfall.maximum_inventory, 10)
            self.assertEqual(windfall.cash, 2_000)
            self.assertIsNotNone(windfall.buy_order)
            self.assertEqual(windfall.buy_order.limit_price, 134.062)
            self.assertEqual(windfall.buy_order.quantity, 10)
            self.assertNotEqual(windfall.strategy_id, standard.strategy_id)

            swept = replace(
                anomaly,
                tick_id=anomaly.tick_id + 1,
                market_ts_ms=anomaly.market_ts_ms + 3_000,
                market_time=(start + timedelta(seconds=3)).time().isoformat(
                    timespec="milliseconds"
                ),
                last_price=134.060,
                trade_bonds=10,
                inferred_side="sell",
            )
            engine.on_replay_tick(swept, persist=True)
            self.assertEqual(windfall.inventory, 10)
            self.assertAlmostEqual(windfall.cash, 659.38)
            fill = store.connection.execute(
                """SELECT quantity,price,fill_reason FROM maker_paper_fills
                   WHERE strategy_id='maker_v01_super_windfall'"""
            ).fetchone()
            self.assertEqual(float(fill["quantity"]), 10)
            self.assertEqual(float(fill["price"]), 134.062)
            self.assertEqual(fill["fill_reason"], "super_windfall_buy")
            store.close()

    def test_disabled_live_paper_does_not_create_maker_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-off.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(enabled=True))
            store = SQLiteStore(config)
            processor = MarketProcessor(config, store, enable_paper=False)
            processor.process(make_tick(
                config.qmt.bond_code,
                datetime(2026, 8, 13, 10, 0, tzinfo=SHANGHAI),
                last=137.0, bid=136.9, ask=137.0,
            ))
            self.assertEqual(
                store.connection.execute(
                    "SELECT COUNT(*) FROM maker_paper_accounts"
                ).fetchone()[0],
                0,
            )
            store.close()


if __name__ == "__main__":
    unittest.main()
