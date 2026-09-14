"""Causal, offline one-contract gold-option long/short and futures-value research.

Prices, cash and PnL are integer cents PER CONTRACT. Futures are quoted CNY/g.
No market or broker API. Immutable IDs separate settlement and model variants.
"""
from collections import deque, Counter
from dataclasses import dataclass
from math import erf, exp, expm1, log, sqrt, ceil, floor
from .commodity_flow_strategy import FEE
from .gold_state_research import START, CUTOFF, END, DATE

FAMILY='probe_gold_direction_20260913_v1'
CAPITAL=25_000_000
BOUNDARIES=(START+75*60000,START+150*60000,END)
SESSION_STARTS=(START,START+90*60000,START+270*60000)
PROFILES={
    'long':dict(direction='long',value=False,trend=False,proactive=False,patient=True),
    'short':dict(direction='short',value=False,trend=False,proactive=False,patient=True),
    'long_trend':dict(direction='long',value=False,trend=True,proactive=False,patient=True),
    'short_trend':dict(direction='short',value=False,trend=True,proactive=False,patient=True),
    'fair_long':dict(direction='long',value=True,trend=False,proactive=False,patient=True),
    'fair_short':dict(direction='short',value=True,trend=False,proactive=False,patient=True),
    'fair_switch':dict(direction='fair',value=True,trend=False,proactive=False,patient=True),
    'fair_trend_switch':dict(direction='fair',value=True,trend=True,proactive=False,patient=True),
    'fair_risk_switch':dict(direction='fair',value=True,trend=True,proactive=True,patient=True),
    'fair_top_switch':dict(direction='fair',value=True,trend=True,proactive=True,patient=False),
}


def normal(x):return (1+erf(x/sqrt(2)))/2


def black_call(f,k,t,vol):
    if min(f,k,t,vol)<=0:return max(f-k,0.),float(f>k)
    v=vol*sqrt(t);d1=log(f/k)/v+v/2;d2=d1-v
    return f*normal(d1)-k*normal(d2),normal(d1)


def implied_vol(price,f,k,t):
    if not max(f-k,0)<price<f:return None
    lo,hi=.005,3.
    if not black_call(f,k,t,lo)[0]<=price<=black_call(f,k,t,hi)[0]:return None
    for _ in range(36):
        mid=(lo+hi)/2
        if black_call(f,k,t,mid)[0]<price:lo=mid
        else:hi=mid
    return (lo+hi)/2


@dataclass(frozen=True)
class Future:
    ts:int
    source_ts:int
    session:int
    mid:float


class ValueState:
    """Value before assimilating the current option book; stale sources rejected."""
    def __init__(self,strike):
        self.strike=strike;self.future=None;self.history=deque();self.session=None
        self.vol=None;self.vol_ts=None;self.first=None;self.updates=0

    def new_session(self,session):
        if session!=self.session:
            self.session=session;self.history.clear();self.future=None
            self.vol=None;self.vol_ts=None;self.first=None;self.updates=0

    def on_future(self,f):
        self.new_session(f.session)
        if self.future and f.ts<=self.future.ts:raise ValueError('Unordered future')
        self.future=f;self.history.append(f)
        while self.history and self.history[0].ts<f.ts-61000:self.history.popleft()

    def maturity(self,ts):return max(1/365,(END+12*86400000-ts)/(365*86400000))

    def observe_option(self,e):
        self.new_session(e.session)
        f=self.future
        if not(f and f.source_ts<e.ts and e.ts-f.source_ts<=2000 and valid(e)):return
        vol=implied_vol((e.bid+e.ask)/200000,f.mid,self.strike,self.maturity(e.ts))
        if vol is None:return
        if self.vol is None:self.vol=vol;self.first=e.ts
        else:self.vol+=-expm1(-(e.ts-self.vol_ts)/60000)*(vol-self.vol)
        self.vol_ts=e.ts;self.updates+=1

    def feature(self,ts,allow_same_future=False):
        f=self.future
        if not(f and f.ts<=ts and (f.source_ts<ts or allow_same_future and f.source_ts==ts)
               and ts-f.source_ts<=2000 and self.vol is not None):return None
        if self.vol_ts>=ts or ts-self.vol_ts>60000:return None
        price,delta=black_call(f.mid,self.strike,self.maturity(ts),self.vol)
        moves={}
        for seconds in (10,60):
            old=next((x for x in reversed(self.history) if x.ts<=ts-seconds*1000),None)
            moves[f'move{seconds}_cents']=delta*(f.mid-old.mid)*100000 if old and ts-seconds*1000-old.ts<=2000 else None
        return dict(fair_cents=price*100000,delta=delta,vol=self.vol,future_mid=f.mid,
            future_source_ts=f.source_ts,future_available_ts=f.ts,vol_source_ts=self.vol_ts,
            ready=ts-self.first>=60000 and self.updates>=3,**moves)


