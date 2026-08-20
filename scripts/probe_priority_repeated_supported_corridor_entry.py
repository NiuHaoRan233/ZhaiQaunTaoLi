from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from dataclasses import replace
from pathlib import Path

from zhaiquant.config import load_config, maker_underlying_stock_code
from zhaiquant.database import SQLiteStore
from zhaiquant.maker_paper import (
    MakerPaperEngine,
    PRIORITY_POLICY_V137_CANDIDATE,
    PRIORITY_POLICY_V138_CANDIDATE,
    PRIORITY_POLICY_V139_CANDIDATE,
    _floor_to_tick,
    _load_ticks,
)


MINIMUM_CORRIDOR_EDGE = 0.18
MAXIMUM_CORRIDOR_EDGE = 0.50
EVIDENCE_LOOKBACK_SECONDS = 120
MINIMUM_SIDE_BONDS = 1_000.0


POLICIES = {
    policy.model_id: policy
    for policy in (
        PRIORITY_POLICY_V137_CANDIDATE,
        PRIORITY_POLICY_V138_CANDIDATE,
        PRIORITY_POLICY_V139_CANDIDATE,
    )
}


def _bid_quantity_at_price(tick, price: float | None) -> float:
    if price is None:
        return 0.0
    return sum(
        float(bonds)
        for bid_price, bonds in tick.bids
        if abs(float(bid_price) - price) <= 1e-9
    )


def _future_path(
    bond_ticks,
    *,
    signal_ms: int,
    candidate_price: float,
    high_side_price: float,
    visible_wall_price: float | None,
    visible_wall_threshold_bonds: float,
    horizon_seconds: int,
) -> dict:
    future = [
        tick
        for tick in bond_ticks
        if signal_ms < tick.market_ts_ms
        <= signal_ms + horizon_seconds * 1_000
    ]
    sell_hits = [
        tick
        for tick in future
        if tick.trade_bonds > 0
        and tick.inferred_side == "sell"
        and tick.last_price <= candidate_price + 1e-9
    ]
    first_sell = sell_hits[0] if sell_hits else None
    later_high_buys = [
        tick
        for tick in future
        if first_sell is not None
        and tick.market_ts_ms > first_sell.market_ts_ms
        and tick.trade_bonds > 0
        and tick.inferred_side == "buy"
        and tick.last_price + 0.015 + 1e-9 >= high_side_price
    ]
    first_high_buy = later_high_buys[0] if later_high_buys else None
    bids = [tick.bid1 for tick in future if tick.bid1 > 0]
    asks = [tick.ask1 for tick in future if tick.ask1 > 0]
    wall_path = [
        {
            "market_time": tick.market_time,
            "quantity_bonds": _bid_quantity_at_price(
                tick, visible_wall_price,
            ),
        }
        for tick in future
    ]
    first_wall_absent = next(
        (
            item for item in wall_path
            if item["quantity_bonds"] <= 1e-9
        ),
        None,
    )
    first_wall_below_threshold = next(
        (
            item for item in wall_path
            if item["quantity_bonds"] + 1e-9
            < visible_wall_threshold_bonds
        ),
        None,
    )
    wall_quantities = [item["quantity_bonds"] for item in wall_path]
    return {
        "post_hoc_only": True,
        "ticks": len(future),
        "first_sell_at_or_below_candidate": (
            {
                "market_time": first_sell.market_time,
                "price": round(float(first_sell.last_price), 3),
                "quantity_bonds": float(first_sell.trade_bonds),
                "seconds_after_signal": int(
                    (first_sell.market_ts_ms - signal_ms) / 1_000
                ),
            }
            if first_sell is not None
            else None
        ),
        "first_later_buy_at_high_side": (
            {
                "market_time": first_high_buy.market_time,
                "price": round(float(first_high_buy.last_price), 3),
                "quantity_bonds": float(first_high_buy.trade_bonds),
                "seconds_after_signal": int(
                    (first_high_buy.market_ts_ms - signal_ms) / 1_000
                ),
            }
            if first_high_buy is not None
            else None
        ),
        "minimum_bid1": round(min(bids), 3) if bids else None,
        "maximum_bid1": round(max(bids), 3) if bids else None,
        "minimum_ask1": round(min(asks), 3) if asks else None,
        "maximum_ask1": round(max(asks), 3) if asks else None,
        "visible_wall_price": visible_wall_price,
        "minimum_visible_wall_bonds": (
            min(wall_quantities) if wall_quantities else None
        ),
        "maximum_visible_wall_bonds": (
            max(wall_quantities) if wall_quantities else None
        ),
        "first_visible_wall_below_threshold": first_wall_below_threshold,
        "first_visible_wall_absent": first_wall_absent,
    }


