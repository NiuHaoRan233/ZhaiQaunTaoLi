"""Second-round offline commodity research; never connects to trading APIs.

Parent execution is immutable. Entry gates are evaluated AFTER settling the old
order, so a new quote cannot retroactively veto an already eligible fill.
Virtual per-contract ledgers retain the original budget ceilings; shared cash is
the sole funded portfolio cash, and all pending buys reserve premium plus fees.
"""
from collections import Counter
from .commodity_flow_strategy import FlowAccount, FEE

FAMILY = 'probe_commodity_capital_20260913_v1'
PROFILES = {
    'base': dict(net_floor=0, min_each=1, window_ms=300000),
    'edge_fee': dict(net_floor=340, min_each=1, window_ms=300000),
    'edge10': dict(net_floor=1000, min_each=1, window_ms=300000),
    'flow2': dict(net_floor=0, min_each=2, window_ms=300000),
    'fresh60': dict(net_floor=0, min_each=1, window_ms=60000),
    'edge_flow2': dict(net_floor=340, min_each=2, window_ms=300000),
}


class ResearchAccount(FlowAccount):
    def __init__(self, code, profile, initial_cents, tick_cents, suffix='independent'):
        if profile not in PROFILES:
            raise ValueError(profile)
        super().__init__(code, 'flow_patient', initial_cents, tick_cents)
        self.profile = profile
        self.model = f'{FAMILY}_{profile}_{suffix}'
        self.s['model_id'] = self.model

    def step(self, e, date, entry_cash_limit=None):
        before_cash = self.s['cash']
        result = super().step(e, date)
        s = self.s
        o = s['order']
        if o is not None and o['side'] == 'buy':
            cfg = PROFILES[self.profile]
            improved = e.ask-e.bid > self.tick
            sell = e.ask-(self.tick if improved else 0)
            counts = Counter(side for ts, side in s['flow'] if ts >= e.ts-cfg['window_ms'])
            reason = None
            if sell-o['price']-2*FEE < cfg['net_floor']:
                reason = 'net_space_below_extra_floor'
            elif min(counts['buy'], counts['sell']) < cfg['min_each']:
                reason = 'insufficient_recent_flow_count'
            elif entry_cash_limit is not None and o['price']+FEE > entry_cash_limit+s['cash']-before_cash:
                reason = 'shared_cash_reserved_elsewhere'
            if reason:
                new_order = any(x['id'] == o['id'] for x in result['orders'])
                if new_order:
                    result['orders'] = [x for x in result['orders'] if x['id'] != o['id']]
                    s['order_count'] -= 1
                    s['order'] = None
                else:
                    result['cancels'] += self._cancel(e.ts, reason)
                s['rejects'][reason] = s['rejects'].get(reason, 0)+1
                s['latest_reason'] = reason
        return result

    def summary(self, asof_ms=None):
        value = super().summary(asof_ms)
        value.update(mode=self.profile, profile=dict(PROFILES[self.profile]),
                     rejection_frames=dict(self.s['rejects']))
        return value


