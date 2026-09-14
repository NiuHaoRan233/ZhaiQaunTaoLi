"""Frozen first-round commodity optimization, independent of all saved kernels.

Only retrospective paper research. All cash/prices are cents per option contract.
Control must reproduce the archived zero-delay, latest-contract-evidence account.
"""
from collections import Counter, deque, defaultdict
from dataclasses import replace
from statistics import median
from . import commodity_dadao_research as source

FAMILY = 'probe_commodity_selective_20260912_v1'
VARIANTS = ('control','fee_edge','stable_entry','patient_exit','day_flat','combined','flow_entry','flow_patient')
FEE_CENTS = 170


def quote_prices(e, tick):
    improved = e.ask-e.bid > tick
    return e.bid+(tick if improved else 0), e.ask-(tick if improved else 0)


class PastBook:
    """Current/past only, time sampled; clear on invalid/gapped/session input."""
    def __init__(self):
        self.samples=deque();self.previous_ts=None;self.session=None

    def update(self,e):
        valid=0<e.bid<e.ask and min(e.bid_qty,e.ask_qty)>0
        if (not valid or e.session!=self.session or
                self.previous_ts is not None and e.ts-self.previous_ts>60000):
            self.samples.clear()
        self.session=e.session;self.previous_ts=e.ts
        if not valid:return None
        while self.samples and self.samples[0][0]<e.ts-300000:self.samples.popleft()
        item=(e.ts,e.bid+e.ask)
        # Retain the first observation in each 5s bucket; current is used separately.
        if not self.samples or e.ts//5000!=self.samples[-1][0]//5000:self.samples.append(item)
        old=next((x for x in reversed(self.samples) if x[0]<=e.ts-60000),None)
        if old is None or e.ts-old[0]>90000:return None
        mids=[x[1] for x in self.samples]+[item[1]]
        return dict(drop60_twice=old[1]-item[1],range300_twice=max(mids)-min(mids),
                    history_ms=e.ts-self.samples[0][0])


def prepare(day_inputs):
    if not day_inputs:raise ValueError('No input days')
    events=[];dates={};meta=day_inputs[0][2]
    for ordinal,(es,flags,m) in enumerate(day_inputs):
        if m['unit']!=meta['unit'] or m['tick_cents']!=meta['tick_cents']:
            raise ValueError('Contract terms changed')
        for e in es:
            event=replace(e,session=ordinal*10+e.session,quantity=min(e.quantity,1))
            events.append(event);dates[event.ts]=m['date']
    return events,dates,meta


