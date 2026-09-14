"""Explicit post-first-round follow-up; preserves the original search contract."""
from itertools import groupby
import heapq
from pathlib import Path
from probe_commodity_capital import ROOT,WORK,OUT,read,write,digest,stream,pack,unpack
from zhaiquant import commodity_capital_research as engine

DEST=WORK/'reports/commodity_capital_extension_20260913'

def run():
    DEST.mkdir(exist_ok=True)
    inputs=read(OUT/'independent_summary.json')
    contract=dict(stage='post-first-round explicit extension',family=engine.FAMILY,profile='edge10',
        budgets_cny=[200000,300000,500000],
        reason='Independent edge10 improves total PnL and absolute drawdown; test shared interaction, do not infer combined results by arithmetic.',
        selection_uses_seen_results=True,untouched_oos=False,
        first_round_summary_sha256=digest(OUT/'independent_summary.json'),
        sources={str(p.relative_to(ROOT)):digest(p) for p in [Path(__file__),Path(engine.__file__),ROOT/'scripts/probe_commodity_capital.py']})
    target=DEST/'candidate_contract.json'
    if target.exists():assert read(target)==contract
    else:write(target,contract)
    cfg=read(WORK/'commodity_strategy/strategy_r2.json')
    instruments={v['code']:dict(initial_cents=v['initial_cents'],tick_cents=round(v['unit']*v['price_tick']*100)) for v in cfg['instruments']}
    ps={f'edge10_{c}':engine.SharedPortfolio(instruments,'edge10',c*100) for c in contract['budgets_cny']}
    last=None;frames=0
    for ts,group in groupby(heapq.merge(*(stream(c) for c in sorted(instruments)),key=lambda x:(x[0],x[1])),key=lambda x:x[0]):
        for _,code,e,date in group:
            for p in ps.values():p.step(code,e,date)
            frames+=1
        for p in ps.values():p.mark(ts,date)
        if date!=last:print('edge10 day',date,flush=True);last=date
    results={}
    for key,p in ps.items():
        a=p.result();pack(DEST/f'{key}.json.gz',a)
        results[key]={k:a[k] for k in ['summary','daily','accounts','curve']}
        print(key,a['summary'],flush=True)
    write(DEST/'summary.json',dict(results=results,frames=frames,contract=contract))
    # Recompute a real chronological prefix from initial cash; future is never fed.
    prefix={profile:engine.SharedPortfolio(instruments,profile,30000000) for profile in ['base','edge10']}
    for ts,group in groupby(heapq.merge(*(stream(c) for c in sorted(instruments)),key=lambda x:(x[0],x[1])),key=lambda x:x[0]):
        if ts>1788505199999:break
        for _,code,e,date in group:
            for p in prefix.values():p.step(code,e,date)
        for p in prefix.values():p.mark(ts,date)
    for profile,p in prefix.items():
        saved=unpack((OUT/'shared'/f'{profile}_300000.json.gz') if profile=='base' else DEST/f'{profile}_300000.json.gz')
        a=p.result()
        assert a['fills']==[f for f in saved['fills'] if f['ts']<=1788505199999]
        assert a['orders']==[o for o in saved['orders'] if o['created_ts']<=1788505199999]
        assert a['daily']==[d for d in saved['daily'] if d['date']<='20260904']
    write(DEST/'prefix_verification.json',dict(status='passed',models=['base_300000','edge10_300000'],through='20260904',
        fresh_initial_cash=True,checks=['orders','fills','daily'],future_inputs_processed=False))
    write(DEST/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in DEST.iterdir() if p.is_file() and p.name!='result_manifest.json'})

if __name__=='__main__':run()
