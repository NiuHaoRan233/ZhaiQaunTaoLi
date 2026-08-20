from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from zhaiquant.maker import (
    MakerAnalyzer,
    MakerParameters,
    ReplayTick,
    _load_ticks,
    generate_maker_report,
)
from zhaiquant.database import SQLiteStore
from zhaiquant.recorder import TickRecorder
from zhaiquant.types import SHANGHAI, Tick

from .helpers import make_tick, test_config


def replay_tick(
    code: str,
    moment: datetime,
    *,
    last: float,
    bid: float,
    ask: float,
    bid_bonds: float = 1_000.0,
    ask_bonds: float = 1_000.0,
    trade_bonds: float = 0.0,
    side: str = "none",
    next_ask: float | None = None,
    next_ask_bonds: float = 1_000.0,
) -> ReplayTick:
    timestamp = int(moment.timestamp() * 1000)
    asks = [(ask, ask_bonds)]
    if next_ask is not None:
        asks.append((next_ask, next_ask_bonds))
    return ReplayTick(
        tick_id=timestamp,
        code=code,
        market_ts_ms=timestamp,
        market_date=moment.date().isoformat(),
        market_time=moment.time().isoformat(timespec="milliseconds"),
        last_price=last,
        bids=((bid, bid_bonds),),
        asks=tuple(asks),
        trade_bonds=trade_bonds,
        transaction_delta=1 if trade_bonds else 0,
        inferred_side=side,
        side_confidence="quote" if trade_bonds else "none",
    )


class MakerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parameters = MakerParameters(
            minimum_anchor_bonds=5_000,
            minimum_entry_edge=0.20,
            minimum_sweep_source_bonds=4_000,
            minimum_sweep_source_multiple=5.0,
            minimum_sweep_consumed_ratio=0.80,
            maximum_sweep_tail_bonds=2_000,
            minimum_sweep_jump=0.15,
        )
        self.analyzer = MakerAnalyzer("BOND", "STOCK", self.parameters)
        self.base = datetime(2026, 8, 12, 14, 0, tzinfo=SHANGHAI)

    def test_large_buy_and_continuing_buys_create_price_band(self) -> None:
        self.analyzer.on_tick(replay_tick(
            "BOND", self.base,
            last=136.800, bid=136.700, ask=136.800,
            trade_bonds=28_000, side="buy",
        ))
        self.analyzer.on_tick(replay_tick(
            "BOND", self.base + timedelta(seconds=30),
            last=136.998, bid=136.750, ask=136.998,
            trade_bonds=8_000, side="buy",
        ))

        anchor = self.analyzer.last_anchor
        self.assertIsNotNone(anchor)
        self.assertAlmostEqual(anchor.support_price, 136.800, places=3)
        self.assertAlmostEqual(anchor.exit_price, 136.998, places=3)
        self.assertGreater(anchor.reference_price, 136.800)
        self.assertLess(anchor.reference_price, 136.998)

    def test_small_low_sell_reduces_but_does_not_replace_anchor(self) -> None:
        self.analyzer.on_tick(replay_tick(
            "BOND", self.base,
            last=136.800, bid=136.700, ask=136.800,
            trade_bonds=28_000, side="buy",
        ))
        self.analyzer.on_tick(replay_tick(
            "BOND", self.base + timedelta(seconds=30),
            last=136.998, bid=136.750, ask=136.998,
            trade_bonds=8_000, side="buy",
        ))
        before = self.analyzer.last_anchor
        self.analyzer.on_tick(replay_tick(
            "BOND", self.base + timedelta(seconds=60),
            last=136.379, bid=136.379, ask=136.900,
            trade_bonds=380, side="sell",
        ))
        after = self.analyzer.last_anchor

        self.assertIsNotNone(before)
        self.assertIsNotNone(after)
        self.assertLess(after.reference_price, before.reference_price)
        self.assertGreater(after.reference_price, 136.800)
        self.assertAlmostEqual(after.support_price, 136.800, places=3)

    def test_low_bid_reversion_is_emitted_before_later_fill(self) -> None:
        self.analyzer.on_tick(replay_tick(
            "BOND", self.base,
            last=136.900, bid=136.850, ask=136.900,
            trade_bonds=10_000, side="buy",
        ))
        quote_time = self.base + timedelta(seconds=3)
        opportunities = self.analyzer.on_tick(replay_tick(
            "BOND", quote_time,
            last=136.900, bid=136.401, ask=136.998,
            bid_bonds=4_000,
        ))

        low_bid_opportunities = [
            item for item in opportunities if item.kind == "low_bid_reversion"
        ]
        self.assertEqual(len(low_bid_opportunities), 1)
        opportunity = low_bid_opportunities[0]
        self.assertEqual(opportunity.kind, "low_bid_reversion")
        self.assertEqual(opportunity.signal_ts_ms, int(quote_time.timestamp() * 1000))
        self.assertEqual(opportunity.entry_price, 136.401)
        self.assertEqual(opportunity.queue_ahead_bonds, 4_000)
        self.assertAlmostEqual(opportunity.improved_entry_price, 136.402)

    def test_sweep_tail_requires_verified_trade_consumption(self) -> None:
        initial = replay_tick(
            "BOND", self.base,
            last=136.700, bid=136.600, ask=136.800,
            ask_bonds=28_000, next_ask=136.999,
        )
        self.assertEqual(self.analyzer.on_tick(initial), [])

        # A pure displayed-size reduction is not enough; it may be cancellation.
        cancelled = replay_tick(
            "BOND", self.base + timedelta(seconds=3),
            last=136.700, bid=136.600, ask=136.800,
            ask_bonds=2_000, next_ask=136.999,
        )
        self.assertEqual(self.analyzer.on_tick(cancelled), [])

        # Rebuild the wall and then consume it with confirmed aggressive volume.
        analyzer = MakerAnalyzer("BOND", "STOCK", self.parameters)
        analyzer.on_tick(initial)
        opportunities = analyzer.on_tick(replay_tick(
            "BOND", self.base + timedelta(seconds=3),
            last=136.800, bid=136.600, ask=136.800,
            ask_bonds=2_000, next_ask=136.999,
            trade_bonds=26_000, side="buy",
        ))

        sweep_opportunities = [
            item for item in opportunities if item.kind == "sweep_tail"
        ]
        self.assertEqual(len(sweep_opportunities), 1)
        opportunity = sweep_opportunities[0]
        self.assertEqual(opportunity.kind, "sweep_tail")
        self.assertEqual(opportunity.source_wall_bonds, 28_000)
        self.assertEqual(opportunity.tail_bonds, 2_000)
        self.assertAlmostEqual(opportunity.consumed_ratio, 26_000 / 28_000)
        self.assertAlmostEqual(opportunity.priority_exit_price, 136.998)

    def test_immediate_visible_wall_consumption_uses_the_pretrade_snapshot(
        self,
    ) -> None:
        analyzer = MakerAnalyzer("BOND", "STOCK", self.parameters)
        # An older, larger cluster remains in historical wall memory.  It must
        # not dilute a later, independently verified one-frame sweep.
        analyzer.on_tick(replay_tick(
            "BOND", self.base,
            last=135.699, bid=135.451, ask=135.700,
            ask_bonds=20_020, next_ask=135.989,
        ))
        analyzer.on_tick(replay_tick(
            "BOND", self.base + timedelta(seconds=3),
            last=135.699, bid=135.451, ask=135.700,
            ask_bonds=9_140, next_ask=135.989,
        ))
        opportunities = analyzer.on_tick(replay_tick(
            "BOND", self.base + timedelta(seconds=6),
            last=135.700, bid=135.451, ask=135.700,
            ask_bonds=1_140, next_ask=135.989,
            trade_bonds=8_000, side="buy",
        ))

        sweep = [item for item in opportunities if item.kind == "sweep_tail"]
        self.assertEqual(len(sweep), 1)
        self.assertEqual(sweep[0].source_wall_bonds, 9_140)
        self.assertEqual(sweep[0].consumed_bonds, 8_000)
        self.assertAlmostEqual(sweep[0].consumed_ratio, 8_000 / 9_140)
        self.assertEqual(sweep[0].tail_bonds, 1_140)
        self.assertEqual(sweep[0].priority_exit_price, 135.988)
        self.assertIn(
            "immediate_visible_cluster_tail_consumption", sweep[0].notes,
        )

        cancellation_dominated = MakerAnalyzer(
            "BOND", "STOCK", self.parameters,
        )
        cancellation_dominated.on_tick(replay_tick(
            "BOND", self.base,
            last=135.699, bid=135.451, ask=135.700,
            ask_bonds=20_020, next_ask=135.989,
        ))
        cancellation_dominated.on_tick(replay_tick(
            "BOND", self.base + timedelta(seconds=3),
            last=135.699, bid=135.451, ask=135.700,
            ask_bonds=9_140, next_ask=135.989,
        ))
        rejected = cancellation_dominated.on_tick(replay_tick(
            "BOND", self.base + timedelta(seconds=6),
            last=135.700, bid=135.451, ask=135.700,
            ask_bonds=1_140, next_ask=135.989,
            trade_bonds=1_000, side="buy",
        ))
        self.assertFalse(any(item.kind == "sweep_tail" for item in rejected))

    def test_adjacent_offer_cluster_exhaustion_can_trigger_a_large_jump_sweep(
        self,
    ) -> None:
        analyzer = MakerAnalyzer("BOND", "STOCK", self.parameters)
        initial = replace(
            replay_tick(
                "BOND", self.base,
                last=135.800, bid=135.800, ask=135.999,
                ask_bonds=4_000, next_ask=136.000,
                trade_bonds=5_000, side="buy",
            ),
            asks=(
                (135.999, 4_000),
                (136.000, 1_000),
                (137.198, 12_000),
            ),
        )
        analyzer.on_tick(initial)

        # Some of the original display disappears without a trade, so it
        # cannot be counted as verified consumption.  The remaining nearby
        # cluster is then actually bought in two separate events.
        analyzer.on_tick(replace(
            initial,
            tick_id=initial.tick_id + 10,
            market_ts_ms=initial.market_ts_ms + 10_000,
            market_time=(self.base + timedelta(seconds=10)).time().isoformat(
                timespec="milliseconds"
            ),
            asks=(
                (135.999, 2_000),
                (136.000, 1_000),
                (137.198, 12_000),
            ),
        ))
        analyzer.on_tick(replace(
            initial,
            tick_id=initial.tick_id + 30,
            market_ts_ms=initial.market_ts_ms + 30_000,
            market_time=(self.base + timedelta(seconds=30)).time().isoformat(
                timespec="milliseconds"
            ),
            last_price=135.999,
            asks=(
                (135.999, 1_000),
                (136.000, 1_000),
                (137.198, 12_000),
            ),
            trade_bonds=1_000,
            inferred_side="buy",
        ))
        opportunities = analyzer.on_tick(replace(
            initial,
            tick_id=initial.tick_id + 80,
            market_ts_ms=initial.market_ts_ms + 80_000,
            market_time=(self.base + timedelta(seconds=80)).time().isoformat(
                timespec="milliseconds"
            ),
            last_price=135.999,
            bids=((135.851, 2_000),),
            asks=((136.000, 1_000), (137.196, 8_000)),
            trade_bonds=1_000,
            inferred_side="buy",
        ))

        sweep = [item for item in opportunities if item.kind == "sweep_tail"]
        self.assertEqual(len(sweep), 1)
        self.assertEqual(sweep[0].entry_price, 136.000)
        self.assertEqual(sweep[0].source_wall_bonds, 5_000)
        self.assertEqual(sweep[0].tail_bonds, 1_000)
        self.assertEqual(sweep[0].priority_exit_price, 137.195)
        self.assertIn(
            "adjacent_offer_cluster_exhaustion_with_large_jump",
            sweep[0].notes,
        )

    def test_sweep_tail_rejects_small_or_slow_wall_consumption(self) -> None:
        # A 4,000-bond offer is only four times our 1,000-bond order and is not
        # the institution-sized source wall required by the sweep pattern.
        small = MakerAnalyzer("BOND", "STOCK", self.parameters)
        small.on_tick(replay_tick(
            "BOND", self.base,
            last=136.900, bid=136.800, ask=137.000,
            ask_bonds=4_000, next_ask=137.289,
        ))
        opportunities = small.on_tick(replay_tick(
            "BOND", self.base + timedelta(seconds=3),
            last=137.000, bid=136.800, ask=137.000,
            ask_bonds=1_000, next_ask=137.289,
            trade_bonds=3_000, side="buy",
        ))
        self.assertFalse(any(item.kind == "sweep_tail" for item in opportunities))

    def test_sweep_tail_rejects_recent_material_lower_price_selling(self) -> None:
        analyzer = MakerAnalyzer("BOND", "STOCK", self.parameters)
        analyzer.on_tick(replay_tick(
            "BOND", self.base,
            last=136.612, bid=136.612, ask=136.800,
            ask_bonds=1_000, next_ask=137.000,
            trade_bonds=2_760, side="sell",
        ))
        analyzer.on_tick(replay_tick(
            "BOND", self.base + timedelta(seconds=30),
            last=136.700, bid=136.700, ask=137.000,
            ask_bonds=10_000, next_ask=137.289,
        ))
        opportunities = analyzer.on_tick(replay_tick(
            "BOND", self.base + timedelta(seconds=33),
            last=137.000, bid=136.800, ask=137.000,
            ask_bonds=1_000, next_ask=137.289,
            trade_bonds=9_000, side="buy",
        ))
        self.assertFalse(any(item.kind == "sweep_tail" for item in opportunities))

    def test_market_assessment_identifies_stable_state(self) -> None:
        analyzer = MakerAnalyzer("BOND", "STOCK", self.parameters)
        ticks = [
            replay_tick(
                "BOND", self.base, last=136.600, bid=136.500, ask=136.700,
                trade_bonds=1_000, side="buy",
            ),
            replay_tick(
                "BOND", self.base + timedelta(seconds=3),
                last=136.500, bid=136.500, ask=136.700,
                trade_bonds=1_000, side="sell",
            ),
        ]
        ticks.extend(
            replay_tick(
                "BOND", self.base + timedelta(seconds=seconds),
                last=136.600, bid=136.500, ask=136.700,
            )
            for seconds in range(6, 31, 3)
        )
        for tick in ticks:
            analyzer.on_tick(tick)

        assessment = analyzer.assess_market(ticks[-1], 136.600)

        self.assertEqual(assessment.state, "stable")
        self.assertEqual(assessment.state_score, 0)
        self.assertEqual(assessment.reference_source, "persistent_inside_market")

    def test_market_assessment_identifies_rising_state(self) -> None:
        analyzer = MakerAnalyzer("BOND", "STOCK", self.parameters)
        ticks = [
            replay_tick(
                "BOND", self.base, last=136.800, bid=136.500, ask=136.800,
                trade_bonds=1_000, side="buy", next_ask=136.900,
            ),
            replay_tick(
                "BOND", self.base + timedelta(seconds=30),
                last=136.900, bid=136.650, ask=136.900,
                trade_bonds=1_000, side="buy", next_ask=137.050,
            ),
            replay_tick(
                "BOND", self.base + timedelta(seconds=61),
                last=137.000, bid=136.800, ask=137.000,
                trade_bonds=1_000, side="buy", next_ask=137.300,
            ),
        ]
        for tick in ticks:
            analyzer.on_tick(tick)

        assessment = analyzer.assess_market(ticks[-1], 136.600)

        self.assertEqual(assessment.state, "rising")
        self.assertGreaterEqual(assessment.state_score, 3)
        self.assertGreaterEqual(assessment.largest_ask_gap, 0.30 - 1e-9)

    def test_market_assessment_identifies_falling_state(self) -> None:
        analyzer = MakerAnalyzer("BOND", "STOCK", self.parameters)
        ticks = [
            replay_tick(
                "BOND", self.base, last=137.000, bid=137.000, ask=137.200,
                trade_bonds=1_000, side="sell",
            ),
            replay_tick(
                "BOND", self.base + timedelta(seconds=30),
                last=136.800, bid=136.800, ask=137.000,
                trade_bonds=1_000, side="sell",
            ),
            replay_tick(
                "BOND", self.base + timedelta(seconds=61),
                last=136.500, bid=136.500, ask=136.800,
                trade_bonds=1_000, side="sell",
            ),
        ]
        for tick in ticks:
            analyzer.on_tick(tick)

        assessment = analyzer.assess_market(ticks[-1], 136.600)

        self.assertEqual(assessment.state, "falling")
        self.assertLessEqual(assessment.state_score, -3)

    def test_provisional_midpoint_replaces_stale_close_after_real_trading(self) -> None:
        analyzer = MakerAnalyzer("BOND", "STOCK", self.parameters)
        ticks = [
            replay_tick(
                "BOND", self.base + timedelta(seconds=index * 3),
                last=135.300 + index * 0.01,
                bid=135.006, ask=135.618,
                trade_bonds=1_000, side="sell",
            )
            for index in range(3)
        ]
        for tick in ticks:
            analyzer.on_tick(tick)

        assessment = analyzer.assess_market(ticks[-1], 136.922)

        self.assertEqual(assessment.reference_source, "current_midpoint")
        self.assertAlmostEqual(assessment.reference_price, 135.312)

    def test_assessment_marks_visible_135_iron_floor(self) -> None:
        analyzer = MakerAnalyzer("BOND", "STOCK", self.parameters)
        tick = replace(
            replay_tick(
                "BOND", self.base, last=135.920,
                bid=135.201, ask=136.495,
            ),
            bids=(
                (135.201, 4_000),
                (135.050, 74_000),
                (135.006, 2_000),
                (135.000, 62_000),
                (134.101, 4_000),
            ),
        )
        analyzer.on_tick(tick)

        assessment = analyzer.assess_market(tick, 136.922)

        self.assertAlmostEqual(assessment.iron_floor_price or 0.0, 135.050)
        self.assertEqual(assessment.iron_floor_bonds, 138_000)
        self.assertTrue(any("强承托区" in item for item in assessment.evidence))

    def test_slow_wall_consumption_is_not_a_sweep(self) -> None:
        # Even a visible 28,000-bond wall is not a sweep when its consumption
        # is spread beyond the short, 60-second momentum window.
        slow = MakerAnalyzer("BOND", "STOCK", self.parameters)
        slow.on_tick(replay_tick(
            "BOND", self.base,
            last=136.700, bid=136.600, ask=136.800,
            ask_bonds=28_000, next_ask=136.999,
        ))
        slow.on_tick(replay_tick(
            "BOND", self.base + timedelta(seconds=3),
            last=136.800, bid=136.600, ask=136.800,
            ask_bonds=15_000, next_ask=136.999,
            trade_bonds=13_000, side="buy",
        ))
        opportunities = slow.on_tick(replay_tick(
            "BOND", self.base + timedelta(seconds=66),
            last=136.800, bid=136.600, ask=136.800,
            ask_bonds=2_000, next_ask=136.999,
            trade_bonds=13_000, side="buy",
        ))
        self.assertFalse(any(item.kind == "sweep_tail" for item in opportunities))

    def test_report_fails_for_date_without_bond_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "empty.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript("""
                CREATE TABLE raw_ticks (market_date TEXT, code TEXT);
                CREATE TABLE tick_changes (tick_id INTEGER);
            """)
            connection.close()

            with self.assertRaisesRegex(RuntimeError, "No recorded ticks"):
                generate_maker_report(database, "2026-08-12", "BOND", "STOCK")

    def test_replay_prefers_live_tick_over_overlapping_backfill(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "ticks.sqlite3"
            config = test_config(database)
            moment = datetime(2026, 8, 14, 9, 40, 54, tzinfo=SHANGHAI)
            live_ticks = [
                make_tick(
                    "BOND", moment,
                    last=136.100, bid=136.000, ask=136.100,
                    volume=1_000, transactions=100,
                ),
                make_tick(
                    "BOND", moment + timedelta(seconds=3),
                    last=136.000, bid=136.000, ask=136.100,
                    volume=1_120, transactions=101,
                ),
            ]

            live_store = SQLiteStore(config, run_id="live-run")
            live_store.start_session()
            live_recorder = TickRecorder(live_store)
            live_records = [
                live_recorder.record(tick) for tick in live_ticks
            ]
            live_store.end_session("stopped")
            live_store.close()

            backfill_ticks = []
            for tick in live_ticks:
                payload = json.loads(tick.raw_json)
                payload["pvolume"] = payload["pvolume"] + 1
                backfill_ticks.append(Tick.from_qmt("BOND", payload))
            payload = json.loads(live_ticks[-1].raw_json)
            payload.update({
                "time": int((moment + timedelta(seconds=6)).timestamp() * 1000),
                "volume": 1_300,
                "pvolume": 1_301,
                "transactionNum": 102,
            })
            backfill_ticks.append(Tick.from_qmt("BOND", payload))

            backfill_store = SQLiteStore(config, run_id="backfill-run")
            backfill_store.start_session()
            backfill_recorder = TickRecorder(backfill_store)
            backfill_records = [
                backfill_recorder.record(tick) for tick in backfill_ticks
            ]
            backfill_store.end_session("backfill")
            backfill_store.close()

            connection = sqlite3.connect(database)
            connection.row_factory = sqlite3.Row
            ticks = _load_ticks(
                connection, "2026-08-14", "BOND", "STOCK",
                MakerParameters(),
            )
            connection.close()

            self.assertEqual(len(ticks), 3)
            self.assertEqual(
                [tick.tick_id for tick in ticks[:2]],
                [record.tick_id for record in live_records],
            )
            self.assertEqual(ticks[2].tick_id, backfill_records[2].tick_id)
            self.assertEqual(
                [tick.trade_bonds for tick in ticks],
                [0.0, 1_200.0, 1_800.0],
            )


if __name__ == "__main__":
    unittest.main()
