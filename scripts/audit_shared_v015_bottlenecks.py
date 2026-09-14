"""Read saved immutable v0.15 r2 evidence; never run or change trading rules.

Component arithmetic is diagnostic only, not a counterfactual fill/replay.
"""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo


SCORES = (
    'bond_code', 'order_id', 'order_kind', 'active_fill', 'shadow_intent',
    'remaining_bonds', 'planned_remaining_bonds', 'entry_price', 'exit_price',
    'gross_edge_per_bond', 'entry_probability_proxy', 'exit_probability_proxy',
    'parent_exit_probability', 'exit_probability_without_session',
    'session_exit_probability_bonus', 'session_resilience_eligible',
    'local_recent_sell_bonds', 'local_recent_buy_bonds', 'all_recent_buy_bonds',
    'all_recent_sell_bonds', 'weighted_exit_buy_bonds', 'inner_bid_support_bonds',
    'near_bid_support_bonds', 'downside_distance_per_bond', 'downside_risk_fraction',
    'expected_gain_per_bond', 'expected_downside_per_bond', 'expected_lock_seconds',
    'parent_expected_lock_seconds', 'flow_lock_seconds', 'parent_score_cny',
    'pre_waiting_score_cny', 'slot_waiting_cost_cny', 'score_cny',
    'full_consumed_live_attack', 'terminal_time_factor',
)
CASES = (
    ('2026-08-07', '132024.SH', '09:51:32'),
    ('2026-08-07', '132026.SH', '11:16:35'),
    ('2026-08-07', '132026.SH', '13:43:14'),
    ('2026-08-17', '132024.SH', '10:11:54'),
    ('2026-08-17', '132024.SH', '13:03:15'),
    ('2026-08-20', '132026.SH', '11:10:18'),
    ('2026-08-20', '132024.SH', '13:25:39'),
    ('2026-08-20', '132026.SH', '14:27:30'),
)


