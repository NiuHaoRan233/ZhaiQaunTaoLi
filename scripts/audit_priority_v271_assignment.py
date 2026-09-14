"""Read-only audit of the tenth paper model, old ledgers and frozen policies."""
from collections import defaultdict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3

from audit_shared_v016r2_assignment import digest
from zhaiquant.config import load_config
from zhaiquant.maker_paper import REALTIME_COMPARISON_POLICIES


def main():
    day = '2026-09-11'
    model = 'maker_priority_v2_71_candidate'
    root = Path('output/research')
    before = json.loads((root/'priority_v271_live_before.json').read_text(encoding='utf-8'))
    config = load_config('config.toml')
    models = list(config.maker_paper.realtime_comparison_model_ids)
    assert len(models) == 10 and models[5] == model
    assert [m for m in models if m != model] == before['config']['maker_paper']['realtime_comparison_model_ids']
    for name, profile in before['profiles'].items():
        assert json.loads(json.dumps(asdict(REALTIME_COMPARISON_POLICIES[name]))) == profile, name
    assert hashlib.sha256(Path('src/zhaiquant/maker_paper.py').read_bytes()).hexdigest() == before['sha256']['src/zhaiquant/maker_paper.py']
    assert Path('config.toml').read_bytes().replace(b'  "maker_priority_v2_71_candidate",\r\n', b'').replace(
        b'  "maker_priority_v2_71_candidate",\n', b'') == (root/'priority_v271_before_live_config.toml').read_bytes()
    live = sqlite3.connect(config.storage.database.as_uri()+'?mode=ro', uri=True)
    old = sqlite3.connect(Path('backups/zhaiquant-before-v271-20260911.sqlite3').resolve().as_uri()+'?mode=ro', uri=True)
    live.row_factory = sqlite3.Row
    live.execute('BEGIN')
    result = {'date': day, 'model_id': model, 'old_profiles_unchanged': len(before['profiles']), 'prior_days_exact': {}}
    for table in ('maker_paper_accounts', 'maker_paper_model_assignments', 'maker_paper_orders', 'maker_paper_lots', 'maker_paper_fills'):
        previous, current = digest(old, table, day), digest(live, table, day)
        assert previous == current, table
        result['prior_days_exact'][table] = current
    fields = 'strategy_id,market_ts_ms,side,price,quantity,fill_reason,reference_tick_id,cash_after,inventory_after'
    def fills(conn):
        groups = defaultdict(list)
        for row in conn.execute(f'SELECT {fields} FROM maker_paper_fills WHERE market_date=? ORDER BY id', (day,)):
            groups[row[0]].append(tuple(row))
        return groups
    previous, current = fills(old), fills(live)
    for strategy, rows in previous.items():
        assert rows == current[strategy][:len(rows)], strategy
    result['old_same_day_economic_fill_prefix'] = sum(map(len, previous.values()))
    for table in ('maker_shared_arrival_days', 'maker_shared_arrival_events'):
        rows = old.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
        now = [tuple(r) for r in live.execute(f'SELECT * FROM {table} ORDER BY rowid')]
        assert rows == now[:len(rows)], table
        result[table+'_original_rows_preserved'] = len(rows)
    session = dict(live.execute('SELECT * FROM sessions ORDER BY started_at_utc DESC LIMIT 1').fetchone())
    persisted = json.loads(session.pop('config_json'))['maker_paper']['realtime_comparison_model_ids']
    assert persisted == models and session['status'] == 'running'
    assignments = [dict(r) for r in live.execute('SELECT * FROM maker_paper_model_assignments WHERE market_date=?', (day,))]
    assert len(assignments) == 20
    for code in config.maker_paper.bond_codes:
        assert {a['model_id'] for a in assignments if a['bond_code'] == code} == set(models)
    own = [a for a in assignments if a['model_id'] == model]
    assert len(own) == 2 and all(a['parent_model_id'] == 'maker_priority_v2_63_candidate' for a in own)
    errors = [dict(r) for r in live.execute("SELECT event_type,message FROM app_events WHERE run_id=? AND level IN ('error','critical')", (session['run_id'],))]
    assert not errors, errors
    heartbeat = json.loads(live.execute("SELECT details_json FROM app_events WHERE run_id=? AND event_type='heartbeat' ORDER BY id DESC LIMIT 1", (session['run_id'],)).fetchone()[0])
    assert heartbeat['dropped_callbacks'] == heartbeat['callback_errors'] == 0
    accounts = heartbeat['maker_paper']['accounts']
    assert len(accounts) == 20
    own_accounts = [a for a in accounts if a['model_id'] == model]
    assert len(own_accounts) == 2
    for a in own_accounts:
        assert a['initial_inventory'] == 1000 and a['maximum_inventory'] == 2000
        assert 0 <= a['inventory'] <= 2000 and a['cash'] >= 0
    for a in accounts:
        if 'arrival_execution' in a:
            assert not a['arrival_execution']['failed']
    result.update(session=session, models=models, assignments=own, own_accounts=own_accounts,
                  errors=errors, dropped_callbacks=heartbeat['dropped_callbacks'], verified=True)
    destination = root/'priority_v271_live_assignment_verification.json'
    with destination.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in ('prior_days_exact','own_accounts','session','models')}, ensure_ascii=False))
    live.close()
    old.close()


if __name__ == '__main__':
    main()
