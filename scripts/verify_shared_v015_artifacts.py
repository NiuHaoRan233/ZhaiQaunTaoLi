"""Check published daily numbers and archived hashes, without rewriting artifacts."""
import hashlib
import json
import re
from pathlib import Path


def main():
    manifest = json.loads(Path('策略自我迭代优化/千张共享资金/maker_shared_1000_v0_15_研究归档清单_2026-09-07.json').read_text(encoding='utf-8'))
    paths = manifest['source_hashes_sha256'] | manifest['local_report_hashes_sha256']
    for name, digest in paths.items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest:
            raise AssertionError(f'Archive hash differs: {name}')
    report = json.loads(Path('output/research/shared_v015r2_matrix_20260804_20260904.json').read_text(encoding='utf-8'))
    table = Path(manifest['report']).read_text(encoding='utf-8')
    days = {}
    for line in table.splitlines():
        if re.match(r'^\| \d{2}-\d{2} \|', line):
            fields = [s.strip() for s in line.split('|')[1:-1]]
            days[fields[0]] = [float(s.replace(',', '').replace('−', '-')) for s in fields[1:]]
    assert len(days) == 24
    for cell in report['cells']:
        expected = [cell[k]['trading_pnl'] for k in ('reference', 'parent', 'candidate')] + [cell['pnl_delta']]
        assert days[cell['market_date'][5:]] == expected, cell['market_date']
    assert round(sum(c['pnl_delta'] for c in report['cells']), 2) == 885.57
    print(json.dumps(dict(archive_hashes_exact=len(paths), published_daily_rows_exact=len(days))))


if __name__ == '__main__':
    main()
