"""Compact daily and causal episode diagnostics from saved v0.15 replays."""
import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def clock(ts):
    return datetime.fromtimestamp(ts/1000, ZoneInfo('Asia/Shanghai')).strftime('%H:%M:%S') if ts else None


def episode(row):
    pred = row['prediction'] or {}
    return dict(code=row['code'], start=clock(row['entry_ts']), end=clock(row['exit_ts']),
        seconds=row['holding_seconds'], first=row['first_quantity'], quantity=row['buy_quantity'],
        pnl=row['cash_flow'] if not row['censored'] else None, censored=row['censored'],
        entry=row['entry_price'], score=pred.get('score_cny'),
        exit_proxy=pred.get('exit_probability_proxy'), lock=pred.get('expected_lock_seconds'),
        weighted_buys=pred.get('weighted_exit_buy_bonds'), cost=pred.get('slot_waiting_cost_cny'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report')
    parser.add_argument('--date')
    parser.add_argument('--episodes', action='store_true')
    parser.add_argument('--rejections', action='store_true')
    parser.add_argument('--frame-time')
    args = parser.parse_args()
    result = json.loads(Path(args.report).read_text(encoding='utf-8'))
    print(json.dumps(dict(totals=result['totals']), ensure_ascii=False))
    for pair in result['cells']:
        if args.date and pair['market_date'] != args.date:
            continue
        print(json.dumps(dict(day=pair['market_date'], delta=pair['pnl_delta'],
            pnl={k: pair[k]['trading_pnl'] for k in ('reference', 'parent', 'candidate')},
            slot={k: pair[k]['slot_metrics']['locked_slot_seconds'] for k in ('parent', 'candidate')},
            terminal={k: pair[k]['terminal_inventory_bonds'] for k in ('parent', 'candidate')},
            counters=pair['candidate']['allocation_v015_metrics']), ensure_ascii=False))
        if args.frame_time:
            for label in ('parent', 'candidate'):
                for frame in pair[label].get('frames', []):
                    if frame['time'] == args.frame_time:
                        print(json.dumps(dict(label=label, frame=frame), ensure_ascii=False))
        if args.episodes:
            for label in ('parent', 'candidate'):
                print(json.dumps(dict(label=label, episodes=[episode(e) for e in pair[label]['slot_metrics']['episodes']]), ensure_ascii=False))
        if args.rejections:
            seen = set()
            for c in pair['candidate']['observed_fill_checks']:
                s = c['score']
                if c['allowed'] or not s:
                    continue
                key = (c['code'], round(s['entry_price'], 3))
                if key in seen:
                    continue
                seen.add(key)
                print(json.dumps(dict(rejected=c['market_time'], code=c['code'], quantity=c['quantity'],
                    entry=s['entry_price'], exit=s['exit_price'], parent=s['parent_score_cny'],
                    pre_wait=s['pre_waiting_score_cny'], cost=s['slot_waiting_cost_cny'], score=s['score_cny'],
                    buy=s['weighted_exit_buy_bonds'], lock=s['expected_lock_seconds']), ensure_ascii=False))


if __name__ == '__main__':
    main()
