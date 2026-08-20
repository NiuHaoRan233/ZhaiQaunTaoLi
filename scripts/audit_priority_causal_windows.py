from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import tempfile
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from zhaiquant.config import load_config, maker_underlying_stock_code
from zhaiquant.database import SQLiteStore
from zhaiquant.maker_paper import (
    MakerPaperEngine,
    PRIORITY_POLICY_V130_CANDIDATE,
    PRIORITY_POLICY_V133_CANDIDATE,
    PRIORITY_POLICY_V134_CANDIDATE,
    PRIORITY_POLICY_V135_CANDIDATE,
    PRIORITY_POLICY_V136_CANDIDATE,
    PRIORITY_POLICY_V137_CANDIDATE,
    PRIORITY_POLICY_V138_CANDIDATE,
    PRIORITY_POLICY_V139_CANDIDATE,
    _load_ticks,
)
from zhaiquant.types import SHANGHAI


POLICIES = {
    policy.model_id: policy
    for policy in (
        PRIORITY_POLICY_V130_CANDIDATE,
        PRIORITY_POLICY_V133_CANDIDATE,
        PRIORITY_POLICY_V134_CANDIDATE,
        PRIORITY_POLICY_V135_CANDIDATE,
        PRIORITY_POLICY_V136_CANDIDATE,
        PRIORITY_POLICY_V137_CANDIDATE,
        PRIORITY_POLICY_V138_CANDIDATE,
        PRIORITY_POLICY_V139_CANDIDATE,
    )
}


def _seconds(value: str) -> int:
    hour, minute, second = (int(part) for part in value[:8].split(":"))
    return hour * 3_600 + minute * 60 + second


def _order_public(order) -> dict | None:
    if order is None:
        return None
    return {
        "order_id": int(order.db_id or 0),
        "side": order.side,
        "kind": order.kind,
        "status": "open",
        "limit_price": round(float(order.limit_price), 3),
        "quantity_bonds": float(order.quantity),
        "filled_quantity_bonds": float(order.filled_quantity),
        "created_market_time": datetime.fromtimestamp(
            order.created_ms / 1_000, SHANGHAI,
        ).strftime("%H:%M:%S"),
    }


def _snapshot(engine: MakerPaperEngine, tick, assessment) -> dict:
    account = next(
        account for account in engine.accounts.values()
        if account.fill_mode == "priority" and account.purpose == "standard"
    )
    context = engine._decision_context(tick, account.policy)
    return {
        "market_ts_ms": int(tick.market_ts_ms),
        "market_time": tick.market_time,
        "tick_id": int(tick.tick_id),
        "last_price": round(float(tick.last_price), 3),
        "bid1": round(float(tick.bid1), 3),
        "bid1_bonds": float(tick.bid1_bonds),
        "ask1": round(float(tick.ask1), 3),
        "ask1_bonds": float(tick.ask1_bonds),
        "bids": [[round(float(price), 3), float(quantity)] for price, quantity in tick.bids],
        "asks": [[round(float(price), 3), float(quantity)] for price, quantity in tick.asks],
        "trade_bonds": float(tick.trade_bonds),
        "transaction_delta": int(tick.transaction_delta),
        "inferred_side": tick.inferred_side,
        "side_confidence": tick.side_confidence,
        "assessment": assessment.public(),
        "decision_context": asdict(context),
        "inventory_before_decision_bonds": float(account.inventory),
        "customer_base_short_before_decision_bonds": max(
            0.0, float(account.initial_inventory - account.inventory),
        ),
        "extra_inventory_before_decision_bonds": max(
            0.0, float(account.inventory - account.initial_inventory),
        ),
        "buy_order_before_decision": _order_public(account.buy_order),
        "sell_orders_before_decision": [
            _order_public(order)
            for order in sorted(account.sell_orders.values(), key=lambda item: item.db_id)
        ],
    }