def valid(e):return 0<e.bid<e.ask and min(e.bid_qty,e.ask_qty)>0


def candidate(e,direction,tick,feature,cfg,flow):
    price=e.bid+tick if direction==1 else e.ask-tick
    if e.ask-e.bid-2*tick-2*FEE<max(tick,1000):return None,'net_edge'
    if set(side for _,side in flow)!={'buy','sell'}:return None,'two_sided_flow'
    depth=e.bids if direction==1 else e.asks;top=e.bid if direction==1 else e.ask
    second=next((p for p,q in depth if q>0 and direction*(top-p)>0),None)
    if second is None:return None,'missing_second_depth'
    if abs(top-second)>=max(2*tick,e.ask-e.bid):return None,'isolated_top'
    if cfg['value'] or cfg['trend']:
        if not(feature and feature['ready']):return None,'value_warmup_or_stale'
    edge=direction*(feature['fair_cents']-price)-2*FEE if feature else None
    if cfg['value'] and edge<max(tick,(e.ask-e.bid)/4):return None,'fair_edge'
    if cfg['trend']:
        for seconds in (10,60):
            move=feature[f'move{seconds}_cents']
            if move is None:return None,'trend_warmup'
            if direction*move < -max(2*tick,(e.ask-e.bid)/2):return None,f'adverse_future_{seconds}s'
    return dict(direction=direction,price=price,edge_cents=edge),'eligible'


