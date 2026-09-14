import unittest
from zhaiquant import gold_direction_research as g
from zhaiquant.gold_aligned_value_research import ValueState,Account
from tests.test_gold_direction_research import event


class AlignedTests(unittest.TestCase):
    def test_option_calibration_waits_for_same_time_future(self):
        v=ValueState(960);ts=g.CUTOFF;v.on_future(g.Future(ts-500,ts-500,0,940));v.observe_option(event(ts))
        self.assertIsNone(v.vol)
        v.on_future(g.Future(ts,ts,0,941))
        self.assertEqual(v.calibrations[-1]['future_source_ts'],ts)
        self.assertIsNone(v.feature(ts))

    def test_future_after_option_is_only_a_watermark_not_a_pair(self):
        v=ValueState(960);ts=g.CUTOFF;v.on_future(g.Future(ts-500,ts-500,0,940));v.observe_option(event(ts))
        v.on_future(g.Future(ts+500,ts+500,0,950))
        expected=g.implied_vol(20.1,940,960,v.maturity(ts))
        self.assertAlmostEqual(v.vol,expected)
        self.assertEqual(v.calibrations[-1]['calibration_available_ts'],ts+500)

    def test_delay_changes_availability_not_calibration(self):
        a,b=ValueState(960),ValueState(960);ts=g.CUTOFF
        for v,delay in ((a,0),(b,1000)):
            v.observe_option(event(ts));v.on_future(g.Future(ts+delay,ts,0,940))
        self.assertEqual(a.vol,b.vol)
        self.assertEqual(b.vol_ts-a.vol_ts,1000)

    def test_new_session_discards_unpaired_old_option(self):
        v=ValueState(960);v.observe_option(event(g.BOUNDARIES[0]-500))
        v.on_future(g.Future(g.SESSION_STARTS[1],g.SESSION_STARTS[1],1,940))
        self.assertIsNone(v.vol);self.assertFalse(v.pending)

    def test_fixed_target_does_not_chase_upward_ask_or_lose_priority(self):
        a=Account('au2610C960.SF','aligned_fixed_take','cost',960)
        a.inventory=1;a.cycle=dict(entry_price_cents=2002000,entry_spread=20000)
        e=event(g.CUTOFF,bid=2000000,ask=2040000)
        a.issue(e.ts,'sell',2038000,'patient_exit',e)
        self.assertEqual(a.order['price'],2018000)
        oid=a.order['id'];a.issue(e.ts+1000,'sell',2048000,'patient_exit',e)
        self.assertEqual(a.order['id'],oid)

    def test_fair_cap_preserves_cost_protection_and_passivity(self):
        a=Account('au2610C960.SF','aligned_fair_take','cost',960)
        a.inventory=1;a.cycle=dict(entry_price_cents=2002000,entry_spread=20000)
        e=event(g.CUTOFF);f=dict(ready=True,fair_cents=1980000)
        a.issue(e.ts,'sell',2018000,'patient_exit',e,f)
        self.assertEqual(a.order['price'],2006000)
        a.released=True;a.issue(e.ts+1000,'sell',2018000,'top_exit',e,f)
        self.assertEqual(a.order['price'],e.bid+2000)


if __name__=='__main__':unittest.main()
