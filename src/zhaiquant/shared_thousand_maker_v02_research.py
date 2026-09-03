from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from .config import AppConfig, load_config
from .maker import ReplayTick
from .maker_paper import (
    MakerAccount,
    MakerOrder,
    MakerPaperEngine,
    SHARED_THOUSAND_POLICY_V02_CANDIDATE,
)
from .one_hand_maker_research import (
    DEFAULT_BOND_CODES,
    SHARED_THOUSAND_BONDS,
    OneHandSharedAllocator,
    _only_account,
    replay_one_hand_day,
    run_one_hand_matrix,
)


MODEL_ID = SHARED_THOUSAND_POLICY_V02_CANDIDATE.model_id
PARENT_MODEL_ID = SHARED_THOUSAND_POLICY_V02_CANDIDATE.parent_model_id


@dataclass(frozen=True)
class AllocationParametersV02:
    """Causal capital-time and tail-risk allocation parameters for v0.2.

    The parent 2.52-r2 policy still owns entry legality.  These parameters
    decide whether cash, Three Gorges, or Jiangxi Copper should receive the
    single 1,000-bond slot after a legal intent exists.
    """

    recent_flow_window_seconds: int = 300
    minimum_evidence_scale_bonds: float = 1_000.0
    evidence_multiple: float = 5.0
    entry_flow_price_band: float = 0.03
    exit_flow_price_band: float = 0.05
    inner_support_distance: float = 0.05
    near_support_distance: float = 0.20
    protection_distance: float = 0.50
    support_level_minimum_bonds: float = 1_000.0
    maximum_support_relief: float = 0.90
    base_tail_risk_fraction: float = 0.08
    unsupported_tail_risk_fraction: float = 0.20
    sell_pressure_risk_fraction: float = 0.08
    active_fill_penalty_per_bond: float = 0.02
    active_book_gap_penalty_fraction: float = 0.25
    base_expected_lock_seconds: float = 300.0
    minimum_expected_lock_seconds: float = 60.0
    maximum_expected_lock_seconds: float = 1_800.0
    capital_time_reference_seconds: float = 300.0
    terminal_lock_penalty_weight: float = 0.50
    minimum_actionable_score_cny: float = 0.0
    switch_minimum_score_advantage_cny: float = 50.0
    switch_relative_score_advantage: float = 0.30
    minimum_selection_dwell_seconds: int = 60
    unselected_tie_tolerance_cny: float = 5.0
    shadow_intent_ttl_seconds: int = 9
    challenge_audit_limit: int = 2_000


@dataclass(frozen=True)
class AllocationScoreV02:
    bond_code: str
    order_id: int
    order_kind: str
    active_fill: bool
    shadow_intent: bool
    remaining_bonds: float
    entry_price: float
    exit_price: float
    gross_edge_per_bond: float
    local_recent_sell_bonds: float
    local_recent_buy_bonds: float
    all_recent_sell_bonds: float
    all_recent_buy_bonds: float
    inner_bid_support_bonds: float
    near_bid_support_bonds: float
    support_level_count: int
    entry_probability_proxy: float
    exit_probability_proxy: float
    support_quality: float
    downside_distance_per_bond: float
    downside_risk_fraction: float
    active_adverse_selection_penalty_per_bond: float
    expected_lock_seconds: float
    remaining_session_seconds: float
    terminal_time_factor: float
    expected_gain_per_bond: float
    expected_downside_per_bond: float
    raw_expected_value_cny: float
    capital_time_score_cny: float

    @property
    def score_cny(self) -> float:
        return self.capital_time_score_cny

    def public(self) -> dict[str, Any]:
        row = asdict(self)
        for name, value in tuple(row.items()):
            if isinstance(value, float):
                row[name] = round(value, 6)
        row["score_cny"] = row["capital_time_score_cny"]
        return row


@dataclass(frozen=True)
class ChallengeAuditEvent:
    market_ts_ms: int
    selected_before: str | None
    winner: str | None
    reason: str
    scores: tuple[AllocationScoreV02, ...]

    def public(self) -> dict[str, Any]:
        return {
            "market_ts_ms": self.market_ts_ms,
            "selected_before": self.selected_before,
            "winner": self.winner,
            "reason": self.reason,
            "scores": [score.public() for score in self.scores],
        }


