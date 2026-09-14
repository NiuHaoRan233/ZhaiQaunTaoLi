"""Full new paths under the user's cost-price daily virtual-settlement rule."""
from collections import Counter
from itertools import groupby
from pathlib import Path
import argparse
import heapq
import pickle

from zhaiquant import commodity_intraday_research as engine
from zhaiquant.commodity_selective_research import prepare
from probe_commodity_capital import ROOT,WORK,OLD,BASE,read,write,digest,pack,unpack,stream,aggregate

OUT=WORK/'reports/commodity_intraday_20260913'
METRICS=['market_closed_cycles','market_cycle_net_cny','virtual_close_count','virtual_roundtrip_fees_cny',
    'virtual_cycle_net_cny','market_fill_sides','quote_mark_pnl_before_virtual_close_cny',
    'virtual_mark_adjustment_cny','stale_virtual_closes']

def setup():
    OUT.mkdir(exist_ok=True);(OUT/'independent').mkdir(exist_ok=True);(OUT/'shared').mkdir(exist_ok=True)
    sources=[Path(engine.__file__),ROOT/'src/zhaiquant/commodity_capital_research.py',
        ROOT/'src/zhaiquant/commodity_flow_strategy.py',ROOT/'scripts/probe_commodity_capital.py',Path(__file__)]
    contract=dict(family=engine.FAMILY,profiles=engine.PROFILES,
        rule='Every observed day 15:00, virtual sell open position at its own entry cost, gross zero, both side fees retained; flat next day and continuous cash.',
        virtual_settlement_is_not_market_execution=True,fee_cents=170,delay_ms=0,capacity_per_contract=1,
        shared_specs=[(p,300000) for p in engine.PROFILES]+[('base',1698000),('edge10',200000),('edge10',500000)],
        code_universe=read(OLD/'candidate_contract.json')['code_list'],
        source_hashes={str(p.relative_to(ROOT)):digest(p) for p in sources},
        note='All dates previously seen. Daily cost reset neutralizes unclosed gross PnL by user instruction. Report pre-reset floating PnL separately.')
    target=OUT/'candidate_contract.json'
    if target.exists():assert read(target)==contract,'Frozen intraday study changed'
    else:write(target,contract)
    preserved={}
    for d in [BASE,OLD,WORK/'reports/commodity_capital_20260913',WORK/'reports/commodity_capital_extension_20260913']:
        for p,h in read(d/'result_manifest.json').items():assert digest(ROOT/p)==h,p;preserved[p]=h
    write(OUT/'preserved_result_manifest.json',preserved)
    return contract

def append(rows,delta):
    for k in ['orders','fills','cycles']:rows[k].extend(delta[k])
    if 'curve' in delta:
        c=list(delta['curve'])
        if not rows['curve'] or c[1:]!=rows['curve'][-1][1:]:rows['curve'].append(c)

