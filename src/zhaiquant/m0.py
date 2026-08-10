from __future__ import annotations

import json
import statistics
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import AppConfig
from .database import SQLiteStore
from .recorder import RecordedTick
from .timeutil import parse_clock


@dataclass
class M0Observation:
    observation_id: int
    bond: RecordedTick
    stock: RecordedTick
    conversion_price: float
    parity_mid: float
    premium_mid: float
    reference_premium: float | None
    fair_buy: float | None
    buy_discount: float | None
    fair_sell: float | None
    exit_discount: float | None
    warmup_count: int
    valid: bool
    invalid_reason: str | None
    entry_signal: bool
    exit_signal: bool
    entry_window: bool
    signal_id: int | None = None

    @property
    def warmed(self) -> bool:
        return self.reference_premium is not None


class M0Engine:
    def __init__(
        self, config: AppConfig, store: SQLiteStore, *,
        preload_history: bool = True, synchronize: bool = False,
    ) -> None:
        self.config = config
        self.store = store
        self.latest: dict[str, RecordedTick] = {}
        current_price = config.m0.conversion_price_for(datetime.now().date())
        history = (
            store.recent_premiums(current_price, config.m0.rolling_observations)
            if preload_history else []
        )
        self.premiums: deque[float] = deque(history, maxlen=config.m0.rolling_observations)
        self.active_conversion_price = current_price
        self.entry_condition_active = False
        self.synchronize = synchronize

    def on_tick(self, recorded: RecordedTick) -> M0Observation | None:
        self.latest[recorded.tick.code] = recorded
        if recorded.tick.code != self.config.qmt.bond_code:
            return None
        stock = self.latest.get(self.config.qmt.stock_code)
        if stock is None:
            return None
        return self._evaluate(recorded, stock)

    def _evaluate(self, bond: RecordedTick, stock: RecordedTick) -> M0Observation:
        moment = bond.tick.market_datetime
        conversion_price = self.config.m0.conversion_price_for(moment.date())
        if conversion_price != self.active_conversion_price:
            self.premiums.clear()
            self.active_conversion_price = conversion_price
            self.entry_condition_active = False
            self.store.app_event(
                "warning", "conversion_price_reset", "M0 premium history reset",
                {"conversion_price": conversion_price, "market_date": moment.date().isoformat()},
            )

        valid = True
        invalid_reason = None
        sync_ms = abs(bond.tick.market_ts_ms - stock.tick.market_ts_ms)
        if not bond.tick.valid_book:
            valid, invalid_reason = False, "invalid_bond_book"
        elif not stock.tick.valid_book:
            valid, invalid_reason = False, "invalid_stock_book"
        elif sync_ms > self.config.m0.maximum_sync_seconds * 1000:
            valid, invalid_reason = False, "stale_pair"

        parity_mid = 0.0
        premium_mid = 0.0
        reference = None
        fair_buy = buy_discount = fair_sell = exit_discount = None
        warmup_count = len(self.premiums)
        entry_signal = exit_signal = False
        local_clock = moment.time().replace(tzinfo=None)
        entry_window = (
            parse_clock(self.config.m0.earliest_entry)
            <= local_clock
            <= parse_clock(self.config.m0.latest_entry)
        )

        if valid:
            parity_mid = 100.0 * stock.tick.midpoint / conversion_price
            premium_mid = bond.tick.midpoint / parity_mid - 1.0
            if warmup_count >= self.config.m0.minimum_observations:
                reference = float(statistics.median(self.premiums))
                parity_buy = 100.0 * stock.tick.bid1 / conversion_price
                fair_buy = parity_buy * (1.0 + reference)
                buy_discount = fair_buy / bond.tick.ask1 - 1.0
                parity_sell = 100.0 * stock.tick.ask1 / conversion_price
                fair_sell = parity_sell * (1.0 + reference)
                exit_discount = fair_sell / bond.tick.bid1 - 1.0
                condition = (
                    self.config.m0.trading_enabled
                    and entry_window
                    and buy_discount >= self.config.m0.entry_discount
                )
                entry_signal = condition and not self.entry_condition_active
                self.entry_condition_active = condition
                exit_signal = exit_discount <= self.config.m0.exit_discount
            else:
                self.entry_condition_active = False
            self.premiums.append(premium_mid)
        values = {
            "run_id": self.store.run_id,
            "bond_tick_id": bond.tick_id,
            "stock_tick_id": stock.tick_id,
            "market_ts_ms": bond.tick.market_ts_ms,
            "stock_market_ts_ms": stock.tick.market_ts_ms,
            "conversion_price": conversion_price,
            "parity_mid": parity_mid,
            "premium_mid": premium_mid,
            "reference_premium": reference,
            "fair_buy": fair_buy,
            "buy_discount": buy_discount,
            "fair_sell": fair_sell,
            "exit_discount": exit_discount,
            "warmup_count": warmup_count,
            "is_entry_signal": int(entry_signal),
            "is_exit_signal": int(exit_signal),
            "valid": int(valid),
            "invalid_reason": invalid_reason,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        observation_id = self.store.insert_m0_observation(values)
        signal_id = None
        if entry_signal:
            signal_id = self.store.insert_signal({
                "run_id": self.store.run_id,
                "observation_id": observation_id,
                "signal_type": "entry",
                "market_ts_ms": bond.tick.market_ts_ms,
                "discount": buy_discount,
                "reference_price": fair_buy,
                "executable_price": bond.tick.ask1,
                "details_json": json.dumps({
                    "reference_premium": reference,
                    "parity_mid": parity_mid,
                    "sync_ms": sync_ms,
                    "warmup_count": warmup_count,
                }, ensure_ascii=False, separators=(",", ":")),
            })
        elif self.synchronize:
            self.store.delete_signal(observation_id, "entry")

        return M0Observation(
            observation_id=observation_id,
            bond=bond,
            stock=stock,
            conversion_price=conversion_price,
            parity_mid=parity_mid,
            premium_mid=premium_mid,
            reference_premium=reference,
            fair_buy=fair_buy,
            buy_discount=buy_discount,
            fair_sell=fair_sell,
            exit_discount=exit_discount,
            warmup_count=warmup_count,
            valid=valid,
            invalid_reason=invalid_reason,
            entry_signal=entry_signal,
            exit_signal=exit_signal,
            entry_window=entry_window,
            signal_id=signal_id,
        )
