from __future__ import annotations

import unittest
from datetime import datetime, timedelta

import pandas as pd

from zhaiquant.anchored_maker_research import (
    PRIORITY_MODEL_ID,
    QUEUE_MODEL_ID,
    MarketEvent,
    qmt_frame_to_events,
    run_day,
)


def event(
    second: int,
    *,
    aggressor: str = "none",
    last: float = 100.005,
    traded_units: float = 0.0,
    strict: bool = True,
    bid_units: float = 1_000.0,
    ask_units: float = 1_000.0,
    bid: float = 100.000,
    ask: float = 100.010,
) -> MarketEvent:
    stamp = datetime(2026, 8, 21, 10, 0) + timedelta(seconds=second)
    return MarketEvent(
        code="551999.SH",
        market_date="2026-08-21",
        market_ts_ms=int(stamp.timestamp() * 1_000),
        market_time=stamp.time().isoformat(timespec="seconds"),
        last_price=last,
        bid1=bid,
        ask1=ask,
        bid1_units=bid_units,
        ask1_units=ask_units,
        traded_units=traded_units,
        transaction_delta=1 if traded_units else 0,
        aggressor=aggressor,
        strict_trade=strict,
    )


class AnchoredMakerResearchTests(unittest.TestCase):
    def test_no_fills_have_zero_incremental_pnl_despite_price_move(self) -> None:
        rows = [
            event(0),
            event(15),
            event(30, bid=101.000, ask=101.010),
        ]

        result, fills = run_day(
            "551999.SH",
            "测试ETF",
            rows,
            mode="priority",
            minimum_spread_ticks=20,
            one_way_fee_bps=0.5,
            sample="test",
        )

        self.assertEqual(fills, [])
        self.assertEqual(result.marked_pnl_cny, 0.0)
        self.assertEqual(result.gross_marked_pnl_cny, 0.0)

    def test_qmt_etf_volume_is_converted_to_fund_units(self) -> None:
        base = int(datetime(2026, 8, 21, 10, 0).timestamp() * 1_000)
        common = {
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "lastClose": 1.0,
            "askPrice": [1.001, 0, 0, 0, 0],
            "bidPrice": [1.000, 0, 0, 0, 0],
            "askVol": [12, 0, 0, 0, 0],
            "bidVol": [34, 0, 0, 0, 0],
        }
        frame = pd.DataFrame([
            {**common, "time": base, "lastPrice": 1.0, "volume": 10, "transactionNum": 1},
            {**common, "time": base + 3_000, "lastPrice": 1.0, "volume": 15, "transactionNum": 2},
        ], index=["20260821100000", "20260821100003"])

        rows = qmt_frame_to_events("551999.SH", frame)

        self.assertEqual(rows[1].traded_units, 500.0)
        self.assertEqual(rows[1].bid1_units, 3_400.0)
        self.assertEqual(rows[1].ask1_units, 1_200.0)

    def test_priority_fill_requires_future_trade_after_latency(self) -> None:
        rows = [
            event(0),
            event(15),
            event(16, aggressor="sell", last=100.0, traded_units=1_000.0),
            event(18, aggressor="sell", last=100.0, traded_units=1_000.0),
        ]

        result, fills = run_day(
            "551999.SH",
            "测试ETF",
            rows,
            mode="priority",
            minimum_spread_ticks=6,
            one_way_fee_bps=0.0,
            sample="test",
        )

        self.assertEqual(result.model_id, PRIORITY_MODEL_ID)
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].market_time, "10:00:18")
        self.assertEqual(fills[0].side, "buy")
        self.assertAlmostEqual(fills[0].price, 100.001)

    def test_queue_fill_consumes_visible_ahead_before_model(self) -> None:
        rows = [
            event(0, bid_units=1_000.0),
            event(15, bid_units=1_000.0),
            event(18, aggressor="sell", last=100.0, traded_units=600.0),
            event(21, aggressor="sell", last=100.0, traded_units=600.0),
        ]

        result, fills = run_day(
            "551999.SH",
            "测试ETF",
            rows,
            mode="queue",
            minimum_spread_ticks=6,
            one_way_fee_bps=0.0,
            sample="test",
        )

        self.assertEqual(result.model_id, QUEUE_MODEL_ID)
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].quantity_units, 100)
        self.assertEqual(fills[0].market_time, "10:00:21")

    def test_round_trip_marks_fees_and_returns_to_neutral_inventory(self) -> None:
        rows = [
            event(0),
            event(15),
            event(18, aggressor="sell", last=100.0, traded_units=1_000.0),
            event(21, aggressor="buy", last=100.010, traded_units=1_000.0),
        ]

        result, fills = run_day(
            "551999.SH",
            "测试ETF",
            rows,
            mode="priority",
            minimum_spread_ticks=6,
            one_way_fee_bps=0.5,
            sample="test",
        )

        self.assertEqual([fill.side for fill in fills], ["buy", "sell"])
        self.assertEqual(result.completed_turns, 1)
        self.assertEqual(result.ending_inventory_deviation_units, 0)
        self.assertGreater(result.gross_marked_pnl_cny, result.marked_pnl_cny)
        self.assertAlmostEqual(
            result.gross_marked_pnl_cny - result.marked_pnl_cny,
            result.fees_cny,
        )


if __name__ == "__main__":
    unittest.main()
