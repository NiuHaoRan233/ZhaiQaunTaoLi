from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from zhaiquant.config import load_config  # noqa: E402
from zhaiquant.database import SQLiteStore  # noqa: E402
from zhaiquant.maker import ReplayTick  # noqa: E402
from zhaiquant.maker_paper import MakerPaperEngine  # noqa: E402
from zhaiquant.recorder import TickRecorder  # noqa: E402
from zhaiquant.types import SHANGHAI, Tick  # noqa: E402


DEFAULT_CODES = (
    "184814.SH",
    "184808.SH",
    "184815.SH",
    "184803.SH",
    "184818.SH",
    "184807.SH",
    "184813.SH",
    "184819.SH",
    "184823.SH",
    "184804.SH",
)


@dataclass(frozen=True)
class RailwayReplaySummary:
    code: str
    name: str
    model_id: str
    fill_mode: str
    tick_count: int
    market_trade_count: int
    market_amount_cny: float
    market_price_range: float
    median_visible_spread: float
    reversal_pairs: int
    reversal_pairs_ge_002: int
    reversal_pairs_ge_005: int
    reversal_pairs_ge_010: int
    reversal_pairs_ge_020: int
    simulated_fill_records: int
    simulated_filled_bonds: float
    buy_filled_bonds: float
    sell_filled_bonds: float
    ending_inventory_bonds: float
    customer_base_short_bonds: float
    extra_inventory_bonds: float
    marked_trading_pnl_cny: float


@dataclass(frozen=True)
class CorridorPilotSummary:
    code: str
    name: str
    minimum_edge: float
    entry_fills: int
    completed_turns: int
    ending_extra_inventory_bonds: float
    realized_gross_cny: float
    marked_gross_cny: float
    worst_completed_edge: float | None
    best_completed_edge: float | None


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def qmt_frame_to_replay_ticks(code: str, frame: Any, target_date: str) -> list[ReplayTick]:
    if frame is None or frame.empty:
        return []
    previous_tick: Tick | None = None
    previous_tick_id: int | None = None
    replay: list[ReplayTick] = []
    seen: set[tuple[int, str]] = set()
    for _, payload in frame.sort_values("time", kind="stable").iterrows():
        tick = Tick.from_qmt(code, payload.to_dict())
        if tick.market_datetime.date().isoformat() != target_date:
            continue
        identity = (tick.market_ts_ms, tick.snapshot_hash)
        if identity in seen:
            continue
        seen.add(identity)
        tick_id = len(replay) + 1
        change = TickRecorder._change(previous_tick_id, previous_tick, tick)
        replay.append(ReplayTick(
            tick_id=tick_id,
            code=code,
            market_ts_ms=tick.market_ts_ms,
            market_date=target_date,
            market_time=tick.market_datetime.time().isoformat(timespec="milliseconds"),
            last_price=tick.last_price,
            bids=tuple(
                (price, volume * 10.0)
                for price, volume in zip(tick.bid_prices, tick.bid_volumes)
                if price > 0
            ),
            asks=tuple(
                (price, volume * 10.0)
                for price, volume in zip(tick.ask_prices, tick.ask_volumes)
                if price > 0
            ),
            trade_bonds=change.volume_delta * 10.0,
            transaction_delta=change.transaction_delta,
            inferred_side=change.inferred_side,
            side_confidence=change.side_confidence,
            previous_close=tick.previous_close,
        ))
        previous_tick = tick
        previous_tick_id = tick_id
    return replay


def _instrument_name(code: str) -> str:
    from xtquant import xtdata

    detail = xtdata.get_instrument_detail(code, False) or {}
    return str(detail.get("InstrumentName") or code).strip()


def _read_qmt_frames(codes: tuple[str, ...], target_date: str) -> dict[str, Any]:
    from xtquant import xtdata

    config = load_config(PROJECT_ROOT / "config.toml")
    xtdata.enable_hello = False
    client = xtdata.connect(port=config.qmt.port)
    if client is None or not client.is_connected():
        raise ConnectionError(f"MiniQMT连接失败，端口：{config.qmt.port}")
    compact = target_date.replace("-", "")
    xtdata.download_history_data2(
        list(codes), "tick", start_time=compact, end_time=compact,
        incrementally=True,
    )
    return xtdata.get_market_data_ex(
        [], list(codes), period="tick", start_time=compact, end_time=compact,
        count=-1, dividend_type="none", fill_data=False,
    )


