"""Fixed long and fixed short counterparts of frozen gold backer policies."""
from collections import deque
from dataclasses import replace
from . import gold_backer_research as parent

FAMILY = 'probe_gold_two_mode_20260914_v1'
POLICIES = tuple(p for p in parent.POLICIES if p != 'switch')


class Account(parent.Account):
    def __init__(self, code, policy, strike, direction, through=False, capital=250000):
        if direction not in (1, -1) or policy not in POLICIES:
            raise ValueError('Fixed long/short policy required')
        super().__init__(code, policy, strike, through, capital, 'exclude')
        self.fixed_direction = direction
        self.direction = direction
        self.profile_cfg = replace(self.profile_cfg, direction='long' if direction == 1 else 'short')
        self.mode = 'long' if direction == 1 else 'short'
        self.model = f'{FAMILY}_{self.mode}_{policy}_exclude_{code}_capital{self.initial}_through{int(through)}'
        self.side_history = deque()

    def option(self, e):
        if self.session != e.session:
            self.side_history.clear()
        price, qty = (e.bid, e.bid_qty) if self.fixed_direction == 1 else (e.ask, e.ask_qty)
        self.side_history.append((e.ts, price, qty))
        while self.side_history and self.side_history[0][0] < e.ts - 4000:
            self.side_history.popleft()
        super().option(e)

    def candidate(self, e, feature):
        if not self.policy.backer:
            return super().candidate(e, feature)
        # Preserve the original candidate except mirror the displayed support side.
        c, reason = parent.base.Account.candidate(self, e, feature)
        if not c: return c, reason
        feat = self.features[self.direction]
        if self.policy.fast:
            bad, why = parent.fast_bad(feat, self.direction, self.tick)
            if bad: return None, why
        price, qty = (e.bid, e.bid_qty) if self.direction == 1 else (e.ask, e.ask_qty)
        history = [x for x in self.side_history if x[0] >= e.ts - 2000]
        prior = next((x for x in reversed(self.side_history) if x[0] <= e.ts - 2000), None)
        if prior: history.insert(0, prior)
        if not (prior and len(history) >= 3 and e.ts-prior[0] <= 3000 and
                all(p == price and q >= 10 for _, p, q in history) and qty >= 10):
            return None, 'no_stable_ten_contract_bid' if self.direction == 1 else 'no_stable_ten_contract_ask'
        if not feat or not feat.get('ready'): return None, 'support_future_unavailable'
        self.pending_support = dict(price=price, initial_qty=qty, minimum_qty=qty,
            observed_qty=qty, trade_evidence_contracts=0, stable_since_ts=prior[0], last_observed_ts=e.ts)
        return c, reason

    def update_support(self, e):
        s = self.support
        if not s or e.ts <= s['last_observed_ts']: return
        depth = e.bids if self.fixed_direction == 1 else e.asks
        aggressor = 'sell' if self.fixed_direction == 1 else 'buy'
        qty = next((q for p, q in depth if p == s['price']), 0)
        drop = max(0, s['observed_qty']-qty)
        if drop and e.quantity > 0 and e.single and e.strict_side == aggressor and e.last == s['price']:
            s['trade_evidence_contracts'] += min(1, drop)
        s.update(observed_qty=qty, minimum_qty=min(s['minimum_qty'], qty), last_observed_ts=e.ts)

    def result(self):
        r = super().result()
        r['summary'].update(trade_mode=self.mode, fixed_direction=self.fixed_direction,
            prototype_policy_parent=self.policy.parent, policy_parent_direction=self.mode)
        return r
