"""Read-only causal audit of reentry state and selected market frames."""
from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

from zhaiquant.config import load_config, maker_underlying_stock_code
from zhaiquant.database import SQLiteStore
from zhaiquant.maker import _load_ticks
from zhaiquant.maker_paper import MakerPaperEngine, REALTIME_COMPARISON_POLICIES


class IdentityAuditEngine(MakerPaperEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.audit_fills = []
        self.audit_frames = []
        self.last_risk_exit = None
        self.probe_times = set()

    def _fill_sell(self, account, tick, order, quantity, received_ts_ns, **kwargs):
        before = account.pending_inventory_turn_quantity
        super()._fill_sell(account, tick, order, quantity, received_ts_ns, **kwargs)
        event = dict(time=tick.market_time, kind=order.kind, price=order.limit_price,
                     quantity=quantity, pending_before=before,
                     pending_after=account.pending_inventory_turn_quantity,
                     stalled_price=account.last_stalled_extra_exit_price,
                     stalled_timestamp=account.last_stalled_extra_exit_ts_ms,
                     inventory=account.inventory)
        self.audit_fills.append(event)
        if order.kind in {"live_priority_extra_inventory_exit",
                          "live_priority_extra_inventory_isolated_hold",
                          "stalled_extra_inventory_near_flat_exit",
                          "support_collapse_capacity_release_exit"}:
            self.last_risk_exit = event

    def _refresh_orders(self, account, tick, assessment, **kwargs):
        super()._refresh_orders(account, tick, assessment, **kwargs)
        if self.probe_times:
            if tick.market_time[:8] not in self.probe_times:
                return
        elif self.last_risk_exit is None or (
            account.pending_inventory_turn_quantity <= 0
            and not 0 <= tick.market_ts_ms - account.last_stalled_extra_exit_ts_ms <= 600_000
        ):
            return
        order = account.buy_order
        if (not self.probe_times and account.inventory > account.initial_inventory) or tick.bid1 <= 0:
            return
        context = self._decision_context(tick, account.policy)
        self.audit_frames.append(dict(
            time=tick.market_time, timestamp=tick.market_ts_ms,
            risk_exit=self.last_risk_exit, inventory=account.inventory,
            pending_quantity=account.pending_inventory_turn_quantity,
            pending_average=account.pending_inventory_turn_sale_value
                / max(account.pending_inventory_turn_quantity, 1e-9),
            stalled_price=account.last_stalled_extra_exit_price,
            stalled_timestamp=account.last_stalled_extra_exit_ts_ms,
            ordinary_risk_exit_timestamp=account.last_ordinary_risk_exit_ts_ms,
            bid=tick.bid1, ask=tick.ask1, bids=tick.bids, asks=tick.asks,
            trade_bonds=tick.trade_bonds, side=tick.inferred_side,
            assessment=asdict(assessment), context=asdict(context),
            order=None if order is None else dict(
                price=order.limit_price, kind=order.kind, quantity=order.remaining),
        ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.toml")
    parser.add_argument("--model", default="maker_priority_v2_64_candidate_r2")
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument("--codes", nargs="+", default=["132026.SH", "132024.SH"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--probe-times", nargs="*", default=[])
    args = parser.parse_args()
    config = load_config(args.config)
    policy = REALTIME_COMPARISON_POLICIES[args.model]
    results = []
    with sqlite3.connect(f"file:{config.storage.database.resolve().as_posix()}?mode=ro", uri=True) as source:
        source.row_factory = sqlite3.Row
        for day in args.dates:
            for code in args.codes:
                with tempfile.TemporaryDirectory() as directory:
                    isolated = replace(config, storage=replace(
                        config.storage, database=Path(directory) / "audit.sqlite3"))
                    store = SQLiteStore(isolated)
                    store.start_session()
                    try:
                        engine = IdentityAuditEngine(isolated, store, bond_code=code,
                            priority_policy=policy, fill_modes=("priority",), include_windfall=False)
                        engine.probe_times = set(args.probe_times)
                        ticks = _load_ticks(source, day, code,
                            maker_underlying_stock_code(config, code), engine.parameters)
                        for tick in ticks:
                            engine.on_replay_tick(tick, persist=False)
                        results.append(dict(date=day, code=code, model=args.model,
                            fills=engine.audit_fills, frames=engine.audit_frames))
                        print(day, code, "sells", len(engine.audit_fills),
                              "pending_frames", len(engine.audit_frames), flush=True)
                    finally:
                        store.close()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(source_readonly=True, cells=results),
                                ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
