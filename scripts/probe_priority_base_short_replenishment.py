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
    seen: set[tuple[int, str]] = set()
    try:
        with tempfile.TemporaryDirectory() as temporary:
            replay_config = replace(
                config,
                storage=replace(
                    config.storage,
                    database=Path(temporary) / "base-short-probe.sqlite3",
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

                def capture_assessment(tick, previous_close):
                    assessment = original_assess_market(tick, previous_close)
                    account = next(
                        account for account in engine.accounts.values()
                        if account.fill_mode == "priority"
                        and account.purpose == "standard"
                    )
                    deficit = max(
                        0.0, account.initial_inventory - account.inventory,
                    )
                    if not (
                        deficit > 1e-9
                        and account.replenishment_quantity > 1e-9
                        and account.last_base_short_sale_ts_ms > 0
                        and tick.bid1 > 0
                        and tick.ask1 > tick.bid1
                    ):
                        return assessment
                    average_sale = (
                        account.replenishment_sale_value
                        / account.replenishment_quantity
                    )
                    categories: list[str] = []
                    if (
                        average_sale - tick.bid1 + 1e-9 >= 0.18
                        and tick.bid1_bonds + 1e-9
                            >= engine.parameters.order_quantity_bonds
                    ):
                        categories.append("profitable_visible_bid_018")
                    if (
                        assessment.state == "rising"
                        and tick.ask1 - tick.bid1
                            <= engine.parameters.maximum_active_turnover_spread
                                + 1e-9
                        and tick.ask1_bonds > 1e-9
                        and tick.ask1 - average_sale <= 0.05 + 1e-9
                    ):
                        categories.append("rising_tight_ask_loss_le_005")
                    for category in categories:
                        key = (account.last_base_short_sale_ts_ms, category)
                        if key in seen:
                            continue
                        seen.add(key)
                        context = engine._decision_context(tick, account.policy)
                        signals.append({
                            "market_date": market_date,
                            "bond_code": bond_code,
                            "category": category,
                            "signal_market_ts_ms": int(tick.market_ts_ms),
                            "signal_market_time": tick.market_time,
                            "base_short_sale_market_ts_ms": int(
                                account.last_base_short_sale_ts_ms
                            ),
                            "seconds_since_base_short_sale": int(
                                (tick.market_ts_ms
                                 - account.last_base_short_sale_ts_ms) / 1_000
                            ),
                            "deficit_bonds": float(deficit),
                            "average_sale_price": round(average_sale, 3),
                            "bid1": round(float(tick.bid1), 3),
                            "bid1_bonds": float(tick.bid1_bonds),
                            "ask1": round(float(tick.ask1), 3),
                            "ask1_bonds": float(tick.ask1_bonds),
                            "profit_at_bid": round(average_sale - tick.bid1, 3),
                            "loss_at_ask": round(tick.ask1 - average_sale, 3),
                            "reference_price": round(
                                float(assessment.reference_price), 3,
                            ),
                            "reference_source": assessment.reference_source,
                            "reference_reliable": context.reliable_anchor,
                            "state": assessment.state,
                            "recent_buy_bonds": assessment.recent_buy_bonds,
                            "recent_sell_bonds": assessment.recent_sell_bonds,
                            "existing_buy_order": (
                                {
                                    "kind": account.buy_order.kind,
                                    "limit_price": account.buy_order.limit_price,
                                    "remaining_bonds": account.buy_order.remaining,
                                }
                                if account.buy_order is not None else None
                            ),
                        })
                    return assessment

                engine.analyzer.assess_market = capture_assessment
                for tick in ticks:
                    engine.on_replay_tick(tick, persist=True)

                for signal in signals:
                    fill = store.connection.execute(
                        """SELECT f.market_ts_ms,f.price,f.quantity,f.fill_reason
                             FROM maker_paper_fills f
                            WHERE f.strategy_id=? AND f.side='buy'
                              AND f.market_ts_ms>=?
                            ORDER BY f.market_ts_ms,f.id LIMIT 1""",
                        (
                            next(
                                account.strategy_id
                                for account in engine.accounts.values()
                                if account.fill_mode == "priority"
                                and account.purpose == "standard"
                            ),
                            signal["signal_market_ts_ms"],
                        ),
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
            "Read-only probe for causal customer-base-short replenishment "
            "windows under the frozen priority v1.30 policy."
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
    counts: dict[str, int] = {}
    for signal in signals:
        key = (
            f"{signal['category']}|{signal['market_date']}|"
            f"{signal['bond_code']}"
        )
        counts[key] = counts.get(key, 0) + 1
    output = {
        "model_id": PRIORITY_POLICY_V130_CANDIDATE.model_id,
        "behavior_changed": False,
        "source_database_opened_readonly": True,
        "probe_conditions": {
            "profitable_visible_bid_minimum_edge": 0.18,
            "profitable_visible_bid_minimum_bonds": 1000,
            "rising_tight_ask_maximum_spread": 0.02,
            "rising_tight_ask_maximum_loss": 0.05,
            "customer_base_short_only": True,
        },
        "signal_count": len(signals),
        "signals_by_category_date_and_code": counts,
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
