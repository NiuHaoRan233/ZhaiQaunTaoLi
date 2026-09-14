"""Offline gold break exclusion, causal futures cancellation and displayed support.

New family; immutable parent engines and saved accounts are never modified.
Money is cents/contract, size is exchange contracts. L1 support is a price level,
not an identified participant/order. Strict fills require penetration, not queue.
"""
from collections import deque
from dataclasses import dataclass, replace, asdict
from math import ceil, floor
from . import gold_direction_research as g
from . import gold_rule_ladder_research as ladder
from . import gold_spread_future_research as base
from . import gold_spread_quote_control as quote

FAMILY = 'probe_gold_backer_20260914_v1'


@dataclass(frozen=True)
class Policy:
    parent: str = 'trend_long'
    cancel: bool = False
    fast: bool = False
    exit: bool = False
    backer: bool = False
    guard: bool = False


POLICIES = {
    'trend': Policy(),
    'switch': Policy(parent='cancel_switch'),
    'value': Policy(parent='throttle_long'),
    'trend_cancel': Policy(cancel=True),
    'trend_fast': Policy(cancel=True, fast=True),
    'trend_exit': Policy(cancel=True, fast=True, exit=True),
    'value_exit': Policy(parent='throttle_long', cancel=True, fast=True, exit=True),
    'backer_entry': Policy(parent='base_long', backer=True),
    'backer_guard': Policy(parent='base_long', backer=True, guard=True),
    'backer_fast': Policy(parent='base_long', cancel=True, fast=True, backer=True, guard=True),
}


def fast_bad(feature, direction, tick, anchor=None):
    """Fixed risk budget, never widened by the current option spread."""
    if not feature or not feature.get('ready'):
        return True, 'future_unavailable'
    for seconds in (2, 10):
        move = feature.get(f'move{seconds}_cents')
        if move is not None and direction * move <= -2 * tick:
            return True, f'future_{seconds}s_two_ticks'
    if anchor and anchor.get('future_mid') is not None:
        impact = direction * feature['delta'] * (feature['future_mid'] - anchor['future_mid']) * 100000
        if impact <= -2 * tick:
            return True, 'future_since_order_two_ticks'
    return False, 'future_ok'


