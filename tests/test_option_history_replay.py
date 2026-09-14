import unittest
import pandas as pd
from zhaiquant.option_history_replay import load_day,coverage
from zhaiquant.option_top_cycle_research import Event

DETAILS={'OptUnit':10000,'PriceTick':.0001}


def ts(date,clock):return int(pd.Timestamp(date+' '+clock,tz='Asia/Shanghai').value//10**6)
def row(date,clock,volume=0,amount=0,transactions=0):
    return dict(time=ts(date,clock),bidPrice=[.9,.89,0,0,0],askPrice=[1.,1.01,0,0,0],
                bidVol=[10,20,0,0,0],askVol=[10,20,0,0,0],lastPrice=.9,
                volume=volume,amount=amount,transactionNum=transactions)


class OptionMonthInputTests(unittest.TestCase):
    def test_dates_not_silently_mixed_or_reset_across_days(self):
        f=pd.DataFrame([row('2026-08-10','09:30:00'),row('2026-08-11','09:30:00')])
        with self.assertRaises(AssertionError):load_day(f,'20260810',DETAILS)

    def test_first_cumulative_volume_is_not_an_invented_trade(self):
        f=pd.DataFrame([row('2026-08-10','09:30:00',5,45000,3),row('2026-08-10','09:30:00.500',6,54000,4)])
        events,meta=load_day(f,'20260810',DETAILS)
        self.assertEqual(events[0].quantity,0)
        self.assertEqual(events[1].quantity,1)
        self.assertTrue(events[1].single)
        self.assertEqual(events[1].strict_side,'sell')
        self.assertEqual(meta['cumulative_resets'],0)

    def test_cumulative_reset_is_reported_for_exclusion(self):
        f=pd.DataFrame([row('2026-08-10','09:30:00',5,45000,3),row('2026-08-10','09:30:01',1,9000,1)])
        events,meta=load_day(f,'20260810',DETAILS)
        self.assertEqual(meta['cumulative_resets'],1)
        self.assertEqual(events[1].quantity,0)

    def test_quote_carry_caps_at_30s_and_never_bridges_lunch(self):
        date='20260810'
        def e(clock,session):
            t=ts('2026-08-10',clock)
            return Event(t,t,session,10000,11000,1,1,((10000,1),),((11000,1),))
        result=coverage([e('11:29:50',0),e('13:00:00',1)],date)
        self.assertAlmostEqual(result['valid_book_coverage'],40000/(237*60000))
        self.assertEqual(result['time_weighted_spread_cny'],10)

    def test_nonstandard_contract_rejected(self):
        with self.assertRaises(AssertionError):
            load_day(pd.DataFrame([row('2026-08-10','09:30:00')]),'20260810',dict(DETAILS,OptUnit=10200))


if __name__=='__main__':unittest.main()
