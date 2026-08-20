from __future__ import annotations

import argparse
import json
from pathlib import Path

from zhaiquant.config import load_config
from zhaiquant.maker_paper import (
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
    PRIORITY_POLICY_V142_CANDIDATE,
    PRIORITY_POLICY_V143_CANDIDATE,
)
from zhaiquant.opportunity_audit import replay_registered_models_readonly


CANDIDATES = {
    "v131": PRIORITY_POLICY_V131_CANDIDATE,
    "v132": PRIORITY_POLICY_V132_CANDIDATE,
    "v133": PRIORITY_POLICY_V133_CANDIDATE,
    "v134": PRIORITY_POLICY_V134_CANDIDATE,
    "v135": PRIORITY_POLICY_V135_CANDIDATE,
    "v136": PRIORITY_POLICY_V136_CANDIDATE,
    "v137": PRIORITY_POLICY_V137_CANDIDATE,
    "v138": PRIORITY_POLICY_V138_CANDIDATE,
    "v139": PRIORITY_POLICY_V139_CANDIDATE,
    "v140": PRIORITY_POLICY_V140_CANDIDATE,
    "v141": PRIORITY_POLICY_V141_CANDIDATE,
    "v142": PRIORITY_POLICY_V142_CANDIDATE,
    "v143": PRIORITY_POLICY_V143_CANDIDATE,
}

NEW_PERMISSION_FILL_REASONS = {
    "v131": {"active_confirmed_falling_near_flat_exit"},
    # v1.32 reuses the ordinary passive-buy fill reason; its report identifies
    # changed fills from the complete parent/candidate path rather than
    # pretending every passive buy came from the new price override.
    "v132": set(),
    "v133": {"active_confirmed_rising_buy_sequence_base_short_stop"},
    "v134": set(),
    "v135": set(),
    "v136": set(),
    "v137": {"active_supported_ask_collapse_entry"},
    "v138": set(),
    "v139": set(),
    "v140": set(),
    "v141": set(),
    "v142": set(),
    "v143": {"active_tail_sweep"},
}

NEW_PERMISSION_ORDER_KINDS = {
    "v134": {"persistent_wall_supported_falling_entry"},
    "v135": {"persistent_wall_supported_falling_entry"},
    "v136": {"persistent_wall_supported_falling_entry"},
    "v137": {"supported_ask_collapse_sweep"},
    "v138": {"high_side_validated_corridor_entry"},
    "v139": {"persistent_two_sided_wall_corridor_entry"},
    "v140": {
        "dynamic_customer_base_replenish",
        "high_ask_cluster_base_preposition",
    },
    "v141": {"dynamic_customer_base_replenish"},
    "v142": set(),
    "v143": {"sweep_tail"},
}


def _priority_account(replay: dict, model_id: str) -> dict:
    return next(
        account for account in replay["accounts"]
        if account["fill_mode"] == "priority"
        and account["model_id"] == model_id
    )


def _priority_fills(replay: dict, model_id: str) -> list[dict]:
    return [
        {
            key: fill[key]
            for key in (
                "market_time", "side", "price", "quantity_bonds",
                "fill_reason", "inventory_after_bonds",
            )
        }
        for fill in replay["fills"]
        if fill["model_id"] == model_id
    ]


def _new_permission_orders(
    replay: dict, model_id: str, order_kinds: set[str],
) -> list[dict]:
    fills_by_order: dict[int, list[dict]] = {}
    for fill in replay["fills"]:
        if fill["model_id"] != model_id:
            continue
        fills_by_order.setdefault(int(fill["order_id"]), []).append({
            key: fill[key]
            for key in (
                "market_time", "side", "price", "quantity_bonds",
                "fill_reason", "inventory_after_bonds",
            )
        })
    return [
        {
            key: order[key]
            for key in (
                "id", "created_market_time", "updated_market_time", "side",
                "kind", "status", "limit_price", "quantity",
                "filled_quantity", "cancel_reason",
            )
        } | {"fills": fills_by_order.get(int(order["id"]), [])}
        for order in replay["orders"]
        if order["model_id"] == model_id
        and order["kind"] in order_kinds
    ]


def _inventory_and_cash_path(account: dict, fills: list[dict]) -> dict:
    inventories = [float(account["initial_inventory"])] + [
        float(fill["inventory_after_bonds"]) for fill in fills
    ]
    cash = float(account["initial_cash"])
    minimum_cash = cash
    for fill in fills:
        value = float(fill["price"]) * float(fill["quantity_bonds"])
        cash += value if fill["side"] == "sell" else -value
        minimum_cash = min(minimum_cash, cash)
    return {
        "minimum_inventory_bonds": min(inventories),
        "maximum_inventory_bonds": max(inventories),
        "minimum_cash_cny": minimum_cash,
        "derived_terminal_cash_cny": cash,
    }


