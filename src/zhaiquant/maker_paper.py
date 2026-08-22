from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field, replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from datetime import date, datetime, timezone
from typing import Any

from .config import AppConfig, maker_underlying_stock_code
from .database import SQLiteStore
from .maker import (
    MakerAnalyzer,
    MakerParameters,
    MarketAssessment,
    Opportunity,
    ReplayTick,
    _load_ticks,
)
from .recorder import RecordedTick
from .types import SHANGHAI, Tick


QMT_BONDS_PER_HAND = 10.0


@dataclass(frozen=True)
class MakerPolicyProfile:
    """Immutable decision-policy identity for one execution branch."""

    model_id: str
    model_version: str
    parent_model_id: str | None
    execution_mode: str
    enable_priority_v11_extensions: bool
    # Existing registered models retain their historical 14:56:30 trading
    # cutoff.  New models must opt into the user-confirmed close-long window
    # explicitly so replaying an old model ID never changes its order path.
    latest_entry_time: str = "14:56:30.000"
    exclude_wide_persistent_windfall_reference: bool = False
    enable_downtrend_wide_spread_base_turn: bool = False
    enable_downtrend_turn_while_extra_inventory: bool = False
    confirmed_rise_grace_seconds_override: int | None = None
    confirm_exact_offer_clear_in_possible_rise: bool = False
    require_exact_offer_clear_volume_coverage: bool = False
    minimum_downtrend_turn_edge_override: float | None = None
    minimum_wall_supported_base_high_sell_edge_override: float | None = None
    enable_persistent_bid_downtrend_turn: bool = False
    use_recent_intraday_reference_for_active_entry: bool = False
    require_concentrated_downtrend_bid_support: bool = False
    use_local_reference_after_base_replenishment: bool = False
    enable_falling_profitable_bid_exit: bool = False
    enable_confirmed_falling_near_flat_extra_exit: bool = False
    confirmed_falling_extra_exit_minimum_sell_multiple: float = 5.0
    confirmed_falling_extra_exit_minimum_imbalance_ratio: float = 5.0
    confirmed_falling_extra_exit_minimum_midpoint_drop: float = 0.10
    enable_repeated_two_sided_base_turn: bool = False
    repeated_turn_window_seconds: int = 60
    repeated_turn_latest_low_seconds: int = 30
    minimum_repeated_turn_side_bonds: float = 2_000.0
    minimum_repeated_turn_side_events: int = 2
    minimum_repeated_turn_runs: int = 4
    enable_recent_completed_base_turn_repeat: bool = False
    recent_completed_base_turn_window_seconds: int = 180
    maximum_completed_base_turn_low_drift: float = 0.10
    minimum_completed_base_turn_lower_sell_bonds: float = 1_000.0
    allow_repeated_replenishment_to_downtrend_edge: bool = False
    minimum_falling_profitable_exit_edge: float = 0.05
    minimum_falling_profitable_sell_multiple: float = 4.0
    falling_profitable_reentry_cooldown_seconds: int = 600
    minimum_falling_profitable_reentry_improvement: float = 0.10
    priority_base_turn_stable_context_grace_seconds: int = 0
    retain_priority_base_turn_while_live_corridor: bool = False
    retain_priority_base_turn_on_lower_bid_shift: bool = False
    retain_priority_base_turn_on_recent_sell_corridor: bool = False
    enable_visible_wall_anchored_downtrend_entry: bool = False
    enable_priority_sweep_recovery_target: bool = False
    enable_immediate_visible_cluster_tail_recovery: bool = False
    priority_sweep_recovery_target_seconds: int = 30
    enable_supported_post_replenishment_entry: bool = False
    supported_post_replenishment_entry_seconds: int = 30
    minimum_supported_post_replenishment_gap: float = 0.10
    minimum_supported_post_replenishment_sell_bonds: float = 1_000.0
    enable_priority_book_side_fill_correction: bool = False
    minimum_book_side_distance_advantage: float = 0.05
    prefer_fresh_lower_visible_wall_after_base_replenishment: bool = False
    require_rising_base_short_recent_trade_premium_and_supply: bool = False
    minimum_rising_base_short_reliable_reference_edge: float | None = None
    priority_rising_base_short_after_extra_exit_isolation_seconds: int = 0
    enable_dynamic_medium_base_short_replenishment: bool = False
    enable_confirmed_rising_near_flat_base_short_stop: bool = False
    confirmed_rising_base_short_stop_seconds: int = 0
    enable_confirmed_rising_buy_sequence_base_short_stop: bool = False
    confirmed_rising_buy_sequence_base_short_stop_seconds: int = 0
    enable_profitable_visible_bid_base_replenishment: bool = False
    minimum_profitable_visible_bid_base_replenishment_edge_override: (
        float | None
    ) = None
    enable_continuous_dynamic_base_short_replenishment: bool = False
    dynamic_base_replenishment_maximum_loss: float = 0.015
    enable_post_replenishment_high_ask_cluster_preposition: bool = False
    high_ask_cluster_preposition_seconds: int = 600
    high_ask_cluster_minimum_inside_gap: float = 0.20
    high_ask_cluster_minimum_supply_bonds: float = 5_000.0
    high_ask_cluster_maximum_sale_distance: float = 0.05
    enable_persistent_wall_supported_falling_extra_entry: bool = False
    persistent_wall_supported_entry_minimum_wall_seconds: int = 30
    persistent_wall_supported_entry_high_buy_lookback_seconds: int = 120
    persistent_wall_supported_entry_minimum_high_buy_bonds: float = 1_000.0
    persistent_wall_supported_entry_maximum_wall_premium: float = 0.01
    persistent_wall_supported_entry_minimum_ask_bonds: float = 1_000.0
    persistent_wall_supported_entry_minimum_exit_edge: float = 0.18
    enable_supported_current_midpoint_collapse_extra_entry: bool = False
    supported_midpoint_collapse_minimum_wall_seconds: int = 15
    supported_midpoint_collapse_high_buy_lookback_seconds: int = 120
    supported_midpoint_collapse_minimum_high_buy_bonds: float = 1_000.0
    supported_midpoint_collapse_minimum_reference_dislocation: float = 0.20
    enable_high_side_validated_supported_corridor_entry: bool = False
    supported_corridor_minimum_edge: float = 0.18
    supported_corridor_maximum_edge: float = 0.50
    supported_corridor_minimum_high_buy_bonds: float = 1_000.0
    supported_corridor_minimum_ask_supply_bonds: float = 3_000.0
    supported_corridor_maximum_reference_low_edge: float = 0.18
    supported_corridor_maximum_midpoint_change: float = 0.05
    supported_corridor_maximum_ask_drop: float = 0.05
    supported_corridor_maximum_high_trade_ask_gap: float = 0.05
    enable_persistent_two_sided_wall_corridor_entry: bool = False
    two_sided_wall_corridor_minimum_wall_seconds: int = 60
    two_sided_wall_corridor_minimum_side_bonds: float = 5_000.0
    two_sided_wall_corridor_maximum_wall_premium: float = 0.10
    two_sided_wall_corridor_minimum_ask_supply_bonds: float = 3_000.0
    two_sided_wall_corridor_minimum_edge: float = 0.18
    two_sided_wall_corridor_maximum_edge: float = 0.50
    two_sided_wall_corridor_maximum_reference_low_edge: float = 0.18
    two_sided_wall_corridor_maximum_midpoint_change: float = 0.05
    two_sided_wall_corridor_maximum_ask_drop: float = 0.05
    enable_persistent_wide_spread_buy_first_entry: bool = False
    wide_spread_buy_first_minimum_edge: float = 0.30
    wide_spread_buy_first_maximum_edge: float = 0.50
    wide_spread_buy_first_minimum_book_seconds: int = 60
    wide_spread_buy_first_maximum_book_drift: float = 0.015
    wide_spread_buy_first_high_buy_lookback_seconds: int = 600
    wide_spread_buy_first_minimum_high_buy_bonds: float = 1_000.0
    wide_spread_buy_first_maximum_high_trade_ask_gap: float = 0.015
    wide_spread_buy_first_maximum_midpoint_change: float = 0.05
    wide_spread_buy_first_maximum_ask_drop: float = 0.05
    retain_persistent_wall_supported_falling_extra_entry: bool = False
    persistent_wall_supported_entry_maximum_lifetime_seconds: int = 300
    retain_persistent_wall_supported_entry_across_state_relabels: bool = False
    share_simultaneous_same_price_queue: bool = False
    queue_extra_exit_context_grace_seconds: int = 0
    queue_graced_extra_exit_to_base_sale_window_seconds: int = 0
    queue_replenishment_exact_fill_buffer_bonds: float = 0.0
    queue_cleared_position_one_tick_grace_seconds: int = 0
    queue_cleared_buy_context_grace_seconds: int = 0
    queue_cleared_sell_reprice_grace_seconds: int = 0
    queue_cleared_extra_sell_reprice_grace_seconds: int = 0
    queue_inventory_turn_exact_fill_buffer_bonds: float = 0.0
    allow_fresh_post_replenishment_inventory_turn: bool = False
    waive_inventory_turn_buffer_on_clean_exact_queue_clear: bool = False
    queue_cleared_inventory_turn_corridor_seconds: int = 0
    retain_queue_cleared_inventory_turn_while_live_corridor: bool = False
    fill_queue_cleared_crossed_book_residual: bool = False
    retain_queue_queued_inventory_turn_in_stable: bool = False
    retain_queue_cleared_inventory_turn_buy_on_lower_reprice: bool = False
    retain_clean_cleared_inventory_turn_buy_while_falling: bool = False
    quote_at_second_level_front: bool = False
    dynamically_choose_second_level_front: bool = False
    second_level_front_minimum_top_quantity_multiple: float = 2.0
    second_level_front_minimum_price_improvement: float = 0.02
    second_level_front_minimum_inside_spread: float = 0.18


PRIORITY_POLICY_V11 = MakerPolicyProfile(
    model_id="maker_priority_v1_1",
    model_version="1.1",
    parent_model_id="maker_shared_v1_0",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
)
PRIORITY_POLICY_V12_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_2_candidate",
    model_version="1.2-candidate",
    parent_model_id="maker_priority_v1_1",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
)
PRIORITY_POLICY_V13_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_3_candidate",
    model_version="1.3-candidate",
    parent_model_id="maker_priority_v1_2_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
)
PRIORITY_POLICY_V14_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_4_candidate",
    model_version="1.4-candidate",
    parent_model_id="maker_priority_v1_3_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    enable_persistent_bid_downtrend_turn=True,
)
PRIORITY_POLICY_V15_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_5_candidate",
    model_version="1.5-candidate",
    parent_model_id="maker_priority_v1_4_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
)
PRIORITY_POLICY_V16_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_6_candidate",
    model_version="1.6-candidate",
    parent_model_id="maker_priority_v1_5_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
)
PRIORITY_POLICY_V17_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_7_candidate",
    model_version="1.7-candidate",
    parent_model_id="maker_priority_v1_6_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
    use_local_reference_after_base_replenishment=True,
)
PRIORITY_POLICY_V18_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_8_candidate",
    model_version="1.8-candidate",
    parent_model_id="maker_priority_v1_7_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
    use_local_reference_after_base_replenishment=True,
    enable_falling_profitable_bid_exit=True,
)
PRIORITY_POLICY_V19_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_9_candidate",
    model_version="1.9-candidate",
    parent_model_id="maker_priority_v1_8_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
    use_local_reference_after_base_replenishment=True,
    enable_falling_profitable_bid_exit=True,
    priority_base_turn_stable_context_grace_seconds=15,
)
PRIORITY_POLICY_V110_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_10_candidate",
    model_version="1.10-candidate",
    parent_model_id="maker_priority_v1_9_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
    use_local_reference_after_base_replenishment=True,
    enable_falling_profitable_bid_exit=True,
    priority_base_turn_stable_context_grace_seconds=15,
    enable_repeated_two_sided_base_turn=True,
)
PRIORITY_POLICY_V111_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_11_candidate",
    model_version="1.11-candidate",
    parent_model_id="maker_priority_v1_10_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
    use_local_reference_after_base_replenishment=True,
    enable_falling_profitable_bid_exit=True,
    priority_base_turn_stable_context_grace_seconds=15,
    enable_repeated_two_sided_base_turn=True,
    enable_recent_completed_base_turn_repeat=True,
    allow_repeated_replenishment_to_downtrend_edge=True,
)
PRIORITY_POLICY_V112_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_12_candidate",
    model_version="1.12-candidate",
    parent_model_id="maker_priority_v1_11_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
    use_local_reference_after_base_replenishment=True,
    enable_falling_profitable_bid_exit=True,
    priority_base_turn_stable_context_grace_seconds=15,
    enable_repeated_two_sided_base_turn=True,
    enable_recent_completed_base_turn_repeat=True,
    allow_repeated_replenishment_to_downtrend_edge=True,
    retain_priority_base_turn_while_live_corridor=True,
)
PRIORITY_POLICY_V113_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_13_candidate",
    model_version="1.13-candidate",
    parent_model_id="maker_priority_v1_12_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
    use_local_reference_after_base_replenishment=True,
    enable_falling_profitable_bid_exit=True,
    priority_base_turn_stable_context_grace_seconds=15,
    enable_repeated_two_sided_base_turn=True,
    enable_recent_completed_base_turn_repeat=True,
    allow_repeated_replenishment_to_downtrend_edge=True,
    retain_priority_base_turn_while_live_corridor=True,
    retain_priority_base_turn_on_lower_bid_shift=True,
)
PRIORITY_POLICY_V114_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_14_candidate",
    model_version="1.14-candidate",
    parent_model_id="maker_priority_v1_13_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
    use_local_reference_after_base_replenishment=True,
    enable_falling_profitable_bid_exit=True,
    priority_base_turn_stable_context_grace_seconds=15,
    enable_repeated_two_sided_base_turn=True,
    enable_recent_completed_base_turn_repeat=True,
    allow_repeated_replenishment_to_downtrend_edge=True,
    retain_priority_base_turn_while_live_corridor=True,
    retain_priority_base_turn_on_lower_bid_shift=True,
    enable_visible_wall_anchored_downtrend_entry=True,
)
PRIORITY_POLICY_V115_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_15_candidate",
    model_version="1.15-candidate",
    parent_model_id="maker_priority_v1_14_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
    use_local_reference_after_base_replenishment=True,
    enable_falling_profitable_bid_exit=True,
    priority_base_turn_stable_context_grace_seconds=15,
    enable_repeated_two_sided_base_turn=True,
    enable_recent_completed_base_turn_repeat=True,
    allow_repeated_replenishment_to_downtrend_edge=True,
    retain_priority_base_turn_while_live_corridor=True,
    retain_priority_base_turn_on_lower_bid_shift=True,
    enable_visible_wall_anchored_downtrend_entry=True,
    enable_priority_sweep_recovery_target=True,
)
PRIORITY_POLICY_V116_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_16_candidate",
    model_version="1.16-candidate",
    parent_model_id="maker_priority_v1_15_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
    use_local_reference_after_base_replenishment=True,
    enable_falling_profitable_bid_exit=True,
    priority_base_turn_stable_context_grace_seconds=15,
    enable_repeated_two_sided_base_turn=True,
    enable_recent_completed_base_turn_repeat=True,
    allow_repeated_replenishment_to_downtrend_edge=True,
    retain_priority_base_turn_while_live_corridor=True,
    retain_priority_base_turn_on_lower_bid_shift=True,
    enable_visible_wall_anchored_downtrend_entry=True,
    enable_priority_sweep_recovery_target=True,
    enable_supported_post_replenishment_entry=True,
)
PRIORITY_POLICY_V117_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_17_candidate",
    model_version="1.17-candidate",
    parent_model_id="maker_priority_v1_16_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
    use_local_reference_after_base_replenishment=True,
    enable_falling_profitable_bid_exit=True,
    priority_base_turn_stable_context_grace_seconds=15,
    enable_repeated_two_sided_base_turn=True,
    enable_recent_completed_base_turn_repeat=True,
    allow_repeated_replenishment_to_downtrend_edge=True,
    retain_priority_base_turn_while_live_corridor=True,
    retain_priority_base_turn_on_lower_bid_shift=True,
    enable_visible_wall_anchored_downtrend_entry=True,
    enable_priority_sweep_recovery_target=True,
    enable_supported_post_replenishment_entry=True,
    enable_priority_book_side_fill_correction=True,
)
PRIORITY_POLICY_V118_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_18_candidate",
    model_version="1.18-candidate",
    parent_model_id="maker_priority_v1_17_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
    use_local_reference_after_base_replenishment=True,
    enable_falling_profitable_bid_exit=True,
    priority_base_turn_stable_context_grace_seconds=15,
    enable_repeated_two_sided_base_turn=True,
    enable_recent_completed_base_turn_repeat=True,
    allow_repeated_replenishment_to_downtrend_edge=True,
    retain_priority_base_turn_while_live_corridor=True,
    retain_priority_base_turn_on_lower_bid_shift=True,
    enable_visible_wall_anchored_downtrend_entry=True,
    enable_priority_sweep_recovery_target=True,
    enable_supported_post_replenishment_entry=True,
    enable_priority_book_side_fill_correction=True,
    prefer_fresh_lower_visible_wall_after_base_replenishment=True,
)
PRIORITY_POLICY_V119_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_19_candidate",
    model_version="1.19-candidate",
    parent_model_id="maker_priority_v1_18_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    minimum_wall_supported_base_high_sell_edge_override=0.20,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
    use_local_reference_after_base_replenishment=True,
    enable_falling_profitable_bid_exit=True,
    priority_base_turn_stable_context_grace_seconds=15,
    enable_repeated_two_sided_base_turn=True,
    enable_recent_completed_base_turn_repeat=True,
    allow_repeated_replenishment_to_downtrend_edge=True,
    retain_priority_base_turn_while_live_corridor=True,
    retain_priority_base_turn_on_lower_bid_shift=True,
    enable_visible_wall_anchored_downtrend_entry=True,
    enable_priority_sweep_recovery_target=True,
    enable_supported_post_replenishment_entry=True,
    enable_priority_book_side_fill_correction=True,
    prefer_fresh_lower_visible_wall_after_base_replenishment=True,
)
PRIORITY_POLICY_V120_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_20_candidate",
    model_version="1.20-candidate",
    parent_model_id="maker_priority_v1_19_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    minimum_downtrend_turn_edge_override=0.18,
    minimum_wall_supported_base_high_sell_edge_override=0.20,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
    use_local_reference_after_base_replenishment=True,
    enable_falling_profitable_bid_exit=True,
    priority_base_turn_stable_context_grace_seconds=15,
    enable_repeated_two_sided_base_turn=True,
    enable_recent_completed_base_turn_repeat=True,
    allow_repeated_replenishment_to_downtrend_edge=True,
    retain_priority_base_turn_while_live_corridor=True,
    retain_priority_base_turn_on_lower_bid_shift=True,
    enable_visible_wall_anchored_downtrend_entry=True,
    enable_priority_sweep_recovery_target=True,
    enable_supported_post_replenishment_entry=True,
    enable_priority_book_side_fill_correction=True,
    prefer_fresh_lower_visible_wall_after_base_replenishment=True,
    retain_priority_base_turn_on_recent_sell_corridor=True,
)
PRIORITY_POLICY_V121_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_21_candidate",
    model_version="1.21-candidate",
    parent_model_id="maker_priority_v1_20_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    confirm_exact_offer_clear_in_possible_rise=True,
    minimum_downtrend_turn_edge_override=0.18,
    minimum_wall_supported_base_high_sell_edge_override=0.20,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
    use_local_reference_after_base_replenishment=True,
    enable_falling_profitable_bid_exit=True,
    priority_base_turn_stable_context_grace_seconds=15,
    enable_repeated_two_sided_base_turn=True,
    enable_recent_completed_base_turn_repeat=True,
    allow_repeated_replenishment_to_downtrend_edge=True,
    retain_priority_base_turn_while_live_corridor=True,
    retain_priority_base_turn_on_lower_bid_shift=True,
    enable_visible_wall_anchored_downtrend_entry=True,
    enable_priority_sweep_recovery_target=True,
    enable_supported_post_replenishment_entry=True,
    enable_priority_book_side_fill_correction=True,
    prefer_fresh_lower_visible_wall_after_base_replenishment=True,
    retain_priority_base_turn_on_recent_sell_corridor=True,
)
PRIORITY_POLICY_V122_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_22_candidate",
    model_version="1.22-candidate",
    parent_model_id="maker_priority_v1_21_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    enable_downtrend_wide_spread_base_turn=True,
    confirmed_rise_grace_seconds_override=60,
    confirm_exact_offer_clear_in_possible_rise=True,
    require_exact_offer_clear_volume_coverage=True,
    minimum_downtrend_turn_edge_override=0.18,
    minimum_wall_supported_base_high_sell_edge_override=0.20,
    enable_persistent_bid_downtrend_turn=True,
    use_recent_intraday_reference_for_active_entry=True,
    require_concentrated_downtrend_bid_support=True,
    use_local_reference_after_base_replenishment=True,
    enable_falling_profitable_bid_exit=True,
    priority_base_turn_stable_context_grace_seconds=15,
    enable_repeated_two_sided_base_turn=True,
    enable_recent_completed_base_turn_repeat=True,
    allow_repeated_replenishment_to_downtrend_edge=True,
    retain_priority_base_turn_while_live_corridor=True,
    retain_priority_base_turn_on_lower_bid_shift=True,
    enable_visible_wall_anchored_downtrend_entry=True,
    enable_priority_sweep_recovery_target=True,
    enable_supported_post_replenishment_entry=True,
    enable_priority_book_side_fill_correction=True,
    prefer_fresh_lower_visible_wall_after_base_replenishment=True,
    retain_priority_base_turn_on_recent_sell_corridor=True,
)
PRIORITY_POLICY_V123_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_23_candidate",
    model_version="1.23-candidate",
    # The accumulated v1.2--v1.22 chain overfit the Sanxia calibration days.
    # Rebranch from the production v1.1 policy and add only independently
    # explainable, cross-instrument features.  In particular this profile does
    # not permit a base short merely from ``possible_fall`` plus a wide spread.
    parent_model_id="maker_priority_v1_1",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    confirmed_rise_grace_seconds_override=60,
    use_recent_intraday_reference_for_active_entry=True,
    enable_falling_profitable_bid_exit=True,
    enable_repeated_two_sided_base_turn=True,
    enable_priority_book_side_fill_correction=True,
    confirm_exact_offer_clear_in_possible_rise=True,
    require_exact_offer_clear_volume_coverage=True,
)
PRIORITY_POLICY_V124_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_24_candidate",
    model_version="1.24-candidate",
    parent_model_id="maker_priority_v1_23_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    confirmed_rise_grace_seconds_override=60,
    use_recent_intraday_reference_for_active_entry=True,
    enable_falling_profitable_bid_exit=True,
    enable_repeated_two_sided_base_turn=True,
    enable_priority_book_side_fill_correction=True,
    confirm_exact_offer_clear_in_possible_rise=True,
    require_exact_offer_clear_volume_coverage=True,
    # A customer-base sale in positive momentum may not rely on a stale
    # anchor alone.  Unless a repeated high/low corridor already supplies an
    # explicit replenishment price, the offer must also stand materially
    # above recent real trades and have current nearby sell-side supply.
    require_rising_base_short_recent_trade_premium_and_supply=True,
)
PRIORITY_POLICY_V125_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_25_candidate",
    model_version="1.25-candidate",
    parent_model_id="maker_priority_v1_24_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    confirmed_rise_grace_seconds_override=60,
    use_recent_intraday_reference_for_active_entry=True,
    enable_falling_profitable_bid_exit=True,
    enable_repeated_two_sided_base_turn=True,
    enable_priority_book_side_fill_correction=True,
    confirm_exact_offer_clear_in_possible_rise=True,
    require_exact_offer_clear_volume_coverage=True,
    require_rising_base_short_recent_trade_premium_and_supply=True,
    # Flattening an extra lot and shorting the customer base are two separate
    # risk decisions.  Isolate only the immediate same-cluster rising case;
    # fresh low-side evidence, a deep current premium, a distinct price or an
    # explicit repeated corridor keeps its ordinary authority.
    priority_rising_base_short_after_extra_exit_isolation_seconds=15,
)
PRIORITY_POLICY_V126_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_26_candidate",
    model_version="1.26-candidate",
    parent_model_id="maker_priority_v1_25_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    confirmed_rise_grace_seconds_override=60,
    use_recent_intraday_reference_for_active_entry=True,
    enable_falling_profitable_bid_exit=True,
    enable_repeated_two_sided_base_turn=True,
    enable_priority_book_side_fill_correction=True,
    confirm_exact_offer_clear_in_possible_rise=True,
    require_exact_offer_clear_volume_coverage=True,
    require_rising_base_short_recent_trade_premium_and_supply=True,
    priority_rising_base_short_after_extra_exit_isolation_seconds=15,
    # A wall-supported 0.30--0.50 yuan customer-base short is a moderate,
    # not unlimited, conviction trade.  Once the tape is no longer falling,
    # at least the ordinary 0.20-yuan profit is executable at ask1 and the
    # existing replenishment bid is a full 1.00 yuan below that ask, restore
    # the borrowed customer inventory instead of waiting for a windfall.
    enable_dynamic_medium_base_short_replenishment=True,
)
PRIORITY_POLICY_V127_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_27_candidate",
    model_version="1.27-candidate",
    parent_model_id="maker_priority_v1_26_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    confirmed_rise_grace_seconds_override=60,
    use_recent_intraday_reference_for_active_entry=True,
    enable_falling_profitable_bid_exit=True,
    enable_repeated_two_sided_base_turn=True,
    enable_priority_book_side_fill_correction=True,
    confirm_exact_offer_clear_in_possible_rise=True,
    require_exact_offer_clear_volume_coverage=True,
    require_rising_base_short_recent_trade_premium_and_supply=True,
    priority_rising_base_short_after_extra_exit_isolation_seconds=15,
    enable_dynamic_medium_base_short_replenishment=True,
    # Selling the customer's base creates an economic short.  If a full-sized
    # active buy immediately validates the sold level in a tight rising market,
    # restore the base while the stop cost is still near flat instead of
    # leaving the liability open in hope of a later decline.
    enable_confirmed_rising_near_flat_base_short_stop=True,
    confirmed_rising_base_short_stop_seconds=30,
)
PRIORITY_POLICY_V128_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_28_candidate",
    model_version="1.28-candidate",
    parent_model_id="maker_priority_v1_27_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    confirmed_rise_grace_seconds_override=60,
    use_recent_intraday_reference_for_active_entry=True,
    enable_falling_profitable_bid_exit=True,
    enable_repeated_two_sided_base_turn=True,
    enable_priority_book_side_fill_correction=True,
    confirm_exact_offer_clear_in_possible_rise=True,
    require_exact_offer_clear_volume_coverage=True,
    require_rising_base_short_recent_trade_premium_and_supply=True,
    priority_rising_base_short_after_extra_exit_isolation_seconds=15,
    enable_dynamic_medium_base_short_replenishment=True,
    enable_confirmed_rising_near_flat_base_short_stop=True,
    confirmed_rising_base_short_stop_seconds=30,
    # The five-minute trade reference is an extra stale-anchor check, not a
    # substitute for the current reliable fair region.  In positive momentum,
    # a non-corridor customer-base short must still stand at least the handbook
    # 0.20 yuan above a reliable live trade anchor (including tolerance).
    minimum_rising_base_short_reliable_reference_edge=0.20,
)
PRIORITY_POLICY_V129_CANDIDATE = MakerPolicyProfile(
    model_id="maker_priority_v1_29_candidate",
    model_version="1.29-candidate",
    parent_model_id="maker_priority_v1_28_candidate",
    execution_mode="priority",
    enable_priority_v11_extensions=True,
    confirmed_rise_grace_seconds_override=60,
    use_recent_intraday_reference_for_active_entry=True,
    enable_falling_profitable_bid_exit=True,
    enable_repeated_two_sided_base_turn=True,
    enable_priority_book_side_fill_correction=True,
    confirm_exact_offer_clear_in_possible_rise=True,
    require_exact_offer_clear_volume_coverage=True,
    require_rising_base_short_recent_trade_premium_and_supply=True,
    priority_rising_base_short_after_extra_exit_isolation_seconds=15,
    enable_dynamic_medium_base_short_replenishment=True,
    enable_confirmed_rising_near_flat_base_short_stop=True,
    confirmed_rising_base_short_stop_seconds=30,
    minimum_rising_base_short_reliable_reference_edge=0.20,
    # A customer-base deficit is an economic short.  Once the visible bid
    # leaves at least the ordinary 0.50-yuan active-entry edge and displays a
    # full standard lot, quote one tick ahead instead of leaving a stale deep
    # replenishment target.  The order remains passive and uses only later
    # sells; no triggering trade is reused.
    enable_profitable_visible_bid_base_replenishment=True,
)
PRIORITY_POLICY_V130_CANDIDATE = replace(
    PRIORITY_POLICY_V129_CANDIDATE,
    model_id="maker_priority_v1_30_candidate",
    model_version="1.30-candidate",
    parent_model_id="maker_priority_v1_29_candidate",
)
PRIORITY_POLICY_V131_CANDIDATE = replace(
    PRIORITY_POLICY_V130_CANDIDATE,
    model_id="maker_priority_v1_31_candidate",
    model_version="1.31-candidate",
    parent_model_id="maker_priority_v1_30_candidate",
    # A confirmed falling state with overwhelming cumulative selling can make
    # a still-executable near-flat bid more valuable than a distant passive
    # target.  This permission only flattens inventory above the customer base;
    # it never creates or enlarges a customer-base short.
    enable_confirmed_falling_near_flat_extra_exit=True,
)
PRIORITY_POLICY_V132_CANDIDATE = replace(
    PRIORITY_POLICY_V130_CANDIDATE,
    model_id="maker_priority_v1_32_candidate",
    model_version="1.32-candidate",
    parent_model_id="maker_priority_v1_30_candidate",
    # Selling the customer's base creates an economic short.  Once a full
    # visible bid already locks in the ordinary 0.20-yuan replenishment edge,
    # quote that live bid instead of leaving the liability pinned to an older
    # deep target.  This changes only passive base replenishment; it does not
    # add a base-sale permission or touch extra inventory.
    minimum_profitable_visible_bid_base_replenishment_edge_override=0.20,
)
PRIORITY_POLICY_V133_CANDIDATE = replace(
    PRIORITY_POLICY_V130_CANDIDATE,
    model_id="maker_priority_v1_33_candidate",
    model_version="1.33-candidate",
    parent_model_id="maker_priority_v1_30_candidate",
    # A customer-base short is invalidated when, shortly after the sale, a
    # fresh uninterrupted sequence of real active buys totals at least the
    # ordinary anchor size and brings a tight market back to the sale price.
    # Any intervening active sell resets the sequence.  This deliberately
    # bypasses only a lagging fair-price reference; all near-flat, capacity and
    # rising-state checks remain in force.
    enable_confirmed_rising_buy_sequence_base_short_stop=True,
    confirmed_rising_buy_sequence_base_short_stop_seconds=60,
)
PRIORITY_POLICY_V134_CANDIDATE = replace(
    PRIORITY_POLICY_V133_CANDIDATE,
    model_id="maker_priority_v1_34_candidate",
    model_version="1.34-candidate",
    parent_model_id="maker_priority_v1_33_candidate",
    # A long-lived, concentrated nearby bid wall can support one passive
    # extra-inventory entry during a falling tape when the same causal window
    # has already traded at the high side.  This permission never sells the
    # customer's base and never bypasses the post-risk-exit re-entry cooldown.
    enable_persistent_wall_supported_falling_extra_entry=True,
)
PRIORITY_POLICY_V135_CANDIDATE = replace(
    PRIORITY_POLICY_V134_CANDIDATE,
    model_id="maker_priority_v1_35_candidate",
    model_version="1.35-candidate",
    parent_model_id="maker_priority_v1_34_candidate",
    # Once the causal wall-backed opportunity has created a passive bid, keep
    # that exact price for a short lifecycle while the same wall remains
    # continuously visible and the exit corridor is still intact.  A vanished
    # wall, confirmed rise, excessive age or loss of capacity cancels it.
    retain_persistent_wall_supported_falling_extra_entry=True,
)
PRIORITY_POLICY_V136_CANDIDATE = replace(
    PRIORITY_POLICY_V135_CANDIDATE,
    model_id="maker_priority_v1_36_candidate",
    model_version="1.36-candidate",
    parent_model_id="maker_priority_v1_35_candidate",
    # Once the low bid has been causally authorized, a later trend-label or
    # inside-spread relabel does not by itself invalidate the exact same
    # continuously visible wall and still-wide order-to-ask corridor.
    retain_persistent_wall_supported_entry_across_state_relabels=True,
)
PRIORITY_POLICY_V137_CANDIDATE = replace(
    PRIORITY_POLICY_V134_CANDIDATE,
    model_id="maker_priority_v1_37_candidate",
    model_version="1.37-candidate",
    parent_model_id="maker_priority_v1_34_candidate",
    # A tight low offer can be taken for one extra lot when it collapses the
    # current midpoint but does not erase a causally established higher
    # working range: the account already had a passive low bid, a nearby wall
    # has persisted, and recent real high-side buying proves an exit corridor.
    # This never restores or creates a customer-base short and does not inherit
    # the economically empty v1.35/v1.36 order-lifecycle experiments.
    enable_supported_current_midpoint_collapse_extra_entry=True,
)
PRIORITY_POLICY_V138_CANDIDATE = replace(
    PRIORITY_POLICY_V137_CANDIDATE,
    model_id="maker_priority_v1_38_candidate",
    model_version="1.38-candidate",
    parent_model_id="maker_priority_v1_37_candidate",
    # A real high-side buy can validate the upper half of a still-balanced
    # 0.18--0.50 yuan corridor.  When the lower side remains visibly supported,
    # quote one passive extra-inventory bid there instead of selling the
    # customer's base first.  The permission is identical for both bonds and
    # does not cross the spread or reuse the triggering print as a fill.
    enable_high_side_validated_supported_corridor_entry=True,
)
PRIORITY_POLICY_V139_CANDIDATE = replace(
    PRIORITY_POLICY_V138_CANDIDATE,
    model_id="maker_priority_v1_39_candidate",
    model_version="1.39-candidate",
    parent_model_id="maker_priority_v1_38_candidate",
    # A continuously visible nearby bid wall plus substantial real buying and
    # selling over the same recent market-temperature window can establish a
    # two-sided corridor before the next low print arrives.  Quote only one
    # passive extra-inventory bid; never sell or restore the customer base.
    # Both bonds use this identical causal permission and parameter set.
    enable_persistent_two_sided_wall_corridor_entry=True,
)
PRIORITY_POLICY_V140_CANDIDATE = replace(
    PRIORITY_POLICY_V137_CANDIDATE,
    model_id="maker_priority_v1_40_candidate",
    model_version="1.40-candidate",
    parent_model_id="maker_priority_v1_37_candidate",
    # A customer-base deficit is an economic short and must retain a live,
    # causally bounded passive recovery quote even when neither the old
    # 0.50-yuan profit gate nor the ordinary 5,000-bond extra-entry wall is
    # present.  After a completed high/low base turn, an already visible,
    # concentrated ask2--ask5 cluster near the proven high side may also be
    # pre-positioned one tick ahead.  Both permissions are priority-only and
    # intentionally do not inherit the unconfirmed v1.38/v1.39 extra entries.
    enable_continuous_dynamic_base_short_replenishment=True,
    enable_post_replenishment_high_ask_cluster_preposition=True,
)
PRIORITY_POLICY_V141_CANDIDATE = replace(
    PRIORITY_POLICY_V137_CANDIDATE,
    model_id="maker_priority_v1_41_candidate",
    model_version="1.41-candidate",
    parent_model_id="maker_priority_v1_37_candidate",
    # A customer-base deficit is an economic short.  Keep one causally
    # bounded passive recovery quote alive without requiring the ordinary
    # extra-entry wall or a fixed profit threshold.  This candidate
    # deliberately excludes the unconfirmed ask2--ask5 pre-positioning
    # experiment from v1.40.
    enable_continuous_dynamic_base_short_replenishment=True,
)
PRIORITY_POLICY_V142_CANDIDATE = replace(
    PRIORITY_POLICY_V141_CANDIDATE,
    model_id="maker_priority_v1_42_candidate",
    model_version="1.42-candidate",
    parent_model_id="maker_priority_v1_41_candidate",
    # Normal maker decisions continue until the last pre-close millisecond.
    # Resting orders can still fill on the 15:30 closing frame, but the engine
    # does not create a new order after there is no later execution chance.
    latest_entry_time="15:29:59.999",
)
PRIORITY_POLICY_V143_CANDIDATE = replace(
    PRIORITY_POLICY_V142_CANDIDATE,
    model_id="maker_priority_v1_43_candidate",
    model_version="1.43-candidate",
    parent_model_id="maker_priority_v1_42_candidate",
    # If one newly observed aggressive-buy frame consumes at least 80% of the
    # immediately preceding visible ask cluster, leaves only a sweepable tail,
    # and exposes a large next-ask gap, a customer-base deficit may recover by
    # sweeping the tail.  Earlier historical cluster peaks must not dilute the
    # causally local consumption ratio.
    enable_immediate_visible_cluster_tail_recovery=True,
)
PRIORITY_POLICY_V144_CANDIDATE = replace(
    PRIORITY_POLICY_V143_CANDIDATE,
    model_id="maker_priority_v1_44_candidate",
    model_version="1.44-candidate",
    parent_model_id="maker_priority_v1_43_candidate",
    # A persistent 0.30--0.50-yuan inside corridor plus a recent real
    # high-side buy can authorize one passive extra-inventory bid without a
    # 5,000-bond lower wall.  The customer base is never sold first: only the
    # quantity actually bought at the low side becomes eligible for exit.
    enable_persistent_wide_spread_buy_first_entry=True,
)
QUEUE_POLICY_V10 = MakerPolicyProfile(
    model_id="maker_queue_v1_0",
    model_version="1.0",
    parent_model_id="maker_shared_v1_0",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
)
QUEUE_POLICY_V11_CANDIDATE = MakerPolicyProfile(
    model_id="maker_queue_v1_1_candidate",
    model_version="1.1-candidate",
    parent_model_id="maker_queue_v1_0",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
    share_simultaneous_same_price_queue=True,
)
QUEUE_POLICY_V12_CANDIDATE = MakerPolicyProfile(
    model_id="maker_queue_v1_2_candidate",
    model_version="1.2-candidate",
    parent_model_id="maker_queue_v1_1_candidate",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
    share_simultaneous_same_price_queue=True,
    queue_extra_exit_context_grace_seconds=15,
    queue_graced_extra_exit_to_base_sale_window_seconds=300,
    queue_replenishment_exact_fill_buffer_bonds=1_000.0,
)
QUEUE_POLICY_V13_CANDIDATE = MakerPolicyProfile(
    model_id="maker_queue_v1_3_candidate",
    model_version="1.3-candidate",
    parent_model_id="maker_queue_v1_2_candidate",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
    share_simultaneous_same_price_queue=True,
    queue_extra_exit_context_grace_seconds=15,
    queue_graced_extra_exit_to_base_sale_window_seconds=300,
    queue_replenishment_exact_fill_buffer_bonds=1_000.0,
    queue_cleared_position_one_tick_grace_seconds=3,
)
QUEUE_POLICY_V14_CANDIDATE = MakerPolicyProfile(
    model_id="maker_queue_v1_4_candidate",
    model_version="1.4-candidate",
    parent_model_id="maker_queue_v1_3_candidate",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
    share_simultaneous_same_price_queue=True,
    queue_extra_exit_context_grace_seconds=15,
    queue_graced_extra_exit_to_base_sale_window_seconds=300,
    queue_replenishment_exact_fill_buffer_bonds=1_000.0,
    queue_cleared_position_one_tick_grace_seconds=3,
    queue_cleared_buy_context_grace_seconds=3,
)
QUEUE_POLICY_V15_CANDIDATE = MakerPolicyProfile(
    model_id="maker_queue_v1_5_candidate",
    model_version="1.5-candidate",
    parent_model_id="maker_queue_v1_4_candidate",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
    share_simultaneous_same_price_queue=True,
    queue_extra_exit_context_grace_seconds=15,
    queue_graced_extra_exit_to_base_sale_window_seconds=300,
    queue_replenishment_exact_fill_buffer_bonds=1_000.0,
    queue_cleared_position_one_tick_grace_seconds=3,
    queue_cleared_buy_context_grace_seconds=3,
    queue_cleared_sell_reprice_grace_seconds=3,
)
QUEUE_POLICY_V16_CANDIDATE = MakerPolicyProfile(
    model_id="maker_queue_v1_6_candidate",
    model_version="1.6-candidate",
    parent_model_id="maker_queue_v1_5_candidate",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
    share_simultaneous_same_price_queue=True,
    queue_extra_exit_context_grace_seconds=15,
    queue_graced_extra_exit_to_base_sale_window_seconds=300,
    queue_replenishment_exact_fill_buffer_bonds=1_000.0,
    queue_cleared_position_one_tick_grace_seconds=3,
    queue_cleared_buy_context_grace_seconds=3,
    queue_cleared_sell_reprice_grace_seconds=3,
    queue_cleared_extra_sell_reprice_grace_seconds=30,
)
QUEUE_POLICY_V17_CANDIDATE = MakerPolicyProfile(
    model_id="maker_queue_v1_7_candidate",
    model_version="1.7-candidate",
    parent_model_id="maker_queue_v1_6_candidate",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
    enable_downtrend_wide_spread_base_turn=True,
    enable_downtrend_turn_while_extra_inventory=True,
    minimum_downtrend_turn_edge_override=0.18,
    share_simultaneous_same_price_queue=True,
    queue_extra_exit_context_grace_seconds=15,
    queue_graced_extra_exit_to_base_sale_window_seconds=300,
    queue_replenishment_exact_fill_buffer_bonds=1_000.0,
    queue_cleared_position_one_tick_grace_seconds=3,
    queue_cleared_buy_context_grace_seconds=3,
    queue_cleared_sell_reprice_grace_seconds=3,
    queue_cleared_extra_sell_reprice_grace_seconds=30,
    queue_inventory_turn_exact_fill_buffer_bonds=1_000.0,
)
QUEUE_POLICY_V18_CANDIDATE = MakerPolicyProfile(
    model_id="maker_queue_v1_8_candidate",
    model_version="1.8-candidate",
    parent_model_id="maker_queue_v1_7_candidate",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
    enable_downtrend_wide_spread_base_turn=True,
    enable_downtrend_turn_while_extra_inventory=True,
    minimum_downtrend_turn_edge_override=0.18,
    share_simultaneous_same_price_queue=True,
    queue_extra_exit_context_grace_seconds=15,
    queue_graced_extra_exit_to_base_sale_window_seconds=300,
    queue_replenishment_exact_fill_buffer_bonds=1_000.0,
    queue_cleared_position_one_tick_grace_seconds=3,
    queue_cleared_buy_context_grace_seconds=3,
    queue_cleared_sell_reprice_grace_seconds=3,
    queue_cleared_extra_sell_reprice_grace_seconds=30,
    queue_inventory_turn_exact_fill_buffer_bonds=1_000.0,
    allow_fresh_post_replenishment_inventory_turn=True,
)
QUEUE_POLICY_V19_CANDIDATE = MakerPolicyProfile(
    model_id="maker_queue_v1_9_candidate",
    model_version="1.9-candidate",
    parent_model_id="maker_queue_v1_8_candidate",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
    enable_downtrend_wide_spread_base_turn=True,
    enable_downtrend_turn_while_extra_inventory=True,
    minimum_downtrend_turn_edge_override=0.18,
    share_simultaneous_same_price_queue=True,
    queue_extra_exit_context_grace_seconds=15,
    queue_graced_extra_exit_to_base_sale_window_seconds=300,
    queue_replenishment_exact_fill_buffer_bonds=1_000.0,
    queue_cleared_position_one_tick_grace_seconds=3,
    queue_cleared_buy_context_grace_seconds=3,
    queue_cleared_sell_reprice_grace_seconds=3,
    queue_cleared_extra_sell_reprice_grace_seconds=30,
    queue_inventory_turn_exact_fill_buffer_bonds=1_000.0,
    allow_fresh_post_replenishment_inventory_turn=True,
    waive_inventory_turn_buffer_on_clean_exact_queue_clear=True,
)
QUEUE_POLICY_V110_CANDIDATE = MakerPolicyProfile(
    model_id="maker_queue_v1_10_candidate",
    model_version="1.10-candidate",
    parent_model_id="maker_queue_v1_9_candidate",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
    enable_downtrend_wide_spread_base_turn=True,
    enable_downtrend_turn_while_extra_inventory=True,
    minimum_downtrend_turn_edge_override=0.18,
    share_simultaneous_same_price_queue=True,
    queue_extra_exit_context_grace_seconds=15,
    queue_graced_extra_exit_to_base_sale_window_seconds=300,
    queue_replenishment_exact_fill_buffer_bonds=1_000.0,
    queue_cleared_position_one_tick_grace_seconds=3,
    queue_cleared_buy_context_grace_seconds=3,
    queue_cleared_sell_reprice_grace_seconds=3,
    queue_cleared_extra_sell_reprice_grace_seconds=30,
    queue_inventory_turn_exact_fill_buffer_bonds=1_000.0,
    allow_fresh_post_replenishment_inventory_turn=True,
    waive_inventory_turn_buffer_on_clean_exact_queue_clear=True,
    queue_cleared_inventory_turn_corridor_seconds=180,
)
QUEUE_POLICY_V111_CANDIDATE = MakerPolicyProfile(
    model_id="maker_queue_v1_11_candidate",
    model_version="1.11-candidate",
    parent_model_id="maker_queue_v1_10_candidate",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
    enable_downtrend_wide_spread_base_turn=True,
    enable_downtrend_turn_while_extra_inventory=True,
    minimum_downtrend_turn_edge_override=0.18,
    share_simultaneous_same_price_queue=True,
    queue_extra_exit_context_grace_seconds=15,
    queue_graced_extra_exit_to_base_sale_window_seconds=300,
    queue_replenishment_exact_fill_buffer_bonds=1_000.0,
    queue_cleared_position_one_tick_grace_seconds=3,
    queue_cleared_buy_context_grace_seconds=3,
    queue_cleared_sell_reprice_grace_seconds=3,
    queue_cleared_extra_sell_reprice_grace_seconds=30,
    queue_inventory_turn_exact_fill_buffer_bonds=1_000.0,
    allow_fresh_post_replenishment_inventory_turn=True,
    waive_inventory_turn_buffer_on_clean_exact_queue_clear=True,
    queue_cleared_inventory_turn_corridor_seconds=180,
    retain_queue_cleared_inventory_turn_while_live_corridor=True,
)
QUEUE_POLICY_V112_CANDIDATE = MakerPolicyProfile(
    model_id="maker_queue_v1_12_candidate",
    model_version="1.12-candidate",
    parent_model_id="maker_queue_v1_11_candidate",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
    enable_downtrend_wide_spread_base_turn=True,
    enable_downtrend_turn_while_extra_inventory=True,
    minimum_downtrend_turn_edge_override=0.18,
    share_simultaneous_same_price_queue=True,
    queue_extra_exit_context_grace_seconds=15,
    queue_graced_extra_exit_to_base_sale_window_seconds=300,
    queue_replenishment_exact_fill_buffer_bonds=1_000.0,
    queue_cleared_position_one_tick_grace_seconds=3,
    queue_cleared_buy_context_grace_seconds=3,
    queue_cleared_sell_reprice_grace_seconds=3,
    queue_cleared_extra_sell_reprice_grace_seconds=30,
    queue_inventory_turn_exact_fill_buffer_bonds=1_000.0,
    allow_fresh_post_replenishment_inventory_turn=True,
    waive_inventory_turn_buffer_on_clean_exact_queue_clear=True,
    queue_cleared_inventory_turn_corridor_seconds=180,
    retain_queue_cleared_inventory_turn_while_live_corridor=True,
    fill_queue_cleared_crossed_book_residual=True,
)
QUEUE_POLICY_V113_CANDIDATE = MakerPolicyProfile(
    model_id="maker_queue_v1_13_candidate",
    model_version="1.13-candidate",
    parent_model_id="maker_queue_v1_12_candidate",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
    enable_downtrend_wide_spread_base_turn=True,
    enable_downtrend_turn_while_extra_inventory=True,
    minimum_downtrend_turn_edge_override=0.18,
    share_simultaneous_same_price_queue=True,
    queue_extra_exit_context_grace_seconds=15,
    queue_graced_extra_exit_to_base_sale_window_seconds=300,
    queue_replenishment_exact_fill_buffer_bonds=1_000.0,
    queue_cleared_position_one_tick_grace_seconds=3,
    queue_cleared_buy_context_grace_seconds=3,
    queue_cleared_sell_reprice_grace_seconds=3,
    queue_cleared_extra_sell_reprice_grace_seconds=30,
    queue_inventory_turn_exact_fill_buffer_bonds=1_000.0,
    allow_fresh_post_replenishment_inventory_turn=True,
    waive_inventory_turn_buffer_on_clean_exact_queue_clear=True,
    queue_cleared_inventory_turn_corridor_seconds=180,
    retain_queue_cleared_inventory_turn_while_live_corridor=True,
    fill_queue_cleared_crossed_book_residual=True,
    retain_queue_queued_inventory_turn_in_stable=True,
)
QUEUE_POLICY_V114_CANDIDATE = MakerPolicyProfile(
    model_id="maker_queue_v1_14_candidate",
    model_version="1.14-candidate",
    parent_model_id="maker_queue_v1_13_candidate",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
    enable_downtrend_wide_spread_base_turn=True,
    enable_downtrend_turn_while_extra_inventory=True,
    minimum_downtrend_turn_edge_override=0.18,
    share_simultaneous_same_price_queue=True,
    queue_extra_exit_context_grace_seconds=15,
    queue_graced_extra_exit_to_base_sale_window_seconds=300,
    queue_replenishment_exact_fill_buffer_bonds=1_000.0,
    queue_cleared_position_one_tick_grace_seconds=3,
    queue_cleared_buy_context_grace_seconds=3,
    queue_cleared_sell_reprice_grace_seconds=3,
    queue_cleared_extra_sell_reprice_grace_seconds=30,
    queue_inventory_turn_exact_fill_buffer_bonds=1_000.0,
    allow_fresh_post_replenishment_inventory_turn=True,
    waive_inventory_turn_buffer_on_clean_exact_queue_clear=True,
    queue_cleared_inventory_turn_corridor_seconds=180,
    retain_queue_cleared_inventory_turn_while_live_corridor=True,
    fill_queue_cleared_crossed_book_residual=True,
    retain_queue_queued_inventory_turn_in_stable=True,
    retain_queue_cleared_inventory_turn_buy_on_lower_reprice=True,
)
QUEUE_POLICY_V115_CANDIDATE = MakerPolicyProfile(
    model_id="maker_queue_v1_15_candidate",
    model_version="1.15-candidate",
    parent_model_id="maker_queue_v1_13_candidate",
    execution_mode="queue",
    enable_priority_v11_extensions=False,
    enable_downtrend_wide_spread_base_turn=True,
    enable_downtrend_turn_while_extra_inventory=True,
    minimum_downtrend_turn_edge_override=0.18,
    share_simultaneous_same_price_queue=True,
    queue_extra_exit_context_grace_seconds=15,
    queue_graced_extra_exit_to_base_sale_window_seconds=300,
    queue_replenishment_exact_fill_buffer_bonds=1_000.0,
    queue_cleared_position_one_tick_grace_seconds=3,
    queue_cleared_buy_context_grace_seconds=3,
    queue_cleared_sell_reprice_grace_seconds=3,
    queue_cleared_extra_sell_reprice_grace_seconds=30,
    queue_inventory_turn_exact_fill_buffer_bonds=1_000.0,
    allow_fresh_post_replenishment_inventory_turn=True,
    waive_inventory_turn_buffer_on_clean_exact_queue_clear=True,
    queue_cleared_inventory_turn_corridor_seconds=180,
    retain_queue_cleared_inventory_turn_while_live_corridor=True,
    fill_queue_cleared_crossed_book_residual=True,
    retain_queue_queued_inventory_turn_in_stable=True,
    retain_clean_cleared_inventory_turn_buy_while_falling=True,
)
QUEUE_POLICY_V116_CANDIDATE = replace(
    QUEUE_POLICY_V115_CANDIDATE,
    model_id="maker_queue_v1_16_candidate",
    model_version="1.16-candidate",
    parent_model_id="maker_queue_v1_15_candidate",
)
QUEUE_POLICY_V117_CANDIDATE = replace(
    QUEUE_POLICY_V113_CANDIDATE,
    model_id="maker_queue_v1_17_candidate",
    model_version="1.17-candidate",
    parent_model_id="maker_queue_v1_13_candidate",
    latest_entry_time="15:29:59.999",
)
QUEUE_POLICY_V118_CANDIDATE = replace(
    QUEUE_POLICY_V117_CANDIDATE,
    model_id="maker_queue_v1_18_candidate",
    model_version="1.18-candidate",
    parent_model_id="maker_queue_v1_17_candidate",
    # When the parent would join the displayed best quote, improve the old
    # second level by one tick instead.  This creates a new second level with
    # no displayed queue ahead and waits for a later sweep through level one.
    quote_at_second_level_front=True,
)
QUEUE_POLICY_V119_CANDIDATE = replace(
    QUEUE_POLICY_V118_CANDIDATE,
    model_id="maker_queue_v1_19_candidate",
    model_version="1.19-candidate",
    parent_model_id="maker_queue_v1_18_candidate",
    # Keep an ordinary best-level tail when that queue still has realistic
    # turnover value.  Move to the empty slot before level two only when the
    # visible best queue is already large, the inside spread remains a real
    # maker corridor, and level two pays a material price improvement.
    dynamically_choose_second_level_front=True,
)
WINDFALL_POLICY_V10 = MakerPolicyProfile(
    model_id="maker_windfall_v1_0",
    model_version="1.0",
    parent_model_id=None,
    execution_mode="windfall",
    enable_priority_v11_extensions=False,
)
WINDFALL_POLICY_V11_CANDIDATE = MakerPolicyProfile(
    model_id="maker_windfall_v1_1_candidate",
    model_version="1.1-candidate",
    parent_model_id="maker_windfall_v1_0",
    execution_mode="windfall",
    enable_priority_v11_extensions=False,
    exclude_wide_persistent_windfall_reference=True,
)


