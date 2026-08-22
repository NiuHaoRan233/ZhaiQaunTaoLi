from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable, Literal

from .maker import ReplayTick


MODEL_ID = "maker_whale_v0_1_candidate"
WHALE_V02_MODEL_ID = "maker_whale_v0_2_candidate"

BookSide = Literal["bid", "ask"]
OrderSide = Literal["buy", "sell"]


@dataclass(frozen=True)
class WhaleResearchParameters:
    """Transparent, paper-only parameters for the first whale-following study.

    Quantities are bonds, not hands.  The default account is deliberately much
    larger than the ordinary maker account: 10,000 opening bonds and enough
    paper cash for another 10,000.  At prices near CNY136 this is about CNY2.72m
    of gross capacity.  The first quote tranche is 5,000 bonds.
    """

    model_id: str = MODEL_ID
    tick_size: float = 0.001
    lot_size_bonds: int = 10
    quote_quantity_bonds: int = 5_000
    opening_inventory_bonds: int = 10_000
    additional_capacity_bonds: int = 10_000
    minimum_wall_bonds: int = 10_000
    minimum_wall_to_quote_ratio: float = 2.0
    wall_stability_seconds: int = 15
    maximum_wall_gap_from_inside: float = 0.50
    minimum_quote_edge: float = 0.20
    opening_caution_edge: float = 1.00
    earliest_quote_time: str = "09:20:00.000"
    opening_caution_end_time: str = "09:30:00.000"
    latest_quote_time: str = "15:29:59.999"
    risk_window_seconds: int = 30
    wall_damage_bonds: int = 5_000
    one_way_fee_bps: float = 0.0

    @property
    def maximum_inventory_bonds(self) -> int:
        return self.opening_inventory_bonds + self.additional_capacity_bonds

    @property
    def effective_minimum_wall_bonds(self) -> float:
        return max(
            float(self.minimum_wall_bonds),
            self.quote_quantity_bonds * self.minimum_wall_to_quote_ratio,
        )


@dataclass
class WallState:
    side: BookSide
    price: float
    continuous_since_ms: int
    last_seen_ms: int
    current_bonds: float
    peak_bonds: float


@dataclass
class WhaleOrder:
    side: OrderSide
    price: float
    quantity_bonds: int
    remaining_bonds: int
    queue_ahead_bonds: float
    created_ts_ms: int
    wall_side: BookSide
    wall_price: float
    wall_peak_bonds: float


@dataclass
class ExposureChunk:
    direction: Literal["long", "short"]
    quantity_bonds: int
    backing_wall_price: float
    backing_wall_peak_bonds: float
    opened_ts_ms: int
    escape_attempted: bool = False


@dataclass(frozen=True)
class WhaleFill:
    model_id: str
    code: str
    market_date: str
    market_ts_ms: int
    market_time: str
    side: OrderSide
    price: float
    quantity_bonds: int
    fill_kind: Literal["passive", "active_escape"]
    reason: str
    wall_price: float
    inventory_after_bonds: int


@dataclass(frozen=True)
class WhaleQuoteEvent:
    model_id: str
    code: str
    market_date: str
    market_ts_ms: int
    market_time: str
    action: Literal["place", "cancel"]
    side: OrderSide
    price: float
    quantity_bonds: int
    queue_ahead_bonds: float
    wall_price: float
    wall_bonds: float
    reason: str


@dataclass(frozen=True)
class WhaleDailyResult:
    model_id: str
    code: str
    market_date: str
    opening_inventory_bonds: int
    maximum_inventory_bonds: int
    quote_quantity_bonds: int
    minimum_wall_bonds: int
    placed_orders: int
    cancelled_orders: int
    passive_fills: int
    active_escape_fills: int
    passive_fill_bonds: int
    active_escape_bonds: int
    completed_turns: int
    ending_inventory_bonds: int
    ending_inventory_deviation_bonds: int
    maximum_absolute_inventory_deviation_bonds: int
    stranded_exposure_bonds: int
    failed_escape_chunks: int
    turnover_cny: float
    fees_cny: float
    marked_pnl_cny: float
    first_midpoint: float
    final_midpoint: float


@dataclass(frozen=True)
class WhaleV02Parameters:
    """Frozen parameters for the layered whale-liquidity candidate.

    This profile is intentionally separate from :class:`WhaleResearchParameters`
    so the registered v0.1 replay and its historical results cannot change.
    Quantities are bonds throughout.
    """

    model_id: str = WHALE_V02_MODEL_ID
    tick_size: float = 0.001
    lot_size_bonds: int = 10
    opening_inventory_bonds: int = 10_000
    additional_capacity_bonds: int = 10_000
    observation_wall_bonds: int = 10_000
    probe_quantity_bonds: int = 1_000
    maximum_cumulative_risk_bonds: int = 5_000
    wall_risk_fraction: float = 0.20
    probe_stability_seconds: int = 30
    probe_minimum_adjacent_observations: int = 3
    full_size_stability_seconds: int = 60
    wall_recovery_seconds: int = 30
    wall_maintained_ratio: float = 0.80
    unexplained_shrink_ratio: float = 0.20
    significant_attack_damage_bonds: int = 5_000
    probe_maximum_wall_gap: float = 0.30
    maximum_backup_gap: float = 0.20
    full_size_maximum_wall_gap: float = 0.10
    minimum_quote_edge: float = 0.20
    full_size_minimum_edge: float = 0.50
    adverse_midpoint_move: float = 0.20
    adverse_window_seconds: int = 30
    passive_trade_attribution_ratio: float = 1.0
    earliest_quote_time: str = "09:20:00.000"
    opening_caution_end_time: str = "09:30:00.000"
    opening_caution_edge: float = 1.00
    latest_quote_time: str = "15:29:59.999"
    one_way_fee_bps: float = 0.0

    @property
    def maximum_inventory_bonds(self) -> int:
        return self.opening_inventory_bonds + self.additional_capacity_bonds


@dataclass
class WhaleV02WallEpisode:
    episode_id: int
    side: BookSide
    price: float
    first_seen_ms: int
    last_seen_ms: int
    last_sequence: int
    adjacent_observations: int
    current_bonds: float
    peak_bonds: float
    safe: bool = True
    ended: bool = False
    unsafe_reason: str | None = None
    probe_filled_ts_ms: int | None = None
    probe_opened_bonds: int = 0
    survived_attacks: int = 0
    pending_attack_ts_ms: int | None = None
    pending_attack_reference_bonds: float = 0.0
    risk_exit_required_ts_ms: int | None = None
    reentry_blocked: bool = False


@dataclass
class WhaleV02Order:
    tranche_id: int
    side: OrderSide
    price: float
    quantity_bonds: int
    remaining_bonds: int
    queue_ahead_bonds: float
    created_ts_ms: int
    wall_episode_id: int
    wall_price: float
    cumulative_target_bonds: int
    certification_count: int
    exit_only: bool


@dataclass
class WhaleV02Exposure:
    risk_block_id: int
    direction: Literal["long", "short"]
    quantity_bonds: int
    entry_price: float
    wall_episode_id: int
    wall_price: float
    opened_ts_ms: int
    escape_attempted: bool = False


@dataclass(frozen=True)
class WhaleV02Fill:
    model_id: str
    code: str
    market_date: str
    market_ts_ms: int
    market_time: str
    side: OrderSide
    price: float
    quantity_bonds: int
    fill_kind: Literal["passive", "active_risk_exit"]
    reason: str
    tranche_id: int
    risk_block_id: int
    affected_risk_block_ids: tuple[int, ...]
    wall_episode_id: int
    wall_price: float
    wall_current_bonds: float
    wall_peak_bonds: float
    cumulative_target_bonds: int
    certification_count: int
    realized_gross_pnl_cny: float
    inventory_after_bonds: int


@dataclass(frozen=True)
class WhaleV02QuoteEvent:
    model_id: str
    code: str
    market_date: str
    market_ts_ms: int
    market_time: str
    action: Literal["place", "cancel"]
    side: OrderSide
    price: float
    quantity_bonds: int
    queue_ahead_bonds: float
    wall_episode_id: int
    wall_price: float
    wall_bonds: float
    cumulative_target_bonds: int
    certification_count: int
    reason: str


@dataclass(frozen=True)
class WhaleV02DailyResult:
    model_id: str
    code: str
    market_date: str
    opening_inventory_bonds: int
    maximum_inventory_bonds: int
    placed_orders: int
    cancelled_orders: int
    passive_fills: int
    active_risk_exit_fills: int
    passive_fill_bonds: int
    active_risk_exit_bonds: int
    completed_turns: int
    maximum_cumulative_risk_bonds: int
    maximum_absolute_inventory_deviation_bonds: int
    ending_inventory_bonds: int
    ending_inventory_deviation_bonds: int
    stranded_exposure_bonds: int
    failed_risk_exit_blocks: int
    created_risk_blocks: int
    realized_closed_loop_gross_pnl_cny: float
    open_exposure_marked_contribution_cny: float
    fees_cny: float
    marked_pnl_cny: float
    accounting_residual_cny: float
    attributed_passive_fill_ratio: float
    first_midpoint: float
    final_midpoint: float


