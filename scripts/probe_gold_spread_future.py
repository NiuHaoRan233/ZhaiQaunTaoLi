"""Fixed8-tick fusion study, neighboring spreads and availability-lag controls."""
from pathlib import Path
from dataclasses import asdict
from math import exp
import numpy as np
from zhaiquant import gold_direction_research as g
from zhaiquant.gold_aligned_value_research import ValueState
from zhaiquant import gold_spread_future_research as engine
from probe_gold_direction import inputs,CODES
from probe_gold_rule_ladder import audit,portfolio
from probe_commodity_capital import ROOT,WORK,read,write,pack,unpack,digest

OUT=WORK/'reports/gold_spread_future_20260913_v3'
CASES=[(p,8,0) for p in engine.PROFILES]+[(p,n,0) for p in ('base_long','value_long','value_switch') for n in (6,10)]+[(p,8,d) for p in ('value_long','value_switch','throttle_long') for d in (500,1000)]


def features(value,ts,future_event=False):
    low=value.cancellation_feature(ts) if future_event else value.feature(ts)
    if low is None:return {1:None,-1:None}
    high=dict(low);vol=max(value.vol,value.fast_vol)
    fair,delta=g.black_call(value.future.mid,value.strike,value.maturity(ts),vol)
    high.update(fair_cents=fair*100000,delta=delta,vol=vol,reference_kind='fast_upper')
    for feat in (low,high):
        for seconds in (10,60):
            old=next((x for x in reversed(value.history) if x.source_ts<=value.future.source_ts-seconds*1000),None)
            feat[f'move{seconds}_cents']=feat['delta']*(value.future.mid-old.mid)*100000 if old and value.future.source_ts-seconds*1000-old.source_ts<=2000 else None
    return {1:low,-1:high}


def timeline(es,fs,strike,cut=None):
    value=ValueState(strike,'fast_lower');rows=[]
    events=sorted([(e.ts,0,e) for e in es]+[(f.ts,1,f) for f in fs]+[(b,-1,b) for b in g.BOUNDARIES],key=lambda x:x[:2])
    for ts,kind,event in events:
        if cut is not None and ts>cut:break
        if kind==-1:rows.append((kind,event,None,None));continue
        if kind==1:
            value.on_future(event);rows.append((kind,event,features(value,ts,True),value.future))
        else:
            value.new_session(event.session);rows.append((kind,event,features(value,ts),value.future));value.observe_option(event)
    return rows


def replay(rows,code,profile,strike,spread=8,through=False,capital=250000,delay=0):
    a=engine.Account(code,profile,strike,spread,through,capital,delay)
    for kind,event,features_,future in rows:
        if kind==-1:a.boundary(event)
        elif kind==1:a.future_event(event,features_)
        else:
            a.value.future=future;a.features=features_;a.option(event)
    return a.result()


def economics(r):
    def clean(x):
        if isinstance(x,dict):return {k:clean(v) for k,v in x.items() if k not in ('model_id','feature','entry_signal','entry_fill_future','cash_cents')}
        if isinstance(x,list):return [clean(v) for v in x]
        return x
    return {k:clean(r[k]) for k in ('orders','fills','cycles','cancels','curve','boundaries')}


def check(r):
    audit(r);s=r['summary'];curve=np.asarray(r['curve'],dtype=np.int64)[:,1]
    assert int((np.maximum.accumulate(np.maximum(curve,0))-curve).max())==round(s['max_drawdown_cny']*100)
    for o in r['orders']:
        if o['reason']=='entry':assert o['entry_spread']>=s['spread_ticks']*2000
    for f in r['fills']:
        if f.get('cancel_ts') is not None:assert f['cancel_ts']>f['source_previous_ts']
    for c in r['cancels']:
        feat=c.get('feature')
        if feat:assert feat['vol_source_ts']<=c['ts'] and feat['future_available_ts']<=c['ts']
    assert not s['margin_breach_frames']


