from __future__ import annotations

import argparse
import csv
import statistics
import sys
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from research_railway_maker import (
    DEFAULT_CODES,
    _instrument_name,
    _read_qmt_frames,
    qmt_frame_to_replay_ticks,
)


ROOT = Path(__file__).resolve().parent
MODEL_ID = "railway_priority_v0_1_candidate"
EPSILON = 1e-9


@dataclass(frozen=True)
class RailwayPriorityParameters:
    model_id: str = MODEL_ID
    order_quantity_bonds: float = 1_000.0
    customer_base_inventory_bonds: float = 1_000.0
    maximum_inventory_bonds: float = 2_000.0
    evidence_window_seconds: int = 1_800
    minimum_evidence_span_seconds: int = 600
    price_cluster_width: float = 0.015
    minimum_side_events: int = 3
    minimum_side_bonds: float = 3_000.0
    maximum_center_drift: float = 0.020
    entry_cluster_tolerance: float = 0.015
    minimum_corridor: float = 0.050
    maximum_target_edge: float = 0.100
    price_tick: float = 0.001
    competition_capture_rate: float = 1.0
    round_trip_cost_per_bond: float = 0.0


@dataclass(frozen=True)
class TradePoint:
    market_ts_ms: int
    price: float
    bonds: float
    side: str


@dataclass(frozen=True)
class CorridorSignal:
    low_cluster: float
    high_cluster: float
    entry_price: float
    exit_price: float
    corridor: float
    center_drift: float


@dataclass(frozen=True)
class RailwayFill:
    model_id: str
    code: str
    name: str
    market_date: str
    market_time: str
    side: str
    price: float
    quantity_bonds: float
    extra_inventory_after_bonds: float
    low_cluster: float
    high_cluster: float
    paired_exit_price: float
    reason: str


@dataclass(frozen=True)
class RailwayPriorityResult:
    model_id: str
    scenario: str
    code: str
    name: str
    market_date: str
    minimum_corridor: float
    competition_capture_rate: float
    round_trip_cost_per_bond: float
    signals: int
    entry_fills: int
    completed_turns: int
    bought_bonds: float
    sold_bonds: float
    ending_inventory_bonds: float
    ending_extra_inventory_bonds: float
    customer_base_short_bonds: float
    realized_gross_cny: float
    marked_gross_cny: float
    estimated_cost_cny: float
    realized_net_cny: float
    marked_net_cny: float
    average_holding_seconds: float | None
    maximum_adverse_move_per_bond: float


def _round_price(value: float) -> float:
    return round(value + 1e-12, 3)


def _weighted_median(points: Iterable[TradePoint]) -> float:
    weighted = sorted(
        (point.price, min(point.bonds, 1_000.0)) for point in points
    )
    total = sum(weight for _, weight in weighted)
    if total <= 0:
        return 0.0
    cumulative = 0.0
    for price, weight in weighted:
        cumulative += weight
        if cumulative * 2 + EPSILON >= total:
            return price
    return weighted[-1][0]


def _corridor_center(
    points: list[TradePoint],
    low: float,
    high: float,
    width: float,
) -> float | None:
    low_points = [point for point in points if abs(point.price - low) <= width + EPSILON]
    high_points = [point for point in points if abs(point.price - high) <= width + EPSILON]
    if not low_points or not high_points:
        return None
    return (_weighted_median(low_points) + _weighted_median(high_points)) / 2


def _extreme_repeated_cluster(
    points: list[TradePoint],
    *,
    prefer_low: bool,
    parameters: RailwayPriorityParameters,
) -> float | None:
    qualified: list[float] = []
    for candidate in sorted({point.price for point in points}):
        cluster = [
            point
            for point in points
            if abs(point.price - candidate) <= parameters.price_cluster_width + EPSILON
        ]
        capped_bonds = sum(min(point.bonds, 1_000.0) for point in cluster)
        if (
            len(cluster) >= parameters.minimum_side_events
            and capped_bonds + EPSILON >= parameters.minimum_side_bonds
        ):
            qualified.append(_weighted_median(cluster))
    if not qualified:
        return None
    return min(qualified) if prefer_low else max(qualified)


