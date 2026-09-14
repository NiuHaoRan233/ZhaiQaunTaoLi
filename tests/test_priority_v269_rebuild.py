from __future__ import annotations

from dataclasses import asdict, replace
from datetime import timedelta

from zhaiquant.maker_paper import (
    PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE as BASE,
    PRIORITY_POLICY_FIRST_POSITION_V269_CANDIDATE as REBUILD,
)
from . import test_priority_v268_research as cases
from .test_priority_v264_research import _tick, _assessment


class PriorityV269RebuildTests(cases.PriorityV268WideCorridorTests):
    # Reuse behavioral cases, never their model profiles or candidate ancestry.
    policy = REBUILD

    def test_registered_child_keeps_the_parent_profile(self):
        self.assertEqual(REBUILD, replace(BASE,
            model_id='maker_priority_v2_69_candidate', model_version='2.69-candidate',
            parent_model_id=BASE.model_id, enable_causal_ordinary_inventory_turnover=True))

    def test_no_unapproved_intermediate_profile_changes_are_inherited(self):
        base, rebuilt = asdict(BASE), asdict(REBUILD)
        self.assertEqual({k for k in base if base[k]!=rebuilt[k]},
            {'model_id','model_version','parent_model_id','enable_causal_ordinary_inventory_turnover'})
        self.assertFalse(REBUILD.normalize_native_priority_price_grid)
        self.assertEqual(REBUILD.ordinary_entry_sell_pressure_mode,'off')
        self.assertFalse(REBUILD.enable_guarded_live_priority_extra_inventory_exit_exposure)
        self.assertFalse(REBUILD.enable_ordinary_tape_turnover_regime)
        self.assertFalse(REBUILD.enable_ordinary_tape_horizontal_recovery)
        self.assertFalse(REBUILD.preserve_ordinary_tape_current_wide_corridor)

    def test_new_fallback_only_sells_the_actual_extra_capacity(self):
        self.account.inventory=1250
        self.refresh(self.shock())
        order=self.account.sell_orders[self.lot.db_id]
        self.assertEqual(order.remaining,250)
        self.engine._fill_sell(self.account,self.shock(),order,250,
                              self.shock().market_ts_ms*1_000_000,persist=True)
        self.assertEqual(self.account.inventory,1000)
        later=replace(self.shock(),tick_id=3,market_ts_ms=self.shock().market_ts_ms+3_000)
        self.refresh(later)
        self.assertNotIn(self.lot.db_id,self.account.sell_orders)

    def profitable_exit(self):
        tick=replace(_tick(2,self.moment+timedelta(seconds=3),bid=137.500,ask=137.800),
                     trade_bonds=10_000,inferred_side='sell',last_price=137.500)
        assessment=replace(_assessment(),state='falling',recent_sell_bonds=10_000,
                           recent_buy_bonds=2_000)
        self.engine._active_falling_profitable_bid_exit(self.account,tick,assessment,
            persist=True,received_ts_ns=tick.market_ts_ms*1_000_000)
        return tick

    def test_ordinary_profitable_risk_exit_does_not_create_old_price_cooldown(self):
        tick=self.profitable_exit()
        self.assertEqual(self.account.inventory,1000)
        self.assertEqual(self.account.last_ordinary_risk_exit_ts_ms,tick.market_ts_ms)
        self.assertEqual(self.account.last_falling_profitable_exit_ts_ms,0)

    def test_special_profitable_risk_exit_keeps_263_identity(self):
        self.lot.kind='deep_discount_sweep'
        tick=self.profitable_exit()
        self.assertEqual(self.account.inventory,1000)
        self.assertEqual(self.account.last_ordinary_risk_exit_ts_ms,0)
        self.assertEqual(self.account.last_falling_profitable_exit_ts_ms,tick.market_ts_ms)