def _remaining_session_seconds(market_time: str) -> float:
    hour, minute, second = market_time.split(":")
    current = int(hour) * 3_600 + int(minute) * 60 + float(second)
    morning_end = 11 * 3_600 + 30 * 60
    afternoon_start = 13 * 3_600
    close = 15 * 3_600 + 30 * 60
    if current < morning_end:
        return max(0.0, morning_end - current + close - afternoon_start)
    if current < afternoon_start:
        return float(close - afternoon_start)
    return max(0.0, close - current)


def score_buy_intent_v02(
    *, bond_code: str, engine: MakerPaperEngine, account: MakerAccount,
    order: MakerOrder, tick: ReplayTick, parameters: AllocationParametersV02,
    active_fill: bool, shared_capacity_bonds: float = SHARED_THOUSAND_BONDS,
) -> AllocationScoreV02:
    """Rank one parent-legal intent by causal value per locked capital-time."""

    del account  # Account legality and capacity were already checked upstream.
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

    cutoff = tick.market_ts_ms - parameters.recent_flow_window_seconds * 1_000
    recent = tuple(
        event for event in engine.analyzer.trade_evidence
        if cutoff <= event.market_ts_ms <= tick.market_ts_ms
    )
    all_recent_sells = sum(
        event.bonds for event in recent if event.side == "sell"
    )
    all_recent_buys = sum(
        event.bonds for event in recent if event.side == "buy"
    )
    local_recent_sells = sum(
        event.bonds for event in recent
        if event.side == "sell"
        and event.price <= entry + parameters.entry_flow_price_band + 1e-9
    )
    exit_evidence_price = min(exit_price, max(entry, tick.ask1))
    exit_evidence_floor = max(
        entry,
        exit_evidence_price - parameters.exit_flow_price_band,
    )
    local_recent_buys = sum(
        event.bonds for event in recent
        if event.side == "buy" and event.price + 1e-9 >= exit_evidence_floor
    )

    evidence_scale = max(
        parameters.minimum_evidence_scale_bonds,
        parameters.evidence_multiple
        * max(order.remaining, shared_capacity_bonds),
    )
    sell_flow = local_recent_sells / (local_recent_sells + evidence_scale)
    buy_flow = local_recent_buys / (local_recent_buys + evidence_scale)

    priority_distance = max(0.0, tick.bid1 - entry)
    priority_quality = math.exp(
        -priority_distance / max(engine.parameters.price_tick, 0.01)
    )
    entry_probability = (
        1.0
        if active_fill
        else 0.15 + 0.65 * sell_flow + 0.20 * priority_quality
    )

    inner_support = sum(
        bonds for price, bonds in tick.bids
        if entry - parameters.inner_support_distance - 1e-9
        <= price <= entry + engine.parameters.price_tick + 1e-9
    )
    near_support_levels = tuple(
        (price, bonds) for price, bonds in tick.bids
        if entry - parameters.near_support_distance - 1e-9
        <= price <= entry + engine.parameters.price_tick + 1e-9
    )
    near_support = sum(bonds for _, bonds in near_support_levels)
    support_level_count = sum(
        bonds + 1e-9 >= parameters.support_level_minimum_bonds
        for _, bonds in near_support_levels
    )
    inner_quality = min(1.0, inner_support / evidence_scale)
    near_quality = min(1.0, near_support / evidence_scale)
    distribution_quality = min(1.0, support_level_count / 2.0)
    support_quality = min(
        parameters.maximum_support_relief,
        0.35 * inner_quality
        + 0.40 * near_quality
        + 0.25 * distribution_quality,
    )

    # Exit demand comes from price-local aggressive buys and current bid-side
    # capacity.  V0.1's positive ask-size term is intentionally removed: a
    # thick sell wall is supply, not direct evidence that a future sell exits.
    exit_probability = (
        0.15 + 0.65 * buy_flow + 0.20 * inner_quality
    )

    protection_candidates = [
        price for price, bonds in tick.bids
        if 0 <= entry - price <= parameters.protection_distance + 1e-9
        and bonds + 1e-9 >= evidence_scale
    ]
    if order.protective_bid_floor_price > 0:
        protection_candidates.append(order.protective_bid_floor_price)
    protection_price = max(protection_candidates, default=0.0)
    protection_distance = (
        max(engine.parameters.price_tick, entry - protection_price)
        if protection_price > 0
        else parameters.protection_distance
    )
    immediate_book_gap = max(0.0, entry - tick.bid1)
    downside_distance = max(
        engine.parameters.price_tick,
        protection_distance,
        immediate_book_gap,
    )

    sell_pressure = local_recent_sells / (
        local_recent_sells + local_recent_buys + evidence_scale
    )
    downside_risk_fraction = min(
        1.0,
        parameters.base_tail_risk_fraction
        + parameters.unsupported_tail_risk_fraction
        * (1.0 - support_quality)
        + parameters.sell_pressure_risk_fraction * sell_pressure,
    )
    downside_penalty = downside_distance * downside_risk_fraction
    active_penalty = (
        parameters.active_fill_penalty_per_bond
        + parameters.active_book_gap_penalty_fraction * immediate_book_gap
        if active_fill else 0.0
    )

    expected_lock_seconds = min(
        parameters.maximum_expected_lock_seconds,
        max(
            parameters.minimum_expected_lock_seconds,
            parameters.base_expected_lock_seconds
            / max(exit_probability, 0.05),
        ),
    )
    remaining_session = _remaining_session_seconds(tick.market_time)
    terminal_time_factor = min(
        1.0,
        remaining_session / max(expected_lock_seconds, 1.0),
    )
    expected_gain = (
        gross_edge
        * entry_probability
        * exit_probability
        * terminal_time_factor
    )
    terminal_penalty = (
        downside_penalty
        * parameters.terminal_lock_penalty_weight
        * (1.0 - terminal_time_factor)
    )
    expected_downside = downside_penalty + active_penalty + terminal_penalty
    raw_value = order.remaining * (expected_gain - expected_downside)
    capital_time_factor = min(
        1.0,
        parameters.capital_time_reference_seconds
        / max(expected_lock_seconds, 1.0),
    )
    capital_time_score = raw_value * capital_time_factor

    return AllocationScoreV02(
        bond_code=bond_code,
        order_id=order.db_id,
        order_kind=order.kind,
        active_fill=active_fill,
        shadow_intent=False,
        remaining_bonds=order.remaining,
        entry_price=entry,
        exit_price=exit_price,
        gross_edge_per_bond=gross_edge,
        local_recent_sell_bonds=local_recent_sells,
        local_recent_buy_bonds=local_recent_buys,
        all_recent_sell_bonds=all_recent_sells,
        all_recent_buy_bonds=all_recent_buys,
        inner_bid_support_bonds=inner_support,
        near_bid_support_bonds=near_support,
        support_level_count=support_level_count,
        entry_probability_proxy=entry_probability,
        exit_probability_proxy=exit_probability,
        support_quality=support_quality,
        downside_distance_per_bond=downside_distance,
        downside_risk_fraction=downside_risk_fraction,
        active_adverse_selection_penalty_per_bond=active_penalty,
        expected_lock_seconds=expected_lock_seconds,
        remaining_session_seconds=remaining_session,
        terminal_time_factor=terminal_time_factor,
        expected_gain_per_bond=expected_gain,
        expected_downside_per_bond=expected_downside,
        raw_expected_value_cny=raw_value,
        capital_time_score_cny=capital_time_score,
    )