def infer_corridor(
    evidence: list[TradePoint],
    tick: object,
    parameters: RailwayPriorityParameters,
) -> CorridorSignal | None:
    if not evidence:
        return None
    span_ms = evidence[-1].market_ts_ms - evidence[0].market_ts_ms
    if span_ms + EPSILON < parameters.minimum_evidence_span_seconds * 1_000:
        return None
    low = _extreme_repeated_cluster(
        evidence, prefer_low=True, parameters=parameters,
    )
    high = _extreme_repeated_cluster(
        evidence, prefer_low=False, parameters=parameters,
    )
    if low is None or high is None:
        return None

    midpoint_ts = evidence[0].market_ts_ms + span_ms / 2
    older = [point for point in evidence if point.market_ts_ms <= midpoint_ts]
    newer = [point for point in evidence if point.market_ts_ms > midpoint_ts]
    if len(older) < 2 or len(newer) < 2:
        return None
    older_center = _corridor_center(
        older, low, high, parameters.price_cluster_width,
    )
    newer_center = _corridor_center(
        newer, low, high, parameters.price_cluster_width,
    )
    if older_center is None or newer_center is None:
        return None
    center_drift = abs(newer_center - older_center)
    if center_drift > parameters.maximum_center_drift + EPSILON:
        return None

    if not (
        tick.bid1 > 0
        and tick.ask1 > tick.bid1
        and tick.bid1_bonds + EPSILON >= parameters.order_quantity_bonds
        and tick.ask1_bonds + EPSILON >= parameters.order_quantity_bonds
    ):
        return None
    entry = _round_price(tick.bid1 + parameters.price_tick)
    if entry >= tick.ask1 - EPSILON:
        return None
    if abs(entry - low) > parameters.entry_cluster_tolerance + EPSILON:
        return None
    available_edge = high - entry
    if available_edge + EPSILON < parameters.minimum_corridor:
        return None
    exit_price = _round_price(min(high, entry + parameters.maximum_target_edge))
    corridor = exit_price - entry
    if corridor + EPSILON < parameters.minimum_corridor:
        return None
    return CorridorSignal(
        low_cluster=_round_price(low),
        high_cluster=_round_price(high),
        entry_price=entry,
        exit_price=exit_price,
        corridor=_round_price(corridor),
        center_drift=_round_price(center_drift),
    )