def main():
    OUT.mkdir(exist_ok=True)
    if (OUT/'result_manifest.json').exists():raise RuntimeError('Frozen family; do not overwrite')
    parents={}
    for folder in ('gold_rule_ladder_20260913_v1','gold_rule_simplification_20260913_v2'):
        parents.update(read(WORK/f'reports/{folder}/result_manifest.json'))
    for p,h in parents.items():assert digest(ROOT/p)==h
    sources=[Path(__file__),Path(engine.__file__),ROOT/'src/zhaiquant/gold_rule_ladder_research.py',ROOT/'src/zhaiquant/gold_aligned_value_research.py',ROOT/'scripts/probe_gold_direction.py']
    plan=dict(family=engine.FAMILY,cases=CASES,profiles={k:asdict(v) for k,v in engine.PROFILES.items()},
        fixed_spread_ticks=8,neighbors=[6,10],fee_cny=1.7,order_delay_ms=0,date=g.DATE,
        common='same two contracts/day/09:30 start/one contract/session flat5seconds before breaks/actual quote PnL',
        trend='delta-scaled actual same-month future source-time10/60s move; adverse beyond max(2ticks,half spread) rejects side',
        throttle='entry only; keep still safe/passive old quote until both movement>=2ticks and age>=1s; unsafe cancellation and exits bypass',
        future_manage='adverse futures immediately release patient protection, reprice only on next option quote; never backfill or erase eligible old fills',
        short_reserve='inherited20% futures notional research proxy, not actual broker margin',
        sources={str(p.relative_to(ROOT)):digest(p) for p in sources})
    write(OUT/'plan.json',plan);summaries={};pairs={};baselines=[];cash=[]
    for code in CODES:
        cache={}
        for delay in (0,500,1000):
            es,fs,detail=inputs(code,delay);strike=float(detail['OptExercisePrice'])
            rows=timeline(es,fs,strike);pre=timeline(es,fs,strike,g.START+5*3600000)
            assert pre==[x for x in rows if (x[1] if x[0]==-1 else x[1].ts)<=g.START+5*3600000]
            cache[delay]=(rows,pre)
        for profile,spread,delay in CASES:
            rows,pre=cache[delay]
            for through in (False,True):
                key=f'{code}_{profile}_spread{spread}_through{int(through)}_delay{delay}'
                r=replay(rows,code,profile,strike,spread,through,delay=delay);check(r)
                part=replay(pre,code,profile,strike,spread,through,delay=delay)
                for field,t in [('orders','created_ts'),('fills','ts'),('cycles','exit_ts'),('cancels','ts'),('manage_events','ts')]:
                    assert part[field]==[x for x in r[field] if x[t]<=g.START+5*3600000],(key,field)
                if profile in ('base_long','base_short') and delay==0:
                    d=profile.split('_')[1]
                    old=unpack(WORK/f'reports/gold_rule_simplification_20260913_v2/{code}_spread{spread}_{d}_through{int(through)}.json.gz')
                    assert economics(old)==economics(r);baselines.append(key)
                if profile in ('value_long','throttle_long') and spread==8 and delay==0:
                    small=replay(rows,code,profile,strike,spread,through,capital=20000);check(small)
                    assert economics(small)==economics(r);pack(OUT/f'{key}_capital20000.json.gz',small);cash.append(key)
                summaries[key]=r['summary'];pack(OUT/f'{key}.json.gz',r);pairs.setdefault((profile,spread,delay,through),[]).append(r)
                print(key,r['summary']['pnl_cny'],r['summary']['max_drawdown_cny'],r['summary']['order_count'],flush=True)
    write(OUT/'results.json',summaries);write(OUT/'portfolios.json',{f'{p}_spread{s}_through{int(t)}_delay{d}':portfolio(v) for (p,s,d,t),v in pairs.items()})
    for p,h in {**parents,**plan['sources']}.items():assert digest(ROOT/p)==h
    write(OUT/'verification.json',dict(status='passed',accounts=len(summaries),capital_replays=len(cash),
        boundary_count=3*(len(summaries)+len(cash)),all_cash_fees_inventory_dd_prefix=True,
        base_equal_to_frozen=baselines,small_cash_equal=cash,causal_shared_timeline=True,parents_unchanged=True))
    write(OUT/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='result_manifest.json'})


if __name__=='__main__':main()
