from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from zhaiquant.tdx_tape import (
    OCRToken,
    TdxOrderEvent,
    _normalize_panel_tokens,
    _order_glyph_repair_requires_review,
    _order_screenshot_layout,
    _mark_price_outliers_for_review,
    _sort_chronologically,
    _trade_screenshot_layout,
    apply_manual_order_reviews,
    parse_order_panel,
    parse_trade_panel,
)


class TdxTapeParserTests(unittest.TestCase):
    def test_verified_2026_08_13_screenshot_layouts_are_explicit(self) -> None:
        trade = _trade_screenshot_layout(1689, 1015)
        order = _order_screenshot_layout(2560, 1392)

        self.assertIsNotNone(trade)
        self.assertEqual((trade.panels, trade.top, trade.bottom), (5, 55, 1000))
        self.assertIsNotNone(order)
        self.assertEqual((order.panels, order.top, order.bottom), (10, 55, 1377))
        self.assertEqual(order.canonical_panel_width, 241.0)
        self.assertIsNone(_trade_screenshot_layout(1689, 900))
        self.assertIsNone(_order_screenshot_layout(2560, 1200))

    def test_verified_2026_08_18_four_panel_trade_layout_is_explicit(self) -> None:
        trade = _trade_screenshot_layout(1389, 1063)

        self.assertIsNotNone(trade)
        self.assertEqual((trade.panels, trade.top, trade.bottom), (4, 55, 1048))
        self.assertIsNone(_trade_screenshot_layout(1389, 900))

    def test_verified_ultrawide_trade_layout_normalizes_eight_panels(self) -> None:
        trade = _trade_screenshot_layout(2560, 1392)

        self.assertIsNotNone(trade)
        self.assertEqual((trade.panels, trade.top, trade.bottom), (8, 55, 1377))
        self.assertEqual(trade.canonical_panel_width, 340.0)

    def test_page_overlap_survivors_are_stably_sorted_by_market_time(self) -> None:
        def event(market_time: str, row: int) -> TdxOrderEvent:
            return TdxOrderEvent(
                market_date="2026-08-18", code="132026.SH",
                market_time=market_time, price=135.0, hands=100,
                event_type="B", source_page="order.png", page_sequence=1,
                panel=1, row=row, time_inherited=False, ocr_confidence=1.0,
                event_confidence=1.0, event_source="ocr_text",
                review_required=False,
            )

        ordered = _sort_chronologically([
            event("13:03:13", 1),
            event("11:22:18", 2),
            event("13:03:13", 3),
        ])

        self.assertEqual(
            [(item.market_time, item.row) for item in ordered],
            [("11:22:18", 2), ("13:03:13", 1), ("13:03:13", 3)],
        )

    def test_extreme_high_confidence_price_is_still_sent_to_review(self) -> None:
        def event(price: float, row: int) -> TdxOrderEvent:
            return TdxOrderEvent(
                market_date="2026-08-18", code="132024.SH",
                market_time="10:00:00", price=price, hands=100,
                event_type="S", source_page="order.png", page_sequence=1,
                panel=1, row=row, time_inherited=False, ocr_confidence=0.99,
                event_confidence=1.0, event_source="ocr_text",
                review_required=False,
            )

        reviewed, flagged = _mark_price_outliers_for_review([
            event(136.0, 1), event(136.1, 2), event(316.9, 3),
        ])

        self.assertEqual(flagged, 1)
        self.assertFalse(reviewed[0].review_required)
        self.assertTrue(reviewed[2].review_required)

    def test_ultrawide_order_tokens_normalize_to_verified_panel_columns(self) -> None:
        tokens = _normalize_panel_tokens(
            [OCRToken(194.4, 10, "100", 0.99)],
            actual_width=256,
            canonical_width=241.0,
        )

        self.assertAlmostEqual(tokens[0].x, 183.01, places=2)

    def test_manual_order_review_preserves_raw_identity_and_clears_review(self) -> None:
        event = TdxOrderEvent(
            market_date="2026-08-14", code="132026.SH",
            market_time="13:32:45", price=135.990, hands=0,
            event_type="SC", source_page="order_08.png", page_sequence=8,
            panel=1, row=31, time_inherited=False, ocr_confidence=0.67,
            event_confidence=0.95, event_source="ocr_text",
            review_required=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            reviews = Path(temporary) / "reviews.csv"
            reviews.write_text(
                "source_page,page_sequence,panel,row,market_time,"
                "corrected_price,corrected_hands,corrected_event_type,review_note\n"
                "order_08.png,8,1,31,13:32:45,135.990,100,SC,verified\n",
                encoding="utf-8",
            )
            corrected, applied = apply_manual_order_reviews([event], reviews)

        self.assertEqual(applied, 1)
        self.assertEqual(corrected[0].hands, 100)
        self.assertEqual(corrected[0].event_type, "SC")
        self.assertEqual(corrected[0].event_source, "manual_review")
        self.assertFalse(corrected[0].review_required)

    def test_manual_order_review_can_correct_an_invalid_ocr_time(self) -> None:
        event = TdxOrderEvent(
            market_date="2026-08-18", code="132024.SH",
            market_time="00:91:60", price=128.0, hands=2000,
            event_type="B", source_page="order_01.png", page_sequence=1,
            panel=1, row=1, time_inherited=False, ocr_confidence=0.74,
            event_confidence=1.0, event_source="ocr_text",
            review_required=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            reviews = Path(temporary) / "reviews.csv"
            reviews.write_text(
                "source_page,page_sequence,panel,row,market_time,"
                "corrected_market_time,corrected_price,corrected_hands,"
                "corrected_event_type,review_note\n"
                "order_01.png,1,1,1,00:91:60,09:15:00,128.000,2000,B,"
                "verified from source image\n",
                encoding="utf-8",
            )
            corrected, applied = apply_manual_order_reviews([event], reviews)

        self.assertEqual(applied, 1)
        self.assertEqual(corrected[0].market_time, "09:15:00")
        self.assertFalse(corrected[0].review_required)

    def test_parse_order_panel_keeps_add_cancel_and_same_second_rows(self) -> None:
        tokens = [
            OCRToken(3, 2, "13:22:14135.799", 0.98),
            OCRToken(183, 2, "100", 0.99),
            OCRToken(230, 2, "S", 0.97, "S", 0.99),
            OCRToken(87, 19, "135.601", 0.99),
            OCRToken(183, 19, "400", 0.99),
            OCRToken(220, 19, "BC", 0.98, "B", 0.99),
        ]

        events, last_time = parse_order_panel(
            tokens,
            market_date="2026-08-14",
            code="132026.SH",
            source_page="order_07.png",
            page_sequence=7,
            panel=1,
        )

        self.assertEqual(last_time, "13:22:14")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].identity(), (
            "13:22:14", 135.799, 100, "S",
        ))
        self.assertEqual(events[0].action, "add")
        self.assertEqual(events[0].side, "sell")
        self.assertEqual(events[1].market_time, "13:22:14")
        self.assertTrue(events[1].time_inherited)
        self.assertEqual(events[1].event_type, "BC")
        self.assertEqual(events[1].action, "cancel")
        self.assertEqual(events[1].side, "buy")
        self.assertFalse(events[1].review_required)

    def test_order_event_is_not_guessed_from_price_colour(self) -> None:
        tokens = [
            OCRToken(3, 2, "13:22:14", 0.99),
            OCRToken(87, 2, "135.799", 0.99, "S", 1.0),
            OCRToken(183, 2, "100", 0.99),
        ]

        events, _ = parse_order_panel(
            tokens,
            market_date="2026-08-14",
            code="132026.SH",
            source_page="order_07.png",
            page_sequence=7,
            panel=1,
        )

        self.assertIsNone(events[0].event_type)
        self.assertTrue(events[0].review_required)

    def test_invalid_ocr_time_and_inherited_rows_require_review(self) -> None:
        tokens = [
            OCRToken(3, 2, "00:91:60128.000", 0.99),
            OCRToken(183, 2, "2000", 0.99),
            OCRToken(230, 2, "B", 0.99, "B", 1.0),
            OCRToken(87, 19, "131.000", 0.99),
            OCRToken(183, 19, "2000", 0.99),
            OCRToken(230, 19, "B", 0.99, "B", 1.0),
        ]

        events, last_time = parse_order_panel(
            tokens,
            market_date="2026-08-18",
            code="132024.SH",
            source_page="order_01.png",
            page_sequence=1,
            panel=1,
        )

        self.assertEqual(last_time, "00:91:60")
        self.assertEqual(len(events), 2)
        self.assertTrue(events[0].review_required)
        self.assertTrue(events[1].time_inherited)
        self.assertTrue(events[1].review_required)

    def test_invalid_inherited_time_cannot_be_cleared_by_event_repair(self) -> None:
        event = TdxOrderEvent(
            market_date="2026-08-20", code="132024.SH",
            market_time="00:91:60", price=138.0, hands=200,
            event_type=None, source_page="order_01.png", page_sequence=1,
            panel=1, row=8, time_inherited=True, ocr_confidence=0.99,
            event_confidence=0.0, event_source="ocr_text",
            review_required=True,
        )

        self.assertTrue(_order_glyph_repair_requires_review(event))

    def test_parse_trade_panel_keeps_true_side_and_inherits_repeated_time(self) -> None:
        tokens = [
            OCRToken(3, 37, "09:31:51", 0.98),
            OCRToken(92, 37, "137.479", 0.99, "B", 0.97),
            OCRToken(179, 37, "100B", 0.96, "B", 0.98),
            OCRToken(249, 37, "100", 0.99),
            OCRToken(309, 37, "100", 0.99),
            OCRToken(49, 54, "2", 0.99),
            OCRToken(92, 54, "136.900", 0.99, "S", 0.96),
            OCRToken(186, 54, "38S", 0.94, "S", 0.98),
            OCRToken(317, 54, "38", 0.99),
        ]

        trades, last_time = parse_trade_panel(
            tokens,
            market_date="2026-08-14",
            code="132026.SH",
            source_page="page_01.png",
            page_sequence=1,
            panel=1,
        )

        self.assertEqual(last_time, "09:31:51")
        self.assertEqual(len(trades), 2)
        self.assertEqual(trades[0].identity(), (
            "09:31:51", 137.479, 100, "B", 100, 100,
        ))
        self.assertEqual(trades[1].market_time, "09:31:51")
        self.assertTrue(trades[1].time_inherited)
        self.assertEqual(trades[1].side, "S")
        self.assertEqual(trades[1].sell_order, 38)

    def test_uncertain_side_is_not_silently_substituted(self) -> None:
        tokens = [
            OCRToken(3, 37, "10:00:00", 0.99),
            OCRToken(92, 37, "136.500", 0.99),
            OCRToken(179, 37, "100", 0.99),
        ]

        trades, _ = parse_trade_panel(
            tokens,
            market_date="2026-08-14",
            code="132026.SH",
            source_page="page_01.png",
            page_sequence=1,
            panel=1,
        )

        self.assertEqual(trades[0].side, None)
        self.assertTrue(trades[0].review_required)


if __name__ == "__main__":
    unittest.main()
