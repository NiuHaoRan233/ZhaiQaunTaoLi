from __future__ import annotations

from dataclasses import asdict, replace
from datetime import timedelta
from unittest.mock import patch
from unittest.mock import Mock

from zhaiquant.maker_paper import (
    PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE as BASE,
    PRIORITY_POLICY_FIRST_POSITION_V269_CANDIDATE as PREVIOUS,
    PRIORITY_POLICY_FIRST_POSITION_V270_CANDIDATE as FIRST,
    PRIORITY_POLICY_FIRST_POSITION_V270_R2_CANDIDATE as CANDIDATE,
)
from . import test_priority_v269_rebuild as cases
from .test_priority_v264_research import _tick, _assessment, _context


class PriorityV270RecoveryTests(cases.PriorityV269RebuildTests):
    policy = CANDIDATE

    def test_registered_child_keeps_the_parent_profile(self):
        self.assertEqual(CANDIDATE, replace(BASE,
            model_id='maker_priority_v2_70_candidate_r2', model_version='2.70-candidate-r2',
            parent_model_id=BASE.model_id, enable_causal_ordinary_inventory_turnover=True,
            recognize_consumed_recovered_support=True, quote_on_current_ordinary_recovery=True,
            allow_horizontal_recovery_quote_with_bid_retreat=True))
        self.assertEqual(FIRST, replace(CANDIDATE,
            model_id='maker_priority_v2_70_candidate', model_version='2.70-candidate',
            allow_horizontal_recovery_quote_with_bid_retreat=False))

    def test_no_unapproved_intermediate_profile_changes_are_inherited(self):
        base, current = asdict(BASE), asdict(CANDIDATE)
        self.assertEqual({k for k in base if base[k] != current[k]}, {
            'model_id', 'model_version', 'parent_model_id',
            'enable_causal_ordinary_inventory_turnover', 'recognize_consumed_recovered_support',
            'quote_on_current_ordinary_recovery', 'allow_horizontal_recovery_quote_with_bid_retreat'})
        self.assertFalse(CANDIDATE.normalize_native_priority_price_grid)
        for policy in (BASE, PREVIOUS):
            self.assertFalse(policy.recognize_consumed_recovered_support)
            self.assertFalse(policy.quote_on_current_ordinary_recovery)

    def restored_support(self):
        tick = replace(_tick(3, self.moment+timedelta(seconds=6), bid=137.700, ask=137.995),
                       bids=((137.700, 2_000), (137.501, 9_000), (137.500, 10_000)))
        self.engine._mark_account(self.account, tick, persist=False)
        self.account.ordinary_tape_recovery_ts_ms = tick.market_ts_ms-3_000
        self.account.ordinary_tape_recovery_bid = 137.701
        self.lot.entry_price = 137.702
        return replace(_tick(4, self.moment+timedelta(seconds=9), bid=137.501, ask=137.701),
                       trade_bonds=2_000, inferred_side='sell', last_price=137.700)

    def test_complete_real_consumption_rearms_without_five_thousand(self):
        tick = self.restored_support()
        self.refresh(tick, replace(_assessment(), recent_buy_bonds=100_000,
                                  recent_sell_bonds=3_000, short_ask_change=-.294))
        self.assertTrue(self.account.ordinary_tape_pressure_active)
        self.assertEqual(self.account.ordinary_tape_pressure_since_ms, tick.market_ts_ms)
        self.assertAlmostEqual(self.account.sell_orders[self.lot.db_id].limit_price, 137.700)
        self.assertEqual(self.account.inventory, 2_000)

    def test_cancellation_small_or_misattributed_print_is_not_consumption(self):
        tick = self.restored_support()
        for index, modified in enumerate((
            replace(tick, trade_bonds=0, inferred_side='none'),
            replace(tick, trade_bonds=520),
            replace(tick, trade_bonds=1_000),
            replace(tick, last_price=137.5),
            replace(tick, inferred_side='buy'),
            replace(tick, asks=((137.995, 2_000),)),
            replace(tick, bids=((137.699, 1_000), (137.501, 9_000))),
        )):
            with self.subTest(index=index):
                self.engine._update_ordinary_tape_turnover_regime(
                    self.account, replace(modified, tick_id=10+index), _assessment())
                self.assertFalse(self.account.ordinary_tape_pressure_active)

    def test_recovery_must_be_fresh_local_and_before_this_frame(self):
        tick = self.restored_support()
        for index, (timestamp, bid) in enumerate((
            (0, 137.701), (tick.market_ts_ms-301_000, 137.701),
            (tick.market_ts_ms, 137.701), (tick.market_ts_ms-3_000, 138.000),
        )):
            self.account.ordinary_tape_recovery_ts_ms = timestamp
            self.account.ordinary_tape_recovery_bid = bid
            self.engine._update_ordinary_tape_turnover_regime(
                self.account, replace(tick, tick_id=index+10), _assessment())
            self.assertFalse(self.account.ordinary_tape_pressure_active)

    def test_new_consumption_rule_does_not_claim_neutral_or_special_inventory(self):
        tick = self.restored_support()
        self.account.inventory = 1_000
        self.engine._update_ordinary_tape_turnover_regime(self.account, tick, _assessment())
        self.assertFalse(self.account.ordinary_tape_pressure_active)
        self.account.inventory = 2_000
        self.lot.kind = 'deep_discount_sweep'
        self.engine._update_ordinary_tape_turnover_regime(
            self.account, replace(tick, tick_id=5), _assessment())
        self.assertFalse(self.account.ordinary_tape_pressure_active)

    def recovery_exit_frame(self, horizontal=False):
        before = _tick(2, self.moment+timedelta(seconds=3),
                       bid=137.299 if horizontal else 137.200, ask=137.399)
        self.engine._mark_account(self.account, before, persist=False)
        tick = replace(_tick(3, self.moment+timedelta(seconds=6),
                             bid=137.199 if horizontal else 137.200,
                             ask=137.399 if horizontal else 137.600),
                       trade_bonds=1_000, inferred_side='buy', last_price=137.399)
        order = self.engine._new_order(self.account, before, side='sell',
            kind='full_inventory_capacity_release_exit', lot_id=self.lot.db_id,
            price=137.398, quantity=1_000, queue_ahead=0, target_price=None,
            price_boundary=137.398, persist=True)
        self.account.sell_orders[self.lot.db_id] = order
        self.engine._fill_sell(self.account, tick, order, 1_000,
                              tick.market_ts_ms*1_000_000, persist=True)
        self.add_trade(tick, tick.last_price, 1_000)
        return tick

    def quote_recovery(self, tick):
        with patch.object(self.engine, '_decision_context',
                          return_value=_context(spread=tick.ask1-tick.bid1)):
            self.engine._refresh_orders(self.account, tick, _assessment(), persist=True)

    def test_offer_clear_posts_now_but_cannot_fill_at_creation_timestamp(self):
        tick = self.recovery_exit_frame()
        self.quote_recovery(tick)
        self.assertIsNotNone(self.account.buy_order)
        order = self.account.buy_order
        self.assertEqual(order.kind, 'low_bid_reversion')
        self.assertEqual(order.created_ms, tick.market_ts_ms)
        self.assertEqual(self.account.inventory, 1_000)
        # New callback ID with same market timestamp is not a future fill.
        sell = replace(tick, tick_id=4, inferred_side='sell', last_price=tick.bid1)
        self.engine._process_resting_orders(self.account, sell, persist=True,
                                           received_ts_ns=sell.market_ts_ms*1_000_000)
        self.assertEqual(self.account.inventory, 1_000)
        later = replace(sell, tick_id=5, market_ts_ms=sell.market_ts_ms+3_000)
        self.engine._process_resting_orders(self.account, later, persist=True,
                                           received_ts_ns=later.market_ts_ms*1_000_000)
        self.assertEqual(self.account.inventory, 2_000)

    def test_horizontal_current_buy_can_quote_despite_bid_retreat(self):
        tick = self.recovery_exit_frame(horizontal=True)
        self.quote_recovery(tick)
        self.assertEqual(self.account.ordinary_tape_recovery_ts_ms, tick.market_ts_ms)
        self.assertIsNotNone(self.account.buy_order)
        self.assertAlmostEqual(self.account.buy_order.limit_price, 137.200)

    def test_current_quote_exception_never_allows_active_same_frame_reentry(self):
        tick = self.recovery_exit_frame()
        self.quote_recovery(tick)
        self.assertTrue(self.engine._ordinary_recovery_allows_passive_quote(self.account, tick))
        self.assertTrue(self.engine._ordinary_risk_exit_needs_new_frame(self.account, tick))
        self.engine._active_discount_entry(self.account, tick, _assessment(), persist=True)
        self.engine._active_sweep(self.account, tick, Mock(), persist=True)
        self.assertEqual(self.account.inventory, 1_000)

    def test_no_quote_exception_for_old_recovery_no_trade_shock_or_pending(self):
        tick = self.recovery_exit_frame()
        self.quote_recovery(tick)
        for modified in (replace(tick, trade_bonds=0), replace(tick, trade_bonds=520),
                         replace(tick, inferred_side='sell'),
                         replace(tick, bids=((137.0, 5_000),), asks=((137.2, 5_000),))):
            self.assertFalse(self.engine._ordinary_recovery_allows_passive_quote(self.account, modified))
        self.account.ordinary_tape_recovery_ts_ms -= 3_000
        self.assertFalse(self.engine._ordinary_recovery_allows_passive_quote(self.account, tick))
        self.account.ordinary_tape_recovery_ts_ms = tick.market_ts_ms
        self.account.pending_inventory_turn_quantity = 1_000
        self.assertFalse(self.engine._ordinary_recovery_allows_passive_quote(self.account, tick))

    def test_recovery_does_not_bypass_parent_entry_legality(self):
        tick = self.recovery_exit_frame()
        with patch.object(self.engine, '_decision_context', return_value=replace(
                _context(spread=.4), reference_price=0)):
            self.engine._refresh_orders(self.account, tick, _assessment(), persist=True)
        self.assertIsNone(self.account.buy_order)

    def test_quote_exception_does_not_extend_to_special_redeployment(self):
        tick = self.recovery_exit_frame()
        with patch.object(self.engine, '_support_collapse_capacity_redeploy_price',
                          return_value=(137.2, 137.2)):
            self.quote_recovery(tick)
        self.assertIsNone(self.account.buy_order)

    def test_only_validated_recovery_revision_joins_the_live_matrix(self):
        from zhaiquant.config import load_config
        from zhaiquant.maker_dashboard import _model_display_name
        config = load_config('config.example.toml')
        self.assertIn(CANDIDATE.model_id, config.maker_paper.realtime_comparison_model_ids)
        self.assertNotIn(FIRST.model_id, config.maker_paper.realtime_comparison_model_ids)
        self.assertIn(BASE.model_id, config.maker_paper.realtime_comparison_model_ids)
        self.assertEqual(_model_display_name({'model_id': CANDIDATE.model_id}),
                         '第一顺位2.70')
