from __future__ import annotations

import argparse
import json
import math
import sqlite3
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from .config import AppConfig, load_config, maker_underlying_stock_code
from .database import SQLiteStore
from .maker import ReplayTick, _load_ticks
from .maker_paper import (
    MakerAccount,
    MakerOrder,
    MakerPaperEngine,
    MakerPolicyProfile,
    ONE_HAND_POLICY_V01_CANDIDATE,
    SHARED_THOUSAND_POLICY_V01_CANDIDATE,
    maker_comparison_strategy_id,
)
from .types import SHANGHAI


MODEL_ID = ONE_HAND_POLICY_V01_CANDIDATE.model_id
PARENT_MODEL_ID = ONE_HAND_POLICY_V01_CANDIDATE.parent_model_id
DEFAULT_BOND_CODES = ("132026.SH", "132024.SH")
ONE_HAND_BONDS = 10.0
SHARED_THOUSAND_BONDS = 1_000.0
SHARED_THOUSAND_MODEL_ID = SHARED_THOUSAND_POLICY_V01_CANDIDATE.model_id


@dataclass(frozen=True)
class AllocationParameters:
    """Transparent first-pass capital-allocation parameters.

    These values rank opportunities that have already passed the immutable
    2.52-r2 entry policy. They never create an entry permission by themselves.
    """

    recent_flow_window_seconds: int = 300
    minimum_evidence_scale_bonds: float = 1_000.0
    support_distance: float = 0.20
    protection_distance: float = 0.30
    support_multiple: float = 5.0
    downside_penalty_weight: float = 0.50
    switch_minimum_score_advantage_cny: float = 1.00
    switch_relative_score_advantage: float = 0.30
    minimum_selection_dwell_seconds: int = 60
    unselected_tie_tolerance_cny: float = 0.10


@dataclass(frozen=True)
class AllocationScore:
    bond_code: str
    order_id: int
    order_kind: str
    active_fill: bool
    entry_price: float
    exit_price: float
    gross_edge_per_bond: float
    downside_distance_per_bond: float
    recent_sell_bonds: float
    recent_buy_bonds: float
    near_bid_support_bonds: float
    entry_probability_proxy: float
    exit_probability_proxy: float
    support_quality: float
    score_cny: float

    def public(self) -> dict[str, Any]:
        row = asdict(self)
        for name in (
            "entry_price", "exit_price", "gross_edge_per_bond",
            "downside_distance_per_bond", "entry_probability_proxy",
            "exit_probability_proxy", "support_quality", "score_cny",
        ):
            row[name] = round(float(row[name]), 6)
        return row


@dataclass(frozen=True)
class AllocationEvent:
    market_ts_ms: int
    previous_bond_code: str | None
    selected_bond_code: str | None
    reason: str
    shared_cash_cny: float
    scores: tuple[AllocationScore, ...]

    def public(self) -> dict[str, Any]:
        return {
            "market_ts_ms": self.market_ts_ms,
            "market_time": datetime.fromtimestamp(
                self.market_ts_ms / 1_000, SHANGHAI,
            ).strftime("%H:%M:%S.%f")[:-3],
            "previous_bond_code": self.previous_bond_code,
            "selected_bond_code": self.selected_bond_code,
            "reason": self.reason,
            "shared_cash_cny": round(self.shared_cash_cny, 3),
            "scores": [score.public() for score in self.scores],
        }


def _only_account(engine: MakerPaperEngine) -> MakerAccount | None:
    if not engine.accounts:
        return None
    if len(engine.accounts) != 1:
        raise RuntimeError("one-hand research engine must contain one account")
    return next(iter(engine.accounts.values()))


