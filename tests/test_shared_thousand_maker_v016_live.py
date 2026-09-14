from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from zhaiquant.database import SQLiteStore
from zhaiquant.maker_paper import (
    MakerPaperEngine, MakerPaperPortfolio, SHARED_THOUSAND_POLICY_V016_CANDIDATE as PARENT,
    SHARED_THOUSAND_POLICY_V01_CANDIDATE, SHARED_THOUSAND_POLICY_V013_CANDIDATE,
)
from zhaiquant.recorder import TickRecorder
from zhaiquant.runner import MarketProcessor
from zhaiquant.shared_thousand_maker_v016_live import (
    POLICY, ArrivalTick, ArrivalSharedPaperRuntime, ArrivalSharedMakerEngine,
)
from zhaiquant.types import SHANGHAI
from .helpers import test_config, make_tick


def configured(database):
    config = test_config(database)
    return replace(config, maker_paper=replace(config.maker_paper, enabled=True,
        bond_codes=('132026.SH', '132024.SH'),
        underlying_stock_codes={'132026.SH': '600900.SH', '132024.SH': '600362.SH'},
        fill_modes=(), super_windfall_enabled=False,
        realtime_comparison_model_ids=(POLICY.model_id,)))


def economic_rows(store):
    return [tuple(row) for row in store.connection.execute('''
        SELECT strategy_id,market_ts_ms,side,price,quantity,fill_reason,
               reference_tick_id,cash_after,inventory_after FROM maker_paper_fills ORDER BY id''')]


class ArrivalRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.config = configured(Path(temporary.name)/'arrival.sqlite3')
        self.store = SQLiteStore(self.config)
        self.store.start_session()
        self.addCleanup(self.store.close)
        self.runtime = ArrivalSharedPaperRuntime(self.config, self.store)
        self.runtime.rebuild_date('2026-09-07')
        self.recorder = TickRecorder(self.store)
        self.start = datetime(2026, 9, 7, 10, tzinfo=SHANGHAI)

    def source(self, seconds, code='132026.SH', *, receive=None, volume=1000, **kwargs):
        tick = make_tick(code, self.start+timedelta(seconds=seconds),
                         last=kwargs.pop('last', 136.2), bid=kwargs.pop('bid', 136.2),
                         ask=kwargs.pop('ask', 136.8), volume=volume,
                         amount=volume*1362, transactions=int(volume), **kwargs)
        if receive is not None:
            tick = replace(tick, received_ts_ns=int((self.start+timedelta(seconds=receive)).timestamp()*1e9))
        return tick

    def deliver(self, tick):
        recorded = self.recorder.record(tick)
        self.runtime.on_recorded_tick(recorded)
        return recorded

    def test_registered_child_and_native_parameters_unchanged(self):
        diff = {key for key, value in asdict(POLICY).items() if value != asdict(PARENT)[key]}
        self.assertEqual(diff, {'model_id', 'model_version', 'parent_model_id'})
        self.assertEqual(POLICY.parent_model_id, PARENT.model_id)

    def test_legacy_contract_restores_both_arrival_models_without_rewriting(self):
        from zhaiquant.dadao_maker_live import DadaoPaperRuntime
        for cls in (ArrivalSharedPaperRuntime, DadaoPaperRuntime):
            with self.subTest(runtime=cls.__name__):
                runtime = cls(self.config, self.store)
                contract = json.loads(runtime._contract())
                for field in ('protect_discounted_ordinary_lot_from_fragile_bid_exit',
                              'require_strict_passive_order_timestamp'):
                    contract['policy'].pop(field)
                original = json.dumps(contract, sort_keys=True)
                self.store.connection.execute('''INSERT OR REPLACE INTO maker_shared_arrival_days
                    (model_id,market_date,contract_json) VALUES (?,?,?)''',
                    (runtime.policy.model_id, '2026-09-07', original))
                runtime.rebuild_date('2026-09-07')
                saved = self.store.connection.execute('''SELECT contract_json FROM maker_shared_arrival_days
                    WHERE model_id=? AND market_date=?''',
                    (runtime.policy.model_id, '2026-09-07')).fetchone()[0]
                self.assertEqual(saved, original)

    def test_contract_compatibility_still_rejects_enabled_flags_and_execution_changes(self):
        current = self.runtime._contract()
        for section, field, value in (
            ('policy', 'protect_discounted_ordinary_lot_from_fragile_bid_exit', True),
            ('policy', 'require_strict_passive_order_timestamp', True),
            ('maker_paper', 'initial_inventory_bonds', 9990),
            ('allocation', 'switch_minimum_score_advantage_cny', 9990),
        ):
            with self.subTest(field=field):
                changed = json.loads(current)
                changed[section][field] = value
                self.assertFalse(self.runtime._contracts_match(json.dumps(changed), current))

    def test_plain_engine_cannot_mislabel_arrival_execution(self):
        with self.assertRaisesRegex(ValueError, 'requires Arrival'):
            MakerPaperEngine(self.config, self.store, priority_policy=POLICY)

    def test_cross_symbol_old_quote_accepts_and_preserves_source_clock(self):
        self.deliver(self.source(21, '600900.SH', receive=21))
        self.deliver(self.source(13, receive=21))
        tick = self.runtime.allocator.last_bond_ticks['132026.SH']
        self.assertEqual(tick.market_ts_ms-tick.source_market_ts_ms, 8000)
        self.assertEqual(self.runtime.arrival_counts['processed'], 2)
        self.assertEqual(self.runtime.arrival_counts['cross_symbol_older_source_accepted'], 1)

    def test_source_ahead_of_receive_clock_does_not_move_decision_back(self):
        self.deliver(self.source(21, '600900.SH', receive=19))
        self.deliver(self.source(13, receive=19))
        self.assertEqual(self.runtime._decision_ms, int((self.start+timedelta(seconds=21)).timestamp()*1000))

    def test_same_symbol_regression_cannot_roll_book_or_double_count(self):
        self.deliver(self.source(21, volume=1100))
        self.deliver(self.source(13, receive=22, volume=1000, bid=130))
        self.assertEqual(self.runtime._previous['132026.SH'][1].volume, 1100)
        self.deliver(self.source(24, volume=1200))
        self.assertEqual(self.runtime.allocator.last_bond_ticks['132026.SH'].trade_bonds, 1000)
        self.assertEqual(self.runtime.arrival_counts['same_symbol_regression_ignored'], 1)

    def test_cumulative_regression_at_newer_time_cannot_reuse_volume(self):
        self.deliver(self.source(21, volume=1100))
        self.deliver(self.source(22, volume=1000))
        self.deliver(self.source(24, volume=1200))
        self.assertEqual(self.runtime.allocator.last_bond_ticks['132026.SH'].trade_bonds, 1000)

    def test_duplicate_event_is_idempotent(self):
        recorded = self.deliver(self.source(1))
        self.runtime.on_recorded_tick(recorded)
        self.assertEqual(self.runtime.arrival_counts['processed'], 1)
        self.assertEqual(self.store.connection.execute('SELECT count(*) FROM maker_shared_arrival_events').fetchone()[0], 1)

    def test_raw_ticks_not_seen_by_model_cannot_enter_recovery(self):
        self.deliver(self.source(1))
        self.recorder.record(self.source(2, volume=2000))  # recorded by another consumer only
        self.runtime.rebuild_date('2026-09-07')
        self.assertEqual(self.runtime.arrival_counts['processed'], 1)

    def test_rebuild_then_reset_recorder_keeps_cumulative_predecessor(self):
        self.deliver(self.source(1, volume=1000))
        self.deliver(self.source(2, '132024.SH', volume=1000))
        self.runtime.rebuild_date('2026-09-07')
        self.recorder = TickRecorder(self.store)
        self.deliver(self.source(4, volume=1100))
        self.assertEqual(self.runtime.allocator.last_bond_ticks['132026.SH'].trade_bonds, 1000)

    def test_changed_execution_contract_refuses_before_clearing_account(self):
        self.deliver(self.source(1))
        count = self.store.connection.execute('SELECT count(*) FROM maker_paper_accounts').fetchone()[0]
        self.runtime.config = replace(self.runtime.config,
            qmt=replace(self.runtime.config.qmt, stale_after_seconds=10))
        with self.assertRaisesRegex(ValueError, 'contract changed'):
            self.runtime.rebuild_date('2026-09-07')
        self.assertEqual(count, self.store.connection.execute('SELECT count(*) FROM maker_paper_accounts').fetchone()[0])

    def test_earlier_day_cannot_reset_active_day(self):
        self.runtime.rebuild_date('2026-09-08')
        self.deliver(self.source(1))
        self.assertEqual(self.runtime.market_date, '2026-09-08')
        self.assertEqual(self.runtime.arrival_counts['old_date_ignored'], 1)

    def test_next_day_resets_independent_cash_slot(self):
        self.deliver(self.source(1))
        self.deliver(self.source(2, '132024.SH'))
        self.runtime.allocator.shared_cash_cny = 1
        self.deliver(self.source(86401))
        self.assertEqual(self.runtime.market_date, '2026-09-08')
        self.assertEqual(self.runtime.allocator.shared_cash_cny, 0)
        self.assertEqual(len(self.runtime.allocator.opening_quotes), 1)

    def test_untyped_market_sorted_input_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'arrival journal'):
            self.runtime.on_replay_tick(None, persist=True)

    def test_overnight_startup_preserves_all_old_ledgers_and_current_predecessor(self):
        processor = MarketProcessor(self.config, self.store, preload_m0_history=False,
                                   live_market_date=self.start.date()+timedelta(days=1))
        processor.maker_paper.rebuild_date('2026-09-08')
        before = [tuple(r) for r in self.store.connection.execute('SELECT * FROM maker_paper_accounts')]
        with patch.object(processor.maker_paper, 'on_recorded_tick') as maker, \
                patch.object(processor.m0, 'on_tick', return_value=None) as m0:
            current = self.source(86401)
            processor.process(current)
            processor.process(self.source(2, volume=1200))
            self.assertEqual(maker.call_count, 1)
            self.assertEqual(m0.call_count, 1)
            self.assertEqual(processor.recorder.previous['132026.SH'][1], current)
        self.assertEqual(before, [tuple(r) for r in self.store.connection.execute('SELECT * FROM maker_paper_accounts')])
        self.assertEqual(self.store.connection.execute('SELECT count(*) FROM raw_ticks').fetchone()[0], 2)

    def test_three_capital_slots_and_correct_engine_types(self):
        config = replace(self.config, maker_paper=replace(self.config.maker_paper,
            realtime_comparison_model_ids=(SHARED_THOUSAND_POLICY_V01_CANDIDATE.model_id,
                SHARED_THOUSAND_POLICY_V013_CANDIDATE.model_id, POLICY.model_id)))
        portfolio = MakerPaperPortfolio(config, self.store)
        portfolio.rebuild_date('2026-09-08')
        runtimes = portfolio.shared_capital_runtimes
        self.assertEqual(len(runtimes), 3)
        self.assertEqual(len({id(runtime.allocator) for runtime in runtimes}), 3)
        self.assertTrue(all(isinstance(e, ArrivalSharedMakerEngine) for e in runtimes[-1].engines.values()))
        runtimes[-1].allocator.shared_cash_cny = 10
        self.assertEqual([r.allocator.shared_cash_cny for r in runtimes[:-1]], [0, 0])
        self.assertEqual(len(portfolio.accounts), 6)


