"""Read-only causal order/fill audit for registered ordinary tape candidates."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

from zhaiquant.config import load_config, maker_underlying_stock_code
from zhaiquant.database import SQLiteStore
from zhaiquant.maker import _load_ticks
from zhaiquant.maker_paper import REALTIME_COMPARISON_POLICIES
from replay_priority_entry_quality import EntryAuditEngine


class TapeAuditEngine(EntryAuditEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.frames = []
        self.frame_start = '00:00:00'

    def _refresh_orders(self, account, tick, assessment, **kwargs):
        super()._refresh_orders(account, tick, assessment, **kwargs)
        if tick.market_time[:8] < self.frame_start:
            return
        self.frames.append(dict(time=tick.market_time[:8], tick_id=tick.tick_id,
            inventory=account.inventory, bids=tick.bids, asks=tick.asks,
            assessment=asdict(assessment),
            pressure=account.ordinary_tape_pressure_active,
            pressure_since=account.ordinary_tape_pressure_since_ms,
            recovered_at=account.ordinary_tape_recovery_ts_ms,
            risk_exit_at=account.last_ordinary_risk_exit_ts_ms,
            latest_extra_entry_at=account.last_new_extra_entry_ts_ms,
            orders=[dict(side=o.side,kind=o.kind,price=o.limit_price,quantity=o.remaining)
                    for o in ([account.buy_order] if account.buy_order else [])
                    + list(account.sell_orders.values())]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='config.toml')
    parser.add_argument('--dates',nargs='+',required=True)
    parser.add_argument('--codes',nargs='+',default=['132026.SH','132024.SH'])
    parser.add_argument('--models',nargs='+',default=[
        'maker_priority_v2_67_candidate','maker_priority_v2_68_candidate'])
    parser.add_argument('--cutoff',default='15:30:03')
    parser.add_argument('--frame-start',default='00:00:00')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    config=load_config(args.config)
    report=dict(source_readonly=True,cutoff=args.cutoff,cells=[])
    with sqlite3.connect(config.storage.database.resolve().as_uri()+'?mode=ro',uri=True) as source:
        source.row_factory=sqlite3.Row
        source.execute('PRAGMA query_only=ON')
        source.execute('BEGIN')
        for day in args.dates:
            for code in args.codes:
                ticks=None
                variants={}
                for model in args.models:
                    with tempfile.TemporaryDirectory(prefix='ordinary_tape_audit_') as folder:
                        isolated=replace(config,storage=replace(config.storage,
                            database=Path(folder)/'paper.sqlite3'))
                        store=SQLiteStore(isolated)
                        store.start_session()
                        try:
                            engine=TapeAuditEngine(isolated,store,bond_code=code,
                                priority_policy=REALTIME_COMPARISON_POLICIES[model],
                                fill_modes=('priority',),include_windfall=False)
                            engine.frame_start=args.frame_start
                            if ticks is None:
                                ticks=tuple(t for t in _load_ticks(source,day,code,
                                    maker_underlying_stock_code(config,code),engine.parameters)
                                    if t.market_time[:8]<=args.cutoff)
                            for tick in ticks:
                                engine.on_replay_tick(tick,persist=False)
                            variants[model]=dict(profile=asdict(engine.priority_policy),
                                fills=engine.audit_fills,frames=engine.frames,
                                blocked_frames=engine.blocked_frames,summary=engine.runtime_summary())
                            print(day,code,model,engine.runtime_summary()['accounts'],flush=True)
                        finally:
                            store.close()
                report['cells'].append(dict(date=day,code=code,variants=variants))
    output=Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('SHA256',hashlib.sha256(output.read_bytes()).hexdigest())


if __name__=='__main__':
    main()