def score_buy_intent(
    *, bond_code: str, engine: MakerPaperEngine, account: MakerAccount,
    order: MakerOrder, tick: ReplayTick, parameters: AllocationParameters,
    active_fill: bool, shared_capacity_bonds: float = ONE_HAND_BONDS,
) -> AllocationScore:
    """Rank one already-legal parent-model buy intent using causal evidence."""

    entry = order.limit_price
    visible_exit = max(entry, tick.ask1 - engine.parameters.price_tick)
    anchor = engine.analyzer.last_anchor
    if order.target_price is not None and order.target_price > entry:
        exit_price = order.target_price
    elif anchor is not None and anchor.exit_price > entry:
        exit_price = min(visible_exit, anchor.exit_price)
    else:
        exit_price = visible_exit
    gross_edge = max(0.0, exit_price - entry)

    cutoff = (
        tick.market_ts_ms - parameters.recent_flow_window_seconds * 1_000
    )
    recent = tuple(
        event for event in engine.analyzer.trade_evidence
        if cutoff <= event.market_ts_ms <= tick.market_ts_ms
    )
    recent_sells = sum(event.bonds for event in recent if event.side == "sell")
    recent_buys = sum(event.bonds for event in recent if event.side == "buy")
    evidence_scale = max(
        parameters.minimum_evidence_scale_bonds,
        parameters.support_multiple
            * max(order.remaining, shared_capacity_bonds),
    )
    sell_flow = recent_sells / (recent_sells + evidence_scale)
    buy_flow = recent_buys / (recent_buys + evidence_scale)

    priority_distance = max(0.0, tick.bid1 - entry)
    priority_quality = math.exp(
        -priority_distance / max(engine.parameters.price_tick, 0.01)
    )
    entry_probability = (
        1.0
        if active_fill
        else 0.25 + 0.55 * sell_flow + 0.20 * priority_quality
    )
    exit_probability = 0.25 + 0.55 * buy_flow + 0.20 * min(
        1.0,
        max(0.0, tick.ask1_bonds) / evidence_scale,
    )

    near_support = sum(
        bonds for price, bonds in tick.bids
        if entry - parameters.support_distance - 1e-9
        <= price <= entry + engine.parameters.price_tick + 1e-9
    )
    support_quality = min(1.0, near_support / evidence_scale)

    protection_candidates = [
        price for price, bonds in tick.bids
        if 0 <= entry - price <= parameters.protection_distance + 1e-9
        and bonds + 1e-9 >= evidence_scale
    ]
    if order.protective_bid_floor_price > 0:
        protection_candidates.append(order.protective_bid_floor_price)
    protection_price = max(protection_candidates, default=0.0)
    downside_distance = (
        max(engine.parameters.price_tick, entry - protection_price)
        if protection_price > 0
        else parameters.protection_distance
    )

    expected_gain = gross_edge * entry_probability * exit_probability
    expected_downside = (
        downside_distance
        * (1.0 - support_quality)
        * parameters.downside_penalty_weight
    )
    score_cny = order.remaining * (expected_gain - expected_downside)
    return AllocationScore(
        bond_code=bond_code,
        order_id=order.db_id,
        order_kind=order.kind,
        active_fill=active_fill,
        entry_price=entry,
        exit_price=exit_price,
        gross_edge_per_bond=gross_edge,
        downside_distance_per_bond=downside_distance,
        recent_sell_bonds=recent_sells,
        recent_buy_bonds=recent_buys,
        near_bid_support_bonds=near_support,
        entry_probability_proxy=entry_probability,
        exit_probability_proxy=exit_probability,
        support_quality=support_quality,
        score_cny=score_cny,
    )


