"""Read-only coverage supplement; missing mapped stock data is not substituted."""
import json
import sqlite3
from pathlib import Path

from zhaiquant.config import load_config, maker_underlying_stock_code


def main():
    output = Path('output/research/shared_v014_data_coverage.json')
    if output.exists():
        raise FileExistsError(output)
    config = load_config('config.toml')
    dates = json.loads(Path('output/research/shared_v014_parent_freeze_20260907.json')
                       .read_text(encoding='utf-8'))['dates'] + ['2026-09-07']
    result = dict(source_readonly=True, sample_out_evidence=False, cells=[])
    source = sqlite3.connect(config.storage.database.resolve().as_uri()+'?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    source.execute('PRAGMA query_only=ON')
    source.execute('BEGIN')
    try:
        for day in dates:
            cutoff = '14:45:00.999' if day == '2026-09-07' else '23:59:59.999'
            for bond in ('132026.SH', '132024.SH'):
                stock = maker_underlying_stock_code(config, bond)
                row = source.execute(
                    'SELECT COUNT(*) n, MIN(market_time) first, MAX(market_time) last '
                    'FROM raw_ticks WHERE market_date=? AND code=? AND market_time<=?',
                    (day, stock, cutoff)).fetchone()
                result['cells'].append(dict(market_date=day, bond_code=bond,
                    underlying_stock_code=stock, underlying_stock_ticks=row['n'],
                    underlying_stock_data_available=row['n'] > 0,
                    first_market_time=row['first'], last_market_time=row['last'],
                    cutoff_time=cutoff, count_semantics='saved raw_ticks, not a completeness certification'))
    finally:
        source.close()
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(output=str(output), missing=[(c['market_date'], c['underlying_stock_code'])
        for c in result['cells'] if not c['underlying_stock_data_available']]), ensure_ascii=False))


if __name__ == '__main__':
    main()
