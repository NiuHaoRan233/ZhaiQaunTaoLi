from dataclasses import replace
import unittest
import pandas as pd
from zhaiquant.commodity_dadao_research import load_frame, run, audit_result, price_cents, FAMILY


def frame(volumes, *, asks=None, bids=None, amounts=None, seconds=None, lasts=None):
    n=len(volumes); asks=asks or [110]*n; bids=bids or [100]*n
    lasts=lasts or [100]*n; seconds=seconds or list(range(n))
    return pd.DataFrame(dict(time=[pd.Timestamp('2026-09-11 09:00',tz='Asia/Shanghai').value//1_000_000+s*1000 for s in seconds],
        bidPrice=[[x] for x in bids],askPrice=[[x] for x in asks],bidVol=[[2]]*n,askVol=[[2]]*n,
        lastPrice=lasts,volume=volumes,amount=amounts or [v*1000 for v in volumes],transactionNum=[0]*n))


class CommodityDadaoTests(unittest.TestCase):
    def load(self,f):return load_frame(f,code='test.DF',date='20260911',detail={'OptUnit':10,'PriceTick':.5})
    def execute(self,f,mode='last_d500',fee=500):
        e,u,m=self.load(f)
        r=run(e,u,code='test.DF',date='20260911',mode=mode,fee_cents=fee,
              initial_cents=m['initial_cents'],tick_cents=m['tick_cents'])
        self.assertTrue(audit_result(r))
        return r
    def test_zero_trade_counter_does_not_mean_zero_evidence(self):
        e,u,m=self.load(frame([0,2,3],amounts=[0,2000,3000]))
        self.assertFalse(m['trade_count_available']);self.assertEqual(m['usable_last_events'],2)
        self.assertEqual(u,[False,False,True])
    def test_aggregated_volume_never_assigned_to_primary_price(self):
        r=self.execute(frame([0,10,20]),mode='last_d0')
        self.assertEqual(r['fills'][0]['quantity'],1)
        self.assertEqual(r['fills'][0]['source_quantity'],1)
        self.assertTrue(r['fills'][0]['model_id'].startswith(FAMILY))
        self.assertNotIn('source_single',r['fills'][0])
    def test_unit_sensitivity_rejects_aggregate(self):
        r=self.execute(frame([0,10,20]),mode='unit_d500')
        self.assertEqual(r['summary']['fill_count'],0)
    def test_realized_round_and_fee(self):
        r=self.execute(frame([0,0,1,1,2],lasts=[100,100,100,100,110],amounts=[0,0,1000,1000,2100]))
        self.assertEqual(r['summary']['complete_cycles'],1)
        self.assertEqual(r['summary']['pnl_cny'],80)
        self.assertEqual(r['summary']['fees_cny'],10)
    def test_one_second_delay_needs_preexisting_active_order(self):
        r=self.execute(frame([0,1,2]),mode='last_d1000')
        self.assertEqual([f['ts'] for f in r['fills']],[int(frame([0,1,2]).time.iloc[2])])
    def test_old_order_can_fill_during_cancel_latency(self):
        r=self.execute(frame([0,0,0,1],bids=[100,100,90,90],asks=[110,110,100,100],
                             lasts=[100,100,100,90],amounts=[0,0,0,900]))
        self.assertEqual(r['fills'][0]['price_cents'],100500)
    def test_price_unit_is_contract_cash_not_bond_hand(self):
        self.assertEqual(price_cents(.02,1000),2000)
        self.assertEqual(price_cents(.5,5),250)
        self.assertEqual(price_cents(.001,10),1)
    def test_long_gap_and_volume_reset_not_trade(self):
        e,u,m=self.load(frame([5,0,1],seconds=[0,1,65]))
        self.assertEqual(m['cumulative_resets'],1)
        self.assertEqual(m['usable_last_events'],0)
    def test_prefix_orders_and_fills(self):
        f=frame([0,0,1,1,2,2,3],lasts=[100,100,100,100,110,100,100],amounts=[0,0,1000,1000,2100,2100,3100])
        whole=self.execute(f);part=self.execute(f.iloc[:4]);ts=int(f.time.iloc[3])
        self.assertEqual(part['fills'],[x for x in whole['fills'] if x['ts']<=ts])
        self.assertEqual(part['orders'],[x for x in whole['orders'] if x['created_ts']<=ts])
    def test_stale_tail_is_disclosed(self):
        f=frame([0,0,1,1],seconds=[0,1,2,125],bids=[100,100,100,0],asks=[110,110,110,0])
        self.assertTrue(self.execute(f)['summary']['stale_tail'])


if __name__=='__main__':unittest.main()
