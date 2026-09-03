from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from zhaiquant.database import SQLiteStore
from zhaiquant.maker import ReplayTick
from zhaiquant.maker_paper import (
    MakerPaperEngine,
    ONE_HAND_POLICY_V01_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE,
    maker_comparison_strategy_id,
)
from zhaiquant.one_hand_maker_research import (
    MODEL_ID,
    ONE_HAND_BONDS,
    OneHandSharedAllocator,
    _small_account_config,
)

from .helpers import test_config


def _tick(
    tick_id: int, code: str, *, bid: float, ask: float,
    timestamp: int = 1_786_000_000_000,
) -> ReplayTick:
    return ReplayTick(
        tick_id=tick_id,
        code=code,
        market_ts_ms=timestamp,
        market_date="2026-08-31",
        market_time="10:00:00.000",
        last_price=bid,
        bids=tuple((bid - index * 0.001, 1_000.0) for index in range(5)),
        asks=tuple((ask + index * 0.001, 1_000.0) for index in range(5)),
        trade_bonds=0.0,
        transaction_delta=0,
        inferred_side="unknown",
        side_confidence="none",
        previous_close=136.0,
    )


class OneHandMakerResearchTests(unittest.TestCase):
    def test_candidate_is_an_immutable_child_of_latest_priority_model(self) -> None:
        self.assertEqual(MODEL_ID, "maker_one_hand_v0_1_candidate")
        self.assertEqual(
            ONE_HAND_POLICY_V01_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE.model_id,
        )
        self.assertEqual(
            ONE_HAND_POLICY_V01_CANDIDATE.enable_wide_reward_risk_nested_support_override,
            PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE
                .enable_wide_reward_risk_nested_support_override,
        )
        self.assertEqual(
            ONE_HAND_POLICY_V01_CANDIDATE.enable_live_priority_base_replenishment_exposure,
            PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE
                .enable_live_priority_base_replenishment_exposure,
        )

    def test_research_account_has_zero_base_and_exactly_one_hand_capacity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "small.sqlite3"
            config = _small_account_config(test_config(database), database)
            self.assertEqual(config.maker_paper.initial_inventory_bonds, 0.0)
            self.assertEqual(
                config.maker_paper.additional_buying_capacity_bonds,
                ONE_HAND_BONDS,
            )
            self.assertEqual(
                config.maker_paper.maximum_inventory_bonds,
                ONE_HAND_BONDS,
            )
            self.assertEqual(
                config.maker_paper.order_quantity_bonds,
                ONE_HAND_BONDS,
            )

    def test_shared_allocator_keeps_only_the_better_order_and_one_position(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "allocator.sqlite3"
            config = _small_account_config(test_config(database), database)
            store = SQLiteStore(config)
            store.start_session()
            try:
                codes = ("132026.SH", "132024.SH")
                engines = {}
                for code in codes:
                    strategy_id = maker_comparison_strategy_id(
                        config, code, ONE_HAND_POLICY_V01_CANDIDATE,
                    )
                    engine = MakerPaperEngine(
                        config,
                        store,
                        bond_code=code,
                        priority_policy=ONE_HAND_POLICY_V01_CANDIDATE,
                        fill_modes=("priority",),
                        include_windfall=False,
                        strategy_ids_by_mode={"priority": strategy_id},
                    )
                    engine._start_date("2026-08-31")
                    engines[code] = engine

                allocator = OneHandSharedAllocator(
                    engines,
                    initial_cash_cny=1_400.0,
                    capital_ready_ts_ms=1_785_999_000_000,
                )
                ticks = {
                    "132026.SH": _tick(
                        1, "132026.SH", bid=136.000, ask=136.600,
                    ),
                    "132024.SH": _tick(
                        2, "132024.SH", bid=136.000, ask=136.250,
                    ),
                }
                allocator.last_bond_ticks.update(ticks)
                for code, engine in engines.items():
                    account = next(iter(engine.accounts.values()))
                    account.last_bid = ticks[code].bid1
                    account.last_ask = ticks[code].ask1
                    order = engine._new_order(
                        account,
                        ticks[code],
                        side="buy",
                        kind="low_bid_reversion",
                        lot_id=None,
                        price=136.001,
                        quantity=ONE_HAND_BONDS,
                        queue_ahead=0.0,
                        target_price=None,
                        price_boundary=136.001,
                        persist=True,
                    )
                    account.buy_order = order

                allocator._reconcile(ticks["132026.SH"])
                allocator._assert_invariants()
                self.assertEqual(allocator.selected_code, "132026.SH")
                self.assertIsNotNone(
                    next(iter(engines["132026.SH"].accounts.values())).buy_order
                )
                self.assertIsNone(
                    next(iter(engines["132024.SH"].accounts.values())).buy_order
                )

                selected = engines["132026.SH"]
                selected_account = next(iter(selected.accounts.values()))
                selected_order = selected_account.buy_order
                assert selected_order is not None
                accepted = selected._fill_buy(
                    selected_account,
                    ticks["132026.SH"],
                    selected_order,
                    ONE_HAND_BONDS,
                    ticks["132026.SH"].market_ts_ms * 1_000_000,
                    kind="low_bid_reversion",
                    target_price=None,
                    persist=True,
                )
                self.assertTrue(accepted)
                self.assertEqual(selected_account.inventory, ONE_HAND_BONDS)

                other = engines["132024.SH"]
                other_account = next(iter(other.accounts.values()))
                active_order = other._new_order(
                    other_account,
                    ticks["132024.SH"],
                    side="buy",
                    kind="deep_discount_sweep",
                    lot_id=None,
                    price=136.100,
                    quantity=ONE_HAND_BONDS,
                    queue_ahead=0.0,
                    target_price=None,
                    price_boundary=136.100,
                    persist=True,
                )
                rejected = other._fill_buy(
                    other_account,
                    ticks["132024.SH"],
                    active_order,
                    ONE_HAND_BONDS,
                    ticks["132024.SH"].market_ts_ms * 1_000_000,
                    kind="deep_discount_sweep",
                    target_price=None,
                    persist=True,
                    reason="active_deep_discount_sweep",
                )
                self.assertFalse(rejected)
                self.assertEqual(other_account.inventory, 0.0)
                self.assertEqual(
                    selected_account.inventory + other_account.inventory,
                    ONE_HAND_BONDS,
                )
            finally:
                store.close()

    def test_shared_cash_is_not_recapitalized_after_a_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "cash.sqlite3"
            config = _small_account_config(test_config(database), database)
            store = SQLiteStore(config)
            store.start_session()
            try:
                code = "132026.SH"
                strategy_id = maker_comparison_strategy_id(
                    config, code, ONE_HAND_POLICY_V01_CANDIDATE,
                )
                engine = MakerPaperEngine(
                    config,
                    store,
                    bond_code=code,
                    priority_policy=ONE_HAND_POLICY_V01_CANDIDATE,
                    fill_modes=("priority",),
                    include_windfall=False,
                    strategy_ids_by_mode={"priority": strategy_id},
                )
                engine._start_date("2026-08-31")
                allocator = OneHandSharedAllocator(
                    {code: engine},
                    initial_cash_cny=1_360.0,
                    capital_ready_ts_ms=1_785_999_000_000,
                )
                tick = _tick(1, code, bid=135.900, ask=136.100)
                allocator.last_bond_ticks[code] = tick
                account = next(iter(engine.accounts.values()))
                order = engine._new_order(
                    account,
                    tick,
                    side="buy",
                    kind="low_bid_reversion",
                    lot_id=None,
                    price=136.000,
                    quantity=ONE_HAND_BONDS,
                    queue_ahead=0.0,
                    target_price=None,
                    price_boundary=136.000,
                    persist=True,
                )
                account.buy_order = order
                self.assertTrue(engine._fill_buy(
                    account,
                    tick,
                    order,
                    ONE_HAND_BONDS,
                    tick.market_ts_ms * 1_000_000,
                    kind="low_bid_reversion",
                    target_price=None,
                    persist=True,
                ))
                self.assertAlmostEqual(allocator.shared_cash_cny, 0.0)

                order.limit_price = 135.900
                allocator._observe_fill(
                    account,
                    tick,
                    order,
                    "sell",
                    ONE_HAND_BONDS,
                    "test_loss_exit",
                )
                # The simulated exit is 135.900, one yuan below the required
                # 1,360 yuan for the next complete hand.
                self.assertAlmostEqual(allocator.shared_cash_cny, 1_359.0)
                next_order = engine._new_order(
                    account,
                    tick,
                    side="buy",
                    kind="low_bid_reversion",
                    lot_id=None,
                    price=136.000,
                    quantity=ONE_HAND_BONDS,
                    queue_ahead=0.0,
                    target_price=None,
                    price_boundary=136.000,
                    persist=True,
                )
                self.assertFalse(allocator._allow_buy_fill(
                    code,
                    account,
                    tick,
                    next_order,
                    ONE_HAND_BONDS,
                    "low_bid_reversion",
                    "passive_buy",
                ))
                self.assertEqual(allocator.initial_cash_cny, 1_360.0)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