def _market_metrics(ticks: list[ReplayTick]) -> dict[str, float]:
    trade_ticks = [tick for tick in ticks if tick.trade_bonds > 0]
    prices = [tick.last_price for tick in trade_ticks if tick.last_price > 0]
    visible_spreads = [
        tick.ask1 - tick.bid1
        for tick in ticks
        if tick.bid1 > 0 and tick.ask1 > tick.bid1
    ]
    segments: list[dict[str, float | str]] = []
    for tick in trade_ticks:
        if tick.inferred_side not in {"buy", "sell"}:
            continue
        if segments and segments[-1]["side"] == tick.inferred_side:
            segment = segments[-1]
            segment["bonds"] = float(segment["bonds"]) + tick.trade_bonds
            segment["high"] = max(float(segment["high"]), tick.last_price)
            segment["low"] = min(float(segment["low"]), tick.last_price)
        else:
            segments.append({
                "side": tick.inferred_side,
                "bonds": tick.trade_bonds,
                "high": tick.last_price,
                "low": tick.last_price,
            })
    reversal_edges: list[float] = []
    for previous, current in zip(segments, segments[1:]):
        if min(float(previous["bonds"]), float(current["bonds"])) + 1e-9 < 1_000.0:
            continue
        if previous["side"] == "sell" and current["side"] == "buy":
            edge = float(current["high"]) - float(previous["low"])
        elif previous["side"] == "buy" and current["side"] == "sell":
            edge = float(previous["high"]) - float(current["low"])
        else:
            continue
        if edge > 0:
            reversal_edges.append(edge)
    return {
        "market_trade_count": float(sum(tick.transaction_delta for tick in trade_ticks)),
        "market_price_range": max(prices) - min(prices) if prices else 0.0,
        "median_visible_spread": statistics.median(visible_spreads) if visible_spreads else 0.0,
        "reversal_pairs": float(len(reversal_edges)),
        "reversal_pairs_ge_002": float(sum(edge + 1e-9 >= 0.02 for edge in reversal_edges)),
        "reversal_pairs_ge_005": float(sum(edge + 1e-9 >= 0.05 for edge in reversal_edges)),
        "reversal_pairs_ge_010": float(sum(edge + 1e-9 >= 0.10 for edge in reversal_edges)),
        "reversal_pairs_ge_020": float(sum(edge + 1e-9 >= 0.20 for edge in reversal_edges)),
    }


