from __future__ import annotations

import json
import math
import sqlite3
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .types import SHANGHAI


@dataclass(frozen=True)
class MakerParameters:
    """Transparent V0.1 parameters for the liquidity-provision model."""

    bonds_per_qmt_hand: float = 10.0
    price_tick: float = 0.001
    order_quantity_bonds: float = 1_000.0
    evidence_window_seconds: int = 1_800
    evidence_half_life_seconds: int = 600
    price_cluster_width: float = 0.015
    minimum_anchor_bonds: float = 5_000.0
    minimum_anchor_confidence: float = 0.45
    minimum_entry_edge: float = 0.20
    minimum_passive_turnover_edge: float = 0.10
    maximum_active_turnover_spread: float = 0.02
    minimum_fair_value_exit_edge: float = 0.30
    minimum_active_entry_edge: float = 0.50
    legacy_queue_supported_active_edge: float = 0.25
    legacy_queue_passive_turnover_edge: float = 0.185
    minimum_base_high_sell_edge: float = 0.30
    minimum_distinct_active_improvement: float = 0.05
    large_wall_multiple: float = 5.0
    book_safety_distance: float = 0.20
    book_reference_window_seconds: int = 60
    minimum_book_reference_seconds: int = 15
    maximum_book_midpoint_range: float = 0.15
    maximum_provisional_midpoint_spread: float = 0.80
    minimum_provisional_trade_events: int = 3
    fair_price_tolerance: float = 0.015
    opportunity_cooldown_seconds: int = 60
    wall_memory_seconds: int = 90
    sweep_consumption_window_seconds: int = 60
    thin_sweep_consumption_window_seconds: int = 90
    market_temperature_window_seconds: int = 300
    confirmed_rise_grace_seconds: int = 15
    downside_risk_window_seconds: int = 30
    minimum_short_ask_drop: float = 0.10
    minimum_top_bid_gap: float = 0.10
    minimum_lower_bid_gap: float = 0.20
    maximum_intermediate_bid_multiple: float = 3.0
    maximum_near_flat_exit_loss: float = 0.015
    downside_sell_imbalance_ratio: float = 1.5
    minimum_fragile_top_bid_gap: float = 0.50
    maximum_fragile_top_bid_multiple: float = 1.0
    iron_floor_cluster_width: float = 0.075
    iron_floor_multiple: float = 50.0
    iron_floor_memory_seconds: int = 600
    maximum_iron_floor_entry_premium: float = 0.30
    windfall_recent_trade_window_seconds: int = 600
    minimum_windfall_discount: float = 1.50
    minimum_windfall_book_gap: float = 1.00
    breakout_support_seconds: int = 1_800
    breakout_weakening_sell_bonds: float = 5_000.0
    minimum_sweep_source_bonds: float = 4_000.0
    minimum_sweep_source_multiple: float = 5.0
    minimum_sweep_consumed_ratio: float = 0.80
    maximum_sweep_tail_bonds: float = 2_000.0
    minimum_sweep_jump: float = 0.15
    minimum_thin_sweep_source_multiple: float = 3.0
    minimum_thin_sweep_buy_multiple: float = 2.0
    minimum_thin_sweep_jump: float = 0.80
    maximum_low_sell_to_sweep_ratio: float = 0.25
    maximum_stock_drop_5m: float = 0.003
    outcome_horizon_seconds: int = 600
    earliest_entry_time: str = "09:30:00.000"
    latest_entry_time: str = "14:56:30.000"


