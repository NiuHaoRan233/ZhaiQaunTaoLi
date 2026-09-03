from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from zhaiquant.database import SQLiteStore
from zhaiquant.maker import MarketAssessment, ReplayTick, TradeEvidence
from zhaiquant.maker_paper import (
    MakerPaperEngine,
    SHARED_THOUSAND_POLICY_V02_CANDIDATE,
    SHARED_THOUSAND_POLICY_V03_CANDIDATE,
    maker_comparison_strategy_id,
)
from zhaiquant.one_hand_maker_research import (
    SHARED_THOUSAND_BONDS,
    _small_account_config,
)
from zhaiquant.shared_thousand_maker_v02_research import (
    AllocationParametersV02,
    score_buy_intent_v02,
)
from zhaiquant.shared_thousand_maker_v03_research import (
    MODEL_ID,
    AllocationParametersV03,
    SharedThousandV03Allocator,
    score_buy_intent_v03,
)

from .helpers import test_config


def _target_tick(
    tick_id: int = 1, *, near_support_bonds: float = 3_000.0,
) -> ReplayTick:
    first = min(2_000.0, near_support_bonds)
    second = max(0.0, near_support_bonds - first)
    bids = [(136.501, first)] if first > 0 else []
    if second > 0:
        bids.append((136.500, second))
    bids.extend((
        (136.251, 2_000.0),
        (136.250, 1_000.0),
        (136.220, 1_000.0),
    ))
    return ReplayTick(
        tick_id=tick_id,
        code="132024.SH",
        market_ts_ms=1_788_316_243_000 + tick_id,
        market_date="2026-09-02",
        market_time="10:44:03.000",
        last_price=136.603,
        bids=tuple(bids),
        asks=(
            (137.199, 1_000.0),
            (137.200, 15_000.0),
            (137.250, 4_000.0),
            (137.300, 1_000.0),
            (137.476, 1_190.0),
        ),
        trade_bonds=0.0,
        transaction_delta=0,
        inferred_side="unknown",
        side_confidence="none",
        previous_close=136.0,
    )


def _stable_assessment() -> MarketAssessment:
    return MarketAssessment(
        reference_price=136.850,
        reference_low=136.501,
        reference_high=137.199,
        reference_source="current_midpoint",
        reference_confidence=0.35,
        state="stable",
        state_score=0,
        state_confidence=0.42,
        recent_buy_bonds=0.0,
        recent_sell_bonds=1_000.0,
        midpoint_change=0.0,
        short_ask_change=0.0,
        largest_ask_gap=0.05,
        downside_book_vacuum=False,
        fragile_top_bid=False,
        iron_floor_price=None,
        iron_floor_bonds=0.0,
        evidence=(),
    )


