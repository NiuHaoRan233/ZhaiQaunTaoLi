import unittest
from dataclasses import replace
from zhaiquant.commodity_mau_transfer_research import OwnReference, TransferAccount, PROFILES, gate
from zhaiquant.commodity_intraday_research import IntradayAccount
from tests.test_commodity_intraday_research import event, DATE


class TransferTests(unittest.TestCase):
    def test_control_economic_paths_match_parent(self):
        a=TransferAccount('a','control',100000,100)
        b=IntradayAccount('a','edge10',100000,100)
        for i,side in enumerate(['sell','buy','sell','buy']):
            x,y=a.step(event(i,side),DATE),b.step(event(i,side),DATE)
            self.assertEqual(x['curve'],y['curve']);self.assertEqual(x['cycles'],y['cycles'])
        a.close_day(DATE);b.close_day(DATE)
        self.assertEqual(a.daily(),b.daily())

    def test_gap_missing_depth_and_normal_depth(self):
        e=event(0)
        self.assertEqual(gate(e,10100,100,PROFILES['gap'],None,DATE),'bid2_unavailable')
        self.assertIsNone(gate(replace(e,bids=((10000,2),(9900,2))),10100,100,PROFILES['gap'],None,DATE))
        self.assertEqual(gate(replace(e,bids=((10000,2),(8000,2))),10100,100,PROFILES['gap'],None,DATE),'isolated_bid1')

    def test_reference_requires_warmup_resets_on_session_and_not_unchanged_quotes(self):
        r=OwnReference()
        for t in [0,10,20,30,40,50,60]:f=r.update(event(t))
        self.assertTrue(f['ready']);self.assertEqual(f['conservative'],11000)
        self.assertFalse(r.update(replace(event(61),session=1))['ready'])

    def test_reference_does_not_use_future_and_fast_drop_blocks(self):
        a,b=OwnReference(),OwnReference()
        for t in range(61):self.assertEqual(a.update(event(t)),b.update(event(t)))
        feature=a.update(event(62,bid=7000,ask=9000))
        self.assertEqual(gate(event(62,bid=7000,ask=9000),7100,100,PROFILES['reference'],feature,DATE),'own_adverse_10s')
        self.assertEqual(b.fast,11000)

    def test_new_gap_cannot_retroactively_erase_old_buy_fill(self):
        a=TransferAccount('a','gap',100000,100)
        for i,side in enumerate(['sell','buy']):
            a.step(replace(event(i,side),bids=((10000,2),(9900,2))),DATE)
        result=a.step(event(2,'sell',bid=7000,ask=9000),DATE)
        self.assertEqual(len(result['fills']),1);self.assertEqual(a.s['inventory'],1)
        self.assertEqual(result['fills'][0]['entry_signal']['ts'],event(1).ts)

    def test_top_exit_can_realize_loss_does_not_wait_for_cost_reset(self):
        a=TransferAccount('a','top_exit',100000,100)
        for i,side in enumerate(['sell','buy','sell']):a.step(event(i,side),DATE)
        a.step(event(3,'sell',bid=7000,ask=9000,volume=0),DATE)
        r=a.step(event(4,'buy',bid=7000,ask=9000),DATE)
        self.assertEqual(r['cycles'][0]['net_cents'],-1540)
        a.close_day(DATE);self.assertEqual(a.summary()['virtual_close_count'],0)

    def test_cutoff_only_blocks_entries(self):
        from zhaiquant.commodity_intraday_research import close_timestamp
        e=replace(event(0),ts=close_timestamp(DATE)-300000)
        self.assertEqual(gate(e,10100,100,PROFILES['cutoff'],None,DATE),'last_five_minutes')
        self.assertIsNone(gate(replace(e,ts=e.ts-1),10100,100,PROFILES['cutoff'],None,DATE))

    def test_new_models_still_settle_tail_at_cost_with_two_fees(self):
        a=TransferAccount('a','top_exit',100000,100)
        for i,side in enumerate(['sell','buy','sell']):a.step(event(i,side),DATE)
        a.step(event(3,'sell',bid=7000,ask=9000,volume=0),DATE)
        a.close_day(DATE)
        self.assertEqual(a.summary()['pnl_cny'],-3.4)
        self.assertEqual(a.summary()['quote_mark_pnl_before_virtual_close_cny'],-31)


if __name__=='__main__':unittest.main()
