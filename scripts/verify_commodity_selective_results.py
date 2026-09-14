"""Independent raw-quote/economic verification of commodity candidate archives.

This does not import the candidate kernel or simulate a strategy. It reconstructs
cash/holdings and marked equity from saved fills and independently read raw QMT
quotes, and checks current-book execution for every forced closing trade.
"""
from pathlib import Path
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import argparse
import gzip
import hashlib
import json
import sys
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / '广义套利/reports'
OUT = REPORT / 'commodity_optimization_20260912'
DATA = ROOT / '广义套利/data'
TZ = timezone(timedelta(hours=8))
FOCUS = ['au2612C920.SF', 'au2612C840.SF', 'ag2612P16000.SF', 'ag2612P15800.SF', 'cu2611P114000.SF', 'cu2612C104000.SF', 'CF703P17400.ZF', 'PK612P8400.ZF', 'c2611-C-2300.DF']


def read(p):
    return json.loads(p.read_text(encoding='utf-8'))


def unpack(p):
    return json.loads(gzip.decompress(p.read_bytes()))


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def cents(p, unit):
    return int((Decimal(str(p)) * Decimal(str(unit)) * 100).to_integral_value())


def scalar(v):
    if isinstance(v, np.generic):
        return v.item()
    return v


def write(p, obj):
    temp = p.with_suffix('.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=scalar, allow_nan=False), encoding='utf-8')
    temp.replace(p)


def market(code, tags, metadata):
    arrays = []
    hashes = {}
    for date, digest in tags['inputs']:
        m = metadata[f'{code}_{date}']
        path = DATA / f'{code}_{date}_tick.pkl'
        assert sha(path) == digest == m['sha256']
        hashes[str(path.relative_to(ROOT))] = digest
        raw = pd.read_pickle(path).sort_values('time', kind='stable').drop_duplicates('time', keep='last')
        ts = raw.time.to_numpy(dtype=np.int64)
        unit = m['unit']
        bid = np.array([cents(x[0], unit) for x in raw.bidPrice], dtype=np.int64)
        ask = np.array([cents(x[0], unit) for x in raw.askPrice], dtype=np.int64)
        bq = np.array([int(x[0]) for x in raw.bidVol], dtype=np.int64)
        aq = np.array([int(x[0]) for x in raw.askVol], dtype=np.int64)
        last = np.array([cents(x, unit) for x in raw.lastPrice], dtype=np.int64)
        volume = raw.volume.to_numpy(dtype=float)
        amount = raw.amount.to_numpy(dtype=float)
        minute = ((ts // 1000 + 8 * 3600) % 86400) / 60
        session = np.full(len(ts), -1)
        for j, (a, b) in enumerate([(540, 615), (630, 690), (810, 900)]):
            session[(minute >= a) & (minute < b)] = j
        valid = (bid > 0) & (ask > bid) & (bq > 0) & (aq > 0)
        dv = np.r_[0, np.diff(volume)]
        da = np.r_[0, np.diff(amount)]
        previous_ts = np.r_[ts[0], ts[:-1]]
        quality = ((dv > 0) & (da > 0) & np.r_[False, valid[:-1]] & (ts > previous_ts) &
            (ts - previous_ts <= 60000) & (session >= 0) & (session == np.r_[-2, session[:-1]]))
        strict = np.where(quality & (last >= np.r_[0, ask[:-1]]), 1,
            np.where(quality & (last <= np.r_[0, bid[:-1]]), -1, 0))
        take = session >= 0
        arrays.append(dict(ts=ts[take], bid=bid[take], ask=ask[take], bid_qty=bq[take], ask_qty=aq[take],
            last=last[take], valid=valid[take], previous_ts=previous_ts[take], quantity=np.maximum(0, dv[take]),
            quality=quality[take], strict=strict[take], session=session[take], date=np.full(sum(take), date)))
    data = {key: np.concatenate([d[key] for d in arrays]) for key in arrays[0]}
    assert np.all(np.diff(data['ts']) > 0)
    valid_index = np.where(data['valid'], np.arange(len(data['ts'])), -1)
    held = np.maximum.accumulate(valid_index)
    data['mark_bid'] = np.where(held >= 0, data['bid'][np.maximum(held, 0)], 0)
    return data, hashes


def verify_account(result, data, path, saved_baseline=None):
    s = result['summary']
    assert s['fee_per_side_cny'] == 1.7 and s['delay_ms'] == 0
    initial = round(s['initial_cash_cny'] * 100)
    cash = initial
    inv = basis = fees = gross = turnover = 0
    orders = {o['id']: o for o in result['orders']}
    assert len(orders) == s['order_count'] == len(result['orders'])
    times = data['ts']
    for order in result['orders']:
        assert order['model_id'] == s['model_id'] and order['quantity'] == 1
        assert order['created_ts'] == order['due_ts']
        j = np.searchsorted(times, order['created_ts'])
        assert j < len(times) and times[j] == order['created_ts']
        if order.get('reason') != 'day_flat_visible_bid':
            assert data['valid'][j]
    previous = -1
    cash_values, inv_values, fill_times, rebuilt_cycles, active = [], [], [], [], []
    entry = None
    for f in result['fills']:
        o = orders[f['order_id']]
        assert f['ts'] >= previous
        previous = f['ts']
        assert f['model_id'] == o['model_id'] == s['model_id']
        assert f['quantity'] == 1 and f['fee_cents'] == 170 and f['side'] == o['side']
        assert f['created_ts'] == o['created_ts']
        j = np.searchsorted(times, f['ts'])
        assert j < len(times) and times[j] == f['ts'] and f['date'] == data['date'][j]
        assert f['price_cents'] == o['price']
        if f['kind'] == 'active_exit':
            assert s['variant'] in ('day_flat', 'combined') and f['side'] == 'sell'
            assert o['reason'] == 'day_flat_visible_bid' and f['created_ts'] == f['ts']
            assert f['price_cents'] == data['bid'][j] > 0 and data['bid_qty'][j] >= 1
            assert (f['ts'] // 60000 + 480) % 1440 >= 895
            active.append(dict(ts=f['ts'], raw_bid_cents=int(data['bid'][j]),
                raw_bid_qty=int(data['bid_qty'][j]), execution_cents=f['price_cents'],
                current_two_sided_valid=bool(data['valid'][j])))
        else:
            assert f['kind'] == 'passive'
            assert o['created_ts'] < f['ts'] and f['active_ts'] <= data['previous_ts'][j]
            assert f['source_previous_ts'] == data['previous_ts'][j]
            assert data['quality'][j] and data['quantity'][j] >= 1
            assert f['source_quantity'] == 1 and f['source_last_contract_evidence']
            assert f['source_last_cents'] == data['last'][j]
            assert data['strict'][j] == (-1 if f['side'] == 'buy' else 1)
            assert data['last'][j] <= f['price_cents'] if f['side'] == 'buy' else data['last'][j] >= f['price_cents']
        p = f['price_cents']
        if f['side'] == 'buy':
            assert inv == 0 and entry is None
            cash -= p + 170
            inv = 1; basis = p
            entry = dict(entry_ts=f['ts'], entry_price_cents=p, quantity=1, gross_cents=0, fees_cents=170)
        else:
            assert inv == 1 and entry is not None
            cash += p - 170
            inv = 0; gross += p - basis
            rebuilt_cycles.append(dict(**{**entry, 'gross_cents': p - basis, 'fees_cents': 340},
                exit_ts=f['ts'], duration_seconds=(f['ts'] - entry['entry_ts']) / 1000,
                net_cents=p - basis - 340))
            basis = 0; entry = None
        fees += 170; turnover += p
        assert cash >= 0 and cash == f['cash_cents'] and inv == f['inventory']
        cash_values.append(cash); inv_values.append(inv); fill_times.append(f['ts'])
    assert rebuilt_cycles == result['cycles']
    assert entry == s['open_cycle']
    curve = np.asarray(result['curve'], dtype=np.int64)
    assert np.array_equal(curve[:, 0], times)
    idx = np.searchsorted(fill_times, times, side='right')
    cvalues = np.r_[initial, np.asarray(cash_values, dtype=np.int64)][idx]
    ivals = np.r_[0, np.asarray(inv_values, dtype=np.int64)][idx]
    pnl = cvalues + ivals * data['mark_bid'] - initial
    assert np.array_equal(curve[:, 1], pnl) and np.array_equal(curve[:, 2], ivals)
    dds = np.maximum.accumulate(np.r_[0, pnl])[1:] - pnl
    holding_ms = int(np.sum(np.diff(times) * ivals[:-1]))
    assert round(s['max_drawdown_cny'] * 100) == int(np.max(dds))
    assert round(s['holding_seconds'] * 1000) == holding_ms
    assert round(s['end_cash_cny'] * 100) == cash and s['end_inventory'] == inv
    assert round(s['fees_cny'] * 100) == fees and round(s['realized_gross_cny'] * 100) == gross
    assert round(s['turnover_cny'] * 100) == turnover
    assert round(s['tail_gross_cny'] * 100) == inv * data['mark_bid'][-1] - basis
    assert round(s['pnl_cny'] * 100) == pnl[-1] == gross + inv * data['mark_bid'][-1] - basis - fees
    assert len(active) == s['active_exit_fills']
    endpoints = np.r_[np.flatnonzero(data['date'][1:] != data['date'][:-1]), len(times) - 1]
    before = 0
    daily = []
    for j in endpoints:
        date = str(data['date'][j]); value = int(pnl[j])
        daily.append(dict(date=date, pnl_cny=(value - before) / 100, cumulative_pnl_cny=value / 100,
            end_inventory=int(ivals[j]), fill_count=sum(f['date'] == date for f in result['fills']), last_ts=int(times[j])))
        before = value
    assert daily == result['daily']
    baseline_equal = None
    if saved_baseline:
        old = unpack(saved_baseline)
        economic = lambda rows: [{k: v for k, v in r.items() if k != 'model_id'} for r in rows]
        assert economic(result['orders']) == economic(old['orders'])
        assert economic(result['fills']) == economic(old['fills'])
        assert result['cycles'] == old['cycles'] and result['curve'] == old['curve'] and result['daily'] == old['daily']
        baseline_equal = True
    return dict(code=s['code'], variant=s['variant'], path=str(path.relative_to(ROOT)), sha256=sha(path),
        passed=True, fill_count=len(result['fills']), cycle_count=len(result['cycles']), curve_points=len(times),
        daily_points=len(daily), pnl_cny=s['pnl_cny'], active_exits=active, baseline_equivalent=baseline_equal)


def raw_window(data, ts):
    j = int(np.searchsorted(data['ts'], ts))
    lo, hi = max(0, j - 2), min(len(data['ts']), j + 3)
    return [{k: scalar(v[i]) for k, v in data.items() if k != 'mark_bid'} for i in range(lo, hi)]


def causal_flow_at(data, ts):
    j = int(np.searchsorted(data['ts'], ts))
    assert data['ts'][j] == ts
    same = (data['date'][:j + 1] == data['date'][j]) & (data['session'][:j + 1] == data['session'][j])
    lo = int(np.flatnonzero(same)[0])
    lo = max(lo, int(np.searchsorted(data['ts'], ts - 300000)))
    bad = np.flatnonzero(~data['valid'][lo:j + 1])
    if len(bad):
        lo += int(bad[-1]) + 1
    gaps = np.flatnonzero(np.r_[False, np.diff(data['ts']) > 60000][lo:j + 1])
    if len(gaps):
        lo += int(gaps[-1])
    return dict(buy_evidence=int(np.sum(data['strict'][lo:j + 1] == 1)),
        sell_evidence=int(np.sum(data['strict'][lo:j + 1] == -1)),
        effective_start_ts=int(data['ts'][lo]) if lo <= j else None,
        history_reset_on_invalid_or_gap=True)


def cycle_example(label, cycle, original, candidate, data, peer=None):
    buy = next(f for f in original['fills'] if f['side'] == 'buy' and f['ts'] == cycle['entry_ts'])
    sell = next(f for f in original['fills'] if f['side'] == 'sell' and f['ts'] == cycle['exit_ts'])
    creation = buy['created_ts']
    j = int(np.searchsorted(data['ts'], creation))
    candidate_inv = candidate['curve'][j][2]
    own_cycle = next((c for c in candidate['cycles'] if c['entry_ts'] == cycle['entry_ts']), None)
    own_buy = next((f for f in candidate['fills'] if f['ts'] == cycle['entry_ts'] and f['side'] == 'buy'), None)
    item = dict(label=label, original_cycle=cycle, original_buy=buy, original_sell=sell,
        causal_flow_at_buy_creation=causal_flow_at(data, creation),
        candidate_inventory_after_creation_frame=candidate_inv,
        candidate_same_entry_buy=own_buy, candidate_same_entry_cycle=own_cycle,
        candidate_orders_at_original_creation=[o for o in candidate['orders'] if o['created_ts'] == creation],
        creation_window=raw_window(data, creation), entry_window=raw_window(data, cycle['entry_ts']),
        exit_window=raw_window(data, cycle['exit_ts']))
    if peer:
        peer_cycle = next((c for c in peer['cycles'] if c['entry_ts'] == cycle['entry_ts']), None)
        item['flow_entry_same_entry_cycle'] = peer_cycle
        if peer_cycle:
            item['flow_entry_exit_window'] = raw_window(data, peer_cycle['exit_ts'])
    if own_cycle:
        holding_orders = [o for o in candidate['orders'] if own_cycle['entry_ts'] <= o['created_ts'] <= own_cycle['exit_ts'] and o['side'] == 'sell']
        item['first_holding_sell_orders'] = holding_orders[:12]
        item['first_current_top_sell_order'] = next((o for o in holding_orders if o.get('reason') == 'current_top'), None)
        item['exit_holding_sell_orders'] = holding_orders[-3:]
    return item


def focus_audit(by_code, metadata, variant):
    rows = []
    for code in FOCUS:
        info = by_code.get(code)
        if not info or variant not in info['variants'] or 'control' not in info['variants']:
            continue
        candidate = unpack(info['variants'][variant]['path'])
        control = unpack(info['variants']['control']['path'])
        peer = unpack(info['variants']['flow_entry']['path']) if variant == 'flow_patient' else None
        data, _ = market(code, info['tag'], metadata)
        olddays = {d['date']: d for d in control['daily']}
        differences = [dict(date=d['date'], candidate_pnl_cny=d['pnl_cny'], baseline_pnl_cny=olddays[d['date']]['pnl_cny'],
            delta_cny=round(d['pnl_cny'] - olddays[d['date']]['pnl_cny'], 6)) for d in candidate['daily']]
        oldsign = {(f['ts'], f['side'], f['price_cents']) for f in control['fills']}
        newsign = {(f['ts'], f['side'], f['price_cents']) for f in candidate['fills']}
        examples = []
        for d in [max(differences, key=lambda d: d['delta_cny']), min(differences, key=lambda d: d['delta_cny'])]:
            changed = [f for f in candidate['fills'] if f['date'] == d['date'] and (f['ts'], f['side'], f['price_cents']) not in oldsign]
            skipped = [f for f in control['fills'] if f['date'] == d['date'] and (f['ts'], f['side'], f['price_cents']) not in newsign]
            representative = [('candidate_changed', x) for x in changed[:2]] + [('baseline_not_reproduced', x) for x in skipped[:2]]
            examples.append(dict(**d, candidate_fills=[f for f in candidate['fills'] if f['date'] == d['date']],
                baseline_fills=[f for f in control['fills'] if f['date'] == d['date']],
                raw_examples=[dict(label=label, fill=f, creation_window=raw_window(data, f['created_ts']),
                    execution_window=raw_window(data, f['ts'])) for label, f in representative]))
        selected = [('candidate_top_three', c, candidate) for c in sorted(candidate['cycles'], key=lambda c: -c['net_cents'])[:3]]
        selected += [('baseline_worst_three', c, control) for c in sorted(control['cycles'], key=lambda c: c['net_cents'])[:3]]
        selected += [('baseline_best_three', c, control) for c in sorted(control['cycles'], key=lambda c: -c['net_cents'])[:3]]
        if peer:
            peer_by_entry = {c['entry_ts']: c for c in peer['cycles']}
            common = [c for c in candidate['cycles'] if c['entry_ts'] in peer_by_entry]
            if common:
                selected += [('patient_best_delta_same_entry', max(common, key=lambda c: c['net_cents'] - peer_by_entry[c['entry_ts']]['net_cents']), candidate),
                    ('patient_worst_delta_same_entry', min(common, key=lambda c: c['net_cents'] - peer_by_entry[c['entry_ts']]['net_cents']), candidate)]
        rows.append(dict(code=code, variant=variant, candidate_summary=candidate['summary'],
            baseline_summary=control['summary'], daily_differences=differences, examples=examples,
            cycle_examples=[cycle_example(label, c, original, candidate, data, peer) for label, c, original in selected]))
    write(OUT / f'verification_focus_{variant}.json', dict(variant=variant, code_count=len(rows), rows=rows,
        note='Windows include adjacent observations after execution for audit only, never strategy input. Changed daily PnL includes positions carried from previous days.'))


def once(args):
    metadata = {}
    for p in [REPORT / 'dadao_v1_screen/matrix.json', *sorted(REPORT.glob('dadao_v1_validation_*/matrix.json'))]:
        metadata.update(read(p)['inputs'])
    target = OUT / 'verification.json'
    state = read(target) if target.exists() else dict(accounts={}, errors={}, raw_sha256={})
    by_code, published, runner_errors, prefixes, equivalences = {}, {}, {}, 0, 0
    for p in sorted(OUT.glob('part_*/matrix.json')):
        matrix = read(p)
        prefixes += matrix['prefix_checks']; equivalences += matrix['baseline_equivalences']
        runner_errors.update(matrix['errors'])
        for key, summary in matrix['accounts'].items():
            code, variant = summary['code'], summary['variant']
            path = p.parent / 'accounts' / f'{key}.json.gz'
            published[key] = dict(summary=summary, path=path)
            by_code.setdefault(code, dict(tag=matrix['inputs'][code], variants={}))['variants'][variant] = published[key]
    baseline_paths = {p.name.removesuffix('.json.gz'): p for p in REPORT.glob('dadao_user_f170_d0_*/accounts/*.json.gz')}
    for code, info in by_code.items():
        pending = [(v, x) for v, x in info['variants'].items() if not state['accounts'].get(code + '_' + v, {}).get('sha256') == sha(x['path'])]
        if not pending:
            continue
        try:
            data, hashes = market(code, info['tag'], metadata)
            state['raw_sha256'].update(hashes)
            for variant, item in pending:
                key = code + '_' + variant
                r = unpack(item['path'])
                assert r['summary'] == item['summary']
                state['accounts'][key] = verify_account(r, data, item['path'], baseline_paths[code] if variant == 'control' else None)
                state['errors'].pop(key, None)
            state['errors'].pop(code, None)
            print('VERIFIED', code, 'accounts', len(state['accounts']), flush=True)
        except Exception as exc:
            state['errors'][code] = repr(exc)
            print('ERROR', code, repr(exc), flush=True)
        write(target, state)
    values = list(state['accounts'].values())
    complete = len(values) == 512 and not state['errors'] and not runner_errors
    state.update(updated_utc=datetime.now(timezone.utc).isoformat(), published_accounts=len(published),
        expected_accounts=512, verified_accounts=len(values), complete=complete,
        runner_errors=runner_errors, runner_prefix_checks=prefixes, runner_baseline_equivalences=equivalences,
        independent_baseline_equivalences=sum(v['baseline_equivalent'] is True for v in values),
        independently_checked_fills=sum(v['fill_count'] for v in values),
        independently_checked_curve_points=sum(v['curve_points'] for v in values),
        independently_checked_days=sum(v['daily_points'] for v in values),
        current_book_active_exit_checks=sum(len(v['active_exits']) for v in values),
        raw_input_days=len(state['raw_sha256']), verifier_sha256=sha(Path(__file__)),
        limits=['Read-only audit of published account artifacts; no strategy changes.',
            'L1 trade directions are independently reconstructed but are not true exchange aggressor flags.',
            'Marking invalid books carries the last valid bid exactly as frozen; active exits must use current bid.'])
    write(target, state)
    print(json.dumps({k: v for k, v in state.items() if k not in ['accounts', 'raw_sha256', 'limits']}, ensure_ascii=False), flush=True)
    if args.focus_variant:
        focus_audit(by_code, metadata, args.focus_variant)
    return complete


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(); p.add_argument('--watch', action='store_true'); p.add_argument('--focus-variant')
    args = p.parse_args()
    while True:
        complete = once(args)
        if complete or not args.watch:
            return
        time.sleep(60)


if __name__ == '__main__':
    main()
