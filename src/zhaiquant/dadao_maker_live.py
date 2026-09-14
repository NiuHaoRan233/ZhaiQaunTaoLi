"""Dadao 0.1 arrival revision: simple quotes with the archived v013 selector.

The native engine is a ledger/analyzer only. Its trading decisions and matcher
are never invoked. The original research script remains the frozen oracle.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace

from .maker_paper import (
    DADAO_POLICY_V01_R2_CANDIDATE as POLICY, MakerPaperEngine, MakerLot, MakerOrder,
    SharedCapitalPaperRuntime, maker_comparison_strategy_id, maker_strategy_prefix,
)
from .shared_thousand_maker_v013_research import (
    SharedThousandV013Allocator, AllocationParametersV03,
)
from .shared_thousand_maker_v016_live import ArrivalSharedPaperRuntime, ArrivalTick

CODES = ('132026.SH', '132024.SH')


def milli(value):
    return round(value * 1000)


class DadaoLedgerEngine(MakerPaperEngine):
    dadao_execution = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.execution_counts = Counter()
        self.execution_audit = []

    def on_replay_tick(self, *args, **kwargs):
        raise ValueError('Dadao decisions belong exclusively to DadaoAllocator')


class DadaoAllocator(SharedThousandV013Allocator):
    """Incremental form of the archived switch_v013 decision loop."""

    def __init__(self, engines, **kwargs):
        super().__init__(engines, **kwargs)
        self.candidates = {}
        self._cash_milli = milli(self.shared_cash_cny)

    def account(self, code):
        return next(iter(self.engines[code].accounts.values()))

    @staticmethod
    def quote(tick, side):
        bid, ask = milli(tick.bid1), milli(tick.ask1)
        return (bid + (ask - bid > 1) if side == 'buy' else ask - (ask - bid > 1)) / 1000

    def allowed(self, tick):
        params = next(iter(self.engines.values())).parameters
        return (params.effective_earliest_entry_time(tick.market_date)
                <= tick.market_time <= params.latest_entry_time
                and not ('11:30:00.001' <= tick.market_time < '13:00:00.000'))

    def cancel(self, code, order, tick, reason='simple_quote_replaced'):
        if order is not None:
            self.engines[code]._cancel_order(self.account(code), order, tick, reason, True)

    def order(self, code, tick, side, price, quantity, old=None):
        if old and (milli(old.limit_price), old.remaining) == (milli(price), quantity):
            return old
        self.cancel(code, old, tick)
        account = self.account(code)
        lot_id = next((lot.db_id for lot in account.lots.values()
                       if lot.remaining_quantity > 0), None) if side == 'sell' else None
        # Prices are already integer milli-yuan. Do not inherit old native
        # floating-point floor behavior or any native economic price boundary.
        metadata = dict(paper_only=True, model_id=POLICY.model_id,
                        model_version=POLICY.model_version, quantity_unit='bond',
                        fill_mode='priority', price_boundary=price,
                        price_boundary_kind='live_priority_price' if side == 'buy' else 'sell_floor',
                        time_basis='arrival_decision_clock', decision_ts_ms=tick.market_ts_ms,
                        source_market_ts_ms=tick.source_market_ts_ms,
                        received_ts_ns=tick.received_ts_ns, arrival_sequence=tick.arrival_sequence)
        order_id = self.engines[code].store.insert_maker_order(dict(
            run_id=self.engines[code].store.run_id, market_date=tick.market_date,
            strategy_id=account.strategy_id, side=side, status='open', kind='simple_top_cycle',
            lot_id=lot_id, created_market_ts_ms=tick.market_ts_ms,
            updated_market_ts_ms=tick.market_ts_ms, limit_price=price, quantity=quantity,
            filled_quantity=0, queue_ahead=0, target_price=None, cancel_reason=None,
            metadata_json=json.dumps(metadata, separators=(',', ':'))))
        result = MakerOrder(order_id, side, 'simple_top_cycle', lot_id, tick.market_ts_ms,
                            price, quantity, price, metadata['price_boundary_kind'])
        if side == 'buy':
            account.buy_order = result
        else:
            account.sell_orders[lot_id] = result
        return result

    def settle(self, tick):
        if tick.code not in self.engines or tick.trade_bonds <= 0:
            return
        code = tick.code
        engine, account = self.engines[code], self.account(code)
        order = (account.buy_order if tick.inferred_side == 'sell' else
                 next(iter(account.sell_orders.values()), None) if tick.inferred_side == 'buy' else None)
        if order is None or tick.market_time > '15:30:05.000':
            return
        if order.created_ms >= tick.source_market_ts_ms:
            engine.execution_counts['pre_submission_source_print_rejected'] += 1
            return
        last, price = milli(tick.last_price), milli(order.limit_price)
        if not (last <= price if order.side == 'buy' else last >= price):
            return
        quantity = int(min(order.remaining, round(tick.trade_bonds))) // 10 * 10
        if quantity <= 0:
            return
        cost = price * quantity
        if order.side == 'buy':
            assert not self._holdings() and cost <= self._cash_milli and quantity <= 1000
            self._cash_milli -= cost
            account.cash = (milli(account.cash) - cost) / 1000
            account.inventory += quantity
            lot_id = engine.store.insert_maker_lot(dict(
                run_id=engine.store.run_id, market_date=tick.market_date,
                strategy_id=account.strategy_id, kind='simple_top_cycle',
                opened_market_ts_ms=tick.market_ts_ms, entry_price=order.limit_price,
                original_quantity=quantity, remaining_quantity=quantity,
                target_price=None, status='open', updated_market_ts_ms=tick.market_ts_ms))
            account.lots[lot_id] = MakerLot(lot_id, 'simple_top_cycle', tick.market_ts_ms,
                                          order.limit_price, quantity, quantity)
            account.buy_order = None
        else:
            assert quantity <= account.inventory
            lot_id = order.lot_id
            lot = account.lots[lot_id]
            self._cash_milli += cost
            account.cash = (milli(account.cash) + cost) / 1000
            account.inventory -= quantity
            lot.remaining_quantity -= quantity
            engine.store.update_maker_lot(lot_id, remaining_quantity=lot.remaining_quantity,
                status='closed' if lot.remaining_quantity == 0 else 'open',
                updated_market_ts_ms=tick.market_ts_ms)
            account.sell_orders.pop(lot_id, None)
            if lot.remaining_quantity == 0:
                account.lots.pop(lot_id)
        self.shared_cash_cny = self._cash_milli / 1000
        order.filled_quantity += quantity
        engine.store.update_maker_order(order.db_id, filled_quantity=order.filled_quantity,
            status='filled' if order.remaining == 0 else 'cancelled',
            cancel_reason=None if order.remaining == 0 else 'simple_partial_switch_to_actual_inventory',
            updated_market_ts_ms=tick.market_ts_ms)
        engine._record_fill(account, tick, order, lot_id, order.side, order.limit_price,
                            quantity, 'simple_passive_' + order.side, tick.received_ts_ns)

    def on_replay_tick(self, tick):
        if not isinstance(tick, ArrivalTick):
            raise ValueError('Dadao requires explicit source and decision clocks')
        # Settle the previously selected real order before updating the book,
        # analyzer, or score. A bad new score cannot veto the old fill.
        self.settle(tick)
        for code, engine in self.engines.items():
            if tick.code in (code, engine.stock_code) and engine.parameters.maker_session_has_started(
                    tick.market_date, tick.market_time):
                engine.analyzer.on_tick(tick)
                if tick.code == code:
                    engine.last_market_assessment = engine.analyzer.assess_market(tick, tick.previous_close)
        if tick.code in self.engines:
            self.last_bond_ticks[tick.code] = tick
            was_ready = self.capital_ready
            self._observe_opening_quote(tick.code, self.engines[tick.code], tick)
            if self.capital_ready and not was_ready:
                self._cash_milli = milli(self.initial_cash_cny)
                self.shared_cash_cny = self._cash_milli / 1000
            self.shadow_scores.pop(tick.code, None)
            self.candidates.pop(tick.code, None)
            if self.allowed(tick) and self._capital_available_at(tick.market_ts_ms) and 0 < tick.bid1 < tick.ask1:
                price = self.quote(tick, 'buy')
                quantity = min(1000, (self._cash_milli // milli(price)) // 10 * 10)
                if quantity:
                    candidate = MakerOrder(tick.tick_id, 'buy', 'simple_top_cycle', None,
                        tick.market_ts_ms, price, quantity, price, 'current_top',
                        target_price=self.quote(tick, 'sell'))
                    self.candidates[tick.code] = candidate
                    self._score(tick.code, candidate, active_fill=False)
        holdings = self._holdings()
        scores = {}
        if holdings:
            assert len(holdings) == 1
            winner, reason = holdings[0], 'position_locks_shared_cash'
        else:
            for code, (timestamp, score) in tuple(self.shadow_scores.items()):
                live = self.account(code).buy_order is not None
                if tick.market_ts_ms - timestamp > self.parameters.shadow_intent_ttl_seconds * 1000 and not live:
                    continue
                if code not in self.candidates or score.entry_price * score.remaining_bonds > self.shared_cash_cny + 1e-9:
                    continue
                scores[code] = replace(score, shadow_intent=not live)
            winner, reason = self._choose(scores, market_ts_ms=tick.market_ts_ms)
        self._record_selection(tick, winner, reason, scores)
        for code in self.engines:
            account = self.account(code)
            if holdings or winner != code or code not in self.candidates:
                self.cancel(code, account.buy_order, tick, 'simple_shared_selection')
            else:
                candidate = self.candidates[code]
                self.order(code, tick, 'buy', candidate.limit_price, round(candidate.remaining), account.buy_order)
            if holdings and tick.code == code == holdings[0]:
                old = next(iter(account.sell_orders.values()), None)
                if self.allowed(tick) and 0 < tick.bid1 < tick.ask1:
                    self.order(code, tick, 'sell', self.quote(tick, 'sell'), account.inventory, old)
                else:
                    self.cancel(code, old, tick, 'simple_outside_quote_window')
            elif not holdings:
                for order in list(account.sell_orders.values()):
                    self.cancel(code, order, tick)
            if code == tick.code:
                self.engines[code]._mark_account(account, tick, persist=True)
        self._assert_invariants()
        assert self._cash_milli >= 0 and len(holdings) <= 1
        assert all(self.account(c).inventory >= 0 for c in self.engines)


class DadaoPaperRuntime(ArrivalSharedPaperRuntime):
    """Reuse the validated append-only arrival transport, isolated by model ID."""

    def __init__(self, config, store, *, policy=POLICY):
        if policy != POLICY:
            raise ValueError('Dadao runtime requires its registered arrival revision')
        self.source_config, self.store, self.policy = config, store, policy
        self.config = replace(config, maker_paper=replace(config.maker_paper,
            initial_inventory_bonds=0.0, additional_buying_capacity_bonds=1000.0,
            maximum_inventory_bonds=1000.0, initial_cash_cny=0.0, order_quantity_bonds=1000.0,
            fill_modes=(), realtime_comparison_model_ids=(), super_windfall_enabled=False))
        self.parameters = AllocationParametersV03()
        self.allocator_class = DadaoAllocator
        self.engines = {code: DadaoLedgerEngine(self.config, store, bond_code=code,
            strategy_prefix=maker_strategy_prefix(config, code), priority_policy=policy,
            fill_modes=('priority',), include_windfall=False,
            strategy_ids_by_mode={'priority': maker_comparison_strategy_id(config, code, policy)}) for code in CODES}
        self.relevant_codes = {c for e in self.engines.values() for c in (e.bond_code, e.stock_code)}
        self.market_date, self.allocator = None, None
        self._published_event_count = 0
        for sql in (
            '''CREATE TABLE IF NOT EXISTS maker_shared_arrival_days (
               model_id TEXT NOT NULL, market_date TEXT NOT NULL, contract_json TEXT NOT NULL,
               PRIMARY KEY(model_id,market_date))''',
            '''CREATE TABLE IF NOT EXISTS maker_shared_arrival_events (
               sequence INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT NOT NULL,
               market_date TEXT NOT NULL, tick_id INTEGER NOT NULL, payload_json TEXT NOT NULL,
               UNIQUE(model_id,market_date,tick_id))''',
        ):
            store.connection.execute(sql)
        self._previous = {}
        self._decision_ms = self._sequence = 0
        self._failed = False
        self.arrival_counts = Counter()

    def _reset_date(self, market_date, *, clear):
        SharedCapitalPaperRuntime._reset_date(self, market_date, clear=clear)
        # Native startup has a zero-size base lot; it is not Dadao inventory.
        for engine in self.engines.values():
            account = next(iter(engine.accounts.values()))
            for lot_id in list(account.lots):
                engine.store.update_maker_lot(lot_id, status='closed')
                account.lots.pop(lot_id)
