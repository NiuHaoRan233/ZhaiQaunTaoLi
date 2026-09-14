"""Complete timing repair and deterministic source-delay robustness checks."""
from pathlib import Path
import json
from zhaiquant import gold_direction_research as g
from zhaiquant import gold_direction_timing_research as engine
from probe_gold_direction import inputs,CODES,OUT as PARENT,audit
from probe_commodity_capital import ROOT,WORK,read,write,digest,pack

OUT=WORK/'reports/gold_direction_timing_20260913_v2'
CASES=[('fair_risk_switch',0),('fair_top_switch',0),('fair_long',0),('fair_long',500),('fair_long',1000),
       ('fair_switch',0),('fair_switch',500),('fair_switch',1000)]


def replay(es,fs,code,profile,settlement,detail,delay,through=None):
    a=engine.Account(code,profile,settlement,float(detail['OptExercisePrice']))
    a.model+=f'_future_delay{delay}'
    events=[(e.ts,0,e) for e in es]+[(f.ts,1,f) for f in fs]+[(b,-1,b) for b in g.BOUNDARIES]
    for ts,kind,event in sorted(events,key=lambda x:x[:2]):
        if through is not None and ts>through:break
        if kind==0:a.option(event)
        elif kind==1:a.on_future(event)
        else:a.boundary(event)
    return a.result()


def main():
    OUT.mkdir(exist_ok=True)
    sources=[Path(__file__),Path(engine.__file__),Path(g.__file__),ROOT/'scripts/probe_gold_direction.py']
    plan=dict(family=engine.FAMILY,parent_manifest_sha256=digest(PARENT/'result_manifest.json'),cases=CASES,
        settlements=['cost','market'],change='future cancellation may use already processed same-timestamp option calibration; option decisions remain strict past',
        delays='source availability shifted0/500/1000ms, identical expiry/age tests and ties option first; robustness NOT parameter selection',
        sources={str(p.relative_to(ROOT)):digest(p) for p in sources})
    plan=json.loads(json.dumps(plan))
    if (OUT/'plan.json').exists():assert read(OUT/'plan.json')==plan
    else:write(OUT/'plan.json',plan)
    parent=read(PARENT/'result_manifest.json')
    for p,h in parent.items():assert digest(ROOT/p)==h
    results={}
    for code in CODES:
        cached={delay:inputs(code,delay) for delay in (0,500,1000)}
        for profile,delay in CASES:
            es,fs,detail=cached[delay]
            for settlement in ('cost','market'):
                key=f'{code}_{profile}_{settlement}_d{delay}'
                r=replay(es,fs,code,profile,settlement,detail,delay);audit(r)
                prefix=replay(es,fs,code,profile,settlement,detail,delay,through=g.START+5*3600000)
                for field,time in [('orders','created_ts'),('fills','ts'),('cancels','ts'),('cycles','exit_ts')]:
                    assert prefix[field]==[x for x in r[field] if x[time]<=g.START+5*3600000]
                for c in r['cancels']:
                    f=c.get('feature')
                    if f:assert max(f['vol_source_ts'],f['future_available_ts'])<=c['ts']
                pack(OUT/f'{key}.json.gz',r);results[key]=r['summary']
                print(key,{k:r['summary'][k] for k in ('pnl_cny','complete_cycles','future_cancel_requests','ambiguous_cancel_fills','removed_tail_gross_cny')},flush=True)
    write(OUT/'results.json',results)
    for p,h in {**parent,**plan['sources']}.items():assert digest(ROOT/p)==h
    write(OUT/'verification.json',dict(status='passed',accounts=len(results),boundaries=3*len(results),
        causal_sources_and_1400_prefix=True,independent_cash_fees=True,parent_hashes_unchanged=True))
    write(OUT/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='result_manifest.json'})


if __name__=='__main__':main()
