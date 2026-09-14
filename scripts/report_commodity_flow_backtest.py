"""Read frozen commodity replay ledgers and produce standalone backtest artifacts.

No market connection, strategy decisions or account mutations occur here.
Run from any directory with the repository's Python environment.
"""
from __future__ import annotations

import base64
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import gzip
import hashlib
import html
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '广义套利'
REPLAY = WORK / 'reports/commodity_strategy_20260913_r2'
OLD = WORK / 'reports/commodity_optimization_20260912'
OUT = WORK / 'reports/commodity_backtest_20260913'
MODES = ['flow_patient', 'flow_entry', 'control']
LABEL = dict(zip(MODES, ['主策略 · 双侧成交＋耐心退出', '对照 · 双侧成交入场', '原循环']))
COLOR = dict(zip(MODES, ['#007d78', '#4676bf', '#bd6572']))
TZ = timezone(timedelta(hours=8))
HASHES = {}


def read(path):
    raw = path.read_bytes()
    HASHES[str(path.relative_to(ROOT))] = hashlib.sha256(raw).hexdigest()
    return json.loads(gzip.decompress(raw) if path.suffix == '.gz' else raw)


def dump(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), 'utf-8')


def cents(value):
    return int(round(value * 100))


def stamp(ts):
    return datetime.fromtimestamp(int(ts) / 1000, TZ).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]


def combine(changes):
    """Aggregate all same-timestamp deltas before marking equity or exposure."""
    if not changes:
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64)
    arr = np.concatenate(changes)
    arr = arr[np.argsort(arr[:, 0], kind='stable')]
    ts, idx = np.unique(arr[:, 0], return_index=True)
    return ts, np.cumsum(np.add.reduceat(arr[:, 1], idx))


def drawdown(values, capital):
    equity = capital + np.asarray(values, dtype=np.int64)
    peak = np.maximum.accumulate(np.maximum(equity, capital))
    amount = peak - equity
    pct = amount / peak * 100
    return amount, pct


def money(value):
    return f'{value:,.2f}'