class OneHandSharedAllocator:
    """Enforce one shared fixed-size cash slot across multiple bond engines."""

    def __init__(
        self, engines: dict[str, MakerPaperEngine], *,
        parameters: AllocationParameters | None = None,
        initial_cash_cny: float | None = None,
        capital_ready_ts_ms: int | None = None,
        shared_capacity_bonds: float = ONE_HAND_BONDS,
    ) -> None:
        if not engines:
            raise ValueError("shared-capital allocator requires at least one bond")
        if shared_capacity_bonds <= 0:
            raise ValueError("shared capacity must be positive")
        self.engines = engines
        self.shared_capacity_bonds = float(shared_capacity_bonds)
        self.parameters = parameters or AllocationParameters()
        self.selected_code: str | None = None
        self.selected_since_ts_ms = 0
        self.last_bond_ticks: dict[str, ReplayTick] = {}
        self.opening_quotes: dict[str, tuple[int, float]] = {}
        self.initial_cash_cny = float(initial_cash_cny or 0.0)
        self.shared_cash_cny = float(initial_cash_cny or 0.0)
        self.capital_ready_ts_ms = int(capital_ready_ts_ms or 0)
        self.events: list[AllocationEvent] = []
        self.rejected_for_cash = 0
        self.maximum_aggregate_inventory_bonds = 0.0
        self.maximum_simultaneous_buy_orders = 0
        for code, engine in self.engines.items():
            engine.buy_fill_guard = self._guard_for(code)
            engine.fill_observer = self._observe_fill

    def _guard_for(self, code: str):
        def guard(
            account: MakerAccount, tick: ReplayTick, order: MakerOrder,
            quantity: float, kind: str, reason: str,
        ) -> bool:
            return self._allow_buy_fill(
                code, account, tick, order, quantity, kind, reason,
            )

        return guard

    @property
    def capital_ready(self) -> bool:
        return self.initial_cash_cny > 0 and self.capital_ready_ts_ms > 0

    def _capital_available_at(self, market_ts_ms: int) -> bool:
        return self.capital_ready and market_ts_ms >= self.capital_ready_ts_ms

    def _observe_opening_quote(
        self, code: str, engine: MakerPaperEngine, tick: ReplayTick,
    ) -> None:
        if code in self.opening_quotes or tick.ask1 <= 0:
            return
        if tick.market_time < engine.parameters.effective_earliest_entry_time(
            tick.market_date,
        ):
            return
        self.opening_quotes[code] = (tick.market_ts_ms, tick.ask1)
        if self.capital_ready or len(self.opening_quotes) != len(self.engines):
            return
        # Funding is fixed once both live opening offers have been observed.
        # The higher one guarantees that the shared account can choose either
        # bond at that causal setup moment, but it cannot later recapitalize.
        self.initial_cash_cny = (
            max(price for _, price in self.opening_quotes.values())
            * self.shared_capacity_bonds
        )
        self.shared_cash_cny = self.initial_cash_cny
        self.capital_ready_ts_ms = max(
            timestamp for timestamp, _ in self.opening_quotes.values()
        )

    def _holdings(self) -> tuple[str, ...]:
        return tuple(
            code for code, engine in self.engines.items()
            if (account := _only_account(engine)) is not None
            and account.inventory > 1e-9
        )

    def _score(
        self, code: str, order: MakerOrder, *, active_fill: bool,
    ) -> AllocationScore | None:
        engine = self.engines[code]
        account = _only_account(engine)
        tick = self.last_bond_ticks.get(code)
        if account is None or tick is None:
            return None
        if order.remaining * order.limit_price > self.shared_cash_cny + 1e-9:
            return None
        return score_buy_intent(
            bond_code=code,
            engine=engine,
            account=account,
            order=order,
            tick=tick,
            parameters=self.parameters,
            active_fill=active_fill,
            shared_capacity_bonds=self.shared_capacity_bonds,
        )

    def _resting_scores(self) -> dict[str, AllocationScore]:
        scores: dict[str, AllocationScore] = {}
        for code, engine in self.engines.items():
            account = _only_account(engine)
            if account is None or account.buy_order is None:
                continue
            score = self._score(code, account.buy_order, active_fill=False)
            if score is not None:
                scores[code] = score
        return scores

    def _choose(
        self, scores: dict[str, AllocationScore], *, market_ts_ms: int,
    ) -> tuple[str | None, str]:
        if not scores:
            return None, "no_affordable_parent_intent"
        if self.selected_code in scores:
            current = scores[self.selected_code]
            challengers = sorted(
                (
                    score for code, score in scores.items()
                    if code != self.selected_code
                ),
                key=lambda item: item.score_cny,
                reverse=True,
            )
            if not challengers:
                return self.selected_code, "retain_only_legal_intent"
            challenger = challengers[0]
            if (
                not challenger.active_fill
                and self.selected_since_ts_ms > 0
                and market_ts_ms - self.selected_since_ts_ms
                    < self.parameters.minimum_selection_dwell_seconds * 1_000
            ):
                return self.selected_code, "retain_minimum_selection_dwell"
            required = max(
                self.parameters.switch_minimum_score_advantage_cny,
                abs(current.score_cny)
                    * self.parameters.switch_relative_score_advantage,
            )
            if challenger.score_cny + 1e-9 >= current.score_cny + required:
                return challenger.bond_code, "challenger_has_clear_score_advantage"
            return self.selected_code, "retain_queue_without_clear_advantage"

        ranked = sorted(
            scores.values(), key=lambda item: item.score_cny, reverse=True,
        )
        if (
            len(ranked) > 1
            and abs(ranked[0].score_cny - ranked[1].score_cny)
                <= self.parameters.unselected_tie_tolerance_cny + 1e-9
        ):
            return None, "unselected_score_tie"
        return ranked[0].bond_code, "select_highest_causal_score"

    def _cancel_order(
        self, code: str, tick: ReplayTick, reason: str,
    ) -> None:
        engine = self.engines[code]
        account = _only_account(engine)
        if account is None or account.buy_order is None:
            return
        engine._cancel_order(
            account, account.buy_order, tick, reason, persist=True,
        )

    def _record_selection(
        self, tick: ReplayTick, selected: str | None, reason: str,
        scores: dict[str, AllocationScore],
    ) -> None:
        if selected == self.selected_code:
            return
        self.events.append(AllocationEvent(
            market_ts_ms=tick.market_ts_ms,
            previous_bond_code=self.selected_code,
            selected_bond_code=selected,
            reason=reason,
            shared_cash_cny=self.shared_cash_cny,
            scores=tuple(sorted(scores.values(), key=lambda item: item.bond_code)),
        ))
        self.selected_code = selected
        self.selected_since_ts_ms = tick.market_ts_ms if selected else 0

    def _apply_winner(
        self, tick: ReplayTick, winner: str | None, reason: str,
        scores: dict[str, AllocationScore],
    ) -> None:
        for code in self.engines:
            if code != winner:
                self._cancel_order(
                    code, tick, "shared_capital_preferred_other_bond",
                )
        self._record_selection(tick, winner, reason, scores)

    def _allow_buy_fill(
        self, code: str, account: MakerAccount, tick: ReplayTick,
        order: MakerOrder, quantity: float, kind: str, reason: str,
    ) -> bool:
        if not self._capital_available_at(tick.market_ts_ms):
            return False
        holdings = self._holdings()
        if holdings and holdings != (code,):
            return False
        cost = quantity * order.limit_price
        if cost > self.shared_cash_cny + 1e-9:
            self.rejected_for_cash += 1
            return False

        scores = self._resting_scores()
        active_score = self._score(
            code, order, active_fill=(reason != "passive_buy"),
        )
        if active_score is None:
            self.rejected_for_cash += 1
            return False
        scores[code] = active_score
        winner, selection_reason = self._choose(
            scores, market_ts_ms=tick.market_ts_ms,
        )
        if winner != code:
            return False

        # If the same engine is crossing an active opportunity, remove any
        # older passive intent before the fill so the shared slot has one
        # unambiguous economic purpose.
        if (
            account.buy_order is not None
            and account.buy_order.db_id != order.db_id
        ):
            self.engines[code]._cancel_order(
                account,
                account.buy_order,
                tick,
                "active_shared_capital_opportunity_replaced_passive_intent",
                persist=True,
            )
        self._apply_winner(tick, winner, selection_reason, scores)
        self.shared_cash_cny -= cost
        return True

    def _observe_fill(
        self, account: MakerAccount, tick: ReplayTick, order: MakerOrder,
        side: str, quantity: float, reason: str,
    ) -> None:
        if side == "sell":
            self.shared_cash_cny += quantity * order.limit_price

    def _reconcile(self, tick: ReplayTick) -> None:
        if not self._capital_available_at(tick.market_ts_ms):
            self._apply_winner(tick, None, "waiting_for_shared_opening_cash", {})
            return
        holdings = self._holdings()
        if len(holdings) > 1:
            raise AssertionError(
                "shared-capital allocator held two bonds simultaneously"
            )
        if holdings:
            winner = holdings[0]
            self._apply_winner(tick, winner, "position_locks_shared_cash", {})
            return
        scores = self._resting_scores()
        winner, reason = self._choose(
            scores, market_ts_ms=tick.market_ts_ms,
        )
        self._apply_winner(tick, winner, reason, scores)

    def _assert_invariants(self) -> None:
        accounts = tuple(
            account for engine in self.engines.values()
            if (account := _only_account(engine)) is not None
        )
        total_inventory = sum(account.inventory for account in accounts)
        open_buy_orders = sum(
            account.buy_order is not None for account in accounts
        )
        self.maximum_aggregate_inventory_bonds = max(
            self.maximum_aggregate_inventory_bonds, total_inventory,
        )
        self.maximum_simultaneous_buy_orders = max(
            self.maximum_simultaneous_buy_orders, open_buy_orders,
        )
        if total_inventory > self.shared_capacity_bonds + 1e-9:
            raise AssertionError("aggregate inventory exceeded shared capacity")
        if open_buy_orders > 1:
            raise AssertionError("more than one shared-capital buy order remained")

    def on_replay_tick(self, tick: ReplayTick) -> None:
        for code, engine in self.engines.items():
            if tick.code == code:
                self.last_bond_ticks[code] = tick
                self._observe_opening_quote(code, engine, tick)
            if tick.code in {code, engine.stock_code}:
                engine.on_replay_tick(tick, persist=True)
        self._reconcile(tick)
        self._assert_invariants()


