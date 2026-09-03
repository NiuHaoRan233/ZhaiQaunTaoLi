from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from zhaiquant.database import SQLiteStore
from zhaiquant.maker import ReplayTick, TradeEvidence
from zhaiquant.maker_paper import (
    MakerPaperEngine,
    SHARED_THOUSAND_POLICY_V01_CANDIDATE,
    SHARED_THOUSAND_POLICY_V02_CANDIDATE,
    maker_comparison_strategy_id,
)
from zhaiquant.one_hand_maker_research import (
    SHARED_THOUSAND_BONDS,
    _small_account_config,
)
from zhaiquant.shared_thousand_maker_v02_research import (
    MODEL_ID,
    AllocationParametersV02,
    SharedThousandV02Allocator,
    score_buy_intent_v02,
)

from .helpers import test_config


def _tick(
    tick_id: int, *, bid: float, ask: float,
    market_time: str = "10:00:00.000", support_bonds: float = 5_000.0,
) -> ReplayTick:
    return ReplayTick(
        tick_id=tick_id,
        code="132026.SH",
        market_ts_ms=1_786_000_000_000 + tick_id * 1_000,
        market_date="2026-08-31",
        market_time=market_time,
        last_price=bid,
        bids=tuple(
            (bid - index * 0.001, support_bonds) for index in range(5)
        ),
        asks=tuple((ask + index * 0.001, 1_000.0) for index in range(5)),
        trade_bonds=0.0,
        transaction_delta=0,
        inferred_side="unknown",
        side_confidence="none",
        previous_close=136.0,
    )


