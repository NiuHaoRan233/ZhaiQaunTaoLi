import unittest
from zhaiquant import gold_direction_research as g
from zhaiquant.option_top_cycle_research import Event


def event(ts,side='sell',bid=2000000,ask=2020000,quantity=1,session=0):
    return Event(ts,ts-1000,session,bid,ask,2,2,((bid,2),(bid-2000,2)),((ask,2),(ask+2000,2)),
        bid if side=='sell' else ask,quantity,0,side,True,side)


def account(profile='long',settlement='cost'):
    return g.Account('au2610C960.SF',profile,settlement,960)


def entered(profile='long',settlement='cost'):
    a=account(profile,settlement)
    for i,side in enumerate(['sell','buy','sell' if profile=='long' else 'buy']):
        ts=g.CUTOFF+i*1000;a.on_future(g.Future(ts-500,ts-500,0,940));a.option(event(ts,side))
    assert a.inventory==(1 if profile=='long' else -1)
    return a


class GoldDirectionTests(unittest.TestCase):
    def test_black_price_increases_with_future_and_vol(self):
        p,d=g.black_call(940,960,12/365,.22)
        self.assertGreater(g.black_call(941,960,12/365,.22)[0],p)
        self.assertGreater(g.black_call(940,960,12/365,.25)[0],p)
        self.assertAlmostEqual(g.implied_vol(p,940,960,12/365),.22,places=8)
        self.assertTrue(0<d<1)

    def test_short_profit_and_premium_cash_are_reconciled(self):
        a=entered('short');self.assertGreater(a.cash,g.CAPITAL)
        o=a.order;a.fill(g.CUTOFF+3000,1900000,'buy','pre_break_market_close',event(g.CUTOFF+3000))
        a.mark(g.CUTOFF+3000)
        self.assertEqual(a.inventory,0)
        self.assertEqual(a.cash-g.CAPITAL,a.rows['cycles'][0]['net_cents'])
        self.assertEqual(a.rows['cycles'][0]['gross_cents'],118000)

    def test_short_loss_is_not_sign_reversed(self):
        a=entered('short');a.fill(g.CUTOFF+3000,2100000,'buy','pre_break_market_close',event(g.CUTOFF+3000))
        self.assertEqual(a.rows['cycles'][0]['net_cents'],-82340)

    def test_short_cost_exit_removes_only_tail_with_both_fees(self):
        a=entered('short');a.option(event(g.CUTOFF+3000,'sell',2100000,2120000,quantity=0))
        a.boundary(g.BOUNDARIES[0]);c=a.rows['cycles'][-1]
        self.assertEqual(c['net_cents'],-340)
        self.assertEqual(c['quote_mark_gross_before_close_cents'],-102000)
        self.assertEqual(a.inventory,0)

    def test_long_cost_exit_does_not_erase_previous_losses(self):
        a=entered();a.fill(g.CUTOFF+3000,1900000,'sell','pre_break_market_close',event(g.CUTOFF+3000))
        old=a.cash;a.boundary(g.BOUNDARIES[0]);self.assertEqual(a.cash,old)
        self.assertEqual(a.rows['cycles'][0]['net_cents'],-102340)

    def test_market_short_exit_consumes_ask(self):
        a=entered('short','market');a.option(event(g.BOUNDARIES[0]-4000,'sell',2100000,2120000,quantity=0))
        self.assertEqual(a.rows['fills'][-1]['price_cents'],2120000)
        self.assertEqual(a.rows['fills'][-1]['kind'],'pre_break_market_close')
        a.boundary(g.BOUNDARIES[0]);self.assertEqual(a.inventory,0)

    def test_market_missing_quote_cannot_cost_reset(self):
        a=entered('long','market')
        with self.assertRaises(ValueError):a.boundary(g.BOUNDARIES[0])

    def test_cross_break_event_requires_settlement(self):
        a=entered()
        with self.assertRaises(ValueError):a.option(event(g.SESSION_STARTS[1],session=1))

    def test_same_timestamp_future_not_used_by_option_feature(self):
        v=g.ValueState(960);ts=g.CUTOFF;v.on_future(g.Future(ts,ts,0,940))
        v.vol=.22;v.vol_ts=ts-1000;v.first=ts-120000;v.updates=20
        self.assertIsNone(v.feature(ts))
        self.assertIsNotNone(v.feature(ts,allow_same_future=True))

    def test_future_older_than_two_seconds_is_not_a_signal(self):
        v=g.ValueState(960);ts=g.CUTOFF;v.on_future(g.Future(ts-2500,ts-2500,0,940))
        v.vol=.22;v.vol_ts=ts-1000;v.first=ts-120000;v.updates=20
        self.assertIsNone(v.feature(ts))

    def test_future_cancel_inside_trade_interval_cannot_erase_fill(self):
        a=account('fair_risk_switch');ts=g.CUTOFF
        a.session=0;a.last_book=event(ts);a.value.new_session(0)
        a.issue(ts,'buy',2002000,'entry',event(ts))
        a.on_future(g.Future(ts+500,ts+500,0,935))
        self.assertEqual(a.order['cancel_ts'],ts+500)
        a.option(event(ts+1000))
        self.assertTrue(a.rows['fills'][0]['cancellation_inside_aggregate_interval'])
        self.assertEqual(a.inventory,1)

    def test_future_cancel_before_interval_avoids_fill(self):
        a=account('fair_risk_switch');ts=g.CUTOFF
        a.session=0;a.last_book=event(ts);a.value.new_session(0)
        a.issue(ts,'buy',2002000,'entry',event(ts));a.on_future(g.Future(ts+500,ts+500,0,935))
        a.option(event(ts+2000))
        self.assertFalse(a.rows['fills'])

    def test_directional_trend_gate_is_symmetric(self):
        e=event(g.CUTOFF);flow=[(e.ts,'buy'),(e.ts,'sell')]
        f=dict(ready=True,fair_cents=2010000,move10_cents=-100000,move60_cents=-100000)
        self.assertIsNone(g.candidate(e,1,2000,f,g.PROFILES['long_trend'],flow)[0])
        self.assertIsNotNone(g.candidate(e,-1,2000,f,g.PROFILES['short_trend'],flow)[0])


if __name__=='__main__':unittest.main()
