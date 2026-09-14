from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from zhaiquant.maker_paper import (
    PRIORITY_POLICY_FIRST_POSITION_V267_CANDIDATE as PARENT,
    PRIORITY_POLICY_FIRST_POSITION_V268_CANDIDATE as CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V268_R2_CANDIDATE as REVISION,
    PRIORITY_POLICY_FIRST_POSITION_V268_R3_CANDIDATE as WIDE_REVISION,
)
from zhaiquant.maker import TradeEvidence
from zhaiquant.types import SHANGHAI
from . import test_priority_v264_research as helpers
from .test_priority_v264_research import _tick, _assessment, _context


class PriorityV268ResearchTests(unittest.TestCase):
    policy = CANDIDATE

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.engine, self.store = helpers.PriorityV264ResearchTests._engine(
            self, Path(self.temporary.name)/'paper.sqlite3', self.policy)
        self.moment = datetime(2026, 9, 7, 10, 21, 1, tzinfo=SHANGHAI)
        self.engine._start_date(self.moment.date().isoformat())
        self.initial = _tick(1, self.moment, bid=137.440, ask=137.621)
        self.account, self.lot = helpers.PriorityV264ResearchTests._open_extra_lot(
            self.engine, self.initial)
        self.engine._mark_account(self.account, self.initial, persist=False)

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def shock(self, tick_id=2):
        return replace(_tick(tick_id, self.moment+timedelta(seconds=3),
                             bid=137.300, ask=137.400),
                       trade_bonds=10_000, inferred_side='sell', last_price=137.400)

    def refresh(self, tick, assessment=None):
        with patch.object(self.engine, '_decision_context',
                          return_value=_context(spread=tick.ask1-tick.bid1)):
            self.engine._refresh_orders(self.account, tick,
                                        assessment or _assessment(), persist=True)
        self.engine._mark_account(self.account, tick, persist=False)

    def test_registered_child_keeps_the_parent_profile(self):
        self.assertEqual(CANDIDATE, replace(PARENT,
            model_id='maker_priority_v2_68_candidate', model_version='2.68-candidate',
            parent_model_id=PARENT.model_id, enable_ordinary_tape_turnover_regime=True))

    def test_new_sell_impulse_is_not_hidden_by_old_buy_volume(self):
        self.refresh(self.shock(), replace(_assessment(), state='falling',
            recent_buy_bonds=100_000, recent_sell_bonds=22_000, short_ask_change=-.22))
        self.assertTrue(self.account.ordinary_tape_pressure_active)
        order=self.account.sell_orders[self.lot.db_id]
        self.assertEqual(order.kind, 'live_priority_extra_inventory_exit')
        self.assertAlmostEqual(order.limit_price, 137.399)
        self.assertEqual(order.remaining, 1_000)
        self.assertEqual(self.account.inventory, 2_000)

    def test_stalled_quotes_do_not_end_exit_and_loss_does_not_cancel_it(self):
        self.refresh(self.shock())
        lower=_tick(3,self.moment+timedelta(seconds=40),bid=136.900,ask=137.000)
        self.refresh(lower)
        order=self.account.sell_orders[self.lot.db_id]
        self.assertAlmostEqual(order.limit_price, 136.999)
        quiet=replace(lower,tick_id=4,market_ts_ms=lower.market_ts_ms+90_000,
                      market_time='10:23:11.000')
        self.refresh(quiet,replace(_assessment(),short_ask_change=0,
                                  recent_buy_bonds=0,recent_sell_bonds=0))
        self.assertEqual(self.account.sell_orders[self.lot.db_id].db_id,order.db_id)

    def test_quote_withdrawal_alone_does_not_restore_demand(self):
        self.refresh(self.shock())
        recovered_quote=_tick(3,self.moment+timedelta(seconds=6),bid=137.400,ask=137.600)
        self.refresh(recovered_quote)
        self.assertTrue(self.account.ordinary_tape_pressure_active)

    def test_real_offer_attack_restores_now_and_old_pressure_does_not_rearm(self):
        self.refresh(self.shock())
        recovery=replace(_tick(3,self.moment+timedelta(seconds=6),bid=137.400,ask=137.600),
                         trade_bonds=1_000,inferred_side='buy',last_price=137.400)
        self.refresh(recovery)
        self.assertFalse(self.account.ordinary_tape_pressure_active)
        old_pressure=replace(_assessment(),recent_sell_bonds=50_000,
                             recent_buy_bonds=2_000,short_ask_change=-.20)
        self.refresh(replace(recovery,tick_id=4,market_ts_ms=recovery.market_ts_ms+3_000,
                             trade_bonds=0,inferred_side='none'),old_pressure)
        self.assertFalse(self.account.ordinary_tape_pressure_active)

    def test_small_isolated_sell_does_not_start_the_regime(self):
        small=replace(self.shock(),trade_bonds=520)
        self.refresh(small,replace(_assessment(),short_ask_change=-.22,
                                  recent_buy_bonds=5_000,recent_sell_bonds=5_000))
        self.assertFalse(self.account.ordinary_tape_pressure_active)
        self.assertNotIn(self.lot.db_id,self.account.sell_orders)

    def test_real_huge_wall_consumption_differs_from_cancellation(self):
        before=replace(self.initial,bids=((137.440,1_000),(137.439,79_000),(137.200,10_000)))
        self.engine._mark_account(self.account,before,persist=False)
        after=replace(_tick(2,self.moment+timedelta(seconds=3),bid=137.439,ask=137.621),
                      bids=((137.439,30_000),(137.200,10_000)),
                      trade_bonds=50_000,inferred_side='sell',last_price=137.439)
        self.engine._update_ordinary_tape_turnover_regime(self.account,
            replace(after,trade_bonds=0,inferred_side='none'),_assessment())
        self.assertFalse(self.account.ordinary_tape_pressure_active)
        self.engine._update_ordinary_tape_turnover_regime(self.account,
            replace(after,tick_id=3),_assessment())
        self.assertTrue(self.account.ordinary_tape_pressure_active)
        self.assertEqual(self.account.inventory,2_000)  # no new active selling permission

    def blocked(self,tick,context,kind='low_bid_reversion'):
        return self.engine._ordinary_entry_sell_pressure_blocks(
            self.account,tick,_assessment(),context,kind=kind,
            price=tick.bid1+.001,quantity=1_000)

    def test_old_anchor_cannot_create_room_inside_a_narrow_book(self):
        self.account.inventory=1_000
        narrow=_tick(2,self.moment+timedelta(seconds=3),bid=137.180,ask=137.200)
        self.assertTrue(self.blocked(narrow,_context(spread=.02)))
        wide=replace(narrow,asks=((137.600,5_000),))
        self.assertFalse(self.blocked(wide,_context(spread=.42)))
        self.assertFalse(self.blocked(narrow,replace(_context(spread=.02),
                                                    reference_price=137.190)))

    def test_active_pressure_blocks_new_extra_but_not_special_or_base_tasks(self):
        self.refresh(self.shock())
        self.account.inventory=1_000
        tick=_tick(3,self.moment+timedelta(seconds=6),bid=137.100,ask=137.600)
        self.assertTrue(self.blocked(tick,_context(spread=.5)))
        for kind in ('sweep_tail','deep_discount_sweep','joint_causal_corridor_entry',
                     'support_collapse_capacity_redeploy_entry','dynamic_customer_base_replenish'):
            self.assertFalse(self.blocked(tick,_context(spread=.5),kind))
        self.account.inventory=500
        self.assertFalse(self.blocked(tick,_context(spread=.5)))
        self.account.inventory=1_000
        self.account.pending_inventory_turn_quantity=500
        self.assertFalse(self.blocked(tick,_context(spread=.5)))

    def test_independently_normal_isolated_offer_keeps_its_protection(self):
        self.account.ordinary_tape_pressure_active=True
        narrow=_tick(2,self.moment+timedelta(seconds=3),bid=137.180,ask=137.200)
        with patch.object(self.engine,'_live_priority_extra_inventory_isolated_offer_price',
                          return_value=137.600):
            self.assertFalse(self.blocked(narrow,_context(spread=.02)))

    def test_active_capacity_release_has_fresh_reentry_identity_and_no_old_cap(self):
        tick=replace(self.shock(),last_price=137.400)
        order=self.engine._new_order(self.account,tick,side='sell',
            kind='full_inventory_capacity_release_exit',lot_id=self.lot.db_id,
            price=137.400,quantity=1_000,queue_ahead=0,target_price=None,
            price_boundary=137.400,persist=True)
        self.account.sell_orders[self.lot.db_id]=order
        self.engine._fill_sell(self.account,tick,order,1_000,
                              tick.market_ts_ms*1_000_000,persist=True)
        self.assertEqual(self.account.inventory,1_000)
        self.assertEqual(self.account.last_ordinary_risk_exit_ts_ms,tick.market_ts_ms)
        self.assertEqual(self.account.last_stalled_extra_exit_ts_ms,0)
        self.assertTrue(self.engine._ordinary_risk_exit_needs_new_frame(self.account,tick))
        self.assertFalse(self.engine._ordinary_risk_exit_needs_new_frame(
            self.account,replace(tick,market_ts_ms=tick.market_ts_ms+1)))


