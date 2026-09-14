"""Freeze latest-day long/short/futures ablations; all inputs read-only."""
from pathlib import Path
from dataclasses import replace
import json
import numpy as np
import pandas as pd
from zhaiquant import gold_direction_research as g
from zhaiquant.commodity_dadao_research import load_frame
from probe_commodity_capital import ROOT,WORK,read,write,digest,pack,unpack
from probe_gold_state import OUT as OLD

OUT=WORK/'reports/gold_direction_20260913_v1'
CODES=('au2610C960.SF','au2610C952.SF')


def inputs(code,delay=0):
    details=read(OLD/'catalog_terms.json')['details']
    p=OLD/'inputs'/f'{code}.pkl';f=pd.read_pickle(p);f=f[(f.time>=g.START)&(f.time<g.END)]
    es,flags,meta=load_frame(f,code=code,date=g.DATE,detail=details[code])
    es=[replace(e,quantity=min(e.quantity,1)) for e in es]
    f=pd.read_pickle(OUT/'inputs/au2610.SF.pkl');f=f.sort_values('time',kind='stable').drop_duplicates('time',keep='last')
    fs=[]
    for x in f.itertuples():
        session=next((i for i,(lo,hi) in enumerate(zip(g.SESSION_STARTS,g.BOUNDARIES)) if lo<=x.time<hi),None)
        if session is None or not(0<x.bidPrice[0]<=x.askPrice[0] and min(x.bidVol[0],x.askVol[0])>0):continue
        if x.time+delay>=g.BOUNDARIES[session]:continue
        fs.append(g.Future(int(x.time)+delay,int(x.time),session,(x.bidPrice[0]+x.askPrice[0])/2))
    return es,fs,details[code]


def replay(es,fs,code,profile,settlement,detail,through=None):
    a=g.Account(code,profile,settlement,float(detail['OptExercisePrice']))
    events=[(e.ts,0,e) for e in es]+[(f.ts,1,f) for f in fs]+[(b,-1,b) for b in g.BOUNDARIES]
    events.sort(key=lambda x:x[:2])
    for ts,kind,event in events:
        if through is not None and ts>through:break
        if kind==0:a.option(event)
        elif kind==1:a.on_future(event)
        else:a.boundary(event)
    return a.result()


def audit(r):
    s=r['summary'];cash=g.CAPITAL;inv=fees=gross=0;basis=None
    orders={o['id']:o for o in r['orders']}
    for f in r['fills']:
        d=1 if f['side']=='buy' else -1;p=f['price_cents'];o=orders[f['order_id']]
        assert f['model_id']==o['model_id']==s['model_id']
        if inv:assert d==-inv;gross+=inv*(p-basis);basis=None
        else:basis=p
        inv+=d;cash-=d*p+g.FEE;fees+=g.FEE
        assert (inv,cash)==(f['inventory'],f['cash_cents']) and inv in (-1,0,1) and cash>=0
        if f['kind']=='passive':
            assert f['created_ts']<f['ts'] and f['active_ts']<=f['source_previous_ts']
            assert f['source_single'] and f['source_quantity']==1
            assert f['source_strict_side']==('sell' if d==1 else 'buy')
            assert (f['source_last_cents']<=p if d==1 else f['source_last_cents']>=p)
            if f.get('cancel_ts') is not None:assert f['cancel_ts']>f['source_previous_ts']
        elif f['kind']=='pre_break_market_close':
            assert p==(f['source_bid'] if d==-1 else f['source_ask']) and f['source_qty']>=1
            assert any(b-5000<=f['ts']<b for b in g.BOUNDARIES)
        else:assert f['kind']=='virtual_cost_close' and f['ts'] in g.BOUNDARIES
        feat=o.get('feature')
        if feat:
            assert feat['vol_source_ts']<o['created_ts'] and feat['future_source_ts']<o['created_ts']
            assert feat['future_available_ts']<=o['created_ts']
    assert inv==s['end_inventory']==0 and cash==round(s['end_cash_cny']*100)
    assert fees==round(s['fees_cny']*100) and gross==round(s['realized_gross_cny']*100)
    assert cash-g.CAPITAL==sum(c['net_cents'] for c in r['cycles'])
    assert len(r['boundaries'])==3 and all(b['inventory']==0 for b in r['boundaries'])
    for c in r['cycles']:
        assert not any(c['entry_ts']<b<c['exit_ts'] for b in g.BOUNDARIES)
        if c['exit_kind']=='virtual_cost_close':assert c['gross_cents']==0 and c['net_cents']==-340
    return True


