from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is required for frontend placement tests")
class BookOrderPlacementTests(unittest.TestCase):
    def run_javascript(self, expression: str):
        script = (
            "const ui=require('./app.js');"
            "const book={"
            "asks:[5,4,3,2,1].map((level,index)=>({level,price:138.4-index*0.1,quantity:1000})),"
            "bids:[1,2,3,4,5].map((level,index)=>({level,price:137-index*0.1,quantity:1000}))"
            "};"
            f"console.log(JSON.stringify({expression}));"
        )
        completed = subprocess.run(
            [NODE, "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return json.loads(completed.stdout)

    def test_one_tick_improvements_stay_on_best_book_rows(self) -> None:
        result = self.run_javascript(
            "ui.placeBookOrders(book,["
            "{id:1,side:'buy',limit_price:137.001},"
            "{id:2,side:'sell',limit_price:137.999}"
            "])"
        )
        self.assertEqual([item["id"] for item in result["byRow"]["bid:1"]], [1])
        self.assertEqual([item["id"] for item in result["byRow"]["ask:1"]], [2])
        self.assertEqual(result["outside"], [])

    def test_internal_prices_are_assigned_by_price_rank(self) -> None:
        result = self.run_javascript(
            "ui.placeBookOrders(book,["
            "{id:3,side:'buy',limit_price:136.850},"
            "{id:4,side:'sell',limit_price:138.150}"
            "])"
        )
        self.assertEqual([item["id"] for item in result["byRow"]["bid:3"]], [3])
        self.assertEqual([item["id"] for item in result["byRow"]["ask:3"]], [4])
        self.assertEqual(result["outside"], [])

    def test_only_true_out_of_range_orders_use_outside_strip(self) -> None:
        result = self.run_javascript(
            "ui.placeBookOrders(book,["
            "{id:5,side:'buy',limit_price:136.500},"
            "{id:6,side:'sell',limit_price:138.500}"
            "])"
        )
        self.assertEqual([item["id"] for item in result["outside"]], [5, 6])
        self.assertEqual(result["byRow"], {})

    def test_book_chip_shows_direction_price_and_quantity(self) -> None:
        html = self.run_javascript(
            "ui.renderBookRow(book.bids[0],'bid',["
            "{side:'buy',limit_price:137.001,remaining:1000,kind_label:'改善一厘'}"
            "])"
        )
        self.assertIn("order-buy", html)
        self.assertIn(">B<", html)
        self.assertIn("137.001", html)
        self.assertIn("1,000张", html)

    def test_replay_playback_skips_the_lunch_break(self) -> None:
        result = self.run_javascript(
            "(()=>{"
            "const date='2026-08-21';"
            "const lunch=ui.replayLunchWindow(date);"
            "return {lunch,"
            "before:ui.advanceReplayTimestamp(lunch.start-2000,1000,date),"
            "crossed:ui.advanceReplayTimestamp(lunch.start-1000,2000,date),"
            "atStart:ui.advanceReplayTimestamp(lunch.start,1000,date)"
            "};})()"
        )
        lunch = result["lunch"]
        self.assertEqual(result["before"], lunch["start"] - 1000)
        self.assertEqual(result["crossed"], lunch["end"] + 1000)
        self.assertEqual(result["atStart"], lunch["end"] + 1000)

    def test_replay_scrubbing_snaps_over_the_lunch_break(self) -> None:
        result = self.run_javascript(
            "(()=>{"
            "const date='2026-08-21';"
            "const lunch=ui.replayLunchWindow(date);"
            "const middle=(lunch.start+lunch.end)/2;"
            "return {lunch,"
            "forward:ui.normalizeReplayScrubTimestamp(middle,lunch.start-1,date),"
            "backward:ui.normalizeReplayScrubTimestamp(middle,lunch.end+1,date)"
            "};})()"
        )
        lunch = result["lunch"]
        self.assertEqual(result["forward"], lunch["end"])
        self.assertEqual(result["backward"], lunch["start"])

    def test_market_trades_render_from_earlier_to_later(self) -> None:
        result = self.run_javascript(
            "ui.marketTradesAscending(["
            "{id:'latest',ts:3000},{id:'earliest',ts:1000},{id:'middle',ts:2000}"
            "]).map(item=>item.id)"
        )
        self.assertEqual(result, ["earliest", "middle", "latest"])


if __name__ == "__main__":
    unittest.main()
