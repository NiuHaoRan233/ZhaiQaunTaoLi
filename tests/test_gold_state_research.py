import unittest
from dataclasses import replace
import pandas as pd
from zhaiquant import gold_state_research as g
from zhaiquant.option_top_cycle_research import Event
from zhaiquant.commodity_mau_transfer_research import TransferAccount


def frame():
    return pd.DataFrame([dict(time=g.START+i*1000,bidPrice=[20.,19.98],askPrice=[20.2,20.22],
        bidVol=[2,2],askVol=[2,2],volume=i,amount=i*20100.,lastPrice=20. if i%2 else 20.2,
        openInt=1000) for i in range(1800)])


DETAIL=dict(OptUnit=1000.,PriceTick=.02,OptExercisePrice=1000.,OptionType=0,ExpireDate='20261124')
UNDERLYING=dict(mid=960.,ts=g.CUTOFF-1000)


class GoldStateTests(unittest.TestCase):
    def test_future_data_cannot_change_selection(self):
        f=frame();before=g.state_metrics(f,DETAIL,UNDERLYING)
        extra=f.iloc[-1:].copy();extra['time']=g.CUTOFF;extra['volume']=1000000
        self.assertEqual(before,g.state_metrics(pd.concat([f,extra]),DETAIL,UNDERLYING))
        self.assertTrue(before['eligible'])

    def test_two_tick_spread_fails_despite_percentage(self):
        f=frame();f['bidPrice']=[[3.62,3.60] for _ in range(len(f))];f['askPrice']=[[3.66,3.68] for _ in range(len(f))]
        m=g.state_metrics(f,DETAIL,UNDERLYING)
        self.assertIn('few_net_edge_intervals',m['reasons']);self.assertAlmostEqual(m['median_relative_spread_pct'],1.0989010989)

    def test_call_put_moneyness_reverses(self):
        a=g.state_metrics(frame(),DETAIL,UNDERLYING);b=g.state_metrics(frame(),dict(DETAIL,OptionType=1),UNDERLYING)
        self.assertEqual(a['moneyness_state'],'out_of_money');self.assertEqual(b['moneyness_state'],'in_money')

    def test_static_quotes_are_valid_but_no_trade_activity_fails(self):
        f=frame();f['volume']=100;f['amount']=100000
        m=g.state_metrics(f,DETAIL,UNDERLYING)
        self.assertGreater(m['valid_time_fraction'],.99);self.assertIn('insufficient_two_sided_flow',m['reasons'])

    def test_historical_underlying_ignores_future_open_interest(self):
        f=frame();state=g.future_state(f);extra=f.iloc[-1:].copy();extra['time']=g.CUTOFF;extra['openInt']=999999
        self.assertEqual(state,g.future_state(pd.concat([f,extra])))

    def test_account_starts_after_selection_and_matches_frozen_parent(self):
        a=g.GoldAccount('au2612C1000.SF','gap',2000)
        b=TransferAccount('au2612C1000.SF','gap',15000000,2000)
        def event(i,side):
            return Event(g.CUTOFF+i*1000,g.CUTOFF+(i-1)*1000,0,2000000,2020000,2,2,
                ((2000000,2),(1998000,2)),((2020000,2),),2000000 if side=='sell' else 2020000,1,0,side,True,side)
        with self.assertRaises(ValueError):a.step(event(-1,'sell'),g.DATE)
        for i,side in enumerate(['sell','buy','sell']):
            x,y=a.step(event(i,side),g.DATE),b.step(event(i,side),g.DATE)
            self.assertEqual(x['curve'],y['curve']);self.assertEqual(x['cycles'],y['cycles'])
        a.close_day(g.DATE);b.close_day(g.DATE)
        self.assertEqual(a.daily(),b.daily());self.assertEqual(a.summary()['pnl_cny'],-3.4)
        self.assertNotEqual(a.model,b.model)


if __name__=='__main__':unittest.main()
