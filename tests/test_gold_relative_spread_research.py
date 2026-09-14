import unittest
from zhaiquant import gold_relative_spread_research as m
from zhaiquant import gold_direction_research as g
from tests.test_gold_direction_research import event
from tests.test_gold_backer_research import feat


class RelativeSpreadTests(unittest.TestCase):
    def account(self, direction=1):
        a=m.Account('test','trend',960,direction,100)
        a.features={1:feat(),-1:feat()}
        return a

    def step(self,a,e):
        a.value.future=g.Future(e.ts-500,e.ts-500,e.session,960)
        a.option(e)

    def test_exact_boundaries_and_invalid_books(self):
        for bps in m.THRESHOLDS:
            bid=20000-bps;ask=20000+bps
            self.assertTrue(m.qualifies(bid,ask,bps))
            self.assertFalse(m.qualifies(bid,ask-1,bps))
        for bid,ask in [(0,100),(100,0),(100,100),(200,100)]:
            self.assertFalse(m.qualifies(bid,ask,100))

    def test_under_eight_ticks_can_enter_both_sides(self):
        for d in (1,-1):
            a=self.account(d)
            self.step(a,event(g.CUTOFF,bid=994000,ask=1006000,quantity=0))
            self.assertIsNotNone(a.order)  # six ticks, 1.2%
            self.assertEqual(a.order['side'],'buy' if d==1 else 'sell')

    def test_eight_ticks_at_high_premium_fails(self):
        a=self.account()
        self.step(a,event(g.CUTOFF,bid=1992000,ask=2008000,quantity=0))
        self.assertIsNone(a.order)  # eight ticks, 0.8%

    def test_narrowing_cannot_erase_old_fill_or_block_exit(self):
        for d in (1,-1):
            a=self.account(d);t=g.CUTOFF
            self.step(a,event(t,bid=994000,ask=1006000,quantity=0))
            self.step(a,event(t+1000,bid=994000 if d==1 else 998000,
                ask=1002000 if d==1 else 1006000,side='sell' if d==1 else 'buy'))
            self.assertEqual(a.inventory,d)
            self.assertEqual(a.order['side'],'sell' if d==1 else 'buy')

    def test_quote_throttle_cannot_keep_order_below_percentage(self):
        a=self.account();t=g.CUTOFF
        e=event(t,bid=994000,ask=1006000,quantity=0);self.step(a,e)
        a.issue(t+500,'buy',998000,'entry',event(t+500,bid=996000,ask=1002000))
        self.assertIsNone(a.order)


if __name__=='__main__':unittest.main()
