from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from zhaiquant.database import SQLiteStore
from zhaiquant.dadao_maker_live import DadaoPaperRuntime, POLICY, CODES
from zhaiquant.maker_paper import MakerPaperEngine, MakerPaperPortfolio
from zhaiquant.recorder import TickRecorder
from zhaiquant.types import SHANGHAI
from .helpers import test_config, make_tick


def configured(path):
    cfg = test_config(path)
    return replace(cfg, maker_paper=replace(cfg.maker_paper, enabled=True,
        bond_codes=CODES, underlying_stock_codes=dict(zip(CODES, ('600900.SH', '600362.SH'))),
        fill_modes=(), super_windfall_enabled=False, realtime_comparison_model_ids=(POLICY.model_id,)))


def economics(store):
    result = {}
    queries = {
        'fills': 'strategy_id,market_ts_ms,side,price,quantity,reference_tick_id,cash_after,inventory_after',
        'orders': 'strategy_id,side,status,created_market_ts_ms,updated_market_ts_ms,limit_price,quantity,filled_quantity,cancel_reason,metadata_json',
        'accounts': 'strategy_id,cash,inventory,initial_inventory,initial_cash,trading_pnl,fills',
    }
    for key, fields in queries.items():
        result[key] = [tuple(r) for r in store.connection.execute(
            f'SELECT {fields} FROM maker_paper_{key} ORDER BY rowid')]
    return result


class DadaoRuntimeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = configured(Path(tmp.name) / 'trial.sqlite3')
        self.store = SQLiteStore(self.config)
        self.store.start_session()
        self.addCleanup(self.store.close)
        self.runtime = DadaoPaperRuntime(self.config, self.store)
        self.runtime.rebuild_date('2026-09-08')
        self.recorder = TickRecorder(self.store)
        self.start = datetime(2026, 9, 8, 10, tzinfo=SHANGHAI)

    def source(self, seconds, code=CODES[0], *, receive=None, volume=1000, bid=100, ask=101, last=None):
        tick = make_tick(code, self.start + timedelta(seconds=seconds),
            last=bid if last is None else last, bid=bid, ask=ask,
            volume=volume, amount=volume*1000, transactions=int(volume))
        return replace(tick, received_ts_ns=int((self.start + timedelta(
            seconds=seconds if receive is None else receive)).timestamp()*1e9))

    def deliver(self, *args, **kwargs):
        row = self.recorder.record(self.source(*args, **kwargs))
        self.runtime.on_recorded_tick(row)
        return row

    def ready(self):
        self.deliver(1)
        self.deliver(2, CODES[1], ask=100.001)
        self.deliver(3)

    def fills(self):
        return [dict(r) for r in self.store.connection.execute('SELECT * FROM maker_paper_fills ORDER BY id')]

    def test_waits_for_both_quotes_and_never_tops_up(self):
        self.deliver(1)
        self.assertEqual(self.runtime.allocator.shared_cash_cny, 0)
        self.assertEqual(self.store.connection.execute('SELECT count(*) FROM maker_paper_orders').fetchone()[0], 0)
        self.deliver(2, CODES[1], ask=105)
        self.assertEqual(self.runtime.allocator.initial_cash_cny, 105000)
        self.deliver(3, ask=900)
        self.assertEqual(self.runtime.allocator.initial_cash_cny, 105000)

    def test_old_order_settles_before_bad_score_and_sells_at_loss(self):
        self.ready()
        self.deliver(4, bid=98, ask=99, volume=1100, last=98)
        self.assertEqual(self.fills()[0]['price'], 100.001)
        self.deliver(5, bid=98, ask=99, volume=1200, last=99)
        self.assertEqual(self.fills()[1]['price'], 98.999)
        self.assertEqual(self.runtime.allocator.shared_cash_cny, 99998)

    def test_partial_buy_locks_then_partial_sell(self):
        self.ready()
        self.deliver(4, volume=1020)
        self.deliver(5, CODES[1], ask=110, volume=1100)
        self.deliver(6, volume=1120)
        self.deliver(7, last=101, volume=1130)
        self.deliver(8, last=101, volume=1140)
        self.assertEqual([f['quantity'] for f in self.fills()], [200, 100, 100])
        self.assertEqual(sum(a.inventory for e in self.runtime.engines.values() for a in e.accounts.values()), 0)

    def test_same_millisecond_cannot_fill(self):
        self.ready()
        self.deliver(3, volume=1100)
        self.assertEqual(self.fills(), [])

    def test_zero_trade_touch_cannot_fill(self):
        self.ready()
        self.deliver(4, bid=98, ask=99, last=98)
        self.assertEqual(self.fills(), [])

    def test_late_source_print_cannot_fill_newly_submitted_order(self):
        self.deliver(1, receive=10)
        self.deliver(2, CODES[1], receive=10, ask=100.001)
        self.deliver(3, receive=10)
        self.deliver(4, receive=11, volume=1100)
        self.assertEqual(self.fills(), [])
        self.assertGreater(sum(e.execution_counts['pre_submission_source_print_rejected'] for e in self.runtime.engines.values()), 0)

    def test_same_symbol_regression_does_not_reuse_volume(self):
        self.ready()
        self.deliver(2, receive=4, volume=900)
        self.deliver(5, volume=1100)
        self.assertEqual(self.runtime.arrival_counts['same_symbol_regression_ignored'], 1)
        self.assertEqual(self.runtime.allocator.last_bond_ticks[CODES[0]].trade_bonds, 1000)

    def test_duplicate_and_unseen_raw_do_not_enter_recovery(self):
        r = self.deliver(1)
        self.runtime.on_recorded_tick(r)
        self.recorder.record(self.source(2, volume=1100))
        self.runtime.rebuild_date('2026-09-08')
        self.assertEqual(self.runtime.arrival_counts['processed'], 1)

    def test_restart_preserves_partial_inventory_orders_and_continuation(self):
        self.ready()
        self.deliver(4, volume=1020)
        before = economics(self.store)
        self.runtime = DadaoPaperRuntime(self.config, self.store)
        self.runtime.rebuild_date('2026-09-08')
        self.assertEqual(economics(self.store), before)
        self.deliver(5, volume=1040, last=101)
        self.assertEqual([f['quantity'] for f in self.fills()], [200, 200])

    def test_contract_change_rejected_before_ledger_clear(self):
        self.ready()
        before = economics(self.store)
        self.runtime.config = replace(self.runtime.config, qmt=replace(self.runtime.config.qmt, stale_after_seconds=99))
        with self.assertRaisesRegex(ValueError, 'contract changed'):
            self.runtime.rebuild_date('2026-09-08')
        self.assertEqual(economics(self.store), before)

    def test_next_day_independent_and_old_date_cannot_roll_back(self):
        self.ready()
        self.deliver(4, volume=1100)
        self.deliver(86401)
        self.assertEqual(self.runtime.allocator.shared_cash_cny, 0)
        self.deliver(5, volume=1200)
        self.assertEqual(self.runtime.market_date, '2026-09-09')
        self.assertEqual(self.runtime.arrival_counts['old_date_ignored'], 1)

    def test_normalized_contract_survives_other_model_append(self):
        self.ready()
        before = economics(self.store)
        cfg = replace(self.config, maker_paper=replace(self.config.maker_paper,
            realtime_comparison_model_ids=('maker_shared_1000_v0_13_candidate', POLICY.model_id)))
        restarted = DadaoPaperRuntime(cfg, self.store)
        restarted.rebuild_date('2026-09-08')
        self.assertEqual(economics(self.store), before)

    def test_plain_engine_and_market_sorted_entry_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Dadao'):
            MakerPaperEngine(self.config, self.store, priority_policy=POLICY)
        with self.assertRaisesRegex(ValueError, 'arrival journal'):
            self.runtime.on_replay_tick(None, persist=True)

    def test_portfolio_routes_to_separate_runtime(self):
        portfolio = MakerPaperPortfolio(self.config, self.store)
        self.assertEqual(len(portfolio.shared_capital_runtimes), 1)
        self.assertIsInstance(portfolio.shared_capital_runtimes[0], DadaoPaperRuntime)
        self.assertFalse(any(portfolio.comparison_engines.values()))

    def test_name_and_model_metadata(self):
        from zhaiquant.maker_dashboard import _model_display_name
        self.assertEqual(_model_display_name({'model_id': POLICY.model_id}), '大道至简0.1（实时修订）')
        self.ready()
        for row in self.store.connection.execute('SELECT metadata_json FROM maker_paper_orders'):
            meta = json.loads(row[0])
            self.assertEqual(meta['model_id'], POLICY.model_id)
            self.assertGreaterEqual(meta['decision_ts_ms'], meta['source_market_ts_ms'])


if __name__ == '__main__':
    unittest.main()
