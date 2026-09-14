"""Replay daily newly selected contracts; no reuse of the old fixed pair."""
from pathlib import Path
from collections import defaultdict
import pandas as pd
from zhaiquant import gold_reselection_research as engine
from zhaiquant.gold_callput_research import Clock,timeline
from zhaiquant.gold_history_validation import timeline as call_parent_timeline
from probe_gold_backer import add_fast,audit
from probe_gold_two_mode import LABELS,table,fmt
from probe_gold_rule_ladder import portfolio
from probe_commodity_capital import ROOT,WORK,read,write,pack,unpack,digest
from capture_gold_reselection import OUT,DATES


def replay(rows,code,policy,terms,clock,direction,bps,through,cut=None):
    a=engine.Account(code,policy,terms['OptExercisePrice'],direction,bps,terms['OptionType'],through)
    a.model=a.model.replace(engine.FAMILY,engine.FAMILY+'_'+clock.date)
    for kind,e,features,mid,future in rows:
        ts=e if kind==-1 else e.ts
        if cut is not None and ts>cut:break
        if kind==-1:a.boundary(e)
        elif kind==1:a.future_event(e,features)
        else:a.value.future=future;a.features=features;a.option(e)
    r=a.result();audit(r,cut is None)
    r['summary']['history_date']=clock.date
    for o in r['orders']:
        if o['reason']=='entry':
            assert o['side']==('buy' if direction==1 else 'sell')
            assert 2*o['entry_spread']*10000>=bps*o['entry_mid_twice']
    return clock.restore(r)


