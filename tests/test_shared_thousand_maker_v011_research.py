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
    SHARED_THOUSAND_POLICY_V01_CANDIDATE,
    SHARED_THOUSAND_POLICY_V011_CANDIDATE,
    SHARED_THOUSAND_POLICY_V011_WITHDRAWN_CANDIDATE,
    SHARED_THOUSAND_POLICY_V02_CANDIDATE,
    SHARED_THOUSAND_POLICY_V03_CANDIDATE,
)
from zhaiquant.one_hand_maker_research import (
    SHARED_THOUSAND_BONDS,
    _small_account_config,
)
from zhaiquant.shared_thousand_maker_v011_research import (
    MODEL_ID,
    PARENT_MODEL_ID,
)
from zhaiquant.types import SHANGHAI

from .helpers import test_config


def _tick(
    tick_id: int, moment: datetime, *, bid: float, ask: float,
    bid_bonds: float = 5_000.0, ask_bonds: float = 5_000.0,
    last: float | None = None, trade_bonds: float = 0.0,
    inferred_side: str = "unknown",
    bids: tuple[tuple[float, float], ...] | None = None,
    asks: tuple[tuple[float, float], ...] | None = None,
) -> ReplayTick:
    return ReplayTick(
        tick_id=tick_id,
        code="132026.SH",
        market_ts_ms=int(moment.timestamp() * 1_000),
        market_date=moment.date().isoformat(),
        market_time=moment.time().isoformat(timespec="milliseconds"),
        last_price=bid if last is None else last,
        bids=bids or tuple(
            (bid - index * 0.001, bid_bonds) for index in range(5)
        ),
        asks=asks or tuple(
            (ask + index * 0.001, ask_bonds) for index in range(5)
        ),
        trade_bonds=trade_bonds,
        transaction_delta=1 if trade_bonds > 0 else 0,
        inferred_side=inferred_side,
        side_confidence="high" if trade_bonds > 0 else "none",
        previous_close=136.800,
    )


def _assessment(
    *, state: str = "possible_fall", recent_buy: float = 1_000.0,
    recent_sell: float = 6_000.0, short_ask_change: float = -0.20,
) -> MarketAssessment:
    return MarketAssessment(
        reference_price=136.800,
        reference_low=136.700,
        reference_high=136.900,
        reference_source="intraday_trade_anchor",
        reference_confidence=0.75,
        state=state,
        state_score=-1 if state != "stable" else 0,
        state_confidence=0.75,
        recent_buy_bonds=recent_buy,
        recent_sell_bonds=recent_sell,
        midpoint_change=-0.20 if state != "stable" else 0.0,
        short_ask_change=short_ask_change,
        largest_ask_gap=0.0,
        downside_book_vacuum=False,
        fragile_top_bid=False,
        iron_floor_price=None,
        iron_floor_bonds=0.0,
        evidence=(),
    )


def _context(*, spread: float = 0.20) -> MakerDecisionContext:
    return MakerDecisionContext(
        reference_price=136.800,
        reference_source="intraday_trade_anchor",
        reliable_anchor=True,
        spread=spread,
        bid_support_bonds=10_000.0,
        ask_supply_bonds=5_000.0,
        wall_threshold_bonds=5_000.0,
    )


