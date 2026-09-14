"""Replay actual non-backfill arrival streams in disposable paper ledgers.

No market-time sorting or writes to the source database. The complete run,
truncated prefix and checkpoint/restart run must have identical prefixes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

from zhaiquant import maker_paper
from zhaiquant.config import load_config
from zhaiquant.database import SQLiteStore
from zhaiquant.recorder import RecordedTick, TickRecorder
from zhaiquant.shared_thousand_maker_v016_live import ArrivalSharedPaperRuntime, POLICY
from zhaiquant.types import Tick


def ledger(store):
    fills = [dict(r) for r in store.connection.execute('''SELECT strategy_id,market_ts_ms,
        side,price,quantity,fill_reason,reference_tick_id,cash_after,inventory_after
        FROM maker_paper_fills ORDER BY id''')]
    orders = [dict(r) for r in store.connection.execute('''SELECT strategy_id,side,status,kind,
        created_market_ts_ms,updated_market_ts_ms,limit_price,quantity,filled_quantity,
        queue_ahead,target_price,cancel_reason,metadata_json FROM maker_paper_orders ORDER BY id''')]
    accounts = [dict(r) for r in store.connection.execute('''SELECT strategy_id,cash,inventory,
        initial_cash,initial_inventory,last_market_ts_ms FROM maker_paper_accounts ORDER BY strategy_id''')]
    return dict(fills=fills, orders=orders, accounts=accounts)


def replay(config, rows, day, *, restart_at=None, stop_at=None):
    with tempfile.TemporaryDirectory(prefix='shared-arrival-verify-') as temporary:
        trial = replace(config, storage=replace(config.storage, database=Path(temporary)/'trial.sqlite3'),
                        maker_paper=replace(config.maker_paper, realtime_comparison_model_ids=(POLICY.model_id,)))
        store = SQLiteStore(trial)
        store.start_session()
        try:
            runtime = ArrivalSharedPaperRuntime(trial, store)
            runtime.rebuild_date(day)
            prefix = None
            raw_columns = [row['name'] for row in store.connection.execute('PRAGMA table_info(raw_ticks)')]
            insert_sql = f"INSERT INTO raw_ticks({','.join(raw_columns)}) VALUES ({','.join('?' for _ in raw_columns)})"
            # Importing all rows up front also proves recovery ignores source
            # rows not yet journaled by this model at the checkpoint.
            store.connection.executemany(insert_sql, [tuple(row[k] for k in raw_columns) for row in rows])
            for index, row in enumerate(rows[:stop_at] if stop_at else rows, 1):
                tick = Tick.from_qmt(row['code'], json.loads(row['raw_json']), row['received_ts_ns'])
                change = TickRecorder._change(None, None, tick)  # runtime must not rely on this reset delta
                runtime.on_recorded_tick(RecordedTick(row['id'], tick, change, True))
                if index == len(rows)//2:
                    prefix = ledger(store)
                if index == restart_at:
                    before = ledger(store)
                    # New runtime instance, not an in-memory reset only.
                    runtime = ArrivalSharedPaperRuntime(trial, store)
                    runtime.rebuild_date(day)
                    assert ledger(store) == before, 'restart rewrote recorded economic prefix'
            state = ledger(store)
            cash, inventory = 0.0, {}
            initial = runtime.allocator.initial_cash_cny
            cash = initial
            for fill in state['fills']:
                side = 1 if fill['side'] == 'buy' else -1
                cash -= side*fill['price']*fill['quantity']
                code = fill['strategy_id']
                inventory[code] = inventory.get(code, 0)+side*fill['quantity']
                assert min(inventory.values()) >= -1e-7
                assert cash >= -1e-7
                assert sum(inventory.values()) <= 1000+1e-7
                assert sum(q > 1e-7 for q in inventory.values()) <= 1
            assert abs(cash-runtime.allocator.shared_cash_cny) < 1e-6
            for order in state['orders']:
                metadata = json.loads(order['metadata_json'])
                assert metadata['model_id'] == POLICY.model_id
                assert metadata['decision_ts_ms'] == order['created_market_ts_ms']
                assert metadata['decision_ts_ms'] >= metadata['received_ts_ns']//1_000_000
                assert metadata['decision_ts_ms'] >= metadata['source_market_ts_ms']
            summary = runtime.runtime_summary()
            pnl = sum(row['pnl'] for row in summary['accounts'])
            return dict(state=state, prefix=prefix, summary=dict(
                day=day, inputs=len(rows) if stop_at is None else stop_at,
                fills=len(state['fills']), orders=len(state['orders']), gross_pnl=pnl,
                initial_cash=initial, cash=round(cash, 6), inventory=sum(inventory.values()),
                arrival=summary['arrival_execution'],
                execution={code: dict(e.execution_counts) for code, e in runtime.engines.items()}))
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config.toml')
    parser.add_argument('--dates', nargs='+', default=['2026-08-17', '2026-08-31', '2026-09-07'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing to overwrite evidence')
    config = load_config(args.config)
    freeze = json.loads(Path('output/research/shared_v016r2_before_20260908.json').read_text(encoding='utf-8'))
    for name, value in freeze['profiles'].items():
        assert json.loads(json.dumps(asdict(getattr(maker_paper, name)))) == value, name
    immutable = [path for path in freeze['files'] if path.startswith('output/') or path.endswith('_research.py')]
    for path in immutable:
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == freeze['files'][path], path
    source = sqlite3.connect(config.storage.database.as_uri()+'?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    source.execute('BEGIN')
    days = []
    try:
        for day in args.dates:
            rows = source.execute('''SELECT r.* FROM raw_ticks r JOIN sessions s ON s.run_id=r.run_id
                WHERE r.market_date=? AND s.status!='backfill'
                AND r.code IN ('132026.SH','132024.SH','600900.SH','600362.SH') ORDER BY r.id''', (day,)).fetchall()
            if not rows:
                raise ValueError(f'{day} has no actual arrival data')
            complete = replay(config, rows, day)
            prefix = replay(config, rows, day, stop_at=len(rows)//2)
            restarted = replay(config, rows, day, restart_at=len(rows)//2)
            assert complete['prefix'] == prefix['state'], 'future suffix changed prefix'
            assert complete['state'] == restarted['state'], 'full restarted path differs'
            days.append(dict(**complete['summary'], prefix_exact=True, restart_exact=True))
            print(json.dumps(days[-1], ensure_ascii=False), flush=True)
    finally:
        source.close()
    report = dict(model_id=POLICY.model_id, old_profiles_unchanged=len(freeze['profiles']),
                  immutable_files_unchanged=immutable, days=days,
                  input_basis='actual insertion/processing order, non-backfill only; not old market-time reports',
                  paper_only=True, verified=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
