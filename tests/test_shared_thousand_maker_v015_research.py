from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from zhaiquant.maker import TradeEvidence
from zhaiquant.maker_paper import (
    SHARED_THOUSAND_POLICY_V014_CANDIDATE as PARENT,
    SHARED_THOUSAND_POLICY_V015_CANDIDATE as CHILD,
    SharedCapitalPaperRuntime,
)
from zhaiquant.shared_thousand_maker_v013_research import score_buy_intent_v013
from zhaiquant.shared_thousand_maker_v015_research import (
    AllocationParametersV015, SharedThousandV015Allocator, liquidity_score_v015,
)
from zhaiquant.types import SHANGHAI
from . import test_shared_thousand_maker_v011_research as v011
from .test_shared_thousand_maker_v011_research import _tick


class SharedThousandMakerV015Tests(unittest.TestCase):
    _engine = v011.SharedThousandMakerV011Tests._engine

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.engine, self.store = self._engine(Path(temporary.name)/'test.sqlite3', CHILD)
        self.addCleanup(self.store.close)
        self.tick = _tick(1, datetime(2026, 9, 7, 10, 0, tzinfo=SHANGHAI), bid=136.2, ask=136.8)
        self.engine._start_date(self.tick.market_date)
        self.account = next(iter(self.engine.accounts.values()))
        self.p = AllocationParametersV015()
        self.order = self.engine._new_order(self.account, self.tick, side='buy',
            kind='low_bid_reversion', lot_id=None, price=136.201, quantity=1000,
            target_price=136.799, price_boundary=136.201, queue_ahead=0, persist=True)
        self.base = score_buy_intent_v013(bond_code=self.tick.code, engine=self.engine,
            account=self.account, order=self.order, tick=self.tick, parameters=self.p,
            active_fill=False)
        self.base = replace(self.base, exit_probability_without_session=0.4,
            exit_probability_proxy=0.8, session_exit_probability_bonus=0.4,
            expected_lock_seconds=375, entry_probability_proxy=0.8)
        self.allocator = SharedThousandV015Allocator({self.tick.code: self.engine},
            initial_cash_cny=140000, capital_ready_ts_ms=self.tick.market_ts_ms)
        self.allocator.last_bond_ticks[self.tick.code] = self.tick

    def add_buy(self, age=0, quantity=1000, price=136.8, side='buy'):
        self.engine.analyzer.trade_evidence.append(TradeEvidence(
            self.tick.market_ts_ms-age*1000, price, quantity, 1, side))

    def score(self, quantity=1000, **kwargs):
        return liquidity_score_v015(score=kwargs.pop('base', self.base), engine=self.engine,
            tick=kwargs.pop('tick', self.tick), parameters=self.p,
            quantity=quantity, shared_capacity_bonds=1000, **kwargs)

    def choose_score(self, value=100, quantity=1000):
        score = self.score(quantity=quantity)
        return replace(score, capital_time_score_cny=value, pre_waiting_score_cny=value)

    def test_new_identity_native_profile_identical_and_offline(self):
        diff = {k for k, v in asdict(CHILD).items() if v != asdict(PARENT)[k]}
        self.assertEqual(diff, {'model_id', 'model_version', 'parent_model_id'})
        self.assertEqual(CHILD.parent_model_id, PARENT.model_id)
        with self.assertRaisesRegex(ValueError, 'unsupported shared-capital realtime model'):
            SharedCapitalPaperRuntime(self.engine.config, self.store, policy=CHILD)

    def test_no_current_buys_retains_background_but_not_fast_exit(self):
        result = self.score()
        self.assertEqual(result.session_exit_freshness_factor, 0.25)
        self.assertAlmostEqual(result.session_exit_probability_bonus, 0.1)
        self.assertEqual(result.expected_lock_seconds, 1800)
        self.assertLess(result.exit_probability_proxy, self.base.exit_probability_proxy)

    def test_real_current_buy_improves_exit_and_occupation(self):
        old = self.score()
        self.add_buy(quantity=10000)
        new = self.score()
        self.assertGreater(new.exit_probability_proxy, old.exit_probability_proxy)
        self.assertLess(new.expected_lock_seconds, old.expected_lock_seconds)
        self.assertLessEqual(new.exit_probability_proxy, self.base.exit_probability_proxy)
        self.assertGreater(new.score_cny, 0)

    def test_half_life_and_window_boundaries(self):
        self.add_buy(age=150, quantity=1000)
        self.assertAlmostEqual(self.score().weighted_exit_buy_bonds, 500)
        self.add_buy(age=300, quantity=1000)
        self.add_buy(age=301, quantity=100000)
        self.assertAlmostEqual(self.score().weighted_exit_buy_bonds, 750)

    def test_future_unknown_sell_and_low_buys_do_not_help(self):
        before = self.score()
        for args in (dict(age=-1), dict(side='unknown'), dict(side='sell'), dict(price=136.2)):
            self.add_buy(quantity=100000, **args)
        self.assertEqual(self.score(), before)

    def test_full_post_sweep_price_still_uses_remaining_book(self):
        tick = replace(self.tick, asks=((136.201, 20), (136.8, 1000)))
        order = replace(self.order, kind='deep_discount_sweep', quantity=20)
        base = score_buy_intent_v013(bond_code=tick.code, engine=self.engine,
            account=self.account, order=order, tick=tick, parameters=self.p,
            active_fill=True, fill_quantity=20)
        result = self.score(quantity=20, base=base, tick=tick)
        self.assertAlmostEqual(result.exit_price, 136.799)
        self.assertAlmostEqual(result.exit_evidence_floor_v015, 136.749)

    def test_near_close_penalty_not_removed(self):
        normal = self.score()
        late = self.score(base=replace(self.base, remaining_session_seconds=30))
        self.assertLess(late.terminal_time_factor, normal.terminal_time_factor)
        self.assertGreater(late.expected_downside_per_bond, normal.expected_downside_per_bond)

    def test_no_peer_evidence_does_not_ban_small_positive_trade(self):
        scores = {self.tick.code: self.choose_score(3, 20)}
        winner, _ = self.allocator._choose(scores, market_ts_ms=self.tick.market_ts_ms)
        self.assertEqual(winner, self.tick.code)
        self.assertEqual(scores[winner].slot_waiting_cost_cny, 0)

    def test_recent_peer_opportunity_rejects_marginal_small_entry(self):
        self.allocator.waiting_opportunities = {'peer': {self.tick.market_ts_ms: 50}}
        scores = {self.tick.code: self.choose_score(3, 20)}
        winner, _ = self.allocator._choose(scores, market_ts_ms=self.tick.market_ts_ms)
        self.assertIsNone(winner)
        self.assertAlmostEqual(scores[self.tick.code].slot_waiting_cost_cny, 49)

    def test_good_small_entry_can_pay_slot_cost(self):
        self.allocator.waiting_opportunities = {'peer': {self.tick.market_ts_ms: 50}}
        scores = {self.tick.code: self.choose_score(80, 20)}
        winner, _ = self.allocator._choose(scores, market_ts_ms=self.tick.market_ts_ms)
        self.assertEqual(winner, self.tick.code)

    def test_full_slot_not_charged_unused_capacity(self):
        self.allocator.waiting_opportunities = {'peer': {self.tick.market_ts_ms: 500}}
        scores = {self.tick.code: self.choose_score(10)}
        self.allocator._choose(scores, market_ts_ms=self.tick.market_ts_ms)
        self.assertEqual(scores[self.tick.code].slot_waiting_cost_cny, 0)

    def test_held_topup_not_charged_again(self):
        self.account.inventory = 20
        self.allocator.waiting_opportunities = {'peer': {self.tick.market_ts_ms: 500}}
        scores = {self.tick.code: self.choose_score(10, 20)}
        self.allocator._choose(scores, market_ts_ms=self.tick.market_ts_ms)
        self.assertEqual(scores[self.tick.code].slot_waiting_cost_cny, 0)

    def test_waiting_memory_causal_expiring_and_decaying(self):
        now = self.tick.market_ts_ms
        self.allocator.waiting_opportunities = {'peer': {
            now-150000: 50, now-301000: 50000, now+1000: 50000}, self.tick.code: {now: 99999}}
        self.assertEqual(self.allocator._peer_waiting_value(self.tick.code, now), 25)
        self.assertEqual(self.allocator._peer_waiting_value(self.tick.code, now+601000), 0)

    def test_repeated_choice_does_not_double_charge(self):
        self.allocator.waiting_opportunities = {'peer': {self.tick.market_ts_ms: 50}}
        scores = {self.tick.code: self.choose_score(80, 20)}
        self.allocator._choose(scores, market_ts_ms=self.tick.market_ts_ms)
        first = scores[self.tick.code]
        self.allocator._choose(scores, market_ts_ms=self.tick.market_ts_ms)
        self.assertEqual(scores[self.tick.code], first)

    def test_partial_fill_scoring_uses_actual_quantity_without_mutating_order(self):
        self.add_buy(quantity=10000)
        before = asdict(self.order)
        key = (self.tick.code, self.order.db_id)
        self.allocator._active_fill_quantities[key] = 20
        score = self.allocator._score(self.tick.code, self.order, active_fill=False)
        self.assertEqual(score.remaining_bonds, 20)
        self.assertEqual(asdict(self.order), before)
        self.assertNotIn(self.tick.code, self.allocator.shadow_scores)
        self.assertNotIn(self.tick.code, self.allocator.waiting_opportunities)

    def test_shadow_is_adjusted_and_does_not_reteach_waiting_memory(self):
        self.account.buy_order = self.order
        self.allocator._resting_scores()
        memory = dict(self.allocator.waiting_opportunities[self.tick.code])
        self.account.buy_order = None
        scores = self.allocator._resting_scores()
        self.assertTrue(scores[self.tick.code].shadow_intent)
        self.assertEqual(self.allocator.waiting_opportunities[self.tick.code], memory)
        self.allocator._choose(scores, market_ts_ms=self.tick.market_ts_ms)

    def test_holding_remains_locked_without_choose_or_sale(self):
        self.account.inventory = 20
        with patch.object(self.allocator, '_choose', side_effect=AssertionError('must stay locked')):
            self.allocator._reconcile(self.tick)
        self.assertEqual(self.allocator.selected_code, self.tick.code)
        self.assertEqual(self.account.inventory, 20)

    def test_cash_guard_still_rejects_and_does_not_charge(self):
        self.allocator.shared_cash_cny = 0
        allowed = self.allocator._allow_buy_fill(self.tick.code, self.account, self.tick,
            self.order, 1000, self.order.kind, 'passive_buy')
        self.assertFalse(allowed)
        self.assertEqual(self.allocator.shared_cash_cny, 0)


if __name__ == '__main__':
    unittest.main()
