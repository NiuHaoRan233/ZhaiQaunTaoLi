from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal

import numpy as np


PRIORITY_MODEL_ID = "anchored_liquidity_priority_v0_1_candidate"
QUEUE_MODEL_ID = "anchored_liquidity_queue_v0_1_candidate"
MODEL_IDS = {"priority": PRIORITY_MODEL_ID, "queue": QUEUE_MODEL_ID}

DEFAULT_CODES = (
    "019547.SH",
    "511220.SH",
    "551060.SH",
    "551300.SH",
    "159113.SZ",
    "551800.SH",
    "127025.SZ",
    "128135.SZ",
)

Mode = Literal["priority", "queue"]


@dataclass(frozen=True)
class InstrumentProfile:
    category: str
    lot_size: int
    volume_multiplier: int
    tick_size: float = 0.001
    stability_seconds: int = 15
    maximum_book_drift_ticks: int = 6
    minimum_spread_grid: tuple[int, ...] = (4, 6, 8, 10, 12, 16)


PROFILES = {
    "etf": InstrumentProfile(
        category="etf",
        lot_size=100,
        volume_multiplier=100,
        maximum_book_drift_ticks=6,
        minimum_spread_grid=(4, 6, 8, 10, 12, 16),
    ),
    "treasury": InstrumentProfile(
        category="treasury",
        lot_size=10,
        volume_multiplier=10,
        stability_seconds=30,
        maximum_book_drift_ticks=40,
        minimum_spread_grid=(20, 40, 60, 80, 100, 120),
    ),
    "maturity_cb": InstrumentProfile(
        category="maturity_cb",
        lot_size=10,
        volume_multiplier=10,
        maximum_book_drift_ticks=20,
        minimum_spread_grid=(4, 6, 8, 10, 12, 16, 24),
    ),
}


def profile_for_code(code: str) -> InstrumentProfile:
    if code.startswith(("51", "55", "15")):
        return PROFILES["etf"]
    if code.startswith(("11", "12")):
        return PROFILES["maturity_cb"]
    return PROFILES["treasury"]


@dataclass(frozen=True)
class MarketEvent:
    code: str
    market_date: str
    market_ts_ms: int
    market_time: str
    last_price: float
    bid1: float
    ask1: float
    bid1_units: float
    ask1_units: float
    traded_units: float
    transaction_delta: int
    aggressor: str
    strict_trade: bool

    @property
    def midpoint(self) -> float:
        if self.bid1 > 0 and self.ask1 >= self.bid1:
            return (self.bid1 + self.ask1) / 2.0
        return self.last_price


@dataclass
class PaperOrder:
    model_id: str
    code: str
    market_date: str
    mode: Mode
    side: str
    price: float
    quantity_units: int
    remaining_units: int
    queue_ahead_units: float
    created_ts_ms: int


@dataclass(frozen=True)
class PaperFill:
    model_id: str
    code: str
    market_date: str
    market_ts_ms: int
    market_time: str
    mode: Mode
    side: str
    price: float
    quantity_units: int
    notional_cny: float
    fee_cny: float
    reason: str
    inventory_after_units: int


@dataclass(frozen=True)
class DailyResult:
    model_id: str
    code: str
    name: str
    category: str
    market_date: str
    sample: str
    mode: Mode
    minimum_spread_ticks: int
    one_way_fee_bps: float
    opening_inventory_units: int
    maximum_inventory_units: int
    quote_size_units: int
    fills: int
    completed_turns: int
    buy_units: int
    sell_units: int
    turnover_cny: float
    fees_cny: float
    ending_inventory_units: int
    ending_inventory_deviation_units: int
    marked_pnl_cny: float
    gross_marked_pnl_cny: float
    first_midpoint: float
    final_midpoint: float


@dataclass(frozen=True)
class SelectionResult:
    model_id: str
    category: str
    mode: Mode
    minimum_spread_ticks: int
    training_days: int
    active_training_days: int
    training_mean_net_cny: float
    training_median_net_cny: float
    training_score: float


