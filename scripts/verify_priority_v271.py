"""Verify saved baselines, causal prefixes and the ordinary v2.71 account paths."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

from audit_priority_v270_events import RecoveryAuditEngine
from zhaiquant.config import load_config, maker_underlying_stock_code
from zhaiquant.database import SQLiteStore
from zhaiquant.maker import _load_ticks
from zhaiquant.maker_paper import REALTIME_COMPARISON_POLICIES

ROOT = Path('output/research')
NEW = 'maker_priority_v2_71_candidate'
OLD = 'maker_priority_v2_70_candidate_r2'
FIELDS = ('fills', 'fill_count', 'order_count', 'terminal_inventory', 'trading_pnl',
          'path_bounds', 'weighted_customer_base_short_metrics', 'extra_inventory_metrics')


def read(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def indexed(report):
    return {(c['market_date'], c['bond_code']): c for c in report['cells']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT/'priority_v271_verification.json'))
    args = parser.parse_args()
    manifest = read('priority_v271_predevelopment_manifest.json')
    for model, old in manifest['profiles'].items():
        current = json.loads(json.dumps(asdict(REALTIME_COMPARISON_POLICIES[model])))
        assert all(current[k] == value for k, value in old.items()), model
        assert not current['protect_discounted_ordinary_lot_from_fragile_bid_exit']
        assert not current['require_strict_passive_order_timestamp']
    for path, digest in manifest['sha256'].items():
        if path != 'src/zhaiquant/maker_paper.py':
            assert sha(path) == digest, path
    assert REALTIME_COMPARISON_POLICIES[NEW] == replace(
        REALTIME_COMPARISON_POLICIES[OLD], model_id=NEW, model_version='2.71-candidate',
        protect_discounted_ordinary_lot_from_fragile_bid_exit=True,
        require_strict_passive_order_timestamp=True)

    saved = indexed(read('priority_v270r2_final_matrix_20260804_20260904.json'))
    before = indexed(read('priority_v271_before_baselines.json'))
    for key, cell in before.items():
        for prefix in ('parent', 'candidate'):
            for field in FIELDS:
                k = prefix+'_'+field
                assert cell[k] == saved[key][k], (key, k)
    controls = dict(before)
    controls.update(indexed(read('priority_v271_recent_270_control.json')))
    final = indexed(read('priority_v271_final_matrix_20260804_20260910.json'))
    assert final.keys() == controls.keys()
    changed = []
    for key, cell in final.items():
        for field in FIELDS:
            k = 'parent_'+field
            assert cell[k] == controls[key][k], (key, k)
        assert cell['queue_branch_identical'] and cell['windfall_branch_identical'], key
        bounds = cell['candidate_path_bounds']
        assert bounds['minimum_inventory_bonds'] >= 0
        assert bounds['maximum_inventory_bonds'] <= 2_000
        assert bounds['minimum_cash_cny'] >= -1e-7
        if any(cell['candidate_'+f] != controls[key]['candidate_'+f] for f in FIELDS):
            changed.append(dict(date=key[0], code=key[1],
                delta_270=cell['candidate_trading_pnl']-controls[key]['candidate_trading_pnl']))

    today = read('priority_v271_today_101500.json')
    config = load_config('config.toml')
    prefix_checks = 0
    input_hashes, today_results = {}, []
    with sqlite3.connect(config.storage.database.resolve().as_uri()+'?mode=ro', uri=True) as source:
        source.row_factory = sqlite3.Row
        source.execute('PRAGMA query_only=ON')
        source.execute('BEGIN')
        for cell in today['cells']:
            code, expected = cell['code'], cell['variants'][NEW]
            account = expected['summary']['accounts'][0]
            inventory, cash = account['initial_inventory'], account['initial_cash']
            # No capacity top-up occurred in these two fixed morning windows.
            assert account['funding_adjustment'] == 0
            for fill in expected['fills']:
                sign = 1 if fill['side'] == 'buy' else -1
                inventory += sign * fill['quantity_bonds']
                cash -= sign * fill['price'] * fill['quantity_bonds']
                assert inventory == fill['inventory_after_bonds']
                assert 0 <= inventory <= 2_000 and cash >= -1e-7
                if 'passive' in fill['fill_reason']:
                    assert fill['created_ms'] < fill['market_ts_ms'], fill
            assert inventory == account['inventory'] and abs(cash-account['cash']) < 1e-6
            old = cell['variants'][OLD]['summary']['accounts'][0]
            today_results.append(dict(code=code, old_pnl=old['pnl'], new_pnl=account['pnl'],
                delta=account['pnl']-old['pnl'], fills=len(expected['fills']),
                terminal_inventory=inventory))
            for cutoff in ('09:30:15', '09:30:18', '09:30:21', '09:32:18', '09:35:00', '10:00:00'):
                with tempfile.TemporaryDirectory(prefix='priority_v271_prefix_') as folder:
                    isolated = replace(config, storage=replace(config.storage,
                        database=Path(folder)/'paper.sqlite3'))
                    store = SQLiteStore(isolated)
                    store.start_session()
                    try:
                        engine = RecoveryAuditEngine(isolated, store, bond_code=code,
                            priority_policy=REALTIME_COMPARISON_POLICIES[NEW],
                            fill_modes=('priority',), include_windfall=False)
                        ticks = tuple(t for t in _load_ticks(source, '2026-09-11', code,
                            maker_underlying_stock_code(config, code), engine.parameters)
                            if t.market_time[:8] <= cutoff)
                        for tick in ticks:
                            engine.on_replay_tick(tick, persist=False)
                        assert engine.audit_fills == [f for f in expected['fills']
                            if f['market_time'] <= cutoff], (code, cutoff, 'fills')
                        assert json.loads(json.dumps(engine.frames)) == [f for f in expected['frames']
                            if f['time'] <= cutoff], (code, cutoff, 'frames')
                        input_hashes[code+' '+cutoff] = hashlib.sha256(json.dumps(
                            [asdict(t) for t in ticks], sort_keys=True).encode()).hexdigest()
                        prefix_checks += 1
                    finally:
                        store.close()
    target = next(c for c in today['cells'] if c['code'] == '132026.SH')['variants'][NEW]
    assert [(f['market_time'],f['side'],f['price'],f['quantity_bonds'])
            for f in target['fills'][:2]] == [
        ('09:30:18','buy',134.875,1_000), ('09:32:18','sell',135.948,1_000)]
    for frame in target['frames']:
        if '09:30:18' <= frame['time'] < '09:32:18':
            assert frame['inventory'] == 2_000
            assert any(o['side']=='sell' and abs(o['price']-135.948)<1e-9
                       and o['quantity']==1_000 for o in frame['orders'])
    paths = ['src/zhaiquant/maker_paper.py', 'tests/test_priority_v271_discount_exit.py',
             'scripts/verify_priority_v271.py', 'scripts/replay_priority_candidate_matrix.py']
    paths += [str(ROOT/name) for name in (
        'priority_v271_predevelopment_manifest.json', 'priority_v271_before_baselines.json',
        'priority_v271_final_matrix_20260804_20260910.json',
        'priority_v271_recent_270_control.json', 'priority_v271_today_101500.json')]
    report = dict(old_profiles_preserved=len(manifest['profiles']),
        saved_baseline_cells=len(saved), complete_cells=len(final),
        changed_cells_vs_270=changed, prefix_checks=prefix_checks,
        today_cutoff='10:15:00', today=today_results, input_prefix_sha256=input_hashes,
        sha256={path:sha(path) for path in paths})
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('input_prefix_sha256','sha256')},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
