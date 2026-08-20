from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.audit_priority_sample_out import (
    discover_source_market_dates,
    run_first_eligible_batch,
    select_first_eligible_common_date,
)


INSTRUMENTS = ["132026.SH", "132024.SH"]
UNDERLYING_STOCK_CODES = {
    "132026.SH": "600900.SH",
    "132024.SH": "600362.SH",
}
REQUIRED_CODES = [*INSTRUMENTS, *UNDERLYING_STOCK_CODES.values()]


def full_day_stats() -> dict:
    return {
        "distinct_ticks": 1_000,
        "first_morning_tick": "09:25:00.000",
        "last_afternoon_tick": "15:30:00.000",
    }


def freeze_verification() -> dict:
    return {
        "verified": True,
        "instruments": list(INSTRUMENTS),
        "underlying_stock_codes": dict(UNDERLYING_STOCK_CODES),
        "calibration_dates": ["2026-08-14"],
        "first_eligible_sample_out_date": "2026-08-17",
        "production_comparator_model_id": "maker_priority_v1_1",
        "candidate_model_id": "maker_priority_v1_30_candidate",
        "opening_account": {
            "base_inventory_bonds": 1_000,
            "additional_buying_capacity_bonds": 1_000,
            "normal_maximum_inventory_bonds": 2_000,
        },
    }


