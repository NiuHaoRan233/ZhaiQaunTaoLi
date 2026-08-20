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
    PRIORITY_POLICY_V130_CANDIDATE,
    _load_ticks,
)


def _future_path(bond_ticks, signal_ms: int, horizon_seconds: int) -> dict:
    selected = [
        tick for tick in bond_ticks
        if signal_ms < tick.market_ts_ms <= signal_ms + horizon_seconds * 1_000
    ]
    if not selected:
        return {"ticks": 0}
    bids = [tick.bid1 for tick in selected if tick.bid1 > 0]
    asks = [tick.ask1 for tick in selected if tick.ask1 > 0]
    return {
        "ticks": len(selected),
        "minimum_bid1": round(min(bids), 3) if bids else None,
        "maximum_bid1": round(max(bids), 3) if bids else None,
        "minimum_ask1": round(min(asks), 3) if asks else None,
        "maximum_ask1": round(max(asks), 3) if asks else None,
    }


def _probe_one(config, market_date: str, bond_code: str) -> list[dict]:
    source_path = config.storage.database.resolve()
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    signals: list[dict] = []
    try:
        with tempfile.TemporaryDirectory() as temporary:
            replay_config = replace(
                config,
                storage=replace(
                    config.storage,
                    database=Path(temporary) / "base-short-revalidation.sqlite3",
                ),
            )
            store = SQLiteStore(replay_config)
            store.start_session()
            try:
                engine = MakerPaperEngine(
                    replay_config,
                    store,
                    bond_code=bond_code,
                    priority_policy=PRIORITY_POLICY_V130_CANDIDATE,
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
                tracked_sale_ts_ms = 0
                post_sale_buy_bonds = 0.0
                post_sale_sell_bonds = 0.0
                post_sale_buy_events = 0
                post_sale_sell_events = 0
                post_sale_low_sell_bonds = 0.0
                minimum_post_sale_bid1: float | None = None
                maximum_post_sale_bid1: float | None = None

                def capture_assessment(tick, previous_close):
                    nonlocal tracked_sale_ts_ms
                    nonlocal post_sale_buy_bonds, post_sale_sell_bonds
                    nonlocal post_sale_buy_events, post_sale_sell_events
                    nonlocal post_sale_low_sell_bonds
                    nonlocal minimum_post_sale_bid1, maximum_post_sale_bid1

                    assessment = original_assess_market(tick, previous_close)
                    account = next(
                        account for account in engine.accounts.values()
                        if account.fill_mode == "priority"
                        and account.purpose == "standard"
                    )
                    deficit = account.customer_base_short_bonds
                    if (
                        deficit <= 1e-9
                        or account.replenishment_quantity <= 1e-9
                        or account.last_base_short_sale_ts_ms <= 0
                    ):
                        tracked_sale_ts_ms = 0
                        return assessment

                    average_sale = (
                        account.replenishment_sale_value
                        / account.replenishment_quantity
                    )
                    if account.last_base_short_sale_ts_ms != tracked_sale_ts_ms:
                        tracked_sale_ts_ms = account.last_base_short_sale_ts_ms
                        post_sale_buy_bonds = 0.0
                        post_sale_sell_bonds = 0.0
                        post_sale_buy_events = 0
                        post_sale_sell_events = 0
                        post_sale_low_sell_bonds = 0.0
                        minimum_post_sale_bid1 = None
                        maximum_post_sale_bid1 = None

                    if tick.bid1 > 0:
                        minimum_post_sale_bid1 = (
                            tick.bid1 if minimum_post_sale_bid1 is None
                            else min(minimum_post_sale_bid1, tick.bid1)
                        )
                        maximum_post_sale_bid1 = (
                            tick.bid1 if maximum_post_sale_bid1 is None
                            else max(maximum_post_sale_bid1, tick.bid1)
                        )
                    if tick.trade_bonds > 0 and tick.inferred_side == "buy":
                        post_sale_buy_bonds += tick.trade_bonds
                        post_sale_buy_events += 1
                    elif tick.trade_bonds > 0 and tick.inferred_side == "sell":
                        post_sale_sell_bonds += tick.trade_bonds
                        post_sale_sell_events += 1
                        if (
                            tick.last_price
                            <= average_sale
                                - engine.parameters.minimum_entry_edge + 1e-9
                        ):
                            post_sale_low_sell_bonds += tick.trade_bonds

                    loss_at_ask = tick.ask1 - average_sale
                    causal_gate = (
                        assessment.state == "rising"
                        and tick.inferred_side == "buy"
                        and tick.trade_bonds + 1e-9
                            >= engine.parameters.order_quantity_bonds
                        and tick.ask1 > tick.bid1 > 0
                        and tick.ask1 - tick.bid1
                            <= engine.parameters.maximum_active_turnover_spread
                                + 1e-9
                        and tick.ask1_bonds > 1e-9
                        and tick.last_price
                            + engine.parameters.fair_price_tolerance + 1e-9
                            >= tick.ask1
                        and loss_at_ask
                            <= engine.parameters.maximum_near_flat_exit_loss
                                + 1e-9
                    )
                    if not causal_gate:
                        return assessment

                    age_seconds = int(
                        (tick.market_ts_ms - tracked_sale_ts_ms) / 1_000
                    )
                    context = engine._decision_context(tick, account.policy)
                    signals.append({
                        "market_date": market_date,
                        "bond_code": bond_code,
                        "signal_market_ts_ms": int(tick.market_ts_ms),
                        "signal_market_time": tick.market_time,
                        "base_short_sale_market_ts_ms": int(tracked_sale_ts_ms),
                        "seconds_since_base_short_sale": age_seconds,
                        "deficit_bonds": float(deficit),
                        "average_sale_price": round(average_sale, 3),
                        "bid1": round(float(tick.bid1), 3),
                        "bid1_bonds": float(tick.bid1_bonds),
                        "ask1": round(float(tick.ask1), 3),
                        "ask1_bonds": float(tick.ask1_bonds),
                        "loss_at_ask": round(loss_at_ask, 3),
                        "reference_price": round(
                            float(assessment.reference_price), 3,
                        ),
                        "reference_source": assessment.reference_source,
                        "reference_reliable": context.reliable_anchor,
                        "recent_buy_bonds": float(assessment.recent_buy_bonds),
                        "recent_sell_bonds": float(assessment.recent_sell_bonds),
                        "post_sale_buy_bonds_including_signal": float(
                            post_sale_buy_bonds
                        ),
                        "post_sale_sell_bonds": float(post_sale_sell_bonds),
                        "post_sale_buy_events_including_signal": (
                            post_sale_buy_events
                        ),
                        "post_sale_sell_events": post_sale_sell_events,
                        "post_sale_low_sell_bonds": float(
                            post_sale_low_sell_bonds
                        ),
                        "minimum_post_sale_bid1": (
                            round(minimum_post_sale_bid1, 3)
                            if minimum_post_sale_bid1 is not None else None
                        ),
                        "maximum_post_sale_bid1": (
                            round(maximum_post_sale_bid1, 3)
                            if maximum_post_sale_bid1 is not None else None
                        ),
                        "existing_stop_time_gate": (
                            0 < age_seconds
                            <= account.policy
                                .confirmed_rising_base_short_stop_seconds
                        ),
                        "existing_stop_reference_gate": (
                            assessment.reference_price
                                + engine.parameters.fair_price_tolerance + 1e-9
                                >= average_sale
                        ),
                        "existing_buy_order": (
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
                        ),
                    })
                    return assessment

                engine.analyzer.assess_market = capture_assessment
                for tick in ticks:
                    engine.on_replay_tick(tick, persist=True)

                strategy_id = next(
                    account.strategy_id
                    for account in engine.accounts.values()
                    if account.fill_mode == "priority"
                    and account.purpose == "standard"
                )
                for signal in signals:
                    fill = store.connection.execute(
                        """SELECT market_ts_ms,price,quantity,fill_reason
                             FROM maker_paper_fills
                            WHERE strategy_id=? AND side='buy'
                              AND market_ts_ms>=?
                            ORDER BY market_ts_ms,id LIMIT 1""",
                        (strategy_id, signal["signal_market_ts_ms"]),
                    ).fetchone()
                    signal["existing_model_next_buy"] = (
                        {
                            "market_ts_ms": int(fill["market_ts_ms"]),
                            "price": round(float(fill["price"]), 3),
                            "quantity_bonds": float(fill["quantity"]),
                            "fill_reason": fill["fill_reason"],
                            "seconds_after_signal": int(
                                (fill["market_ts_ms"]
                                 - signal["signal_market_ts_ms"]) / 1_000
                            ),
                        }
                        if fill is not None else None
                    )
                    for horizon in (30, 120, 600):
                        signal[f"future_{horizon}s"] = _future_path(
                            bond_ticks,
                            signal["signal_market_ts_ms"],
                            horizon,
                        )
            finally:
                store.close()
    finally:
        source.close()
    return signals


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only causal probe for revalidating a customer-base short "
            "when a tight rising market returns to its sale price."
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
        "model_id": PRIORITY_POLICY_V130_CANDIDATE.model_id,
        "behavior_changed": False,
        "source_database_opened_readonly": True,
        "probe_semantics": (
            "The signal uses only information available on its frame. "
            "Future paths are post-hoc validation fields and are never inputs."
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
