"""Verify the actual paper assignment and preservation of all prior-day ledgers."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from zhaiquant.config import load_config
from zhaiquant.shared_thousand_maker_v016_live import POLICY


def digest(connection, table, before):
    rows = connection.execute(f'SELECT * FROM {table} WHERE market_date<? ORDER BY rowid', (before,)).fetchall()
    return dict(rows=len(rows), sha256=hashlib.sha256(
        json.dumps([tuple(row) for row in rows], ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup', type=Path, default=Path('backups/zhaiquant-before-v016r2-20260908.sqlite3'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Refusing to overwrite evidence')
    config = load_config('config.toml')
    current = sqlite3.connect(config.storage.database.as_uri()+'?mode=ro', uri=True)
    previous = sqlite3.connect(args.backup.resolve().as_uri()+'?mode=ro', uri=True)
    current.row_factory = sqlite3.Row
    current.execute('BEGIN')
    tables = ('maker_paper_accounts', 'maker_paper_model_assignments', 'maker_paper_orders',
              'maker_paper_lots', 'maker_paper_fills')
    retained = {}
    for table in tables:
        old = digest(previous, table, '2026-09-08')
        new = digest(current, table, '2026-09-08')
        assert old == new, (table, old, new)
        retained[table] = new
    session = dict(current.execute('SELECT * FROM sessions ORDER BY started_at_utc DESC LIMIT 1').fetchone())
    persisted = json.loads(session.pop('config_json'))['maker_paper']['realtime_comparison_model_ids']
    assert persisted == list(config.maker_paper.realtime_comparison_model_ids)
    assert len(persisted) == 8 and persisted[-1] == POLICY.model_id
    assert session['status'] == 'running'
    assignments = [dict(r) for r in current.execute('''SELECT bond_code,strategy_id,model_id,
        model_version,parent_model_id FROM maker_paper_model_assignments WHERE market_date=?''', ('2026-09-08',))]
    assert len(assignments) == 16
    for code in config.maker_paper.bond_codes:
        assert {row['model_id'] for row in assignments if row['bond_code']==code} == set(persisted)
    own = [row for row in assignments if row['model_id']==POLICY.model_id]
    assert len(own) == 2 and all(row['parent_model_id']==POLICY.parent_model_id for row in own)
    errors = [dict(r) for r in current.execute('''SELECT event_type,message FROM app_events
        WHERE run_id=? AND level IN ('error','critical')''', (session['run_id'],))]
    assert not errors, errors
    heartbeat = current.execute('''SELECT details_json FROM app_events
        WHERE run_id=? AND event_type='heartbeat' ORDER BY id DESC LIMIT 1''', (session['run_id'],)).fetchone()
    heartbeat = json.loads(heartbeat[0]) if heartbeat else None
    if heartbeat:
        assert heartbeat['dropped_callbacks']==0
        rows = heartbeat['maker_paper']['accounts']
        assert len(rows)==16
        arrival_accounts = [row for row in rows if row['model_id']==POLICY.model_id]
        assert len(arrival_accounts)==2 and not any(row['arrival_execution']['failed'] for row in arrival_accounts)
    result = dict(session=session, models=persisted, assignments=assignments,
                  prior_days_exact=retained, heartbeat=heartbeat, errors=errors, verified=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(dict(session=session['run_id'], models=len(persisted), assignments=len(assignments),
                         old_tables_unchanged=retained, heartbeat=heartbeat is not None), ensure_ascii=False))
    current.close()
    previous.close()


if __name__ == '__main__':
    main()
