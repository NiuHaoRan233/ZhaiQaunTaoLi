"""User-specified intraday research: cost-price virtual liquidation at 15:00.

This settlement is NOT a market fill. Quote-mark PnL immediately before the
virtual close, quote age and the resulting valuation adjustment are retained.
No broker or market connections. Prior registered engines remain immutable.
"""
from datetime import datetime, timezone, timedelta

from .commodity_flow_strategy import FEE
from .commodity_capital_research import ResearchAccount, SharedPortfolio, PROFILES

FAMILY = 'probe_commodity_intraday_20260913_v1'
SHANGHAI = timezone(timedelta(hours=8))


def close_timestamp(date):
    return int(datetime.strptime(date,'%Y%m%d').replace(hour=15,tzinfo=SHANGHAI).timestamp()*1000)


class IntradayAccount(ResearchAccount):
    def __init__(self,code,profile,initial_cents,tick_cents,suffix='independent'):
        super().__init__(code,profile,initial_cents,tick_cents,suffix)
        self.model=f'{FAMILY}_{profile}_{suffix}_cost_close'
        self.s['model_id']=self.model
        self.virtual_closes=[]
        self.closed_dates=set()

    def step(self,e,date,entry_cash_limit=None):
        if date in self.closed_dates:
            raise ValueError('Cannot trade after the date has been settled')
        return super().step(e,date,entry_cash_limit)

    def close_day(self,date):
        if date in self.closed_dates:
            raise ValueError('Date already virtually settled')
        self.closed_dates.add(date)
        s=self.s;ts=close_timestamp(date)
        if s['last_ts'] is not None and s['last_ts']>ts:
            raise ValueError('Cannot backdate cost settlement')
        d=dict(orders=[],fills=[],cycles=[],cancels=self._cancel(ts,'end_of_day'))
        if date not in s['daily']:
            if s['inventory']:
                raise ValueError('Inventory unexpectedly crossed an unobserved date')
            return d
        if s['inventory']:
            if s['cycle']['entry_ts']//86400000 != ts//86400000:
                raise ValueError('Overnight inventory is prohibited')
            price=s['basis']
            if s['cash']+price<FEE:
                raise ValueError('Insufficient cash for virtual exit fee')
            s['holding_ms'] += ts-s['last_ts']
            quote_pnl=s['last_bid']-price
            mark_adjustment=price-s['last_bid']
            age=(ts-s['last_quote_ts'])/1000 if s['last_quote_ts'] else None
            s['order_count']+=1
            order=dict(id=s['order_count'],model_id=self.model,code=self.code,side='sell',
                price=price,quantity=1,created_ts=ts,due_ts=ts,reason='user_virtual_cost_close',
                execution='virtual_settlement_not_market_order')
            cycle=dict(s['cycle'],gross_cents=0,fees_cents=2*FEE,exit_ts=ts,
                duration_seconds=(ts-s['cycle']['entry_ts'])/1000,net_cents=-2*FEE,
                exit_kind='virtual_cost_close',quote_mark_pnl_before_close_cents=quote_pnl,
                settlement_mark_adjustment_cents=mark_adjustment,reference_bid_cents=s['last_bid'],
                reference_quote_ts=s['last_quote_ts'],reference_quote_age_seconds=age)
            s.update(cash=s['cash']+price-FEE,inventory=0,basis=0,cycle=None,
                     fees=s['fees']+FEE,fill_count=s['fill_count']+1,
                     cycle_count=s['cycle_count']+1,completed_net=s['completed_net']-2*FEE,
                     losing=s['losing']+1,turnover=s['turnover']+price)
            fill=dict(model_id=self.model,code=self.code,order_id=order['id'],ts=ts,side='sell',
                price_cents=price,quantity=1,fee_cents=FEE,inventory=0,cash_cents=s['cash'],
                kind='virtual_cost_close',created_ts=ts,active_ts=ts,date=date,
                source_last_contract_evidence=False,source_quantity=0,
                reference_bid_cents=s['last_bid'],reference_quote_ts=s['last_quote_ts'],
                reference_quote_age_seconds=age,quote_mark_pnl_before_close_cents=quote_pnl,
                settlement_mark_adjustment_cents=mark_adjustment)
            self.virtual_closes.append(fill)
            d['orders'].append(order);d['fills'].append(fill);d['cycles'].append(cycle)
            s['daily'][date]['fill_count']+=1
        pnl=s['cash']-s['initial']
        s['peak']=max(s['peak'],s['cash'])
        s['drawdown']=max(s['drawdown'],s['peak']-s['cash'])
        s['daily'][date].update(pnl_cny=(pnl-s['daily'][date]['opening_pnl_cents'])/100,
            cumulative_pnl_cny=pnl/100,end_inventory=0,last_ts=ts)
        s.update(last_pnl=pnl,last_ts=ts,flow=[],previous_valid=False,risk_since=None,
                 latest_reason='day_closed_at_entry_cost')
        d['curve']=(ts,pnl,0)
        self._invariant()
        return d

    def summary(self,asof_ms=None):
        s=super().summary(asof_ms)
        count=len(self.virtual_closes)
        s.update(intraday_only=True,settlement='entry_cost_virtual_close_at_1500',
            market_closed_cycles=s['complete_cycles']-count,
            market_cycle_net_cny=(self.s['completed_net']+count*2*FEE)/100,
            virtual_close_count=count,virtual_roundtrip_fees_cny=count*2*FEE/100,
            virtual_sell_fees_cny=count*FEE/100,
            virtual_cycle_net_cny=-count*2*FEE/100,
            market_fill_sides=s['fill_count']-count,
            quote_mark_pnl_before_virtual_close_cny=sum(f['quote_mark_pnl_before_close_cents'] for f in self.virtual_closes)/100,
            virtual_mark_adjustment_cny=sum(f['settlement_mark_adjustment_cents'] for f in self.virtual_closes)/100,
            stale_virtual_closes=sum(f['reference_quote_age_seconds'] is None or f['reference_quote_age_seconds']>60 for f in self.virtual_closes),
            cash_carries_between_days=True)
        return s


