from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


class DashboardLayoutTests(unittest.TestCase):
    def test_manual_live_refresh_requests_a_fresh_snapshot(self) -> None:
        script = (ROOT / "app.js").read_text(encoding="utf-8")
        self.assertIn(
            'const freshness = manual && state.mode === "live" ? "&fresh=1" : "";',
            script,
        )
        self.assertIn("${encodeURIComponent(state.modelId)}${freshness}", script)

    def test_manual_takeover_is_a_third_mode_with_two_bond_forms(self) -> None:
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-mode="manual"', html)
        self.assertEqual(html.count('data-manual-bond="'), 2)
        self.assertEqual(html.count('class="manual-form"'), 2)
        self.assertIn('id="manualCancelAll"', html)
        self.assertIn('name="quantity_bonds"', html)
        self.assertIn('name="start_price"', html)
        self.assertIn('name="extreme_price"', html)

    def test_action_streams_keep_sanxia_left_and_jiangtong_right(self) -> None:
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        sanxia = html.index('data-action-bond="132026.SH"')
        jiangtong = html.index('data-action-bond="132024.SH"')
        self.assertLess(sanxia, jiangtong)
        self.assertIn('id="actionStream-132026-SH"', html)
        self.assertIn('id="actionStream-132024-SH"', html)

    def test_order_filter_combines_submits_and_cancels(self) -> None:
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-action-filter="order"', html)
        self.assertNotIn('data-action-filter="submit"', html)
        self.assertNotIn('data-action-filter="cancel"', html)

    def test_closing_fill_renders_its_realized_profit_inline(self) -> None:
        script = (ROOT / "app.js").read_text(encoding="utf-8")
        closing_check = script.index("const pnlText = item.is_closing")
        profit_label = script.index("本笔收益 ${fmtPnl(item.realized_pnl)}元", closing_check)
        action_detail = script.index('<span class="action-detail">', profit_label)
        self.assertLess(closing_check, profit_label)
        self.assertLess(profit_label, action_detail)
        self.assertIn("${pnlText}<code>#", script[action_detail:action_detail + 240])

    def test_fill_badges_use_buy_green_and_sell_red(self) -> None:
        script = (ROOT / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "styles.css").read_text(encoding="utf-8")
        self.assertIn('`event-fill event-fill-${item.side}`', script)
        self.assertIn(".event-fill-buy, .event-complete { color: var(--bid); }", css)
        self.assertIn(".event-fill-sell { color: var(--ask); }", css)

    def test_alignment_spacer_stays_above_asks_not_inside_bid_ask_spread(self) -> None:
        script = (ROOT / "app.js").read_text(encoding="utf-8")
        spacer = script.index('<div class="book-top-align-spacer"')
        asks = script.index("${asks}", spacer)
        spread = script.index('<div class="spread-row"', asks)
        self.assertLess(spacer, asks)
        self.assertLess(asks, spread)
        self.assertNotIn("spread-align-spacer", script)

    def test_book_level_gap_labels_are_right_aligned(self) -> None:
        css = (ROOT / "styles.css").read_text(encoding="utf-8")
        rule_start = css.index(".book-price-gap {")
        rule_end = css.index("}", rule_start)
        rule = css[rule_start:rule_end]
        self.assertIn("justify-content: flex-end", rule)


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

    def test_same_side_price_gaps_use_capped_row_height_tiers(self) -> None:
        result = self.run_javascript(
            "[0.001,0.05,0.199,0.2,0.5,0.999,1,2].map(gap=>"
            "ui.sameSidePriceGapUnits(136,136+gap))"
        )
        self.assertEqual(result, [0, 0.25, 0.25, 0.5, 0.75, 0.75, 1, 1])

    def test_best_bid_ask_spread_keeps_half_to_two_row_heights(self) -> None:
        result = self.run_javascript(
            "[0.001,0.05,0.199,0.2,0.5,0.999,1,2].map(ui.spreadGapUnits)"
        )
        self.assertEqual(result, [0.5, 0.75, 0.75, 1, 1.5, 1.5, 2, 2])

    def test_book_row_exposes_computed_visual_gap(self) -> None:
        html = self.run_javascript(
            "ui.renderBookPriceGap(136,136.75)"
        )
        self.assertIn('data-gap-units="0.75"', html)
        self.assertIn("--price-gap-height:30.75px", html)
        self.assertIn("--price-gap-font-size:17px", html)
        self.assertIn("差 0.750", html)

    def test_gap_label_font_grows_with_available_height(self) -> None:
        result = self.run_javascript(
            "[0.5,0.75,1].map(ui.priceGapFontSizePx)"
        )
        self.assertEqual(result, [14, 17, 20])

    def test_bid_ask_spread_font_grows_on_a_gentler_scale(self) -> None:
        result = self.run_javascript(
            "[0.5,0.75,1,1.5,2].map(ui.spreadFontSizePx)"
        )
        self.assertEqual(result, [12, 14, 16, 20, 24])

    def test_small_book_gap_omits_amount_label(self) -> None:
        html = self.run_javascript(
            "ui.renderBookPriceGap(136,136.1)"
        )
        self.assertIn('data-gap-units="0.25"', html)
        self.assertNotIn("差 0.100", html)

    def test_two_books_share_the_same_spread_center(self) -> None:
        result = self.run_javascript(
            "(()=>{const layouts=ui.synchronizedSpreadLayouts(["
            "{bond:{code:'left'},market:{spread:.05},book:{asks:[{price:136.4},{price:136.3},{price:136.2}]}},"
            "{bond:{code:'right'},market:{spread:1},book:{asks:[{price:137.2},{price:136.6},{price:136}]}}"
            "]);return Object.values(layouts).map(item=>item.askGapUnits+item.alignUnits+item.spreadUnits/2);})()"
        )
        self.assertEqual(result[0], result[1])

    def test_book_chip_shows_direction_price_quantity_and_buy_ceiling(self) -> None:
        html = self.run_javascript(
            "ui.renderBookRow(book.bids[0],'bid',["
            "{side:'buy',limit_price:137.001,remaining:1000,"
            "price_boundary:137.235,price_boundary_label:'最高买价',"
            "kind_label:'改善一厘'}"
            "])"
        )
        self.assertIn("order-buy", html)
        self.assertIn(">B<", html)
        self.assertIn("137.001", html)
        self.assertIn("1,000张", html)
        self.assertIn("上限137.235", html)
        self.assertIn("最高买价 137.235", html)

    def test_book_chip_shows_sell_floor_and_marks_legacy_unknown(self) -> None:
        sell_html = self.run_javascript(
            "ui.renderBookRow(book.asks[0],'ask',["
            "{side:'sell',limit_price:138.399,remaining:1000,"
            "price_boundary:138.205,price_boundary_label:'最低卖价',"
            "kind_label:'高卖'}"
            "])"
        )
        legacy_html = self.run_javascript(
            "ui.renderBookRow(book.bids[0],'bid',["
            "{side:'buy',limit_price:137.001,remaining:1000,"
            "price_boundary:null,kind_label:'历史订单'}"
            "])"
        )
        self.assertIn("order-sell", sell_html)
        self.assertIn(">S<", sell_html)
        self.assertIn("下限138.205", sell_html)
        self.assertIn("最低卖价 138.205", sell_html)
        self.assertIn("上限未记录", legacy_html)

    def test_live_priority_replenishment_uses_follow_label(self) -> None:
        html = self.run_javascript(
            "ui.renderBookRow(book.bids[0],'bid',["
            "{side:'buy',limit_price:136.516,remaining:1000,"
            "price_boundary:136.516,"
            "price_boundary_kind:'live_priority_price',"
            "price_boundary_label:'当前跟随价',kind_label:'动态底仓回补'}"
            "])"
        )
        self.assertIn("跟随136.516", html)
        self.assertIn("当前跟随价 136.516", html)
        self.assertNotIn("上限136.516", html)

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

    def test_intraday_chart_rejects_zero_and_invalid_last_prices(self) -> None:
        result = self.run_javascript(
            "ui.validChartHistory(["
            "{ts:1,last:0},{ts:2,last:null},{ts:3,last:137.9},"
            "{ts:4,last:137.3},{ts:5,last:136},{ts:6,last:136.6}"
            "]).map(item=>item.last)"
        )
        self.assertEqual(result, [137.9, 137.3, 136, 136.6])

    def test_observation_models_are_grouped_and_newest_first(self) -> None:
        result = self.run_javascript(
            "ui.modelDisplayOrder(["
            "{model_id:'windfall',fill_mode:'windfall',model_version:'1.0'},"
            "{model_id:'priority37',fill_mode:'priority',model_version:'1.37-candidate'},"
            "{model_id:'queue17',fill_mode:'queue',model_version:'1.17-candidate'},"
            "{model_id:'priority49',fill_mode:'priority',model_version:'1.49-candidate-r2'},"
            "{model_id:'queue18',fill_mode:'queue',model_version:'1.18-candidate'}"
            "]).map(item=>item.model_id)"
        )
        self.assertEqual(
            result,
            ["priority49", "priority37", "queue18", "queue17", "windfall"],
        )

    def test_new_order_events_use_the_quiet_order_alert(self) -> None:
        result = self.run_javascript(
            "(()=>{"
            "const old={bond_code:'132026.SH',model_id:'m1',event_type:'submit',order_id:1,ts:1000,side:'buy',price:137};"
            "const next={bond_code:'132024.SH',model_id:'m1',event_type:'cancel',order_id:2,ts:2000,side:'sell',price:138};"
            "return ui.detectActionAlert(new Set([ui.actionNotificationKey(old)]),[old,next]);"
            "})()"
        )
        self.assertEqual(result["alertType"], "order")
        self.assertEqual(len(result["newActions"]), 1)
        self.assertEqual(result["newActions"][0]["event_type"], "cancel")

    def test_alert_gain_is_boosted_for_low_system_volume_without_clipping(self) -> None:
        result = self.run_javascript(
            "[ui.alertGain(.052),ui.alertGain(.13),ui.alertGain(1)]"
        )
        self.assertAlmostEqual(result[0], 0.0936)
        self.assertAlmostEqual(result[1], 0.234)
        self.assertEqual(result[2], 0.95)

    def test_fill_alert_wins_when_poll_contains_order_and_fill_events(self) -> None:
        result = self.run_javascript(
            "ui.detectActionAlert(new Set(),["
            "{bond_code:'132026.SH',model_id:'m1',event_type:'complete',order_id:3,ts:3000,side:'buy',price:137},"
            "{bond_code:'132026.SH',model_id:'m1',event_type:'fill',order_id:3,ts:3000,side:'buy',price:137}"
            "])"
        )
        self.assertEqual(result["alertType"], "fill")
        self.assertEqual(len(result["newActions"]), 2)

    def test_non_order_actions_do_not_trigger_sound(self) -> None:
        result = self.run_javascript(
            "ui.detectActionAlert(new Set(),["
            "{bond_code:'132026.SH',model_id:'m1',event_type:'market_trade',order_id:null,ts:4000,side:'buy',price:137}"
            "])"
        )
        self.assertIsNone(result["alertType"])
        self.assertEqual(result["newActions"], [])

    def test_action_stream_data_isolated_by_bond(self) -> None:
        result = self.run_javascript(
            "ui.actionsForBond(["
            "{id:'sanxia',bond_code:'132026.SH'},"
            "{id:'jiangtong',bond_code:'132024.SH'},"
            "{id:'sanxia-2',bond_code:'132026.SH'}"
            "],'132026.SH').map(item=>item.id)"
        )
        self.assertEqual(result, ["sanxia", "sanxia-2"])

    def test_action_reasons_are_rendered_in_chinese(self) -> None:
        result = self.run_javascript(
            "["
            "'maker_reprice','entry_context_changed','passive_buy',"
            "'active_turnover_replaced_passive_sell',"
            "'active_isolated_top_bid_sell_wall_attack_replenishment',"
            "'isolated_top_bid_guarded_base_replenish',"
            "'低价承接','future_reason_code'"
            "].map(ui.actionReasonLabel)"
        )
        self.assertEqual(
            result,
            [
                "做市比价改价",
                "买入条件变化",
                "被动买入成交",
                "主动周转替换被动卖单",
                "卖墙受攻击主动回补",
                "孤岛买一保护回补",
                "低价承接",
                "其他未登记原因",
            ],
        )
        self.assertTrue(all(not any(char.isascii() and char.isalpha() for char in item) for item in result))

    def test_manual_extreme_price_direction_is_validated_for_both_sides(self) -> None:
        result = self.run_javascript(
            "["
            "ui.manualPriceDirectionValid('buy',100,101),"
            "ui.manualPriceDirectionValid('buy',100,100),"
            "ui.manualPriceDirectionValid('sell',102,101),"
            "ui.manualPriceDirectionValid('sell',102,103)"
            "]"
        )
        self.assertEqual(result, [True, False, True, False])

    def test_manual_alarm_only_reports_new_alert_events(self) -> None:
        result = self.run_javascript(
            "ui.detectManualAlerts(new Set([1]),["
            "{event_id:1,alert:true},{event_id:2,alert:false},{event_id:3,alert:true}"
            "]).map(item=>item.event_id)"
        )
        self.assertEqual(result, [3])

    def test_latest_manual_task_is_isolated_by_bond(self) -> None:
        result = self.run_javascript(
            "ui.manualLatestTask(["
            "{task_id:'old',bond_code:'132026.SH',created_at:'2026-08-25T01:00:00Z'},"
            "{task_id:'other',bond_code:'132024.SH',created_at:'2026-08-25T03:00:00Z'},"
            "{task_id:'new',bond_code:'132026.SH',created_at:'2026-08-25T02:00:00Z'}"
            "],'132026.SH').task_id"
        )
        self.assertEqual(result, "new")


if __name__ == "__main__":
    unittest.main()