SharedCapitalAllocator = OneHandSharedAllocator


def _small_account_config(
    config: AppConfig, database: Path, *,
    shared_capacity_bonds: float = ONE_HAND_BONDS,
) -> AppConfig:
    return replace(
        config,
        storage=replace(config.storage, database=database),
        maker_paper=replace(
            config.maker_paper,
            enabled=True,
            initial_inventory_bonds=0.0,
            additional_buying_capacity_bonds=shared_capacity_bonds,
            maximum_inventory_bonds=shared_capacity_bonds,
            initial_cash_cny=0.0,
            order_quantity_bonds=shared_capacity_bonds,
            fill_modes=(),
            realtime_comparison_model_ids=(),
            super_windfall_enabled=False,
        ),
    )


def _merged_ticks(
    source: sqlite3.Connection, config: AppConfig,
    market_date: str, engines: dict[str, MakerPaperEngine],
) -> list[ReplayTick]:
    ticks_by_id: dict[int, ReplayTick] = {}
    for code, engine in engines.items():
        for tick in _load_ticks(
            source,
            market_date,
            code,
            maker_underlying_stock_code(config, code),
            engine.parameters,
        ):
            ticks_by_id[tick.tick_id] = tick
    return sorted(
        ticks_by_id.values(),
        key=lambda tick: (tick.market_ts_ms, tick.tick_id),
    )


def _fill_rows(
    store: SQLiteStore, market_date: str,
    strategy_to_code: dict[str, str],
) -> list[dict[str, Any]]:
    rows = []
    for row in store.connection.execute(
        """SELECT id,strategy_id,market_ts_ms,side,price,quantity,
                  fill_reason,inventory_after
             FROM maker_paper_fills
            WHERE market_date=? ORDER BY market_ts_ms,id""",
        (market_date,),
    ):
        item = dict(row)
        item["bond_code"] = strategy_to_code[item["strategy_id"]]
        item["market_time"] = datetime.fromtimestamp(
            item["market_ts_ms"] / 1_000, SHANGHAI,
        ).strftime("%H:%M:%S.%f")[:-3]
        item["price"] = round(float(item["price"]), 3)
        item["quantity"] = float(item["quantity"])
        item["inventory_after"] = float(item["inventory_after"])
        rows.append(item)
    return rows


