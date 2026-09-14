"""Independent causal and accounting checks for first-round commodity research."""
from dataclasses import replace
import unittest

import pandas as pd

from zhaiquant import commodity_dadao_continuous as archived
from zhaiquant import commodity_selective_research as candidate
from zhaiquant.option_top_cycle_research import Event


START = int(pd.Timestamp('2026-09-11 09:00', tz='Asia/Shanghai').timestamp() * 1000)


def event(seconds, *, bid=10000, ask=11000, quantity=0, last=10000,
          side='sell', bid_qty=2, ask_qty=2, session=0, previous_seconds=None):
    ts = START + round(seconds * 1000)
    prev = ts - 1000 if previous_seconds is None else START + round(previous_seconds * 1000)
    return Event(ts, prev, session, bid, ask, bid_qty, ask_qty,
                 ((bid, bid_qty),), ((ask, ask_qty),), last, quantity, 0,
                 side, True, side)


def inputs(events, *, tick=100, date='20260911'):
    return [(events, [True] * len(events), dict(unit=10, tick_cents=tick,
            initial_cents=1_000_000, date=date))]


def execute(events, variant, *, tick=100):
    return candidate.run(inputs(events, tick=tick), code='unit.DF', variant=variant)


class IndependentSelectiveTests(unittest.TestCase):
    def test_control_reconstructs_archived_continuous_economics(self):
        first = [event(0), event(1, quantity=1), event(2, bid=5000, ask=11000)]
        second = [event(86400), event(86401, quantity=1, last=11000, side='buy')]
        src = inputs(first, date='20260910') + inputs(second, date='20260911')
        a = archived.run(src, code='unit.DF', mode='last_d0', fee_cents=170)
        b = candidate.run(src, code='unit.DF', variant='control')
        for key in ['pnl_cny', 'initial_cash_cny', 'end_cash_cny', 'end_inventory',
                    'realized_gross_cny', 'tail_gross_cny', 'fees_cny',
                    'fill_count', 'complete_cycles', 'max_drawdown_cny', 'holding_seconds']:
            self.assertEqual(a['summary'][key], b['summary'][key], key)
        self.assertEqual(a['curve'], b['curve'])
        self.assertEqual(a['daily'], b['daily'])
        for key in ['ts', 'side', 'price_cents', 'quantity', 'fee_cents', 'cash_cents', 'inventory']:
            self.assertEqual([x[key] for x in a['fills']], [x[key] for x in b['fills']], key)

    def test_current_trade_cannot_fill_new_order_or_both_legs(self):
        r = execute([event(0, quantity=1), event(1, quantity=20)], 'fee_edge')
        self.assertEqual(len(r['fills']), 1)
        self.assertEqual(r['fills'][0]['ts'], START + 1000)
        self.assertEqual(r['fills'][0]['source_quantity'], 1)
        self.assertEqual(r['summary']['end_inventory'], 1)
        self.assertEqual(r['summary']['complete_cycles'], 0)

    def test_two_tick_book_has_no_improved_round_edge(self):
        es = [event(0, ask=10200), event(1, ask=10200, quantity=1),
              event(2, ask=10200, quantity=1, last=10200, side='buy')]
        raw = execute(es, 'control')
        self.assertEqual(raw['summary']['pnl_cny'], -3.4)
        filtered = execute(es, 'fee_edge')
        self.assertEqual(filtered['fills'], [])
        self.assertEqual(filtered['summary']['rejection_frames']['insufficient_net_edge'], 3)

    def test_fee_gate_uses_net_improved_space_and_one_extra_tick(self):
        no = execute([event(0, ask=10360)], 'fee_edge', tick=10)
        yes = execute([event(0, ask=10370)], 'fee_edge', tick=10)
        self.assertEqual(no['orders'], [])
        self.assertEqual(yes['orders'][0]['price'], 10010)

    def test_entry_filter_never_blocks_existing_position_exit(self):
        es = [event(0), event(1, quantity=1), event(2, bid=10000, ask=10200),
              event(3, bid=10000, ask=10200, quantity=1, last=10200, side='buy')]
        r = execute(es, 'fee_edge')
        self.assertEqual([f['side'] for f in r['fills']], ['buy', 'sell'])
        self.assertEqual(r['summary']['pnl_cny'], -3.4)

    def test_stability_requires_history_before_first_entry(self):
        r = execute([event(s, previous_seconds=s-5) for s in range(0, 61, 5)], 'stable_entry')
        self.assertEqual(r['orders'][0]['created_ts'], START + 60000)

    def test_stability_resets_on_gap_invalid_quote_and_session(self):
        for reset in [event(121), event(61, bid=0), event(61, session=1)]:
            with self.subTest(reset=reset):
                p = candidate.PastBook()
                for s in range(0, 61, 5): features = p.update(event(s, previous_seconds=s-5))
                self.assertIsNotNone(features)
                self.assertIsNone(p.update(reset))

    def test_falling_book_blocks_new_entry(self):
        es = [event(s, previous_seconds=s-5) for s in range(0, 61, 5)]
        es += [event(61, bid=9400, ask=10400), event(62, bid=9400, ask=10400)]
        r = execute(es, 'stable_entry')
        self.assertGreaterEqual(r['summary']['rejection_frames']['falling_book'], 2)
        self.assertEqual([o['created_ts'] for o in r['orders']], [START + 60000])

    def test_old_buy_settles_before_current_stability_rejection(self):
        es = [event(s, previous_seconds=s-5) for s in range(0, 61, 5)]
        es += [event(61, bid=9000, ask=10000, quantity=1, last=9000)]
        r = execute(es, 'stable_entry')
        self.assertEqual(r['fills'][0]['price_cents'], 10100)
        self.assertEqual(r['fills'][0]['ts'], START + 61000)

    def test_patient_exit_prices_above_all_fees_but_has_finite_wall_clock(self):
        es = [event(0), event(1, quantity=1), event(2, bid=10000, ask=10200),
              event(301, bid=10000, ask=10200),
              event(302, bid=10000, ask=10200, quantity=1, last=10200, side='buy')]
        r = execute(es, 'patient_exit')
        protected = next(o for o in r['orders'] if o['created_ts'] == START + 2000)
        self.assertEqual(protected['price'], 10600)
        self.assertEqual(r['fills'][-1]['price_cents'], 10100)
        self.assertEqual(r['summary']['pnl_cny'], -3.4)

    def test_patient_risk_release_requires_sustained_downward_value(self):
        es = [event(0), event(1, quantity=1), event(2, bid=8000, ask=9000),
              event(31, bid=8000, ask=9000), event(32, bid=8000, ask=9000)]
        r = execute(es, 'patient_exit')
        orders = {o['created_ts']: o for o in r['orders']}
        self.assertEqual(orders[START + 2000]['price'], 10600)
        self.assertNotIn(START + 31000, orders)
        self.assertEqual(orders[START + 32000]['price'], 8900)

    def test_risk_release_cannot_bridge_invalid_quotes(self):
        es = [event(0), event(1, quantity=1), event(2, bid=8000, ask=9000),
              event(20, bid=0), event(32, bid=8000, ask=9000)]
        r = execute(es, 'patient_exit')
        latest = r['orders'][-1]
        self.assertEqual(latest['created_ts'], START + 32000)
        self.assertEqual(latest['price'], 10600)

    def test_day_flat_stops_new_buys_at_1430(self):
        r = execute([event(5.5 * 3600)], 'day_flat')
        self.assertEqual(r['orders'], [])
        self.assertEqual(r['summary']['rejection_frames']['late_entry'], 1)

    def test_day_flat_uses_observed_bid_charges_fee_and_never_reenters(self):
        es = [event(0), event(1, quantity=1), event(21300, bid=9900), event(21301, bid=9900)]
        r = execute(es, 'day_flat')
        self.assertEqual(r['summary']['active_exit_fills'], 1)
        self.assertEqual(r['summary']['pnl_cny'], -5.4)
        self.assertEqual(r['fills'][-1]['kind'], 'active_exit')
        self.assertEqual(r['fills'][-1]['price_cents'], 9900)
        self.assertEqual(len(r['orders']), 3)

    def test_day_flat_accepts_live_bid_when_ask_is_missing(self):
        es = [event(0), event(1, quantity=1), event(21300, bid=9900, ask=0, ask_qty=0)]
        r = execute(es, 'day_flat')
        self.assertEqual(r['summary']['end_inventory'], 0)
        self.assertEqual(r['fills'][-1]['kind'], 'active_exit')
        self.assertEqual(r['fills'][-1]['price_cents'], 9900)

    def test_missing_or_empty_bid_never_fabricates_flattening(self):
        for kwargs in [dict(bid=0), dict(bid=9900, bid_qty=0)]:
            r = execute([event(0), event(1, quantity=1), event(21300, **kwargs)], 'day_flat')
            self.assertEqual(r['summary']['end_inventory'], 1)
            self.assertEqual(r['summary']['active_exit_fills'], 0)
            self.assertEqual(r['summary']['rejection_frames']['day_flat_no_current_bid'], 1)

    def test_flow_requires_observed_both_sides_before_new_order(self):
        es = [event(0, quantity=1), event(1, quantity=1, last=11000, side='buy'),
              event(2, quantity=1)]
        for variant in ('flow_entry', 'flow_patient'):
            r = execute(es, variant)
            self.assertEqual(r['orders'][0]['created_ts'], START + 1000)
            self.assertEqual([f['ts'] for f in r['fills']], [START + 2000])

    def test_flow_rejects_unusable_trade_evidence(self):
        base = [event(0, quantity=1), event(1, quantity=1, last=11000, side='buy')]
        for rejected in [replace(base[1], single=False), replace(base[1], strict_side='unknown'),
                         replace(base[1], quantity=0)]:
            r = execute([base[0], rejected], 'flow_entry')
            self.assertEqual(r['orders'], [])

    def test_flow_never_carries_across_gap_invalid_or_session(self):
        initial = [event(0, quantity=1), event(1, quantity=1, last=11000, side='buy')]
        for boundary in [event(62), event(2, bid=0), event(2, session=1)]:
            after = replace(event((boundary.ts - START) / 1000 + 1, quantity=1,
                                  last=11000, side='buy'), session=boundary.session)
            r = execute(initial + [boundary, after], 'flow_entry')
            self.assertEqual([o['created_ts'] for o in r['orders']], [START + 1000])
            self.assertEqual(r['fills'], [])

    def test_flow_window_expires_evidence_older_than_300_seconds(self):
        es = [event(0, quantity=1), event(1, quantity=1, last=11000, side='buy')]
        es += [event(s) for s in [60, 120, 180, 240, 300, 301]]
        es += [event(302, quantity=1, last=11000, side='buy'), event(303, quantity=1)]
        r = execute(es, 'flow_entry')
        self.assertEqual([o['created_ts'] for o in r['orders']], [START + 1000, START + 303000])
        self.assertEqual(r['fills'], [])

    def test_expired_flow_does_not_block_inventory_sale(self):
        es = [event(0, quantity=1), event(1, quantity=1, last=11000, side='buy'),
              event(2, quantity=1)]
        es += [event(s) for s in [60, 120, 180, 240, 300, 301, 302]]
        es += [event(303, quantity=1, last=11000, side='buy')]
        r = execute(es, 'flow_entry')
        self.assertEqual([f['side'] for f in r['fills']], ['buy', 'sell'])
        self.assertEqual(r['summary']['end_inventory'], 0)

    def test_patient_protection_releases_at_session_change(self):
        es = [event(0), event(1, quantity=1), event(2, bid=8000, ask=9000),
              event(20, bid=8000, ask=9000, session=1)]
        r = execute(es, 'patient_exit')
        self.assertEqual(r['orders'][-1]['price'], 8900)
        self.assertEqual(r['orders'][-1]['created_ts'], START + 20000)

    def test_prefix_causality_all_variants(self):
        es = [event(s, previous_seconds=s-5) for s in range(0, 66, 5)]
        es += [event(66, quantity=1), event(67, bid=10000, ask=10200),
               event(68, bid=9000, ask=10100), event(69, quantity=1, last=11000, side='buy')]
        cutoff = es[-3].ts
        for variant in candidate.VARIANTS:
            a = execute(es, variant)
            b = execute(es[:-2], variant)
            self.assertEqual(b['orders'], [o for o in a['orders'] if o['created_ts'] <= cutoff], variant)
            self.assertEqual(b['fills'], [f for f in a['fills'] if f['ts'] <= cutoff], variant)
            self.assertEqual(b['curve'], [p for p in a['curve'] if p[0] <= cutoff], variant)


if __name__ == '__main__':
    unittest.main()
