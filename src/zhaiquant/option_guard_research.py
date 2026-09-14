"""Causal, offline option entry filters and a separately identified exit experiment.

Integer cents per contract; v1 remains immutable. No market or broker connection.
"""
from bisect import bisect_right
from collections import Counter, deque
from dataclasses import dataclass
from statistics import median

from .option_top_cycle_research import MODES

FAMILY = 'probe_option_dadao_guard_20260909_v2'
PROFILES = ('control', 'spread', 'trend', 'gap', 'entry', 'risk', 'entry5', 'entry15')


@dataclass(frozen=True)
class Feature:
    ts: int
    ready: bool
    change10: float | None
    change60: float | None
    range60: float
    gap: int | None


def features(events):
    """Past/current observations only, session-reset, no interpolation from the future."""
    times, mids, result = [], [], []
    low, high = deque(), deque()
    session = None
    for e in events:
        if e.session != session:
            times, mids = [], []
            low.clear(); high.clear()
            session = e.session
        valid = 0 < e.bid < e.ask and e.bid_qty > 0 and e.ask_qty > 0
        mid = (e.bid + e.ask) / 2
        if valid:
            times.append(e.ts); mids.append(mid)
            while low and low[-1][1] >= mid: low.pop()
            while high and high[-1][1] <= mid: high.pop()
            low.append((e.ts, mid)); high.append((e.ts, mid))
        while low and low[0][0] < e.ts - 60000: low.popleft()
        while high and high[0][0] < e.ts - 60000: high.popleft()
        def change(seconds):
            target = e.ts - seconds * 1000
            i = bisect_right(times, target) - 1
            return mid - mids[i] if valid and i >= 0 and target - times[i] <= 2000 else None
        c10, c60 = change(10), change(60)
        bid2 = next((p for p, q in e.bids if p < e.bid and q > 0), None)
        result.append(Feature(e.ts, valid and len(times) >= 3 and c10 is not None and c60 is not None,
                              c10, c60, high[0][1] - low[0][1] if low else 0,
                              e.bid - bid2 if bid2 is not None else None))
    return result


def entry_reasons(e, f, profile, fee_cents, tick_cents, queued):
    if profile == 'control': return []
    reasons = []
    spread = e.ask - e.bid
    inward = 0 if queued or spread <= tick_cents else 2 * tick_cents
    edge = spread - inward - 2 * fee_cents
    floor = 500 if profile == 'entry5' else 1500 if profile == 'entry15' else 1000
    if edge < floor: reasons.append('net_spread')
    if profile == 'spread': return reasons
    if not f.ready: reasons.append('warmup_or_stale')
    else:
        if f.change10 <= -max(spread, 5 * tick_cents): reasons.append('fall10')
        if f.change60 <= -max(2 * spread, 10 * tick_cents): reasons.append('fall60')
    if profile == 'trend': return reasons
    if f.gap is None or f.gap > spread / 2: reasons.append('bid_gap')
    if profile != 'gap' and edge < f.range60 / 2: reasons.append('range60')
    return reasons


