from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from .config import AppConfig, load_config
from .maker_paper import SHARED_THOUSAND_POLICY_V012_CANDIDATE
from .one_hand_maker_research import (
    DEFAULT_BOND_CODES,
    SHARED_THOUSAND_BONDS,
    replay_one_hand_day,
    run_one_hand_matrix,
)
from .shared_thousand_maker_v03_research import (
    AllocationParametersV03,
    SharedThousandV03Allocator,
)


MODEL_ID = SHARED_THOUSAND_POLICY_V012_CANDIDATE.model_id
PARENT_MODEL_ID = SHARED_THOUSAND_POLICY_V012_CANDIDATE.parent_model_id


def replay_shared_thousand_v012_day(
    config: AppConfig, *, market_date: str,
    bond_codes: tuple[str, ...] = DEFAULT_BOND_CODES,
    parameters: AllocationParametersV03 | None = None,
    initial_cash_cny: float | None = None,
    capital_ready_ts_ms: int | None = None,
) -> dict[str, Any]:
    """Replay repaired v0.11 with v0.3's complete entry allocation."""

    return replay_one_hand_day(
        config,
        market_date=market_date,
        bond_codes=bond_codes,
        parameters=parameters or AllocationParametersV03(),
        initial_cash_cny=initial_cash_cny,
        capital_ready_ts_ms=capital_ready_ts_ms,
        priority_policy=SHARED_THOUSAND_POLICY_V012_CANDIDATE,
        shared_capacity_bonds=SHARED_THOUSAND_BONDS,
        allocator_class=SharedThousandV03Allocator,
    )


def run_shared_thousand_v012_matrix(
    config: AppConfig, *, dates: tuple[str, ...],
    bond_codes: tuple[str, str] = DEFAULT_BOND_CODES,
    parameters: AllocationParametersV03 | None = None,
) -> dict[str, Any]:
    """Run the causal daily matrix with v0.3 allocation on v0.11 r2."""

    chosen_parameters = parameters or AllocationParametersV03()
    result = run_one_hand_matrix(
        config,
        dates=dates,
        bond_codes=bond_codes,
        parameters=chosen_parameters,
        priority_policy=SHARED_THOUSAND_POLICY_V012_CANDIDATE,
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
        "guarded_exit_repairs_only_an_ordinary_parent_order_gap": True,
        "v012_and_v031_are_expected_to_be_behaviorally_equivalent": True,
        "parameters_are_exploratory_not_user_confirmed_permanent_rules": True,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only causal replay for shared 1,000-bond maker v0.12."
        ),
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument(
        "--codes", nargs=2, default=list(DEFAULT_BOND_CODES),
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    result = run_shared_thousand_v012_matrix(
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