@dataclass(frozen=True)
class ReplayTick:
    tick_id: int
    code: str
    market_ts_ms: int
    market_date: str
    market_time: str
    last_price: float
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]
    trade_bonds: float
    transaction_delta: int
    inferred_side: str
    side_confidence: str
    previous_close: float = 0.0

    @property
    def bid1(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def bid1_bonds(self) -> float:
        return self.bids[0][1] if self.bids else 0.0

    @property
    def ask1(self) -> float:
        return self.asks[0][0] if self.asks else 0.0

    @property
    def ask1_bonds(self) -> float:
        return self.asks[0][1] if self.asks else 0.0


@dataclass(frozen=True)
class TradeEvidence:
    market_ts_ms: int
    price: float
    bonds: float
    transactions: int
    side: str


@dataclass(frozen=True)
class BookQuote:
    market_ts_ms: int
    bid: float
    ask: float

    @property
    def midpoint(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass
class PriceCluster:
    price: float
    effective_bonds: float
    raw_bonds: float
    events: int
    transactions: int
    maximum_event_bonds: float
    first_ts_ms: int
    last_ts_ms: int

    def public(self) -> dict[str, Any]:
        return {
            "price": _round_price(self.price),
            "effective_bonds": round(self.effective_bonds, 1),
            "raw_bonds": round(self.raw_bonds, 1),
            "events": self.events,
            "transactions": self.transactions,
            "maximum_event_bonds": round(self.maximum_event_bonds, 1),
            "first_time": _clock(self.first_ts_ms),
            "last_time": _clock(self.last_ts_ms),
        }


@dataclass(frozen=True)
class AnchorState:
    support_price: float
    exit_price: float
    band_midpoint: float
    reference_price: float
    confidence: float
    buy_effective_bonds: float
    sell_effective_bonds: float
    downside_pressure: float
    stock_return_5m: float | None
    stock_factor: float
    buy_clusters: tuple[PriceCluster, ...]
    sell_reference_price: float | None

    def public(self) -> dict[str, Any]:
        return {
            "support_price": _round_price(self.support_price),
            "exit_price": _round_price(self.exit_price),
            "band_midpoint": _round_price(self.band_midpoint),
            "reference_price": _round_price(self.reference_price),
            "confidence": round(self.confidence, 4),
            "buy_effective_bonds": round(self.buy_effective_bonds, 1),
            "sell_effective_bonds": round(self.sell_effective_bonds, 1),
            "downside_pressure": round(self.downside_pressure, 4),
            "sell_reference_price": (
                _round_price(self.sell_reference_price)
                if self.sell_reference_price is not None else None
            ),
            "stock_return_5m": (
                round(self.stock_return_5m, 6)
                if self.stock_return_5m is not None else None
            ),
            "stock_factor": round(self.stock_factor, 4),
            "buy_clusters": [cluster.public() for cluster in self.buy_clusters],
        }


@dataclass(frozen=True)
class MarketAssessment:
    reference_price: float
    reference_low: float
    reference_high: float
    reference_source: str
    reference_confidence: float
    state: str
    state_score: int
    state_confidence: float
    recent_buy_bonds: float
    recent_sell_bonds: float
    midpoint_change: float
    short_ask_change: float
    largest_ask_gap: float
    downside_book_vacuum: bool
    fragile_top_bid: bool
    iron_floor_price: float | None
    iron_floor_bonds: float
    evidence: tuple[str, ...]

    def public(self) -> dict[str, Any]:
        return {
            "reference_price": _round_price(self.reference_price),
            "reference_low": _round_price(self.reference_low),
            "reference_high": _round_price(self.reference_high),
            "reference_source": self.reference_source,
            "reference_confidence": round(self.reference_confidence, 4),
            "state": self.state,
            "state_score": self.state_score,
            "state_confidence": round(self.state_confidence, 4),
            "recent_buy_bonds": round(self.recent_buy_bonds, 1),
            "recent_sell_bonds": round(self.recent_sell_bonds, 1),
            "midpoint_change": round(self.midpoint_change, 3),
            "short_ask_change": round(self.short_ask_change, 3),
            "largest_ask_gap": round(self.largest_ask_gap, 3),
            "downside_book_vacuum": self.downside_book_vacuum,
            "fragile_top_bid": self.fragile_top_bid,
            "iron_floor_price": (
                _round_price(self.iron_floor_price)
                if self.iron_floor_price is not None else None
            ),
            "iron_floor_bonds": round(self.iron_floor_bonds, 1),
            "evidence": list(self.evidence),
        }


@dataclass
class Opportunity:
    kind: str
    signal_ts_ms: int
    market_time: str
    entry_price: float
    quantity_bonds: float
    target_exit_price: float
    priority_exit_price: float
    theoretical_edge: float
    anchor: AnchorState
    queue_ahead_bonds: float = 0.0
    improved_entry_price: float | None = None
    source_wall_bonds: float | None = None
    consumed_bonds: float | None = None
    consumed_ratio: float | None = None
    consumption_seconds: float | None = None
    tail_bonds: float | None = None
    next_ask_price: float | None = None
    notes: tuple[str, ...] = ()
    outcome: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        result = {
            "kind": self.kind,
            "signal_time": self.market_time,
            "signal_ts_ms": self.signal_ts_ms,
            "entry_price": _round_price(self.entry_price),
            "improved_entry_price": (
                _round_price(self.improved_entry_price)
                if self.improved_entry_price is not None else None
            ),
            "quantity_bonds": round(self.quantity_bonds, 1),
            "queue_ahead_bonds": round(self.queue_ahead_bonds, 1),
            "target_exit_price": _round_price(self.target_exit_price),
            "priority_exit_price": _round_price(self.priority_exit_price),
            "theoretical_edge": round(self.theoretical_edge, 3),
            "theoretical_gross_cny": round(
                self.theoretical_edge * self.quantity_bonds, 2
            ),
            "anchor": self.anchor.public(),
            "notes": list(self.notes),
            "outcome": self.outcome,
        }
        if self.kind == "sweep_tail":
            result["sweep"] = {
                "source_wall_bonds": round(self.source_wall_bonds or 0.0, 1),
                "consumed_bonds": round(self.consumed_bonds or 0.0, 1),
                "consumed_ratio": round(self.consumed_ratio or 0.0, 4),
                "consumption_seconds": round(self.consumption_seconds or 0.0, 1),
                "tail_bonds": round(self.tail_bonds or 0.0, 1),
                "next_ask_price": _round_price(self.next_ask_price or 0.0),
            }
        return result


@dataclass
class AskWall:
    price: float
    first_ts_ms: int
    last_seen_ts_ms: int
    peak_bonds: float
    current_bonds: float
    cluster_peak_bonds: float = 0.0
    traded_bonds: float = 0.0
    aggressive_buy_trades: deque[tuple[int, float]] = field(default_factory=deque)
    emitted: bool = False


class MakerAnalyzer:
    """
    Stateful, causal V0.1 analyzer.

    Only information at or before the current snapshot is used to construct an
    anchor or signal. Future ticks are consulted later, only by outcome scoring.
    """

    def __init__(
        self, bond_code: str, stock_code: str,
        parameters: MakerParameters | None = None,
    ) -> None:
        self.bond_code = bond_code
        self.stock_code = stock_code
        self.parameters = parameters or MakerParameters()
        self.trade_evidence: deque[TradeEvidence] = deque()
        self.book_quotes: deque[BookQuote] = deque()
        self.stock_prices: deque[tuple[int, float]] = deque()
        self.ask_walls: dict[float, AskWall] = {}
        self.opportunities: list[Opportunity] = []
        self.anchor_observations = 0
        self.last_anchor: AnchorState | None = None
        self.last_low_signal_by_bucket: dict[float, int] = {}
        self.breakout_support_price = 0.0
        self.breakout_support_ts_ms = 0
        self.iron_floor_price = 0.0
        self.iron_floor_bonds = 0.0
        self.iron_floor_ts_ms = 0

    def on_tick(self, tick: ReplayTick) -> list[Opportunity]:
        if tick.code == self.stock_code:
            self._on_stock(tick)
            return []
        if tick.code != self.bond_code:
            return []

        self._expire(tick.market_ts_ms)
        if tick.bid1 > 0 and tick.ask1 > tick.bid1:
            self.book_quotes.append(BookQuote(
                tick.market_ts_ms, tick.bid1, tick.ask1,
            ))
        if tick.trade_bonds > 0 and tick.inferred_side in {"buy", "sell"}:
            self.trade_evidence.append(TradeEvidence(
                tick.market_ts_ms, tick.last_price, tick.trade_bonds,
                max(1, tick.transaction_delta), tick.inferred_side,
            ))

        sweep_opportunities = self._update_ask_walls(tick)
        anchor = self._anchor(tick.market_ts_ms)
        self.last_anchor = anchor
        if anchor is not None:
            self.anchor_observations += 1

        emitted = list(sweep_opportunities)
        if anchor is not None:
            low_bid = self._low_bid_opportunity(tick, anchor)
            if low_bid is not None:
                emitted.append(low_bid)
        self.opportunities.extend(emitted)
        return emitted

    def _on_stock(self, tick: ReplayTick) -> None:
        if tick.last_price <= 0:
            return
        self.stock_prices.append((tick.market_ts_ms, tick.last_price))
        cutoff = tick.market_ts_ms - self.parameters.evidence_window_seconds * 1000
        while self.stock_prices and self.stock_prices[0][0] < cutoff:
            self.stock_prices.popleft()

    def _expire(self, now_ms: int) -> None:
        cutoff = now_ms - self.parameters.evidence_window_seconds * 1000
        while self.trade_evidence and self.trade_evidence[0].market_ts_ms < cutoff:
            self.trade_evidence.popleft()
        book_cutoff = now_ms - max(
            self.parameters.book_reference_window_seconds,
            self.parameters.market_temperature_window_seconds,
        ) * 1000
        while self.book_quotes and self.book_quotes[0].market_ts_ms < book_cutoff:
            self.book_quotes.popleft()
        wall_cutoff = now_ms - self.parameters.wall_memory_seconds * 1000
        self.ask_walls = {
            price: wall for price, wall in self.ask_walls.items()
            if wall.last_seen_ts_ms >= wall_cutoff
        }

    def _update_ask_walls(self, tick: ReplayTick) -> list[Opportunity]:
        parameters = self.parameters
        visible = {round(price, 6): bonds for price, bonds in tick.asks if price > 0}
        trade_price = round(tick.last_price, 6)
        if tick.trade_bonds > 0 and tick.inferred_side == "buy":
            wall = self.ask_walls.get(trade_price)
            if wall is not None:
                wall.traded_bonds += tick.trade_bonds
                wall.aggressive_buy_trades.append(
                    (tick.market_ts_ms, tick.trade_bonds)
                )

        for price, bonds in visible.items():
            wall = self.ask_walls.get(price)
            if wall is None:
                self.ask_walls[price] = AskWall(
                    price=price,
                    first_ts_ms=tick.market_ts_ms,
                    last_seen_ts_ms=tick.market_ts_ms,
                    peak_bonds=bonds,
                    current_bonds=bonds,
                )
                continue
            wall.last_seen_ts_ms = tick.market_ts_ms
            wall.current_bonds = bonds
            # The source wall must have been visibly present in Level 1.  Do
            # not manufacture a larger historical wall by repeatedly adding
            # cumulative trades to the remaining displayed quantity.
            wall.peak_bonds = max(wall.peak_bonds, bonds)

        # A sell wall may be split across adjacent legal ticks.  Preserve the
        # largest cluster that was simultaneously visible; summing each
        # level's independent historical peak would manufacture liquidity
        # that may never have existed at the same moment.
        for price, wall in self.ask_walls.items():
            cluster_bonds = sum(
                bonds for visible_price, bonds in visible.items()
                if abs(visible_price - price)
                    <= parameters.price_cluster_width + 1e-9
            )
            if cluster_bonds > 0:
                wall.cluster_peak_bonds = max(
                    wall.cluster_peak_bonds, cluster_bonds,
                )

        emitted: list[Opportunity] = []
        for price, wall in self.ask_walls.items():
            if wall.emitted or price not in visible:
                continue
            cluster_members = [
                member for member_price, member in self.ask_walls.items()
                if abs(member_price - price)
                    <= parameters.price_cluster_width + 1e-9
            ]
            source_cluster_bonds = max(
                (
                    member.cluster_peak_bonds or member.peak_bonds
                    for member in cluster_members
                ),
                default=wall.peak_bonds,
            )
            current_cluster_bonds = sum(
                bonds for visible_price, bonds in visible.items()
                if abs(visible_price - price)
                    <= parameters.price_cluster_width + 1e-9
            )
            rapid_cutoff = (
                tick.market_ts_ms
                - parameters.sweep_consumption_window_seconds * 1000
            )
            thin_cutoff = (
                tick.market_ts_ms
                - parameters.thin_sweep_consumption_window_seconds * 1000
            )
            for member in cluster_members:
                while (
                    member.aggressive_buy_trades
                    and member.aggressive_buy_trades[0][0] < thin_cutoff
                ):
                    member.aggressive_buy_trades.popleft()
            rapid_traded = sum(
                quantity
                for member in cluster_members
                for traded_at, quantity in member.aggressive_buy_trades
                if traded_at >= rapid_cutoff
            )
            thin_window_traded = sum(
                quantity
                for member in cluster_members
                for _, quantity in member.aggressive_buy_trades
            )
            verified_consumed = min(
                rapid_traded,
                max(0.0, source_cluster_bonds - current_cluster_bonds),
            )
            consumed_ratio = (
                verified_consumed / source_cluster_bonds
                if source_cluster_bonds > 0 else 0.0
            )
            higher_asks = sorted(
                value for value in visible
                if value > price + parameters.price_cluster_width + 1e-9
            )
            next_ask = higher_asks[0] if higher_asks else 0.0
            jump = next_ask - price if next_ask > 0 else 0.0
            planned_quantity = min(
                parameters.order_quantity_bonds, visible[price]
            )
            minimum_source_wall = max(
                parameters.minimum_sweep_source_bonds,
                parameters.minimum_sweep_source_multiple * planned_quantity,
            )
            thin_cluster_exhaustion = (
                source_cluster_bonds + 1e-9
                    >= parameters.minimum_thin_sweep_source_multiple
                        * planned_quantity
                and thin_window_traded + 1e-9
                    >= parameters.minimum_thin_sweep_buy_multiple
                        * planned_quantity
                and 0 < current_cluster_bonds
                    <= planned_quantity + 1e-9
                and jump + 1e-9 >= parameters.minimum_thin_sweep_jump
            )
            consumption_seconds = (
                (
                    tick.market_ts_ms
                    - min(
                        traded_at
                        for member in cluster_members
                        for traded_at, _ in member.aggressive_buy_trades
                    )
                ) / 1000
                if any(
                    member.aggressive_buy_trades
                    for member in cluster_members
                ) else 0.0
            )
            if not self._entry_window(tick.market_time) or not (
                (
                    source_cluster_bonds >= minimum_source_wall
                    and consumed_ratio >= parameters.minimum_sweep_consumed_ratio
                    and 0 < current_cluster_bonds
                        <= parameters.maximum_sweep_tail_bonds
                    and jump + 1e-9 >= parameters.minimum_sweep_jump
                )
                or thin_cluster_exhaustion
            ):
                continue

            anchor = self._anchor(tick.market_ts_ms)
            if anchor is None and thin_cluster_exhaustion:
                stock_return = self._stock_return(tick.market_ts_ms, 300)
                anchor = AnchorState(
                    support_price=price,
                    exit_price=next_ask,
                    band_midpoint=(price + next_ask) / 2,
                    reference_price=price,
                    confidence=parameters.minimum_anchor_confidence,
                    buy_effective_bonds=thin_window_traded,
                    sell_effective_bonds=0.0,
                    downside_pressure=0.0,
                    stock_return_5m=stock_return,
                    stock_factor=self._stock_factor(stock_return),
                    buy_clusters=(),
                    sell_reference_price=None,
                )
            if anchor is None:
                # The verified aggressive buying that consumed the wall should
                # normally establish an anchor. Keep this guard explicit.
                continue
            if not self._sweep_temperature_supportive(
                price,
                thin_window_traded if thin_cluster_exhaustion else rapid_traded,
                tick.market_ts_ms,
            ):
                continue
            priority_exit = max(price + parameters.price_tick, next_ask - parameters.price_tick)
            quantity = planned_quantity
            opportunity = Opportunity(
                kind="sweep_tail",
                signal_ts_ms=tick.market_ts_ms,
                market_time=tick.market_time,
                entry_price=price,
                quantity_bonds=quantity,
                target_exit_price=next_ask,
                priority_exit_price=priority_exit,
                theoretical_edge=priority_exit - price,
                anchor=anchor,
                source_wall_bonds=source_cluster_bonds,
                consumed_bonds=verified_consumed,
                consumed_ratio=consumed_ratio,
                consumption_seconds=consumption_seconds,
                tail_bonds=current_cluster_bonds,
                next_ask_price=next_ask,
                notes=(
                    (
                        "adjacent_offer_cluster_exhaustion_with_large_jump"
                        if thin_cluster_exhaustion
                        else "verified_large_wall_tail_consumption"
                    ),
                    "active_tail_sweep_uses_current_level1_snapshot",
                    "three_second_snapshots_may_overstate_execution_window",
                ),
            )
            active_support = self.active_breakout_support(tick.market_ts_ms)
            if active_support is None:
                self.breakout_support_price = price
            else:
                self.breakout_support_price = max(active_support, price)
            self.breakout_support_ts_ms = tick.market_ts_ms
            for member in cluster_members:
                member.emitted = True
            emitted.append(opportunity)
        return emitted

    def active_breakout_support(self, now_ms: int) -> float | None:
        if self.breakout_support_price <= 0 or self.breakout_support_ts_ms <= 0:
            return None
        if (
            now_ms - self.breakout_support_ts_ms
            > self.parameters.breakout_support_seconds * 1000
        ):
            return None
        return self.breakout_support_price

    def persistent_book_reference(self, now_ms: int) -> float | None:
        """Return a causal early-session reference from a stable inside market.

        Yesterday's close remains the opening fallback, but it must not dominate
        after actual trading has begun and the displayed bid/ask midpoint has
        occupied a narrow range for long enough.  The consecutive stable run is
        taken from the newest quote backwards so an earlier regime cannot dilute
        a newly established local market.
        """
        parameters = self.parameters
        trade_cutoff = (
            now_ms - parameters.book_reference_window_seconds * 1000
        )
        if not any(
            trade_cutoff <= event.market_ts_ms < now_ms
            for event in self.trade_evidence
        ):
            return None
        candidates = [
            quote for quote in self.book_quotes
            if trade_cutoff <= quote.market_ts_ms < now_ms
        ]
        if len(candidates) < 2:
            return None
        selected = [candidates[-1]]
        minimum = maximum = candidates[-1].midpoint
        for quote in reversed(candidates[:-1]):
            midpoint = quote.midpoint
            next_minimum = min(minimum, midpoint)
            next_maximum = max(maximum, midpoint)
            if (
                next_maximum - next_minimum
                > parameters.maximum_book_midpoint_range + 1e-9
            ):
                break
            selected.append(quote)
            minimum, maximum = next_minimum, next_maximum
        duration_ms = (
            selected[0].market_ts_ms - selected[-1].market_ts_ms
        )
        if duration_ms < parameters.minimum_book_reference_seconds * 1000:
            return None
        midpoints = sorted(quote.midpoint for quote in selected)
        middle = len(midpoints) // 2
        if len(midpoints) % 2:
            return midpoints[middle]
        return (midpoints[middle - 1] + midpoints[middle]) / 2

    def recent_trade_reference(
        self, now_ms: int, window_seconds: int | None = None,
    ) -> float | None:
        """Return a robust quantity-weighted median of recent actual trades."""
        window = (
            window_seconds
            if window_seconds is not None
            else self.parameters.windfall_recent_trade_window_seconds
        )
        cutoff = now_ms - window * 1000
        events = [
            event for event in self.trade_evidence
            if event.market_ts_ms >= cutoff and event.bonds > 0
        ]
        if not events:
            return None
        total = sum(event.bonds for event in events)
        threshold = total / 2
        cumulative = 0.0
        for event in sorted(events, key=lambda item: item.price):
            cumulative += event.bonds
            if cumulative + 1e-9 >= threshold:
                return event.price
        return events[-1].price

    def provisional_midpoint_ready(self) -> bool:
        return (
            len(self.trade_evidence)
            >= self.parameters.minimum_provisional_trade_events
        )

    def assess_market(
        self, tick: ReplayTick, previous_close: float,
    ) -> MarketAssessment:
        """Explain the current fair-price hypothesis and directional state.

        This is a transparent diagnostic layer for human correction.  It does
        not by itself authorize larger orders or bypass the paper-only engine.
        """
        parameters = self.parameters
        now_ms = tick.market_ts_ms
        anchor = self.last_anchor
        reliable_anchor = (
            anchor is not None
            and anchor.confidence >= parameters.minimum_anchor_confidence
        )
        persistent = self.persistent_book_reference(now_ms)
        if reliable_anchor and anchor is not None:
            reference = anchor.reference_price
            lower = min(anchor.support_price, reference)
            upper = max(anchor.exit_price, reference)
            source = "intraday_trade_anchor"
            reference_confidence = anchor.confidence
        elif persistent is not None:
            reference = persistent
            lower = min(tick.bid1, reference)
            upper = max(tick.ask1, reference)
            source = "persistent_inside_market"
            reference_confidence = 0.55
        elif (
            self.provisional_midpoint_ready()
            and tick.bid1 > 0
            and tick.ask1 > tick.bid1
            and tick.ask1 - tick.bid1
                <= parameters.maximum_provisional_midpoint_spread + 1e-9
        ):
            reference = (tick.bid1 + tick.ask1) / 2
            lower = tick.bid1
            upper = tick.ask1
            source = "current_midpoint"
            reference_confidence = 0.35
        else:
            reference = (
                previous_close if previous_close > 0
                else (tick.bid1 + tick.ask1) / 2
            )
            tolerance = parameters.fair_price_tolerance
            lower = max(parameters.price_tick, reference - tolerance)
            upper = reference + tolerance
            source = "previous_close" if previous_close > 0 else "current_midpoint"
            reference_confidence = 0.25 if previous_close > 0 else 0.20

        breakout = self.active_breakout_support(now_ms)
        lower_sells = self.breakout_lower_sell_bonds(now_ms)
        breakout_strong = (
            breakout is not None
            and lower_sells + 1e-9
                < parameters.breakout_weakening_sell_bonds
        )
        if breakout_strong and breakout is not None and breakout > reference:
            reference = breakout
            lower = min(lower, breakout)
            upper = max(upper, breakout, tick.ask1)
            source = "large_buy_breakout_support"
            reference_confidence = max(reference_confidence, 0.75)

        temperature_cutoff = (
            now_ms - parameters.market_temperature_window_seconds * 1000
        )
        recent_trades = [
            event for event in self.trade_evidence
            if event.market_ts_ms >= temperature_cutoff
        ]
        buy_bonds = sum(
            event.bonds for event in recent_trades if event.side == "buy"
        )
        sell_bonds = sum(
            event.bonds for event in recent_trades if event.side == "sell"
        )

        minute_cutoff = now_ms - 60_000
        prior_quotes = [
            quote for quote in self.book_quotes
            if quote.market_ts_ms <= minute_cutoff
        ]
        baseline = prior_quotes[-1] if prior_quotes else (
            self.book_quotes[0] if self.book_quotes else None
        )
        current_midpoint = (tick.bid1 + tick.ask1) / 2
        midpoint_change = (
            current_midpoint - baseline.midpoint if baseline is not None else 0.0
        )
        ask_change = tick.ask1 - baseline.ask if baseline is not None else 0.0
        bid_change = tick.bid1 - baseline.bid if baseline is not None else 0.0
        ask_prices = [price for price, _ in tick.asks[:3] if price > 0]
        ask_gaps = [
            right - left for left, right in zip(ask_prices, ask_prices[1:])
        ]
        largest_ask_gap = max(ask_gaps, default=0.0)
        short_cutoff = (
            now_ms - parameters.downside_risk_window_seconds * 1000
        )
        short_quotes = [
            quote for quote in self.book_quotes
            if quote.market_ts_ms >= short_cutoff
        ]
        short_ask_change = (
            tick.ask1 - max(quote.ask for quote in short_quotes)
            if short_quotes else 0.0
        )
        downside_book_vacuum = self.downside_book_vacuum(tick)
        fragile_top_bid = self.fragile_top_bid(tick)
        iron_floor_price, iron_floor_bonds = self.strong_bid_floor(tick)
        iron_floor_from_memory = False
        if iron_floor_price is not None:
            self.iron_floor_price = iron_floor_price
            self.iron_floor_bonds = iron_floor_bonds
            self.iron_floor_ts_ms = now_ms
        elif (
            self.iron_floor_price > 0
            and now_ms - self.iron_floor_ts_ms
                <= parameters.iron_floor_memory_seconds * 1000
        ):
            iron_floor_price = self.iron_floor_price
            iron_floor_bonds = self.iron_floor_bonds
            iron_floor_from_memory = True

        score = 0
        evidence: list[str] = []
        if breakout_strong and breakout is not None:
            score += 3
            evidence.append(
                f"卖墙被真实买盘吃穿，{breakout:.3f}附近形成突破支撑"
            )
        elif breakout is not None and not breakout_strong:
            score -= 1
            evidence.append(
                f"突破后低价卖出累计{lower_sells:,.0f}张，原支撑正在减弱"
            )

        imbalance_floor = parameters.order_quantity_bonds * 2
        if (
            buy_bonds + 1e-9 >= imbalance_floor
            and buy_bonds >= sell_bonds * 1.5 + 1e-9
        ):
            score += 1
            evidence.append(
                f"近5分钟主动买入约{buy_bonds:,.0f}张，明显强于卖出"
            )
        elif (
            sell_bonds + 1e-9 >= imbalance_floor
            and sell_bonds >= buy_bonds * 1.5 + 1e-9
        ):
            score -= 1
            evidence.append(
                f"近5分钟主动卖出约{sell_bonds:,.0f}张，明显强于买入"
            )

        movement_threshold = 0.10
        if midpoint_change >= movement_threshold:
            score += 1
            evidence.append(
                f"近1分钟买卖中点上移{midpoint_change:.3f}元"
            )
        elif midpoint_change <= -movement_threshold:
            score -= 1
            evidence.append(
                f"近1分钟买卖中点下移{abs(midpoint_change):.3f}元"
            )

        if ask_change <= -parameters.minimum_sweep_jump:
            score -= 1
            evidence.append(
                f"卖一近1分钟主动下压{abs(ask_change):.3f}元"
            )
        if bid_change >= parameters.minimum_sweep_jump:
            score += 1
            evidence.append(f"买一近1分钟抬升{bid_change:.3f}元")
        elif bid_change <= -parameters.minimum_sweep_jump:
            score -= 1
            evidence.append(f"买一近1分钟后退{abs(bid_change):.3f}元")

        if short_ask_change <= -parameters.minimum_short_ask_drop:
            score -= 1
            evidence.append(
                f"近{parameters.downside_risk_window_seconds}秒卖一从阶段高位下压"
                f"{abs(short_ask_change):.3f}元"
            )

        if downside_book_vacuum:
            if score < 0:
                score -= 1
            evidence.append(
                "买一以下存在两段明显断档，中间承托量偏薄，"
                "关键买单撤走后下方空间较大"
            )

        if fragile_top_bid:
            if score <= 0:
                score -= 1
            evidence.append(
                "买一显示量很小且与买二大幅断档，"
                "当前退出窗口可能随时消失"
            )

        if iron_floor_price is not None:
            evidence.append(
                (
                    f"近期曾观察到{iron_floor_price:.3f}附近买盘约"
                    if iron_floor_from_memory
                    else f"{iron_floor_price:.3f}附近可见买盘约"
                )
                + f"{iron_floor_bonds:,.0f}张，形成强承托区"
            )

        if largest_ask_gap >= parameters.minimum_sweep_jump:
            evidence.append(
                f"前三档卖盘最大断档{largest_ask_gap:.3f}元，上方供给稀疏"
            )
            if score > 0:
                score += 1

        if score >= 3:
            state = "rising"
        elif score >= 1:
            state = "possible_rise"
        elif score <= -3:
            state = "falling"
        elif score <= -1:
            state = "possible_fall"
        else:
            state = "stable"
            if persistent is not None:
                evidence.append("买卖区间持续稳定，暂按平稳状态做市")
            elif not evidence:
                evidence.append("方向证据不足，暂不假设趋势")

        state_confidence = min(
            0.95,
            0.30 + 0.12 * len(evidence) + 0.08 * abs(score),
        )
        return MarketAssessment(
            reference_price=reference,
            reference_low=lower,
            reference_high=upper,
            reference_source=source,
            reference_confidence=reference_confidence,
            state=state,
            state_score=score,
            state_confidence=state_confidence,
            recent_buy_bonds=buy_bonds,
            recent_sell_bonds=sell_bonds,
            midpoint_change=midpoint_change,
            short_ask_change=short_ask_change,
            largest_ask_gap=largest_ask_gap,
            downside_book_vacuum=downside_book_vacuum,
            fragile_top_bid=fragile_top_bid,
            iron_floor_price=iron_floor_price,
            iron_floor_bonds=iron_floor_bonds,
            evidence=tuple(evidence),
        )

    def downside_book_vacuum(self, tick: ReplayTick) -> bool:
        """Detect a thin two-step bid ladder below the current best bid."""
        if len(tick.bids) < 4:
            return False
        parameters = self.parameters
        bid1, bid2, bid3, bid4 = tick.bids[:4]
        intermediate_bonds = bid2[1] + bid3[1]
        maximum_intermediate = (
            parameters.maximum_intermediate_bid_multiple
            * parameters.order_quantity_bonds
        )
        return (
            bid1[0] - bid2[0] + 1e-9
                >= parameters.minimum_top_bid_gap
            and intermediate_bonds <= maximum_intermediate + 1e-9
            and bid3[0] - bid4[0] + 1e-9
                >= parameters.minimum_lower_bid_gap
        )

    def fragile_top_bid(self, tick: ReplayTick) -> bool:
        """A small best bid is the last exit before a large downward jump."""
        if len(tick.bids) < 2:
            return False
        parameters = self.parameters
        return (
            tick.bids[0][0] - tick.bids[1][0] + 1e-9
                >= parameters.minimum_fragile_top_bid_gap
            and tick.bids[0][1] <= (
                parameters.maximum_fragile_top_bid_multiple
                * parameters.order_quantity_bonds
            ) + 1e-9
        )

    def strong_bid_floor(
        self, tick: ReplayTick,
    ) -> tuple[float | None, float]:
        """Find the strongest tight bid cluster visible in the five levels."""
        if not tick.bids:
            return None, 0.0
        parameters = self.parameters
        best_price = None
        best_bonds = 0.0
        for index, (price, _) in enumerate(tick.bids):
            bonds = sum(
                quantity for lower_price, quantity in tick.bids[index:]
                if price - lower_price
                    <= parameters.iron_floor_cluster_width + 1e-9
            )
            if bonds > best_bonds:
                best_price, best_bonds = price, bonds
        minimum_bonds = (
            parameters.iron_floor_multiple
            * parameters.order_quantity_bonds
        )
        if best_bonds + 1e-9 < minimum_bonds:
            return None, 0.0
        return best_price, best_bonds

    def breakout_lower_sell_bonds(self, now_ms: int) -> float:
        support = self.active_breakout_support(now_ms)
        if support is None:
            return 0.0
        threshold = (
            support - self.parameters.minimum_sweep_jump
            + self.parameters.price_tick
        )
        return sum(
            event.bonds for event in self.trade_evidence
            if event.side == "sell"
            and event.market_ts_ms > self.breakout_support_ts_ms
            and event.price <= threshold + 1e-9
        )

    def _sweep_temperature_supportive(
        self, price: float, rapid_buy_bonds: float, now_ms: int,
    ) -> bool:
        """Reject a tail chase when recent lower-price selling is material."""
        cutoff = (
            now_ms - self.parameters.market_temperature_window_seconds * 1000
        )
        lower_sell_bonds = sum(
            event.bonds for event in self.trade_evidence
            if event.side == "sell"
            and event.market_ts_ms >= cutoff
            and event.price <= price - self.parameters.minimum_sweep_jump + 1e-9
        )
        return not (
            lower_sell_bonds + 1e-9
                >= self.parameters.order_quantity_bonds
            and lower_sell_bonds + 1e-9
                >= rapid_buy_bonds
                    * self.parameters.maximum_low_sell_to_sweep_ratio
        )

    def _anchor(self, now_ms: int) -> AnchorState | None:
        buys = [event for event in self.trade_evidence if event.side == "buy"]
        if not buys:
            return None
        buy_clusters = self._clusters(buys, now_ms)
        qualified = [
            cluster for cluster in buy_clusters
            if cluster.effective_bonds >= self.parameters.minimum_anchor_bonds
        ]
        if not qualified:
            return None

        strongest = sorted(
            qualified, key=lambda item: item.effective_bonds, reverse=True
        )[:2]
        selected = sorted(strongest, key=lambda item: item.price)
        support = selected[0].price
        exit_price = selected[-1].price
        band_midpoint = (support + exit_price) / 2
        buy_effective = sum(cluster.effective_bonds for cluster in selected)

        sells = [event for event in self.trade_evidence if event.side == "sell"]
        sell_effective = sum(self._effective_bonds(event, now_ms) for event in sells)
        sell_reference = self._weighted_price(sells, now_ms)
        pressure = (
            sell_effective / (buy_effective + sell_effective)
            if buy_effective + sell_effective > 0 else 0.0
        )
        adjustment = min(0.50, pressure)
        reference = band_midpoint
        if sell_reference is not None and sell_reference < band_midpoint:
            reference = band_midpoint * (1 - adjustment) + sell_reference * adjustment

        dominance = buy_effective / (buy_effective + 1.5 * sell_effective)
        volume_strength = min(
            1.0,
            buy_effective / (self.parameters.minimum_anchor_bonds * 2),
        )
        stock_return = self._stock_return(now_ms, 300)
        stock_factor = self._stock_factor(stock_return)
        confidence = (0.45 * volume_strength + 0.55 * dominance) * stock_factor
        return AnchorState(
            support_price=support,
            exit_price=exit_price,
            band_midpoint=band_midpoint,
            reference_price=reference,
            confidence=max(0.0, min(1.0, confidence)),
            buy_effective_bonds=buy_effective,
            sell_effective_bonds=sell_effective,
            downside_pressure=pressure,
            stock_return_5m=stock_return,
            stock_factor=stock_factor,
            buy_clusters=tuple(selected),
            sell_reference_price=sell_reference,
        )

    def _clusters(
        self, events: Iterable[TradeEvidence], now_ms: int,
    ) -> list[PriceCluster]:
        groups: list[list[TradeEvidence]] = []
        for event in sorted(events, key=lambda item: item.price):
            if not groups:
                groups.append([event])
                continue
            center = self._simple_weighted_price(groups[-1])
            if event.price - center <= self.parameters.price_cluster_width + 1e-9:
                groups[-1].append(event)
            else:
                groups.append([event])

        clusters: list[PriceCluster] = []
        for group in groups:
            effective = [self._effective_bonds(event, now_ms) for event in group]
            total_effective = sum(effective)
            if total_effective <= 0:
                continue
            price = sum(
                event.price * weight for event, weight in zip(group, effective)
            ) / total_effective
            clusters.append(PriceCluster(
                price=price,
                effective_bonds=total_effective,
                raw_bonds=sum(event.bonds for event in group),
                events=len(group),
                transactions=sum(event.transactions for event in group),
                maximum_event_bonds=max(event.bonds for event in group),
                first_ts_ms=min(event.market_ts_ms for event in group),
                last_ts_ms=max(event.market_ts_ms for event in group),
            ))
        return clusters

    def _low_bid_opportunity(
        self, tick: ReplayTick, anchor: AnchorState,
    ) -> Opportunity | None:
        parameters = self.parameters
        if not self._entry_window(tick.market_time):
            return None
        if tick.bid1 <= 0 or tick.ask1 <= tick.bid1:
            return None
        if anchor.confidence < parameters.minimum_anchor_confidence:
            return None
        edge = anchor.reference_price - tick.bid1
        exit_edge = anchor.exit_price - tick.bid1
        if edge + 1e-9 < parameters.minimum_entry_edge or exit_edge <= 0:
            return None

        price_bucket_width = 0.05
        price_bucket = round(
            math.floor((tick.bid1 + 1e-9) / price_bucket_width) * price_bucket_width,
            3,
        )
        last_signal = self.last_low_signal_by_bucket.get(price_bucket)
        if (
            last_signal is not None
            and tick.market_ts_ms - last_signal
            < parameters.opportunity_cooldown_seconds * 1000
        ):
            return None
        self.last_low_signal_by_bucket[price_bucket] = tick.market_ts_ms

        improved = tick.bid1 + parameters.price_tick
        if improved >= tick.ask1:
            improved = None
        priority_exit = max(
            tick.bid1 + parameters.price_tick,
            anchor.exit_price - parameters.price_tick,
        )
        return Opportunity(
            kind="low_bid_reversion",
            signal_ts_ms=tick.market_ts_ms,
            market_time=tick.market_time,
            entry_price=tick.bid1,
            improved_entry_price=improved,
            quantity_bonds=parameters.order_quantity_bonds,
            queue_ahead_bonds=tick.bid1_bonds,
            target_exit_price=anchor.exit_price,
            priority_exit_price=priority_exit,
            theoretical_edge=priority_exit - tick.bid1,
            anchor=anchor,
            notes=(
                "join_bid_fill_is_not_guaranteed",
                "inferred_side_is_level1_estimate",
            ),
        )

    def _entry_window(self, market_time: str) -> bool:
        parameters = self.parameters
        if not (
            parameters.earliest_entry_time
            <= market_time
            <= parameters.latest_entry_time
        ):
            return False
        return not ("11:30:00.001" <= market_time < "13:00:00.000")

    def _effective_bonds(self, event: TradeEvidence, now_ms: int) -> float:
        age_seconds = max(0.0, (now_ms - event.market_ts_ms) / 1000)
        decay = math.exp(
            -math.log(2) * age_seconds / self.parameters.evidence_half_life_seconds
        )
        return event.bonds * decay

    def _weighted_price(
        self, events: Iterable[TradeEvidence], now_ms: int,
    ) -> float | None:
        pairs = [(event, self._effective_bonds(event, now_ms)) for event in events]
        total = sum(weight for _, weight in pairs)
        if total <= 0:
            return None
        return sum(event.price * weight for event, weight in pairs) / total

    @staticmethod
    def _simple_weighted_price(events: Iterable[TradeEvidence]) -> float:
        items = list(events)
        total = sum(event.bonds for event in items)
        return sum(event.price * event.bonds for event in items) / total

    def _stock_return(self, now_ms: int, seconds: int) -> float | None:
        values = [item for item in self.stock_prices if item[0] <= now_ms]
        if not values:
            return None
        latest = values[-1][1]
        cutoff = now_ms - seconds * 1000
        earlier = next((price for timestamp, price in values if timestamp >= cutoff), values[0][1])
        return latest / earlier - 1 if earlier > 0 else None

    def _stock_factor(self, stock_return: float | None) -> float:
        if stock_return is None or stock_return >= 0:
            return 1.0
        scale = self.parameters.maximum_stock_drop_5m
        return max(0.25, 1.0 + stock_return / scale)


def generate_maker_report(
    database: Path,
    market_date: str | None,
    bond_code: str,
    stock_code: str,
    parameters: MakerParameters | None = None,
) -> dict[str, Any]:
    parameters = parameters or MakerParameters()
    connection = _read_only_connection(database)
    try:
        selected_date = market_date or _latest_market_date(connection, bond_code)
        if selected_date is None:
            raise RuntimeError(f"No recorded ticks found for {bond_code}")
        has_bond = connection.execute(
            "SELECT 1 FROM raw_ticks WHERE market_date=? AND code=? LIMIT 1",
            (selected_date, bond_code),
        ).fetchone()
        if has_bond is None:
            raise RuntimeError(
                f"No recorded ticks found for {bond_code} on {selected_date}"
            )
        ticks = _load_ticks(connection, selected_date, bond_code, stock_code, parameters)
    finally:
        connection.close()

    analyzer = MakerAnalyzer(bond_code, stock_code, parameters)
    bond_ticks: list[ReplayTick] = []
    for tick in ticks:
        analyzer.on_tick(tick)
        if tick.code == bond_code:
            bond_ticks.append(tick)

    _evaluate_outcomes(analyzer.opportunities, bond_ticks, parameters)
    public_opportunities = [item.public() for item in analyzer.opportunities]
    counts: dict[str, int] = {}
    for item in analyzer.opportunities:
        counts[item.kind] = counts.get(item.kind, 0) + 1
    return {
        "model": "maker_v0.1",
        "causal_replay": True,
        "paper_only": True,
        "date": selected_date,
        "bond_code": bond_code,
        "stock_code": stock_code,
        "database": str(database),
        "parameters": asdict(parameters),
        "data": {
            "ticks": len(ticks),
            "bond_ticks": len(bond_ticks),
            "anchor_observations": analyzer.anchor_observations,
        },
        "summary": {
            "opportunities": len(analyzer.opportunities),
            "by_kind": counts,
            "optimistic_entry_fills": sum(
                1 for item in analyzer.opportunities
                if item.outcome.get("entry_models", {}).get("optimistic", {}).get("filled")
            ),
            "improved_priority_entry_fills": sum(
                1 for item in analyzer.opportunities
                if item.outcome.get("entry_models", {}).get("improved_priority", {}).get("filled")
            ),
            "queue_entry_fills": sum(
                1 for item in analyzer.opportunities
                if item.outcome.get("entry_models", {}).get("queue", {}).get("filled")
            ),
            "conservative_entry_fills": sum(
                1 for item in analyzer.opportunities
                if item.outcome.get("entry_models", {}).get("conservative", {}).get("filled")
            ),
            "outcomes_by_entry_model": _summarize_outcomes(analyzer.opportunities),
        },
        "limitations": [
            "Level 1 snapshots do not reveal true order ownership or exact queue position.",
            "inferred_side is locally estimated, not an exchange Level 2 aggressor flag.",
            "A three-second snapshot can miss or reorder events inside the interval.",
            "Outcome fields use future data only for evaluation, never for signal construction.",
        ],
        "opportunities": public_opportunities,
    }


def _summarize_outcomes(opportunities: list[Opportunity]) -> dict[str, Any]:
    summary: dict[str, dict[str, int]] = {}
    for opportunity in opportunities:
        for model, result in opportunity.outcome.get("entry_models", {}).items():
            model_summary = summary.setdefault(model, {
                "candidates": 0,
                "filled": 0,
                "support_exit_evidenced": 0,
                "reference_exit_evidenced": 0,
                "upper_exit_evidenced": 0,
            })
            model_summary["candidates"] += 1
            if not result.get("filled"):
                continue
            model_summary["filled"] += 1
            exit_levels = result.get("exit_levels", {})
            for level in ("support", "reference", "upper"):
                if exit_levels.get(level, {}).get("evidenced"):
                    model_summary[f"{level}_exit_evidenced"] += 1
    return summary


def _evaluate_outcomes(
    opportunities: list[Opportunity],
    ticks: list[ReplayTick],
    parameters: MakerParameters,
) -> None:
    for opportunity in opportunities:
        end_ms = opportunity.signal_ts_ms + parameters.outcome_horizon_seconds * 1000
        future = [
            tick for tick in ticks
            if opportunity.signal_ts_ms < tick.market_ts_ms <= end_ms
        ]
        if opportunity.kind == "sweep_tail":
            fill = {
                "filled": True,
                "fill_time": opportunity.market_time,
                "fill_price": _round_price(opportunity.entry_price),
                "assumption": "active_sweep_at_signal_snapshot",
            }
            fill.update(_exit_and_path(opportunity, future, opportunity.signal_ts_ms))
            opportunity.outcome = {
                "entry_models": {"active_sweep": fill},
            }
            continue

        entry_models: dict[str, dict[str, Any]] = {}
        optimistic_tick = next(
            (tick for tick in future if _is_sell_trade(tick) and tick.last_price <= opportunity.entry_price + 1e-9),
            None,
        )
        entry_models["optimistic"] = _fill_result(
            opportunity, future, optimistic_tick, "touches_limit"
        )

        improved_price = opportunity.improved_entry_price
        improved_tick = None
        if improved_price is not None:
            improved_tick = next(
                (
                    tick for tick in future
                    if _is_sell_trade(tick)
                    and tick.last_price <= improved_price + 1e-9
                ),
                None,
            )
        entry_models["improved_priority"] = _fill_result(
            opportunity,
            future,
            improved_tick,
            "improves_best_bid_by_one_tick",
            {
                "level1_sequence_uncertain": True,
                "quoted_price": (
                    _round_price(improved_price) if improved_price is not None else None
                ),
            },
            fill_price=improved_price,
        )

        cumulative = 0.0
        queue_tick = None
        required = opportunity.queue_ahead_bonds + opportunity.quantity_bonds
        for tick in future:
            if _is_sell_trade(tick) and tick.last_price <= opportunity.entry_price + 1e-9:
                cumulative += tick.trade_bonds
                if cumulative + 1e-9 >= required:
                    queue_tick = tick
                    break
        entry_models["queue"] = _fill_result(
            opportunity, future, queue_tick, "displayed_queue_consumed",
            {"required_bonds": round(required, 1), "observed_bonds": round(cumulative, 1)},
        )

        conservative_tick = next(
            (
                tick for tick in future
                if _is_sell_trade(tick)
                and tick.last_price <= opportunity.entry_price - parameters.price_tick + 1e-9
            ),
            None,
        )
        entry_models["conservative"] = _fill_result(
            opportunity, future, conservative_tick, "trades_through_one_tick"
        )
        opportunity.outcome = {"entry_models": entry_models}


def _fill_result(
    opportunity: Opportunity,
    future: list[ReplayTick],
    fill_tick: ReplayTick | None,
    assumption: str,
    extra: dict[str, Any] | None = None,
    *,
    fill_price: float | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"filled": fill_tick is not None, "assumption": assumption}
    if extra:
        result.update(extra)
    if fill_tick is None:
        return result
    actual_fill_price = fill_price if fill_price is not None else opportunity.entry_price
    result.update({
        "fill_time": fill_tick.market_time,
        "fill_price": _round_price(actual_fill_price),
        "observed_trade_price": _round_price(fill_tick.last_price),
        "seconds_to_fill": round(
            (fill_tick.market_ts_ms - opportunity.signal_ts_ms) / 1000, 1
        ),
    })
    result.update(_exit_and_path(
        opportunity, future, fill_tick.market_ts_ms, entry_price=actual_fill_price
    ))
    return result


def _exit_and_path(
    opportunity: Opportunity,
    future: list[ReplayTick],
    fill_ms: int,
    *,
    entry_price: float | None = None,
) -> dict[str, Any]:
    actual_entry_price = entry_price if entry_price is not None else opportunity.entry_price
    after_fill = [tick for tick in future if tick.market_ts_ms > fill_ms]
    exit_levels = {
        "support": max(
            actual_entry_price + 0.001,
            opportunity.anchor.support_price - 0.001,
        ),
        "reference": max(
            actual_entry_price + 0.001,
            opportunity.anchor.reference_price - 0.001,
        ),
        "upper": opportunity.priority_exit_price,
    }
    exits: dict[str, Any] = {}
    for name, level in exit_levels.items():
        exit_tick = next(
            (
                tick for tick in after_fill
                if _is_buy_trade(tick) and tick.last_price + 1e-9 >= level
            ),
            None,
        )
        outcome: dict[str, Any] = {
            "price": _round_price(level),
            "evidenced": exit_tick is not None,
        }
        if exit_tick is not None:
            outcome.update({
                "evidence_time": exit_tick.market_time,
                "observed_trade_price": _round_price(exit_tick.last_price),
                "seconds_from_fill": round(
                    (exit_tick.market_ts_ms - fill_ms) / 1000, 1
                ),
                "gross_cny": round(
                    (level - actual_entry_price) * opportunity.quantity_bonds, 2
                ),
            })
        exits[name] = outcome
    path = [tick.bid1 for tick in after_fill if tick.bid1 > 0]
    result: dict[str, Any] = {
        "exit_levels": exits,
        "maximum_adverse_price": _round_price(min(path)) if path else None,
        "maximum_adverse_cny_per_bond": (
            round(min(path) - actual_entry_price, 3) if path else None
        ),
    }
    return result


def _is_buy_trade(tick: ReplayTick) -> bool:
    return tick.trade_bonds > 0 and tick.inferred_side in {"buy", "unknown"}


def _is_sell_trade(tick: ReplayTick) -> bool:
    return tick.trade_bonds > 0 and tick.inferred_side in {"sell", "unknown"}


def _read_only_connection(database: Path) -> sqlite3.Connection:
    path = database.expanduser().resolve()
    if not path.exists():
        raise RuntimeError(f"Database does not exist: {path}")
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _latest_market_date(connection: sqlite3.Connection, bond_code: str) -> str | None:
    row = connection.execute(
        "SELECT MAX(market_date) AS market_date FROM raw_ticks WHERE code=?",
        (bond_code,),
    ).fetchone()
    return str(row["market_date"]) if row and row["market_date"] else None


def _load_ticks(
    connection: sqlite3.Connection,
    market_date: str,
    bond_code: str,
    stock_code: str,
    parameters: MakerParameters,
) -> list[ReplayTick]:
    rows = connection.execute(
        """
        SELECT r.*,
               COALESCE(c.volume_delta,0) AS volume_delta,
               COALESCE(c.transaction_delta,0) AS transaction_delta_value,
               COALESCE(c.inferred_side,'none') AS inferred_side_value,
               COALESCE(c.side_confidence,'none') AS side_confidence_value
        FROM raw_ticks r
        LEFT JOIN tick_changes c ON c.tick_id=r.id
        WHERE r.market_date=? AND r.code IN (?,?)
        ORDER BY r.market_ts_ms,r.received_ts_ns,r.id
        """,
        (market_date, bond_code, stock_code),
    ).fetchall()
    ticks: list[ReplayTick] = []
    for row in rows:
        multiplier = parameters.bonds_per_qmt_hand if row["code"] == bond_code else 1.0
        bids = tuple(
            (
                float(row[f"bid_price_{level}"]),
                float(row[f"bid_volume_{level}"]) * multiplier,
            )
            for level in range(1, 6)
            if float(row[f"bid_price_{level}"]) > 0
        )
        asks = tuple(
            (
                float(row[f"ask_price_{level}"]),
                float(row[f"ask_volume_{level}"]) * multiplier,
            )
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
            trade_bonds=float(row["volume_delta"]) * multiplier,
            transaction_delta=int(row["transaction_delta_value"]),
            inferred_side=str(row["inferred_side_value"]),
            side_confidence=str(row["side_confidence_value"]),
            previous_close=float(row["previous_close"]),
        ))
    return ticks


def write_report(report: dict[str, Any], destination: Path) -> None:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _round_price(value: float) -> float:
    return round(float(value), 3)


def _clock(market_ts_ms: int) -> str:
    return datetime.fromtimestamp(
        market_ts_ms / 1000, tz=SHANGHAI
    ).time().isoformat(timespec="milliseconds")