def _first(value: Any) -> float:
    if isinstance(value, (list, tuple, np.ndarray)) and len(value):
        try:
            return float(value[0])
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def qmt_frame_to_events(
    code: str,
    frame: Any,
    profile: InstrumentProfile | None = None,
) -> list[MarketEvent]:
    profile = profile or profile_for_code(code)
    if frame is None or frame.empty:
        return []
    events: list[MarketEvent] = []
    previous_volume: float | None = None
    previous_transactions: int | None = None
    previous_bid = 0.0
    previous_ask = 0.0
    previous_date = ""
    for index, row in frame.sort_values("time", kind="stable").iterrows():
        ts_ms = int(row.get("time") or 0)
        if ts_ms <= 0:
            continue
        stamp = datetime.fromtimestamp(ts_ms / 1_000).astimezone()
        tag = str(index)
        if len(tag) >= 14 and tag[:8].isdigit():
            market_date = f"{tag[:4]}-{tag[4:6]}-{tag[6:8]}"
            market_time = f"{tag[8:10]}:{tag[10:12]}:{tag[12:14]}"
        else:
            market_date = stamp.date().isoformat()
            market_time = stamp.time().isoformat(timespec="seconds")
        volume = float(row.get("volume") or 0.0)
        transactions = int(row.get("transactionNum") or 0)
        if market_date != previous_date:
            previous_volume = None
            previous_transactions = None
            previous_bid = 0.0
            previous_ask = 0.0
            previous_date = market_date
        volume_delta = max(0.0, volume - previous_volume) if previous_volume is not None else 0.0
        transaction_delta = (
            max(0, transactions - previous_transactions)
            if previous_transactions is not None else 0
        )
        last_price = float(row.get("lastPrice") or 0.0)
        bid1 = _first(row.get("bidPrice"))
        ask1 = _first(row.get("askPrice"))
        aggressor = "none"
        half_tick = profile.tick_size / 2.0
        if volume_delta > 0 and transaction_delta > 0 and last_price > 0:
            if previous_ask > 0 and last_price >= previous_ask - half_tick:
                aggressor = "buy"
            elif previous_bid > 0 and last_price <= previous_bid + half_tick:
                aggressor = "sell"
            elif ask1 > 0 and last_price >= ask1 - half_tick:
                aggressor = "buy"
            elif bid1 > 0 and last_price <= bid1 + half_tick:
                aggressor = "sell"
        events.append(MarketEvent(
            code=code,
            market_date=market_date,
            market_ts_ms=ts_ms,
            market_time=market_time,
            last_price=last_price,
            bid1=bid1,
            ask1=ask1,
            bid1_units=_first(row.get("bidVol")) * profile.volume_multiplier,
            ask1_units=_first(row.get("askVol")) * profile.volume_multiplier,
            traded_units=volume_delta * profile.volume_multiplier,
            transaction_delta=transaction_delta,
            aggressor=aggressor,
            strict_trade=transaction_delta == 1,
        ))
        previous_volume = volume
        previous_transactions = transactions
        previous_bid = bid1
        previous_ask = ask1
    return events


def split_events_by_date(events: Iterable[MarketEvent]) -> dict[str, list[MarketEvent]]:
    result: dict[str, list[MarketEvent]] = defaultdict(list)
    for event in events:
        if "09:30:00" <= event.market_time < "14:57:00":
            result[event.market_date].append(event)
    return dict(sorted(result.items()))


