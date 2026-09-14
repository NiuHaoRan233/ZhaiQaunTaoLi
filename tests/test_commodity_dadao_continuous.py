import unittest
from dataclasses import replace
from zhaiquant.commodity_dadao_continuous import run
from zhaiquant.option_top_cycle_research import Event


def ev(ts,bid=10000,ask=11000,qty=0,last=10000,side='sell',prev=None):
    return Event(ts,ts-1000 if prev is None else prev,0,bid,ask,10,10,((bid,10),),((ask,10),),last,qty,0,side,True,side)


class ContinuousTests(unittest.TestCase):
    def inputs(self):
        a=[ev(1000),ev(2000),ev(3000,qty=1),ev(4000,bid=5000,ask=11000)]
        b=[ev(100000,bid=10000,ask=11000),ev(101000),ev(102000,qty=1,last=11000,side='buy')]
        meta=dict(unit=10,tick_cents=100,initial_cents=1000000)
        return [(a,[True]*4,dict(meta,date='20260824')),(b,[True]*3,dict(meta,date='20260825'))]
    def test_overnight_position_and_cash_do_not_reset(self):
        r=run(self.inputs(),code='test',mode='last_d500',fee_cents=500)
        self.assertEqual(r['summary']['complete_cycles'],1)
        self.assertEqual([f['side'] for f in r['fills']],['buy','sell'])
        self.assertEqual(r['summary']['pnl_cny'],-2)
        self.assertEqual(r['summary']['end_inventory'],0)
        self.assertEqual(r['daily'][0]['end_inventory'],1)
        self.assertAlmostEqual(sum(x['pnl_cny'] for x in r['daily']),-2)
    def test_next_day_initial_cash_is_ignored(self):
        inp=self.inputs();inp[1][2]['initial_cents']=999999999
        self.assertEqual(run(inp,code='test',mode='last_d500',fee_cents=500)['summary']['initial_cash_cny'],10000)
    def test_missing_night_never_creates_trade(self):
        r=run(self.inputs(),code='test',mode='last_d500',fee_cents=500)
        self.assertEqual([f['ts'] for f in r['fills']],[3000,102000])
        self.assertGreater(r['summary']['max_drawdown_cny'],50)


if __name__=='__main__':unittest.main()
