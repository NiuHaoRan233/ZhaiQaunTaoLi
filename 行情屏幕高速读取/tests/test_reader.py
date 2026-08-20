from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np


MODULE_PATH = Path(__file__).resolve().parents[1] / "reader.py"
SPEC = importlib.util.spec_from_file_location("market_screen_reader", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
reader = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reader
SPEC.loader.exec_module(reader)


class ReaderCoreTests(unittest.TestCase):
    def test_normalized_region_scales_with_frame(self) -> None:
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        region = {"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.4}
        self.assertEqual(reader.normalized_region(region, frame), (20, 20, 100, 40))

    def test_change_fraction_ignores_identical_frame(self) -> None:
        frame = np.full((40, 80, 3), 20, dtype=np.uint8)
        self.assertEqual(reader.change_fraction(frame, frame.copy(), 18), 0.0)

    def test_change_fraction_detects_large_change(self) -> None:
        before = np.zeros((40, 80, 3), dtype=np.uint8)
        after = before.copy()
        after[:, :40] = 255
        self.assertGreater(reader.change_fraction(before, after, 18), 0.45)

    def test_scrolled_rows_only_report_new_edge(self) -> None:
        old = [np.full((8, 20, 3), value, dtype=np.uint8) for value in (0, 40, 80)]
        current = [old[1], old[2], np.full((8, 20, 3), 120, dtype=np.uint8)]
        self.assertEqual(reader.new_row_indices(old, current), [2])

    def test_top_insert_reports_new_top_row(self) -> None:
        old = [np.full((8, 20, 3), value, dtype=np.uint8) for value in (0, 40, 80)]
        current = [np.full((8, 20, 3), 120, dtype=np.uint8), old[0], old[1]]
        self.assertEqual(
            reader.new_row_indices(old, current, newest_at="top"),
            [0],
        )

    def test_select_newest_respects_screen_direction(self) -> None:
        indices = [1, 2, 3, 4]
        self.assertEqual(reader.select_newest(indices, maximum=2, newest_at="top"), [1, 2])
        self.assertEqual(reader.select_newest(indices, maximum=2, newest_at="bottom"), [3, 4])

    def test_table_columns_preserve_cell_boundaries(self) -> None:
        row = np.zeros((10, 100, 3), dtype=np.uint8)
        row[:, :30] = 10
        row[:, 30:60] = 20
        row[:, 60:] = 30
        columns = (
            {"name": "a", "label": "A", "left": 0.0, "right": 0.3},
            {"name": "b", "label": "B", "left": 0.3, "right": 0.6},
            {"name": "c", "label": "C", "left": 0.6, "right": 1.0},
        )
        cells = reader.split_table_cells((row,), columns)
        self.assertEqual([cell.shape[1] for cell in cells], [30, 30, 40])
        self.assertEqual([int(cell.mean()) for cell in cells], [10, 20, 30])


if __name__ == "__main__":
    unittest.main()
