from __future__ import annotations

import csv
import json
import re
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable


_TIME_RE = re.compile(r"(?<!\d)(\d{2}:\d{2}:\d{2})(?!\d)")
_PRICE_RE = re.compile(r"(?<!\d)(\d{2,3}\.\d{3})(?!\d)")
_COMPACT_TIME_RE = re.compile(r"(\d{2}:\d{2}:\d{2})")
_COMPACT_PRICE_RE = re.compile(r"(\d{2,3}\.\d{3})")
_INTEGER_RE = re.compile(r"\d+")
_SEQUENCE_RE = re.compile(r"_(\d{2})\.png$", re.IGNORECASE)
_ORDER_EVENT_RE = re.compile(r"(?:BC|SC|B|S)")


@dataclass(frozen=True)
class OCRToken:
    x: float
    y: float
    text: str
    confidence: float
    side_hint: str | None = None
    side_confidence: float = 0.0


@dataclass(frozen=True)
class ScreenshotLayout:
    panels: int
    top: int
    bottom: int
    canonical_panel_width: float | None = None


def _trade_screenshot_layout(width: int, height: int) -> ScreenshotLayout | None:
    if 2_550 <= width <= 2_570 and 1_380 <= height <= 1_405:
        # The ultrawide full-screen trade-detail view uses eight 320-pixel
        # panels.  Normalize those columns to the verified 340-pixel parser
        # geometry; later panels may legitimately be blank on quiet days.
        return ScreenshotLayout(8, 55, height - 15, 340.0)
    if 1_380 <= width <= 1_400 and 1_050 <= height <= 1_075:
        # The 2026-08-18 manually captured trade-detail window shows four
        # equal-width panels.  Column geometry is unchanged from the verified
        # five-panel layout; only the number of visible panels differs.
        return ScreenshotLayout(4, 55, min(height - 15, 1_060))
    if not 1_680 <= width <= 1_700:
        return None
    if 1_060 <= height <= 1_090:
        return ScreenshotLayout(5, 55, min(height - 15, 1_060))
    if 1_000 <= height <= 1_025:
        return ScreenshotLayout(5, 55, height - 15)
    return None


def _order_screenshot_layout(width: int, height: int) -> ScreenshotLayout | None:
    if 1_680 <= width <= 1_700 and 1_060 <= height <= 1_090:
        return ScreenshotLayout(7, 55, min(height - 15, 1_060))
    if 2_550 <= width <= 2_570 and 1_380 <= height <= 1_405:
        # The 2026-08-13 ultrawide capture shows ten panels.  OCR token x
        # coordinates are normalized back to the verified 241-pixel panel
        # coordinate system before the common row parser is used.
        return ScreenshotLayout(10, 55, height - 15, 241.0)
    return None


def _normalize_panel_tokens(
    tokens: Iterable[OCRToken], *, actual_width: int,
    canonical_width: float | None,
) -> list[OCRToken]:
    if canonical_width is None or actual_width <= 0:
        return list(tokens)
    scale = canonical_width / actual_width
    return [replace(token, x=token.x * scale) for token in tokens]


@dataclass(frozen=True)
class TdxTrade:
    market_date: str
    code: str
    market_time: str
    price: float
    hands: int | None
    side: str | None
    buy_order: int | None
    sell_order: int | None
    source_page: str
    page_sequence: int
    panel: int
    row: int
    time_inherited: bool
    ocr_confidence: float
    side_confidence: float
    review_required: bool

    def identity(self) -> tuple[Any, ...]:
        return (
            self.market_time,
            self.price,
            self.hands,
            self.side,
            self.buy_order,
            self.sell_order,
        )


@dataclass(frozen=True)
class TdxOrderEvent:
    market_date: str
    code: str
    market_time: str
    price: float
    hands: int | None
    event_type: str | None
    source_page: str
    page_sequence: int
    panel: int
    row: int
    time_inherited: bool
    ocr_confidence: float
    event_confidence: float
    event_source: str
    review_required: bool

    def identity(self) -> tuple[Any, ...]:
        return (
            self.market_time,
            self.price,
            self.hands,
            self.event_type,
        )

    @property
    def side(self) -> str | None:
        if self.event_type in {"B", "BC"}:
            return "buy"
        if self.event_type in {"S", "SC"}:
            return "sell"
        return None

    @property
    def action(self) -> str | None:
        if self.event_type in {"B", "S"}:
            return "add"
        if self.event_type in {"BC", "SC"}:
            return "cancel"
        return None