def _in_session(market_time: str, parameters: WhaleResearchParameters) -> bool:
    if not (
        parameters.earliest_quote_time
        <= market_time
        <= parameters.latest_quote_time
    ):
        return False
    return not ("11:30:00.001" <= market_time < "13:00:00.000")


def _book_map(tick: ReplayTick, side: BookSide) -> dict[float, float]:
    levels = tick.bids if side == "bid" else tick.asks
    return {round(price, 3): float(quantity) for price, quantity in levels if price > 0}


def _quantity_at(tick: ReplayTick, side: BookSide, price: float) -> float:
    return _book_map(tick, side).get(round(price, 3), 0.0)


def _midpoint(tick: ReplayTick) -> float:
    if tick.bid1 > 0 and tick.ask1 >= tick.bid1:
        return (tick.bid1 + tick.ask1) / 2.0
    return tick.last_price


def _wall_is_near_inside(
    tick: ReplayTick,
    side: BookSide,
    price: float,
    maximum_gap: float,
) -> bool:
    if side == "bid":
        return tick.bid1 > 0 and tick.bid1 - price <= maximum_gap + 1e-9
    return tick.ask1 > 0 and price - tick.ask1 <= maximum_gap + 1e-9


def _update_walls(
    walls: dict[tuple[BookSide, float], WallState],
    tick: ReplayTick,
    parameters: WhaleResearchParameters,
) -> None:
    minimum = parameters.effective_minimum_wall_bonds
    current: dict[tuple[BookSide, float], float] = {}
    for side, levels in (("bid", tick.bids), ("ask", tick.asks)):
        for price, quantity in levels:
            if price <= 0:
                continue
            current[(side, round(price, 3))] = float(quantity)

    for key, wall in walls.items():
        quantity = current.get(key, 0.0)
        was_qualified = wall.current_bonds + 1e-9 >= minimum
        wall.current_bonds = quantity
        if quantity + 1e-9 >= minimum:
            if not was_qualified:
                wall.continuous_since_ms = tick.market_ts_ms
            if wall.last_seen_ms != tick.market_ts_ms:
                wall.last_seen_ms = tick.market_ts_ms
            wall.peak_bonds = max(wall.peak_bonds, quantity)
        else:
            # A later recovery is a new continuous wall, not proof that the old
            # displayed size remained available while it was below threshold.
            wall.continuous_since_ms = tick.market_ts_ms

    for key, quantity in current.items():
        if quantity + 1e-9 < minimum:
            continue
        existing = walls.get(key)
        if existing is None:
            walls[key] = WallState(
                side=key[0],
                price=key[1],
                continuous_since_ms=tick.market_ts_ms,
                last_seen_ms=tick.market_ts_ms,
                current_bonds=quantity,
                peak_bonds=quantity,
            )
        elif existing.current_bonds + 1e-9 >= minimum:
            existing.last_seen_ms = tick.market_ts_ms


def _eligible_walls(
    walls: dict[tuple[BookSide, float], WallState],
    tick: ReplayTick,
    side: BookSide,
    parameters: WhaleResearchParameters,
) -> list[WallState]:
    minimum = parameters.effective_minimum_wall_bonds
    age_ms = parameters.wall_stability_seconds * 1_000
    candidates = [
        wall
        for wall in walls.values()
        if wall.side == side
        and wall.current_bonds + 1e-9 >= minimum
        and tick.market_ts_ms - wall.continuous_since_ms >= age_ms
        and _wall_is_near_inside(
            tick,
            side,
            wall.price,
            parameters.maximum_wall_gap_from_inside,
        )
    ]
    if side == "bid":
        return sorted(candidates, key=lambda wall: (-wall.price, -wall.current_bonds))
    return sorted(candidates, key=lambda wall: (wall.price, -wall.current_bonds))


def _required_edge(tick: ReplayTick, parameters: WhaleResearchParameters) -> float:
    if tick.market_time < parameters.opening_caution_end_time:
        return parameters.opening_caution_edge
    return parameters.minimum_quote_edge


def _desired_quote(
    tick: ReplayTick,
    side: OrderSide,
    wall: WallState,
    parameters: WhaleResearchParameters,
) -> float | None:
    if side == "buy":
        price = round(wall.price + parameters.tick_size, 3)
        edge = tick.ask1 - price
        if not (tick.ask1 > price):
            return None
    else:
        price = round(wall.price - parameters.tick_size, 3)
        edge = price - tick.bid1
        if not (price > tick.bid1):
            return None
    if edge + 1e-9 < _required_edge(tick, parameters):
        return None
    return price


def _order_is_still_valid(
    order: WhaleOrder,
    walls: dict[tuple[BookSide, float], WallState],
    tick: ReplayTick,
    parameters: WhaleResearchParameters,
) -> bool:
    wall = walls.get((order.wall_side, round(order.wall_price, 3)))
    if wall is None or wall.current_bonds + 1e-9 < parameters.effective_minimum_wall_bonds:
        return False
    if not _wall_is_near_inside(
        tick,
        order.wall_side,
        order.wall_price,
        parameters.maximum_wall_gap_from_inside,
    ):
        return False
    return _desired_quote(tick, order.side, wall, parameters) == order.price


def _passive_fillable(order: WhaleOrder, tick: ReplayTick) -> bool:
    if tick.trade_bonds <= 0 or tick.market_ts_ms <= order.created_ts_ms:
        return False
    if order.side == "buy":
        return tick.inferred_side == "sell" and tick.last_price <= order.price + 1e-9
    return tick.inferred_side == "buy" and tick.last_price + 1e-9 >= order.price


