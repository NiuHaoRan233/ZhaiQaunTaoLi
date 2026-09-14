import unittest
from dataclasses import replace
from zhaiquant import gold_backer_research as m
from tests.test_gold_direction_research import event


def feat(mid=960, move=0):
    return dict(ready=True, fair_cents=2100000, delta=.5, future_mid=mid,
        move2_cents=move, move10_cents=move, move60_cents=0)


class BackerTests(unittest.TestCase):
    def account(self, policy='trend', settlement='exclude'):
        a=m.Account('test',policy,960,settlement=settlement)
        a.features={1:feat(),-1:feat()};a.value.future=m.g.Future(m.g.CUTOFF-1,m.g.CUTOFF-1,0,960)
        return a

    def enter(self, a, ts=None):
        ts=ts or m.g.CUTOFF
        a.option(event(ts,quantity=0));a.option(event(ts+1000))
        self.assertEqual(a.inventory,1)

    def test_last_second_normal_entry_and_excluded_fee_refund(self):
        a=self.account();self.enter(a,m.g.BOUNDARIES[0]-2000)
        self.assertEqual(len(a.rows['fills']),1)
        a.boundary(m.g.BOUNDARIES[0])
        self.assertEqual(a.cash,a.initial);self.assertEqual(a.fees,0)
        self.assertEqual(a.rows['cycles'][-1]['net_cents'],0)
        self.assertEqual(a.rows['cycles'][-1]['quote_mark_gross_before_close_cents'],-2000)

    def test_cost_mode_still_charges_both_fees(self):
        a=self.account(settlement='cost');self.enter(a)
        a.boundary(m.g.BOUNDARIES[0]);self.assertEqual(a.cash-a.initial,-340)

    def test_ordinary_loss_is_never_deleted(self):
        a=self.account();self.enter(a)
        e=event(m.g.CUTOFF+2000,bid=1900000)
        a.issue(e.ts,'sell',e.bid,'risk_market_close',e);a.fill(e.ts,e.bid,'sell','risk_market_close',e)
        before=a.cash;a.boundary(m.g.BOUNDARIES[0]);self.assertEqual(before,a.cash)
        self.assertLess(a.rows['cycles'][0]['net_cents'],0)

    def test_cancel_inside_interval_cannot_erase_possible_old_fill(self):
        a=self.account('trend_fast');ts=m.g.CUTOFF;a.option(event(ts,quantity=0))
        a.future_event(m.g.Future(ts+1500,ts+1500,0,959),{1:feat(959,-5000),-1:feat()})
        a.features={1:feat(959,-5000),-1:feat()};a.option(event(ts+2000))
        self.assertEqual(a.inventory,1)
        self.assertTrue(a.rows['fills'][0]['cancellation_inside_aggregate_interval'])

    def test_cancel_before_interval_prevents_old_fill(self):
        a=self.account('trend_fast');ts=m.g.CUTOFF;a.option(event(ts,quantity=0))
        a.future_event(m.g.Future(ts+500,ts+500,0,959),{1:feat(959,-5000),-1:feat()})
        a.features={1:feat(959,-5000),-1:feat()};a.option(event(ts+2000))
        self.assertEqual(a.inventory,0);self.assertEqual(a.rows['fills'],[])

    def test_futures_only_update_never_sells_stale_option_book(self):
        a=self.account('trend_exit');self.enter(a)
        a.future_event(m.g.Future(m.g.CUTOFF+1500,m.g.CUTOFF+1500,0,959),{1:feat(959,-5000),-1:feat()})
        self.assertEqual(a.inventory,1)
        a.features={1:feat(959,-5000),-1:feat()};e=event(m.g.CUTOFF+2000,quantity=0)
        a.option(e);self.assertEqual(a.inventory,0)
        self.assertEqual(a.rows['fills'][-1]['price_cents'],e.bid)

    def test_large_aggregate_volume_is_not_all_assigned_to_support(self):
        a=self.account('backer_guard');e=event(m.g.CUTOFF)
        a.support=dict(price=e.bid,initial_qty=10,observed_qty=10,minimum_qty=10,
            trade_evidence_contracts=0,last_observed_ts=e.ts-1000)
        e=replace(e,bids=((e.bid,4),),bid_qty=4,quantity=6)
        a.update_support(e);self.assertEqual(a.support['trade_evidence_contracts'],1)

    def test_quote_withdrawal_is_not_classified_as_trade_consumption(self):
        a=self.account('backer_guard');e=event(m.g.CUTOFF,quantity=0)
        a.support=dict(price=e.bid,initial_qty=10,observed_qty=10,minimum_qty=10,
            trade_evidence_contracts=0,last_observed_ts=e.ts-1000)
        a.update_support(e);self.assertEqual(a.support['trade_evidence_contracts'],0)

    def test_wide_spread_does_not_expand_fast_risk_budget(self):
        self.assertTrue(m.fast_bad(feat(move=-4000),1,2000)[0])
        self.assertFalse(m.fast_bad(feat(move=-3999),1,2000)[0])

    def test_support_requires_past_stability(self):
        a=self.account('backer_entry');ts=m.g.CUTOFF
        for i in range(3):
            e=replace(event(ts+i*1000,quantity=0),bid_qty=10,bids=((2000000,10),))
            a.option(e)
            self.assertEqual(bool(a.order),i==2)

    def supported_position(self):
        a=self.account('backer_guard');ts=m.g.CUTOFF
        for i in range(3):
            a.option(replace(event(ts+i*1000,quantity=0),bid_qty=10,bids=((2000000,10),)))
        a.option(replace(event(ts+3000),bid_qty=4,bids=((2000000,4),)))
        self.assertEqual(a.inventory,1)
        return a

    def test_support_exit_uses_current_bid_and_includes_tick_loss(self):
        a=self.supported_position();a.features={1:feat(959,-5000),-1:feat()}
        a.option(replace(event(m.g.CUTOFF+4000,quantity=0),bid_qty=4,bids=((2000000,4),)))
        self.assertEqual(a.inventory,0)
        self.assertEqual(a.risk_events[-1]['reason'],'support_depleted_with_trade_evidence')
        self.assertEqual(a.rows['cycles'][-1]['net_cents'],-2340)

    def test_same_depletion_without_future_warning_does_not_force_exit(self):
        a=self.supported_position()
        a.option(replace(event(m.g.CUTOFF+4000,quantity=0),bid_qty=4,bids=((2000000,4),)))
        self.assertEqual(a.inventory,1)
        self.assertEqual(a.risk_events,[])

    def test_support_disappearance_does_not_sell_at_old_price(self):
        a=self.supported_position();a.features={1:feat(959,-5000),-1:feat()}
        e=event(m.g.CUTOFF+4000,bid=1990000,quantity=0);a.option(e)
        self.assertEqual(a.rows['fills'][-1]['price_cents'],1990000)
        self.assertEqual(a.rows['cycles'][-1]['net_cents'],-12340)

    def test_missing_future_is_not_a_holding_price_warning(self):
        a=self.account('trend_exit');self.enter(a);a.features={1:None,-1:None}
        a.option(event(m.g.CUTOFF+2000,quantity=0))
        self.assertEqual(a.inventory,1)


if __name__=='__main__':unittest.main()