def report(summaries,selections):
    groups={}
    for mode in ('long','short'):
        for policy in LABELS:
            for bps in engine.THRESHOLDS:
                for strict in (0,1):
                    subset={k:s for k,s in summaries.items() if s['trade_mode']==mode and s['policy']==policy and s['relative_spread_bps']==bps and s['strict_through']==bool(strict)}
                    rs=[unpack(OUT/'ledgers'/f'{k}.json.gz') for k in subset]
                    p=portfolio(rs)
                    p['independent_minimum_quote_cash_cny']=max(sum(s['minimum_cash_for_all_entry_quotes_cny'] for s in subset.values() if s['history_date']==d) for d in DATES)
                    p.update(daily=[round(sum(s['pnl_cny'] for s in subset.values() if s['history_date']==d),2) for d in DATES],
                        normal_cycles=sum(s['market_cycles'] for s in subset.values()),virtual_cycles=sum(s['virtual_close_count'] for s in subset.values()),
                        removed_tail_gross_cny=sum(s['removed_tail_gross_cny'] for s in subset.values()))
                    groups[f'{mode}_{policy}_bps{bps}_through{strict}']=p
    write(OUT/'comparison.json',groups)
    h=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>黄金期权按百分比每日换约回测</title><style>body{font-family:"Microsoft YaHei",sans-serif;background:#f3f5f9;color:#243249}main{max-width:1550px;margin:auto;padding:22px}p{line-height:1.8}table{border-collapse:collapse;background:white;width:100%;font-size:13px}td,th{padding:9px;text-align:right;white-space:nowrap;border-bottom:1px solid #dce2eb}th{background:#e5edf8}td:first-child,th:first-child{text-align:left}.scroll{overflow:auto;max-height:700px}summary{padding:14px;cursor:pointer}</style><main>',
       '<h1>先换合约，再比较1%、1.5%、2%</h1>',
       '<p>本轮全部排除au2610C960.SF、au2610C952.SF。每天首30分钟按当时主次月、实虚值、权利金及有效盘口先筛选，再分别按1%、1.5%、2%挑最多两张新合约；09:30后回测，多空各自独立。空缺不强行补单，不根据全天利润选约。</p>',
       '<p>初始筛选假设：原始相对价差达到门槛的时间≥50%，且达标盘口扣除双侧改善报价和双费后空间为正的时间≥50%；两侧相容成交更新各≥3。保留原权利金2—100元/克、到期7—120天、盘口深度/新鲜度和活动要求。按较少一侧成交更新数排序，随后按正净空间达标时间比例排序。百分比以中价为分母，阈值包含等号。这些不是已优化的最佳参数。</p>',
       '<p>认购与认沽都纳入，买认沽仍叫先做多期权，卖认沽仍叫先做空期权；认沽Delta为负，黄金上涨对买认沽不利。单边手续费1.70元，额外延迟0，每合约最多1手，九规则其余逻辑保持。</p>',
       '<p>研究口径仍按用户要求：三个休市边界未完成的循环按自身成本整笔排除并退双费，普通已平亏损保留。尾仓排除前毛浮盈亏单列，不能把表中净收益当成实际清仓收益。</p>',
       '<h2>每天实际使用的合约</h2>',table(['日期','门槛','新合约1','新合约2','合格候选数'],[
           [date,f'{b/100:g}%',*(selections[date]['scenarios'][str(b)]['selected']+['空缺']*2)[:2],sum(m['eligible'] for m in selections[date]['scenarios'][str(b)]['rows'].values())]
           for date in DATES for b in engine.THRESHOLDS],'selection'),
       '<h2>选中时的状态（首30分钟）</h2>',table(['日期','门槛','合约','中位权利金','相对价差达标时间','达标且净空间正时间','首30分钟成交手数','较少一侧更新','平均净空间/元每手'],[
           [date,f'{b/100:g}%',c,fmt(m['median_premium']),f'{100*m["relative_time_fraction"]:.2f}%',f'{100*m["positive_net_relative_time_fraction"]:.2f}%',m['volume_increment'],m['balanced_relative_updates'],fmt(m['mean_net_space_cny'])]
           for date in DATES for b in engine.THRESHOLDS for c in selections[date]['scenarios'][str(b)]['selected'] for m in [selections[date]['scenarios'][str(b)]['rows'][c]]],'states')]
    for strict in (0,1):
        h+=['<h2>'+('普通成交' if not strict else '严格穿价成交')+'</h2>']
        for mode,label in [('long','先做多：买期权→卖期权'),('short','先做空：卖期权→买回')]:
            rr=[]
            for policy,name in LABELS.items():
                for b in engine.THRESHOLDS:
                    s=groups[f'{mode}_{policy}_bps{b}_through{strict}']
                    rr.append([name,f'{b/100:g}%',*map(fmt,s['daily']),fmt(s['pnl_cny']),s['normal_cycles'],s['virtual_cycles'],fmt(s['max_drawdown_cny']),fmt(s['removed_tail_gross_cny'])])
            h+=['<h3>'+label+'</h3>',table(['规则','门槛','09-07','09-08','09-09','09-10','09-11','合计','正常循环','尾单','盘中回撤','排除前尾仓毛值'],rr,mode+str(strict))]
    h+=['<details><summary>逐日逐合约完整账户</summary>',table(['日期','合约','模式','规则','门槛','假设','净收益','正常循环','尾单','尾仓毛值'],[
        [s['history_date'],s['code'],s['trade_mode'],LABELS[s['policy']],f'{s["relative_spread_bps"]/100:g}%',
         '严格' if s['strict_through'] else '普通',fmt(s['pnl_cny']),s['market_cycles'],s['virtual_close_count'],fmt(s['removed_tail_gross_cny'])] for s in summaries.values()],'accounts'),'</details>',
        '<p>每代码每天25万元为足额研究预算，不是一手买方实际所需资本；空头20%期货名义额为准备金代理。两方向和不同门槛分别独立，不相加当作共享账户。普通与严格均为L1证据模型，不是真实队列。五天仍为重复开发数据；本轮同时改变选约与门槛，不能把收益差异全部归因于门槛大小。</p>',
        '<p>现有目录覆盖认购认沽，按各历史日上市/到期核对；可能缺少目录已移除的到期合约。2%空缺只表示本轮同时质量条件下不足，不代表全市场不存在2%报价。</p>',
        '<p><a href="comparison.json">日度、回撤与盘中曲线</a> · <a href="results.json">完整账户摘要</a> · <a href="verification.json">核验记录</a></p></main></html>']
    (OUT/'黄金期权_按百分比每日换约回测.html').write_text(''.join(h),'utf-8')
    return groups


