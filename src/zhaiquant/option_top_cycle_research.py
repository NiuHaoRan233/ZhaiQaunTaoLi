"""Independent offline option adaptation of the archived simple top-of-book loop.

All prices/cash are integer cents per contract. No broker API or live registration.
"""
from dataclasses import dataclass, asdict
from statistics import median

FAMILY = 'probe_option_dadao_20260909_v1'
MODES = ('improve_l1', 'improve_single', 'improve_single_d500',
         'improve_single_d1000', 'queue_single_d500')


@dataclass(frozen=True)
class Event:
    ts: int
    previous_ts: int
    session: int
    bid: int
    ask: int
    bid_qty: int
    ask_qty: int
    bids: tuple
    asks: tuple
    last: int = 0
    quantity: int = 0
    transactions: int = 0
    side: str = 'unknown'
    single: bool = False
    strict_side: str = 'unknown'


def run(events, *, code, mode, capacity=1, fee_cents=0, initial_cents=1_000_000, tick_cents=100):
    assert mode in MODES and capacity > 0 and fee_cents >= 0
    delay = 1000 if mode.endswith('d1000') else 500 if mode.endswith('d500') else 0
    queued = mode.startswith('queue')
    model_id = f'{FAMILY}_{mode}_q{capacity}_f{fee_cents}'
    cash = initial_cents
    inventory = basis = realized_gross = fees = turnover = 0
    order = pending = cycle = None
    orders, fills, cycles, curve = [], [], [], []
    peak = initial_cents
    drawdown = max_inventory = 0
    holding_ms = inventory_ms = 0
    previous = None
    last_bid = last_book = None
    session = None

    def fill(e, o, qty, price, kind):
        nonlocal cash, inventory, basis, realized_gross, fees, turnover, order, pending, cycle
        assert qty > 0 and qty <= o['quantity']
        fee = qty*fee_cents
        if o['side'] == 'buy':
            assert inventory == 0 and cash >= qty*price+fee
            cash -= qty*price+fee
            inventory = qty
            basis = qty*price
            cycle = dict(entry_ts=e.ts, entry_price_cents=price, quantity=qty,
                         gross_cents=0, fees_cents=fee)
        else:
            assert qty <= inventory
            cost = cycle['entry_price_cents']*qty
            cash += qty*price-fee
            inventory -= qty
            basis -= cost
            gain = qty*price-cost
            realized_gross += gain
            cycle['gross_cents'] += gain
            cycle['fees_cents'] += fee
            if inventory == 0:
                cycles.append(dict(**cycle, exit_ts=e.ts,
                    duration_seconds=(e.ts-cycle['entry_ts'])/1000,
                    net_cents=cycle['gross_cents']-cycle['fees_cents']))
                cycle = None
        fees += fee
        turnover += qty*price
        fills.append(dict(model_id=model_id, code=code, order_id=o['id'], ts=e.ts,
            side=o['side'], price_cents=price, quantity=qty, fee_cents=fee,
            inventory=inventory, cash_cents=cash, kind=kind,
            created_ts=o['created_ts'], active_ts=o.get('active_ts',e.ts),
            source_last_cents=e.last, source_quantity=e.quantity,
            source_side=e.side, source_strict_side=e.strict_side, source_single=e.single,
            source_previous_ts=e.previous_ts))
        # First partial buy switches to sell; cancel all old/pending intents after fills.
        order = pending = None

    def activate(e, request):
        nonlocal order
        if request['side']=='buy' and inventory or request['side']=='sell' and not inventory:
            order=None
            return
        o=dict(request, active_ts=e.ts, ahead=0)
        order=o
        if queued:
            depth=e.bids if o['side']=='buy' else e.asks
            o['ahead']=sum(q for p,q in depth if p==o['price'])
        # A stale delayed limit may cross on arrival. Execute top visible opposite depth;
        # never assume its cancellation arrived before the observed interval's trade.
        crossing=(e.ask<=o['price']) if o['side']=='buy' else (e.bid>=o['price'])
        if crossing:
            price=e.ask if o['side']=='buy' else e.bid
            qty=min(o['quantity'],e.ask_qty if o['side']=='buy' else e.bid_qty)
            qty=min(qty,cash//(price+fee_cents)) if o['side']=='buy' else min(qty,inventory)
            if qty: fill(e,o,qty,price,'arrival_cross')
            else: order=None

    for e in events:
        assert previous is None or e.ts > previous.ts
        if previous:
            elapsed=e.ts-previous.ts
            holding_ms += int(inventory>0)*elapsed
            inventory_ms += inventory*elapsed
        if e.session != session:
            order=pending=None
            session=e.session
        # Current trade interval is wholly after the old order became active.
        if order and order['active_ts']<=e.previous_ts and order['created_ts']<e.ts and e.quantity>0:
            side=e.side if mode=='improve_l1' else e.strict_side if e.single else 'unknown'
            opposite='sell' if order['side']=='buy' else 'buy'
            reachable=e.last<=order['price'] if order['side']=='buy' else e.last>=order['price']
            if side==opposite and reachable:
                available=e.quantity
                if queued:
                    consumed=min(order['ahead'],available)
                    order['ahead']-=consumed
                    available-=consumed
                qty=min(order['quantity'],available)
                if qty: fill(e,order,qty,order['price'],'passive')
        valid=0<e.bid<e.ask and e.bid_qty>0 and e.ask_qty>0
        if valid:
            last_bid=e.bid
            last_book=e
        if pending and pending['due_ts']<=e.ts:
            request=pending
            pending=None
            order=None
            if valid: activate(e,request)
        if valid:
            side='sell' if inventory else 'buy'
            price=e.ask if inventory else e.bid
            if not queued and e.ask-e.bid>tick_cents:
                price += -tick_cents if inventory else tick_cents
            qty=inventory if inventory else min(capacity,cash//(price+fee_cents))
            target=(side,price,qty)
            if pending is None and qty and (order is None or (order['side'],order['price'],order['quantity'])!=target):
                request=dict(id=len(orders)+1,model_id=model_id,code=code,side=side,
                    price=price,quantity=qty,created_ts=e.ts,due_ts=e.ts+delay)
                orders.append(dict(request))
                if delay: pending=request
                else:
                    order=None
                    activate(e,request)
            elif qty==0:
                order=pending=None
        else:
            # Invalid book cancels decisions after old-trade accounting; no new quotation.
            order=pending=None
        assert cash>=0 and 0<=inventory<=capacity
        max_inventory=max(max_inventory,inventory)
        equity=cash+inventory*(last_bid or 0)
        peak=max(peak,equity)
        drawdown=max(drawdown,peak-equity)
        curve.append((e.ts,equity-initial_cents,inventory))
        previous=e
    tail_gross=inventory*(last_bid or 0)-basis
    pnl=cash+inventory*(last_bid or 0)-initial_cents
    assert pnl==realized_gross+tail_gross-fees
    rest=inventory
    depth_value=0
    if last_book:
        for p,q in last_book.bids:
            n=min(rest,q);depth_value+=n*p;rest-=n
    s=dict(model_id=model_id,code=code,mode=mode,capacity=capacity,fee_per_side_cny=fee_cents/100,
        initial_cash_cny=initial_cents/100,pnl_cny=pnl/100,
        realized_gross_cny=realized_gross/100,tail_gross_cny=tail_gross/100,fees_cny=fees/100,
        end_cash_cny=cash/100,end_inventory=inventory,max_inventory=max_inventory,
        filled_contract_sides=sum(f['quantity'] for f in fills),fill_count=len(fills),
        arrival_cross_fills=sum(f['kind']=='arrival_cross' for f in fills),
        complete_cycles=len(cycles),winning_cycles=sum(c['net_cents']>0 for c in cycles),
        losing_cycles=sum(c['net_cents']<0 for c in cycles),order_count=len(orders),
        worst_cycle_cny=min((c['net_cents']/100 for c in cycles),default=None),
        median_cycle_seconds=median([c['duration_seconds'] for c in cycles]) if cycles else None,
        longest_cycle_seconds=max((c['duration_seconds'] for c in cycles),default=None),
        holding_seconds=holding_ms/1000,inventory_contract_seconds=inventory_ms/1000,
        max_drawdown_cny=drawdown/100,turnover_cny=turnover/100,
        tail_depth_uncovered_contracts=rest,
        hypothetical_tail_liquidation_pnl_cny=(cash+depth_value-inventory*fee_cents-initial_cents)/100 if rest==0 else None,
        last_quote_ts=last_book.ts if last_book else None,
        open_cycle=cycle)
    return dict(summary=s,orders=orders,fills=fills,cycles=cycles,curve=curve)
