from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .config import AppConfig, maker_underlying_stock_code
from .database import SQLiteStore
from .maker_paper import MakerPaperEngine, MakerPolicyProfile, _load_ticks
from .types import SHANGHAI
from .tdx_tape import TdxOrderEvent, TdxTrade


EVALUATION_LAYERS = {
    "layer_1_theoretical_market": {
        "question": "Which positive B/S price differences existed on the reviewed tape?",
        "future_information_allowed": True,
        "model_signal_used": False,
        "may_be_capture_rate_denominator": False,
    },
    "layer_2_causal_mode_in": {
        "question": "Which theoretical paths were recognizable and executable in the user's mode using only then-available evidence?",
        "future_information_allowed": False,
        "required_inputs": (
            "causal Level 1 book, prior trades, prior order events, fair value, "
            "trend, inventory and execution branch"
        ),
        "status": "requires explicit causal review or a validated causal classifier",
    },
    "layer_3_branch_capture": {
        "question": "Did each registered execution branch actually quote and fill the causal mode-in opportunity?",
        "branches_must_remain_separate": True,
        "queue_requires_front_queue_evidence": True,
    },
}


def _seconds(value: str) -> int:
    hour, minute, second = (int(part) for part in value.split(":"))
    return hour * 3_600 + minute * 60 + second


@dataclass(frozen=True)
class TheoreticalOpportunityPair:
    pair_id: str
    market_date: str
    code: str
    direction: str
    open_trade_index: int
    open_time: str
    open_side: str
    open_price: float
    open_hands: int
    close_trade_index: int
    close_time: str
    close_side: str
    close_price: float
    close_hands: int
    edge: float
    elapsed_seconds: int
    single_pair_capacity_hands: int
    source_pages: tuple[str, ...]


@dataclass(frozen=True)
class LocalOpportunityTurn:
    turn_id: str
    market_date: str
    code: str
    direction: str
    open_side: str
    close_side: str
    open_run_start_time: str
    open_run_end_time: str
    close_run_start_time: str
    close_run_end_time: str
    open_run_hands: int
    close_run_hands: int
    profitable_pair_count: int
    first_completion_time: str
    best_pair_id: str
    best_open_time: str
    best_open_price: float
    best_open_hands: int
    best_close_time: str
    best_close_price: float
    best_close_hands: int
    minimum_positive_edge: float
    maximum_edge: float
    maximum_matchable_hands: int
    source_pages: tuple[str, ...]
    review_status: str


@dataclass(frozen=True)
class ReplayFill:
    order_id: int
    strategy_id: str
    model_id: str
    market_time: str
    side: str
    price: float
    quantity_bonds: float
    fill_reason: str
    inventory_after_bonds: float
    reference_tick_id: int
    crossed_book_residual_price: float
    crossed_book_residual_bonds: float


@dataclass(frozen=True)
class InventoryPathAction:
    trade_index: int
    market_time: str
    tape_side: str
    action: str
    price: float
    hands: int
    inventory_before_hands: int
    inventory_after_hands: int
    cash_change: float
    source_page: str


@dataclass(frozen=True)
class HindsightInventoryPath:
    market_date: str
    code: str
    initial_inventory_hands: int
    maximum_inventory_hands: int
    terminal_inventory_hands: int
    terminal_inventory_forced: bool
    terminal_mark_price: float
    gross_cash_flow: float
    gross_cash_profit: float
    buy_hands: int
    sell_hands: int
    actions: tuple[InventoryPathAction, ...]


@dataclass(frozen=True)
class QueueOrderAudit:
    order_id: int
    cohort_model_order_ids: str
    strategy_id: str
    model_id: str
    side: str
    kind: str
    limit_price: float
    quantity_bonds: float
    created_market_time: str
    updated_market_time: str
    duration_seconds: int
    model_status: str
    simulated_filled_bonds: float
    initial_queue_ahead_bonds: float
    final_queue_ahead_bonds: float
    cohort_id: str
    cohort_order_count: int
    cohort_quantity_bonds: float
    repeated_external_queue_bonds: float
    same_price_add_bonds: float
    same_price_cancel_bonds: float
    same_price_trade_bonds: float
    price_through_trade_bonds: float
    creation_second_eligible_trade_bonds: float
    crossed_book_residual_fill_bonds: float
    exact_fill_lower_bound_bonds: float
    fill_upper_bound_bonds: float
    execution_status: str
    causal_decision_status: str


def optimize_nonoverlapping_inventory_path(
    trades: Iterable[TdxTrade], *, initial_inventory_hands: int = 100,
    maximum_inventory_hands: int = 200,
    terminal_inventory_hands: int | None = None,
) -> HindsightInventoryPath:
    """Compute a hindsight tape-liquidity upper bound without capacity reuse.

    An `S` print can fill a resting buy and a `B` print can fill a resting sell.
    Every print's hand quantity is consumed at most once and inventory always
    stays inside the supplied bounds.  With no explicit terminal inventory, the
    path may finish anywhere in those bounds and open exposure is marked at the
    last reviewed tape price.  This is an ex-post evaluation ceiling, never a
    causal signal.
    """

    usable = _usable_trades(trades)
    if not usable:
        raise ValueError("No fully reviewed TDX trades for inventory-path audit")
    if not (0 <= initial_inventory_hands <= maximum_inventory_hands):
        raise ValueError("Initial inventory must be inside the inventory bounds")
    if (
        terminal_inventory_hands is not None
        and not 0 <= terminal_inventory_hands <= maximum_inventory_hands
    ):
        raise ValueError("Terminal inventory must be inside the inventory bounds")

    negative_infinity = float("-inf")
    state = [negative_infinity] * (maximum_inventory_hands + 1)
    state[initial_inventory_hands] = 0.0
    predecessors: list[list[int]] = []
    for trade in usable:
        quantity = min(int(trade.hands or 0), maximum_inventory_hands)
        next_state = list(state)
        previous = list(range(maximum_inventory_hands + 1))
        if trade.side == "S":
            for before, cash in enumerate(state):
                if cash == negative_infinity:
                    continue
                maximum_after = min(maximum_inventory_hands, before + quantity)
                for after in range(before + 1, maximum_after + 1):
                    candidate = cash - (after - before) * trade.price
                    if candidate > next_state[after] + 1e-9:
                        next_state[after] = candidate
                        previous[after] = before
        else:
            for before, cash in enumerate(state):
                if cash == negative_infinity:
                    continue
                minimum_after = max(0, before - quantity)
                for after in range(minimum_after, before):
                    candidate = cash + (before - after) * trade.price
                    if candidate > next_state[after] + 1e-9:
                        next_state[after] = candidate
                        previous[after] = before
        state = next_state
        predecessors.append(previous)

    terminal_mark_price = usable[-1].price
    terminal_forced = terminal_inventory_hands is not None
    if terminal_forced:
        terminal = int(terminal_inventory_hands)
    else:
        # Compare open long/short deficits on a marked basis.  On an exact tie,
        # prefer the terminal inventory closest to the opening base so the
        # hindsight ceiling does not manufacture gratuitous exposure.
        terminal = max(
            range(maximum_inventory_hands + 1),
            key=lambda inventory: (
                state[inventory]
                + (inventory - initial_inventory_hands) * terminal_mark_price,
                -abs(inventory - initial_inventory_hands),
            ),
        )
    if state[terminal] == negative_infinity:
        raise ValueError("No feasible terminal inventory path")
    actions: list[InventoryPathAction] = []
    inventory_after = terminal
    for offset in range(len(usable) - 1, -1, -1):
        inventory_before = predecessors[offset][inventory_after]
        if inventory_before != inventory_after:
            trade = usable[offset]
            action = "buy" if inventory_after > inventory_before else "sell"
            hands = abs(inventory_after - inventory_before)
            cash_change = (
                -hands * trade.price * 10.0
                if action == "buy"
                else hands * trade.price * 10.0
            )
            actions.append(InventoryPathAction(
                trade_index=offset + 1,
                market_time=trade.market_time,
                tape_side=trade.side or "",
                action=action,
                price=round(trade.price, 3),
                hands=hands,
                inventory_before_hands=inventory_before,
                inventory_after_hands=inventory_after,
                cash_change=round(cash_change, 3),
                source_page=trade.source_page,
            ))
        inventory_after = inventory_before
    actions.reverse()
    gross_cash_flow = state[terminal] * 10.0
    gross_marked_profit = gross_cash_flow + (
        terminal - initial_inventory_hands
    ) * terminal_mark_price * 10.0
    return HindsightInventoryPath(
        market_date=usable[0].market_date,
        code=usable[0].code,
        initial_inventory_hands=initial_inventory_hands,
        maximum_inventory_hands=maximum_inventory_hands,
        terminal_inventory_hands=terminal,
        terminal_inventory_forced=terminal_forced,
        terminal_mark_price=round(terminal_mark_price, 3),
        gross_cash_flow=round(gross_cash_flow, 3),
        # Keep the historical field name for report compatibility.  When the
        # terminal is free this is marked profit, not raw cash received.
        gross_cash_profit=round(gross_marked_profit, 3),
        buy_hands=sum(item.hands for item in actions if item.action == "buy"),
        sell_hands=sum(item.hands for item in actions if item.action == "sell"),
        actions=tuple(actions),
    )


