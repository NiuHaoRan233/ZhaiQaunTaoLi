from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _levels(value: Any, count: int = 5) -> tuple[float, ...]:
    if value is None:
        values: list[Any] = []
    elif hasattr(value, "tolist"):
        values = list(value.tolist())
    elif isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        values = list(value)
    else:
        values = []
    result = [_number(item) for item in values[:count]]
    return tuple(result + [0.0] * (count - len(result)))


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if hasattr(value, "tolist"):
        return json_safe(value.tolist())
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return json_safe(value.item())
        except (TypeError, ValueError):
            pass
    return repr(value)


@dataclass(frozen=True)
class Tick:
    code: str
    market_ts_ms: int
    received_ts_ns: int
    last_price: float
    open_price: float
    high_price: float
    low_price: float
    previous_close: float
    amount: float
    volume: float
    pvolume: float
    tick_volume: float
    stock_status: int
    open_interest: float
    last_settlement_price: float
    settlement_price: float
    transaction_count: int
    pe: float
    ask_prices: tuple[float, ...]
    bid_prices: tuple[float, ...]
    ask_volumes: tuple[float, ...]
    bid_volumes: tuple[float, ...]
    raw_json: str
    snapshot_hash: str

    @classmethod
    def from_qmt(cls, code: str, data: dict[str, Any], received_ts_ns: int | None = None) -> "Tick":
        safe = json_safe(data)
        raw_json = json.dumps(safe, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        market_ts_ms = _integer(data.get("time"))
        last_price = _number(data.get("lastPrice"))
        open_price = _number(data.get("open"))
        high_price = _number(data.get("high"))
        low_price = _number(data.get("low"))
        previous_close = _number(data.get("lastClose"))
        amount = _number(data.get("amount"))
        volume = _number(data.get("volume"))
        pvolume = _number(data.get("pvolume"))
        tick_volume = _number(data.get("tickvol"))
        stock_status = _integer(data.get("stockStatus"))
        open_interest = _number(data.get("openInt"))
        last_settlement_price = _number(data.get("lastSettlementPrice"))
        settlement_price = _number(data.get("settlementPrice"))
        transaction_count = _integer(data.get("transactionNum"))
        pe = _number(data.get("pe"))
        ask_prices = _levels(data.get("askPrice"))
        bid_prices = _levels(data.get("bidPrice"))
        ask_volumes = _levels(data.get("askVol"))
        bid_volumes = _levels(data.get("bidVol"))
        stable_snapshot = (
            code, market_ts_ms, last_price, open_price, high_price, low_price,
            previous_close, amount, volume, pvolume, stock_status, open_interest,
            last_settlement_price, settlement_price, transaction_count, pe,
            ask_prices, bid_prices, ask_volumes, bid_volumes,
        )
        snapshot_hash = hashlib.sha256(
            repr(stable_snapshot).encode("ascii")
        ).hexdigest()
        return cls(
            code=code,
            market_ts_ms=market_ts_ms,
            received_ts_ns=received_ts_ns or time.time_ns(),
            last_price=last_price,
            open_price=open_price,
            high_price=high_price,
            low_price=low_price,
            previous_close=previous_close,
            amount=amount,
            volume=volume,
            pvolume=pvolume,
            tick_volume=tick_volume,
            stock_status=stock_status,
            open_interest=open_interest,
            last_settlement_price=last_settlement_price,
            settlement_price=settlement_price,
            transaction_count=transaction_count,
            pe=pe,
            ask_prices=ask_prices,
            bid_prices=bid_prices,
            ask_volumes=ask_volumes,
            bid_volumes=bid_volumes,
            raw_json=raw_json,
            snapshot_hash=snapshot_hash,
        )

    @property
    def market_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.market_ts_ms / 1000, tz=timezone.utc).astimezone(SHANGHAI)

    @property
    def received_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.received_ts_ns / 1_000_000_000, tz=timezone.utc).astimezone(SHANGHAI)

    @property
    def bid1(self) -> float:
        return self.bid_prices[0]

    @property
    def ask1(self) -> float:
        return self.ask_prices[0]

    @property
    def bid_volume1(self) -> float:
        return self.bid_volumes[0]

    @property
    def ask_volume1(self) -> float:
        return self.ask_volumes[0]

    @property
    def midpoint(self) -> float:
        return (self.bid1 + self.ask1) / 2 if self.valid_book else 0.0

    @property
    def spread(self) -> float:
        return self.ask1 - self.bid1 if self.valid_book else 0.0

    @property
    def valid_book(self) -> bool:
        return self.bid1 > 0 and self.ask1 >= self.bid1


@dataclass
class TickChange:
    previous_tick_id: int | None
    volume_delta: float
    amount_delta: float
    transaction_delta: int
    inferred_side: str
    side_confidence: str
    last_price_changed: bool
    best_bid_changed: bool
    best_ask_changed: bool
    book_change_json: str
