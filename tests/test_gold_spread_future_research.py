import unittest
from zhaiquant import gold_direction_research as g
from zhaiquant.gold_spread_future_research import Account,trend_ok
from tests.test_gold_direction_research import event


def feat(price=2100000,move=0):return dict(ready=True,fair_cents=price,move10_cents=move,move60_cents=move)


class GoldSpreadFutureTests(unittest.TestCase):
    def test_spread_is_hard_gate_even_with_large_estimated_value(self):
        a=Account('test','value_long',960);a.features={1:feat(),-1:feat()}
        self.assertIsNone(a.candidate(event(g.CUTOFF,ask=2014000),None)[0])
        self.assertIsNotNone(a.candidate(event(g.CUTOFF,ask=2016000),None)[0])

    def test_trend_rejects_only_adverse_direction(self):
        f=feat(move=-20000)
        self.assertFalse(trend_ok(f,1,20000,2000));self.assertTrue(trend_ok(f,-1,20000,2000))

    def test_direction_switch_selects_eligible_side(self):
        a=Account('test','value_switch',960);a.features={1:feat(1900000),-1:feat(1900000)}
        c,_=a.candidate(event(g.CUTOFF),None)
        self.assertIsNotNone(c);self.assertEqual(a.direction,-1)

    def test_throttle_keeps_valid_quote_but_not_unsafe_quote(self):
        a=Account('test','throttle_long',960);e=event(g.CUTOFF);a.chosen_feature=feat()
        a.issue(e.ts,'buy',2002000,'entry',e);a.issue(e.ts+500,'buy',2004000,'entry',e)
        self.assertEqual(a.order_count,1)
        a.chosen_feature=feat(1900000);a.issue(e.ts+600,'buy',1902000,'entry',e)
        self.assertEqual(a.order_count,2)

    def test_future_cancel_inside_interval_keeps_old_fill(self):
        a=Account('test','cancel_switch',960);e=event(g.CUTOFF)
        a.last_book=e;a.session=0;a.chosen_feature=feat();a.issue(e.ts,'buy',2002000,'entry',e)
        a.future_event(g.Future(e.ts+500,e.ts+500,0,940),{1:None,-1:None})
        a.features={1:None,-1:None};a.option(event(e.ts+1000))
        self.assertEqual(a.inventory,1);self.assertTrue(a.rows['fills'][0]['cancellation_inside_aggregate_interval'])

    def test_future_cancel_before_interval_removes_old_order(self):
        a=Account('test','cancel_switch',960);e=event(g.CUTOFF);a.last_book=e;a.session=0
        a.issue(e.ts,'buy',2002000,'entry',e);a.future_event(g.Future(e.ts+500,e.ts+500,0,940),{1:None,-1:None})
        a.option(event(e.ts+2000));self.assertFalse(a.rows['fills'])

    def test_exit_not_throttled(self):
        a=Account('test','throttle_long',960);e=event(g.CUTOFF);a.inventory=1
        a.issue(e.ts,'sell',2020000,'top_exit',e);a.issue(e.ts+100,'sell',2018000,'top_exit',e)
        self.assertEqual(a.order_count,2)

    def test_chase_only_control_always_allows_risk_retreat(self):
        from zhaiquant.gold_spread_quote_control import Account as Controlled
        a=Controlled('test','trend_long',960);e=event(g.CUTOFF);a.chosen_feature=feat()
        a.issue(e.ts,'buy',2002000,'entry',e);a.issue(e.ts+500,'buy',2004000,'entry',e)
        self.assertEqual(a.order_count,1)
        a.issue(e.ts+600,'buy',2000000,'entry',e);self.assertEqual(a.order_count,2)

    def test_chase_control_does_not_hold_on_to_bad_trend(self):
        from zhaiquant.gold_spread_quote_control import Account as Controlled
        a=Controlled('test','trend_long',960);e=event(g.CUTOFF);a.chosen_feature=feat()
        a.issue(e.ts,'buy',2002000,'entry',e);a.chosen_feature=feat(move=-20000)
        a.issue(e.ts+500,'buy',2004000,'entry',e);self.assertEqual(a.order_count,2)


if __name__=='__main__':unittest.main()
