"""Explicit continuous-session account; no mutations of frozen engine globals."""
from dataclasses import replace
from math import ceil,floor
import numpy as np
from . import gold_reselection_research as parent
from . import gold_direction_research as g
from .gold_callput_research import timeline as parent_timeline
from .commodity_dadao_research import price_cents
from .option_top_cycle_research import Event

FAMILY='probe_gold_sessions_20260914_v1'
MASKS={'day':(0,1,2),'am1':(0,),'am2':(1,),'pm':(2,),'night':(3,),'both':(0,1,2,3)}
NIGHT_START=g.START+12*3600000
NIGHT_END=NIGHT_START+int(5.5*3600000)
ENDS={**dict(enumerate(g.BOUNDARIES)),3:NIGHT_END}


def night_inputs(options,futures,clock,detail):
    """One real continuous session across midnight; cumulative evidence never resets at00:00."""
    lo=NIGHT_START-clock.shift;hi=NIGHT_END-clock.shift
    f=options[(options.time>=lo)&(options.time<hi)].sort_values('time',kind='stable').drop_duplicates('time',keep='last')
    if f.empty:raise ValueError('Missing night option data')
    unit=detail['OptUnit'];events=[];previous=None;raw_volume=resets=0
    for r in f.itertuples():
        bids=tuple((price_cents(p,unit),int(q)) for p,q in zip(r.bidPrice,r.bidVol) if p>0 and q>0)
        asks=tuple((price_cents(p,unit),int(q)) for p,q in zip(r.askPrice,r.askVol) if p>0 and q>0)
        bid=price_cents(r.bidPrice[0],unit);ask=price_cents(r.askPrice[0],unit);last=price_cents(r.lastPrice,unit)
        qty=tx=0;side=strict='unknown';quality=False
        if previous is not None:
            pr,pbid,pask,plast=previous
            dv=int(r.volume-pr.volume);da=round(float(r.amount-pr.amount)*100)
            tx=max(0,int(r.transactionNum-pr.transactionNum))
            if dv<0 or da < -2:resets+=1
            elif dv>0 and last>0:
                qty=dv;raw_volume+=dv
                if pask>0 and last>=pask:side='buy'
                elif pbid>0 and last<=pbid:side='sell'
                elif last>plast:side='buy'
                elif last<plast:side='sell'
                quality=(da>0 and 0<pbid<pask and pr.bidVol[0]>0 and pr.askVol[0]>0 and 0<r.time-pr.time<=60000)
                if quality:
                    if last>=pask:strict='buy'
                    elif last<=pbid:strict='sell'
        events.append(Event(int(r.time)+clock.shift,int(previous[0].time if previous else r.time)+clock.shift,
            3,bid,ask,int(r.bidVol[0]),int(r.askVol[0]),bids,asks,last,min(qty,1),tx,side,quality,strict))
        previous=(r,bid,ask,last)
    fs=[]
    fu=futures[(futures.time>=lo)&(futures.time<hi)].sort_values('time',kind='stable').drop_duplicates('time',keep='last')
    for r in fu.itertuples():
        if 0<r.bidPrice[0]<=r.askPrice[0] and min(r.bidVol[0],r.askVol[0])>0:
            fs.append(g.Future(int(r.time)+clock.shift,int(r.time)+clock.shift,3,(r.bidPrice[0]+r.askPrice[0])/2))
    if not fs:raise ValueError('Missing night underlying')
    return events,fs,dict(volume=raw_volume,cumulative_resets=resets,rows=len(f),last_quote_age_seconds=(hi-int(f.time.max()))/1000)


def timeline(es,fs,strike,expiry,clock,option_type,cut=None):
    # Valuation never changes state on a boundary-only row. Keep its frozen
    # source-aligned calculations, adding only the true02:30 night boundary.
    rows=parent_timeline(es,fs,strike,expiry,clock,option_type,cut)
    if cut is None or NIGHT_END<=cut:rows.append((-1,NIGHT_END,None,None,None))
    return sorted(rows,key=lambda r:((r[1] if r[0]==-1 else r[1].ts),r[0]))


