"""Freeze existing identities and files before live-arrival integration."""
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from zhaiquant import maker_paper

destination = Path('output/research/shared_v016r2_before_20260908.json')
paths = [
    'src/zhaiquant/maker_paper.py', 'src/zhaiquant/config.py',
    'src/zhaiquant/shared_thousand_maker_v016_research.py',
    'src/zhaiquant/shared_thousand_maker_v013_research.py',
    'src/zhaiquant/shared_thousand_maker_v03_research.py',
    'src/zhaiquant/shared_thousand_maker_v02_research.py',
    'src/zhaiquant/one_hand_maker_research.py',
    'config.toml', 'config.example.toml',
    'output/research/shared_v016_matrix_20260804_20260904.json',
    'output/research/shared_v016_target_20260907_144500.json',
]
result = dict(
    profiles={name: asdict(value) for name, value in vars(maker_paper).items()
              if isinstance(value, maker_paper.MakerPolicyProfile)},
    files={path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths},
    config_before=Path('config.toml').read_text(encoding='utf-8'),
)
destination.parent.mkdir(parents=True, exist_ok=True)
with destination.open('x', encoding='utf-8') as handle:
    json.dump(result, handle, ensure_ascii=False, indent=2)
print(destination)
