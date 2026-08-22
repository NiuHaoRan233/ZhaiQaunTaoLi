from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from unittest.mock import patch

from zhaiquant.maker import ReplayTick
import zhaiquant.whale_maker_research as whale_research
from zhaiquant.whale_maker_research import (
    WHALE_V02_MODEL_ID,
    WhaleV02Exposure,
    WhaleV02Parameters,
    _v02_apply_fill_to_exposures,
    run_day_v02,
)


BASE = datetime(2026, 8, 21, 10, 0)


def tick(
    second: int,
    *,
    bids: tuple[tuple[float, float], ...],
    asks: tuple[tuple[float, float], ...],
    last: float = 100.250,
    trade_bonds: float = 0.0,
    inferred_side: str = "none",
    base: datetime = BASE,
) -> ReplayTick:
    moment = base + timedelta(seconds=second)
    return ReplayTick(
        tick_id=second + 1,
        code="132026.SH",
        market_ts_ms=int(moment.timestamp() * 1_000),
        market_date=moment.date().isoformat(),
        market_time=moment.time().isoformat(timespec="milliseconds"),
        last_price=last,
        bids=bids,
        asks=asks,
        trade_bonds=trade_bonds,
        transaction_delta=1 if trade_bonds else 0,
        inferred_side=inferred_side,
        side_confidence="high" if trade_bonds else "none",
        previous_close=100.250,
    )


