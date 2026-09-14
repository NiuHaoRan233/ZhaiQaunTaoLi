"""Frozen latest-day gold state screening and existing maker-strategy replay.

No broker calls. Selection observes the first30 minutes only; execution starts later.
Current catalogue supplies contract terms, never current quote ranks or prices.
"""
from datetime import datetime
import math
import numpy as np
from .commodity_mau_transfer_research import TransferAccount

FAMILY='probe_gold_state_20260913_v1'
DATE='20260911'
START=1789088400000
CUTOFF=START+1800000
END=START+21600000
CRITERIA=dict(top_future_months=2,minimum_days_to_expiry=7,maximum_days_to_expiry=120,
    maximum_absolute_log_moneyness=.08,minimum_premium=2.,maximum_premium=100.,
    minimum_valid_time_fraction=.8,minimum_bid2_time_fraction=.8,
    minimum_net_edge_time_fraction=.5,maximum_median_relative_spread_pct=5.,
    minimum_volume_increment=10,minimum_trade_update_frames=10,minimum_each_side_updates=3,
    selected_count=2,initial_cash_per_contract_cny=150000,fee_cny=1.7,delay_ms=0,
    rank='min(strict_buy_updates,strict_sell_updates) descending; executable net-edge time fraction descending; median net edge / median premium descending; code ascending')


def prefix_frame(frame):
    return frame[(frame.time>=START)&(frame.time<CUTOFF)].sort_values('time',kind='stable').drop_duplicates('time',keep='last')


def future_state(frame):
    f=prefix_frame(frame)
    if f.empty:return None
    # At the selection boundary use only an actually observed recent quote.
    r=f.iloc[-1];b,a=r.bidPrice[0],r.askPrice[0]
    if not(0<b<=a) or CUTOFF-int(r.time)>60000:return None
    return dict(ts=int(r.time),mid=(b+a)/2,open_interest=float(r.openInt),
        observed_volume_increment=max(0,int(r.volume-f.iloc[0].volume)))


