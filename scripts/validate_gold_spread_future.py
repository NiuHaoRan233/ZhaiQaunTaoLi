"""Frozen-profile validation, small long account and futures-cancel path audit."""
from pathlib import Path
from probe_gold_spread_future import OUT as BASE,timeline,replay,check,economics
from probe_gold_direction import inputs,CODES
from probe_gold_rule_ladder import portfolio
from audit_gold_rule_ladder import delta
from probe_commodity_capital import ROOT,WORK,read,write,pack,unpack,digest
from zhaiquant import gold_direction_research as g

OUT=WORK/'reports/gold_spread_future_validation_20260913'


def main():
    OUT.mkdir(exist_ok=True)
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen validation exists')
    m=read(BASE/'result_manifest.json')
    for p,h in m.items():assert digest(ROOT/p)==h
    cases=[(p,s,d) for p in ('trend_long','cancel_switch') for s in (6,8,10) for d in (0,500,1000)]
    write(OUT/'plan.json',dict(profiles=['trend_long','cancel_switch'],spreads=[6,8,10],future_delay=[0,500,1000],
        through=[False,True],fixed_main_spread=8,main_delay=0,selection='simplest positive two-code trend-long; enhanced cancel-switch separately; do not pick maximum neighbor or delay',
        parent_manifest=digest(BASE/'result_manifest.json'),source_sha256=digest(Path(__file__))))
    ss={};pairs={};small=[];deltas={};attribution={};reprice={}
    for code in CODES:
        cache={}
        for d in (0,500,1000):
            es,fs,detail=inputs(code,d);strike=float(detail['OptExercisePrice']);cache[d]=timeline(es,fs,strike)
        for profile,spread,delay in cases:
            rows=cache[delay]
            for through in (False,True):
                key=f'{code}_{profile}_spread{spread}_through{int(through)}_delay{delay}'
                r=replay(rows,code,profile,strike,spread,through,delay=delay);check(r)
                part=replay([x for x in rows if (x[1] if x[0]==-1 else x[1].ts)<=g.START+5*3600000],code,profile,strike,spread,through,delay=delay)
                for field,t in [('orders','created_ts'),('fills','ts'),('cycles','exit_ts'),('cancels','ts'),('manage_events','ts')]:
                    assert part[field]==[x for x in r[field] if x[t]<=g.START+5*3600000]
                if spread==8 and delay==0:
                    assert r==unpack(BASE/f'{key}.json.gz')
                    if profile=='trend_long':
                        low=replay(rows,code,profile,strike,spread,through,capital=20000);check(low)
                        assert economics(low)==economics(r);pack(OUT/f'{key}_capital20000.json.gz',low);small.append(low['summary'])
                pack(OUT/f'{key}.json.gz',r);ss[key]=r['summary'];pairs.setdefault((profile,spread,delay,through),[]).append(r)
                print(key,r['summary']['pnl_cny'],r['summary']['max_drawdown_cny'],flush=True)
        for through in (0,1):
            a=unpack(BASE/f'{code}_trend_switch_spread8_through{through}_delay0.json.gz')
            b=unpack(BASE/f'{code}_cancel_switch_spread8_through{through}_delay0.json.gz')
            deltas[f'{code}_through{through}']=delta(a,b)
        # Price-edge versus inventory drift: use quotes at actual fills, not order creation.
        books={x[1].ts:x[1] for x in cache[0] if x[0]==0}
        for profile in ('base_long','base_short','trend_long','value_long','value_switch','trend_switch','cancel_switch','manage_switch','throttle_long','throttle_switch'):
            r=unpack(BASE/f'{code}_{profile}_spread8_through0_delay0.json.gz')
            entry=drift=exit_=0;cycle_rows=[]
            for c in r['cycles']:
                e=books[c['entry_ts']];x=books[c['exit_ts']];assert g.valid(e) and g.valid(x)
                em=(e.bid+e.ask)/2;xm=(x.bid+x.ask)/2;d=c['direction']
                en=d*(em-c['entry_price_cents']);mv=d*(xm-em);ex=d*(c['exit_price_cents']-xm)
                assert en+mv+ex==c['gross_cents'];entry+=en;drift+=mv;exit_+=ex
                cycle_rows.append(dict(entry_ts=c['entry_ts'],exit_ts=c['exit_ts'],direction=d,entry_edge_cny=en/100,
                    holding_drift_cny=mv/100,exit_edge_cny=ex/100,fees_cny=c['fees_cents']/100,net_cny=c['net_cents']/100))
            attribution[code+'_'+profile]=dict(entry_edge_cny=entry/100,holding_drift_cny=drift/100,exit_edge_cny=exit_/100,
                fees_cny=r['summary']['fees_cny'],net_cny=r['summary']['pnl_cny'],cycles=cycle_rows)
            ordered={o['id']:o for o in r['orders']};dur=[]
            for c in r['cancels']:
                if c['reason']=='reprice':dur.append((c['ts']-ordered[c['order_id']]['created_ts'])/1000)
            reprice[code+'_'+profile]=dict(order_count=len(r['orders']),reprice_count=len(dur),
                reprice_under1s=sum(t<1 for t in dur),reprice_under2s=sum(t<2 for t in dur),
                median_lifetime_seconds=__import__('statistics').median(dur) if dur else None)
    write(OUT/'results.json',ss);write(OUT/'portfolios.json',{f'{p}_spread{s}_through{int(t)}_delay{d}':portfolio(v) for (p,s,d,t),v in pairs.items()})
    write(OUT/'small_cash.json',small);write(OUT/'cancel_path_changes.json',deltas);write(OUT/'attribution.json',attribution);write(OUT/'reprice_audit.json',reprice)
    for p,h in m.items():assert digest(ROOT/p)==h
    write(OUT/'verification.json',dict(status='passed',accounts=len(ss),small_accounts=len(small),boundaries=3*(len(ss)+len(small)),
        all_cash_fees_inventory_prefix_dd=True,base_main_full_dictionary_equal=True,frozen_spread8_not_optimized=True,
        cycle_edge_drift_fee_attribution_exact=True,parents_unchanged=True))
    write(OUT/'manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='manifest.json'})


if __name__=='__main__':main()
