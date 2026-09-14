"""Check observer noninterference and saved baseline/candidate identities."""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from zhaiquant import maker_paper
from zhaiquant.config import load_config
from zhaiquant.shared_thousand_maker_v014_research import replay_shared_thousand_v014_day, economic_fills
from zhaiquant.shared_thousand_maker_v015_research import replay_shared_thousand_v015_day


def main():
    output = Path('output/research/shared_v015_verification.json')
    if output.exists():
        raise FileExistsError(output)
    freeze = json.loads(Path('output/research/shared_v015_parent_freeze_20260907.json').read_text(encoding='utf-8'))
    target = json.loads(Path('output/research/shared_v015_target_20260907_144500.json').read_text(encoding='utf-8'))['cells'][0]
    matrix = json.loads(Path('output/research/shared_v015_matrix_20260804_20260904.json').read_text(encoding='utf-8'))
    profiles = {name: json.loads(json.dumps(asdict(getattr(maker_paper, name)))) == values
                for name, values in freeze['profiles'].items()}
    if not all(profiles.values()):
        raise AssertionError('Immutable profiles changed')
    config = load_config('config.toml')
    exact = {}
    for label, policy in (
        ('reference', maker_paper.SHARED_THOUSAND_POLICY_V013_CANDIDATE),
        ('parent', maker_paper.SHARED_THOUSAND_POLICY_V014_CANDIDATE),
        ('candidate', maker_paper.SHARED_THOUSAND_POLICY_V015_CANDIDATE),
    ):
        kwargs = dict(config=config, market_date='2026-09-07', cutoff_time='14:45:00', observe=False)
        cell = (replay_shared_thousand_v015_day(**kwargs) if label == 'candidate'
                else replay_shared_thousand_v014_day(**kwargs, policy=policy))
        exact[label] = (economic_fills(cell) == economic_fills(target[label])
                        and cell['order_counts'] == target[label]['order_counts']
                        and cell['trading_pnl'] == target[label]['trading_pnl'])
        if not exact[label]:
            raise AssertionError(f'Observer changed {label}')
    prominent = json.loads(Path('output/research/shared_v015_prominent_days.json').read_text(encoding='utf-8'))
    prominent_exact = {}
    for pair in prominent['cells']:
        saved = next(c for c in matrix['cells'] if c['market_date'] == pair['market_date'])
        prominent_exact[pair['market_date']] = all(
            economic_fills(pair[label]) == economic_fills(saved[label])
            and pair[label]['trading_pnl'] == saved[label]['trading_pnl']
            and pair[label]['order_counts'] == saved[label]['order_counts']
            for label in ('reference', 'parent', 'candidate'))
    if not all(prominent_exact.values()):
        raise AssertionError('Frame observer changed prominent days')
    immutable = {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest
                 for p, digest in freeze['source_hashes'].items() if p != 'src/zhaiquant/maker_paper.py'}
    if not all(immutable.values()):
        raise AssertionError('Old source/config/report changed')
    block = '''SHARED_THOUSAND_POLICY_V015_CANDIDATE = replace(
    SHARED_THOUSAND_POLICY_V014_CANDIDATE,
    model_id="maker_shared_1000_v0_15_candidate",
    model_version="0.15-candidate",
    parent_model_id="maker_shared_1000_v0_14_candidate",
    # Offline only: native trading is unchanged; use the v0.15 allocator.
)
'''.encode()
    core = Path('src/zhaiquant/maker_paper.py').read_bytes()
    restored_core = hashlib.sha256(core.replace(block, b'', 1)).hexdigest() == freeze['source_hashes']['src/zhaiquant/maker_paper.py']
    if not restored_core:
        raise AssertionError('Core changed beyond new identity declaration')
    result = dict(old_profiles=profiles, immutable_hashes=immutable,
        core_identity_only_exact=restored_core, target_observer_exact=exact,
        prominent_observer_exact=prominent_exact,
        historical_saved_parent_exact=all(c['parent_frozen_exact'] for c in matrix['cells']),
        historical_saved_reference_exact=all(c['reference_frozen_exact'] for c in matrix['cells']))
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(output=str(output), core_identity_only_exact=restored_core,
                         target_observer_exact=exact, prominent_observer_exact=prominent_exact)))


if __name__ == '__main__':
    main()
