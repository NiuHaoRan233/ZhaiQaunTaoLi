import unittest
from dataclasses import replace
from zhaiquant import gold_two_mode_research as m
from zhaiquant import gold_direction_research as g
from tests.test_gold_direction_research import event
from tests.test_gold_backer_research import feat


class TwoModeTests(unittest.TestCase):
    def account(self, policy='trend'):
        a=m.Account('test',policy,960,-1)
        a.features={1:feat(),-1:feat()}
        return a

    def step(self,a,e):
        a.value.future=g.Future(e.ts-500,e.ts-500,e.session,960)
        a.option(e)

    def test_short_sells_then_buys(self):
        a=self.account();t=g.CUTOFF
        self.step(a,event(t,quantity=0));self.assertEqual(a.order['side'],'sell')
        self.assertEqual(a.order['price'],2018000)
        self.step(a,event(t+1000,side='buy'));self.assertEqual(a.inventory,-1)
        self.assertEqual(a.order['side'],'buy')
        self.step(a,event(t+2000,side='sell'))
        self.assertEqual(a.inventory,0);self.assertEqual(a.rows['cycles'][0]['net_cents'],15660)

    def test_upward_future_risk_cancels_short(self):
        a=self.account('trend_fast');t=g.CUTOFF;self.step(a,event(t,quantity=0))
        a.future_event(g.Future(t+500,t+500,0,961),{1:feat(961,5000),-1:feat(961,5000)})
        self.assertEqual(a.order['cancel_ts'],t+500)

    def test_falling_future_does_not_cancel_short(self):
        a=self.account('trend_fast');t=g.CUTOFF;self.step(a,event(t,quantity=0))
        a.future_event(g.Future(t+500,t+500,0,959),{1:feat(959,-5000),-1:feat(959,-5000)})
        self.assertIsNone(a.order.get('cancel_ts'))

    def test_short_virtual_exclusion_refunds_fees(self):
        a=self.account();t=g.BOUNDARIES[0]-2000
        self.step(a,event(t,quantity=0));self.step(a,event(t+1000,side='buy'))
        a.boundary(g.BOUNDARIES[0]);self.assertEqual(a.cash,a.initial)
        self.assertEqual(a.rows['cycles'][0]['net_cents'],0)

    def test_short_backer_uses_ask_and_exits_at_remaining_ask(self):
        a=self.account('backer_guard');t=g.CUTOFF
        for i in range(3):
            self.step(a,replace(event(t+1000*i,quantity=0),ask_qty=10,asks=((2020000,10),)))
        self.assertEqual(a.order['support']['price'],2020000)
        a.features={1:feat(961,5000),-1:feat(961,5000)}
        self.step(a,replace(event(t+3000,side='buy'),ask_qty=5,asks=((2020000,5),)))
        self.assertEqual(a.inventory,0)
        self.assertEqual(a.rows['fills'][-1]['side'],'buy')
        self.assertEqual(a.rows['fills'][-1]['price_cents'],2020000)
        self.assertEqual(a.rows['cycles'][0]['net_cents'],-2340)


if __name__=='__main__':unittest.main()