class PriorityV268RevisionTests(PriorityV268ResearchTests):
    policy = REVISION

    def test_registered_child_keeps_the_parent_profile(self):
        self.assertEqual(REVISION, replace(CANDIDATE,
            model_id='maker_priority_v2_68_candidate_r2',
            model_version='2.68-candidate-r2', parent_model_id=CANDIDATE.model_id,
            enable_ordinary_tape_horizontal_recovery=True))

    def add_trade(self, tick, price, bonds, side='buy', offset=0):
        self.engine.analyzer.trade_evidence.append(TradeEvidence(
            market_ts_ms=tick.market_ts_ms+offset, price=price, bonds=bonds,
            transactions=1, side=side))

    def test_replenished_offer_can_restore_horizontal_turnover(self):
        self.refresh(self.shock())
        tick=_tick(3,self.moment+timedelta(seconds=12),bid=137.200,ask=137.600)
        self.add_trade(tick,137.600,600,offset=-3_000)
        self.add_trade(tick,137.600,400)
        self.refresh(tick)
        self.assertFalse(self.account.ordinary_tape_pressure_active)
        recovery=self.account.ordinary_tape_recovery_ts_ms
        self.refresh(replace(tick,tick_id=4,market_ts_ms=tick.market_ts_ms+3_000))
        self.assertEqual(self.account.ordinary_tape_recovery_ts_ms,recovery)

    def test_native_exit_does_not_deadlock_pressure_at_neutral(self):
        self.refresh(self.shock())
        # The native exit can sell the last ordinary lot without writing the
        # fallback risk-exit identity. Its pressure must still accept recovery.
        self.account.inventory=1_000
        self.account.lots.clear()
        self.account.sell_orders.clear()
        self.assertEqual(self.account.last_ordinary_risk_exit_ts_ms,0)
        tick=_tick(3,self.moment+timedelta(seconds=12),bid=137.200,ask=137.600)
        self.add_trade(tick,137.600,1_000)
        self.refresh(tick)
        self.assertFalse(self.account.ordinary_tape_pressure_active)

    def test_old_or_remote_high_trade_does_not_restore_current_book(self):
        self.refresh(self.shock())
        tick=_tick(3,self.moment+timedelta(seconds=12),bid=137.200,ask=137.600)
        self.add_trade(tick,137.600,5_000,offset=-12_000)
        self.add_trade(tick,137.800,5_000)
        self.refresh(tick)
        self.assertTrue(self.account.ordinary_tape_pressure_active)

    def test_narrow_offer_buy_does_not_invent_a_turnover_corridor(self):
        self.refresh(self.shock())
        tick=_tick(3,self.moment+timedelta(seconds=12),bid=137.380,ask=137.400)
        self.add_trade(tick,137.400,5_000)
        self.refresh(tick)
        self.assertTrue(self.account.ordinary_tape_pressure_active)

    def test_new_sell_shock_overrides_horizontal_buy_evidence(self):
        self.refresh(self.shock())
        tick=replace(_tick(3,self.moment+timedelta(seconds=12),
                          bid=137.100,ask=137.300),trade_bonds=10_000,
                     inferred_side='sell',last_price=137.300)
        self.add_trade(tick,137.300,5_000,offset=-1_000)
        self.refresh(tick)
        self.assertTrue(self.account.ordinary_tape_pressure_active)

    def test_new_gradual_sells_can_end_horizontal_recovery(self):
        self.refresh(self.shock())
        tick=_tick(3,self.moment+timedelta(seconds=12),bid=137.200,ask=137.600)
        self.add_trade(tick,137.600,1_000)
        self.refresh(tick)
        lower=_tick(4,self.moment+timedelta(seconds=18),bid=137.190,ask=137.490)
        self.add_trade(lower,137.200,5_000,side='sell')
        self.refresh(lower,replace(_assessment(),short_ask_change=-.11,
                                  recent_sell_bonds=5_000,recent_buy_bonds=1_000))
        self.assertTrue(self.account.ordinary_tape_pressure_active)