class SharedThousandMakerV02ResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        database = Path(self.temporary.name) / "v02.sqlite3"
        self.config = _small_account_config(
            test_config(database),
            database,
            shared_capacity_bonds=SHARED_THOUSAND_BONDS,
        )
        self.store = SQLiteStore(self.config)
        self.store.start_session()
        strategy_id = maker_comparison_strategy_id(
            self.config,
            "132026.SH",
            SHARED_THOUSAND_POLICY_V02_CANDIDATE,
        )
        self.engine = MakerPaperEngine(
            self.config,
            self.store,
            bond_code="132026.SH",
            priority_policy=SHARED_THOUSAND_POLICY_V02_CANDIDATE,
            fill_modes=("priority",),
            include_windfall=False,
            strategy_ids_by_mode={"priority": strategy_id},
        )
        self.engine._start_date("2026-08-31")
        self.account = next(iter(self.engine.accounts.values()))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _order(self, tick: ReplayTick, *, price: float, target: float):
        return self.engine._new_order(
            self.account,
            tick,
            side="buy",
            kind="sweep_tail",
            lot_id=None,
            price=price,
            quantity=SHARED_THOUSAND_BONDS,
            queue_ahead=0.0,
            target_price=target,
            price_boundary=price,
            persist=False,
        )

    def test_v02_is_an_immutable_child_of_shared_v01(self) -> None:
        self.assertEqual(MODEL_ID, "maker_shared_1000_v0_2_candidate")
        self.assertEqual(
            SHARED_THOUSAND_POLICY_V02_CANDIDATE.parent_model_id,
            SHARED_THOUSAND_POLICY_V01_CANDIDATE.model_id,
        )
        self.assertEqual(
            SHARED_THOUSAND_POLICY_V02_CANDIDATE
                .enable_wide_reward_risk_nested_support_override,
            SHARED_THOUSAND_POLICY_V01_CANDIDATE
                .enable_wide_reward_risk_nested_support_override,
        )

    def test_wide_active_fill_with_weak_support_loses_to_cash(self) -> None:
        tick = _tick(
            1, bid=136.000, ask=136.490, support_bonds=200.0,
        )
        order = self._order(tick, price=136.490, target=136.700)
        score = score_buy_intent_v02(
            bond_code=tick.code,
            engine=self.engine,
            account=self.account,
            order=order,
            tick=tick,
            parameters=AllocationParametersV02(),
            active_fill=True,
        )
        self.assertLess(score.score_cny, 0.0)
        self.assertGreaterEqual(score.downside_distance_per_bond, 0.49)
        self.assertGreater(score.expected_downside_per_bond, 0.0)

    def test_price_local_two_sided_flow_creates_positive_capital_time_score(
        self,
    ) -> None:
        tick = _tick(2, bid=136.000, ask=136.400)
        order = self._order(tick, price=136.001, target=136.400)
        self.engine.analyzer.trade_evidence.extend((
            TradeEvidence(
                tick.market_ts_ms - 20_000, 136.001, 8_000.0, 2, "sell",
            ),
            TradeEvidence(
                tick.market_ts_ms - 10_000, 136.390, 8_000.0, 2, "buy",
            ),
        ))
        score = score_buy_intent_v02(
            bond_code=tick.code,
            engine=self.engine,
            account=self.account,
            order=order,
            tick=tick,
            parameters=AllocationParametersV02(),
            active_fill=False,
        )
        self.assertGreater(score.score_cny, 0.0)
        self.assertEqual(score.local_recent_sell_bonds, 8_000.0)
        self.assertEqual(score.local_recent_buy_bonds, 8_000.0)
        self.assertLessEqual(score.support_quality, 0.90)
        self.assertGreater(score.expected_downside_per_bond, 0.0)

    def test_far_away_buy_flow_does_not_improve_exit_probability(self) -> None:
        tick = _tick(3, bid=136.000, ask=136.400)
        order = self._order(tick, price=136.001, target=136.400)
        baseline = score_buy_intent_v02(
            bond_code=tick.code,
            engine=self.engine,
            account=self.account,
            order=order,
            tick=tick,
            parameters=AllocationParametersV02(),
            active_fill=False,
        )
        self.engine.analyzer.trade_evidence.append(TradeEvidence(
            tick.market_ts_ms - 1_000, 136.050, 20_000.0, 2, "buy",
        ))
        with_far_buy = score_buy_intent_v02(
            bond_code=tick.code,
            engine=self.engine,
            account=self.account,
            order=order,
            tick=tick,
            parameters=AllocationParametersV02(),
            active_fill=False,
        )
        self.assertEqual(with_far_buy.local_recent_buy_bonds, 0.0)
        self.assertAlmostEqual(
            with_far_buy.exit_probability_proxy,
            baseline.exit_probability_proxy,
        )

    def test_cash_is_selected_when_all_scores_are_nonpositive(self) -> None:
        tick = _tick(4, bid=136.000, ask=136.490, support_bonds=200.0)
        order = self._order(tick, price=136.490, target=136.700)
        score = score_buy_intent_v02(
            bond_code=tick.code,
            engine=self.engine,
            account=self.account,
            order=order,
            tick=tick,
            parameters=AllocationParametersV02(),
            active_fill=True,
        )
        allocator = SharedThousandV02Allocator(
            {tick.code: self.engine},
            initial_cash_cny=140_000.0,
            capital_ready_ts_ms=tick.market_ts_ms - 1_000,
        )
        winner, reason = allocator._choose(
            {tick.code: score}, market_ts_ms=tick.market_ts_ms,
        )
        self.assertIsNone(winner)
        self.assertEqual(reason, "cash_dominates_nonpositive_scores")

    def test_terminal_time_reduces_an_otherwise_identical_score(self) -> None:
        morning = _tick(5, bid=136.000, ask=136.400)
        late = replace(
            morning,
            tick_id=6,
            market_ts_ms=morning.market_ts_ms + 1_000,
            market_time="15:29:30.000",
        )
        order = self._order(morning, price=136.001, target=136.400)
        morning_score = score_buy_intent_v02(
            bond_code=morning.code,
            engine=self.engine,
            account=self.account,
            order=order,
            tick=morning,
            parameters=AllocationParametersV02(),
            active_fill=False,
        )
        late_score = score_buy_intent_v02(
            bond_code=late.code,
            engine=self.engine,
            account=self.account,
            order=order,
            tick=late,
            parameters=AllocationParametersV02(),
            active_fill=False,
        )
        self.assertLess(late_score.terminal_time_factor, 1.0)
        self.assertLess(late_score.score_cny, morning_score.score_cny)


if __name__ == "__main__":
    unittest.main()
