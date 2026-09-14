"""Small standalone, read-only 1,000-bond top-of-book cycle experiment.

No strategy engine, runtime registration, broker connection or database writes.
Prices and cash are integer 0.001 CNY units. Output is immutable per filename.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics


FAMILY = "probe_top_cycle_1000_20260908"
MODES = ("exact_l1", "improve_l1", "improve_single")


def milli(value):
    return round(float(value) * 1000)


def in_session(clock):
    return "09:30:00" <= clock <= "11:30:05" or "13:00:00" <= clock <= "15:30:05"


@dataclass(frozen=True)
class Event:
    tick_id: int
    day: str
    clock: str
    ts: int
    bid: int
    ask: int
    last: int
    bids: tuple
    quantity: int
    transactions: int
    side: str
    single: bool


def load_events(connection):
    days = [r[0] for r in connection.execute(
        "SELECT DISTINCT market_date FROM raw_ticks WHERE code='132026.SH' "
        "AND market_date BETWEEN '2026-08-04' AND '2026-09-07' ORDER BY 1")]
    result, coverage = [], []
    for day in days:
        rows = connection.execute("""
            WITH ranked AS (
              SELECT r.*, s.status AS source_status, ROW_NUMBER() OVER (
                PARTITION BY r.market_ts_ms
                ORDER BY CASE WHEN s.status='backfill' THEN 1 ELSE 0 END,
                         r.received_ts_ns, r.id) AS rn
              FROM raw_ticks r LEFT JOIN sessions s ON s.run_id=r.run_id
              WHERE r.market_date=? AND r.code='132026.SH')
            SELECT * FROM ranked WHERE rn=1 ORDER BY market_ts_ms
        """, (day,)).fetchall()
        previous, events = None, []
        resets = backfills = 0
        digest = hashlib.sha256()
        for r in rows:
            bid, ask, last = (milli(r[k]) for k in
                              ('bid_price_1', 'ask_price_1', 'last_price'))
            quantity, transactions, side, single = 0, 0, 'none', False
            if previous is not None:
                dv = r['volume'] - previous['volume']
                da = r['amount'] - previous['amount']
                dt = r['transaction_count'] - previous['transaction_count']
                if dv < 0 or da < -0.01 or dt < 0:
                    resets += 1
                elif dv > 0:
                    quantity = round(dv * 10)  # QMT bond hand = 10 bonds.
                    transactions = dt
                    if milli(previous['ask_price_1']) > 0 and last >= milli(previous['ask_price_1']):
                        side = 'buy'
                    elif milli(previous['bid_price_1']) > 0 and last <= milli(previous['bid_price_1']):
                        side = 'sell'
                    elif last > milli(previous['last_price']):
                        side = 'buy'
                    elif last < milli(previous['last_price']):
                        side = 'sell'
                    else:
                        side = 'unknown'
                    single = dt == 1 and abs(da * 1000 / quantity - last) <= 1.01
            previous = r
            if not in_session(r['market_time']):
                continue
            bids = tuple((milli(r[f'bid_price_{i}']), round(r[f'bid_volume_{i}'] * 10))
                         for i in range(1, 6) if r[f'bid_price_{i}'] > 0)
            e = Event(r['id'], day, r['market_time'], r['market_ts_ms'],
                      bid, ask, last, bids, quantity, transactions, side, single)
            events.append(e)
            digest.update(json.dumps(asdict(e), sort_keys=True).encode())
            backfills += r['source_status'] == 'backfill'
        result.extend(events)
        gaps = [(b.ts-a.ts)/1000 for a,b in zip(events, events[1:])
                if not (a.clock < '12:00' <= b.clock)]
        coverage.append(dict(day=day, events=len(events), first=events[0].clock,
            last=events[-1].clock, missing_open=events[0].clock > '09:30:05',
            cumulative_resets=resets, backfill_events=backfills,
            volume_events=sum(e.quantity > 0 for e in events),
            single_events=sum(e.single for e in events),
            max_observation_gap_seconds=max(gaps, default=0),
            sha256=digest.hexdigest()))
    return result, coverage


def run(events, mode):
    assert mode in MODES
    model_id = FAMILY + '_' + mode
    cash = initial = None
    inventory = basis = 0
    order = None
    orders, fills, cycles, daily = [], [], [], []
    realized = turnover = 0
    peak = drawdown = 0
    previous_day = None
    cycle = None
    previous_equity = 0
    last_event = None

    def equity(e):
        return cash + inventory * (e.bid or e.last)

    def end_day(e):
        nonlocal previous_equity
        value = equity(e)
        day_fills = [f for f in fills if f['day'] == e.day]
        day_turnover = sum(f['price_milli'] * f['quantity'] for f in day_fills)
        tail_value, rest = 0, inventory
        for price, quantity in e.bids:
            q = min(rest, quantity)
            tail_value += price*q
            rest -= q
        daily.append(dict(day=e.day, last=e.clock, pnl_cny=(value-previous_equity)/1000,
            cumulative_pnl_cny=(value-initial)/1000, inventory_bonds=inventory,
            cash_cny=cash/1000, tail_unrealized_cny=(inventory*(e.bid or e.last)-basis)/1000,
            realized_to_date_cny=realized/1000, fills=len(day_fills),
            turnover_cny=day_turnover/1000,
            pnl_after_1bp_cny=(value-previous_equity-day_turnover*0.0001)/1000,
            visible_depth_tail_value_cny=tail_value/1000,
            tail_bonds_beyond_visible_depth=rest))
        previous_equity = value

    for e in events:
        if previous_day != e.day:
            if last_event is not None and cash is not None:
                end_day(last_event)
            order = None  # Exchange day orders expire, positions and cash do not.
            previous_day = e.day
        if cash is None:
            if not (0 < e.bid < e.ask):
                continue
            initial = cash = e.ask * 1000
            previous_equity = peak = cash
        # Settle the previously resting order BEFORE reading this book to reprice.
        if order and e.ts > order['created_ts'] and e.quantity > 0:
            opposite = 'sell' if order['side'] == 'buy' else 'buy'
            price_ok = e.last <= order['price'] if order['side'] == 'buy' else e.last >= order['price']
            evidence_ok = e.single if mode == 'improve_single' else True
            if e.side == opposite and price_ok and evidence_ok:
                q = min(order['quantity'], e.quantity)
                q = q // 10 * 10
                if q:
                    side, price = order['side'], order['price']
                    assert q <= e.quantity
                    turnover += q*price
                    if side == 'buy':
                        assert inventory == 0 and q*price <= cash
                        cash -= q*price
                        inventory, basis = q, q*price
                        cycle = dict(entry_day=e.day, entry_clock=e.clock, entry_ts=e.ts,
                                     entry_price_milli=price, quantity=q, realized_milli=0)
                    else:
                        assert q <= inventory
                        cost = cycle['entry_price_milli']*q
                        cash += q*price
                        inventory -= q
                        basis -= cost
                        realized += q*price-cost
                        cycle['realized_milli'] += q*price-cost
                        if inventory == 0:
                            cycles.append(dict(**cycle, exit_day=e.day, exit_clock=e.clock,
                                exit_ts=e.ts, duration_seconds=(e.ts-cycle['entry_ts'])/1000,
                                pnl_cny=cycle['realized_milli']/1000))
                            cycle = None
                    fills.append(dict(model_id=model_id, order_id=order['id'], day=e.day,
                        clock=e.clock, ts=e.ts, tick_id=e.tick_id, side=side,
                        price_milli=price, quantity=q, inventory_bonds=inventory,
                        cash_milli=cash, created_ts=order['created_ts'],
                        source_last_milli=e.last, source_quantity=e.quantity,
                        source_transactions=e.transactions, source_single=e.single))
                    order = None  # First partial buy switches to sell; no extra buying.
        value = equity(e)
        peak = max(peak, value)
        drawdown = max(drawdown, peak-value)
        assert cash >= 0 and 0 <= inventory <= 1000
        if 0 < e.bid < e.ask:
            side = 'sell' if inventory else 'buy'
            price = e.ask if inventory else e.bid
            if mode != 'exact_l1' and e.ask-e.bid > 1:
                price += -1 if inventory else 1
            quantity = inventory if inventory else min(1000, (cash//price)//10*10)
            if quantity:
                target = (side, price, quantity)
                if order is None or (order['side'], order['price'], order['quantity']) != target:
                    order = dict(id=len(orders)+1, model_id=model_id, day=e.day,
                        created_ts=e.ts, side=side, price=price, quantity=quantity)
                    orders.append(dict(order))
            else:
                order = None
        else:
            order = None
        last_event = e
    if last_event is not None and cash is not None:
        end_day(last_event)
    pnl = previous_equity-initial
    winners = [c for c in cycles if c['pnl_cny'] > 0]
    losers = [c for c in cycles if c['pnl_cny'] < 0]
    summary = dict(model_id=model_id, days=len(daily), initial_cash_cny=initial/1000,
        total_pnl_cny=pnl/1000, realized_cny=realized/1000,
        end_unrealized_cny=(pnl-realized)/1000, end_inventory_bonds=inventory,
        end_cash_cny=cash/1000, turnover_cny=turnover/1000, orders=len(orders),
        fills=len(fills), complete_cycles=len(cycles), winning_cycles=len(winners),
        losing_cycles=len(losers), flat_cycles=len(cycles)-len(winners)-len(losers),
        profitable_days=sum(d['pnl_cny'] > 0 for d in daily),
        average_daily_cny=pnl/1000/len(daily), max_bid_mark_drawdown_cny=drawdown/1000,
        best_cycle_cny=max((c['pnl_cny'] for c in cycles),default=0),
        worst_cycle_cny=min((c['pnl_cny'] for c in cycles),default=0),
        median_cycle_seconds=statistics.median(c['duration_seconds'] for c in cycles) if cycles else 0,
        longest_cycle_seconds=max((c['duration_seconds'] for c in cycles),default=0),
        worst_day=min(daily,key=lambda d:d['pnl_cny']),
        best_day=max(daily,key=lambda d:d['pnl_cny']),
        total_at_fee_bps={str(bp):(pnl-turnover*bp/10000)/1000 for bp in (0,0.5,1,2,5)},
        breakeven_fee_bps=pnl/turnover*10000 if turnover else None,
        fee_note='Per-side proportional cost on executed turnover; no minimum fee, no cash-path feedback.',
        open_cycle=cycle)
    return dict(summary=summary,daily=daily,cycles=cycles,fills=fills,orders=orders)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(output)
    db = Path('data/zhaiquant.sqlite3').resolve()
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as connection:
        connection.row_factory=sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        events, coverage = load_events(connection)
    scenarios = {mode:run(events,mode) for mode in MODES}
    result = dict(family=FAMILY, symbol='132026.SH', offline_only=True,
        source_readonly=True, source=str(db), coverage=coverage,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        assumptions=['Hypothetical first queue position, market-time L1 replay, no impact or measured latency.',
            'L1 mode allocates cumulative delta to last price and inferred side; not tick-by-tick truth.',
            'Single mode excludes aggregate/amount-inconsistent frames; not a profit lower bound.',
            'Starting flat, first ask*1000 cash once; inventory/cash carry across all observed days.',
            'Positive spread only; first partial buy switches to selling actual inventory; sell even at loss.',
            'Day PnL = cash plus tail at bid1 minus prior closing equity; not forced liquidation.',
            'End-of-day depth is reported separately, insufficient five-level depth remains unvalued there.',
            'Fees are turnover sensitivities without cash-path feedback; no actual account fee schedule known.'],
        scenarios=scenarios)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2)
    for mode, scenario in scenarios.items():
        print(mode, json.dumps(scenario['summary'],ensure_ascii=False),flush=True)
    print('coverage',json.dumps(coverage,ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