def run_candidate(
    code: str,
    name: str,
    ticks: list[object],
    *,
    scenario: str = "base_005",
    parameters: RailwayPriorityParameters | None = None,
) -> tuple[RailwayPriorityResult, list[RailwayFill]]:
    parameters = parameters or RailwayPriorityParameters()
    evidence: list[TradePoint] = []
    entry_order: CorridorSignal | None = None
    extra_inventory = 0.0
    entry_price = 0.0
    exit_price = 0.0
    entry_low_cluster = 0.0
    entry_high_cluster = 0.0
    entry_ts_ms = 0
    signals = 0
    entry_fills = 0
    completed_turns = 0
    bought_bonds = 0.0
    sold_bonds = 0.0
    realized_gross = 0.0
    maximum_adverse = 0.0
    holding_seconds: list[float] = []
    fills: list[RailwayFill] = []

    for tick in ticks:
        captured_bonds = tick.trade_bonds * parameters.competition_capture_rate

        # Existing orders see this tick before the tick is admitted as new evidence.
        if (
            extra_inventory <= EPSILON
            and entry_order is not None
            and tick.last_price <= entry_order.entry_price + EPSILON
            and captured_bonds > EPSILON
        ):
            quantity = min(parameters.order_quantity_bonds, captured_bonds)
            extra_inventory = quantity
            entry_price = entry_order.entry_price
            exit_price = entry_order.exit_price
            entry_low_cluster = entry_order.low_cluster
            entry_high_cluster = entry_order.high_cluster
            entry_ts_ms = tick.market_ts_ms
            entry_fills += 1
            bought_bonds += quantity
            fills.append(RailwayFill(
                model_id=parameters.model_id,
                code=code,
                name=name,
                market_date=tick.market_date,
                market_time=tick.market_time,
                side="buy",
                price=entry_price,
                quantity_bonds=quantity,
                extra_inventory_after_bonds=extra_inventory,
                low_cluster=entry_low_cluster,
                high_cluster=entry_high_cluster,
                paired_exit_price=exit_price,
                reason="future_trade_hit_low_cluster_order",
            ))
            entry_order = None
        elif (
            extra_inventory > EPSILON
            and tick.last_price + EPSILON >= exit_price
            and captured_bonds > EPSILON
        ):
            quantity = min(extra_inventory, captured_bonds)
            realized_gross += (exit_price - entry_price) * quantity
            sold_bonds += quantity
            extra_inventory -= quantity
            fills.append(RailwayFill(
                model_id=parameters.model_id,
                code=code,
                name=name,
                market_date=tick.market_date,
                market_time=tick.market_time,
                side="sell",
                price=exit_price,
                quantity_bonds=quantity,
                extra_inventory_after_bonds=extra_inventory,
                low_cluster=entry_low_cluster,
                high_cluster=entry_high_cluster,
                paired_exit_price=exit_price,
                reason="future_trade_hit_prior_high_cluster_order",
            ))
            if extra_inventory <= EPSILON:
                extra_inventory = 0.0
                completed_turns += 1
                holding_seconds.append((tick.market_ts_ms - entry_ts_ms) / 1_000)
                entry_price = 0.0
                exit_price = 0.0
                entry_low_cluster = 0.0
                entry_high_cluster = 0.0
                entry_ts_ms = 0

        if extra_inventory > EPSILON:
            adverse_reference = tick.bid1 if tick.bid1 > 0 else tick.last_price
            if adverse_reference > 0:
                maximum_adverse = max(maximum_adverse, entry_price - adverse_reference)

        cutoff = tick.market_ts_ms - parameters.evidence_window_seconds * 1_000
        evidence = [point for point in evidence if point.market_ts_ms >= cutoff]
        if tick.trade_bonds > 0 and tick.last_price > 0:
            evidence.append(TradePoint(
                market_ts_ms=tick.market_ts_ms,
                price=tick.last_price,
                bonds=tick.trade_bonds,
                side=tick.inferred_side,
            ))

        if extra_inventory > EPSILON:
            continue
        signal = infer_corridor(evidence, tick, parameters)
        if signal is None:
            entry_order = None
            continue
        if (
            entry_order is None
            or entry_order.entry_price != signal.entry_price
            or entry_order.exit_price != signal.exit_price
        ):
            signals += 1
        entry_order = signal

    final_bid = next((tick.bid1 for tick in reversed(ticks) if tick.bid1 > 0), 0.0)
    marked_gross = realized_gross
    if extra_inventory > EPSILON and final_bid > 0:
        marked_gross += (final_bid - entry_price) * extra_inventory
    estimated_cost = (
        (bought_bonds + sold_bonds)
        * parameters.round_trip_cost_per_bond
        / 2
    )
    realized_cost = (
        sold_bonds * parameters.round_trip_cost_per_bond
    )
    market_date = ticks[0].market_date if ticks else ""
    result = RailwayPriorityResult(
        model_id=parameters.model_id,
        scenario=scenario,
        code=code,
        name=name,
        market_date=market_date,
        minimum_corridor=parameters.minimum_corridor,
        competition_capture_rate=parameters.competition_capture_rate,
        round_trip_cost_per_bond=parameters.round_trip_cost_per_bond,
        signals=signals,
        entry_fills=entry_fills,
        completed_turns=completed_turns,
        bought_bonds=bought_bonds,
        sold_bonds=sold_bonds,
        ending_inventory_bonds=parameters.customer_base_inventory_bonds + extra_inventory,
        ending_extra_inventory_bonds=extra_inventory,
        customer_base_short_bonds=0.0,
        realized_gross_cny=realized_gross,
        marked_gross_cny=marked_gross,
        estimated_cost_cny=estimated_cost,
        realized_net_cny=realized_gross - realized_cost,
        marked_net_cny=marked_gross - estimated_cost,
        average_holding_seconds=(
            statistics.mean(holding_seconds) if holding_seconds else None
        ),
        maximum_adverse_move_per_bond=max(0.0, maximum_adverse),
    )
    return result, fills