def run(events, *, code, mode, profile, fee_cents=300, initial_cents=1_000_000,
        tick_cents=100, feature_rows=None, capacity=1):
    assert profile in PROFILES and mode in MODES and capacity > 0 and fee_cents >= 0
    delay = 1000 if mode.endswith('d1000') else 500 if mode.endswith('d500') else 0
    queued = mode.startswith('queue')
    model_id = f'{FAMILY}_{profile}_{mode}_q{capacity}_f{fee_cents}'
    feature_rows = features(events) if feature_rows is None else feature_rows
    assert len(feature_rows) == len(events)
    cash = initial_cents
    inventory = basis = realized_gross = fees = turnover = 0
    order = pending = cycle = None
    orders, fills, cycles, curve, decisions = [], [], [], [], []
    blocked = Counter()
    peak = initial_cents
    drawdown = max_inventory = holding_ms = inventory_ms = 0
    previous = last_book = last_bid = session = None
    loss_since = None
    risk_latched = False
    risk_reason = None
    entry_spread = 0
    cooldown_until = 0

    def fill(e, o, qty, price, kind):
        nonlocal cash, inventory, basis, realized_gross, fees, turnover, order, pending, cycle
        nonlocal loss_since, risk_latched, entry_spread, cooldown_until, risk_reason
        assert 0 < qty <= o['quantity']
        fee = qty * fee_cents
        if o['side'] == 'buy':
            assert inventory == 0 and cash >= qty * price + fee
            cash -= qty * price + fee
            inventory = qty; basis = qty * price
            cycle = dict(entry_ts=e.ts, entry_price_cents=price, quantity=qty, gross_cents=0, fees_cents=fee)
            entry_spread = max(tick_cents, e.ask - e.bid)
            loss_since = None; risk_latched = False; risk_reason = None
        else:
            assert qty <= inventory
            cost = cycle['entry_price_cents'] * qty
            cash += qty * price - fee
            inventory -= qty; basis -= cost
            gain = qty * price - cost
            realized_gross += gain
            cycle['gross_cents'] += gain; cycle['fees_cents'] += fee
            if inventory == 0:
                cycles.append(dict(**cycle, exit_ts=e.ts, duration_seconds=(e.ts-cycle['entry_ts'])/1000,
                                   net_cents=cycle['gross_cents']-cycle['fees_cents']))
                cycle = None
                if kind == 'risk_ioc': cooldown_until = e.ts + 60000
                loss_since = None; risk_latched = False; risk_reason = None
        fees += fee; turnover += qty * price
        fills.append(dict(model_id=model_id, code=code, order_id=o['id'], ts=e.ts, side=o['side'],
            price_cents=price, quantity=qty, fee_cents=fee, inventory=inventory, cash_cents=cash, kind=kind,
            created_ts=o['created_ts'], active_ts=o.get('active_ts',e.ts), source_last_cents=e.last,
            source_quantity=e.quantity, source_side=e.side, source_strict_side=e.strict_side,
            source_single=e.single, source_previous_ts=e.previous_ts))
        order = pending = None

    def activate(e, request):
        nonlocal order
        if request['side'] == 'cancel':
            order = None
            return
        if request['side'] == 'buy' and inventory or request['side'] == 'sell' and not inventory:
            order = None
            return
        o = dict(request, active_ts=e.ts, ahead=0)
        order = o
        if o.get('intent') == 'risk':
            remaining = min(o['quantity'], inventory)
            for p, q in e.bids:
                qty = min(remaining, q)
                if qty:
                    fill(e, o, qty, p, 'risk_ioc')
                    remaining -= qty
                if not remaining: break
            order = None  # IOC remainder expires; next event may request remaining inventory.
            return
        if queued:
            depth = e.bids if o['side'] == 'buy' else e.asks
            o['ahead'] = sum(q for p, q in depth if p == o['price'])
        crossing = e.ask <= o['price'] if o['side'] == 'buy' else e.bid >= o['price']
        if crossing:
            price = e.ask if o['side'] == 'buy' else e.bid
            qty = min(o['quantity'], e.ask_qty if o['side'] == 'buy' else e.bid_qty)
            qty = min(qty, cash//(price+fee_cents)) if o['side'] == 'buy' else min(qty, inventory)
            if qty: fill(e, o, qty, price, 'arrival_cross')
            else: order = None

    for e, f in zip(events, feature_rows):
        assert e.ts == f.ts and (previous is None or e.ts > previous.ts)
        if previous:
            elapsed = e.ts - previous.ts
            holding_ms += int(inventory > 0) * elapsed
            inventory_ms += inventory * elapsed
        if e.session != session:
            order = pending = None
            session = e.session
            loss_since = None
        if order and order['active_ts'] <= e.previous_ts and order['created_ts'] < e.ts and e.quantity > 0:
            side = e.side if mode == 'improve_l1' else e.strict_side if e.single else 'unknown'
            opposite = 'sell' if order['side'] == 'buy' else 'buy'
            reachable = e.last <= order['price'] if order['side'] == 'buy' else e.last >= order['price']
            if side == opposite and reachable:
                available = e.quantity
                if queued:
                    consumed = min(order['ahead'], available)
                    order['ahead'] -= consumed; available -= consumed
                qty = min(order['quantity'], available)
                if qty: fill(e, order, qty, order['price'], 'passive')
        valid = 0 < e.bid < e.ask and e.bid_qty > 0 and e.ask_qty > 0
        if valid: last_bid = e.bid; last_book = e
        if pending and pending['due_ts'] <= e.ts:
            request = pending; pending = order = None
            if valid: activate(e, request)
        if valid:
            reasons = entry_reasons(e, f, profile, fee_cents, tick_cents, queued) if not inventory else []
            if not inventory and e.ts < cooldown_until: reasons.append('cooldown')
            if reasons:
                blocked.update(reasons)
                decisions.append((e.ts, tuple(reasons)))
            if profile == 'risk' and inventory and e.ts > cycle['entry_ts']:
                loss = cycle['entry_price_cents'] - e.bid
                if loss >= max(3 * entry_spread, 30 * tick_cents):
                    # Do not count unobserved outages as continuously confirmed loss.
                    if loss_since is None or previous is None or e.ts-previous.ts > 2000: loss_since = e.ts
                else: loss_since = None
                if loss_since is not None and e.ts-loss_since >= 5000:
                    risk_latched = True; risk_reason = 'adverse_5s'
                if e.ts-cycle['entry_ts'] >= 300000:
                    risk_latched = True; risk_reason = risk_reason or 'hold_300s'
            side = 'sell' if inventory else 'buy'
            price = e.ask if inventory else e.bid
            if not queued and e.ask-e.bid > tick_cents: price += -tick_cents if inventory else tick_cents
            qty = inventory if inventory else min(capacity, cash//(price+fee_cents))
            risk_now = profile == 'risk' and inventory and risk_latched
            target = (side, price, qty)
            request = None
            if pending is None:
                if risk_now:
                    request = dict(side='sell', price=0, quantity=inventory, intent='risk', reason=risk_reason)
                elif reasons:
                    if order and order['side'] == 'buy': request = dict(side='cancel', price=0, quantity=0)
                elif qty and (order is None or (order['side'], order['price'], order['quantity']) != target):
                    request = dict(side=side, price=price, quantity=qty)
                elif qty == 0:
                    order = pending = None
            if request:
                request = dict(id=len(orders)+1, model_id=model_id, code=code, **request,
                               created_ts=e.ts, due_ts=e.ts+delay)
                orders.append(dict(request))
                if delay: pending = request
                else:
                    order = None
                    activate(e, request)
            elif qty == 0 and not reasons:
                order = pending = None
        else:
            order = pending = None
            loss_since = None
        assert cash >= 0 and 0 <= inventory <= capacity
        max_inventory = max(max_inventory, inventory)
        equity = cash + inventory * (last_bid or 0)
        peak = max(peak, equity); drawdown = max(drawdown, peak-equity)
        curve.append((e.ts, equity-initial_cents, inventory))
        previous = e
    tail_gross = inventory * (last_bid or 0) - basis
    pnl = cash + inventory * (last_bid or 0) - initial_cents
    assert pnl == realized_gross + tail_gross - fees
    rest = inventory; depth_value = 0
    if last_book:
        for p, q in last_book.bids:
            n = min(rest, q); depth_value += n*p; rest -= n
    s = dict(model_id=model_id, code=code, mode=mode, capacity=capacity, fee_per_side_cny=fee_cents/100,
        initial_cash_cny=initial_cents/100, pnl_cny=pnl/100, realized_gross_cny=realized_gross/100,
        tail_gross_cny=tail_gross/100, fees_cny=fees/100, end_cash_cny=cash/100, end_inventory=inventory,
        max_inventory=max_inventory, filled_contract_sides=sum(f['quantity'] for f in fills), fill_count=len(fills),
        arrival_cross_fills=sum(f['kind']=='arrival_cross' for f in fills), complete_cycles=len(cycles),
        winning_cycles=sum(c['net_cents']>0 for c in cycles), losing_cycles=sum(c['net_cents']<0 for c in cycles),
        order_count=len(orders), worst_cycle_cny=min((c['net_cents']/100 for c in cycles),default=None),
        median_cycle_seconds=median([c['duration_seconds'] for c in cycles]) if cycles else None,
        longest_cycle_seconds=max((c['duration_seconds'] for c in cycles),default=None),
        holding_seconds=holding_ms/1000, inventory_contract_seconds=inventory_ms/1000,
        max_drawdown_cny=drawdown/100, turnover_cny=turnover/100, tail_depth_uncovered_contracts=rest,
        hypothetical_tail_liquidation_pnl_cny=(cash+depth_value-inventory*fee_cents-initial_cents)/100 if rest==0 else None,
        last_quote_ts=last_book.ts if last_book else None, open_cycle=cycle,
        profile=profile, blocked_frames=dict(blocked), cancel_requests=sum(o['side']=='cancel' for o in orders),
        risk_ioc_fills=sum(f['kind']=='risk_ioc' for f in fills))
    return dict(summary=s, orders=orders, fills=fills, cycles=cycles, curve=curve, decisions=decisions)
