import unittest
from zhaiquant import gold_direction_research as g
from zhaiquant.gold_direction_timing_research import ValueState,Account
from tests.test_gold_direction_research import event


class TimingTests(unittest.TestCase):
    def ready(self):
        v=ValueState(960);ts=g.CUTOFF
        for offset in (-60000,-10000,0):v.on_future(g.Future(ts+offset,ts+offset,0,940))
        v.vol=.22;v.vol_ts=ts;v.first=ts-120000;v.updates=20
        return v

    def test_processed_same_time_option_is_available_for_future_cancel(self):
        v=self.ready();self.assertIsNotNone(v.cancellation_feature(g.CUTOFF))
        self.assertIsNone(v.feature(g.CUTOFF))

    def test_unprocessed_later_option_is_never_available(self):
        v=self.ready();v.vol_ts=g.CUTOFF+1
        self.assertIsNone(v.cancellation_feature(g.CUTOFF))

    def test_same_time_future_does_not_cancel_a_valid_new_order(self):
        ts=g.CUTOFF;a=Account('au2610C960.SF','fair_risk_switch','cost',960)
        a.value=self.ready();value=a.value.cancellation_feature(ts)['fair_cents'];bid=int(value//2000)*2000-10000
        e=event(ts,bid=bid,ask=bid+20000);a.last_book=e;a.session=0;a.flow.extend([(ts,'buy'),(ts,'sell')])
        a.issue(ts,'buy',bid+2000,'entry',e)
        a.value.future=None # The pending tied future has not yet been processed.
        a.on_future(g.Future(ts,ts,0,940));self.assertNotIn('cancel_ts',a.order)


if __name__=='__main__':unittest.main()
