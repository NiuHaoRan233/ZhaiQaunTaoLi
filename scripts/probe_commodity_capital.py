"""Frozen small hypothesis set, followed by causally funded portfolio replay."""
from pathlib import Path
from collections import Counter
from itertools import groupby
import argparse
import gzip
import hashlib
import heapq
import json
import pickle
import numpy as np

from zhaiquant import commodity_capital_research as engine
from zhaiquant.commodity_selective_research import prepare

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'广义套利'
OLD=WORK/'reports/commodity_optimization_20260912'
BASE=WORK/'reports/commodity_strategy_20260913_r2'
OUT=WORK/'reports/commodity_capital_20260913'

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text('utf-8'))
def unpack(p):return json.loads(gzip.decompress(p.read_bytes()))
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False),'utf-8')
def pack(p,v):
    with gzip.open(p,'wt',encoding='utf-8',compresslevel=1) as f:json.dump(v,f,ensure_ascii=False,separators=(',',':'))
def economic(rows):return [{k:v for k,v in r.items() if k!='model_id'} for r in rows]

def aggregate(rows, capital):
    daily=Counter();changes=[];cycles=[]
    for code,a in rows.items():
        for d in a['daily']:daily[d['date']]+=round(d['pnl_cny']*100)
        ar=np.asarray(a['curve'],dtype=np.int64)
        changes.append(np.column_stack((ar[:,0],np.diff(ar[:,1],prepend=0))))
        cycles.extend(dict(c,code=code) for c in a['cycles'])
    ar=np.concatenate(changes);ar=ar[np.argsort(ar[:,0],kind='stable')]
    ts,idx=np.unique(ar[:,0],return_index=True);pnl=np.cumsum(np.add.reduceat(ar[:,1],idx))
    peak=np.maximum.accumulate(np.maximum(capital+pnl,capital));dd=peak-capital-pnl
    fees=sum(round(a['summary']['fees_cny']*100) for a in rows.values())
    net=int(pnl[-1]);ds=[];cum=0
    for date,p in sorted(daily.items()):
        cum+=p;ds.append(dict(date=date,pnl_cny=p/100,cumulative_pnl_cny=cum/100))
    assert cum==net
    early=sum(p for d,p in daily.items() if d<='20260904')
    cutoff=1788505199999
    early_wins=sorted([c['net_cents'] for c in cycles if c['exit_ts']<=cutoff and c['net_cents']>0],reverse=True)
    s=dict(pnl_cny=net/100,initial_cash_cny=capital/100,return_pct=net/capital*100,
        fees_cny=fees/100,cycles=len(cycles),fills=sum(a['summary']['fill_count'] for a in rows.values()),
        max_drawdown_cny=int(dd.max())/100,max_drawdown_pct=float(np.max(dd/peak*100)),
        early_pnl_cny=early/100,later_pnl_cny=sum(p for d,p in daily.items() if '20260907'<=d<='20260910')/100,
        final_day_pnl_cny=daily['20260911']/100,
        early_without_top_three_cny=(early-sum(early_wins[:3]))/100,
        same_day_cycle_net_cny=sum(c['net_cents'] for c in cycles if c['entry_ts']//86400000==c['exit_ts']//86400000)/100,
        completed_cycle_net_cny=sum(c['net_cents'] for c in cycles)/100,
        open_cycle_contribution_cny=(net-sum(c['net_cents'] for c in cycles))/100,
        tail_contracts=sum(a['summary']['end_inventory'] for a in rows.values()))
    return dict(summary=s,daily=ds,curve=np.column_stack((ts,pnl)).tolist())

def setup():
    OUT.mkdir(exist_ok=True);(OUT/'independent').mkdir(exist_ok=True);(OUT/'shared').mkdir(exist_ok=True)
    source={str(p.relative_to(ROOT)):digest(p) for p in [Path(engine.__file__),ROOT/'src/zhaiquant/commodity_flow_strategy.py',ROOT/'src/zhaiquant/commodity_selective_research.py',Path(__file__)]}
    contract=dict(family=engine.FAMILY,profiles=engine.PROFILES,source_hashes=source,
        capital_grid_cny=[100000,200000,300000,500000],shared_profiles='base and best nonbase by early net minus largest three early winning closes',
        evaluation='All dates previously inspected; no untouched OOS. Do not choose capital solely by highest historical return.',
        code_universe=read(OLD/'candidate_contract.json')['code_list'],fee_cents=170,delay_ms=0,capacity_per_contract=1,
        allocation='Market timestamp ascending, ties code ascending. Reserve premium+fee at buy order acceptance; release cancellation or sale; continuous shared cash.',
        per_code_ceiling='Original virtual budget retained as ceiling, not separately funded capital.',
        stale_quotes='Reservations retained until next contract update or global session change. No optimistic release on absent quotes.',
        no_live_connection=True)
    p=OUT/'candidate_contract.json'
    if p.exists():assert read(p)==contract,'Frozen research code or plan changed'
    else:write(p,contract)
    preserved={}
    for folder in [BASE,OLD]:
        manifest=read(folder/'result_manifest.json')
        for rel,h in manifest.items():assert digest(ROOT/rel)==h,rel
        preserved.update(manifest)
    write(OUT/'preserved_result_manifest.json',preserved)
    return contract

def independent(contract):
    counts=Counter();inputs={}
    for i,code in enumerate(sorted(contract['code_universe'])):
        targets={v:OUT/'independent'/f'{code}_{v}.json.gz' for v in engine.PROFILES}
        cache=pickle.loads((OLD/'event_cache'/f'{code}.pkl').read_bytes())
        inputs[code]=cache['tag']
        for date,h in cache['tag']['inputs']:
            assert digest(WORK/'data'/f'{code}_{date}_tick.pkl')==h
            counts['input_days']+=1
        if all(p.exists() for p in targets.values()):continue
        events,dates,meta=prepare(cache['inputs'])
        for profile,target in targets.items():
            if target.exists():continue
            a=engine.ResearchAccount(code,profile,meta['initial_cents'],meta['tick_cents'])
            rows={k:[] for k in ['orders','fills','cycles','curve']}
            saved=unpack(BASE/'accounts'/f'{code}_flow_patient.json.gz') if profile=='base' else None
            for j,e in enumerate(events):
                delta=a.step(e,dates[e.ts])
                if saved:assert list(delta['curve'])==saved['curve'][j],(code,j)
                for k in ['orders','fills','cycles']:rows[k].extend(delta[k])
                if not rows['curve'] or list(delta['curve'][1:])!=rows['curve'][-1][1:]:rows['curve'].append(list(delta['curve']))
            rows.update(summary=a.summary(),daily=a.daily())
            if saved:
                for k in ['orders','fills']:assert economic(rows[k])==economic(saved[k]),(code,k)
                assert rows['daily']==saved['daily'] and rows['cycles']==saved['cycles']
            assert sum(f['fee_cents'] for f in rows['fills'])==round(rows['summary']['fees_cny']*100)
            assert sum(c['net_cents'] for c in rows['cycles'])+round(rows['summary']['open_cycle_contribution_cny']*100)==round(rows['summary']['pnl_cny']*100)
            pack(target,rows)
        print('independent',i+1,'/64',code,flush=True)
    write(OUT/'input_manifest.json',inputs)
    result={}
    for v in engine.PROFILES:
        rows={c:unpack(OUT/'independent'/f'{c}_{v}.json.gz') for c in contract['code_universe']}
        result[v]=aggregate(rows,169800000)
        result[v]['accounts']={c:dict(summary=a['summary'],daily=a['daily']) for c,a in rows.items()}
        print('PROFILE',v,json.dumps(result[v]['summary']),flush=True)
    assert result['base']['summary']['pnl_cny']==21839.7
    winner=max((v for v in engine.PROFILES if v!='base'),key=lambda v:(result[v]['summary']['early_without_top_three_cny'],v))
    write(OUT/'independent_summary.json',dict(results=result,selected_nonbase=winner,counts=dict(counts)))
    print('SELECTED',winner,flush=True)

def stream(code):
    cache=pickle.loads((OLD/'event_cache'/f'{code}.pkl').read_bytes())
    events,dates,meta=prepare(cache['inputs'])
    del cache
    for e in events:yield e.ts,code,e,dates[e.ts]

def shared(contract):
    study=read(OUT/'independent_summary.json');winner=study['selected_nonbase']
    cfg=read(WORK/'commodity_strategy/strategy_r2.json')
    instruments={v['code']:dict(initial_cents=v['initial_cents'],tick_cents=round(v['price_tick']*v['unit']*100)) for v in cfg['instruments']}
    specs=[('base',1698000)]+[(p,c) for p in ['base',winner] for c in contract['capital_grid_cny']]
    portfolios={f'{p}_{c}':engine.SharedPortfolio(instruments,p,c*100) for p,c in specs}
    # All streams only expose current/past events. Sorted code is the explicit tie-break.
    merged=heapq.merge(*(stream(c) for c in sorted(instruments)),key=lambda x:(x[0],x[1]))
    count=0;last_date=None
    for ts,group in groupby(merged,key=lambda x:x[0]):
        date=None
        for _,code,e,date in group:
            for p in portfolios.values():p.step(code,e,date)
            count+=1
        for p in portfolios.values():p.mark(ts,date)
        if date!=last_date:
            print('shared day',date,'frames',count,flush=True);last_date=date
    results={}
    for key,p in portfolios.items():
        a=p.result();pack(OUT/'shared'/f'{key}.json.gz',a)
        if key=='base_1698000':
            for code,row in a['accounts'].items():
                baseline=study['results']['base']['accounts'][code]
                assert row['daily']==baseline['daily'],code
                saved=unpack(BASE/'accounts'/f'{code}_flow_patient.json.gz')
                pf=[{k:v for k,v in f.items() if k not in ('model_id','portfolio_model_id','portfolio_cash_cents','portfolio_reserved_cents')} for f in a['fills'] if f['code']==code]
                assert pf==economic(saved['fills']),code
        results[key]={k:a[k] for k in ['summary','daily','accounts','curve']}
        print('SHARED',key,json.dumps(a['summary']),flush=True)
    for rel,h in read(OUT/'preserved_result_manifest.json').items():assert digest(ROOT/rel)==h,rel
    write(OUT/'shared_summary.json',dict(results=results,frames=count,selected_nonbase=winner,
        funding_checks='Every input frame: nonnegative shared cash, all live buy orders funded, equity and cash reconciliation',
        baseline_equivalence='1698000 shared base reproduces all 64 frozen baseline fill and daily paths'))
    files=[p for p in OUT.rglob('*') if p.is_file() and p.name!='result_manifest.json']
    write(OUT/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in files})

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',choices=['independent','shared','all'],default='all');args=parser.parse_args()
    contract=setup()
    if args.stage in ('independent','all'):independent(contract)
    if args.stage in ('shared','all'):shared(contract)

if __name__=='__main__':main()