class SharedThousandMakerV011Tests(unittest.TestCase):
    def _open_lot(
        self, engine: MakerPaperEngine, tick: ReplayTick, *,
        price: float = 136.702, quantity: float = SHARED_THOUSAND_BONDS,
        kind: str = "low_bid_reversion", target_price: float | None = None,
    ):
        account = next(iter(engine.accounts.values()))
        order = engine._new_order(
            account, tick, side="buy", kind=kind,
            lot_id=None, price=price, quantity=quantity, queue_ahead=0.0,
            target_price=target_price, price_boundary=price, persist=True,
        )
        account.buy_order = order
        self.assertTrue(engine._fill_buy(
            account, tick, order, quantity,
            tick.market_ts_ms * 1_000_000,
            kind=kind, target_price=target_price,
            persist=True, reason="passive_buy",
        ))
        lot = next(
            item for item in account.lots.values()
            if item.entry_price is not None
        )
        return account, lot

    def _engine(self, database: Path, policy):
        config = _small_account_config(
            test_config(database), database,
            shared_capacity_bonds=SHARED_THOUSAND_BONDS,
        )
        store = SQLiteStore(config)
        store.start_session()
        engine = MakerPaperEngine(
            config, store, bond_code="132026.SH",
            priority_policy=policy, fill_modes=("priority",),
            include_windfall=False,
        )
        return engine, store

    def test_v011_replaces_withdrawn_build_without_mutating_siblings(self) -> None:
        self.assertEqual(MODEL_ID, "maker_shared_1000_v0_11_candidate_r2")
        self.assertEqual(
            PARENT_MODEL_ID,
            SHARED_THOUSAND_POLICY_V011_WITHDRAWN_CANDIDATE.model_id,
        )
        self.assertEqual(
            SHARED_THOUSAND_POLICY_V011_CANDIDATE.model_version,
            "0.11-candidate-r2",
        )
        self.assertFalse(
            SHARED_THOUSAND_POLICY_V011_CANDIDATE
                .enable_live_priority_extra_inventory_exit_exposure
        )
        self.assertTrue(
            SHARED_THOUSAND_POLICY_V011_CANDIDATE
                .enable_guarded_live_priority_extra_inventory_exit_exposure
        )
        self.assertTrue(
            SHARED_THOUSAND_POLICY_V011_WITHDRAWN_CANDIDATE
                .enable_live_priority_extra_inventory_exit_exposure
        )
        self.assertFalse(
            SHARED_THOUSAND_POLICY_V011_WITHDRAWN_CANDIDATE
                .enable_guarded_live_priority_extra_inventory_exit_exposure
        )
        for unchanged in (
            SHARED_THOUSAND_POLICY_V01_CANDIDATE,
            SHARED_THOUSAND_POLICY_V02_CANDIDATE,
            SHARED_THOUSAND_POLICY_V03_CANDIDATE,
        ):
            self.assertFalse(
                unchanged.enable_live_priority_extra_inventory_exit_exposure
            )
            self.assertFalse(
                unchanged
                    .enable_guarded_live_priority_extra_inventory_exit_exposure
            )

    def test_v011_does_not_replace_a_parent_native_sell_order(self) -> None:
        moment = datetime(2026, 9, 2, 11, 14, 41, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engines = []
            stores = []
            try:
                for name, policy in (
                    ("v01", SHARED_THOUSAND_POLICY_V01_CANDIDATE),
                    ("v011", SHARED_THOUSAND_POLICY_V011_CANDIDATE),
                ):
                    engine, store = self._engine(root / f"{name}.sqlite3", policy)
                    stores.append(store)
                    engine._start_date(moment.date().isoformat())
                    account, lot = self._open_lot(
                        engine,
                        _tick(1, moment, bid=136.399, ask=136.400),
                        price=136.400,
                    )
                    later = _tick(
                        2, moment + timedelta(seconds=3),
                        bid=136.600, ask=136.800,
                    )
                    with patch.object(
                        engine, "_decision_context", return_value=_context(),
                    ):
                        engine._refresh_orders(
                            account, later,
                            _assessment(
                                state="stable", recent_sell=0.0,
                                short_ask_change=0.0,
                            ),
                            persist=True,
                        )
                    engines.append((account, lot))
                parent_order = engines[0][0].sell_orders[engines[0][1].db_id]
                revised_order = engines[1][0].sell_orders[engines[1][1].db_id]
                self.assertEqual(revised_order.kind, parent_order.kind)
                self.assertEqual(
                    revised_order.limit_price, parent_order.limit_price,
                )
                self.assertEqual(revised_order.kind, "inventory_exit")
            finally:
                for store in stores:
                    store.close()

    def test_v011_follows_a_normal_falling_offer_beyond_old_loss_limits(
        self,
    ) -> None:
        moment = datetime(2026, 9, 2, 11, 14, 41, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            engine, store = self._engine(root / "v011.sqlite3", SHARED_THOUSAND_POLICY_V011_CANDIDATE)
            try:
                engine._start_date(moment.date().isoformat())
                account, lot = self._open_lot(
                    engine, _tick(1, moment, bid=136.701, ask=136.702),
                )
                context = _context()
                pressure = _assessment()
                frames = (
                    _tick(2, moment + timedelta(minutes=3, seconds=24), bid=136.400, ask=136.511),
                    _tick(3, moment + timedelta(minutes=8), bid=136.200, ask=136.399),
                )
                for frame in frames:
                    with patch.object(engine, "_decision_context", return_value=context):
                        engine._refresh_orders(account, frame, pressure, persist=True)
                    order = account.sell_orders.get(lot.db_id)
                    self.assertIsNotNone(order)
                    assert order is not None
                    self.assertAlmostEqual(order.limit_price, frame.ask1 - 0.001)
                    self.assertEqual(order.kind, "live_priority_extra_inventory_exit")
                    self.assertEqual(order.remaining, SHARED_THOUSAND_BONDS)
                self.assertLess(
                    account.sell_orders[lot.db_id].limit_price,
                    lot.entry_price - 0.15,
                )
            finally:
                store.close()

    def test_v011_does_not_reuse_the_buy_fill_snapshot_for_fallback(self) -> None:
        moment = datetime(2026, 9, 2, 11, 14, 41, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "same-frame.sqlite3",
                SHARED_THOUSAND_POLICY_V011_CANDIDATE,
            )
            try:
                engine._start_date(moment.date().isoformat())
                fill_tick = _tick(
                    1, moment, bid=136.701, ask=136.702,
                )
                account, lot = self._open_lot(engine, fill_tick)
                with patch.object(
                    engine, "_decision_context", return_value=_context(),
                ):
                    engine._refresh_orders(
                        account, fill_tick, _assessment(), persist=True,
                    )
                self.assertNotIn(lot.db_id, account.sell_orders)
            finally:
                store.close()

    def test_v011_one_tick_spread_stays_passive_at_the_offer(self) -> None:
        moment = datetime(2026, 9, 2, 11, 14, 41, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "one-tick.sqlite3",
                SHARED_THOUSAND_POLICY_V011_CANDIDATE,
            )
            try:
                engine._start_date(moment.date().isoformat())
                account, lot = self._open_lot(
                    engine, _tick(1, moment, bid=136.701, ask=136.702),
                )
                tight = _tick(
                    2, moment + timedelta(minutes=3),
                    bid=136.510, ask=136.511,
                )
                with patch.object(
                    engine, "_decision_context", return_value=_context(),
                ):
                    engine._refresh_orders(
                        account, tight, _assessment(), persist=True,
                    )
                order = account.sell_orders[lot.db_id]
                self.assertEqual(order.limit_price, 136.511)
                self.assertGreater(order.limit_price, tight.bid1)
            finally:
                store.close()

    def test_v01_still_allows_the_same_loss_frame_to_have_no_exit(self) -> None:
        moment = datetime(2026, 9, 2, 11, 14, 41, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "v01.sqlite3",
                SHARED_THOUSAND_POLICY_V01_CANDIDATE,
            )
            try:
                engine._start_date(moment.date().isoformat())
                account, lot = self._open_lot(
                    engine, _tick(1, moment, bid=136.701, ask=136.702),
                )
                loss_frame = _tick(
                    2, moment + timedelta(minutes=3, seconds=24),
                    bid=136.400, ask=136.511,
                )
                with patch.object(engine, "_decision_context", return_value=_context()):
                    engine._refresh_orders(
                        account, loss_frame, _assessment(), persist=True,
                    )
                self.assertNotIn(lot.db_id, account.sell_orders)
            finally:
                store.close()

    def test_v011_keeps_parent_high_exit_even_when_later_pressure_changes(
        self,
    ) -> None:
        moment = datetime(2026, 9, 2, 11, 14, 41, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "isolated.sqlite3",
                SHARED_THOUSAND_POLICY_V011_CANDIDATE,
            )
            try:
                engine._start_date(moment.date().isoformat())
                entry_tick = _tick(1, moment, bid=136.701, ask=136.702)
                account, lot = self._open_lot(engine, entry_tick)
                normal = _tick(
                    2, moment + timedelta(seconds=3),
                    bid=136.600, ask=136.800,
                    asks=((136.800, 1_000.0), (136.801, 2_000.0)),
                )
                with patch.object(engine, "_decision_context", return_value=_context()):
                    engine._refresh_orders(
                        account, normal,
                        _assessment(state="stable", recent_sell=0.0, short_ask_change=0.0),
                        persist=True,
                    )
                self.assertEqual(account.sell_orders[lot.db_id].limit_price, 136.799)
                account.last_asks = normal.asks

                isolated = _tick(
                    3, moment + timedelta(seconds=6),
                    bid=136.500, ask=136.510, ask_bonds=1_000.0,
                    bids=((136.500, 1_000.0), (136.499, 2_000.0), (136.300, 5_000.0)),
                    asks=((136.510, 1_000.0), (136.800, 1_000.0), (136.801, 2_000.0)),
                )
                quiet = _assessment(
                    state="stable", recent_buy=1_000.0,
                    recent_sell=1_000.0, short_ask_change=0.0,
                )
                with patch.object(engine, "_decision_context", return_value=_context(spread=0.010)):
                    engine._refresh_orders(account, isolated, quiet, persist=True)
                held = account.sell_orders[lot.db_id]
                self.assertEqual(held.limit_price, 136.799)
                self.assertEqual(
                    held.kind, "live_priority_extra_inventory_isolated_hold",
                )

                with patch.object(engine, "_decision_context", return_value=_context(spread=0.010)):
                    engine._refresh_orders(
                        account, replace(isolated, tick_id=4, market_ts_ms=isolated.market_ts_ms + 3_000),
                        _assessment(), persist=True,
                    )
                followed = account.sell_orders[lot.db_id]
                self.assertEqual(followed.limit_price, 136.799)
                self.assertEqual(
                    followed.kind,
                    "live_priority_extra_inventory_isolated_hold",
                )
            finally:
                store.close()

    def test_v011_does_not_force_special_entry_lots_into_live_fallback(
        self,
    ) -> None:
        moment = datetime(2026, 9, 2, 11, 20, 0, tzinfo=SHANGHAI)
        live_kinds = {
            "live_priority_extra_inventory_exit",
            "live_priority_extra_inventory_isolated_hold",
        }
        with tempfile.TemporaryDirectory() as temporary:
            for index, kind in enumerate((
                "deep_discount_sweep",
                "sweep_tail",
                "joint_causal_corridor_entry",
                "adjacent_bid_cushion_entry",
            )):
                engine, store = self._engine(
                    Path(temporary) / f"special-{index}.sqlite3",
                    SHARED_THOUSAND_POLICY_V011_CANDIDATE,
                )
                try:
                    engine._start_date(moment.date().isoformat())
                    account, lot = self._open_lot(
                        engine,
                        _tick(1, moment, bid=136.701, ask=136.702),
                        kind=kind, target_price=136.900,
                    )
                    frame = _tick(
                        2, moment + timedelta(minutes=3),
                        bid=136.400, ask=136.511,
                    )
                    with patch.object(
                        engine, "_decision_context", return_value=_context(),
                    ):
                        engine._refresh_orders(
                            account, frame, _assessment(), persist=True,
                        )
                    order = account.sell_orders.get(lot.db_id)
                    if order is not None:
                        self.assertNotIn(order.kind, live_kinds)
                finally:
                    store.close()

    def test_v011_protects_a_just_filled_corridor_from_distant_bid_dump(
        self,
    ) -> None:
        moment = datetime(2026, 8, 7, 14, 11, 56, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "rapid-gap.sqlite3",
                SHARED_THOUSAND_POLICY_V011_CANDIDATE,
            )
            try:
                engine._start_date(moment.date().isoformat())
                account, lot = self._open_lot(
                    engine,
                    _tick(1, moment, bid=140.103, ask=140.104),
                    price=140.104,
                    kind="joint_causal_corridor_entry",
                    target_price=140.398,
                )
                account.last_asks = (
                    (140.000, 3_000.0),
                    (140.398, 3_000.0),
                )
                gap = _tick(
                    2, moment + timedelta(seconds=9),
                    bid=138.999, ask=140.000,
                    bids=((138.999, 6_000.0), (138.998, 2_000.0)),
                    asks=((140.000, 3_000.0), (140.398, 3_000.0)),
                )
                self.assertTrue(engine._guarded_shared_rapid_gap_active_exit(
                    account, lot, gap, exit_price=138.999,
                ))
            finally:
                store.close()

    def test_v011_protects_a_five_thousand_bond_offer_parent_would_buy(
        self,
    ) -> None:
        moment = datetime(2026, 9, 2, 11, 20, 0, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "active-buy-exception.sqlite3",
                SHARED_THOUSAND_POLICY_V011_CANDIDATE,
            )
            try:
                engine._start_date(moment.date().isoformat())
                account, lot = self._open_lot(
                    engine,
                    _tick(1, moment - timedelta(minutes=1), bid=136.001, ask=136.702),
                )
                account.last_asks = ((136.800, 1_000.0), (136.801, 1_000.0))
                engine.observed_market_trade = True
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    int((moment - timedelta(seconds=3)).timestamp() * 1_000),
                    136.800, 5_000.0, 1, "buy",
                ))
                deep = _tick(
                    2, moment, bid=135.590, ask=135.600,
                    bid_bonds=5_000.0, ask_bonds=5_000.0,
                    bids=((135.590, 5_000.0), (135.589, 2_000.0)),
                    asks=((135.600, 5_000.0), (136.800, 1_000.0), (136.801, 1_000.0)),
                )
                self.assertGreater(
                    deep.ask1_bonds,
                    SHARED_THOUSAND_BONDS * 2.0,
                )
                with patch.object(engine, "_decision_context", return_value=_context(spread=0.010)):
                    engine._refresh_orders(account, deep, _assessment(), persist=True)
                protected = account.sell_orders[lot.db_id]
                self.assertEqual(protected.limit_price, 136.799)
                self.assertEqual(
                    protected.kind, "live_priority_extra_inventory_isolated_hold",
                )
            finally:
                store.close()

    def test_v011_near_cost_fill_releases_cash_and_requires_lower_reentry(
        self,
    ) -> None:
        moment = datetime(2026, 9, 2, 11, 14, 41, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "release-reentry.sqlite3",
                SHARED_THOUSAND_POLICY_V011_CANDIDATE,
            )
            try:
                engine._start_date(moment.date().isoformat())
                account, lot = self._open_lot(
                    engine, _tick(1, moment, bid=136.701, ask=136.702),
                )
                quote = _tick(
                    2, moment + timedelta(minutes=8),
                    bid=136.500, ask=136.700,
                )
                with patch.object(engine, "_decision_context", return_value=_context()):
                    engine._refresh_orders(account, quote, _assessment(), persist=True)
                self.assertEqual(account.sell_orders[lot.db_id].limit_price, 136.698)
                fill = _tick(
                    3, moment + timedelta(minutes=9),
                    bid=136.500, ask=136.700, last=136.699,
                    trade_bonds=SHARED_THOUSAND_BONDS, inferred_side="buy",
                )
                engine._process_resting_orders(
                    account, fill, persist=True,
                    received_ts_ns=fill.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 0.0)
                self.assertAlmostEqual(account.cash, 136_698.0)
                self.assertAlmostEqual(
                    account.cash - account.initial_cash, -4.0,
                )
                self.assertEqual(account.last_stalled_extra_exit_price, 136.698)

                same_level = _tick(
                    4, moment + timedelta(minutes=9, seconds=3),
                    bid=136.650, ask=136.900,
                )
                with patch.object(engine, "_decision_context", return_value=_context()):
                    engine._refresh_orders(account, same_level, _assessment(), persist=True)
                if account.buy_order is not None:
                    self.assertLessEqual(
                        account.buy_order.limit_price, 136.398 + 1e-9,
                    )
            finally:
                store.close()

    def test_v011_sell_quantity_never_exceeds_partial_inventory(self) -> None:
        moment = datetime(2026, 9, 2, 11, 14, 41, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "partial.sqlite3",
                SHARED_THOUSAND_POLICY_V011_CANDIDATE,
            )
            try:
                engine._start_date(moment.date().isoformat())
                account, lot = self._open_lot(
                    engine,
                    _tick(1, moment, bid=136.701, ask=136.702),
                    quantity=400.0,
                )
                falling = _tick(
                    2, moment + timedelta(minutes=3),
                    bid=136.400, ask=136.511,
                )
                with patch.object(engine, "_decision_context", return_value=_context()):
                    engine._refresh_orders(account, falling, _assessment(), persist=True)
                order = account.sell_orders[lot.db_id]
                self.assertEqual(order.remaining, 400.0)
                self.assertLessEqual(order.remaining, account.inventory)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