def maker_policy_for_mode(fill_mode: str) -> MakerPolicyProfile:
    if fill_mode == "priority":
        return PRIORITY_POLICY_V11
    if fill_mode == "queue":
        return QUEUE_POLICY_V10
    if fill_mode == "windfall":
        return WINDFALL_POLICY_V10
    raise ValueError(f"Unknown maker fill mode: {fill_mode}")


REALTIME_COMPARISON_POLICIES = {
    PRIORITY_POLICY_V137_CANDIDATE.model_id: PRIORITY_POLICY_V137_CANDIDATE,
    PRIORITY_POLICY_V142_CANDIDATE.model_id: PRIORITY_POLICY_V142_CANDIDATE,
    PRIORITY_POLICY_V143_CANDIDATE.model_id: PRIORITY_POLICY_V143_CANDIDATE,
    PRIORITY_POLICY_V144_CANDIDATE.model_id: PRIORITY_POLICY_V144_CANDIDATE,
    QUEUE_POLICY_V113_CANDIDATE.model_id: QUEUE_POLICY_V113_CANDIDATE,
    QUEUE_POLICY_V117_CANDIDATE.model_id: QUEUE_POLICY_V117_CANDIDATE,
    QUEUE_POLICY_V118_CANDIDATE.model_id: QUEUE_POLICY_V118_CANDIDATE,
}


def realtime_comparison_policies(
    config: AppConfig,
) -> tuple[MakerPolicyProfile, ...]:
    """Return configured paper-only models that run beside the baselines."""
    return tuple(
        REALTIME_COMPARISON_POLICIES[model_id]
        for model_id in config.maker_paper.realtime_comparison_model_ids
    )


def configured_maker_bond_codes(config: AppConfig) -> tuple[str, ...]:
    """Return the independently simulated maker instruments."""
    return config.maker_paper.bond_codes or (config.qmt.bond_code,)


def maker_strategy_prefix(config: AppConfig, bond_code: str) -> str:
    """Keep the primary bond's historical IDs while namespacing extra bonds."""
    if bond_code == config.qmt.bond_code:
        return "maker_v01"
    code_key = bond_code.split(".", 1)[0].lower()
    return f"maker_{code_key}_v01"


def maker_strategy_ids(config: AppConfig, bond_code: str) -> tuple[str, ...]:
    prefix = maker_strategy_prefix(config, bond_code)
    strategy_ids = [
        f"{prefix}_{mode}" for mode in config.maker_paper.fill_modes
    ]
    if config.maker_paper.super_windfall_enabled:
        strategy_ids.append(f"{prefix}_super_windfall")
    strategy_ids.extend(
        maker_comparison_strategy_id(config, bond_code, policy)
        for policy in realtime_comparison_policies(config)
    )
    return tuple(strategy_ids)


def maker_comparison_strategy_id(
    config: AppConfig, bond_code: str, policy: MakerPolicyProfile,
) -> str:
    """Give each persisted comparison ledger an explicit model identity."""
    prefix = maker_strategy_prefix(config, bond_code)
    model_key = policy.model_id.removeprefix("maker_")
    return f"{prefix}_{model_key}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _floor_to_tick(value: float, price_tick: float) -> float:
    """Quantize a simulated limit price down to a legal exchange price tick."""
    step = Decimal(str(price_tick))
    units = (Decimal(str(value)) / step).to_integral_value(rounding=ROUND_FLOOR)
    return float(units * step)


def _ceil_to_tick(value: float, price_tick: float) -> float:
    """Quantize a sell-side economic floor up to a legal price tick."""
    step = Decimal(str(price_tick))
    units = (Decimal(str(value)) / step).to_integral_value(
        rounding=ROUND_CEILING
    )
    return float(units * step)


@dataclass
class MakerLot:
    db_id: int
    kind: str
    opened_ms: int
    entry_price: float | None
    original_quantity: float
    remaining_quantity: float
    target_price: float | None = None


@dataclass
class MakerOrder:
    db_id: int
    side: str
    kind: str
    lot_id: int | None
    created_ms: int
    limit_price: float
    quantity: float
    price_boundary: float
    price_boundary_kind: str
    filled_quantity: float = 0.0
    queue_ahead: float = 0.0
    queue_cleared_ms: int = 0
    queue_cleared_crossed_book: bool = False
    exact_fill_uncertainty_buffer: float = 0.0
    context_invalid_since_ms: int = 0
    retained_after_context_loss: bool = False
    retained_after_queue_cleared_reprice: bool = False
    stable_context_grace_eligible: bool = False
    base_turn_corridor_origin: bool = False
    retained_after_recent_sell_corridor: bool = False
    retained_after_queue_cleared_inventory_turn: bool = False
    base_turn_replenishment_ceiling: float = 0.0
    repeated_turn_replenishment_price: float = 0.0
    visible_wall_entry_price: float = 0.0
    inventory_neutral_downtrend_turn: bool = False
    medium_wall_supported_base_short: bool = False
    queue_position_kind: str | None = None
    target_price: float | None = None

    @property
    def remaining(self) -> float:
        return max(0.0, self.quantity - self.filled_quantity)


@dataclass
class LegacyAskWall:
    price: float
    first_ms: int
    last_seen_ms: int
    peak_bonds: float
    current_bonds: float
    aggressive_buys: deque[tuple[int, float]] = field(default_factory=deque)
    emitted: bool = False


@dataclass(frozen=True)
class MakerDecisionContext:
    """One causal view shared by entry, exit and inventory decisions."""

    reference_price: float
    reference_source: str
    reliable_anchor: bool
    spread: float
    bid_support_bonds: float
    ask_supply_bonds: float
    wall_threshold_bonds: float
    breakout_support_price: float = 0.0
    breakout_lower_sell_bonds: float = 0.0

    @property
    def has_bid_support(self) -> bool:
        return self.bid_support_bonds + 1e-9 >= self.wall_threshold_bonds

    @property
    def has_ask_supply(self) -> bool:
        return self.ask_supply_bonds + 1e-9 >= self.wall_threshold_bonds

    @property
    def breakout_support_strong(self) -> bool:
        return (
            self.breakout_support_price > 0
            and self.breakout_lower_sell_bonds + 1e-9 < 5_000.0
        )


@dataclass
class MakerAccount:
    market_date: str
    bond_code: str
    strategy_id: str
    fill_mode: str
    policy: MakerPolicyProfile
    initial_inventory: float
    maximum_inventory: float
    initial_cash: float
    cash: float
    inventory: float
    additional_buying_capacity: float = 0.0
    funding_adjustment: float = 0.0
    lots: dict[int, MakerLot] = field(default_factory=dict)
    buy_order: MakerOrder | None = None
    sell_orders: dict[int, MakerOrder] = field(default_factory=dict)
    fills: int = 0
    trading_pnl: float = 0.0
    last_market_ts_ms: int = 0
    last_tick_id: int = 0
    last_bid: float = 0.0
    last_ask: float = 0.0
    last_bids: tuple[tuple[float, float], ...] = ()
    last_asks: tuple[tuple[float, float], ...] = ()
    replenishment_quantity: float = 0.0
    replenishment_sale_value: float = 0.0
    medium_wall_supported_replenishment_quantity: float = 0.0
    medium_wall_supported_replenishment_sale_value: float = 0.0
    last_base_short_sale_ts_ms: int = 0
    base_short_rising_buy_sequence_bonds: float = 0.0
    last_base_replenishment_price: float = 0.0
    last_base_replenishment_ts_ms: int = 0
    last_profitable_visible_bid_replenishment_ts_ms: int = 0
    last_extra_exit_ts_ms: int = 0
    last_priority_extra_inventory_exit_price: float = 0.0
    last_priority_extra_inventory_exit_ts_ms: int = 0
    last_falling_profitable_exit_price: float = 0.0
    last_falling_profitable_exit_ts_ms: int = 0
    pending_replenishment_exact_fill_buffer: float = 0.0
    pending_repeated_turn_replenishment_price: float = 0.0
    pending_inventory_turn_quantity: float = 0.0
    pending_inventory_turn_sale_value: float = 0.0
    last_completed_base_turn_sell_price: float = 0.0
    last_completed_base_turn_buy_price: float = 0.0
    last_completed_base_turn_ts_ms: int = 0
    last_active_entry_price: float | None = None
    purpose: str = "standard"

    @property
    def customer_base_short_bonds(self) -> float:
        """Economic short created by selling the customer's opening base."""
        if self.purpose != "standard":
            return 0.0
        return max(0.0, self.initial_inventory - self.inventory)

    @property
    def extra_inventory_bonds(self) -> float:
        """Inventory held above the customer's opening base."""
        if self.purpose != "standard":
            return max(0.0, self.inventory)
        return max(0.0, self.inventory - self.initial_inventory)


