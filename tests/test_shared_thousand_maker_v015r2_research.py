from dataclasses import asdict, replace
import unittest

from zhaiquant.maker_paper import SHARED_THOUSAND_POLICY_V014_CANDIDATE, SharedCapitalPaperRuntime
from zhaiquant.shared_thousand_maker_v013_research import score_buy_intent_v013
from zhaiquant.shared_thousand_maker_v015r2_research import (
    POLICY, SharedThousandV015R2Allocator, full_consumed_live_attack,
)
from . import test_shared_thousand_maker_v015_research as first


class SharedThousandMakerV015R2Tests(unittest.TestCase):
    _engine = first.SharedThousandMakerV015Tests._engine
    add_buy = first.SharedThousandMakerV015Tests.add_buy

    def setUp(self):
        first.SharedThousandMakerV015Tests.setUp(self)
        self.allocator = SharedThousandV015R2Allocator({self.tick.code: self.engine},
            initial_cash_cny=140000, capital_ready_ts_ms=self.tick.market_ts_ms)
        self.tick = replace(self.tick, asks=((136.201, 1000), (136.800, 4000)))
        self.allocator.last_bond_ticks[self.tick.code] = self.tick
        self.order = replace(self.order, kind='sweep_tail')
        self.allocator._active_fill_quantities[(self.tick.code, self.order.db_id)] = 1000

    def test_direct_parent_identity_and_offline(self):
        self.assertEqual(POLICY.parent_model_id, SHARED_THOUSAND_POLICY_V014_CANDIDATE.model_id)
        diff = {k for k, v in asdict(POLICY).items() if v != asdict(SHARED_THOUSAND_POLICY_V014_CANDIDATE)[k]}
        self.assertEqual(diff, {'model_id', 'model_version', 'parent_model_id'})
        with self.assertRaisesRegex(ValueError, 'unsupported shared-capital realtime model'):
            SharedCapitalPaperRuntime(self.engine.config, self.store, policy=POLICY)

    def test_live_buy_full_consumption_preserves_native_score(self):
        self.add_buy(quantity=8000, price=136.201)
        score = self.allocator._score(self.tick.code, self.order, active_fill=True)
        parent = score_buy_intent_v013(bond_code=self.tick.code, engine=self.engine,
            account=self.account, order=self.order, tick=self.tick, parameters=self.p,
            active_fill=True, fill_quantity=1000)
        self.assertTrue(score.full_consumed_live_attack)
        self.assertAlmostEqual(score.score_cny, parent.score_cny)

    def test_large_real_buy_with_residual_low_offer_is_not_full_sweep(self):
        self.add_buy(quantity=30000, price=136.201)
        self.allocator.last_bond_ticks[self.tick.code] = replace(self.tick, asks=((136.201, 1710), (136.8, 10000)))
        score = self.allocator._score(self.tick.code, self.order, active_fill=True)
        self.assertFalse(score.full_consumed_live_attack)

    def test_old_unknown_low_quantity_and_no_next_offer_do_not_exempt(self):
        for kwargs in (dict(age=3, quantity=8000), dict(side='unknown', quantity=8000), dict(quantity=20)):
            with self.subTest(kwargs=kwargs):
                self.engine.analyzer.trade_evidence.clear()
                self.add_buy(price=136.201, **kwargs)
                self.assertFalse(full_consumed_live_attack(self.engine, self.tick, self.order, 1000, True, 1000))
        self.add_buy(price=136.201, quantity=8000)
        self.assertFalse(full_consumed_live_attack(self.engine,
            replace(self.tick, asks=((136.201, 1000),)), self.order, 1000, True, 1000))

    def test_passive_and_other_active_kind_not_exempt(self):
        self.add_buy(price=136.201, quantity=8000)
        self.assertFalse(full_consumed_live_attack(self.engine, self.tick, self.order, 1000, False, 1000))
        self.assertFalse(full_consumed_live_attack(self.engine, self.tick,
            replace(self.order, kind='low_bid_reversion'), 1000, True, 1000))

    def test_passive_first_partial_keeps_plan_but_uses_actual_value_quantity(self):
        order = replace(self.order, kind='low_bid_reversion')
        self.allocator._active_fill_quantities[(self.tick.code, order.db_id)] = 180
        score = self.allocator._score(self.tick.code, order, active_fill=False)
        self.assertEqual(score.remaining_bonds, 180)
        self.assertEqual(score.planned_remaining_bonds, 1000)
        self.allocator.waiting_opportunities['peer'] = {self.tick.market_ts_ms: 100}
        scores = {self.tick.code: replace(score, pre_waiting_score_cny=10, capital_time_score_cny=10)}
        winner, _ = self.allocator._choose(scores, market_ts_ms=self.tick.market_ts_ms)
        self.assertEqual(winner, self.tick.code)
        self.assertEqual(scores[self.tick.code].slot_waiting_cost_cny, 0)

    def test_small_active_intent_still_pays_whole_slot_cost(self):
        order = replace(self.order, kind='deep_discount_sweep', quantity=20)
        self.allocator._active_fill_quantities[(self.tick.code, order.db_id)] = 20
        self.allocator.last_bond_ticks[self.tick.code] = replace(self.tick, asks=((136.201, 20), (136.8, 1000)))
        score = self.allocator._score(self.tick.code, order, active_fill=True)
        self.assertEqual(score.planned_remaining_bonds, 20)
        self.allocator.waiting_opportunities['peer'] = {self.tick.market_ts_ms: 100}
        scores = {self.tick.code: replace(score, pre_waiting_score_cny=10, capital_time_score_cny=10)}
        winner, _ = self.allocator._choose(scores, market_ts_ms=self.tick.market_ts_ms)
        self.assertIsNone(winner)
        self.assertGreater(scores[self.tick.code].slot_waiting_cost_cny, 0)


if __name__ == '__main__':
    unittest.main()
