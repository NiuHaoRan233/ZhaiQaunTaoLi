from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig, load_config
from .maker import ReplayTick
from .maker_paper import (
    MakerAccount,
    MakerOrder,
    MakerPaperEngine,
    SHARED_THOUSAND_POLICY_V03_CANDIDATE,
)
from .one_hand_maker_research import (
    DEFAULT_BOND_CODES,
    SHARED_THOUSAND_BONDS,
    _only_account,
    replay_one_hand_day,
    run_one_hand_matrix,
)
from .shared_thousand_maker_v02_research import (
    AllocationParametersV02,
    AllocationScoreV02,
    SharedThousandV02Allocator,
    _remaining_session_seconds,
    score_buy_intent_v02,
)


MODEL_ID = SHARED_THOUSAND_POLICY_V03_CANDIDATE.model_id
PARENT_MODEL_ID = SHARED_THOUSAND_POLICY_V03_CANDIDATE.parent_model_id
SESSION_RESILIENT_ORDER_KIND = "session_resilient_value_entry"


@dataclass(frozen=True)
class AllocationParametersV03(AllocationParametersV02):
    """V0.2 capital-time score plus causal same-day resilience evidence."""

    session_entry_reach_price_band: float = 0.15
    session_entry_reach_bond_scale: float = 5_000.0
    session_entry_probability_weight: float = 0.15
    session_exit_probability_weight: float = 0.45
    maximum_entry_probability: float = 0.95
    maximum_exit_probability: float = 0.95


@dataclass(frozen=True)
class SessionEvidenceV03:
    high_trade_floor_price: float
    high_trade_ceiling_price: float
    high_trade_bonds: float
    high_trade_events: int
    high_trade_transactions: int
    high_buy_bonds: float
    high_buy_events: int
    total_session_trade_bonds: float
    high_trade_share: float
    high_trade_span_seconds: float
    high_trade_bucket_count: int
    near_support_bonds: float
    resilience_quality: float
    entry_reach_sell_bonds: float
    entry_reach_sell_events: int
    entry_reach_quality: float


@dataclass(frozen=True)
class AllocationScoreV03(AllocationScoreV02):
    session_resilience_eligible: bool
    high_trade_floor_price: float
    high_trade_ceiling_price: float
    session_high_trade_bonds: float
    session_high_trade_events: int
    session_high_trade_transactions: int
    session_high_buy_bonds: float
    session_high_buy_events: int
    total_session_trade_bonds: float
    session_high_trade_share: float
    session_high_trade_span_seconds: float
    session_high_trade_bucket_count: int
    session_near_support_bonds: float
    session_resilience_quality: float
    session_entry_reach_sell_bonds: float
    session_entry_reach_sell_events: int
    session_entry_reach_quality: float
    entry_probability_without_session: float
    exit_probability_without_session: float
    session_exit_actionability_factor: float
    session_entry_probability_bonus: float
    session_exit_probability_bonus: float


def _scaled(value: float, scale: float) -> float:
    return min(1.0, value / max(scale, 1e-9))


