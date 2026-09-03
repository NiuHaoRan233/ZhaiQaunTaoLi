from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import AppConfig, load_config
from .maker import ReplayTick
from .maker_paper import (
    MakerAccount,
    MakerOrder,
    MakerPaperEngine,
    SHARED_THOUSAND_POLICY_V013_CANDIDATE,
)
from .one_hand_maker_research import (
    DEFAULT_BOND_CODES,
    SHARED_THOUSAND_BONDS,
    replay_one_hand_day,
    run_one_hand_matrix,
)
from .shared_thousand_maker_v03_research import (
    AllocationParametersV03,
    AllocationScoreV03,
    SharedThousandV03Allocator,
    score_buy_intent_v03,
)


MODEL_ID = SHARED_THOUSAND_POLICY_V013_CANDIDATE.model_id
PARENT_MODEL_ID = SHARED_THOUSAND_POLICY_V013_CANDIDATE.parent_model_id
DEEP_DISCOUNT_SWEEP_KIND = "deep_discount_sweep"


def _post_sweep_exit_price_v013(
    *, engine: MakerPaperEngine, order: MakerOrder, tick: ReplayTick,
    fill_quantity: float,
) -> float | None:
    """Return the causal exit ceiling after a full best-offer sweep.

    The offer being purchased cannot also remain the prospective exit offer.
    This correction is deliberately limited to the parent's evidence-specific
    deep-discount sweep.  Other active and passive intents keep v0.12 scoring.
    """

    if (
        order.kind != DEEP_DISCOUNT_SWEEP_KIND
        or tick.ask1 <= 0
        or abs(order.limit_price - tick.ask1) > 1e-9
        or fill_quantity <= 0
    ):
        return None
    consumed_offer_bonds = sum(
        bonds for price, bonds in tick.asks
        if bonds > 0 and price <= order.limit_price + 1e-9
    )
    if fill_quantity + 1e-9 < consumed_offer_bonds:
        return None
    next_ask = next(
        (
            price for price, bonds in tick.asks
            if bonds > 0 and price > order.limit_price + 1e-9
        ),
        None,
    )
    if next_ask is None:
        return None
    visible_exit = max(
        order.limit_price,
        next_ask - engine.parameters.price_tick,
    )
    conservative_caps = [visible_exit]
    if order.target_price is not None and order.target_price > order.limit_price:
        conservative_caps.append(order.target_price)
    anchor = engine.analyzer.last_anchor
    if anchor is not None and anchor.exit_price > order.limit_price:
        conservative_caps.append(anchor.exit_price)
    return max(order.limit_price, min(conservative_caps))


def score_buy_intent_v013(
    *, bond_code: str, engine: MakerPaperEngine, account: MakerAccount,
    order: MakerOrder, tick: ReplayTick, parameters: AllocationParametersV03,
    active_fill: bool, fill_quantity: float | None = None,
    shared_capacity_bonds: float = SHARED_THOUSAND_BONDS,
) -> AllocationScoreV03:
    """Apply v0.12 scoring, then repair a fully consumed deep offer."""

    score = score_buy_intent_v03(
        bond_code=bond_code,
        engine=engine,
        account=account,
        order=order,
        tick=tick,
        parameters=parameters,
        active_fill=active_fill,
        shared_capacity_bonds=shared_capacity_bonds,
    )
    return _adjust_post_sweep_score_v013(
        score=score,
        engine=engine,
        order=order,
        tick=tick,
        parameters=parameters,
        active_fill=active_fill,
        fill_quantity=fill_quantity,
    )


def _adjust_post_sweep_score_v013(
    *, score: AllocationScoreV03, engine: MakerPaperEngine,
    order: MakerOrder, tick: ReplayTick, parameters: AllocationParametersV03,
    active_fill: bool, fill_quantity: float | None,
) -> AllocationScoreV03:
    """Adjust an already calculated v0.12 score without losing its audits."""

    if not active_fill or fill_quantity is None:
        return score
    exit_price = _post_sweep_exit_price_v013(
        engine=engine,
        order=order,
        tick=tick,
        fill_quantity=fill_quantity,
    )
    if exit_price is None:
        return score
    gross_edge = max(0.0, exit_price - order.limit_price)
    if gross_edge <= score.gross_edge_per_bond + 1e-9:
        return score
    expected_gain = (
        gross_edge
        * score.entry_probability_proxy
        * score.exit_probability_proxy
        * score.terminal_time_factor
    )
    raw_value = order.remaining * (
        expected_gain - score.expected_downside_per_bond
    )
    capital_time_factor = min(
        1.0,
        parameters.capital_time_reference_seconds
        / max(score.expected_lock_seconds, 1.0),
    )
    return replace(
        score,
        exit_price=exit_price,
        gross_edge_per_bond=gross_edge,
        expected_gain_per_bond=expected_gain,
        raw_expected_value_cny=raw_value,
        capital_time_score_cny=raw_value * capital_time_factor,
    )