class SharedPortfolio:
    def __init__(self, instruments, profile, initial_cents, label=None):
        if type(initial_cents) is not int or initial_cents <= 0:
            raise ValueError('Positive fixed portfolio cash required')
        self.initial = self.cash = initial_cents
        self.profile = profile
        self.model = label or f'{FAMILY}_{profile}_shared_{initial_cents//100}'
        self.accounts = {c: ResearchAccount(c, profile, v['initial_cents'], v['tick_cents'],
            suffix=f'shared_{initial_cents//100}') for c,v in instruments.items()}
        self.reserves = {}
        self.reserved = self.marked = self.pnl = self.basis = 0
        self.peak_basis = self.peak_reserved_and_basis = 0
        self.peak = initial_cents
        self.max_dd = self.max_dd_pct = 0
        self.orders, self.fills, self.cycles, self.curve = [], [], [], []
        self.daily = {}
        self.session = None
        self.last_key = None

    def step(self, code, e, date):
        key = (e.ts, code)
        if self.last_key is not None and key <= self.last_key:
            raise ValueError('Global market timestamp and code order required')
        self.last_key = key
        session = (date, e.session % 10)
        if session != self.session:
            # No synthetic active quote may cross a daily trading break.
            for a in self.accounts.values():
                a._cancel(e.ts, 'global_session_change')
            self.reserves.clear()
            self.reserved = 0
            self.session = session
        a = self.accounts[code]
        cash_before, pnl_before = a.s['cash'], a.s['last_pnl']
        mark_before = a.s['inventory']*a.s['last_bid']
        basis_before = a.s['basis']
        self.reserved -= self.reserves.pop(code, 0)
        d = a.step(e, date, entry_cash_limit=self.cash-self.reserved)
        self.cash += a.s['cash']-cash_before
        self.pnl += a.s['last_pnl']-pnl_before
        self.marked += a.s['inventory']*a.s['last_bid']-mark_before
        self.basis += a.s['basis']-basis_before
        if a.s['order'] and a.s['order']['side'] == 'buy':
            self.reserves[code] = a.s['order']['price']+FEE
            self.reserved += self.reserves[code]
        self.peak_basis = max(self.peak_basis, self.basis)
        self.peak_reserved_and_basis = max(self.peak_reserved_and_basis, self.reserved+self.basis)
        if not (0 <= self.reserved <= self.cash and self.cash+self.marked == self.initial+self.pnl):
            raise ValueError('Shared funding or equity invariant violated')
        self.orders.extend(d['orders'])
        self.fills.extend(dict(f, portfolio_model_id=self.model,
            portfolio_cash_cents=self.cash, portfolio_reserved_cents=self.reserved) for f in d['fills'])
        self.cycles.extend(dict(c, code=code) for c in d['cycles'])
        return d

    def mark(self, ts, date):
        """Call only after ALL events at this timestamp, avoiding tie-order DD."""
        equity = self.initial+self.pnl
        self.peak = max(self.peak, equity)
        self.max_dd = max(self.max_dd, self.peak-equity)
        self.max_dd_pct = max(self.max_dd_pct, (self.peak-equity)/self.peak*100)
        if not self.curve or self.curve[-1][1] != self.pnl:
            self.curve.append([ts, self.pnl])
        self.daily[date] = dict(date=date, cumulative_pnl_cny=self.pnl/100,
                               equity_cny=equity/100, last_ts=ts)

    def result(self):
        summaries = {c: a.summary() for c,a in self.accounts.items()}
        assert sum(a.s['cash']-a.s['initial'] for a in self.accounts.values())+self.initial == self.cash
        assert sum(a.s['last_pnl'] for a in self.accounts.values()) == self.pnl
        ds, previous = [], 0
        for d in self.daily.values():
            net = int(round(d['cumulative_pnl_cny']*100))
            ds.append(dict(d, pnl_cny=(net-previous)/100))
            previous = net
        fees = sum(a.s['fees'] for a in self.accounts.values())
        s = dict(model_id=self.model, profile=self.profile, initial_cash_cny=self.initial/100,
            pnl_cny=self.pnl/100, return_pct=self.pnl/self.initial*100,
            max_drawdown_cny=self.max_dd/100, max_drawdown_pct=self.max_dd_pct,
            fees_cny=fees/100, fills=len(self.fills), cycles=len(self.cycles),
            tail_contracts=sum(a.s['inventory'] for a in self.accounts.values()),
            end_cash_cny=self.cash/100, end_reserved_cny=self.reserved/100,
            peak_premium_cost_cny=self.peak_basis/100,
            peak_reserved_plus_cost_cny=self.peak_reserved_and_basis/100,
            funding_rejection_frames=sum(a.s['rejects'].get('shared_cash_reserved_elsewhere',0) for a in self.accounts.values()),
            same_day_cycle_net_cny=sum(c['net_cents'] for c in self.cycles if (c['entry_ts']//86400000)==(c['exit_ts']//86400000))/100,
            open_cycle_contribution_cny=(self.pnl-sum(c['net_cents'] for c in self.cycles))/100,
            completed_cycle_net_cny=sum(c['net_cents'] for c in self.cycles)/100,
            per_contract_virtual_ceiling_retained=True, same_timestamp_order='code_ascending',
            fee_per_side_cny=1.7, delay_ms=0, maximum_contract_inventory=1,
            evidence='historical_L1_latest_contract_only', never_broker_orders=True)
        # All events are 09:00-15:00 China time, so UTC and local day partitions agree here.
        return dict(summary=s, daily=ds, accounts={c: dict(summary=summaries[c],daily=a.daily())
            for c,a in self.accounts.items()}, orders=self.orders, fills=self.fills,
            cycles=self.cycles, curve=self.curve)