def session_evidence_v03(
    *, engine: MakerPaperEngine, tick: ReplayTick, entry_price: float,
    parameters: AllocationParametersV03,
) -> SessionEvidenceV03:
    """Summarize only same-day evidence available at the current frame."""

    policy = engine.priority_policy
    high_floor = entry_price + policy.session_resilient_minimum_exit_edge
    high_ceiling = (
        tick.ask1 + policy.session_resilient_high_trade_ceiling_above_ask
    )
    session = tuple(
        event for event in engine.analyzer.session_trade_evidence
        if event.market_ts_ms <= tick.market_ts_ms
    )
    high = tuple(
        event for event in session
        if high_floor - 1e-9 <= event.price <= high_ceiling + 1e-9
    )
    high_bonds = sum(event.bonds for event in high)
    high_buys = tuple(event for event in high if event.side == "buy")
    high_buy_bonds = sum(event.bonds for event in high_buys)
    total_bonds = sum(event.bonds for event in session)
    high_share = high_bonds / total_bonds if total_bonds > 0 else 0.0
    span_seconds = (
        max(0.0, (high[-1].market_ts_ms - high[0].market_ts_ms) / 1_000)
        if high else 0.0
    )
    bucket_ms = max(1, policy.session_resilient_bucket_seconds * 1_000)
    bucket_count = len({
        event.market_ts_ms // bucket_ms for event in high
    })
    near_support = sum(
        bonds for price, bonds in tick.bids
        if entry_price - policy.session_resilient_near_support_distance - 1e-9
            <= price <= entry_price + 1e-9
    )
    quality = (
        0.15 * _scaled(
            high_bonds, policy.session_resilient_high_trade_bond_scale,
        )
        + 0.10 * _scaled(
            high_buy_bonds,
            policy.session_resilient_high_buy_bond_scale,
        )
        + 0.10 * _scaled(
            len(high), policy.session_resilient_high_trade_event_scale,
        )
        + 0.20 * _scaled(
            span_seconds, policy.session_resilient_span_scale_seconds,
        )
        + 0.20 * _scaled(
            bucket_count, policy.session_resilient_bucket_scale,
        )
        + 0.10 * _scaled(
            high_share, policy.session_resilient_high_trade_share_scale,
        )
        + 0.15 * _scaled(
            near_support,
            engine.parameters.large_wall_multiple
                * engine.parameters.order_quantity_bonds,
        )
    )
    entry_reach = tuple(
        event for event in session
        if event.side == "sell"
        and abs(event.price - entry_price)
            <= parameters.session_entry_reach_price_band + 1e-9
    )
    entry_reach_bonds = sum(event.bonds for event in entry_reach)
    return SessionEvidenceV03(
        high_trade_floor_price=high_floor,
        high_trade_ceiling_price=high_ceiling,
        high_trade_bonds=high_bonds,
        high_trade_events=len(high),
        high_trade_transactions=sum(
            event.transactions for event in high
        ),
        high_buy_bonds=high_buy_bonds,
        high_buy_events=len(high_buys),
        total_session_trade_bonds=total_bonds,
        high_trade_share=high_share,
        high_trade_span_seconds=span_seconds,
        high_trade_bucket_count=bucket_count,
        near_support_bonds=near_support,
        resilience_quality=quality,
        entry_reach_sell_bonds=entry_reach_bonds,
        entry_reach_sell_events=len(entry_reach),
        entry_reach_quality=_scaled(
            entry_reach_bonds,
            parameters.session_entry_reach_bond_scale,
        ),
    )


def _session_resilience_is_eligible(
    *, engine: MakerPaperEngine, order: MakerOrder,
    evidence: SessionEvidenceV03,
) -> bool:
    policy = engine.priority_policy
    assessment = engine.last_market_assessment
    return (
        policy.enable_session_resilient_ordinary_entry
        and (assessment is None or assessment.state != "falling")
        and order.limit_price > 0
        and evidence.near_support_bonds + 1e-9
            >= policy.session_resilient_minimum_near_support_bonds
        and evidence.high_trade_events
            >= policy.session_resilient_minimum_high_trade_events
        and evidence.resilience_quality + 1e-9
            >= policy.session_resilient_minimum_composite_quality
    )


