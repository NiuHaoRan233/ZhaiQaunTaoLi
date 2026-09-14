"""Freeze saved v0.13/v0.14 evidence before developing offline v0.15."""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from zhaiquant import maker_paper


def main():
    output = Path('output/research/shared_v015_parent_freeze_20260907.json')
    if output.exists():
        raise FileExistsError(output)
    paths = (
        'output/research/shared_v014_matrix_20260804_20260904.json',
        'output/research/shared_v014_target_20260907_144500.json',
        'src/zhaiquant/maker_paper.py',
        'src/zhaiquant/one_hand_maker_research.py',
        'src/zhaiquant/shared_thousand_maker_v02_research.py',
        'src/zhaiquant/shared_thousand_maker_v03_research.py',
        'src/zhaiquant/shared_thousand_maker_v013_research.py',
        'src/zhaiquant/shared_thousand_maker_v014_research.py',
        'config.toml', 'config.example.toml',
    )
    profiles = {name: asdict(value) for name, value in vars(maker_paper).items()
                if isinstance(value, maker_paper.MakerPolicyProfile)}
    keys = ('market_date', 'fills', 'trading_pnl', 'order_counts',
            'terminal_inventory_bonds', 'equivalent_full_slot_exposure_seconds')
    saved = {}
    for path in paths[:2]:
        report = json.loads(Path(path).read_text(encoding='utf-8'))
        saved[path] = [dict(market_date=c['market_date'], **{
            label: {k: c[label][k] for k in keys} for label in ('parent', 'candidate')
        }) for c in report['cells']]
    result = dict(parent_model_id=maker_paper.SHARED_THOUSAND_POLICY_V014_CANDIDATE.model_id,
                  reference_model_id=maker_paper.SHARED_THOUSAND_POLICY_V013_CANDIDATE.model_id,
                  source_hashes={p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths},
                  profiles=profiles, saved=saved)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(output=str(output), profiles=len(profiles),
                         days={p: len(c) for p, c in saved.items()}), ensure_ascii=False))


if __name__ == '__main__':
    main()
