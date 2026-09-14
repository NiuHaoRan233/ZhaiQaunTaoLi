import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from zhaiquant.database import SQLiteStore
from zhaiquant.maker_paper import MakerPaperEngine, SHARED_THOUSAND_POLICY_V014_CANDIDATE as PARENT
from zhaiquant.one_hand_maker_research import _small_account_config
from zhaiquant.shared_thousand_maker_v013_research import SharedThousandV013Allocator
from zhaiquant.shared_thousand_maker_v016_research import POLICY, CausalSharedMakerEngine, SharedThousandV016Allocator
from zhaiquant.types import SHANGHAI
from .helpers import test_config
from .test_shared_thousand_maker_v011_research import _tick


class SharedThousandV016Tests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        database = Path(temp.name)/'causal.sqlite3'
        self.config = _small_account_config(test_config(database), database, shared_capacity_bonds=1000)
        self.store = SQLiteStore(self.config)
        self.store.start_session()
        self.addCleanup(self.store.close)
        self.engine = CausalSharedMakerEngine(self.config, self.store,
            priority_policy=POLICY, fill_modes=('priority',), include_windfall=False)
        self.tick = _tick(1, datetime(2026, 9, 7, 10, 0, tzinfo=SHANGHAI), bid=136.2, ask=136.8)
        self.engine._start_date(self.tick.market_date)
        self.account = next(iter(self.engine.accounts.values()))
        self.allocator = SharedThousandV016Allocator({self.tick.code: self.engine},
            initial_cash_cny=140000, capital_ready_ts_ms=self.tick.market_ts_ms)
        self.allocator.selected_code = self.tick.code
        self.allocator.last_bond_ticks[self.tick.code] = self.tick

    def order(self, tick=None, quantity=1000, price=136.201, side='buy', lot_id=None):
        tick = tick or self.tick
        order = self.engine._new_order(self.account, tick, side=side,
            kind='low_bid_reversion' if side == 'buy' else 'inventory_risk_exit',
            lot_id=lot_id, price=price, quantity=quantity, queue_ahead=0,
            target_price=136.799, price_boundary=price, persist=True)
        if side == 'buy':
            self.account.buy_order = order
        else:
            self.account.sell_orders[lot_id] = order
        return order

    def trade(self, seconds=1, quantity=1000, side='sell', price=136.2, **kwargs):
        return replace(self.tick, tick_id=seconds+2, market_ts_ms=self.tick.market_ts_ms+seconds*1000,
            trade_bonds=quantity, inferred_side=side, last_price=price, **kwargs)

    def match(self, tick):
        self.engine._process_resting_orders(self.account, tick, persist=True,
            received_ts_ns=tick.market_ts_ms*1000000)

    def test_identity_direct_parent_and_no_native_rule_change(self):
        diff = {k for k, v in asdict(POLICY).items() if v != asdict(PARENT)[k]}
        self.assertEqual(diff, {'model_id', 'model_version', 'parent_model_id'})
        self.assertEqual(POLICY.parent_model_id, PARENT.model_id)

    def test_plain_engine_cannot_mislabel_execution(self):
        with self.assertRaisesRegex(ValueError, 'requires Causal'):
            MakerPaperEngine(self.config, self.store, priority_policy=POLICY)

    def test_engine_requires_allocator(self):
        self.engine.causal_allocator_attached = False
        with self.assertRaisesRegex(ValueError, 'requires Shared'):
            self.engine.on_replay_tick(self.tick, persist=True)

    def test_old_buy_fills_without_new_score_or_new_choice(self):
        self.order()
        with patch.object(self.allocator, '_score', side_effect=AssertionError('new bad score')), \
             patch.object(self.allocator, '_choose', side_effect=AssertionError('peer improved')):
            self.match(self.trade())
        self.assertEqual(self.account.inventory, 1000)
        self.assertAlmostEqual(self.allocator.shared_cash_cny, 3799)

    def test_corrected_direction_is_still_passive(self):
        self.order()
        with patch.object(self.engine, '_priority_book_trade_side', return_value='sell'), \
             patch.object(self.allocator, '_score', side_effect=AssertionError('not active')):
            self.match(self.trade(side='buy'))
        self.assertEqual(self.account.inventory, 1000)

    def test_unknown_direction_old_buy_can_settle(self):
        self.order()
        self.match(self.trade(side='unknown'))
        self.assertEqual(self.account.inventory, 1000)

    def test_same_timestamp_buy_cannot_consume_print(self):
        order = self.order()
        self.match(self.trade(seconds=0))
        self.assertIs(self.account.buy_order, order)
        self.assertEqual(self.account.inventory, 0)
        self.match(self.trade(seconds=1))
        self.assertEqual(self.account.inventory, 1000)

    def test_partial_buy_remainder_not_rescored(self):
        self.order()
        with patch.object(self.allocator, '_score', side_effect=AssertionError('rescore')):
            self.match(self.trade(quantity=180))
            self.match(self.trade(seconds=2, quantity=820))
        self.assertEqual(self.account.inventory, 1000)
        self.assertEqual(self.allocator.causal_counts['passive_settlements_without_rescoring'], 2)

    def test_same_timestamp_sell_cannot_consume_print(self):
        self.order()
        self.match(self.trade())
        lot = next(k for k, v in self.account.lots.items() if v.remaining_quantity > 0)
        order = self.order(tick=self.trade(), side='sell', lot_id=lot, price=136.799)
        self.match(self.trade(side='buy', price=136.8))
        self.assertEqual(self.account.inventory, 1000)
        self.assertIs(self.account.sell_orders[lot], order)
        self.match(self.trade(seconds=2, side='buy', price=136.8))
        self.assertEqual(self.account.inventory, 0)

    def test_partial_sells_release_cash_but_keep_slot_locked(self):
        self.order()
        self.match(self.trade())
        lot = next(k for k, v in self.account.lots.items() if v.remaining_quantity > 0)
        self.order(tick=self.trade(), side='sell', lot_id=lot, price=136.799)
        self.match(self.trade(seconds=2, side='buy', price=136.8, quantity=180))
        before = self.allocator.shared_cash_cny
        with patch.object(self.allocator, '_choose', side_effect=AssertionError('still locked')):
            self.allocator._reconcile(self.trade(seconds=2))
        self.assertEqual(self.allocator._holdings(), (self.tick.code,))
        self.match(self.trade(seconds=3, side='buy', price=136.8, quantity=820))
        self.assertGreater(self.allocator.shared_cash_cny, before)
        self.assertEqual(self.allocator._holdings(), ())
        self.assertAlmostEqual(self.allocator.shared_cash_cny, 140598)

    def test_unfunded_commitment_fails_closed(self):
        self.order()
        self.allocator.shared_cash_cny = 0
        with self.assertRaisesRegex(AssertionError, 'shared cash'):
            self.match(self.trade())
        self.assertEqual(self.account.inventory, 0)

    def test_unowned_slot_cannot_fill_passively(self):
        self.order()
        self.allocator.selected_code = '132024.SH'
        with self.assertRaisesRegex(AssertionError, 'own the shared slot'):
            self.match(self.trade())

    def test_unsubmitted_active_intent_keeps_parent_scoring(self):
        order = self.order(price=136.8)
        with patch.object(SharedThousandV013Allocator, '_allow_buy_fill', return_value=False) as native:
            result = self.engine._fill_buy(self.account, self.tick, order, 1000, 0,
                kind=order.kind, target_price=order.target_price, persist=True, reason='active_tail_sweep')
        self.assertFalse(result)
        native.assert_called_once()
        self.assertEqual(self.account.inventory, 0)

    def test_active_depth_not_reused_within_timestamp(self):
        tick = replace(self.tick, asks=((136.8, 100), (136.9, 100)))
        plan = self.engine._depth_plan(tick, 'buy', 136.8, 1000)
        self.assertEqual(plan, [(136.8, 100)])
        self.engine._depth_used['buy'][136.8] = 100
        self.assertEqual(self.engine._depth_plan(replace(tick, tick_id=9), 'buy', 136.8, 1000), [])
        self.assertEqual(self.engine._depth_plan(self.trade(asks=tick.asks), 'buy', 136.8, 1000), plan)

    def test_active_buy_missing_ask_cannot_fill(self):
        order = self.order(price=136.201)
        with patch.object(self.allocator, '_allow_buy_fill', side_effect=AssertionError('no available ask')):
            result = self.engine._fill_buy(self.account, self.tick, order, 1000, 0,
                kind=order.kind, target_price=order.target_price, persist=True, reason='active_tail_sweep')
        self.assertFalse(result)
        self.assertEqual(self.account.inventory, 0)
        self.assertEqual(self.engine.execution_counts['active_buy_depth_limited'], 1)

    def test_active_sell_clips_visible_depth_and_cancels_remainder(self):
        self.order()
        self.match(self.trade())
        lot = next(k for k, v in self.account.lots.items() if v.remaining_quantity > 0)
        tick = self.trade(seconds=2, bids=((136.2, 20), (136.1, 1000)))
        order = self.order(tick=tick, side='sell', lot_id=lot, price=136.2)
        self.engine._fill_sell(self.account, tick, order, 1000, 0,
            persist=True, reason='active_test_risk_exit')
        self.assertEqual(order.filled_quantity, 20)
        self.assertEqual(self.account.inventory, 980)
        self.assertNotIn(lot, self.account.sell_orders)
        row = self.store.connection.execute('SELECT status FROM maker_paper_orders WHERE id=?', (order.db_id,)).fetchone()
        self.assertEqual(row[0], 'cancelled')

    def test_new_native_absence_invalidates_own_shadow_before_choice(self):
        self.allocator.shadow_scores[self.tick.code] = (self.tick.market_ts_ms, object())
        self.allocator._reconcile(self.tick)
        self.assertNotIn(self.tick.code, self.allocator.shadow_scores)
        self.assertIsNone(self.allocator.selected_code)

    def test_duplicate_or_backward_events_rejected(self):
        self.allocator.on_replay_tick(self.tick)
        with self.assertRaisesRegex(ValueError, 'out-of-order'):
            self.allocator.on_replay_tick(self.tick)

    def test_future_suffix_cannot_rewrite_recorded_prefix(self):
        self.order()
        self.allocator.on_replay_tick(self.trade())
        sql = 'SELECT market_ts_ms,side,price,quantity FROM maker_paper_fills WHERE market_ts_ms<=? ORDER BY id'
        cutoff = self.trade().market_ts_ms
        before = [tuple(r) for r in self.store.connection.execute(sql, (cutoff,))]
        self.assertTrue(before)
        for i in range(2, 8):
            self.allocator.on_replay_tick(self.trade(seconds=i, price=130, bids=((130, 50000),), asks=((131, 50000),)))
        after = [tuple(r) for r in self.store.connection.execute(sql, (cutoff,))]
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
