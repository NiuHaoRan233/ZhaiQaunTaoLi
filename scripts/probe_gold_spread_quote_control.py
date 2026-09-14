"""Final quote-chasing control applied to simple and enhanced fixed-spread models."""
from pathlib import Path
from zhaiquant import gold_direction_research as g
from zhaiquant import gold_spread_quote_control as engine
from probe_gold_spread_future import timeline,check,economics,OUT as BASE
from probe_gold_direction import inputs,CODES
from probe_gold_rule_ladder import portfolio
from probe_commodity_capital import ROOT,WORK,read,write,pack,unpack,digest

OUT=WORK/'reports/gold_spread_quote_control_20260913_v4'


def replay(rows,code,profile,strike,spread=8,through=False,capital=250000,delay=0):
    a=engine.Account(code,profile,strike,spread,through,capital,delay)
    for kind,event,features_,future in rows:
        if kind==-1:a.boundary(event)
        elif kind==1:a.future_event(event,features_)
        else:a.value.future=future;a.features=features_;a.option(event)
    return a.result()


def main():
    OUT.mkdir(exist_ok=True)
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen output')
    m=read(BASE/'result_manifest.json')
    for p,h in m.items():assert digest(ROOT/p)==h
    write(OUT/'plan.json',dict(family=engine.FAMILY,profiles=list(engine.PROFILES),spreads=[6,8,10],delays=[0,500,1000],through=[False,True],
        fixed_main_spread=8,order_delay_ms=0,rule='only favorable-direction entry price chasing throttled to>=2ticks AND age>=1s; bad/stale signals, retreat reprices, exits and break close bypass',
        source_hashes={str(p.relative_to(ROOT)):digest(p) for p in [Path(__file__),Path(engine.__file__)]},parent_manifest=digest(BASE/'result_manifest.json')))
    ss={};pairs={};small=[]
    for code in CODES:
        cache={}
        for delay in (0,500,1000):
            es,fs,detail=inputs(code,delay);strike=float(detail['OptExercisePrice']);cache[delay]=timeline(es,fs,strike)
        for profile in engine.PROFILES:
            for spread in (6,8,10):
                for delay in (0,500,1000):
                    rows=cache[delay]
                    for through in (False,True):
                        key=f'{code}_{profile}_spread{spread}_through{int(through)}_delay{delay}'
                        r=replay(rows,code,profile,strike,spread,through,delay=delay);check(r)
                        pre=replay([x for x in rows if (x[1] if x[0]==-1 else x[1].ts)<=g.START+5*3600000],code,profile,strike,spread,through,delay=delay)
                        for field,t in [('orders','created_ts'),('fills','ts'),('cycles','exit_ts'),('cancels','ts'),('manage_events','ts')]:
                            assert pre[field]==[x for x in r[field] if x[t]<=g.START+5*3600000]
                        if profile=='trend_long' and spread==8 and delay==0:
                            low=replay(rows,code,profile,strike,spread,through,capital=20000);check(low)
                            assert economics(low)==economics(r);pack(OUT/f'{key}_capital20000.json.gz',low);small.append(low['summary'])
                        pack(OUT/f'{key}.json.gz',r);ss[key]=r['summary'];pairs.setdefault((profile,spread,delay,through),[]).append(r)
                        print(key,r['summary']['pnl_cny'],r['summary']['reprice_cancel_count'],r['summary']['throttle_skips'],flush=True)
    write(OUT/'results.json',ss);write(OUT/'portfolios.json',{f'{p}_spread{s}_through{int(t)}_delay{d}':portfolio(v) for (p,s,d,t),v in pairs.items()});write(OUT/'small_cash.json',small)
    for p,h in m.items():assert digest(ROOT/p)==h
    write(OUT/'verification.json',dict(status='passed',accounts=len(ss),small_accounts=len(small),boundaries=3*(len(ss)+len(small)),
        all_cash_fees_inventory_dd_prefix=True,capital20000_economic_equality=True,parents_unchanged=True))
    write(OUT/'manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='manifest.json'})


if __name__=='__main__':main()
