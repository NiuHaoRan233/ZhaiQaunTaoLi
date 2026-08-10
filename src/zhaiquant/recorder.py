from __future__ import annotations

import json
from dataclasses import dataclass

from .database import SQLiteStore
from .types import Tick, TickChange


@dataclass
class RecordedTick:
    tick_id: int
    tick: Tick
    change: TickChange
    is_new: bool


class TickRecorder:
    def __init__(
        self, store: SQLiteStore, *, deduplicate: bool = True,
        rebuild_changes: bool = False,
    ) -> None:
        self.store = store
        self.deduplicate = deduplicate
        self.rebuild_changes = rebuild_changes
        self.previous: dict[str, tuple[int, Tick]] = {}

    def record(self, tick: Tick) -> RecordedTick:
        previous_item = self.previous.get(tick.code)
        previous_id, previous = previous_item if previous_item else (None, None)
        change = self._change(previous_id, previous, tick)
        existing_id = self.store.find_tick_id(tick) if self.deduplicate else None
        is_new = existing_id is None
        if is_new:
            tick_id = self.store.insert_tick(tick)
        else:
            tick_id = existing_id
        if is_new or self.rebuild_changes:
            self.store.insert_tick_change(tick_id, tick, change)
        self.previous[tick.code] = (tick_id, tick)
        return RecordedTick(tick_id, tick, change, is_new)

    @staticmethod
    def _change(previous_id: int | None, previous: Tick | None, current: Tick) -> TickChange:
        if previous is None or previous.market_datetime.date() != current.market_datetime.date():
            return TickChange(
                previous_tick_id=previous_id,
                volume_delta=0.0,
                amount_delta=0.0,
                transaction_delta=0,
                inferred_side="none",
                side_confidence="none",
                last_price_changed=False,
                best_bid_changed=False,
                best_ask_changed=False,
                book_change_json="{}",
            )

        volume_delta = max(0.0, current.volume - previous.volume)
        amount_delta = max(0.0, current.amount - previous.amount)
        transaction_delta = max(0, current.transaction_count - previous.transaction_count)
        inferred_side = "none"
        confidence = "none"
        if volume_delta > 0:
            if previous.ask1 > 0 and current.last_price >= previous.ask1:
                inferred_side, confidence = "buy", "quote"
            elif previous.bid1 > 0 and current.last_price <= previous.bid1:
                inferred_side, confidence = "sell", "quote"
            elif current.last_price > previous.last_price:
                inferred_side, confidence = "buy", "tick_rule"
            elif current.last_price < previous.last_price:
                inferred_side, confidence = "sell", "tick_rule"
            else:
                inferred_side, confidence = "unknown", "unknown"

        changes: dict[str, object] = {}
        for name, before, after in (
            ("ask_prices", previous.ask_prices, current.ask_prices),
            ("bid_prices", previous.bid_prices, current.bid_prices),
            ("ask_volumes", previous.ask_volumes, current.ask_volumes),
            ("bid_volumes", previous.bid_volumes, current.bid_volumes),
        ):
            level_changes = [
                {"level": index + 1, "before": before[index], "after": after[index]}
                for index in range(5) if before[index] != after[index]
            ]
            if level_changes:
                changes[name] = level_changes

        return TickChange(
            previous_tick_id=previous_id,
            volume_delta=volume_delta,
            amount_delta=amount_delta,
            transaction_delta=transaction_delta,
            inferred_side=inferred_side,
            side_confidence=confidence,
            last_price_changed=current.last_price != previous.last_price,
            best_bid_changed=current.bid1 != previous.bid1,
            best_ask_changed=current.ask1 != previous.ask1,
            book_change_json=json.dumps(changes, ensure_ascii=False, separators=(",", ":")),
        )
