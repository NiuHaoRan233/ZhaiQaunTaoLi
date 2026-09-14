"""Frozen historical comparison of every structural gold ladder/fusion variant."""
from pathlib import Path
from dataclasses import asdict
from collections import Counter
import argparse
import json
import numpy as np
import pandas as pd
from zhaiquant import gold_direction_research as g
from zhaiquant import gold_rule_ladder_research as ladder
from zhaiquant import gold_rule_ladder_simplification as simple
from zhaiquant import gold_spread_future_research as fusion
from zhaiquant import gold_spread_quote_control as control
from zhaiquant.gold_history_validation import Clock, timeline, FAMILY
from capture_gold_history import OUT, DATES, FIXED
from probe_commodity_capital import ROOT, read, write, pack, unpack, digest
from probe_gold_rule_ladder import audit, portfolio


def cases():
    result={}
    for name, cfg in ladder.cases().items():
        for d in (1,-1):
            key=f'ladder_{name}_{"long" if d==1 else "short"}'
            result[key]=dict(engine='ladder',case=name,direction=d,label=cfg['label']+('｜先多' if d==1 else '｜先空'),rules=asdict(cfg['rules']))
    for name,cfg in simple.CASES.items():
        if name.startswith('spread'):continue # Exact shared spread rules already represented.
        for d in (1,-1):
            result[f'simple_{name}_{"long" if d==1 else "short"}']=dict(engine='simple',case=name,direction=d,label=cfg['label']+('｜先多' if d==1 else '｜先空'),rules=asdict(cfg['rules']))
    labels={'base_long':'8跳纯多','base_short':'8跳纯空','trend_long':'8跳＋期货趋势多头','trend_short':'8跳＋期货趋势空头',
        'value_top_long':'8跳＋估值多头，直接退出','value_top_short':'8跳＋估值空头，直接退出',
        'value_long':'8跳＋估值多头＋有限保护','value_short':'8跳＋估值空头＋有限保护','value_switch':'8跳＋估值择向＋有限保护',
        'trend_switch':'估值择向＋期货趋势','cancel_switch':'估值择向＋趋势＋期货先撤单','manage_switch':'再加期货解除持仓保护',
        'throttle_long':'估值多头＋旧节流','throttle_switch':'估值择向＋旧节流'}
    for name, cfg in fusion.PROFILES.items():
        result[f'fusion_{name}']=dict(engine='fusion',case=name,direction=0,label=labels[name],rules=asdict(cfg),spread=8)
    for name in control.PROFILES:
        result[f'v02_{name}']=dict(engine='control',case=name,direction=0,
            label='0.2主版：趋势多头＋少追价' if name=='trend_long' else '0.2增强：估值择向＋先撤单＋少追价',spread=8)
    return result


CASES=cases()


def replay(rows, code, key, detail, clock, through=False, capital=250000, prefix=False):
    cfg=CASES[key]; kind=cfg['engine'];strike=float(detail['OptExercisePrice'])
    cls={'ladder':ladder.Account,'simple':simple.Account,'fusion':fusion.Account,'control':control.Account}[kind]
    if kind in ('ladder','simple'):
        a=cls(code,cfg['case'],cfg['direction'],strike,capital,through)
    else:
        a=cls(code,cfg['case'],strike,8,through,capital)
    parent_id=a.model
    a.model=f'{FAMILY}_{clock.date}_{key}_{code}_capital{a.initial}_through{int(through)}'
    failure=None
    for event_kind,event,features,mid,future in rows:
        try:
            if event_kind==-1:a.boundary(event)
            elif event_kind==1:
                if kind in ('fusion','control'):a.future_event(event,features)
            else:
                a.value.future=future
                if kind in ('fusion','control'):a.features=features
                else:a.value.current=features[cfg['direction']] if a.rules.fast else mid
                a.option(event)
        except ValueError as exc:
            if str(exc)!='No executable quote in pre-break window':raise
            failure=dict(ts=event,reason=str(exc),inventory=a.inventory,
                last_quote_ts=a.last_book.ts if a.last_book else None,
                last_quote_mark_pnl_cny=a.rows['curve'][-1][1]/100)
            break
    r=a.result();s=r['summary']
    s.update(history_date=clock.date,case_key=key,parent_model_id=parent_id,actual_expiry=detail['ExpireDate'],
        status='failed_flatten' if failure else 'prefix' if prefix else 'complete',failure=failure)
    audit(r,complete=failure is None and not prefix)
    ar=np.array(r['curve'],dtype=np.int64)
    assert int((np.maximum.accumulate(np.maximum(ar[:,1],0))-ar[:,1]).max())==round(s['max_drawdown_cny']*100)
    s.update(order_count=len(r['orders']),cancel_count=len(r['cancels']),
        reprice_cancel_count=sum(c['reason']=='reprice' for c in r['cancels']),
        observed_pnl_cny=s['pnl_cny'])
    if failure:
        # Marked stopped-account value is evidence, never a full-day result.
        s['pnl_cny']=None
    return r


def comparable(x):
    if isinstance(x,dict):return {k:comparable(v) for k,v in x.items() if k not in ('model_id','feature','entry_signal','entry_fill_future')}
    if isinstance(x,(list,tuple)):return [comparable(v) for v in x]
    return x


