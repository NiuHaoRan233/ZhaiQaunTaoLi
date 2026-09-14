"""Source-time aligned IV calibration; available-time causal decisions and exits.

An option calibration waits until a future watermark reaches its market time,
then pairs only with a future observation whose SOURCE time is no later than it.
No future price is backfilled into an already made order decision.
"""
from collections import deque
from bisect import bisect_right
from math import expm1, floor, ceil
from . import gold_direction_research as g
from .gold_direction_timing_research import Account as Parent,ValueState as ParentValue

FAMILY='probe_gold_aligned_value_20260913_v4'
PROFILES={
    'aligned_long':dict(reference='mid',exit='parent'),
    'aligned_bid_band':dict(reference='bid',exit='parent'),
    'aligned_fast_lower':dict(reference='fast_lower',exit='parent'),
    'aligned_fixed_take':dict(reference='mid',exit='fixed'),
    'aligned_fair_take':dict(reference='mid',exit='fair'),
    'aligned_fast_fair_take':dict(reference='fast_lower',exit='fair'),
}


class ValueState(ParentValue):
    def __init__(self,strike,reference='mid'):
        super().__init__(strike);self.reference=reference;self.pending=deque();self.paired_futures=[]
        self.paired_times=[];self.sample_ts=None;self.fast_vol=None;self.bid_vol=None;self.calibrations=[]

    def new_session(self,session):
        if session!=self.session:
            super().new_session(session);self.pending.clear();self.paired_futures.clear();self.paired_times.clear()
            self.sample_ts=None;self.fast_vol=None;self.bid_vol=None

    def observe_option(self,e):
        self.new_session(e.session)
        if g.valid(e):self.pending.append(e)

    def on_future(self,f):
        super().on_future(f);self.paired_futures.append(f);self.paired_times.append(f.source_ts)
        while self.pending and self.pending[0].ts<=f.source_ts:
            e=self.pending.popleft();j=bisect_right(self.paired_times,e.ts)-1
            if j<0:continue
            pair=self.paired_futures[j]
            if not(0<=e.ts-pair.source_ts<=2000):continue
            raw=g.implied_vol((e.bid+e.ask)/200000,pair.mid,self.strike,self.maturity(e.ts))
            bid_raw=g.implied_vol(e.bid/100000,pair.mid,self.strike,self.maturity(e.ts))
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

    def enrich(self,feature,ts):
        if feature is None:return None
        chosen=self.vol if self.reference=='mid' else self.bid_vol if self.reference=='bid' else min(self.vol,self.fast_vol)
        fair,delta=g.black_call(self.future.mid,self.strike,self.maturity(ts),chosen)
        feature.update(fair_cents=fair*100000,delta=delta,vol=chosen,slow_mid_iv=self.vol,
            fast_mid_iv=self.fast_vol,lower_bid_iv=self.bid_vol,calibration_option_source_ts=self.sample_ts,
            calibration_future_source_ts=self.calibrations[-1]['future_source_ts'],reference_kind=self.reference)
        # Trend components are not used in these value-only long ablations.
        return feature

    def feature(self,ts,allow_same_future=False):
        return self.enrich(super().feature(ts,allow_same_future),ts)

    def cancellation_feature(self,ts):
        return self.enrich(super().cancellation_feature(ts),ts)


class Account(Parent):
    def __init__(self,code,profile,settlement,strike,tick=2000):
        if profile not in PROFILES:raise ValueError(profile)
        super().__init__(code,'fair_long',settlement,strike,tick)
        self.profile=profile;self.research_cfg=PROFILES[profile]
        self.value=ValueState(strike,self.research_cfg['reference'])
        self.model=f'{FAMILY}_{profile}_{settlement}_{code}_independent_250000'

    def issue(self,ts,side,price,reason,e,feature=None):
        exit_mode=self.research_cfg['exit']
        if self.inventory==1 and side=='sell' and reason in ('patient_exit','top_exit') and exit_mode!='parent':
            # Preserve the parent's release rules and first settle old eligible fills.
            if exit_mode=='fixed' and not self.released:
                c=self.cycle;target=c['entry_price_cents']+c['entry_spread']-2*self.tick
                price=min(price,target)
            elif exit_mode=='fair' and feature and feature['ready']:
                price=min(price,floor(feature['fair_cents']/self.tick)*self.tick)
            if not self.released:
                cost_floor=ceil((self.cycle['entry_price_cents']+2*g.FEE+self.tick)/self.tick)*self.tick
                price=max(price,cost_floor)
            # This remains passive: don't invent crossing fills in a passive limit.
            price=max(price,e.bid+self.tick)
            reason=f'{exit_mode}_target_'+('protected' if not self.released else 'released')
            if self.order and (self.order['side'],self.order['price'])==(side,price):return
        super().issue(ts,side,price,reason,e,feature)

    def result(self):
        r=super().result();r['calibrations']=self.value.calibrations
        r['summary'].update(research_profile=self.research_cfg,calibration_pairs=len(self.value.calibrations),
            source_aligned_calibration=True)
        return r
