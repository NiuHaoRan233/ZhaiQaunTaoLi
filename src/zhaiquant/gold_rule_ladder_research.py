"""Offline gold rule ladder. New identities; immutable parents are not patched.

All monetary engine fields are integer cents per one option contract. The shared
valuation snapshot is supplied BEFORE processing the current option quote.
"""
from dataclasses import dataclass, asdict
from math import ceil, floor
from . import gold_direction_research as g

FAMILY = 'probe_gold_rule_ladder_20260913_v1'


@dataclass(frozen=True)
class Rules:
    improve: bool = False
    net_spread: bool = False
    flow: bool = False
    second: bool = False
    gap: bool = False
    patient: bool = False
    adverse_release: bool = False
    warm: bool = False
    value: bool = False
    fast: bool = False
    min_spread_ticks: int = 0


STEPS = [
    ('s00', '原买一卖一循环', {}),
    ('s01', '加：双侧改善一跳', dict(improve=True)),
    ('s02', '加：扣费净价差至少一跳', dict(net_spread=True)),
    ('s03', '加：300秒双向成交证据', dict(flow=True)),
    ('s04', '加：存在有效第二档', dict(second=True)),
    ('s05', '加：过滤一二档断层', dict(gap=True)),
    ('s06', '加：成本底价保护，最多300秒', dict(patient=True)),
    ('s07', '加：逆向持续30秒提前解除保护', dict(adverse_release=True)),
    ('s08', '加：估值预热与新鲜度门槛', dict(warm=True)),
    ('s09', '加：60秒IV估值优势门槛', dict(value=True)),
    ('s10', '加：10/60秒保守IV', dict(fast=True)),
]


def cases():
    result = {}; cfg = {}
    for key, label, changes in STEPS:
        cfg.update(changes)
        result[key] = dict(label=label, group='ladder', rules=Rules(**cfg))
    removals = {
        'no_spread': ('删：净价差门槛', dict(net_spread=False)),
        'no_flow': ('删：双向成交证据', dict(flow=False)),
        'no_depth': ('删：第二档及依赖它的断层过滤', dict(second=False, gap=False)),
        'no_gap': ('删：断层过滤，仍要求第二档', dict(gap=False)),
        'no_patient': ('删：成本保护及其释放规则', dict(patient=False, adverse_release=False)),
        'no_adverse': ('删：逆向提前释放，仍300秒到期', dict(adverse_release=False)),
        'no_value': ('删：估值及其数据门槛', dict(value=False, warm=False, fast=False)),
        'slow_only': ('删：快慢取保守值，退回60秒', dict(fast=False)),
    }
    for key, (label, changes) in removals.items():
        result[key] = dict(label=label, group='removal', rules=Rules(**{**cfg, **changes}))
    for ticks in (3, 4, 5, 6, 8, 10):
        result[f'spread{ticks}'] = dict(label=f'仅改善报价＋原始价差≥{ticks}跳', group='spread',
            rules=Rules(improve=True, min_spread_ticks=ticks))
    return result


class SnapshotValue:
    def __init__(self): self.future = None; self.current = None
    def feature(self, ts): return self.current
    def new_session(self, session): pass
    def observe_option(self, e): pass


class Account(g.Account):
    def __init__(self, code, case, direction, strike, capital_cny=250000, through=False):
        super().__init__(code, 'long' if direction == 1 else 'short', 'market', strike)
        self.rules = cases()[case]['rules']; self.case = case; self.direction = direction
        self.initial = round(capital_cny * 100); self.cash = self.initial; self.peak = self.initial
        self.value = SnapshotValue(); self.through = through; self.touch_rejections = 0
        self.model = f'{FAMILY}_{case}_{"long" if direction == 1 else "short"}_market_{code}_capital{self.initial}_through{int(through)}'
        self.demands = []

    def candidate(self, e, feature):
        r = self.rules; d = self.direction; spread = e.ask - e.bid
        improvement = self.tick if r.improve and spread > self.tick else 0
        price = e.bid + improvement if d == 1 else e.ask - improvement
        if spread < r.min_spread_ticks * self.tick: return None, 'raw_spread'
        if r.net_spread and spread - 2 * improvement - 2 * g.FEE < self.tick: return None, 'net_edge'
        if r.flow and {side for _, side in self.flow} != {'buy', 'sell'}: return None, 'two_sided_flow'
        if r.second or r.gap:
            depth = e.bids if d == 1 else e.asks; top = e.bid if d == 1 else e.ask
            second = next((p for p, q in depth if q > 0 and d * (top - p) > 0), None)
            if second is None: return None, 'missing_second_depth'
            if r.gap and abs(top - second) >= max(2 * self.tick, spread): return None, 'isolated_top'
        if (r.warm or r.value) and not (feature and feature['ready']): return None, 'value_warmup_or_stale'
        edge = d * (feature['fair_cents'] - price) - 2 * g.FEE if feature else None
        if r.value and edge < max(self.tick, spread / 4): return None, 'fair_edge'
        return dict(price=price, edge_cents=edge), 'eligible'

    def fill(self, ts, price, side, kind, e=None, feature=None):
        if self.through and kind == 'passive' and e.last == price:
            self.touch_rejections += 1
            return
        super().fill(ts, price, side, kind, e, feature)

    def mark(self, ts):
        b = self.last_book
        mark = (b.bid if self.inventory == 1 else b.ask) if b and self.inventory else 0
        pnl = self.cash + self.inventory * mark - self.initial
        self.peak = max(self.peak, self.initial + pnl); self.dd = max(self.dd, self.peak-self.initial-pnl)
        point = [ts, pnl, self.inventory]
        if self.rows['curve'] and self.rows['curve'][-1][0] == ts: self.rows['curve'][-1] = point
        elif not self.rows['curve'] or self.rows['curve'][-1][1:] != point[1:]: self.rows['curve'].append(point)
        assert pnl == self.gross-self.fees+(self.inventory*(mark-self.cycle['entry_price_cents']) if self.cycle else 0)

    def option(self, e):
        if self.last and e.ts <= self.last.ts: raise ValueError('Unordered option')
        if any(b <= e.ts and b not in self.checked for b in g.BOUNDARIES): raise ValueError('Unsettled break')
        if e.session != self.session:
            if self.inventory: raise ValueError('Cross-break exposure')
            self.cancel(e.ts, 'session_change'); self.flow.clear(); self.session = e.session
        feature = self.value.feature(e.ts)
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
            if e.ts >= boundary-5000:
                self.cancel(e.ts, 'pre_break_market_window')
                if self.inventory:
                    side = 'sell' if self.inventory == 1 else 'buy'; price = e.bid if side == 'sell' else e.ask
                    self.issue(e.ts,side,price,'pre_break_market_close',e,feature)
                    self.fill(e.ts,price,side,'pre_break_market_close',e)
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

    def result(self):
        r=super().result(); s=r['summary']
        s.update(profile=self.case, rules=asdict(self.rules), initial_cash_cny=self.initial/100,
            pnl_cny=(self.cash-self.initial)/100 if not self.inventory else self.rows['curve'][-1][1]/100,
            minimum_cash_for_all_entry_quotes_cny=max((x[1] for x in self.demands),default=0)/100,
            strict_through=self.through, rejected_touch_only_attempts=self.touch_rejections)
        r['funding_demands']=self.demands
        return r