class SharedThousandV02Allocator(OneHandSharedAllocator):
    """V0.2 allocator with cash, short-lived shadows, and full audits."""

    def __init__(
        self, engines: dict[str, MakerPaperEngine], *,
        parameters: AllocationParametersV02 | None = None,
        initial_cash_cny: float | None = None,
        capital_ready_ts_ms: int | None = None,
        shared_capacity_bonds: float = SHARED_THOUSAND_BONDS,
    ) -> None:
        super().__init__(
            engines,
            parameters=parameters or AllocationParametersV02(),
            initial_cash_cny=initial_cash_cny,
            capital_ready_ts_ms=capital_ready_ts_ms,
            shared_capacity_bonds=shared_capacity_bonds,
        )
        self.parameters: AllocationParametersV02
        self.shadow_scores: dict[
            str, tuple[int, AllocationScoreV02]
        ] = {}
        self.decision_reason_counts: Counter[str] = Counter()
        self.challenge_events: list[ChallengeAuditEvent] = []
        self.challenge_decision_count = 0
        self.shadow_score_uses = 0
        self.effective_cross_bond_reselections = 0
        self.cross_bond_reselections_via_cash = 0
        self.last_non_null_selected_code: str | None = None

    def _score(
        self, code: str, order: MakerOrder, *, active_fill: bool,
    ) -> AllocationScoreV02 | None:
        engine = self.engines[code]
        account = _only_account(engine)
        tick = self.last_bond_ticks.get(code)
        if account is None or tick is None:
            return None
        if order.remaining * order.limit_price > self.shared_cash_cny + 1e-9:
            return None
        score = score_buy_intent_v02(
            bond_code=code,
            engine=engine,
            account=account,
            order=order,
            tick=tick,
            parameters=self.parameters,
            active_fill=active_fill,
            shared_capacity_bonds=self.shared_capacity_bonds,
        )
        if not active_fill:
            self.shadow_scores[code] = (tick.market_ts_ms, score)
        return score

    def _resting_scores(self) -> dict[str, AllocationScoreV02]:
        scores: dict[str, AllocationScoreV02] = {}
        newest_ts = max(
            (tick.market_ts_ms for tick in self.last_bond_ticks.values()),
            default=0,
        )
        for code, engine in self.engines.items():
            account = _only_account(engine)
            if account is None or account.buy_order is None:
                continue
            score = self._score(code, account.buy_order, active_fill=False)
            if score is not None:
                scores[code] = score
        ttl_ms = self.parameters.shadow_intent_ttl_seconds * 1_000
        for code, (timestamp, score) in tuple(self.shadow_scores.items()):
            if code in scores:
                continue
            if newest_ts - timestamp > ttl_ms:
                self.shadow_scores.pop(code, None)
                continue
            if (
                score.remaining_bonds * score.entry_price
                > self.shared_cash_cny + 1e-9
            ):
                continue
            scores[code] = replace(score, shadow_intent=True)
            self.shadow_score_uses += 1
        return scores

    def _finish_choice(
        self, winner: str | None, reason: str,
        scores: dict[str, AllocationScoreV02], *, market_ts_ms: int,
    ) -> tuple[str | None, str]:
        self.decision_reason_counts[reason] += 1
        if len(scores) >= 2:
            self.challenge_decision_count += 1
            if len(self.challenge_events) < self.parameters.challenge_audit_limit:
                self.challenge_events.append(ChallengeAuditEvent(
                    market_ts_ms=market_ts_ms,
                    selected_before=self.selected_code,
                    winner=winner,
                    reason=reason,
                    scores=tuple(sorted(
                        scores.values(), key=lambda item: item.bond_code,
                    )),
                ))
        return winner, reason

    def _choose(
        self, scores: dict[str, AllocationScoreV02], *, market_ts_ms: int,
    ) -> tuple[str | None, str]:
        if not scores:
            return self._finish_choice(
                None, "no_affordable_parent_intent", scores,
                market_ts_ms=market_ts_ms,
            )

        eligible = {
            code: score for code, score in scores.items()
            if score.score_cny
            > self.parameters.minimum_actionable_score_cny + 1e-9
        }
        if not eligible:
            return self._finish_choice(
                None, "cash_dominates_nonpositive_scores", scores,
                market_ts_ms=market_ts_ms,
            )

        if self.selected_code in eligible:
            current = eligible[self.selected_code]
            challengers = sorted(
                (
                    score for code, score in eligible.items()
                    if code != self.selected_code
                ),
                key=lambda item: item.score_cny,
                reverse=True,
            )
            if not challengers:
                return self._finish_choice(
                    self.selected_code, "retain_only_positive_intent", scores,
                    market_ts_ms=market_ts_ms,
                )
            challenger = challengers[0]
            if (
                not challenger.active_fill
                and self.selected_since_ts_ms > 0
                and market_ts_ms - self.selected_since_ts_ms
                < self.parameters.minimum_selection_dwell_seconds * 1_000
            ):
                return self._finish_choice(
                    self.selected_code, "retain_minimum_selection_dwell",
                    scores, market_ts_ms=market_ts_ms,
                )
            required = max(
                self.parameters.switch_minimum_score_advantage_cny,
                abs(current.score_cny)
                * self.parameters.switch_relative_score_advantage,
            )
            if challenger.score_cny + 1e-9 >= current.score_cny + required:
                return self._finish_choice(
                    challenger.bond_code,
                    "challenger_has_clear_capital_time_advantage",
                    scores, market_ts_ms=market_ts_ms,
                )
            return self._finish_choice(
                self.selected_code,
                "retain_without_clear_capital_time_advantage",
                scores, market_ts_ms=market_ts_ms,
            )

        ranked = sorted(
            eligible.values(), key=lambda item: item.score_cny, reverse=True,
        )
        if (
            len(ranked) > 1
            and abs(ranked[0].score_cny - ranked[1].score_cny)
            <= self.parameters.unselected_tie_tolerance_cny + 1e-9
        ):
            return self._finish_choice(
                None, "cash_retained_on_score_tie", scores,
                market_ts_ms=market_ts_ms,
            )
        return self._finish_choice(
            ranked[0].bond_code,
            "select_highest_positive_capital_time_score",
            scores, market_ts_ms=market_ts_ms,
        )

    def _record_selection(
        self, tick: ReplayTick, selected: str | None, reason: str,
        scores: dict[str, AllocationScoreV02],
    ) -> None:
        if selected != self.selected_code and selected is not None:
            if (
                self.last_non_null_selected_code is not None
                and selected != self.last_non_null_selected_code
            ):
                self.effective_cross_bond_reselections += 1
                if self.selected_code is None:
                    self.cross_bond_reselections_via_cash += 1
            self.last_non_null_selected_code = selected
        super()._record_selection(tick, selected, reason, scores)

    def research_metrics(self) -> dict[str, Any]:
        return {
            "allocation_v02_metrics": {
                "decision_reason_counts": dict(
                    sorted(self.decision_reason_counts.items()),
                ),
                "challenge_decisions": self.challenge_decision_count,
                "challenge_events_recorded": len(self.challenge_events),
                "shadow_score_uses": self.shadow_score_uses,
                "effective_cross_bond_reselections": (
                    self.effective_cross_bond_reselections
                ),
                "cross_bond_reselections_via_cash": (
                    self.cross_bond_reselections_via_cash
                ),
            },
            "allocation_challenge_events": [
                event.public() for event in self.challenge_events
            ],
        }