def write_inventory_path_report(
    path: Path, *, trades_path: Path, inventory_path: HindsightInventoryPath,
    manual_review_rows: int = 0, manual_reviews_path: Path | None = None,
) -> dict[str, str]:
    """Persist the non-reusing hindsight inventory ceiling for audit.

    The report is deliberately separate from causal model capture.  Its only
    purpose is to answer how much of the reviewed tape could be used by one
    ex-post inventory path when every print has finite capacity.
    """

    payload = {
        "evaluation_layers": EVALUATION_LAYERS,
        "current_layer": "layer_1_theoretical_market.inventory_capacity_ceiling",
        "source_trades": str(trades_path.resolve()),
        "purpose": "hindsight_nonoverlapping_tape_liquidity_upper_bound",
        "causal_signal": False,
        "mode_in_opportunity_count": False,
        "may_be_used_as_capture_rate_denominator": False,
        "assumptions": {
            "one_hand_equals_bonds": 10,
            "S_print_can_fill_resting_buy": True,
            "B_print_can_fill_resting_sell": True,
            "each_print_capacity_used_at_most_once": True,
            "inventory_never_below_zero": True,
            "forced_terminal_inventory": inventory_path.terminal_inventory_forced,
            "open_terminal_exposure_marked": True,
            "fees_and_slippage_included": False,
            "future_information_used_for_optimization": True,
        },
        "manual_reviews": {
            "applied_rows": manual_review_rows,
            "source": (
                str(manual_reviews_path.resolve()) if manual_reviews_path else None
            ),
        },
        "summary": {
            "market_date": inventory_path.market_date,
            "code": inventory_path.code,
            "initial_inventory_hands": inventory_path.initial_inventory_hands,
            "initial_inventory_bonds": inventory_path.initial_inventory_hands * 10,
            "maximum_inventory_hands": inventory_path.maximum_inventory_hands,
            "maximum_inventory_bonds": inventory_path.maximum_inventory_hands * 10,
            "terminal_inventory_hands": inventory_path.terminal_inventory_hands,
            "terminal_inventory_bonds": inventory_path.terminal_inventory_hands * 10,
            "terminal_inventory_forced": inventory_path.terminal_inventory_forced,
            "terminal_mark_price": inventory_path.terminal_mark_price,
            "gross_cash_flow": inventory_path.gross_cash_flow,
            "gross_cash_profit": inventory_path.gross_cash_profit,
            "buy_hands": inventory_path.buy_hands,
            "buy_bonds": inventory_path.buy_hands * 10,
            "sell_hands": inventory_path.sell_hands,
            "sell_bonds": inventory_path.sell_hands * 10,
            "action_count": len(inventory_path.actions),
        },
        "actions": [asdict(item) for item in inventory_path.actions],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    actions_csv = path.with_name("理论流动性上界_不重复库存路径_动作.csv")
    markdown = path.with_name("理论流动性上界_不重复库存路径.md")
    _write_dataclass_csv(actions_csv, list(inventory_path.actions))
    _write_inventory_path_markdown(markdown, inventory_path)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return {
        "report": str(path),
        "actions_csv": str(actions_csv),
        "markdown": str(markdown),
    }


def _write_inventory_path_markdown(
    path: Path, inventory_path: HindsightInventoryPath,
) -> None:
    lines = [
        "# 全天理论流动性上界（不重复库存路径）",
        "",
        "> 这是使用未来信息求出的事后流动性容量上界，不是盘中因果信号，不代表模式内机会，也不能直接作为任何模型的捕获率分母。",
        "",
        "## 口径",
        "",
        "- 通达信`S`逐笔可成交预挂买单，`B`逐笔可成交预挂卖单。",
        "- 每一笔逐笔成交的容量最多使用一次；1手等于10张。",
        (
            f"- 库存范围：0—{inventory_path.maximum_inventory_hands}手，日初"
            f"{inventory_path.initial_inventory_hands}手，收盘强制指定"
            f"{inventory_path.terminal_inventory_hands}手。"
            if inventory_path.terminal_inventory_forced else
            f"- 库存范围：0—{inventory_path.maximum_inventory_hands}手，日初"
            f"{inventory_path.initial_inventory_hands}手；收盘不强制平仓，最优路径"
            f"保留{inventory_path.terminal_inventory_hands}手，并按"
            f"{inventory_path.terminal_mark_price:.3f}元盯市。"
        ),
        "- 未扣手续费和滑点。",
        "",
        "## 汇总",
        "",
        f"- 理论盯市毛收益上界：{inventory_path.gross_cash_profit:,.2f}元。",
        f"- 路径累计现金流：{inventory_path.gross_cash_flow:,.2f}元。",
        f"- 买入：{inventory_path.buy_hands:,}手（{inventory_path.buy_hands * 10:,}张）。",
        f"- 卖出：{inventory_path.sell_hands:,}手（{inventory_path.sell_hands * 10:,}张）。",
        f"- 动作：{len(inventory_path.actions):,}个。",
        "",
        "## 动作路径",
        "",
        "| 序号 | 时间 | 逐笔方向 | 模拟动作 | 价格 | 数量 | 库存前→后 | 现金变化 |",
        "|---:|---|---|---|---:|---:|---:|---:|",
    ]
    for index, item in enumerate(inventory_path.actions, start=1):
        lines.append(
            f"| {index} | {item.market_time} | {item.tape_side} | {item.action} | "
            f"{item.price:.3f} | {item.hands}手 | "
            f"{item.inventory_before_hands}→{item.inventory_after_hands}手 | "
            f"{item.cash_change:,.2f}元 |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def audit_queue_orders(
    replay: dict, trades: Iterable[TdxTrade], order_events: Iterable[TdxOrderEvent],
) -> list[QueueOrderAudit]:
    """Bound queue fills using reviewed prints and add/cancel evidence.

    TDX order events do not expose exchange order IDs, so a same-price cancel
    cannot be assigned uniquely to queue ahead or queue behind. The lower fill
    bound assumes no cancellation helped our position; the upper bound lets
    cancellations remove the initial queue first. A strict price-through print
    only raises the upper bound because the hypothetical model order itself
    could have absorbed part of the aggressor.
    """

    usable_trades = _usable_trades(trades)
    usable_events = [
        item for item in order_events
        if not item.review_required
        and item.event_type in {"B", "S", "BC", "SC"}
        and item.hands is not None
    ]
    queue_strategy_ids = {
        account["strategy_id"] for account in replay["accounts"]
        if account.get("fill_mode") == "queue"
    }
    orders = [
        item for item in replay["orders"]
        if item["strategy_id"] in queue_strategy_ids
        and item["side"] in {"buy", "sell"}
        and item["kind"] not in {"deep_discount_sweep", "sweep_tail"}
    ]
    crossed_residual_groups: dict[tuple, list[dict]] = {}
    for fill in replay.get("fills", []):
        if fill.get("fill_reason") != "queue_cleared_crossed_residual_fill":
            continue
        reference_tick_id = int(fill.get("reference_tick_id", 0))
        residual_bonds = float(fill.get("crossed_book_residual_bonds", 0.0))
        residual_price = float(fill.get("crossed_book_residual_price", 0.0))
        if reference_tick_id <= 0 or residual_bonds <= 1e-9:
            continue
        price = float(fill["price"])
        crosses = (
            residual_price <= price + 1e-9
            if fill["side"] == "buy"
            else residual_price + 1e-9 >= price
        )
        if not crosses:
            continue
        key = (
            fill["strategy_id"], reference_tick_id, fill["side"],
        )
        crossed_residual_groups.setdefault(key, []).append(fill)
    crossed_residual_support_by_order: dict[int, float] = {}
    for group in crossed_residual_groups.values():
        capacities = {
            round(float(fill["crossed_book_residual_bonds"]), 9)
            for fill in group
        }
        if len(capacities) != 1:
            continue
        capacity = next(iter(capacities))
        allocated = sum(float(fill["quantity_bonds"]) for fill in group)
        if allocated > capacity + 1e-9:
            continue
        for fill in group:
            order_id = int(fill.get("order_id", 0))
            if order_id <= 0:
                continue
            crossed_residual_support_by_order[order_id] = (
                crossed_residual_support_by_order.get(order_id, 0.0)
                + float(fill["quantity_bonds"])
            )
    cohorts: dict[tuple, list[dict]] = {}
    for order in orders:
        created_ms = int(order.get("created_market_ts_ms", 0))
        if not created_ms:
            created_ms = _seconds(order["created_market_time"]) * 1_000
        key = (
            order["strategy_id"], order["side"],
            round(float(order["limit_price"]), 3), created_ms,
        )
        cohorts.setdefault(key, []).append(order)

    audits: list[QueueOrderAudit] = []
    for cohort_key, cohort in cohorts.items():
        order = min(cohort, key=lambda item: int(item["id"]))
        created_seconds = _seconds(order["created_market_time"])
        updated_seconds = max(
            _seconds(item["updated_market_time"]) for item in cohort
        )
        updated_market_time = max(
            (item["updated_market_time"] for item in cohort), key=_seconds,
        )
        cohort_id = (
            f"{order['strategy_id']}:{order['side']}:"
            f"{float(order['limit_price']):.3f}:{order['created_market_time']}"
        )
        price = float(order["limit_price"])
        expected_tape_side = "S" if order["side"] == "buy" else "B"
        expected_add = "B" if order["side"] == "buy" else "S"
        expected_cancel = "BC" if order["side"] == "buy" else "SC"

        exact_trade_bonds = 0.0
        through_trade_bonds = 0.0
        boundary_trade_bonds = 0.0
        for trade in usable_trades:
            if trade.side != expected_tape_side or trade.hands is None:
                continue
            trade_seconds = _seconds(trade.market_time)
            if trade_seconds == created_seconds and _trade_reaches_order(
                order["side"], trade.price, price,
            ):
                boundary_trade_bonds += trade.hands * 10.0
            if not (created_seconds < trade_seconds <= updated_seconds):
                continue
            if abs(trade.price - price) <= 0.0005:
                exact_trade_bonds += trade.hands * 10.0
            elif _trade_passes_order(order["side"], trade.price, price):
                through_trade_bonds += trade.hands * 10.0

        add_bonds = 0.0
        cancel_bonds = 0.0
        for event in usable_events:
            if abs(event.price - price) > 0.0005 or event.hands is None:
                continue
            event_seconds = _seconds(event.market_time)
            if not (created_seconds < event_seconds <= updated_seconds):
                continue
            if event.event_type == expected_add:
                add_bonds += event.hands * 10.0
            elif event.event_type == expected_cancel:
                cancel_bonds += event.hands * 10.0

        initial_queue = max(
            float(item.get("initial_queue_ahead_bonds", 0.0)) for item in cohort
        )
        final_queue = sum(float(item.get("queue_ahead", 0.0)) for item in cohort)
        quantity = sum(float(item["quantity"]) for item in cohort)
        lower = min(quantity, max(0.0, exact_trade_bonds - initial_queue))
        optimistic_queue_reduction = min(initial_queue, cancel_bonds)
        upper = min(
            quantity,
            max(
                0.0,
                exact_trade_bonds + optimistic_queue_reduction - initial_queue,
            ),
        )
        if through_trade_bonds > 0:
            upper = quantity
        crossed_residual_fill_bonds = sum(
            crossed_residual_support_by_order.get(int(item["id"]), 0.0)
            for item in cohort
        )
        lower = min(quantity, lower + crossed_residual_fill_bonds)
        upper = min(quantity, upper + crossed_residual_fill_bonds)
        simulated = sum(float(item["filled_quantity"]) for item in cohort)
        if simulated > upper + 1e-9:
            execution_status = "simulated_fill_exceeds_tdx_upper_bound"
        elif simulated + 1e-9 < lower:
            execution_status = "simulation_underfills_tdx_lower_bound"
        elif abs(lower - upper) <= 1e-9 and abs(simulated - lower) <= 1e-9:
            execution_status = "exactly_consistent_with_tdx_bounds"
        elif lower - 1e-9 <= simulated <= upper + 1e-9:
            execution_status = "consistent_with_ambiguous_tdx_bounds"
        else:
            execution_status = "requires_manual_execution_review"
        repeated_external_queue = (
            initial_queue * (len(cohort) - 1) if len(cohort) > 1 else 0.0
        )
        audits.append(QueueOrderAudit(
            order_id=int(order["id"]),
            cohort_model_order_ids="|".join(
                str(item["id"]) for item in sorted(
                    cohort, key=lambda item: int(item["id"]),
                )
            ),
            strategy_id=order["strategy_id"],
            model_id=order.get("model_id", "unregistered"),
            side=order["side"],
            kind=order["kind"],
            limit_price=round(price, 3),
            quantity_bonds=quantity,
            created_market_time=order["created_market_time"],
            updated_market_time=updated_market_time,
            duration_seconds=max(0, updated_seconds - created_seconds),
            model_status="|".join(sorted({item["status"] for item in cohort})),
            simulated_filled_bonds=simulated,
            initial_queue_ahead_bonds=initial_queue,
            final_queue_ahead_bonds=final_queue,
            cohort_id=cohort_id,
            cohort_order_count=len(cohort),
            cohort_quantity_bonds=quantity,
            repeated_external_queue_bonds=repeated_external_queue,
            same_price_add_bonds=add_bonds,
            same_price_cancel_bonds=cancel_bonds,
            same_price_trade_bonds=exact_trade_bonds,
            price_through_trade_bonds=through_trade_bonds,
            creation_second_eligible_trade_bonds=boundary_trade_bonds,
            crossed_book_residual_fill_bonds=(
                crossed_residual_fill_bonds
            ),
            exact_fill_lower_bound_bonds=lower,
            fill_upper_bound_bonds=upper,
            execution_status=execution_status,
            causal_decision_status="not_evaluated_by_queue_execution_audit",
        ))
    return audits


def _trade_reaches_order(side: str, trade_price: float, order_price: float) -> bool:
    return (
        trade_price <= order_price + 0.0005
        if side == "buy"
        else trade_price >= order_price - 0.0005
    )


def _trade_passes_order(side: str, trade_price: float, order_price: float) -> bool:
    return (
        trade_price < order_price - 0.0005
        if side == "buy"
        else trade_price > order_price + 0.0005
    )


def write_queue_order_audit(
    path: Path, *, trades_path: Path, order_events_path: Path,
    replay: dict, audits: list[QueueOrderAudit],
    trade_manual_review_rows: int = 0,
    order_manual_review_rows: int = 0,
) -> dict[str, str]:
    summary_statuses = {
        status: sum(item.execution_status == status for item in audits)
        for status in sorted({item.execution_status for item in audits})
    }
    repeated_cohorts = {
        item.cohort_id for item in audits if item.cohort_order_count > 1
    }
    summary = {
        "audited_queue_cohorts": len(audits),
        "audited_model_orders": sum(item.cohort_order_count for item in audits),
        "execution_statuses": summary_statuses,
        "multi_order_same_price_cohorts": len(repeated_cohorts),
        "orders_with_repeated_external_queue": sum(
            item.repeated_external_queue_bonds > 0 for item in audits
        ),
        "creation_second_uncertain_orders": sum(
            item.creation_second_eligible_trade_bonds > 0 for item in audits
        ),
    }
    payload = {
        "evaluation_layers": EVALUATION_LAYERS,
        "current_layer": "layer_3_branch_capture.queue_execution_bounds",
        "layer_2_causal_mode_in_status": "not_evaluated",
        "source_trades": str(trades_path.resolve()),
        "source_order_events": str(order_events_path.resolve()),
        "model_replay": {
            "market_date": replay["market_date"],
            "bond_code": replay["bond_code"],
            "source_database_opened_readonly": replay["source_database_opened_readonly"],
            "temporary_replay_database": replay["temporary_replay_database"],
            "accounts": replay["accounts"],
        },
        "time_precision": {
            "TDX_resolution": "one_second",
            "creation_second_trades_excluded_from_strict_window": True,
            "creation_second_volume_reported_separately": True,
        },
        "cancellation_bounds": {
            "lower_fill_bound": "assume no cancellation removes queue ahead",
            "upper_fill_bound": "allow same-price cancellations to remove initial queue ahead first",
            "price_through_only_sets_upper_bound": True,
            "crossed_book_residual": (
                "a queue-cleared fill is bounded by the source Level 1 contra "
                "residual; allocations sharing one strategy/tick/side must "
                "sum to no more than that displayed residual"
            ),
        },
        "manual_reviews": {
            "trade_rows": trade_manual_review_rows,
            "order_event_rows": order_manual_review_rows,
        },
        "summary": summary,
        "orders": [asdict(item) for item in audits],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    csv_path = path.with_suffix(".csv")
    markdown = path.with_suffix(".md")
    _write_dataclass_csv(csv_path, list(audits))
    _write_queue_order_audit_markdown(markdown, audits, summary)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return {"report": str(path), "csv": str(csv_path), "markdown": str(markdown)}


def _write_queue_order_audit_markdown(
    path: Path, audits: list[QueueOrderAudit], summary: dict,
) -> None:
    lines = [
        "# 排队模型逐笔执行审计",
        "",
        "> 本表只审计排队位置和成交上下界，不判断挂单决策是否属于用户模式。模式内可识别性必须在第二层因果审计中独立确认。",
        "",
        "## 汇总",
        "",
        f"- 排队市场订单批次：{summary['audited_queue_cohorts']:,}组，包含模型分批订单{summary['audited_model_orders']:,}张。",
        f"- 同时同价多订单批次：{summary['multi_order_same_price_cohorts']:,}组。",
        f"- 带重复外部前方队列的订单：{summary['orders_with_repeated_external_queue']:,}张。",
        f"- 创建同秒存在方向相符成交、时序不确定：{summary['creation_second_uncertain_orders']:,}张。",
        "",
        "## 需要优先检查的订单",
        "",
        "| 订单 | 存续 | 方向 | 价格 | 数量 | 初始前队 | 同价成交 | 同价撤单 | 穿价残量成交 | 成交下界—上界 | 模拟成交 | 同批订单 | 状态 |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    prioritized = sorted(
        audits,
        key=lambda item: (
            item.execution_status in {
                "exactly_consistent_with_tdx_bounds",
                "consistent_with_ambiguous_tdx_bounds",
            },
            item.repeated_external_queue_bonds <= 0,
            item.created_market_time,
            item.order_id,
        ),
    )
    for item in prioritized:
        lines.append(
            f"| {item.order_id} | {item.created_market_time}—{item.updated_market_time} | "
            f"{item.side} | {item.limit_price:.3f} | {item.quantity_bonds:.0f}张 | "
            f"{item.initial_queue_ahead_bonds:.0f}张 | {item.same_price_trade_bonds:.0f}张 | "
            f"{item.same_price_cancel_bonds:.0f}张 | "
            f"{item.crossed_book_residual_fill_bonds:.0f}张 | "
            f"{item.exact_fill_lower_bound_bonds:.0f}—{item.fill_upper_bound_bonds:.0f}张 | "
            f"{item.simulated_filled_bonds:.0f}张 | {item.cohort_order_count} | "
            f"{item.execution_status} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_tdx_trades(path: Path) -> list[TdxTrade]:
    trades: list[TdxTrade] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            trades.append(TdxTrade(
                market_date=row["market_date"],
                code=row["code"],
                market_time=row["market_time"],
                price=float(row["price"]),
                hands=int(row["hands"]) if row["hands"] else None,
                side=row["side"] or None,
                buy_order=int(row["buy_order"]) if row["buy_order"] else None,
                sell_order=int(row["sell_order"]) if row["sell_order"] else None,
                source_page=row["source_page"],
                page_sequence=int(row["page_sequence"]),
                panel=int(row["panel"]),
                row=int(row["row"]),
                time_inherited=row["time_inherited"].lower() == "true",
                ocr_confidence=float(row["ocr_confidence"]),
                side_confidence=float(row["side_confidence"]),
                review_required=row["review_required"].lower() == "true",
            ))
    return trades


def apply_manual_trade_reviews(
    trades: Iterable[TdxTrade], reviews_path: Path,
) -> tuple[list[TdxTrade], int]:
    reviewed = list(trades)
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
                raise ValueError(f"Manual review row does not match a source trade: {key}")
            index = positions[key]
            original = reviewed[index]
            if original.market_time != row["market_time"]:
                raise ValueError(
                    f"Manual review time mismatch for {key}: "
                    f"{original.market_time} != {row['market_time']}"
                )
            side = row["corrected_side"]
            if side not in {"B", "S"}:
                raise ValueError(f"Invalid manually reviewed side for {key}: {side}")
            reviewed[index] = replace(
                original,
                price=float(row["corrected_price"]),
                hands=int(row["corrected_hands"]),
                side=side,
                review_required=False,
            )
            applied += 1
    return reviewed, applied


def _usable_trades(trades: Iterable[TdxTrade]) -> list[TdxTrade]:
    usable = [
        trade for trade in trades
        if trade.side in {"B", "S"}
        and trade.hands is not None
        and trade.hands > 0
        and not trade.review_required
    ]
    usable.sort(key=lambda item: (
        _seconds(item.market_time), item.page_sequence, item.panel, item.row,
    ))
    return usable


def _pair_direction(open_trade: TdxTrade, close_trade: TdxTrade) -> str | None:
    if open_trade.side == "S" and close_trade.side == "B":
        return "buy_then_sell"
    if open_trade.side == "B" and close_trade.side == "S":
        return "sell_then_buy"
    return None


def _pair_edge(open_trade: TdxTrade, close_trade: TdxTrade) -> float:
    direction = _pair_direction(open_trade, close_trade)
    if direction == "buy_then_sell":
        return close_trade.price - open_trade.price
    if direction == "sell_then_buy":
        return open_trade.price - close_trade.price
    return 0.0


def discover_theoretical_pairs(
    trades: Iterable[TdxTrade],
) -> list[TheoreticalOpportunityPair]:
    """Enumerate every ordered opposite-side print pair with a strict positive edge.

    This is a hindsight market-opportunity definition, not a causal trading rule.
    It intentionally has no minimum edge, size, cluster, or lookback requirement.
    """

    usable = _usable_trades(trades)
    pairs: list[TheoreticalOpportunityPair] = []
    for open_offset, open_trade in enumerate(usable):
        for close_offset in range(open_offset + 1, len(usable)):
            close_trade = usable[close_offset]
            direction = _pair_direction(open_trade, close_trade)
            if direction is None:
                continue
            edge = _pair_edge(open_trade, close_trade)
            if edge <= 1e-9:
                continue
            sequence = len(pairs) + 1
            pairs.append(TheoreticalOpportunityPair(
                pair_id=(
                    f"{open_trade.market_date}_{open_trade.code}_P{sequence:06d}"
                ),
                market_date=open_trade.market_date,
                code=open_trade.code,
                direction=direction,
                open_trade_index=open_offset + 1,
                open_time=open_trade.market_time,
                open_side=open_trade.side or "",
                open_price=round(open_trade.price, 3),
                open_hands=int(open_trade.hands or 0),
                close_trade_index=close_offset + 1,
                close_time=close_trade.market_time,
                close_side=close_trade.side or "",
                close_price=round(close_trade.price, 3),
                close_hands=int(close_trade.hands or 0),
                edge=round(edge, 3),
                elapsed_seconds=(
                    _seconds(close_trade.market_time) - _seconds(open_trade.market_time)
                ),
                single_pair_capacity_hands=min(
                    int(open_trade.hands or 0), int(close_trade.hands or 0),
                ),
                source_pages=tuple(sorted({
                    open_trade.source_page, close_trade.source_page,
                })),
            ))
    return pairs


def _maximum_matchable_hands(
    opens: list[TdxTrade], closes: list[TdxTrade], direction: str,
) -> int:
    if direction == "buy_then_sell":
        ordered_opens = sorted(opens, key=lambda item: item.price)
        ordered_closes = sorted(closes, key=lambda item: item.price, reverse=True)
        profitable = lambda left, right: right.price > left.price + 1e-9
    else:
        ordered_opens = sorted(opens, key=lambda item: item.price, reverse=True)
        ordered_closes = sorted(closes, key=lambda item: item.price)
        profitable = lambda left, right: left.price > right.price + 1e-9

    open_remaining = [int(item.hands or 0) for item in ordered_opens]
    close_remaining = [int(item.hands or 0) for item in ordered_closes]
    open_index = close_index = matched = 0
    while open_index < len(ordered_opens) and close_index < len(ordered_closes):
        if not profitable(ordered_opens[open_index], ordered_closes[close_index]):
            break
        quantity = min(open_remaining[open_index], close_remaining[close_index])
        matched += quantity
        open_remaining[open_index] -= quantity
        close_remaining[close_index] -= quantity
        if open_remaining[open_index] == 0:
            open_index += 1
        if close_remaining[close_index] == 0:
            close_index += 1
    return matched


def summarize_local_turns(
    trades: Iterable[TdxTrade],
    pairs: Iterable[TheoreticalOpportunityPair] | None = None,
) -> list[LocalOpportunityTurn]:
    """Compress adjacent aggressor-side runs without redefining theory.

    The exhaustive pair table remains authoritative.  A local turn is included
    whenever at least one pair between two adjacent opposite-side runs has a
    strict positive edge, including a one-tick edge.
    """

    usable = _usable_trades(trades)
    pair_rows = list(pairs) if pairs is not None else discover_theoretical_pairs(usable)
    pair_lookup = {
        (item.open_trade_index, item.close_trade_index): item for item in pair_rows
    }
    runs: list[list[int]] = []
    for index, trade in enumerate(usable, start=1):
        if not runs or usable[runs[-1][-1] - 1].side != trade.side:
            runs.append([index])
        else:
            runs[-1].append(index)

    turns: list[LocalOpportunityTurn] = []
    for open_run, close_run in zip(runs, runs[1:]):
        candidates = [
            pair_lookup[(open_index, close_index)]
            for open_index in open_run
            for close_index in close_run
            if (open_index, close_index) in pair_lookup
        ]
        if not candidates:
            continue
        best = min(
            candidates,
            key=lambda item: (
                -item.edge, item.close_trade_index, item.open_trade_index,
            ),
        )
        first = min(
            candidates,
            key=lambda item: (item.close_trade_index, item.open_trade_index),
        )
        opens = [usable[index - 1] for index in open_run]
        closes = [usable[index - 1] for index in close_run]
        sequence = len(turns) + 1
        turns.append(LocalOpportunityTurn(
            turn_id=(
                f"{best.market_date}_{best.code}_T{sequence:03d}"
            ),
            market_date=best.market_date,
            code=best.code,
            direction=best.direction,
            open_side=best.open_side,
            close_side=best.close_side,
            open_run_start_time=opens[0].market_time,
            open_run_end_time=opens[-1].market_time,
            close_run_start_time=closes[0].market_time,
            close_run_end_time=closes[-1].market_time,
            open_run_hands=sum(int(item.hands or 0) for item in opens),
            close_run_hands=sum(int(item.hands or 0) for item in closes),
            profitable_pair_count=len(candidates),
            first_completion_time=first.close_time,
            best_pair_id=best.pair_id,
            best_open_time=best.open_time,
            best_open_price=best.open_price,
            best_open_hands=best.open_hands,
            best_close_time=best.close_time,
            best_close_price=best.close_price,
            best_close_hands=best.close_hands,
            minimum_positive_edge=min(item.edge for item in candidates),
            maximum_edge=best.edge,
            maximum_matchable_hands=_maximum_matchable_hands(
                opens, closes, best.direction,
            ),
            source_pages=tuple(sorted({
                item.source_page for item in opens + closes
            })),
            review_status="theory_only_strategy_audit_required",
        ))
    return turns


def _edge_distribution(
    pairs: Iterable[TheoreticalOpportunityPair],
) -> dict[str, int]:
    result = {
        "0_to_0.05": 0,
        "0.05_to_0.10": 0,
        "0.10_to_0.20": 0,
        "0.20_to_0.50": 0,
        "0.50_to_1.00": 0,
        "1.00_or_more": 0,
    }
    for item in pairs:
        if item.edge < 0.05:
            result["0_to_0.05"] += 1
        elif item.edge < 0.10:
            result["0.05_to_0.10"] += 1
        elif item.edge < 0.20:
            result["0.10_to_0.20"] += 1
        elif item.edge < 0.50:
            result["0.20_to_0.50"] += 1
        elif item.edge < 1.00:
            result["0.50_to_1.00"] += 1
        else:
            result["1.00_or_more"] += 1
    return result


def _write_dataclass_csv(path: Path, rows: list[object]) -> None:
    if not rows:
        return
    serialized = []
    for item in rows:
        row = asdict(item)
        if "source_pages" in row:
            row["source_pages"] = "|".join(row["source_pages"])
        serialized.append(row)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(serialized[0]))
        writer.writeheader()
        writer.writerows(serialized)


def _write_local_turn_markdown(
    path: Path, *, pairs: list[TheoreticalOpportunityPair],
    local_turns: list[LocalOpportunityTurn], excluded_review_rows: int,
    manual_review_rows: int,
) -> None:
    lines = [
        "# G三峡EB2全天理论机会局部往返（人工核对表）",
        "",
        "口径：底表穷举所有有时间先后的异向成交严格正价差，不设最低价差、成交量、成交簇或回看窗口。下表只是把相邻主动方向成交段压缩成可读的局部往返；完整配对仍以`理论机会配对_全部.csv`为准。",
        "",
        f"- 理论配对：{len(pairs):,}个。",
        f"- 相邻成交段局部往返：{len(local_turns)}段。",
        f"- 已对照原图人工确认并通过侧表纳入：{manual_review_rows}条。",
        f"- 明确排除且等待人工复核的OCR记录：{excluded_review_rows}条。",
        "- `先买后卖`对应`S低→B高`；`先卖后买`对应`B高→S低`，后者只允许卖已有底仓，不表示裸卖空。",
        "- 同一秒内出现多笔成交时，先后顺序沿用通达信画面中的逐行顺序（页码、分栏、行号），不会把同秒记录当成同时发生。",
        "- 最大可匹配手数只在该相邻成交段内避免重复使用同一成交量；不同表行和完整理论配对之间仍可能嵌套，不能直接相加为交易数或利润。",
        "",
        "| 段 | 方向 | 先段 | 后段 | 最佳真实配对 | 最大价差 | 最小正价差 | 最大可匹配 | 嵌套配对 |",
        "|---:|---|---|---|---|---:|---:|---:|---:|",
    ]
    for item in local_turns:
        direction = "先买后卖" if item.direction == "buy_then_sell" else "先卖后买"
        open_window = (
            item.open_run_start_time
            if item.open_run_start_time == item.open_run_end_time
            else f"{item.open_run_start_time}—{item.open_run_end_time}"
        )
        close_window = (
            item.close_run_start_time
            if item.close_run_start_time == item.close_run_end_time
            else f"{item.close_run_start_time}—{item.close_run_end_time}"
        )
        best = (
            f"{item.best_open_time} {item.open_side}{item.best_open_price:.3f} → "
            f"{item.best_close_time} {item.close_side}{item.best_close_price:.3f}"
        )
        lines.append(
            f"| {item.turn_id.rsplit('T', 1)[-1]} | {direction} | {open_window} | "
            f"{close_window} | {best} | {item.maximum_edge:.3f} | "
            f"{item.minimum_positive_edge:.3f} | {item.maximum_matchable_hands}手 | "
            f"{item.profitable_pair_count} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_opportunity_report(
    path: Path, *, trades_path: Path,
    pairs: list[TheoreticalOpportunityPair],
    local_turns: list[LocalOpportunityTurn],
    excluded_review_rows: int,
    manual_review_rows: int = 0,
    manual_reviews_path: Path | None = None,
) -> dict[str, str]:
    definition = {
        "ordered_opposite_side_pairs_only": True,
        "strict_positive_edge_only": True,
        "minimum_edge": None,
        "minimum_hands": None,
        "lookback_seconds": None,
        "cluster_requirement": None,
        "model_signal_used": False,
        "same_second_order": "TDX displayed row order: page, panel, row",
        "compression": (
            "Adjacent aggressor-side runs; exhaustive pairs remain authoritative"
        ),
    }
    payload = {
        "evaluation_layers": EVALUATION_LAYERS,
        "current_layer": "layer_1_theoretical_market",
        "source_trades": str(trades_path.resolve()),
        "definition": definition,
        "review_exclusions": {
            "excluded_rows": excluded_review_rows,
            "reason": "OCR rows explicitly marked review_required are not silently repaired",
        },
        "manual_reviews": {
            "applied_rows": manual_review_rows,
            "source": (
                str(manual_reviews_path.resolve()) if manual_reviews_path else None
            ),
            "original_ocr_confidence_preserved_in_source_csv": True,
        },
        "summary": {
            "theoretical_pairs": len(pairs),
            "buy_then_sell_pairs": sum(
                item.direction == "buy_then_sell" for item in pairs
            ),
            "sell_then_buy_pairs": sum(
                item.direction == "sell_then_buy" for item in pairs
            ),
            "local_turns": len(local_turns),
            "buy_then_sell_turns": sum(
                item.direction == "buy_then_sell" for item in local_turns
            ),
            "sell_then_buy_turns": sum(
                item.direction == "sell_then_buy" for item in local_turns
            ),
            "edge_distribution": _edge_distribution(pairs),
        },
        "theoretical_pairs": [asdict(item) for item in pairs],
        "local_turns": [asdict(item) for item in local_turns],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    pairs_csv = path.with_name("理论机会配对_全部.csv")
    turns_csv = path.with_name("理论机会局部往返_相邻成交段.csv")
    turns_markdown = path.with_name("理论机会局部往返_人工核对.md")
    _write_dataclass_csv(pairs_csv, list(pairs))
    _write_dataclass_csv(turns_csv, list(local_turns))
    _write_local_turn_markdown(
        turns_markdown,
        pairs=pairs,
        local_turns=local_turns,
        excluded_review_rows=excluded_review_rows,
        manual_review_rows=manual_review_rows,
    )
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return {
        "report": str(path),
        "theoretical_pairs_csv": str(pairs_csv),
        "local_turns_csv": str(turns_csv),
        "local_turns_markdown": str(turns_markdown),
    }


def load_opportunity_report(path: Path) -> tuple[dict, list[LocalOpportunityTurn]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    turns = [
        LocalOpportunityTurn(**{
            **row,
            "source_pages": tuple(row["source_pages"]),
        })
        for row in payload["local_turns"]
    ]
    return payload, turns


def replay_registered_models_readonly(
    config: AppConfig, *, market_date: str, bond_code: str,
    priority_policy: MakerPolicyProfile | None = None,
    queue_policy: MakerPolicyProfile | None = None,
    windfall_policy: MakerPolicyProfile | None = None,
) -> dict:
    """Replay current or explicit candidate policies without mutating live state."""

    source_path = config.storage.database.resolve()
    source = sqlite3.connect(
        f"file:{source_path.as_posix()}?mode=ro", uri=True,
    )
    source.row_factory = sqlite3.Row
    try:
        with tempfile.TemporaryDirectory() as temporary:
            replay_config = replace(
                config,
                storage=replace(
                    config.storage,
                    database=Path(temporary) / "maker-audit.sqlite3",
                ),
            )
            store = SQLiteStore(replay_config)
            store.start_session()
            try:
                engine = MakerPaperEngine(
                    replay_config, store, bond_code=bond_code,
                    priority_policy=priority_policy,
                    queue_policy=queue_policy,
                    windfall_policy=windfall_policy,
                )
                ticks = _load_ticks(
                    source,
                    market_date,
                    bond_code,
                    maker_underlying_stock_code(config, bond_code),
                    engine.parameters,
                )
                for tick in ticks:
                    engine.on_replay_tick(tick, persist=True)
                store.connection.commit()

                assignments = {
                    row["strategy_id"]: row["model_id"]
                    for row in store.connection.execute(
                        """SELECT strategy_id,model_id
                           FROM maker_paper_model_assignments
                           WHERE market_date=?""",
                        (market_date,),
                    )
                }
                accounts = [
                    {
                        **dict(row),
                        "model_id": assignments.get(row["strategy_id"]),
                        "additional_buying_capacity": (
                            config.maker_paper.additional_buying_capacity_bonds
                            if row["fill_mode"] != "windfall" else 0.0
                        ),
                        "funding_adjustment": max(
                            0.0,
                            float(row["initial_cash"])
                                - config.maker_paper.initial_cash_cny,
                        ),
                        "customer_base_short_bonds": (
                            max(
                                0.0,
                                float(row["initial_inventory"])
                                    - float(row["inventory"]),
                            )
                            if row["fill_mode"] != "windfall" else 0.0
                        ),
                        "extra_inventory_bonds": (
                            max(
                                0.0,
                                float(row["inventory"])
                                    - float(row["initial_inventory"]),
                            )
                            if row["fill_mode"] != "windfall"
                            else max(0.0, float(row["inventory"]))
                        ),
                    }
                    for row in store.connection.execute(
                        """SELECT strategy_id,fill_mode,initial_inventory,
                                  maximum_inventory,initial_cash,cash,inventory,
                                  trading_pnl,fills,last_bid,last_ask
                           FROM maker_paper_accounts
                           WHERE market_date=? ORDER BY strategy_id""",
                        (market_date,),
                    )
                ]
                ticks_by_id = {tick.tick_id: tick for tick in ticks}
                fills = []
                for row in store.connection.execute(
                    """SELECT order_id,strategy_id,market_ts_ms,side,price,
                              quantity,fill_reason,inventory_after,
                              reference_tick_id
                       FROM maker_paper_fills
                       WHERE market_date=? ORDER BY market_ts_ms,id""",
                    (market_date,),
                ):
                    market_time = datetime.fromtimestamp(
                        row["market_ts_ms"] / 1_000, SHANGHAI,
                    ).strftime("%H:%M:%S")
                    reference_tick_id = int(row["reference_tick_id"] or 0)
                    reference_tick = ticks_by_id.get(reference_tick_id)
                    residual_price = 0.0
                    residual_bonds = 0.0
                    if (
                        row["fill_reason"]
                            == "queue_cleared_crossed_residual_fill"
                        and reference_tick is not None
                    ):
                        if row["side"] == "sell":
                            residual_price = reference_tick.bid1
                            residual_bonds = reference_tick.bid1_bonds
                        else:
                            residual_price = reference_tick.ask1
                            residual_bonds = reference_tick.ask1_bonds
                    fills.append(asdict(ReplayFill(
                        order_id=int(row["order_id"]),
                        strategy_id=row["strategy_id"],
                        model_id=assignments.get(row["strategy_id"], "unregistered"),
                        market_time=market_time,
                        side=row["side"],
                        price=round(float(row["price"]), 3),
                        quantity_bonds=float(row["quantity"]),
                        fill_reason=row["fill_reason"],
                        inventory_after_bonds=float(row["inventory_after"]),
                        reference_tick_id=reference_tick_id,
                        crossed_book_residual_price=round(
                            float(residual_price), 3,
                        ),
                        crossed_book_residual_bonds=float(residual_bonds),
                    )))
                orders = []
                for row in store.connection.execute(
                    """SELECT id,strategy_id,side,status,kind,lot_id,
                               created_market_ts_ms,updated_market_ts_ms,
                               limit_price,quantity,filled_quantity,queue_ahead,
                               target_price,cancel_reason,metadata_json
                        FROM maker_paper_orders
                       WHERE market_date=? ORDER BY created_market_ts_ms,id""",
                    (market_date,),
                ):
                    order = dict(row)
                    order["created_market_time"] = datetime.fromtimestamp(
                        order["created_market_ts_ms"] / 1_000, SHANGHAI,
                    ).strftime("%H:%M:%S")
                    order["updated_market_time"] = datetime.fromtimestamp(
                        order["updated_market_ts_ms"] / 1_000, SHANGHAI,
                    ).strftime("%H:%M:%S")
                    order["model_id"] = assignments.get(
                        order["strategy_id"], "unregistered",
                    )
                    metadata = json.loads(order.pop("metadata_json") or "{}")
                    order["initial_queue_ahead_bonds"] = float(
                        metadata.get("initial_queue_ahead_bonds", order["queue_ahead"])
                    )
                    order["queue_position_kind"] = metadata.get(
                        "queue_position_kind"
                    )
                    orders.append(order)
            finally:
                store.close()
    finally:
        source.close()

    return {
        "market_date": market_date,
        "bond_code": bond_code,
        "underlying_stock_code": maker_underlying_stock_code(
            config, bond_code,
        ),
        "underlying_stock_ticks": sum(
            tick.code == maker_underlying_stock_code(config, bond_code)
            for tick in ticks
        ),
        "underlying_stock_data_available": any(
            tick.code == maker_underlying_stock_code(config, bond_code)
            for tick in ticks
        ),
        "source_database": str(source_path),
        "source_database_opened_readonly": True,
        "temporary_replay_database": True,
        "ticks": len(ticks),
        "accounts": accounts,
        "fills": fills,
        "orders": orders,
    }


def compare_model_capture(
    opportunities: Iterable[LocalOpportunityTurn], replay: dict, *,
    time_tolerance_seconds: int = 6, price_tolerance: float = 0.005,
) -> list[dict]:
    fills = replay["fills"]
    strategies = sorted({
        account["strategy_id"] for account in replay["accounts"]
    })
    comparisons: list[dict] = []
    for opportunity in opportunities:
        expected_open_side = (
            "buy" if opportunity.direction == "buy_then_sell" else "sell"
        )
        expected_close_side = "sell" if expected_open_side == "buy" else "buy"
        branch_results = []
        for strategy_id in strategies:
            matching_open_fills = _matching_fills(
                fills,
                strategy_id=strategy_id,
                side=expected_open_side,
                target_time=opportunity.best_open_time,
                target_price=opportunity.best_open_price,
                time_tolerance_seconds=time_tolerance_seconds,
                price_tolerance=price_tolerance,
            )
            matching_close_fills = _matching_fills(
                fills,
                strategy_id=strategy_id,
                side=expected_close_side,
                target_time=opportunity.best_close_time,
                target_price=opportunity.best_close_price,
                time_tolerance_seconds=time_tolerance_seconds,
                price_tolerance=price_tolerance,
            )
            if matching_open_fills and matching_close_fills:
                capture_status = "best_pair_both_legs_matched"
            elif matching_open_fills:
                capture_status = "best_pair_open_leg_matched_only"
            elif matching_close_fills:
                capture_status = "best_pair_close_leg_matched_only"
            else:
                capture_status = "not_exactly_matched_needs_causal_execution_audit"
            branch_results.append({
                "strategy_id": strategy_id,
                "model_id": next(
                    (
                        account["model_id"] for account in replay["accounts"]
                        if account["strategy_id"] == strategy_id
                    ),
                    "unregistered",
                ),
                "capture_status": capture_status,
                "matching_open_fills": matching_open_fills,
                "matching_close_fills": matching_close_fills,
            })
        comparisons.append({
            "opportunity": asdict(opportunity),
            "expected_open_side": expected_open_side,
            "expected_close_side": expected_close_side,
            "branch_results": branch_results,
        })
    return comparisons


def build_branch_opportunity_diagnostics(
    opportunities: Iterable[LocalOpportunityTurn], replay: dict, *,
    nearby_seconds: int = 120,
) -> list[dict]:
    """Build the traceable layer-2 review base without deciding layer 2.

    The result intentionally separates account capacity, order exposure and
    economic model fills.  None of those fields alone proves that a hindsight
    opportunity was causally recognizable in the user's mode.
    """

    opportunities = list(opportunities)
    accounts = {
        row["strategy_id"]: row
        for row in replay.get("accounts", [])
        if row.get("fill_mode") in {"priority", "queue"}
    }
    fills_by_strategy = {
        strategy_id: [
            row for row in replay.get("fills", [])
            if row["strategy_id"] == strategy_id
        ]
        for strategy_id in accounts
    }
    orders_by_strategy = {
        strategy_id: [
            row for row in replay.get("orders", [])
            if row["strategy_id"] == strategy_id
        ]
        for strategy_id in accounts
    }
    exact_comparisons = {
        row["opportunity"]["turn_id"]: {
            branch["strategy_id"]: branch
            for branch in row["branch_results"]
            if branch["strategy_id"] in accounts
        }
        for row in compare_model_capture(opportunities, replay)
    }

    rows: list[dict] = []
    for opportunity in opportunities:
        expected_open_side = (
            "buy" if opportunity.direction == "buy_then_sell" else "sell"
        )
        expected_close_side = (
            "sell" if expected_open_side == "buy" else "buy"
        )
        open_second = _seconds(opportunity.best_open_time)
        open_window_second = _seconds(opportunity.open_run_start_time)
        close_window_second = _seconds(opportunity.close_run_end_time)
        tape_capacity_bonds = min(
            opportunity.best_open_hands,
            opportunity.best_close_hands,
        ) * 10

        for strategy_id, account in accounts.items():
            fills = fills_by_strategy[strategy_id]
            orders = orders_by_strategy[strategy_id]
            strict_state = _account_state_before_second(
                account, fills, open_second, inclusive=False,
            )
            inclusive_state = _account_state_before_second(
                account, fills, open_second, inclusive=True,
            )
            strict_capacity = _opening_action_capacity(
                expected_open_side,
                opportunity.best_open_price,
                strict_state,
                float(account["maximum_inventory"]),
            )
            inclusive_capacity = _opening_action_capacity(
                expected_open_side,
                opportunity.best_open_price,
                inclusive_state,
                float(account["maximum_inventory"]),
            )
            active_open_orders = _active_reaching_orders(
                orders,
                side=expected_open_side,
                target_second=open_second,
                target_price=opportunity.best_open_price,
            )
            preexisting_open_orders = [
                order for order in active_open_orders
                if _seconds(order["created_market_time"]) < open_second
            ]
            exact = exact_comparisons[opportunity.turn_id][strategy_id]
            in_turn_pair = _best_economic_fill_pair(
                fills,
                open_side=expected_open_side,
                close_side=expected_close_side,
                start_second=open_window_second,
                end_second=close_window_second,
            )
            nearby_pair = _best_economic_fill_pair(
                fills,
                open_side=expected_open_side,
                close_side=expected_close_side,
                start_second=max(0, open_window_second - nearby_seconds),
                end_second=close_window_second + nearby_seconds,
                overlap_start_second=open_window_second,
                overlap_end_second=close_window_second,
            )
            cross_turn_pair = _best_economic_fill_pair(
                fills,
                open_side=expected_open_side,
                close_side=expected_close_side,
                start_second=max(0, open_window_second - 600),
                end_second=close_window_second + 600,
                overlap_start_second=open_window_second,
                overlap_end_second=close_window_second,
            )
            required_bonds = min(1_000, tape_capacity_bonds)
            capacity_low = min(strict_capacity, inclusive_capacity)
            capacity_high = max(strict_capacity, inclusive_capacity)
            if exact["capture_status"] == "best_pair_both_legs_matched":
                preliminary_status = "exact_best_pair_capture"
            elif in_turn_pair is not None:
                preliminary_status = (
                    "economic_pair_within_turn_needs_overlap_review"
                )
            elif nearby_pair is not None:
                preliminary_status = (
                    "economic_pair_nearby_needs_cross_turn_review"
                )
            elif cross_turn_pair is not None:
                preliminary_status = (
                    "economic_pair_cross_turn_600s_needs_path_review"
                )
            elif capacity_high <= 0:
                preliminary_status = "opening_inventory_or_cash_capacity_blocked"
            elif capacity_high < required_bonds:
                preliminary_status = "opening_capacity_partial"
            elif preexisting_open_orders:
                preliminary_status = (
                    "preexisting_reaching_order_needs_execution_audit"
                )
            else:
                preliminary_status = "causal_mode_in_review_required"

            rows.append({
                "turn_id": opportunity.turn_id,
                "market_date": opportunity.market_date,
                "code": opportunity.code,
                "direction": opportunity.direction,
                "best_open_time": opportunity.best_open_time,
                "best_open_price": opportunity.best_open_price,
                "best_close_time": opportunity.best_close_time,
                "best_close_price": opportunity.best_close_price,
                "maximum_edge": opportunity.maximum_edge,
                "maximum_matchable_hands": opportunity.maximum_matchable_hands,
                "best_pair_tape_capacity_bonds": tape_capacity_bonds,
                "expected_open_side": expected_open_side,
                "expected_close_side": expected_close_side,
                "strategy_id": strategy_id,
                "fill_mode": account["fill_mode"],
                "model_id": account.get("model_id", "unregistered"),
                "initial_inventory_bonds": float(account["initial_inventory"]),
                "maximum_inventory_bonds": float(account["maximum_inventory"]),
                "inventory_before_open_strict_bonds": strict_state["inventory"],
                "inventory_after_open_second_bonds": inclusive_state["inventory"],
                "cash_before_open_strict": round(strict_state["cash"], 3),
                "cash_after_open_second": round(inclusive_state["cash"], 3),
                "opening_action_capacity_strict_bonds": strict_capacity,
                "opening_action_capacity_including_same_second_bonds": (
                    inclusive_capacity
                ),
                "opening_action_capacity_low_bonds": capacity_low,
                "opening_action_capacity_high_bonds": capacity_high,
                "standard_or_tape_required_bonds": required_bonds,
                "active_reaching_open_order_count": len(active_open_orders),
                "preexisting_reaching_open_order_count": len(
                    preexisting_open_orders
                ),
                "active_reaching_open_orders": active_open_orders,
                "exact_capture_status": exact["capture_status"],
                "matching_open_fills": exact["matching_open_fills"],
                "matching_close_fills": exact["matching_close_fills"],
                "economic_pair_within_turn": in_turn_pair,
                "economic_pair_nearby_120s": nearby_pair,
                "economic_pair_cross_turn_600s": cross_turn_pair,
                "preliminary_status": preliminary_status,
                "causal_mode_in_status": "unreviewed",
                "final_capture_class": "unreviewed",
            })
    return rows


def _account_state_before_second(
    account: dict, fills: list[dict], target_second: int, *, inclusive: bool,
) -> dict[str, float]:
    inventory = float(account["initial_inventory"])
    cash = float(account["initial_cash"])
    for fill in fills:
        fill_second = _seconds(fill["market_time"])
        if fill_second > target_second or (
            fill_second == target_second and not inclusive
        ):
            continue
        quantity = float(fill["quantity_bonds"])
        price = float(fill["price"])
        if fill["side"] == "buy":
            inventory += quantity
            cash -= price * quantity
        else:
            inventory -= quantity
            cash += price * quantity
    return {"inventory": inventory, "cash": cash}


def _opening_action_capacity(
    side: str, price: float, state: dict[str, float], maximum_inventory: float,
) -> int:
    if side == "sell":
        return max(0, int(state["inventory"] + 1e-9))
    inventory_capacity = max(0, int(maximum_inventory - state["inventory"] + 1e-9))
    cash_capacity = max(0, int((state["cash"] + 1e-9) / price))
    return min(inventory_capacity, cash_capacity)


def _active_reaching_orders(
    orders: list[dict], *, side: str, target_second: int, target_price: float,
) -> list[dict]:
    result = []
    for order in orders:
        if order["side"] != side:
            continue
        created = _seconds(order["created_market_time"])
        updated = _seconds(order["updated_market_time"])
        if not (created <= target_second <= updated):
            continue
        limit_price = float(order["limit_price"])
        reaches = (
            limit_price >= target_price - 0.0005
            if side == "buy"
            else limit_price <= target_price + 0.0005
        )
        if not reaches:
            continue
        result.append({
            "order_id": int(order["id"]),
            "kind": order["kind"],
            "limit_price": limit_price,
            "quantity_bonds": float(order["quantity"]),
            "filled_quantity_bonds": float(order["filled_quantity"]),
            "created_market_time": order["created_market_time"],
            "updated_market_time": order["updated_market_time"],
            "status": order["status"],
            "cancel_reason": order.get("cancel_reason"),
            "initial_queue_ahead_bonds": float(
                order.get("initial_queue_ahead_bonds", 0.0)
            ),
            "final_queue_ahead_bonds": float(order.get("queue_ahead", 0.0)),
            "created_same_second_as_tape": created == target_second,
        })
    return result


def _best_economic_fill_pair(
    fills: list[dict], *, open_side: str, close_side: str,
    start_second: int, end_second: int,
    overlap_start_second: int | None = None,
    overlap_end_second: int | None = None,
) -> dict | None:
    window = [
        row for row in fills
        if start_second <= _seconds(row["market_time"]) <= end_second
    ]
    best: dict | None = None
    for open_index, open_fill in enumerate(window):
        if open_fill["side"] != open_side:
            continue
        open_fill_second = _seconds(open_fill["market_time"])
        for close_fill in window[open_index + 1:]:
            if close_fill["side"] != close_side:
                continue
            close_fill_second = _seconds(close_fill["market_time"])
            if overlap_start_second is not None and overlap_end_second is not None:
                overlaps_core = (
                    overlap_start_second <= open_fill_second <= overlap_end_second
                    or overlap_start_second <= close_fill_second <= overlap_end_second
                    or (
                        open_fill_second <= overlap_start_second
                        and close_fill_second >= overlap_end_second
                    )
                )
                if not overlaps_core:
                    continue
            edge = (
                float(close_fill["price"]) - float(open_fill["price"])
                if open_side == "buy"
                else float(open_fill["price"]) - float(close_fill["price"])
            )
            if edge <= 0:
                continue
            quantity = min(
                float(open_fill["quantity_bonds"]),
                float(close_fill["quantity_bonds"]),
            )
            candidate = {
                "open_time": open_fill["market_time"],
                "open_price": float(open_fill["price"]),
                "open_quantity_bonds": float(open_fill["quantity_bonds"]),
                "close_time": close_fill["market_time"],
                "close_price": float(close_fill["price"]),
                "close_quantity_bonds": float(close_fill["quantity_bonds"]),
                "matched_quantity_bonds": quantity,
                "edge": round(edge, 3),
                "gross_edge_cash": round(edge * quantity, 3),
            }
            if best is None or (
                candidate["gross_edge_cash"], candidate["edge"]
            ) > (best["gross_edge_cash"], best["edge"]):
                best = candidate
    return best


def write_branch_opportunity_diagnostics(
    path: Path, *, opportunity_source: Path, replay: dict,
    diagnostics: list[dict],
) -> dict[str, str]:
    """Write the layer-2 review base plus a durable manual-review sidecar."""

    path.parent.mkdir(parents=True, exist_ok=True)
    csv_path = path.with_suffix(".csv")
    markdown_path = path.with_suffix(".md")
    review_path = path.with_name(f"{path.stem}_人工因果复核.csv")
    strategy_ids = sorted({row["strategy_id"] for row in diagnostics})
    summary_by_strategy = {
        strategy_id: {
            status: sum(
                row["preliminary_status"] == status
                for row in diagnostics
                if row["strategy_id"] == strategy_id
            )
            for status in sorted({
                row["preliminary_status"]
                for row in diagnostics
                if row["strategy_id"] == strategy_id
            })
        }
        for strategy_id in strategy_ids
    }
    payload = {
        "evaluation_layers": EVALUATION_LAYERS,
        "current_layer": "layer_2_causal_mode_in.review_base",
        "opportunity_source": str(opportunity_source.resolve()),
        "model_replay": {
            "market_date": replay["market_date"],
            "bond_code": replay["bond_code"],
            "source_database": replay["source_database"],
            "source_database_opened_readonly": replay[
                "source_database_opened_readonly"
            ],
            "temporary_replay_database": replay[
                "temporary_replay_database"
            ],
            "accounts": replay["accounts"],
        },
        "scope": {
            "one_row_per_local_turn_and_execution_branch": True,
            "same_second_inventory_is_a_range": True,
            "cash_and_inventory_capacity_are_reconstructed_from_model_fills": True,
            "nearby_economic_pair_is_not_final_capture": True,
            "preexisting_order_is_not_proof_of_queue_fill": True,
            "causal_mode_in_requires_explicit_review": True,
        },
        "summary": {
            "local_turns": len({row["turn_id"] for row in diagnostics}),
            "branch_rows": len(diagnostics),
            "preliminary_status_by_strategy": summary_by_strategy,
        },
        "diagnostics": diagnostics,
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    flat_rows = [_flatten_branch_diagnostic(row) for row in diagnostics]
    if flat_rows:
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(flat_rows[0]))
            writer.writeheader()
            writer.writerows(flat_rows)

    existing_reviews = _load_existing_causal_reviews(review_path)
    review_fields = [
        "turn_id", "strategy_id", "model_id", "direction", "maximum_edge",
        "best_open_time", "best_open_price", "best_close_time",
        "best_close_price", "preliminary_status", "causal_mode_in_status",
        "final_capture_class", "causal_reason", "review_notes",
    ]
    with review_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=review_fields)
        writer.writeheader()
        for row in diagnostics:
            key = (row["turn_id"], row["strategy_id"])
            existing = existing_reviews.get(key, {})
            writer.writerow({
                field: existing.get(field, row.get(field, ""))
                for field in review_fields
            })

    _write_branch_diagnostic_markdown(
        markdown_path,
        diagnostics=diagnostics,
        summary_by_strategy=summary_by_strategy,
        review_path=review_path,
    )
    return {
        "report": str(path),
        "csv": str(csv_path),
        "markdown": str(markdown_path),
        "manual_review_csv": str(review_path),
    }


def _flatten_branch_diagnostic(row: dict) -> dict:
    result = dict(row)
    for field in (
        "active_reaching_open_orders", "matching_open_fills",
        "matching_close_fills", "economic_pair_within_turn",
        "economic_pair_nearby_120s",
        "economic_pair_cross_turn_600s",
    ):
        result[field] = json.dumps(result[field], ensure_ascii=False)
    return result


def _load_existing_causal_reviews(path: Path) -> dict[tuple[str, str], dict]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {
            (row["turn_id"], row["strategy_id"]): row
            for row in csv.DictReader(handle)
        }


def _write_branch_diagnostic_markdown(
    path: Path, *, diagnostics: list[dict], summary_by_strategy: dict,
    review_path: Path,
) -> None:
    lines = [
        "# G三峡EB2理论机会—分支容量与捕获诊断底表",
        "",
        "本表用于第二层因果复核。库存容量、订单在场或附近经济双腿都不能单独证明机会属于模式内，也不能直接作为覆盖率分母。排队分支即使订单在场，仍须另查真实前队和保守缓冲。",
        "",
        f"- 分支诊断行：{len(diagnostics)}。",
        f"- 人工因果复核侧表：`{review_path.name}`。",
        "",
        "## 初步诊断分布",
        "",
        "| 分支 | 初步状态 | 数量 |",
        "|---|---|---:|",
    ]
    for strategy_id, statuses in summary_by_strategy.items():
        for status, count in statuses.items():
            lines.append(f"| {strategy_id} | {status} | {count} |")

    candidates = sorted(
        (
            row for row in diagnostics
            if row["maximum_edge"] >= 0.18
            and row["opening_action_capacity_high_bonds"] >= min(
                1_000, row["best_pair_tape_capacity_bonds"]
            )
            and row["preliminary_status"] in {
                "causal_mode_in_review_required",
                "preexisting_reaching_order_needs_execution_audit",
            }
        ),
        key=lambda row: (-row["maximum_edge"], row["best_open_time"]),
    )
    lines.extend([
        "",
        "## 优先人工因果复核候选",
        "",
        "筛选条件仅为价差至少约0.18元、开腿容量足够且没有段内/邻近经济双腿；仍须倒回事前盘口判断是否模式内。",
        "",
        "| 分支 | 段 | 方向 | 开腿→闭腿 | 最大价差 | 开腿容量低—高 | 初步状态 |",
        "|---|---|---|---|---:|---:|---|",
    ])
    for row in candidates[:40]:
        lines.append(
            f"| {row['strategy_id']} | {row['turn_id'].rsplit('T', 1)[-1]} | "
            f"{row['direction']} | {row['best_open_time']} {row['best_open_price']:.3f}"
            f"→{row['best_close_time']} {row['best_close_price']:.3f} | "
            f"{row['maximum_edge']:.3f} | "
            f"{row['opening_action_capacity_low_bonds']:.0f}—"
            f"{row['opening_action_capacity_high_bonds']:.0f} | "
            f"{row['preliminary_status']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _matching_fills(
    fills: list[dict], *, strategy_id: str, side: str, target_time: str,
    target_price: float, time_tolerance_seconds: int, price_tolerance: float,
) -> list[dict]:
    target_seconds = _seconds(target_time)
    result = []
    for fill in fills:
        if fill["strategy_id"] != strategy_id or fill["side"] != side:
            continue
        if abs(_seconds(fill["market_time"]) - target_seconds) > time_tolerance_seconds:
            continue
        price_matches = (
            fill["price"] <= target_price + price_tolerance
            if side == "buy"
            else fill["price"] >= target_price - price_tolerance
        )
        if price_matches:
            result.append(fill)
    return result


def write_model_opportunity_audit(
    path: Path, *, opportunity_source: Path, opportunity_definition: dict,
    replay: dict, comparisons: list[dict],
) -> None:
    payload = {
        "evaluation_layers": EVALUATION_LAYERS,
        "current_layer": "layer_3_branch_capture.exact_best_pair_diagnostic",
        "opportunity_source": str(opportunity_source.resolve()),
        "opportunity_definition": opportunity_definition,
        "model_replay": replay,
        "comparison_scope": {
            "matched_means_best_local_pair_legs_match_time_and_price": True,
            "unmatched_never_automatically_means_missed": True,
            "inventory_book_and_queue_still_require_causal_audit": True,
            "time_tolerance_seconds": 6,
            "price_tolerance": 0.005,
        },
        "summary": {
            "local_turns": len(comparisons),
            "best_pair_both_legs_matched_by_strategy": {
                account["strategy_id"]: sum(
                    branch["capture_status"] == "best_pair_both_legs_matched"
                    for item in comparisons
                    for branch in item["branch_results"]
                    if branch["strategy_id"] == account["strategy_id"]
                )
                for account in replay["accounts"]
                if "super_windfall" not in account["strategy_id"]
            },
        },
        "comparisons": comparisons,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
