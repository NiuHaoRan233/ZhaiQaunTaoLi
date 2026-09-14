"""Offline comparisons of top-of-book arithmetic and immutable native models.

Reuse native L1 loading, scoring and selection; keep the simple matcher explicit.
No mutation of existing policies, source SQLite or forward paper accounts.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from zhaiquant.config import load_config, maker_underlying_stock_code
from zhaiquant.maker import MakerAnalyzer, MakerParameters, _load_ticks
from zhaiquant.maker_paper import MakerOrder, SHARED_THOUSAND_POLICY_V016_CANDIDATE
from zhaiquant.shared_thousand_maker_v013_research import SharedThousandV013Allocator

FAMILY = 'probe_top_compare_20260908_v1'
CODES = ('132026.SH', '132024.SH')
VARIANTS = ('fixed_132026','fixed_132024','switch_v013','ordinary_132026','ordinary_132024')
BASELINE = 'output/research/shared_v016_matrix_20260804_20260904.json'
ORDINARY_BASELINE = 'output/research/priority_v269_matrix_20260804_20260904.json'
FREEZE = 'output/research/top_cycle_comparison_20260908_freeze.json'


def money_units(value):
    return round(value*1000)


def parameters(config):
    p=config.maker_paper
    return MakerParameters(price_tick=p.price_tick,order_quantity_bonds=p.order_quantity_bonds,
        earliest_entry_time=p.earliest_entry,latest_entry_time=p.latest_entry,
        opening_caution_effective_date=p.opening_caution_effective_date,
        opening_caution_end_time=p.opening_caution_end,opening_caution_minimum_edge=p.opening_caution_minimum_edge)


def load_day(connection, config, day, params):
    merged={}
    for code in CODES:
        for t in _load_ticks(connection,day,code,maker_underlying_stock_code(config,code),params):
            merged[t.tick_id]=t
    ticks=sorted(merged.values(),key=lambda t:(t.market_ts_ms,t.tick_id))
    quotes={}
    for t in ticks:
        if t.code in CODES and t.code not in quotes and t.ask1>0 and t.market_time>=params.effective_earliest_entry_time(day):
            quotes[t.code]=(t.market_ts_ms,t.ask1)
    assert len(quotes)==2
    cash=max(v[1] for v in quotes.values())*1000
    ready=max(v[0] for v in quotes.values())
    return ticks,cash,ready


def run_day(ticks, *, variant, initial_cash, ready_ts, params, stock_codes, ordinary_seed=136800.0):
    ordinary=variant.startswith('ordinary_')
    switch=variant=='switch_v013'
    selected_fixed=None if switch else variant.split('_')[-1]+'.SH'
    codes=CODES if switch else (selected_fixed,)
    base=1000 if ordinary else 0
    cap=2000 if ordinary else 1000
    cash=initial=money_units(ordinary_seed if ordinary else initial_cash)
    inventory={c:base for c in codes}
    last={}
    buy_order=sell_order=None
    orders=[];fills=[];cycles=[]
    open_cycle=None
    count=0
    funding=0
    turnover=0
    peak=dd=0
    dd_witness=None
    max_inventory=base
    extra_seconds=short_seconds=locked_seconds=0.0
    previous_ts=None
    cycle_realized=0
    model_id=FAMILY+'_'+variant
    engines={}
    if switch:
        for code in CODES:
            engines[code]=SimpleNamespace(parameters=params,
                analyzer=MakerAnalyzer(code,stock_codes[code],params),
                priority_policy=SHARED_THOUSAND_POLICY_V016_CANDIDATE,
                last_market_assessment=None,accounts={'probe':SimpleNamespace(inventory=0,buy_order=None)})
        allocator=SharedThousandV013Allocator(engines,initial_cash_cny=initial_cash,
            capital_ready_ts_ms=ready_ts,shared_capacity_bonds=1000)
    else:
        allocator=None
    candidate_orders={}
    selections=[]

    def quote_price(t,side):
        bid,ask=money_units(t.bid1),money_units(t.ask1)
        return (bid+(ask-bid>1)) if side=='buy' else (ask-(ask-bid>1))

    def allowed(t):
        return (params.effective_earliest_entry_time(t.market_date)<=t.market_time<=params.latest_entry_time
                and not ('11:30:00.001'<=t.market_time<'13:00:00.000'))

    def mark_pnl():
        delta=0
        for c,q in inventory.items():
            if c not in last: continue
            t=last[c]
            price=money_units(t.bid1 if q>=base else t.ask1)
            delta+=(q-base)*price
        return cash-initial+delta

    def create_order(t,code,side,price,quantity,old=None):
        nonlocal count
        if old and (old['code'],old['side'],old['price'],old['quantity'])==(code,side,price,quantity):
            return old
        count+=1
        item=dict(id=count,model_id=model_id,code=code,side=side,price=price,quantity=quantity,
                  created_ts=t.market_ts_ms,created_tick_id=t.tick_id)
        orders.append(dict(item))
        return item

    for t in ticks:
        if previous_ts is not None:
            seconds=(t.market_ts_ms-previous_ts)/1000
            extra_seconds+=sum(max(0,q-base) for q in inventory.values())/1000*seconds
            short_seconds+=sum(max(0,base-q) for q in inventory.values())/1000*seconds
            locked_seconds+=int(any(q>base for q in inventory.values()))*seconds
        previous_ts=t.market_ts_ms
        # Match only old, actually selected orders. No new scoring can veto this fill.
        order=buy_order if t.inferred_side=='sell' else sell_order if t.inferred_side=='buy' else None
        if (t.code in codes and order and order['code']==t.code and t.trade_bonds>0
            and order['created_ts']<t.market_ts_ms and t.market_time<='15:30:05.000'):
            price=order['price']
            reachable=money_units(t.last_price)<=price if order['side']=='buy' else money_units(t.last_price)>=price
            if reachable:
                q=min(order['quantity'],round(t.trade_bonds))//10*10
                if q:
                    side=order['side'];cost=price*q
                    if side=='buy':
                        if ordinary and cost>cash:
                            adjustment=cost-cash;cash+=adjustment;initial+=adjustment;funding+=adjustment
                        assert cost<=cash and inventory[t.code]+q<=cap
                        if not ordinary:
                            assert sum(inventory.values())==0
                            open_cycle=dict(code=t.code,entry_ts=t.market_ts_ms,entry_time=t.market_time,
                                entry_price=price/1000,quantity=q,pnl_cny=0.0)
                        cash-=cost;inventory[t.code]+=q;buy_order=None
                    else:
                        assert q<=inventory[t.code]
                        cash+=cost;inventory[t.code]-=q;sell_order=None
                        if not ordinary:
                            gain=(price/1000-open_cycle['entry_price'])*q
                            open_cycle['pnl_cny']+=gain;cycle_realized+=money_units(gain)
                            if inventory[t.code]==0:
                                cycles.append(dict(**open_cycle,exit_ts=t.market_ts_ms,exit_time=t.market_time,
                                    duration_seconds=(t.market_ts_ms-open_cycle['entry_ts'])/1000))
                                open_cycle=None
                    turnover+=cost
                    fills.append(dict(model_id=model_id,order_id=order['id'],bond_code=t.code,
                        market_ts_ms=t.market_ts_ms,market_time=t.market_time,side=side,price=price/1000,
                        quantity=q,inventory_after=inventory[t.code],cash_cny=cash/1000,
                        created_ts=order['created_ts'],reference_tick_id=t.tick_id,
                        source_quantity=t.trade_bonds,source_last=t.last_price,source_side=t.inferred_side))
        if t.code in codes:
            last[t.code]=t
        if switch:
            allocator.shared_cash_cny=cash/1000
            for code,engine in engines.items():
                engine.accounts['probe'].inventory=inventory[code]
                if t.code in (code,stock_codes[code]) and params.maker_session_has_started(t.market_date,t.market_time):
                    engine.analyzer.on_tick(t)
                    if t.code==code:
                        engine.last_market_assessment=engine.analyzer.assess_market(t,t.previous_close)
            if t.code in CODES:
                allocator.last_bond_ticks[t.code]=t
                allocator.shadow_scores.pop(t.code,None)  # v0.16's own-book invalidation.
                candidate_orders.pop(t.code,None)
                if allowed(t) and t.market_ts_ms>=ready_ts and 0<t.bid1<t.ask1:
                    p=quote_price(t,'buy');qty=min(1000,(cash//p)//10*10)
                    if qty:
                        candidate=MakerOrder(db_id=t.tick_id,side='buy',kind='simple_top_cycle',lot_id=None,
                            created_ms=t.market_ts_ms,limit_price=p/1000,quantity=qty,
                            price_boundary=p/1000,price_boundary_kind='current_top',
                            target_price=quote_price(t,'sell')/1000)
                        candidate_orders[t.code]=candidate
                        allocator._score(t.code,candidate,active_fill=False)
            holdings=[c for c,q in inventory.items() if q]
            if holdings:
                assert len(holdings)==1
                winner=holdings[0];reason='position_locks_shared_cash';scores={}
                buy_order=None
            else:
                scores={}
                for code,(ts,score) in tuple(allocator.shadow_scores.items()):
                    live=buy_order is not None and buy_order['code']==code
                    if t.market_ts_ms-ts>allocator.parameters.shadow_intent_ttl_seconds*1000 and not live:
                        continue
                    if code not in candidate_orders or score.entry_price*score.remaining_bonds>cash/1000+1e-9:
                        continue
                    scores[code]=replace(score,shadow_intent=not live)
                winner,reason=allocator._choose(scores,market_ts_ms=t.market_ts_ms)
            if winner!=allocator.selected_code:
                selections.append(dict(ts=t.market_ts_ms,time=t.market_time,previous=allocator.selected_code,
                    winner=winner,reason=reason,scores={c:s.public() for c,s in scores.items()}))
            allocator._record_selection(t,winner,reason,scores)
            if not holdings:
                if winner is None or winner not in candidate_orders:
                    buy_order=None
                else:
                    candidate=candidate_orders[winner]
                    buy_order=create_order(t,winner,'buy',money_units(candidate.limit_price),round(candidate.remaining),buy_order)
            if holdings and t.code==holdings[0]:
                sell_order=(create_order(t,t.code,'sell',quote_price(t,'sell'),inventory[t.code],sell_order)
                    if allowed(t) and 0<t.bid1<t.ask1 else None)
            elif not holdings:
                sell_order=None
        elif t.code in codes:
            valid=allowed(t) and 0<t.bid1<t.ask1 and (ordinary or t.market_ts_ms>=ready_ts)
            if not valid:
                buy_order=sell_order=None
            else:
                q=inventory[t.code];p=quote_price(t,'buy')
                target=min(1000,cap-q) if ordinary else min(1000,(cash//p)//10*10) if q==0 else 0
                buy_order=create_order(t,t.code,'buy',p,target,buy_order) if target else None
                target=min(1000,q) if ordinary else q
                sell_order=create_order(t,t.code,'sell',quote_price(t,'sell'),target,sell_order) if target else None
        assert cash>=0 and all(0<=q<=cap for q in inventory.values())
        if not ordinary: assert sum(inventory.values())<=1000 and sum(q>0 for q in inventory.values())<=1
        max_inventory=max(max_inventory,sum(inventory.values()))
        pnl=mark_pnl()
        peak=max(peak,pnl)
        if peak-pnl>dd:
            dd=peak-pnl;dd_witness=dict(time=t.market_time,code=t.code,inventory=dict(inventory))
    pnl=mark_pnl()/1000
    if switch:
        actual=[s['winner'] for s in selections if s['winner'] is not None]
        changes=sum(a!=b for a,b in zip(actual,actual[1:]))
    else: changes=0
    return dict(model_id=model_id,market_date=ticks[0].market_date,variant=variant,
        initial_inventory_per_bond=base,initial_cash_cny=initial/1000,funding_adjustment_cny=funding/1000,
        capital_ready_ts_ms=ready_ts,trading_pnl=round(pnl,6),terminal_cash_cny=cash/1000,
        terminal_inventories=inventory,terminal_exposure_bonds=sum(abs(q-base) for q in inventory.values()),
        fill_count=len(fills),order_count=len(orders),turnover_cny=turnover/1000,
        exposure_seconds=round(extra_seconds,3),base_short_seconds=round(short_seconds,3),
        locked_seconds=round(locked_seconds,3),maximum_inventory_bonds=max_inventory,
        max_mark_drawdown_cny=dd/1000,drawdown_witness=dd_witness,
        realized_cny=None if ordinary else cycle_realized/1000,
        terminal_unrealized_cny=None if ordinary else round(pnl-cycle_realized/1000,6),
        closed_cycles=len(cycles),losing_cycles=sum(c['pnl_cny']<-1e-6 for c in cycles),
        worst_cycle=min((c['pnl_cny'] for c in cycles),default=0),
        cross_bond_reselections=changes,selection_events=selections,orders=orders,fills=fills,cycles=cycles,
        terminal_books={c:dict(bid=t.bid1,ask=t.ask1,time=t.market_time) for c,t in last.items()})


def totals(cells):
    return dict(pnl=round(sum(c['trading_pnl'] for c in cells),6),
        days=len(cells),fills=sum(c['fill_count'] for c in cells),
        exposure_seconds=round(sum(c['exposure_seconds'] for c in cells),3),
        base_short_seconds=round(sum(c['base_short_seconds'] for c in cells),3),
        terminal_exposure_days=sum(c['terminal_exposure_bonds']>0 for c in cells),
        worst_day=min(c['trading_pnl'] for c in cells),profitable_days=sum(c['trading_pnl']>0 for c in cells),
        turnover_cny=round(sum(c['turnover_cny'] for c in cells),3),
        worst_cycle=min(c['worst_cycle'] for c in cells),
        terminal_unrealized_cny=round(sum(c['terminal_unrealized_cny'] or 0 for c in cells),6),
        closed_cycles=sum(c['closed_cycles'] for c in cells),losing_cycles=sum(c['losing_cycles'] for c in cells),
        cross_bond_reselections=sum(c['cross_bond_reselections'] for c in cells))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--dates',nargs='+')
    args=parser.parse_args()
    output=Path(args.output)
    if output.exists(): raise FileExistsError(output)
    hashes=json.loads(Path(FREEZE).read_text(encoding='utf-8'))
    for p,h in hashes.items(): assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==h,p
    old=json.loads(Path(BASELINE).read_text(encoding='utf-8'))
    ordinary=json.loads(Path(ORDINARY_BASELINE).read_text(encoding='utf-8'))
    config=load_config('config.toml');params=parameters(config)
    stock_codes={c:maker_underlying_stock_code(config,c) for c in CODES}
    result=dict(family=FAMILY,source_readonly=True,temporary_in_memory_accounts=True,
        account_comparison='independent daily resets, equal shared opening cash/ready time; no fees',
        execution_warning='Common L1 input, but simple matcher is not identical to native model matchers.',
        switching_components='Unmodified SharedThousandV013Allocator._score/_choose/_record_selection, AllocationParametersV03; simple candidates/exits; v016-style settle-before-score and own-book invalidation.',
        frozen_sources=hashes,script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        parameters=asdict(params),cells=[])
    with sqlite3.connect(config.storage.database.resolve().as_uri()+'?mode=ro',uri=True) as connection:
        connection.row_factory=sqlite3.Row
        connection.execute('PRAGMA query_only=ON');connection.execute('BEGIN')
        for pair in old['cells']:
            day=pair['market_date']
            if args.dates and day not in args.dates: continue
            ticks,cash,ready=load_day(connection,config,day,params)
            assert abs(cash-pair['candidate']['initial_cash_cny'])<1e-6
            assert ready==pair['candidate']['capital_ready_ts_ms']
            baseline_keys=('model_id','initial_cash_cny','trading_pnl','terminal_inventory_bonds','terminal_mark_value_cny','fill_count','fills','risk_metrics','slot_metrics','equivalent_full_slot_exposure_seconds','pnl_by_code')
            cell=dict(market_date=day,ticks=len(ticks),tick_sha256=hashlib.sha256(json.dumps([asdict(t) for t in ticks],sort_keys=True).encode()).hexdigest(),
                saved_shared={label:{k:pair[label][k] for k in baseline_keys} for label in ('reference','parent','candidate')},
                saved_ordinary={c['bond_code']:c for c in ordinary['cells'] if c['market_date']==day},probes={})
            for v in VARIANTS:
                cell['probes'][v]=run_day(ticks,variant=v,initial_cash=cash,ready_ts=ready,params=params,
                    stock_codes=stock_codes,ordinary_seed=config.maker_paper.initial_cash_cny)
            result['cells'].append(cell)
            print(json.dumps(dict(day=day,pnl={v:c['trading_pnl'] for v,c in cell['probes'].items()})),flush=True)
    result['totals']={v:totals([c['probes'][v] for c in result['cells']]) for v in VARIANTS}
    with output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result['totals']),flush=True)


if __name__=='__main__':main()
