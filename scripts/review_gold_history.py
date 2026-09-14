"""Read-only forensic attribution of frozen gold ledgers, no rule changes."""
from pathlib import Path
from bisect import bisect_left,bisect_right
from collections import Counter,defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import numpy as np
import pandas as pd
from zhaiquant.commodity_dadao_research import load_frame
from zhaiquant import gold_direction_research as g
from zhaiquant.gold_history_validation import timestamp
from probe_commodity_capital import ROOT,WORK,read,write,unpack,pack,digest
from capture_gold_history import OUT as BASE,DATES,FIXED

OUT=WORK/'reports/gold_history_review_20260914_v1'
FOCUS=['ladder_spread8_long','ladder_s10_long','simple_core_long','fusion_value_top_long',
       'fusion_value_long','fusion_throttle_long','fusion_trend_long','v02_trend_long',
       'fusion_value_switch','fusion_trend_switch','fusion_cancel_switch','v02_cancel_switch']


def clock(ts):return datetime.fromtimestamp(ts/1000,ZoneInfo('Asia/Shanghai')).strftime('%H:%M:%S.%f')[:-3]


class Market:
    def __init__(self,date,code,detail):
        self.date=date;self.code=code;self.detail=detail
        f=pd.read_pickle(BASE/date/'full_inputs'/f'{code}.pkl')
        es,_,_=load_frame(f,code=code,date=date,detail=detail)
        self.es=es;self.em={e.ts:e for e in es};self.times=np.array([e.ts for e in es]);self.mids=np.array([(e.bid+e.ask)/2 for e in es]);self.valid=np.array([g.valid(e) for e in es])
        self.sessions=np.array([e.session for e in es]);self.bids=np.array([e.bid for e in es]);self.asks=np.array([e.ask for e in es])
        f=pd.read_pickle(BASE/date/'full_inputs'/f'{detail["OptUndlCode"]}.SF.pkl').sort_values('time',kind='stable').drop_duplicates('time',keep='last')
        valid=[0<x.bidPrice[0]<=x.askPrice[0] and min(x.bidVol[0],x.askVol[0])>0 for x in f.itertuples()]
        f=f[valid];self.ft=f.time.to_numpy(dtype=np.int64);self.fm=np.array([(x.bidPrice[0]+x.askPrice[0])/2 for x in f.itertuples()])
        self.expiry=timestamp(detail['ExpireDate'],'150000');self.cache={}

    def future(self,ts):
        i=bisect_left(self.ft,ts)-1
        return float(self.fm[i]) if i>=0 and ts-int(self.ft[i])<=2000 else None

    def maturity(self,ts):return max(1/365,(self.expiry-ts)/(365*86400000))

    def book(self,ts,session,age=2000):
        i=bisect_right(self.times,ts)-1
        return self.es[i] if i>=0 and ts-self.times[i]<=age and self.sessions[i]==session and self.valid[i] else None

    def analyze(self,c):
        key=tuple(c[x] for x in ('entry_ts','exit_ts','entry_price_cents','exit_price_cents','direction','entry_mid_twice','entry_order_ts'))
        if key in self.cache:return dict(self.cache[key])
        en=self.em[c['entry_ts']];ex=self.em[c['exit_ts']];d=c['direction'];p=c['entry_price_cents'];q=c['exit_price_cents']
        row=dict(date=self.date,code=self.code,entry_ts=c['entry_ts'],exit_ts=c['exit_ts'],entry_time=clock(c['entry_ts']),exit_time=clock(c['exit_ts']),
            direction=d,session=en.session,entry_price=p/100000,exit_price=q/100000,net_cny=c['net_cents']/100,
            fees_cny=c['fees_cents']/100,duration=c['duration_seconds'],exit_kind=c['exit_kind'],
            order_age_seconds=(c['entry_ts']-c['entry_order_ts'])/1000,entry_spread_ticks=c['entry_spread']/2000,
            valid_endpoint_books=g.valid(en) and g.valid(ex))
        if not row['valid_endpoint_books']:
            self.cache[key]=row;return dict(row)
        mi=(en.bid+en.ask)/2;mo=(ex.bid+ex.ask)/2
        advertised=d*(c['entry_mid_twice']/2-p)/100
        wait=d*(mi-c['entry_mid_twice']/2)/100
        drift=d*(mo-mi)/100;exit_edge=d*(q-mo)/100
        assert abs(advertised+wait+drift+exit_edge-row['fees_cny']-row['net_cny'])<1e-6
        row.update(advertised_entry_edge=advertised,pre_fill_mid_move=wait,entry_edge=advertised+wait,
            holding_mid_move=drift,exit_edge=exit_edge,entry_bid=en.bid/100000,entry_ask=en.ask/100000,
            exit_bid=ex.bid/100000,exit_ask=ex.ask/100000)
        lo=bisect_left(self.times,c['entry_ts']);hi=bisect_right(self.times,c['exit_ts'])
        marks=(self.bids[lo:hi] if d==1 else self.asks[lo:hi]);okay=self.valid[lo:hi]
        liquid=d*(marks[okay]-p)/100-3.4
        row.update(best_executable_net=float(liquid.max()),worst_executable_net=float(liquid.min()))
        fi=self.future(c['entry_ts']);fo=self.future(c['exit_ts']);t=self.maturity(c['entry_ts']);te=self.maturity(c['exit_ts']);k=float(self.detail['OptExercisePrice'])
        iv=g.implied_vol(mi/100000,fi,k,t) if fi is not None else None
        row.update(future_entry=fi,future_exit=fo,entry_mid_iv=iv,future_price_component=None,time_component=None,residual_component=None)
        if fi is not None and fo is not None and iv is not None:
            base=g.black_call(fi,k,t,iv)[0];at_future=g.black_call(fo,k,t,iv)[0];at_end=g.black_call(fo,k,te,iv)[0]
            future_move=d*(at_future-base)*1000;theta=d*(at_end-at_future)*1000
            row.update(future_price_component=future_move,time_component=theta,residual_component=drift-future_move-theta)
        for sec in (1,10,30,60):
            after=self.book(c['entry_ts']+sec*1000,en.session)
            row[f'after{sec}_mid_move']=d*((after.bid+after.ask)/2-mi)/100 if after else None
        before=self.book(c['entry_order_ts']-60000,en.session)
        current=self.em.get(c['entry_order_ts'])
        row['prior60_option_move_cny']=d*((current.bid+current.ask)-(before.bid+before.ask))/200 if current and before else None
        self.cache[key]=row;return dict(row)

    def market_summary(self):
        out=[]
        for ses in range(3):
            ids=np.where(self.valid&(self.sessions==ses))[0]
            es=[self.es[i] for i in ids];lo,hi=es[0].ts,es[-1].ts
            fs=self.fm[(self.ft>=lo)&(self.ft<=hi)]
            ts=self.times[ids];mid=self.mids[ids]/100000
            weights=np.minimum(np.diff(ts,append=ts[-1]),60000)
            spread=np.array([(e.ask-e.bid)/2000 for e in es])
            iv=[]
            for e in es[::120]:
                fu=self.future(e.ts)
                if fu:
                    v=g.implied_vol((e.bid+e.ask)/200000,fu,float(self.detail['OptExercisePrice']),self.maturity(e.ts))
                    if v is not None:iv.append(v)
            out.append(dict(date=self.date,code=self.code,session=ses,future_start=float(fs[0]),future_end=float(fs[-1]),future_change=float(fs[-1]-fs[0]),
                option_start=float(mid[0]),option_end=float(mid[-1]),option_change=float(mid[-1]-mid[0]),option_range=float(mid.max()-mid.min()),
                time_spread8_fraction=float(weights[spread>=8].sum()/weights.sum()),median_sample_iv=float(np.median(iv)) if iv else None,
                opening_sample_iv=iv[0] if iv else None,closing_sample_iv=iv[-1] if iv else None))
        return out


