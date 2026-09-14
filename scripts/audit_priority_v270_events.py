"""Read-only causal frames, new permissions and fill timing for v2.70 repairs."""
from __future__ import annotations

import argparse
import sqlite3
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

from audit_priority_tape_regime import TapeAuditEngine
from audit_priority_v269 import save
from zhaiquant.config import load_config, maker_underlying_stock_code
from zhaiquant.database import SQLiteStore
from zhaiquant.maker import _load_ticks
from zhaiquant.maker_paper import REALTIME_COMPARISON_POLICIES


class RecoveryAuditEngine(TapeAuditEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.contexts = {}
        self.transitions = []
        self.current_passive_permission = False

    def _decision_context(self, tick, policy=None):
        result = super()._decision_context(tick, policy)
        if tick is not None:
            self.contexts[tick.tick_id] = asdict(result)
        return result

    def _record_fill(self, account, tick, order, lot_id, side, price, quantity, reason, received_ts_ns):
        super()._record_fill(account, tick, order, lot_id, side, price, quantity, reason, received_ts_ns)
        self.audit_fills[-1].update(order_kind=order.kind, lot_id=lot_id,
            tick_id=tick.tick_id, market_ts_ms=tick.market_ts_ms, created_ms=order.created_ms)

    def _ordinary_recovery_allows_passive_quote(self, account, tick):
        result = super()._ordinary_recovery_allows_passive_quote(account, tick)
        self.current_passive_permission = result
        return result

    def _update_ordinary_tape_turnover_regime(self, account, tick, assessment):
        before = dict(pressure=account.ordinary_tape_pressure_active,
            since=account.ordinary_tape_pressure_since_ms,
            recovered=account.ordinary_tape_recovery_ts_ms,
            recovery_bid=account.ordinary_tape_recovery_bid,
            bids=account.last_bids, asks=account.last_asks)
        super()._update_ordinary_tape_turnover_regime(account, tick, assessment)
        if account.ordinary_tape_pressure_since_ms != before['since']:
            self.transitions.append(dict(time=tick.market_time[:8], tick=asdict(tick),
                before=before, inventory=account.inventory, assessment=asdict(assessment)))

    def _refresh_orders(self, account, tick, assessment, **kwargs):
        self.current_passive_permission = False
        super()._refresh_orders(account, tick, assessment, **kwargs)
        if self.frames and self.frames[-1]['tick_id'] == tick.tick_id:
            self.frames[-1].update(tick=asdict(tick), context=self.contexts.get(tick.tick_id),
                passive_permission=self.current_passive_permission,
                pending=account.pending_inventory_turn_quantity,
                recovery_bid=account.ordinary_tape_recovery_bid,
                lots=[dict(kind=l.kind, entry=l.entry_price, quantity=l.remaining_quantity,
                           opened=l.opened_ms) for l in account.lots.values()
                      if l.remaining_quantity > 0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dates', nargs='+', required=True)
    parser.add_argument('--codes', nargs='+', default=['132026.SH', '132024.SH'])
    parser.add_argument('--models', nargs='+', default=[
        'maker_priority_v2_69_candidate', 'maker_priority_v2_70_candidate_r2'])
    parser.add_argument('--cutoff', default='15:30:03')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if Path(args.output).exists():
        raise FileExistsError(args.output)
    config = load_config('config.toml')
    report = dict(source_readonly=True, cutoff=args.cutoff, cells=[])
    with sqlite3.connect(config.storage.database.resolve().as_uri()+'?mode=ro', uri=True) as source:
        source.row_factory = sqlite3.Row
        source.execute('PRAGMA query_only=ON')
        source.execute('BEGIN')
        for day in args.dates:
            for code in args.codes:
                ticks, variants = None, {}
                for model in args.models:
                    with tempfile.TemporaryDirectory(prefix='recovery_audit_') as folder:
                        isolated = replace(config, storage=replace(config.storage,
                            database=Path(folder)/'paper.sqlite3'))
                        store = SQLiteStore(isolated)
                        store.start_session()
                        try:
                            engine = RecoveryAuditEngine(isolated, store, bond_code=code,
                                priority_policy=REALTIME_COMPARISON_POLICIES[model],
                                fill_modes=('priority',), include_windfall=False)
                            if ticks is None:
                                ticks = tuple(t for t in _load_ticks(source, day, code,
                                    maker_underlying_stock_code(config, code), engine.parameters)
                                    if t.market_time[:8] <= args.cutoff)
                            for tick in ticks:
                                engine.on_replay_tick(tick, persist=False)
                            for fill in engine.audit_fills:
                                if 'passive' in fill['fill_reason']:
                                    assert fill['created_ms'] < fill['market_ts_ms'], fill
                            variants[model] = dict(fills=engine.audit_fills,
                                frames=engine.frames, transitions=engine.transitions,
                                summary=engine.runtime_summary())
                            print(day, code, model, len(engine.audit_fills), flush=True)
                        finally:
                            store.close()
                report['cells'].append(dict(date=day, code=code, variants=variants))
    save(args.output, report)


if __name__ == '__main__':
    main()
