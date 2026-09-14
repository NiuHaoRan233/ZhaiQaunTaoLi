from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from zhaiquant.maker import TradeEvidence
from zhaiquant.maker_paper import (
    MakerPaperEngine, REALTIME_COMPARISON_POLICIES,
    PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE as BASE,
    PRIORITY_POLICY_FIRST_POSITION_V270_R2_CANDIDATE as PREVIOUS,
    PRIORITY_POLICY_FIRST_POSITION_V271_CANDIDATE as CANDIDATE,
)
from zhaiquant.database import SQLiteStore
from zhaiquant.types import SHANGHAI
from .helpers import test_config
from .test_priority_v264_research import _tick, _assessment, _context


class PriorityV271DiscountExitTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.config = test_config(Path(self.folder.name) / 'paper.sqlite3')
        self.config = replace(self.config,
            maker_paper=replace(self.config.maker_paper, enabled=True))
        self.store = SQLiteStore(self.config)
        self.store.start_session()
        self.addCleanup(self.store.close)
        self.engine = MakerPaperEngine(self.config, self.store,
            priority_policy=CANDIDATE, fill_modes=('priority',), include_windfall=False)
        self.moment = datetime(2026, 9, 11, 9, 30, 15, tzinfo=SHANGHAI)
        self.before = replace(_tick(1, self.moment, bid=134.874, ask=135.950),
            last_price=0, previous_close=136.112,
            bids=((134.874, 2_000), (134.863, 1_000), (134.101, 8_000),
                  (134.100, 9_000), (134.000, 2_000)),
            asks=((135.950, 17_460), (136.960, 1_000), (137.8, 1_000)))
        self.engine.on_replay_tick(self.before, persist=True)
        self.account = next(a for a in self.engine.accounts.values()
                            if a.policy.model_id == CANDIDATE.model_id)
        self.assertIsNotNone(self.account.buy_order)
        self.assertAlmostEqual(self.account.buy_order.limit_price, 134.875)
        self.tick = replace(self.before, tick_id=2,
            market_ts_ms=self.before.market_ts_ms+3_000, market_time='09:30:18.000',
            last_price=134.874, trade_bonds=1_000, inferred_side='sell',
            bids=((134.874, 1_000), (134.101, 8_000), (134.100, 9_000),
                  (134.000, 2_000), (125.558, 1_000)),
            asks=((135.949, 1_020), (135.950, 17_460), (136.960, 1_000)))
        self.assessment = replace(_assessment(), reference_price=136.112,
            reference_source='previous_close', fragile_top_bid=True,
            recent_sell_bonds=1_000, recent_buy_bonds=0, state='rising')
        self.context = replace(_context(spread=1.075), reference_price=136.112,
                               reference_source='previous_close')

    def fill_old_buy(self):
        self.engine._process_resting_orders(self.account, self.tick, persist=True,
            received_ts_ns=self.tick.market_ts_ms*1_000_000)
        self.assertEqual(self.account.inventory, 2_000)
        return next(l for l in self.account.lots.values() if l.entry_price is not None)

    def risk(self, tick=None, assessment=None):
        with patch.object(self.engine, '_decision_context', return_value=self.context):
            self.engine._active_inventory_risk_exit(self.account, tick or self.tick,
                assessment or self.assessment, persist=True,
                received_ts_ns=(tick or self.tick).market_ts_ms*1_000_000)

    def test_registration_preserves_selected_baseline_and_validated_repairs(self):
        self.assertEqual(CANDIDATE, replace(PREVIOUS,
            model_id='maker_priority_v2_71_candidate', model_version='2.71-candidate',
            protect_discounted_ordinary_lot_from_fragile_bid_exit=True,
            require_strict_passive_order_timestamp=True))
        self.assertEqual(CANDIDATE.parent_model_id, BASE.model_id)
        for p in REALTIME_COMPARISON_POLICIES.values():
            if p.model_id != CANDIDATE.model_id:
                self.assertFalse(p.protect_discounted_ordinary_lot_from_fragile_bid_exit)
                self.assertFalse(p.require_strict_passive_order_timestamp)

    def test_new_passive_buy_cannot_consume_same_or_earlier_source_time(self):
        for offset in (0, -1):
            same = replace(self.tick, market_ts_ms=self.before.market_ts_ms+offset)
            self.engine._process_resting_orders(self.account, same, persist=True,
                received_ts_ns=same.market_ts_ms*1_000_000)
            self.assertEqual(self.account.inventory, 1_000)
        self.fill_old_buy()

    def test_actual_old_order_fills_and_posts_high_sell_for_later_buyer(self):
        self.engine.on_replay_tick(self.tick, persist=True)
        self.assertEqual(self.account.inventory, 2_000)
        lot = next(l for l in self.account.lots.values() if l.entry_price is not None)
        order = self.account.sell_orders[lot.db_id]
        self.assertAlmostEqual(order.limit_price, 135.948)
        # A new passive exit must not consume the same source timestamp.
        same = replace(self.tick, tick_id=3, inferred_side='buy', last_price=135.949)
        self.engine._process_resting_orders(self.account, same, persist=True,
            received_ts_ns=same.market_ts_ms*1_000_000)
        self.assertEqual(self.account.inventory, 2_000)
        later = replace(same, tick_id=4, market_ts_ms=same.market_ts_ms+120_000,
                        market_time='09:32:18.000')
        self.engine._process_resting_orders(self.account, later, persist=True,
            received_ts_ns=later.market_ts_ms*1_000_000)
        self.assertEqual(self.account.inventory, 1_000)
        self.assertEqual(lot.remaining_quantity, 0)

    def test_same_value_protection_applies_to_older_lot_not_only_fill_frame(self):
        lot = self.fill_old_buy()
        lot.opened_ms -= 60_000
        self.risk()
        self.assertEqual(self.account.inventory, 2_000)

    def test_narrow_offer_keeps_old_exit_even_when_reference_is_high(self):
        self.fill_old_buy()
        self.risk(replace(self.tick, asks=((135.0, 10_000), (135.001, 10_000))))
        self.assertEqual(self.account.inventory, 1_000)

    def test_high_offer_alone_without_value_discount_does_not_protect(self):
        self.fill_old_buy()
        self.context = replace(self.context, reference_price=135.0)
        self.risk()
        self.assertEqual(self.account.inventory, 1_000)

    def test_current_midpoint_cannot_self_confirm_a_wide_offer(self):
        self.fill_old_buy()
        self.context = replace(self.context, reference_source='intraday_current_midpoint_reset')
        self.risk()
        self.assertEqual(self.account.inventory, 1_000)

    def test_previous_close_not_resurrected_in_afternoon(self):
        self.fill_old_buy()
        self.risk(replace(self.tick, market_time='13:10:00.000'))
        self.assertEqual(self.account.inventory, 1_000)

    def test_current_intraday_reference_can_protect_later_in_day(self):
        self.fill_old_buy()
        self.context = replace(self.context, reference_source='carried_intraday_reference')
        self.risk(replace(self.tick, market_time='13:10:00.000'))
        self.assertEqual(self.account.inventory, 2_000)

    def test_stale_or_future_prior_quote_is_not_normal_offer_evidence(self):
        self.fill_old_buy()
        self.account.last_market_ts_ms = self.tick.market_ts_ms-61_000
        self.risk()
        self.assertEqual(self.account.inventory, 1_000)

    def test_fresh_tiny_high_offer_is_not_normal_supply(self):
        self.fill_old_buy()
        self.risk(replace(self.tick, asks=((135.950, 10), (137.0, 10_000))))
        self.assertEqual(self.account.inventory, 1_000)

    def test_offer_repricing_cancels_protection_despite_remaining_wide_space(self):
        self.fill_old_buy()
        self.risk(replace(self.tick, asks=((135.7, 10_000), (135.701, 10_000))))
        self.assertEqual(self.account.inventory, 1_000)

    def test_large_real_sell_is_independent_evidence(self):
        self.fill_old_buy()
        self.risk(replace(self.tick, trade_bonds=3_000))
        self.assertEqual(self.account.inventory, 1_000)

    def test_persistent_low_selling_cancels_protection(self):
        self.fill_old_buy()
        for i in range(5):
            self.engine.analyzer.trade_evidence.append(TradeEvidence(
                self.tick.market_ts_ms-i*3_000, 134.874, 1_000, 1, 'sell'))
        self.risk()
        self.assertEqual(self.account.inventory, 1_000)

    def test_old_or_future_sell_evidence_does_not_block_present_value(self):
        self.fill_old_buy()
        for ts in (self.tick.market_ts_ms-31_000, self.tick.market_ts_ms+1):
            self.engine.analyzer.trade_evidence.append(TradeEvidence(ts,134.874,10_000,1,'sell'))
        self.risk()
        self.assertEqual(self.account.inventory, 2_000)

    def test_independent_bearish_vacuum_keeps_exit(self):
        self.fill_old_buy()
        self.risk(assessment=replace(self.assessment, downside_book_vacuum=True,
                                     short_ask_change=-.2, recent_sell_bonds=5_000))
        self.assertEqual(self.account.inventory, 1_000)

    def test_special_lots_keep_their_native_risk_rules(self):
        lot = self.fill_old_buy()
        lot.kind = 'deep_discount_sweep'
        self.risk()
        self.assertEqual(self.account.inventory, 1_000)

    def test_protection_releases_on_new_deterioration_without_a_timer(self):
        self.fill_old_buy()
        self.risk()
        self.assertEqual(self.account.inventory, 2_000)
        later = replace(self.tick, tick_id=3, market_ts_ms=self.tick.market_ts_ms+3_000,
                        asks=((135.0, 10_000), (135.001, 10_000)))
        self.risk(later)
        self.assertEqual(self.account.inventory, 1_000)

    def test_previous_registered_model_still_sells_same_fill(self):
        self.fill_old_buy()
        self.account.policy = PREVIOUS
        self.risk()
        self.assertEqual(self.account.inventory, 1_000)

    def test_no_new_permission_to_sell_customer_base(self):
        self.account.buy_order = None
        self.risk()
        self.assertEqual(self.account.inventory, 1_000)
