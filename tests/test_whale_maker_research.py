from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from zhaiquant.maker import ReplayTick
from zhaiquant.whale_maker_research import (
    MODEL_ID,
    WHALE_V02_MODEL_ID,
    WhaleResearchParameters,
    WhaleV02Parameters,
    run_day,
    run_day_v02,
)


BASE = datetime(2026, 8, 21, 10, 0)


def tick(
    second: int,
    *,
    bids: tuple[tuple[float, float], ...] = ((100.000, 10_000.0),),
    asks: tuple[tuple[float, float], ...] = ((100.500, 1_000.0),),
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
        side_confidence="medium" if trade_bonds else "none",
        previous_close=100.250,
    )


class WhaleMakerResearchTests(unittest.TestCase):
    def test_defaults_are_large_bond_units_not_hands(self) -> None:
        parameters = WhaleResearchParameters()

        self.assertEqual(parameters.model_id, MODEL_ID)
        self.assertEqual(parameters.quote_quantity_bonds, 5_000)
        self.assertEqual(parameters.opening_inventory_bonds, 10_000)
        self.assertEqual(parameters.maximum_inventory_bonds, 20_000)
        self.assertEqual(parameters.effective_minimum_wall_bonds, 10_000)

    def test_wall_must_be_large_and_stable_before_quoting(self) -> None:
        rows = [
            tick(0, bids=((100.000, 9_990.0),)),
            tick(15, bids=((100.000, 9_990.0),)),
            tick(30, bids=((100.000, 10_000.0),)),
        ]

        result, fills, quotes = run_day(rows)

        self.assertEqual(result.placed_orders, 0)
        self.assertEqual(fills, [])
        self.assertEqual(quotes, [])

    def test_quotes_one_tick_ahead_and_records_existing_same_price_queue(self) -> None:
        book = ((100.001, 1_000.0), (100.000, 10_000.0))
        rows = [tick(0, bids=book), tick(15, bids=book), tick(18, bids=book)]

        result, _, quotes = run_day(rows)

        places = [event for event in quotes if event.action == "place"]
        self.assertEqual(result.placed_orders, 1)
        self.assertEqual(len(places), 1)
        self.assertEqual(places[0].side, "buy")
        self.assertAlmostEqual(places[0].price, 100.001)
        self.assertEqual(places[0].quantity_bonds, 5_000)
        self.assertEqual(places[0].queue_ahead_bonds, 1_000.0)
        self.assertEqual(places[0].wall_price, 100.000)

    def test_future_opposing_flow_consumes_queue_before_whale_order(self) -> None:
        book = ((100.001, 1_000.0), (100.000, 10_000.0))
        rows = [
            tick(0, bids=book),
            tick(15, bids=book),
            tick(
                18,
                bids=book,
                last=100.001,
                trade_bonds=6_000.0,
                inferred_side="sell",
            ),
        ]

        result, fills, _ = run_day(rows)

        self.assertEqual(result.passive_fills, 1)
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].fill_kind, "passive")
        self.assertEqual(fills[0].quantity_bonds, 5_000)
        self.assertEqual(fills[0].inventory_after_bonds, 15_000)

    def test_same_tick_trade_cannot_fill_a_new_quote(self) -> None:
        rows = [
            tick(0),
            tick(
                15,
                last=100.001,
                trade_bonds=10_000.0,
                inferred_side="sell",
            ),
        ]

        result, fills, _ = run_day(rows)

        self.assertEqual(result.placed_orders, 1)
        self.assertEqual(fills, [])

    def test_damaged_wall_can_absorb_active_escape_on_later_tick(self) -> None:
        rows = [
            tick(0),
            tick(15),
            tick(
                18,
                last=100.001,
                trade_bonds=5_000.0,
                inferred_side="sell",
            ),
            tick(
                21,
                bids=((100.000, 5_000.0),),
                last=100.000,
                trade_bonds=5_000.0,
                inferred_side="sell",
            ),
        ]

        result, fills, _ = run_day(rows)

        self.assertEqual(
            [fill.fill_kind for fill in fills],
            ["passive", "active_escape"],
        )
        self.assertEqual(result.ending_inventory_bonds, 10_000)
        self.assertEqual(result.stranded_exposure_bonds, 0)
        self.assertAlmostEqual(result.marked_pnl_cny, -5.0)

    def test_disappeared_wall_is_not_invented_as_an_escape_fill(self) -> None:
        rows = [
            tick(0),
            tick(15),
            tick(
                18,
                last=100.001,
                trade_bonds=5_000.0,
                inferred_side="sell",
            ),
            tick(
                21,
                bids=((99.900, 1_000.0),),
                asks=((100.500, 1_000.0),),
                last=100.000,
                trade_bonds=5_000.0,
                inferred_side="sell",
            ),
        ]

        result, fills, _ = run_day(rows)

        self.assertEqual(len(fills), 1)
        self.assertEqual(result.active_escape_fills, 0)
        self.assertEqual(result.failed_escape_chunks, 1)
        self.assertEqual(result.stranded_exposure_bonds, 5_000)

    def test_sell_side_is_symmetric(self) -> None:
        asks = ((100.999, 1_000.0), (101.000, 10_000.0))
        rows = [
            tick(0, bids=((100.500, 1_000.0),), asks=asks),
            tick(15, bids=((100.500, 1_000.0),), asks=asks),
            tick(
                18,
                bids=((100.500, 1_000.0),),
                asks=asks,
                last=100.999,
                trade_bonds=6_000.0,
                inferred_side="buy",
            ),
        ]

        result, fills, quotes = run_day(rows)

        sell_places = [
            event for event in quotes
            if event.action == "place" and event.side == "sell"
        ]
        self.assertEqual(len(sell_places), 1)
        self.assertAlmostEqual(sell_places[0].price, 100.999)
        self.assertEqual(fills[0].side, "sell")
        self.assertEqual(result.ending_inventory_bonds, 5_000)

    def test_before_opening_caution_end_requires_one_yuan_edge(self) -> None:
        early = datetime(2026, 8, 21, 9, 20)
        rows = [
            tick(0, base=early),
            tick(15, base=early),
            tick(600, base=early),
        ]

        result, _, quotes = run_day(rows)

        places = [event for event in quotes if event.action == "place"]
        self.assertEqual(result.placed_orders, 1)
        self.assertEqual(places[0].market_time, "09:30:00.000")


