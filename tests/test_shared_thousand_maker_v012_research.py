from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from zhaiquant.database import SQLiteStore
from zhaiquant.maker import MarketAssessment, ReplayTick, TradeEvidence
from zhaiquant.maker_paper import (
    MakerDecisionContext,
    MakerPaperEngine,
    SHARED_THOUSAND_POLICY_V01_CANDIDATE,
    SHARED_THOUSAND_POLICY_V011_CANDIDATE,
    SHARED_THOUSAND_POLICY_V012_CANDIDATE,
    SHARED_THOUSAND_POLICY_V03_CANDIDATE,
    SHARED_THOUSAND_POLICY_V031_CANDIDATE,
    maker_comparison_strategy_id,
)
from zhaiquant.one_hand_maker_research import (
    SHARED_THOUSAND_BONDS,
    _small_account_config,
)
from zhaiquant.shared_thousand_maker_v012_research import (
    MODEL_ID,
    PARENT_MODEL_ID,
)
from zhaiquant.types import SHANGHAI

from .helpers import test_config


def _tick(
    tick_id: int, moment: datetime, *, code: str = "132026.SH",
    bid: float = 136.701, ask: float = 136.702,
    bids: tuple[tuple[float, float], ...] | None = None,
    asks: tuple[tuple[float, float], ...] | None = None,
) -> ReplayTick:
    return ReplayTick(
        tick_id=tick_id,
        code=code,
        market_ts_ms=int(moment.timestamp() * 1_000),
        market_date=moment.date().isoformat(),
        market_time=moment.time().isoformat(timespec="milliseconds"),
        last_price=bid,
        bids=bids or tuple(
            (bid - index * 0.001, 5_000.0) for index in range(5)
        ),
        asks=asks or tuple(
            (ask + index * 0.001, 5_000.0) for index in range(5)
        ),
        trade_bonds=0.0,
        transaction_delta=0,
        inferred_side="unknown",
        side_confidence="none",
        previous_close=136.800,
    )


def _assessment(*, state: str = "possible_fall") -> MarketAssessment:
    return MarketAssessment(
        reference_price=136.800,
        reference_low=136.700,
        reference_high=136.900,
        reference_source="intraday_trade_anchor",
        reference_confidence=0.75,
        state=state,
        state_score=-1 if state != "stable" else 0,
        state_confidence=0.75,
        recent_buy_bonds=1_000.0,
        recent_sell_bonds=6_000.0,
        midpoint_change=-0.20 if state != "stable" else 0.0,
        short_ask_change=-0.20 if state != "stable" else 0.0,
        largest_ask_gap=0.0,
        downside_book_vacuum=False,
        fragile_top_bid=False,
        iron_floor_price=None,
        iron_floor_bonds=0.0,
        evidence=(),
    )


def _context() -> MakerDecisionContext:
    return MakerDecisionContext(
        reference_price=136.800,
        reference_source="intraday_trade_anchor",
        reliable_anchor=True,
        spread=0.20,
        bid_support_bonds=10_000.0,
        ask_supply_bonds=5_000.0,
        wall_threshold_bonds=5_000.0,
    )


