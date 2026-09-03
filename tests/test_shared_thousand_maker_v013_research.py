from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from zhaiquant.database import SQLiteStore
from zhaiquant.maker import ReplayTick
from zhaiquant.maker_paper import (
    MakerPaperEngine,
    MakerPaperPortfolio,
    SHARED_THOUSAND_POLICY_V01_CANDIDATE,
    SHARED_THOUSAND_POLICY_V012_CANDIDATE,
    SHARED_THOUSAND_POLICY_V013_CANDIDATE,
    maker_comparison_strategy_id,
)
from zhaiquant.one_hand_maker_research import (
    SHARED_THOUSAND_BONDS,
    _small_account_config,
)
from zhaiquant.shared_thousand_maker_v013_research import (
    MODEL_ID,
    PARENT_MODEL_ID,
    AllocationParametersV03,
    SharedThousandV013Allocator,
    score_buy_intent_v013,
)
from zhaiquant.shared_thousand_maker_v03_research import score_buy_intent_v03
from zhaiquant.types import SHANGHAI

from .helpers import test_config


def _tick(
    moment: datetime, *, ask1_bonds: float = 1_000.0,
    include_next_ask: bool = True,
) -> ReplayTick:
    asks = [(136.499, ask1_bonds)]
    if include_next_ask:
        asks.extend([
            (138.879, 1_000.0),
            (138.880, 1_000.0),
            (139.000, 8_000.0),
            (139.500, 20_000.0),
        ])
    return ReplayTick(
        tick_id=1,
        code="132024.SH",
        market_ts_ms=int(moment.timestamp() * 1_000),
        market_date=moment.date().isoformat(),
        market_time=moment.time().isoformat(timespec="milliseconds"),
        last_price=136.499,
        bids=(
            (136.201, 2_000.0),
            (136.200, 1_000.0),
            (136.101, 2_000.0),
            (136.100, 3_000.0),
            (135.001, 6_000.0),
        ),
        asks=tuple(asks),
        trade_bonds=0.0,
        transaction_delta=0,
        inferred_side="none",
        side_confidence="none",
        previous_close=138.900,
    )


