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
            self.assertEqual(config.qmt.watch_codes, ("132024.SH",))
            self.assertEqual(config.qmt.instrument_names["132026.SH"], "G三峡EB2")
            self.assertEqual(config.qmt.instrument_names["132024.SH"], "26江铜EB")
            self.assertEqual(
                config.maker_paper.bond_codes,
                ("132026.SH", "132024.SH"),
            )
            self.assertEqual(config.m0.conversion_price_for(date(2026, 8, 10)), 21.20)
            self.assertEqual(config.paper.price_tick, 0.001)
            self.assertFalse(config.maker_paper.super_windfall_enabled)
            self.assertEqual(config.maker_paper.super_windfall_quantity_bonds, 10)
            self.assertEqual(config.storage.database, Path(temp).resolve() / "data" / "zhaiquant.sqlite3")


if __name__ == "__main__":
    unittest.main()
