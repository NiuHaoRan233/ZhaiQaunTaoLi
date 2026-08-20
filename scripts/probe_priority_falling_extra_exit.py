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
    PRIORITY_POLICY_V130_CANDIDATE,
    _load_ticks,
)
from zhaiquant.types import SHANGHAI


def _market_time(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1_000, SHANGHAI).strftime("%H:%M:%S")


def _future_path(bond_ticks, signal_ms: int, horizon_seconds: int) -> dict:
    selected = [
        tick for tick in bond_ticks
        if signal_ms < tick.market_ts_ms <= signal_ms + horizon_seconds * 1_000
    ]
    if not selected:
        return {"ticks": 0}
    bids = [tick.bid1 for tick in selected if tick.bid1 > 0]
    asks = [tick.ask1 for tick in selected if tick.ask1 > 0]
    lasts = [tick.last_price for tick in selected if tick.last_price > 0]
    return {
        "ticks": len(selected),
        "minimum_bid1": round(min(bids), 3) if bids else None,
        "maximum_bid1": round(max(bids), 3) if bids else None,
        "minimum_ask1": round(min(asks), 3) if asks else None,
        "maximum_ask1": round(max(asks), 3) if asks else None,
        "minimum_last": round(min(lasts), 3) if lasts else None,
        "maximum_last": round(max(lasts), 3) if lasts else None,
    }


def _probe_one(config, market_date: str, bond_code: str) -> list[dict]:
    source_path = config.storage.database.resolve()
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    signals: list[dict] = []
    signaled_lots: set[int] = set()
    try:
        with tempfile.TemporaryDirectory() as temporary:
            replay_config = replace(
                config,
                storage=replace(
                    config.storage,
                    database=Path(temporary) / "falling-extra-probe.sqlite3",
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
                    parameters = engine.parameters
                    if not (
                        assessment.state == "falling"
                        and assessment.recent_sell_bonds + 1e-9
                            >= 5 * parameters.order_quantity_bonds
                        and assessment.recent_sell_bonds + 1e-9
                            >= 5 * max(assessment.recent_buy_bonds, 1.0)
                        and assessment.midpoint_change <= -0.10 + 1e-9
                        and tick.bid1 > 0
                        and tick.bid1_bonds + 1e-9
                            >= parameters.order_quantity_bonds
                        and account.inventory
                            > account.initial_inventory + 1e-9
                        and not engine._confirmed_rise_is_recent(
                            tick, account.policy,
                        )
                    ):
                        return assessment

                    extra_remaining = account.inventory - account.initial_inventory
                    lots = sorted(
                        (
                            lot for lot in account.lots.values()
                            if lot.entry_price is not None
                            and lot.remaining_quantity > 1e-9
                            and lot.entry_price - tick.bid1
                                <= parameters.maximum_near_flat_exit_loss + 1e-9
                        ),
                        key=lambda lot: (lot.opened_ms, lot.db_id),
                        reverse=True,
                    )
                    for lot in lots:
                        if extra_remaining <= 1e-9:
                            break
                        quantity = min(extra_remaining, lot.remaining_quantity)
                        extra_remaining -= quantity
                        if lot.db_id in signaled_lots:
                            continue
                        signaled_lots.add(lot.db_id)
                        context = engine._decision_context(tick, account.policy)
                        signals.append({
                            "market_date": market_date,
                            "bond_code": bond_code,
                            "signal_market_ts_ms": int(tick.market_ts_ms),
                            "signal_market_time": tick.market_time,
                            "lot_id": int(lot.db_id),
                            "lot_kind": lot.kind,
                            "lot_opened_time": _market_time(lot.opened_ms),
                            "lot_entry_price": round(float(lot.entry_price), 3),
                            "eligible_quantity_bonds": float(quantity),
                            "bid1": round(float(tick.bid1), 3),
                            "bid1_bonds": float(tick.bid1_bonds),
                            "ask1": round(float(tick.ask1), 3),
                            "last_price": round(float(tick.last_price), 3),
                            "inferred_side": tick.inferred_side,
                            "trade_bonds": float(tick.trade_bonds),
                            "reference_price": round(
                                float(assessment.reference_price), 3,
                            ),
                            "reference_source": assessment.reference_source,
                            "reference_reliable": context.reliable_anchor,
                            "spread": context.spread,
                            "bid_support_bonds": context.bid_support_bonds,
                            "ask_supply_bonds": context.ask_supply_bonds,
                            "state": assessment.state,
                            "recent_buy_bonds": assessment.recent_buy_bonds,
                            "recent_sell_bonds": assessment.recent_sell_bonds,
                            "midpoint_change": assessment.midpoint_change,
                            "short_ask_change": assessment.short_ask_change,
                            "largest_ask_gap": assessment.largest_ask_gap,
                            "downside_book_vacuum": (
                                assessment.downside_book_vacuum
                            ),
                            "fragile_top_bid": assessment.fragile_top_bid,
                            "evidence": list(assessment.evidence),
                            "exit_edge_at_bid": round(
                                float(tick.bid1 - lot.entry_price), 3,
                            ),
                        })
                    return assessment

                engine.analyzer.assess_market = capture_assessment
                for tick in ticks:
                    engine.on_replay_tick(tick, persist=True)

                for signal in signals:
                    sell = store.connection.execute(
                        """SELECT f.market_ts_ms,f.price,f.quantity,f.fill_reason
                             FROM maker_paper_fills f
                             JOIN maker_paper_orders o ON o.id=f.order_id
                            WHERE o.lot_id=? AND f.side='sell'
                              AND f.market_ts_ms>=?
                            ORDER BY f.market_ts_ms,f.id LIMIT 1""",
                        (signal["lot_id"], signal["signal_market_ts_ms"]),
                    ).fetchone()
                    signal["existing_model_next_exit"] = (
                        {
                            "market_time": _market_time(int(sell["market_ts_ms"])),
                            "price": round(float(sell["price"]), 3),
                            "quantity_bonds": float(sell["quantity"]),
                            "fill_reason": sell["fill_reason"],
                            "seconds_after_signal": int(
                                (sell["market_ts_ms"]
                                 - signal["signal_market_ts_ms"]) / 1_000
                            ),
                        }
                        if sell is not None else None
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
        description="Read-only probe for near-flat extra inventory exits in confirmed falling markets.",
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
    summary: dict[str, int] = {}
    for signal in signals:
        key = f"{signal['market_date']}|{signal['bond_code']}"
        summary[key] = summary.get(key, 0) + 1
    output = {
        "model_id": PRIORITY_POLICY_V130_CANDIDATE.model_id,
        "behavior_changed": False,
        "source_database_opened_readonly": True,
        "probe_conditions": {
            "state": "falling",
            "minimum_recent_sell_bonds": 5000,
            "minimum_recent_sell_to_buy_ratio": 5,
            "maximum_midpoint_change": -0.10,
            "minimum_bid1_bonds": 1000,
            "maximum_exit_loss_per_bond": 0.015,
            "recent_confirmed_rise_forbidden": True,
            "extra_inventory_only": True,
        },
        "signal_count": len(signals),
        "signals_by_date_and_code": summary,
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
