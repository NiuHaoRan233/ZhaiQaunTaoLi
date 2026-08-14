from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR
from datetime import date, datetime, timezone
from typing import Any

from .config import AppConfig
from .database import SQLiteStore
from .maker import (
    MakerAnalyzer,
    MakerParameters,
    MarketAssessment,
    Opportunity,
    ReplayTick,
    _load_ticks,
)
from .recorder import RecordedTick
from .types import SHANGHAI, Tick


QMT_BONDS_PER_HAND = 10.0


@dataclass(frozen=True)
class MakerPolicyProfile:
    """Immutable decision-policy identity for one execution branch."""

    model_id: str
    model_version: str
    parent_model_id: str | None
    execution_mode: str
    enable_priority_v11_extensions: bool


PRIORITY_POLICY_V11 = MakerPolicyProfile(
    model_id="maker_priority_v1_1",
    model_version="1.1",
    parent_model_id="maker_shared_v1_0",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
)
QUEUE_POLICY_V10 = MakerPolicyProfile(
    model_id="maker_queue_v1_0",
    model_version="1.0",
    parent_model_id="maker_shared_v1_0",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
)
WINDFALL_POLICY_V10 = MakerPolicyProfile(
    model_id="maker_windfall_v1_0",
    model_version="1.0",
    parent_model_id=None,
    execution_mode="windfall",
    enable_priority_v11_extensions=False,
)


def maker_policy_for_mode(fill_mode: str) -> MakerPolicyProfile:
    if fill_mode == "priority":
        return PRIORITY_POLICY_V11
    if fill_mode == "queue":
        return QUEUE_POLICY_V10
    if fill_mode == "windfall":
        return WINDFALL_POLICY_V10
    raise ValueError(f"Unknown maker fill mode: {fill_mode}")


def configured_maker_bond_codes(config: AppConfig) -> tuple[str, ...]:
    """Return the independently simulated maker instruments."""
    return config.maker_paper.bond_codes or (config.qmt.bond_code,)


def maker_strategy_prefix(config: AppConfig, bond_code: str) -> str:
    """Keep the primary bond's historical IDs while namespacing extra bonds."""
    if bond_code == config.qmt.bond_code:
        return "maker_v01"
    code_key = bond_code.split(".", 1)[0].lower()
    return f"maker_{code_key}_v01"


def maker_strategy_ids(config: AppConfig, bond_code: str) -> tuple[str, ...]:
    prefix = maker_strategy_prefix(config, bond_code)
    strategy_ids = [
        f"{prefix}_{mode}" for mode in config.maker_paper.fill_modes
    ]
    if config.maker_paper.super_windfall_enabled:
        strategy_ids.append(f"{prefix}_super_windfall")
    return tuple(strategy_ids)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _floor_to_tick(value: float, price_tick: float) -> float:
    """Quantize a simulated limit price down to a legal exchange price tick."""
    step = Decimal(str(price_tick))
    units = (Decimal(str(value)) / step).to_integral_value(rounding=ROUND_FLOOR)
    return float(units * step)


@dataclass
class MakerLot:
    db_id: int
    kind: str
    opened_ms: int
    entry_price: float | None
    original_quantity: float
    remaining_quantity: float
    target_price: float | None = None


@dataclass
class MakerOrder:
    db_id: int
    side: str
    kind: str
    lot_id: int | None
    created_ms: int
    limit_price: float
    quantity: float
    filled_quantity: float = 0.0
    queue_ahead: float = 0.0
    target_price: float | None = None

    @property
    def remaining(self) -> float:
        return max(0.0, self.quantity - self.filled_quantity)


@dataclass
class LegacyAskWall:
    price: float
    first_ms: int
    last_seen_ms: int
    peak_bonds: float
    current_bonds: float
    aggressive_buys: deque[tuple[int, float]] = field(default_factory=deque)
    emitted: bool = False


@dataclass(frozen=True)
class MakerDecisionContext:
    """One causal view shared by entry, exit and inventory decisions."""

    reference_price: float
    reference_source: str
    reliable_anchor: bool
    spread: float
    bid_support_bonds: float
    ask_supply_bonds: float
    wall_threshold_bonds: float
    breakout_support_price: float = 0.0
    breakout_lower_sell_bonds: float = 0.0

    @property
    def has_bid_support(self) -> bool:
        return self.bid_support_bonds + 1e-9 >= self.wall_threshold_bonds

    @property
    def has_ask_supply(self) -> bool:
        return self.ask_supply_bonds + 1e-9 >= self.wall_threshold_bonds

    @property
    def breakout_support_strong(self) -> bool:
        return (
            self.breakout_support_price > 0
            and self.breakout_lower_sell_bonds + 1e-9 < 5_000.0
        )


@dataclass
class MakerAccount:
    market_date: str
    bond_code: str
    strategy_id: str
    fill_mode: str
    policy: MakerPolicyProfile
    initial_inventory: float
    maximum_inventory: float
    initial_cash: float
    cash: float
    inventory: float
    lots: dict[int, MakerLot] = field(default_factory=dict)
    buy_order: MakerOrder | None = None
    sell_orders: dict[int, MakerOrder] = field(default_factory=dict)
    fills: int = 0
    trading_pnl: float = 0.0
    last_market_ts_ms: int = 0
    last_tick_id: int = 0
    last_bid: float = 0.0
    last_ask: float = 0.0
    replenishment_quantity: float = 0.0
    replenishment_sale_value: float = 0.0
    last_active_entry_price: float | None = None
    purpose: str = "standard"


