import unittest
from zhaiquant import gold_reselection_research as m
from zhaiquant import gold_callput_research as v
from zhaiquant import gold_direction_research as g
from tests.test_gold_direction_research import event


class ReselectionTests(unittest.TestCase):
    def test_percentage_state_and_selection_ignore_future_rows(self):
        import pandas as pd
        clock=v.Clock('20260911');start=clock.start
        rows=[]
        for i in range(3600):
            rows.append(dict(time=start+i*500,bidPrice=[9.95,9.93],askPrice=[10.05,10.07],bidVol=[10,10],askVol=[10,10],
                volume=i,amount=i*10000,lastPrice=9.95 if i%2 else 10.05))
        f=pd.DataFrame(rows);detail=dict(OptUnit=1000,PriceTick=.02,OptExercisePrice=960,OptionType=0,ExpireDate='20260923')
        underlying=dict(mid=960,ts=start+1799500)
        a=m.metrics(f,detail,underlying,clock,100)
        self.assertTrue(a['eligible'],a['reasons'])
        self.assertFalse(m.metrics(f,detail,underlying,clock,150)['eligible'])
        future=f.copy();future['time']+=1800000;future['lastPrice']=999
        self.assertEqual(a,m.metrics(pd.concat([f,future]),detail,underlying,clock,100))

    def test_old_contracts_never_selected(self):
        rows={c:dict(eligible=True,balanced_relative_updates=100,positive_net_relative_time_fraction=.8) for c in m.EXCLUDED}
        rows['new']=dict(eligible=True,balanced_relative_updates=3,positive_net_relative_time_fraction=.5)
        self.assertEqual(m.select(rows),['new'])
        with self.assertRaises(ValueError):m.Account(m.EXCLUDED[0],'trend',960,1,100,0)

    def test_ranking_uses_activity_then_coverage_never_profit(self):
        rows={'a':dict(eligible=True,balanced_relative_updates=4,positive_net_relative_time_fraction=.5,pnl=-999),
            'b':dict(eligible=True,balanced_relative_updates=3,positive_net_relative_time_fraction=1,pnl=999),
            'c':dict(eligible=False,balanced_relative_updates=999,positive_net_relative_time_fraction=1)}
        self.assertEqual(m.select(rows),['a','b'])

    def test_put_call_parity_delta_and_iv(self):
        for f in (900,950,1000):
            c,cd=v.black_option(f,960,20/365,.25,0);p,pd=v.black_option(f,960,20/365,.25,1)
            self.assertAlmostEqual(c-p,f-960);self.assertAlmostEqual(cd-pd,1)
            self.assertTrue(-1<pd<0)
            self.assertAlmostEqual(v.implied_vol(p,f,960,20/365,1),.25,places=8)

    def test_call_timeline_exact_parent_match_and_put_causality(self):
        from zhaiquant.gold_history_validation import timeline as old
        clock=v.Clock('20260911');t=g.CUTOFF
        es=[event(t+i*1000,quantity=0) for i in range(80)]
        fs=[g.Future(t+i*500,t+i*500,0,960+i*.001) for i in range(160)]
        new=v.timeline(es,fs,960,'20260923',clock,0)
        self.assertEqual(new,old(es,fs,960,'20260923',clock))
        put=v.timeline(es,fs,960,'20260923',clock,1)
        cut=t+70000
        self.assertEqual(v.timeline([e for e in es if e.ts<=cut],[f for f in fs if f.ts<=cut],960,'20260923',clock,1,cut),
            [r for r in put if (r[1] if r[0]==-1 else r[1].ts)<=cut])
        for _,e,feats,_,_ in put:
            if feats and feats[1]:self.assertLess(feats[1]['delta'],0)

    def test_put_future_rise_is_adverse_to_long_not_short(self):
        from zhaiquant.gold_backer_research import fast_bad
        feat=dict(ready=True,delta=-.5,future_mid=961,move2_cents=-5000,move10_cents=-5000)
        self.assertTrue(fast_bad(feat,1,2000)[0]);self.assertFalse(fast_bad(feat,-1,2000)[0])


if __name__=='__main__':unittest.main()
