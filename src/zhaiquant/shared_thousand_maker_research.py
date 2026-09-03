from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import AppConfig, load_config
from .maker_paper import SHARED_THOUSAND_POLICY_V01_CANDIDATE
from .one_hand_maker_research import (
    AllocationParameters,
    DEFAULT_BOND_CODES,
    SHARED_THOUSAND_BONDS,
    replay_one_hand_day,
    run_one_hand_matrix,
)


MODEL_ID = SHARED_THOUSAND_POLICY_V01_CANDIDATE.model_id
PARENT_MODEL_ID = SHARED_THOUSAND_POLICY_V01_CANDIDATE.parent_model_id
DEFAULT_SWITCH_MINIMUM_CNY = 100.0
DEFAULT_TIE_TOLERANCE_CNY = 10.0


def replay_shared_thousand_day(
    config: AppConfig, *, market_date: str,
    bond_codes: tuple[str, ...] = DEFAULT_BOND_CODES,
    parameters: AllocationParameters | None = None,
    initial_cash_cny: float | None = None,
    capital_ready_ts_ms: int | None = None,
) -> dict:
    return replay_one_hand_day(
        config,
        market_date=market_date,
        bond_codes=bond_codes,
        parameters=parameters,
        initial_cash_cny=initial_cash_cny,
        capital_ready_ts_ms=capital_ready_ts_ms,
        priority_policy=SHARED_THOUSAND_POLICY_V01_CANDIDATE,
        shared_capacity_bonds=SHARED_THOUSAND_BONDS,
    )


def run_shared_thousand_matrix(
    config: AppConfig, *, dates: tuple[str, ...],
    bond_codes: tuple[str, str] = DEFAULT_BOND_CODES,
    parameters: AllocationParameters | None = None,
) -> dict:
    return run_one_hand_matrix(
        config,
        dates=dates,
        bond_codes=bond_codes,
        parameters=parameters,
        priority_policy=SHARED_THOUSAND_POLICY_V01_CANDIDATE,
        shared_capacity_bonds=SHARED_THOUSAND_BONDS,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only causal replay for the zero-base shared 1,000-bond "
            "maker candidate."
        ),
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument(
        "--codes", nargs=2, default=list(DEFAULT_BOND_CODES),
    )
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--switch-minimum-cny",
        type=float,
        default=DEFAULT_SWITCH_MINIMUM_CNY,
    )
    parser.add_argument("--switch-relative-advantage", type=float, default=0.30)
    parser.add_argument("--selection-dwell-seconds", type=int, default=60)
    parser.add_argument(
        "--tie-tolerance-cny",
        type=float,
        default=DEFAULT_TIE_TOLERANCE_CNY,
    )
    args = parser.parse_args()

    parameters = AllocationParameters(
        switch_minimum_score_advantage_cny=args.switch_minimum_cny,
        switch_relative_score_advantage=args.switch_relative_advantage,
        minimum_selection_dwell_seconds=args.selection_dwell_seconds,
        unselected_tie_tolerance_cny=args.tie_tolerance_cny,
    )
    result = run_shared_thousand_matrix(
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
