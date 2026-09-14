import unittest
from dataclasses import replace
from zhaiquant.commodity_mau_depth_audit import DepthAccount
from tests.test_commodity_intraday_research import event, DATE


class DepthTests(unittest.TestCase):
    def account(self,profile,bids):
        a=DepthAccount('a',profile,100000,100)
        for i,side in enumerate(['sell','buy']):a.step(replace(event(i,side),bids=bids),DATE)
        return a

    def test_presence_control_permits_isolated_quote(self):
        a=self.account('depth_only',((10000,2),(8000,2)))
        self.assertIsNotNone(a.s['order'])
        self.assertIsNone(self.account('depth_only',((10000,2),)).s['order'])

    def test_observed_gap_filter_does_not_impute_missing_depth(self):
        self.assertIsNotNone(self.account('gap_when_observed',((10000,2),)).s['order'])
        self.assertIsNone(self.account('gap_when_observed',((10000,2),(8000,2))).s['order'])

    def test_new_isolation_cannot_erase_old_fill(self):
        a=self.account('gap_when_observed',((10000,2),(9900,2)))
        r=a.step(replace(event(2,'sell'),bids=((10000,2),(8000,2))),DATE)
        self.assertEqual(len(r['fills']),1)
        self.assertIsNotNone(r['fills'][0]['entry_signal'])


if __name__=='__main__':unittest.main()
