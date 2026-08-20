from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Callable

from zhaiquant.config import load_config
from zhaiquant.maker_paper import (
    PRIORITY_POLICY_V11,
    PRIORITY_POLICY_V129_CANDIDATE,
    PRIORITY_POLICY_V130_CANDIDATE,
    PRIORITY_POLICY_V131_CANDIDATE,
    PRIORITY_POLICY_V132_CANDIDATE,
    PRIORITY_POLICY_V133_CANDIDATE,
    PRIORITY_POLICY_V134_CANDIDATE,
    PRIORITY_POLICY_V135_CANDIDATE,
    PRIORITY_POLICY_V136_CANDIDATE,
    PRIORITY_POLICY_V137_CANDIDATE,
    PRIORITY_POLICY_V138_CANDIDATE,
    PRIORITY_POLICY_V139_CANDIDATE,
    PRIORITY_POLICY_V140_CANDIDATE,
    PRIORITY_POLICY_V141_CANDIDATE,
)
from zhaiquant.opportunity_audit import (
    build_branch_opportunity_diagnostics,
    compare_model_capture,
    load_opportunity_report,
    replay_registered_models_readonly,
    write_branch_opportunity_diagnostics,
    write_model_opportunity_audit,
)


POLICIES = {
    PRIORITY_POLICY_V11.model_id: PRIORITY_POLICY_V11,
    PRIORITY_POLICY_V129_CANDIDATE.model_id: PRIORITY_POLICY_V129_CANDIDATE,
    PRIORITY_POLICY_V130_CANDIDATE.model_id: PRIORITY_POLICY_V130_CANDIDATE,
    PRIORITY_POLICY_V131_CANDIDATE.model_id: PRIORITY_POLICY_V131_CANDIDATE,
    PRIORITY_POLICY_V132_CANDIDATE.model_id: PRIORITY_POLICY_V132_CANDIDATE,
    PRIORITY_POLICY_V133_CANDIDATE.model_id: PRIORITY_POLICY_V133_CANDIDATE,
    PRIORITY_POLICY_V134_CANDIDATE.model_id: PRIORITY_POLICY_V134_CANDIDATE,
    PRIORITY_POLICY_V135_CANDIDATE.model_id: PRIORITY_POLICY_V135_CANDIDATE,
    PRIORITY_POLICY_V136_CANDIDATE.model_id: PRIORITY_POLICY_V136_CANDIDATE,
    PRIORITY_POLICY_V137_CANDIDATE.model_id: PRIORITY_POLICY_V137_CANDIDATE,
    PRIORITY_POLICY_V138_CANDIDATE.model_id: PRIORITY_POLICY_V138_CANDIDATE,
    PRIORITY_POLICY_V139_CANDIDATE.model_id: PRIORITY_POLICY_V139_CANDIDATE,
    PRIORITY_POLICY_V140_CANDIDATE.model_id: PRIORITY_POLICY_V140_CANDIDATE,
    PRIORITY_POLICY_V141_CANDIDATE.model_id: PRIORITY_POLICY_V141_CANDIDATE,
}

