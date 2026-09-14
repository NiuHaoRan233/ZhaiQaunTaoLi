"""Wide-spread first, futures-aware causal maker candidates. Offline only."""
from dataclasses import dataclass, replace, asdict
from collections import Counter
from . import gold_rule_ladder_research as parent
from . import gold_direction_research as g

FAMILY='probe_gold_spread_future_20260913_v3'


@dataclass(frozen=True)
class Profile:
    direction:str='long'
    value:bool=False
    trend:bool=False
    patient:bool=False
    cancel:bool=False
    manage:bool=False
    throttle:bool=False


PROFILES={
    'base_long':Profile(), 'base_short':Profile(direction='short'),
    'trend_long':Profile(trend=True), 'trend_short':Profile(direction='short',trend=True),
    'value_top_long':Profile(value=True), 'value_top_short':Profile(direction='short',value=True),
    'value_long':Profile(value=True,patient=True), 'value_short':Profile(direction='short',value=True,patient=True),
    'value_switch':Profile(direction='switch',value=True,patient=True),
    'trend_switch':Profile(direction='switch',value=True,trend=True,patient=True),
    'cancel_switch':Profile(direction='switch',value=True,trend=True,patient=True,cancel=True),
    'manage_switch':Profile(direction='switch',value=True,trend=True,patient=True,cancel=True,manage=True),
    'throttle_long':Profile(value=True,patient=True,throttle=True),
    'throttle_switch':Profile(direction='switch',value=True,patient=True,throttle=True),
}


def trend_ok(feature,d,spread,tick):
    if not(feature and feature['ready']):return False
    moves=[feature.get(f'move{n}_cents') for n in (10,60)]
    return all(v is not None and d*v>=-max(2*tick,spread/2) for v in moves)


class Account(parent.Account):
    def __init__(self,code,profile,strike,spread=8,through=False,capital=250000,delay=0):
        cfg=PROFILES[profile]
        super().__init__(code,'s10',1 if cfg.direction!='short' else -1,strike,capital,through)
        self.profile_cfg=cfg;self.profile=profile;self.spread=spread;self.delay=delay
        self.rules=parent.Rules(improve=True,min_spread_ticks=spread,value=cfg.value,fast=True,
            warm=cfg.value or cfg.trend,patient=cfg.patient,adverse_release=cfg.patient)
        self.model=f'{FAMILY}_{profile}_spread{spread}_{code}_capital{self.initial}_through{int(through)}_delay{delay}'
        self.features={1:None,-1:None};self.chosen_feature=None;self.throttle_skips=0;self.manage_events=[]

    def candidate(self,e,feature):
        # A pending future cancellation was conservatively eligible for the OLD
        # aggregate interval above; expire it before making a fresh decision.
        if self.order and self.order.get('cancel_ts') is not None:self.order=None
        cfg=self.profile_cfg;chosen=[];reason='no_side_eligible'
        for d in ((1,-1) if cfg.direction=='switch' else ((1,) if cfg.direction=='long' else (-1,))):
            self.direction=d;feat=self.features[d]
            c,why=super().candidate(e,feat)
            if c and cfg.trend and not trend_ok(feat,d,e.ask-e.bid,self.tick):c=None;why='future_trend'
            if c:chosen.append((c['edge_cents'] if c['edge_cents'] is not None else 0,d,c,feat))
            else:self.rejects['side_'+str(d)+'_'+why]+=1;reason=why
        if not chosen:return None,reason
        _,self.direction,c,self.chosen_feature=max(chosen,key=lambda x:x[:2])
        return c,'eligible'

    def issue(self,ts,side,price,reason,e,feature=None):
        if reason=='entry':
            feature=self.chosen_feature
            o=self.order;cfg=self.profile_cfg;d=1 if side=='buy' else -1
            if cfg.throttle and o and o['side']==side and o.get('cancel_ts') is None:
                passive=o['price']<e.ask if d==1 else o['price']>e.bid
                edge=d*(feature['fair_cents']-o['price'])-2*g.FEE if feature else None
                valid_old=passive and edge is not None and edge>=max(self.tick,(e.ask-e.bid)/4)
                if valid_old and (abs(price-o['price'])<2*self.tick or ts-o['created_ts']<1000):
                    self.throttle_skips+=1;return
        super().issue(ts,side,price,reason,e,feature)

    def option(self,e):
        if self.order and self.order.get('cancel_ts') is not None and self.order['cancel_ts']<=e.previous_ts:
            self.order=None
        d=self.inventory or (1 if self.order and self.order['side']=='buy' else -1 if self.order else self.direction)
        self.value.current=self.features[d]
        super().option(e)

    def future_event(self,f,features):
        self.value.future=f
        o=self.order;b=self.last_book;cfg=self.profile_cfg
        if cfg.cancel and o and not self.inventory and o.get('cancel_ts') is None:
            d=1 if o['side']=='buy' else -1;feat=features[d]
            good=bool(b and f.session==b.session and f.ts-b.ts<=2000 and feat and feat['ready'])
            if good:
                edge=d*(feat['fair_cents']-o['price'])-2*g.FEE
                good=edge>=max(self.tick,(b.ask-b.bid)/4) and trend_ok(feat,d,b.ask-b.bid,self.tick)
            if not good:
                o['cancel_ts']=f.ts
                self.rows['cancels'].append(dict(model_id=self.model,code=self.code,order_id=o['id'],ts=f.ts,
                    reason='future_risk_cancel',conservative_interval_settlement=True,feature=feat))
        if cfg.manage and self.inventory and not self.released and b and f.session==b.session:
            feat=features[self.inventory]
            if feat and feat['ready'] and not trend_ok(feat,self.inventory,b.ask-b.bid,self.tick):
                self.released=True
                self.manage_events.append(dict(ts=f.ts,entry_ts=self.cycle['entry_ts'],reason='future_adverse_release',feature=feat))

    def result(self):
        r=super().result();s=r['summary'];r['manage_events']=self.manage_events
        orders=r['orders'];filled={f['order_id'] for f in r['fills']};counts=Counter(o['reason'] for o in orders)
        s.update(profile=self.profile,profile_rules=asdict(self.profile_cfg),spread_ticks=self.spread,delay_ms=self.delay,
            order_count=len(orders),entry_order_count=counts['entry'],cancel_count=len(r['cancels']),
            reprice_cancel_count=sum(c['reason']=='reprice' for c in r['cancels']),
            order_fill_ratio_pct=100*len(filled)/len(orders) if orders else 0,
            throttle_skips=self.throttle_skips,manage_release_count=len(self.manage_events))
        return r
