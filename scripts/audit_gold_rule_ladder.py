"""Independent drawdown, trade-path deltas and small-account replays."""
from pathlib import Path
import numpy as np
from zhaiquant import gold_direction_research as g
from probe_gold_rule_ladder import OUT as V1,timeline,replay,audit,economic,CODES
from probe_gold_rule_simplification import OUT as V2,replay as replay2
from probe_gold_direction import inputs
from probe_commodity_capital import ROOT,WORK,read,write,unpack,pack,digest

OUT=WORK/'reports/gold_rule_ladder_audit_20260913'


def cycle_key(c):return tuple(c[k] for k in ('entry_ts','exit_ts','direction','entry_price_cents','exit_price_cents'))


def delta(a,b):
    old={cycle_key(c):c for c in a['cycles']};new={cycle_key(c):c for c in b['cycles']}
    common=old.keys()&new.keys();removed=old.keys()-new.keys();added=new.keys()-old.keys()
    assert len(old)==len(a['cycles']) and len(new)==len(b['cycles'])
    for k in common:assert old[k]['net_cents']==new[k]['net_cents']
    rm=sum(old[k]['net_cents'] for k in removed);ad=sum(new[k]['net_cents'] for k in added)
    assert ad-rm==round(100*(b['summary']['pnl_cny']-a['summary']['pnl_cny']))
    return dict(same_cycles=len(common),removed_cycles=len(removed),added_cycles=len(added),
        removed_net_cny=rm/100,added_net_cny=ad/100,delta_pnl_cny=(ad-rm)/100,
        delta_drawdown_cny=round(b['summary']['max_drawdown_cny']-a['summary']['max_drawdown_cny'],2))


def main():
    OUT.mkdir(exist_ok=True); manifests={};summaries={}; count=0
    for folder in (V1,V2):
        m=read(folder/'result_manifest.json');manifests.update(m)
        for p,h in m.items():assert digest(ROOT/p)==h
        summaries[folder.name]=read(folder/'results.json')
        for key,s in summaries[folder.name].items():
            r=unpack(folder/f'{key}.json.gz');a=np.asarray(r['curve'],dtype=np.int64)[:,1]
            dd=int((np.maximum.accumulate(np.maximum(a,0))-a).max())
            assert dd==round(s['max_drawdown_cny']*100)
            assert sum(c['fees_cents'] for c in r['cycles'])==round(s['fees_cny']*100)
            assert sum(c['net_cents'] for c in r['cycles'])==round(s['pnl_cny']*100);count+=1
    deltas={};cash={};occupancy={}
    for code in CODES:
        for d in ('long','short'):
            for through in (0,1):
                for i in range(1,11):
                    a=unpack(V1/f'{code}_s{i-1:02d}_{d}_through{through}.json.gz')
                    b=unpack(V1/f'{code}_s{i:02d}_{d}_through{through}.json.gz')
                    deltas[f'{code}_s{i:02d}_{d}_through{through}']=delta(a,b)
        es,fs,detail=inputs(code);strike=float(detail['OptExercisePrice']);rows,_=timeline(es,fs,strike)
        for case,run,folder in [('s00',replay,V1),('spread8',replay2,V2)]:
            for through in (False,True):
                key=f'{code}_{case}_long_through{int(through)}'
                r=run(rows,code,case,1,strike,capital=20000,through=through);audit(r)
                old=unpack(folder/f'{key}.json.gz');same=economic(r)==economic(old)
                assert same,'Do not quote40k returns if cash changes path'
                pack(OUT/f'{key}_capital20000.json.gz',r)
                cash[key]=dict(same_orders_fills_curve=same,summary=r['summary'])
        r=unpack(V1/f'{code}_s10_long_through0.json.gz')
        for f in r['fills']:occupancy.setdefault(f['ts'],[]).append((code,0 if f['closing'] else f['price_cents']))
    held={c:0 for c in CODES};peak=0;peak_ts=None
    for ts,changes in sorted(occupancy.items()):
        for code,basis in changes:held[code]=basis
        if sum(held.values())>peak:peak=sum(held.values());peak_ts=ts
    write(OUT/'step_trade_deltas.json',deltas);write(OUT/'small_account_checks.json',cash)
    write(OUT/'verification.json',dict(status='passed',independent_accounts_audited=count,step_pairs=len(deltas),
        extra_capital_replays=len(cash),extra_boundary_checks=3*len(cash),
        original_full_peak_simultaneously_held_premium_cny=peak/100,peak_ts=peak_ts,
        occupancy_convention='aggregate same-timestamp fills then mark paid entry-premium basis; excludes unfilled buy-order cash reservation'))
    for p,h in manifests.items():assert digest(ROOT/p)==h
    write(OUT/'manifest.json',{**{str(Path(__file__).relative_to(ROOT)):digest(Path(__file__))},
        **{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='manifest.json'}})


if __name__=='__main__':main()