class ArrivalSettlementTests(ArrivalRuntimeTests):
    def setup_order(self, *, side='buy'):
        self.deliver(self.source(0))
        self.deliver(self.source(1, '132024.SH'))
        engine = self.runtime.engines['132026.SH']
        account = next(iter(engine.accounts.values()))
        tick = replace(self.runtime.allocator.last_bond_ticks['132026.SH'],
                       market_ts_ms=int((self.start+timedelta(seconds=20)).timestamp()*1000))
        if side == 'sell':
            # A test-owned legacy base lot suffices for matching quantity/clock.
            lot = next(iter(account.lots.values()))
            lot.remaining_quantity = 1000
            lot.original_quantity = 1000
            account.inventory = 1000
            lot_id = lot.db_id
        else:
            lot_id = None
        order = engine._new_order(account, tick, side=side, kind='low_bid_reversion' if side=='buy' else 'inventory_exit',
            lot_id=lot_id, price=136.201 if side=='buy' else 136.799, quantity=1000,
            queue_ahead=0, target_price=136.799, persist=True)
        if side == 'buy':
            account.buy_order = order
        else:
            account.sell_orders[lot_id] = order
        self.runtime.allocator.selected_code = '132026.SH'
        return engine, account, tick, order

    def test_late_print_cannot_fill_later_buy(self):
        engine, account, tick, order = self.setup_order()
        late = replace(tick, tick_id=10, market_ts_ms=tick.market_ts_ms+5000,
                       source_market_ts_ms=tick.market_ts_ms-5000, trade_bonds=1000,
                       inferred_side='sell', last_price=136.2)
        engine._process_resting_orders(account, late, persist=True, received_ts_ns=late.received_ts_ns)
        self.assertEqual(order.filled_quantity, 0)
        self.assertIs(account.buy_order, order)

    def test_late_print_cannot_fill_later_sell(self):
        engine, account, tick, order = self.setup_order(side='sell')
        late = replace(tick, tick_id=10, market_ts_ms=tick.market_ts_ms+5000,
                       source_market_ts_ms=tick.market_ts_ms-5000, trade_bonds=1000,
                       inferred_side='buy', last_price=136.8)
        engine._process_resting_orders(account, late, persist=True, received_ts_ns=late.received_ts_ns)
        self.assertEqual(order.filled_quantity, 0)

    def test_old_passive_order_settles_without_rescoring_and_partial_locks_slot(self):
        engine, account, tick, order = self.setup_order()
        new = replace(tick, tick_id=10, market_ts_ms=tick.market_ts_ms+5000,
                      source_market_ts_ms=tick.market_ts_ms+1000, trade_bonds=400,
                      inferred_side='sell', last_price=136.2)
        with patch.object(self.runtime.allocator, '_score', side_effect=AssertionError('must not rescore')):
            engine._process_resting_orders(account, new, persist=True, received_ts_ns=new.received_ts_ns)
        self.assertEqual(order.filled_quantity, 400)
        self.assertEqual(account.inventory, 400)
        self.runtime.allocator._reconcile(new)
        self.assertEqual(self.runtime.allocator.selected_code, '132026.SH')
        metadata = json.loads(self.store.connection.execute('SELECT metadata_json FROM maker_paper_orders WHERE id=?',
                                                           (order.db_id,)).fetchone()[0])
        self.assertEqual(metadata['model_id'], POLICY.model_id)
        self.assertEqual(metadata['time_basis'], 'arrival_decision_clock')

    def test_source_timestamp_depth_budget_survives_later_receipt(self):
        engine, account, tick, order = self.setup_order()
        tick = replace(tick, source_market_ts_ms=tick.market_ts_ms, asks=((136.8, 1000),))
        self.assertEqual(engine._depth_plan(tick, 'buy', 136.8, 1000), [(136.8, 1000)])
        engine._depth_used['buy'][136.8] += 700
        later = replace(tick, market_ts_ms=tick.market_ts_ms+1000)
        self.assertEqual(engine._depth_plan(later, 'buy', 136.8, 1000), [(136.8, 300)])

    def test_stale_quote_not_assumed_immediately_executable(self):
        engine, account, tick, order = self.setup_order()
        stale = replace(tick, source_market_ts_ms=tick.market_ts_ms-4000)
        self.assertEqual(engine._depth_plan(stale, 'buy', 200, 1000), [])
