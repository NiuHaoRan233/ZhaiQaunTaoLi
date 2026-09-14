"""Replay 1%, 1.5%, 2% entry gates, all paired policies, frozen four-day inputs."""
from pathlib import Path
from collections import defaultdict
import json
import pandas as pd
from zhaiquant import gold_relative_spread_research as engine
from zhaiquant import gold_direction_research as g
from probe_gold_two_mode import LABELS, table, fmt, OUT as OLD
from probe_gold_backer import BASE,DATES,CODES,Clock,timeline,add_fast,audit
from probe_commodity_capital import ROOT,WORK,read,write,pack,unpack,digest
from probe_gold_rule_ladder import portfolio

OUT=WORK/'reports/gold_relative_spread_20260914_v1'


def replay(rows,code,policy,terms,clock,direction,bps,through,cut=None):
    a=engine.Account(code,policy,terms['OptExercisePrice'],direction,bps,through)
    a.model=a.model.replace(engine.FAMILY,engine.FAMILY+'_'+clock.date)
    for kind,event,features,mid,future in rows:
        ts=event if kind==-1 else event.ts
        if cut is not None and ts>cut:break
        if kind==-1:a.boundary(event)
        elif kind==1:a.future_event(event,features)
        else:a.value.future=future;a.features=features;a.option(event)
    r=a.result();audit(r,cut is None)
    assert all(c['direction']==direction for c in r['cycles'])
    r['summary']['history_date']=clock.date
    return clock.restore(r)


def opportunities(es,date,code):
    total=0;eligible=defaultdict(float);runs=defaultdict(list);active={}
    for i,e in enumerate(es):
        following=es[i+1].ts if i+1<len(es) and es[i+1].session==e.session else g.BOUNDARIES[e.session]
        lo=max(g.CUTOFF,e.ts);hi=min(following,g.BOUNDARIES[e.session],e.ts+60000)
        if hi<=lo:continue
        valid=g.valid(e)
        if valid:total+=hi-lo
        for bps in engine.THRESHOLDS:
            ok=valid and engine.qualifies(e.bid,e.ask,bps)
            old=active.get(bps)
            if old and (not ok or old[1]!=lo or old[2]!=e.session):
                runs[bps].append(old[1]-old[0]);active.pop(bps)
            if ok:
                eligible[bps]+=hi-lo
                if bps in active:active[bps][1]=hi
                else:active[bps]=[lo,hi,e.session]
    for bps,(lo,hi,si) in active.items():runs[bps].append(hi-lo)
    return [dict(date=date,code=code,bps=b,valid_seconds=total/1000,
        eligible_seconds=eligible[b]/1000,eligible_pct=100*eligible[b]/total if total else 0,
        episodes=len(runs[b]),median_episode_seconds=float(pd.Series(runs[b],dtype=float).median()/1000) if runs[b] else 0,
        max_episode_seconds=max(runs[b],default=0)/1000) for b in engine.THRESHOLDS]


