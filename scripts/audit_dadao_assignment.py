"""Read-only verification of the ninth paper model and retained old ledgers."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sqlite3

from audit_shared_v016r2_assignment import digest
from zhaiquant.config import load_config
from zhaiquant.dadao_maker_live import POLICY


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backup', type=Path, default=Path('backups/zhaiquant-before-dadao-20260908.sqlite3'))
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    config = load_config('config.toml')
    current = sqlite3.connect(config.storage.database.as_uri()+'?mode=ro', uri=True)
    before = sqlite3.connect(args.backup.resolve().as_uri()+'?mode=ro', uri=True)
    current.row_factory = sqlite3.Row
    current.execute('BEGIN')
    result = dict(model_id=POLICY.model_id, prior_days_exact={})
    for table in ('maker_paper_accounts','maker_paper_model_assignments','maker_paper_orders','maker_paper_lots','maker_paper_fills'):
        old, new = digest(before,table,'2026-09-08'), digest(current,table,'2026-09-08')
        assert old == new, table
        result['prior_days_exact'][table] = new
    fields='strategy_id,market_ts_ms,side,price,quantity,reference_tick_id,cash_after,inventory_after'
    def grouped(conn):
        groups = defaultdict(list)
        for row in conn.execute(f"SELECT {fields} FROM maker_paper_fills WHERE market_date='2026-09-08' ORDER BY id"):
            groups[row[0]].append(tuple(row))
        return groups
    old, new = grouped(before), grouped(current)
    for strategy, rows in old.items():
        assert rows == new[strategy][:len(rows)], strategy
    result['same_day_old_fill_prefix_exact'] = sum(map(len,old.values()))
    session = dict(current.execute('SELECT * FROM sessions WHERE status!=? ORDER BY started_at_utc DESC LIMIT 1',('backfill',)).fetchone())
    models = json.loads(session.pop('config_json'))['maker_paper']['realtime_comparison_model_ids']
    assert models == list(config.maker_paper.realtime_comparison_model_ids)
    assert len(models)==9 and models[-1]==POLICY.model_id and session['status']=='running'
    assignments=[dict(r) for r in current.execute("SELECT * FROM maker_paper_model_assignments WHERE market_date='2026-09-08'")]
    assert len(assignments)==18
    for code in ('132026.SH','132024.SH'):
        assert {r['model_id'] for r in assignments if r['bond_code']==code}==set(models)
    own=[r for r in assignments if r['model_id']==POLICY.model_id]
    assert len(own)==2 and all(r['parent_model_id']==POLICY.parent_model_id for r in own)
    errors=[dict(r) for r in current.execute("SELECT event_type,message FROM app_events WHERE run_id=? AND level IN ('error','critical')",(session['run_id'],))]
    assert not errors,errors
    heartbeat=json.loads(current.execute("SELECT details_json FROM app_events WHERE run_id=? AND event_type='heartbeat' ORDER BY id DESC LIMIT 1",(session['run_id'],)).fetchone()[0])
    assert heartbeat['dropped_callbacks']==0
    accounts=heartbeat['maker_paper']['accounts'];assert len(accounts)==18
    own_accounts=[r for r in accounts if r['model_id']==POLICY.model_id]
    assert len(own_accounts)==2 and not any(r['arrival_execution']['failed'] for r in own_accounts)
    journal=dict(current.execute('SELECT count(*) AS events,min(sequence) AS first_sequence,max(sequence) AS last_sequence FROM maker_shared_arrival_events WHERE model_id=?',(POLICY.model_id,)).fetchone())
    assert journal['events']>0
    orders=[dict(r) for r in current.execute('''SELECT o.* FROM maker_paper_orders o JOIN maker_paper_model_assignments a
        ON a.market_date=o.market_date AND a.strategy_id=o.strategy_id WHERE a.model_id=?''',(POLICY.model_id,))]
    for order in orders:
        metadata=json.loads(order['metadata_json'])
        assert metadata['model_id']==POLICY.model_id
        assert metadata['decision_ts_ms']==order['created_market_ts_ms']
    result.update(session=session,models=models,assignments=assignments,own_accounts=own_accounts,
                  journal=journal,order_count=len(orders),errors=errors,verified=True)
    with args.output.open('x',encoding='utf-8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))
    current.close();before.close()


if __name__=='__main__':
    main()