def score_buy_intent_v03(
    *, bond_code: str, engine: MakerPaperEngine, account: MakerAccount,
    order: MakerOrder, tick: ReplayTick, parameters: AllocationParametersV03,
    active_fill: bool, shared_capacity_bonds: float = SHARED_THOUSAND_BONDS,
) -> AllocationScoreV03:
    """Extend v0.2 without allowing resilience to erase downside risk."""

    base = score_buy_intent_v02(
        bond_code=bond_code,
        engine=engine,
        account=account,
        order=order,
        tick=tick,
        parameters=parameters,
        active_fill=active_fill,
        shared_capacity_bonds=shared_capacity_bonds,
    )
    evidence = session_evidence_v03(
        engine=engine,
        tick=tick,
        entry_price=order.limit_price,
        parameters=parameters,
    )
    eligible = _session_resilience_is_eligible(
        engine=engine, order=order, evidence=evidence,
    )
    if order.kind == SESSION_RESILIENT_ORDER_KIND:
        # The engine already performed the same causal admission test when it
        # created this order.  Preserve that identity through a later book
        # frame even if the raw composite sits on a floating-point boundary.
        eligible = True

    entry_bonus = (
        parameters.session_entry_probability_weight
        * evidence.entry_reach_quality
        if eligible and not active_fill else 0.0
    )
    # Persistent high-side acceptance matters only if the passive low bid has
    # some causal evidence of being reachable.  This coupling prevents a huge
    # but remote historical spread from stealing the shared slot solely on
    # exit evidence.  The square root lets one 1,000-bond near-entry sell be
    # meaningful without treating it as full certainty.
    exit_actionability = math.sqrt(evidence.entry_reach_quality)
    exit_bonus = (
        parameters.session_exit_probability_weight
        * evidence.resilience_quality
        * exit_actionability
        if eligible else 0.0
    )
    entry_probability = min(
        parameters.maximum_entry_probability,
        base.entry_probability_proxy + entry_bonus,
    )
    exit_probability = min(
        parameters.maximum_exit_probability,
        base.exit_probability_proxy + exit_bonus,
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
        base.gross_edge_per_bond
        * entry_probability
        * exit_probability
        * terminal_time_factor
    )
    downside_penalty = (
        base.downside_distance_per_bond * base.downside_risk_fraction
    )
    terminal_penalty = (
        downside_penalty
        * parameters.terminal_lock_penalty_weight
        * (1.0 - terminal_time_factor)
    )
    expected_downside = (
        downside_penalty
        + base.active_adverse_selection_penalty_per_bond
        + terminal_penalty
    )
    raw_value = order.remaining * (expected_gain - expected_downside)
    capital_time_factor = min(
        1.0,
        parameters.capital_time_reference_seconds
            / max(expected_lock_seconds, 1.0),
    )
    capital_time_score = raw_value * capital_time_factor

    values = asdict(base)
    values.update({
        "entry_probability_proxy": entry_probability,
        "exit_probability_proxy": exit_probability,
        "expected_lock_seconds": expected_lock_seconds,
        "remaining_session_seconds": remaining_session,
        "terminal_time_factor": terminal_time_factor,
        "expected_gain_per_bond": expected_gain,
        "expected_downside_per_bond": expected_downside,
        "raw_expected_value_cny": raw_value,
        "capital_time_score_cny": capital_time_score,
    })
    return AllocationScoreV03(
        **values,
        session_resilience_eligible=eligible,
        high_trade_floor_price=evidence.high_trade_floor_price,
        high_trade_ceiling_price=evidence.high_trade_ceiling_price,
        session_high_trade_bonds=evidence.high_trade_bonds,
        session_high_trade_events=evidence.high_trade_events,
        session_high_trade_transactions=(
            evidence.high_trade_transactions
        ),
        session_high_buy_bonds=evidence.high_buy_bonds,
        session_high_buy_events=evidence.high_buy_events,
        total_session_trade_bonds=evidence.total_session_trade_bonds,
        session_high_trade_share=evidence.high_trade_share,
        session_high_trade_span_seconds=evidence.high_trade_span_seconds,
        session_high_trade_bucket_count=evidence.high_trade_bucket_count,
        session_near_support_bonds=evidence.near_support_bonds,
        session_resilience_quality=evidence.resilience_quality,
        session_entry_reach_sell_bonds=(
            evidence.entry_reach_sell_bonds
        ),
        session_entry_reach_sell_events=(
            evidence.entry_reach_sell_events
        ),
        session_entry_reach_quality=evidence.entry_reach_quality,
        entry_probability_without_session=base.entry_probability_proxy,
        exit_probability_without_session=base.exit_probability_proxy,
        session_exit_actionability_factor=exit_actionability,
        session_entry_probability_bonus=(
            entry_probability - base.entry_probability_proxy
        ),
        session_exit_probability_bonus=(
            exit_probability - base.exit_probability_proxy
        ),
    )


class SharedThousandV03Allocator(SharedThousandV02Allocator):
    """V0.3 ranks admitted resilience candidates against cash and the pair."""

    def __init__(
        self, engines: dict[str, MakerPaperEngine], *,
        parameters: AllocationParametersV03 | None = None,
        initial_cash_cny: float | None = None,
        capital_ready_ts_ms: int | None = None,
        shared_capacity_bonds: float = SHARED_THOUSAND_BONDS,
    ) -> None:
        super().__init__(
            engines,
            parameters=parameters or AllocationParametersV03(),
            initial_cash_cny=initial_cash_cny,
            capital_ready_ts_ms=capital_ready_ts_ms,
            shared_capacity_bonds=shared_capacity_bonds,
        )
        self.parameters: AllocationParametersV03
        self.session_resilient_scores = 0
        self.session_resilient_positive_scores = 0
        self.session_resilient_selections = 0
        self.session_resilient_score_audit: list[dict[str, Any]] = []
        self._session_resilient_score_keys: set[
            tuple[str, int, int, bool]
        ] = set()

    def _score(
        self, code: str, order: MakerOrder, *, active_fill: bool,
    ) -> AllocationScoreV03 | None:
        engine = self.engines[code]
        account = _only_account(engine)
        tick = self.last_bond_ticks.get(code)
        if account is None or tick is None:
            return None
        if order.remaining * order.limit_price > self.shared_cash_cny + 1e-9:
            return None
        score = score_buy_intent_v03(
            bond_code=code,
            engine=engine,
            account=account,
            order=order,
            tick=tick,
            parameters=self.parameters,
            active_fill=active_fill,
            shared_capacity_bonds=self.shared_capacity_bonds,
        )
        if order.kind == SESSION_RESILIENT_ORDER_KIND:
            audit_key = (
                code, tick.market_ts_ms, order.db_id, active_fill,
            )
            if audit_key not in self._session_resilient_score_keys:
                self._session_resilient_score_keys.add(audit_key)
                self.session_resilient_scores += 1
                if (
                    score.score_cny
                    > self.parameters.minimum_actionable_score_cny
                ):
                    self.session_resilient_positive_scores += 1
                if (
                    len(self.session_resilient_score_audit)
                    < self.parameters.challenge_audit_limit
                ):
                    self.session_resilient_score_audit.append({
                        "market_ts_ms": tick.market_ts_ms,
                        "active_fill": active_fill,
                        "score": score.public(),
                    })
        if not active_fill:
            self.shadow_scores[code] = (tick.market_ts_ms, score)
        return score

    def _record_selection(
        self, tick: ReplayTick, selected: str | None, reason: str,
        scores: dict[str, AllocationScoreV03],
    ) -> None:
        if (
            selected is not None
            and selected != self.selected_code
            and selected in scores
            and scores[selected].order_kind == SESSION_RESILIENT_ORDER_KIND
        ):
            self.session_resilient_selections += 1
        super()._record_selection(tick, selected, reason, scores)

    def research_metrics(self) -> dict[str, Any]:
        inherited = super().research_metrics()
        metrics = inherited.pop("allocation_v02_metrics")
        metrics.update({
            "session_resilient_scores": self.session_resilient_scores,
            "session_resilient_positive_scores": (
                self.session_resilient_positive_scores
            ),
            "session_resilient_selections": (
                self.session_resilient_selections
            ),
        })
        return {
            "allocation_v03_metrics": metrics,
            "session_resilient_score_audit": (
                self.session_resilient_score_audit
            ),
            **inherited,
        }


