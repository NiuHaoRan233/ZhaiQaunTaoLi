"""Causal percentage-state selection and new contracts; offline calls AND puts."""
from dataclasses import replace
import numpy as np
from .gold_history_validation import Clock as ParentClock
from . import gold_relative_spread_research as parent

FAMILY='probe_gold_reselection_20260914_v1'
EXCLUDED=('au2610C960.SF','au2610C952.SF')
THRESHOLDS=(100,150,200)


def metrics(frame, detail, underlying, clock, bps):
    # Parent state measures already truncate strictly before09:30.
    base=clock.metrics(frame,detail,underlying)
    f=frame[(frame.time>=clock.start)&(frame.time<clock.start+1800000)].sort_values('time',kind='stable').drop_duplicates('time',keep='last')
    if len(f)<2:return dict(eligible=False,reasons=['insufficient_prefix_data'])
    bid=np.rint(np.array([x[0] for x in f.bidPrice])*100000).astype(np.int64)
    ask=np.rint(np.array([x[0] for x in f.askPrice])*100000).astype(np.int64)
    valid=(bid>0)&(ask>bid)&(np.array([x[0] for x in f.bidVol])>0)&(np.array([x[0] for x in f.askVol])>0)
    ts=f.time.to_numpy(dtype=np.int64);dt=np.diff(ts,append=clock.start+1800000)
    w=np.minimum(np.maximum(dt,0),60000)*valid
    ok=valid&(2*(ask-bid)*10000>=bps*(ask+bid))
    net=ask-bid-4000-340  # cents per contract, two improvements and two fees
    usable=ok&(net>0)
    covered=float(w.sum());fraction=float(w[ok].sum()/covered) if covered else 0
    net_fraction=float(w[usable].sum()/covered) if covered else 0
    dv=np.diff(f.volume.to_numpy(),prepend=f.volume.iloc[0]);da=np.diff(f.amount.to_numpy(),prepend=f.amount.iloc[0])
    delta=np.diff(ts,prepend=ts[0]);last=np.rint(f.lastPrice.to_numpy()*100000).astype(np.int64)
    evidence=(dv>0)&(da>0)&(delta>0)&(delta<=60000)&np.r_[False,usable[:-1]]
    buy=int((evidence&(last>=np.r_[ask[0],ask[:-1]])).sum())
    sell=int((evidence&(last<=np.r_[bid[0],bid[:-1]])).sum())
    reasons=[r for r in base.get('reasons',[]) if r!='few_net_edge_intervals']
    if fraction<.5:reasons.append('relative_spread_less_than_half_time')
    if net_fraction<.5:reasons.append('positive_net_space_less_than_half_time')
    if min(buy,sell)<3:reasons.append('few_two_sided_eligible_updates')
    return dict(base,eligible=not reasons,reasons=reasons,bps=bps,relative_time_fraction=fraction,
        positive_net_relative_time_fraction=net_fraction,relative_buy_updates=buy,relative_sell_updates=sell,
        balanced_relative_updates=min(buy,sell),mean_net_space_cny=float(np.average(net[usable]/100,weights=w[usable])) if w[usable].sum()>0 else None)


def select(rows):
    return [c for c,m in sorted(rows.items(),key=lambda x:(-x[1].get('balanced_relative_updates',0),
        -x[1].get('positive_net_relative_time_fraction',0),x[0])) if c not in EXCLUDED and m['eligible']][:2]


class Account(parent.Account):
    def __init__(self,code,policy,strike,direction,bps,option_type,through=False,capital=250000):
        if code in EXCLUDED:raise ValueError('User excluded old contracts')
        if option_type not in (0,1):raise ValueError('Unknown option type')
        super().__init__(code,policy,strike,direction,bps,through,capital)
        self.option_type=option_type
        self.model=self.model.replace(parent.FAMILY,FAMILY)

    def result(self):
        r=super().result();r['summary'].update(option_type='call' if self.option_type==0 else 'put',
            selection_family=FAMILY,prototype_family=parent.FAMILY)
        return r
