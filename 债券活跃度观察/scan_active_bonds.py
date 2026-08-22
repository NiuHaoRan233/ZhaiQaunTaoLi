from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import shutil
import statistics
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG = ROOT / "config.json"
CATEGORY_ORDER = ("信用债",)
DEFAULT_SCORE_WEIGHTS = {
    "amount": 0.30,
    "transaction_count": 0.30,
    "price_range": 0.30,
    "active_intervals": 0.10,
}


@dataclass
class BondRow:
    code: str
    market: int
    name: str
    category: str
    issuer_hint: str = ""
    price: float = 0.0
    change_pct: float = 0.0
    volume_hands: float = 0.0
    amount_cny: float = 0.0
    previous_close: float = 0.0
    history: list[tuple[str, float]] = field(default_factory=list)
    activity_history: list[tuple[str, int]] = field(default_factory=list)
    transaction_history: list[tuple[str, int]] = field(default_factory=list)
    price_range_history: list[tuple[str, float]] = field(default_factory=list)
    today_active_intervals: int = 0
    recent_average_active_intervals: float | None = None
    today_transaction_count: int = 0
    recent_average_transaction_count: float | None = None
    today_price_range: float = 0.0
    recent_average_price_range: float | None = None
    today_trade_value_ratio: float | None = None
    recent_trade_value_ratio: float | None = None
    today_percentile: float | None = None
    recent_percentile: float | None = None
    previous_average_cny: float | None = None
    recent_average_cny: float | None = None
    today_vs_previous: float | None = None
    continuity_days: int | None = None
    benchmark_ratio: float | None = None
    score: float = 0.0
    label: str = "观察"

    @property
    def exchange(self) -> str:
        return "SH" if self.market == 1 else "SZ"

    @property
    def full_code(self) -> str:
        return f"{self.code}.{self.exchange}"


def number(value: Any, default: float = 0.0) -> float:
    if value in (None, "", "-"):
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def classify_bond(code: str, name: str) -> str | None:
    upper_name = name.upper().replace(" ", "")
    if not code or not name:
        return None
    if code.startswith(("204", "1318")):
        return None
    if any(token in upper_name for token in ("GC00", "R-00", "Ｒ-00", "回购", "ETF", "LOF", "基金")):
        return None
    if code.startswith(("132", "117")) or upper_name.endswith("EB") or "可交换" in name:
        return "可交换债"
    if code.startswith(("110", "111", "113", "118", "123", "127", "128")) or "转债" in name:
        return "可转债"
    rate_prefixes = ("018", "019", "020", "102", "23")
    rate_tokens = ("国债", "特国", "地方债", "国开", "农发", "进出", "政金债", "贴债")
    if code.startswith(rate_prefixes) or any(token in name for token in rate_tokens):
        return "利率债"
    return "信用债"


def infer_issuer_hint(name: str) -> str:
    compact = name.upper().replace(" ", "")
    real_estate_tokens = (
        "地产", "房产", "置业", "置地", "万科", "金地", "龙湖", "绿城", "碧桂园",
        "首开", "越秀", "华发", "滨江", "金茂", "招蛇", "保利发", "新城控",
    )
    urban_investment_tokens = (
        "城投", "城建", "城发", "城资", "城运", "交投", "轨交", "轨道", "铁投",
        "路桥", "文旅", "旅投", "水投", "产投", "经开", "高新投", "园区",
    )
    if any(token in compact for token in real_estate_tokens):
        return "地产线索"
    if any(token in compact for token in urban_investment_tokens):
        return "城投线索"
    return "其他信用债"


def split_full_code(full_code: str) -> tuple[str, str]:
    code, dot, exchange = full_code.upper().partition(".")
    if not dot or exchange not in {"SH", "SZ"} or not code.isdigit():
        raise ValueError(f"无效代码格式：{full_code}，应类似132026.SH")
    return code, exchange


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_qmt_port(config: dict[str, Any]) -> int:
    if config.get("qmt_port") is not None:
        return int(config["qmt_port"])
    project_config = (ROOT / str(config.get("project_config", "../config.toml"))).resolve()
    with project_config.open("rb") as handle:
        payload = tomllib.load(handle)
    return int(payload["qmt"]["port"])


