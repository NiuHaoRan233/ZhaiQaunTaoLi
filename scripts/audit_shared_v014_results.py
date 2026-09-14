"""Read the frozen v0.14 reports; expose full economic differences and gaps."""
from __future__ import annotations

import hashlib
import json
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path('output/research')
KEYS = ('market_ts_ms', 'side', 'price', 'quantity', 'fill_reason', 'inventory_after')


def fills(rows):
    return [tuple(row[k] for k in KEYS) for row in rows]


def differences(pair):
    results = []
    for code in ('132026.SH', '132024.SH'):
        a, b = ([f for f in pair[variant]['fills'] if f['bond_code'] == code]
                for variant in ('parent', 'candidate'))
        for tag, i, j, k, l in SequenceMatcher(a=fills(a), b=fills(b), autojunk=False).get_opcodes():
            if tag == 'equal':
                continue
            results.append(dict(code=code, parent=a[i:j], candidate=b[k:l]))
    return results


def main():
    names = ('shared_v014_matrix_20260804_20260904.json',
             'shared_v014_target_20260907_144500.json',
             'shared_v014_prominent_days.json', 'shared_v014_positive_days.json')
    reports = {name: json.loads((ROOT/name).read_text(encoding='utf-8')) for name in names}
    matrix, target = reports[names[0]], reports[names[1]]['cells'][0]
    old = json.loads((ROOT/'shared013_followup_20260907_144500.json').read_text(encoding='utf-8'))
    parent = target['parent']
    # Native-runtime audit vs research allocator: same ordering, IDs, economic fields.
    exact = ([[f['strategy_id']] + list(v) for f, v in zip(parent['fills'], fills(parent['fills']))]
             == [[f['strategy_id']] + list(v) for f, v in zip(old['fills'], fills(old['fills']))])
    if not exact:
        raise AssertionError('Today does not reproduce the saved native 66-fill parent')
    result = dict(native_today_66_fills_exact=exact, historical_days=len(matrix['cells']),
        all_frozen_parent_days_exact=all(c['frozen_parent_exact'] for c in matrix['cells']),
        old_profiles_unchanged=all(matrix['old_profile_checks'].values()),
        old_profile_count=len(matrix['old_profile_checks']),
        positive_days=sum(c['pnl_delta'] > 1e-6 for c in matrix['cells']),
        negative_days=sum(c['pnl_delta'] < -1e-6 for c in matrix['cells']),
        same_fill_days=sum(c['same_fills'] for c in matrix['cells']),
        daily=[dict(date=c['market_date'], parent=c['parent']['trading_pnl'],
                    candidate=c['candidate']['trading_pnl'], delta=c['pnl_delta'],
                    exposure_delta=round(c['candidate']['equivalent_full_slot_exposure_seconds']
                                         - c['parent']['equivalent_full_slot_exposure_seconds'], 3))
               for c in matrix['cells']],
        source_hashes={name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in names},
        differences={c['market_date']: differences(c) for c in matrix['cells'] if not c['same_fills']},
        today_differences=differences(target), gap_audit={}, target_frames={})
    for variant in ('parent', 'candidate'):
        frames = target[variant]['frames']
        checks = {}
        for kind in ('low_bid_reversion', 'session_resilient_value_entry'):
            held = [f for f in frames if any(l['kind'] == kind and l['opened_ms'] < f['ts'] for l in f['lots'])]
            checks[kind] = dict(held_frames=len(held), no_sell_frames=sum(
                not any(o['side'] == 'sell' for o in f['orders']) for f in held))
        result['gap_audit'][variant] = checks
        result['target_frames'][variant] = [f for f in frames if f['code'] == '132026.SH'
            and f['time'] in ('09:53:52', '09:54:01', '09:54:13', '09:54:16', '09:55:01', '09:55:10',
                             '10:01:31', '10:05:34', '10:21:04', '10:27:37', '10:27:58', '10:28:46')]
    path = ROOT/'shared_v014_result_audit.json'
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in ('differences', 'target_frames', 'today_differences')},
                     ensure_ascii=False, indent=2))
    for date, diffs in result['differences'].items():
        print(json.dumps(dict(date=date, differences=[dict(code=d['code'], **{
            variant: [[f['market_time'], f['side'], f['price'], f['quantity']] for f in d[variant]]
            for variant in ('parent', 'candidate')}) for d in diffs]), ensure_ascii=False))


if __name__ == '__main__':
    main()
