"""Neighboring memory lengths, source lags and conservative fill evidence."""
from pathlib import Path
from zhaiquant import gold_direction_research as g
from zhaiquant import gold_fast_validation_research as engine
from probe_gold_direction import inputs,CODES,audit
from probe_gold_aligned_value import OUT as PARENT
from probe_commodity_capital import ROOT,WORK,read,write,digest,pack,unpack

OUT=WORK/'reports/gold_fast_validation_20260913_v5'


def replay(es,fs,code,settlement,detail,fast,through,delay,cutoff=None):
    a=engine.Account(code,settlement,float(detail['OptExercisePrice']),fast,through);a.model+=f'_future_delay{delay}'
    events=[(e.ts,0,e) for e in es]+[(f.ts,1,f) for f in fs]+[(b,-1,b) for b in g.BOUNDARIES]
    for ts,kind,event in sorted(events,key=lambda x:x[:2]):
        if cutoff is not None and ts>cutoff:break
        if kind==0:a.option(event)
        elif kind==1:a.on_future(event)
        else:a.boundary(event)
    return a.result()


def main():
    OUT.mkdir(exist_ok=True)
    sources=[Path(__file__),Path(engine.__file__),ROOT/'src/zhaiquant/gold_aligned_value_research.py',
        ROOT/'src/zhaiquant/gold_direction_timing_research.py',Path(g.__file__),ROOT/'scripts/probe_gold_direction.py']
    cases=[dict(fast=fast,delay=delay,through=False) for fast in (5,10,20) for delay in (0,500,1000)]+[dict(fast=10,delay=0,through=True)]
    plan=dict(family=engine.FAMILY,parent_manifest_sha256=digest(PARENT/'result_manifest.json'),cases=cases,
        fixed_lead=dict(fast=10,slow=60,reference='minimum aligned fast and slow IV',exit='unchanged bounded patient'),
        interpretation='5/20s and source delays are robustness cases, not optimization ranks; strict through requires last print beyond limit instead of touch, actual exchange queue is unknown',
        sources={str(p.relative_to(ROOT)):digest(p) for p in sources})
    if (OUT/'plan.json').exists():assert read(OUT/'plan.json')==plan
    else:write(OUT/'plan.json',plan)
    parent=read(PARENT/'result_manifest.json')
    for p,h in parent.items():assert digest(ROOT/p)==h
    result={}
    for code in CODES:
        cached={d:inputs(code,d) for d in (0,500,1000)}
        for c in cases:
            fast,delay,through=c['fast'],c['delay'],c['through'];es,fs,detail=cached[delay]
            for settlement in ('cost','market'):
                key=f'{code}_fast{fast}_through{int(through)}_{settlement}_d{delay}'
                r=replay(es,fs,code,settlement,detail,fast,through,delay);audit(r)
                if through:assert all(f['source_last_cents']!=f['price_cents'] for f in r['fills'] if f['kind']=='passive')
                if fast==10 and not through and delay==0:
                    old=unpack(PARENT/f'{code}_aligned_fast_lower_{settlement}_d0.json.gz')
                    economic=lambda rows:[(f['ts'],f['side'],f['price_cents'],f['fee_cents']) for f in rows['fills']]
                    assert economic(old)==economic(r)
                    assert r['calibrations']==old['calibrations']
                cut=g.START+5*3600000;pre=replay(es,fs,code,settlement,detail,fast,through,delay,cut)
                for field,time in [('orders','created_ts'),('fills','ts'),('cancels','ts'),('cycles','exit_ts'),('calibrations','calibration_available_ts')]:
                    assert pre[field]==[x for x in r[field] if x[time]<=cut]
                pack(OUT/f'{key}.json.gz',r);result[key]=r['summary']
                print(key,{k:r['summary'][k] for k in ('pnl_cny','complete_cycles','virtual_close_count','removed_tail_gross_cny','rejected_touch_only_attempts')},flush=True)
    write(OUT/'results.json',result)
    for p,h in {**parent,**plan['sources']}.items():assert digest(ROOT/p)==h
    write(OUT/'verification.json',dict(status='passed',accounts=len(result),boundaries=len(result)*3,
        parent_fast10_economic_and_iv_equivalence=True,all_cash_fees_inventory_and_prefixes=True,
        through_requires_strict_price=True,parent_sources_preserved=True))
    write(OUT/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='result_manifest.json'})


if __name__=='__main__':main()