def connect_qmt(port: int) -> None:
    from xtquant import xtdata

    xtdata.enable_hello = False
    client = xtdata.connect(port=port)
    if client is None or not client.is_connected():
        raise ConnectionError(f"MiniQMT连接失败，端口：{port}")


def row_from_qmt(code: str, detail: dict[str, Any], tick: dict[str, Any]) -> BondRow | None:
    raw_code, exchange = split_full_code(code)
    name = str(detail.get("InstrumentName") or "").strip()
    category = classify_bond(raw_code, name)
    if category is None:
        return None
    previous_close = number(tick.get("lastClose"))
    price = number(tick.get("lastPrice"))
    volume = number(tick.get("volume"))
    pvolume = number(tick.get("pvolume"))
    return BondRow(
        code=raw_code,
        market=1 if exchange == "SH" else 0,
        name=name,
        category=category,
        issuer_hint=infer_issuer_hint(name) if category == "信用债" else "满分标尺",
        price=price,
        change_pct=(price / previous_close - 1.0) * 100.0 if previous_close > 0 else 0.0,
        volume_hands=volume if exchange == "SH" else pvolume / 10.0 if pvolume > 0 else volume / 10.0,
        amount_cny=number(tick.get("amount")),
        previous_close=previous_close,
    )


def gather_qmt_rows(config: dict[str, Any], target_date: str) -> list[BondRow]:
    from xtquant import xtdata

    sector = str(config.get("qmt_bond_sector", "沪深债券"))
    universe = xtdata.get_stock_list_in_sector(sector)
    if not universe:
        raise RuntimeError(f"QMT板块“{sector}”没有返回债券代码")
    ticks = xtdata.get_full_tick(universe)
    benchmark_codes = set(config["full_score_benchmarks"])
    target_compact = target_date.replace("-", "")
    candidate_codes: set[str] = set(benchmark_codes)
    for full_code, tick in ticks.items():
        if not isinstance(tick, dict):
            continue
        if not str(tick.get("timetag", "")).startswith(target_compact):
            continue
        if number(tick.get("amount")) > 0:
            candidate_codes.add(full_code)

    missing_benchmarks = benchmark_codes.difference(ticks)
    if missing_benchmarks:
        ticks.update(xtdata.get_full_tick(sorted(missing_benchmarks)))

    rows: list[BondRow] = []
    for full_code in sorted(candidate_codes):
        tick = ticks.get(full_code)
        if not isinstance(tick, dict):
            continue
        detail = xtdata.get_instrument_detail(full_code, False) or {}
        row = row_from_qmt(full_code, detail, tick)
        if row is None:
            continue
        if row.category == "信用债" or full_code in benchmark_codes:
            rows.append(row)
    return rows


def _tick_frame_history(
    frame: Any,
) -> tuple[
    list[tuple[str, float]],
    list[tuple[str, int]],
    list[tuple[str, int]],
    list[tuple[str, float]],
]:
    if frame is None or frame.empty or "amount" not in frame.columns:
        return [], [], [], []
    amount_by_day: dict[str, float] = {}
    intervals_by_day: dict[str, set[str]] = {}
    previous_by_day: dict[str, float] = {}
    transactions_by_day: dict[str, int] = {}
    high_by_day: dict[str, float] = {}
    low_by_day: dict[str, float] = {}
    for index, payload in frame.sort_index().iterrows():
        stamp = str(index)
        if len(stamp) < 12:
            continue
        day = stamp[:8]
        current = number(payload.get("amount"))
        previous = previous_by_day.get(day, 0.0)
        if current > previous:
            hour = stamp[8:10]
            minute = int(stamp[10:12])
            bucket = f"{day}{hour}{minute // 5 * 5:02d}"
            intervals_by_day.setdefault(day, set()).add(bucket)
        previous_by_day[day] = current
        amount_by_day[day] = max(amount_by_day.get(day, 0.0), current)
        transactions_by_day[day] = max(
            transactions_by_day.get(day, 0),
            int(number(payload.get("transactionNum"))),
        )
        high = number(payload.get("high"))
        low = number(payload.get("low"))
        if high > 0:
            high_by_day[day] = max(high_by_day.get(day, 0.0), high)
        if low > 0:
            low_by_day[day] = min(low_by_day.get(day, low), low)
    amount_history = [(day, amount_by_day[day]) for day in sorted(amount_by_day)]
    activity_history = [(day, len(intervals_by_day.get(day, set()))) for day in sorted(amount_by_day)]
    transaction_history = [(day, transactions_by_day.get(day, 0)) for day in sorted(amount_by_day)]
    price_range_history = [
        (day, max(0.0, high_by_day.get(day, 0.0) - low_by_day.get(day, 0.0)))
        for day in sorted(amount_by_day)
    ]
    return amount_history, activity_history, transaction_history, price_range_history