class SharedThousandMakerV03ResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        database = Path(self.temporary.name) / "v03.sqlite3"
        self.config = _small_account_config(
            test_config(database),
            database,
            shared_capacity_bonds=SHARED_THOUSAND_BONDS,
        )
        self.store = SQLiteStore(self.config)
        self.store.start_session()
        strategy_id = maker_comparison_strategy_id(
            self.config,
            "132024.SH",
            SHARED_THOUSAND_POLICY_V03_CANDIDATE,
        )
        self.engine = MakerPaperEngine(
            self.config,
            self.store,
            bond_code="132024.SH",
            priority_policy=SHARED_THOUSAND_POLICY_V03_CANDIDATE,
            fill_modes=("priority",),
            include_windfall=False,
            strategy_ids_by_mode={"priority": strategy_id},
        )
        self.engine._start_date("2026-09-02")
        self.engine.last_market_assessment = _stable_assessment()
        self.account = next(iter(self.engine.accounts.values()))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _persistent_session(self, tick: ReplayTick) -> None:
        events = tuple(
            TradeEvidence(
                tick.market_ts_ms - (3_700 - index * 600) * 1_000,
                137.000 + 0.04 * (index % 4),
                4_000.0,
                1,
                "buy",
            )
            for index in range(7)
        ) + (
            TradeEvidence(
                tick.market_ts_ms - 126_000,
                136.603,
                1_000.0,
                1,
                "sell",
            ),
        )
        self.engine.analyzer.session_market_date = tick.market_date
        self.engine.analyzer.session_trade_evidence.extend(events)
        self.engine.analyzer.trade_evidence.append(events[-1])

    def _order(self, tick: ReplayTick, *, kind: str):
        return self.engine._new_order(
            self.account,
            tick,
            side="buy",
            kind=kind,
            lot_id=None,
            price=136.502,
            quantity=SHARED_THOUSAND_BONDS,
            queue_ahead=0.0,
            target_price=137.000,
            price_boundary=136.502,
            persist=False,
        )

    def test_v03_is_immutable_child_and_v02_permission_stays_off(self) -> None:
        self.assertEqual(MODEL_ID, "maker_shared_1000_v0_3_candidate")
        self.assertEqual(
            SHARED_THOUSAND_POLICY_V03_CANDIDATE.parent_model_id,
            SHARED_THOUSAND_POLICY_V02_CANDIDATE.model_id,
        )
        self.assertTrue(
            SHARED_THOUSAND_POLICY_V03_CANDIDATE
                .enable_session_resilient_ordinary_entry
        )
        self.assertFalse(
            SHARED_THOUSAND_POLICY_V02_CANDIDATE
                .enable_session_resilient_ordinary_entry
        )

    def test_target_frame_enters_consideration_and_beats_cash(self) -> None:
        tick = _target_tick()
        self._persistent_session(tick)
        decision = self.engine._session_resilient_ordinary_entry(
            self.account, tick, _stable_assessment(), 136.502,
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertAlmostEqual(decision.exit_price, 137.000)
        self.assertEqual(decision.near_support_bonds, 3_000.0)
        self.assertGreater(decision.high_trade_span_seconds, 3_000.0)
        self.assertGreater(decision.composite_quality, 0.65)

        order = self._order(
            tick, kind="session_resilient_value_entry",
        )
        old_score = score_buy_intent_v02(
            bond_code=tick.code,
            engine=self.engine,
            account=self.account,
            order=order,
            tick=tick,
            parameters=AllocationParametersV02(),
            active_fill=False,
        )
        score = score_buy_intent_v03(
            bond_code=tick.code,
            engine=self.engine,
            account=self.account,
            order=order,
            tick=tick,
            parameters=AllocationParametersV03(),
            active_fill=False,
        )
        self.assertLess(old_score.score_cny, 0.0)
        self.assertGreater(score.score_cny, 0.0)
        self.assertTrue(score.session_resilience_eligible)
        self.assertGreater(score.session_exit_probability_bonus, 0.0)
        self.assertGreater(score.session_entry_probability_bonus, 0.0)

        allocator = SharedThousandV03Allocator(
            {tick.code: self.engine},
            initial_cash_cny=140_000.0,
            capital_ready_ts_ms=tick.market_ts_ms - 1_000,
        )
        winner, reason = allocator._choose(
            {tick.code: score}, market_ts_ms=tick.market_ts_ms,
        )
        self.assertEqual(winner, tick.code)
        self.assertEqual(
            reason, "select_highest_positive_capital_time_score",
        )

    def test_target_refresh_creates_the_auditable_passive_order(self) -> None:
        tick = _target_tick()
        self._persistent_session(tick)
        self.engine.previous_close_reference = 136.850
        self.engine._refresh_orders(
            self.account, tick, _stable_assessment(), persist=False,
        )
        order = self.account.buy_order
        self.assertIsNotNone(order)
        assert order is not None
        self.assertEqual(order.kind, "session_resilient_value_entry")
        self.assertAlmostEqual(order.limit_price, 136.502)
        self.assertAlmostEqual(order.target_price or 0.0, 137.000)

    def test_one_high_print_does_not_turn_wide_spread_into_candidate(
        self,
    ) -> None:
        tick = _target_tick()
        self.engine.analyzer.session_trade_evidence.append(
            TradeEvidence(
                tick.market_ts_ms - 1_000,
                137.200,
                10_000.0,
                10,
                "buy",
            )
        )
        decision = self.engine._session_resilient_ordinary_entry(
            self.account, tick, _stable_assessment(), 136.502,
        )
        self.assertIsNone(decision)
        order = self._order(tick, kind="low_bid_reversion")
        score = score_buy_intent_v03(
            bond_code=tick.code,
            engine=self.engine,
            account=self.account,
            order=order,
            tick=tick,
            parameters=AllocationParametersV03(),
            active_fill=False,
        )
        self.assertFalse(score.session_resilience_eligible)
        self.assertEqual(score.session_exit_probability_bonus, 0.0)

    def test_support_is_continuous_but_zero_support_does_not_admit(self) -> None:
        strong_tick = _target_tick(1, near_support_bonds=3_000.0)
        self._persistent_session(strong_tick)
        weak_tick = _target_tick(2, near_support_bonds=1_000.0)
        zero_tick = _target_tick(3, near_support_bonds=0.0)
        strong_order = self._order(
            strong_tick, kind="session_resilient_value_entry",
        )
        weak_order = self._order(
            weak_tick, kind="session_resilient_value_entry",
        )
        strong_score = score_buy_intent_v03(
            bond_code=strong_tick.code,
            engine=self.engine,
            account=self.account,
            order=strong_order,
            tick=strong_tick,
            parameters=AllocationParametersV03(),
            active_fill=False,
        )
        weak_score = score_buy_intent_v03(
            bond_code=weak_tick.code,
            engine=self.engine,
            account=self.account,
            order=weak_order,
            tick=weak_tick,
            parameters=AllocationParametersV03(),
            active_fill=False,
        )
        self.assertTrue(weak_score.session_resilience_eligible)
        self.assertGreater(strong_score.score_cny, weak_score.score_cny)
        self.assertIsNone(self.engine._session_resilient_ordinary_entry(
            self.account, zero_tick, _stable_assessment(), 136.502,
        ))

    def test_clearly_superior_other_bond_wins_comparison(self) -> None:
        tick = _target_tick()
        self._persistent_session(tick)
        order = self._order(
            tick, kind="session_resilient_value_entry",
        )
        jiangxi = score_buy_intent_v03(
            bond_code=tick.code,
            engine=self.engine,
            account=self.account,
            order=order,
            tick=tick,
            parameters=AllocationParametersV03(),
            active_fill=False,
        )
        three_gorges = replace(
            jiangxi,
            bond_code="132026.SH",
            capital_time_score_cny=jiangxi.score_cny + 100.0,
        )
        allocator = SharedThousandV03Allocator(
            {tick.code: self.engine},
            initial_cash_cny=140_000.0,
            capital_ready_ts_ms=tick.market_ts_ms - 1_000,
        )
        winner, _ = allocator._choose(
            {tick.code: jiangxi, "132026.SH": three_gorges},
            market_ts_ms=tick.market_ts_ms,
        )
        self.assertEqual(winner, "132026.SH")


if __name__ == "__main__":
    unittest.main()
