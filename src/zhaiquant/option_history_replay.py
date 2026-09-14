"""Date-general local option input adapter, preserving the frozen one-day semantics."""
import numpy as np
import pandas as pd
from .option_top_cycle_research import Event


def load_day(frame, date, details):
    f=frame.sort_values('time',kind='stable').drop_duplicates('time',keep='last')
    unit=int(details.get('OptUnit') or details['VolumeMultiple']);tick=float(details['PriceTick'])
    assert unit==10000 and tick==.0001
    times=f.time.to_numpy(dtype=np.int64)
    dt=pd.to_datetime(times,unit='ms',utc=True).tz_convert('Asia/Shanghai')
    assert set(dt.strftime('%Y%m%d'))=={date}
    mins=np.asarray(dt.hour*60+dt.minute+dt.second/60+dt.microsecond/60000000)
    sessions=np.where((mins>=570)&(mins<690),0,np.where((mins>=780)&(mins<897),1,-1))
    prices=lambda values:tuple(int(round(float(p)*unit*100)) for p in values)
    events=[];previous=None;resets=0
    for i,r in enumerate(f.itertuples()):
        bid,ask=prices(r.bidPrice),prices(r.askPrice)
        last=int(round(r.lastPrice*unit*100));qty=tx=0;single=False;side=strict='unknown'
        if previous is not None:
            pr,pb,pa,pl,ps=previous
            dv=int(r.volume-pr.volume);tx=int(r.transactionNum-pr.transactionNum);da=float(r.amount-pr.amount)
            if dv<0 or tx<0 or da<-.02:resets+=1
            elif dv>0 and sessions[i]>=0 and sessions[i]==ps:
                qty=dv
                if pa[0]>0 and last>=pa[0]:side='buy'
                elif pb[0]>0 and last<=pb[0]:side='sell'
                elif last>pl:side='buy'
                elif last<pl:side='sell'
                single=tx==1 and abs(da*100-last*qty)<=2.01
                if single and 0<pb[0]<pa[0] and pr.bidVol[0]>0 and pr.askVol[0]>0 and r.time-pr.time<=2000:
                    if last>=pa[0]:strict='buy'
                    elif last<=pb[0]:strict='sell'
        if sessions[i]>=0:
            events.append(Event(int(r.time),int(previous[0].time) if previous else int(r.time),int(sessions[i]),
                bid[0],ask[0],int(r.bidVol[0]),int(r.askVol[0]),
                tuple((p,int(q)) for p,q in zip(bid,r.bidVol) if p>0 and q>0),
                tuple((p,int(q)) for p,q in zip(ask,r.askVol) if p>0 and q>0),
                last,qty,max(0,tx),side,single,strict))
        previous=(r,bid,ask,last,int(sessions[i]))
    return events,dict(rows=len(f),duplicate_times_removed=len(frame)-len(f),continuous_frames=len(events),
        cumulative_resets=resets,final_tick_volume=int(f.iloc[-1].volume),final_tick_amount=float(f.iloc[-1].amount),
        final_tick_transactions=int(f.iloc[-1].transactionNum),first_tick_ts=int(f.iloc[0].time),last_tick_ts=int(f.iloc[-1].time))


def coverage(events,date):
    base=pd.Timestamp(date,tz='Asia/Shanghai').value//10**6
    windows=[(base+570*60000,base+690*60000),(base+780*60000,base+897*60000)]
    ms=spread_sum=relative_sum=affordable_ms=0
    intervals=[];first_mid=last_mid=None;session_covers=[];session_edge_gaps=[]
    for session,(start,end) in enumerate(windows):
        rows=[e for e in events if e.session==session]
        span=0
        for i,e in enumerate(rows):
            if not (0<e.bid<e.ask and e.bid_qty>0 and e.ask_qty>0):continue
            mid=(e.bid+e.ask)/2
            if first_mid is None:first_mid=mid
            last_mid=mid
            duration=max(0,min(end,rows[i+1].ts if i+1<len(rows) else end,e.ts+30000)-max(start,e.ts))
            ms+=duration;span+=duration;spread_sum+=(e.ask-e.bid)*duration;relative_sum+=(e.ask-e.bid)/mid*duration
            price=e.bid+(100 if e.ask-e.bid>100 else 0)
            if price+170<=1_000_000:affordable_ms+=duration
            intervals.append((e.ask-e.bid,duration))
        session_covers.append(span/(end-start))
        session_edge_gaps.append(dict(start_gap_ms=rows[0].ts-start if rows else None,end_gap_ms=end-rows[-1].ts if rows else None))
    median=None
    if ms:
        cumulative=0
        for spread,duration in sorted(intervals):
            cumulative+=duration
            if cumulative>=ms/2:median=spread/100;break
    return dict(valid_book_coverage=ms/(237*60000),session_coverage=session_covers,session_edge_gaps=session_edge_gaps,
        time_weighted_spread_cny=spread_sum/ms/100 if ms else None,time_weighted_spread_percent=relative_sum/ms*100 if ms else None,
        weighted_median_spread_cny=median,initial_cash_affordable_quote_share=affordable_ms/ms if ms else None,
        first_mid_cents=first_mid,last_mid_cents=last_mid,
        day_mid_change_cny=(last_mid-first_mid)/100 if first_mid is not None else None,
        day_mid_change_percent=(last_mid/first_mid-1)*100 if first_mid else None)