def scenario_parameters() -> list[tuple[str, RailwayPriorityParameters]]:
    base = RailwayPriorityParameters()
    return [
        ("base_005", base),
        ("base_008", replace(base, minimum_corridor=0.080)),
        ("base_010", replace(base, minimum_corridor=0.100)),
        ("fee_005", replace(base, round_trip_cost_per_bond=0.010)),
        ("competition_005", replace(
            base,
            competition_capture_rate=0.50,
            round_trip_cost_per_bond=0.010,
        )),
        ("hard_005", replace(
            base,
            competition_capture_rate=0.25,
            round_trip_cost_per_bond=0.020,
        )),
    ]


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_report(path: Path, results: list[RailwayPriorityResult]) -> None:
    lines = [
        f"# {MODEL_ID} 多日因果回放",
        "",
        "> 独立铁路债第一顺位研究候选。账户为1,000张客户底仓＋最多1,000张新增仓；只先买新增仓，再卖实际买到的数量。未调用交易接口，未加入生产或实时比较矩阵。",
        "",
        "## 逐品种日结果",
        "",
        "| 日期 | 代码 | 名称 | 场景 | 信号 | 买入 | 闭环 | 日终额外仓 | 毛收益 | 压力后收益 | 平均持有 | 最大不利/张 |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results:
        holding = "—" if row.average_holding_seconds is None else f"{row.average_holding_seconds:.0f}秒"
        lines.append(
            f"| {row.market_date} | `{row.code}` | {row.name} | {row.scenario} | "
            f"{row.signals} | {row.entry_fills} | {row.completed_turns} | "
            f"{row.ending_extra_inventory_bonds:.0f} | {row.marked_gross_cny:.2f} | "
            f"{row.marked_net_cny:.2f} | {holding} | "
            f"{row.maximum_adverse_move_per_bond:.3f} |"
        )

    lines.extend([
        "",
        "## 场景合计",
        "",
        "| 场景 | 品种日 | 买入 | 闭环 | 日终滞留品种日 | 已实现毛收益 | 盯市毛收益 | 压力后盯市 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for scenario in dict.fromkeys(row.scenario for row in results):
        rows = [row for row in results if row.scenario == scenario]
        lines.append(
            f"| {scenario} | {len(rows)} | {sum(row.entry_fills for row in rows)} | "
            f"{sum(row.completed_turns for row in rows)} | "
            f"{sum(row.ending_extra_inventory_bonds > 0 for row in rows)} | "
            f"{sum(row.realized_gross_cny for row in rows):.2f} | "
            f"{sum(row.marked_gross_cny for row in rows):.2f} | "
            f"{sum(row.marked_net_cny for row in rows):.2f} |"
        )

    lines.extend([
        "",
        "## 口径",
        "",
        "- 过去30分钟内，低侧与高侧实际成交价必须各形成至少3个、合计至少3,000张的重复价格簇，而且前后半窗都真实到达过两侧；证据跨度至少10分钟，前后半窗走廊中心漂移不超过0.020元。Level 1方向估计不用于建立价格簇。",
        "- 当前买一改善一厘必须仍位于低簇0.015元内；高簇到买价至少有场景要求的0.05/0.08/0.10元，单轮目标上限0.10元。订单只接受创建后的新成交，创建帧不能倒灌成交；低侧首次成交多少就撤掉未成交余额，只对实际新增仓退出，高侧允许由多笔未来成交逐步卖完。",
        "- base是假设第一顺位取得合格成交量；competition只把可归属成交量按50%折减，hard按25%折减。两者是执行压力，不是queue分支。费用按每张完整往返0.01/0.02元估计。",
        "- Level 1主动方向是本地估计；没有逐笔委托队列、撤单和真实席位竞争，因此结果只能用于选择逐笔审计样本，不能视为可部署收益。",
        "- 0.05元是毛价差研究下沿，不是保证扣费后盈利的承诺；日终额外仓不强平，按末笔有效买一盯市并单独报告。",
        "",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"{MODEL_ID}只读多日回放")
    parser.add_argument("--dates", nargs="+", default=[date.today().isoformat()])
    parser.add_argument("--codes", nargs="+", default=list(DEFAULT_CODES))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports")
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    codes = tuple(dict.fromkeys(code.upper() for code in args.codes))
    dates = tuple(dict.fromkeys(args.dates))
    names = {code: _instrument_name(code) for code in codes}
    results: list[RailwayPriorityResult] = []
    fills: list[RailwayFill] = []
    for target_date in dates:
        frames = _read_qmt_frames(codes, target_date)
        for code in codes:
            ticks = qmt_frame_to_replay_ticks(code, frames.get(code), target_date)
            if not ticks:
                continue
            for scenario, parameters in scenario_parameters():
                result, scenario_fills = run_candidate(
                    code,
                    names[code],
                    ticks,
                    scenario=scenario,
                    parameters=parameters,
                )
                results.append(result)
                fills.extend(scenario_fills)
            print(f"{target_date} {code} {names[code]}：{len(ticks)}帧", flush=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = args.output_dir / f"railway_priority_v0_1_{stamp}.md"
    summary = args.output_dir / f"railway_priority_v0_1_{stamp}.csv"
    fill_path = args.output_dir / f"railway_priority_v0_1_fills_{stamp}.csv"
    write_report(report, results)
    _write_csv(summary, [asdict(row) for row in results])
    _write_csv(fill_path, [asdict(row) for row in fills])
    latest = args.output_dir / "railway_priority_v0_1_latest.md"
    latest.write_text(report.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"报告：{report}")
    print(f"汇总：{summary}")
    print(f"成交：{fill_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
