"""New ledgers under compulsory pre-break observed-bid liquidation; parent immutable."""
from pathlib import Path
from dataclasses import replace
import json
import pandas as pd
from zhaiquant import gold_breakflat_research as engine
from zhaiquant.commodity_dadao_research import load_frame
from probe_commodity_intraday import append
from probe_gold_state import OUT as BASE
from probe_commodity_capital import ROOT,WORK,read,write,digest,pack,unpack

OUT=WORK/'reports/gold_breakflat_20260913_v1'


def replay(events,code,profile,tick,through=None):
    a=engine.BreakFlatAccount(code,profile,tick);rows={k:[] for k in ['orders','fills','cycles','curve']}
    boundaries=iter(engine.BOUNDARIES);boundary=next(boundaries,None)
    for e in events:
        if through is not None and e.ts>through:break
        while boundary is not None and e.ts>=boundary:
            a.check_boundary(boundary);boundary=next(boundaries,None)
        append(rows,a.step(e,engine.DATE))
    if through is None:
        while boundary is not None:
            a.check_boundary(boundary);boundary=next(boundaries,None)
        append(rows,a.close_day(engine.DATE))
    rows.update(summary=a.summary(),daily=a.daily(),boundary_checks=a.boundary_checks)
    return rows


def main():
    OUT.mkdir(exist_ok=True)
    sources=[Path(__file__),Path(engine.__file__),ROOT/'src/zhaiquant/gold_state_research.py',
        ROOT/'src/zhaiquant/commodity_flow_strategy.py',ROOT/'src/zhaiquant/commodity_intraday_research.py']
    plan=dict(family=engine.FAMILY,parent_plan_sha256=digest(BASE/'plan.json'),
        selection_sha256=digest(BASE/'selection.json'),boundaries=list(engine.BOUNDARIES),
        stop_entry_seconds=300,active_exit_seconds=60,profiles=['gap','control'],
        rule='Settle old eligible fills first. Stop new buys5min before break. Sell residual1contract against current valid bid1min before each10:15/11:30/15:00 boundary; fees1.70per side. No cost reset. Missing bid is an explicit flatness failure.',
        sources={str(p.relative_to(ROOT)):digest(p) for p in sources})
    if (OUT/'plan.json').exists():assert read(OUT/'plan.json')==plan
    else:write(OUT/'plan.json',plan)
    prior_manifest=read(BASE/'result_manifest.json')
    for p,h in prior_manifest.items():assert digest(ROOT/p)==h
    selected=read(BASE/'selection.json')['selected'];details=read(BASE/'catalog_terms.json')['details'];results={}
    for code in selected:
        frame=pd.read_pickle(BASE/'inputs'/f'{code}.pkl')
        frame=frame[(frame.time>=engine.START)&(frame.time<engine.END)]
        es,flags,meta=load_frame(frame,code=code,date=engine.DATE,detail=details[code])
        events=[replace(e,quantity=min(e.quantity,1)) for e in es if e.ts>=engine.CUTOFF]
        event_map={e.ts:e for e in events}
        for profile in plan['profiles']:
            key=f'{code}_{profile}';r=replay(events,code,profile,meta['tick_cents']);s=r['summary']
            assert s['end_inventory']==0 and s['virtual_close_count']==0
            cash=15000000;inv=0
            for f in r['fills']:
                cash+=f['price_cents']*(1 if f['side']=='sell' else -1)-170
                inv+=1 if f['side']=='buy' else -1
                assert (cash,inv)==(f['cash_cents'],f['inventory'])
                if f['kind']=='pre_break_active_sell':
                    e=event_map[f['ts']]
                    assert f['price_cents']==e.bid and e.bid_qty>=1
                    assert f['boundary_ts']-60000<=f['ts']<f['boundary_ts']
            assert cash==round(s['end_cash_cny']*100) and inv==0
            assert sum(c['net_cents'] for c in r['cycles'])==round(s['pnl_cny']*100)
            assert sum(f['fee_cents'] for f in r['fills'])==round(s['fees_cny']*100)
            for boundary in engine.BOUNDARIES:
                assert not any(c['entry_ts']<boundary<=c['exit_ts'] for c in r['cycles'])
                assert not any(o['side']=='buy' and boundary-300000<=o['created_ts']<boundary for o in r['orders'])
            assert len(r['boundary_checks'])==3
            # Exact economic equivalence before the first new entry cutoff.
            old=unpack(BASE/f'{key}.json.gz');first_cut=engine.BOUNDARIES[0]-300000
            normalize=lambda rs:[{k:v for k,v in x.items() if k!='model_id'} for x in json.loads(json.dumps(rs))]
            for field,time in [('orders','created_ts'),('fills','ts')]:
                assert normalize([x for x in r[field] if x[time]<first_cut])==normalize([x for x in old[field] if x[time]<first_cut])
            # Fresh prefix contains both intervening break checks and active exits.
            cut=engine.START+5*3600000
            prefix=replay(events,code,profile,meta['tick_cents'],through=cut)
            for field,time in [('orders','created_ts'),('fills','ts')]:
                assert json.loads(json.dumps(prefix[field]))==[x for x in json.loads(json.dumps(r[field])) if x[time]<=cut]
            pack(OUT/f'{key}.json.gz',r);results[key]={k:r[k] for k in ['summary','daily','boundary_checks']}
            print('RESULT',key,s,flush=True)
    write(OUT/'results.json',results)
    for p,h in prior_manifest.items():assert digest(ROOT/p)==h
    for p,h in plan['sources'].items():assert digest(ROOT/p)==h
    write(OUT/'verification.json',dict(status='passed',accounts=4,zero_inventory_boundaries=12,
        no_cross_break_cycles=True,no_virtual_cost_exits=True,active_fills_match_observed_bid=True,
        cash_and_fees_reconstructed=True,pre_first_cutoff_parent_equivalence=True,
        prefix_replayed_through='14:00',prefix_includes_both_breaks=True,
        parent_result_files_unchanged=len(prior_manifest)))
    write(OUT/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='result_manifest.json'})


if __name__=='__main__':main()