def independent(contract):
    hashes={};counts=Counter()
    for i,code in enumerate(sorted(contract['code_universe'])):
        cache=pickle.loads((OLD/'event_cache'/f'{code}.pkl').read_bytes())
        hashes[code]=cache['tag']
        for d,h in cache['tag']['inputs']:
            assert digest(WORK/'data'/f'{code}_{d}_tick.pkl')==h;counts['input_days']+=1
        events,dates,meta=prepare(cache['inputs'])
        for profile in engine.PROFILES:
            path=OUT/'independent'/f'{code}_{profile}.json.gz'
            if path.exists():continue
            a=engine.IntradayAccount(code,profile,meta['initial_cents'],meta['tick_cents'])
            rows={k:[] for k in ['orders','fills','cycles','curve']};day=None
            for e in events:
                date=dates[e.ts]
                if day is not None and date!=day:append(rows,a.close_day(day))
                day=date;append(rows,a.step(e,date))
            append(rows,a.close_day(day))
            rows.update(summary=a.summary(),daily=a.daily())
            assert all(d['end_inventory']==0 for d in rows['daily'])
            assert all(c['entry_ts']//86400000==c['exit_ts']//86400000 for c in rows['cycles'])
            assert sum(c['net_cents'] for c in rows['cycles'])==round(rows['summary']['pnl_cny']*100)
            assert sum(f['fee_cents'] for f in rows['fills'])==round(rows['summary']['fees_cny']*100)
            pack(path,rows)
        print('intraday independent',i+1,'/64',code,flush=True)
    results={}
    for profile in engine.PROFILES:
        rows={code:unpack(OUT/'independent'/f'{code}_{profile}.json.gz') for code in contract['code_universe']}
        r=aggregate(rows,169800000)
        for key in METRICS:r['summary'][key]=round(sum(a['summary'][key] for a in rows.values()),2)
        r['accounts']={code:{k:a[k] for k in ['summary','daily']} for code,a in rows.items()}
        assert r['summary']['tail_contracts']==0
        results[profile]=r
        print('INTRADAY PROFILE',profile,r['summary'],flush=True)
    write(OUT/'input_manifest.json',hashes)
    write(OUT/'independent_summary.json',dict(results=results,counts=dict(counts)))

def instruments():
    cfg=read(WORK/'commodity_strategy/strategy_r2.json')
    return {v['code']:dict(initial_cents=v['initial_cents'],tick_cents=round(v['unit']*v['price_tick']*100)) for v in cfg['instruments']}

def run_portfolios(ps,through=None):
    merged=heapq.merge(*(stream(c) for c in sorted(next(iter(ps.values())).accounts)),key=lambda x:(x[0],x[1]))
    last=None;count=0
    for ts,group in groupby(merged,key=lambda x:x[0]):
        if through and ts>engine.close_timestamp(through):break
        for _,code,e,date in group:
            if last is not None and date!=last:
                for p in ps.values():p.close_day(last)
            if date!=last:print('intraday shared',date,'frames',count,flush=True)
            last=date
            for p in ps.values():p.step(code,e,date)
            count+=1
        for p in ps.values():p.mark(ts,date)
    if last is not None:
        for p in ps.values():p.close_day(last)
    return count

def shared(contract):
    ins=instruments()
    ps={f'{p}_{c}':engine.IntradayPortfolio(ins,p,c*100) for p,c in contract['shared_specs']}
    frames=run_portfolios(ps);results={}
    independent=read(OUT/'independent_summary.json')
    for key,p in ps.items():
        r=p.result();pack(OUT/'shared'/f'{key}.json.gz',r)
        if key=='base_1698000':
            for code,a in r['accounts'].items():
                old=unpack(OUT/'independent'/f'{code}_base.json.gz')
                assert a['daily']==old['daily'],code
                def econ(fs):return [{k:v for k,v in f.items() if k not in ['model_id','portfolio_model_id','portfolio_cash_cents','portfolio_reserved_cents']} for f in fs]
                assert econ([f for f in r['fills'] if f['code']==code])==econ(old['fills']),code
        results[key]={k:r[k] for k in ['summary','daily','accounts','curve']}
        print('INTRADAY SHARED',key,r['summary'],flush=True)
    write(OUT/'shared_summary.json',dict(results=results,frames=frames))
    # Fresh replay of a chronological prefix, including that day's virtual exits.
    prefix={p:engine.IntradayPortfolio(ins,p,30000000) for p in ['base','edge10']}
    run_portfolios(prefix,through='20260904')
    cutoff=engine.close_timestamp('20260904')
    for profile,p in prefix.items():
        saved=unpack(OUT/'shared'/f'{profile}_300000.json.gz');r=p.result()
        assert r['fills']==[f for f in saved['fills'] if f['ts']<=cutoff]
        assert r['orders']==[o for o in saved['orders'] if o['created_ts']<=cutoff]
        assert r['daily']==[d for d in saved['daily'] if d['date']<='20260904']
    for p,h in read(OUT/'preserved_result_manifest.json').items():assert digest(ROOT/p)==h,p
    for p,h in contract['source_hashes'].items():assert digest(ROOT/p)==h,p
    write(OUT/'verification.json',dict(status='passed',independent_accounts=384,shared_portfolios=len(ps),
        frames_per_shared_portfolio=frames,all_end_of_day_inventory_zero=True,
        all_cycles_intraday=True,fees_and_cash_reconcile=True,prefix_models=['base_300000','edge10_300000'],
        prefix_through='20260904',virtual_cost_exits_in_prefix=True,full_budget_independent_equivalence=64,
        preserved_result_files=len(read(OUT/'preserved_result_manifest.json'))))
    write(OUT/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.rglob('*') if p.is_file() and p.name!='result_manifest.json'})

def main():
    p=argparse.ArgumentParser();p.add_argument('--stage',choices=['independent','shared','all'],default='all');a=p.parse_args()
    contract=setup()
    if a.stage in ['independent','all']:independent(contract)
    if a.stage in ['shared','all']:shared(contract)

if __name__=='__main__':main()
