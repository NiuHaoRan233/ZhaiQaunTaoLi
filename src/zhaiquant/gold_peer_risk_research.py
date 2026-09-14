"""Causal option-neighbour repricing warnings; immutable parent execution."""
from types import SimpleNamespace
import numpy as np
from math import erf
from . import gold_session_research as parent
from .gold_history_validation import timestamp

FAMILY='probe_gold_peer_risk_20260914_v1'
VARIANTS=('baseline','future_only','peer_cancel','peer_exit')
LOOKBACK=10000
FRESH=2000


def ndtr(x):
    return (1+np.frompyfunc(erf,1,1)(x/np.sqrt(2)).astype(float))/2


def black(f,k,t,v,kind):
    w=v*np.sqrt(t);d=np.log(f/k)/w+w/2
    call=f*ndtr(d)-k*ndtr(d-w)
    return call if kind==0 else call-f+k


def iv(price,f,k,t,kind):
    lo=np.full_like(price,.005);hi=np.full_like(price,3.)
    valid=(price>=black(f,k,t,lo,kind))&(price<=black(f,k,t,hi,kind))&(price>0)
    for _ in range(36):
        mid=(lo+hi)/2;below=black(f,k,t,mid,kind)<price
        lo=np.where(below,mid,lo);hi=np.where(below,hi,mid)
    return np.where(valid,(lo+hi)/2,np.nan)


def session(ts):
    out=np.full(len(ts),-1)
    for i,(lo,hi) in enumerate(zip(parent.g.SESSION_STARTS,parent.g.BOUNDARIES)):
        out[(ts>=lo)&(ts<hi)]=i
    return out


def prices(frame,clock):
    f=clock.frame(frame).sort_values('time',kind='stable').drop_duplicates('time',keep='last')
    bid=np.array(f.bidPrice.tolist())[:,0];ask=np.array(f.askPrice.tolist())[:,0]
    valid=(bid>0)&(ask>=bid)&(np.array(f.bidVol.tolist())[:,0]>0)&(np.array(f.askVol.tolist())[:,0]>0)
    return dict(ts=f.time.to_numpy(dtype=np.int64),mid=(bid+ask)/2,valid=valid,session=session(f.time.to_numpy()))


def sample(series,ts):
    ix=np.searchsorted(series['ts'],ts,side='right')-1;idx=np.maximum(ix,0)
    good=(ix>=0)&(ts-series['ts'][idx]<=FRESH)&series['valid'][idx]&(series['session'][idx]==session(ts))&(session(ts)>=0)
    return idx,good


def make_series(frame,terms,future,clock):
    q=prices(frame,clock);j,good=sample(future,q['ts'])
    q['future_source_ts']=future['ts'][j]
    maturity=np.maximum(1/365,(timestamp(terms['ExpireDate'],'150000')+clock.shift-q['ts'])/(365*86400000))
    q['iv']=iv(q['mid'],future['mid'][j],terms['OptExercisePrice'],maturity,terms['OptionType'])
    q['valid'] &= good&np.isfinite(q['iv'])
    return q


