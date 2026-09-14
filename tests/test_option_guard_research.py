from dataclasses import replace
import unittest

from zhaiquant.option_guard_research import Feature, features, entry_reasons, run
from zhaiquant.option_top_cycle_research import MODES, run as baseline
from tests.test_option_top_cycle_research import event


def normalized(value):
    if isinstance(value, dict): return {k: normalized(v) for k, v in value.items() if k != 'model_id'}
    if isinstance(value, (tuple, list)): return [normalized(v) for v in value]
    return value


class OptionGuardTests(unittest.TestCase):
    def replay(self, events, profile='spread', mode='improve_single', **kw):
        return run(events, code='test', profile=profile, mode=mode, **kw)

    def test_control_matches_old_all_modes(self):
        events = [event(1000), event(1500), event(2000,qty=3),
                  event(2500,bid=9000,ask=9700), event(3000,bid=9000,ask=9700,qty=12,last=9700,side='buy')]
        for mode in MODES:
            for fee in [0,300,500]:
                old = baseline(events,code='test',mode=mode,fee_cents=fee)
                new = self.replay(events,'control',mode,fee_cents=fee)
                for key in ('orders','fills','cycles','curve'):
                    self.assertEqual(normalized(new[key]), normalized(old[key]))
                for key, value in old['summary'].items():
                    if key != 'model_id': self.assertEqual(new['summary'][key], value)

    def test_fee_edge_includes_both_improvements(self):
        e = event(1000,ask=11700); f = Feature(e.ts,True,0,0,0,100)
        self.assertIn('net_spread',entry_reasons(e,f,'spread',300,100,False))
        self.assertEqual(entry_reasons(e,f,'spread',300,100,True),[])
        self.assertEqual(entry_reasons(replace(e,ask=11800),f,'spread',300,100,False),[])

    def test_features_reset_lunch_and_reject_stale_anchor(self):
        rows = [event(1000),event(11000),event(51000),event(61000),event(62000,session=1)]
        fs = features(rows)
        self.assertTrue(fs[3].ready)
        self.assertFalse(fs[4].ready)
        self.assertFalse(features([event(1000),event(51000),event(65000)])[-1].ready)

    def test_filters_only_adverse_long_direction_and_book_gap(self):
        e = event(1000,ask=14000)
        rising = Feature(e.ts,True,4000,8000,0,100)
        falling = replace(rising,change10=-4000,change60=-8000)
        self.assertEqual(entry_reasons(e,rising,'entry',300,100,False),[])
        self.assertEqual(entry_reasons(e,falling,'entry',300,100,False),['fall10','fall60'])
        self.assertIn('bid_gap',entry_reasons(e,replace(rising,gap=None),'gap',300,100,False))
        self.assertIn('range60',entry_reasons(e,replace(rising,range60=8000),'entry',300,100,False))

    def test_cancel_inflight_still_fills_old_buy(self):
        rows=[event(1000,ask=12000),event(1500,ask=12000),event(2000,ask=11000),event(2500,ask=11000,qty=1)]
        r=self.replay(rows,mode='improve_single_d500')
        self.assertEqual(r['orders'][1]['side'],'cancel')
        self.assertEqual(r['fills'][0]['ts'],2500)
        self.assertEqual(r['fills'][0]['kind'],'passive')
        self.assertEqual(r['orders'][-1]['side'],'sell')

    def test_cancel_arrival_removes_order_when_no_fill(self):
        rows=[event(1000,ask=12000),event(1500,ask=12000),event(2000),event(2500),event(3000,qty=1)]
        self.assertEqual(self.replay(rows,mode='improve_single_d500')['fills'],[])

    def test_pending_buy_can_cross_after_signal_turns_bad(self):
        rows=[event(1000,ask=12000),event(1500,bid=9000,ask=9900)]
        r=self.replay(rows,mode='improve_single_d500')
        self.assertEqual(r['fills'][0]['kind'],'arrival_cross')
        self.assertEqual(r['summary']['end_inventory'],1)

    def test_exit_not_blocked_by_entry_fee_filter(self):
        rows=[event(1000,ask=12000),event(1500,ask=12000,qty=1),event(2000,bid=9000,ask=9500),
              event(2500,bid=9000,ask=9500,qty=1,last=9500,side='buy')]
        r=self.replay(rows)
        self.assertEqual(r['summary']['complete_cycles'],1)
        self.assertLess(r['summary']['pnl_cny'],0)

    def risk_rows(self):
        rows=[event(1000,ask=12000),event(11000,ask=12000),event(51000,ask=12000),
              event(61000,ask=12000),event(61500,ask=12000),event(62000,ask=12000,qty=1)]
        return [replace(e,bids=((e.bid,10),(e.bid-100,10))) for e in rows]

    def test_timeout_exit_uses_arrival_depth_and_cooldown(self):
        rows=self.risk_rows()+[event(362000,bid=9500,ask=12000),event(362500,bid=9300,ask=12000),
                               event(363000,bid=9300,ask=12000)]
        r=self.replay(rows,'risk','improve_single_d500')
        risk=[f for f in r['fills'] if f['kind']=='risk_ioc']
        self.assertEqual(len(risk),1)
        self.assertEqual((risk[0]['ts'],risk[0]['price_cents']),(362500,9300))
        self.assertIn('cooldown',r['summary']['blocked_frames'])

    def test_risk_partial_depth_keeps_remainder_and_cash_accounting(self):
        rows=self.risk_rows()
        rows[-1]=replace(rows[-1],quantity=5)
        rows += [replace(event(362000,bid=9500,ask=12000),bids=((9500,1),(9400,1))),
                 replace(event(362500,bid=9300,ask=12000),bids=((9300,1),(9200,1)))]
        r=self.replay(rows,'risk','improve_single_d500',capacity=5)
        self.assertEqual(r['summary']['end_inventory'],3)
        self.assertEqual([f['price_cents'] for f in r['fills'] if f['kind']=='risk_ioc'],[9300,9200])
        self.assertEqual(r['summary']['fees_cny'],21)

    def test_adverse_exit_needs_continuous_five_seconds(self):
        rows=self.risk_rows()
        rows += [event(ts,bid=3000,ask=5000) for ts in range(62500,68500,500)]
        r=self.replay(rows,'risk','improve_single_d500')
        self.assertEqual([f['ts'] for f in r['fills'] if f['kind']=='risk_ioc'],[68000])
        split=self.replay(self.risk_rows()+[event(62500,bid=3000,ask=5000),event(68500,bid=3000,ask=5000)],'risk','improve_single_d500')
        self.assertEqual(split['summary']['risk_ioc_fills'],0)

    def test_prefix_features_orders_fills(self):
        rows=self.risk_rows()+[event(362000,bid=9500,ask=12000),event(362500,bid=9300,ask=12000)]
        full=self.replay(rows,'risk','improve_single_d500')
        prefix=self.replay(rows[:-1],'risk','improve_single_d500')
        self.assertEqual(features(rows[:-1]),features(rows)[:-1])
        for key,field in [('orders','created_ts'),('fills','ts')]:
            self.assertEqual(prefix[key],[v for v in full[key] if v[field]<=rows[-2].ts])


if __name__ == '__main__': unittest.main()