class Account(base.Account):
    def __init__(self, code, policy, strike, through=False, capital=250000, settlement='exclude'):
        if settlement not in ('exclude', 'cost', 'market'):
            raise ValueError('Unknown settlement')
        self.policy = POLICIES[policy]; self.policy_name = policy
        super().__init__(code, self.policy.parent, strike, 8, through, capital, 0)
        if self.policy.parent in quote.PROFILES:
            self.profile_cfg = replace(self.profile_cfg, throttle=True)
        self.settlement = 'market' if settlement == 'market' else 'cost'
        self.research_settlement = settlement
        self.model = f'{FAMILY}_{policy}_{settlement}_{code}_capital{self.initial}_through{int(through)}'
        self.support_history = deque(); self.pending_support = None; self.support = None
        self.risk_events = []; self.adjustments = []; self.guard_waiting = False

    def issue(self, ts, side, price, reason, e, feature=None):
        if self.policy.parent in quote.PROFILES:
            quote.Account.issue(self, ts, side, price, reason, e, feature)
        else:
            super().issue(ts, side, price, reason, e, feature)
        if reason == 'entry' and self.order and self.order['created_ts'] == ts and self.policy.backer:
            self.order['support'] = dict(self.pending_support)
            self.rows['orders'][-1]['support'] = dict(self.pending_support)

    def candidate(self, e, feature):
        c, reason = super().candidate(e, feature)
        if not c:
            return c, reason
        feat = self.features[self.direction]
        if self.policy.fast:
            bad, why = fast_bad(feat, self.direction, self.tick)
            if bad: return None, why
        if self.policy.backer:
            # Stable CURRENT best bid, all observed updates over the preceding 2s.
            history = [x for x in self.support_history if x[0] >= e.ts - 2000]
            prior = next((x for x in reversed(self.support_history) if x[0] <= e.ts - 2000), None)
            if prior: history.insert(0, prior)
            if not (prior and len(history) >= 3 and e.ts-prior[0] <= 3000 and
                    all(p == e.bid and q >= 10 for _, p, q in history) and e.bid_qty >= 10):
                return None, 'no_stable_ten_contract_bid'
            if not feat or not feat.get('ready'): return None, 'support_future_unavailable'
            self.pending_support = dict(price=e.bid, initial_qty=e.bid_qty, minimum_qty=e.bid_qty,
                observed_qty=e.bid_qty, trade_evidence_contracts=0, stable_since_ts=prior[0],
                last_observed_ts=e.ts)
        return c, reason

    def fill(self, ts, price, side, kind, e=None, feature=None):
        opening = not self.inventory
        support = self.order.get('support') if opening and self.order else None
        old_count = len(self.rows['fills'])
        super().fill(ts, price, side, kind, e, feature)
        if len(self.rows['fills']) == old_count: return
        if opening:
            self.support = dict(support) if support else None
            self.guard_waiting = False
            self.cycle['support_at_entry_order'] = dict(support) if support else None
            if self.support and e:
                self.update_support(e)
        else:
            self.support = None; self.guard_waiting = False
        if kind == 'risk_market_close':
            self.rows['fills'][-1].update(source_quote_ts=e.ts, source_bid=e.bid,
                source_ask=e.ask, source_qty=e.bid_qty if side == 'sell' else e.ask_qty)

    def update_support(self, e):
        s = self.support
        if not s or e.ts <= s['last_observed_ts']: return
        qty = next((q for p, q in e.bids if p == s['price']), 0)
        drop = max(0, s['observed_qty'] - qty)
        # At most ONE confirmed last-price contract from an aggregate interval.
        # Quantity loss by itself is not executed consumption.
        if drop and e.quantity > 0 and e.single and e.strict_side == 'sell' and e.last == s['price']:
            s['trade_evidence_contracts'] += min(1, drop)
        s.update(observed_qty=qty, minimum_qty=min(s['minimum_qty'], qty), last_observed_ts=e.ts)

    def request_cancel(self, f, feature, reason):
        o = self.order
        if o and o.get('cancel_ts') is None:
            o['cancel_ts'] = f.ts
            self.rows['cancels'].append(dict(model_id=self.model, code=self.code,
                order_id=o['id'], ts=f.ts, reason=reason, feature=feature,
                conservative_interval_settlement=True))

    def future_event(self, f, features):
        super().future_event(f, features)
        o = self.order; b = self.last_book
        if self.policy.cancel and o and not self.inventory:
            d = 1 if o['side'] == 'buy' else -1; feat = features[d]
            if self.policy.fast: bad, why = fast_bad(feat, d, self.tick, o.get('feature'))
            else:
                bad = not (b and base.trend_ok(feat, d, b.ask-b.bid, self.tick))
                why = 'original_trend_invalid'
            if bad: self.request_cancel(f, feat, 'future_risk_' + why)
        # Futures updates request cancellation only. A holding exit waits for a
        # fresh option quote, whose preceding aggregate interval is settled first.
        # This never invents execution at a stale, already-consumed support price.

    def risk_exit(self, e, feature):
        if not self.inventory: return False
        d = self.inventory; c = self.cycle
        self.update_support(e)
        anchor = c.get('entry_fill_future') or c.get('entry_signal')
        bad, reason = fast_bad(feature, d, self.tick, anchor)
        # Missing futures data is an entry/cancellation restriction, not proof of
        # adverse prices and never by itself an aggressive liquidation signal.
        adverse = bad and reason != 'future_unavailable'
        close = self.policy.exit and adverse
        s = self.support
        if self.policy.guard and s and adverse:
            if s['observed_qty'] == 0:
                close = True
                reason = ('support_disappeared_after_trade_evidence' if s['trade_evidence_contracts']
                          else 'support_disappeared_unclassified')
            elif s['observed_qty'] <= s['initial_qty'] / 2 and s['trade_evidence_contracts'] >= 1:
                close = True; reason = 'support_depleted_with_trade_evidence'
        if not close: return False
        side = 'sell' if d == 1 else 'buy'
        price = e.bid if d == 1 else e.ask
        qty = e.bid_qty if d == 1 else e.ask_qty
        if qty < 1 or price <= 0: return False
        evidence = dict(ts=e.ts, entry_ts=c['entry_ts'], reason=reason, feature=feature,
            support=dict(s) if s else None, exit_price_cents=price,
            loss_from_entry_cents=d*(price-c['entry_price_cents']))
        self.issue(e.ts, side, price, 'risk_market_close', e, feature)
        self.fill(e.ts, price, side, 'risk_market_close', e, feature)
        self.risk_events.append(evidence)
        return True

    def boundary(self, ts):
        n = len(self.rows['cycles'])
        super().boundary(ts)
        if len(self.rows['cycles']) > n and self.research_settlement == 'exclude':
            c = self.rows['cycles'][-1]
            self.cash += 2*g.FEE; self.fees -= 2*g.FEE; self.completed += 2*g.FEE
            c.update(fees_cents=0, net_cents=0, excluded_from_research=True,
                     excluded_original_fees_cents=2*g.FEE)
            self.adjustments.append(dict(ts=ts, entry_ts=c['entry_ts'], amount_cents=2*g.FEE,
                reason='exclude_break_cycle_fee_refund'))
            self.mark(ts)
        self.support_history.clear(); self.support = None

    def result(self):
        r = super().result(); s = r['summary']
        r.update(risk_events=self.risk_events, fee_adjustments=self.adjustments)
        s.update(policy=self.policy_name, policy_rules=asdict(self.policy),
            research_settlement=self.research_settlement,
            virtual_cycle_net_cny=sum(c['net_cents'] for c in r['cycles'] if c['exit_kind']=='virtual_cost_close')/100,
            refunded_fees_cny=sum(x['amount_cents'] for x in self.adjustments)/100,
            risk_close_count=len(self.risk_events), normal_entry_until_break=True)
        return r

    def option(self, e):
        if self.last and e.ts <= self.last.ts: raise ValueError('Unordered option')
        if any(b <= e.ts and b not in self.checked for b in g.BOUNDARIES): raise ValueError('Unsettled break')
        if e.session != self.session:
            if self.inventory: raise ValueError('Cross-break exposure')
            self.cancel(e.ts, 'session_change'); self.flow.clear(); self.session = e.session
        if self.order and self.order.get('cancel_ts') is not None and self.order['cancel_ts'] <= e.previous_ts:
            self.order = None
        d = self.inventory or (1 if self.order and self.order['side'] == 'buy' else -1 if self.order else self.direction)
        feature = self.features[d]
        self.value.current = feature
        self.support_history.append((e.ts, e.bid, e.bid_qty))
        while self.support_history and self.support_history[0][0] < e.ts - 4000:
            self.support_history.popleft()
        if e.ts < g.CUTOFF: self.last = e; return
        o = self.order
        if o and o['active_ts'] <= e.previous_ts and o['created_ts'] < e.ts and e.quantity > 0 and e.single:
            opposite = 'sell' if o['side'] == 'buy' else 'buy'
            reachable = e.last <= o['price'] if o['side'] == 'buy' else e.last >= o['price']
            if e.strict_side == opposite and reachable: self.fill(e.ts, o['price'], o['side'], 'passive', e, feature)
        if not g.valid(e) or (self.last and e.ts-self.last.ts > 60000): self.flow.clear()
        while self.flow and self.flow[0][0] < e.ts-300000: self.flow.popleft()
        if g.valid(e) and e.quantity > 0 and e.single and e.strict_side in ('buy','sell'): self.flow.append((e.ts,e.strict_side))
        if g.valid(e):
            self.last_book = e; boundary = g.BOUNDARIES[e.session]
            if self.research_settlement == 'market' and e.ts >= boundary-5000:
                self.cancel(e.ts, 'pre_break_market_window')
                if self.inventory:
                    side = 'sell' if self.inventory == 1 else 'buy'; price = e.bid if side == 'sell' else e.ask
                    self.issue(e.ts,side,price,'pre_break_market_close',e,feature)
                    self.fill(e.ts,price,side,'pre_break_market_close',e)
            elif self.inventory and self.risk_exit(e, self.features[self.inventory]):
                pass
            elif self.inventory:
                d=self.inventory; c=self.cycle
                if e.ts-c['entry_ts'] >= 300000: self.released=True
                if self.rules.adverse_release:
                    if self.last and (e.ts-self.last.ts > 60000 or not g.valid(self.last)): self.risk_since=None
                    if d*((e.bid+e.ask)-c['entry_mid_twice']) < -2*c['entry_spread']:
                        if self.risk_since is None: self.risk_since=e.ts
                        if e.ts-self.risk_since >= 30000: self.released=True
                    else: self.risk_since=None
                improvement=self.tick if self.rules.improve and e.ask-e.bid > self.tick else 0
                price=e.ask-improvement if d == 1 else e.bid+improvement
                if self.rules.patient and not self.released:
                    limit=c['entry_price_cents']+d*(2*g.FEE+self.tick)
                    limit=ceil(limit/self.tick)*self.tick if d==1 else floor(limit/self.tick)*self.tick
                    price=max(price,limit) if d==1 else min(price,limit)
                side='sell' if d==1 else 'buy'
                if price > 0 and (not self.order or (self.order['side'],self.order['price']) != (side,price)):
                    self.issue(e.ts,side,price,'patient_exit' if self.rules.patient and not self.released else 'top_exit',e,feature)
            else:
                c,reason=self.candidate(e,feature)
                if c:
                    if self.direction==1: reserve=c['price']+g.FEE
                    else:
                        f=self.value.future
                        if not(f and f.source_ts < e.ts and e.ts-f.source_ts <= 2000):
                            self.rejects['short_funding_source_stale']+=1; c=None
                        else: reserve=ceil(f.mid*100000*.20)+c['price']+2*g.FEE
                    if c:
                        # Required starting cash to preserve EVERY eligible quote, including unfilled orders.
                        self.demands.append([e.ts, reserve-(self.cash-self.initial), reserve])
                        if self.cash < reserve: self.rejects['insufficient_risk_capital']+=1; c=None
                if c:
                    side='buy' if self.direction==1 else 'sell'; price=c['price']; self.max_reserve=max(self.max_reserve,reserve)
                    if not self.order or (self.order['side'],self.order['price']) != (side,price): self.issue(e.ts,side,price,'entry',e,feature)
                else:
                    if reason != 'eligible': self.rejects[reason]+=1
                    self.cancel(e.ts,'entry_gate')
        else: self.cancel(e.ts,'invalid_book')
        self.last=e; self.mark(e.ts)
        if self.inventory==-1 and self.value.future and self.last_book:
            reserve=ceil(self.value.future.mid*100000*.20)+self.last_book.ask
            self.max_reserve=max(self.max_reserve,reserve)
            if self.cash-self.last_book.ask < reserve: self.margin_breaches+=1
