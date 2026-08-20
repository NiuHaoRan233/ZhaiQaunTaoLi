from __future__ import annotations

import argparse
import ctypes
import json
import queue
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "settings.json"
EXAMPLE_CONFIG = ROOT / "settings.example.json"

TABLE_PROFILES: dict[str, dict[str, Any]] = {
    "futures_trades": {
        "header_rows": 1,
        "columns": [
            {"name": "time", "label": "时间", "left": 0.000, "right": 0.305},
            {"name": "price", "label": "价格", "left": 0.305, "right": 0.510},
            {"name": "volume", "label": "现量", "left": 0.510, "right": 0.690},
            {"name": "open_interest_change", "label": "仓差", "left": 0.690, "right": 0.850},
            {"name": "open_close", "label": "开平", "left": 0.850, "right": 1.000},
        ],
    },
}

PROFILE_HEADER_ALIASES: dict[str, tuple[tuple[str, ...], ...]] = {
    "futures_trades": (
        ("时间", "成交时间"),
        ("价格", "成交价"),
        ("现量", "数量", "成交量"),
        ("仓差",),
        ("开平", "性质"),
    ),
}


@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class OCRJob:
    captured_at: str
    window_title: str
    region_name: str
    changed_fraction: float
    row_indices: tuple[int, ...]
    row_images: tuple[np.ndarray, ...]
    columns: tuple[dict[str, Any], ...]
    source_image: np.ndarray


def _enable_dpi_awareness() -> None:
    """Keep Win32 coordinates aligned with physical screenshot pixels."""

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def visible_windows() -> list[Window]:
    if sys.platform != "win32":
        raise RuntimeError("This reader currently supports Windows only")
    user32 = ctypes.windll.user32
    windows: list[Window] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if title:
            windows.append(Window(int(hwnd), title))
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return sorted(windows, key=lambda item: item.title.casefold())


def find_window(title_contains: str) -> Window:
    needle = title_contains.casefold().strip()
    matches = [window for window in visible_windows() if needle in window.title.casefold()]
    if not matches:
        raise RuntimeError(f"No visible window title contains: {title_contains!r}")
    if len(matches) > 1:
        choices = "\n".join(f"  hwnd={item.hwnd}  {item.title}" for item in matches)
        raise RuntimeError(f"Window title is ambiguous:\n{choices}")
    window = matches[0]
    if ctypes.windll.user32.IsIconic(window.hwnd):
        raise RuntimeError(f"Target window is minimized: {window.title}")
    return window


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _WinRect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def client_rect(window: Window) -> Rect:
    user32 = ctypes.windll.user32
    raw = _WinRect()
    if not user32.GetClientRect(window.hwnd, ctypes.byref(raw)):
        raise ctypes.WinError()
    origin = _Point(0, 0)
    if not user32.ClientToScreen(window.hwnd, ctypes.byref(origin)):
        raise ctypes.WinError()
    width = int(raw.right - raw.left)
    height = int(raw.bottom - raw.top)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Target window has an invalid client area: {width}x{height}")
    return Rect(int(origin.x), int(origin.y), width, height)


class ScreenCapture:
    def __init__(self) -> None:
        try:
            import mss
        except ImportError as exc:
            raise RuntimeError(
                "Missing mss. Run: .\\.venv\\Scripts\\python.exe -m pip install "
                "-r .\\行情屏幕高速读取\\requirements.txt"
            ) from exc
        self._mss = mss.mss()

    def grab(self, rect: Rect) -> np.ndarray:
        shot = self._mss.grab({
            "left": rect.left,
            "top": rect.top,
            "width": rect.width,
            "height": rect.height,
        })
        return np.asarray(shot, dtype=np.uint8)[:, :, :3].copy()

    def close(self) -> None:
        self._mss.close()


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Config not found: {path}\nCopy {EXAMPLE_CONFIG.name} to {path.name} first."
        )
    with path.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    required = {
        "window_title_contains",
        "target_fps",
        "pixel_delta",
        "changed_fraction",
        "min_ocr_interval_ms",
        "save_changed_images",
        "output_dir",
        "regions",
    }
    missing = sorted(required - config.keys())
    if missing:
        raise ValueError(f"Config is missing keys: {', '.join(missing)}")
    if float(config["target_fps"]) <= 0:
        raise ValueError("target_fps must be positive")
    if not 0 <= float(config["changed_fraction"]) <= 1:
        raise ValueError("changed_fraction must be between 0 and 1")
    return config


