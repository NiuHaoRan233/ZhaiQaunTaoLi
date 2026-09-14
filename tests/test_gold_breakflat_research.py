import unittest
from zhaiquant import gold_breakflat_research as g
from zhaiquant.option_top_cycle_research import Event


def event(ts,side='sell',bid=2000000,ask=2020000,quantity=1,bid_qty=2):
    return Event(ts,ts-1000,0,bid,ask,bid_qty,2,((bid,bid_qty),(bid-2000,2)),((ask,2),),
        bid if side=='sell' else ask,quantity,0,side,True,side)


def bought(start=None):
    start=start or g.CUTOFF
    a=g.BreakFlatAccount('au2610C960.SF','gap',2000)
    for i,side in enumerate(['sell','buy','sell']):a.step(event(start+i*1000,side),g.DATE)
    assert a.s['inventory']==1
    return a


class GoldBreakFlatTests(unittest.TestCase):
    def test_active_loss_is_realized_with_both_fees(self):
        a=bought();ts=g.BOUNDARIES[0]-g.FLAT_BEFORE_MS
        r=a.step(event(ts,bid=1950000,ask=1970000,quantity=0),g.DATE)
        self.assertEqual(r['fills'][0]['kind'],'pre_break_active_sell')
        self.assertEqual(r['cycles'][0]['net_cents'],-52340)
        self.assertEqual(a.summary()['pnl_cny'],-523.4)
        self.assertEqual(a.s['inventory'],0)
        a.check_boundary(g.BOUNDARIES[0])

    def test_zero_new_trade_volume_still_allows_active_bid_sale(self):
        a=bought();r=a.step(event(g.BOUNDARIES[0]-60000,quantity=0),g.DATE)
        self.assertEqual(len(r['fills']),1)
        self.assertFalse(r['fills'][0]['source_last_contract_evidence'])
        self.assertEqual(r['fills'][0]['source_bid_qty'],2)

    def test_missing_bid_does_not_invent_cost_liquidation(self):
        a=bought();a.step(event(g.BOUNDARIES[0]-60000,bid=0,bid_qty=0,quantity=0),g.DATE)
        self.assertEqual(a.s['inventory'],1)
        with self.assertRaises(ValueError):a.check_boundary(g.BOUNDARIES[0])
        with self.assertRaises(ValueError):a.close_day(g.DATE)

    def test_later_valid_quote_within_window_can_flatten(self):
        a=bought();a.step(event(g.BOUNDARIES[0]-60000,bid=0,bid_qty=0,quantity=0),g.DATE)
        a.step(event(g.BOUNDARIES[0]-30000,quantity=0),g.DATE)
        a.check_boundary(g.BOUNDARIES[0]);self.assertEqual(a.s['inventory'],0)

    def test_old_buy_fill_at_entry_cutoff_is_not_erased(self):
        cutoff=g.BOUNDARIES[0]-300000
        a=g.BreakFlatAccount('au2610C960.SF','gap',2000)
        a.step(event(cutoff-2000),g.DATE);a.step(event(cutoff-1000,'buy'),g.DATE)
        r=a.step(event(cutoff,'sell'),g.DATE)
        self.assertEqual(len(r['fills']),1);self.assertEqual(a.s['inventory'],1)

    def test_cannot_submit_new_buy_after_cutoff(self):
        cutoff=g.BOUNDARIES[0]-300000
        a=g.BreakFlatAccount('au2610C960.SF','gap',2000)
        a.step(event(cutoff),g.DATE);r=a.step(event(cutoff+1000,'buy'),g.DATE)
        self.assertFalse(r['orders']);self.assertIsNone(a.s['order'])

    def test_previously_executable_passive_sale_precedes_forced_sale(self):
        a=bought();r=a.step(event(g.BOUNDARIES[0]-60000,'buy'),g.DATE)
        self.assertEqual(len(r['fills']),1);self.assertEqual(r['fills'][0]['kind'],'passive')
        self.assertEqual(len(a.active_exits),0)

    def test_resume_cannot_hide_boundary_inventory(self):
        a=bought()
        with self.assertRaises(ValueError):a.step(event(g.BOUNDARIES[0]+900000),g.DATE)


if __name__=='__main__':unittest.main()