class IntradayPortfolio(SharedPortfolio):
    def __init__(self,instruments,profile,initial_cents):
        super().__init__(instruments,profile,initial_cents)
        self.model=f'{FAMILY}_{profile}_shared_{initial_cents//100}_cost_close'
        self.accounts={c:IntradayAccount(c,profile,v['initial_cents'],v['tick_cents'],
            suffix=f'shared_{initial_cents//100}') for c,v in instruments.items()}

    def close_day(self,date):
        ts=close_timestamp(date)
        for code,a in sorted(self.accounts.items()):
            before_cash,before_pnl=a.s['cash'],a.s['last_pnl']
            before_mark=a.s['inventory']*a.s['last_bid'];before_basis=a.s['basis']
            self.reserved-=self.reserves.pop(code,0)
            d=a.close_day(date)
            self.cash+=a.s['cash']-before_cash
            self.pnl+=a.s['last_pnl']-before_pnl
            self.marked-=before_mark
            self.basis-=before_basis
            self.orders.extend(d['orders'])
            self.fills.extend(dict(f,portfolio_model_id=self.model,
                portfolio_cash_cents=self.cash,portfolio_reserved_cents=self.reserved) for f in d['fills'])
            self.cycles.extend(dict(c,code=code) for c in d['cycles'])
            if not (0<=self.reserved<=self.cash and self.cash+self.marked==self.initial+self.pnl):
                raise ValueError('Virtual close funding reconciliation failed')
        assert not self.reserves and self.reserved==self.marked==self.basis==0
        self.mark(ts,date)

    def result(self):
        r=super().result()
        s=r['summary'];rows=[a['summary'] for a in r['accounts'].values()]
        for key in ['market_closed_cycles','market_cycle_net_cny','virtual_close_count',
            'virtual_roundtrip_fees_cny','virtual_sell_fees_cny','virtual_cycle_net_cny',
            'market_fill_sides','quote_mark_pnl_before_virtual_close_cny',
            'virtual_mark_adjustment_cny','stale_virtual_closes']:
            s[key]=sum(a[key] for a in rows)
        s.update(intraday_only=True,settlement='entry_cost_virtual_close_at_1500',
            cash_carries_between_days=True,overnight_cycles=0)
        assert s['tail_contracts']==0
        assert round((s['market_cycle_net_cny']+s['virtual_cycle_net_cny'])*100)==round(s['pnl_cny']*100)
        for a in r['accounts'].values():
            assert all(d['end_inventory']==0 for d in a['daily'])
        return r
