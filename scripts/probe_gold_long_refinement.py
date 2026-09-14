"""Predeclared long-only combinations; retain every delay and settlement result."""
from pathlib import Path
from zhaiquant import gold_direction_research as g
from zhaiquant import gold_long_refinement_research as engine
from probe_gold_direction import inputs,CODES,audit,OUT as ORIGINAL
from probe_gold_direction_timing import OUT as PREVIOUS
from probe_commodity_capital import ROOT,WORK,read,write,digest,pack

OUT=WORK/'reports/gold_long_refinement_20260913_v3'


def replay(es,fs,code,profile,settlement,detail,delay,through=None):
    a=engine.Account(code,profile,settlement,float(detail['OptExercisePrice']));a.model+=f'_future_delay{delay}'
    events=[(e.ts,0,e) for e in es]+[(f.ts,1,f) for f in fs]+[(b,-1,b) for b in g.BOUNDARIES]
    for ts,kind,event in sorted(events,key=lambda x:x[:2]):
        if through is not None and ts>through:break
        if kind==0:a.option(event)
        elif kind==1:a.on_future(event)
        else:a.boundary(event)
    return a.result()


def main():
    OUT.mkdir(exist_ok=True)
    sources=[Path(__file__),Path(engine.__file__),Path(g.__file__),ROOT/'src/zhaiquant/gold_direction_timing_research.py',
        ROOT/'scripts/probe_gold_direction.py']
    plan=dict(family=engine.FAMILY,profiles=engine.PROFILES,future_delays_ms=[0,500,1000],
        settlements=['cost','market'],parent_manifest_sha256=digest(PREVIOUS/'result_manifest.json'),
        rationale='plain short loses both; value long gains both but C952 depends on top trades; test adding trend/cancel and top exit to same long value, no contract replacement, all perturbations retained',
        sources={str(p.relative_to(ROOT)):digest(p) for p in sources})
    if (OUT/'plan.json').exists():assert read(OUT/'plan.json')==plan
    else:write(OUT/'plan.json',plan)
    parent={**read(ORIGINAL/'result_manifest.json'),**read(PREVIOUS/'result_manifest.json')}
    for p,h in parent.items():assert digest(ROOT/p)==h
    results={}
    for code in CODES:
        cached={delay:inputs(code,delay) for delay in plan['future_delays_ms']}
        for profile in plan['profiles']:
            for delay,(es,fs,detail) in cached.items():
                for settlement in plan['settlements']:
                    key=f'{code}_{profile}_{settlement}_d{delay}'
                    r=replay(es,fs,code,profile,settlement,detail,delay);audit(r)
                    pre=replay(es,fs,code,profile,settlement,detail,delay,through=g.START+5*3600000)
                    for field,time in [('orders','created_ts'),('fills','ts'),('cancels','ts'),('cycles','exit_ts')]:
                        assert pre[field]==[x for x in r[field] if x[time]<=g.START+5*3600000]
                    pack(OUT/f'{key}.json.gz',r);results[key]=r['summary']
                    print(key,{k:r['summary'][k] for k in ('pnl_cny','complete_cycles','virtual_close_count','removed_tail_gross_cny')},flush=True)
    write(OUT/'results.json',results)
    for p,h in {**parent,**plan['sources']}.items():assert digest(ROOT/p)==h
    write(OUT/'verification.json',dict(status='passed',accounts=len(results),boundaries=3*len(results),
        causal_1400_prefix=True,independent_cash_fees=True,parent_hashes_unchanged=True))
    write(OUT/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='result_manifest.json'})


if __name__=='__main__':main()