def signals(target,peers,futures,terms,peer_terms,clock):
    """All calculations at t use only raw rows <=t. Delivered AFTER target rows at t."""
    f=prices(futures,clock);q=make_series(target,terms,f,clock)
    ps={c:make_series(frame,peer_terms[c],f,clock) for c,frame in peers.items()}
    grid=np.unique(np.concatenate([f['ts'],q['ts']]+[p['ts'] for p in ps.values()]))
    grid=grid[session(grid)>=0]
    now,good=sample(q,grid);old,gold=sample(q,grid-LOOKBACK);fj,fg=sample(f,grid)
    maturity=np.maximum(1/365,(timestamp(terms['ExpireDate'],'150000')+clock.shift-grid)/(365*86400000))
    pred0=black(f['mid'][fj],terms['OptExercisePrice'],maturity,q['iv'][old],terms['OptionType'])*100000
    common=good&gold&fg&(q['session'][old]==session(grid))
    forecasts=[];usable=[];indices=[]
    for c,p in ps.items():
        pn,ng=sample(p,grid);po,og=sample(p,grid-LOOKBACK)
        valid=ng&og&(p['session'][po]==session(grid))
        mapped=q['iv'][old]+p['iv'][pn]-p['iv'][po]
        valid &= (mapped>=.005)&(mapped<=3)
        forecasts.append(black(f['mid'][fj],terms['OptExercisePrice'],maturity,np.clip(mapped,.005,3),terms['OptionType'])*100000)
        usable.append(valid);indices.append((pn,po))
    out=[];coverage=0
    for i,t in enumerate(grid):
        goodpeers=[j for j in range(len(ps)) if usable[j][i]]
        valid=bool(common[i] and len(goodpeers)>=2)
        signal=dict(ts=int(t),session=int(session(grid[i:i+1])[0]),ready=valid)
        if valid:
            coverage+=1
            signal.update(target_source_ts=int(q['ts'][now[i]]),target_anchor_ts=int(q['ts'][old[i]]),
                target_anchor_iv=float(q['iv'][old[i]]),target_mid_cents=float(q['mid'][now[i]]*100000),
                future_source_ts=int(f['ts'][fj[i]]),future_fair_cents=float(pred0[i]),peers=[])
            for j,(c,p) in enumerate(ps.items()):
                if j not in goodpeers:continue
                pn,po=indices[j]
                signal['peers'].append(dict(code=c,source_ts=int(p['ts'][pn[i]]),anchor_ts=int(p['ts'][po[i]]),
                    iv_change=float(p['iv'][pn[i]]-p['iv'][po[i]]),fair_cents=float(forecasts[j][i]),
                    paired_future_ts=int(p['future_source_ts'][pn[i]]),anchor_future_ts=int(p['future_source_ts'][po[i]])))
        out.append(signal)
    return out,dict(events=len(grid),ready_events=coverage,ready_fraction=coverage/len(grid),peer_codes=list(ps))


def adverse(signal,variant,ts,book,direction,tick):
    if variant=='baseline' or not signal or not signal['ready']:return False
    if not 0<=ts-signal['ts']<=FRESH or book.session!=signal['session'] or not 0<=ts-book.ts<=FRESH:return False
    mid=(book.bid+book.ask)/2
    if variant=='future_only':return direction*(signal['future_fair_cents']-mid)<=-2*tick
    gaps=[direction*(p['fair_cents']-mid) for p in signal['peers']]
    return sum(g<=-2*tick for g in gaps)>=2 and float(np.median(gaps))<=-2*tick


class Account(parent.Account):
    def __init__(self,code,policy,strike,direction,option_type,mask,variant,through=False):
        if variant not in VARIANTS:raise ValueError('Unknown peer variant')
        super().__init__(code,policy,strike,direction,100,option_type,mask,through)
        self.variant=variant;self.peer_signal=None;self.peer_cancellations=0;self.peer_exits=0
        self.model=f'{FAMILY}_{mask}_{policy}_{self.mode}_{variant}_bps100_{code}_through{int(through)}'

    def peer_event(self,signal):
        self.peer_signal=signal
        if self.variant=='baseline' or not self.order or self.inventory or not self.last_book:return
        if self.order.get('cancel_ts') is not None:return
        if adverse(signal,self.variant,signal['ts'],self.last_book,self.fixed_direction,self.tick):
            self.request_cancel(SimpleNamespace(ts=signal['ts']),signal,'peer_'+self.variant)
            self.peer_cancellations+=1

    def candidate(self,e,feature):
        c,reason=super().candidate(e,feature)
        if c and adverse(self.peer_signal,self.variant,e.ts,e,self.fixed_direction,self.tick):return None,'peer_'+self.variant
        return c,reason

    def risk_exit(self,e,feature):
        if super().risk_exit(e,feature):return True
        if self.variant!='peer_exit' or not adverse(self.peer_signal,self.variant,e.ts,e,self.inventory,self.tick):return False
        d=self.inventory;side='sell' if d==1 else 'buy';price=e.bid if d==1 else e.ask
        if (e.bid_qty if d==1 else e.ask_qty)<1 or price<=0:return False
        evidence=dict(ts=e.ts,entry_ts=self.cycle['entry_ts'],reason='peer_consensus_exit',feature=self.peer_signal,
            exit_price_cents=price,loss_from_entry_cents=d*(price-self.cycle['entry_price_cents']))
        self.issue(e.ts,side,price,'risk_market_close',e,feature);self.fill(e.ts,price,side,'risk_market_close',e,feature)
        self.risk_events.append(evidence);self.peer_exits+=1
        return True

    def result(self):
        r=super().result();r['summary'].update(peer_variant=self.variant,peer_cancel_count=self.peer_cancellations,peer_exit_count=self.peer_exits)
        return r
