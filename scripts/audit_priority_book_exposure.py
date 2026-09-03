from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from collections import Counter
from dataclasses import replace
from pathlib import Path

from zhaiquant.config import load_config, maker_underlying_stock_code
from zhaiquant.database import SQLiteStore
from zhaiquant.maker_paper import (
    MakerPaperEngine,
    PRIORITY_POLICY_FIRST_POSITION_V149_R2_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V150_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V21_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V25_R2_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V251_R3_CANDIDATE,
    PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE,
    _load_ticks,
)


POLICIES = {
    "v149r2": PRIORITY_POLICY_FIRST_POSITION_V149_R2_CANDIDATE,
    "v150": PRIORITY_POLICY_FIRST_POSITION_V150_CANDIDATE,
    "v21": PRIORITY_POLICY_FIRST_POSITION_V21_CANDIDATE,
    "v25r2": PRIORITY_POLICY_FIRST_POSITION_V25_R2_CANDIDATE,
    "v251r3": PRIORITY_POLICY_FIRST_POSITION_V251_R3_CANDIDATE,
    "v252r2": PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE,
}


def _front_price(tick, side: str, price_tick: float) -> float:
    # The user-requested historical question is whether an order remains on
    # the visible best level at all, rather than whether it improved that
    # level by the priority branch's usual one tick.  Orders at bid1/ask1 are
    # therefore counted as exposed; only deeper or outside-five orders are
    # classified as resting behind the inside market.
    return tick.bid1 if side == "buy" else tick.ask1


def _book_level(tick, side: str, price: float, price_tick: float) -> str:
    rows = tick.bids if side == "buy" else tick.asks
    for level, (book_price, _) in enumerate(rows, start=1):
        if abs(price - book_price) <= price_tick / 2:
            return str(level)
    if side == "buy":
        if price > tick.bid1:
            return "ahead_of_1"
        if rows and price < rows[-1][0]:
            return "outside_5"
    else:
        if price < tick.ask1:
            return "ahead_of_1"
        if rows and price > rows[-1][0]:
            return "outside_5"
    return "between_levels"


def _observation(account, tick, order, side: str, price_tick: float) -> dict:
    front = _front_price(tick, side, price_tick)
    gap = front - order.limit_price if side == "buy" else order.limit_price - front
    return {
        "market_ts_ms": int(tick.market_ts_ms),
        "market_time": tick.market_time,
        "order_id": int(order.db_id),
        "side": side,
        "kind": order.kind,
        "limit_price": round(float(order.limit_price), 3),
        "price_boundary": round(float(order.price_boundary), 3),
        "front_price": round(float(front), 3),
        "gap_yuan": round(float(gap), 3),
        "book_level": _book_level(
            tick, side, float(order.limit_price), price_tick,
        ),
        "bid1": round(float(tick.bid1), 3),
        "bid1_bonds": float(tick.bid1_bonds),
        "ask1": round(float(tick.ask1), 3),
        "ask1_bonds": float(tick.ask1_bonds),
        "inventory_bonds": float(account.inventory),
        "customer_base_short_bonds": float(account.customer_base_short_bonds),
        "extra_inventory_bonds": float(account.extra_inventory_bonds),
    }


def _orders_view(account, tick, price_tick: float) -> list[dict]:
    orders = []
    if account is not None and account.buy_order is not None:
        orders.append(("buy", account.buy_order))
    if account is not None:
        orders.extend(
            ("sell", order) for order in account.sell_orders.values()
        )
    return [
        {
            "side": side,
            "kind": order.kind,
            "limit_price": round(float(order.limit_price), 3),
            "price_boundary": round(float(order.price_boundary), 3),
            "price_boundary_kind": order.price_boundary_kind,
            "quantity_bonds": float(order.remaining),
            "book_level": _book_level(
                tick, side, float(order.limit_price), price_tick,
            ),
        }
        for side, order in orders
    ]


