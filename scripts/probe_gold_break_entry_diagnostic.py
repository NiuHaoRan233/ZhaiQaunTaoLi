"""One declared diagnostic intervention: stop new entries60s before each break.

This is a post-hoc causal replay, not a newly validated production strategy.
Settlement remains the frozen5s opposite-quote close. Possible old aggregate
fills settle before the new entry decision, as in the immutable parent.
"""
from pathlib import Path
import pandas as pd
from zhaiquant import gold_spread_quote_control as parent
from zhaiquant import gold_direction_research as g
from zhaiquant.gold_history_validation import Clock,timeline
from review_gold_history import OUT,BASE,DATES,FIXED
from probe_commodity_capital import ROOT,read,write,pack,unpack,digest
from probe_gold_rule_ladder import audit,portfolio

FAMILY='probe_gold_break_entry_diagnostic_20260914_v1'


class Account(parent.Account):
    def candidate(self,e,feature):
        if e.ts>=g.BOUNDARIES[e.session]-60000:return None,'entry_cutoff60_diagnostic'
        return super().candidate(e,feature)


def replay(rows,code,profile,detail,clock,through=False,cut=None):
    a=Account(code,profile,detail['OptExercisePrice'],8,through,250000)
    a.model=f'{FAMILY}_{clock.date}_{profile}_{code}_capital25000000_through{int(through)}'
    for kind,event,features,mid,future in rows:
        ts=event if kind==-1 else event.ts
        if cut is not None and ts>cut:break
        if kind==-1:a.boundary(event)
        elif kind==1:a.future_event(event,features)
        else:a.value.future=future;a.features=features;a.option(event)
    r=a.result();audit(r,complete=cut is None)
    r['summary'].update(history_date=clock.date,entry_cutoff_seconds=60,exit_cutoff_seconds=5,diagnostic_only=True)
    return clock.restore(r)


def main():
    dest=OUT/'entry_cutoff60';dest.mkdir(exist_ok=True)
    if (dest/'manifest.json').exists():raise RuntimeError('Frozen diagnostic')
    details=read(BASE/'catalog_terms.json')['details'];results={};pairs={}
    write(dest/'plan.json',dict(family=FAMILY,profiles=['trend_long','cancel_switch'],dates=DATES,
        hypothesis='Fewer near-break fresh openings can prevent exit-side liquidity blowout without changing the5s exit rule.',
        intervention_seconds=60,comparison='same frozen dates/contracts/fee/0delay/250k/code and both fills; no cutoff parameter search',
        limitation='Post-hoc diagnostic; evaluated on the same four dates that suggested the issue. Event-driven cutoff retains old-interval possible fills.',
        sources={str(Path(__file__).relative_to(ROOT)):digest(Path(__file__))}))
    for date in DATES:
        clock=Clock(date)
        for code in FIXED:
            detail=details[code];folder=BASE/date/'full_inputs'
            es,fs,_,_=clock.inputs(pd.read_pickle(folder/f'{code}.pkl'),pd.read_pickle(folder/f'{detail["OptUndlCode"]}.SF.pkl'),code,detail)
            rows=timeline(es,fs,detail['OptExercisePrice'],detail['ExpireDate'],clock)
            for profile in ('trend_long','cancel_switch'):
                for through in (False,True):
                    key=f'{date}_{code}_{profile}_through{int(through)}'
                    r=replay(rows,code,profile,detail,clock,through)
                    pre=replay(rows,code,profile,detail,clock,through,g.START+5*3600000)
                    for f,t in [('orders','created_ts'),('fills','ts'),('cycles','exit_ts'),('cancels','ts')]:
                        assert pre[f]==[v for v in r[f] if v[t]<=clock.start+5*3600000]
                    old=unpack(BASE/date/'ledgers'/f'{code}_v02_{profile}_through{int(through)}.json.gz')
                    def ck(c):return tuple(c[k] for k in ('direction','entry_ts','entry_price_cents','exit_ts','exit_price_cents'))
                    old_cs={ck(c):c for c in old['cycles']};new_cs={ck(c):c for c in r['cycles']}
                    removed=[c for k,c in old_cs.items() if k not in new_cs];added=[c for k,c in new_cs.items() if k not in old_cs]
                    assert sum(c['net_cents'] for c in added)-sum(c['net_cents'] for c in removed)==round((r['summary']['pnl_cny']-old['summary']['pnl_cny'])*100)
                    r['diagnostic_diff']=dict(old_pnl_cny=old['summary']['pnl_cny'],removed=removed,added=added,
                        retained=len(old_cs.keys()&new_cs.keys()))
                    pack(dest/f'{key}.json.gz',r);results[key]=r['summary'];pairs.setdefault((date,profile,through),[]).append(r)
                    print(key,r['summary']['pnl_cny'],'old',old['summary']['pnl_cny'],'removed',len(removed),'added',len(added),flush=True)
    write(dest/'results.json',results);write(dest/'portfolios.json',{f'{d}_{p}_through{int(t)}':portfolio(v) for (d,p,t),v in pairs.items()})
    write(dest/'verification.json',dict(status='passed',accounts=len(results),flat_boundaries=3*len(results),cash_fees_prefix_and_difference=True))
    write(dest/'manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in dest.glob('*') if p.name!='manifest.json'})


if __name__=='__main__':main()
