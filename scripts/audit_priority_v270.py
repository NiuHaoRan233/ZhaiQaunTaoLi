"""Freeze the user-selected v2.63 baseline and verify selective v2.70 repairs."""
from __future__ import annotations

import argparse
from dataclasses import asdict

from audit_priority_v269 import read, save, sha, SOURCES, BASE
from zhaiquant.maker_paper import REALTIME_COMPARISON_POLICIES

OLD = 'maker_priority_v2_69_candidate'
NEW = 'maker_priority_v2_70_candidate_r2'
MANIFEST = 'output/research/priority_v270_predevelopment_manifest.json'
SAVED = 'output/research/priority_v269_matrix_20260804_20260904.json'
CONTROLS = ('enable_causal_ordinary_inventory_turnover',
            'recognize_consumed_recovered_support', 'quote_on_current_ordinary_recovery',
            'allow_horizontal_recovery_quote_with_bid_retreat')
FIELDS = ('fills', 'fill_count', 'order_count', 'terminal_inventory', 'trading_pnl',
          'path_bounds', 'weighted_customer_base_short_metrics', 'extra_inventory_metrics')
TARGET = 'output/research/priority_v270_final_target_preservation.json'
MORNING = 'output/research/priority_v270r2_morning_20260907_113005.json'
DETAILS = ('output/research/priority_v270r2_non_target_details.json',
           'output/research/priority_v270r2_copper_reentry_details.json',
           'output/research/priority_v270r2_morning_details.json')


def verify_targets(matrix):
    first = 'maker_priority_v2_70_candidate'
    saved_first = {(c['date'], c['code']): c['variants'][first]
        for c in read('output/research/priority_v270_prominent_days.json')['cells']}
    targets = {(c['date'], c['code']): c['variants'] for c in read(TARGET)['cells']}
    for key, variants in targets.items():
        for field in ('fills', 'frames', 'summary'):
            assert variants[first][field] == saved_first[key][field], (key, field)
        cell = next(c for c in matrix['cells'] if (c['market_date'], c['bond_code']) == key)
        assert variants[NEW]['fills'] == cell['candidate_fills']
    def has_fill(variant, time, side, price):
        return any(f['market_time'] == time and f['side'] == side and
                   abs(f['price']-price) < 1e-9 for f in variant['fills'])
    for day, time, price in (('2026-08-04', '10:15:30', 136.2),
                             ('2026-08-13', '14:05:21', 136.613)):
        assert has_fill(targets[(day, '132026.SH')][NEW], time, 'buy', price)
    assert has_fill(targets[('2026-08-13', '132024.SH')][NEW], '14:13:54', 'sell', 136.699)
    morning = read(MORNING)
    case = next(c for c in morning['cells'] if c['code'] == '132026.SH')
    variant = case['variants'][NEW]
    assert has_fill(variant, '10:21:10', 'sell', 136.699)
    assert has_fill(variant, '10:28:46', 'sell', 136.199)
    frames = 0
    for frame in variant['frames']:
        if '10:29:00' <= frame['time'] <= '10:32:22':
            assert frame['inventory'] == 1000 and not frame['orders'], frame['time']
        if any(start <= frame['time'] < end for start, end in (
            ('10:22:52', '10:24:49'), ('10:28:10', '10:28:46'))):
            assert sum(o['quantity'] for o in frame['orders'] if o['side'] == 'sell') >= max(
                0, frame['inventory']-1000), frame['time']
            frames += 1
    for path in DETAILS:
        for cell in read(path)['cells']:
            for variant in cell['variants'].values():
                for fill in variant['fills']:
                    if 'passive' in fill['fill_reason']:
                        assert fill['created_ms'] < fill['market_ts_ms'], fill
    return frames


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('freeze', 'verify'))
    parser.add_argument('--matrix')
    parser.add_argument('--preservation')
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.action == 'freeze':
        assert NEW not in REALTIME_COMPARISON_POLICIES
        save(MANIFEST, dict(baseline_model_id=BASE, previous_candidate_id=OLD,
            profiles={k: asdict(v) for k, v in REALTIME_COMPARISON_POLICIES.items()},
            sha256={p: sha(p) for p in (*SOURCES, SAVED,
                'src/zhaiquant/maker_paper.py', 'config.toml', 'config.example.toml')}))
        return
    if not all((args.matrix, args.preservation, args.output)):
        parser.error('verify requires --matrix, --preservation, --output')
    manifest = read(MANIFEST)
    for path, digest in manifest['sha256'].items():
        if path != 'src/zhaiquant/maker_paper.py':
            assert sha(path) == digest, path
    import json
    for model, profile in manifest['profiles'].items():
        current = json.loads(json.dumps(asdict(REALTIME_COMPARISON_POLICIES[model])))
        assert all(current[k] == value for k, value in profile.items()), model
        assert not current['recognize_consumed_recovered_support'], model
        assert not current['quote_on_current_ordinary_recovery'], model
        assert not current['allow_horizontal_recovery_quote_with_bid_retreat'], model
    base = asdict(REALTIME_COMPARISON_POLICIES[BASE])
    new = asdict(REALTIME_COMPARISON_POLICIES[NEW])
    assert new == dict(base, model_id=NEW, model_version='2.70-candidate-r2',
                      parent_model_id=BASE, **{c: True for c in CONTROLS})
    saved = {(c['market_date'], c['bond_code']): c for c in read(SAVED)['cells']}
    matrix, preservation = read(args.matrix), read(args.preservation)
    assert matrix['parent_model_id'] == preservation['parent_model_id'] == BASE
    assert matrix['candidate_model_id'] == NEW
    assert preservation['candidate_model_id'] == OLD
    for report, prefixes in ((matrix, ('parent',)), (preservation, ('parent', 'candidate'))):
        assert {(c['market_date'], c['bond_code']) for c in report['cells']} == saved.keys()
        for cell in report['cells']:
            previous = saved[(cell['market_date'], cell['bond_code'])]
            for prefix in prefixes:
                for field in FIELDS:
                    key = prefix + '_' + field
                    assert cell[key] == previous[key], (cell['market_date'], cell['bond_code'], key)
            assert cell['queue_branch_identical'] and cell['windfall_branch_identical']
    for cell in matrix['cells']:
        bounds = cell['candidate_path_bounds']
        assert bounds['minimum_inventory_bonds'] >= 0
        assert bounds['maximum_inventory_bonds'] <= 2000
        assert bounds['minimum_cash_cny'] >= -1e-7
    target_exit_frames = verify_targets(matrix)
    daily = []
    for day in sorted({c['market_date'] for c in matrix['cells']}):
        cells = [c for c in matrix['cells'] if c['market_date'] == day]
        before = sum(saved[(day, c['bond_code'])]['candidate_trading_pnl'] for c in cells)
        baseline = sum(c['parent_trading_pnl'] for c in cells)
        candidate = sum(c['candidate_trading_pnl'] for c in cells)
        daily.append(dict(date=day, baseline_263=baseline, previous_269=before,
                          candidate_270=candidate, delta_263=candidate-baseline,
                          delta_269=candidate-before))
    save(args.output, dict(old_profiles_unchanged=len(manifest['profiles']),
        saved_263_and_269_cells_exact=len(saved), daily=daily,
        target_exit_frames=target_exit_frames,
        totals_by_code=matrix['totals_by_code'],
        sha256={p: sha(p) for p in (MANIFEST, SAVED, args.matrix, args.preservation,
                                    TARGET, MORNING, *DETAILS,
                                    'src/zhaiquant/maker_paper.py')}))


if __name__ == '__main__':
    main()