def _nearest_pair(snapshots: list[dict], market_time: str) -> dict:
    target = _seconds(market_time)
    keys = [_seconds(item["market_time"]) for item in snapshots]
    right = bisect.bisect_right(keys, target)
    before = snapshots[right - 1] if right else None
    after = snapshots[right] if right < len(snapshots) else None
    return {
        "target_time": market_time,
        "seconds_from_previous_bond_frame": (
            target - _seconds(before["market_time"]) if before else None
        ),
        "seconds_to_next_bond_frame": (
            _seconds(after["market_time"]) - target if after else None
        ),
        "previous_or_same_bond_frame": before,
        "next_bond_frame": after,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build causal MiniQMT snapshots for unresolved priority opportunities.",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--diagnostics", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--minimum-edge", type=float, default=0.0)
    parser.add_argument(
        "--turn-id",
        action="append",
        default=[],
        help=(
            "Explicit opportunity turn ID to inspect even when the branch "
            "diagnostic classified it as an overlapping or cross-turn "
            "economic path. May be supplied more than once."
        ),
    )
    parser.add_argument(
        "--model",
        choices=tuple(POLICIES),
        default=PRIORITY_POLICY_V130_CANDIDATE.model_id,
    )
    args = parser.parse_args()

    policy = POLICIES[args.model]

    config = load_config(args.config)
    diagnostics_path = Path(args.diagnostics).resolve()
    payload = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    explicit_turn_ids = set(args.turn_id)
    unresolved = [
        row for row in payload["diagnostics"]
        if row["model_id"] == policy.model_id
        and (
            row["turn_id"] in explicit_turn_ids
            if explicit_turn_ids
            else row["preliminary_status"] == "causal_mode_in_review_required"
        )
        and float(row["maximum_edge"]) + 1e-9 >= args.minimum_edge
    ]
    if not unresolved:
        raise SystemExit("No matching unresolved priority opportunities")
    missing_turn_ids = explicit_turn_ids.difference(
        row["turn_id"] for row in unresolved
    )
    if missing_turn_ids:
        raise SystemExit(
            "Explicit turn IDs not found for the selected model and edge "
            f"threshold: {', '.join(sorted(missing_turn_ids))}"
        )

    market_date = unresolved[0]["market_date"]
    bond_code = unresolved[0]["code"]
    source_path = config.storage.database.resolve()
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    snapshots: list[dict] = []
    try:
        with tempfile.TemporaryDirectory() as temporary:
            replay_config = replace(
                config,
                storage=replace(
                    config.storage,
                    database=Path(temporary) / "causal-window-audit.sqlite3",
                ),
            )
            store = SQLiteStore(replay_config)
            store.start_session()
            try:
                engine = MakerPaperEngine(
                    replay_config,
                    store,
                    bond_code=bond_code,
                    priority_policy=policy,
                )
                # Starting a new market date replaces the analyzer.  Initialize
                # first so the read-only observation hook below survives all
                # replay ticks instead of silently producing empty snapshots.
                engine._start_date(market_date)
                ticks = _load_ticks(
                    source,
                    market_date,
                    bond_code,
                    maker_underlying_stock_code(config, bond_code),
                    engine.parameters,
                )
                original_assess_market = engine.analyzer.assess_market

                def capture_assessment(tick, previous_close):
                    assessment = original_assess_market(tick, previous_close)
                    snapshots.append(_snapshot(engine, tick, assessment))
                    return assessment

                engine.analyzer.assess_market = capture_assessment
                for tick in ticks:
                    engine.on_replay_tick(tick, persist=True)
                if not snapshots:
                    raise RuntimeError(
                        "Replay produced no bond assessment snapshots"
                    )
            finally:
                store.close()
    finally:
        source.close()

    rows = []
    for row in unresolved:
        rows.append({
            "turn_id": row["turn_id"],
            "direction": row["direction"],
            "maximum_edge": float(row["maximum_edge"]),
            "maximum_matchable_hands": int(row["maximum_matchable_hands"]),
            "opening_action_capacity_strict_bonds": int(
                row["opening_action_capacity_strict_bonds"]
            ),
            "best_open_time": row["best_open_time"],
            "best_open_price": float(row["best_open_price"]),
            "best_close_time": row["best_close_time"],
            "best_close_price": float(row["best_close_price"]),
            "open_causal_frames": _nearest_pair(snapshots, row["best_open_time"]),
            "close_causal_frames": _nearest_pair(snapshots, row["best_close_time"]),
        })

    output = {
        "market_date": market_date,
        "bond_code": bond_code,
        "model_id": policy.model_id,
        "source_database": str(source_path),
        "source_database_opened_readonly": True,
        "temporary_replay_database": True,
        "diagnostics_source": str(diagnostics_path),
        "selection_status": (
            "explicit_turn_ids" if explicit_turn_ids
            else "causal_mode_in_review_required"
        ),
        "explicit_turn_ids": sorted(explicit_turn_ids),
        "minimum_edge": args.minimum_edge,
        "selected_rows": len(rows),
        "opportunities": rows,
    }
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
