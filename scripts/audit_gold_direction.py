"""Independent economic attribution and descriptive lead/lag diagnostics."""
from collections import Counter
from bisect import bisect_left
import numpy as np
from zhaiquant import gold_direction_research as g
from probe_gold_direction import OUT as BASE,OLD,inputs,CODES
from probe_commodity_capital import ROOT,WORK,read,write,unpack,digest

OUT=WORK/'reports/gold_direction_attribution_20260913'


def leadlag(es,fs):
    ot=np.array([e.ts for e in es]);om=np.array([(e.bid+e.ask)/200000 if g.valid(e) else np.nan for e in es])
    ft=np.array([f.ts for f in fs]);fm=np.array([f.mid for f in fs]);pairs={lag:[] for lag in (-4,-2,0,2,4,10,20)}
    for start,end in zip(g.SESSION_STARTS,g.BOUNDARIES):
        ts=np.arange(start+5000,end,500);oi=np.searchsorted(ot,ts,side='right')-1;fi=np.searchsorted(ft,ts,side='right')-1
        valid=(oi>=0)&(fi>=0)&(ts-ot[oi]<=2000)&(ts-ft[fi]<=2000)
        o=om[oi].copy();f=fm[fi].copy();o[~valid]=np.nan;f[~valid]=np.nan
        # One-second returns. Positive lag means option responds later.
        dr_o=o[2:]-o[:-2];dr_f=f[2:]-f[:-2]
        for lag in pairs:
            x=dr_f[:len(dr_f)-lag] if lag>0 else dr_f[-lag:]
            y=dr_o[lag:] if lag>0 else dr_o[:len(dr_o)+lag]
            ok=np.isfinite(x)&np.isfinite(y);pairs[lag].extend(zip(x[ok],y[ok]))
    return [dict(option_lag_seconds=lag/2,observations=len(ps),correlation=float(np.corrcoef(np.array(ps).T)[0,1])) for lag,ps in pairs.items()]


def main():
    OUT.mkdir(exist_ok=True);result={};lags={};checks={}
    for code in CODES:
        es,fs,detail=inputs(code);em={e.ts:e for e in es};ft=[f.ts for f in fs];k=float(detail['OptExercisePrice'])
        value=g.ValueState(k);lags[code]=leadlag(es,fs)
        # Independently compare a newly implemented plain long against saved parent.
        old=unpack(OLD/f'{code}_gap.json.gz');new=unpack(BASE/f'{code}_long_cost.json.gz')
        econ=lambda r:[(f['ts'],f['side'],f['price_cents'],f['fee_cents']) for f in r['fills'] if f['ts']<g.BOUNDARIES[0]]
        assert econ(old)==econ(new);checks[code]=dict(before_first_break_parent_economic_equivalence=True,fill_sides=len(econ(new)))
        for profile in g.PROFILES:
            for settlement in ('cost','market'):
                key=f'{code}_{profile}_{settlement}';r=unpack(BASE/f'{key}.json.gz');cs=[]
                for c in r['cycles']:
                    if c['exit_kind']=='virtual_cost_close':continue
                    en,ex=em[c['entry_ts']],em[c['exit_ts']];d=c['direction']
                    mi=(en.bid+en.ask)/2;mo=(ex.bid+ex.ask)/2
                    entry=d*(mi-c['entry_price_cents']);drift=d*(mo-mi);exit=d*(c['exit_price_cents']-mo)
                    assert round(entry+drift+exit)==c['gross_cents']
                    feat=c['entry_fill_future'];j=bisect_left(ft,c['exit_ts'])-1;future_component=None
                    if feat and j>=0 and c['exit_ts']-ft[j]<=2000:
                        vi=g.black_call(feat['future_mid'],k,value.maturity(c['entry_ts']),feat['vol'])[0]
                        vo=g.black_call(fs[j].mid,k,value.maturity(c['exit_ts']),feat['vol'])[0]
                        future_component=d*(vo-vi)*100000
                    cs.append(dict(**c,entry_edge_cents=entry,book_drift_cents=drift,exit_edge_cents=exit,
                        fixed_entry_iv_futures_drift_cents=future_component,
                        residual_book_drift_cents=drift-future_component if future_component is not None else None))
                total={field:sum(c[field] for c in cs if c[field] is not None)/100 for field in
                    ('entry_edge_cents','book_drift_cents','exit_edge_cents','fixed_entry_iv_futures_drift_cents','residual_book_drift_cents')}
                nets=sorted((c['net_cents'] for c in cs),reverse=True)
                result[key]=dict(components_cny=total,normal_cycles=len(cs),net_cny=sum(nets)/100,
                    missing_future_attribution=sum(c['fixed_entry_iv_futures_drift_cents'] is None for c in cs),
                    net_without_best_three_cny=(sum(nets)-sum(max(0,n) for n in nets[:3]))/100,
                    largest_win_cny=max(nets,default=0)/100,largest_loss_cny=min(nets,default=0)/100,
                    winning_cycles=sum(n>0 for n in nets),losing_cycles=sum(n<0 for n in nets),
                    sessions=[dict(session=i,net_cny=sum(c['net_cents'] for c in cs if lo<=c['entry_ts']<hi)/100,
                        cycles=sum(lo<=c['entry_ts']<hi for c in cs)) for i,(lo,hi) in enumerate(zip(g.SESSION_STARTS,g.BOUNDARIES))],cycles=cs)
    write(OUT/'attribution.json',result);write(OUT/'leadlag.json',lags);write(OUT/'parent_equivalence.json',checks)
    manifest=read(BASE/'result_manifest.json')
    for p,h in manifest.items():assert digest(ROOT/p)==h
    write(OUT/'source_manifest.json',manifest)
    write(OUT/'artifact_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='artifact_manifest.json'})
    for key,r in result.items():
        if key.endswith('_market') and any('_'+p+'_market' in key for p in ('long','short','fair_long','fair_short','fair_switch')):
            print(key,{k:v for k,v in r.items() if k not in ('cycles','sessions')},flush=True)
    print('LEADLAG',lags)


if __name__=='__main__':main()
