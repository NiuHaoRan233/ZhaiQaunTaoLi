import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from probe_gold_break_entry_diagnostic import Account
from zhaiquant import gold_direction_research as g
from tests.test_gold_direction_research import event


class EntryCutoffDiagnosticTests(unittest.TestCase):
    def account(self):
        a=Account('test','trend_long',960)
        a.features={1:dict(ready=True,fair_cents=2100000,move10_cents=0,move60_cents=0),-1:None}
        return a

    def test_entry_is_allowed_before_cutoff_and_rejected_at_cutoff(self):
        a=self.account();cut=g.BOUNDARIES[0]-60000
        self.assertIsNotNone(a.candidate(event(cut-1000),None)[0])
        self.assertEqual(a.candidate(event(cut),None),(None,'entry_cutoff60_diagnostic'))

    def test_old_aggregate_fill_is_not_erased_by_new_cutoff(self):
        a=self.account();cut=g.BOUNDARIES[0]-60000;old=event(cut-2000)
        a.session=0;a.last_book=old;a.chosen_feature=a.features[1]
        a.issue(old.ts,'buy',2002000,'entry',old)
        a.option(event(cut))
        self.assertEqual(a.inventory,1)
        self.assertEqual(a.rows['fills'][0]['kind'],'passive')

    def test_exit_still_uses_original_last5seconds_opposite_quote(self):
        a=self.account();cut=g.BOUNDARIES[0]-60000;old=event(cut-2000)
        a.session=0;a.last_book=old;a.chosen_feature=a.features[1]
        a.issue(old.ts,'buy',2002000,'entry',old);a.option(event(cut))
        a.option(event(g.BOUNDARIES[0]-4000))
        self.assertEqual(a.inventory,0)
        self.assertEqual(a.rows['fills'][-1]['kind'],'pre_break_market_close')


if __name__=='__main__':unittest.main()