def replay_shared_thousand_v02_day(
    config: AppConfig, *, market_date: str,
    bond_codes: tuple[str, ...] = DEFAULT_BOND_CODES,
    parameters: AllocationParametersV02 | None = None,
    initial_cash_cny: float | None = None,
    capital_ready_ts_ms: int | None = None,
) -> dict[str, Any]:
    return replay_one_hand_day(
        config,
        market_date=market_date,
        bond_codes=bond_codes,
        parameters=parameters or AllocationParametersV02(),
        initial_cash_cny=initial_cash_cny,
        capital_ready_ts_ms=capital_ready_ts_ms,
        priority_policy=SHARED_THOUSAND_POLICY_V02_CANDIDATE,
        shared_capacity_bonds=SHARED_THOUSAND_BONDS,
        allocator_class=SharedThousandV02Allocator,
    )


def run_shared_thousand_v02_matrix(
    config: AppConfig, *, dates: tuple[str, ...],
    bond_codes: tuple[str, str] = DEFAULT_BOND_CODES,
    parameters: AllocationParametersV02 | None = None,
) -> dict[str, Any]:
    chosen_parameters = parameters or AllocationParametersV02()
    result = run_one_hand_matrix(
        config,
        dates=dates,
        bond_codes=bond_codes,
        parameters=chosen_parameters,
        priority_policy=SHARED_THOUSAND_POLICY_V02_CANDIDATE,
        shared_capacity_bonds=SHARED_THOUSAND_BONDS,
        allocator_class=SharedThousandV02Allocator,
    )
    metrics = [
        cell["shared"].get("allocation_v02_metrics", {})
        for cell in result["cells"]
    ]
    reason_counts: Counter[str] = Counter()
    for metric in metrics:
        reason_counts.update(metric.get("decision_reason_counts", {}))
    result["totals"]["allocation_v02_metrics"] = {
        "decision_reason_counts": dict(sorted(reason_counts.items())),
        "challenge_decisions": sum(
            metric.get("challenge_decisions", 0) for metric in metrics
        ),
        "shadow_score_uses": sum(
            metric.get("shadow_score_uses", 0) for metric in metrics
        ),
        "effective_cross_bond_reselections": sum(
            metric.get("effective_cross_bond_reselections", 0)
            for metric in metrics
        ),
        "cross_bond_reselections_via_cash": sum(
            metric.get("cross_bond_reselections_via_cash", 0)
            for metric in metrics
        ),
    }
    result["selection_contract"] = {
        "cash_is_an_explicit_third_candidate": True,
        "score_is_risk_adjusted_capital_time_value": True,
        "price_local_flow_only": True,
        "ask_size_is_not_positive_exit_demand": True,
        "support_cannot_zero_tail_risk": True,
        "short_lived_shadow_intents_preserve_comparison_state": True,
        "parameters_are_exploratory_not_user_confirmed_permanent_rules": True,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only causal replay for shared 1,000-bond maker v0.2."
        ),
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument(
        "--codes", nargs=2, default=list(DEFAULT_BOND_CODES),
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    result = run_shared_thousand_v02_matrix(
        load_config(args.config),
        dates=tuple(args.dates),
        bond_codes=tuple(args.codes),
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
