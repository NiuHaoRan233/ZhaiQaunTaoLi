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
    PRIORITY_POLICY_V134_CANDIDATE,
    _load_ticks,
)
from zhaiquant.types import SHANGHAI


def _future_path(
    bond_ticks, *, signal_ms: int, entry_price: float,
    minimum_exit_edge: float, horizon_seconds: int = 600,
) -> dict:
    future = [
        tick for tick in bond_ticks
        if signal_ms < tick.market_ts_ms
        <= signal_ms + horizon_seconds * 1_000
    ]
    later_high_buys = [
        tick for tick in future
        if tick.trade_bonds > 0
        and tick.inferred_side == "buy"
        and tick.last_price - entry_price + 1e-9 >= minimum_exit_edge
    ]
    first_exit = later_high_buys[0] if later_high_buys else None
    bids_30s = [
        tick.bid1 for tick in future
        if tick.market_ts_ms <= signal_ms + 30_000 and tick.bid1 > 0
    ]
    bids_120s = [
        tick.bid1 for tick in future
        if tick.market_ts_ms <= signal_ms + 120_000 and tick.bid1 > 0
    ]
    bids_600s = [tick.bid1 for tick in future if tick.bid1 > 0]
    return {
        "post_hoc_validation_only": True,
        "first_later_high_buy": (
            {
                "market_time": first_exit.market_time,
                "price": round(float(first_exit.last_price), 3),
                "quantity_bonds": float(first_exit.trade_bonds),
                "seconds_after_signal": int(
                    (first_exit.market_ts_ms - signal_ms) / 1_000
                ),
                "gross_edge_per_bond": round(
                    float(first_exit.last_price - entry_price), 3,
                ),
            }
            if first_exit is not None else None
        ),
        "minimum_bid_30s": round(min(bids_30s), 3) if bids_30s else None,
        "minimum_bid_120s": round(min(bids_120s), 3) if bids_120s else None,
        "minimum_bid_600s": round(min(bids_600s), 3) if bids_600s else None,
        "maximum_bid_600s": round(max(bids_600s), 3) if bids_600s else None,
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
                    database=Path(temporary) / "supported-ask-collapse.sqlite3",
                ),
            )
            store = SQLiteStore(replay_config)
            store.start_session()
            try:
                engine = MakerPaperEngine(
                    replay_config,
                    store,
                    bond_code=bond_code,
                    priority_policy=PRIORITY_POLICY_V134_CANDIDATE,
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
                    wall_threshold = context.wall_threshold_bonds
                    visible_walls = [
                        (price, quantity)
                        for price, quantity in tick.bids
                        if price > 0
                        and tick.bid1 - price
                            <= engine.parameters.maximum_downtrend_wall_anchor_gap
                                + 1e-9
                        and quantity + 1e-9 >= wall_threshold
                    ]
                    visible_keys = {
                        round(price, 6) for price, _ in visible_walls
                    }
                    for price in list(wall_first_seen):
                        if price not in visible_keys:
                            del wall_first_seen[price]
                    for price, _ in visible_walls:
                        wall_first_seen.setdefault(
                            round(price, 6), tick.market_ts_ms,
                        )
                    if not visible_walls:
                        return assessment
                    wall_price, wall_bonds = max(
                        visible_walls, key=lambda item: item[0],
                    )
                    prior_reference = engine.last_intraday_working_reference
                    prior_reference_age_ms = (
                        tick.market_ts_ms
                        - engine.last_intraday_working_reference_ts_ms
                    )
                    lookback_start_ms = tick.market_ts_ms - 120_000
                    prior_high_buys = [
                        event for event in engine.analyzer.trade_evidence
                        if lookback_start_ms <= event.market_ts_ms
                            < tick.market_ts_ms
                        and event.side == "buy"
                        and event.price - tick.ask1
                            + engine.parameters.fair_price_tolerance + 1e-9
                            >= engine.parameters.minimum_entry_edge
                    ]
                    falling_reentry_active = (
                        account.policy.enable_falling_profitable_bid_exit
                        and account.last_falling_profitable_exit_price > 0
                        and tick.market_ts_ms
                            - account.last_falling_profitable_exit_ts_ms
                            <= account.policy
                                .falling_profitable_reentry_cooldown_seconds
                                    * 1_000
                    )
                    causal_gate = (
                        account.customer_base_short_bonds <= 1e-9
                        and account.inventory + 1e-9
                            >= account.initial_inventory
                        and account.inventory + 1e-9
                            < account.maximum_inventory
                        and assessment.state in {"possible_fall", "falling"}
                        and not engine._confirmed_rise_is_recent(
                            tick, account.policy,
                        )
                        and not falling_reentry_active
                        and tick.ask1 > tick.bid1 > 0
                        and tick.ask1_bonds + 1e-9
                            >= engine.parameters.order_quantity_bonds
                        and tick.ask1 - wall_price
                            <= engine.parameters
                                .maximum_downtrend_wall_entry_premium + 1e-9
                        and prior_reference > 0
                        and 0 <= prior_reference_age_ms
                            <= engine.parameters
                                .market_temperature_window_seconds * 1_000
                        and prior_reference - tick.ask1
                            + engine.parameters.fair_price_tolerance + 1e-9
                            >= engine.parameters.minimum_entry_edge
                        and sum(event.bonds for event in prior_high_buys)
                            + 1e-9
                            >= engine.parameters.order_quantity_bonds
                    )
                    if not causal_gate:
                        return assessment
                    pending_by_tick[tick.market_ts_ms] = {
                        "market_date": market_date,
                        "bond_code": bond_code,
                        "signal_market_ts_ms": int(tick.market_ts_ms),
                        "signal_market_time": tick.market_time,
                        "state": assessment.state,
                        "state_score": int(assessment.state_score),
                        "current_reference_price": round(
                            float(context.reference_price), 3,
                        ),
                        "current_reference_source": context.reference_source,
                        "prior_working_reference_price": round(
                            float(prior_reference), 3,
                        ),
                        "prior_working_reference_age_seconds": int(
                            prior_reference_age_ms / 1_000
                        ),
                        "bid1": round(float(tick.bid1), 3),
                        "bid1_bonds": float(tick.bid1_bonds),
                        "ask1": round(float(tick.ask1), 3),
                        "ask1_bonds": float(tick.ask1_bonds),
                        "spread": round(float(context.spread), 3),
                        "prior_reference_discount": round(
                            float(prior_reference - tick.ask1), 3,
                        ),
                        "visible_wall_price": round(float(wall_price), 3),
                        "visible_wall_bonds": float(wall_bonds),
                        "ask_premium_to_wall": round(
                            float(tick.ask1 - wall_price), 3,
                        ),
                        "visible_wall_seconds": int(
                            (
                                tick.market_ts_ms
                                - wall_first_seen[round(wall_price, 6)]
                            ) / 1_000
                        ),
                        "prior_120s_high_buy_bonds": float(sum(
                            event.bonds for event in prior_high_buys
                        )),
                        "prior_120s_high_buy_events": len(prior_high_buys),
                        "most_recent_high_buy_time": (
                            datetime.fromtimestamp(
                                prior_high_buys[-1].market_ts_ms / 1_000,
                                SHANGHAI,
                            ).strftime("%H:%M:%S")
                            if prior_high_buys else None
                        ),
                        "most_recent_high_buy_price": (
                            round(float(prior_high_buys[-1].price), 3)
                            if prior_high_buys else None
                        ),
                        "inventory_before_decision_bonds": float(
                            account.inventory
                        ),
                        "existing_buy_before_decision": (
                            {
                                "kind": account.buy_order.kind,
                                "limit_price": round(
                                    float(account.buy_order.limit_price), 3,
                                ),
                                "created_market_ts_ms": int(
                                    account.buy_order.created_ms
                                ),
                            }
                            if account.buy_order is not None else None
                        ),
                        "assessment_evidence": list(assessment.evidence),
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
                    signal["model_inventory_after_decision_bonds"] = float(
                        account.inventory
                    )
                    signal["model_buy_after_decision"] = (
                        {
                            "kind": account.buy_order.kind,
                            "limit_price": round(
                                float(account.buy_order.limit_price), 3,
                            ),
                        }
                        if account.buy_order is not None else None
                    )
                    raw_signals.append(signal)

                signals: list[dict] = []
                for signal in raw_signals:
                    if (
                        signals
                        and signal["market_date"] == signals[-1]["market_date"]
                        and signal["bond_code"] == signals[-1]["bond_code"]
                        and signal["visible_wall_price"]
                            == signals[-1]["visible_wall_price"]
                        and signal["ask1"] == signals[-1]["ask1"]
                        and signal["signal_market_ts_ms"]
                            - signals[-1]["signal_market_ts_ms"] <= 15_000
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
                    signal["future_600s"] = _future_path(
                        bond_ticks,
                        signal_ms=signal["signal_market_ts_ms"],
                        entry_price=signal["ask1"],
                        minimum_exit_edge=(
                            engine.parameters.minimum_entry_edge
                        ),
                    )
                return signals
            finally:
                store.close()
    finally:
        source.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only probe for a tight low ask near a visible bid wall "
            "after a causally established higher working range."
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
        "model_id": PRIORITY_POLICY_V134_CANDIDATE.model_id,
        "behavior_changed": False,
        "source_database_opened_readonly": True,
        "temporary_replay_database": True,
        "selection_is_causal": True,
        "future_fields_are_post_hoc_validation_only": True,
        "dates": args.dates,
        "codes": args.codes,
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
