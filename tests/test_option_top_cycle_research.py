from dataclasses import replace
import unittest
from zhaiquant.option_top_cycle_research import Event,run

def event(ts,bid=10000,ask=11000,qty=0,last=10000,side='sell',single=True,depth=10,previous=None,session=0):
    return Event(ts,ts-500 if previous is None else previous,session,bid,ask,depth,depth,
                 ((bid,depth),),((ask,depth),),last,qty,int(qty>0),side,single,side if single else 'unknown')

class OptionCycleTests(unittest.TestCase):
    def run_case(self,events,mode='improve_single',**kw):
        return run(events,code='test',mode=mode,initial_cents=100000,**kw)

    def test_no_new_order_same_event_fill_and_cash_fees(self):
        r=self.run_case([event(1000,qty=1),event(1500,qty=1),event(2000,qty=1,last=11000,side='buy')],fee_cents=300)
        self.assertEqual([f['ts'] for f in r['fills']],[1500,2000])
        self.assertEqual(r['summary']['pnl_cny'],2)
        self.assertEqual(r['summary']['fees_cny'],6)

    def test_first_partial_buy_cancels_remainder(self):
        r=self.run_case([event(1000),event(1500,qty=2),event(2000,qty=10)],capacity=5)
        self.assertEqual(r['summary']['end_inventory'],2)
        self.assertEqual(len(r['fills']),1)
        self.assertEqual(r['orders'][-1]['side'],'sell')

    def test_aggregate_excluded_only_from_single(self):
        events=[event(1000),event(1500,qty=2,single=False)]
        self.assertEqual(len(self.run_case(events)['fills']),0)
        self.assertEqual(len(self.run_case(events,'improve_l1')['fills']),1)

    def test_queue_trade_consumes_front_before_own(self):
        events=[event(1000,depth=2),event(1500,depth=2),event(2000,qty=2,depth=1),event(2500,qty=1)]
        r=self.run_case(events,'queue_single_d500')
        self.assertEqual([f['ts'] for f in r['fills']],[2500])

    def test_cancel_latency_preserves_old_fill(self):
        events=[event(1000),event(1500),event(2000,bid=9000,ask=10000),
                event(2500,bid=9000,ask=10000,qty=1,last=10000)]
        r=self.run_case(events,'improve_single_d500')
        self.assertEqual(r['fills'][0]['price_cents'],10100)
        self.assertEqual(r['fills'][0]['ts'],2500)
        self.assertEqual(r['fills'][0]['kind'],'passive')

    def test_arrival_does_not_claim_interval_trade(self):
        r=self.run_case([event(1000),event(1500,qty=1),event(2000,qty=1)],'improve_single_d500')
        self.assertEqual([f['ts'] for f in r['fills']],[2000])

    def test_crossing_arrival_uses_visible_price_depth(self):
        r=self.run_case([event(1000),event(1500,bid=9000,ask=9900,depth=2)],'improve_single_d500',capacity=5)
        self.assertEqual(r['summary']['end_inventory'],2)
        self.assertEqual(r['fills'][0]['kind'],'arrival_cross')
        self.assertEqual(r['fills'][0]['price_cents'],9900)

    def test_no_credit_when_fees_make_buy_unaffordable(self):
        r=run([event(1000),event(1500,qty=1)],code='test',mode='improve_single',initial_cents=10100,fee_cents=1)
        self.assertEqual(r['summary']['fill_count'],0)

    def test_tail_and_loss_are_not_hidden(self):
        events=[event(1000),event(1500,qty=1),event(2000,bid=9000,ask=9500),event(2500,bid=9000,ask=9500,qty=1,last=9500,side='buy')]
        r=self.run_case(events)
        self.assertEqual(r['summary']['pnl_cny'],-7)
        self.assertEqual(r['summary']['losing_cycles'],1)
        p=self.run_case(events[:3])
        self.assertEqual(p['summary']['end_inventory'],1)
        self.assertEqual(p['summary']['pnl_cny'],-11)

    def test_lunch_orders_expire_but_position_persists(self):
        events=[event(1000),event(1500,qty=1),event(100000,qty=1,last=11000,side='buy',session=1)]
        r=self.run_case(events)
        self.assertEqual(r['summary']['end_inventory'],1)
        self.assertEqual(len(r['fills']),1)

if __name__=='__main__':unittest.main()
