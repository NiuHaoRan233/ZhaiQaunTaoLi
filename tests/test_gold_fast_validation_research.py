import unittest
from zhaiquant import gold_direction_research as g
from zhaiquant.gold_fast_validation_research import Account,ValueState
from tests.test_gold_direction_research import event


class FastValidationTests(unittest.TestCase):
    def test_touch_stress_rejects_fill_without_removing_original_intent(self):
        a=Account('au2610C960.SF','cost',960,through=True);ts=g.CUTOFF;e=event(ts)
        a.issue(ts,'buy',2002000,'entry',e)
        a.fill(ts+1000,2002000,'buy','passive',event(ts+1000,bid=2002000,ask=2020000))
        self.assertEqual(a.inventory,0);self.assertIsNotNone(a.order);self.assertEqual(a.touch_rejections,1)
        a.fill(ts+2000,2002000,'buy','passive',event(ts+2000,bid=2000000,ask=2020000))
        self.assertEqual(a.inventory,1)

    def test_faster_memory_moves_farther_on_same_observation(self):
        vs=[ValueState(960,t) for t in (5,10,20)];ts=g.CUTOFF
        for v in vs:
            v.observe_option(event(ts));v.on_future(g.Future(ts,ts,0,940))
            v.observe_option(event(ts+1000,bid=2100000,ask=2120000));v.on_future(g.Future(ts+1000,ts+1000,0,940))
        self.assertGreater(vs[0].fast_vol,vs[1].fast_vol);self.assertGreater(vs[1].fast_vol,vs[2].fast_vol)


if __name__=='__main__':unittest.main()
