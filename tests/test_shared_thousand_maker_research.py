from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from zhaiquant.database import SQLiteStore
from zhaiquant.maker import ReplayTick
from zhaiquant.maker_paper import (
    MakerPaperEngine,
    MakerPaperPortfolio,
    PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE,
    SHARED_THOUSAND_POLICY_V01_CANDIDATE,
    maker_comparison_strategy_id,
)
from zhaiquant.one_hand_maker_research import (
    SHARED_THOUSAND_BONDS,
    SharedCapitalAllocator,
    _small_account_config,
)
from zhaiquant.shared_thousand_maker_research import (
    DEFAULT_SWITCH_MINIMUM_CNY,
    DEFAULT_TIE_TOLERANCE_CNY,
    MODEL_ID,
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
        bids=tuple((bid - index * 0.001, 5_000.0) for index in range(5)),
        asks=tuple((ask + index * 0.001, 5_000.0) for index in range(5)),
        trade_bonds=0.0,
        transaction_delta=0,
        inferred_side="unknown",
        side_confidence="none",
        previous_close=136.0,
    )


class SharedThousandMakerResearchTests(unittest.TestCase):
    def test_realtime_portfolio_uses_one_combination_level_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "shared-realtime.sqlite3"
            base = test_config(database)
            config = replace(base, maker_paper=replace(
                base.maker_paper,
                enabled=True,
                bond_codes=("132026.SH", "132024.SH"),
                underlying_stock_codes={
                    "132026.SH": "600900.SH",
                    "132024.SH": "600362.SH",
                },
                fill_modes=(),
                realtime_comparison_model_ids=(MODEL_ID,),
                super_windfall_enabled=False,
            ))
            store = SQLiteStore(config)
            store.start_session()
            try:
                portfolio = MakerPaperPortfolio(config, store)
                self.assertEqual(len(portfolio.shared_capital_runtimes), 1)
                self.assertTrue(all(
                    not engines for engines in portfolio.comparison_engines.values()
                ))

                portfolio.rebuild_date("2026-09-03")
                runtime = portfolio.shared_capital_runtimes[0]
                summary = runtime.runtime_summary()
                self.assertEqual(len(summary["accounts"]), 2)
                self.assertEqual(
                    {row["model_id"] for row in summary["accounts"]},
                    {MODEL_ID},
                )
                self.assertTrue(all(
                    row["initial_inventory"] == 0.0
                    and row["maximum_inventory"] == SHARED_THOUSAND_BONDS
                    and row["shared_capacity_bonds"] == SHARED_THOUSAND_BONDS
                    for row in summary["accounts"]
                ))
                assignments = store.connection.execute(
                    "SELECT bond_code, model_id FROM "
                    "maker_paper_model_assignments WHERE market_date=?",
                    ("2026-09-03",),
                ).fetchall()
                self.assertEqual(
                    {(row["bond_code"], row["model_id"]) for row in assignments},
                    {
                        ("132026.SH", MODEL_ID),
                        ("132024.SH", MODEL_ID),
                    },
                )
            finally:
                store.close()

    def test_corrected_candidate_is_a_new_child_with_scaled_thresholds(
        self,
    ) -> None:
        self.assertEqual(MODEL_ID, "maker_shared_1000_v0_1_candidate")
        self.assertEqual(SHARED_THOUSAND_BONDS, 1_000.0)
        self.assertEqual(DEFAULT_SWITCH_MINIMUM_CNY, 100.0)
        self.assertEqual(DEFAULT_TIE_TOLERANCE_CNY, 10.0)
        self.assertEqual(
            SHARED_THOUSAND_POLICY_V01_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE.model_id,
        )

    def test_account_has_zero_base_and_exactly_one_thousand_bond_capacity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "shared-thousand.sqlite3"
            config = _small_account_config(
                test_config(database),
                database,
                shared_capacity_bonds=SHARED_THOUSAND_BONDS,
            )
            self.assertEqual(config.maker_paper.initial_inventory_bonds, 0.0)
            self.assertEqual(
                config.maker_paper.additional_buying_capacity_bonds,
                SHARED_THOUSAND_BONDS,
            )
            self.assertEqual(
                config.maker_paper.maximum_inventory_bonds,
                SHARED_THOUSAND_BONDS,
            )
            self.assertEqual(
                config.maker_paper.order_quantity_bonds,
                SHARED_THOUSAND_BONDS,
            )

    def test_two_bonds_share_one_thousand_bond_position(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "allocator.sqlite3"
            config = _small_account_config(
                test_config(database),
                database,
                shared_capacity_bonds=SHARED_THOUSAND_BONDS,
            )
            store = SQLiteStore(config)
            store.start_session()
            try:
                codes = ("132026.SH", "132024.SH")
                engines = {}
                ticks = {
                    "132026.SH": _tick(
                        1, "132026.SH", bid=136.000, ask=136.600,
                    ),
                    "132024.SH": _tick(
                        2, "132024.SH", bid=136.000, ask=136.250,
                    ),
                }
                for code in codes:
                    strategy_id = maker_comparison_strategy_id(
                        config, code, SHARED_THOUSAND_POLICY_V01_CANDIDATE,
                    )
                    engine = MakerPaperEngine(
                        config,
                        store,
                        bond_code=code,
                        priority_policy=SHARED_THOUSAND_POLICY_V01_CANDIDATE,
                        fill_modes=("priority",),
                        include_windfall=False,
                        strategy_ids_by_mode={"priority": strategy_id},
                    )
                    engine._start_date("2026-08-31")
                    account = next(iter(engine.accounts.values()))
                    account.last_bid = ticks[code].bid1
                    account.last_ask = ticks[code].ask1
                    account.buy_order = engine._new_order(
                        account,
                        ticks[code],
                        side="buy",
                        kind="low_bid_reversion",
                        lot_id=None,
                        price=136.001,
                        quantity=SHARED_THOUSAND_BONDS,
                        queue_ahead=0.0,
                        target_price=None,
                        price_boundary=136.001,
                        persist=True,
                    )
                    engines[code] = engine

                allocator = SharedCapitalAllocator(
                    engines,
                    initial_cash_cny=140_000.0,
                    capital_ready_ts_ms=1_785_999_000_000,
                    shared_capacity_bonds=SHARED_THOUSAND_BONDS,
                )
                allocator.last_bond_ticks.update(ticks)
                allocator._reconcile(ticks["132026.SH"])
                allocator._assert_invariants()
                self.assertEqual(allocator.selected_code, "132026.SH")

                selected = engines["132026.SH"]
                selected_account = next(iter(selected.accounts.values()))
                selected_order = selected_account.buy_order
                assert selected_order is not None
                self.assertTrue(selected._fill_buy(
                    selected_account,
                    ticks["132026.SH"],
                    selected_order,
                    SHARED_THOUSAND_BONDS,
                    ticks["132026.SH"].market_ts_ms * 1_000_000,
                    kind="low_bid_reversion",
                    target_price=None,
                    persist=True,
                ))

                other = engines["132024.SH"]
                other_account = next(iter(other.accounts.values()))
                active_order = other._new_order(
                    other_account,
                    ticks["132024.SH"],
                    side="buy",
                    kind="deep_discount_sweep",
                    lot_id=None,
                    price=136.100,
                    quantity=SHARED_THOUSAND_BONDS,
                    queue_ahead=0.0,
                    target_price=None,
                    price_boundary=136.100,
                    persist=True,
                )
                self.assertFalse(other._fill_buy(
                    other_account,
                    ticks["132024.SH"],
                    active_order,
                    SHARED_THOUSAND_BONDS,
                    ticks["132024.SH"].market_ts_ms * 1_000_000,
                    kind="deep_discount_sweep",
                    target_price=None,
                    persist=True,
                    reason="active_deep_discount_sweep",
                ))
                self.assertEqual(
                    selected_account.inventory + other_account.inventory,
                    SHARED_THOUSAND_BONDS,
                )
            finally:
                store.close()

    def test_shared_cash_is_not_recapitalized_after_a_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "cash.sqlite3"
            config = _small_account_config(
                test_config(database),
                database,
                shared_capacity_bonds=SHARED_THOUSAND_BONDS,
            )
            store = SQLiteStore(config)
            store.start_session()
            try:
                code = "132026.SH"
                strategy_id = maker_comparison_strategy_id(
                    config, code, SHARED_THOUSAND_POLICY_V01_CANDIDATE,
                )
                engine = MakerPaperEngine(
                    config,
                    store,
                    bond_code=code,
                    priority_policy=SHARED_THOUSAND_POLICY_V01_CANDIDATE,
                    fill_modes=("priority",),
                    include_windfall=False,
                    strategy_ids_by_mode={"priority": strategy_id},
                )
                engine._start_date("2026-08-31")
                allocator = SharedCapitalAllocator(
                    {code: engine},
                    initial_cash_cny=136_000.0,
                    capital_ready_ts_ms=1_785_999_000_000,
                    shared_capacity_bonds=SHARED_THOUSAND_BONDS,
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
                    quantity=SHARED_THOUSAND_BONDS,
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
                    SHARED_THOUSAND_BONDS,
                    tick.market_ts_ms * 1_000_000,
                    kind="low_bid_reversion",
                    target_price=None,
                    persist=True,
                ))
                order.limit_price = 135.900
                allocator._observe_fill(
                    account,
                    tick,
                    order,
                    "sell",
                    SHARED_THOUSAND_BONDS,
                    "test_loss_exit",
                )
                self.assertAlmostEqual(allocator.shared_cash_cny, 135_900.0)

                next_order = engine._new_order(
                    account,
                    tick,
                    side="buy",
                    kind="low_bid_reversion",
                    lot_id=None,
                    price=136.000,
                    quantity=SHARED_THOUSAND_BONDS,
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
                    SHARED_THOUSAND_BONDS,
                    "low_bid_reversion",
                    "passive_buy",
                ))
                self.assertEqual(allocator.initial_cash_cny, 136_000.0)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