def data_audit(es,fs):
    ft=np.array([x.source_ts for x in fs]);ot=np.array([x.ts for x in es]);idx=np.searchsorted(ft,ot,side='left')-1
    ages=ot[idx>=0]-ft[idx[idx>=0]]
    return dict(option_frames=len(es),future_frames=len(fs),strict_asof_future_coverage_le2s=float(np.mean(ages<=2000)),
        future_age_median_ms=float(np.median(ages)),future_age_p99_ms=float(np.quantile(ages,.99)),
        first_future_ts=int(ft[0]),last_future_ts=int(ft[-1]))


def main():
    OUT.mkdir(exist_ok=True)
    sources=[Path(__file__),Path(g.__file__),ROOT/'src/zhaiquant/commodity_dadao_research.py',
        ROOT/'src/zhaiquant/gold_state_research.py',ROOT/'src/zhaiquant/commodity_flow_strategy.py']
    data=[OUT/'inputs/au2610.SF.pkl',OLD/'catalog_terms.json']+[OLD/'inputs'/f'{c}.pkl' for c in CODES]
    plan=dict(family=g.FAMILY,profiles=g.PROFILES,settlements=['cost','market'],capital_per_code_cny=g.CAPITAL/100,
        codes=list(CODES),date=g.DATE,fee_cny=1.7,delay_ms=0,option_before_future_at_ties=True,
        boundaries=list(g.BOUNDARIES),market_stop_and_flat_before_ms=5000,
        value_model='Black76 r0 European approximation to American gold call; past option IV EWMA tau60s; updated AFTER current option decision; max future age2s; reset/warm1minute per session',
        trend='delta-scaled10s/60s futures move against entry side >max(2ticks,half option spread) => reject',
        fair_edge='direction*(value-limit)-two_fees >=max(tick,quarter spread); choose larger eligible edge; tie long',
        cancellation='future news inside aggregate trade interval may not erase old eligible fill',
        short_risk_reserve='20% futures notional plus option repurchase premium; research assumption, not broker margin',
        sources={str(p.relative_to(ROOT)):digest(p) for p in sources},inputs={str(p.relative_to(ROOT)):digest(p) for p in data})
    if (OUT/'plan.json').exists():assert read(OUT/'plan.json')==plan
    else:write(OUT/'plan.json',plan)
    parent=read(OLD/'result_manifest.json')
    for p,h in parent.items():assert digest(ROOT/p)==h
    results={};da={}
    for code in CODES:
        es,fs,detail=inputs(code);da[code]=data_audit(es,fs)
        print('DATA',code,da[code],flush=True)
        for profile in g.PROFILES:
            for settlement in plan['settlements']:
                key=f'{code}_{profile}_{settlement}'
                r=replay(es,fs,code,profile,settlement,detail);audit(r)
                cut=g.START+5*3600000
                pre=replay(es,fs,code,profile,settlement,detail,through=cut)
                for field,time in [('orders','created_ts'),('fills','ts'),('cancels','ts'),('cycles','exit_ts')]:
                    assert pre[field]==[x for x in r[field] if x[time]<=cut],(key,field)
                pack(OUT/f'{key}.json.gz',r);results[key]=r['summary']
                s=r['summary'];print('RESULT',key,{k:s[k] for k in ('pnl_cny','market_cycle_net_cny','virtual_close_count','removed_tail_gross_cny','complete_cycles','future_cancel_requests','ambiguous_cancel_fills','margin_breach_frames')},flush=True)
    write(OUT/'results.json',results);write(OUT/'data_audit.json',da)
    for p,h in {**parent,**plan['sources'],**plan['inputs']}.items():assert digest(ROOT/p)==h
    write(OUT/'verification.json',dict(status='passed',accounts=len(results),boundaries=len(results)*3,
        all_cash_fees_positions_reconstructed=True,all_prefixes_through_1400=True,
        source_times_strictly_causal=True,parent_files_unchanged=len(parent)))
    write(OUT/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.rglob('*') if p.is_file() and p.name!='result_manifest.json'})


if __name__=='__main__':main()
