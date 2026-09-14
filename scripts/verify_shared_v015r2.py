"""Final noninterference, ancestry and immutable-source checks for v0.15 r2."""
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from zhaiquant import maker_paper
from zhaiquant.config import load_config
from zhaiquant.one_hand_maker_research import replay_one_hand_day, SHARED_THOUSAND_BONDS
from zhaiquant.shared_thousand_maker_v014_research import economic_fills
from zhaiquant.shared_thousand_maker_v015_research import AllocationParametersV015, replay_shared_thousand_v015_day
from zhaiquant.shared_thousand_maker_v015r2_research import POLICY, SharedThousandV015R2Allocator


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def exact(a, b):
    return (economic_fills(a) == economic_fills(b) and a['trading_pnl'] == b['trading_pnl']
            and a['order_counts'] == b['order_counts'])


def main():
    output = Path('output/research/shared_v015r2_verification.json')
    if output.exists():
        raise FileExistsError(output)
    frozen = read('output/research/shared_v015_parent_freeze_20260907.json')
    matrix = read('output/research/shared_v015r2_matrix_20260804_20260904.json')
    target = read('output/research/shared_v015r2_target_20260907_144500.json')['cells'][0]
    prominent = read('output/research/shared_v015r2_prominent_days.json')
    profiles = {n: json.loads(json.dumps(asdict(getattr(maker_paper, n)))) == v for n, v in frozen['profiles'].items()}
    assert all(profiles.values())
    core = Path('src/zhaiquant/maker_paper.py').read_bytes()
    restored = re.sub(rb'SHARED_THOUSAND_POLICY_V015(?:_R2)?_CANDIDATE = replace\(\n.*?\n\)\n', b'', core, flags=re.S)
    core_exact = hashlib.sha256(restored).hexdigest() == frozen['source_hashes']['src/zhaiquant/maker_paper.py']
    assert core_exact
    immutable = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
                 for p, h in frozen['source_hashes'].items() if p != 'src/zhaiquant/maker_paper.py'}
    assert all(immutable.values())
    first_report = read('output/research/shared_v015_matrix_20260804_20260904.json')
    first_hashes = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
                    for p, h in first_report['source_hashes'].items() if p != 'src/zhaiquant/maker_paper.py'}
    assert all(first_hashes.values())
    config = load_config('config.toml')
    no_observer = replay_one_hand_day(config, market_date='2026-09-07', priority_policy=POLICY,
        allocator_class=SharedThousandV015R2Allocator, parameters=AllocationParametersV015(),
        shared_capacity_bonds=SHARED_THOUSAND_BONDS, cutoff_time='14:45:00')
    observer_exact = exact(no_observer, target['candidate'])
    assert observer_exact
    first = replay_shared_thousand_v015_day(config, market_date='2026-09-07', cutoff_time='14:45:00')
    first_replay_exact = exact(first, target['first'])
    assert first_replay_exact
    frame_exact = {p['market_date']: exact(p['candidate'], next(
        c['candidate'] for c in matrix['cells'] if c['market_date'] == p['market_date'])) for p in prominent['cells']}
    assert all(frame_exact.values())
    totals = matrix['totals']
    counts = Counter()
    reasons = Counter()
    reselect = {}
    for pair in matrix['cells']:
        counts.update(pair['candidate']['allocation_v015_metrics'])
        reasons.update(pair['candidate']['allocation_v013_metrics']['decision_reason_counts'])
    for label in ('reference', 'parent', 'first', 'candidate'):
        reselect[label] = {key: sum(p[label]['allocation_v013_metrics'][key] for p in matrix['cells'])
                          for key in ('effective_cross_bond_reselections', 'cross_bond_reselections_via_cash')}
    results = dict(core_old_implementation_exact=core_exact, old_profiles=profiles,
        immutable_hashes=immutable, first_implementation_hashes=first_hashes,
        first_replay_exact=first_replay_exact, target_observer_exact=observer_exact,
        prominent_observer_exact=frame_exact, counters=dict(counts), reasons=dict(reasons),
        effective_reselections=reselect,
        days_up=sum(c['pnl_delta'] > 0 for c in matrix['cells']),
        days_down=sum(c['pnl_delta'] < 0 for c in matrix['cells']),
        days_same=sum(c['pnl_delta'] == 0 for c in matrix['cells']),
        same_fill_days=sum(c['same_fills'] for c in matrix['cells']),
        exposure_change_pct=100*(totals['candidate']['exposure_seconds']/totals['parent']['exposure_seconds']-1),
        locked_slot_change_pct=100*(totals['candidate']['locked_slot_seconds']/totals['parent']['locked_slot_seconds']-1))
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in results.items() if k not in ('old_profiles', 'immutable_hashes', 'first_implementation_hashes', 'reasons')}), flush=True)
    for c in matrix['cells']:
        print('| '+c['market_date'][5:]+' | '+' | '.join(f"{c[k]['trading_pnl']:,.2f}" for k in ('reference', 'parent', 'candidate'))+f" | {c['pnl_delta']:+,.2f} |")


if __name__ == '__main__':
    main()
