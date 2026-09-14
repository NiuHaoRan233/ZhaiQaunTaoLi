from dataclasses import replace
import unittest

from zhaiquant.commodity_capital_research import ResearchAccount, SharedPortfolio
from zhaiquant.commodity_flow_strategy import FlowAccount
from zhaiquant.option_top_cycle_research import Event

START = 1787533200000

def event(second, side='sell', bid=10000, ask=12000, volume=1, session=0):
    ts=START+second*1000
    last=bid if side=='sell' else ask
    return Event(ts,ts-1000,session,bid,ask,2,2,((bid,2),),((ask,2),),last,volume,0,side,True,side)

def economic(rows):
    return [{k:v for k,v in r.items() if k!='model_id'} for r in rows]

class CapitalResearchTests(unittest.TestCase):
    def test_base_exact_parent_economics(self):
        a=ResearchAccount('a','base',100000,100)
        b=FlowAccount('a','flow_patient',100000,100)
        for i,side in enumerate(['sell','buy','sell','buy','sell','buy']):
            x,y=a.step(event(i,side),'20260824'),b.step(event(i,side),'20260824')
            for k in ['orders','fills']:self.assertEqual(economic(x[k]),economic(y[k]))
            self.assertEqual(x['curve'],y['curve'])
        self.assertEqual(a.daily(),b.daily())

    def test_gate_never_retroactively_vetoes_pending_fill(self):
        a=ResearchAccount('a','fresh60',100000,100)
        a.step(event(0),'20260824');a.step(event(1,'buy'),'20260824')
        # Buy evidence is now older than the new-entry window; old order still settles.
        d=a.step(event(62),'20260824')
        self.assertEqual(d['fills'][0]['side'],'buy')

    def test_fee_edge_positive_and_negative(self):
        for ask, allowed in [(10600,False),(11200,True)]:
            a=ResearchAccount('a','edge_fee',100000,100)
            a.step(event(0,ask=ask),'20260824')
            a.step(event(1,'buy',ask=ask),'20260824')
            self.assertEqual(a.s['order'] is not None,allowed)

    def test_two_sided_twice(self):
        a=ResearchAccount('a','flow2',100000,100)
        for i,side in enumerate(['sell','buy','sell']):
            a.step(event(i,side),'20260824');self.assertIsNone(a.s['order'])
        a.step(event(3,'buy'),'20260824');self.assertIsNotNone(a.s['order'])

    def test_fresh_flow_expires_and_gap_clears(self):
        a=ResearchAccount('a','fresh60',100000,100)
        a.step(event(0),'20260824');a.step(event(30,'sell'),'20260824')
        a.step(event(61,'buy'),'20260824');self.assertIsNotNone(a.s['order'])
        a.step(event(122,'buy'),'20260824');self.assertIsNone(a.s['order'])

    def portfolio(self,cash=15000):
        return SharedPortfolio({c:dict(initial_cents=100000,tick_cents=100) for c in ['a','b']},'base',cash)

    def feed(self,p,second,side):
        for c in ['a','b']:p.step(c,event(second,side),'20260824')
        p.mark(START+second*1000,'20260824')

    def test_pending_orders_reserve_cash_before_fill(self):
        p=self.portfolio();self.feed(p,0,'sell');self.feed(p,1,'buy')
        self.assertIsNotNone(p.accounts['a'].s['order'])
        self.assertIsNone(p.accounts['b'].s['order'])
        self.assertEqual(p.reserved,10270);self.assertEqual(p.cash,15000)
        self.feed(p,2,'sell');self.assertEqual(p.cash,4730)
        self.assertEqual(len(p.fills),1)

    def test_fees_prevent_unfunded_buy(self):
        p=self.portfolio(10100);self.feed(p,0,'sell');self.feed(p,1,'buy')
        self.assertEqual(p.reserved,0);self.feed(p,2,'sell');self.assertEqual(len(p.fills),0)

    def test_sell_releases_cash_and_cash_does_not_reset_next_day(self):
        p=self.portfolio();self.feed(p,0,'sell');self.feed(p,1,'buy');self.feed(p,2,'sell');self.feed(p,3,'buy')
        self.assertEqual(p.cash,15000+1800-340)
        before=p.cash
        e=event(86400,'sell',session=10)
        p.step('a',e,'20260825');p.mark(e.ts,'20260825')
        self.assertEqual(p.cash,before)
        self.assertEqual(p.result()['summary']['pnl_cny'],14.6)

    def test_session_break_releases_pending_buy_reservations(self):
        p=self.portfolio();self.feed(p,0,'sell');self.feed(p,1,'buy')
        p.step('b',event(5400,'sell',session=1),'20260824')
        self.assertEqual(p.reserved,0)
        self.assertIsNone(p.accounts['a'].s['order'])

    def test_simultaneous_marking_does_not_create_artificial_drawdown(self):
        p=self.portfolio();p.pnl=100;p.mark(START,'20260824')
        p.pnl=-100;p.pnl=100;p.mark(START+1000,'20260824')
        self.assertEqual(p.max_dd,0)

    def test_prefix_and_deterministic_tie_order(self):
        a,b=self.portfolio(),self.portfolio()
        for i,side in enumerate(['sell','buy','sell','buy']):
            self.feed(a,i,side)
            if i<3:self.feed(b,i,side)
        self.assertEqual(b.fills,[f for f in a.fills if f['ts']<=START+2000])
        with self.assertRaises(ValueError):a.step('a',event(3),'20260824')

    def test_repricing_cannot_exceed_funded_cash(self):
        p=self.portfolio();self.feed(p,0,'sell');self.feed(p,1,'buy')
        p.step('a',event(2,'buy',bid=16000,ask=18000),'20260824')
        self.assertIsNone(p.accounts['a'].s['order']);self.assertEqual(p.reserved,0)

if __name__=='__main__':unittest.main()
