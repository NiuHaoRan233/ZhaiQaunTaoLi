"""Paired peer-information ablation with immutable baseline reconciliation."""
from concurrent.futures import ProcessPoolExecutor,as_completed
from collections import defaultdict
from pathlib import Path
import pandas as pd
from zhaiquant import gold_peer_risk_research as m
from zhaiquant.gold_callput_research import Clock,timeline
from probe_gold_backer import add_fast,audit,economics
from probe_gold_rule_ladder import portfolio
from probe_commodity_capital import ROOT,WORK,read,write,pack,unpack,digest
from capture_gold_peers import OUT,BASE,DATES

POLICIES=('value','backer_fast')
SESSION_BASE=WORK/'reports/gold_sessions_20260914_v1'


def replay(rows,code,terms,date,mask,policy,d,strict,variant,cut=None):
    clock=Clock(date);a=m.Account(code,policy,terms['OptExercisePrice'],d,terms['OptionType'],mask,variant,bool(strict))
    a.model=a.model.replace(m.FAMILY,m.FAMILY+'_'+date)
    for kind,e,features,mid,future in rows:
        ts=e if kind==-1 else e['ts'] if kind==2 else e.ts
        if cut is not None and ts>cut:break
        if kind==-1:a.boundary(e)
        elif kind==2:a.peer_event(e)
        elif kind==1:a.future_event(e,features)
        else:a.value.future=future;a.features=features;a.option(e)
    r=a.result();audit(r,False)
    if cut is None:
        assert a.inventory==0 and len(r['boundaries'])==len(a.allowed)
        assert round(r['summary']['pnl_cny']*100)==sum(c['net_cents'] for c in r['cycles'])
    r['summary'].update(history_date=date)
    return clock.restore(r)


def job(date,code):
    done=OUT/'jobs'/f'{date}_{code}.json'
    if done.exists():return str(done)
    clock=Clock(date);allterms=read(BASE/'catalog_terms.json')['details'];terms=allterms[code]
    folder=BASE/date/'full_inputs';target=pd.read_pickle(folder/f'{code}.pkl');futures=pd.read_pickle(folder/f'{terms["OptUndlCode"]}.SF.pkl')
    peers={c:pd.read_pickle(OUT/date/'inputs'/f'{c}.pkl') for c in read(OUT/date/'selection.json')[code]['peers']}
    sig,meta=m.signals(target,peers,futures,terms,allterms,clock)
    cut=m.parent.g.START+5*3600000;actual=cut-clock.shift
    pre,_=m.signals(target[target.time<=actual],{c:v[v.time<=actual] for c,v in peers.items()},futures[futures.time<=actual],terms,allterms,clock)
    assert pre==[r for r in sig if r['ts']<=cut]
    for s in sig:
        if not s['ready']:continue
        assert s['target_source_ts']<=s['ts'] and s['target_anchor_ts']<=s['ts']-10000
        assert s['future_source_ts']<=s['ts']
        for p in s['peers']:
            assert p['source_ts']<=s['ts'] and p['anchor_ts']<=s['ts']-10000
            assert p['paired_future_ts']<=p['source_ts'] and p['anchor_future_ts']<=p['anchor_ts']
    pack(OUT/'signals'/f'{date}_{code}.json.gz',clock.restore(sig))
    es,fs,_,_=clock.inputs(target,futures,code,terms)
    base=add_fast(timeline(es,fs,terms['OptExercisePrice'],terms['ExpireDate'],clock,terms['OptionType']))
    allrows=sorted(base+[(2,s,None,None,None) for s in sig],key=lambda r:((r[1] if r[0]==-1 else r[1]['ts'] if r[0]==2 else r[1].ts),r[0]))
    summaries={};reproductions=prefixes=0
    for mask in ('day','pm'):
        allowed=m.parent.MASKS[mask];ends={m.parent.ENDS[s] for s in allowed}
        rows=[r for r in allrows if (r[1] in ends if r[0]==-1 else r[1]['session'] in allowed if r[0]==2 else r[1].session in allowed)]
        for policy in POLICIES:
            for d in (1,-1):
                mode='long' if d==1 else 'short'
                for strict in (0,1):
                    for variant in m.VARIANTS:
                        r=replay(rows,code,terms,date,mask,policy,d,strict,variant)
                        pr=replay(rows,code,terms,date,mask,policy,d,strict,variant,cut)
                        for field,t in [('orders','created_ts'),('fills','ts'),('cycles','exit_ts'),('cancels','ts'),('risk_events','ts'),('fee_adjustments','ts')]:
                            assert pr[field]==[v for v in r[field] if v[t]<=actual],(date,code,mask,variant,field)
                        prefixes+=1
                        if variant=='baseline':
                            old=unpack(SESSION_BASE/'ledgers'/f'{date}_{code}_{mode}_{policy}_bps100_through{strict}_{mask}.json.gz')
                            assert economics(r)==economics(old),(date,code,mask,policy,mode,'baseline changed')
                            reproductions+=1
                        for c in r['cancels']:
                            if c['reason'].startswith('peer_'):assert c['feature']['ts']==c['ts']
                        for ev in r['risk_events']:
                            if ev['reason']=='peer_consensus_exit':assert ev['feature']['ts']<ev['ts']
                        key=f'{date}_{code}_{mask}_{policy}_{mode}_{variant}_through{strict}'
                        pack(OUT/'ledgers'/f'{key}.json.gz',r);summaries[key]=r['summary']
    write(done,dict(summaries=summaries,signal_meta=meta,baseline_reproductions=reproductions,execution_prefixes=prefixes))
    return str(done)