def _round_down(value: float, lot_size: int) -> int:
    return max(0, int(value // lot_size) * lot_size)


def _order_fillable(order: PaperOrder, event: MarketEvent) -> bool:
    if event.aggressor == "sell" and order.side == "buy":
        return event.last_price <= order.price + 1e-9
    if event.aggressor == "buy" and order.side == "sell":
        return event.last_price + 1e-9 >= order.price
    return False


def run_day(
    code: str,
    name: str,
    events: list[MarketEvent],
    *,
    mode: Mode,
    minimum_spread_ticks: int,
    one_way_fee_bps: float,
    sample: str,
    total_capital_cny: float = 270_000.0,
    quote_notional_cny: float = 10_000.0,
    latency_ms: int = 3_000,
    strict_only: bool = True,
) -> tuple[DailyResult, list[PaperFill]]:
    if not events:
        raise ValueError(f"{code} has no events")
    profile = profile_for_code(code)
    model_id = MODEL_IDS[mode]
    first_mid = next((event.midpoint for event in events if event.midpoint > 0), 0.0)
    if first_mid <= 0:
        raise ValueError(f"{code} has no valid opening midpoint")
    opening_inventory = _round_down(
        total_capital_cny / 2.0 / first_mid,
        profile.lot_size,
    )
    if opening_inventory <= 0:
        opening_inventory = profile.lot_size
    maximum_inventory = opening_inventory * 2
    quote_size = max(
        profile.lot_size,
        _round_down(quote_notional_cny / first_mid, profile.lot_size),
    )
    initial_cash = opening_inventory * first_mid
    cash = initial_cash
    inventory = opening_inventory
    orders: dict[str, PaperOrder] = {}
    fills: list[PaperFill] = []
    book_history: deque[MarketEvent] = deque()
    completed_turns = 0
    previous_deviation = 0

    for event in events:
        if not (event.bid1 > 0 and event.ask1 > event.bid1):
            orders.clear()
            continue
        for side in ("buy", "sell"):
            order = orders.get(side)
            if order is None or event.market_ts_ms - order.created_ts_ms < latency_ms:
                continue
            if strict_only and not event.strict_trade:
                continue
            if event.traded_units <= 0 or not _order_fillable(order, event):
                continue
            available = event.traded_units
            if mode == "queue" and order.queue_ahead_units > 0:
                consumed = min(order.queue_ahead_units, available)
                order.queue_ahead_units -= consumed
                available -= consumed
            if available + 1e-9 < profile.lot_size:
                continue
            quantity = min(order.remaining_units, _round_down(available, profile.lot_size))
            if side == "buy":
                affordable = _round_down(
                    cash / (order.price * (1.0 + one_way_fee_bps / 10_000.0)),
                    profile.lot_size,
                )
                quantity = min(quantity, affordable, maximum_inventory - inventory)
            else:
                quantity = min(quantity, inventory)
            quantity = _round_down(quantity, profile.lot_size)
            if quantity <= 0:
                continue
            notional = order.price * quantity
            fee = notional * one_way_fee_bps / 10_000.0
            if side == "buy":
                cash -= notional + fee
                inventory += quantity
            else:
                cash += notional - fee
                inventory -= quantity
            order.remaining_units -= quantity
            fills.append(PaperFill(
                model_id=model_id,
                code=code,
                market_date=event.market_date,
                market_ts_ms=event.market_ts_ms,
                market_time=event.market_time,
                mode=mode,
                side=side,
                price=order.price,
                quantity_units=quantity,
                notional_cny=notional,
                fee_cny=fee,
                reason=("improved_first_position" if mode == "priority" else "visible_queue_depleted"),
                inventory_after_units=inventory,
            ))
            if order.remaining_units <= 0:
                orders.pop(side, None)
            deviation = inventory - opening_inventory
            if previous_deviation != 0 and deviation == 0:
                completed_turns += 1
            previous_deviation = deviation

        book_history.append(event)
        # Keep a full minute of causal book observations, while requiring only
        # ``stability_seconds`` of elapsed evidence.  Sparse instruments may
        # not publish a snapshot on every second; discarding the older anchor
        # immediately would make one harmless intervening update erase an
        # already established stable corridor.
        cutoff = event.market_ts_ms - 60_000
        while book_history and book_history[0].market_ts_ms < cutoff:
            book_history.popleft()
        stable = (
            len(book_history) >= 2
            and book_history[-1].market_ts_ms - book_history[0].market_ts_ms
            >= profile.stability_seconds * 1_000
        )
        if stable:
            bids = [item.bid1 for item in book_history]
            asks = [item.ask1 for item in book_history]
            stable = (
                (max(bids) - min(bids)) / profile.tick_size
                <= profile.maximum_book_drift_ticks + 1e-9
                and (max(asks) - min(asks)) / profile.tick_size
                <= profile.maximum_book_drift_ticks + 1e-9
            )
        spread_ticks = int(round((event.ask1 - event.bid1) / profile.tick_size))
        improvement = 1 if mode == "priority" else 0
        target_bid = round(event.bid1 + improvement * profile.tick_size, 3)
        target_ask = round(event.ask1 - improvement * profile.tick_size, 3)
        capture_ticks = spread_ticks - 2 * improvement
        valid_corridor = (
            stable
            and spread_ticks >= minimum_spread_ticks
            and capture_ticks >= 1
            and target_ask > target_bid
        )
        desired_sides: set[str] = set()
        if valid_corridor:
            if inventory <= opening_inventory and inventory + quote_size <= maximum_inventory:
                desired_sides.add("buy")
            if inventory >= opening_inventory and inventory >= quote_size:
                desired_sides.add("sell")
        for side in ("buy", "sell"):
            if side not in desired_sides:
                orders.pop(side, None)
                continue
            target = target_bid if side == "buy" else target_ask
            existing = orders.get(side)
            if existing is not None and abs(existing.price - target) < profile.tick_size / 2:
                continue
            queue_ahead = 0.0
            if mode == "queue":
                queue_ahead = event.bid1_units if side == "buy" else event.ask1_units
            orders[side] = PaperOrder(
                model_id=model_id,
                code=code,
                market_date=event.market_date,
                mode=mode,
                side=side,
                price=target,
                quantity_units=quote_size,
                remaining_units=quote_size,
                queue_ahead_units=queue_ahead,
                created_ts_ms=event.market_ts_ms,
            )

    final_mid = next((event.midpoint for event in reversed(events) if event.midpoint > 0), first_mid)
    final_equity = cash + inventory * final_mid
    # Measure the maker overlay, not the directional return of the opening
    # inventory.  The correct counterfactual holds the same opening inventory
    # and cash unchanged through the close.  Without this baseline, a no-fill
    # day would incorrectly report the bond's own price move as maker P&L.
    buy_and_hold_final_equity = initial_cash + opening_inventory * final_mid
    marked_pnl = final_equity - buy_and_hold_final_equity
    fees = sum(fill.fee_cny for fill in fills)
    gross_marked = marked_pnl + fees
    result = DailyResult(
        model_id=model_id,
        code=code,
        name=name,
        category=profile.category,
        market_date=events[0].market_date,
        sample=sample,
        mode=mode,
        minimum_spread_ticks=minimum_spread_ticks,
        one_way_fee_bps=one_way_fee_bps,
        opening_inventory_units=opening_inventory,
        maximum_inventory_units=maximum_inventory,
        quote_size_units=quote_size,
        fills=len(fills),
        completed_turns=completed_turns,
        buy_units=sum(fill.quantity_units for fill in fills if fill.side == "buy"),
        sell_units=sum(fill.quantity_units for fill in fills if fill.side == "sell"),
        turnover_cny=sum(fill.notional_cny for fill in fills),
        fees_cny=fees,
        ending_inventory_units=inventory,
        ending_inventory_deviation_units=inventory - opening_inventory,
        marked_pnl_cny=marked_pnl,
        gross_marked_pnl_cny=gross_marked,
        first_midpoint=first_mid,
        final_midpoint=final_mid,
    )
    return result, fills


def _selection_score(rows: list[DailyResult]) -> float:
    if not rows:
        return -math.inf
    active = [row for row in rows if row.fills > 0]
    if len(active) < max(2, math.ceil(len(rows) * 0.3)):
        return -math.inf
    pnls = [row.marked_pnl_cny for row in rows]
    deviations = [
        abs(row.ending_inventory_deviation_units) * row.final_midpoint
        for row in rows
    ]
    return (
        statistics.fmean(pnls)
        - 0.5 * (statistics.stdev(pnls) if len(pnls) > 1 else 0.0)
        - 0.001 * statistics.fmean(deviations)
    )


def select_category_parameters(
    dataset: dict[str, dict[str, list[MarketEvent]]],
    names: dict[str, str],
    category: str,
    mode: Mode,
    train_dates: set[str],
    *,
    selection_fee_bps: float = 0.5,
) -> SelectionResult:
    profile = PROFILES[category]
    best: tuple[float, int, list[DailyResult]] | None = None
    for minimum_spread in profile.minimum_spread_grid:
        rows: list[DailyResult] = []
        for code, by_date in dataset.items():
            if profile_for_code(code).category != category:
                continue
            for market_date in sorted(train_dates & set(by_date)):
                result, _ = run_day(
                    code,
                    names.get(code, code),
                    by_date[market_date],
                    mode=mode,
                    minimum_spread_ticks=minimum_spread,
                    one_way_fee_bps=selection_fee_bps,
                    sample="train",
                )
                rows.append(result)
        score = _selection_score(rows)
        if best is None or score > best[0]:
            best = (score, minimum_spread, rows)
    assert best is not None
    score, minimum_spread, rows = best
    pnls = [row.marked_pnl_cny for row in rows]
    return SelectionResult(
        model_id=MODEL_IDS[mode],
        category=category,
        mode=mode,
        minimum_spread_ticks=minimum_spread,
        training_days=len(rows),
        active_training_days=sum(row.fills > 0 for row in rows),
        training_mean_net_cny=statistics.fmean(pnls) if pnls else 0.0,
        training_median_net_cny=statistics.median(pnls) if pnls else 0.0,
        training_score=score,
    )


def common_dates(dataset: dict[str, dict[str, list[MarketEvent]]]) -> list[str]:
    date_sets = [set(by_date) for by_date in dataset.values() if by_date]
    return sorted(set.intersection(*date_sets)) if date_sets else []


def read_qmt_dataset(
    codes: tuple[str, ...],
    start_date: str,
    end_date: str,
    *,
    qmt_port: int,
) -> tuple[dict[str, dict[str, list[MarketEvent]]], dict[str, str]]:
    from xtquant import xtdata

    xtdata.enable_hello = False
    client = xtdata.connect(port=qmt_port)
    if client is None or not client.is_connected():
        raise ConnectionError(f"MiniQMT连接失败，端口：{qmt_port}")
    compact_start = start_date.replace("-", "") + "092500"
    compact_end = end_date.replace("-", "") + "150030"
    for code in codes:
        xtdata.download_history_data(
            code,
            "tick",
            start_time=compact_start,
            end_time=compact_end,
        )
    frames = xtdata.get_market_data_ex(
        [],
        list(codes),
        period="tick",
        start_time=compact_start,
        end_time=compact_end,
        count=-1,
        dividend_type="none",
        fill_data=False,
    )
    dataset: dict[str, dict[str, list[MarketEvent]]] = {}
    names: dict[str, str] = {}
    for code in codes:
        detail = xtdata.get_instrument_detail(code, False) or {}
        names[code] = str(detail.get("InstrumentName") or code).strip()
        events = qmt_frame_to_events(code, frames.get(code))
        dataset[code] = split_events_by_date(events)
    return dataset, names


def run_walk_forward(
    dataset: dict[str, dict[str, list[MarketEvent]]],
    names: dict[str, str],
    *,
    fee_grid_bps: tuple[float, ...] = (0.0, 0.2, 0.5, 1.0),
) -> tuple[list[SelectionResult], list[DailyResult], list[PaperFill], list[str], list[str]]:
    dates = common_dates(dataset)
    if len(dates) < 10:
        raise ValueError(f"共同完整交易日只有{len(dates)}天，不足以做前后切分")
    midpoint = len(dates) // 2
    train_dates = dates[:midpoint]
    test_dates = dates[midpoint:]
    selections: list[SelectionResult] = []
    for category in PROFILES:
        for mode in ("priority", "queue"):
            selections.append(select_category_parameters(
                dataset,
                names,
                category,
                mode,
                set(train_dates),
            ))
    selection_map = {
        (item.category, item.mode): item.minimum_spread_ticks
        for item in selections
    }
    results: list[DailyResult] = []
    fills: list[PaperFill] = []
    for code, by_date in dataset.items():
        category = profile_for_code(code).category
        for market_date in dates:
            sample = "train" if market_date in train_dates else "test"
            for mode in ("priority", "queue"):
                minimum_spread = selection_map[(category, mode)]
                for fee in fee_grid_bps:
                    result, day_fills = run_day(
                        code,
                        names.get(code, code),
                        by_date[market_date],
                        mode=mode,
                        minimum_spread_ticks=minimum_spread,
                        one_way_fee_bps=fee,
                        sample=sample,
                    )
                    results.append(result)
                    if fee == 0.5:
                        fills.extend(day_fills)
    return selections, results, fills, train_dates, test_dates


def aggregate_rows(results: list[DailyResult]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, float], list[DailyResult]] = defaultdict(list)
    for row in results:
        groups[(row.code, row.sample, row.mode, row.one_way_fee_bps)].append(row)
    output: list[dict[str, Any]] = []
    for (code, sample, mode, fee), rows in sorted(groups.items()):
        pnls = [row.marked_pnl_cny for row in rows]
        output.append({
            "model_id": rows[0].model_id,
            "code": code,
            "name": rows[0].name,
            "category": rows[0].category,
            "sample": sample,
            "mode": mode,
            "minimum_spread_ticks": rows[0].minimum_spread_ticks,
            "one_way_fee_bps": fee,
            "days": len(rows),
            "active_days": sum(row.fills > 0 for row in rows),
            "profitable_days": sum(value > 0 for value in pnls),
            "mean_daily_pnl_cny": statistics.fmean(pnls),
            "median_daily_pnl_cny": statistics.median(pnls),
            "total_pnl_cny": sum(pnls),
            "worst_day_cny": min(pnls),
            "best_day_cny": max(pnls),
            "mean_fills": statistics.fmean(row.fills for row in rows),
            "mean_completed_turns": statistics.fmean(row.completed_turns for row in rows),
            "mean_turnover_cny": statistics.fmean(row.turnover_cny for row in rows),
            "unresolved_inventory_days": sum(row.ending_inventory_deviation_units != 0 for row in rows),
        })
    return output


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_report(
    path: Path,
    selections: list[SelectionResult],
    aggregates: list[dict[str, Any]],
    train_dates: list[str],
    test_dates: list[str],
) -> None:
    lines = [
        "# 国内锚定证券特殊做市回测",
        "",
        f"> 模型：`{PRIORITY_MODEL_ID}`与`{QUEUE_MODEL_ID}`。只读MiniQMT历史tick，不发送委托。",
        "",
        f"- 校准日：{train_dates[0]}—{train_dates[-1]}（{len(train_dates)}天）",
        f"- 样本外：{test_dates[0]}—{test_dates[-1]}（{len(test_dates)}天）",
        "- 账户：约27万元总资产，一半底仓、一半追加买入能力；日与日独立重置。",
        "- 委托：每笔约1万元；改善一档与原价排队分支独立。",
        "- 成交：只使用委托建立后至少3秒、且`transaction_delta == 1`的后续对手成交。",
        "",
        "## 校准集冻结参数",
        "",
        "| 类别 | 执行 | 模型 | 最小显示价差 | 校准活跃天/总天 | 校准日均净收益（0.5bp/边） |",
        "|---|---|---|---:|---:|---:|",
    ]
    for row in selections:
        lines.append(
            f"| {row.category} | {row.mode} | `{row.model_id}` | "
            f"{row.minimum_spread_ticks}档 | {row.active_training_days}/{row.training_days} | "
            f"{row.training_mean_net_cny:.2f}元 |"
        )
    lines.extend([
        "",
        "## 样本外每日结果",
        "",
        "| 代码 | 名称 | 执行 | 单边费率 | 有成交天 | 盈利天 | 日均 | 中位数 | 最差日 | 最好日 | 日均完整轮次 | 未回中性库存天 |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    rows = [row for row in aggregates if row["sample"] == "test"]
    for row in sorted(rows, key=lambda item: (
        float(item["one_way_fee_bps"]),
        -float(item["mean_daily_pnl_cny"]),
    )):
        lines.append(
            f"| `{row['code']}` | {row['name']} | {row['mode']} | "
            f"{row['one_way_fee_bps']:.1f}bp | {row['active_days']}/{row['days']} | "
            f"{row['profitable_days']}/{row['days']} | {row['mean_daily_pnl_cny']:.2f} | "
            f"{row['median_daily_pnl_cny']:.2f} | {row['worst_day_cny']:.2f} | "
            f"{row['best_day_cny']:.2f} | {row['mean_completed_turns']:.2f} | "
            f"{row['unresolved_inventory_days']}/{row['days']} |"
        )
    lines.extend([
        "",
        "## 边界",
        "",
        "- 改善一档是反事实第一顺位：后续真实卖单打到旧买一或更低时，才支持改善后买单成交；不倒灌当前帧。",
        "- 排队分支仅用后续严格单笔成交消耗初始可见前队，不假设撤单帮助清队，因此是保守下界。",
        "- 收益是相对同等开盘底仓一直持有不动的增量收益；零成交时严格为零，不把底仓本身涨跌冒充做市收益。",
        "- 增量收益按收盘最后有效中点盯市，不强平；未回到开盘中性库存的日子必须单独看风险。",
        "- 回测未计利息、分红、申赎、税务、冲击成本和外部抢位；结果只是纸面候选证据。",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="国内锚定证券特殊做市只读回测")
    parser.add_argument("--start", default="2026-07-27")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--codes", nargs="+", default=list(DEFAULT_CODES))
    parser.add_argument("--qmt-port", type=int, default=58611)
    parser.add_argument("--output-dir", type=Path, default=Path("output") / "anchored-maker")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    codes = tuple(dict.fromkeys(code.upper() for code in args.codes))
    dataset, names = read_qmt_dataset(
        codes,
        args.start,
        args.end,
        qmt_port=args.qmt_port,
    )
    selections, results, fills, train_dates, test_dates = run_walk_forward(dataset, names)
    aggregates = aggregate_rows(results)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = args.output_dir / f"anchored_maker_{args.start}_{args.end}_{stamp}.md"
    write_report(report, selections, aggregates, train_dates, test_dates)
    _write_csv(args.output_dir / f"anchored_maker_daily_{stamp}.csv", [asdict(row) for row in results])
    _write_csv(args.output_dir / f"anchored_maker_fills_{stamp}.csv", [asdict(row) for row in fills])
    _write_csv(args.output_dir / f"anchored_maker_aggregate_{stamp}.csv", aggregates)
    (args.output_dir / "anchored_maker_latest.md").write_text(
        report.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    manifest = {
        "priority_model_id": PRIORITY_MODEL_ID,
        "queue_model_id": QUEUE_MODEL_ID,
        "codes": list(codes),
        "train_dates": train_dates,
        "test_dates": test_dates,
        "selections": [asdict(row) for row in selections],
    }
    (args.output_dir / f"anchored_maker_manifest_{stamp}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"报告：{report}")
    print(f"校准日：{train_dates[0]}—{train_dates[-1]}")
    print(f"样本外：{test_dates[0]}—{test_dates[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
