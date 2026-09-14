"""Bounded, offline mAu-inspired ablations; option own-price references are NOT fair value.

Frozen parent execution, daily entry-cost settlement and cash accounting are retained.
New entry gates run after old-order settlement. No market or broker connections.
"""
from collections import deque
from math import expm1

from .commodity_intraday_research import IntradayAccount, IntradayPortfolio, close_timestamp
from .commodity_flow_strategy import FEE

FAMILY = 'probe_commodity_mau_transfer_20260913_v1'
PROFILES = {
    'control': dict(top=False, gap=False, reference=False, cutoff=False),
    'top_exit': dict(top=True, gap=False, reference=False, cutoff=False),
    'gap': dict(top=False, gap=True, reference=False, cutoff=False),
    'reference': dict(top=False, gap=False, reference=True, cutoff=False),
    'cutoff': dict(top=False, gap=False, reference=False, cutoff=True),
    'combined': dict(top=False, gap=True, reference=True, cutoff=True),
    'combined_top': dict(top=True, gap=True, reference=True, cutoff=True),
}


class OwnReference:
    """Current and prior option midpoints only, reset at trading-session boundaries."""
    def __init__(self):
        self.session = self.last = self.first = None
        self.fast = self.slow = None
        self.count = 0
        self.history = deque()

    def update(self, e):
        valid = 0 < e.bid < e.ask and min(e.bid_qty, e.ask_qty) > 0
        if not valid or e.session != self.session or (self.last is not None and e.ts-self.last > 60000):
            self.first = self.fast = self.slow = None
            self.count = 0
            self.history.clear()
        self.session = e.session
        previous = self.last
        self.last = e.ts
        if not valid:
            return None
        mid = (e.bid+e.ask)/2
        if self.first is None:
            self.first = e.ts
            self.fast = self.slow = mid
        else:
            dt = (e.ts-previous)/1000
            self.fast += -expm1(-dt/150)*(mid-self.fast)
            self.slow += -expm1(-dt/900)*(mid-self.slow)
        self.count += 1
        self.history.append((e.ts, mid))
        while len(self.history) > 1 and self.history[1][0] <= e.ts-10000:
            self.history.popleft()
        old = self.history[0]
        change = mid-old[1] if old[0] <= e.ts-10000 else None
        return dict(ready=e.ts-self.first >= 60000 and self.count >= 3,
                    conservative=min(self.fast, self.slow, mid), change10=change,
                    fast=self.fast, slow=self.slow, ts=e.ts)


def gate(e, order_price, tick, cfg, feature, date):
    if cfg['cutoff'] and e.ts >= close_timestamp(date)-300000:
        return 'last_five_minutes'
    if cfg['gap']:
        second = next((p for p, q in e.bids if 0 < p < e.bid and q > 0), None)
        if second is None:
            return 'bid2_unavailable'
        if e.bid-second >= max(2*tick, e.ask-e.bid):
            return 'isolated_bid1'
    if cfg['reference']:
        if feature is None or not feature['ready']:
            return 'own_reference_warmup'
        if feature['change10'] is not None and feature['change10'] <= -max(2*tick, (e.ask-e.bid)/2):
            return 'own_adverse_10s'
        if feature['conservative']-order_price-2*FEE < max(tick, 2*FEE):
            return 'own_reference_net_edge'
    return None


class TransferAccount(IntradayAccount):
    def __init__(self, code, profile, initial_cents, tick_cents, suffix='independent'):
        super().__init__(code, 'edge10', initial_cents, tick_cents, suffix)
        self.transfer_profile = profile
        self.cfg = PROFILES[profile]
        self.model = f'{FAMILY}_{profile}_{suffix}_cost_close'
        self.s['model_id'] = self.model
        if self.cfg['top']:
            self.variant = 'flow_entry'
        self.reference = OwnReference()
        self.entry_context = {}

    def step(self, e, date, entry_cash_limit=None):
        # Parent first: never remove a fill because its arrival carries adverse news.
        old = self.s['order']
        old_context = self.entry_context.get(old['id']) if old else None
        result = super().step(e, date, entry_cash_limit)
        for f in result['fills']:
            if f['side'] == 'buy':
                f['entry_signal'] = old_context
        feature = self.reference.update(e) if self.cfg['reference'] else None
        order = self.s['order']
        if order and order['side'] == 'buy':
            reason = gate(e, order['price'], self.tick, self.cfg, feature, date)
            if reason:
                new = any(o['id'] == order['id'] for o in result['orders'])
                if new:
                    result['orders'] = [o for o in result['orders'] if o['id'] != order['id']]
                    self.s['order_count'] -= 1
                    self.s['order'] = None
                else:
                    result['cancels'] += self._cancel(e.ts, reason)
                self.s['rejects'][reason] = self.s['rejects'].get(reason, 0)+1
                self.s['latest_reason'] = reason
            elif any(o['id'] == order['id'] for o in result['orders']):
                self.entry_context = {order['id']: dict(ts=e.ts, bid=e.bid, ask=e.ask,
                    bids=e.bids, own_reference=feature)}
        return result

    def summary(self, asof_ms=None):
        s = super().summary(asof_ms)
        s.update(mode=self.transfer_profile, profile=dict(self.cfg), parent_profile='edge10',
                 reference_kind='option_own_midpoint_not_cross_market_fair_value')
        return s


class TransferPortfolio(IntradayPortfolio):
    def __init__(self, instruments, profile, initial_cents):
        super().__init__(instruments, 'edge10', initial_cents)
        self.profile = profile
        self.model = f'{FAMILY}_{profile}_shared_{initial_cents//100}_cost_close'
        self.accounts = {c: TransferAccount(c, profile, v['initial_cents'], v['tick_cents'],
            f'shared_{initial_cents//100}') for c, v in instruments.items()}