def main():
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen')
    for folder in ('jobs','signals','ledgers','curves'):(OUT/folder).mkdir(exist_ok=True)
    for manifest in (BASE/'manifest.json',SESSION_BASE/'manifest.json',OUT/'capture_manifest.json'):
        for rel,h in read(manifest).items():assert digest(ROOT/rel)==h,rel
    sources=[Path(__file__),ROOT/'src/zhaiquant/gold_peer_risk_research.py',ROOT/'scripts/capture_gold_peers.py',ROOT/'tests/test_gold_peer_risk_research.py']
    write(OUT/'replay_plan.json',dict(source_hashes={str(p.relative_to(ROOT)):digest(p) for p in sources}))
    tasks=[(d,c) for d in DATES for c in read(OUT/d/'selection.json')]
    paths=[]
    with ProcessPoolExecutor(max_workers=3) as pool:
        jobs={pool.submit(job,*t):t for t in tasks}
        for f in as_completed(jobs):paths.append(f.result());print('DONE',jobs[f],len(paths),'/',len(tasks),flush=True)
    summaries={};count=defaultdict(int);coverage={}
    for p in paths:
        r=read(Path(p));summaries.update(r['summaries']);coverage[Path(p).stem]=r['signal_meta']
        for k in ('baseline_reproductions','execution_prefixes'):count[k]+=r[k]
    write(OUT/'results.json',summaries);write(OUT/'signal_coverage.json',coverage)
    index=defaultdict(list)
    for key,s in summaries.items():index[(s['session_mask'],s['policy'],s['trade_mode'],s['peer_variant'],int(s['strict_through']))].append(key)
    groups={}
    for (mask,policy,mode,variant,strict),keys in index.items():
        rr=[unpack(OUT/'ledgers'/f'{k}.json.gz') for k in keys];result=portfolio(rr)
        key=f'{mask}_{policy}_{mode}_{variant}_through{strict}';pack(OUT/'curves'/f'{key}.json.gz',result.pop('curve'))
        result.update(daily=[round(sum(r['summary']['pnl_cny'] for r in rr if r['summary']['history_date']==d),2) for d in DATES],
            normal_cycles=sum(r['summary']['market_cycles'] for r in rr),virtual_cycles=sum(r['summary']['virtual_close_count'] for r in rr),
            removed_tail_gross_cny=sum(r['summary']['removed_tail_gross_cny'] for r in rr),
            peer_cancels=sum(r['summary']['peer_cancel_count'] for r in rr),peer_exits=sum(r['summary']['peer_exit_count'] for r in rr))
        groups[key]=result
        if not strict:print(key,result['daily'],result['pnl_cny'],flush=True)
    write(OUT/'comparison.json',groups);write(OUT/'verification.json',dict(accounts=len(summaries),raw_signal_prefixes=len(tasks),**count))


if __name__=='__main__':main()
