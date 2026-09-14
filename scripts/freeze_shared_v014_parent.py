"""Freeze the unmodified v0.13 profile and 24 shared-slot causal days."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from zhaiquant.config import load_config
from zhaiquant import maker_paper
from zhaiquant.shared_thousand_maker_v013_research import replay_shared_thousand_v013_day


def main():
    destination = Path('output/research/shared_v014_parent_freeze_20260907.json')
    if destination.exists():
        raise FileExistsError(destination)
    old_path = Path('output/research/shared_1000_v013_matrix_20260804_20260901.json')
    old = json.loads(old_path.read_text(encoding='utf-8'))
    dates = old['dates'] + ['2026-09-02', '2026-09-03', '2026-09-04']
    paths = [old_path, Path('config.toml'), Path('config.example.toml'),
             Path('src/zhaiquant/maker_paper.py'),
             Path('src/zhaiquant/shared_thousand_maker_v013_research.py'),
             Path('output/research/shared013_followup_20260907_144500.json')]
    result = dict(dates=dates, profiles={
        name: asdict(value) for name, value in vars(maker_paper).items()
        if isinstance(value, maker_paper.MakerPolicyProfile)
    }, source_hashes={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
                  cells=[], saved_baseline_checks=[])
    config = load_config('config.toml')
    fill_keys = ('bond_code', 'market_ts_ms', 'side', 'price', 'quantity',
                 'fill_reason', 'inventory_after')
    for day in dates:
        cell = replay_shared_thousand_v013_day(config, market_date=day)
        result['cells'].append(cell)
        saved = next((c['shared'] for c in old['cells'] if c['market_date'] == day), None)
        if saved is not None:
            same = (cell['trading_pnl'] == saved['trading_pnl'] and
                    [[f[k] for k in fill_keys] for f in cell['fills']] ==
                    [[f[k] for k in fill_keys] for f in saved['fills']])
            result['saved_baseline_checks'].append(dict(date=day, exact=same))
            if not same:
                raise AssertionError(f'Immutable saved baseline differs on {day}')
        print(json.dumps(dict(date=day, pnl=cell['trading_pnl'], fills=cell['fill_count']),
                         ensure_ascii=False), flush=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(output=str(destination), saved_checks=len(result['saved_baseline_checks']),
                          pnl=sum(c['trading_pnl'] for c in result['cells'])), ensure_ascii=False))


if __name__ == '__main__':
    main()