def run_stable_corridor_pilot(
    code: str,
    name: str,
    ticks: list[ReplayTick],
    minimum_edge: float,
) -> CorridorPilotSummary:
    """Causal first-position sensitivity test inspired by the v1.44 corridor idea."""

    book: list[ReplayTick] = []
    high_buys: list[ReplayTick] = []
    entry_order: tuple[float, float] | None = None
    position = 0.0
    entry_price = 0.0
    exit_price = 0.0
    entry_fills = 0
    completed_edges: list[float] = []
    realized = 0.0
    for tick in ticks:
        if (
            entry_order is not None
            and position == 0
            and tick.trade_bonds + 1e-9 >= 1_000.0
            and tick.inferred_side == "sell"
            and tick.last_price <= entry_order[0] + 1e-9
        ):
            entry_price, exit_price = entry_order
            position = 1_000.0
            entry_fills += 1
            entry_order = None
        elif (
            position > 0
            and tick.trade_bonds + 1e-9 >= position
            and tick.inferred_side == "buy"
            and tick.last_price + 1e-9 >= exit_price
        ):
            edge = exit_price - entry_price
            realized += edge * position
            completed_edges.append(edge)
            position = 0.0
            entry_price = 0.0
            exit_price = 0.0

        if tick.bid1 > 0 and tick.ask1 > tick.bid1:
            book.append(tick)
        if tick.trade_bonds > 0 and tick.inferred_side == "buy":
            high_buys.append(tick)
        book_cutoff = tick.market_ts_ms - 60_000
        evidence_cutoff = tick.market_ts_ms - 600_000
        book = [item for item in book if item.market_ts_ms >= book_cutoff]
        high_buys = [item for item in high_buys if item.market_ts_ms >= evidence_cutoff]

        if position > 0:
            continue
        entry_order = None
        if not (
            tick.bid1 > 0
            and tick.ask1 > tick.bid1
            and tick.bid1_bonds + 1e-9 >= 1_000.0
            and tick.ask1_bonds + 1e-9 >= 1_000.0
            and len(book) >= 2
            and book[-1].market_ts_ms - book[0].market_ts_ms >= 15_000
        ):
            continue
        bids = [item.bid1 for item in book]
        asks = [item.ask1 for item in book]
        if max(bids) - min(bids) > 0.015 + 1e-9 or max(asks) - min(asks) > 0.015 + 1e-9:
            continue
        proposed_entry = round(tick.bid1 + 0.001, 3)
        proposed_exit = round(tick.ask1 - 0.001, 3)
        edge = proposed_exit - proposed_entry
        if edge + 1e-9 < minimum_edge or edge > 0.20 + 1e-9:
            continue
        high_evidence = sum(
            item.trade_bonds
            for item in high_buys
            if item.last_price + 1e-9 >= proposed_exit
        )
        if high_evidence + 1e-9 < 1_000.0:
            continue
        entry_order = (proposed_entry, proposed_exit)

    final_bid = next((tick.bid1 for tick in reversed(ticks) if tick.bid1 > 0), 0.0)
    marked = realized + ((final_bid - entry_price) * position if position > 0 else 0.0)
    return CorridorPilotSummary(
        code=code,
        name=name,
        minimum_edge=minimum_edge,
        entry_fills=entry_fills,
        completed_turns=len(completed_edges),
        ending_extra_inventory_bonds=position,
        realized_gross_cny=realized,
        marked_gross_cny=marked,
        worst_completed_edge=min(completed_edges) if completed_edges else None,
        best_completed_edge=max(completed_edges) if completed_edges else None,
    )


def replay_code(
    code: str,
    name: str,
    ticks: list[ReplayTick],
    amount_cny: float,
) -> tuple[list[RailwayReplaySummary], list[dict[str, Any]]]:
    base_config = load_config(PROJECT_ROOT / "config.toml")
    mappings = dict(base_config.maker_paper.underlying_stock_codes)
    mappings[code] = "000000.SH"
    metrics = _market_metrics(ticks)
    summaries: list[RailwayReplaySummary] = []
    fill_rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as temporary:
        replay_config = replace(
            base_config,
            storage=replace(
                base_config.storage,
                database=Path(temporary) / "railway-maker.sqlite3",
            ),
            maker_paper=replace(
                base_config.maker_paper,
                bond_codes=(code,),
                underlying_stock_codes=mappings,
                fill_modes=("priority", "queue"),
                super_windfall_enabled=False,
            ),
        )
        store = SQLiteStore(replay_config)
        store.start_session()
        try:
            engine = MakerPaperEngine(
                replay_config,
                store,
                bond_code=code,
                strategy_prefix=f"railway_research_{code.replace('.', '_')}",
                fill_modes=("priority", "queue"),
                include_windfall=False,
            )
            for tick in ticks:
                engine.on_replay_tick(tick, persist=True)
            store.connection.commit()
            assignments = {
                row["strategy_id"]: row["model_id"]
                for row in store.connection.execute(
                    "SELECT strategy_id,model_id FROM maker_paper_model_assignments"
                )
            }
            fills_by_strategy: dict[str, list[dict[str, Any]]] = {}
            for row in store.connection.execute(
                """SELECT strategy_id,market_ts_ms,side,price,quantity,
                          fill_reason,inventory_after
                   FROM maker_paper_fills ORDER BY market_ts_ms,id"""
            ):
                item = dict(row)
                item.update({
                    "code": code,
                    "name": name,
                    "model_id": assignments.get(item["strategy_id"], "unregistered"),
                    "market_time": datetime.fromtimestamp(
                        item["market_ts_ms"] / 1_000, SHANGHAI,
                    ).strftime("%H:%M:%S"),
                })
                fills_by_strategy.setdefault(item["strategy_id"], []).append(item)
                fill_rows.append(item)
            for row in store.connection.execute(
                """SELECT strategy_id,fill_mode,initial_inventory,inventory,
                          trading_pnl,fills
                   FROM maker_paper_accounts ORDER BY strategy_id"""
            ):
                account = dict(row)
                fills = fills_by_strategy.get(account["strategy_id"], [])
                summaries.append(RailwayReplaySummary(
                    code=code,
                    name=name,
                    model_id=assignments.get(account["strategy_id"], "unregistered"),
                    fill_mode=account["fill_mode"],
                    tick_count=len(ticks),
                    market_trade_count=int(metrics["market_trade_count"]),
                    market_amount_cny=amount_cny,
                    market_price_range=metrics["market_price_range"],
                    median_visible_spread=metrics["median_visible_spread"],
                    reversal_pairs=int(metrics["reversal_pairs"]),
                    reversal_pairs_ge_002=int(metrics["reversal_pairs_ge_002"]),
                    reversal_pairs_ge_005=int(metrics["reversal_pairs_ge_005"]),
                    reversal_pairs_ge_010=int(metrics["reversal_pairs_ge_010"]),
                    reversal_pairs_ge_020=int(metrics["reversal_pairs_ge_020"]),
                    simulated_fill_records=len(fills),
                    simulated_filled_bonds=sum(float(item["quantity"]) for item in fills),
                    buy_filled_bonds=sum(
                        float(item["quantity"]) for item in fills if item["side"] == "buy"
                    ),
                    sell_filled_bonds=sum(
                        float(item["quantity"]) for item in fills if item["side"] == "sell"
                    ),
                    ending_inventory_bonds=float(account["inventory"]),
                    customer_base_short_bonds=max(
                        0.0,
                        float(account["initial_inventory"]) - float(account["inventory"]),
                    ),
                    extra_inventory_bonds=max(
                        0.0,
                        float(account["inventory"]) - float(account["initial_inventory"]),
                    ),
                    marked_trading_pnl_cny=float(account["trading_pnl"]),
                ))
        finally:
            store.close()
    return summaries, fill_rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _amount_text(value: float) -> str:
    return f"{value / 100_000_000:.2f}亿" if value >= 100_000_000 else f"{value / 10_000:.0f}万"


