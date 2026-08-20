from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import pandas as pd


MODULE_PATH = Path(__file__).with_name("scan_active_bonds.py")
SPEC = importlib.util.spec_from_file_location("scan_active_bonds", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ClassifyBondTests(unittest.TestCase):
    def test_excludes_reverse_repo(self) -> None:
        self.assertIsNone(MODULE.classify_bond("204001", "GC001"))
        self.assertIsNone(MODULE.classify_bond("131810", "Ｒ-001"))

    def test_classifies_exchangeable_and_convertible(self) -> None:
        self.assertEqual(MODULE.classify_bond("132026", "G三峡EB2"), "可交换债")
        self.assertEqual(MODULE.classify_bond("123112", "万讯转债"), "可转债")

    def test_classifies_rate_and_credit(self) -> None:
        self.assertEqual(MODULE.classify_bond("019831", "26国债05"), "利率债")
        self.assertEqual(MODULE.classify_bond("102335", "特国2601"), "利率债")
        self.assertEqual(MODULE.classify_bond("235916", "26浙江28"), "利率债")
        self.assertEqual(MODULE.classify_bond("188888", "24示例公司债"), "信用债")

    def test_infers_real_estate_and_urban_investment_hints(self) -> None:
        self.assertEqual(MODULE.infer_issuer_hint("24万科01"), "地产线索")
        self.assertEqual(MODULE.infer_issuer_hint("25锡铁投"), "城投线索")
        self.assertEqual(MODULE.infer_issuer_hint("24示例公司债"), "其他信用债")


class MetricTests(unittest.TestCase):
    def row(self, code: str, amount: float, history: list[tuple[str, float]]) -> object:
        return MODULE.BondRow(
            code=code,
            market=1,
            name=f"债{code}",
            category="信用债",
            amount_cny=amount,
            history=history,
        )

    def test_reference_bonds_are_full_score_and_one_tenth_is_very_active(self) -> None:
        jiangtong = self.row("132024", 100.0, [("20260820", 100.0)])
        sanxia = self.row("132026", 50.0, [("20260820", 50.0)])
        tenth = self.row("100001", 5.0, [("20260820", 5.0)])
        jiangtong.activity_history = [("20260820", 40)]
        sanxia.activity_history = [("20260820", 30)]
        tenth.activity_history = [("20260820", 3)]
        rows = [jiangtong, sanxia, tenth]

        MODULE.calculate_metrics(
            rows,
            "2026-08-20",
            5,
            benchmark_codes={"132024.SH", "132026.SH"},
            very_active_ratio=0.1,
        )

        self.assertEqual(jiangtong.score, 100.0)
        self.assertEqual(sanxia.score, 100.0)
        self.assertAlmostEqual(tenth.benchmark_ratio, 0.1)
        self.assertAlmostEqual(tenth.score, 80.0)
        self.assertEqual(tenth.label, "非常活跃")

    def test_today_or_recent_uses_the_more_active_horizon(self) -> None:
        jiangtong = self.row("132024", 100.0, [("20260819", 100.0), ("20260820", 100.0)])
        sanxia = self.row("132026", 50.0, [("20260819", 50.0), ("20260820", 50.0)])
        recently_active = self.row("100001", 1.0, [("20260819", 9.0), ("20260820", 1.0)])
        jiangtong.activity_history = [("20260819", 40), ("20260820", 40)]
        sanxia.activity_history = [("20260819", 30), ("20260820", 30)]
        recently_active.activity_history = [("20260819", 6)]

        MODULE.calculate_metrics(
            [jiangtong, sanxia, recently_active],
            "2026-08-20",
            2,
            benchmark_codes={"132024.SH", "132026.SH"},
            very_active_ratio=0.1,
        )

        self.assertAlmostEqual(recently_active.benchmark_ratio, 0.1)
        self.assertEqual(recently_active.label, "非常活跃")

    def test_one_large_trade_does_not_look_continuously_active(self) -> None:
        jiangtong = self.row("132024", 100.0, [("20260820", 100.0)])
        sanxia = self.row("132026", 50.0, [("20260820", 50.0)])
        one_large_trade = self.row("100001", 1_000_000_000.0, [("20260820", 1_000_000_000.0)])
        jiangtong.activity_history = [("20260820", 40)]
        sanxia.activity_history = [("20260820", 30)]
        one_large_trade.activity_history = [("20260820", 1)]

        MODULE.calculate_metrics(
            [jiangtong, sanxia, one_large_trade],
            "2026-08-20",
            5,
            benchmark_codes={"132024.SH", "132026.SH"},
            very_active_ratio=0.1,
        )

        self.assertAlmostEqual(one_large_trade.benchmark_ratio, 1 / 30)
        self.assertLess(one_large_trade.score, 80.0)
        self.assertEqual(one_large_trade.label, "较活跃")

    def test_qmt_tick_cumulative_changes_count_unique_five_minute_intervals(self) -> None:
        frame = pd.DataFrame(
            {"amount": [0.0, 100.0, 200.0, 200.0, 300.0]},
            index=[
                "20260820093000",
                "20260820093100",
                "20260820093400",
                "20260820093500",
                "20260820094100",
            ],
        )

        amounts, intervals = MODULE._tick_frame_history(frame)

        self.assertEqual(amounts, [("20260820", 300.0)])
        self.assertEqual(intervals, [("20260820", 2)])

    def test_report_candidates_exclude_convertible_rate_and_reference_bonds(self) -> None:
        credit = self.row("188888", 10.0, [])
        convertible = MODULE.BondRow("123112", 0, "万讯转债", "可转债", amount_cny=100.0)
        rate = MODULE.BondRow("019831", 1, "26国债05", "利率债", amount_cny=100.0)
        reference = MODULE.BondRow("132026", 1, "G三峡EB2", "可交换债", amount_cny=50.0)

        result = MODULE.report_rows([credit, convertible, rate, reference], 15)

        self.assertEqual([row.full_code for row in result], ["188888.SH"])


if __name__ == "__main__":
    unittest.main()