def _field_text(tokens: Iterable[OCRToken], left: float, right: float) -> str:
    selected = [token for token in tokens if left <= token.x < right]
    return "".join(token.text for token in sorted(selected, key=lambda item: item.x))


def _field_tokens(
    tokens: Iterable[OCRToken], left: float, right: float,
) -> list[OCRToken]:
    return [token for token in tokens if left <= token.x < right]


def _first_integer(text: str) -> int | None:
    match = _INTEGER_RE.search(text)
    return int(match.group()) if match else None


def _is_valid_market_time(value: str | None) -> bool:
    if value is None or not re.fullmatch(r"\d{2}:\d{2}:\d{2}", value):
        return False
    hour, minute, second = (int(part) for part in value.split(":"))
    return 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59


def _cluster_rows(
    tokens: Iterable[OCRToken], tolerance: float = 5.5, minimum_y: float = 28.0,
) -> list[list[OCRToken]]:
    rows: list[list[OCRToken]] = []
    centers: list[float] = []
    for token in sorted(tokens, key=lambda item: (item.y, item.x)):
        if token.y < minimum_y:
            continue
        if not rows or abs(token.y - centers[-1]) > tolerance:
            rows.append([token])
            centers.append(token.y)
            continue
        rows[-1].append(token)
        centers[-1] = sum(item.y for item in rows[-1]) / len(rows[-1])
    return rows


def parse_order_panel(
    tokens: Iterable[OCRToken], *, market_date: str, code: str,
    source_page: str, page_sequence: int, panel: int,
    inherited_time: str | None = None,
) -> tuple[list[TdxOrderEvent], str | None]:
    """Parse one normalized 通达信逐笔委托 screenshot column.

    `B`/`S` are new buy/sell orders and `BC`/`SC` are their cancellations.
    Uncertain OCR is retained with `review_required=True`; cancellation events
    are never guessed from colour alone because that would corrupt queue decay.
    """

    events: list[TdxOrderEvent] = []
    last_time = inherited_time
    for row_number, row_tokens in enumerate(
        _cluster_rows(tokens, minimum_y=-1.0), start=1,
    ):
        ordered = sorted(row_tokens, key=lambda item: item.x)
        compact = "".join(token.text for token in ordered)
        price_match = _COMPACT_PRICE_RE.search(compact)
        if not price_match:
            continue
        time_match = _COMPACT_TIME_RE.search(compact)
        time_inherited = time_match is None
        if time_match:
            last_time = time_match.group(1)
        if last_time is None:
            continue

        quantity_tokens = _field_tokens(row_tokens, 168, 218)
        quantity = _first_integer("".join(
            token.text for token in sorted(quantity_tokens, key=lambda item: item.x)
        ))
        event_tokens = _field_tokens(row_tokens, 210, 242)
        event_text = "".join(
            token.text.upper()
            for token in sorted(event_tokens, key=lambda item: item.x)
        )
        event_match = _ORDER_EVENT_RE.search(event_text)
        if event_match is None:
            # RapidOCR occasionally joins quantity and the final B/S glyph.
            joined_tail = "".join(
                token.text.upper()
                for token in sorted(
                    _field_tokens(row_tokens, 168, 242),
                    key=lambda item: item.x,
                )
            )
            event_match = _ORDER_EVENT_RE.search(joined_tail)
        event_type = event_match.group(0) if event_match else None

        colour_confidence = max(
            (token.side_confidence for token in event_tokens), default=0.0,
        )
        text_confidence = min(
            (token.confidence for token in event_tokens), default=0.0,
        )
        event_confidence = max(colour_confidence, text_confidence)
        price_tokens = [
            token for token in ordered
            if price_match.group(0) in token.text
        ]
        time_tokens = [
            token for token in ordered
            if time_match is not None and time_match.group(0) in token.text
        ]
        relevant = price_tokens + quantity_tokens + time_tokens
        confidence = min(
            (token.confidence for token in relevant), default=0.0,
        )
        review_required = (
            quantity is None
            or event_type is None
            or not _is_valid_market_time(last_time)
            or confidence < 0.85
            or event_confidence < 0.75
        )
        events.append(TdxOrderEvent(
            market_date=market_date,
            code=code,
            market_time=last_time,
            price=float(price_match.group(1)),
            hands=quantity,
            event_type=event_type,
            source_page=source_page,
            page_sequence=page_sequence,
            panel=panel,
            row=row_number,
            time_inherited=time_inherited,
            ocr_confidence=round(confidence, 4),
            event_confidence=round(event_confidence, 4),
            event_source="ocr_text",
            review_required=review_required,
        ))
    return events, last_time


