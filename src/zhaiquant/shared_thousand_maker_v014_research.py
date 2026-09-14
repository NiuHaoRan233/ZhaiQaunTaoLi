"""Offline v0.14, directly from v0.13 with its unchanged shared allocator."""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import maker_paper
from .config import load_config
from .maker_paper import SHARED_THOUSAND_POLICY_V013_CANDIDATE as PARENT
from .maker_paper import SHARED_THOUSAND_POLICY_V014_CANDIDATE as CHILD
from .one_hand_maker_research import replay_one_hand_day, SHARED_THOUSAND_BONDS
from .shared_thousand_maker_v013_research import SharedThousandV013Allocator

MODEL_ID = CHILD.model_id
PARENT_MODEL_ID = PARENT.model_id
FILL_KEYS = ('bond_code', 'market_ts_ms', 'side', 'price', 'quantity', 'fill_reason', 'inventory_after')


def economic_fills(cell):
    return [[f[k] for k in FILL_KEYS] for f in cell['fills']]


def risk_metrics(cell):
    queues: dict[str, list[list[float]]] = {}
    closed = []
    for fill in cell['fills']:
        queue = queues.setdefault(fill['bond_code'], [])
        if fill['side'] == 'buy':
            queue.append([fill['price'], fill['quantity']])
            continue
        remaining = fill['quantity']
        while remaining > 1e-9:
            entry, bonds = queue[0]
            matched = min(remaining, bonds)
            closed.append((fill['price'] - entry) * matched)
            remaining -= matched
            queue[0][1] -= matched
            if queue[0][1] <= 1e-9:
                queue.pop(0)
    cost = sum(p * q for queue in queues.values() for p, q in queue)
    return dict(closed_segments=len(closed), losing_segments=sum(p < -1e-6 for p in closed),
                worst_closed_segment=round(min(closed, default=0), 6),
                terminal_unrealized_pnl=round(cell['terminal_mark_value_cny'] - cost, 6))


def compact_order(order):
    return None if order is None else dict(id=order.db_id, kind=order.kind, side=order.side,
        price=order.limit_price, quantity=order.remaining, created_ms=order.created_ms)


class CausalObserver:
    """Record native calls once; never invoke an extra decision context."""
    def __init__(self):
        self.frames = []
        self.contexts = {}
        self.native_buys = {}

    def __call__(self, allocator, tick):
        if tick is None:
            for code, engine in allocator.engines.items():
                native_context, native_refresh = engine._decision_context, engine._refresh_orders
                def observed_context(*args, _native=native_context, _code=code, **kwargs):
                    result = _native(*args, **kwargs)
                    self.contexts[_code] = asdict(result)
                    return result
                def observed_refresh(account, tick, assessment, *, persist,
                                     _native=native_refresh, _code=code):
                    result = _native(account, tick, assessment, persist=persist)
                    self.native_buys[_code] = compact_order(account.buy_order)
                    return result
                engine._decision_context = observed_context
                engine._refresh_orders = observed_refresh
            return
        if tick.code not in allocator.engines:
            return
        engine = allocator.engines[tick.code]
        if not engine.accounts:
            return
        account = next(iter(engine.accounts.values()))
        assessment = engine.last_market_assessment
        self.frames.append(dict(code=tick.code, time=tick.market_time[:8], ts=tick.market_ts_ms,
            tick_id=tick.tick_id, inventory=account.inventory, bids=tick.bids, asks=tick.asks,
            context=self.contexts.get(tick.code), assessment=asdict(assessment) if assessment else None,
            native_buy=self.native_buys.get(tick.code),
            orders=[compact_order(o) for o in ([account.buy_order] if account.buy_order else [])
                    + list(account.sell_orders.values())],
            lots=[asdict(lot) for lot in account.lots.values() if lot.remaining_quantity > 1e-9],
            stalled_price=account.last_stalled_extra_exit_price,
            stalled_ts=account.last_stalled_extra_exit_ts_ms,
            reentry_exit_ts=account.last_shared_reentry_exit_ts_ms,
            recovery_ts=account.shared_reentry_recovery_ts_ms,
            reentry_allowed=engine._shared_current_opportunity_reentry_allowed(account, tick),
            selected=allocator.selected_code,
            shared_cash=allocator.shared_cash_cny,
            inventories={code: sum(a.inventory for a in e.accounts.values())
                         for code, e in allocator.engines.items()},
            current_trades=[asdict(e) for e in engine.analyzer.trade_evidence
                            if e.market_ts_ms == tick.market_ts_ms],
        ))