def attach_qmt_tick_histories(rows: list[BondRow], target_date: str, recent_days: int) -> None:
    from xtquant import xtdata

    codes = [row.full_code for row in rows]
    end_date = datetime.strptime(target_date, "%Y-%m-%d").date()
    start_date = end_date - timedelta(days=max(14, recent_days * 3))
    start_text = start_date.strftime("%Y%m%d")
    end_text = end_date.strftime("%Y%m%d")
    xtdata.download_history_data2(
        codes,
        "tick",
        start_time=start_text,
        end_time=end_text,
        incrementally=True,
    )
    frames = xtdata.get_market_data_ex(
        [],
        codes,
        period="tick",
        start_time=start_text,
        end_time=end_text,
        count=-1,
        dividend_type="none",
        fill_data=False,
    )
    for row in rows:
        (
            row.history,
            row.activity_history,
            row.transaction_history,
            row.price_range_history,
        ) = _tick_frame_history(frames.get(row.full_code))


def percentile_map(rows: Iterable[BondRow], attribute: str) -> dict[str, float]:
    rows = list(rows)
    values = sorted(number(getattr(row, attribute)) for row in rows)
    if not values:
        return {}
    return {
        row.full_code: 100.0 * bisect.bisect_right(values, number(getattr(row, attribute))) / len(values)
        for row in rows
    }


def normalized_score_weights(score_weights: dict[str, float] | None) -> dict[str, float]:
    weights = dict(DEFAULT_SCORE_WEIGHTS if score_weights is None else score_weights)
    if set(weights) != set(DEFAULT_SCORE_WEIGHTS):
        raise ValueError(f"交易价值权重必须包含：{', '.join(DEFAULT_SCORE_WEIGHTS)}")
    if any(number(value) < 0 for value in weights.values()):
        raise ValueError("交易价值权重不能为负数")
    total = sum(number(value) for value in weights.values())
    if total <= 0:
        raise ValueError("交易价值权重合计必须大于0")
    return {key: number(value) / total for key, value in weights.items()}


def _positive_reference(rows: Iterable[BondRow], attribute: str) -> float | None:
    values = [number(getattr(row, attribute)) for row in rows]
    positive = [value for value in values if value > 0]
    return min(positive) if positive else None


def _weighted_trade_value_ratio(
    values: dict[str, float],
    references: dict[str, float | None],
    weights: dict[str, float],
) -> float:
    available = [key for key in weights if references.get(key) and number(references[key]) > 0]
    available_weight = sum(weights[key] for key in available)
    if available_weight <= 0:
        return 0.0
    return sum(
        weights[key] * min(max(number(values.get(key)) / number(references[key]), 0.0), 1.0)
        for key in available
    ) / available_weight


