from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from zhaiquant.maker_paper import (
    PRIORITY_POLICY_FIRST_POSITION_V265_CANDIDATE as PARENT,
    PRIORITY_POLICY_FIRST_POSITION_V266_CANDIDATE as STRICT,
    PRIORITY_POLICY_FIRST_POSITION_V266_R2_CANDIDATE as STRESS,
    PRIORITY_POLICY_FIRST_POSITION_V266_R3_CANDIDATE as REENTRY,
)
from zhaiquant.types import SHANGHAI
from . import test_priority_v264_research as helpers
from .test_priority_v264_research import _tick, _assessment, _context


class PriorityV266ResearchTests(unittest.TestCase):
    _engine = helpers.PriorityV264ResearchTests._engine

    def test_registered_hypotheses_only_change_their_named_mode(self):
        for policy, mode in ((STRICT, 'all_repricing'), (STRESS, 'edge_stress'), (REENTRY, 'reentry_repricing')):
            self.assertEqual(policy, replace(PARENT,
                model_id=policy.model_id, model_version=policy.model_version,
                parent_model_id=PARENT.model_id, ordinary_entry_sell_pressure_mode=mode))
        self.assertEqual(PARENT.ordinary_entry_sell_pressure_mode, 'off')

    def test_current_space_survives_pressure_only_in_stress_variant(self):
        moment = datetime(2026, 8, 14, 14, 50, 59, tzinfo=SHANGHAI)
        for policy in (PARENT, STRICT, STRESS):
            with self.subTest(policy=policy.model_id), tempfile.TemporaryDirectory() as tmp:
                engine, store = self._engine(Path(tmp)/'paper.sqlite3', policy)
                try:
                    engine._start_date(moment.date().isoformat())
                    account = next(iter(engine.accounts.values()))
                    assessment = replace(_assessment(), state='falling',
                        short_ask_change=-0.30, recent_sell_bonds=46_000,
                        recent_buy_bonds=2_190)
                    for ask, expected in ((137.5, policy is not PARENT),
                                          (138.0, policy is STRICT)):
                        tick = _tick(1, moment, bid=137.3, ask=ask)
                        with patch.object(engine, '_confirmed_rise_is_recent', return_value=False), \
                             patch.object(engine, '_live_priority_extra_inventory_isolated_offer_price', return_value=None):
                            result = engine._ordinary_entry_sell_pressure_blocks(account,
                                tick, assessment, _context(spread=ask-137.3),
                                kind='low_bid_reversion', price=137.301, quantity=1_000)
                        self.assertEqual(result, expected)
                finally:
                    store.close()

    def test_reentry_identity_ends_on_actual_new_extra_buy_and_ignores_first_entry(self):
        moment = datetime(2026, 8, 14, 14, 50, 59, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as tmp:
            engine, store = self._engine(Path(tmp)/'paper.sqlite3', REENTRY)
            try:
                engine._start_date(moment.date().isoformat())
                account = next(iter(engine.accounts.values()))
                tick = _tick(1, moment, bid=137.3, ask=137.5)
                bad = replace(_assessment(), short_ask_change=-.30,
                    recent_sell_bonds=46_000, recent_buy_bonds=2_190)
                def blocked():
                    return engine._ordinary_entry_sell_pressure_blocks(account, tick, bad,
                        _context(spread=.2), kind='low_bid_reversion', price=137.301, quantity=1_000)
                with patch.object(engine, '_confirmed_rise_is_recent', return_value=False), \
                     patch.object(engine, '_live_priority_extra_inventory_isolated_offer_price', return_value=None):
                    self.assertFalse(blocked())
                    account.last_ordinary_risk_exit_ts_ms = tick.market_ts_ms-1
                    self.assertTrue(blocked())
                    helpers.PriorityV264ResearchTests._open_extra_lot(engine, tick)
                    self.assertEqual(account.last_new_extra_entry_ts_ms, tick.market_ts_ms)
                    self.assertFalse(blocked())
                    account.last_ordinary_risk_exit_ts_ms = tick.market_ts_ms+1
                    self.assertTrue(blocked())
            finally:
                store.close()

    def test_recovery_or_insufficient_pressure_restores_parent_permission_now(self):
        moment = datetime(2026, 8, 14, 14, 50, 59, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as tmp:
            engine, store = self._engine(Path(tmp)/'paper.sqlite3', STRESS)
            try:
                engine._start_date(moment.date().isoformat())
                account = next(iter(engine.accounts.values()))
                account.last_ordinary_risk_exit_ts_ms = int(moment.timestamp()*1000)-1
                tick = _tick(1, moment, bid=137.3, ask=137.5)
                bad = replace(_assessment(), short_ask_change=-0.30,
                    recent_sell_bonds=46_000, recent_buy_bonds=2_190)
                cases = [(replace(bad, short_ask_change=0), False),
                         (replace(bad, recent_sell_bonds=500), False),
                         (replace(bad, recent_buy_bonds=46_000), False), (bad, True)]
                for assessment, rising in cases:
                    with patch.object(engine, '_confirmed_rise_is_recent', return_value=rising):
                        self.assertFalse(engine._ordinary_entry_sell_pressure_blocks(account,
                            tick, assessment, _context(spread=.2),
                            kind='low_bid_reversion', price=137.301, quantity=1_000))
            finally:
                store.close()

    def test_special_entries_base_short_pending_and_isolated_offer_are_exempt(self):
        moment = datetime(2026, 8, 14, 14, 50, 59, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as tmp:
            engine, store = self._engine(Path(tmp)/'paper.sqlite3', STRESS)
            try:
                engine._start_date(moment.date().isoformat())
                account = next(iter(engine.accounts.values()))
                tick = _tick(1, moment, bid=137.3, ask=137.5)
                bad = replace(_assessment(), short_ask_change=-0.30,
                    recent_sell_bonds=46_000, recent_buy_bonds=2_190)
                def blocked(kind='low_bid_reversion'):
                    return engine._ordinary_entry_sell_pressure_blocks(account, tick,
                        bad, _context(spread=.2), kind=kind, price=137.301, quantity=1_000)
                for kind in ('deep_discount_sweep', 'sweep_tail', 'joint_causal_corridor_entry',
                             'support_collapse_capacity_redeploy_entry', 'dynamic_customer_base_replenish'):
                    self.assertFalse(blocked(kind))
                account.inventory = 500
                self.assertFalse(blocked())
                account.inventory = 1_000
                account.pending_inventory_turn_quantity = 190
                self.assertFalse(blocked())
                account.pending_inventory_turn_quantity = 0
                with patch.object(engine, '_confirmed_rise_is_recent', return_value=False), \
                     patch.object(engine, '_live_priority_extra_inventory_isolated_offer_price', return_value=138.0):
                    self.assertFalse(blocked())
            finally:
                store.close()

    def test_refresh_cancels_a_preexisting_ordinary_bid_under_current_pressure(self):
        moment = datetime(2026, 8, 14, 14, 50, 59, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as tmp:
            engine, store = self._engine(Path(tmp)/'paper.sqlite3', STRESS)
            try:
                engine._start_date(moment.date().isoformat())
                engine.observed_market_trade = True
                account = next(iter(engine.accounts.values()))
                tick = _tick(1, moment, bid=137.3, ask=137.6)
                context = replace(_context(spread=.3), reference_price=137.8,
                                  bid_support_bonds=25_000)
                stable = replace(_assessment(), reference_price=137.8)
                with patch.object(engine, '_decision_context', return_value=context), \
                     patch.object(engine, '_confirmed_rise_is_recent', return_value=False):
                    engine._refresh_orders(account, tick, stable, persist=True)
                    self.assertIsNotNone(account.buy_order)
                    old_id = account.buy_order.db_id
                    bad = replace(stable, state='falling', short_ask_change=-.3,
                        recent_sell_bonds=46_000, recent_buy_bonds=2_190)
                    engine._refresh_orders(account, replace(tick, market_ts_ms=tick.market_ts_ms+1),
                                           bad, persist=True)
                    self.assertIsNone(account.buy_order)
                    row = store.connection.execute('SELECT status,cancel_reason FROM maker_paper_orders WHERE id=?',
                                                   (old_id,)).fetchone()
                    self.assertEqual(tuple(row), ('cancelled', 'ordinary_entry_sell_pressure'))
                    engine._refresh_orders(account, replace(tick, market_ts_ms=tick.market_ts_ms+2),
                                           stable, persist=True)
                    self.assertIsNotNone(account.buy_order)
                    self.assertEqual(account.inventory, 1_000)
            finally:
                store.close()