class MakerPaperEngine:
    """
    Inventory-aware, broker-free maker simulation.

    All orders and fills exist only in SQLite. This class never imports or
    calls a trading API. One account is maintained per configured fill mode.
    """

    def __init__(
        self, config: AppConfig, store: SQLiteStore, *,
        bond_code: str | None = None, strategy_prefix: str | None = None,
        priority_policy: MakerPolicyProfile | None = None,
        queue_policy: MakerPolicyProfile | None = None,
        windfall_policy: MakerPolicyProfile | None = None,
        fill_modes: tuple[str, ...] | None = None,
        include_windfall: bool | None = None,
        strategy_ids_by_mode: dict[str, str] | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.bond_code = bond_code or config.qmt.bond_code
        self.stock_code = maker_underlying_stock_code(config, self.bond_code)
        self.strategy_prefix = strategy_prefix or maker_strategy_prefix(
            config, self.bond_code
        )
        self.priority_policy = priority_policy or PRIORITY_POLICY_V11
        self.queue_policy = queue_policy or QUEUE_POLICY_V10
        self.windfall_policy = windfall_policy or WINDFALL_POLICY_V10
        paper = config.maker_paper
        self.fill_modes = tuple(
            paper.fill_modes if fill_modes is None else fill_modes
        )
        self.include_windfall = (
            paper.super_windfall_enabled
            if include_windfall is None else include_windfall
        )
        self.strategy_ids_by_mode = dict(strategy_ids_by_mode or {})
        self.parameters = MakerParameters(
            price_tick=paper.price_tick,
            order_quantity_bonds=paper.order_quantity_bonds,
            earliest_entry_time=paper.earliest_entry,
            latest_entry_time=paper.latest_entry,
            opening_caution_effective_date=(
                paper.opening_caution_effective_date
            ),
            opening_caution_end_time=paper.opening_caution_end,
            opening_caution_minimum_edge=(
                paper.opening_caution_minimum_edge
            ),
        )
        self.analyzer = MakerAnalyzer(
            self.bond_code, self.stock_code, self.parameters
        )
        self.accounts: dict[str, MakerAccount] = {}
        self.market_date: str | None = None
        self.fills_this_run = 0
        self.previous_close_reference = 0.0
        self.observed_market_trade = False
        self.last_confirmed_rise_trade_ts_ms = 0
        self.last_confirmed_rise_price = 0.0
        # A narrowly confirmed offer clear in ``possible_rise`` belongs only
        # to profiles that explicitly enable the permission.  Keep it apart
        # from the market-wide ``rising`` confirmation so a priority
        # candidate cannot silently change queue execution decisions.
        self.last_exact_offer_clear_rise_trade_ts_ms = 0
        self.last_exact_offer_clear_rise_price = 0.0
        self.last_intraday_working_reference = 0.0
        self.last_intraday_working_reference_ts_ms = 0
        self.previous_intraday_working_reference = 0.0
        self.previous_intraday_working_reference_ts_ms = 0
        self.last_visible_bid_wall_price = 0.0
        self.last_visible_bid_wall_bonds = 0.0
        self.last_visible_bid_wall_ts_ms = 0
        self.last_bid_wall_left_book_ts_ms = 0
        self.bid_wall_currently_visible = False
        self.visible_bid_wall_first_seen_ms: dict[float, int] = {}
        self.last_legacy_reliable_reference = 0.0
        self.last_legacy_reliable_reference_ts_ms = 0
        self.legacy_breakout_support_price = 0.0
        self.legacy_breakout_support_ts_ms = 0
        self.legacy_ask_walls: dict[float, LegacyAskWall] = {}

    @property
    def enabled(self) -> bool:
        return self.config.maker_paper.enabled

    def rebuild_date(self, market_date: date | str, *, clear: bool = True) -> None:
        """Deterministically rebuild derived paper state from today's saved ticks."""
        if not self.enabled:
            return
        date_text = market_date.isoformat() if isinstance(market_date, date) else market_date
        if clear:
            self._clear_date(date_text)
        self._start_date(date_text)
        ticks = _load_ticks(
            self.store.connection, date_text,
            self.bond_code, self.stock_code,
            self.parameters,
        )
        for tick in ticks:
            self.on_replay_tick(tick, persist=True)
        self.store.app_event(
            "info", "maker_paper_rebuilt",
            "Maker paper accounts rebuilt from recorded ticks",
            {
                "market_date": date_text,
                "bond_code": self.bond_code,
                "ticks": len(ticks),
                "accounts": {
                    account.strategy_id: account.policy.model_id
                    for account in self.accounts.values()
                },
                "paper_only": True,
            },
        )

    def on_recorded_tick(self, recorded: RecordedTick) -> None:
        if not self.enabled or not recorded.is_new:
            return
        tick = recorded.tick
        multiplier = (
            self.parameters.bonds_per_qmt_hand
            if tick.code == self.bond_code else 1.0
        )
        replay = ReplayTick(
            tick_id=recorded.tick_id,
            code=tick.code,
            market_ts_ms=tick.market_ts_ms,
            market_date=tick.market_datetime.date().isoformat(),
            market_time=tick.market_datetime.time().isoformat(timespec="milliseconds"),
            last_price=tick.last_price,
            bids=tuple(
                (price, volume * multiplier)
                for price, volume in zip(tick.bid_prices, tick.bid_volumes)
                if price > 0
            ),
            asks=tuple(
                (price, volume * multiplier)
                for price, volume in zip(tick.ask_prices, tick.ask_volumes)
                if price > 0
            ),
            trade_bonds=recorded.change.volume_delta * multiplier,
            transaction_delta=recorded.change.transaction_delta,
            inferred_side=recorded.change.inferred_side,
            side_confidence=recorded.change.side_confidence,
            previous_close=tick.previous_close,
        )
        self.on_replay_tick(replay, persist=True, received_ts_ns=tick.received_ts_ns)

    def on_replay_tick(
        self, tick: ReplayTick, *, persist: bool,
        received_ts_ns: int | None = None,
    ) -> None:
        if not self.enabled:
            return
        if self.market_date != tick.market_date:
            self._start_date(tick.market_date)

        if not self.parameters.maker_session_has_started(
            tick.market_date, tick.market_time,
        ):
            if tick.code == self.bond_code:
                if tick.previous_close > 0:
                    self.previous_close_reference = tick.previous_close
                for account in self.accounts.values():
                    self._cancel_all_orders(
                        account, tick, "maker_session_not_started",
                        persist=persist,
                    )
                    self._mark_account(account, tick, persist=persist)
            return

        if tick.code == self.bond_code:
            if tick.previous_close > 0:
                self.previous_close_reference = tick.previous_close
            for account in self.accounts.values():
                self._process_resting_orders(
                    account, tick, persist=persist,
                    received_ts_ns=received_ts_ns or tick.market_ts_ms * 1_000_000,
                )

        emitted = self.analyzer.on_tick(tick)
        if tick.code != self.bond_code:
            return

        legacy_sweeps = self._legacy_sweep_opportunities(tick)
        if (
            self.analyzer.last_anchor is not None
            and self.analyzer.last_anchor.confidence + 1e-9
                >= self.parameters.minimum_anchor_confidence
        ):
            self.last_legacy_reliable_reference = (
                self.analyzer.last_anchor.reference_price
            )
            self.last_legacy_reliable_reference_ts_ms = tick.market_ts_ms

        if tick.trade_bonds > 0:
            self.observed_market_trade = True

        for account in self._standard_accounts():
            opportunities = (
                emitted
                if account.policy.enable_priority_v11_extensions
                else legacy_sweeps
            )
            for opportunity in opportunities:
                if opportunity.kind == "sweep_tail":
                    self._active_sweep(
                        account, tick, opportunity, persist=persist
                    )

        assessment = self.analyzer.assess_market(
            tick, tick.previous_close or self.previous_close_reference,
        )
        self._update_visible_bid_wall(tick)
        if (
            assessment.reference_price > 0
            and assessment.reference_source != "previous_close"
        ):
            self.previous_intraday_working_reference = (
                self.last_intraday_working_reference
            )
            self.previous_intraday_working_reference_ts_ms = (
                self.last_intraday_working_reference_ts_ms
            )
            self.last_intraday_working_reference = assessment.reference_price
            self.last_intraday_working_reference_ts_ms = tick.market_ts_ms
        previous_ask = next(
            (
                account.last_ask for account in self._standard_accounts()
                if account.last_ask > 0
            ),
            0.0,
        )
        previous_ask_bonds = next(
            (
                account.last_asks[0][1]
                for account in self._standard_accounts()
                if account.last_asks
                and account.last_asks[0][0] > 0
                and account.last_asks[0][1] > 0
            ),
            0.0,
        )
        confirmed_rise_trade = (
            tick.inferred_side == "buy"
            and tick.trade_bonds + 1e-9
                >= self.parameters.order_quantity_bonds
            and previous_ask > 0
            and tick.last_price + self.parameters.fair_price_tolerance + 1e-9
                >= previous_ask
            and tick.ask1 - previous_ask + 1e-9
                >= self.parameters.minimum_sweep_jump
        )
        if confirmed_rise_trade and assessment.state == "rising":
            self.last_confirmed_rise_trade_ts_ms = tick.market_ts_ms
            self.last_confirmed_rise_price = tick.last_price
        if (
            confirmed_rise_trade
            and assessment.state == "possible_rise"
            and any(
                account.policy.confirm_exact_offer_clear_in_possible_rise
                and (
                    not account.policy.require_exact_offer_clear_volume_coverage
                    or (
                        previous_ask_bonds > 0
                        and tick.trade_bonds + 1e-9
                            >= previous_ask_bonds
                    )
                )
                for account in self._standard_accounts()
            )
        ):
            self.last_exact_offer_clear_rise_trade_ts_ms = tick.market_ts_ms
            self.last_exact_offer_clear_rise_price = tick.last_price
        for account in self._standard_accounts():
            self._update_base_short_rising_buy_sequence(account, tick)
            stopped_confirmed_rise_short = (
                self._active_confirmed_rising_near_flat_base_short_stop(
                    account, tick, assessment, persist=persist,
                    received_ts_ns=(
                        received_ts_ns or tick.market_ts_ms * 1_000_000
                    ),
                )
            )
            restored_medium_short = False
            if not stopped_confirmed_rise_short:
                restored_medium_short = (
                    self._active_medium_base_short_replenishment(
                        account, tick, assessment, persist=persist,
                        received_ts_ns=(
                            received_ts_ns or tick.market_ts_ms * 1_000_000
                        ),
                    )
                )
            if not stopped_confirmed_rise_short and not restored_medium_short:
                self._active_discount_entry(
                    account, tick, assessment, persist=persist,
                )
        for account in self._standard_accounts():
            if account.policy.enable_priority_v11_extensions:
                self._active_profitable_turnover_exit(
                    account, tick, persist=persist,
                    received_ts_ns=(
                        received_ts_ns or tick.market_ts_ms * 1_000_000
                    ),
                )
                self._active_falling_profitable_bid_exit(
                    account, tick, assessment, persist=persist,
                    received_ts_ns=(
                        received_ts_ns or tick.market_ts_ms * 1_000_000
                    ),
                )
                self._active_inventory_risk_exit(
                    account, tick, assessment, persist=persist,
                    received_ts_ns=(
                        received_ts_ns or tick.market_ts_ms * 1_000_000
                    ),
                )

        for account in self.accounts.values():
            if tick.bid1 <= 0 or tick.ask1 <= tick.bid1:
                self._cancel_all_orders(
                    account, tick, "invalid_book", persist=persist
                )
                self._mark_account(account, tick, persist=persist)
                continue
            if account.purpose == "super_windfall":
                self._refresh_super_windfall(
                    account, tick, assessment, persist=persist,
                )
            else:
                self._refresh_orders(
                    account, tick, assessment, persist=persist,
                )
            self._mark_account(account, tick, persist=persist)

    def _standard_accounts(self) -> tuple[MakerAccount, ...]:
        return tuple(
            account for account in self.accounts.values()
            if account.purpose == "standard"
        )

    def _entry_window_for_policy(
        self, market_time: str, policy: MakerPolicyProfile,
        market_date: str | None = None,
    ) -> bool:
        """Apply the registered model's immutable trading window."""

        latest_entry = min(
            policy.latest_entry_time,
            self.parameters.latest_entry_time,
        )
        if not (
            self.parameters.effective_earliest_entry_time(
                market_date or self.market_date,
            )
            <= market_time
            <= latest_entry
        ):
            return False
        return not ("11:30:00.001" <= market_time < "13:00:00.000")

    def _clear_date(self, market_date: str) -> None:
        strategy_ids = [
            self.strategy_ids_by_mode.get(
                mode, f"{self.strategy_prefix}_{mode}"
            )
            for mode in self.fill_modes
        ]
        if self.include_windfall:
            strategy_ids.append(f"{self.strategy_prefix}_super_windfall")
        if not strategy_ids:
            return
        placeholders = ",".join("?" for _ in strategy_ids)
        for table in (
            "maker_paper_fills", "maker_paper_orders", "maker_paper_lots",
            "maker_paper_accounts", "maker_paper_model_assignments",
        ):
            self.store.connection.execute(
                f"DELETE FROM {table} WHERE market_date=? "
                f"AND strategy_id IN ({placeholders})",
                (market_date, *strategy_ids),
            )
        self.store.connection.commit()

    def _start_date(self, market_date: str) -> None:
        self.market_date = market_date
        self.analyzer = MakerAnalyzer(
            self.bond_code, self.stock_code, self.parameters
        )
        self.accounts = {}
        self.previous_close_reference = 0.0
        self.observed_market_trade = False
        self.last_confirmed_rise_trade_ts_ms = 0
        self.last_confirmed_rise_price = 0.0
        self.last_exact_offer_clear_rise_trade_ts_ms = 0
        self.last_exact_offer_clear_rise_price = 0.0
        self.last_intraday_working_reference = 0.0
        self.last_intraday_working_reference_ts_ms = 0
        self.previous_intraday_working_reference = 0.0
        self.previous_intraday_working_reference_ts_ms = 0
        self.last_visible_bid_wall_price = 0.0
        self.last_visible_bid_wall_bonds = 0.0
        self.last_visible_bid_wall_ts_ms = 0
        self.last_bid_wall_left_book_ts_ms = 0
        self.bid_wall_currently_visible = False
        self.visible_bid_wall_first_seen_ms = {}
        self.last_legacy_reliable_reference = 0.0
        self.last_legacy_reliable_reference_ts_ms = 0
        self.legacy_breakout_support_price = 0.0
        self.legacy_breakout_support_ts_ms = 0
        self.legacy_ask_walls = {}
        paper = self.config.maker_paper
        for mode in self.fill_modes:
            strategy_id = self.strategy_ids_by_mode.get(
                mode, f"{self.strategy_prefix}_{mode}"
            )
            if mode == "priority":
                policy = self.priority_policy
            elif mode == "queue":
                policy = self.queue_policy
            else:
                policy = maker_policy_for_mode(mode)
            account = MakerAccount(
                market_date=market_date,
                bond_code=self.bond_code,
                strategy_id=strategy_id,
                fill_mode=mode,
                policy=policy,
                initial_inventory=paper.initial_inventory_bonds,
                maximum_inventory=paper.maximum_inventory_bonds,
                initial_cash=paper.initial_cash_cny,
                cash=paper.initial_cash_cny,
                inventory=paper.initial_inventory_bonds,
                additional_buying_capacity=(
                    paper.additional_buying_capacity_bonds
                ),
            )
            lot_id = self.store.insert_maker_lot({
                "run_id": self.store.run_id,
                "market_date": market_date,
                "strategy_id": strategy_id,
                "kind": "base",
                "opened_market_ts_ms": 0,
                "entry_price": None,
                "original_quantity": paper.initial_inventory_bonds,
                "remaining_quantity": paper.initial_inventory_bonds,
                "target_price": None,
                "status": "open",
                "updated_market_ts_ms": 0,
            })
            account.lots[lot_id] = MakerLot(
                lot_id, "base", 0, None,
                paper.initial_inventory_bonds, paper.initial_inventory_bonds,
            )
            self.accounts[strategy_id] = account
            self._persist_model_assignment(account)
            self._persist_account(account)
        if self.include_windfall:
            strategy_id = f"{self.strategy_prefix}_super_windfall"
            policy = self.windfall_policy
            account = MakerAccount(
                market_date=market_date,
                bond_code=self.bond_code,
                strategy_id=strategy_id,
                fill_mode="windfall",
                policy=policy,
                initial_inventory=0.0,
                maximum_inventory=paper.super_windfall_quantity_bonds,
                initial_cash=paper.super_windfall_credit_cny,
                cash=paper.super_windfall_credit_cny,
                inventory=0.0,
                purpose="super_windfall",
            )
            self.accounts[strategy_id] = account
            self._persist_model_assignment(account)
            self._persist_account(account)

    def _process_resting_orders(
        self, account: MakerAccount, tick: ReplayTick, *, persist: bool,
        received_ts_ns: int,
    ) -> None:
        if tick.trade_bonds <= 0:
            return
        available = tick.trade_bonds
        effective_side = tick.inferred_side
        book_side = self._priority_book_trade_side(account, tick)
        corrected_buy_order = (
            effective_side == "buy"
            and book_side == "sell"
            and account.buy_order is not None
            and tick.last_price <= account.buy_order.limit_price + 1e-9
        )
        corrected_sell_order = (
            effective_side == "sell"
            and book_side == "buy"
            and any(
                tick.last_price + 1e-9 >= order.limit_price
                for order in account.sell_orders.values()
            )
        )
        side_corrected = corrected_buy_order or corrected_sell_order
        if corrected_buy_order:
            effective_side = "sell"
        elif corrected_sell_order:
            effective_side = "buy"
        if effective_side in {"buy", "unknown"}:
            high_cluster_prices = [
                order.limit_price
                for order in account.sell_orders.values()
                if order.kind == "high_ask_cluster_base_preposition"
                and tick.last_price + 1e-9 >= order.limit_price
            ]
            if high_cluster_prices and account.last_asks:
                # A cumulative Level-1 frame can combine the removal of a
                # lower ask with prints at the pre-positioned upper cluster.
                # Only the residual volume after the visibly consumed lower
                # levels may fill the upper order.  This is deliberately tied
                # to the v1.40 order identity so earlier execution branches
                # retain their frozen historical assumptions.
                cluster_price = min(high_cluster_prices)
                current_asks = {
                    round(price, 6): bonds for price, bonds in tick.asks
                }
                visibly_consumed_below = sum(
                    max(
                        0.0,
                        previous_bonds
                            - current_asks.get(round(previous_price, 6), 0.0),
                    )
                    for previous_price, previous_bonds in account.last_asks
                    if previous_price < cluster_price - 1e-9
                )
                available = max(0.0, available - visibly_consumed_below)
            sell_orders = sorted(
                list(account.sell_orders.items()),
                key=lambda item: (
                    item[1].limit_price,
                    account.lots[item[0]].kind == "base",
                    item[1].created_ms,
                ),
            )
            if account.policy.share_simultaneous_same_price_queue:
                available = self._process_shared_queue_sell_orders(
                    account, tick, sell_orders, available,
                    received_ts_ns=received_ts_ns, persist=persist,
                )
            else:
                for lot_id, order in sell_orders:
                    if (
                        available <= 1e-9
                        or tick.last_price + 1e-9 < order.limit_price
                    ):
                        continue
                    available = self._consume_queue(
                        order, available, account.fill_mode,
                        market_ts_ms=tick.market_ts_ms,
                        crossed_book=tick.bid1 + 1e-9 >= order.limit_price,
                    )
                    quantity = min(available, order.remaining)
                    if quantity <= 1e-9:
                        continue
                    self._fill_sell(
                        account, tick, order, quantity, received_ts_ns,
                        persist=persist,
                        reason=(
                            "priority_book_side_passive_sell"
                            if side_corrected else "passive_sell"
                        ),
                    )
                    available -= quantity

        if (
            available > 1e-9
            and effective_side in {"sell", "unknown"}
            and account.buy_order is not None
            and tick.last_price <= account.buy_order.limit_price + 1e-9
        ):
            order = account.buy_order
            clean_exact_queue_clear = self._clean_exact_queue_clear(
                account, tick, order, available,
                external_queue=order.queue_ahead,
            )
            available = self._consume_queue(
                order, available, account.fill_mode,
                market_ts_ms=tick.market_ts_ms,
                crossed_book=(
                    tick.ask1 > 0
                    and tick.ask1 <= order.limit_price + 1e-9
                ),
                price_penetrated=(
                    tick.last_price
                    < order.limit_price - self.parameters.price_tick / 2
                ),
                waive_exact_fill_buffer_on_queue_clear=(
                    clean_exact_queue_clear
                ),
            )
            capacity = max(0.0, account.maximum_inventory - account.inventory)
            affordable = self._affordable_buy_bonds(
                account, order.limit_price,
            )
            quantity = min(available, order.remaining, capacity, affordable)
            if quantity > 1e-9:
                self._fill_buy(
                    account, tick, order, quantity, received_ts_ns,
                    kind=order.kind, target_price=order.target_price,
                    persist=persist,
                    reason=(
                        "super_windfall_buy"
                        if order.kind == "super_windfall"
                        else (
                            "priority_book_side_passive_buy"
                            if side_corrected else "passive_buy"
                        )
                    ),
                )
                available -= quantity

        self._fill_queue_cleared_crossed_book_residual(
            account, tick, received_ts_ns=received_ts_ns, persist=persist,
        )
        self._fill_recent_cleared_queue_trade(
            account, tick, available=available,
            received_ts_ns=received_ts_ns, persist=persist,
        )

    @staticmethod
    def _priority_book_trade_side(
        account: MakerAccount, tick: ReplayTick,
    ) -> str:
        """Return a strong book-side correction for a priority fill only."""

        policy = account.policy
        if not (
            policy.enable_priority_book_side_fill_correction
            and account.fill_mode == "priority"
            and tick.bid1 > 0
            and tick.ask1 > tick.bid1
            and tick.last_price > 0
        ):
            return "none"
        if tick.last_price <= tick.bid1:
            return "sell"
        if tick.last_price >= tick.ask1:
            return "buy"
        distance_to_bid = tick.last_price - tick.bid1
        distance_to_ask = tick.ask1 - tick.last_price
        advantage = policy.minimum_book_side_distance_advantage
        if distance_to_ask - distance_to_bid + 1e-9 >= advantage:
            return "sell"
        if distance_to_bid - distance_to_ask + 1e-9 >= advantage:
            return "buy"
        return "none"

    def _process_shared_queue_sell_orders(
        self, account: MakerAccount, tick: ReplayTick,
        sell_orders: list[tuple[int, MakerOrder]], available: float, *,
        received_ts_ns: int, persist: bool,
    ) -> float:
        """Consume one external queue for a simultaneous same-price batch.

        The strategy keeps inventory in separate internal lots, so one market
        quote can create several model sell rows at the same price and market
        timestamp.  They represent one combined exchange queue position, not
        several copies of the displayed external quantity.  Later arrivals at
        the same price retain their own timestamp and therefore remain a
        separate queue cohort.
        """

        cohort_map: dict[
            tuple[float, int], list[tuple[int, MakerOrder]]
        ] = {}
        for item in sell_orders:
            order = item[1]
            key = (round(order.limit_price, 6), order.created_ms)
            cohort_map.setdefault(key, []).append(item)
        cohorts = [cohort_map[key] for key in sorted(cohort_map)]

        for cohort in cohorts:
            if available <= 1e-9:
                break
            first_order = cohort[0][1]
            if tick.last_price + 1e-9 < first_order.limit_price:
                continue
            external_queue = max(order.queue_ahead for _, order in cohort)
            clean_exact_queue_clear = self._clean_exact_queue_clear(
                account, tick, first_order, available,
                external_queue=external_queue,
            )
            consumed = min(external_queue, available)
            remaining_queue = external_queue - consumed
            available -= consumed
            for _, order in cohort:
                previous_queue = order.queue_ahead
                order.queue_ahead = remaining_queue
                if previous_queue > 1e-9 and remaining_queue <= 1e-9:
                    order.queue_cleared_ms = tick.market_ts_ms
                    order.queue_cleared_crossed_book = (
                        tick.bid1 + 1e-9 >= order.limit_price
                    )
            if remaining_queue > 1e-9:
                continue
            price_penetrated = (
                tick.last_price
                > first_order.limit_price + self.parameters.price_tick / 2
            )
            if price_penetrated:
                for _, order in cohort:
                    order.exact_fill_uncertainty_buffer = 0.0
            else:
                if clean_exact_queue_clear:
                    for _, order in cohort:
                        if self._is_inventory_turn_buffer_order(
                            account, order,
                        ):
                            order.exact_fill_uncertainty_buffer = 0.0
                uncertainty_buffer = max(
                    order.exact_fill_uncertainty_buffer
                    for _, order in cohort
                )
                consumed_buffer = min(uncertainty_buffer, available)
                available -= consumed_buffer
                remaining_buffer = uncertainty_buffer - consumed_buffer
                for _, order in cohort:
                    order.exact_fill_uncertainty_buffer = remaining_buffer
            if available <= 1e-9:
                continue
            for _, order in cohort:
                quantity = min(available, order.remaining)
                if quantity <= 1e-9:
                    continue
                self._fill_sell(
                    account, tick, order, quantity, received_ts_ns,
                    persist=persist,
                )
                available -= quantity
                if available <= 1e-9:
                    break
        return available

    def _fill_queue_cleared_crossed_book_residual(
        self, account: MakerAccount, tick: ReplayTick, *,
        received_ts_ns: int, persist: bool,
    ) -> None:
        """Fill from the displayed contra residual after a queue clears.

        The paper order is absent from the observed exchange book.  If its
        external queue is consumed in this frame and the resulting best
        contra quote still crosses the model limit, that displayed quantity
        could not have remained there in the counterfactual book: it would
        have matched the already-resting model order first.  This capacity is
        distinct from ``trade_bonds`` and is shared once across same-side
        internal orders.
        """

        if not (
            account.fill_mode == "queue"
            and account.policy.fill_queue_cleared_crossed_book_residual
        ):
            return

        sell_capacity = (
            tick.bid1_bonds if tick.bid1 > 0 else 0.0
        )
        if sell_capacity > 1e-9:
            sell_orders = sorted(
                list(account.sell_orders.items()),
                key=lambda item: (
                    item[1].limit_price,
                    account.lots[item[0]].kind == "base",
                    item[1].created_ms,
                ),
            )
            for _, order in sell_orders:
                if sell_capacity <= 1e-9:
                    break
                if not (
                    order.created_ms < tick.market_ts_ms
                    and order.queue_cleared_ms == tick.market_ts_ms
                    and order.queue_cleared_crossed_book
                    and order.queue_ahead <= 1e-9
                    and order.exact_fill_uncertainty_buffer <= 1e-9
                    and tick.bid1 + 1e-9 >= order.limit_price
                ):
                    continue
                requested = min(sell_capacity, order.remaining)
                if requested <= 1e-9:
                    continue
                before = order.filled_quantity
                self._fill_sell(
                    account, tick, order, requested, received_ts_ns,
                    persist=persist,
                    reason="queue_cleared_crossed_residual_fill",
                )
                sell_capacity -= max(
                    0.0, order.filled_quantity - before,
                )

        order = account.buy_order
        if order is None:
            return
        if not (
            order.created_ms < tick.market_ts_ms
            and order.queue_cleared_ms == tick.market_ts_ms
            and order.queue_cleared_crossed_book
            and order.queue_ahead <= 1e-9
            and order.exact_fill_uncertainty_buffer <= 1e-9
            and tick.ask1 > 0
            and tick.ask1 <= order.limit_price + 1e-9
            and tick.ask1_bonds > 1e-9
        ):
            return
        capacity = max(0.0, account.maximum_inventory - account.inventory)
        affordable = self._affordable_buy_bonds(account, order.limit_price)
        quantity = min(
            tick.ask1_bonds, order.remaining, capacity, affordable,
        )
        if quantity > 1e-9:
            self._fill_buy(
                account, tick, order, quantity, received_ts_ns,
                kind=order.kind, target_price=order.target_price,
                persist=persist,
                reason="queue_cleared_crossed_residual_fill",
            )

    def _fill_recent_cleared_queue_trade(
        self, account: MakerAccount, tick: ReplayTick, *,
        available: float, received_ts_ns: int, persist: bool,
    ) -> None:
        """Use a same-price next-frame print after a crossed queue clears.

        The normal Level 1 inference can label the next print on the wrong
        aggressor side.  When the previous frame both consumed the full queue
        ahead and showed the opposite quote at our limit, retain the order for
        at most one three-second frame.  A same-price print in that window can
        fill it even if ``inferred_side`` conflicts; no post-close TDX label is
        read by the replay.
        """

        grace_seconds = (
            account.policy.queue_cleared_position_one_tick_grace_seconds
        )
        if account.fill_mode != "queue" or grace_seconds <= 0:
            return

        for _, order in sorted(
            list(account.sell_orders.items()),
            key=lambda item: (
                item[1].limit_price,
                account.lots[item[0]].kind == "base",
                item[1].created_ms,
            ),
        ):
            elapsed = tick.market_ts_ms - order.queue_cleared_ms
            if not (
                order.queue_cleared_crossed_book
                and 0 < elapsed <= grace_seconds * 1_000
                and order.queue_ahead <= 1e-9
                and order.exact_fill_uncertainty_buffer <= 1e-9
                and tick.inferred_side == "sell"
                and abs(tick.last_price - order.limit_price) <= 1e-9
            ):
                continue
            quantity = min(available, order.remaining)
            if quantity > 1e-9:
                self._fill_sell(
                    account, tick, order, quantity, received_ts_ns,
                    persist=persist, reason="queue_cleared_next_frame_fill",
                )
                return

        order = account.buy_order
        if order is None:
            return
        elapsed = tick.market_ts_ms - order.queue_cleared_ms
        if not (
            order.queue_cleared_crossed_book
            and 0 < elapsed <= grace_seconds * 1_000
            and order.queue_ahead <= 1e-9
            and order.exact_fill_uncertainty_buffer <= 1e-9
            and tick.inferred_side == "buy"
            and abs(tick.last_price - order.limit_price) <= 1e-9
        ):
            return
        capacity = max(0.0, account.maximum_inventory - account.inventory)
        affordable = self._affordable_buy_bonds(account, order.limit_price)
        quantity = min(available, order.remaining, capacity, affordable)
        if quantity > 1e-9:
            self._fill_buy(
                account, tick, order, quantity, received_ts_ns,
                kind=order.kind, target_price=order.target_price,
                persist=persist, reason="queue_cleared_next_frame_fill",
            )

    @staticmethod
    def _consume_queue(
        order: MakerOrder, available: float, fill_mode: str, *,
        market_ts_ms: int = 0,
        crossed_book: bool = False,
        price_penetrated: bool = False,
        waive_exact_fill_buffer_on_queue_clear: bool = False,
    ) -> float:
        if fill_mode != "queue":
            return available
        if price_penetrated and order.exact_fill_uncertainty_buffer > 1e-9:
            order.exact_fill_uncertainty_buffer = 0.0
        if order.queue_ahead > 1e-9:
            previous_queue = order.queue_ahead
            consumed = min(order.queue_ahead, available)
            order.queue_ahead -= consumed
            available -= consumed
            if previous_queue > 1e-9 and order.queue_ahead <= 1e-9:
                order.queue_cleared_ms = market_ts_ms
                order.queue_cleared_crossed_book = crossed_book
                if waive_exact_fill_buffer_on_queue_clear:
                    order.exact_fill_uncertainty_buffer = 0.0
        if available > 1e-9 and order.exact_fill_uncertainty_buffer > 1e-9:
            consumed = min(order.exact_fill_uncertainty_buffer, available)
            order.exact_fill_uncertainty_buffer -= consumed
            available -= consumed
        return available

    @staticmethod
    def _visible_quantity_at_price(
        book: tuple[tuple[float, float], ...], price: float,
    ) -> float:
        return sum(
            quantity for level_price, quantity in book
            if abs(level_price - price) <= 1e-9
        )

    @staticmethod
    def _is_inventory_turn_buffer_order(
        account: MakerAccount, order: MakerOrder,
    ) -> bool:
        if order.kind == "inventory_turn_replenish":
            return True
        lot = account.lots.get(order.lot_id) if order.lot_id is not None else None
        return (
            order.inventory_neutral_downtrend_turn
            or (lot is not None and lot.kind == "inventory_turn_replenish")
        )

    def _clean_exact_queue_clear(
        self, account: MakerAccount, tick: ReplayTick, order: MakerOrder,
        available: float, *, external_queue: float,
    ) -> bool:
        """Return whether one exact-price frame cleanly clears visible queue.

        The inventory-turn buffer protects the queue replay when a three-second
        Level 1 volume increment mixes trades from other prices or from the
        opposite side.  It should not reserve another standard lot after a
        frame is fully explained by an equal reduction of the visible queue at
        our exact limit.  That clean depletion proves the displayed queue ahead
        was consumed; later same-price volume reaches our established position
        before additions that arrived after it.

        Only the preceding and current Level 1 books are used here.  Post-close
        TDX aggressor labels remain audit evidence and never enter the replay.
        """

        if not (
            account.fill_mode == "queue"
            and account.policy
                .waive_inventory_turn_buffer_on_clean_exact_queue_clear
            and order.exact_fill_uncertainty_buffer > 1e-9
            and external_queue > 1e-9
            and available + 1e-9 >= external_queue
            and abs(tick.last_price - order.limit_price) <= 1e-9
            and tick.transaction_delta == 1
            and self._is_inventory_turn_buffer_order(account, order)
        ):
            return False
        previous_book = (
            account.last_asks if order.side == "sell" else account.last_bids
        )
        current_book = tick.asks if order.side == "sell" else tick.bids
        previous_quantity = self._visible_quantity_at_price(
            previous_book, order.limit_price,
        )
        current_quantity = self._visible_quantity_at_price(
            current_book, order.limit_price,
        )
        if previous_quantity <= 1e-9:
            return False
        visible_depletion = max(0.0, previous_quantity - current_quantity)
        return (
            visible_depletion > 1e-9
            and abs(visible_depletion - available) <= 1e-9
        )

    def _legacy_sweep_opportunities(
        self, tick: ReplayTick,
    ) -> tuple[Opportunity, ...]:
        """Reconstruct the 1.0 single-price wall sweep for queue accounts.

        Priority 1.1 groups adjacent legal prices and supports an additional
        thin-cluster pattern. Queue 1.0 instead remembers and validates one
        exact displayed ask price, matching the pre-2026-08-14 execution
        model and avoiding automatic inheritance from the priority branch.
        """
        parameters = self.parameters
        now_ms = tick.market_ts_ms
        cutoff = now_ms - parameters.wall_memory_seconds * 1000
        self.legacy_ask_walls = {
            price: wall for price, wall in self.legacy_ask_walls.items()
            if wall.last_seen_ms >= cutoff
        }
        visible = {
            round(price, 6): bonds for price, bonds in tick.asks if price > 0
        }
        trade_price = round(tick.last_price, 6)
        if tick.trade_bonds > 0 and tick.inferred_side == "buy":
            wall = self.legacy_ask_walls.get(trade_price)
            if wall is not None:
                wall.aggressive_buys.append((now_ms, tick.trade_bonds))
        for price, bonds in visible.items():
            wall = self.legacy_ask_walls.get(price)
            if wall is None:
                self.legacy_ask_walls[price] = LegacyAskWall(
                    price, now_ms, now_ms, bonds, bonds,
                )
                continue
            wall.last_seen_ms = now_ms
            wall.current_bonds = bonds
            wall.peak_bonds = max(wall.peak_bonds, bonds)

        emitted: list[Opportunity] = []
        rapid_cutoff = (
            now_ms - parameters.sweep_consumption_window_seconds * 1000
        )
        for price, wall in self.legacy_ask_walls.items():
            while (
                wall.aggressive_buys
                and wall.aggressive_buys[0][0] < rapid_cutoff
            ):
                wall.aggressive_buys.popleft()
            if wall.emitted or price not in visible:
                continue
            current = visible[price]
            rapid_buys = sum(quantity for _, quantity in wall.aggressive_buys)
            consumed = min(
                rapid_buys, max(0.0, wall.peak_bonds - current)
            )
            consumed_ratio = (
                consumed / wall.peak_bonds if wall.peak_bonds > 0 else 0.0
            )
            planned_quantity = min(
                parameters.order_quantity_bonds, current
            )
            minimum_source = max(
                parameters.minimum_sweep_source_bonds,
                parameters.minimum_sweep_source_multiple * planned_quantity,
            )
            higher_asks = sorted(
                ask_price for ask_price in visible if ask_price > price + 1e-9
            )
            next_ask = higher_asks[0] if higher_asks else 0.0
            jump = next_ask - price if next_ask > 0 else 0.0
            if not (
                self.analyzer._entry_window(
                    tick.market_time, tick.market_date,
                )
                and planned_quantity > 0
                and wall.peak_bonds + 1e-9 >= minimum_source
                and consumed_ratio + 1e-9
                    >= parameters.minimum_sweep_consumed_ratio
                and current <= parameters.maximum_sweep_tail_bonds + 1e-9
                and jump + 1e-9 >= parameters.minimum_sweep_jump
            ):
                continue
            anchor = self.analyzer.last_anchor
            if anchor is None or not self.analyzer._sweep_temperature_supportive(
                price, rapid_buys, now_ms,
            ):
                continue
            first_trade_ms = (
                wall.aggressive_buys[0][0]
                if wall.aggressive_buys else now_ms
            )
            priority_exit = max(
                price + parameters.price_tick,
                next_ask - parameters.price_tick,
            )
            emitted.append(Opportunity(
                kind="sweep_tail",
                signal_ts_ms=now_ms,
                market_time=tick.market_time,
                entry_price=price,
                quantity_bonds=planned_quantity,
                target_exit_price=next_ask,
                priority_exit_price=priority_exit,
                theoretical_edge=priority_exit - price,
                anchor=anchor,
                source_wall_bonds=wall.peak_bonds,
                consumed_bonds=consumed,
                consumed_ratio=consumed_ratio,
                consumption_seconds=(now_ms - first_trade_ms) / 1000,
                tail_bonds=current,
                next_ask_price=next_ask,
                notes=(
                    "legacy_single_price_wall_tail_consumption",
                    "active_tail_sweep_uses_current_level1_snapshot",
                ),
            ))
            active_support = (
                self.legacy_breakout_support_price
                if now_ms - self.legacy_breakout_support_ts_ms
                    <= parameters.breakout_support_seconds * 1000
                else 0.0
            )
            self.legacy_breakout_support_price = max(active_support, price)
            self.legacy_breakout_support_ts_ms = now_ms
            wall.emitted = True
        merged: dict[float, Opportunity] = {}
        for opportunity in emitted:
            execution_price = _floor_to_tick(
                opportunity.entry_price, parameters.price_tick
            )
            existing = merged.get(execution_price)
            if existing is None:
                opportunity.entry_price = execution_price
                merged[execution_price] = opportunity
                continue
            existing.quantity_bonds = min(
                parameters.order_quantity_bonds,
                existing.quantity_bonds + opportunity.quantity_bonds,
            )
            existing.tail_bonds = (
                (existing.tail_bonds or 0.0)
                + (opportunity.tail_bonds or 0.0)
            )
            existing.source_wall_bonds = max(
                existing.source_wall_bonds or 0.0,
                opportunity.source_wall_bonds or 0.0,
            )
            existing.consumed_bonds = max(
                existing.consumed_bonds or 0.0,
                opportunity.consumed_bonds or 0.0,
            )
        return tuple(merged.values())

    def _active_sweep(
        self, account: MakerAccount, tick: ReplayTick,
        opportunity: Opportunity, *, persist: bool,
    ) -> None:
        if not self._entry_window_for_policy(
            tick.market_time, account.policy, tick.market_date,
        ):
            return
        if not self.parameters.opening_edge_is_safe(
            tick.market_date,
            tick.market_time,
            opportunity.theoretical_edge,
        ):
            return
        if (
            "immediate_visible_cluster_tail_consumption" in opportunity.notes
            and not account.policy
                .enable_immediate_visible_cluster_tail_recovery
        ):
            return
        capacity = max(0.0, account.maximum_inventory - account.inventory)
        # A wall-consumption breakout establishes the swept price as support.
        # Chasing that support is only for restoring a base-inventory deficit;
        # it must not turn a full base position into an extra high-cost lot.
        if (
            account.policy.enable_priority_v11_extensions
            and
            opportunity.entry_price + self.parameters.fair_price_tolerance
            >= opportunity.anchor.reference_price
            and opportunity.theoretical_edge + 1e-9
                < self.parameters.minimum_thin_sweep_jump
        ):
            capacity = min(
                capacity,
                max(0.0, account.initial_inventory - account.inventory),
            )
        affordable = self._affordable_buy_bonds(
            account, opportunity.entry_price,
        )
        quantity = min(opportunity.quantity_bonds, capacity, affordable)
        if quantity <= 1e-9:
            return
        order = self._new_order(
            account, tick, side="buy", kind="sweep_tail", lot_id=None,
            price=opportunity.entry_price, quantity=quantity, queue_ahead=0.0,
            target_price=opportunity.priority_exit_price,
            price_boundary=opportunity.entry_price, persist=persist,
        )
        self._fill_buy(
            account, tick, order, quantity, tick.market_ts_ms * 1_000_000,
            kind="sweep_tail", target_price=opportunity.priority_exit_price,
            persist=persist, reason="active_tail_sweep",
        )

    def _active_medium_base_short_replenishment(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, *, persist: bool,
        received_ts_ns: int,
    ) -> bool:
        """Restore a moderate customer-base short when its old bid is stale.

        This is deliberately narrower than a general stop-profit or forced
        close.  It applies only when every currently outstanding base deficit
        came from a 0.30--0.50 yuan, wall-supported high sale.  A deep high
        sale and a repeated executable corridor retain their original plans.
        The tape must no longer be falling, ask1 must already lock in the
        ordinary 0.20-yuan edge, and the resting replenishment bid must be at
        least the existing 1.00-yuan windfall-gap threshold below ask1.
        """

        policy = account.policy
        order = account.buy_order
        deficit = max(0.0, account.initial_inventory - account.inventory)
        qualified = account.medium_wall_supported_replenishment_quantity
        recent_probe_sequence = (
            policy.enable_profitable_visible_bid_base_replenishment
            and account.last_profitable_visible_bid_replenishment_ts_ms > 0
            and account.last_base_short_sale_ts_ms
                > account.last_profitable_visible_bid_replenishment_ts_ms
            and 0 < tick.market_ts_ms
                - account.last_profitable_visible_bid_replenishment_ts_ms
                <= 300_000
        )
        if not (
            policy.enable_dynamic_medium_base_short_replenishment
            and account.fill_mode == "priority"
            and assessment.state in {"stable", "possible_rise", "rising"}
            and deficit > 1e-9
            and account.replenishment_quantity > 1e-9
            and qualified + 1e-9 >= account.replenishment_quantity
            and order is not None
            and order.side == "buy"
            and order.kind in {
                "inventory_replenish",
                "profitable_visible_bid_base_replenish",
                "dynamic_customer_base_replenish",
            }
            and order.remaining > 1e-9
            and tick.ask1 > tick.bid1 > 0
            and tick.ask1_bonds > 1e-9
        ):
            return False
        average_sale_price = (
            account.replenishment_sale_value
            / account.replenishment_quantity
        )
        if (
            average_sale_price - tick.ask1 + 1e-9
                < self.parameters.minimum_entry_edge
            or (
                not recent_probe_sequence
                and tick.ask1 - order.limit_price + 1e-9
                    < self.parameters.minimum_windfall_book_gap
            )
        ):
            return False
        capacity = max(0.0, account.maximum_inventory - account.inventory)
        affordable = self._affordable_buy_bonds(account, tick.ask1)
        quantity = min(
            deficit,
            qualified,
            self.parameters.order_quantity_bonds,
            tick.ask1_bonds,
            capacity,
            affordable,
        )
        if quantity <= 1e-9:
            return False
        self._cancel_order(
            account, order, tick,
            "dynamic_medium_base_short_replenishment", persist,
        )
        active_order = self._new_order(
            account, tick, side="buy", kind="inventory_replenish",
            lot_id=None, price=tick.ask1, quantity=quantity,
            queue_ahead=0.0, target_price=None,
            price_boundary=(
                average_sale_price - self.parameters.minimum_entry_edge
            ),
            persist=persist,
        )
        self._fill_buy(
            account, tick, active_order, quantity, received_ts_ns,
            kind="inventory_replenish", target_price=None, persist=persist,
            reason="active_medium_base_short_replenishment",
        )
        return True

    def _active_confirmed_rising_near_flat_base_short_stop(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, *, persist: bool,
        received_ts_ns: int,
    ) -> bool:
        """Restore a customer-base short after its high-sale thesis fails.

        A base sale is an economic short even though technical inventory never
        becomes negative.  A full-sized active buy at the current offer, a
        genuinely tight book and a ``rising`` assessment together provide a
        causal invalidation signal.  When the base can still be restored within
        the existing near-flat loss allowance, stop the short immediately.

        This is neither a close-of-day flattening rule nor permission to chase
        a wide market.  It applies to every base-short origin, uses only the
        current visible offer capacity, and is enabled only by an explicit
        priority profile.
        """

        policy = account.policy
        deficit = max(0.0, account.initial_inventory - account.inventory)
        if not (
            (
                policy.enable_confirmed_rising_near_flat_base_short_stop
                or policy.enable_confirmed_rising_buy_sequence_base_short_stop
            )
            and account.fill_mode == "priority"
            and assessment.state == "rising"
            and deficit > 1e-9
            and account.replenishment_quantity > 1e-9
            and account.last_base_short_sale_ts_ms > 0
            and tick.inferred_side == "buy"
            and tick.trade_bonds + 1e-9
                >= self.parameters.order_quantity_bonds
            and tick.ask1 > tick.bid1 > 0
            and tick.ask1 - tick.bid1
                <= self.parameters.maximum_active_turnover_spread + 1e-9
            and tick.ask1_bonds > 1e-9
            and tick.last_price + self.parameters.fair_price_tolerance + 1e-9
                >= tick.ask1
        ):
            return False
        average_sale_price = (
            account.replenishment_sale_value
            / account.replenishment_quantity
        )
        age_ms = tick.market_ts_ms - account.last_base_short_sale_ts_ms
        ordinary_confirmation = (
            policy.enable_confirmed_rising_near_flat_base_short_stop
            and 0 < age_ms
                <= policy.confirmed_rising_base_short_stop_seconds * 1_000
            and assessment.reference_price
                + self.parameters.fair_price_tolerance + 1e-9
                >= average_sale_price
        )
        buy_sequence_confirmation = (
            policy.enable_confirmed_rising_buy_sequence_base_short_stop
            and 0 < age_ms
                <= policy.confirmed_rising_buy_sequence_base_short_stop_seconds
                    * 1_000
            and account.base_short_rising_buy_sequence_bonds + 1e-9
                >= self.parameters.minimum_anchor_bonds
        )
        if (
            not (ordinary_confirmation or buy_sequence_confirmation)
            or tick.ask1 - average_sale_price
                > self.parameters.maximum_near_flat_exit_loss + 1e-9
        ):
            return False
        capacity = max(0.0, account.maximum_inventory - account.inventory)
        affordable = self._affordable_buy_bonds(account, tick.ask1)
        quantity = min(
            deficit,
            account.replenishment_quantity,
            self.parameters.order_quantity_bonds,
            tick.ask1_bonds,
            capacity,
            affordable,
        )
        if quantity <= 1e-9:
            return False
        stop_reason = (
            "confirmed_rising_buy_sequence_base_short_stop"
            if buy_sequence_confirmation and not ordinary_confirmation
            else "confirmed_rising_near_flat_base_short_stop"
        )
        if account.buy_order is not None:
            self._cancel_order(
                account, account.buy_order, tick,
                stop_reason, persist,
            )
        active_order = self._new_order(
            account, tick, side="buy", kind="inventory_replenish",
            lot_id=None, price=tick.ask1, quantity=quantity,
            queue_ahead=0.0, target_price=None,
            price_boundary=(
                average_sale_price + self.parameters.maximum_near_flat_exit_loss
            ),
            persist=persist,
        )
        self._fill_buy(
            account, tick, active_order, quantity, received_ts_ns,
            kind="inventory_replenish", target_price=None, persist=persist,
            reason=(
                "active_confirmed_rising_buy_sequence_base_short_stop"
                if buy_sequence_confirmation and not ordinary_confirmation
                else "active_confirmed_rising_base_short_stop"
            ),
        )
        return True

    def _update_base_short_rising_buy_sequence(
        self, account: MakerAccount, tick: ReplayTick,
    ) -> None:
        """Track only uninterrupted post-sale real buying for v1.33.

        The evidence is causal and branch-local.  A sell print invalidates the
        sequence immediately; quote changes and no-trade frames neither add nor
        remove evidence.  The active stop applies its own sale-age limit.
        """

        if not account.policy.enable_confirmed_rising_buy_sequence_base_short_stop:
            return
        if (
            account.fill_mode != "priority"
            or account.customer_base_short_bonds <= 1e-9
            or account.last_base_short_sale_ts_ms <= 0
            or tick.market_ts_ms <= account.last_base_short_sale_ts_ms
        ):
            account.base_short_rising_buy_sequence_bonds = 0.0
            return
        if tick.trade_bonds <= 1e-9:
            return
        if tick.inferred_side == "sell":
            account.base_short_rising_buy_sequence_bonds = 0.0
        elif tick.inferred_side == "buy":
            account.base_short_rising_buy_sequence_bonds += tick.trade_bonds

    def _active_discount_entry(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, *, persist: bool,
    ) -> None:
        """Actively take a cheap ask when price distance or support makes it safe."""
        inventory_turn_replenishment = (
            account.policy.enable_downtrend_turn_while_extra_inventory
            and account.replenishment_quantity <= 1e-9
            and account.pending_inventory_turn_quantity > 1e-9
        )
        if (
            account.policy.enable_downtrend_turn_while_extra_inventory
            and (
                account.replenishment_quantity > 1e-9
                or account.pending_inventory_turn_quantity > 1e-9
            )
        ):
            if account.replenishment_quantity > 1e-9:
                average_sale_price = (
                    account.replenishment_sale_value
                    / account.replenishment_quantity
                )
            else:
                average_sale_price = (
                    account.pending_inventory_turn_sale_value
                    / account.pending_inventory_turn_quantity
                )
            if (
                tick.ask1 + 1e-9
                > average_sale_price - self._downtrend_turn_edge(account.policy)
            ):
                # A queue branch can fill its high leg and see the same stale
                # reference call the current ask "cheap" in that very frame.
                # Preserve the user's sell-high/buy-low sequence: the active
                # second leg must itself retain the promised corridor edge.
                return
        context = self._decision_context(tick, account.policy)
        supported_collapse_reference = (
            self._supported_current_midpoint_collapse_entry_reference(
                account, tick, assessment, context,
            )
        )
        if supported_collapse_reference is not None:
            active_reference = supported_collapse_reference
            active_reference_source = (
                "supported_previous_intraday_working_reference"
            )
        else:
            active_reference, active_reference_source = (
                self._active_entry_reference(context, tick, account.policy)
            )
            guarded_reference = self._ordinary_extra_entry_reference(
                account, tick, active_reference,
            )
            if guarded_reference + 1e-9 < active_reference:
                active_reference = guarded_reference
                active_reference_source = "post_replenishment_local_reference"
        edge = active_reference - tick.ask1
        if account.policy.enable_priority_v11_extensions:
            active_entry_safe = (
                supported_collapse_reference is not None
                or (
                    context.breakout_support_strong
                    and edge + self.parameters.fair_price_tolerance + 1e-9
                        >= self.parameters.minimum_base_high_sell_edge
                )
                or (
                    not context.breakout_support_strong
                    and edge + 1e-9
                        >= self.parameters.minimum_active_entry_edge
                )
            )
        else:
            active_entry_safe = (
                edge + 1e-9 >= self.parameters.minimum_active_entry_edge
                or (
                    edge + 1e-9
                        >= self.parameters.legacy_queue_supported_active_edge
                    and context.has_bid_support
                )
            )
        if not (
            self.observed_market_trade
            and self._entry_window_for_policy(
                tick.market_time, account.policy, tick.market_date,
            )
            and context.reference_price > 0
            and active_reference_source != "persistent_inside_market"
            and tick.ask1 > tick.bid1 > 0
            and tick.ask1_bonds > 0
            and active_entry_safe
            and self.parameters.opening_edge_is_safe(
                tick.market_date, tick.market_time, edge,
            )
        ):
            return
        if (
            account.policy.enable_priority_v11_extensions
            and
            assessment.iron_floor_price is not None
            and assessment.state != "rising"
            and not self._confirmed_rise_is_recent(tick, account.policy)
            and tick.ask1 - assessment.iron_floor_price + 1e-9
                > self.parameters.maximum_iron_floor_entry_premium
        ):
            return
        if (
            account.last_active_entry_price is not None
            and tick.ask1
                > account.last_active_entry_price
                    - self.parameters.minimum_distinct_active_improvement + 1e-9
        ):
            return
        capacity = max(0.0, account.maximum_inventory - account.inventory)
        affordable = self._affordable_buy_bonds(account, tick.ask1)
        quantity = min(
            self.parameters.order_quantity_bonds,
            tick.ask1_bonds,
            capacity,
            affordable,
            (
                account.pending_inventory_turn_quantity
                if inventory_turn_replenishment
                else self.parameters.order_quantity_bonds
            ),
        )
        if quantity <= 1e-9:
            return
        entry_kind = (
            "inventory_turn_replenish"
            if inventory_turn_replenishment
            else "supported_ask_collapse_entry"
            if supported_collapse_reference is not None
            else "deep_discount_sweep"
        )
        order_kind = (
            "supported_ask_collapse_sweep"
            if supported_collapse_reference is not None
            else "deep_discount_sweep"
        )
        active_buy_boundary = (
            tick.ask1
            if supported_collapse_reference is not None
            else active_reference - (
                self.parameters.minimum_base_high_sell_edge
                - self.parameters.fair_price_tolerance
                if context.breakout_support_strong
                else self.parameters.minimum_active_entry_edge
            )
        )
        if self.parameters.opening_caution_is_active(
            tick.market_date, tick.market_time,
        ):
            active_buy_boundary = min(
                active_buy_boundary,
                active_reference
                    - self.parameters.opening_caution_minimum_edge,
            )
        order = self._new_order(
            # This remains an actively executed deep-discount order for queue
            # auditing purposes.  The resulting inventory lot carries the
            # more specific replenishment kind below.
            account, tick, side="buy", kind=order_kind, lot_id=None,
            price=tick.ask1, quantity=quantity, queue_ahead=0.0,
            target_price=None, price_boundary=active_buy_boundary,
            persist=persist,
        )
        self._fill_buy(
            account, tick, order, quantity, tick.market_ts_ms * 1_000_000,
            kind=entry_kind, target_price=None,
            persist=persist, reason=(
                "active_inventory_turn_replenish"
                if inventory_turn_replenishment
                else "active_supported_ask_collapse_entry"
                if supported_collapse_reference is not None
                else "active_deep_discount"
            ),
        )

        account.last_active_entry_price = tick.ask1
        if account.buy_order is not None:
            self._cancel_order(
                account, account.buy_order, tick,
                "active_entry_replaced_passive_buy", persist,
            )

    def _supported_current_midpoint_collapse_entry_reference(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, context: MakerDecisionContext,
    ) -> float | None:
        """Keep a causally established range when a low offer crushes midpoint.

        The permission only upgrades an already-live passive extra-inventory
        bid to the visible offer.  A persisted nearby wall, a recent full-sized
        high-side buy and the immediately preceding intraday working reference
        must all agree.  Thus a falling label or a cheap-looking current
        midpoint cannot create a new buy thesis by itself.
        """

        policy = account.policy
        order = account.buy_order
        if not (
            policy.enable_supported_current_midpoint_collapse_extra_entry
            and account.fill_mode == "priority"
            and account.customer_base_short_bonds <= 1e-9
            and account.inventory + 1e-9 >= account.initial_inventory
            and account.inventory + 1e-9 < account.maximum_inventory
            and assessment.state in {"possible_fall", "falling"}
            and context.reference_source == "current_midpoint"
            and not self._confirmed_rise_is_recent(tick, policy)
            and order is not None
            and order.side == "buy"
            and order.kind == "low_bid_reversion"
            and order.remaining > 1e-9
            and order.created_ms < tick.market_ts_ms
            and account.inventory + order.remaining
                <= account.maximum_inventory + 1e-9
            and tick.ask1 > tick.bid1 > 0
            and tick.ask1 > order.limit_price > 0
            and tick.ask1_bonds + 1e-9
                >= self.parameters.order_quantity_bonds
            and self.previous_intraday_working_reference > 0
            and 0 <= (
                tick.market_ts_ms
                - self.previous_intraday_working_reference_ts_ms
            ) <= self.parameters.market_temperature_window_seconds * 1_000
            and (
                self.previous_intraday_working_reference
                - context.reference_price
                + self.parameters.fair_price_tolerance + 1e-9
            ) >= policy.supported_midpoint_collapse_minimum_reference_dislocation
            and (
                self.previous_intraday_working_reference - tick.ask1
                + self.parameters.fair_price_tolerance + 1e-9
            ) >= self.parameters.minimum_entry_edge
        ):
            return None
        if (
            account.last_falling_profitable_exit_price > 0
            and 0 <= (
                tick.market_ts_ms
                - account.last_falling_profitable_exit_ts_ms
            ) <= policy.falling_profitable_reentry_cooldown_seconds * 1_000
        ):
            return None

        threshold = (
            self.parameters.large_wall_multiple
            * self.parameters.order_quantity_bonds
        )
        visible_walls = [
            (price, quantity) for price, quantity in tick.bids
            if price > 0
            and tick.bid1 - price
                <= self.parameters.maximum_downtrend_wall_anchor_gap + 1e-9
            and quantity + 1e-9 >= threshold
        ]
        if not visible_walls:
            return None
        wall_price, _ = max(visible_walls, key=lambda item: item[0])
        first_seen_ms = self.visible_bid_wall_first_seen_ms.get(
            round(wall_price, 6),
        )
        if (
            first_seen_ms is None
            or tick.market_ts_ms - first_seen_ms
                < policy.supported_midpoint_collapse_minimum_wall_seconds
                    * 1_000
            or tick.ask1 - wall_price
                > self.parameters.maximum_downtrend_wall_entry_premium + 1e-9
            or order.limit_price + 1e-9 < wall_price
            or order.limit_price - wall_price
                > self.parameters.maximum_downtrend_wall_entry_premium + 1e-9
            or tick.ask1 - order.limit_price
                > self.parameters.maximum_downtrend_wall_entry_premium + 1e-9
        ):
            return None

        lookback_start_ms = (
            tick.market_ts_ms
            - policy.supported_midpoint_collapse_high_buy_lookback_seconds
                * 1_000
        )
        prior_high_buy_bonds = sum(
            event.bonds for event in self.analyzer.trade_evidence
            if lookback_start_ms <= event.market_ts_ms < tick.market_ts_ms
            and event.side == "buy"
            and event.price - tick.ask1
                + self.parameters.fair_price_tolerance + 1e-9
                >= self.parameters.minimum_entry_edge
        )
        if (
            prior_high_buy_bonds + 1e-9
            < policy.supported_midpoint_collapse_minimum_high_buy_bonds
        ):
            return None
        return self.previous_intraday_working_reference

    def _active_entry_reference(
        self, context: MakerDecisionContext, tick: ReplayTick,
        policy: MakerPolicyProfile,
    ) -> tuple[float, str]:
        """Keep a transient wide gap from reviving a stale close for active buys.

        Passive bids can still use their existing conservative execution logic.
        Paying the current ask is different: once a causal intraday working
        reference has formed, a brief loss of the book/anchor reference must
        not make yesterday's close look like a fresh 0.50 discount.
        """

        if (
            policy.use_recent_intraday_reference_for_active_entry
            and context.reference_source == "previous_close"
            and self.observed_market_trade
            and self.last_intraday_working_reference > 0
            and tick.market_ts_ms - self.last_intraday_working_reference_ts_ms
                <= self.parameters.market_temperature_window_seconds * 1_000
        ):
            return (
                self.last_intraday_working_reference,
                "recent_intraday_working_reference",
            )
        return context.reference_price, context.reference_source

    def _ordinary_extra_entry_reference(
        self, account: MakerAccount, tick: ReplayTick, reference: float,
    ) -> float:
        """Prevent an old high anchor from re-authorizing a high extra bid.

        Completing a base-inventory replenishment at a real lower-side price
        establishes fresh causal price discovery.  During the evidence
        half-life, until a later confirmed rise occurs, a new *extra* position
        must also look cheap relative to the local executable market; a
        quote-only bid staircase cannot use the pre-replenishment high anchor
        as its sole safety source.

        The local reference is the higher of the completed replenishment price
        and the current inside midpoint.  Taking the more conservative value
        between it and the existing causal reference preserves genuine new
        downside discounts and wide-spread low-side making, while rejecting a
        bid that has merely climbed back to the midpoint without confirming
        trades.  Inventory deficits are deliberately excluded because their
        planned second leg must still restore the base position immediately.
        """

        if not (
            account.policy.use_local_reference_after_base_replenishment
            and account.inventory + 1e-9 >= account.initial_inventory
            and account.last_base_replenishment_price > 0
            and account.last_base_replenishment_ts_ms > 0
            and tick.bid1 > 0
            and tick.ask1 > tick.bid1
        ):
            return reference
        if (
            self.last_confirmed_rise_trade_ts_ms
                >= account.last_base_replenishment_ts_ms
        ):
            return reference
        if (
            tick.market_ts_ms - account.last_base_replenishment_ts_ms
                > self.parameters.evidence_half_life_seconds * 1_000
        ):
            return reference
        local_reference = max(
            account.last_base_replenishment_price,
            (tick.bid1 + tick.ask1) / 2,
        )
        return min(reference, local_reference)

    def _high_side_validated_supported_corridor_entry(
        self,
        account: MakerAccount,
        tick: ReplayTick,
        assessment: MarketAssessment,
        context: MakerDecisionContext,
    ) -> float | None:
        """Quote only the supported low side after a real high-side buy.

        This is an extra-long entry, not a customer-base sale.  The current
        print may authorize a resting bid but is never reused to fill it;
        ordinary replay ordering requires a later sell print to execute the
        newly created order.
        """

        policy = account.policy
        if not (
            policy.enable_high_side_validated_supported_corridor_entry
            and account.fill_mode == "priority"
            and account.customer_base_short_bonds <= 1e-9
            and account.inventory + 1e-9 >= account.initial_inventory
            and account.inventory + 1e-9 < account.maximum_inventory
            and assessment.state in {"stable", "possible_fall"}
            and context.reference_source != "previous_close"
            and tick.inferred_side == "buy"
            and tick.trade_bonds + 1e-9
                >= policy.supported_corridor_minimum_high_buy_bonds
            and tick.ask1 > tick.bid1 > 0
        ):
            return None

        candidate_price = _floor_to_tick(
            tick.bid1 + self.parameters.price_tick,
            self.parameters.price_tick,
        )
        corridor_edge = tick.ask1 - candidate_price
        reference_low_edge = context.reference_price - candidate_price
        if not (
            candidate_price < tick.ask1
            and policy.supported_corridor_minimum_edge - 1e-9
                <= corridor_edge
                <= policy.supported_corridor_maximum_edge + 1e-9
            and tick.last_price - candidate_price + 1e-9
                >= policy.supported_corridor_minimum_edge
            and tick.ask1 - tick.last_price
                <= policy.supported_corridor_maximum_high_trade_ask_gap + 1e-9
            and context.bid_support_bonds + 1e-9
                >= context.wall_threshold_bonds
            and context.ask_supply_bonds + 1e-9
                >= policy.supported_corridor_minimum_ask_supply_bonds
            and 0 <= reference_low_edge + 1e-9
            and reference_low_edge
                <= policy.supported_corridor_maximum_reference_low_edge + 1e-9
            and abs(assessment.midpoint_change)
                <= policy.supported_corridor_maximum_midpoint_change + 1e-9
            and assessment.short_ask_change
                >= -policy.supported_corridor_maximum_ask_drop - 1e-9
        ):
            return None
        if (
            account.last_falling_profitable_exit_price > 0
            and 0 <= (
                tick.market_ts_ms
                - account.last_falling_profitable_exit_ts_ms
            ) <= policy.falling_profitable_reentry_cooldown_seconds * 1_000
        ):
            return None
        return candidate_price

    def _persistent_two_sided_wall_corridor_entry(
        self,
        account: MakerAccount,
        tick: ReplayTick,
        assessment: MarketAssessment,
        context: MakerDecisionContext,
    ) -> tuple[float, float, float] | None:
        """Quote a passive extra bid in a causally established corridor.

        Unlike the v1.38 permission, the current frame need not itself be a
        high-side buy.  Both sides must already have traded substantial real
        volume, and a nearby concentrated bid wall must have stayed visible
        for a full minute before the decision.  A later sell print is still
        required to fill the newly resting order.
        """

        policy = account.policy
        if not (
            policy.enable_persistent_two_sided_wall_corridor_entry
            and account.fill_mode == "priority"
            and account.customer_base_short_bonds <= 1e-9
            and account.inventory + 1e-9 >= account.initial_inventory
            and account.inventory + 1e-9 < account.maximum_inventory
            and assessment.state in {"stable", "possible_fall"}
            and context.reference_source != "previous_close"
            and assessment.recent_buy_bonds + 1e-9
                >= policy.two_sided_wall_corridor_minimum_side_bonds
            and assessment.recent_sell_bonds + 1e-9
                >= policy.two_sided_wall_corridor_minimum_side_bonds
            and context.ask_supply_bonds + 1e-9
                >= policy.two_sided_wall_corridor_minimum_ask_supply_bonds
            and tick.ask1 > tick.bid1 > 0
        ):
            return None

        candidate_price = _floor_to_tick(
            tick.bid1 + self.parameters.price_tick,
            self.parameters.price_tick,
        )
        corridor_edge = tick.ask1 - candidate_price
        reference_low_edge = context.reference_price - candidate_price
        if not (
            candidate_price < tick.ask1
            and policy.two_sided_wall_corridor_minimum_edge - 1e-9
                <= corridor_edge
                <= policy.two_sided_wall_corridor_maximum_edge + 1e-9
            and 0 <= reference_low_edge + 1e-9
            and reference_low_edge
                <= policy.two_sided_wall_corridor_maximum_reference_low_edge
                    + 1e-9
            and abs(assessment.midpoint_change)
                <= policy.two_sided_wall_corridor_maximum_midpoint_change
                    + 1e-9
            and assessment.short_ask_change
                >= -policy.two_sided_wall_corridor_maximum_ask_drop - 1e-9
        ):
            return None
        if (
            account.last_falling_profitable_exit_price > 0
            and 0 <= (
                tick.market_ts_ms
                - account.last_falling_profitable_exit_ts_ms
            ) <= policy.falling_profitable_reentry_cooldown_seconds * 1_000
        ):
            return None

        visible_walls = [
            (price, bonds)
            for price, bonds in tick.bids
            if price > 0
            and price <= candidate_price + 1e-9
            and candidate_price - price
                <= policy.two_sided_wall_corridor_maximum_wall_premium + 1e-9
            and bonds + 1e-9 >= context.wall_threshold_bonds
        ]
        if not visible_walls:
            return None
        wall_price, wall_bonds = max(visible_walls, key=lambda item: item[0])
        first_seen_ms = self.visible_bid_wall_first_seen_ms.get(
            round(wall_price, 6),
        )
        if (
            first_seen_ms is None
            or tick.market_ts_ms - first_seen_ms
                < policy.two_sided_wall_corridor_minimum_wall_seconds * 1_000
        ):
            return None
        return candidate_price, wall_price, wall_bonds

    def _persistent_wide_spread_buy_first_entry(
        self,
        account: MakerAccount,
        tick: ReplayTick,
        assessment: MarketAssessment,
        context: MakerDecisionContext,
    ) -> float | None:
        """Quote the low side first when a real, persistent corridor exists.

        The recent high-side trade validates an executable exit area, while a
        full minute of stable inside quotes prevents a momentary wide spread
        from manufacturing permission.  This opens only extra inventory; the
        customer's base remains untouched until low-side inventory exists.
        """

        policy = account.policy
        if not (
            policy.enable_persistent_wide_spread_buy_first_entry
            and account.fill_mode == "priority"
            and account.customer_base_short_bonds <= 1e-9
            and account.inventory + 1e-9 >= account.initial_inventory
            and account.inventory + 1e-9 < account.maximum_inventory
            and assessment.state in {"stable", "possible_fall"}
            and context.reference_source != "previous_close"
            and tick.ask1 > tick.bid1 > 0
            and tick.bid1_bonds + 1e-9
                >= self.parameters.order_quantity_bonds
            and tick.ask1_bonds + 1e-9
                >= self.parameters.order_quantity_bonds
            and abs(assessment.midpoint_change)
                <= policy.wide_spread_buy_first_maximum_midpoint_change + 1e-9
            and assessment.short_ask_change
                >= -policy.wide_spread_buy_first_maximum_ask_drop - 1e-9
        ):
            return None

        candidate_price = _floor_to_tick(
            tick.bid1 + self.parameters.price_tick,
            self.parameters.price_tick,
        )
        current_edge = tick.ask1 - candidate_price
        if not (
            candidate_price < tick.ask1
            and policy.wide_spread_buy_first_minimum_edge - 1e-9
                <= current_edge
                <= policy.wide_spread_buy_first_maximum_edge + 1e-9
        ):
            return None

        cutoff_ms = (
            tick.market_ts_ms
            - policy.wide_spread_buy_first_minimum_book_seconds * 1_000
        )
        corridor_quotes = [
            quote for quote in self.analyzer.book_quotes
            if cutoff_ms <= quote.market_ts_ms <= tick.market_ts_ms
        ]
        if not corridor_quotes or corridor_quotes[0].market_ts_ms > cutoff_ms:
            return None
        maximum_drift = policy.wide_spread_buy_first_maximum_book_drift
        bid_prices = [quote.bid for quote in corridor_quotes]
        ask_prices = [quote.ask for quote in corridor_quotes]
        if (
            max(bid_prices) - min(bid_prices) > maximum_drift + 1e-9
            or max(ask_prices) - min(ask_prices) > maximum_drift + 1e-9
        ):
            return None
        for quote in corridor_quotes:
            quote_candidate = _floor_to_tick(
                quote.bid + self.parameters.price_tick,
                self.parameters.price_tick,
            )
            quote_edge = quote.ask - quote_candidate
            if not (
                policy.wide_spread_buy_first_minimum_edge - 1e-9
                    <= quote_edge
                    <= policy.wide_spread_buy_first_maximum_edge + 1e-9
            ):
                return None

        high_buy_cutoff_ms = (
            tick.market_ts_ms
            - policy.wide_spread_buy_first_high_buy_lookback_seconds * 1_000
        )
        high_buy_bonds = sum(
            event.bonds
            for event in self.analyzer.trade_evidence
            if high_buy_cutoff_ms <= event.market_ts_ms <= tick.market_ts_ms
            and event.side == "buy"
            and abs(event.price - tick.ask1)
                <= (
                    policy.wide_spread_buy_first_maximum_high_trade_ask_gap
                    + 1e-9
                )
        )
        existing_corridor_order = (
            account.buy_order is not None
            and account.buy_order.kind
                == "persistent_wide_spread_buy_first_entry"
            and abs(account.buy_order.limit_price - candidate_price) <= 1e-9
        )
        if (
            high_buy_bonds + 1e-9
            < policy.wide_spread_buy_first_minimum_high_buy_bonds
            and not existing_corridor_order
        ):
            return None
        if (
            account.last_falling_profitable_exit_price > 0
            and 0 <= (
                tick.market_ts_ms
                - account.last_falling_profitable_exit_ts_ms
            ) <= policy.falling_profitable_reentry_cooldown_seconds * 1_000
        ):
            return None
        return candidate_price

    def _active_inventory_risk_exit(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, *, persist: bool,
        received_ts_ns: int,
    ) -> None:
        """Hit the visible bid before a thin downside ladder opens up.

        This only reduces extra inventory bought above the daily base.  A
        correct low entry can still become unsafe after the offer ladder keeps
        compressing; near-flat execution at the remaining best bid is then
        preferable to waiting for a passive high-side fill.
        """
        parameters = self.parameters
        minimum_sell_bonds = parameters.order_quantity_bonds * 2
        sell_dominant = (
            assessment.recent_sell_bonds + 1e-9 >= minimum_sell_bonds
            and assessment.recent_sell_bonds + 1e-9
                >= assessment.recent_buy_bonds
                * parameters.downside_sell_imbalance_ratio
        )
        bearish_vacuum = (
            assessment.short_ask_change
                <= -parameters.minimum_short_ask_drop + 1e-9
            and assessment.downside_book_vacuum
            and sell_dominant
        )
        policy = account.policy
        confirmed_falling_pressure = (
            policy.enable_confirmed_falling_near_flat_extra_exit
            and assessment.state == "falling"
            and assessment.recent_sell_bonds + 1e-9 >= (
                parameters.order_quantity_bonds
                * policy.confirmed_falling_extra_exit_minimum_sell_multiple
            )
            and assessment.recent_sell_bonds + 1e-9 >= (
                assessment.recent_buy_bonds
                * policy.confirmed_falling_extra_exit_minimum_imbalance_ratio
            )
            and assessment.midpoint_change <= (
                -policy.confirmed_falling_extra_exit_minimum_midpoint_drop
                + 1e-9
            )
            and tick.bid1_bonds + 1e-9
                >= parameters.order_quantity_bonds
            and not self._confirmed_rise_is_recent(tick, policy)
        )
        if not (
            (
                bearish_vacuum
                or assessment.fragile_top_bid
                or confirmed_falling_pressure
            )
            and tick.bid1 > 0
            and tick.bid1_bonds > 0
            and account.inventory > account.initial_inventory + 1e-9
        ):
            return

        available = min(
            tick.bid1_bonds,
            account.inventory - account.initial_inventory,
        )
        candidates = sorted(
            (
                lot for lot in account.lots.values()
                if lot.entry_price is not None
                and lot.remaining_quantity > 1e-9
                and lot.entry_price - tick.bid1
                    <= parameters.maximum_near_flat_exit_loss + 1e-9
            ),
            key=lambda lot: (lot.opened_ms, lot.db_id),
            reverse=True,
        )
        if not candidates:
            return
        if account.buy_order is not None:
            self._cancel_order(
                account, account.buy_order, tick,
                "downside_risk_exit", persist,
            )
        for lot in candidates:
            quantity = min(available, lot.remaining_quantity)
            if quantity <= 1e-9:
                break
            existing = account.sell_orders.get(lot.db_id)
            if existing is not None:
                self._cancel_order(
                    account, existing, tick,
                    "active_risk_exit_replaced_passive_sell", persist,
                )
            order = self._new_order(
                account, tick, side="sell", kind="inventory_risk_exit",
                lot_id=lot.db_id, price=tick.bid1, quantity=quantity,
                queue_ahead=0.0, target_price=tick.bid1,
                price_boundary=tick.bid1, persist=persist,
            )
            account.sell_orders[lot.db_id] = order
            self._fill_sell(
                account, tick, order, quantity, received_ts_ns,
                persist=persist,
                reason=(
                    "active_confirmed_falling_near_flat_exit"
                    if confirmed_falling_pressure
                    and not bearish_vacuum
                    and not assessment.fragile_top_bid
                    else "active_downside_risk_exit"
                ),
            )
            if confirmed_falling_pressure:
                # The decision is a falling-market risk release, not a
                # one-frame round trip.  Reuse the existing v1.8 cooldown so
                # ordinary extra inventory cannot be bought straight back at
                # the same price while the same downside evidence survives.
                account.last_falling_profitable_exit_price = tick.bid1
                account.last_falling_profitable_exit_ts_ms = tick.market_ts_ms
            available -= quantity

    def _active_falling_profitable_bid_exit(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, *, persist: bool,
        received_ts_ns: int,
    ) -> None:
        """Use a still-profitable bid while active selling consumes it.

        A wide spread should not force an extra lot to wait at a distant ask
        when the market is already falling, a material active sell has just
        hit the bid, and that bid still offers a clean profit.  This action is
        deliberately narrower than the ordinary tight-spread turnover exit:
        it only removes inventory above the daily base, requires current
        sell-side pressure, and is disabled while a confirmed rise remains
        causally valid.
        """

        policy = account.policy
        parameters = self.parameters
        if not (
            policy.enable_falling_profitable_bid_exit
            and assessment.state in {"possible_fall", "falling"}
            and tick.inferred_side == "sell"
            and tick.trade_bonds + 1e-9 >= (
                parameters.order_quantity_bonds
                * policy.minimum_falling_profitable_sell_multiple
            )
            and assessment.recent_sell_bonds + 1e-9
                >= assessment.recent_buy_bonds
                * parameters.downside_sell_imbalance_ratio
            and tick.bid1 > 0
            and tick.bid1_bonds + 1e-9 >= parameters.order_quantity_bonds
            and account.inventory > account.initial_inventory + 1e-9
            and not self._confirmed_rise_is_recent(tick, policy)
        ):
            return

        available = min(
            tick.bid1_bonds,
            account.inventory - account.initial_inventory,
        )
        candidates = sorted(
            (
                lot for lot in account.lots.values()
                if lot.entry_price is not None
                and lot.remaining_quantity > 1e-9
                and tick.bid1 - lot.entry_price + 1e-9
                    >= policy.minimum_falling_profitable_exit_edge
            ),
            key=lambda lot: (lot.opened_ms, lot.db_id),
        )
        if not candidates:
            return
        if account.buy_order is not None:
            self._cancel_order(
                account, account.buy_order, tick,
                "falling_profitable_bid_exit", persist,
            )
        for lot in candidates:
            quantity = min(available, lot.remaining_quantity)
            if quantity <= 1e-9:
                break
            existing = account.sell_orders.get(lot.db_id)
            if existing is not None:
                self._cancel_order(
                    account, existing, tick,
                    "active_falling_exit_replaced_passive_sell", persist,
                )
            order = self._new_order(
                account, tick, side="sell",
                kind="falling_profitable_bid_exit",
                lot_id=lot.db_id, price=tick.bid1, quantity=quantity,
                queue_ahead=0.0, target_price=tick.bid1,
                price_boundary=(
                    lot.entry_price
                    + policy.minimum_falling_profitable_exit_edge
                ),
                persist=persist,
            )
            account.sell_orders[lot.db_id] = order
            self._fill_sell(
                account, tick, order, quantity, received_ts_ns,
                persist=persist,
                reason="active_falling_profitable_bid_exit",
            )
            account.last_falling_profitable_exit_price = tick.bid1
            account.last_falling_profitable_exit_ts_ms = tick.market_ts_ms
            available -= quantity

    def _active_profitable_turnover_exit(
        self, account: MakerAccount, tick: ReplayTick, *, persist: bool,
        received_ts_ns: int,
    ) -> None:
        """Take a nearby bid when an extra lot already has a clean T edge.

        In a tight, two-sided market the executable round trip matters more
        than waiting for a distant fair-value target.  This only turns over
        inventory above the base position and never sells the base lot.
        """
        if not (
            tick.bid1 > 0
            and tick.bid1_bonds > 0
            and tick.ask1 > tick.bid1
            and tick.ask1 - tick.bid1
                <= self.parameters.maximum_active_turnover_spread + 1e-9
            and account.inventory > account.initial_inventory + 1e-9
        ):
            return
        available = min(
            tick.bid1_bonds,
            account.inventory - account.initial_inventory,
        )
        candidates = sorted(
            (
                lot for lot in account.lots.values()
                if lot.entry_price is not None
                and lot.remaining_quantity > 1e-9
                and tick.bid1 - lot.entry_price + 1e-9
                    >= self.parameters.minimum_passive_turnover_edge
            ),
            key=lambda lot: (lot.opened_ms, lot.db_id),
        )
        if not candidates:
            return
        if account.buy_order is not None:
            self._cancel_order(
                account, account.buy_order, tick,
                "active_turnover_exit", persist,
            )
        for lot in candidates:
            quantity = min(available, lot.remaining_quantity)
            if quantity <= 1e-9:
                break
            existing = account.sell_orders.get(lot.db_id)
            if existing is not None:
                self._cancel_order(
                    account, existing, tick,
                    "active_turnover_replaced_passive_sell", persist,
                )
            order = self._new_order(
                account, tick, side="sell", kind="inventory_turnover_exit",
                lot_id=lot.db_id, price=tick.bid1, quantity=quantity,
                queue_ahead=0.0, target_price=tick.bid1,
                price_boundary=(
                    lot.entry_price
                    + self.parameters.minimum_passive_turnover_edge
                ),
                persist=persist,
            )
            account.sell_orders[lot.db_id] = order
            self._fill_sell(
                account, tick, order, quantity, received_ts_ns,
                persist=persist, reason="active_tight_spread_turnover",
            )
            available -= quantity

    def _refresh_super_windfall(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, *, persist: bool,
    ) -> None:
        """Keep one sticky order at a deeply anomalous bid-book level."""
        if not self.parameters.maker_session_has_started(
            tick.market_date, tick.market_time,
        ):
            if account.buy_order is not None:
                self._cancel_order(
                    account, account.buy_order, tick,
                    "maker_session_not_started", persist,
                )
            return
        if account.inventory >= account.maximum_inventory - 1e-9:
            if account.buy_order is not None:
                self._cancel_order(
                    account, account.buy_order, tick,
                    "super_windfall_capacity_full", persist,
                )
            return
        recent_trade_reference = self.analyzer.recent_trade_reference(
            tick.market_ts_ms,
            self.parameters.windfall_recent_trade_window_seconds,
        )
        assessment_reference = assessment.reference_price
        if (
            account.policy.exclude_wide_persistent_windfall_reference
            and assessment.reference_source == "persistent_inside_market"
            and tick.ask1 - tick.bid1
                > self.parameters.maximum_provisional_midpoint_spread + 1e-9
        ):
            # The anomalous deep bid is the event being tested.  During a very
            # wide spread it must not drag the midpoint reference down and then
            # disqualify its own one-tick improvement as insufficiently cheap.
            assessment_reference = None
        references = [
            value for value in (assessment_reference, recent_trade_reference)
            if value is not None and value > 0
        ]
        if not references:
            return
        reference = min(references)
        candidate: tuple[float, float, float] | None = None
        for upper, lower in zip(tick.bids, tick.bids[1:]):
            book_gap = upper[0] - lower[0]
            discount = reference - lower[0]
            if (
                book_gap + 1e-9
                    >= self.parameters.minimum_windfall_book_gap
                and discount + 1e-9
                    >= self.parameters.minimum_windfall_discount
            ):
                candidate = (lower[0], lower[1], upper[0])
                break
        if candidate is None and tick.bids:
            top_gap = max(tick.last_price, tick.ask1) - tick.bid1
            if (
                top_gap + 1e-9
                    >= self.parameters.minimum_windfall_book_gap
                and reference - tick.bid1 + 1e-9
                    >= self.parameters.minimum_windfall_discount
            ):
                candidate = (tick.bid1, tick.bid1_bonds, tick.ask1)
        if candidate is None:
            return

        level_price, _, upper_price = candidate
        price = level_price + self.parameters.price_tick
        if price >= upper_price - 1e-9:
            price = level_price
        price = _floor_to_tick(price, self.parameters.price_tick)
        capacity = account.maximum_inventory - account.inventory
        affordable = self._affordable_buy_bonds(account, price)
        quantity = min(
            self.config.maker_paper.super_windfall_quantity_bonds,
            capacity,
            affordable,
        )
        if quantity <= 1e-9:
            return
        if account.buy_order is not None:
            if price <= account.buy_order.limit_price + 1e-9:
                return
            self._cancel_order(
                account, account.buy_order, tick,
                "super_windfall_better_anomaly", persist,
            )
        queue = self._book_quantity(tick, "buy", price)
        if price > level_price:
            queue = 0.0
        account.buy_order = self._new_order(
            account, tick, side="buy", kind="super_windfall",
            lot_id=None, price=price, quantity=quantity,
            queue_ahead=queue, target_price=None,
            price_boundary=(
                reference - self.parameters.minimum_windfall_discount
            ),
            persist=persist,
        )

    def _fair_reference(self) -> float:
        return self._decision_context(None).reference_price

    def _decision_context(
        self, tick: ReplayTick | None,
        policy: MakerPolicyProfile | None = None,
    ) -> MakerDecisionContext:
        policy = policy or PRIORITY_POLICY_V11
        anchor = self.analyzer.last_anchor
        reliable = (
            anchor is not None
            and anchor.confidence >= self.parameters.minimum_anchor_confidence
        )
        reference = (
            anchor.reference_price if reliable and anchor is not None
            else self.previous_close_reference
        )
        source = "intraday_trade_anchor" if reliable else "previous_close"
        if (
            not policy.enable_priority_v11_extensions
            and not reliable
            and self.last_legacy_reliable_reference > 0
            and tick is not None
            and tick.market_ts_ms - self.last_legacy_reliable_reference_ts_ms
                <= self.parameters.market_temperature_window_seconds * 1000
        ):
            reference = self.last_legacy_reliable_reference
            source = "legacy_last_trade_anchor"
        now_ms = tick.market_ts_ms if tick is not None else 0
        book_reference = (
            self.analyzer.persistent_book_reference(now_ms)
            if not reliable and now_ms > 0 else None
        )
        if (
            policy.enable_priority_v11_extensions
            and book_reference is not None
        ):
            reference = book_reference
            source = "persistent_inside_market"
        elif (
            policy.enable_priority_v11_extensions
            and
            not reliable
            and tick is not None
            and self.analyzer.provisional_midpoint_ready()
            and tick.ask1 > tick.bid1 > 0
            and tick.ask1 - tick.bid1
                <= self.parameters.maximum_provisional_midpoint_spread + 1e-9
        ):
            reference = (tick.bid1 + tick.ask1) / 2
            source = "current_midpoint"
        if (
            not policy.enable_priority_v11_extensions
            and self.legacy_breakout_support_price > 0
            and now_ms - self.legacy_breakout_support_ts_ms
                <= self.parameters.breakout_support_seconds * 1000
        ):
            breakout_support = self.legacy_breakout_support_price
            breakout_lower_sells = 0.0
        else:
            breakout_support = (
                self.analyzer.active_breakout_support(now_ms)
                if now_ms > 0 else None
            )
            breakout_lower_sells = (
                self.analyzer.breakout_lower_sell_bonds(now_ms)
                if breakout_support is not None else 0.0
            )
        breakout_strong = (
            breakout_support is not None
            and breakout_lower_sells + 1e-9
                < self.parameters.breakout_weakening_sell_bonds
        )
        if breakout_strong and breakout_support > reference:
            reference = breakout_support
            source = "large_buy_breakout_support"
        if tick is None:
            return MakerDecisionContext(
                reference, source, reliable, 0.0, 0.0, 0.0,
                self.parameters.large_wall_multiple
                    * self.parameters.order_quantity_bonds,
                breakout_support or 0.0, breakout_lower_sells,
            )
        distance = self.parameters.book_safety_distance
        bid_support = sum(
            quantity for price, quantity in tick.bids
            if price + 1e-9 >= tick.bid1 - distance
        )
        ask_supply = sum(
            quantity for price, quantity in tick.asks
            if price <= tick.ask1 + distance + 1e-9
        )
        return MakerDecisionContext(
            reference_price=reference,
            reference_source=source,
            reliable_anchor=reliable,
            spread=max(0.0, tick.ask1 - tick.bid1),
            bid_support_bonds=bid_support,
            ask_supply_bonds=ask_supply,
            wall_threshold_bonds=(
                self.parameters.large_wall_multiple
                * self.parameters.order_quantity_bonds
            ),
            breakout_support_price=breakout_support or 0.0,
            breakout_lower_sell_bonds=breakout_lower_sells,
        )

    def _entry_is_safe(self, edge: float, bid_support_bonds: float) -> bool:
        if edge + 1e-9 >= self.parameters.minimum_active_entry_edge:
            return True
        wall_threshold = (
            self.parameters.large_wall_multiple
            * self.parameters.order_quantity_bonds
        )
        return (
            edge + self.parameters.fair_price_tolerance + 1e-9
                >= self.parameters.minimum_entry_edge
            and bid_support_bonds + 1e-9 >= wall_threshold
        )

    def _update_visible_bid_wall(self, tick: ReplayTick) -> None:
        wall_threshold = (
            self.parameters.large_wall_multiple
            * self.parameters.order_quantity_bonds
        )
        visible_walls = [
            (price, quantity) for price, quantity in tick.bids
            if quantity + 1e-9 >= wall_threshold
        ]
        visible_wall_keys = {
            round(price, 6) for price, _ in visible_walls
        }
        for price_key in list(self.visible_bid_wall_first_seen_ms):
            if price_key not in visible_wall_keys:
                del self.visible_bid_wall_first_seen_ms[price_key]
        for price, _ in visible_walls:
            self.visible_bid_wall_first_seen_ms.setdefault(
                round(price, 6), tick.market_ts_ms,
            )
        if visible_walls:
            price, quantity = max(visible_walls, key=lambda item: item[0])
            self.last_visible_bid_wall_price = price
            self.last_visible_bid_wall_bonds = quantity
            self.last_visible_bid_wall_ts_ms = tick.market_ts_ms
            self.bid_wall_currently_visible = True
        elif self.bid_wall_currently_visible:
            self.last_bid_wall_left_book_ts_ms = tick.market_ts_ms
            self.bid_wall_currently_visible = False

    def _persistent_wall_supported_falling_extra_entry(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, context: MakerDecisionContext, *,
        confirmed_rise_recent: bool,
        falling_profitable_reentry_active: bool,
    ) -> tuple[float, float, float] | None:
        """Return a causal wall-backed extra-entry quote for priority v1.34.

        This is deliberately narrower than the ordinary visible-wall logic.
        A wall must have remained continuously visible before the decision, a
        real high-side buy must already exist in the lookback window, and the
        candidate must retain a practical passive exit corridor.  The helper
        cannot restore a customer-base short or undo a recent active risk exit.
        """

        policy = account.policy
        if not (
            policy.enable_persistent_wall_supported_falling_extra_entry
            and account.fill_mode == "priority"
            and account.customer_base_short_bonds <= 1e-9
            and account.inventory + 1e-9 >= account.initial_inventory
            and account.inventory + 1e-9 < account.maximum_inventory
            and assessment.state in {"possible_fall", "falling"}
            and not confirmed_rise_recent
            and not falling_profitable_reentry_active
            and tick.ask1 > tick.bid1 > 0
            and context.spread + 1e-9 >= self.parameters.minimum_entry_edge
            and context.spread
                < self.parameters.minimum_active_entry_edge - 1e-9
            and tick.ask1_bonds + 1e-9
                >= policy.persistent_wall_supported_entry_minimum_ask_bonds
        ):
            return None

        visible_walls = [
            (price, quantity) for price, quantity in tick.bids
            if price > 0
            and price <= tick.bid1 + 1e-9
            and tick.bid1 - price
                <= self.parameters.maximum_downtrend_wall_anchor_gap + 1e-9
            and quantity + 1e-9 >= context.wall_threshold_bonds
        ]
        if not visible_walls:
            return None
        wall_price, wall_bonds = max(visible_walls, key=lambda item: item[0])
        first_seen_ms = self.visible_bid_wall_first_seen_ms.get(
            round(wall_price, 6),
        )
        if (
            first_seen_ms is None
            or tick.market_ts_ms - first_seen_ms
                < policy.persistent_wall_supported_entry_minimum_wall_seconds
                    * 1_000
        ):
            return None

        candidate_price = _floor_to_tick(
            min(
                tick.bid1 + self.parameters.price_tick,
                wall_price
                    + policy.persistent_wall_supported_entry_maximum_wall_premium,
            ),
            self.parameters.price_tick,
        )
        if not (
            candidate_price > 0
            and candidate_price < tick.ask1
            and candidate_price - wall_price <= (
                policy.persistent_wall_supported_entry_maximum_wall_premium
                + 1e-9
            )
            and tick.ask1 - candidate_price + 1e-9
                >= policy.persistent_wall_supported_entry_minimum_exit_edge
        ):
            return None

        lookback_start_ms = (
            tick.market_ts_ms
            - policy.persistent_wall_supported_entry_high_buy_lookback_seconds
                * 1_000
        )
        prior_high_buy_bonds = sum(
            event.bonds for event in self.analyzer.trade_evidence
            if lookback_start_ms <= event.market_ts_ms < tick.market_ts_ms
            and event.side == "buy"
            and event.price + self.parameters.fair_price_tolerance + 1e-9
                >= tick.ask1
        )
        if (
            prior_high_buy_bonds + 1e-9
            < policy.persistent_wall_supported_entry_minimum_high_buy_bonds
        ):
            return None
        return candidate_price, wall_price, wall_bonds

    def _retain_persistent_wall_supported_falling_extra_entry(
        self, account: MakerAccount, order: MakerOrder, tick: ReplayTick,
        assessment: MarketAssessment, context: MakerDecisionContext, *,
        confirmed_rise_recent: bool,
        falling_profitable_reentry_active: bool,
        in_entry_window: bool,
    ) -> tuple[float, float] | None:
        """Return the still-live original wall when a v1.35 bid may persist."""

        policy = account.policy
        if not (
            policy.retain_persistent_wall_supported_falling_extra_entry
            and account.fill_mode == "priority"
            and order.side == "buy"
            and order.kind == "persistent_wall_supported_falling_entry"
            and order.remaining > 1e-9
            and order.visible_wall_entry_price > 0
            and account.customer_base_short_bonds <= 1e-9
            and account.inventory + 1e-9 >= account.initial_inventory
            and account.inventory + order.remaining
                <= account.maximum_inventory + 1e-9
            and not falling_profitable_reentry_active
            and in_entry_window
            and 0 <= tick.market_ts_ms - order.created_ms
                <= policy.persistent_wall_supported_entry_maximum_lifetime_seconds
                    * 1_000
            and tick.ask1 > order.limit_price > 0
            and tick.ask1_bonds + 1e-9
                >= policy.persistent_wall_supported_entry_minimum_ask_bonds
            and tick.ask1 - order.limit_price + 1e-9
                >= policy.persistent_wall_supported_entry_minimum_exit_edge
        ):
            return None
        if not policy.retain_persistent_wall_supported_entry_across_state_relabels:
            if (
                assessment.state
                    not in {"stable", "possible_fall", "falling"}
                or confirmed_rise_recent
                or context.spread + 1e-9
                    < self.parameters.minimum_entry_edge
                or context.spread
                    >= self.parameters.minimum_active_entry_edge - 1e-9
            ):
                return None
        retained_wall = next(
            (
                (price, bonds) for price, bonds in tick.bids
                if abs(price - order.visible_wall_entry_price) <= 1e-9
                and bonds + 1e-9 >= context.wall_threshold_bonds
            ),
            None,
        )
        if retained_wall is None:
            return None
        first_seen_ms = self.visible_bid_wall_first_seen_ms.get(
            round(retained_wall[0], 6),
        )
        if (
            first_seen_ms is None
            or first_seen_ms > order.created_ms
            or order.limit_price + 1e-9 < retained_wall[0]
            or order.limit_price - retained_wall[0]
                > policy.persistent_wall_supported_entry_maximum_wall_premium
                    + 1e-9
        ):
            return None
        return retained_wall

    def _sell_is_reasonable(
        self, price: float, context: MakerDecisionContext,
    ) -> bool:
        if price + self.parameters.fair_price_tolerance >= context.reference_price:
            return True
        return (
            context.has_ask_supply
            and price + self.parameters.book_safety_distance + 1e-9
                >= context.reference_price
        )

    def _base_high_sell_is_safe(
        self, price: float, context: MakerDecisionContext,
        policy: MakerPolicyProfile, market_state: str,
        recent_lower_sell_bonds: float = 0.0,
        persistent_lower_bid: bool = False,
        repeated_turn_replenishment_price: float | None = None,
        recent_trade_reference: float | None = None,
        recent_priority_extra_exit_price: float | None = None,
        recent_priority_extra_exit_age_ms: int | None = None,
    ) -> bool:
        """A base sale needs a future replenishment edge, not merely fair value.

        Extra inventory bought below fair value may exit around fair value for
        turnover.  Base inventory is different: selling it creates a deficit,
        so the sale price must already stand sufficiently above the causal fair
        reference.  A moderate 0.20--0.50 edge additionally needs a thick ask
        wall as replenishment protection; an edge of 0.50 or more is itself the
        safety margin.
        """
        edge = price - context.reference_price
        isolation_seconds = (
            policy.priority_rising_base_short_after_extra_exit_isolation_seconds
        )
        if (
            isolation_seconds > 0
            and market_state == "rising"
            and repeated_turn_replenishment_price is None
            and recent_priority_extra_exit_price is not None
            and recent_priority_extra_exit_age_ms is not None
            and 0 <= recent_priority_extra_exit_age_ms
                <= isolation_seconds * 1_000
            and price - recent_priority_extra_exit_price
                <= self.parameters.price_cluster_width + 1e-9
            and recent_lower_sell_bonds + 1e-9
                < self.parameters.order_quantity_bonds
            and recent_trade_reference is not None
            and price - recent_trade_reference + 1e-9
                < self.parameters.minimum_active_entry_edge
        ):
            # The extra lot has only just been flattened.  Selling the customer
            # base in the same rising price cluster is a new short, not a
            # continuation of that harmless long exit.  Require either a new
            # price, a deep premium, fresh executable low-side evidence or the
            # separately validated repeated-corridor permission.
            return False
        if (
            policy.require_rising_base_short_recent_trade_premium_and_supply
            and market_state in {"possible_rise", "rising"}
            and repeated_turn_replenishment_price is None
            and recent_trade_reference is not None
        ):
            # In positive momentum an old anchor can lag the market by an
            # entire quote corridor and manufacture a false "high".  Selling
            # the customer base is a new economic short, so revalue it against
            # recent real prints and require current overhead supply.  Opening
            # gaps with no intraday trade reference retain the parent logic;
            # a causally repeated two-sided corridor is handled separately.
            recent_trade_edge = price - recent_trade_reference
            recent_trade_gate_passes = (
                recent_trade_edge
                    + self.parameters.fair_price_tolerance + 1e-9
                    >= self.parameters.minimum_base_high_sell_edge
                and context.has_ask_supply
            )
            if not recent_trade_gate_passes:
                return False
            reliable_reference_edge = (
                policy.minimum_rising_base_short_reliable_reference_edge
            )
            if (
                reliable_reference_edge is not None
                and context.reliable_anchor
                and price - context.reference_price
                    + self.parameters.fair_price_tolerance + 1e-9
                    < reliable_reference_edge
            ):
                # A reliable current trade anchor is stronger than an older
                # five-minute median.  In positive momentum, that older
                # reference and visible supply cannot authorize a customer-
                # base short below the live causal fair region.
                return False
            return True
        if edge + 1e-9 >= self.parameters.minimum_active_entry_edge:
            return True
        downtrend_turn_edge = self._downtrend_turn_edge(policy)
        if (
            policy.enable_repeated_two_sided_base_turn
            and repeated_turn_replenishment_price is not None
            and market_state
                in {"stable", "possible_rise", "possible_fall", "falling"}
            and not context.breakout_support_strong
            and price - repeated_turn_replenishment_price + 1e-9
                >= downtrend_turn_edge
        ):
            # Repeated full-sized prints at the same upper and lower clusters
            # establish an executable oscillation even when aggregate volume
            # still labels the state possible_rise.  This is a sell-first T
            # with a pre-existing base lot, never naked shorting.
            return True
        if (
            policy.enable_downtrend_wide_spread_base_turn
            and market_state in {"possible_fall", "falling"}
            and not context.breakout_support_strong
            and context.spread - self.parameters.price_tick + 1e-9
                >= downtrend_turn_edge
            and (
                recent_lower_sell_bonds + 1e-9
                    >= self.parameters.order_quantity_bonds
                or (
                    policy.enable_persistent_bid_downtrend_turn
                    and persistent_lower_bid
                )
            )
        ):
            # In a declining oscillation, verified recent lower-side selling plus a
            # wide executable inside market makes the current ask a high-side
            # base sale even when it is only modestly above the midpoint.  The
            # resulting inventory deficit is replenished at the current bid;
            # this never permits negative inventory.
            return True
        minimum_edge = (
            policy.minimum_wall_supported_base_high_sell_edge_override
            if (
                policy.enable_priority_v11_extensions
                and policy.minimum_wall_supported_base_high_sell_edge_override
                    is not None
            )
            else (
                self.parameters.minimum_base_high_sell_edge
                if policy.enable_priority_v11_extensions
                else self.parameters.minimum_entry_edge
            )
        )
        return (
            edge + self.parameters.fair_price_tolerance + 1e-9
                >= minimum_edge
            and context.has_ask_supply
        )

    def _is_medium_wall_supported_base_short(
        self, price: float, context: MakerDecisionContext,
        repeated_turn_replenishment_price: float | None,
    ) -> bool:
        """Identify the moderate, wall-dependent base-short authorization.

        Classify the sale against the current causal working fair value.  The
        recent-trade check in positive momentum remains an additional entry
        guard against stale anchors; it must not replace an already updated
        working fair value and exaggerate a moderate sale into a deep one.
        Explicitly exclude a repeated high/low corridor and a 0.50-yuan-or-
        deeper premium to the working fair value, both of which have their own
        stronger replenishment thesis.
        """

        if repeated_turn_replenishment_price is not None:
            return False
        reference = context.reference_price
        edge = price - reference
        return (
            context.has_ask_supply
            and edge + self.parameters.fair_price_tolerance + 1e-9
                >= self.parameters.minimum_base_high_sell_edge
            and edge + 1e-9
                < self.parameters.minimum_active_entry_edge
        )

    def _downtrend_turn_edge(self, policy: MakerPolicyProfile) -> float:
        return (
            policy.minimum_downtrend_turn_edge_override
            if policy.minimum_downtrend_turn_edge_override is not None
            else self.parameters.minimum_entry_edge
        )

    def _persistent_bid_corridor(self, tick: ReplayTick) -> bool:
        """Confirm that the current lower bid has persisted causally.

        A sell-first T can use a stable lower inside bid as the planned
        replenishment corridor even when a three-second Level 1 frame retains
        only the final high-side print.  The newest consecutive bid run must
        remain within the existing price-cluster width for at least the same
        15-second persistence used by the inside-market reference.
        """

        if tick.bid1 <= 0:
            return False
        cutoff = (
            tick.market_ts_ms
            - self.parameters.book_reference_window_seconds * 1_000
        )
        selected = []
        for quote in reversed(self.analyzer.book_quotes):
            if quote.market_ts_ms < cutoff:
                break
            if (
                abs(quote.bid - tick.bid1)
                > self.parameters.price_cluster_width + 1e-9
            ):
                break
            selected.append(quote)
        return (
            len(selected) >= 2
            and selected[0].market_ts_ms - selected[-1].market_ts_ms
                >= self.parameters.minimum_book_reference_seconds * 1_000
        )

    def _repeated_two_sided_turn_replenishment_price(
        self, tick: ReplayTick, high_price: float,
        policy: MakerPolicyProfile,
    ) -> float | None:
        """Return the causal low corridor after repeated high/low alternation.

        The pattern must contain at least two full-sized events in the same
        upper cluster and two in one lower cluster, compressed into at least
        four alternating side runs ending at the lower side.  A single high
        print followed by one low print remains only hindsight, especially in
        a possible-rise state.
        """

        if (
            not policy.enable_repeated_two_sided_base_turn
            or high_price <= 0
        ):
            return None
        cutoff = (
            tick.market_ts_ms
            - policy.repeated_turn_window_seconds * 1_000
        )
        upper = [
            event for event in self.analyzer.trade_evidence
            if event.market_ts_ms >= cutoff
            and event.side == "buy"
            and abs(event.price - high_price)
                <= self.parameters.price_cluster_width + 1e-9
        ]
        lower_candidates = [
            event for event in self.analyzer.trade_evidence
            if event.market_ts_ms >= cutoff
            and event.side == "sell"
            and high_price - event.price + 1e-9
                >= self._downtrend_turn_edge(policy)
        ]
        if not upper or not lower_candidates:
            return None
        latest_lower = max(
            lower_candidates, key=lambda event: event.market_ts_ms,
        )
        lower = [
            event for event in lower_candidates
            if abs(event.price - latest_lower.price)
                <= self.parameters.price_cluster_width + 1e-9
        ]
        minimum_bonds = policy.minimum_repeated_turn_side_bonds
        minimum_events = policy.minimum_repeated_turn_side_events
        if (
            len(upper) < minimum_events
            or len(lower) < minimum_events
            or sum(event.bonds for event in upper) + 1e-9 < minimum_bonds
            or sum(event.bonds for event in lower) + 1e-9 < minimum_bonds
            or tick.market_ts_ms - latest_lower.market_ts_ms
                > policy.repeated_turn_latest_low_seconds * 1_000
        ):
            return None
        clustered = sorted(
            [(event, "buy") for event in upper]
            + [(event, "sell") for event in lower],
            key=lambda item: item[0].market_ts_ms,
        )
        runs: list[str] = []
        for _, side in clustered:
            if not runs or runs[-1] != side:
                runs.append(side)
        if (
            len(runs) < policy.minimum_repeated_turn_runs
            or runs[-1] != "sell"
        ):
            return None
        replenishment_price = _floor_to_tick(
            latest_lower.price + self.parameters.price_tick,
            self.parameters.price_tick,
        )
        if (
            high_price - replenishment_price + 1e-9
                < self._downtrend_turn_edge(policy)
        ):
            return None
        return replenishment_price

    def _recent_completed_base_turn_replenishment_price(
        self, account: MakerAccount, tick: ReplayTick, high_price: float,
    ) -> float | None:
        """Reuse a just-completed high/low corridor while it still exists.

        A completed base sale and replenishment are causal proof that both
        sides were executable.  The same upper cluster may be quoted again
        after a fresh lower-side sell, even if a lifted bid temporarily labels
        the state possible_rise.  The memory is deliberately short, the upper
        price must be unchanged, and the lower corridor may drift upward by at
        most 0.10 yuan; a new market regime must not inherit an old T range.
        """

        policy = account.policy
        if (
            not policy.enable_recent_completed_base_turn_repeat
            or high_price <= 0
            or tick.bid1 <= 0
            or account.last_completed_base_turn_ts_ms <= 0
            or tick.market_ts_ms < account.last_completed_base_turn_ts_ms
            or tick.market_ts_ms - account.last_completed_base_turn_ts_ms
                > policy.recent_completed_base_turn_window_seconds * 1_000
            or abs(
                high_price - account.last_completed_base_turn_sell_price
            ) > self.parameters.price_cluster_width + 1e-9
            or tick.bid1 - account.last_completed_base_turn_buy_price
                > policy.maximum_completed_base_turn_low_drift + 1e-9
        ):
            return None
        replenishment_price = _floor_to_tick(
            tick.bid1 + self.parameters.price_tick,
            self.parameters.price_tick,
        )
        edge = self._downtrend_turn_edge(policy)
        if high_price - replenishment_price + 1e-9 < edge:
            return None
        lower_sell_bonds = sum(
            event.bonds for event in self.analyzer.trade_evidence
            if account.last_completed_base_turn_ts_ms < event.market_ts_ms
                <= tick.market_ts_ms
            and event.side == "sell"
            and high_price - event.price + 1e-9 >= edge
        )
        if (
            lower_sell_bonds + 1e-9
            < policy.minimum_completed_base_turn_lower_sell_bonds
        ):
            return None
        return replenishment_price

    def _post_replenishment_high_ask_cluster_price(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, context: MakerDecisionContext, *,
        confirmed_rise_recent: bool,
    ) -> float | None:
        """Return a causal ask2--ask5 pre-position price after a base turn.

        The just-completed base sale and recovery prove an executable high/low
        corridor.  A still-visible concentrated upper cluster may therefore be
        joined one tick ahead before a later active buy sweeps through it.  The
        triggering buy is never reused: this helper only creates a resting
        order for future ticks.
        """

        policy = account.policy
        if not (
            policy.enable_post_replenishment_high_ask_cluster_preposition
            and account.fill_mode == "priority"
            and account.customer_base_short_bonds <= 1e-9
            and account.extra_inventory_bonds <= 1e-9
            and account.last_completed_base_turn_ts_ms > 0
            and tick.market_ts_ms >= account.last_completed_base_turn_ts_ms
            and tick.market_ts_ms - account.last_completed_base_turn_ts_ms
                <= policy.high_ask_cluster_preposition_seconds * 1_000
            and account.last_completed_base_turn_sell_price > 0
            and account.last_completed_base_turn_buy_price > 0
            and assessment.state in {"stable", "possible_fall", "falling"}
            and not confirmed_rise_recent
            and context.reference_price > 0
            and tick.ask1 > tick.bid1 > 0
            and len(tick.asks) >= 2
        ):
            return None

        previous_high = account.last_completed_base_turn_sell_price
        previous_low = account.last_completed_base_turn_buy_price
        for level_price, _ in tick.asks[1:5]:
            if (
                level_price <= tick.ask1 + 1e-9
                or level_price - tick.ask1 + 1e-9
                    < policy.high_ask_cluster_minimum_inside_gap
                or abs(level_price - previous_high)
                    > policy.high_ask_cluster_maximum_sale_distance + 1e-9
            ):
                continue
            clustered_supply = sum(
                bonds for price, bonds in tick.asks[1:5]
                if abs(price - level_price)
                    <= self.parameters.price_cluster_width + 1e-9
            )
            candidate = _floor_to_tick(
                level_price - self.parameters.price_tick,
                self.parameters.price_tick,
            )
            if (
                clustered_supply + 1e-9
                    < policy.high_ask_cluster_minimum_supply_bonds
                or candidate <= tick.ask1 + 1e-9
                or candidate - previous_low + 1e-9
                    < self._downtrend_turn_edge(policy)
                or candidate - context.reference_price
                    + self.parameters.fair_price_tolerance + 1e-9
                    < self.parameters.minimum_base_high_sell_edge
            ):
                continue
            return candidate
        return None

    def _refresh_orders(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, *, persist: bool,
    ) -> None:
        anchor = self.analyzer.last_anchor
        context = self._decision_context(tick, account.policy)
        v11 = account.policy.enable_priority_v11_extensions
        confirmed_rise_recent = (
            self._confirmed_rise_is_recent(tick, account.policy) if v11 else False
        )
        desired_buy: tuple[float, float, float | None] | None = None
        desired_buy_boundary: float | None = None
        desired_buy_kind = "low_bid_reversion"
        visible_downtrend_wall_price = None
        visible_downtrend_wall_bonds = 0.0
        falling_profitable_reentry_active = False
        inventory_deficit = max(
            0.0, account.initial_inventory - account.inventory
        )
        inventory_turn_replenishment = min(
            account.pending_inventory_turn_quantity,
            max(0.0, account.maximum_inventory - account.inventory),
        )
        in_entry_window = self._entry_window_for_policy(
            tick.market_time, account.policy, tick.market_date,
        )
        if (
            context.reference_price > 0
            and in_entry_window
            and tick.bid1 > 0 and tick.ask1 > tick.bid1
        ):
            price = tick.bid1
            falling_profitable_reentry_cap = None
            falling_profitable_reentry_active = (
                account.policy.enable_falling_profitable_bid_exit
                and inventory_deficit <= 1e-9
                and account.last_falling_profitable_exit_price > 0
                and tick.market_ts_ms
                    - account.last_falling_profitable_exit_ts_ms
                    <= account.policy.falling_profitable_reentry_cooldown_seconds
                        * 1_000
                and assessment.state in {"possible_fall", "falling"}
                and not confirmed_rise_recent
            )
            persistent_wall_supported_entry = (
                self._persistent_wall_supported_falling_extra_entry(
                    account, tick, assessment, context,
                    confirmed_rise_recent=confirmed_rise_recent,
                    falling_profitable_reentry_active=(
                        falling_profitable_reentry_active
                    ),
                )
            )
            if falling_profitable_reentry_active:
                falling_profitable_reentry_cap = (
                    account.last_falling_profitable_exit_price
                    - account.policy.minimum_falling_profitable_reentry_improvement
                )
                price = min(price, falling_profitable_reentry_cap)
            average_sale_price = None
            maximum_replenishment_price = None
            minimum_replenishment_edge = self.parameters.minimum_entry_edge
            planned_downtrend_replenishment = False
            planned_repeated_turn_replenishment = False
            planned_profitable_base_replenishment = False
            planned_dynamic_base_replenishment = False
            replenishment_needed = inventory_deficit
            replenishment_quantity = account.replenishment_quantity
            replenishment_sale_value = account.replenishment_sale_value
            inventory_turn_plan_pending = (
                inventory_deficit <= 1e-9
                and inventory_turn_replenishment > 1e-9
                and account.pending_inventory_turn_quantity > 1e-9
            )
            if inventory_turn_plan_pending:
                replenishment_needed = inventory_turn_replenishment
                replenishment_quantity = account.pending_inventory_turn_quantity
                replenishment_sale_value = (
                    account.pending_inventory_turn_sale_value
                )
            if replenishment_needed > 1e-9 and replenishment_quantity > 1e-9:
                average_sale_price = (
                    replenishment_sale_value / replenishment_quantity
                )
                repeated_turn_plan_pending = (
                    account.policy.enable_repeated_two_sided_base_turn
                    and account.pending_repeated_turn_replenishment_price > 0
                )
                minimum_replenishment_edge = (
                    self._downtrend_turn_edge(account.policy)
                    if inventory_turn_plan_pending or (
                        repeated_turn_plan_pending
                        and account.policy
                            .allow_repeated_replenishment_to_downtrend_edge
                    )
                    else self.parameters.minimum_entry_edge
                )
                maximum_replenishment_price = max(
                    self.parameters.price_tick,
                    average_sale_price - minimum_replenishment_edge,
                )
                price = min(price, maximum_replenishment_price)
                planned_profitable_base_replenishment = (
                    account.policy
                        .enable_profitable_visible_bid_base_replenishment
                    and inventory_deficit > 1e-9
                    and average_sale_price - tick.bid1 + 1e-9
                        >= (
                            account.policy
                                .minimum_profitable_visible_bid_base_replenishment_edge_override
                            if account.policy
                                .minimum_profitable_visible_bid_base_replenishment_edge_override
                                is not None
                            else self.parameters.minimum_active_entry_edge
                        )
                    and tick.bid1_bonds + 1e-9
                        >= self.parameters.order_quantity_bonds
                )
                if planned_profitable_base_replenishment:
                    # A currently displayed low-side bid already leaves at
                    # least the ordinary active-entry edge against an
                    # outstanding customer-base short.  Improve that bid
                    # passively instead of letting an old iron-floor target
                    # turn a profitable replenishment into a windfall bet.
                    # No existing trade is reused; only later market flow can
                    # fill the order.
                    minimum_replenishment_edge = max(
                        minimum_replenishment_edge,
                        (
                            account.policy
                                .minimum_profitable_visible_bid_base_replenishment_edge_override
                            if account.policy
                                .minimum_profitable_visible_bid_base_replenishment_edge_override
                                is not None
                            else self.parameters.minimum_active_entry_edge
                        ),
                    )
                    maximum_replenishment_price = max(
                        self.parameters.price_tick,
                        average_sale_price - minimum_replenishment_edge,
                    )
                    price = min(tick.bid1, maximum_replenishment_price)
                planned_dynamic_base_replenishment = (
                    account.policy
                        .enable_continuous_dynamic_base_short_replenishment
                    and inventory_deficit > 1e-9
                    and not inventory_turn_plan_pending
                    and context.reference_source != "previous_close"
                )
                if planned_dynamic_base_replenishment:
                    # A base deficit is an economic short, so keep a passive
                    # recovery intention alive without requiring a fixed
                    # profit or a 5,000-bond extra-entry wall.  The live quote
                    # remains bounded by the causal fair region and the
                    # existing small stop-loss allowance; strong invalidation
                    # continues to use the separate active-stop path.
                    maximum_replenishment_price = min(
                        context.reference_price
                            + self.parameters.fair_price_tolerance,
                        average_sale_price
                            + account.policy
                                .dynamic_base_replenishment_maximum_loss,
                    )
                    price = min(tick.bid1, maximum_replenishment_price)
                planned_downtrend_replenishment = (
                    account.policy.enable_downtrend_wide_spread_base_turn
                    and assessment.state in {"stable", "possible_fall", "falling"}
                    and average_sale_price - tick.bid1 + 1e-9
                        >= (
                            self._downtrend_turn_edge(account.policy)
                            if inventory_turn_plan_pending
                            else self.parameters.minimum_entry_edge
                        )
                    and (
                        self._recent_lower_sell_bonds(
                            tick, average_sale_price,
                        ) + 1e-9 >= self.parameters.order_quantity_bonds
                        or (
                            account.policy.enable_persistent_bid_downtrend_turn
                            and self._persistent_bid_corridor(tick)
                        )
                    )
                )
                if repeated_turn_plan_pending:
                    price = min(
                        price,
                        account.pending_repeated_turn_replenishment_price,
                    )
                    planned_repeated_turn_replenishment = (
                        average_sale_price - price + 1e-9
                            >= self._downtrend_turn_edge(account.policy)
                    )
            planned_base_replenishment = (
                planned_downtrend_replenishment
                or planned_repeated_turn_replenishment
                or planned_profitable_base_replenishment
                or planned_dynamic_base_replenishment
            )
            if (
                account.policy.enable_visible_wall_anchored_downtrend_entry
                and inventory_deficit <= 1e-9
                and assessment.state in {"possible_fall", "falling"}
                and not confirmed_rise_recent
                and not planned_base_replenishment
                and context.spread + 1e-9
                    < self.parameters.minimum_active_entry_edge
                and context.reference_price - tick.bid1 + 1e-9
                    < self.parameters.minimum_active_entry_edge
            ):
                visible_walls = [
                    (bid_price, bid_bonds)
                    for bid_price, bid_bonds in tick.bids
                    if bid_price > 0
                    and bid_price <= tick.bid1 + 1e-9
                    and tick.bid1 - bid_price
                        <= self.parameters.maximum_downtrend_wall_anchor_gap
                            + 1e-9
                    and bid_bonds + 1e-9 >= context.wall_threshold_bonds
                ]
                if visible_walls:
                    (
                        visible_downtrend_wall_price,
                        visible_downtrend_wall_bonds,
                    ) = max(visible_walls, key=lambda wall: wall[0])
                    # In a falling tape, a staircase of small bids may lift
                    # bid1 too far from the actual exit cushion.  The user
                    # would still quote, but only a few cents to at most
                    # 0.10 yuan above the currently visible concentrated
                    # wall.  Keep the order inside that wall-backed zone
                    # instead of following the small staircase to bid1.
                    price = min(
                        price,
                        visible_downtrend_wall_price
                            + self.parameters.maximum_downtrend_wall_entry_premium,
                    )
            if (
                v11
                and
                assessment.iron_floor_price is not None
                and assessment.state != "rising"
                and not confirmed_rise_recent
                and not planned_base_replenishment
                and not (
                    account.policy
                        .prefer_fresh_lower_visible_wall_after_base_replenishment
                    and visible_downtrend_wall_price is not None
                    and account.last_base_replenishment_price > 0
                    and account.last_base_replenishment_ts_ms > 0
                    and 0 < (
                        tick.market_ts_ms
                        - account.last_base_replenishment_ts_ms
                    ) <= self.parameters.evidence_half_life_seconds * 1_000
                    and account.last_base_replenishment_price - price + 1e-9
                        >= account.policy
                            .minimum_supported_post_replenishment_gap
                )
                and tick.bid1 - assessment.iron_floor_price + 1e-9
                    > self.parameters.maximum_iron_floor_entry_premium
            ):
                # A recently observed exceptional support wall defines the
                # attractive low-entry zone even after it falls below Level
                # 1's visible five levels. Do not chase a staircase of small
                # bids far above that remembered safety source.
                price = min(
                    price,
                    assessment.iron_floor_price
                        + self.parameters.maximum_iron_floor_entry_premium,
                )
            if account.fill_mode == "priority":
                improved = price + self.parameters.price_tick
                if (
                    price >= tick.bid1
                    and improved < tick.ask1
                    and (
                        maximum_replenishment_price is None
                        or improved <= maximum_replenishment_price
                    )
                ):
                    price = improved
            if falling_profitable_reentry_cap is not None:
                price = min(price, falling_profitable_reentry_cap)
            price = _floor_to_tick(price, self.parameters.price_tick)
            extra_entry_reference = self._ordinary_extra_entry_reference(
                account, tick, context.reference_price,
            )
            fair_value_entry_edge = extra_entry_reference - price
            entry_edge = fair_value_entry_edge
            round_trip_safe = True
            if average_sale_price is not None:
                round_trip_safe = (
                    planned_dynamic_base_replenishment
                    or average_sale_price - price + 1e-9
                        >= minimum_replenishment_edge
                )
                entry_edge = max(
                    entry_edge,
                    average_sale_price - price,
                )
            ordinary_entry_safe = self._entry_is_safe(
                entry_edge,
                max(
                    context.bid_support_bonds,
                    visible_downtrend_wall_bonds,
                ),
            )
            entry_safe = ordinary_entry_safe
            if planned_base_replenishment:
                # The high-side base sale was justified by this already-seen
                # lower-side turnover.  Replenishing at that side closes the
                # planned T and restores base inventory; it is not a new extra
                # position that must wait for the remembered deep wall.
                entry_safe = True
            if (
                v11
                and
                not entry_safe
                and context.reference_source == "persistent_inside_market"
                and context.spread + 1e-9
                    >= self.parameters.minimum_entry_edge
                and context.has_bid_support
            ):
                # A stable, wide inside market is itself the working space for
                # passive T-making.  Do not cancel the bid merely because the
                # bid-to-midpoint distance is slightly below 0.20.
                entry_safe = True
            supported_post_replenishment_entry = (
                self._supported_post_replenishment_extra_entry(
                    account, tick, assessment, context, price,
                    confirmed_rise_recent=confirmed_rise_recent,
                )
            )
            if supported_post_replenishment_entry:
                # The newly observed low-side print, current supported inside
                # market and executable 0.18-yuan corridor form a new T
                # opportunity after base recovery.  This narrow candidate does
                # not lower the ordinary entry threshold globally.
                entry_safe = True
                desired_buy_kind = "post_replenishment_supported_entry"
            if (
                not entry_safe
                and context.breakout_support_strong
                and entry_edge + self.parameters.fair_price_tolerance + 1e-9
                    >= self.parameters.minimum_entry_edge
            ):
                entry_safe = True
            recent_bid_wall_disappearance = (
                self.last_bid_wall_left_book_ts_ms > 0
                and tick.market_ts_ms - self.last_bid_wall_left_book_ts_ms
                    <= self.parameters.wall_memory_seconds * 1_000
            )
            if (
                entry_safe
                and account.policy.require_concentrated_downtrend_bid_support
                and assessment.state in {"stable", "possible_fall", "falling"}
                and not confirmed_rise_recent
                and not planned_base_replenishment
                and not supported_post_replenishment_entry
                and recent_bid_wall_disappearance
                and price - self.last_visible_bid_wall_price
                    > self.parameters.maximum_downtrend_wall_entry_premium
                        + 1e-9
                and context.spread + 1e-9
                    < self.parameters.minimum_active_entry_edge
                and entry_edge + 1e-9
                    < self.parameters.minimum_active_entry_edge
            ):
                # Once a visible wall has left Level 1, its memory may limit
                # chasing but may not keep authorizing a moderate-discount
                # entry.  A new order needs one currently visible wall close
                # enough below it; several small staircase bids do not replace
                # that disappeared execution cushion.
                minimum_wall_price = (
                    price
                    - self.parameters.maximum_downtrend_wall_entry_premium
                )
                current_concentrated_support = any(
                    bid_price <= price + 1e-9
                    and bid_price + 1e-9 >= minimum_wall_price
                    and bid_bonds + 1e-9 >= context.wall_threshold_bonds
                    for bid_price, bid_bonds in tick.bids
                )
                if not current_concentrated_support:
                    entry_safe = False
            if (
                entry_safe
                and falling_profitable_reentry_active
                and fair_value_entry_edge + 1e-9
                    < self.parameters.minimum_active_entry_edge
            ):
                # Selling an extra lot into a bid that is being consumed must
                # genuinely release risk capacity.  During the same falling
                # episode, only rebuild it at a new lower edge that also has a
                # currently visible concentrated wall within 0.10 yuan.  A
                # loose staircase of small bids cannot immediately undo the
                # active exit.
                minimum_wall_price = (
                    price
                    - self.parameters.maximum_downtrend_wall_entry_premium
                )
                current_concentrated_support = any(
                    bid_price <= price + 1e-9
                    and bid_price + 1e-9 >= minimum_wall_price
                    and bid_bonds + 1e-9 >= context.wall_threshold_bonds
                    for bid_price, bid_bonds in tick.bids
                )
                if not current_concentrated_support:
                    entry_safe = False
            unconfirmed_rapid_requote = (
                v11
                and
                assessment.state == "possible_rise"
                and not confirmed_rise_recent
                and not planned_repeated_turn_replenishment
                and assessment.midpoint_change + 1e-9
                    >= self.parameters.minimum_sweep_jump
                and not context.breakout_support_strong
                and fair_value_entry_edge + 1e-9
                    < self.parameters.minimum_active_entry_edge
            )
            if unconfirmed_rapid_requote:
                # A rapidly lifted bid can sit below the new wide-spread
                # midpoint without being genuinely cheap.  While the rise is
                # only provisional, do not let either that midpoint or an old
                # high-side sale manufacture a passive replenishment edge.
                # A real deep discount remains eligible, and a confirmed
                # rising state is evaluated normally on the updated market.
                entry_safe = False
            if persistent_wall_supported_entry is not None:
                candidate_price, candidate_wall_price, candidate_wall_bonds = (
                    persistent_wall_supported_entry
                )
                if (
                    not entry_safe
                    or candidate_price
                        > price + self.parameters.price_tick + 1e-9
                ):
                    # Apply the narrow permission only when it adds a missing
                    # quote or materially improves the parent's stale deep bid.
                    # Capacity and cash are still checked below, and only a
                    # later real sell can fill this passive order.
                    price = candidate_price
                    entry_safe = True
                    desired_buy_kind = (
                        "persistent_wall_supported_falling_entry"
                    )
                    visible_downtrend_wall_price = candidate_wall_price
                    visible_downtrend_wall_bonds = candidate_wall_bonds
            supported_corridor_entry = (
                self._high_side_validated_supported_corridor_entry(
                    account, tick, assessment, context,
                )
            )
            if (
                supported_corridor_entry is not None
                and (
                    not entry_safe
                    or supported_corridor_entry
                        > price + self.parameters.price_tick + 1e-9
                )
            ):
                # This permission places a new passive low-side quote only.
                # It neither crosses the current ask nor changes any base-lot
                # sale or replenishment path.
                price = supported_corridor_entry
                entry_safe = True
                desired_buy_kind = "high_side_validated_corridor_entry"
            persistent_two_sided_corridor_entry = (
                self._persistent_two_sided_wall_corridor_entry(
                    account, tick, assessment, context,
                )
            )
            if persistent_two_sided_corridor_entry is not None:
                (
                    candidate_price,
                    candidate_wall_price,
                    candidate_wall_bonds,
                ) = persistent_two_sided_corridor_entry
                if (
                    not entry_safe
                    or candidate_price
                        > price + self.parameters.price_tick + 1e-9
                ):
                    price = candidate_price
                    entry_safe = True
                    desired_buy_kind = (
                        "persistent_two_sided_wall_corridor_entry"
                    )
                    visible_downtrend_wall_price = candidate_wall_price
                    visible_downtrend_wall_bonds = candidate_wall_bonds
            wide_spread_buy_first_entry = (
                self._persistent_wide_spread_buy_first_entry(
                    account, tick, assessment, context,
                )
            )
            if (
                wide_spread_buy_first_entry is not None
                and (
                    not entry_safe
                    or wide_spread_buy_first_entry
                        > price + self.parameters.price_tick + 1e-9
                )
            ):
                price = wide_spread_buy_first_entry
                entry_safe = True
                desired_buy_kind = "persistent_wide_spread_buy_first_entry"
            opening_entry_edge = extra_entry_reference - price
            if average_sale_price is not None:
                opening_entry_edge = max(
                    opening_entry_edge,
                    average_sale_price - price,
                )
            opening_edge_safe = self.parameters.opening_edge_is_safe(
                tick.market_date,
                tick.market_time,
                opening_entry_edge,
            )
            capacity = max(0.0, account.maximum_inventory - account.inventory)
            affordable = self._affordable_buy_bonds(account, price)
            if inventory_deficit > 1e-9:
                desired_buy_kind = (
                    "dynamic_customer_base_replenish"
                    if planned_dynamic_base_replenishment
                    else (
                        "profitable_visible_bid_base_replenish"
                        if planned_profitable_base_replenishment
                        else "inventory_replenish"
                    )
                )
                quantity = min(
                    inventory_deficit,
                    self.config.maker_paper.order_quantity_bonds,
                    capacity, affordable,
                )
            elif inventory_turn_plan_pending:
                desired_buy_kind = "inventory_turn_replenish"
                quantity = min(
                    inventory_turn_replenishment,
                    self.config.maker_paper.order_quantity_bonds,
                    capacity, affordable,
                )
            else:
                quantity = min(
                    self.config.maker_paper.order_quantity_bonds,
                    capacity, affordable,
                )
            if (
                quantity > 1e-9
                and round_trip_safe
                and entry_safe
                and opening_edge_safe
            ):
                desired_buy = (price, quantity, None)
                if (
                    planned_base_replenishment
                    and maximum_replenishment_price is not None
                ):
                    desired_buy_boundary = maximum_replenishment_price
                    if planned_repeated_turn_replenishment:
                        desired_buy_boundary = min(
                            desired_buy_boundary,
                            account.pending_repeated_turn_replenishment_price,
                        )
                elif (
                    desired_buy_kind == "low_bid_reversion"
                    and ordinary_entry_safe
                ):
                    wall_threshold = (
                        self.parameters.large_wall_multiple
                        * self.parameters.order_quantity_bonds
                    )
                    required_edge = self.parameters.minimum_active_entry_edge
                    if max(
                        context.bid_support_bonds,
                        visible_downtrend_wall_bonds,
                    ) + 1e-9 >= wall_threshold:
                        required_edge = min(
                            required_edge,
                            self.parameters.minimum_entry_edge
                                - self.parameters.fair_price_tolerance,
                        )
                    desired_buy_boundary = (
                        extra_entry_reference - required_edge
                    )
                    if visible_downtrend_wall_price is not None:
                        desired_buy_boundary = min(
                            desired_buy_boundary,
                            visible_downtrend_wall_price
                                + self.parameters
                                    .maximum_downtrend_wall_entry_premium,
                        )
                    if (
                        assessment.iron_floor_price is not None
                        and assessment.state != "rising"
                        and not confirmed_rise_recent
                    ):
                        desired_buy_boundary = min(
                            desired_buy_boundary,
                            assessment.iron_floor_price
                                + self.parameters
                                    .maximum_iron_floor_entry_premium,
                        )
                else:
                    # Pattern-specific permissions (for example a persistent
                    # wall corridor) authorize the exact reviewed level.  Do
                    # not manufacture a wider continuous ceiling that the
                    # decision code never evaluated.
                    desired_buy_boundary = price
                if falling_profitable_reentry_cap is not None:
                    desired_buy_boundary = min(
                        desired_buy_boundary,
                        falling_profitable_reentry_cap,
                    )
                if self.parameters.opening_caution_is_active(
                    tick.market_date, tick.market_time,
                ):
                    opening_reference = max(
                        extra_entry_reference,
                        average_sale_price or 0.0,
                    )
                    desired_buy_boundary = min(
                        desired_buy_boundary,
                        opening_reference
                            - self.parameters.opening_caution_minimum_edge,
                    )
        existing_buy = account.buy_order
        if existing_buy is not None:
            retained_persistent_wall = (
                self._retain_persistent_wall_supported_falling_extra_entry(
                    account, existing_buy, tick, assessment, context,
                    confirmed_rise_recent=confirmed_rise_recent,
                    falling_profitable_reentry_active=(
                        falling_profitable_reentry_active
                    ),
                    in_entry_window=in_entry_window,
                )
            )
            if retained_persistent_wall is not None:
                visible_downtrend_wall_price = retained_persistent_wall[0]
                visible_downtrend_wall_bonds = retained_persistent_wall[1]
                desired_buy_kind = existing_buy.kind
                desired_buy = (
                    existing_buy.limit_price,
                    existing_buy.remaining,
                    existing_buy.target_price,
                )
                desired_buy_boundary = existing_buy.price_boundary
        if (
            desired_buy is None
            and existing_buy is not None
            and account.policy.enable_visible_wall_anchored_downtrend_entry
            and existing_buy.kind == "low_bid_reversion"
            and existing_buy.visible_wall_entry_price > 0
            and assessment.state == "stable"
            and not confirmed_rise_recent
            and in_entry_window
        ):
            retained_wall = next(
                (
                    (bid_price, bid_bonds)
                    for bid_price, bid_bonds in tick.bids
                    if abs(
                        bid_price - existing_buy.visible_wall_entry_price
                    ) <= self.parameters.price_cluster_width + 1e-9
                    and bid_bonds + 1e-9 >= context.wall_threshold_bonds
                ),
                None,
            )
            if (
                retained_wall is not None
                and existing_buy.limit_price + 1e-9 >= retained_wall[0]
                and existing_buy.limit_price - retained_wall[0]
                    <= self.parameters.maximum_downtrend_wall_entry_premium
                        + 1e-9
                and tick.ask1 - existing_buy.limit_price + 1e-9
                    >= self._downtrend_turn_edge(account.policy)
            ):
                visible_downtrend_wall_price = retained_wall[0]
                visible_downtrend_wall_bonds = retained_wall[1]
                desired_buy_kind = existing_buy.kind
                desired_buy = (
                    existing_buy.limit_price,
                    existing_buy.remaining,
                    existing_buy.target_price,
                )
                desired_buy_boundary = existing_buy.price_boundary
        self._replace_buy(
            account, tick, desired_buy, desired_buy_kind,
            price_boundary=desired_buy_boundary,
            market_state=assessment.state, persist=persist,
        )
        if account.buy_order is not None and desired_buy is not None:
            if (
                visible_downtrend_wall_price is not None
                and account.buy_order.limit_price + 1e-9
                    >= visible_downtrend_wall_price
                and account.buy_order.limit_price - visible_downtrend_wall_price
                    <= self.parameters.maximum_downtrend_wall_entry_premium
                        + 1e-9
            ):
                account.buy_order.visible_wall_entry_price = (
                    visible_downtrend_wall_price
                )
            else:
                account.buy_order.visible_wall_entry_price = 0.0

        desired_lots: set[int] = set()
        has_extra_inventory = any(
            lot.entry_price is not None and lot.remaining_quantity > 1e-9
            for lot in account.lots.values()
        )
        downtrend_turn_while_extra_inventory = False
        if (
            account.policy.enable_downtrend_turn_while_extra_inventory
            and has_extra_inventory
            and assessment.state in {"possible_fall", "falling"}
            and not context.breakout_support_strong
            and context.spread - self.parameters.price_tick + 1e-9
                >= self._downtrend_turn_edge(account.policy)
        ):
            lower_sell_bonds = self._recent_lower_sell_bonds(
                tick,
                tick.ask1,
                minimum_gap=self._downtrend_turn_edge(account.policy),
            )
            downtrend_turn_while_extra_inventory = (
                lower_sell_bonds + 1e-9
                    >= self.parameters.order_quantity_bonds
                or (
                    account.policy.enable_persistent_bid_downtrend_turn
                    and self._persistent_bid_corridor(tick)
                )
            )
        minimum_turnover_edge = (
            self.parameters.minimum_passive_turnover_edge
            if v11
            else self.parameters.legacy_queue_passive_turnover_edge
        )
        if context.reference_price > 0 and tick.ask1 > tick.bid1:
            for lot in list(account.lots.values()):
                if lot.remaining_quantity <= 1e-9:
                    continue
                base_turn_grace_eligible = False
                repeated_turn_replenishment_price = None
                inventory_neutral_downtrend_turn = False
                medium_wall_supported_base_short = False
                desired_sell_kind = "inventory_exit"
                if account.fill_mode == "priority":
                    price = tick.ask1 - self.parameters.price_tick
                else:
                    price = tick.ask1
                if (
                    lot.entry_price is not None
                    and context.breakout_support_strong
                ):
                    support_quote = (
                        context.breakout_support_price
                        - self.parameters.price_tick
                        if account.fill_mode == "priority"
                        else context.breakout_support_price
                    )
                    price = max(price, support_quote)
                if lot.entry_price is None:
                    sweep_recovery_target = (
                        self._priority_sweep_recovery_target(
                            account, lot, tick,
                        )
                    )
                    high_cluster_preposition = (
                        self._post_replenishment_high_ask_cluster_price(
                            account, tick, assessment, context,
                            confirmed_rise_recent=confirmed_rise_recent,
                        )
                    )
                    if has_extra_inventory:
                        # Quote the one standard-sized extra T lot first. Do
                        # not expose the base lot at the same price and let one
                        # market print sell both before the new state can be
                        # reassessed.
                        continue
                    if high_cluster_preposition is not None:
                        price = high_cluster_preposition
                        desired_sell_kind = (
                            "high_ask_cluster_base_preposition"
                        )
                        # The completed turn already proved the corresponding
                        # low side.  Preserve that price as the recovery plan
                        # if this pre-positioned high order later fills.
                        repeated_turn_replenishment_price = (
                            account.last_completed_base_turn_buy_price
                        )
                    elif sweep_recovery_target is not None:
                        price = sweep_recovery_target
                    else:
                        if not confirmed_rise_recent:
                            repeated_turn_replenishment_price = (
                                self._repeated_two_sided_turn_replenishment_price(
                                    tick, price, account.policy,
                                )
                            )
                            if repeated_turn_replenishment_price is None:
                                repeated_turn_replenishment_price = (
                                    self._recent_completed_base_turn_replenishment_price(
                                        account, tick, price,
                                    )
                                )
                        recent_lower_sell_bonds = self._recent_lower_sell_bonds(
                            tick, price,
                            minimum_gap=self._downtrend_turn_edge(
                                account.policy,
                            ),
                        )
                        persistent_lower_bid = self._persistent_bid_corridor(tick)
                        recent_trade_reference = (
                            self.analyzer.recent_trade_reference(
                                tick.market_ts_ms,
                                self.parameters
                                    .market_temperature_window_seconds,
                            )
                        )
                        if not self._base_high_sell_is_safe(
                            price, context, account.policy, assessment.state,
                            recent_lower_sell_bonds,
                            persistent_lower_bid=persistent_lower_bid,
                            repeated_turn_replenishment_price=(
                                repeated_turn_replenishment_price
                            ),
                            recent_trade_reference=recent_trade_reference,
                            recent_priority_extra_exit_price=(
                                account.last_priority_extra_inventory_exit_price
                                if account
                                    .last_priority_extra_inventory_exit_ts_ms > 0
                                else None
                            ),
                            recent_priority_extra_exit_age_ms=(
                                tick.market_ts_ms
                                - account
                                    .last_priority_extra_inventory_exit_ts_ms
                                if account
                                    .last_priority_extra_inventory_exit_ts_ms > 0
                                else None
                            ),
                        ):
                            continue
                        medium_wall_supported_base_short = (
                            self._is_medium_wall_supported_base_short(
                                price, context,
                                repeated_turn_replenishment_price=(
                                    repeated_turn_replenishment_price
                                ),
                            )
                        )
                        base_turn_grace_eligible = (
                            account.policy
                                .priority_base_turn_stable_context_grace_seconds > 0
                            and assessment.state in {"possible_fall", "falling"}
                            and account.policy.enable_downtrend_wide_spread_base_turn
                            and not context.breakout_support_strong
                            and context.spread - self.parameters.price_tick + 1e-9
                                >= self._downtrend_turn_edge(account.policy)
                            and (
                                recent_lower_sell_bonds + 1e-9
                                    >= self.parameters.order_quantity_bonds
                                or (
                                    account.policy.enable_persistent_bid_downtrend_turn
                                    and persistent_lower_bid
                                )
                            )
                        )
                elif (
                    lot.kind == "inventory_turn_replenish"
                    and price - lot.entry_price + 1e-9
                        < self._downtrend_turn_edge(account.policy)
                    and not (
                        account.policy
                            .allow_fresh_post_replenishment_inventory_turn
                        and downtrend_turn_while_extra_inventory
                        and self._post_replenishment_lower_sell_bonds(
                            tick,
                            price,
                            replenished_ms=lot.opened_ms,
                            minimum_gap=self._downtrend_turn_edge(
                                account.policy,
                            ),
                        ) + 1e-9
                            >= self.parameters.order_quantity_bonds
                    )
                ):
                    # The low leg of an inventory-neutral sell-first turn is
                    # not a fresh extra lot that an old fair-value reference
                    # may immediately sell at the same price.  Queue 1.8 may
                    # reuse it below historical cost only after new lower-side
                    # selling has causally rebuilt a full executable corridor.
                    continue
                elif downtrend_turn_while_extra_inventory:
                    # Inventory is economically fungible for a sell-first T,
                    # but preserve the explicit base lot.  Turn the extra lot
                    # and remember a separate neutral replenishment target so
                    # the low leg cannot be mistaken for a brand-new entry.
                    inventory_neutral_downtrend_turn = True
                elif (
                    lot.kind == "sweep_tail"
                    and lot.target_price is not None
                    and tick.ask1
                        <= lot.entry_price + self.parameters.price_tick + 1e-9
                ):
                    # Keep the immediate post-sweep exit at the exposed upper
                    # level while the final tail is still the visible ask.
                    # Once the book actually jumps or reprices lower, normal
                    # dynamic exit logic resumes.
                    price = max(price, lot.target_price)
                elif (
                    price - lot.entry_price + 1e-9
                    < minimum_turnover_edge
                    and price - context.reference_price + 1e-9
                    < self.parameters.minimum_fair_value_exit_edge
                ):
                    continue
                elif not self._sell_is_reasonable(price, context):
                    continue
                price = _floor_to_tick(price, self.parameters.price_tick)
                opening_sell_edge = price - context.reference_price
                if lot.entry_price is not None:
                    opening_sell_edge = max(
                        opening_sell_edge,
                        price - lot.entry_price,
                    )
                if not self.parameters.opening_edge_is_safe(
                    tick.market_date,
                    tick.market_time,
                    opening_sell_edge,
                ):
                    continue
                sell_price_boundary = price
                if (
                    lot.entry_price is None
                    and repeated_turn_replenishment_price is not None
                    and price - repeated_turn_replenishment_price + 1e-9
                        >= self._downtrend_turn_edge(account.policy)
                ):
                    sell_price_boundary = (
                        repeated_turn_replenishment_price
                        + self._downtrend_turn_edge(account.policy)
                    )
                elif (
                    lot.entry_price is not None
                    and not inventory_neutral_downtrend_turn
                    and lot.kind not in {
                        "inventory_turn_replenish", "sweep_tail",
                    }
                ):
                    # A normal extra lot may leave by either its own turnover
                    # edge or the fair-value exit edge.  The lower of those two
                    # route floors is the economic minimum, then the shared
                    # reasonableness guard can tighten it.
                    route_floor = min(
                        lot.entry_price + minimum_turnover_edge,
                        context.reference_price
                            + self.parameters.minimum_fair_value_exit_edge,
                    )
                    reasonable_floor = (
                        context.reference_price
                        - (
                            self.parameters.book_safety_distance
                            if context.has_ask_supply
                            else self.parameters.fair_price_tolerance
                        )
                    )
                    sell_price_boundary = max(
                        route_floor, reasonable_floor,
                    )
                if self.parameters.opening_caution_is_active(
                    tick.market_date, tick.market_time,
                ):
                    opening_floors = [
                        context.reference_price
                            + self.parameters.opening_caution_minimum_edge,
                    ]
                    if lot.entry_price is not None:
                        opening_floors.append(
                            lot.entry_price
                                + self.parameters.opening_caution_minimum_edge
                        )
                    sell_price_boundary = max(
                        sell_price_boundary,
                        min(opening_floors),
                    )
                price, queue_position_kind = self._queue_quote_position(
                    account, tick, side="sell", desired_price=price,
                )
                desired_lots.add(lot.db_id)
                existing = account.sell_orders.get(lot.db_id)
                if existing:
                    was_inventory_neutral_downtrend_turn = (
                        existing.inventory_neutral_downtrend_turn
                    )
                    existing.context_invalid_since_ms = 0
                    # Eligibility belongs to the immediately preceding causal
                    # justification, not to the lifetime of this price order.
                    # Clear an earlier downtrend tag once another sell context
                    # is what keeps the same order alive.
                    existing.stable_context_grace_eligible = (
                        base_turn_grace_eligible
                    )
                    # Preserve how this unchanged price order was first
                    # justified.  A later stable/fair-value frame may keep
                    # the same order valid for a different reason, but it
                    # must not erase the causal lower-side corridor that
                    # originally established this sell-first T.
                    existing.base_turn_corridor_origin = (
                        existing.base_turn_corridor_origin
                        or base_turn_grace_eligible
                    )
                    existing.base_turn_replenishment_ceiling = (
                        _floor_to_tick(
                            tick.bid1 + self.parameters.price_tick,
                            self.parameters.price_tick,
                        )
                        if base_turn_grace_eligible else 0.0
                    )
                    existing.repeated_turn_replenishment_price = (
                        repeated_turn_replenishment_price or 0.0
                    )
                    existing.inventory_neutral_downtrend_turn = (
                        inventory_neutral_downtrend_turn
                        or (
                            account.policy
                                .queue_cleared_inventory_turn_corridor_seconds
                                > 0
                            and was_inventory_neutral_downtrend_turn
                        )
                    )
                    existing.medium_wall_supported_base_short = (
                        medium_wall_supported_base_short
                    )
                    if (
                        inventory_neutral_downtrend_turn
                        and not was_inventory_neutral_downtrend_turn
                    ):
                        existing.exact_fill_uncertainty_buffer = max(
                            existing.exact_fill_uncertainty_buffer,
                            account.policy
                                .queue_inventory_turn_exact_fill_buffer_bonds,
                        )
                if (
                    existing
                    and existing.kind == desired_sell_kind
                    and abs(existing.limit_price - price) < 1e-9
                ):
                    continue
                if (
                    existing
                    and account.policy
                        .retain_queue_cleared_inventory_turn_while_live_corridor
                    and price
                        < existing.limit_price
                            - self.parameters.price_tick / 2
                    and self._retain_queue_cleared_inventory_turn_corridor(
                        account, lot, existing, tick, assessment, context,
                    )
                ):
                    # A lower current ask is not a reason to abandon an
                    # already queue-leading high leg while the lower-side
                    # corridor remains live.  Keep the better price and the
                    # earned queue position; do not chase the market down.
                    continue
                if existing and self._retain_queue_cleared_sell_on_worse_reprice(
                    account, lot, existing, tick,
                    desired_price=price,
                    desired_kind=desired_sell_kind,
                    desired_quantity=lot.remaining_quantity,
                    market_state=assessment.state,
                ):
                    continue
                if existing and self._retain_cleared_queue_for_one_tick(
                    account, existing, tick=tick, desired_price=price,
                    desired_kind=desired_sell_kind,
                    desired_quantity=lot.remaining_quantity,
                ):
                    continue
                if existing:
                    self._cancel_order(account, existing, tick, "maker_reprice", persist)
                queue = self._queue_ahead_at_quote(
                    tick, side="sell", price=price,
                    queue_position_kind=queue_position_kind,
                )
                if account.fill_mode == "priority" and price < tick.ask1:
                    queue = 0.0
                new_order = self._new_order(
                    account, tick, side="sell", kind=desired_sell_kind,
                    lot_id=lot.db_id, price=price,
                    quantity=lot.remaining_quantity, queue_ahead=queue,
                    target_price=price,
                    price_boundary=sell_price_boundary, persist=persist,
                    repeated_turn_replenishment_price=(
                        repeated_turn_replenishment_price or 0.0
                    ),
                    medium_wall_supported_base_short=(
                        medium_wall_supported_base_short
                    ),
                    exact_fill_uncertainty_buffer=(
                        account.policy
                            .queue_inventory_turn_exact_fill_buffer_bonds
                        if (
                            inventory_neutral_downtrend_turn
                            or lot.kind == "inventory_turn_replenish"
                        )
                        else 0.0
                    ),
                    queue_position_kind=queue_position_kind,
                )
                new_order.stable_context_grace_eligible = (
                    base_turn_grace_eligible
                )
                new_order.base_turn_corridor_origin = (
                    base_turn_grace_eligible
                )
                new_order.base_turn_replenishment_ceiling = (
                    _floor_to_tick(
                        tick.bid1 + self.parameters.price_tick,
                        self.parameters.price_tick,
                    )
                    if base_turn_grace_eligible else 0.0
                )
                new_order.inventory_neutral_downtrend_turn = (
                    inventory_neutral_downtrend_turn
                )
                account.sell_orders[lot.db_id] = new_order
        for lot_id, order in list(account.sell_orders.items()):
            if lot_id not in desired_lots:
                lot = account.lots.get(lot_id)
                if (
                    lot is not None
                    and (
                        self._retain_priority_base_turn_stable_context_grace(
                            account, lot, order, tick, assessment, context,
                        )
                        or self._retain_priority_base_turn_recent_sell_corridor(
                            account, lot, order, tick, assessment, context,
                        )
                        or self._retain_queue_extra_exit_context_grace(
                            account, lot, order, tick,
                        )
                        or self._retain_queue_queued_inventory_turn_corridor(
                            account, lot, order, tick, assessment, context,
                        )
                        or self._retain_queue_cleared_inventory_turn_corridor(
                            account, lot, order, tick, assessment, context,
                        )
                    )
                ):
                    continue
                self._cancel_order(account, order, tick, "exit_context_changed", persist)

    def _supported_post_replenishment_extra_entry(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, context: MakerDecisionContext,
        price: float, *, confirmed_rise_recent: bool,
    ) -> bool:
        """Recognize a fresh supported low-side turn after base recovery."""

        policy = account.policy
        if not (
            policy.enable_supported_post_replenishment_entry
            and account.fill_mode == "priority"
            and abs(account.inventory - account.initial_inventory) <= 1e-9
            and not any(
                lot.entry_price is not None
                and lot.remaining_quantity > 1e-9
                for lot in account.lots.values()
            )
            and account.last_base_replenishment_price > 0
            and account.last_base_replenishment_ts_ms > 0
            and assessment.state in {"stable", "possible_fall", "falling"}
            and not confirmed_rise_recent
            and not context.breakout_support_strong
            and context.has_bid_support
        ):
            return False

        elapsed_ms = (
            tick.market_ts_ms - account.last_base_replenishment_ts_ms
        )
        if not (
            0 < elapsed_ms
                <= policy.supported_post_replenishment_entry_seconds * 1_000
            and account.last_base_replenishment_price - price + 1e-9
                >= policy.minimum_supported_post_replenishment_gap
            and tick.ask1 - price + 1e-9
                >= self._downtrend_turn_edge(policy)
        ):
            return False

        lower_sell_bonds = sum(
            event.bonds
            for event in self.analyzer.trade_evidence
            if event.side == "sell"
            and event.market_ts_ms > account.last_base_replenishment_ts_ms
            and event.market_ts_ms <= tick.market_ts_ms
            and event.price
                <= account.last_base_replenishment_price
                    - policy.minimum_supported_post_replenishment_gap + 1e-9
        )
        return (
            lower_sell_bonds + 1e-9
                >= policy.minimum_supported_post_replenishment_sell_bonds
        )

    def _priority_sweep_recovery_target(
        self, account: MakerAccount, lot: MakerLot, tick: ReplayTick,
    ) -> float | None:
        """Keep the event-backed sweep target when that buy restored base.

        A sweep target comes from the just-observed exhausted offer cluster,
        not from whether the purchased bonds are accounted for as base or as
        extra inventory.  The permission is deliberately short and is erased
        as soon as the target stops matching the live book, so it cannot turn
        into a stale historical anchor or later resurrect.
        """

        target = lot.target_price
        policy = account.policy
        if not (
            policy.enable_priority_sweep_recovery_target
            and account.fill_mode == "priority"
            and lot.kind == "base"
            and lot.entry_price is None
            and target is not None
            and target > 0
        ):
            return None

        has_extra_inventory = any(
            candidate.entry_price is not None
            and candidate.remaining_quantity > 1e-9
            for candidate in account.lots.values()
        )
        elapsed_ms = tick.market_ts_ms - lot.opened_ms
        same_completed_recovery = (
            account.last_base_replenishment_ts_ms == lot.opened_ms
            and account.last_base_replenishment_price > 0
        )
        tail_still_visible = (
            same_completed_recovery
            and abs(tick.ask1 - account.last_base_replenishment_price)
                <= self.parameters.price_tick + 1e-9
        )
        current_priority_target = _floor_to_tick(
            tick.ask1 - self.parameters.price_tick,
            self.parameters.price_tick,
        )
        target_is_current = (
            abs(current_priority_target - target) <= 1e-9
        )
        still_valid = (
            not has_extra_inventory
            and 0 <= elapsed_ms
            and elapsed_ms
                <= policy.priority_sweep_recovery_target_seconds * 1_000
            and (tail_still_visible or target_is_current)
        )
        if still_valid:
            return target

        lot.target_price = None
        self.store.update_maker_lot(
            lot.db_id, target_price=None,
            updated_market_ts_ms=tick.market_ts_ms,
        )
        return None

    def _retain_priority_base_turn_stable_context_grace(
        self, account: MakerAccount, lot: MakerLot, order: MakerOrder,
        tick: ReplayTick, assessment: MarketAssessment,
        context: MakerDecisionContext,
    ) -> bool:
        """Bridge a brief downtrend-to-stable diagnostic flicker.

        The order must already have been justified as the high leg of a
        sell-first wide-spread turn while the market was falling.  A later
        stable label may briefly suppress the same action even though the
        lower bid corridor and executable spread are unchanged.  Earlier
        profiles preserve the existing first-priority order only for a short
        grace period.  A later candidate may keep it for as long as every live
        corridor condition below remains true; neither form creates a new
        stable-state base sale through this rule.
        """

        grace = account.policy.priority_base_turn_stable_context_grace_seconds
        crosses_morning_close = (
            "11:30:00.000" <= tick.market_time < "13:00:00.000"
        )
        current_target = _floor_to_tick(
            tick.ask1 - self.parameters.price_tick,
            self.parameters.price_tick,
        )
        current_replenishment = _floor_to_tick(
            tick.bid1 + self.parameters.price_tick,
            self.parameters.price_tick,
        )
        lower_bid_shift_preserves_edge = (
            account.policy.retain_priority_base_turn_on_lower_bid_shift
            and order.base_turn_replenishment_ceiling > 0
            and current_replenishment
                <= order.base_turn_replenishment_ceiling + 1e-9
            and assessment.state in {"stable", "possible_fall", "falling"}
        )
        live_lower_side = (
            self._persistent_bid_corridor(tick)
            or lower_bid_shift_preserves_edge
        )
        allowed_state = (
            assessment.state == "stable"
            or lower_bid_shift_preserves_edge
        )
        has_extra_inventory = any(
            candidate.entry_price is not None
            and candidate.remaining_quantity > 1e-9
            for candidate in account.lots.values()
        )
        if not (
            account.fill_mode == "priority"
            and grace > 0
            and lot.entry_price is None
            and order.stable_context_grace_eligible
            and allowed_state
            and not (
                account.policy.retain_priority_base_turn_on_lower_bid_shift
                and has_extra_inventory
            )
            # The tick-driven simulator has no timer event during lunch.  The
            # 11:30 refresh must therefore cancel an expired context instead
            # of leaving a nominal 15-second grace order live until 13:00.
            and not crosses_morning_close
            and not context.breakout_support_strong
            and context.spread - self.parameters.price_tick + 1e-9
                >= self._downtrend_turn_edge(account.policy)
            and live_lower_side
            and abs(current_target - order.limit_price)
                <= self.parameters.price_cluster_width + 1e-9
            and not self._confirmed_rise_is_recent(tick, account.policy)
        ):
            return False
        if order.context_invalid_since_ms <= 0:
            order.context_invalid_since_ms = tick.market_ts_ms
        # V1.12 removes only the arbitrary expiry clock.  Target price,
        # executable edge, persistent low corridor, breakout and confirmed
        # rise checks above remain live on every frame, so a changed regime
        # still withdraws the old order immediately.
        retained = (
            account.policy.retain_priority_base_turn_while_live_corridor
            or tick.market_ts_ms - order.context_invalid_since_ms
                <= grace * 1_000
        )
        if retained:
            order.retained_after_context_loss = True
        return retained

    def _retain_priority_base_turn_recent_sell_corridor(
        self, account: MakerAccount, lot: MakerLot, order: MakerOrder,
        tick: ReplayTick, assessment: MarketAssessment,
        context: MakerDecisionContext,
    ) -> bool:
        """Keep an established high leg while its executable T range survives.

        A downtrend high-side order can remain at the same price through a
        stable frame that independently calls it fair-value-safe.  That
        intermediate label must not erase the order's original corridor
        identity and make a later one-frame weak rise cancel it.  Retention
        still requires recent full-sized lower-side selling, at least the
        configured live replenishment edge, an unchanged upper target, and no
        confirmed rise or breakout.  Stronger possible-rise evidence and a
        real rising state cancel normally.
        """

        policy = account.policy
        if not (
            policy.retain_priority_base_turn_on_recent_sell_corridor
            and account.fill_mode == "priority"
            and lot.entry_price is None
            and order.base_turn_corridor_origin
            and assessment.state
                in {"stable", "possible_rise", "possible_fall", "falling"}
            and assessment.state_score <= 1
            and not context.breakout_support_strong
            and not self._confirmed_rise_is_recent(tick, policy)
            and not (
                "11:30:00.000" <= tick.market_time < "13:00:00.000"
            )
            and not any(
                candidate.entry_price is not None
                and candidate.remaining_quantity > 1e-9
                for candidate in account.lots.values()
            )
        ):
            return False
        current_target = _floor_to_tick(
            tick.ask1 - self.parameters.price_tick,
            self.parameters.price_tick,
        )
        live_replenishment = _floor_to_tick(
            tick.bid1 + self.parameters.price_tick,
            self.parameters.price_tick,
        )
        minimum_edge = self._downtrend_turn_edge(policy)
        if not (
            abs(current_target - order.limit_price)
                <= self.parameters.price_cluster_width + 1e-9
            and order.limit_price - live_replenishment + 1e-9
                >= minimum_edge
            and self._recent_lower_sell_bonds(
                tick, order.limit_price, minimum_gap=minimum_edge,
            ) + 1e-9 >= self.parameters.order_quantity_bonds
        ):
            return False
        order.retained_after_recent_sell_corridor = True
        return True

    def _retain_queue_extra_exit_context_grace(
        self, account: MakerAccount, lot: MakerLot, order: MakerOrder,
        tick: ReplayTick,
    ) -> bool:
        """Keep an extra exit briefly when its context vanishes between frames."""

        grace = account.policy.queue_extra_exit_context_grace_seconds
        if not (
            account.fill_mode == "queue"
            and grace > 0
            and lot.entry_price is not None
            and order.limit_price - lot.entry_price + 1e-9
                >= self.parameters.legacy_queue_passive_turnover_edge
        ):
            return False
        if order.context_invalid_since_ms <= 0:
            order.context_invalid_since_ms = tick.market_ts_ms
        retained = (
            tick.market_ts_ms - order.context_invalid_since_ms
                <= grace * 1_000
        )
        if retained:
            order.retained_after_context_loss = True
        return retained

    def _retain_queue_queued_inventory_turn_corridor(
        self, account: MakerAccount, lot: MakerLot, order: MakerOrder,
        tick: ReplayTick, assessment: MarketAssessment,
        context: MakerDecisionContext,
    ) -> bool:
        """Keep an uncleared high leg when only the state label stabilizes.

        A queue inventory-turn offer can be validly established by recent
        lower-side selling and an executable high/low corridor before it has
        consumed the displayed queue.  A one-frame change from falling to
        stable is not independent evidence that those inputs disappeared.
        Queue 1.13 preserves only the unchanged offer while its original
        corridor remains live; it neither resets queue-ahead nor waives the
        conservative exact-price buffer.
        """

        policy = account.policy
        minimum_edge = self._downtrend_turn_edge(policy)
        live_replenishment = _floor_to_tick(
            tick.bid1 + self.parameters.price_tick,
            self.parameters.price_tick,
        )
        return (
            policy.retain_queue_queued_inventory_turn_in_stable
            and account.fill_mode == "queue"
            and order.side == "sell"
            and order.kind == "inventory_exit"
            and order.inventory_neutral_downtrend_turn
            and lot.remaining_quantity > 1e-9
            and order.queue_ahead > 1e-9
            and order.queue_cleared_ms <= 0
            and assessment.state == "stable"
            and not context.breakout_support_strong
            and not self._confirmed_rise_is_recent(tick, policy)
            and not (
                "11:30:00.000" <= tick.market_time < "13:00:00.000"
            )
            and tick.bid1 > 0
            and tick.ask1 > tick.bid1
            and abs(order.limit_price - tick.ask1)
                <= self.parameters.price_cluster_width + 1e-9
            and order.limit_price - live_replenishment + 1e-9
                >= minimum_edge
            and self._recent_lower_sell_bonds(
                tick, order.limit_price, minimum_gap=minimum_edge,
            ) + 1e-9 >= self.parameters.order_quantity_bonds
        )

    def _retain_queue_cleared_inventory_turn_corridor(
        self, account: MakerAccount, lot: MakerLot, order: MakerOrder,
        tick: ReplayTick, assessment: MarketAssessment,
        context: MakerDecisionContext,
    ) -> bool:
        """Preserve a proven queue-leading high leg through a lower excursion.

        An inventory-neutral high offer can consume both its displayed queue
        and its conservative exact-price buffer without filling.  If new
        lower-side selling then temporarily removes the ordinary quote
        context, cancelling the already first-in-line high offer throws away
        the most valuable part of the queue position.  Queue 1.10 retains only
        that proven position while the sell-first corridor remains causally
        executable and the market has not turned into a rise or breakout.
        Queue 1.10 caps that evidence at 180 seconds.  Queue 1.11 keeps the
        same earned position while fresh lower-side evidence remains live,
        so the clock alone cannot invalidate an otherwise unchanged corridor.
        """

        policy = account.policy
        window_seconds = (
            policy.queue_cleared_inventory_turn_corridor_seconds
        )
        retain_while_live = (
            policy.retain_queue_cleared_inventory_turn_while_live_corridor
        )
        elapsed = tick.market_ts_ms - order.queue_cleared_ms
        minimum_edge = self._downtrend_turn_edge(policy)
        live_replenishment = _floor_to_tick(
            tick.bid1 + self.parameters.price_tick,
            self.parameters.price_tick,
        )
        retained = (
            account.fill_mode == "queue"
            and window_seconds > 0
            and order.side == "sell"
            and order.kind == "inventory_exit"
            and order.inventory_neutral_downtrend_turn
            and lot.remaining_quantity > 1e-9
            and order.queue_ahead <= 1e-9
            and order.exact_fill_uncertainty_buffer <= 1e-9
            and order.queue_cleared_ms > 0
            and 0 <= elapsed
            and (
                retain_while_live
                or elapsed <= window_seconds * 1_000
            )
            and (
                assessment.state in {"stable", "possible_fall", "falling"}
                or (
                    retain_while_live
                    and assessment.state == "possible_rise"
                    and assessment.state_score <= 2
                )
            )
            and not context.breakout_support_strong
            and (
                retain_while_live
                or not self._confirmed_rise_is_recent(tick, policy)
            )
            and not (
                "11:30:00.000" <= tick.market_time < "13:00:00.000"
            )
            and tick.bid1 > 0
            and order.limit_price - live_replenishment + 1e-9
                >= minimum_edge
            and self._recent_lower_sell_bonds(
                tick, order.limit_price, minimum_gap=minimum_edge,
            ) + 1e-9 >= self.parameters.order_quantity_bonds
        )
        if retained:
            # Once the short generic extra-exit grace has expired, this order
            # survives for a different causal reason.  Do not let its later
            # fill masquerade as the queue-1.2 grace sequence and propagate an
            # unrelated replenishment buffer.
            order.retained_after_context_loss = False
            order.retained_after_queue_cleared_inventory_turn = True
        return retained

    def _retain_queue_cleared_sell_on_worse_reprice(
        self, account: MakerAccount, lot: MakerLot, order: MakerOrder,
        tick: ReplayTick, *, desired_price: float, desired_kind: str,
        desired_quantity: float, market_state: str,
    ) -> bool:
        """Keep a cleared sell queue instead of chasing one price tick.

        A same-price aggressive buy can consume the complete displayed ask
        queue ahead without filling the paper order.  If the external best ask
        then moves up by one tick, repricing with it throws away the first
        position just earned.  Queue 1.5 preserves an exact base offer only
        through the clearing frame.  Queue 1.6 separately lets a profitable
        extra-lot exit retain that cleared position for a short non-rising
        one-tick flicker.  The clearing trade is never reused to fill the model
        quantity.
        """

        base_grace_seconds = (
            account.policy.queue_cleared_sell_reprice_grace_seconds
        )
        extra_grace_seconds = (
            account.policy.queue_cleared_extra_sell_reprice_grace_seconds
        )
        elapsed = tick.market_ts_ms - order.queue_cleared_ms
        improvement = desired_price - order.limit_price
        has_extra_inventory = any(
            candidate.entry_price is not None
            and candidate.remaining_quantity > 1e-9
            for candidate in account.lots.values()
        )
        is_base_offer = (
            lot.entry_price is None
            and not has_extra_inventory
            and base_grace_seconds > 0
        )
        is_profitable_extra_exit = (
            lot.entry_price is not None
            and extra_grace_seconds > 0
            and order.limit_price - lot.entry_price + 1e-9
                >= self.parameters.legacy_queue_passive_turnover_edge
            and market_state != "rising"
        )
        common_context = (
            account.fill_mode == "queue"
            and (is_base_offer or is_profitable_extra_exit)
            and order.side == "sell"
            and order.kind == "inventory_exit"
            and desired_kind == order.kind
            and abs(order.remaining - desired_quantity) <= 1e-9
            and order.queue_ahead <= 1e-9
            and order.queue_cleared_ms > 0
            and not order.queue_cleared_crossed_book
            and order.exact_fill_uncertainty_buffer <= 1e-9
            and improvement > 1e-9
            and improvement <= self.parameters.price_tick + 1e-9
            and abs(tick.ask1 - desired_price) <= 1e-9
        )
        queue_just_cleared = (
            tick.market_ts_ms == order.queue_cleared_ms
            and tick.inferred_side == "buy"
            and tick.trade_bonds > 1e-9
            and abs(tick.last_price - order.limit_price) <= 1e-9
        )
        continuing_profitable_extra_exit = (
            is_profitable_extra_exit
            and order.retained_after_queue_cleared_reprice
            and 0 < elapsed <= extra_grace_seconds * 1_000
        )
        retained = common_context and (
            queue_just_cleared or continuing_profitable_extra_exit
        )
        if retained:
            order.retained_after_queue_cleared_reprice = True
        return retained

    def _confirmed_rise_is_recent(
        self, tick: ReplayTick, policy: MakerPolicyProfile | None = None,
    ) -> bool:
        grace_seconds = (
            policy.confirmed_rise_grace_seconds_override
            if policy is not None
            and policy.confirmed_rise_grace_seconds_override is not None
            else self.parameters.confirmed_rise_grace_seconds
        )
        trade_ts_ms = self.last_confirmed_rise_trade_ts_ms
        rise_price = self.last_confirmed_rise_price
        if (
            policy is not None
            and policy.confirm_exact_offer_clear_in_possible_rise
            and self.last_exact_offer_clear_rise_trade_ts_ms > trade_ts_ms
        ):
            trade_ts_ms = self.last_exact_offer_clear_rise_trade_ts_ms
            rise_price = self.last_exact_offer_clear_rise_price
        return (
            trade_ts_ms > 0
            and tick.market_ts_ms - trade_ts_ms
                <= grace_seconds * 1000
            and not self._confirmed_rise_has_counterevidence(
                tick, trade_ts_ms=trade_ts_ms, rise_price=rise_price,
            )
        )

    def _confirmed_rise_has_counterevidence(
        self, tick: ReplayTick, *, trade_ts_ms: int | None = None,
        rise_price: float | None = None,
    ) -> bool:
        """End the recovery grace period when the recovered level clearly fails."""

        trade_ts_ms = (
            self.last_confirmed_rise_trade_ts_ms
            if trade_ts_ms is None else trade_ts_ms
        )
        rise_price = (
            self.last_confirmed_rise_price
            if rise_price is None else rise_price
        )
        if (
            trade_ts_ms <= 0
            or rise_price <= 0
        ):
            return False
        lower_sell_cutoff = (
            rise_price - self.parameters.minimum_sweep_jump
        )
        lower_sell_bonds = sum(
            event.bonds for event in self.analyzer.trade_evidence
            if event.market_ts_ms > trade_ts_ms
            and event.market_ts_ms <= tick.market_ts_ms
            and event.side == "sell"
            and event.price <= lower_sell_cutoff + 1e-9
        )
        sustained_lower_selling = (
            lower_sell_bonds + 1e-9
            >= self.parameters.breakout_weakening_sell_bonds
        )
        bid_has_retired = (
            tick.bid1 > 0
            and rise_price - tick.bid1 + 1e-9
                >= self.parameters.minimum_active_entry_edge
        )
        return sustained_lower_selling or bid_has_retired

    def _recent_lower_sell_bonds(
        self, tick: ReplayTick, high_price: float, *, window_seconds: int = 120,
        minimum_gap: float | None = None,
    ) -> float:
        cutoff = tick.market_ts_ms - window_seconds * 1_000
        required_gap = (
            minimum_gap
            if minimum_gap is not None
            else self.parameters.minimum_entry_edge
        )
        return sum(
            event.bonds for event in self.analyzer.trade_evidence
            if event.market_ts_ms >= cutoff
            and event.side == "sell"
            and high_price - event.price + 1e-9
                >= required_gap
        )

    def _post_replenishment_lower_sell_bonds(
        self, tick: ReplayTick, high_price: float, *, replenished_ms: int,
        window_seconds: int = 120, minimum_gap: float | None = None,
    ) -> float:
        """Count only low-side evidence formed after a neutral refill.

        An inventory-neutral refill must not be sold again immediately because
        an older fair-value anchor still looks high.  Conversely, its historic
        entry cost must not block a later sell-first turn after the market has
        formed a new lower corridor.  Strictly post-refill sells distinguish
        those two cases without using future data or freezing historical cost.
        """

        cutoff = max(
            tick.market_ts_ms - window_seconds * 1_000,
            replenished_ms,
        )
        required_gap = (
            minimum_gap
            if minimum_gap is not None
            else self.parameters.minimum_entry_edge
        )
        return sum(
            event.bonds for event in self.analyzer.trade_evidence
            if event.market_ts_ms > cutoff
            and event.market_ts_ms <= tick.market_ts_ms
            and event.side == "sell"
            and high_price - event.price + 1e-9 >= required_gap
        )

    def _queue_quote_position(
        self, account: MakerAccount, tick: ReplayTick, *, side: str,
        desired_price: float,
    ) -> tuple[float, str | None]:
        """Move a best-quote queue order to the empty slot before level two.

        The observed book excludes the paper order.  Improving the old second
        level by one tick therefore creates a new second level with zero
        displayed external quantity ahead.  Only a parent quote that would
        have joined level one is transformed; deeper economic caps and
        historical targets keep their original price.
        """

        if not (
            account.fill_mode == "queue"
            and account.policy.quote_at_second_level_front
        ):
            return desired_price, None
        price_tick = self.parameters.price_tick
        if side == "buy":
            if (
                abs(desired_price - tick.bid1) > 1e-9
            ):
                return desired_price, None
            if len(tick.bids) < 2:
                return desired_price, (
                    "best_level_tail"
                    if account.policy.dynamically_choose_second_level_front
                    else None
                )
            second_price = tick.bids[1][0]
            candidate = _floor_to_tick(
                second_price + price_tick, price_tick,
            )
            if not (
                second_price > 0
                and candidate > second_price + 1e-9
                and candidate < tick.bid1 - 1e-9
            ):
                return desired_price, (
                    "best_level_tail"
                    if account.policy.dynamically_choose_second_level_front
                    else None
                )
            if not self._dynamic_second_level_front_is_worthwhile(
                account, tick, side=side, candidate=candidate,
            ):
                return desired_price, "best_level_tail"
            return candidate, "second_level_front"
        if side == "sell":
            if (
                abs(desired_price - tick.ask1) > 1e-9
            ):
                return desired_price, None
            if len(tick.asks) < 2:
                return desired_price, (
                    "best_level_tail"
                    if account.policy.dynamically_choose_second_level_front
                    else None
                )
            second_price = tick.asks[1][0]
            candidate = _ceil_to_tick(
                second_price - price_tick, price_tick,
            )
            if not (
                second_price > 0
                and candidate < second_price - 1e-9
                and candidate > tick.ask1 + 1e-9
            ):
                return desired_price, (
                    "best_level_tail"
                    if account.policy.dynamically_choose_second_level_front
                    else None
                )
            if not self._dynamic_second_level_front_is_worthwhile(
                account, tick, side=side, candidate=candidate,
            ):
                return desired_price, "best_level_tail"
            return candidate, "second_level_front"
        raise ValueError(f"unsupported maker order side: {side}")

    def _dynamic_second_level_front_is_worthwhile(
        self, account: MakerAccount, tick: ReplayTick, *, side: str,
        candidate: float,
    ) -> bool:
        """Choose deeper price only when waiting already resembles a sweep.

        A large displayed best queue means a full best-tail fill already needs
        an unusually large aggressive order.  In that state it is reasonable
        to demand a material price concession and wait for a sweep into the
        empty slot before level two.  A small queue, narrow inside spread, or
        trivial level gap keeps the ordinary best-level tail instead.
        """

        policy = account.policy
        if not policy.dynamically_choose_second_level_front:
            return True
        if tick.ask1 <= tick.bid1:
            return False
        top_quantity = tick.bid1_bonds if side == "buy" else tick.ask1_bonds
        top_price = tick.bid1 if side == "buy" else tick.ask1
        price_improvement = (
            top_price - candidate if side == "buy" else candidate - top_price
        )
        return (
            top_quantity + 1e-9
                >= (
                    policy.second_level_front_minimum_top_quantity_multiple
                    * self.parameters.order_quantity_bonds
                )
            and tick.ask1 - tick.bid1 + 1e-9
                >= policy.second_level_front_minimum_inside_spread
            and price_improvement + 1e-9
                >= policy.second_level_front_minimum_price_improvement
        )

    def _queue_ahead_at_quote(
        self, tick: ReplayTick, *, side: str, price: float,
        queue_position_kind: str | None,
    ) -> float:
        if queue_position_kind == "second_level_front":
            if side == "buy":
                return tick.bid1_bonds
            if side == "sell":
                return tick.ask1_bonds
            raise ValueError(f"unsupported maker order side: {side}")
        return self._book_quantity(tick, side, price)

    def _replace_buy(
        self, account: MakerAccount, tick: ReplayTick,
        desired: tuple[float, float, float | None] | None,
        kind: str, *, price_boundary: float | None = None,
        market_state: str | None = None, persist: bool,
    ) -> None:
        current = account.buy_order
        if desired is None:
            if current:
                if self._retain_queue_cleared_buy_after_context_loss(
                    account, current, tick,
                ):
                    return
                self._cancel_order(account, current, tick, "entry_context_changed", persist)
            return
        price, quantity, target = desired
        price, queue_position_kind = self._queue_quote_position(
            account, tick, side="buy", desired_price=price,
        )
        if price_boundary is None:
            # Direct test/research construction predating the observability
            # field has no wider reviewed range.  Production refresh paths
            # always pass their explicit causal boundary.
            price_boundary = price
        if current and self._retain_clean_cleared_inventory_turn_buy_while_falling(
            account, current, tick=tick, desired_price=price,
            desired_kind=kind, desired_quantity=quantity,
            desired_target=target, market_state=market_state,
        ):
            return
        if current and self._retain_queue_cleared_inventory_turn_buy_on_lower_reprice(
            account, current, tick=tick, desired_price=price,
            desired_kind=kind, desired_quantity=quantity,
            desired_target=target,
        ):
            return
        if current and self._retain_cleared_queue_for_one_tick(
            account, current, tick=tick, desired_price=price,
            desired_kind=kind, desired_quantity=quantity,
        ):
            return
        if (
            current
            and current.kind == kind
            and abs(current.limit_price - price) < 1e-9
            and abs(current.remaining - quantity) < 1e-9
        ):
            return
        if current:
            self._cancel_order(account, current, tick, "maker_reprice", persist)
        queue = self._queue_ahead_at_quote(
            tick, side="buy", price=price,
            queue_position_kind=queue_position_kind,
        )
        exact_fill_uncertainty_buffer = 0.0
        if (
            account.fill_mode == "queue"
            and kind == "inventory_replenish"
            and account.pending_replenishment_exact_fill_buffer > 0
        ):
            exact_fill_uncertainty_buffer = (
                account.pending_replenishment_exact_fill_buffer
            )
        elif (
            account.fill_mode == "queue"
            and kind == "inventory_turn_replenish"
        ):
            # The sell-first turn is an exploratory queue path.  A same-price
            # three-second Level 1 increment can mix prints that occurred
            # before this exact order position was established.  Absorb one
            # standard lot at the exact price, while _consume_queue still
            # discards this buffer immediately when the market trades through.
            exact_fill_uncertainty_buffer = (
                account.policy.queue_inventory_turn_exact_fill_buffer_bonds
            )
        if account.fill_mode == "priority" and price > tick.bid1:
            queue = 0.0
        account.buy_order = self._new_order(
            account, tick, side="buy", kind=kind, lot_id=None,
            price=price, quantity=quantity, queue_ahead=queue,
            target_price=target, price_boundary=price_boundary,
            persist=persist,
            exact_fill_uncertainty_buffer=exact_fill_uncertainty_buffer,
            queue_position_kind=queue_position_kind,
        )

    def _retain_clean_cleared_inventory_turn_buy_while_falling(
        self, account: MakerAccount, order: MakerOrder, *, tick: ReplayTick,
        desired_price: float, desired_kind: str, desired_quantity: float,
        desired_target: float | None, market_state: str | None,
    ) -> bool:
        """Keep an earned lower refill position instead of chasing a falling bid.

        The external best bid excludes the paper order.  Once real sells have
        cleanly removed both the displayed queue and the conservative exact-price
        buffer, the old order is first at its lower price.  While the tape is
        still falling and the strategy wants the same inventory-neutral refill,
        following a rising external bid throws away that position and pays more.
        Queue 1.15 keeps the old bid; a move down by more than one tick, a state
        change, or any order-semantic change still follows the parent path.
        """

        policy = account.policy
        if not (
            policy.retain_clean_cleared_inventory_turn_buy_while_falling
            and account.fill_mode == "queue"
            and market_state in {"possible_fall", "falling"}
            and order.side == "buy"
            and order.kind == "inventory_turn_replenish"
            and order.filled_quantity <= 1e-9
            and desired_kind == order.kind
            and abs(order.remaining - desired_quantity) <= 1e-9
            and (
                (order.target_price is None and desired_target is None)
                or (
                    order.target_price is not None
                    and desired_target is not None
                    and abs(order.target_price - desired_target) <= 1e-9
                )
            )
            and order.queue_ahead <= 1e-9
            and order.queue_cleared_ms > 0
            and not order.queue_cleared_crossed_book
            and order.exact_fill_uncertainty_buffer <= 1e-9
            and tick.ask1 > order.limit_price + 1e-9
            and desired_price + self.parameters.price_tick + 1e-9
                >= order.limit_price
            and account.pending_inventory_turn_quantity > 1e-9
            and account.pending_inventory_turn_sale_value > 0
        ):
            return False
        average_sale_price = (
            account.pending_inventory_turn_sale_value
            / account.pending_inventory_turn_quantity
        )
        return (
            average_sale_price - order.limit_price + 1e-9
                >= self._downtrend_turn_edge(policy)
        )

    def _retain_queue_cleared_inventory_turn_buy_on_lower_reprice(
        self, account: MakerAccount, order: MakerOrder, *, tick: ReplayTick,
        desired_price: float, desired_kind: str, desired_quantity: float,
        desired_target: float | None,
    ) -> bool:
        """Keep a genuinely cleared neutral refill through a one-tick dip.

        A clean exact-price sell can consume the full external bid ahead of an
        inventory-neutral replenishment.  If the displayed best bid then
        flickers down by one exchange tick while the strategy still wants the
        same refill, cancelling the old order throws away first position and
        immediately queues behind the returning price.  Queue 1.14 retains
        only the already-cleared original bid.  It never waives a remaining
        uncertainty buffer, never creates a higher bid, and a two-tick move or
        lost decision context still follows the parent cancellation path.
        """

        policy = account.policy
        if not (
            policy.retain_queue_cleared_inventory_turn_buy_on_lower_reprice
            and account.fill_mode == "queue"
            and order.side == "buy"
            and order.kind == "inventory_turn_replenish"
            and desired_kind == order.kind
            and abs(order.remaining - desired_quantity) <= 1e-9
            and (
                (order.target_price is None and desired_target is None)
                or (
                    order.target_price is not None
                    and desired_target is not None
                    and abs(order.target_price - desired_target) <= 1e-9
                )
            )
            and order.queue_ahead <= 1e-9
            and order.queue_cleared_ms > 0
            and not order.queue_cleared_crossed_book
            and order.exact_fill_uncertainty_buffer <= 1e-9
            and tick.ask1 > order.limit_price + 1e-9
        ):
            return False
        one_tick_lower = order.limit_price - desired_price
        if not (
            one_tick_lower > self.parameters.price_tick / 2
            and one_tick_lower
                <= self.parameters.price_tick + 1e-9
        ):
            return False
        if (
            account.pending_inventory_turn_quantity <= 1e-9
            or account.pending_inventory_turn_sale_value <= 0
        ):
            return False
        average_sale_price = (
            account.pending_inventory_turn_sale_value
            / account.pending_inventory_turn_quantity
        )
        return (
            average_sale_price - order.limit_price + 1e-9
                >= self._downtrend_turn_edge(policy)
        )

    def _retain_queue_cleared_buy_after_context_loss(
        self, account: MakerAccount, order: MakerOrder, tick: ReplayTick,
    ) -> bool:
        """Keep a newly first-in-line bid through one own-side book flicker.

        A same-price sell can consume the complete displayed queue ahead and
        make the external best bid disappear for one Level 1 frame.  The
        paper order would then itself be the best bid; cancelling it merely
        because the external quote vanished throws away the position just
        earned.  Queue 1.4 retains only that exact low-bid order for one
        three-second frame.  It does not infer a fill from the clearing trade,
        and a later trade is still required to execute the model quantity.
        """

        grace_seconds = account.policy.queue_cleared_buy_context_grace_seconds
        elapsed = tick.market_ts_ms - order.queue_cleared_ms
        return (
            account.fill_mode == "queue"
            and grace_seconds > 0
            and order.side == "buy"
            and order.kind == "low_bid_reversion"
            and order.queue_ahead <= 1e-9
            and order.queue_cleared_ms > 0
            and 0 <= elapsed <= grace_seconds * 1_000
            and not order.queue_cleared_crossed_book
            and order.exact_fill_uncertainty_buffer <= 1e-9
            and tick.inferred_side == "sell"
            and abs(tick.last_price - order.limit_price) <= 1e-9
            and tick.bid1 < order.limit_price - self.parameters.price_tick / 2
        )

    def _retain_cleared_queue_for_one_tick(
        self, account: MakerAccount, order: MakerOrder, *, tick: ReplayTick,
        desired_price: float, desired_kind: str, desired_quantity: float,
    ) -> bool:
        """Retain one crossed, cleared queue position for one Level 1 frame."""

        grace_seconds = (
            account.policy.queue_cleared_position_one_tick_grace_seconds
        )
        if not (
            account.fill_mode == "queue"
            and grace_seconds > 0
            and order.kind == desired_kind
            and abs(order.remaining - desired_quantity) < 1e-9
            and order.queue_ahead <= 1e-9
            and order.queue_cleared_crossed_book
            and 0 <= tick.market_ts_ms - order.queue_cleared_ms
                <= grace_seconds * 1_000
            and order.exact_fill_uncertainty_buffer <= 1e-9
        ):
            return False
        improvement = (
            order.limit_price - desired_price
            if order.side == "buy"
            else desired_price - order.limit_price
        )
        return (
            improvement > 1e-9
            and improvement <= self.parameters.price_tick + 1e-9
        )

    def _new_order(
        self, account: MakerAccount, tick: ReplayTick, *, side: str,
        kind: str, lot_id: int | None, price: float, quantity: float,
        queue_ahead: float, target_price: float | None,
        price_boundary: float | None = None, persist: bool,
        exact_fill_uncertainty_buffer: float = 0.0,
        repeated_turn_replenishment_price: float = 0.0,
        medium_wall_supported_base_short: bool = False,
        queue_position_kind: str | None = None,
    ) -> MakerOrder:
        price = _floor_to_tick(price, self.parameters.price_tick)
        if price_boundary is None:
            # Compatibility for direct test/research order construction: no
            # unreviewed chase range is implied, so the boundary is the order
            # price itself.  Every production decision path supplies a value.
            price_boundary = price
        if side == "buy":
            price_boundary_kind = "buy_ceiling"
            price_boundary = max(
                price,
                _floor_to_tick(price_boundary, self.parameters.price_tick),
            )
        elif side == "sell":
            price_boundary_kind = "sell_floor"
            price_boundary = min(
                price,
                _ceil_to_tick(price_boundary, self.parameters.price_tick),
            )
        else:
            raise ValueError(f"unsupported maker order side: {side}")
        if target_price is not None:
            target_price = _floor_to_tick(
                target_price, self.parameters.price_tick
            )
        values = {
            "run_id": self.store.run_id,
            "market_date": account.market_date,
            "strategy_id": account.strategy_id,
            "side": side,
            "status": "open",
            "kind": kind,
            "lot_id": lot_id,
            "created_market_ts_ms": tick.market_ts_ms,
            "updated_market_ts_ms": tick.market_ts_ms,
            "limit_price": price,
            "quantity": quantity,
            "filled_quantity": 0.0,
            "queue_ahead": queue_ahead,
            "target_price": target_price if kind == "sweep_tail" else None,
            "cancel_reason": None,
            "metadata_json": json.dumps({
                "paper_only": True,
                "fill_mode": account.fill_mode,
                "model_id": account.policy.model_id,
                "model_version": account.policy.model_version,
                "quantity_unit": "bond",
                "price_boundary": price_boundary,
                "price_boundary_kind": price_boundary_kind,
                "initial_queue_ahead_bonds": queue_ahead,
                "queue_position_kind": queue_position_kind,
                "exact_fill_uncertainty_buffer_bonds": (
                    exact_fill_uncertainty_buffer
                ),
                "repeated_turn_replenishment_price": (
                    repeated_turn_replenishment_price
                ),
                "medium_wall_supported_base_short": (
                    medium_wall_supported_base_short
                ),
            }, separators=(",", ":")),
        }
        order_id = self.store.insert_maker_order(values)
        return MakerOrder(
            order_id, side, kind, lot_id, tick.market_ts_ms, price, quantity,
            price_boundary, price_boundary_kind,
            queue_ahead=queue_ahead,
            exact_fill_uncertainty_buffer=exact_fill_uncertainty_buffer,
            repeated_turn_replenishment_price=(
                repeated_turn_replenishment_price
            ),
            medium_wall_supported_base_short=(
                medium_wall_supported_base_short
            ),
            queue_position_kind=queue_position_kind,
            target_price=target_price,
        )

    def _cancel_order(
        self, account: MakerAccount, order: MakerOrder, tick: ReplayTick,
        reason: str, persist: bool,
    ) -> None:
        self.store.update_maker_order(
            order.db_id, status="cancelled",
            updated_market_ts_ms=tick.market_ts_ms,
            filled_quantity=order.filled_quantity,
            queue_ahead=max(0.0, order.queue_ahead), cancel_reason=reason,
        )
        if order.side == "buy":
            account.buy_order = None
        elif order.lot_id is not None:
            account.sell_orders.pop(order.lot_id, None)

    def _cancel_all_orders(
        self, account: MakerAccount, tick: ReplayTick, reason: str, *, persist: bool,
    ) -> None:
        if account.buy_order is not None:
            self._cancel_order(account, account.buy_order, tick, reason, persist)
        for order in list(account.sell_orders.values()):
            self._cancel_order(account, order, tick, reason, persist)

    @staticmethod
    def _affordable_buy_bonds(
        account: MakerAccount, price: float,
    ) -> float:
        if price <= 0:
            return 0.0
        if account.purpose == "standard":
            # The user's ordinary-account input is denominated in bonds:
            # 1,000 base bonds plus capacity for 1,000 additional bonds.
            # A stale CNY seed must not silently shrink that explicit capacity.
            return max(0.0, account.maximum_inventory - account.inventory)
        return max(0.0, account.cash / price)

    @staticmethod
    def _ensure_standard_funding(
        account: MakerAccount, required_cash: float,
    ) -> None:
        if (
            account.purpose != "standard"
            or required_cash <= account.cash + 1e-9
        ):
            return
        adjustment = required_cash - account.cash
        # Increase cash and its PnL basis equally. This converts the explicit
        # bond-denominated capacity at the actual fill price without creating
        # paper profit or allowing inventory beyond the configured maximum.
        account.initial_cash += adjustment
        account.cash += adjustment
        account.funding_adjustment += adjustment

    def _fill_buy(
        self, account: MakerAccount, tick: ReplayTick, order: MakerOrder,
        quantity: float, received_ts_ns: int, *, kind: str,
        target_price: float | None, persist: bool, reason: str = "passive_buy",
    ) -> None:
        previous_inventory = account.inventory
        completed_sale_price = 0.0
        required_cash = quantity * order.limit_price
        self._ensure_standard_funding(account, required_cash)
        account.cash -= required_cash
        account.inventory += quantity
        order.filled_quantity += quantity
        restored = min(
            quantity,
            max(0.0, account.initial_inventory - previous_inventory),
        )
        inventory_turn_restored = min(
            max(0.0, quantity - restored),
            account.pending_inventory_turn_quantity,
        )
        if inventory_turn_restored > 1e-9:
            average_turn_sale = (
                account.pending_inventory_turn_sale_value
                / account.pending_inventory_turn_quantity
            )
            account.pending_inventory_turn_quantity = max(
                0.0,
                account.pending_inventory_turn_quantity
                    - inventory_turn_restored,
            )
            account.pending_inventory_turn_sale_value = max(
                0.0,
                account.pending_inventory_turn_sale_value
                    - inventory_turn_restored * average_turn_sale,
            )
        if restored > 1e-9 and account.replenishment_quantity > 1e-9:
            previous_replenishment_quantity = account.replenishment_quantity
            restored_share = min(
                1.0, restored / previous_replenishment_quantity,
            )
            account.medium_wall_supported_replenishment_quantity = max(
                0.0,
                account.medium_wall_supported_replenishment_quantity
                    * (1.0 - restored_share),
            )
            account.medium_wall_supported_replenishment_sale_value = max(
                0.0,
                account.medium_wall_supported_replenishment_sale_value
                    * (1.0 - restored_share),
            )
            average_sale = (
                account.replenishment_sale_value
                / account.replenishment_quantity
            )
            completed_sale_price = average_sale
            account.replenishment_quantity = max(
                0.0, account.replenishment_quantity - restored
            )
            account.replenishment_sale_value = max(
                0.0,
                account.replenishment_sale_value - restored * average_sale,
            )
            if order.kind == "profitable_visible_bid_base_replenish":
                account.last_profitable_visible_bid_replenishment_ts_ms = (
                    tick.market_ts_ms
                )
        completed_base_recovery = (
            restored > 1e-9
            and previous_inventory + 1e-9 < account.initial_inventory
            and account.inventory + 1e-9 >= account.initial_inventory
        )
        if completed_base_recovery:
            account.medium_wall_supported_replenishment_quantity = 0.0
            account.medium_wall_supported_replenishment_sale_value = 0.0
            account.last_base_replenishment_price = order.limit_price
            account.last_base_replenishment_ts_ms = tick.market_ts_ms
            # A newly completed base recovery supersedes any earlier corridor,
            # even when the new round trip is too narrow to qualify for repeat
            # memory.  Do not let a different, older regime remain reusable.
            account.last_completed_base_turn_sell_price = 0.0
            account.last_completed_base_turn_buy_price = 0.0
            account.last_completed_base_turn_ts_ms = 0
            if (
                completed_sale_price - order.limit_price + 1e-9
                >= self._downtrend_turn_edge(account.policy)
            ):
                account.last_completed_base_turn_sell_price = (
                    completed_sale_price
                )
                account.last_completed_base_turn_buy_price = order.limit_price
                account.last_completed_base_turn_ts_ms = tick.market_ts_ms
            account.pending_replenishment_exact_fill_buffer = 0.0
            account.pending_repeated_turn_replenishment_price = 0.0
            account.base_short_rising_buy_sequence_bonds = 0.0
        components: list[tuple[str, float, float | None, float | None]] = []
        if restored > 1e-9:
            base_target = (
                target_price
                if (
                    completed_base_recovery
                    and kind == "sweep_tail"
                    and account.policy.enable_priority_sweep_recovery_target
                )
                else None
            )
            components.append(("base", restored, None, base_target))
        extra = quantity - restored
        if extra > 1e-9:
            components.append((
                kind,
                extra,
                order.limit_price,
                target_price if kind == "sweep_tail" else None,
            ))
        for lot_kind, lot_quantity, entry_price, lot_target in components:
            lot_id = self.store.insert_maker_lot({
                "run_id": self.store.run_id,
                "market_date": account.market_date,
                "strategy_id": account.strategy_id,
                "kind": lot_kind,
                "opened_market_ts_ms": tick.market_ts_ms,
                "entry_price": entry_price,
                "original_quantity": lot_quantity,
                "remaining_quantity": lot_quantity,
                "target_price": lot_target,
                "status": "open",
                "updated_market_ts_ms": tick.market_ts_ms,
            })
            account.lots[lot_id] = MakerLot(
                lot_id, lot_kind, tick.market_ts_ms, entry_price,
                lot_quantity, lot_quantity, lot_target,
            )
            self._record_fill(
                account, tick, order, lot_id, "buy", order.limit_price,
                lot_quantity, reason, received_ts_ns,
            )
        if order.remaining <= 1e-9:
            self.store.update_maker_order(
                order.db_id, status="filled", updated_market_ts_ms=tick.market_ts_ms,
                filled_quantity=order.filled_quantity,
                queue_ahead=max(0.0, order.queue_ahead),
            )
            if account.buy_order and account.buy_order.db_id == order.db_id:
                account.buy_order = None
        else:
            self.store.update_maker_order(
                order.db_id, status="partial", updated_market_ts_ms=tick.market_ts_ms,
                filled_quantity=order.filled_quantity,
                queue_ahead=max(0.0, order.queue_ahead),
            )

    def _fill_sell(
        self, account: MakerAccount, tick: ReplayTick, order: MakerOrder,
        quantity: float, received_ts_ns: int, *, persist: bool,
        reason: str = "passive_sell",
    ) -> None:
        if order.lot_id is None or order.lot_id not in account.lots:
            return
        lot = account.lots[order.lot_id]
        quantity = min(quantity, lot.remaining_quantity)
        previous_inventory = account.inventory
        account.cash += quantity * order.limit_price
        account.inventory -= quantity
        if (
            account.fill_mode == "priority"
            and account.policy
                .priority_rising_base_short_after_extra_exit_isolation_seconds > 0
            and lot.entry_price is not None
            and previous_inventory > account.initial_inventory + 1e-9
            and account.inventory <= account.initial_inventory + 1e-9
        ):
            account.last_priority_extra_inventory_exit_price = (
                order.limit_price
            )
            account.last_priority_extra_inventory_exit_ts_ms = (
                tick.market_ts_ms
            )
        # A completed high-side execution ends the previous low-price sweep
        # episode. A later displayed discount is then a new causal opportunity.
        account.last_active_entry_price = None
        new_deficit = max(0.0, account.initial_inventory - account.inventory)
        old_deficit = max(0.0, account.initial_inventory - previous_inventory)
        added_deficit = max(0.0, new_deficit - old_deficit)
        if (
            account.policy.enable_downtrend_turn_while_extra_inventory
            and lot.entry_price is not None
            and previous_inventory > account.initial_inventory + 1e-9
        ):
            # An extra-lot exit is one high leg of the user's ordinary M0
            # turnover, not permission to rebuy at the same quote.  Preserve
            # its released capacity as a separate sell-high/buy-lower plan.
            account.pending_inventory_turn_quantity += quantity
            account.pending_inventory_turn_sale_value += (
                quantity * order.limit_price
            )
        if added_deficit > 1e-9:
            account.last_base_short_sale_ts_ms = tick.market_ts_ms
            account.base_short_rising_buy_sequence_bonds = 0.0
            account.replenishment_quantity += added_deficit
            account.replenishment_sale_value += added_deficit * order.limit_price
            if order.medium_wall_supported_base_short:
                account.medium_wall_supported_replenishment_quantity += (
                    added_deficit
                )
                account.medium_wall_supported_replenishment_sale_value += (
                    added_deficit * order.limit_price
                )
            if (
                lot.kind == "base"
                and order.retained_after_recent_sell_corridor
                and account.policy
                    .retain_priority_base_turn_on_recent_sell_corridor
            ):
                # The retained order survived because the *live* high/low
                # range remained executable.  Replenish against the lower end
                # visible on the actual fill tick instead of freezing an old
                # completed-turn target from the order's creation frame.
                account.pending_repeated_turn_replenishment_price = (
                    _floor_to_tick(
                        tick.bid1 + self.parameters.price_tick,
                        self.parameters.price_tick,
                    )
                )
            elif (
                lot.kind == "base"
                and order.repeated_turn_replenishment_price > 0
            ):
                account.pending_repeated_turn_replenishment_price = (
                    order.repeated_turn_replenishment_price
                )
        sequence_window = (
            account.policy.queue_graced_extra_exit_to_base_sale_window_seconds
        )
        sequence_buffer = (
            account.policy.queue_replenishment_exact_fill_buffer_bonds
        )
        if (
            account.fill_mode == "queue"
            and sequence_window > 0
            and sequence_buffer > 0
        ):
            if (
                lot.entry_price is not None
                and order.retained_after_context_loss
                and previous_inventory > account.initial_inventory + 1e-9
                and account.inventory <= account.initial_inventory + 1e-9
            ):
                account.last_extra_exit_ts_ms = tick.market_ts_ms
            elif (
                lot.kind == "base"
                and added_deficit > 1e-9
                and account.last_extra_exit_ts_ms > 0
                and tick.market_ts_ms - account.last_extra_exit_ts_ms
                    <= sequence_window * 1_000
            ):
                account.pending_replenishment_exact_fill_buffer = max(
                    account.pending_replenishment_exact_fill_buffer,
                    sequence_buffer,
                )
                if (
                    reason in {
                        "queue_cleared_next_frame_fill",
                        "queue_cleared_crossed_residual_fill",
                    }
                    and account.policy.queue_cleared_position_one_tick_grace_seconds
                        > 0
                ):
                    account.pending_replenishment_exact_fill_buffer += quantity
                account.last_extra_exit_ts_ms = 0
        order.filled_quantity += quantity
        lot.remaining_quantity -= quantity
        closed = lot.remaining_quantity <= 1e-9
        self.store.update_maker_lot(
            lot.db_id,
            remaining_quantity=max(0.0, lot.remaining_quantity),
            status="closed" if closed else "open",
            updated_market_ts_ms=tick.market_ts_ms,
        )
        self._record_fill(
            account, tick, order, lot.db_id, "sell", order.limit_price,
            quantity, reason, received_ts_ns,
        )
        if closed:
            account.lots.pop(lot.db_id, None)
            account.sell_orders.pop(lot.db_id, None)
        if order.remaining <= 1e-9 or closed:
            self.store.update_maker_order(
                order.db_id, status="filled", updated_market_ts_ms=tick.market_ts_ms,
                filled_quantity=order.filled_quantity,
                queue_ahead=max(0.0, order.queue_ahead),
            )
        else:
            self.store.update_maker_order(
                order.db_id, status="partial", updated_market_ts_ms=tick.market_ts_ms,
                filled_quantity=order.filled_quantity,
                queue_ahead=max(0.0, order.queue_ahead),
            )

    def _record_fill(
        self, account: MakerAccount, tick: ReplayTick, order: MakerOrder,
        lot_id: int, side: str, price: float, quantity: float, reason: str,
        received_ts_ns: int,
    ) -> None:
        account.fills += 1
        self.fills_this_run += 1
        self.store.insert_maker_fill({
            "run_id": self.store.run_id,
            "market_date": account.market_date,
            "strategy_id": account.strategy_id,
            "order_id": order.db_id,
            "lot_id": lot_id,
            "market_ts_ms": tick.market_ts_ms,
            "received_ts_ns": received_ts_ns,
            "side": side,
            "price": price,
            "quantity": quantity,
            "fill_reason": reason,
            "reference_tick_id": tick.tick_id,
            "cash_after": account.cash,
            "inventory_after": account.inventory,
        })

    def _mark_account(
        self, account: MakerAccount, tick: ReplayTick, *, persist: bool,
    ) -> None:
        account.last_market_ts_ms = tick.market_ts_ms
        account.last_tick_id = tick.tick_id
        account.last_bid = tick.bid1
        account.last_ask = tick.ask1
        account.last_bids = tick.bids
        account.last_asks = tick.asks
        mark = self._inventory_mark(account, tick.bid1, tick.ask1)
        account.trading_pnl = (
            account.cash - account.initial_cash
            + (account.inventory - account.initial_inventory) * mark
        )
        self._persist_account(account)

    @staticmethod
    def _inventory_mark(account: MakerAccount, bid: float, ask: float) -> float:
        if account.inventory > account.initial_inventory:
            return bid
        if account.inventory < account.initial_inventory:
            return ask
        return (bid + ask) / 2 if bid > 0 and ask > 0 else max(bid, ask)

    def _persist_account(self, account: MakerAccount) -> None:
        self.store.upsert_maker_account({
            "market_date": account.market_date,
            "strategy_id": account.strategy_id,
            "fill_mode": account.fill_mode,
            "initial_inventory": account.initial_inventory,
            "maximum_inventory": account.maximum_inventory,
            "initial_cash": account.initial_cash,
            "cash": account.cash,
            "inventory": account.inventory,
            "last_market_ts_ms": account.last_market_ts_ms,
            "last_tick_id": account.last_tick_id,
            "last_bid": account.last_bid,
            "last_ask": account.last_ask,
            "trading_pnl": account.trading_pnl,
            "fills": account.fills,
            "updated_at_utc": _utc_now(),
        })

    def _persist_model_assignment(self, account: MakerAccount) -> None:
        self.store.upsert_maker_model_assignment({
            "market_date": account.market_date,
            "strategy_id": account.strategy_id,
            "bond_code": account.bond_code,
            "model_id": account.policy.model_id,
            "model_version": account.policy.model_version,
            "execution_mode": account.policy.execution_mode,
            "parent_model_id": account.policy.parent_model_id,
            "assigned_at_utc": _utc_now(),
        })

    @staticmethod
    def _book_quantity(tick: ReplayTick, side: str, price: float) -> float:
        levels = tick.bids if side == "buy" else tick.asks
        for level_price, quantity in levels:
            if abs(level_price - price) < 1e-9:
                return quantity
        return 0.0

    def runtime_summary(self) -> dict[str, Any]:
        rows = []
        for account in self.accounts.values():
            rows.append({
                "bond_code": account.bond_code,
                "strategy_id": account.strategy_id,
                "fill_mode": account.fill_mode,
                "model_id": account.policy.model_id,
                "model_version": account.policy.model_version,
                "cash": round(account.cash, 2),
                "initial_cash": round(account.initial_cash, 2),
                "additional_buying_capacity": round(
                    account.additional_buying_capacity, 1,
                ),
                "funding_adjustment": round(account.funding_adjustment, 2),
                "initial_inventory": round(account.initial_inventory, 1),
                "maximum_inventory": round(account.maximum_inventory, 1),
                "inventory": round(account.inventory, 1),
                "customer_base_short_bonds": round(
                    account.customer_base_short_bonds, 1,
                ),
                "extra_inventory_bonds": round(
                    account.extra_inventory_bonds, 1,
                ),
                "pnl": round(account.trading_pnl, 2),
                "fills": account.fills,
                "open_buy_order": account.buy_order is not None,
                "open_sell_orders": len(account.sell_orders),
            })
        return {
            "enabled": self.enabled,
            "bond_codes": [self.bond_code],
            "underlying_stock_code": self.stock_code,
            "market_date": self.market_date,
            "fills_this_run": self.fills_this_run,
            "accounts": rows,
        }


class MakerPaperPortfolio:
    """Route one tick stream into independent persisted paper-model ledgers."""

    def __init__(self, config: AppConfig, store: SQLiteStore) -> None:
        self.config = config
        self.store = store
        self.engines = {
            code: MakerPaperEngine(
                config, store, bond_code=code,
                strategy_prefix=maker_strategy_prefix(config, code),
            )
            for code in configured_maker_bond_codes(config)
        }
        self.comparison_engines = {
            code: tuple(
                MakerPaperEngine(
                    config,
                    store,
                    bond_code=code,
                    strategy_prefix=maker_strategy_prefix(config, code),
                    priority_policy=(
                        policy if policy.execution_mode == "priority" else None
                    ),
                    queue_policy=(
                        policy if policy.execution_mode == "queue" else None
                    ),
                    fill_modes=(policy.execution_mode,),
                    include_windfall=False,
                    strategy_ids_by_mode={
                        policy.execution_mode: maker_comparison_strategy_id(
                            config, code, policy,
                        ),
                    },
                )
                for policy in realtime_comparison_policies(config)
            )
            for code in configured_maker_bond_codes(config)
        }

    def _all_engines(self) -> tuple[MakerPaperEngine, ...]:
        return tuple(self.engines.values()) + tuple(
            engine
            for engines in self.comparison_engines.values()
            for engine in engines
        )

    @property
    def enabled(self) -> bool:
        return self.config.maker_paper.enabled

    @property
    def accounts(self) -> dict[str, MakerAccount]:
        result: dict[str, MakerAccount] = {}
        for engine in self._all_engines():
            result.update(engine.accounts)
        return result

    @property
    def market_date(self) -> str | None:
        return next(
            (engine.market_date for engine in self._all_engines() if engine.market_date),
            None,
        )

    @property
    def fills_this_run(self) -> int:
        return sum(engine.fills_this_run for engine in self._all_engines())

    def rebuild_date(self, market_date: date | str) -> None:
        if not self.enabled or not self.engines:
            return
        for engine in self._all_engines():
            engine.rebuild_date(market_date, clear=True)

    def on_recorded_tick(self, recorded: RecordedTick) -> None:
        code = recorded.tick.code
        matching_engines = tuple(
            engine for engine in self._all_engines()
            if engine.stock_code == code or engine.bond_code == code
        )
        for engine in matching_engines:
            engine.on_recorded_tick(recorded)

    def on_replay_tick(
        self, tick: ReplayTick, *, persist: bool,
        received_ts_ns: int | None = None,
    ) -> None:
        matching_engines = tuple(
            engine for engine in self._all_engines()
            if engine.stock_code == tick.code or engine.bond_code == tick.code
        )
        for engine in matching_engines:
            engine.on_replay_tick(
                tick, persist=persist, received_ts_ns=received_ts_ns
            )

    def runtime_summary(self) -> dict[str, Any]:
        summaries = [engine.runtime_summary() for engine in self._all_engines()]
        return {
            "enabled": self.enabled,
            "bond_codes": list(self.engines),
            "market_date": self.market_date,
            "fills_this_run": sum(
                int(summary["fills_this_run"]) for summary in summaries
            ),
            "accounts": [
                account
                for summary in summaries
                for account in summary["accounts"]
            ],
        }