def _customer_base_short_path(
    account: dict, fills: list[dict],
) -> list[dict]:
    base = float(account["initial_inventory"])
    previous_inventory = base
    path = []
    for fill in fills:
        inventory_after = float(fill["inventory_after_bonds"])
        if previous_inventory < base - 1e-9 or inventory_after < base - 1e-9:
            path.append(fill)
        previous_inventory = inventory_after
    return path


def _market_seconds(market_time: str) -> int:
    hours, minutes, seconds = market_time.split(":")
    return int(hours) * 3_600 + int(minutes) * 60 + int(float(seconds))


def _customer_base_short_metrics(
    account: dict, fills: list[dict],
) -> dict:
    base = float(account["initial_inventory"])
    inventory = base
    episode_start = None
    durations = []
    for fill in fills:
        after = float(fill["inventory_after_bonds"])
        second = _market_seconds(fill["market_time"])
        if inventory >= base - 1e-9 and after < base - 1e-9:
            episode_start = second
        elif (
            inventory < base - 1e-9
            and after >= base - 1e-9
            and episode_start is not None
        ):
            durations.append(second - episode_start)
            episode_start = None
        inventory = after
    open_seconds = 0
    if episode_start is not None:
        open_seconds = max(0, 15 * 3_600 + 30 * 60 - episode_start)
        durations.append(open_seconds)
    return {
        "episodes": len(durations),
        "completed_episodes": len(durations) - int(episode_start is not None),
        "total_exposure_seconds": sum(durations),
        "maximum_episode_seconds": max(durations, default=0),
        "open_at_close": episode_start is not None,
        "open_episode_seconds_to_15_30": open_seconds,
    }