def _round_lot(quantity: float, lot_size: int) -> int:
    return max(0, int(quantity // lot_size) * lot_size)


def _marketable_vwap(
    levels: tuple[tuple[float, float], ...],
    quantity_bonds: int,
    *,
    minimum_price: float | None = None,
    maximum_price: float | None = None,
) -> float | None:
    remaining = float(quantity_bonds)
    notional = 0.0
    for price, displayed in levels:
        if minimum_price is not None and price + 1e-9 < minimum_price:
            break
        if maximum_price is not None and price > maximum_price + 1e-9:
            break
        take = min(remaining, float(displayed))
        notional += take * price
        remaining -= take
        if remaining <= 1e-9:
            return notional / quantity_bonds
    return None


def _apply_exposure_fill(
    chunks: list[ExposureChunk],
    side: OrderSide,
    quantity_bonds: int,
    *,
    wall_price: float,
    wall_peak_bonds: float,
    market_ts_ms: int,
) -> None:
    remaining = quantity_bonds
    opposite = "short" if side == "buy" else "long"
    index = 0
    while index < len(chunks) and remaining > 0:
        chunk = chunks[index]
        if chunk.direction != opposite:
            index += 1
            continue
        closed = min(remaining, chunk.quantity_bonds)
        remaining -= closed
        chunk.quantity_bonds -= closed
        if chunk.quantity_bonds == 0:
            chunks.pop(index)
        else:
            index += 1
    if remaining:
        chunks.append(ExposureChunk(
            direction="long" if side == "buy" else "short",
            quantity_bonds=remaining,
            backing_wall_price=wall_price,
            backing_wall_peak_bonds=wall_peak_bonds,
            opened_ts_ms=market_ts_ms,
        ))


def run_day(
    ticks: Iterable[ReplayTick],
    *,
    parameters: WhaleResearchParameters | None = None,
) -> tuple[WhaleDailyResult, list[WhaleFill], list[WhaleQuoteEvent]]:
    """Replay one bond/day without writing to SQLite or any broker interface."""

    parameters = parameters or WhaleResearchParameters()
    rows = sorted(
        (tick for tick in ticks if tick.code.startswith("132")),
        key=lambda tick: (tick.market_ts_ms, tick.tick_id),
    )
    if not rows:
        raise ValueError("whale replay needs at least one exchangeable-bond tick")
    code = rows[0].code
    market_date = rows[0].market_date
    valid_mids = [_midpoint(tick) for tick in rows if _midpoint(tick) > 0]
    if not valid_mids:
        raise ValueError("whale replay has no valid midpoint")
    first_midpoint = valid_mids[0]
    initial_cash = parameters.additional_capacity_bonds * first_midpoint
    cash = initial_cash
    inventory = parameters.opening_inventory_bonds
    maximum_deviation = 0
    completed_turns = 0
    previous_deviation = 0
    orders: dict[OrderSide, WhaleOrder] = {}
    walls: dict[tuple[BookSide, float], WallState] = {}
    exposures: list[ExposureChunk] = []
    recent_trades: deque[tuple[int, str, float, float]] = deque()
    fills: list[WhaleFill] = []
    quote_events: list[WhaleQuoteEvent] = []
    cancelled_orders = 0
    failed_escape_chunks = 0

    def record_fill(
        tick: ReplayTick,
        side: OrderSide,
        price: float,
        quantity: int,
        fill_kind: Literal["passive", "active_escape"],
        reason: str,
        wall_price: float,
    ) -> None:
        nonlocal cash, inventory, completed_turns, previous_deviation, maximum_deviation
        notional = price * quantity
        fee = notional * parameters.one_way_fee_bps / 10_000.0
        if side == "buy":
            cash -= notional + fee
            inventory += quantity
        else:
            cash += notional - fee
            inventory -= quantity
        deviation = inventory - parameters.opening_inventory_bonds
        if previous_deviation != 0 and deviation == 0:
            completed_turns += 1
        previous_deviation = deviation
        maximum_deviation = max(maximum_deviation, abs(deviation))
        fills.append(WhaleFill(
            model_id=parameters.model_id,
            code=code,
            market_date=market_date,
            market_ts_ms=tick.market_ts_ms,
            market_time=tick.market_time,
            side=side,
            price=price,
            quantity_bonds=quantity,
            fill_kind=fill_kind,
            reason=reason,
            wall_price=wall_price,
            inventory_after_bonds=inventory,
        ))

    for tick in rows:
        if tick.trade_bonds > 0 and tick.inferred_side in {"buy", "sell"}:
            recent_trades.append((
                tick.market_ts_ms,
                tick.inferred_side,
                tick.last_price,
                tick.trade_bonds,
            ))
        cutoff = tick.market_ts_ms - parameters.risk_window_seconds * 1_000
        while recent_trades and recent_trades[0][0] < cutoff:
            recent_trades.popleft()

        in_session = _in_session(tick.market_time, parameters)
        if not in_session or not (tick.bid1 > 0 and tick.ask1 > tick.bid1):
            for order in orders.values():
                quote_events.append(WhaleQuoteEvent(
                    parameters.model_id, code, market_date, tick.market_ts_ms,
                    tick.market_time, "cancel", order.side, order.price,
                    order.remaining_bonds, order.queue_ahead_bonds,
                    order.wall_price, 0.0, "outside_session_or_invalid_book",
                ))
                cancelled_orders += 1
            orders.clear()
            walls.clear()
            continue

        _update_walls(walls, tick, parameters)

        for side in tuple(orders):
            order = orders.get(side)
            if order is None or not _passive_fillable(order, tick):
                continue
            available = float(tick.trade_bonds)
            if order.queue_ahead_bonds > 0:
                consumed = min(order.queue_ahead_bonds, available)
                order.queue_ahead_bonds -= consumed
                available -= consumed
            quantity = min(
                order.remaining_bonds,
                _round_lot(available, parameters.lot_size_bonds),
            )
            if side == "buy":
                affordable = _round_lot(
                    cash / max(order.price, parameters.tick_size),
                    parameters.lot_size_bonds,
                )
                quantity = min(
                    quantity,
                    affordable,
                    parameters.maximum_inventory_bonds - inventory,
                )
            else:
                quantity = min(quantity, inventory)
            quantity = _round_lot(quantity, parameters.lot_size_bonds)
            if quantity <= 0:
                continue
            record_fill(
                tick, side, order.price, quantity, "passive",
                "opposing_trade_consumed_visible_queue",
                order.wall_price,
            )
            _apply_exposure_fill(
                exposures,
                side,
                quantity,
                wall_price=order.wall_price,
                wall_peak_bonds=order.wall_peak_bonds,
                market_ts_ms=tick.market_ts_ms,
            )
            order.remaining_bonds -= quantity
            if order.remaining_bonds <= 0:
                orders.pop(side, None)

        # A wall is an escape option, not a guarantee.  We only book an active
        # escape while the current five-level book still displays enough size
        # through that wall for the entire exposed chunk.
        for chunk in tuple(exposures):
            if tick.market_ts_ms <= chunk.opened_ts_ms:
                continue
            wall_side: BookSide = "bid" if chunk.direction == "long" else "ask"
            current_wall = _quantity_at(
                tick, wall_side, chunk.backing_wall_price,
            )
            attack_side = "sell" if chunk.direction == "long" else "buy"
            attacking_bonds = sum(
                bonds
                for _, trade_side, trade_price, bonds in recent_trades
                if trade_side == attack_side
                and (
                    trade_price <= chunk.backing_wall_price + parameters.tick_size + 1e-9
                    if chunk.direction == "long"
                    else trade_price + 1e-9
                    >= chunk.backing_wall_price - parameters.tick_size
                )
            )
            damaged = (
                chunk.backing_wall_peak_bonds - current_wall
                >= parameters.wall_damage_bonds - 1e-9
                and attacking_bonds
                >= parameters.quote_quantity_bonds - 1e-9
            )
            if not damaged:
                continue
            if chunk.direction == "long":
                escape_price = _marketable_vwap(
                    tick.bids,
                    chunk.quantity_bonds,
                    minimum_price=chunk.backing_wall_price,
                )
                escape_side: OrderSide = "sell"
            else:
                escape_price = _marketable_vwap(
                    tick.asks,
                    chunk.quantity_bonds,
                    maximum_price=chunk.backing_wall_price,
                )
                escape_side = "buy"
            if escape_price is None:
                if not chunk.escape_attempted:
                    chunk.escape_attempted = True
                    failed_escape_chunks += 1
                continue
            record_fill(
                tick,
                escape_side,
                escape_price,
                chunk.quantity_bonds,
                "active_escape",
                "backing_whale_attacked_and_damaged",
                chunk.backing_wall_price,
            )
            exposures.remove(chunk)

        deviation = inventory - parameters.opening_inventory_bonds
        allowed_sides: set[OrderSide]
        if deviation > 0:
            allowed_sides = {"sell"}
        elif deviation < 0:
            allowed_sides = {"buy"}
        else:
            allowed_sides = {"buy", "sell"}

        for side in ("buy", "sell"):
            order = orders.get(side)
            capacity_ok = (
                inventory + parameters.quote_quantity_bonds
                <= parameters.maximum_inventory_bonds
                if side == "buy"
                else inventory >= parameters.quote_quantity_bonds
            )
            if (
                side not in allowed_sides
                or not capacity_ok
                or (order is not None and not _order_is_still_valid(
                    order, walls, tick, parameters,
                ))
            ):
                if order is not None:
                    wall = walls.get((order.wall_side, round(order.wall_price, 3)))
                    quote_events.append(WhaleQuoteEvent(
                        parameters.model_id, code, market_date,
                        tick.market_ts_ms, tick.market_time, "cancel", side,
                        order.price, order.remaining_bonds,
                        order.queue_ahead_bonds, order.wall_price,
                        wall.current_bonds if wall else 0.0,
                        "inventory_skew_or_wall_boundary_failed",
                    ))
                    orders.pop(side, None)
                    cancelled_orders += 1
                continue
            if order is not None:
                # Preserve price-time priority while the same whale and all
                # causal safety boundaries remain valid.
                continue
            wall_side: BookSide = "bid" if side == "buy" else "ask"
            selected = next((
                wall
                for wall in _eligible_walls(walls, tick, wall_side, parameters)
                if _desired_quote(tick, side, wall, parameters) is not None
            ), None)
            if selected is None:
                continue
            price = _desired_quote(tick, side, selected, parameters)
            assert price is not None
            queue_ahead = _quantity_at(tick, wall_side, price)
            orders[side] = WhaleOrder(
                side=side,
                price=price,
                quantity_bonds=parameters.quote_quantity_bonds,
                remaining_bonds=parameters.quote_quantity_bonds,
                queue_ahead_bonds=queue_ahead,
                created_ts_ms=tick.market_ts_ms,
                wall_side=wall_side,
                wall_price=selected.price,
                wall_peak_bonds=selected.peak_bonds,
            )
            quote_events.append(WhaleQuoteEvent(
                parameters.model_id, code, market_date, tick.market_ts_ms,
                tick.market_time, "place", side, price,
                parameters.quote_quantity_bonds, queue_ahead,
                selected.price, selected.current_bonds,
                "one_tick_ahead_of_stable_whale",
            ))

    final_midpoint = valid_mids[-1]
    fees = sum(
        fill.price * fill.quantity_bonds * parameters.one_way_fee_bps / 10_000.0
        for fill in fills
    )
    final_equity = cash + inventory * final_midpoint
    hold_equity = initial_cash + parameters.opening_inventory_bonds * final_midpoint
    result = WhaleDailyResult(
        model_id=parameters.model_id,
        code=code,
        market_date=market_date,
        opening_inventory_bonds=parameters.opening_inventory_bonds,
        maximum_inventory_bonds=parameters.maximum_inventory_bonds,
        quote_quantity_bonds=parameters.quote_quantity_bonds,
        minimum_wall_bonds=int(parameters.effective_minimum_wall_bonds),
        placed_orders=sum(event.action == "place" for event in quote_events),
        cancelled_orders=cancelled_orders,
        passive_fills=sum(fill.fill_kind == "passive" for fill in fills),
        active_escape_fills=sum(fill.fill_kind == "active_escape" for fill in fills),
        passive_fill_bonds=sum(
            fill.quantity_bonds for fill in fills if fill.fill_kind == "passive"
        ),
        active_escape_bonds=sum(
            fill.quantity_bonds for fill in fills if fill.fill_kind == "active_escape"
        ),
        completed_turns=completed_turns,
        ending_inventory_bonds=inventory,
        ending_inventory_deviation_bonds=(
            inventory - parameters.opening_inventory_bonds
        ),
        maximum_absolute_inventory_deviation_bonds=maximum_deviation,
        stranded_exposure_bonds=sum(chunk.quantity_bonds for chunk in exposures),
        failed_escape_chunks=failed_escape_chunks,
        turnover_cny=sum(fill.price * fill.quantity_bonds for fill in fills),
        fees_cny=fees,
        marked_pnl_cny=final_equity - hold_equity,
        first_midpoint=first_midpoint,
        final_midpoint=final_midpoint,
    )
    return result, fills, quote_events


def _v02_in_session(
    market_time: str,
    parameters: WhaleV02Parameters,
) -> bool:
    if not (
        parameters.earliest_quote_time
        <= market_time
        <= parameters.latest_quote_time
    ):
        return False
    return not ("11:30:00.001" <= market_time < "13:00:00.000")


def _v02_required_edge(
    tick: ReplayTick,
    parameters: WhaleV02Parameters,
) -> float:
    if tick.market_time < parameters.opening_caution_end_time:
        return parameters.opening_caution_edge
    return parameters.minimum_quote_edge


def _v02_quote_price(
    tick: ReplayTick,
    side: OrderSide,
    wall_price: float,
    parameters: WhaleV02Parameters,
) -> float | None:
    if side == "buy":
        price = round(wall_price + parameters.tick_size, 3)
        edge = tick.ask1 - price
        if tick.ask1 <= price:
            return None
    else:
        price = round(wall_price - parameters.tick_size, 3)
        edge = price - tick.bid1
        if price <= tick.bid1:
            return None
    if edge + 1e-9 < _v02_required_edge(tick, parameters):
        return None
    return price


def _v02_wall_gap(tick: ReplayTick, episode: WhaleV02WallEpisode) -> float:
    if episode.side == "bid":
        return tick.bid1 - episode.price
    return episode.price - tick.ask1


def _v02_attack_reaches_wall(
    tick: ReplayTick,
    episode: WhaleV02WallEpisode,
) -> bool:
    if tick.trade_bonds <= 0:
        return False
    if episode.side == "bid":
        return (
            tick.inferred_side == "sell"
            and tick.last_price <= episode.price + 1e-9
        )
    return (
        tick.inferred_side == "buy"
        and tick.last_price + 1e-9 >= episode.price
    )


def _v02_mark_unsafe(
    episode: WhaleV02WallEpisode,
    reason: str,
    *,
    ended: bool = False,
) -> None:
    if episode.safe:
        episode.safe = False
        episode.unsafe_reason = reason
    episode.ended = episode.ended or ended
    episode.pending_attack_ts_ms = None


def _v02_update_wall_episodes(
    active: dict[tuple[BookSide, float], WhaleV02WallEpisode],
    episodes: dict[int, WhaleV02WallEpisode],
    tick: ReplayTick,
    *,
    sequence: int,
    next_episode_id: int,
    adverse_reference_midpoint: float | None,
    parameters: WhaleV02Parameters,
) -> int:
    current: dict[tuple[BookSide, float], float] = {}
    for side, levels in (("bid", tick.bids), ("ask", tick.asks)):
        for price, quantity in levels:
            if price > 0:
                current[(side, round(price, 3))] = float(quantity)

    for key, episode in tuple(active.items()):
        previous_bonds = episode.current_bonds
        current_bonds = current.get(key, 0.0)
        compatible_attack = _v02_attack_reaches_wall(tick, episode)
        if current_bonds + 1e-9 < parameters.observation_wall_bonds:
            episode.current_bonds = current_bonds
            _v02_mark_unsafe(
                episode,
                "wall_left_five_levels_or_fell_below_10000",
                ended=True,
            )
            active.pop(key, None)
            continue

        if episode.pending_attack_ts_ms is not None:
            # The attack frame itself can never certify a scale-up.  Only a
            # strictly later snapshot in the same uninterrupted episode may.
            elapsed_ms = tick.market_ts_ms - episode.pending_attack_ts_ms
            maintained = (
                current_bonds + 1e-9
                >= episode.pending_attack_reference_bonds
                * parameters.wall_maintained_ratio
            )
            if elapsed_ms > 0 and maintained:
                if episode.probe_filled_ts_ms is not None:
                    episode.survived_attacks += 1
                episode.pending_attack_ts_ms = None
                episode.risk_exit_required_ts_ms = None
                episode.reentry_blocked = False
            elif elapsed_ms > parameters.wall_recovery_seconds * 1_000:
                _v02_mark_unsafe(
                    episode,
                    "attacked_wall_did_not_recover_within_30_seconds",
                )

        shrink_bonds = max(0.0, previous_bonds - current_bonds)
        explained_bonds = min(
            shrink_bonds,
            tick.trade_bonds if compatible_attack else 0.0,
        )
        shrink_ratio = (
            shrink_bonds / previous_bonds if previous_bonds > 0 else 0.0
        )
        unexplained_shrink_ratio = (
            max(0.0, shrink_bonds - explained_bonds) / previous_bonds
            if previous_bonds > 0 else 0.0
        )
        if (
            episode.safe
            and unexplained_shrink_ratio + 1e-9
            >= parameters.unexplained_shrink_ratio
        ):
            _v02_mark_unsafe(
                episode,
                "unexplained_wall_shrink_at_least_20_percent",
            )

        if (
            episode.safe
            and compatible_attack
            and episode.probe_filled_ts_ms is not None
            and tick.market_ts_ms > episode.probe_filled_ts_ms
            and (
                shrink_bonds + 1e-9
                >= parameters.significant_attack_damage_bonds
                or shrink_ratio + 1e-9
                >= parameters.unexplained_shrink_ratio
            )
        ):
            # A trade may explain the shrink and keep the episode observable,
            # but it does not make an already-filled probe safe to hold while
            # the wall is being eaten.  Exit now; recovery only re-authorizes
            # a future probe/scale decision.
            episode.risk_exit_required_ts_ms = tick.market_ts_ms
            episode.reentry_blocked = True

        if episode.safe and adverse_reference_midpoint is not None:
            current_midpoint = _midpoint(tick)
            adverse = (
                current_midpoint
                <= adverse_reference_midpoint
                - parameters.adverse_midpoint_move + 1e-9
                if episode.side == "bid"
                else current_midpoint + 1e-9
                >= adverse_reference_midpoint
                + parameters.adverse_midpoint_move
            )
            if adverse:
                _v02_mark_unsafe(episode, "adverse_30_second_midpoint_trend")

        if (
            episode.safe
            and compatible_attack
            and episode.probe_filled_ts_ms is not None
            and tick.market_ts_ms > episode.probe_filled_ts_ms
            and episode.pending_attack_ts_ms is None
        ):
            episode.pending_attack_ts_ms = tick.market_ts_ms
            episode.pending_attack_reference_bonds = previous_bonds

        episode.current_bonds = current_bonds
        episode.peak_bonds = max(episode.peak_bonds, current_bonds)
        if episode.last_sequence == sequence - 1:
            episode.adjacent_observations += 1
        else:
            episode.adjacent_observations = 1
        episode.last_sequence = sequence
        episode.last_seen_ms = tick.market_ts_ms

    for key, quantity in current.items():
        if quantity + 1e-9 < parameters.observation_wall_bonds or key in active:
            continue
        episode = WhaleV02WallEpisode(
            episode_id=next_episode_id,
            side=key[0],
            price=key[1],
            first_seen_ms=tick.market_ts_ms,
            last_seen_ms=tick.market_ts_ms,
            last_sequence=sequence,
            adjacent_observations=1,
            current_bonds=quantity,
            peak_bonds=quantity,
        )
        active[key] = episode
        episodes[episode.episode_id] = episode
        next_episode_id += 1
    return next_episode_id


def _v02_base_eligible(
    episode: WhaleV02WallEpisode,
    tick: ReplayTick,
    parameters: WhaleV02Parameters,
) -> bool:
    levels = tick.bids if episode.side == "bid" else tick.asks
    backup_exists = any(
        quantity + 1e-9 >= parameters.probe_quantity_bonds
        and (
            episode.price - parameters.maximum_backup_gap - 1e-9
            <= price < episode.price
            if episode.side == "bid"
            else episode.price < price
            <= episode.price + parameters.maximum_backup_gap + 1e-9
        )
        for price, quantity in levels
    )
    return (
        episode.safe
        and not episode.ended
        and not episode.reentry_blocked
        # A 10,000-bond wall enters observation first; it earns only the
        # 1,000-bond probe after the full time/snapshot checks below.
        and episode.current_bonds + 1e-9 >= parameters.observation_wall_bonds
        and tick.market_ts_ms - episode.first_seen_ms
        >= parameters.probe_stability_seconds * 1_000
        and episode.adjacent_observations
        >= parameters.probe_minimum_adjacent_observations
        and backup_exists
        and _v02_wall_gap(tick, episode)
        <= parameters.probe_maximum_wall_gap + 1e-9
    )


def _v02_stage_target(
    episode: WhaleV02WallEpisode,
    tick: ReplayTick,
    side: OrderSide,
    *,
    account_capacity_bonds: int,
    corridor_edge: float,
    parameters: WhaleV02Parameters,
) -> int:
    if not _v02_base_eligible(episode, tick, parameters):
        return 0
    stages = (1_000, 2_000, 3_000, 5_000)
    requested = stages[min(episode.survived_attacks, len(stages) - 1)]
    fractional_cap = int(
        episode.current_bonds * parameters.wall_risk_fraction
        // parameters.lot_size_bonds
        * parameters.lot_size_bonds
    )
    hard_cap = min(
        parameters.maximum_cumulative_risk_bonds,
        fractional_cap,
        account_capacity_bonds,
    )
    eligible_stages = [stage for stage in stages if stage <= min(requested, hard_cap)]
    target = max(eligible_stages, default=0)
    if target == 5_000:
        full_size_allowed = (
            episode.current_bonds + 1e-9 >= 25_000
            and tick.market_ts_ms - episode.first_seen_ms
            >= parameters.full_size_stability_seconds * 1_000
            and _v02_wall_gap(tick, episode)
            <= parameters.full_size_maximum_wall_gap + 1e-9
            and corridor_edge + 1e-9 >= parameters.full_size_minimum_edge
        )
        if not full_size_allowed:
            target = 3_000 if hard_cap >= 3_000 else max(
                (stage for stage in stages[:2] if stage <= hard_cap),
                default=0,
            )
    return target


def _v02_corridor(
    primary: WhaleV02WallEpisode,
    tick: ReplayTick,
    active: dict[tuple[BookSide, float], WhaleV02WallEpisode],
    parameters: WhaleV02Parameters,
) -> float | None:
    del active  # The opposite exit does not require a second whale wall.
    primary_side: OrderSide = "buy" if primary.side == "bid" else "sell"
    primary_quote = _v02_quote_price(
        tick, primary_side, primary.price, parameters,
    )
    if primary_quote is None:
        return None
    if primary.side == "bid":
        if not tick.asks:
            return None
        opposite_quote = round(tick.ask1 - parameters.tick_size, 3)
        edge = opposite_quote - primary_quote
    else:
        if not tick.bids:
            return None
        opposite_quote = round(tick.bid1 + parameters.tick_size, 3)
        edge = primary_quote - opposite_quote
    if edge + 1e-9 < _v02_required_edge(tick, parameters):
        return None
    return edge


def _v02_apply_fill_to_exposures(
    exposures: list[WhaleV02Exposure],
    side: OrderSide,
    quantity_bonds: int,
    price: float,
    *,
    wall_episode_id: int,
    wall_price: float,
    market_ts_ms: int,
    next_risk_block_id: int,
    close_only_risk_block_ids: set[int] | None = None,
    allow_open: bool = True,
) -> tuple[float, int, int, tuple[int, ...]]:
    remaining = quantity_bonds
    realized = 0.0
    affected: list[int] = []
    opposite = "short" if side == "buy" else "long"
    index = 0
    while index < len(exposures) and remaining > 0:
        exposure = exposures[index]
        if exposure.direction != opposite:
            index += 1
            continue
        if (
            close_only_risk_block_ids is not None
            and exposure.risk_block_id not in close_only_risk_block_ids
        ):
            index += 1
            continue
        closed = min(remaining, exposure.quantity_bonds)
        if exposure.risk_block_id not in affected:
            affected.append(exposure.risk_block_id)
        if side == "buy":
            realized += (exposure.entry_price - price) * closed
        else:
            realized += (price - exposure.entry_price) * closed
        remaining -= closed
        exposure.quantity_bonds -= closed
        if exposure.quantity_bonds <= 0:
            exposures.pop(index)
        else:
            index += 1
    opened = remaining if allow_open else 0
    if remaining and not allow_open:
        raise RuntimeError("targeted risk exit quantity did not match its blocks")
    if opened:
        opened_risk_block_id = next_risk_block_id
        exposures.append(WhaleV02Exposure(
            risk_block_id=opened_risk_block_id,
            direction="long" if side == "buy" else "short",
            quantity_bonds=opened,
            entry_price=price,
            wall_episode_id=wall_episode_id,
            wall_price=wall_price,
            opened_ts_ms=market_ts_ms,
        ))
        affected.append(opened_risk_block_id)
        next_risk_block_id += 1
    return realized, opened, next_risk_block_id, tuple(affected)


def run_day_v02(
    ticks: Iterable[ReplayTick],
    *,
    parameters: WhaleV02Parameters | None = None,
) -> tuple[
    WhaleV02DailyResult,
    list[WhaleV02Fill],
    list[WhaleV02QuoteEvent],
]:
    """Replay the immutable layered v0.2 candidate, paper-only and causal."""

    parameters = parameters or WhaleV02Parameters()
    rows = sorted(
        (tick for tick in ticks if tick.code.startswith("132")),
        key=lambda tick: (tick.market_ts_ms, tick.tick_id),
    )
    if not rows:
        raise ValueError("whale v0.2 replay needs at least one bond tick")
    code = rows[0].code
    market_date = rows[0].market_date
    valid_mids = [_midpoint(tick) for tick in rows if _midpoint(tick) > 0]
    if not valid_mids:
        raise ValueError("whale v0.2 replay has no valid midpoint")
    first_midpoint = valid_mids[0]
    initial_cash = parameters.additional_capacity_bonds * first_midpoint
    cash = initial_cash
    inventory = parameters.opening_inventory_bonds
    orders: dict[OrderSide, WhaleV02Order] = {}
    active_episodes: dict[tuple[BookSide, float], WhaleV02WallEpisode] = {}
    episodes: dict[int, WhaleV02WallEpisode] = {}
    exposures: list[WhaleV02Exposure] = []
    fills: list[WhaleV02Fill] = []
    quote_events: list[WhaleV02QuoteEvent] = []
    midpoint_history: deque[tuple[int, float]] = deque()
    next_episode_id = 1
    next_tranche_id = 1
    next_risk_block_id = 1
    cancelled_orders = 0
    failed_risk_exit_blocks = 0
    realized_gross_pnl = 0.0
    fees = 0.0
    completed_turns = 0
    previous_deviation = 0
    maximum_deviation = 0
    maximum_cumulative_risk = 0
    last_neutralized_ts_ms: int | None = None

    def cancel_order(tick: ReplayTick, side: OrderSide, reason: str) -> None:
        nonlocal cancelled_orders
        order = orders.pop(side, None)
        if order is None:
            return
        episode = episodes.get(order.wall_episode_id)
        quote_events.append(WhaleV02QuoteEvent(
            parameters.model_id, code, market_date, tick.market_ts_ms,
            tick.market_time, "cancel", side, order.price,
            order.remaining_bonds, order.queue_ahead_bonds,
            order.wall_episode_id, order.wall_price,
            episode.current_bonds if episode else 0.0,
            order.cumulative_target_bonds, order.certification_count, reason,
        ))
        cancelled_orders += 1

    def record_fill(
        tick: ReplayTick,
        *,
        side: OrderSide,
        price: float,
        quantity: int,
        fill_kind: Literal["passive", "active_risk_exit"],
        reason: str,
        tranche_id: int,
        risk_block_id: int,
        wall_episode_id: int,
        wall_price: float,
        cumulative_target: int,
        certifications: int,
        close_only_risk_block_ids: set[int] | None = None,
        allow_open: bool = True,
    ) -> tuple[float, int, tuple[int, ...]]:
        nonlocal cash, inventory, fees, completed_turns
        nonlocal previous_deviation, maximum_deviation, next_risk_block_id
        nonlocal maximum_cumulative_risk
        episode = episodes.get(wall_episode_id)
        wall_current = (
            _quantity_at(tick, episode.side, episode.price)
            if episode else 0.0
        )
        wall_peak = episode.peak_bonds if episode else 0.0
        notional = price * quantity
        fee = notional * parameters.one_way_fee_bps / 10_000.0
        fees += fee
        if side == "buy":
            cash -= notional + fee
            inventory += quantity
        else:
            cash += notional - fee
            inventory -= quantity
        realized, opened, next_risk_block_id, affected = (
            _v02_apply_fill_to_exposures(
            exposures,
            side,
            quantity,
            price,
            wall_episode_id=wall_episode_id,
            wall_price=wall_price,
            market_ts_ms=tick.market_ts_ms,
            next_risk_block_id=next_risk_block_id,
            close_only_risk_block_ids=close_only_risk_block_ids,
            allow_open=allow_open,
        ))
        deviation = inventory - parameters.opening_inventory_bonds
        if previous_deviation != 0 and deviation == 0:
            completed_turns += 1
        previous_deviation = deviation
        maximum_deviation = max(maximum_deviation, abs(deviation))
        instantaneous_risk = sum(
            exposure.quantity_bonds for exposure in exposures
        )
        maximum_cumulative_risk = max(
            maximum_cumulative_risk, instantaneous_risk,
        )
        fills.append(WhaleV02Fill(
            parameters.model_id, code, market_date, tick.market_ts_ms,
            tick.market_time, side, price, quantity, fill_kind, reason,
            tranche_id,
            risk_block_id or (affected[0] if affected else 0),
            affected,
            wall_episode_id, wall_price,
            wall_current, wall_peak, cumulative_target, certifications,
            realized, inventory,
        ))
        return realized, opened, affected

    def process_passive_orders(tick: ReplayTick) -> None:
        """Fill orders that existed before this frame, then inspect frame-end book."""

        nonlocal realized_gross_pnl, last_neutralized_ts_ms
        for side in tuple(orders):
            order = orders.get(side)
            if order is None:
                continue
            episode = episodes.get(order.wall_episode_id)
            if episode is None:
                continue
            fillable = (
                tick.trade_bonds > 0
                and tick.market_ts_ms > order.created_ts_ms
                and (
                    tick.inferred_side == "sell"
                    and tick.last_price <= order.price + 1e-9
                    if side == "buy"
                    else tick.inferred_side == "buy"
                    and tick.last_price + 1e-9 >= order.price
                )
            )
            if not fillable:
                continue
            attributed_public = (
                float(tick.trade_bonds)
                * parameters.passive_trade_attribution_ratio
            )
            consumed = min(order.queue_ahead_bonds, attributed_public)
            order.queue_ahead_bonds -= consumed
            attributed_available = attributed_public - consumed
            quantity = min(
                order.remaining_bonds,
                _round_lot(attributed_available, parameters.lot_size_bonds),
            )
            if side == "buy":
                affordable = _round_lot(
                    cash / max(order.price, parameters.tick_size),
                    parameters.lot_size_bonds,
                )
                quantity = min(
                    quantity,
                    affordable,
                    parameters.maximum_inventory_bonds - inventory,
                )
            else:
                quantity = min(quantity, inventory)
            quantity = _round_lot(quantity, parameters.lot_size_bonds)
            if quantity <= 0:
                continue
            deviation_before_fill = (
                inventory - parameters.opening_inventory_bonds
            )
            realized, opened, _ = record_fill(
                tick,
                side=side,
                price=order.price,
                quantity=quantity,
                fill_kind="passive",
                reason="layered_whale_queue_consumed",
                tranche_id=order.tranche_id,
                risk_block_id=0,
                wall_episode_id=order.wall_episode_id,
                wall_price=order.wall_price,
                cumulative_target=order.cumulative_target_bonds,
                certifications=order.certification_count,
            )
            realized_gross_pnl += realized
            if opened > 0:
                episode.probe_opened_bonds += opened
                if (
                    episode.probe_filled_ts_ms is None
                    and episode.probe_opened_bonds
                    >= parameters.probe_quantity_bonds
                ):
                    episode.probe_filled_ts_ms = tick.market_ts_ms
            order.remaining_bonds -= quantity
            if order.remaining_bonds <= 0:
                orders.pop(side, None)
            if (
                deviation_before_fill != 0
                and inventory == parameters.opening_inventory_bonds
            ):
                for other_side in tuple(orders):
                    cancel_order(
                        tick,
                        other_side,
                        "returned_to_neutral_rebuild_next_frame",
                    )
                last_neutralized_ts_ms = tick.market_ts_ms

    for sequence, tick in enumerate(rows, start=1):
        midpoint = _midpoint(tick)
        midpoint_history.append((tick.market_ts_ms, midpoint))
        history_cutoff = (
            tick.market_ts_ms - (parameters.adverse_window_seconds + 30) * 1_000
        )
        while midpoint_history and midpoint_history[0][0] < history_cutoff:
            midpoint_history.popleft()
        adverse_reference = next((
            value for stamp, value in reversed(midpoint_history)
            if stamp <= tick.market_ts_ms
            - parameters.adverse_window_seconds * 1_000
        ), None)

        if not _v02_in_session(tick.market_time, parameters) or not (
            tick.bid1 > 0 and tick.ask1 > tick.bid1
        ):
            for side in tuple(orders):
                cancel_order(tick, side, "outside_session_or_invalid_book")
            for episode in active_episodes.values():
                _v02_mark_unsafe(
                    episode, "session_boundary_ended_episode", ended=True,
                )
            active_episodes.clear()
            continue

        # Orders were in the book before this tick.  A trade may fill them
        # before the same frame's final snapshot reveals that the whale wall
        # collapsed; cancelling first would erase the most dangerous fills.
        process_passive_orders(tick)

        next_episode_id = _v02_update_wall_episodes(
            active_episodes,
            episodes,
            tick,
            sequence=sequence,
            next_episode_id=next_episode_id,
            adverse_reference_midpoint=adverse_reference,
            parameters=parameters,
        )

        # Risk exit is independent of the backing wall: once an episode is
        # unsafe, use only the current five-level executable depth.  Never
        # invent liquidity after a cancelled wall.
        def exposure_requires_exit(exposure: WhaleV02Exposure) -> bool:
            episode = episodes[exposure.wall_episode_id]
            return (
                not episode.safe
                or (
                    episode.risk_exit_required_ts_ms is not None
                    and exposure.opened_ts_ms
                    < episode.risk_exit_required_ts_ms
                    <= tick.market_ts_ms
                )
            )

        risk_exit_frame = any(
            exposure_requires_exit(exposure) for exposure in exposures
        )
        if risk_exit_frame:
            for side in tuple(orders):
                order = orders[side]
                order_episode = episodes.get(order.wall_episode_id)
                cancel_order(
                    tick,
                    side,
                    order_episode.unsafe_reason
                    if order_episode and not order_episode.safe
                    and order_episode.unsafe_reason
                    else "compatible_attack_significant_wall_damage"
                    if order_episode
                    and order_episode.risk_exit_required_ts_ms is not None
                    else "independent_risk_exit_preempts_quotes",
                )
        for direction, exit_side, levels in (
            ("long", "sell", tick.bids),
            ("short", "buy", tick.asks),
        ):
            unsafe_blocks = [
                exposure for exposure in exposures
                if exposure.direction == direction
                and exposure_requires_exit(exposure)
                # A block created from this frame's trade cannot also submit
                # and fill a new active escape against the frame-end book.
                and tick.market_ts_ms > exposure.opened_ts_ms
            ]
            exit_quantity = sum(block.quantity_bonds for block in unsafe_blocks)
            if exit_quantity <= 0:
                continue
            exit_price = _marketable_vwap(levels, exit_quantity)
            if exit_price is None:
                for block in unsafe_blocks:
                    if not block.escape_attempted:
                        block.escape_attempted = True
                        failed_risk_exit_blocks += 1
                continue
            # Price the combined sweep once so multiple unsafe blocks cannot
            # reuse the same displayed depth, then close each named block and
            # preserve its episode/risk-block attribution.
            for block in tuple(unsafe_blocks):
                episode = episodes[block.wall_episode_id]
                exit_reason = (
                    episode.unsafe_reason
                    if not episode.safe and episode.unsafe_reason
                    else "compatible_attack_significant_wall_damage"
                )
                realized, _, _ = record_fill(
                    tick,
                    side=exit_side,
                    price=exit_price,
                    quantity=block.quantity_bonds,
                    fill_kind="active_risk_exit",
                    reason=exit_reason,
                    tranche_id=0,
                    risk_block_id=block.risk_block_id,
                    wall_episode_id=block.wall_episode_id,
                    wall_price=block.wall_price,
                    cumulative_target=block.quantity_bonds,
                    certifications=episode.survived_attacks,
                    close_only_risk_block_ids={block.risk_block_id},
                    allow_open=False,
                )
                realized_gross_pnl += realized
                if episode.risk_exit_required_ts_ms is not None:
                    episode.probe_filled_ts_ms = None
                    episode.probe_opened_bonds = 0
                    episode.survived_attacks = 0

        if risk_exit_frame:
            continue

        if last_neutralized_ts_ms == tick.market_ts_ms:
            continue

        for side in ("buy", "sell"):
            existing = orders.get(side)
            if existing is not None:
                episode = episodes[existing.wall_episode_id]
                corridor = _v02_corridor(
                    episode, tick, active_episodes, parameters,
                )
                current_target = 0
                if corridor is not None:
                    current_target = _v02_stage_target(
                        episode,
                        tick,
                        side,
                        account_capacity_bonds=(
                            parameters.additional_capacity_bonds
                            if side == "buy"
                            else parameters.opening_inventory_bonds
                        ),
                        corridor_edge=corridor,
                        parameters=parameters,
                    )
                direction = "long" if side == "buy" else "short"
                committed = existing.remaining_bonds + sum(
                    block.quantity_bonds for block in exposures
                    if block.direction == direction
                    and block.wall_episode_id == episode.episode_id
                )
                deviation = inventory - parameters.opening_inventory_bonds
                worst_directional_risk = (
                    max(0, deviation + existing.remaining_bonds)
                    if side == "buy"
                    else max(0, -deviation + existing.remaining_bonds)
                )
                opposite_direction = "short" if side == "buy" else "long"
                opposite_exposure = sum(
                    block.quantity_bonds for block in exposures
                    if block.direction == opposite_direction
                )
                valid = (
                    episode.safe
                    and not episode.ended
                    and corridor is not None
                    and committed <= current_target
                    and worst_directional_risk
                    <= parameters.maximum_cumulative_risk_bonds
                    and (
                        not existing.exit_only
                        or existing.remaining_bonds <= opposite_exposure
                    )
                    and _v02_quote_price(
                        tick, side, episode.price, parameters,
                    ) == existing.price
                )
                if not valid:
                    cancel_order(tick, side, "wall_or_exit_corridor_invalid")
                continue

            # Before any probe has filled, expose the bond to only one whale
            # entry order.  The other side remains an observed exit corridor,
            # not a second simultaneous probe.
            if not exposures and orders:
                continue

            wall_side: BookSide = "bid" if side == "buy" else "ask"
            candidates = [
                episode for episode in active_episodes.values()
                if episode.side == wall_side
                and _v02_base_eligible(episode, tick, parameters)
            ]
            direction = "long" if side == "buy" else "short"
            owned_episode_ids = {
                block.wall_episode_id for block in exposures
                if block.direction == direction
            }
            if owned_episode_ids:
                # Scale only the episode that owns the live directional risk;
                # do not layer a fresh whale identity on top of an unresolved
                # block merely because a second wall appeared.
                candidates = [
                    episode for episode in candidates
                    if episode.episode_id in owned_episode_ids
                ]
            candidates.sort(
                key=lambda episode: episode.price,
                reverse=wall_side == "bid",
            )
            for episode in candidates:
                corridor = _v02_corridor(
                    episode, tick, active_episodes, parameters,
                )
                if corridor is None:
                    continue
                corridor_edge = corridor
                account_risk_capacity = (
                    parameters.additional_capacity_bonds
                    if side == "buy" else parameters.opening_inventory_bonds
                )
                target = _v02_stage_target(
                    episode,
                    tick,
                    side,
                    account_capacity_bonds=account_risk_capacity,
                    corridor_edge=corridor_edge,
                    parameters=parameters,
                )
                exposed = sum(
                    block.quantity_bonds for block in exposures
                    if block.direction == direction
                    and block.wall_episode_id == episode.episode_id
                )
                addition = target - exposed
                if addition <= 0:
                    continue
                if side == "buy":
                    addition = min(
                        addition,
                        parameters.maximum_inventory_bonds - inventory,
                    )
                else:
                    addition = min(addition, inventory)
                opposite_direction = "short" if side == "buy" else "long"
                opposite_exposure = sum(
                    block.quantity_bonds for block in exposures
                    if block.direction == opposite_direction
                )
                exit_only = opposite_exposure > 0
                if exit_only:
                    # An exit order may return inventory to neutral but may
                    # never cross through neutral and create the opposite bet.
                    addition = min(addition, opposite_exposure)
                deviation = inventory - parameters.opening_inventory_bonds
                global_directional_room = (
                    parameters.maximum_cumulative_risk_bonds
                    - max(0, deviation)
                    if side == "buy"
                    else parameters.maximum_cumulative_risk_bonds
                    - max(0, -deviation)
                )
                addition = min(addition, global_directional_room)
                addition = _round_lot(addition, parameters.lot_size_bonds)
                if addition <= 0:
                    continue
                price = _v02_quote_price(
                    tick, side, episode.price, parameters,
                )
                if price is None:
                    continue
                queue_ahead = _quantity_at(tick, wall_side, price)
                order = WhaleV02Order(
                    tranche_id=next_tranche_id,
                    side=side,
                    price=price,
                    quantity_bonds=addition,
                    remaining_bonds=addition,
                    queue_ahead_bonds=queue_ahead,
                    created_ts_ms=tick.market_ts_ms,
                    wall_episode_id=episode.episode_id,
                    wall_price=episode.price,
                    cumulative_target_bonds=target,
                    certification_count=episode.survived_attacks,
                    exit_only=exit_only,
                )
                next_tranche_id += 1
                orders[side] = order
                quote_events.append(WhaleV02QuoteEvent(
                    parameters.model_id, code, market_date,
                    tick.market_ts_ms, tick.market_time, "place", side,
                    price, addition, queue_ahead, episode.episode_id,
                    episode.price, episode.current_bonds, target,
                    episode.survived_attacks,
                    "layered_probe_or_certified_scale",
                ))
                break

        deviation = inventory - parameters.opening_inventory_bonds
        directional_exposure = max(
            max(
                0,
                deviation
                + (orders["buy"].remaining_bonds if "buy" in orders else 0),
            ),
            max(
                0,
                -deviation
                + (orders["sell"].remaining_bonds if "sell" in orders else 0),
            ),
        )
        maximum_cumulative_risk = max(
            maximum_cumulative_risk, directional_exposure,
        )

    final_midpoint = valid_mids[-1]
    open_mark = sum(
        (final_midpoint - exposure.entry_price) * exposure.quantity_bonds
        if exposure.direction == "long"
        else (exposure.entry_price - final_midpoint) * exposure.quantity_bonds
        for exposure in exposures
    )
    marked_from_components = realized_gross_pnl + open_mark - fees
    final_equity = cash + inventory * final_midpoint
    hold_equity = initial_cash + parameters.opening_inventory_bonds * final_midpoint
    marked_from_account = final_equity - hold_equity
    passive_bonds = sum(
        fill.quantity_bonds for fill in fills if fill.fill_kind == "passive"
    )
    attributed_passive_bonds = sum(
        fill.quantity_bonds for fill in fills
        if fill.fill_kind == "passive" and fill.wall_episode_id > 0
    )
    result = WhaleV02DailyResult(
        model_id=parameters.model_id,
        code=code,
        market_date=market_date,
        opening_inventory_bonds=parameters.opening_inventory_bonds,
        maximum_inventory_bonds=parameters.maximum_inventory_bonds,
        placed_orders=sum(event.action == "place" for event in quote_events),
        cancelled_orders=cancelled_orders,
        passive_fills=sum(fill.fill_kind == "passive" for fill in fills),
        active_risk_exit_fills=sum(
            fill.fill_kind == "active_risk_exit" for fill in fills
        ),
        passive_fill_bonds=passive_bonds,
        active_risk_exit_bonds=sum(
            fill.quantity_bonds for fill in fills
            if fill.fill_kind == "active_risk_exit"
        ),
        completed_turns=completed_turns,
        maximum_cumulative_risk_bonds=maximum_cumulative_risk,
        maximum_absolute_inventory_deviation_bonds=maximum_deviation,
        ending_inventory_bonds=inventory,
        ending_inventory_deviation_bonds=(
            inventory - parameters.opening_inventory_bonds
        ),
        stranded_exposure_bonds=sum(
            exposure.quantity_bonds for exposure in exposures
        ),
        failed_risk_exit_blocks=failed_risk_exit_blocks,
        created_risk_blocks=next_risk_block_id - 1,
        realized_closed_loop_gross_pnl_cny=realized_gross_pnl,
        open_exposure_marked_contribution_cny=open_mark,
        fees_cny=fees,
        marked_pnl_cny=marked_from_account,
        accounting_residual_cny=marked_from_account - marked_from_components,
        attributed_passive_fill_ratio=(
            parameters.passive_trade_attribution_ratio
            if passive_bonds else 1.0
        ),
        first_midpoint=first_midpoint,
        final_midpoint=final_midpoint,
    )
    return result, fills, quote_events


def load_ticks_readonly(
    database: Path,
    *,
    market_date: str,
    code: str,
) -> list[ReplayTick]:
    """Load the recorder's canonical per-timestamp bond snapshots read-only."""

    database = database.expanduser().resolve()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            WITH ranked_ticks AS (
                SELECT r.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY r.code,r.market_ts_ms
                           ORDER BY
                               CASE WHEN s.status='backfill' THEN 1 ELSE 0 END,
                               r.received_ts_ns,
                               r.id
                       ) AS replay_rank
                FROM raw_ticks r
                LEFT JOIN sessions s ON s.run_id=r.run_id
                WHERE r.market_date=? AND r.code=?
            )
            SELECT r.*,
                   COALESCE(c.volume_delta,0) AS volume_delta_value,
                   COALESCE(c.transaction_delta,0) AS transaction_delta_value,
                   COALESCE(c.inferred_side,'none') AS inferred_side_value,
                   COALESCE(c.side_confidence,'none') AS side_confidence_value
            FROM ranked_ticks r
            LEFT JOIN tick_changes c ON c.tick_id=r.id
            WHERE r.replay_rank=1
            ORDER BY r.market_ts_ms,r.received_ts_ns,r.id
            """,
            (market_date, code),
        ).fetchall()
    finally:
        connection.close()
    ticks: list[ReplayTick] = []
    for row in rows:
        bids = tuple(
            (float(row[f"bid_price_{level}"]), float(row[f"bid_volume_{level}"]) * 10.0)
            for level in range(1, 6)
            if float(row[f"bid_price_{level}"]) > 0
        )
        asks = tuple(
            (float(row[f"ask_price_{level}"]), float(row[f"ask_volume_{level}"]) * 10.0)
            for level in range(1, 6)
            if float(row[f"ask_price_{level}"]) > 0
        )
        ticks.append(ReplayTick(
            tick_id=int(row["id"]),
            code=str(row["code"]),
            market_ts_ms=int(row["market_ts_ms"]),
            market_date=str(row["market_date"]),
            market_time=str(row["market_time"]),
            last_price=float(row["last_price"]),
            bids=bids,
            asks=asks,
            trade_bonds=float(row["volume_delta_value"]) * 10.0,
            transaction_delta=int(row["transaction_delta_value"]),
            inferred_side=str(row["inferred_side_value"]),
            side_confidence=str(row["side_confidence_value"]),
            previous_close=float(row["previous_close"]),
        ))
    return ticks


def replay_study(
    database: Path,
    *,
    dates: Iterable[str],
    codes: Iterable[str] = ("132026.SH", "132024.SH"),
    parameters: WhaleResearchParameters | None = None,
) -> dict[str, object]:
    parameters = parameters or WhaleResearchParameters()
    results: list[WhaleDailyResult] = []
    fills: list[WhaleFill] = []
    missing: list[str] = []
    for market_date in dates:
        for code in codes:
            ticks = load_ticks_readonly(
                database, market_date=market_date, code=code,
            )
            if not ticks:
                missing.append(f"{market_date}/{code}")
                continue
            result, day_fills, _ = run_day(ticks, parameters=parameters)
            results.append(result)
            fills.extend(day_fills)
    pnl_values = [row.marked_pnl_cny for row in results]
    return {
        "model_id": parameters.model_id,
        "parameters": asdict(parameters),
        "missing": missing,
        "aggregate": {
            "instrument_days": len(results),
            "active_instrument_days": sum(row.passive_fills > 0 for row in results),
            "total_pnl_cny": sum(pnl_values),
            "median_pnl_cny": statistics.median(pnl_values) if pnl_values else 0.0,
            "worst_pnl_cny": min(pnl_values) if pnl_values else 0.0,
            "best_pnl_cny": max(pnl_values) if pnl_values else 0.0,
            "passive_fills": sum(row.passive_fills for row in results),
            "active_escape_fills": sum(row.active_escape_fills for row in results),
            "passive_fill_bonds": sum(row.passive_fill_bonds for row in results),
            "active_escape_bonds": sum(row.active_escape_bonds for row in results),
            "stranded_instrument_days": sum(
                row.stranded_exposure_bonds > 0 for row in results
            ),
            "stranded_exposure_bonds": sum(
                row.stranded_exposure_bonds for row in results
            ),
        },
        "daily": [asdict(row) for row in results],
        "fills": [asdict(fill) for fill in fills],
        "limitations": [
            "MiniQMT Level 1 inferred_side is not an exchange aggressor flag.",
            "A 5,000-bond counterfactual order is market-impacting; replay assumes the recorded future book remains exogenous.",
            "An active escape is booked only when current five-level displayed depth can absorb the full chunk through its backing wall.",
            "No broker order is sent and the SQLite database is opened read-only.",
        ],
    }


def replay_sensitivity(
    database: Path,
    *,
    dates: Iterable[str],
    codes: Iterable[str] = ("132026.SH", "132024.SH"),
    base_parameters: WhaleResearchParameters | None = None,
) -> list[dict[str, object]]:
    """Evaluate the explicit 2x/3x/4x wall and 15s/30s safety grid."""

    base = base_parameters or WhaleResearchParameters()
    date_values = tuple(dates)
    code_values = tuple(codes)
    output: list[dict[str, object]] = []
    for ratio in (2.0, 3.0, 4.0):
        for stability_seconds in (15, 30):
            parameters = replace(
                base,
                minimum_wall_to_quote_ratio=ratio,
                wall_stability_seconds=stability_seconds,
            )
            report = replay_study(
                database,
                dates=date_values,
                codes=code_values,
                parameters=parameters,
            )
            output.append({
                "wall_to_quote_ratio": ratio,
                "minimum_wall_bonds": int(
                    parameters.effective_minimum_wall_bonds
                ),
                "wall_stability_seconds": stability_seconds,
                **dict(report["aggregate"]),
            })
    return output


def replay_study_v02(
    database: Path,
    *,
    dates: Iterable[str],
    codes: Iterable[str] = ("132026.SH", "132024.SH"),
    parameters: WhaleV02Parameters | None = None,
) -> dict[str, object]:
    """Run the layered candidate over recorded ticks without mutating state."""

    parameters = parameters or WhaleV02Parameters()
    results: list[WhaleV02DailyResult] = []
    fills: list[WhaleV02Fill] = []
    missing: list[str] = []
    for market_date in dates:
        for code in codes:
            ticks = load_ticks_readonly(
                database, market_date=market_date, code=code,
            )
            if not ticks:
                missing.append(f"{market_date}/{code}")
                continue
            result, day_fills, _ = run_day_v02(
                ticks, parameters=parameters,
            )
            results.append(result)
            fills.extend(day_fills)
    return {
        "model_id": parameters.model_id,
        "parameters": asdict(parameters),
        "missing": missing,
        "aggregate": {
            "instrument_days": len(results),
            "active_instrument_days": sum(
                result.passive_fills > 0 for result in results
            ),
            "placed_orders": sum(result.placed_orders for result in results),
            "passive_fills": sum(result.passive_fills for result in results),
            "active_risk_exit_fills": sum(
                result.active_risk_exit_fills for result in results
            ),
            "passive_fill_bonds": sum(
                result.passive_fill_bonds for result in results
            ),
            "active_risk_exit_bonds": sum(
                result.active_risk_exit_bonds for result in results
            ),
            "realized_closed_loop_gross_pnl_cny": sum(
                result.realized_closed_loop_gross_pnl_cny
                for result in results
            ),
            "open_exposure_marked_contribution_cny": sum(
                result.open_exposure_marked_contribution_cny
                for result in results
            ),
            "fees_cny": sum(result.fees_cny for result in results),
            "marked_pnl_cny": sum(result.marked_pnl_cny for result in results),
            "stranded_instrument_days": sum(
                result.stranded_exposure_bonds > 0 for result in results
            ),
            "stranded_exposure_bonds": sum(
                result.stranded_exposure_bonds for result in results
            ),
            "created_risk_blocks": sum(
                result.created_risk_blocks for result in results
            ),
            "failed_risk_exit_blocks": sum(
                result.failed_risk_exit_blocks for result in results
            ),
            "maximum_cumulative_risk_bonds": max(
                (result.maximum_cumulative_risk_bonds for result in results),
                default=0,
            ),
            "minimum_attributed_passive_fill_ratio": min(
                (result.attributed_passive_fill_ratio for result in results),
                default=1.0,
            ),
            "maximum_abs_accounting_residual_cny": max(
                (abs(result.accounting_residual_cny) for result in results),
                default=0.0,
            ),
        },
        "daily": [asdict(result) for result in results],
        "fills": [asdict(fill) for fill in fills],
        "limitations": [
            "The v0.2 model is offline research only and is not registered in the real-time paper matrix.",
            "MiniQMT Level 1 inferred_side is an estimate rather than an exchange aggressor flag.",
            "Every wall lifecycle has a non-reusable episode_id; a wall below 10,000 bonds or outside five levels ends that episode permanently.",
            "Risk exits use only the current five displayed levels and are not booked when the full unsafe block cannot execute.",
            "No broker order is sent and SQLite is opened read-only.",
        ],
    }


def replay_attribution_sensitivity_v02(
    database: Path,
    *,
    dates: Iterable[str],
    codes: Iterable[str] = ("132026.SH", "132024.SH"),
    base_parameters: WhaleV02Parameters | None = None,
) -> list[dict[str, object]]:
    """Replay v0.2 at 100%, 50% and 25% trade attribution."""

    base = base_parameters or WhaleV02Parameters()
    date_values = tuple(dates)
    code_values = tuple(codes)
    rows: list[dict[str, object]] = []
    for ratio in (1.0, 0.5, 0.25):
        report = replay_study_v02(
            database,
            dates=date_values,
            codes=code_values,
            parameters=replace(
                base, passive_trade_attribution_ratio=ratio,
            ),
        )
        rows.append({
            "passive_trade_attribution_ratio": ratio,
            **dict(report["aggregate"]),
        })
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="巨鲸跟随做市候选只读回放")
    parser.add_argument("--database", type=Path, default=Path("data/zhaiquant.sqlite3"))
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument(
        "--codes", nargs="+", default=["132026.SH", "132024.SH"],
    )
    parser.add_argument(
        "--sensitivity", action="store_true",
        help="同时运行2x/3x/4x墙与15/30秒稳定性网格",
    )
    parser.add_argument(
        "--model-version",
        choices=("v0.1", "v0.2"),
        default="v0.1",
        help="选择不可变的离线候选版本；默认保持v0.1",
    )
    parser.add_argument(
        "--attribution-sensitivity",
        action="store_true",
        help="v0.2同时运行100%/50%/25%成交归属",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.model_version == "v0.2":
        report = replay_study_v02(
            args.database,
            dates=args.dates,
            codes=args.codes,
        )
    else:
        report = replay_study(
            args.database,
            dates=args.dates,
            codes=args.codes,
        )
    if args.sensitivity and args.model_version == "v0.1":
        report["sensitivity"] = replay_sensitivity(
            args.database,
            dates=args.dates,
            codes=args.codes,
        )
    if args.attribution_sensitivity and args.model_version == "v0.2":
        report["attribution_sensitivity"] = (
            replay_attribution_sensitivity_v02(
                args.database,
                dates=args.dates,
                codes=args.codes,
            )
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