class PrioritySampleOutBatchTests(unittest.TestCase):
    def test_selects_first_date_only_when_both_bonds_are_full_day(self) -> None:
        coverage = {
            "2026-08-17": {INSTRUMENTS[0]: full_day_stats()},
            "2026-08-18": {
                INSTRUMENTS[0]: full_day_stats(),
                INSTRUMENTS[1]: {
                    **full_day_stats(),
                    "last_afternoon_tick": "14:30:00.000",
                },
            },
            "2026-08-19": {
                instrument: full_day_stats() for instrument in INSTRUMENTS
            },
        }

        selection = select_first_eligible_common_date(
            coverage,
            instruments=INSTRUMENTS,
            calibration_dates=["2026-08-14"],
            first_eligible_date="2026-08-17",
        )

        self.assertEqual(selection["selected_market_date"], "2026-08-19")
        self.assertEqual(len(selection["rejected_dates"]), 2)
        self.assertIn(
            "missing_instrument:132024.SH",
            selection["rejected_dates"][0]["reasons"],
        )
        self.assertIn(
            "missing_closing_coverage:132024.SH",
            selection["rejected_dates"][1]["reasons"],
        )

    def test_waiting_for_data_runs_no_replays(self) -> None:
        calls: list[tuple] = []

        def replay_runner(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("replay must not run without a common date")

        result = run_first_eligible_batch(
            SimpleNamespace(
                maker_paper=SimpleNamespace(
                    underlying_stock_codes=UNDERLYING_STOCK_CODES,
                ),
            ),
            freeze_verification=freeze_verification(),
            coverage={"2026-08-17": {
                INSTRUMENTS[0]: full_day_stats(),
                UNDERLYING_STOCK_CODES[INSTRUMENTS[0]]: full_day_stats(),
            }},
            replay_runner=replay_runner,
        )

        self.assertEqual(result["status"], "waiting_for_new_data")
        self.assertEqual(result["matrix"], [])
        self.assertEqual(calls, [])

    def test_both_bonds_without_jiangxi_copper_stock_are_not_eligible(self) -> None:
        calls: list[tuple] = []

        def replay_runner(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("replay must wait for each bond's own stock")

        coverage = {
            "2026-08-17": {
                INSTRUMENTS[0]: full_day_stats(),
                INSTRUMENTS[1]: full_day_stats(),
                UNDERLYING_STOCK_CODES[INSTRUMENTS[0]]: full_day_stats(),
            },
        }
        result = run_first_eligible_batch(
            SimpleNamespace(
                maker_paper=SimpleNamespace(
                    underlying_stock_codes=UNDERLYING_STOCK_CODES,
                ),
            ),
            freeze_verification=freeze_verification(),
            coverage=coverage,
            replay_runner=replay_runner,
        )

        self.assertEqual(result["status"], "waiting_for_new_data")
        self.assertEqual(calls, [])
        self.assertIn(
            "missing_instrument:600362.SH",
            result["selection"]["rejected_dates"][0]["reasons"],
        )

    def test_eligible_date_forces_two_bonds_times_two_models(self) -> None:
        calls: list[tuple[str, str, str]] = []

        def replay_runner(
            config, *, market_date: str, bond_code: str, priority_policy,
        ) -> dict:
            model_id = priority_policy.model_id
            calls.append((market_date, bond_code, model_id))
            candidate = model_id == "maker_priority_v1_30_candidate"
            pnl = 125.0 if candidate else 100.0
            inventory = 1_000.0 if candidate else 0.0
            return {
                "accounts": [{
                    "fill_mode": "priority",
                    "model_id": model_id,
                    "initial_inventory": 1_000.0,
                    "maximum_inventory": 2_000.0,
                    "trading_pnl": pnl,
                    "cash": 100_000.0,
                    "inventory": inventory,
                    "customer_base_short_bonds": max(
                        0.0, 1_000.0 - inventory,
                    ),
                    "extra_inventory_bonds": max(
                        0.0, inventory - 1_000.0,
                    ),
                }],
                "fills": [{"model_id": model_id}],
                "orders": [{"model_id": model_id}],
                "source_database_opened_readonly": True,
            }

        result = run_first_eligible_batch(
            SimpleNamespace(
                maker_paper=SimpleNamespace(
                    underlying_stock_codes=UNDERLYING_STOCK_CODES,
                ),
            ),
            freeze_verification=freeze_verification(),
            coverage={
                "2026-08-17": {
                    instrument: full_day_stats()
                    for instrument in REQUIRED_CODES
                },
            },
            replay_runner=replay_runner,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["matrix"]), 4)
        self.assertEqual(len(set(calls)), 4)
        self.assertEqual(
            {item[1] for item in calls}, set(INSTRUMENTS),
        )
        self.assertEqual(
            {item[2] for item in calls},
            {"maker_priority_v1_1", "maker_priority_v1_30_candidate"},
        )
        self.assertEqual(
            [item["delta"]["trading_pnl"]
             for item in result["instrument_comparisons"]],
            [25.0, 25.0],
        )
        self.assertEqual(
            [item["delta"]["customer_base_short_bonds"]
             for item in result["instrument_comparisons"]],
            [-1_000.0, -1_000.0],
        )
        for item in result["matrix"]:
            self.assertEqual(
                item["underlying_stock_code"],
                UNDERLYING_STOCK_CODES[item["bond_code"]],
            )
            self.assertTrue(
                item["replay"]["freeze_verification"][
                    "sample_out_eligible"
                ]
            )

    def test_discovers_source_coverage_using_read_only_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "market.sqlite3"
            connection = sqlite3.connect(database)
            try:
                connection.execute(
                    """CREATE TABLE raw_ticks (
                           market_date TEXT NOT NULL,
                           code TEXT NOT NULL,
                           market_ts_ms INTEGER NOT NULL,
                           market_time TEXT NOT NULL
                       )"""
                )
                rows = []
                for code in INSTRUMENTS:
                    for index in range(100):
                        rows.append((
                            "2026-08-17", code, index,
                            "09:25:00.000" if index == 0
                            else "15:30:00.000",
                        ))
                connection.executemany(
                    "INSERT INTO raw_ticks VALUES (?,?,?,?)", rows,
                )
                connection.commit()
            finally:
                connection.close()

            config = SimpleNamespace(
                storage=SimpleNamespace(database=database),
            )
            coverage = discover_source_market_dates(
                config,
                instruments=INSTRUMENTS,
                first_eligible_date="2026-08-17",
            )

            self.assertEqual(
                set(coverage["2026-08-17"]), set(INSTRUMENTS),
            )
            self.assertEqual(
                coverage["2026-08-17"][INSTRUMENTS[0]]["distinct_ticks"],
                100,
            )


if __name__ == "__main__":
    unittest.main()
