from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from zhaiquant.config import MakerPaperConfig
from zhaiquant.database import SQLiteStore
from zhaiquant.maker import (
    AnchorState,
    BookQuote,
    MakerParameters,
    MarketAssessment,
    Opportunity,
    ReplayTick,
    TradeEvidence,
    trend_price_discovery_assessment,
)
from zhaiquant.maker_paper import (
    MakerDecisionContext,
    MakerLot,
    MakerPaperEngine,
    MakerPaperPortfolio,
    PRIORITY_POLICY_V11,
    PRIORITY_POLICY_V12_CANDIDATE,
    PRIORITY_POLICY_V13_CANDIDATE,
    PRIORITY_POLICY_V14_CANDIDATE,
    PRIORITY_POLICY_V15_CANDIDATE,
    PRIORITY_POLICY_V16_CANDIDATE,
    PRIORITY_POLICY_V17_CANDIDATE,
    PRIORITY_POLICY_V18_CANDIDATE,
    PRIORITY_POLICY_V19_CANDIDATE,
    PRIORITY_POLICY_V110_CANDIDATE,
    PRIORITY_POLICY_V111_CANDIDATE,
    PRIORITY_POLICY_V112_CANDIDATE,
    PRIORITY_POLICY_V113_CANDIDATE,
    PRIORITY_POLICY_V114_CANDIDATE,
    PRIORITY_POLICY_V115_CANDIDATE,
    PRIORITY_POLICY_V116_CANDIDATE,
    PRIORITY_POLICY_V117_CANDIDATE,
    PRIORITY_POLICY_V118_CANDIDATE,
    PRIORITY_POLICY_V119_CANDIDATE,
    PRIORITY_POLICY_V120_CANDIDATE,
    PRIORITY_POLICY_V121_CANDIDATE,
    PRIORITY_POLICY_V122_CANDIDATE,
    PRIORITY_POLICY_V123_CANDIDATE,
    PRIORITY_POLICY_V124_CANDIDATE,
    PRIORITY_POLICY_V125_CANDIDATE,
    PRIORITY_POLICY_V126_CANDIDATE,
    PRIORITY_POLICY_V127_CANDIDATE,
    PRIORITY_POLICY_V128_CANDIDATE,
    PRIORITY_POLICY_V129_CANDIDATE,
    PRIORITY_POLICY_V130_CANDIDATE,
    PRIORITY_POLICY_V131_CANDIDATE,
    PRIORITY_POLICY_V132_CANDIDATE,
    PRIORITY_POLICY_V133_CANDIDATE,
    PRIORITY_POLICY_V134_CANDIDATE,
    PRIORITY_POLICY_V135_CANDIDATE,
    PRIORITY_POLICY_V136_CANDIDATE,
    PRIORITY_POLICY_V137_CANDIDATE,
    PRIORITY_POLICY_V138_CANDIDATE,
    PRIORITY_POLICY_V139_CANDIDATE,
    PRIORITY_POLICY_V140_CANDIDATE,
    PRIORITY_POLICY_V141_CANDIDATE,
    PRIORITY_POLICY_V142_CANDIDATE,
    PRIORITY_POLICY_V143_CANDIDATE,
    PRIORITY_POLICY_V144_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V144,
    PRIORITY_POLICY_FIRST_POSITION_V145,
    PRIORITY_POLICY_FIRST_POSITION_V146,
    PRIORITY_POLICY_FIRST_POSITION_V147,
    PRIORITY_POLICY_FIRST_POSITION_V148_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V149_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V149_R2_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V150_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V21_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V22_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V251_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V251_R2_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V252_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V25_R2_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V251_R3_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V26_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V26_R2_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V26_R3_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE,
    QUEUE_POLICY_V10,
    QUEUE_POLICY_V11_CANDIDATE,
    QUEUE_POLICY_V12_CANDIDATE,
    QUEUE_POLICY_V13_CANDIDATE,
    QUEUE_POLICY_V14_CANDIDATE,
    QUEUE_POLICY_V15_CANDIDATE,
    QUEUE_POLICY_V16_CANDIDATE,
    QUEUE_POLICY_V17_CANDIDATE,
    QUEUE_POLICY_V18_CANDIDATE,
    QUEUE_POLICY_V19_CANDIDATE,
    QUEUE_POLICY_V110_CANDIDATE,
    QUEUE_POLICY_V111_CANDIDATE,
    QUEUE_POLICY_V112_CANDIDATE,
    QUEUE_POLICY_V113_CANDIDATE,
    QUEUE_POLICY_V114_CANDIDATE,
    QUEUE_POLICY_V115_CANDIDATE,
    QUEUE_POLICY_V116_CANDIDATE,
    QUEUE_POLICY_V117_CANDIDATE,
    QUEUE_POLICY_V118_CANDIDATE,
    QUEUE_POLICY_V119_CANDIDATE,
    WINDFALL_POLICY_V10,
    WINDFALL_POLICY_V11_CANDIDATE,
    WINDFALL_POLICY_V20_CANDIDATE,
    _floor_to_tick,
    maker_strategy_ids,
)
from zhaiquant.runner import MarketProcessor
from zhaiquant.types import SHANGHAI

from .helpers import make_tick, test_config


class MakerPaperTests(unittest.TestCase):
    def test_terminal_summary_reports_customer_base_short_without_forcing_fill(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "terminal-base-short.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                additional_buying_capacity_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=136_800,
                order_quantity_bonds=1_000,
                fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(config, store)
                moment = datetime(
                    2026, 8, 17, 15, 29, 59, tzinfo=SHANGHAI,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.inventory = 0.0
                account.replenishment_quantity = 1_000.0
                account.replenishment_sale_value = 140_000.0
                for lot in account.lots.values():
                    if lot.kind == "base":
                        lot.remaining_quantity = 0.0

                # A terminal timestamp is not an instruction to buy. With no
                # later executable sell, the customer's 1,000-bond short is
                # reported and carried instead of being cosmetically closed.
                tick = replace(
                    self._replay_tick(
                        moment, last=140.0, bid=139.0, ask=141.0,
                        previous_close=140.0,
                    ),
                    bids=(), asks=(),
                )
                engine.on_replay_tick(tick, persist=True)

                self.assertEqual(account.inventory, 0)
                self.assertEqual(account.customer_base_short_bonds, 1_000)
                self.assertEqual(account.extra_inventory_bonds, 0)
                summary = engine.runtime_summary()["accounts"][0]
                self.assertEqual(summary["initial_inventory"], 1_000)
                self.assertEqual(summary["maximum_inventory"], 2_000)
                self.assertEqual(
                    summary["customer_base_short_bonds"], 1_000,
                )
                self.assertEqual(summary["extra_inventory_bonds"], 0)
            finally:
                store.close()

    def test_standard_account_uses_explicit_bond_capacity_not_stale_cash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "bond-capacity.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                additional_buying_capacity_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=100,
                order_quantity_bonds=1_000,
                fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(config, store)
                moment = datetime(
                    2026, 8, 17, 9, 30, tzinfo=SHANGHAI,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                quote = self._replay_tick(
                    moment, last=140.0, bid=140.0, ask=140.0,
                )
                order = engine._new_order(
                    account, quote,
                    side="buy", kind="low_bid_reversion", lot_id=None,
                    price=140.0, quantity=1_000.0, queue_ahead=0.0,
                    target_price=None, persist=True,
                )
                account.buy_order = order
                trade = self._replay_tick(
                    moment + timedelta(seconds=3),
                    last=140.0, bid=140.0, ask=140.0,
                    trade_bonds=1_000.0, inferred_side="sell",
                )

                engine._process_resting_orders(
                    account, trade, persist=True,
                    received_ts_ns=trade.market_ts_ms * 1_000_000,
                )
                engine._mark_account(account, trade, persist=True)

                fill_quantity = store.connection.execute(
                    "SELECT SUM(quantity) FROM maker_paper_fills"
                ).fetchone()[0]
                self.assertEqual(fill_quantity, 1_000)
                self.assertEqual(account.inventory, 2_000)
                self.assertEqual(account.additional_buying_capacity, 1_000)
                self.assertAlmostEqual(account.funding_adjustment, 139_900)
                self.assertAlmostEqual(account.initial_cash, 140_000)
                self.assertAlmostEqual(account.cash, 0)
                self.assertAlmostEqual(account.trading_pnl, 0)
            finally:
                store.close()

    def _run_shared_queue_scenario(
        self, database: Path, *, later_base_order: bool,
    ) -> tuple[float, int, float]:
        base = test_config(database)
        config = replace(base, maker_paper=MakerPaperConfig(
            enabled=True,
            initial_inventory_bonds=1_000,
            maximum_inventory_bonds=2_000,
            initial_cash_cny=137_000,
            order_quantity_bonds=1_000,
            fill_modes=("queue",),
        ))
        store = SQLiteStore(config)
        try:
            engine = MakerPaperEngine(
                config, store,
                queue_policy=QUEUE_POLICY_V11_CANDIDATE,
            )
            moment = datetime(2026, 8, 14, 10, 20, 24, tzinfo=SHANGHAI)
            engine._start_date(moment.date().isoformat())
            account = engine.accounts["maker_v01_queue"]
            base_lot = next(iter(account.lots.values()))
            base_lot.original_quantity = 140
            base_lot.remaining_quantity = 140
            extra_lot_id = store.insert_maker_lot({
                "run_id": store.run_id,
                "market_date": account.market_date,
                "strategy_id": account.strategy_id,
                "kind": "low_bid_reversion",
                "opened_market_ts_ms": int(moment.timestamp() * 1_000),
                "entry_price": 136.659,
                "original_quantity": 860.0,
                "remaining_quantity": 860.0,
                "target_price": None,
                "status": "open",
                "updated_market_ts_ms": int(moment.timestamp() * 1_000),
            })
            account.lots[extra_lot_id] = MakerLot(
                extra_lot_id, "low_bid_reversion",
                int(moment.timestamp() * 1_000), 136.659,
                860.0, 860.0,
            )
            quote = self._replay_tick(
                moment, last=136.900, bid=136.800, ask=137.197,
                ask_bonds=1_000,
            )
            for lot in account.lots.values():
                order = engine._new_order(
                    account, quote, side="sell", kind="inventory_exit",
                    lot_id=lot.db_id, price=137.197,
                    quantity=lot.remaining_quantity,
                    queue_ahead=1_000.0, target_price=137.197,
                    persist=True,
                )
                if later_base_order and lot.kind == "base":
                    order.created_ms += 1_000
                account.sell_orders[lot.db_id] = order

            trade = self._replay_tick(
                moment + timedelta(seconds=45),
                last=137.197, bid=137.100, ask=137.197,
                trade_bonds=2_000, inferred_side="buy",
            )
            engine._process_resting_orders(
                account, trade, persist=True,
                received_ts_ns=trade.market_ts_ms * 1_000_000,
            )
            filled = store.connection.execute(
                "SELECT COALESCE(SUM(quantity),0) FROM maker_paper_fills"
            ).fetchone()[0]
            return account.inventory, account.fills, float(filled)
        finally:
            store.close()

    def test_queue_v11_candidate_consumes_shared_external_queue_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self._run_shared_queue_scenario(
                Path(temp) / "maker-queue-v11.sqlite3",
                later_base_order=False,
            )
            self.assertEqual(result, (0.0, 2, 1_000.0))

    def test_queue_v11_candidate_keeps_later_same_price_order_separate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self._run_shared_queue_scenario(
                Path(temp) / "maker-queue-v11-later.sqlite3",
                later_base_order=True,
            )
            self.assertEqual(result, (140.0, 1, 860.0))

    def test_queue_v12_retains_extra_exit_for_context_grace_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-queue-v12-retain.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, queue_policy=QUEUE_POLICY_V12_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 11, 18, 36, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_queue"]
                extra = MakerLot(
                    99, "low_bid_reversion",
                    int(moment.timestamp() * 1_000), 136.521,
                    1_000.0, 1_000.0,
                )
                order = engine._new_order(
                    account, self._replay_tick(
                        moment, last=136.521, bid=136.521, ask=136.907,
                    ),
                    side="sell", kind="inventory_exit", lot_id=extra.db_id,
                    price=136.907, quantity=1_000.0, queue_ahead=2_000.0,
                    target_price=136.907, persist=True,
                )

                lost_context = self._replay_tick(
                    moment + timedelta(seconds=1),
                    last=136.800, bid=136.700, ask=136.800,
                )
                self.assertTrue(
                    engine._retain_queue_extra_exit_context_grace(
                        account, extra, order, lost_context,
                    )
                )
                self.assertTrue(order.retained_after_context_loss)
                expired_context = self._replay_tick(
                    moment + timedelta(seconds=17),
                    last=136.800, bid=136.700, ask=136.800,
                )
                self.assertFalse(
                    engine._retain_queue_extra_exit_context_grace(
                        account, extra, order, expired_context,
                    )
                )
                base = replace(extra, kind="base", entry_price=None)
                self.assertFalse(
                    engine._retain_queue_extra_exit_context_grace(
                        account, base, order, lost_context,
                    )
                )
            finally:
                store.close()

    def test_queue_v12_buffers_replenishment_after_extra_then_base_exit(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-queue-v12-buffer.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, queue_policy=QUEUE_POLICY_V12_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 10, 21, 9, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_queue"]
                account.last_extra_exit_ts_ms = int(
                    (moment - timedelta(seconds=210)).timestamp() * 1_000
                )
                base = next(iter(account.lots.values()))
                sale_tick = self._replay_tick(
                    moment, last=137.197, bid=136.671, ask=137.197,
                    trade_bonds=1_000, inferred_side="buy", bid_bonds=2_000,
                )
                sale = engine._new_order(
                    account, sale_tick, side="sell", kind="inventory_exit",
                    lot_id=base.db_id, price=137.197, quantity=1_000,
                    queue_ahead=0, target_price=137.197, persist=True,
                )
                engine._fill_sell(
                    account, sale_tick, sale, 1_000,
                    sale_tick.market_ts_ms * 1_000_000, persist=True,
                )
                self.assertEqual(
                    account.pending_replenishment_exact_fill_buffer, 1_000,
                )
                self.assertEqual(account.last_extra_exit_ts_ms, 0)

                engine._replace_buy(
                    account, sale_tick, (136.671, 1_000, None),
                    "inventory_replenish", persist=True,
                )
                replenishment = account.buy_order
                self.assertIsNotNone(replenishment)
                assert replenishment is not None
                self.assertEqual(replenishment.queue_ahead, 2_000)
                self.assertEqual(
                    replenishment.exact_fill_uncertainty_buffer, 1_000,
                )
                for _ in range(3):
                    self.assertEqual(
                        engine._consume_queue(
                            replenishment, 1_000, account.fill_mode,
                        ),
                        0,
                    )
                self.assertEqual(replenishment.queue_ahead, 0)
                self.assertEqual(
                    engine._consume_queue(
                        replenishment, 1_000, account.fill_mode,
                    ),
                    1_000,
                )

                penetrated = replace(
                    replenishment, queue_ahead=2_000,
                    exact_fill_uncertainty_buffer=1_000,
                )
                self.assertEqual(
                    engine._consume_queue(
                        penetrated, 1_000, account.fill_mode,
                        price_penetrated=True,
                    ),
                    0,
                )
                self.assertEqual(penetrated.queue_ahead, 1_000)
                self.assertEqual(
                    penetrated.exact_fill_uncertainty_buffer, 0,
                )
            finally:
                store.close()

    def test_queue_v13_fills_sell_on_next_frame_after_crossed_queue_clears(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-queue-v13-sell.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, queue_policy=QUEUE_POLICY_V13_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 10, 20, 12, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_queue"]
                base = next(iter(account.lots.values()))
                quote = self._replay_tick(
                    moment - timedelta(seconds=18), last=136.796,
                    bid=136.796, ask=137.197, ask_bonds=2_000,
                )
                account.sell_orders[base.db_id] = engine._new_order(
                    account, quote, side="sell", kind="inventory_exit",
                    lot_id=base.db_id, price=137.197, quantity=1_000,
                    queue_ahead=2_000, target_price=137.197, persist=True,
                )
                trade = self._replay_tick(
                    moment, last=137.197, bid=137.197, ask=137.198,
                    bid_bonds=3_000, trade_bonds=2_000,
                    inferred_side="buy",
                )

                engine._process_resting_orders(
                    account, trade, persist=True,
                    received_ts_ns=trade.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_000)
                self.assertTrue(engine._retain_cleared_queue_for_one_tick(
                    account, account.sell_orders[base.db_id], tick=trade,
                    desired_price=137.198, desired_kind="inventory_exit",
                    desired_quantity=1_000,
                ))
                next_frame = self._replay_tick(
                    moment + timedelta(seconds=3), last=137.197,
                    bid=136.671, ask=137.198, trade_bonds=3_000,
                    inferred_side="sell",
                )
                engine._process_resting_orders(
                    account, next_frame, persist=True,
                    received_ts_ns=next_frame.market_ts_ms * 1_000_000,
                )

                self.assertEqual(account.inventory, 0)
                fill = store.connection.execute(
                    """SELECT price,quantity,fill_reason
                       FROM maker_paper_fills ORDER BY id DESC LIMIT 1"""
                ).fetchone()
                self.assertEqual(float(fill["price"]), 137.197)
                self.assertEqual(float(fill["quantity"]), 1_000)
                self.assertEqual(
                    fill["fill_reason"], "queue_cleared_next_frame_fill",
                )
            finally:
                store.close()

    def test_queue_v13_adds_next_frame_sale_uncertainty_to_replenishment_buffer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)

            def run_case(
                name: str, policy, *,
                reason: str = "queue_cleared_next_frame_fill",
            ) -> float:
                config = test_config(temp_path / f"{name}.sqlite3")
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, queue_policy=policy,
                    )
                    moment = datetime(
                        2026, 8, 14, 10, 20, 15, tzinfo=SHANGHAI,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_queue"]
                    account.last_extra_exit_ts_ms = int(
                        (moment - timedelta(seconds=210)).timestamp() * 1_000
                    )
                    base = next(iter(account.lots.values()))
                    sale_tick = self._replay_tick(
                        moment, last=137.197, bid=136.671, ask=137.198,
                        trade_bonds=3_000, inferred_side="sell",
                        bid_bonds=2_000,
                    )
                    sale = engine._new_order(
                        account, sale_tick, side="sell",
                        kind="inventory_exit", lot_id=base.db_id,
                        price=137.197, quantity=1_000, queue_ahead=0,
                        target_price=137.197, persist=True,
                    )
                    engine._fill_sell(
                        account, sale_tick, sale, 1_000,
                        sale_tick.market_ts_ms * 1_000_000,
                        persist=True,
                        reason=reason,
                    )
                    return account.pending_replenishment_exact_fill_buffer
                finally:
                    store.close()

            self.assertEqual(
                run_case("parent", QUEUE_POLICY_V12_CANDIDATE), 1_000,
            )
            self.assertEqual(
                run_case("candidate", QUEUE_POLICY_V13_CANDIDATE), 2_000,
            )
            self.assertEqual(
                run_case(
                    "crossed-residual", QUEUE_POLICY_V112_CANDIDATE,
                    reason="queue_cleared_crossed_residual_fill",
                ),
                2_000,
            )

    def test_queue_v13_fills_buy_on_next_frame_after_crossed_queue_clears(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-queue-v13-buy.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, queue_policy=QUEUE_POLICY_V13_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 10, 20, 12, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_queue"]
                quote = self._replay_tick(
                    moment - timedelta(seconds=3), last=136.672,
                    bid=136.672, ask=136.700, bid_bonds=1_000,
                )
                account.buy_order = engine._new_order(
                    account, quote, side="buy", kind="low_bid_reversion",
                    lot_id=None, price=136.672, quantity=1_000,
                    queue_ahead=1_000, target_price=None, persist=True,
                )
                trade = self._replay_tick(
                    moment, last=136.672, bid=136.671, ask=136.672,
                    ask_bonds=1_000, trade_bonds=1_000,
                    inferred_side="sell",
                )

                engine._process_resting_orders(
                    account, trade, persist=True,
                    received_ts_ns=trade.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_000)
                self.assertTrue(engine._retain_cleared_queue_for_one_tick(
                    account, account.buy_order, tick=trade,
                    desired_price=136.671, desired_kind="low_bid_reversion",
                    desired_quantity=1_000,
                ))
                next_frame = self._replay_tick(
                    moment + timedelta(seconds=3), last=136.672,
                    bid=136.671, ask=136.700, trade_bonds=1_000,
                    inferred_side="buy",
                )
                engine._process_resting_orders(
                    account, next_frame, persist=True,
                    received_ts_ns=next_frame.market_ts_ms * 1_000_000,
                )

                self.assertEqual(account.inventory, 2_000)
                fill = store.connection.execute(
                    """SELECT price,quantity,fill_reason
                       FROM maker_paper_fills ORDER BY id DESC LIMIT 1"""
                ).fetchone()
                self.assertEqual(float(fill["price"]), 136.672)
                self.assertEqual(float(fill["quantity"]), 1_000)
                self.assertEqual(
                    fill["fill_reason"], "queue_cleared_next_frame_fill",
                )
            finally:
                store.close()

    def test_queue_v13_requires_cleared_unbuffered_queue_and_crossed_book(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)

            def run_case(
                name: str, *, policy, queue_ahead: float,
                trade_bonds: float, bid: float, buffer: float = 0,
                next_price: float = 137.197,
            ) -> tuple[float, int, float, float]:
                config = test_config(temp_path / f"{name}.sqlite3")
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, queue_policy=policy,
                    )
                    moment = datetime(
                        2026, 8, 14, 10, 20, 12, tzinfo=SHANGHAI,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_queue"]
                    base = next(iter(account.lots.values()))
                    quote = self._replay_tick(
                        moment - timedelta(seconds=3), last=136.796,
                        bid=136.796, ask=137.197,
                    )
                    order = engine._new_order(
                        account, quote, side="sell", kind="inventory_exit",
                        lot_id=base.db_id, price=137.197, quantity=1_000,
                        queue_ahead=queue_ahead, target_price=137.197,
                        persist=True,
                        exact_fill_uncertainty_buffer=buffer,
                    )
                    account.sell_orders[base.db_id] = order
                    trade = self._replay_tick(
                        moment, last=137.197, bid=bid, ask=137.198,
                        bid_bonds=3_000, trade_bonds=trade_bonds,
                        inferred_side="buy",
                    )
                    engine._process_resting_orders(
                        account, trade, persist=True,
                        received_ts_ns=trade.market_ts_ms * 1_000_000,
                    )
                    next_frame = self._replay_tick(
                        moment + timedelta(seconds=3), last=next_price,
                        bid=136.671, ask=137.198, trade_bonds=3_000,
                        inferred_side="sell",
                    )
                    engine._process_resting_orders(
                        account, next_frame, persist=True,
                        received_ts_ns=next_frame.market_ts_ms * 1_000_000,
                    )
                    return (
                        account.inventory, account.fills,
                        order.queue_ahead, order.exact_fill_uncertainty_buffer,
                    )
                finally:
                    store.close()

            self.assertEqual(run_case(
                "parent", policy=QUEUE_POLICY_V12_CANDIDATE,
                queue_ahead=2_000, trade_bonds=2_000, bid=137.197,
            ), (1_000, 0, 0, 0))
            self.assertEqual(run_case(
                "queue-not-cleared", policy=QUEUE_POLICY_V13_CANDIDATE,
                queue_ahead=2_000, trade_bonds=1_000, bid=137.197,
            ), (1_000, 0, 1_000, 0))
            self.assertEqual(run_case(
                "buffer-remains", policy=QUEUE_POLICY_V13_CANDIDATE,
                queue_ahead=1_000, trade_bonds=1_000, bid=137.197,
                buffer=1_000,
            ), (1_000, 0, 0, 1_000))
            self.assertEqual(run_case(
                "book-not-crossed", policy=QUEUE_POLICY_V13_CANDIDATE,
                queue_ahead=2_000, trade_bonds=2_000, bid=137.196,
            ), (1_000, 0, 0, 0))
            self.assertEqual(run_case(
                "next-frame-price-differs", policy=QUEUE_POLICY_V13_CANDIDATE,
                queue_ahead=2_000, trade_bonds=2_000, bid=137.197,
                next_price=137.198,
            ), (1_000, 0, 0, 0))

    def test_queue_v13_does_not_reuse_trade_volume_for_special_fill(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-queue-v13-volume.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, queue_policy=QUEUE_POLICY_V13_CANDIDATE,
                )
                moment = datetime(
                    2026, 8, 14, 10, 20, 15, tzinfo=SHANGHAI,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_queue"]
                account.cash = 200_000
                base = next(iter(account.lots.values()))
                quote = self._replay_tick(
                    moment - timedelta(seconds=3), last=137.197,
                    bid=137.197, ask=137.198,
                )
                sell = engine._new_order(
                    account, quote, side="sell", kind="inventory_exit",
                    lot_id=base.db_id, price=137.197, quantity=1_000,
                    queue_ahead=0, target_price=137.197, persist=True,
                )
                sell.queue_cleared_ms = quote.market_ts_ms
                sell.queue_cleared_crossed_book = True
                account.sell_orders[base.db_id] = sell
                account.buy_order = engine._new_order(
                    account, quote, side="buy", kind="low_bid_reversion",
                    lot_id=None, price=137.197, quantity=1_000,
                    queue_ahead=0, target_price=None, persist=True,
                )
                next_frame = self._replay_tick(
                    moment, last=137.197, bid=137.196, ask=137.198,
                    trade_bonds=1_000, inferred_side="sell",
                )

                engine._process_resting_orders(
                    account, next_frame, persist=True,
                    received_ts_ns=next_frame.market_ts_ms * 1_000_000,
                )

                self.assertEqual(account.fills, 1)
                self.assertEqual(account.inventory, 2_000)
                self.assertIn(base.db_id, account.sell_orders)
                fill = store.connection.execute(
                    """SELECT side,fill_reason FROM maker_paper_fills
                       ORDER BY id DESC LIMIT 1"""
                ).fetchone()
                self.assertEqual(fill["side"], "buy")
                self.assertEqual(fill["fill_reason"], "passive_buy")
            finally:
                store.close()

    def test_queue_v14_keeps_a_bid_when_same_price_sells_just_clear_its_queue(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)

            def run_case(
                policy, *, restore_context: bool,
            ) -> tuple[bool, bool, float, int]:
                config = test_config(
                    temp_path
                    / f"queue-cleared-own-bid-{policy.model_version}-{restore_context}.sqlite3"
                )
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, queue_policy=policy,
                    )
                    moment = datetime(
                        2026, 8, 14, 10, 28, 9, tzinfo=SHANGHAI,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_queue"]
                    account.cash = 200_000.0
                    quote = self._replay_tick(
                        moment - timedelta(seconds=18), last=136.650,
                        bid=136.601, ask=136.966,
                    )
                    order = engine._new_order(
                        account, quote, side="buy", kind="low_bid_reversion",
                        lot_id=None, price=136.601, quantity=1_000.0,
                        queue_ahead=2_000.0, target_price=None, persist=True,
                    )
                    account.buy_order = order
                    clearing = self._replay_tick(
                        moment, last=136.601, bid=136.600, ask=136.965,
                        trade_bonds=2_000.0, inferred_side="sell",
                    )
                    engine._process_resting_orders(
                        account, clearing, persist=True,
                        received_ts_ns=clearing.market_ts_ms * 1_000_000,
                    )
                    self.assertEqual(order.queue_ahead, 0)
                    self.assertFalse(order.queue_cleared_crossed_book)

                    engine._replace_buy(
                        account, clearing, None, "low_bid_reversion",
                        persist=True,
                    )
                    retained_after_clear = account.buy_order is order
                    if not retained_after_clear:
                        return False, False, account.inventory, account.fills
                    if not restore_context:
                        expired = self._replay_tick(
                            moment + timedelta(seconds=6), last=136.601,
                            bid=136.600, ask=136.965,
                        )
                        engine._replace_buy(
                            account, expired, None, "low_bid_reversion",
                            persist=True,
                        )
                        return (
                            True, account.buy_order is order,
                            account.inventory, account.fills,
                        )

                    restored = self._replay_tick(
                        moment + timedelta(seconds=3), last=136.601,
                        bid=136.601, ask=136.965,
                    )
                    engine._replace_buy(
                        account, restored, (136.601, 1_000.0, None),
                        "low_bid_reversion", persist=True,
                    )
                    self.assertIs(account.buy_order, order)
                    later_sell = self._replay_tick(
                        moment + timedelta(seconds=32), last=136.601,
                        bid=136.601, ask=136.965, trade_bonds=1_000.0,
                        inferred_side="sell",
                    )
                    engine._process_resting_orders(
                        account, later_sell, persist=True,
                        received_ts_ns=later_sell.market_ts_ms * 1_000_000,
                    )
                    return (
                        retained_after_clear, account.buy_order is order,
                        account.inventory, account.fills,
                    )
                finally:
                    store.close()

            self.assertEqual(
                run_case(QUEUE_POLICY_V13_CANDIDATE, restore_context=True),
                (False, False, 1_000.0, 0),
            )
            self.assertEqual(
                run_case(QUEUE_POLICY_V14_CANDIDATE, restore_context=True),
                (True, False, 2_000.0, 1),
            )
            self.assertEqual(
                run_case(QUEUE_POLICY_V14_CANDIDATE, restore_context=False),
                (True, False, 1_000.0, 0),
            )
            self.assertEqual(
                QUEUE_POLICY_V13_CANDIDATE.queue_cleared_buy_context_grace_seconds,
                0,
            )
            self.assertEqual(
                PRIORITY_POLICY_V112_CANDIDATE.queue_cleared_buy_context_grace_seconds,
                0,
            )

    def test_queue_v15_keeps_a_base_offer_when_buys_just_clear_its_queue(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)

            def run_case(
                policy, *, restore_context: bool,
                ask_improvement: float = 0.001,
                extra_inventory: bool = False,
            ) -> tuple[bool, bool, float, int]:
                config = test_config(
                    temp_path
                    / f"queue-cleared-own-ask-{policy.model_version}-{restore_context}.sqlite3"
                )
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, queue_policy=policy,
                    )
                    moment = datetime(
                        2026, 8, 14, 9, 31, 51, tzinfo=SHANGHAI,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_queue"]
                    base = next(iter(account.lots.values()))
                    if extra_inventory:
                        base.kind = "extra"
                        base.entry_price = 136.000
                    quote = self._replay_tick(
                        moment - timedelta(seconds=18), last=0.0,
                        bid=134.001, ask=137.479,
                    )
                    order = engine._new_order(
                        account, quote, side="sell", kind="inventory_exit",
                        lot_id=base.db_id, price=137.479, quantity=1_000.0,
                        queue_ahead=1_000.0, target_price=137.479,
                        persist=True,
                    )
                    account.sell_orders[base.db_id] = order
                    clearing = self._replay_tick(
                        moment, last=137.479, bid=135.001,
                        ask=137.479 + ask_improvement,
                        trade_bonds=1_000.0, inferred_side="buy",
                    )
                    engine._process_resting_orders(
                        account, clearing, persist=True,
                        received_ts_ns=clearing.market_ts_ms * 1_000_000,
                    )
                    self.assertEqual(order.queue_ahead, 0)
                    self.assertFalse(order.queue_cleared_crossed_book)

                    assessment = MarketAssessment(
                        reference_price=137.479,
                        reference_low=135.001,
                        reference_high=137.480,
                        reference_source="current_midpoint",
                        reference_confidence=0.25,
                        state="stable",
                        state_score=0,
                        state_confidence=0.5,
                        recent_buy_bonds=1_000.0,
                        recent_sell_bonds=0.0,
                        midpoint_change=0.0,
                        short_ask_change=0.0,
                        largest_ask_gap=0.02,
                        downside_book_vacuum=False,
                        fragile_top_bid=False,
                        iron_floor_price=None,
                        iron_floor_bonds=0.0,
                        evidence=("同价主动买入刚好清空卖单前队",),
                    )
                    valid_context = MakerDecisionContext(
                        reference_price=136.800,
                        reference_source="current_midpoint",
                        reliable_anchor=False,
                        spread=137.479 + ask_improvement - 135.001,
                        bid_support_bonds=1_000.0,
                        ask_supply_bonds=5_000.0,
                        wall_threshold_bonds=5_000.0,
                    )
                    with patch.object(
                        engine, "_decision_context",
                        return_value=valid_context,
                    ):
                        engine._refresh_orders(
                            account, clearing, assessment, persist=True,
                        )
                    retained_after_clear = (
                        account.sell_orders.get(base.db_id) is order
                    )
                    if not retained_after_clear:
                        return False, False, account.inventory, account.fills

                    if not restore_context:
                        next_frame = self._replay_tick(
                            moment + timedelta(seconds=3), last=137.479,
                            bid=135.001, ask=137.479 + ask_improvement,
                        )
                        with patch.object(
                            engine, "_decision_context",
                            return_value=valid_context,
                        ):
                            engine._refresh_orders(
                                account, next_frame, assessment, persist=True,
                            )
                        return (
                            True,
                            account.sell_orders.get(base.db_id) is order,
                            account.inventory,
                            account.fills,
                        )

                    restored = self._replay_tick(
                        moment + timedelta(seconds=3), last=137.479,
                        bid=135.001, ask=137.479,
                    )
                    with patch.object(
                        engine, "_decision_context",
                        return_value=valid_context,
                    ):
                        engine._refresh_orders(
                            account, restored, assessment, persist=True,
                        )
                    self.assertIs(account.sell_orders.get(base.db_id), order)

                    later_buy = self._replay_tick(
                        moment + timedelta(seconds=32), last=137.479,
                        bid=135.001, ask=137.480,
                        trade_bonds=1_000.0, inferred_side="buy",
                    )
                    engine._process_resting_orders(
                        account, later_buy, persist=True,
                        received_ts_ns=later_buy.market_ts_ms * 1_000_000,
                    )
                    return (
                        retained_after_clear,
                        account.sell_orders.get(base.db_id) is order,
                        account.inventory,
                        account.fills,
                    )
                finally:
                    store.close()

            self.assertEqual(
                run_case(QUEUE_POLICY_V14_CANDIDATE, restore_context=True),
                (False, False, 1_000.0, 0),
            )
            self.assertEqual(
                run_case(QUEUE_POLICY_V15_CANDIDATE, restore_context=True),
                (True, False, 0.0, 1),
            )
            self.assertEqual(
                run_case(QUEUE_POLICY_V15_CANDIDATE, restore_context=False),
                (True, False, 1_000.0, 0),
            )
            self.assertEqual(
                run_case(
                    QUEUE_POLICY_V15_CANDIDATE,
                    restore_context=True,
                    ask_improvement=0.002,
                ),
                (False, False, 1_000.0, 0),
            )
            self.assertEqual(
                run_case(
                    QUEUE_POLICY_V15_CANDIDATE,
                    restore_context=True,
                    extra_inventory=True,
                ),
                (False, False, 1_000.0, 0),
            )
            self.assertEqual(
                QUEUE_POLICY_V14_CANDIDATE
                    .queue_cleared_sell_reprice_grace_seconds,
                0,
            )
            self.assertEqual(
                PRIORITY_POLICY_V119_CANDIDATE
                    .queue_cleared_sell_reprice_grace_seconds,
                0,
            )

    def test_queue_v16_retains_a_cleared_profitable_extra_exit_for_one_tick(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)

            def run_case(
                policy, *, market_state: str = "possible_rise",
                continuation_seconds: int = 21,
                fill_after_restore: bool = False,
                entry_price: float = 136.721,
                ask_improvement: float = 0.001,
                exact_buffer_after_clear: float = 0.0,
            ) -> tuple[bool, bool, float, int]:
                config = test_config(
                    temp_path
                    / f"queue-cleared-extra-{policy.model_version}-{market_state}.sqlite3"
                )
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, queue_policy=policy,
                    )
                    moment = datetime(
                        2026, 8, 13, 10, 53, 6, tzinfo=SHANGHAI,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_queue"]
                    extra = next(iter(account.lots.values()))
                    extra.kind = "deep_discount_sweep"
                    extra.entry_price = entry_price
                    quote = self._replay_tick(
                        moment - timedelta(seconds=87), last=137.042,
                        bid=136.851, ask=137.042,
                    )
                    order = engine._new_order(
                        account, quote, side="sell", kind="inventory_exit",
                        lot_id=extra.db_id, price=137.042, quantity=1_000.0,
                        queue_ahead=1_000.0, target_price=137.042,
                        persist=True,
                    )
                    account.sell_orders[extra.db_id] = order
                    clearing = self._replay_tick(
                        moment, last=137.042, bid=136.851,
                        ask=137.042 + ask_improvement,
                        trade_bonds=1_000.0, inferred_side="buy",
                    )
                    engine._process_resting_orders(
                        account, clearing, persist=True,
                        received_ts_ns=clearing.market_ts_ms * 1_000_000,
                    )
                    self.assertEqual(order.queue_ahead, 0)
                    order.exact_fill_uncertainty_buffer = (
                        exact_buffer_after_clear
                    )
                    retained = (
                        engine._retain_queue_cleared_sell_on_worse_reprice(
                            account, extra, order, clearing,
                            desired_price=137.042 + ask_improvement,
                            desired_kind="inventory_exit",
                            desired_quantity=1_000.0,
                            market_state=market_state,
                        )
                    )
                    later = self._replay_tick(
                        moment + timedelta(seconds=continuation_seconds),
                        last=137.042, bid=136.851,
                        ask=137.042 + ask_improvement,
                    )
                    continued = (
                        engine._retain_queue_cleared_sell_on_worse_reprice(
                            account, extra, order, later,
                            desired_price=137.042 + ask_improvement,
                            desired_kind="inventory_exit",
                            desired_quantity=1_000.0,
                            market_state=market_state,
                        )
                    )
                    if fill_after_restore and retained:
                        later_buy = self._replay_tick(
                            moment + timedelta(seconds=36),
                            last=137.042, bid=136.851, ask=137.043,
                            trade_bonds=1_000.0, inferred_side="buy",
                        )
                        engine._process_resting_orders(
                            account, later_buy, persist=True,
                            received_ts_ns=(
                                later_buy.market_ts_ms * 1_000_000
                            ),
                        )
                    return retained, continued, account.inventory, account.fills
                finally:
                    store.close()

            self.assertEqual(
                run_case(QUEUE_POLICY_V15_CANDIDATE),
                (False, False, 1_000.0, 0),
            )
            self.assertEqual(
                run_case(
                    QUEUE_POLICY_V16_CANDIDATE, fill_after_restore=True,
                ),
                (True, True, 0.0, 1),
            )
            self.assertEqual(
                run_case(
                    QUEUE_POLICY_V16_CANDIDATE, market_state="rising",
                ),
                (False, False, 1_000.0, 0),
            )
            self.assertEqual(
                run_case(
                    QUEUE_POLICY_V16_CANDIDATE,
                    entry_price=136.900,
                ),
                (False, False, 1_000.0, 0),
            )
            self.assertEqual(
                run_case(
                    QUEUE_POLICY_V16_CANDIDATE,
                    ask_improvement=0.002,
                ),
                (False, False, 1_000.0, 0),
            )
            self.assertEqual(
                run_case(
                    QUEUE_POLICY_V16_CANDIDATE,
                    exact_buffer_after_clear=1_000.0,
                ),
                (False, False, 1_000.0, 0),
            )
            self.assertEqual(
                run_case(
                    QUEUE_POLICY_V16_CANDIDATE, continuation_seconds=33,
                ),
                (True, False, 1_000.0, 0),
            )
            self.assertEqual(
                QUEUE_POLICY_V15_CANDIDATE
                    .queue_cleared_extra_sell_reprice_grace_seconds,
                0,
            )
            self.assertEqual(
                PRIORITY_POLICY_V119_CANDIDATE
                    .queue_cleared_extra_sell_reprice_grace_seconds,
                0,
            )

    def test_queue_v17_active_turn_replenishment_needs_a_new_exit_edge(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-queue-v17-turn-edge.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, queue_policy=QUEUE_POLICY_V17_CANDIDATE,
                )
                moment = datetime(2026, 8, 13, 10, 7, 45, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                engine.observed_market_trade = True
                account = engine.accounts["maker_v01_queue"]

                extra_entry_tick = self._replay_tick(
                    moment - timedelta(minutes=1), last=136.000,
                    bid=135.999, ask=136.000,
                )
                extra_entry = engine._new_order(
                    account, extra_entry_tick, side="buy",
                    kind="deep_discount_sweep", lot_id=None,
                    price=136.000, quantity=1_000.0, queue_ahead=0.0,
                    target_price=None, persist=True,
                )
                engine._fill_buy(
                    account, extra_entry_tick, extra_entry, 1_000.0,
                    extra_entry_tick.market_ts_ms * 1_000_000,
                    kind="deep_discount_sweep", target_price=None,
                    persist=True, reason="active_deep_discount",
                )
                extra_lot = next(
                    lot for lot in account.lots.values()
                    if lot.entry_price is not None
                )
                high_tick = self._replay_tick(
                    moment - timedelta(seconds=3), last=136.500,
                    bid=136.199, ask=136.500,
                )
                high_order = engine._new_order(
                    account, high_tick, side="sell", kind="inventory_exit",
                    lot_id=extra_lot.db_id, price=136.500,
                    quantity=1_000.0, queue_ahead=0.0,
                    target_price=136.500, persist=True,
                )
                engine._fill_sell(
                    account, high_tick, high_order, 1_000.0,
                    high_tick.market_ts_ms * 1_000_000, persist=True,
                )
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(account.pending_inventory_turn_quantity, 1_000.0)

                low_tick = self._replay_tick(
                    moment, last=136.199, bid=136.100, ask=136.199,
                    ask_bonds=1_000.0,
                )
                assessment = MarketAssessment(
                    reference_price=137.000,
                    reference_low=136.100,
                    reference_high=137.000,
                    reference_source="intraday_trade_anchor",
                    reference_confidence=0.70,
                    state="possible_fall",
                    state_score=-1,
                    state_confidence=0.62,
                    recent_buy_bonds=0.0,
                    recent_sell_bonds=1_000.0,
                    midpoint_change=-0.05,
                    short_ask_change=-0.10,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("库存中性高腿后的低价主动回补",),
                )
                low_context = MakerDecisionContext(
                    reference_price=137.000,
                    reference_source="intraday_trade_anchor",
                    reliable_anchor=True,
                    spread=0.099,
                    bid_support_bonds=5_000.0,
                    ask_supply_bonds=1_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                with patch.object(
                    engine, "_decision_context", return_value=low_context,
                ):
                    engine._active_discount_entry(
                        account, low_tick, assessment, persist=True,
                    )

                replenished = next(
                    lot for lot in account.lots.values()
                    if lot.entry_price is not None
                )
                self.assertEqual(replenished.kind, "inventory_turn_replenish")
                self.assertEqual(replenished.entry_price, 136.199)
                self.assertEqual(account.pending_inventory_turn_quantity, 0.0)

                same_price_tick = self._replay_tick(
                    moment + timedelta(seconds=3), last=136.199,
                    bid=136.000, ask=136.199,
                )
                same_price_context = replace(
                    low_context,
                    reference_price=135.800,
                    spread=0.199,
                )
                with (
                    patch.object(
                        engine, "_decision_context",
                        return_value=same_price_context,
                    ),
                    patch.object(
                        engine, "_recent_lower_sell_bonds",
                        return_value=1_000.0,
                    ),
                ):
                    engine._refresh_orders(
                        account, same_price_tick, assessment, persist=True,
                    )
                self.assertNotIn(replenished.db_id, account.sell_orders)

                higher_tick = self._replay_tick(
                    moment + timedelta(seconds=6), last=136.380,
                    bid=136.000, ask=136.380,
                )
                higher_context = replace(
                    same_price_context,
                    spread=0.380,
                )
                with (
                    patch.object(
                        engine, "_decision_context", return_value=higher_context,
                    ),
                    patch.object(
                        engine, "_recent_lower_sell_bonds",
                        return_value=1_000.0,
                    ),
                ):
                    engine._refresh_orders(
                        account, higher_tick,
                        replace(assessment, state="stable", state_score=0),
                        persist=True,
                    )
                self.assertEqual(
                    account.sell_orders[replenished.db_id].limit_price,
                    136.380,
                )
                self.assertEqual(
                    account.sell_orders[replenished.db_id]
                        .exact_fill_uncertainty_buffer,
                    1_000.0,
                )
            finally:
                store.close()

    def test_queue_v17_buffers_exact_price_inventory_turn_replenishment(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-queue-v17-buy-buffer.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, queue_policy=QUEUE_POLICY_V17_CANDIDATE,
                )
                moment = datetime(2026, 8, 13, 10, 42, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_queue"]
                tick = self._replay_tick(
                    moment, last=136.702, bid=136.702, ask=137.043,
                    bid_bonds=1_000.0,
                )
                engine._replace_buy(
                    account, tick, (136.702, 1_000.0, None),
                    "inventory_turn_replenish", persist=True,
                )
                order = account.buy_order
                self.assertIsNotNone(order)
                assert order is not None
                self.assertEqual(order.queue_ahead, 1_000.0)
                self.assertEqual(
                    order.exact_fill_uncertainty_buffer, 1_000.0,
                )
                self.assertEqual(
                    engine._consume_queue(order, 2_000.0, account.fill_mode),
                    0.0,
                )
                self.assertEqual(order.queue_ahead, 0.0)
                self.assertEqual(order.exact_fill_uncertainty_buffer, 0.0)

                penetrated = replace(
                    order,
                    queue_ahead=1_000.0,
                    exact_fill_uncertainty_buffer=1_000.0,
                )
                self.assertEqual(
                    engine._consume_queue(
                        penetrated, 2_000.0, account.fill_mode,
                        price_penetrated=True,
                    ),
                    1_000.0,
                )
                self.assertEqual(
                    penetrated.exact_fill_uncertainty_buffer, 0.0,
                )
            finally:
                store.close()

    def test_queue_v18_reopens_a_full_inventory_turn_only_after_fresh_lower_sells(
        self,
    ) -> None:
        def run_case(policy, *, fresh_lower_sells: bool) -> dict | None:
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(Path(temp) / "maker-queue-v18-fresh-turn.sqlite3")
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, queue_policy=policy,
                    )
                    replenished_at = datetime(
                        2026, 8, 14, 11, 18, 39, tzinfo=SHANGHAI,
                    )
                    engine._start_date(replenished_at.date().isoformat())
                    account = engine.accounts["maker_v01_queue"]
                    refill_tick = self._replay_tick(
                        replenished_at, last=136.521,
                        bid=136.520, ask=136.521,
                    )
                    refill = engine._new_order(
                        account, refill_tick, side="buy",
                        kind="inventory_turn_replenish", lot_id=None,
                        price=136.521, quantity=1_000.0, queue_ahead=0.0,
                        target_price=None, persist=True,
                    )
                    engine._fill_buy(
                        account, refill_tick, refill, 1_000.0,
                        refill_tick.market_ts_ms * 1_000_000,
                        kind="inventory_turn_replenish", target_price=None,
                        persist=True, reason="passive_buy",
                    )
                    replenished_lot = next(
                        lot for lot in account.lots.values()
                        if lot.kind == "inventory_turn_replenish"
                    )
                    quote_time = datetime(
                        2026, 8, 14, 13, 22, 15, tzinfo=SHANGHAI,
                    )
                    if fresh_lower_sells:
                        engine.analyzer.trade_evidence.append(TradeEvidence(
                            market_ts_ms=int(
                                (quote_time - timedelta(seconds=3)).timestamp()
                                * 1_000
                            ),
                            price=135.507,
                            bonds=1_000.0,
                            transactions=1,
                            side="sell",
                        ))
                    quote = self._replay_tick(
                        quote_time, last=135.508,
                        bid=135.506, ask=135.799,
                        bid_bonds=1_620.0, ask_bonds=1_000.0,
                    )
                    assessment = MarketAssessment(
                        reference_price=135.645,
                        reference_low=135.506,
                        reference_high=135.799,
                        reference_source="persistent_inside_market",
                        reference_confidence=0.55,
                        state="possible_fall",
                        state_score=-1,
                        state_confidence=0.74,
                        recent_buy_bonds=7_000.0,
                        recent_sell_bonds=11_380.0,
                        midpoint_change=0.008,
                        short_ask_change=-0.001,
                        largest_ask_gap=1.199,
                        downside_book_vacuum=False,
                        fragile_top_bid=False,
                        iron_floor_price=135.051,
                        iron_floor_bonds=158_000.0,
                        evidence=("补仓后形成新的低侧主动卖出走廊",),
                    )
                    context = MakerDecisionContext(
                        reference_price=136.922,
                        reference_source="previous_close",
                        reliable_anchor=False,
                        spread=0.293,
                        bid_support_bonds=4_620.0,
                        ask_supply_bonds=2_000.0,
                        wall_threshold_bonds=5_000.0,
                    )
                    with patch.object(
                        engine, "_decision_context", return_value=context,
                    ):
                        engine._refresh_orders(
                            account, quote, assessment, persist=True,
                        )
                    order = account.sell_orders.get(replenished_lot.db_id)
                    if order is None:
                        return None
                    return {
                        "price": order.limit_price,
                        "queue": order.queue_ahead,
                        "buffer": order.exact_fill_uncertainty_buffer,
                        "neutral": order.inventory_neutral_downtrend_turn,
                        "inventory": account.inventory,
                    }
                finally:
                    store.close()

        self.assertIsNone(run_case(
            QUEUE_POLICY_V17_CANDIDATE, fresh_lower_sells=True,
        ))
        self.assertIsNone(run_case(
            QUEUE_POLICY_V18_CANDIDATE, fresh_lower_sells=False,
        ))
        self.assertEqual(
            run_case(QUEUE_POLICY_V18_CANDIDATE, fresh_lower_sells=True),
            {
                "price": 135.799,
                "queue": 1_000.0,
                "buffer": 1_000.0,
                "neutral": True,
                "inventory": 2_000.0,
            },
        )

    def test_queue_v18_1322_buy_only_consumes_the_real_queue_ahead(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-queue-v18-1322.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, queue_policy=QUEUE_POLICY_V18_CANDIDATE,
                )
                replenished_at = datetime(
                    2026, 8, 14, 11, 18, 39, tzinfo=SHANGHAI,
                )
                engine._start_date(replenished_at.date().isoformat())
                account = engine.accounts["maker_v01_queue"]
                refill_tick = self._replay_tick(
                    replenished_at, last=136.521,
                    bid=136.520, ask=136.521,
                )
                refill = engine._new_order(
                    account, refill_tick, side="buy",
                    kind="inventory_turn_replenish", lot_id=None,
                    price=136.521, quantity=1_000.0, queue_ahead=0.0,
                    target_price=None, persist=True,
                )
                engine._fill_buy(
                    account, refill_tick, refill, 1_000.0,
                    refill_tick.market_ts_ms * 1_000_000,
                    kind="inventory_turn_replenish", target_price=None,
                    persist=True, reason="passive_buy",
                )
                replenished_lot = next(
                    lot for lot in account.lots.values()
                    if lot.kind == "inventory_turn_replenish"
                )
                quote_time = datetime(
                    2026, 8, 14, 13, 22, 15, tzinfo=SHANGHAI,
                )
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    market_ts_ms=int(
                        (quote_time - timedelta(seconds=3)).timestamp() * 1_000
                    ),
                    price=135.507,
                    bonds=1_000.0,
                    transactions=1,
                    side="sell",
                ))
                quote = self._replay_tick(
                    quote_time, last=135.508,
                    bid=135.506, ask=135.799,
                    bid_bonds=1_620.0, ask_bonds=1_000.0,
                )
                assessment = MarketAssessment(
                    reference_price=135.645,
                    reference_low=135.506,
                    reference_high=135.799,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="possible_fall",
                    state_score=-1,
                    state_confidence=0.74,
                    recent_buy_bonds=7_000.0,
                    recent_sell_bonds=11_380.0,
                    midpoint_change=0.008,
                    short_ask_change=-0.001,
                    largest_ask_gap=1.199,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=135.051,
                    iron_floor_bonds=158_000.0,
                    evidence=("13:22补仓后新的低侧走廊",),
                )
                context = MakerDecisionContext(
                    reference_price=136.922,
                    reference_source="previous_close",
                    reliable_anchor=False,
                    spread=0.293,
                    bid_support_bonds=4_620.0,
                    ask_supply_bonds=2_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, quote, assessment, persist=True,
                    )
                order = account.sell_orders[replenished_lot.db_id]
                self.assertEqual(order.queue_ahead, 1_000.0)
                self.assertEqual(order.exact_fill_uncertainty_buffer, 1_000.0)
                fills_before_trade = account.fills

                trade = self._replay_tick(
                    quote_time + timedelta(seconds=3),
                    last=135.799, bid=135.506, ask=135.800,
                    trade_bonds=1_000.0, inferred_side="buy",
                    bid_bonds=1_620.0, ask_bonds=1_000.0,
                )
                engine._process_resting_orders(
                    account, trade, persist=True,
                    received_ts_ns=trade.market_ts_ms * 1_000_000,
                )

                self.assertEqual(account.fills, fills_before_trade)
                self.assertEqual(account.inventory, 2_000.0)
                self.assertEqual(order.queue_ahead, 0.0)
                self.assertEqual(order.exact_fill_uncertainty_buffer, 1_000.0)
                self.assertEqual(order.remaining, 1_000.0)
            finally:
                store.close()

    def test_queue_v19_clean_exact_clear_releases_inventory_turn_buffer(
        self,
    ) -> None:
        def run_case(policy) -> tuple[float, float, float, int]:
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(
                    Path(temp) / "maker-queue-v19-clean-clear.sqlite3"
                )
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, queue_policy=policy,
                    )
                    moment = datetime(
                        2026, 8, 13, 11, 19, 33, tzinfo=SHANGHAI,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_queue"]
                    lot = next(iter(account.lots.values()))
                    lot.kind = "inventory_turn_replenish"
                    lot.entry_price = 136.601
                    order = engine._new_order(
                        account,
                        self._replay_tick(
                            moment, last=136.783, bid=136.671,
                            ask=136.893, ask_bonds=2_000.0,
                        ),
                        side="sell", kind="inventory_exit",
                        lot_id=lot.db_id, price=136.893,
                        quantity=1_000.0, queue_ahead=2_000.0,
                        target_price=136.893, persist=True,
                        exact_fill_uncertainty_buffer=1_000.0,
                    )
                    account.sell_orders[lot.db_id] = order
                    account.last_asks = ((136.893, 5_520.0),)

                    clearing = replace(
                        self._replay_tick(
                            moment + timedelta(seconds=102),
                            last=136.893, bid=136.511, ask=136.893,
                            ask_bonds=3_520.0, trade_bonds=2_000.0,
                            inferred_side="buy",
                        ),
                        asks=((136.893, 3_520.0),),
                    )
                    engine._process_resting_orders(
                        account, clearing, persist=True,
                        received_ts_ns=clearing.market_ts_ms * 1_000_000,
                    )
                    self.assertEqual(order.queue_ahead, 0.0)
                    expected_buffer = (
                        0.0 if policy is QUEUE_POLICY_V19_CANDIDATE
                        else 1_000.0
                    )
                    self.assertEqual(
                        order.exact_fill_uncertainty_buffer,
                        expected_buffer,
                    )
                    self.assertEqual(account.fills, 0)
                    account.last_asks = clearing.asks

                    next_trade = replace(
                        self._replay_tick(
                            moment + timedelta(seconds=153),
                            last=136.893, bid=136.612, ask=136.893,
                            ask_bonds=2_520.0, trade_bonds=1_000.0,
                            inferred_side="buy",
                        ),
                        asks=((136.893, 2_520.0),),
                    )
                    engine._process_resting_orders(
                        account, next_trade, persist=True,
                        received_ts_ns=next_trade.market_ts_ms * 1_000_000,
                    )
                    return (
                        order.queue_ahead,
                        order.exact_fill_uncertainty_buffer,
                        account.inventory,
                        account.fills,
                    )
                finally:
                    store.close()

        self.assertEqual(
            run_case(QUEUE_POLICY_V18_CANDIDATE),
            (0.0, 0.0, 1_000.0, 0),
        )
        self.assertEqual(
            run_case(QUEUE_POLICY_V19_CANDIDATE),
            (0.0, 0.0, 0.0, 1),
        )

    def test_queue_v19_keeps_buffer_for_mixed_price_buy_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(
                Path(temp) / "maker-queue-v19-mixed-buy.sqlite3"
            )
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, queue_policy=QUEUE_POLICY_V19_CANDIDATE,
                )
                moment = datetime(2026, 8, 13, 10, 42, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_queue"]
                order = engine._new_order(
                    account,
                    self._replay_tick(
                        moment, last=137.048, bid=136.702, ask=137.048,
                        bid_bonds=2_000.0,
                    ),
                    side="buy", kind="inventory_turn_replenish",
                    lot_id=None, price=136.702, quantity=1_000.0,
                    queue_ahead=1_000.0, target_price=None, persist=True,
                    exact_fill_uncertainty_buffer=1_000.0,
                )
                account.buy_order = order
                account.last_bids = ((136.702, 2_000.0),)
                mixed_frame = replace(
                    self._replay_tick(
                        moment + timedelta(seconds=102),
                        last=136.702, bid=136.702, ask=137.048,
                        bid_bonds=1_000.0, trade_bonds=2_000.0,
                        inferred_side="sell",
                    ),
                    bids=((136.702, 1_000.0),),
                )
                engine._process_resting_orders(
                    account, mixed_frame, persist=True,
                    received_ts_ns=mixed_frame.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.fills, 0)
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(order.queue_ahead, 0.0)
                self.assertEqual(order.exact_fill_uncertainty_buffer, 0.0)
                self.assertEqual(order.remaining, 1_000.0)
            finally:
                store.close()

    def test_queue_v19_keeps_buffer_for_cross_level_sell_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(
                Path(temp) / "maker-queue-v19-cross-level-sell.sqlite3"
            )
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, queue_policy=QUEUE_POLICY_V19_CANDIDATE,
                )
                moment = datetime(2026, 8, 13, 14, 37, 15, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_queue"]
                lot = next(iter(account.lots.values()))
                lot.kind = "inventory_turn_replenish"
                lot.entry_price = 136.708
                order = engine._new_order(
                    account,
                    self._replay_tick(
                        moment, last=136.708, bid=136.612, ask=136.999,
                        ask_bonds=700.0,
                    ),
                    side="sell", kind="inventory_exit", lot_id=lot.db_id,
                    price=137.000, quantity=1_000.0,
                    queue_ahead=1_000.0, target_price=137.000,
                    persist=True, exact_fill_uncertainty_buffer=1_000.0,
                )
                account.sell_orders[lot.db_id] = order
                account.last_asks = (
                    (136.999, 700.0), (137.000, 1_000.0),
                )

                first_frame = replace(
                    self._replay_tick(
                        moment + timedelta(seconds=63),
                        last=137.000, bid=136.803, ask=137.000,
                        ask_bonds=700.0, trade_bonds=1_000.0,
                        inferred_side="buy",
                    ),
                    asks=((137.000, 700.0), (137.297, 1_000.0)),
                )
                engine._process_resting_orders(
                    account, first_frame, persist=True,
                    received_ts_ns=first_frame.market_ts_ms * 1_000_000,
                )
                self.assertEqual(order.queue_ahead, 0.0)
                self.assertEqual(order.exact_fill_uncertainty_buffer, 1_000.0)
                account.last_asks = first_frame.asks

                second_frame = replace(
                    self._replay_tick(
                        moment + timedelta(seconds=78),
                        last=137.000, bid=137.011, ask=137.297,
                        ask_bonds=1_000.0, trade_bonds=700.0,
                        inferred_side="buy",
                    ),
                    asks=((137.297, 1_000.0),),
                )
                engine._process_resting_orders(
                    account, second_frame, persist=True,
                    received_ts_ns=second_frame.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.fills, 0)
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(order.exact_fill_uncertainty_buffer, 300.0)
                self.assertEqual(order.remaining, 1_000.0)
            finally:
                store.close()

    def test_queue_v19_keeps_buffer_for_multi_transaction_exact_frame(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(
                Path(temp) / "maker-queue-v19-multi-transaction.sqlite3"
            )
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, queue_policy=QUEUE_POLICY_V19_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 13, 33, 51, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_queue"]
                lot = next(iter(account.lots.values()))
                lot.kind = "inventory_turn_replenish"
                lot.entry_price = 135.701
                order = engine._new_order(
                    account,
                    self._replay_tick(
                        moment, last=135.661, bid=135.701, ask=135.999,
                    ),
                    side="sell", kind="inventory_exit", lot_id=lot.db_id,
                    price=135.999, quantity=1_000.0,
                    queue_ahead=1_000.0, target_price=135.999,
                    persist=True, exact_fill_uncertainty_buffer=1_000.0,
                )
                account.sell_orders[lot.db_id] = order
                account.last_asks = ((135.999, 4_000.0),)
                mixed_same_price = replace(
                    self._replay_tick(
                        moment + timedelta(seconds=54),
                        last=135.999, bid=135.701, ask=135.999,
                        ask_bonds=2_000.0, trade_bonds=2_000.0,
                        inferred_side="buy",
                    ),
                    asks=((135.999, 2_000.0),),
                    transaction_delta=2,
                )
                engine._process_resting_orders(
                    account, mixed_same_price, persist=True,
                    received_ts_ns=(
                        mixed_same_price.market_ts_ms * 1_000_000
                    ),
                )
                self.assertEqual(account.fills, 0)
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(order.queue_ahead, 0.0)
                self.assertEqual(order.exact_fill_uncertainty_buffer, 0.0)
                self.assertEqual(order.remaining, 1_000.0)
            finally:
                store.close()

    def test_queue_v110_retains_a_cleared_inventory_turn_high_leg(
        self,
    ) -> None:
        def run_case(policy, *, decline_state: str = "falling") -> tuple:
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(
                    Path(temp) / "maker-queue-v110-retain-turn.sqlite3"
                )
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, queue_policy=policy,
                    )
                    moment = datetime(
                        2026, 8, 14, 13, 33, 51, tzinfo=SHANGHAI,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_queue"]
                    lot_id = store.insert_maker_lot({
                        "run_id": store.run_id,
                        "market_date": account.market_date,
                        "strategy_id": account.strategy_id,
                        "kind": "inventory_turn_replenish",
                        "opened_market_ts_ms": int(moment.timestamp() * 1_000),
                        "entry_price": 136.521,
                        "original_quantity": 1_000.0,
                        "remaining_quantity": 1_000.0,
                        "target_price": None,
                        "status": "open",
                        "updated_market_ts_ms": int(moment.timestamp() * 1_000),
                    })
                    account.lots[lot_id] = MakerLot(
                        lot_id, "inventory_turn_replenish",
                        int(moment.timestamp() * 1_000), 136.521,
                        1_000.0, 1_000.0,
                    )
                    account.inventory += 1_000.0
                    order = engine._new_order(
                        account,
                        self._replay_tick(
                            moment, last=135.661, bid=135.701,
                            ask=135.999, ask_bonds=1_000.0,
                        ),
                        side="sell", kind="inventory_exit", lot_id=lot_id,
                        price=135.999, quantity=1_000.0,
                        queue_ahead=1_000.0, target_price=135.999,
                        persist=True,
                        exact_fill_uncertainty_buffer=1_000.0,
                    )
                    order.inventory_neutral_downtrend_turn = True
                    account.sell_orders[lot_id] = order
                    account.last_asks = ((135.999, 4_000.0),)

                    mixed_frame = replace(
                        self._replay_tick(
                            moment + timedelta(seconds=54),
                            last=135.999, bid=135.701, ask=135.999,
                            ask_bonds=2_000.0, trade_bonds=2_000.0,
                            inferred_side="buy",
                        ),
                        asks=((135.999, 2_000.0),),
                        transaction_delta=2,
                    )
                    engine._process_resting_orders(
                        account, mixed_frame, persist=True,
                        received_ts_ns=(
                            mixed_frame.market_ts_ms * 1_000_000
                        ),
                    )
                    self.assertEqual(order.queue_ahead, 0.0)
                    self.assertEqual(order.exact_fill_uncertainty_buffer, 0.0)
                    engine.analyzer.trade_evidence.append(TradeEvidence(
                        market_ts_ms=(
                            moment + timedelta(seconds=63)
                        ).timestamp() * 1_000,
                        price=135.700, bonds=2_000.0,
                        transactions=2, side="sell",
                    ))

                    decline = self._replay_tick(
                        moment + timedelta(seconds=75),
                        last=135.700, bid=135.710, ask=135.800,
                    )
                    decline_assessment = MarketAssessment(
                        reference_price=135.850,
                        reference_low=135.710,
                        reference_high=135.850,
                        reference_source="persistent_inside_market",
                        reference_confidence=0.55,
                        state=decline_state,
                        state_score=-3 if decline_state == "falling" else 3,
                        state_confidence=0.95,
                        recent_buy_bonds=2_000.0,
                        recent_sell_bonds=9_000.0,
                        midpoint_change=-0.095,
                        short_ask_change=-0.199,
                        largest_ask_gap=0.199,
                        downside_book_vacuum=False,
                        fragile_top_bid=False,
                        iron_floor_price=135.050,
                        iron_floor_bonds=74_000.0,
                        evidence=("高腿清队后的低侧成交走廊",),
                    )
                    narrow_context = MakerDecisionContext(
                        reference_price=136.922,
                        reference_source="previous_close",
                        reliable_anchor=False,
                        spread=0.090,
                        bid_support_bonds=9_000.0,
                        ask_supply_bonds=4_000.0,
                        wall_threshold_bonds=5_000.0,
                    )
                    with patch.object(
                        engine, "_decision_context",
                        return_value=narrow_context,
                    ):
                        engine._refresh_orders(
                            account, decline, decline_assessment,
                            persist=True,
                        )
                    retained = account.sell_orders.get(lot_id)
                    if retained is None:
                        return False, account.fills, account.inventory

                    rebound = self._replay_tick(
                        moment + timedelta(seconds=192),
                        last=135.999, bid=135.801, ask=135.999,
                        bid_bonds=1_000.0, ask_bonds=2_000.0,
                        trade_bonds=2_000.0, inferred_side="buy",
                    )
                    engine._process_resting_orders(
                        account, rebound, persist=True,
                        received_ts_ns=rebound.market_ts_ms * 1_000_000,
                    )
                    return (
                        retained.retained_after_queue_cleared_inventory_turn,
                        account.fills,
                        account.inventory,
                    )
                finally:
                    store.close()

        self.assertEqual(
            run_case(QUEUE_POLICY_V19_CANDIDATE),
            (False, 0, 2_000.0),
        )
        self.assertEqual(
            run_case(QUEUE_POLICY_V110_CANDIDATE),
            (True, 1, 1_000.0),
        )
        self.assertEqual(
            run_case(QUEUE_POLICY_V110_CANDIDATE, decline_state="rising"),
            (False, 0, 2_000.0),
        )

    def test_queue_v111_keeps_a_cleared_high_leg_above_lower_reprices(
        self,
    ) -> None:
        def run_case(
            policy, *, final_state: str = "possible_rise",
            breakout: bool = False,
        ) -> tuple[bool, int, float]:
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(
                    Path(temp) / "maker-queue-v111-live-corridor.sqlite3"
                )
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, queue_policy=policy,
                    )
                    moment = datetime(
                        2026, 8, 14, 13, 22, 15, tzinfo=SHANGHAI,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_queue"]
                    lot_id = store.insert_maker_lot({
                        "run_id": store.run_id,
                        "market_date": account.market_date,
                        "strategy_id": account.strategy_id,
                        "kind": "inventory_turn_replenish",
                        "opened_market_ts_ms": int(moment.timestamp() * 1_000),
                        "entry_price": 136.521,
                        "original_quantity": 1_000.0,
                        "remaining_quantity": 1_000.0,
                        "target_price": None,
                        "status": "open",
                        "updated_market_ts_ms": int(moment.timestamp() * 1_000),
                    })
                    account.lots[lot_id] = MakerLot(
                        lot_id, "inventory_turn_replenish",
                        int(moment.timestamp() * 1_000), 136.521,
                        1_000.0, 1_000.0,
                    )
                    account.inventory += 1_000.0
                    seed = self._replay_tick(
                        moment, last=135.508, bid=135.506, ask=135.799,
                    )
                    order = engine._new_order(
                        account, seed, side="sell", kind="inventory_exit",
                        lot_id=lot_id, price=135.799, quantity=1_000.0,
                        queue_ahead=0.0, target_price=135.799,
                        persist=True, exact_fill_uncertainty_buffer=0.0,
                    )
                    order.inventory_neutral_downtrend_turn = True
                    order.queue_cleared_ms = (
                        moment + timedelta(seconds=3)
                    ).timestamp() * 1_000
                    account.sell_orders[lot_id] = order
                    engine.analyzer.trade_evidence.append(TradeEvidence(
                        market_ts_ms=int(
                            (moment + timedelta(seconds=105)).timestamp()
                            * 1_000
                        ),
                        price=135.307, bonds=2_000.0,
                        transactions=2, side="sell",
                    ))

                    decline = self._replay_tick(
                        moment + timedelta(seconds=111),
                        last=135.307, bid=135.306, ask=135.599,
                    )
                    decline_assessment = MarketAssessment(
                        reference_price=135.450,
                        reference_low=135.306,
                        reference_high=135.599,
                        reference_source="persistent_inside_market",
                        reference_confidence=0.55,
                        state="falling", state_score=-4,
                        state_confidence=0.95,
                        recent_buy_bonds=1_000.0,
                        recent_sell_bonds=9_000.0,
                        midpoint_change=-0.10,
                        short_ask_change=-0.20,
                        largest_ask_gap=0.30,
                        downside_book_vacuum=False,
                        fragile_top_bid=False,
                        iron_floor_price=135.101,
                        iron_floor_bonds=8_000.0,
                        evidence=("清队高腿下方继续出现卖出",),
                    )
                    context = MakerDecisionContext(
                        reference_price=136.922,
                        reference_source="previous_close",
                        reliable_anchor=False,
                        spread=0.293,
                        bid_support_bonds=8_000.0,
                        ask_supply_bonds=2_000.0,
                        wall_threshold_bonds=5_000.0,
                        breakout_support_price=135.500 if breakout else 0.0,
                        breakout_lower_sell_bonds=0.0,
                    )
                    with patch.object(
                        engine, "_decision_context", return_value=context,
                    ):
                        engine._refresh_orders(
                            account, decline, decline_assessment,
                            persist=True,
                        )
                    original_retained = (
                        account.sell_orders.get(lot_id) is order
                    )

                    recovery = moment + timedelta(seconds=339)
                    engine.analyzer.trade_evidence.append(TradeEvidence(
                        market_ts_ms=int(
                            (recovery - timedelta(seconds=60)).timestamp()
                            * 1_000
                        ),
                        price=135.320, bonds=1_000.0,
                        transactions=1, side="sell",
                    ))
                    recovery_tick = self._replay_tick(
                        recovery, last=135.600,
                        bid=135.600, ask=135.800,
                    )
                    recovery_assessment = replace(
                        decline_assessment,
                        state=final_state,
                        state_score=2 if final_state == "possible_rise" else 3,
                        recent_buy_bonds=5_000.0,
                        recent_sell_bonds=5_000.0,
                    )
                    with patch.object(
                        engine, "_decision_context", return_value=context,
                    ):
                        engine._refresh_orders(
                            account, recovery_tick, recovery_assessment,
                            persist=True,
                        )
                    through = self._replay_tick(
                        recovery + timedelta(seconds=3),
                        last=135.800, bid=135.601, ask=136.990,
                        trade_bonds=1_000.0, inferred_side="buy",
                    )
                    engine._process_resting_orders(
                        account, through, persist=True,
                        received_ts_ns=through.market_ts_ms * 1_000_000,
                    )
                    return original_retained, account.fills, account.inventory
                finally:
                    store.close()

        self.assertEqual(
            run_case(QUEUE_POLICY_V110_CANDIDATE),
            (False, 0, 2_000.0),
        )
        self.assertEqual(
            run_case(QUEUE_POLICY_V111_CANDIDATE),
            (True, 1, 1_000.0),
        )
        self.assertEqual(
            run_case(QUEUE_POLICY_V111_CANDIDATE, final_state="rising"),
            (True, 0, 2_000.0),
        )
        self.assertEqual(
            run_case(QUEUE_POLICY_V111_CANDIDATE, breakout=True),
            (False, 0, 2_000.0),
        )

    def test_queue_v113_keeps_an_uncleared_high_leg_through_stable(
        self,
    ) -> None:
        def run_case(
            policy, *, state: str = "stable", bid: float = 136.097,
            ask: float = 136.317, include_lower_sell: bool = True,
        ) -> tuple[bool, float, float, int]:
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(
                    Path(temp) / f"queue-v113-{policy.model_version}.sqlite3"
                )
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, queue_policy=policy,
                    )
                    moment = datetime(
                        2026, 8, 14, 13, 5, 42, tzinfo=SHANGHAI,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_queue"]
                    lot_id = store.insert_maker_lot({
                        "run_id": store.run_id,
                        "market_date": account.market_date,
                        "strategy_id": account.strategy_id,
                        "kind": "inventory_turn_replenish",
                        "opened_market_ts_ms": int(
                            (moment - timedelta(minutes=5)).timestamp() * 1_000
                        ),
                        "entry_price": 136.521,
                        "original_quantity": 1_000.0,
                        "remaining_quantity": 1_000.0,
                        "target_price": None,
                        "status": "open",
                        "updated_market_ts_ms": int(moment.timestamp() * 1_000),
                    })
                    account.lots[lot_id] = MakerLot(
                        lot_id, "inventory_turn_replenish",
                        int((moment - timedelta(minutes=5)).timestamp() * 1_000),
                        136.521, 1_000.0, 1_000.0,
                    )
                    account.inventory += 1_000.0
                    quote = self._replay_tick(
                        moment - timedelta(seconds=99),
                        last=136.053, bid=136.052, ask=136.317,
                        ask_bonds=1_000.0,
                    )
                    order = engine._new_order(
                        account, quote, side="sell", kind="inventory_exit",
                        lot_id=lot_id, price=136.317, quantity=1_000.0,
                        queue_ahead=1_000.0, target_price=136.317,
                        persist=True,
                        exact_fill_uncertainty_buffer=1_000.0,
                    )
                    order.inventory_neutral_downtrend_turn = True
                    account.sell_orders[lot_id] = order
                    if include_lower_sell:
                        engine.analyzer.trade_evidence.append(TradeEvidence(
                            market_ts_ms=int(
                                (moment - timedelta(seconds=98)).timestamp()
                                * 1_000
                            ),
                            price=136.053, bonds=1_000.0,
                            transactions=1, side="sell",
                        ))
                    assessment = MarketAssessment(
                        reference_price=136.207,
                        reference_low=bid,
                        reference_high=ask,
                        reference_source="current_midpoint",
                        reference_confidence=0.35,
                        state=state,
                        state_score=0 if state == "stable" else 2,
                        state_confidence=0.42,
                        recent_buy_bonds=0.0,
                        recent_sell_bonds=(
                            1_000.0 if include_lower_sell else 0.0
                        ),
                        midpoint_change=0.022,
                        short_ask_change=0.0,
                        largest_ask_gap=0.023,
                        downside_book_vacuum=False,
                        fragile_top_bid=False,
                        iron_floor_price=None,
                        iron_floor_bonds=0.0,
                        evidence=("状态标签短暂稳定",),
                    )
                    context = MakerDecisionContext(
                        reference_price=136.922,
                        reference_source="previous_close",
                        reliable_anchor=False,
                        spread=ask - bid,
                        bid_support_bonds=7_000.0,
                        ask_supply_bonds=3_000.0,
                        wall_threshold_bonds=5_000.0,
                    )
                    tick = self._replay_tick(
                        moment, last=136.053, bid=bid, ask=ask,
                        ask_bonds=1_000.0,
                    )
                    with patch.object(
                        engine, "_decision_context", return_value=context,
                    ):
                        engine._refresh_orders(
                            account, tick, assessment, persist=True,
                        )
                    retained = account.sell_orders.get(lot_id) is order
                    return (
                        retained, order.queue_ahead,
                        order.exact_fill_uncertainty_buffer, account.fills,
                    )
                finally:
                    store.close()

        self.assertEqual(
            run_case(QUEUE_POLICY_V112_CANDIDATE),
            (False, 1_000.0, 1_000.0, 0),
        )
        self.assertEqual(
            run_case(QUEUE_POLICY_V113_CANDIDATE),
            (True, 1_000.0, 1_000.0, 0),
        )
        self.assertEqual(
            run_case(QUEUE_POLICY_V113_CANDIDATE, state="possible_rise"),
            (False, 1_000.0, 1_000.0, 0),
        )
        self.assertEqual(
            run_case(QUEUE_POLICY_V113_CANDIDATE, bid=136.140),
            (False, 1_000.0, 1_000.0, 0),
        )
        self.assertEqual(
            run_case(
                QUEUE_POLICY_V113_CANDIDATE,
                ask=136.350,
            ),
            (False, 1_000.0, 1_000.0, 0),
        )
        self.assertEqual(
            run_case(
                QUEUE_POLICY_V113_CANDIDATE,
                include_lower_sell=False,
            ),
            (False, 1_000.0, 1_000.0, 0),
        )

    def test_queue_v114_retains_only_a_clean_cleared_refill_on_one_tick_dip(
        self,
    ) -> None:
        def run_case(
            policy, *, queue_ahead: float = 0.0,
            exact_buffer: float = 0.0, crossed: bool = False,
            desired_price: float = 136.520,
        ) -> tuple[bool, bool, float, str, str | None]:
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(
                    Path(temp) / f"queue-v114-{policy.model_version}.sqlite3"
                )
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, queue_policy=policy,
                    )
                    moment = datetime(
                        2026, 8, 14, 11, 15, 45, tzinfo=SHANGHAI,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_queue"]
                    account.pending_inventory_turn_quantity = 1_000.0
                    account.pending_inventory_turn_sale_value = 136_988.0
                    original_tick = self._replay_tick(
                        moment - timedelta(seconds=36),
                        last=136.521, bid=136.521, ask=136.522,
                        bid_bonds=1_000.0,
                    )
                    original = engine._new_order(
                        account, original_tick, side="buy",
                        kind="inventory_turn_replenish", lot_id=None,
                        price=136.521, quantity=1_000.0,
                        queue_ahead=queue_ahead, target_price=None,
                        persist=True,
                        exact_fill_uncertainty_buffer=exact_buffer,
                    )
                    original.queue_cleared_ms = int(moment.timestamp() * 1_000)
                    original.queue_cleared_crossed_book = crossed
                    account.buy_order = original
                    dip = self._replay_tick(
                        moment, last=136.521, bid=desired_price,
                        ask=136.522, bid_bonds=2_000.0,
                    )
                    candidate_retention = engine._retain_queue_cleared_inventory_turn_buy_on_lower_reprice(
                        account, original, tick=dip,
                        desired_price=desired_price,
                        desired_kind="inventory_turn_replenish",
                        desired_quantity=1_000.0, desired_target=None,
                    )
                    engine._replace_buy(
                        account, dip, (desired_price, 1_000.0, None),
                        "inventory_turn_replenish", persist=True,
                    )
                    assert account.buy_order is not None
                    cancel_row = store.connection.execute(
                        "SELECT cancel_reason FROM maker_paper_orders WHERE id=?",
                        (original.db_id,),
                    ).fetchone()
                    return (
                        candidate_retention,
                        account.buy_order.db_id == original.db_id,
                        account.buy_order.limit_price,
                        account.buy_order.kind,
                        cancel_row["cancel_reason"],
                    )
                finally:
                    store.close()

        self.assertEqual(
            run_case(QUEUE_POLICY_V113_CANDIDATE),
            (False, False, 136.520, "inventory_turn_replenish", "maker_reprice"),
        )
        self.assertEqual(
            run_case(QUEUE_POLICY_V114_CANDIDATE),
            (True, True, 136.521, "inventory_turn_replenish", None),
        )
        for kwargs in (
            {"queue_ahead": 1_000.0},
            {"exact_buffer": 1_000.0},
            {"crossed": True},
            {"desired_price": 136.519},
        ):
            candidate_retention, _, _, _, _ = run_case(
                QUEUE_POLICY_V114_CANDIDATE, **kwargs,
            )
            self.assertFalse(candidate_retention)

    def test_queue_v114_permission_is_queue_branch_local(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "queue-v114-branch-local.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority", "queue"),
                super_windfall_enabled=True,
            ))
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V122_CANDIDATE,
                    queue_policy=QUEUE_POLICY_V114_CANDIDATE,
                    windfall_policy=WINDFALL_POLICY_V11_CANDIDATE,
                )
                engine._start_date("2026-08-14")
                self.assertFalse(
                    engine.accounts["maker_v01_priority"].policy
                        .retain_queue_cleared_inventory_turn_buy_on_lower_reprice
                )
                self.assertTrue(
                    engine.accounts["maker_v01_queue"].policy
                        .retain_queue_cleared_inventory_turn_buy_on_lower_reprice
                )
                self.assertFalse(
                    engine.accounts["maker_v01_super_windfall"].policy
                        .retain_queue_cleared_inventory_turn_buy_on_lower_reprice
                )
            finally:
                store.close()

    def test_queue_v115_keeps_only_an_unfilled_clean_cleared_refill_while_falling(
        self,
    ) -> None:
        def run_case(
            *, state: str = "falling", desired_price: float = 135.330,
            queue_ahead: float = 0.0, exact_buffer: float = 0.0,
            crossed: bool = False, filled_quantity: float = 0.0,
            desired_kind: str = "inventory_turn_replenish",
            desired_quantity: float | None = None,
            desired_target: float | None = None,
        ) -> bool:
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(
                    Path(temp) / "queue-v115-clean-falling.sqlite3"
                )
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, queue_policy=QUEUE_POLICY_V115_CANDIDATE,
                    )
                    moment = datetime(
                        2026, 8, 14, 13, 25, 39, tzinfo=SHANGHAI,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_queue"]
                    account.pending_inventory_turn_quantity = 1_000.0
                    account.pending_inventory_turn_sale_value = 135_500.0
                    original = engine._new_order(
                        account,
                        self._replay_tick(
                            moment - timedelta(seconds=120),
                            last=135.307, bid=135.307, ask=135.500,
                        ),
                        side="buy", kind="inventory_turn_replenish",
                        lot_id=None, price=135.307, quantity=1_000.0,
                        queue_ahead=queue_ahead, target_price=None,
                        persist=True,
                        exact_fill_uncertainty_buffer=exact_buffer,
                    )
                    original.queue_cleared_ms = int(moment.timestamp() * 1_000)
                    original.queue_cleared_crossed_book = crossed
                    original.filled_quantity = filled_quantity
                    account.buy_order = original
                    tick = self._replay_tick(
                        moment, last=135.320, bid=desired_price,
                        ask=135.340, bid_bonds=1_000.0,
                    )
                    quantity = (
                        original.remaining
                        if desired_quantity is None else desired_quantity
                    )
                    return engine._retain_clean_cleared_inventory_turn_buy_while_falling(
                        account, original, tick=tick,
                        desired_price=desired_price,
                        desired_kind=desired_kind,
                        desired_quantity=quantity,
                        desired_target=desired_target,
                        market_state=state,
                    )
                finally:
                    store.close()

        self.assertTrue(run_case())
        # A one-tick lower parent target is still only a transient book move;
        # two ticks means the lower corridor itself has moved and must reprice.
        self.assertTrue(run_case(desired_price=135.306))
        for kwargs in (
            {"state": "stable"},
            {"state": "rising"},
            {"desired_price": 135.305},
            {"queue_ahead": 1_000.0},
            {"exact_buffer": 1_000.0},
            {"crossed": True},
            {"filled_quantity": 100.0},
            {"desired_kind": "inventory_replenish"},
            {"desired_quantity": 900.0},
            {"desired_target": 135.500},
        ):
            self.assertFalse(run_case(**kwargs), kwargs)

    def test_queue_v115_reverts_to_parent_on_context_loss_and_is_branch_local(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "queue-v115-branch-local.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority", "queue"),
                super_windfall_enabled=True,
            ))
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V122_CANDIDATE,
                    queue_policy=QUEUE_POLICY_V115_CANDIDATE,
                    windfall_policy=WINDFALL_POLICY_V11_CANDIDATE,
                )
                moment = datetime(
                    2026, 8, 14, 13, 25, 39, tzinfo=SHANGHAI,
                )
                engine._start_date(moment.date().isoformat())
                queue = engine.accounts["maker_v01_queue"]
                queue.pending_inventory_turn_quantity = 1_000.0
                queue.pending_inventory_turn_sale_value = 135_500.0
                order = engine._new_order(
                    queue,
                    self._replay_tick(
                        moment - timedelta(seconds=120),
                        last=135.307, bid=135.307, ask=135.500,
                    ),
                    side="buy", kind="inventory_turn_replenish", lot_id=None,
                    price=135.307, quantity=1_000.0, queue_ahead=0.0,
                    target_price=None, persist=True,
                    exact_fill_uncertainty_buffer=0.0,
                )
                order.queue_cleared_ms = int(moment.timestamp() * 1_000)
                queue.buy_order = order
                tick = self._replay_tick(
                    moment, last=135.320, bid=135.330, ask=135.340,
                )
                engine._replace_buy(
                    queue, tick, None, "inventory_turn_replenish",
                    market_state="falling", persist=True,
                )
                self.assertIsNone(queue.buy_order)
                self.assertEqual(
                    store.connection.execute(
                        "SELECT cancel_reason FROM maker_paper_orders WHERE id=?",
                        (order.db_id,),
                    ).fetchone()["cancel_reason"],
                    "entry_context_changed",
                )
                self.assertEqual(
                    QUEUE_POLICY_V115_CANDIDATE.parent_model_id,
                    "maker_queue_v1_13_candidate",
                )
                self.assertFalse(
                    engine.accounts["maker_v01_priority"].policy
                        .retain_clean_cleared_inventory_turn_buy_while_falling
                )
                self.assertTrue(
                    queue.policy
                        .retain_clean_cleared_inventory_turn_buy_while_falling
                )
                self.assertFalse(
                    engine.accounts["maker_v01_super_windfall"].policy
                        .retain_clean_cleared_inventory_turn_buy_while_falling
                )
            finally:
                store.close()

    def test_queue_v112_fills_sell_from_same_frame_crossed_residual(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)

            def run_case(policy) -> tuple[float, float, str | None]:
                config = test_config(
                    temp_path / f"queue-crossed-sell-{policy.model_version}.sqlite3"
                )
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, queue_policy=policy,
                    )
                    moment = datetime(
                        2026, 8, 14, 10, 20, 12, tzinfo=SHANGHAI,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_queue"]
                    base = next(iter(account.lots.values()))
                    quote = self._replay_tick(
                        moment - timedelta(seconds=18),
                        last=136.796, bid=136.796, ask=137.197,
                        ask_bonds=2_000.0,
                    )
                    order = engine._new_order(
                        account, quote, side="sell", kind="inventory_exit",
                        lot_id=base.db_id, price=137.197,
                        quantity=1_000.0, queue_ahead=2_000.0,
                        target_price=137.197, persist=True,
                    )
                    account.sell_orders[base.db_id] = order
                    trade = self._replay_tick(
                        moment, last=137.197, bid=137.197, ask=137.198,
                        bid_bonds=3_000.0, trade_bonds=2_000.0,
                        inferred_side="buy",
                    )
                    engine._process_resting_orders(
                        account, trade, persist=True,
                        received_ts_ns=trade.market_ts_ms * 1_000_000,
                    )
                    fill = store.connection.execute(
                        """SELECT quantity,fill_reason FROM maker_paper_fills
                           ORDER BY id DESC LIMIT 1"""
                    ).fetchone()
                    return (
                        account.inventory,
                        0.0 if fill is None else float(fill["quantity"]),
                        None if fill is None else fill["fill_reason"],
                    )
                finally:
                    store.close()

            self.assertEqual(
                run_case(QUEUE_POLICY_V111_CANDIDATE),
                (1_000.0, 0.0, None),
            )
            self.assertEqual(
                run_case(QUEUE_POLICY_V112_CANDIDATE),
                (
                    0.0, 1_000.0,
                    "queue_cleared_crossed_residual_fill",
                ),
            )

    def test_queue_v112_fills_only_displayed_buy_residual(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "queue-crossed-buy.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, queue_policy=QUEUE_POLICY_V112_CANDIDATE,
                )
                moment = datetime(
                    2026, 8, 13, 11, 5, 12, tzinfo=SHANGHAI,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_queue"]
                quote = self._replay_tick(
                    moment - timedelta(seconds=9),
                    last=136.760, bid=136.760, ask=136.987,
                    bid_bonds=240.0,
                )
                order = engine._new_order(
                    account, quote, side="buy",
                    kind="inventory_turn_replenish", lot_id=None,
                    price=136.760, quantity=1_000.0,
                    queue_ahead=240.0, target_price=None, persist=True,
                )
                account.buy_order = order
                trade = self._replay_tick(
                    moment, last=136.760, bid=136.601, ask=136.749,
                    ask_bonds=380.0, trade_bonds=240.0,
                    inferred_side="sell",
                )
                engine._process_resting_orders(
                    account, trade, persist=True,
                    received_ts_ns=trade.market_ts_ms * 1_000_000,
                )

                self.assertEqual(account.inventory, 1_380.0)
                self.assertEqual(order.filled_quantity, 380.0)
                self.assertIs(account.buy_order, order)
                fill = store.connection.execute(
                    """SELECT quantity,fill_reason FROM maker_paper_fills
                       ORDER BY id DESC LIMIT 1"""
                ).fetchone()
                self.assertEqual(float(fill["quantity"]), 380.0)
                self.assertEqual(
                    fill["fill_reason"],
                    "queue_cleared_crossed_residual_fill",
                )
            finally:
                store.close()

    def test_queue_v112_shares_crossed_residual_across_same_price_sells(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "queue-crossed-shared.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, queue_policy=QUEUE_POLICY_V112_CANDIDATE,
                )
                moment = datetime(
                    2026, 8, 13, 11, 28, 24, tzinfo=SHANGHAI,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_queue"]
                base = next(iter(account.lots.values()))
                base.original_quantity = 380.0
                base.remaining_quantity = 380.0
                store.update_maker_lot(
                    base.db_id, remaining_quantity=380.0, status="open",
                    updated_market_ts_ms=0,
                )
                extra_id = store.insert_maker_lot({
                    "run_id": store.run_id,
                    "market_date": account.market_date,
                    "strategy_id": account.strategy_id,
                    "kind": "low_bid_reversion",
                    "opened_market_ts_ms": 0,
                    "entry_price": 136.001,
                    "original_quantity": 620.0,
                    "remaining_quantity": 620.0,
                    "target_price": None,
                    "status": "open",
                    "updated_market_ts_ms": 0,
                })
                account.lots[extra_id] = MakerLot(
                    extra_id, "low_bid_reversion", 0, 136.001,
                    620.0, 620.0,
                )
                quote = self._replay_tick(
                    moment - timedelta(seconds=6),
                    last=136.001, bid=136.001, ask=136.599,
                    ask_bonds=1_280.0,
                )
                for lot in tuple(account.lots.values()):
                    account.sell_orders[lot.db_id] = engine._new_order(
                        account, quote, side="sell", kind="inventory_exit",
                        lot_id=lot.db_id, price=136.599,
                        quantity=lot.remaining_quantity,
                        queue_ahead=1_280.0, target_price=136.599,
                        persist=True,
                    )
                trade = self._replay_tick(
                    moment, last=136.599, bid=136.602, ask=136.900,
                    bid_bonds=720.0, trade_bonds=1_280.0,
                    inferred_side="buy",
                )
                engine._process_resting_orders(
                    account, trade, persist=True,
                    received_ts_ns=trade.market_ts_ms * 1_000_000,
                )

                total = store.connection.execute(
                    """SELECT COALESCE(SUM(quantity),0)
                       FROM maker_paper_fills"""
                ).fetchone()[0]
                self.assertEqual(float(total), 720.0)
                self.assertEqual(account.inventory, 280.0)
            finally:
                store.close()

    def test_queue_v112_requires_newly_cleared_unbuffered_old_order(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "queue-crossed-guards.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, queue_policy=QUEUE_POLICY_V112_CANDIDATE,
                )
                moment = datetime(
                    2026, 8, 14, 13, 7, 57, tzinfo=SHANGHAI,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_queue"]
                base = next(iter(account.lots.values()))
                quote = self._replay_tick(
                    moment - timedelta(seconds=6),
                    last=135.006, bid=135.006, ask=135.618,
                    ask_bonds=760.0,
                )
                order = engine._new_order(
                    account, quote, side="sell", kind="inventory_exit",
                    lot_id=base.db_id, price=135.618,
                    quantity=1_000.0, queue_ahead=760.0,
                    target_price=135.618, persist=True,
                    exact_fill_uncertainty_buffer=1_000.0,
                )
                account.sell_orders[base.db_id] = order
                trade = self._replay_tick(
                    moment, last=135.618, bid=135.629, ask=136.340,
                    bid_bonds=1_240.0, trade_bonds=760.0,
                    inferred_side="buy",
                )
                with patch.object(
                    engine, "_clean_exact_queue_clear", return_value=False,
                ):
                    engine._process_resting_orders(
                        account, trade, persist=True,
                        received_ts_ns=trade.market_ts_ms * 1_000_000,
                    )

                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(order.queue_ahead, 0.0)
                self.assertEqual(
                    order.exact_fill_uncertainty_buffer, 1_000.0,
                )
                self.assertEqual(account.fills, 0)
            finally:
                store.close()

    def test_priority_v12_candidate_can_high_sell_a_downtrend_wide_spread(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v12-high-sell.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=PRIORITY_POLICY_V12_CANDIDATE,
                )
                context = MakerDecisionContext(
                    reference_price=135.645,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=0.293,
                    bid_support_bonds=4_620.0,
                    ask_supply_bonds=2_000.0,
                    wall_threshold_bonds=5_000.0,
                )

                self.assertFalse(engine._base_high_sell_is_safe(
                    135.798, context, PRIORITY_POLICY_V11, "possible_fall",
                    recent_lower_sell_bonds=1_000.0,
                ))
                self.assertTrue(engine._base_high_sell_is_safe(
                    135.798, context, PRIORITY_POLICY_V12_CANDIDATE,
                    "possible_fall", recent_lower_sell_bonds=1_000.0,
                ))
                self.assertFalse(engine._base_high_sell_is_safe(
                    135.798, context, PRIORITY_POLICY_V12_CANDIDATE,
                    "possible_fall", recent_lower_sell_bonds=0.0,
                ))
                self.assertFalse(engine._base_high_sell_is_safe(
                    135.798, context, PRIORITY_POLICY_V12_CANDIDATE, "rising",
                    recent_lower_sell_bonds=1_000.0,
                ))
                marginal_context = replace(context, spread=0.191)
                self.assertFalse(engine._base_high_sell_is_safe(
                    135.798, marginal_context, PRIORITY_POLICY_V12_CANDIDATE,
                    "falling", recent_lower_sell_bonds=1_000.0,
                ))
                self.assertTrue(engine._base_high_sell_is_safe(
                    135.798, marginal_context, PRIORITY_POLICY_V13_CANDIDATE,
                    "falling", recent_lower_sell_bonds=1_000.0,
                ))
                too_narrow = replace(context, spread=0.180)
                self.assertFalse(engine._base_high_sell_is_safe(
                    135.798, too_narrow, PRIORITY_POLICY_V13_CANDIDATE,
                    "falling", recent_lower_sell_bonds=1_000.0,
                ))
                self.assertFalse(engine._base_high_sell_is_safe(
                    135.798, marginal_context, PRIORITY_POLICY_V13_CANDIDATE,
                    "stable", recent_lower_sell_bonds=1_000.0,
                ))
                self.assertFalse(engine._base_high_sell_is_safe(
                    135.798, marginal_context, PRIORITY_POLICY_V13_CANDIDATE,
                    "falling", recent_lower_sell_bonds=0.0,
                    persistent_lower_bid=True,
                ))
                self.assertTrue(engine._base_high_sell_is_safe(
                    135.798, marginal_context, PRIORITY_POLICY_V14_CANDIDATE,
                    "falling", recent_lower_sell_bonds=0.0,
                    persistent_lower_bid=True,
                ))
                self.assertFalse(engine._base_high_sell_is_safe(
                    135.798, marginal_context, PRIORITY_POLICY_V14_CANDIDATE,
                    "rising", recent_lower_sell_bonds=0.0,
                    persistent_lower_bid=True,
                ))
            finally:
                store.close()

    def test_priority_v12_candidate_keeps_confirmed_recovery_for_sixty_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v12-recovery.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(config, store)
                start = datetime(2026, 8, 14, 13, 27, 57, tzinfo=SHANGHAI)
                start_ms = int(start.timestamp() * 1000)
                engine.last_confirmed_rise_trade_ts_ms = start_ms
                engine.last_confirmed_rise_price = 135.800
                tick_30s = self._replay_tick(
                    start + timedelta(seconds=30),
                    last=135.800, bid=135.601, ask=135.900,
                )
                tick_61s = self._replay_tick(
                    start + timedelta(seconds=61),
                    last=135.800, bid=135.601, ask=135.900,
                )

                self.assertFalse(engine._confirmed_rise_is_recent(
                    tick_30s, PRIORITY_POLICY_V11,
                ))
                self.assertTrue(engine._confirmed_rise_is_recent(
                    tick_30s, PRIORITY_POLICY_V12_CANDIDATE,
                ))
                self.assertFalse(engine._confirmed_rise_is_recent(
                    tick_61s, PRIORITY_POLICY_V12_CANDIDATE,
                ))
                retired_bid = self._replay_tick(
                    start + timedelta(seconds=31),
                    last=135.500, bid=135.299, ask=135.500,
                )
                self.assertFalse(engine._confirmed_rise_is_recent(
                    retired_bid, PRIORITY_POLICY_V12_CANDIDATE,
                ))
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    market_ts_ms=start_ms + 31_000,
                    price=135.600,
                    bonds=5_000.0,
                    transactions=5,
                    side="sell",
                ))
                lower_selling = self._replay_tick(
                    start + timedelta(seconds=32),
                    last=135.600, bid=135.601, ask=135.900,
                )
                self.assertFalse(engine._confirmed_rise_is_recent(
                    lower_selling, PRIORITY_POLICY_V12_CANDIDATE,
                ))
            finally:
                store.close()

    def test_priority_v12_candidate_immediately_replenishes_the_downtrend_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v12-replenish.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=PRIORITY_POLICY_V12_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 13, 22, 18, tzinfo=SHANGHAI)
                moment_ms = int(moment.timestamp() * 1000)
                engine._start_date(moment.date().isoformat())
                engine.previous_close_reference = 136.922
                engine.observed_market_trade = True
                account = engine.accounts["maker_v01_priority"]
                account.inventory = 0.0
                account.lots.clear()
                account.replenishment_quantity = 1_000.0
                account.replenishment_sale_value = 135_798.0
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    market_ts_ms=moment_ms - 10_000,
                    price=135.507,
                    bonds=1_000.0,
                    transactions=1,
                    side="sell",
                ))
                tick = self._replay_tick(
                    moment, last=135.799, bid=135.506, ask=135.799,
                    previous_close=136.922,
                )
                assessment = MarketAssessment(
                    reference_price=135.645,
                    reference_low=135.506,
                    reference_high=135.799,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="stable",
                    state_score=0,
                    state_confidence=0.62,
                    recent_buy_bonds=1_000.0,
                    recent_sell_bonds=5_000.0,
                    midpoint_change=-0.10,
                    short_ask_change=-0.10,
                    largest_ask_gap=1.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=135.050,
                    iron_floor_bonds=136_000.0,
                    evidence=("低端主动卖出仍在，先高卖后按下沿回补",),
                )
                context = MakerDecisionContext(
                    reference_price=135.645,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=0.293,
                    bid_support_bonds=4_620.0,
                    ask_supply_bonds=2_000.0,
                    wall_threshold_bonds=5_000.0,
                )

                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )

                self.assertIsNotNone(account.buy_order)
                self.assertEqual(account.buy_order.kind, "inventory_replenish")
                self.assertEqual(account.buy_order.limit_price, 135.507)
            finally:
                store.close()

    def test_persistent_bid_corridor_requires_a_continuous_fifteen_second_run(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v14-bid-corridor.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(config, store)
                moment = datetime(2026, 8, 14, 13, 20, 48, tzinfo=SHANGHAI)
                moment_ms = int(moment.timestamp() * 1000)
                tick = self._replay_tick(
                    moment, last=135.787, bid=135.504, ask=135.787,
                )

                engine.analyzer.book_quotes.extend((
                    BookQuote(moment_ms - 18_000, 135.503, 135.790),
                    BookQuote(moment_ms - 9_000, 135.506, 135.788),
                    BookQuote(moment_ms, 135.504, 135.787),
                ))
                self.assertTrue(engine._persistent_bid_corridor(tick))

                engine.analyzer.book_quotes.clear()
                engine.analyzer.book_quotes.extend((
                    BookQuote(moment_ms - 12_000, 135.503, 135.790),
                    BookQuote(moment_ms, 135.504, 135.787),
                ))
                self.assertFalse(engine._persistent_bid_corridor(tick))

                engine.analyzer.book_quotes.clear()
                engine.analyzer.book_quotes.extend((
                    BookQuote(moment_ms - 18_000, 135.503, 135.790),
                    BookQuote(moment_ms - 6_000, 135.480, 135.788),
                    BookQuote(moment_ms, 135.504, 135.787),
                ))
                self.assertFalse(engine._persistent_bid_corridor(tick))
            finally:
                store.close()

    def test_priority_v14_uses_the_same_bid_corridor_for_replenishment(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v14-replenish.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=PRIORITY_POLICY_V14_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 13, 20, 48, tzinfo=SHANGHAI)
                moment_ms = int(moment.timestamp() * 1000)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.inventory = 0.0
                account.lots.clear()
                account.replenishment_quantity = 1_000.0
                account.replenishment_sale_value = 135_786.0
                engine.analyzer.book_quotes.extend((
                    BookQuote(moment_ms - 18_000, 135.503, 135.790),
                    BookQuote(moment_ms - 9_000, 135.506, 135.788),
                    BookQuote(moment_ms, 135.504, 135.787),
                ))
                tick = self._replay_tick(
                    moment, last=135.787, bid=135.504, ask=135.787,
                    previous_close=136.922,
                )
                assessment = MarketAssessment(
                    reference_price=135.645,
                    reference_low=135.504,
                    reference_high=135.787,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="falling",
                    state_score=-3,
                    state_confidence=0.70,
                    recent_buy_bonds=1_000.0,
                    recent_sell_bonds=3_000.0,
                    midpoint_change=-0.10,
                    short_ask_change=-0.10,
                    largest_ask_gap=1.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=135.050,
                    iron_floor_bonds=136_000.0,
                    evidence=("低端买价走廊持续，计划内卖后回补",),
                )
                context = MakerDecisionContext(
                    reference_price=135.645,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=0.283,
                    bid_support_bonds=4_000.0,
                    ask_supply_bonds=4_000.0,
                    wall_threshold_bonds=5_000.0,
                )

                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )

                self.assertIsNotNone(account.buy_order)
                self.assertEqual(account.buy_order.kind, "inventory_replenish")
                self.assertEqual(account.buy_order.limit_price, 135.505)
            finally:
                store.close()

    def test_priority_v15_uses_recent_intraday_reference_only_for_active_entry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v15-reference.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(config, store)
                moment = datetime(2026, 8, 14, 13, 6, 57, tzinfo=SHANGHAI)
                moment_ms = int(moment.timestamp() * 1000)
                engine.previous_close_reference = 136.922
                engine.observed_market_trade = True
                engine.last_intraday_working_reference = 136.210
                engine.last_intraday_working_reference_ts_ms = moment_ms - 9_000
                tick = self._replay_tick(
                    moment, last=136.052, bid=134.061, ask=136.349,
                    previous_close=136.922,
                )

                context = engine._decision_context(
                    tick, PRIORITY_POLICY_V14_CANDIDATE,
                )
                parent = engine._active_entry_reference(
                    context, tick, PRIORITY_POLICY_V14_CANDIDATE,
                )
                candidate = engine._active_entry_reference(
                    context, tick, PRIORITY_POLICY_V15_CANDIDATE,
                )
                self.assertEqual(context.reference_source, "previous_close")
                self.assertEqual(context.reference_price, 136.922)
                self.assertEqual(parent, (136.922, "previous_close"))
                self.assertEqual(
                    candidate,
                    (136.210, "recent_intraday_working_reference"),
                )

                engine.last_intraday_working_reference_ts_ms = (
                    moment_ms
                    - (engine.parameters.market_temperature_window_seconds + 1)
                    * 1_000
                )
                expired = engine._active_entry_reference(
                    context, tick, PRIORITY_POLICY_V15_CANDIDATE,
                )
                self.assertEqual(expired, (136.922, "previous_close"))
            finally:
                store.close()

    def test_priority_v16_requires_one_visible_downtrend_support_wall(
        self,
    ) -> None:
        def quoted_buy(
            database: Path, policy, bids: tuple[tuple[float, float], ...],
        ) -> float | None:
            config = test_config(database)
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                moment = datetime(2026, 8, 14, 13, 27, 6, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                engine.last_visible_bid_wall_price = 135.111
                engine.last_visible_bid_wall_bonds = 6_000.0
                engine.last_visible_bid_wall_ts_ms = int(
                    (moment - timedelta(seconds=3)).timestamp() * 1_000
                )
                engine.last_bid_wall_left_book_ts_ms = int(
                    moment.timestamp() * 1_000
                )
                tick = replace(self._replay_tick(
                    moment, last=135.328, bid=bids[0][0], ask=135.599,
                ), bids=bids)
                assessment = MarketAssessment(
                    reference_price=135.450,
                    reference_low=135.200,
                    reference_high=135.600,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="falling",
                    state_score=-3,
                    state_confidence=0.70,
                    recent_buy_bonds=1_000.0,
                    recent_sell_bonds=4_000.0,
                    midpoint_change=-0.10,
                    short_ask_change=-0.10,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("下降阶段检查当前可见集中承托",),
                )
                context = MakerDecisionContext(
                    reference_price=135.450,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=135.599 - bids[0][0],
                    bid_support_bonds=sum(
                        quantity for price, quantity in bids
                        if price + 1e-9 >= bids[0][0] - 0.20
                    ),
                    ask_supply_bonds=1_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        engine.accounts["maker_v01_priority"],
                        tick, assessment, persist=True,
                    )
                order = engine.accounts["maker_v01_priority"].buy_order
                return order.limit_price if order is not None else None
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            small_ladder = (
                (135.221, 1_000.0),
                (135.220, 1_000.0),
                (135.201, 1_000.0),
                (135.200, 1_000.0),
                (135.150, 1_000.0),
            )
            self.assertEqual(quoted_buy(
                root / "v15-small-ladder.sqlite3",
                PRIORITY_POLICY_V15_CANDIDATE,
                small_ladder,
            ), 135.222)
            self.assertIsNone(quoted_buy(
                root / "v16-small-ladder.sqlite3",
                PRIORITY_POLICY_V16_CANDIDATE,
                small_ladder,
            ))

            current_wall = (
                (135.200, 1_000.0),
                (135.150, 1_000.0),
                (135.111, 6_000.0),
                (135.110, 3_000.0),
                (135.100, 2_000.0),
            )
            self.assertEqual(quoted_buy(
                root / "v16-current-wall.sqlite3",
                PRIORITY_POLICY_V16_CANDIDATE,
                current_wall,
            ), 135.201)

    def test_priority_v114_quotes_inside_visible_downtrend_wall_zone(
        self,
    ) -> None:
        def quoted_buy(
            database: Path, policy, *,
            bids: tuple[tuple[float, float], ...],
            ask: float, reference: float, state: str = "falling",
        ) -> float | None:
            config = test_config(database)
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                moment = datetime(2026, 8, 14, 13, 26, 21, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                tick = replace(self._replay_tick(
                    moment, last=bids[0][0], bid=bids[0][0], ask=ask,
                ), bids=bids)
                assessment = MarketAssessment(
                    reference_price=reference,
                    reference_low=reference - 0.10,
                    reference_high=reference + 0.10,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state=state,
                    state_score=-3 if state == "falling" else 0,
                    state_confidence=0.70,
                    recent_buy_bonds=1_000.0,
                    recent_sell_bonds=4_000.0,
                    midpoint_change=-0.10,
                    short_ask_change=-0.10,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("下降阶段当前集中承托仍在",),
                )
                context = MakerDecisionContext(
                    reference_price=reference,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=ask - bids[0][0],
                    bid_support_bonds=sum(quantity for _, quantity in bids),
                    ask_supply_bonds=1_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        engine.accounts["maker_v01_priority"],
                        tick, assessment, persist=True,
                    )
                order = engine.accounts["maker_v01_priority"].buy_order
                return order.limit_price if order is not None else None
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lifted_small_bids_over_wall = (
                (135.331, 1_000.0),
                (135.330, 1_000.0),
                (135.320, 1_000.0),
                (135.310, 1_000.0),
                (135.101, 8_000.0),
            )
            self.assertIsNone(quoted_buy(
                root / "v113-lifted-small-bids.sqlite3",
                PRIORITY_POLICY_V113_CANDIDATE,
                bids=lifted_small_bids_over_wall,
                ask=135.453,
                reference=135.392,
            ))
            self.assertEqual(quoted_buy(
                root / "v114-wall-cap.sqlite3",
                PRIORITY_POLICY_V114_CANDIDATE,
                bids=lifted_small_bids_over_wall,
                ask=135.453,
                reference=135.392,
            ), 135.201)

            near_wall = (
                (135.111, 2_000.0),
                (135.101, 8_000.0),
                (135.100, 2_000.0),
                (135.070, 3_000.0),
                (135.060, 8_000.0),
            )
            self.assertIsNone(quoted_buy(
                root / "v113-low-midpoint-edge.sqlite3",
                PRIORITY_POLICY_V113_CANDIDATE,
                bids=near_wall,
                ask=135.309,
                reference=135.210,
            ))
            self.assertIsNone(quoted_buy(
                root / "v114-still-needs-normal-space.sqlite3",
                PRIORITY_POLICY_V114_CANDIDATE,
                bids=near_wall,
                ask=135.309,
                reference=135.210,
            ))
            self.assertIsNone(quoted_buy(
                root / "v114-too-narrow.sqlite3",
                PRIORITY_POLICY_V114_CANDIDATE,
                bids=near_wall,
                ask=135.270,
                reference=135.210,
            ))
            self.assertIsNone(quoted_buy(
                root / "v114-stable-does-not-inherit.sqlite3",
                PRIORITY_POLICY_V114_CANDIDATE,
                bids=near_wall,
                ask=135.309,
                reference=135.210,
                state="stable",
            ))

            abnormal_deep_wall = (
                (136.050, 1_000.0),
                (136.040, 1_000.0),
                (136.030, 1_000.0),
                (136.020, 1_000.0),
                (134.001, 8_000.0),
            )
            parent_deep_quote = quoted_buy(
                root / "v113-deep-wall-control.sqlite3",
                PRIORITY_POLICY_V113_CANDIDATE,
                bids=abnormal_deep_wall,
                ask=136.300,
                reference=136.210,
            )
            candidate_deep_quote = quoted_buy(
                root / "v114-deep-wall-is-not-ordinary-cushion.sqlite3",
                PRIORITY_POLICY_V114_CANDIDATE,
                bids=abnormal_deep_wall,
                ask=136.300,
                reference=136.210,
            )
            self.assertEqual(candidate_deep_quote, parent_deep_quote)
            self.assertEqual(candidate_deep_quote, 136.051)

    def test_priority_v114_retains_wall_entry_only_while_wall_is_live(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "wall-retention.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V114_CANDIDATE,
                )
                first = datetime(2026, 8, 14, 13, 25, 39, tzinfo=SHANGHAI)
                engine._start_date(first.date().isoformat())
                live_wall = (
                    (135.331, 1_000.0),
                    (135.330, 1_000.0),
                    (135.320, 1_000.0),
                    (135.310, 1_000.0),
                    (135.101, 8_000.0),
                )
                context = MakerDecisionContext(
                    reference_price=135.392,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=135.453 - 135.331,
                    bid_support_bonds=4_000.0,
                    ask_supply_bonds=2_000.0,
                    wall_threshold_bonds=5_000.0,
                )

                def assessment(state: str) -> MarketAssessment:
                    return MarketAssessment(
                        reference_price=135.392,
                        reference_low=135.300,
                        reference_high=135.453,
                        reference_source="persistent_inside_market",
                        reference_confidence=0.55,
                        state=state,
                        state_score=-3 if state == "falling" else 0,
                        state_confidence=0.70,
                        recent_buy_bonds=1_000.0,
                        recent_sell_bonds=4_000.0,
                        midpoint_change=-0.10,
                        short_ask_change=-0.10,
                        largest_ask_gap=0.20,
                        downside_book_vacuum=False,
                        fragile_top_bid=False,
                        iron_floor_price=None,
                        iron_floor_bonds=0.0,
                        evidence=("集中承托仍在",),
                    )

                first_tick = replace(self._replay_tick(
                    first, last=135.331, bid=135.331, ask=135.453,
                ), bids=live_wall)
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    account = engine.accounts["maker_v01_priority"]
                    engine._refresh_orders(
                        account, first_tick, assessment("falling"), persist=True,
                    )
                    self.assertIsNotNone(account.buy_order)
                    assert account.buy_order is not None
                    original_id = account.buy_order.db_id
                    self.assertEqual(account.buy_order.limit_price, 135.201)
                    self.assertEqual(
                        account.buy_order.visible_wall_entry_price, 135.101,
                    )

                    stable_tick = replace(
                        first_tick,
                        market_ts_ms=first_tick.market_ts_ms + 3_000,
                        market_time="13:25:42.000",
                    )
                    engine._refresh_orders(
                        account, stable_tick, assessment("stable"), persist=True,
                    )
                    self.assertIsNotNone(account.buy_order)
                    assert account.buy_order is not None
                    self.assertEqual(account.buy_order.db_id, original_id)

                    no_wall_tick = replace(
                        stable_tick,
                        market_ts_ms=stable_tick.market_ts_ms + 3_000,
                        market_time="13:25:45.000",
                        bids=(
                            (135.331, 1_000.0),
                            (135.330, 1_000.0),
                            (135.320, 1_000.0),
                            (135.310, 1_000.0),
                            (135.300, 1_000.0),
                        ),
                    )
                    engine._refresh_orders(
                        account, no_wall_tick, assessment("stable"), persist=True,
                    )
                    self.assertIsNone(account.buy_order)
            finally:
                store.close()

    def test_priority_v115_keeps_sweep_target_when_sweep_restores_base(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            parent_store, parent_engine, parent_account, parent_tick, parent_lot = (
                self._seed_sweep_base_recovery(
                    root / "v114-sweep-restores-base.sqlite3",
                    PRIORITY_POLICY_V114_CANDIDATE,
                )
            )
            try:
                self.assertIsNone(parent_lot.target_price)
                with patch.object(
                    parent_engine, "_decision_context",
                    return_value=self._sweep_recovery_context(),
                ), patch.object(
                    parent_engine, "_base_high_sell_is_safe",
                    return_value=False,
                ):
                    parent_engine._refresh_orders(
                        parent_account, parent_tick,
                        self._sweep_recovery_assessment(), persist=True,
                    )
                self.assertFalse(parent_account.sell_orders)
            finally:
                parent_store.close()

            store, engine, account, sweep_tick, recovered_lot = (
                self._seed_sweep_base_recovery(
                    root / "v115-sweep-restores-base.sqlite3",
                    PRIORITY_POLICY_V115_CANDIDATE,
                )
            )
            try:
                self.assertEqual(recovered_lot.kind, "base")
                self.assertIsNone(recovered_lot.entry_price)
                self.assertEqual(recovered_lot.target_price, 137.195)
                with patch.object(
                    engine, "_decision_context",
                    return_value=self._sweep_recovery_context(),
                ), patch.object(
                    engine, "_base_high_sell_is_safe", return_value=False,
                ):
                    engine._refresh_orders(
                        account, sweep_tick,
                        self._sweep_recovery_assessment(), persist=True,
                    )
                    order = account.sell_orders[recovered_lot.db_id]
                    self.assertEqual(order.limit_price, 137.195)

                    exposed = self._replay_tick(
                        datetime(2026, 8, 14, 13, 44, 18, tzinfo=SHANGHAI),
                        last=136.000, bid=136.000, ask=137.196,
                        ask_bonds=8_000.0,
                    )
                    engine._refresh_orders(
                        account, exposed,
                        self._sweep_recovery_assessment(), persist=True,
                    )
                    self.assertIs(
                        account.sell_orders[recovered_lot.db_id], order,
                    )

                traded = self._replay_tick(
                    datetime(2026, 8, 14, 13, 44, 22, tzinfo=SHANGHAI),
                    last=137.196, bid=136.000, ask=137.196,
                    trade_bonds=1_000.0, inferred_side="buy",
                    ask_bonds=8_000.0,
                )
                engine._process_resting_orders(
                    account, traded, persist=True,
                    received_ts_ns=traded.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 0.0)
                self.assertNotIn(recovered_lot.db_id, account.lots)
                self.assertFalse(
                    QUEUE_POLICY_V14_CANDIDATE
                        .enable_priority_sweep_recovery_target
                )
            finally:
                store.close()

    def test_priority_v115_sweep_recovery_target_does_not_go_stale_or_revive(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store, engine, account, sweep_tick, recovered_lot = (
                self._seed_sweep_base_recovery(
                    root / "target-falls-away.sqlite3",
                    PRIORITY_POLICY_V115_CANDIDATE,
                )
            )
            try:
                with patch.object(
                    engine, "_decision_context",
                    return_value=self._sweep_recovery_context(),
                ), patch.object(
                    engine, "_base_high_sell_is_safe", return_value=False,
                ):
                    engine._refresh_orders(
                        account, sweep_tick,
                        self._sweep_recovery_assessment(), persist=True,
                    )
                    exposed = self._replay_tick(
                        datetime(2026, 8, 14, 13, 44, 18, tzinfo=SHANGHAI),
                        last=136.000, bid=136.000, ask=137.196,
                    )
                    engine._refresh_orders(
                        account, exposed,
                        self._sweep_recovery_assessment(), persist=True,
                    )
                    self.assertIn(recovered_lot.db_id, account.sell_orders)

                    lower_ask = self._replay_tick(
                        datetime(2026, 8, 14, 13, 44, 21, tzinfo=SHANGHAI),
                        last=136.000, bid=136.000, ask=136.800,
                    )
                    engine._refresh_orders(
                        account, lower_ask,
                        self._sweep_recovery_assessment(), persist=True,
                    )
                    self.assertIsNone(recovered_lot.target_price)
                    self.assertNotIn(recovered_lot.db_id, account.sell_orders)

                    returned = self._replay_tick(
                        datetime(2026, 8, 14, 13, 44, 24, tzinfo=SHANGHAI),
                        last=136.000, bid=136.000, ask=137.196,
                    )
                    engine._refresh_orders(
                        account, returned,
                        self._sweep_recovery_assessment(), persist=True,
                    )
                    self.assertNotIn(recovered_lot.db_id, account.sell_orders)
                stored_target = store.connection.execute(
                    "SELECT target_price FROM maker_paper_lots WHERE id=?",
                    (recovered_lot.db_id,),
                ).fetchone()[0]
                self.assertIsNone(stored_target)
            finally:
                store.close()

            stale_store, stale_engine, stale_account, stale_tick, stale_lot = (
                self._seed_sweep_base_recovery(
                    root / "target-times-out.sqlite3",
                    PRIORITY_POLICY_V115_CANDIDATE,
                )
            )
            try:
                with patch.object(
                    stale_engine, "_decision_context",
                    return_value=self._sweep_recovery_context(),
                ), patch.object(
                    stale_engine, "_base_high_sell_is_safe",
                    return_value=False,
                ):
                    stale_engine._refresh_orders(
                        stale_account, stale_tick,
                        self._sweep_recovery_assessment(), persist=True,
                    )
                    expired = self._replay_tick(
                        datetime(2026, 8, 14, 13, 44, 43, tzinfo=SHANGHAI),
                        last=136.000, bid=136.000, ask=137.196,
                    )
                    stale_engine._refresh_orders(
                        stale_account, expired,
                        self._sweep_recovery_assessment(), persist=True,
                    )
                self.assertIsNone(stale_lot.target_price)
                self.assertNotIn(stale_lot.db_id, stale_account.sell_orders)
            finally:
                stale_store.close()

    def test_priority_v116_reopens_only_a_fresh_supported_post_recovery_turn(
        self,
    ) -> None:
        def quoted_buy(
            database: Path, policy, *, support_bonds: float = 5_860.0,
            lower_sell_bonds: float = 2_000.0, recovery_age_seconds: int = 3,
            state: str = "possible_fall", ask: float = 136.596,
        ) -> tuple[float | None, str | None]:
            config = test_config(database)
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                moment = datetime(2026, 8, 14, 10, 48, 9, tzinfo=SHANGHAI)
                moment_ms = int(moment.timestamp() * 1_000)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.last_base_replenishment_price = 136.529
                account.last_base_replenishment_ts_ms = (
                    moment_ms - recovery_age_seconds * 1_000
                )
                if lower_sell_bonds > 0:
                    engine.analyzer.trade_evidence.append(TradeEvidence(
                        market_ts_ms=moment_ms,
                        price=136.400,
                        bonds=lower_sell_bonds,
                        transactions=2,
                        side="sell",
                    ))
                tick = replace(
                    self._replay_tick(
                        moment, last=136.400, bid=136.400, ask=ask,
                        trade_bonds=lower_sell_bonds,
                        inferred_side="sell" if lower_sell_bonds else "none",
                    ),
                    bids=(
                        (136.400, 1_000.0),
                        (136.352, 2_000.0),
                        (136.351, 2_000.0),
                        (136.350, 860.0),
                    ),
                )
                context = MakerDecisionContext(
                    reference_price=136.562,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=ask - 136.400,
                    bid_support_bonds=support_bonds,
                    ask_supply_bonds=5_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                assessment = MarketAssessment(
                    reference_price=136.562,
                    reference_low=136.400,
                    reference_high=ask,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state=state,
                    state_score=-1 if state == "possible_fall" else 0,
                    state_confidence=0.5,
                    recent_buy_bonds=2_140.0,
                    recent_sell_bonds=4_000.0,
                    midpoint_change=-0.05,
                    short_ask_change=-0.067,
                    largest_ask_gap=0.016,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("补仓后出现新的低侧卖出",),
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )
                order = account.buy_order
                return (
                    order.limit_price if order is not None else None,
                    order.kind if order is not None else None,
                )
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(
                quoted_buy(
                    root / "v116-positive.sqlite3",
                    PRIORITY_POLICY_V116_CANDIDATE,
                ),
                (136.401, "post_replenishment_supported_entry"),
            )
            self.assertEqual(
                quoted_buy(
                    root / "v115-parent.sqlite3",
                    PRIORITY_POLICY_V115_CANDIDATE,
                ),
                (None, None),
            )
            self.assertEqual(
                quoted_buy(
                    root / "v116-no-support.sqlite3",
                    PRIORITY_POLICY_V116_CANDIDATE,
                    support_bonds=4_999.0,
                ),
                (None, None),
            )
            self.assertEqual(
                quoted_buy(
                    root / "v116-no-new-sell.sqlite3",
                    PRIORITY_POLICY_V116_CANDIDATE,
                    lower_sell_bonds=0.0,
                ),
                (None, None),
            )
            self.assertEqual(
                quoted_buy(
                    root / "v116-stale.sqlite3",
                    PRIORITY_POLICY_V116_CANDIDATE,
                    recovery_age_seconds=31,
                ),
                (None, None),
            )
            self.assertEqual(
                quoted_buy(
                    root / "v116-rising.sqlite3",
                    PRIORITY_POLICY_V116_CANDIDATE,
                    state="rising",
                ),
                (None, None),
            )
            self.assertEqual(
                quoted_buy(
                    root / "v116-too-narrow.sqlite3",
                    PRIORITY_POLICY_V116_CANDIDATE,
                    ask=136.570,
                ),
                (None, None),
            )
            self.assertFalse(
                QUEUE_POLICY_V14_CANDIDATE
                    .enable_supported_post_replenishment_entry
            )

    def test_priority_v117_corrects_only_a_strong_opposite_book_side(self) -> None:
        def buy_fill(database: Path, policy, *, ambiguous: bool = False):
            config = test_config(database)
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                moment = datetime(2026, 8, 14, 10, 48, 9, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                seed = self._replay_tick(
                    moment, last=136.400, bid=136.400, ask=136.596,
                )
                limit = 136.500 if ambiguous else 136.401
                order = engine._new_order(
                    account, seed, side="buy",
                    kind="post_replenishment_supported_entry", lot_id=None,
                    price=limit, quantity=1_000.0, queue_ahead=0.0,
                    target_price=None, persist=True,
                )
                account.buy_order = order
                trade = self._replay_tick(
                    moment + timedelta(seconds=6),
                    last=136.500 if ambiguous else 136.401,
                    bid=136.450 if ambiguous else 136.400,
                    ask=136.550 if ambiguous else 136.596,
                    trade_bonds=1_000.0, inferred_side="buy",
                )
                engine._process_resting_orders(
                    account, trade, persist=True,
                    received_ts_ns=trade.market_ts_ms * 1_000_000,
                )
                reason_row = store.connection.execute(
                    "SELECT fill_reason FROM maker_paper_fills ORDER BY id DESC LIMIT 1"
                ).fetchone()
                return (
                    account.inventory,
                    reason_row[0] if reason_row is not None else None,
                )
            finally:
                store.close()

        def sell_fill(database: Path, policy):
            config = test_config(database)
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                moment = datetime(2026, 8, 14, 10, 49, 30, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                lot_id = store.insert_maker_lot({
                    "run_id": store.run_id,
                    "market_date": account.market_date,
                    "strategy_id": account.strategy_id,
                    "kind": "low_bid_reversion",
                    "opened_market_ts_ms": int(moment.timestamp() * 1_000),
                    "entry_price": 136.401,
                    "original_quantity": 1_000.0,
                    "remaining_quantity": 1_000.0,
                    "target_price": None,
                    "status": "open",
                    "updated_market_ts_ms": int(moment.timestamp() * 1_000),
                })
                account.lots[lot_id] = MakerLot(
                    lot_id, "low_bid_reversion",
                    int(moment.timestamp() * 1_000), 136.401,
                    1_000.0, 1_000.0,
                )
                account.inventory += 1_000.0
                seed = self._replay_tick(
                    moment, last=136.500, bid=136.400, ask=136.601,
                )
                order = engine._new_order(
                    account, seed, side="sell", kind="inventory_exit",
                    lot_id=lot_id, price=136.599, quantity=1_000.0,
                    queue_ahead=0.0, target_price=136.599, persist=True,
                )
                account.sell_orders[lot_id] = order
                trade = self._replay_tick(
                    moment + timedelta(seconds=6),
                    last=136.600, bid=136.400, ask=136.601,
                    trade_bonds=1_000.0, inferred_side="sell",
                )
                engine._process_resting_orders(
                    account, trade, persist=True,
                    received_ts_ns=trade.market_ts_ms * 1_000_000,
                )
                reason_row = store.connection.execute(
                    "SELECT fill_reason FROM maker_paper_fills ORDER BY id DESC LIMIT 1"
                ).fetchone()
                return (
                    account.inventory,
                    reason_row[0] if reason_row is not None else None,
                )
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(
                buy_fill(
                    root / "v117-bid-side.sqlite3",
                    PRIORITY_POLICY_V117_CANDIDATE,
                ),
                (2_000.0, "priority_book_side_passive_buy"),
            )
            self.assertEqual(
                buy_fill(
                    root / "v116-parent.sqlite3",
                    PRIORITY_POLICY_V116_CANDIDATE,
                ),
                (1_000.0, None),
            )
            self.assertEqual(
                buy_fill(
                    root / "v117-midpoint-ambiguous.sqlite3",
                    PRIORITY_POLICY_V117_CANDIDATE,
                    ambiguous=True,
                ),
                (1_000.0, None),
            )
            self.assertEqual(
                sell_fill(
                    root / "v117-ask-side.sqlite3",
                    PRIORITY_POLICY_V117_CANDIDATE,
                ),
                (1_000.0, "priority_book_side_passive_sell"),
            )
            self.assertFalse(
                QUEUE_POLICY_V14_CANDIDATE
                    .enable_priority_book_side_fill_correction
            )

    def test_priority_v118_uses_a_fresh_lower_live_wall_after_replenishment(
        self,
    ) -> None:
        def quoted_buy(
            database: Path, policy, *, bids: tuple[tuple[float, float], ...],
            ask: float, replenishment_price: float,
            replenishment_age_seconds: int = 207,
        ) -> tuple[float | None, float]:
            config = test_config(database)
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                moment = datetime(2026, 8, 14, 14, 49, 48, tzinfo=SHANGHAI)
                moment_ms = int(moment.timestamp() * 1_000)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.last_base_replenishment_price = replenishment_price
                account.last_base_replenishment_ts_ms = (
                    moment_ms - replenishment_age_seconds * 1_000
                )
                tick = replace(self._replay_tick(
                    moment, last=bids[0][0], bid=bids[0][0], ask=ask,
                ), bids=bids)
                assessment = MarketAssessment(
                    reference_price=135.601,
                    reference_low=135.401,
                    reference_high=135.602,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="falling",
                    state_score=-3,
                    state_confidence=0.75,
                    recent_buy_bonds=1_000.0,
                    recent_sell_bonds=20_000.0,
                    midpoint_change=-0.20,
                    short_ask_change=-0.20,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=135.051,
                    iron_floor_bonds=92_000.0,
                    evidence=("较高买墙被砸穿后，新的近端墙仍可见",),
                )
                context = MakerDecisionContext(
                    reference_price=135.601,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=ask - bids[0][0],
                    bid_support_bonds=sum(
                        quantity for price, quantity in bids
                        if bids[0][0] - price <= 0.20 + 1e-9
                    ),
                    ask_supply_bonds=1_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )
                order = account.buy_order
                return (
                    order.limit_price if order is not None else None,
                    order.visible_wall_entry_price if order is not None else 0.0,
                )
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lower_live_wall = (
                (135.401, 2_000.0),
                (135.400, 9_000.0),
                (135.051, 8_000.0),
                (135.050, 74_000.0),
                (135.001, 10_000.0),
            )
            self.assertEqual(quoted_buy(
                root / "v117-old-iron-floor.sqlite3",
                PRIORITY_POLICY_V117_CANDIDATE,
                bids=lower_live_wall,
                ask=135.602,
                replenishment_price=135.626,
            ), (135.351, 0.0))
            self.assertEqual(quoted_buy(
                root / "v118-fresh-lower-wall.sqlite3",
                PRIORITY_POLICY_V118_CANDIDATE,
                bids=lower_live_wall,
                ask=135.602,
                replenishment_price=135.626,
            ), (135.402, 135.400))

            same_replenishment_zone = (
                (135.629, 1_000.0),
                (135.600, 15_000.0),
                (135.401, 2_000.0),
                (135.400, 9_000.0),
                (135.050, 74_000.0),
            )
            self.assertEqual(quoted_buy(
                root / "v118-no-new-lower-edge.sqlite3",
                PRIORITY_POLICY_V118_CANDIDATE,
                bids=same_replenishment_zone,
                ask=135.997,
                replenishment_price=135.626,
            ), (135.351, 0.0))
            self.assertEqual(quoted_buy(
                root / "v118-expired-replenishment.sqlite3",
                PRIORITY_POLICY_V118_CANDIDATE,
                bids=lower_live_wall,
                ask=135.602,
                replenishment_price=135.626,
                replenishment_age_seconds=601,
            ), (135.351, 0.0))
            self.assertFalse(
                QUEUE_POLICY_V14_CANDIDATE
                    .prefer_fresh_lower_visible_wall_after_base_replenishment
            )

    def test_priority_v119_uses_confirmed_wall_supported_base_sale_edge(
        self,
    ) -> None:
        def quoted_sell(
            database: Path, policy, *, reference: float,
            ask_supply_bonds: float = 6_000.0,
        ) -> float | None:
            config = test_config(database)
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                moment = datetime(2026, 8, 14, 10, 10, 33, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                tick = self._replay_tick(
                    moment, last=136.569, bid=136.234, ask=136.799,
                )
                assessment = MarketAssessment(
                    reference_price=reference,
                    reference_low=136.234,
                    reference_high=136.799,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="rising",
                    state_score=3,
                    state_confidence=0.75,
                    recent_buy_bonds=2_000.0,
                    recent_sell_bonds=0.0,
                    midpoint_change=0.10,
                    short_ask_change=0.23,
                    largest_ask_gap=0.576,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("卖一厚墙仍在且高卖空间超过0.20元",),
                )
                context = MakerDecisionContext(
                    reference_price=reference,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=136.799 - 136.234,
                    bid_support_bonds=6_000.0,
                    ask_supply_bonds=ask_supply_bonds,
                    wall_threshold_bonds=5_000.0,
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )
                order = next(iter(account.sell_orders.values()), None)
                return order.limit_price if order is not None else None
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertIsNone(quoted_sell(
                root / "v118-still-uses-030.sqlite3",
                PRIORITY_POLICY_V118_CANDIDATE,
                reference=136.517,
            ))
            self.assertEqual(quoted_sell(
                root / "v119-confirmed-020.sqlite3",
                PRIORITY_POLICY_V119_CANDIDATE,
                reference=136.517,
            ), 136.798)
            self.assertIsNone(quoted_sell(
                root / "v119-below-020.sqlite3",
                PRIORITY_POLICY_V119_CANDIDATE,
                reference=136.620,
            ))
            self.assertIsNone(quoted_sell(
                root / "v119-thin-ask.sqlite3",
                PRIORITY_POLICY_V119_CANDIDATE,
                reference=136.517,
                ask_supply_bonds=4_990.0,
            ))
            self.assertIsNone(
                QUEUE_POLICY_V14_CANDIDATE
                    .minimum_wall_supported_base_high_sell_edge_override
            )

    def test_priority_v120_keeps_a_recent_sell_corridor_and_reprices_recovery(
        self,
    ) -> None:
        def assessment(
            state: str, score: int, reference: float,
        ) -> MarketAssessment:
            return MarketAssessment(
                reference_price=reference,
                reference_low=136.601,
                reference_high=136.987,
                reference_source="current_midpoint",
                reference_confidence=0.35,
                state=state,
                state_score=score,
                state_confidence=0.62,
                recent_buy_bonds=1_000.0,
                recent_sell_bonds=1_240.0,
                midpoint_change=0.19 if score > 0 else 0.0,
                short_ask_change=0.0,
                largest_ask_gap=0.0,
                downside_book_vacuum=False,
                fragile_top_bid=False,
                iron_floor_price=None,
                iron_floor_bonds=0.0,
                evidence=("高低成交走廊仍有实时回补空间",),
            )

        def seed_case(database: Path, policy):
            config = test_config(database)
            store = SQLiteStore(config)
            engine = MakerPaperEngine(
                config, store, priority_policy=policy,
            )
            moment = datetime(2026, 8, 13, 11, 6, 30, tzinfo=SHANGHAI)
            engine._start_date(moment.date().isoformat())
            account = engine.accounts["maker_v01_priority"]
            base_lot = next(
                lot for lot in account.lots.values()
                if lot.entry_price is None
            )
            original = self._replay_tick(
                moment, last=136.760, bid=136.601, ask=136.987,
            )
            order = engine._new_order(
                account, original, side="sell", kind="inventory_exit",
                lot_id=base_lot.db_id, price=136.986, quantity=1_000.0,
                queue_ahead=0.0, target_price=136.986, persist=True,
                repeated_turn_replenishment_price=136.602,
            )
            order.stable_context_grace_eligible = True
            order.base_turn_corridor_origin = True
            order.base_turn_replenishment_ceiling = 136.602
            account.sell_orders[base_lot.db_id] = order
            engine.analyzer.trade_evidence.append(TradeEvidence(
                market_ts_ms=original.market_ts_ms - 60_000,
                price=136.760,
                bonds=1_240.0,
                transactions=2,
                side="sell",
            ))

            # The same order is momentarily valid under the ordinary stable
            # fair-value rule.  Its immediate grace tag is cleared, while the
            # causal sell-first origin must survive on the unchanged order.
            stable_context = MakerDecisionContext(
                reference_price=136.794,
                reference_source="current_midpoint",
                reliable_anchor=False,
                spread=0.386,
                bid_support_bonds=2_000.0,
                ask_supply_bonds=6_000.0,
                wall_threshold_bonds=5_000.0,
            )
            with patch.object(
                engine, "_decision_context", return_value=stable_context,
            ):
                engine._refresh_orders(
                    account, original,
                    assessment("stable", 0, 136.794), persist=True,
                )
            self.assertIs(account.sell_orders[base_lot.db_id], order)
            self.assertFalse(order.stable_context_grace_eligible)
            self.assertTrue(order.base_turn_corridor_origin)
            return store, engine, account, base_lot, order, moment

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parent = seed_case(
                root / "priority-v119-corridor.sqlite3",
                PRIORITY_POLICY_V119_CANDIDATE,
            )
            parent_store, parent_engine, parent_account, parent_lot, _, moment = (
                parent
            )
            try:
                lifted = self._replay_tick(
                    moment + timedelta(seconds=6),
                    last=136.760, bid=136.793, ask=136.987,
                )
                lifted_context = MakerDecisionContext(
                    reference_price=136.890,
                    reference_source="current_midpoint",
                    reliable_anchor=False,
                    spread=0.194,
                    bid_support_bonds=2_000.0,
                    ask_supply_bonds=1_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                with patch.object(
                    parent_engine, "_decision_context",
                    return_value=lifted_context,
                ):
                    parent_engine._refresh_orders(
                        parent_account, lifted,
                        assessment("possible_rise", 1, 136.890),
                        persist=True,
                    )
                self.assertNotIn(parent_lot.db_id, parent_account.sell_orders)
                self.assertFalse(
                    PRIORITY_POLICY_V119_CANDIDATE
                        .retain_priority_base_turn_on_recent_sell_corridor
                )
            finally:
                parent_store.close()

            candidate = seed_case(
                root / "priority-v120-corridor.sqlite3",
                PRIORITY_POLICY_V120_CANDIDATE,
            )
            store, engine, account, base_lot, order, moment = candidate
            try:
                lifted = self._replay_tick(
                    moment + timedelta(seconds=6),
                    last=136.760, bid=136.793, ask=136.987,
                )
                lifted_context = MakerDecisionContext(
                    reference_price=136.890,
                    reference_source="current_midpoint",
                    reliable_anchor=False,
                    spread=0.194,
                    bid_support_bonds=2_000.0,
                    ask_supply_bonds=1_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                weak_rise = assessment("possible_rise", 1, 136.890)
                with patch.object(
                    engine, "_decision_context", return_value=lifted_context,
                ):
                    engine._refresh_orders(
                        account, lifted, weak_rise, persist=True,
                    )
                self.assertIs(account.sell_orders[base_lot.db_id], order)
                self.assertTrue(order.retained_after_recent_sell_corridor)

                # The positive high print fills the retained first-position
                # order.  Its replenishment target follows the live 136.601
                # lower side, rather than a stale completed-turn target.
                order.repeated_turn_replenishment_price = 136.302
                high_buy = self._replay_tick(
                    moment + timedelta(seconds=9),
                    last=136.987, bid=136.601, ask=136.988,
                    trade_bonds=1_000.0, inferred_side="buy",
                )
                engine._process_resting_orders(
                    account, high_buy, persist=True,
                    received_ts_ns=high_buy.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 0.0)
                self.assertEqual(
                    account.pending_repeated_turn_replenishment_price,
                    136.602,
                )
                engine.observed_market_trade = True
                recovery_context = replace(
                    lifted_context, spread=136.988 - 136.601,
                    reference_price=136.794,
                )
                with patch.object(
                    engine, "_decision_context",
                    return_value=recovery_context,
                ):
                    engine._refresh_orders(
                        account, high_buy, weak_rise, persist=True,
                    )
                self.assertIsNotNone(account.buy_order)
                self.assertEqual(account.buy_order.limit_price, 136.602)

                low_sell = self._replay_tick(
                    moment + timedelta(seconds=12),
                    last=136.601, bid=136.601, ask=136.987,
                    trade_bonds=1_000.0, inferred_side="sell",
                )
                engine._process_resting_orders(
                    account, low_sell, persist=True,
                    received_ts_ns=low_sell.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_000.0)

                # A stronger provisional rise, confirmed rise, insufficient
                # edge, vanished lower prints, or extra inventory each ends
                # the corridor; none may inherit this retention rule.
                guard_lot = next(
                    lot for lot in account.lots.values()
                    if lot.entry_price is None
                )
                guard_order = engine._new_order(
                    account, lifted, side="sell", kind="inventory_exit",
                    lot_id=guard_lot.db_id,
                    price=136.986, quantity=1_000.0, queue_ahead=0.0,
                    target_price=136.986, persist=True,
                )
                guard_order.base_turn_corridor_origin = True
                self.assertFalse(
                    engine._retain_priority_base_turn_recent_sell_corridor(
                        account, guard_lot, guard_order, lifted,
                        assessment("possible_rise", 2, 136.890),
                        lifted_context,
                    )
                )
                self.assertFalse(
                    engine._retain_priority_base_turn_recent_sell_corridor(
                        account, guard_lot, guard_order, lifted,
                        assessment("rising", 3, 136.890), lifted_context,
                    )
                )
                narrow = replace(lifted, bids=((136.807, 1_000.0),))
                self.assertFalse(
                    engine._retain_priority_base_turn_recent_sell_corridor(
                        account, guard_lot, guard_order, narrow,
                        weak_rise, replace(lifted_context, spread=0.180),
                    )
                )
                engine.analyzer.trade_evidence.clear()
                self.assertFalse(
                    engine._retain_priority_base_turn_recent_sell_corridor(
                        account, guard_lot, guard_order, lifted,
                        weak_rise, lifted_context,
                    )
                )
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    market_ts_ms=lifted.market_ts_ms - 3_000,
                    price=136.760, bonds=1_240.0,
                    transactions=2, side="sell",
                ))
                account.lots[-1] = MakerLot(
                    -1, "low_bid_reversion", lifted.market_ts_ms,
                    136.602, 1_000.0, 1_000.0,
                )
                self.assertFalse(
                    engine._retain_priority_base_turn_recent_sell_corridor(
                        account, guard_lot, guard_order, lifted,
                        weak_rise, lifted_context,
                    )
                )
                del account.lots[-1]
                engine.last_confirmed_rise_trade_ts_ms = lifted.market_ts_ms
                engine.last_confirmed_rise_price = 136.987
                self.assertFalse(
                    engine._retain_priority_base_turn_recent_sell_corridor(
                        account, guard_lot, guard_order, lifted,
                        weak_rise, lifted_context,
                    )
                )
                self.assertFalse(
                    QUEUE_POLICY_V16_CANDIDATE
                        .retain_priority_base_turn_on_recent_sell_corridor
                )
            finally:
                store.close()

    def test_priority_v17_uses_local_reference_after_base_replenishment(
        self,
    ) -> None:
        def quoted_buy(
            database: Path, policy, *, bid: float, ask: float,
            reference: float, replenishment: float,
            bid_support: float = 1_000.0, confirmed_rise: bool = False,
            replenishment_age_seconds: int = 132,
        ) -> tuple[float | None, float]:
            config = test_config(database)
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                moment = datetime(2026, 8, 13, 10, 39, 42, tzinfo=SHANGHAI)
                moment_ms = int(moment.timestamp() * 1_000)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.last_base_replenishment_price = replenishment
                account.last_base_replenishment_ts_ms = (
                    moment_ms - replenishment_age_seconds * 1_000
                )
                if confirmed_rise:
                    engine.last_confirmed_rise_trade_ts_ms = moment_ms - 3_000
                    engine.last_confirmed_rise_price = ask
                tick = self._replay_tick(
                    moment, last=bid, bid=bid, ask=ask,
                )
                assessment = MarketAssessment(
                    reference_price=reference,
                    reference_low=replenishment,
                    reference_high=reference,
                    reference_source="intraday_trade_anchor",
                    reference_confidence=0.70,
                    state="possible_rise",
                    state_score=2,
                    state_confidence=0.62,
                    recent_buy_bonds=0.0,
                    recent_sell_bonds=1_000.0,
                    midpoint_change=0.10,
                    short_ask_change=0.0,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("买一仅靠挂单阶梯抬高，尚无真实恢复确认",),
                )
                context = MakerDecisionContext(
                    reference_price=reference,
                    reference_source="intraday_trade_anchor",
                    reliable_anchor=True,
                    spread=ask - bid,
                    bid_support_bonds=bid_support,
                    ask_supply_bonds=1_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                guarded = engine._ordinary_extra_entry_reference(
                    account, tick, context.reference_price,
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )
                order = account.buy_order
                return (
                    order.limit_price if order is not None else None,
                    guarded,
                )
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parent_order, parent_reference = quoted_buy(
                root / "v16-old-anchor.sqlite3",
                PRIORITY_POLICY_V16_CANDIDATE,
                bid=137.052, ask=137.403,
                reference=137.559, replenishment=136.160,
            )
            self.assertEqual(parent_order, 137.053)
            self.assertAlmostEqual(parent_reference, 137.559)

            candidate_order, candidate_reference = quoted_buy(
                root / "v17-local-reference.sqlite3",
                PRIORITY_POLICY_V17_CANDIDATE,
                bid=137.052, ask=137.403,
                reference=137.559, replenishment=136.160,
            )
            self.assertIsNone(candidate_order)
            self.assertAlmostEqual(candidate_reference, 137.2275)

            recovered_order, recovered_reference = quoted_buy(
                root / "v17-confirmed-rise.sqlite3",
                PRIORITY_POLICY_V17_CANDIDATE,
                bid=137.052, ask=137.403,
                reference=137.559, replenishment=136.160,
                confirmed_rise=True,
            )
            self.assertEqual(recovered_order, 137.053)
            self.assertAlmostEqual(recovered_reference, 137.559)

            expired_order, expired_reference = quoted_buy(
                root / "v17-expired-low-reference.sqlite3",
                PRIORITY_POLICY_V17_CANDIDATE,
                bid=137.052, ask=137.403,
                reference=137.559, replenishment=136.160,
                replenishment_age_seconds=601,
            )
            self.assertEqual(expired_order, 137.053)
            self.assertAlmostEqual(expired_reference, 137.559)

            supported_order, supported_reference = quoted_buy(
                root / "v17-current-wall.sqlite3",
                PRIORITY_POLICY_V17_CANDIDATE,
                bid=135.200, ask=135.600,
                reference=135.650, replenishment=135.508,
                bid_support=6_000.0,
            )
            self.assertEqual(supported_order, 135.201)
            self.assertAlmostEqual(supported_reference, 135.508)

    def test_priority_v17_applies_post_replenishment_reference_to_active_entry(
        self,
    ) -> None:
        def inventory_after_active_entry(
            database: Path, *, confirmed_rise: bool,
        ) -> float:
            config = test_config(database)
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V17_CANDIDATE,
                )
                moment = datetime(2026, 8, 13, 10, 40, tzinfo=SHANGHAI)
                moment_ms = int(moment.timestamp() * 1_000)
                engine._start_date(moment.date().isoformat())
                engine.observed_market_trade = True
                account = engine.accounts["maker_v01_priority"]
                account.last_base_replenishment_price = 136.200
                account.last_base_replenishment_ts_ms = moment_ms - 60_000
                if confirmed_rise:
                    engine.last_confirmed_rise_trade_ts_ms = moment_ms - 3_000
                    engine.last_confirmed_rise_price = 137.000
                tick = self._replay_tick(
                    moment, last=136.400, bid=136.400, ask=136.800,
                    ask_bonds=1_000.0,
                )
                assessment = MarketAssessment(
                    reference_price=137.400,
                    reference_low=136.200,
                    reference_high=137.600,
                    reference_source="intraday_trade_anchor",
                    reference_confidence=0.70,
                    state="possible_rise",
                    state_score=2,
                    state_confidence=0.62,
                    recent_buy_bonds=0.0,
                    recent_sell_bonds=1_000.0,
                    midpoint_change=0.10,
                    short_ask_change=0.0,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("测试主动新增仓局部参考",),
                )
                context = MakerDecisionContext(
                    reference_price=137.400,
                    reference_source="intraday_trade_anchor",
                    reliable_anchor=True,
                    spread=0.400,
                    bid_support_bonds=6_000.0,
                    ask_supply_bonds=1_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._active_discount_entry(
                        account, tick, assessment, persist=True,
                    )
                return account.inventory
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(inventory_after_active_entry(
                root / "v17-active-guard.sqlite3",
                confirmed_rise=False,
            ), 1_000.0)
            self.assertEqual(inventory_after_active_entry(
                root / "v17-active-recovered.sqlite3",
                confirmed_rise=True,
            ), 2_000.0)

    def test_priority_v18_exits_only_extra_inventory_into_a_falling_profitable_bid(
        self,
    ) -> None:
        def run_case(
            database: Path, *, policy=PRIORITY_POLICY_V18_CANDIDATE,
            state: str = "possible_fall", trade_bonds: float = 4_000.0,
            bid: float = 136.100, bid_bonds: float = 3_000.0,
            recent_buy_bonds: float = 4_000.0,
            recent_sell_bonds: float = 24_380.0,
            add_extra_lot: bool = True, confirmed_rise: bool = False,
        ) -> tuple[float, list[str]]:
            config = test_config(database)
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                moment = datetime(2026, 8, 14, 14, 35, 33, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                entry_tick = self._replay_tick(
                    moment - timedelta(seconds=36),
                    last=136.012, bid=136.011, ask=136.599,
                    trade_bonds=4_000.0, inferred_side="sell",
                    bid_bonds=27_000.0,
                )
                if add_extra_lot:
                    order = engine._new_order(
                        account, entry_tick, side="buy",
                        kind="low_bid_reversion", lot_id=None,
                        price=136.013, quantity=1_000.0,
                        queue_ahead=0.0, target_price=None, persist=True,
                    )
                    account.buy_order = order
                    engine._fill_buy(
                        account, entry_tick, order, 1_000.0,
                        entry_tick.market_ts_ms * 1_000_000,
                        kind="low_bid_reversion", target_price=None,
                        persist=True,
                    )
                tick = self._replay_tick(
                    moment, last=bid, bid=bid, ask=136.597,
                    trade_bonds=trade_bonds, inferred_side="sell",
                    bid_bonds=bid_bonds,
                )
                if confirmed_rise:
                    engine.last_confirmed_rise_trade_ts_ms = (
                        tick.market_ts_ms - 3_000
                    )
                    engine.last_confirmed_rise_price = 136.200
                assessment = MarketAssessment(
                    reference_price=136.305,
                    reference_low=136.011,
                    reference_high=136.599,
                    reference_source="intraday_trade_anchor",
                    reference_confidence=0.70,
                    state=state,
                    state_score=-2 if state == "possible_fall" else 0,
                    state_confidence=0.70,
                    recent_buy_bonds=recent_buy_bonds,
                    recent_sell_bonds=recent_sell_bonds,
                    midpoint_change=-0.10,
                    short_ask_change=-0.001,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=136.011,
                    iron_floor_bonds=27_000.0,
                    evidence=("盈利买盘正在被主动卖单消耗",),
                )
                engine._active_falling_profitable_bid_exit(
                    account, tick, assessment, persist=True,
                    received_ts_ns=tick.market_ts_ms * 1_000_000,
                )
                reasons = [
                    row[0] for row in store.connection.execute(
                        "SELECT fill_reason FROM maker_paper_fills "
                        "WHERE side='sell' ORDER BY id"
                    )
                ]
                return account.inventory, reasons
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parent_inventory, parent_reasons = run_case(
                root / "v17-parent.sqlite3",
                policy=PRIORITY_POLICY_V17_CANDIDATE,
            )
            self.assertEqual(parent_inventory, 2_000)
            self.assertEqual(parent_reasons, [])

            inventory, reasons = run_case(root / "v18-positive.sqlite3")
            self.assertEqual(inventory, 1_000)
            self.assertEqual(reasons, ["active_falling_profitable_bid_exit"])

            negative_cases = (
                ("base-only", {"add_extra_lot": False}),
                ("stable", {"state": "stable"}),
                ("small-sell", {"trade_bonds": 2_000.0}),
                ("small-profit", {"bid": 136.060}),
                ("thin-bid", {"bid_bonds": 500.0}),
                ("buy-dominant", {
                    "recent_buy_bonds": 20_000.0,
                    "recent_sell_bonds": 24_000.0,
                }),
                ("confirmed-rise", {"confirmed_rise": True}),
            )
            for name, overrides in negative_cases:
                with self.subTest(name=name):
                    inventory, reasons = run_case(
                        root / f"v18-{name}.sqlite3", **overrides,
                    )
                    expected_inventory = 1_000 if name == "base-only" else 2_000
                    self.assertEqual(inventory, expected_inventory)
                    self.assertEqual(reasons, [])

    def test_priority_v18_requires_a_new_lower_edge_after_active_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v18-reentry.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V18_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 14, 35, 33, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.last_falling_profitable_exit_price = 136.100
                account.last_falling_profitable_exit_ts_ms = int(
                    moment.timestamp() * 1_000
                )
                tick = self._replay_tick(
                    moment, last=136.100, bid=136.100, ask=136.597,
                    trade_bonds=4_000.0, inferred_side="sell",
                    bid_bonds=3_000.0,
                )
                assessment = MarketAssessment(
                    reference_price=136.315,
                    reference_low=136.011,
                    reference_high=136.599,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="possible_fall",
                    state_score=-2,
                    state_confidence=0.70,
                    recent_buy_bonds=4_000.0,
                    recent_sell_bonds=24_380.0,
                    midpoint_change=-0.10,
                    short_ask_change=-0.001,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("主动退出后等待新的低价空间",),
                )
                context = MakerDecisionContext(
                    reference_price=136.315,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=0.497,
                    bid_support_bonds=18_000.0,
                    ask_supply_bonds=4_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )

                self.assertIsNone(account.buy_order)

                lower = replace(
                    self._replay_tick(
                        moment + timedelta(seconds=195),
                        last=135.911, bid=135.911, ask=135.947,
                        bid_bonds=2_000.0,
                    ),
                    bids=((135.911, 2_000.0), (135.910, 5_000.0)),
                )
                lower_context = replace(
                    context,
                    reference_price=136.200,
                    spread=135.947 - 135.911,
                    bid_support_bonds=7_000.0,
                )
                with patch.object(
                    engine, "_decision_context", return_value=lower_context,
                ):
                    engine._refresh_orders(
                        account, lower, assessment, persist=True,
                    )
                self.assertIsNotNone(account.buy_order)
                self.assertEqual(account.buy_order.limit_price, 135.912)
            finally:
                store.close()

    def test_priority_v19_keeps_a_stable_wide_spread_base_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v19-stable-turn.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V19_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 13, 11, 36, tzinfo=SHANGHAI)
                moment_ms = int(moment.timestamp() * 1_000)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                base_lot = next(
                    lot for lot in account.lots.values()
                    if lot.entry_price is None
                )
                tick = self._replay_tick(
                    moment, last=135.701, bid=136.002, ask=136.239,
                    bid_bonds=2_000.0, ask_bonds=2_000.0,
                )
                engine.analyzer.book_quotes.extend((
                    BookQuote(moment_ms - 18_000, 136.002, 136.239),
                    BookQuote(moment_ms - 9_000, 136.002, 136.239),
                    BookQuote(moment_ms, 136.002, 136.239),
                ))
                context = MakerDecisionContext(
                    reference_price=136.120,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=0.237,
                    bid_support_bonds=4_000.0,
                    ask_supply_bonds=2_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                assessment = MarketAssessment(
                    reference_price=136.120,
                    reference_low=136.002,
                    reference_high=136.239,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="stable",
                    state_score=0,
                    state_confidence=0.62,
                    recent_buy_bonds=0.0,
                    recent_sell_bonds=1_000.0,
                    midpoint_change=0.0,
                    short_ask_change=0.0,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("下降卖单短暂切为稳定，低端走廊未变",),
                )
                order = engine._new_order(
                    account, tick, side="sell", kind="inventory_exit",
                    lot_id=base_lot.db_id, price=136.238,
                    quantity=1_000.0, queue_ahead=0.0,
                    target_price=136.238, persist=True,
                )
                order.stable_context_grace_eligible = True
                account.sell_orders[base_lot.db_id] = order

                self.assertFalse(engine._base_high_sell_is_safe(
                    136.238, context, PRIORITY_POLICY_V19_CANDIDATE,
                    "stable", persistent_lower_bid=True,
                ))
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )
                self.assertIs(account.sell_orders[base_lot.db_id], order)
                self.assertTrue(order.retained_after_context_loss)

                expired = replace(
                    tick,
                    market_ts_ms=moment_ms + 18_000,
                    market_time=(moment + timedelta(seconds=18)).time().isoformat(
                        timespec="milliseconds"
                    ),
                )
                engine.analyzer.book_quotes.append(
                    BookQuote(moment_ms + 18_000, 136.002, 136.239)
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, expired, assessment, persist=True,
                    )
                self.assertNotIn(base_lot.db_id, account.sell_orders)
            finally:
                store.close()

    def test_priority_v19_clears_old_grace_eligibility_when_context_changes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v19-clear-grace.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V19_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 13, 11, 36, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                base_lot = next(
                    lot for lot in account.lots.values()
                    if lot.entry_price is None
                )
                tick = self._replay_tick(
                    moment, last=136.120, bid=136.002, ask=136.239,
                    bid_bonds=2_000.0, ask_bonds=6_000.0,
                )
                assessment = MarketAssessment(
                    reference_price=135.800,
                    reference_low=135.700,
                    reference_high=135.900,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="stable",
                    state_score=0,
                    state_confidence=0.62,
                    recent_buy_bonds=0.0,
                    recent_sell_bonds=1_000.0,
                    midpoint_change=0.0,
                    short_ask_change=0.0,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("普通高卖依据接管原下行依据",),
                )
                order = engine._new_order(
                    account, tick, side="sell", kind="inventory_exit",
                    lot_id=base_lot.db_id, price=136.238,
                    quantity=1_000.0, queue_ahead=0.0,
                    target_price=136.238, persist=True,
                )
                order.stable_context_grace_eligible = True
                account.sell_orders[base_lot.db_id] = order
                ordinary_high_sell = MakerDecisionContext(
                    reference_price=135.800,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=0.237,
                    bid_support_bonds=4_000.0,
                    ask_supply_bonds=6_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                with patch.object(
                    engine, "_decision_context",
                    return_value=ordinary_high_sell,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )

                self.assertIs(account.sell_orders[base_lot.db_id], order)
                self.assertFalse(order.stable_context_grace_eligible)

                no_longer_safe = replace(
                    ordinary_high_sell,
                    reference_price=136.120,
                    ask_supply_bonds=2_000.0,
                )
                later = replace(
                    tick,
                    market_ts_ms=tick.market_ts_ms + 3_000,
                    market_time=(moment + timedelta(seconds=3)).time().isoformat(
                        timespec="milliseconds"
                    ),
                )
                with patch.object(
                    engine, "_decision_context", return_value=no_longer_safe,
                ):
                    engine._refresh_orders(
                        account, later, assessment, persist=True,
                    )
                self.assertNotIn(base_lot.db_id, account.sell_orders)
            finally:
                store.close()

    def test_priority_v19_does_not_carry_grace_order_across_lunch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v19-lunch-grace.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V19_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 11, 30, tzinfo=SHANGHAI)
                moment_ms = int(moment.timestamp() * 1_000)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                base_lot = next(
                    lot for lot in account.lots.values()
                    if lot.entry_price is None
                )
                tick = self._replay_tick(
                    moment, last=136.210, bid=136.210, ask=136.590,
                    bid_bonds=2_000.0, ask_bonds=2_000.0,
                )
                engine.analyzer.book_quotes.extend((
                    BookQuote(moment_ms - 18_000, 136.210, 136.590),
                    BookQuote(moment_ms - 9_000, 136.210, 136.590),
                    BookQuote(moment_ms, 136.210, 136.590),
                ))
                context = MakerDecisionContext(
                    reference_price=136.400,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=0.380,
                    bid_support_bonds=4_000.0,
                    ask_supply_bonds=2_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                assessment = MarketAssessment(
                    reference_price=136.400,
                    reference_low=136.210,
                    reference_high=136.590,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="stable",
                    state_score=0,
                    state_confidence=0.62,
                    recent_buy_bonds=0.0,
                    recent_sell_bonds=1_000.0,
                    midpoint_change=0.0,
                    short_ask_change=0.0,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("午间休市前上下文失效",),
                )
                order = engine._new_order(
                    account, tick, side="sell", kind="inventory_exit",
                    lot_id=base_lot.db_id, price=136.589,
                    quantity=1_000.0, queue_ahead=0.0,
                    target_price=136.589, persist=True,
                )
                order.stable_context_grace_eligible = True
                account.sell_orders[base_lot.db_id] = order
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )

                self.assertNotIn(base_lot.db_id, account.sell_orders)
            finally:
                store.close()

    def test_priority_v110_requires_repeated_two_sided_turn_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v110-pattern.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V110_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 10, 19, 54, tzinfo=SHANGHAI)
                moment_ms = int(moment.timestamp() * 1_000)
                tick = self._replay_tick(
                    moment, last=136.796, bid=136.671, ask=137.197,
                )

                def event(
                    seconds_ago: int, price: float, side: str,
                    bonds: float = 1_000.0,
                ) -> TradeEvidence:
                    return TradeEvidence(
                        market_ts_ms=moment_ms - seconds_ago * 1_000,
                        price=price,
                        bonds=bonds,
                        transactions=1,
                        side=side,
                    )

                valid = (
                    event(39, 137.198, "buy"),
                    event(36, 136.795, "sell"),
                    event(6, 137.198, "buy"),
                    event(3, 136.796, "sell"),
                )
                engine.analyzer.trade_evidence.extend(valid)
                self.assertAlmostEqual(
                    engine._repeated_two_sided_turn_replenishment_price(
                        tick, 137.196, PRIORITY_POLICY_V110_CANDIDATE,
                    ),
                    136.797,
                )

                rejected_cases = {
                    "only_three_runs_ending_high": (
                        event(50, 137.198, "buy"),
                        event(49, 137.197, "buy"),
                        event(40, 136.795, "sell"),
                        event(39, 136.796, "sell"),
                        event(20, 137.198, "buy"),
                        event(19, 137.197, "buy"),
                    ),
                    "single_event_each_side": (
                        event(10, 137.198, "buy", 2_000.0),
                        event(3, 136.796, "sell", 2_000.0),
                    ),
                    "upper_prints_not_same_cluster": (
                        event(39, 137.180, "buy"),
                        event(36, 136.795, "sell"),
                        event(6, 137.198, "buy"),
                        event(3, 136.796, "sell"),
                    ),
                    "latest_low_is_stale": (
                        event(55, 137.198, "buy"),
                        event(45, 136.795, "sell"),
                        event(40, 137.198, "buy"),
                        event(31, 136.796, "sell"),
                    ),
                }
                for label, evidence in rejected_cases.items():
                    with self.subTest(label=label):
                        engine.analyzer.trade_evidence.clear()
                        engine.analyzer.trade_evidence.extend(evidence)
                        self.assertIsNone(
                            engine._repeated_two_sided_turn_replenishment_price(
                                tick, 137.196,
                                PRIORITY_POLICY_V110_CANDIDATE,
                            )
                        )
            finally:
                store.close()

    def test_priority_v110_possible_rise_sells_base_and_plans_low_replenishment(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v110-replenish.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V110_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 10, 19, 54, tzinfo=SHANGHAI)
                moment_ms = int(moment.timestamp() * 1_000)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                engine.analyzer.trade_evidence.extend((
                    TradeEvidence(
                        moment_ms - 39_000, 137.198, 1_000.0, 1, "buy",
                    ),
                    TradeEvidence(
                        moment_ms - 36_000, 136.795, 1_000.0, 1, "sell",
                    ),
                    TradeEvidence(
                        moment_ms - 6_000, 137.198, 1_000.0, 1, "buy",
                    ),
                    TradeEvidence(
                        moment_ms - 3_000, 136.796, 1_000.0, 1, "sell",
                    ),
                ))
                tick = self._replay_tick(
                    moment, last=136.796, bid=136.671, ask=137.197,
                )
                context = MakerDecisionContext(
                    reference_price=137.050,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=137.197 - 136.671,
                    bid_support_bonds=1_000.0,
                    ask_supply_bonds=1_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                assessment = MarketAssessment(
                    reference_price=137.050,
                    reference_low=136.671,
                    reference_high=137.197,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="possible_rise",
                    state_score=1,
                    state_confidence=0.62,
                    recent_buy_bonds=2_000.0,
                    recent_sell_bonds=2_000.0,
                    midpoint_change=0.20,
                    short_ask_change=0.0,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("高低价格簇两轮交替",),
                )
                self.assertFalse(engine._base_high_sell_is_safe(
                    137.196, context, PRIORITY_POLICY_V19_CANDIDATE,
                    "possible_rise",
                    repeated_turn_replenishment_price=136.797,
                ))
                self.assertTrue(engine._base_high_sell_is_safe(
                    137.196, context, PRIORITY_POLICY_V110_CANDIDATE,
                    "possible_rise",
                    repeated_turn_replenishment_price=136.797,
                ))
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )

                sell_order = next(iter(account.sell_orders.values()))
                self.assertEqual(sell_order.limit_price, 137.196)
                self.assertEqual(
                    sell_order.repeated_turn_replenishment_price, 136.797,
                )
                metadata = json.loads(store.connection.execute(
                    "SELECT metadata_json FROM maker_paper_orders WHERE id=?",
                    (sell_order.db_id,),
                ).fetchone()[0])
                self.assertEqual(
                    metadata["repeated_turn_replenishment_price"], 136.797,
                )
                self.assertEqual(metadata["price_boundary_kind"], "sell_floor")
                self.assertEqual(metadata["price_boundary"], 136.977)
                self.assertEqual(sell_order.price_boundary, 136.977)

                engine._fill_sell(
                    account, tick, sell_order, 1_000.0,
                    tick.market_ts_ms * 1_000_000,
                    persist=True,
                )
                self.assertEqual(account.inventory, 0.0)
                self.assertEqual(
                    account.pending_repeated_turn_replenishment_price,
                    136.797,
                )

                lower_tick = self._replay_tick(
                    moment + timedelta(seconds=24),
                    last=136.671, bid=136.671, ask=136.997,
                )
                lower_context = replace(
                    context,
                    spread=136.997 - 136.671,
                    reference_price=137.000,
                )
                with patch.object(
                    engine, "_decision_context", return_value=lower_context,
                ):
                    engine._refresh_orders(
                        account, lower_tick, assessment, persist=True,
                    )
                self.assertIsNotNone(account.buy_order)
                self.assertEqual(account.buy_order.kind, "inventory_replenish")
                self.assertEqual(account.buy_order.limit_price, 136.672)
                engine._fill_buy(
                    account, lower_tick, account.buy_order, 1_000.0,
                    lower_tick.market_ts_ms * 1_000_000,
                    kind="inventory_replenish", target_price=None,
                    persist=True,
                )
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(
                    account.pending_repeated_turn_replenishment_price, 0.0,
                )
            finally:
                store.close()

    def test_priority_v110_rejects_breakout_and_stays_out_of_queue_branch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v110-guards.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V110_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 10, 19, 54, tzinfo=SHANGHAI)
                context = MakerDecisionContext(
                    reference_price=137.050,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=0.526,
                    bid_support_bonds=1_000.0,
                    ask_supply_bonds=1_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                for state in ("rising",):
                    with self.subTest(state=state):
                        self.assertFalse(engine._base_high_sell_is_safe(
                            137.196, context,
                            PRIORITY_POLICY_V110_CANDIDATE, state,
                            repeated_turn_replenishment_price=136.797,
                        ))
                strong_breakout = replace(
                    context,
                    breakout_support_price=137.000,
                    breakout_lower_sell_bonds=0.0,
                )
                self.assertFalse(engine._base_high_sell_is_safe(
                    137.196, strong_breakout,
                    PRIORITY_POLICY_V110_CANDIDATE, "possible_rise",
                    repeated_turn_replenishment_price=136.797,
                ))

                tick = self._replay_tick(
                    moment, last=136.796, bid=136.671, ask=137.197,
                )
                engine.analyzer.trade_evidence.extend((
                    TradeEvidence(
                        tick.market_ts_ms - 39_000,
                        137.198, 1_000.0, 1, "buy",
                    ),
                    TradeEvidence(
                        tick.market_ts_ms - 36_000,
                        136.795, 1_000.0, 1, "sell",
                    ),
                    TradeEvidence(
                        tick.market_ts_ms - 6_000,
                        137.198, 1_000.0, 1, "buy",
                    ),
                    TradeEvidence(
                        tick.market_ts_ms - 3_000,
                        136.796, 1_000.0, 1, "sell",
                    ),
                ))
                self.assertIsNone(
                    engine._repeated_two_sided_turn_replenishment_price(
                        tick, 137.196, QUEUE_POLICY_V13_CANDIDATE,
                    )
                )
                self.assertFalse(
                    QUEUE_POLICY_V13_CANDIDATE.enable_repeated_two_sided_base_turn
                )

                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                engine.last_confirmed_rise_trade_ts_ms = tick.market_ts_ms
                engine.last_confirmed_rise_price = 137.000
                assessment = MarketAssessment(
                    reference_price=137.050,
                    reference_low=136.671,
                    reference_high=137.197,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="possible_rise",
                    state_score=1,
                    state_confidence=0.62,
                    recent_buy_bonds=2_000.0,
                    recent_sell_bonds=2_000.0,
                    midpoint_change=0.20,
                    short_ask_change=0.0,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("仍有真实清档上涨确认",),
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )
                self.assertEqual(account.sell_orders, {})
            finally:
                store.close()

    def test_priority_v111_records_and_repeats_a_completed_base_turn(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v111-repeat.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V111_CANDIDATE,
                )
                first_high = datetime(
                    2026, 8, 14, 13, 34, 45, tzinfo=SHANGHAI,
                )
                engine._start_date(first_high.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                base_lot = next(
                    lot for lot in account.lots.values()
                    if lot.entry_price is None
                )
                high_tick = self._replay_tick(
                    first_high, last=135.999, bid=135.701, ask=135.999,
                    trade_bonds=2_000.0, inferred_side="buy",
                )
                sell_order = engine._new_order(
                    account, high_tick,
                    side="sell", kind="inventory_exit",
                    lot_id=base_lot.db_id, price=135.998,
                    quantity=1_000.0, queue_ahead=0.0,
                    target_price=135.998, persist=True,
                )
                account.sell_orders[base_lot.db_id] = sell_order
                engine._fill_sell(
                    account, high_tick, sell_order, 1_000.0,
                    high_tick.market_ts_ms * 1_000_000,
                    persist=True,
                )

                first_low = first_high + timedelta(seconds=9)
                low_tick = self._replay_tick(
                    first_low, last=135.700, bid=135.701, ask=135.999,
                    trade_bonds=2_000.0, inferred_side="sell",
                )
                buy_order = engine._new_order(
                    account, low_tick,
                    side="buy", kind="inventory_replenish",
                    lot_id=None, price=135.702,
                    quantity=1_000.0, queue_ahead=0.0,
                    target_price=None, persist=True,
                )
                account.buy_order = buy_order
                engine._fill_buy(
                    account, low_tick, buy_order, 1_000.0,
                    low_tick.market_ts_ms * 1_000_000,
                    kind="inventory_replenish", target_price=None,
                    persist=True,
                )
                self.assertEqual(
                    account.last_completed_base_turn_sell_price, 135.998,
                )
                self.assertEqual(
                    account.last_completed_base_turn_buy_price, 135.702,
                )
                self.assertEqual(
                    account.last_completed_base_turn_ts_ms,
                    low_tick.market_ts_ms,
                )

                repeat = datetime(2026, 8, 14, 13, 36, 6, tzinfo=SHANGHAI)
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    market_ts_ms=int(
                        datetime(
                            2026, 8, 14, 13, 36, 3, tzinfo=SHANGHAI,
                        ).timestamp() * 1_000
                    ),
                    price=135.714,
                    bonds=1_000.0,
                    transactions=1,
                    side="sell",
                ))
                repeat_tick = self._replay_tick(
                    repeat, last=135.714, bid=135.750, ask=135.999,
                )
                context = MakerDecisionContext(
                    reference_price=135.900,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=0.249,
                    bid_support_bonds=3_000.0,
                    ask_supply_bonds=2_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                assessment = MarketAssessment(
                    reference_price=135.900,
                    reference_low=135.750,
                    reference_high=135.999,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="possible_rise",
                    state_score=1,
                    state_confidence=0.62,
                    recent_buy_bonds=1_000.0,
                    recent_sell_bonds=1_000.0,
                    midpoint_change=0.12,
                    short_ask_change=0.0,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("刚完成的高低走廊仍在",),
                )
                self.assertEqual(
                    engine._recent_completed_base_turn_replenishment_price(
                        account, repeat_tick, 135.998,
                    ),
                    135.751,
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, repeat_tick, assessment, persist=True,
                    )
                repeated_sell = next(iter(account.sell_orders.values()))
                self.assertEqual(repeated_sell.limit_price, 135.998)
                self.assertEqual(
                    repeated_sell.repeated_turn_replenishment_price, 135.751,
                )

                lifted = self._replay_tick(
                    datetime(2026, 8, 14, 13, 36, 39, tzinfo=SHANGHAI),
                    last=135.714, bid=135.801, ask=135.999,
                )
                lifted_context = replace(context, spread=0.198)
                with patch.object(
                    engine, "_decision_context", return_value=lifted_context,
                ):
                    engine._refresh_orders(
                        account, lifted, assessment, persist=True,
                    )
                self.assertIs(
                    account.sell_orders[repeated_sell.lot_id], repeated_sell,
                )
                self.assertEqual(
                    repeated_sell.repeated_turn_replenishment_price, 135.802,
                )

                second_high = self._replay_tick(
                    datetime(2026, 8, 14, 13, 37, 3, tzinfo=SHANGHAI),
                    last=135.999, bid=135.801, ask=135.999,
                    trade_bonds=2_000.0, inferred_side="buy",
                )
                engine._process_resting_orders(
                    account, second_high, persist=True,
                    received_ts_ns=second_high.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 0.0)
                with patch.object(
                    engine, "_decision_context", return_value=lifted_context,
                ):
                    engine._refresh_orders(
                        account, second_high, assessment, persist=True,
                    )
                self.assertIsNotNone(account.buy_order)
                self.assertEqual(account.buy_order.limit_price, 135.802)

                second_low = self._replay_tick(
                    datetime(2026, 8, 14, 13, 37, 12, tzinfo=SHANGHAI),
                    last=135.800, bid=135.751, ask=135.999,
                    trade_bonds=2_000.0, inferred_side="sell",
                )
                engine._process_resting_orders(
                    account, second_low, persist=True,
                    received_ts_ns=second_low.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_000.0)
            finally:
                store.close()

    def test_priority_v111_completed_turn_memory_has_strict_boundaries(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v111-guards.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V111_CANDIDATE,
                    queue_policy=QUEUE_POLICY_V13_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 13, 36, 6, tzinfo=SHANGHAI)
                moment_ms = int(moment.timestamp() * 1_000)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.last_completed_base_turn_sell_price = 135.998
                account.last_completed_base_turn_buy_price = 135.702
                account.last_completed_base_turn_ts_ms = moment_ms - 72_000
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    market_ts_ms=moment_ms - 3_000,
                    price=135.714,
                    bonds=1_000.0,
                    transactions=1,
                    side="sell",
                ))
                tick = self._replay_tick(
                    moment, last=135.714, bid=135.750, ask=135.999,
                )
                self.assertEqual(
                    engine._recent_completed_base_turn_replenishment_price(
                        account, tick, 135.998,
                    ),
                    135.751,
                )

                account.last_completed_base_turn_ts_ms = moment_ms - 181_000
                self.assertIsNone(
                    engine._recent_completed_base_turn_replenishment_price(
                        account, tick, 135.998,
                    )
                )
                account.last_completed_base_turn_ts_ms = moment_ms - 72_000
                self.assertIsNone(
                    engine._recent_completed_base_turn_replenishment_price(
                        account, tick, 135.982,
                    )
                )
                drifted = self._replay_tick(
                    moment, last=135.714, bid=135.803, ask=135.999,
                )
                self.assertIsNone(
                    engine._recent_completed_base_turn_replenishment_price(
                        account, drifted, 135.998,
                    )
                )
                engine.analyzer.trade_evidence.clear()
                self.assertIsNone(
                    engine._recent_completed_base_turn_replenishment_price(
                        account, tick, 135.998,
                    )
                )

                queue = engine.accounts["maker_v01_queue"]
                queue.last_completed_base_turn_sell_price = 135.998
                queue.last_completed_base_turn_buy_price = 135.702
                queue.last_completed_base_turn_ts_ms = moment_ms - 72_000
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    market_ts_ms=moment_ms - 3_000,
                    price=135.714,
                    bonds=1_000.0,
                    transactions=1,
                    side="sell",
                ))
                self.assertIsNone(
                    engine._recent_completed_base_turn_replenishment_price(
                        queue, tick, 135.998,
                    )
                )
            finally:
                store.close()

    def test_priority_v112_retains_only_while_live_turn_corridor_survives(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v112-live-corridor.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V112_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 10, 46, 9, tzinfo=SHANGHAI)
                moment_ms = int(moment.timestamp() * 1_000)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                base_lot = next(
                    lot for lot in account.lots.values()
                    if lot.entry_price is None
                )
                tick = self._replay_tick(
                    moment, last=136.351, bid=136.352, ask=136.745,
                    bid_bonds=2_000.0, ask_bonds=1_000.0,
                )
                engine.analyzer.book_quotes.extend((
                    BookQuote(moment_ms - 18_000, 136.352, 136.745),
                    BookQuote(moment_ms - 9_000, 136.352, 136.745),
                    BookQuote(moment_ms, 136.352, 136.745),
                ))
                context = MakerDecisionContext(
                    reference_price=136.548,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=0.393,
                    bid_support_bonds=5_860.0,
                    ask_supply_bonds=1_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                assessment = MarketAssessment(
                    reference_price=136.548,
                    reference_low=136.352,
                    reference_high=136.745,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="stable",
                    state_score=0,
                    state_confidence=0.62,
                    recent_buy_bonds=1_140.0,
                    recent_sell_bonds=1_000.0,
                    midpoint_change=0.0,
                    short_ask_change=0.0,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("高低盘口走廊仍然逐帧存在",),
                )
                order = engine._new_order(
                    account, tick, side="sell", kind="inventory_exit",
                    lot_id=base_lot.db_id, price=136.744,
                    quantity=1_000.0, queue_ahead=0.0,
                    target_price=136.744, persist=True,
                )
                order.stable_context_grace_eligible = True
                account.sell_orders[base_lot.db_id] = order

                self.assertFalse(
                    PRIORITY_POLICY_V111_CANDIDATE
                        .retain_priority_base_turn_while_live_corridor
                )
                self.assertTrue(
                    PRIORITY_POLICY_V112_CANDIDATE
                        .retain_priority_base_turn_while_live_corridor
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )

                later = replace(
                    tick,
                    market_ts_ms=moment_ms + 90_000,
                    market_time=(moment + timedelta(seconds=90)).time().isoformat(
                        timespec="milliseconds"
                    ),
                )
                engine.analyzer.book_quotes.extend((
                    BookQuote(moment_ms + 72_000, 136.352, 136.745),
                    BookQuote(moment_ms + 81_000, 136.352, 136.745),
                    BookQuote(moment_ms + 90_000, 136.352, 136.745),
                ))
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, later, assessment, persist=True,
                    )
                self.assertIs(account.sell_orders[base_lot.db_id], order)

                # The candidate removes only the mechanical clock.  A lifted
                # bid that destroys the 0.18-yuan replenishment edge must
                # still cancel the old high-side order immediately.
                broken = replace(
                    later,
                    market_ts_ms=moment_ms + 93_000,
                    market_time=(moment + timedelta(seconds=93)).time().isoformat(
                        timespec="milliseconds"
                    ),
                    bids=((136.620, 2_000.0),),
                )
                broken_context = replace(context, spread=0.125)
                with patch.object(
                    engine, "_decision_context", return_value=broken_context,
                ):
                    engine._refresh_orders(
                        account, broken, assessment, persist=True,
                    )
                self.assertNotIn(base_lot.db_id, account.sell_orders)
                self.assertFalse(
                    QUEUE_POLICY_V13_CANDIDATE
                        .retain_priority_base_turn_while_live_corridor
                )
            finally:
                store.close()

    def test_priority_v113_keeps_existing_high_sale_when_lower_bid_reprices_down(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v113-lower-bid.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V113_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 11, 15, 0, tzinfo=SHANGHAI)
                moment_ms = int(moment.timestamp() * 1_000)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                base_lot = next(
                    lot for lot in account.lots.values()
                    if lot.entry_price is None
                )
                original = self._replay_tick(
                    moment, last=136.539, bid=136.558, ask=136.781,
                    bid_bonds=2_000.0, ask_bonds=2_000.0,
                )
                engine.analyzer.book_quotes.extend((
                    BookQuote(moment_ms - 18_000, 136.558, 136.781),
                    BookQuote(moment_ms - 9_000, 136.558, 136.781),
                    BookQuote(moment_ms, 136.558, 136.781),
                ))
                order = engine._new_order(
                    account, original, side="sell", kind="inventory_exit",
                    lot_id=base_lot.db_id, price=136.780,
                    quantity=1_000.0, queue_ahead=0.0,
                    target_price=136.780, persist=True,
                )
                order.stable_context_grace_eligible = True
                order.base_turn_replenishment_ceiling = 136.559
                account.sell_orders[base_lot.db_id] = order

                shifted = self._replay_tick(
                    moment + timedelta(seconds=6),
                    last=136.539, bid=136.520, ask=136.781,
                    bid_bonds=2_000.0, ask_bonds=2_000.0,
                )
                shifted_context = MakerDecisionContext(
                    reference_price=136.669,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=0.261,
                    bid_support_bonds=4_000.0,
                    ask_supply_bonds=6_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                shifted_assessment = MarketAssessment(
                    reference_price=136.669,
                    reference_low=136.520,
                    reference_high=136.781,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="possible_fall",
                    state_score=-1,
                    state_confidence=0.62,
                    recent_buy_bonds=1_000.0,
                    recent_sell_bonds=1_380.0,
                    midpoint_change=-0.01,
                    short_ask_change=0.0,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("买一向更低回补区移动，高侧卖价未变",),
                )
                with patch.object(
                    engine, "_decision_context", return_value=shifted_context,
                ):
                    engine._refresh_orders(
                        account, shifted, shifted_assessment, persist=True,
                    )
                self.assertIs(account.sell_orders[base_lot.db_id], order)
                self.assertTrue(order.retained_after_context_loss)
                self.assertFalse(
                    PRIORITY_POLICY_V112_CANDIDATE
                        .retain_priority_base_turn_on_lower_bid_shift
                )

                # Buying an extra lot changes which inventory may be exposed.
                # The retained base sale must step aside so one market print
                # cannot sell both the new T lot and the base before a fresh
                # assessment, preserving the existing extra-first rule.
                account.lots[-1] = MakerLot(
                    -1, "low_bid_reversion", shifted.market_ts_ms,
                    136.520, 1_000.0, 1_000.0,
                )
                self.assertFalse(
                    engine._retain_priority_base_turn_stable_context_grace(
                        account, base_lot, order, shifted,
                        shifted_assessment, shifted_context,
                    )
                )
                del account.lots[-1]

                # A higher bid is the opposite situation: it consumes the
                # planned replenishment edge and must retire the old order.
                lifted = replace(
                    shifted,
                    market_ts_ms=moment_ms + 9_000,
                    market_time=(moment + timedelta(seconds=9)).time().isoformat(
                        timespec="milliseconds"
                    ),
                    bids=((136.620, 2_000.0),),
                )
                lifted_context = replace(shifted_context, spread=0.161)
                with patch.object(
                    engine, "_decision_context", return_value=lifted_context,
                ):
                    engine._refresh_orders(
                        account, lifted, shifted_assessment, persist=True,
                    )
                self.assertNotIn(base_lot.db_id, account.sell_orders)
            finally:
                store.close()

    def test_two_bonds_have_independent_maker_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-multi.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                bond_codes=(base.qmt.bond_code, "132024.SH"),
                fill_modes=("priority", "queue"),
                realtime_comparison_model_ids=(
                    "maker_priority_v1_37_candidate",
                    "maker_queue_v1_13_candidate",
                ),
            ))
            store = SQLiteStore(config)
            processor = MarketProcessor(config, store, preload_m0_history=False)
            self.assertIsInstance(processor.maker_paper, MakerPaperPortfolio)
            start = datetime(2026, 8, 14, 10, 0, tzinfo=SHANGHAI)

            processor.process(make_tick(
                config.qmt.stock_code, start,
                last=28.0, bid=27.99, ask=28.01,
            ))
            self.assertEqual(
                len(processor.maker_paper.engines["132026.SH"].analyzer.stock_prices),
                1,
            )
            self.assertEqual(
                len(processor.maker_paper.engines["132024.SH"].analyzer.stock_prices),
                0,
            )
            processor.process(make_tick(
                "600362.SH", start,
                last=44.0, bid=43.99, ask=44.01,
            ))
            self.assertEqual(
                len(processor.maker_paper.engines["132026.SH"].analyzer.stock_prices),
                1,
            )
            self.assertEqual(
                len(processor.maker_paper.engines["132024.SH"].analyzer.stock_prices),
                1,
            )
            processor.process(make_tick(
                "132024.SH", start,
                last=136.8, bid=136.5, ask=136.8,
            ))

            accounts = processor.maker_paper.accounts
            self.assertEqual(set(accounts), {
                "maker_v01_priority", "maker_v01_queue",
                "maker_v01_priority_v1_37_candidate",
                "maker_v01_queue_v1_13_candidate",
                "maker_132024_v01_priority", "maker_132024_v01_queue",
                "maker_132024_v01_priority_v1_37_candidate",
                "maker_132024_v01_queue_v1_13_candidate",
            })
            self.assertEqual(accounts["maker_v01_priority"].last_bid, 0.0)
            self.assertEqual(
                accounts["maker_132024_v01_priority"].last_bid, 136.5
            )
            self.assertEqual(
                accounts[
                    "maker_132024_v01_priority_v1_37_candidate"
                ].last_bid,
                136.5,
            )
            self.assertEqual(
                accounts[
                    "maker_132024_v01_queue_v1_13_candidate"
                ].last_bid,
                136.5,
            )
            self.assertEqual(
                store.connection.execute(
                    "SELECT COUNT(*) FROM maker_paper_accounts"
                ).fetchone()[0],
                8,
            )
            assignments = {
                row["strategy_id"]: row["model_id"]
                for row in store.connection.execute(
                    "SELECT strategy_id,model_id "
                    "FROM maker_paper_model_assignments"
                )
            }
            self.assertEqual(
                assignments["maker_v01_priority_v1_37_candidate"],
                "maker_priority_v1_37_candidate",
            )
            self.assertEqual(
                assignments["maker_v01_queue_v1_13_candidate"],
                "maker_queue_v1_13_candidate",
            )
            summary = processor.maker_paper.runtime_summary()
            self.assertEqual(
                {account["bond_code"] for account in summary["accounts"]},
                {"132026.SH", "132024.SH"},
            )
            store.close()

    def test_first_position_v144_has_a_selectable_independent_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-v144-ledger.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                bond_codes=(base.qmt.bond_code,),
                underlying_stock_codes={
                    base.qmt.bond_code: base.qmt.stock_code,
                },
                fill_modes=(),
                realtime_comparison_model_ids=("maker_priority_v1_44",),
                super_windfall_enabled=False,
            ))
            store = SQLiteStore(config)
            try:
                portfolio = MakerPaperPortfolio(config, store)
                portfolio.rebuild_date("2026-08-24")
                self.assertEqual(
                    maker_strategy_ids(config, base.qmt.bond_code),
                    ("maker_v01_priority_v1_44",),
                )
                account = portfolio.accounts["maker_v01_priority_v1_44"]
                self.assertEqual(account.policy.model_id, "maker_priority_v1_44")
                self.assertEqual(account.policy.model_version, "1.44")
            finally:
                store.close()

    def test_live_matrix_uses_v137_v150_v22_v23_without_deleting_history(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-current-matrix.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                bond_codes=(base.qmt.bond_code, "132024.SH"),
                underlying_stock_codes={
                    base.qmt.bond_code: base.qmt.stock_code,
                    "132024.SH": "600362.SH",
                },
                fill_modes=(),
                realtime_comparison_model_ids=(
                    "maker_priority_v1_37_candidate",
                    "maker_priority_v1_50_candidate",
                    "maker_priority_v2_2_candidate",
                    "maker_priority_v2_3_candidate",
                    "maker_queue_v1_17_candidate",
                    "maker_queue_v1_18_candidate",
                ),
                super_windfall_enabled=True,
            ))
            store = SQLiteStore(config)
            try:
                legacy_config = replace(config, maker_paper=replace(
                    config.maker_paper,
                    fill_modes=("priority", "queue"),
                    realtime_comparison_model_ids=(
                        "maker_priority_v1_37_candidate",
                        "maker_priority_v1_43_candidate",
                        "maker_queue_v1_17_candidate",
                        "maker_queue_v1_18_candidate",
                    ),
                    super_windfall_enabled=True,
                ))
                MakerPaperPortfolio(legacy_config, store).rebuild_date(
                    "2026-08-24"
                )
                previous_v144_config = replace(
                    config,
                    maker_paper=replace(
                        config.maker_paper,
                        realtime_comparison_model_ids=(
                            "maker_priority_v1_37_candidate",
                            "maker_priority_v1_44",
                            "maker_queue_v1_17_candidate",
                            "maker_queue_v1_18_candidate",
                        ),
                    ),
                )
                MakerPaperPortfolio(previous_v144_config, store).rebuild_date(
                    "2026-08-24"
                )
                previous_v145_config = replace(
                    config,
                    maker_paper=replace(
                        config.maker_paper,
                        realtime_comparison_model_ids=(
                            "maker_priority_v1_37_candidate",
                            "maker_priority_v1_45",
                            "maker_queue_v1_17_candidate",
                            "maker_queue_v1_18_candidate",
                        ),
                    ),
                )
                MakerPaperPortfolio(previous_v145_config, store).rebuild_date(
                    "2026-08-24"
                )
                previous_v146_config = replace(
                    config,
                    maker_paper=replace(
                        config.maker_paper,
                        realtime_comparison_model_ids=(
                            "maker_priority_v1_37_candidate",
                            "maker_priority_v1_46",
                            "maker_queue_v1_17_candidate",
                            "maker_queue_v1_18_candidate",
                        ),
                    ),
                )
                MakerPaperPortfolio(previous_v146_config, store).rebuild_date(
                    "2026-08-24"
                )
                previous_v148_config = replace(
                    config,
                    maker_paper=replace(
                        config.maker_paper,
                        realtime_comparison_model_ids=(
                            "maker_priority_v1_37_candidate",
                            "maker_priority_v1_47",
                            "maker_priority_v1_48_candidate",
                            "maker_queue_v1_17_candidate",
                            "maker_queue_v1_18_candidate",
                        ),
                    ),
                )
                MakerPaperPortfolio(previous_v148_config, store).rebuild_date(
                    "2026-08-24"
                )
                previous_v149_config = replace(
                    config,
                    maker_paper=replace(
                        config.maker_paper,
                        realtime_comparison_model_ids=(
                            "maker_priority_v1_37_candidate",
                            "maker_priority_v1_49_candidate_r2",
                            "maker_queue_v1_17_candidate",
                            "maker_queue_v1_18_candidate",
                        ),
                    ),
                )
                MakerPaperPortfolio(previous_v149_config, store).rebuild_date(
                    "2026-08-24"
                )
                previous_v21_config = replace(
                    config,
                    maker_paper=replace(
                        config.maker_paper,
                        realtime_comparison_model_ids=(
                            "maker_priority_v1_37_candidate",
                            "maker_priority_v2_1_candidate",
                            "maker_queue_v1_17_candidate",
                            "maker_queue_v1_18_candidate",
                        ),
                    ),
                )
                MakerPaperPortfolio(previous_v21_config, store).rebuild_date(
                    "2026-08-24"
                )

                portfolio = MakerPaperPortfolio(config, store)
                portfolio.rebuild_date("2026-08-24")

                expected = {
                    "maker_v01_super_windfall",
                    "maker_v01_priority_v1_37_candidate",
                    "maker_v01_priority_v1_50_candidate",
                    "maker_v01_priority_v2_2_candidate",
                    "maker_v01_priority_v2_3_candidate",
                    "maker_v01_queue_v1_17_candidate",
                    "maker_v01_queue_v1_18_candidate",
                    "maker_132024_v01_super_windfall",
                    "maker_132024_v01_priority_v1_37_candidate",
                    "maker_132024_v01_priority_v1_50_candidate",
                    "maker_132024_v01_priority_v2_2_candidate",
                    "maker_132024_v01_priority_v2_3_candidate",
                    "maker_132024_v01_queue_v1_17_candidate",
                    "maker_132024_v01_queue_v1_18_candidate",
                }
                self.assertEqual(set(portfolio.accounts), expected)
                self.assertNotIn("maker_v01_priority", portfolio.accounts)
                self.assertNotIn("maker_v01_queue", portfolio.accounts)
                self.assertNotIn(
                    "maker_v01_priority_v1_43_candidate",
                    portfolio.accounts,
                )
                self.assertNotIn(
                    "maker_v01_priority_v1_44",
                    portfolio.accounts,
                )
                self.assertEqual(
                    maker_strategy_ids(config, base.qmt.bond_code),
                    (
                        "maker_v01_super_windfall",
                        "maker_v01_priority_v1_37_candidate",
                        "maker_v01_priority_v1_50_candidate",
                        "maker_v01_priority_v2_2_candidate",
                        "maker_v01_priority_v2_3_candidate",
                        "maker_v01_queue_v1_17_candidate",
                        "maker_v01_queue_v1_18_candidate",
                    ),
                )
                assignments = {
                    row["strategy_id"]: row["model_id"]
                    for row in store.connection.execute(
                        "SELECT strategy_id,model_id "
                        "FROM maker_paper_model_assignments"
                    )
                }
                self.assertEqual(
                    set(assignments),
                    expected | {
                        "maker_v01_priority", "maker_v01_queue",
                        "maker_132024_v01_priority",
                        "maker_132024_v01_queue",
                        "maker_v01_priority_v1_43_candidate",
                        "maker_132024_v01_priority_v1_43_candidate",
                        "maker_v01_priority_v1_44",
                        "maker_132024_v01_priority_v1_44",
                        "maker_v01_priority_v1_45",
                        "maker_132024_v01_priority_v1_45",
                        "maker_v01_priority_v1_46",
                        "maker_132024_v01_priority_v1_46",
                        "maker_v01_priority_v1_47",
                        "maker_132024_v01_priority_v1_47",
                        "maker_v01_priority_v1_48_candidate",
                        "maker_132024_v01_priority_v1_48_candidate",
                        "maker_v01_priority_v1_49_candidate_r2",
                        "maker_132024_v01_priority_v1_49_candidate_r2",
                        "maker_v01_priority_v2_1_candidate",
                        "maker_132024_v01_priority_v2_1_candidate",
                    },
                )
                self.assertEqual(
                    assignments["maker_v01_priority_v1_43_candidate"],
                    "maker_priority_v1_43_candidate",
                )
                self.assertEqual(
                    assignments["maker_v01_priority_v1_44"],
                    "maker_priority_v1_44",
                )
                self.assertEqual(
                    assignments["maker_v01_priority_v1_45"],
                    "maker_priority_v1_45",
                )
                self.assertEqual(
                    assignments["maker_v01_priority_v1_46"],
                    "maker_priority_v1_46",
                )
                self.assertEqual(
                    assignments["maker_v01_priority_v1_47"],
                    "maker_priority_v1_47",
                )
                self.assertEqual(
                    assignments["maker_v01_priority_v1_48_candidate"],
                    "maker_priority_v1_48_candidate",
                )
                self.assertEqual(
                    assignments["maker_v01_priority_v1_49_candidate_r2"],
                    "maker_priority_v1_49_candidate_r2",
                )
                self.assertEqual(
                    assignments["maker_v01_priority_v2_1_candidate"],
                    "maker_priority_v2_1_candidate",
                )
                self.assertEqual(
                    assignments["maker_v01_priority_v1_50_candidate"],
                    "maker_priority_v1_50_candidate",
                )
                self.assertEqual(
                    assignments["maker_v01_priority_v2_2_candidate"],
                    "maker_priority_v2_2_candidate",
                )
                self.assertEqual(
                    assignments["maker_v01_priority_v2_3_candidate"],
                    "maker_priority_v2_3_candidate",
                )
                self.assertEqual(
                    assignments["maker_v01_queue_v1_18_candidate"],
                    "maker_queue_v1_18_candidate",
                )
                self.assertEqual(
                    assignments["maker_v01_priority"],
                    "maker_priority_v1_1",
                )
            finally:
                store.close()

    def test_live_matrix_replaces_v25_with_v251_r2_without_deleting_history(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-v251-r2-matrix.sqlite3")
            v25_config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                bond_codes=(base.qmt.bond_code,),
                underlying_stock_codes={
                    base.qmt.bond_code: base.qmt.stock_code,
                },
                fill_modes=(),
                realtime_comparison_model_ids=(
                    "maker_priority_v2_5_candidate",
                ),
                super_windfall_enabled=False,
            ))
            store = SQLiteStore(v25_config)
            try:
                MakerPaperPortfolio(v25_config, store).rebuild_date(
                    "2026-08-28"
                )
                current_config = replace(
                    v25_config,
                    maker_paper=replace(
                        v25_config.maker_paper,
                        realtime_comparison_model_ids=(
                            "maker_priority_v2_51_candidate_r2",
                        ),
                    ),
                )
                portfolio = MakerPaperPortfolio(current_config, store)
                portfolio.rebuild_date("2026-08-28")

                self.assertEqual(
                    set(portfolio.accounts),
                    {"maker_v01_priority_v2_51_candidate_r2"},
                )
                assignments = {
                    row["strategy_id"]: (row["model_id"], row["parent_model_id"])
                    for row in store.connection.execute(
                        "SELECT strategy_id,model_id,parent_model_id "
                        "FROM maker_paper_model_assignments "
                        "WHERE market_date = '2026-08-28'"
                    )
                }
                self.assertEqual(
                    assignments["maker_v01_priority_v2_5_candidate"],
                    ("maker_priority_v2_5_candidate", "maker_priority_v2_4_candidate"),
                )
                self.assertEqual(
                    assignments["maker_v01_priority_v2_51_candidate_r2"],
                    (
                        "maker_priority_v2_51_candidate_r2",
                        "maker_priority_v2_51_candidate",
                    ),
                )
            finally:
                store.close()

    def test_realtime_candidate_fills_persist_and_rebuild_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-realtime.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                bond_codes=(base.qmt.bond_code, "132024.SH"),
                fill_modes=("priority", "queue"),
                realtime_comparison_model_ids=(
                    "maker_priority_v1_37_candidate",
                    "maker_queue_v1_13_candidate",
                ),
            ))
            store = SQLiteStore(config)
            processor = MarketProcessor(config, store, preload_m0_history=False)
            start = datetime(2026, 8, 13, 10, 0, tzinfo=SHANGHAI)

            processor.process(make_tick(
                config.qmt.stock_code, start,
                last=28.0, bid=27.99, ask=28.01,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, start,
                last=137.0, bid=136.8, ask=137.0,
                volume=100, amount=137_000, transactions=1,
                level_volume=100,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=3),
                last=137.0, bid=136.8, ask=137.0,
                volume=700, amount=959_000, transactions=2,
                level_volume=100,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=6),
                last=137.0, bid=136.4, ask=137.0,
                volume=700, amount=959_000, transactions=2,
                level_volume=100,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=9),
                last=136.401, bid=136.4, ask=137.0,
                volume=800, amount=1_095_401, transactions=3,
                level_volume=100,
            ))

            strategy_id = "maker_v01_priority_v1_37_candidate"
            candidate = processor.maker_paper.accounts[strategy_id]
            self.assertEqual(candidate.inventory, 2_000)
            before = [
                tuple(row)
                for row in store.connection.execute(
                    "SELECT market_ts_ms,side,price,quantity,fill_reason,"
                    "reference_tick_id,cash_after,inventory_after "
                    "FROM maker_paper_fills WHERE strategy_id=? ORDER BY id",
                    (strategy_id,),
                )
            ]
            self.assertGreater(len(before), 0)

            processor.maker_paper.rebuild_date(start.date())
            after = [
                tuple(row)
                for row in store.connection.execute(
                    "SELECT market_ts_ms,side,price,quantity,fill_reason,"
                    "reference_tick_id,cash_after,inventory_after "
                    "FROM maker_paper_fills WHERE strategy_id=? ORDER BY id",
                    (strategy_id,),
                )
            ]
            self.assertEqual(after, before)
            self.assertEqual(
                processor.maker_paper.accounts[strategy_id].inventory,
                2_000,
            )
            store.close()

    def test_public_opening_caution_starts_at_0920_and_requires_one_yuan(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-opening-caution.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                additional_buying_capacity_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority", "queue"),
                super_windfall_enabled=True,
            ))
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(config, store)

                before_start = datetime(
                    2026, 8, 21, 9, 19, 59, tzinfo=SHANGHAI,
                )
                engine.on_replay_tick(self._replay_tick(
                    before_start,
                    last=136.000,
                    bid=133.900,
                    ask=138.100,
                    previous_close=136.000,
                    bid_bonds=5_000,
                ), persist=True)
                for account in engine.accounts.values():
                    self.assertIsNone(account.buy_order)
                    self.assertEqual(account.sell_orders, {})

                # A few tenths of ordinary edge is deliberately ignored in the
                # volatile 09:20--09:30 opening window on both execution branches.
                cautious_small = before_start.replace(
                    hour=9, minute=20, second=0,
                )
                engine.on_replay_tick(self._replay_tick(
                    cautious_small,
                    last=136.000,
                    bid=135.600,
                    ask=136.400,
                    previous_close=136.000,
                    bid_bonds=5_000,
                ), persist=True)
                for account in engine._standard_accounts():
                    self.assertIsNone(account.buy_order)
                    self.assertEqual(account.sell_orders, {})

                # At least one yuan of causal room permits both ordinary sides;
                # Windfall also starts at 09:20 and keeps its stricter 1.50 edge.
                cautious_wide = cautious_small + timedelta(seconds=3)
                engine.on_replay_tick(self._replay_tick(
                    cautious_wide,
                    last=136.000,
                    bid=134.000,
                    ask=138.000,
                    previous_close=136.000,
                    bid_bonds=5_000,
                ), persist=True)
                for account in engine._standard_accounts():
                    self.assertIsNotNone(account.buy_order)
                    self.assertTrue(account.sell_orders)
                windfall = engine.accounts["maker_v01_super_windfall"]
                self.assertIsNotNone(windfall.buy_order)
            finally:
                store.close()

    def test_public_opening_caution_ends_at_0930_and_preserves_old_dates(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-opening-boundaries.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                additional_buying_capacity_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority", "queue"),
            ))
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(config, store)
                normal_open = datetime(
                    2026, 8, 21, 9, 30, 0, tzinfo=SHANGHAI,
                )
                engine.on_replay_tick(self._replay_tick(
                    normal_open,
                    last=136.000,
                    bid=135.600,
                    ask=137.100,
                    previous_close=136.000,
                    bid_bonds=5_000,
                ), persist=True)
                for account in engine._standard_accounts():
                    self.assertIsNotNone(account.buy_order)
                    self.assertTrue(account.sell_orders)

                historical = MakerPaperEngine(config, store)
                old_open = datetime(
                    2026, 8, 20, 9, 25, 0, tzinfo=SHANGHAI,
                )
                historical.on_replay_tick(self._replay_tick(
                    old_open,
                    last=136.000,
                    bid=135.600,
                    ask=137.100,
                    previous_close=136.000,
                    bid_bonds=5_000,
                ), persist=False)
                for account in historical._standard_accounts():
                    self.assertIsNone(account.buy_order)
                    self.assertTrue(account.sell_orders)
            finally:
                store.close()

    def test_public_opening_caution_applies_to_active_sweep_edge(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-opening-sweep.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                additional_buying_capacity_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(config, store)
                moment = datetime(
                    2026, 8, 21, 9, 25, 0, tzinfo=SHANGHAI,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                tick = self._replay_tick(
                    moment,
                    last=135.500,
                    bid=135.400,
                    ask=135.500,
                    previous_close=136.000,
                )
                anchor = AnchorState(
                    support_price=135.500,
                    exit_price=136.000,
                    band_midpoint=135.750,
                    reference_price=136.000,
                    confidence=1.0,
                    buy_effective_bonds=5_000,
                    sell_effective_bonds=0,
                    downside_pressure=0,
                    stock_return_5m=0,
                    stock_factor=1.0,
                    buy_clusters=(),
                    sell_reference_price=None,
                )
                narrow = Opportunity(
                    kind="sweep_tail",
                    signal_ts_ms=tick.market_ts_ms,
                    market_time=tick.market_time,
                    entry_price=135.500,
                    quantity_bonds=1_000,
                    target_exit_price=136.000,
                    priority_exit_price=136.000,
                    theoretical_edge=0.500,
                    anchor=anchor,
                )
                engine._active_sweep(account, tick, narrow, persist=True)
                self.assertEqual(account.inventory, 1_000)

                safe = replace(
                    narrow,
                    target_exit_price=136.600,
                    priority_exit_price=136.600,
                    theoretical_edge=1.100,
                )
                engine._active_sweep(account, tick, safe, persist=True)
                self.assertEqual(account.inventory, 2_000)
            finally:
                store.close()

    @staticmethod
    def _replay_tick(
        moment: datetime, *, last: float, bid: float, ask: float,
        trade_bonds: float = 0.0, inferred_side: str = "none",
        previous_close: float = 136.867, ask_bonds: float = 1_000.0,
        bid_bonds: float = 1_000.0,
    ) -> ReplayTick:
        return ReplayTick(
            tick_id=int(moment.timestamp()),
            code="132026.SH",
            market_ts_ms=int(moment.timestamp() * 1000),
            market_date=moment.date().isoformat(),
            market_time=moment.time().isoformat(timespec="milliseconds"),
            last_price=last,
            bids=((bid, bid_bonds),),
            asks=((ask, ask_bonds),),
            trade_bonds=trade_bonds,
            transaction_delta=1 if trade_bonds else 0,
            inferred_side=inferred_side,
            side_confidence="high" if trade_bonds else "none",
            previous_close=previous_close,
        )

    def _seed_sweep_base_recovery(self, database: Path, policy):
        config = test_config(database)
        store = SQLiteStore(config)
        engine = MakerPaperEngine(config, store, priority_policy=policy)
        sale_time = datetime(2026, 8, 14, 13, 43, 0, tzinfo=SHANGHAI)
        engine._start_date(sale_time.date().isoformat())
        account = engine.accounts["maker_v01_priority"]
        base_lot = next(
            lot for lot in account.lots.values() if lot.entry_price is None
        )
        sale_tick = self._replay_tick(
            sale_time, last=135.999, bid=135.800, ask=135.999,
        )
        sale_order = engine._new_order(
            account, sale_tick, side="sell", kind="inventory_exit",
            lot_id=base_lot.db_id, price=135.998, quantity=1_000.0,
            queue_ahead=0.0, target_price=135.998, persist=True,
        )
        engine._fill_sell(
            account, sale_tick, sale_order, 1_000.0,
            sale_tick.market_ts_ms * 1_000_000, persist=True,
        )

        sweep_time = datetime(2026, 8, 14, 13, 44, 12, tzinfo=SHANGHAI)
        sweep_tick = self._replay_tick(
            sweep_time, last=136.000, bid=135.999, ask=136.000,
        )
        sweep_order = engine._new_order(
            account, sweep_tick, side="buy", kind="sweep_tail", lot_id=None,
            price=136.000, quantity=1_000.0, queue_ahead=0.0,
            target_price=137.195, persist=True,
        )
        engine._fill_buy(
            account, sweep_tick, sweep_order, 1_000.0,
            sweep_tick.market_ts_ms * 1_000_000, kind="sweep_tail",
            target_price=137.195, persist=True, reason="active_tail_sweep",
        )
        recovered_lot = next(iter(account.lots.values()))
        return store, engine, account, sweep_tick, recovered_lot

    @staticmethod
    def _sweep_recovery_context() -> MakerDecisionContext:
        return MakerDecisionContext(
            reference_price=136.922,
            reference_source="previous_close",
            reliable_anchor=False,
            spread=1.196,
            bid_support_bonds=1_000.0,
            ask_supply_bonds=8_000.0,
            wall_threshold_bonds=5_000.0,
        )

    @staticmethod
    def _sweep_recovery_assessment() -> MarketAssessment:
        return MarketAssessment(
            reference_price=136.922,
            reference_low=136.000,
            reference_high=137.196,
            reference_source="previous_close",
            reference_confidence=0.25,
            state="stable",
            state_score=0,
            state_confidence=0.50,
            recent_buy_bonds=2_000.0,
            recent_sell_bonds=1_000.0,
            midpoint_change=0.0,
            short_ask_change=0.0,
            largest_ask_gap=1.196,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=("扫尾上档仍是当前卖一",),
        )

    def test_opening_previous_close_and_deep_discount_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-open.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority", "queue"),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 13, 9, 30, tzinfo=SHANGHAI)

            # The opening ask is already 1.08 yuan above yesterday's close,
            # so both fill assumptions must have a sell resting before 137.950 trades.
            engine.on_replay_tick(self._replay_tick(
                start, last=136.867, bid=137.400, ask=137.949,
            ), persist=True)
            self.assertEqual(
                next(iter(
                    engine.accounts["maker_v01_priority"].sell_orders.values()
                )).limit_price,
                137.948,
            )
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=3), last=136.867,
                bid=137.400, ask=137.948,
            ), persist=True)
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=21), last=137.950,
                bid=137.400, ask=137.950, trade_bonds=2_000,
                inferred_side="buy", ask_bonds=5_000,
            ), persist=True)
            for account in engine.accounts.values():
                self.assertEqual(account.inventory, 0)

            # A displayed 135.200 ask is actively bought. A one-tick repeat is
            # not counted again; 135.114 is a distinct lower opportunity.
            t = start.replace(hour=9, minute=51, second=30)
            engine.on_replay_tick(self._replay_tick(
                t, last=137.950, bid=135.100, ask=135.200,
            ), persist=True)
            for account in engine.accounts.values():
                self.assertEqual(account.inventory, 1_000)
            engine.on_replay_tick(self._replay_tick(
                t + timedelta(seconds=3), last=137.950,
                bid=135.100, ask=135.199,
            ), persist=True)
            for account in engine.accounts.values():
                self.assertEqual(account.inventory, 1_000)
            engine.on_replay_tick(self._replay_tick(
                t + timedelta(seconds=15), last=135.200,
                bid=135.000, ask=135.114, trade_bonds=1_000,
                inferred_side="sell",
            ), persist=True)
            for account in engine.accounts.values():
                self.assertEqual(account.inventory, 2_000)

            fills = store.connection.execute(
                """SELECT strategy_id,side,price,fill_reason
                   FROM maker_paper_fills ORDER BY id"""
            ).fetchall()
            active_prices = [
                round(float(row["price"]), 3) for row in fills
                if row["fill_reason"] == "active_deep_discount"
            ]
            self.assertEqual(active_prices, [135.2, 135.2, 135.114, 135.114])

            # Exit quotes must follow the current ask instead of keeping the
            # historical anchor that existed when the lot was bought.
            engine.on_replay_tick(self._replay_tick(
                t + timedelta(seconds=16), last=135.200,
                bid=136.500, ask=136.800, ask_bonds=6_000,
            ), persist=True)
            self.assertEqual(
                {order.limit_price for order in
                 engine.accounts["maker_v01_priority"].sell_orders.values()},
                {136.799},
            )
            self.assertEqual(
                {order.limit_price for order in
                 engine.accounts["maker_v01_queue"].sell_orders.values()},
                {136.8},
            )
            engine.on_replay_tick(self._replay_tick(
                t + timedelta(seconds=17), last=135.200,
                bid=135.000, ask=135.150, ask_bonds=0,
            ), persist=True)
            for account in engine.accounts.values():
                self.assertEqual(account.sell_orders, {})

            # A later one-yuan downward repricing is classified as falling.
            # Priority 1.1 must not let the old 137.284 sale manufacture a
            # "cheap" buy. Queue 1.0 deliberately preserves the 2026-08-13
            # baseline behavior until that branch is upgraded separately.
            engine.on_replay_tick(self._replay_tick(
                t + timedelta(seconds=18), last=135.200,
                bid=137.000, ask=137.284, ask_bonds=0,
            ), persist=True)
            engine.on_replay_tick(self._replay_tick(
                t + timedelta(seconds=24), last=137.284,
                bid=137.000, ask=137.284, trade_bonds=1_000,
                inferred_side="buy", ask_bonds=0,
            ), persist=True)
            self.assertEqual(
                engine.accounts["maker_v01_priority"].inventory, 1_000,
            )
            self.assertEqual(
                engine.accounts["maker_v01_queue"].inventory, 1_000,
            )
            later = start.replace(hour=10, minute=7, second=45)
            engine.on_replay_tick(self._replay_tick(
                later, last=136.210, bid=136.000, ask=136.199,
                trade_bonds=3_000, inferred_side="sell",
            ), persist=True)
            self.assertEqual(
                engine.accounts["maker_v01_priority"].inventory, 1_000,
            )
            self.assertEqual(
                engine.accounts["maker_v01_queue"].inventory, 2_000,
            )
            active_prices = [
                round(float(row["price"]), 3)
                for row in store.connection.execute(
                    """SELECT price FROM maker_paper_fills
                       WHERE fill_reason='active_deep_discount' ORDER BY id"""
                )
            ]
            self.assertEqual(
                active_prices,
                [135.2, 135.2, 135.114, 135.114, 136.199],
            )

            assignments = {
                row["strategy_id"]: row["model_id"]
                for row in store.connection.execute(
                    "SELECT strategy_id,model_id "
                    "FROM maker_paper_model_assignments"
                )
            }
            self.assertEqual(
                assignments["maker_v01_priority"], "maker_priority_v1_1",
            )
            self.assertEqual(
                assignments["maker_v01_queue"], "maker_queue_v1_0",
            )

            store.close()

    def test_simulated_limit_prices_are_floored_to_exchange_tick(self) -> None:
        self.assertEqual(_floor_to_tick(137.291064, 0.001), 137.291)
        self.assertEqual(_floor_to_tick(136.999999, 0.001), 136.999)

    def test_priority_account_rolls_inventory_without_broker_orders(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-paper.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            processor = MarketProcessor(config, store, preload_m0_history=False)
            start = datetime(2026, 8, 13, 10, 0, tzinfo=SHANGHAI)

            # Establish a 137.000 active-buy anchor with 6,000 bonds.
            processor.process(make_tick(
                config.qmt.stock_code, start,
                last=28.0, bid=27.99, ask=28.01,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, start,
                last=137.0, bid=136.8, ask=137.0,
                volume=100, amount=137_000, transactions=1,
                level_volume=100,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=3),
                last=137.0, bid=136.8, ask=137.0,
                volume=700, amount=959_000, transactions=2,
                level_volume=100,
            ))

            # A low bid creates a priority buy at 136.401; the next sell fills it.
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=6),
                last=137.0, bid=136.4, ask=137.0,
                volume=700, amount=959_000, transactions=2,
                level_volume=100,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=9),
                last=136.401, bid=136.4, ask=137.0,
                volume=800, amount=1_095_401, transactions=3,
                level_volume=100,
            ))

            account = processor.maker_paper.accounts["maker_v01_priority"]
            self.assertEqual(account.inventory, 2_000)
            self.assertGreaterEqual(account.cash, 0)
            self.assertEqual(
                store.connection.execute(
                    "SELECT COUNT(*) FROM maker_paper_fills WHERE side='buy'"
                ).fetchone()[0],
                1,
            )

            # An active buy at the maker exits consumes both 1,000-bond sell lots.
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=12),
                last=137.0, bid=136.8, ask=137.0,
                volume=1_000, amount=1_369_401, transactions=5,
                level_volume=200,
            ))
            self.assertLessEqual(account.inventory, 2_000)
            self.assertGreater(
                store.connection.execute(
                    "SELECT COUNT(*) FROM maker_paper_fills WHERE side='sell'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                store.connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'xttrader%'"
                ).fetchone()[0],
                0,
            )
            store.close()

    def test_sold_base_inventory_keeps_an_exact_replenishment_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-replenish.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("queue",),
            ))
            store = SQLiteStore(config)
            processor = MarketProcessor(config, store, preload_m0_history=False)
            start = datetime(2026, 8, 13, 10, 0, tzinfo=SHANGHAI)

            processor.process(make_tick(
                config.qmt.stock_code, start,
                last=28.0, bid=27.99, ask=28.01,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, start,
                last=137.0, bid=136.8, ask=137.0,
                volume=100, amount=137_000, transactions=1,
                level_volume=100,
            ))
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=3),
                last=137.0, bid=136.8, ask=137.0,
                volume=700, amount=959_000, transactions=2,
                level_volume=100,
            ))

            account = processor.maker_paper.accounts["maker_v01_queue"]
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=4),
                last=137.0, bid=136.95, ask=137.3,
                volume=700, amount=959_000, transactions=2,
                level_volume=600,
            ))
            base_order = next(iter(account.sell_orders.values()))
            sell_time = start + timedelta(seconds=5)
            sell_tick = ReplayTick(
                tick_id=999,
                code=config.qmt.bond_code,
                market_ts_ms=int(sell_time.timestamp() * 1000),
                market_date=sell_time.date().isoformat(),
                market_time=sell_time.time().isoformat(timespec="milliseconds"),
                last_price=base_order.limit_price,
                bids=((136.95, 1_000),),
                asks=((base_order.limit_price, 1_000),),
                trade_bonds=960,
                transaction_delta=1,
                inferred_side="buy",
                side_confidence="high",
            )
            processor.maker_paper._fill_sell(
                account, sell_tick, base_order, 960,
                int(sell_time.timestamp() * 1_000_000_000),
                persist=True,
            )

            # Refresh on a valid book whose normal entry edge is below 0.20.
            # Replenishment must still quote exactly the 960-bond deficit.
            processor.process(make_tick(
                config.qmt.bond_code, start + timedelta(seconds=6),
                last=137.0, bid=136.95, ask=137.0,
                volume=700, amount=959_000, transactions=2,
                level_volume=100,
            ))
            self.assertEqual(account.inventory, 40)
            self.assertIsNotNone(account.buy_order)
            self.assertEqual(account.buy_order.kind, "inventory_replenish")
            self.assertEqual(account.buy_order.quantity, 960)
            self.assertEqual(account.buy_order.limit_price, 136.95)
            self.assertEqual(account.replenishment_quantity, 960)
            # The restored quantity is base inventory again, so it must not
            # carry the historical replenishment entry as an exit constraint.
            base_lots = [
                lot for lot in account.lots.values() if lot.kind == "base"
            ]
            self.assertTrue(base_lots)
            self.assertTrue(all(lot.entry_price is None for lot in base_lots))
            store.close()

    def test_small_edge_without_book_support_does_not_quote_buy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-thin-book.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            moment = datetime(2026, 8, 13, 10, 0, tzinfo=SHANGHAI)
            engine.on_replay_tick(self._replay_tick(
                moment, last=136.867, bid=136.600, ask=136.850,
                trade_bonds=1_000, inferred_side="sell",
                previous_close=136.867, ask_bonds=1_000,
            ), persist=True)
            account = engine.accounts["maker_v01_priority"]
            self.assertIsNone(account.buy_order)
            self.assertEqual(account.inventory, 1_000)
            engine.on_replay_tick(self._replay_tick(
                moment + timedelta(seconds=3),
                last=136.867, bid=136.600, ask=136.850,
                previous_close=136.867, bid_bonds=6_000,
            ), persist=True)
            self.assertIsNotNone(account.buy_order)
            self.assertEqual(account.buy_order.limit_price, 136.601)

            # A visible wall is a current execution cushion, not permanent
            # support.  Once it disappears, the same moderate edge is no
            # longer safe and the passive bid must be withdrawn.
            engine.on_replay_tick(self._replay_tick(
                moment + timedelta(seconds=6),
                last=136.867, bid=136.600, ask=136.850,
                previous_close=136.867, bid_bonds=1_000,
            ), persist=True)
            self.assertIsNone(account.buy_order)
            store.close()

    def test_persistent_early_book_allows_current_ask_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-early-book.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 14, 9, 40, 30, tzinfo=SHANGHAI)

            # The opening trade anchor is still weak, but the 136.003/136.687
            # market remains stable after actual selling begins.  This local
            # band must replace yesterday's close as the causal early reference.
            for seconds in range(0, 28, 3):
                trade_bonds = (
                    380 if seconds == 0 else 1_000 if seconds == 27 else 0
                )
                engine.on_replay_tick(self._replay_tick(
                    start + timedelta(seconds=seconds),
                    last=136.003 if trade_bonds else 136.835,
                    bid=136.003, ask=136.687,
                    bid_bonds=6_000, ask_bonds=920,
                    trade_bonds=trade_bonds,
                    inferred_side="sell" if trade_bonds else "none",
                    previous_close=136.867,
                ), persist=True)

            account = engine.accounts["maker_v01_priority"]
            self.assertEqual(account.inventory, 2_000)
            self.assertAlmostEqual(
                engine.analyzer.persistent_book_reference(
                    int((start + timedelta(seconds=27)).timestamp() * 1000)
                ) or 0.0,
                136.345,
            )
            sell_order = next(iter(account.sell_orders.values()))
            self.assertEqual(sell_order.limit_price, 136.686)
            store.close()

    def test_persistent_book_alone_cannot_trigger_active_buy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-book-no-active.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 14, 9, 50, tzinfo=SHANGHAI)

            for seconds in range(0, 19, 3):
                engine.on_replay_tick(self._replay_tick(
                    start + timedelta(seconds=seconds),
                    last=136.500, bid=136.000, ask=137.000,
                    trade_bonds=1_000 if seconds == 0 else 0,
                    inferred_side="buy" if seconds == 0 else "none",
                    previous_close=136.000,
                ), persist=True)
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=21),
                last=136.500, bid=135.500, ask=135.900,
                previous_close=136.000,
            ), persist=True)

            account = engine.accounts["maker_v01_priority"]
            self.assertEqual(account.inventory, 1_000)
            active_fills = store.connection.execute(
                """SELECT COUNT(*) FROM maker_paper_fills
                   WHERE fill_reason='active_deep_discount'"""
            ).fetchone()[0]
            self.assertEqual(active_fills, 0)
            store.close()

    def test_stable_wide_book_keeps_passive_t_bid_inside_the_spread(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-stable-wide.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 14, 11, 15, 27, tzinfo=SHANGHAI)

            for seconds in range(0, 22, 3):
                engine.on_replay_tick(self._replay_tick(
                    start + timedelta(seconds=seconds),
                    last=136.521, bid=136.521, ask=136.781,
                    bid_bonds=6_000,
                    trade_bonds=1_000 if seconds == 0 else 0,
                    inferred_side="sell" if seconds == 0 else "none",
                    previous_close=136.922,
                ), persist=True)

            account = engine.accounts["maker_v01_priority"]
            context = engine._decision_context(self._replay_tick(
                start + timedelta(seconds=21),
                last=136.521, bid=136.521, ask=136.781,
                bid_bonds=6_000, previous_close=136.922,
            ))
            self.assertEqual(
                context.reference_source, "persistent_inside_market"
            )
            self.assertLess(
                context.reference_price - 136.522,
                engine.parameters.minimum_entry_edge,
            )
            self.assertIsNotNone(account.buy_order)
            self.assertEqual(account.buy_order.limit_price, 136.522)
            store.close()

    def test_moderate_discount_with_support_remains_passive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-moderate-passive.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            moment = datetime(2026, 8, 14, 9, 51, tzinfo=SHANGHAI)

            engine.on_replay_tick(self._replay_tick(
                moment, last=136.690, bid=136.003, ask=136.690,
                bid_bonds=6_000, ask_bonds=1_000,
                trade_bonds=1_000, inferred_side="sell",
                previous_close=136.922,
            ), persist=True)

            account = engine.accounts["maker_v01_priority"]
            self.assertEqual(account.inventory, 1_000)
            self.assertIsNotNone(account.buy_order)
            active_fills = store.connection.execute(
                """SELECT COUNT(*) FROM maker_paper_fills
                   WHERE fill_reason='active_deep_discount'"""
            ).fetchone()[0]
            self.assertEqual(active_fills, 0)
            store.close()

    def test_cheap_buy_exits_at_current_wall_instead_of_old_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-current-wall.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority", "queue"),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 13, 10, 16, 36, tzinfo=SHANGHAI)
            engine.on_replay_tick(self._replay_tick(
                start, last=137.600, bid=137.200, ask=137.600,
                trade_bonds=6_000, inferred_side="buy", ask_bonds=3_000,
            ), persist=True)
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=36),
                last=137.200, bid=136.801, ask=137.000,
                ask_bonds=1_000,
            ), persist=True)
            for account in engine.accounts.values():
                self.assertEqual(account.inventory, 2_000)
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=45),
                last=137.000, bid=136.801, ask=137.660,
                ask_bonds=3_280,
            ), persist=True)
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=54),
                last=137.000, bid=136.801, ask=137.650,
                ask_bonds=1_000,
            ), persist=True)
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=60),
                last=137.660, bid=136.801, ask=137.660,
                trade_bonds=3_000, inferred_side="buy", ask_bonds=1_280,
            ), persist=True)
            fills = store.connection.execute(
                """SELECT strategy_id,side,price FROM maker_paper_fills
                   WHERE side='sell' ORDER BY strategy_id"""
            ).fetchall()
            exits = {
                row["strategy_id"]: round(float(row["price"]), 3)
                for row in fills
            }
            self.assertEqual(exits["maker_v01_priority"], 137.649)
            self.assertEqual(exits["maker_v01_queue"], 137.65)
            store.close()

    def test_base_inventory_is_not_sold_at_dynamic_fair_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-base-high-sell.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority", "queue"),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 13, 14, 10, tzinfo=SHANGHAI)

            # Establish 136.800 as the current reliable transaction anchor.
            engine.on_replay_tick(self._replay_tick(
                start, last=136.800, bid=136.700, ask=136.800,
                trade_bonds=10_000, inferred_side="buy", ask_bonds=6_000,
            ), persist=True)
            for account in engine.accounts.values():
                self.assertEqual(account.sell_orders, {})

            # A thick offer 0.30 above fair value is a genuine high-sale setup.
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=3), last=136.800,
                bid=136.900, ask=137.100, ask_bonds=6_000,
            ), persist=True)
            self.assertEqual(
                next(iter(
                    engine.accounts["maker_v01_priority"].sell_orders.values()
                )).limit_price,
                137.099,
            )
            self.assertEqual(
                next(iter(
                    engine.accounts["maker_v01_queue"].sell_orders.values()
                )).limit_price,
                137.1,
            )
            store.close()

    def test_large_buy_breakout_turns_swept_price_into_support(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-breakout-support.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 13, 14, 35, 39, tzinfo=SHANGHAI)
            engine.on_replay_tick(self._replay_tick(
                start - timedelta(seconds=3), last=136.900,
                bid=136.700, ask=137.000,
                trade_bonds=10_000, inferred_side="buy",
                ask_bonds=11_000,
            ), persist=True)
            anchor = AnchorState(
                support_price=136.900, exit_price=137.000,
                band_midpoint=136.950, reference_price=136.950,
                confidence=0.8, buy_effective_bonds=10_000,
                sell_effective_bonds=0, downside_pressure=0,
                stock_return_5m=None, stock_factor=1.0,
                buy_clusters=(), sell_reference_price=None,
            )
            opportunity = Opportunity(
                kind="sweep_tail", signal_ts_ms=int(start.timestamp() * 1000),
                market_time=start.time().isoformat(timespec="milliseconds"),
                entry_price=137.000, quantity_bonds=1_000,
                target_exit_price=137.288, priority_exit_price=137.287,
                theoretical_edge=0.287, anchor=anchor,
            )
            engine.analyzer.breakout_support_price = 137.000
            engine.analyzer.breakout_support_ts_ms = int(start.timestamp() * 1000)
            account = engine.accounts["maker_v01_priority"]

            # A full base position must not chase the breakout price into an
            # extra lot; buying near support is only for restoring a deficit.
            engine._active_sweep(account, self._replay_tick(
                start, last=137.000, bid=136.800, ask=137.000,
                ask_bonds=2_000,
            ), opportunity, persist=True)
            self.assertEqual(account.inventory, 1_000)

            # Base inventory is not sold back at 136.999/137.000 while the
            # 137 breakout support remains strong.
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=3), last=137.000,
                bid=136.700, ask=137.000, ask_bonds=12_000,
            ), persist=True)
            self.assertEqual(account.sell_orders, {})

            # Roughly 0.20 below support belongs on the passive bid; it is not
            # enough discount to cross the spread while the tape is strong.
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=5), last=137.000,
                bid=136.600, ask=136.804, ask_bonds=1_000,
            ), persist=True)
            self.assertEqual(account.inventory, 1_000)

            # A 136.708 ask is a roughly 0.30 discount to the new 137 support
            # and should be actively taken as the one extra-inventory lot.
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=6), last=136.708,
                bid=136.600, ask=136.708, ask_bonds=1_000,
            ), persist=True)
            self.assertEqual(account.inventory, 2_000)
            extra_lots = [
                lot for lot in account.lots.values()
                if lot.entry_price is not None and lot.remaining_quantity > 0
            ]
            self.assertEqual(len(extra_lots), 1)
            self.assertEqual(extra_lots[0].entry_price, 136.708)

            # That discounted extra lot may exit around the 137 support, but
            # the base lot still has no sell order there.
            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=9), last=136.708,
                bid=136.800, ask=137.000, ask_bonds=12_000,
            ), persist=True)
            orders = account.sell_orders
            self.assertEqual(set(orders), {extra_lots[0].db_id})
            self.assertEqual(orders[extra_lots[0].db_id].limit_price, 136.999)

            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=12), last=137.000,
                bid=136.800, ask=137.000, ask_bonds=11_000,
                trade_bonds=1_000, inferred_side="buy",
            ), persist=True)
            self.assertEqual(account.inventory, 1_000)
            store.close()

    def test_near_flat_extra_inventory_exits_before_downside_book_vacuum(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-downside-risk.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 14, 11, 18, 30, tzinfo=SHANGHAI)

            def risk_tick(
                moment: datetime, *, ask: float,
                trade_bonds: float = 0.0,
            ) -> ReplayTick:
                return replace(
                    self._replay_tick(
                        moment, last=136.521, bid=136.520, ask=ask,
                        trade_bonds=trade_bonds,
                        inferred_side="sell" if trade_bonds else "none",
                        previous_close=136.867,
                    ),
                    bids=(
                        (136.520, 2_000),
                        (136.351, 1_000),
                        (136.350, 1_860),
                        (136.053, 4_000),
                        (136.052, 1_000),
                    ),
                    asks=(
                        (ask, 1_000),
                        (ask + 0.001, 2_000),
                        (ask + 0.100, 1_000),
                    ),
                )

            first = risk_tick(start, ask=136.907, trade_bonds=3_000)
            engine.on_replay_tick(first, persist=True)
            account = engine.accounts["maker_v01_priority"]

            # Reproduce the already-correct 136.522 low fill.  The new rule
            # evaluates what to do after that fill; it must not reject it.
            buy_order = engine._new_order(
                account, first, side="buy", kind="low_bid_reversion",
                lot_id=None, price=136.522, quantity=1_000,
                queue_ahead=0.0, target_price=None, persist=True,
            )
            account.buy_order = buy_order
            engine._fill_buy(
                account, first, buy_order, 1_000,
                first.market_ts_ms * 1_000_000,
                kind="low_bid_reversion", target_price=None,
                persist=True,
            )
            self.assertEqual(account.inventory, 2_000)

            engine.on_replay_tick(risk_tick(
                start + timedelta(seconds=9), ask=136.907,
                trade_bonds=3_000,
            ), persist=True)
            self.assertEqual(account.inventory, 2_000)

            # The offer has compressed by 0.107 in thirty seconds while the
            # 136.520 bid still exists above a two-step downside vacuum.
            engine.on_replay_tick(risk_tick(
                start + timedelta(seconds=33), ask=136.800,
            ), persist=True)

            self.assertEqual(account.inventory, 1_000)
            exit_fill = store.connection.execute(
                """SELECT side,price,quantity,fill_reason,inventory_after
                   FROM maker_paper_fills
                   WHERE fill_reason='active_downside_risk_exit'"""
            ).fetchone()
            self.assertIsNotNone(exit_fill)
            self.assertEqual(exit_fill["side"], "sell")
            self.assertEqual(float(exit_fill["price"]), 136.520)
            self.assertEqual(float(exit_fill["quantity"]), 1_000)
            self.assertEqual(float(exit_fill["inventory_after"]), 1_000)
            store.close()

    def test_downside_vacuum_does_not_force_a_large_loss_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-downside-loss.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 14, 11, 18, 30, tzinfo=SHANGHAI)

            def risk_tick(moment: datetime, ask: float) -> ReplayTick:
                return replace(
                    self._replay_tick(
                        moment, last=136.520, bid=136.520, ask=ask,
                        trade_bonds=3_000 if ask == 136.907 else 0,
                        inferred_side="sell" if ask == 136.907 else "none",
                    ),
                    bids=(
                        (136.520, 2_000), (136.351, 1_000),
                        (136.350, 1_860), (136.053, 4_000),
                    ),
                    asks=((ask, 1_000), (ask + 0.001, 1_000)),
                )

            first = risk_tick(start, 136.907)
            engine.on_replay_tick(first, persist=True)
            account = engine.accounts["maker_v01_priority"]
            order = engine._new_order(
                account, first, side="buy", kind="low_bid_reversion",
                lot_id=None, price=136.600, quantity=1_000,
                queue_ahead=0.0, target_price=None, persist=True,
            )
            account.buy_order = order
            engine._fill_buy(
                account, first, order, 1_000,
                first.market_ts_ms * 1_000_000,
                kind="low_bid_reversion", target_price=None, persist=True,
            )
            engine.on_replay_tick(risk_tick(
                start + timedelta(seconds=9), 136.907,
            ), persist=True)
            engine.on_replay_tick(risk_tick(
                start + timedelta(seconds=33), 136.800,
            ), persist=True)

            self.assertEqual(account.inventory, 2_000)
            risk_fills = store.connection.execute(
                """SELECT COUNT(*) FROM maker_paper_fills
                   WHERE fill_reason='active_downside_risk_exit'"""
            ).fetchone()[0]
            self.assertEqual(risk_fills, 0)
            store.close()

    def test_compressed_offer_is_a_second_profitable_exit_opportunity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-second-exit.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 14, 11, 18, 0, tzinfo=SHANGHAI)
            first = self._replay_tick(
                start, last=136.522, bid=136.520, ask=136.739,
                previous_close=136.867,
            )
            engine.on_replay_tick(first, persist=True)
            account = engine.accounts["maker_v01_priority"]
            order = engine._new_order(
                account, first, side="buy", kind="low_bid_reversion",
                lot_id=None, price=136.522, quantity=1_000,
                queue_ahead=0.0, target_price=None, persist=True,
            )
            account.buy_order = order
            engine._fill_buy(
                account, first, order, 1_000,
                first.market_ts_ms * 1_000_000,
                kind="low_bid_reversion", target_price=None, persist=True,
            )

            compressed = self._replay_tick(
                start + timedelta(seconds=81),
                last=136.521, bid=136.351, ask=136.699,
                ask_bonds=6_000, previous_close=136.867,
            )
            engine.on_replay_tick(compressed, persist=True)
            sell_order = next(iter(account.sell_orders.values()))
            self.assertEqual(sell_order.limit_price, 136.698)

            engine.on_replay_tick(self._replay_tick(
                start + timedelta(seconds=93),
                last=136.699, bid=136.351, ask=136.700,
                trade_bonds=1_000, inferred_side="buy",
                ask_bonds=6_000, previous_close=136.867,
            ), persist=True)
            self.assertEqual(account.inventory, 1_000)
            exit_fill = store.connection.execute(
                """SELECT price FROM maker_paper_fills
                   WHERE side='sell' ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            self.assertEqual(float(exit_fill["price"]), 136.698)
            store.close()

    def test_extra_inventory_can_exit_above_fair_despite_historical_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-fair-exit.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 14, 13, 7, 42, tzinfo=SHANGHAI)
            engine._start_date(start.date().isoformat())
            account = engine.accounts["maker_v01_priority"]
            entry_tick = self._replay_tick(
                start, last=136.381, bid=136.300, ask=136.400,
            )
            order = engine._new_order(
                account, entry_tick, side="buy", kind="low_bid_reversion",
                lot_id=None, price=136.381, quantity=1_000,
                queue_ahead=0.0, target_price=None, persist=True,
            )
            account.buy_order = order
            engine._fill_buy(
                account, entry_tick, order, 1_000,
                entry_tick.market_ts_ms * 1_000_000,
                kind="low_bid_reversion", target_price=None, persist=True,
            )
            for offset in (-9, -6, -3):
                engine.analyzer.on_tick(self._replay_tick(
                    start + timedelta(seconds=offset),
                    last=135.300, bid=135.000, ask=136.000,
                    trade_bonds=1_000, inferred_side="sell",
                ))
            engine.observed_market_trade = True

            current = self._replay_tick(
                start + timedelta(seconds=9),
                last=135.006, bid=135.006, ask=135.618,
                previous_close=136.922, ask_bonds=760,
            )
            engine.on_replay_tick(current, persist=True)

            context = engine._decision_context(current)
            self.assertEqual(context.reference_source, "current_midpoint")
            self.assertAlmostEqual(context.reference_price, 135.312)
            sell_order = next(iter(account.sell_orders.values()))
            self.assertEqual(sell_order.limit_price, 135.617)
            self.assertLess(sell_order.limit_price, 136.381)
            store.close()

    def test_fragile_last_bid_triggers_near_flat_active_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-fragile-bid.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            moment = datetime(2026, 8, 14, 13, 9, 39, tzinfo=SHANGHAI)
            seed = self._replay_tick(
                moment - timedelta(seconds=3),
                last=135.920, bid=135.921, ask=136.497,
            )
            engine.on_replay_tick(seed, persist=True)
            account = engine.accounts["maker_v01_priority"]
            order = engine._new_order(
                account, seed, side="buy", kind="low_bid_reversion",
                lot_id=None, price=135.922, quantity=1_000,
                queue_ahead=0.0, target_price=None, persist=True,
            )
            account.buy_order = order
            engine._fill_buy(
                account, seed, order, 1_000,
                seed.market_ts_ms * 1_000_000,
                kind="low_bid_reversion", target_price=None, persist=True,
            )
            fragile = replace(
                self._replay_tick(
                    moment, last=135.920,
                    bid=135.920, ask=136.497,
                ),
                bids=(
                    (135.920, 1_000),
                    (135.201, 4_000),
                    (135.050, 74_000),
                    (135.006, 2_000),
                    (135.000, 62_000),
                ),
            )
            engine.on_replay_tick(fragile, persist=True)

            self.assertEqual(account.inventory, 1_000)
            fill = store.connection.execute(
                """SELECT price,quantity,fill_reason FROM maker_paper_fills
                   WHERE fill_reason='active_downside_risk_exit'"""
            ).fetchone()
            self.assertEqual(float(fill["price"]), 135.920)
            self.assertEqual(float(fill["quantity"]), 1_000)
            store.close()

    def test_unconfirmed_fast_rise_does_not_buy_the_newly_lifted_bid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-unconfirmed-rise.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            moment = datetime(2026, 8, 14, 13, 8, 30, tzinfo=SHANGHAI)
            engine._start_date(moment.date().isoformat())
            engine.previous_close_reference = 136.000
            account = engine.accounts["maker_v01_priority"]
            account.inventory = 0.0
            account.replenishment_quantity = 1_000
            account.replenishment_sale_value = 136_334
            tick = self._replay_tick(
                moment, last=135.701, bid=135.700, ask=136.334,
                bid_bonds=6_000, previous_close=136.000,
            )
            assessment = MarketAssessment(
                reference_price=136.017,
                reference_low=135.700,
                reference_high=136.334,
                reference_source="current_midpoint",
                reference_confidence=0.35,
                state="possible_rise",
                state_score=2,
                state_confidence=0.62,
                recent_buy_bonds=1_000,
                recent_sell_bonds=0,
                midpoint_change=0.343,
                short_ask_change=0.0,
                largest_ask_gap=0.0,
                downside_book_vacuum=False,
                fragile_top_bid=False,
                iron_floor_price=None,
                iron_floor_bonds=0.0,
                evidence=("买卖中点快速上移但突破尚未确认",),
            )

            engine._refresh_orders(
                account, tick, assessment, persist=True,
            )
            self.assertIsNone(account.buy_order)

            # Once the upward state is genuinely confirmed, the guard no
            # longer blocks the same quote; the normal fair-price and depth
            # checks decide whether inventory should be restored.
            confirmed = replace(
                assessment, state="rising", state_score=3,
            )
            engine._refresh_orders(
                account, tick, confirmed, persist=True,
            )
            self.assertIsNotNone(account.buy_order)
            self.assertEqual(account.buy_order.limit_price, 135.701)
            store.close()

    def test_priority_v121_exact_offer_clear_confirmation_is_branch_local(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-v121-confirm.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority", "queue"),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(
                config, store,
                priority_policy=PRIORITY_POLICY_V121_CANDIDATE,
                queue_policy=QUEUE_POLICY_V113_CANDIDATE,
            )
            moment = datetime(2026, 8, 14, 10, 17, 39, tzinfo=SHANGHAI)
            engine._start_date(moment.date().isoformat())
            for account in engine._standard_accounts():
                account.last_ask = 136.800
            event = self._replay_tick(
                moment, last=136.800, bid=136.709, ask=137.379,
                trade_bonds=4_000, inferred_side="buy",
                previous_close=136.922,
            )
            assessment = MarketAssessment(
                reference_price=136.754,
                reference_low=136.709,
                reference_high=137.379,
                reference_source="persistent_inside_market",
                reference_confidence=0.55,
                state="possible_rise",
                state_score=2,
                state_confidence=0.62,
                recent_buy_bonds=5_000,
                recent_sell_bonds=0,
                midpoint_change=0.309,
                short_ask_change=0.579,
                largest_ask_gap=0.0,
                downside_book_vacuum=False,
                fragile_top_bid=False,
                iron_floor_price=None,
                iron_floor_bonds=0.0,
                evidence=("active buy clears the exact offer",),
            )
            with (
                patch.object(
                    engine.analyzer, "assess_market",
                    return_value=assessment,
                ),
                patch.object(engine, "_active_discount_entry"),
                patch.object(engine, "_active_profitable_turnover_exit"),
                patch.object(engine, "_active_falling_profitable_bid_exit"),
                patch.object(engine, "_active_inventory_risk_exit"),
                patch.object(engine, "_refresh_orders"),
            ):
                engine.on_replay_tick(event, persist=True)

            self.assertEqual(
                engine.last_exact_offer_clear_rise_trade_ts_ms,
                event.market_ts_ms,
            )
            lower_print = self._replay_tick(
                moment + timedelta(seconds=3),
                last=136.709, bid=136.708, ask=137.378,
                trade_bonds=1_000, inferred_side="sell",
                previous_close=136.922,
            )
            self.assertTrue(engine._confirmed_rise_is_recent(
                lower_print, PRIORITY_POLICY_V121_CANDIDATE,
            ))
            self.assertFalse(engine._confirmed_rise_is_recent(
                lower_print, PRIORITY_POLICY_V120_CANDIDATE,
            ))
            self.assertFalse(engine._confirmed_rise_is_recent(
                lower_print, QUEUE_POLICY_V113_CANDIDATE,
            ))
            store.close()

    def test_priority_v121_exact_offer_clear_allows_causal_low_buy(
        self,
    ) -> None:
        def run_case(database: Path, policy) -> float | None:
            base = test_config(database)
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                moment = datetime(
                    2026, 8, 14, 10, 17, 42, tzinfo=SHANGHAI,
                )
                engine._start_date(moment.date().isoformat())
                engine.previous_close_reference = 136.922
                account = engine.accounts["maker_v01_priority"]
                account.inventory = 1_000
                engine.observed_market_trade = True
                engine.last_exact_offer_clear_rise_trade_ts_ms = int(
                    (moment - timedelta(seconds=3)).timestamp() * 1_000
                )
                engine.last_exact_offer_clear_rise_price = 136.800
                tick = self._replay_tick(
                    moment, last=136.709, bid=136.708, ask=137.378,
                    bid_bonds=6_000, trade_bonds=1_000,
                    inferred_side="sell", previous_close=136.922,
                )
                assessment = MarketAssessment(
                    reference_price=137.043,
                    reference_low=136.708,
                    reference_high=137.378,
                    reference_source="current_midpoint",
                    reference_confidence=0.35,
                    state="possible_rise",
                    state_score=2,
                    state_confidence=0.62,
                    recent_buy_bonds=5_000,
                    recent_sell_bonds=1_000,
                    midpoint_change=0.308,
                    short_ask_change=0.0,
                    largest_ask_gap=0.0,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("lower sell after exact offer clear",),
                )
                engine._refresh_orders(
                    account, tick, assessment, persist=True,
                )
                return (
                    account.buy_order.limit_price
                    if account.buy_order is not None else None
                )
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertIsNone(run_case(
                root / "maker-v120-no-buy.sqlite3",
                PRIORITY_POLICY_V120_CANDIDATE,
            ))
            self.assertEqual(
                run_case(
                    root / "maker-v121-buy.sqlite3",
                    PRIORITY_POLICY_V121_CANDIDATE,
                ),
                136.709,
            )

    def test_priority_v122_requires_trade_volume_to_cover_visible_offer(
        self,
    ) -> None:
        def run_case(
            database: Path, policy, *, displayed_offer_bonds: float,
            trade_bonds: float, transaction_delta: int,
        ) -> bool:
            base = test_config(database)
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority", "queue"),
            ))
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                    queue_policy=QUEUE_POLICY_V113_CANDIDATE,
                )
                moment = datetime(
                    2026, 8, 14, 10, 17, 39, tzinfo=SHANGHAI,
                )
                engine._start_date(moment.date().isoformat())
                for account in engine._standard_accounts():
                    account.last_ask = 136.800
                    account.last_asks = ((136.800, displayed_offer_bonds),)
                event = replace(
                    self._replay_tick(
                        moment, last=136.800, bid=136.709, ask=137.379,
                        trade_bonds=trade_bonds, inferred_side="buy",
                        previous_close=136.922,
                    ),
                    transaction_delta=transaction_delta,
                )
                assessment = MarketAssessment(
                    reference_price=137.043,
                    reference_low=136.709,
                    reference_high=137.379,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="possible_rise",
                    state_score=2,
                    state_confidence=0.62,
                    recent_buy_bonds=5_000,
                    recent_sell_bonds=0,
                    midpoint_change=0.309,
                    short_ask_change=0.579,
                    largest_ask_gap=0.0,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("active buy may clear the visible offer",),
                )
                with (
                    patch.object(
                        engine.analyzer, "assess_market",
                        return_value=assessment,
                    ),
                    patch.object(engine, "_active_discount_entry"),
                    patch.object(engine, "_active_profitable_turnover_exit"),
                    patch.object(engine, "_active_falling_profitable_bid_exit"),
                    patch.object(engine, "_active_inventory_risk_exit"),
                    patch.object(engine, "_refresh_orders"),
                ):
                    engine.on_replay_tick(event, persist=True)
                queue = engine.accounts["maker_v01_queue"]
                self.assertFalse(engine._confirmed_rise_is_recent(
                    event, queue.policy,
                ))
                return (
                    engine.last_exact_offer_clear_rise_trade_ts_ms
                    == event.market_ts_ms
                )
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertTrue(run_case(
                root / "v121-partial.sqlite3",
                PRIORITY_POLICY_V121_CANDIDATE,
                displayed_offer_bonds=4_000,
                trade_bonds=1_000,
                transaction_delta=1,
            ))
            self.assertFalse(run_case(
                root / "v122-partial.sqlite3",
                PRIORITY_POLICY_V122_CANDIDATE,
                displayed_offer_bonds=4_000,
                trade_bonds=1_000,
                transaction_delta=1,
            ))
            self.assertFalse(run_case(
                root / "v122-missing-size.sqlite3",
                PRIORITY_POLICY_V122_CANDIDATE,
                displayed_offer_bonds=0,
                trade_bonds=4_000,
                transaction_delta=1,
            ))
            self.assertTrue(run_case(
                root / "v122-exact.sqlite3",
                PRIORITY_POLICY_V122_CANDIDATE,
                displayed_offer_bonds=4_000,
                trade_bonds=4_000,
                transaction_delta=1,
            ))
            self.assertTrue(run_case(
                root / "v122-aggregated.sqlite3",
                PRIORITY_POLICY_V122_CANDIDATE,
                displayed_offer_bonds=1_000,
                trade_bonds=2_000,
                transaction_delta=2,
            ))

    def test_priority_v122_full_t018_order_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-v122-t018.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                initial_inventory_bonds=1_000,
                maximum_inventory_bonds=2_000,
                initial_cash_cny=137_000,
                order_quantity_bonds=1_000,
                fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V122_CANDIDATE,
                )
                moment = datetime(
                    2026, 8, 14, 10, 17, 39, tzinfo=SHANGHAI,
                )
                engine._start_date(moment.date().isoformat())
                engine.previous_close_reference = 136.922
                account = engine.accounts["maker_v01_priority"]
                account.last_ask = 136.800
                account.last_asks = ((136.800, 4_000.0),)
                event = self._replay_tick(
                    moment, last=136.800, bid=136.709, ask=137.379,
                    trade_bonds=4_000, inferred_side="buy",
                    previous_close=136.922,
                )
                assessment = MarketAssessment(
                    reference_price=137.043,
                    reference_low=136.709,
                    reference_high=137.379,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="possible_rise",
                    state_score=2,
                    state_confidence=0.62,
                    recent_buy_bonds=5_000,
                    recent_sell_bonds=0,
                    midpoint_change=0.309,
                    short_ask_change=0.579,
                    largest_ask_gap=0.0,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("the full visible offer was bought",),
                )
                context = MakerDecisionContext(
                    reference_price=137.043,
                    reference_source="current_midpoint",
                    reliable_anchor=False,
                    spread=0.670,
                    bid_support_bonds=6_000.0,
                    ask_supply_bonds=3_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                with (
                    patch.object(
                        engine.analyzer, "assess_market",
                        return_value=assessment,
                    ),
                    patch.object(engine, "_active_discount_entry"),
                    patch.object(engine, "_active_profitable_turnover_exit"),
                    patch.object(engine, "_active_falling_profitable_bid_exit"),
                    patch.object(engine, "_active_inventory_risk_exit"),
                    patch.object(
                        engine, "_decision_context", return_value=context,
                    ),
                ):
                    engine.on_replay_tick(event, persist=True)
                self.assertIsNotNone(account.buy_order)
                self.assertEqual(account.buy_order.limit_price, 136.710)

                lower_print = self._replay_tick(
                    moment + timedelta(seconds=3),
                    last=136.709, bid=136.708, ask=137.378,
                    bid_bonds=3_000, trade_bonds=1_000,
                    inferred_side="sell", previous_close=136.922,
                )
                with (
                    patch.object(
                        engine.analyzer, "assess_market",
                        return_value=assessment,
                    ),
                    patch.object(engine, "_active_discount_entry"),
                    patch.object(engine, "_active_profitable_turnover_exit"),
                    patch.object(engine, "_active_falling_profitable_bid_exit"),
                    patch.object(engine, "_active_inventory_risk_exit"),
                    patch.object(
                        engine, "_decision_context", return_value=context,
                    ),
                ):
                    engine.on_replay_tick(lower_print, persist=True)
                fill = store.connection.execute(
                    """SELECT side,price,quantity,inventory_after
                       FROM maker_paper_fills
                       WHERE strategy_id='maker_v01_priority'
                       ORDER BY id DESC LIMIT 1"""
                ).fetchone()
                self.assertEqual(fill["side"], "buy")
                self.assertEqual(fill["price"], 136.710)
                self.assertEqual(fill["quantity"], 1_000.0)
                self.assertEqual(fill["inventory_after"], 2_000.0)
                assignment = store.connection.execute(
                    """SELECT model_id,parent_model_id
                       FROM maker_paper_model_assignments
                       WHERE strategy_id='maker_v01_priority'"""
                ).fetchone()
                self.assertEqual(
                    assignment["model_id"],
                    "maker_priority_v1_22_candidate",
                )
                self.assertEqual(
                    assignment["parent_model_id"],
                    "maker_priority_v1_21_candidate",
                )
            finally:
                store.close()

    def test_priority_v123_rebranches_from_v11_with_general_rules_only(self) -> None:
        policy = PRIORITY_POLICY_V123_CANDIDATE
        self.assertEqual(policy.parent_model_id, "maker_priority_v1_1")
        self.assertTrue(policy.enable_repeated_two_sided_base_turn)
        self.assertTrue(policy.enable_falling_profitable_bid_exit)
        self.assertTrue(policy.use_recent_intraday_reference_for_active_entry)
        self.assertTrue(policy.enable_priority_book_side_fill_correction)
        self.assertEqual(policy.confirmed_rise_grace_seconds_override, 60)

        # Selling extra inventory into confirmed downside flow is allowed, but
        # the final customer base cannot be shorted merely because a falling
        # label and a 0.20-yuan inside spread happen to coexist.
        self.assertFalse(policy.enable_downtrend_wide_spread_base_turn)
        self.assertFalse(policy.enable_persistent_bid_downtrend_turn)
        self.assertIsNone(policy.minimum_downtrend_turn_edge_override)
        self.assertFalse(policy.retain_priority_base_turn_on_recent_sell_corridor)
        self.assertTrue(policy.confirm_exact_offer_clear_in_possible_rise)
        self.assertTrue(policy.require_exact_offer_clear_volume_coverage)

        context = MakerDecisionContext(
            reference_price=136.000,
            reference_source="intraday_trade_anchor",
            reliable_anchor=True,
            spread=0.198,
            bid_support_bonds=2_000.0,
            ask_supply_bonds=1_000.0,
            wall_threshold_bonds=5_000.0,
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v123-base-short.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                self.assertFalse(engine._base_high_sell_is_safe(
                    136.097, context, policy, "possible_fall",
                    recent_lower_sell_bonds=1_000.0,
                ))
                self.assertTrue(engine._base_high_sell_is_safe(
                    136.251, context, policy, "possible_fall",
                    repeated_turn_replenishment_price=135.803,
                ))
            finally:
                store.close()

    def test_priority_v124_revalues_positive_momentum_base_shorts(self) -> None:
        parent = PRIORITY_POLICY_V123_CANDIDATE
        policy = PRIORITY_POLICY_V124_CANDIDATE
        self.assertEqual(
            policy.parent_model_id, "maker_priority_v1_23_candidate",
        )
        self.assertTrue(
            policy.require_rising_base_short_recent_trade_premium_and_supply,
        )
        self.assertFalse(
            parent.require_rising_base_short_recent_trade_premium_and_supply,
        )

        supplied = MakerDecisionContext(
            reference_price=138.800,
            reference_source="large_buy_breakout_support",
            reliable_anchor=True,
            spread=0.388,
            bid_support_bonds=5_000.0,
            ask_supply_bonds=14_000.0,
            wall_threshold_bonds=5_000.0,
        )
        thin = replace(supplied, ask_supply_bonds=4_000.0)
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v124-base-short.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                # Parent v1.23 calls 139.388 high relative to the stale
                # 138.800 breakout anchor.  V1.24 sees that it is only 0.189
                # above recent real trades and refuses the customer-base short.
                self.assertTrue(engine._base_high_sell_is_safe(
                    139.388, supplied, parent, "possible_rise",
                    recent_trade_reference=139.199,
                ))
                self.assertFalse(engine._base_high_sell_is_safe(
                    139.388, supplied, policy, "possible_rise",
                    recent_trade_reference=139.199,
                ))

                # A genuine recent-trade premium still needs current overhead
                # supply; either missing input rejects the rising-state short.
                self.assertTrue(engine._base_high_sell_is_safe(
                    139.388, supplied, policy, "possible_rise",
                    recent_trade_reference=139.050,
                ))
                self.assertFalse(engine._base_high_sell_is_safe(
                    139.388, thin, policy, "possible_rise",
                    recent_trade_reference=139.050,
                ))

                # A causally repeated high/low corridor has its own explicit
                # replenishment price and is not overwritten by the new fair-
                # value guard.  No intraday trades at the open also retain the
                # parent gap logic rather than inventing a reference.
                self.assertTrue(engine._base_high_sell_is_safe(
                    139.100, supplied, policy, "possible_rise",
                    repeated_turn_replenishment_price=138.800,
                    recent_trade_reference=139.050,
                ))
                self.assertTrue(engine._base_high_sell_is_safe(
                    139.388, supplied, policy, "possible_rise",
                    recent_trade_reference=None,
                ))
            finally:
                store.close()

    def test_priority_v125_separates_extra_exit_from_immediate_base_short(self) -> None:
        parent = PRIORITY_POLICY_V124_CANDIDATE
        policy = PRIORITY_POLICY_V125_CANDIDATE
        self.assertEqual(
            policy.parent_model_id, "maker_priority_v1_24_candidate",
        )
        self.assertEqual(
            policy.priority_rising_base_short_after_extra_exit_isolation_seconds,
            15,
        )
        self.assertEqual(
            parent.priority_rising_base_short_after_extra_exit_isolation_seconds,
            0,
        )
        context = MakerDecisionContext(
            reference_price=136.755,
            reference_source="intraday_trade_anchor",
            reliable_anchor=True,
            spread=0.299,
            bid_support_bonds=5_000.0,
            ask_supply_bonds=14_440.0,
            wall_threshold_bonds=5_000.0,
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v125-base-short.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                common = {
                    "recent_trade_reference": 136.599,
                    "recent_priority_extra_exit_price": 136.898,
                    "recent_priority_extra_exit_age_ms": 6_000,
                }
                # V1.24 allows the 0.299 recent-trade premium with thick
                # supply.  V1.25 recognizes that the same price just flattened
                # an extra lot and refuses to turn it into an immediate short.
                self.assertTrue(engine._base_high_sell_is_safe(
                    136.898, context, parent, "rising", **common,
                ))
                self.assertFalse(engine._base_high_sell_is_safe(
                    136.898, context, policy, "rising", **common,
                ))

                # Each independent source of new certainty releases the
                # isolation: a full lower-side print, a deep current premium,
                # a distinct higher cluster, expiry, or only provisional rise.
                self.assertTrue(engine._base_high_sell_is_safe(
                    136.898, context, policy, "rising",
                    recent_lower_sell_bonds=1_000.0, **common,
                ))
                self.assertTrue(engine._base_high_sell_is_safe(
                    136.898, context, policy, "rising",
                    **{**common, "recent_trade_reference": 136.300},
                ))
                self.assertTrue(engine._base_high_sell_is_safe(
                    136.920, context, policy, "rising", **common,
                ))
                self.assertTrue(engine._base_high_sell_is_safe(
                    136.898, context, policy, "rising",
                    **{**common, "recent_priority_extra_exit_age_ms": 15_001},
                ))
                self.assertTrue(engine._base_high_sell_is_safe(
                    136.898, context, policy, "possible_rise", **common,
                ))
            finally:
                store.close()

    def test_priority_v126_identifies_only_medium_wall_supported_base_shorts(
        self,
    ) -> None:
        policy = PRIORITY_POLICY_V126_CANDIDATE
        self.assertEqual(
            policy.parent_model_id, "maker_priority_v1_25_candidate",
        )
        self.assertTrue(policy.enable_dynamic_medium_base_short_replenishment)
        self.assertFalse(
            PRIORITY_POLICY_V125_CANDIDATE
                .enable_dynamic_medium_base_short_replenishment
        )
        supplied = MakerDecisionContext(
            reference_price=134.849,
            reference_source="intraday_trade_anchor",
            reliable_anchor=True,
            spread=0.400,
            bid_support_bonds=2_000.0,
            ask_supply_bonds=5_000.0,
            wall_threshold_bonds=5_000.0,
        )
        thin = replace(supplied, ask_supply_bonds=4_000.0)
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v126-origin.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                self.assertTrue(
                    engine._is_medium_wall_supported_base_short(
                        135.196, supplied,
                        repeated_turn_replenishment_price=None,
                    )
                )
                self.assertTrue(
                    engine._is_medium_wall_supported_base_short(
                        135.196, supplied,
                        repeated_turn_replenishment_price=None,
                    )
                )
                self.assertFalse(
                    engine._is_medium_wall_supported_base_short(
                        135.196, thin,
                        repeated_turn_replenishment_price=None,
                    )
                )
                self.assertFalse(
                    engine._is_medium_wall_supported_base_short(
                        135.196, supplied,
                        repeated_turn_replenishment_price=134.899,
                    )
                )
                self.assertFalse(
                    engine._is_medium_wall_supported_base_short(
                        135.400, supplied,
                        repeated_turn_replenishment_price=None,
                    )
                )
            finally:
                store.close()

    def test_priority_v126_replenishes_only_a_stale_profitable_medium_short(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v126-cover.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V126_CANDIDATE,
                )
                moment = datetime(2026, 8, 10, 9, 39, 0, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                base_lot = next(iter(account.lots.values()))
                sale_tick = self._replay_tick(
                    moment, last=135.196, bid=134.899, ask=135.197,
                    ask_bonds=5_000.0,
                )
                sale_order = engine._new_order(
                    account, sale_tick, side="sell", kind="inventory_exit",
                    lot_id=base_lot.db_id, price=135.196, quantity=1_000.0,
                    queue_ahead=0.0, target_price=135.196, persist=True,
                    medium_wall_supported_base_short=True,
                )
                engine._fill_sell(
                    account, sale_tick, sale_order, 1_000.0,
                    sale_tick.market_ts_ms * 1_000_000, persist=True,
                )
                self.assertEqual(account.inventory, 0.0)
                self.assertEqual(
                    account.medium_wall_supported_replenishment_quantity,
                    1_000.0,
                )
                old_buy = engine._new_order(
                    account, sale_tick, side="buy",
                    kind="inventory_replenish", lot_id=None,
                    price=133.399, quantity=1_000.0, queue_ahead=0.0,
                    target_price=None, persist=True,
                )
                account.buy_order = old_buy

                def assessment(state: str) -> MarketAssessment:
                    return MarketAssessment(
                        reference_price=134.899,
                        reference_low=134.800,
                        reference_high=135.000,
                        reference_source="intraday_trade_anchor",
                        reference_confidence=0.8,
                        state=state,
                        state_score=0,
                        state_confidence=0.7,
                        recent_buy_bonds=1_000.0,
                        recent_sell_bonds=0.0,
                        midpoint_change=0.0,
                        short_ask_change=0.0,
                        largest_ask_gap=0.05,
                        downside_book_vacuum=False,
                        fragile_top_bid=False,
                        iron_floor_price=None,
                        iron_floor_bonds=0.0,
                        evidence=(),
                    )

                recovery_tick = self._replay_tick(
                    moment + timedelta(seconds=58),
                    last=134.899, bid=134.800, ask=134.899,
                    ask_bonds=1_000.0,
                )
                self.assertFalse(
                    engine._active_medium_base_short_replenishment(
                        account, recovery_tick, assessment("falling"),
                        persist=True,
                        received_ts_ns=recovery_tick.market_ts_ms * 1_000_000,
                    )
                )
                too_expensive = replace(
                    recovery_tick,
                    asks=((135.050, 1_000.0),),
                )
                self.assertFalse(
                    engine._active_medium_base_short_replenishment(
                        account, too_expensive, assessment("stable"),
                        persist=True,
                        received_ts_ns=too_expensive.market_ts_ms * 1_000_000,
                    )
                )
                old_buy.limit_price = 134.200
                self.assertFalse(
                    engine._active_medium_base_short_replenishment(
                        account, recovery_tick, assessment("possible_rise"),
                        persist=True,
                        received_ts_ns=recovery_tick.market_ts_ms * 1_000_000,
                    )
                )
                old_buy.limit_price = 133.399
                account.policy = PRIORITY_POLICY_V125_CANDIDATE
                self.assertFalse(
                    engine._active_medium_base_short_replenishment(
                        account, recovery_tick, assessment("possible_rise"),
                        persist=True,
                        received_ts_ns=recovery_tick.market_ts_ms * 1_000_000,
                    )
                )
                account.policy = PRIORITY_POLICY_V126_CANDIDATE
                self.assertTrue(
                    engine._active_medium_base_short_replenishment(
                        account, recovery_tick, assessment("possible_rise"),
                        persist=True,
                        received_ts_ns=recovery_tick.market_ts_ms * 1_000_000,
                    )
                )
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(
                    account.medium_wall_supported_replenishment_quantity,
                    0.0,
                )
                fill = store.connection.execute(
                    """SELECT price,quantity,fill_reason
                       FROM maker_paper_fills
                       WHERE fill_reason=
                           'active_medium_base_short_replenishment'"""
                ).fetchone()
                self.assertEqual(float(fill["price"]), 134.899)
                self.assertEqual(float(fill["quantity"]), 1_000.0)
                self.assertEqual(
                    fill["fill_reason"],
                    "active_medium_base_short_replenishment",
                )
                cancelled = store.connection.execute(
                    "SELECT status,cancel_reason FROM maker_paper_orders "
                    "WHERE id=?",
                    (old_buy.db_id,),
                ).fetchone()
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertEqual(
                    cancelled["cancel_reason"],
                    "dynamic_medium_base_short_replenishment",
                )
            finally:
                store.close()

    def test_priority_v127_stops_a_confirmed_rising_base_short_near_flat(
        self,
    ) -> None:
        policy = PRIORITY_POLICY_V127_CANDIDATE
        self.assertEqual(
            policy.parent_model_id, "maker_priority_v1_26_candidate",
        )
        self.assertTrue(
            policy.enable_confirmed_rising_near_flat_base_short_stop,
        )
        self.assertFalse(
            PRIORITY_POLICY_V126_CANDIDATE
                .enable_confirmed_rising_near_flat_base_short_stop,
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v127-stop.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                moment = datetime(2026, 8, 7, 9, 55, 20, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                base_lot = next(iter(account.lots.values()))
                sale_tick = self._replay_tick(
                    moment, last=138.796, bid=137.201, ask=138.796,
                )
                sale_order = engine._new_order(
                    account, sale_tick, side="sell", kind="inventory_exit",
                    lot_id=base_lot.db_id, price=138.795, quantity=1_000.0,
                    queue_ahead=0.0, target_price=138.795, persist=True,
                )
                engine._fill_sell(
                    account, sale_tick, sale_order, 1_000.0,
                    sale_tick.market_ts_ms * 1_000_000, persist=True,
                )
                old_buy = engine._new_order(
                    account, sale_tick, side="buy",
                    kind="inventory_replenish", lot_id=None,
                    price=137.202, quantity=1_000.0, queue_ahead=0.0,
                    target_price=None, persist=True,
                )
                account.buy_order = old_buy

                def assessment(state: str = "rising") -> MarketAssessment:
                    return MarketAssessment(
                        reference_price=138.800,
                        reference_low=138.799,
                        reference_high=138.800,
                        reference_source="current_midpoint",
                        reference_confidence=0.7,
                        state=state,
                        state_score=2,
                        state_confidence=0.8,
                        recent_buy_bonds=5_000.0,
                        recent_sell_bonds=0.0,
                        midpoint_change=0.004,
                        short_ask_change=0.004,
                        largest_ask_gap=0.05,
                        downside_book_vacuum=False,
                        fragile_top_bid=False,
                        iron_floor_price=None,
                        iron_floor_bonds=0.0,
                        evidence=(),
                    )

                recovery_tick = self._replay_tick(
                    moment + timedelta(seconds=12),
                    last=138.800, bid=138.799, ask=138.800,
                    trade_bonds=5_000.0, inferred_side="buy",
                    ask_bonds=3_190.0,
                )
                account.policy = PRIORITY_POLICY_V126_CANDIDATE
                self.assertFalse(
                    engine._active_confirmed_rising_near_flat_base_short_stop(
                        account, recovery_tick, assessment(), persist=True,
                        received_ts_ns=recovery_tick.market_ts_ms * 1_000_000,
                    )
                )
                account.policy = policy
                self.assertFalse(
                    engine._active_confirmed_rising_near_flat_base_short_stop(
                        account, recovery_tick, assessment("possible_rise"),
                        persist=True,
                        received_ts_ns=recovery_tick.market_ts_ms * 1_000_000,
                    )
                )
                stale_reference = replace(
                    assessment(), reference_price=138.700,
                )
                self.assertFalse(
                    engine._active_confirmed_rising_near_flat_base_short_stop(
                        account, recovery_tick, stale_reference, persist=True,
                        received_ts_ns=recovery_tick.market_ts_ms * 1_000_000,
                    )
                )
                expired_tick = replace(
                    recovery_tick,
                    market_ts_ms=sale_tick.market_ts_ms + 31_000,
                    market_time="09:55:51.000",
                )
                self.assertFalse(
                    engine._active_confirmed_rising_near_flat_base_short_stop(
                        account, expired_tick, assessment(), persist=True,
                        received_ts_ns=expired_tick.market_ts_ms * 1_000_000,
                    )
                )
                self.assertFalse(
                    engine._active_confirmed_rising_near_flat_base_short_stop(
                        account,
                        replace(recovery_tick, trade_bonds=999.0),
                        assessment(), persist=True,
                        received_ts_ns=recovery_tick.market_ts_ms * 1_000_000,
                    )
                )
                self.assertFalse(
                    engine._active_confirmed_rising_near_flat_base_short_stop(
                        account,
                        replace(recovery_tick, bids=((138.700, 1_000.0),)),
                        assessment(), persist=True,
                        received_ts_ns=recovery_tick.market_ts_ms * 1_000_000,
                    )
                )
                too_expensive = replace(
                    recovery_tick,
                    last_price=138.811,
                    bids=((138.810, 1_000.0),),
                    asks=((138.811, 3_190.0),),
                )
                self.assertFalse(
                    engine._active_confirmed_rising_near_flat_base_short_stop(
                        account, too_expensive, assessment(), persist=True,
                        received_ts_ns=too_expensive.market_ts_ms * 1_000_000,
                    )
                )
                self.assertTrue(
                    engine._active_confirmed_rising_near_flat_base_short_stop(
                        account, recovery_tick, assessment(), persist=True,
                        received_ts_ns=recovery_tick.market_ts_ms * 1_000_000,
                    )
                )
                self.assertEqual(account.inventory, 1_000.0)
                fill = store.connection.execute(
                    "SELECT price,quantity,fill_reason FROM maker_paper_fills "
                    "WHERE fill_reason="
                    "'active_confirmed_rising_base_short_stop'"
                ).fetchone()
                self.assertEqual(float(fill["price"]), 138.800)
                self.assertEqual(float(fill["quantity"]), 1_000.0)
                cancelled = store.connection.execute(
                    "SELECT status,cancel_reason FROM maker_paper_orders "
                    "WHERE id=?", (old_buy.db_id,),
                ).fetchone()
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertEqual(
                    cancelled["cancel_reason"],
                    "confirmed_rising_near_flat_base_short_stop",
                )
            finally:
                store.close()

    def test_priority_v128_keeps_rising_base_short_above_live_fair(self) -> None:
        parent = PRIORITY_POLICY_V127_CANDIDATE
        policy = PRIORITY_POLICY_V128_CANDIDATE
        self.assertEqual(
            policy.parent_model_id, "maker_priority_v1_27_candidate",
        )
        self.assertIsNone(
            parent.minimum_rising_base_short_reliable_reference_edge,
        )
        self.assertEqual(
            policy.minimum_rising_base_short_reliable_reference_edge, 0.20,
        )
        reliable = MakerDecisionContext(
            reference_price=136.899,
            reference_source="intraday_trade_anchor",
            reliable_anchor=True,
            spread=0.348,
            bid_support_bonds=5_000.0,
            ask_supply_bonds=20_000.0,
            wall_threshold_bonds=5_000.0,
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v128-live-fair.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                # The older five-minute reference and thick supply pass v1.27,
                # but a rising-state customer-base sale below the current
                # reliable trade anchor is not a high sale.
                self.assertTrue(engine._base_high_sell_is_safe(
                    136.788, reliable, parent, "rising",
                    recent_trade_reference=136.402,
                ))
                self.assertFalse(engine._base_high_sell_is_safe(
                    136.788, reliable, policy, "rising",
                    recent_trade_reference=136.402,
                ))

                # A genuine 0.20-yuan live-fair edge plus the existing recent-
                # trade premium and supply remains authorized.
                self.assertTrue(engine._base_high_sell_is_safe(
                    137.099, reliable, policy, "rising",
                    recent_trade_reference=136.602,
                ))

                # Do not promote a wide-book midpoint to a reliable veto, and
                # preserve a separately proven repeated high/low corridor.
                self.assertTrue(engine._base_high_sell_is_safe(
                    136.788, replace(reliable, reliable_anchor=False),
                    policy, "rising", recent_trade_reference=136.402,
                ))
                self.assertTrue(engine._base_high_sell_is_safe(
                    136.788, reliable, policy, "possible_rise",
                    repeated_turn_replenishment_price=136.400,
                    recent_trade_reference=136.402,
                ))
            finally:
                store.close()

    def test_priority_v129_replenishes_a_profitable_base_short_at_visible_bid(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_V128_CANDIDATE
        policy = PRIORITY_POLICY_V129_CANDIDATE
        self.assertEqual(
            policy.parent_model_id, "maker_priority_v1_28_candidate",
        )
        self.assertFalse(
            parent.enable_profitable_visible_bid_base_replenishment,
        )
        self.assertTrue(
            policy.enable_profitable_visible_bid_base_replenishment,
        )

        moment = datetime(2026, 8, 14, 9, 56, 17, tzinfo=SHANGHAI)
        assessment = MarketAssessment(
            reference_price=136.450,
            reference_low=136.201,
            reference_high=136.599,
            reference_source="persistent_inside_market",
            reference_confidence=0.55,
            state="possible_fall",
            state_score=-1,
            state_confidence=0.8,
            recent_buy_bonds=2_380.0,
            recent_sell_bonds=3_000.0,
            midpoint_change=0.0,
            short_ask_change=0.0,
            largest_ask_gap=0.10,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=135.611,
            iron_floor_bonds=52_000.0,
            evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=136.450,
            reference_source="persistent_inside_market",
            reliable_anchor=False,
            spread=0.398,
            bid_support_bonds=6_000.0,
            ask_supply_bonds=11_000.0,
            wall_threshold_bonds=5_000.0,
        )

        def planned_buy(candidate_policy, *, bid_bonds=1_000.0, sale=136.995):
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(Path(temp) / "maker-v129-bid.sqlite3")
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, priority_policy=candidate_policy,
                    )
                    engine._start_date(moment.date().isoformat())
                    engine.observed_market_trade = True
                    account = engine.accounts["maker_v01_priority"]
                    account.inventory = 0.0
                    account.lots.clear()
                    account.replenishment_quantity = 1_000.0
                    account.replenishment_sale_value = sale * 1_000.0
                    tick = self._replay_tick(
                        moment, last=136.599, bid=136.201, ask=136.599,
                        bid_bonds=bid_bonds,
                    )
                    with patch.object(
                        engine, "_decision_context", return_value=context,
                    ):
                        engine._refresh_orders(
                            account, tick, assessment, persist=True,
                        )
                    self.assertIsNotNone(account.buy_order)
                    return account.buy_order.kind, account.buy_order.limit_price
                finally:
                    store.close()

        # The parent remains pinned to the remembered deep wall.  The
        # candidate quotes one tick ahead of the currently visible full-lot
        # bid while preserving at least the 0.50-yuan replenishment edge.
        self.assertEqual(
            planned_buy(parent), ("inventory_replenish", 135.911),
        )
        self.assertEqual(
            planned_buy(policy),
            ("profitable_visible_bid_base_replenish", 136.202),
        )

        # Neither a sub-lot top bid nor less than 0.50 yuan of locked profit
        # receives the new permission.
        self.assertEqual(
            planned_buy(policy, bid_bonds=999.0),
            ("inventory_replenish", 135.911),
        )
        self.assertEqual(
            planned_buy(policy, sale=136.650),
            ("inventory_replenish", 135.911),
        )

        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v129-sequence.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.inventory = 0.0
                account.lots.clear()
                account.replenishment_quantity = 1_000.0
                account.replenishment_sale_value = 135_196.0
                account.medium_wall_supported_replenishment_quantity = 1_000.0
                account.medium_wall_supported_replenishment_sale_value = 135_196.0
                account.last_base_short_sale_ts_ms = (
                    int(moment.timestamp() * 1000) - 60_000
                )
                old_buy = engine._new_order(
                    account, self._replay_tick(
                        moment - timedelta(seconds=1),
                        last=135.197, bid=134.661, ask=134.899,
                    ),
                    side="buy",
                    kind="profitable_visible_bid_base_replenish",
                    lot_id=None, price=134.662, quantity=1_000.0,
                    queue_ahead=0.0, target_price=None, persist=True,
                )
                account.buy_order = old_buy
                recovery_tick = self._replay_tick(
                    moment, last=135.197, bid=134.661, ask=134.899,
                )
                recovery = replace(
                    assessment,
                    reference_price=134.899,
                    reference_low=134.661,
                    reference_high=134.899,
                    reference_source="intraday_trade_anchor",
                    state="possible_rise",
                    state_score=1,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                )

                # The candidate does not globally remove v1.26's anomalous-
                # distance guard.  Only a recent recovery produced by this
                # exact permission, followed by a fresh base sale, may close
                # the resulting medium short at the ordinary 0.20-yuan profit.
                self.assertFalse(
                    engine._active_medium_base_short_replenishment(
                        account, recovery_tick, recovery, persist=True,
                        received_ts_ns=recovery_tick.market_ts_ms * 1_000_000,
                    )
                )
                account.last_profitable_visible_bid_replenishment_ts_ms = (
                    recovery_tick.market_ts_ms - 240_000
                )
                self.assertTrue(
                    engine._active_medium_base_short_replenishment(
                        account, recovery_tick, recovery, persist=True,
                        received_ts_ns=recovery_tick.market_ts_ms * 1_000_000,
                    )
                )
                self.assertEqual(account.inventory, 1_000.0)
            finally:
                store.close()

    def test_priority_v130_registers_the_underlying_mapping_correction(self) -> None:
        self.assertEqual(
            PRIORITY_POLICY_V130_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_V129_CANDIDATE.model_id,
        )
        self.assertEqual(
            replace(
                PRIORITY_POLICY_V130_CANDIDATE,
                model_id=PRIORITY_POLICY_V129_CANDIDATE.model_id,
                model_version=PRIORITY_POLICY_V129_CANDIDATE.model_version,
                parent_model_id=PRIORITY_POLICY_V129_CANDIDATE.parent_model_id,
            ),
            PRIORITY_POLICY_V129_CANDIDATE,
        )

    def test_priority_v131_exits_only_extra_inventory_on_confirmed_falling_pressure(
        self,
    ) -> None:
        self.assertEqual(
            PRIORITY_POLICY_V131_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_V130_CANDIDATE.model_id,
        )
        self.assertTrue(
            PRIORITY_POLICY_V131_CANDIDATE
                .enable_confirmed_falling_near_flat_extra_exit
        )
        self.assertFalse(
            PRIORITY_POLICY_V130_CANDIDATE
                .enable_confirmed_falling_near_flat_extra_exit
        )

        def run_case(
            database: Path, *, policy=PRIORITY_POLICY_V131_CANDIDATE,
            state: str = "falling", recent_buy_bonds: float = 5_000.0,
            recent_sell_bonds: float = 39_660.0,
            midpoint_change: float = -0.2175,
            bid_bonds: float = 1_810.0, add_extra: bool = True,
            confirmed_rise: bool = False,
        ) -> tuple[float, list[str]]:
            config = test_config(database)
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                moment = datetime(
                    2026, 8, 14, 14, 50, 14, tzinfo=SHANGHAI,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                entry_tick = self._replay_tick(
                    moment - timedelta(seconds=120),
                    last=135.607, bid=135.605, ask=135.899,
                )
                if add_extra:
                    order = engine._new_order(
                        account, entry_tick, side="buy",
                        kind="low_bid_reversion", lot_id=None,
                        price=135.607, quantity=1_000.0,
                        queue_ahead=0.0, target_price=None, persist=True,
                    )
                    account.buy_order = order
                    engine._fill_buy(
                        account, entry_tick, order, 1_000.0,
                        entry_tick.market_ts_ms * 1_000_000,
                        kind="low_bid_reversion", target_price=None,
                        persist=True,
                    )
                tick = self._replay_tick(
                    moment, last=135.605, bid=135.605, ask=135.999,
                    bid_bonds=bid_bonds, trade_bonds=190.0,
                    inferred_side="buy",
                )
                if confirmed_rise:
                    engine.last_confirmed_rise_trade_ts_ms = (
                        tick.market_ts_ms - 3_000
                    )
                    engine.last_confirmed_rise_price = 135.700
                assessment = MarketAssessment(
                    reference_price=135.609,
                    reference_low=135.609,
                    reference_high=135.999,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state=state,
                    state_score=-3,
                    state_confidence=0.95,
                    recent_buy_bonds=recent_buy_bonds,
                    recent_sell_bonds=recent_sell_bonds,
                    midpoint_change=midpoint_change,
                    short_ask_change=0.0,
                    largest_ask_gap=0.299,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("确认下跌且累计卖压显著",),
                )
                engine._active_inventory_risk_exit(
                    account, tick, assessment, persist=True,
                    received_ts_ns=tick.market_ts_ms * 1_000_000,
                )
                reasons = [
                    row[0] for row in store.connection.execute(
                        "SELECT fill_reason FROM maker_paper_fills "
                        "WHERE side='sell' ORDER BY id"
                    )
                ]
                return account.inventory, reasons
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(
                run_case(root / "v130-parent.sqlite3", policy=PRIORITY_POLICY_V130_CANDIDATE),
                (2_000.0, []),
            )
            self.assertEqual(
                run_case(root / "v131-positive.sqlite3"),
                (1_000.0, ["active_confirmed_falling_near_flat_exit"]),
            )
            negative_cases = (
                ("possible-fall", {"state": "possible_fall"}),
                ("weak-ratio", {
                    "recent_buy_bonds": 9_000.0,
                    "recent_sell_bonds": 39_660.0,
                }),
                ("small-midpoint-drop", {"midpoint_change": -0.099}),
                ("thin-bid", {"bid_bonds": 810.0}),
                ("base-only", {"add_extra": False}),
                ("confirmed-rise", {"confirmed_rise": True}),
            )
            for name, overrides in negative_cases:
                with self.subTest(name=name):
                    inventory, reasons = run_case(
                        root / f"v131-{name}.sqlite3", **overrides,
                    )
                    self.assertEqual(
                        inventory, 1_000.0 if name == "base-only" else 2_000.0,
                    )
                    self.assertEqual(reasons, [])

    def test_priority_v132_replenishes_customer_base_at_ordinary_live_edge(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_V130_CANDIDATE
        policy = PRIORITY_POLICY_V132_CANDIDATE
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertIsNone(
            parent.minimum_profitable_visible_bid_base_replenishment_edge_override,
        )
        self.assertEqual(
            policy.minimum_profitable_visible_bid_base_replenishment_edge_override,
            0.20,
        )
        self.assertFalse(
            policy.enable_confirmed_falling_near_flat_extra_exit,
        )

        moment = datetime(2026, 8, 14, 10, 21, 53, tzinfo=SHANGHAI)
        assessment = MarketAssessment(
            reference_price=136.568,
            reference_low=136.501,
            reference_high=136.797,
            reference_source="intraday_trade_anchor",
            reference_confidence=0.9,
            state="possible_rise",
            state_score=1,
            state_confidence=0.7,
            recent_buy_bonds=14_000.0,
            recent_sell_bonds=0.0,
            midpoint_change=0.0,
            short_ask_change=0.0,
            largest_ask_gap=0.10,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=135.000,
            iron_floor_bonds=50_000.0,
            evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=136.568,
            reference_source="intraday_trade_anchor",
            reliable_anchor=True,
            spread=0.197,
            bid_support_bonds=8_000.0,
            ask_supply_bonds=6_000.0,
            wall_threshold_bonds=5_000.0,
        )

        def planned_buy(candidate_policy, *, sale=136.801, bid_bonds=1_000.0):
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(Path(temp) / "maker-v132-bid.sqlite3")
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, priority_policy=candidate_policy,
                    )
                    engine._start_date(moment.date().isoformat())
                    engine.observed_market_trade = True
                    account = engine.accounts["maker_v01_priority"]
                    account.inventory = 0.0
                    account.lots.clear()
                    account.replenishment_quantity = 1_000.0
                    account.replenishment_sale_value = sale * 1_000.0
                    tick = self._replay_tick(
                        moment, last=136.601, bid=136.601, ask=136.798,
                        bid_bonds=bid_bonds,
                    )
                    with patch.object(
                        engine, "_decision_context", return_value=context,
                    ):
                        engine._refresh_orders(
                            account, tick, assessment, persist=True,
                        )
                    self.assertIsNotNone(account.buy_order)
                    return account.buy_order.kind, account.buy_order.limit_price
                finally:
                    store.close()

        # v1.30 retains its 0.50-yuan deep-target override.  v1.32 treats the
        # ordinary 0.20-yuan live profit as sufficient to move the passive
        # replenishment order to the visible bid.
        self.assertEqual(
            planned_buy(parent), ("inventory_replenish", 135.3),
        )
        self.assertEqual(
            planned_buy(policy),
            ("profitable_visible_bid_base_replenish", 136.601),
        )

        # The permission is not rounded down into a smaller profit and still
        # requires a full standard lot at the visible bid.
        self.assertEqual(
            planned_buy(policy, sale=136.799),
            ("inventory_replenish", 135.3),
        )
        self.assertEqual(
            planned_buy(policy, bid_bonds=999.0),
            ("inventory_replenish", 135.3),
        )

    def test_priority_v133_requires_an_uninterrupted_rising_buy_sequence(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_V130_CANDIDATE
        policy = PRIORITY_POLICY_V133_CANDIDATE
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertFalse(
            parent.enable_confirmed_rising_buy_sequence_base_short_stop,
        )
        self.assertTrue(
            policy.enable_confirmed_rising_buy_sequence_base_short_stop,
        )
        self.assertEqual(
            policy.confirmed_rising_buy_sequence_base_short_stop_seconds, 60,
        )

        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "maker-v133-sequence.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                moment = datetime(2026, 8, 13, 10, 7, 57, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                base_lot = next(iter(account.lots.values()))
                sale_tick = self._replay_tick(
                    moment, last=137.293, bid=136.100, ask=137.292,
                )
                sale_order = engine._new_order(
                    account, sale_tick, side="sell", kind="inventory_exit",
                    lot_id=base_lot.db_id, price=137.292, quantity=1_000.0,
                    queue_ahead=0.0, target_price=137.292, persist=True,
                )
                engine._fill_sell(
                    account, sale_tick, sale_order, 1_000.0,
                    sale_tick.market_ts_ms * 1_000_000, persist=True,
                )
                old_buy = engine._new_order(
                    account, sale_tick, side="buy",
                    kind="inventory_replenish", lot_id=None,
                    price=136.101, quantity=1_000.0, queue_ahead=0.0,
                    target_price=None, persist=True,
                )
                account.buy_order = old_buy
                stale_reference = MarketAssessment(
                    reference_price=136.980,
                    reference_low=136.900,
                    reference_high=137.000,
                    reference_source="intraday_trade_anchor",
                    reference_confidence=0.7,
                    state="rising",
                    state_score=2,
                    state_confidence=0.8,
                    recent_buy_bonds=20_000.0,
                    recent_sell_bonds=7_000.0,
                    midpoint_change=0.20,
                    short_ask_change=0.0,
                    largest_ask_gap=0.50,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=(),
                )

                def buy_tick(seconds: int, bonds: float) -> ReplayTick:
                    return self._replay_tick(
                        moment + timedelta(seconds=seconds),
                        last=137.294, bid=137.291, ask=137.294,
                        trade_bonds=bonds, inferred_side="buy",
                        ask_bonds=2_000.0,
                    )

                first_buy = buy_tick(18, 3_000.0)
                engine._update_base_short_rising_buy_sequence(
                    account, first_buy,
                )
                self.assertEqual(
                    account.base_short_rising_buy_sequence_bonds, 3_000.0,
                )
                self.assertFalse(
                    engine._active_confirmed_rising_near_flat_base_short_stop(
                        account, first_buy, stale_reference, persist=True,
                        received_ts_ns=first_buy.market_ts_ms * 1_000_000,
                    )
                )

                intervening_sell = self._replay_tick(
                    moment + timedelta(seconds=24),
                    last=136.900, bid=136.900, ask=137.294,
                    trade_bonds=1_000.0, inferred_side="sell",
                )
                engine._update_base_short_rising_buy_sequence(
                    account, intervening_sell,
                )
                self.assertEqual(
                    account.base_short_rising_buy_sequence_bonds, 0.0,
                )

                second_buy = buy_tick(30, 3_000.0)
                engine._update_base_short_rising_buy_sequence(
                    account, second_buy,
                )
                self.assertFalse(
                    engine._active_confirmed_rising_near_flat_base_short_stop(
                        account, second_buy, stale_reference, persist=True,
                        received_ts_ns=second_buy.market_ts_ms * 1_000_000,
                    )
                )
                final_buy = buy_tick(39, 2_000.0)
                engine._update_base_short_rising_buy_sequence(account, final_buy)
                self.assertEqual(
                    account.base_short_rising_buy_sequence_bonds, 5_000.0,
                )

                expired_buy = buy_tick(61, 1_000.0)
                self.assertFalse(
                    engine._active_confirmed_rising_near_flat_base_short_stop(
                        account, expired_buy, stale_reference, persist=True,
                        received_ts_ns=expired_buy.market_ts_ms * 1_000_000,
                    )
                )

                account.policy = parent
                self.assertFalse(
                    engine._active_confirmed_rising_near_flat_base_short_stop(
                        account, final_buy, stale_reference, persist=True,
                        received_ts_ns=final_buy.market_ts_ms * 1_000_000,
                    )
                )
                account.policy = policy
                self.assertTrue(
                    engine._active_confirmed_rising_near_flat_base_short_stop(
                        account, final_buy, stale_reference, persist=True,
                        received_ts_ns=final_buy.market_ts_ms * 1_000_000,
                    )
                )
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(
                    account.base_short_rising_buy_sequence_bonds, 0.0,
                )
                fill = store.connection.execute(
                    "SELECT price,quantity,fill_reason FROM maker_paper_fills "
                    "WHERE fill_reason="
                    "'active_confirmed_rising_buy_sequence_base_short_stop'"
                ).fetchone()
                self.assertEqual(float(fill["price"]), 137.294)
                self.assertEqual(float(fill["quantity"]), 1_000.0)
                cancelled = store.connection.execute(
                    "SELECT status,cancel_reason FROM maker_paper_orders "
                    "WHERE id=?", (old_buy.db_id,),
                ).fetchone()
                self.assertEqual(cancelled["status"], "cancelled")
                self.assertEqual(
                    cancelled["cancel_reason"],
                    "confirmed_rising_buy_sequence_base_short_stop",
                )
                self.assertFalse(
                    QUEUE_POLICY_V116_CANDIDATE
                        .enable_confirmed_rising_buy_sequence_base_short_stop,
                )
                self.assertFalse(
                    WINDFALL_POLICY_V11_CANDIDATE
                        .enable_confirmed_rising_buy_sequence_base_short_stop,
                )
            finally:
                store.close()

    def test_priority_v134_adds_only_a_persistent_wall_supported_extra_entry(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_V133_CANDIDATE
        policy = PRIORITY_POLICY_V134_CANDIDATE
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertFalse(
            parent.enable_persistent_wall_supported_falling_extra_entry,
        )
        self.assertTrue(
            policy.enable_persistent_wall_supported_falling_extra_entry,
        )
        self.assertFalse(
            QUEUE_POLICY_V10.enable_persistent_wall_supported_falling_extra_entry,
        )
        self.assertFalse(
            WINDFALL_POLICY_V10
                .enable_persistent_wall_supported_falling_extra_entry,
        )

        moment = datetime(2026, 8, 14, 14, 49, 45, tzinfo=SHANGHAI)
        moment_ms = int(moment.timestamp() * 1_000)
        live_bids = (
            (135.401, 2_000.0),
            (135.400, 9_000.0),
            (135.051, 8_000.0),
            (135.050, 74_000.0),
            (135.001, 10_000.0),
        )

        def quoted_buy(
            database: Path, candidate_policy, *,
            state: str = "falling", wall_bonds: float = 9_000.0,
            wall_seconds: int = 30, high_buy_bonds: float = 1_000.0,
            high_buy_age_seconds: int = 84, ask_bonds: float = 1_000.0,
            inventory: float = 1_000.0, recent_exit_age_seconds: int | None = None,
            bond_code: str = "132026.SH",
        ) -> tuple[str | None, float | None]:
            config = test_config(database)
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, bond_code=bond_code,
                    priority_policy=candidate_policy,
                )
                engine._start_date(moment.date().isoformat())
                account = next(
                    item for item in engine.accounts.values()
                    if item.fill_mode == "priority"
                )
                account.inventory = inventory
                if inventory <= 1e-9:
                    account.lots.clear()
                    account.replenishment_quantity = 1_000.0
                    account.replenishment_sale_value = 136_000.0
                if recent_exit_age_seconds is not None:
                    account.last_falling_profitable_exit_price = 135.501
                    account.last_falling_profitable_exit_ts_ms = (
                        moment_ms - recent_exit_age_seconds * 1_000
                    )
                bids = tuple(
                    (
                        price,
                        wall_bonds if abs(price - 135.400) < 1e-9 else bonds,
                    )
                    for price, bonds in live_bids
                )
                tick = replace(
                    self._replay_tick(
                        moment, last=135.401, bid=135.401, ask=135.602,
                        ask_bonds=ask_bonds,
                    ),
                    code=bond_code,
                    bids=bids,
                )
                engine.visible_bid_wall_first_seen_ms = {
                    135.400: moment_ms - wall_seconds * 1_000,
                    135.051: moment_ms - 300_000,
                    135.050: moment_ms - 300_000,
                    135.001: moment_ms - 300_000,
                }
                if high_buy_bonds > 0:
                    engine.analyzer.trade_evidence.append(TradeEvidence(
                        market_ts_ms=(
                            moment_ms - high_buy_age_seconds * 1_000
                        ),
                        price=135.625,
                        bonds=high_buy_bonds,
                        transactions=1,
                        side="buy",
                    ))
                assessment = MarketAssessment(
                    reference_price=135.502,
                    reference_low=135.400,
                    reference_high=135.625,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state=state,
                    state_score=-3 if state == "falling" else 0,
                    state_confidence=0.75,
                    recent_buy_bonds=high_buy_bonds,
                    recent_sell_bonds=30_000.0,
                    midpoint_change=-0.20,
                    short_ask_change=-0.20,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=135.051,
                    iron_floor_bonds=92_000.0,
                    evidence=(),
                )
                context = MakerDecisionContext(
                    reference_price=135.502,
                    reference_source="persistent_inside_market",
                    reliable_anchor=False,
                    spread=0.201,
                    bid_support_bonds=11_000.0,
                    ask_supply_bonds=ask_bonds,
                    wall_threshold_bonds=5_000.0,
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )
                order = account.buy_order
                return (
                    order.kind if order is not None else None,
                    order.limit_price if order is not None else None,
                )
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(
                quoted_buy(root / "v134-t141.sqlite3", policy),
                ("persistent_wall_supported_falling_entry", 135.402),
            )
            parent_result = quoted_buy(
                root / "v133-parent.sqlite3", parent,
            )
            self.assertNotEqual(
                parent_result[0], "persistent_wall_supported_falling_entry",
            )
            self.assertNotEqual(parent_result[1], 135.402)
            self.assertEqual(
                quoted_buy(
                    root / "v134-jiangtong.sqlite3", policy,
                    bond_code="132024.SH",
                ),
                ("persistent_wall_supported_falling_entry", 135.402),
            )

            negative_cases = {
                "wall-too-small": {"wall_bonds": 4_999.0},
                "wall-too-fresh": {"wall_seconds": 29},
                "no-high-buy": {"high_buy_bonds": 0.0},
                "stale-high-buy": {"high_buy_age_seconds": 121},
                "small-high-buy": {"high_buy_bonds": 999.0},
                "thin-ask": {"ask_bonds": 999.0},
                "stable": {"state": "stable"},
                "rising": {"state": "rising"},
                "customer-base-short": {"inventory": 0.0},
                "full-inventory": {"inventory": 2_000.0},
                "risk-exit-cooldown": {"recent_exit_age_seconds": 60},
            }
            for name, overrides in negative_cases.items():
                with self.subTest(name=name):
                    self.assertNotEqual(
                        quoted_buy(
                            root / f"v134-{name}.sqlite3",
                            policy,
                            **overrides,
                        )[0],
                        "persistent_wall_supported_falling_entry",
                    )

    def test_priority_v137_takes_only_a_supported_collapsed_midpoint_offer(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_V134_CANDIDATE
        policy = PRIORITY_POLICY_V137_CANDIDATE
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertFalse(
            parent.enable_supported_current_midpoint_collapse_extra_entry,
        )
        self.assertTrue(
            policy.enable_supported_current_midpoint_collapse_extra_entry,
        )
        self.assertFalse(
            QUEUE_POLICY_V10
                .enable_supported_current_midpoint_collapse_extra_entry,
        )
        self.assertFalse(
            WINDFALL_POLICY_V10
                .enable_supported_current_midpoint_collapse_extra_entry,
        )

        moment = datetime(2026, 8, 14, 13, 46, 26, tzinfo=SHANGHAI)
        moment_ms = int(moment.timestamp() * 1_000)

        def run_case(
            database: Path, candidate_policy=policy, *,
            bond_code: str = "132024.SH", state: str = "falling",
            reference_source: str = "current_midpoint",
            previous_reference: float = 135.905,
            current_reference: float = 135.630,
            wall_bonds: float = 6_000.0, wall_seconds: int = 15,
            high_buy_bonds: float = 1_000.0,
            existing_buy: bool = True, inventory: float = 1_000.0,
            risk_exit_age_seconds: int | None = None,
        ) -> tuple[float, list[str]]:
            config = test_config(database)
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, bond_code=bond_code,
                    priority_policy=candidate_policy,
                )
                engine._start_date(moment.date().isoformat())
                account = next(
                    item for item in engine.accounts.values()
                    if item.fill_mode == "priority"
                )
                account.inventory = inventory
                if inventory <= 1e-9:
                    account.lots.clear()
                    account.replenishment_quantity = 1_000.0
                    account.replenishment_sale_value = 136_000.0
                tick = replace(
                    self._replay_tick(
                        moment, last=136.251, bid=135.611, ask=135.649,
                        ask_bonds=1_000.0,
                    ),
                    code=bond_code,
                    bids=(
                        (135.611, 1_000.0),
                        (135.606, wall_bonds),
                        (
                            135.605,
                            43_000.0 if wall_bonds >= 5_000.0
                            else wall_bonds,
                        ),
                    ),
                    asks=((135.649, 1_000.0), (136.199, 2_000.0)),
                )
                if existing_buy:
                    account.buy_order = engine._new_order(
                        account, replace(
                            tick,
                            market_ts_ms=moment_ms - 15_000,
                            market_time="13:46:11.000",
                        ),
                        side="buy", kind="low_bid_reversion", lot_id=None,
                        price=135.612, quantity=1_000.0, queue_ahead=0.0,
                        target_price=None, persist=True,
                    )
                engine.visible_bid_wall_first_seen_ms = {
                    135.606: moment_ms - wall_seconds * 1_000,
                    135.605: moment_ms - 60_000,
                }
                engine.previous_intraday_working_reference = (
                    previous_reference
                )
                engine.previous_intraday_working_reference_ts_ms = (
                    moment_ms - 3_000
                )
                if high_buy_bonds > 0:
                    engine.analyzer.trade_evidence.append(TradeEvidence(
                        market_ts_ms=moment_ms - 15_000,
                        price=136.251,
                        bonds=high_buy_bonds,
                        transactions=1,
                        side="buy",
                    ))
                if risk_exit_age_seconds is not None:
                    account.last_falling_profitable_exit_price = 135.800
                    account.last_falling_profitable_exit_ts_ms = (
                        moment_ms - risk_exit_age_seconds * 1_000
                    )
                engine.observed_market_trade = True
                assessment = MarketAssessment(
                    reference_price=current_reference,
                    reference_low=135.611,
                    reference_high=135.649,
                    reference_source=reference_source,
                    reference_confidence=0.35,
                    state=state,
                    state_score=-4 if state == "falling" else 0,
                    state_confidence=0.95,
                    recent_buy_bonds=2_000.0,
                    recent_sell_bonds=37_000.0,
                    midpoint_change=-0.325,
                    short_ask_change=-0.625,
                    largest_ask_gap=0.55,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=135.611,
                    iron_floor_bonds=50_000.0,
                    evidence=(),
                )
                context = MakerDecisionContext(
                    reference_price=current_reference,
                    reference_source=reference_source,
                    reliable_anchor=False,
                    spread=0.038,
                    bid_support_bonds=50_000.0,
                    ask_supply_bonds=1_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._active_discount_entry(
                        account, tick, assessment, persist=True,
                    )
                reasons = [
                    str(row["fill_reason"])
                    for row in store.connection.execute(
                        "SELECT fill_reason FROM maker_paper_fills "
                        "ORDER BY id"
                    ).fetchall()
                ]
                return account.inventory, reasons
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for bond_code in ("132026.SH", "132024.SH"):
                with self.subTest(bond_code=bond_code):
                    inventory, reasons = run_case(
                        root / f"v137-{bond_code[:6]}.sqlite3",
                        bond_code=bond_code,
                    )
                    self.assertEqual(inventory, 2_000.0)
                    self.assertEqual(
                        reasons, ["active_supported_ask_collapse_entry"],
                    )

            parent_inventory, parent_reasons = run_case(
                root / "v134-parent.sqlite3", parent,
            )
            self.assertEqual(parent_inventory, 1_000.0)
            self.assertEqual(parent_reasons, [])

            negative_cases = {
                "no-existing-buy": {"existing_buy": False},
                "small-wall": {"wall_bonds": 4_999.0},
                "fresh-wall": {"wall_seconds": 14},
                "no-high-buy": {"high_buy_bonds": 0.0},
                "small-high-buy": {"high_buy_bonds": 999.0},
                "stable": {"state": "stable"},
                "anchored-reference": {
                    "reference_source": "intraday_trade_anchor",
                },
                "small-dislocation": {
                    "previous_reference": 135.800,
                },
                "customer-base-short": {"inventory": 0.0},
                "full-inventory": {"inventory": 2_000.0},
                "risk-exit-cooldown": {"risk_exit_age_seconds": 60},
            }
            for name, overrides in negative_cases.items():
                with self.subTest(name=name):
                    inventory, reasons = run_case(
                        root / f"v137-{name}.sqlite3", **overrides,
                    )
                    self.assertNotIn(
                        "active_supported_ask_collapse_entry", reasons,
                    )

    def test_priority_v138_quotes_only_a_high_side_validated_supported_corridor(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_V137_CANDIDATE
        policy = PRIORITY_POLICY_V138_CANDIDATE
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertFalse(
            parent.enable_high_side_validated_supported_corridor_entry,
        )
        self.assertTrue(
            policy.enable_high_side_validated_supported_corridor_entry,
        )
        self.assertFalse(
            QUEUE_POLICY_V10
                .enable_high_side_validated_supported_corridor_entry,
        )
        self.assertFalse(
            WINDFALL_POLICY_V10
                .enable_high_side_validated_supported_corridor_entry,
        )

        moment = datetime(2026, 8, 14, 11, 12, 21, tzinfo=SHANGHAI)

        def quoted_buy(
            database: Path, candidate_policy=policy, *,
            bond_code: str = "132026.SH", state: str = "possible_fall",
            inferred_side: str = "buy", trade_bonds: float = 1_000.0,
            bid: float = 136.476, ask: float = 136.781,
            last: float = 136.762, reference_price: float = 136.629,
            reference_source: str = "current_midpoint",
            bid_support_bonds: float = 6_860.0,
            ask_supply_bonds: float = 6_000.0,
            midpoint_change: float = 0.018,
            short_ask_change: float = 0.0,
            inventory: float = 1_000.0,
            risk_exit_age_seconds: int | None = None,
        ) -> tuple[str | None, float | None]:
            config = test_config(database)
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, bond_code=bond_code,
                    priority_policy=candidate_policy,
                )
                engine._start_date(moment.date().isoformat())
                account = next(
                    item for item in engine.accounts.values()
                    if item.fill_mode == "priority"
                )
                account.inventory = inventory
                if inventory <= 1e-9:
                    account.lots.clear()
                    account.replenishment_quantity = 1_000.0
                    account.replenishment_sale_value = 136_900.0
                if risk_exit_age_seconds is not None:
                    account.last_falling_profitable_exit_price = 136.600
                    account.last_falling_profitable_exit_ts_ms = (
                        int(moment.timestamp() * 1_000)
                        - risk_exit_age_seconds * 1_000
                    )
                tick = replace(
                    self._replay_tick(
                        moment, last=last, bid=bid, ask=ask,
                        bid_bonds=2_000.0, ask_bonds=2_000.0,
                        trade_bonds=trade_bonds,
                        inferred_side=inferred_side,
                    ),
                    code=bond_code,
                )
                context = MakerDecisionContext(
                    reference_price=reference_price,
                    reference_source=reference_source,
                    reliable_anchor=(
                        reference_source == "intraday_trade_anchor"
                    ),
                    spread=ask - bid,
                    bid_support_bonds=bid_support_bonds,
                    ask_supply_bonds=ask_supply_bonds,
                    wall_threshold_bonds=5_000.0,
                )
                assessment = MarketAssessment(
                    reference_price=reference_price,
                    reference_low=bid,
                    reference_high=ask,
                    reference_source=reference_source,
                    reference_confidence=0.55,
                    state=state,
                    state_score=-1 if state == "possible_fall" else 0,
                    state_confidence=0.62,
                    recent_buy_bonds=trade_bonds,
                    recent_sell_bonds=4_000.0,
                    midpoint_change=midpoint_change,
                    short_ask_change=short_ask_change,
                    largest_ask_gap=0.04,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("高侧真实买入且低侧仍有承托",),
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )
                return (
                    account.buy_order.kind if account.buy_order else None,
                    account.buy_order.limit_price
                    if account.buy_order else None,
                )
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for bond_code in ("132026.SH", "132024.SH"):
                with self.subTest(bond_code=bond_code):
                    self.assertEqual(
                        quoted_buy(
                            root / f"v138-{bond_code[:6]}.sqlite3",
                            bond_code=bond_code,
                        ),
                        ("high_side_validated_corridor_entry", 136.477),
                    )

            self.assertNotEqual(
                quoted_buy(root / "v137-parent.sqlite3", parent)[0],
                "high_side_validated_corridor_entry",
            )
            negative_cases = {
                "customer-base-short": {"inventory": 0.0},
                "full-inventory": {"inventory": 2_000.0},
                "stable-is-allowed-control": {"state": "stable"},
                "possible-rise": {"state": "possible_rise"},
                "falling": {"state": "falling"},
                "sell-print": {"inferred_side": "sell"},
                "small-buy": {"trade_bonds": 999.0},
                "thin-support": {"bid_support_bonds": 4_999.0},
                "thin-supply": {"ask_supply_bonds": 2_999.0},
                "narrow-corridor": {"ask": 136.650, "last": 136.650},
                "wide-corridor": {"ask": 137.000, "last": 137.000},
                "low-high-print": {"last": 136.600},
                "stale-close": {"reference_source": "previous_close"},
                "reference-below-low": {"reference_price": 136.450},
                "reference-too-far": {"reference_price": 136.700},
                "moving-midpoint": {"midpoint_change": 0.051},
                "falling-ask": {"short_ask_change": -0.051},
                "risk-exit-cooldown": {"risk_exit_age_seconds": 60},
            }
            for name, overrides in negative_cases.items():
                if name == "stable-is-allowed-control":
                    expected = "high_side_validated_corridor_entry"
                else:
                    expected = None
                with self.subTest(name=name):
                    kind, _ = quoted_buy(
                        root / f"v138-{name}.sqlite3", **overrides,
                    )
                    if expected is None:
                        self.assertNotEqual(
                            kind, "high_side_validated_corridor_entry",
                        )
                    else:
                        self.assertEqual(kind, expected)

    def test_priority_v139_quotes_only_a_persistent_two_sided_wall_corridor(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_V138_CANDIDATE
        policy = PRIORITY_POLICY_V139_CANDIDATE
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertFalse(
            parent.enable_persistent_two_sided_wall_corridor_entry,
        )
        self.assertTrue(
            policy.enable_persistent_two_sided_wall_corridor_entry,
        )
        self.assertFalse(
            QUEUE_POLICY_V10
                .enable_persistent_two_sided_wall_corridor_entry,
        )
        self.assertFalse(
            WINDFALL_POLICY_V10
                .enable_persistent_two_sided_wall_corridor_entry,
        )

        moment = datetime(2026, 8, 14, 13, 22, 17, tzinfo=SHANGHAI)

        def quoted_buy(
            database: Path, candidate_policy=policy, *,
            bond_code: str = "132024.SH", state: str = "possible_fall",
            bid: float = 136.051, ask: float = 136.239,
            wall_price: float = 136.010, wall_bonds: float = 5_000.0,
            wall_seconds: int = 60, reference_price: float = 136.147,
            reference_source: str = "intraday_trade_anchor",
            ask_supply_bonds: float = 3_000.0,
            recent_buy_bonds: float = 9_000.0,
            recent_sell_bonds: float = 14_000.0,
            midpoint_change: float = -0.0305,
            short_ask_change: float = -0.008,
            inventory: float = 1_000.0,
            risk_exit_age_seconds: int | None = None,
        ) -> tuple[str | None, float | None]:
            config = test_config(database)
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, bond_code=bond_code,
                    priority_policy=candidate_policy,
                )
                engine._start_date(moment.date().isoformat())
                account = next(
                    item for item in engine.accounts.values()
                    if item.fill_mode == "priority"
                )
                account.inventory = inventory
                if inventory <= 1e-9:
                    account.lots.clear()
                    account.replenishment_quantity = 1_000.0
                    account.replenishment_sale_value = 136_900.0
                if risk_exit_age_seconds is not None:
                    account.last_falling_profitable_exit_price = 136.200
                    account.last_falling_profitable_exit_ts_ms = (
                        int(moment.timestamp() * 1_000)
                        - risk_exit_age_seconds * 1_000
                    )
                engine.visible_bid_wall_first_seen_ms[
                    round(wall_price, 6)
                ] = (
                    int(moment.timestamp() * 1_000)
                    - wall_seconds * 1_000
                )
                tick = replace(
                    self._replay_tick(
                        moment, last=bid, bid=bid, ask=ask,
                        bid_bonds=2_000.0, ask_bonds=1_000.0,
                        trade_bonds=0.0, inferred_side="none",
                    ),
                    code=bond_code,
                    bids=((bid, 2_000.0), (wall_price, wall_bonds)),
                    asks=((ask, 1_000.0), (ask + 0.001, 2_000.0)),
                )
                context = MakerDecisionContext(
                    reference_price=reference_price,
                    reference_source=reference_source,
                    reliable_anchor=(
                        reference_source == "intraday_trade_anchor"
                    ),
                    spread=ask - bid,
                    bid_support_bonds=2_000.0 + wall_bonds,
                    ask_supply_bonds=ask_supply_bonds,
                    wall_threshold_bonds=5_000.0,
                )
                assessment = MarketAssessment(
                    reference_price=reference_price,
                    reference_low=bid,
                    reference_high=ask,
                    reference_source=reference_source,
                    reference_confidence=0.68,
                    state=state,
                    state_score=-1 if state == "possible_fall" else 0,
                    state_confidence=0.62,
                    recent_buy_bonds=recent_buy_bonds,
                    recent_sell_bonds=recent_sell_bonds,
                    midpoint_change=midpoint_change,
                    short_ask_change=short_ask_change,
                    largest_ask_gap=0.04,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=("双边成交与持续买墙支持低侧被动挂单",),
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )
                return (
                    account.buy_order.kind if account.buy_order else None,
                    account.buy_order.limit_price
                    if account.buy_order else None,
                )
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for bond_code in ("132026.SH", "132024.SH"):
                with self.subTest(bond_code=bond_code):
                    self.assertEqual(
                        quoted_buy(
                            root / f"v139-{bond_code[:6]}.sqlite3",
                            bond_code=bond_code,
                        ),
                        (
                            "persistent_two_sided_wall_corridor_entry",
                            136.052,
                        ),
                    )

            self.assertNotEqual(
                quoted_buy(root / "v138-parent.sqlite3", parent)[0],
                "persistent_two_sided_wall_corridor_entry",
            )
            negative_cases = {
                "customer-base-short": {"inventory": 0.0},
                "full-inventory": {"inventory": 2_000.0},
                "stable-is-allowed-control": {"state": "stable"},
                "possible-rise": {"state": "possible_rise"},
                "falling": {"state": "falling"},
                "stale-close": {"reference_source": "previous_close"},
                "small-buy-side": {"recent_buy_bonds": 4_999.0},
                "small-sell-side": {"recent_sell_bonds": 4_999.0},
                "thin-supply": {"ask_supply_bonds": 2_999.0},
                "narrow-corridor": {"ask": 136.220},
                "wide-corridor": {"ask": 136.600},
                "reference-below-low": {"reference_price": 136.040},
                "reference-too-far": {"reference_price": 136.240},
                "moving-midpoint": {"midpoint_change": 0.051},
                "falling-ask": {"short_ask_change": -0.051},
                "small-wall": {"wall_bonds": 4_999.0},
                "far-wall": {"wall_price": 135.951},
                "fresh-wall": {"wall_seconds": 59},
                "risk-exit-cooldown": {"risk_exit_age_seconds": 60},
            }
            for name, overrides in negative_cases.items():
                if name == "stable-is-allowed-control":
                    expected = "persistent_two_sided_wall_corridor_entry"
                else:
                    expected = None
                with self.subTest(name=name):
                    kind, _ = quoted_buy(
                        root / f"v139-{name}.sqlite3", **overrides,
                    )
                    if expected is None:
                        self.assertNotEqual(
                            kind,
                            "persistent_two_sided_wall_corridor_entry",
                        )
                    else:
                        self.assertEqual(kind, expected)

    def test_priority_v141_continuously_quotes_customer_base_recovery_only(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_V137_CANDIDATE
        policy = PRIORITY_POLICY_V141_CANDIDATE
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertFalse(
            parent.enable_continuous_dynamic_base_short_replenishment,
        )
        self.assertTrue(
            policy.enable_continuous_dynamic_base_short_replenishment,
        )
        self.assertFalse(
            policy.enable_post_replenishment_high_ask_cluster_preposition,
        )
        self.assertTrue(
            PRIORITY_POLICY_V140_CANDIDATE
                .enable_post_replenishment_high_ask_cluster_preposition,
        )
        self.assertFalse(
            QUEUE_POLICY_V10.enable_continuous_dynamic_base_short_replenishment,
        )
        self.assertFalse(
            WINDFALL_POLICY_V10
                .enable_continuous_dynamic_base_short_replenishment,
        )

        moment = datetime(2026, 8, 17, 9, 52, 35, tzinfo=SHANGHAI)
        assessment = MarketAssessment(
            reference_price=135.816,
            reference_low=135.644,
            reference_high=135.988,
            reference_source="current_midpoint",
            reference_confidence=0.45,
            state="rising",
            state_score=2,
            state_confidence=0.70,
            recent_buy_bonds=4_050.0,
            recent_sell_bonds=350.0,
            midpoint_change=0.1355,
            short_ask_change=0.0,
            largest_ask_gap=0.009,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=135.816,
            reference_source="current_midpoint",
            reliable_anchor=False,
            spread=0.344,
            bid_support_bonds=2_000.0,
            ask_supply_bonds=33_480.0,
            wall_threshold_bonds=5_000.0,
        )

        def quoted_buy(candidate_policy, sale_price: float):
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(Path(temp) / "v141-base-recovery.sqlite3")
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, priority_policy=candidate_policy,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_priority"]
                    account.inventory = 0.0
                    account.lots.clear()
                    account.replenishment_quantity = 1_000.0
                    account.replenishment_sale_value = sale_price * 1_000.0
                    account.last_base_short_sale_ts_ms = (
                        int(moment.timestamp() * 1_000) - 120_000
                    )
                    tick = replace(
                        self._replay_tick(
                            moment, last=135.996, bid=135.644, ask=135.988,
                            bid_bonds=1_000.0, ask_bonds=2_000.0,
                            trade_bonds=0.0, inferred_side="none",
                        ),
                        asks=(
                            (135.988, 2_000.0),
                            (135.989, 1_000.0),
                            (135.998, 2_000.0),
                            (135.999, 8_000.0),
                        ),
                    )
                    with patch.object(
                        engine, "_decision_context", return_value=context,
                    ):
                        engine._refresh_orders(
                            account, tick, assessment, persist=True,
                        )
                    metadata = (
                        json.loads(store.connection.execute(
                            "SELECT metadata_json FROM maker_paper_orders "
                            "WHERE id=?",
                            (account.buy_order.db_id,),
                        ).fetchone()[0])
                        if account.buy_order else {}
                    )
                    return (
                        account.buy_order.kind if account.buy_order else None,
                        account.buy_order.limit_price
                        if account.buy_order else None,
                        account.buy_order.price_boundary
                        if account.buy_order else None,
                        metadata.get("price_boundary_kind"),
                    )
                finally:
                    store.close()

        # The 1,000-bond top bid is below the ordinary 5,000-bond support
        # threshold and leaves only 0.35 yuan, yet the economic short keeps a
        # live first-position recovery quote.
        self.assertEqual(
            quoted_buy(policy, 135.995),
            (
                "dynamic_customer_base_replenish", 135.645,
                135.831, "buy_ceiling",
            ),
        )
        self.assertEqual(
            quoted_buy(parent, 135.995),
            (None, None, None, None),
        )
        # A fixed profit threshold is not required: a near-flat recovery still
        # quotes passively while the current causal fair region supports it.
        self.assertEqual(
            quoted_buy(policy, 135.650),
            (
                "dynamic_customer_base_replenish", 135.645,
                135.665, "buy_ceiling",
            ),
        )

    def test_queue_v118_quotes_the_empty_slot_before_level_two(self) -> None:
        self.assertEqual(
            QUEUE_POLICY_V118_CANDIDATE.parent_model_id,
            QUEUE_POLICY_V117_CANDIDATE.model_id,
        )
        self.assertFalse(QUEUE_POLICY_V117_CANDIDATE.quote_at_second_level_front)
        self.assertTrue(QUEUE_POLICY_V118_CANDIDATE.quote_at_second_level_front)

        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "queue-v118-second-level.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("queue",),
            ))
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, queue_policy=QUEUE_POLICY_V118_CANDIDATE,
                )
                moment = datetime(2026, 8, 21, 10, 0, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_queue"]
                tick = replace(
                    self._replay_tick(
                        moment, last=135.500, bid=135.500, ask=136.000,
                        bid_bonds=3_000.0, ask_bonds=3_000.0,
                    ),
                    bids=((135.500, 3_000.0), (135.400, 5_000.0)),
                    asks=((136.000, 3_000.0), (136.200, 5_000.0)),
                )

                self.assertEqual(
                    engine._queue_quote_position(
                        account, tick, side="buy", desired_price=135.500,
                    ),
                    (135.401, "second_level_front"),
                )
                self.assertEqual(
                    engine._queue_quote_position(
                        account, tick, side="sell", desired_price=136.000,
                    ),
                    (136.199, "second_level_front"),
                )
                self.assertEqual(
                    engine._queue_ahead_at_quote(
                        tick, side="buy", price=135.401,
                        queue_position_kind="second_level_front",
                    ),
                    3_000.0,
                )
                self.assertEqual(
                    engine._queue_ahead_at_quote(
                        tick, side="sell", price=136.199,
                        queue_position_kind="second_level_front",
                    ),
                    3_000.0,
                )
                parent_account = replace(
                    account, policy=QUEUE_POLICY_V117_CANDIDATE,
                )
                self.assertEqual(
                    engine._queue_quote_position(
                        parent_account, tick, side="buy", desired_price=135.500,
                    ),
                    (135.500, None),
                )
                # A deeper economic cap was never a level-one join order and
                # must not be raised merely to obtain the second-level slot.
                self.assertEqual(
                    engine._queue_quote_position(
                        account, tick, side="buy", desired_price=135.450,
                    ),
                    (135.450, None),
                )

                engine._replace_buy(
                    account, tick, (135.500, 1_000.0, None),
                    "low_bid_reversion", price_boundary=135.500,
                    persist=True,
                )
                order = account.buy_order
                self.assertIsNotNone(order)
                self.assertEqual(order.limit_price, 135.401)
                # The inserted price has no same-price queue, but the 3,000
                # bonds at the better bid retain cross-price priority.
                self.assertEqual(order.queue_ahead, 3_000.0)
                self.assertEqual(order.price_boundary, 135.500)
                metadata = json.loads(store.connection.execute(
                    "SELECT metadata_json FROM maker_paper_orders WHERE id=?",
                    (order.db_id,),
                ).fetchone()[0])
                self.assertEqual(
                    metadata["queue_position_kind"], "second_level_front",
                )

                # A 3,400-bond sweep first consumes the better 3,000-bond
                # level and fills only its 400-bond residual at our price.
                swept = replace(
                    tick,
                    tick_id=tick.tick_id + 1,
                    market_ts_ms=tick.market_ts_ms + 3_000,
                    market_time=(moment + timedelta(seconds=3)).time().isoformat(
                        timespec="milliseconds"
                    ),
                    last_price=135.401,
                    trade_bonds=3_400.0,
                    transaction_delta=1,
                    inferred_side="sell",
                    side_confidence="high",
                )
                engine._process_resting_orders(
                    account, swept, persist=True,
                    received_ts_ns=swept.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_400.0)
                self.assertEqual(account.buy_order.remaining, 600.0)
                fill = store.connection.execute(
                    "SELECT price,quantity FROM maker_paper_fills"
                ).fetchone()
                self.assertEqual(float(fill["price"]), 135.401)
                self.assertEqual(float(fill["quantity"]), 400.0)

                tight = replace(
                    tick,
                    bids=((135.500, 3_000.0), (135.499, 5_000.0)),
                    asks=((136.000, 3_000.0), (136.001, 5_000.0)),
                )
                self.assertEqual(
                    engine._queue_quote_position(
                        account, tight, side="buy", desired_price=135.500,
                    ),
                    (135.500, None),
                )
                self.assertEqual(
                    engine._queue_quote_position(
                        account, tight, side="sell", desired_price=136.000,
                    ),
                    (136.000, None),
                )
            finally:
                store.close()

    def test_queue_v119_causally_chooses_between_two_queue_positions(
        self,
    ) -> None:
        self.assertEqual(
            QUEUE_POLICY_V119_CANDIDATE.parent_model_id,
            QUEUE_POLICY_V118_CANDIDATE.model_id,
        )
        self.assertFalse(
            QUEUE_POLICY_V118_CANDIDATE
                .dynamically_choose_second_level_front,
        )
        self.assertTrue(
            QUEUE_POLICY_V119_CANDIDATE
                .dynamically_choose_second_level_front,
        )

        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "queue-v119-dynamic.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("queue",),
            ))
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, queue_policy=QUEUE_POLICY_V119_CANDIDATE,
                )
                moment = datetime(2026, 8, 21, 10, 0, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_queue"]
                worthwhile = replace(
                    self._replay_tick(
                        moment, last=135.500, bid=135.500, ask=136.000,
                        bid_bonds=3_000.0, ask_bonds=3_000.0,
                    ),
                    bids=((135.500, 3_000.0), (135.400, 5_000.0)),
                    asks=((136.000, 3_000.0), (136.200, 5_000.0)),
                )

                # A large best queue, a wide maker corridor, and a material
                # level gap make the second-level front worth its lower fill
                # probability on both sides.
                self.assertEqual(
                    engine._queue_quote_position(
                        account, worthwhile, side="buy",
                        desired_price=135.500,
                    ),
                    (135.401, "second_level_front"),
                )
                self.assertEqual(
                    engine._queue_quote_position(
                        account, worthwhile, side="sell",
                        desired_price=136.000,
                    ),
                    (136.199, "second_level_front"),
                )

                # Any missing condition keeps the parent's economically
                # valid quote at the ordinary best-level tail.
                small_queue = replace(
                    worthwhile,
                    bids=((135.500, 1_000.0), (135.400, 5_000.0)),
                )
                self.assertEqual(
                    engine._queue_quote_position(
                        account, small_queue, side="buy",
                        desired_price=135.500,
                    ),
                    (135.500, "best_level_tail"),
                )
                narrow_inside = replace(
                    worthwhile,
                    asks=((135.600, 3_000.0), (136.200, 5_000.0)),
                )
                self.assertEqual(
                    engine._queue_quote_position(
                        account, narrow_inside, side="buy",
                        desired_price=135.500,
                    ),
                    (135.500, "best_level_tail"),
                )
                trivial_improvement = replace(
                    worthwhile,
                    bids=((135.500, 3_000.0), (135.490, 5_000.0)),
                )
                self.assertEqual(
                    engine._queue_quote_position(
                        account, trivial_improvement, side="buy",
                        desired_price=135.500,
                    ),
                    (135.500, "best_level_tail"),
                )
                no_second_level = replace(
                    worthwhile,
                    bids=((135.500, 3_000.0),),
                )
                self.assertEqual(
                    engine._queue_quote_position(
                        account, no_second_level, side="buy",
                        desired_price=135.500,
                    ),
                    (135.500, "best_level_tail"),
                )

                # A deeper economic cap was never a best-level queue order;
                # dynamic placement cannot lift it toward the market.
                self.assertEqual(
                    engine._queue_quote_position(
                        account, worthwhile, side="buy",
                        desired_price=135.450,
                    ),
                    (135.450, None),
                )

                engine._replace_buy(
                    account, worthwhile, (135.500, 1_000.0, None),
                    "low_bid_reversion", price_boundary=135.500,
                    persist=True,
                )
                second_order = account.buy_order
                self.assertIsNotNone(second_order)
                assert second_order is not None
                second_metadata = json.loads(store.connection.execute(
                    "SELECT metadata_json FROM maker_paper_orders WHERE id=?",
                    (second_order.db_id,),
                ).fetchone()[0])
                self.assertEqual(
                    second_metadata["queue_position_kind"],
                    "second_level_front",
                )
                self.assertEqual(second_order.price_boundary, 135.500)

                engine._replace_buy(
                    account, small_queue, (135.500, 1_000.0, None),
                    "low_bid_reversion", price_boundary=135.500,
                    persist=True,
                )
                best_order = account.buy_order
                self.assertIsNotNone(best_order)
                assert best_order is not None
                best_metadata = json.loads(store.connection.execute(
                    "SELECT metadata_json FROM maker_paper_orders WHERE id=?",
                    (best_order.db_id,),
                ).fetchone()[0])
                self.assertEqual(
                    best_metadata["queue_position_kind"],
                    "best_level_tail",
                )
                self.assertEqual(best_order.price_boundary, 135.500)
            finally:
                store.close()

    def test_close_trading_window_is_versioned_per_execution_branch(self) -> None:
        self.assertEqual(
            PRIORITY_POLICY_V142_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_V141_CANDIDATE.model_id,
        )
        self.assertEqual(
            QUEUE_POLICY_V117_CANDIDATE.parent_model_id,
            QUEUE_POLICY_V113_CANDIDATE.model_id,
        )
        self.assertEqual(
            PRIORITY_POLICY_V141_CANDIDATE.latest_entry_time,
            "14:56:30.000",
        )
        self.assertEqual(
            QUEUE_POLICY_V113_CANDIDATE.latest_entry_time,
            "14:56:30.000",
        )
        self.assertEqual(
            PRIORITY_POLICY_V142_CANDIDATE.latest_entry_time,
            "15:29:59.999",
        )
        self.assertEqual(
            QUEUE_POLICY_V117_CANDIDATE.latest_entry_time,
            "15:29:59.999",
        )
        self.assertEqual(
            replace(
                PRIORITY_POLICY_V142_CANDIDATE,
                model_id=PRIORITY_POLICY_V141_CANDIDATE.model_id,
                model_version=PRIORITY_POLICY_V141_CANDIDATE.model_version,
                parent_model_id=PRIORITY_POLICY_V141_CANDIDATE.parent_model_id,
                latest_entry_time=PRIORITY_POLICY_V141_CANDIDATE.latest_entry_time,
            ),
            PRIORITY_POLICY_V141_CANDIDATE,
        )
        self.assertEqual(
            replace(
                QUEUE_POLICY_V117_CANDIDATE,
                model_id=QUEUE_POLICY_V113_CANDIDATE.model_id,
                model_version=QUEUE_POLICY_V113_CANDIDATE.model_version,
                parent_model_id=QUEUE_POLICY_V113_CANDIDATE.parent_model_id,
                latest_entry_time=QUEUE_POLICY_V113_CANDIDATE.latest_entry_time,
            ),
            QUEUE_POLICY_V113_CANDIDATE,
        )

        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "close-window.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                latest_entry="15:29:59.999",
                fill_modes=("priority", "queue"),
            ))
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(config, store)
                for legacy, close_long in (
                    (
                        PRIORITY_POLICY_V141_CANDIDATE,
                        PRIORITY_POLICY_V142_CANDIDATE,
                    ),
                    (
                        QUEUE_POLICY_V113_CANDIDATE,
                        QUEUE_POLICY_V117_CANDIDATE,
                    ),
                ):
                    with self.subTest(model_id=close_long.model_id):
                        self.assertFalse(
                            engine._entry_window_for_policy(
                                "14:57:15.000", legacy,
                            )
                        )
                        self.assertTrue(
                            engine._entry_window_for_policy(
                                "14:57:15.000", close_long,
                            )
                        )
                        self.assertTrue(
                            engine._entry_window_for_policy(
                                "15:29:59.999", close_long,
                            )
                        )
                        self.assertFalse(
                            engine._entry_window_for_policy(
                                "15:30:00.000", close_long,
                            )
                        )
            finally:
                store.close()

    def test_priority_v142_keeps_customer_base_recovery_after_145630(
        self,
    ) -> None:
        moment = datetime(2026, 8, 19, 14, 57, 12, tzinfo=SHANGHAI)
        assessment = replace(
            self._sweep_recovery_assessment(),
            reference_price=135.750,
            reference_low=135.550,
            reference_high=136.199,
            reference_source="current_midpoint",
            state="stable",
        )
        context = MakerDecisionContext(
            reference_price=135.750,
            reference_source="current_midpoint",
            reliable_anchor=False,
            spread=0.649,
            bid_support_bonds=200.0,
            ask_supply_bonds=700.0,
            wall_threshold_bonds=5_000.0,
        )

        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "v142-close-recovery.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                latest_entry="15:29:59.999",
                fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V141_CANDIDATE,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.inventory = 0.0
                account.lots.clear()
                account.replenishment_quantity = 1_000.0
                account.replenishment_sale_value = 136_287.0
                tick = self._replay_tick(
                    moment, last=135.550, bid=135.550, ask=136.199,
                    bid_bonds=200.0, ask_bonds=700.0,
                    trade_bonds=0.0, inferred_side="none",
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )
                self.assertIsNone(account.buy_order)

                account.policy = PRIORITY_POLICY_V142_CANDIDATE
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )
                self.assertIsNotNone(account.buy_order)
                assert account.buy_order is not None
                self.assertEqual(
                    account.buy_order.kind,
                    "dynamic_customer_base_replenish",
                )
                self.assertEqual(account.buy_order.limit_price, 135.551)

                closing = replace(
                    tick,
                    market_ts_ms=tick.market_ts_ms + 1_968_000,
                    market_time="15:30:00.000",
                    last_price=135.551,
                    trade_bonds=1_000.0,
                    inferred_side="sell",
                )
                engine._process_resting_orders(
                    account, closing, persist=True,
                    received_ts_ns=closing.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_000.0)
                self.assertIsNone(account.buy_order)
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, closing, assessment, persist=True,
                    )
                self.assertIsNone(account.buy_order)
            finally:
                store.close()

    def test_priority_v143_recovers_base_from_an_immediate_visible_tail(
        self,
    ) -> None:
        self.assertEqual(
            PRIORITY_POLICY_V143_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_V142_CANDIDATE.model_id,
        )
        self.assertEqual(
            replace(
                PRIORITY_POLICY_V143_CANDIDATE,
                model_id=PRIORITY_POLICY_V142_CANDIDATE.model_id,
                model_version=PRIORITY_POLICY_V142_CANDIDATE.model_version,
                parent_model_id=PRIORITY_POLICY_V142_CANDIDATE.parent_model_id,
                enable_immediate_visible_cluster_tail_recovery=(
                    PRIORITY_POLICY_V142_CANDIDATE
                        .enable_immediate_visible_cluster_tail_recovery
                ),
            ),
            PRIORITY_POLICY_V142_CANDIDATE,
        )
        moment = datetime(2026, 8, 19, 10, 14, 37, tzinfo=SHANGHAI)
        anchor = AnchorState(
            support_price=135.700,
            exit_price=135.700,
            band_midpoint=135.700,
            reference_price=135.700,
            confidence=0.8,
            buy_effective_bonds=8_000.0,
            sell_effective_bonds=0.0,
            downside_pressure=0.0,
            stock_return_5m=None,
            stock_factor=1.0,
            buy_clusters=(),
            sell_reference_price=None,
        )
        opportunity = Opportunity(
            kind="sweep_tail",
            signal_ts_ms=int(moment.timestamp() * 1_000),
            market_time="10:14:37.000",
            entry_price=135.700,
            quantity_bonds=1_000.0,
            target_exit_price=135.989,
            priority_exit_price=135.988,
            theoretical_edge=0.288,
            anchor=anchor,
            source_wall_bonds=9_140.0,
            consumed_bonds=8_000.0,
            consumed_ratio=8_000 / 9_140,
            consumption_seconds=3.0,
            tail_bonds=1_140.0,
            next_ask_price=135.989,
            notes=("immediate_visible_cluster_tail_consumption",),
        )

        def inventory_after(policy) -> float:
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(Path(temp) / "v143-tail-recovery.sqlite3")
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, priority_policy=policy,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_priority"]
                    account.inventory = 0.0
                    account.lots.clear()
                    account.replenishment_quantity = 1_000.0
                    account.replenishment_sale_value = 135_670.0
                    engine._active_sweep(
                        account,
                        self._replay_tick(
                            moment, last=135.700, bid=135.451, ask=135.700,
                            bid_bonds=1_000.0, ask_bonds=1_140.0,
                            trade_bonds=8_000.0, inferred_side="buy",
                        ),
                        opportunity,
                        persist=True,
                    )
                    return account.inventory
                finally:
                    store.close()

        self.assertEqual(inventory_after(PRIORITY_POLICY_V142_CANDIDATE), 0.0)
        self.assertEqual(inventory_after(PRIORITY_POLICY_V143_CANDIDATE), 1_000.0)

    def test_priority_v144_buys_first_in_a_persistent_wide_corridor(
        self,
    ) -> None:
        self.assertEqual(
            PRIORITY_POLICY_V144_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_V143_CANDIDATE.model_id,
        )
        self.assertFalse(
            PRIORITY_POLICY_V143_CANDIDATE
                .enable_persistent_wide_spread_buy_first_entry,
        )
        self.assertTrue(
            PRIORITY_POLICY_V144_CANDIDATE
                .enable_persistent_wide_spread_buy_first_entry,
        )
        moment = datetime(2026, 8, 21, 11, 7, 39, tzinfo=SHANGHAI)
        assessment = MarketAssessment(
            reference_price=135.700,
            reference_low=135.501,
            reference_high=135.899,
            reference_source="persistent_inside_market",
            reference_confidence=0.55,
            state="stable",
            state_score=0,
            state_confidence=0.75,
            recent_buy_bonds=0.0,
            recent_sell_bonds=0.0,
            midpoint_change=0.0,
            short_ask_change=0.0,
            largest_ask_gap=0.0,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=135.700,
            reference_source="persistent_inside_market",
            reliable_anchor=False,
            spread=0.398,
            bid_support_bonds=2_000.0,
            ask_supply_bonds=4_000.0,
            wall_threshold_bonds=5_000.0,
        )

        def quote_for(policy, *, age_seconds: int = 60,
                      high_buy_bonds: float = 1_000.0,
                      state: str = "stable", bid_bonds: float = 1_000.0,
                      ask: float = 135.899):
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(Path(temp) / "v144-buy-first.sqlite3")
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, priority_policy=policy,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_priority"]
                    start_ms = int(
                        (moment - timedelta(seconds=age_seconds)).timestamp()
                        * 1_000
                    )
                    now_ms = int(moment.timestamp() * 1_000)
                    engine.analyzer.book_quotes.extend((
                        BookQuote(start_ms, 135.501, ask),
                        BookQuote(now_ms, 135.501, ask),
                    ))
                    if high_buy_bonds > 0:
                        engine.analyzer.trade_evidence.append(TradeEvidence(
                            now_ms - 480_000,
                            ask,
                            high_buy_bonds,
                            1,
                            "buy",
                        ))
                    tick = replace(
                        self._replay_tick(
                            moment, last=ask, bid=135.501, ask=ask,
                            bid_bonds=bid_bonds, ask_bonds=4_000.0,
                        ),
                        bids=((135.501, bid_bonds), (135.500, 1_000.0)),
                    )
                    with patch.object(
                        engine, "_decision_context", return_value=context,
                    ):
                        engine._refresh_orders(
                            account, tick, replace(assessment, state=state),
                            persist=True,
                        )
                    return account.buy_order
                finally:
                    store.close()

        self.assertIsNone(quote_for(PRIORITY_POLICY_V143_CANDIDATE))
        order = quote_for(PRIORITY_POLICY_V144_CANDIDATE)
        self.assertIsNotNone(order)
        assert order is not None
        self.assertEqual(order.kind, "persistent_wide_spread_buy_first_entry")
        self.assertEqual(order.limit_price, 135.502)
        self.assertEqual(order.quantity, 1_000.0)
        self.assertEqual(order.price_boundary, 135.502)
        self.assertIsNone(
            quote_for(PRIORITY_POLICY_V144_CANDIDATE, age_seconds=59),
        )
        self.assertIsNone(
            quote_for(
                PRIORITY_POLICY_V144_CANDIDATE, high_buy_bonds=999.0,
            ),
        )
        self.assertIsNone(
            quote_for(PRIORITY_POLICY_V144_CANDIDATE, state="rising"),
        )
        self.assertIsNone(
            quote_for(PRIORITY_POLICY_V144_CANDIDATE, bid_bonds=999.0),
        )
        self.assertIsNone(
            quote_for(PRIORITY_POLICY_V144_CANDIDATE, ask=135.791),
        )

        # Once causally established, the exact low quote survives expiry of
        # the initial high-side trade while the same full corridor persists.
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v144-retain.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V144_CANDIDATE,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                now_ms = int(moment.timestamp() * 1_000)
                engine.analyzer.book_quotes.extend((
                    BookQuote(now_ms - 60_000, 135.501, 135.899),
                    BookQuote(now_ms, 135.501, 135.899),
                ))
                tick = self._replay_tick(
                    moment, last=135.899, bid=135.501, ask=135.899,
                    bid_bonds=1_000.0, ask_bonds=4_000.0,
                )
                account.buy_order = engine._new_order(
                    account, tick, side="buy",
                    kind="persistent_wide_spread_buy_first_entry",
                    lot_id=None, price=135.502, quantity=1_000.0,
                    price_boundary=135.502, queue_ahead=0.0,
                    target_price=None, persist=True,
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )
                self.assertIsNotNone(account.buy_order)
                assert account.buy_order is not None
                self.assertEqual(account.buy_order.limit_price, 135.502)
            finally:
                store.close()

    def test_priority_v144_exits_only_the_low_side_quantity_actually_bought(
        self,
    ) -> None:
        moment = datetime(2026, 8, 21, 11, 7, 39, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v144-partial-turn.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V144_CANDIDATE,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                quote = self._replay_tick(
                    moment, last=135.899, bid=135.501, ask=135.899,
                    bid_bonds=1_000.0, ask_bonds=4_000.0,
                )
                buy_order = engine._new_order(
                    account, quote, side="buy",
                    kind="persistent_wide_spread_buy_first_entry",
                    lot_id=None, price=135.502, quantity=1_000.0,
                    price_boundary=135.502,
                    queue_ahead=0.0, target_price=None, persist=True,
                )
                account.buy_order = buy_order
                low_print = replace(
                    quote,
                    market_ts_ms=quote.market_ts_ms + 144_000,
                    market_time="11:10:03.000",
                    last_price=135.501,
                    trade_bonds=360.0,
                    transaction_delta=1,
                    inferred_side="sell",
                    bids=((135.501, 640.0), (135.500, 1_000.0)),
                )
                engine._process_resting_orders(
                    account, low_print, persist=True,
                    received_ts_ns=low_print.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_360.0)
                self.assertEqual(buy_order.filled_quantity, 360.0)

                extra_lot = next(
                    lot for lot in account.lots.values()
                    if lot.kind != "base" and lot.remaining_quantity > 0
                )
                sell_order = engine._new_order(
                    account, low_print, side="sell", kind="inventory_exit",
                    lot_id=extra_lot.db_id, price=135.898, quantity=360.0,
                    price_boundary=135.898,
                    queue_ahead=0.0, target_price=135.898, persist=True,
                )
                account.sell_orders[extra_lot.db_id] = sell_order
                high_print = replace(
                    low_print,
                    market_ts_ms=low_print.market_ts_ms + 15_000,
                    market_time="11:10:18.000",
                    last_price=135.899,
                    trade_bonds=1_000.0,
                    transaction_delta=1,
                    inferred_side="buy",
                    asks=((135.899, 3_000.0),),
                )
                engine._process_resting_orders(
                    account, high_print, persist=True,
                    received_ts_ns=high_print.market_ts_ms * 1_000_000,
                )
                engine._mark_account(account, high_print, persist=True)
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(sell_order.filled_quantity, 360.0)
                self.assertAlmostEqual(account.trading_pnl, 142.56, places=2)
                self.assertEqual(account.customer_base_short_bonds, 0.0)
            finally:
                store.close()

    def test_first_position_v144_uses_adjacent_depth_and_edge_jointly(
        self,
    ) -> None:
        policy = PRIORITY_POLICY_FIRST_POSITION_V144
        self.assertEqual(policy.model_id, "maker_priority_v1_44")
        self.assertEqual(policy.model_version, "1.44")
        self.assertEqual(
            policy.parent_model_id, PRIORITY_POLICY_V143_CANDIDATE.model_id,
        )
        self.assertFalse(
            PRIORITY_POLICY_V143_CANDIDATE.enable_adjacent_bid_cushion_entry,
        )
        self.assertFalse(QUEUE_POLICY_V118_CANDIDATE.enable_adjacent_bid_cushion_entry)

        moment = datetime(2026, 8, 24, 9, 55, 16, tzinfo=SHANGHAI)
        assessment = MarketAssessment(
            reference_price=136.7505,
            reference_low=136.601,
            reference_high=136.900,
            reference_source="persistent_inside_market",
            reference_confidence=0.55,
            state="stable",
            state_score=0,
            state_confidence=0.75,
            recent_buy_bonds=0.0,
            recent_sell_bonds=0.0,
            midpoint_change=0.0,
            short_ask_change=0.0,
            largest_ask_gap=0.0,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=136.7505,
            reference_source="persistent_inside_market",
            reliable_anchor=False,
            spread=0.299,
            bid_support_bonds=9_000.0,
            ask_supply_bonds=1_000.0,
            wall_threshold_bonds=5_000.0,
        )

        def decide(
            selected_policy, *,
            bids=((136.601, 4_000.0), (136.600, 5_000.0)),
            state="stable", attacking_sells=0.0,
            recent_global_exit=False,
        ):
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(Path(temp) / "adjacent-cushion.sqlite3")
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, priority_policy=selected_policy,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_priority"]
                    result = None
                    for seconds in (0, 15, 30):
                        current = moment + timedelta(seconds=seconds)
                        tick = replace(
                            self._replay_tick(
                                current, last=136.900,
                                bid=bids[0][0], ask=136.900,
                                bid_bonds=bids[0][1],
                            ),
                            bids=bids,
                        )
                        if seconds == 30 and attacking_sells > 0:
                            engine.analyzer.trade_evidence.append(TradeEvidence(
                                tick.market_ts_ms - 3_000,
                                136.601,
                                attacking_sells,
                                1,
                                "sell",
                            ))
                        if seconds == 30 and recent_global_exit:
                            account.last_falling_profitable_exit_price = 136.601
                            account.last_falling_profitable_exit_ts_ms = (
                                tick.market_ts_ms - 1_000
                            )
                        result = engine._adjacent_bid_cushion_entry(
                            account, tick,
                            replace(assessment, state=state), context,
                        )
                    return result
                finally:
                    store.close()

        self.assertIsNone(decide(PRIORITY_POLICY_V143_CANDIDATE))
        decision = decide(policy)
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.price, 136.602)
        self.assertEqual(decision.floor_price, 136.600)
        self.assertEqual(decision.ceiling_price, 136.601)
        self.assertEqual(decision.entry_bonds, 9_000.0)
        self.assertAlmostEqual(decision.entry_edge, 0.297)

        self.assertIsNone(decide(
            policy,
            bids=((136.601, 4_000.0), (136.600, 3_990.0)),
        ))
        self.assertIsNone(decide(
            policy,
            bids=((136.601, 4_000.0), (136.590, 5_000.0)),
        ))
        self.assertIsNone(decide(policy, state="rising"))
        self.assertIsNone(decide(policy, attacking_sells=1_000.0))
        self.assertIsNotNone(decide(policy, recent_global_exit=True))

    def test_first_position_v144_ignores_vanished_iron_floor_quote_caps(
        self,
    ) -> None:
        self.assertFalse(
            PRIORITY_POLICY_V143_CANDIDATE.ignore_legacy_bid_wall_entry_caps,
        )
        self.assertTrue(
            PRIORITY_POLICY_FIRST_POSITION_V144.ignore_legacy_bid_wall_entry_caps,
        )
        self.assertFalse(
            QUEUE_POLICY_V118_CANDIDATE.ignore_legacy_bid_wall_entry_caps,
        )

        moment = datetime(2026, 8, 24, 10, 23, 46, tzinfo=SHANGHAI)
        assessment = MarketAssessment(
            reference_price=136.850,
            reference_low=136.702,
            reference_high=136.997,
            reference_source="persistent_inside_market",
            reference_confidence=0.55,
            state="stable",
            state_score=0,
            state_confidence=0.75,
            recent_buy_bonds=0.0,
            recent_sell_bonds=0.0,
            midpoint_change=0.0,
            short_ask_change=0.0,
            largest_ask_gap=0.0,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            # The 135.100 wall is no longer in the current book.  v1.43 keeps
            # the analyzer's 600-second memory and caps at 135.400; the user-
            # confirmed v1.44 must quote from the current 136.702/136.701
            # support and the live inside corridor instead.
            iron_floor_price=135.100,
            iron_floor_bonds=74_000.0,
            evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=136.850,
            reference_source="persistent_inside_market",
            reliable_anchor=False,
            spread=0.295,
            bid_support_bonds=11_000.0,
            ask_supply_bonds=2_000.0,
            wall_threshold_bonds=5_000.0,
        )

        def quoted_buy(policy) -> tuple[float, float]:
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(Path(temp) / "vanished-iron-floor.sqlite3")
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, priority_policy=policy,
                    )
                    engine._start_date(moment.date().isoformat())
                    engine.observed_market_trade = True
                    account = engine.accounts["maker_v01_priority"]
                    tick = replace(
                        self._replay_tick(
                            moment, last=136.702, bid=136.702, ask=136.997,
                            bid_bonds=5_000.0, ask_bonds=2_000.0,
                        ),
                        bids=((136.702, 5_000.0), (136.701, 6_000.0)),
                    )
                    with patch.object(
                        engine, "_decision_context", return_value=context,
                    ):
                        engine._refresh_orders(
                            account, tick, assessment, persist=True,
                        )
                    self.assertIsNotNone(account.buy_order)
                    assert account.buy_order is not None
                    return (
                        account.buy_order.limit_price,
                        account.buy_order.price_boundary,
                    )
                finally:
                    store.close()

        self.assertEqual(
            quoted_buy(PRIORITY_POLICY_V143_CANDIDATE),
            (135.400, 135.400),
        )
        self.assertEqual(
            quoted_buy(PRIORITY_POLICY_FIRST_POSITION_V144),
            (136.703, 136.703),
        )

        visible_wall_assessment = replace(
            assessment,
            state="possible_fall",
            state_score=-1,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
        )

        def quoted_above_visible_distant_wall(policy) -> float:
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(Path(temp) / "visible-wall-cap.sqlite3")
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, priority_policy=policy,
                    )
                    engine._start_date(moment.date().isoformat())
                    engine.observed_market_trade = True
                    account = engine.accounts["maker_v01_priority"]
                    tick = replace(
                        self._replay_tick(
                            moment, last=136.702, bid=136.702, ask=136.997,
                            bid_bonds=1_000.0, ask_bonds=2_000.0,
                        ),
                        bids=((136.702, 1_000.0), (136.500, 5_000.0)),
                    )
                    with patch.object(
                        engine, "_decision_context", return_value=context,
                    ):
                        engine._refresh_orders(
                            account, tick, visible_wall_assessment, persist=True,
                        )
                    self.assertIsNotNone(account.buy_order)
                    assert account.buy_order is not None
                    return account.buy_order.limit_price
                finally:
                    store.close()

        legacy_visible_wall_cap_policy = replace(
            PRIORITY_POLICY_V143_CANDIDATE,
            enable_visible_wall_anchored_downtrend_entry=True,
        )
        corrected_visible_wall_policy = replace(
            legacy_visible_wall_cap_policy,
            ignore_legacy_bid_wall_entry_caps=True,
        )
        # Exercise the older fixed current-wall cap directly: current depth
        # remains safety evidence, but first-position 1.44's correction does
        # not mechanically turn a 136.500 wall into a 136.600 quote ceiling.
        self.assertEqual(
            quoted_above_visible_distant_wall(legacy_visible_wall_cap_policy),
            136.600,
        )
        self.assertEqual(
            quoted_above_visible_distant_wall(corrected_visible_wall_policy),
            136.703,
        )

    def test_first_position_v145_does_not_chase_an_isolated_top_bid(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_FIRST_POSITION_V144
        policy = PRIORITY_POLICY_FIRST_POSITION_V145
        self.assertEqual(policy.model_id, "maker_priority_v1_45")
        self.assertEqual(policy.model_version, "1.45")
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertFalse(
            parent.enable_isolated_top_bid_base_replenishment_guard,
        )
        self.assertTrue(
            policy.enable_isolated_top_bid_base_replenishment_guard,
        )
        self.assertFalse(
            QUEUE_POLICY_V118_CANDIDATE
                .enable_isolated_top_bid_base_replenishment_guard,
        )
        self.assertFalse(
            WINDFALL_POLICY_V10
                .enable_isolated_top_bid_base_replenishment_guard,
        )
        self.assertEqual(
            policy.enable_confirmed_rising_buy_sequence_base_short_stop,
            parent.enable_confirmed_rising_buy_sequence_base_short_stop,
        )
        self.assertEqual(
            policy.enable_immediate_visible_cluster_tail_recovery,
            parent.enable_immediate_visible_cluster_tail_recovery,
        )

        moment = datetime(2026, 8, 24, 10, 48, 25, tzinfo=SHANGHAI)
        assessment = MarketAssessment(
            reference_price=136.994,
            reference_low=136.755,
            reference_high=136.996,
            reference_source="persistent_inside_market",
            reference_confidence=0.55,
            state="stable",
            state_score=0,
            state_confidence=0.75,
            recent_buy_bonds=0.0,
            recent_sell_bonds=0.0,
            midpoint_change=0.0,
            short_ask_change=0.0,
            largest_ask_gap=0.0,
            downside_book_vacuum=False,
            fragile_top_bid=True,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=136.994,
            reference_source="persistent_inside_market",
            reliable_anchor=False,
            spread=0.003,
            bid_support_bonds=1_000.0,
            ask_supply_bonds=36_360.0,
            wall_threshold_bonds=5_000.0,
        )
        target_bids = (
            (136.993, 1_000.0),
            (136.755, 2_000.0),
            (136.748, 1_000.0),
            (136.503, 4_820.0),
            (136.502, 5_000.0),
        )
        heavy_asks = (
            (136.996, 2_000.0),
            (136.999, 17_360.0),
            (137.000, 17_000.0),
            (137.100, 5_000.0),
        )

        def quoted_buy(
            selected_policy, *, bids=target_bids, asks=heavy_asks,
        ) -> tuple[str | None, float | None, dict]:
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(Path(temp) / "isolated-top-bid.sqlite3")
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, priority_policy=selected_policy,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_priority"]
                    account.inventory = 0.0
                    account.lots.clear()
                    account.replenishment_quantity = 1_000.0
                    account.replenishment_sale_value = 136.995 * 1_000.0
                    account.last_base_short_sale_ts_ms = (
                        int(moment.timestamp() * 1_000) - 465_000
                    )
                    tick = replace(
                        self._replay_tick(
                            moment, last=136.996,
                            bid=bids[0][0], ask=asks[0][0],
                            bid_bonds=bids[0][1], ask_bonds=asks[0][1],
                            trade_bonds=0.0, inferred_side="none",
                        ),
                        bids=bids,
                        asks=asks,
                    )
                    with patch.object(
                        engine, "_decision_context", return_value=context,
                    ):
                        engine._refresh_orders(
                            account, tick, assessment, persist=True,
                        )
                    order = account.buy_order
                    metadata = (
                        json.loads(store.connection.execute(
                            "SELECT metadata_json FROM maker_paper_orders "
                            "WHERE id=?",
                            (order.db_id,),
                        ).fetchone()[0])
                        if order is not None else {}
                    )
                    return (
                        order.kind if order is not None else None,
                        order.limit_price if order is not None else None,
                        metadata,
                    )
                finally:
                    store.close()

        parent_kind, parent_price, _ = quoted_buy(parent)
        self.assertEqual(parent_kind, "dynamic_customer_base_replenish")
        self.assertEqual(parent_price, 136.994)

        kind, price, metadata = quoted_buy(policy)
        self.assertEqual(kind, "isolated_top_bid_guarded_base_replenish")
        self.assertEqual(price, 136.756)
        self.assertEqual(metadata["isolated_top_bid_price"], 136.993)
        self.assertEqual(metadata["isolated_top_bid_bonds"], 1_000.0)
        self.assertEqual(
            metadata["reliable_replenishment_bid_price"], 136.756,
        )
        self.assertEqual(metadata["near_ask_supply_bonds"], 36_360.0)
        self.assertEqual(
            metadata["isolated_near_ask_floor_price"], 136.996,
        )
        self.assertEqual(
            metadata["isolated_near_ask_ceiling_price"], 137.000,
        )

        # A continuous bid ladder is not an island.
        _, continuous_price, _ = quoted_buy(
            policy,
            bids=((136.993, 1_000.0), (136.990, 2_000.0)),
        )
        self.assertEqual(continuous_price, 136.994)
        # Light nearby sell supply does not justify extra patience.
        _, light_ask_price, _ = quoted_buy(
            policy,
            asks=((136.996, 1_000.0), (137.020, 1_000.0)),
        )
        self.assertEqual(light_ask_price, 136.994)
        # A genuinely thick best bid is not treated as a one-lot mistake.
        _, thick_bid_price, _ = quoted_buy(
            policy,
            bids=((136.993, 5_000.0), (136.755, 2_000.0)),
        )
        self.assertEqual(thick_bid_price, 136.994)

    def test_first_position_v145_restores_base_when_isolated_sell_wall_is_hit(
        self,
    ) -> None:
        moment = datetime(2026, 8, 24, 10, 48, 25, tzinfo=SHANGHAI)
        assessment = MarketAssessment(
            reference_price=136.994,
            reference_low=136.755,
            reference_high=136.996,
            reference_source="persistent_inside_market",
            reference_confidence=0.55,
            state="stable",
            state_score=0,
            state_confidence=0.75,
            recent_buy_bonds=0.0,
            recent_sell_bonds=0.0,
            midpoint_change=0.0,
            short_ask_change=0.0,
            largest_ask_gap=0.0,
            downside_book_vacuum=False,
            fragile_top_bid=True,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=136.994,
            reference_source="persistent_inside_market",
            reliable_anchor=False,
            spread=0.003,
            bid_support_bonds=1_000.0,
            ask_supply_bonds=30_000.0,
            wall_threshold_bonds=5_000.0,
        )
        initial_bids = (
            (136.993, 1_000.0),
            (136.755, 2_000.0),
            (136.748, 1_000.0),
        )
        initial_asks = (
            (136.996, 2_000.0),
            (136.999, 14_000.0),
            (137.000, 14_000.0),
        )

        def seeded_guard(database: Path):
            config = test_config(database)
            store = SQLiteStore(config)
            engine = MakerPaperEngine(
                config, store,
                priority_policy=PRIORITY_POLICY_FIRST_POSITION_V145,
            )
            engine._start_date(moment.date().isoformat())
            account = engine.accounts["maker_v01_priority"]
            account.inventory = 0.0
            account.lots.clear()
            account.replenishment_quantity = 1_000.0
            account.replenishment_sale_value = 136.995 * 1_000.0
            account.last_base_short_sale_ts_ms = (
                int(moment.timestamp() * 1_000) - 465_000
            )
            opening = replace(
                self._replay_tick(
                    moment, last=136.996,
                    bid=136.993, ask=136.996,
                    bid_bonds=1_000.0, ask_bonds=2_000.0,
                    trade_bonds=0.0, inferred_side="none",
                ),
                bids=initial_bids,
                asks=initial_asks,
            )
            with patch.object(
                engine, "_decision_context", return_value=context,
            ):
                engine._refresh_orders(
                    account, opening, assessment, persist=True,
                )
            self.assertIsNotNone(account.buy_order)
            assert account.buy_order is not None
            self.assertEqual(
                account.buy_order.kind,
                "isolated_top_bid_guarded_base_replenish",
            )
            account.last_ask = opening.ask1
            account.last_asks = opening.asks
            account.last_bid = opening.bid1
            account.last_bids = opening.bids
            return store, engine, account

        with tempfile.TemporaryDirectory() as temp:
            store, engine, account = seeded_guard(
                Path(temp) / "real-wall-attack.sqlite3",
            )
            try:
                attack = replace(
                    self._replay_tick(
                        moment + timedelta(seconds=3),
                        last=136.999, bid=136.993, ask=136.999,
                        bid_bonds=1_000.0, ask_bonds=1_000.0,
                        trade_bonds=15_000.0, inferred_side="buy",
                    ),
                    bids=initial_bids,
                    asks=((136.999, 1_000.0), (137.000, 14_000.0)),
                )
                restored = (
                    engine._active_isolated_top_bid_wall_attack_replenishment(
                        account, attack, assessment, persist=True,
                        received_ts_ns=attack.market_ts_ms * 1_000_000,
                    )
                )
                self.assertTrue(restored)
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(account.customer_base_short_bonds, 0.0)
                fill = store.connection.execute(
                    "SELECT fill_reason, price, quantity "
                    "FROM maker_paper_fills "
                    "ORDER BY id DESC LIMIT 1",
                ).fetchone()
                self.assertEqual(
                    fill[0],
                    "active_isolated_top_bid_sell_wall_attack_replenishment",
                )
                self.assertEqual(float(fill[1]), 136.999)
                self.assertEqual(float(fill[2]), 1_000.0)
            finally:
                store.close()

        scenarios = (
            (
                "withdrawal_is_not_consumption",
                0.0,
                "none",
                136.999,
                ((136.999, 1_000.0), (137.000, 14_000.0)),
            ),
            (
                "small_buy_is_not_material",
                4_000.0,
                "buy",
                136.999,
                ((136.999, 12_000.0), (137.000, 14_000.0)),
            ),
            (
                "large_price_move_keeps_parent_stop_boundary",
                30_000.0,
                "buy",
                137.050,
                ((137.050, 1_000.0), (137.100, 14_000.0)),
            ),
        )
        for name, trade_bonds, side, ask_price, asks in scenarios:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                store, engine, account = seeded_guard(
                    Path(temp) / f"{name}.sqlite3",
                )
                try:
                    tick = replace(
                        self._replay_tick(
                            moment + timedelta(seconds=3),
                            last=ask_price, bid=136.993, ask=ask_price,
                            bid_bonds=1_000.0, ask_bonds=asks[0][1],
                            trade_bonds=trade_bonds, inferred_side=side,
                        ),
                        bids=initial_bids,
                        asks=asks,
                    )
                    restored = engine._active_isolated_top_bid_wall_attack_replenishment(
                        account, tick, assessment, persist=True,
                        received_ts_ns=tick.market_ts_ms * 1_000_000,
                    )
                    self.assertFalse(restored)
                    self.assertEqual(account.inventory, 0.0)
                    self.assertEqual(account.customer_base_short_bonds, 1_000.0)
                finally:
                    store.close()

    def test_first_position_v146_quotes_the_reviewed_three_gorges_corridor(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_FIRST_POSITION_V145
        policy = PRIORITY_POLICY_FIRST_POSITION_V146
        self.assertEqual(policy.model_id, "maker_priority_v1_46")
        self.assertEqual(policy.model_version, "1.46")
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertFalse(
            parent.enable_joint_causal_corridor_two_sided_quote,
        )
        self.assertTrue(
            policy.enable_joint_causal_corridor_two_sided_quote,
        )
        self.assertFalse(
            QUEUE_POLICY_V118_CANDIDATE
                .enable_joint_causal_corridor_two_sided_quote,
        )
        self.assertFalse(
            WINDFALL_POLICY_V10
                .enable_joint_causal_corridor_two_sided_quote,
        )

        moment = datetime(2026, 8, 24, 13, 23, 2, tzinfo=SHANGHAI)
        bids = (
            (136.067, 2_000.0),
            (136.060, 2_000.0),
            (136.040, 2_000.0),
            (136.020, 2_000.0),
        )
        asks = (
            (136.399, 4_000.0),
            (136.400, 14_500.0),
        )
        assessment = MarketAssessment(
            reference_price=136.200,
            reference_low=136.200,
            reference_high=136.399,
            reference_source="intraday_trade_anchor",
            reference_confidence=0.75,
            state="stable",
            state_score=0,
            state_confidence=0.75,
            recent_buy_bonds=2_000.0,
            recent_sell_bonds=4_000.0,
            midpoint_change=0.0,
            short_ask_change=0.0,
            largest_ask_gap=0.0,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=136.200,
            reference_source="intraday_trade_anchor",
            reliable_anchor=True,
            spread=0.332,
            bid_support_bonds=8_000.0,
            ask_supply_bonds=18_500.0,
            wall_threshold_bonds=5_000.0,
        )

        def seeded(database: Path, selected_policy):
            config = test_config(database)
            store = SQLiteStore(config)
            engine = MakerPaperEngine(
                config, store, priority_policy=selected_policy,
            )
            engine._start_date(moment.date().isoformat())
            for seconds, price, bonds, side in (
                (-192, 136.396, 2_000.0, "sell"),
                (-90, 136.398, 2_000.0, "buy"),
                (-18, 136.399, 2_000.0, "sell"),
            ):
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    int((moment + timedelta(seconds=seconds)).timestamp() * 1_000),
                    price,
                    bonds,
                    1,
                    side,
                ))
            tick = replace(
                self._replay_tick(
                    moment, last=136.399,
                    bid=bids[0][0], ask=asks[0][0],
                    bid_bonds=bids[0][1], ask_bonds=asks[0][1],
                    trade_bonds=0.0, inferred_side="none",
                ),
                bids=bids,
                asks=asks,
            )
            for seconds in (-36, -18, 0):
                engine._observe_joint_corridor_book(replace(
                    tick,
                    market_ts_ms=int(
                        (moment + timedelta(seconds=seconds)).timestamp()
                        * 1_000
                    ),
                ))
            account = engine.accounts["maker_v01_priority"]
            with patch.object(
                engine, "_decision_context", return_value=context,
            ):
                engine._refresh_orders(
                    account, tick, assessment, persist=True,
                )
            return store, engine, account, tick

        with tempfile.TemporaryDirectory() as temp:
            parent_store, _, parent_account, _ = seeded(
                Path(temp) / "v145-three-gorges.sqlite3", parent,
            )
            try:
                self.assertIsNone(parent_account.buy_order)
                self.assertEqual(parent_account.sell_orders, {})
            finally:
                parent_store.close()

            store, engine, account, tick = seeded(
                Path(temp) / "v146-three-gorges.sqlite3", policy,
            )
            try:
                self.assertIsNotNone(account.buy_order)
                assert account.buy_order is not None
                buy = account.buy_order
                self.assertEqual(buy.kind, "joint_causal_corridor_entry")
                self.assertEqual(buy.limit_price, 136.068)
                self.assertEqual(buy.target_price, 136.398)
                self.assertEqual(buy.protective_bid_floor_price, 136.020)
                self.assertEqual(buy.protective_bid_ceiling_price, 136.067)
                self.assertEqual(buy.protective_bid_entry_bonds, 8_000.0)
                self.assertAlmostEqual(
                    buy.protective_bid_entry_edge, 0.330,
                )
                self.assertEqual(len(account.sell_orders), 1)
                base_sell = next(iter(account.sell_orders.values()))
                self.assertEqual(
                    base_sell.kind, "joint_causal_corridor_base_sell",
                )
                self.assertEqual(base_sell.limit_price, 136.398)
                self.assertEqual(
                    base_sell.repeated_turn_replenishment_price, 136.068,
                )

                buy_id = buy.db_id
                sell_id = base_sell.db_id
                flicker_bids = (
                    (136.396, 1_000.0),
                    *bids,
                )
                flicker = replace(
                    tick,
                    market_ts_ms=tick.market_ts_ms + 3_000,
                    market_time="13:23:05.000",
                    bids=flicker_bids,
                )
                flicker_context = replace(
                    context,
                    breakout_support_price=136.396,
                    breakout_lower_sell_bonds=0.0,
                )
                with patch.object(
                    engine, "_decision_context",
                    return_value=flicker_context,
                ):
                    engine._refresh_orders(
                        account, flicker, assessment, persist=True,
                    )
                assert account.buy_order is not None
                self.assertEqual(account.buy_order.db_id, buy_id)
                self.assertEqual(account.buy_order.limit_price, 136.068)
                self.assertEqual(
                    next(iter(account.sell_orders.values())).db_id,
                    sell_id,
                )

                damaged = replace(
                    flicker,
                    market_ts_ms=flicker.market_ts_ms + 3_000,
                    market_time="13:23:08.000",
                    bids=((136.067, 1_000.0), (136.060, 1_000.0)),
                )
                damaged_context = replace(
                    context, bid_support_bonds=2_000.0,
                )
                with patch.object(
                    engine, "_decision_context", return_value=damaged_context,
                ):
                    engine._refresh_orders(
                        account, damaged, assessment, persist=True,
                    )
                self.assertIsNone(account.buy_order)
                self.assertEqual(account.sell_orders, {})
            finally:
                store.close()

    def test_first_position_v147_keeps_intraday_centre_when_quiet_book_falls_back(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_FIRST_POSITION_V146
        policy = PRIORITY_POLICY_FIRST_POSITION_V147
        self.assertEqual(policy.model_id, "maker_priority_v1_47")
        self.assertEqual(policy.model_version, "1.47")
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertFalse(
            parent.retain_intraday_reference_in_quiet_wide_market,
        )
        self.assertTrue(
            policy.retain_intraday_reference_in_quiet_wide_market,
        )
        self.assertEqual(policy.quiet_wide_market_earliest_time, "14:45:00.000")
        self.assertEqual(policy.quiet_wide_market_minimum_seconds, 600)
        self.assertEqual(policy.quiet_wide_market_minimum_spread, 0.40)

        moment = datetime(2026, 8, 24, 15, 29, 30, tzinfo=SHANGHAI)

        def quoted(
            database: Path, selected_policy, *, previous_close: float,
            intraday_reference: float,
            last: float,
            bids: tuple[tuple[float, float], ...],
            asks: tuple[tuple[float, float], ...],
            selected_moment: datetime = moment,
            last_trade_age_seconds: int = 2_400,
        ) -> tuple[MakerDecisionContext, float | None, tuple[float, ...]]:
            config = test_config(database)
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=selected_policy,
                )
                engine._start_date(selected_moment.date().isoformat())
                engine.previous_close_reference = previous_close
                engine.observed_market_trade = True
                engine.last_market_trade_ts_ms = int(
                    (
                        selected_moment
                        - timedelta(seconds=last_trade_age_seconds)
                    ).timestamp() * 1_000
                )
                engine.last_intraday_working_reference = intraday_reference
                engine.last_intraday_working_reference_ts_ms = int(
                    (selected_moment - timedelta(minutes=40)).timestamp() * 1_000
                )
                tick = replace(
                    self._replay_tick(
                        selected_moment, last=last,
                        bid=bids[0][0], ask=asks[0][0],
                        bid_bonds=bids[0][1], ask_bonds=asks[0][1],
                        trade_bonds=0.0, inferred_side="none",
                    ),
                    bids=bids,
                    asks=asks,
                    previous_close=previous_close,
                )
                assessment = MarketAssessment(
                    reference_price=previous_close,
                    reference_low=previous_close - 0.015,
                    reference_high=previous_close + 0.015,
                    reference_source="previous_close",
                    reference_confidence=0.25,
                    state="stable", state_score=0,
                    state_confidence=0.30,
                    recent_buy_bonds=0.0, recent_sell_bonds=0.0,
                    midpoint_change=0.0, short_ask_change=0.0,
                    largest_ask_gap=0.0,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None, iron_floor_bonds=0.0,
                    evidence=(),
                )
                context = engine._decision_context(tick, selected_policy)
                account = engine.accounts["maker_v01_priority"]
                engine._refresh_orders(
                    account, tick, assessment, persist=True,
                )
                return (
                    context,
                    (
                        account.buy_order.limit_price
                        if account.buy_order is not None else None
                    ),
                    tuple(
                        order.limit_price
                        for order in account.sell_orders.values()
                    ),
                )
            finally:
                store.close()

        three_gorges = {
            "previous_close": 135.824,
            "intraday_reference": 136.041,
            "last": 136.291,
            "bids": (
                (135.801, 2_000.0), (135.800, 2_000.0),
                (135.720, 2_000.0), (135.620, 2_000.0),
                (135.500, 12_000.0),
            ),
            "asks": (
                (136.280, 360.0), (136.299, 2_000.0),
                (136.300, 25_000.0), (136.350, 14_000.0),
                (136.490, 4_000.0),
            ),
        }
        copper = {
            "previous_close": 136.938,
            "intraday_reference": 136.300,
            "last": 136.300,
            "bids": (
                (136.002, 10_000.0), (135.501, 1_000.0),
                (135.100, 74_000.0), (135.000, 5_000.0),
                (134.500, 5_000.0),
            ),
            "asks": (
                (136.887, 1_000.0), (136.888, 1_000.0),
                (137.999, 7_620.0), (138.000, 8_000.0),
                (138.500, 4_180.0),
            ),
        }

        with tempfile.TemporaryDirectory() as temp:
            parent_gorges = quoted(
                Path(temp) / "v146-gorges.sqlite3", parent,
                **three_gorges,
            )
            child_gorges = quoted(
                Path(temp) / "v147-gorges.sqlite3", policy,
                **three_gorges,
            )
            parent_copper = quoted(
                Path(temp) / "v146-copper.sqlite3", parent,
                **copper,
            )
            child_copper = quoted(
                Path(temp) / "v147-copper.sqlite3", policy,
                **copper,
            )

        self.assertEqual(parent_gorges[0].reference_source, "previous_close")
        self.assertIsNone(parent_gorges[1])
        self.assertEqual(parent_gorges[2], (136.279,))
        self.assertEqual(
            child_gorges[0].reference_source,
            "retained_intraday_working_reference",
        )
        self.assertEqual(child_gorges[0].reference_price, 136.041)
        self.assertEqual(child_gorges[1], 135.802)
        # The retained centre restores the definitely missing low bid.  It
        # does not by itself lower the inherited customer-base short gate;
        # the 0.238 high-side premium remains too small for an unconfirmed
        # base sale after the old previous-close distortion is removed.
        self.assertEqual(child_gorges[2], ())

        self.assertEqual(parent_copper[0].reference_source, "previous_close")
        self.assertEqual(parent_copper[1], 136.003)
        self.assertEqual(parent_copper[2], ())
        self.assertEqual(
            child_copper[0].reference_source,
            "retained_intraday_working_reference",
        )
        self.assertEqual(child_copper[0].reference_price, 136.300)
        self.assertEqual(child_copper[1], 136.003)
        self.assertEqual(child_copper[2], (136.886,))

        with tempfile.TemporaryDirectory() as temp:
            shifted = quoted(
                Path(temp) / "v147-shifted.sqlite3", policy,
                previous_close=136.938,
                intraday_reference=136.300,
                last=137.200,
                bids=((137.100, 5_000.0),),
                asks=((137.300, 5_000.0),),
            )
        self.assertEqual(shifted[0].reference_source, "previous_close")

        negative_cases = {
            "before-late-session-window": {
                **copper,
                "selected_moment": datetime(
                    2026, 8, 24, 14, 44, 59, tzinfo=SHANGHAI,
                ),
            },
            "only-599-seconds-without-trade": {
                **copper,
                "last_trade_age_seconds": 599,
            },
            "spread-below-040": {
                **copper,
                "bids": ((136.100, 5_000.0),),
                "asks": ((136.499, 5_000.0),),
            },
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for label, case in negative_cases.items():
                with self.subTest(label=label):
                    result = quoted(
                        root / f"v147-{label}.sqlite3", policy, **case,
                    )
                    self.assertEqual(
                        result[0].reference_source, "previous_close",
                    )

    def test_first_position_v148_uses_one_strict_breakout_episode(self) -> None:
        parent = PRIORITY_POLICY_FIRST_POSITION_V147
        policy = PRIORITY_POLICY_FIRST_POSITION_V148_CANDIDATE
        self.assertEqual(policy.model_id, "maker_priority_v1_48_candidate")
        self.assertEqual(policy.model_version, "1.48-candidate")
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertFalse(parent.enable_strict_breakout_episode)
        self.assertTrue(policy.enable_strict_breakout_episode)
        self.assertFalse(parent.allow_neutral_inventory_sweep_tail)
        self.assertTrue(policy.allow_neutral_inventory_sweep_tail)

        moment = datetime(2026, 8, 25, 10, 25, 45, tzinfo=SHANGHAI)
        anchor = AnchorState(
            support_price=136.000, exit_price=136.199,
            band_midpoint=136.100, reference_price=136.000,
            confidence=0.8, buy_effective_bonds=5_660.0,
            sell_effective_bonds=0.0, downside_pressure=0.0,
            stock_return_5m=0.0, stock_factor=1.0,
            buy_clusters=(), sell_reference_price=None,
        )
        opportunity = Opportunity(
            kind="sweep_tail", signal_ts_ms=int(moment.timestamp() * 1_000),
            market_time=moment.time().isoformat(timespec="milliseconds"),
            entry_price=136.000, quantity_bonds=340.0,
            target_exit_price=136.199, priority_exit_price=136.198,
            theoretical_edge=0.198, anchor=anchor,
            source_wall_bonds=5_694.0, consumed_bonds=5_660.0,
            consumed_ratio=5_660.0 / 5_694.0,
            consumption_seconds=3.0, tail_bonds=340.0,
            next_ask_price=136.199,
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for selected_policy, expected_inventory in (
                (parent, 1_000.0), (policy, 1_340.0),
            ):
                store = SQLiteStore(
                    test_config(root / f"{selected_policy.model_id}.sqlite3")
                )
                try:
                    engine = MakerPaperEngine(
                        test_config(root / "unused.sqlite3"), store,
                        priority_policy=selected_policy,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_priority"]
                    tick = replace(
                        self._replay_tick(
                            moment, last=136.000,
                            bid=135.900, ask=136.000,
                            ask_bonds=340.0,
                        ),
                        asks=((136.000, 340.0), (136.199, 1_000.0)),
                    )
                    engine._active_sweep(
                        account, tick, opportunity, persist=True,
                    )
                    self.assertEqual(account.inventory, expected_inventory)
                finally:
                    store.close()

            config = test_config(root / "episode.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                engine._start_date(moment.date().isoformat())
                engine.previous_close_reference = 135.800
                engine.analyzer.breakout_support_price = 136.000
                engine.analyzer.breakout_support_ts_ms = int(
                    moment.timestamp() * 1_000
                )
                tail = replace(
                    self._replay_tick(
                        moment, last=136.000, bid=135.900, ask=136.000,
                        ask_bonds=340.0,
                    ),
                    asks=((136.000, 340.0), (136.199, 1_000.0)),
                )
                engine._update_strict_breakout_episode_from_book(
                    tail, [opportunity],
                )
                account = engine.accounts["maker_v01_priority"]
                engine._active_sweep(
                    account, tail, opportunity, persist=True,
                )
                self.assertEqual(account.inventory, 1_340.0)
                self.assertFalse(
                    engine._decision_context(tail, policy)
                        .breakout_support_strong
                )

                cleared = replace(
                    tail,
                    market_ts_ms=tail.market_ts_ms + 15_000,
                    market_time="10:26:00.000",
                    asks=((136.199, 1_000.0),),
                )
                engine._update_strict_breakout_episode_from_book(cleared, [])
                self.assertTrue(
                    engine._decision_context(cleared, policy)
                        .breakout_support_strong
                )

                reappeared = replace(
                    cleared,
                    market_ts_ms=cleared.market_ts_ms + 60_000,
                    market_time="10:27:00.000",
                    asks=((135.999, 4_000.0), (136.000, 8_000.0)),
                )
                engine._update_strict_breakout_episode_from_book(
                    reappeared, [],
                )
                self.assertTrue(engine.strict_breakout_failed)
                self.assertFalse(
                    engine._decision_context(reappeared, policy)
                        .breakout_support_strong
                )
                failed_assessment = MarketAssessment(
                    reference_price=135.900,
                    reference_low=135.850,
                    reference_high=136.000,
                    reference_source="intraday_trade_anchor",
                    reference_confidence=0.70,
                    state="possible_fall", state_score=-1,
                    state_confidence=0.70,
                    recent_buy_bonds=5_000.0,
                    recent_sell_bonds=5_000.0,
                    midpoint_change=-0.10, short_ask_change=-0.20,
                    largest_ask_gap=0.0, downside_book_vacuum=False,
                    fragile_top_bid=False, iron_floor_price=None,
                    iron_floor_bonds=0.0, evidence=(),
                )
                engine._refresh_orders(
                    account, reappeared, failed_assessment, persist=True,
                )
                sweep_lot = next(
                    item for item in account.lots.values()
                    if item.kind == "sweep_tail"
                )
                release = account.sell_orders[sweep_lot.db_id]
                self.assertEqual(release.limit_price, 135.998)
                self.assertEqual(
                    release.kind, "failed_breakout_sweep_release",
                )
                later_buy = replace(
                    reappeared,
                    market_ts_ms=reappeared.market_ts_ms + 30_000,
                    market_time="10:28:30.000",
                    last_price=135.999,
                    trade_bonds=1_000.0,
                    transaction_delta=1,
                    inferred_side="buy",
                    side_confidence="high",
                )
                engine._process_resting_orders(
                    account, later_buy, persist=True,
                    received_ts_ns=later_buy.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(sweep_lot.remaining_quantity, 0.0)
            finally:
                store.close()

    def test_first_position_v148_crosses_only_an_isolated_deep_discount(
        self,
    ) -> None:
        policy = PRIORITY_POLICY_FIRST_POSITION_V148_CANDIDATE
        self.assertEqual(
            [
                MakerPaperEngine._deep_discount_for_displayed_bonds(quantity)
                for quantity in (1_000.0, 2_000.0, 5_000.0, 10_000.0, 20_000.0)
            ],
            [0.50, 0.50, 1.00, 1.50, 2.00],
        )
        moment = datetime(2026, 8, 25, 10, 36, 3, tzinfo=SHANGHAI)
        assessment = MarketAssessment(
            reference_price=136.300, reference_low=136.250,
            reference_high=136.350,
            reference_source="intraday_trade_anchor",
            reference_confidence=0.75,
            state="possible_fall", state_score=-1,
            state_confidence=0.70,
            recent_buy_bonds=1_000.0, recent_sell_bonds=6_000.0,
            midpoint_change=-0.10, short_ask_change=-0.10,
            largest_ask_gap=0.0, downside_book_vacuum=False,
            fragile_top_bid=False, iron_floor_price=None,
            iron_floor_bonds=0.0, evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=136.300,
            reference_source="intraday_trade_anchor",
            reliable_anchor=True, spread=0.102,
            bid_support_bonds=20_000.0,
            ask_supply_bonds=18_340.0,
            wall_threshold_bonds=5_000.0,
        )

        def run(asks: tuple[tuple[float, float], ...], name: str) -> float:
            config = test_config(Path(temp) / f"{name}.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                engine._start_date(moment.date().isoformat())
                engine.observed_market_trade = True
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    int((moment - timedelta(seconds=3)).timestamp() * 1_000),
                    136.300, 5_000.0, 1, "buy",
                ))
                tick = replace(
                    self._replay_tick(
                        moment, last=asks[0][0], bid=135.600,
                        ask=asks[0][0], ask_bonds=asks[0][1],
                        bid_bonds=20_000.0,
                    ),
                    bids=((135.603, 5_000.0), (135.602, 8_000.0),
                          (135.600, 8_000.0)),
                    asks=asks,
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._active_discount_entry(
                        engine.accounts["maker_v01_priority"],
                        tick, assessment, persist=True,
                    )
                return engine.accounts["maker_v01_priority"].inventory
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            # Today's 135.702 offer was only about 0.15 below recent trades in
            # the real path and faced dense supply.  Even with a deliberately
            # generous 136.300 test reference, the 0.098 ask gap rejects it.
            self.assertEqual(run((
                (135.702, 1_000.0),
                (135.800, 8_340.0),
                (135.999, 10_000.0),
            ), "dense"), 1_000.0)
            self.assertEqual(run((
                (135.700, 1_000.0),
                (136.250, 1_000.0),
                (136.300, 1_000.0),
            ), "isolated"), 2_000.0)

    def test_first_position_v148_releases_only_the_full_extra_lot(self) -> None:
        policy = PRIORITY_POLICY_FIRST_POSITION_V148_CANDIDATE
        moment = datetime(2026, 8, 25, 10, 40, 45, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "capacity-release.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                entry_tick = self._replay_tick(
                    moment - timedelta(minutes=4, seconds=42),
                    last=135.702, bid=135.600, ask=135.702,
                )
                entry = engine._new_order(
                    account, entry_tick, side="buy",
                    kind="deep_discount_sweep", lot_id=None,
                    price=135.702, quantity=1_000.0,
                    queue_ahead=0.0, target_price=None,
                    price_boundary=135.702, persist=True,
                )
                engine._fill_buy(
                    account, entry_tick, entry, 1_000.0,
                    entry_tick.market_ts_ms * 1_000_000,
                    kind="deep_discount_sweep", target_price=None,
                    persist=True, reason="active_deep_discount",
                )
                lot = next(
                    item for item in account.lots.values()
                    if item.entry_price is not None
                )
                assessment = MarketAssessment(
                    reference_price=136.000, reference_low=135.900,
                    reference_high=136.100,
                    reference_source="intraday_trade_anchor",
                    reference_confidence=0.70,
                    state="possible_fall", state_score=-1,
                    state_confidence=0.75,
                    recent_buy_bonds=1_000.0,
                    recent_sell_bonds=6_000.0,
                    midpoint_change=-0.10, short_ask_change=-0.10,
                    largest_ask_gap=0.0, downside_book_vacuum=False,
                    fragile_top_bid=False, iron_floor_price=None,
                    iron_floor_bonds=0.0, evidence=(),
                )
                context = MakerDecisionContext(
                    reference_price=136.000,
                    reference_source="intraday_trade_anchor",
                    reliable_anchor=True, spread=0.199,
                    bid_support_bonds=8_000.0,
                    ask_supply_bonds=1_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                recovery_window = replace(
                    self._replay_tick(
                        moment, last=135.600,
                        bid=135.600, ask=135.799,
                        bid_bonds=8_000.0,
                    ),
                    bids=((135.600, 8_000.0),),
                    asks=((135.799, 1_000.0), (135.999, 3_000.0)),
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, recovery_window, assessment, persist=True,
                    )
                exit_order = account.sell_orders[lot.db_id]
                self.assertEqual(exit_order.limit_price, 135.798)
                self.assertEqual(
                    exit_order.kind,
                    "full_inventory_capacity_release_exit",
                )

                stop_tick = replace(
                    recovery_window,
                    market_ts_ms=recovery_window.market_ts_ms + 219_000,
                    market_time="10:44:24.000",
                    last_price=135.600,
                    bids=((135.600, 8_000.0),),
                    asks=((135.650, 3_000.0), (135.799, 1_000.0)),
                    trade_bonds=3_000.0,
                    transaction_delta=3,
                    inferred_side="sell",
                    side_confidence="high",
                )
                original_opened_ms = lot.opened_ms
                lot.opened_ms = stop_tick.market_ts_ms
                engine._active_inventory_risk_exit(
                    account, stop_tick, assessment, persist=True,
                    received_ts_ns=stop_tick.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 2_000.0)
                lot.opened_ms = original_opened_ms
                engine._active_inventory_risk_exit(
                    account, stop_tick, assessment, persist=True,
                    received_ts_ns=stop_tick.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(lot.remaining_quantity, 0.0)
                base = next(
                    item for item in account.lots.values()
                    if item.entry_price is None
                )
                self.assertEqual(base.remaining_quantity, 1_000.0)
                reason = store.connection.execute(
                    "SELECT fill_reason FROM maker_paper_fills "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()[0]
                self.assertEqual(
                    reason, "active_full_inventory_capacity_release",
                )
            finally:
                store.close()

    def test_first_position_v149_returns_a_stalled_extra_lot_to_neutral(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_FIRST_POSITION_V148_CANDIDATE
        policy = PRIORITY_POLICY_FIRST_POSITION_V149_CANDIDATE
        self.assertEqual(policy.model_id, "maker_priority_v1_49_candidate")
        self.assertEqual(policy.model_version, "1.49-candidate")
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertFalse(parent.enable_stalled_extra_inventory_near_flat_exit)
        self.assertTrue(policy.enable_stalled_extra_inventory_near_flat_exit)

        entry_time = datetime(2026, 8, 25, 13, 9, 52, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v149-near-flat.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                engine._start_date(entry_time.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                entry_tick = self._replay_tick(
                    entry_time, last=136.002, bid=136.001, ask=136.002,
                )
                entry_order = engine._new_order(
                    account, entry_tick, side="buy",
                    kind="low_bid_reversion", lot_id=None,
                    price=136.002, quantity=1_000.0, queue_ahead=0.0,
                    target_price=None, price_boundary=136.002,
                    persist=True,
                )
                engine._fill_buy(
                    account, entry_tick, entry_order, 1_000.0,
                    entry_tick.market_ts_ms * 1_000_000,
                    kind="low_bid_reversion", target_price=None,
                    persist=True, reason="passive_buy",
                )
                lot = next(
                    item for item in account.lots.values()
                    if item.entry_price is not None
                )

                pressure = MarketAssessment(
                    reference_price=135.975,
                    reference_low=135.900,
                    reference_high=136.050,
                    reference_source="intraday_trade_anchor",
                    reference_confidence=0.75,
                    state="possible_fall", state_score=-1,
                    state_confidence=0.75,
                    recent_buy_bonds=1_000.0,
                    recent_sell_bonds=6_000.0,
                    midpoint_change=-0.30,
                    short_ask_change=-0.50,
                    largest_ask_gap=0.0,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=(),
                )
                context = MakerDecisionContext(
                    reference_price=135.975,
                    reference_source="intraday_trade_anchor",
                    reliable_anchor=True,
                    spread=0.048,
                    bid_support_bonds=4_000.0,
                    ask_supply_bonds=6_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                release_tick = replace(
                    self._replay_tick(
                        entry_time + timedelta(minutes=4, seconds=15),
                        last=135.999, bid=135.951, ask=135.999,
                        bid_bonds=4_000.0,
                    ),
                    bids=((135.951, 4_000.0), (135.900, 2_000.0)),
                    asks=((135.999, 1_000.0), (136.199, 3_000.0)),
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, release_tick, pressure, persist=True,
                    )
                release = account.sell_orders[lot.db_id]
                self.assertEqual(release.limit_price, 135.998)
                self.assertEqual(
                    release.price_boundary, 135.987,
                )
                self.assertEqual(
                    release.kind,
                    "stalled_extra_inventory_near_flat_exit",
                )

                later_buy = replace(
                    release_tick,
                    market_ts_ms=release_tick.market_ts_ms + 33_000,
                    market_time="13:14:40.000",
                    last_price=135.999,
                    trade_bonds=1_000.0,
                    transaction_delta=1,
                    inferred_side="buy",
                    side_confidence="high",
                )
                engine._process_resting_orders(
                    account, later_buy, persist=True,
                    received_ts_ns=later_buy.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(lot.remaining_quantity, 0.0)
                self.assertEqual(
                    account.last_stalled_extra_exit_price, 135.998,
                )

                early_reentry = replace(
                    later_buy,
                    market_ts_ms=later_buy.market_ts_ms + 3_000,
                    market_time="13:14:43.000",
                    last_price=135.999,
                    bids=((135.970, 1_000.0), (135.951, 1_640.0)),
                    asks=((136.199, 1_000.0), (136.200, 1_000.0)),
                    trade_bonds=0.0,
                    transaction_delta=0,
                    inferred_side="none",
                    side_confidence="none",
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, early_reentry, pressure, persist=True,
                    )
                if account.buy_order is not None:
                    self.assertLessEqual(
                        account.buy_order.limit_price, 135.698,
                    )

                # The same holding time is not an exit signal while the upper
                # route remains healthy; the rule is pressure-driven, not a
                # mechanical three-minute stop.
                self.assertFalse(
                    engine._stalled_extra_inventory_near_flat_exit_ready(
                        account, lot, replace(
                            release_tick,
                            asks=((136.500, 1_000.0),),
                        ),
                        replace(
                            pressure,
                            state="stable",
                            short_ask_change=-0.02,
                        ),
                    )
                )
            finally:
                store.close()

    def test_first_position_v149_uses_unpolluted_offer_clusters(
        self,
    ) -> None:
        policy = PRIORITY_POLICY_FIRST_POSITION_V149_CANDIDATE
        self.assertFalse(
            PRIORITY_POLICY_FIRST_POSITION_V148_CANDIDATE
                .enable_unpolluted_isolated_discount_reference
        )
        self.assertTrue(policy.enable_unpolluted_isolated_discount_reference)

        def assessment(reference: float) -> MarketAssessment:
            return MarketAssessment(
                reference_price=reference,
                reference_low=reference - 0.05,
                reference_high=reference + 0.05,
                reference_source="current_midpoint",
                reference_confidence=0.40,
                state="possible_fall", state_score=-1,
                state_confidence=0.70,
                recent_buy_bonds=1_000.0,
                recent_sell_bonds=5_000.0,
                midpoint_change=-0.20,
                short_ask_change=-0.30,
                largest_ask_gap=0.0,
                downside_book_vacuum=False,
                fragile_top_bid=False,
                iron_floor_price=None,
                iron_floor_bonds=0.0,
                evidence=(),
            )

        def context(reference: float, spread: float) -> MakerDecisionContext:
            return MakerDecisionContext(
                reference_price=reference,
                reference_source="current_midpoint",
                reliable_anchor=False,
                spread=spread,
                bid_support_bonds=4_000.0,
                ask_supply_bonds=8_000.0,
                wall_threshold_bonds=5_000.0,
            )

        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v149-clusters.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                first_time = datetime(
                    2026, 8, 25, 13, 26, 55, tzinfo=SHANGHAI,
                )
                engine._start_date(first_time.date().isoformat())
                engine.observed_market_trade = True
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    int((first_time - timedelta(seconds=30)).timestamp() * 1_000),
                    135.900, 2_000.0, 1, "buy",
                ))
                account = engine.accounts["maker_v01_priority"]
                account.last_asks = (
                    (136.199, 6_000.0), (136.200, 2_000.0),
                )
                mistaken = replace(
                    self._replay_tick(
                        first_time, last=135.312,
                        bid=135.301, ask=135.312,
                        ask_bonds=360.0, bid_bonds=4_000.0,
                    ),
                    bids=((135.301, 4_000.0), (135.300, 3_000.0)),
                    asks=((135.312, 360.0), (136.199, 6_000.0)),
                )
                with patch.object(
                    engine, "_decision_context",
                    return_value=context(135.3065, 0.011),
                ):
                    engine._active_discount_entry(
                        account, mistaken, assessment(135.3065), persist=True,
                    )
                self.assertEqual(account.inventory, 1_360.0)
                first_fill = store.connection.execute(
                    "SELECT price,quantity FROM maker_paper_fills "
                    "WHERE fill_reason='active_deep_discount' ORDER BY id"
                ).fetchone()
                self.assertEqual(float(first_fill["price"]), 135.312)
                self.assertEqual(float(first_fill["quantity"]), 360.0)

                # A repeated snapshot at the same price cannot consume the
                # same displayed erroneous order twice.
                with patch.object(
                    engine, "_decision_context",
                    return_value=context(135.3065, 0.011),
                ):
                    engine._active_discount_entry(
                        account,
                        replace(
                            mistaken,
                            market_ts_ms=mistaken.market_ts_ms + 1_000,
                            market_time="13:26:56.000",
                        ),
                        assessment(135.3065), persist=True,
                    )
                self.assertEqual(account.inventory, 1_360.0)
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v149-adjacent-cluster.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                first_time = datetime(
                    2026, 8, 25, 14, 4, 58, tzinfo=SHANGHAI,
                )
                engine._start_date(first_time.date().isoformat())
                engine.observed_market_trade = True
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    int((first_time - timedelta(seconds=30)).timestamp() * 1_000),
                    135.900, 2_000.0, 1, "buy",
                ))
                account = engine.accounts["maker_v01_priority"]
                account.last_asks = ((135.994, 2_000.0),)
                first = replace(
                    self._replay_tick(
                        first_time, last=135.355,
                        bid=135.331, ask=135.355,
                        ask_bonds=40.0, bid_bonds=4_000.0,
                    ),
                    asks=((135.355, 40.0), (135.994, 2_000.0)),
                )
                with patch.object(
                    engine, "_decision_context",
                    return_value=context(135.343, 0.024),
                ):
                    engine._active_discount_entry(
                        account, first, assessment(135.343), persist=True,
                    )
                self.assertEqual(account.inventory, 1_040.0)

                account.last_asks = first.asks
                second = replace(
                    first,
                    market_ts_ms=first.market_ts_ms + 3_000,
                    market_time="14:05:01.000",
                    last_price=135.354,
                    asks=(
                        (135.354, 1_000.0),
                        (135.355, 40.0),
                        (135.994, 2_000.0),
                    ),
                )
                with patch.object(
                    engine, "_decision_context",
                    return_value=context(135.3425, 0.023),
                ):
                    engine._active_discount_entry(
                        account, second, assessment(135.3425), persist=True,
                    )
                self.assertEqual(account.inventory, 2_000.0)
                fills = store.connection.execute(
                    "SELECT price,quantity FROM maker_paper_fills "
                    "WHERE fill_reason='active_deep_discount' ORDER BY id"
                ).fetchall()
                self.assertEqual(
                    [(float(row["price"]), float(row["quantity"])) for row in fills],
                    [(135.355, 40.0), (135.354, 960.0)],
                )

                # A dense descending ask ladder is repricing, not one isolated
                # mistaken seller.  With no pre-anomaly reference at all the
                # same active permission is also unavailable.
                dense = replace(
                    second,
                    market_ts_ms=second.market_ts_ms + 3_000,
                    market_time="14:05:04.000",
                    asks=(
                        (135.300, 100.0), (135.400, 1_000.0),
                        (135.500, 1_000.0), (135.994, 2_000.0),
                    ),
                )
                self.assertIsNone(
                    engine._isolated_deep_discount_decision(
                        account, dense, 135.900, "current_midpoint", policy,
                    )
                )
                engine.analyzer.trade_evidence.clear()
                account.last_asks = ()
                no_reference = replace(
                    dense,
                    asks=((135.300, 100.0), (135.994, 2_000.0)),
                )
                self.assertIsNone(
                    engine._isolated_deep_discount_decision(
                        account, no_reference, 135.295, "current_midpoint", policy,
                    )
                )
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    no_reference.market_ts_ms - 3_000,
                    135.250, 2_000.0, 1, "sell",
                ))
                account.last_asks = ((135.994, 2_000.0),)
                self.assertIsNone(
                    engine._isolated_deep_discount_decision(
                        account, no_reference, 135.900, "current_midpoint", policy,
                    )
                )
            finally:
                store.close()

    def test_first_position_v146_requires_joint_evidence_and_deep_support_size(
        self,
    ) -> None:
        moment = datetime(2026, 8, 24, 13, 23, 2, tzinfo=SHANGHAI)
        assessment = MarketAssessment(
            reference_price=136.200,
            reference_low=136.200,
            reference_high=136.399,
            reference_source="intraday_trade_anchor",
            reference_confidence=0.75,
            state="stable", state_score=0, state_confidence=0.75,
            recent_buy_bonds=2_000.0, recent_sell_bonds=4_000.0,
            midpoint_change=0.0, short_ask_change=0.0,
            largest_ask_gap=0.0, downside_book_vacuum=False,
            fragile_top_bid=False, iron_floor_price=None,
            iron_floor_bonds=0.0, evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=136.200,
            reference_source="intraday_trade_anchor",
            reliable_anchor=True,
            spread=0.332,
            bid_support_bonds=8_000.0,
            ask_supply_bonds=18_500.0,
            wall_threshold_bonds=5_000.0,
        )
        asks = ((136.399, 4_000.0), (136.400, 14_500.0))

        def decision(*, bids, high_trade_bonds=6_000.0,
                     selected_assessment=assessment,
                     selected_context=context, selected_asks=asks,
                     fixed_buy_price=None, fixed_sell_price=None,
                     history_bids=None, history_asks=None):
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(Path(temp) / "joint-evidence.sqlite3")
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store,
                        priority_policy=PRIORITY_POLICY_FIRST_POSITION_V146,
                    )
                    engine._start_date(moment.date().isoformat())
                    if high_trade_bonds > 0:
                        engine.analyzer.trade_evidence.append(TradeEvidence(
                            int((moment - timedelta(seconds=30)).timestamp() * 1_000),
                            136.398,
                            high_trade_bonds,
                            2,
                            "sell",
                        ))
                    tick = replace(
                        self._replay_tick(
                            moment, last=136.399,
                            bid=bids[0][0], ask=selected_asks[0][0],
                            bid_bonds=bids[0][1],
                            ask_bonds=selected_asks[0][1],
                            trade_bonds=0.0, inferred_side="none",
                        ),
                        bids=bids,
                        asks=selected_asks,
                    )
                    for seconds in (-36, -18, 0):
                        engine._observe_joint_corridor_book(replace(
                            tick,
                            market_ts_ms=int(
                                (
                                    moment + timedelta(seconds=seconds)
                                ).timestamp() * 1_000
                            ),
                            bids=(
                                history_bids
                                if seconds < 0 and history_bids is not None
                                else tick.bids
                            ),
                            asks=(
                                history_asks
                                if seconds < 0 and history_asks is not None
                                else tick.asks
                            ),
                        ))
                    account = engine.accounts["maker_v01_priority"]
                    return engine._joint_causal_corridor_two_sided_quote(
                        account, tick, selected_assessment, selected_context,
                        fixed_buy_price=fixed_buy_price,
                        fixed_sell_price=fixed_sell_price,
                    )
                finally:
                    store.close()

        target_bids = (
            (136.067, 2_000.0), (136.060, 2_000.0),
            (136.040, 2_000.0), (136.020, 2_000.0),
        )
        self.assertIsNotNone(decision(bids=target_bids))
        self.assertIsNone(decision(
            bids=target_bids, high_trade_bonds=0.0,
        ))
        self.assertIsNone(decision(
            bids=((136.067, 2_000.0), (136.060, 2_000.0)),
        ))
        self.assertIsNone(decision(
            bids=((136.067, 1_100.0), (136.060, 4_000.0)),
        ))
        self.assertIsNotNone(decision(
            bids=((136.067, 2_000.0), (136.060, 4_000.0)),
        ))
        self.assertIsNone(decision(
            bids=((136.067, 1_000.0), (135.980, 8_000.0)),
        ))
        exceptional = decision(
            bids=((136.067, 1_000.0), (135.980, 9_000.0)),
        )
        self.assertIsNotNone(exceptional)
        assert exceptional is not None
        self.assertTrue(exceptional.exceptional_support)
        self.assertEqual(exceptional.entry_bonds, 10_000.0)
        self.assertIsNone(decision(
            bids=target_bids,
            selected_asks=((136.399, 2_000.0), (136.400, 2_000.0)),
        ))
        self.assertIsNone(decision(
            bids=target_bids,
            selected_asks=((136.399, 4_000.0), (136.400, 7_000.0)),
        ))
        self.assertIsNone(decision(
            bids=target_bids,
            history_bids=(
                (136.067, 1_000.0),
                (136.060, 1_000.0),
                (136.040, 1_000.0),
                (136.020, 1_000.0),
            ),
        ))
        self.assertIsNone(decision(
            bids=target_bids,
            history_asks=((136.399, 2_000.0), (136.400, 2_000.0)),
        ))
        self.assertIsNone(decision(
            bids=target_bids,
            selected_assessment=replace(assessment, state="rising"),
        ))
        broad_breakout_context = replace(
            context,
            breakout_support_price=136.350,
            breakout_lower_sell_bonds=0.0,
        )
        self.assertIsNotNone(decision(
            bids=target_bids,
            selected_context=broad_breakout_context,
        ))
        self.assertIsNone(decision(
            bids=((136.396, 5_000.0), *target_bids),
            selected_context=broad_breakout_context,
            fixed_buy_price=136.068,
            fixed_sell_price=136.398,
        ))

    def test_first_position_v146_reassigns_inventory_after_either_fill(
        self,
    ) -> None:
        moment = datetime(2026, 8, 24, 13, 23, 2, tzinfo=SHANGHAI)
        bids = (
            (136.067, 2_000.0), (136.060, 2_000.0),
            (136.040, 2_000.0), (136.020, 2_000.0),
        )
        asks = ((136.399, 4_000.0), (136.400, 14_500.0))
        assessment = MarketAssessment(
            reference_price=136.200, reference_low=136.200,
            reference_high=136.399,
            reference_source="intraday_trade_anchor",
            reference_confidence=0.75,
            state="stable", state_score=0, state_confidence=0.75,
            recent_buy_bonds=2_000.0, recent_sell_bonds=4_000.0,
            midpoint_change=0.0, short_ask_change=0.0,
            largest_ask_gap=0.0, downside_book_vacuum=False,
            fragile_top_bid=False, iron_floor_price=None,
            iron_floor_bonds=0.0, evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=136.200,
            reference_source="intraday_trade_anchor",
            reliable_anchor=True, spread=0.332,
            bid_support_bonds=8_000.0, ask_supply_bonds=18_500.0,
            wall_threshold_bonds=5_000.0,
        )

        def seeded(database: Path):
            config = test_config(database)
            store = SQLiteStore(config)
            engine = MakerPaperEngine(
                config, store,
                priority_policy=PRIORITY_POLICY_FIRST_POSITION_V146,
            )
            engine._start_date(moment.date().isoformat())
            engine.analyzer.trade_evidence.append(TradeEvidence(
                int((moment - timedelta(seconds=30)).timestamp() * 1_000),
                136.398, 6_000.0, 2, "sell",
            ))
            tick = replace(
                self._replay_tick(
                    moment, last=136.399,
                    bid=bids[0][0], ask=asks[0][0],
                    bid_bonds=bids[0][1], ask_bonds=asks[0][1],
                    trade_bonds=0.0, inferred_side="none",
                ),
                bids=bids, asks=asks,
            )
            for seconds in (-36, -18, 0):
                engine._observe_joint_corridor_book(replace(
                    tick,
                    market_ts_ms=int(
                        (moment + timedelta(seconds=seconds)).timestamp()
                        * 1_000
                    ),
                ))
            account = engine.accounts["maker_v01_priority"]
            with patch.object(
                engine, "_decision_context", return_value=context,
            ):
                engine._refresh_orders(
                    account, tick, assessment, persist=True,
                )
            return store, engine, account, tick

        with tempfile.TemporaryDirectory() as temp:
            store, engine, account, tick = seeded(
                Path(temp) / "buy-first.sqlite3",
            )
            try:
                assert account.buy_order is not None
                engine._fill_buy(
                    account, tick, account.buy_order, 1_000.0,
                    tick.market_ts_ms * 1_000_000,
                    kind="joint_causal_corridor_entry",
                    target_price=136.398,
                    persist=True,
                )
                self.assertEqual(account.inventory, 2_000.0)
                next_tick = replace(
                    tick,
                    market_ts_ms=tick.market_ts_ms + 3_000,
                    market_time="13:23:05.000",
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, next_tick, assessment, persist=True,
                    )
                extra_lot = next(
                    lot for lot in account.lots.values()
                    if lot.kind == "joint_causal_corridor_entry"
                )
                self.assertEqual(set(account.sell_orders), {extra_lot.db_id})
                self.assertEqual(
                    account.sell_orders[extra_lot.db_id].limit_price,
                    136.398,
                )

                damaged = replace(
                    next_tick,
                    market_ts_ms=next_tick.market_ts_ms + 3_000,
                    market_time="13:23:08.000",
                    last_price=136.060,
                    trade_bonds=4_500.0,
                    inferred_side="sell",
                    bids=(
                        (136.060, 2_000.0),
                        (136.040, 1_000.0),
                        (136.020, 500.0),
                    ),
                )
                engine._active_adjacent_bid_cushion_risk_exit(
                    account, damaged, assessment, persist=True,
                    received_ts_ns=damaged.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(extra_lot.remaining_quantity, 0.0)
                fill_reason = store.connection.execute(
                    "SELECT fill_reason FROM maker_paper_fills "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()[0]
                self.assertEqual(
                    fill_reason,
                    "active_joint_causal_corridor_risk_exit",
                )
            finally:
                store.close()

            store, engine, account, tick = seeded(
                Path(temp) / "sell-first.sqlite3",
            )
            try:
                assert account.buy_order is not None
                buy_id = account.buy_order.db_id
                base_sell = next(iter(account.sell_orders.values()))
                engine._fill_sell(
                    account, tick, base_sell, 1_000.0,
                    tick.market_ts_ms * 1_000_000,
                    persist=True,
                )
                self.assertEqual(account.inventory, 0.0)
                self.assertEqual(account.customer_base_short_bonds, 1_000.0)
                self.assertEqual(
                    account.joint_corridor_base_short_bonds, 1_000.0,
                )
                self.assertEqual(
                    account.joint_corridor_base_short_ask_supply_bonds,
                    18_500.0,
                )
                self.assertEqual(
                    account.pending_repeated_turn_replenishment_price,
                    136.068,
                )
                account.last_bid = 136.101
                account.last_bids = (
                    (136.101, 2_000.0),
                    (136.100, 1_000.0),
                    (136.061, 2_000.0),
                    (136.060, 2_000.0),
                    (136.040, 2_000.0),
                )
                next_tick = replace(
                    tick,
                    market_ts_ms=tick.market_ts_ms + 3_000,
                    market_time="13:23:05.000",
                    bids=(
                        (136.250, 2_000.0),
                        (136.101, 2_000.0),
                        (136.100, 1_000.0),
                        (136.060, 2_000.0),
                        (136.040, 2_000.0),
                    ),
                )
                flicker_context = replace(
                    context,
                    breakout_support_price=136.250,
                    breakout_lower_sell_bonds=0.0,
                )
                with patch.object(
                    engine, "_decision_context",
                    return_value=flicker_context,
                ):
                    stopped = engine._active_joint_corridor_base_short_stop(
                        account, next_tick, assessment, persist=True,
                        received_ts_ns=next_tick.market_ts_ms * 1_000_000,
                    )
                    self.assertFalse(stopped)
                    engine._refresh_orders(
                        account, next_tick, assessment, persist=True,
                    )
                assert account.buy_order is not None
                self.assertEqual(account.buy_order.db_id, buy_id)
                self.assertEqual(
                    account.buy_order.kind, "joint_causal_corridor_entry",
                )
                engine._fill_buy(
                    account, next_tick, account.buy_order, 1_000.0,
                    next_tick.market_ts_ms * 1_000_000,
                    kind="joint_causal_corridor_entry",
                    target_price=136.398,
                    persist=True,
                )
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(account.customer_base_short_bonds, 0.0)
                self.assertFalse(any(
                    lot.kind == "joint_causal_corridor_entry"
                    and lot.remaining_quantity > 1e-9
                    for lot in account.lots.values()
                ))
            finally:
                store.close()

            store, engine, account, tick = seeded(
                Path(temp) / "sell-first-wall-withdrawal.sqlite3",
            )
            try:
                base_sell = next(iter(account.sell_orders.values()))
                engine._fill_sell(
                    account, tick, base_sell, 1_000.0,
                    tick.market_ts_ms * 1_000_000,
                    persist=True,
                )
                withdrawn_wall = replace(
                    tick,
                    market_ts_ms=tick.market_ts_ms + 3_000,
                    market_time="13:23:05.000",
                    asks=((136.399, 1_000.0), (136.400, 1_000.0)),
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    stopped = engine._active_joint_corridor_base_short_stop(
                        account, withdrawn_wall, assessment, persist=True,
                        received_ts_ns=(
                            withdrawn_wall.market_ts_ms * 1_000_000
                        ),
                    )
                self.assertTrue(stopped)
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(account.customer_base_short_bonds, 0.0)
                self.assertEqual(
                    account.joint_corridor_base_short_bonds, 0.0,
                )
                fill_reason = store.connection.execute(
                    "SELECT fill_reason FROM maker_paper_fills "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()[0]
                self.assertEqual(
                    fill_reason,
                    "active_joint_causal_corridor_base_short_stop",
                )
            finally:
                store.close()

    def test_first_position_v144_exits_at_half_on_damage_but_waits_to_two_times_when_safe(
        self,
    ) -> None:
        moment = datetime(2026, 8, 24, 9, 56, tzinfo=SHANGHAI)

        def seeded_extra_lot(database: Path):
            config = test_config(database)
            store = SQLiteStore(config)
            engine = MakerPaperEngine(
                config, store,
                priority_policy=PRIORITY_POLICY_FIRST_POSITION_V144,
            )
            engine._start_date(moment.date().isoformat())
            account = engine.accounts["maker_v01_priority"]
            tick = replace(
                self._replay_tick(
                    moment, last=136.602, bid=136.601, ask=136.900,
                    bid_bonds=4_000.0,
                ),
                bids=((136.601, 4_000.0), (136.600, 5_000.0)),
            )
            order = engine._new_order(
                account, tick, side="buy", kind="adjacent_bid_cushion_entry",
                lot_id=None, price=136.602, quantity=1_000.0,
                price_boundary=136.602, queue_ahead=0.0,
                target_price=None, persist=True,
                protective_bid_floor_price=136.600,
                protective_bid_ceiling_price=136.601,
                protective_bid_entry_bonds=9_000.0,
                protective_bid_entry_edge=0.297,
            )
            account.buy_order = order
            engine._fill_buy(
                account, tick, order, 1_000.0,
                tick.market_ts_ms * 1_000_000,
                kind="adjacent_bid_cushion_entry",
                target_price=None, persist=True,
            )
            lot = next(
                item for item in account.lots.values()
                if item.kind == "adjacent_bid_cushion_entry"
            )
            return store, engine, account, lot

        with tempfile.TemporaryDirectory() as temp:
            store, engine, account, lot = seeded_extra_lot(
                Path(temp) / "damaged-cushion.sqlite3",
            )
            try:
                damaged = replace(
                    self._replay_tick(
                        moment + timedelta(seconds=3),
                        last=136.601, bid=136.601, ask=136.900,
                        bid_bonds=4_000.0, trade_bonds=4_500.0,
                        inferred_side="sell",
                    ),
                    bids=((136.601, 4_000.0), (136.600, 500.0)),
                )
                engine._active_adjacent_bid_cushion_risk_exit(
                    account, damaged, MarketAssessment(
                        reference_price=136.7505,
                        reference_low=136.601,
                        reference_high=136.900,
                        reference_source="persistent_inside_market",
                        reference_confidence=0.55,
                        state="stable", state_score=0,
                        state_confidence=0.75,
                        recent_buy_bonds=0.0, recent_sell_bonds=4_500.0,
                        midpoint_change=0.0, short_ask_change=0.0,
                        largest_ask_gap=0.0,
                        downside_book_vacuum=False, fragile_top_bid=False,
                        iron_floor_price=None, iron_floor_bonds=0.0,
                        evidence=(),
                    ),
                    persist=True,
                    received_ts_ns=damaged.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(lot.remaining_quantity, 0.0)
                base = next(
                    item for item in account.lots.values()
                    if item.kind == "base"
                )
                self.assertEqual(base.remaining_quantity, 1_000.0)
                fill = store.connection.execute(
                    "SELECT quantity,fill_reason FROM maker_paper_fills "
                    "WHERE fill_reason='active_adjacent_bid_cushion_risk_exit'"
                ).fetchone()
                self.assertEqual(float(fill["quantity"]), 1_000.0)
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as temp:
            store, engine, account, lot = seeded_extra_lot(
                Path(temp) / "safe-cushion.sqlite3",
            )
            try:
                lot.protective_bid_last_bonds = 3_000.0
                lot.protective_bid_last_damage_ts_ms = 0
                assessment = MarketAssessment(
                    reference_price=136.7505,
                    reference_low=136.601,
                    reference_high=136.900,
                    reference_source="persistent_inside_market",
                    reference_confidence=0.55,
                    state="stable", state_score=0,
                    state_confidence=0.75,
                    recent_buy_bonds=0.0, recent_sell_bonds=0.0,
                    midpoint_change=0.0, short_ask_change=0.0,
                    largest_ask_gap=0.0,
                    downside_book_vacuum=False, fragile_top_bid=False,
                    iron_floor_price=None, iron_floor_bonds=0.0,
                    evidence=(),
                )
                three_times = replace(
                    self._replay_tick(
                        moment + timedelta(seconds=40),
                        last=136.700, bid=136.601, ask=136.900,
                        bid_bonds=2_000.0,
                    ),
                    bids=((136.601, 2_000.0), (136.600, 1_000.0)),
                )
                engine._active_adjacent_bid_cushion_risk_exit(
                    account, three_times, assessment, persist=True,
                    received_ts_ns=three_times.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 2_000.0)

                two_times = replace(
                    three_times,
                    market_ts_ms=three_times.market_ts_ms + 40_000,
                    market_time=(moment + timedelta(seconds=80)).time().isoformat(
                        timespec="milliseconds"
                    ),
                    bids=((136.601, 1_500.0), (136.600, 500.0)),
                )
                engine._active_adjacent_bid_cushion_risk_exit(
                    account, two_times, assessment, persist=True,
                    received_ts_ns=two_times.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(lot.remaining_quantity, 0.0)
            finally:
                store.close()

    def test_priority_v140_prepositions_only_a_recent_safe_high_ask_cluster(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_V137_CANDIDATE
        policy = PRIORITY_POLICY_V140_CANDIDATE
        self.assertFalse(
            parent.enable_post_replenishment_high_ask_cluster_preposition,
        )
        self.assertTrue(
            policy.enable_post_replenishment_high_ask_cluster_preposition,
        )
        moment = datetime(2026, 8, 17, 10, 0, 5, tzinfo=SHANGHAI)
        moment_ms = int(moment.timestamp() * 1_000)
        assessment = MarketAssessment(
            reference_price=135.522,
            reference_low=135.400,
            reference_high=135.644,
            reference_source="current_midpoint",
            reference_confidence=0.45,
            state="possible_fall",
            state_score=-1,
            state_confidence=0.70,
            recent_buy_bonds=0.0,
            recent_sell_bonds=2_000.0,
            midpoint_change=-0.0005,
            short_ask_change=0.0,
            largest_ask_gap=0.340,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=135.522,
            reference_source="current_midpoint",
            reliable_anchor=False,
            spread=0.244,
            bid_support_bonds=3_000.0,
            ask_supply_bonds=350.0,
            wall_threshold_bonds=5_000.0,
        )

        def quoted_sell(candidate_policy, *, cluster_supply: float = 7_000.0,
                        age_seconds: int = 0, state: str = "possible_fall"):
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(Path(temp) / "v140-high-cluster.sqlite3")
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, priority_policy=candidate_policy,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_priority"]
                    account.last_completed_base_turn_sell_price = 135.995
                    account.last_completed_base_turn_buy_price = 135.402
                    account.last_completed_base_turn_ts_ms = (
                        moment_ms - age_seconds * 1_000
                    )
                    cluster_parts = (
                        cluster_supply / 7.0,
                        cluster_supply * 4.0 / 7.0,
                        cluster_supply / 7.0,
                        cluster_supply / 7.0,
                    )
                    tick = replace(
                        self._replay_tick(
                            moment, last=135.401, bid=135.400, ask=135.644,
                            bid_bonds=1_000.0, ask_bonds=350.0,
                            trade_bonds=1_000.0, inferred_side="buy",
                        ),
                        asks=(
                            (135.644, 350.0),
                            (135.984, cluster_parts[0]),
                            (135.985, cluster_parts[1]),
                            (135.987, cluster_parts[2]),
                            (135.989, cluster_parts[3]),
                        ),
                    )
                    with patch.object(
                        engine, "_decision_context", return_value=context,
                    ):
                        engine._refresh_orders(
                            account, tick, replace(assessment, state=state),
                            persist=True,
                        )
                    orders = list(account.sell_orders.values())
                    return account.inventory, [
                        (order.kind, order.limit_price) for order in orders
                    ]
                finally:
                    store.close()

        inventory, orders = quoted_sell(policy)
        self.assertEqual(inventory, 1_000.0)
        self.assertEqual(
            orders, [("high_ask_cluster_base_preposition", 135.983)],
        )
        # Creating the order from a frame that already contains a buy print
        # does not back-fill it; only a later frame may execute the order.
        self.assertEqual(quoted_sell(parent)[1], [])
        self.assertEqual(quoted_sell(policy, cluster_supply=4_999.0)[1], [])
        self.assertEqual(quoted_sell(policy, age_seconds=601)[1], [])
        self.assertEqual(quoted_sell(policy, state="rising")[1], [])

        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v140-residual-fill.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                lot_id = next(iter(account.lots))
                seed = replace(
                    self._replay_tick(
                        moment, last=135.401, bid=135.400, ask=135.644,
                        bid_bonds=1_000.0, ask_bonds=350.0,
                        trade_bonds=0.0, inferred_side="none",
                    ),
                    asks=(
                        (135.644, 350.0),
                        (135.984, 1_000.0),
                        (135.985, 4_000.0),
                        (135.987, 1_000.0),
                        (135.989, 1_000.0),
                    ),
                )
                order = engine._new_order(
                    account, seed, side="sell",
                    kind="high_ask_cluster_base_preposition",
                    lot_id=lot_id, price=135.983, quantity=1_000.0,
                    queue_ahead=0.0, target_price=135.983, persist=True,
                )
                account.sell_orders[lot_id] = order
                account.last_asks = seed.asks
                swept = replace(
                    seed,
                    market_ts_ms=seed.market_ts_ms + 9_000,
                    market_time="10:00:14.000",
                    last_price=135.984,
                    trade_bonds=1_000.0,
                    inferred_side="buy",
                    asks=(
                        (135.984, 350.0),
                        (135.985, 4_000.0),
                        (135.987, 1_000.0),
                        (135.989, 1_000.0),
                    ),
                )
                engine._process_resting_orders(
                    account, swept, persist=True,
                    received_ts_ns=swept.market_ts_ms * 1_000_000,
                )
                self.assertEqual(order.filled_quantity, 650.0)
                self.assertEqual(account.inventory, 350.0)
            finally:
                store.close()

    def test_priority_v135_retains_only_the_same_continuous_wall_lifecycle(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_V134_CANDIDATE
        policy = PRIORITY_POLICY_V135_CANDIDATE
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertFalse(
            parent.retain_persistent_wall_supported_falling_extra_entry,
        )
        self.assertTrue(
            policy.retain_persistent_wall_supported_falling_extra_entry,
        )

        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v135-retention.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                created = datetime(2026, 8, 13, 9, 56, 45, tzinfo=SHANGHAI)
                engine._start_date(created.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                seed = replace(
                    self._replay_tick(
                        created, last=137.201, bid=137.201, ask=137.547,
                    ),
                    bids=((137.201, 2_960.0), (137.200, 5_000.0)),
                )
                order = engine._new_order(
                    account, seed, side="buy",
                    kind="persistent_wall_supported_falling_entry",
                    lot_id=None, price=137.202, quantity=1_000.0,
                    queue_ahead=0.0, target_price=None, persist=True,
                )
                order.visible_wall_entry_price = 137.200
                account.buy_order = order
                engine.visible_bid_wall_first_seen_ms = {
                    137.200: seed.market_ts_ms - 900_000,
                }
                context = MakerDecisionContext(
                    reference_price=137.374,
                    reference_source="current_midpoint",
                    reliable_anchor=False,
                    spread=0.345,
                    bid_support_bonds=7_960.0,
                    ask_supply_bonds=1_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                assessment = MarketAssessment(
                    reference_price=137.374,
                    reference_low=137.200,
                    reference_high=137.547,
                    reference_source="current_midpoint",
                    reference_confidence=0.5,
                    state="stable",
                    state_score=0,
                    state_confidence=0.5,
                    recent_buy_bonds=0.0,
                    recent_sell_bonds=190.0,
                    midpoint_change=0.0,
                    short_ask_change=0.0,
                    largest_ask_gap=0.20,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=(),
                )
                retained_tick = replace(
                    seed,
                    market_ts_ms=seed.market_ts_ms + 171_000,
                    market_time="09:59:36.000",
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, retained_tick, assessment, persist=True,
                    )
                self.assertIsNotNone(account.buy_order)
                assert account.buy_order is not None
                self.assertEqual(account.buy_order.db_id, order.db_id)
                self.assertEqual(account.buy_order.limit_price, 137.202)

                # The parent cannot retain the order under the same stale-high-
                # buy state, and the candidate immediately loses permission if
                # the original wall disappears or is replaced.
                account.policy = parent
                self.assertIsNone(
                    engine._retain_persistent_wall_supported_falling_extra_entry(
                        account, order, retained_tick, assessment, context,
                        confirmed_rise_recent=False,
                        falling_profitable_reentry_active=False,
                        in_entry_window=True,
                    )
                )

                relabel_policy = PRIORITY_POLICY_V136_CANDIDATE
                self.assertEqual(relabel_policy.parent_model_id, policy.model_id)
                account.policy = relabel_policy
                rising_assessment = replace(assessment, state="rising")
                tight_context = replace(context, spread=0.05)
                self.assertIsNotNone(
                    engine._retain_persistent_wall_supported_falling_extra_entry(
                        account, order, retained_tick,
                        rising_assessment, tight_context,
                        confirmed_rise_recent=True,
                        falling_profitable_reentry_active=False,
                        in_entry_window=True,
                    )
                )
                narrow_ask_tick = replace(retained_tick, asks=((137.300, 1_000.0),))
                self.assertIsNone(
                    engine._retain_persistent_wall_supported_falling_extra_entry(
                        account, order, narrow_ask_tick,
                        rising_assessment, tight_context,
                        confirmed_rise_recent=True,
                        falling_profitable_reentry_active=False,
                        in_entry_window=True,
                    )
                )
                account.policy = policy
                engine.visible_bid_wall_first_seen_ms[137.200] = (
                    order.created_ms + 1
                )
                self.assertIsNone(
                    engine._retain_persistent_wall_supported_falling_extra_entry(
                        account, order, retained_tick, assessment, context,
                        confirmed_rise_recent=False,
                        falling_profitable_reentry_active=False,
                        in_entry_window=True,
                    )
                )
                engine.visible_bid_wall_first_seen_ms[137.200] = (
                    seed.market_ts_ms - 900_000
                )
                too_old_tick = replace(
                    retained_tick,
                    market_ts_ms=seed.market_ts_ms + 301_000,
                    market_time="10:01:46.000",
                )
                self.assertIsNone(
                    engine._retain_persistent_wall_supported_falling_extra_entry(
                        account, order, too_old_tick, assessment, context,
                        confirmed_rise_recent=False,
                        falling_profitable_reentry_active=False,
                        in_entry_window=True,
                    )
                )
                self.assertIsNone(
                    engine._retain_persistent_wall_supported_falling_extra_entry(
                        account, order, retained_tick, assessment, context,
                        confirmed_rise_recent=True,
                        falling_profitable_reentry_active=False,
                        in_entry_window=True,
                    )
                )
            finally:
                store.close()

    def test_priority_v134_requires_a_later_real_sell_to_fill(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v134-passive-fill.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V134_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 14, 49, 45, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                seed = self._replay_tick(
                    moment, last=135.401, bid=135.401, ask=135.602,
                )
                order = engine._new_order(
                    account, seed, side="buy",
                    kind="persistent_wall_supported_falling_entry",
                    lot_id=None, price=135.402, quantity=1_000.0,
                    queue_ahead=0.0, target_price=None, persist=True,
                )
                account.buy_order = order
                later_buy = self._replay_tick(
                    moment + timedelta(seconds=3),
                    last=135.606, bid=135.401, ask=135.606,
                    trade_bonds=1_000.0, inferred_side="buy",
                )
                engine._process_resting_orders(
                    account, later_buy, persist=True,
                    received_ts_ns=later_buy.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_000.0)

                later_sell = self._replay_tick(
                    moment + timedelta(seconds=18),
                    last=135.401, bid=135.401, ask=135.602,
                    trade_bonds=380.0, inferred_side="sell",
                )
                engine._process_resting_orders(
                    account, later_sell, persist=True,
                    received_ts_ns=later_sell.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_380.0)
                self.assertIsNotNone(account.buy_order)
                assert account.buy_order is not None
                self.assertEqual(account.buy_order.remaining, 620.0)
            finally:
                store.close()

    def test_priority_v134_wall_duration_resets_after_disappearance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v134-wall-reset.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_V134_CANDIDATE,
                )
                moment = datetime(2026, 8, 14, 14, 35, 45, tzinfo=SHANGHAI)
                wall_tick = replace(
                    self._replay_tick(
                        moment, last=136.111, bid=136.111, ask=136.310,
                    ),
                    bids=((136.111, 1_000.0), (136.110, 9_000.0)),
                )
                engine._update_visible_bid_wall(wall_tick)
                first_seen = engine.visible_bid_wall_first_seen_ms[136.11]
                missing_tick = replace(
                    wall_tick,
                    market_ts_ms=wall_tick.market_ts_ms + 30_000,
                    market_time="14:36:15.000",
                    bids=((136.111, 1_000.0), (136.110, 4_999.0)),
                )
                engine._update_visible_bid_wall(missing_tick)
                self.assertNotIn(136.11, engine.visible_bid_wall_first_seen_ms)
                restored_tick = replace(
                    wall_tick,
                    market_ts_ms=wall_tick.market_ts_ms + 33_000,
                    market_time="14:36:18.000",
                )
                engine._update_visible_bid_wall(restored_tick)
                self.assertEqual(
                    engine.visible_bid_wall_first_seen_ms[136.11],
                    restored_tick.market_ts_ms,
                )
                self.assertGreater(
                    engine.visible_bid_wall_first_seen_ms[136.11], first_seen,
                )
            finally:
                store.close()

    def test_queue_v116_registers_the_underlying_mapping_correction(self) -> None:
        self.assertEqual(
            QUEUE_POLICY_V116_CANDIDATE.parent_model_id,
            QUEUE_POLICY_V115_CANDIDATE.model_id,
        )
        self.assertEqual(
            replace(
                QUEUE_POLICY_V116_CANDIDATE,
                model_id=QUEUE_POLICY_V115_CANDIDATE.model_id,
                model_version=QUEUE_POLICY_V115_CANDIDATE.model_version,
                parent_model_id=QUEUE_POLICY_V115_CANDIDATE.parent_model_id,
            ),
            QUEUE_POLICY_V115_CANDIDATE,
        )

    def test_tight_market_takes_a_profitable_bid_for_fast_turnover(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-tight-turnover.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("priority",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            moment = datetime(2026, 8, 14, 14, 5, 33, tzinfo=SHANGHAI)
            seed = self._replay_tick(
                moment, last=136.002, bid=136.001, ask=136.880,
                previous_close=136.400,
            )
            engine.on_replay_tick(seed, persist=True)
            account = engine.accounts["maker_v01_priority"]
            order = engine._new_order(
                account, seed, side="buy", kind="low_bid_reversion",
                lot_id=None, price=136.002, quantity=1_000,
                queue_ahead=0.0, target_price=None, persist=True,
            )
            account.buy_order = order
            engine._fill_buy(
                account, seed, order, 1_000,
                seed.market_ts_ms * 1_000_000,
                kind="low_bid_reversion", target_price=None, persist=True,
            )

            tight = replace(
                self._replay_tick(
                    moment + timedelta(minutes=2),
                    last=136.007, bid=136.291, ask=136.299,
                    bid_bonds=1_000, previous_close=136.400,
                ),
                bids=(
                    (136.291, 1_000),
                    (136.290, 1_000),
                    (136.031, 2_000),
                ),
            )
            engine.on_replay_tick(tight, persist=True)

            self.assertEqual(account.inventory, 1_000)
            fill = store.connection.execute(
                """SELECT price,quantity,fill_reason FROM maker_paper_fills
                   WHERE fill_reason='active_tight_spread_turnover'"""
            ).fetchone()
            self.assertEqual(float(fill["price"]), 136.291)
            self.assertEqual(float(fill["quantity"]), 1_000)
            store.close()

    def test_v149_r2_waits_when_an_isolated_low_offer_only_narrows_spread(
        self,
    ) -> None:
        self.assertEqual(
            PRIORITY_POLICY_FIRST_POSITION_V149_R2_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_FIRST_POSITION_V149_CANDIDATE.model_id,
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v149-r2-offer-hold.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=(
                        PRIORITY_POLICY_FIRST_POSITION_V149_R2_CANDIDATE
                    ),
                )
                moment = datetime(2026, 8, 25, 14, 5, 1, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                entry_tick = replace(
                    self._replay_tick(
                        moment, last=135.999, bid=135.331, ask=135.354,
                        ask_bonds=960.0,
                    ),
                    asks=((135.354, 960.0), (135.355, 40.0), (135.994, 2_000.0)),
                )
                entry = engine._new_order(
                    account, entry_tick, side="buy", kind="deep_discount_sweep",
                    lot_id=None, price=135.354, quantity=1_000.0,
                    queue_ahead=0.0, target_price=135.999, persist=True,
                )
                account.buy_order = entry
                engine._fill_buy(
                    account, entry_tick, entry, 1_000.0,
                    entry_tick.market_ts_ms * 1_000_000,
                    kind="deep_discount_sweep", target_price=135.999,
                    persist=True, reason="active_deep_discount",
                )
                lot = next(
                    lot for lot in account.lots.values()
                    if lot.kind == "deep_discount_sweep"
                )
                passive_exit = engine._new_order(
                    account, entry_tick, side="sell", kind="inventory_exit",
                    lot_id=lot.db_id, price=135.998, quantity=1_000.0,
                    queue_ahead=0.0, target_price=135.999, persist=True,
                )
                account.sell_orders[lot.db_id] = passive_exit

                isolated_offer = replace(
                    self._replay_tick(
                        moment + timedelta(minutes=1, seconds=57),
                        last=135.355, bid=135.701, ask=135.711,
                        bid_bonds=1_000.0, ask_bonds=40.0,
                    ),
                    bids=(
                        (135.701, 1_000.0),
                        (135.700, 2_000.0),
                        (135.601, 1_000.0),
                    ),
                    asks=(
                        (135.711, 40.0),
                        (135.999, 1_000.0),
                        (136.000, 1_000.0),
                    ),
                )
                engine._active_profitable_turnover_exit(
                    account, isolated_offer, persist=True,
                    received_ts_ns=(
                        isolated_offer.market_ts_ms * 1_000_000
                    ),
                )
                assessment = replace(
                    self._sweep_recovery_assessment(),
                    reference_price=135.900,
                    reference_low=135.700,
                    reference_high=136.000,
                    reference_source="intraday_trade_anchor",
                    state="possible_fall",
                    largest_ask_gap=0.288,
                )
                context = MakerDecisionContext(
                    reference_price=135.900,
                    reference_source="intraday_trade_anchor",
                    reliable_anchor=True,
                    spread=0.010,
                    bid_support_bonds=3_000.0,
                    ask_supply_bonds=2_040.0,
                    wall_threshold_bonds=5_000.0,
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, isolated_offer, assessment, persist=True,
                    )

                self.assertEqual(account.inventory, 2_000.0)
                self.assertIs(
                    account.sell_orders.get(lot.db_id), passive_exit,
                )
                self.assertEqual(
                    store.connection.execute(
                        "SELECT COUNT(*) FROM maker_paper_fills "
                        "WHERE fill_reason='active_tight_spread_turnover'"
                    ).fetchone()[0],
                    0,
                )

                two_level_low_offer = replace(
                    isolated_offer,
                    market_ts_ms=isolated_offer.market_ts_ms + 3_000,
                    market_time="14:07:01.000",
                    asks=(
                        (135.710, 1_000.0),
                        (135.711, 40.0),
                        (135.999, 1_000.0),
                    ),
                )
                engine._active_profitable_turnover_exit(
                    account, two_level_low_offer, persist=True,
                    received_ts_ns=(
                        two_level_low_offer.market_ts_ms * 1_000_000
                    ),
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, two_level_low_offer, assessment, persist=True,
                    )
                self.assertEqual(account.inventory, 2_000.0)
                self.assertIs(
                    account.sell_orders.get(lot.db_id), passive_exit,
                )

                aggressive_buy = replace(
                    self._replay_tick(
                        moment + timedelta(minutes=4, seconds=54),
                        last=135.999, bid=135.712, ask=135.999,
                        trade_bonds=1_000.0, inferred_side="buy",
                    ),
                    bids=((135.712, 1_000.0), (135.710, 2_000.0)),
                    asks=((135.999, 1_000.0), (136.000, 1_000.0)),
                )
                engine._process_resting_orders(
                    account, aggressive_buy, persist=True,
                    received_ts_ns=aggressive_buy.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_000.0)
                fill = store.connection.execute(
                    "SELECT price,quantity,fill_reason FROM maker_paper_fills "
                    "WHERE side='sell' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                self.assertEqual(float(fill["price"]), 135.998)
                self.assertEqual(float(fill["quantity"]), 1_000.0)
                self.assertEqual(fill["fill_reason"], "passive_sell")
            finally:
                store.close()

    def test_v149_r2_does_not_disable_real_tight_market_turnover(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v149-r2-tight-control.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=(
                        PRIORITY_POLICY_FIRST_POSITION_V149_R2_CANDIDATE
                    ),
                )
                moment = datetime(2026, 8, 25, 14, 5, 1, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                entry_tick = self._replay_tick(
                    moment, last=135.999, bid=135.331, ask=135.354,
                )
                entry = engine._new_order(
                    account, entry_tick, side="buy", kind="deep_discount_sweep",
                    lot_id=None, price=135.354, quantity=1_000.0,
                    queue_ahead=0.0, target_price=135.999, persist=True,
                )
                account.buy_order = entry
                engine._fill_buy(
                    account, entry_tick, entry, 1_000.0,
                    entry_tick.market_ts_ms * 1_000_000,
                    kind="deep_discount_sweep", target_price=135.999,
                    persist=True, reason="active_deep_discount",
                )
                lot = next(
                    lot for lot in account.lots.values()
                    if lot.kind == "deep_discount_sweep"
                )
                passive_exit = engine._new_order(
                    account, entry_tick, side="sell", kind="inventory_exit",
                    lot_id=lot.db_id, price=135.720, quantity=1_000.0,
                    queue_ahead=0.0, target_price=135.720, persist=True,
                )
                account.sell_orders[lot.db_id] = passive_exit
                ordinary_tight_book = replace(
                    self._replay_tick(
                        moment + timedelta(minutes=1),
                        last=135.711, bid=135.701, ask=135.711,
                    ),
                    bids=((135.701, 1_000.0), (135.700, 2_000.0)),
                    asks=((135.711, 1_000.0), (135.720, 1_000.0)),
                )
                engine._active_profitable_turnover_exit(
                    account, ordinary_tight_book, persist=True,
                    received_ts_ns=(
                        ordinary_tight_book.market_ts_ms * 1_000_000
                    ),
                )
                self.assertEqual(account.inventory, 1_000.0)
                fill = store.connection.execute(
                    "SELECT price,fill_reason FROM maker_paper_fills "
                    "WHERE side='sell' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                self.assertEqual(float(fill["price"]), 135.701)
                self.assertEqual(
                    fill["fill_reason"], "active_tight_spread_turnover",
                )
            finally:
                store.close()

    def test_original_v149_keeps_its_registered_tight_turnover_path(self) -> None:
        self.assertFalse(
            PRIORITY_POLICY_FIRST_POSITION_V149_CANDIDATE
            .enable_isolated_low_offer_active_turnover_hold
        )
        self.assertTrue(
            PRIORITY_POLICY_FIRST_POSITION_V149_R2_CANDIDATE
            .enable_isolated_low_offer_active_turnover_hold
        )

    def test_first_position_v21_lifts_the_fair_region_with_live_price_discovery(
        self,
    ) -> None:
        moment = datetime(2026, 8, 26, 10, 1, 38, tzinfo=SHANGHAI)
        tick = replace(
            self._replay_tick(
                moment, last=138.105, bid=138.105, ask=138.299,
                bid_bonds=18_000.0, ask_bonds=3_000.0,
            ),
            bids=((138.105, 18_000.0), (138.100, 15_000.0)),
            asks=((138.299, 3_000.0), (138.300, 2_000.0)),
        )
        stale = MarketAssessment(
            reference_price=137.628,
            reference_low=137.498,
            reference_high=137.993,
            reference_source="intraday_trade_anchor",
            reference_confidence=0.72,
            state="rising",
            state_score=4,
            state_confidence=0.90,
            recent_buy_bonds=21_360.0,
            recent_sell_bonds=0.0,
            midpoint_change=0.204,
            short_ask_change=0.0,
            largest_ask_gap=0.0,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        lifted = trend_price_discovery_assessment(
            stale, tick, MakerParameters(),
        )
        self.assertEqual(lifted.reference_source, "trend_price_discovery")
        self.assertEqual(lifted.reference_low, 138.105)
        self.assertEqual(lifted.reference_high, 138.299)
        self.assertEqual(lifted.reference_price, 138.202)

        isolated = replace(
            tick,
            bids=((138.105, 500.0), (137.800, 20_000.0)),
        )
        self.assertIs(
            trend_price_discovery_assessment(
                stale, isolated, MakerParameters(),
            ),
            stale,
        )

        early = replace(
            tick,
            bids=((137.601, 2_000.0), (137.600, 2_000.0), (137.550, 5_000.0)),
            asks=((137.988, 2_000.0), (137.989, 3_000.0)),
        )
        possible = replace(
            stale,
            state="possible_rise",
            state_score=2,
            recent_buy_bonds=4_000.0,
            midpoint_change=0.025,
        )
        self.assertIs(
            trend_price_discovery_assessment(
                possible, early, MakerParameters(),
            ),
            possible,
        )
        stock_accelerated = trend_price_discovery_assessment(
            possible, early, MakerParameters(), stock_extremely_strong=True,
        )
        self.assertEqual(
            stock_accelerated.reference_source, "trend_price_discovery",
        )
        self.assertEqual(stock_accelerated.reference_low, 137.601)
        self.assertEqual(stock_accelerated.reference_high, 137.988)

    def test_first_position_v150_keeps_base_recovery_at_live_priority(
        self,
    ) -> None:
        self.assertEqual(
            PRIORITY_POLICY_FIRST_POSITION_V150_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_FIRST_POSITION_V149_R2_CANDIDATE.model_id,
        )
        moment = datetime(2026, 8, 26, 13, 22, 3, tzinfo=SHANGHAI)
        tick = replace(
            self._replay_tick(
                moment, last=136.799, bid=136.515, ask=136.799,
                bid_bonds=1_000.0, ask_bonds=3_000.0,
            ),
            bids=(
                (136.515, 1_000.0),
                (136.514, 1_000.0),
                (136.513, 1_000.0),
                (136.500, 11_000.0),
            ),
            asks=((136.799, 3_000.0), (136.800, 10_000.0)),
        )
        assessment = MarketAssessment(
            reference_price=136.498,
            reference_low=136.300,
            reference_high=136.513,
            reference_source="intraday_trade_anchor",
            reference_confidence=0.80,
            state="possible_rise",
            state_score=1,
            state_confidence=0.50,
            recent_buy_bonds=8_000.0,
            recent_sell_bonds=4_000.0,
            midpoint_change=0.01,
            short_ask_change=0.0,
            largest_ask_gap=0.0,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=136.498,
            reference_source="intraday_trade_anchor",
            reliable_anchor=True,
            spread=0.284,
            bid_support_bonds=14_000.0,
            ask_supply_bonds=13_000.0,
            wall_threshold_bonds=5_000.0,
        )

        observed = {}
        for name, policy in (
            ("parent", PRIORITY_POLICY_FIRST_POSITION_V149_R2_CANDIDATE),
            ("candidate", PRIORITY_POLICY_FIRST_POSITION_V150_CANDIDATE),
        ):
            with tempfile.TemporaryDirectory() as temp:
                config = test_config(Path(temp) / f"v150-{name}.sqlite3")
                store = SQLiteStore(config)
                try:
                    engine = MakerPaperEngine(
                        config, store, priority_policy=policy,
                    )
                    engine._start_date(moment.date().isoformat())
                    account = engine.accounts["maker_v01_priority"]
                    account.inventory = 0.0
                    account.lots.clear()
                    account.replenishment_quantity = 1_000.0
                    account.replenishment_sale_value = 136.798 * 1_000.0
                    with patch.object(
                        engine, "_decision_context", return_value=context,
                    ):
                        engine._refresh_orders(
                            account, tick, assessment, persist=True,
                        )
                    self.assertIsNotNone(account.buy_order)
                    observed[name] = (
                        account.buy_order.limit_price,
                        account.buy_order.price_boundary,
                        account.buy_order.price_boundary_kind,
                    )
                finally:
                    store.close()

        self.assertLess(observed["parent"][0], tick.bid1)
        self.assertEqual(observed["parent"][0], observed["parent"][1])
        self.assertEqual(observed["parent"][2], "buy_ceiling")
        self.assertEqual(
            observed["candidate"],
            (136.516, 136.516, "live_priority_price"),
        )

    def test_first_position_v150_does_not_floor_a_live_bid_back_one_tick(
        self,
    ) -> None:
        moment = datetime(2026, 8, 7, 9, 55, 29, tzinfo=SHANGHAI)
        tick = replace(
            self._replay_tick(
                moment, last=138.800, bid=138.79899999999998,
                ask=138.800, bid_bonds=3_000.0, ask_bonds=3_000.0,
            ),
            bids=(
                (138.79899999999998, 3_000.0),
                (138.798, 3_000.0),
            ),
            asks=((138.800, 3_000.0), (138.900, 3_000.0)),
        )
        assessment = MarketAssessment(
            reference_price=138.500,
            reference_low=138.300,
            reference_high=138.600,
            reference_source="intraday_trade_anchor",
            reference_confidence=0.80,
            state="stable",
            state_score=0,
            state_confidence=0.50,
            recent_buy_bonds=0.0,
            recent_sell_bonds=0.0,
            midpoint_change=0.0,
            short_ask_change=0.0,
            largest_ask_gap=0.0,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=138.500,
            reference_source="intraday_trade_anchor",
            reliable_anchor=True,
            spread=0.001,
            bid_support_bonds=6_000.0,
            ask_supply_bonds=6_000.0,
            wall_threshold_bonds=5_000.0,
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v150-live-bid-tick.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_FIRST_POSITION_V150_CANDIDATE,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.inventory = 0.0
                account.lots.clear()
                account.replenishment_quantity = 1_000.0
                account.replenishment_sale_value = 138.800 * 1_000.0
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )
                self.assertIsNotNone(account.buy_order)
                self.assertEqual(account.buy_order.limit_price, 138.799)
                self.assertEqual(
                    account.buy_order.price_boundary_kind,
                    "live_priority_price",
                )
            finally:
                store.close()

    def test_first_position_v25_and_later_revisions_keep_base_short_recovery_live(
        self,
    ) -> None:
        """The 2026-08-31 14:09 gap must not leave a deep off-book order."""

        self.assertEqual(
            PRIORITY_POLICY_FIRST_POSITION_V25_R2_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE.model_id,
        )
        self.assertEqual(
            PRIORITY_POLICY_FIRST_POSITION_V251_R3_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_FIRST_POSITION_V251_R2_CANDIDATE.model_id,
        )
        self.assertEqual(
            PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_FIRST_POSITION_V252_CANDIDATE.model_id,
        )
        for legacy in (
            PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE,
            PRIORITY_POLICY_FIRST_POSITION_V251_R2_CANDIDATE,
            PRIORITY_POLICY_FIRST_POSITION_V252_CANDIDATE,
        ):
            self.assertFalse(
                legacy.enable_live_priority_base_replenishment_exposure,
            )

        moment = datetime(2026, 8, 31, 14, 9, 54, tzinfo=SHANGHAI)
        tick = replace(
            self._replay_tick(
                moment, last=134.801, bid=134.801, ask=135.199,
                bid_bonds=2_500.0, ask_bonds=1_000.0,
            ),
            bids=(
                (134.801, 2_500.0),
                (134.800, 10_000.0),
                (134.666, 1_000.0),
                (134.600, 4_000.0),
                (134.500, 9_000.0),
            ),
            asks=(
                (135.199, 1_000.0),
                (135.200, 1_000.0),
                (135.699, 6_000.0),
                (135.700, 300.0),
                (135.999, 23_000.0),
            ),
        )
        assessment = MarketAssessment(
            reference_price=135.000,
            reference_low=134.801,
            reference_high=135.199,
            reference_source="carried_intraday_reference",
            reference_confidence=0.40,
            state="possible_fall",
            state_score=-1,
            state_confidence=0.50,
            recent_buy_bonds=1_000.0,
            recent_sell_bonds=4_200.0,
            midpoint_change=-0.10,
            short_ask_change=0.0,
            largest_ask_gap=0.499,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=135.000,
            reference_source="carried_intraday_reference",
            reliable_anchor=False,
            spread=0.398,
            bid_support_bonds=26_500.0,
            ask_supply_bonds=31_300.0,
            wall_threshold_bonds=5_000.0,
        )

        for policy in (
            PRIORITY_POLICY_FIRST_POSITION_V25_R2_CANDIDATE,
            PRIORITY_POLICY_FIRST_POSITION_V251_R3_CANDIDATE,
            PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE,
        ):
            with self.subTest(model_id=policy.model_id):
                with tempfile.TemporaryDirectory() as temp:
                    config = test_config(Path(temp) / "live-recovery.sqlite3")
                    store = SQLiteStore(config)
                    try:
                        engine = MakerPaperEngine(
                            config, store, priority_policy=policy,
                        )
                        engine._start_date(moment.date().isoformat())
                        account = engine.accounts["maker_v01_priority"]
                        account.inventory = 0.0
                        account.lots.clear()
                        account.replenishment_quantity = 1_000.0
                        account.replenishment_sale_value = 133.999 * 1_000.0
                        with patch.object(
                            engine, "_decision_context", return_value=context,
                        ):
                            engine._refresh_orders(
                                account, tick, assessment, persist=True,
                            )
                        self.assertIsNotNone(account.buy_order)
                        self.assertEqual(account.buy_order.limit_price, 134.802)
                        self.assertEqual(
                            account.buy_order.price_boundary_kind,
                            "live_priority_price",
                        )
                        self.assertEqual(account.buy_order.quantity, 1_000.0)
                    finally:
                        store.close()

    def test_first_position_v150_preserves_isolated_top_bid_guard(
        self,
    ) -> None:
        self.assertTrue(
            PRIORITY_POLICY_FIRST_POSITION_V150_CANDIDATE
                .enable_isolated_top_bid_base_replenishment_guard
        )
        self.assertTrue(
            PRIORITY_POLICY_FIRST_POSITION_V150_CANDIDATE
                .enable_isolated_low_offer_active_turnover_hold
        )
        self.assertFalse(
            PRIORITY_POLICY_FIRST_POSITION_V150_CANDIDATE
                .enable_trend_price_discovery_base_replenishment
        )

    def test_first_position_v21_actively_restores_base_at_the_escape_wall(
        self,
    ) -> None:
        self.assertEqual(
            PRIORITY_POLICY_FIRST_POSITION_V21_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_FIRST_POSITION_V149_R2_CANDIDATE.model_id,
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v21-trend-escape.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_FIRST_POSITION_V21_CANDIDATE,
                )
                moment = datetime(2026, 8, 26, 9, 58, 23, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.inventory = 0.0
                account.lots.clear()
                account.replenishment_quantity = 1_000.0
                account.replenishment_sale_value = 137.988 * 1_000.0
                account.last_base_short_sale_ts_ms = (
                    int(moment.timestamp() * 1_000) - 159_000
                )
                tick = replace(
                    self._replay_tick(
                        moment, last=138.000, bid=138.000, ask=138.100,
                        bid_bonds=10_000.0, ask_bonds=3_000.0,
                    ),
                    bids=((138.000, 10_000.0), (137.990, 2_000.0)),
                    asks=((138.100, 3_000.0), (138.200, 2_000.0)),
                )
                assessment = MarketAssessment(
                    reference_price=138.050,
                    reference_low=138.000,
                    reference_high=138.100,
                    reference_source="trend_price_discovery",
                    reference_confidence=0.90,
                    state="rising",
                    state_score=4,
                    state_confidence=0.90,
                    recent_buy_bonds=21_360.0,
                    recent_sell_bonds=0.0,
                    midpoint_change=0.204,
                    short_ask_change=0.0,
                    largest_ask_gap=0.0,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=(),
                )
                self.assertTrue(
                    engine._active_trend_base_short_replenishment(
                        account, tick, assessment, persist=True,
                        received_ts_ns=tick.market_ts_ms * 1_000_000,
                    )
                )
                self.assertEqual(account.inventory, 1_000.0)
                fill = store.connection.execute(
                    "SELECT price,quantity,fill_reason FROM maker_paper_fills "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
                self.assertEqual(float(fill["price"]), 138.100)
                self.assertEqual(float(fill["quantity"]), 1_000.0)
                self.assertEqual(
                    fill["fill_reason"],
                    "active_bond_confirmed_trend_base_replenishment",
                )
            finally:
                store.close()

    def test_first_position_v21_passive_recovery_follows_a_reliable_rising_bid(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v21-passive-follow.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_FIRST_POSITION_V21_CANDIDATE,
                )
                moment = datetime(2026, 8, 26, 9, 56, 0, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.inventory = 0.0
                account.lots.clear()
                account.replenishment_quantity = 1_000.0
                account.replenishment_sale_value = 137.988 * 1_000.0
                account.last_base_short_sale_ts_ms = (
                    int(moment.timestamp() * 1_000) - 16_000
                )
                tick = replace(
                    self._replay_tick(
                        moment, last=138.105, bid=138.105, ask=138.299,
                        bid_bonds=18_000.0, ask_bonds=3_000.0,
                    ),
                    bids=((138.105, 18_000.0), (138.100, 15_000.0)),
                    asks=((138.299, 3_000.0), (138.300, 2_000.0)),
                )
                assessment = MarketAssessment(
                    reference_price=138.202,
                    reference_low=138.105,
                    reference_high=138.299,
                    reference_source="trend_price_discovery",
                    reference_confidence=0.80,
                    state="rising",
                    state_score=3,
                    state_confidence=0.85,
                    recent_buy_bonds=13_000.0,
                    recent_sell_bonds=0.0,
                    midpoint_change=0.15,
                    short_ask_change=0.0,
                    largest_ask_gap=0.0,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=(),
                )
                stale_context = MakerDecisionContext(
                    reference_price=137.628,
                    reference_source="intraday_trade_anchor",
                    reliable_anchor=True,
                    spread=0.388,
                    bid_support_bonds=14_000.0,
                    ask_supply_bonds=12_000.0,
                    wall_threshold_bonds=5_000.0,
                )
                with patch.object(
                    engine, "_decision_context", return_value=stale_context,
                ):
                    engine._refresh_orders(
                        account, tick, assessment, persist=True,
                    )
                self.assertIsNotNone(account.buy_order)
                self.assertEqual(
                    account.buy_order.kind,
                    "dynamic_customer_base_replenish",
                )
                self.assertEqual(account.buy_order.limit_price, 138.106)
                self.assertGreater(
                    account.buy_order.limit_price, stale_context.reference_price,
                )
            finally:
                store.close()

    def test_first_position_v21_stock_strength_requires_a_bond_wall_attack(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v21-stock-accelerator.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_FIRST_POSITION_V21_CANDIDATE,
                )
                moment = datetime(2026, 8, 26, 9, 58, 17, tzinfo=SHANGHAI)
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.inventory = 0.0
                account.lots.clear()
                account.replenishment_quantity = 1_000.0
                account.replenishment_sale_value = 137.988 * 1_000.0
                account.last_base_short_sale_ts_ms = (
                    int(moment.timestamp() * 1_000) - 153_000
                )
                engine.analyzer.stock_previous_close = 44.15
                engine.analyzer.stock_latest_price = 48.57
                assessment = MarketAssessment(
                    reference_price=137.995,
                    reference_low=137.990,
                    reference_high=138.000,
                    reference_source="trend_price_discovery",
                    reference_confidence=0.90,
                    state="possible_rise",
                    state_score=2,
                    state_confidence=0.80,
                    recent_buy_bonds=13_000.0,
                    recent_sell_bonds=0.0,
                    midpoint_change=0.15,
                    short_ask_change=0.0,
                    largest_ask_gap=0.0,
                    downside_book_vacuum=False,
                    fragile_top_bid=False,
                    iron_floor_price=None,
                    iron_floor_bonds=0.0,
                    evidence=(),
                )
                no_attack = replace(
                    self._replay_tick(
                        moment, last=138.000, bid=137.990, ask=138.000,
                        bid_bonds=2_000.0, ask_bonds=6_363.0,
                    ),
                    bids=((137.990, 2_000.0), (137.989, 8_000.0)),
                )
                self.assertFalse(
                    engine._active_trend_base_short_replenishment(
                        account, no_attack, assessment, persist=True,
                        received_ts_ns=no_attack.market_ts_ms * 1_000_000,
                    )
                )
                attack = replace(
                    no_attack, trade_bonds=8_000.0, inferred_side="buy",
                )
                self.assertTrue(
                    engine._active_trend_base_short_replenishment(
                        account, attack, assessment, persist=True,
                        received_ts_ns=attack.market_ts_ms * 1_000_000,
                    )
                )
                fill = store.connection.execute(
                    "SELECT fill_reason FROM maker_paper_fills "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
                self.assertEqual(
                    fill["fill_reason"],
                    "active_stock_accelerated_trend_base_replenishment",
                )
            finally:
                store.close()

    def test_first_position_v23_carries_morning_reference_over_lunch(
        self,
    ) -> None:
        self.assertEqual(
            PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_FIRST_POSITION_V22_CANDIDATE.model_id,
        )
        self.assertFalse(
            PRIORITY_POLICY_FIRST_POSITION_V22_CANDIDATE
                .enable_midday_intraday_reference_continuity,
        )
        self.assertTrue(
            PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE
                .enable_midday_intraday_reference_continuity,
        )
        moment = datetime(2026, 8, 27, 13, 0, 2, tzinfo=SHANGHAI)
        tick = replace(
            self._replay_tick(
                moment,
                last=137.596,
                bid=137.596,
                ask=137.649,
                previous_close=137.874,
                bid_bonds=1_000.0,
                ask_bonds=200.0,
            ),
            code="132024.SH",
            bids=(
                (137.596, 1_000.0),
                (137.590, 7_000.0),
                (137.550, 7_000.0),
                (137.500, 7_000.0),
                (137.450, 7_000.0),
            ),
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v23-midday-carry.sqlite3")
            store = SQLiteStore(config)
            try:
                parent = MakerPaperEngine(
                    config,
                    store,
                    bond_code="132024.SH",
                    priority_policy=(
                        PRIORITY_POLICY_FIRST_POSITION_V22_CANDIDATE
                    ),
                )
                parent._start_date(moment.date().isoformat())
                parent.previous_close_reference = 137.874
                parent.last_intraday_working_reference = 137.623
                parent.last_intraday_working_reference_ts_ms = (
                    tick.market_ts_ms - 5_403_000
                )
                parent_assessment = parent.analyzer.assess_market(
                    tick, 137.874,
                )
                parent_context = parent._decision_context(
                    tick, PRIORITY_POLICY_FIRST_POSITION_V22_CANDIDATE,
                )
                parent_account = parent.accounts[
                    "maker_132024_v01_priority"
                ]
                self.assertEqual(
                    parent_assessment.reference_source, "previous_close",
                )
                self.assertEqual(
                    parent_context.reference_source, "previous_close",
                )
                self.assertAlmostEqual(parent_context.reference_price, 137.874)
                parent._refresh_orders(
                    parent_account, tick, parent_assessment, persist=False,
                )
                self.assertIsNotNone(parent_account.buy_order)
                self.assertAlmostEqual(
                    parent_account.buy_order.limit_price, 137.597,
                )
                self.assertEqual(
                    parent_account.buy_order.quantity, 1_000.0,
                )

                child = MakerPaperEngine(
                    config,
                    store,
                    bond_code="132024.SH",
                    priority_policy=(
                        PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE
                    ),
                )
                child._start_date(moment.date().isoformat())
                child.previous_close_reference = 137.874
                child.last_intraday_working_reference = 137.700
                child.last_intraday_working_reference_ts_ms = (
                    tick.market_ts_ms - 5_403_000
                )
                child_account = child.accounts["maker_132024_v01_priority"]
                morning = replace(
                    tick,
                    market_ts_ms=tick.market_ts_ms - 5_403_000,
                    market_time="11:29:59.000",
                    bids=((137.600, 1_000.0),),
                    asks=((137.646, 1_000.0),),
                )
                morning_assessment = replace(
                    child.analyzer.assess_market(morning, 137.874),
                    reference_price=137.623,
                    reference_low=137.600,
                    reference_high=137.646,
                    reference_source="trend_price_discovery",
                    reference_confidence=0.75,
                )
                child._assessment_for_account(
                    child_account, morning, morning_assessment,
                )
                child_assessment = child._assessment_for_account(
                    child_account,
                    tick,
                    child.analyzer.assess_market(tick, 137.874),
                )
                child_context = child._decision_context(
                    tick, PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE,
                )
                self.assertEqual(
                    child_assessment.reference_source,
                    "midday_carried_intraday_reference",
                )
                self.assertEqual(
                    child_context.reference_source,
                    "midday_carried_intraday_reference",
                )
                self.assertAlmostEqual(child_context.reference_price, 137.623)
                self.assertEqual(child_assessment.recent_buy_bonds, 0.0)
                self.assertEqual(child_assessment.recent_sell_bonds, 0.0)
                child._refresh_orders(
                    child_account,
                    tick,
                    child.analyzer.assess_market(tick, 137.874),
                    persist=False,
                )
                self.assertIsNone(child_account.buy_order)
            finally:
                store.close()

    def test_first_position_v23_resets_midpoint_after_afternoon_gap(
        self,
    ) -> None:
        moment = datetime(2026, 8, 27, 13, 0, 2, tzinfo=SHANGHAI)
        tick = self._replay_tick(
            moment,
            last=135.000,
            bid=135.000,
            ask=135.050,
            previous_close=137.874,
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v23-midday-gap.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config,
                    store,
                    priority_policy=(
                        PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE
                    ),
                )
                engine._start_date(moment.date().isoformat())
                engine.previous_close_reference = 137.874
                engine.last_intraday_working_reference = 137.623
                context = engine._decision_context(
                    tick, PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE,
                )
                self.assertEqual(
                    context.reference_source,
                    "midday_current_midpoint_reset",
                )
                self.assertAlmostEqual(context.reference_price, 135.025)
                repeated = engine._decision_context(
                    tick, PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE,
                )
                self.assertEqual(
                    repeated.reference_source,
                    "midday_current_midpoint_reset",
                )

                fresh = replace(
                    tick,
                    market_ts_ms=tick.market_ts_ms + 60_000,
                    market_time="13:01:02.000",
                    last_price=135.080,
                    bids=((135.070, 1_000.0),),
                    asks=((135.090, 1_000.0),),
                )
                self.assertIsNone(
                    engine._midday_continuity_reference(
                        PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE,
                        fresh,
                        135.080,
                        "intraday_trade_anchor",
                    ),
                )
                later = replace(
                    fresh,
                    market_ts_ms=fresh.market_ts_ms + 600_000,
                    market_time="13:11:02.000",
                )
                carried = engine._midday_continuity_reference(
                    PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE,
                    later,
                    137.874,
                    "previous_close",
                )
                self.assertEqual(
                    carried,
                    (135.080, "midday_carried_intraday_reference"),
                )
            finally:
                store.close()

    def test_first_position_v23_without_morning_reference_keeps_parent_path(
        self,
    ) -> None:
        moment = datetime(2026, 8, 27, 13, 0, 2, tzinfo=SHANGHAI)
        tick = self._replay_tick(
            moment,
            last=137.596,
            bid=137.596,
            ask=137.649,
            previous_close=137.874,
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v23-no-morning.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config,
                    store,
                    priority_policy=(
                        PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE
                    ),
                )
                engine._start_date(moment.date().isoformat())
                engine.previous_close_reference = 137.874
                context = engine._decision_context(
                    tick, PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE,
                )
                self.assertEqual(context.reference_source, "previous_close")
                self.assertAlmostEqual(context.reference_price, 137.874)
            finally:
                store.close()

    def test_first_position_v24_keeps_intraday_reference_after_morning_expiry(
        self,
    ) -> None:
        self.assertEqual(
            PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE.model_id,
        )
        self.assertFalse(
            PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE
                .enable_intraday_reference_continuity,
        )
        self.assertTrue(
            PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE
                .enable_intraday_reference_continuity,
        )
        moment = datetime(2026, 8, 27, 10, 25, 26, tzinfo=SHANGHAI)
        tick = replace(
            self._replay_tick(
                moment,
                last=135.600,
                bid=135.502,
                ask=135.600,
                previous_close=136.559,
                bid_bonds=200.0,
                ask_bonds=300.0,
            ),
            bids=(
                (135.502, 200.0),
                (135.501, 1_000.0),
                (135.002, 400.0),
                (135.001, 1_000.0),
            ),
            asks=((135.600, 300.0),),
        )
        earlier = replace(
            tick,
            market_ts_ms=tick.market_ts_ms - 1_801_000,
            market_time="09:55:25.000",
            bids=((135.502, 1_000.0),),
            asks=((135.600, 1_000.0),),
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v24-all-day-continuity.sqlite3")
            store = SQLiteStore(config)
            try:
                parent = MakerPaperEngine(
                    config,
                    store,
                    priority_policy=(
                        PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE
                    ),
                )
                parent._start_date(moment.date().isoformat())
                parent.previous_close_reference = 136.559
                parent_account = parent.accounts["maker_v01_priority"]
                parent._assessment_for_account(
                    parent_account,
                    earlier,
                    replace(
                        parent.analyzer.assess_market(earlier, 136.559),
                        reference_price=135.551,
                        reference_low=135.502,
                        reference_high=135.600,
                        reference_source="current_midpoint",
                        reference_confidence=0.35,
                    ),
                )
                parent_context = parent._decision_context(
                    tick, PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE,
                )
                self.assertEqual(parent_context.reference_source, "previous_close")
                self.assertAlmostEqual(parent_context.reference_price, 136.559)

                child = MakerPaperEngine(
                    config,
                    store,
                    priority_policy=(
                        PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE
                    ),
                )
                child._start_date(moment.date().isoformat())
                child.previous_close_reference = 136.559
                child_account = child.accounts["maker_v01_priority"]
                child._assessment_for_account(
                    child_account,
                    earlier,
                    replace(
                        child.analyzer.assess_market(earlier, 136.559),
                        reference_price=135.551,
                        reference_low=135.502,
                        reference_high=135.600,
                        reference_source="current_midpoint",
                        reference_confidence=0.35,
                    ),
                )
                expired = child.analyzer.assess_market(tick, 136.559)
                child_assessment = child._assessment_for_account(
                    child_account, tick, expired,
                )
                child_context = child._decision_context(
                    tick, PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE,
                )
                self.assertEqual(expired.reference_source, "previous_close")
                self.assertEqual(
                    child_assessment.reference_source,
                    "carried_intraday_reference",
                )
                self.assertEqual(
                    child_context.reference_source,
                    "carried_intraday_reference",
                )
                self.assertAlmostEqual(child_context.reference_price, 135.551)
                self.assertEqual(child_assessment.recent_buy_bonds, 0.0)
                self.assertEqual(child_assessment.recent_sell_bonds, 0.0)
                child._refresh_orders(
                    child_account, tick, expired, persist=False,
                )
                self.assertIsNone(child_account.buy_order)
            finally:
                store.close()

    def test_first_position_v24_resets_from_live_midpoint_all_day(
        self,
    ) -> None:
        moment = datetime(2026, 8, 27, 10, 25, 26, tzinfo=SHANGHAI)
        tick = self._replay_tick(
            moment,
            last=135.000,
            bid=135.000,
            ask=135.050,
            previous_close=137.874,
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v24-intraday-gap.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config,
                    store,
                    priority_policy=(
                        PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE
                    ),
                )
                engine._start_date(moment.date().isoformat())
                engine.previous_close_reference = 137.874
                engine.intraday_working_references_by_model[
                    PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE.model_id
                ] = 137.623
                context = engine._decision_context(
                    tick, PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE,
                )
                self.assertEqual(
                    context.reference_source,
                    "intraday_current_midpoint_reset",
                )
                self.assertAlmostEqual(context.reference_price, 135.025)
            finally:
                store.close()

    def test_first_position_v24_allows_close_only_before_intraday_discovery(
        self,
    ) -> None:
        moment = datetime(2026, 8, 27, 9, 25, 1, tzinfo=SHANGHAI)
        tick = self._replay_tick(
            moment,
            last=137.596,
            bid=137.596,
            ask=137.649,
            previous_close=137.874,
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v24-opening-close.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config,
                    store,
                    priority_policy=(
                        PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE
                    ),
                )
                engine._start_date(moment.date().isoformat())
                engine.previous_close_reference = 137.874
                context = engine._decision_context(
                    tick, PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE,
                )
                self.assertEqual(context.reference_source, "previous_close")
                self.assertAlmostEqual(context.reference_price, 137.874)
            finally:
                store.close()

    def test_first_position_v25_low_carried_reference_vetoes_isolated_buy(
        self,
    ) -> None:
        self.assertEqual(
            PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE.model_id,
        )
        self.assertFalse(
            PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE
                .veto_isolated_discount_with_low_carried_reference,
        )
        self.assertTrue(
            PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE
                .veto_isolated_discount_with_low_carried_reference,
        )
        moment = datetime(2026, 8, 13, 9, 45, 42, tzinfo=SHANGHAI)
        tick = replace(
            self._replay_tick(
                moment, last=137.300, bid=135.101, ask=136.500,
                bid_bonds=2_000.0, ask_bonds=1_000.0,
            ),
            bids=(
                (135.101, 2_000.0), (135.100, 1_000.0),
                (133.002, 8_000.0), (133.001, 4_000.0),
                (128.260, 1_000.0),
            ),
            asks=(
                (136.500, 1_000.0), (137.200, 1_000.0),
                (137.290, 3_000.0), (137.298, 3_000.0),
                (137.750, 3_000.0),
            ),
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v25-low-carried-veto.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE,
                )
                engine._start_date(moment.date().isoformat())
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    tick.market_ts_ms - 7 * 60 * 1_000,
                    137.300, 1_000.0, 1, "sell",
                ))
                account = engine.accounts["maker_v01_priority"]
                account.last_asks = (
                    (136.999, 1_000.0), (137.200, 1_000.0),
                    (137.290, 3_000.0), (137.298, 3_000.0),
                    (137.750, 3_000.0),
                )

                parent = engine._isolated_deep_discount_decision(
                    account, tick, 136.15475,
                    "carried_intraday_reference",
                    PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE,
                )
                child = engine._isolated_deep_discount_decision(
                    account, tick, 136.15475,
                    "carried_intraday_reference",
                    PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE,
                )
                self.assertEqual(parent, (0.50, 137.200))
                self.assertIsNone(child)

                # A current midpoint contains the suspect ask and is not an
                # independent low-price veto; genuine isolated errors retain
                # the pre-cluster trade/normal-ask permission.
                current_midpoint = engine._isolated_deep_discount_decision(
                    account, tick, 136.15475, "current_midpoint",
                    PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE,
                )
                self.assertEqual(current_midpoint, (0.50, 137.200))

                high_carried = engine._isolated_deep_discount_decision(
                    account, tick, 137.100,
                    "carried_intraday_reference",
                    PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE,
                )
                self.assertEqual(high_carried, (0.50, 137.100))
            finally:
                store.close()

    def test_first_position_v251_caps_jiangxi_copper_opening_book_reference(
        self,
    ) -> None:
        self.assertEqual(
            PRIORITY_POLICY_FIRST_POSITION_V251_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE.model_id,
        )
        self.assertFalse(
            PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE
                .enable_opening_trade_constrained_reference,
        )
        self.assertTrue(
            PRIORITY_POLICY_FIRST_POSITION_V251_CANDIDATE
                .enable_opening_trade_constrained_reference,
        )
        moment = datetime(2026, 8, 28, 9, 36, 13, tzinfo=SHANGHAI)
        tick = replace(
            self._replay_tick(
                moment, last=137.500, bid=137.400, ask=137.799,
                trade_bonds=1_000.0, inferred_side="sell",
                previous_close=137.874, bid_bonds=1_000.0,
                ask_bonds=2_000.0,
            ),
            bids=(
                (137.400, 1_000.0),
                (137.201, 3_000.0),
                (137.200, 2_000.0),
            ),
            asks=((137.799, 2_000.0),),
        )

        def seed_opening_evidence(engine: MakerPaperEngine) -> None:
            now_ms = tick.market_ts_ms
            engine.previous_close_reference = 137.874
            engine.analyzer.trade_evidence.extend((
                TradeEvidence(now_ms - 138_000, 137.000, 1_000.0, 1, "sell"),
                TradeEvidence(now_ms - 75_000, 137.001, 1_000.0, 1, "sell"),
                TradeEvidence(now_ms - 3_000, 137.501, 1_260.0, 1, "sell"),
                TradeEvidence(now_ms, 137.500, 1_000.0, 1, "sell"),
            ))
            # The same causal midpoint range used by 2.5 is stable for more
            # than 15 seconds and has a median of 137.68975.
            engine.analyzer.book_quotes.extend((
                BookQuote(now_ms - 57_000, 137.500, 137.800),
                BookQuote(now_ms - 45_000, 137.500, 137.879),
                BookQuote(now_ms - 30_000, 137.501, 137.879),
                BookQuote(now_ms - 15_000, 137.501, 137.979),
            ))

        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v251-opening-reference.sqlite3")
            store = SQLiteStore(config)
            try:
                parent = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE,
                )
                parent._start_date(moment.date().isoformat())
                seed_opening_evidence(parent)
                parent_context = parent._decision_context(
                    tick, PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE,
                )
                self.assertEqual(
                    parent_context.reference_source,
                    "persistent_inside_market",
                )
                self.assertAlmostEqual(parent_context.reference_price, 137.68975)

                child = MakerPaperEngine(
                    config, store,
                    priority_policy=(
                        PRIORITY_POLICY_FIRST_POSITION_V251_CANDIDATE
                    ),
                )
                child._start_date(moment.date().isoformat())
                seed_opening_evidence(child)
                child_context = child._decision_context(
                    tick, PRIORITY_POLICY_FIRST_POSITION_V251_CANDIDATE,
                )
                self.assertEqual(
                    child_context.reference_source,
                    "persistent_inside_market",
                )
                self.assertAlmostEqual(child_context.reference_price, 137.68975)
                # Active/special entry callers retain the parent reference;
                # only the explicitly marked ordinary passive-extra path is
                # allowed to use the opening trade constraint.
                self.assertAlmostEqual(
                    child._ordinary_extra_entry_reference(
                        child.accounts["maker_v01_priority"],
                        tick,
                        child_context.reference_price,
                        child_context.reference_source,
                    ),
                    137.68975,
                )
                child_entry_reference = (
                    child._ordinary_extra_entry_reference(
                        child.accounts["maker_v01_priority"],
                        tick,
                        child_context.reference_price,
                        child_context.reference_source,
                        apply_opening_trade_constraint=True,
                    )
                )
                self.assertAlmostEqual(child_entry_reference, 137.500)
                child_account = child.accounts["maker_v01_priority"]
                child_account.inventory = 0.0
                self.assertAlmostEqual(
                    child._ordinary_extra_entry_reference(
                        child_account,
                        tick,
                        child_context.reference_price,
                        child_context.reference_source,
                        apply_opening_trade_constraint=True,
                    ),
                    137.68975,
                )
                child_account.inventory = child_account.initial_inventory

                parent_account = parent.accounts["maker_v01_priority"]
                parent_assessment = parent.analyzer.assess_market(tick, 137.874)
                parent._refresh_orders(
                    parent_account, tick, parent_assessment, persist=False,
                )
                self.assertIsNotNone(parent_account.buy_order)
                self.assertAlmostEqual(
                    parent_account.buy_order.limit_price, 137.401,
                )

                child_assessment = child.analyzer.assess_market(tick, 137.874)
                child._refresh_orders(
                    child_account, tick, child_assessment, persist=False,
                )
                self.assertIsNone(child_account.buy_order)

                # The price correction must independently veto the ordinary
                # quote.  It cannot rely on the separate 3,000/5,000 support
                # rule to hide a wide-market bypass of the lower trade cap.
                pricing_only_policy = replace(
                    PRIORITY_POLICY_FIRST_POSITION_V251_CANDIDATE,
                    require_nested_ordinary_bid_support=False,
                )
                pricing_only = MakerPaperEngine(
                    config, store, priority_policy=pricing_only_policy,
                )
                pricing_only._start_date(moment.date().isoformat())
                seed_opening_evidence(pricing_only)
                pricing_only_account = pricing_only.accounts[
                    "maker_v01_priority"
                ]
                pricing_only_assessment = (
                    pricing_only.analyzer.assess_market(tick, 137.874)
                )
                pricing_only._refresh_orders(
                    pricing_only_account, tick, pricing_only_assessment,
                    persist=False,
                )
                self.assertIsNone(pricing_only_account.buy_order)
            finally:
                store.close()

    def test_first_position_v251_opening_caution_is_evidence_matured(
        self,
    ) -> None:
        policy = PRIORITY_POLICY_FIRST_POSITION_V251_CANDIDATE
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v251-opening-maturity.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(config, store, priority_policy=policy)
                early = datetime(2026, 8, 28, 9, 40, 0, tzinfo=SHANGHAI)
                early_tick = self._replay_tick(
                    early, last=137.500, bid=137.500, ask=137.900,
                    trade_bonds=0.0, previous_close=137.874,
                )
                engine._start_date(early.date().isoformat())
                engine.analyzer.trade_evidence.extend((
                    TradeEvidence(
                        early_tick.market_ts_ms - 20_000,
                        137.500, 3_000.0, 3, "buy",
                    ),
                    TradeEvidence(
                        early_tick.market_ts_ms - 10_000,
                        137.510, 3_000.0, 3, "sell",
                    ),
                    TradeEvidence(
                        early_tick.market_ts_ms - 5_000,
                        137.520, 4_000.0, 1, "buy",
                    ),
                ))
                self.assertIsNone(engine._opening_trade_reference_cap(
                    policy, early_tick, 137.700, "persistent_inside_market",
                ))
                self.assertIn(
                    policy.model_id,
                    engine.opening_discovery_matured_models,
                )
                # Strong two-sided discovery finishes the day-level opening
                # episode.  Later expiry of that rolling evidence must not
                # turn the afternoon into another opening.
                degraded_tick = replace(
                    early_tick,
                    market_ts_ms=early_tick.market_ts_ms + 6 * 60 * 1_000,
                    market_time="09:46:00.000",
                )
                engine.analyzer.trade_evidence.clear()
                engine.analyzer.trade_evidence.extend((
                    TradeEvidence(
                        degraded_tick.market_ts_ms - 20_000,
                        137.490, 1_000.0, 1, "sell",
                    ),
                    TradeEvidence(
                        degraded_tick.market_ts_ms - 10_000,
                        137.500, 1_000.0, 1, "sell",
                    ),
                ))
                self.assertIsNone(
                    engine._opening_trade_reference_cap(
                        policy, degraded_tick, 137.700,
                        "persistent_inside_market",
                    )
                )

                # A short-lived ordinary trade anchor before 10:00 is valid
                # for that frame, but it must not permanently unlock the day.
                # This is the shape seen in Jiangxi Copper at 09:33--09:36.
                anchor_engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                anchor_tick = replace(
                    early_tick,
                    market_ts_ms=early_tick.market_ts_ms - 6 * 60 * 1_000,
                    market_time="09:34:00.000",
                )
                anchor_engine._start_date(anchor_tick.market_date)
                self.assertIsNone(anchor_engine._opening_trade_reference_cap(
                    policy, anchor_tick, 137.000,
                    "intraday_trade_anchor",
                ))
                self.assertNotIn(
                    policy.model_id,
                    anchor_engine.opening_discovery_matured_models,
                )
                quote_tick = replace(
                    anchor_tick,
                    market_ts_ms=anchor_tick.market_ts_ms + 2 * 60 * 1_000,
                    market_time="09:36:00.000",
                )
                anchor_engine.analyzer.trade_evidence.extend((
                    TradeEvidence(
                        quote_tick.market_ts_ms - 20_000,
                        137.000, 1_000.0, 1, "sell",
                    ),
                    TradeEvidence(
                        quote_tick.market_ts_ms - 10_000,
                        137.001, 1_000.0, 1, "sell",
                    ),
                ))
                self.assertAlmostEqual(
                    anchor_engine._opening_trade_reference_cap(
                        policy, quote_tick, 137.700,
                        "persistent_inside_market",
                    ),
                    137.000,
                )

                # A separate still-immature episode remains cautious after
                # 10:00 when the tape is sparse and one-sided; the clock alone
                # is not permission to trust the quote midpoint.
                late_engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                late = datetime(2026, 8, 28, 10, 2, 0, tzinfo=SHANGHAI)
                late_tick = self._replay_tick(
                    late, last=137.500, bid=137.500, ask=137.900,
                    trade_bonds=0.0, previous_close=137.874,
                )
                late_engine._start_date(late.date().isoformat())
                late_engine.analyzer.trade_evidence.extend((
                    TradeEvidence(
                        late_tick.market_ts_ms - 20_000,
                        137.490, 1_000.0, 1, "sell",
                    ),
                    TradeEvidence(
                        late_tick.market_ts_ms - 10_000,
                        137.500, 1_000.0, 1, "sell",
                    ),
                ))
                self.assertAlmostEqual(
                    late_engine._opening_trade_reference_cap(
                        policy, late_tick, 137.700,
                        "persistent_inside_market",
                    ),
                    137.490,
                )

                # A later clock time still does not prove price discovery.
                # With the same sparse one-sided tape, caution remains active
                # beyond 10:30 until causal evidence actually matures it.
                later_tick = replace(
                    late_tick,
                    market_ts_ms=late_tick.market_ts_ms + 30 * 60 * 1_000,
                    market_time="10:32:00.000",
                )
                late_engine.analyzer.trade_evidence.extend((
                    TradeEvidence(
                        later_tick.market_ts_ms - 20_000,
                        137.480, 1_000.0, 1, "sell",
                    ),
                    TradeEvidence(
                        later_tick.market_ts_ms - 10_000,
                        137.490, 1_000.0, 1, "sell",
                    ),
                ))
                self.assertAlmostEqual(
                    late_engine._opening_trade_reference_cap(
                        policy, later_tick, 137.700,
                        "persistent_inside_market",
                    ),
                    137.480,
                )

                # After the nominal prior, a fresh reliable trade anchor can
                # finally confirm maturity.  The completed episode then stays
                # complete when the rolling window later becomes sparse.
                mature_anchor_tick = replace(
                    late_tick,
                    market_ts_ms=later_tick.market_ts_ms + 60_000,
                    market_time="10:33:00.000",
                )
                self.assertIsNone(
                    late_engine._opening_trade_reference_cap(
                        policy, mature_anchor_tick, 137.500,
                        "intraday_trade_anchor",
                    )
                )
                self.assertIn(
                    policy.model_id,
                    late_engine.opening_discovery_matured_models,
                )
                sparse_after_maturity = replace(
                    mature_anchor_tick,
                    market_ts_ms=mature_anchor_tick.market_ts_ms + 6 * 60 * 1_000,
                    market_time="10:39:00.000",
                )
                late_engine.analyzer.trade_evidence.clear()
                late_engine.analyzer.trade_evidence.append(TradeEvidence(
                    sparse_after_maturity.market_ts_ms - 10_000,
                    137.400, 1_000.0, 1, "sell",
                ))
                self.assertIsNone(
                    late_engine._opening_trade_reference_cap(
                        policy, sparse_after_maturity, 137.700,
                        "persistent_inside_market",
                    )
                )
            finally:
                store.close()

    def test_first_position_v251_requires_nested_ordinary_bid_support(
        self,
    ) -> None:
        policy = PRIORITY_POLICY_FIRST_POSITION_V251_CANDIDATE
        moment = datetime(2026, 8, 28, 10, 5, 0, tzinfo=SHANGHAI)
        outer_only = replace(
            self._replay_tick(
                moment, last=137.400, bid=137.400, ask=137.799,
                bid_bonds=1_000.0,
            ),
            bids=(
                (137.400, 1_000.0),
                (137.201, 3_000.0),
                (137.200, 2_000.0),
            ),
        )
        nested = replace(
            outer_only,
            bids=(
                (137.400, 1_000.0),
                (137.350, 2_000.0),
                (137.200, 2_000.0),
            ),
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v251-nested-support.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(config, store, priority_policy=policy)
                self.assertTrue(engine._ordinary_nested_bid_support_is_safe(
                    PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE,
                    outer_only,
                    0.20,
                ))
                self.assertFalse(engine._ordinary_nested_bid_support_is_safe(
                    policy, outer_only, 0.20,
                ))
                self.assertTrue(engine._ordinary_nested_bid_support_is_safe(
                    policy, nested, 0.20,
                ))
                self.assertTrue(engine._ordinary_nested_bid_support_is_safe(
                    policy, outer_only, 0.50,
                ))
            finally:
                store.close()

    def test_first_position_v251_r2_uses_wide_trade_corridor_not_median(
        self,
    ) -> None:
        policy = PRIORITY_POLICY_FIRST_POSITION_V251_R2_CANDIDATE
        self.assertEqual(
            policy.parent_model_id,
            PRIORITY_POLICY_FIRST_POSITION_V251_CANDIDATE.model_id,
        )
        self.assertTrue(
            PRIORITY_POLICY_FIRST_POSITION_V251_CANDIDATE
                .enable_opening_trade_constrained_reference,
        )
        self.assertFalse(policy.enable_opening_trade_constrained_reference)
        self.assertTrue(policy.require_nested_ordinary_bid_support)
        self.assertTrue(policy.enable_ordinary_liquidity_corridor_entry)

        moment = datetime(2026, 8, 4, 9, 58, 18, tzinfo=SHANGHAI)
        tick = replace(
            self._replay_tick(
                moment, last=136.997, bid=136.300, ask=137.000,
                trade_bonds=1_000.0, inferred_side="buy",
                bid_bonds=1_000.0, ask_bonds=1_000.0,
            ),
            bids=(
                (136.300, 1_000.0),
                (136.221, 2_000.0),
                (136.204, 1_000.0),
                (136.201, 4_000.0),
                (136.200, 12_000.0),
            ),
            asks=((137.000, 1_000.0),),
        )
        assessment = MarketAssessment(
            reference_price=136.650,
            reference_low=136.300,
            reference_high=137.000,
            reference_source="persistent_inside_market",
            reference_confidence=0.45,
            state="stable",
            state_score=0,
            state_confidence=0.65,
            recent_buy_bonds=3_000.0,
            recent_sell_bonds=3_000.0,
            midpoint_change=0.0,
            short_ask_change=0.0,
            largest_ask_gap=0.0,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v251-r2-corridor.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                engine._start_date(moment.date().isoformat())
                now_ms = tick.market_ts_ms
                engine.analyzer.trade_evidence.extend((
                    TradeEvidence(
                        now_ms - 150_000, 136.203, 1_000.0, 1, "sell",
                    ),
                    TradeEvidence(
                        now_ms - 120_000, 136.204, 2_000.0, 2, "sell",
                    ),
                    TradeEvidence(
                        now_ms - 90_000, 136.993, 1_000.0, 1, "buy",
                    ),
                    TradeEvidence(
                        now_ms - 60_000, 136.996, 1_000.0, 1, "buy",
                    ),
                    TradeEvidence(
                        now_ms - 30_000, 136.997, 1_000.0, 1, "buy",
                    ),
                ))
                account = engine.accounts["maker_v01_priority"]
                ceiling = (
                    engine._ordinary_liquidity_corridor_buy_ceiling(
                        account, tick, assessment, 136.301,
                    )
                )
                self.assertAlmostEqual(ceiling, 136.304)

                # An already valid customer-base high sell has priority.  A
                # plain extra-entry corridor may not fill first and make the
                # engine cancel that more valuable high leg; the separately
                # registered joint-corridor branch owns simultaneous quotes.
                base_lot = next(
                    lot for lot in account.lots.values()
                    if lot.entry_price is None
                )
                account.sell_orders[base_lot.db_id] = engine._new_order(
                    account, tick, side="sell", kind="inventory_exit",
                    lot_id=base_lot.db_id, price=136.999,
                    quantity=base_lot.remaining_quantity,
                    queue_ahead=0.0, target_price=136.999,
                    persist=False,
                )
                self.assertIsNone(
                    engine._ordinary_liquidity_corridor_buy_ceiling(
                        account, tick, assessment, 136.301,
                    )
                )
                account.sell_orders.clear()

                # The immutable first build collapses the two clusters to the
                # low-side weighted median.  The r2 entry reference remains
                # unchanged; direction and location stay visible instead.
                self.assertAlmostEqual(
                    engine._opening_trade_reference_cap(
                        PRIORITY_POLICY_FIRST_POSITION_V251_CANDIDATE,
                        tick, 136.650, "persistent_inside_market",
                    ),
                    136.204,
                )
                self.assertAlmostEqual(
                    engine._ordinary_extra_entry_reference(
                        account, tick, 136.650,
                        "persistent_inside_market",
                        apply_opening_trade_constraint=True,
                    ),
                    136.650,
                )
            finally:
                store.close()

    def test_first_position_v251_r2_rejects_one_sided_or_thin_corridor(
        self,
    ) -> None:
        policy = PRIORITY_POLICY_FIRST_POSITION_V251_R2_CANDIDATE
        moment = datetime(2026, 8, 28, 9, 36, 13, tzinfo=SHANGHAI)
        tick = replace(
            self._replay_tick(
                moment, last=137.500, bid=137.400, ask=137.799,
                trade_bonds=1_000.0, inferred_side="sell",
                previous_close=137.874, bid_bonds=1_000.0,
                ask_bonds=2_000.0,
            ),
            bids=(
                (137.400, 1_000.0),
                (137.201, 3_000.0),
                (137.200, 2_000.0),
            ),
            asks=((137.799, 2_000.0),),
        )
        assessment = MarketAssessment(
            reference_price=137.68975,
            reference_low=137.400,
            reference_high=137.799,
            reference_source="persistent_inside_market",
            reference_confidence=0.45,
            state="possible_fall",
            state_score=-1,
            state_confidence=0.65,
            recent_buy_bonds=0.0,
            recent_sell_bonds=4_260.0,
            midpoint_change=0.0,
            short_ask_change=-0.08,
            largest_ask_gap=0.0,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v251-r2-reject.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                engine._start_date(moment.date().isoformat())
                now_ms = tick.market_ts_ms
                engine.previous_close_reference = 137.874
                engine.analyzer.trade_evidence.extend((
                    TradeEvidence(
                        now_ms - 138_000, 137.000, 1_000.0, 1, "sell",
                    ),
                    TradeEvidence(
                        now_ms - 75_000, 137.001, 1_000.0, 1, "sell",
                    ),
                    TradeEvidence(
                        now_ms - 3_000, 137.501, 1_260.0, 1, "sell",
                    ),
                    TradeEvidence(
                        now_ms, 137.500, 1_000.0, 1, "sell",
                    ),
                ))
                account = engine.accounts["maker_v01_priority"]
                self.assertIsNone(
                    engine._ordinary_liquidity_corridor_buy_ceiling(
                        account, tick, assessment, 137.401,
                    )
                )
                self.assertFalse(
                    engine._ordinary_nested_bid_support_is_safe(
                        policy, tick, 0.20,
                    )
                )

                # Even adding high-side buys cannot rescue a candidate whose
                # current one-tenth-yuan support is only 1,000 bonds.
                engine.analyzer.trade_evidence.extend((
                    TradeEvidence(
                        now_ms - 2_000, 137.790, 2_000.0, 2, "buy",
                    ),
                    TradeEvidence(
                        now_ms - 1_000, 137.799, 1_000.0, 1, "buy",
                    ),
                ))
                self.assertIsNone(
                    engine._ordinary_liquidity_corridor_buy_ceiling(
                        account, tick, assessment, 137.401,
                    )
                )
            finally:
                store.close()

    def test_first_position_v252_profitable_offer_tail_restores_only_base(
        self,
    ) -> None:
        policy = PRIORITY_POLICY_FIRST_POSITION_V252_CANDIDATE
        self.assertEqual(
            policy.parent_model_id,
            PRIORITY_POLICY_FIRST_POSITION_V251_R2_CANDIDATE.model_id,
        )
        self.assertFalse(
            PRIORITY_POLICY_FIRST_POSITION_V251_R2_CANDIDATE
                .enable_profitable_offer_tail_base_replenishment,
        )
        self.assertTrue(
            policy.enable_profitable_offer_tail_base_replenishment,
        )

        moment = datetime(2026, 8, 31, 10, 23, 51, tzinfo=SHANGHAI)
        tick = replace(
            self._replay_tick(
                moment, last=135.700, bid=135.015, ask=135.700,
                trade_bonds=1_000.0, inferred_side="buy",
                bid_bonds=1_000.0, ask_bonds=1_000.0,
            ),
            bids=((135.015, 1_000.0), (135.000, 12_000.0)),
            asks=((135.700, 1_000.0), (136.000, 21_000.0)),
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v252-offer-tail.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.inventory = 0.0
                account.lots.clear()
                account.replenishment_quantity = 1_000.0
                account.replenishment_sale_value = 135.998 * 1_000.0
                account.last_base_short_sale_ts_ms = (
                    tick.market_ts_ms - 20 * 60 * 1_000
                )
                engine.analyzer.trade_evidence.extend((
                    TradeEvidence(
                        tick.market_ts_ms - 54_000,
                        135.590, 2_000.0, 2, "buy",
                    ),
                    TradeEvidence(
                        tick.market_ts_ms - 33_000,
                        135.599, 1_000.0, 1, "buy",
                    ),
                    TradeEvidence(
                        tick.market_ts_ms - 27_000,
                        135.699, 1_000.0, 1, "buy",
                    ),
                    TradeEvidence(
                        tick.market_ts_ms,
                        135.700, 1_000.0, 1, "buy",
                    ),
                ))

                no_gap = replace(
                    tick,
                    asks=((135.700, 1_000.0), (135.850, 21_000.0)),
                )
                self.assertFalse(
                    engine._active_profitable_offer_tail_base_replenishment(
                        account, no_gap, persist=True,
                        received_ts_ns=no_gap.market_ts_ms * 1_000_000,
                    )
                )
                saved_evidence = tuple(engine.analyzer.trade_evidence)
                engine.analyzer.trade_evidence.clear()
                engine.analyzer.trade_evidence.extend(saved_evidence[-2:])
                self.assertFalse(
                    engine._active_profitable_offer_tail_base_replenishment(
                        account, tick, persist=True,
                        received_ts_ns=tick.market_ts_ms * 1_000_000,
                    )
                )
                engine.analyzer.trade_evidence.clear()
                engine.analyzer.trade_evidence.extend(saved_evidence)
                account.replenishment_sale_value = 135.850 * 1_000.0
                self.assertFalse(
                    engine._active_profitable_offer_tail_base_replenishment(
                        account, tick, persist=True,
                        received_ts_ns=tick.market_ts_ms * 1_000_000,
                    )
                )
                account.replenishment_sale_value = 135.998 * 1_000.0

                self.assertTrue(
                    engine._active_profitable_offer_tail_base_replenishment(
                        account, tick, persist=True,
                        received_ts_ns=tick.market_ts_ms * 1_000_000,
                    )
                )
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(account.customer_base_short_bonds, 0.0)
                fill = store.connection.execute(
                    "SELECT price,quantity,fill_reason FROM maker_paper_fills "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
                self.assertEqual(float(fill["price"]), 135.700)
                self.assertEqual(float(fill["quantity"]), 1_000.0)
                self.assertEqual(
                    fill["fill_reason"],
                    "active_profitable_offer_tail_base_replenishment",
                )

                # The same tape may reduce an existing base short only.  Once
                # inventory is neutral it cannot open an extra long.
                self.assertFalse(
                    engine._active_profitable_offer_tail_base_replenishment(
                        account, tick, persist=True,
                        received_ts_ns=tick.market_ts_ms * 1_000_000,
                    )
                )
                self.assertEqual(account.inventory, 1_000.0)
            finally:
                store.close()

    def test_first_position_v252_overrides_nested_support_only_for_wide_rr(
        self,
    ) -> None:
        policy = PRIORITY_POLICY_FIRST_POSITION_V252_CANDIDATE
        self.assertFalse(
            PRIORITY_POLICY_FIRST_POSITION_V251_R2_CANDIDATE
                .enable_wide_reward_risk_nested_support_override,
        )
        self.assertTrue(
            policy.enable_wide_reward_risk_nested_support_override,
        )
        moment = datetime(2026, 8, 31, 11, 19, 30, tzinfo=SHANGHAI)
        target = replace(
            self._replay_tick(
                moment, last=135.202, bid=135.200, ask=135.695,
                bid_bonds=1_000.0, ask_bonds=1_000.0,
            ),
            bids=(
                (135.200, 1_000.0),
                (135.100, 1_000.0),
                (135.000, 12_000.0),
            ),
            asks=((135.695, 1_000.0), (135.700, 4_000.0)),
        )
        borderline = replace(
            target,
            market_time="09:54:51.000",
            bids=((135.271, 1_000.0), (135.000, 12_000.0)),
            asks=((135.496, 1_000.0),),
        )
        jiangxi = replace(
            target,
            market_time="09:36:13.000",
            bids=(
                (137.400, 1_000.0),
                (137.201, 3_000.0),
                (137.200, 2_000.0),
            ),
            asks=((137.799, 2_000.0),),
        )
        transient_low_offer = replace(
            target,
            market_ts_ms=target.market_ts_ms + 144_000,
            market_time="11:21:54.000",
            bids=(
                (135.200, 1_000.0),
                (135.006, 2_000.0),
                (135.005, 1_000.0),
                (135.000, 12_000.0),
            ),
            asks=(
                (135.399, 1_000.0),
                (135.693, 350.0),
                (135.696, 1_900.0),
                (135.999, 1_000.0),
                (136.000, 18_450.0),
            ),
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v252-wide-rr.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                self.assertFalse(
                    engine._ordinary_nested_bid_support_is_safe(
                        PRIORITY_POLICY_FIRST_POSITION_V251_R2_CANDIDATE,
                        target, 0.2465, 135.201,
                    )
                )
                self.assertTrue(
                    engine._ordinary_nested_bid_support_is_safe(
                        policy, target, 0.2465, 135.201,
                    )
                )
                engine.analyzer.book_quotes.append(BookQuote(
                    target.market_ts_ms, 135.201, 135.695,
                ))
                self.assertTrue(
                    engine._ordinary_nested_bid_support_is_safe(
                        policy, transient_low_offer, 0.2465, 135.201,
                    )
                )
                self.assertFalse(
                    engine._ordinary_nested_bid_support_is_safe(
                        policy, borderline, 0.224, 135.272,
                    )
                )
                self.assertFalse(
                    engine._ordinary_nested_bid_support_is_safe(
                        policy, jiangxi, 0.289, 137.401,
                    )
                )
            finally:
                store.close()

    def test_first_position_v26_releases_and_redeploys_only_extra_capacity(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE
        policy = PRIORITY_POLICY_FIRST_POSITION_V26_CANDIDATE
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertFalse(
            parent.enable_support_collapse_capacity_redeployment,
        )
        self.assertTrue(
            policy.enable_support_collapse_capacity_redeployment,
        )
        self.assertFalse(
            QUEUE_POLICY_V118_CANDIDATE
                .enable_support_collapse_capacity_redeployment,
        )
        self.assertFalse(
            WINDFALL_POLICY_V20_CANDIDATE
                .enable_support_collapse_capacity_redeployment,
        )

        entry_time = datetime(2026, 9, 2, 11, 14, 41, tzinfo=SHANGHAI)
        pressure = MarketAssessment(
            reference_price=136.700,
            reference_low=136.500,
            reference_high=136.900,
            reference_source="intraday_trade_anchor",
            reference_confidence=0.75,
            state="possible_fall",
            state_score=-1,
            state_confidence=0.75,
            recent_buy_bonds=6_000.0,
            recent_sell_bonds=10_000.0,
            midpoint_change=-0.398,
            short_ask_change=-0.146,
            largest_ask_gap=0.0,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        context = MakerDecisionContext(
            reference_price=136.700,
            reference_source="intraday_trade_anchor",
            reliable_anchor=True,
            spread=0.409,
            bid_support_bonds=11_000.0,
            ask_supply_bonds=4_000.0,
            wall_threshold_bonds=5_000.0,
        )

        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v26-capacity-redeploy.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                engine._start_date(entry_time.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                entry_tick = replace(
                    self._replay_tick(
                        entry_time, last=136.701,
                        bid=136.700, ask=136.701,
                        bid_bonds=2_000.0, ask_bonds=5_000.0,
                    ),
                    bids=(
                        (136.700, 2_000.0),
                        (136.680, 2_000.0),
                        (136.657, 2_000.0),
                        (136.650, 1_000.0),
                        (136.620, 3_000.0),
                    ),
                )
                entry_order = engine._new_order(
                    account, entry_tick, side="buy",
                    kind="low_bid_reversion", lot_id=None,
                    price=136.702, quantity=1_000.0,
                    queue_ahead=0.0, target_price=None,
                    price_boundary=136.704, persist=True,
                )
                engine._fill_buy(
                    account, entry_tick, entry_order, 1_000.0,
                    entry_tick.market_ts_ms * 1_000_000,
                    kind="low_bid_reversion", target_price=None,
                    persist=True, reason="passive_buy",
                )
                extra_lot = next(
                    lot for lot in account.lots.values()
                    if lot.entry_price is not None
                )
                base_lot = next(
                    lot for lot in account.lots.values()
                    if lot.entry_price is None
                )
                self.assertEqual(account.inventory, 2_000.0)

                healthy = replace(
                    self._replay_tick(
                        entry_time + timedelta(minutes=1, seconds=30),
                        last=136.700, bid=136.657, ask=136.975,
                        bid_bonds=2_000.0, ask_bonds=2_000.0,
                    ),
                    bids=(
                        (136.657, 2_000.0),
                        (136.602, 1_000.0),
                        (136.511, 2_000.0),
                        (136.510, 2_000.0),
                        (136.101, 4_000.0),
                    ),
                    asks=((136.975, 2_000.0), (136.976, 5_000.0)),
                )
                self.assertFalse(
                    engine._support_collapse_capacity_release_ready(
                        account, extra_lot, healthy,
                        replace(pressure, state="rising"),
                    )
                )

                collapse = replace(
                    self._replay_tick(
                        entry_time + timedelta(minutes=3, seconds=45),
                        last=136.602, bid=136.101, ask=136.510,
                        bid_bonds=8_000.0, ask_bonds=1_000.0,
                    ),
                    bids=(
                        (136.101, 8_000.0),
                        (136.100, 3_000.0),
                        (136.000, 2_000.0),
                        (135.701, 8_000.0),
                    ),
                    asks=((136.510, 1_000.0), (136.655, 1_000.0)),
                )
                self.assertTrue(
                    engine._support_collapse_capacity_release_ready(
                        account, extra_lot, collapse, pressure,
                    )
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, collapse, pressure, persist=True,
                    )
                release = account.sell_orders[extra_lot.db_id]
                self.assertEqual(
                    release.kind,
                    "support_collapse_capacity_release_exit",
                )
                self.assertEqual(release.limit_price, 136.509)

                release_fill = replace(
                    collapse,
                    market_ts_ms=collapse.market_ts_ms + 30_000,
                    market_time="11:18:56.000",
                    last_price=136.510,
                    bids=((136.521, 1_000.0), (136.510, 2_000.0)),
                    asks=((136.656, 2_000.0),),
                    trade_bonds=3_000.0,
                    transaction_delta=1,
                    inferred_side="buy",
                    side_confidence="high",
                )
                engine._process_resting_orders(
                    account, release_fill, persist=True,
                    received_ts_ns=release_fill.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(base_lot.remaining_quantity, 1_000.0)
                self.assertEqual(
                    account.last_support_collapse_exit_price, 136.509,
                )
                self.assertTrue(
                    engine._support_collapse_reentry_restriction_active(
                        account, release_fill,
                    )
                )

                middle = replace(
                    release_fill,
                    market_ts_ms=release_fill.market_ts_ms + 51_000,
                    market_time="11:19:47.000",
                    last_price=136.440,
                    bids=(
                        (136.441, 1_000.0),
                        (136.401, 2_000.0),
                        (136.400, 2_000.0),
                        (136.301, 1_000.0),
                        (136.300, 2_000.0),
                    ),
                    asks=((136.655, 1_000.0),),
                    trade_bonds=0.0,
                    transaction_delta=0,
                    inferred_side="none",
                    side_confidence="none",
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, middle, pressure, persist=True,
                    )
                if account.buy_order is not None:
                    self.assertLessEqual(
                        account.buy_order.limit_price, 136.209,
                    )
                self.assertEqual(account.sell_orders, {})

                lower = replace(
                    middle,
                    market_ts_ms=middle.market_ts_ms + 30_000,
                    market_time="11:20:17.000",
                    last_price=136.401,
                    bids=(
                        (136.201, 2_000.0),
                        (136.200, 2_000.0),
                        (136.101, 8_000.0),
                        (136.100, 3_000.0),
                    ),
                    asks=((136.655, 1_000.0),),
                )
                with patch.object(
                    engine, "_decision_context", return_value=context,
                ):
                    engine._refresh_orders(
                        account, lower, pressure, persist=True,
                    )
                self.assertIsNotNone(account.buy_order)
                self.assertEqual(
                    account.buy_order.kind,
                    "support_collapse_capacity_redeploy_entry",
                )
                self.assertLessEqual(
                    account.buy_order.limit_price, 136.209,
                )

                lower_fill = replace(
                    lower,
                    market_ts_ms=lower.market_ts_ms + 9_000,
                    market_time="11:20:26.000",
                    last_price=136.201,
                    bids=((136.200, 2_000.0), (136.101, 8_000.0)),
                    trade_bonds=2_000.0,
                    transaction_delta=1,
                    inferred_side="sell",
                    side_confidence="high",
                )
                engine._process_resting_orders(
                    account, lower_fill, persist=True,
                    received_ts_ns=lower_fill.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 2_000.0)
                redeployed_lot = max(
                    (
                        lot for lot in account.lots.values()
                        if lot.entry_price is not None
                        and lot.remaining_quantity > 1e-9
                    ),
                    key=lambda lot: lot.opened_ms,
                )
                self.assertEqual(
                    redeployed_lot.kind,
                    "support_collapse_capacity_redeploy_entry",
                )
                with patch.object(
                    engine, "_decision_context",
                    return_value=replace(
                        context, reference_price=136.500,
                    ),
                ):
                    engine._refresh_orders(
                        account, lower_fill,
                        replace(pressure, state="stable"), persist=True,
                    )
                self.assertEqual(len(account.sell_orders), 1)
                high_exit = account.sell_orders[redeployed_lot.db_id]
                self.assertEqual(high_exit.limit_price, 136.654)
                self.assertNotIn(base_lot.db_id, account.sell_orders)

                high_fill = replace(
                    lower_fill,
                    market_ts_ms=lower_fill.market_ts_ms + 204_000,
                    market_time="11:23:50.000",
                    last_price=136.699,
                    bids=((136.101, 4_000.0),),
                    asks=((136.699, 1_000.0),),
                    trade_bonds=1_000.0,
                    transaction_delta=1,
                    inferred_side="buy",
                    side_confidence="high",
                )
                engine._process_resting_orders(
                    account, high_fill, persist=True,
                    received_ts_ns=high_fill.market_ts_ms * 1_000_000,
                )
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(base_lot.remaining_quantity, 1_000.0)
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    high_fill.market_ts_ms, 136.699, 1_000.0, 1, "buy",
                ))
                self.assertFalse(
                    engine._support_collapse_reentry_restriction_active(
                        account, high_fill,
                    )
                )
                self.assertTrue(
                    engine._support_collapse_base_protection_active(
                        account, high_fill,
                    )
                )
            finally:
                store.close()

    def test_first_position_v26_r2_rechecks_every_release_reprice(
        self,
    ) -> None:
        old_policy = PRIORITY_POLICY_FIRST_POSITION_V26_CANDIDATE
        policy = PRIORITY_POLICY_FIRST_POSITION_V26_R2_CANDIDATE
        self.assertEqual(policy.parent_model_id, old_policy.model_id)
        self.assertFalse(
            old_policy.enable_support_collapse_consistency_revision,
        )
        self.assertTrue(
            policy.enable_support_collapse_consistency_revision,
        )

        moment = datetime(2026, 8, 28, 9, 38, 7, tzinfo=SHANGHAI)
        pressure = MarketAssessment(
            reference_price=137.197,
            reference_low=137.100,
            reference_high=137.300,
            reference_source="intraday_trade_anchor",
            reference_confidence=0.80,
            state="falling",
            state_score=-2,
            state_confidence=0.80,
            recent_buy_bonds=1_000.0,
            recent_sell_bonds=8_000.0,
            midpoint_change=-0.40,
            short_ask_change=-0.20,
            largest_ask_gap=0.997,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        anomaly = replace(
            self._replay_tick(
                moment, last=136.199, bid=136.101, ask=136.199,
                bid_bonds=8_000.0, ask_bonds=1_000.0,
            ),
            bids=(
                (136.101, 8_000.0),
                (136.100, 3_000.0),
                (136.000, 2_000.0),
            ),
            asks=(
                (136.199, 1_000.0),
                (136.200, 440.0),
                (137.197, 2_000.0),
                (137.198, 3_000.0),
            ),
        )

        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v26-r2-reprice.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                entry_tick = replace(
                    anomaly,
                    market_ts_ms=anomaly.market_ts_ms - 39_000,
                    market_time="09:37:28.000",
                    last_price=137.202,
                    bids=((137.201, 2_000.0),),
                    asks=((137.202, 2_000.0),),
                )
                entry_order = engine._new_order(
                    account, entry_tick, side="buy",
                    kind="low_bid_reversion", lot_id=None,
                    price=137.202, quantity=1_000.0,
                    queue_ahead=0.0, target_price=None,
                    price_boundary=137.202, persist=True,
                )
                engine._fill_buy(
                    account, entry_tick, entry_order, 1_000.0,
                    entry_tick.market_ts_ms * 1_000_000,
                    kind="low_bid_reversion", target_price=None,
                    persist=True,
                )
                lot = next(
                    item for item in account.lots.values()
                    if item.entry_price is not None
                )
                release = engine._new_order(
                    account, entry_tick, side="sell",
                    kind="support_collapse_capacity_release_exit",
                    lot_id=lot.db_id, price=136.998,
                    quantity=1_000.0, queue_ahead=0.0,
                    target_price=136.998, price_boundary=136.998,
                    persist=True,
                )
                account.sell_orders[lot.db_id] = release
                account.last_asks = ((137.197, 2_000.0),)
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    anomaly.market_ts_ms - 3_000,
                    137.197, 1_000.0, 1, "buy",
                ))

                account.policy = old_policy
                self.assertTrue(
                    engine._support_collapse_capacity_release_ready(
                        account, lot, anomaly, pressure,
                    )
                )
                account.policy = policy
                self.assertFalse(
                    engine._support_collapse_capacity_release_ready(
                        account, lot, anomaly, pressure,
                    )
                )

                # Even while the 0.25-yuan loss boundary is still respected,
                # the release must not sell into a cluster that the same
                # model recognises as an isolated deep-discount buy.
                lot.entry_price = 136.400
                self.assertIsNotNone(
                    engine._isolated_deep_discount_decision(
                        account, anomaly, 137.197,
                        "intraday_trade_anchor", policy,
                    )
                )
                self.assertFalse(
                    engine._support_collapse_capacity_release_ready(
                        account, lot, anomaly, pressure,
                    )
                )

                continuous = replace(
                    anomaly,
                    last_price=136.251,
                    bids=((135.900, 8_000.0), (135.899, 3_000.0)),
                    asks=(
                        (136.251, 1_000.0),
                        (136.252, 1_000.0),
                        (136.253, 2_000.0),
                    ),
                )
                self.assertTrue(
                    engine._support_collapse_capacity_release_ready(
                        account, lot, continuous, pressure,
                    )
                )
            finally:
                store.close()

    def test_first_position_v26_r2_uses_recovery_latches_not_base_timer(
        self,
    ) -> None:
        policy = PRIORITY_POLICY_FIRST_POSITION_V26_R2_CANDIDATE
        entry_time = datetime(2026, 9, 2, 11, 14, 41, tzinfo=SHANGHAI)

        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v26-r2-latches.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                engine._start_date(entry_time.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                entry_tick = self._replay_tick(
                    entry_time, last=136.702,
                    bid=136.701, ask=136.702,
                    bid_bonds=2_000.0, ask_bonds=2_000.0,
                )
                entry_order = engine._new_order(
                    account, entry_tick, side="buy",
                    kind="low_bid_reversion", lot_id=None,
                    price=136.702, quantity=1_000.0,
                    queue_ahead=0.0, target_price=None,
                    price_boundary=136.702, persist=True,
                )
                engine._fill_buy(
                    account, entry_tick, entry_order, 1_000.0,
                    entry_tick.market_ts_ms * 1_000_000,
                    kind="low_bid_reversion", target_price=None,
                    persist=True,
                )
                original_lot = next(
                    item for item in account.lots.values()
                    if item.entry_price is not None
                )

                release_tick = replace(
                    entry_tick,
                    market_ts_ms=entry_tick.market_ts_ms + 255_000,
                    market_time="11:18:56.000",
                    last_price=136.510,
                    bids=((136.521, 1_000.0), (136.510, 2_000.0)),
                    asks=((136.656, 2_000.0),),
                )
                release = engine._new_order(
                    account, release_tick, side="sell",
                    kind="support_collapse_capacity_release_exit",
                    lot_id=original_lot.db_id, price=136.509,
                    quantity=1_000.0, queue_ahead=0.0,
                    target_price=136.509, price_boundary=136.509,
                    persist=True,
                )
                account.sell_orders[original_lot.db_id] = release
                engine._fill_sell(
                    account, release_tick, release, 1_000.0,
                    release_tick.market_ts_ms * 1_000_000,
                    persist=True,
                )
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(
                    account.last_support_collapse_entry_price, 136.702,
                )
                self.assertEqual(
                    account.pending_inventory_turn_quantity, 1_000.0,
                )
                self.assertEqual(account.last_stalled_extra_exit_price, 0.0)

                lower_tick = replace(
                    release_tick,
                    market_ts_ms=release_tick.market_ts_ms + 90_000,
                    market_time="11:20:26.000",
                    last_price=136.201,
                    bids=((136.200, 2_000.0), (136.101, 8_000.0)),
                    asks=((136.655, 1_000.0),),
                )
                lower_order = engine._new_order(
                    account, lower_tick, side="buy",
                    kind="support_collapse_capacity_redeploy_entry",
                    lot_id=None, price=136.201, quantity=1_000.0,
                    queue_ahead=0.0, target_price=None,
                    price_boundary=136.209, persist=True,
                )
                engine._fill_buy(
                    account, lower_tick, lower_order, 1_000.0,
                    lower_tick.market_ts_ms * 1_000_000,
                    kind="support_collapse_capacity_redeploy_entry",
                    target_price=None, persist=True,
                )
                self.assertEqual(
                    account.pending_inventory_turn_quantity, 0.0,
                )
                redeployed_lot = next(
                    item for item in account.lots.values()
                    if item.kind
                        == "support_collapse_capacity_redeploy_entry"
                )

                high_tick = replace(
                    lower_tick,
                    market_ts_ms=lower_tick.market_ts_ms + 204_000,
                    market_time="11:23:50.000",
                    last_price=136.699,
                    bids=((136.101, 4_000.0),),
                    asks=((136.699, 1_000.0),),
                )
                high_order = engine._new_order(
                    account, high_tick, side="sell",
                    kind="inventory_exit", lot_id=redeployed_lot.db_id,
                    price=136.698, quantity=1_000.0,
                    queue_ahead=0.0, target_price=136.698,
                    price_boundary=136.698, persist=True,
                )
                account.sell_orders[redeployed_lot.db_id] = high_order
                engine._fill_sell(
                    account, high_tick, high_order, 1_000.0,
                    high_tick.market_ts_ms * 1_000_000,
                    persist=True,
                )
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    high_tick.market_ts_ms,
                    136.699, 1_000.0, 1, "buy",
                ))
                self.assertEqual(account.inventory, 1_000.0)
                self.assertEqual(
                    account.pending_inventory_turn_quantity, 1_000.0,
                )
                self.assertFalse(
                    engine._support_collapse_reentry_restriction_active(
                        account, high_tick,
                    )
                )
                self.assertTrue(
                    account.support_collapse_extra_reentry_released,
                )
                self.assertTrue(
                    engine._support_collapse_base_protection_active(
                        account, high_tick,
                    )
                )

                after_attack_window = replace(
                    high_tick,
                    market_ts_ms=high_tick.market_ts_ms + 61_000,
                    market_time="11:24:51.000",
                )
                self.assertFalse(
                    engine._support_collapse_reentry_restriction_active(
                        account, after_attack_window,
                    )
                )

                recovered_tick = replace(
                    after_attack_window,
                    market_ts_ms=after_attack_window.market_ts_ms + 3_000,
                    market_time="11:24:54.000",
                    last_price=136.702,
                )
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    recovered_tick.market_ts_ms,
                    136.702, 1_000.0, 1, "buy",
                ))
                self.assertFalse(
                    engine._support_collapse_base_protection_active(
                        account, recovered_tick,
                    )
                )
                self.assertTrue(account.support_collapse_base_short_released)
            finally:
                store.close()

    def test_first_position_v26_r3_retires_recovered_stop_turn_only(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_FIRST_POSITION_V26_R2_CANDIDATE
        policy = PRIORITY_POLICY_FIRST_POSITION_V26_R3_CANDIDATE
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertFalse(
            parent.retire_recovered_support_collapse_pending_turn,
        )
        self.assertTrue(
            policy.retire_recovered_support_collapse_pending_turn,
        )
        moment = datetime(2026, 8, 14, 13, 26, 51, tzinfo=SHANGHAI)

        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v26-r3-recovery.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.last_support_collapse_exit_price = 135.327
                account.last_support_collapse_exit_ts_ms = int(
                    moment.timestamp() * 1_000
                )
                account.last_support_collapse_entry_price = 135.508
                account.pending_inventory_turn_quantity = 1_500.0
                account.pending_inventory_turn_sale_value = 203_327.0
                account.pending_support_collapse_turn_quantity = 1_000.0
                account.pending_support_collapse_turn_sale_value = 135_327.0

                below_entry = self._replay_tick(
                    moment + timedelta(seconds=30),
                    last=135.500, bid=135.499, ask=135.500,
                    bid_bonds=2_000.0, ask_bonds=2_000.0,
                )
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    below_entry.market_ts_ms,
                    135.500, 1_000.0, 1, "buy",
                ))
                engine._update_support_collapse_recovery_latches(
                    account, below_entry,
                )
                self.assertTrue(
                    account.support_collapse_extra_reentry_released,
                )
                self.assertFalse(
                    account.support_collapse_base_short_released,
                )
                self.assertEqual(
                    account.pending_inventory_turn_quantity, 1_500.0,
                )

                recovered_entry = replace(
                    below_entry,
                    market_ts_ms=below_entry.market_ts_ms + 3_000,
                    market_time="13:27:24.000",
                    last_price=135.508,
                )
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    recovered_entry.market_ts_ms,
                    135.508, 1_000.0, 1, "buy",
                ))
                engine._update_support_collapse_recovery_latches(
                    account, recovered_entry,
                )
                self.assertTrue(
                    account.support_collapse_base_short_released,
                )
                self.assertEqual(
                    account.pending_support_collapse_turn_quantity, 0.0,
                )
                # The unrelated 500-bond turn remains registered.  Only the
                # quantity created by the recovered support-collapse stop is
                # detached from future ordinary T decisions.
                self.assertEqual(
                    account.pending_inventory_turn_quantity, 500.0,
                )
                self.assertAlmostEqual(
                    account.pending_inventory_turn_sale_value, 68_000.0,
                )
            finally:
                store.close()

    def test_first_position_v263_promotes_v26_r3_without_behavior_change(
        self,
    ) -> None:
        parent = PRIORITY_POLICY_FIRST_POSITION_V26_R3_CANDIDATE
        policy = PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE
        self.assertEqual(policy.parent_model_id, parent.model_id)
        self.assertEqual(policy.model_id, "maker_priority_v2_63_candidate")
        self.assertEqual(policy.model_version, "2.63-candidate")
        self.assertEqual(
            policy,
            replace(
                parent,
                model_id="maker_priority_v2_63_candidate",
                model_version="2.63-candidate",
                parent_model_id=parent.model_id,
            ),
        )

    def test_first_position_v26_r3_keeps_redeployed_profit_turn_separate(
        self,
    ) -> None:
        policy = PRIORITY_POLICY_FIRST_POSITION_V26_R3_CANDIDATE
        moment = datetime(2026, 9, 2, 11, 20, 26, tzinfo=SHANGHAI)
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v26-r3-turn-identity.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, priority_policy=policy,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.pending_inventory_turn_quantity = 1_000.0
                account.pending_inventory_turn_sale_value = 136_509.0
                account.pending_support_collapse_turn_quantity = 1_000.0
                account.pending_support_collapse_turn_sale_value = 136_509.0
                tick = self._replay_tick(
                    moment, last=136.201, bid=136.200, ask=136.201,
                )
                buy = engine._new_order(
                    account, tick, side="buy",
                    kind="support_collapse_capacity_redeploy_entry",
                    lot_id=None, price=136.201, quantity=1_000.0,
                    queue_ahead=0.0, target_price=None, persist=True,
                )
                engine._fill_buy(
                    account, tick, buy, 1_000.0,
                    tick.market_ts_ms * 1_000_000,
                    kind="support_collapse_capacity_redeploy_entry",
                    target_price=None, persist=True,
                )
                self.assertEqual(
                    account.pending_support_collapse_turn_quantity, 0.0,
                )
                lot = next(
                    item for item in account.lots.values()
                    if item.kind == "support_collapse_capacity_redeploy_entry"
                )
                high_tick = replace(
                    tick,
                    market_ts_ms=tick.market_ts_ms + 204_000,
                    market_time="11:23:50.000",
                    last_price=136.699,
                )
                sell = engine._new_order(
                    account, high_tick, side="sell", kind="inventory_exit",
                    lot_id=lot.db_id, price=136.698, quantity=1_000.0,
                    queue_ahead=0.0, target_price=136.698, persist=True,
                )
                account.sell_orders[lot.db_id] = sell
                engine._fill_sell(
                    account, high_tick, sell, 1_000.0,
                    high_tick.market_ts_ms * 1_000_000, persist=True,
                )
                self.assertEqual(
                    account.pending_inventory_turn_quantity, 1_000.0,
                )
                self.assertEqual(
                    account.pending_support_collapse_turn_quantity, 0.0,
                )
                self.assertEqual(
                    account.pending_support_collapse_turn_sale_value, 0.0,
                )
            finally:
                store.close()

    def test_first_position_v22_rejects_a_large_disconnected_top_bid(
        self,
    ) -> None:
        self.assertEqual(
            PRIORITY_POLICY_FIRST_POSITION_V22_CANDIDATE.parent_model_id,
            PRIORITY_POLICY_FIRST_POSITION_V21_CANDIDATE.model_id,
        )
        self.assertFalse(
            PRIORITY_POLICY_FIRST_POSITION_V21_CANDIDATE
                .enable_strict_trend_market_structure,
        )
        self.assertTrue(
            PRIORITY_POLICY_FIRST_POSITION_V22_CANDIDATE
                .enable_strict_trend_market_structure,
        )
        moment = datetime(2026, 8, 18, 9, 38, 32, tzinfo=SHANGHAI)
        assessment = MarketAssessment(
            reference_price=134.950,
            reference_low=134.900,
            reference_high=135.000,
            reference_source="intraday_trade_anchor",
            reference_confidence=0.72,
            state="rising",
            state_score=4,
            state_confidence=0.90,
            recent_buy_bonds=20_000.0,
            recent_sell_bonds=0.0,
            midpoint_change=0.20,
            short_ask_change=0.0,
            largest_ask_gap=0.0,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        tick = replace(
            self._replay_tick(
                moment, last=135.988, bid=135.988, ask=135.990,
                bid_bonds=3_000.0, ask_bonds=11_100.0,
                trade_bonds=4_000.0, inferred_side="buy",
            ),
            bids=((135.988, 3_000.0), (134.900, 20_000.0)),
            asks=((135.990, 11_100.0), (136.000, 31_680.0)),
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v22-disconnected-top.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_FIRST_POSITION_V22_CANDIDATE,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                account.inventory = 0.0
                account.lots.clear()
                account.replenishment_quantity = 1_000.0
                account.replenishment_sale_value = 135.987 * 1_000.0
                strict = engine._assessment_for_account(
                    account, tick, assessment,
                )
                self.assertIs(strict, assessment)
                account.policy = PRIORITY_POLICY_FIRST_POSITION_V21_CANDIDATE
                parent = engine._assessment_for_account(
                    account, tick, assessment,
                )
                self.assertEqual(
                    parent.reference_source, "trend_price_discovery",
                )
            finally:
                store.close()

    def test_first_position_v22_accepts_real_wall_consumption_and_staircase(
        self,
    ) -> None:
        moment = datetime(2026, 8, 7, 10, 0, tzinfo=SHANGHAI)
        assessment = MarketAssessment(
            reference_price=137.500,
            reference_low=137.400,
            reference_high=137.600,
            reference_source="intraday_trade_anchor",
            reference_confidence=0.75,
            state="rising",
            state_score=4,
            state_confidence=0.90,
            recent_buy_bonds=25_000.0,
            recent_sell_bonds=1_000.0,
            midpoint_change=0.20,
            short_ask_change=0.0,
            largest_ask_gap=0.0,
            downside_book_vacuum=False,
            fragile_top_bid=False,
            iron_floor_price=None,
            iron_floor_bonds=0.0,
            evidence=(),
        )
        heavy = replace(
            self._replay_tick(
                moment, last=137.999, bid=137.999, ask=138.000,
                bid_bonds=7_000.0, ask_bonds=25_000.0,
            ),
            bids=((137.999, 7_000.0), (137.400, 5_000.0)),
            asks=((138.000, 25_000.0), (138.010, 15_000.0)),
        )
        attacked = replace(
            heavy,
            market_ts_ms=heavy.market_ts_ms + 3_000,
            market_time="10:00:03.000",
            last_price=138.000,
            trade_bonds=25_000.0,
            inferred_side="buy",
            bids=((137.999, 7_000.0), (137.998, 5_000.0)),
            asks=((138.000, 5_000.0), (138.010, 3_000.0)),
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v22-wall-consumption.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store,
                    priority_policy=PRIORITY_POLICY_FIRST_POSITION_V22_CANDIDATE,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_v01_priority"]
                self.assertIs(
                    engine._assessment_for_account(
                        account, heavy, assessment,
                    ),
                    assessment,
                )
                account.last_asks = heavy.asks
                lifted = engine._assessment_for_account(
                    account, attacked, assessment,
                )
                self.assertEqual(
                    lifted.reference_source, "trend_price_discovery",
                )
                self.assertEqual(lifted.reference_low, 137.999)
                self.assertEqual(lifted.reference_high, 138.000)
                self.assertGreaterEqual(
                    account.strict_trend_wall_confirmed_attack_bonds,
                    20_000.0,
                )
            finally:
                store.close()

    def test_first_position_v22_opening_discount_restores_base_before_extra(
        self,
    ) -> None:
        moment = datetime(2026, 8, 18, 9, 35, 22, tzinfo=SHANGHAI)
        far_book = replace(
            self._replay_tick(
                moment, last=137.700, bid=136.001, ask=137.009,
                bid_bonds=6_000.0, ask_bonds=1_000.0,
            ),
            bids=(
                (136.001, 6_000.0),
                (135.001, 4_000.0),
                (135.000, 42_000.0),
            ),
            asks=((137.009, 1_000.0), (137.010, 180.0)),
        )
        with tempfile.TemporaryDirectory() as temp:
            config = test_config(Path(temp) / "v22-opening-purpose.sqlite3")
            store = SQLiteStore(config)
            try:
                engine = MakerPaperEngine(
                    config, store, bond_code="132024.SH",
                    priority_policy=PRIORITY_POLICY_FIRST_POSITION_V22_CANDIDATE,
                )
                engine._start_date(moment.date().isoformat())
                account = engine.accounts["maker_132024_v01_priority"]
                account.inventory = 180.0
                account.last_ask = 137.010
                engine.analyzer.stock_previous_close = 47.72
                engine.analyzer.stock_latest_price = 46.74
                self.assertEqual(
                    engine._opening_confirmed_extra_inventory_capacity(
                        account, far_book,
                    ),
                    820.0,
                )
                account.inventory = 1_000.0
                self.assertEqual(
                    engine._opening_confirmed_extra_inventory_capacity(
                        account, far_book,
                    ),
                    0.0,
                )

                supported = replace(
                    far_book,
                    bids=((137.008, 3_000.0), (137.007, 2_000.0)),
                )
                engine.analyzer.trade_evidence.append(TradeEvidence(
                    market_ts_ms=moment.timestamp().__int__() * 1_000,
                    price=137.009,
                    bonds=5_000.0,
                    transactions=1,
                    side="buy",
                ))
                self.assertEqual(
                    engine._opening_confirmed_extra_inventory_capacity(
                        account, supported,
                    ),
                    1_000.0,
                )
                after_open = replace(
                    far_book,
                    market_ts_ms=far_book.market_ts_ms + 300_000,
                    market_time="09:40:22.000",
                )
                engine.analyzer.trade_evidence.clear()
                self.assertEqual(
                    engine._opening_confirmed_extra_inventory_capacity(
                        account, after_open,
                    ),
                    1_000.0,
                )
            finally:
                store.close()

    def test_queue_v10_does_not_inherit_priority_v11_fast_turnover(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-queue-v10.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True, fill_modes=("queue",),
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            moment = datetime(2026, 8, 14, 14, 5, 33, tzinfo=SHANGHAI)
            seed = self._replay_tick(
                moment, last=136.002, bid=136.001, ask=136.880,
                previous_close=136.400,
            )
            engine.on_replay_tick(seed, persist=True)
            account = engine.accounts["maker_v01_queue"]
            order = engine._new_order(
                account, seed, side="buy", kind="low_bid_reversion",
                lot_id=None, price=136.002, quantity=1_000,
                queue_ahead=0.0, target_price=None, persist=True,
            )
            account.buy_order = order
            engine._fill_buy(
                account, seed, order, 1_000,
                seed.market_ts_ms * 1_000_000,
                kind="low_bid_reversion", target_price=None, persist=True,
            )

            tight = self._replay_tick(
                moment + timedelta(minutes=2),
                last=136.007, bid=136.291, ask=136.299,
                bid_bonds=1_000, previous_close=136.400,
            )
            engine.on_replay_tick(tight, persist=True)

            self.assertEqual(account.inventory, 2_000)
            self.assertEqual(account.policy.model_id, "maker_queue_v1_0")
            self.assertEqual(
                store.connection.execute(
                    "SELECT COUNT(*) FROM maker_paper_fills "
                    "WHERE fill_reason='active_tight_spread_turnover'"
                ).fetchone()[0],
                0,
            )
            store.close()

    def test_super_windfall_uses_an_independent_one_hand_credit_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-windfall.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                fill_modes=("priority",),
                super_windfall_enabled=True,
                super_windfall_quantity_bonds=10,
                super_windfall_credit_cny=2_000,
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 14, 13, 5, 33, tzinfo=SHANGHAI)
            anomaly = replace(
                self._replay_tick(
                    start, last=136.200, bid=136.094, ask=136.317,
                    trade_bonds=1_000, inferred_side="buy",
                    previous_close=136.867,
                ),
                bids=(
                    (136.094, 2_000), (136.092, 1_000),
                    (136.089, 1_000), (136.052, 1_000),
                    (134.061, 8_000),
                ),
                asks=((136.317, 1_000), (136.318, 1_000)),
            )
            engine.on_replay_tick(anomaly, persist=True)

            windfall = engine.accounts["maker_v01_super_windfall"]
            standard = engine.accounts["maker_v01_priority"]
            self.assertEqual(windfall.initial_inventory, 0)
            self.assertEqual(windfall.maximum_inventory, 10)
            self.assertEqual(windfall.cash, 2_000)
            self.assertIsNotNone(windfall.buy_order)
            self.assertEqual(windfall.buy_order.limit_price, 134.062)
            self.assertEqual(windfall.buy_order.quantity, 10)
            self.assertNotEqual(windfall.strategy_id, standard.strategy_id)

            swept = replace(
                anomaly,
                tick_id=anomaly.tick_id + 1,
                market_ts_ms=anomaly.market_ts_ms + 3_000,
                market_time=(start + timedelta(seconds=3)).time().isoformat(
                    timespec="milliseconds"
                ),
                last_price=134.060,
                trade_bonds=10,
                inferred_side="sell",
            )
            engine.on_replay_tick(swept, persist=True)
            self.assertEqual(windfall.inventory, 10)
            self.assertAlmostEqual(windfall.cash, 659.38)
            fill = store.connection.execute(
                """SELECT quantity,price,fill_reason FROM maker_paper_fills
                   WHERE strategy_id='maker_v01_super_windfall'"""
            ).fetchone()
            self.assertEqual(float(fill["quantity"]), 10)
            self.assertEqual(float(fill["price"]), 134.062)
            self.assertEqual(fill["fill_reason"], "super_windfall_buy")
            store.close()

    def test_windfall_v11_candidate_ignores_wide_anomalous_midpoint_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-windfall-v11.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                fill_modes=("priority",),
                super_windfall_enabled=True,
                super_windfall_quantity_bonds=10,
                super_windfall_credit_cny=2_000,
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(
                config, store,
                windfall_policy=WINDFALL_POLICY_V11_CANDIDATE,
            )
            start = datetime(2026, 8, 14, 13, 7, 6, tzinfo=SHANGHAI)
            seed = replace(
                self._replay_tick(
                    start, last=136.200, bid=136.052, ask=136.349,
                    trade_bonds=1_000, inferred_side="buy",
                    previous_close=136.922,
                ),
                bids=(
                    (136.052, 1_000), (134.061, 8_000),
                    (134.060, 15_000), (134.001, 3_000), (134.000, 1_000),
                ),
                asks=((136.349, 1_000), (136.350, 1_000)),
            )
            engine.on_replay_tick(seed, persist=True)
            windfall = engine.accounts["maker_v01_super_windfall"]
            self.assertEqual(windfall.buy_order.limit_price, 134.062)

            reprice = replace(
                seed,
                tick_id=seed.tick_id + 1,
                market_ts_ms=seed.market_ts_ms + 6_000,
                market_time=(start + timedelta(seconds=6)).time().isoformat(
                    timespec="milliseconds"
                ),
                bids=(
                    (134.063, 4_000), (134.061, 8_000),
                    (134.060, 15_000), (134.001, 3_000), (134.000, 1_000),
                ),
                trade_bonds=0,
                inferred_side="none",
            )
            assessment = MarketAssessment(
                reference_price=135.205,
                reference_low=134.063,
                reference_high=136.349,
                reference_source="persistent_inside_market",
                reference_confidence=0.55,
                state="stable",
                state_score=0,
                state_confidence=0.5,
                recent_buy_bonds=0,
                recent_sell_bonds=0,
                midpoint_change=0,
                short_ask_change=0,
                largest_ask_gap=0,
                downside_book_vacuum=False,
                fragile_top_bid=False,
                iron_floor_price=None,
                iron_floor_bonds=0,
                evidence=(),
            )
            with patch.object(
                engine.analyzer, "recent_trade_reference", return_value=136.200,
            ):
                engine._refresh_super_windfall(
                    windfall, reprice, assessment, persist=True,
                )
            self.assertEqual(windfall.buy_order.limit_price, 134.064)

            swept = replace(
                reprice,
                tick_id=reprice.tick_id + 1,
                market_ts_ms=reprice.market_ts_ms + 3_000,
                market_time=(start + timedelta(seconds=9)).time().isoformat(
                    timespec="milliseconds"
                ),
                last_price=134.064,
                trade_bonds=2_000,
                inferred_side="sell",
            )
            engine.on_replay_tick(swept, persist=True)

            self.assertEqual(windfall.inventory, 10)
            assignment = store.connection.execute(
                """SELECT model_id,parent_model_id
                   FROM maker_paper_model_assignments
                   WHERE strategy_id='maker_v01_super_windfall'"""
            ).fetchone()
            self.assertEqual(
                assignment["model_id"], "maker_windfall_v1_1_candidate",
            )
            self.assertEqual(assignment["parent_model_id"], "maker_windfall_v1_0")
            store.close()

    def test_windfall_v20_prepositions_one_thousand_bonds_for_jiangxi_gap(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-windfall-v20-gap.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                fill_modes=(),
                super_windfall_enabled=True,
                super_windfall_model_id="maker_windfall_v2_0_candidate",
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 27, 13, 14, 53, tzinfo=SHANGHAI)
            normal = replace(
                self._replay_tick(
                    start, last=137.646, bid=137.501, ask=137.644,
                    trade_bonds=1_000, inferred_side="buy",
                    previous_close=137.874,
                ),
                bids=((137.501, 3_000), (137.500, 15_000)),
                asks=((137.644, 1_000), (137.645, 2_000)),
            )
            engine.on_replay_tick(normal, persist=True)
            gap = replace(
                normal,
                tick_id=normal.tick_id + 1,
                market_ts_ms=normal.market_ts_ms + 3_000,
                market_time="13:14:56.000",
                trade_bonds=0,
                inferred_side="none",
                bids=(
                    (137.501, 3_000), (137.500, 15_000),
                    (136.499, 6_000), (136.498, 2_000),
                ),
            )
            engine.on_replay_tick(gap, persist=True)

            strategy_id = "maker_v01_windfall_v2_0_candidate"
            windfall = engine.accounts[strategy_id]
            self.assertEqual(windfall.maximum_inventory, 1_000)
            self.assertEqual(windfall.initial_cash, 0)
            self.assertIsNotNone(windfall.buy_order)
            self.assertEqual(windfall.buy_order.limit_price, 136.500)
            self.assertEqual(windfall.buy_order.quantity, 1_000)

            swept = replace(
                gap,
                tick_id=gap.tick_id + 1,
                market_ts_ms=gap.market_ts_ms + 51_000,
                market_time="13:15:47.000",
                last_price=136.499,
                trade_bonds=1_000,
                inferred_side="sell",
                bids=(
                    (136.499, 5_000), (136.498, 2_000),
                    (136.497, 2_000), (136.001, 5_000),
                ),
                asks=((137.500, 7_000), (137.501, 14_000)),
            )
            engine.on_replay_tick(swept, persist=True)
            self.assertEqual(windfall.inventory, 1_000)
            self.assertAlmostEqual(windfall.initial_cash, 136_500)
            self.assertAlmostEqual(windfall.cash, 0)
            fill = store.connection.execute(
                """SELECT quantity,price,fill_reason FROM maker_paper_fills
                   WHERE strategy_id=?""",
                (strategy_id,),
            ).fetchone()
            self.assertEqual(float(fill["quantity"]), 1_000)
            self.assertEqual(float(fill["price"]), 136.500)
            self.assertEqual(fill["fill_reason"], "super_windfall_buy")
            assignment = store.connection.execute(
                """SELECT model_id,parent_model_id
                   FROM maker_paper_model_assignments
                   WHERE strategy_id=?""",
                (strategy_id,),
            ).fetchone()
            self.assertEqual(
                assignment["model_id"], "maker_windfall_v2_0_candidate",
            )
            self.assertEqual(
                assignment["parent_model_id"],
                "maker_windfall_v1_1_candidate",
            )
            store.close()

    def test_windfall_v20_actively_buys_only_an_isolated_large_offer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-windfall-v20-active.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                fill_modes=(),
                super_windfall_enabled=True,
                super_windfall_model_id="maker_windfall_v2_0_candidate",
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            start = datetime(2026, 8, 27, 13, 10, tzinfo=SHANGHAI)
            normal = replace(
                self._replay_tick(
                    start, last=137.500, bid=137.499, ask=137.501,
                    trade_bonds=2_000, inferred_side="buy",
                    previous_close=137.874,
                ),
                asks=((137.501, 2_000), (137.502, 3_000)),
            )
            engine.on_replay_tick(normal, persist=True)

            leaked = replace(
                normal,
                tick_id=normal.tick_id + 1,
                market_ts_ms=normal.market_ts_ms + 3_000,
                market_time="13:10:03.000",
                last_price=136.499,
                trade_bonds=0,
                inferred_side="none",
                bids=((136.300, 5_000), (136.299, 2_000)),
                asks=((136.499, 1_000), (137.500, 5_000)),
            )
            engine.on_replay_tick(leaked, persist=True)
            strategy_id = "maker_v01_windfall_v2_0_candidate"
            windfall = engine.accounts[strategy_id]
            self.assertEqual(windfall.inventory, 1_000)
            fill = store.connection.execute(
                """SELECT quantity,price,fill_reason FROM maker_paper_fills
                   WHERE strategy_id=?""",
                (strategy_id,),
            ).fetchone()
            self.assertEqual(float(fill["quantity"]), 1_000)
            self.assertEqual(float(fill["price"]), 136.499)
            self.assertEqual(fill["fill_reason"], "active_super_windfall_buy")
            store.close()

        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-windfall-v20-dense.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                fill_modes=(),
                super_windfall_enabled=True,
                super_windfall_model_id="maker_windfall_v2_0_candidate",
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            engine.on_replay_tick(normal, persist=True)
            dense = replace(
                leaked,
                asks=((136.499, 2_000), (136.600, 5_000)),
            )
            engine.on_replay_tick(dense, persist=True)
            windfall = engine.accounts[
                "maker_v01_windfall_v2_0_candidate"
            ]
            self.assertEqual(windfall.inventory, 0)
            self.assertIsNone(windfall.buy_order)
            store.close()

        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-windfall-v20-no-anchor.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(
                enabled=True,
                fill_modes=(),
                super_windfall_enabled=True,
                super_windfall_model_id="maker_windfall_v2_0_candidate",
            ))
            store = SQLiteStore(config)
            engine = MakerPaperEngine(config, store)
            no_anchor = replace(
                leaked,
                previous_close=0.0,
                trade_bonds=0.0,
                inferred_side="none",
            )
            engine.on_replay_tick(no_anchor, persist=True)
            windfall = engine.accounts[
                "maker_v01_windfall_v2_0_candidate"
            ]
            self.assertEqual(windfall.inventory, 0)
            self.assertIsNone(windfall.buy_order)
            store.close()

    def test_disabled_live_paper_does_not_create_maker_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = test_config(Path(temp) / "maker-off.sqlite3")
            config = replace(base, maker_paper=MakerPaperConfig(enabled=True))
            store = SQLiteStore(config)
            processor = MarketProcessor(config, store, enable_paper=False)
            processor.process(make_tick(
                config.qmt.bond_code,
                datetime(2026, 8, 13, 10, 0, tzinfo=SHANGHAI),
                last=137.0, bid=136.9, ask=137.0,
            ))
            self.assertEqual(
                store.connection.execute(
                    "SELECT COUNT(*) FROM maker_paper_accounts"
                ).fetchone()[0],
                0,
            )
            store.close()


if __name__ == "__main__":
    unittest.main()