def _branch_snapshot(replay: dict, fill_mode: str) -> dict:
    account = next(
        row for row in replay["accounts"] if row["fill_mode"] == fill_mode
    )
    strategy_id = account["strategy_id"]
    orders = [
        {
            key: value
            for key, value in order.items()
            if key not in {"id", "lot_id"}
        }
        for order in replay["orders"]
        if order["strategy_id"] == strategy_id
    ]
    fills = [
        {
            key: value
            for key, value in fill.items()
            if key != "order_id"
        }
        for fill in replay["fills"]
        if fill["strategy_id"] == strategy_id
    ]
    return {"account": account, "orders": orders, "fills": fills}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only calibration matrix for registered priority candidates.",
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--dates", nargs="+", required=True)
    parser.add_argument("--codes", nargs="+", required=True)
    parser.add_argument(
        "--candidate", choices=tuple(CANDIDATES), default="v131",
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    config = load_config(args.config)
    candidate_policy = CANDIDATES[args.candidate]
    parent_policy = (
        PRIORITY_POLICY_V135_CANDIDATE
        if args.candidate == "v136"
        else PRIORITY_POLICY_V134_CANDIDATE
        if args.candidate in {"v135", "v137"}
        else PRIORITY_POLICY_V137_CANDIDATE
        if args.candidate == "v138"
        else PRIORITY_POLICY_V138_CANDIDATE
        if args.candidate == "v139"
        else PRIORITY_POLICY_V137_CANDIDATE
        if args.candidate in {"v140", "v141"}
        else PRIORITY_POLICY_V141_CANDIDATE
        if args.candidate == "v142"
        else PRIORITY_POLICY_V142_CANDIDATE
        if args.candidate == "v143"
        else PRIORITY_POLICY_V133_CANDIDATE
        if args.candidate == "v134"
        else PRIORITY_POLICY_V130_CANDIDATE
    )
    policies = (parent_policy, candidate_policy)
    cells = []
    for market_date in args.dates:
        for bond_code in args.codes:
            variants = {}
            for policy in policies:
                replay = replay_registered_models_readonly(
                    config,
                    market_date=market_date,
                    bond_code=bond_code,
                    priority_policy=policy,
                )
                account = _priority_account(replay, policy.model_id)
                fills = _priority_fills(replay, policy.model_id)
                new_permission_orders = _new_permission_orders(
                    replay, policy.model_id,
                    NEW_PERMISSION_ORDER_KINDS.get(args.candidate, set()),
                )
                new_permission_order_fills = [
                    fill
                    for order in new_permission_orders
                    for fill in order["fills"]
                ]
                path_bounds = _inventory_and_cash_path(account, fills)
                variants[policy.model_id] = {
                    "account": account,
                    "orders": sum(
                        order["model_id"] == policy.model_id
                        for order in replay["orders"]
                    ),
                    "fills": fills,
                    "new_permission_orders": new_permission_orders,
                    "path_bounds": path_bounds,
                    "customer_base_short_path": _customer_base_short_path(
                        account, fills,
                    ),
                    "customer_base_short_metrics": (
                        _customer_base_short_metrics(account, fills)
                    ),
                    "new_permission_fills": (
                        new_permission_order_fills
                        if args.candidate in {
                            "v134", "v135", "v136", "v137", "v138",
                            "v139",
                            "v140", "v141", "v143",
                        }
                        else [
                            fill for fill in fills
                            if fill["fill_reason"]
                                in NEW_PERMISSION_FILL_REASONS[args.candidate]
                        ]
                    ),
                    "queue_snapshot": _branch_snapshot(replay, "queue"),
                    "windfall_snapshot": _branch_snapshot(
                        replay, "windfall",
                    ),
                }
            parent = variants[parent_policy.model_id]
            candidate = variants[candidate_policy.model_id]
            cells.append({
                "market_date": market_date,
                "bond_code": bond_code,
                "parent_model_id": parent_policy.model_id,
                "candidate_model_id": candidate_policy.model_id,
                "parent_trading_pnl": parent["account"]["trading_pnl"],
                "candidate_trading_pnl": candidate["account"]["trading_pnl"],
                "trading_pnl_delta": (
                    candidate["account"]["trading_pnl"]
                    - parent["account"]["trading_pnl"]
                ),
                "parent_terminal_inventory": parent["account"]["inventory"],
                "candidate_terminal_inventory": candidate["account"]["inventory"],
                "parent_customer_base_short_bonds": (
                    parent["account"]["customer_base_short_bonds"]
                ),
                "candidate_customer_base_short_bonds": (
                    candidate["account"]["customer_base_short_bonds"]
                ),
                "parent_path_bounds": parent["path_bounds"],
                "candidate_path_bounds": candidate["path_bounds"],
                "customer_base_short_path_identical": (
                    parent["customer_base_short_path"]
                    == candidate["customer_base_short_path"]
                ),
                "parent_customer_base_short_metrics": (
                    parent["customer_base_short_metrics"]
                ),
                "candidate_customer_base_short_metrics": (
                    candidate["customer_base_short_metrics"]
                ),
                "parent_fill_count": len(parent["fills"]),
                "candidate_fill_count": len(candidate["fills"]),
                "parent_order_count": parent["orders"],
                "candidate_order_count": candidate["orders"],
                "new_permission_fill_count": len(
                    candidate["new_permission_fills"]
                ),
                "new_permission_fills": candidate["new_permission_fills"],
                "new_permission_order_count": len(
                    candidate["new_permission_orders"]
                ),
                "new_permission_orders": candidate["new_permission_orders"],
                "fill_paths_identical": parent["fills"] == candidate["fills"],
                "queue_branch_identical": (
                    parent["queue_snapshot"] == candidate["queue_snapshot"]
                ),
                "windfall_branch_identical": (
                    parent["windfall_snapshot"]
                    == candidate["windfall_snapshot"]
                ),
                "parent_fills": parent["fills"],
                "candidate_fills": candidate["fills"],
            })

    totals = {}
    for code in args.codes:
        selected = [cell for cell in cells if cell["bond_code"] == code]
        totals[code] = {
            "parent_trading_pnl": sum(
                cell["parent_trading_pnl"] for cell in selected
            ),
            "candidate_trading_pnl": sum(
                cell["candidate_trading_pnl"] for cell in selected
            ),
            "trading_pnl_delta": sum(
                cell["trading_pnl_delta"] for cell in selected
            ),
            "changed_cells": sum(
                not cell["fill_paths_identical"] for cell in selected
            ),
            "new_permission_fills": sum(
                cell["new_permission_fill_count"] for cell in selected
            ),
        }
    output = {
        "source_database_opened_readonly": True,
        "temporary_replay_databases": True,
        "same_policy_for_all_codes": True,
        "security_specific_trading_conditions": False,
        "parent_model_id": parent_policy.model_id,
        "candidate_model_id": candidate_policy.model_id,
        "branch_isolation": {
            "queue_all_cells_identical": all(
                cell["queue_branch_identical"] for cell in cells
            ),
            "windfall_all_cells_identical": all(
                cell["windfall_branch_identical"] for cell in cells
            ),
            "verified_cells": len(cells),
        },
        "totals_by_code": totals,
        "cells": cells,
    }
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
