"""Inventory, switching and causality contracts of the offline comparison."""
from datetime import datetime, timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from probe_top_cycle_comparison import CODES, load_day, run_day
from zhaiquant.maker import MakerParameters, ReplayTick

STOCKS = dict(zip(CODES, ('600900.SH', '600362.SH')))


def tick(seconds, code=CODES[0], bid=100, ask=101, last=100,
         quantity=0, side='none', tick_id=None):
    return ReplayTick(tick_id or seconds, code, 1000000 + seconds * 1000,
        '2026-09-04', (datetime(2026, 9, 4, 10) + timedelta(seconds=seconds)).strftime('%H:%M:%S.000'),
        last, ((bid, 10000),), ((ask, 10000),), quantity,
        int(quantity > 0), side, 'test', 100)


def simulate(events, variant='fixed_132026', **kwargs):
    return run_day(events, variant=variant, initial_cash=101000,
        ready_ts=1000000, params=MakerParameters(), stock_codes=STOCKS, **kwargs)


class TopCycleComparisonTests(unittest.TestCase):
    def test_same_timestamp_and_no_trade_cannot_fill(self):
        for variant in ('fixed_132026', 'switch_v013', 'ordinary_132026'):
            result = simulate([tick(1), tick(1, quantity=1000, side='sell', tick_id=2),
                tick(2, bid=98, ask=99, last=98)], variant)
            self.assertEqual(result['fills'], [])

    def test_old_selected_buy_fills_before_bad_score(self):
        result = simulate([tick(1), tick(2, bid=98, ask=99, last=98,
            quantity=1000, side='sell')], 'switch_v013')
        self.assertEqual(result['fills'][0]['price'], 100.001)
        self.assertEqual(result['terminal_inventories'][CODES[0]], 1000)
        self.assertEqual(result['orders'][-1]['price'], 98999)

    def test_partial_position_locks_bond_until_sold(self):
        result = simulate([tick(1), tick(2, quantity=200, side='sell'),
            tick(3, code=CODES[1], ask=110),
            tick(4, code=CODES[1], ask=110, quantity=1000, side='sell'),
            tick(5, quantity=1000, side='sell'),
            tick(6, last=101, quantity=100, side='buy'),
            tick(7, last=101, quantity=1000, side='buy')], 'switch_v013')
        self.assertEqual([f['quantity'] for f in result['fills']], [200, 100, 100])
        self.assertTrue(all(f['bond_code'] == CODES[0] for f in result['fills']))
        self.assertEqual(result['closed_cycles'], 1)

    def test_native_dwell_and_advantage_control_switching(self):
        # Challenger is much better, but cannot displace a still-positive
        # current candidate until the native 60-second dwell has elapsed.
        result = simulate([tick(1), tick(2, code=CODES[1], ask=110),
            tick(60, code=CODES[1], ask=110),
            tick(62, code=CODES[1], ask=110)], 'switch_v013')
        self.assertEqual([(s['time'], s['winner']) for s in result['selection_events']],
            [('10:00:01.000', CODES[0]), ('10:01:02.000', CODES[1])])
        self.assertEqual(result['selection_events'][-1]['reason'],
            'challenger_has_clear_capital_time_advantage')
        weak = simulate([tick(1), tick(62, code=CODES[1], ask=101.1)], 'switch_v013')
        self.assertEqual(len(weak['selection_events']), 1)

    def test_future_other_bond_cannot_change_order_fill_selection_prefix(self):
        prefix = [tick(1), tick(2, quantity=200, side='sell'),
            tick(3, last=101, quantity=200, side='buy')]
        for variant in ('fixed_132026', 'switch_v013', 'ordinary_132026'):
            before = simulate(prefix, variant)
            after = simulate(prefix + [tick(4, code=CODES[1], bid=1, ask=500)], variant)
            for key, ts_key in [('fills', 'market_ts_ms'), ('orders', 'created_ts'),
                                ('selection_events', 'ts')]:
                self.assertEqual(before[key], [r for r in after[key] if r[ts_key] <= 1003000])

    def test_cash_ready_uses_first_eligible_quotes_not_future_high(self):
        streams = {CODES[0]: [tick(1, ask=101), tick(9, ask=900)],
                   CODES[1]: [tick(3, code=CODES[1], ask=105)]}
        with patch('probe_top_cycle_comparison._load_ticks',
                   side_effect=lambda conn, day, code, stock, p: streams[code]), \
             patch('probe_top_cycle_comparison.maker_underlying_stock_code',
                   side_effect=lambda config, code: STOCKS[code]):
            events, cash, ready = load_day(None, None, '2026-09-04', MakerParameters())
        self.assertEqual((cash, ready), (105000, 1003000))
        result = run_day(events, variant='fixed_132026', initial_cash=cash,
            ready_ts=ready, params=MakerParameters(), stock_codes=STOCKS)
        self.assertTrue(all(o['created_ts'] >= ready for o in result['orders']))

    def test_ordinary_funding_is_excluded_from_profit_and_capacity_is_bounded(self):
        result = simulate([tick(1), tick(2, quantity=200, side='sell'),
            tick(3, quantity=1000, side='sell'),
            tick(4, quantity=1000, side='sell'),
            tick(5, last=101, quantity=1000, side='buy'),
            tick(6, last=101, quantity=1000, side='buy')],
            'ordinary_132026', ordinary_seed=1)
        self.assertEqual([f['quantity'] for f in result['fills']], [200, 800, 1000, 1000])
        self.assertEqual(result['maximum_inventory_bonds'], 2000)
        self.assertEqual(result['terminal_inventories'][CODES[0]], 0)
        self.assertAlmostEqual(result['funding_adjustment_cny'], 100000)
        # Buying 1000 and selling 2000 with 1000 base short marked at ask.
        self.assertAlmostEqual(result['trading_pnl'], 997)


if __name__ == '__main__':
    unittest.main()