class SharedThousandV013Allocator(SharedThousandV03Allocator):
    """V0.12 allocation with causal post-sweep offer valuation."""

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
        self._active_fill_quantities: dict[tuple[str, int], float] = {}
        self._post_sweep_score_keys: set[tuple[str, int, int]] = set()
        self._positive_post_sweep_score_keys: set[tuple[str, int, int]] = set()
        self._accepted_post_sweep_fill_keys: set[tuple[str, int, int]] = set()
        self.post_sweep_score_audit: list[dict[str, Any]] = []
        self.post_sweep_accepted_bonds = 0.0

    def _score(
        self, code: str, order: MakerOrder, *, active_fill: bool,
    ) -> AllocationScoreV03 | None:
        engine = self.engines[code]
        tick = self.last_bond_ticks.get(code)
        parent_score = super()._score(code, order, active_fill=active_fill)
        if parent_score is None or tick is None:
            return None
        fill_quantity = self._active_fill_quantities.get((code, order.db_id))
        score = _adjust_post_sweep_score_v013(
            score=parent_score,
            engine=engine,
            order=order,
            tick=tick,
            parameters=self.parameters,
            active_fill=active_fill,
            fill_quantity=fill_quantity,
        )
        if not active_fill:
            return score
        adjusted = (
            order.kind == DEEP_DISCOUNT_SWEEP_KIND
            and score.gross_edge_per_bond
                > parent_score.gross_edge_per_bond + 1e-9
        )
        if adjusted:
            key = (code, tick.market_ts_ms, order.db_id)
            if key not in self._post_sweep_score_keys:
                self._post_sweep_score_keys.add(key)
                if score.score_cny > self.parameters.minimum_actionable_score_cny:
                    self._positive_post_sweep_score_keys.add(key)
                if len(self.post_sweep_score_audit) < self.parameters.challenge_audit_limit:
                    self.post_sweep_score_audit.append({
                        "bond_code": code,
                        "market_ts_ms": tick.market_ts_ms,
                        "market_time": tick.market_time,
                        "order_id": order.db_id,
                        "fill_quantity_bonds": fill_quantity,
                        "consumed_ask_price": tick.ask1,
                        "consumed_ask_bonds": tick.ask1_bonds,
                        "post_sweep_exit_price": score.exit_price,
                        "adjusted_score": score.public(),
                    })
        return score

    def _allow_buy_fill(
        self, code: str, account: MakerAccount, tick: ReplayTick,
        order: MakerOrder, quantity: float, kind: str, reason: str,
    ) -> bool:
        order_key = (code, order.db_id)
        score_key = (code, tick.market_ts_ms, order.db_id)
        self._active_fill_quantities[order_key] = quantity
        try:
            allowed = super()._allow_buy_fill(
                code, account, tick, order, quantity, kind, reason,
            )
        finally:
            self._active_fill_quantities.pop(order_key, None)
        if (
            allowed
            and score_key in self._post_sweep_score_keys
            and score_key not in self._accepted_post_sweep_fill_keys
        ):
            self._accepted_post_sweep_fill_keys.add(score_key)
            self.post_sweep_accepted_bonds += quantity
        return allowed

    def research_metrics(self) -> dict[str, Any]:
        inherited = super().research_metrics()
        metrics = inherited.pop("allocation_v03_metrics")
        metrics.update({
            "post_sweep_scores": len(self._post_sweep_score_keys),
            "post_sweep_positive_scores": len(
                self._positive_post_sweep_score_keys,
            ),
            "post_sweep_accepted_fills": len(
                self._accepted_post_sweep_fill_keys,
            ),
            "post_sweep_accepted_bonds": round(
                self.post_sweep_accepted_bonds, 6,
            ),
        })
        return {
            "allocation_v013_metrics": metrics,
            "post_sweep_score_audit": self.post_sweep_score_audit,
            **inherited,
        }


def replay_shared_thousand_v013_day(
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
        priority_policy=SHARED_THOUSAND_POLICY_V013_CANDIDATE,
        shared_capacity_bonds=SHARED_THOUSAND_BONDS,
        allocator_class=SharedThousandV013Allocator,
    )


def run_shared_thousand_v013_matrix(
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
        priority_policy=SHARED_THOUSAND_POLICY_V013_CANDIDATE,
        shared_capacity_bonds=SHARED_THOUSAND_BONDS,
        allocator_class=SharedThousandV013Allocator,
    )
    metrics = [
        cell["shared"].get("allocation_v013_metrics", {})
        for cell in result["cells"]
    ]
    reason_counts: Counter[str] = Counter()
    for metric in metrics:
        reason_counts.update(metric.get("decision_reason_counts", {}))
    result["totals"]["allocation_v013_metrics"] = {
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
            metric.get("session_resilient_scores", 0) for metric in metrics
        ),
        "session_resilient_positive_scores": sum(
            metric.get("session_resilient_positive_scores", 0)
            for metric in metrics
        ),
        "session_resilient_selections": sum(
            metric.get("session_resilient_selections", 0)
            for metric in metrics
        ),
        "post_sweep_scores": sum(
            metric.get("post_sweep_scores", 0) for metric in metrics
        ),
        "post_sweep_positive_scores": sum(
            metric.get("post_sweep_positive_scores", 0)
            for metric in metrics
        ),
        "post_sweep_accepted_fills": sum(
            metric.get("post_sweep_accepted_fills", 0)
            for metric in metrics
        ),
        "post_sweep_accepted_bonds": round(sum(
            metric.get("post_sweep_accepted_bonds", 0.0)
            for metric in metrics
        ), 6),
    }
    result["selection_contract"] = {
        "v012_entry_exit_and_allocation_rules_are_preserved": True,
        "cash_remains_an_explicit_third_candidate": True,
        "only_parent_legal_deep_discount_sweeps_are_adjusted": True,
        "the_consumed_offer_is_removed_before_exit_valuation": True,
        "partial_offer_consumption_does_not_use_the_next_ask": True,
        "missing_next_ask_does_not_create_phantom_exit_value": True,
        "tail_sweeps_and_passive_orders_keep_v012_scoring": True,
        "post_sweep_value_still_competes_with_risk_and_cash": True,
        "parameters_are_exploratory_not_user_confirmed_permanent_rules": True,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only causal replay for shared 1,000-bond maker v0.13.",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument("--codes", nargs=2, default=list(DEFAULT_BOND_CODES))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run_shared_thousand_v013_matrix(
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
