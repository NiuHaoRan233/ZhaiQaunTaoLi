from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from zhaiquant.config import load_config


class ConfigTests(unittest.TestCase):
    def test_example_config_loads_and_resolves_database(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "config.toml"
            target.write_bytes((repository / "config.example.toml").read_bytes())
            config = load_config(target)
            self.assertEqual(config.qmt.port, 58611)
            self.assertEqual(
                config.qmt.watch_codes,
                ("132024.SH", "600362.SH"),
            )
            self.assertEqual(config.qmt.instrument_names["132026.SH"], "G三峡EB2")
            self.assertEqual(config.qmt.instrument_names["132024.SH"], "26江铜EB")
            self.assertEqual(
                config.maker_paper.bond_codes,
                ("132026.SH", "132024.SH"),
            )
            self.assertEqual(
                config.maker_paper.underlying_stock_codes,
                {
                    "132026.SH": "600900.SH",
                    "132024.SH": "600362.SH",
                },
            )
            self.assertEqual(
                config.maker_paper.additional_buying_capacity_bonds,
                1_000,
            )
            self.assertEqual(
                config.maker_paper.fill_modes,
                (),
            )
            self.assertEqual(
                config.maker_paper.realtime_comparison_model_ids,
                (
                    "maker_priority_v1_37_candidate",
                    "maker_priority_v1_43_candidate",
                    "maker_queue_v1_17_candidate",
                    "maker_queue_v1_18_candidate",
                ),
            )
            self.assertEqual(
                config.maker_paper.latest_entry,
                "15:29:59.999",
            )
            self.assertEqual(
                config.maker_paper.earliest_entry,
                "09:20:00.000",
            )
            self.assertEqual(
                config.maker_paper.opening_caution_effective_date,
                "2026-08-21",
            )
            self.assertEqual(
                config.maker_paper.opening_caution_end,
                "09:30:00.000",
            )
            self.assertEqual(
                config.maker_paper.opening_caution_minimum_edge,
                1.00,
            )
            self.assertEqual(config.m0.conversion_price_for(date(2026, 8, 10)), 21.20)
            self.assertEqual(config.paper.price_tick, 0.001)
            self.assertTrue(config.maker_paper.super_windfall_enabled)
            self.assertEqual(config.maker_paper.super_windfall_quantity_bonds, 10)
            self.assertEqual(config.storage.database, Path(temp).resolve() / "data" / "zhaiquant.sqlite3")


if __name__ == "__main__":
    unittest.main()