def main():
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen output')
    preserved=read(WORK/'reports/gold_relative_spread_20260914_v1/manifest.json')
    inputs=read(OUT/'capture_manifest.json')
    for rel,h in {**preserved,**inputs}.items():assert digest(ROOT/rel)==h,rel
    (OUT/'ledgers').mkdir(exist_ok=True)
    terms=read(OUT/'catalog_terms.json')['details'];selections={d:read(OUT/d/'selection.json') for d in DATES}
    summaries={};prefixes=0;timelines=0;call_matches=0
    for date in DATES:
        clock=Clock(date);selection=selections[date]
        code_bps=defaultdict(list)
        for b,s in selection['scenarios'].items():
            assert engine.select(s['rows'])==s['selected']
            for c in s['selected']:code_bps[c].append(int(b))
        for code,bps_list in sorted(code_bps.items()):
            assert code not in engine.EXCLUDED
            d=terms[code];folder=OUT/date/'full_inputs'
            opt=pd.read_pickle(folder/f'{code}.pkl');fut=pd.read_pickle(folder/f'{d["OptUndlCode"]}.SF.pkl')
            es,fs,_,_=clock.inputs(opt,fut,code,d)
            raw=timeline(es,fs,d['OptExercisePrice'],d['ExpireDate'],clock,d['OptionType'])
            if d['OptionType']==0:
                assert raw==call_parent_timeline(es,fs,d['OptExercisePrice'],d['ExpireDate'],clock)
                call_matches+=1
            rows=add_fast(raw)
            cut=clock.start+5*3600000;ic=cut+clock.shift
            pe,pf,_,_=clock.inputs(opt[opt.time<=cut],fut[fut.time<=cut],code,d)
            pre=add_fast(timeline(pe,pf,d['OptExercisePrice'],d['ExpireDate'],clock,d['OptionType'],ic))
            assert pre==[x for x in rows if (x[1] if x[0]==-1 else x[1].ts)<=ic];timelines+=1
            for b in bps_list:
                for direction in (1,-1):
                    for strict in (0,1):
                        for policy in LABELS:
                            r=replay(rows,code,policy,d,clock,direction,b,bool(strict))
                            pr=replay(pre,code,policy,d,clock,direction,b,bool(strict),ic)
                            for field,t in [('orders','created_ts'),('fills','ts'),('cycles','exit_ts'),('cancels','ts'),('risk_events','ts'),('fee_adjustments','ts')]:
                                assert pr[field]==[x for x in r[field] if x[t]<=cut],(date,code,b,policy,field)
                            prefixes+=1
                            key=f'{date}_{code}_{r["summary"]["trade_mode"]}_{policy}_bps{b}_through{strict}'
                            pack(OUT/'ledgers'/f'{key}.json.gz',r);summaries[key]=r['summary']
                print('REPLAY',date,code,b,'done',flush=True)
    write(OUT/'results.json',summaries);groups=report(summaries,selections)
    for rel,h in {**preserved,**inputs}.items():assert digest(ROOT/rel)==h,rel
    write(OUT/'verification.json',dict(status='passed',accounts=len(summaries),execution_prefixes=prefixes,
        raw_truncated_timeline_prefixes=timelines,call_timelines_exact_parent_matches=call_matches,
        flat_boundaries=3*len(summaries),old_frozen_files_unchanged=len(preserved),zero_old_contract_accounts=True))
    source=[Path(__file__),ROOT/'src/zhaiquant/gold_reselection_research.py',ROOT/'src/zhaiquant/gold_callput_research.py',ROOT/'tests/test_gold_reselection_research.py']
    write(OUT/'source_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in source})
    for mode in ('long','short'):
        for policy in ('trend','trend_fast','value'):
            for b in engine.THRESHOLDS:
                s=groups[f'{mode}_{policy}_bps{b}_through0'];strict=groups[f'{mode}_{policy}_bps{b}_through1']
                print(mode,policy,b,s['daily'],s['pnl_cny'],'strict',strict['pnl_cny'],'cycles',s['normal_cycles'],'tails',s['virtual_cycles'],s['removed_tail_gross_cny'])


if __name__=='__main__':main()
