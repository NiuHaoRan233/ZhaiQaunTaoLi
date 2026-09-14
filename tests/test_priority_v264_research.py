from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from zhaiquant.database import SQLiteStore
from zhaiquant.maker import MarketAssessment, ReplayTick, TradeEvidence
from zhaiquant.maker_paper import (
    MakerDecisionContext,
    MakerPaperEngine,
    PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V264_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V264_R2_CANDIDATE,
)
from zhaiquant.types import SHANGHAI

from .helpers import test_config


def _tick(
    tick_id: int, moment: datetime, *, bid: float, ask: float,
) -> ReplayTick:
    return ReplayTick(
        tick_id=tick_id,
        code="132026.SH",
        market_ts_ms=int(moment.timestamp() * 1_000),
        market_date=moment.date().isoformat(),
        market_time=moment.time().isoformat(timespec="milliseconds"),
        last_price=bid,
        bids=tuple((bid - index * 0.001, 5_000.0) for index in range(5)),
        asks=tuple((ask + index * 0.001, 5_000.0) for index in range(5)),
        trade_bonds=0.0,
        transaction_delta=0,
        inferred_side="none",
        side_confidence="none",
        previous_close=136.867,
    )


def _assessment() -> MarketAssessment:
    return MarketAssessment(
        reference_price=137.610,
        reference_low=137.500,
        reference_high=137.620,
        reference_source="intraday_trade_anchor",
        reference_confidence=0.75,
        state="stable",
        state_score=0,
        state_confidence=0.75,
        recent_buy_bonds=2_000.0,
        recent_sell_bonds=2_000.0,
        midpoint_change=0.0,
        short_ask_change=0.0,
        largest_ask_gap=0.0,
        downside_book_vacuum=False,
        fragile_top_bid=False,
        iron_floor_price=None,
        iron_floor_bonds=0.0,
        evidence=(),
    )


def _context(*, spread: float) -> MakerDecisionContext:
    return MakerDecisionContext(
        reference_price=137.610,
        reference_source="intraday_trade_anchor",
        reliable_anchor=True,
        spread=spread,
        bid_support_bonds=5_000.0,
        ask_supply_bonds=10_000.0,
        wall_threshold_bonds=5_000.0,
    )