def baseline_check():
    from probe_gold_direction import inputs
    from probe_gold_spread_future import timeline as original_timeline
    from probe_gold_rule_ladder import replay as old_ladder_replay, timeline as old_ladder_timeline
    from probe_gold_spread_quote_control import replay as old_control_replay
    checked=[]
    for code in FIXED:
        es,fs,detail=inputs(code);clock=Clock('20260911');strike=float(detail['OptExercisePrice'])
        rows=timeline(es,fs,strike,detail['ExpireDate'],clock)
        old_rows=original_timeline(es,fs,strike)
        assert [(k,e,f,fu) for k,e,f,mid,fu in rows]==old_rows
        for through in (False,True):
            for profile in control.PROFILES:
                old=old_control_replay(old_rows,code,profile,strike,8,through,20000 if profile=='trend_long' else 250000)
                new=replay(rows,code,'v02_'+profile,detail,clock,through,20000 if profile=='trend_long' else 250000)
                for field in ('orders','fills','cycles','cancels','curve','boundaries','funding_demands'):
                    # Main fused timeline is exactly equal, including features; identity alone changes.
                    def strip_id(x):
                        if isinstance(x,dict):return {k:strip_id(v) for k,v in x.items() if k!='model_id'}
                        if isinstance(x,(list,tuple)):return [strip_id(v) for v in x]
                        return x
                    assert strip_id(old[field])==strip_id(new[field]),(code,profile,field)
                checked.append(f'{code}_{profile}_{through}')
        old_ladder_rows,_=old_ladder_timeline(es,fs,strike)
        for key,cfg in CASES.items():
            if cfg['engine']!='ladder':continue
            old=old_ladder_replay(old_ladder_rows,code,cfg['case'],cfg['direction'],strike)
            new=replay(rows,code,key,detail,clock)
            for field in ('orders','fills','cycles','cancels','curve','boundaries'):
                assert comparable(old[field])==comparable(new[field]),(code,key,field)
        print('BASELINE_EQUAL',code,flush=True)
    write(OUT/'baseline_verification.json',dict(status='passed',controlled_accounts=checked,
        ladder_accounts=2*sum(c['engine']=='ladder' for c in CASES.values()),source_timeline_exact=True))


def setup():
    assert (OUT/'full_input_manifest.json').exists(),'Capture and freeze full inputs first'
    for p,h in read(OUT/'full_input_manifest.json').items():assert digest(ROOT/p)==h
    sources=[Path(__file__),ROOT/'scripts/capture_gold_history.py']+list((ROOT/'src/zhaiquant').glob('gold_*.py'))+[
        ROOT/'src/zhaiquant/commodity_dadao_research.py',ROOT/'scripts/probe_gold_rule_ladder.py']
    plan=dict(family=FAMILY,dates=DATES,cases=CASES,through=[False,True],capital_per_code_cny=250000,
        fee_cny=1.7,extra_delay_ms=0,selection='Frozen09:00-09:30 call selector; same pair all4days',
        comparison='Every structural ladder/removal/spread/simplification/fusion variant, no historical retuning. Duplicate six spread rules counted once; fusion base pair retained as ancestry check.',
        funding='Common250k/code resets daily for strategy comparison; cumulative curve is sum of daily PnL, not a continuously funded live account. Separate0.2main uses two20k slots with cash carried forward.',
        failures='Stop and expose a failed account at any boundary with inventory and no quote during final5s. No fake close. Totals require all days complete.',
        sources={str(p.relative_to(ROOT)):digest(p) for p in sources})
    target=OUT/'replay_plan.json'
    if target.exists():assert read(target)==plan,'Frozen plan changed'
    else:write(target,plan)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--baseline',action='store_true');parser.add_argument('--date');args=parser.parse_args()
    if args.baseline:baseline_check();return
    setup();details=read(OUT/'catalog_terms.json')['details']
    dates=[args.date] if args.date else DATES
    for date in dates:
        folder=OUT/date;dest=folder/'ledgers';dest.mkdir(exist_ok=True);clock=Clock(date)
        chosen=sorted(set(FIXED+read(folder/'selection.json')['selected']))
        summaries={}
        for code in chosen:
            f=pd.read_pickle(folder/'full_inputs'/f'{code}.pkl');detail=details[code]
            fs=pd.read_pickle(folder/'full_inputs'/f'{detail["OptUndlCode"]}.SF.pkl')
            es,futures,flags,meta=clock.inputs(f,fs,code,detail)
            rows=timeline(es,futures,detail['OptExercisePrice'],detail['ExpireDate'],clock)
            cut=g.START+5*3600000
            pre=timeline([e for e in es if e.ts<=cut],[e for e in futures if e.ts<=cut],detail['OptExercisePrice'],detail['ExpireDate'],clock,cut)
            assert pre==[x for x in rows if (x[1] if x[0]==-1 else x[1].ts)<=cut]
            write(folder/f'{code}_input_audit.json',dict(flags=flags,meta=meta,option_events=len(es),future_events=len(futures),timeline_prefix_equal=True))
            for index,key in enumerate(CASES):
                for through in (False,True):
                    name=f'{code}_{key}_through{int(through)}';path=dest/f'{name}.json.gz'
                    if path.exists():
                        summaries[name]=unpack(path)['summary'];continue
                    r=replay(rows,code,key,detail,clock,through)
                    prefix=replay(pre,code,key,detail,clock,through,prefix=True)
                    for field,t in [('orders','created_ts'),('fills','ts'),('cycles','exit_ts'),('cancels','ts')]:
                        assert prefix[field]==[x for x in r[field] if x[t]<=cut],(date,code,key,field)
                    restored=clock.restore(r)
                    assert all(clock.start<=x['ts']<clock.start+21600000 for x in restored['fills'])
                    pack(path,restored);summaries[name]=restored['summary']
                if index%10==0:print('REPLAY',date,code,index+1,len(CASES),flush=True)
            print('DAY_CODE_DONE',date,code,Counter(s['status'] for n,s in summaries.items() if n.startswith(code)),flush=True)
        write(folder/'results.json',summaries)
    for p,h in read(OUT/'replay_plan.json')['sources'].items():assert digest(ROOT/p)==h


if __name__=='__main__':main()
