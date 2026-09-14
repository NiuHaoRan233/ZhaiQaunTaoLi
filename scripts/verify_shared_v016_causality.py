"""Prefix replay and stored tick-change predecessor audit (read-only)."""
import argparse
import json
import sqlite3
from pathlib import Path

from zhaiquant.config import load_config
from zhaiquant.one_hand_maker_research import replay_one_hand_day
from zhaiquant.shared_thousand_maker_v014_research import economic_fills
from zhaiquant.shared_thousand_maker_v016_research import POLICY, CausalSharedMakerEngine, SharedThousandV016Allocator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    path = Path(args.output)
    if path.exists():
        raise FileExistsError(path)
    config = load_config('config.toml')
    result = dict(prefix_checks=[])
    for day, cutoff, report_name in (
        ('2026-08-07', '11:17:00', 'shared_v016_matrix_20260804_20260904.json'),
        ('2026-08-20', '11:18:00', 'shared_v016_matrix_20260804_20260904.json'),
        ('2026-09-07', '10:29:00', 'shared_v016_target_20260907_144500.json'),
    ):
        report = json.loads((Path('output/research')/report_name).read_text(encoding='utf-8'))
        full = next(c['candidate'] for c in report['cells'] if c['market_date'] == day)
        prefix = replay_one_hand_day(config, market_date=day, cutoff_time=cutoff,
            priority_policy=POLICY, engine_class=CausalSharedMakerEngine,
            allocator_class=SharedThousandV016Allocator, shared_capacity_bonds=1000)
        expected_fills = dict(fills=[f for f in full['fills'] if f['market_time'][:8] <= cutoff])
        assert economic_fills(prefix) == economic_fills(expected_fills)
        expected_events = [e for e in full['allocation_events'] if e['market_time'][:8] <= cutoff]
        assert prefix['allocation_events'] == expected_events
        result['prefix_checks'].append(dict(day=day, cutoff=cutoff, fills_exact=True, choices_and_scores_exact=True))
        print(day, cutoff, 'prefix exact', flush=True)
    source = sqlite3.connect(f'file:{config.storage.database.resolve().as_posix()}?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    source.execute('PRAGMA query_only=ON')
    source.execute('BEGIN')
    try:
        rows = source.execute('''WITH ranked AS (
            SELECT r.*,s.status session_status,
                ROW_NUMBER() OVER(PARTITION BY r.code,r.market_ts_ms
                    ORDER BY CASE WHEN s.status='backfill' THEN 1 ELSE 0 END,r.received_ts_ns,r.id) n
            FROM raw_ticks r LEFT JOIN sessions s ON r.run_id=s.run_id
            WHERE r.market_date BETWEEN '2026-08-04' AND '2026-09-07'
                AND r.code IN ('132026.SH','132024.SH','600900.SH','600362.SH'))
            SELECT r.id,r.code,r.market_date,r.market_ts_ms,r.session_status,
                   t.previous_tick_id,p.market_ts_ms previous_market_ts_ms
            FROM ranked r LEFT JOIN tick_changes t ON t.tick_id=r.id
            LEFT JOIN raw_ticks p ON p.id=t.previous_tick_id WHERE r.n=1''').fetchall()
        future = [dict(r) for r in rows if r['previous_market_ts_ms'] is not None
                  and r['previous_market_ts_ms'] > r['market_ts_ms']]
        assert not future, 'stored delta/side references a future market event'
        result['predecessor_audit'] = dict(selected_ticks=len(rows), future_predecessors=len(future),
            backfilled_ticks=sum(r['session_status'] == 'backfill' for r in rows),
            limitations='Backfill receive times can be synthetic; this is market-time causality, not measured network-arrival/latency equivalence.')
    finally:
        source.rollback()
        source.close()
    with path.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result['predecessor_audit']), flush=True)


if __name__ == '__main__':
    main()