def _exposure_seconds(
    fills: Iterable[dict[str, Any]], terminal_ts_ms: int, *,
    shared_capacity_bonds: float = ONE_HAND_BONDS,
) -> float:
    inventory = 0.0
    last_ts: int | None = None
    bond_seconds = 0.0
    for fill in fills:
        timestamp = int(fill["market_ts_ms"])
        if last_ts is not None and timestamp >= last_ts:
            bond_seconds += inventory * (timestamp - last_ts) / 1_000
        if fill["side"] == "buy":
            inventory += float(fill["quantity"])
        else:
            inventory -= float(fill["quantity"])
        last_ts = timestamp
    if last_ts is not None and terminal_ts_ms >= last_ts:
        bond_seconds += inventory * (terminal_ts_ms - last_ts) / 1_000
    return bond_seconds / shared_capacity_bonds


def replay_one_hand_day(
    config: AppConfig, *, market_date: str,
    bond_codes: tuple[str, ...] = DEFAULT_BOND_CODES,
    parameters: AllocationParameters | None = None,
    initial_cash_cny: float | None = None,
    capital_ready_ts_ms: int | None = None,
    priority_policy: MakerPolicyProfile = ONE_HAND_POLICY_V01_CANDIDATE,
    shared_capacity_bonds: float = ONE_HAND_BONDS,
    allocator_class: type[OneHandSharedAllocator] = OneHandSharedAllocator,
    cutoff_time: str | None = None,
    observer: Callable[[OneHandSharedAllocator, ReplayTick | None], None] | None = None,
    engine_class: type[MakerPaperEngine] = MakerPaperEngine,
) -> dict[str, Any]:
    """Run one read-only causal day with one shared fixed-size cash slot."""

    source_path = config.storage.database.resolve()
    source = sqlite3.connect(
        f"file:{source_path.as_posix()}?mode=ro", uri=True,
    )
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    source.execute("BEGIN")
    try:
        with tempfile.TemporaryDirectory() as temporary:
            replay_config = _small_account_config(
                config, Path(temporary) / "one-hand-replay.sqlite3",
                shared_capacity_bonds=shared_capacity_bonds,
            )
            store = SQLiteStore(replay_config)
            store.start_session()
            try:
                engines: dict[str, MakerPaperEngine] = {}
                strategy_to_code: dict[str, str] = {}
                for code in bond_codes:
                    strategy_id = maker_comparison_strategy_id(
                        replay_config, code, priority_policy,
                    )
                    strategy_to_code[strategy_id] = code
                    engines[code] = engine_class(
                        replay_config,
                        store,
                        bond_code=code,
                        priority_policy=priority_policy,
                        fill_modes=("priority",),
                        include_windfall=False,
                        strategy_ids_by_mode={"priority": strategy_id},
                    )
                allocator = allocator_class(
                    engines,
                    parameters=parameters,
                    initial_cash_cny=initial_cash_cny,
                    capital_ready_ts_ms=capital_ready_ts_ms,
                    shared_capacity_bonds=shared_capacity_bonds,
                )
                ticks = _merged_ticks(
                    source, replay_config, market_date, engines,
                )
                if observer is not None:
                    observer(allocator, None)
                for tick in ticks:
                    if cutoff_time is not None and tick.market_time[:8] > cutoff_time:
                        continue
                    allocator.on_replay_tick(tick)
                    if observer is not None:
                        observer(allocator, tick)
                store.connection.commit()

                fills = _fill_rows(store, market_date, strategy_to_code)
                accounts = {
                    code: _only_account(engine)
                    for code, engine in engines.items()
                }
                terminal_ts_ms = max(
                    (account.last_market_ts_ms for account in accounts.values()
                     if account is not None),
                    default=0,
                )
                terminal_inventory = sum(
                    account.inventory for account in accounts.values()
                    if account is not None
                )
                terminal_value = sum(
                    account.inventory * max(0.0, account.last_bid)
                    for account in accounts.values()
                    if account is not None
                )
                trading_pnl = (
                    allocator.shared_cash_cny
                    + terminal_value
                    - allocator.initial_cash_cny
                )
                pnl_by_code = {}
                for code, account in accounts.items():
                    cash_flow = sum(
                        (
                            -fill["price"] * fill["quantity"]
                            if fill["side"] == "buy"
                            else fill["price"] * fill["quantity"]
                        )
                        for fill in fills if fill["bond_code"] == code
                    )
                    mark = (
                        account.inventory * max(0.0, account.last_bid)
                        if account is not None else 0.0
                    )
                    pnl_by_code[code] = round(cash_flow + mark, 6)

                order_counts = {
                    f"{row['status']}:{row['cancel_reason'] or ''}": int(row["n"])
                    for row in store.connection.execute(
                        """SELECT status,cancel_reason,COUNT(*) AS n
                             FROM maker_paper_orders
                            WHERE market_date=?
                            GROUP BY status,cancel_reason
                            ORDER BY status,cancel_reason""",
                        (market_date,),
                    )
                }
                assignments = [
                    dict(row) for row in store.connection.execute(
                        """SELECT strategy_id,bond_code,model_id,model_version,
                                  parent_model_id,execution_mode
                             FROM maker_paper_model_assignments
                            WHERE market_date=? ORDER BY bond_code""",
                        (market_date,),
                    )
                ]
                switches = sum(
                    event.previous_bond_code is not None
                    and event.selected_bond_code is not None
                    and event.previous_bond_code != event.selected_bond_code
                    for event in allocator.events
                )
                result = {
                    "market_date": market_date,
                    "source_database_opened_readonly": True,
                    "temporary_replay_database": True,
                    "model_id": priority_policy.model_id,
                    "parent_model_id": priority_policy.parent_model_id,
                    "bond_codes": list(bond_codes),
                    "initial_inventory_bonds": 0.0,
                    "shared_buying_capacity_bonds": shared_capacity_bonds,
                    "initial_cash_cny": round(allocator.initial_cash_cny, 3),
                    "terminal_cash_cny": round(allocator.shared_cash_cny, 3),
                    "capital_ready_ts_ms": allocator.capital_ready_ts_ms,
                    "capital_ready_time": (
                        datetime.fromtimestamp(
                            allocator.capital_ready_ts_ms / 1_000, SHANGHAI,
                        ).strftime("%H:%M:%S.%f")[:-3]
                        if allocator.capital_ready_ts_ms else None
                    ),
                    "opening_quotes": {
                        code: {
                            "market_ts_ms": timestamp,
                            "ask1": round(price, 3),
                        }
                        for code, (timestamp, price)
                        in allocator.opening_quotes.items()
                    },
                    "trading_pnl": round(trading_pnl, 6),
                    "pnl_by_code": pnl_by_code,
                    "terminal_inventory_bonds": round(
                        terminal_inventory, 6,
                    ),
                    "terminal_position_code": next(
                        (
                            code for code, account in accounts.items()
                            if account is not None and account.inventory > 1e-9
                        ),
                        None,
                    ),
                    "terminal_mark_value_cny": round(terminal_value, 6),
                    "equivalent_full_slot_exposure_seconds": round(
                        _exposure_seconds(
                            fills,
                            terminal_ts_ms,
                            shared_capacity_bonds=shared_capacity_bonds,
                        ),
                        3,
                    ),
                    "equivalent_full_hand_exposure_seconds": round(
                        _exposure_seconds(
                            fills,
                            terminal_ts_ms,
                            shared_capacity_bonds=shared_capacity_bonds,
                        ),
                        3,
                    ),
                    "fill_count": len(fills),
                    "buy_bonds": round(sum(
                        fill["quantity"] for fill in fills
                        if fill["side"] == "buy"
                    ), 6),
                    "sell_bonds": round(sum(
                        fill["quantity"] for fill in fills
                        if fill["side"] == "sell"
                    ), 6),
                    "allocation_switches": switches,
                    "rejected_buy_fills_for_cash": allocator.rejected_for_cash,
                    "maximum_aggregate_inventory_bonds": round(
                        allocator.maximum_aggregate_inventory_bonds, 6,
                    ),
                    "maximum_simultaneous_buy_orders": (
                        allocator.maximum_simultaneous_buy_orders
                    ),
                    "one_hand_inventory_invariant": (
                        allocator.maximum_aggregate_inventory_bonds
                            <= shared_capacity_bonds + 1e-9
                    ),
                    "shared_capacity_inventory_invariant": (
                        allocator.maximum_aggregate_inventory_bonds
                            <= shared_capacity_bonds + 1e-9
                    ),
                    "one_buy_order_invariant": (
                        allocator.maximum_simultaneous_buy_orders <= 1
                    ),
                    "order_counts": order_counts,
                    "model_assignments": assignments,
                    "allocation_events": [
                        event.public() for event in allocator.events
                    ],
                    "fills": fills,
                }
                research_metrics = getattr(
                    allocator, "research_metrics", None,
                )
                if research_metrics is not None:
                    result.update(research_metrics())
                return result
            finally:
                store.close()
    finally:
        source.close()


