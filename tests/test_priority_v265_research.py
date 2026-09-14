from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from zhaiquant.maker_paper import (
    PRIORITY_POLICY_FIRST_POSITION_V264_R2_CANDIDATE as PARENT,
    PRIORITY_POLICY_FIRST_POSITION_V265_CANDIDATE as CANDIDATE,
    REALTIME_COMPARISON_POLICIES,
)
from zhaiquant.types import SHANGHAI
from . import test_priority_v264_research as prior_tests
from .test_priority_v264_research import _tick, _assessment, _context


class PriorityV265ResearchTests(unittest.TestCase):
    _engine = prior_tests.PriorityV264ResearchTests._engine
    _open_extra_lot = staticmethod(prior_tests.PriorityV264ResearchTests._open_extra_lot)

    def _sell(self, engine, account, lot, tick, *,
              kind="stalled_extra_inventory_near_flat_exit", quantity=1_000):
        order = engine._new_order(account, tick, side="sell", kind=kind,
            lot_id=lot.db_id, price=138.001, quantity=quantity, queue_ahead=0,
            target_price=138.001, price_boundary=138.001, persist=True)
        account.sell_orders[lot.db_id] = order
        engine._fill_sell(account, tick, order, quantity,
                          tick.market_ts_ms * 1_000_000, persist=True)

    def test_only_new_profile_changes_and_old_profiles_keep_their_contract(self):
        self.assertEqual(CANDIDATE, replace(PARENT,
            model_id="maker_priority_v2_65_candidate",
            model_version="2.65-candidate", parent_model_id=PARENT.model_id,
            ordinary_risk_exit_uses_fresh_reentry=True))
        self.assertIs(REALTIME_COMPARISON_POLICIES[CANDIDATE.model_id], CANDIDATE)
        self.assertFalse(PARENT.ordinary_risk_exit_uses_fresh_reentry)

    def test_both_ordinary_exit_routes_use_new_frames_without_stale_caps(self):
        moment = datetime(2026, 8, 5, 11, 17, 12, tzinfo=SHANGHAI)
        for kind in ("stalled_extra_inventory_near_flat_exit",
                     "live_priority_extra_inventory_exit",
                     "live_priority_extra_inventory_isolated_hold"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                engine, store = self._engine(Path(temporary) / "paper.sqlite3", CANDIDATE)
                try:
                    engine._start_date(moment.date().isoformat())
                    account, lot = self._open_extra_lot(engine,
                        _tick(1, moment-timedelta(seconds=30), bid=137.440, ask=137.621))
                    sale = _tick(2, moment, bid=138.010, ask=138.198)
                    self._sell(engine, account, lot, sale, kind=kind)
                    self.assertEqual(account.inventory, 1_000)
                    self.assertEqual(account.last_stalled_extra_exit_price, 0)
                    self.assertEqual(account.pending_inventory_turn_quantity, 0)
                    self.assertTrue(engine._ordinary_risk_exit_needs_new_frame(account, sale))
                    with patch.object(engine, "_decision_context") as context:
                        engine._active_discount_entry(account, sale, _assessment(), persist=True)
                        context.assert_not_called()
                    engine.observed_market_trade = True
                    with patch.object(engine, "_decision_context", return_value=_context(spread=0.188)):
                        engine._refresh_orders(account, sale, _assessment(), persist=True)
                    self.assertIsNone(account.buy_order)
                    self.assertFalse(engine._ordinary_risk_exit_needs_new_frame(
                        account, replace(sale, market_ts_ms=sale.market_ts_ms+1)))
                finally:
                    store.close()

    def test_recovered_supported_book_uses_live_bid_not_previous_sale_minus_030(self):
        moment = datetime(2026, 8, 5, 11, 17, 12, tzinfo=SHANGHAI)
        prices = {}
        for policy in (PARENT, CANDIDATE):
            with tempfile.TemporaryDirectory() as temporary:
                engine, store = self._engine(Path(temporary) / "paper.sqlite3", policy)
                try:
                    engine._start_date(moment.date().isoformat())
                    account, lot = self._open_extra_lot(engine,
                        _tick(1, moment-timedelta(seconds=30), bid=137.440, ask=137.621))
                    sale = _tick(2, moment, bid=138.010, ask=138.198)
                    self._sell(engine, account, lot, sale)
                    engine.observed_market_trade = True
                    frame = _tick(3, moment+timedelta(seconds=3), bid=138.011, ask=138.197)
                    assessment = replace(_assessment(), reference_price=138.224,
                        reference_low=138.201, reference_high=138.300,
                        state="possible_fall", recent_buy_bonds=14_000,
                        recent_sell_bonds=39_000, short_ask_change=-0.003)
                    context = replace(_context(spread=0.186), reference_price=138.224,
                        bid_support_bonds=25_000)
                    with patch.object(engine, "_decision_context", return_value=context), \
                         patch.object(engine, "_confirmed_rise_is_recent", return_value=False):
                        engine._refresh_orders(account, frame, assessment, persist=True)
                    self.assertIsNotNone(account.buy_order)
                    prices[policy.model_id] = account.buy_order.limit_price
                    if policy is CANDIDATE:
                        # The quote is still passive and no future print has filled it.
                        self.assertLess(account.buy_order.limit_price, frame.ask1)
                        self.assertEqual(account.inventory, 1_000)
                finally:
                    store.close()
        self.assertAlmostEqual(prices[PARENT.model_id], 137.701)
        self.assertAlmostEqual(prices[CANDIDATE.model_id], 138.012)

    def test_special_lot_old_memory_and_unrelated_pending_plan_are_preserved(self):
        moment = datetime(2026, 8, 5, 11, 17, 12, tzinfo=SHANGHAI)
        for kind in ("low_bid_reversion", "sweep_tail"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                engine, store = self._engine(Path(temporary) / "paper.sqlite3", CANDIDATE)
                try:
                    engine._start_date(moment.date().isoformat())
                    account, lot = self._open_extra_lot(engine,
                        _tick(1, moment-timedelta(seconds=30), bid=137.440, ask=137.621), kind=kind)
                    account.pending_inventory_turn_quantity = 380
                    account.pending_inventory_turn_sale_value = 380 * 139
                    account.pending_support_collapse_turn_quantity = 180
                    account.pending_support_collapse_turn_sale_value = 180 * 139
                    sale = _tick(2, moment, bid=138.010, ask=138.198)
                    self._sell(engine, account, lot, sale, quantity=380)
                    self.assertEqual(account.inventory, 1_620)
                    self.assertEqual(account.pending_inventory_turn_quantity, 380)
                    self.assertEqual(account.pending_inventory_turn_sale_value, 380 * 139)
                    self.assertEqual(account.pending_support_collapse_turn_quantity, 180)
                    if kind == "sweep_tail":
                        self.assertEqual(account.last_stalled_extra_exit_price, 138.001)
                        self.assertEqual(account.last_ordinary_risk_exit_ts_ms, 0)
                    else:
                        self.assertEqual(account.last_stalled_extra_exit_price, 0)
                        self.assertEqual(account.last_ordinary_risk_exit_ts_ms, sale.market_ts_ms)
                finally:
                    store.close()

    def test_frame_guard_never_blocks_existing_customer_base_short(self):
        moment = datetime(2026, 8, 5, 11, 17, 12, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(Path(temporary) / "paper.sqlite3", CANDIDATE)
            try:
                engine._start_date(moment.date().isoformat())
                account = next(iter(engine.accounts.values()))
                tick = _tick(1, moment, bid=138.010, ask=138.198)
                account.last_ordinary_risk_exit_ts_ms = tick.market_ts_ms
                account.inventory = 0
                self.assertFalse(engine._ordinary_risk_exit_needs_new_frame(account, tick))
            finally:
                store.close()

    def test_removing_old_cap_does_not_authorize_an_unsupported_new_buy(self):
        moment = datetime(2026, 8, 5, 11, 17, 12, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(Path(temporary) / "paper.sqlite3", CANDIDATE)
            try:
                engine._start_date(moment.date().isoformat())
                account, lot = self._open_extra_lot(engine,
                    _tick(1, moment-timedelta(seconds=30), bid=137.440, ask=137.621))
                sale = _tick(2, moment, bid=138.010, ask=138.198)
                self._sell(engine, account, lot, sale)
                engine.observed_market_trade = True
                frame = replace(_tick(3, moment+timedelta(seconds=3),
                    bid=138.011, ask=138.197), bids=((138.011, 1_000), (137.800, 2_000)))
                assessment = replace(_assessment(), reference_price=138.224,
                    state="possible_fall", recent_sell_bonds=39_000,
                    recent_buy_bonds=14_000, short_ask_change=-0.003)
                context = replace(_context(spread=0.186), reference_price=138.224,
                    bid_support_bonds=1_000)
                with patch.object(engine, "_decision_context", return_value=context), \
                     patch.object(engine, "_confirmed_rise_is_recent", return_value=False):
                    engine._refresh_orders(account, frame, assessment, persist=True)
                self.assertIsNone(account.buy_order)
                self.assertEqual(account.inventory, 1_000)
            finally:
                store.close()