def _episodes(observations: list[dict]) -> list[dict]:
    episodes: list[dict] = []
    active: dict[tuple[int, str], dict] = {}
    for row in observations:
        key = (row["order_id"], row["side"])
        episode = active.get(key)
        if episode is None:
            episode = {
                "order_id": row["order_id"],
                "side": row["side"],
                "kind": row["kind"],
                "first_market_ts_ms": row["market_ts_ms"],
                "first_market_time": row["market_time"],
                "last_market_ts_ms": row["market_ts_ms"],
                "last_market_time": row["market_time"],
                "frames": 0,
                "maximum_gap_yuan": 0.0,
                "levels": set(),
                "minimum_inventory_bonds": row["inventory_bonds"],
                "maximum_customer_base_short_bonds": (
                    row["customer_base_short_bonds"]
                ),
                "maximum_extra_inventory_bonds": row["extra_inventory_bonds"],
                "examples": [],
            }
            active[key] = episode
            episodes.append(episode)
        episode["last_market_ts_ms"] = row["market_ts_ms"]
        episode["last_market_time"] = row["market_time"]
        episode["frames"] += 1
        episode["maximum_gap_yuan"] = max(
            episode["maximum_gap_yuan"], row["gap_yuan"],
        )
        episode["levels"].add(row["book_level"])
        episode["minimum_inventory_bonds"] = min(
            episode["minimum_inventory_bonds"], row["inventory_bonds"],
        )
        episode["maximum_customer_base_short_bonds"] = max(
            episode["maximum_customer_base_short_bonds"],
            row["customer_base_short_bonds"],
        )
        episode["maximum_extra_inventory_bonds"] = max(
            episode["maximum_extra_inventory_bonds"],
            row["extra_inventory_bonds"],
        )
        if len(episode["examples"]) < 3:
            episode["examples"].append(row)
    for episode in episodes:
        episode["duration_seconds"] = max(
            0.0,
            (episode.pop("last_market_ts_ms")
                - episode.pop("first_market_ts_ms")) / 1_000,
        )
        episode["levels"] = sorted(episode["levels"])
        episode["maximum_gap_yuan"] = round(
            episode["maximum_gap_yuan"], 3,
        )
    return episodes


