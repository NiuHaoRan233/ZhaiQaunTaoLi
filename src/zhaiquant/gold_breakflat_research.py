"""New user rule: actual-price simulated liquidation before every daytime break.

No synthetic cost resets and no broker access. Old eligible fills settle first;
then new buys are gated and inventory sells against the observed bid, if present.
"""
from .gold_state_research import GoldAccount, START, CUTOFF, END, DATE
from .commodity_flow_strategy import FEE

FAMILY='probe_gold_breakflat_20260913_v1'
BOUNDARIES=(START+75*60000,START+150*60000,END)
STOP_BEFORE_MS=300000
FLAT_BEFORE_MS=60000


class BreakFlatAccount(GoldAccount):
    def __init__(self,code,profile,tick_cents):
        super().__init__(code,profile,tick_cents)
        self.model=f'{FAMILY}_{profile}_{code}_independent_150000'
        self.s['model_id']=self.model
        self.active_exits=[]
        self.boundary_checks=[]

    def check_boundary(self,ts):
        if ts not in BOUNDARIES or any(x['ts']==ts for x in self.boundary_checks):
            raise ValueError('Unknown or duplicate trading boundary')
        if self.s['last_ts'] is not None and self.s['last_ts']>=ts:
            raise ValueError('Boundary check cannot be backdated')
        if self.s['inventory']:
            raise ValueError('Unflattened inventory at trading break: no executable bid before deadline')
        cancels=self._cancel(ts,'trading_break_cancel')
        self.boundary_checks.append(dict(ts=ts,inventory=0,pending_order=None,last_event_ts=self.s['last_ts']))
        return cancels

    def _active_sell(self,e,boundary,result):
        s=self.s
        if not s['inventory'] or e.bid<=0 or e.bid_qty<1:
            return
        price=e.bid
        if s['cash']+price<FEE:
            raise ValueError('Insufficient cash for active closing fee')
        result['cancels']+=self._cancel(e.ts,'pre_break_active_exit')
        s['order_count']+=1
        order=dict(id=s['order_count'],model_id=self.model,code=self.code,side='sell',price=price,
            quantity=1,created_ts=e.ts,due_ts=e.ts,reason='pre_break_active_exit',
            execution='simulated_marketable_limit_at_observed_bid',boundary_ts=boundary)
        cycle=dict(s['cycle'],gross_cents=price-s['basis'],fees_cents=2*FEE,exit_ts=e.ts,
            duration_seconds=(e.ts-s['cycle']['entry_ts'])/1000,net_cents=price-s['basis']-2*FEE,
            exit_kind='pre_break_active_sell',boundary_ts=boundary)
        s.update(cash=s['cash']+price-FEE,realized=s['realized']+price-s['basis'],inventory=0,basis=0,cycle=None,
            fees=s['fees']+FEE,fill_count=s['fill_count']+1,cycle_count=s['cycle_count']+1,
            completed_net=s['completed_net']+cycle['net_cents'],turnover=s['turnover']+price,risk_since=None,
            winning=s['winning']+int(cycle['net_cents']>0),losing=s['losing']+int(cycle['net_cents']<0))
        fill=dict(model_id=self.model,code=self.code,order_id=order['id'],ts=e.ts,side='sell',
            price_cents=price,quantity=1,fee_cents=FEE,inventory=0,cash_cents=s['cash'],
            kind='pre_break_active_sell',created_ts=e.ts,active_ts=e.ts,date=DATE,
            source='current_displayed_bid_not_trade_print',source_bid_cents=e.bid,source_bid_qty=e.bid_qty,
            source_quote_ts=e.ts,boundary_ts=boundary,source_last_contract_evidence=False)
        self.active_exits.append(dict(fill,net_cycle_cents=cycle['net_cents']))
        result['orders'].append(order);result['fills'].append(fill);result['cycles'].append(cycle)
        pnl=s['cash']-s['initial']
        s['peak']=max(s['peak'],s['cash']);s['drawdown']=max(s['drawdown'],s['peak']-s['cash'])
        s['daily'][DATE]['fill_count']+=1
        s['daily'][DATE].update(pnl_cny=(pnl-s['daily'][DATE]['opening_pnl_cents'])/100,
            cumulative_pnl_cny=pnl/100,end_inventory=0,last_ts=e.ts)
        s.update(last_pnl=pnl,latest_reason='pre_break_active_exit')
        result['curve']=(e.ts,pnl,0)
        self._invariant()

    def step(self,e,date,entry_cash_limit=None):
        if any(e.ts>=ts and not any(x['ts']==ts for x in self.boundary_checks) for ts in BOUNDARIES):
            raise ValueError('Must check intervening break before resuming')
        boundary=next((ts for ts in BOUNDARIES if ts>e.ts),None)
        result=super().step(e,date,entry_cash_limit)
        if boundary is None:return result
        if e.ts>=boundary-STOP_BEFORE_MS:
            o=self.s['order']
            if o and o['side']=='buy':
                if any(x['id']==o['id'] for x in result['orders']):
                    result['orders']=[x for x in result['orders'] if x['id']!=o['id']]
                    self.s['order_count']-=1;self.s['order']=None
                else:
                    result['cancels']+=self._cancel(e.ts,'pre_break_stop_entry')
                self.s['rejects']['pre_break_stop_entry']=self.s['rejects'].get('pre_break_stop_entry',0)+1
                self.s['latest_reason']='pre_break_stop_entry'
        if e.ts>=boundary-FLAT_BEFORE_MS:
            self._active_sell(e,boundary,result)
        return result

    def close_day(self,date):
        if self.s['inventory']:
            raise ValueError('Day-end cost reset prohibited; actual-price flatness required')
        if len(self.boundary_checks)!=len(BOUNDARIES):
            raise ValueError('Every daily boundary must be checked')
        return super().close_day(date)

    def summary(self,asof_ms=None):
        s=super().summary(asof_ms)
        n=len(self.active_exits);net=sum(f['net_cycle_cents'] for f in self.active_exits)/100
        s.update(settlement='observed_bid_active_close_before_all_daytime_breaks',
            stop_entry_seconds=STOP_BEFORE_MS/1000,active_exit_seconds=FLAT_BEFORE_MS/1000,
            active_close_count=n,active_close_cycle_net_cny=net,
            passive_closed_cycles=s['market_closed_cycles']-n,passive_cycle_net_cny=s['market_cycle_net_cny']-net,
            completed_boundary_checks=len(self.boundary_checks),virtual_cost_close_allowed=False)
        return s