FIELDS=['net_cny','fees_cny','advertised_entry_edge','pre_fill_mid_move','entry_edge','holding_mid_move','exit_edge',
        'future_price_component','time_component','residual_component']


def summarize(rows):
    complete=[r for r in rows if r['valid_endpoint_books']]
    result={f:sum(r[f] for r in complete if r.get(f) is not None) for f in FIELDS}
    result.update(cycles=len(rows),invalid_books=len(rows)-len(complete),missing_future=sum(r.get('future_price_component') is None for r in rows),
        median_hold=float(np.median([r['duration'] for r in rows])) if rows else 0,
        winners=sum(r['net_cny']>0 for r in rows),losers=sum(r['net_cny']<0 for r in rows),
        profitable_before_losing=sum(r['net_cny']<0 and r.get('best_executable_net',0)>0 for r in rows),
        loser_best_net_cny=sum(r['net_cny'] for r in rows if r['net_cny']<0 and r.get('best_executable_net',0)>0),
        active_close_count=sum(r['exit_kind']=='pre_break_market_close' for r in rows),
        active_close_net=sum(r['net_cny'] for r in rows if r['exit_kind']=='pre_break_market_close'),
        without_best3=sum(r['net_cny'] for r in rows)-sum(sorted([r['net_cny'] for r in rows if r['net_cny']>0],reverse=True)[:3]))
    for sec in (1,10,30,60):
        v=[r.get(f'after{sec}_mid_move') for r in rows];v=[x for x in v if x is not None]
        result[f'after{sec}_mean']=float(np.mean(v)) if v else None
        result[f'after{sec}_adverse_fraction']=sum(x<0 for x in v)/len(v) if v else None
        result[f'after{sec}_observations']=len(v)
    return result