class Account(parent.Account):
    def __init__(self,code,policy,strike,direction,bps,option_type,mask,through=False):
        if mask not in MASKS:raise ValueError('Unknown session mask')
        super().__init__(code,policy,strike,direction,bps,option_type,through)
        self.mask=mask;self.allowed=MASKS[mask];self.boundaries=tuple(ENDS[i] for i in self.allowed)
        self.cutoff=NIGHT_START if mask=='night' else g.CUTOFF
        self.model=self.model.replace(parent.FAMILY,FAMILY+'_'+mask)

    def candidate(self,e,feature):
        if e.session not in self.allowed:return None,'session_filter'
        return super().candidate(e,feature)

    def boundary(self,ts):
        if ts not in self.boundaries or ts in self.checked:raise ValueError('Invalid boundary')
        if self.last and self.last.ts>=ts:raise ValueError('Backdated boundary')
        self.cancel(ts,'break');n=len(self.rows['cycles'])
        if self.inventory:
            e=self.last_book;side='sell' if self.inventory==1 else 'buy';price=self.cycle['entry_price_cents']
            self.issue(ts,side,price,'virtual_cost_close',e);self.fill(ts,price,side,'virtual_cost_close')
        self.checked.add(ts);self.flow.clear();self.mark(ts)
        self.rows['boundaries'].append(dict(ts=ts,inventory=self.inventory,pending_order=self.order))
        if len(self.rows['cycles'])>n:
            c=self.rows['cycles'][-1];self.cash+=2*g.FEE;self.fees-=2*g.FEE;self.completed+=2*g.FEE
            c.update(fees_cents=0,net_cents=0,excluded_from_research=True,excluded_original_fees_cents=2*g.FEE)
            self.adjustments.append(dict(ts=ts,entry_ts=c['entry_ts'],amount_cents=2*g.FEE,reason='exclude_break_cycle_fee_refund'))
            self.mark(ts)
        self.support_history.clear();self.support=None

    def result(self):
        r=super().result();r['summary'].update(session_mask=self.mask,allowed_sessions=self.allowed,
            prototype_family=parent.FAMILY,grouping='morning selection date plus following evening')
        return r

    def option(self,e):
        if e.session not in self.allowed:raise ValueError('Feed only selected sessions')
        if self.session!=e.session:self.side_history.clear()
        price,qty=(e.bid,e.bid_qty) if self.fixed_direction==1 else (e.ask,e.ask_qty)
        self.side_history.append((e.ts,price,qty))
        while self.side_history and self.side_history[0][0]<e.ts-4000:self.side_history.popleft()
        if self.last and e.ts<=self.last.ts:raise ValueError('Unordered option')
        if any(b<=e.ts and b not in self.checked for b in self.boundaries):raise ValueError('Unsettled break')
        if e.session!=self.session:
            if self.inventory:raise ValueError('Cross-break exposure')
            self.cancel(e.ts,'session_change');self.flow.clear();self.session=e.session
        if self.order and self.order.get('cancel_ts') is not None and self.order['cancel_ts']<=e.previous_ts:self.order=None
        d=self.inventory or (1 if self.order and self.order['side']=='buy' else -1 if self.order else self.direction)
        feature=self.features[d];self.value.current=feature
        self.support_history.append((e.ts,e.bid,e.bid_qty))
        while self.support_history and self.support_history[0][0]<e.ts-4000:self.support_history.popleft()
        if e.ts<self.cutoff:self.last=e;return
        o=self.order
        if o and o['active_ts']<=e.previous_ts and o['created_ts']<e.ts and e.quantity>0 and e.single:
            opposite='sell' if o['side']=='buy' else 'buy'
            reachable=e.last<=o['price'] if o['side']=='buy' else e.last>=o['price']
            if e.strict_side==opposite and reachable:self.fill(e.ts,o['price'],o['side'],'passive',e,feature)
        if not g.valid(e) or (self.last and e.ts-self.last.ts>60000):self.flow.clear()
        while self.flow and self.flow[0][0]<e.ts-300000:self.flow.popleft()
        if g.valid(e) and e.quantity>0 and e.single and e.strict_side in ('buy','sell'):self.flow.append((e.ts,e.strict_side))
        if g.valid(e):
            self.last_book=e
            if self.inventory and self.risk_exit(e,self.features[self.inventory]):pass
            elif self.inventory:
                d=self.inventory;c=self.cycle
                if e.ts-c['entry_ts']>=300000:self.released=True
                if self.rules.adverse_release:
                    if self.last and (e.ts-self.last.ts>60000 or not g.valid(self.last)):self.risk_since=None
                    if d*((e.bid+e.ask)-c['entry_mid_twice']) < -2*c['entry_spread']:
                        if self.risk_since is None:self.risk_since=e.ts
                        if e.ts-self.risk_since>=30000:self.released=True
                    else:self.risk_since=None
                improvement=self.tick if self.rules.improve and e.ask-e.bid>self.tick else 0
                price=e.ask-improvement if d==1 else e.bid+improvement
                if self.rules.patient and not self.released:
                    limit=c['entry_price_cents']+d*(2*g.FEE+self.tick)
                    limit=ceil(limit/self.tick)*self.tick if d==1 else floor(limit/self.tick)*self.tick
                    price=max(price,limit) if d==1 else min(price,limit)
                side='sell' if d==1 else 'buy'
                if price>0 and (not self.order or (self.order['side'],self.order['price'])!=(side,price)):
                    self.issue(e.ts,side,price,'patient_exit' if self.rules.patient and not self.released else 'top_exit',e,feature)
            else:
                c,reason=self.candidate(e,feature)
                if c:
                    if self.direction==1:reserve=c['price']+g.FEE
                    else:
                        f=self.value.future
                        if not(f and f.source_ts<e.ts and e.ts-f.source_ts<=2000):self.rejects['short_funding_source_stale']+=1;c=None
                        else:reserve=ceil(f.mid*100000*.20)+c['price']+2*g.FEE
                    if c:
                        self.demands.append([e.ts,reserve-(self.cash-self.initial),reserve])
                        if self.cash<reserve:self.rejects['insufficient_risk_capital']+=1;c=None
                if c:
                    side='buy' if self.direction==1 else 'sell';price=c['price'];self.max_reserve=max(self.max_reserve,reserve)
                    if not self.order or (self.order['side'],self.order['price'])!=(side,price):self.issue(e.ts,side,price,'entry',e,feature)
                else:
                    if reason!='eligible':self.rejects[reason]+=1
                    self.cancel(e.ts,'entry_gate')
        else:self.cancel(e.ts,'invalid_book')
        self.last=e;self.mark(e.ts)
        if self.inventory==-1 and self.value.future and self.last_book:
            reserve=ceil(self.value.future.mid*100000*.20)+self.last_book.ask
            self.max_reserve=max(self.max_reserve,reserve)
            if self.cash-self.last_book.ask<reserve:self.margin_breaches+=1