def save_config(path: Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def normalized_region(region: dict[str, Any], frame: np.ndarray) -> tuple[int, int, int, int]:
    height, width = frame.shape[:2]
    x = max(0, min(width - 1, round(float(region["x"]) * width)))
    y = max(0, min(height - 1, round(float(region["y"]) * height)))
    right = max(x + 1, min(width, round((float(region["x"]) + float(region["width"])) * width)))
    bottom = max(y + 1, min(height, round((float(region["y"]) + float(region["height"])) * height)))
    return x, y, right - x, bottom - y


def change_fraction(previous: np.ndarray | None, current: np.ndarray, pixel_delta: int) -> float:
    if previous is None or previous.shape != current.shape:
        return 1.0
    previous_gray = cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY)
    current_gray = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
    target_width = min(320, current_gray.shape[1])
    scale = target_width / current_gray.shape[1]
    target_height = max(1, round(current_gray.shape[0] * scale))
    previous_small = cv2.resize(previous_gray, (target_width, target_height), interpolation=cv2.INTER_AREA)
    current_small = cv2.resize(current_gray, (target_width, target_height), interpolation=cv2.INTER_AREA)
    difference = cv2.absdiff(previous_small, current_small)
    return float(np.count_nonzero(difference >= pixel_delta) / difference.size)


def split_rows(image: np.ndarray, row_height: int) -> list[np.ndarray]:
    if row_height <= 0:
        raise ValueError("row_height must be positive")
    return [
        image[top:top + row_height]
        for top in range(0, image.shape[0] - row_height + 1, row_height)
    ]


