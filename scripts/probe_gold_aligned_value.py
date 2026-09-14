"""Six mechanism ablations; all decisions causal in source-availability time."""
from pathlib import Path
import json
from zhaiquant import gold_direction_research as g
from zhaiquant import gold_aligned_value_research as engine
from probe_gold_direction import inputs,CODES,audit,OUT as BASE
from probe_gold_direction_timing import OUT as V2
from probe_gold_long_refinement import OUT as V3
from probe_commodity_capital import ROOT,WORK,read,write,digest,pack

OUT=WORK/'reports/gold_aligned_value_20260913_v4'
CASES=[(p,0) for p in engine.PROFILES]+[(p,d) for p in ('aligned_long','aligned_fast_fair_take') for d in (500,1000)]


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
    sources=[Path(__file__),Path(engine.__file__),Path(g.__file__),ROOT/'src/zhaiquant/gold_direction_timing_research.py',ROOT/'scripts/probe_gold_direction.py']
    parent={**read(BASE/'result_manifest.json'),**read(V2/'result_manifest.json'),**read(V3/'result_manifest.json')}
    plan=dict(family=engine.FAMILY,cases=CASES,settlements=['cost','market'],profiles=engine.PROFILES,
        rationale='current C952 value-long has adverse drift and weak exits; investigate source-alignment bias, conservative volatility and bounded passive targets',
        calibration='queue option quote until received future watermark covers option source time; use only future source<=option source; paired quote gap<=2s; EWMA dt uses option source time; availability is processing future time',
        exits='parent release unchanged; fixed target=entry+entry spread-2ticks while protected; fair target caps at floor(fair/tick); protected cost floor and current bid+tick remain',
        sources={str(p.relative_to(ROOT)):digest(p) for p in sources},parent_manifests={str(p.relative_to(ROOT)):digest(p) for p in (BASE/'result_manifest.json',V2/'result_manifest.json',V3/'result_manifest.json')})
    plan=json.loads(json.dumps(plan))
    if (OUT/'plan.json').exists():assert read(OUT/'plan.json')==plan
    else:write(OUT/'plan.json',plan)
    for p,h in parent.items():assert digest(ROOT/p)==h
    results={};paired={}
    for code in CODES:
        cached={d:inputs(code,d) for d in (0,500,1000)}
        for profile,delay in CASES:
            es,fs,detail=cached[delay]
            for settlement in ('cost','market'):
                key=f'{code}_{profile}_{settlement}_d{delay}';r=replay(es,fs,code,profile,settlement,detail,delay);audit(r)
                for c in r['calibrations']:
                    assert c['future_source_ts']<=c['option_source_ts']<=c['calibration_available_ts']
                    assert c['future_available_ts']<=c['calibration_available_ts']
                if profile=='aligned_long' and settlement=='cost':paired[(code,delay)]={c['option_source_ts']:c for c in r['calibrations']}
                cut=g.START+5*3600000;pre=replay(es,fs,code,profile,settlement,detail,delay,through=cut)
                for field,time in [('orders','created_ts'),('fills','ts'),('cancels','ts'),('cycles','exit_ts'),('calibrations','calibration_available_ts')]:
                    assert pre[field]==[x for x in r[field] if x[time]<=cut]
                pack(OUT/f'{key}.json.gz',r);results[key]=r['summary']
                print(key,{k:r['summary'][k] for k in ('pnl_cny','complete_cycles','virtual_close_count','removed_tail_gross_cny')},flush=True)
    # Delay can postpone calibration, but must not change its paired prices or IV.
    same=[]
    for code in CODES:
        original=paired[(code,0)]
        for delay in (500,1000):
            other=paired[(code,delay)];common=original.keys()&other.keys()
            for ts in common:
                a,b=original[ts],other[ts]
                for field in ('future_source_ts','raw_iv','smoothed_iv','fast_iv','bid_iv'):assert a[field]==b[field],(code,delay,field,ts)
                assert b['calibration_available_ts']-a['calibration_available_ts']==delay
            same.append(dict(code=code,delay_ms=delay,identical_iv_pairs=len(common)))
    write(OUT/'results.json',results)
    for p,h in {**parent,**plan['sources']}.items():assert digest(ROOT/p)==h
    write(OUT/'verification.json',dict(status='passed',accounts=len(results),boundaries=len(results)*3,
        cash_fees_inventory_and_1400_prefix=True,aligned_iv_invariant_to_availability_delay=same,parent_unchanged=True))
    write(OUT/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='result_manifest.json'})


if __name__=='__main__':main()
