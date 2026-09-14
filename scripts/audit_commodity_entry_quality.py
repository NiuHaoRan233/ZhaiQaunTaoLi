"""Causal order-creation feature audit of frozen commodity Dadao accounts.

Read-only audit, not a strategy backtest. Selecting historical filled orders does
not simulate cash/position paths after an entry filter is changed.
"""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import gzip
import hashlib
import json
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / '广义套利/reports'
DATA = ROOT / '广义套利/data'
OUT = REPORT / 'commodity_optimization_20260912'
TZ = timezone(timedelta(hours=8))


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def stamp(ts):
    return datetime.fromtimestamp(ts / 1000, TZ).strftime('%Y%m%d')


def split(date):
    return 'development_0824_0904' if date <= '20260904' else 'comparison_0907_0910' if date <= '20260910' else 'selection_0911'


def clean(x):
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, float) and not np.isfinite(x):
        return None
    return x


def features(frame, unit, tick_cents, wanted):
    """Only raw rows at/before order creation enter features.

    Windows are reset at morning break, lunch and day changes. Midpoint is a
    quote feature, not a fair-value assertion. Time coverage holds a valid quote
    for at most 60 seconds; no interpolation from a future quote.
    """
    f = frame.sort_values('time', kind='stable').drop_duplicates('time', keep='last')
    t = f.time.to_numpy(dtype=np.int64)
    minute = ((t // 1000 + 8 * 3600) % 86400) / 60
    session = np.full(len(f), -1)
    for s, (a, b) in enumerate([(540, 615), (630, 690), (810, 900)]):
        session[(minute >= a) & (minute < b)] = s
    bp = np.asarray(f.bidPrice.tolist(), dtype=float)
    ap = np.asarray(f.askPrice.tolist(), dtype=float)
    bv = np.asarray(f.bidVol.tolist(), dtype=float)
    av = np.asarray(f.askVol.tolist(), dtype=float)
    bid, ask = np.rint(bp[:, 0] * unit * 100), np.rint(ap[:, 0] * unit * 100)
    last = np.rint(f.lastPrice.to_numpy(dtype=float) * unit * 100)
    volume = f.volume.to_numpy(dtype=float)
    amount = f.amount.to_numpy(dtype=float)
    valid = (bid > 0) & (ask > bid) & (bv[:, 0] > 0) & (av[:, 0] > 0)
    dv = np.r_[0, np.diff(volume)]
    da = np.r_[0, np.diff(amount)]
    gap = np.r_[0, np.diff(t)]
    quality = (dv > 0) & (da > 0) & np.r_[False, valid[:-1]] & (gap > 0) & (gap <= 60_000) & (session >= 0) & (session == np.r_[-2, session[:-1]])
    buy = quality & (last >= np.r_[0, ask[:-1]])
    sell = quality & (~buy) & (last <= np.r_[0, bid[:-1]])
    result = {}
    wanted = set(wanted)
    for s in range(3):
        ii = np.flatnonzero(session == s)
        if not len(ii):
            continue
        st = t[ii]
        q = np.array([j for j, ts in enumerate(st) if int(ts) in wanted], dtype=int)
        if not len(q):
            continue
        vv = valid[ii]
        mids = np.where(vv, (bid[ii] + ask[ii]) / 200, np.nan)
        valid_idx = np.flatnonzero(vv)
        ix = pd.to_datetime(st, unit='ms')
        series = pd.Series(mids, index=ix)
        coverage_prefix = np.r_[0, np.cumsum(np.minimum(np.diff(st), 60_000) * vv[:-1])]
        prefix = {key: np.r_[0, np.cumsum(values)] for key, values in dict(
            observations=np.ones(len(ii), dtype=int), valid=vv,
            volume=np.where((dv[ii] > 0) & (session[ii] == np.r_[-2, session[:-1]][ii]), dv[ii], 0),
            usable_volume=np.where((dv[ii] > 0) & quality[ii], dv[ii], 0),
            buy_evidence=buy[ii], sell_evidence=sell[ii]).items()}
        wf = {}
        for seconds in (60, 300):
            low = st[q] - seconds * 1000
            left = np.searchsorted(st, low, side='left')
            firstpos = np.searchsorted(st[valid_idx], low, side='left')
            first = valid_idx[np.minimum(firstpos, max(len(valid_idx) - 1, 0))]
            first_ok = (firstpos < len(valid_idx)) & (first <= q)
            firstmid = np.where(first_ok, mids[first], np.nan)
            rolling = series.rolling(f'{seconds}s', closed='both', min_periods=1)
            minimum = rolling.min().to_numpy()[q]
            maximum = rolling.max().to_numpy()[q]
            median = rolling.median().to_numpy()[q]
            lower_event = np.clip(np.searchsorted(st, low, side='right') - 1, 0, len(st) - 1)
            lower_integral = np.where(low >= st[0], coverage_prefix[lower_event] + np.minimum(np.maximum(low - st[lower_event], 0), 60_000) * vv[lower_event], 0)
            elapsed = np.minimum(st[q] - st[0], seconds * 1000)
            covered = coverage_prefix[q] - lower_integral
            info = dict(mid_drift_cny=mids[q] - firstmid, mid_range_cny=maximum - minimum,
                mid_median_cny=median, mid_min_cny=minimum, mid_max_cny=maximum,
                history_seconds=elapsed / 1000, valid_coverage_seconds=covered / 1000,
                valid_coverage_ratio=covered / (seconds * 1000))
            for key, p in prefix.items():
                info[key] = p[q + 1] - p[left]
            wf[seconds] = info
        for j, k in enumerate(q):
            r = ii[k]
            spread = (ask[r] - bid[r]) / 100
            improve = tick_cents / 100 if ask[r] - bid[r] > tick_cents else 0
            entry = dict(ts=int(t[r]), valid=bool(valid[r]), bid_cny=bid[r] / 100,
                ask_cny=ask[r] / 100, bid_qty=int(bv[r, 0]), ask_qty=int(av[r, 0]),
                spread_cny=spread, relative_spread=2 * (ask[r] - bid[r]) / (ask[r] + bid[r]),
                tick_cny=tick_cents / 100, planned_buy_cny=bid[r] / 100 + improve,
                planned_sell_cny=ask[r] / 100 - improve,
                planned_gross_space_cny=spread - 2 * improve,
                planned_net_space_cny=spread - 2 * improve - 3.4,
                bid_second_gap_cny=(bp[r, 0] - bp[r, 1]) * unit if bp.shape[1] > 1 and bp[r, 1] > 0 and bv[r, 1] > 0 else None,
                ask_second_gap_cny=(ap[r, 1] - ap[r, 0]) * unit if ap.shape[1] > 1 and ap[r, 1] > 0 and av[r, 1] > 0 else None)
            for seconds, info in wf.items():
                for key, values in info.items():
                    entry[f'w{seconds}_{key}'] = values[j]
            result[entry['ts']] = clean(entry)
    assert set(result) == wanted, (len(result), len(wanted))
    return result


def aggregate(rows):
    if not rows:
        return dict(n=0, net_cny=0)
    a = pd.DataFrame(rows)
    closed = a[~a['tail']]
    return clean(dict(n=len(a), closed=len(closed), tails=int(a['tail'].sum()),
        net_cny=round(a.net_cny.sum(), 6), gross_cny=round(a.gross_cny.sum(), 6),
        mean_net_cny=a.net_cny.mean(), median_net_cny=a.net_cny.median(),
        positive=int((a.net_cny > 0).sum()), mean_holding_seconds=a.holding_seconds.mean(),
        median_holding_seconds=a.holding_seconds.median(),
        net_excluding_largest_three=round(a.net_cny.sum() - a.net_cny.nlargest(min(3, len(a))).sum(), 6)))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    OUT.mkdir(exist_ok=True)
    metadata = {}
    for p in [REPORT / 'dadao_v1_screen/matrix.json', *sorted(REPORT.glob('dadao_v1_validation_*/matrix.json'))]:
        metadata.update(read(p)['inputs'])
    source_hashes = {}
    filled, accounts, missing = [], [], []
    created_count = 0
    all_counts = dict(total=0, no_net_space=0, negative_60s_drift=0, two_sided_300s_evidence=0, sufficient_300s_coverage=0)
    with gzip.open(OUT / 'all_buy_order_features.jsonl.gz', 'wt', encoding='utf-8', compresslevel=1) as output:
        paths = sorted(REPORT.glob('dadao_user_f170_d0_*/accounts/*.json.gz'))
        for pi, path in enumerate(paths):
            result = json.loads(gzip.decompress(path.read_bytes()))
            source_hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
            s = result['summary']; code = s['code']
            assert s['fee_per_side_cny'] == 1.7 and s['mode'] == 'last_d0'
            orders = [o for o in result['orders'] if o['side'] == 'buy']
            fill_by_order = {f['order_id']: f for f in result['fills'] if f['side'] == 'buy'}
            cycles = {c['entry_ts']: c for c in result['cycles']}
            by_date = {}
            for order in orders:
                by_date.setdefault(stamp(order['created_ts']), []).append(order)
            for date, daily_orders in sorted(by_date.items()):
                meta = metadata[f'{code}_{date}']
                raw = DATA / f'{code}_{date}_tick.pkl'
                digest = hashlib.sha256(raw.read_bytes()).hexdigest()
                assert digest == meta['sha256']
                source_hashes[str(raw.relative_to(ROOT))] = digest
                fs = features(pd.read_pickle(raw), meta['unit'], meta['tick_cents'], [o['created_ts'] for o in daily_orders])
                for o in daily_orders:
                    feat = dict(fs[o['created_ts']], code=code, date=date, split=split(date), order_id=o['id'])
                    assert abs(feat['planned_buy_cny'] - o['price'] / 100) < 1e-6
                    created_count += 1
                    all_counts['total'] += 1
                    all_counts['no_net_space'] += feat['planned_net_space_cny'] <= 0
                    all_counts['negative_60s_drift'] += feat['w60_mid_drift_cny'] is not None and feat['w60_mid_drift_cny'] < 0
                    all_counts['two_sided_300s_evidence'] += feat['w300_buy_evidence'] > 0 and feat['w300_sell_evidence'] > 0
                    all_counts['sufficient_300s_coverage'] += feat['w300_valid_coverage_ratio'] >= .8
                    f = fill_by_order.get(o['id'])
                    feat['filled'] = f is not None
                    if f:
                        c = cycles.get(f['ts'])
                        feat.update(fill_ts=f['ts'], entry_price_cny=f['price_cents'] / 100,
                            fill_wait_seconds=(f['ts'] - o['created_ts']) / 1000,
                            tail=c is None,
                            net_cny=c['net_cents'] / 100 if c else s['open_cycle_contribution_cny'],
                            gross_cny=c['gross_cents'] / 100 if c else s['tail_gross_cny'],
                            holding_seconds=c['duration_seconds'] if c else (result['curve'][-1][0] - f['ts']) / 1000,
                            exit_ts=c['exit_ts'] if c else None,
                            exit_split=split(stamp(c['exit_ts'])) if c else None)
                        feat['cross_date'] = stamp(f['ts']) != stamp(c['exit_ts'] if c else result['curve'][-1][0])
                        filled.append(feat)
                    output.write(json.dumps(feat, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n')
            accounts.append(s)
            own = [r for r in filled if r['code'] == code]
            assert abs(sum(r['net_cny'] for r in own) - s['pnl_cny']) < 1e-6
            print(f'{pi + 1}/{len(paths)} {code} entries={len(own)} net={s["pnl_cny"]}', flush=True)
    conditions = {
        'all': lambda r: True,
        'net_space_le_0': lambda r: r['planned_net_space_cny'] <= 0,
        'net_space_0_to_10': lambda r: 0 < r['planned_net_space_cny'] <= 10,
        'net_space_gt_10': lambda r: r['planned_net_space_cny'] > 10,
        'drift60_down_more_than_net_space': lambda r: r['w60_mid_drift_cny'] is not None and r['w60_mid_drift_cny'] < -max(0, r['planned_net_space_cny']),
        'drift60_not_down_more_than_net_space': lambda r: r['w60_mid_drift_cny'] is not None and r['w60_mid_drift_cny'] >= -max(0, r['planned_net_space_cny']),
        'range60_gt_twice_net_space': lambda r: r['w60_mid_range_cny'] is not None and r['w60_mid_range_cny'] > 2 * max(0, r['planned_net_space_cny']),
        'range60_le_twice_net_space': lambda r: r['w60_mid_range_cny'] is not None and r['w60_mid_range_cny'] <= 2 * max(0, r['planned_net_space_cny']),
        'both_side_evidence_300s': lambda r: r['w300_buy_evidence'] > 0 and r['w300_sell_evidence'] > 0,
        'missing_side_evidence_300s': lambda r: not (r['w300_buy_evidence'] > 0 and r['w300_sell_evidence'] > 0),
        'full_60s_valid_coverage': lambda r: r['w60_valid_coverage_ratio'] >= .8,
        'insufficient_60s_valid_coverage': lambda r: r['w60_valid_coverage_ratio'] < .8,
        'holding_le_30s': lambda r: r['holding_seconds'] <= 30,
        'holding_30_to_300s': lambda r: 30 < r['holding_seconds'] <= 300,
        'holding_300_to_1800s': lambda r: 300 < r['holding_seconds'] <= 1800,
        'holding_gt_1800s_same_day': lambda r: r['holding_seconds'] > 1800 and not r['cross_date'] and not r['tail'],
        'cross_date': lambda r: r['cross_date'],
        'tail': lambda r: r['tail'],
        'causal_space_and_drift': lambda r: r['planned_net_space_cny'] >= 10 and r['w60_mid_drift_cny'] is not None and r['w60_mid_drift_cny'] >= -r['planned_net_space_cny'] and r['w60_mid_range_cny'] <= 2 * r['planned_net_space_cny'],
        'both_side_and_net_space_gt_roundtrip_fee': lambda r: r['w300_buy_evidence'] > 0 and r['w300_sell_evidence'] > 0 and r['planned_net_space_cny'] > 3.4,
        'both_side_and_net_space_gt_10': lambda r: r['w300_buy_evidence'] > 0 and r['w300_sell_evidence'] > 0 and r['planned_net_space_cny'] > 10,
    }
    grouped = {period: {label: aggregate([r for r in filled if (period == 'all' or r['split'] == period) and condition(r)]) for label, condition in conditions.items()} for period in ['all', 'development_0824_0904', 'comparison_0907_0910', 'selection_0911']}
    by_code = {s['code']: {label: aggregate([r for r in filled if r['code'] == s['code'] and condition(r)]) for label, condition in conditions.items()} for s in accounts}
    prefix_checks = []
    for code in ['ag2612P16000.SF', 'cu2611P114000.SF', 'CF703P17400.ZF', 'PK612P8400.ZF', 'au2612C840.SF', 'FG701C1000.ZF']:
        choices = [r for r in filled if r['code'] == code]
        for original in [choices[len(choices) // 3], choices[len(choices) * 2 // 3]]:
            date, cutoff = original['date'], original['ts']
            meta = metadata[f'{code}_{date}']
            frame = pd.read_pickle(DATA / f'{code}_{date}_tick.pkl')
            a = features(frame, meta['unit'], meta['tick_cents'], [cutoff])[cutoff]
            b = features(frame[frame.time <= cutoff], meta['unit'], meta['tick_cents'], [cutoff])[cutoff]
            assert a == b
            assert all(original[key] == value for key, value in a.items())
            prefix_checks.append(dict(code=code, date=date, cutoff=cutoff, passed=True))
    output = clean(dict(purpose='Causal feature attribution; filtered rows do not constitute a strategy replay',
        account_count=len(accounts), buy_order_count=created_count, filled_entry_count=len(filled),
        input_count=sum(k.endswith('.pkl') for k in source_hashes), all_buy_order_counts=all_counts,
        model_id=accounts[0]['model_id'], fee_per_side_cny=1.7, delay_ms=0,
        grouped_by_entry_creation_date=grouped, by_code=by_code, entries=filled,
        source_sha256=source_hashes, feature_prefix_checks=prefix_checks, limitations=[
            'Entry features use only the creating snapshot and earlier observations, within the same daytime session.',
            'Only 64 contracts chosen using Sept11 evidence; earlier dates are not unbiased instrument selection.',
            'Attribution conditions on original fills; changed filters alter orders, fills and later cash/inventory, requiring full replay.',
            'Date partitions assign full eventual cycle PnL to creation date; this is not daily equity and crosses date boundaries.',
            'Inferred trade-side evidence is L1, at most one contract per usable interval; not exchange aggressor flags.',
            'No fair value/underlying hedge/night trading is inferred from midpoint features.',
        ]))
    (OUT / 'entry_quality.json').write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    lines = ['# 商品期权入场因果特征归因', '', f'64只原始账户；单边1.7元、延迟0。已检查{created_count:,}张买入委托创建时刻，全部{len(filled):,}笔买入均连接到闭环或期末尾仓。数据仅在创建当时及之前、同连续日盘时段内计算。', '',
        f'读取850个存在买单的输入日；其余61个已有输入日没有买入委托，无需计算入场窗口。全部64账户盈亏核对通过，12个截断未来数据的特征前缀检查通过。', '',
        '此处是原始交易的归因分组，不是应用过滤后的策略收益。日期组按买单创建日期计入最终回合盈亏，因此会跨越对照期，不能冒充日度或严格样本外验证。', '',
        '报价空间=(卖一−买一)×合约乘数；拟挂空间扣除两侧各改善一跳，再扣往返3.4元。60秒/300秒中点来自有效双侧快照，报价时间覆盖最多向前持有60秒；中点不自证合理价值。两侧成交证据沿用量增、正额增、新鲜前报价与末价方向的L1口径。', '']
    lines += ['## 关键发现与可回放假设', '',
        '1. 只找宽价差、低中点波动不够。原路径中净空间超过10元的1,787次买入净亏78,786.4元；价差静止可能只是没有成交。净空间至少10元、60秒下漂不超过空间且波幅不超过两倍空间的组合，开发期和对照期归因都明显亏损。不要把该组的9月11日大赚倒推成有效过滤。', '',
        '2. 近300秒真实两侧成交可达性比静态价差更值得先检验。两侧均有至少一次可用末价证据的4,087次买入原路径合计12,462.9元，缺一侧2,315次合计−106,425元；去掉最大三笔后两侧组仍亏4,771.9元，不能当做已证实的策略。最差十个闭环均缺至少一侧证据，其中黄金最差三笔两侧均无。', '',
        '3. 小空间的主要问题之一是费用。玉米c2611-C-2300毛利3,885元，扣费后−2,177.2；玻璃FG701C1000毛利3,410元、净−1,088.2；豆粕m2701-P-3050毛125元、净−561.8。1,384次入场计划空间不足往返费；当前核仍可反复两侧各改善一跳到同一价位，静态来回净亏3.4元。', '',
        '4. 大赢和大亏都涉及方向与跨日，不能仅删除大赚长持仓。黄金au2612C920、白银以及铜保留个案，改入场与退出后必须看是否意外丢掉原来有价值的低接。近300秒双侧是研究假设，不是所有正确低接的必要条件：棉花原有双侧27回合−276.8，缺侧58回合+877.8，反例保留。', '',
        '建议先做少量先验尺度的完整回放：保留原交易模式，要求往返费用覆盖；另测近300秒买、卖证据各≥1、有效报价预热，并把净空间>一次往返费3.4元作为透明质量余量。净空间>10元只作相邻尺度对照。不能直接把本报告筛出的旧成交相加，当作过滤后收益。持仓后的正常退出与低价保护需另设模型，不以“盈利才卖”抹掉风险。', '']
    for period, groups in grouped.items():
        lines += [f'## {period}', '', '| 组别 | 买入数 | 净收益元 | 中位净元 | 正回合数 | 中位持秒 | 去掉最大三笔后净元 |', '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
        for label, g in groups.items():
            if not g['n']:
                continue
            lines.append(f'| {label} | {g["n"]} | {g["net_cny"]:,.2f} | {g["median_net_cny"]:,.2f} | {g["positive"]} | {g["median_holding_seconds"]:,.1f} | {g["net_excluding_largest_three"]:,.2f} |')
        lines += ['']
    lines += ['## 文件与限制', '', 'entry_quality.json包含全部买入成交的创建时因果特征、各合约分组、旧账户及输入哈希。all_buy_order_features.jsonl.gz保留全部买入委托，包括未成交者。', '', *['- ' + x for x in output['limitations']]]
    (OUT / 'entry_quality.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps(dict(count=created_count, filled=len(filled), grouped=grouped), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