class WhaleMakerV02AuditTests(unittest.TestCase):
    def test_opening_caution_requires_one_yuan_until_0930(self) -> None:
        early = datetime(2026, 8, 21, 9, 20)
        bids = ((100.000, 15_000.0), (99.800, 1_000.0))
        asks = ((100.500, 15_000.0), (100.700, 1_000.0))
        rows = [
            tick(0, bids=bids, asks=asks, base=early),
            tick(15, bids=bids, asks=asks, base=early),
            tick(30, bids=bids, asks=asks, base=early),
            tick(599, bids=bids, asks=asks, base=early),
            tick(600, bids=bids, asks=asks, base=early),
        ]

        _, _, quotes = run_day_v02(rows)

        places = [event for event in quotes if event.action == "place"]
        self.assertEqual(len(places), 1)
        self.assertEqual(places[0].market_time, "09:30:00.000")

    def test_backup_level_boundaries_are_symmetric(self) -> None:
        cases = (
            (
                "buy_exact_gap_and_quantity",
                ((100.000, 15_000.0), (99.800, 1_000.0)),
                ((101.000, 1_000.0),),
                "buy",
                True,
            ),
            (
                "buy_gap_one_mill_too_wide",
                ((100.000, 15_000.0), (99.799, 1_000.0)),
                ((101.000, 1_000.0),),
                "buy",
                False,
            ),
            (
                "buy_backup_one_bond_too_small",
                ((100.000, 15_000.0), (99.800, 999.0)),
                ((101.000, 1_000.0),),
                "buy",
                False,
            ),
            (
                "sell_exact_gap_and_quantity",
                ((100.000, 1_000.0),),
                ((101.000, 15_000.0), (101.200, 1_000.0)),
                "sell",
                True,
            ),
            (
                "sell_gap_one_mill_too_wide",
                ((100.000, 1_000.0),),
                ((101.000, 15_000.0), (101.201, 1_000.0)),
                "sell",
                False,
            ),
            (
                "sell_backup_one_bond_too_small",
                ((100.000, 1_000.0),),
                ((101.000, 15_000.0), (101.200, 999.0)),
                "sell",
                False,
            ),
        )
        for name, bids, asks, side, expected in cases:
            with self.subTest(name=name):
                rows = [
                    tick(second, bids=bids, asks=asks)
                    for second in (0, 15, 30)
                ]
                _, _, quotes = run_day_v02(rows)
                placed_sides = {
                    event.side for event in quotes if event.action == "place"
                }
                self.assertEqual(side in placed_sides, expected)

    def test_certified_stage_is_not_hard_capped_by_opposite_top_depth(
        self,
    ) -> None:
        bids = ((100.000, 15_000.0), (99.800, 1_000.0))
        # The only displayed opposite level is 1,000 bonds.  It establishes
        # a price corridor but must not cap the certified stage at 1,000.
        asks = ((101.000, 1_000.0),)
        rows = [
            tick(0, bids=bids, asks=asks),
            tick(15, bids=bids, asks=asks),
            tick(30, bids=bids, asks=asks),
            tick(
                33, bids=bids, asks=asks, last=100.001,
                trade_bonds=1_000.0, inferred_side="sell",
            ),
            tick(
                36, bids=bids, asks=asks, last=100.000,
                trade_bonds=1_000.0, inferred_side="sell",
            ),
            tick(39, bids=bids, asks=asks),
        ]

        result, _, quotes = run_day_v02(rows)

        buy_places = [
            event for event in quotes
            if event.action == "place" and event.side == "buy"
        ]
        self.assertEqual(
            [event.cumulative_target_bonds for event in buy_places],
            [1_000, 2_000],
        )
        self.assertEqual(buy_places[-1].quantity_bonds, 1_000)
        self.assertEqual(result.maximum_cumulative_risk_bonds, 2_000)

    def test_lunch_ends_episode_and_requires_fresh_30_second_observation(
        self,
    ) -> None:
        before_lunch = datetime(2026, 8, 21, 11, 29, 20)
        bids = ((100.000, 15_000.0), (99.800, 1_000.0))
        asks = ((101.000, 15_000.0), (101.200, 1_000.0))
        rows = [
            tick(0, bids=bids, asks=asks, base=before_lunch),
            tick(20, bids=bids, asks=asks, base=before_lunch),
            tick(40, bids=bids, asks=asks, base=before_lunch),
            tick(41, bids=bids, asks=asks, base=before_lunch),
            # 13:00:00, 13:00:15, 13:00:30.
            tick(5_440, bids=bids, asks=asks, base=before_lunch),
            tick(5_455, bids=bids, asks=asks, base=before_lunch),
            tick(5_470, bids=bids, asks=asks, base=before_lunch),
        ]

        _, _, quotes = run_day_v02(rows)

        places = [event for event in quotes if event.action == "place"]
        self.assertEqual(
            [event.market_time for event in places],
            ["11:30:00.000", "13:00:30.000"],
        )
        self.assertTrue(any(
            event.action == "cancel"
            and event.market_time == "11:30:01.000"
            and event.reason == "outside_session_or_invalid_book"
            for event in quotes
        ))
        self.assertNotEqual(
            places[0].wall_episode_id,
            places[1].wall_episode_id,
        )

    def test_cli_v02_dispatch_and_output_keep_registered_model_id(self) -> None:
        payload = {
            "model_id": WHALE_V02_MODEL_ID,
            "aggregate": {"instrument_days": 0},
        }
        output = io.StringIO()
        with (
            patch(
                "zhaiquant.whale_maker_research.replay_study_v02",
                return_value=payload,
            ) as replay,
            patch(
                "sys.argv",
                [
                    "whale_maker_research",
                    "--dates", "2026-08-21",
                    "--model-version", "v0.2",
                ],
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(whale_research.main(), 0)

        replay.assert_called_once()
        self.assertEqual(
            json.loads(output.getvalue())["model_id"],
            WHALE_V02_MODEL_ID,
        )

    def test_preexisting_probe_fills_before_wall_collapse_cancels_residual(
        self,
    ) -> None:
        bid_wall = ((100.000, 12_000.0), (99.800, 1_000.0))
        ask_wall = ((100.500, 12_000.0), (100.700, 1_000.0))
        rows = [
            tick(0, bids=bid_wall, asks=ask_wall),
            tick(15, bids=bid_wall, asks=ask_wall),
            tick(30, bids=bid_wall, asks=ask_wall),
            # The order existed before this frame.  The sell reached its
            # 100.001 limit and also removed the backing wall by frame end.
            tick(
                31,
                bids=((99.900, 100.0),),
                asks=ask_wall,
                last=100.001,
                trade_bonds=500.0,
                inferred_side="sell",
            ),
        ]

        result, fills, quotes = run_day_v02(rows)

        passive = [fill for fill in fills if fill.fill_kind == "passive"]
        self.assertEqual(result.model_id, WHALE_V02_MODEL_ID)
        self.assertEqual(len(passive), 1)
        self.assertEqual(passive[0].quantity_bonds, 500)
        self.assertEqual(result.ending_inventory_bonds, 10_500)
        self.assertEqual(result.stranded_exposure_bonds, 500)
        self.assertTrue(any(
            event.action == "cancel"
            and event.side == "buy"
            and "wall" in event.reason
            for event in quotes
        ))

    def test_tiny_trade_does_not_explain_a_large_wall_withdrawal(self) -> None:
        full = ((100.000, 25_000.0), (99.800, 1_000.0))
        reduced = ((100.000, 10_000.0), (99.800, 1_000.0))
        asks = ((100.500, 25_000.0), (100.700, 1_000.0))
        rows = [
            tick(0, bids=full, asks=asks),
            tick(15, bids=full, asks=asks),
            tick(30, bids=full, asks=asks),
            tick(
                31,
                bids=reduced,
                asks=asks,
                last=100.000,
                trade_bonds=10.0,
                inferred_side="sell",
            ),
            # The risk block was created at t31.  Active risk exit may use
            # only a strictly later observable book, never its creation frame.
            tick(32, bids=reduced, asks=asks),
        ]

        _, fills, _ = run_day_v02(rows)

        risk_exits = [
            fill for fill in fills
            if fill.fill_kind == "active_risk_exit"
        ]
        self.assertEqual(len(risk_exits), 1)
        self.assertEqual(
            risk_exits[0].reason,
            "unexplained_wall_shrink_at_least_20_percent",
        )

    def test_real_attack_immediate_exit_thresholds_are_symmetric(self) -> None:
        cases = (
            ("buy_absolute_5000", "buy", 30_000.0, 5_000.0, True),
            ("sell_absolute_5000", "sell", 30_000.0, 5_000.0, True),
            ("buy_ratio_20_percent", "buy", 20_000.0, 4_000.0, True),
            ("sell_ratio_20_percent", "sell", 20_000.0, 4_000.0, True),
            ("buy_below_both", "buy", 25_000.0, 4_990.0, False),
            ("sell_below_both", "sell", 25_000.0, 4_990.0, False),
        )
        for name, probe_side, initial_wall, damage, expected_exit in cases:
            with self.subTest(name=name):
                if probe_side == "buy":
                    full_bids = (
                        (100.000, initial_wall), (99.800, 1_000.0),
                    )
                    damaged_bids = (
                        (100.000, initial_wall - damage),
                        (99.800, 1_000.0),
                    )
                    asks = ((101.000, 1_000.0),)
                    rows = [
                        tick(second, bids=full_bids, asks=asks)
                        for second in (0, 15, 30)
                    ]
                    rows.extend((
                        tick(
                            31, bids=full_bids, asks=asks, last=100.001,
                            trade_bonds=1_000.0, inferred_side="sell",
                        ),
                        tick(
                            34, bids=damaged_bids, asks=asks,
                            last=100.000, trade_bonds=damage,
                            inferred_side="sell",
                        ),
                    ))
                else:
                    bids = ((100.000, 1_000.0),)
                    full_asks = (
                        (101.000, initial_wall), (101.200, 1_000.0),
                    )
                    damaged_asks = (
                        (101.000, initial_wall - damage),
                        (101.200, 1_000.0),
                    )
                    rows = [
                        tick(second, bids=bids, asks=full_asks)
                        for second in (0, 15, 30)
                    ]
                    rows.extend((
                        tick(
                            31, bids=bids, asks=full_asks, last=100.999,
                            trade_bonds=1_000.0, inferred_side="buy",
                        ),
                        tick(
                            34, bids=bids, asks=damaged_asks,
                            last=101.000, trade_bonds=damage,
                            inferred_side="buy",
                        ),
                    ))

                result, fills, _ = run_day_v02(rows)
                risk_exits = [
                    fill for fill in fills
                    if fill.fill_kind == "active_risk_exit"
                ]
                self.assertEqual(bool(risk_exits), expected_exit)
                if expected_exit:
                    self.assertEqual(len(risk_exits), 1)
                    self.assertEqual(risk_exits[0].market_time, "10:00:34.000")
                    self.assertEqual(
                        risk_exits[0].reason,
                        "compatible_attack_significant_wall_damage",
                    )
                    self.assertEqual(result.ending_inventory_deviation_bonds, 0)
                else:
                    self.assertEqual(result.ending_inventory_deviation_bonds, (
                        1_000 if probe_side == "buy" else -1_000
                    ))

    def test_same_price_wall_reappearance_starts_a_fresh_probe_episode(
        self,
    ) -> None:
        walls = (
            ((100.000, 25_000.0), (99.800, 1_000.0)),
            ((100.500, 25_000.0), (100.700, 1_000.0)),
        )
        rows = [
            tick(0, bids=walls[0], asks=walls[1]),
            tick(15, bids=walls[0], asks=walls[1]),
            tick(30, bids=walls[0], asks=walls[1]),
            tick(
                31, bids=walls[0], asks=walls[1], last=100.001,
                trade_bonds=1_000.0, inferred_side="sell",
            ),
            tick(
                32, bids=walls[0], asks=walls[1], last=100.000,
                trade_bonds=1_000.0, inferred_side="sell",
            ),
            tick(33, bids=walls[0], asks=walls[1]),
            # Old wall episode ends.  Current depth can close its probe, so
            # the later identity check is not confounded by stranded risk.
            tick(
                34,
                bids=((100.001, 1_000.0), (99.900, 1_000.0)),
                asks=((100.500, 1_000.0),),
            ),
            tick(40, bids=walls[0], asks=walls[1]),
            tick(55, bids=walls[0], asks=walls[1]),
            tick(70, bids=walls[0], asks=walls[1]),
        ]

        _, _, quotes = run_day_v02(rows)

        buy_places = [
            event for event in quotes
            if event.action == "place" and event.side == "buy"
        ]
        self.assertGreaterEqual(len(buy_places), 3)
        first = buy_places[0]
        fresh = buy_places[-1]
        self.assertNotEqual(fresh.wall_episode_id, first.wall_episode_id)
        self.assertEqual(fresh.certification_count, 0)
        self.assertEqual(fresh.cumulative_target_bonds, 1_000)
        self.assertEqual(fresh.quantity_bonds, 1_000)

    def test_targeted_risk_exit_does_not_consume_another_risk_block(
        self,
    ) -> None:
        exposures = [
            WhaleV02Exposure(
                risk_block_id=1, direction="long", quantity_bonds=1_000,
                entry_price=100.101, wall_episode_id=1,
                wall_price=100.100, opened_ts_ms=1,
            ),
            WhaleV02Exposure(
                risk_block_id=2, direction="long", quantity_bonds=1_000,
                entry_price=100.001, wall_episode_id=2,
                wall_price=100.000, opened_ts_ms=2,
            ),
        ]

        realized, opened, next_id, affected = (
            _v02_apply_fill_to_exposures(
                exposures,
                "sell",
                1_000,
                100.000,
                wall_episode_id=2,
                wall_price=100.000,
                market_ts_ms=3,
                next_risk_block_id=3,
                close_only_risk_block_ids={2},
                allow_open=False,
            )
        )

        self.assertAlmostEqual(realized, -1.0)
        self.assertEqual(opened, 0)
        self.assertEqual(next_id, 3)
        self.assertEqual(affected, (2,))
        self.assertEqual(
            [(block.risk_block_id, block.quantity_bonds) for block in exposures],
            [(1, 1_000)],
        )

    def test_partial_probe_exit_cannot_flip_through_neutral(self) -> None:
        parameters = WhaleV02Parameters(
            passive_trade_attribution_ratio=0.5,
        )
        bids = ((100.000, 15_000.0), (99.800, 1_000.0))
        asks = ((101.000, 15_000.0), (101.200, 1_000.0))
        rows = [
            tick(0, bids=bids, asks=asks),
            tick(15, bids=bids, asks=asks),
            tick(30, bids=bids, asks=asks),
            tick(
                33, bids=bids, asks=asks, last=100.001,
                trade_bonds=1_000.0, inferred_side="sell",
            ),
            tick(
                36, bids=bids, asks=asks, last=100.999,
                trade_bonds=1_000.0, inferred_side="buy",
            ),
        ]

        result, fills, quotes = run_day_v02(
            rows, parameters=parameters,
        )

        self.assertEqual(
            [(fill.side, fill.quantity_bonds) for fill in fills],
            [("buy", 500), ("sell", 500)],
        )
        sell_places = [
            event for event in quotes
            if event.action == "place" and event.side == "sell"
        ]
        self.assertEqual(len(sell_places), 1)
        self.assertEqual(sell_places[0].quantity_bonds, 500)
        self.assertEqual(result.ending_inventory_bonds, 10_000)
        self.assertEqual(result.ending_inventory_deviation_bonds, 0)
        self.assertEqual(result.stranded_exposure_bonds, 0)
        self.assertEqual(result.completed_turns, 1)
        self.assertTrue(any(
            event.action == "cancel"
            and event.side == "buy"
            and event.reason == "returned_to_neutral_rebuild_next_frame"
            for event in quotes
        ))

    def test_opposite_episode_certification_cannot_enlarge_exit_order(
        self,
    ) -> None:
        small_bid = ((100.000, 1_000.0), (99.800, 1_000.0))
        bid_wall = ((100.000, 15_000.0), (99.800, 1_000.0))
        ask_wall = ((101.000, 15_000.0), (101.200, 1_000.0))
        rows = [
            tick(0, bids=small_bid, asks=ask_wall),
            tick(15, bids=small_bid, asks=ask_wall),
            tick(30, bids=small_bid, asks=ask_wall),
            # First create a short probe against the ask episode.
            tick(
                33, bids=bid_wall, asks=ask_wall, last=100.999,
                trade_bonds=1_000.0, inferred_side="buy",
            ),
            # Certify that ask episode for a 2,000-bond cumulative stage.
            tick(
                36, bids=bid_wall, asks=ask_wall, last=101.000,
                trade_bonds=1_000.0, inferred_side="buy",
            ),
            tick(39, bids=bid_wall, asks=ask_wall),
            tick(48, bids=bid_wall, asks=ask_wall),
            tick(63, bids=bid_wall, asks=ask_wall),
            # The independent bid episode closes the 1,000 short to neutral.
            tick(
                66, bids=bid_wall, asks=ask_wall, last=100.001,
                trade_bonds=1_000.0, inferred_side="sell",
            ),
            tick(69, bids=bid_wall, asks=ask_wall),
            # A fresh 1,000 long is opened against the bid episode.
            tick(
                72, bids=bid_wall, asks=ask_wall, last=100.001,
                trade_bonds=1_000.0, inferred_side="sell",
            ),
        ]

        result, _, quotes = run_day_v02(rows)

        final_sell = [
            event for event in quotes
            if event.action == "place"
            and event.side == "sell"
            and event.market_time == "10:01:12.000"
        ]
        self.assertEqual(len(final_sell), 1)
        # The ask episode has one certification and a 2,000 stage, but this
        # order is only an exit for the bid episode's live 1,000 long.
        self.assertEqual(final_sell[0].certification_count, 1)
        self.assertEqual(final_sell[0].cumulative_target_bonds, 2_000)
        self.assertEqual(final_sell[0].quantity_bonds, 1_000)
        self.assertEqual(result.ending_inventory_deviation_bonds, 1_000)
        self.assertEqual(result.maximum_absolute_inventory_deviation_bonds, 1_000)
        # Earlier in the scenario the ask episode legitimately carried a
        # 1,000 short plus its own certified 1,000 scale order.  The 2,000
        # peak therefore does not mean that certification enlarged this
        # later, opposite-direction 1,000 exit order.
        self.assertEqual(result.maximum_cumulative_risk_bonds, 2_000)


if __name__ == "__main__":
    unittest.main()