def report(summaries,opp):
    groups={};baseline=read(OLD/'comparison.json')
    for mode in ('long','short'):
        for policy in LABELS:
            for strict in (0,1):
                for bps in engine.THRESHOLDS:
                    subset={k:s for k,s in summaries.items() if s['trade_mode']==mode and s['policy']==policy and
                        s['strict_through']==bool(strict) and s['relative_spread_bps']==bps}
                    rs=[unpack(OUT/'ledgers'/f'{k}.json.gz') for k in subset]
                    p=portfolio(rs)
                    p['independent_minimum_quote_cash_cny']=sum(max(r['summary']['minimum_cash_for_all_entry_quotes_cny'] for r in rs if r['summary']['code']==c) for c in CODES)
                    p.update(daily=[round(sum(s['pnl_cny'] for s in subset.values() if s['history_date']==d),2) for d in DATES],
                        normal_cycles=sum(s['market_cycles'] for s in subset.values()),
                        virtual_cycles=sum(s['virtual_close_count'] for s in subset.values()),
                        removed_tail_gross_cny=sum(s['removed_tail_gross_cny'] for s in subset.values()))
                    groups[f'{mode}_{policy}_bps{bps}_through{strict}']=p
    write(OUT/'comparison.json',groups)
    h=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>黄金期权相对价差门槛比较</title>',
       '<style>body{font-family:"Microsoft YaHei",sans-serif;background:#f3f5f9;color:#243249}main{max-width:1500px;margin:auto;padding:24px}p{line-height:1.8}table{border-collapse:collapse;background:white;width:100%;font-size:13px}td,th{padding:9px;text-align:right;white-space:nowrap;border-bottom:1px solid #dce2eb}th{background:#e5edf8}td:first-child,th:first-child{text-align:left}.scroll{overflow:auto;max-height:720px}summary{padding:14px;cursor:pointer}</style><main>',
       '<h1>1%、1.5%、2%：逐帧相对价差过滤</h1>',
       '<p>固定au2610C960.SF、au2610C952.SF，2026年9月7—10日。相对价差＝(卖一−买一)/买卖一中价。仅替换原8跳开仓门槛，满足≥阈值才允许新挂入场单；旧聚合区间成交先结算，持仓退出不受入场价差限制。报价改善、撤改节流和期货风控保持原规则。</p>',
       '<p>每合约最多1手，单边1.70元、额外延迟0，09:30开始。依用户研究口径，三个休市边界未闭环尾单整笔排除并退双费；正常亏损保留。以下收益单位元，均非实际清仓收益。两方向独立账户，不把逐日最赚钱一边拼成策略。</p>']
    for strict in (0,1):
        h+=['<h2>'+('普通成交' if not strict else '严格穿价成交')+'</h2>']
        for mode,label in [('long','先做多：买→卖'),('short','先做空：卖→买')]:
            rr=[]
            for policy,name in LABELS.items():
                for b in (0,*engine.THRESHOLDS):
                    s=baseline[f'{mode}_{policy}_through{strict}'] if not b else groups[f'{mode}_{policy}_bps{b}_through{strict}']
                    rr.append([name,'原8跳' if not b else f'{b/100:g}%',*map(fmt,s['daily']),fmt(s['pnl_cny']),
                        s['normal_cycles'],s['virtual_cycles'],fmt(s['max_drawdown_cny']),fmt(s['removed_tail_gross_cny'])])
            h+=['<h3>'+label+'</h3>',table(['规则','入场门槛','09-07','09-08','09-09','09-10','合计','正常循环','排除尾单','盘中回撤','排除前尾仓毛值'],rr,f'{mode}{strict}')]
    h+=['<h2>满足门槛的行情时间</h2><p>仅描述盘口机会，不代表能成交。按有效报价驻留时间计算，下一帧中断上一帧，最长60秒，不跨休市；百分比的分母是有效报价覆盖时间。持续段是连续满足门槛的报价片段，不能当成真实挂单寿命。</p>',
        table(['日期','合约','门槛','有效分钟','达标分钟','达标时间占比','连续片段','片段中位秒','最长秒'],[
            [s['date'],s['code'],f'{s["bps"]/100:g}%',fmt(s['valid_seconds']/60),fmt(s['eligible_seconds']/60),f'{s["eligible_pct"]:.2f}%',s['episodes'],fmt(s['median_episode_seconds']),fmt(s['max_episode_seconds'])] for s in opp],'opportunities'),
        '<details><summary>全部864账户：逐日逐合约、多空、九规则、三门槛、两成交假设</summary>',
        table(['日期','合约','模式','规则','门槛','成交假设','收益','正常循环','尾单','尾仓毛值'],[
            [s['history_date'],s['code'],s['trade_mode'],LABELS[s['policy']],f'{s["relative_spread_bps"]/100:g}%',
             '严格' if s['strict_through'] else '普通',fmt(s['pnl_cny']),s['market_cycles'],s['virtual_close_count'],fmt(s['removed_tail_gross_cny'])] for s in summaries.values()],'accounts'),'</details>',
        '<p>每代码25万元只是复用原足额研究账户预算，空头20%期货名义额准备金为代理口径。普通和严格假设都未模拟真实队列。四天为重复开发样本，本轮未重新选择其他合约，不能据最优参数声称已获得稳定策略。</p>',
        '<p><a href="comparison.json">日度、风险与曲线JSON</a> · <a href="results.json">864账户摘要</a> · <a href="verification.json">核验</a></p></main></html>']
    (OUT/'黄金期权_相对价差门槛比较.html').write_text(''.join(h),'utf-8')
    return groups