def at(day, time):
    return int(datetime.fromisoformat(day+'T'+time).replace(
        tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()*1000)


def clock(ts):
    return datetime.fromtimestamp(ts/1000, ZoneInfo('Asia/Shanghai')).strftime('%H:%M:%S')


def score(row):
    return {k: row[k] for k in SCORES if k in row} if row else None


def frame(row):
    return dict(time=row['time'], code=row['code'], tick_id=row['tick_id'],
        bids=row['bids'], asks=row['asks'], trades=row['current_trades'],
        state=row['assessment'].get('state'), context=row['context'],
        native_buy=row['native_buy'], orders=row['orders'],
        selected=row['selected'], inventories=row['inventories'])


def forecast(cells, label):
    rows = []
    for cell in cells:
        for e in cell[label]['slot_metrics']['episodes']:
            pred = e.get('prediction')
            if e['censored'] or not pred or not pred.get('expected_lock_seconds'):
                continue
            # Remove cross-lunch episodes from this timing comparison.
            if e['entry_ts'] < at(cell['market_date'], '11:30:00') < e['exit_ts']:
                continue
            rows.append(dict(day=cell['market_date'], code=e['code'],
                start=clock(e['entry_ts']), seconds=e['holding_seconds'],
                predicted=pred['expected_lock_seconds'],
                ratio=e['holding_seconds']/pred['expected_lock_seconds']))
    return dict(completed_non_lunch_count=len(rows),
        actual_median=median(r['seconds'] for r in rows),
        prediction_median=median(r['predicted'] for r in rows),
        ratio_median=median(r['ratio'] for r in rows),
        above_three_times=sum(r['ratio'] > 3 for r in rows),
        below_one_third=sum(r['ratio'] < 1/3 for r in rows),
        longest_underestimated=sorted(rows, key=lambda r: -r['ratio'])[:8])


def verify_timing(saved):
    """Observe original calls once, with no extra scoring/decision calls."""
    from zhaiquant.config import load_config
    from zhaiquant.one_hand_maker_research import replay_one_hand_day, _only_account
    from zhaiquant.shared_thousand_maker_v014_research import economic_fills
    from zhaiquant.shared_thousand_maker_v015_research import AllocationParametersV015
    from zhaiquant.shared_thousand_maker_v015r2_research import POLICY, SharedThousandV015R2Allocator
    from replay_shared_v015 import Observer

    class TimingObserver(Observer):
        def __init__(self):
            super().__init__(False)
            self.trace = []

        def __call__(self, allocator, tick):
            super().__call__(allocator, tick)
            if tick is not None:
                return
            original = allocator._score

            def observe_score(code, order, *, active_fill):
                current = allocator.last_bond_ticks.get(code)
                account = _only_account(allocator.engines[code])
                value = original(code, order, active_fill=active_fill)
                if current and (('09:51:00' <= current.market_time < '09:51:40' and code == '132024.SH')
                                or ('11:16:20' <= current.market_time < '11:16:40' and code == '132026.SH')):
                    self.trace.append(dict(time=current.market_time, tick_id=current.tick_id,
                        code=code, created_ms=order.created_ms, price=order.limit_price,
                        selected=allocator.selected_code,
                        holding=allocator._holdings(), cash=allocator.shared_cash_cny,
                        actual_fill_check_quantity=allocator._active_fill_quantities.get((code, order.db_id)),
                        prior_bids=account.last_bids, current_bids=current.bids,
                        incoming_trade_bonds=current.trade_bonds, incoming_last_price=current.last_price,
                        incoming_side=current.inferred_side, score=score(value.public() if value else None)))
                return value
            allocator._score = observe_score

    observer = TimingObserver()
    replay = replay_one_hand_day(load_config('config.toml'), market_date='2026-08-07',
        priority_policy=POLICY, allocator_class=SharedThousandV015R2Allocator,
        parameters=AllocationParametersV015(), shared_capacity_bonds=1000,
        observer=observer)
    assert economic_fills(replay) == economic_fills(saved)
    assert replay['trading_pnl'] == saved['trading_pnl']
    assert replay['order_counts'] == saved['order_counts']
    return dict(saved_full_day_fills_pnl_order_counts_exact=True, trace=observer.trace)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output')
    parser.add_argument('--case', nargs=3, metavar=('DATE', 'CODE', 'TIME'))
    parser.add_argument('--window-seconds', type=int, default=65)
    parser.add_argument('--verify-timing', action='store_true')
    args = parser.parse_args()
    paths = [Path('output/research/shared_v015r2_prominent_days.json'),
             Path('output/research/shared_v015r2_matrix_20260804_20260904.json')]
    reports = [json.loads(p.read_text(encoding='utf-8')) for p in paths]
    detailed = {c['market_date']: c for c in reports[0]['cells']}
    result = dict(source_hashes={str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in paths}, offline_readonly_analysis=True,
        no_trading_or_model_changes=True, cases=[],
        forecast={k: forecast(reports[1]['cells'], k) for k in ('parent', 'candidate')})
    for day, code, time in ([args.case] if args.case else CASES):
        pair = detailed[day]
        ts = at(day, time)
        case = dict(day=day, code=code, time=time, pnl_delta=pair['pnl_delta'])
        for label in ('parent', 'candidate'):
            cell = pair[label]
            checks = [dict(time=c['market_time'], allowed=c['allowed'], quantity=c['quantity'],
                           score=score(c['score'])) for c in cell['observed_fill_checks']
                      if c['code'] == code and abs(c['market_ts_ms']-ts) <= 1000]
            choices = [dict(time=clock(c['market_ts_ms']), before=c['selected_before'],
                            winner=c['winner'], reason=c['reason'],
                            scores=[score(s) for s in c['scores']])
                       for c in cell['allocation_challenge_events']
                       if c['market_ts_ms'] == ts]
            frames = [frame(f) for f in cell['frames'] if f['code'] == code
                      and ts-args.window_seconds*1000 <= f['ts'] <= ts+30_000]
            case[label] = dict(checks=checks, choices=choices, frames=frames)
        result['cases'].append(case)
    if args.verify_timing:
        result['timing_verification'] = verify_timing(detailed['2026-08-07']['candidate'])
    if args.output:
        target = Path(args.output)
        with target.open('x', encoding='utf-8') as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        print(json.dumps(dict(output=str(target), forecast=result['forecast']), ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
