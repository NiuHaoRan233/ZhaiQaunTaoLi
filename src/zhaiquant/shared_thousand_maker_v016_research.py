"""Offline v0.16: v0.14 decisions, causally separated settlement and intent.

L1 priority execution remains an explicit assumption, not exchange queue truth.
No future ticks, broker calls, or changes to previously registered models.
"""
from collections import Counter

from .maker_paper import MakerPaperEngine, SHARED_THOUSAND_POLICY_V016_CANDIDATE as POLICY
from .one_hand_maker_research import _only_account
from .shared_thousand_maker_v013_research import SharedThousandV013Allocator

PASSIVE_BUYS = frozenset({'passive_buy', 'priority_book_side_passive_buy'})
PASSIVE_SELLS = frozenset({'passive_sell', 'priority_book_side_passive_sell'})


class CausalSharedMakerEngine(MakerPaperEngine):
    causal_shared_execution = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.priority_policy != POLICY or self.fill_modes != ('priority',) or self.include_windfall:
            raise ValueError('causal engine is exclusive to offline v0.16 priority')
        self.execution_counts = Counter()
        self.execution_audit = []
        self._settling_buy_id = None
        self._depth_ts = None
        self._depth_used = {'buy': Counter(), 'sell': Counter()}
        self.causal_allocator_attached = False

    def on_replay_tick(self, tick, **kwargs):
        if not self.causal_allocator_attached:
            raise ValueError('v0.16 requires SharedThousandV016Allocator')
        return super().on_replay_tick(tick, **kwargs)

    def _process_resting_orders(self, account, tick, **kwargs):
        # Hide only ineligible orders for the matching phase. Returning zero
        # from _consume_queue would wrongly discard prints for OTHER old lots.
        new_buy = account.buy_order if (
            account.buy_order is not None and account.buy_order.created_ms >= tick.market_ts_ms
        ) else None
        new_sells = {key: order for key, order in account.sell_orders.items()
                     if order.created_ms >= tick.market_ts_ms}
        if new_buy is not None:
            account.buy_order = None
        for key in new_sells:
            del account.sell_orders[key]
        self._settling_buy_id = account.buy_order.db_id if account.buy_order else None
        if tick.trade_bonds > 0:
            self.execution_counts['same_timestamp_buy_exclusions'] += int(new_buy is not None)
            self.execution_counts['same_timestamp_sell_exclusions'] += len(new_sells)
        try:
            super()._process_resting_orders(account, tick, **kwargs)
        finally:
            self._settling_buy_id = None
            if new_buy is not None:
                account.buy_order = new_buy
            account.sell_orders.update(new_sells)

    def _depth_plan(self, tick, side, limit, quantity):
        if self._depth_ts != tick.market_ts_ms:
            self._depth_ts = tick.market_ts_ms
            self._depth_used = {'buy': Counter(), 'sell': Counter()}
        levels = tick.asks if side == 'buy' else tick.bids
        # Aggregate duplicate displayed prices, then traverse best to worst.
        visible = Counter()
        for price, size in levels:
            if price > 0 and size > 0:
                visible[price] += size
        plan = []
        remaining = quantity
        for price in sorted(visible, reverse=side == 'sell'):
            if (side == 'buy' and price > limit+1e-9) or (side == 'sell' and price < limit-1e-9):
                continue
            available = max(0.0, visible[price]-self._depth_used[side][price])
            consumed = min(remaining, available)
            if consumed > 1e-9:
                plan.append((price, consumed))
                remaining -= consumed
        return plan

    def _cancel_ioc_remainder(self, account, tick, order, persist):
        if order.remaining <= 1e-9:
            return
        # Do not let cancellation of an ephemeral active intent remove an
        # unrelated resting order (the legacy helper clears by side/lot).
        self.store.update_maker_order(order.db_id, status='cancelled',
            updated_market_ts_ms=tick.market_ts_ms, filled_quantity=order.filled_quantity,
            queue_ahead=max(0.0, order.queue_ahead), cancel_reason='causal_visible_depth_ioc_remainder')
        if account.buy_order is order:
            account.buy_order = None
        if order.lot_id is not None and account.sell_orders.get(order.lot_id) is order:
            account.sell_orders.pop(order.lot_id)

    def _active_execution(self, account, tick, order, quantity, side, execute, persist):
        plan = self._depth_plan(tick, side, order.limit_price, quantity)
        executable = sum(q for _, q in plan)
        before = order.filled_quantity
        result = execute(executable) if executable > 1e-9 else False
        filled = order.filled_quantity-before
        if filled > 1e-9:
            left = filled
            for price, size in plan:
                used = min(size, left)
                self._depth_used[side][price] += used
                left -= used
        if executable < quantity-1e-9:
            self.execution_counts[f'active_{side}_depth_limited'] += 1
        self.execution_audit.append(dict(market_ts_ms=tick.market_ts_ms, tick_id=tick.tick_id,
            order_id=order.db_id, side=side, requested=quantity, executable=executable,
            filled=filled, plan=plan, limit_price=order.limit_price))
        # Scoring rejection already cancels its order with its own reason.
        if executable < quantity-1e-9 and (filled > 0 or executable <= 1e-9):
            self._cancel_ioc_remainder(account, tick, order, persist)
        return result

    def _fill_buy(self, account, tick, order, quantity, received_ts_ns, **kwargs):
        native = super()._fill_buy
        if kwargs.get('reason', 'passive_buy') in PASSIVE_BUYS:
            if order.created_ms >= tick.market_ts_ms or self._settling_buy_id != order.db_id:
                raise AssertionError('passive buy must be an old order in settlement phase')
            return native(account, tick, order, quantity, received_ts_ns, **kwargs)
        return self._active_execution(account, tick, order, quantity, 'buy',
            lambda q: native(account, tick, order, q, received_ts_ns, **kwargs), kwargs['persist'])

    def _fill_sell(self, account, tick, order, quantity, received_ts_ns, **kwargs):
        native = super()._fill_sell
        if kwargs.get('reason', 'passive_sell') in PASSIVE_SELLS:
            if order.created_ms >= tick.market_ts_ms:
                raise AssertionError('passive sell must predate the market print')
            return native(account, tick, order, quantity, received_ts_ns, **kwargs)
        return self._active_execution(account, tick, order, quantity, 'sell',
            lambda q: native(account, tick, order, q, received_ts_ns, **kwargs), kwargs['persist'])


