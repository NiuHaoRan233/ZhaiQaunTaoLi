"""Frozen v0.14/v0.13 comparison, temporary ledgers, read-only source SQLite."""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from zhaiquant import maker_paper
from zhaiquant.config import load_config
from zhaiquant.one_hand_maker_research import replay_one_hand_day, SHARED_THOUSAND_BONDS
from zhaiquant.shared_thousand_maker_v013_research import SharedThousandV013Allocator
from zhaiquant.shared_thousand_maker_v014_research import CausalObserver, economic_fills, risk_metrics, totals
from zhaiquant.shared_thousand_maker_v015_research import SharedThousandV015Allocator, AllocationParametersV015


class Observer:
    """Wrap each original call once; no extra decision/context invocation."""
    def __init__(self, frames=False):
        self.frames = CausalObserver() if frames else None
        self.terminal_ts_ms = 0
        self.fill_checks = []
        self.choice = {}

    def __call__(self, allocator, tick):
        if tick is None:
            native_choose = allocator._choose
            native_fill = allocator._allow_buy_fill
            def choose(scores, *, market_ts_ms):
                result = native_choose(scores, market_ts_ms=market_ts_ms)
                self.choice = dict(scores)
                return result
            def fill(code, account, tick, order, quantity, kind, reason):
                self.choice = {}
                result = native_fill(code, account, tick, order, quantity, kind, reason)
                score = self.choice.get(code)
                self.fill_checks.append(dict(code=code, market_ts_ms=tick.market_ts_ms,
                    market_time=tick.market_time, quantity=quantity, allowed=result,
                    score=score.public() if score is not None else None))
                return result
            allocator._choose = choose
            allocator._allow_buy_fill = fill
        else:
            self.terminal_ts_ms = max((a.last_market_ts_ms for engine in allocator.engines.values()
                                      for a in engine.accounts.values()), default=0)
        if self.frames:
            self.frames(allocator, tick)


def slot_metrics(cell, terminal_ts_ms, checks):
    episodes = []
    inventory = 0
    episode = None
    predictions = {(c['code'], c['market_ts_ms']): c['score'] for c in checks if c['allowed']}
    for fill in cell['fills']:
        if fill['side'] == 'buy':
            if inventory <= 1e-9:
                episode = dict(code=fill['bond_code'], entry_ts=fill['market_ts_ms'],
                    entry_price=fill['price'], first_quantity=fill['quantity'], buy_quantity=0,
                    cash_flow=0, prediction=predictions.get((fill['bond_code'], fill['market_ts_ms'])))
            inventory += fill['quantity']
            episode['buy_quantity'] += fill['quantity']
            episode['cash_flow'] -= fill['price']*fill['quantity']
        else:
            inventory -= fill['quantity']
            episode['cash_flow'] += fill['price']*fill['quantity']
            if inventory <= 1e-9:
                episode.update(exit_ts=fill['market_ts_ms'], censored=False,
                    holding_seconds=(fill['market_ts_ms']-episode['entry_ts'])/1000)
                episodes.append(episode)
                episode = None
    if episode:
        episode.update(exit_ts=None, censored=True,
                       holding_seconds=(terminal_ts_ms-episode['entry_ts'])/1000)
        episodes.append(episode)
    for row in episodes:
        row['cash_flow'] = round(row['cash_flow'], 6)
    return dict(locked_slot_seconds=round(sum(e['holding_seconds'] for e in episodes), 3),
                completed_episodes=sum(not e['censored'] for e in episodes),
                long_lock_episodes=sum(e['holding_seconds'] > 1800 for e in episodes),
                maximum_lock_seconds=max((e['holding_seconds'] for e in episodes), default=0),
                episodes=episodes)


