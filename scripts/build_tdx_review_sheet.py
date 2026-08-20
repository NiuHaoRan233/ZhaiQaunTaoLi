from __future__ import annotations

import argparse
import csv
from pathlib import Path

import cv2
import numpy as np
from rapidocr_onnxruntime import RapidOCR

from zhaiquant.tdx_tape import _cluster_rows, _ocr_tokens


def read_image(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Cannot decode {path}")
    return image


def write_image(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    extension = path.suffix or ".png"
    ok, encoded = cv2.imencode(extension, image)
    if not ok:
        raise ValueError(f"Cannot encode {path}")
    encoded.tofile(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an OCR manual-review contact sheet")
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--kind", choices=("orders", "trades"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    with args.queue.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    panels = 10 if args.kind == "orders" else 8
    tile_width = 1040
    tile_height = 62
    columns = 2
    sheet = np.full(
        ((len(rows) + columns - 1) // columns * tile_height,
         columns * tile_width, 3),
        245,
        dtype=np.uint8,
    )
    cache: dict[str, np.ndarray] = {}
    row_y_cache: dict[tuple[str, int], list[float]] = {}
    ocr = RapidOCR()
    for index, row in enumerate(rows, start=1):
        source = row["source_page"]
        image = cache.setdefault(source, read_image(args.images / source))
        panel = int(row["panel"])
        visible_row = int(row["row"])
        left = round(image.shape[1] * (panel - 1) / panels)
        right = round(image.shape[1] * panel / panels)
        cache_key = (source, panel)
        if args.kind == "trades":
            if cache_key not in row_y_cache:
                tokens = _ocr_tokens(ocr, image[55:image.shape[0] - 15, left:right])
                row_y_cache[cache_key] = [
                    min(token.y for token in cluster)
                    for cluster in _cluster_rows(tokens)
                ]
            cluster_rows = row_y_cache[cache_key]
        else:
            cluster_rows = []
        if args.kind == "trades" and 1 <= visible_row <= len(cluster_rows):
            row_y = cluster_rows[visible_row - 1]
            top = max(55, 55 + round(row_y) - 3)
        else:
            top = max(55, 55 + (visible_row - 1) * 17 - 2)
        bottom = min(image.shape[0] - 15, top + 22)
        crop = image[top:bottom, left:right]
        crop = cv2.resize(crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_NEAREST)

        tile_column = (index - 1) % columns
        tile_row = (index - 1) // columns
        x0 = tile_column * tile_width
        y0 = tile_row * tile_height
        label_fields = [
            f"#{index} p{row['page_sequence']}/{panel} r{visible_row}",
            row.get("market_time", ""),
            row.get("price", ""),
            row.get("hands", "") or "MISSING",
            row.get("event_type", row.get("side", "")),
        ]
        label = " | ".join(label_fields)
        cv2.putText(
            sheet, label, (x0 + 5, y0 + 24), cv2.FONT_HERSHEY_SIMPLEX,
            0.48, (0, 0, 0), 1, cv2.LINE_AA,
        )
        crop_x = x0 + 365
        crop_y = y0 + 4
        available_width = tile_width - 370
        copy_width = min(available_width, crop.shape[1])
        copy_height = min(tile_height - 8, crop.shape[0])
        sheet[crop_y:crop_y + copy_height, crop_x:crop_x + copy_width] = (
            crop[:copy_height, :copy_width]
        )

    write_image(args.output, sheet)
    print(f"Wrote {len(rows)} review rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
