"""Source-aligned Black call/put valuation; immutable call engines unchanged."""
from dataclasses import replace
from bisect import bisect_right
from math import expm1
from . import gold_direction_research as g
from .gold_history_validation import Clock as ParentClock, ValueState as ParentValue
from .commodity_dadao_research import load_frame


def black_option(f,k,t,vol,option_type):
    price,delta=g.black_call(f,k,t,vol)
    if option_type==0:return price,delta
    if option_type==1:return price-f+k,delta-1
    raise ValueError('Unknown option type')


def implied_vol(price,f,k,t,option_type):
    if option_type not in (0,1):raise ValueError('Unknown option type')
    return g.implied_vol(price if option_type==0 else price+f-k,f,k,t)


class Clock(ParentClock):
    def inputs(self,options,futures,code,detail):
        if detail['OptionType'] not in (0,1) or float(detail['OptUnit'])!=1000 or float(detail['PriceTick'])!=.02:
            raise ValueError('Gold call/put multiplier1000 tick0.02 required')
        options=options[(options.time>=self.start)&(options.time<self.start+21600000)]
        es,flags,meta=load_frame(options,code=code,date=self.date,detail=detail)
        es=[replace(e,ts=e.ts+self.shift,previous_ts=e.previous_ts+self.shift,quantity=min(e.quantity,1)) for e in es]
        fs=[]
        for x in self.frame(futures).sort_values('time',kind='stable').drop_duplicates('time',keep='last').itertuples():
            session=next((i for i,(lo,hi) in enumerate(zip(g.SESSION_STARTS,g.BOUNDARIES)) if lo<=x.time<hi),None)
            if session is None or not(0<x.bidPrice[0]<=x.askPrice[0] and min(x.bidVol[0],x.askVol[0])>0):continue
            fs.append(g.Future(int(x.time),int(x.time),session,(x.bidPrice[0]+x.askPrice[0])/2))
        return es,fs,flags,meta


class ValueState(ParentValue):
    def __init__(self,strike,expiry,clock,option_type):
        super().__init__(strike,expiry,clock)
        if option_type not in (0,1):raise ValueError('Unknown option type')
        self.option_type=option_type

    def on_future(self,f):
        g.ValueState.on_future(self,f)
        self.paired_futures.append(f);self.paired_times.append(f.source_ts)
        while self.pending and self.pending[0].ts<=f.source_ts:
            e=self.pending.popleft();j=bisect_right(self.paired_times,e.ts)-1
            if j<0:continue
            pair=self.paired_futures[j]
            if not 0<=e.ts-pair.source_ts<=2000:continue
            raw=implied_vol((e.bid+e.ask)/200000,pair.mid,self.strike,self.maturity(e.ts),self.option_type)
            bid_raw=implied_vol(e.bid/100000,pair.mid,self.strike,self.maturity(e.ts),self.option_type)
            if raw is None or bid_raw is None:continue
            if self.vol is None:
                self.vol=raw;self.fast_vol=raw;self.bid_vol=bid_raw;self.first=e.ts
            else:
                dt=e.ts-self.sample_ts;alpha=-expm1(-dt/60000);fast_alpha=-expm1(-dt/10000)
                self.vol+=alpha*(raw-self.vol);self.fast_vol+=fast_alpha*(raw-self.fast_vol)
                self.bid_vol+=alpha*(bid_raw-self.bid_vol)
            self.sample_ts=e.ts;self.vol_ts=f.ts;self.updates+=1
            self.calibrations.append(dict(option_source_ts=e.ts,future_source_ts=pair.source_ts,
                future_available_ts=pair.ts,calibration_available_ts=f.ts,raw_iv=raw,smoothed_iv=self.vol,
                fast_iv=self.fast_vol,bid_iv=self.bid_vol,session=e.session))

    def make_feature(self,ts,cancel=False):
        f=self.future
        if not(f and f.ts<=ts and (f.source_ts<=ts if cancel else f.source_ts<ts) and ts-f.source_ts<=2000 and self.vol is not None):return None
        if (self.vol_ts>ts if cancel else self.vol_ts>=ts) or ts-self.vol_ts>60000:return None
        vol=min(self.vol,self.fast_vol)
        price,delta=black_option(f.mid,self.strike,self.maturity(ts),vol,self.option_type)
        return dict(fair_cents=price*100000,delta=delta,vol=vol,future_mid=f.mid,
            future_source_ts=f.source_ts,future_available_ts=f.ts,vol_source_ts=self.vol_ts,
            ready=ts-self.first>=60000 and self.updates>=3,slow_mid_iv=self.vol,
            fast_mid_iv=self.fast_vol,lower_bid_iv=self.bid_vol,calibration_option_source_ts=self.sample_ts,
            calibration_future_source_ts=self.calibrations[-1]['future_source_ts'],reference_kind='fast_lower')


def timeline(es,fs,strike,expiry,clock,option_type,cut=None):
    value=ValueState(strike,expiry,clock,option_type)
    events=sorted([(e.ts,0,e) for e in es]+[(f.ts,1,f) for f in fs]+[(b,-1,b) for b in g.BOUNDARIES],key=lambda x:x[:2])
    rows=[]
    for ts,kind,event in events:
        if cut is not None and ts>cut:break
        if kind==-1:rows.append((kind,event,None,None,None));continue
        if kind==1:value.on_future(event)
        else:value.new_session(event.session)
        low=value.make_feature(ts,kind==1);mid=high=None
        if low:
            high=dict(low);mid=dict(low)
            for feat,vol,name in ((high,max(value.vol,value.fast_vol),'fast_upper'),(mid,value.vol,'mid')):
                fair,delta=black_option(value.future.mid,strike,value.maturity(ts),vol,option_type)
                feat.update(fair_cents=fair*100000,delta=delta,vol=vol,reference_kind=name)
            for feat in (low,high,mid):
                for seconds in (10,60):
                    old=next((x for x in reversed(value.history) if x.source_ts<=value.future.source_ts-seconds*1000),None)
                    feat[f'move{seconds}_cents']=feat['delta']*(value.future.mid-old.mid)*100000 if old and value.future.source_ts-seconds*1000-old.source_ts<=2000 else None
        rows.append((kind,event,{1:low,-1:high},mid,value.future))
        if kind==0:value.observe_option(event)
    return rows