def run(day_inputs, *, code, variant, prepared=None):
    if variant not in VARIANTS:raise ValueError(variant)
    events,dates,meta=prepared if prepared is not None else prepare(day_inputs)
    initial=meta['initial_cents'];tick=meta['tick_cents'];fee=FEE_CENTS
    model=f'{FAMILY}_{variant}_d0_q1_f170'
    edge=variant!='control';stable=variant in ('stable_entry','combined')
    patient=variant in ('patient_exit','combined','flow_patient');flat=variant in ('day_flat','combined')
    flow_filter=variant in ('flow_entry','flow_patient')
    cash=initial;inventory=basis=realized=fees=turnover=0
    order=cycle=None;orders=[];fills=[];cycles=[];curve=[]
    peak=initial;drawdown=max_inventory=holding_ms=0
    previous=None;session=None;last_book=None;book=PastBook();rejects=Counter()
    flow=deque();flow_counts=Counter()
    entry_mid_twice=entry_spread=entry_session=None;risk_since=None;patient_released=False

    def fill(e,o,price,kind):
        nonlocal cash,inventory,basis,realized,fees,turnover,order,cycle
        nonlocal entry_mid_twice,entry_spread,entry_session,risk_since,patient_released
        if o['side']=='buy':
            assert inventory==0 and cash>=price+fee
            cash-=price+fee;inventory=1;basis=price
            cycle=dict(entry_ts=e.ts,entry_price_cents=price,quantity=1,gross_cents=0,fees_cents=fee)
            # Anchor from the causal order decision, before this incoming trade.
            entry_mid_twice=o.get('entry_mid_twice',e.bid+e.ask)
            entry_spread=o.get('entry_spread',max(tick,e.ask-e.bid))
            entry_session=e.session;risk_since=None;patient_released=False
        else:
            assert inventory==1
            cash+=price-fee;inventory=0;realized+=price-basis;basis=0
            cycle['gross_cents']=price-cycle['entry_price_cents'];cycle['fees_cents']+=fee
            cycles.append(dict(**cycle,exit_ts=e.ts,duration_seconds=(e.ts-cycle['entry_ts'])/1000,
                               net_cents=cycle['gross_cents']-cycle['fees_cents']))
            cycle=None
        fees+=fee;turnover+=price
        fills.append(dict(model_id=model,code=code,order_id=o['id'],ts=e.ts,side=o['side'],
            price_cents=price,quantity=1,fee_cents=fee,inventory=inventory,cash_cents=cash,kind=kind,
            created_ts=o['created_ts'],active_ts=o.get('active_ts',e.ts),source_last_cents=e.last,
            source_quantity=e.quantity,source_side=e.side,source_strict_side=e.strict_side,
            source_previous_ts=e.previous_ts,source_last_contract_evidence=e.single,date=dates[e.ts]))
        order=None

    def request(e,side,price,reason=None):
        o=dict(id=len(orders)+1,model_id=model,code=code,side=side,price=price,quantity=1,
               created_ts=e.ts,due_ts=e.ts)
        if variant!='control':
            o['reason']=reason
            if side=='buy':o.update(entry_mid_twice=e.bid+e.ask,entry_spread=e.ask-e.bid)
        orders.append(dict(o));return dict(o,active_ts=e.ts)

    for e in events:
        assert previous is None or e.ts>previous.ts
        if previous:holding_ms+=int(inventory>0)*(e.ts-previous.ts)
        if e.session!=session:
            order=None;session=e.session;flow.clear();flow_counts.clear()
        # Original old order is settled before any new filter or exit decision.
        if (order and order['active_ts']<=e.previous_ts and order['created_ts']<e.ts and
                e.quantity>0 and e.single):
            opposite='sell' if order['side']=='buy' else 'buy'
            reachable=e.last<=order['price'] if order['side']=='buy' else e.last>=order['price']
            if e.strict_side==opposite and reachable:fill(e,order,order['price'],'passive')
        valid=0<e.bid<e.ask and min(e.bid_qty,e.ask_qty)>0
        features=book.update(e) if stable else None
        if flow_filter:
            if not valid or previous and e.ts-previous.ts>60000:flow.clear();flow_counts.clear()
            while flow and flow[0][0]<e.ts-300000:
                _,oldside=flow.popleft();flow_counts[oldside]-=1
            if valid and e.quantity>0 and e.single and e.strict_side in ('buy','sell'):
                flow.append((e.ts,e.strict_side));flow_counts[e.strict_side]+=1
        if valid:last_book=e
        minute=(e.ts//60000+480)%1440
        flattened=False
        exit_bid_valid=e.bid>0 and e.bid_qty>0
        if flat and inventory and minute>=895 and exit_bid_valid:
            # Current quote only. One contract and positive bid depth suffice.
            o=request(e,'sell',e.bid,'day_flat_visible_bid');fill(e,o,e.bid,'active_exit');flattened=True
        elif flat and inventory and minute>=895:
            rejects['day_flat_no_current_bid']+=1
        if valid and not flattened:
            buy,sell=quote_prices(e,tick)
            side='sell' if inventory else 'buy';price=sell if inventory else buy
            reason='current_top';allowed=True
            if inventory and patient:
                if (e.session!=entry_session or e.ts-cycle['entry_ts']>=300000):
                    patient_released=True
                if previous and (e.ts-previous.ts>60000 or not (0<previous.bid<previous.ask and min(previous.bid_qty,previous.ask_qty)>0)):
                    risk_since=None
                if e.bid+e.ask < entry_mid_twice-2*entry_spread:
                    if risk_since is None:risk_since=e.ts
                    if e.ts-risk_since>=30000:patient_released=True
                else:risk_since=None
                if not patient_released:
                    floor=((cycle['entry_price_cents']+2*fee+tick+tick-1)//tick)*tick
                    if price<floor:price=floor;reason='bounded_cost_exit'
            if not inventory:
                if flat and minute>=870:allowed=False;reason='late_entry'
                elif edge and sell-buy<2*fee+tick:allowed=False;reason='insufficient_net_edge'
                elif flow_filter and not (flow_counts['buy']>0 and flow_counts['sell']>0):
                    allowed=False;reason='no_recent_two_sided_flow'
                elif stable:
                    if features is None:allowed=False;reason='short_or_gapped_history'
                    elif features['drop60_twice']>e.ask-e.bid:allowed=False;reason='falling_book'
                    elif features['range300_twice']>6*(e.ask-e.bid):allowed=False;reason='range_exceeds_spread'
            if allowed and (inventory or cash>=price+fee):
                if order is None or (order['side'],order['price'])!=(side,price):
                    order=request(e,side,price,reason)
                    # Patient floor is above current ask; all intended quotes remain passive.
                    assert (side=='buy' and price<e.ask) or (side=='sell' and price>e.bid)
            else:
                order=None
                if not allowed:rejects[reason]+=1
        elif not valid:
            order=None;risk_since=None
        max_inventory=max(max_inventory,inventory)
        equity=cash+inventory*(last_book.bid if last_book else 0)
        peak=max(peak,equity);drawdown=max(drawdown,peak-equity)
        curve.append((e.ts,equity-initial,inventory));previous=e
        assert cash>=0 and 0<=inventory<=1
    pnl=curve[-1][1];tail=inventory*(last_book.bid if last_book else 0)-basis
    assert pnl==realized+tail-fees
    endings={};dfills=Counter(f['date'] for f in fills)
    for ts,value,inv in curve:endings[dates[ts]]=(ts,value,inv)
    daily=[];before=0
    for date,(ts,value,inv) in endings.items():
        daily.append(dict(date=date,pnl_cny=(value-before)/100,cumulative_pnl_cny=value/100,
                          end_inventory=inv,fill_count=dfills[date],last_ts=ts));before=value
    period=min(dates.values())+'_'+max(dates.values())
    age=(events[-1].ts-last_book.ts)/1000 if last_book else None
    summary=dict(model_id=model,account_id=model+'_'+code+'_'+period,code=code,mode=variant,variant=variant,
        capacity=1,fee_per_side_cny=fee/100,delay_ms=0,initial_cash_cny=initial/100,pnl_cny=pnl/100,
        realized_gross_cny=realized/100,tail_gross_cny=tail/100,fees_cny=fees/100,
        end_cash_cny=cash/100,end_inventory=inventory,max_inventory=max_inventory,
        filled_contract_sides=len(fills),fill_count=len(fills),arrival_cross_fills=0,
        active_exit_fills=sum(f['kind']=='active_exit' for f in fills),complete_cycles=len(cycles),
        winning_cycles=sum(c['net_cents']>0 for c in cycles),losing_cycles=sum(c['net_cents']<0 for c in cycles),
        order_count=len(orders),worst_cycle_cny=min((c['net_cents']/100 for c in cycles),default=None),
        median_cycle_seconds=median(c['duration_seconds'] for c in cycles) if cycles else None,
        longest_cycle_seconds=max((c['duration_seconds'] for c in cycles),default=None),holding_seconds=holding_ms/1000,
        inventory_contract_seconds=holding_ms/1000,max_drawdown_cny=drawdown/100,turnover_cny=turnover/100,
        last_quote_ts=last_book.ts if last_book else None,last_quote_age_seconds=age,
        stale_tail=bool(inventory and (age is None or age>60)),tail_depth_uncovered_contracts=0 if last_book else inventory,
        completed_cycle_net_cny=sum(c['net_cents'] for c in cycles)/100,open_cycle_contribution_cny=0,
        open_cycle=cycle,date=period,days=len(daily),first_date=min(dates.values()),last_date=max(dates.values()),
        continuous_cash_and_inventory=True,daytime_only=True,positive_days=sum(d['pnl_cny']>0 for d in daily),
        negative_days=sum(d['pnl_cny']<0 for d in daily),zero_trade_days=sum(d['fill_count']==0 for d in daily),
        tail_days=sum(d['end_inventory']>0 for d in daily),available_dates=list(endings),rejection_frames=dict(rejects))
    summary['open_cycle_contribution_cny']=summary['pnl_cny']-summary['completed_cycle_net_cny']
    result=dict(summary=summary,orders=orders,fills=fills,cycles=cycles,curve=curve,daily=daily)
    source.audit_result(result)
    assert abs(sum(d['pnl_cny'] for d in daily)-summary['pnl_cny'])<1e-7
    return result