class PriorityV268WideCorridorTests(PriorityV268RevisionTests):
    policy = WIDE_REVISION

    def test_registered_child_keeps_the_parent_profile(self):
        self.assertEqual(WIDE_REVISION, replace(REVISION,
            model_id='maker_priority_v2_68_candidate_r3',
            model_version='2.68-candidate-r3', parent_model_id=REVISION.model_id,
            preserve_ordinary_tape_current_wide_corridor=True))

    def test_active_pressure_blocks_new_extra_but_not_special_or_base_tasks(self):
        self.refresh(self.shock())
        self.account.inventory=1_000
        tick=_tick(3,self.moment+timedelta(seconds=6),bid=137.300,ask=137.479)
        self.assertTrue(self.blocked(tick,_context(spread=.179)))
        for kind in ('sweep_tail','deep_discount_sweep','joint_causal_corridor_entry',
                     'support_collapse_capacity_redeploy_entry','dynamic_customer_base_replenish'):
            self.assertFalse(self.blocked(tick,_context(spread=.179),kind))
        self.account.inventory=500
        self.assertFalse(self.blocked(tick,_context(spread=.179)))
        self.account.inventory=1_000
        self.account.pending_inventory_turn_quantity=500
        self.assertFalse(self.blocked(tick,_context(spread=.179)))

    def test_current_wide_corridor_does_not_require_a_trend_reversal(self):
        self.refresh(self.shock())
        self.account.inventory=1_000
        tick=_tick(3,self.moment+timedelta(seconds=6),bid=137.100,ask=137.600)
        self.assertFalse(self.blocked(tick,_context(spread=.5)))
        self.assertTrue(self.account.ordinary_tape_pressure_active)

    def test_wide_exception_does_not_reuse_the_shock_frame(self):
        tick=replace(self.shock(),bids=((137.100,5_000),))
        self.refresh(tick)
        self.account.inventory=1_000
        self.assertTrue(self.blocked(tick,_context(spread=.3)))

    def test_old_reference_cannot_supply_the_missing_current_width(self):
        self.refresh(self.shock())
        self.account.inventory=1_000
        tick=_tick(3,self.moment+timedelta(seconds=6),bid=137.000,ask=137.127)
        self.assertTrue(self.blocked(tick,replace(_context(spread=.127),reference_price=138)))