def calculate_metrics(
    rows: list[BondRow],
    target_date: str,
    recent_days: int,
    benchmark_codes: set[str],
    score_weights: dict[str, float] | None = None,
) -> None:
    weights = normalized_score_weights(score_weights)
    target_compact = target_date.replace("-", "")
    benchmark_rows = [row for row in rows if row.full_code in benchmark_codes]
    calendar_dates = sorted(
        {day for row in benchmark_rows for day, _ in row.activity_history if day <= target_compact}
    )
    recent_dates = calendar_dates[-recent_days:]
    previous_dates = [day for day in calendar_dates if day < target_compact][-recent_days:]
    if not recent_dates:
        raise ValueError("江铜、三峡满分参照均无QMT历史tick，无法计算活跃度")

    for row in rows:
        amount_map = dict(row.history)
        activity_map = dict(row.activity_history)
        transaction_map = dict(row.transaction_history)
        price_range_map = dict(row.price_range_history)
        recent_amounts = [amount_map.get(day, 0.0) for day in recent_dates]
        previous_amounts = [amount_map.get(day, 0.0) for day in previous_dates]
        recent_intervals = [activity_map.get(day, 0) for day in recent_dates]
        recent_transactions = [transaction_map.get(day, 0) for day in recent_dates]
        recent_price_ranges = [price_range_map.get(day, 0.0) for day in recent_dates]
        row.today_active_intervals = activity_map.get(target_compact, 0)
        row.recent_average_active_intervals = statistics.fmean(recent_intervals)
        row.today_transaction_count = transaction_map.get(target_compact, 0)
        row.recent_average_transaction_count = statistics.fmean(recent_transactions)
        row.today_price_range = price_range_map.get(target_compact, 0.0)
        row.recent_average_price_range = statistics.fmean(recent_price_ranges)
        row.recent_average_cny = statistics.fmean(recent_amounts)
        threshold = row.recent_average_cny * 0.5
        row.continuity_days = sum(amount >= threshold and amount > 0 for amount in recent_amounts)
        if previous_amounts:
            row.previous_average_cny = statistics.fmean(previous_amounts)
            if row.previous_average_cny > 0:
                row.today_vs_previous = row.amount_cny / row.previous_average_cny

    credit_rows = [row for row in rows if row.category == "信用债"]
    today_percentiles = percentile_map(credit_rows, "amount_cny")
    recent_percentiles = percentile_map(credit_rows, "recent_average_cny")
    for row in credit_rows:
        row.today_percentile = today_percentiles.get(row.full_code)
        row.recent_percentile = recent_percentiles.get(row.full_code)

    today_references = {
        "amount": _positive_reference(benchmark_rows, "amount_cny"),
        "transaction_count": _positive_reference(benchmark_rows, "today_transaction_count"),
        "price_range": _positive_reference(benchmark_rows, "today_price_range"),
        "active_intervals": _positive_reference(benchmark_rows, "today_active_intervals"),
    }
    recent_references = {
        "amount": _positive_reference(benchmark_rows, "recent_average_cny"),
        "transaction_count": _positive_reference(benchmark_rows, "recent_average_transaction_count"),
        "price_range": _positive_reference(benchmark_rows, "recent_average_price_range"),
        "active_intervals": _positive_reference(benchmark_rows, "recent_average_active_intervals"),
    }
    if not any(today_references.values()) and not any(recent_references.values()):
        raise ValueError("江铜、三峡满分参照均无有效QMT交易价值数据，无法计算活跃度")

    for row in rows:
        row.today_trade_value_ratio = _weighted_trade_value_ratio(
            {
                "amount": row.amount_cny,
                "transaction_count": row.today_transaction_count,
                "price_range": row.today_price_range,
                "active_intervals": row.today_active_intervals,
            },
            today_references,
            weights,
        )
        row.recent_trade_value_ratio = _weighted_trade_value_ratio(
            {
                "amount": number(row.recent_average_cny),
                "transaction_count": number(row.recent_average_transaction_count),
                "price_range": number(row.recent_average_price_range),
                "active_intervals": number(row.recent_average_active_intervals),
            },
            recent_references,
            weights,
        )
        row.benchmark_ratio = max(row.today_trade_value_ratio, row.recent_trade_value_ratio)
        if row.full_code in benchmark_codes:
            row.today_trade_value_ratio = 1.0
            row.recent_trade_value_ratio = 1.0
            row.benchmark_ratio = 1.0
            row.score = 100.0
        else:
            row.benchmark_ratio = min(row.benchmark_ratio, 0.99)
            row.score = 100.0 * row.benchmark_ratio

        if row.full_code in benchmark_codes:
            row.label = "满分参照"
        elif row.score >= 80.0:
            row.label = "非常活跃"
        elif row.score >= 60.0:
            row.label = "较活跃"
        elif row.score >= 40.0:
            row.label = "相对活跃"
        else:
            row.label = "观察"


