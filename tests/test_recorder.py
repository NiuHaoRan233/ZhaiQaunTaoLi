from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from zhaiquant.database import SQLiteStore
from zhaiquant.recorder import TickRecorder
from zhaiquant.types import SHANGHAI, Tick

from .helpers import make_tick, test_config


class RecorderTests(unittest.TestCase):
    def test_snapshot_hash_ignores_unstable_historical_tickvol(self) -> None:
        moment = datetime(2026, 8, 11, 10, 0, tzinfo=SHANGHAI)
        first = make_tick("132026.SH", moment, last=135.0, bid=134.9, ask=135.0)
        payload = json.loads(first.raw_json)
        payload["tickvol"] = 999
        second = Tick.from_qmt("132026.SH", payload)
        self.assertNotEqual(first.tick_volume, second.tick_volume)
        self.assertEqual(first.snapshot_hash, second.snapshot_hash)

    def test_records_raw_book_and_trade_delta(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "ticks.sqlite3")
            store = SQLiteStore(config)
            recorder = TickRecorder(store)
            moment = datetime(2026, 8, 11, 10, 0, tzinfo=SHANGHAI)
            first = make_tick("132026.SH", moment, last=135.0, bid=134.9, ask=135.0)
            second = make_tick(
                "132026.SH", moment + timedelta(seconds=3),
                last=134.9, bid=134.8, ask=134.9,
                volume=1120, amount=116_188, transactions=103,
            )
            recorder.record(first)
            recorded = recorder.record(second)
            store.flush(force=True)
            self.assertEqual(recorded.change.volume_delta, 120)
            self.assertEqual(recorded.change.transaction_delta, 3)
            self.assertEqual(recorded.change.inferred_side, "sell")
            row = store.connection.execute(
                "SELECT ask_price_1,bid_price_1,raw_json FROM raw_ticks WHERE id=?",
                (recorded.tick_id,),
            ).fetchone()
            self.assertEqual(row["ask_price_1"], 134.9)
            self.assertEqual(row["bid_price_1"], 134.8)
            self.assertIn("askPrice", row["raw_json"])
            store.close()

    def test_deduplicated_replay_rebuilds_changes_without_new_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "ticks.sqlite3")
            store = SQLiteStore(config)
            moment = datetime(2026, 8, 11, 10, 0, tzinfo=SHANGHAI)
            ticks = [
                make_tick(
                    "132026.SH", moment + timedelta(seconds=index),
                    last=135.0, bid=134.9, ask=135.0,
                    volume=1000 + index * 10,
                )
                for index in range(3)
            ]
            for _ in range(2):
                recorder = TickRecorder(store, deduplicate=True, rebuild_changes=True)
                for tick in ticks:
                    recorder.record(tick)
            store.flush(force=True)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM raw_ticks").fetchone()[0], 3)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM tick_changes").fetchone()[0], 3)
            last_delta = store.connection.execute(
                "SELECT volume_delta FROM tick_changes ORDER BY tick_id DESC LIMIT 1"
            ).fetchone()[0]
            self.assertEqual(last_delta, 10)
            store.close()


if __name__ == "__main__":
    unittest.main()
