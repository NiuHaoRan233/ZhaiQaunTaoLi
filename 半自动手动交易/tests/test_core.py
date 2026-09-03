from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from manual_takeover import (  # noqa: E402
    CommandValidationError,
    DryRunExecutionAdapter,
    ManualTakeoverCommand,
    ManualTakeoverEngine,
    MarketQuote,
)


def command(side: str = "buy") -> ManualTakeoverCommand:
    return ManualTakeoverCommand.from_mapping({
        "bond_code": "132026.SH",
        "side": side,
        "quantity_bonds": 2000,
        "start_price": 100 if side == "buy" else 102,
        "extreme_price": 101 if side == "buy" else 101,
    })


def quote(*, bid: str = "100.000", ask: str = "102.000") -> MarketQuote:
    return MarketQuote.create(
        bond_code="132026.SH",
        market_date="2026-08-25",
        market_time="10:00:00.000",
        market_ts_ms=1_000,
        bid_price=bid,
        ask_price=ask,
    )


class CommandTests(unittest.TestCase):
    def test_quantity_is_explicitly_in_bonds_and_must_be_full_hands(self) -> None:
        with self.assertRaisesRegex(CommandValidationError, "10张"):
            ManualTakeoverCommand.from_mapping({
                "bond_code": "132026.SH", "side": "buy",
                "quantity_bonds": 1001, "start_price": 100, "extreme_price": 101,
            })

    def test_extreme_price_direction_is_strict(self) -> None:
        with self.assertRaisesRegex(CommandValidationError, "严格高于"):
            ManualTakeoverCommand.from_mapping({
                "bond_code": "132026.SH", "side": "buy",
                "quantity_bonds": 1000, "start_price": 100, "extreme_price": 100,
            })
        with self.assertRaisesRegex(CommandValidationError, "严格低于"):
            ManualTakeoverCommand.from_mapping({
                "bond_code": "132026.SH", "side": "sell",
                "quantity_bonds": 1000, "start_price": 100, "extreme_price": 101,
            })


class BuyEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executor = DryRunExecutionAdapter()
        self.engine = ManualTakeoverEngine(self.executor, clock=lambda: 10.0)

    def test_starts_at_exact_user_price_and_does_not_chase_itself(self) -> None:
        task = self.engine.start(command())
        self.assertEqual(task["current_price"], 100.0)
        unchanged = self.engine.on_quote(quote(bid="100.000"))
        assert unchanged is not None
        self.assertEqual(unchanged["current_price"], 100.0)
        self.assertEqual(unchanged["reprice_count"], 0)

    def test_strictly_better_competitor_is_improved_by_one_tick(self) -> None:
        self.engine.start(command())
        changed = self.engine.on_quote(quote(bid="100.500"))
        assert changed is not None
        self.assertEqual(changed["current_price"], 100.501)
        self.assertEqual(changed["reprice_count"], 1)
        self.assertEqual(changed["last_competitor_price"], 100.5)

    def test_reaching_extreme_is_inclusive_and_cancels_without_posting_it(self) -> None:
        self.engine.start(command())
        stopped = self.engine.on_quote(quote(bid="100.999"))
        assert stopped is not None
        self.assertEqual(stopped["status"], "limit_reached")
        self.assertIsNone(stopped["current_order_id"])
        self.assertIsNone(stopped["current_price"])
        alerts = [item for item in self.engine.snapshot()["events"] if item["alert"]]
        self.assertEqual(alerts[0]["event_type"], "limit_reached")

    def test_partial_fill_reprices_only_the_remaining_bonds(self) -> None:
        started = self.engine.start(command())
        self.executor.simulate_fill(started["current_order_id"], 500)
        changed = self.engine.on_quote(quote(bid="100.500"))
        assert changed is not None
        self.assertEqual(changed["filled_bonds"], 500)
        self.assertEqual(changed["remaining_bonds"], 1500)
        report = self.executor.get(changed["current_order_id"])
        self.assertEqual(report.quantity_bonds, 1500)

    def test_price_never_moves_back_when_competitor_withdraws(self) -> None:
        self.engine.start(command())
        self.engine.on_quote(quote(bid="100.500"))
        changed = self.engine.on_quote(quote(bid="100.200"))
        assert changed is not None
        self.assertEqual(changed["current_price"], 100.501)

    def test_old_order_is_confirmed_cancelled_before_replacement_submission(self) -> None:
        actions: list[str] = []

        class RecordingAdapter(DryRunExecutionAdapter):
            def submit_limit(self, **kwargs):
                actions.append("submit")
                return super().submit_limit(**kwargs)

            def cancel(self, order_id):
                actions.append("cancel")
                return super().cancel(order_id)

        engine = ManualTakeoverEngine(RecordingAdapter(), clock=lambda: 10.0)
        engine.start(command())
        engine.on_quote(quote(bid="100.500"))
        self.assertEqual(actions, ["submit", "cancel", "submit"])

    def test_unconfirmed_cancel_blocks_new_task_and_can_be_retried(self) -> None:
        class OneFailureAdapter(DryRunExecutionAdapter):
            fail_once = True

            def cancel(self, order_id):
                if self.fail_once:
                    self.fail_once = False
                    raise RuntimeError("回报超时")
                return super().cancel(order_id)

        engine = ManualTakeoverEngine(OneFailureAdapter(), clock=lambda: 10.0)
        engine.start(command())
        failed = engine.on_quote(quote(bid="100.500"))
        assert failed is not None
        self.assertEqual(failed["status"], "error")
        self.assertTrue(failed["unconfirmed_live_order"])
        with self.assertRaisesRegex(CommandValidationError, "尚未确认撤销"):
            engine.start(command())
        cancelled = engine.cancel(bond_code="132026.SH")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertFalse(cancelled["unconfirmed_live_order"])


class SellEngineTests(unittest.TestCase):
    def test_sell_side_is_symmetric(self) -> None:
        engine = ManualTakeoverEngine(DryRunExecutionAdapter(), clock=lambda: 10.0)
        engine.start(command("sell"))
        changed = engine.on_quote(quote(ask="101.500"))
        assert changed is not None
        self.assertEqual(changed["current_price"], 101.499)
        stopped = engine.on_quote(quote(ask="101.001"))
        assert stopped is not None
        self.assertEqual(stopped["status"], "limit_reached")


if __name__ == "__main__":
    unittest.main()