class Account:
    def __init__(self,code,profile,settlement,strike,tick=2000):
        if profile not in PROFILES or settlement not in ('cost','market'):raise ValueError('Unknown model')
        self.code=code;self.profile=profile;self.cfg=PROFILES[profile];self.settlement=settlement;self.tick=tick
        self.model=f'{FAMILY}_{profile}_{settlement}_{code}_independent_250000'
        self.value=ValueState(strike);self.cash=CAPITAL;self.inventory=0;self.cycle=None;self.order=None
        self.order_count=0;self.fees=0;self.gross=0;self.completed=0;self.session=None;self.last=None
        self.flow=deque();self.last_book=None;self.released=False;self.risk_since=None;self.peak=CAPITAL;self.dd=0
        self.max_reserve=0;self.margin_breaches=0;self.rejects=Counter();self.checked=set();self.rows={k:[] for k in ('orders','fills','cycles','cancels','curve','boundaries')}

    def cancel(self,ts,reason):
        if self.order:
            self.rows['cancels'].append(dict(model_id=self.model,code=self.code,order_id=self.order['id'],ts=ts,reason=reason))
            self.order=None

    def on_future(self,f):
        self.value.on_future(f)
        if not(self.cfg['proactive'] and self.order and not self.inventory and self.last_book):return
        o=self.order
        if o.get('cancel_ts') is not None:return
        feat=self.value.feature(f.ts,allow_same_future=True)
        c,reason=candidate(self.last_book,1 if o['side']=='buy' else -1,self.tick,feat,self.cfg,self.flow)
        # Re-evaluate the outstanding limit itself, not a newly improved price.
        d=1 if o['side']=='buy' else -1
        edge=d*(feat['fair_cents']-o['price'])-2*FEE if feat else None
        if c is None or edge is None or edge<max(self.tick,(self.last_book.ask-self.last_book.bid)/4):
            o['cancel_ts']=f.ts
            self.rows['cancels'].append(dict(model_id=self.model,code=self.code,order_id=o['id'],ts=f.ts,
                reason='future_risk_'+reason,conservative_interval_settlement=True))

    def mark(self,ts):
        b=self.last_book;mark=(b.bid if self.inventory==1 else b.ask) if b and self.inventory else 0
        pnl=self.cash+self.inventory*mark-CAPITAL
        self.peak=max(self.peak,CAPITAL+pnl);self.dd=max(self.dd,self.peak-CAPITAL-pnl)
        point=[ts,pnl,self.inventory]
        if self.rows['curve'] and self.rows['curve'][-1][0]==ts:self.rows['curve'][-1]=point
        elif not self.rows['curve'] or self.rows['curve'][-1][1:]!=point[1:]:self.rows['curve'].append(point)
        assert pnl==self.gross-self.fees+(self.inventory*(mark-self.cycle['entry_price_cents']) if self.cycle else 0)
        assert bool(self.inventory)==bool(self.cycle) and self.inventory in (-1,0,1)

    def fill(self,ts,price,side,kind,e=None,feature=None):
        o=self.order;direction=1 if side=='buy' else -1;closing=bool(self.inventory)
        if closing and direction==self.inventory:raise ValueError('Cannot pyramid')
        self.cash-=direction*price+FEE;self.fees+=FEE
        if closing:
            c=self.cycle;gross=self.inventory*(price-c['entry_price_cents'])
            closed=dict(c,exit_ts=ts,exit_price_cents=price,gross_cents=gross,fees_cents=2*FEE,
                net_cents=gross-2*FEE,exit_kind=kind,duration_seconds=(ts-c['entry_ts'])/1000)
            if kind=='virtual_cost_close':
                b=self.last_book;mark=b.bid if self.inventory==1 else b.ask
                closed.update(quote_mark_gross_before_close_cents=self.inventory*(mark-c['entry_price_cents']),
                    reference_quote_ts=b.ts,reference_quote_age_seconds=(ts-b.ts)/1000)
            self.rows['cycles'].append(closed);self.gross+=gross;self.completed+=gross-2*FEE
            self.inventory=0;self.cycle=None
        else:
            self.inventory=direction
            self.cycle=dict(model_id=self.model,code=self.code,entry_ts=ts,entry_price_cents=price,
                direction=direction,entry_mid_twice=o['entry_mid_twice'],entry_spread=o['entry_spread'],
                entry_signal=o.get('feature'),entry_order_ts=o['created_ts'],entry_fill_future=feature)
            self.released=False;self.risk_since=None
        f=dict(model_id=self.model,code=self.code,ts=ts,order_id=o['id'],side=side,price_cents=price,
            fee_cents=FEE,quantity=1,inventory=self.inventory,cash_cents=self.cash,kind=kind,
            created_ts=o['created_ts'],active_ts=o['active_ts'],closing=closing)
        if kind=='passive':
            f.update(source_previous_ts=e.previous_ts,source_last_cents=e.last,source_quantity=min(1,e.quantity),
                source_strict_side=e.strict_side,source_single=e.single,
                cancellation_inside_aggregate_interval=bool(o.get('cancel_ts') is not None and e.previous_ts<o['cancel_ts']<=ts),
                cancel_ts=o.get('cancel_ts'))
        elif kind=='pre_break_market_close':
            f.update(source_quote_ts=e.ts,source_bid=e.bid,source_ask=e.ask,
                source_qty=e.bid_qty if side=='sell' else e.ask_qty)
        self.rows['fills'].append(f);self.order=None
        assert self.cash>=0

    def issue(self,ts,side,price,reason,e,feature=None):
        self.cancel(ts,'reprice');self.order_count+=1
        o=dict(id=self.order_count,model_id=self.model,code=self.code,side=side,price=price,quantity=1,
            created_ts=ts,active_ts=ts,reason=reason,entry_mid_twice=e.bid+e.ask,
            entry_spread=e.ask-e.bid,feature=feature)
        self.rows['orders'].append(dict(o));self.order=o

    def option(self,e):
        if self.last and e.ts<=self.last.ts:raise ValueError('Unordered option')
        if any(b<=e.ts and b not in self.checked for b in BOUNDARIES):raise ValueError('Unsettled break')
        if e.session!=self.session:
            if self.inventory:raise ValueError('Cross-break exposure')
            self.cancel(e.ts,'session_change');self.flow.clear();self.session=e.session
        self.value.new_session(e.session);feature=self.value.feature(e.ts)
        if e.ts<CUTOFF:
            self.value.observe_option(e);self.last=e;return
        o=self.order
        if o and o.get('cancel_ts') is not None and o['cancel_ts']<=e.previous_ts:self.order=None;o=None
        if o and o['active_ts']<=e.previous_ts and o['created_ts']<e.ts and e.quantity>0 and e.single:
            opposite='sell' if o['side']=='buy' else 'buy'
            reachable=e.last<=o['price'] if o['side']=='buy' else e.last>=o['price']
            if e.strict_side==opposite and reachable:self.fill(e.ts,o['price'],o['side'],'passive',e,feature)
        if self.order and self.order.get('cancel_ts') is not None:self.order=None
        if not valid(e) or (self.last and e.ts-self.last.ts>60000):self.flow.clear()
        while self.flow and self.flow[0][0]<e.ts-300000:self.flow.popleft()
        if valid(e) and e.quantity>0 and e.single and e.strict_side in ('buy','sell'):self.flow.append((e.ts,e.strict_side))
        if valid(e):
            self.last_book=e
            boundary=BOUNDARIES[e.session]
            if self.settlement=='market' and e.ts>=boundary-5000:
                self.cancel(e.ts,'pre_break_market_window')
                if self.inventory:
                    side='sell' if self.inventory==1 else 'buy';price=e.bid if side=='sell' else e.ask
                    self.issue(e.ts,side,price,'pre_break_market_close',e,feature)
                    self.fill(e.ts,price,side,'pre_break_market_close',e)
            elif self.inventory:
                d=self.inventory;c=self.cycle
                if e.ts-c['entry_ts']>=300000:self.released=True
                if self.last and (e.ts-self.last.ts>60000 or not valid(self.last)):self.risk_since=None
                if d*((e.bid+e.ask)-c['entry_mid_twice']) < -2*c['entry_spread']:
                    if self.risk_since is None:self.risk_since=e.ts
                    if e.ts-self.risk_since>=30000:self.released=True
                else:self.risk_since=None
                improved=e.ask-e.bid>self.tick
                price=e.ask-(self.tick if improved else 0) if d==1 else e.bid+(self.tick if improved else 0)
                if self.cfg['patient'] and not self.released:
                    limit=c['entry_price_cents']+d*(2*FEE+self.tick)
                    limit=ceil(limit/self.tick)*self.tick if d==1 else floor(limit/self.tick)*self.tick
                    price=max(price,limit) if d==1 else min(price,limit)
                side='sell' if d==1 else 'buy'
                if price>0 and (not self.order or (self.order['side'],self.order['price'])!=(side,price)):
                    self.issue(e.ts,side,price,'patient_exit' if self.cfg['patient'] and not self.released else 'top_exit',e,feature)
            else:
                allowed=[]
                ds=(1,-1) if self.cfg['direction']=='fair' else ((1,) if self.cfg['direction']=='long' else (-1,))
                for d in ds:
                    c,reason=candidate(e,d,self.tick,feature,self.cfg,self.flow)
                    if c:
                        if d==1:reserve=c['price']+FEE
                        else:
                            f=self.value.future
                            if not(f and f.source_ts<e.ts and e.ts-f.source_ts<=2000):
                                self.rejects['short_funding_source_stale']+=1;continue
                            reserve=ceil(f.mid*100000*.20)+c['price']+2*FEE
                        if self.cash<reserve:self.rejects['insufficient_risk_capital']+=1;continue
                        c['reserve']=reserve;allowed.append(c)
                    else:self.rejects[reason]+=1
                if allowed:
                    c=max(allowed,key=lambda x:(x['edge_cents'] if x['edge_cents'] is not None else 0,x['direction']))
                    side='buy' if c['direction']==1 else 'sell';price=c['price'];self.max_reserve=max(self.max_reserve,c['reserve'])
                    if not self.order or (self.order['side'],self.order['price'])!=(side,price):self.issue(e.ts,side,price,'entry',e,feature)
                else:self.cancel(e.ts,'entry_gate')
        else:self.cancel(e.ts,'invalid_book')
        self.value.observe_option(e);self.last=e;self.mark(e.ts)
        if self.inventory==-1 and self.value.future and self.last_book:
            reserve=ceil(self.value.future.mid*100000*.20)+self.last_book.ask
            self.max_reserve=max(self.max_reserve,reserve)
            if self.cash-self.last_book.ask<reserve:self.margin_breaches+=1

    def boundary(self,ts):
        if ts not in BOUNDARIES or ts in self.checked:raise ValueError('Invalid boundary')
        if self.last and self.last.ts>=ts:raise ValueError('Backdated boundary')
        self.cancel(ts,'break')
        if self.inventory:
            if self.settlement=='market':raise ValueError('No executable quote in pre-break window')
            e=self.last_book;side='sell' if self.inventory==1 else 'buy';price=self.cycle['entry_price_cents']
            self.issue(ts,side,price,'virtual_cost_close',e)
            self.fill(ts,price,side,'virtual_cost_close')
        self.checked.add(ts);self.flow.clear();self.mark(ts)
        self.rows['boundaries'].append(dict(ts=ts,inventory=self.inventory,pending_order=self.order))

    def result(self):
        virtual=[c for c in self.rows['cycles'] if c['exit_kind']=='virtual_cost_close']
        normal=[c for c in self.rows['cycles'] if c['exit_kind']!='virtual_cost_close']
        pnl=self.cash-CAPITAL if not self.inventory else self.rows['curve'][-1][1]
        return dict(**self.rows,summary=dict(model_id=self.model,code=self.code,profile=self.profile,
            settlement=self.settlement,initial_cash_cny=CAPITAL/100,pnl_cny=pnl/100,
            end_cash_cny=self.cash/100,end_inventory=self.inventory,fees_cny=self.fees/100,
            realized_gross_cny=self.gross/100,complete_cycles=len(self.rows['cycles']),
            market_cycle_net_cny=sum(c['net_cents'] for c in normal)/100,market_cycles=len(normal),
            virtual_close_count=len(virtual),virtual_cycle_net_cny=-len(virtual)*2*FEE/100,
            removed_tail_gross_cny=sum(c['quote_mark_gross_before_close_cents'] for c in virtual)/100,
            active_close_count=sum(c['exit_kind']=='pre_break_market_close' for c in normal),
            max_drawdown_cny=self.dd/100,max_reserve_cny=self.max_reserve/100,margin_breach_frames=self.margin_breaches,
            rejection_frames=dict(self.rejects),future_cancel_requests=sum('future_risk_' in c['reason'] for c in self.rows['cancels']),
            ambiguous_cancel_fills=sum(f.get('cancellation_inside_aggregate_interval',False) for f in self.rows['fills']),
            long_cycles=sum(c['direction']==1 for c in self.rows['cycles']),short_cycles=sum(c['direction']==-1 for c in self.rows['cycles'])))