class WhaleMakerV02ResearchTests(unittest.TestCase):
    @staticmethod
    def paired_tick(
        second: int,
        *,
        bid_wall: float = 15_000.0,
        ask_wall: float = 15_000.0,
        last: float = 100.500,
        trade_bonds: float = 0.0,
        inferred_side: str = "none",
        bids: tuple[tuple[float, float], ...] | None = None,
        asks: tuple[tuple[float, float], ...] | None = None,
    ) -> ReplayTick:
        return tick(
            second,
            bids=bids if bids is not None else (
                (100.000, bid_wall), (99.900, 1_000.0),
            ),
            asks=asks if asks is not None else (
                (101.000, ask_wall), (101.100, 1_000.0),
            ),
            last=last,
            trade_bonds=trade_bonds,
            inferred_side=inferred_side,
        )

    def test_v02_exact_10000_observes_then_allows_only_1000_probe(self) -> None:
        rows = [
            self.paired_tick(0, bid_wall=10_000.0, ask_wall=10_000.0),
            self.paired_tick(15, bid_wall=10_000.0, ask_wall=10_000.0),
            self.paired_tick(30, bid_wall=10_000.0, ask_wall=10_000.0),
            self.paired_tick(60, bid_wall=10_000.0, ask_wall=10_000.0),
        ]

        result, fills, quotes = run_day_v02(rows)

        self.assertEqual(result.model_id, WHALE_V02_MODEL_ID)
        self.assertEqual(result.placed_orders, 1)
        self.assertEqual(fills, [])
        places = [event for event in quotes if event.action == "place"]
        self.assertEqual(len(places), 1)
        self.assertEqual(places[0].quantity_bonds, 1_000)
        self.assertEqual(places[0].cumulative_target_bonds, 1_000)

    def test_probe_requires_observations_and_opposite_exit_corridor(self) -> None:
        rows = [
            self.paired_tick(0),
            self.paired_tick(15),
            self.paired_tick(30),
        ]

        result, _, quotes = run_day_v02(rows)

        places = [event for event in quotes if event.action == "place"]
        self.assertEqual(result.placed_orders, 1)
        self.assertEqual({event.side for event in places}, {"buy"})
        self.assertTrue(all(event.quantity_bonds == 1_000 for event in places))
        self.assertTrue(all(event.cumulative_target_bonds == 1_000 for event in places))
        self.assertTrue(all(event.wall_episode_id > 0 for event in places))

        one_sided = [
            self.paired_tick(
                0, asks=((100.100, 500.0), (100.200, 1_000.0)),
            ),
            self.paired_tick(
                15, asks=((100.100, 500.0), (100.200, 1_000.0)),
            ),
            self.paired_tick(
                30, asks=((100.100, 500.0), (100.200, 1_000.0)),
            ),
        ]
        one_sided_result, _, _ = run_day_v02(one_sided)
        self.assertEqual(one_sided_result.placed_orders, 0)

    def test_backup_level_gap_and_quantity_boundaries_are_symmetric(self) -> None:
        def places(
            bids: tuple[tuple[float, float], ...],
            asks: tuple[tuple[float, float], ...],
        ) -> set[str]:
            _, _, quotes = run_day_v02([
                self.paired_tick(0, bids=bids, asks=asks),
                self.paired_tick(15, bids=bids, asks=asks),
                self.paired_tick(30, bids=bids, asks=asks),
            ])
            return {
                event.side for event in quotes if event.action == "place"
            }

        self.assertIn("buy", places(
            ((100.000, 15_000.0), (99.800, 1_000.0)),
            ((101.000, 15_000.0), (101.100, 1_000.0)),
        ))
        self.assertNotIn("buy", places(
            ((100.000, 15_000.0), (99.799, 1_000.0)),
            ((101.000, 15_000.0), (101.100, 1_000.0)),
        ))
        self.assertNotIn("buy", places(
            ((100.000, 15_000.0), (99.800, 999.0)),
            ((101.000, 15_000.0), (101.100, 1_000.0)),
        ))
        self.assertIn("sell", places(
            ((100.000, 15_000.0), (99.900, 999.0)),
            ((101.000, 15_000.0), (101.200, 1_000.0)),
        ))
        self.assertNotIn("sell", places(
            ((100.000, 15_000.0), (99.900, 999.0)),
            ((101.000, 15_000.0), (101.201, 1_000.0)),
        ))

    def test_attack_frame_cannot_certify_but_later_maintained_wall_can(self) -> None:
        rows = [
            self.paired_tick(0),
            self.paired_tick(15),
            self.paired_tick(30),
            self.paired_tick(
                33, last=100.001, trade_bonds=1_000.0,
                inferred_side="sell",
            ),
            self.paired_tick(
                36, last=100.000, trade_bonds=1_000.0,
                inferred_side="sell",
            ),
            self.paired_tick(39),
        ]

        _, fills, quotes = run_day_v02(rows)

        buy_places = [
            event for event in quotes
            if event.action == "place" and event.side == "buy"
        ]
        self.assertEqual([event.market_time for event in buy_places], [
            "10:00:30.000", "10:00:39.000",
        ])
        self.assertEqual([event.cumulative_target_bonds for event in buy_places], [
            1_000, 2_000,
        ])
        self.assertEqual(buy_places[1].quantity_bonds, 1_000)
        self.assertEqual(fills[0].wall_episode_id, buy_places[0].wall_episode_id)

    def test_attack_in_same_frame_as_probe_fill_does_not_scale(self) -> None:
        rows = [
            self.paired_tick(0),
            self.paired_tick(15),
            self.paired_tick(30),
            self.paired_tick(
                33, last=100.000, trade_bonds=1_000.0,
                inferred_side="sell",
            ),
            self.paired_tick(36),
            self.paired_tick(39),
        ]

        _, _, quotes = run_day_v02(rows)

        buy_places = [
            event for event in quotes
            if event.action == "place" and event.side == "buy"
        ]
        self.assertEqual(len(buy_places), 1)
        self.assertEqual(buy_places[0].cumulative_target_bonds, 1_000)

    def test_three_survived_attacks_reach_5000_only_after_60_seconds(self) -> None:
        rows = [
            self.paired_tick(0, bid_wall=26_000.0, ask_wall=26_000.0),
            self.paired_tick(15, bid_wall=26_000.0, ask_wall=26_000.0),
            self.paired_tick(30, bid_wall=26_000.0, ask_wall=26_000.0),
            self.paired_tick(
                33, bid_wall=26_000.0, ask_wall=26_000.0,
                last=100.001, trade_bonds=1_000.0, inferred_side="sell",
            ),
            self.paired_tick(
                36, bid_wall=26_000.0, ask_wall=26_000.0,
                last=100.000, trade_bonds=1_000.0, inferred_side="sell",
            ),
            self.paired_tick(39, bid_wall=26_000.0, ask_wall=26_000.0),
            self.paired_tick(
                42, bid_wall=26_000.0, ask_wall=26_000.0,
                last=100.001, trade_bonds=1_000.0, inferred_side="sell",
            ),
            self.paired_tick(
                45, bid_wall=26_000.0, ask_wall=26_000.0,
                last=100.000, trade_bonds=1_000.0, inferred_side="sell",
            ),
            self.paired_tick(48, bid_wall=26_000.0, ask_wall=26_000.0),
            self.paired_tick(
                51, bid_wall=26_000.0, ask_wall=26_000.0,
                last=100.001, trade_bonds=1_000.0, inferred_side="sell",
            ),
            self.paired_tick(
                54, bid_wall=26_000.0, ask_wall=26_000.0,
                last=100.000, trade_bonds=1_000.0, inferred_side="sell",
            ),
            self.paired_tick(57, bid_wall=26_000.0, ask_wall=26_000.0),
            self.paired_tick(60, bid_wall=26_000.0, ask_wall=26_000.0),
        ]

        result, _, quotes = run_day_v02(rows)

        buy_places = [
            event for event in quotes
            if event.action == "place" and event.side == "buy"
        ]
        self.assertEqual(
            [event.cumulative_target_bonds for event in buy_places],
            [1_000, 2_000, 3_000, 5_000],
        )
        self.assertEqual([event.quantity_bonds for event in buy_places], [
            1_000, 1_000, 1_000, 2_000,
        ])
        self.assertEqual(buy_places[-1].market_time, "10:01:00.000")
        self.assertEqual(result.maximum_cumulative_risk_bonds, 5_000)

    def test_unexplained_20_percent_shrink_ends_scaling_and_risk_exits(self) -> None:
        rows = [
            self.paired_tick(0),
            self.paired_tick(15),
            self.paired_tick(30),
            self.paired_tick(
                33, last=100.001, trade_bonds=1_000.0,
                inferred_side="sell",
            ),
            self.paired_tick(
                36,
                bid_wall=11_500.0,
                bids=((100.000, 11_500.0),),
            ),
        ]

        result, fills, quotes = run_day_v02(rows)

        self.assertEqual(
            [fill.fill_kind for fill in fills],
            ["passive", "active_risk_exit"],
        )
        self.assertEqual(result.ending_inventory_deviation_bonds, 0)
        self.assertEqual(result.stranded_exposure_bonds, 0)
        self.assertTrue(any(
            event.action == "cancel" and event.side == "sell"
            for event in quotes
        ))

    def test_new_risk_block_cannot_escape_until_a_strictly_later_frame(self) -> None:
        rows = [
            self.paired_tick(0, bid_wall=12_000.0, ask_wall=12_000.0),
            self.paired_tick(15, bid_wall=12_000.0, ask_wall=12_000.0),
            self.paired_tick(30, bid_wall=12_000.0, ask_wall=12_000.0),
            self.paired_tick(
                31,
                bids=((99.900, 1_000.0),),
                asks=((100.500, 12_000.0),),
                last=100.001,
                trade_bonds=500.0,
                inferred_side="sell",
            ),
            self.paired_tick(
                32,
                bids=((99.900, 1_000.0),),
                asks=((100.500, 12_000.0),),
            ),
        ]

        result, fills, _ = run_day_v02(rows)

        self.assertEqual(
            [(fill.fill_kind, fill.market_time) for fill in fills],
            [
                ("passive", "10:00:31.000"),
                ("active_risk_exit", "10:00:32.000"),
            ],
        )
        self.assertEqual(result.ending_inventory_deviation_bonds, 0)
        self.assertEqual(result.maximum_cumulative_risk_bonds, 1_000)

    def test_older_probe_exits_immediately_when_real_attack_damages_wall(self) -> None:
        rows = [
            self.paired_tick(0, bid_wall=42_050.0),
            self.paired_tick(15, bid_wall=42_050.0),
            self.paired_tick(30, bid_wall=42_050.0),
            self.paired_tick(
                31,
                bid_wall=42_050.0,
                last=100.001,
                trade_bonds=1_000.0,
                inferred_side="sell",
            ),
            self.paired_tick(
                34,
                bid_wall=27_060.0,
                last=100.000,
                trade_bonds=14_990.0,
                inferred_side="sell",
            ),
        ]

        result, fills, _ = run_day_v02(rows)

        self.assertEqual(
            [(fill.fill_kind, fill.market_time) for fill in fills],
            [
                ("passive", "10:00:31.000"),
                ("active_risk_exit", "10:00:34.000"),
            ],
        )
        self.assertEqual(
            fills[-1].reason,
            "compatible_attack_significant_wall_damage",
        )
        self.assertEqual(result.ending_inventory_deviation_bonds, 0)
        self.assertAlmostEqual(result.realized_closed_loop_gross_pnl_cny, -1.0)

    def test_same_price_reappearance_gets_new_episode_and_no_old_certification(self) -> None:
        rows = [
            self.paired_tick(0),
            self.paired_tick(15),
            self.paired_tick(30),
            self.paired_tick(
                33,
                bids=((99.900, 1_000.0),),
                asks=((101.000, 1_000.0),),
            ),
            self.paired_tick(36, ask_wall=1_000.0),
            self.paired_tick(51, ask_wall=1_000.0),
            self.paired_tick(66, ask_wall=1_000.0),
        ]

        _, _, quotes = run_day_v02(rows)

        buy_places = [
            event for event in quotes
            if event.action == "place" and event.side == "buy"
        ]
        self.assertEqual(len(buy_places), 2)
        self.assertNotEqual(
            buy_places[0].wall_episode_id,
            buy_places[1].wall_episode_id,
        )
        self.assertEqual(buy_places[1].cumulative_target_bonds, 1_000)
        self.assertEqual(buy_places[1].certification_count, 0)

    def test_accounting_separates_closed_and_open_mark_contribution(self) -> None:
        rows = [
            self.paired_tick(0),
            self.paired_tick(15),
            self.paired_tick(30),
            self.paired_tick(
                33, last=100.001, trade_bonds=1_000.0,
                inferred_side="sell",
            ),
            self.paired_tick(
                36, last=100.999, trade_bonds=1_000.0,
                inferred_side="buy",
            ),
        ]

        result, _, _ = run_day_v02(rows)

        self.assertAlmostEqual(result.realized_closed_loop_gross_pnl_cny, 998.0)
        self.assertAlmostEqual(result.open_exposure_marked_contribution_cny, 0.0)
        self.assertAlmostEqual(result.marked_pnl_cny, 998.0)
        self.assertAlmostEqual(result.accounting_residual_cny, 0.0)
        self.assertEqual(result.attributed_passive_fill_ratio, 1.0)

    def test_partial_attribution_scales_fill_and_preserves_risk_block_trace(self) -> None:
        rows = [
            self.paired_tick(0),
            self.paired_tick(15),
            self.paired_tick(30),
            self.paired_tick(
                33, last=100.001, trade_bonds=1_000.0,
                inferred_side="sell",
            ),
            self.paired_tick(
                36, last=100.000, trade_bonds=1_000.0,
                inferred_side="sell",
            ),
            self.paired_tick(39),
        ]

        result, fills, quotes = run_day_v02(
            rows,
            parameters=WhaleV02Parameters(
                passive_trade_attribution_ratio=0.50,
            ),
        )

        passive = [fill for fill in fills if fill.fill_kind == "passive"]
        self.assertEqual(len(passive), 2)
        self.assertEqual([fill.quantity_bonds for fill in passive], [500, 500])
        self.assertTrue(all(fill.risk_block_id > 0 for fill in passive))
        self.assertTrue(all(fill.affected_risk_block_ids for fill in passive))
        self.assertEqual(result.created_risk_blocks, 2)
        self.assertEqual(result.attributed_passive_fill_ratio, 0.50)
        buy_places = [
            event for event in quotes
            if event.action == "place" and event.side == "buy"
        ]
        # The episode earns attack certification only after the two partial
        # fills have cumulatively completed the 1,000-bond probe.
        self.assertEqual(
            [event.cumulative_target_bonds for event in buy_places],
            [1_000],
        )


if __name__ == "__main__":
    unittest.main()