def run_one_hand_matrix(
    config: AppConfig, *, dates: tuple[str, ...],
    bond_codes: tuple[str, str] = DEFAULT_BOND_CODES,
    parameters: AllocationParameters | None = None,
    priority_policy: MakerPolicyProfile = ONE_HAND_POLICY_V01_CANDIDATE,
    shared_capacity_bonds: float = ONE_HAND_BONDS,
    allocator_class: type[OneHandSharedAllocator] = OneHandSharedAllocator,
) -> dict[str, Any]:
    """Compare causal shared allocation with static and noncausal bounds."""

    cells = []
    for market_date in dates:
        shared = replay_one_hand_day(
            config,
            market_date=market_date,
            bond_codes=bond_codes,
            parameters=parameters,
            priority_policy=priority_policy,
            shared_capacity_bonds=shared_capacity_bonds,
            allocator_class=allocator_class,
        )
        static = {
            code: replay_one_hand_day(
                config,
                market_date=market_date,
                bond_codes=(code,),
                parameters=parameters,
                initial_cash_cny=shared["initial_cash_cny"],
                capital_ready_ts_ms=shared["capital_ready_ts_ms"],
                priority_policy=priority_policy,
                shared_capacity_bonds=shared_capacity_bonds,
                allocator_class=allocator_class,
            )
            for code in bond_codes
        }
        static_pnls = {
            code: result["trading_pnl"] for code, result in static.items()
        }
        cells.append({
            "market_date": market_date,
            "shared": shared,
            "static_by_code": static,
            "independent_two_slot_pnl_upper_bound": round(
                sum(static_pnls.values()), 6,
            ),
            # Retained for the already-registered 10-bond report schema.
            "independent_two_hand_pnl_upper_bound": round(
                sum(static_pnls.values()), 6,
            ),
            "hindsight_daily_best_static_pnl_upper_bound": round(
                max(static_pnls.values()), 6,
            ),
            "hindsight_daily_best_static_code": max(
                static_pnls, key=static_pnls.get,
            ),
        })

    def total(path: tuple[str, ...]) -> float:
        values = []
        for cell in cells:
            value: Any = cell
            for key in path:
                value = value[key]
            values.append(float(value))
        return round(sum(values), 6)

    totals = {
        "shared_causal_pnl": total(("shared", "trading_pnl")),
        "static_pnl_by_code": {
            code: round(sum(
                cell["static_by_code"][code]["trading_pnl"]
                for cell in cells
            ), 6)
            for code in bond_codes
        },
        "independent_two_slot_pnl_upper_bound": total(
            ("independent_two_slot_pnl_upper_bound",),
        ),
        "independent_two_hand_pnl_upper_bound": total(
            ("independent_two_slot_pnl_upper_bound",),
        ),
        "hindsight_daily_best_static_pnl_upper_bound": total(
            ("hindsight_daily_best_static_pnl_upper_bound",),
        ),
        "shared_fill_count": int(sum(
            cell["shared"]["fill_count"] for cell in cells
        )),
        "shared_allocation_switches": int(sum(
            cell["shared"]["allocation_switches"] for cell in cells
        )),
        "shared_terminal_inventory_days": int(sum(
            cell["shared"]["terminal_inventory_bonds"] > 1e-9
            for cell in cells
        )),
        "shared_equivalent_full_slot_exposure_seconds": round(sum(
            cell["shared"]["equivalent_full_slot_exposure_seconds"]
            for cell in cells
        ), 3),
        "shared_equivalent_full_hand_exposure_seconds": round(sum(
            cell["shared"]["equivalent_full_slot_exposure_seconds"]
            for cell in cells
        ), 3),
        "shared_negative_days": int(sum(
            cell["shared"]["trading_pnl"] < -1e-9 for cell in cells
        )),
        "shared_positive_days": int(sum(
            cell["shared"]["trading_pnl"] > 1e-9 for cell in cells
        )),
        "all_inventory_invariants_hold": all(
            cell["shared"]["shared_capacity_inventory_invariant"]
            and all(
                result["shared_capacity_inventory_invariant"]
                for result in cell["static_by_code"].values()
            )
            for cell in cells
        ),
        "all_order_invariants_hold": all(
            cell["shared"]["one_buy_order_invariant"]
            for cell in cells
        ),
    }
    closed_matches: list[dict[str, Any]] = []
    terminal_positions: list[dict[str, Any]] = []
    total_traded_notional = 0.0
    for cell in cells:
        queues: dict[str, list[list[Any]]] = {
            code: [] for code in bond_codes
        }
        closed_pnl_by_code = {code: 0.0 for code in bond_codes}
        for fill in cell["shared"]["fills"]:
            code = fill["bond_code"]
            total_traded_notional += fill["price"] * fill["quantity"]
            if fill["side"] == "buy":
                queues[code].append([
                    fill["quantity"], fill["price"], fill["market_time"],
                ])
                continue
            remaining = fill["quantity"]
            while remaining > 1e-9:
                lot = queues[code][0]
                quantity = min(remaining, lot[0])
                match_pnl = (fill["price"] - lot[1]) * quantity
                closed_pnl_by_code[code] += match_pnl
                closed_matches.append({
                    "market_date": cell["market_date"],
                    "bond_code": code,
                    "buy_time": lot[2],
                    "sell_time": fill["market_time"],
                    "buy_price": lot[1],
                    "sell_price": fill["price"],
                    "quantity_bonds": quantity,
                    "gross_pnl": round(match_pnl, 6),
                })
                lot[0] -= quantity
                remaining -= quantity
                if lot[0] <= 1e-9:
                    queues[code].pop(0)
        for code, queue in queues.items():
            if not queue:
                continue
            terminal_positions.append({
                "market_date": cell["market_date"],
                "bond_code": code,
                "open_lots": [
                    {
                        "quantity_bonds": lot[0],
                        "entry_price": lot[1],
                        "entry_time": lot[2],
                    }
                    for lot in queue
                ],
                "terminal_unrealized_pnl": round(
                    cell["shared"]["pnl_by_code"][code]
                        - closed_pnl_by_code[code],
                    6,
                ),
            })
    closed_matches.sort(key=lambda row: row["gross_pnl"])
    terminal_positions.sort(
        key=lambda row: row["terminal_unrealized_pnl"],
    )
    fill_events = totals["shared_fill_count"]
    totals["path_audit"] = {
        "closed_fifo_matches": len(closed_matches),
        "profitable_closed_matches": sum(
            row["gross_pnl"] > 1e-9 for row in closed_matches
        ),
        "losing_closed_matches": sum(
            row["gross_pnl"] < -1e-9 for row in closed_matches
        ),
        "flat_closed_matches": sum(
            abs(row["gross_pnl"]) <= 1e-9 for row in closed_matches
        ),
        "closed_gross_pnl": round(sum(
            row["gross_pnl"] for row in closed_matches
        ), 6),
        "terminal_unrealized_pnl": round(sum(
            row["terminal_unrealized_pnl"] for row in terminal_positions
        ), 6),
        "worst_closed_matches": closed_matches[:10],
        "terminal_positions": terminal_positions,
    }
    totals["fee_stress"] = {
        "gross_pnl": totals["shared_causal_pnl"],
        "fill_events": fill_events,
        "two_sided_traded_notional_cny": round(total_traded_notional, 2),
        "net_after_fixed_0_10_cny_per_fill": round(
            totals["shared_causal_pnl"] - 0.10 * fill_events, 6,
        ),
        "net_after_fixed_0_50_cny_per_fill": round(
            totals["shared_causal_pnl"] - 0.50 * fill_events, 6,
        ),
        "net_after_fixed_1_00_cny_per_fill": round(
            totals["shared_causal_pnl"] - 1.00 * fill_events, 6,
        ),
        "net_after_0_5_bp_each_fill_notional": round(
            totals["shared_causal_pnl"] - total_traded_notional * 0.00005,
            6,
        ),
        "net_after_1_0_bp_each_fill_notional": round(
            totals["shared_causal_pnl"] - total_traded_notional * 0.00010,
            6,
        ),
        "break_even_fixed_cost_cny_per_fill": round(
            totals["shared_causal_pnl"] / fill_events
            if fill_events else 0.0,
            6,
        ),
    }
    return {
        "model_id": priority_policy.model_id,
        "parent_model_id": priority_policy.parent_model_id,
        "status": "offline_candidate_not_in_realtime_matrix",
        "source_database_opened_readonly": True,
        "same_parent_policy_for_all_codes": True,
        "security_specific_allocation_conditions": False,
        "account": {
            "opening_base_inventory_bonds": 0.0,
            "shared_buying_capacity_bonds": shared_capacity_bonds,
            "shared_cash_slots": 1,
            "cash_is_fixed_after_both_opening_quotes": True,
            "recapitalization_after_losses": False,
        },
        "allocation_parameters": asdict(parameters or AllocationParameters()),
        "benchmark_boundaries": {
            "independent_two_slots": (
                f"Uses one {shared_capacity_bonds:g}-bond cash slot per bond "
                "and is not the same-capital strategy."
            ),
            "hindsight_daily_best_static": (
                "Uses the completed day's winning bond and is a future-information upper bound."
            ),
        },
        "dates": list(dates),
        "totals": totals,
        "cells": cells,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only causal replay for the zero-base shared one-hand maker candidate."
        ),
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument(
        "--codes", nargs=2, default=list(DEFAULT_BOND_CODES),
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--switch-minimum-cny", type=float, default=1.00)
    parser.add_argument("--switch-relative-advantage", type=float, default=0.30)
    parser.add_argument("--selection-dwell-seconds", type=int, default=60)
    parser.add_argument("--tie-tolerance-cny", type=float, default=0.10)
    args = parser.parse_args()

    parameters = AllocationParameters(
        switch_minimum_score_advantage_cny=args.switch_minimum_cny,
        switch_relative_score_advantage=args.switch_relative_advantage,
        minimum_selection_dwell_seconds=args.selection_dwell_seconds,
        unselected_tie_tolerance_cny=args.tie_tolerance_cny,
    )
    result = run_one_hand_matrix(
        load_config(args.config),
        dates=tuple(args.dates),
        bond_codes=tuple(args.codes),
        parameters=parameters,
    )
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(output),
        "model_id": result["model_id"],
        "dates": result["dates"],
        "totals": result["totals"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
