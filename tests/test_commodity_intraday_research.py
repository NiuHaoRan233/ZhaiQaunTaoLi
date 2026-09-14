import unittest
from dataclasses import replace

from zhaiquant.commodity_intraday_research import IntradayAccount,IntradayPortfolio,close_timestamp
from zhaiquant.commodity_capital_research import ResearchAccount
from zhaiquant.option_top_cycle_research import Event

START=1787533200000
DATE='20260824'

def event(sec,side='sell',bid=10000,ask=12000,volume=1):
    return Event(START+sec*1000,START+(sec-1)*1000,0,bid,ask,2,2,((bid,2),),((ask,2),),
        bid if side=='sell' else ask,volume,0,side,True,side)

def bought():
    a=IntradayAccount('a','base',100000,100)
    for i,side in enumerate(['sell','buy','sell']):a.step(event(i,side),DATE)
    return a

class IntradayCostTests(unittest.TestCase):
    def test_loss_position_returns_cost_and_still_pays_both_fees(self):
        a=bought();a.step(event(3,'sell',bid=7000,ask=8000),DATE)
        d=a.close_day(DATE);s=a.summary()
        self.assertEqual(s['pnl_cny'],-3.4);self.assertEqual(s['end_inventory'],0)
        self.assertEqual(d['fills'][0]['price_cents'],10100)
        self.assertEqual(s['quote_mark_pnl_before_virtual_close_cny'],-31)
        self.assertEqual(s['virtual_mark_adjustment_cny'],31)
        self.assertEqual(d['fills'][0]['source_last_contract_evidence'],False)
        self.assertEqual(d['cycles'][0]['gross_cents'],0)

    def test_profitable_unclosed_position_also_resets_gross_to_zero(self):
        a=bought();a.step(event(3,'sell',bid=14000,ask=16000,volume=0),DATE)
        a.close_day(DATE)
        self.assertEqual(a.summary()['pnl_cny'],-3.4)
        self.assertEqual(a.summary()['quote_mark_pnl_before_virtual_close_cny'],39)

    def test_market_closed_profit_is_retained(self):
        a=bought();d=a.step(event(3,'buy'),DATE);self.assertEqual(len(d['fills']),1)
        a.close_day(DATE);s=a.summary()
        self.assertEqual(s['pnl_cny'],14.6)
        self.assertEqual(s['market_cycle_net_cny'],14.6)
        self.assertEqual(s['virtual_close_count'],0)

    def test_unfilled_order_cancels_without_fee(self):
        a=IntradayAccount('a','base',100000,100)
        a.step(event(0),DATE);a.step(event(1,'buy'),DATE);d=a.close_day(DATE)
        self.assertFalse(d['fills']);self.assertIsNone(a.s['order']);self.assertEqual(a.s['cash'],100000)

    def test_cannot_settle_twice_or_trade_after_close(self):
        a=bought();a.close_day(DATE)
        with self.assertRaises(ValueError):a.close_day(DATE)
        with self.assertRaises(ValueError):a.step(event(4),DATE)

    def test_next_day_cash_not_reset_and_no_overnight_inventory(self):
        a=bought();a.close_day(DATE)
        e=replace(event(86400),session=10);a.step(e,'20260825')
        self.assertEqual(a.s['cash'],99660);self.assertEqual(a.s['inventory'],0)
        a.close_day('20260825')
        self.assertEqual(a.daily()[1]['pnl_cny'],0)

    def test_missing_date_is_not_invented_as_observed_day(self):
        a=IntradayAccount('a','base',100000,100);d=a.close_day(DATE)
        self.assertFalse(d['fills']);self.assertEqual(a.daily(),[])

    def test_intraday_execution_identical_before_virtual_close(self):
        a=IntradayAccount('a','edge10',100000,100);b=ResearchAccount('a','edge10',100000,100)
        for i,side in enumerate(['sell','buy','sell','buy']):
            x,y=a.step(event(i,side),DATE),b.step(event(i,side),DATE)
            self.assertEqual(x['curve'],y['curve'])
            self.assertEqual(x['cycles'],y['cycles'])

    def test_shared_wallet_settles_all_positions_and_releases_reserves(self):
        p=IntradayPortfolio({c:dict(initial_cents=100000,tick_cents=100) for c in ['a','b']},'base',30000)
        for i,side in enumerate(['sell','buy','sell']):
            for c in ['a','b']:p.step(c,event(i,side),DATE)
            p.mark(event(i).ts,DATE)
        p.close_day(DATE);r=p.result()
        self.assertEqual(r['summary']['pnl_cny'],-6.8)
        self.assertEqual(p.cash,29320);self.assertEqual(p.reserved,0)
        self.assertEqual(r['summary']['virtual_close_count'],2)
        self.assertTrue(all(a['daily'][0]['end_inventory']==0 for a in r['accounts'].values()))

    def test_closing_timestamp_is_china_1500(self):
        self.assertEqual(close_timestamp(DATE),START+6*3600000)

if __name__=='__main__':unittest.main()