def row_fingerprint(image: np.ndarray) -> bytes:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height = min(16, gray.shape[0])
    resized = cv2.resize(gray, (128, height), interpolation=cv2.INTER_AREA)
    return (resized // 8).astype(np.uint8).tobytes()


def new_row_indices(
    previous: list[np.ndarray] | None,
    current: list[np.ndarray],
    *,
    newest_at: str = "bottom",
) -> list[int]:
    """Find inserted edge rows by aligning the previous and current table."""

    if previous is None:
        return list(range(len(current)))
    old = [row_fingerprint(row) for row in previous]
    new = [row_fingerprint(row) for row in current]
    maximum_shift = min(len(old), len(new))
    for shift in range(1, maximum_shift + 1):
        overlap = min(len(new) - shift, len(old) - shift)
        if overlap <= 0:
            break
        if newest_at == "bottom" and new[:overlap] == old[shift:shift + overlap]:
            return list(range(len(new) - shift, len(new)))
        if newest_at == "top" and new[shift:shift + overlap] == old[:overlap]:
            return list(range(shift))
    if not new:
        return []
    edge = 0 if newest_at == "top" else len(new) - 1
    old_edge = 0 if newest_at == "top" else len(old) - 1
    if old and new[edge] == old[old_edge]:
        return []
    # If antialiasing or a one-pixel row offset prevents full-table alignment,
    # emit only the changing newest edge. This avoids flooding the output with
    # every old visible row while retaining the newest market event.
    return [edge]


def select_newest(indices: list[int], *, maximum: int, newest_at: str) -> list[int]:
    if maximum <= 0:
        raise ValueError("max_rows_per_event must be positive")
    if newest_at == "top":
        return indices[:maximum]
    if newest_at == "bottom":
        return indices[-maximum:]
    raise ValueError("newest_at must be 'top' or 'bottom'")


def split_table_cells(
    rows: tuple[np.ndarray, ...], columns: tuple[dict[str, Any], ...],
) -> list[np.ndarray]:
    cells: list[np.ndarray] = []
    for row in rows:
        width = row.shape[1]
        for column in columns:
            left = max(0, min(width - 1, round(float(column["left"]) * width)))
            right = max(left + 1, min(width, round(float(column["right"]) * width)))
            cells.append(row[:, left:right])
    return cells


class FIFOJobBuffer:
    """Preserve every detected screen event without blocking frame capture."""

    def __init__(self) -> None:
        self._queue: queue.Queue[OCRJob | None] = queue.Queue()

    def put(self, job: OCRJob) -> None:
        self._queue.put_nowait(job)

    def get(self) -> OCRJob | None:
        return self._queue.get()

    def done(self) -> None:
        self._queue.task_done()

    def backlog(self) -> int:
        return self._queue.qsize()

    def close(self) -> None:
        self._queue.join()
        self._queue.put_nowait(None)

    def discard_pending(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                return


class OCRWorker:
    def __init__(self, output_dir: Path, save_images: bool) -> None:
        self.output_dir = output_dir
        self.save_images = save_images
        self.jobs = FIFOJobBuffer()
        self.thread = threading.Thread(target=self._run, name="market-ocr", daemon=True)
        self.error: BaseException | None = None
        self.processed = 0
        self._printed_headers: set[str] = set()

    def start(self) -> None:
        self.thread.start()

    def submit(self, job: OCRJob) -> None:
        self.jobs.put(job)

    @property
    def backlog(self) -> int:
        return self.jobs.backlog()

    def stop(self) -> None:
        if self.error is None:
            self.jobs.close()
        else:
            self.jobs.discard_pending()
        self.thread.join(timeout=10)

    def _run(self) -> None:
        try:
            from rapidocr_onnxruntime import RapidOCR

            engine = RapidOCR()
            while True:
                job = self.jobs.get()
                if job is None:
                    return
                try:
                    started = time.perf_counter()
                    images = (
                        split_table_cells(job.row_images, job.columns)
                        if job.columns
                        else list(job.row_images)
                    )
                    result, _engine_elapsed = engine.text_rec(images)
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    self._write(job, result or [], elapsed_ms)
                    self.processed += 1
                finally:
                    self.jobs.done()
        except BaseException as exc:
            self.error = exc

    def _write(self, job: OCRJob, result: list[Any], elapsed_ms: float) -> None:
        day = job.captured_at[:10]
        event_dir = self.output_dir / "events"
        event_dir.mkdir(parents=True, exist_ok=True)
        lines: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        if job.columns:
            column_count = len(job.columns)
            for row_offset, row_index in enumerate(job.row_indices):
                fields: dict[str, str] = {}
                confidences: dict[str, float] = {}
                for column_offset, column in enumerate(job.columns):
                    result_index = row_offset * column_count + column_offset
                    if result_index < len(result):
                        text, confidence = result[result_index]
                    else:
                        text, confidence = "", 0.0
                    fields[str(column["name"])] = str(text).strip()
                    confidences[str(column["name"])] = round(float(confidence), 4)
                display = " | ".join(fields[str(column["name"])] for column in job.columns)
                minimum_confidence = min(confidences.values(), default=0.0)
                lines.append({
                    "row_index": row_index,
                    "text": display,
                    "confidence": round(minimum_confidence, 4),
                })
                records.append({
                    "row_index": row_index,
                    "fields": fields,
                    "confidences": confidences,
                })
        else:
            for row_index, recognized in zip(job.row_indices, result):
                text, confidence = recognized
                lines.append({
                    "row_index": row_index,
                    "text": str(text),
                    "confidence": round(float(confidence), 4),
                })
        record = {
            "captured_at": job.captured_at,
            "window_title": job.window_title,
            "region": job.region_name,
            "changed_fraction": round(job.changed_fraction, 6),
            "ocr_elapsed_ms": round(elapsed_ms, 2),
            "recognition_mode": "detector_free_table_cells" if job.columns else "detector_free_rows",
            "lines": lines,
        }
        if job.columns:
            record["columns"] = [
                {"name": column["name"], "label": column["label"]}
                for column in job.columns
            ]
            record["records"] = records
        with (event_dir / f"{day}.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

        if job.columns and job.region_name not in self._printed_headers:
            labels = " | ".join(str(column["label"]) for column in job.columns)
            print(f"\n[{job.region_name}] {labels}", flush=True)
            print("-" * max(40, len(labels) * 3), flush=True)
            self._printed_headers.add(job.region_name)
        for line in lines:
            print(f"{line['text']}  (conf={line['confidence']:.3f})", flush=True)

        if self.save_images:
            image_dir = self.output_dir / "changed_images" / day
            image_dir.mkdir(parents=True, exist_ok=True)
            stamp = job.captured_at[11:23].replace(":", "-").replace(".", "-")
            safe_region = "".join(char if char.isalnum() or char in "-_" else "_" for char in job.region_name)
            target = image_dir / f"{stamp}_{safe_region}.png"
            cv2.imencode(".png", job.source_image)[1].tofile(target)


def resolve_output_dir(config_path: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else config_path.parent / path


def capture_target(config: dict[str, Any]) -> tuple[Window, Rect, np.ndarray]:
    window = find_window(str(config["window_title_contains"]))
    rect = client_rect(window)
    capture = ScreenCapture()
    try:
        frame = capture.grab(rect)
    finally:
        capture.close()
    return window, rect, frame


def recognize_row_batches(
    engine: Any, rows: list[np.ndarray], *, batch_size: int = 6,
) -> tuple[list[tuple[str, float]], float]:
    recognized: list[tuple[str, float]] = []
    started = time.perf_counter()
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        result, _engine_elapsed = engine.text_rec(batch)
        recognized.extend((str(text), float(confidence)) for text, confidence in result)
    return recognized, (time.perf_counter() - started) * 1000


def infer_table_profile(image: np.ndarray, row_height: int) -> tuple[str | None, str]:
    """Recognize a header once and map it to a reusable table-layout profile."""

    from rapidocr_onnxruntime import RapidOCR

    header_band = image[:max(row_height + 4, row_height * 2)]
    result, _elapsed = RapidOCR()(header_band)
    header_text = "".join(str(text).replace(" ", "") for _box, text, _confidence in (result or []))
    best_profile: str | None = None
    best_matches = 0
    for profile_name, groups in PROFILE_HEADER_ALIASES.items():
        matches = sum(
            any(alias in header_text for alias in aliases)
            for aliases in groups
        )
        if matches > best_matches:
            best_profile = profile_name
            best_matches = matches
    return (best_profile if best_matches >= 3 else None), header_text


def command_inspect(args: argparse.Namespace) -> int:
    """Read every configured row once so a static screen can be checked."""

    from rapidocr_onnxruntime import RapidOCR

    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    if not config["regions"]:
        raise RuntimeError("No regions configured. Run a calibrate command first.")
    window, _rect, frame = capture_target(config)
    output_dir = resolve_output_dir(config_path, str(config["output_dir"])) / "inspection"
    output_dir.mkdir(parents=True, exist_ok=True)
    captured_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    annotated = frame.copy()
    engine = RapidOCR()
    report_regions: list[dict[str, Any]] = []

    for region_number, region in enumerate(config["regions"], start=1):
        x, y, width, height = normalized_region(region, frame)
        crop = frame[y:y + height, x:x + width]
        calibrated_height = int(region.get("calibration_frame_height", frame.shape[0]))
        row_height = max(
            1,
            round(int(region.get("row_height", 18)) * frame.shape[0] / calibrated_height),
        )
        rows = split_rows(crop, row_height)
        profile = TABLE_PROFILES.get(str(region.get("profile", "")), {})
        columns = tuple(dict(column) for column in profile.get("columns", []))
        header_rows = int(profile.get("header_rows", 0))
        data_rows = rows[header_rows:]
        recognition_images = (
            split_table_cells(tuple(data_rows), columns)
            if columns
            else data_rows
        )
        recognized, elapsed_ms = recognize_row_batches(engine, recognition_images)
        name = str(region["name"])
        row_records: list[dict[str, Any]] = []
        if columns:
            for row_index in range(len(data_rows)):
                fields: dict[str, str] = {}
                confidences: dict[str, float] = {}
                for column_index, column in enumerate(columns):
                    result_index = row_index * len(columns) + column_index
                    text, confidence = recognized[result_index]
                    fields[str(column["name"])] = text.strip()
                    confidences[str(column["name"])] = round(confidence, 4)
                row_records.append({
                    "row_index": row_index,
                    "fields": fields,
                    "confidences": confidences,
                    "text": " | ".join(fields[str(column["name"])] for column in columns),
                    "confidence": min(confidences.values(), default=0.0),
                })
        else:
            row_records = [
                {
                    "row_index": index,
                    "text": text,
                    "confidence": round(confidence, 4),
                }
                for index, (text, confidence) in enumerate(recognized)
            ]
        report_regions.append({
            "name": name,
            "pixel_rect": {"x": x, "y": y, "width": width, "height": height},
            "row_height": row_height,
            "ocr_elapsed_ms": round(elapsed_ms, 2),
            "rows": row_records,
        })
        if columns:
            report_regions[-1]["columns"] = [
                {"name": column["name"], "label": column["label"]}
                for column in columns
            ]

        cv2.rectangle(annotated, (x, y), (x + width, y + height), (0, 255, 0), 2)
        cv2.putText(
            annotated,
            f"ROI {region_number}",
            (x + 4, max(18, y + 18)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )
        for row_index in range(1, len(rows)):
            line_y = y + row_index * row_height
            cv2.line(annotated, (x, line_y), (x + width, line_y), (0, 160, 0), 1)
        for column in columns[:-1]:
            line_x = x + round(float(column["right"]) * width)
            cv2.line(annotated, (line_x, y), (line_x, y + height), (0, 160, 0), 1)

        print(f"\n[{name}] rows={len(rows)}  OCR={elapsed_ms:.1f} ms")
        if columns:
            print("  " + " | ".join(str(column["label"]) for column in columns))
        for item in row_records:
            print(
                f"  row={item['row_index']:02d}  conf={item['confidence']:.3f}  "
                f"{item['text']}",
                flush=True,
            )

    image_path = output_dir / f"inspection_{stamp}.png"
    report_path = output_dir / f"inspection_{stamp}.json"
    cv2.imencode(".png", annotated)[1].tofile(image_path)
    report = {
        "captured_at": captured_at,
        "window_title": window.title,
        "image": image_path.name,
        "regions": report_regions,
    }
    with report_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(f"\nAnnotated screen: {image_path}")
    print(f"OCR report:       {report_path}")
    if args.show:
        cv2.imshow("Market screen OCR inspection - press any key to close", annotated)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    return 0


def command_windows(_args: argparse.Namespace) -> int:
    for window in visible_windows():
        print(f"{window.hwnd:>10}  {window.title}")
    return 0


def command_snapshot(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    window, rect, frame = capture_target(config)
    output_dir = resolve_output_dir(config_path, str(config["output_dir"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    target = output_dir / f"window_{stamp}.png"
    cv2.imencode(".png", frame)[1].tofile(target)
    print(f"Captured {window.title!r} client area {rect.width}x{rect.height}: {target}")
    return 0


def command_calibrate(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    window, _rect, frame = capture_target(config)
    selection = cv2.selectROI(
        f"Select {args.name} - Enter confirms, Esc cancels",
        frame,
        showCrosshair=True,
        fromCenter=False,
    )
    cv2.destroyAllWindows()
    x, y, width, height = (int(value) for value in selection)
    if width <= 0 or height <= 0:
        print("Calibration cancelled; config was not changed.")
        return 1
    frame_height, frame_width = frame.shape[:2]
    profile_name = args.profile
    detected_header = ""
    if profile_name is None:
        profile_name, detected_header = infer_table_profile(
            frame[y:y + height, x:x + width], args.row_height,
        )
    region = {
        "name": args.name,
        "x": x / frame_width,
        "y": y / frame_height,
        "width": width / frame_width,
        "height": height / frame_height,
        "row_height": args.row_height,
        "calibration_frame_height": frame_height,
        "newest_at": args.newest_at,
        "max_rows_per_event": args.max_rows,
    }
    if profile_name:
        region["profile"] = profile_name
    regions = [item for item in config["regions"] if item.get("name") != args.name]
    regions.append(region)
    config["regions"] = regions
    save_config(config_path, config)
    print(
        f"Saved region {args.name!r} for {window.title!r}: "
        f"x={x}, y={y}, width={width}, height={height}"
    )
    if profile_name:
        print(f"Table profile: {profile_name}")
    elif detected_header:
        print(f"No known table profile matched header OCR: {detected_header}")
    return 0


def command_run(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    if not config["regions"]:
        raise RuntimeError("No regions configured. Run the calibrate command first.")

    window = find_window(str(config["window_title_contains"]))
    capture = ScreenCapture()
    output_dir = resolve_output_dir(config_path, str(config["output_dir"]))
    worker = OCRWorker(output_dir, bool(config["save_changed_images"]))
    worker.start()

    target_fps = float(config["target_fps"])
    frame_period = 1.0 / target_fps
    pixel_delta = int(config["pixel_delta"])
    threshold = float(config["changed_fraction"])
    minimum_interval = float(config["min_ocr_interval_ms"]) / 1000
    previous: dict[str, np.ndarray] = {}
    previous_rows: dict[str, list[np.ndarray]] = {}
    last_submitted: dict[str, float] = {}
    frames = submitted = 0
    stats_started = time.perf_counter()
    rect = client_rect(window)
    rect_checked = 0.0

    print(f"Reading {window.title!r} at target {target_fps:g} FPS. Press Ctrl+C to stop.")
    try:
        while True:
            loop_started = time.perf_counter()
            if worker.error is not None:
                raise RuntimeError("OCR worker stopped") from worker.error
            if loop_started - rect_checked >= 1.0:
                if ctypes.windll.user32.IsIconic(window.hwnd):
                    raise RuntimeError("Target window was minimized")
                rect = client_rect(window)
                rect_checked = loop_started
            frame = capture.grab(rect)
            frames += 1

            for region in config["regions"]:
                x, y, width, height = normalized_region(region, frame)
                crop = frame[y:y + height, x:x + width]
                name = str(region["name"])
                changed = change_fraction(previous.get(name), crop, pixel_delta)
                previous[name] = crop.copy()
                now = time.perf_counter()
                if changed < threshold:
                    continue
                if now - last_submitted.get(name, 0.0) < minimum_interval:
                    continue
                calibrated_height = int(region.get("calibration_frame_height", frame.shape[0]))
                scaled_row_height = max(
                    1,
                    round(int(region.get("row_height", 18)) * frame.shape[0] / calibrated_height),
                )
                rows = split_rows(crop, scaled_row_height)
                profile = TABLE_PROFILES.get(str(region.get("profile", "")), {})
                header_rows = int(profile.get("header_rows", 0))
                if header_rows:
                    rows = rows[header_rows:]
                newest_at = str(region.get("newest_at", "bottom"))
                indices = new_row_indices(
                    previous_rows.get(name), rows, newest_at=newest_at,
                )
                previous_rows[name] = [row.copy() for row in rows]
                indices = select_newest(
                    indices,
                    maximum=int(region.get("max_rows_per_event", 20)),
                    newest_at=newest_at,
                )
                if not indices:
                    continue
                job = OCRJob(
                    captured_at=datetime.now().astimezone().isoformat(timespec="milliseconds"),
                    window_title=window.title,
                    region_name=name,
                    changed_fraction=changed,
                    row_indices=tuple(indices),
                    row_images=tuple(rows[index].copy() for index in indices),
                    columns=tuple(dict(column) for column in profile.get("columns", [])),
                    source_image=crop.copy(),
                )
                worker.submit(job)
                submitted += 1
                last_submitted[name] = now

            now = time.perf_counter()
            if now - stats_started >= 2.0:
                actual_fps = frames / (now - stats_started)
                print(
                    f"fps={actual_fps:5.1f}  submitted={submitted:4d}  "
                    f"ocr_done={worker.processed:4d}  backlog={worker.backlog:4d}",
                    flush=True,
                )
                frames = submitted = 0
                stats_started = now

            remaining = frame_period - (time.perf_counter() - loop_started)
            if remaining > 0:
                time.sleep(remaining)
    except KeyboardInterrupt:
        print("Stopping reader...")
    finally:
        capture.close()
        worker.stop()
    if worker.error is not None:
        raise RuntimeError("OCR worker failed") from worker.error
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Low-latency, read-only market screen OCR")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to settings JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("windows", help="List visible top-level windows").set_defaults(func=command_windows)
    subparsers.add_parser("snapshot", help="Capture the target window once").set_defaults(func=command_snapshot)
    inspect = subparsers.add_parser(
        "inspect", help="OCR every configured row once for an after-close check",
    )
    inspect.add_argument(
        "--show", action="store_true",
        help="Show the annotated screenshot until a key is pressed",
    )
    inspect.set_defaults(func=command_inspect)
    calibrate = subparsers.add_parser("calibrate", help="Interactively select one OCR region")
    calibrate.add_argument("--name", required=True, help="Stable region name")
    calibrate.add_argument(
        "--row-height", type=int, default=18,
        help="One visible table-row height in pixels (default: 18)",
    )
    calibrate.add_argument(
        "--newest-at", choices=("top", "bottom"), default="bottom",
        help="Which edge receives the newest market rows (default: bottom)",
    )
    calibrate.add_argument(
        "--max-rows", type=int, default=20,
        help="Maximum new rows recognized per change event (default: 20)",
    )
    calibrate.add_argument(
        "--profile", choices=tuple(TABLE_PROFILES),
        help="Known table layout used for structured column output",
    )
    calibrate.set_defaults(func=command_calibrate)
    subparsers.add_parser("run", help="Continuously capture changed regions and OCR them").set_defaults(func=command_run)
    return parser


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    _enable_dpi_awareness()
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