def write_report(
    path: Path,
    target_date: str,
    summaries: list[RailwayReplaySummary],
    fill_rows: list[dict[str, Any]],
    pilots: list[CorridorPilotSummary],
) -> None:
    lines = [
        f"# {target_date} 铁路债现有做市模型只读实验",
        "",
        "> 使用生产基线第一顺位1.1与排队1.0的原规则和1,000张账户容量；输入为MiniQMT历史Level 1 tick。未配置铁路债正股辅助信号，不发送委托。",
        "",
        "## 实际成交结构",
        "",
        "| 代码 | 名称 | 笔数 | 日内价差 | 显示价差中位数 | 相邻反转段 | ≥0.02元 | ≥0.05元 | ≥0.10元 | ≥0.20元 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    seen_codes: set[str] = set()
    for row in summaries:
        if row.code in seen_codes:
            continue
        seen_codes.add(row.code)
        lines.append(
            f"| `{row.code}` | {row.name} | {row.market_trade_count} | "
            f"{row.market_price_range:.3f} | {row.median_visible_spread:.3f} | "
            f"{row.reversal_pairs} | {row.reversal_pairs_ge_002} | "
            f"{row.reversal_pairs_ge_005} | {row.reversal_pairs_ge_010} | "
            f"{row.reversal_pairs_ge_020} |"
        )
    lines.extend([
        "",
        "> 相邻反转段只统计买卖方向发生切换、两侧估计成交量都至少1,000张的事后价格对；它用于衡量市场给过的空间，不代表模型事前一定能够挂到或成交。",
        "",
        "## 铁路债稳定走廊敏感性实验",
        "",
        "> 只做额外仓先买后卖，不卖客户底仓；要求当前买卖盘至少稳定15秒、60秒内两端漂移不超过0.015元、高侧过去10分钟已有至少1,000张真实买入。订单在证据成立后才挂，成交不能倒灌。",
        "",
        "| 代码 | 名称 | 最小走廊 | 买入次数 | 完整轮次 | 日终额外仓 | 已实现毛收益 | 盯市毛收益 | 完整轮次价差 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in pilots:
        completed_range = (
            "—"
            if row.worst_completed_edge is None
            else f"{row.worst_completed_edge:.3f}—{row.best_completed_edge:.3f}"
        )
        lines.append(
            f"| `{row.code}` | {row.name} | {row.minimum_edge:.3f} | "
            f"{row.entry_fills} | {row.completed_turns} | "
            f"{row.ending_extra_inventory_bonds:.0f} | {row.realized_gross_cny:.2f} | "
            f"{row.marked_gross_cny:.2f} | {completed_range} |"
        )
    lines.extend([
        "",
        "> 该敏感性实验只是寻找值得继续研究的最小价差范围，没有手续费、外部抢位和盘口冲击；不得描述为候选模型或可部署收益。",
        "",
        "## 现有模型原样回放",
        "",
        "| 代码 | 名称 | 执行口径 | 模型 | 市场笔数 | 日内价差 | 成交额 | 模拟成交记录 | 买入张数 | 卖出张数 | 日终库存 | 盯市毛收益 |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for row in sorted(summaries, key=lambda item: (item.fill_mode, -item.marked_trading_pnl_cny, item.code)):
        lines.append(
            f"| `{row.code}` | {row.name} | {row.fill_mode} | `{row.model_id}` | "
            f"{row.market_trade_count} | {row.market_price_range:.3f} | "
            f"{_amount_text(row.market_amount_cny)} | {row.simulated_fill_records} | "
            f"{row.buy_filled_bonds:.0f} | {row.sell_filled_bonds:.0f} | "
            f"{row.ending_inventory_bonds:.0f} | {row.marked_trading_pnl_cny:.2f} |"
        )
    lines.extend([
        "",
        "## 解释边界",
        "",
        "- 这是把现有可交换债模型原样迁移到铁路债的压力测试，不是已经针对铁路债校准的新模型。",
        "- QMT Level 1的主动方向仍是本地估计；排队账户只按显示前队消耗，第一顺位仍假设改善一厘后在前。",
        "- 盯市毛收益不含手续费、外部报价竞争和真实账户准入限制；成交记录多不自动代表可部署。",
        f"- 全部模拟成交明细共{len(fill_rows)}条，另存CSV供逐笔核查。",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用现有做市模型只读回放铁路债")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--codes", nargs="+", default=list(DEFAULT_CODES))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    codes = tuple(dict.fromkeys(code.upper() for code in args.codes))
    frames = _read_qmt_frames(codes, args.date)
    from xtquant import xtdata

    latest_ticks = xtdata.get_full_tick(list(codes))
    summaries: list[RailwayReplaySummary] = []
    fills: list[dict[str, Any]] = []
    pilots: list[CorridorPilotSummary] = []
    for code in codes:
        name = _instrument_name(code)
        ticks = qmt_frame_to_replay_ticks(code, frames.get(code), args.date)
        if not ticks:
            continue
        code_summaries, code_fills = replay_code(
            code,
            name,
            ticks,
            _number((latest_ticks.get(code) or {}).get("amount")),
        )
        summaries.extend(code_summaries)
        fills.extend(code_fills)
        for minimum_edge in (0.02, 0.05, 0.10):
            pilots.append(run_stable_corridor_pilot(
                code, name, ticks, minimum_edge,
            ))
        print(f"{code} {name}：{len(ticks)}帧，模拟成交{len(code_fills)}条", flush=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = args.output_dir / f"railway_maker_{args.date}_{stamp}.md"
    summary_path = args.output_dir / f"railway_maker_{args.date}_{stamp}.csv"
    fills_path = args.output_dir / f"railway_maker_fills_{args.date}_{stamp}.csv"
    pilots_path = args.output_dir / f"railway_corridor_pilots_{args.date}_{stamp}.csv"
    _write_csv(summary_path, [asdict(row) for row in summaries])
    _write_csv(fills_path, fills)
    _write_csv(pilots_path, [asdict(row) for row in pilots])
    write_report(report_path, args.date, summaries, fills, pilots)
    latest_path = args.output_dir / "railway_maker_latest.md"
    latest_path.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"报告：{report_path}")
    print(f"汇总：{summary_path}")
    print(f"成交：{fills_path}")
    print(f"走廊实验：{pilots_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
