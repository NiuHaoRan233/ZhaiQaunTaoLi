import unittest
from dataclasses import replace
import numpy as np
import pandas as pd
from zhaiquant import gold_peer_risk_research as m
from zhaiquant.gold_callput_research import Clock,black_option
from zhaiquant.gold_history_validation import timestamp
from test_gold_direction_research import event


class PeerTests(unittest.TestCase):
    def signal(self,fairs):
        return dict(ts=m.parent.g.CUTOFF,session=0,ready=True,future_fair_cents=2000000,
                    peers=[dict(fair_cents=x) for x in fairs])

    def test_consensus_and_direction_mirror(self):
        e=event(m.parent.g.CUTOFF,bid=2000000,ask=2040000)
        s=self.signal([2010000,2010000,2100000])
        self.assertTrue(m.adverse(s,'peer_cancel',e.ts,e,1,2000))
        self.assertFalse(m.adverse(s,'peer_cancel',e.ts,e,-1,2000))
        self.assertFalse(m.adverse(self.signal([2010000,2100000]),'peer_cancel',e.ts,e,1,2000))
        self.assertTrue(m.adverse(self.signal([2040000,2040000]),'peer_cancel',e.ts,e,-1,2000))
        self.assertFalse(m.adverse(s,'peer_cancel',e.ts+2001,e,1,2000))

    def test_target_catchup_clears_warning(self):
        e=event(m.parent.g.CUTOFF,bid=2000000,ask=2040000);s=self.signal([2010000]*3)
        self.assertTrue(m.adverse(s,'peer_cancel',e.ts,e,1,2000))
        caught=replace(e,bid=1990000,ask=2010000)
        self.assertFalse(m.adverse(s,'peer_cancel',caught.ts,caught,1,2000))

    def test_cancel_requests_preserve_ambiguous_interval(self):
        a=m.Account('au2610C976.SF','value',976,1,0,'day','peer_cancel')
        e=event(m.parent.g.CUTOFF,bid=2000000,ask=2040000);a.last_book=e
        a.order=dict(id=1,created_ts=e.ts-500,side='buy',cancel_ts=None)
        a.peer_event(self.signal([2010000]*3))
        self.assertIsNotNone(a.order)
        self.assertEqual(a.order['cancel_ts'],e.ts)
        self.assertTrue(a.rows['cancels'][0]['conservative_interval_settlement'])

    def frames(self):
        clock=Clock('20260911');ts=np.arange(m.parent.g.CUTOFF,m.parent.g.CUTOFF+25001,500,dtype=np.int64)
        def frame(prices):
            return pd.DataFrame(dict(time=ts,bidPrice=[[p-.01] for p in prices],askPrice=[[p+.01] for p in prices],bidVol=[[20]]*len(ts),askVol=[[20]]*len(ts)))
        terms=dict(OptExercisePrice=976,OptionType=0,ExpireDate='20260923')
        maturity=(timestamp('20260923','150000')-ts)/(365*86400000)
        target=frame(m.black(np.full(len(ts),950.),976,maturity,np.full(len(ts),.3),0))
        peers={};pt={}
        for k in (968,984,1000):
            c=str(k);pt[c]=dict(terms,OptExercisePrice=k)
            peers[c]=frame(m.black(np.full(len(ts),950.),k,maturity,np.where(ts>=ts[0]+15000,.29,.3),0))
        return clock,target,peers,frame(np.full(len(ts),950.)),terms,pt

    def test_iv_mapping_prefix_and_boundary(self):
        clock,t,p,f,d,pdct=self.frames();rows,meta=m.signals(t,p,f,d,pdct,clock)
        cut=m.parent.g.CUTOFF+17000
        pr,_=m.signals(t[t.time<=cut],{c:v[v.time<=cut] for c,v in p.items()},f[f.time<=cut],d,pdct,clock)
        self.assertEqual(pr,[r for r in rows if r['ts']<=cut])
        s=next(r for r in rows if r['ts']==cut)
        self.assertTrue(s['ready'])
        self.assertAlmostEqual(s['peers'][0]['iv_change'],-.01,places=8)
        self.assertLess(s['peers'][0]['fair_cents'],s['future_fair_cents'])
        self.assertTrue(all(not r['ready'] for r in rows if r['ts']<m.parent.g.CUTOFF+10000))

    def test_vector_iv_call_and_put(self):
        for kind in (0,1):
            f=np.array([930.,950.,970.]);k=960;t=np.full(3,.05);v=np.array([.2,.3,.4])
            prices=m.black(f,k,t,v,kind)
            np.testing.assert_allclose(m.iv(prices,f,k,t,kind),v,atol=1e-9)
            for i in range(3):self.assertAlmostEqual(prices[i],black_option(f[i],k,t[i],v[i],kind)[0],places=9)


if __name__=='__main__':unittest.main()