def replay(config, day, label, cutoff, frames):
    policies = dict(reference=maker_paper.SHARED_THOUSAND_POLICY_V013_CANDIDATE,
                    parent=maker_paper.SHARED_THOUSAND_POLICY_V014_CANDIDATE,
                    candidate=maker_paper.SHARED_THOUSAND_POLICY_V015_CANDIDATE)
    observer = Observer(frames)
    child = label == 'candidate'
    cell = replay_one_hand_day(config, market_date=day, priority_policy=policies[label],
        allocator_class=SharedThousandV015Allocator if child else SharedThousandV013Allocator,
        parameters=AllocationParametersV015() if child else None,
        shared_capacity_bonds=SHARED_THOUSAND_BONDS, cutoff_time=cutoff, observer=observer)
    cell['risk_metrics'] = risk_metrics(cell)
    cell['slot_metrics'] = slot_metrics(cell, observer.terminal_ts_ms, observer.fill_checks)
    cell['observed_fill_checks'] = observer.fill_checks
    if observer.frames:
        cell['frames'] = observer.frames.frames
    return cell


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config.toml')
    parser.add_argument('--dates', nargs='+')
    parser.add_argument('--cutoff-time')
    parser.add_argument('--observe', action='store_true')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(output)
    frozen = json.loads(Path('output/research/shared_v015_parent_freeze_20260907.json').read_text(encoding='utf-8'))
    for name, values in frozen['profiles'].items():
        if json.loads(json.dumps(asdict(getattr(maker_paper, name)))) != values:
            raise AssertionError(f'Immutable profile changed: {name}')
    for path, digest in frozen['source_hashes'].items():
        if path == 'src/zhaiquant/maker_paper.py':
            continue  # Only a new profile identity was appended; fields above checked.
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
            raise AssertionError(f'Immutable source/config/report changed: {path}')
    sources = tuple(frozen['source_hashes']) + (
        'src/zhaiquant/shared_thousand_maker_v015_research.py', 'scripts/replay_shared_v015.py')
    result = dict(offline_only=True, source_readonly=True, model_id=maker_paper.SHARED_THOUSAND_POLICY_V015_CANDIDATE.model_id,
        parent_model_id=maker_paper.SHARED_THOUSAND_POLICY_V014_CANDIDATE.model_id,
        parameters=asdict(AllocationParametersV015()), cutoff_time=args.cutoff_time,
        source_hashes={p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in sources}, cells=[])
    saved_path = ('output/research/shared_v014_target_20260907_144500.json' if args.cutoff_time
                  else 'output/research/shared_v014_matrix_20260804_20260904.json')
    saved = {c['market_date']: c for c in frozen['saved'][saved_path]}
    config = load_config(args.config)
    for day in args.dates or sorted(saved):
        pair = dict(market_date=day)
        for label in ('reference', 'parent', 'candidate'):
            pair[label] = replay(config, day, label, args.cutoff_time, args.observe)
            if label != 'candidate' and day in saved:
                old = saved[day]['parent' if label == 'reference' else 'candidate']
                if (economic_fills(pair[label]) != economic_fills(old)
                    or pair[label]['trading_pnl'] != old['trading_pnl']
                    or pair[label]['order_counts'] != old['order_counts']):
                    raise AssertionError(f'Saved {label} changed: {day}')
                pair[label+'_frozen_exact'] = True
        pair['pnl_delta'] = round(pair['candidate']['trading_pnl']-pair['parent']['trading_pnl'], 6)
        pair['same_fills'] = economic_fills(pair['candidate']) == economic_fills(pair['parent'])
        result['cells'].append(pair)
        print(json.dumps(dict(day=day, delta=pair['pnl_delta'], same_fills=pair['same_fills'],
                              pnl={k: pair[k]['trading_pnl'] for k in ('reference', 'parent', 'candidate')})), flush=True)
    result['totals'] = {}
    for label in ('reference', 'parent', 'candidate'):
        cells = [c[label] for c in result['cells']]
        result['totals'][label] = dict(**totals(cells),
            locked_slot_seconds=round(sum(c['slot_metrics']['locked_slot_seconds'] for c in cells), 3),
            long_lock_episodes=sum(c['slot_metrics']['long_lock_episodes'] for c in cells))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(output=str(output), totals=result['totals'])), flush=True)


if __name__ == '__main__':
    main()
