"""Registered timing repair: an already processed same-time option is available.

Only the futures-event cancellation evaluator changes. Option-event valuation
still requires strictly earlier calibration, as in the immutable parent.
"""
from . import gold_direction_research as parent

FAMILY='probe_gold_direction_timing_20260913_v2'


class ValueState(parent.ValueState):
    def cancellation_feature(self,ts):
        f=self.future
        if not(f and f.ts<=ts and f.source_ts<=ts and ts-f.source_ts<=2000 and self.vol is not None):return None
        if self.vol_ts>ts or ts-self.vol_ts>60000:return None
        price,delta=parent.black_call(f.mid,self.strike,self.maturity(ts),self.vol)
        moves={}
        for seconds in (10,60):
            old=next((x for x in reversed(self.history) if x.ts<=ts-seconds*1000),None)
            moves[f'move{seconds}_cents']=delta*(f.mid-old.mid)*100000 if old and ts-seconds*1000-old.ts<=2000 else None
        return dict(fair_cents=price*100000,delta=delta,vol=self.vol,future_mid=f.mid,
            future_source_ts=f.source_ts,future_available_ts=f.ts,vol_source_ts=self.vol_ts,
            ready=ts-self.first>=60000 and self.updates>=3,**moves)


class Account(parent.Account):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.value=ValueState(self.value.strike);self.model=self.model.replace(parent.FAMILY,FAMILY)

    def on_future(self,f):
        self.value.on_future(f)
        if not(self.cfg['proactive'] and self.order and not self.inventory and self.last_book):return
        o=self.order
        if o.get('cancel_ts') is not None:return
        feat=self.value.cancellation_feature(f.ts)
        d=1 if o['side']=='buy' else -1
        c,reason=parent.candidate(self.last_book,d,self.tick,feat,self.cfg,self.flow)
        edge=d*(feat['fair_cents']-o['price'])-2*parent.FEE if feat else None
        if c is None or edge is None or edge<max(self.tick,(self.last_book.ask-self.last_book.bid)/4):
            o['cancel_ts']=f.ts
            self.rows['cancels'].append(dict(model_id=self.model,code=self.code,order_id=o['id'],ts=f.ts,
                reason='future_risk_'+reason,conservative_interval_settlement=True,feature=feat))
