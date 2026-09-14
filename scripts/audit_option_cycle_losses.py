"""Explain frozen option losses without modifying orders or using future entry signals."""
from bisect import bisect_right
import gzip,json
from pathlib import Path
import numpy as np
from probe_option_top_cycle import ARCHIVE,NAMES,load

BASE=ARCHIVE/'大道至简初试_20260909_v1_run2'

def analyze(events,result):
    times=[e.ts for e in events]
    lookup={e.ts:i for i,e in enumerate(events)}
    order_lookup={o['id']:o for o in result['orders']}
    buys=[f for f in result['fills'] if f['side']=='buy']
    closed={c['entry_ts']:c for c in result['cycles']}
    entries=[]
    def at(ts):return events[max(0,bisect_right(times,ts)-1)]
    for f in buys:
        o=order_lookup[f['order_id']];e=at(o['created_ts']);entry=at(f['ts'])
        old10=at(e.ts-10000);old60=at(e.ts-60000)
        mid=(e.bid+e.ask)/2;spread=e.ask-e.bid
        trend10=(mid-(old10.bid+old10.ask)/2)/100 if old10.session==e.session and e.ts-old10.ts<=12000 else None
        trend60=(mid-(old60.bid+old60.ask)/2)/100 if old60.session==e.session and e.ts-old60.ts<=62000 else None
        c=closed.get(f['ts']);end=at(c['exit_ts']) if c else events[-1]
        section=events[lookup[f['ts']]:lookup[end.ts]+1]
        bids=[x.bid for x in section if x.bid>0]
        mark5=at(f['ts']+5000)
        row=dict(entry_ts=f['ts'],decision_ts=e.ts,kind=f['kind'],quantity=f['quantity'],
            quote_edge_cny=(spread-200-600)/100,spread_cny=spread/100,
            decision_trend10_cny=trend10,decision_trend60_cny=trend60,
            decision_bid_gap_cny=(e.bids[0][0]-e.bids[1][0])/100 if len(e.bids)>1 else None,
            complete=bool(c),net_cny=c['net_cents']/100 if c else None,
            duration_seconds=(end.ts-f['ts'])/1000,
            min_bid_mark_cny=(min(bids)-f['price_cents'])/100,
            entry_mid_mark_cny=((entry.bid+entry.ask)/2-f['price_cents'])/100,
            mid_5s_mark_cny=((mark5.bid+mark5.ask)/2-f['price_cents'])/100 if mark5.session==entry.session else None)
        if c:
            sells=[x for x in result['fills'] if x['side']=='sell' and f['ts']<x['ts']<=c['exit_ts']]
            p=sum(x['price_cents']*x['quantity'] for x in sells)/f['quantity']
            row.update(gross_cny=c['gross_cents']/100,
                entry_half_capture_cny=((entry.bid+entry.ask)/2-f['price_cents'])*f['quantity']/100,
                holding_mid_drift_cny=((end.bid+end.ask-entry.bid-entry.ask)/2)*f['quantity']/100,
                exit_half_capture_cny=(p-(end.bid+end.ask)/2)*f['quantity']/100)
            assert abs(row['gross_cny']-sum(row[k] for k in ['entry_half_capture_cny','holding_mid_drift_cny','exit_half_capture_cny']))<1e-5
        entries.append(row)
    def group(predicate):
        rr=[r for r in entries if r['complete'] and predicate(r)]
        return dict(cycles=len(rr),net_cny=sum(r['net_cny'] for r in rr),
            losing_cycles=sum(r['net_cny']<0 for r in rr),
            sum_losing_cny=sum(min(0,r['net_cny']) for r in rr))
    finished=[r for r in entries if r['complete']]
    groups={
        'hold_lt_30s':group(lambda r:r['duration_seconds']<30),
        'hold_30_120s':group(lambda r:30<=r['duration_seconds']<120),
        'hold_120_600s':group(lambda r:120<=r['duration_seconds']<600),
        'hold_ge_600s':group(lambda r:r['duration_seconds']>=600),
        'quote_net_space_le_0':group(lambda r:r['quote_edge_cny']<=0),
        'quote_net_space_0_10':group(lambda r:0<r['quote_edge_cny']<10),
        'quote_net_space_ge_10':group(lambda r:r['quote_edge_cny']>=10),
        'pre10_drop_halfspread':group(lambda r:r['decision_trend10_cny'] is not None and r['decision_trend10_cny']<=-.5*r['spread_cny']),
        'pre60_drop_spread':group(lambda r:r['decision_trend60_cny'] is not None and r['decision_trend60_cny']<=-r['spread_cny']),
        'entry_mid_negative':group(lambda r:r['entry_mid_mark_cny']<0),
        'arrival_cross':group(lambda r:r['kind']=='arrival_cross')}
    decomp={k:sum(r[k] for r in finished) for k in ['entry_half_capture_cny','holding_mid_drift_cny','exit_half_capture_cny']}
    return dict(code=result['summary']['code'],mode=result['summary']['mode'],summary=result['summary'],
        groups=groups,decomposition=decomp,entries=entries,
        worst=sorted(finished,key=lambda r:r['net_cny'])[:5],
        negative_entry_mid_count=sum(r['entry_mid_mark_cny']<0 for r in entries))

def main():
    details=json.loads((ARCHIVE/'options_market_20260909_snapshot.json').read_text(encoding='utf-8'))['details']
    rows=[]
    for c in NAMES:
        events,_=load(c,details[c])
        for mode in ['improve_l1','improve_single_d500']:
            result=json.load(gzip.open(BASE/f'{c}_{mode}_q1_f300.json.gz','rt',encoding='utf-8'))
            row=analyze(events,result);rows.append(row)
            print(c,mode,row['decomposition'],row['groups'],flush=True)
    out=ARCHIVE/'options_loss_diagnosis_20260909.json'
    out.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