def table(headers, rows, cls=''):
    return '<div class="scroll"><table class="' + cls + '"><thead><tr>' + ''.join(
        '<th>' + html.escape(str(x)) + '</th>' for x in headers
    ) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(
        '<td>' + html.escape(str(x)) + '</td>' for x in row
    ) + '</tr>' for row in rows) + '</tbody></table></div>'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    old_summary = read(OLD / 'optimization_summary.json')
    replay_summary = read(REPLAY / 'replay_summary.json')
    verification = read(REPLAY / 'verification.json')
    forward = read(REPLAY / 'preflight_paper_status.json')
    config = read(WORK / 'commodity_strategy/strategy_r2.json')
    instruments = {r['code']: r for r in config['instruments']}
    dates = old_summary['dates']
    codes = sorted(instruments)
    assert len(codes) == 64 and len(dates) == 15
    # Verify source ledgers against their frozen manifests, not merely today's hashes.
    frozen = {}
    for path in [REPLAY / 'result_manifest.json', OLD / 'result_manifest.json']:
        frozen.update(read(path))
    accounts = {m: {} for m in MODES}
    series, metrics, daily_all, cycles_all, fills_all = {}, {}, {}, [], []
    contract_data, product_data = {}, {}
    qa = {'source_manifest_matches': 0, 'accounts': 0, 'account_days': {}, 'missing_cells': {}, 'checks': []}
    for mode in MODES:
        changes, exposure_changes = [], []
        mode_cycles, mode_fills = [], []
        start, finish = None, None
        totals = defaultdict(int)
        days = {d: {'pnl_cents': 0, 'observed_contracts': 0, 'fills': 0} for d in dates}
        for code in codes:
            paths = list(OLD.glob(f'part_*/accounts/{code}_control.json.gz')) if mode == 'control' else [REPLAY / 'accounts' / f'{code}_{mode}.json.gz']
            assert len(paths) == 1, (code, mode, paths)
            path = paths[0]
            a = read(path)
            key = str(path.relative_to(ROOT))
            assert frozen[key] == HASHES[key], key
            qa['source_manifest_matches'] += 1
            s = a['summary']
            assert s['code'] == code and cents(s['fee_per_side_cny']) == 170 and s['delay_ms'] == 0
            assert cents(s['initial_cash_cny']) == instruments[code]['initial_cents']
            assert cents(s['pnl_cny']) == cents(old_summary['by_code'][code][mode]['pnl_cny'])
            assert sum(cents(d['pnl_cny']) for d in a['daily']) == cents(s['pnl_cny'])
            assert sum(f['fee_cents'] for f in a['fills']) == cents(s['fees_cny'])
            assert cents(s['realized_gross_cny']) + cents(s['tail_gross_cny']) - cents(s['fees_cny']) == cents(s['pnl_cny'])
            assert sum(c['net_cents'] for c in a['cycles']) + cents(s['open_cycle_contribution_cny']) == cents(s['pnl_cny'])
            curve = np.asarray(a['curve'], dtype=np.int64)
            assert len(curve) and np.all(np.diff(curve[:, 0]) >= 0)
            assert curve[-1, 1] == cents(s['pnl_cny'])
            start = min(start or int(curve[0, 0]), int(curve[0, 0]))
            finish = max(finish or int(curve[-1, 0]), int(curve[-1, 0]))
            delta = np.diff(curve[:, 1], prepend=0)
            mask = delta != 0
            changes.append(np.column_stack([curve[mask, 0], delta[mask]]))
            basis = 0
            ec = []
            for fill in a['fills']:
                next_basis = fill['price_cents'] * fill['quantity'] if fill['side'] == 'buy' else 0
                ec.append([fill['ts'], next_basis - basis])
                basis = next_basis
                mode_fills.append(dict(fill, variant=mode, code=code))
            if ec:
                exposure_changes.append(np.array(ec, dtype=np.int64))
            for c in a['cycles']:
                mode_cycles.append(dict(c, variant=mode, code=code,
                    overnight=stamp(c['entry_ts'])[:10] != stamp(c['exit_ts'])[:10]))
            daily = {d['date']: d for d in a['daily']}
            for d, r in daily.items():
                days[d]['pnl_cents'] += cents(r['pnl_cny'])
                days[d]['observed_contracts'] += 1
                days[d]['fills'] += r['fill_count']
            for k in ['initial_cash_cny', 'pnl_cny', 'realized_gross_cny', 'tail_gross_cny', 'fees_cny', 'open_cycle_contribution_cny']:
                totals[k] += cents(s[k])
            accounts[mode][code] = {'summary': s, 'daily': daily}
            qa['accounts'] += 1
        ts, pnl = combine(changes)
        ts = np.r_[start - 1, ts, finish]
        pnl = np.r_[0, pnl, pnl[-1]]
        capital = totals['initial_cash_cny']
        assert capital == 169800000 and pnl[-1] == totals['pnl_cny']
        dd, ddpct = drawdown(pnl, capital)
        exp_ts, exp = combine(exposure_changes)
        exp_ts = np.r_[start, exp_ts, finish]
        exp = np.r_[0, exp, exp[-1]]
        assert np.all(exp >= 0)
        mean_exp = float(np.sum(exp[:-1].astype(float) * np.diff(exp_ts)) / (finish - start)) / 100
        daily_pnl = np.array([days[d]['pnl_cents'] for d in dates], dtype=np.int64)
        cumulative = np.r_[0, np.cumsum(daily_pnl)]
        daydd, dayddpct = drawdown(cumulative, capital)
        assert cumulative[-1] == pnl[-1]
        for i, d in enumerate(dates):
            cut = int(datetime.strptime(d, '%Y%m%d').replace(tzinfo=TZ, hour=23, minute=59, second=59).timestamp() * 1000)
            assert pnl[np.searchsorted(ts, cut, side='right') - 1] == cumulative[i + 1], (mode, d)
        wins = [c['net_cents'] for c in mode_cycles if c['net_cents'] > 0]
        losses = [c['net_cents'] for c in mode_cycles if c['net_cents'] < 0]
        ddidx = int(np.argmax(ddpct))
        peak_value = int(np.max(np.r_[0, pnl[:ddidx + 1]]))
        peakidx = int(np.flatnonzero(pnl[:ddidx + 1] == peak_value)[-1])
        recovering = np.flatnonzero(pnl[ddidx + 1:] >= peak_value)
        m = {k: v / 100 for k, v in totals.items()}
        m.update(return_pct=pnl[-1] / capital * 100, nav=1 + pnl[-1] / capital,
            daily_max_drawdown_cny=int(daydd.max()) / 100, daily_max_drawdown_pct=float(dayddpct.max()),
            intraday_max_drawdown_cny=int(dd.max()) / 100, intraday_max_drawdown_pct=float(ddpct.max()),
            intraday_dd_peak=stamp(ts[peakidx]), intraday_dd_trough=stamp(ts[ddidx]),
            intraday_dd_recovery=stamp(ts[ddidx + 1 + recovering[0]]) if len(recovering) else None,
            cycles=len(mode_cycles), fills=len(mode_fills), cycle_win_rate_pct=len(wins) / len(mode_cycles) * 100,
            cycle_profit_factor=sum(wins) / -sum(losses) if losses else None,
            average_cycle_net_cny=sum(c['net_cents'] for c in mode_cycles) / len(mode_cycles) / 100,
            median_holding_minutes=float(np.median([c['duration_seconds'] for c in mode_cycles])) / 60,
            longest_holding_hours=max(c['duration_seconds'] for c in mode_cycles) / 3600,
            profitable_days=int(np.sum(daily_pnl > 0)), losing_days=int(np.sum(daily_pnl < 0)),
            traded_contracts=sum(a['summary']['fill_count'] > 0 for a in accounts[mode].values()),
            winning_contracts=sum(a['summary']['pnl_cny'] > 0 for a in accounts[mode].values()),
            losing_contracts=sum(a['summary']['pnl_cny'] < 0 for a in accounts[mode].values()),
            tail_contracts=sum(a['summary']['end_inventory'] for a in accounts[mode].values()),
            peak_premium_at_cost_cny=int(exp.max()) / 100,
            wall_clock_mean_premium_at_cost_cny=mean_exp,
            wall_clock_mean_budget_utilization_pct=mean_exp / (capital / 100) * 100,
            same_day_cycles=sum(not c['overnight'] for c in mode_cycles),
            same_day_cycle_net_cny=sum(c['net_cents'] for c in mode_cycles if not c['overnight']) / 100,
            overnight_cycles=sum(c['overnight'] for c in mode_cycles),
            overnight_cycle_net_cny=sum(c['net_cents'] for c in mode_cycles if c['overnight']) / 100,
            excluding_last_day_cny=int(cumulative[-2]) / 100,
            excluding_top_three_cycles_cny=(int(pnl[-1]) - sum(sorted(wins, reverse=True)[:3])) / 100,
            final_day_contribution_pct=int(daily_pnl[-1]) / int(pnl[-1]) * 100,
            phase_early_cny=int(daily_pnl[:10].sum()) / 100,
            phase_later_cny=int(daily_pnl[10:14].sum()) / 100,
            phase_selection_cny=int(daily_pnl[14]) / 100)
        metrics[mode] = m
        daily_all[mode] = [dict(date=d, pnl_cny=days[d]['pnl_cents']/100,
            cumulative_pnl_cny=int(cumulative[i+1])/100, nav=1+cumulative[i+1]/capital,
            daily_return_on_open_equity_pct=days[d]['pnl_cents']/(capital+cumulative[i])*100,
            daily_drawdown_pct=float(dayddpct[i+1]), observed_contracts=days[d]['observed_contracts'],
            missing_contracts=64-days[d]['observed_contracts'], fills=days[d]['fills']) for i,d in enumerate(dates)]
        qa['account_days'][mode] = sum(d['observed_contracts'] for d in days.values())
        qa['missing_cells'][mode] = 64 * 15 - qa['account_days'][mode]
        assert qa['account_days'][mode] == 911 and qa['missing_cells'][mode] == 49
        series[mode] = {'daily_cumulative_cents': cumulative, 'daily_dd_pct': dayddpct,
                        'ts': ts, 'pnl': pnl, 'dd_pct': ddpct}
        with gzip.open(OUT / f'{mode}_intraday_curve.json.gz', 'wt', encoding='utf-8') as f:
            json.dump({'columns': ['timestamp_ms', 'net_pnl_cents'], 'data': np.column_stack([ts, pnl]).tolist()}, f)
        cycles_all.extend(mode_cycles)
        fills_all.extend(mode_fills)
        print(mode, json.dumps(m, ensure_ascii=False), flush=True)
    for code in codes:
        contract_data[code] = dict(name=instruments[code]['name'], initial_cash_cny=instruments[code]['initial_cents']/100,
            variants={mode: dict(summary=accounts[mode][code]['summary'],
                daily=[accounts[mode][code]['daily'].get(d) for d in dates]) for mode in MODES})
    for name in sorted({r['name'] for r in instruments.values()}):
        subset = [c for c in codes if instruments[c]['name'] == name]
        product_data[name] = {'contracts': subset, 'budget_cny': sum(instruments[c]['initial_cents'] for c in subset)/100,
            'variants': {m: {'net_cny': sum(cents(accounts[m][c]['summary']['pnl_cny']) for c in subset)/100,
                'daily_cny': [sum(cents(accounts[m][c]['daily'][d]['pnl_cny']) for c in subset if d in accounts[m][c]['daily'])/100
                    if any(d in accounts[m][c]['daily'] for c in subset) else None for d in dates]} for m in MODES}}
    assert len(product_data) == 36
    assert sum(cents(x['summary']['pnl_cny']) for x in forward['accounts']) == 0
    assert sum(x['summary']['fill_count'] for x in forward['accounts']) == 0
    report = {'period': [dates[0], dates[-1]], 'dates': dates, 'labels': LABEL, 'metrics': metrics,
        'daily': daily_all, 'contracts': contract_data, 'products': product_data,
        'forward_snapshot_asof': stamp(forward['asof_ms']), 'forward_fills': 0, 'forward_pnl_cny': 0,
        'verification': verification, 'return_definition': 'Fixed independent-account budgets; missing quotes carried, not imputed fresh observations; no shared capital.'}
    dump('backtest_data.json', report)
    dump('trades.json', {'cycles': cycles_all, 'fills': fills_all})
    font_manager.fontManager.addfont('C:/Windows/Fonts/msyh.ttc')
    plt.rcParams.update({'font.family': 'Microsoft YaHei', 'axes.unicode_minus': False,
        'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False,
        'axes.edgecolor': '#d3dce3', 'axes.labelcolor': '#40536a', 'text.color': '#20344b',
        'xtick.color': '#556879', 'ytick.color': '#556879', 'savefig.facecolor': '#f7f9fc'})
    x = np.arange(16)
    tick_labels = ['起点'] + [d[4:6]+'/'+d[6:] for d in dates]
    fig, axes = plt.subplots(2, 1, figsize=(13, 8.2), gridspec_kw={'height_ratios': [1.25, 1]}, layout='constrained')
    fig.suptitle('商品期权策略｜收益率与回撤', x=.035, ha='left', fontsize=21, fontweight='bold')
    axes[0].set_title('2026/08/24—09/11 · 15个交易日 · 每组固定初始预算169.8万元 · 已扣单边1.70元手续费', loc='left', fontsize=10, pad=16)
    for mode in MODES:
        y = series[mode]['daily_cumulative_cents'] / 169800000 * 100
        axes[0].plot(x, y, label=LABEL[mode]+f"  {metrics[mode]['return_pct']:+.2f}%", color=COLOR[mode], lw=2.3, marker='o', ms=3)
        axes[1].plot(x, -series[mode]['daily_dd_pct'], color=COLOR[mode], lw=2)
    for ax in axes:
        ax.grid(axis='y', alpha=.18)
        ax.axhline(0, color='#8c9aaa', lw=.7)
        ax.set_xticks(x, tick_labels, fontsize=9)
        ax.axvspan(14.5, 15.3, color='#e9c875', alpha=.17)
        ax.set_xlim(0, 15.3)
    axes[0].set_ylabel('累计净收益率（%）')
    axes[0].legend(loc='lower left', frameon=False)
    axes[1].set_ylabel('日末净值回撤（%）')
    axes[1].set_xlabel('独立账户合计，并非共享资金组合；缺失日沿用上次估值。阴影为9月11日（选样日）。\n图示日末采样；完整报告另列逐帧合并计算的盘中最大回撤。', labelpad=12, fontsize=9)
    fig.savefig(OUT/'收益率与回撤.png', dpi=170)
    plt.close(fig)
    fig, axes = plt.subplots(2, 1, figsize=(13, 8), layout='constrained')
    fig.suptitle('主策略｜净值与每日净收益', x=.035, ha='left', fontsize=21, fontweight='bold')
    for m in MODES[:2]:
        axes[0].plot(x, 1+series[m]['daily_cumulative_cents']/169800000, label=LABEL[m], color=COLOR[m], lw=2, marker='o', ms=4)
    axes[0].set_ylabel('净值指数（初始 = 1）');axes[0].legend(frameon=False)
    y=np.array([r['pnl_cny'] for r in daily_all['flow_patient']])
    axes[1].bar(x[1:], y, color=['#007d78' if z>=0 else '#bd6572' for z in y], width=.68)
    for i,v in enumerate(y):
        axes[1].annotate(f'{v:,.0f}', (i+1,v), xytext=(0,5 if v>=0 else -13), textcoords='offset points', ha='center', fontsize=8)
    axes[1].set_ylabel('当日净收益（元）')
    for ax in axes:
        ax.set_xticks(x, tick_labels, fontsize=9);ax.grid(axis='y',alpha=.18);ax.set_xlim(0,15.6)
    axes[1].axhline(0,color='#899aaa',lw=.7)
    axes[1].set_ylim(min(y)*1.3,max(y)*1.2)
    axes[1].set_xlabel('日报含买一估值变动、隔夜持仓及手续费。9月11日前累计 +7,132.80 元；末日 +14,706.90 元。', fontsize=9, labelpad=12)
    fig.savefig(OUT/'主策略净值与日收益.png',dpi=170);plt.close(fig)
    build_html(report, series, cycles_all)
    # Recheck files after all report generation; preserve frozen strategy/accounts.
    for path, sha in HASHES.items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == sha, path
    qa['checks'] = ['192 source accounts match frozen manifest hashes', 'fees and gross/tail/net reconcile in integer cents',
        'all daily sums and same-timestamp aggregated curve endpoints reconcile', '64 contracts and 36 products included',
        '911 observed and 49 missing cells per model', 'forward snapshot has zero fills and zero profit', 'all report inputs unchanged after generation']
    qa['status'] = 'passed'
    qa['source_files'] = len(HASHES)
    dump('source_manifest.json',HASHES)
    dump('report_qa.json',qa)
    dump('artifact_manifest.json',{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.iterdir() if p.is_file() and p.name!='artifact_manifest.json'})
    print(json.dumps(qa,ensure_ascii=False),flush=True)


