"""Separate frozen follow-up: distinguish missing-depth selection from gap avoidance."""
from .commodity_mau_transfer_research import TransferAccount, TransferPortfolio, gate, PROFILES

FAMILY='probe_commodity_mau_depth_audit_20260913_v1'
VARIANTS=('depth_only','gap_when_observed')


class DepthAccount(TransferAccount):
    def __init__(self,code,profile,initial_cents,tick_cents,suffix='independent'):
        if profile not in VARIANTS:
            raise ValueError(profile)
        super().__init__(code,'control',initial_cents,tick_cents,suffix)
        self.audit_profile=profile
        self.model=f'{FAMILY}_{profile}_{suffix}_cost_close'
        self.s['model_id']=self.model

    def step(self,e,date,entry_cash_limit=None):
        result=super().step(e,date,entry_cash_limit)
        o=self.s['order']
        if o and o['side']=='buy':
            reason=gate(e,o['price'],self.tick,PROFILES['gap'],None,date)
            if self.audit_profile=='depth_only' and reason=='isolated_bid1':
                reason=None
            if self.audit_profile=='gap_when_observed' and reason=='bid2_unavailable':
                reason=None
            if reason:
                if any(x['id']==o['id'] for x in result['orders']):
                    result['orders']=[x for x in result['orders'] if x['id']!=o['id']]
                    self.s['order_count']-=1;self.s['order']=None
                else:
                    result['cancels']+=self._cancel(e.ts,reason)
                self.s['rejects'][reason]=self.s['rejects'].get(reason,0)+1
                self.s['latest_reason']=reason
        return result

    def summary(self,asof_ms=None):
        s=super().summary(asof_ms)
        s.update(mode=self.audit_profile,profile=self.audit_profile)
        return s


class DepthPortfolio(TransferPortfolio):
    def __init__(self,instruments,profile,initial_cents):
        super().__init__(instruments,'control',initial_cents)
        self.profile=profile
        self.model=f'{FAMILY}_{profile}_shared_{initial_cents//100}_cost_close'
        self.accounts={c:DepthAccount(c,profile,v['initial_cents'],v['tick_cents'],
            f'shared_{initial_cents//100}') for c,v in instruments.items()}
