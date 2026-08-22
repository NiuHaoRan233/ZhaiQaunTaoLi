from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime
from pathlib import Path

import pandas as pd


MODULE_PATH = Path(__file__).with_name("research_railway_maker.py")
SPEC = importlib.util.spec_from_file_location("research_railway_maker", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def replay_tick(
    second: int,
    *,
    last: float = 100.0,
    bid: float = 100.0,
    ask: float = 100.05,
    trade_bonds: float = 0.0,
    side: str = "none",
) -> object:
    stamp = datetime(2026, 8, 21, 10, 0, second, tzinfo=MODULE.SHANGHAI)
    return MODULE.ReplayTick(
        tick_id=second + 1,
        code="184999.SH",
        market_ts_ms=int(stamp.timestamp() * 1_000),
        market_date="2026-08-21",
        market_time=stamp.time().isoformat(timespec="milliseconds"),
        last_price=last,
        bids=((bid, 1_000.0),),
        asks=((ask, 1_000.0),),
        trade_bonds=trade_bonds,
        transaction_delta=1 if trade_bonds else 0,
        inferred_side=side,
        side_confidence="test",
        previous_close=100.0,
    )


class RailwayResearchTests(unittest.TestCase):
    def test_qmt_frame_conversion_uses_bonds_and_causal_side(self) -> None:
        base_ms = int(datetime(
            2026, 8, 21, 10, 0, tzinfo=MODULE.SHANGHAI,
        ).timestamp() * 1_000)
        common = {
            "open": 100.0,
            "high": 100.1,
            "low": 99.9,
            "lastClose": 100.0,
            "pvolume": 0,
            "tickvol": 0,
            "stockStatus": 0,
            "openInt": 0,
            "lastSettlementPrice": 0,
            "settlementPrice": 0,
            "pe": 0,
            "askPrice": [100.05, 0, 0, 0, 0],
            "bidPrice": [100.00, 0, 0, 0, 0],
            "askVol": [100, 0, 0, 0, 0],
            "bidVol": [100, 0, 0, 0, 0],
        }
        frame = pd.DataFrame([
            {**common, "time": base_ms, "lastPrice": 100.0, "amount": 0, "volume": 0, "transactionNum": 0},
            {**common, "time": base_ms + 3_000, "lastPrice": 100.05, "amount": 100_050, "volume": 100, "transactionNum": 1},
        ])

        ticks = MODULE.qmt_frame_to_replay_ticks("184999.SH", frame, "2026-08-21")

        self.assertEqual(len(ticks), 2)
        self.assertEqual(ticks[1].trade_bonds, 1_000.0)
        self.assertEqual(ticks[1].inferred_side, "buy")
        self.assertEqual(ticks[1].bid1_bonds, 1_000.0)

    def test_corridor_pilot_uses_only_future_trades_after_signal(self) -> None:
        ticks = [
            replay_tick(0),
            replay_tick(10, last=100.05, trade_bonds=1_000.0, side="buy"),
            replay_tick(20),
            replay_tick(25, last=100.001, trade_bonds=1_000.0, side="sell"),
            replay_tick(30, last=100.049, trade_bonds=1_000.0, side="buy"),
        ]

        result = MODULE.run_stable_corridor_pilot(
            "184999.SH", "测试铁道", ticks, 0.02,
        )
        too_wide = MODULE.run_stable_corridor_pilot(
            "184999.SH", "测试铁道", ticks, 0.05,
        )

        self.assertEqual(result.entry_fills, 1)
        self.assertEqual(result.completed_turns, 1)
        self.assertAlmostEqual(result.realized_gross_cny, 48.0)
        self.assertEqual(too_wide.entry_fills, 0)


if __name__ == "__main__":
    unittest.main()
