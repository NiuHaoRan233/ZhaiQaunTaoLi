"""Reproduce the two fixed0.2 research profiles without network or order access."""
from pathlib import Path
from probe_gold_spread_quote_control import OUT as BASE,replay
from probe_gold_spread_future import timeline,check
from probe_gold_direction import inputs,CODES
from probe_commodity_capital import ROOT,WORK,read,write,pack,unpack,digest

OUT=WORK/'reports/gold_intraday_v02_reproduction_20260911'


def main():
    frozen={}
    for folder,name in [('gold_rule_ladder_20260913_v1','result_manifest.json'),('gold_rule_simplification_20260913_v2','result_manifest.json'),
        ('gold_spread_future_20260913_v3','result_manifest.json'),('gold_spread_quote_control_20260913_v4','manifest.json')]:
        frozen.update(read(WORK/f'reports/{folder}/{name}'))
    for p,h in frozen.items():assert digest(ROOT/p)==h
    for folder in ('gold_rule_ladder_20260913_v1','gold_spread_future_20260913_v3','gold_spread_quote_control_20260913_v4'):
        plan=read(WORK/f'reports/{folder}/plan.json')
        for field in ('sources','source_hashes','inputs'):
            for p,h in plan.get(field,{}).items():assert digest(ROOT/p)==h
    OUT.mkdir(exist_ok=True);results=[]
    for code in CODES:
        es,fs,detail=inputs(code);strike=float(detail['OptExercisePrice']);rows=timeline(es,fs,strike)
        for profile in ('trend_long','cancel_switch'):
            capital=20000 if profile=='trend_long' else 250000
            for through in (False,True):
                key=f'{code}_{profile}_spread8_through{int(through)}_delay0'
                r=replay(rows,code,profile,strike,8,through,capital);check(r)
                expected=BASE/f'{key}{"_capital20000" if profile=="trend_long" else ""}.json.gz'
                assert r==unpack(expected),'Full dictionary mismatch'
                target=OUT/f'{key}.json.gz'
                if target.exists():assert unpack(target)==r
                else:pack(target,r)
                results.append(r['summary']);print(key,r['summary']['pnl_cny'],flush=True)
    write(OUT/'verification.json',dict(status='passed',accounts=len(results),boundaries=3*len(results),
        full_dictionary_equality=True,no_network_or_broker_connection=True,summaries=results))


if __name__=='__main__':main()
