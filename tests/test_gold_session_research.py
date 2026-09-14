import unittest
from dataclasses import replace
import pandas as pd
from zhaiquant import gold_session_research as m
from zhaiquant import gold_reselection_research as parent
from zhaiquant import gold_direction_research as g
from zhaiquant.gold_callput_research import Clock
from tests.test_gold_direction_research import event
from tests.test_gold_backer_research import feat


class SessionTests(unittest.TestCase):
    def step(self,a,e):
        a.features={1:feat(),-1:feat()};a.value.future=g.Future(e.ts-500,e.ts-500,e.session,960);a.option(e)

    def test_day_execution_matches_parent(self):
        for policy in ('trend','trend_cancel','trend_fast','trend_exit','value','value_exit','backer_entry','backer_guard','backer_fast'):
            for d in (1,-1):
                a=m.Account('au2610C976.SF',policy,976,d,100,0,'day')
                b=parent.Account('au2610C976.SF',policy,976,d,100,0)
                for i,side in enumerate(('sell','buy','sell','buy')):
                    e=event(g.CUTOFF+i*1000,side=side,bid=2000000,ask=2040000,quantity=int(i>0))
                    self.step(a,e);self.step(b,e)
                for ts in g.BOUNDARIES:a.boundary(ts);b.boundary(ts)
                for k in ('pnl_cny','market_cycles','virtual_close_count','removed_tail_gross_cny'):
                    self.assertEqual(a.result()['summary'][k],b.result()['summary'][k])

    def test_midnight_is_not_a_break_and_night_end_excludes(self):
        a=m.Account('au2610C976.SF','trend',976,1,100,0,'night')
        midnight=m.NIGHT_START+3*3600000
        self.step(a,event(midnight-1500,session=3,bid=2000000,ask=2040000,quantity=0))
        self.step(a,event(midnight,session=3,bid=2000000,ask=2040000,side='sell'))
        self.assertEqual(a.inventory,1);self.assertEqual(len(a.rows['boundaries']),0)
        a.boundary(m.NIGHT_END)
        self.assertEqual(a.inventory,0);self.assertEqual(a.cash,a.initial)

    def test_excluded_session_cannot_enter(self):
        a=m.Account('au2610C976.SF','trend',976,1,100,0,'pm')
        self.assertEqual(a.candidate(event(g.CUTOFF),None),(None,'session_filter'))
        with self.assertRaises(ValueError):a.option(event(g.CUTOFF))

    def test_night_loader_keeps_cross_midnight_volume_and_blocks_reset(self):
        clock=Clock('20260911');midnight=m.NIGHT_START+3*3600000
        rows=[]
        for i,vol in enumerate((10,11,0,1)):
            rows.append(dict(time=midnight-1000+i*500,volume=vol,amount=vol*20000,transactionNum=0,
                bidPrice=[20],askPrice=[20.4],bidVol=[10],askVol=[10],lastPrice=20))
        f=pd.DataFrame(rows)
        es,fs,meta=m.night_inputs(f,f,clock,dict(OptUnit=1000))
        self.assertEqual([e.session for e in es],[3]*4)
        self.assertEqual([e.quantity for e in es],[0,1,0,1]);self.assertEqual(meta['cumulative_resets'],1)
        self.assertEqual(es[1].previous_ts,es[0].ts)


if __name__=='__main__':unittest.main()