def main():
    OUT.mkdir(exist_ok=True)
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen attribution exists')
    detail=read(BASE/'catalog_terms.json')['details'];all_rows={};accounts={};market=[];totals=defaultdict(list)
    for date in DATES:
        for code in FIXED:
            m=Market(date,code,detail[code]);market.extend(m.market_summary())
            paths=sorted((BASE/date/'ledgers').glob(f'{code}_*.json.gz'))
            for path in paths:
                r=unpack(path);s=r['summary'];key=s['case_key'];through=int(s['strict_through'])
                rows=[]
                for c in r['cycles']:
                    row=m.analyze(c);signal=c.get('entry_signal') or {};fill_signal=c.get('entry_fill_future') or {}
                    row.update(case=key,through=through,model_id=s['model_id'],
                        signal_fair_edge=(c['direction']*(signal['fair_cents']-c['entry_price_cents'])-340)/100 if signal else None,
                        fill_fair_edge=(c['direction']*(fill_signal['fair_cents']-c['entry_price_cents'])-340)/100 if fill_signal else None,
                        signal_delta=signal.get('delta'),signal_move10=signal.get('move10_cents'),signal_move60=signal.get('move60_cents'))
                    rows.append(row)
                agg=summarize(rows);agg.update(status=s['status'],expected_pnl=s['pnl_cny'],date=date,code=code,case=key,through=through)
                assert abs(sum(x['net_cny'] for x in rows)-sum(c['net_cents'] for c in r['cycles'])/100)<1e-6
                accounts[s['model_id']]=agg
                totals[(date,key,through)].extend(rows)
                if key in FOCUS:all_rows[s['model_id']]=rows
            print('ATTRIBUTED',date,code,'unique cycles',len(m.cache),flush=True)
    daily={f'{date}_{key}_through{t}':summarize(rows) for (date,key,t),rows in totals.items()}
    write(OUT/'daily_attribution.json',daily);write(OUT/'accounts.json',accounts);write(OUT/'market_sessions.json',market)
    pack(OUT/'focus_cycles.json.gz',all_rows)
    # Grouping labels are retrospective diagnostics, never entry filters.
    groups={}
    for key in FOCUS:
        for t in (0,1):
            rows=[r for rs in all_rows.values() for r in rs if r['case']==key and r['through']==t]
            groups[f'{key}_through{t}']={}
            for name,func in [('session',lambda r:str(r['session'])),('date',lambda r:r['date']),
                ('outcome',lambda r:'win' if r['net_cny']>0 else 'loss'),('hold',lambda r:'<30s' if r['duration']<30 else '30-300s' if r['duration']<300 else '>=300s'),
                ('premium',lambda r:'<20' if r['entry_price']<20 else '20-25' if r['entry_price']<25 else '>=25')]:
                bucket=defaultdict(list)
                for row in rows:bucket[func(row)].append(row)
                groups[f'{key}_through{t}'][name]={label:summarize(rs) for label,rs in bucket.items()}
    write(OUT/'groups.json',groups)
    write(OUT/'plan.json',dict(scope='1184 frozen accounts price identity,12structural focus groups,ordinary/strict,4 historical dates; failed account closed cycles only, not full-day PnL',
        identity='net=advertised entry edge + pre-fill mid change + holding mid change + exit edge - fees',
        markout='1/10/30/60s future outcome descriptive ONLY; same session and <=2s quote age; not used to select trades',
        valuation='Entry observed midpoint implied vol and strictly earlier same-month future; freeze IV, split futures change then elapsed time; residual includes volatility/smile and quote microstructure, not pure IV causality',
        source_manifest=digest(BASE/'result_manifest.json'),sources={str(Path(__file__).relative_to(ROOT)):digest(Path(__file__))}))
    for p,h in read(BASE/'result_manifest.json').items():assert digest(ROOT/p)==h,p
    for key in ['v02_trend_long','fusion_throttle_long','v02_cancel_switch']:
        for date in DATES:
            d=daily[f'{date}_{key}_through0'];print(key,date,{k:round(d[k],2) if isinstance(d[k],float) else d[k] for k in ['cycles','net_cny','entry_edge','holding_mid_move','exit_edge','fees_cny','future_price_component','time_component','residual_component','missing_future','median_hold','active_close_net','after10_mean','after60_mean']},flush=True)


if __name__=='__main__':main()
