"""Synthetic causality and inventory checks for the independent arithmetic probe."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from probe_top_of_book_cycle import Event, run


def event(ts, bid=100000, ask=101000, last=100000, quantity=0, side='none',
          day='2026-08-04', single=True):
    return Event(ts,day,'10:00:00.000',ts,bid,ask,last,((bid,5000),),
                 quantity,1 if single else 2,side,single)


class TopCycleProbeTests(unittest.TestCase):
    def test_new_order_cannot_use_creation_frame_or_same_timestamp(self):
        result=run([event(1000,quantity=1000,side='sell'),
                    event(1000,quantity=1000,side='sell')], 'improve_l1')
        self.assertEqual(result['fills'],[])

    def test_quote_touch_without_new_trade_never_fills(self):
        result=run([event(1000),event(2000,bid=98000,ask=99000,last=98000)],'improve_l1')
        self.assertEqual(result['fills'],[])

    def test_resting_buy_fills_before_bad_new_quote_can_cancel_it(self):
        result=run([event(1000),event(2000,bid=98000,ask=99000,last=98000,
                                    quantity=1000,side='sell')],'improve_l1')
        self.assertEqual(result['fills'][0]['price_milli'],100001)
        self.assertEqual(result['summary']['end_inventory_bonds'],1000)
        self.assertEqual(result['orders'][-1]['price'],98999)

    def test_partial_buy_switches_to_actual_quantity_sell(self):
        result=run([event(1000),event(2000,quantity=200,side='sell'),
                    event(3000,quantity=1000,side='sell'),
                    event(4000,last=101000,quantity=100,side='buy'),
                    event(5000,last=101000,quantity=1000,side='buy')],'improve_l1')
        self.assertEqual([f['quantity'] for f in result['fills']],[200,100,100])
        self.assertEqual(result['summary']['end_inventory_bonds'],0)
        self.assertEqual(result['summary']['complete_cycles'],1)

    def test_loss_is_realized_and_next_purchase_respects_cash(self):
        result=run([event(1000),event(2000,quantity=1000,side='sell'),
                    event(3000,bid=88000,ask=89000,last=88000),
                    event(4000,bid=100000,ask=101000,last=89000,quantity=1000,side='buy')],
                   'improve_l1')
        self.assertLess(result['summary']['realized_cny'],0)
        self.assertEqual(result['orders'][-1]['quantity'],890)
        self.assertGreaterEqual(result['summary']['end_cash_cny'],0)

    def test_overnight_inventory_persists_but_day_order_expires(self):
        result=run([event(1000),event(2000,quantity=1000,side='sell'),
                    event(3000,last=101000,quantity=1000,side='buy',day='2026-08-05'),
                    event(4000,last=101000,quantity=1000,side='buy',day='2026-08-05')],
                   'improve_l1')
        self.assertEqual(result['daily'][0]['inventory_bonds'],1000)
        self.assertEqual(result['fills'][-1]['ts'],4000)
        self.assertEqual(result['summary']['end_inventory_bonds'],0)

    def test_one_tick_spread_does_not_cross_opposite_quote(self):
        result=run([event(1000,ask=100001)],'improve_l1')
        self.assertEqual(result['orders'][0]['price'],100000)

    def test_single_frame_sensitivity_rejects_aggregate_trade(self):
        events=[event(1000),event(2000,quantity=1000,side='sell',single=False)]
        self.assertEqual(len(run(events,'improve_l1')['fills']),1)
        self.assertEqual(run(events,'improve_single')['fills'],[])

    def test_future_events_cannot_change_fill_prefix(self):
        prefix=[event(1000),event(2000,quantity=1000,side='sell'),
                event(3000,last=101000,quantity=1000,side='buy')]
        extended=prefix+[event(4000,bid=1,ask=500000,last=100000)]
        for mode in ('exact_l1','improve_l1','improve_single'):
            self.assertEqual(run(prefix,mode)['fills'],run(extended,mode)['fills'])


if __name__ == '__main__':
    unittest.main()