class SharedThousandMakerV012ResearchTests(unittest.TestCase):
    def _engine(self, database: Path, *, policy, code: str = "132026.SH"):
        config = _small_account_config(
            test_config(database), database,
            shared_capacity_bonds=SHARED_THOUSAND_BONDS,
        )
        store = SQLiteStore(config)
        store.start_session()
        strategy_id = maker_comparison_strategy_id(config, code, policy)
        engine = MakerPaperEngine(
            config, store, bond_code=code,
            priority_policy=policy,
            fill_modes=("priority",), include_windfall=False,
            strategy_ids_by_mode={"priority": strategy_id},
        )
        return engine, store

    def _open_lot(
        self, engine: MakerPaperEngine, tick: ReplayTick, *,
        kind: str = "low_bid_reversion", price: float = 136.702,
        target_price: float | None = None,
    ):
        account = next(iter(engine.accounts.values()))
        order = engine._new_order(
            account, tick, side="buy", kind=kind, lot_id=None,
            price=price, quantity=SHARED_THOUSAND_BONDS,
            queue_ahead=0.0, target_price=target_price,
            price_boundary=price, persist=True,
        )
        account.buy_order = order
        self.assertTrue(engine._fill_buy(
            account, tick, order, SHARED_THOUSAND_BONDS,
            tick.market_ts_ms * 1_000_000,
            kind=kind, target_price=target_price,
            persist=True, reason="passive_buy",
        ))
        lot = next(
            item for item in account.lots.values()
            if item.entry_price is not None
        )
        return account, lot

    def _load_target_evidence(
        self, engine: MakerPaperEngine, tick: ReplayTick,
    ) -> None:
        events = tuple(
            TradeEvidence(
                tick.market_ts_ms - (3_700 - index * 600) * 1_000,
                137.000 + 0.04 * (index % 4),
                4_000.0, 1, "buy",
            )
            for index in range(7)
        ) + (
            TradeEvidence(
                tick.market_ts_ms - 126_000,
                136.603, 1_000.0, 1, "sell",
            ),
        )
        engine.analyzer.session_market_date = tick.market_date
        engine.analyzer.session_trade_evidence.extend(events)
        engine.analyzer.trade_evidence.append(events[-1])

    def test_v012_is_direct_child_of_effective_v011_and_preserves_parents(self) -> None:
        self.assertEqual(MODEL_ID, "maker_shared_1000_v0_12_candidate")
        self.assertEqual(PARENT_MODEL_ID, SHARED_THOUSAND_POLICY_V011_CANDIDATE.model_id)
        self.assertEqual(
            SHARED_THOUSAND_POLICY_V012_CANDIDATE.model_version,
            "0.12-candidate",
        )
        self.assertTrue(
            SHARED_THOUSAND_POLICY_V012_CANDIDATE
                .enable_session_resilient_ordinary_entry
        )
        self.assertTrue(
            SHARED_THOUSAND_POLICY_V012_CANDIDATE
                .enable_guarded_live_priority_extra_inventory_exit_exposure
        )
        self.assertFalse(
            SHARED_THOUSAND_POLICY_V012_CANDIDATE
                .enable_live_priority_extra_inventory_exit_exposure
        )
        self.assertFalse(
            SHARED_THOUSAND_POLICY_V011_CANDIDATE
                .enable_session_resilient_ordinary_entry
        )
        self.assertFalse(
            SHARED_THOUSAND_POLICY_V01_CANDIDATE
                .enable_session_resilient_ordinary_entry
        )
        self.assertTrue(
            SHARED_THOUSAND_POLICY_V03_CANDIDATE
                .enable_session_resilient_ordinary_entry
        )

    def test_v012_and_v031_profiles_are_behaviorally_equal_not_same_identity(self) -> None:
        excluded = {"model_id", "model_version", "parent_model_id"}
        v012 = {
            key: value for key, value in vars(
                SHARED_THOUSAND_POLICY_V012_CANDIDATE,
            ).items() if key not in excluded
        }
        v031 = {
            key: value for key, value in vars(
                SHARED_THOUSAND_POLICY_V031_CANDIDATE,
            ).items() if key not in excluded
        }
        self.assertEqual(v012, v031)
        self.assertNotEqual(
            SHARED_THOUSAND_POLICY_V012_CANDIDATE.parent_model_id,
            SHARED_THOUSAND_POLICY_V031_CANDIDATE.parent_model_id,
        )

    def test_target_frame_proves_v011_omission_and_v012_repair(self) -> None:
        moment = datetime(2026, 9, 2, 10, 44, 3, tzinfo=SHANGHAI)
        tick = _tick(
            1, moment, code="132024.SH", bid=136.501, ask=137.199,
            bids=(
                (136.501, 2_000.0),
                (136.500, 1_000.0),
                (136.251, 2_000.0),
                (136.250, 1_000.0),
                (136.220, 1_000.0),
            ),
            asks=(
                (137.199, 1_000.0),
                (137.200, 15_000.0),
                (137.250, 4_000.0),
                (137.300, 1_000.0),
                (137.476, 1_190.0),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            stores: list[SQLiteStore] = []
            try:
                orders = {}
                for name, policy in (
                    ("v011", SHARED_THOUSAND_POLICY_V011_CANDIDATE),
                    ("v012", SHARED_THOUSAND_POLICY_V012_CANDIDATE),
                ):
                    engine, store = self._engine(
                        Path(temporary) / f"{name}.sqlite3",
                        policy=policy, code="132024.SH",
                    )
                    stores.append(store)
                    engine._start_date(moment.date().isoformat())
                    self._load_target_evidence(engine, tick)
                    stable = _assessment(state="stable")
                    engine.last_market_assessment = stable
                    engine.previous_close_reference = 136.850
                    account = next(iter(engine.accounts.values()))
                    engine._refresh_orders(account, tick, stable, persist=False)
                    orders[name] = account.buy_order

                self.assertIsNone(orders["v011"])
                self.assertIsNotNone(orders["v012"])
                assert orders["v012"] is not None
                self.assertEqual(
                    orders["v012"].kind, "session_resilient_value_entry",
                )
                self.assertAlmostEqual(orders["v012"].limit_price, 136.502)
            finally:
                for store in stores:
                    store.close()

    def test_ordinary_parent_exit_gap_still_receives_v011_fallback(self) -> None:
        moment = datetime(2026, 9, 2, 11, 14, 41, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "ordinary.sqlite3",
                policy=SHARED_THOUSAND_POLICY_V012_CANDIDATE,
            )
            try:
                engine._start_date(moment.date().isoformat())
                account, lot = self._open_lot(engine, _tick(1, moment))
                with patch.object(engine, "_decision_context", return_value=_context()):
                    engine._refresh_orders(
                        account, _tick(1, moment), _assessment(), persist=True,
                    )
                self.assertNotIn(lot.db_id, account.sell_orders)

                next_tick = _tick(
                    2, moment + timedelta(minutes=3, seconds=24),
                    bid=136.400, ask=136.511,
                )
                with patch.object(engine, "_decision_context", return_value=_context()):
                    engine._refresh_orders(
                        account, next_tick, _assessment(), persist=True,
                    )
                order = account.sell_orders[lot.db_id]
                self.assertEqual(
                    order.kind, "live_priority_extra_inventory_exit",
                )
                self.assertAlmostEqual(order.limit_price, 136.510)
            finally:
                store.close()

    def test_session_resilient_lot_is_not_added_to_guarded_exit_whitelist(self) -> None:
        moment = datetime(2026, 9, 2, 10, 44, 3, tzinfo=SHANGHAI)
        live_kinds = {
            "live_priority_extra_inventory_exit",
            "live_priority_extra_inventory_isolated_hold",
        }
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(
                Path(temporary) / "resilient.sqlite3",
                policy=SHARED_THOUSAND_POLICY_V012_CANDIDATE,
                code="132024.SH",
            )
            try:
                engine._start_date(moment.date().isoformat())
                account, lot = self._open_lot(
                    engine, _tick(1, moment, code="132024.SH"),
                    kind="session_resilient_value_entry",
                    target_price=137.000,
                )
                falling = _tick(
                    2, moment + timedelta(minutes=3), code="132024.SH",
                    bid=136.400, ask=136.511,
                )
                with patch.object(engine, "_decision_context", return_value=_context()):
                    engine._refresh_orders(
                        account, falling, _assessment(), persist=True,
                    )
                order = account.sell_orders.get(lot.db_id)
                if order is not None:
                    self.assertNotIn(order.kind, live_kinds)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