def parse_trade_panel(
    tokens: Iterable[OCRToken], *, market_date: str, code: str,
    source_page: str, page_sequence: int, panel: int,
    inherited_time: str | None = None,
) -> tuple[list[TdxTrade], str | None]:
    """Parse one verified five-panel 通达信逐笔成交 screenshot column.

    The parser deliberately keeps uncertain rows and marks them for review. It does
    not manufacture a B/S direction when neither OCR nor the source pixel colour
    supplies one.
    """

    trades: list[TdxTrade] = []
    last_time = inherited_time
    for row_number, row_tokens in enumerate(_cluster_rows(tokens), start=1):
        time_text = _field_text(row_tokens, 0, 82)
        price_text = _field_text(row_tokens, 72, 170)
        quantity_text = _field_text(row_tokens, 162, 236)
        buy_text = _field_text(row_tokens, 228, 294)
        sell_text = _field_text(row_tokens, 286, 340)

        price_match = _PRICE_RE.search(price_text)
        if not price_match:
            continue
        time_match = _TIME_RE.search(time_text)
        time_inherited = time_match is None
        if time_match:
            last_time = time_match.group(1)
        if last_time is None:
            continue

        quantity = _first_integer(quantity_text)
        buy_order = _first_integer(buy_text)
        sell_order = _first_integer(sell_text)

        side_candidates = [
            token for token in _field_tokens(row_tokens, 72, 236)
            if token.side_hint in {"B", "S"}
        ]
        side: str | None = None
        side_confidence = 0.0
        if side_candidates:
            strongest = max(side_candidates, key=lambda item: item.side_confidence)
            side = strongest.side_hint
            side_confidence = strongest.side_confidence
        else:
            compact = (price_text + quantity_text).upper()
            if "B" in compact and "S" not in compact:
                side = "B"
                side_confidence = 0.6
            elif "S" in compact and "B" not in compact:
                side = "S"
                side_confidence = 0.6

        relevant = _field_tokens(row_tokens, 0, 236)
        confidence = min((token.confidence for token in relevant), default=0.0)
        review_required = (
            quantity is None
            or side is None
            or not _is_valid_market_time(last_time)
            or confidence < 0.85
            or side_confidence < 0.55
        )
        trades.append(TdxTrade(
            market_date=market_date,
            code=code,
            market_time=last_time,
            price=float(price_match.group(1)),
            hands=quantity,
            side=side,
            buy_order=buy_order,
            sell_order=sell_order,
            source_page=source_page,
            page_sequence=page_sequence,
            panel=panel,
            row=row_number,
            time_inherited=time_inherited,
            ocr_confidence=round(confidence, 4),
            side_confidence=round(side_confidence, 4),
            review_required=review_required,
        ))
    return trades, last_time


def _token_side_hint(image: Any, box: list[list[float]]) -> tuple[str | None, float]:
    import numpy as np

    x0 = max(0, int(min(point[0] for point in box)))
    y0 = max(0, int(min(point[1] for point in box)))
    x1 = min(image.shape[1], int(max(point[0] for point in box)) + 1)
    y1 = min(image.shape[0], int(max(point[1] for point in box)) + 1)
    region = image[y0:y1, x0:x1]
    if region.size == 0:
        return None, 0.0
    blue = region[:, :, 0].astype(np.int16)
    green = region[:, :, 1].astype(np.int16)
    red = region[:, :, 2].astype(np.int16)
    red_count = int(((red > 85) & (red > green * 1.25) & (red > blue * 1.25)).sum())
    green_count = int(((green > 70) & (green > red * 1.25) & (green > blue * 1.1)).sum())
    total = red_count + green_count
    if total < 3:
        return None, 0.0
    if red_count > green_count:
        return "B", red_count / total
    if green_count > red_count:
        return "S", green_count / total
    return None, 0.0


def _ocr_tokens(engine: Any, image: Any) -> list[OCRToken]:
    result, _elapsed = engine(image)
    if not result:
        return []
    tokens: list[OCRToken] = []
    for box, text, confidence in result:
        x = min(point[0] for point in box)
        y = min(point[1] for point in box)
        side, side_confidence = _token_side_hint(image, box)
        tokens.append(OCRToken(
            x=float(x),
            y=float(y),
            text=str(text),
            confidence=float(confidence),
            side_hint=side,
            side_confidence=float(side_confidence),
        ))
    return tokens