def build_html(r, series, cycles):
    metrics=r['metrics'];dates=r['dates'];contracts=r['contracts']
    rows=[]
    fields=[('初始资金（元）','initial_cash_cny'),('净收益（元）','pnl_cny'),('累计净收益率（%）','return_pct'),
        ('期末净值指数','nav'),('日末最大回撤（元）','daily_max_drawdown_cny'),('日末最大回撤（%）','daily_max_drawdown_pct'),
        ('盘中合计最大回撤（元）','intraday_max_drawdown_cny'),('盘中合计最大回撤（%）','intraday_max_drawdown_pct'),
        ('已闭环毛收益（元）','realized_gross_cny'),('未平仓毛估值收益（元）','tail_gross_cny'),('全部手续费（元）','fees_cny'),
        ('闭环笔数','cycles'),('成交单边数','fills'),('闭环胜率（%）','cycle_win_rate_pct'),('闭环净利润因子','cycle_profit_factor'),
        ('平均闭环净收益（元）','average_cycle_net_cny'),('闭环持仓中位数（分钟）','median_holding_minutes'),
        ('最长已闭环持仓（墙钟小时）','longest_holding_hours'),('盈利交易日','profitable_days'),('亏损交易日','losing_days'),
        ('实际成交合约','traded_contracts'),('盈利合约','winning_contracts'),('亏损合约','losing_contracts'),('期末未平仓手数','tail_contracts'),
        ('未平仓净贡献，已计入总收益（元）','open_cycle_contribution_cny'),('同时持仓成本峰值（元）','peak_premium_at_cost_cny'),
        ('墙钟平均持仓成本（元）','wall_clock_mean_premium_at_cost_cny'),('墙钟平均预算占用（%）','wall_clock_mean_budget_utilization_pct')]
    for label,key in fields:
        rows.append([label]+[f'{metrics[m][key]:,.4f}' if key=='nav' else money(metrics[m][key]) for m in MODES])
    b=['''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>商品期权策略 · 完整回测报告</title><style>
    :root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f4f7fa;color:#20344b;font:15px/1.75 "Microsoft YaHei",sans-serif}main{max-width:1280px;margin:auto;padding:36px 30px}h1{font-size:34px;line-height:1.4;margin:10px 0}h2{margin:0 0 18px;font-size:23px}h3{font-size:17px}.eyebrow{color:#007d78;font-weight:bold;letter-spacing:2px}.muted,small{color:#61758b}.card{background:white;border:1px solid #dfe7ed;border-radius:14px;padding:26px;margin:24px 0;break-inside:avoid}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.kpi{padding:20px;background:#eaf5f3;border-radius:10px}.kpi strong{font-size:29px;display:block}.note{background:#fff7e3;border-left:4px solid #d3a943;padding:15px 18px}.scroll{overflow:auto;max-height:680px}table{border-collapse:collapse;width:100%;white-space:nowrap;font-size:13px}th,td{text-align:right;padding:9px 12px;border-bottom:1px solid #e5edf2}th{position:sticky;top:0;background:#eaf0f6;z-index:1}th:first-child,td:first-child{text-align:left}tr:nth-child(even){background:#f8fafc}img{width:100%;display:block}a{color:#007d78}nav{display:flex;flex-wrap:wrap;gap:20px;margin:22px 0}select{padding:10px;max-width:100%;font:inherit;border:1px solid #bdced9;border-radius:6px}canvas{width:100%;height:330px}details{margin:18px 0}summary{cursor:pointer;font-weight:bold}.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:750px){main{padding:18px 12px}.card{padding:15px}.grid,.two{grid-template-columns:1fr}h1{font-size:27px}}@media print{body{background:white}main{max-width:none;padding:0}.scroll{max-height:none;overflow:visible}nav,select{display:none}.card{break-inside:auto}table{font-size:9px}th,td{padding:4px}details{break-before:page}}
    </style><main><div class="eyebrow">COMMODITY OPTIONS / RESEARCH BACKTEST</div><h1>商品期权策略 · 完整回测报告</h1><p class="muted">2026年8月24日—9月11日｜15个交易日｜36品种 / 64合约｜0.1运行修订｜报告生成：2026-09-13</p>''']
    b.append('<div class="grid">'+''.join(f'<div class="kpi">{LABEL[m]}<strong>{metrics[m]["return_pct"]:+.4f}%</strong>净收益 {metrics[m]["pnl_cny"]:+,.2f} 元<br>初始预算 1,698,000 元</div>' for m in MODES)+'</div>')
    b.append('<nav><a href="#curve">收益曲线</a><a href="#metrics">统计指标</a><a href="#daily">每日收益</a><a href="#attribution">收益来源</a><a href="#contract">全部合约</a><a href="#products">全部品种日度</a><a href="#method">规则与口径</a></nav>')
    b.append('<p class="note">结论：主策略在这批已查看历史样本中由亏转盈，但末日贡献 '+f'{metrics["flow_patient"]["final_day_contribution_pct"]:.2f}%'+ ' 的净利润；剔除末日仍盈利 7,132.80 元。它已形成可运行的纸面策略，当前证据尚不能确认稳定盈利。三条曲线都是64个独立账户的合计，各自预留169.8万元，并非共享资金实测。</p>')
    b.append('<section class="card" id="curve"><h2>收益率、净值与回撤</h2>')
    for name in ['收益率与回撤.png','主策略净值与日收益.png']:
        b.append('<img alt="'+name[:-4]+'" src="data:image/png;base64,'+base64.b64encode((OUT/name).read_bytes()).decode()+'">')
    b.append('</section><section class="card" id="metrics"><h2>回测统计</h2>'+table(['指标']+[LABEL[m] for m in MODES],rows))
    b.append('<p class="muted">回撤率 =（此前最高净值 − 当前净值）/ 此前最高净值。日末回撤取每日最后估值；盘中回撤先对齐所有合约时间戳、同时刻合并净值变化后计算，不能把各账户最大回撤相加。金额最大回撤与比例最大回撤分别取最大值。</p>')
    b.append(table(['模型','盘中比例最大回撤：前高时间','谷底时间','恢复此前高点'],[[LABEL[m],metrics[m]['intraday_dd_peak'],metrics[m]['intraday_dd_trough'],metrics[m]['intraday_dd_recovery'] or '区间内未恢复'] for m in MODES]))
    b.append('<p class="muted">占用资金按持仓买入成本计算，手续费单列；平均占用按首个至最后观测时刻的完整墙钟区间加权，包含夜间、周末及缺失时段沿用的持仓。占用低不表示可以直接按这个数额复用所有独立预算。胜率及利润因子只看已闭环净收益，尾仓另列。未对15日样本做年化或夏普外推。</p></section>')
    b.append('<section class="card" id="daily"><h2>每日净收益与累计收益率</h2>'+table(['日期','有数据/缺失合约']+[LABEL[m]+' 净收益/元' for m in MODES]+[LABEL[m]+' 累计收益率' for m in MODES],
        [[d,f'{r["daily"]["flow_patient"][i]["observed_contracts"]} / {r["daily"]["flow_patient"][i]["missing_contracts"]}']+[money(r['daily'][m][i]['pnl_cny']) for m in MODES]+[f'{(r["daily"][m][i]["nav"]-1)*100:+.4f}%' for m in MODES] for i,d in enumerate(dates)])+'</section>')
    b.append('<section class="card" id="attribution"><h2>收益来源与优化代价</h2>'+table(['分段 / 诊断']+[LABEL[m] for m in MODES],[[label]+[money(metrics[m][key]) for m in MODES] for label,key in [('8/24—9/4 净收益','phase_early_cny'),('9/7—9/10 净收益','phase_later_cny'),('9/11 选样日净收益','phase_selection_cny'),('不含末日净收益','excluding_last_day_cny'),('扣除最大的3笔盈利闭环（诊断）','excluding_top_three_cycles_cny'),('日内闭环净收益','same_day_cycle_net_cny'),('跨日闭环净收益','overnight_cycle_net_cny'),('尾仓净贡献','open_cycle_contribution_cny')]]))
    b.append('<p>双侧成交入场条件减少缺乏真实对手交易的循环，手续费从原循环21,739.60元降至主策略2,191.30元；仅靠手续费门槛或“低波动”筛选，在已有对比中仍亏损。耐心退出进一步改善全体合计，但也可能扩大某些持仓亏损。300秒是价格保护时限，不是强制平仓时限。</p><p>原循环日内闭环整体赚钱，跨日闭环整体大亏；优化后的铜、银仍依赖跨日盈利，隔夜风险并未消失。9/7—9/10已经被研究过，9/11参与选样，均不能宣称未触碰样本外。扣除末日或最大盈利笔数属于集中度诊断，不能当作重新交易后的回测。</p>')
    examples=['au2612C920.SF','pt2612-C-448.GF','au2612C840.SF','ag2612P15800.SF','ag2612P16000.SF','cu2612C104000.SF','cu2611P114000.SF','pg2611-P-6100.DF']
    b.append(table(['保留的重点合约','品种','原循环/元','双侧入场/元','主策略/元'],[[c,contracts[c]['name']]+[money(contracts[c]['variants'][m]['summary']['pnl_cny']) for m in ['control','flow_entry','flow_patient']] for c in examples]))
    top=sorted(cycles,key=lambda c:c['net_cents'],reverse=True)
    b.append('<details><summary>主策略盈利最大的10个闭环</summary>'+table(['合约','开仓时间','平仓时间','净收益/元','跨日'],[[c['code'],stamp(c['entry_ts']),stamp(c['exit_ts']),money(c['net_cents']/100),'是' if c['overnight'] else '否'] for c in [x for x in top if x['variant']=='flow_patient'][:10]])+'</details></section>')
    b.append('<section class="card" id="contract"><h2>全部64合约：收益曲线与每日明细</h2><p>选择任一合约可对比三套规则。单合约收益率使用它自己的初始资金。断开的线段代表该合约缺失数据；“—”表示未观测，0.00才是有记录的零净变动。</p><select id="code">'+''.join('<option value="'+c+'">'+c+' · '+contracts[c]['name']+'</option>' for c in sorted(contracts,key=lambda c:contracts[c]['variants']['flow_patient']['summary']['pnl_cny'],reverse=True))+'</select><p id="contract-caption"></p><canvas id="plot"></canvas><div id="selected-daily"></div>')
    b.append('<details open><summary>64合约总收益对照（全部保留）</summary>'+table(['合约','品种','初始资金/元','主策略/元','主策略收益率','对照/元','原循环/元','主策略较原循环/元','主策略成交数'],[[c,contracts[c]['name'],money(contracts[c]['initial_cash_cny']),money(contracts[c]['variants']['flow_patient']['summary']['pnl_cny']),f'{contracts[c]["variants"]["flow_patient"]["summary"]["pnl_cny"]/contracts[c]["initial_cash_cny"]*100:+.3f}%',money(contracts[c]['variants']['flow_entry']['summary']['pnl_cny']),money(contracts[c]['variants']['control']['summary']['pnl_cny']),money(contracts[c]['variants']['flow_patient']['summary']['pnl_cny']-contracts[c]['variants']['control']['summary']['pnl_cny']),contracts[c]['variants']['flow_patient']['summary']['fill_count']] for c in sorted(contracts,key=lambda c:contracts[c]['variants']['flow_patient']['summary']['pnl_cny'],reverse=True)])+'</details>')
    for m in MODES:
        b.append('<details><summary>'+LABEL[m]+'：完整64×15每日净收益矩阵</summary>'+table(['合约','品种','全期/元']+[d[4:6]+'/'+d[6:] for d in dates],[[c,contracts[c]['name'],money(contracts[c]['variants'][m]['summary']['pnl_cny'])]+[money(d['pnl_cny']) if d else '—' for d in contracts[c]['variants'][m]['daily']] for c in contracts], 'daily-matrix')+'</details>')
    b.append('</section><section class="card" id="products"><h2>全部36品种：每日净收益</h2><p>同品种内独立合约合计。部分合约缺失时，只计已观测净值变化，其余沿用此前估值；完整缺口见上面的合约矩阵。</p>')
    for m in MODES:
        b.append('<details'+(' open' if m=='flow_patient' else '')+'><summary>'+LABEL[m]+'</summary>'+table(['品种','合约数','预算/元','全期/元']+[d[4:6]+'/'+d[6:] for d in dates],[[n,len(p['contracts']),money(p['budget_cny']),money(p['variants'][m]['net_cny'])]+[money(v) if v is not None else '—' for v in p['variants'][m]['daily_cny']] for n,p in r['products'].items()], 'product-matrix')+'</details>')
    b.append('</section><section class="card" id="method"><h2>完整交易规则与回测口径</h2><ol><li>商品期权买方，每合约最多1手；固定独立现金、零初始库存，现金库存跨日连续，资金不互借。单边每手手续费1.70元，额外委托延迟0毫秒。</li><li>空仓时价差大于一跳则买一加一跳、拟卖一减一跳，否则按买卖一。改善后的毛空间至少覆盖双边3.40元及一跳；同一连续时段过去300秒内推断主动买卖成交各至少一次。行情缺口超过60秒或时段变化清空证据。</li><li>持仓仅维护卖单，开仓过滤不妨碍退出。主策略卖价先保护买入成本＋3.40元＋一跳，最长300墙钟秒；连续30秒中点下跌超过入场价差，或交易时段改变，提前解除；解除后跟随卖一前一跳。对照省去价格保护。</li><li>日盘09:00—10:15、10:30—11:30、13:30—15:00。时段外撤纸面委托、保留库存；不强制日末平仓。没有夜盘成交输入或虚构的夜间退出。</li><li>QMT L1快照、累计量增及最新价推断成交；每次量增最多分配最新价1手证据。必须是创建委托之后的可用成交区间，不认领此前成交。真实队列、冲击与期权行权没有被本回测验证。</li></ol>')
    b.append('<p>净收益 = 已闭环毛利润 + 未平仓买一估值毛利润 − 全部手续费；累计收益率 = 累计净收益 ÷ 固定初始资金。净值指数 = 1 + 累计收益率。JSON中的每日收益率使用当日期初净值为分母，其连乘与全期收益率一致。</p><p>每模型共911个有效合约日、49个缺失格。尚未开始观测的账户预算从起点预留；缺失日期合计曲线沿用上次观测估值，单合约日收益仍保留为空。逐帧合计回撤同样沿用最近估值，不能覆盖夜间或缺失期间未观测价格风险。图表线段连接采样点，不代表缺失期间连续观测。</p>')
    b.append('<p class="note">前向账户单独展示：冻结验收快照 '+r['forward_snapshot_asof']+'（北京时间），128账户、0笔成交、0元收益。当时QMT 58611不可达；本报告未重新检查当前连接，也未启动下单或纸面采集。历史盈利没有写入前向账户。</p>')
    b.append('<p>主策略ID：commodity_flow_20260913_v0_1_r2_flow_patient_replay；对照为同家族flow_entry_replay。原循环取旧研究control账本，已验证其与原始连续循环经济等价。主策略、对照的128账户重放曾通过2,692笔成交、5,828,830帧、1,822账户日和256恢复位置核验；工程验收为756项测试。本次仅从冻结账本生成报告，没有重调策略。</p><p>数据文件：<a href="backtest_data.json">指标及全部日度JSON</a> · <a href="trades.json">成交与闭环明细</a> · <a href="report_qa.json">报告核验</a> · <a href="source_manifest.json">输入哈希</a>。本HTML的图表与交互数据均已内嵌，可单文件离线打开；附件文件请连同报告目录保存。</p></section>')
    payload=json.dumps({'dates':dates,'contracts':contracts,'labels':LABEL},ensure_ascii=False).replace('</','<\\/')
    b.append('<script id="report-data" type="application/json">'+payload+'</script>')
    b.append('''<script>
    const D=JSON.parse(document.getElementById('report-data').textContent),M=['flow_patient','flow_entry','control'],C=['#007d78','#4676bf','#bd6572'];
    const fmt=n=>n.toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
    function draw(){const a=D.contracts[document.getElementById('code').value];
      document.getElementById('contract-caption').textContent='初始资金 '+fmt(a.initial_cash_cny)+' 元；'+M.map(m=>D.labels[m]+' '+fmt(a.variants[m].summary.pnl_cny)+' 元').join(' / ');
      const can=document.getElementById('plot'),w=can.clientWidth,h=330,ratio=window.devicePixelRatio||1;can.width=w*ratio;can.height=h*ratio;const ctx=can.getContext('2d');ctx.scale(ratio,ratio);
      const yy=M.map(m=>[0,...a.variants[m].daily.map(d=>d===null?null:d.cumulative_pnl_cny/a.initial_cash_cny*100)]),v=yy.flat().filter(x=>x!==null),lo=Math.min(...v,0),hi=Math.max(...v,0),pad=Math.max((hi-lo)*.12,.02),mn=lo-pad,mx=hi+pad;
      const xx=i=>65+i*(w-90)/15,y=v=>45+(mx-v)/(mx-mn)*230;ctx.font='12px Microsoft YaHei';
      for(let i=0;i<5;i++){const q=mn+(mx-mn)*i/4;ctx.strokeStyle='#e1e8ef';ctx.beginPath();ctx.moveTo(65,y(q));ctx.lineTo(w-25,y(q));ctx.stroke();ctx.fillStyle='#61758b';ctx.fillText(q.toFixed(2)+'%',4,y(q)+4)}
      M.forEach((m,j)=>{ctx.strokeStyle=C[j];ctx.lineWidth=2;ctx.beginPath();let pen=false;yy[j].forEach((v,i)=>{if(v===null){pen=false;return}if(pen)ctx.lineTo(xx(i),y(v));else ctx.moveTo(xx(i),y(v));pen=true});ctx.stroke();ctx.fillStyle=C[j];ctx.fillText(D.labels[m],65+j*(w-90)/3,20);yy[j].forEach((v,i)=>{if(v!==null){ctx.beginPath();ctx.arc(xx(i),y(v),3,0,Math.PI*2);ctx.fill()}})});
      ctx.fillStyle='#61758b';[0,3,6,9,12,15].forEach(i=>ctx.fillText(i===0?'起点':D.dates[i-1].slice(4,6)+'/'+D.dates[i-1].slice(6),xx(i)-15,300));
      document.getElementById('selected-daily').innerHTML='<div class="scroll"><table><thead><tr><th>日期</th>'+M.map(m=>'<th>'+D.labels[m]+' / 元</th>').join('')+'</tr></thead><tbody>'+D.dates.map((d,i)=>'<tr><td>'+d+'</td>'+M.map(m=>'<td>'+(a.variants[m].daily[i]===null?'—':fmt(a.variants[m].daily[i].pnl_cny))+'</td>').join('')+'</tr>').join('')+'</tbody></table></div>';
    } document.getElementById('code').addEventListener('change',draw);window.addEventListener('resize',draw);draw();
    </script></main></html>''')
    (OUT/'完整回测报告.html').write_text(''.join(b),'utf-8')
    (OUT/'README.md').write_text('# 商品期权回测报告\n\n打开 `完整回测报告.html`，可离线查看收益率、日末回撤、逐帧合计回撤、64合约与36品种全部日度以及单合约切换曲线。\n\n运行生成器：从仓库根目录执行 `.\\.venv\\Scripts\\python.exe -X utf8 scripts/report_commodity_flow_backtest.py`。仅读取冻结回测，不启动行情或改动策略账本。\n\n盘中曲线GZIP JSON为净收益改变的时间戳（毫秒）和累计净收益（人民币分），相同时间戳已合并；日末图从每日最终估值抽样。详见HTML中的口径说明。\n','utf-8')


if __name__ == '__main__':
    main()
