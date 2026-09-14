"""Verify frozen simple decisions and real arrival/restart paths in temporary DBs."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from zhaiquant import maker_paper
from zhaiquant.config import load_config
from zhaiquant.database import SQLiteStore
from zhaiquant.dadao_maker_live import DadaoPaperRuntime, DadaoAllocator, POLICY
from zhaiquant.shared_thousand_maker_v016_live import ArrivalTick
from probe_top_cycle_comparison import load_day, parameters
import verify_shared_v016r2_live as arrival_verifier


def frozen_day(config, source, cell):
    day = cell['market_date']
    ticks, cash, ready = load_day(source, config, day, parameters(config))
    assert hashlib.sha256(json.dumps([asdict(t) for t in ticks], sort_keys=True).encode()).hexdigest() == cell['tick_sha256']
    expected = cell['probes']['switch_v013']
    with tempfile.TemporaryDirectory(prefix='dadao-oracle-') as temporary:
        trial = replace(config, storage=replace(config.storage, database=Path(temporary)/'trial.sqlite3'))
        store = SQLiteStore(trial)
        store.start_session()
        try:
            runtime = DadaoPaperRuntime(trial, store)
            runtime._reset_date(day, clear=False)
            runtime.allocator = DadaoAllocator(runtime.engines,
                initial_cash_cny=cash, capital_ready_ts_ms=ready)
            # Match the oracle's explicit cash/ready inputs and clocks. Live
            # startup instead waits for BOTH callbacks even at equal times.
            # This is decision equivalence, NOT live execution evidence.
            for i, t in enumerate(ticks, 1):
                runtime.allocator.on_replay_tick(ArrivalTick(**asdict(t),
                    source_market_ts_ms=t.market_ts_ms, received_ts_ns=t.market_ts_ms*1_000_000,
                    arrival_sequence=i))
            ids = {a.strategy_id: c for c, e in runtime.engines.items() for a in e.accounts.values()}
            fields = ('market_ts_ms','side','price','quantity','inventory_after','reference_tick_id')
            actual = [(ids[r['strategy_id']], *(r[k] for k in fields)) for r in store.connection.execute(
                'SELECT * FROM maker_paper_fills ORDER BY id')]
            target = [(f['bond_code'], *(f[k] for k in fields)) for f in expected['fills']]
            assert actual == target, (day, 'fill difference', len(actual), len(target),
                next(((a,b) for a,b in zip(actual,target) if a!=b), None))
            orders = [(ids[r['strategy_id']],r['side'],r['created_market_ts_ms'],round(r['limit_price']*1000),r['quantity'])
                      for r in store.connection.execute('SELECT * FROM maker_paper_orders ORDER BY id')]
            target_orders = [(o['code'],o['side'],o['created_ts'],o['price'],o['quantity']) for o in expected['orders']]
            assert orders == target_orders, (day,'order difference',len(orders),len(target_orders),
                next(((i,a,b,orders[max(0,i-1):i+3],target_orders[max(0,i-1):i+3]) for i,(a,b) in enumerate(zip(orders,target_orders)) if a!=b),None))
            selection = [(e.market_ts_ms,e.previous_bond_code,e.selected_bond_code,e.reason) for e in runtime.allocator.events]
            assert selection == [(e['ts'],e['previous'],e['winner'],e['reason']) for e in expected['selection_events']], (day,'selection difference')
            pnl = sum(a.trading_pnl for e in runtime.engines.values() for a in e.accounts.values())
            assert abs(pnl-expected['trading_pnl']) < 1e-6, (day,pnl,expected['trading_pnl'])
            assert abs(runtime.allocator.initial_cash_cny-cash) < 1e-6
            assert runtime.allocator.capital_ready_ts_ms == ready
            assert abs(runtime.allocator.shared_cash_cny-expected['terminal_cash_cny']) < 1e-6
            return dict(day=day,fills=len(actual),orders=len(orders),gross_pnl=round(pnl,2),
                        fills_orders_selections_exact=True)
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--oracle-dates',nargs='*')
    parser.add_argument('--arrival-dates',nargs='*',default=['2026-08-17','2026-08-31','2026-09-07'])
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    config = load_config('config.toml')
    freeze = json.loads(Path('output/research/dadao_live_before_20260908.json').read_text(encoding='utf-8'))
    for name, value in freeze['profiles'].items():
        assert json.loads(json.dumps(asdict(getattr(maker_paper,name)))) == value, name
    archive = json.loads(Path('策略自我迭代优化/大道至简/大道至简0.1_研究归档清单_2026-09-08.json').read_text(encoding='utf-8'))
    for artifact in archive['local_report_artifacts']:
        assert hashlib.sha256(Path(artifact['path']).read_bytes()).hexdigest() == artifact['sha256']
    for path in ('scripts/probe_top_cycle_comparison.py','scripts/probe_top_of_book_cycle.py',
                 'src/zhaiquant/shared_thousand_maker_v016_live.py','src/zhaiquant/maker.py',
                 'src/zhaiquant/one_hand_maker_research.py'):
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == freeze['files'][path], path
    matrix = json.loads(Path('output/research/top_cycle_comparison_20260908_matrix.json').read_text(encoding='utf-8'))
    source = sqlite3.connect(config.storage.database.as_uri()+'?mode=ro',uri=True)
    source.row_factory = sqlite3.Row
    source.execute('BEGIN')
    result = dict(model_id=POLICY.model_id, old_profiles_unchanged=len(freeze['profiles']),
                  original_reports_unchanged=True, frozen_decision_days=[], arrival_days=[])
    try:
        for cell in matrix['cells']:
            if args.oracle_dates is not None and cell['market_date'] not in args.oracle_dates:
                continue
            item = frozen_day(config,source,cell)
            result['frozen_decision_days'].append(item)
            print(json.dumps(item),flush=True)
        with patch.object(arrival_verifier,'ArrivalSharedPaperRuntime',DadaoPaperRuntime), patch.object(arrival_verifier,'POLICY',POLICY):
            for day in args.arrival_dates:
                rows = source.execute('''SELECT r.* FROM raw_ticks r JOIN sessions s ON s.run_id=r.run_id
                    WHERE r.market_date=? AND s.status!='backfill'
                    AND r.code IN ('132026.SH','132024.SH','600900.SH','600362.SH') ORDER BY r.id''',(day,)).fetchall()
                assert rows, day
                full = arrival_verifier.replay(config,rows,day)
                prefix = arrival_verifier.replay(config,rows,day,stop_at=len(rows)//2)
                restarted = arrival_verifier.replay(config,rows,day,restart_at=len(rows)//2)
                assert full['prefix'] == prefix['state'], (day,'prefix')
                assert full['state'] == restarted['state'], (day,'restart')
                item = dict(**full['summary'],prefix_exact=True,restart_exact=True)
                result['arrival_days'].append(item)
                print(json.dumps(item),flush=True)
    finally:
        source.close()
    result['verified'] = True
    with args.output.open('x',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)


if __name__ == '__main__':
    main()