def _page_sequence(path: Path) -> int:
    match = _SEQUENCE_RE.search(path.name)
    if not match:
        raise ValueError(f"Screenshot filename has no two-digit sequence: {path.name}")
    return int(match.group(1))


def _remove_page_overlap(pages: list[list[TdxTrade]]) -> tuple[list[TdxTrade], int]:
    output: list[TdxTrade] = []
    removed = 0
    previous_last_time: str | None = None
    for page in pages:
        if not page:
            continue
        for trade in page:
            if previous_last_time is not None and trade.market_time <= previous_last_time:
                removed += 1
                continue
            output.append(trade)
        previous_last_time = max(trade.market_time for trade in page)
    return output, removed


def _remove_order_page_overlap(
    pages: list[list[TdxOrderEvent]],
) -> tuple[list[TdxOrderEvent], int]:
    """Remove repeated PageDown rows while preserving same-second multiplicity."""

    output: list[TdxOrderEvent] = []
    prior_counts: Counter[tuple[Any, ...]] = Counter()
    removed = 0
    previous_last_time: str | None = None
    for page in pages:
        if not page:
            continue
        overlap_occurrences: Counter[tuple[Any, ...]] = Counter()
        for event in page:
            identity = event.identity()
            if previous_last_time is not None and event.market_time <= previous_last_time:
                overlap_occurrences[identity] += 1
                if overlap_occurrences[identity] <= prior_counts[identity]:
                    removed += 1
                    continue
            output.append(event)
            prior_counts[identity] += 1
        page_last_time = max(event.market_time for event in page)
        if previous_last_time is None or page_last_time > previous_last_time:
            previous_last_time = page_last_time
    return output, removed


def _sort_chronologically(
    rows: Iterable[TdxTrade | TdxOrderEvent],
) -> list[TdxTrade | TdxOrderEvent]:
    """Stable market-time order after page-overlap removal.

    Manual captures can overlap by hours, so a later screenshot may begin well
    before the preceding screenshot ends.  Python's stable sort preserves the
    visible order of same-second rows while restoring causal page-to-page order.
    Invalid OCR times remain in the review queue and sort deterministically.
    """

    return sorted(rows, key=lambda item: item.market_time)


def _mark_price_outliers_for_review(
    rows: Iterable[TdxTrade | TdxOrderEvent],
) -> tuple[list[TdxTrade | TdxOrderEvent], int]:
    """Flag extreme OCR price substitutions without repairing them silently."""

    output = list(rows)
    if not output:
        return output, 0
    center = statistics.median(item.price for item in output)
    lower = center * 0.5
    upper = center * 1.5
    flagged = 0
    reviewed: list[TdxTrade | TdxOrderEvent] = []
    for item in output:
        if lower <= item.price <= upper:
            reviewed.append(item)
            continue
        flagged += 1
        reviewed.append(replace(item, review_required=True))
    return reviewed, flagged


