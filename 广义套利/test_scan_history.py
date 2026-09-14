import unittest
import pandas as pd
from scan_history import measure, segments

def frame(times,bids,asks,vols=None):
    return pd.DataFrame(dict(time=[pd.Timestamp('2026-09-11 '+t,tz='Asia/Shanghai').timestamp()*1000 for t in times],
        bidPrice=[[x] for x in bids],askPrice=[[x] for x in asks],bidVol=[[2]]*len(times),askVol=[[3]]*len(times),
        volume=vols or list(range(len(times))),amount=[100*x for x in (vols or list(range(len(times))))],lastPrice=asks))

class MetricsTests(unittest.TestCase):
    def test_time_weighted_and_stale(self):
        f=frame(['09:00:00','09:00:10'],[99,98],[101,102])
        r=measure(f,'20260911',{'OptUnit':10,'PriceTick':.5})
        self.assertAlmostEqual(r['valid_minutes'],70/60)
        self.assertAlmostEqual(r['mean_relative_spread_pct'],(2*10+4*60)/70)
        self.assertAlmostEqual(r['improved_both_gross_cash_per_lot'],((2*10+4*60)/70-1)*10)
    def test_break_not_carried(self):
        f=frame(['10:14:50','10:30:00'],[99,99],[101,101],[1,50])
        r=measure(f,'20260911',{})
        self.assertAlmostEqual(r['valid_minutes'],70/60)
        self.assertEqual(r['incremental_volume'],0)
    def test_zero_and_crossed_excluded(self):
        f=frame(['09:00:00','09:00:10','09:00:20'],[0,103,99],[101,102,101])
        r=measure(f,'20260911',{})
        self.assertEqual(r['valid_minutes'],1)
        self.assertEqual(r['mean_relative_spread_pct'],2)
    def test_volume_reset_not_trade(self):
        f=frame(['09:00:00','09:00:10','09:00:20'],[99]*3,[101]*3,[50,0,2])
        self.assertEqual(measure(f,'20260911',{})['incremental_volume'],2)
    def test_window_durations(self):
        self.assertEqual(sum(b-a for a,b in segments('20260911')),225*60)
        self.assertEqual(sum(b-a for a,b in segments('20260911',bond=True)),270*60)
        self.assertEqual(sum(b-a for a,b in segments('20260911',common=True)),195*60)

if __name__=='__main__':unittest.main()
