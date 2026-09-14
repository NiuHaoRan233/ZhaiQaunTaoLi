"""Exploratory liquidity screen. No orders, forecasts or simulated fills."""
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import argparse
import json
import math
import sys
import tomllib
import numpy as np
import pandas as pd
from probe_commodity_options import OPTION, FUTURE

BASE=Path(__file__).resolve().parent
DATA=BASE/'data'
REPORT=BASE/'reports'
EXCHANGES={'SHFE':'SF','CZCE':'ZF','DCE':'DF','GFEX':'GF','INE':'INE'}

def dump(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str,allow_nan=False),encoding='utf-8')

def daily_rows(inventory,daily):
    rows=[]
    for c,bars in daily['bars'].items():
        m=OPTION.fullmatch(c) or FUTURE.fullmatch(c)
        if not m: continue
        kind='option' if OPTION.fullmatch(c) else 'future'
        if kind=='future' and (not 1<=int(m[2][-2:])<=12 or (len(m[2])!=4 and not c.endswith('.ZF'))):continue
        b=pd.DataFrame(bars)
        # Zero-volume highs/lows can be carry-forward settlement placeholders.
        traded=b[(b.volume>0)&(b.low>0)&(b.high>=b.low)&(b.close>0)].copy()
        if traded.empty: continue
        ranges=(traded.high-traded.low)/traded.close*100
        d=inventory['details'].get(c) or {}
        last=b.iloc[-1]
        r=dict(code=c,kind=kind,product=m[1],market=c.split('.')[-1],name=d.get('ProductName',''),
            days=len(b),traded_days=len(traded),median_daily_volume=float(b.volume.median()),
            mean_daily_amount=float(b.amount.mean()),median_daily_amount=float(b.amount.median()),
            median_daily_range_pct=float(ranges.median()),worst_daily_range_pct=float(ranges.max()),
            last_close=float(last['close']),last_volume=float(last.volume),last_amount=float(last.amount),
            first_date=str(b.iloc[0]['index']),last_date=str(last['index']))
        rows.append(r)
    return rows

def select(rows):
    groups=defaultdict(list)
    for r in rows:
        if r['kind']=='option':groups[(r['market'],r['product'])].append(r)
    chosen={}
    for key,rs in groups.items():
        liquid=sorted(rs,key=lambda r:r['mean_daily_amount'],reverse=True)
        if liquid[0]['mean_daily_amount']>0:chosen[liquid[0]['code']]='品种成交额代表'
        viable=[r for r in rs if r['median_daily_volume']>=50 and r['median_daily_amount']>=20000 and r['traded_days']>=min(3,r['days'])]
        for r in sorted(viable,key=lambda r:(r['median_daily_range_pct'],-r['median_daily_amount']))[:2]:
            chosen[r['code']]='品种内较温和且有成交'
    viable=[r for r in rows if r['kind']=='option' and r['traded_days']>=3 and r['median_daily_volume']>=100 and r['median_daily_amount']>=100000]
    for r in sorted(viable,key=lambda r:(r['median_daily_range_pct'],-r['median_daily_amount']))[:40]:chosen[r['code']]='跨品种温和候选'
    for r in rows:
        if r['kind']=='option' and r['traded_days']>=3 and r['median_daily_volume']>=50 and r['median_daily_amount']>=20000 and r['median_daily_range_pct']<=12:
            chosen[r['code']]='近五日成交振幅较低扩展扫描'
    futures=[r for r in rows if r['kind']=='future' and r['traded_days']>=3 and r['median_daily_volume']>=1000 and r['median_daily_amount']>=10000000]
    future_products=set()
    for r in sorted(futures,key=lambda r:r['median_daily_range_pct']):
        if r['product'] in future_products:continue
        future_products.add(r['product']);chosen[r['code']]='商品期货温和对照'
        if len(future_products)>=12:break
    return chosen