def format_amount(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value) >= 100_000_000:
        return f"{value / 100_000_000:.2f}亿"
    if abs(value) >= 10_000:
        return f"{value / 10_000:.0f}万"
    return f"{value:.0f}"


def csv_record(row: BondRow) -> dict[str, Any]:
    return {
        "代码": row.full_code,
        "名称": row.name,
        "类别": row.category,
        "主体线索": row.issuer_hint,
        "活跃标签": row.label,
        "综合分": f"{row.score:.2f}",
        "最新价": f"{row.price:.4f}",
        "涨跌幅%": f"{row.change_pct:.3f}",
        "QMT当日成交额元": f"{row.amount_cny:.2f}",
        "前5日均额元": "" if row.previous_average_cny is None else f"{row.previous_average_cny:.2f}",
        "近5日均额元": "" if row.recent_average_cny is None else f"{row.recent_average_cny:.2f}",
        "当日成交笔数": row.today_transaction_count,
        "近5日平均成交笔数": "" if row.recent_average_transaction_count is None else f"{row.recent_average_transaction_count:.2f}",
        "当日成交价差元": f"{row.today_price_range:.4f}",
        "近5日平均成交价差元": "" if row.recent_average_price_range is None else f"{row.recent_average_price_range:.4f}",
        "当日活跃时段": row.today_active_intervals,
        "近5日平均活跃时段": "" if row.recent_average_active_intervals is None else f"{row.recent_average_active_intervals:.2f}",
        "今日交易价值分": "" if row.today_trade_value_ratio is None else f"{100.0 * row.today_trade_value_ratio:.2f}",
        "近5日交易价值分": "" if row.recent_trade_value_ratio is None else f"{100.0 * row.recent_trade_value_ratio:.2f}",
        "今日除以前5日": "" if row.today_vs_previous is None else f"{row.today_vs_previous:.4f}",
        "连续活跃天数": "" if row.continuity_days is None else row.continuity_days,
        "相对满分参照": "" if row.benchmark_ratio is None else f"{row.benchmark_ratio:.6f}",
        "当日组内分位": "" if row.today_percentile is None else f"{row.today_percentile:.2f}",
        "近日组内分位": "" if row.recent_percentile is None else f"{row.recent_percentile:.2f}",
    }