def state_metrics(frame,detail,underlying):
    f=prefix_frame(frame)
    if len(f)<2:return dict(eligible=False,reasons=['insufficient_prefix_data'])
    ts=f.time.to_numpy(dtype=np.int64);bid=np.array([x[0] for x in f.bidPrice]);ask=np.array([x[0] for x in f.askPrice])
    bq=np.array([x[0] for x in f.bidVol]);aq=np.array([x[0] for x in f.askVol]);mid=(bid+ask)/2
    valid=(bid>0)&(bid<ask)&(bq>0)&(aq>0)
    dv=np.diff(f.volume.to_numpy(),prepend=f.volume.iloc[0]);da=np.diff(f.amount.to_numpy(),prepend=f.amount.iloc[0])
    dt=np.diff(ts,append=min(CUTOFF,int(ts[-1])));weight=dt.astype(float)
    weight[(dt<=0)|(dt>60000)]=0
    weight[np.r_[dv[1:]<0,False]]=0
    w=weight*valid;covered=float(w.sum())
    if not covered:return dict(eligible=False,reasons=['no_valid_weight'])
    depth=np.array([any(p>0 and q>0 and p<b for p,q in zip(ps,qs)) for ps,qs,b in zip(f.bidPrice,f.bidVol,bid)])
    unit=float(detail['OptUnit']);tick=float(detail['PriceTick']);spread=ask-bid
    edge=spread*unit-2*tick*unit-3.4
    ratio=np.divide(spread,mid,out=np.zeros_like(spread),where=mid>0)*100
    def med(values):
        idx=np.argsort(values);return float(values[idx][np.searchsorted(np.cumsum(w[idx]),covered/2)])
    elapsed=np.diff(ts,prepend=ts[0]);last=f.lastPrice.to_numpy()
    evidence=(dv>0)&(da>0)&(elapsed>0)&(elapsed<=60000)&np.r_[False,valid[:-1]]&(last>0)
    buys=evidence&(last>=np.r_[ask[0],ask[:-1]])
    sells=evidence&(last<=np.r_[bid[0],bid[:-1]])
    strike=float(detail['OptExercisePrice']);signed=math.log(underlying['mid']/strike)
    is_call=detail['OptionType']==0
    itm=signed if is_call else -signed
    expiry=datetime.strptime(detail['ExpireDate'],'%Y%m%d');days=(expiry-datetime.strptime(DATE,'%Y%m%d')).days
    last_mid=float(mid[-1]) if valid[-1] else None
    m=dict(eligible=True,reasons=[],valid_time_fraction=covered/1800000,
        bid2_time_fraction=float(w[depth].sum()/covered),
        net_edge_time_fraction=float(w[edge>=max(10,tick*unit)-1e-7].sum()/covered),
        median_premium=med(mid),median_spread=med(spread),median_relative_spread_pct=med(ratio),
        median_net_edge_cny=med(edge),volume_increment=int(np.maximum(dv,0).sum()),
        volume_resets=int((dv<0).sum()),trade_update_frames=int((dv>0).sum()),
        strict_buy_updates=int(buys.sum()),strict_sell_updates=int(sells.sum()),
        days_to_expiry=days,underlying_mid=underlying['mid'],underlying_ts=underlying['ts'],
        option_last_ts=int(ts[-1]),option_last_mid=last_mid,strike=strike,option_type='call' if is_call else 'put',
        log_moneyness=itm,moneyness_state='near_money' if abs(itm)<=.01 else ('in_money' if itm>0 else 'out_of_money'))
    checks=[('short_or_long_expiry',not(CRITERIA['minimum_days_to_expiry']<=days<=CRITERIA['maximum_days_to_expiry'])),
        ('deep_moneyness',abs(signed)>CRITERIA['maximum_absolute_log_moneyness']),
        ('premium_outside_band',not(CRITERIA['minimum_premium']<=m['median_premium']<=CRITERIA['maximum_premium'])),
        ('quote_coverage',m['valid_time_fraction']<CRITERIA['minimum_valid_time_fraction']),
        ('bid2_coverage',m['bid2_time_fraction']<CRITERIA['minimum_bid2_time_fraction']),
        ('few_net_edge_intervals',m['net_edge_time_fraction']<CRITERIA['minimum_net_edge_time_fraction']),
        ('excessive_relative_spread',m['median_relative_spread_pct']>CRITERIA['maximum_median_relative_spread_pct']),
        ('few_contracts_traded',m['volume_increment']<CRITERIA['minimum_volume_increment']),
        ('few_trade_updates',m['trade_update_frames']<CRITERIA['minimum_trade_update_frames']),
        ('insufficient_two_sided_flow',min(m['strict_buy_updates'],m['strict_sell_updates'])<CRITERIA['minimum_each_side_updates']),
        ('volume_reset',m['volume_resets']>0),
        ('no_recent_option_quote',CUTOFF-int(ts[-1])>60000 or last_mid is None)]
    m['reasons']=[reason for reason,failed in checks if failed];m['eligible']=not m['reasons']
    return m


def rank_key(item):
    code,m=item
    return (-min(m['strict_buy_updates'],m['strict_sell_updates']),-m['net_edge_time_fraction'],
            -m['median_net_edge_cny']/(m['median_premium']*1000),code)


class GoldAccount(TransferAccount):
    def __init__(self,code,profile,tick_cents):
        super().__init__(code,profile,CRITERIA['initial_cash_per_contract_cny']*100,tick_cents)
        self.model=f'{FAMILY}_{profile}_{code}_independent_150000_cost_close'
        self.s['model_id']=self.model

    def step(self,e,date,entry_cash_limit=None):
        if date!=DATE or not CUTOFF<=e.ts<END:
            raise ValueError('Execution must follow the frozen selection window')
        return super().step(e,date,entry_cash_limit)
