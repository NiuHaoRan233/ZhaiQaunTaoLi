"""Freeze immutable parents and settings before the causal execution repair."""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from zhaiquant import maker_paper


def main():
    output = Path('output/research/shared_v016_parent_freeze_20260907.json')
    paths = [
        'src/zhaiquant/maker_paper.py', 'src/zhaiquant/one_hand_maker_research.py',
        'src/zhaiquant/shared_thousand_maker_v02_research.py',
        'src/zhaiquant/shared_thousand_maker_v03_research.py',
        'src/zhaiquant/shared_thousand_maker_v013_research.py',
        'src/zhaiquant/shared_thousand_maker_v015_research.py',
        'src/zhaiquant/shared_thousand_maker_v015r2_research.py',
        'config.toml', 'config.example.toml',
        'output/research/shared_v014_matrix_20260804_20260904.json',
        'output/research/shared_v015r2_matrix_20260804_20260904.json',
        'output/research/shared_v015r2_target_20260907_144500.json',
    ]
    result = dict(parent_model_id='maker_shared_1000_v0_14_candidate',
        reference_model_id='maker_shared_1000_v0_15_candidate_r2',
        source_hashes={p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths},
        profiles={k: asdict(v) for k, v in vars(maker_paper).items()
                  if isinstance(v, maker_paper.MakerPolicyProfile)},
        modified_source_text={p: Path(p).read_text(encoding='utf-8') for p in paths[:2]})
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(dict(output=str(output), profiles=len(result['profiles']))))


if __name__ == '__main__':
    main()