def extract_trade_screenshots(
    input_dir: Path, *, market_date: str, code: str,
) -> tuple[list[TdxTrade], list[TdxTrade], dict[str, Any]]:
    try:
        import cv2
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            "OCR dependencies are missing; install the project 'ocr' extra"
        ) from exc

    screenshots = sorted(input_dir.glob("*.png"), key=_page_sequence)
    if not screenshots:
        raise FileNotFoundError(f"No PNG screenshots found in {input_dir}")

    expected = list(range(1, len(screenshots) + 1))
    actual = [_page_sequence(path) for path in screenshots]
    if actual != expected:
        raise ValueError(f"Screenshot sequence is not contiguous: {actual}")

    engine = RapidOCR()
    pages: list[list[TdxTrade]] = []
    dimensions: set[tuple[int, int]] = set()
    for path in screenshots:
        encoded = np.fromfile(path, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Cannot decode screenshot: {path}")
        height, width = image.shape[:2]
        dimensions.add((width, height))
        layout = _trade_screenshot_layout(width, height)
        if layout is None:
            raise ValueError(
                f"Unverified screenshot layout {width}x{height}: {path.name}"
            )

        page_trades: list[TdxTrade] = []
        inherited_time: str | None = None
        for panel in range(layout.panels):
            left = round(width * panel / layout.panels)
            right = round(width * (panel + 1) / layout.panels)
            crop = image[layout.top:layout.bottom, left:right]
            tokens = _normalize_panel_tokens(
                _ocr_tokens(engine, crop),
                actual_width=right - left,
                canonical_width=layout.canonical_panel_width,
            )
            trades, inherited_time = parse_trade_panel(
                tokens,
                market_date=market_date,
                code=code,
                source_page=path.name,
                page_sequence=_page_sequence(path),
                panel=panel + 1,
                inherited_time=inherited_time,
            )
            page_trades.extend(trades)
        pages.append(page_trades)

    raw = [trade for page in pages for trade in page]
    deduplicated, overlap_removed = _remove_page_overlap(pages)
    deduplicated = _sort_chronologically(deduplicated)
    deduplicated, price_outlier_rows = _mark_price_outliers_for_review(
        deduplicated,
    )
    review_count = sum(trade.review_required for trade in deduplicated)
    valid_times = [
        trade.market_time for trade in deduplicated
        if _is_valid_market_time(trade.market_time)
    ]
    invalid_time_rows = len(deduplicated) - len(valid_times)
    summary = {
        "market_date": market_date,
        "code": code,
        "input_dir": str(input_dir.resolve()),
        "pages": len(screenshots),
        "page_sequences": actual,
        "dimensions": [list(item) for item in sorted(dimensions)],
        "raw_rows": len(raw),
        "overlap_rows_removed": overlap_removed,
        "deduplicated_rows": len(deduplicated),
        "review_required_rows": review_count,
        "price_outlier_rows": price_outlier_rows,
        "invalid_time_rows": invalid_time_rows,
        "first_time": min(valid_times) if valid_times else None,
        "last_time": max(valid_times) if valid_times else None,
        "complete_for_automatic_analysis": bool(deduplicated) and review_count == 0,
    }
    return raw, deduplicated, summary


def _order_event_glyph_feature(event: TdxOrderEvent, image: Any) -> Any:
    import cv2
    import numpy as np

    height, width = image.shape[:2]
    layout = _order_screenshot_layout(width, height)
    if layout is None:
        return None
    panel_left = round(width * (event.panel - 1) / layout.panels)
    panel_right = round(width * event.panel / layout.panels)
    panel_width = panel_right - panel_left
    canonical_width = layout.canonical_panel_width or float(panel_width)
    glyph_left = panel_left + round(215 * panel_width / canonical_width)
    glyph_right = min(
        panel_right,
        panel_left + round(242 * panel_width / canonical_width),
    )
    row_top = layout.top + (event.row - 1) * 17
    crop = image[
        max(0, row_top - 1):min(image.shape[0], row_top + 16),
        glyph_left:glyph_right,
    ]
    if crop.size == 0:
        return None
    colour = cv2.resize(crop, (27, 16)).astype(np.float32) / 255.0
    gray = colour.mean(axis=2)
    return np.concatenate((gray.reshape(-1), colour.reshape(-1)))


def _order_glyph_repair_requires_review(event: TdxOrderEvent) -> bool:
    return (
        event.hands is None
        or not _is_valid_market_time(event.market_time)
        or event.ocr_confidence < 0.85
    )


def _classify_unknown_order_glyphs(
    pages: list[list[TdxOrderEvent]], page_images: dict[str, Any],
) -> tuple[list[list[TdxOrderEvent]], dict[str, Any]]:
    """Recover unread final event glyphs from the verified fixed TDX layout.

    A ridge classifier is trained only on OCR-labelled glyphs from the same
    capture.  Leave-one-page-out validation must reach 99.5% before any unknown
    row is filled, and each filled row must retain a score margin of at least
    0.50.  This is OCR repair, not a trading model or direction inference.
    """

    import numpy as np

    labels = ("B", "S", "BC", "SC")
    known: list[tuple[TdxOrderEvent, Any]] = []
    for page in pages:
        for event in page:
            if (
                event.event_type in labels
                and event.event_confidence >= 0.75
            ):
                feature = _order_event_glyph_feature(
                    event, page_images[event.source_page],
                )
                if feature is not None:
                    known.append((event, feature))
    if not known:
        return pages, {
            "enabled": False,
            "reason": "no_high_confidence_training_glyphs",
        }

    features = np.stack([item[1] for item in known]).astype(np.float64)
    features = np.column_stack((np.ones(len(features)), features))
    label_indexes = np.array([
        labels.index(item[0].event_type or "") for item in known
    ])
    targets = np.eye(len(labels))[label_indexes]
    page_indexes = np.array([item[0].page_sequence for item in known])
    regularization = 1.0

    validated = 0
    correct = 0
    class_totals = Counter()
    class_correct = Counter()
    validation_margins: list[float] = []
    for page_sequence in sorted(set(page_indexes.tolist())):
        train = page_indexes != page_sequence
        test = page_indexes == page_sequence
        if not train.any() or not test.any():
            continue
        train_features = features[train]
        weights = np.linalg.solve(
            train_features.T @ train_features
                + regularization * np.eye(train_features.shape[1]),
            train_features.T @ targets[train],
        )
        scores = features[test] @ weights
        predictions = scores.argmax(axis=1)
        ordered_scores = np.sort(scores, axis=1)
        validation_margins.extend(
            (ordered_scores[:, -1] - ordered_scores[:, -2]).tolist()
        )
        actual = label_indexes[test]
        validated += int(test.sum())
        correct += int((predictions == actual).sum())
        for expected, predicted in zip(actual, predictions):
            label = labels[int(expected)]
            class_totals[label] += 1
            if expected == predicted:
                class_correct[label] += 1

    validation_accuracy = correct / validated if validated else 0.0
    per_class_accuracy = {
        label: (
            class_correct[label] / class_totals[label]
            if class_totals[label] else 0.0
        )
        for label in labels
    }
    validation_passed = (
        validation_accuracy >= 0.995
        and all(value >= 0.99 for value in per_class_accuracy.values())
    )
    if not validation_passed:
        return pages, {
            "enabled": False,
            "reason": "leave_one_page_out_validation_failed",
            "training_rows": len(known),
            "validation_accuracy": round(validation_accuracy, 6),
            "per_class_accuracy": {
                key: round(value, 6)
                for key, value in per_class_accuracy.items()
            },
        }

    weights = np.linalg.solve(
        features.T @ features
            + regularization * np.eye(features.shape[1]),
        features.T @ targets,
    )
    repaired_pages: list[list[TdxOrderEvent]] = []
    repaired = 0
    low_margin = 0
    predicted_counts = Counter()
    for page in pages:
        repaired_page: list[TdxOrderEvent] = []
        for event in page:
            if event.event_type is not None:
                repaired_page.append(event)
                continue
            feature = _order_event_glyph_feature(
                event, page_images[event.source_page],
            )
            if feature is None:
                repaired_page.append(event)
                continue
            vector = np.concatenate(([1.0], feature.astype(np.float64)))
            scores = vector @ weights
            order = np.argsort(scores)
            margin = float(scores[order[-1]] - scores[order[-2]])
            if margin < 0.50:
                low_margin += 1
                repaired_page.append(event)
                continue
            predicted = labels[int(order[-1])]
            predicted_counts[predicted] += 1
            repaired += 1
            repaired_page.append(replace(
                event,
                event_type=predicted,
                event_confidence=round(min(1.0, margin), 4),
                event_source="glyph_ridge",
                review_required=_order_glyph_repair_requires_review(event),
            ))
        repaired_pages.append(repaired_page)

    return repaired_pages, {
        "enabled": True,
        "method": "fixed_layout_glyph_ridge",
        "training_rows": len(known),
        "regularization": regularization,
        "minimum_prediction_margin": 0.50,
        "validation_rows": validated,
        "validation_accuracy": round(validation_accuracy, 6),
        "per_class_accuracy": {
            key: round(value, 6)
            for key, value in per_class_accuracy.items()
        },
        "minimum_validation_margin": round(
            min(validation_margins) if validation_margins else 0.0, 6,
        ),
        "repaired_rows": repaired,
        "low_margin_rows": low_margin,
        "predicted_counts": dict(sorted(predicted_counts.items())),
    }


def extract_order_screenshots(
    input_dir: Path, *, market_date: str, code: str,
) -> tuple[list[TdxOrderEvent], list[TdxOrderEvent], dict[str, Any]]:
    try:
        import cv2
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError(
            "OCR dependencies are missing; install the project 'ocr' extra"
        ) from exc

    screenshots = sorted(input_dir.glob("*.png"), key=_page_sequence)
    if not screenshots:
        raise FileNotFoundError(f"No PNG screenshots found in {input_dir}")
    expected = list(range(1, len(screenshots) + 1))
    actual = [_page_sequence(path) for path in screenshots]
    if actual != expected:
        raise ValueError(f"Screenshot sequence is not contiguous: {actual}")

    engine = RapidOCR()
    pages: list[list[TdxOrderEvent]] = []
    page_images: dict[str, Any] = {}
    dimensions: set[tuple[int, int]] = set()
    for path in screenshots:
        encoded = np.fromfile(path, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"Cannot decode screenshot: {path}")
        page_images[path.name] = image
        height, width = image.shape[:2]
        dimensions.add((width, height))
        layout = _order_screenshot_layout(width, height)
        if layout is None:
            raise ValueError(
                f"Unverified screenshot layout {width}x{height}: {path.name}"
            )

        page_events: list[TdxOrderEvent] = []
        inherited_time: str | None = None
        for panel_index in range(layout.panels):
            left = round(width * panel_index / layout.panels)
            right = round(width * (panel_index + 1) / layout.panels)
            panel_crop = image[layout.top:layout.bottom, left:right]
            tokens_for_panel = _normalize_panel_tokens(
                _ocr_tokens(engine, panel_crop),
                actual_width=right - left,
                canonical_width=layout.canonical_panel_width,
            )
            events, inherited_time = parse_order_panel(
                tokens_for_panel,
                market_date=market_date,
                code=code,
                source_page=path.name,
                page_sequence=_page_sequence(path),
                panel=panel_index + 1,
                inherited_time=inherited_time,
            )
            page_events.extend(events)
        pages.append(page_events)

    pages, glyph_classifier = _classify_unknown_order_glyphs(
        pages, page_images,
    )
    raw = [event for page in pages for event in page]
    deduplicated, overlap_removed = _remove_order_page_overlap(pages)
    deduplicated = _sort_chronologically(deduplicated)
    deduplicated, price_outlier_rows = _mark_price_outliers_for_review(
        deduplicated,
    )
    review_count = sum(event.review_required for event in deduplicated)
    valid_times = [
        event.market_time for event in deduplicated
        if _is_valid_market_time(event.market_time)
    ]
    invalid_time_rows = len(deduplicated) - len(valid_times)
    event_counts = Counter(
        event.event_type or "unknown" for event in deduplicated
    )
    summary = {
        "market_date": market_date,
        "code": code,
        "input_dir": str(input_dir.resolve()),
        "pages": len(screenshots),
        "page_sequences": actual,
        "panels_per_page": next(iter({
            _order_screenshot_layout(width, height).panels
            for width, height in dimensions
            if _order_screenshot_layout(width, height) is not None
        })),
        "dimensions": [list(item) for item in sorted(dimensions)],
        "raw_rows": len(raw),
        "overlap_rows_removed": overlap_removed,
        "deduplicated_rows": len(deduplicated),
        "event_counts": dict(sorted(event_counts.items())),
        "event_glyph_classifier": glyph_classifier,
        "review_required_rows": review_count,
        "price_outlier_rows": price_outlier_rows,
        "invalid_time_rows": invalid_time_rows,
        "first_time": min(valid_times) if valid_times else None,
        "last_time": max(valid_times) if valid_times else None,
        "complete_for_automatic_analysis": bool(deduplicated) and review_count == 0,
    }
    return raw, deduplicated, summary


def _write_csv(path: Path, trades: Iterable[TdxTrade]) -> None:
    rows = [asdict(trade) for trade in trades]
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(TdxTrade(
        market_date="", code="", market_time="", price=0.0, hands=None,
        side=None, buy_order=None, sell_order=None, source_page="",
        page_sequence=0, panel=0, row=0, time_inherited=False,
        ocr_confidence=0.0, side_confidence=0.0, review_required=True,
    )).keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_order_csv(path: Path, events: Iterable[TdxOrderEvent]) -> None:
    rows = [asdict(event) for event in events]
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(TdxOrderEvent(
        market_date="", code="", market_time="", price=0.0, hands=None,
        event_type=None, source_page="", page_sequence=0, panel=0, row=0,
        time_inherited=False, ocr_confidence=0.0, event_confidence=0.0,
        event_source="", review_required=True,
    )).keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _trade_review_reasons(trade: TdxTrade) -> str:
    reasons: list[str] = []
    if not _is_valid_market_time(trade.market_time):
        reasons.append("invalid_market_time")
    if trade.hands is None:
        reasons.append("missing_hands")
    if trade.side is None:
        reasons.append("missing_side")
    if trade.ocr_confidence < 0.85:
        reasons.append("low_ocr_confidence")
    if trade.side_confidence < 0.55:
        reasons.append("low_side_confidence")
    if trade.review_required and not reasons:
        reasons.append("price_or_plausibility_outlier")
    return ";".join(reasons)


def _order_review_reasons(event: TdxOrderEvent) -> str:
    reasons: list[str] = []
    if not _is_valid_market_time(event.market_time):
        reasons.append("invalid_market_time")
    if event.hands is None:
        reasons.append("missing_hands")
    if event.event_type is None:
        reasons.append("missing_event_type")
    if event.ocr_confidence < 0.85:
        reasons.append("low_ocr_confidence")
    if event.event_confidence < 0.75:
        reasons.append("low_event_confidence")
    if event.review_required and not reasons:
        reasons.append("price_or_plausibility_outlier")
    return ";".join(reasons)


def _write_review_queue(
    path: Path, rows: Iterable[TdxTrade | TdxOrderEvent], *, order: bool,
) -> None:
    selected = [item for item in rows if item.review_required]
    example = asdict(TdxOrderEvent(
        market_date="", code="", market_time="", price=0.0, hands=None,
        event_type=None, source_page="", page_sequence=0, panel=0, row=0,
        time_inherited=False, ocr_confidence=0.0, event_confidence=0.0,
        event_source="", review_required=True,
    ) if order else TdxTrade(
        market_date="", code="", market_time="", price=0.0, hands=None,
        side=None, buy_order=None, sell_order=None, source_page="",
        page_sequence=0, panel=0, row=0, time_inherited=False,
        ocr_confidence=0.0, side_confidence=0.0, review_required=True,
    ))
    fieldnames = [*example.keys(), "review_reasons"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in selected:
            row = asdict(item)
            row["review_reasons"] = (
                _order_review_reasons(item)
                if order else _trade_review_reasons(item)
            )
            writer.writerow(row)


def write_trade_extraction(
    output_dir: Path, raw: list[TdxTrade], deduplicated: list[TdxTrade],
    summary: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "逐笔成交_原始识别.csv", raw)
    _write_csv(output_dir / "逐笔成交_去重.csv", deduplicated)
    _write_review_queue(
        output_dir / "逐笔成交_待人工复核.csv", deduplicated, order=False,
    )
    with (output_dir / "逐笔成交_识别摘要.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def write_order_extraction(
    output_dir: Path, raw: list[TdxOrderEvent],
    deduplicated: list[TdxOrderEvent], summary: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_order_csv(output_dir / "逐笔委托_原始识别.csv", raw)
    _write_order_csv(output_dir / "逐笔委托_去重.csv", deduplicated)
    _write_review_queue(
        output_dir / "逐笔委托_待人工复核.csv", deduplicated, order=True,
    )
    with (output_dir / "逐笔委托_识别摘要.json").open(
        "w", encoding="utf-8",
    ) as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def load_order_events(path: Path) -> list[TdxOrderEvent]:
    events: list[TdxOrderEvent] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            events.append(TdxOrderEvent(
                market_date=row["market_date"],
                code=row["code"],
                market_time=row["market_time"],
                price=float(row["price"]),
                hands=int(row["hands"]) if row["hands"] else None,
                event_type=row["event_type"] or None,
                source_page=row["source_page"],
                page_sequence=int(row["page_sequence"]),
                panel=int(row["panel"]),
                row=int(row["row"]),
                time_inherited=row["time_inherited"].lower() == "true",
                ocr_confidence=float(row["ocr_confidence"]),
                event_confidence=float(row["event_confidence"]),
                event_source=row["event_source"],
                review_required=row["review_required"].lower() == "true",
            ))
    return events


def apply_manual_order_reviews(
    events: Iterable[TdxOrderEvent], reviews_path: Path,
) -> tuple[list[TdxOrderEvent], int]:
    reviewed = list(events)
    positions = {
        (item.source_page, item.page_sequence, item.panel, item.row): index
        for index, item in enumerate(reviewed)
    }
    applied = 0
    with reviews_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (
                row["source_page"], int(row["page_sequence"]),
                int(row["panel"]), int(row["row"]),
            )
            if key not in positions:
                raise ValueError(
                    f"Manual order review row does not match a source event: {key}"
                )
            index = positions[key]
            original = reviewed[index]
            if original.market_time != row["market_time"]:
                raise ValueError(
                    f"Manual order review time mismatch for {key}: "
                    f"{original.market_time} != {row['market_time']}"
                )
            corrected_market_time = (
                row.get("corrected_market_time") or original.market_time
            )
            if not _is_valid_market_time(corrected_market_time):
                raise ValueError(
                    f"Invalid manually reviewed order time for {key}: "
                    f"{corrected_market_time}"
                )
            event_type = row["corrected_event_type"]
            if event_type not in {"B", "S", "BC", "SC"}:
                raise ValueError(
                    f"Invalid manually reviewed order event for {key}: {event_type}"
                )
            reviewed[index] = replace(
                original,
                market_time=corrected_market_time,
                price=float(row["corrected_price"]),
                hands=int(row["corrected_hands"]),
                event_type=event_type,
                event_confidence=1.0,
                event_source="manual_review",
                review_required=False,
            )
            applied += 1
    return reviewed, applied
