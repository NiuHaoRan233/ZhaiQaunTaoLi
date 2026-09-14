"""Registered v0.16 r2: arrival-ordered paper execution and durable recovery.

Raw exchange and receive clocks are immutable. Legacy market_ts_ms ledger
columns hold the monotonic decision clock for THIS identity only. Every order
records its source clock and journal sequence; fills reference the raw tick.
No backfill, market-time resorting, or broker orders are performed here.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime

from .maker import ReplayTick
from .maker_paper import (
    MakerPaperEngine, SharedCapitalPaperRuntime,
    SHARED_THOUSAND_POLICY_V016_R2_CANDIDATE as POLICY,
)
from .recorder import RecordedTick, TickRecorder
from .shared_thousand_maker_v016_research import (
    CausalSharedMakerEngine, SharedThousandV016Allocator, PASSIVE_BUYS, PASSIVE_SELLS,
)
from .types import SHANGHAI, Tick


@dataclass(frozen=True)
class ArrivalTick(ReplayTick):
    source_market_ts_ms: int = 0
    received_ts_ns: int = 0
    arrival_sequence: int = 0


class ArrivalSharedMakerEngine(CausalSharedMakerEngine):
    arrival_shared_execution = True

    def __init__(self, *args, **kwargs):
        # Reuse the immutable v0.16 execution methods, not its exclusive
        # constructor/identity. All native trading still belongs to the parent.
        MakerPaperEngine.__init__(self, *args, **kwargs)
        if self.priority_policy != POLICY or self.fill_modes != ('priority',) or self.include_windfall:
            raise ValueError('arrival engine is exclusive to v0.16 r2 priority')
        self.execution_counts = Counter()
        self.execution_audit = []
        self._settling_buy_id = None
        self._depth_ts = None
        self._depth_used = {'buy': Counter(), 'sell': Counter()}
        self.causal_allocator_attached = False

    def on_replay_tick(self, tick, **kwargs):
        if not isinstance(tick, ArrivalTick):
            raise ValueError('v0.16 r2 requires an arrival event with both clocks')
        kwargs['received_ts_ns'] = tick.received_ts_ns
        return super().on_replay_tick(tick, **kwargs)

    def _process_resting_orders(self, account, tick, **kwargs):
        # A late print may settle an order already exposed at its SOURCE time,
        # never an order created later, even though the print arrived now.
        hidden_buy = account.buy_order if (
            account.buy_order is not None
            and account.buy_order.created_ms >= tick.source_market_ts_ms
        ) else None
        hidden_sells = {key: order for key, order in account.sell_orders.items()
                        if order.created_ms >= tick.source_market_ts_ms}
        if hidden_buy is not None:
            account.buy_order = None
        for key in hidden_sells:
            del account.sell_orders[key]
        if tick.trade_bonds > 0:
            self.execution_counts['source_time_buy_exclusions'] += int(hidden_buy is not None)
            self.execution_counts['source_time_sell_exclusions'] += len(hidden_sells)
        try:
            super()._process_resting_orders(account, tick, **kwargs)
        finally:
            if hidden_buy is not None:
                account.buy_order = hidden_buy
            account.sell_orders.update(hidden_sells)

    def _depth_plan(self, tick, side, limit, quantity):
        if tick.market_ts_ms - tick.source_market_ts_ms > self.config.qmt.stale_after_seconds * 1000:
            self.execution_counts['stale_source_active_depth_refusals'] += 1
            return []
        source_tick = replace(tick, market_ts_ms=tick.source_market_ts_ms)
        return super()._depth_plan(source_tick, side, limit, quantity)

    def _fill_buy(self, account, tick, order, quantity, received_ts_ns, **kwargs):
        if kwargs.get('reason', 'passive_buy') in PASSIVE_BUYS:
            if order.created_ms >= tick.source_market_ts_ms:
                raise AssertionError('passive buy cannot use a pre-submission source print')
        return super()._fill_buy(account, tick, order, quantity, received_ts_ns, **kwargs)

    def _fill_sell(self, account, tick, order, quantity, received_ts_ns, **kwargs):
        if kwargs.get('reason', 'passive_sell') in PASSIVE_SELLS:
            if order.created_ms >= tick.source_market_ts_ms:
                raise AssertionError('passive sell cannot use a pre-submission source print')
        return super()._fill_sell(account, tick, order, quantity, received_ts_ns, **kwargs)

    def _new_order(self, account, tick, **kwargs):
        order = super()._new_order(account, tick, **kwargs)
        row = self.store.connection.execute(
            'SELECT metadata_json FROM maker_paper_orders WHERE id=?', (order.db_id,),
        ).fetchone()
        metadata = json.loads(row['metadata_json'])
        metadata.update(time_basis='arrival_decision_clock',
                        source_market_ts_ms=tick.source_market_ts_ms,
                        received_ts_ns=tick.received_ts_ns,
                        arrival_sequence=tick.arrival_sequence,
                        decision_ts_ms=tick.market_ts_ms)
        self.store.update_maker_order(order.db_id, metadata_json=json.dumps(metadata, separators=(',', ':')))
        return order


class ArrivalSharedAllocator(SharedThousandV016Allocator):
    def __init__(self, engines, **kwargs):
        if any(not isinstance(engine, ArrivalSharedMakerEngine) for engine in engines.values()):
            raise ValueError('arrival allocator requires arrival engines')
        super().__init__(engines, **kwargs)


class ArrivalSharedPaperRuntime(SharedCapitalPaperRuntime):
    """One model, one capital slot, one immutable arrival journal per day."""

    def __init__(self, config, store, *, policy=POLICY):
        if policy != POLICY:
            raise ValueError('arrival runtime requires v0.16 r2')
        super().__init__(config, store, policy=policy)
        store.connection.execute('''CREATE TABLE IF NOT EXISTS maker_shared_arrival_days (
            model_id TEXT NOT NULL, market_date TEXT NOT NULL, contract_json TEXT NOT NULL,
            PRIMARY KEY(model_id,market_date))''')
        store.connection.execute('''CREATE TABLE IF NOT EXISTS maker_shared_arrival_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id TEXT NOT NULL, market_date TEXT NOT NULL, tick_id INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            UNIQUE(model_id,market_date,tick_id))''')
        self._previous: dict[str, tuple[int, Tick]] = {}
        self._decision_ms = 0
        self._sequence = 0
        self._failed = False
        self.arrival_counts = Counter()

    def _contract(self):
        return json.dumps(dict(policy=asdict(self.policy), maker_paper=asdict(self.config.maker_paper),
                               allocation=asdict(self.parameters),
                               stale_after_seconds=self.config.qmt.stale_after_seconds), sort_keys=True)

    @staticmethod
    def _contracts_match(saved, current):
        # These ordinary-only fields were added after the arrival identities
        # were registered. Missing means disabled; preserve the original JSON
        # and still reject enabled flags or any other execution change.
        contracts = [json.loads(saved), json.loads(current)]
        for contract in contracts:
            for field in (
                'protect_discounted_ordinary_lot_from_fragile_bid_exit',
                'require_strict_passive_order_timestamp',
            ):
                contract['policy'].setdefault(field, False)
        return contracts[0] == contracts[1]

    def rebuild_date(self, market_date: date | str):
        date_text = market_date.isoformat() if isinstance(market_date, date) else market_date
        connection = self.store.connection
        row = connection.execute('''SELECT contract_json FROM maker_shared_arrival_days
            WHERE model_id=? AND market_date=?''', (self.policy.model_id, date_text)).fetchone()
        contract = self._contract()
        if row is not None and not self._contracts_match(row['contract_json'], contract):
            raise ValueError('arrival execution contract changed; a new registered identity is required')
        connection.execute('''INSERT OR IGNORE INTO maker_shared_arrival_days
            (model_id,market_date,contract_json) VALUES (?,?,?)''',
            (self.policy.model_id, date_text, contract))
        rows = connection.execute('''SELECT sequence,tick_id,payload_json FROM maker_shared_arrival_events
            WHERE model_id=? AND market_date=? ORDER BY sequence''', (self.policy.model_id, date_text)).fetchall()
        self._reset_date(date_text, clear=True)
        self._previous = {}
        self._decision_ms = 0
        self._sequence = 0
        self._failed = False
        self.arrival_counts = Counter()
        # Also reset execution state on a same-day deterministic rebuild.
        for engine in self.engines.values():
            engine.fills_this_run = 0
            engine._depth_ts = None
            engine._depth_used = {'buy': Counter(), 'sell': Counter()}
            engine.execution_counts.clear()
            engine.execution_audit.clear()
        for row in rows:
            self._consume(row['sequence'], row['tick_id'], self._decode(row['payload_json']))
        self.store.app_event('info', 'shared_arrival_paper_rebuilt',
            'Paper model restored only from its original arrival journal',
            dict(model_id=self.policy.model_id, market_date=date_text, events=len(rows),
                 sequence=self._sequence, paper_only=True))

    @staticmethod
    def _decode(payload):
        values = json.loads(payload)
        for name in ('ask_prices', 'bid_prices', 'ask_volumes', 'bid_volumes'):
            values[name] = tuple(values[name])
        return Tick(**values)

    def on_recorded_tick(self, recorded: RecordedTick):
        if not recorded.is_new or recorded.tick.code not in self.relevant_codes:
            return
        tick = recorded.tick
        date_text = tick.market_datetime.date().isoformat()
        if self.market_date is not None and date_text < self.market_date:
            self.arrival_counts['old_date_ignored'] += 1
            return
        if self.market_date != date_text or self.allocator is None:
            self.rebuild_date(date_text)
        payload = json.dumps(asdict(tick), separators=(',', ':'), ensure_ascii=False)
        connection = self.store.connection
        inserted = connection.execute('''INSERT OR IGNORE INTO maker_shared_arrival_events
            (model_id,market_date,tick_id,payload_json) VALUES (?,?,?,?)''',
            (self.policy.model_id, date_text, recorded.tick_id, payload))
        if inserted.rowcount == 0:
            self.arrival_counts['duplicate_tick_ignored'] += 1
            return
        # Journal is appended before any economic mutation. A failed event is
        # retained for deterministic recovery; never continue from partial state.
        self.store._changed(1)
        if self._failed:
            self.arrival_counts['journaled_while_failed'] += 1
            return
        try:
            self._consume(int(inserted.lastrowid), recorded.tick_id, tick)
        except Exception:
            self._failed = True
            raise

    def on_replay_tick(self, tick, *, persist):
        raise ValueError('arrival runtime must replay its recorded arrival journal, not market-sorted ticks')

    def _consume(self, sequence: int, tick_id: int, tick: Tick):
        if sequence <= self._sequence:
            raise ValueError('arrival journal sequence must strictly increase')
        self._sequence = sequence
        previous_id, previous = self._previous.get(tick.code, (None, None))
        if previous is not None and (
            tick.market_ts_ms < previous.market_ts_ms
            or tick.volume < previous.volume
            or tick.amount < previous.amount
            or tick.transaction_count < previous.transaction_count
        ):
            self.arrival_counts['same_symbol_regression_ignored'] += 1
            return
        if previous is not None and tick.snapshot_hash == previous.snapshot_hash:
            self.arrival_counts['same_symbol_snapshot_ignored'] += 1
            return
        if tick.market_ts_ms < self._decision_ms:
            self.arrival_counts['cross_symbol_older_source_accepted'] += 1
        change = TickRecorder._change(previous_id, previous, tick)
        self._previous[tick.code] = (tick_id, tick)
        self._decision_ms = max(self._decision_ms, tick.market_ts_ms, tick.received_ts_ns // 1_000_000)
        moment = datetime.fromtimestamp(self._decision_ms / 1000, tz=SHANGHAI)
        if moment.date().isoformat() != self.market_date:
            # A same-date quote received after midnight is historical data,
            # not a new trading event for the preceding paper day.
            self.arrival_counts['receipt_outside_market_date_ignored'] += 1
            return
        multiplier = 10.0 if tick.code in self.engines else 1.0
        replay = ArrivalTick(
            tick_id=tick_id, code=tick.code, market_ts_ms=self._decision_ms,
            market_date=self.market_date, market_time=moment.time().isoformat(timespec='milliseconds'),
            last_price=tick.last_price,
            bids=tuple((p, q*multiplier) for p, q in zip(tick.bid_prices, tick.bid_volumes) if p > 0),
            asks=tuple((p, q*multiplier) for p, q in zip(tick.ask_prices, tick.ask_volumes) if p > 0),
            trade_bonds=change.volume_delta*multiplier,
            transaction_delta=change.transaction_delta,
            inferred_side=change.inferred_side, side_confidence=change.side_confidence,
            previous_close=tick.previous_close, source_market_ts_ms=tick.market_ts_ms,
            received_ts_ns=tick.received_ts_ns, arrival_sequence=sequence,
        )
        self.allocator.on_replay_tick(replay)
        self._publish_new_selection_events()
        self.arrival_counts['processed'] += 1

    def runtime_summary(self):
        result = super().runtime_summary()
        status = dict(failed=self._failed, last_sequence=self._sequence,
                      decision_ts_ms=self._decision_ms, counts=dict(self.arrival_counts))
        result['arrival_execution'] = status
        for row in result['accounts']:
            row['arrival_execution'] = status
        return result
