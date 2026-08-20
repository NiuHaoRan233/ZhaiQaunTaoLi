from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from zhaiquant.config import load_config, maker_underlying_stock_code
from zhaiquant.database import SQLiteStore
from zhaiquant.maker_paper import (
    MakerPaperEngine,
    PRIORITY_POLICY_V133_CANDIDATE,
    _floor_to_tick,
    _load_ticks,
)
from zhaiquant.types import SHANGHAI


def _future_validation(
    bond_ticks, *, signal_ms: int, candidate_price: float,
    initial_ask: float, horizon_seconds: int = 600,
) -> dict:
    future = [
        tick for tick in bond_ticks
        if signal_ms < tick.market_ts_ms
        <= signal_ms + horizon_seconds * 1_000
    ]
    sell_hits = [
        tick for tick in future
        if tick.trade_bonds > 0
        and tick.inferred_side == "sell"
        and tick.last_price <= candidate_price + 1e-9
    ]
    first_sell = sell_hits[0] if sell_hits else None
    later_buys = [
        tick for tick in future
        if first_sell is not None
        and tick.market_ts_ms > first_sell.market_ts_ms
        and tick.trade_bonds > 0
        and tick.inferred_side == "buy"
        and tick.last_price + 1e-9 >= initial_ask
    ]
    first_buy = later_buys[0] if later_buys else None
    bids = [tick.bid1 for tick in future if tick.bid1 > 0]
    return {
        "post_hoc_only": True,
        "first_sell_hit": (
            {
                "market_time": first_sell.market_time,
                "price": round(float(first_sell.last_price), 3),
                "quantity_bonds": float(first_sell.trade_bonds),
                "seconds_after_signal": int(
                    (first_sell.market_ts_ms - signal_ms) / 1_000
                ),
            }
            if first_sell is not None else None
        ),
        "first_later_buy_at_or_above_initial_ask": (
            {
                "market_time": first_buy.market_time,
                "price": round(float(first_buy.last_price), 3),
                "quantity_bonds": float(first_buy.trade_bonds),
                "seconds_after_signal": int(
                    (first_buy.market_ts_ms - signal_ms) / 1_000
                ),
            }
            if first_buy is not None else None
        ),
        "minimum_future_bid1": round(min(bids), 3) if bids else None,
        "maximum_future_bid1": round(max(bids), 3) if bids else None,
    }