def segments(date,bond=False,common=False):
    intervals=[('09:30','10:15'),('10:30','11:30'),('13:30','15:00')] if common else (
        [('09:30','11:30'),('13:00','15:30')] if bond else [('09:00','10:15'),('10:30','11:30'),('13:30','15:00')])
    return [(pd.Timestamp(f'{date} {a}',tz='Asia/Shanghai').timestamp(),pd.Timestamp(f'{date} {b}',tz='Asia/Shanghai').timestamp()) for a,b in intervals]

def weighted_quantile(x,w,q):
    order=np.argsort(x);x=x[order];w=w[order]
    return float(x[min(np.searchsorted(np.cumsum(w),sum(w)*q),len(x)-1)])

def measure(f,date,detail,bond=False,common=False,cap=60):
    spans=segments(date,bond,common);duration=sum(b-a for a,b in spans)
    f=f.sort_values('time',kind='stable').drop_duplicates('time',keep='last')
    t=f.time.to_numpy(dtype=float)/1000
    bid=np.array([v[0] for v in f.bidPrice],float);ask=np.array([v[0] for v in f.askPrice],float)
    bv=np.array([v[0] for v in f.bidVol],float);av=np.array([v[0] for v in f.askVol],float)
    mid=(bid+ask)/2;spread=ask-bid
    valid=(bid>0)&(ask>bid)&(bv>0)&(av>0)&np.isfinite(mid)
    w=np.zeros(len(f));session=np.full(len(f),-1)
    for si,(a,b) in enumerate(spans):
        mask=(t>=a)&(t<b);ii=np.flatnonzero(mask);session[ii]=si
        if len(ii):
            end=np.minimum(np.r_[t[ii[1:]],b],b)
            w[ii]=np.minimum(np.maximum(end-t[ii],0),cap)
    w[~valid]=0;v=w>0
    if not v.any():return dict(rows=len(f),valid_coverage_pct=0)
    rel=np.divide(spread,mid,out=np.zeros(len(f)),where=mid>0)*100
    total=sum(w);meanmid=float(np.average(mid[v],weights=w[v]))
    unit=10 if bond else (detail.get('OptUnit') or detail.get('VolumeMultiple') or 1)
    tick=detail.get('PriceTick') or 0
    # Sample observed 60s quote movements only with valid, reasonably fresh endpoints.
    j=np.searchsorted(t,t+60);j=np.minimum(j,len(t)-1)
    good=v&v[j]&(session==session[j])&(t[j]-t>=50)&(t[j]-t<=70)
    drift=np.abs(mid[j[good]]-mid[good]);norm=drift/spread[good]
    # Cumulative increment events, not exchange trade count or classified fills.
    vol=f.volume.to_numpy(float);amt=f.amount.to_numpy(float)
    dv=np.r_[0,np.diff(vol)];da=np.r_[0,np.diff(amt)]
    adjacent=(session>=0)&(session==np.r_[-1,session[:-1]])
    events=adjacent&(dv>0);bins=set()
    wide_events=0
    last=f.lastPrice.to_numpy(float)
    high_events=low_events=0
    for i in np.flatnonzero(events):
        bins.add((int(session[i]),int((t[i]-spans[session[i]][0])//300)))
        if i>0 and valid[i-1] and t[i]-t[i-1]<=cap:
            if rel[i-1]>=.5:wide_events+=1
            if last[i]>=ask[i-1]:high_events+=1
            if last[i]<=bid[i-1]:low_events+=1
    result=dict(rows=len(f),session_seconds=duration,stale_cap_seconds=cap,
        valid_coverage_pct=total/duration*100,valid_minutes=total/60,
        mean_mid=meanmid,mean_spread=float(np.average(spread[v],weights=w[v])),
        mean_relative_spread_pct=float(np.average(rel[v],weights=w[v])),
        median_relative_spread_pct=weighted_quantile(rel[v],w[v],.5),
        spread_ge_05_minutes=float(sum(w[v&(rel>=.5)])/60),
        spread_ge_1_minutes=float(sum(w[v&(rel>=1)])/60),
        spread_ge_05_valid_pct=float(sum(w[v&(rel>=.5)])/total*100),
        mean_spread_cash_per_lot=float(np.average(spread[v],weights=w[v])*unit),
        improved_both_gross_cash_per_lot=float(np.average(spread[v]-2*tick,weights=w[v])*unit),
        nonpositive_after_two_ticks_pct=float(sum(w[v&(spread<=2*tick)])/total*100),
        mid_p95_p05_pct=(weighted_quantile(mid[v],w[v],.95)-weighted_quantile(mid[v],w[v],.05))/meanmid*100,
        median_min_depth_lots=weighted_quantile(np.minimum(bv[v],av[v]),w[v],.5),
        incremental_volume=float(sum(dv[events])),incremental_amount=float(sum(np.maximum(da[events],0))),
        volume_increment_events=int(sum(events)),active_5min_bins=len(bins),
        total_5min_bins=int(duration/300),wide_prior_quote_events=wide_events,
        last_trade_near_prior_ask_events=high_events,last_trade_near_prior_bid_events=low_events,
        drift60_samples=int(sum(good)),
        drift60_median_spreads=float(np.median(norm)) if len(norm) else None,
        drift60_p90_spreads=float(np.quantile(norm,.9)) if len(norm) else None,
        drift60_p90_pct=float(np.quantile(drift, .9)/meanmid*100) if len(drift) else None)
    return result

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser();p.add_argument('--dates',nargs='+',default=['20260911']);p.add_argument('--codes',nargs='*');p.add_argument('--select-only',action='store_true');args=p.parse_args()
    inventory=json.loads(sorted(DATA.glob('commodity_inventory_*.json'))[-1].read_text(encoding='utf-8'))
    daily=json.loads((DATA/'daily_20260907_20260911.json').read_text(encoding='utf-8'))
    rows=daily_rows(inventory,daily);chosen=select(rows)
    dump(REPORT/'daily_screen.json',dict(rows=rows,selected=chosen,requested=len(daily['completed']),received=len(daily['bars']),errors=daily['errors']))
    print('daily universe',len(rows),'selected',len(chosen),flush=True)
    if args.select_only:return
    from xtquant import xtdata
    xtdata.enable_hello=False
    xtdata.connect(port=tomllib.loads((BASE.parent/'config.toml').read_text(encoding='utf-8-sig'))['qmt']['port'])
    codes=args.codes or sorted(chosen)+['132024.SH','132026.SH']
    target=REPORT/'intraday_screen.json'
    state=json.loads(target.read_text(encoding='utf-8')) if target.exists() else dict(source='MiniQMT xtdata',rows={},errors=[])
    for num,c in enumerate(codes):
        detail=inventory['details'].get(c) or xtdata.get_instrument_detail(c,True) or {}
        for date in args.dates:
            key=c+'_'+date
            if key in state['rows']:continue
            print(num+1,'/',len(codes),key,flush=True)
            try:
                path=DATA/f'{key}_tick.pkl'
                if path.exists():f=pd.read_pickle(path)
                else:
                    xtdata.download_history_data(c,'tick',date+'090000',date+'153000')
                    f=xtdata.get_market_data_ex([], [c],period='tick',start_time=date+'090000',end_time=date+'153000',fill_data=False).get(c)
                    if f is None or f.empty:raise ValueError('no historical tick data')
                    f.to_pickle(path)
                actual=pd.to_datetime(f.time,unit='ms',utc=True).dt.tz_convert('Asia/Shanghai').dt.strftime('%Y%m%d')
                if not (actual==date).all():raise ValueError('unexpected calendar date')
                bond=c.endswith('.SH')
                state['rows'][key]=dict(code=c,date=date,detail=detail,selection=chosen.get(c,'explicit/benchmark'),
                    metrics=measure(f,date,detail,bond),sensitivity_300=measure(f,date,detail,bond,cap=300),
                    common=measure(f,date,detail,bond,common=True))
            except Exception as e:
                state['errors'].append(dict(code=c,date=date,error=str(e)))
                print('ERROR',c,str(e),flush=True)
            dump(target,state)
    print('DONE',len(state['rows']),flush=True)

if __name__=='__main__':main()
