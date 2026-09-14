from dataclasses import replace
import unittest

from zhaiquant.option_guard_cost_separated import run
from zhaiquant.option_guard_research import run as coupled
from tests.test_option_top_cycle_research import event
from tests.test_option_guard_research import normalized


class CostSeparationTests(unittest.TestCase):
    def rows(self,spread=1800):
        rows=[event(t,ask=10000+spread) for t in [1000,11000,51000,61000]]
        rows += [event(61500,ask=10000+spread,qty=1),event(62000,ask=10000+spread,qty=1,last=10000+spread,side='buy')]
        return [replace(e,bids=((e.bid,10),(e.bid-100,10))) for e in rows]

    def replay(self,rows,fee=170,**kw):
        return run(rows,code='test',mode='improve_single',profile='entry',fee_cents=fee,**kw)

    def test_fee_correction_does_not_admit_old_rejected_book(self):
        rows=self.rows(1700)
        self.assertEqual(self.replay(rows,170)['fills'],[])
        self.assertEqual(self.replay(rows,300)['fills'],[])
        old=coupled(rows,code='test',mode='improve_single',profile='entry',fee_cents=170)
        self.assertGreater(len(old['fills']),0)

    def test_accepted_orders_identical_and_only_actual_fee_is_charged(self):
        a=self.replay(self.rows(),170);b=self.replay(self.rows(),300)
        self.assertEqual(normalized(a['orders']),normalized(b['orders']))
        self.assertEqual([(f['ts'],f['side'],f['price_cents']) for f in a['fills']],
                         [(f['ts'],f['side'],f['price_cents']) for f in b['fills']])
        self.assertEqual(a['summary']['fees_cny'],3.4)
        self.assertEqual(a['summary']['pnl_cny'],12.6)
        self.assertEqual(b['summary']['pnl_cny'],10)

    def test_fee300_control_reproduces_frozen_parent(self):
        rows=self.rows();a=self.replay(rows,300)
        b=coupled(rows,code='test',mode='improve_single',profile='entry',fee_cents=300)
        for key in ['orders','fills','curve','cycles','decisions']:
            self.assertEqual(normalized(a[key]),normalized(b[key]))

    def test_cash_affordability_uses_actual_fee(self):
        a=self.replay(self.rows(),170,initial_cents=10270)
        b=self.replay(self.rows(),300,initial_cents=10270)
        self.assertEqual(a['summary']['complete_cycles'],1)
        self.assertEqual(b['summary']['fill_count'],0)

    def test_future_cannot_change_decisions_or_fills(self):
        rows=self.rows()+[event(63000,bid=5000,ask=6000)]
        a=self.replay(rows);b=self.replay(rows[:-1])
        self.assertEqual(b['fills'],[f for f in a['fills'] if f['ts']<=62000])
        self.assertEqual(b['orders'],[o for o in a['orders'] if o['created_ts']<=62000])
        self.assertEqual(b['curve'],a['curve'][:-1])

    def test_identity_records_quality_and_actual_cost_separately(self):
        s=self.replay(self.rows())['summary']
        self.assertTrue(s['model_id'].endswith('_q1_b600_f170'))
        self.assertEqual(s['quality_buffer_cents'],600)
        self.assertEqual(s['fee_per_side_cny'],1.7)


if __name__=='__main__':unittest.main()
