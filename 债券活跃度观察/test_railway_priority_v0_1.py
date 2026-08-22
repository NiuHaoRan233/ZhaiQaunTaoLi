from __future__ import annotations

import importlib.util
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("railway_priority_v0_1.py")
SPEC = importlib.util.spec_from_file_location("railway_priority_v0_1", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def replay_tick(
    offset_seconds: int,
    *,
    last: float,
    bid: float,
    ask: float,
    trade_bonds: float = 0.0,
    side: str = "none",
) -> object:
    shanghai = MODULE.qmt_frame_to_replay_ticks.__globals__["SHANGHAI"]
    stamp = datetime(2026, 8, 21, 10, 0, tzinfo=shanghai)
    stamp = stamp + timedelta(seconds=offset_seconds)
    return MODULE.qmt_frame_to_replay_ticks.__globals__["ReplayTick"](
        tick_id=offset_seconds + 1,
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


def stable_evidence() -> list[object]:
    return [
        replay_tick(0, last=100.000, bid=99.999, ask=100.070, trade_bonds=1_000, side="sell"),
        replay_tick(60, last=100.070, bid=100.000, ask=100.071, trade_bonds=1_000, side="buy"),
        replay_tick(120, last=100.001, bid=100.000, ask=100.071, trade_bonds=1_000, side="sell"),
        replay_tick(180, last=100.069, bid=100.000, ask=100.071, trade_bonds=1_000, side="buy"),
        replay_tick(240, last=100.000, bid=100.000, ask=100.071, trade_bonds=1_000, side="sell"),
        replay_tick(300, last=100.070, bid=100.000, ask=100.071, trade_bonds=1_000, side="buy"),
    ]


class RailwayPriorityV01Tests(unittest.TestCase):
    def parameters(self) -> object:
        return replace(
            MODULE.RailwayPriorityParameters(),
            minimum_evidence_span_seconds=300,
        )

    def test_signal_uses_repeated_two_sided_clusters(self) -> None:
        ticks = stable_evidence()
        evidence = [
            MODULE.TradePoint(
                tick.market_ts_ms, tick.last_price, tick.trade_bonds, tick.inferred_side,
            )
            for tick in ticks
        ]

        signal = MODULE.infer_corridor(evidence, ticks[-1], self.parameters())

        self.assertIsNotNone(signal)
        assert signal is not None
        self.assertAlmostEqual(signal.entry_price, 100.001)
        self.assertAlmostEqual(signal.exit_price, 100.070)

    def test_order_cannot_fill_on_signal_creation_tick(self) -> None:
        ticks = stable_evidence() + [
            replay_tick(360, last=100.000, bid=100.000, ask=100.071, trade_bonds=1_000, side="sell"),
            replay_tick(420, last=100.000, bid=100.000, ask=100.071, trade_bonds=1_000, side="sell"),
            replay_tick(480, last=100.070, bid=100.069, ask=100.071, trade_bonds=1_000, side="buy"),
        ]

        result, fills = MODULE.run_candidate(
            "184999.SH", "测试铁路", ticks, parameters=self.parameters(),
        )

        self.assertEqual(result.entry_fills, 1)
        self.assertEqual(result.completed_turns, 1)
        self.assertEqual([fill.market_time for fill in fills], ["10:06:00.000", "10:08:00.000"])
        self.assertAlmostEqual(result.realized_gross_cny, 69.0)
        self.assertEqual(result.ending_inventory_bonds, 1_000.0)
        self.assertEqual(result.customer_base_short_bonds, 0.0)

    def test_one_sided_evidence_never_buys(self) -> None:
        ticks = [
            replay_tick(index * 60, last=100.0, bid=100.0, ask=100.071, trade_bonds=1_000, side="sell")
            for index in range(8)
        ]

        result, fills = MODULE.run_candidate(
            "184999.SH", "测试铁路", ticks, parameters=self.parameters(),
        )

        self.assertEqual(result.signals, 0)
        self.assertEqual(result.bought_bonds, 0.0)
        self.assertEqual(fills, [])

    def test_competition_stress_reduces_attributed_quantity(self) -> None:
        ticks = stable_evidence() + [
            replay_tick(420, last=100.000, bid=100.000, ask=100.071, trade_bonds=1_000, side="sell"),
            replay_tick(480, last=100.070, bid=100.069, ask=100.071, trade_bonds=1_000, side="buy"),
        ]
        stressed = replace(
            self.parameters(), competition_capture_rate=0.5,
        )

        result, _ = MODULE.run_candidate(
            "184999.SH", "测试铁路", ticks, parameters=stressed,
        )

        self.assertEqual(result.entry_fills, 1)
        self.assertEqual(result.completed_turns, 1)
        self.assertEqual(result.bought_bonds, 500.0)
        self.assertEqual(result.ending_extra_inventory_bonds, 0.0)

    def test_partial_entry_sells_only_actual_fill(self) -> None:
        ticks = stable_evidence() + [
            replay_tick(420, last=100.000, bid=100.000, ask=100.071, trade_bonds=300, side="sell"),
            replay_tick(480, last=100.070, bid=100.069, ask=100.071, trade_bonds=1_000, side="buy"),
        ]

        result, fills = MODULE.run_candidate(
            "184999.SH", "测试铁路", ticks, parameters=self.parameters(),
        )

        self.assertEqual([fill.quantity_bonds for fill in fills], [300, 300])
        self.assertEqual(result.completed_turns, 1)
        self.assertAlmostEqual(result.realized_gross_cny, 20.7)
        self.assertEqual(result.ending_extra_inventory_bonds, 0.0)


if __name__ == "__main__":
    unittest.main()
