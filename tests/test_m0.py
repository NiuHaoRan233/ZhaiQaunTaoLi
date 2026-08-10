from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from zhaiquant.database import SQLiteStore
from zhaiquant.m0 import M0Engine
from zhaiquant.recorder import TickRecorder
from zhaiquant.runner import MarketProcessor
from zhaiquant.types import SHANGHAI

from .helpers import make_tick, test_config


class M0Tests(unittest.TestCase):
    def test_reference_is_shifted_and_entry_is_one_shot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "m0.sqlite3")
            store = SQLiteStore(config)
            recorder = TickRecorder(store)
            engine = M0Engine(config, store)
            base = datetime(2026, 8, 11, 10, 0, tzinfo=SHANGHAI)
            observations = []
            for index, bond_price in enumerate((135.0, 135.0, 133.0, 132.9)):
                moment = base + timedelta(seconds=index * 3)
                stock = recorder.record(make_tick(
                    config.qmt.stock_code, moment,
                    last=28.0, bid=28.0, ask=28.01,
                    volume=1000 + index,
                ))
                engine.on_tick(stock)
                bond = recorder.record(make_tick(
                    config.qmt.bond_code, moment,
                    last=bond_price, bid=bond_price - 0.02, ask=bond_price,
                    volume=1000 + index,
                ))
                observations.append(engine.on_tick(bond))
            self.assertIsNone(observations[0].reference_premium)
            self.assertIsNone(observations[1].reference_premium)
            self.assertIsNotNone(observations[2].reference_premium)
            self.assertTrue(observations[2].entry_signal)
            self.assertFalse(observations[3].entry_signal)
            self.assertGreater(observations[2].buy_discount, config.m0.entry_discount)
            store.close()

    def test_historical_replay_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "m0-replay.sqlite3")
            store = SQLiteStore(config)
            start = datetime(2026, 8, 11, 10, 0, tzinfo=SHANGHAI)
            ticks = []
            for index in range(4):
                moment = start + timedelta(seconds=index)
                ticks.extend([
                    make_tick("600900.SH", moment, last=28.0, bid=27.99, ask=28.01),
                    make_tick("132026.SH", moment, last=133.0, bid=132.99, ask=133.0),
                ])
            for _ in range(2):
                processor = MarketProcessor(
                    config, store, enable_paper=False, deduplicate_ticks=True,
                    preload_m0_history=False, synchronize_m0=True,
                )
                for tick in ticks:
                    processor.process(tick)
            store.flush(force=True)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM raw_ticks").fetchone()[0], 8)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM tick_changes").fetchone()[0], 8)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM m0_observations").fetchone()[0], 4)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM strategy_signals").fetchone()[0], 0)
            store.close()


if __name__ == "__main__":
    unittest.main()