def write_csv(path: Path, rows: list[BondRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [csv_record(row) for row in rows]
    if not records:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def load_latest_snapshot(snapshot_dir: Path) -> list[BondRow]:
    paths = sorted(snapshot_dir.glob("*.csv"), reverse=True)
    if not paths:
        raise RuntimeError("本地还没有QMT快照，无法使用--offline")
    rows: list[BondRow] = []
    with paths[0].open("r", encoding="utf-8-sig", newline="") as handle:
        for item in csv.DictReader(handle):
            code, exchange = split_full_code(item["代码"])
            amount = item.get("QMT当日成交额元", item.get("当日成交额元"))
            rows.append(
                BondRow(
                    code=code,
                    market=1 if exchange == "SH" else 0,
                    name=item["名称"],
                    category=item["类别"],
                    issuer_hint=item.get("主体线索") or infer_issuer_hint(item["名称"]),
                    price=number(item.get("最新价")),
                    change_pct=number(item.get("涨跌幅%")),
                    amount_cny=number(amount),
                    previous_average_cny=number(item.get("前5日均额元")) or None,
                    recent_average_cny=number(item.get("近5日均额元")) or None,
                    today_transaction_count=int(number(item.get("当日成交笔数"), 0)),
                    recent_average_transaction_count=number(item.get("近5日平均成交笔数")) or None,
                    today_price_range=number(item.get("当日成交价差元")),
                    recent_average_price_range=number(item.get("近5日平均成交价差元")) or None,
                    today_active_intervals=int(number(item.get("当日活跃时段"), 0)),
                    recent_average_active_intervals=number(item.get("近5日平均活跃时段")) or None,
                    today_trade_value_ratio=(number(item.get("今日交易价值分")) / 100.0) or None,
                    recent_trade_value_ratio=(number(item.get("近5日交易价值分")) / 100.0) or None,
                    today_vs_previous=number(item.get("今日除以前5日")) or None,
                    continuity_days=int(number(item.get("连续活跃天数"), 0)),
                    benchmark_ratio=number(item.get("相对满分参照")) or None,
                    score=number(item.get("综合分")),
                    label=item.get("活跃标签") or "观察",
                )
            )
    return rows


def ranked_credit_rows(rows: list[BondRow]) -> list[BondRow]:
    return sorted(
        (row for row in rows if row.category == "信用债"),
        key=lambda row: (row.score, row.amount_cny),
        reverse=True,
    )


def ranked_benchmark_rows(rows: list[BondRow], benchmark_codes: set[str]) -> list[BondRow]:
    return sorted(
        (row for row in rows if row.full_code in benchmark_codes),
        key=lambda row: (row.score, row.amount_cny),
        reverse=True,
    )


def report_rows(
    rows: list[BondRow],
    top: int,
    benchmark_codes: set[str] | None = None,
) -> list[BondRow]:
    benchmark_codes = benchmark_codes or {row.full_code for row in rows if row.label == "满分参照"}
    selected = {row.full_code: row for row in ranked_credit_rows(rows)[:top]}
    for row in ranked_benchmark_rows(rows, benchmark_codes):
        selected[row.full_code] = row
    for issuer_hint in ("地产线索", "城投线索"):
        ranked = [row for row in ranked_credit_rows(rows) if row.issuer_hint == issuer_hint]
        for row in ranked[: min(top, 5)]:
            selected[row.full_code] = row
    return sorted(selected.values(), key=lambda row: (row.score, row.amount_cny), reverse=True)


def _append_report_table(lines: list[str], rows: list[BondRow]) -> None:
    lines.extend(
        [
            "| 标签 | 代码 | 名称 | 类别/线索 | 综合分 | 今日分 | 近5日分 | 场内成交额 | 成交笔数 | 成交价差 | 活跃时段 |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    if not rows:
        lines.append("| — | — | 暂无有效QMT成交数据 | — | — | — | — | — | — | — | — |")
    for row in rows:
        category_hint = row.issuer_hint if row.category == "信用债" else row.category
        lines.append(
            f"| {row.label} | `{row.full_code}` | {row.name} | {category_hint} | {row.score:.1f} | "
            f"{100.0 * number(row.today_trade_value_ratio):.1f} | "
            f"{100.0 * number(row.recent_trade_value_ratio):.1f} | {format_amount(row.amount_cny)} | "
            f"{row.today_transaction_count} | {row.today_price_range:.3f} | {row.today_active_intervals} |"
        )
    lines.append("")


def write_markdown(
    path: Path,
    rows: list[BondRow],
    target_date: str,
    generated_at: datetime,
    top: int,
    offline: bool,
    benchmark_codes: set[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {target_date} QMT机构信用债活跃观察",
        "",
        f"生成时间：{generated_at:%Y-%m-%d %H:%M:%S}（Asia/Shanghai本机时间）  ",
        f"数据状态：{'本地QMT快照，不代表实时' if offline else 'MiniQMT xtdata，只读'}",
        f"满分参照：{', '.join(sorted(benchmark_codes))}；两只参照固定100分并保留在总榜",
        "",
        "> 总榜包含两只满分可交换债参照及QMT当天累计成交额大于0的信用债；其余可转债、可交换债和利率债仍排除。账户能否买入须在QMT终端逐券核验。",
        "",
        "## 交易价值活跃榜（含满分参照）",
        "",
    ]
    main_rows = ranked_benchmark_rows(rows, benchmark_codes) + ranked_credit_rows(rows)[:top]
    main_rows.sort(key=lambda row: (row.score, row.amount_cny), reverse=True)
    _append_report_table(lines, main_rows)

    sector_rows: list[BondRow] = []
    for issuer_hint in ("地产线索", "城投线索"):
        ranked = [row for row in ranked_credit_rows(rows) if row.issuer_hint == issuer_hint]
        sector_rows.extend(ranked[: min(top, 5)])
    lines.extend(["## 地产、城投线索专项", ""])
    _append_report_table(lines, sector_rows)

    lines.extend(
        [
            "## 怎么看这张表",
            "",
            "- 候选代码、简称、当日累计成交额和历史tick全部来自当前MiniQMT；不使用新浪或其他第三方行情。",
            "- `综合分`按场内成交额30%、成交笔数30%、实际成交价差30%、五分钟活跃时段10%计算；取今日与近5日中较高者。",
            "- 每个维度相对江铜、三峡中较低的有效值计分并封顶；除两只人工确认参照外，其他债最高99分。",
            "- 一个`活跃时段`是QMT历史tick累计成交额至少增加过一次的五分钟区间；单笔大宗成交只计一个时段。",
            "- `成交笔数`直接读取QMT的累计`transactionNum`；`成交价差`是当日累计最高成交价减最低成交价。",
            "- `主体线索`只由简称推断；发行人性质、合格投资者限制和账户权限必须在QMT终端核验。",
            "- 报告是只读观察名单，不是买卖建议，不调用`xttrader`，不会发送委托。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用MiniQMT扫描当日及近日活跃的机构信用债")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="配置JSON路径")
    parser.add_argument("--date", default=date.today().isoformat(), help="报告日期 YYYY-MM-DD")
    parser.add_argument("--top", type=int, help="报告显示数量")
    parser.add_argument("--output-dir", type=Path, default=ROOT, help="运行数据与报告根目录")
    parser.add_argument("--offline", action="store_true", help="只用最近本地QMT快照")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    try:
        datetime.strptime(args.date, "%Y-%m-%d")
        if not args.offline and args.date != date.today().isoformat():
            raise ValueError("QMT联网扫描只支持今天；历史日请读取当日已保存快照")
        config = load_config(args.config)
        top = args.top or int(config["report_top_per_category"])
        if top <= 0:
            raise ValueError("--top必须大于0")

        snapshot_dir = args.output_dir / "data" / "snapshots"
        report_dir = args.output_dir / "reports"
        generated_at = datetime.now()
        if args.offline:
            print("正在读取最近一次本地 QMT 快照...", flush=True)
            rows = load_latest_snapshot(snapshot_dir)
        else:
            port = resolve_qmt_port(config)
            print(f"正在连接 MiniQMT（端口 {port}）...", flush=True)
            connect_qmt(port)
            print("连接成功，正在读取当日债券快照...", flush=True)
            rows = gather_qmt_rows(config, args.date)
            if not rows:
                raise RuntimeError("QMT没有返回当天有效信用债行情")
            print(f"已取得 {len(rows)} 只候选及参考债，正在更新历史 tick...", flush=True)
            attach_qmt_tick_histories(rows, args.date, int(config["recent_trading_days"]))
            print("历史 tick 更新完成，正在计算活跃度并生成报告...", flush=True)
            calculate_metrics(
                rows,
                args.date,
                int(config["recent_trading_days"]),
                benchmark_codes=set(config["full_score_benchmarks"]),
                score_weights=config.get("score_weights"),
            )

        benchmark_codes = set(config["full_score_benchmarks"])
        selected = report_rows(rows, top, benchmark_codes=benchmark_codes)
        stamp = generated_at.strftime("%Y%m%d_%H%M%S")
        snapshot_path = snapshot_dir / f"{args.date}.csv"
        detail_path = report_dir / f"{args.date}_{stamp}.csv"
        markdown_path = report_dir / f"{args.date}_{stamp}.md"
        if not args.offline:
            write_csv(snapshot_path, rows)
        write_csv(detail_path, selected)
        write_markdown(
            markdown_path,
            rows,
            args.date,
            generated_at,
            top,
            args.offline,
            benchmark_codes=benchmark_codes,
        )
        latest_path = report_dir / "latest.md"
        shutil.copyfile(markdown_path, latest_path)
        credit_count = sum(row.category == "信用债" for row in rows)
        print(f"QMT扫描完成：当天有成交的信用债{credit_count}只，报告显示前{top}只")
        print(f"最新报告：{latest_path}")
        print(f"明细CSV：{detail_path}")
        return 0
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