class SharedThousandMakerV013ResearchTests(unittest.TestCase):
    def test_realtime_portfolio_keeps_v01_and_v013_capital_separate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "shared-v013-realtime.sqlite3"
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
                realtime_comparison_model_ids=(
                    SHARED_THOUSAND_POLICY_V01_CANDIDATE.model_id,
                    SHARED_THOUSAND_POLICY_V013_CANDIDATE.model_id,
                ),
                super_windfall_enabled=False,
            ))
            store = SQLiteStore(config)
            store.start_session()
            try:
                portfolio = MakerPaperPortfolio(config, store)
                runtimes = {
                    runtime.policy.model_id: runtime
                    for runtime in portfolio.shared_capital_runtimes
                }
                self.assertEqual(set(runtimes), {
                    SHARED_THOUSAND_POLICY_V01_CANDIDATE.model_id,
                    SHARED_THOUSAND_POLICY_V013_CANDIDATE.model_id,
                })
                portfolio.rebuild_date("2026-09-03")
                baseline = runtimes[
                    SHARED_THOUSAND_POLICY_V01_CANDIDATE.model_id
                ]
                candidate = runtimes[
                    SHARED_THOUSAND_POLICY_V013_CANDIDATE.model_id
                ]
                self.assertIsInstance(
                    candidate.allocator, SharedThousandV013Allocator,
                )
                self.assertIsNot(baseline.allocator, candidate.allocator)
                self.assertTrue(all(
                    row["shared_capacity_bonds"] == SHARED_THOUSAND_BONDS
                    for runtime in runtimes.values()
                    for row in runtime.runtime_summary()["accounts"]
                ))
                assignments = store.connection.execute(
                    "SELECT bond_code, model_id FROM "
                    "maker_paper_model_assignments WHERE market_date=?",
                    ("2026-09-03",),
                ).fetchall()
                self.assertEqual(
                    {(row["bond_code"], row["model_id"])
                     for row in assignments},
                    {
                        ("132026.SH", policy.model_id)
                        for policy in (
                            SHARED_THOUSAND_POLICY_V01_CANDIDATE,
                            SHARED_THOUSAND_POLICY_V013_CANDIDATE,
                        )
                    } | {
                        ("132024.SH", policy.model_id)
                        for policy in (
                            SHARED_THOUSAND_POLICY_V01_CANDIDATE,
                            SHARED_THOUSAND_POLICY_V013_CANDIDATE,
                        )
                    },
                )
            finally:
                store.close()

    def _engine(self, database: Path):
        config = _small_account_config(
            test_config(database),
            database,
            shared_capacity_bonds=SHARED_THOUSAND_BONDS,
        )
        store = SQLiteStore(config)
        store.start_session()
        strategy_id = maker_comparison_strategy_id(
            config,
            "132024.SH",
            SHARED_THOUSAND_POLICY_V013_CANDIDATE,
        )
        engine = MakerPaperEngine(
            config,
            store,
            bond_code="132024.SH",
            priority_policy=SHARED_THOUSAND_POLICY_V013_CANDIDATE,
            fill_modes=("priority",),
            include_windfall=False,
            strategy_ids_by_mode={"priority": strategy_id},
        )
        return engine, store

    def _order(
        self, engine: MakerPaperEngine, tick: ReplayTick, *,
        kind: str = "deep_discount_sweep",
        target_price: float | None = None,
    ):
        account = next(iter(engine.accounts.values()))
        order = engine._new_order(
            account,
            tick,
            side="buy",
            kind=kind,
            lot_id=None,
            price=136.499,
            quantity=SHARED_THOUSAND_BONDS,
            queue_ahead=0.0,
            target_price=target_price,
            price_boundary=136.499,
            persist=True,
        )
        return account, order

    def test_v013_is_an_immutable_child_of_v012(self) -> None:
        self.assertEqual(MODEL_ID, "maker_shared_1000_v0_13_candidate")
        self.assertEqual(PARENT_MODEL_ID, SHARED_THOUSAND_POLICY_V012_CANDIDATE.model_id)
        self.assertEqual(
            SHARED_THOUSAND_POLICY_V013_CANDIDATE.model_version,
            "0.13-candidate",
        )
        excluded = {"model_id", "model_version", "parent_model_id"}
        parent = {
            key: value for key, value in vars(
                SHARED_THOUSAND_POLICY_V012_CANDIDATE,
            ).items() if key not in excluded
        }
        child = {
            key: value for key, value in vars(
                SHARED_THOUSAND_POLICY_V013_CANDIDATE,
            ).items() if key not in excluded
        }
        self.assertEqual(child, parent)

    def test_full_deep_offer_sweep_uses_first_remaining_ask(self) -> None:
        moment = datetime(2026, 8, 4, 9, 52, 46, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(Path(temporary) / "full.sqlite3")
            try:
                engine._start_date(moment.date().isoformat())
                tick = _tick(moment)
                account, order = self._order(engine, tick)
                parameters = AllocationParametersV03()
                parent = score_buy_intent_v03(
                    bond_code=tick.code,
                    engine=engine,
                    account=account,
                    order=order,
                    tick=tick,
                    parameters=parameters,
                    active_fill=True,
                    shared_capacity_bonds=SHARED_THOUSAND_BONDS,
                )
                child = score_buy_intent_v013(
                    bond_code=tick.code,
                    engine=engine,
                    account=account,
                    order=order,
                    tick=tick,
                    parameters=parameters,
                    active_fill=True,
                    fill_quantity=1_000.0,
                    shared_capacity_bonds=SHARED_THOUSAND_BONDS,
                )
                self.assertAlmostEqual(parent.gross_edge_per_bond, 0.0)
                self.assertLess(parent.score_cny, 0.0)
                self.assertAlmostEqual(child.exit_price, 138.878)
                self.assertAlmostEqual(child.gross_edge_per_bond, 2.379)
                self.assertGreater(child.score_cny, 0.0)
            finally:
                store.close()

    def test_partial_consumption_cannot_borrow_next_ask(self) -> None:
        moment = datetime(2026, 8, 4, 9, 52, 46, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(Path(temporary) / "partial.sqlite3")
            try:
                engine._start_date(moment.date().isoformat())
                tick = _tick(moment)
                account, order = self._order(engine, tick)
                score = score_buy_intent_v013(
                    bond_code=tick.code,
                    engine=engine,
                    account=account,
                    order=order,
                    tick=tick,
                    parameters=AllocationParametersV03(),
                    active_fill=True,
                    fill_quantity=999.0,
                    shared_capacity_bonds=SHARED_THOUSAND_BONDS,
                )
                self.assertAlmostEqual(score.exit_price, order.limit_price)
                self.assertAlmostEqual(score.gross_edge_per_bond, 0.0)
            finally:
                store.close()

    def test_missing_remaining_ask_cannot_create_exit_value(self) -> None:
        moment = datetime(2026, 8, 4, 9, 52, 46, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(Path(temporary) / "missing.sqlite3")
            try:
                engine._start_date(moment.date().isoformat())
                tick = _tick(moment, include_next_ask=False)
                account, order = self._order(engine, tick)
                score = score_buy_intent_v013(
                    bond_code=tick.code,
                    engine=engine,
                    account=account,
                    order=order,
                    tick=tick,
                    parameters=AllocationParametersV03(),
                    active_fill=True,
                    fill_quantity=1_000.0,
                    shared_capacity_bonds=SHARED_THOUSAND_BONDS,
                )
                self.assertAlmostEqual(score.gross_edge_per_bond, 0.0)
            finally:
                store.close()

    def test_tail_sweep_keeps_v012_target_scoring(self) -> None:
        moment = datetime(2026, 8, 21, 13, 55, 28, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(Path(temporary) / "tail.sqlite3")
            try:
                engine._start_date(moment.date().isoformat())
                tick = _tick(moment)
                account, order = self._order(
                    engine,
                    tick,
                    kind="sweep_tail",
                    target_price=137.698,
                )
                parameters = AllocationParametersV03()
                parent = score_buy_intent_v03(
                    bond_code=tick.code,
                    engine=engine,
                    account=account,
                    order=order,
                    tick=tick,
                    parameters=parameters,
                    active_fill=True,
                    shared_capacity_bonds=SHARED_THOUSAND_BONDS,
                )
                child = score_buy_intent_v013(
                    bond_code=tick.code,
                    engine=engine,
                    account=account,
                    order=order,
                    tick=tick,
                    parameters=parameters,
                    active_fill=True,
                    fill_quantity=1_000.0,
                    shared_capacity_bonds=SHARED_THOUSAND_BONDS,
                )
                self.assertEqual(child, parent)
            finally:
                store.close()

    def test_existing_lower_target_caps_post_sweep_exit(self) -> None:
        moment = datetime(2026, 8, 4, 9, 52, 46, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temporary:
            engine, store = self._engine(Path(temporary) / "target.sqlite3")
            try:
                engine._start_date(moment.date().isoformat())
                tick = _tick(moment)
                account, order = self._order(
                    engine,
                    tick,
                    target_price=137.000,
                )
                score = score_buy_intent_v013(
                    bond_code=tick.code,
                    engine=engine,
                    account=account,
                    order=order,
                    tick=tick,
                    parameters=AllocationParametersV03(),
                    active_fill=True,
                    fill_quantity=1_000.0,
                    shared_capacity_bonds=SHARED_THOUSAND_BONDS,
                )
                self.assertAlmostEqual(score.exit_price, 137.000)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
