from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from manual_takeover import (  # noqa: E402
    CommandValidationError,
    DryRunExecutionAdapter,
    ManualTakeoverService,
    MarketQuote,
    SqliteQuoteProvider,
)


class FakeQuoteProvider:
    def __init__(self, now_seconds: float) -> None:
        self.calls = 0
        self.quote = MarketQuote.create(
            bond_code="132026.SH",
            market_date="2026-08-25",
            market_time="10:00:00.000",
            market_ts_ms=int(now_seconds * 1000),
            bid_price=100,
            ask_price=102,
        )

    def latest(self, bond_code: str):
        self.calls += 1
        return self.quote if bond_code == self.quote.bond_code else None


class ServiceTests(unittest.TestCase):
    def test_status_is_explicitly_dry_run_and_broker_disabled(self) -> None:
        provider = FakeQuoteProvider(100)
        service = ManualTakeoverService(
            provider, window_active=lambda: False, clock=lambda: 100
        )
        status = service.status()
        self.assertTrue(status["paper_only"])
        self.assertFalse(status["broker_orders_enabled"])
        self.assertEqual(status["execution_mode"], "dry_run")
        self.assertEqual(provider.calls, 0, "窗口外不得轮询行情SQLite")

    def test_start_requires_fresh_quote_inside_window(self) -> None:
        provider = FakeQuoteProvider(80)
        service = ManualTakeoverService(
            provider, window_active=lambda: True, clock=lambda: 100,
            maximum_quote_age_seconds=10,
        )
        with self.assertRaisesRegex(CommandValidationError, "超过10秒"):
            service.start({
                "bond_code": "132026.SH", "side": "buy",
                "quantity_bonds": 1000, "start_price": 100, "extreme_price": 101,
            })


class SqliteQuoteProviderTests(unittest.TestCase):
    def test_reads_latest_quote_without_mutating_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "quotes.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """CREATE TABLE raw_ticks (
                           id INTEGER PRIMARY KEY,code TEXT,market_date TEXT,
                           market_time TEXT,market_ts_ms INTEGER,
                           bid_price_1 REAL,ask_price_1 REAL)"""
                )
                connection.executemany(
                    "INSERT INTO raw_ticks VALUES (?,?,?,?,?,?,?)",
                    [
                        (1, "132026.SH", "2026-08-25", "09:30:00.000", 1000, 100, 102),
                        (2, "132026.SH", "2026-08-25", "09:30:03.000", 2000, 100.5, 101.5),
                    ],
                )
                connection.commit()
            finally:
                connection.close()
            before = database.read_bytes()
            result = SqliteQuoteProvider(database).latest("132026.SH")
            after = database.read_bytes()
        assert result is not None
        self.assertEqual(result.market_ts_ms, 2000)
        self.assertEqual(float(result.bid_price), 100.5)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
