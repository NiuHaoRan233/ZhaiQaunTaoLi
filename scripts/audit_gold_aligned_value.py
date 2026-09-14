"""Audit the exit-target hypothesis and all economic differences without deleting losers."""
from collections import Counter
from bisect import bisect_right
from math import ceil
from probe_gold_direction import OUT as OLD,inputs,CODES
from probe_gold_aligned_value import OUT as BASE
from probe_commodity_capital import ROOT,WORK,read,write,unpack,digest

OUT=WORK/'reports/gold_aligned_audit_20260913'


def econ(c):return (c['entry_ts'],c['exit_ts'],c['entry_price_cents'],c['exit_price_cents'],c['net_cents'])


def main():
    OUT.mkdir(exist_ok=True);exit_audit={};changes={};stats={}
    for code in CODES:
        es,fs,d=inputs(code);times=[e.ts for e in es];old=unpack(OLD/f'{code}_fair_long_market.json.gz');losses=[]
        for c in old['cycles']:
            if c['net_cents']>=0:continue
            target=max(c['entry_price_cents']+c['entry_spread']-4000,ceil((c['entry_price_cents']+2340)/2000)*2000)
            seen=next((e for e in es[bisect_right(times,c['entry_ts']):bisect_right(times,c['exit_ts'])]
                if e.previous_ts>=c['entry_ts'] and e.single and e.quantity>0 and e.strict_side=='buy' and e.last>=target),None)
            losses.append(dict(entry_ts=c['entry_ts'],exit_ts=c['exit_ts'],net_cents=c['net_cents'],
                fixed_target_cents=target,eligible_target_touch_ts=seen.ts if seen else None))
        exit_audit[code]=dict(losing_cycles=len(losses),initial_profitable_target_eligible_before_exit=sum(x['eligible_target_touch_ts'] is not None for x in losses),losses=losses)
        a=unpack(BASE/f'{code}_aligned_long_market_d0.json.gz');b=unpack(BASE/f'{code}_aligned_fast_lower_market_d0.json.gz')
        ca,cb=Counter(econ(c) for c in a['cycles']),Counter(econ(c) for c in b['cycles']);common=ca&cb
        before,after=ca-common,cb-common
        changes[code]=dict(common_cycles=sum(common.values()),removed_cycles=sum(before.values()),added_cycles=sum(after.values()),
            removed_net_cny=sum(k[-1]*v for k,v in before.items())/100,added_net_cny=sum(k[-1]*v for k,v in after.items())/100,
            pnl_change_cny=b['summary']['pnl_cny']-a['summary']['pnl_cny'])
        assert round((changes[code]['added_net_cny']-changes[code]['removed_net_cny'])*100)==round(changes[code]['pnl_change_cny']*100)
        for p in BASE.glob(code+'*.json.gz'):
            r=unpack(p);nets=sorted([c['net_cents']/100 for c in r['cycles'] if c['exit_kind']!='virtual_cost_close'],reverse=True)
            stats[p.stem.removesuffix('.json')]=dict(normal_net_cny=sum(nets),normal_cycles=len(nets),
                without_best_three_cny=sum(nets)-sum(max(x,0) for x in nets[:3]),
                winning_cycles=sum(x>0 for x in nets),losing_cycles=sum(x<0 for x in nets),
                largest_win_cny=max(nets,default=0),largest_loss_cny=min(nets,default=0))
    write(OUT/'exit_target_audit.json',exit_audit);write(OUT/'fast_reference_path_changes.json',changes);write(OUT/'statistics.json',stats)
    sources={**read(OLD/'result_manifest.json'),**read(BASE/'result_manifest.json')}
    for p,h in sources.items():assert digest(ROOT/p)==h
    write(OUT/'source_manifest.json',sources)
    write(OUT/'artifact_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='artifact_manifest.json'})
    print(changes)


if __name__=='__main__':main()