def _audit_cell(
    config, source, market_date: str, bond_code: str, policy,
    focus_times: set[str],
) -> dict:
    with tempfile.TemporaryDirectory() as temporary:
        replay_config = replace(
            config,
            storage=replace(
                config.storage,
                database=Path(temporary) / "priority-exposure-audit.sqlite3",
            ),
            maker_paper=replace(
                config.maker_paper,
                fill_modes=("priority",),
                super_windfall_enabled=False,
            ),
        )
        store = SQLiteStore(replay_config)
        store.start_session()
        observations: list[dict] = []
        focus_snapshots: list[dict] = []
        try:
            engine = MakerPaperEngine(
                replay_config,
                store,
                bond_code=bond_code,
                priority_policy=policy,
                fill_modes=("priority",),
                include_windfall=False,
            )
            ticks = _load_ticks(
                source,
                market_date,
                bond_code,
                maker_underlying_stock_code(config, bond_code),
                engine.parameters,
            )
            for tick in ticks:
                before_account = None
                before_orders = []
                before_inventory = None
                before_short = None
                if tick.code == bond_code and tick.market_time[:8] in focus_times:
                    before_account = next(
                        (
                            item for item in engine.accounts.values()
                            if item.fill_mode == "priority"
                            and item.purpose == "standard"
                        ),
                        None,
                    )
                    before_orders = _orders_view(
                        before_account, tick, engine.parameters.price_tick,
                    )
                    if before_account is not None:
                        before_inventory = float(before_account.inventory)
                        before_short = float(
                            before_account.customer_base_short_bonds
                        )
                engine.on_replay_tick(tick, persist=True)
                if tick.code != bond_code:
                    continue
                account = next(
                    item for item in engine.accounts.values()
                    if item.fill_mode == "priority" and item.purpose == "standard"
                )
                orders = []
                if account.buy_order is not None:
                    orders.append(("buy", account.buy_order))
                orders.extend(("sell", order) for order in account.sell_orders.values())
                if tick.market_time[:8] in focus_times:
                    focus_snapshots.append({
                        "market_time": tick.market_time,
                        "bid1": round(float(tick.bid1), 3),
                        "bid1_bonds": float(tick.bid1_bonds),
                        "ask1": round(float(tick.ask1), 3),
                        "ask1_bonds": float(tick.ask1_bonds),
                        "trade_bonds": float(tick.trade_bonds),
                        "inferred_side": tick.inferred_side,
                        "before_inventory_bonds": before_inventory,
                        "before_customer_base_short_bonds": before_short,
                        "before_orders": before_orders,
                        "inventory_bonds": float(account.inventory),
                        "customer_base_short_bonds": float(
                            account.customer_base_short_bonds
                        ),
                        "orders": _orders_view(
                            account, tick, engine.parameters.price_tick,
                        ),
                    })
                for side, order in orders:
                    front = _front_price(tick, side, engine.parameters.price_tick)
                    is_behind = (
                        order.limit_price + 1e-9 < front
                        if side == "buy"
                        else order.limit_price > front + 1e-9
                    )
                    if is_behind:
                        observations.append(_observation(
                            account, tick, order, side,
                            engine.parameters.price_tick,
                        ))
        finally:
            store.close()

    episodes = _episodes(observations)
    kinds = Counter((row["side"], row["kind"]) for row in observations)
    return {
        "market_date": market_date,
        "bond_code": bond_code,
        "model_id": policy.model_id,
        "behind_first_position_frames": len(observations),
        "behind_first_position_orders": len(episodes),
        "base_short_frames": sum(
            row["customer_base_short_bonds"] > 1e-9 for row in observations
        ),
        "zero_inventory_frames": sum(
            row["inventory_bonds"] <= 1e-9 for row in observations
        ),
        "frames_by_side_and_kind": {
            f"{side}:{kind}": count
            for (side, kind), count in sorted(kinds.items())
        },
        "focus_snapshots": focus_snapshots,
        "episodes": sorted(
            episodes,
            key=lambda row: (
                row["maximum_customer_base_short_bonds"],
                row["duration_seconds"],
                row["frames"],
            ),
            reverse=True,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of priority orders resting behind the current "
            "one-tick-improved best bid or offer."
        ),
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument("--codes", nargs="+", required=True)
    parser.add_argument("--model", choices=tuple(POLICIES), default="v149r2")
    parser.add_argument("--focus-time", action="append", default=[])
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    config = load_config(args.config)
    source_path = config.storage.database.resolve()
    source = sqlite3.connect(
        f"file:{source_path.as_posix()}?mode=ro", uri=True,
    )
    source.row_factory = sqlite3.Row
    try:
        cells = [
            _audit_cell(
                config, source, market_date, bond_code, POLICIES[args.model],
                set(args.focus_time),
            )
            for market_date in args.dates
            for bond_code in args.codes
        ]
    finally:
        source.close()

    totals = Counter()
    kinds = Counter()
    for cell in cells:
        totals["frames"] += cell["behind_first_position_frames"]
        totals["orders"] += cell["behind_first_position_orders"]
        totals["base_short_frames"] += cell["base_short_frames"]
        totals["zero_inventory_frames"] += cell["zero_inventory_frames"]
        kinds.update(cell["frames_by_side_and_kind"])
    output = {
        "source_database": str(source_path),
        "source_database_opened_readonly": True,
        "temporary_replay_databases": True,
        "model_id": POLICIES[args.model].model_id,
        "totals": dict(totals),
        "frames_by_side_and_kind": dict(sorted(kinds.items())),
        "cells": cells,
    }
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
