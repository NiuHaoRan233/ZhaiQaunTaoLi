from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .config import AppConfig
from .database import SQLiteStore
from .m0 import M0Observation
from .recorder import RecordedTick
from .timeutil import parse_clock, trading_seconds_between
from .types import SHANGHAI, Tick


QMT_BONDS_PER_HAND = 10.0
BOND_ORDER_LOT = 10.0


@dataclass
class SimOrder:
    db_id: int
    strategy_id: str
    model: str
    fill_mode: str
    side: str
    order_type: str
    created_ms: int
    expires_ms: int | None
    limit_price: float | None
    quantity: float
    signal_id: int | None
    queue_remaining: float = 0.0


@dataclass
class SimPosition:
    db_id: int
    quantity: float
    entry_ms: int
    entry_datetime: datetime
    entry_price: float
    entry_stock_price: float
    entry_signal_id: int | None
    max_favorable: float = 0.0
    max_adverse: float = 0.0


@dataclass
class Account:
    strategy_id: str
    model: str
    fill_mode: str
    order: SimOrder | None = None
    position: SimPosition | None = None
    cooldown_until: datetime | None = None
    realized_pnl: float = 0.0
    last_equity_ms: int = 0


class PaperEngine:
    def __init__(self, config: AppConfig, store: SQLiteStore, *, recover: bool = False) -> None:
        self.config = config
        self.store = store
        self.accounts = self._accounts()
        self.fills_this_run = 0
        if recover and self.config.paper.enabled:
            self.recover()

    def _accounts(self) -> dict[str, Account]:
        accounts: dict[str, Account] = {}
        for model in self.config.paper.execution_models:
            if model == "E1":
                account = Account("E1_direct", model, "direct")
                accounts[account.strategy_id] = account
            else:
                for fill_mode in self.config.paper.fill_modes:
                    account = Account(f"{model}_{fill_mode}", model, fill_mode)
                    accounts[account.strategy_id] = account
        return accounts

    def recover(self) -> None:
        now_ms = int(datetime.now(SHANGHAI).timestamp() * 1000)
        state = self.store.recover_paper_state(self.accounts, now_ms)
        seen: set[str] = set()
        for row in state["positions"]:
            strategy_id = str(row["strategy_id"])
            if strategy_id in seen:
                raise RuntimeError(f"Multiple open paper positions found for {strategy_id}")
            seen.add(strategy_id)
            account = self.accounts[strategy_id]
            entry_ms = int(row["entry_market_ts_ms"])
            account.position = SimPosition(
                db_id=int(row["id"]),
                quantity=float(row["quantity"]),
                entry_ms=entry_ms,
                entry_datetime=datetime.fromtimestamp(entry_ms / 1000, tz=SHANGHAI),
                entry_price=float(row["entry_price"]),
                entry_stock_price=float(row["entry_stock_price"]),
                entry_signal_id=row["entry_signal_id"],
                max_favorable=float(row["max_favorable_return"]),
                max_adverse=float(row["max_adverse_return"]),
            )
        for strategy_id, pnl in state["realized"].items():
            self.accounts[strategy_id].realized_pnl = pnl
        for strategy_id, exit_ms in state["last_exits"].items():
            exit_time = datetime.fromtimestamp(exit_ms / 1000, tz=SHANGHAI)
            self.accounts[strategy_id].cooldown_until = exit_time + timedelta(
                minutes=self.config.m0.cooldown_minutes
            )
        if state["cancelled_orders"] or state["positions"]:
            self.store.app_event(
                "warning", "paper_state_recovered", "Recovered paper state after restart",
                {
                    "cancelled_orders": state["cancelled_orders"],
                    "open_positions": len(state["positions"]),
                },
            )

    def on_observation(self, observation: M0Observation) -> None:
        if not self.config.paper.enabled or not observation.valid:
            return
        tick = observation.bond.tick
        for account in self.accounts.values():
            filled_now = self._process_open_order(account, observation)
            self._update_position_mark(account, tick)
            if account.position is not None and not filled_now:
                self._manage_exit(account, observation)
            if account.position is None:
                self._manage_entry(account, observation)
            self._record_equity(account, observation)

    def _manage_entry(self, account: Account, observation: M0Observation) -> None:
        if account.order is not None:
            self._manage_entry_order(account, observation)
            return
        now = observation.bond.tick.market_datetime
        if account.cooldown_until and now < account.cooldown_until:
            return
        if not observation.valid or not observation.warmed or not observation.entry_window:
            return
        if not self.config.m0.trading_enabled:
            return

        if account.model == "E1" and observation.entry_signal:
            self._direct_entry(account, observation)
        elif account.model == "E2" and observation.entry_signal:
            self._create_passive_order(
                account, observation, "buy", observation.bond.tick.bid1,
                self.config.paper.maker_entry_wait_seconds, observation.signal_id,
                "triggered_bid",
            )
        elif account.model in {"E3", "E4"}:
            desired = self._standing_buy_price(observation)
            if desired > 0:
                self._create_passive_order(
                    account, observation, "buy", desired, None, observation.signal_id,
                    "standing_bid",
                )

    def _manage_entry_order(self, account: Account, observation: M0Observation) -> None:
        order = account.order
        if order is None or order.side != "buy":
            return
        now_ms = observation.bond.tick.market_ts_ms
        if order.expires_ms is not None and now_ms >= order.expires_ms:
            self._cancel_order(account, "entry_wait_expired", now_ms)
            return
        if not observation.valid or not observation.warmed or not observation.entry_window:
            self._cancel_order(account, "entry_context_invalid", now_ms)
            return
        if account.model == "E2":
            if observation.buy_discount is None or observation.buy_discount < self.config.m0.entry_discount:
                self._cancel_order(account, "entry_signal_disappeared", now_ms)
            return
        if account.model in {"E3", "E4"}:
            desired = self._standing_buy_price(observation)
            if desired <= 0:
                self._cancel_order(account, "standing_price_unavailable", now_ms)
                return
            current = order.limit_price or 0.0
            maximum = self._maximum_buy_price(observation)
            price_move = abs(desired - current)
            min_move = (
                self.config.paper.standing_reprice_ticks
                * self.config.paper.price_tick
            )
            old_price_exceeds_limit = maximum > 0 and current > maximum + 1e-9
            reprice_interval_elapsed = (
                now_ms - order.created_ms
                >= self.config.paper.standing_reprice_seconds * 1000
            )
            if old_price_exceeds_limit or (
                price_move + 1e-9 >= min_move and reprice_interval_elapsed
            ):
                reason = "standing_risk_reprice" if old_price_exceeds_limit else "standing_reprice"
                self._cancel_order(account, reason, now_ms)
                self._create_passive_order(
                    account, observation, "buy", desired, None, observation.signal_id,
                    "standing_bid",
                )

    def _manage_exit(self, account: Account, observation: M0Observation) -> None:
        position = account.position
        if position is None:
            return
        tick = observation.bond.tick
        local_time = tick.market_datetime.time().replace(tzinfo=None)
        held_seconds = trading_seconds_between(position.entry_datetime, tick.market_datetime)
        hard_reason = None
        if position.entry_datetime.date() != tick.market_datetime.date():
            hard_reason = "overnight_recovery"
        elif not self.config.m0.trading_enabled:
            hard_reason = "model_disabled"
        elif local_time >= parse_clock(self.config.m0.force_exit):
            hard_reason = "force_exit"
        elif held_seconds >= self.config.m0.maximum_holding_minutes * 60:
            hard_reason = "holding_timeout"

        if hard_reason:
            if account.order and account.order.side == "sell":
                self._cancel_order(account, hard_reason, tick.market_ts_ms)
            self._direct_exit(account, observation, hard_reason)
            return

        if account.order and account.order.side == "sell":
            if account.order.expires_ms and tick.market_ts_ms >= account.order.expires_ms:
                self._cancel_order(account, "maker_exit_expired", tick.market_ts_ms)
                self._direct_exit(account, observation, "maker_exit_expired")
            return

        if observation.exit_signal:
            if account.model == "E4":
                self._create_passive_order(
                    account, observation, "sell", tick.ask1,
                    self.config.paper.maker_exit_wait_seconds,
                    position.entry_signal_id, "convergence_ask",
                )
            else:
                self._direct_exit(account, observation, "converged")

    def _process_open_order(self, account: Account, observation: M0Observation) -> bool:
        order = account.order
        if order is None or order.order_type == "market":
            return False
        tick = observation.bond.tick
        change = observation.bond.change
        limit = order.limit_price or 0.0
        crossed = False
        fill_reason = ""

        if order.side == "buy":
            if account.fill_mode == "optimistic":
                crossed = tick.ask1 <= limit or (change.volume_delta > 0 and tick.last_price <= limit)
            elif account.fill_mode == "conservative":
                crossed = (
                    tick.ask1 <= limit - self.config.paper.price_tick
                    or (change.volume_delta > 0 and tick.last_price <= limit - self.config.paper.price_tick)
                )
            else:
                trade_volume = change.volume_delta * QMT_BONDS_PER_HAND if (
                    change.volume_delta > 0 and tick.last_price <= limit
                    and change.inferred_side in {"sell", "unknown"}
                ) else 0.0
                if trade_volume > 0:
                    order.queue_remaining -= trade_volume
                crossed = tick.ask1 <= limit or (trade_volume > 0 and order.queue_remaining <= 0)
            fill_reason = f"passive_buy_{account.fill_mode}"
        else:
            if account.fill_mode == "optimistic":
                crossed = tick.bid1 >= limit or (change.volume_delta > 0 and tick.last_price >= limit)
            elif account.fill_mode == "conservative":
                crossed = (
                    tick.bid1 >= limit + self.config.paper.price_tick
                    or (change.volume_delta > 0 and tick.last_price >= limit + self.config.paper.price_tick)
                )
            else:
                trade_volume = change.volume_delta * QMT_BONDS_PER_HAND if (
                    change.volume_delta > 0 and tick.last_price >= limit
                    and change.inferred_side in {"buy", "unknown"}
                ) else 0.0
                if trade_volume > 0:
                    order.queue_remaining -= trade_volume
                crossed = tick.bid1 >= limit or (trade_volume > 0 and order.queue_remaining <= 0)
            fill_reason = f"passive_sell_{account.fill_mode}"

        if not crossed:
            if account.fill_mode == "queue":
                self.store.update_order(
                    order.db_id,
                    updated_market_ts_ms=tick.market_ts_ms,
                    queue_ahead=max(0.0, order.queue_remaining),
                )
            return False

        price = min(limit, tick.ask1) if order.side == "buy" and tick.ask1 <= limit else limit
        if order.side == "sell" and tick.bid1 >= limit:
            price = max(limit, tick.bid1)
        self._fill_order(account, observation, price, order.quantity, fill_reason)
        return True

    def _direct_entry(self, account: Account, observation: M0Observation) -> None:
        quantity = self._entry_quantity(observation.bond.tick.ask1)
        if quantity <= 0:
            self._record_insufficient_budget(account, observation)
            return
        price = self._book_vwap(observation.bond.tick, "buy", quantity)
        if price is None:
            self.store.app_event(
                "warning", "insufficient_depth", "E1 entry skipped because five-level depth was insufficient",
                {"strategy_id": account.strategy_id, "market_ts_ms": observation.bond.tick.market_ts_ms},
            )
            return
        order = self._create_market_order(
            account, observation, "buy", observation.signal_id, "signal_cross", quantity
        )
        self._fill_order(account, observation, price, order.quantity, "market_buy_book_vwap")

    def _direct_exit(self, account: Account, observation: M0Observation, reason: str) -> None:
        position = account.position
        if position is None:
            return
        price = self._book_vwap(observation.bond.tick, "sell", position.quantity)
        if price is None:
            self.store.app_event(
                "warning", "insufficient_depth", "Paper exit deferred because five-level depth was insufficient",
                {"strategy_id": account.strategy_id, "reason": reason},
            )
            return
        order = self._create_market_order(account, observation, "sell", position.entry_signal_id, reason)
        self._fill_order(account, observation, price, order.quantity, f"market_sell_{reason}")

    def _create_market_order(
        self, account: Account, observation: M0Observation, side: str,
        signal_id: int | None, reason: str, quantity: float | None = None,
    ) -> SimOrder:
        if quantity is None:
            quantity = (
                account.position.quantity
                if side == "sell" and account.position
                else self._entry_quantity(observation.bond.tick.ask1)
            )
        tick = observation.bond.tick
        order_id = self.store.create_order({
            "run_id": self.store.run_id,
            "strategy_id": account.strategy_id,
            "execution_model": account.model,
            "fill_mode": account.fill_mode,
            "signal_id": signal_id,
            "side": side,
            "order_type": "market",
            "status": "open",
            "created_market_ts_ms": tick.market_ts_ms,
            "updated_market_ts_ms": tick.market_ts_ms,
            "expires_market_ts_ms": None,
            "limit_price": None,
            "quantity": quantity,
            "filled_quantity": 0.0,
            "average_fill_price": None,
            "queue_ahead": 0.0,
            "cancel_reason": None,
            "metadata_json": self._order_metadata(reason),
        })
        order = SimOrder(
            order_id, account.strategy_id, account.model, account.fill_mode, side,
            "market", tick.market_ts_ms, None, None, quantity, signal_id,
        )
        account.order = order
        return order

    def _create_passive_order(
        self, account: Account, observation: M0Observation, side: str, price: float,
        wait_seconds: int | None, signal_id: int | None, reason: str,
    ) -> None:
        tick = observation.bond.tick
        expires = tick.market_ts_ms + wait_seconds * 1000 if wait_seconds else None
        queue_ahead = self._queue_at_price(tick, side, price)
        quantity = (
            self._entry_quantity(price)
            if side == "buy"
            else account.position.quantity
        )
        if quantity <= 0:
            self._record_insufficient_budget(account, observation)
            return
        order_id = self.store.create_order({
            "run_id": self.store.run_id,
            "strategy_id": account.strategy_id,
            "execution_model": account.model,
            "fill_mode": account.fill_mode,
            "signal_id": signal_id,
            "side": side,
            "order_type": "limit",
            "status": "open",
            "created_market_ts_ms": tick.market_ts_ms,
            "updated_market_ts_ms": tick.market_ts_ms,
            "expires_market_ts_ms": expires,
            "limit_price": price,
            "quantity": quantity,
            "filled_quantity": 0.0,
            "average_fill_price": None,
            "queue_ahead": queue_ahead,
            "cancel_reason": None,
            "metadata_json": self._order_metadata(reason),
        })
        account.order = SimOrder(
            order_id, account.strategy_id, account.model, account.fill_mode, side,
            "limit", tick.market_ts_ms, expires, price,
            quantity,
            signal_id, queue_ahead,
        )

    def _fill_order(
        self, account: Account, observation: M0Observation,
        price: float, quantity: float, reason: str,
    ) -> None:
        order = account.order
        if order is None:
            raise RuntimeError("Cannot fill an account without an open order")
        tick = observation.bond.tick
        self.store.insert_fill({
            "run_id": self.store.run_id,
            "order_id": order.db_id,
            "strategy_id": account.strategy_id,
            "market_ts_ms": tick.market_ts_ms,
            "received_ts_ns": tick.received_ts_ns,
            "side": order.side,
            "price": price,
            "quantity": quantity,
            "fill_reason": reason,
            "reference_tick_id": observation.bond.tick_id,
        })
        self.fills_this_run += 1
        self.store.update_order(
            order.db_id,
            status="filled",
            updated_market_ts_ms=tick.market_ts_ms,
            filled_quantity=quantity,
            average_fill_price=price,
            queue_ahead=max(0.0, order.queue_remaining),
        )
        account.order = None
        if order.side == "buy":
            position_id = self.store.create_position({
                "run_id": self.store.run_id,
                "strategy_id": account.strategy_id,
                "status": "open",
                "quantity": quantity,
                "entry_market_ts_ms": tick.market_ts_ms,
                "entry_price": price,
                "entry_stock_price": observation.stock.tick.midpoint,
                "entry_signal_id": order.signal_id,
                "exit_market_ts_ms": None,
                "exit_price": None,
                "exit_stock_price": None,
                "exit_reason": None,
                "gross_return": None,
                "max_favorable_return": 0.0,
                "max_adverse_return": 0.0,
                "updated_market_ts_ms": tick.market_ts_ms,
            })
            account.position = SimPosition(
                position_id, quantity, tick.market_ts_ms, tick.market_datetime,
                price, observation.stock.tick.midpoint, order.signal_id,
            )
        else:
            self._close_position(account, observation, price, reason)

    def _close_position(
        self, account: Account, observation: M0Observation, exit_price: float, reason: str,
    ) -> None:
        position = account.position
        if position is None:
            return
        tick = observation.bond.tick
        gross_return = exit_price / position.entry_price - 1.0
        account.realized_pnl += position.quantity * (exit_price - position.entry_price)
        self.store.update_position(
            position.db_id,
            status="closed",
            exit_market_ts_ms=tick.market_ts_ms,
            exit_price=exit_price,
            exit_stock_price=observation.stock.tick.midpoint,
            exit_reason=reason,
            gross_return=gross_return,
            max_favorable_return=position.max_favorable,
            max_adverse_return=position.max_adverse,
            updated_market_ts_ms=tick.market_ts_ms,
        )
        account.position = None
        account.cooldown_until = tick.market_datetime + timedelta(minutes=self.config.m0.cooldown_minutes)

    def _cancel_order(self, account: Account, reason: str, market_ts_ms: int) -> None:
        order = account.order
        if order is None:
            return
        self.store.update_order(
            order.db_id,
            status="cancelled",
            updated_market_ts_ms=market_ts_ms,
            cancel_reason=reason,
            queue_ahead=max(0.0, order.queue_remaining),
        )
        account.order = None

    def _standing_buy_price(self, observation: M0Observation) -> float:
        tick = observation.bond.tick
        maximum = self._maximum_buy_price(observation)
        if maximum <= 0:
            return 0.0
        price_tick = self.config.paper.price_tick
        improved = tick.bid1 + price_tick
        if improved < tick.ask1 and improved <= maximum:
            return round(improved, 6)
        if tick.bid1 <= maximum:
            return tick.bid1
        return round(maximum, 6)

    def _maximum_buy_price(self, observation: M0Observation) -> float:
        if observation.fair_buy is None:
            return 0.0
        price_tick = self.config.paper.price_tick
        maximum = observation.fair_buy / (1.0 + self.config.m0.entry_discount)
        return round(math.floor((maximum + 1e-12) / price_tick) * price_tick, 6)

    def _entry_quantity(self, price: float) -> float:
        if price <= 0:
            return 0.0
        affordable = math.floor(
            self.config.paper.notional_cny / price / BOND_ORDER_LOT
        ) * BOND_ORDER_LOT
        return min(self.config.paper.quantity_bonds, affordable)

    def _record_insufficient_budget(
        self, account: Account, observation: M0Observation,
    ) -> None:
        self.store.app_event(
            "warning", "insufficient_paper_budget",
            "Paper entry skipped because budget was below one bond lot",
            {
                "strategy_id": account.strategy_id,
                "notional_cny": self.config.paper.notional_cny,
                "market_ts_ms": observation.bond.tick.market_ts_ms,
            },
        )

    @staticmethod
    def _order_metadata(reason: str) -> str:
        return json.dumps({
            "reason": reason,
            "quantity_unit": "bond",
            "book_volume_unit": "qmt_hand",
            "bonds_per_qmt_hand": QMT_BONDS_PER_HAND,
        }, separators=(",", ":"))

    @staticmethod
    def _queue_at_price(tick: Tick, side: str, price: float) -> float:
        prices = tick.bid_prices if side == "buy" else tick.ask_prices
        volumes = tick.bid_volumes if side == "buy" else tick.ask_volumes
        for level_price, level_volume in zip(prices, volumes):
            if abs(level_price - price) < 1e-9:
                return level_volume * QMT_BONDS_PER_HAND
        return 0.0

    @staticmethod
    def _book_vwap(tick: Tick, side: str, quantity: float) -> float | None:
        prices = tick.ask_prices if side == "buy" else tick.bid_prices
        volumes = tick.ask_volumes if side == "buy" else tick.bid_volumes
        remaining = quantity
        total_value = 0.0
        for price, volume in zip(prices, volumes):
            if price <= 0 or volume <= 0:
                continue
            available_bonds = volume * QMT_BONDS_PER_HAND
            filled = min(remaining, available_bonds)
            total_value += filled * price
            remaining -= filled
            if remaining <= 1e-9:
                return total_value / quantity
        return None

    def runtime_summary(self, tick: Tick | None) -> dict[str, float | int]:
        open_orders = sum(account.order is not None for account in self.accounts.values())
        positions = [
            account.position for account in self.accounts.values()
            if account.position is not None
        ]
        unrealized_pnl = 0.0
        if tick is not None and tick.bid1 > 0:
            unrealized_pnl = sum(
                position.quantity * (tick.bid1 - position.entry_price)
                for position in positions
            )
        return {
            "open_orders": open_orders,
            "open_positions": len(positions),
            "fills_this_run": self.fills_this_run,
            "realized_pnl": sum(account.realized_pnl for account in self.accounts.values()),
            "unrealized_pnl": unrealized_pnl,
            "order_notional_cny": sum(
                (account.order.limit_price or 0.0) * account.order.quantity
                for account in self.accounts.values()
                if account.order is not None and account.order.side == "buy"
            ),
            "position_notional_cny": sum(
                position.entry_price * position.quantity for position in positions
            ),
        }

    def _update_position_mark(self, account: Account, tick: Tick) -> None:
        position = account.position
        if position is None or tick.bid1 <= 0:
            return
        mark_return = tick.bid1 / position.entry_price - 1.0
        position.max_favorable = max(position.max_favorable, mark_return)
        position.max_adverse = min(position.max_adverse, mark_return)
        self.store.update_position(
            position.db_id,
            max_favorable_return=position.max_favorable,
            max_adverse_return=position.max_adverse,
            updated_market_ts_ms=tick.market_ts_ms,
        )

    def _record_equity(self, account: Account, observation: M0Observation) -> None:
        tick = observation.bond.tick
        if tick.market_ts_ms - account.last_equity_ms < 30_000:
            return
        position = account.position
        unrealized = 0.0
        gross_return = 0.0
        quantity = 0.0
        if position and tick.bid1 > 0:
            unrealized = position.quantity * (tick.bid1 - position.entry_price)
            gross_return = tick.bid1 / position.entry_price - 1.0
            quantity = position.quantity
        self.store.insert_equity({
            "run_id": self.store.run_id,
            "strategy_id": account.strategy_id,
            "market_ts_ms": tick.market_ts_ms,
            "realized_pnl": account.realized_pnl,
            "unrealized_pnl": unrealized,
            "gross_return": gross_return,
            "position_quantity": quantity,
        })
        account.last_equity_ms = tick.market_ts_ms
