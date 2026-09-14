import unittest
from dataclasses import replace
from zhaiquant import gold_direction_research as g
from zhaiquant.gold_history_validation import Clock, ValueState, timestamp, timeline, DAY
from tests.test_gold_direction_research import event


class HistoryDateTests(unittest.TestCase):
    def test_expiry_uses_actual_calendar_days(self):
        old=ValueState(960,'20260923',Clock('20260907'))
        new=ValueState(960,'20260923',Clock('20260911'))
        self.assertAlmostEqual(old.maturity(g.END)-new.maturity(g.END),4/365)
        self.assertAlmostEqual(old.maturity(g.END),16/365)

    def test_new_month_uses_own_expiry(self):
        a=ValueState(1000,'20261124',Clock('20260907'))
        self.assertAlmostEqual(a.maturity(g.END),78/365)

    def test_output_restores_nested_sources_but_not_durations_or_money(self):
        clock=Clock('20260907')
        x={'ts':g.START,'entry_signal':{'future_source_ts':g.START+500,'move10_cents':10000},
           'curve':[[g.END,12000,0]],'funding_demands':[[g.START,2000000,2000000]],
           'duration_seconds':3600,'cancel_ts':None}
        actual=clock.restore(x)
        self.assertEqual(actual['ts'],timestamp('20260907'))
        self.assertEqual(actual['entry_signal']['future_source_ts'],timestamp('20260907')+500)
        self.assertEqual(actual['curve'],[[timestamp('20260907','150000'),12000,0]])
        self.assertEqual(actual['funding_demands'][0][1],2000000)
        self.assertEqual(actual['duration_seconds'],3600)
        self.assertIsNone(actual['cancel_ts'])

    def test_no_global_clock_mutation(self):
        dates=(g.START,g.END,g.DATE,g.BOUNDARIES)
        ValueState(960,'20260923',Clock('20260907')).maturity(g.START)
        self.assertEqual((g.START,g.END,g.DATE,g.BOUNDARIES),dates)

    def test_option_processed_before_same_timestamp_future(self):
        clock=Clock('20260907');e=event(g.CUTOFF)
        f=g.Future(e.ts,e.ts,0,940)
        rows=timeline([e],[f],960,'20260923',clock,cut=e.ts)
        self.assertEqual([x[0] for x in rows],[0,1])
        self.assertIsNone(rows[0][-1])

    def test_actual_prefix_and_full_timeline_agree(self):
        clock=Clock('20260907')
        es=[event(g.CUTOFF+i*1000) for i in range(5)]
        fs=[g.Future(g.CUTOFF+i*1000-500,g.CUTOFF+i*1000-500,0,940) for i in range(5)]
        cut=g.CUTOFF+2000
        full=timeline(es,fs,960,'20260923',clock)
        pre=timeline([e for e in es if e.ts<=cut],[f for f in fs if f.ts<=cut],960,'20260923',clock,cut)
        self.assertEqual(pre,[x for x in full if (x[1] if x[0]==-1 else x[1].ts)<=cut])

    def test_selector_uses_actual_expiry_and_excludes_post_selection_data(self):
        import pandas as pd
        clock=Clock('20260907');start=clock.start
        rows=[]
        for i in range(4):
            rows.append(dict(time=start+1680000+i*59000,bidPrice=[10.,9.9],askPrice=[10.2,10.3],
                bidVol=[1,1],askVol=[1,1],volume=10+i,amount=100000+i*10000,lastPrice=10.))
        frame=pd.DataFrame(rows)
        terms=dict(ExpireDate='20260923',OptUnit=1000,PriceTick=.02,OptExercisePrice=960,OptionType=0)
        future=dict(mid=950,ts=g.CUTOFF-500)
        result=clock.metrics(frame,terms,future)
        self.assertEqual(result['days_to_expiry'],16)
        self.assertEqual(result,clock.metrics(frame[frame.time<start+1800000],terms,future))


if __name__=='__main__':unittest.main()