def replay_shared_thousand_v03_day(
    config: AppConfig, *, market_date: str,
    bond_codes: tuple[str, ...] = DEFAULT_BOND_CODES,
    parameters: AllocationParametersV03 | None = None,
    initial_cash_cny: float | None = None,
    capital_ready_ts_ms: int | None = None,
) -> dict[str, Any]:
    return replay_one_hand_day(
        config,
        market_date=market_date,
        bond_codes=bond_codes,
        parameters=parameters or AllocationParametersV03(),
        initial_cash_cny=initial_cash_cny,
        capital_ready_ts_ms=capital_ready_ts_ms,
        priority_policy=SHARED_THOUSAND_POLICY_V03_CANDIDATE,
        shared_capacity_bonds=SHARED_THOUSAND_BONDS,
        allocator_class=SharedThousandV03Allocator,
    )


def run_shared_thousand_v03_matrix(
    config: AppConfig, *, dates: tuple[str, ...],
    bond_codes: tuple[str, str] = DEFAULT_BOND_CODES,
    parameters: AllocationParametersV03 | None = None,
) -> dict[str, Any]:
    chosen_parameters = parameters or AllocationParametersV03()
    result = run_one_hand_matrix(
        config,
        dates=dates,
        bond_codes=bond_codes,
        parameters=chosen_parameters,
        priority_policy=SHARED_THOUSAND_POLICY_V03_CANDIDATE,
        shared_capacity_bonds=SHARED_THOUSAND_BONDS,
        allocator_class=SharedThousandV03Allocator,
    )
    metrics = [
        cell["shared"].get("allocation_v03_metrics", {})
        for cell in result["cells"]
    ]
    reason_counts: Counter[str] = Counter()
    for metric in metrics:
        reason_counts.update(metric.get("decision_reason_counts", {}))
    result["totals"]["allocation_v03_metrics"] = {
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
        "session_resilient_scores": sum(
            metric.get("session_resilient_scores", 0)
            for metric in metrics
        ),
        "session_resilient_positive_scores": sum(
            metric.get("session_resilient_positive_scores", 0)
            for metric in metrics
        ),
        "session_resilient_selections": sum(
            metric.get("session_resilient_selections", 0)
            for metric in metrics
        ),
    }
    result["selection_contract"] = {
        "cash_is_an_explicit_third_candidate": True,
        "session_resilience_admits_consideration_not_automatic_selection": True,
        "same_day_evidence_is_causal_and_resets_daily": True,
        "persistent_high_side_trades_are_composite_evidence": True,
        "current_support_remains_a_continuous_risk_input": True,
        "session_resilience_does_not_reduce_downside_penalty": True,
        "wide_spread_alone_never_creates_the_candidate": True,
        "parameters_are_exploratory_not_user_confirmed_permanent_rules": True,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only causal replay for shared 1,000-bond maker v0.3."
        ),
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument(
        "--codes", nargs=2, default=list(DEFAULT_BOND_CODES),
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    result = run_shared_thousand_v03_matrix(
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
