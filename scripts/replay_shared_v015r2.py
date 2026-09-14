"""Compare independently registered r2 to already verified immutable reports."""
import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from zhaiquant import maker_paper
from zhaiquant.config import load_config
from zhaiquant.one_hand_maker_research import replay_one_hand_day, SHARED_THOUSAND_BONDS
from zhaiquant.shared_thousand_maker_v014_research import risk_metrics, totals, economic_fills
from zhaiquant.shared_thousand_maker_v015_research import AllocationParametersV015
from zhaiquant.shared_thousand_maker_v015r2_research import POLICY, SharedThousandV015R2Allocator
from replay_shared_v015 import Observer, slot_metrics


def replay(config, day, cutoff, observe=False):
    observer = Observer(observe)
    cell = replay_one_hand_day(config, market_date=day, priority_policy=POLICY,
        allocator_class=SharedThousandV015R2Allocator, parameters=AllocationParametersV015(),
        shared_capacity_bonds=SHARED_THOUSAND_BONDS, cutoff_time=cutoff, observer=observer)
    cell['risk_metrics'] = risk_metrics(cell)
    cell['slot_metrics'] = slot_metrics(cell, observer.terminal_ts_ms, observer.fill_checks)
    cell['observed_fill_checks'] = observer.fill_checks
    if observer.frames:
        cell['frames'] = observer.frames.frames
    return cell


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent-report', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--observe', action='store_true')
    parser.add_argument('--dates', nargs='+')
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(output)
    old = json.loads(Path(args.parent_report).read_text(encoding='utf-8'))
    for p, digest in old['source_hashes'].items():
        if p == 'src/zhaiquant/maker_paper.py':
            continue
        if hashlib.sha256(Path(p).read_bytes()).hexdigest() != digest:
            raise AssertionError(f'Frozen first implementation/source changed: {p}')
    freeze = json.loads(Path('output/research/shared_v015_parent_freeze_20260907.json').read_text(encoding='utf-8'))
    for name, values in freeze['profiles'].items():
        if json.loads(json.dumps(asdict(getattr(maker_paper, name)))) != values:
            raise AssertionError(f'Old profile changed: {name}')
    result = dict(model_id=POLICY.model_id, parent_model_id=POLICY.parent_model_id,
        offline_only=True, source_readonly=True, cutoff_time=old['cutoff_time'],
        parameters=asdict(AllocationParametersV015()), baseline_report=args.parent_report,
        source_hashes={p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (
            args.parent_report, 'src/zhaiquant/maker_paper.py',
            'src/zhaiquant/shared_thousand_maker_v015_research.py',
            'src/zhaiquant/shared_thousand_maker_v015r2_research.py',
            'scripts/replay_shared_v015.py', 'scripts/replay_shared_v015r2.py',
            'config.toml', 'config.example.toml')}, cells=[])
    config = load_config('config.toml')
    for pair in old['cells']:
        day = pair['market_date']
        if args.dates and day not in args.dates:
            continue
        candidate = replay(config, day, old['cutoff_time'], args.observe)
        cell = dict(market_date=day, reference=pair['reference'], parent=pair['parent'],
            first=pair['candidate'], candidate=candidate,
            pnl_delta=round(candidate['trading_pnl']-pair['parent']['trading_pnl'], 6),
            same_fills=economic_fills(candidate) == economic_fills(pair['parent']))
        result['cells'].append(cell)
        print(json.dumps(dict(day=day, delta=cell['pnl_delta'], pnl=candidate['trading_pnl'])), flush=True)
    result['totals'] = {}
    for label in ('reference', 'parent', 'first', 'candidate'):
        cells = [c[label] for c in result['cells']]
        result['totals'][label] = dict(**totals(cells),
            locked_slot_seconds=round(sum(c['slot_metrics']['locked_slot_seconds'] for c in cells), 3),
            long_lock_episodes=sum(c['slot_metrics']['long_lock_episodes'] for c in cells))
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(output=str(output), totals=result['totals'])), flush=True)


if __name__ == '__main__':
    main()
