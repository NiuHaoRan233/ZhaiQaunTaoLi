"""Independent reconciliation and immutable final evidence capture."""
from pathlib import Path
import sys
import pandas as pd
import numpy as np
from probe_gold_peers import OUT,BASE,SESSION_BASE,ROOT,DATES,read,write,unpack,digest,POLICIES,m
from report_gold_peers import LABELS,POLICY,MASK,fmt
from verify_gold_relative_spread import Tables
from probe_gold_backer import audit


def main():
    if (OUT/'manifest.json').exists():raise RuntimeError('Already frozen')
    results=read(OUT/'results.json');groups=read(OUT/'comparison.json');ids=set();orders=0;excluded=0;exits=0
    for key,s in results.items():
        r=unpack(OUT/'ledgers'/f'{key}.json.gz');assert s==r['summary'];audit(r,False)
        assert s['model_id'] not in ids;ids.add(s['model_id'])
        assert s['code'] in read(BASE/s['history_date']/'selection.json')['scenarios']['100']['selected']
        for o in r['orders']:
            if o['reason']=='entry':assert 2*o['entry_spread']*10000>=100*o['entry_mid_twice'];orders+=1
        for c in r['cycles']:
            if c['exit_kind']=='virtual_cost_close':assert c['net_cents']==c['fees_cents']==0;excluded+=1
            else:assert c['fees_cents']==340 and c['net_cents']==c['direction']*(c['exit_price_cents']-c['entry_price_cents'])-340
        assert round(s['pnl_cny']*100)==sum(c['net_cents'] for c in r['cycles'])
        for e in r['risk_events']:
            if e['reason']=='peer_consensus_exit':
                assert e['feature']['ts']<e['ts'];exits+=1
    for mask in MASK:
        for policy in POLICIES:
            for mode in ('long','short'):
                for variant in m.VARIANTS:
                    for strict in (0,1):
                        a=groups[f'{mask}_{policy}_{mode}_{variant}_through{strict}']
                        expected=[round(sum(s['pnl_cny'] for s in results.values() if s['session_mask']==mask and s['policy']==policy and s['trade_mode']==mode and s['peer_variant']==variant and int(s['strict_through'])==strict and s['history_date']==d),2) for d in DATES]
                        assert a['daily']==expected and abs(sum(expected)-a['pnl_cny'])<1e-8
    parser=Tables();parser.feed((OUT/'黄金期权_期权间联动预警对照.html').read_text('utf-8'))
    for strict in (0,1):
        rr=parser.tables[f'daily{strict}'][1:];i=0
        for mask in MASK:
            for policy in POLICIES:
                for mode,dl in [('long','先多'),('short','先空')]:
                    for variant in m.VARIANTS:
                        s=groups[f'{mask}_{policy}_{mode}_{variant}_through{strict}']
                        assert rr[i]==[MASK[mask],POLICY[policy],dl,LABELS[variant],*map(fmt,s['daily']),fmt(s['pnl_cny']),fmt(s['max_drawdown_cny']),str(s['normal_cycles']),str(s['virtual_cycles']),fmt(s['removed_tail_gross_cny']),str(s['peer_cancels']),str(s['peer_exits'])];i+=1
        assert i==32
    market=read(OUT/'target_market_context.json');assert digest(Path(market['source']))==market['source_sha256']
    for row,r in zip(parser.tables['target_market'][1:],market['rows'],strict=True):
        d=r['day'];vals=[fmt(results[f'{r["date"]}_{r["code"]}_day_value_{mode}_{variant}_through0']['pnl_cny']) for variant in m.VARIANTS for mode in ('long','short')]
        assert row==[r['date'],r['code'],str(d['volume_contracts']),f'{d["mean_mid"]:.3f}',f'{d["mean_spread"]:.4f}',f'{d["mean_relative_pct"]:.3f}%',*vals]
    cols=['time','lastPrice','volume','amount','bidPrice','askPrice','bidVol','askVol'];prefixes=0
    for date,ss in read(OUT/'selections.json').items():
        end=next(iter(ss.values()))['selection_end_exclusive']
        for c in {c for s in ss.values() for c in s['peers']}:
            a=pd.read_pickle(BASE/date/'prefix'/f'{c}.pkl');f=pd.read_pickle(OUT/date/'inputs'/f'{c}.pkl');f=f[f.time<end]
            pd.testing.assert_frame_equal(a[cols],f[cols],check_dtype=False);prefixes+=1
    for manifest in (BASE/'manifest.json',SESSION_BASE/'manifest.json',OUT/'capture_manifest.json'):
        for rel,h in read(manifest).items():assert digest(ROOT/rel)==h,rel
    for rel,h in read(OUT/'replay_plan.json')['source_hashes'].items():assert digest(ROOT/rel)==h,rel
    paths=list((ROOT/'src/zhaiquant').glob('gold*.py'))+list((ROOT/'scripts').glob('*gold*.py'))+[ROOT/'scripts/probe_commodity_capital.py']
    for name in ('commodity_dadao_research.py','commodity_flow_strategy.py','option_top_cycle_research.py'):paths.append(ROOT/'src/zhaiquant'/name)
    paths.append(ROOT/'tests/test_gold_peer_risk_research.py')
    sources={str(p.relative_to(ROOT)):digest(p) for p in paths};write(OUT/'source_manifest.json',sources)
    for rel in sources:
        p=OUT/'source_snapshot'/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((ROOT/rel).read_bytes())
    write(OUT/'report_verification.json',dict(status='passed',accounts=len(ids),entry_orders=orders,excluded_cycles=excluded,peer_exits=exits,
        unit_tests=110,html_daily_rows=64,html_target_market_rows=10,peer_prefixes_exact=prefixes,
        python=sys.version,numpy=np.__version__,pandas=pd.__version__))
    write(OUT/'manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.rglob('*') if p.is_file() and p.name!='manifest.json'})
    print('FROZEN',len(ids),'accounts',orders,'entries',prefixes,'prefixes')


if __name__=='__main__':main()