def replay_shared_thousand_v014_day(config, *, market_date: str, cutoff_time: str | None = None,
                                    observe: bool = False, policy=CHILD) -> dict[str, Any]:
    observer = CausalObserver() if observe else None
    result = replay_one_hand_day(config, market_date=market_date, priority_policy=policy,
        shared_capacity_bonds=SHARED_THOUSAND_BONDS, allocator_class=SharedThousandV013Allocator,
        cutoff_time=cutoff_time, observer=observer)
    result['cutoff_time'] = cutoff_time
    result['risk_metrics'] = risk_metrics(result)
    if observer is not None:
        result['frames'] = observer.frames
    return result


def totals(cells):
    return dict(pnl=round(sum(c['trading_pnl'] for c in cells), 6),
        fills=sum(c['fill_count'] for c in cells),
        exposure_seconds=round(sum(c['equivalent_full_slot_exposure_seconds'] for c in cells), 3),
        terminal_inventory_days=sum(c['terminal_inventory_bonds'] > 1e-9 for c in cells),
        losing_segments=sum(c['risk_metrics']['losing_segments'] for c in cells),
        worst_closed_segment=min((c['risk_metrics']['worst_closed_segment'] for c in cells), default=0),
        terminal_unrealized_pnl=round(sum(c['risk_metrics']['terminal_unrealized_pnl'] for c in cells), 6),
        allocation_switches=sum(c['allocation_switches'] for c in cells),
        all_inventory_invariants=all(c['shared_capacity_inventory_invariant'] for c in cells),
        all_order_invariants=all(c['one_buy_order_invariant'] for c in cells))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config.toml')
    parser.add_argument('--dates', nargs='+', required=True)
    parser.add_argument('--observe-dates', nargs='*', default=[])
    parser.add_argument('--cutoff-time', help='Same explicit cutoff for every requested day')
    parser.add_argument('--parent-freeze', default='output/research/shared_v014_parent_freeze_20260907.json')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(output)
    frozen = json.loads(Path(args.parent_freeze).read_text(encoding='utf-8'))
    old_profiles = {}
    for name, values in frozen['profiles'].items():
        current = json.loads(json.dumps(asdict(getattr(maker_paper, name))))
        old_profiles[name] = all(current[k] == v for k, v in values.items())
    if not all(old_profiles.values()):
        raise AssertionError('An immutable profile changed')
    config = load_config(args.config)
    result = dict(model_id=MODEL_ID, parent_model_id=PARENT_MODEL_ID,
        offline_only=True, profile=asdict(CHILD), source_readonly=True, cells=[],
        old_profile_checks=old_profiles, config_hashes={}, source_hashes={})
    for name in ('config.toml', 'config.example.toml'):
        digest = hashlib.sha256(Path(name).read_bytes()).hexdigest()
        if digest != frozen['source_hashes'][name]:
            raise AssertionError(f'Configuration changed: {name}')
        result['config_hashes'][name] = digest
    for name in ('src/zhaiquant/maker_paper.py',
                 'src/zhaiquant/shared_thousand_maker_v014_research.py',
                 'src/zhaiquant/one_hand_maker_research.py',
                 'src/zhaiquant/shared_thousand_maker_v013_research.py'):
        result['source_hashes'][name] = hashlib.sha256(Path(name).read_bytes()).hexdigest()
    for day in args.dates:
        pair = {}
        for key, policy in (('parent', PARENT), ('candidate', CHILD)):
            pair[key] = replay_shared_thousand_v014_day(config, market_date=day,
                cutoff_time=args.cutoff_time, observe=day in args.observe_dates, policy=policy)
        saved = next((c for c in frozen['cells'] if c['market_date'] == day), None)
        if saved is not None and args.cutoff_time is None:
            pair['frozen_parent_exact'] = (
                economic_fills(pair['parent']) == economic_fills(saved)
                and pair['parent']['trading_pnl'] == saved['trading_pnl']
                and pair['parent']['order_counts'] == saved['order_counts'])
            if not pair['frozen_parent_exact']:
                raise AssertionError(f'Frozen v0.13 changed on {day}')
        pair.update(market_date=day, same_fills=economic_fills(pair['parent']) == economic_fills(pair['candidate']),
                    pnl_delta=round(pair['candidate']['trading_pnl'] - pair['parent']['trading_pnl'], 6))
        result['cells'].append(pair)
        print(json.dumps({k: pair[k] for k in ('market_date', 'same_fills', 'pnl_delta')}, ensure_ascii=False), flush=True)
    result['totals'] = {key: totals([c[key] for c in result['cells']]) for key in ('parent', 'candidate')}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(output=str(output), totals=result['totals']), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
