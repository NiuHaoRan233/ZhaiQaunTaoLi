from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from zhaiquant.database import SQLiteStore
from zhaiquant.paper import PaperEngine
from zhaiquant.runner import MarketProcessor
from zhaiquant.types import SHANGHAI

from .helpers import make_tick, test_config


class PaperTests(unittest.TestCase):
    def test_bond_book_hands_are_converted_to_bonds(self) -> None:
        moment = datetime(2026, 8, 11, 10, 0, tzinfo=SHANGHAI)
        tick = make_tick(
            "132026.SH", moment, last=133.0, bid=132.99, ask=133.0,
            level_volume=5,
        )

        self.assertEqual(PaperEngine._queue_at_price(tick, "buy", 132.99), 50.0)
        self.assertAlmostEqual(
            PaperEngine._book_vwap(tick, "buy", 100.0),
            (133.0 + 133.01) / 2,
        )
        self.assertIsNone(PaperEngine._book_vwap(tick, "buy", 260.0))

    def test_standing_order_reprice_is_throttled(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(
                Path(temp) / "paper-reprice.sqlite3",
                models=("E3",), fill_modes=("queue",),
            )
            store = SQLiteStore(config)
            processor = MarketProcessor(config, store)
            base = datetime(2026, 8, 11, 10, 0, tzinfo=SHANGHAI)

            for seconds, bid in ((0, 130.00), (3, 130.00), (6, 130.00)):
                moment = base + timedelta(seconds=seconds)
                processor.process(make_tick(
                    config.qmt.stock_code, moment,
                    last=28.0, bid=28.0, ask=28.01, volume=1000,
                ))
                processor.process(make_tick(
                    config.qmt.bond_code, moment,
                    last=132.0, bid=bid, ask=134.0, volume=1000,
                ))

            first = store.connection.execute(
                "SELECT id,limit_price,status FROM paper_orders ORDER BY id"
            ).fetchall()
            self.assertEqual(len(first), 1)
            self.assertEqual(first[0]["status"], "open")

            moment = base + timedelta(seconds=9)
            processor.process(make_tick(
                config.qmt.stock_code, moment,
                last=28.0, bid=28.0, ask=28.01, volume=1000,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, moment,
                last=132.0, bid=130.02, ask=134.0, volume=1000,
            ))
            self.assertEqual(
                store.connection.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0], 1
            )

            moment = base + timedelta(seconds=18)
            processor.process(make_tick(
                config.qmt.stock_code, moment,
                last=28.0, bid=28.0, ask=28.01, volume=1000,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, moment,
                last=132.0, bid=130.08, ask=134.0, volume=1000,
            ))
            orders = store.connection.execute(
                "SELECT status,cancel_reason,limit_price FROM paper_orders ORDER BY id"
            ).fetchall()
            self.assertEqual(len(orders), 2)
            self.assertEqual(
                (orders[0]["status"], orders[0]["cancel_reason"]),
                ("cancelled", "standing_reprice"),
            )
            self.assertEqual(orders[1]["status"], "open")
            self.assertGreater(orders[1]["limit_price"], orders[0]["limit_price"])
            store.close()

    def test_invalid_book_cannot_fill_passive_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(
                Path(temp) / "paper-invalid.sqlite3",
                models=("E2",), fill_modes=("optimistic",),
            )
            store = SQLiteStore(config)
            processor = MarketProcessor(config, store)
            base = datetime(2026, 8, 11, 10, 0, tzinfo=SHANGHAI)
            for index, bond_price in enumerate((135.0, 135.0, 133.0)):
                moment = base + timedelta(seconds=index * 3)
                processor.process(make_tick(
                    config.qmt.stock_code, moment,
                    last=28.0, bid=28.0, ask=28.01, volume=1000 + index,
                ))
                processor.process(make_tick(
                    config.qmt.bond_code, moment,
                    last=bond_price, bid=bond_price - 0.02, ask=bond_price,
                    volume=1000 + index,
                ))
            invalid_time = base + timedelta(seconds=9)
            processor.process(make_tick(
                config.qmt.stock_code, invalid_time,
                last=28.0, bid=28.0, ask=28.01, volume=1004,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, invalid_time,
                last=0.0, bid=0.0, ask=0.0, volume=1004,
            ))
            order = store.connection.execute(
                "SELECT status FROM paper_orders ORDER BY id DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(order["status"], "open")
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0], 0)
            store.close()

    def test_e1_enters_on_discount_and_exits_on_convergence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "paper.sqlite3", models=("E1",))
            store = SQLiteStore(config)
            processor = MarketProcessor(config, store)
            base = datetime(2026, 8, 11, 10, 0, tzinfo=SHANGHAI)
            prices = (135.0, 135.0, 133.0, 135.1)
            for index, bond_price in enumerate(prices):
                moment = base + timedelta(seconds=index * 3)
                processor.process(make_tick(
                    config.qmt.stock_code, moment,
                    last=28.0, bid=28.0, ask=28.01,
                    volume=1000 + index,
                ))
                processor.process(make_tick(
                    config.qmt.bond_code, moment,
                    last=bond_price, bid=bond_price - 0.02, ask=bond_price,
                    volume=1000 + index,
                ))
            store.flush(force=True)
            position = store.connection.execute(
                "SELECT status,entry_price,exit_price,gross_return FROM paper_positions"
            ).fetchone()
            self.assertEqual(position["status"], "closed")
            self.assertEqual(position["entry_price"], 133.0)
            self.assertGreater(position["exit_price"], position["entry_price"])
            self.assertGreater(position["gross_return"], 0)
            self.assertEqual(
                store.connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0], 2
            )
            store.close()

    def test_restart_cancels_orders_and_recovers_position(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "paper-recovery.sqlite3")
            store = SQLiteStore(config)
            moment = datetime(2026, 8, 11, 10, 0, tzinfo=SHANGHAI)
            market_ms = int(moment.timestamp() * 1000)
            store.create_order({
                "run_id": store.run_id, "strategy_id": "E1_direct",
                "execution_model": "E1", "fill_mode": "direct", "signal_id": None,
                "side": "buy", "order_type": "limit", "status": "open",
                "created_market_ts_ms": market_ms, "updated_market_ts_ms": market_ms,
                "expires_market_ts_ms": None, "limit_price": 134.0, "quantity": 10.0,
                "filled_quantity": 0.0, "average_fill_price": None, "queue_ahead": 100.0,
                "cancel_reason": None, "metadata_json": "{}",
            })
            position_id = store.create_position({
                "run_id": store.run_id, "strategy_id": "E1_direct", "status": "open",
                "quantity": 10.0, "entry_market_ts_ms": market_ms, "entry_price": 134.0,
                "entry_stock_price": 28.0, "entry_signal_id": None,
                "exit_market_ts_ms": None, "exit_price": None, "exit_stock_price": None,
                "exit_reason": None, "gross_return": None, "max_favorable_return": 0.01,
                "max_adverse_return": -0.005, "updated_market_ts_ms": market_ms,
            })
            engine = PaperEngine(config, store, recover=True)
            self.assertEqual(engine.accounts["E1_direct"].position.db_id, position_id)
            order = store.connection.execute(
                "SELECT status,cancel_reason FROM paper_orders"
            ).fetchone()
            self.assertEqual((order["status"], order["cancel_reason"]), ("cancelled", "restart_recovery"))
            store.close()


if __name__ == "__main__":
    unittest.main()
