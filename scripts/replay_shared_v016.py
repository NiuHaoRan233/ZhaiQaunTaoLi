"""Read-only v0.16 execution repair; compare frozen v0.14 and v0.15 r2."""
import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from zhaiquant import maker_paper
from zhaiquant.config import load_config
from zhaiquant.one_hand_maker_research import replay_one_hand_day, SHARED_THOUSAND_BONDS
from zhaiquant.shared_thousand_maker_v014_research import risk_metrics, totals
from zhaiquant.shared_thousand_maker_v016_research import POLICY, CausalSharedMakerEngine, SharedThousandV016Allocator
from replay_shared_v015 import Observer, slot_metrics


def replay(config, day, cutoff=None, observe=False):
    observer = Observer(observe)
    cell = replay_one_hand_day(config, market_date=day, priority_policy=POLICY,
        allocator_class=SharedThousandV016Allocator, engine_class=CausalSharedMakerEngine,
        shared_capacity_bonds=SHARED_THOUSAND_BONDS, cutoff_time=cutoff, observer=observer)
    cell['risk_metrics'] = risk_metrics(cell)
    cell['slot_metrics'] = slot_metrics(cell, observer.terminal_ts_ms, observer.fill_checks)
    cell['observed_fill_checks'] = observer.fill_checks
    if observer.frames:
        cell['frames'] = observer.frames.frames
    return cell


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent-report', default='output/research/shared_v015r2_matrix_20260804_20260904.json')
    parser.add_argument('--output', required=True)
    parser.add_argument('--dates', nargs='+')
    parser.add_argument('--observe', action='store_true')
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(output)
    frozen = json.loads(Path('output/research/shared_v016_parent_freeze_20260907.json').read_text(encoding='utf-8'))
    for name, values in frozen['profiles'].items():
        if json.loads(json.dumps(asdict(getattr(maker_paper, name)))) != values:
            raise AssertionError(f'Old profile changed: {name}')
    for p, digest in frozen['source_hashes'].items():
        if p in frozen['modified_source_text']:
            continue
        if hashlib.sha256(Path(p).read_bytes()).hexdigest() != digest:
            raise AssertionError(f'Immutable source/config/report changed: {p}')
    old = json.loads(Path(args.parent_report).read_text(encoding='utf-8'))
    sources = list(frozen['source_hashes']) + ['src/zhaiquant/shared_thousand_maker_v016_research.py',
        'scripts/replay_shared_v016.py', 'scripts/freeze_shared_v016_parent.py']
    result = dict(model_id=POLICY.model_id, parent_model_id=POLICY.parent_model_id,
        offline_only=True, source_readonly=True, cutoff_time=old['cutoff_time'],
        source_hashes={p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sources},
        execution_assumptions=['strict later market timestamp for passive orders',
            'market-time causal merge; not measured receipt/latency replay',
            'L1 inferred direction and first-priority hypothetical fills',
            'active IOC on visible book at zero latency; limit-price conservative fill',
            'no fees, calibrated queue, or market impact'], cells=[])
    config = load_config('config.toml')
    for pair in old['cells']:
        day = pair['market_date']
        if args.dates and day not in args.dates:
            continue
        candidate = replay(config, day, old['cutoff_time'], args.observe)
        cell = dict(market_date=day, reference=pair['reference'], parent=pair['parent'],
            previous=pair['candidate'], candidate=candidate,
            pnl_delta=round(candidate['trading_pnl']-pair['parent']['trading_pnl'], 6))
        result['cells'].append(cell)
        print(json.dumps(dict(day=day, pnl=candidate['trading_pnl'], delta=cell['pnl_delta'])), flush=True)
    result['totals'] = {}
    for label in ('reference', 'parent', 'previous', 'candidate'):
        cells = [c[label] for c in result['cells']]
        result['totals'][label] = dict(**totals(cells),
            locked_slot_seconds=round(sum(c['slot_metrics']['locked_slot_seconds'] for c in cells), 3),
            long_lock_episodes=sum(c['slot_metrics']['long_lock_episodes'] for c in cells))
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(dict(output=str(output), totals=result['totals'])), flush=True)


if __name__ == '__main__':
    main()
