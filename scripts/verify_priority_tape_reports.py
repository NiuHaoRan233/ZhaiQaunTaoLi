"""Verify saved baselines and the 2026-09-07 ordinary-exit case, without a DB."""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from zhaiquant.maker_paper import REALTIME_COMPARISON_POLICIES


FIELDS = (
    'fills', 'fill_count', 'order_count', 'terminal_inventory', 'trading_pnl',
    'weighted_customer_base_short_metrics', 'extra_inventory_metrics',
)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def indexed(report):
    return {(c['market_date'], c['bond_code']): c for c in report['cells']}


def compare(saved, replay, saved_prefix='candidate', replay_prefix='parent'):
    assert saved.keys() == replay.keys(), 'Different matrix cells'
    for key, cell in saved.items():
        for field in FIELDS:
            assert cell[saved_prefix+'_'+field] == replay[key][replay_prefix+'_'+field], (key, field)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline', 'first-report', 'revision-report', 'manifest', 'target-report', 'output'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--intermediate-reports', nargs='*', default=[])
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise FileExistsError('Preserve earlier verification evidence: '+str(output))
    manifest = read(args.manifest)
    for path, digest in manifest['sha256'].items():
        if Path(path).name != 'maker_paper.py':
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, path
    for model, saved in manifest['profiles'].items():
        current = json.loads(json.dumps(asdict(REALTIME_COMPARISON_POLICIES[model])))
        assert all(current[key] == value for key, value in saved.items()), model
        assert not current['enable_ordinary_tape_turnover_regime'], model
        assert not current['enable_ordinary_tape_horizontal_recovery'], model
        assert not current['preserve_ordinary_tape_current_wide_corridor'], model
    baseline, first, revision = (read(p) for p in (
        args.baseline, args.first_report, args.revision_report))
    assert baseline['candidate_model_id'] == first['parent_model_id']
    old, initial, final = map(indexed, (baseline, first, revision))
    compare(old, initial)
    chain = [first, *(read(p) for p in args.intermediate_reports), revision]
    for parent, child in zip(chain, chain[1:]):
        assert parent['candidate_model_id'] == child['parent_model_id']
        compare(indexed(parent), indexed(child))
    for report in chain:
        isolation = report['branch_isolation']
        assert isolation['queue_all_cells_identical'] and isolation['windfall_all_cells_identical']
    cells = []
    totals = {}
    for (day, code), cell in final.items():
        parent = old[(day, code)]
        result = dict(date=day, code=code,
            identical_fills=parent['candidate_fills'] == cell['candidate_fills'],
            pnl_delta=round(cell['candidate_trading_pnl']-parent['candidate_trading_pnl'], 8),
            old_inventory=parent['candidate_terminal_inventory'],
            new_inventory=cell['candidate_terminal_inventory'])
        cells.append(result)
        total = totals.setdefault(code, {})
        for label, data in (('baseline', parent), ('revision', cell)):
            for metric in ('trading_pnl', 'fill_count', 'order_count'):
                key = label+'_'+metric
                total[key] = total.get(key, 0)+data['candidate_'+metric]
            for label2, metric in (('extra', 'extra_inventory_metrics'),
                                  ('short', 'weighted_customer_base_short_metrics')):
                key = label+'_'+label2+'_equivalent_seconds'
                total[key] = total.get(key, 0)+data['candidate_'+metric]['equivalent_full_lot_seconds']
    target = read(args.target_report)
    case = next(c for c in target['cells'] if c['date']=='2026-09-07' and c['code']=='132026.SH')
    model = revision['candidate_model_id']
    data = case['variants'][model]
    fills = data['fills']
    for time, side, price, quantity in (
        ('10:21:01', 'buy', 136.707, 1000), ('10:21:10', 'sell', 136.699, 1000),
        ('10:28:01', 'buy', 136.351, 1000), ('10:28:46', 'sell', 136.199, 1000),
    ):
        assert any(f['market_time']==time and f['side']==side and
                   abs(f['price']-price)<1e-9 and f['quantity_bonds']==quantity for f in fills), time
    frames = {f['time']: f for f in data['frames']}
    assert any(o['side']=='sell' and abs(o['price']-136.699)<1e-9
               for o in frames['10:21:04']['orders'])
    assert any(o['side']=='buy' and abs(o['price']-136.202)<1e-9
               for o in frames['10:27:37']['orders'])
    checked = 0
    for f in data['frames']:
        if any(start<=f['time']<end for start,end in (
            ('10:22:52','10:24:49'), ('10:28:10','10:28:46'))):
            extra = max(f['inventory']-1000, 0)
            assert sum(o['quantity'] for o in f['orders'] if o['side']=='sell') >= extra, f['time']
            checked += 1
        if '10:29:00' <= f['time'] <= '10:32:22':
            assert f['inventory']==1000 and not f['orders'], f['time']
    assert checked > 0
    report = dict(model_id=model, old_profiles_unchanged=len(manifest['profiles']),
        frozen_baseline_cells=len(old), first_version_preserved=True,
        branch_isolation=revision['branch_isolation'], target_exit_frames_checked=checked,
        target_case_checks_passed=True, totals=totals, cells=cells,
        inputs_sha256={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (
            args.baseline,args.first_report,*args.intermediate_reports,
            args.revision_report,args.manifest,args.target_report)},
        implementation_sha256=hashlib.sha256(Path('src/zhaiquant/maker_paper.py').read_bytes()).hexdigest())
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('cells','inputs_sha256')},ensure_ascii=False))
    print('SHA256',hashlib.sha256(output.read_bytes()).hexdigest())


if __name__=='__main__':
    main()