def _probe_one(
    config, market_date: str, bond_code: str, policy,
) -> list[dict]:
    source_path = config.storage.database.resolve()
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    raw_signals: list[dict] = []
    try:
        with tempfile.TemporaryDirectory() as temporary:
            replay_config = replace(
                config,
                storage=replace(
                    config.storage,
                    database=Path(temporary) / "corridor-probe.sqlite3",
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
                engine._start_date(market_date)
                ticks = _load_ticks(
                    source,
                    market_date,
                    bond_code,
                    maker_underlying_stock_code(config, bond_code),
                    engine.parameters,
                )
                bond_ticks = [tick for tick in ticks if tick.code == bond_code]
                original_assess_market = engine.analyzer.assess_market
                pending_by_tick: dict[int, dict] = {}
                visible_wall_since_ms: dict[float, int] = {}

                def capture_assessment(tick, previous_close):
                    assessment = original_assess_market(tick, previous_close)
                    account = next(
                        item
                        for item in engine.accounts.values()
                        if item.fill_mode == "priority"
                        and item.purpose == "standard"
                    )
                    context = engine._decision_context(tick, account.policy)
                    current_wall_prices = {
                        round(float(price), 3)
                        for price, bonds in tick.bids
                        if price > 0
                        and bonds + 1e-9 >= context.wall_threshold_bonds
                    }
                    for price in tuple(visible_wall_since_ms):
                        if price not in current_wall_prices:
                            del visible_wall_since_ms[price]
                    for price in current_wall_prices:
                        visible_wall_since_ms.setdefault(
                            price, int(tick.market_ts_ms),
                        )
                    candidate_price = _floor_to_tick(
                        tick.bid1 + engine.parameters.price_tick,
                        engine.parameters.price_tick,
                    )
                    if not (
                        account.customer_base_short_bonds <= 1e-9
                        and account.inventory + 1e-9 >= account.initial_inventory
                        and account.inventory + 1e-9 < account.maximum_inventory
                        and assessment.state
                        in {"stable", "possible_rise", "possible_fall", "falling"}
                        and tick.ask1 > candidate_price > 0
                        and MINIMUM_CORRIDOR_EDGE - 1e-9
                        <= tick.ask1 - candidate_price
                        <= MAXIMUM_CORRIDOR_EDGE + 1e-9
                        and context.bid_support_bonds + 1e-9
                        >= context.wall_threshold_bonds
                    ):
                        return assessment

                    lookback_start_ms = (
                        tick.market_ts_ms - EVIDENCE_LOOKBACK_SECONDS * 1_000
                    )
                    evidence = [
                        event
                        for event in engine.analyzer.trade_evidence
                        if lookback_start_ms
                        <= event.market_ts_ms
                        <= tick.market_ts_ms
                    ]
                    low_sells = [
                        event
                        for event in evidence
                        if event.side == "sell"
                        and event.price
                        <= candidate_price
                        + engine.parameters.price_cluster_width
                        + 1e-9
                    ]
                    high_buys = [
                        event
                        for event in evidence
                        if event.side == "buy"
                        and event.price - candidate_price + 1e-9
                        >= MINIMUM_CORRIDOR_EDGE
                    ]
                    low_sell_bonds = sum(event.bonds for event in low_sells)
                    high_buy_bonds = sum(event.bonds for event in high_buys)
                    if (
                        low_sell_bonds + 1e-9 < MINIMUM_SIDE_BONDS
                        or high_buy_bonds + 1e-9 < MINIMUM_SIDE_BONDS
                    ):
                        return assessment
                    high_side_price = max(event.price for event in high_buys)
                    if high_side_price - candidate_price > MAXIMUM_CORRIDOR_EDGE + 1e-9:
                        return assessment
                    visible_walls = [
                        (price, bonds)
                        for price, bonds in tick.bids
                        if price > 0
                        and tick.bid1 - price
                        <= engine.parameters.maximum_downtrend_wall_anchor_gap
                        + 1e-9
                        and bonds + 1e-9 >= context.wall_threshold_bonds
                    ]
                    visible_wall = (
                        max(visible_walls, key=lambda item: item[0])
                        if visible_walls
                        else None
                    )
                    visible_wall_price = (
                        round(float(visible_wall[0]), 3)
                        if visible_wall is not None
                        else None
                    )

                    pending_by_tick[tick.market_ts_ms] = {
                        "market_date": market_date,
                        "bond_code": bond_code,
                        "signal_market_ts_ms": int(tick.market_ts_ms),
                        "signal_market_time": tick.market_time,
                        "last_price": round(float(tick.last_price), 3),
                        "inferred_side": tick.inferred_side,
                        "trade_bonds": float(tick.trade_bonds),
                        "state": assessment.state,
                        "reference_price": round(
                            float(assessment.reference_price), 3
                        ),
                        "reference_source": assessment.reference_source,
                        "bid1": round(float(tick.bid1), 3),
                        "bid1_bonds": float(tick.bid1_bonds),
                        "ask1": round(float(tick.ask1), 3),
                        "ask1_bonds": float(tick.ask1_bonds),
                        "spread": round(float(context.spread), 3),
                        "bid_support_bonds": float(context.bid_support_bonds),
                        "ask_supply_bonds": float(context.ask_supply_bonds),
                        "visible_concentrated_wall_price": (
                            visible_wall_price
                        ),
                        "visible_concentrated_wall_bonds": (
                            float(visible_wall[1])
                            if visible_wall is not None
                            else 0.0
                        ),
                        "visible_wall_continuous_seconds": (
                            int(
                                (
                                    tick.market_ts_ms
                                    - visible_wall_since_ms[
                                        visible_wall_price
                                    ]
                                )
                                / 1_000
                            )
                            if visible_wall_price is not None
                            else 0
                        ),
                        "visible_wall_threshold_bonds": float(
                            context.wall_threshold_bonds
                        ),
                        "candidate_price": round(float(candidate_price), 3),
                        "candidate_to_ask_edge": round(
                            float(tick.ask1 - candidate_price), 3
                        ),
                        "high_side_price": round(float(high_side_price), 3),
                        "evidence_corridor_edge": round(
                            float(high_side_price - candidate_price), 3
                        ),
                        "prior_120s_low_sell_bonds": float(low_sell_bonds),
                        "prior_120s_low_sell_events": len(low_sells),
                        "prior_120s_high_buy_bonds": float(high_buy_bonds),
                        "prior_120s_high_buy_events": len(high_buys),
                        "recent_buy_bonds": float(assessment.recent_buy_bonds),
                        "recent_sell_bonds": float(assessment.recent_sell_bonds),
                        "midpoint_change": float(assessment.midpoint_change),
                        "short_ask_change": float(assessment.short_ask_change),
                        "inventory_before_decision_bonds": float(
                            account.inventory
                        ),
                    }
                    return assessment

                engine.analyzer.assess_market = capture_assessment
                for tick in ticks:
                    engine.on_replay_tick(tick, persist=True)
                    signal = pending_by_tick.pop(tick.market_ts_ms, None)
                    if signal is None:
                        continue
                    account = next(
                        item
                        for item in engine.accounts.values()
                        if item.fill_mode == "priority"
                        and item.purpose == "standard"
                    )
                    signal["existing_model_buy_after_decision"] = (
                        {
                            "kind": account.buy_order.kind,
                            "limit_price": round(
                                float(account.buy_order.limit_price), 3
                            ),
                            "remaining_bonds": float(account.buy_order.remaining),
                        }
                        if account.buy_order is not None
                        else None
                    )
                    signal["candidate_improves_existing_model"] = (
                        account.buy_order is None
                        or account.buy_order.limit_price
                        + engine.parameters.price_tick
                        + 1e-9
                        < signal["candidate_price"]
                    )
                    raw_signals.append(signal)

                signals: list[dict] = []
                for signal in raw_signals:
                    if (
                        signals
                        and signal["candidate_price"]
                        == signals[-1]["candidate_price"]
                        and signal["signal_market_ts_ms"]
                        - signals[-1]["signal_market_ts_ms"]
                        <= 60_000
                    ):
                        signals[-1]["last_matching_market_time"] = signal[
                            "signal_market_time"
                        ]
                        signals[-1]["matching_frame_count"] += 1
                        continue
                    signal["last_matching_market_time"] = signal[
                        "signal_market_time"
                    ]
                    signal["matching_frame_count"] = 1
                    signals.append(signal)

                for signal in signals:
                    for horizon in (30, 120, 600):
                        signal[f"future_{horizon}s"] = _future_path(
                            bond_ticks,
                            signal_ms=signal["signal_market_ts_ms"],
                            candidate_price=signal["candidate_price"],
                            high_side_price=signal["high_side_price"],
                            visible_wall_price=signal[
                                "visible_concentrated_wall_price"
                            ],
                            visible_wall_threshold_bonds=signal[
                                "visible_wall_threshold_bonds"
                            ],
                            horizon_seconds=horizon,
                        )
                return signals
            finally:
                store.close()
    finally:
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only probe for passive extra entries in a causally repeated "
            "two-sided corridor with visible bid support."
        )
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument("--codes", nargs="+", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--model",
        choices=tuple(POLICIES),
        default=PRIORITY_POLICY_V138_CANDIDATE.model_id,
    )
    args = parser.parse_args()

    config = load_config(args.config)
    policy = POLICIES[args.model]
    signals = [
        signal
        for market_date in args.dates
        for bond_code in args.codes
        for signal in _probe_one(config, market_date, bond_code, policy)
    ]
    output = {
        "model_id": policy.model_id,
        "behavior_changed": False,
        "source_database_opened_readonly": True,
        "probe_semantics": (
            "Signal fields use only current and prior replay state. Future "
            "fields are post-hoc validation and never decision inputs."
        ),
        "thresholds": {
            "minimum_corridor_edge": MINIMUM_CORRIDOR_EDGE,
            "maximum_corridor_edge": MAXIMUM_CORRIDOR_EDGE,
            "evidence_lookback_seconds": EVIDENCE_LOOKBACK_SECONDS,
            "minimum_side_bonds": MINIMUM_SIDE_BONDS,
            "minimum_aggregate_bid_support": "current profile wall threshold",
        },
        "signal_count": len(signals),
        "signals": signals,
    }
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