def _probe_one(config, market_date: str, bond_code: str) -> list[dict]:
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
                    database=Path(temporary) / "supported-spread-probe.sqlite3",
                ),
            )
            store = SQLiteStore(replay_config)
            store.start_session()
            try:
                engine = MakerPaperEngine(
                    replay_config,
                    store,
                    bond_code=bond_code,
                    priority_policy=PRIORITY_POLICY_V133_CANDIDATE,
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
                wall_first_seen: dict[float, int] = {}

                def capture_assessment(tick, previous_close):
                    assessment = original_assess_market(tick, previous_close)
                    account = next(
                        item for item in engine.accounts.values()
                        if item.fill_mode == "priority"
                        and item.purpose == "standard"
                    )
                    context = engine._decision_context(tick, account.policy)
                    threshold = context.wall_threshold_bonds
                    visible_walls = [
                        (price, quantity)
                        for price, quantity in tick.bids
                        if price > 0
                        and tick.bid1 - price
                        <= engine.parameters.maximum_downtrend_wall_anchor_gap
                            + 1e-9
                        and quantity + 1e-9 >= threshold
                    ]
                    visible_prices = {round(price, 3) for price, _ in visible_walls}
                    for price in list(wall_first_seen):
                        if price not in visible_prices:
                            del wall_first_seen[price]
                    for price, _ in visible_walls:
                        wall_first_seen.setdefault(
                            round(price, 3), tick.market_ts_ms,
                        )

                    if not visible_walls:
                        return assessment
                    wall_price, wall_bonds = max(
                        visible_walls, key=lambda item: item[0],
                    )
                    candidate_price = _floor_to_tick(
                        min(
                            tick.bid1 + engine.parameters.price_tick,
                            wall_price
                                + engine.parameters
                                    .maximum_downtrend_wall_entry_premium,
                        ),
                        engine.parameters.price_tick,
                    )
                    confirmed_rise = engine._confirmed_rise_is_recent(
                        tick, account.policy,
                    )
                    causal_gate = (
                        account.customer_base_short_bonds <= 1e-9
                        and account.inventory + 1e-9
                            >= account.initial_inventory
                        and account.inventory + 1e-9
                            < account.maximum_inventory
                        and assessment.state in {"possible_fall", "falling"}
                        and not confirmed_rise
                        and tick.ask1 > candidate_price > 0
                        and context.spread + 1e-9
                            >= engine.parameters.minimum_entry_edge
                        and context.spread
                            < engine.parameters.minimum_active_entry_edge
                                - 1e-9
                        and tick.ask1 - candidate_price
                            + engine.parameters.fair_price_tolerance + 1e-9
                            >= engine.parameters.minimum_entry_edge
                        and candidate_price - wall_price
                            <= engine.parameters
                                .maximum_downtrend_wall_entry_premium + 1e-9
                    )
                    if not causal_gate:
                        return assessment

                    lookback_start_ms = tick.market_ts_ms - 120_000
                    prior_high_buys = [
                        event for event in engine.analyzer.trade_evidence
                        if lookback_start_ms <= event.market_ts_ms
                        < tick.market_ts_ms
                        and event.side == "buy"
                        and event.price
                            + engine.parameters.fair_price_tolerance + 1e-9
                            >= tick.ask1
                    ]
                    prior_low_sells = [
                        event for event in engine.analyzer.trade_evidence
                        if lookback_start_ms <= event.market_ts_ms
                        < tick.market_ts_ms
                        and event.side == "sell"
                        and event.price <= candidate_price + 1e-9
                    ]

                    pending_by_tick[tick.market_ts_ms] = {
                        "market_date": market_date,
                        "bond_code": bond_code,
                        "signal_market_ts_ms": int(tick.market_ts_ms),
                        "signal_market_time": tick.market_time,
                        "state": assessment.state,
                        "reference_price": round(
                            float(assessment.reference_price), 3,
                        ),
                        "reference_source": assessment.reference_source,
                        "recent_buy_bonds": float(assessment.recent_buy_bonds),
                        "recent_sell_bonds": float(assessment.recent_sell_bonds),
                        "bid1": round(float(tick.bid1), 3),
                        "bid1_bonds": float(tick.bid1_bonds),
                        "ask1": round(float(tick.ask1), 3),
                        "ask1_bonds": float(tick.ask1_bonds),
                        "spread": round(float(context.spread), 3),
                        "candidate_price": round(float(candidate_price), 3),
                        "candidate_to_ask_edge": round(
                            float(tick.ask1 - candidate_price), 3,
                        ),
                        "visible_wall_price": round(float(wall_price), 3),
                        "visible_wall_bonds": float(wall_bonds),
                        "candidate_premium_to_wall": round(
                            float(candidate_price - wall_price), 3,
                        ),
                        "visible_wall_seconds": int(
                            (
                                tick.market_ts_ms
                                - wall_first_seen[round(wall_price, 3)]
                            ) / 1_000
                        ),
                        "prior_120s_high_buy_bonds": float(sum(
                            event.bonds for event in prior_high_buys
                        )),
                        "prior_120s_high_buy_events": len(prior_high_buys),
                        "most_recent_prior_high_buy_time": (
                            datetime.fromtimestamp(
                                prior_high_buys[-1].market_ts_ms / 1_000,
                                SHANGHAI,
                            ).strftime("%H:%M:%S")
                            if prior_high_buys else None
                        ),
                        "most_recent_prior_high_buy_price": (
                            round(float(prior_high_buys[-1].price), 3)
                            if prior_high_buys else None
                        ),
                        "prior_120s_low_sell_bonds": float(sum(
                            event.bonds for event in prior_low_sells
                        )),
                        "prior_120s_low_sell_events": len(prior_low_sells),
                        "remembered_iron_floor_price": (
                            round(float(assessment.iron_floor_price), 3)
                            if assessment.iron_floor_price is not None else None
                        ),
                        "remembered_iron_floor_bonds": float(
                            assessment.iron_floor_bonds
                        ),
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
                        item for item in engine.accounts.values()
                        if item.fill_mode == "priority"
                        and item.purpose == "standard"
                    )
                    signal["existing_model_buy_after_decision"] = (
                        {
                            "kind": account.buy_order.kind,
                            "limit_price": round(
                                float(account.buy_order.limit_price), 3,
                            ),
                            "remaining_bonds": float(
                                account.buy_order.remaining
                            ),
                        }
                        if account.buy_order is not None else None
                    )
                    signal["candidate_improves_existing_model"] = (
                        account.buy_order is None
                        or account.buy_order.limit_price
                            + engine.parameters.price_tick + 1e-9
                            < signal["candidate_price"]
                    )
                    raw_signals.append(signal)

                # Collapse consecutive three-second frames that describe the
                # same wall-backed opportunity.  The earliest causal frame is
                # retained; future fields are validation only.
                signals: list[dict] = []
                for signal in raw_signals:
                    if (
                        signals
                        and signal["bond_code"] == signals[-1]["bond_code"]
                        and signal["visible_wall_price"]
                            == signals[-1]["visible_wall_price"]
                        and signal["candidate_price"]
                            == signals[-1]["candidate_price"]
                        and signal["signal_market_ts_ms"]
                            - signals[-1]["signal_market_ts_ms"] <= 60_000
                    ):
                        signals[-1]["last_matching_market_time"] = signal[
                            "signal_market_time"
                        ]
                        signals[-1]["matching_frame_count"] += 1
                        signals[-1]["maximum_wall_seconds"] = max(
                            signals[-1]["maximum_wall_seconds"],
                            signal["visible_wall_seconds"],
                        )
                        continue
                    signal["last_matching_market_time"] = signal[
                        "signal_market_time"
                    ]
                    signal["matching_frame_count"] = 1
                    signal["maximum_wall_seconds"] = signal[
                        "visible_wall_seconds"
                    ]
                    signals.append(signal)

                for signal in signals:
                    signal["future_600s"] = _future_validation(
                        bond_ticks,
                        signal_ms=signal["signal_market_ts_ms"],
                        candidate_price=signal["candidate_price"],
                        initial_ask=signal["ask1"],
                    )
                return signals
            finally:
                store.close()
    finally:
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only no-behavior probe for falling/possible-fall extra "
            "entries backed by a currently visible concentrated bid wall."
        ),
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument("--codes", nargs="+", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    config = load_config(args.config)
    signals = [
        signal
        for market_date in args.dates
        for bond_code in args.codes
        for signal in _probe_one(config, market_date, bond_code)
    ]
    output = {
        "model_id": PRIORITY_POLICY_V133_CANDIDATE.model_id,
        "behavior_changed": False,
        "source_database_opened_readonly": True,
        "probe_semantics": (
            "Signal fields use only the current and prior causal replay "
            "state. Future fields are post-hoc validation and never inputs."
        ),
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