def main():
    OUT.mkdir(exist_ok=True);(OUT/'ledgers').mkdir(exist_ok=True)
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen output')
    preserved=read(OLD/'manifest.json');frozen=read(BASE/'result_manifest.json')
    for rel,h in preserved.items():assert digest(ROOT/rel)==h,rel
    sources=[ROOT/'src/zhaiquant/gold_relative_spread_research.py',Path(__file__),ROOT/'tests/test_gold_relative_spread_research.py']
    write(OUT/'plan.json',dict(family=engine.FAMILY,bps=list(engine.THRESHOLDS),policies=list(LABELS),
        dates=DATES,codes=CODES,directions=[1,-1],replace_absolute_floor=True,parent_manifest=digest(OLD/'manifest.json'),
        extra_latency_ms=0,fee_one_side_cny=1.7,settlement='exclude entire pending break cycle including fees'))
    terms=read(BASE/'catalog_terms.json')['details'];summaries={};opp=[];inputs={};prefixes=0
    for date in DATES:
        clock=Clock(date)
        for code in CODES:
            d=terms[code];folder=BASE/date/'full_inputs'
            paths=[folder/f'{code}.pkl',folder/f'{d["OptUndlCode"]}.SF.pkl']
            for path in paths:
                rel=str(path.relative_to(ROOT));assert digest(path)==frozen[rel];inputs[rel]=digest(path)
            opt,fut=map(pd.read_pickle,paths);es,fs,_,_=clock.inputs(opt,fut,code,d)
            rows=add_fast(timeline(es,fs,d['OptExercisePrice'],d['ExpireDate'],clock))
            opp+=opportunities(es,date,code)
            cut=clock.start+5*3600000;ic=cut+clock.shift
            pe,pf,_,_=clock.inputs(opt[opt.time<=cut],fut[fut.time<=cut],code,d)
            pre=add_fast(timeline(pe,pf,d['OptExercisePrice'],d['ExpireDate'],clock,cut=ic))
            assert pre==[x for x in rows if (x[1] if x[0]==-1 else x[1].ts)<=ic]
            for direction in (1,-1):
                for bps in engine.THRESHOLDS:
                    for strict in (0,1):
                        for policy in LABELS:
                            r=replay(rows,code,policy,d,clock,direction,bps,bool(strict))
                            pr=replay(pre,code,policy,d,clock,direction,bps,bool(strict),cut=ic)
                            for field,t in [('orders','created_ts'),('fills','ts'),('cycles','exit_ts'),('cancels','ts'),('risk_events','ts'),('fee_adjustments','ts')]:
                                assert pr[field]==[v for v in r[field] if v[t]<=cut],(date,code,bps,policy,field)
                            prefixes+=1
                            key=f'{date}_{code}_{r["summary"]["trade_mode"]}_{policy}_bps{bps}_through{strict}'
                            pack(OUT/'ledgers'/f'{key}.json.gz',r);summaries[key]=r['summary']
                    print(date,code,direction,bps,'complete',flush=True)
    write(OUT/'results.json',summaries);write(OUT/'opportunities.json',opp);write(OUT/'input_manifest.json',inputs)
    groups=report(summaries,opp)
    for rel,h in preserved.items():assert digest(ROOT/rel)==h,rel
    write(OUT/'verification.json',dict(status='passed',accounts=len(summaries),execution_prefixes=prefixes,
        raw_truncated_timeline_prefixes=8,flat_boundaries=3*len(summaries),old_frozen_files_unchanged=len(preserved)))
    write(OUT/'source_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in sources})
    for mode in ('long','short'):
        for policy in ('trend','trend_fast','value'):
            for b in engine.THRESHOLDS:
                s=groups[f'{mode}_{policy}_bps{b}_through0'];strict=groups[f'{mode}_{policy}_bps{b}_through1']
                print(mode,policy,b,s['daily'],s['pnl_cny'],'strict',strict['pnl_cny'],'cycles',s['normal_cycles'],'tails',s['virtual_cycles'],s['removed_tail_gross_cny'])


if __name__=='__main__':main()
