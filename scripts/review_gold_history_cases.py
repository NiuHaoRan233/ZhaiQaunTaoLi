"""Causal entry information, saved-policy differences and quote-level examples."""
from collections import defaultdict
import numpy as np
from review_gold_history import OUT,BASE,DATES,FIXED,FOCUS,Market,summarize,clock
from probe_commodity_capital import read,write,unpack,pack
from zhaiquant.gold_history_validation import timestamp

PAIRS=[('fusion_base_long','fusion_trend_long','只加趋势和预热'),
       ('fusion_value_top_long','fusion_value_long','只加有限成本保护'),
       ('fusion_value_long','fusion_throttle_long','只加旧节流'),
       ('fusion_value_switch','fusion_trend_switch','估值择向只加趋势'),
       ('fusion_trend_switch','fusion_cancel_switch','只加期货先撤单'),
       ('fusion_cancel_switch','v02_cancel_switch','只加新版追价控制'),
       ('fusion_trend_long','v02_trend_long','趋势多头只加新版追价控制')]


def cycle_key(c):return tuple(c[k] for k in ('direction','entry_ts','entry_price_cents','exit_ts','exit_price_cents'))


def main():
    details=read(BASE/'catalog_terms.json')['details'];rows=unpack(OUT/'focus_cycles.json.gz');enriched={};comparisons=[];examples=[]
    for date in DATES:
        for code in FIXED:
            m=Market(date,code,details[code]);cache={}
            def ledger(key,t=0):
                name=(key,t)
                if name not in cache:cache[name]=unpack(BASE/date/'ledgers'/f'{code}_{key}_through{t}.json.gz')
                return cache[name]
            for key in FOCUS:
                for t in (0,1):
                    r=ledger(key,t);sid=r['summary']['model_id'];fill_map={f['ts']:f for f in r['fills'] if f['closing']};orders={o['id']:o for o in r['orders']}
                    rs=rows[sid]
                    for diag,c in zip(rs,r['cycles']):
                        f=fill_map[c['exit_ts']];o=orders[f['order_id']];d=c['direction'];mid=o['entry_mid_twice']/2
                        diag.update(exit_order_time=clock(o['created_ts']),exit_order_ts=o['created_ts'],exit_reason=o['reason'],
                            advertised_exit_edge=d*(c['exit_price_cents']-mid)/100,
                            exit_quote_repricing=diag.get('exit_edge',0)-d*(c['exit_price_cents']-mid)/100,
                            passive_exit=f['kind']=='passive',passive_exit_below_mid=f['kind']=='passive' and diag.get('exit_edge',0)<0,
                            remaining_session_seconds=timestamp(date)+[75,150,360][m.em[c['entry_ts']].session]*60000-c['entry_order_ts'])
                        diag['remaining_session_seconds']/=1000
                        orderbook=m.em[c['entry_order_ts']];fu=m.future(c['entry_order_ts']);delta=diag.get('signal_delta')
                        for sec in (60,300):
                            target=c['entry_order_ts']-sec*1000;before=m.book(target,orderbook.session)
                            old=m.future(target) if before else None
                            diag[f'causal_prior{sec}_future_impact']=d*delta*(fu-old)*1000 if fu and old and delta else None
                        diag['signal_spread_allowance_cny']=max(40,c['entry_spread']/200)
                        diag['edge_gate_required_cny']=max(20,c['entry_spread']/400)
                        diag['entry_valuation_reject']=diag['signal_fair_edge'] is None or diag['signal_fair_edge']<diag['edge_gate_required_cny']
                    enriched[sid]=rs
                if key in ('v02_trend_long','fusion_throttle_long','v02_cancel_switch'):
                    rs=enriched[ledger(key)['summary']['model_id']]
                    if rs:
                        chosen=[min(rs,key=lambda x:x['net_cny']),max(rs,key=lambda x:x['net_cny'])]
                        for diag in chosen:
                            lo=diag['entry_ts']-90000;hi=diag['exit_ts']+60000
                            books=[dict(ts=e.ts,time=clock(e.ts),bid=e.bid/100000,ask=e.ask/100000,
                                bid_qty=e.bid_qty,ask_qty=e.ask_qty,last=e.last/100000,quantity=e.quantity,strict_side=e.strict_side,
                                future=m.future(e.ts)) for e in m.es if lo<=e.ts<=hi and e.session==diag['session']]
                            examples.append(dict(trade=diag,books=books,
                                orders=[o for o in ledger(key)['orders'] if lo<=o['created_ts']<=diag['exit_ts']],
                                fills=[f for f in ledger(key)['fills'] if diag['entry_ts']<=f['ts']<=diag['exit_ts']]))
            for a,b,label in PAIRS:
                for t in (0,1):
                    old,new=ledger(a,t),ledger(b,t);aa={cycle_key(c):c for c in old['cycles']};bb={cycle_key(c):c for c in new['cycles']}
                    removed=[v for k,v in aa.items() if k not in bb];added=[v for k,v in bb.items() if k not in aa]
                    diff=(sum(c['net_cents'] for c in added)-sum(c['net_cents'] for c in removed))/100
                    full=old['summary']['status']==new['summary']['status']=='complete'
                    if full:assert abs(diff-(new['summary']['pnl_cny']-old['summary']['pnl_cny']))<1e-6
                    comparisons.append(dict(date=date,code=code,through=t,old=a,new=b,label=label,full_days=full,
                        pnl_delta=diff if full else None,common=len(aa.keys()&bb.keys()),
                        removed=len(removed),removed_net=sum(c['net_cents'] for c in removed)/100,
                        added=len(added),added_net=sum(c['net_cents'] for c in added)/100,
                        old_orders=old['summary']['order_count'],new_orders=new['summary']['order_count'],
                        old_reprices=old['summary']['reprice_cancel_count'],new_reprices=new['summary']['reprice_cancel_count']))
            print('CASES',date,code,flush=True)
    pack(OUT/'enriched_cycles.json.gz',enriched);pack(OUT/'case_windows.json.gz',examples);write(OUT/'policy_pairs.json',comparisons)
    splits={}
    for key in ('v02_trend_long','fusion_throttle_long','v02_cancel_switch'):
        for t in (0,1):
            rs=[r for vs in enriched.values() for r in vs if r['case']==key and r['through']==t]
            buckets={}
            for label,fn in [('valuation_at_order',lambda x:'would_reject' if x['entry_valuation_reject'] else 'passes'),
                ('prior300_direction',lambda x:'unknown' if x['causal_prior300_future_impact'] is None else 'adverse' if x['causal_prior300_future_impact']<0 else 'non_adverse'),
                ('near_break',lambda x:'<=60s' if x['remaining_session_seconds']<=60 else '>60s')]:
                bs=defaultdict(list)
                for r in rs:bs[fn(r)].append(r)
                buckets[label]={l:summarize(v) for l,v in bs.items()}
            splits[f'{key}_through{t}']=buckets
    write(OUT/'causal_diagnostic_groups.json',splits)


if __name__=='__main__':main()
