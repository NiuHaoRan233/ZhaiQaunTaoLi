"""Only throttle favorable-direction chasing; safety retreat and exits stay immediate."""
from dataclasses import replace
from . import gold_spread_future_research as base

FAMILY='probe_gold_spread_quote_control_20260913_v4'
PROFILES={'trend_long':'trend_long','cancel_switch':'cancel_switch'}


class Account(base.Account):
    def __init__(self,code,profile,strike,spread=8,through=False,capital=250000,delay=0):
        super().__init__(code,PROFILES[profile],strike,spread,through,capital,delay)
        self.profile_cfg=replace(self.profile_cfg,throttle=True)
        self.model=self.model.replace(base.FAMILY,FAMILY)

    def issue(self,ts,side,price,reason,e,feature=None):
        if reason=='entry':
            feature=self.chosen_feature;o=self.order;d=1 if side=='buy' else -1
            if o and o['side']==side and o.get('cancel_ts') is None:
                chasing=d*(price-o['price'])>0
                passive=o['price']<e.ask if d==1 else o['price']>e.bid
                safe=passive and e.ask-e.bid>=self.spread*self.tick
                if self.profile_cfg.value:
                    safe=safe and bool(feature and feature['ready'] and d*(feature['fair_cents']-o['price'])-2*base.g.FEE>=max(self.tick,(e.ask-e.bid)/4))
                if self.profile_cfg.trend:safe=safe and base.trend_ok(feature,d,e.ask-e.bid,self.tick)
                if chasing and safe and (abs(price-o['price'])<2*self.tick or ts-o['created_ts']<1000):
                    self.throttle_skips+=1;return
        # Bypass the old broad throttle: this child defines the whole rule.
        base.parent.Account.issue(self,ts,side,price,reason,e,feature)