class SharedThousandV016Allocator(SharedThousandV013Allocator):
    def __init__(self, engines, **kwargs):
        if any(not isinstance(engine, CausalSharedMakerEngine) for engine in engines.values()):
            raise ValueError('v0.16 allocator requires causal engines')
        super().__init__(engines, **kwargs)
        for engine in engines.values():
            engine.causal_allocator_attached = True
        self.causal_counts = Counter()
        self._last_event_key = None

    def _allow_buy_fill(self, code, account, tick, order, quantity, kind, reason):
        if reason not in PASSIVE_BUYS:
            # This is an unsubmitted active intent: full parent risk/selection
            # assessment is still required, using the actual executable size.
            return super()._allow_buy_fill(code, account, tick, order, quantity, kind, reason)
        engine = self.engines[code]
        if engine._settling_buy_id != order.db_id or order.created_ms >= tick.market_ts_ms:
            raise AssertionError('unexposed order attempted passive settlement')
        if account.buy_order is not order or self.selected_code != code:
            raise AssertionError('passive order did not own the shared slot')
        if not self._capital_available_at(tick.market_ts_ms):
            raise AssertionError('unfunded passive commitment')
        holdings = self._holdings()
        if holdings and holdings != (code,):
            raise AssertionError('passive settlement conflicts with held bond')
        cost = quantity*order.limit_price
        if quantity > order.remaining+1e-9 or account.inventory+quantity > self.shared_capacity_bonds+1e-9:
            raise AssertionError('passive settlement exceeds order or capacity')
        if cost > self.shared_cash_cny+1e-9:
            raise AssertionError('passive commitment exceeded shared cash')
        self.shared_cash_cny -= cost
        self.causal_counts['passive_settlements_without_rescoring'] += 1
        return True

    def on_replay_tick(self, tick):
        key = (tick.market_ts_ms, tick.tick_id)
        if self._last_event_key is not None and key <= self._last_event_key:
            raise ValueError('duplicate or out-of-order causal event')
        self._last_event_key = key
        if tick.code in self.engines and self.shadow_scores.pop(tick.code, None) is not None:
            self.causal_counts['own_snapshot_shadow_invalidations'] += 1
        # Parent does matching, analyzer update, native decisions, reconciliation
        # exactly once. No auxiliary assessment call is injected.
        super().on_replay_tick(tick)
        if self.shared_cash_cny < -1e-9:
            raise AssertionError('negative shared cash')

    def _reconcile(self, tick):
        if tick.code in self.engines:
            account = _only_account(self.engines[tick.code])
            if account is None or account.buy_order is None:
                self.shadow_scores.pop(tick.code, None)
        super()._reconcile(tick)

    def research_metrics(self):
        return dict(**super().research_metrics(), causal_execution_metrics=dict(
            counts=dict(self.causal_counts), engines={code: dict(counts=dict(e.execution_counts),
                active_execution_audit=e.execution_audit) for code, e in self.engines.items()}))
