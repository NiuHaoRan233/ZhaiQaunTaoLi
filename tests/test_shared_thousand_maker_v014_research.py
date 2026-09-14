from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from zhaiquant.maker import TradeEvidence
from zhaiquant.maker_paper import (
    SHARED_THOUSAND_POLICY_V013_CANDIDATE as PARENT,
    SHARED_THOUSAND_POLICY_V014_CANDIDATE as CHILD,
    SharedCapitalPaperRuntime,
)
from zhaiquant.types import SHANGHAI
from . import test_shared_thousand_maker_v011_research as v011
from .test_shared_thousand_maker_v011_research import _tick, _context, _assessment


class SharedThousandMakerV014Tests(unittest.TestCase):
    _engine = v011.SharedThousandMakerV011Tests._engine
    _open_lot = v011.SharedThousandMakerV011Tests._open_lot

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.engine, self.store = self._engine(Path(self.temporary.name) / 'child.sqlite3', CHILD)
        self.addCleanup(self.store.close)
        self.moment = datetime(2026, 9, 7, 10, 0, 0, tzinfo=SHANGHAI)
        self.engine._start_date(self.moment.date().isoformat())
        self.account = next(iter(self.engine.accounts.values()))

    def tick(self, seconds=3, **kwargs):
        return _tick(seconds + 1, self.moment + timedelta(seconds=seconds),
                     **dict(dict(bid=136.201, ask=136.600), **kwargs))

    def set_exit(self):
        ts = int(self.moment.timestamp() * 1_000)
        self.account.last_stalled_extra_exit_price = 136.170
        self.account.last_stalled_extra_exit_ts_ms = ts
        self.account.last_shared_reentry_exit_ts_ms = ts
        self.account.last_bid = 136.201
        self.account.last_ask = 136.600

    def recovery(self, **kwargs):
        self.set_exit()
        tick = self.tick(**dict(dict(last=136.600, trade_bonds=1_000, inferred_side='buy'), **kwargs))
        self.engine._update_shared_reentry_recovery(self.account, tick)
        return tick

    def open_resilient(self, kind='session_resilient_value_entry'):
        account, lot = self._open_lot(self.engine, self.tick(0), price=136.402,
                                      kind=kind, target_price=136.726)
        account.last_asks = ((136.799, 1_000), (136.800, 2_000))
        account.last_bids = ((136.101, 8_000), (136.100, 1_000))
        account.last_bid, account.last_ask = 136.101, 136.799
        return account, lot

    def gap(self, seconds=3, **kwargs):
        return self.tick(seconds, **dict(dict(
            bid=136.101, ask=136.399,
            asks=((136.399, 1_000), (136.400, 2_000), (136.799, 1_000), (136.800, 2_000)),
        ), **kwargs))

    def refresh(self, tick):
        with patch.object(self.engine, '_decision_context', return_value=_context()):
            self.engine._refresh_orders(self.account, tick,
                _assessment(state='stable', recent_sell=0, short_ask_change=0), persist=True)

    def test_identity_only_two_independent_switches_and_no_exit_regime_change(self):
        self.assertEqual(CHILD.parent_model_id, PARENT.model_id)
        self.assertEqual(CHILD.model_id, 'maker_shared_1000_v0_14_candidate')
        difference = {k for k, v in asdict(CHILD).items() if v != asdict(PARENT)[k]}
        self.assertEqual(difference, {'model_id', 'model_version', 'parent_model_id',
                                     'enable_shared_current_opportunity_reentry',
                                     'enable_shared_resilient_exit_gap'})
        self.assertFalse(PARENT.enable_shared_current_opportunity_reentry)
        self.assertFalse(PARENT.enable_shared_resilient_exit_gap)
        self.assertFalse(CHILD.enable_causal_ordinary_inventory_turnover)
        self.assertEqual(CHILD.guarded_live_exit_ordinary_lot_kinds, ('low_bid_reversion',))

    def test_new_real_attack_with_current_space_waives_only_matching_cap(self):
        tick = self.recovery()
        self.assertTrue(self.engine._shared_current_opportunity_reentry_allowed(self.account, tick))
        self.assertEqual(self.account.last_stalled_extra_exit_price, 136.170)
        self.account.last_shared_reentry_exit_ts_ms -= 1
        self.assertFalse(self.engine._shared_current_opportunity_reentry_allowed(self.account, tick))

    def test_withdrawal_unknown_and_old_low_price_buy_do_not_prove_recovery(self):
        for kwargs in (dict(trade_bonds=0), dict(inferred_side='unknown'), dict(last=136.170)):
            with self.subTest(kwargs=kwargs):
                self.account.shared_reentry_recovery_ts_ms = 0
                tick = self.recovery(**kwargs)
                self.assertFalse(self.engine._shared_current_opportunity_reentry_allowed(self.account, tick))

    def test_narrow_market_missing_support_and_stale_recovery_stay_guarded(self):
        tick = self.recovery()
        for bad in (
            replace(tick, asks=((136.220, 20_000),)),
            replace(tick, bids=((136.201, 10), (135.000, 50_000))),
            replace(tick, market_ts_ms=tick.market_ts_ms + 60_001),
            replace(tick, bids=((136.100, 20_000),)),
        ):
            with self.subTest(bad=bad):
                self.assertFalse(self.engine._shared_current_opportunity_reentry_allowed(self.account, bad))

    def test_new_selling_damage_invalidates_recovery(self):
        self.recovery()
        damage = self.tick(6, bid=136.001, ask=136.200, last=136.201,
                           trade_bonds=1_000, inferred_side='sell')
        self.engine._update_shared_reentry_recovery(self.account, damage)
        self.assertEqual(self.account.shared_reentry_recovery_ts_ms, 0)

    def test_same_timestamp_exit_cannot_establish_recovery(self):
        tick = self.recovery(seconds=0)
        self.assertFalse(self.engine._shared_current_opportunity_reentry_allowed(self.account, tick))

    def test_partial_inventory_and_pending_task_never_waive_cap(self):
        tick = self.recovery()
        self.account.inventory = 20
        self.assertFalse(self.engine._shared_current_opportunity_reentry_allowed(self.account, tick))
        self.account.inventory = 0
        self.account.pending_inventory_turn_quantity = 1_000
        self.assertFalse(self.engine._shared_current_opportunity_reentry_allowed(self.account, tick))

    def test_resilient_small_separated_cluster_keeps_high_passive_sell(self):
        _, lot = self.open_resilient()
        self.refresh(self.gap())
        order = self.account.sell_orders[lot.db_id]
        self.assertEqual(order.kind, 'session_resilient_isolated_hold')
        self.assertAlmostEqual(order.limit_price, 136.798)
        self.assertEqual(order.remaining, 1_000)

    def test_resilient_same_entry_timestamp_does_not_create_fallback(self):
        _, lot = self.open_resilient()
        self.refresh(self.gap(0))
        self.assertNotIn(lot.db_id, self.account.sell_orders)

    def test_large_low_supply_and_missing_normal_offer_follow_current_sell(self):
        _, lot = self.open_resilient()
        for asks in (((136.399, 19_000), (136.799, 1_000)), ((136.399, 1_000), (136.450, 1_000))):
            with self.subTest(asks=asks):
                price, kind = self.engine._shared_resilient_gap_price(
                    self.account, lot, self.gap(asks=asks), _assessment())
                self.assertEqual(kind, 'session_resilient_gap_exit')
                self.assertAlmostEqual(price, 136.398)

    def test_actual_full_support_consumption_cancels_high_protection_not_withdrawal(self):
        _, lot = self.open_resilient()
        gap = self.gap(bid=135.900, bids=((135.900, 5_000),), last=136.101,
                       trade_bonds=9_000, inferred_side='sell')
        for bonds, expected in ((0, 'session_resilient_isolated_hold'),
                                (1_000, 'session_resilient_isolated_hold'),
                                (9_000, 'session_resilient_gap_exit')):
            price, kind = self.engine._shared_resilient_gap_price(
                self.account, lot, replace(gap, trade_bonds=bonds), _assessment())
            self.assertEqual(kind, expected)

    def test_post_entry_broad_selling_cancels_high_protection(self):
        _, lot = self.open_resilient()
        for seconds in (1, 2):
            self.engine.analyzer.trade_evidence.append(TradeEvidence(
                market_ts_ms=lot.opened_ms + seconds * 1_000,
                price=136.101, bonds=3_000, transactions=1, side='sell'))
        _, kind = self.engine._shared_resilient_gap_price(self.account, lot, self.gap(), _assessment())
        self.assertEqual(kind, 'session_resilient_gap_exit')

    def test_one_tick_spread_is_still_passive(self):
        _, lot = self.open_resilient()
        tick = self.gap(bid=136.398, asks=((136.399, 19_000),))
        price, _ = self.engine._shared_resilient_gap_price(self.account, lot, tick, _assessment())
        self.assertAlmostEqual(price, 136.399)
        self.assertGreater(price, tick.bid1)

    def test_native_sell_and_other_special_lots_keep_parent_behavior(self):
        for kind in ('low_bid_reversion', 'deep_discount_sweep', 'sweep_tail',
                     'joint_causal_corridor_entry', 'adjacent_bid_cushion_entry',
                     'session_resilient_value_entry'):
            for ask in (136.399, 136.900):
                if kind == 'session_resilient_value_entry' and ask == 136.399:
                    continue
                outcomes = []
                for index, policy in enumerate((PARENT, CHILD)):
                    engine, store = self._engine(Path(self.temporary.name) / f'{kind}-{ask}-{index}.sqlite3', policy)
                    try:
                        engine._start_date(self.moment.date().isoformat())
                        account, lot = self._open_lot(engine, self.tick(0), kind=kind,
                                                      price=136.402, target_price=136.726)
                        with patch.object(engine, '_decision_context', return_value=_context()):
                            engine._refresh_orders(account, self.tick(3, ask=ask), _assessment(), persist=True)
                        order = account.sell_orders.get(lot.db_id)
                        outcomes.append(None if order is None else (order.kind, order.limit_price, order.remaining))
                    finally:
                        store.close()
                self.assertEqual(outcomes[0], outcomes[1], (kind, ask))

    def test_new_exit_records_scoped_reentry_without_pending_base_task(self):
        _, lot = self.open_resilient()
        self.refresh(self.gap())
        order = self.account.sell_orders[lot.db_id]
        tick = self.tick(6, bid=136.790, ask=136.800)
        self.engine._fill_sell(self.account, tick, order, 1_000, tick.market_ts_ms * 1_000_000,
                               persist=True, reason='passive_sell')
        self.assertEqual(self.account.last_shared_reentry_exit_ts_ms, tick.market_ts_ms)
        self.assertEqual(self.account.last_stalled_extra_exit_ts_ms, tick.market_ts_ms)
        self.assertEqual(self.account.pending_inventory_turn_quantity, 0)
        self.assertEqual(self.account.inventory, 0)

    def test_registered_offline_identity_does_not_enable_realtime_runtime(self):
        row = self.store.connection.execute(
            'SELECT model_id,parent_model_id FROM maker_paper_model_assignments'
        ).fetchone()
        self.assertEqual(row['model_id'], CHILD.model_id)
        self.assertEqual(row['parent_model_id'], PARENT.model_id)
        with self.assertRaisesRegex(ValueError, 'unsupported shared-capital realtime model'):
            SharedCapitalPaperRuntime(self.engine.config, self.store, policy=CHILD)

    def test_partial_resilient_fallback_cannot_sell_the_entire_slot(self):
        account, lot = self._open_lot(self.engine, self.tick(0), price=136.402,
            kind='session_resilient_value_entry', quantity=350, target_price=136.726)
        account.last_asks = ((136.799, 1_000), (136.800, 2_000))
        self.refresh(self.gap())
        self.assertEqual(account.sell_orders[lot.db_id].remaining, 350)

    def test_pre_entry_selling_cannot_be_reused_as_new_resilient_damage(self):
        _, lot = self.open_resilient()
        for seconds in (1, 2):
            self.engine.analyzer.trade_evidence.append(TradeEvidence(
                market_ts_ms=lot.opened_ms - seconds * 1_000,
                price=136.101, bonds=3_000, transactions=1, side='sell'))
        _, kind = self.engine._shared_resilient_gap_price(self.account, lot, self.gap(), _assessment())
        self.assertEqual(kind, 'session_resilient_isolated_hold')
