import unittest
from dataclasses import replace
from zhaiquant import gold_direction_research as g
from zhaiquant.gold_rule_ladder_research import Account, cases
from tests.test_gold_direction_research import event


class GoldRuleLadderTests(unittest.TestCase):
    def test_raw_cycle_does_not_require_flow_or_second_depth(self):
        a=Account('test','s00',1,960)
        e=replace(event(g.CUTOFF),bids=((2000000,1),),quantity=0)
        a.option(e)
        self.assertEqual(a.order['price'],e.bid)

    def test_improvement_does_not_cross_one_tick_spread(self):
        a=Account('test','s01',1,960); e=event(g.CUTOFF,ask=2002000)
        a.option(e); self.assertEqual(a.order['price'],e.bid)

    def test_spread_accounts_for_both_improvements_and_fees(self):
        a=Account('test','s02',1,960)
        self.assertIsNone(a.candidate(event(g.CUTOFF,ask=2006000),None)[0])
        self.assertIsNotNone(a.candidate(event(g.CUTOFF,ask=2008000),None)[0])

    def test_flow_and_depth_are_independent_steps(self):
        e=replace(event(g.CUTOFF),bids=((2000000,1),))
        a=Account('test','s03',1,960); a.flow.extend([(e.ts,'buy'),(e.ts,'sell')])
        self.assertIsNotNone(a.candidate(e,None)[0])
        b=Account('test','s04',1,960); b.flow=a.flow
        self.assertEqual(b.candidate(e,None)[1],'missing_second_depth')

    def test_short_is_symmetric_and_closes_at_ask(self):
        a=Account('test','s00',-1,960)
        a.value.future=g.Future(g.CUTOFF-500,g.CUTOFF-500,0,940)
        a.option(event(g.CUTOFF,'buy')); a.option(event(g.CUTOFF+1000,'buy'))
        self.assertEqual(a.inventory,-1)
        a.option(event(g.BOUNDARIES[0]-4000,ask=2100000,quantity=0))
        self.assertEqual(a.rows['fills'][-1]['price_cents'],2100000)
        self.assertLess(a.rows['cycles'][-1]['net_cents'],0)

    def test_cost_floor_changes_exit_but_not_entry(self):
        a=Account('test','s06',1,960); a.rules=replace(a.rules,flow=False,second=False,gap=False)
        a.option(event(g.CUTOFF)); a.option(event(g.CUTOFF+1000))
        self.assertEqual(a.inventory,1)
        a.option(event(g.CUTOFF+2000,bid=1900000,ask=1920000,quantity=0))
        self.assertGreater(a.order['price'],1920000)
        a.option(event(g.CUTOFF+301000,bid=1900000,ask=1920000,quantity=0))
        self.assertEqual(a.order['price'],1918000)

    def test_missing_close_quote_cannot_virtualize_loss(self):
        a=Account('test','s00',1,960)
        a.option(event(g.CUTOFF)); a.option(event(g.CUTOFF+1000))
        with self.assertRaises(ValueError): a.boundary(g.BOUNDARIES[0])

    def test_low_capital_blocks_entry_without_faking_fill(self):
        a=Account('test','s00',1,960,capital_cny=19999)
        a.option(event(g.CUTOFF)); self.assertIsNone(a.order)
        self.assertEqual(a.demands[0][1],2000170)

    def test_through_rejects_touch_and_keeps_flow(self):
        a=Account('test','s00',1,960,through=True)
        a.option(event(g.CUTOFF)); a.option(event(g.CUTOFF+1000))
        self.assertEqual(a.inventory,0); self.assertTrue(a.flow)
        a.option(event(g.CUTOFF+2000,bid=1998000,ask=2020000))
        self.assertEqual(a.inventory,1)

    def test_full_rules_match_all_expected_components(self):
        r=cases()['s10']['rules']
        self.assertTrue(all((r.improve,r.net_spread,r.flow,r.second,r.gap,r.patient,r.adverse_release,r.warm,r.value,r.fast)))

    def test_combined_simplification_removes_only_three_entry_filters(self):
        from zhaiquant.gold_rule_ladder_simplification import Account as Simple
        a=Simple('test','core',1,960)
        e=replace(event(g.CUTOFF),bids=((2000000,1),))
        feature=dict(ready=True,fair_cents=2100000)
        self.assertIsNotNone(a.candidate(e,feature)[0])
        self.assertEqual(Account('test','s10',1,960).candidate(e,feature)[1],'two_sided_flow')
        self.assertTrue(a.rules.patient and a.rules.adverse_release and a.rules.value and a.rules.fast)

    def test_spread_only_does_not_smuggle_valuation_or_exit_protection(self):
        from zhaiquant.gold_rule_ladder_simplification import Account as Simple
        a=Simple('test','spread8',1,960)
        self.assertIsNotNone(a.candidate(event(g.CUTOFF,ask=2016000),None)[0])
        self.assertIsNone(a.candidate(event(g.CUTOFF,ask=2014000),None)[0])
        self.assertFalse(a.rules.value or a.rules.patient or a.rules.flow or a.rules.second)


if __name__=='__main__': unittest.main()