MINIMUM_FULL_DAY_DISTINCT_TICKS = 100
LATEST_ACCEPTABLE_FIRST_REGULAR_TICK = "09:35:00"
EARLIEST_ACCEPTABLE_LAST_REGULAR_TICK = "14:55:00"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _policy_sha256(policy) -> str:
    payload = json.dumps(
        asdict(policy),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def verify_freeze_manifest(
    manifest_path: Path, *, repository_root: Path,
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    candidate_id = manifest["candidate_model_id"]
    candidate = POLICIES.get(candidate_id)
    if candidate is None:
        errors.append(f"unavailable frozen candidate: {candidate_id}")
    else:
        actual_policy_hash = _policy_sha256(candidate)
        expected_policy_hash = manifest["policy_profile_sha256"].upper()
        if actual_policy_hash != expected_policy_hash:
            errors.append(
                "policy profile hash mismatch: "
                f"expected {expected_policy_hash}, got {actual_policy_hash}"
            )

    source_results = []
    for relative, expected in manifest["source_sha256"].items():
        source = repository_root / relative
        if not source.is_file():
            errors.append(f"missing frozen source: {relative}")
            source_results.append({
                "path": relative,
                "expected_sha256": expected.upper(),
                "actual_sha256": None,
                "match": False,
            })
            continue
        actual = _sha256(source)
        match = actual == expected.upper()
        source_results.append({
            "path": relative,
            "expected_sha256": expected.upper(),
            "actual_sha256": actual,
            "match": match,
        })
        if not match:
            errors.append(
                f"source hash mismatch for {relative}: "
                f"expected {expected.upper()}, got {actual}"
            )

    verification = {
        "manifest": str(manifest_path.resolve()),
        "candidate_model_id": candidate_id,
        "production_comparator_model_id": manifest[
            "production_comparator_model_id"
        ],
        "instruments": manifest["instruments"],
        "calibration_dates": manifest["calibration_dates"],
        "first_eligible_sample_out_date": manifest[
            "first_eligible_sample_out_date"
        ],
        "opening_account": manifest["opening_account"],
        "verified": not errors,
        "errors": errors,
        "sources": source_results,
    }
    if errors:
        raise SystemExit(
            "freeze verification failed:\n- " + "\n- ".join(errors)
        )
    return verification


def add_replay_eligibility(
    replay: dict, *, model_id: str, market_date: str, bond_code: str,
    freeze_verification: dict,
) -> None:
    errors: list[str] = []
    priority_accounts = [
        account for account in replay["accounts"]
        if account["fill_mode"] == "priority"
    ]
    if len(priority_accounts) != 1:
        errors.append(
            "expected exactly one priority account, got "
            f"{len(priority_accounts)}"
        )
    else:
        account = priority_accounts[0]
        if account["model_id"] != model_id:
            errors.append(
                f"replay model mismatch: expected {model_id}, "
                f"got {account['model_id']}"
            )
        expected = freeze_verification["opening_account"]
        initial = float(account["initial_inventory"])
        maximum = float(account["maximum_inventory"])
        if initial != float(expected["base_inventory_bonds"]):
            errors.append(
                "opening base inventory mismatch: expected "
                f"{expected['base_inventory_bonds']}, got {initial}"
            )
        capacity = maximum - initial
        if capacity != float(expected["additional_buying_capacity_bonds"]):
            errors.append(
                "additional buying capacity mismatch: expected "
                f"{expected['additional_buying_capacity_bonds']}, got {capacity}"
            )
        if maximum != float(expected["normal_maximum_inventory_bonds"]):
            errors.append(
                "maximum inventory mismatch: expected "
                f"{expected['normal_maximum_inventory_bonds']}, got {maximum}"
            )

    if not replay.get("source_database_opened_readonly"):
        errors.append("source database was not opened read-only")
    if bond_code not in freeze_verification["instruments"]:
        errors.append(f"instrument not present in freeze manifest: {bond_code}")

    requested_date = date.fromisoformat(market_date)
    first_eligible = date.fromisoformat(
        freeze_verification["first_eligible_sample_out_date"]
    )
    calibration_dates = set(freeze_verification["calibration_dates"])
    date_is_new = (
        requested_date >= first_eligible
        and market_date not in calibration_dates
    )
    ineligibility_reasons = list(errors)
    if not date_is_new:
        ineligibility_reasons.append(
            f"{market_date} is a calibration or pre-freeze date"
        )
    freeze_verification["replay_invariants_verified"] = not errors
    freeze_verification["sample_out_date_is_new"] = date_is_new
    freeze_verification["sample_out_eligible"] = (
        freeze_verification["verified"] and not errors and date_is_new
    )
    freeze_verification["sample_out_ineligibility_reasons"] = (
        ineligibility_reasons
    )
    if errors:
        raise SystemExit(
            "sample-out replay invariant verification failed:\n- "
            + "\n- ".join(errors)
        )


def discover_source_market_dates(
    config, *, instruments: list[str], first_eligible_date: str,
) -> dict[str, dict[str, dict]]:
    """Inspect source coverage without opening the live database for writes."""

    source_path = config.storage.database.resolve()
    placeholders = ",".join("?" for _ in instruments)
    source = sqlite3.connect(
        f"file:{source_path.as_posix()}?mode=ro", uri=True,
    )
    source.row_factory = sqlite3.Row
    try:
        rows = source.execute(
            f"""SELECT market_date, code,
                       COUNT(DISTINCT market_ts_ms) AS distinct_ticks,
                       MIN(CASE WHEN market_time >= '09:25:00'
                                 AND market_time <= '11:30:59.999'
                                THEN market_time END) AS first_morning_tick,
                       MAX(CASE WHEN market_time >= '13:00:00'
                                 AND market_time <= '15:30:59.999'
                                THEN market_time END) AS last_afternoon_tick
                  FROM raw_ticks
                 WHERE market_date >= ?
                   AND code IN ({placeholders})
                 GROUP BY market_date, code
                 ORDER BY market_date, code""",
            (first_eligible_date, *instruments),
        ).fetchall()
    finally:
        source.close()

    coverage: dict[str, dict[str, dict]] = {}
    for row in rows:
        coverage.setdefault(row["market_date"], {})[row["code"]] = {
            "distinct_ticks": int(row["distinct_ticks"]),
            "first_morning_tick": row["first_morning_tick"],
            "last_afternoon_tick": row["last_afternoon_tick"],
        }
    return coverage


def select_first_eligible_common_date(
    coverage: dict[str, dict[str, dict]], *, instruments: list[str],
    calibration_dates: list[str], first_eligible_date: str,
) -> dict:
    """Choose the first post-freeze date with credible full-day coverage."""

    rejected: list[dict] = []
    calibration = set(calibration_dates)
    for market_date in sorted(coverage):
        reasons: list[str] = []
        if market_date < first_eligible_date or market_date in calibration:
            reasons.append("calibration_or_pre_freeze_date")
        per_instrument = coverage[market_date]
        for instrument in instruments:
            stats = per_instrument.get(instrument)
            if stats is None:
                reasons.append(f"missing_instrument:{instrument}")
                continue
            if stats["distinct_ticks"] < MINIMUM_FULL_DAY_DISTINCT_TICKS:
                reasons.append(f"insufficient_ticks:{instrument}")
            first_tick = stats["first_morning_tick"]
            if (
                first_tick is None
                or first_tick[:8] > LATEST_ACCEPTABLE_FIRST_REGULAR_TICK
            ):
                reasons.append(f"missing_opening_coverage:{instrument}")
            last_tick = stats["last_afternoon_tick"]
            if (
                last_tick is None
                or last_tick[:8] < EARLIEST_ACCEPTABLE_LAST_REGULAR_TICK
            ):
                reasons.append(f"missing_closing_coverage:{instrument}")
        if reasons:
            rejected.append({
                "market_date": market_date,
                "reasons": reasons,
                "coverage": per_instrument,
            })
            continue
        return {
            "selected_market_date": market_date,
            "selected_coverage": per_instrument,
            "rejected_dates": rejected,
        }
    return {
        "selected_market_date": None,
        "selected_coverage": None,
        "rejected_dates": rejected,
    }


def _priority_account(replay: dict) -> dict:
    accounts = [
        item for item in replay["accounts"]
        if item["fill_mode"] == "priority"
    ]
    if len(accounts) != 1:
        raise SystemExit(
            "expected exactly one priority account in batch replay, got "
            f"{len(accounts)}"
        )
    return accounts[0]


def _replay_summary(replay: dict) -> dict:
    account = _priority_account(replay)
    model_id = account["model_id"]
    model_fills = [
        item for item in replay["fills"] if item["model_id"] == model_id
    ]
    model_orders = [
        item for item in replay["orders"] if item["model_id"] == model_id
    ]
    return {
        "model_id": model_id,
        "trading_pnl": float(account["trading_pnl"]),
        "cash": float(account["cash"]),
        "inventory_bonds": float(account["inventory"]),
        "customer_base_short_bonds": float(
            account["customer_base_short_bonds"]
        ),
        "extra_inventory_bonds": float(account["extra_inventory_bonds"]),
        "fills": len(model_fills),
        "orders": len(model_orders),
    }


def run_first_eligible_batch(
    config, *, freeze_verification: dict,
    coverage: dict[str, dict[str, dict]] | None = None,
    replay_runner: Callable[..., dict] = replay_registered_models_readonly,
) -> dict:
    """Run both frozen models on both bonds for one automatically chosen day."""

    instruments = list(freeze_verification["instruments"])
    configured_mapping = dict(getattr(
        getattr(config, "maker_paper", None),
        "underlying_stock_codes", {},
    ))
    frozen_mapping = dict(
        freeze_verification.get("underlying_stock_codes") or {}
    )
    underlying_stock_codes = frozen_mapping or configured_mapping
    missing_mappings = set(instruments) - set(underlying_stock_codes)
    if missing_mappings:
        raise SystemExit(
            "missing frozen underlying-stock mappings: "
            f"{sorted(missing_mappings)}"
        )
    mapping_mismatches = {
        instrument: {
            "frozen": underlying_stock_codes[instrument],
            "configured": configured_mapping.get(instrument),
        }
        for instrument in instruments
        if configured_mapping.get(instrument)
            != underlying_stock_codes[instrument]
    }
    if mapping_mismatches:
        raise SystemExit(
            "configured underlying-stock mappings differ from freeze: "
            f"{mapping_mismatches}"
        )
    supporting_market_codes = list(dict.fromkeys(
        underlying_stock_codes[instrument] for instrument in instruments
    ))
    required_coverage_codes = list(dict.fromkeys(
        [*instruments, *supporting_market_codes]
    ))
    first_eligible = freeze_verification["first_eligible_sample_out_date"]
    if coverage is None:
        coverage = discover_source_market_dates(
            config,
            instruments=required_coverage_codes,
            first_eligible_date=first_eligible,
        )
    selection = select_first_eligible_common_date(
        coverage,
        instruments=required_coverage_codes,
        calibration_dates=freeze_verification["calibration_dates"],
        first_eligible_date=first_eligible,
    )
    selected_date = selection["selected_market_date"]
    base_payload = {
        "mode": "first_eligible_dual_instrument_dual_model",
        "freeze_verification": freeze_verification,
        "selection": {
            **selection,
            "coverage_gate": {
                "minimum_distinct_ticks": MINIMUM_FULL_DAY_DISTINCT_TICKS,
                "latest_acceptable_first_regular_tick": (
                    LATEST_ACCEPTABLE_FIRST_REGULAR_TICK
                ),
                "earliest_acceptable_last_regular_tick": (
                    EARLIEST_ACCEPTABLE_LAST_REGULAR_TICK
                ),
                "both_instruments_required": True,
                "replay_instruments": instruments,
                "supporting_market_codes": supporting_market_codes,
                "underlying_stock_codes": underlying_stock_codes,
            },
        },
    }
    if selected_date is None:
        return {
            **base_payload,
            "status": "waiting_for_new_data",
            "message": (
                "No post-freeze date has full-day coverage for both frozen "
                "instruments; no replay was run."
            ),
            "matrix": [],
            "instrument_comparisons": [],
        }

    matrix: list[dict] = []
    by_instrument: dict[str, dict[str, dict]] = {
        instrument: {} for instrument in instruments
    }
    for instrument in instruments:
        for model_id in (
            freeze_verification["production_comparator_model_id"],
            freeze_verification["candidate_model_id"],
        ):
            policy = POLICIES[model_id]
            replay = replay_runner(
                config,
                market_date=selected_date,
                bond_code=instrument,
                priority_policy=policy,
            )
            replay_freeze = copy.deepcopy(freeze_verification)
            add_replay_eligibility(
                replay,
                model_id=model_id,
                market_date=selected_date,
                bond_code=instrument,
                freeze_verification=replay_freeze,
            )
            if not replay_freeze["sample_out_eligible"]:
                raise SystemExit(
                    f"batch replay unexpectedly ineligible: {instrument} "
                    f"{model_id}"
                )
            replay["freeze_verification"] = replay_freeze
            summary = _replay_summary(replay)
            matrix.append({
                "bond_code": instrument,
                "underlying_stock_code": underlying_stock_codes[instrument],
                "model_id": model_id,
                "summary": summary,
                "replay": replay,
            })
            by_instrument[instrument][model_id] = summary

    comparisons = []
    baseline_id = freeze_verification["production_comparator_model_id"]
    candidate_id = freeze_verification["candidate_model_id"]
    for instrument in instruments:
        baseline = by_instrument[instrument][baseline_id]
        candidate = by_instrument[instrument][candidate_id]
        comparisons.append({
            "bond_code": instrument,
            "baseline": baseline,
            "candidate": candidate,
            "delta": {
                "trading_pnl": round(
                    candidate["trading_pnl"] - baseline["trading_pnl"], 6,
                ),
                "fills": candidate["fills"] - baseline["fills"],
                "orders": candidate["orders"] - baseline["orders"],
                "ending_inventory_bonds": (
                    candidate["inventory_bonds"]
                    - baseline["inventory_bonds"]
                ),
                "customer_base_short_bonds": (
                    candidate["customer_base_short_bonds"]
                    - baseline["customer_base_short_bonds"]
                ),
            },
        })
    return {
        **base_payload,
        "status": "completed",
        "message": (
            "Four mandatory read-only replays completed for the first "
            "eligible common date."
        ),
        "matrix": matrix,
        "instrument_comparisons": comparisons,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only first-position sample-out replay with optional "
            "TDX opportunity comparison and freeze verification."
        )
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--date")
    parser.add_argument("--code", choices=("132026.SH", "132024.SH"))
    parser.add_argument("--model", choices=tuple(POLICIES))
    parser.add_argument(
        "--batch-first-eligible", action="store_true",
        help=(
            "Automatically select the first full post-freeze date shared by "
            "both bonds and both mapped stocks, then run the frozen "
            "production comparator plus candidate on both bonds."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--opportunities", type=Path)
    parser.add_argument("--diagnostics-output", type=Path)
    parser.add_argument("--freeze-manifest", type=Path)
    args = parser.parse_args()

    if args.batch_first_eligible:
        if args.freeze_manifest is None:
            parser.error("--batch-first-eligible requires --freeze-manifest")
        if any((args.date, args.code, args.model, args.opportunities)):
            parser.error(
                "--batch-first-eligible cannot be combined with --date, "
                "--code, --model, or --opportunities"
            )
        if args.diagnostics_output is not None:
            parser.error(
                "--diagnostics-output is not available in batch mode"
            )
    elif not all((args.date, args.code, args.model)):
        parser.error(
            "single replay mode requires --date, --code, and --model"
        )

    repository_root = Path(__file__).resolve().parents[1]
    freeze_verification = None
    if args.freeze_manifest is not None:
        freeze_verification = verify_freeze_manifest(
            args.freeze_manifest.resolve(), repository_root=repository_root,
        )

    config = load_config(args.config)
    if args.batch_first_eligible:
        batch = run_first_eligible_batch(
            config, freeze_verification=freeze_verification,
        )
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(batch, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        return

    policy = POLICIES[args.model]
    replay = replay_registered_models_readonly(
        config,
        market_date=args.date,
        bond_code=args.code,
        priority_policy=policy,
    )
    if freeze_verification is not None:
        add_replay_eligibility(
            replay,
            model_id=policy.model_id,
            market_date=args.date,
            bond_code=args.code,
            freeze_verification=freeze_verification,
        )
    replay["freeze_verification"] = freeze_verification

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.opportunities is None:
        if args.diagnostics_output is not None:
            parser.error("--diagnostics-output requires --opportunities")
        output.write_text(
            json.dumps(replay, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        return

    opportunity_source = args.opportunities.resolve()
    opportunity_payload, opportunities = load_opportunity_report(
        opportunity_source,
    )
    comparisons = compare_model_capture(opportunities, replay)
    write_model_opportunity_audit(
        output,
        opportunity_source=opportunity_source,
        opportunity_definition=opportunity_payload["definition"],
        replay=replay,
        comparisons=comparisons,
    )
    if args.diagnostics_output is not None:
        diagnostics = build_branch_opportunity_diagnostics(
            opportunities, replay,
        )
        write_branch_opportunity_diagnostics(
            args.diagnostics_output.resolve(),
            opportunity_source=opportunity_source,
            replay=replay,
            diagnostics=diagnostics,
        )


if __name__ == "__main__":
    main()