class PriorityV264ResearchTests(unittest.TestCase):
    def _engine(self, database: Path, policy):
        config = test_config(database)
        store = SQLiteStore(config)
        store.start_session()
        engine = MakerPaperEngine(
            config,
            store,
            priority_policy=policy,
            fill_modes=("priority",),
            include_windfall=False,
        )
        return engine, store

    @staticmethod
    def _open_extra_lot(
        engine: MakerPaperEngine, tick: ReplayTick, *,
        kind: str = "low_bid_reversion",
    ):
        account = engine.accounts["maker_v01_priority"]
        order = engine._new_order(
            account,
            tick,
            side="buy",
            kind=kind,
            lot_id=None,
            price=137.442,
            quantity=1_000.0,
            queue_ahead=0.0,
            target_price=None,
            price_boundary=137.442,
            persist=True,
        )
        account.buy_order = order
        if not engine._fill_buy(
            account,
            tick,
            order,
            1_000.0,
            tick.market_ts_ms * 1_000_000,
            kind=kind,
            target_price=None,
            persist=True,
            reason="passive_buy",
        ):
            raise AssertionError("synthetic extra lot did not fill")
        lot = next(
            lot for lot in account.lots.values()
            if lot.entry_price is not None
        )
        return account, lot

    def test_v264_is_an_immutable_child_with_only_the_exit_gap_controls(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE
        policy = PRIORITY_POLICY_FIRST_POSITION_V264_CANDIDATE
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertEqual(policy.model_id, "maker_priority_v2_64_candidate")
        self.assertEqual(policy.model_version, "2.64-candidate")
        self.assertEqual(
            policy,
            replace(
                parent,
                model_id="maker_priority_v2_64_candidate",
                model_version="2.64-candidate",
                parent_model_id=parent.model_id,
                enable_guarded_live_priority_extra_inventory_exit_exposure=True,
                guarded_live_exit_maximum_loss=(
                    parent.support_collapse_initial_maximum_loss
                ),
                guarded_live_exit_release_reentry_on_high_attack=True,
                retain_guarded_live_exit_across_parent_order_gap=True,
            ),
        )
        self.assertFalse(
            parent.enable_guarded_live_priority_extra_inventory_exit_exposure,
        )
        self.assertIsNone(parent.guarded_live_exit_maximum_loss)
        self.assertTrue(parent.guarded_live_exit_records_stalled_reentry)

    def test_v264_keeps_the_filled_extra_lot_exposed_near_cost(
        self,
    ) -> None:
        moment = datetime(2026, 9, 4, 11, 16, 49, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "v264-live-exit.sqlite3",
                PRIORITY_POLICY_FIRST_POSITION_V264_CANDIDATE,
            )
            try:
                engine._start_date(moment.date().isoformat())
                fill_tick = _tick(1, moment, bid=137.440, ask=137.621)
                account, lot = self._open_extra_lot(engine, fill_tick)
                near_cost = _tick(
                    2,
                    moment + timedelta(seconds=15),
                    bid=137.470,
                    ask=137.500,
                )
                with patch.object(
                    engine,
                    "_decision_context",
                    return_value=_context(spread=0.030),
                ):
                    engine._refresh_orders(
                        account, near_cost, _assessment(), persist=True,
                    )
                order = account.sell_orders[lot.db_id]
                self.assertEqual(order.kind, "live_priority_extra_inventory_exit")
                self.assertAlmostEqual(order.limit_price, 137.499)
                self.assertEqual(order.remaining, 1_000.0)
                unchanged = replace(
                    near_cost,
                    tick_id=3,
                    market_ts_ms=near_cost.market_ts_ms + 3_000,
                    market_time="11:17:07.000",
                )
                with patch.object(
                    engine,
                    "_decision_context",
                    return_value=_context(spread=0.030),
                ):
                    engine._refresh_orders(
                        account, unchanged, _assessment(), persist=True,
                    )
                self.assertEqual(
                    account.sell_orders[lot.db_id].db_id, order.db_id,
                )
            finally:
                store.close()

    def test_v264_does_not_follow_the_fallback_beyond_the_existing_loss_bound(
        self,
    ) -> None:
        moment = datetime(2026, 9, 4, 11, 16, 49, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "v264-loss-bound.sqlite3",
                PRIORITY_POLICY_FIRST_POSITION_V264_CANDIDATE,
            )
            try:
                engine._start_date(moment.date().isoformat())
                account, lot = self._open_extra_lot(
                    engine, _tick(1, moment, bid=137.440, ask=137.621),
                )
                normal = _tick(
                    2,
                    moment + timedelta(seconds=15),
                    bid=137.310,
                    ask=137.400,
                )
                with patch.object(
                    engine,
                    "_decision_context",
                    return_value=_context(spread=0.090),
                ):
                    engine._refresh_orders(
                        account, normal, _assessment(), persist=True,
                    )
                self.assertAlmostEqual(
                    account.sell_orders[lot.db_id].limit_price, 137.399,
                )

                discontinuity = _tick(
                    3,
                    moment + timedelta(seconds=18),
                    bid=137.000,
                    ask=137.100,
                )
                with patch.object(
                    engine,
                    "_decision_context",
                    return_value=_context(spread=0.100),
                ):
                    engine._refresh_orders(
                        account, discontinuity, _assessment(), persist=True,
                    )
                self.assertNotIn(lot.db_id, account.sell_orders)
            finally:
                store.close()

    def test_v264_reentry_waits_for_space_but_real_high_attack_releases_it(
        self,
    ) -> None:
        moment = datetime(2026, 9, 4, 11, 16, 49, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "v264-reentry.sqlite3",
                PRIORITY_POLICY_FIRST_POSITION_V264_CANDIDATE,
            )
            try:
                engine._start_date(moment.date().isoformat())
                account, lot = self._open_extra_lot(
                    engine, _tick(1, moment, bid=137.440, ask=137.621),
                )
                exit_tick = _tick(
                    2,
                    moment + timedelta(seconds=30),
                    bid=137.470,
                    ask=137.499,
                )
                order = engine._new_order(
                    account,
                    exit_tick,
                    side="sell",
                    kind="live_priority_extra_inventory_exit",
                    lot_id=lot.db_id,
                    price=137.498,
                    quantity=1_000.0,
                    queue_ahead=0.0,
                    target_price=137.498,
                    price_boundary=137.498,
                    persist=True,
                )
                account.sell_orders[lot.db_id] = order
                engine._fill_sell(
                    account,
                    exit_tick,
                    order,
                    1_000.0,
                    exit_tick.market_ts_ms * 1_000_000,
                    persist=True,
                )
                self.assertEqual(account.inventory, account.initial_inventory)
                self.assertEqual(account.last_stalled_extra_exit_price, 137.498)
                self.assertEqual(
                    account.last_stalled_extra_exit_ts_ms,
                    exit_tick.market_ts_ms,
                )
                self.assertFalse(account.guarded_live_exit_reentry_released)

                high_attack = _tick(
                    3,
                    moment + timedelta(seconds=33),
                    bid=137.497,
                    ask=137.500,
                )
                engine.analyzer.trade_evidence.append(
                    TradeEvidence(
                        high_attack.market_ts_ms,
                        137.498,
                        1_000.0,
                        1,
                        "buy",
                    )
                )
                engine._update_guarded_live_exit_reentry_latch(
                    account, high_attack,
                )
                self.assertTrue(account.guarded_live_exit_reentry_released)
            finally:
                store.close()

    def test_v264_does_not_apply_the_fallback_to_a_special_entry_lot(
        self,
    ) -> None:
        moment = datetime(2026, 9, 4, 11, 16, 49, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "v264-special-lot.sqlite3",
                PRIORITY_POLICY_FIRST_POSITION_V264_CANDIDATE,
            )
            try:
                engine._start_date(moment.date().isoformat())
                account, lot = self._open_extra_lot(
                    engine,
                    _tick(1, moment, bid=137.440, ask=137.621),
                    kind="sweep_tail",
                )
                frame = _tick(
                    2,
                    moment + timedelta(seconds=15),
                    bid=137.310,
                    ask=137.400,
                )
                with patch.object(
                    engine,
                    "_decision_context",
                    return_value=_context(spread=0.090),
                ):
                    engine._refresh_orders(
                        account, frame, _assessment(), persist=True,
                    )
                order = account.sell_orders.get(lot.db_id)
                if order is not None:
                    self.assertNotIn(
                        order.kind,
                        {
                            "live_priority_extra_inventory_exit",
                            "live_priority_extra_inventory_isolated_hold",
                        },
                    )
            finally:
                store.close()

    def test_v264_r2_restarts_from_v263_without_the_withdrawn_locks(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE
        policy = PRIORITY_POLICY_FIRST_POSITION_V264_R2_CANDIDATE
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertEqual(policy.model_id, "maker_priority_v2_64_candidate_r2")
        self.assertEqual(policy.model_version, "2.64-candidate-r2")
        self.assertEqual(
            policy,
            replace(
                parent,
                model_id="maker_priority_v2_64_candidate_r2",
                model_version="2.64-candidate-r2",
                parent_model_id=parent.model_id,
                enable_guarded_live_priority_extra_inventory_exit_exposure=True,
                guarded_live_exit_maximum_loss=None,
                guarded_live_exit_records_stalled_reentry=False,
                guarded_live_exit_release_reentry_on_high_attack=False,
                retain_guarded_live_exit_across_parent_order_gap=True,
                guarded_live_exit_requires_current_sell_side_repricing=True,
                guarded_live_exit_protects_special_rapid_gap=False,
            ),
        )
        self.assertTrue(
            PRIORITY_POLICY_FIRST_POSITION_V264_CANDIDATE
                .guarded_live_exit_records_stalled_reentry,
        )
        self.assertFalse(
            PRIORITY_POLICY_FIRST_POSITION_V264_CANDIDATE
                .guarded_live_exit_requires_current_sell_side_repricing,
        )

    def test_v264_r2_exits_only_while_current_sell_repricing_is_live(
        self,
    ) -> None:
        moment = datetime(2026, 9, 4, 11, 16, 49, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "v264-r2-live-regime.sqlite3",
                PRIORITY_POLICY_FIRST_POSITION_V264_R2_CANDIDATE,
            )
            try:
                engine._start_date(moment.date().isoformat())
                account, lot = self._open_extra_lot(
                    engine, _tick(1, moment, bid=137.440, ask=137.621),
                )
                sell_repricing = replace(
                    _assessment(),
                    state="possible_rise",
                    recent_buy_bonds=10_000.0,
                    recent_sell_bonds=19_000.0,
                    short_ask_change=-0.129,
                )
                near_cost = _tick(
                    2,
                    moment + timedelta(seconds=15),
                    bid=137.470,
                    ask=137.500,
                )
                with patch.object(
                    engine,
                    "_decision_context",
                    return_value=_context(spread=0.030),
                ), patch.object(
                    engine,
                    "_confirmed_rise_is_recent",
                    return_value=False,
                ):
                    engine._refresh_orders(
                        account, near_cost, sell_repricing, persist=True,
                    )
                order = account.sell_orders[lot.db_id]
                self.assertEqual(
                    order.kind, "live_priority_extra_inventory_exit",
                )
                self.assertAlmostEqual(order.limit_price, 137.499)

                recovered = replace(
                    sell_repricing,
                    state="rising",
                    recent_buy_bonds=20_000.0,
                    recent_sell_bonds=19_000.0,
                    short_ask_change=0.0,
                )
                recovered_tick = _tick(
                    3,
                    moment + timedelta(seconds=18),
                    bid=137.470,
                    ask=137.620,
                )
                with patch.object(
                    engine,
                    "_decision_context",
                    return_value=_context(spread=0.150),
                ), patch.object(
                    engine,
                    "_confirmed_rise_is_recent",
                    return_value=True,
                ):
                    engine._refresh_orders(
                        account, recovered_tick, recovered, persist=True,
                    )
                replacement = account.sell_orders.get(lot.db_id)
                if replacement is not None:
                    self.assertNotIn(
                        replacement.kind,
                        {
                            "live_priority_extra_inventory_exit",
                            "live_priority_extra_inventory_isolated_hold",
                        },
                    )
            finally:
                store.close()

    def test_v264_r2_balanced_one_off_low_offer_does_not_force_exit(
        self,
    ) -> None:
        moment = datetime(2026, 8, 14, 14, 7, 8, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "v264-r2-balanced-offer.sqlite3",
                PRIORITY_POLICY_FIRST_POSITION_V264_R2_CANDIDATE,
            )
            try:
                engine._start_date(moment.date().isoformat())
                account, lot = self._open_extra_lot(
                    engine, _tick(1, moment, bid=136.004, ask=136.199),
                )
                balanced = replace(
                    _assessment(),
                    reference_price=136.101,
                    reference_low=136.086,
                    reference_high=136.116,
                    state="possible_fall",
                    recent_buy_bonds=5_000.0,
                    recent_sell_bonds=5_000.0,
                    short_ask_change=-0.189,
                )
                low_offer = _tick(
                    2,
                    moment + timedelta(seconds=18),
                    bid=136.004,
                    ask=136.010,
                )
                with patch.object(
                    engine,
                    "_decision_context",
                    return_value=_context(spread=0.006),
                ), patch.object(
                    engine,
                    "_confirmed_rise_is_recent",
                    return_value=False,
                ):
                    engine._refresh_orders(
                        account, low_offer, balanced, persist=True,
                    )
                order = account.sell_orders.get(lot.db_id)
                if order is not None:
                    self.assertNotIn(
                        order.kind,
                        {
                            "live_priority_extra_inventory_exit",
                            "live_priority_extra_inventory_isolated_hold",
                        },
                    )
            finally:
                store.close()

    def test_v264_r2_exit_writes_no_fixed_reentry_memory(self) -> None:
        moment = datetime(2026, 9, 4, 11, 17, 19, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "v264-r2-no-reentry-lock.sqlite3",
                PRIORITY_POLICY_FIRST_POSITION_V264_R2_CANDIDATE,
            )
            try:
                engine._start_date(moment.date().isoformat())
                account, lot = self._open_extra_lot(
                    engine,
                    _tick(
                        1,
                        moment - timedelta(seconds=30),
                        bid=137.440,
                        ask=137.621,
                    ),
                )
                exit_tick = _tick(2, moment, bid=137.441, ask=137.499)
                order = engine._new_order(
                    account,
                    exit_tick,
                    side="sell",
                    kind="live_priority_extra_inventory_exit",
                    lot_id=lot.db_id,
                    price=137.498,
                    quantity=1_000.0,
                    queue_ahead=0.0,
                    target_price=137.498,
                    price_boundary=137.498,
                    persist=True,
                )
                account.sell_orders[lot.db_id] = order
                engine._fill_sell(
                    account,
                    exit_tick,
                    order,
                    1_000.0,
                    exit_tick.market_ts_ms * 1_000_000,
                    persist=True,
                )
                self.assertEqual(account.inventory, account.initial_inventory)
                self.assertEqual(account.last_stalled_extra_exit_price, 0.0)
                self.assertEqual(account.last_stalled_extra_exit_ts_ms, 0)
                self.assertEqual(account.last_guarded_live_exit_price, 0.0)
                self.assertEqual(account.last_guarded_live_exit_ts_ms, 0)
            finally:
                store.close()
