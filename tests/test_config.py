from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from zhaiquant.config import load_config


class ConfigTests(unittest.TestCase):
    def test_requested_first_position_models_are_enabled_in_default_matrix(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            source = (repository / "config.example.toml").read_text(
                encoding="utf-8"
            )
            target = Path(temp) / "config.toml"
            target.write_text(source, encoding="utf-8")
            config = load_config(target)
            self.assertEqual(
                config.maker_paper.realtime_comparison_model_ids,
                (
                    "maker_priority_v1_37_candidate",
                    "maker_priority_v1_50_candidate",
                    "maker_priority_v2_52_candidate_r2",
                    "maker_priority_v2_63_candidate",
                    "maker_priority_v2_70_candidate_r2",
                    "maker_priority_v2_71_candidate",
                    "maker_shared_1000_v0_1_candidate",
                    "maker_shared_1000_v0_13_candidate",
                    "maker_shared_1000_v0_16_candidate_r2",
                    "maker_dadao_v0_1_candidate_r2",
                ),
            )

    def test_retired_first_position_v143_remains_a_supported_model(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            source = (repository / "config.example.toml").read_text(
                encoding="utf-8"
            )
            source = source.replace(
                '"maker_priority_v1_50_candidate",',
                '"maker_priority_v1_43_candidate",',
            )
            target = Path(temp) / "config.toml"
            target.write_text(source, encoding="utf-8")
            config = load_config(target)
            self.assertIn(
                "maker_priority_v1_43_candidate",
                config.maker_paper.realtime_comparison_model_ids,
            )

    def test_first_position_v149_is_supported_but_not_enabled_by_default(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            source = (repository / "config.example.toml").read_text(
                encoding="utf-8"
            )
            self.assertNotIn('"maker_priority_v1_49_candidate",', source)
            source = source.replace(
                '"maker_priority_v1_50_candidate",',
                '"maker_priority_v1_50_candidate",\n'
                '  "maker_priority_v1_49_candidate",',
            )
            target = Path(temp) / "config.toml"
            target.write_text(source, encoding="utf-8")
            config = load_config(target)
            self.assertIn(
                "maker_priority_v1_49_candidate",
                config.maker_paper.realtime_comparison_model_ids,
            )

    def test_first_position_v149_r2_is_supported_but_not_enabled_by_default(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            source = (repository / "config.example.toml").read_text(
                encoding="utf-8"
            )
            self.assertNotIn('"maker_priority_v1_49_candidate_r2",', source)
            source = source.replace(
                '"maker_priority_v1_50_candidate",',
                '"maker_priority_v1_50_candidate",\n'
                '  "maker_priority_v1_49_candidate_r2",',
            )
            target = Path(temp) / "config.toml"
            target.write_text(source, encoding="utf-8")
            config = load_config(target)
            self.assertIn(
                "maker_priority_v1_49_candidate_r2",
                config.maker_paper.realtime_comparison_model_ids,
            )

    def test_retired_first_position_v21_remains_supported(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            source = (repository / "config.example.toml").read_text(
                encoding="utf-8"
            )
            self.assertNotIn('"maker_priority_v2_1_candidate",', source)
            source = source.replace(
                '"maker_priority_v2_52_candidate_r2",',
                '"maker_priority_v2_1_candidate",',
            )
            target = Path(temp) / "config.toml"
            target.write_text(source, encoding="utf-8")
            config = load_config(target)
            self.assertIn(
                "maker_priority_v2_1_candidate",
                config.maker_paper.realtime_comparison_model_ids,
            )

    def test_first_position_v150_is_enabled_by_default(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            source = (repository / "config.example.toml").read_text(
                encoding="utf-8"
            )
            self.assertIn(
                '"maker_priority_v1_50_candidate",',
                source,
            )
            target = Path(temp) / "config.toml"
            target.write_text(source, encoding="utf-8")
            config = load_config(target)
            self.assertIn(
                "maker_priority_v1_50_candidate",
                config.maker_paper.realtime_comparison_model_ids,
            )

    def test_retired_first_position_v22_remains_supported(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            source = (repository / "config.example.toml").read_text(
                encoding="utf-8"
            )
            self.assertNotIn('"maker_priority_v2_2_candidate",', source)
            source = source.replace(
                '"maker_priority_v2_52_candidate_r2",',
                '"maker_priority_v2_52_candidate_r2",\n'
                '  "maker_priority_v2_2_candidate",',
            )
            target = Path(temp) / "config.toml"
            target.write_text(source, encoding="utf-8")
            config = load_config(target)
            self.assertIn(
                "maker_priority_v2_2_candidate",
                config.maker_paper.realtime_comparison_model_ids,
            )

    def test_retired_first_position_v23_remains_supported(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            source = (repository / "config.example.toml").read_text(
                encoding="utf-8"
            )
            self.assertNotIn('"maker_priority_v2_3_candidate",', source)
            source = source.replace(
                '"maker_priority_v2_52_candidate_r2",',
                '"maker_priority_v2_52_candidate_r2",\n'
                '  "maker_priority_v2_3_candidate",',
            )
            target = Path(temp) / "config.toml"
            target.write_text(source, encoding="utf-8")
            config = load_config(target)
            self.assertIn(
                "maker_priority_v2_3_candidate",
                config.maker_paper.realtime_comparison_model_ids,
            )

    def test_first_position_v25_r2_is_supported_but_not_enabled_by_default(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            source = (repository / "config.example.toml").read_text(
                encoding="utf-8"
            )
            self.assertNotIn('"maker_priority_v2_5_candidate_r2",', source)
            source = source.replace(
                '"maker_priority_v2_52_candidate_r2",',
                '"maker_priority_v2_52_candidate_r2",\n'
                '  "maker_priority_v2_5_candidate_r2",',
            )
            target = Path(temp) / "config.toml"
            target.write_text(source, encoding="utf-8")
            config = load_config(target)
            self.assertIn(
                "maker_priority_v2_5_candidate_r2",
                config.maker_paper.realtime_comparison_model_ids,
            )

    def test_first_position_v251_r3_is_supported_but_not_enabled_by_default(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            source = (repository / "config.example.toml").read_text(
                encoding="utf-8"
            )
            self.assertNotIn('"maker_priority_v2_51_candidate_r3",', source)
            source = source.replace(
                '"maker_priority_v2_52_candidate_r2",',
                '"maker_priority_v2_52_candidate_r2",\n'
                '  "maker_priority_v2_51_candidate_r3",',
            )
            target = Path(temp) / "config.toml"
            target.write_text(source, encoding="utf-8")
            config = load_config(target)
            self.assertIn(
                "maker_priority_v2_51_candidate_r3",
                config.maker_paper.realtime_comparison_model_ids,
            )

    def test_first_position_v252_r2_is_enabled_by_default(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            source = (repository / "config.example.toml").read_text(
                encoding="utf-8"
            )
            self.assertIn('"maker_priority_v2_52_candidate_r2",', source)
            target = Path(temp) / "config.toml"
            target.write_text(source, encoding="utf-8")
            config = load_config(target)
            self.assertIn(
                "maker_priority_v2_52_candidate_r2",
                config.maker_paper.realtime_comparison_model_ids,
            )

    def test_first_position_v26_r3_is_supported_but_not_enabled_by_default(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            source = (repository / "config.example.toml").read_text(
                encoding="utf-8"
            )
            self.assertNotIn('"maker_priority_v2_6_candidate_r3",', source)
            source = source.replace(
                '"maker_priority_v2_63_candidate",',
                '"maker_priority_v2_63_candidate",\n'
                '  "maker_priority_v2_6_candidate_r3",',
            )
            target = Path(temp) / "config.toml"
            target.write_text(source, encoding="utf-8")
            config = load_config(target)
            self.assertIn(
                "maker_priority_v2_6_candidate_r3",
                config.maker_paper.realtime_comparison_model_ids,
            )

    def test_first_position_v263_is_enabled_by_default(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            source = (repository / "config.example.toml").read_text(
                encoding="utf-8"
            )
            self.assertIn('"maker_priority_v2_63_candidate",', source)
            target = Path(temp) / "config.toml"
            target.write_text(source, encoding="utf-8")
            config = load_config(target)
            self.assertIn(
                "maker_priority_v2_63_candidate",
                config.maker_paper.realtime_comparison_model_ids,
            )

    def test_first_position_v264_is_supported_but_not_enabled_by_default(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            source = (repository / "config.example.toml").read_text(
                encoding="utf-8"
            )
            self.assertNotIn('"maker_priority_v2_64_candidate",', source)
            source = source.replace(
                '"maker_priority_v2_63_candidate",',
                '"maker_priority_v2_63_candidate",\n'
                '  "maker_priority_v2_64_candidate",',
            )
            target = Path(temp) / "config.toml"
            target.write_text(source, encoding="utf-8")
            config = load_config(target)
            self.assertIn(
                "maker_priority_v2_64_candidate",
                config.maker_paper.realtime_comparison_model_ids,
            )

    def test_first_position_v264_r2_is_supported_but_not_enabled_by_default(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            source = (repository / "config.example.toml").read_text(
                encoding="utf-8"
            )
            self.assertNotIn(
                '"maker_priority_v2_64_candidate_r2",', source,
            )
            source = source.replace(
                '"maker_priority_v2_63_candidate",',
                '"maker_priority_v2_63_candidate",\n'
                '  "maker_priority_v2_64_candidate_r2",',
            )
            target = Path(temp) / "config.toml"
            target.write_text(source, encoding="utf-8")
            config = load_config(target)
            self.assertIn(
                "maker_priority_v2_64_candidate_r2",
                config.maker_paper.realtime_comparison_model_ids,
            )

    def test_first_position_v265_is_supported_but_not_enabled_by_default(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp:
            source = (repository / "config.example.toml").read_text(encoding="utf-8")
            self.assertNotIn('"maker_priority_v2_65_candidate",', source)
            source = source.replace('"maker_priority_v2_63_candidate",',
                '"maker_priority_v2_63_candidate",\n  "maker_priority_v2_65_candidate",')
            target = Path(temp) / "config.toml"
            target.write_text(source, encoding="utf-8")
            self.assertIn("maker_priority_v2_65_candidate",
                load_config(target).maker_paper.realtime_comparison_model_ids)

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
                    "maker_priority_v1_50_candidate",
                    "maker_priority_v2_52_candidate_r2",
                    "maker_priority_v2_63_candidate",
                    "maker_priority_v2_70_candidate_r2",
                    "maker_priority_v2_71_candidate",
                    "maker_shared_1000_v0_1_candidate",
                    "maker_shared_1000_v0_13_candidate",
                    "maker_shared_1000_v0_16_candidate_r2",
                    "maker_dadao_v0_1_candidate_r2",
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
            self.assertFalse(config.maker_paper.super_windfall_enabled)
            self.assertEqual(
                config.maker_paper.super_windfall_model_id,
                "maker_windfall_v2_0_candidate",
            )
            self.assertEqual(config.maker_paper.super_windfall_quantity_bonds, 10)
            self.assertEqual(config.storage.database, Path(temp).resolve() / "data" / "zhaiquant.sqlite3")


if __name__ == "__main__":
    unittest.main()
