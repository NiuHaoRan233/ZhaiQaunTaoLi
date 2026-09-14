"""Combined deletions and strict execution checks for every simple spread level."""
from pathlib import Path
from dataclasses import asdict
from zhaiquant import gold_direction_research as g
from zhaiquant import gold_rule_ladder_simplification as engine
from probe_gold_rule_ladder import timeline,audit,portfolio,economic,OUT as BASE
from probe_gold_direction import inputs,CODES
from probe_commodity_capital import ROOT,WORK,read,write,digest,pack,unpack

OUT=WORK/'reports/gold_rule_simplification_20260913_v2'


def replay(rows,code,case,direction,strike,through=False,capital=250000):
    a=engine.Account(code,case,direction,strike,capital,through)
    for kind,e,low,mid,upper,future in rows:
        if kind==-1:a.boundary(e)
        else:
            a.value.future=future
            a.value.current=(low if direction==1 else upper) if a.rules.fast else mid
            a.option(e)
    return a.result()


def main():
    OUT.mkdir(exist_ok=True)
    if (OUT/'result_manifest.json').exists():raise RuntimeError('Frozen output exists')
    parents=read(BASE/'result_manifest.json')
    for p,h in parents.items():assert digest(ROOT/p)==h
    source=[Path(__file__),Path(engine.__file__)]
    plan=dict(family=engine.FAMILY,reason='test combined deletion, not infer additivity of individual ablations; test all6 simple spreads with strict fills',
        cases={k:dict(label=v['label'],rules=asdict(v['rules'])) for k,v in engine.CASES.items()},
        parent_manifest=digest(BASE/'result_manifest.json'),sources={str(p.relative_to(ROOT)):digest(p) for p in source})
    write(OUT/'plan.json',plan);results={};pairs={};same=[];capital={}
    for code in CODES:
        es,fs,detail=inputs(code);strike=float(detail['OptExercisePrice']);rows,_=timeline(es,fs,strike)
        prefix,_=timeline(es,fs,strike,g.START+5*3600000)
        for case in engine.CASES:
            for d in (1,-1):
                for through in (False,True):
                    key=f'{code}_{case}_{"long" if d==1 else "short"}_through{int(through)}'
                    r=replay(rows,code,case,d,strike,through);audit(r)
                    pre=replay(prefix,code,case,d,strike,through)
                    for field,t in [('orders','created_ts'),('fills','ts'),('cycles','exit_ts'),('cancels','ts')]:
                        assert pre[field]==[x for x in r[field] if x[t]<=g.START+5*3600000]
                    if case.startswith('spread') and not through:
                        old=unpack(BASE/f'{key}.json.gz');assert economic(r)==economic(old);same.append(key)
                    if case=='core' and d==1:
                        low=replay(rows,code,case,d,strike,through,capital=20000);audit(low)
                        assert economic(low)==economic(r);capital[key]=low['summary']
                        pack(OUT/f'{key}_capital20000.json.gz',low)
                    pack(OUT/f'{key}.json.gz',r);results[key]=r['summary'];pairs.setdefault((case,d,through),[]).append(r)
                    print(key,r['summary']['pnl_cny'],r['summary']['max_drawdown_cny'],flush=True)
    write(OUT/'results.json',results);write(OUT/'portfolios.json',{f'{k}_{d}_{int(t)}':portfolio(v) for (k,d,t),v in pairs.items()})
    write(OUT/'capital_validation.json',capital)
    for p,h in {**parents,**plan['sources']}.items():assert digest(ROOT/p)==h
    write(OUT/'verification.json',dict(status='passed',accounts=len(results),capital_replays=len(capital),
        boundary_checks=3*(len(results)+len(capital)),all_cash_fees_and_prefixes=True,
        simple_spread_equal_to_v1=same,core_20000_cash_economic_equality=True,parents_unchanged=True))
    write(OUT/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='result_manifest.json'})


if __name__=='__main__':main()