class MakerPaperEngine:
    """
    Inventory-aware, broker-free maker simulation.

    All orders and fills exist only in SQLite. This class never imports or
    calls a trading API. One account is maintained per configured fill mode.
    """

    def __init__(
        self, config: AppConfig, store: SQLiteStore, *,
        bond_code: str | None = None, strategy_prefix: str | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.bond_code = bond_code or config.qmt.bond_code
        self.strategy_prefix = strategy_prefix or maker_strategy_prefix(
            config, self.bond_code
        )
        paper = config.maker_paper
        self.parameters = MakerParameters(
            price_tick=paper.price_tick,
            order_quantity_bonds=paper.order_quantity_bonds,
            earliest_entry_time=paper.earliest_entry,
            latest_entry_time=paper.latest_entry,
        )
        self.analyzer = MakerAnalyzer(
            self.bond_code, config.qmt.stock_code, self.parameters
        )
        self.accounts: dict[str, MakerAccount] = {}
        self.market_date: str | None = None
        self.fills_this_run = 0
        self.previous_close_reference = 0.0
        self.observed_market_trade = False
        self.last_confirmed_rise_trade_ts_ms = 0
        self.last_legacy_reliable_reference = 0.0
        self.last_legacy_reliable_reference_ts_ms = 0
        self.legacy_breakout_support_price = 0.0
        self.legacy_breakout_support_ts_ms = 0
        self.legacy_ask_walls: dict[float, LegacyAskWall] = {}

    @property
    def enabled(self) -> bool:
        return self.config.maker_paper.enabled

    def rebuild_date(self, market_date: date | str, *, clear: bool = True) -> None:
        """Deterministically rebuild derived paper state from today's saved ticks."""
        if not self.enabled:
            return
        date_text = market_date.isoformat() if isinstance(market_date, date) else market_date
        if clear:
            self._clear_date(date_text)
        self._start_date(date_text)
        ticks = _load_ticks(
            self.store.connection, date_text,
            self.bond_code, self.config.qmt.stock_code,
            self.parameters,
        )
        for tick in ticks:
            self.on_replay_tick(tick, persist=True)
        self.store.app_event(
            "info", "maker_paper_rebuilt",
            "Maker paper accounts rebuilt from recorded ticks",
            {
                "market_date": date_text,
                "bond_code": self.bond_code,
                "ticks": len(ticks),
                "accounts": {
                    account.strategy_id: account.policy.model_id
                    for account in self.accounts.values()
                },
                "paper_only": True,
            },
        )

    def on_recorded_tick(self, recorded: RecordedTick) -> None:
        if not self.enabled or not recorded.is_new:
            return
        tick = recorded.tick
        multiplier = (
            self.parameters.bonds_per_qmt_hand
            if tick.code == self.bond_code else 1.0
        )
        replay = ReplayTick(
            tick_id=recorded.tick_id,
            code=tick.code,
            market_ts_ms=tick.market_ts_ms,
            market_date=tick.market_datetime.date().isoformat(),
            market_time=tick.market_datetime.time().isoformat(timespec="milliseconds"),
            last_price=tick.last_price,
            bids=tuple(
                (price, volume * multiplier)
                for price, volume in zip(tick.bid_prices, tick.bid_volumes)
                if price > 0
            ),
            asks=tuple(
                (price, volume * multiplier)
                for price, volume in zip(tick.ask_prices, tick.ask_volumes)
                if price > 0
            ),
            trade_bonds=recorded.change.volume_delta * multiplier,
            transaction_delta=recorded.change.transaction_delta,
            inferred_side=recorded.change.inferred_side,
            side_confidence=recorded.change.side_confidence,
            previous_close=tick.previous_close,
        )
        self.on_replay_tick(replay, persist=True, received_ts_ns=tick.received_ts_ns)

    def on_replay_tick(
        self, tick: ReplayTick, *, persist: bool,
        received_ts_ns: int | None = None,
    ) -> None:
        if not self.enabled:
            return
        if self.market_date != tick.market_date:
            self._start_date(tick.market_date)

        if tick.code == self.bond_code:
            if tick.previous_close > 0:
                self.previous_close_reference = tick.previous_close
            for account in self.accounts.values():
                self._process_resting_orders(
                    account, tick, persist=persist,
                    received_ts_ns=received_ts_ns or tick.market_ts_ms * 1_000_000,
                )

        emitted = self.analyzer.on_tick(tick)
        if tick.code != self.bond_code:
            return

        legacy_sweeps = self._legacy_sweep_opportunities(tick)
        if (
            self.analyzer.last_anchor is not None
            and self.analyzer.last_anchor.confidence + 1e-9
                >= self.parameters.minimum_anchor_confidence
        ):
            self.last_legacy_reliable_reference = (
                self.analyzer.last_anchor.reference_price
            )
            self.last_legacy_reliable_reference_ts_ms = tick.market_ts_ms

        if tick.trade_bonds > 0:
            self.observed_market_trade = True

        for account in self._standard_accounts():
            opportunities = (
                emitted
                if account.policy.enable_priority_v11_extensions
                else legacy_sweeps
            )
            for opportunity in opportunities:
                if opportunity.kind == "sweep_tail":
                    self._active_sweep(
                        account, tick, opportunity, persist=persist
                    )

        assessment = self.analyzer.assess_market(
            tick, tick.previous_close or self.previous_close_reference,
        )
        previous_ask = next(
            (
                account.last_ask for account in self._standard_accounts()
                if account.last_ask > 0
            ),
            0.0,
        )
        if (
            assessment.state == "rising"
            and tick.inferred_side == "buy"
            and tick.trade_bonds + 1e-9
                >= self.parameters.order_quantity_bonds
            and previous_ask > 0
            and tick.last_price + self.parameters.fair_price_tolerance + 1e-9
                >= previous_ask
            and tick.ask1 - previous_ask + 1e-9
                >= self.parameters.minimum_sweep_jump
        ):
            self.last_confirmed_rise_trade_ts_ms = tick.market_ts_ms
        for account in self._standard_accounts():
            self._active_discount_entry(
                account, tick, assessment, persist=persist,
            )
        for account in self._standard_accounts():
            if account.policy.enable_priority_v11_extensions:
                self._active_profitable_turnover_exit(
                    account, tick, persist=persist,
                    received_ts_ns=(
                        received_ts_ns or tick.market_ts_ms * 1_000_000
                    ),
                )
                self._active_inventory_risk_exit(
                    account, tick, assessment, persist=persist,
                    received_ts_ns=(
                        received_ts_ns or tick.market_ts_ms * 1_000_000
                    ),
                )

        for account in self.accounts.values():
            if tick.bid1 <= 0 or tick.ask1 <= tick.bid1:
                self._cancel_all_orders(
                    account, tick, "invalid_book", persist=persist
                )
                self._mark_account(account, tick, persist=persist)
                continue
            if account.purpose == "super_windfall":
                self._refresh_super_windfall(
                    account, tick, assessment, persist=persist,
                )
            else:
                self._refresh_orders(
                    account, tick, assessment, persist=persist,
                )
            self._mark_account(account, tick, persist=persist)

    def _standard_accounts(self) -> tuple[MakerAccount, ...]:
        return tuple(
            account for account in self.accounts.values()
            if account.purpose == "standard"
        )

    def _clear_date(self, market_date: str) -> None:
        for table in (
            "maker_paper_fills", "maker_paper_orders", "maker_paper_lots",
            "maker_paper_accounts", "maker_paper_model_assignments",
        ):
            self.store.connection.execute(
                f"DELETE FROM {table} WHERE market_date=?", (market_date,)
            )
        self.store.connection.commit()

    def _start_date(self, market_date: str) -> None:
        self.market_date = market_date
        self.analyzer = MakerAnalyzer(
            self.bond_code, self.config.qmt.stock_code, self.parameters
        )
        self.accounts = {}
        self.previous_close_reference = 0.0
        self.observed_market_trade = False
        self.last_confirmed_rise_trade_ts_ms = 0
        self.last_legacy_reliable_reference = 0.0
        self.last_legacy_reliable_reference_ts_ms = 0
        self.legacy_breakout_support_price = 0.0
        self.legacy_breakout_support_ts_ms = 0
        self.legacy_ask_walls = {}
        paper = self.config.maker_paper
        for mode in paper.fill_modes:
            strategy_id = f"{self.strategy_prefix}_{mode}"
            policy = maker_policy_for_mode(mode)
            account = MakerAccount(
                market_date=market_date,
                bond_code=self.bond_code,
                strategy_id=strategy_id,
                fill_mode=mode,
                policy=policy,
                initial_inventory=paper.initial_inventory_bonds,
                maximum_inventory=paper.maximum_inventory_bonds,
                initial_cash=paper.initial_cash_cny,
                cash=paper.initial_cash_cny,
                inventory=paper.initial_inventory_bonds,
            )
            lot_id = self.store.insert_maker_lot({
                "run_id": self.store.run_id,
                "market_date": market_date,
                "strategy_id": strategy_id,
                "kind": "base",
                "opened_market_ts_ms": 0,
                "entry_price": None,
                "original_quantity": paper.initial_inventory_bonds,
                "remaining_quantity": paper.initial_inventory_bonds,
                "target_price": None,
                "status": "open",
                "updated_market_ts_ms": 0,
            })
            account.lots[lot_id] = MakerLot(
                lot_id, "base", 0, None,
                paper.initial_inventory_bonds, paper.initial_inventory_bonds,
            )
            self.accounts[strategy_id] = account
            self._persist_model_assignment(account)
            self._persist_account(account)
        if paper.super_windfall_enabled:
            strategy_id = f"{self.strategy_prefix}_super_windfall"
            policy = maker_policy_for_mode("windfall")
            account = MakerAccount(
                market_date=market_date,
                bond_code=self.bond_code,
                strategy_id=strategy_id,
                fill_mode="windfall",
                policy=policy,
                initial_inventory=0.0,
                maximum_inventory=paper.super_windfall_quantity_bonds,
                initial_cash=paper.super_windfall_credit_cny,
                cash=paper.super_windfall_credit_cny,
                inventory=0.0,
                purpose="super_windfall",
            )
            self.accounts[strategy_id] = account
            self._persist_model_assignment(account)
            self._persist_account(account)

    def _process_resting_orders(
        self, account: MakerAccount, tick: ReplayTick, *, persist: bool,
        received_ts_ns: int,
    ) -> None:
        if tick.trade_bonds <= 0:
            return
        available = tick.trade_bonds
        if tick.inferred_side in {"buy", "unknown"}:
            for lot_id, order in sorted(
                list(account.sell_orders.items()),
                key=lambda item: (
                    item[1].limit_price,
                    account.lots[item[0]].kind == "base",
                    item[1].created_ms,
                ),
            ):
                if available <= 1e-9 or tick.last_price + 1e-9 < order.limit_price:
                    continue
                available = self._consume_queue(order, available, account.fill_mode)
                quantity = min(available, order.remaining)
                if quantity <= 1e-9:
                    continue
                self._fill_sell(
                    account, tick, order, quantity, received_ts_ns, persist=persist
                )
                available -= quantity

        if (
            available > 1e-9
            and tick.inferred_side in {"sell", "unknown"}
            and account.buy_order is not None
            and tick.last_price <= account.buy_order.limit_price + 1e-9
        ):
            order = account.buy_order
            available = self._consume_queue(order, available, account.fill_mode)
            capacity = max(0.0, account.maximum_inventory - account.inventory)
            affordable = account.cash / order.limit_price if order.limit_price > 0 else 0.0
            quantity = min(available, order.remaining, capacity, affordable)
            if quantity > 1e-9:
                self._fill_buy(
                    account, tick, order, quantity, received_ts_ns,
                    kind=order.kind, target_price=order.target_price,
                    persist=persist,
                    reason=(
                        "super_windfall_buy"
                        if order.kind == "super_windfall"
                        else "passive_buy"
                    ),
                )

    @staticmethod
    def _consume_queue(order: MakerOrder, available: float, fill_mode: str) -> float:
        if fill_mode != "queue" or order.queue_ahead <= 1e-9:
            return available
        consumed = min(order.queue_ahead, available)
        order.queue_ahead -= consumed
        return available - consumed

    def _legacy_sweep_opportunities(
        self, tick: ReplayTick,
    ) -> tuple[Opportunity, ...]:
        """Reconstruct the 1.0 single-price wall sweep for queue accounts.

        Priority 1.1 groups adjacent legal prices and supports an additional
        thin-cluster pattern. Queue 1.0 instead remembers and validates one
        exact displayed ask price, matching the pre-2026-08-14 execution
        model and avoiding automatic inheritance from the priority branch.
        """
        parameters = self.parameters
        now_ms = tick.market_ts_ms
        cutoff = now_ms - parameters.wall_memory_seconds * 1000
        self.legacy_ask_walls = {
            price: wall for price, wall in self.legacy_ask_walls.items()
            if wall.last_seen_ms >= cutoff
        }
        visible = {
            round(price, 6): bonds for price, bonds in tick.asks if price > 0
        }
        trade_price = round(tick.last_price, 6)
        if tick.trade_bonds > 0 and tick.inferred_side == "buy":
            wall = self.legacy_ask_walls.get(trade_price)
            if wall is not None:
                wall.aggressive_buys.append((now_ms, tick.trade_bonds))
        for price, bonds in visible.items():
            wall = self.legacy_ask_walls.get(price)
            if wall is None:
                self.legacy_ask_walls[price] = LegacyAskWall(
                    price, now_ms, now_ms, bonds, bonds,
                )
                continue
            wall.last_seen_ms = now_ms
            wall.current_bonds = bonds
            wall.peak_bonds = max(wall.peak_bonds, bonds)

        emitted: list[Opportunity] = []
        rapid_cutoff = (
            now_ms - parameters.sweep_consumption_window_seconds * 1000
        )
        for price, wall in self.legacy_ask_walls.items():
            while (
                wall.aggressive_buys
                and wall.aggressive_buys[0][0] < rapid_cutoff
            ):
                wall.aggressive_buys.popleft()
            if wall.emitted or price not in visible:
                continue
            current = visible[price]
            rapid_buys = sum(quantity for _, quantity in wall.aggressive_buys)
            consumed = min(
                rapid_buys, max(0.0, wall.peak_bonds - current)
            )
            consumed_ratio = (
                consumed / wall.peak_bonds if wall.peak_bonds > 0 else 0.0
            )
            planned_quantity = min(
                parameters.order_quantity_bonds, current
            )
            minimum_source = max(
                parameters.minimum_sweep_source_bonds,
                parameters.minimum_sweep_source_multiple * planned_quantity,
            )
            higher_asks = sorted(
                ask_price for ask_price in visible if ask_price > price + 1e-9
            )
            next_ask = higher_asks[0] if higher_asks else 0.0
            jump = next_ask - price if next_ask > 0 else 0.0
            if not (
                self.analyzer._entry_window(tick.market_time)
                and planned_quantity > 0
                and wall.peak_bonds + 1e-9 >= minimum_source
                and consumed_ratio + 1e-9
                    >= parameters.minimum_sweep_consumed_ratio
                and current <= parameters.maximum_sweep_tail_bonds + 1e-9
                and jump + 1e-9 >= parameters.minimum_sweep_jump
            ):
                continue
            anchor = self.analyzer.last_anchor
            if anchor is None or not self.analyzer._sweep_temperature_supportive(
                price, rapid_buys, now_ms,
            ):
                continue
            first_trade_ms = (
                wall.aggressive_buys[0][0]
                if wall.aggressive_buys else now_ms
            )
            priority_exit = max(
                price + parameters.price_tick,
                next_ask - parameters.price_tick,
            )
            emitted.append(Opportunity(
                kind="sweep_tail",
                signal_ts_ms=now_ms,
                market_time=tick.market_time,
                entry_price=price,
                quantity_bonds=planned_quantity,
                target_exit_price=next_ask,
                priority_exit_price=priority_exit,
                theoretical_edge=priority_exit - price,
                anchor=anchor,
                source_wall_bonds=wall.peak_bonds,
                consumed_bonds=consumed,
                consumed_ratio=consumed_ratio,
                consumption_seconds=(now_ms - first_trade_ms) / 1000,
                tail_bonds=current,
                next_ask_price=next_ask,
                notes=(
                    "legacy_single_price_wall_tail_consumption",
                    "active_tail_sweep_uses_current_level1_snapshot",
                ),
            ))
            active_support = (
                self.legacy_breakout_support_price
                if now_ms - self.legacy_breakout_support_ts_ms
                    <= parameters.breakout_support_seconds * 1000
                else 0.0
            )
            self.legacy_breakout_support_price = max(active_support, price)
            self.legacy_breakout_support_ts_ms = now_ms
            wall.emitted = True
        merged: dict[float, Opportunity] = {}
        for opportunity in emitted:
            execution_price = _floor_to_tick(
                opportunity.entry_price, parameters.price_tick
            )
            existing = merged.get(execution_price)
            if existing is None:
                opportunity.entry_price = execution_price
                merged[execution_price] = opportunity
                continue
            existing.quantity_bonds = min(
                parameters.order_quantity_bonds,
                existing.quantity_bonds + opportunity.quantity_bonds,
            )
            existing.tail_bonds = (
                (existing.tail_bonds or 0.0)
                + (opportunity.tail_bonds or 0.0)
            )
            existing.source_wall_bonds = max(
                existing.source_wall_bonds or 0.0,
                opportunity.source_wall_bonds or 0.0,
            )
            existing.consumed_bonds = max(
                existing.consumed_bonds or 0.0,
                opportunity.consumed_bonds or 0.0,
            )
        return tuple(merged.values())

    def _active_sweep(
        self, account: MakerAccount, tick: ReplayTick,
        opportunity: Opportunity, *, persist: bool,
    ) -> None:
        capacity = max(0.0, account.maximum_inventory - account.inventory)
        # A wall-consumption breakout establishes the swept price as support.
        # Chasing that support is only for restoring a base-inventory deficit;
        # it must not turn a full base position into an extra high-cost lot.
        if (
            account.policy.enable_priority_v11_extensions
            and
            opportunity.entry_price + self.parameters.fair_price_tolerance
            >= opportunity.anchor.reference_price
            and opportunity.theoretical_edge + 1e-9
                < self.parameters.minimum_thin_sweep_jump
        ):
            capacity = min(
                capacity,
                max(0.0, account.initial_inventory - account.inventory),
            )
        affordable = account.cash / opportunity.entry_price
        quantity = min(opportunity.quantity_bonds, capacity, affordable)
        if quantity <= 1e-9:
            return
        order = self._new_order(
            account, tick, side="buy", kind="sweep_tail", lot_id=None,
            price=opportunity.entry_price, quantity=quantity, queue_ahead=0.0,
            target_price=opportunity.priority_exit_price, persist=persist,
        )
        self._fill_buy(
            account, tick, order, quantity, tick.market_ts_ms * 1_000_000,
            kind="sweep_tail", target_price=opportunity.priority_exit_price,
            persist=persist, reason="active_tail_sweep",
        )

    def _active_discount_entry(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, *, persist: bool,
    ) -> None:
        """Actively take a cheap ask when price distance or support makes it safe."""
        context = self._decision_context(tick, account.policy)
        edge = context.reference_price - tick.ask1
        if account.policy.enable_priority_v11_extensions:
            active_entry_safe = (
                (
                    context.breakout_support_strong
                    and edge + self.parameters.fair_price_tolerance + 1e-9
                        >= self.parameters.minimum_base_high_sell_edge
                )
                or (
                    not context.breakout_support_strong
                    and edge + 1e-9
                        >= self.parameters.minimum_active_entry_edge
                )
            )
        else:
            active_entry_safe = (
                edge + 1e-9 >= self.parameters.minimum_active_entry_edge
                or (
                    edge + 1e-9
                        >= self.parameters.legacy_queue_supported_active_edge
                    and context.has_bid_support
                )
            )
        if not (
            self.observed_market_trade
            and self.analyzer._entry_window(tick.market_time)
            and context.reference_price > 0
            and context.reference_source != "persistent_inside_market"
            and tick.ask1 > tick.bid1 > 0
            and tick.ask1_bonds > 0
            and active_entry_safe
        ):
            return
        if (
            account.policy.enable_priority_v11_extensions
            and
            assessment.iron_floor_price is not None
            and assessment.state != "rising"
            and not self._confirmed_rise_is_recent(tick)
            and tick.ask1 - assessment.iron_floor_price + 1e-9
                > self.parameters.maximum_iron_floor_entry_premium
        ):
            return
        if (
            account.last_active_entry_price is not None
            and tick.ask1
                > account.last_active_entry_price
                    - self.parameters.minimum_distinct_active_improvement + 1e-9
        ):
            return
        capacity = max(0.0, account.maximum_inventory - account.inventory)
        affordable = account.cash / tick.ask1
        quantity = min(
            self.parameters.order_quantity_bonds,
            tick.ask1_bonds,
            capacity,
            affordable,
        )
        if quantity <= 1e-9:
            return
        order = self._new_order(
            account, tick, side="buy", kind="deep_discount_sweep", lot_id=None,
            price=tick.ask1, quantity=quantity, queue_ahead=0.0,
            target_price=None, persist=persist,
        )
        self._fill_buy(
            account, tick, order, quantity, tick.market_ts_ms * 1_000_000,
            kind="deep_discount_sweep", target_price=None,
            persist=persist, reason="active_deep_discount",
        )
        account.last_active_entry_price = tick.ask1
        if account.buy_order is not None:
            self._cancel_order(
                account, account.buy_order, tick,
                "active_entry_replaced_passive_buy", persist,
            )

    def _active_inventory_risk_exit(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, *, persist: bool,
        received_ts_ns: int,
    ) -> None:
        """Hit the visible bid before a thin downside ladder opens up.

        This only reduces extra inventory bought above the daily base.  A
        correct low entry can still become unsafe after the offer ladder keeps
        compressing; near-flat execution at the remaining best bid is then
        preferable to waiting for a passive high-side fill.
        """
        parameters = self.parameters
        minimum_sell_bonds = parameters.order_quantity_bonds * 2
        sell_dominant = (
            assessment.recent_sell_bonds + 1e-9 >= minimum_sell_bonds
            and assessment.recent_sell_bonds + 1e-9
                >= assessment.recent_buy_bonds
                * parameters.downside_sell_imbalance_ratio
        )
        bearish_vacuum = (
            assessment.short_ask_change
                <= -parameters.minimum_short_ask_drop + 1e-9
            and assessment.downside_book_vacuum
            and sell_dominant
        )
        if not (
            (bearish_vacuum or assessment.fragile_top_bid)
            and tick.bid1 > 0
            and tick.bid1_bonds > 0
            and account.inventory > account.initial_inventory + 1e-9
        ):
            return

        available = min(
            tick.bid1_bonds,
            account.inventory - account.initial_inventory,
        )
        candidates = sorted(
            (
                lot for lot in account.lots.values()
                if lot.entry_price is not None
                and lot.remaining_quantity > 1e-9
                and lot.entry_price - tick.bid1
                    <= parameters.maximum_near_flat_exit_loss + 1e-9
            ),
            key=lambda lot: (lot.opened_ms, lot.db_id),
            reverse=True,
        )
        if not candidates:
            return
        if account.buy_order is not None:
            self._cancel_order(
                account, account.buy_order, tick,
                "downside_risk_exit", persist,
            )
        for lot in candidates:
            quantity = min(available, lot.remaining_quantity)
            if quantity <= 1e-9:
                break
            existing = account.sell_orders.get(lot.db_id)
            if existing is not None:
                self._cancel_order(
                    account, existing, tick,
                    "active_risk_exit_replaced_passive_sell", persist,
                )
            order = self._new_order(
                account, tick, side="sell", kind="inventory_risk_exit",
                lot_id=lot.db_id, price=tick.bid1, quantity=quantity,
                queue_ahead=0.0, target_price=tick.bid1, persist=persist,
            )
            account.sell_orders[lot.db_id] = order
            self._fill_sell(
                account, tick, order, quantity, received_ts_ns,
                persist=persist, reason="active_downside_risk_exit",
            )
            available -= quantity

    def _active_profitable_turnover_exit(
        self, account: MakerAccount, tick: ReplayTick, *, persist: bool,
        received_ts_ns: int,
    ) -> None:
        """Take a nearby bid when an extra lot already has a clean T edge.

        In a tight, two-sided market the executable round trip matters more
        than waiting for a distant fair-value target.  This only turns over
        inventory above the base position and never sells the base lot.
        """
        if not (
            tick.bid1 > 0
            and tick.bid1_bonds > 0
            and tick.ask1 > tick.bid1
            and tick.ask1 - tick.bid1
                <= self.parameters.maximum_active_turnover_spread + 1e-9
            and account.inventory > account.initial_inventory + 1e-9
        ):
            return
        available = min(
            tick.bid1_bonds,
            account.inventory - account.initial_inventory,
        )
        candidates = sorted(
            (
                lot for lot in account.lots.values()
                if lot.entry_price is not None
                and lot.remaining_quantity > 1e-9
                and tick.bid1 - lot.entry_price + 1e-9
                    >= self.parameters.minimum_passive_turnover_edge
            ),
            key=lambda lot: (lot.opened_ms, lot.db_id),
        )
        if not candidates:
            return
        if account.buy_order is not None:
            self._cancel_order(
                account, account.buy_order, tick,
                "active_turnover_exit", persist,
            )
        for lot in candidates:
            quantity = min(available, lot.remaining_quantity)
            if quantity <= 1e-9:
                break
            existing = account.sell_orders.get(lot.db_id)
            if existing is not None:
                self._cancel_order(
                    account, existing, tick,
                    "active_turnover_replaced_passive_sell", persist,
                )
            order = self._new_order(
                account, tick, side="sell", kind="inventory_turnover_exit",
                lot_id=lot.db_id, price=tick.bid1, quantity=quantity,
                queue_ahead=0.0, target_price=tick.bid1, persist=persist,
            )
            account.sell_orders[lot.db_id] = order
            self._fill_sell(
                account, tick, order, quantity, received_ts_ns,
                persist=persist, reason="active_tight_spread_turnover",
            )
            available -= quantity

    def _refresh_super_windfall(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, *, persist: bool,
    ) -> None:
        """Keep one sticky order at a deeply anomalous bid-book level."""
        if account.inventory >= account.maximum_inventory - 1e-9:
            if account.buy_order is not None:
                self._cancel_order(
                    account, account.buy_order, tick,
                    "super_windfall_capacity_full", persist,
                )
            return
        recent_trade_reference = self.analyzer.recent_trade_reference(
            tick.market_ts_ms,
            self.parameters.windfall_recent_trade_window_seconds,
        )
        references = [
            value for value in (
                assessment.reference_price,
                recent_trade_reference,
            )
            if value is not None and value > 0
        ]
        if not references:
            return
        reference = min(references)
        candidate: tuple[float, float, float] | None = None
        for upper, lower in zip(tick.bids, tick.bids[1:]):
            book_gap = upper[0] - lower[0]
            discount = reference - lower[0]
            if (
                book_gap + 1e-9
                    >= self.parameters.minimum_windfall_book_gap
                and discount + 1e-9
                    >= self.parameters.minimum_windfall_discount
            ):
                candidate = (lower[0], lower[1], upper[0])
                break
        if candidate is None and tick.bids:
            top_gap = max(tick.last_price, tick.ask1) - tick.bid1
            if (
                top_gap + 1e-9
                    >= self.parameters.minimum_windfall_book_gap
                and reference - tick.bid1 + 1e-9
                    >= self.parameters.minimum_windfall_discount
            ):
                candidate = (tick.bid1, tick.bid1_bonds, tick.ask1)
        if candidate is None:
            return

        level_price, _, upper_price = candidate
        price = level_price + self.parameters.price_tick
        if price >= upper_price - 1e-9:
            price = level_price
        price = _floor_to_tick(price, self.parameters.price_tick)
        capacity = account.maximum_inventory - account.inventory
        affordable = account.cash / price if price > 0 else 0.0
        quantity = min(
            self.config.maker_paper.super_windfall_quantity_bonds,
            capacity,
            affordable,
        )
        if quantity <= 1e-9:
            return
        if account.buy_order is not None:
            if price <= account.buy_order.limit_price + 1e-9:
                return
            self._cancel_order(
                account, account.buy_order, tick,
                "super_windfall_better_anomaly", persist,
            )
        queue = self._book_quantity(tick, "buy", price)
        if price > level_price:
            queue = 0.0
        account.buy_order = self._new_order(
            account, tick, side="buy", kind="super_windfall",
            lot_id=None, price=price, quantity=quantity,
            queue_ahead=queue, target_price=None, persist=persist,
        )

    def _fair_reference(self) -> float:
        return self._decision_context(None).reference_price

    def _decision_context(
        self, tick: ReplayTick | None,
        policy: MakerPolicyProfile | None = None,
    ) -> MakerDecisionContext:
        policy = policy or PRIORITY_POLICY_V11
        anchor = self.analyzer.last_anchor
        reliable = (
            anchor is not None
            and anchor.confidence >= self.parameters.minimum_anchor_confidence
        )
        reference = (
            anchor.reference_price if reliable and anchor is not None
            else self.previous_close_reference
        )
        source = "intraday_trade_anchor" if reliable else "previous_close"
        if (
            not policy.enable_priority_v11_extensions
            and not reliable
            and self.last_legacy_reliable_reference > 0
            and tick is not None
            and tick.market_ts_ms - self.last_legacy_reliable_reference_ts_ms
                <= self.parameters.market_temperature_window_seconds * 1000
        ):
            reference = self.last_legacy_reliable_reference
            source = "legacy_last_trade_anchor"
        now_ms = tick.market_ts_ms if tick is not None else 0
        book_reference = (
            self.analyzer.persistent_book_reference(now_ms)
            if not reliable and now_ms > 0 else None
        )
        if (
            policy.enable_priority_v11_extensions
            and book_reference is not None
        ):
            reference = book_reference
            source = "persistent_inside_market"
        elif (
            policy.enable_priority_v11_extensions
            and
            not reliable
            and tick is not None
            and self.analyzer.provisional_midpoint_ready()
            and tick.ask1 > tick.bid1 > 0
            and tick.ask1 - tick.bid1
                <= self.parameters.maximum_provisional_midpoint_spread + 1e-9
        ):
            reference = (tick.bid1 + tick.ask1) / 2
            source = "current_midpoint"
        if (
            not policy.enable_priority_v11_extensions
            and self.legacy_breakout_support_price > 0
            and now_ms - self.legacy_breakout_support_ts_ms
                <= self.parameters.breakout_support_seconds * 1000
        ):
            breakout_support = self.legacy_breakout_support_price
            breakout_lower_sells = 0.0
        else:
            breakout_support = (
                self.analyzer.active_breakout_support(now_ms)
                if now_ms > 0 else None
            )
            breakout_lower_sells = (
                self.analyzer.breakout_lower_sell_bonds(now_ms)
                if breakout_support is not None else 0.0
            )
        breakout_strong = (
            breakout_support is not None
            and breakout_lower_sells + 1e-9
                < self.parameters.breakout_weakening_sell_bonds
        )
        if breakout_strong and breakout_support > reference:
            reference = breakout_support
            source = "large_buy_breakout_support"
        if tick is None:
            return MakerDecisionContext(
                reference, source, reliable, 0.0, 0.0, 0.0,
                self.parameters.large_wall_multiple
                    * self.parameters.order_quantity_bonds,
                breakout_support or 0.0, breakout_lower_sells,
            )
        distance = self.parameters.book_safety_distance
        bid_support = sum(
            quantity for price, quantity in tick.bids
            if price + 1e-9 >= tick.bid1 - distance
        )
        ask_supply = sum(
            quantity for price, quantity in tick.asks
            if price <= tick.ask1 + distance + 1e-9
        )
        return MakerDecisionContext(
            reference_price=reference,
            reference_source=source,
            reliable_anchor=reliable,
            spread=max(0.0, tick.ask1 - tick.bid1),
            bid_support_bonds=bid_support,
            ask_supply_bonds=ask_supply,
            wall_threshold_bonds=(
                self.parameters.large_wall_multiple
                * self.parameters.order_quantity_bonds
            ),
            breakout_support_price=breakout_support or 0.0,
            breakout_lower_sell_bonds=breakout_lower_sells,
        )

    def _entry_is_safe(self, edge: float, bid_support_bonds: float) -> bool:
        if edge + 1e-9 >= self.parameters.minimum_active_entry_edge:
            return True
        wall_threshold = (
            self.parameters.large_wall_multiple
            * self.parameters.order_quantity_bonds
        )
        return (
            edge + self.parameters.fair_price_tolerance + 1e-9
                >= self.parameters.minimum_entry_edge
            and bid_support_bonds + 1e-9 >= wall_threshold
        )

    def _sell_is_reasonable(
        self, price: float, context: MakerDecisionContext,
    ) -> bool:
        if price + self.parameters.fair_price_tolerance >= context.reference_price:
            return True
        return (
            context.has_ask_supply
            and price + self.parameters.book_safety_distance + 1e-9
                >= context.reference_price
        )

    def _base_high_sell_is_safe(
        self, price: float, context: MakerDecisionContext,
        policy: MakerPolicyProfile,
    ) -> bool:
        """A base sale needs a future replenishment edge, not merely fair value.

        Extra inventory bought below fair value may exit around fair value for
        turnover.  Base inventory is different: selling it creates a deficit,
        so the sale price must already stand sufficiently above the causal fair
        reference.  A moderate 0.20--0.50 edge additionally needs a thick ask
        wall as replenishment protection; an edge of 0.50 or more is itself the
        safety margin.
        """
        edge = price - context.reference_price
        if edge + 1e-9 >= self.parameters.minimum_active_entry_edge:
            return True
        minimum_edge = (
            self.parameters.minimum_base_high_sell_edge
            if policy.enable_priority_v11_extensions
            else self.parameters.minimum_entry_edge
        )
        return (
            edge + self.parameters.fair_price_tolerance + 1e-9
                >= minimum_edge
            and context.has_ask_supply
        )

    def _refresh_orders(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, *, persist: bool,
    ) -> None:
        anchor = self.analyzer.last_anchor
        context = self._decision_context(tick, account.policy)
        v11 = account.policy.enable_priority_v11_extensions
        confirmed_rise_recent = (
            self._confirmed_rise_is_recent(tick) if v11 else False
        )
        desired_buy: tuple[float, float, float | None] | None = None
        desired_buy_kind = "low_bid_reversion"
        inventory_deficit = max(
            0.0, account.initial_inventory - account.inventory
        )
        in_entry_window = self.analyzer._entry_window(tick.market_time)
        if (
            context.reference_price > 0
            and in_entry_window
            and tick.bid1 > 0 and tick.ask1 > tick.bid1
        ):
            price = tick.bid1
            average_sale_price = None
            maximum_replenishment_price = None
            if inventory_deficit > 1e-9 and account.replenishment_quantity > 1e-9:
                average_sale_price = (
                    account.replenishment_sale_value
                    / account.replenishment_quantity
                )
                maximum_replenishment_price = max(
                    self.parameters.price_tick,
                    average_sale_price - self.parameters.minimum_entry_edge,
                )
                price = min(price, maximum_replenishment_price)
            if (
                v11
                and
                assessment.iron_floor_price is not None
                and assessment.state != "rising"
                and not confirmed_rise_recent
                and tick.bid1 - assessment.iron_floor_price + 1e-9
                    > self.parameters.maximum_iron_floor_entry_premium
            ):
                # A recently observed exceptional support wall defines the
                # attractive low-entry zone even after it falls below Level
                # 1's visible five levels. Do not chase a staircase of small
                # bids far above that remembered safety source.
                price = min(
                    price,
                    assessment.iron_floor_price
                        + self.parameters.maximum_iron_floor_entry_premium,
                )
            if account.fill_mode == "priority":
                improved = price + self.parameters.price_tick
                if (
                    price >= tick.bid1
                    and improved < tick.ask1
                    and (
                        maximum_replenishment_price is None
                        or improved <= maximum_replenishment_price
                    )
                ):
                    price = improved
            price = _floor_to_tick(price, self.parameters.price_tick)
            fair_value_entry_edge = context.reference_price - price
            entry_edge = fair_value_entry_edge
            round_trip_safe = True
            if average_sale_price is not None:
                round_trip_safe = (
                    average_sale_price - price + 1e-9
                    >= self.parameters.minimum_entry_edge
                )
                entry_edge = max(
                    entry_edge,
                    average_sale_price - price,
                )
            entry_safe = self._entry_is_safe(
                entry_edge, context.bid_support_bonds
            )
            if (
                v11
                and
                not entry_safe
                and context.reference_source == "persistent_inside_market"
                and context.spread + 1e-9
                    >= self.parameters.minimum_entry_edge
                and context.has_bid_support
            ):
                # A stable, wide inside market is itself the working space for
                # passive T-making.  Do not cancel the bid merely because the
                # bid-to-midpoint distance is slightly below 0.20.
                entry_safe = True
            if (
                not entry_safe
                and context.breakout_support_strong
                and entry_edge + self.parameters.fair_price_tolerance + 1e-9
                    >= self.parameters.minimum_entry_edge
            ):
                entry_safe = True
            unconfirmed_rapid_requote = (
                v11
                and
                assessment.state == "possible_rise"
                and not confirmed_rise_recent
                and assessment.midpoint_change + 1e-9
                    >= self.parameters.minimum_sweep_jump
                and not context.breakout_support_strong
                and fair_value_entry_edge + 1e-9
                    < self.parameters.minimum_active_entry_edge
            )
            if unconfirmed_rapid_requote:
                # A rapidly lifted bid can sit below the new wide-spread
                # midpoint without being genuinely cheap.  While the rise is
                # only provisional, do not let either that midpoint or an old
                # high-side sale manufacture a passive replenishment edge.
                # A real deep discount remains eligible, and a confirmed
                # rising state is evaluated normally on the updated market.
                entry_safe = False
            capacity = max(0.0, account.maximum_inventory - account.inventory)
            affordable = account.cash / price if price > 0 else 0.0
            if inventory_deficit > 1e-9:
                desired_buy_kind = "inventory_replenish"
                quantity = min(
                    inventory_deficit,
                    self.config.maker_paper.order_quantity_bonds,
                    capacity, affordable,
                )
            else:
                quantity = min(
                    self.config.maker_paper.order_quantity_bonds,
                    capacity, affordable,
                )
            if (
                quantity > 1e-9
                and round_trip_safe
                and entry_safe
            ):
                desired_buy = (price, quantity, None)
        self._replace_buy(
            account, tick, desired_buy, desired_buy_kind, persist=persist
        )

        desired_lots: set[int] = set()
        has_extra_inventory = any(
            lot.entry_price is not None and lot.remaining_quantity > 1e-9
            for lot in account.lots.values()
        )
        minimum_turnover_edge = (
            self.parameters.minimum_passive_turnover_edge
            if v11
            else self.parameters.legacy_queue_passive_turnover_edge
        )
        if context.reference_price > 0 and tick.ask1 > tick.bid1:
            for lot in list(account.lots.values()):
                if lot.remaining_quantity <= 1e-9:
                    continue
                if account.fill_mode == "priority":
                    price = tick.ask1 - self.parameters.price_tick
                else:
                    price = tick.ask1
                if (
                    lot.entry_price is not None
                    and context.breakout_support_strong
                ):
                    support_quote = (
                        context.breakout_support_price
                        - self.parameters.price_tick
                        if account.fill_mode == "priority"
                        else context.breakout_support_price
                    )
                    price = max(price, support_quote)
                if lot.entry_price is None:
                    if has_extra_inventory:
                        # Quote the one standard-sized extra T lot first. Do
                        # not expose the base lot at the same price and let one
                        # market print sell both before the new state can be
                        # reassessed.
                        continue
                    if not self._base_high_sell_is_safe(
                        price, context, account.policy
                    ):
                        continue
                elif (
                    lot.kind == "sweep_tail"
                    and lot.target_price is not None
                    and tick.ask1
                        <= lot.entry_price + self.parameters.price_tick + 1e-9
                ):
                    # Keep the immediate post-sweep exit at the exposed upper
                    # level while the final tail is still the visible ask.
                    # Once the book actually jumps or reprices lower, normal
                    # dynamic exit logic resumes.
                    price = max(price, lot.target_price)
                elif (
                    price - lot.entry_price + 1e-9
                    < minimum_turnover_edge
                    and price - context.reference_price + 1e-9
                    < self.parameters.minimum_fair_value_exit_edge
                ):
                    continue
                elif not self._sell_is_reasonable(price, context):
                    continue
                price = _floor_to_tick(price, self.parameters.price_tick)
                desired_lots.add(lot.db_id)
                existing = account.sell_orders.get(lot.db_id)
                if existing and abs(existing.limit_price - price) < 1e-9:
                    continue
                if existing:
                    self._cancel_order(account, existing, tick, "maker_reprice", persist)
                queue = self._book_quantity(tick, "sell", price)
                if account.fill_mode == "priority" and price < tick.ask1:
                    queue = 0.0
                account.sell_orders[lot.db_id] = self._new_order(
                    account, tick, side="sell", kind="inventory_exit",
                    lot_id=lot.db_id, price=price,
                    quantity=lot.remaining_quantity, queue_ahead=queue,
                    target_price=price, persist=persist,
                )
        for lot_id, order in list(account.sell_orders.items()):
            if lot_id not in desired_lots:
                self._cancel_order(account, order, tick, "exit_context_changed", persist)

    def _confirmed_rise_is_recent(self, tick: ReplayTick) -> bool:
        return (
            self.last_confirmed_rise_trade_ts_ms > 0
            and tick.market_ts_ms - self.last_confirmed_rise_trade_ts_ms
                <= self.parameters.confirmed_rise_grace_seconds * 1000
        )

    def _replace_buy(
        self, account: MakerAccount, tick: ReplayTick,
        desired: tuple[float, float, float | None] | None,
        kind: str, *, persist: bool,
    ) -> None:
        current = account.buy_order
        if desired is None:
            if current:
                self._cancel_order(account, current, tick, "entry_context_changed", persist)
            return
        price, quantity, target = desired
        if (
            current
            and current.kind == kind
            and abs(current.limit_price - price) < 1e-9
            and abs(current.remaining - quantity) < 1e-9
        ):
            return
        if current:
            self._cancel_order(account, current, tick, "maker_reprice", persist)
        queue = self._book_quantity(tick, "buy", price)
        if account.fill_mode == "priority" and price > tick.bid1:
            queue = 0.0
        account.buy_order = self._new_order(
            account, tick, side="buy", kind=kind, lot_id=None,
            price=price, quantity=quantity, queue_ahead=queue,
            target_price=target, persist=persist,
        )

    def _new_order(
        self, account: MakerAccount, tick: ReplayTick, *, side: str,
        kind: str, lot_id: int | None, price: float, quantity: float,
        queue_ahead: float, target_price: float | None, persist: bool,
    ) -> MakerOrder:
        price = _floor_to_tick(price, self.parameters.price_tick)
        if target_price is not None:
            target_price = _floor_to_tick(
                target_price, self.parameters.price_tick
            )
        values = {
            "run_id": self.store.run_id,
            "market_date": account.market_date,
            "strategy_id": account.strategy_id,
            "side": side,
            "status": "open",
            "kind": kind,
            "lot_id": lot_id,
            "created_market_ts_ms": tick.market_ts_ms,
            "updated_market_ts_ms": tick.market_ts_ms,
            "limit_price": price,
            "quantity": quantity,
            "filled_quantity": 0.0,
            "queue_ahead": queue_ahead,
            "target_price": target_price if kind == "sweep_tail" else None,
            "cancel_reason": None,
            "metadata_json": json.dumps({
                "paper_only": True,
                "fill_mode": account.fill_mode,
                "model_id": account.policy.model_id,
                "model_version": account.policy.model_version,
                "quantity_unit": "bond",
            }, separators=(",", ":")),
        }
        order_id = self.store.insert_maker_order(values)
        return MakerOrder(
            order_id, side, kind, lot_id, tick.market_ts_ms, price, quantity,
            queue_ahead=queue_ahead, target_price=target_price,
        )

    def _cancel_order(
        self, account: MakerAccount, order: MakerOrder, tick: ReplayTick,
        reason: str, persist: bool,
    ) -> None:
        self.store.update_maker_order(
            order.db_id, status="cancelled",
            updated_market_ts_ms=tick.market_ts_ms,
            filled_quantity=order.filled_quantity,
            queue_ahead=max(0.0, order.queue_ahead), cancel_reason=reason,
        )
        if order.side == "buy":
            account.buy_order = None
        elif order.lot_id is not None:
            account.sell_orders.pop(order.lot_id, None)

    def _cancel_all_orders(
        self, account: MakerAccount, tick: ReplayTick, reason: str, *, persist: bool,
    ) -> None:
        if account.buy_order is not None:
            self._cancel_order(account, account.buy_order, tick, reason, persist)
        for order in list(account.sell_orders.values()):
            self._cancel_order(account, order, tick, reason, persist)

    def _fill_buy(
        self, account: MakerAccount, tick: ReplayTick, order: MakerOrder,
        quantity: float, received_ts_ns: int, *, kind: str,
        target_price: float | None, persist: bool, reason: str = "passive_buy",
    ) -> None:
        previous_inventory = account.inventory
        account.cash -= quantity * order.limit_price
        account.inventory += quantity
        order.filled_quantity += quantity
        restored = min(
            quantity,
            max(0.0, account.initial_inventory - previous_inventory),
        )
        if restored > 1e-9 and account.replenishment_quantity > 1e-9:
            average_sale = (
                account.replenishment_sale_value
                / account.replenishment_quantity
            )
            account.replenishment_quantity = max(
                0.0, account.replenishment_quantity - restored
            )
            account.replenishment_sale_value = max(
                0.0,
                account.replenishment_sale_value - restored * average_sale,
            )
        components: list[tuple[str, float, float | None, float | None]] = []
        if restored > 1e-9:
            components.append(("base", restored, None, None))
        extra = quantity - restored
        if extra > 1e-9:
            components.append((
                kind,
                extra,
                order.limit_price,
                target_price if kind == "sweep_tail" else None,
            ))
        for lot_kind, lot_quantity, entry_price, lot_target in components:
            lot_id = self.store.insert_maker_lot({
                "run_id": self.store.run_id,
                "market_date": account.market_date,
                "strategy_id": account.strategy_id,
                "kind": lot_kind,
                "opened_market_ts_ms": tick.market_ts_ms,
                "entry_price": entry_price,
                "original_quantity": lot_quantity,
                "remaining_quantity": lot_quantity,
                "target_price": lot_target,
                "status": "open",
                "updated_market_ts_ms": tick.market_ts_ms,
            })
            account.lots[lot_id] = MakerLot(
                lot_id, lot_kind, tick.market_ts_ms, entry_price,
                lot_quantity, lot_quantity, lot_target,
            )
            self._record_fill(
                account, tick, order, lot_id, "buy", order.limit_price,
                lot_quantity, reason, received_ts_ns,
            )
        if order.remaining <= 1e-9:
            self.store.update_maker_order(
                order.db_id, status="filled", updated_market_ts_ms=tick.market_ts_ms,
                filled_quantity=order.filled_quantity,
                queue_ahead=max(0.0, order.queue_ahead),
            )
            if account.buy_order and account.buy_order.db_id == order.db_id:
                account.buy_order = None
        else:
            self.store.update_maker_order(
                order.db_id, status="partial", updated_market_ts_ms=tick.market_ts_ms,
                filled_quantity=order.filled_quantity,
                queue_ahead=max(0.0, order.queue_ahead),
            )

    def _fill_sell(
        self, account: MakerAccount, tick: ReplayTick, order: MakerOrder,
        quantity: float, received_ts_ns: int, *, persist: bool,
        reason: str = "passive_sell",
    ) -> None:
        if order.lot_id is None or order.lot_id not in account.lots:
            return
        lot = account.lots[order.lot_id]
        quantity = min(quantity, lot.remaining_quantity)
        previous_inventory = account.inventory
        account.cash += quantity * order.limit_price
        account.inventory -= quantity
        # A completed high-side execution ends the previous low-price sweep
        # episode. A later displayed discount is then a new causal opportunity.
        account.last_active_entry_price = None
        new_deficit = max(0.0, account.initial_inventory - account.inventory)
        old_deficit = max(0.0, account.initial_inventory - previous_inventory)
        added_deficit = max(0.0, new_deficit - old_deficit)
        if added_deficit > 1e-9:
            account.replenishment_quantity += added_deficit
            account.replenishment_sale_value += added_deficit * order.limit_price
        order.filled_quantity += quantity
        lot.remaining_quantity -= quantity
        closed = lot.remaining_quantity <= 1e-9
        self.store.update_maker_lot(
            lot.db_id,
            remaining_quantity=max(0.0, lot.remaining_quantity),
            status="closed" if closed else "open",
            updated_market_ts_ms=tick.market_ts_ms,
        )
        self._record_fill(
            account, tick, order, lot.db_id, "sell", order.limit_price,
            quantity, reason, received_ts_ns,
        )
        if closed:
            account.lots.pop(lot.db_id, None)
            account.sell_orders.pop(lot.db_id, None)
        if order.remaining <= 1e-9 or closed:
            self.store.update_maker_order(
                order.db_id, status="filled", updated_market_ts_ms=tick.market_ts_ms,
                filled_quantity=order.filled_quantity,
                queue_ahead=max(0.0, order.queue_ahead),
            )
        else:
            self.store.update_maker_order(
                order.db_id, status="partial", updated_market_ts_ms=tick.market_ts_ms,
                filled_quantity=order.filled_quantity,
                queue_ahead=max(0.0, order.queue_ahead),
            )

    def _record_fill(
        self, account: MakerAccount, tick: ReplayTick, order: MakerOrder,
        lot_id: int, side: str, price: float, quantity: float, reason: str,
        received_ts_ns: int,
    ) -> None:
        account.fills += 1
        self.fills_this_run += 1
        self.store.insert_maker_fill({
            "run_id": self.store.run_id,
            "market_date": account.market_date,
            "strategy_id": account.strategy_id,
            "order_id": order.db_id,
            "lot_id": lot_id,
            "market_ts_ms": tick.market_ts_ms,
            "received_ts_ns": received_ts_ns,
            "side": side,
            "price": price,
            "quantity": quantity,
            "fill_reason": reason,
            "reference_tick_id": tick.tick_id,
            "cash_after": account.cash,
            "inventory_after": account.inventory,
        })

    def _mark_account(
        self, account: MakerAccount, tick: ReplayTick, *, persist: bool,
    ) -> None:
        account.last_market_ts_ms = tick.market_ts_ms
        account.last_tick_id = tick.tick_id
        account.last_bid = tick.bid1
        account.last_ask = tick.ask1
        mark = self._inventory_mark(account, tick.bid1, tick.ask1)
        account.trading_pnl = (
            account.cash - account.initial_cash
            + (account.inventory - account.initial_inventory) * mark
        )
        self._persist_account(account)

    @staticmethod
    def _inventory_mark(account: MakerAccount, bid: float, ask: float) -> float:
        if account.inventory > account.initial_inventory:
            return bid
        if account.inventory < account.initial_inventory:
            return ask
        return (bid + ask) / 2 if bid > 0 and ask > 0 else max(bid, ask)

    def _persist_account(self, account: MakerAccount) -> None:
        self.store.upsert_maker_account({
            "market_date": account.market_date,
            "strategy_id": account.strategy_id,
            "fill_mode": account.fill_mode,
            "initial_inventory": account.initial_inventory,
            "maximum_inventory": account.maximum_inventory,
            "initial_cash": account.initial_cash,
            "cash": account.cash,
            "inventory": account.inventory,
            "last_market_ts_ms": account.last_market_ts_ms,
            "last_tick_id": account.last_tick_id,
            "last_bid": account.last_bid,
            "last_ask": account.last_ask,
            "trading_pnl": account.trading_pnl,
            "fills": account.fills,
            "updated_at_utc": _utc_now(),
        })

    def _persist_model_assignment(self, account: MakerAccount) -> None:
        self.store.upsert_maker_model_assignment({
            "market_date": account.market_date,
            "strategy_id": account.strategy_id,
            "bond_code": account.bond_code,
            "model_id": account.policy.model_id,
            "model_version": account.policy.model_version,
            "execution_mode": account.policy.execution_mode,
            "parent_model_id": account.policy.parent_model_id,
            "assigned_at_utc": _utc_now(),
        })

    @staticmethod
    def _book_quantity(tick: ReplayTick, side: str, price: float) -> float:
        levels = tick.bids if side == "buy" else tick.asks
        for level_price, quantity in levels:
            if abs(level_price - price) < 1e-9:
                return quantity
        return 0.0

    def runtime_summary(self) -> dict[str, Any]:
        rows = []
        for account in self.accounts.values():
            rows.append({
                "bond_code": account.bond_code,
                "strategy_id": account.strategy_id,
                "fill_mode": account.fill_mode,
                "model_id": account.policy.model_id,
                "model_version": account.policy.model_version,
                "cash": round(account.cash, 2),
                "inventory": round(account.inventory, 1),
                "pnl": round(account.trading_pnl, 2),
                "fills": account.fills,
                "open_buy_order": account.buy_order is not None,
                "open_sell_orders": len(account.sell_orders),
            })
        return {
            "enabled": self.enabled,
            "bond_codes": [self.bond_code],
            "market_date": self.market_date,
            "fills_this_run": self.fills_this_run,
            "accounts": rows,
        }


class MakerPaperPortfolio:
    """Route one read-only tick stream into independent per-bond maker ledgers."""

    def __init__(self, config: AppConfig, store: SQLiteStore) -> None:
        self.config = config
        self.store = store
        self.engines = {
            code: MakerPaperEngine(
                config, store, bond_code=code,
                strategy_prefix=maker_strategy_prefix(config, code),
            )
            for code in configured_maker_bond_codes(config)
        }

    @property
    def enabled(self) -> bool:
        return self.config.maker_paper.enabled

    @property
    def accounts(self) -> dict[str, MakerAccount]:
        result: dict[str, MakerAccount] = {}
        for engine in self.engines.values():
            result.update(engine.accounts)
        return result

    @property
    def market_date(self) -> str | None:
        return next(
            (engine.market_date for engine in self.engines.values() if engine.market_date),
            None,
        )

    @property
    def fills_this_run(self) -> int:
        return sum(engine.fills_this_run for engine in self.engines.values())

    def rebuild_date(self, market_date: date | str) -> None:
        if not self.enabled or not self.engines:
            return
        first = True
        for engine in self.engines.values():
            engine.rebuild_date(market_date, clear=first)
            first = False

    def on_recorded_tick(self, recorded: RecordedTick) -> None:
        code = recorded.tick.code
        if code == self.config.qmt.stock_code:
            for engine in self.engines.values():
                engine.on_recorded_tick(recorded)
            return
        engine = self.engines.get(code)
        if engine is not None:
            engine.on_recorded_tick(recorded)

    def on_replay_tick(
        self, tick: ReplayTick, *, persist: bool,
        received_ts_ns: int | None = None,
    ) -> None:
        if tick.code == self.config.qmt.stock_code:
            for engine in self.engines.values():
                engine.on_replay_tick(
                    tick, persist=persist, received_ts_ns=received_ts_ns
                )
            return
        engine = self.engines.get(tick.code)
        if engine is not None:
            engine.on_replay_tick(
                tick, persist=persist, received_ts_ns=received_ts_ns
            )

    def runtime_summary(self) -> dict[str, Any]:
        summaries = [engine.runtime_summary() for engine in self.engines.values()]
        return {
            "enabled": self.enabled,
            "bond_codes": list(self.engines),
            "market_date": self.market_date,
            "fills_this_run": sum(
                int(summary["fills_this_run"]) for summary in summaries
            ),
            "accounts": [
                account
                for summary in summaries
                for account in summary["accounts"]
            ],
        }
