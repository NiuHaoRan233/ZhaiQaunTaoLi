from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field, replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from datetime import date, datetime, timezone
from typing import Any, Callable

from .config import AppConfig, maker_underlying_stock_code
from .database import SQLiteStore
from .maker import (
    MakerAnalyzer,
    MakerParameters,
    MarketAssessment,
    Opportunity,
    ReplayTick,
    _load_ticks,
    trend_price_discovery_assessment,
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
    use_unpolluted_windfall_reference: bool = False
    windfall_order_quantity_bonds: float | None = None
    windfall_initial_credit_cny: float | None = None
    windfall_minimum_discount: float | None = None
    windfall_minimum_book_gap: float | None = None
    windfall_capacity_funded: bool = False
    enable_active_windfall_offer_sweep: bool = False
    windfall_minimum_active_offer_bonds: float = 1_000.0
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
    # A valid first-position customer-base recovery intention must remain at
    # the live priority quote.  A stale fair-value or round-trip ceiling may
    # decide that the intention no longer exists, but it may not leave the
    # order resting behind the current inside market.  The isolated-top-bid
    # guard remains a separate reliability exception.
    enable_live_priority_base_replenishment_exposure: bool = False
    # First-position 2.1 treats a sold customer base as a live economic short.
    # In a bond-confirmed uptrend its passive recovery quote follows reliable
    # current bid discovery; an extreme underlying move can accelerate the
    # active stop only when the bond itself is visibly consuming sell supply.
    enable_trend_price_discovery_base_replenishment: bool = False
    trend_base_replenishment_maximum_loss: float = 0.15
    trend_base_replenishment_minimum_bid_multiple: float = 5.0
    trend_base_replenishment_minimum_buy_multiple: float = 5.0
    trend_stock_acceleration_minimum_return: float = 0.08
    # First-position 2.2 requires current bid-ladder support and causal sell-
    # wall consumption.  Rolling tape volume may support a live staircase but
    # may no longer lift a disconnected top bid by itself.
    enable_strict_trend_market_structure: bool = False
    strict_trend_overhead_ask_band: float = 0.015
    strict_trend_minimum_tracked_wall_multiple: float = 30.0
    strict_trend_maximum_overhead_ask_multiple: float = 10.0
    strict_trend_wall_attack_window_seconds: int = 60
    strict_trend_minimum_wall_attack_multiple: float = 5.0
    strict_trend_minimum_wall_attack_ratio: float = 0.50
    strict_trend_migration_minimum_attack_multiple: float = 1.0
    strict_trend_migration_minimum_bid_clearance: float = 0.10
    strict_trend_minimum_secondary_bid_multiple: float = 1.0
    enable_isolated_top_bid_base_replenishment_guard: bool = False
    isolated_top_bid_maximum_inside_spread: float = 0.02
    isolated_top_bid_minimum_gap_to_next_bid: float = 0.10
    isolated_top_bid_maximum_quantity_multiple: float = 2.0
    isolated_top_bid_near_ask_band: float = 0.01
    isolated_top_bid_minimum_ask_supply_multiple: float = 5.0
    isolated_top_bid_wall_attack_window_seconds: int = 60
    isolated_top_bid_minimum_confirmed_attack_multiple: float = 5.0
    isolated_top_bid_material_attack_ratio: float = 0.50
    isolated_top_bid_accelerated_attack_ratio: float = 0.35
    isolated_top_bid_accelerated_attack_multiple: float = 10.0
    isolated_top_bid_accelerated_attack_events: int = 3
    # During fragile opening discovery, a deep-discount cross may restore a
    # customer-base deficit but needs fresh bond buying or a nearby layered
    # cushion before it can create inventory above the opening base.
    enable_opening_extra_inventory_confirmation: bool = False
    opening_extra_inventory_guard_end_time: str = "09:40:00.000"
    opening_extra_inventory_buy_window_seconds: int = 60
    opening_extra_inventory_minimum_buy_multiple: float = 5.0
    opening_extra_inventory_minimum_buy_imbalance_ratio: float = 1.5
    opening_extra_inventory_support_band: float = 0.10
    opening_extra_inventory_minimum_support_levels: int = 2
    opening_extra_inventory_minimum_support_multiple: float = 5.0
    opening_extra_inventory_minimum_secondary_bid_multiple: float = 1.0
    opening_extra_inventory_maximum_descending_ask_step: float = 0.015
    opening_extra_inventory_stock_fall_return: float = -0.005
    # Immutable first-position 2.51 build: constrain an immature opening
    # quote reference with one quantity-weighted trade median.  Its r2 child
    # deliberately disables this switch after the median was shown to erase
    # useful wide, bimodal T-making corridors.
    enable_opening_trade_constrained_reference: bool = False
    opening_discovery_nominal_end_time: str = "10:00:00.000"
    opening_discovery_trade_window_seconds: int = 300
    opening_discovery_minimum_trade_events: int = 5
    opening_discovery_minimum_trade_bonds: float = 5_000.0
    opening_discovery_minimum_two_sided_bonds: float = 1_000.0
    opening_discovery_early_minimum_trade_events: int = 6
    opening_discovery_early_minimum_trade_bonds: float = 10_000.0
    opening_discovery_early_minimum_two_sided_bonds: float = 3_000.0
    opening_discovery_maximum_trade_range: float = 0.20
    opening_discovery_confirmed_buy_bonds: float = 5_000.0
    opening_discovery_buy_dominance_ratio: float = 1.5
    # Ordinary moderate-edge extra-inventory bids need protection in both
    # nested bands.  The inherited outer band remains 0.20 yuan / 5 blocks;
    # 2.51 adds at least 3 blocks inside 0.10 yuan.  Deep >=0.50-yuan value,
    # customer-base recovery and separately registered special branches keep
    # their own safety logic.
    require_nested_ordinary_bid_support: bool = False
    ordinary_inner_bid_support_distance: float = 0.10
    ordinary_inner_bid_support_multiple: float = 3.0
    # First-position 2.51 r2 treats actual trades as a liquidity map rather
    # than a single fair-value ceiling.  A low-side sell cluster proves a
    # passive bid may be reached; a sufficiently higher buy cluster near the
    # live offer proves a potential exit.  This is an alternate permission
    # for an ordinary extra-inventory bid and still requires the confirmed
    # nested 0.10/3,000 plus 0.20/5,000 live bid support.
    enable_ordinary_liquidity_corridor_entry: bool = False
    ordinary_liquidity_corridor_window_seconds: int = 300
    ordinary_liquidity_corridor_low_sell_band: float = 0.10
    ordinary_liquidity_corridor_minimum_low_sell_bonds: float = 3_000.0
    ordinary_liquidity_corridor_minimum_high_buy_bonds: float = 3_000.0
    ordinary_liquidity_corridor_minimum_exit_edge: float = 0.20
    ordinary_liquidity_corridor_maximum_high_buy_ask_gap: float = 0.10
    ordinary_liquidity_corridor_minimum_ask_bonds: float = 1_000.0
    # First-position 2.52 can restore an existing customer-base short by
    # consuming one profitable visible offer tail after real buys have walked
    # through several rising offer levels.  It is strictly a risk-reduction
    # permission and can never create inventory above the opening base.
    enable_profitable_offer_tail_base_replenishment: bool = False
    profitable_offer_tail_window_seconds: int = 60
    profitable_offer_tail_minimum_buy_multiple: float = 5.0
    profitable_offer_tail_minimum_buy_events: int = 3
    profitable_offer_tail_minimum_price_span: float = 0.10
    profitable_offer_tail_minimum_next_ask_gap: float = 0.20
    profitable_offer_tail_minimum_profit: float = 0.20
    # The same 2.52 child retains 2.51's nested support discipline but permits
    # a narrow override when one current deep wall is executable and the live
    # exit corridor is materially wider than the distance to that protection.
    enable_wide_reward_risk_nested_support_override: bool = False
    wide_reward_risk_minimum_exit_edge: float = 0.30
    wide_reward_risk_maximum_protection_distance: float = 0.30
    wide_reward_risk_minimum_ratio: float = 2.0
    wide_reward_risk_minimum_wall_multiple: float = 5.0
    wide_reward_risk_exit_memory_seconds: int = 180
    wide_reward_risk_exit_cluster_band: float = 0.01
    wide_reward_risk_minimum_exit_supply_multiple: float = 1.0
    # Shared-capital 0.3 may admit an ordinary first-position intent that
    # misses the parent's nested support threshold when persistent, causal
    # same-day trades demonstrate high-side price acceptance.  The composite
    # quality is an admission score, not an automatic allocation decision;
    # cash and the competing bond still rank the admitted intent downstream.
    enable_session_resilient_ordinary_entry: bool = False
    session_resilient_minimum_exit_edge: float = 0.30
    session_resilient_high_trade_ceiling_above_ask: float = 0.35
    session_resilient_near_support_distance: float = 0.20
    session_resilient_minimum_near_support_bonds: float = 1_000.0
    session_resilient_high_trade_bond_scale: float = 10_000.0
    session_resilient_high_buy_bond_scale: float = 5_000.0
    session_resilient_high_trade_event_scale: int = 10
    session_resilient_minimum_high_trade_events: int = 3
    session_resilient_span_scale_seconds: int = 1_800
    session_resilient_bucket_seconds: int = 600
    session_resilient_bucket_scale: int = 4
    session_resilient_high_trade_share_scale: float = 0.60
    session_resilient_minimum_composite_quality: float = 0.65
    # A lunch break expires rolling evidence but does not start a new market
    # day.  Once an intraday working reference exists, an afternoon fallback
    # may carry that price or reset from the live midpoint; it may not silently
    # resurrect yesterday's close.
    enable_midday_intraday_reference_continuity: bool = False
    # Once this model has formed a non-close working reference during the
    # current trading day, expiring rolling flow evidence must not resurrect
    # yesterday's close.  Retain only the price hypothesis; stale volume,
    # direction and trend evidence remain expired.
    enable_intraday_reference_continuity: bool = False
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
    enable_adjacent_bid_cushion_entry: bool = False
    # Historical exceptional-wall memory and fixed wall-plus-premium quote
    # caps belong to the immutable older priority models.  First-position
    # 1.44 still uses current nearby depth as safety evidence, but a vanished
    # or distant wall cannot mechanically push an otherwise legal quote away
    # from the current best bid.
    ignore_legacy_bid_wall_entry_caps: bool = False
    adjacent_bid_cushion_minimum_seconds: int = 30
    adjacent_bid_cushion_minimum_observations: int = 3
    adjacent_bid_cushion_maximum_span: float = 0.005
    adjacent_bid_cushion_minimum_capacity_multiple: float = 8.0
    adjacent_bid_cushion_minimum_value_score: float = 0.64
    adjacent_bid_cushion_maximum_lifetime_seconds: int = 300
    adjacent_bid_cushion_hard_exit_capacity_multiple: float = 2.0
    adjacent_bid_cushion_damage_window_seconds: int = 30
    adjacent_bid_cushion_rapid_damage_ratio: float = 0.20
    adjacent_bid_cushion_backup_gap: float = 0.10
    enable_joint_causal_corridor_two_sided_quote: bool = False
    # Once real intraday price discovery has produced a working reference,
    # an illiquid late-session gap must not silently replace it with
    # yesterday's close merely because the rolling evidence window expires.
    # This permission remains model-specific so immutable historical profiles
    # keep their original order paths.
    retain_intraday_reference_in_quiet_wide_market: bool = False
    quiet_wide_market_minimum_seconds: int = 600
    quiet_wide_market_minimum_spread: float = 0.40
    quiet_wide_market_earliest_time: str = "14:45:00.000"
    # First-position 1.48 treats a sweep as one explicit market episode.  Once
    # the swept offer band clears, any displayed sell liquidity back in that
    # band invalidates the episode immediately; the old 30-minute analyzer
    # memory may no longer support or lift a buy/exit quote.
    enable_strict_breakout_episode: bool = False
    strict_breakout_offer_band: float = 0.010
    allow_neutral_inventory_sweep_tail: bool = False
    # A normal active discount is an isolated erroneous offer, not a dense
    # downward repricing of the whole ask ladder.  Quantity controls the
    # required error margin; both recent real trades and the independent
    # dynamic reference must agree that the offer is deeply cheap.
    enable_isolated_deep_discount_sweep: bool = False
    isolated_discount_recent_trade_seconds: int = 600
    isolated_discount_minimum_ask_gap: float = 0.20
    isolated_discount_maximum_intervening_supply_bonds: float = 2_000.0
    # First-position 1.49 evaluates one adjacent low-offer cluster against
    # references that existed independently of that cluster.  The anomalous
    # best ask may therefore not lower the midpoint and then veto itself.
    enable_unpolluted_isolated_discount_reference: bool = False
    # A carried reference was formed before the current suspect offer cluster.
    # When that independent price is already at or below the cluster, it is
    # contrary evidence and may not be discarded in favour of a remote ask.
    veto_isolated_discount_with_low_carried_reference: bool = False
    isolated_discount_price_cluster_width: float = 0.015
    isolated_discount_normal_ask_match_width: float = 0.015
    isolated_discount_fair_value_tolerance: float = 0.015
    isolated_discount_minimum_pre_snapshot_drop_ratio: float = 0.50
    # At maximum inventory the extra lot consumes the account's only buying
    # capacity.  Quote it at the nearest executable offer once a modest T edge
    # is available, and release it into a still-deep bid when a causal sell
    # sequence confirms that waiting has become riskier than a bounded loss.
    enable_full_inventory_capacity_release: bool = False
    full_inventory_passive_exit_minimum_edge: float = 0.05
    full_inventory_active_exit_maximum_loss: float = 0.15
    full_inventory_active_exit_minimum_frame_sell_multiple: float = 3.0
    full_inventory_active_exit_minimum_bid_multiple: float = 5.0
    full_inventory_active_exit_minimum_recent_sell_multiple: float = 5.0
    full_inventory_active_exit_minimum_imbalance_ratio: float = 1.5
    # A full extra T lot may also return to neutral near cost when the old
    # high-side route has failed and persistent selling is walking the offer
    # down.  This is evidence-driven capacity management, never a timer exit.
    enable_stalled_extra_inventory_near_flat_exit: bool = False
    stalled_extra_exit_maximum_loss: float = 0.015
    stalled_extra_exit_minimum_recent_sell_multiple: float = 5.0
    stalled_extra_exit_minimum_imbalance_ratio: float = 1.5
    stalled_extra_exit_minimum_short_ask_drop: float = 0.10
    stalled_extra_exit_reentry_cooldown_seconds: int = 600
    stalled_extra_exit_reentry_minimum_improvement: float = 0.30
    ordinary_risk_exit_uses_fresh_reentry: bool = False
    enable_shared_current_opportunity_reentry: bool = False
    enable_shared_resilient_exit_gap: bool = False
    ordinary_entry_sell_pressure_mode: str = "off"
    enable_ordinary_tape_turnover_regime: bool = False
    enable_ordinary_tape_horizontal_recovery: bool = False
    preserve_ordinary_tape_current_wide_corridor: bool = False
    enable_causal_ordinary_inventory_turnover: bool = False
    recognize_consumed_recovered_support: bool = False
    quote_on_current_ordinary_recovery: bool = False
    allow_horizontal_recovery_quote_with_bid_retreat: bool = False
    protect_discounted_ordinary_lot_from_fragile_bid_exit: bool = False
    require_strict_passive_order_timestamp: bool = False
    normalize_native_priority_price_grid: bool = False
    # First-position 2.6 treats an ordinary extra lot's entry support as a
    # causal identity.  Once that support has disappeared, the offer ladder
    # has migrated down and a new lower support corridor is visible, follow
    # the descending offer with the extra lot so the account can return to
    # neutral and redeploy its T-making capacity below.  This never applies
    # to the customer's opening base.
    enable_support_collapse_capacity_redeployment: bool = False
    support_collapse_inner_distance: float = 0.10
    support_collapse_inner_maximum_multiple: float = 3.0
    support_collapse_outer_distance: float = 0.20
    support_collapse_outer_maximum_multiple: float = 5.0
    support_collapse_minimum_bid_migration: float = 0.30
    support_collapse_minimum_ask_drop: float = 0.10
    support_collapse_minimum_inside_spread: float = 0.20
    support_collapse_minimum_recent_sell_multiple: float = 5.0
    support_collapse_minimum_sell_imbalance_ratio: float = 1.5
    support_collapse_new_support_distance: float = 0.10
    support_collapse_new_support_minimum_multiple: float = 5.0
    support_collapse_initial_maximum_loss: float = 0.25
    support_collapse_reentry_cooldown_seconds: int = 600
    support_collapse_reentry_minimum_improvement: float = 0.30
    support_collapse_reentry_attack_window_seconds: int = 60
    support_collapse_reentry_attack_minimum_improvement: float = 0.10
    support_collapse_reentry_attack_minimum_multiple: float = 1.0
    # The first 2.6 build validated the loss limit only when a release order
    # was created, then followed any later offer downward and also imposed a
    # blanket 600-second base-lot veto.  Its immutable r2 child revalidates
    # every lower quote against the active-buy logic, keeps this release out
    # of the older stalled-exit cooldown, and protects a pending lower turn
    # only until real buying recovers above the failed entry area.
    enable_support_collapse_consistency_revision: bool = False
    # A support-collapse exit is a bounded stop that frees T-making capacity,
    # not necessarily the high leg of a sell-high/buy-lower round trip.  Once
    # real buying has recovered to the failed entry area, a newer immutable
    # 2.6 child retires only the still-open quantity created by that stop so
    # ordinary parent-model entries can compete again.
    retire_recovered_support_collapse_pending_turn: bool = False
    # A tight best bid/ask does not by itself prove that an urgent buyer is
    # available.  When the low offer is a small isolated sell cluster, the
    # bid is a nearby supported cluster, and an existing passive exit still
    # matches the next normal offer, keep waiting for a real aggressive buyer
    # instead of crossing the bid merely because the top spread is narrow.
    enable_isolated_low_offer_active_turnover_hold: bool = False
    turnover_hold_offer_cluster_width: float = 0.015
    turnover_hold_minimum_gap_to_normal_offer: float = 0.20
    turnover_hold_maximum_offer_cluster_multiple: float = 2.0
    turnover_hold_bid_cluster_width: float = 0.015
    turnover_hold_minimum_bid_cluster_multiple: float = 1.0
    turnover_hold_existing_exit_match_width: float = 0.015
    # A zero-base shared-capital T account cannot leave its only bought lot
    # parked without a live exit merely because the nearest offer moved below
    # cost or an old fair-value floor.  While the offer ladder is normal, keep
    # the lot exposed one tick ahead of the reliable best ask.  A causally
    # isolated small low-offer cluster, or a deep offer the model itself would
    # actively buy, remains the only passive-quote exception.
    enable_live_priority_extra_inventory_exit_exposure: bool = False
    # The first v0.11 experiment applied the live-exit override to every bought
    # lot and required an unnecessarily tight inside market before recognising
    # an isolated low offer.  The guarded revision confines the override to an
    # ordinary passive T lot, preserves the native exit plan of active-value and
    # evidence-specific entries, and treats a separated small offer as an
    # anomaly without pretending that a nearby passive bid proves an urgent
    # buyer.  It also prevents a just-filled protected corridor lot from being
    # dumped into a distant bid while the normal ask ladder is still intact.
    enable_guarded_live_priority_extra_inventory_exit_exposure: bool = False
    guarded_live_exit_ordinary_lot_kinds: tuple[str, ...] = (
        "low_bid_reversion",
    )
    # Ordinary full-inventory accounts may reuse the guarded order-gap
    # fallback while bounding downward repricing and qualifying the shared-
    # capital branch's mechanical 600-second/0.30-yuan re-entry memory.
    # ``None`` and ``True`` preserve every registered older model.
    guarded_live_exit_maximum_loss: float | None = None
    guarded_live_exit_records_stalled_reentry: bool = True
    guarded_live_exit_release_reentry_on_high_attack: bool = False
    retain_guarded_live_exit_across_parent_order_gap: bool = False
    guarded_live_exit_requires_current_sell_side_repricing: bool = False
    guarded_live_exit_protects_special_rapid_gap: bool = True
    guarded_live_exit_rapid_entry_seconds: int = 30
    guarded_live_exit_rapid_minimum_loss: float = 0.30
    guarded_live_exit_rapid_minimum_inside_spread: float = 0.20
    guarded_live_exit_repricing_window_seconds: int = 60
    guarded_live_exit_repricing_minimum_sell_events: int = 2
    joint_corridor_high_trade_lookback_seconds: int = 300
    joint_corridor_high_trade_band: float = 0.015
    joint_corridor_minimum_high_trade_bonds: float = 5_000.0
    joint_corridor_ask_supply_band: float = 0.010
    joint_corridor_minimum_ask_supply_multiple: float = 5.0
    joint_corridor_minimum_ask_to_high_trade_multiple: float = 2.0
    joint_corridor_breakout_bid_multiple: float = 5.0
    joint_corridor_minimum_book_seconds: int = 30
    joint_corridor_minimum_book_observations: int = 3
    joint_corridor_primary_support_distance: float = 0.050
    joint_corridor_primary_support_multiple: float = 6.0
    joint_corridor_exceptional_support_distance: float = 0.100
    joint_corridor_exceptional_support_multiple: float = 10.0
    joint_corridor_minimum_edge: float = 0.20
    joint_corridor_minimum_reward_risk: float = 3.0
    joint_corridor_maximum_quote_drift: float = 0.015
    joint_corridor_maximum_lifetime_seconds: int = 300
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
PRIORITY_POLICY_FIRST_POSITION_V144 = replace(
    PRIORITY_POLICY_V143_CANDIDATE,
    # User-facing name: 第一顺位1.44.  The older
    # ``maker_priority_v1_44_candidate`` remains an immutable internal draft
    # and is not this user-confirmed version.
    model_id="maker_priority_v1_44",
    model_version="1.44",
    parent_model_id="maker_priority_v1_43_candidate",
    # Treat a persistent, immediately adjacent multi-level bid cushion as
    # executable protection for one passive extra lot.  Entry value is a
    # continuous combination of whole-corridor edge and cushion capacity;
    # there is no standalone 0.30-yuan gate.  Once filled, the exact cushion
    # becomes the lot's risk identity and controls dynamic active exit.
    enable_adjacent_bid_cushion_entry=True,
    ignore_legacy_bid_wall_entry_caps=True,
)
PRIORITY_POLICY_FIRST_POSITION_V145 = replace(
    PRIORITY_POLICY_FIRST_POSITION_V144,
    model_id="maker_priority_v1_45",
    model_version="1.45",
    parent_model_id="maker_priority_v1_44",
    # A customer-base short still keeps a live recovery intention, but a
    # one-lot best bid sitting almost at ask1 and far above bid2 is not a
    # reliable replenishment anchor when nearby ask supply is much larger.
    # Quote one tick ahead of the next bid instead.  While that guard remains
    # active, separately certify real aggressive-buy attacks on the observed
    # sell wall and restore the base within the inherited near-flat boundary.
    enable_isolated_top_bid_base_replenishment_guard=True,
)
PRIORITY_POLICY_FIRST_POSITION_V146 = replace(
    PRIORITY_POLICY_FIRST_POSITION_V145,
    model_id="maker_priority_v1_46",
    model_version="1.46",
    parent_model_id="maker_priority_v1_45",
    # At neutral base inventory, a real recently traded high side, current
    # nearby ask supply, and executable low-side stop capacity may jointly
    # authorize one simultaneous passive bid and customer-base offer.  The
    # permission judges the complete reward/risk corridor; neither a
    # 0.185-yuan reference discount nor a momentary top-bid change is an
    # independent veto.  Inventory identity is reassessed after either leg
    # fills, and the exact low-side cushion remains the risk identity.
    enable_joint_causal_corridor_two_sided_quote=True,
)
PRIORITY_POLICY_FIRST_POSITION_V147 = replace(
    PRIORITY_POLICY_FIRST_POSITION_V146,
    model_id="maker_priority_v1_47",
    model_version="1.47",
    parent_model_id="maker_priority_v1_46",
    # If the analyzer would otherwise fall all the way back to previous close
    # after real intraday price discovery, retain the last intraday working
    # centre while the current wide inside market still straddles it.  The
    # retained centre is deliberately low confidence: it can support normal
    # passive two-sided evaluation, but it is not a reliable trade anchor and
    # does not itself authorize an active cross.
    retain_intraday_reference_in_quiet_wide_market=True,
)
PRIORITY_POLICY_FIRST_POSITION_V148_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V147,
    model_id="maker_priority_v1_48_candidate",
    model_version="1.48-candidate",
    parent_model_id="maker_priority_v1_47",
    # Breakout tail-taking, isolated erroneous offers and ordinary T exits are
    # separate permissions.  A failed breakout can never lower the active-buy
    # hurdle or hold an extra-lot exit above the nearest executable offer.
    enable_strict_breakout_episode=True,
    allow_neutral_inventory_sweep_tail=True,
    enable_isolated_deep_discount_sweep=True,
    enable_full_inventory_capacity_release=True,
)
PRIORITY_POLICY_FIRST_POSITION_V149_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V148_CANDIDATE,
    model_id="maker_priority_v1_49_candidate",
    model_version="1.49-candidate",
    parent_model_id="maker_priority_v1_48_candidate",
    # A failed high-side exit may return the full extra lot to neutral within
    # the inherited near-flat risk boundary.  Isolated erroneous offers use
    # the pre-anomaly book/trade context and an adjacent price cluster, so the
    # bad quote cannot contaminate the very reference used to judge it.
    enable_unpolluted_isolated_discount_reference=True,
    enable_stalled_extra_inventory_near_flat_exit=True,
)
PRIORITY_POLICY_FIRST_POSITION_V149_R2_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V149_CANDIDATE,
    model_id="maker_priority_v1_49_candidate_r2",
    model_version="1.49-candidate-r2",
    parent_model_id="maker_priority_v1_49_candidate",
    # Continue the user's 1.49 working line without mutating the already
    # replayed 1.49 audit identity.  A small isolated low-offer cluster near a
    # supported bid is supply from an urgent seller, not evidence that the bid
    # belongs to an urgent buyer.  Preserve the existing normal-offer exit.
    enable_isolated_low_offer_active_turnover_hold=True,
)
PRIORITY_POLICY_FIRST_POSITION_V150_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V149_R2_CANDIDATE,
    model_id="maker_priority_v1_50_candidate",
    model_version="1.50-candidate",
    parent_model_id="maker_priority_v1_49_candidate_r2",
    # The customer-base deficit is an economic short.  While its passive
    # recovery intention remains valid, quote the live first position instead
    # of parking behind the book at a stale model ceiling.  This is a passive
    # execution correction only: it does not inherit v2.1 trend discovery or
    # active trend replenishment.
    enable_live_priority_base_replenishment_exposure=True,
)
PRIORITY_POLICY_FIRST_POSITION_V21_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V149_R2_CANDIDATE,
    model_id="maker_priority_v2_1_candidate",
    model_version="2.1-candidate",
    parent_model_id="maker_priority_v1_49_candidate_r2",
    # A confirmed upward repricing invalidates an old low recovery centre.
    # Keep improving a reliable live bid; when the bid has reached the former
    # sale level, or an extremely strong stock is accompanied by a large bond
    # buy that is consuming the offer, restore the customer base actively
    # within the separately audited 0.15-yuan emergency boundary.
    enable_trend_price_discovery_base_replenishment=True,
)
PRIORITY_POLICY_FIRST_POSITION_V22_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V21_CANDIDATE,
    model_id="maker_priority_v2_2_candidate",
    model_version="2.2-candidate",
    parent_model_id="maker_priority_v2_1_candidate",
    # A disconnected top bid and a large unconsumed sell wall are contrary
    # evidence, not an alternate proof of an uptrend.  During fragile opening
    # discovery, the same anomalous low-offer episode may restore the customer
    # base but may not silently create an extra lot without fresh bond support.
    enable_strict_trend_market_structure=True,
    enable_opening_extra_inventory_confirmation=True,
)
PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V22_CANDIDATE,
    model_id="maker_priority_v2_3_candidate",
    model_version="2.3-candidate",
    parent_model_id="maker_priority_v2_2_candidate",
    # Lunch is a suspension within one trading day.  Preserve the last valid
    # intraday price hypothesis across the break when the afternoon book still
    # contains it; otherwise restart from the live midpoint at low confidence.
    # Rolling tape quantities themselves are deliberately not carried.
    enable_midday_intraday_reference_continuity=True,
)
PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE,
    model_id="maker_priority_v2_4_candidate",
    model_version="2.4-candidate",
    parent_model_id="maker_priority_v2_3_candidate",
    # Generalize 2.3's lunch-only correction to the whole trading day.  The
    # close remains an opening fallback only; after intraday discovery, carry
    # the latest compatible price or restart from the live midpoint.
    enable_intraday_reference_continuity=True,
)
PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE,
    model_id="maker_priority_v2_5_candidate",
    model_version="2.5-candidate",
    parent_model_id="maker_priority_v2_4_candidate",
    # The carried price predates the suspect low-offer cluster.  If it is
    # already no higher than that cluster, it must veto an active cross rather
    # than disappear while a remote old ask supplies the only cheapness proof.
    veto_isolated_discount_with_low_carried_reference=True,
)
PRIORITY_POLICY_FIRST_POSITION_V251_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE,
    model_id="maker_priority_v2_51_candidate",
    model_version="2.51-candidate",
    parent_model_id="maker_priority_v2_5_candidate",
    # Keep 2.5 immutable.  This narrow child constrains immature opening
    # quote-midpoint pricing with causal real trades and requires both the
    # user-confirmed 0.10/3,000 and 0.20/5,000 ordinary support bands.
    enable_opening_trade_constrained_reference=True,
    require_nested_ordinary_bid_support=True,
)
PRIORITY_POLICY_FIRST_POSITION_V251_R2_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V251_CANDIDATE,
    model_id="maker_priority_v2_51_candidate_r2",
    model_version="2.51-candidate-r2",
    parent_model_id="maker_priority_v2_51_candidate",
    # Keep the first 2.51 build immutable.  The revision removes its
    # quantity-weighted-median price cap, retains the user-confirmed nested
    # support rule and recognizes a separate real-trade liquidity corridor.
    enable_opening_trade_constrained_reference=False,
    enable_ordinary_liquidity_corridor_entry=True,
)
PRIORITY_POLICY_FIRST_POSITION_V252_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V251_R2_CANDIDATE,
    model_id="maker_priority_v2_52_candidate",
    model_version="2.52-candidate",
    parent_model_id="maker_priority_v2_51_candidate_r2",
    enable_profitable_offer_tail_base_replenishment=True,
    enable_wide_reward_risk_nested_support_override=True,
)
PRIORITY_POLICY_FIRST_POSITION_V25_R2_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE,
    model_id="maker_priority_v2_5_candidate_r2",
    model_version="2.5-candidate-r2",
    parent_model_id="maker_priority_v2_5_candidate",
    # 1.50 recorded a general first-position execution principle rather than
    # a branch-local experiment: while a customer-base recovery intention is
    # still valid, it must stay exposed at the reliable live inside bid.  The
    # original 2.5 account remains immutable; this revision repairs the missed
    # merge without changing any of 2.5's valuation or active permissions.
    enable_live_priority_base_replenishment_exposure=True,
)
PRIORITY_POLICY_FIRST_POSITION_V251_R3_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V251_R2_CANDIDATE,
    model_id="maker_priority_v2_51_candidate_r3",
    model_version="2.51-candidate-r3",
    parent_model_id="maker_priority_v2_51_candidate_r2",
    # Preserve the 2.51 liquidity-corridor and nested-support decisions while
    # applying the already-confirmed live-priority base-recovery principle.
    enable_live_priority_base_replenishment_exposure=True,
)
PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V252_CANDIDATE,
    model_id="maker_priority_v2_52_candidate_r2",
    model_version="2.52-candidate-r2",
    parent_model_id="maker_priority_v2_52_candidate",
    # Keep 2.52's profitable tail recovery and reward/risk entry intact; only
    # repair the ordinary passive customer-base recovery execution path.
    enable_live_priority_base_replenishment_exposure=True,
)
PRIORITY_POLICY_FIRST_POSITION_V26_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE,
    model_id="maker_priority_v2_6_candidate",
    model_version="2.6-candidate",
    parent_model_id="maker_priority_v2_52_candidate_r2",
    # Keep the accepted 136.702 ordinary bid.  The child only manages the
    # resulting extra lot after its original support and high-side route have
    # failed and a materially lower supported corridor has formed.
    enable_support_collapse_capacity_redeployment=True,
)
PRIORITY_POLICY_FIRST_POSITION_V26_R2_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V26_CANDIDATE,
    model_id="maker_priority_v2_6_candidate_r2",
    model_version="2.6-candidate-r2",
    parent_model_id="maker_priority_v2_6_candidate",
    enable_support_collapse_consistency_revision=True,
)
PRIORITY_POLICY_FIRST_POSITION_V26_R3_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V26_R2_CANDIDATE,
    model_id="maker_priority_v2_6_candidate_r3",
    model_version="2.6-candidate-r3",
    parent_model_id="maker_priority_v2_6_candidate_r2",
    retire_recovered_support_collapse_pending_turn=True,
)
PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V26_R3_CANDIDATE,
    model_id="maker_priority_v2_63_candidate",
    model_version="2.63-candidate",
    parent_model_id="maker_priority_v2_6_candidate_r3",
)
PRIORITY_POLICY_FIRST_POSITION_V269_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE,
    model_id="maker_priority_v2_69_candidate",
    model_version="2.69-candidate",
    parent_model_id="maker_priority_v2_63_candidate",
    # Direct rebuild requested by the user. No intermediate candidate
    # profile, global price-grid repair or experimental entry mode is inherited.
    enable_causal_ordinary_inventory_turnover=True,
)
PRIORITY_POLICY_FIRST_POSITION_V270_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE,
    model_id="maker_priority_v2_70_candidate",
    model_version="2.70-candidate",
    parent_model_id="maker_priority_v2_63_candidate",
    enable_causal_ordinary_inventory_turnover=True,
    recognize_consumed_recovered_support=True,
    quote_on_current_ordinary_recovery=True,
)
PRIORITY_POLICY_FIRST_POSITION_V270_R2_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE,
    model_id="maker_priority_v2_70_candidate_r2",
    model_version="2.70-candidate-r2",
    parent_model_id="maker_priority_v2_63_candidate",
    enable_causal_ordinary_inventory_turnover=True,
    recognize_consumed_recovered_support=True,
    quote_on_current_ordinary_recovery=True,
    allow_horizontal_recovery_quote_with_bid_retreat=True,
)
PRIORITY_POLICY_FIRST_POSITION_V271_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE,
    model_id="maker_priority_v2_71_candidate",
    model_version="2.71-candidate",
    parent_model_id="maker_priority_v2_63_candidate",
    enable_causal_ordinary_inventory_turnover=True,
    recognize_consumed_recovered_support=True,
    quote_on_current_ordinary_recovery=True,
    allow_horizontal_recovery_quote_with_bid_retreat=True,
    protect_discounted_ordinary_lot_from_fragile_bid_exit=True,
    require_strict_passive_order_timestamp=True,
)
PRIORITY_POLICY_FIRST_POSITION_V264_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE,
    model_id="maker_priority_v2_64_candidate",
    model_version="2.64-candidate",
    parent_model_id="maker_priority_v2_63_candidate",
    # A filled ordinary extra lot is T-making capacity, not a directional
    # investment.  Preserve every native 2.63 exit first, then fill only a
    # genuine order gap with the reliable live offer.  The inherited 0.25
    # loss boundary keeps the fallback from chasing a deep discontinuity.
    # Its normal 600-second/0.30-yuan low-side plan remains, but one full
    # block of real buying back at the release price proves a fresh upward
    # attack and returns any new entry decision to the unchanged parent.
    enable_guarded_live_priority_extra_inventory_exit_exposure=True,
    guarded_live_exit_maximum_loss=(
        PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE
            .support_collapse_initial_maximum_loss
    ),
    guarded_live_exit_release_reentry_on_high_attack=True,
    retain_guarded_live_exit_across_parent_order_gap=True,
)
PRIORITY_POLICY_FIRST_POSITION_V264_R2_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE,
    model_id="maker_priority_v2_64_candidate_r2",
    model_version="2.64-candidate-r2",
    parent_model_id="maker_priority_v2_63_candidate",
    # The withdrawn first build treated every parent order gap as a falling
    # market and then locked ordinary re-entry behind 600 seconds/0.30 yuan.
    # The rebuild acts only while the parent's existing sell-side repricing
    # evidence is live.  If that evidence clears, cancel the low exit and
    # immediately return both sides of the next decision to unchanged v2.63.
    enable_guarded_live_priority_extra_inventory_exit_exposure=True,
    guarded_live_exit_maximum_loss=None,
    guarded_live_exit_records_stalled_reentry=False,
    guarded_live_exit_release_reentry_on_high_attack=False,
    retain_guarded_live_exit_across_parent_order_gap=True,
    guarded_live_exit_requires_current_sell_side_repricing=True,
    guarded_live_exit_protects_special_rapid_gap=False,
)
PRIORITY_POLICY_FIRST_POSITION_V265_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V264_R2_CANDIDATE,
    model_id="maker_priority_v2_65_candidate",
    model_version="2.65-candidate",
    parent_model_id="maker_priority_v2_64_candidate_r2",
    # The native near-flat and guarded exits serve the same ordinary T lot.
    # Neither may attach an old exit-price cap to the next independent trade.
    ordinary_risk_exit_uses_fresh_reentry=True,
)
PRIORITY_POLICY_FIRST_POSITION_V266_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V265_CANDIDATE,
    model_id="maker_priority_v2_66_candidate",
    model_version="2.66-candidate",
    parent_model_id="maker_priority_v2_65_candidate",
    ordinary_entry_sell_pressure_mode="all_repricing",
)
PRIORITY_POLICY_FIRST_POSITION_V266_R2_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V265_CANDIDATE,
    model_id="maker_priority_v2_66_candidate_r2",
    model_version="2.66-candidate-r2",
    parent_model_id="maker_priority_v2_65_candidate",
    ordinary_entry_sell_pressure_mode="edge_stress",
)
PRIORITY_POLICY_FIRST_POSITION_V266_R3_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V265_CANDIDATE,
    model_id="maker_priority_v2_66_candidate_r3",
    model_version="2.66-candidate-r3",
    parent_model_id="maker_priority_v2_65_candidate",
    ordinary_entry_sell_pressure_mode="reentry_repricing",
)
PRIORITY_POLICY_FIRST_POSITION_V267_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V266_R3_CANDIDATE,
    model_id="maker_priority_v2_67_candidate",
    model_version="2.67-candidate",
    parent_model_id="maker_priority_v2_66_candidate_r3",
    normalize_native_priority_price_grid=True,
)
PRIORITY_POLICY_FIRST_POSITION_V268_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V267_CANDIDATE,
    model_id="maker_priority_v2_68_candidate",
    model_version="2.68-candidate",
    parent_model_id="maker_priority_v2_67_candidate",
    enable_ordinary_tape_turnover_regime=True,
)
PRIORITY_POLICY_FIRST_POSITION_V268_R2_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V268_CANDIDATE,
    model_id="maker_priority_v2_68_candidate_r2",
    model_version="2.68-candidate-r2",
    parent_model_id="maker_priority_v2_68_candidate",
    enable_ordinary_tape_horizontal_recovery=True,
)
PRIORITY_POLICY_FIRST_POSITION_V268_R3_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V268_R2_CANDIDATE,
    model_id="maker_priority_v2_68_candidate_r3",
    model_version="2.68-candidate-r3",
    parent_model_id="maker_priority_v2_68_candidate_r2",
    preserve_ordinary_tape_current_wide_corridor=True,
)
ONE_HAND_POLICY_V01_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE,
    model_id="maker_one_hand_v0_1_candidate",
    model_version="0.1-candidate",
    parent_model_id="maker_priority_v2_52_candidate_r2",
)
SHARED_THOUSAND_POLICY_V01_CANDIDATE = replace(
    PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE,
    model_id="maker_shared_1000_v0_1_candidate",
    model_version="0.1-candidate",
    parent_model_id="maker_priority_v2_52_candidate_r2",
)
SHARED_THOUSAND_POLICY_V011_WITHDRAWN_CANDIDATE = replace(
    SHARED_THOUSAND_POLICY_V01_CANDIDATE,
    model_id="maker_shared_1000_v0_11_candidate",
    model_version="0.11-candidate",
    parent_model_id="maker_shared_1000_v0_1_candidate",
    enable_live_priority_extra_inventory_exit_exposure=True,
)
SHARED_THOUSAND_POLICY_V011_CANDIDATE = replace(
    SHARED_THOUSAND_POLICY_V011_WITHDRAWN_CANDIDATE,
    model_id="maker_shared_1000_v0_11_candidate_r2",
    model_version="0.11-candidate-r2",
    parent_model_id="maker_shared_1000_v0_11_candidate",
    # The withdrawn build replaced every eligible parent exit with a live
    # ask-following order.  The revision leaves the complete v0.1 exit path
    # untouched and uses the guarded switch only to fill a genuine order gap.
    enable_live_priority_extra_inventory_exit_exposure=False,
    enable_guarded_live_priority_extra_inventory_exit_exposure=True,
)
SHARED_THOUSAND_POLICY_V02_CANDIDATE = replace(
    SHARED_THOUSAND_POLICY_V01_CANDIDATE,
    model_id="maker_shared_1000_v0_2_candidate",
    model_version="0.2-candidate",
    parent_model_id="maker_shared_1000_v0_1_candidate",
)
SHARED_THOUSAND_POLICY_V03_CANDIDATE = replace(
    SHARED_THOUSAND_POLICY_V02_CANDIDATE,
    model_id="maker_shared_1000_v0_3_candidate",
    model_version="0.3-candidate",
    parent_model_id="maker_shared_1000_v0_2_candidate",
    enable_session_resilient_ordinary_entry=True,
)
SHARED_THOUSAND_POLICY_V031_CANDIDATE = replace(
    SHARED_THOUSAND_POLICY_V03_CANDIDATE,
    model_id="maker_shared_1000_v0_31_candidate",
    model_version="0.31-candidate",
    parent_model_id="maker_shared_1000_v0_3_candidate",
    # Merge only the repaired v0.11 order-gap fallback.  The guarded default
    # remains limited to ordinary low_bid_reversion lots, so v0.3's
    # evidence-specific session_resilient_value_entry keeps its native exit.
    enable_guarded_live_priority_extra_inventory_exit_exposure=True,
)
SHARED_THOUSAND_POLICY_V012_CANDIDATE = replace(
    SHARED_THOUSAND_POLICY_V011_CANDIDATE,
    model_id="maker_shared_1000_v0_12_candidate",
    model_version="0.12-candidate",
    parent_model_id="maker_shared_1000_v0_11_candidate_r2",
    # Keep the repaired v0.11 exit path and add v0.3's complete resilient
    # candidate permission.  The research entry also uses the v0.3
    # capital-time allocator; the profile switch alone is not the model.
    enable_session_resilient_ordinary_entry=True,
)
SHARED_THOUSAND_POLICY_V013_CANDIDATE = replace(
    SHARED_THOUSAND_POLICY_V012_CANDIDATE,
    model_id="maker_shared_1000_v0_13_candidate",
    model_version="0.13-candidate",
    parent_model_id="maker_shared_1000_v0_12_candidate",
    # The profile remains behaviorally identical to v0.12.  V0.13's only
    # decision change lives in its independent shared-capital allocator,
    # which values a fully consumed deep-discount offer from the remaining
    # post-sweep ask book instead of the offer being bought.
)
SHARED_THOUSAND_POLICY_V014_CANDIDATE = replace(
    SHARED_THOUSAND_POLICY_V013_CANDIDATE,
    model_id="maker_shared_1000_v0_14_candidate",
    model_version="0.14-candidate",
    parent_model_id="maker_shared_1000_v0_13_candidate",
    enable_shared_current_opportunity_reentry=True,
    enable_shared_resilient_exit_gap=True,
)
SHARED_THOUSAND_POLICY_V015_CANDIDATE = replace(
    SHARED_THOUSAND_POLICY_V014_CANDIDATE,
    model_id="maker_shared_1000_v0_15_candidate",
    model_version="0.15-candidate",
    parent_model_id="maker_shared_1000_v0_14_candidate",
    # Offline only: native trading is unchanged; use the v0.15 allocator.
)
SHARED_THOUSAND_POLICY_V015_R2_CANDIDATE = replace(
    SHARED_THOUSAND_POLICY_V014_CANDIDATE,
    model_id="maker_shared_1000_v0_15_candidate_r2",
    model_version="0.15-candidate-r2",
    parent_model_id="maker_shared_1000_v0_14_candidate",
    # Independent offline boundary repair; never replaces the first v0.15.
)
SHARED_THOUSAND_POLICY_V016_CANDIDATE = replace(
    SHARED_THOUSAND_POLICY_V014_CANDIDATE,
    model_id="maker_shared_1000_v0_16_candidate",
    model_version="0.16-candidate",
    parent_model_id="maker_shared_1000_v0_14_candidate",
    # Offline only; requires the independent causal engine and allocator.
)
SHARED_THOUSAND_POLICY_V016_R2_CANDIDATE = replace(
    SHARED_THOUSAND_POLICY_V016_CANDIDATE,
    model_id="maker_shared_1000_v0_16_candidate_r2",
    model_version="0.16-candidate-r2",
    parent_model_id="maker_shared_1000_v0_16_candidate",
)

# Only the selector's inherited evidence parameters are reused. This identity
# must run the independent simple quote loop, never native trading decisions.
DADAO_POLICY_V01_R2_CANDIDATE = replace(
    SHARED_THOUSAND_POLICY_V016_CANDIDATE,
    model_id="maker_dadao_v0_1_candidate_r2",
    model_version="0.1-candidate-r2",
    parent_model_id="probe_top_compare_20260908_v1_switch_v013",
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
    windfall_order_quantity_bonds=10.0,
    windfall_initial_credit_cny=2_000.0,
    windfall_minimum_discount=1.50,
    windfall_minimum_book_gap=1.00,
)
WINDFALL_POLICY_V11_CANDIDATE = replace(
    WINDFALL_POLICY_V10,
    model_id="maker_windfall_v1_1_candidate",
    model_version="1.1-candidate",
    parent_model_id="maker_windfall_v1_0",
    exclude_wide_persistent_windfall_reference=True,
)
WINDFALL_POLICY_V20_CANDIDATE = replace(
    WINDFALL_POLICY_V11_CANDIDATE,
    model_id="maker_windfall_v2_0_candidate",
    model_version="2.0-candidate",
    parent_model_id="maker_windfall_v1_1_candidate",
    latest_entry_time="15:29:59.999",
    use_unpolluted_windfall_reference=True,
    windfall_order_quantity_bonds=1_000.0,
    windfall_initial_credit_cny=0.0,
    windfall_minimum_discount=1.00,
    windfall_minimum_book_gap=1.00,
    windfall_capacity_funded=True,
    enable_active_windfall_offer_sweep=True,
    windfall_minimum_active_offer_bonds=1_000.0,
)

WINDFALL_POLICIES = {
    WINDFALL_POLICY_V10.model_id: WINDFALL_POLICY_V10,
    WINDFALL_POLICY_V11_CANDIDATE.model_id: WINDFALL_POLICY_V11_CANDIDATE,
    WINDFALL_POLICY_V20_CANDIDATE.model_id: WINDFALL_POLICY_V20_CANDIDATE,
}


def configured_windfall_policy(config: AppConfig) -> MakerPolicyProfile:
    try:
        return WINDFALL_POLICIES[
            config.maker_paper.super_windfall_model_id
        ]
    except KeyError as exc:
        raise ValueError(
            "Unknown super windfall model ID: "
            f"{config.maker_paper.super_windfall_model_id}"
        ) from exc


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
    PRIORITY_POLICY_FIRST_POSITION_V144.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V144
    ),
    PRIORITY_POLICY_FIRST_POSITION_V145.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V145
    ),
    PRIORITY_POLICY_FIRST_POSITION_V146.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V146
    ),
    PRIORITY_POLICY_FIRST_POSITION_V147.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V147
    ),
    PRIORITY_POLICY_FIRST_POSITION_V148_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V148_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V149_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V149_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V149_R2_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V149_R2_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V150_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V150_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V21_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V21_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V22_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V22_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V23_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V24_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V25_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V251_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V251_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V251_R2_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V251_R2_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V252_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V252_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V25_R2_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V25_R2_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V251_R3_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V251_R3_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V252_R2_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V26_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V26_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V26_R2_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V26_R2_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V26_R3_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V26_R3_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V263_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V264_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V264_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V264_R2_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V264_R2_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V265_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V265_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V266_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V266_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V266_R2_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V266_R2_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V266_R3_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V266_R3_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V267_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V267_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V268_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V268_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V268_R2_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V268_R2_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V268_R3_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V268_R3_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V269_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V269_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V270_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V270_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V270_R2_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V270_R2_CANDIDATE
    ),
    PRIORITY_POLICY_FIRST_POSITION_V271_CANDIDATE.model_id: (
        PRIORITY_POLICY_FIRST_POSITION_V271_CANDIDATE
    ),
    SHARED_THOUSAND_POLICY_V01_CANDIDATE.model_id: (
        SHARED_THOUSAND_POLICY_V01_CANDIDATE
    ),
    SHARED_THOUSAND_POLICY_V013_CANDIDATE.model_id: (
        SHARED_THOUSAND_POLICY_V013_CANDIDATE
    ),
    SHARED_THOUSAND_POLICY_V016_R2_CANDIDATE.model_id: (
        SHARED_THOUSAND_POLICY_V016_R2_CANDIDATE
    ),
    DADAO_POLICY_V01_R2_CANDIDATE.model_id: DADAO_POLICY_V01_R2_CANDIDATE,
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
        strategy_ids.append(windfall_strategy_id(
            config, bond_code, configured_windfall_policy(config),
        ))
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


def windfall_strategy_id(
    config: AppConfig, bond_code: str, policy: MakerPolicyProfile,
) -> str:
    """Preserve legacy ledgers while giving the redesigned branch a new one."""

    if policy.model_id in {
        WINDFALL_POLICY_V10.model_id,
        WINDFALL_POLICY_V11_CANDIDATE.model_id,
    }:
        return f"{maker_strategy_prefix(config, bond_code)}_super_windfall"
    return maker_comparison_strategy_id(config, bond_code, policy)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _floor_to_tick(
    value: float, price_tick: float, *, snap_grid_noise: bool = False,
) -> float:
    """Quantize a simulated limit price down to a legal exchange price tick."""
    step = Decimal(str(price_tick))
    raw_units = Decimal(str(value)) / step
    if snap_grid_noise:
        nearest = raw_units.to_integral_value()
        if abs(raw_units - nearest) <= Decimal("0.000000001"):
            raw_units = nearest
    units = raw_units.to_integral_value(rounding=ROUND_FLOOR)
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
    protective_bid_floor_price: float = 0.0
    protective_bid_ceiling_price: float = 0.0
    protective_bid_entry_bonds: float = 0.0
    protective_bid_entry_edge: float = 0.0
    protective_bid_last_bonds: float = 0.0
    protective_bid_last_ts_ms: int = 0
    protective_bid_last_damage_ts_ms: int = 0


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
    protective_bid_floor_price: float = 0.0
    protective_bid_ceiling_price: float = 0.0
    protective_bid_entry_bonds: float = 0.0
    protective_bid_entry_edge: float = 0.0
    joint_corridor_high_trade_bonds: float = 0.0
    joint_corridor_ask_supply_bonds: float = 0.0
    joint_corridor_emergency_loss: float = 0.0
    joint_corridor_reward_risk: float = 0.0
    joint_corridor_exceptional_support: bool = False
    isolated_top_bid_price: float = 0.0
    isolated_top_bid_bonds: float = 0.0
    reliable_replenishment_bid_price: float = 0.0
    near_ask_supply_bonds: float = 0.0
    isolated_near_ask_floor_price: float = 0.0
    isolated_near_ask_ceiling_price: float = 0.0
    isolated_confirmed_ask_attack_bonds: float = 0.0
    isolated_last_incompatible_sell_ts_ms: int = 0
    isolated_ask_attack_events: deque[tuple[int, float]] = field(
        default_factory=deque,
    )
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


@dataclass
class AdjacentBidCushionObservation:
    floor_price: float
    ceiling_price: float
    first_seen_ms: int
    last_seen_ms: int
    observations: int
    peak_bonds: float


@dataclass(frozen=True)
class AdjacentBidCushionDecision:
    price: float
    floor_price: float
    ceiling_price: float
    entry_bonds: float
    entry_edge: float


@dataclass(frozen=True)
class JointCausalCorridorDecision:
    """One shared causal permission for a neutral two-sided quote."""

    price: float
    sell_price: float
    floor_price: float
    ceiling_price: float
    entry_bonds: float
    entry_edge: float
    high_trade_bonds: float
    ask_supply_bonds: float
    emergency_loss: float
    reward_risk: float
    exceptional_support: bool


@dataclass(frozen=True)
class IsolatedTopBidDecision:
    isolated_price: float
    isolated_bonds: float
    reliable_bid_price: float
    near_ask_supply_bonds: float
    near_ask_floor_price: float
    near_ask_ceiling_price: float


@dataclass(frozen=True)
class SessionResilientEntryDecision:
    """Causal same-day evidence admitting one ordinary value candidate."""

    entry_price: float
    exit_price: float
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
    composite_quality: float


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
    strict_trend_wall_floor_price: float = 0.0
    strict_trend_wall_ceiling_price: float = 0.0
    strict_trend_wall_peak_bonds: float = 0.0
    strict_trend_wall_confirmed_attack_bonds: float = 0.0
    strict_trend_wall_last_seen_ts_ms: int = 0
    strict_trend_wall_confirmed_ts_ms: int = 0
    last_base_replenishment_price: float = 0.0
    last_base_replenishment_ts_ms: int = 0
    last_profitable_visible_bid_replenishment_ts_ms: int = 0
    last_extra_exit_ts_ms: int = 0
    last_priority_extra_inventory_exit_price: float = 0.0
    last_priority_extra_inventory_exit_ts_ms: int = 0
    last_falling_profitable_exit_price: float = 0.0
    last_falling_profitable_exit_ts_ms: int = 0
    last_stalled_extra_exit_price: float = 0.0
    last_stalled_extra_exit_ts_ms: int = 0
    last_shared_reentry_exit_ts_ms: int = 0
    shared_reentry_recovery_ts_ms: int = 0
    shared_reentry_recovery_bid: float = 0.0
    shared_reentry_recovery_ask: float = 0.0
    last_ordinary_risk_exit_ts_ms: int = 0
    last_new_extra_entry_ts_ms: int = 0
    ordinary_tape_pressure_active: bool = False
    ordinary_tape_pressure_since_ms: int = 0
    ordinary_tape_recovery_ts_ms: int = 0
    ordinary_tape_recovery_bid: float = 0.0
    ordinary_tape_update_key: tuple[int, int] = (0, 0)
    last_guarded_live_exit_price: float = 0.0
    last_guarded_live_exit_ts_ms: int = 0
    guarded_live_exit_reentry_released: bool = False
    last_support_collapse_exit_price: float = 0.0
    last_support_collapse_exit_ts_ms: int = 0
    last_support_collapse_entry_price: float = 0.0
    support_collapse_extra_reentry_released: bool = False
    support_collapse_base_short_released: bool = False
    pending_replenishment_exact_fill_buffer: float = 0.0
    pending_repeated_turn_replenishment_price: float = 0.0
    joint_corridor_base_short_bonds: float = 0.0
    joint_corridor_base_short_sell_price: float = 0.0
    joint_corridor_base_short_buy_price: float = 0.0
    joint_corridor_base_short_support_floor: float = 0.0
    joint_corridor_base_short_support_ceiling: float = 0.0
    joint_corridor_base_short_support_bonds: float = 0.0
    joint_corridor_base_short_ask_supply_bonds: float = 0.0
    pending_inventory_turn_quantity: float = 0.0
    pending_inventory_turn_sale_value: float = 0.0
    pending_support_collapse_turn_quantity: float = 0.0
    pending_support_collapse_turn_sale_value: float = 0.0
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
        buy_fill_guard: Callable[
            [MakerAccount, ReplayTick, MakerOrder, float, str, str], bool
        ] | None = None,
        fill_observer: Callable[
            [MakerAccount, ReplayTick, MakerOrder, str, float, str], None
        ] | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.bond_code = bond_code or config.qmt.bond_code
        self.stock_code = maker_underlying_stock_code(config, self.bond_code)
        self.strategy_prefix = strategy_prefix or maker_strategy_prefix(
            config, self.bond_code
        )
        self.priority_policy = priority_policy or PRIORITY_POLICY_V11
        if (
            self.priority_policy.model_id == "maker_dadao_v0_1_candidate_r2"
            and not getattr(self, "dadao_execution", False)
        ):
            raise ValueError("Dadao requires DadaoLedgerEngine and its simple allocator")
        if (
            self.priority_policy.model_id == "maker_shared_1000_v0_16_candidate"
            and not getattr(self, "causal_shared_execution", False)
        ):
            raise ValueError("v0.16 requires CausalSharedMakerEngine")
        if (
            self.priority_policy.model_id == "maker_shared_1000_v0_16_candidate_r2"
            and not getattr(self, "arrival_shared_execution", False)
        ):
            raise ValueError("v0.16 r2 requires ArrivalSharedMakerEngine")
        self.queue_policy = queue_policy or QUEUE_POLICY_V10
        self.windfall_policy = (
            windfall_policy or configured_windfall_policy(config)
        )
        self.windfall_strategy_id = windfall_strategy_id(
            config, self.bond_code, self.windfall_policy,
        )
        paper = config.maker_paper
        self.fill_modes = tuple(
            paper.fill_modes if fill_modes is None else fill_modes
        )
        self.include_windfall = (
            paper.super_windfall_enabled
            if include_windfall is None else include_windfall
        )
        self.strategy_ids_by_mode = dict(strategy_ids_by_mode or {})
        self.buy_fill_guard = buy_fill_guard
        self.fill_observer = fill_observer
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
        self.last_market_assessment: MarketAssessment | None = None
        self.accounts: dict[str, MakerAccount] = {}
        self.market_date: str | None = None
        self.fills_this_run = 0
        self.previous_close_reference = 0.0
        self.observed_market_trade = False
        self.last_market_trade_ts_ms = 0
        self.last_confirmed_rise_trade_ts_ms = 0
        self.last_confirmed_rise_price = 0.0
        # Opening discovery is a day-level causal episode, not a rolling
        # five-minute label.  Once sufficiently durable evidence has matured a
        # model's price discovery, later expiry of that evidence must not make
        # the afternoon look like a second opening.  Keep this keyed by model
        # ID because one engine can host independently versioned accounts.
        self.opening_discovery_matured_models: set[str] = set()
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
        self.midday_continuity_references: dict[
            str, tuple[float, str]
        ] = {}
        self.intraday_continuity_references: dict[
            str, tuple[float, str]
        ] = {}
        self.intraday_working_references_by_model: dict[str, float] = {}
        self.last_visible_bid_wall_price = 0.0
        self.last_visible_bid_wall_bonds = 0.0
        self.last_visible_bid_wall_ts_ms = 0
        self.last_bid_wall_left_book_ts_ms = 0
        self.bid_wall_currently_visible = False
        self.visible_bid_wall_first_seen_ms: dict[float, int] = {}
        self.adjacent_bid_cushion_observations: dict[
            tuple[float, float], AdjacentBidCushionObservation
        ] = {}
        self.joint_corridor_book_history: deque[ReplayTick] = deque()
        self.last_legacy_reliable_reference = 0.0
        self.last_legacy_reliable_reference_ts_ms = 0
        self.legacy_breakout_support_price = 0.0
        self.legacy_breakout_support_ts_ms = 0
        self.legacy_ask_walls: dict[float, LegacyAskWall] = {}
        self.strict_breakout_price = 0.0
        self.strict_breakout_ts_ms = 0
        self.strict_breakout_initial_tail_bonds = 0.0
        self.strict_breakout_cleared = False
        self.strict_breakout_failed = False

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
        self._update_strict_breakout_episode_from_book(tick, emitted)
        self._observe_joint_corridor_book(tick)

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
            self.last_market_trade_ts_ms = tick.market_ts_ms

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
        self.last_market_assessment = assessment
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
            account_assessment = self._assessment_for_account(
                account, tick, assessment,
            )
            self._update_base_short_rising_buy_sequence(account, tick)
            stopped_trend_short = self._active_trend_base_short_replenishment(
                account, tick, account_assessment, persist=persist,
                received_ts_ns=(
                    received_ts_ns or tick.market_ts_ms * 1_000_000
                ),
            )
            restored_profitable_tail_short = (
                not stopped_trend_short
                and self._active_profitable_offer_tail_base_replenishment(
                    account, tick, persist=persist,
                    received_ts_ns=(
                        received_ts_ns or tick.market_ts_ms * 1_000_000
                    ),
                )
            )
            stopped_joint_corridor_short = (
                not stopped_trend_short
                and not restored_profitable_tail_short
                and self._active_joint_corridor_base_short_stop(
                    account, tick, assessment, persist=persist,
                    received_ts_ns=(
                        received_ts_ns or tick.market_ts_ms * 1_000_000
                    ),
                )
            )
            stopped_confirmed_rise_short = False
            if (
                not restored_profitable_tail_short
                and not stopped_joint_corridor_short
            ):
                stopped_confirmed_rise_short = (
                    self._active_confirmed_rising_near_flat_base_short_stop(
                        account, tick, assessment, persist=persist,
                        received_ts_ns=(
                            received_ts_ns or tick.market_ts_ms * 1_000_000
                        ),
                    )
                )
            restored_medium_short = False
            if (
                not stopped_joint_corridor_short
                and not restored_profitable_tail_short
                and not stopped_confirmed_rise_short
            ):
                restored_medium_short = (
                    self._active_medium_base_short_replenishment(
                        account, tick, assessment, persist=persist,
                        received_ts_ns=(
                            received_ts_ns or tick.market_ts_ms * 1_000_000
                        ),
                    )
                )
            restored_attacked_isolated_short = False
            if (
                not stopped_joint_corridor_short
                and not restored_profitable_tail_short
                and not stopped_confirmed_rise_short
                and not restored_medium_short
            ):
                restored_attacked_isolated_short = (
                    self._active_isolated_top_bid_wall_attack_replenishment(
                        account, tick, assessment, persist=persist,
                        received_ts_ns=(
                            received_ts_ns or tick.market_ts_ms * 1_000_000
                        ),
                    )
                )
            if (
                not stopped_joint_corridor_short
                and not restored_profitable_tail_short
                and not stopped_confirmed_rise_short
                and not restored_medium_short
                and not restored_attacked_isolated_short
            ):
                self._active_discount_entry(
                    account, tick, assessment, persist=persist,
                )
        for account in self._standard_accounts():
            if account.policy.enable_priority_v11_extensions:
                self._active_adjacent_bid_cushion_risk_exit(
                    account, tick, assessment, persist=persist,
                    received_ts_ns=(
                        received_ts_ns or tick.market_ts_ms * 1_000_000
                    ),
                )
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

    def _assessment_for_account(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment,
    ) -> MarketAssessment:
        account_assessment = assessment
        if account.policy.enable_trend_price_discovery_base_replenishment:
            wall_is_clear = True
            if account.policy.enable_strict_trend_market_structure:
                wall_is_clear = self._strict_trend_wall_discovery_state(
                    account, tick,
                )
            if wall_is_clear:
                account_assessment = trend_price_discovery_assessment(
                    assessment, tick, self.parameters,
                    stock_extremely_strong=(
                        self.analyzer.stock_is_extremely_strong(
                            account.policy
                                .trend_stock_acceleration_minimum_return,
                        )
                    ),
                    require_connected_bid_staircase=(
                        account.policy.enable_strict_trend_market_structure
                    ),
                    maximum_overhead_ask_bonds=None,
                    overhead_ask_band=(
                        account.policy.strict_trend_overhead_ask_band
                    ),
                    minimum_secondary_bid_bonds=(
                        account.policy
                            .strict_trend_minimum_secondary_bid_multiple
                        * self.parameters.order_quantity_bonds
                    ),
                )
        if (
            account_assessment.reference_source != "previous_close"
            and account_assessment.reference_price > 0
        ):
            self.intraday_working_references_by_model[
                account.policy.model_id
            ] = account_assessment.reference_price
        continuity = self._reference_continuity(
            account.policy,
            tick,
            account_assessment.reference_price,
            account_assessment.reference_source,
        )
        if continuity is None:
            return account_assessment
        reference, source = continuity
        carried = source in {
            "midday_carried_intraday_reference",
            "carried_intraday_reference",
        }
        return replace(
            account_assessment,
            reference_price=reference,
            reference_low=min(tick.bid1, reference),
            reference_high=max(tick.ask1, reference),
            reference_source=source,
            reference_confidence=0.35 if carried else 0.20,
            evidence=account_assessment.evidence + (
                (
                    (
                        "午休后继承当日上午盘中工作参考"
                        if source == "midday_carried_intraday_reference"
                        else "沿用当日最近盘中工作参考"
                    )
                    if carried
                    else (
                        "午后盘口脱离上午参考，按当前中点低置信重启"
                        if source == "midday_current_midpoint_reset"
                        else "当前盘口脱离最近盘中参考，按当前中点低置信重启"
                    )
                ),
            ),
        )

    def _opening_trade_reference_cap(
        self,
        policy: MakerPolicyProfile,
        tick: ReplayTick,
        reference: float,
        source: str,
    ) -> float | None:
        """Cap an immature quote-derived opening reference with real trades.

        The clock is only a prior.  Trend discovery, strong aggressive bond
        buying or sufficiently dense two-sided trading can end the opening
        episode early.  A short-lived ordinary trade anchor before 10:00 is
        trusted for its current frame but is not durable enough to unlock the
        rest of the day.  After 10:00, a reliable trade anchor can confirm that
        price discovery has matured; sparse or one-sided evidence cannot.
        """

        if not policy.enable_opening_trade_constrained_reference:
            return None
        if (
            tick.market_time < "09:30:00.000"
            or reference <= 0
        ):
            return None
        if policy.model_id in self.opening_discovery_matured_models:
            return None
        durable_early_sources = {
            "trend_price_discovery",
            "large_buy_breakout_support",
        }
        if source in durable_early_sources:
            self.opening_discovery_matured_models.add(policy.model_id)
            return None
        if source == "intraday_trade_anchor":
            if tick.market_time >= policy.opening_discovery_nominal_end_time:
                self.opening_discovery_matured_models.add(policy.model_id)
            return None
        low_confidence_sources = {
            "persistent_inside_market",
            "current_midpoint",
            "carried_intraday_reference",
            "midday_carried_intraday_reference",
            "intraday_current_midpoint_reset",
            "midday_current_midpoint_reset",
            "retained_intraday_working_reference",
        }
        if source not in low_confidence_sources:
            return None
        cutoff_ms = (
            tick.market_ts_ms
            - policy.opening_discovery_trade_window_seconds * 1_000
        )
        recent_trades = tuple(
            event for event in self.analyzer.trade_evidence
            if event.market_ts_ms >= cutoff_ms and event.bonds > 0
        )
        if not recent_trades:
            return None
        buy_bonds = sum(
            event.bonds for event in recent_trades if event.side == "buy"
        )
        sell_bonds = sum(
            event.bonds for event in recent_trades if event.side == "sell"
        )
        total_bonds = buy_bonds + sell_bonds
        event_count = sum(event.transactions for event in recent_trades)
        prices = tuple(event.price for event in recent_trades)
        price_range = max(prices) - min(prices)
        confirmed_buying = (
            buy_bonds + 1e-9
                >= policy.opening_discovery_confirmed_buy_bonds
            and buy_bonds + 1e-9
                >= sell_bonds
                    * policy.opening_discovery_buy_dominance_ratio
        )
        early_two_sided_discovery = (
            event_count >= policy.opening_discovery_early_minimum_trade_events
            and total_bonds + 1e-9
                >= policy.opening_discovery_early_minimum_trade_bonds
            and min(buy_bonds, sell_bonds) + 1e-9
                >= policy.opening_discovery_early_minimum_two_sided_bonds
            and price_range
                <= policy.opening_discovery_maximum_trade_range + 1e-9
        )
        post_nominal_two_sided_discovery = (
            tick.market_time >= policy.opening_discovery_nominal_end_time
            and event_count >= policy.opening_discovery_minimum_trade_events
            and total_bonds + 1e-9
                >= policy.opening_discovery_minimum_trade_bonds
            and min(buy_bonds, sell_bonds) + 1e-9
                >= policy.opening_discovery_minimum_two_sided_bonds
            and price_range
                <= policy.opening_discovery_maximum_trade_range + 1e-9
        )
        if (
            confirmed_buying
            or early_two_sided_discovery
            or post_nominal_two_sided_discovery
        ):
            self.opening_discovery_matured_models.add(policy.model_id)
            return None
        trade_reference = self.analyzer.recent_trade_reference(
            tick.market_ts_ms,
            policy.opening_discovery_trade_window_seconds,
        )
        if (
            trade_reference is None
            or reference - trade_reference
                <= self.parameters.fair_price_tolerance + 1e-9
        ):
            return None
        return trade_reference

    def _reference_continuity(
        self,
        policy: MakerPolicyProfile,
        tick: ReplayTick,
        reference: float,
        source: str,
    ) -> tuple[float, str] | None:
        if policy.enable_intraday_reference_continuity:
            return self._intraday_continuity_reference(
                policy, tick, reference, source,
            )
        return self._midday_continuity_reference(
            policy, tick, reference, source,
        )

    def _intraday_continuity_reference(
        self,
        policy: MakerPolicyProfile,
        tick: ReplayTick,
        reference: float,
        source: str,
    ) -> tuple[float, str] | None:
        """Keep today's latest price hypothesis after rolling evidence expires."""

        if (
            not policy.enable_intraday_reference_continuity
            or tick.ask1 <= tick.bid1
            or tick.bid1 <= 0
        ):
            return None
        model_id = policy.model_id
        if source != "previous_close" and reference > 0:
            # Price knowledge survives; the analyzer's rolling quantities,
            # direction and trend state do not.
            self.intraday_continuity_references[model_id] = (
                reference,
                "carried_intraday_reference",
            )
            self.intraday_working_references_by_model[model_id] = reference
            return None
        if source != "previous_close":
            return None
        carried_reference, carried_source = (
            self.intraday_continuity_references.get(
                model_id,
                (
                    self.intraday_working_references_by_model.get(
                        model_id, 0.0,
                    ),
                    "carried_intraday_reference",
                ),
            )
        )
        if carried_reference <= 0:
            return None
        tolerance = self.parameters.fair_price_tolerance
        if (
            tick.bid1 - tolerance - 1e-9
            <= carried_reference
            <= tick.ask1 + tolerance + 1e-9
        ):
            result = (carried_reference, carried_source)
        else:
            result = (
                (tick.bid1 + tick.ask1) / 2,
                "intraday_current_midpoint_reset",
            )
        self.intraday_continuity_references[model_id] = result
        self.intraday_working_references_by_model[model_id] = result[0]
        return result

    def _midday_continuity_reference(
        self,
        policy: MakerPolicyProfile,
        tick: ReplayTick,
        reference: float,
        source: str,
    ) -> tuple[float, str] | None:
        """Keep price knowledge, but never stale rolling flow, over lunch."""

        if (
            not policy.enable_midday_intraday_reference_continuity
            or tick.market_time < "13:00:00.000"
            or tick.ask1 <= tick.bid1
            or tick.bid1 <= 0
        ):
            return None
        model_id = policy.model_id
        if source != "previous_close" and reference > 0:
            # Fresh afternoon discovery supersedes the inherited price.  Save
            # only its price for a later evidence-window expiry; flow and
            # direction statistics remain owned by the analyzer's windows.
            self.midday_continuity_references[model_id] = (
                reference,
                "midday_carried_intraday_reference",
            )
            self.intraday_working_references_by_model[model_id] = reference
            return None
        if source != "previous_close":
            return None
        carried_reference, carried_source = (
            self.midday_continuity_references.get(
                model_id,
                (
                    self.intraday_working_references_by_model.get(
                        model_id,
                        self.last_intraday_working_reference,
                    ),
                    "midday_carried_intraday_reference",
                ),
            )
        )
        if carried_reference <= 0:
            return None
        tolerance = self.parameters.fair_price_tolerance
        if (
            tick.bid1 - tolerance - 1e-9
            <= carried_reference
            <= tick.ask1 + tolerance + 1e-9
        ):
            result = (carried_reference, carried_source)
        else:
            result = (
                (tick.bid1 + tick.ask1) / 2,
                "midday_current_midpoint_reset",
            )
        self.midday_continuity_references[model_id] = result
        return result

    def _strict_trend_wall_discovery_state(
        self, account: MakerAccount, tick: ReplayTick,
    ) -> bool:
        """Track whether a large nearby offer wall was actually consumed.

        A wall disappearing from Level 1 is not proof of aggressive buying.
        The 2.2 overlay therefore retains the observed price band for a short
        causal window and attributes attack volume only when a real buy print
        reaches that previously visible band.  The return value is
        ``True`` only when no unresolved large wall remains.
        """

        policy = account.policy
        risk_block = self.parameters.order_quantity_bonds
        maximum_remaining = (
            policy.strict_trend_maximum_overhead_ask_multiple * risk_block
        )
        minimum_tracked_wall = (
            policy.strict_trend_minimum_tracked_wall_multiple * risk_block
        )
        minimum_attack = (
            policy.strict_trend_minimum_wall_attack_multiple * risk_block
        )
        band = policy.strict_trend_overhead_ask_band
        tolerance = self.parameters.fair_price_tolerance
        window_ms = policy.strict_trend_wall_attack_window_seconds * 1_000
        valid_asks = tuple(
            (price, bonds) for price, bonds in tick.asks
            if price > 0 and bonds > 0
        )
        current_near_asks = tuple(
            (price, bonds) for price, bonds in valid_asks
            if tick.ask1 <= price <= tick.ask1 + band + 1e-9
        )
        current_near_supply = sum(
            bonds for _, bonds in current_near_asks
        )
        severe_disconnected_top_bid = (
            len(tick.bids) >= 2
            and tick.bid1 - tick.bids[1][0] + 1e-9
                >= self.parameters.minimum_fragile_top_bid_gap
        )

        active = account.strict_trend_wall_floor_price > 0
        if active:
            floor = account.strict_trend_wall_floor_price
            ceiling = account.strict_trend_wall_ceiling_price
            previous_same_band = sum(
                bonds for price, bonds in account.last_asks
                if floor - tolerance <= price <= ceiling + tolerance
            )
            current_same_band = sum(
                bonds for price, bonds in valid_asks
                if floor - tolerance <= price <= ceiling + tolerance
            )
            if current_same_band > 1e-9:
                account.strict_trend_wall_peak_bonds = max(
                    account.strict_trend_wall_peak_bonds,
                    current_same_band,
                )
                account.strict_trend_wall_last_seen_ts_ms = tick.market_ts_ms
            compatible_buy = (
                tick.inferred_side == "buy"
                and tick.trade_bonds > 1e-9
                and previous_same_band > 1e-9
                and tick.last_price + tolerance + 1e-9 >= floor
            )
            if compatible_buy:
                account.strict_trend_wall_confirmed_attack_bonds += min(
                    tick.trade_bonds,
                    previous_same_band,
                    account.strict_trend_wall_peak_bonds,
                )
            required_attack = max(
                minimum_attack,
                account.strict_trend_wall_peak_bonds
                    * policy.strict_trend_minimum_wall_attack_ratio,
            )
            if (
                account.strict_trend_wall_confirmed_attack_bonds + 1e-9
                    >= required_attack
                and current_same_band <= maximum_remaining + 1e-9
            ):
                account.strict_trend_wall_confirmed_ts_ms = tick.market_ts_ms
            price_migrated_above_wall = (
                current_same_band <= 1e-9
                and tick.bid1 > ceiling
                    + policy.strict_trend_migration_minimum_bid_clearance
                    - 1e-9
                and account.strict_trend_wall_confirmed_attack_bonds + 1e-9
                    >= (
                        policy.strict_trend_migration_minimum_attack_multiple
                        * risk_block
                    )
            )
            if price_migrated_above_wall:
                account.strict_trend_wall_confirmed_ts_ms = tick.market_ts_ms

            wall_recent = (
                tick.market_ts_ms
                - account.strict_trend_wall_last_seen_ts_ms <= window_ms
            )
            attack_recent = (
                account.strict_trend_wall_confirmed_ts_ms > 0
                and tick.market_ts_ms
                    - account.strict_trend_wall_confirmed_ts_ms <= window_ms
            )
            if not wall_recent and not attack_recent:
                account.strict_trend_wall_floor_price = 0.0
                account.strict_trend_wall_ceiling_price = 0.0
                account.strict_trend_wall_peak_bonds = 0.0
                account.strict_trend_wall_confirmed_attack_bonds = 0.0
                account.strict_trend_wall_last_seen_ts_ms = 0
                account.strict_trend_wall_confirmed_ts_ms = 0
                active = False
            elif current_near_supply <= maximum_remaining + 1e-9:
                return attack_recent

        if (
            current_near_supply > minimum_tracked_wall + 1e-9
            and (active or severe_disconnected_top_bid)
        ):
            same_active_band = (
                active
                and tick.ask1
                    <= account.strict_trend_wall_ceiling_price
                        + tolerance + 1e-9
                and current_near_asks[-1][0] + tolerance + 1e-9
                    >= account.strict_trend_wall_floor_price
            )
            if not same_active_band:
                account.strict_trend_wall_floor_price = current_near_asks[0][0]
                account.strict_trend_wall_ceiling_price = (
                    current_near_asks[-1][0]
                )
                account.strict_trend_wall_peak_bonds = current_near_supply
                account.strict_trend_wall_confirmed_attack_bonds = 0.0
                account.strict_trend_wall_confirmed_ts_ms = 0
            else:
                account.strict_trend_wall_peak_bonds = max(
                    account.strict_trend_wall_peak_bonds,
                    current_near_supply,
                )
            account.strict_trend_wall_last_seen_ts_ms = tick.market_ts_ms
            return False

        return True

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
            strategy_ids.append(self.windfall_strategy_id)
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
        self.last_market_assessment = None
        self.accounts = {}
        self.previous_close_reference = 0.0
        self.observed_market_trade = False
        self.last_market_trade_ts_ms = 0
        self.last_confirmed_rise_trade_ts_ms = 0
        self.last_confirmed_rise_price = 0.0
        self.opening_discovery_matured_models = set()
        self.last_exact_offer_clear_rise_trade_ts_ms = 0
        self.last_exact_offer_clear_rise_price = 0.0
        self.last_intraday_working_reference = 0.0
        self.last_intraday_working_reference_ts_ms = 0
        self.previous_intraday_working_reference = 0.0
        self.previous_intraday_working_reference_ts_ms = 0
        self.midday_continuity_references = {}
        self.intraday_continuity_references = {}
        self.intraday_working_references_by_model = {}
        self.last_visible_bid_wall_price = 0.0
        self.last_visible_bid_wall_bonds = 0.0
        self.last_visible_bid_wall_ts_ms = 0
        self.last_bid_wall_left_book_ts_ms = 0
        self.bid_wall_currently_visible = False
        self.visible_bid_wall_first_seen_ms = {}
        self.adjacent_bid_cushion_observations = {}
        self.joint_corridor_book_history = deque()
        self.last_legacy_reliable_reference = 0.0
        self.last_legacy_reliable_reference_ts_ms = 0
        self.legacy_breakout_support_price = 0.0
        self.legacy_breakout_support_ts_ms = 0
        self.legacy_ask_walls = {}
        self.strict_breakout_price = 0.0
        self.strict_breakout_ts_ms = 0
        self.strict_breakout_initial_tail_bonds = 0.0
        self.strict_breakout_cleared = False
        self.strict_breakout_failed = False
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
            policy = self.windfall_policy
            strategy_id = self.windfall_strategy_id
            quantity_bonds = (
                policy.windfall_order_quantity_bonds
                if policy.windfall_order_quantity_bonds is not None
                else paper.super_windfall_quantity_bonds
            )
            initial_credit_cny = (
                policy.windfall_initial_credit_cny
                if policy.windfall_initial_credit_cny is not None
                else paper.super_windfall_credit_cny
            )
            account = MakerAccount(
                market_date=market_date,
                bond_code=self.bond_code,
                strategy_id=strategy_id,
                fill_mode="windfall",
                policy=policy,
                initial_inventory=0.0,
                maximum_inventory=quantity_bonds,
                initial_cash=initial_credit_cny,
                cash=initial_credit_cny,
                inventory=0.0,
                additional_buying_capacity=quantity_bonds,
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
            and (
                not account.policy.require_strict_passive_order_timestamp
                or account.buy_order.created_ms < tick.market_ts_ms
            )
            and tick.last_price <= account.buy_order.limit_price + 1e-9
        )
        corrected_sell_order = (
            effective_side == "sell"
            and book_side == "buy"
            and any(
                tick.last_price + 1e-9 >= order.limit_price
                for order in account.sell_orders.values()
                if not account.policy.require_strict_passive_order_timestamp
                or order.created_ms < tick.market_ts_ms
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
                and (
                    not account.policy.require_strict_passive_order_timestamp
                    or order.created_ms < tick.market_ts_ms
                )
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
                [(lot_id, order) for lot_id, order in account.sell_orders.items()
                 if not account.policy.require_strict_passive_order_timestamp
                 or order.created_ms < tick.market_ts_ms],
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
            and not (
                account.policy.require_strict_passive_order_timestamp
                and tick.market_ts_ms <= account.buy_order.created_ms
            )
            and not (
                account.policy.quote_on_current_ordinary_recovery
                and account.buy_order.kind == "low_bid_reversion"
                and account.last_ordinary_risk_exit_ts_ms > 0
                and account.buy_order.created_ms == account.last_ordinary_risk_exit_ts_ms
                and tick.market_ts_ms <= account.buy_order.created_ms
            )
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
        if self._ordinary_risk_exit_needs_new_frame(account, tick):
            return
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
            and not account.policy.allow_neutral_inventory_sweep_tail
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

    def _update_strict_breakout_episode_from_book(
        self, tick: ReplayTick, opportunities: list[Opportunity],
    ) -> None:
        """Invalidate a swept-offer episode on its first real sell re-entry."""

        if not any(
            account.policy.enable_strict_breakout_episode
            for account in self._standard_accounts()
        ):
            return
        sweep = next(
            (
                opportunity for opportunity in opportunities
                if opportunity.kind == "sweep_tail"
            ),
            None,
        )
        if sweep is not None:
            self.strict_breakout_price = sweep.entry_price
            self.strict_breakout_ts_ms = tick.market_ts_ms
            self.strict_breakout_initial_tail_bonds = (
                sweep.tail_bonds or sweep.quantity_bonds
            )
            # The replayed book cannot incorporate the paper order's own
            # market impact.  Wait until a later causal snapshot actually
            # shows the band clear; an unchanged residual tail is therefore
            # neither confirmation nor failure.
            self.strict_breakout_cleared = False
            self.strict_breakout_failed = False
            return
        if (
            self.strict_breakout_price <= 0
            or self.strict_breakout_ts_ms <= 0
            or self.strict_breakout_failed
            or tick.market_ts_ms <= self.strict_breakout_ts_ms
        ):
            return
        policy = next(
            account.policy for account in self._standard_accounts()
            if account.policy.enable_strict_breakout_episode
        )
        band = policy.strict_breakout_offer_band
        same_band_bonds = sum(
            bonds for price, bonds in tick.asks
            if self.strict_breakout_price - band - 1e-9
                <= price
                <= self.strict_breakout_price + band + 1e-9
        )
        offer_back_at_or_below_breakout = (
            tick.ask1 > 0
            and tick.ask1
                <= self.strict_breakout_price + band + 1e-9
        )
        if self.strict_breakout_cleared:
            if offer_back_at_or_below_breakout:
                self.strict_breakout_failed = True
            return
        if (
            tick.ask1 > self.strict_breakout_price + band + 1e-9
            and same_band_bonds <= 1e-9
        ):
            self.strict_breakout_cleared = True
            return
        if same_band_bonds > (
            self.strict_breakout_initial_tail_bonds + 1e-9
        ):
            # Before a clean clear is observed, replenishing more than the
            # residual tail already disproves the claimed breakout.
            self.strict_breakout_failed = True

    def _isolated_top_bid_replenishment_decision(
        self, account: MakerAccount, tick: ReplayTick,
    ) -> IsolatedTopBidDecision | None:
        """Ignore a structurally isolated best bid for passive base recovery.

        The guard is deliberately joint rather than a small-bid veto.  The
        best bid must sit close to ask1, stand materially above bid2, have only
        limited displayed capacity, and face concentrated nearby ask supply.
        A genuine thick bid, a continuous bid ladder, or light sell pressure
        keeps the parent's ordinary dynamic replenishment quote unchanged.
        """

        policy = account.policy
        bids = [(price, bonds) for price, bonds in tick.bids if price > 0]
        asks = [(price, bonds) for price, bonds in tick.asks if price > 0]
        if not (
            policy.enable_isolated_top_bid_base_replenishment_guard
            and len(bids) >= 2
            and asks
            and tick.ask1 > tick.bid1 > 0
        ):
            return None
        top_price, top_bonds = bids[0]
        next_price, _ = bids[1]
        inside_spread = tick.ask1 - top_price
        bid_gap = top_price - next_price
        risk_block = self.parameters.order_quantity_bonds
        near_asks = [
            (price, bonds) for price, bonds in asks
            if price <= tick.ask1
                + policy.isolated_top_bid_near_ask_band + 1e-9
        ]
        near_ask_supply = sum(bonds for _, bonds in near_asks)
        pressure_scale = max(risk_block, top_bonds)
        if not (
            inside_spread <= (
                policy.isolated_top_bid_maximum_inside_spread + 1e-9
            )
            and bid_gap + 1e-9 >= (
                policy.isolated_top_bid_minimum_gap_to_next_bid
            )
            and top_bonds <= (
                risk_block
                * policy.isolated_top_bid_maximum_quantity_multiple + 1e-9
            )
            and near_ask_supply + 1e-9 >= (
                pressure_scale
                * policy.isolated_top_bid_minimum_ask_supply_multiple
            )
        ):
            return None
        reliable_bid_price = _floor_to_tick(
            next_price + self.parameters.price_tick,
            self.parameters.price_tick,
        )
        if reliable_bid_price >= top_price - 1e-9:
            return None
        return IsolatedTopBidDecision(
            isolated_price=top_price,
            isolated_bonds=top_bonds,
            reliable_bid_price=reliable_bid_price,
            near_ask_supply_bonds=near_ask_supply,
            near_ask_floor_price=near_asks[0][0],
            near_ask_ceiling_price=near_asks[-1][0],
        )

    def _active_isolated_top_bid_wall_attack_replenishment(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, *, persist: bool,
        received_ts_ns: int,
    ) -> bool:
        """Restore a guarded base short when its sell-wall cushion is hit.

        The isolated-bid guard deliberately waits below a suspicious best
        bid because nearby sell supply provides observation time.  That
        patience must end when real aggressive buying materially attacks the
        exact supply cluster observed when the guarded order was created.
        Quote cancellations never add attack volume, and old prints expire
        from a short rolling window instead of granting a permanent signal.

        A roughly half-consumed wall is sufficient on its own.  A smaller but
        already material attack can also trigger when the ask lifts, the
        market is rising, or several consecutive prints show acceleration.
        The active fill remains bounded by the existing near-flat base-short
        loss allowance; a market that has already moved farther is left to
        the parent's confirmed-rising and tail-sweep stop logic.
        """

        policy = account.policy
        order = account.buy_order
        deficit = account.customer_base_short_bonds
        if not (
            policy.enable_isolated_top_bid_base_replenishment_guard
            and account.fill_mode == "priority"
            and deficit > 1e-9
            and account.replenishment_quantity > 1e-9
            and order is not None
            and order.kind == "isolated_top_bid_guarded_base_replenish"
            and order.near_ask_supply_bonds > 1e-9
            and order.isolated_near_ask_floor_price > 0
            and order.isolated_near_ask_ceiling_price
                + 1e-9 >= order.isolated_near_ask_floor_price
        ):
            return False

        window_ms = (
            policy.isolated_top_bid_wall_attack_window_seconds * 1_000
        )
        while (
            order.isolated_ask_attack_events
            and tick.market_ts_ms
                - order.isolated_ask_attack_events[0][0] > window_ms
        ):
            order.isolated_ask_attack_events.popleft()

        if tick.trade_bonds > 1e-9 and tick.inferred_side == "sell":
            order.isolated_last_incompatible_sell_ts_ms = tick.market_ts_ms
        if tick.trade_bonds > 1e-9 and tick.inferred_side == "buy":
            tolerance = self.parameters.fair_price_tolerance
            previous_cluster_bonds = sum(
                bonds for price, bonds in account.last_asks
                if price + tolerance + 1e-9
                    >= order.isolated_near_ask_floor_price
                and price <= (
                    order.isolated_near_ask_ceiling_price
                    + tolerance + 1e-9
                )
            )
            traded_inside_cluster = (
                previous_cluster_bonds > 1e-9
                and tick.last_price + tolerance + 1e-9
                    >= order.isolated_near_ask_floor_price
                and tick.last_price <= (
                    order.isolated_near_ask_ceiling_price
                    + tolerance + 1e-9
                )
            )
            swept_beyond_cluster = (
                previous_cluster_bonds > 1e-9
                and tick.last_price > (
                    order.isolated_near_ask_ceiling_price
                    + tolerance + 1e-9
                )
                and tick.trade_bonds + 1e-9 >= previous_cluster_bonds
            )
            if traded_inside_cluster or swept_beyond_cluster:
                # The Level 1 trade delta can include volume beyond the old
                # cluster after a sweep.  Attribute no more than the supply
                # that was actually visible immediately before this frame.
                compatible_bonds = min(
                    tick.trade_bonds,
                    previous_cluster_bonds,
                    order.near_ask_supply_bonds,
                )
                if compatible_bonds > 1e-9:
                    order.isolated_ask_attack_events.append(
                        (tick.market_ts_ms, compatible_bonds),
                    )

        confirmed_attack = min(
            order.near_ask_supply_bonds,
            sum(bonds for _, bonds in order.isolated_ask_attack_events),
        )
        order.isolated_confirmed_ask_attack_bonds = confirmed_attack
        risk_block = min(
            self.parameters.order_quantity_bonds,
            account.replenishment_quantity,
            deficit,
        )
        minimum_attack = (
            risk_block
            * policy.isolated_top_bid_minimum_confirmed_attack_multiple
        )
        attack_ratio = confirmed_attack / order.near_ask_supply_bonds
        ask_lifted = (
            tick.ask1 > order.isolated_near_ask_ceiling_price + 1e-9
        )
        material_attack = (
            confirmed_attack + 1e-9 >= minimum_attack
            and attack_ratio + 1e-9
                >= policy.isolated_top_bid_material_attack_ratio
        )
        accelerated_attack = (
            confirmed_attack + 1e-9 >= max(
                minimum_attack,
                risk_block
                    * policy.isolated_top_bid_accelerated_attack_multiple,
            )
            and attack_ratio + 1e-9
                >= policy.isolated_top_bid_accelerated_attack_ratio
            and (
                ask_lifted
                or assessment.state == "rising"
                or sum(
                    1 for event_ts_ms, _
                    in order.isolated_ask_attack_events
                    if event_ts_ms
                        > order.isolated_last_incompatible_sell_ts_ms
                )
                    >= policy.isolated_top_bid_accelerated_attack_events
            )
        )
        if not (material_attack or accelerated_attack):
            return False

        average_sale_price = (
            account.replenishment_sale_value
            / account.replenishment_quantity
        )
        maximum_recovery_price = (
            average_sale_price
            + policy.dynamic_base_replenishment_maximum_loss
        )
        if not (
            tick.ask1 > tick.bid1 > 0
            and tick.ask1_bonds > 1e-9
            and tick.ask1 <= maximum_recovery_price + 1e-9
        ):
            return False
        capacity = max(0.0, account.maximum_inventory - account.inventory)
        affordable = self._affordable_buy_bonds(account, tick.ask1)
        quantity = min(
            risk_block,
            tick.ask1_bonds,
            capacity,
            affordable,
        )
        if quantity <= 1e-9:
            return False
        self._cancel_order(
            account, order, tick,
            "isolated_top_bid_sell_wall_materially_attacked",
            persist,
        )
        active_order = self._new_order(
            account, tick, side="buy",
            kind="isolated_top_bid_wall_attack_base_replenish",
            lot_id=None, price=tick.ask1, quantity=quantity,
            queue_ahead=0.0, target_price=None,
            price_boundary=maximum_recovery_price,
            persist=persist,
            isolated_top_bid_price=order.isolated_top_bid_price,
            isolated_top_bid_bonds=order.isolated_top_bid_bonds,
            reliable_replenishment_bid_price=(
                order.reliable_replenishment_bid_price
            ),
            near_ask_supply_bonds=order.near_ask_supply_bonds,
            isolated_near_ask_floor_price=(
                order.isolated_near_ask_floor_price
            ),
            isolated_near_ask_ceiling_price=(
                order.isolated_near_ask_ceiling_price
            ),
            isolated_confirmed_ask_attack_bonds=confirmed_attack,
        )
        self._fill_buy(
            account, tick, active_order, quantity, received_ts_ns,
            kind="inventory_replenish", target_price=None,
            persist=persist,
            reason="active_isolated_top_bid_sell_wall_attack_replenishment",
        )
        return True

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
                "isolated_top_bid_guarded_base_replenish",
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

    def _active_joint_corridor_base_short_stop(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, *, persist: bool,
        received_ts_ns: int,
    ) -> bool:
        """Restore a joint-corridor base sale when its safety thesis fails.

        The high offer was allowed only because current overhead supply and a
        visible low replenishment corridor existed together.  If either side
        disappears while the base can still be restored inside the inherited
        near-flat loss allowance, actively close the economic short instead
        of leaving a stale passive low bid behind.
        """

        policy = account.policy
        deficit = max(0.0, account.initial_inventory - account.inventory)
        joint_deficit = min(
            deficit,
            account.replenishment_quantity,
            account.joint_corridor_base_short_bonds,
        )
        if not (
            policy.enable_joint_causal_corridor_two_sided_quote
            and account.fill_mode == "priority"
            and joint_deficit > 1e-9
            and account.joint_corridor_base_short_sell_price > 0
            and account.joint_corridor_base_short_buy_price > 0
            and tick.ask1 > tick.bid1 > 0
            and tick.ask1_bonds > 1e-9
        ):
            return False

        average_sale_price = (
            account.replenishment_sale_value
            / account.replenishment_quantity
        )
        if tick.ask1 - average_sale_price > (
            self.parameters.maximum_near_flat_exit_loss + 1e-9
        ):
            # This narrow guard is designed to act before the loss expands.
            # Once price has already escaped the inherited boundary, preserve
            # the parent's confirmed-rise and sweep recovery controls rather
            # than silently authorizing an unbounded chase.
            return False

        buy_price = account.joint_corridor_base_short_buy_price
        primary_bonds = sum(
            bonds
            for price, bonds in tick.bids
            if buy_price
                - policy.joint_corridor_primary_support_distance - 1e-9
                <= price
                <= buy_price + 1e-9
        )
        exceptional_bonds = sum(
            bonds
            for price, bonds in tick.bids
            if buy_price
                - policy.joint_corridor_exceptional_support_distance - 1e-9
                <= price
                <= buy_price + 1e-9
        )
        order_bonds = self.parameters.order_quantity_bonds
        low_support_live = (
            primary_bonds + 1e-9 >= (
                policy.joint_corridor_primary_support_multiple * order_bonds
            )
            or exceptional_bonds + 1e-9 >= (
                policy.joint_corridor_exceptional_support_multiple
                * order_bonds
            )
        )
        if (
            not low_support_live
            and account.buy_order is not None
            and account.buy_order.kind == "joint_causal_corridor_entry"
        ):
            low_support_live = (
                self._joint_corridor_support_is_temporarily_obscured(
                    account,
                    account.buy_order,
                    tick,
                    required_bonds=max(
                        2.0 * account.buy_order.remaining,
                        0.50
                            * account.buy_order.protective_bid_entry_bonds,
                    ),
                )
            )
        sell_price = account.joint_corridor_base_short_sell_price
        current_high_supply = sum(
            bonds
            for price, bonds in tick.asks
            if abs(price - sell_price)
                <= policy.joint_corridor_ask_supply_band + 1e-9
        )
        minimum_high_supply = max(
            policy.joint_corridor_minimum_ask_supply_multiple * order_bonds,
            0.50 * account.joint_corridor_base_short_ask_supply_bonds,
        )
        high_supply_live = (
            current_high_supply + 1e-9 >= minimum_high_supply
        )
        current_sell_quote = _floor_to_tick(
            tick.ask1 - self.parameters.price_tick,
            self.parameters.price_tick,
        )
        upward_escape = (
            current_sell_quote - sell_price
                > policy.joint_corridor_maximum_quote_drift + 1e-9
        )
        context = self._decision_context(tick, policy)
        high_side_bid_bonds = sum(
            bonds
            for price, bonds in tick.bids
            if sell_price - policy.joint_corridor_high_trade_band - 1e-9
                <= price
                <= sell_price + 1e-9
        )
        capacity_confirmed_breakout = (
            context.breakout_support_strong
            and high_side_bid_bonds + 1e-9 >= (
                policy.joint_corridor_breakout_bid_multiple * order_bonds
            )
        )
        directional_invalidation = (
            assessment.state == "rising"
            or capacity_confirmed_breakout
            or self._confirmed_rise_is_recent(tick, policy)
        )
        high_side_still_relevant = (
            tick.ask1 + policy.joint_corridor_high_trade_band + 1e-9
                >= sell_price
        )
        thesis_invalid = (
            directional_invalidation
            or upward_escape
            or (
                high_side_still_relevant
                and (not high_supply_live or not low_support_live)
            )
        )
        if not thesis_invalid:
            return False

        capacity = max(0.0, account.maximum_inventory - account.inventory)
        affordable = self._affordable_buy_bonds(account, tick.ask1)
        quantity = min(
            joint_deficit,
            tick.ask1_bonds,
            capacity,
            affordable,
        )
        if quantity <= 1e-9:
            return False
        if account.buy_order is not None:
            self._cancel_order(
                account, account.buy_order, tick,
                "joint_causal_corridor_base_short_stop", persist,
            )
        stop = self._new_order(
            account, tick, side="buy",
            kind="joint_causal_corridor_base_short_stop",
            lot_id=None, price=tick.ask1, quantity=quantity,
            queue_ahead=0.0, target_price=None,
            price_boundary=(
                average_sale_price
                + self.parameters.maximum_near_flat_exit_loss
            ),
            persist=persist,
        )
        self._fill_buy(
            account, tick, stop, quantity, received_ts_ns,
            kind="inventory_replenish", target_price=None,
            persist=persist,
            reason="active_joint_causal_corridor_base_short_stop",
        )
        return True

    def _active_trend_base_short_replenishment(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, *, persist: bool,
        received_ts_ns: int,
    ) -> bool:
        """Restore a base short when upward price discovery invalidates waiting.

        The ordinary path waits until a deep passive bid fills.  First-position
        2.1 instead recognizes two causal escape routes: the bond's reliable
        bid cluster has reached the old sale level in a confirmed rise, or an
        extremely strong underlying is accompanied by a large aggressive bond
        buy consuming the old sale area.  Neither the stock move nor a lone
        quote can authorize a cross by itself.
        """

        policy = account.policy
        deficit = max(0.0, account.initial_inventory - account.inventory)
        if not (
            policy.enable_trend_price_discovery_base_replenishment
            and account.fill_mode == "priority"
            and deficit > 1e-9
            and account.replenishment_quantity > 1e-9
            and account.last_base_short_sale_ts_ms > 0
            and tick.ask1 > tick.bid1 > 0
            and tick.ask1_bonds + 1e-9
                >= self.parameters.order_quantity_bonds
        ):
            return False

        average_sale_price = (
            account.replenishment_sale_value / account.replenishment_quantity
        )
        if (
            tick.ask1 - average_sale_price
                > policy.trend_base_replenishment_maximum_loss + 1e-9
        ):
            return False

        nearby_bid_bonds = sum(
            bonds for price, bonds in tick.bids
            if tick.bid1 - price <= 0.10 + 1e-9
        )
        required_bid_bonds = (
            policy.trend_base_replenishment_minimum_bid_multiple
            * self.parameters.order_quantity_bonds
        )
        required_buy_bonds = (
            policy.trend_base_replenishment_minimum_buy_multiple
            * self.parameters.order_quantity_bonds
        )
        bond_escape = (
            assessment.reference_source == "trend_price_discovery"
            and assessment.state == "rising"
            and tick.bid1 + 1e-9 >= average_sale_price
            and nearby_bid_bonds + 1e-9 >= required_bid_bonds
            and assessment.recent_buy_bonds + 1e-9 >= required_buy_bonds
        )
        stock_accelerated_escape = (
            self.analyzer.stock_is_extremely_strong(
                policy.trend_stock_acceleration_minimum_return,
            )
            and tick.inferred_side == "buy"
            and tick.trade_bonds + 1e-9 >= required_buy_bonds
            and tick.last_price + self.parameters.fair_price_tolerance + 1e-9
                >= average_sale_price
            and nearby_bid_bonds + tick.bid1_bonds + 1e-9
                >= self.parameters.order_quantity_bonds
        )
        if not (bond_escape or stock_accelerated_escape):
            return False

        isolated_top_bid = self._isolated_top_bid_replenishment_decision(
            account, tick,
        )
        if isolated_top_bid is not None and not stock_accelerated_escape:
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
        reason = (
            "active_stock_accelerated_trend_base_replenishment"
            if stock_accelerated_escape
            else "active_bond_confirmed_trend_base_replenishment"
        )
        if account.buy_order is not None:
            self._cancel_order(
                account, account.buy_order, tick, reason, persist,
            )
        active_order = self._new_order(
            account, tick, side="buy", kind="inventory_replenish",
            lot_id=None, price=tick.ask1, quantity=quantity,
            queue_ahead=0.0, target_price=None,
            price_boundary=(
                average_sale_price
                + policy.trend_base_replenishment_maximum_loss
            ),
            persist=persist,
        )
        self._fill_buy(
            account, tick, active_order, quantity, received_ts_ns,
            kind="inventory_replenish", target_price=None,
            persist=persist, reason=reason,
        )
        return True

    def _active_profitable_offer_tail_base_replenishment(
        self, account: MakerAccount, tick: ReplayTick, *, persist: bool,
        received_ts_ns: int,
    ) -> bool:
        """Consume one profitable offer tail to restore a customer base.

        A temporary retreat in the bid or a state relabel must not erase a
        still-live episode in which real buyers walked through several rising
        offer prices.  The permission is deliberately capped at the existing
        customer-base deficit and therefore cannot create an extra long.
        """

        policy = account.policy
        deficit = account.customer_base_short_bonds
        if not (
            policy.enable_profitable_offer_tail_base_replenishment
            and account.fill_mode == "priority"
            and deficit > 1e-9
            and account.replenishment_quantity > 1e-9
            and account.last_base_short_sale_ts_ms > 0
            and tick.inferred_side == "buy"
            and tick.trade_bonds > 1e-9
            and tick.ask1 > tick.bid1 > 0
            and len(tick.asks) >= 2
            and tick.ask1_bonds + 1e-9 >= deficit
            and tick.ask1_bonds
                <= self.parameters.order_quantity_bonds + 1e-9
            and tick.asks[1][0] - tick.ask1 + 1e-9
                >= policy.profitable_offer_tail_minimum_next_ask_gap
            and tick.last_price + self.parameters.fair_price_tolerance + 1e-9
                >= tick.ask1
        ):
            return False

        average_sale_price = (
            account.replenishment_sale_value
            / account.replenishment_quantity
        )
        if (
            average_sale_price - tick.ask1 + 1e-9
                < policy.profitable_offer_tail_minimum_profit
        ):
            return False

        cutoff_ms = max(
            account.last_base_short_sale_ts_ms,
            tick.market_ts_ms
                - policy.profitable_offer_tail_window_seconds * 1_000,
        )
        recent_buys = tuple(
            event for event in self.analyzer.trade_evidence
            if event.market_ts_ms >= cutoff_ms
            and event.market_ts_ms <= tick.market_ts_ms
            and event.side == "buy"
            and event.bonds > 1e-9
        )
        required_buy_bonds = (
            policy.profitable_offer_tail_minimum_buy_multiple
            * self.parameters.order_quantity_bonds
        )
        if (
            len(recent_buys) < policy.profitable_offer_tail_minimum_buy_events
            or sum(event.bonds for event in recent_buys) + 1e-9
                < required_buy_bonds
        ):
            return False
        buy_prices = tuple(event.price for event in recent_buys)
        if (
            max(buy_prices) - min(buy_prices) + 1e-9
                < policy.profitable_offer_tail_minimum_price_span
            or max(buy_prices) - tick.last_price
                > self.parameters.price_tick
                    + self.parameters.fair_price_tolerance + 1e-9
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

        reason = "active_profitable_offer_tail_base_replenishment"
        if account.buy_order is not None:
            self._cancel_order(
                account, account.buy_order, tick, reason, persist,
            )
        active_order = self._new_order(
            account, tick, side="buy",
            kind="profitable_offer_tail_base_replenish",
            lot_id=None, price=tick.ask1, quantity=quantity,
            queue_ahead=0.0, target_price=None,
            price_boundary=(
                average_sale_price
                - policy.profitable_offer_tail_minimum_profit
            ),
            persist=persist,
        )
        self._fill_buy(
            account, tick, active_order, quantity, received_ts_ns,
            kind="inventory_replenish", target_price=None,
            persist=persist, reason=reason,
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
        if self._ordinary_risk_exit_needs_new_frame(account, tick):
            return
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
                account, tick, active_reference, active_reference_source,
            )
            if guarded_reference + 1e-9 < active_reference:
                active_reference = guarded_reference
                active_reference_source = "post_replenishment_local_reference"
        edge = active_reference - tick.ask1
        isolated_discount = None
        if (
            account.policy.enable_isolated_deep_discount_sweep
            and supported_collapse_reference is None
            and not inventory_turn_replenishment
        ):
            isolated_discount = self._isolated_deep_discount_decision(
                account, tick, active_reference, active_reference_source,
                account.policy,
            )
        if account.policy.enable_priority_v11_extensions:
            if account.policy.enable_isolated_deep_discount_sweep:
                active_entry_safe = (
                    supported_collapse_reference is not None
                    or inventory_turn_replenishment
                    or isolated_discount is not None
                )
            else:
                active_entry_safe = (
                    supported_collapse_reference is not None
                    or (
                        context.breakout_support_strong
                        and edge
                            + self.parameters.fair_price_tolerance + 1e-9
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
            and (
                active_reference_source != "persistent_inside_market"
                or isolated_discount is not None
            )
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
            not account.policy.ignore_legacy_bid_wall_entry_caps
            and
            assessment.iron_floor_price is not None
            and assessment.state != "rising"
            and not self._confirmed_rise_is_recent(tick, account.policy)
            and tick.ask1 - assessment.iron_floor_price + 1e-9
                > self.parameters.maximum_iron_floor_entry_premium
        ):
            return
        adjacent_isolated_cluster_continuation = (
            account.policy.enable_unpolluted_isolated_discount_reference
            and isolated_discount is not None
            and account.last_active_entry_price is not None
            and tick.ask1 + self.parameters.price_tick / 2
                < account.last_active_entry_price
            and account.last_active_entry_price - tick.ask1
                <= (
                    account.policy.isolated_discount_price_cluster_width
                    + 1e-9
                )
        )
        if (
            account.last_active_entry_price is not None
            and not adjacent_isolated_cluster_continuation
            and tick.ask1
                > account.last_active_entry_price
                    - self.parameters.minimum_distinct_active_improvement + 1e-9
        ):
            return
        capacity = max(0.0, account.maximum_inventory - account.inventory)
        if account.policy.enable_opening_extra_inventory_confirmation:
            confirmed_extra_capacity = (
                self._opening_confirmed_extra_inventory_capacity(
                    account, tick,
                )
            )
            capacity = min(capacity, confirmed_extra_capacity)
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
            else isolated_discount[1] - isolated_discount[0]
            if isolated_discount is not None
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

    def _opening_confirmed_extra_inventory_capacity(
        self, account: MakerAccount, tick: ReplayTick,
    ) -> float:
        """Cap a fragile-opening discount cross at the customer-base line.

        Restoring a sold base and buying a new extra lot have different
        purposes.  Before 09:40, the latter needs fresh real bond buying or a
        nearby layered cushion.  A falling stock or a continuing lower-offer
        sequence raises the requirement to both, so one descending supply
        episode cannot be consumed repeatedly as independent bad quotes.
        """

        policy = account.policy
        ordinary_capacity = max(
            0.0, account.maximum_inventory - account.inventory,
        )
        if tick.market_time >= policy.opening_extra_inventory_guard_end_time:
            return ordinary_capacity

        base_deficit = max(
            0.0, account.initial_inventory - account.inventory,
        )
        now_ms = tick.market_ts_ms
        cutoff_ms = now_ms - (
            policy.opening_extra_inventory_buy_window_seconds * 1_000
        )
        recent_buys = sum(
            item.bonds for item in self.analyzer.trade_evidence
            if item.market_ts_ms >= cutoff_ms and item.side == "buy"
        )
        recent_sells = sum(
            item.bonds for item in self.analyzer.trade_evidence
            if item.market_ts_ms >= cutoff_ms and item.side == "sell"
        )
        required_buys = (
            policy.opening_extra_inventory_minimum_buy_multiple
            * self.parameters.order_quantity_bonds
        )
        buy_confirmed = (
            recent_buys + 1e-9 >= required_buys
            and recent_buys + 1e-9 >= (
                recent_sells
                * policy.opening_extra_inventory_minimum_buy_imbalance_ratio
            )
        )

        nearby_bids = tuple(
            (price, bonds) for price, bonds in tick.bids
            if tick.ask1 - price
                <= policy.opening_extra_inventory_support_band + 1e-9
        )
        support_confirmed = (
            len(nearby_bids)
                >= policy.opening_extra_inventory_minimum_support_levels
            and sum(bonds for _, bonds in nearby_bids) + 1e-9 >= (
                policy.opening_extra_inventory_minimum_support_multiple
                * self.parameters.order_quantity_bonds
            )
            and nearby_bids[1][1] + 1e-9 >= (
                policy.opening_extra_inventory_minimum_secondary_bid_multiple
                * self.parameters.order_quantity_bonds
            )
        )
        compatible_current_buy = (
            tick.inferred_side == "buy"
            and tick.trade_bonds > 1e-9
            and tick.last_price
                + self.parameters.fair_price_tolerance + 1e-9 >= tick.ask1
        )
        descending_offer = (
            account.last_ask > tick.ask1 > 0
            and account.last_ask - tick.ask1 <= (
                policy.opening_extra_inventory_maximum_descending_ask_step
                + 1e-9
            )
            and not compatible_current_buy
        )
        stock_return = self.analyzer.stock_day_return()
        stock_falling = (
            stock_return is not None
            and stock_return <= (
                policy.opening_extra_inventory_stock_fall_return + 1e-12
            )
        )
        extra_confirmed = (
            buy_confirmed and support_confirmed
            if descending_offer or stock_falling
            else buy_confirmed or support_confirmed
        )
        if extra_confirmed:
            return ordinary_capacity
        return min(ordinary_capacity, base_deficit)

    def _isolated_deep_discount_decision(
        self, account: MakerAccount, tick: ReplayTick,
        dynamic_reference: float, dynamic_reference_source: str,
        policy: MakerPolicyProfile,
    ) -> tuple[float, float] | None:
        """Return ``(required_discount, conservative_reference)``.

        The active cross earns from one anomalous seller.  It is rejected when
        the surrounding ask ladder has repriced down with that seller, even if
        an old high reference would make the entire ladder look statistically
        cheap.
        """

        if not (tick.ask1 > tick.bid1 > 0 and tick.ask1_bonds > 0):
            return None
        recent_reference = self._recent_trade_reference_before_tick(
            tick,
            policy.isolated_discount_recent_trade_seconds,
        )
        if policy.enable_unpolluted_isolated_discount_reference:
            return self._unpolluted_isolated_deep_discount_decision(
                account, tick, dynamic_reference, dynamic_reference_source,
                recent_reference, policy,
            )
        if not (
            dynamic_reference > tick.ask1
            and recent_reference is not None
            and recent_reference > tick.ask1
        ):
            return None
        displayed_bonds = max(
            self.parameters.order_quantity_bonds,
            tick.ask1_bonds,
        )
        required_discount = self._deep_discount_for_displayed_bonds(
            displayed_bonds,
        )
        conservative_reference = min(dynamic_reference, recent_reference)
        if (
            dynamic_reference - tick.ask1 + 1e-9 < required_discount
            or recent_reference - tick.ask1 + 1e-9 < required_discount
        ):
            return None
        next_ask = tick.asks[1][0] if len(tick.asks) >= 2 else 0.0
        required_gap = max(
            policy.isolated_discount_minimum_ask_gap,
            0.50 * required_discount,
        )
        if (
            next_ask > 0
            and next_ask - tick.ask1 + 1e-9 < required_gap
        ):
            return None
        intervening_supply = sum(
            bonds for index, (price, bonds) in enumerate(tick.asks)
            if index > 0
            and price < (
                conservative_reference
                - self.parameters.fair_price_tolerance
            )
        )
        if intervening_supply > (
            policy.isolated_discount_maximum_intervening_supply_bonds + 1e-9
        ):
            return None
        return required_discount, conservative_reference

    def _unpolluted_isolated_deep_discount_decision(
        self, account: MakerAccount, tick: ReplayTick,
        dynamic_reference: float, dynamic_reference_source: str,
        recent_reference: float | None,
        policy: MakerPolicyProfile,
    ) -> tuple[float, float] | None:
        """Judge one low-offer cluster against independently visible context.

        A current midpoint that already contains the suspect offer is not an
        independent reference.  At least one pre-tick trade reference or a
        normal ask that survived from the prior causal snapshot must remain
        visible.  The current cluster quantity determines the safety margin,
        while execution still consumes only the displayed best-ask quantity.
        """

        valid_asks = [
            (price, bonds) for price, bonds in tick.asks
            if price > 0 and bonds > 0
        ]
        cluster = []
        cluster_ceiling = (
            tick.ask1 + policy.isolated_discount_price_cluster_width
        )
        for price, bonds in valid_asks:
            if price <= cluster_ceiling + 1e-9:
                cluster.append((price, bonds))
                continue
            break
        if not cluster or len(cluster) >= len(valid_asks):
            return None

        cluster_high = max(price for price, _ in cluster)
        if (
            policy.veto_isolated_discount_with_low_carried_reference
            and dynamic_reference_source in {
                "carried_intraday_reference",
                "midday_carried_intraday_reference",
            }
            and dynamic_reference <= cluster_high + 1e-9
        ):
            return None
        cluster_bonds = sum(bonds for _, bonds in cluster)
        displayed_bonds = max(
            self.parameters.order_quantity_bonds,
            cluster_bonds,
        )
        required_discount = self._deep_discount_for_displayed_bonds(
            displayed_bonds,
        )
        previous_best_ask = next(
            (
                price for price, bonds in account.last_asks
                if price > 0 and bonds > 0
            ),
            0.0,
        )
        adjacent_cluster_continuation = (
            account.last_active_entry_price is not None
            and tick.ask1 + self.parameters.price_tick / 2
                < account.last_active_entry_price
            and account.last_active_entry_price - tick.ask1
                <= policy.isolated_discount_price_cluster_width + 1e-9
        )
        minimum_pre_snapshot_drop = max(
            policy.isolated_discount_minimum_ask_gap,
            required_discount
                * policy.isolated_discount_minimum_pre_snapshot_drop_ratio,
        )
        if not (
            adjacent_cluster_continuation
            or (
                previous_best_ask > cluster_high
                and previous_best_ask - cluster_high + 1e-9
                    >= minimum_pre_snapshot_drop
            )
        ):
            return None
        next_normal_index = len(cluster)
        next_normal_ask = valid_asks[next_normal_index][0]
        required_gap = max(
            policy.isolated_discount_minimum_ask_gap,
            0.50 * required_discount,
        )
        if (
            next_normal_ask <= cluster_high
            or next_normal_ask - cluster_high + 1e-9 < required_gap
        ):
            return None

        protected_references: list[float] = []
        if recent_reference is not None:
            # A lower or equal real-trade median is contrary evidence, not a
            # reference that may be silently discarded in favour of a remote
            # ask.  This is what separates one mistaken offer from an opening
            # or intraday market that has genuinely traded down.
            if recent_reference <= cluster_high:
                return None
            protected_references.append(recent_reference)
        normal_ask_survived = any(
            previous_price > cluster_high
            and abs(previous_price - next_normal_ask)
                <= policy.isolated_discount_normal_ask_match_width + 1e-9
            for previous_price, previous_bonds in account.last_asks
            if previous_price > 0 and previous_bonds > 0
        )
        if normal_ask_survived:
            protected_references.append(next_normal_ask)
        if not protected_references:
            return None

        reference_candidates = list(protected_references)
        if dynamic_reference > cluster_high:
            reference_candidates.append(dynamic_reference)
        conservative_reference = min(reference_candidates)
        if (
            conservative_reference - tick.ask1
            + policy.isolated_discount_fair_value_tolerance + 1e-9
            < required_discount
        ):
            return None

        intervening_supply = sum(
            bonds
            for price, bonds in valid_asks[next_normal_index:]
            if price < (
                conservative_reference
                - policy.isolated_discount_fair_value_tolerance
            )
        )
        if intervening_supply > (
            policy.isolated_discount_maximum_intervening_supply_bonds + 1e-9
        ):
            return None
        return required_discount, conservative_reference

    @staticmethod
    def _deep_discount_for_displayed_bonds(displayed_bonds: float) -> float:
        """Continuous user-confirmed size-to-discount safety curve."""

        anchors = (
            (1_000.0, 0.50),
            (2_000.0, 0.50),
            (5_000.0, 1.00),
            (10_000.0, 1.50),
            (20_000.0, 2.00),
        )
        quantity = max(1_000.0, displayed_bonds)
        for (left_q, left_d), (right_q, right_d) in zip(
            anchors, anchors[1:],
        ):
            if quantity <= right_q + 1e-9:
                weight = (quantity - left_q) / (right_q - left_q)
                return left_d + weight * (right_d - left_d)
        # Larger institutional-sized offers need at least the 20,000-bond
        # margin and continue on the last confirmed slope conservatively.
        return anchors[-1][1] + (
            (quantity - anchors[-1][0]) / 20_000.0
        )

    def _recent_trade_reference_before_tick(
        self, tick: ReplayTick, window_seconds: int,
    ) -> float | None:
        cutoff = tick.market_ts_ms - window_seconds * 1_000
        events = [
            event for event in self.analyzer.trade_evidence
            if cutoff <= event.market_ts_ms < tick.market_ts_ms
            and event.bonds > 0
        ]
        if not events:
            return None
        threshold = sum(event.bonds for event in events) / 2
        cumulative = 0.0
        for event in sorted(events, key=lambda item: item.price):
            cumulative += event.bonds
            if cumulative + 1e-9 >= threshold:
                return event.price
        return events[-1].price

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
        self,
        account: MakerAccount,
        tick: ReplayTick,
        reference: float,
        reference_source: str = "current_midpoint",
        *,
        apply_opening_trade_constraint: bool = False,
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

        if (
            apply_opening_trade_constraint
            and account.inventory + 1e-9 >= account.initial_inventory
        ):
            opening_trade_cap = self._opening_trade_reference_cap(
                account.policy, tick, reference, reference_source,
            )
            if opening_trade_cap is not None:
                reference = min(reference, opening_trade_cap)
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

    def _observe_joint_corridor_book(self, tick: ReplayTick) -> None:
        """Keep a short causal depth history for the v1.46 corridor."""

        self.joint_corridor_book_history.append(tick)
        cutoff_ms = tick.market_ts_ms - 600_000
        while (
            len(self.joint_corridor_book_history) > 1
            and self.joint_corridor_book_history[1].market_ts_ms < cutoff_ms
        ):
            self.joint_corridor_book_history.popleft()

    def _joint_corridor_book_was_persistent(
        self,
        tick: ReplayTick,
        policy: MakerPolicyProfile,
        *,
        buy_price: float,
        sell_price: float,
        support_distance: float,
        minimum_support_bonds: float,
        minimum_ask_bonds: float,
    ) -> bool:
        """Require the two-sided depth thesis to predate the current frame."""

        qualifying: list[ReplayTick] = []
        for snapshot in reversed(self.joint_corridor_book_history):
            if snapshot.market_ts_ms > tick.market_ts_ms:
                continue
            support_bonds = sum(
                bonds
                for price, bonds in snapshot.bids
                if buy_price - support_distance - 1e-9
                    <= price
                    <= buy_price + 1e-9
            )
            ask_bonds = sum(
                bonds
                for price, bonds in snapshot.asks
                if abs(price - sell_price)
                    <= policy.joint_corridor_ask_supply_band + 1e-9
            )
            if (
                support_bonds + 1e-9 < minimum_support_bonds
                or ask_bonds + 1e-9 < minimum_ask_bonds
            ):
                break
            qualifying.append(snapshot)
        if len(qualifying) < policy.joint_corridor_minimum_book_observations:
            return False
        return (
            tick.market_ts_ms - qualifying[-1].market_ts_ms
                + 1e-9
            >= policy.joint_corridor_minimum_book_seconds * 1_000
        )

    def _joint_corridor_support_is_temporarily_obscured(
        self,
        account: MakerAccount,
        order: MakerOrder,
        tick: ReplayTick,
        *,
        required_bonds: float,
    ) -> bool:
        """Distinguish a new small top bid from actual support withdrawal.

        Level 1 exposes only five bid levels.  A new higher bid can therefore
        push the deepest member of the bound support band out of view even
        though no compatible sell traded into that band.  Retain the original
        intent only when the immediately preceding book still showed enough
        of the exact bound band and the newly appearing top capacity is small.
        This is a one-snapshot continuity allowance, not hidden-depth credit.
        """

        policy = account.policy
        if not (
            order.kind == "joint_causal_corridor_entry"
            and order.protective_bid_floor_price > 0
            and order.protective_bid_ceiling_price > 0
            and account.last_bid > 0
            and account.last_bids
            and tick.trade_bonds <= 1e-9
        ):
            return False
        previous_band_bonds = sum(
            bonds
            for price, bonds in account.last_bids
            if order.protective_bid_floor_price - 1e-9
                <= price
                <= order.protective_bid_ceiling_price + 1e-9
        )
        if previous_band_bonds + 1e-9 < required_bonds:
            return False
        new_top_bonds = sum(
            bonds
            for price, bonds in tick.bids
            if price > account.last_bid + 1e-9
        )
        return (
            new_top_bonds > 1e-9
            and new_top_bonds + 1e-9 < (
                policy.joint_corridor_breakout_bid_multiple
                * self.parameters.order_quantity_bonds
            )
        )

    def _joint_causal_corridor_two_sided_quote(
        self,
        account: MakerAccount,
        tick: ReplayTick,
        assessment: MarketAssessment,
        context: MakerDecisionContext,
        *,
        fixed_buy_price: float | None = None,
        fixed_sell_price: float | None = None,
        existing_order: MakerOrder | None = None,
    ) -> JointCausalCorridorDecision | None:
        """Jointly validate a low bid and a high customer-base offer.

        Trade direction is intentionally not used for the high-side proof.
        ``inferred_side`` is a local Level-1 estimate; real prints inside the
        current high cluster prove that the price was executable regardless of
        how that estimate labelled the aggressor.
        """

        policy = account.policy
        order_bonds = self.parameters.order_quantity_bonds
        neutral_inventory = (
            abs(account.inventory - account.initial_inventory) <= 1e-9
            and account.customer_base_short_bonds <= 1e-9
        )
        retaining_original_quote = (
            existing_order is not None
            and existing_order.kind == "joint_causal_corridor_entry"
            and account.inventory + existing_order.remaining
                <= account.maximum_inventory + 1e-9
            and (
                account.customer_base_short_bonds <= 1e-9
                or (
                    account.replenishment_quantity > 1e-9
                    and account.pending_repeated_turn_replenishment_price > 0
                    and abs(
                        account.pending_repeated_turn_replenishment_price
                            - existing_order.limit_price
                    ) <= 1e-9
                )
            )
        )
        if not (
            policy.enable_joint_causal_corridor_two_sided_quote
            and account.fill_mode == "priority"
            and (neutral_inventory or retaining_original_quote)
            and (
                retaining_original_quote
                or assessment.state
                    in {"stable", "possible_rise", "possible_fall"}
            )
            and (
                retaining_original_quote
                or context.reference_source != "previous_close"
            )
            and not self._confirmed_rise_is_recent(tick, policy)
            and tick.ask1 > tick.bid1 > 0
        ):
            return None

        current_buy_price = _floor_to_tick(
            tick.bid1 + self.parameters.price_tick,
            self.parameters.price_tick,
        )
        current_sell_price = _floor_to_tick(
            tick.ask1 - self.parameters.price_tick,
            self.parameters.price_tick,
        )
        buy_price = (
            fixed_buy_price
            if fixed_buy_price is not None else current_buy_price
        )
        sell_price = (
            fixed_sell_price
            if fixed_sell_price is not None else current_sell_price
        )
        if fixed_sell_price is not None and (
            abs(current_sell_price - fixed_sell_price)
                > policy.joint_corridor_maximum_quote_drift + 1e-9
        ):
            return None
        if not (0 < buy_price < sell_price and buy_price < tick.ask1):
            return None

        # ``breakout_support_strong`` is a broad analyzer label.  A small bid
        # briefly appearing inside an otherwise unchanged wide corridor must
        # not erase the original low quote or force an immediate base-short
        # stop.  Only current bid capacity genuinely pressing the reviewed
        # high cluster turns that label into a joint-corridor veto.
        high_side_bid_bonds = sum(
            bonds
            for price, bonds in tick.bids
            if bonds > 0
            and sell_price - policy.joint_corridor_high_trade_band - 1e-9
                <= price
                <= sell_price + 1e-9
        )
        if (
            context.breakout_support_strong
            and high_side_bid_bonds + 1e-9 >= (
                policy.joint_corridor_breakout_bid_multiple * order_bonds
            )
        ):
            return None

        required_buy_bonds = (
            existing_order.remaining
            if existing_order is not None else order_bonds
        )
        if not (
            account.maximum_inventory - account.inventory + 1e-9
                >= required_buy_bonds
            and self._affordable_buy_bonds(account, buy_price) + 1e-9
                >= required_buy_bonds
        ):
            return None

        def support_in(distance: float) -> list[tuple[float, float]]:
            return [
                (price, bonds)
                for price, bonds in tick.bids
                if bonds > 0
                and buy_price - distance - 1e-9
                    <= price
                    <= buy_price + 1e-9
            ]

        primary_support = support_in(
            policy.joint_corridor_primary_support_distance,
        )
        primary_bonds = sum(bonds for _, bonds in primary_support)
        exceptional_support = False
        selected_support = primary_support
        selected_support_distance = (
            policy.joint_corridor_primary_support_distance
        )
        minimum_support_bonds = (
            policy.joint_corridor_primary_support_multiple * order_bonds
        )
        if primary_bonds + 1e-9 < minimum_support_bonds:
            selected_support = support_in(
                policy.joint_corridor_exceptional_support_distance,
            )
            selected_support_distance = (
                policy.joint_corridor_exceptional_support_distance
            )
            minimum_support_bonds = (
                policy.joint_corridor_exceptional_support_multiple
                * order_bonds
            )
            exceptional_support = True
        support_bonds = sum(bonds for _, bonds in selected_support)
        support_temporarily_obscured = False
        if (
            not selected_support
            or support_bonds + 1e-9 < minimum_support_bonds
        ):
            if existing_order is None:
                return None
            obscured_requirement = max(
                2.0 * existing_order.remaining,
                0.50 * existing_order.protective_bid_entry_bonds,
            )
            support_temporarily_obscured = (
                self._joint_corridor_support_is_temporarily_obscured(
                    account,
                    existing_order,
                    tick,
                    required_bonds=obscured_requirement,
                )
            )
            if not support_temporarily_obscured:
                return None
            support_bonds = existing_order.protective_bid_entry_bonds

        floor_price = (
            existing_order.protective_bid_floor_price
            if support_temporarily_obscured and existing_order is not None
            else min(price for price, _ in selected_support)
        )
        ceiling_price = (
            existing_order.protective_bid_ceiling_price
            if support_temporarily_obscured and existing_order is not None
            else max(price for price, _ in selected_support)
        )
        corridor_edge = sell_price - buy_price
        emergency_loss = max(
            self.parameters.price_tick,
            buy_price - floor_price,
        )
        reward_risk = corridor_edge / emergency_loss
        if not (
            corridor_edge + 1e-9
                >= policy.joint_corridor_minimum_edge
            and reward_risk + 1e-9
                >= policy.joint_corridor_minimum_reward_risk
        ):
            return None

        ask_supply_bonds = sum(
            bonds
            for price, bonds in tick.asks
            if bonds > 0
            and abs(price - sell_price)
                <= policy.joint_corridor_ask_supply_band + 1e-9
        )
        if ask_supply_bonds + 1e-9 < (
            policy.joint_corridor_minimum_ask_supply_multiple * order_bonds
        ):
            return None

        high_cutoff_ms = (
            tick.market_ts_ms
            - policy.joint_corridor_high_trade_lookback_seconds * 1_000
        )
        high_trade_bonds = sum(
            event.bonds
            for event in self.analyzer.trade_evidence
            if high_cutoff_ms <= event.market_ts_ms <= tick.market_ts_ms
            and abs(event.price - sell_price)
                <= policy.joint_corridor_high_trade_band + 1e-9
        )
        if high_trade_bonds + 1e-9 < (
            policy.joint_corridor_minimum_high_trade_bonds
        ):
            return None
        if ask_supply_bonds + 1e-9 < (
            policy.joint_corridor_minimum_ask_to_high_trade_multiple
            * high_trade_bonds
        ):
            # High prints prove that the upper price was reachable, but they
            # can also describe a wall being consumed.  Require material
            # current supply left relative to all causally observed prints so
            # the same evidence cannot simultaneously authorize a high offer
            # and warn that its ceiling is already being eaten through.
            return None
        if (
            existing_order is None
            and not self._joint_corridor_book_was_persistent(
                tick,
                policy,
                buy_price=buy_price,
                sell_price=sell_price,
                support_distance=selected_support_distance,
                minimum_support_bonds=minimum_support_bonds,
                minimum_ask_bonds=(
                    policy.joint_corridor_minimum_ask_supply_multiple
                    * order_bonds
                ),
            )
        ):
            return None

        return JointCausalCorridorDecision(
            price=buy_price,
            sell_price=sell_price,
            floor_price=floor_price,
            ceiling_price=ceiling_price,
            entry_bonds=support_bonds,
            entry_edge=corridor_edge,
            high_trade_bonds=high_trade_bonds,
            ask_supply_bonds=ask_supply_bonds,
            emergency_loss=emergency_loss,
            reward_risk=reward_risk,
            exceptional_support=exceptional_support,
        )

    def _retain_joint_causal_corridor_quote(
        self,
        account: MakerAccount,
        order: MakerOrder,
        tick: ReplayTick,
        assessment: MarketAssessment,
        context: MakerDecisionContext,
    ) -> JointCausalCorridorDecision | None:
        """Keep the reviewed low quote through harmless top-bid flicker."""

        policy = account.policy
        if not (
            policy.enable_joint_causal_corridor_two_sided_quote
            and order.kind == "joint_causal_corridor_entry"
            and order.target_price is not None
            and order.protective_bid_floor_price > 0
            and order.protective_bid_ceiling_price > 0
            and order.protective_bid_entry_bonds > 0
            and tick.market_ts_ms - order.created_ms
                <= policy.joint_corridor_maximum_lifetime_seconds * 1_000
        ):
            return None
        current = self._joint_causal_corridor_two_sided_quote(
            account,
            tick,
            assessment,
            context,
            fixed_buy_price=order.limit_price,
            fixed_sell_price=order.target_price,
            existing_order=order,
        )
        if current is None:
            return None
        original_band_bonds = sum(
            bonds
            for price, bonds in tick.bids
            if order.protective_bid_floor_price - 1e-9
                <= price
                <= order.protective_bid_ceiling_price + 1e-9
        )
        if original_band_bonds + 1e-9 < max(
            2.0 * order.remaining,
            0.50 * order.protective_bid_entry_bonds,
        ):
            required_bonds = max(
                2.0 * order.remaining,
                0.50 * order.protective_bid_entry_bonds,
            )
            if not self._joint_corridor_support_is_temporarily_obscured(
                account,
                order,
                tick,
                required_bonds=required_bonds,
            ):
                return None
        return replace(
            current,
            floor_price=order.protective_bid_floor_price,
            ceiling_price=order.protective_bid_ceiling_price,
            entry_bonds=order.protective_bid_entry_bonds,
        )

    def _current_adjacent_bid_cushion(
        self, tick: ReplayTick, policy: MakerPolicyProfile,
    ) -> tuple[float, float, float] | None:
        """Return the contiguous near-best bid cluster behind our quote."""

        if not tick.bids or tick.bid1 <= 0:
            return None
        ceiling = tick.bid1
        levels: list[tuple[float, float]] = []
        for price, bonds in tick.bids:
            if (
                price <= 0
                or bonds <= 0
                or ceiling - price
                    > policy.adjacent_bid_cushion_maximum_span + 1e-9
            ):
                break
            levels.append((price, bonds))
        if not levels:
            return None
        return levels[-1][0], ceiling, sum(bonds for _, bonds in levels)

    def _observe_adjacent_bid_cushion(
        self, tick: ReplayTick, policy: MakerPolicyProfile,
    ) -> AdjacentBidCushionObservation | None:
        current = self._current_adjacent_bid_cushion(tick, policy)
        if current is None:
            self.adjacent_bid_cushion_observations.clear()
            return None
        floor_price, ceiling_price, bonds = current
        key = (round(floor_price, 6), round(ceiling_price, 6))
        for old_key in list(self.adjacent_bid_cushion_observations):
            if old_key != key:
                del self.adjacent_bid_cushion_observations[old_key]
        observation = self.adjacent_bid_cushion_observations.get(key)
        maximum_observation_gap_ms = max(
            35_000,
            (policy.adjacent_bid_cushion_minimum_seconds + 5) * 1_000,
        )
        if (
            observation is None
            or tick.market_ts_ms - observation.last_seen_ms
                > maximum_observation_gap_ms
        ):
            observation = AdjacentBidCushionObservation(
                floor_price=floor_price,
                ceiling_price=ceiling_price,
                first_seen_ms=tick.market_ts_ms,
                last_seen_ms=tick.market_ts_ms,
                observations=1,
                peak_bonds=bonds,
            )
            self.adjacent_bid_cushion_observations[key] = observation
            return observation
        if tick.market_ts_ms > observation.last_seen_ms:
            observation.last_seen_ms = tick.market_ts_ms
            observation.observations += 1
            observation.peak_bonds = max(observation.peak_bonds, bonds)
        return observation

    def _adjacent_bid_cushion_entry(
        self,
        account: MakerAccount,
        tick: ReplayTick,
        assessment: MarketAssessment,
        context: MakerDecisionContext,
    ) -> AdjacentBidCushionDecision | None:
        """Buy first when nearby executable depth pays for corridor risk.

        The rule deliberately has no standalone spread threshold.  A smaller
        whole-corridor edge needs proportionally more immediately adjacent
        exit capacity; a wider corridor can qualify with a smaller multiple.
        """

        policy = account.policy
        if not (
            policy.enable_adjacent_bid_cushion_entry
            and account.fill_mode == "priority"
            and account.customer_base_short_bonds <= 1e-9
            and account.inventory + 1e-9 >= account.initial_inventory
            and account.inventory + 1e-9 < account.maximum_inventory
            and assessment.state in {"stable", "possible_fall"}
            and context.reference_source != "previous_close"
            and tick.ask1 > tick.bid1 > 0
            and not self._confirmed_rise_is_recent(tick, policy)
        ):
            return None
        observation = self._observe_adjacent_bid_cushion(tick, policy)
        if observation is None:
            return None
        current = self._current_adjacent_bid_cushion(tick, policy)
        assert current is not None
        floor_price, ceiling_price, current_bonds = current
        if not (
            observation.observations
                >= policy.adjacent_bid_cushion_minimum_observations
            and tick.market_ts_ms - observation.first_seen_ms
                >= policy.adjacent_bid_cushion_minimum_seconds * 1_000
        ):
            return None

        candidate_price = _floor_to_tick(
            ceiling_price + self.parameters.price_tick,
            self.parameters.price_tick,
        )
        passive_exit_price = _floor_to_tick(
            tick.ask1 - self.parameters.price_tick,
            self.parameters.price_tick,
        )
        entry_edge = passive_exit_price - candidate_price
        order_bonds = self.parameters.order_quantity_bonds
        capacity_multiple = current_bonds / order_bonds
        # Displayed protection has diminishing marginal value: going from
        # two to nine times our risk block matters, while an already huge
        # quote should not make a few cents of corridor edge look boundlessly
        # attractive.  This remains a joint score rather than a standalone
        # spread gate.
        value_score = max(0.0, entry_edge) * math.log1p(capacity_multiple)
        if not (
            candidate_price < passive_exit_price
            and candidate_price
                <= assessment.reference_low
                    + self.parameters.fair_price_tolerance + 1e-9
            and capacity_multiple + 1e-9
                >= policy.adjacent_bid_cushion_minimum_capacity_multiple
            and value_score + 1e-9
                >= policy.adjacent_bid_cushion_minimum_value_score
        ):
            return None

        attack_cutoff_ms = (
            tick.market_ts_ms
            - policy.adjacent_bid_cushion_damage_window_seconds * 1_000
        )
        recent_attacking_sells = sum(
            event.bonds
            for event in self.analyzer.trade_evidence
            if attack_cutoff_ms <= event.market_ts_ms <= tick.market_ts_ms
            and event.side == "sell"
            and floor_price - self.parameters.fair_price_tolerance - 1e-9
                <= event.price
                <= ceiling_price + self.parameters.fair_price_tolerance + 1e-9
        )
        if recent_attacking_sells + 1e-9 >= order_bonds:
            return None
        return AdjacentBidCushionDecision(
            price=candidate_price,
            floor_price=floor_price,
            ceiling_price=ceiling_price,
            entry_bonds=current_bonds,
            entry_edge=entry_edge,
        )

    def _retain_adjacent_bid_cushion_entry(
        self,
        account: MakerAccount,
        order: MakerOrder,
        tick: ReplayTick,
        assessment: MarketAssessment,
    ) -> AdjacentBidCushionDecision | None:
        policy = account.policy
        if not (
            policy.enable_adjacent_bid_cushion_entry
            and order.kind == "adjacent_bid_cushion_entry"
            and order.protective_bid_floor_price > 0
            and order.protective_bid_ceiling_price > 0
            and order.protective_bid_entry_bonds > 0
            and assessment.state in {"stable", "possible_fall"}
            and tick.ask1 > order.limit_price
            and account.customer_base_short_bonds <= 1e-9
            and account.inventory + order.remaining
                <= account.maximum_inventory + 1e-9
            and tick.market_ts_ms - order.created_ms
                <= policy.adjacent_bid_cushion_maximum_lifetime_seconds * 1_000
        ):
            return None
        executable_bonds = sum(
            bonds for price, bonds in tick.bids
            if price + 1e-9 >= order.protective_bid_floor_price
        )
        current_protected_bonds = sum(
            bonds for price, bonds in tick.bids
            if order.protective_bid_floor_price - 1e-9
                <= price
                <= order.protective_bid_ceiling_price + 1e-9
        )
        passive_exit_price = _floor_to_tick(
            tick.ask1 - self.parameters.price_tick,
            self.parameters.price_tick,
        )
        current_edge = passive_exit_price - order.limit_price
        current_multiple = executable_bonds / max(order.remaining, 1.0)
        if not (
            executable_bonds + 1e-9 >= (
                policy.adjacent_bid_cushion_minimum_capacity_multiple
                * order.remaining
            )
            and current_edge > 0
            and current_edge * math.log1p(current_multiple) + 1e-9
                >= policy.adjacent_bid_cushion_minimum_value_score
            and current_protected_bonds + 1e-9
                >= 0.50 * order.protective_bid_entry_bonds
        ):
            return None
        return AdjacentBidCushionDecision(
            price=order.limit_price,
            floor_price=order.protective_bid_floor_price,
            ceiling_price=order.protective_bid_ceiling_price,
            entry_bonds=order.protective_bid_entry_bonds,
            entry_edge=current_edge,
        )

    @staticmethod
    def _active_sell_limit_from_book(
        tick: ReplayTick, quantity: float, *, skip_bonds: float = 0.0,
    ) -> tuple[float, float] | None:
        available = 0.0
        limit_price = 0.0
        unallocated_skip = max(0.0, skip_bonds)
        for price, bonds in tick.bids:
            if price <= 0 or bonds <= 0:
                continue
            skipped = min(bonds, unallocated_skip)
            unallocated_skip -= skipped
            remaining_level = bonds - skipped
            take = min(remaining_level, quantity - available)
            if take <= 1e-9:
                continue
            available += take
            limit_price = price
            if available + 1e-9 >= quantity:
                break
        if available <= 1e-9 or limit_price <= 0:
            return None
        return limit_price, available

    def _active_adjacent_bid_cushion_risk_exit(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, *, persist: bool,
        received_ts_ns: int,
    ) -> None:
        """Exit a wall-backed extra lot while its original cushion remains usable."""

        policy = account.policy
        if not (
            (
                policy.enable_adjacent_bid_cushion_entry
                or policy.enable_joint_causal_corridor_two_sided_quote
            )
            and account.fill_mode == "priority"
            and account.inventory > account.initial_inventory + 1e-9
        ):
            return
        consumed_exit_bonds = 0.0
        for lot in sorted(
            account.lots.values(), key=lambda item: (item.opened_ms, item.db_id),
            reverse=True,
        ):
            protected_lot_kind = (
                lot.kind == "adjacent_bid_cushion_entry"
                and policy.enable_adjacent_bid_cushion_entry
            ) or (
                lot.kind == "joint_causal_corridor_entry"
                and policy.enable_joint_causal_corridor_two_sided_quote
            )
            if not (
                protected_lot_kind
                and lot.remaining_quantity > 1e-9
                and lot.protective_bid_floor_price > 0
                and lot.protective_bid_entry_bonds > 0
                and tick.market_ts_ms > lot.opened_ms
            ):
                continue
            protected_bonds = sum(
                bonds for price, bonds in tick.bids
                if lot.protective_bid_floor_price - 1e-9
                    <= price
                    <= lot.protective_bid_ceiling_price + 1e-9
            )
            executable_bonds = sum(
                bonds for price, bonds in tick.bids
                if price + 1e-9 >= lot.protective_bid_floor_price
            )
            previous_bonds = (
                lot.protective_bid_last_bonds
                if lot.protective_bid_last_bonds > 0
                else lot.protective_bid_entry_bonds
            )
            newly_lost_bonds = max(0.0, previous_bonds - protected_bonds)
            compatible_attack = (
                tick.inferred_side == "sell"
                and tick.trade_bonds > 0
                and tick.last_price
                    <= lot.protective_bid_ceiling_price
                        + self.parameters.fair_price_tolerance + 1e-9
            )
            rapid_damage = (
                newly_lost_bonds + 1e-9 >= (
                    policy.adjacent_bid_cushion_rapid_damage_ratio
                    * lot.protective_bid_entry_bonds
                )
            )
            if compatible_attack or rapid_damage:
                lot.protective_bid_last_damage_ts_ms = tick.market_ts_ms
            recent_damage = (
                lot.protective_bid_last_damage_ts_ms > 0
                and tick.market_ts_ms - lot.protective_bid_last_damage_ts_ms
                    <= policy.adjacent_bid_cushion_damage_window_seconds * 1_000
            )
            lot.protective_bid_last_bonds = protected_bonds
            lot.protective_bid_last_ts_ms = tick.market_ts_ms

            remaining_ratio = (
                protected_bonds / lot.protective_bid_entry_bonds
            )
            # More whole-corridor profit permits more patience.  A smaller
            # edge raises the safe exit line continuously; no single 0.30
            # boundary changes the decision discontinuously.
            exit_ratio = max(
                0.20,
                min(0.50, 0.50 - lot.protective_bid_entry_edge),
            )
            if recent_damage:
                exit_ratio += 0.30
            if assessment.state == "possible_fall":
                exit_ratio += 0.10
            elif assessment.state == "falling":
                exit_ratio += 0.25
            lower_prices = [
                price for price, _ in tick.bids
                if price < lot.protective_bid_floor_price - 1e-9
            ]
            if (
                not lower_prices
                or lot.protective_bid_floor_price - max(lower_prices)
                    >= policy.adjacent_bid_cushion_backup_gap - 1e-9
            ):
                exit_ratio += 0.10
            exit_ratio = min(0.60, exit_ratio)
            hard_capacity_exit = executable_bonds + 1e-9 <= (
                policy.adjacent_bid_cushion_hard_exit_capacity_multiple
                * lot.remaining_quantity
            )
            if not (
                hard_capacity_exit
                or remaining_ratio <= exit_ratio + 1e-9
            ):
                continue

            book_exit = self._active_sell_limit_from_book(
                tick, lot.remaining_quantity,
                skip_bonds=consumed_exit_bonds,
            )
            if book_exit is None:
                continue
            exit_price, exit_quantity = book_exit
            if self._guarded_shared_rapid_gap_active_exit(
                account, lot, tick, exit_price=exit_price,
            ):
                # The protected normal ask is still visible.  Do not turn one
                # newly exposed distant bid into an immediate marketable loss;
                # the passive lot-specific exit below remains available and a
                # later whole-ladder repricing will naturally remove this guard.
                continue
            exit_quantity = min(exit_quantity, lot.remaining_quantity)
            if account.buy_order is not None:
                self._cancel_order(
                    account, account.buy_order, tick,
                    "adjacent_bid_cushion_risk_exit", persist,
                )
            existing = account.sell_orders.get(lot.db_id)
            if existing is not None:
                self._cancel_order(
                    account, existing, tick,
                    "adjacent_bid_cushion_risk_exit", persist,
                )
            risk_exit_kind = (
                "joint_causal_corridor_risk_exit"
                if lot.kind == "joint_causal_corridor_entry"
                else "adjacent_bid_cushion_risk_exit"
            )
            order = self._new_order(
                account, tick, side="sell",
                kind=risk_exit_kind,
                lot_id=lot.db_id, price=exit_price,
                quantity=exit_quantity, queue_ahead=0.0,
                target_price=exit_price, price_boundary=exit_price,
                persist=persist,
            )
            account.sell_orders[lot.db_id] = order
            self._fill_sell(
                account, tick, order, exit_quantity, received_ts_ns,
                persist=persist,
                reason=(
                    "active_joint_causal_corridor_risk_exit"
                    if lot.kind == "joint_causal_corridor_entry"
                    else "active_adjacent_bid_cushion_risk_exit"
                ),
            )
            consumed_exit_bonds += exit_quantity
            account.last_falling_profitable_exit_price = exit_price
            account.last_falling_profitable_exit_ts_ms = tick.market_ts_ms

    @staticmethod
    def _ordinary_risk_exit_needs_new_frame(
        account: MakerAccount, tick: ReplayTick,
    ) -> bool:
        """Do not reuse the risk-exit fill frame for a new extra position."""
        return (
            (account.policy.ordinary_risk_exit_uses_fresh_reentry
             or account.policy.enable_causal_ordinary_inventory_turnover)
            and account.fill_mode == "priority"
            and account.last_ordinary_risk_exit_ts_ms > 0
            and tick.market_ts_ms <= account.last_ordinary_risk_exit_ts_ms
            and account.customer_base_short_bonds <= 1e-9
        )

    def _ordinary_recovery_allows_passive_quote(
        self, account: MakerAccount, tick: ReplayTick,
    ) -> bool:
        """Current demand may authorize a future bid, never a same-frame fill."""
        return (
            account.policy.quote_on_current_ordinary_recovery
            and account.policy.enable_causal_ordinary_inventory_turnover
            and self._ordinary_risk_exit_needs_new_frame(account, tick)
            and account.pending_inventory_turn_quantity <= 1e-9
            and not account.ordinary_tape_pressure_active
            and account.ordinary_tape_recovery_ts_ms == tick.market_ts_ms
            and tick.inferred_side == "buy"
            and tick.trade_bonds + 1e-9 >= self.parameters.order_quantity_bonds
            and account.last_ask > 0
            and tick.last_price + self.parameters.price_tick + 1e-9 >= account.last_ask
            and (
                tick.bid1 + 1e-9 >= account.last_bid
                or (
                    account.policy.allow_horizontal_recovery_quote_with_bid_retreat
                    and tick.ask1 + 1e-9 >= account.last_ask
                    and abs(tick.last_price - tick.ask1)
                        <= self.parameters.fair_price_tolerance + 1e-9
                )
            )
            and (
                tick.ask1 > account.last_ask + self.parameters.price_tick * 0.5
                or abs(tick.last_price - tick.ask1)
                    <= self.parameters.fair_price_tolerance + 1e-9
            )
        )

    def _stalled_extra_inventory_near_flat_exit_ready(
        self, account: MakerAccount, lot: MakerLot, tick: ReplayTick,
        assessment: MarketAssessment,
    ) -> bool:
        """Return whether a failed high-side route should yield to turnover.

        Time held is deliberately absent.  The permission needs a full extra
        lot, persistent sell dominance, an offer ladder that has moved down,
        and a currently executable first-position exit no worse than the
        registered near-flat loss boundary.  Once such an order exists, a
        flat (but not rebounding) short-window ask change may keep it alive.
        """

        policy = account.policy
        if not (
            policy.enable_stalled_extra_inventory_near_flat_exit
            and account.fill_mode == "priority"
            and lot.entry_price is not None
            and lot.kind != "deep_discount_sweep"
            and lot.opened_ms < tick.market_ts_ms
            and account.inventory + 1e-9 >= account.maximum_inventory
            and account.extra_inventory_bonds > 1e-9
            and assessment.state in {"possible_fall", "falling"}
            and assessment.recent_sell_bonds + 1e-9 >= (
                self.parameters.order_quantity_bonds
                * policy.stalled_extra_exit_minimum_recent_sell_multiple
            )
            and assessment.recent_sell_bonds + 1e-9 >= (
                assessment.recent_buy_bonds
                * policy.stalled_extra_exit_minimum_imbalance_ratio
            )
            and tick.ask1 > tick.bid1 > 0
            and not self._confirmed_rise_is_recent(tick, policy)
        ):
            return False
        existing = account.sell_orders.get(lot.db_id)
        offer_is_walking_down = assessment.short_ask_change <= (
            -policy.stalled_extra_exit_minimum_short_ask_drop + 1e-9
        )
        retained_stalled_exit = (
            existing is not None
            and existing.kind == "stalled_extra_inventory_near_flat_exit"
            and assessment.short_ask_change <= 1e-9
        )
        if not (offer_is_walking_down or retained_stalled_exit):
            return False
        nearest_exit = _floor_to_tick(
            tick.ask1 - self.parameters.price_tick,
            self.parameters.price_tick,
        )
        return nearest_exit + policy.stalled_extra_exit_maximum_loss + 1e-9 >= (
            lot.entry_price
        )

    def _support_collapse_capacity_release_ready(
        self, account: MakerAccount, lot: MakerLot, tick: ReplayTick,
        assessment: MarketAssessment,
    ) -> bool:
        """Release one ordinary extra lot after its support migrates lower.

        The initial trigger is deliberately joint: the old nested support is
        gone, recent selling has walked both sides of the book down, and a
        materially lower bid corridor is large enough to be a plausible next
        T-making location.  After the trigger, keep following the offer until
        the lot fills or the original near-entry support is genuinely rebuilt;
        otherwise a transient narrowing of the low corridor would strand the
        same full-inventory risk again.
        """

        policy = account.policy
        if not (
            policy.enable_support_collapse_capacity_redeployment
            and account.fill_mode == "priority"
            and lot.kind == "low_bid_reversion"
            and lot.entry_price is not None
            and lot.opened_ms < tick.market_ts_ms
            and lot.remaining_quantity > 1e-9
            and account.inventory + 1e-9 >= account.maximum_inventory
            and account.extra_inventory_bonds > 1e-9
            and tick.ask1 > tick.bid1 > 0
        ):
            return False

        order_quantity = self.parameters.order_quantity_bonds
        inner_support = sum(
            quantity
            for price, quantity in tick.bids
            if price > 0
            and quantity > 0
            and price + 1e-9 >= (
                lot.entry_price - policy.support_collapse_inner_distance
            )
        )
        outer_support = sum(
            quantity
            for price, quantity in tick.bids
            if price > 0
            and quantity > 0
            and price + 1e-9 >= (
                lot.entry_price - policy.support_collapse_outer_distance
            )
        )
        original_support_has_collapsed = (
            inner_support + 1e-9 < (
                order_quantity
                * policy.support_collapse_inner_maximum_multiple
            )
            and outer_support + 1e-9 < (
                order_quantity
                * policy.support_collapse_outer_maximum_multiple
            )
        )
        if not original_support_has_collapsed:
            return False

        existing = account.sell_orders.get(lot.db_id)
        nearest_exit = _floor_to_tick(
            tick.ask1 - self.parameters.price_tick,
            self.parameters.price_tick,
        )
        if policy.enable_support_collapse_consistency_revision:
            # The release permission must remain economically and
            # directionally self-consistent on every downward reprice.  An
            # already-live order is not permission to sell through the loss
            # boundary or into an isolated offer that this same policy would
            # actively buy if the account had capacity.
            if nearest_exit + (
                policy.support_collapse_initial_maximum_loss
            ) + 1e-9 < lot.entry_price:
                return False
            if self._isolated_deep_discount_decision(
                account,
                tick,
                assessment.reference_price,
                assessment.reference_source,
                policy,
            ) is not None:
                return False
        if (
            existing is not None
            and existing.kind
                == "support_collapse_capacity_release_exit"
        ):
            return True

        new_support = sum(
            quantity
            for price, quantity in tick.bids
            if price > 0
            and quantity > 0
            and tick.bid1 - price
                <= policy.support_collapse_new_support_distance + 1e-9
        )
        return (
            assessment.state in {"possible_fall", "falling"}
            and assessment.recent_sell_bonds + 1e-9 >= (
                order_quantity
                * policy.support_collapse_minimum_recent_sell_multiple
            )
            and assessment.recent_sell_bonds + 1e-9 >= (
                assessment.recent_buy_bonds
                * policy.support_collapse_minimum_sell_imbalance_ratio
            )
            and assessment.short_ask_change <= (
                -policy.support_collapse_minimum_ask_drop + 1e-9
            )
            and lot.entry_price - tick.bid1 + 1e-9 >= (
                policy.support_collapse_minimum_bid_migration
            )
            and lot.entry_price - tick.ask1 + 1e-9 >= (
                policy.support_collapse_minimum_ask_drop
            )
            and tick.ask1 - tick.bid1 + 1e-9 >= (
                policy.support_collapse_minimum_inside_spread
            )
            and new_support + 1e-9 >= (
                order_quantity
                * policy.support_collapse_new_support_minimum_multiple
            )
            and nearest_exit
                + policy.support_collapse_initial_maximum_loss + 1e-9
                >= lot.entry_price
        )

    def _support_collapse_reentry_window_active(
        self, account: MakerAccount, tick: ReplayTick,
    ) -> bool:
        policy = account.policy
        return (
            policy.enable_support_collapse_capacity_redeployment
            and account.last_support_collapse_exit_price > 0
            and account.last_support_collapse_exit_ts_ms > 0
            and 0 <= (
                tick.market_ts_ms
                    - account.last_support_collapse_exit_ts_ms
            ) <= policy.support_collapse_reentry_cooldown_seconds * 1_000
        )

    def _support_collapse_high_attack_bonds(
        self, account: MakerAccount, tick: ReplayTick,
    ) -> float:
        policy = account.policy
        return sum(
            event.bonds
            for event in self.analyzer.trade_evidence
            if event.side == "buy"
            and event.market_ts_ms > account.last_support_collapse_exit_ts_ms
            and event.market_ts_ms <= tick.market_ts_ms
            and tick.market_ts_ms - event.market_ts_ms <= (
                policy.support_collapse_reentry_attack_window_seconds * 1_000
            )
            and event.price + 1e-9 >= (
                account.last_support_collapse_exit_price
                + policy
                    .support_collapse_reentry_attack_minimum_improvement
            )
        )

    def _support_collapse_reentry_restriction_active(
        self, account: MakerAccount, tick: ReplayTick,
    ) -> bool:
        """Keep the released capacity reserved for a lower re-entry.

        A real high-side attack ends the extra-lot re-entry price restriction
        and returns that entry decision to the immutable parent logic.  It
        does not authorize selling the customer's opening base.
        """

        policy = account.policy
        self._update_support_collapse_recovery_latches(account, tick)
        return (
            self._support_collapse_reentry_window_active(account, tick)
            and not (
                policy.enable_support_collapse_consistency_revision
                and account.support_collapse_extra_reentry_released
            )
            and self._support_collapse_high_attack_bonds(account, tick)
                + 1e-9 < (
                    self.parameters.order_quantity_bonds
                    * policy
                        .support_collapse_reentry_attack_minimum_multiple
                )
        )

    def _support_collapse_base_protection_active(
        self, account: MakerAccount, tick: ReplayTick,
    ) -> bool:
        """Keep the base while the lower-capacity turn is still unresolved.

        The first 2.6 candidate used the re-entry cooldown as a mechanical
        600-second base lock.  The consistency revision instead asks whether
        the released capacity still has an unfinished lower buy-back plan and
        whether real buying has recovered to the failed entry region.  That
        keeps the 2026-09-02 low-side plan intact without blocking the parent
        after the 2026-08-14 market genuinely traded back through entry.
        """

        policy = account.policy
        if policy.enable_support_collapse_consistency_revision:
            self._update_support_collapse_recovery_latches(account, tick)
            return (
                abs(account.inventory - account.initial_inventory) <= 1e-9
                and account.last_support_collapse_exit_price > 0
                and account.last_support_collapse_entry_price > 0
                and account.pending_inventory_turn_quantity > 1e-9
                and not account.support_collapse_base_short_released
            )

        return (
            abs(account.inventory - account.initial_inventory) <= 1e-9
            and self._support_collapse_reentry_window_active(account, tick)
        )

    def _update_support_collapse_recovery_latches(
        self, account: MakerAccount, tick: ReplayTick,
    ) -> None:
        """Latch genuine high-side recovery for the current release episode."""

        policy = account.policy
        if not (
            policy.enable_support_collapse_consistency_revision
            and account.last_support_collapse_exit_price > 0
            and account.last_support_collapse_exit_ts_ms > 0
        ):
            return

        minimum_attack_bonds = (
            self.parameters.order_quantity_bonds
            * policy.support_collapse_reentry_attack_minimum_multiple
        )
        if (
            not account.support_collapse_extra_reentry_released
            and self._support_collapse_high_attack_bonds(account, tick)
                + 1e-9 >= minimum_attack_bonds
        ):
            # Once real buyers attack the high side, the parent entry logic
            # stays in control for the rest of this episode.  A 60-second
            # rolling window must not later re-apply the lower-price cap.
            account.support_collapse_extra_reentry_released = True

        if (
            account.support_collapse_base_short_released
            or account.last_support_collapse_entry_price <= 0
        ):
            return
        recovered_entry_bonds = sum(
            event.bonds
            for event in self.analyzer.trade_evidence
            if event.side == "buy"
            and event.market_ts_ms > account.last_support_collapse_exit_ts_ms
            and event.market_ts_ms <= tick.market_ts_ms
            and event.price + 1e-9
                >= account.last_support_collapse_entry_price
        )
        if recovered_entry_bonds + 1e-9 >= minimum_attack_bonds:
            account.support_collapse_base_short_released = True
            if policy.retire_recovered_support_collapse_pending_turn:
                self._retire_recovered_support_collapse_pending_turn(account)

    @staticmethod
    def _retire_recovered_support_collapse_pending_turn(
        account: MakerAccount,
    ) -> None:
        """Detach a recovered stop from later independent T opportunities."""

        quantity = min(
            account.pending_support_collapse_turn_quantity,
            account.pending_inventory_turn_quantity,
        )
        if quantity <= 1e-9:
            account.pending_support_collapse_turn_quantity = 0.0
            account.pending_support_collapse_turn_sale_value = 0.0
            return
        support_average_sale_price = (
            account.pending_support_collapse_turn_sale_value
            / account.pending_support_collapse_turn_quantity
            if account.pending_support_collapse_turn_sale_value > 0
            else account.pending_inventory_turn_sale_value
                / account.pending_inventory_turn_quantity
        )
        account.pending_inventory_turn_quantity = max(
            0.0, account.pending_inventory_turn_quantity - quantity,
        )
        account.pending_inventory_turn_sale_value = max(
            0.0,
            account.pending_inventory_turn_sale_value
                - quantity * support_average_sale_price,
        )
        account.pending_support_collapse_turn_quantity = max(
            0.0,
            account.pending_support_collapse_turn_quantity - quantity,
        )
        account.pending_support_collapse_turn_sale_value = max(
            0.0,
            account.pending_support_collapse_turn_sale_value
                - quantity * support_average_sale_price,
        )
        if account.pending_support_collapse_turn_quantity <= 1e-9:
            account.pending_support_collapse_turn_quantity = 0.0
            account.pending_support_collapse_turn_sale_value = 0.0
        if account.pending_inventory_turn_quantity <= 1e-9:
            account.pending_inventory_turn_quantity = 0.0
            account.pending_inventory_turn_sale_value = 0.0

    def _support_collapse_capacity_redeploy_price(
        self, account: MakerAccount, tick: ReplayTick,
    ) -> tuple[float, float] | None:
        """Return the visible lower bid and the episode's re-entry ceiling."""

        policy = account.policy
        if not (
            self._support_collapse_reentry_restriction_active(account, tick)
            and abs(account.inventory - account.initial_inventory) <= 1e-9
            and account.extra_inventory_bonds <= 1e-9
            and tick.ask1 > tick.bid1 > 0
        ):
            return None

        reentry_ceiling = _floor_to_tick(
            account.last_support_collapse_exit_price
                - policy.support_collapse_reentry_minimum_improvement,
            self.parameters.price_tick,
        )
        if tick.bid1 > reentry_ceiling + 1e-9:
            return None
        new_support = sum(
            quantity
            for price, quantity in tick.bids
            if price > 0
            and quantity > 0
            and tick.bid1 - price
                <= policy.support_collapse_new_support_distance + 1e-9
        )
        if not (
            new_support + 1e-9 >= (
                self.parameters.order_quantity_bonds
                * policy.support_collapse_new_support_minimum_multiple
            )
            and tick.ask1 - tick.bid1 + 1e-9 >= (
                policy.support_collapse_minimum_inside_spread
            )
        ):
            return None
        return tick.bid1, reentry_ceiling

    def _discounted_ordinary_lot_has_normal_offer(
        self, account: MakerAccount, lot: MakerLot, tick: ReplayTick,
        assessment: MarketAssessment,
    ) -> bool:
        """A thin bid alone does not invalidate a valuable ordinary low fill.

        Evaluate the current value and the still-visible normal offer together.
        This is neither a post-fill cooldown nor permission to ignore genuine
        selling/repricing. Only the isolated fragile-bid cause calls this guard.
        All scales reuse existing research parameters, not a fitted price/time.
        """
        policy, parameters = account.policy, self.parameters
        if not (
            policy.protect_discounted_ordinary_lot_from_fragile_bid_exit
            and account.initial_inventory > 0
            and lot.kind == "low_bid_reversion"
            and lot.entry_price is not None
            and tick.ask1 > tick.bid1 > 0
            and account.last_ask > 0
            and account.last_market_ts_ms > 0
            and 0 <= tick.market_ts_ms - account.last_market_ts_ms
                <= parameters.book_reference_window_seconds * 1_000
        ):
            return False
        band = parameters.price_cluster_width
        # Do not infer a normal high side from a lone new offer or from a
        # current offer that is migrating below the previously visible ladder.
        previous_supply = sum(q for p, q in account.last_asks
                              if account.last_ask <= p <= account.last_ask + band + 1e-9)
        current_supply = sum(q for p, q in tick.asks
                             if tick.ask1 <= p <= tick.ask1 + band + 1e-9)
        if (
            tick.ask1 < account.last_ask - band - 1e-9
            or min(previous_supply, current_supply) + 1e-9
                < parameters.order_quantity_bonds
        ):
            return False
        basis = max(lot.entry_price, tick.bid1)
        high_exit = tick.ask1 - parameters.price_tick
        if high_exit - basis + 1e-9 < parameters.minimum_active_entry_edge:
            return False
        # A large actual hit or sustained low-side selling is independent
        # evidence; a wide visible offer is not a blanket hold instruction.
        if (tick.inferred_side == "sell" and tick.trade_bonds + 1e-9 >=
                parameters.order_quantity_bonds
                * policy.full_inventory_active_exit_minimum_frame_sell_multiple):
            return False
        cutoff = tick.market_ts_ms - parameters.downside_risk_window_seconds * 1_000
        low_sells = sum(e.bonds for e in self.analyzer.trade_evidence
                        if cutoff <= e.market_ts_ms <= tick.market_ts_ms
                        and e.side == "sell" and e.price <= basis + band + 1e-9)
        if (low_sells + 1e-9 >= parameters.order_quantity_bonds
                * policy.full_inventory_active_exit_minimum_recent_sell_multiple
                and low_sells + 1e-9 >= assessment.recent_buy_bonds
                * parameters.downside_sell_imbalance_ratio):
            return False
        if (assessment.short_ask_change <= -parameters.minimum_short_ask_drop + 1e-9
                and assessment.recent_sell_bonds + 1e-9 >= parameters.order_quantity_bonds * 2
                and assessment.recent_sell_bonds + 1e-9 >= assessment.recent_buy_bonds
                * parameters.downside_sell_imbalance_ratio):
            return False
        context = self._decision_context(tick, policy)
        # Use this model's current continuity reference, never resurrect a
        # stale close after intraday price discovery. Midpoint-only resets
        # cannot independently prove that a very wide offer is good value.
        if context.reference_source in {
            "current_midpoint", "intraday_current_midpoint_reset",
            "midday_current_midpoint_reset",
        }:
            return False
        if (context.reference_source == "previous_close"
                and tick.market_time >= policy.opening_discovery_nominal_end_time):
            return False
        return context.reference_price - basis + 1e-9 >= parameters.minimum_active_entry_edge

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
        extra_inventory = max(
            0.0, account.inventory - account.initial_inventory,
        )
        nearest_passive_exit = (
            _floor_to_tick(
                tick.ask1 - parameters.price_tick,
                parameters.price_tick,
            )
            if tick.ask1 > tick.bid1 > 0 else 0.0
        )
        full_inventory_pressure = (
            policy.enable_full_inventory_capacity_release
            and account.inventory + 1e-9 >= account.maximum_inventory
            and extra_inventory > 1e-9
            and assessment.state in {"possible_fall", "falling"}
            and tick.bid1_bonds + 1e-9 >= max(
                extra_inventory,
                parameters.order_quantity_bonds
                    * policy.full_inventory_active_exit_minimum_bid_multiple,
            )
            and tick.inferred_side == "sell"
            and tick.trade_bonds + 1e-9 >= (
                parameters.order_quantity_bonds
                * policy
                    .full_inventory_active_exit_minimum_frame_sell_multiple
            )
            and assessment.recent_sell_bonds + 1e-9 >= (
                parameters.order_quantity_bonds
                * policy
                    .full_inventory_active_exit_minimum_recent_sell_multiple
            )
            and assessment.recent_sell_bonds + 1e-9 >= (
                assessment.recent_buy_bonds
                * policy
                    .full_inventory_active_exit_minimum_imbalance_ratio
            )
            and not self._confirmed_rise_is_recent(tick, policy)
        )
        if not (
            (
                bearish_vacuum
                or assessment.fragile_top_bid
                or confirmed_falling_pressure
                or full_inventory_pressure
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
                and (
                    not full_inventory_pressure
                    or lot.opened_ms < tick.market_ts_ms
                )
                and lot.entry_price - tick.bid1
                    <= (
                        policy.full_inventory_active_exit_maximum_loss
                        if full_inventory_pressure
                        else parameters.maximum_near_flat_exit_loss
                    ) + 1e-9
                and not (
                    full_inventory_pressure
                    and self._stalled_extra_inventory_near_flat_exit_ready(
                        account, lot, tick, assessment,
                    )
                )
                and not (
                    full_inventory_pressure
                    and nearest_passive_exit - lot.entry_price + 1e-9
                        >= policy
                            .full_inventory_passive_exit_minimum_edge
                )
                and not (
                    policy.protect_discounted_ordinary_lot_from_fragile_bid_exit
                    and assessment.fragile_top_bid
                    and not bearish_vacuum
                    and not confirmed_falling_pressure
                    and not full_inventory_pressure
                    and self._discounted_ordinary_lot_has_normal_offer(
                        account, lot, tick, assessment,
                    )
                )
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
                account, tick, side="sell", kind=(
                    "full_inventory_capacity_release_exit"
                    if full_inventory_pressure
                    else "inventory_risk_exit"
                ),
                lot_id=lot.db_id, price=tick.bid1, quantity=quantity,
                queue_ahead=0.0, target_price=tick.bid1,
                price_boundary=tick.bid1, persist=persist,
            )
            account.sell_orders[lot.db_id] = order
            self._fill_sell(
                account, tick, order, quantity, received_ts_ns,
                persist=persist,
                reason=(
                    "active_full_inventory_capacity_release"
                    if full_inventory_pressure
                    else "active_confirmed_falling_near_flat_exit"
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
            if not (
                policy.enable_causal_ordinary_inventory_turnover
                and lot.kind == "low_bid_reversion"
            ):
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
                and not self._isolated_low_offer_turnover_hold(
                    account, lot, tick,
                )
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

    def _isolated_low_offer_turnover_hold(
        self, account: MakerAccount, lot: MakerLot, tick: ReplayTick,
    ) -> bool:
        """Keep a normal-offer exit when only an urgent seller narrows spread.

        The ordinary fast-turnover rule treats a tight top spread and a clean
        profit at bid1 as enough reason to sell actively.  That inference is
        wrong when ask1 belongs to a small low-price sell cluster separated
        from the normal ask ladder: the tightness was created by an urgent
        seller, while the nearby multi-level bid cluster is only passive buy
        support.  If this lot already has a passive exit matching the next
        normal offer, preserve it for a later aggressive buyer.  Genuine
        downside exits remain independent and may still execute afterwards.
        """

        policy = account.policy
        if not (
            policy.enable_isolated_low_offer_active_turnover_hold
            and tick.ask1 > tick.bid1 > 0
            and tick.ask1 - tick.bid1
                <= self.parameters.maximum_active_turnover_spread + 1e-9
        ):
            return False
        existing = account.sell_orders.get(lot.db_id)
        if existing is None:
            return False

        asks = tuple(
            (price, quantity)
            for price, quantity in tick.asks
            if price > 0 and quantity > 0
        )
        if len(asks) < 2:
            return False
        low_offer = asks[0][0]
        low_cluster = tuple(
            (price, quantity)
            for price, quantity in asks
            if price <= (
                low_offer + policy.turnover_hold_offer_cluster_width + 1e-9
            )
        )
        next_normal_offer = next(
            (
                (price, quantity)
                for price, quantity in asks
                if price > (
                    low_offer
                    + policy.turnover_hold_offer_cluster_width
                    + 1e-9
                )
            ),
            None,
        )
        if next_normal_offer is None:
            return False
        cluster_high = max(price for price, _ in low_cluster)
        cluster_supply = sum(quantity for _, quantity in low_cluster)
        if not (
            next_normal_offer[0] - cluster_high + 1e-9
                >= policy.turnover_hold_minimum_gap_to_normal_offer
            and cluster_supply <= (
                self.parameters.order_quantity_bonds
                * policy.turnover_hold_maximum_offer_cluster_multiple
                + 1e-9
            )
            and abs(existing.limit_price - next_normal_offer[0])
                <= policy.turnover_hold_existing_exit_match_width + 1e-9
        ):
            return False

        bid_cluster = tuple(
            (price, quantity)
            for price, quantity in tick.bids
            if price > 0
            and quantity > 0
            and tick.bid1 - price
                <= policy.turnover_hold_bid_cluster_width + 1e-9
        )
        return (
            len(bid_cluster) >= 2
            and sum(quantity for _, quantity in bid_cluster) + 1e-9
                >= self.parameters.order_quantity_bonds
                * policy.turnover_hold_minimum_bid_cluster_multiple
        )

    def _live_priority_extra_inventory_exit_enabled_for_lot(
        self, account: MakerAccount, lot: MakerLot,
    ) -> bool:
        policy = account.policy
        if policy.enable_causal_ordinary_inventory_turnover:
            return lot.kind == "low_bid_reversion"
        if not (
            policy.enable_live_priority_extra_inventory_exit_exposure
            or policy
                .enable_guarded_live_priority_extra_inventory_exit_exposure
        ):
            return False
        if not policy.enable_guarded_live_priority_extra_inventory_exit_exposure:
            return True
        return lot.kind in policy.guarded_live_exit_ordinary_lot_kinds

    def _update_shared_reentry_recovery(
        self, account: MakerAccount, tick: ReplayTick,
    ) -> None:
        """V0.14 observes new demand, without changing v0.13's exit regime."""
        if not (
            account.policy.enable_shared_current_opportunity_reentry
            and account.fill_mode == "priority"
            and account.initial_inventory <= 1e-9
            and tick.ask1 > tick.bid1 > 0
            and 0 < account.last_shared_reentry_exit_ts_ms < tick.market_ts_ms
        ):
            return
        block = self.parameters.order_quantity_bonds
        width = account.policy.turnover_hold_offer_cluster_width
        if tick.inferred_side == "sell" and tick.trade_bonds + 1e-9 >= block:
            if (
                account.last_bid - tick.bid1 + 1e-9
                    >= account.policy.stalled_extra_exit_minimum_short_ask_drop
                or account.last_ask - tick.ask1 + 1e-9
                    >= account.policy.stalled_extra_exit_minimum_short_ask_drop
            ):
                account.shared_reentry_recovery_ts_ms = 0
        if (
            tick.inferred_side == "buy"
            and tick.trade_bonds + 1e-9 >= block
            and account.last_ask > 0
            and tick.last_price + self.parameters.price_tick + 1e-9
                >= min(account.last_ask, tick.ask1)
            and tick.ask1 + width + 1e-9 >= account.last_ask
        ):
            account.shared_reentry_recovery_ts_ms = tick.market_ts_ms
            account.shared_reentry_recovery_bid = tick.bid1
            account.shared_reentry_recovery_ask = tick.ask1

    def _shared_current_opportunity_reentry_allowed(
        self, account: MakerAccount, tick: ReplayTick,
    ) -> bool:
        """Waive only the matching stalled cap, not legality or allocation."""
        policy = account.policy
        recovery = account.shared_reentry_recovery_ts_ms
        if not (
            policy.enable_shared_current_opportunity_reentry
            and account.fill_mode == "priority"
            and account.initial_inventory <= 1e-9
            and account.inventory <= 1e-9
            and account.pending_inventory_turn_quantity <= 1e-9
            and 0 < account.last_shared_reentry_exit_ts_ms < recovery
            and account.last_shared_reentry_exit_ts_ms
                == account.last_stalled_extra_exit_ts_ms
            and 0 <= tick.market_ts_ms - recovery
                <= policy.guarded_live_exit_repricing_window_seconds * 1_000
            and tick.ask1 > tick.bid1 > 0
        ):
            return False
        width = policy.turnover_hold_offer_cluster_width
        support = sum(
            bonds for price, bonds in tick.bids
            if bonds > 0 and tick.bid1 >= price
            and tick.bid1 - price <= policy.session_resilient_near_support_distance + 1e-9
        )
        # Conservative visible space: reserve one tick on each side. No
        # retained reference or later high trade can impersonate an exit.
        return (
            tick.bid1 + width + 1e-9 >= account.shared_reentry_recovery_bid
            and tick.ask1 + width + 1e-9 >= account.shared_reentry_recovery_ask
            and support + 1e-9 >= self.parameters.order_quantity_bonds
            and tick.ask1 - tick.bid1 - 2 * self.parameters.price_tick + 1e-9
                >= self.parameters.minimum_entry_edge
        )

    def _shared_resilient_gap_price(
        self, account: MakerAccount, lot: MakerLot, tick: ReplayTick,
        assessment: MarketAssessment,
    ) -> tuple[float, str]:
        """Preserve an independently visible high ladder, never a cost anchor."""
        policy = account.policy
        width = policy.turnover_hold_offer_cluster_width
        asks = [(p, q) for p, q in tick.asks if p > 0 and q > 0]
        low = [(p, q) for p, q in asks if p <= tick.ask1 + width + 1e-9]
        normal = next((p for p, q in asks if p > tick.ask1 + width + 1e-9), None)
        block = self.parameters.order_quantity_bonds
        previous_support = [(p, q) for p, q in account.last_bids
                            if q > 0 and 0 <= account.last_bid - p <= width + 1e-9]
        support_consumed = (
            bool(previous_support)
            and sum(q for p, q in previous_support) + 1e-9 >= block
            and tick.inferred_side == "sell"
            and tick.trade_bonds + 1e-9 >= sum(q for p, q in previous_support)
            and min(p for p, q in previous_support) - self.parameters.price_tick - 1e-9
                <= tick.last_price <= account.last_bid + self.parameters.price_tick + 1e-9
            and account.last_bid - tick.bid1 + 1e-9
                >= policy.stalled_extra_exit_minimum_short_ask_drop
            and not any(q > 0 and p >= min(p0 for p0, q0 in previous_support) - 1e-9
                        for p, q in tick.bids)
        )
        cutoff = max(lot.opened_ms, tick.market_ts_ms
                     - policy.guarded_live_exit_repricing_window_seconds * 1_000)
        events = [e for e in self.analyzer.trade_evidence
                  if cutoff < e.market_ts_ms <= tick.market_ts_ms]
        sells = [e for e in events if e.side == "sell"]
        sell_bonds = sum(e.bonds for e in sells)
        broad_repricing = (
            len(sells) >= policy.guarded_live_exit_repricing_minimum_sell_events
            and sell_bonds + 1e-9 >= block * policy.stalled_extra_exit_minimum_recent_sell_multiple
            and sell_bonds + 1e-9 >= sum(e.bonds for e in events if e.side == "buy")
                * policy.stalled_extra_exit_minimum_imbalance_ratio
            and assessment.short_ask_change
                <= -policy.stalled_extra_exit_minimum_short_ask_drop + 1e-9
        )
        protected = (
            normal is not None and bool(low)
            and normal - max(p for p, q in low) + 1e-9
                >= policy.turnover_hold_minimum_gap_to_normal_offer
            and sum(q for p, q in low) <= 3 * block + 1e-9
            and any(q > 0 and abs(p - normal) <= width + 1e-9
                    for p, q in account.last_asks)
            and not support_consumed and not broad_repricing
        )
        offer = normal if protected else tick.ask1
        assert offer is not None
        price = _floor_to_tick(offer - self.parameters.price_tick * 0.5,
                               self.parameters.price_tick)
        if price <= tick.bid1 + 1e-9:
            price = _ceil_to_tick(tick.ask1, self.parameters.price_tick)
        return price, ("session_resilient_isolated_hold" if protected
                       else "session_resilient_gap_exit")

    def _update_guarded_live_exit_reentry_latch(
        self, account: MakerAccount, tick: ReplayTick,
    ) -> None:
        """Release only the current live-exit low-side plan on a real attack."""

        policy = account.policy
        if not (
            policy.guarded_live_exit_release_reentry_on_high_attack
            and account.last_guarded_live_exit_price > 0
            and account.last_guarded_live_exit_ts_ms > 0
            and not account.guarded_live_exit_reentry_released
            and account.last_stalled_extra_exit_ts_ms
                == account.last_guarded_live_exit_ts_ms
            and abs(
                account.last_stalled_extra_exit_price
                    - account.last_guarded_live_exit_price
            ) <= 1e-9
        ):
            return
        high_attack_bonds = sum(
            event.bonds
            for event in self.analyzer.trade_evidence
            if event.side == "buy"
            and event.market_ts_ms > account.last_guarded_live_exit_ts_ms
            and event.market_ts_ms <= tick.market_ts_ms
            and event.price + 1e-9
                >= account.last_guarded_live_exit_price
        )
        if high_attack_bonds + 1e-9 >= self.parameters.order_quantity_bonds:
            account.guarded_live_exit_reentry_released = True

    def _update_ordinary_tape_turnover_regime(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment,
    ) -> None:
        """Track a real supply shock until new demand invalidates it.

        This belongs to an actual ordinary T risk path, not to every first
        entry on a falling day. Quote silence does not demonstrate recovery;
        nor can pre-shock five-minute buying erase a fresh sell impulse.
        """
        policy = account.policy
        if not (policy.enable_ordinary_tape_turnover_regime
                or policy.enable_causal_ordinary_inventory_turnover):
            return
        key = (tick.market_ts_ms, tick.tick_id)
        if account.ordinary_tape_update_key == key:
            return
        account.ordinary_tape_update_key = key
        if not tick.ask1 > tick.bid1 > 0:
            return
        has_ordinary_risk = (
            account.last_ordinary_risk_exit_ts_ms > 0
            or (
                (policy.enable_ordinary_tape_horizontal_recovery
                 or policy.enable_causal_ordinary_inventory_turnover)
                and account.ordinary_tape_pressure_active
            )
            or any(
                lot.kind == "low_bid_reversion"
                and lot.entry_price is not None
                and lot.remaining_quantity > 1e-9
                for lot in account.lots.values()
            )
        )
        if not has_ordinary_risk:
            return
        quantity = self.parameters.order_quantity_bonds
        minimum_sell = (
            quantity * policy.stalled_extra_exit_minimum_recent_sell_multiple
        )
        sold = (
            tick.trade_bonds
            if tick.inferred_side == "sell" and account.last_bid > 0
            and tick.last_price <= (
                account.last_bid + self.parameters.fair_price_tolerance + 1e-9
            ) else 0.0
        )
        previous_support = sum(
            bonds for price, bonds in account.last_bids
            if account.last_bid - price <= self.parameters.price_cluster_width + 1e-9
        )
        remaining_support = sum(
            bonds for price, bonds in tick.bids
            if abs(price - account.last_bid)
                <= self.parameters.price_cluster_width + 1e-9
        )
        huge_support_consumed = (
            previous_support >= quantity * self.parameters.iron_floor_multiple
            and sold + 1e-9 >= previous_support * 0.5
            and previous_support - remaining_support + 1e-9
                >= previous_support * 0.5
        )
        fresh_shock = (
            sold + 1e-9 >= minimum_sell
            and tick.bid1 < account.last_bid - self.parameters.price_tick * 0.5
            and (
                account.last_ask - tick.ask1 + 1e-9
                    >= policy.stalled_extra_exit_minimum_short_ask_drop
                or account.last_bid - tick.bid1 + 1e-9
                    >= policy.stalled_extra_exit_minimum_short_ask_drop
                or huge_support_consumed
            )
        )
        # A recently restored, still-local support can fail completely with
        # fewer than five blocks. Attribute the whole loss to compatible real
        # selling, not cancellations, a partly consumed wall or an old recovery.
        recovered_support_consumed = (
            policy.recognize_consumed_recovered_support
            and account.extra_inventory_bonds > 1e-9
            and any(lot.kind == "low_bid_reversion"
                    and lot.entry_price is not None and lot.remaining_quantity > 1e-9
                    for lot in account.lots.values())
            and 0 < account.ordinary_tape_recovery_ts_ms
                <= account.last_market_ts_ms < tick.market_ts_ms
            and tick.market_ts_ms - account.ordinary_tape_recovery_ts_ms
                <= policy.ordinary_liquidity_corridor_window_seconds * 1_000
            and account.ordinary_tape_recovery_bid > 0
            and account.last_bid + self.parameters.price_cluster_width + 1e-9
                >= account.ordinary_tape_recovery_bid
            and previous_support + 1e-9 >= quantity
            and remaining_support <= 1e-9
            and sold + 1e-9 >= previous_support
            and abs(tick.last_price - account.last_bid)
                <= self.parameters.price_cluster_width + 1e-9
            and account.last_bid - tick.bid1 + 1e-9
                >= policy.stalled_extra_exit_minimum_short_ask_drop
            and account.last_ask - tick.ask1 + 1e-9
                >= policy.stalled_extra_exit_minimum_short_ask_drop
        )
        new_sells_since_recovery = sum(
            event.bonds for event in self.analyzer.trade_evidence
            if account.ordinary_tape_recovery_ts_ms < event.market_ts_ms
                <= tick.market_ts_ms
            and event.side == "sell"
        )
        gradual_pressure = (
            assessment.short_ask_change <= (
                -policy.stalled_extra_exit_minimum_short_ask_drop + 1e-9
            )
            and assessment.recent_sell_bonds + 1e-9 >= minimum_sell
            and assessment.recent_sell_bonds + 1e-9 >= (
                assessment.recent_buy_bonds
                * policy.stalled_extra_exit_minimum_imbalance_ratio
            )
            and (
                account.ordinary_tape_recovery_ts_ms == 0
                or new_sells_since_recovery + 1e-9 >= minimum_sell
            )
            and not self._confirmed_rise_is_recent(tick, policy)
        )
        demand_recovered = (
            tick.inferred_side == "buy"
            and tick.trade_bonds + 1e-9 >= quantity
            and account.last_ask > 0
            and tick.last_price + self.parameters.price_tick + 1e-9 >= account.last_ask
            and tick.ask1 > account.last_ask + self.parameters.price_tick * 0.5
            and tick.bid1 + 1e-9 >= account.last_bid
        )
        fresh_confirmed_rise = (
            self._confirmed_rise_is_recent(tick, policy)
            and max(
                self.last_confirmed_rise_trade_ts_ms,
                self.last_exact_offer_clear_rise_trade_ts_ms,
            ) > account.ordinary_tape_pressure_since_ms
        )
        horizontal_recovery_ts_ms = 0
        if (policy.enable_ordinary_tape_horizontal_recovery
                or policy.enable_causal_ordinary_inventory_turnover):
            # A traded high side need not move up: replenished offers can
            # support ordinary two-sided turnover without a trend breakout.
            # Only post-pressure demand near today's executable offer counts.
            cutoff = max(
                account.ordinary_tape_pressure_since_ms,
                account.ordinary_tape_recovery_ts_ms,
                tick.market_ts_ms
                - policy.ordinary_liquidity_corridor_window_seconds * 1_000,
            )
            high_buys = [
                event for event in self.analyzer.trade_evidence
                if cutoff < event.market_ts_ms <= tick.market_ts_ms
                and event.side == "buy"
                and abs(event.price - tick.ask1)
                    <= self.parameters.fair_price_tolerance + 1e-9
            ]
            if (
                tick.ask1 - tick.bid1 - 2 * self.parameters.price_tick + 1e-9
                    >= self.parameters.minimum_passive_turnover_edge
                and sum(event.bonds for event in high_buys) + 1e-9 >= quantity
            ):
                horizontal_recovery_ts_ms = max(e.market_ts_ms for e in high_buys)
            fresh_confirmed_rise = fresh_confirmed_rise and max(
                self.last_confirmed_rise_trade_ts_ms,
                self.last_exact_offer_clear_rise_trade_ts_ms,
            ) > account.ordinary_tape_recovery_ts_ms
        if fresh_shock or recovered_support_consumed:
            account.ordinary_tape_pressure_active = True
            account.ordinary_tape_pressure_since_ms = tick.market_ts_ms
        elif demand_recovered or fresh_confirmed_rise:
            account.ordinary_tape_pressure_active = False
            account.ordinary_tape_recovery_ts_ms = tick.market_ts_ms
            if policy.recognize_consumed_recovered_support:
                account.ordinary_tape_recovery_bid = tick.bid1
        elif horizontal_recovery_ts_ms:
            account.ordinary_tape_pressure_active = False
            account.ordinary_tape_recovery_ts_ms = horizontal_recovery_ts_ms
            if policy.recognize_consumed_recovered_support:
                account.ordinary_tape_recovery_bid = tick.bid1
        elif gradual_pressure:
            if not account.ordinary_tape_pressure_active:
                account.ordinary_tape_pressure_since_ms = tick.market_ts_ms
            account.ordinary_tape_pressure_active = True

    def _guarded_live_exit_sell_side_repricing_active(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment,
    ) -> bool:
        """Return whether the rebuilt fallback still sees a bad live tape.

        Older registered guarded profiles deliberately return ``True`` and
        retain their immutable order-gap behavior.  The v2.64 rebuild reuses
        the parent's existing sell-pressure scales but requires all of them
        now: a materially lower short-window ask, enough real selling, a
        sell/buy imbalance, and no still-live confirmed rise.  An order gap or
        a single low displayed offer is not deterioration by itself.
        """

        policy = account.policy
        if (policy.enable_ordinary_tape_turnover_regime
                or policy.enable_causal_ordinary_inventory_turnover):
            return account.ordinary_tape_pressure_active
        if not policy.guarded_live_exit_requires_current_sell_side_repricing:
            return True
        if self._confirmed_rise_is_recent(tick, policy):
            return False
        return (
            assessment.short_ask_change <= (
                -policy.stalled_extra_exit_minimum_short_ask_drop + 1e-9
            )
            and assessment.recent_sell_bonds + 1e-9 >= (
                self.parameters.order_quantity_bonds
                * policy.stalled_extra_exit_minimum_recent_sell_multiple
            )
            and assessment.recent_sell_bonds + 1e-9 >= (
                assessment.recent_buy_bonds
                * policy.stalled_extra_exit_minimum_imbalance_ratio
            )
        )

    def _ordinary_entry_sell_pressure_blocks(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, context: MakerDecisionContext,
        *, kind: str, price: float, quantity: float,
    ) -> bool:
        """Review a legal ordinary bid against currently shrinking exits."""
        if (account.policy.enable_ordinary_tape_turnover_regime
                or account.policy.enable_causal_ordinary_inventory_turnover):
            if (
                account.fill_mode != "priority"
                or kind != "low_bid_reversion"
                or quantity <= 1e-9
                or account.customer_base_short_bonds > 1e-9
                or account.pending_inventory_turn_quantity > 1e-9
            ):
                return False
            hypothetical_lot = MakerLot(
                db_id=-1, kind=kind, opened_ms=tick.market_ts_ms,
                entry_price=price, original_quantity=quantity,
                remaining_quantity=quantity,
            )
            protected_exit = self._live_priority_extra_inventory_isolated_offer_price(
                account, hypothetical_lot, tick, assessment, context,
            )
            nearest_exit = (
                protected_exit if protected_exit is not None
                else max(tick.bid1 + self.parameters.price_tick,
                         tick.ask1 - self.parameters.price_tick)
            )
            current_wide_corridor = (
                (account.policy.preserve_ordinary_tape_current_wide_corridor
                 or account.policy.enable_causal_ordinary_inventory_turnover)
                and tick.market_ts_ms > account.ordinary_tape_pressure_since_ms
                and nearest_exit - price + 1e-9 >= self.parameters.minimum_entry_edge
            )
            if (
                account.ordinary_tape_pressure_active and protected_exit is None
                and not current_wide_corridor
            ):
                return True
            return (
                context.reference_price > (
                    tick.ask1 + self.parameters.fair_price_tolerance + 1e-9
                )
                and nearest_exit - price + 1e-9
                    < self.parameters.minimum_passive_turnover_edge
            )
        mode = account.policy.ordinary_entry_sell_pressure_mode
        if mode == "reentry_repricing" and not (
            account.last_ordinary_risk_exit_ts_ms > 0
            and account.last_ordinary_risk_exit_ts_ms
                >= account.last_new_extra_entry_ts_ms
        ):
            return False
        if (
            mode == "off"
            or account.fill_mode != "priority"
            or kind != "low_bid_reversion"
            or quantity <= 1e-9
            or account.customer_base_short_bonds > 1e-9
            or account.pending_inventory_turn_quantity > 1e-9
            or not self._guarded_live_exit_sell_side_repricing_active(
                account, tick, assessment,
            )
        ):
            return False
        hypothetical_lot = MakerLot(
            db_id=-1, kind=kind, opened_ms=tick.market_ts_ms,
            entry_price=price, original_quantity=quantity,
            remaining_quantity=quantity,
        )
        if self._live_priority_extra_inventory_isolated_offer_price(
            account, hypothetical_lot, tick, assessment, context,
        ) is not None:
            return False
        if mode in {"all_repricing", "reentry_repricing"}:
            return True
        if mode == "edge_stress":
            stressed_edge = (
                tick.ask1 - self.parameters.price_tick - price
                - max(0.0, -assessment.short_ask_change)
            )
            return stressed_edge + 1e-9 < (
                self.parameters.minimum_passive_turnover_edge
            )
        raise ValueError(f"unsupported ordinary entry sell-pressure mode: {mode}")

    def _guarded_shared_rapid_gap_active_exit(
        self, account: MakerAccount, lot: MakerLot, tick: ReplayTick,
        *, exit_price: float,
    ) -> bool:
        """Keep a new protected lot out of one distant, dislocated bid.

        This is the user's explicit "it fell absurdly fast" exception.  It is
        deliberately not a timer-based holding rule: the short age, material
        loss, wide inside market, separated low offer and independently intact
        normal ask must all be present.  Once the ask ladder itself reprices,
        the normal active risk route is available again.
        """

        policy = account.policy
        if not (
            policy.enable_guarded_live_priority_extra_inventory_exit_exposure
            and policy.guarded_live_exit_protects_special_rapid_gap
            and lot.kind in {
                "adjacent_bid_cushion_entry",
                "joint_causal_corridor_entry",
            }
            and lot.entry_price is not None
            and 0 < tick.market_ts_ms - lot.opened_ms
                <= policy.guarded_live_exit_rapid_entry_seconds * 1_000
            and lot.entry_price - exit_price + 1e-9
                >= policy.guarded_live_exit_rapid_minimum_loss
            and tick.ask1 - tick.bid1 + 1e-9
                >= policy.guarded_live_exit_rapid_minimum_inside_spread
        ):
            return False

        asks = tuple(
            (price, quantity)
            for price, quantity in tick.asks
            if price > 0 and quantity > 0
        )
        if len(asks) < 2:
            return False
        low_offer = asks[0][0]
        low_cluster = tuple(
            (price, quantity)
            for price, quantity in asks
            if price <= (
                low_offer + policy.turnover_hold_offer_cluster_width + 1e-9
            )
        )
        cluster_high = max(price for price, _ in low_cluster)
        next_normal_offer = next(
            (
                price for price, quantity in asks
                if quantity > 0
                and price > (
                    low_offer
                    + policy.turnover_hold_offer_cluster_width
                    + 1e-9
                )
            ),
            None,
        )
        if (
            next_normal_offer is None
            or next_normal_offer - cluster_high + 1e-9
                < policy.turnover_hold_minimum_gap_to_normal_offer
        ):
            return False

        existing = account.sell_orders.get(lot.db_id)
        independent_normal_prices = [
            price for price, bonds in account.last_asks
            if price > 0 and bonds > 0
        ]
        if existing is not None:
            independent_normal_prices.append(existing.limit_price)
        if lot.target_price is not None and lot.target_price > 0:
            independent_normal_prices.append(lot.target_price)
        return any(
            abs(price - next_normal_offer)
                <= policy.turnover_hold_existing_exit_match_width + 1e-9
            for price in independent_normal_prices
        )

    def _live_priority_extra_inventory_isolated_offer_price(
        self, account: MakerAccount, lot: MakerLot, tick: ReplayTick,
        assessment: MarketAssessment, context: MakerDecisionContext,
    ) -> float | None:
        """Return the normal-ladder exit above one causal low-offer anomaly.

        V0.11 normally follows the live best offer down without a cost or
        reference veto.  This helper is deliberately the exceptional path:
        it protects a higher normal-ladder exit only when the current low
        cluster is small, separated and independently identifiable.  A broad
        sell sequence invalidates the structural shortcut.  The stricter
        existing deep-discount decision may still protect an offer that the
        model would itself actively buy.
        """

        policy = account.policy
        if not (
            self._live_priority_extra_inventory_exit_enabled_for_lot(
                account, lot,
            )
            and account.fill_mode == "priority"
            and lot.entry_price is not None
            and lot.remaining_quantity > 1e-9
            and tick.ask1 > tick.bid1 > 0
        ):
            return None

        asks = tuple(
            (price, quantity)
            for price, quantity in tick.asks
            if price > 0 and quantity > 0
        )
        if len(asks) < 2:
            return None
        low_offer = asks[0][0]
        low_cluster = tuple(
            (price, quantity)
            for price, quantity in asks
            if price <= (
                low_offer + policy.turnover_hold_offer_cluster_width + 1e-9
            )
        )
        next_normal_offer = next(
            (
                (price, quantity)
                for price, quantity in asks
                if price > (
                    low_offer
                    + policy.turnover_hold_offer_cluster_width
                    + 1e-9
                )
            ),
            None,
        )
        if next_normal_offer is None:
            return None
        cluster_high = max(price for price, _ in low_cluster)
        cluster_supply = sum(quantity for _, quantity in low_cluster)
        structurally_small_low_cluster = (
            next_normal_offer[0] - cluster_high + 1e-9
                >= policy.turnover_hold_minimum_gap_to_normal_offer
            and cluster_supply <= (
                self.parameters.order_quantity_bonds
                * policy.turnover_hold_maximum_offer_cluster_multiple
                + 1e-9
            )
        )

        existing = account.sell_orders.get(lot.db_id)
        existing_matches_normal = (
            existing is not None
            and abs(existing.limit_price - next_normal_offer[0])
                <= policy.turnover_hold_existing_exit_match_width + 1e-9
        )
        prior_book_matches_normal = any(
            previous_bonds > 0
            and abs(previous_price - next_normal_offer[0])
                <= policy.turnover_hold_existing_exit_match_width + 1e-9
            for previous_price, previous_bonds in account.last_asks
        )
        bid_cluster = tuple(
            (price, quantity)
            for price, quantity in tick.bids
            if price > 0
            and quantity > 0
            and tick.bid1 - price
                <= policy.turnover_hold_bid_cluster_width + 1e-9
        )
        if (policy.enable_guarded_live_priority_extra_inventory_exit_exposure
                or policy.enable_causal_ordinary_inventory_turnover):
            repricing_cutoff_ms = tick.market_ts_ms - (
                policy.guarded_live_exit_repricing_window_seconds * 1_000
            )
            repricing_events = tuple(
                event for event in self.analyzer.trade_evidence
                if event.market_ts_ms >= repricing_cutoff_ms
            )
            repricing_sell_events = tuple(
                event for event in repricing_events
                if event.side in {"sell", "unknown"}
            )
            repricing_sell_bonds = sum(
                event.bonds for event in repricing_sell_events
            )
            repricing_buy_bonds = sum(
                event.bonds for event in repricing_events
                if event.side in {"buy", "unknown"}
            )
            broad_sell_repricing = (
                assessment.state in {"possible_fall", "falling"}
                and len(repricing_sell_events)
                    >= policy.guarded_live_exit_repricing_minimum_sell_events
                and repricing_sell_bonds + 1e-9 >= (
                    self.parameters.order_quantity_bonds
                    * policy.stalled_extra_exit_minimum_recent_sell_multiple
                )
                and repricing_sell_bonds + 1e-9 >= (
                    repricing_buy_bonds
                    * policy.stalled_extra_exit_minimum_imbalance_ratio
                )
                and assessment.short_ask_change <= (
                    -policy.stalled_extra_exit_minimum_short_ask_drop + 1e-9
                )
            )
        else:
            broad_sell_repricing = (
                assessment.state in {"possible_fall", "falling"}
                and assessment.recent_sell_bonds + 1e-9 >= (
                    self.parameters.order_quantity_bonds
                    * policy.stalled_extra_exit_minimum_recent_sell_multiple
                )
                and assessment.recent_sell_bonds + 1e-9 >= (
                    assessment.recent_buy_bonds
                    * policy.stalled_extra_exit_minimum_imbalance_ratio
                )
                and assessment.short_ask_change <= (
                    -policy.stalled_extra_exit_minimum_short_ask_drop + 1e-9
                )
            )
        if (policy.enable_guarded_live_priority_extra_inventory_exit_exposure
                or policy.enable_causal_ordinary_inventory_turnover):
            # The user did not require a tight spread or a nearby passive bid
            # to recognise one small separated seller.  Those tests made the
            # first 0.11 follow obvious low-offer anomalies downward.  The
            # independent normal ask and absence of broad sell repricing are
            # the relevant causal distinction.
            structural_anomaly = (
                structurally_small_low_cluster
                and (existing_matches_normal or prior_book_matches_normal)
                and not broad_sell_repricing
            )
        else:
            structural_anomaly = (
                structurally_small_low_cluster
                and tick.ask1 - tick.bid1
                    <= self.parameters.maximum_active_turnover_spread + 1e-9
                and (existing_matches_normal or prior_book_matches_normal)
                and len(bid_cluster) >= 2
                and sum(quantity for _, quantity in bid_cluster) + 1e-9
                    >= self.parameters.order_quantity_bonds
                    * policy.turnover_hold_minimum_bid_cluster_multiple
                and not broad_sell_repricing
            )

        active_reference, active_reference_source = (
            self._active_entry_reference(context, tick, policy)
        )
        guarded_reference = self._ordinary_extra_entry_reference(
            account, tick, active_reference, active_reference_source,
        )
        if guarded_reference + 1e-9 < active_reference:
            active_reference = guarded_reference
            active_reference_source = "post_replenishment_local_reference"
        isolated_discount = None
        if policy.enable_isolated_deep_discount_sweep:
            isolated_discount = self._isolated_deep_discount_decision(
                account, tick, active_reference, active_reference_source,
                policy,
            )
        hypothetical_active_buy = (
            isolated_discount is not None
            and self.observed_market_trade
            and self._entry_window_for_policy(
                tick.market_time, policy, tick.market_date,
            )
            and context.reference_price > 0
            and self.parameters.opening_edge_is_safe(
                tick.market_date,
                tick.market_time,
                active_reference - tick.ask1,
            )
            and not (
                not policy.ignore_legacy_bid_wall_entry_caps
                and assessment.iron_floor_price is not None
                and assessment.state != "rising"
                and not self._confirmed_rise_is_recent(tick, policy)
                and tick.ask1 - assessment.iron_floor_price + 1e-9
                    > self.parameters.maximum_iron_floor_entry_premium
            )
        )
        if not (structural_anomaly or hypothetical_active_buy):
            return None
        if existing_matches_normal:
            assert existing is not None
            return existing.limit_price
        return _floor_to_tick(
            next_normal_offer[0] - self.parameters.price_tick * 0.5,
            self.parameters.price_tick,
        )

    def _refresh_super_windfall(
        self, account: MakerAccount, tick: ReplayTick,
        assessment: MarketAssessment, *, persist: bool,
    ) -> None:
        """Act on a large leaked offer or pre-position below a bid gap."""
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
        if (
            account.policy.enable_active_windfall_offer_sweep
            and not self._entry_window_for_policy(
                tick.market_time, account.policy, tick.market_date,
            )
        ):
            return
        recent_trade_reference = self.analyzer.recent_trade_reference(
            tick.market_ts_ms,
            self.parameters.windfall_recent_trade_window_seconds,
        )
        assessment_reference = assessment.reference_price
        if (
            account.policy.use_unpolluted_windfall_reference
            and assessment.reference_source in {
                "current_midpoint", "persistent_inside_market",
            }
        ):
            # A leaked offer or deep bid may pull a book-derived midpoint down
            # and then veto itself.  V2 uses only independently established
            # trade/anchor references while such a candidate is evaluated.
            assessment_reference = None
        elif (
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
        minimum_discount = (
            account.policy.windfall_minimum_discount
            if account.policy.windfall_minimum_discount is not None
            else self.parameters.minimum_windfall_discount
        )
        minimum_book_gap = (
            account.policy.windfall_minimum_book_gap
            if account.policy.windfall_minimum_book_gap is not None
            else self.parameters.minimum_windfall_book_gap
        )
        if self.parameters.opening_caution_is_active(
            tick.market_date, tick.market_time,
        ):
            minimum_discount = max(
                minimum_discount,
                self.parameters.opening_caution_minimum_edge,
            )
        order_quantity = (
            account.policy.windfall_order_quantity_bonds
            if account.policy.windfall_order_quantity_bonds is not None
            else self.config.maker_paper.super_windfall_quantity_bonds
        )

        if self._active_super_windfall_offer(
            account,
            tick,
            reference=reference,
            minimum_discount=minimum_discount,
            minimum_book_gap=minimum_book_gap,
            order_quantity=order_quantity,
            persist=persist,
        ):
            return

        candidate: tuple[float, float, float] | None = None
        for upper, lower in zip(tick.bids, tick.bids[1:]):
            book_gap = upper[0] - lower[0]
            discount = reference - lower[0]
            if (
                book_gap + 1e-9
                    >= minimum_book_gap
                and discount + 1e-9
                    >= minimum_discount
            ):
                candidate = (lower[0], lower[1], upper[0])
                break
        if candidate is None and tick.bids:
            top_gap = max(tick.last_price, tick.ask1) - tick.bid1
            if (
                top_gap + 1e-9
                    >= minimum_book_gap
                and reference - tick.bid1 + 1e-9
                    >= minimum_discount
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
            order_quantity,
            capacity,
            affordable,
        )
        if quantity + 1e-9 < order_quantity:
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
                reference - minimum_discount
            ),
            persist=persist,
        )

    def _active_super_windfall_offer(
        self,
        account: MakerAccount,
        tick: ReplayTick,
        *,
        reference: float,
        minimum_discount: float,
        minimum_book_gap: float,
        order_quantity: float,
        persist: bool,
    ) -> bool:
        """Buy one complete V2 risk block from an isolated leaked offer."""

        policy = account.policy
        if not (
            policy.enable_active_windfall_offer_sweep
            and len(tick.asks) >= 2
            and tick.ask1 > tick.bid1 > 0
            and tick.ask1_bonds + 1e-9
                >= max(
                    order_quantity,
                    policy.windfall_minimum_active_offer_bonds,
                )
            and tick.asks[1][0] - tick.ask1 + 1e-9
                >= minimum_book_gap
            and reference - tick.ask1 + 1e-9 >= minimum_discount
        ):
            return False
        capacity = max(0.0, account.maximum_inventory - account.inventory)
        affordable = self._affordable_buy_bonds(account, tick.ask1)
        quantity = min(
            order_quantity, tick.ask1_bonds, capacity, affordable,
        )
        if quantity + 1e-9 < order_quantity:
            return False
        if account.buy_order is not None:
            self._cancel_order(
                account,
                account.buy_order,
                tick,
                "active_super_windfall_replaced_preposition",
                persist,
            )
        order = self._new_order(
            account,
            tick,
            side="buy",
            kind="super_windfall_active",
            lot_id=None,
            price=tick.ask1,
            quantity=quantity,
            queue_ahead=0.0,
            target_price=None,
            price_boundary=reference - minimum_discount,
            persist=persist,
        )
        self._fill_buy(
            account,
            tick,
            order,
            quantity,
            tick.market_ts_ms * 1_000_000,
            kind="super_windfall_active",
            target_price=None,
            persist=persist,
            reason="active_super_windfall_buy",
        )
        return True

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
        if source != "previous_close" and reference > 0:
            self.intraday_working_references_by_model[
                policy.model_id
            ] = reference
        if tick is not None:
            continuity = self._reference_continuity(
                policy, tick, reference, source,
            )
            if continuity is not None:
                reference, source = continuity
        if (
            policy.retain_intraday_reference_in_quiet_wide_market
            and source == "previous_close"
            and tick is not None
            and tick.market_time >= policy.quiet_wide_market_earliest_time
            and self.observed_market_trade
            and self.last_market_trade_ts_ms > 0
            and tick.market_ts_ms - self.last_market_trade_ts_ms
                >= policy.quiet_wide_market_minimum_seconds * 1_000
            and self.last_intraday_working_reference > 0
            and tick.bid1 > 0
            and tick.ask1 > tick.bid1
            and tick.ask1 - tick.bid1 + 1e-9
                >= policy.quiet_wide_market_minimum_spread
            and tick.bid1 - self.parameters.fair_price_tolerance
                <= self.last_intraday_working_reference
                <= tick.ask1 + self.parameters.fair_price_tolerance
        ):
            reference = self.last_intraday_working_reference
            source = "retained_intraday_working_reference"
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
        if policy.enable_strict_breakout_episode:
            strict_episode_live = (
                breakout_support is not None
                and self.strict_breakout_cleared
                and not self.strict_breakout_failed
                and self.strict_breakout_price > 0
                and abs(
                    breakout_support - self.strict_breakout_price
                ) <= policy.strict_breakout_offer_band + 1e-9
            )
            if not strict_episode_live:
                breakout_support = None
                breakout_lower_sells = 0.0
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

    def _ordinary_nested_bid_support_is_safe(
        self,
        policy: MakerPolicyProfile,
        tick: ReplayTick,
        edge: float,
        candidate_price: float | None = None,
    ) -> bool:
        if (
            not policy.require_nested_ordinary_bid_support
            or edge + 1e-9 >= self.parameters.minimum_active_entry_edge
        ):
            return True
        outer_support = sum(
            quantity for price, quantity in tick.bids
            if price + 1e-9
                >= tick.bid1 - self.parameters.book_safety_distance
        )
        inner_support = sum(
            quantity for price, quantity in tick.bids
            if price + 1e-9
                >= tick.bid1 - policy.ordinary_inner_bid_support_distance
        )
        nested_safe = (
            outer_support + 1e-9 >= (
                self.parameters.large_wall_multiple
                * self.parameters.order_quantity_bonds
            )
            and inner_support + 1e-9 >= (
                policy.ordinary_inner_bid_support_multiple
                * self.parameters.order_quantity_bonds
            )
        )
        if nested_safe:
            return True
        if not (
            policy.enable_wide_reward_risk_nested_support_override
            and edge + self.parameters.fair_price_tolerance + 1e-9
                >= self.parameters.minimum_entry_edge
        ):
            return False

        entry_price = (
            candidate_price
            if candidate_price is not None
            else tick.bid1 + self.parameters.price_tick
        )
        if entry_price <= 0:
            return False
        recent_cutoff_ms = (
            tick.market_ts_ms
            - policy.wide_reward_risk_exit_memory_seconds * 1_000
        )
        recent_top_asks = (
            tick.ask1,
            *(
                quote.ask for quote in self.analyzer.book_quotes
                if quote.market_ts_ms >= recent_cutoff_ms
                and quote.market_ts_ms <= tick.market_ts_ms
                and quote.ask > 0
            ),
        )
        exit_prices = tuple(
            ask_price for ask_price, _ in tick.asks
            if ask_price - entry_price + 1e-9
                >= policy.wide_reward_risk_minimum_exit_edge
            and any(
                abs(ask_price - recent_top_ask)
                    <= policy.wide_reward_risk_exit_cluster_band + 1e-9
                for recent_top_ask in recent_top_asks
            )
        )
        if not exit_prices:
            return False
        exit_price = min(exit_prices)
        exit_supply = sum(
            quantity for ask_price, quantity in tick.asks
            if abs(ask_price - exit_price)
                <= policy.wide_reward_risk_exit_cluster_band + 1e-9
        )
        if exit_supply + 1e-9 < (
            policy.wide_reward_risk_minimum_exit_supply_multiple
            * self.parameters.order_quantity_bonds
        ):
            return False
        gross_exit_edge = exit_price - entry_price
        required_wall_bonds = (
            policy.wide_reward_risk_minimum_wall_multiple
            * self.parameters.order_quantity_bonds
        )
        protection_prices = tuple(
            price for price, quantity in tick.bids
            if price + 1e-9 < entry_price
            and entry_price - price
                <= (
                    policy.wide_reward_risk_maximum_protection_distance
                    + 1e-9
                )
            and quantity + 1e-9 >= required_wall_bonds
        )
        if not protection_prices:
            return False
        protection_price = max(protection_prices)
        protection_distance = entry_price - protection_price
        return (
            protection_distance > 1e-9
            and gross_exit_edge + 1e-9 >= (
                policy.wide_reward_risk_minimum_ratio
                * protection_distance
            )
        )

    def _session_resilient_ordinary_entry(
        self,
        account: MakerAccount,
        tick: ReplayTick,
        assessment: MarketAssessment,
        candidate_price: float,
    ) -> SessionResilientEntryDecision | None:
        """Admit persistent same-day high-side acceptance as soft evidence.

        This is deliberately narrower than a generic wide-spread exception.
        The parent valuation decision must already consider the ordinary bid
        worthwhile; this method only supplies a composite alternative to the
        nested-support veto.  Allocation to this bond still happens later.
        """

        policy = account.policy
        if not (
            policy.enable_session_resilient_ordinary_entry
            and account.fill_mode == "priority"
            and assessment.state != "falling"
            and candidate_price > 0
            and tick.ask1 > candidate_price
        ):
            return None

        visible_exit = tick.ask1 - self.parameters.price_tick
        if (
            visible_exit - candidate_price + 1e-9
            < policy.session_resilient_minimum_exit_edge
        ):
            return None

        near_support = sum(
            bonds for price, bonds in tick.bids
            if candidate_price
                - policy.session_resilient_near_support_distance - 1e-9
                <= price <= candidate_price + 1e-9
        )
        if (
            near_support + 1e-9
            < policy.session_resilient_minimum_near_support_bonds
        ):
            return None

        high_floor = (
            candidate_price + policy.session_resilient_minimum_exit_edge
        )
        high_ceiling = (
            tick.ask1
            + policy.session_resilient_high_trade_ceiling_above_ask
        )
        session = tuple(
            event for event in self.analyzer.session_trade_evidence
            if event.market_ts_ms <= tick.market_ts_ms
        )
        high = tuple(
            event for event in session
            if high_floor - 1e-9 <= event.price <= high_ceiling + 1e-9
        )
        if (
            len(high)
            < policy.session_resilient_minimum_high_trade_events
        ):
            return None

        high_bonds = sum(event.bonds for event in high)
        high_buys = tuple(event for event in high if event.side == "buy")
        high_buy_bonds = sum(event.bonds for event in high_buys)
        total_bonds = sum(event.bonds for event in session)
        high_share = high_bonds / total_bonds if total_bonds > 0 else 0.0
        span_seconds = max(
            0.0,
            (high[-1].market_ts_ms - high[0].market_ts_ms) / 1_000,
        )
        bucket_ms = max(
            1, policy.session_resilient_bucket_seconds * 1_000,
        )
        bucket_count = len({
            event.market_ts_ms // bucket_ms for event in high
        })

        def scaled(value: float, scale: float) -> float:
            return min(1.0, value / max(scale, 1e-9))

        # Duration and cross-bucket coverage carry forty percent of the
        # composite, so one dense burst cannot impersonate a resilient day.
        quality = (
            0.15 * scaled(
                high_bonds,
                policy.session_resilient_high_trade_bond_scale,
            )
            + 0.10 * scaled(
                high_buy_bonds,
                policy.session_resilient_high_buy_bond_scale,
            )
            + 0.10 * scaled(
                len(high),
                policy.session_resilient_high_trade_event_scale,
            )
            + 0.20 * scaled(
                span_seconds,
                policy.session_resilient_span_scale_seconds,
            )
            + 0.20 * scaled(
                bucket_count,
                policy.session_resilient_bucket_scale,
            )
            + 0.10 * scaled(
                high_share,
                policy.session_resilient_high_trade_share_scale,
            )
            + 0.15 * scaled(
                near_support,
                self.parameters.large_wall_multiple
                    * self.parameters.order_quantity_bonds,
            )
        )
        if (
            quality + 1e-9
            < policy.session_resilient_minimum_composite_quality
        ):
            return None

        exit_price = min(
            visible_exit,
            max(high_floor, min(event.price for event in high)),
        )
        return SessionResilientEntryDecision(
            entry_price=candidate_price,
            exit_price=exit_price,
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
            composite_quality=quality,
        )

    def _ordinary_liquidity_corridor_buy_ceiling(
        self,
        account: MakerAccount,
        tick: ReplayTick,
        assessment: MarketAssessment,
        candidate_price: float,
    ) -> float | None:
        """Return a causal buy ceiling for a real two-sided T corridor.

        This deliberately does not calculate a fair value.  Recent sells near
        the candidate show that a passive bid can realistically be reached;
        materially higher buys near the current offer show that an exit area
        has attracted real demand.  Current nested depth remains mandatory,
        and confirmed one-way states retain their parent-model handling.
        """

        policy = account.policy
        if not (
            policy.enable_ordinary_liquidity_corridor_entry
            and account.fill_mode == "priority"
            and account.customer_base_short_bonds <= 1e-9
            and account.inventory + 1e-9 >= account.initial_inventory
            and account.inventory + 1e-9 < account.maximum_inventory
            and not any(
                lot.entry_price is None
                and lot.db_id in account.sell_orders
                for lot in account.lots.values()
            )
            and assessment.state
                in {"stable", "possible_rise", "possible_fall"}
            and tick.ask1 > candidate_price > 0
            and tick.ask1_bonds + 1e-9
                >= policy.ordinary_liquidity_corridor_minimum_ask_bonds
            and self._ordinary_nested_bid_support_is_safe(
                policy, tick, 0.0, candidate_price,
            )
        ):
            return None

        cutoff_ms = (
            tick.market_ts_ms
            - policy.ordinary_liquidity_corridor_window_seconds * 1_000
        )
        low_sells = tuple(
            event for event in self.analyzer.trade_evidence
            if event.market_ts_ms >= cutoff_ms
            and event.side == "sell"
            and candidate_price
                - policy.ordinary_liquidity_corridor_low_sell_band - 1e-9
                <= event.price
                <= candidate_price + self.parameters.price_tick + 1e-9
        )
        high_buys = tuple(
            event for event in self.analyzer.trade_evidence
            if event.market_ts_ms >= cutoff_ms
            and event.side == "buy"
            and event.price - candidate_price + 1e-9
                >= policy.ordinary_liquidity_corridor_minimum_exit_edge
            and abs(event.price - tick.ask1)
                <= (
                    policy
                        .ordinary_liquidity_corridor_maximum_high_buy_ask_gap
                    + 1e-9
                )
        )
        if not (
            sum(event.bonds for event in low_sells) + 1e-9
                >= policy.ordinary_liquidity_corridor_minimum_low_sell_bonds
            and sum(event.bonds for event in high_buys) + 1e-9
                >= policy.ordinary_liquidity_corridor_minimum_high_buy_bonds
        ):
            return None

        # Remain close to the proven low-side fill area and leave at least the
        # reviewed gross corridor to the lowest qualifying high-side buy.
        low_side_ceiling = (
            max(event.price for event in low_sells)
            + policy.ordinary_liquidity_corridor_low_sell_band
        )
        high_side_ceiling = (
            min(event.price for event in high_buys)
            - policy.ordinary_liquidity_corridor_minimum_exit_edge
        )
        ceiling = _floor_to_tick(
            min(low_side_ceiling, high_side_ceiling),
            self.parameters.price_tick,
        )
        if candidate_price > ceiling + 1e-9:
            return None
        return ceiling

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
        self._update_ordinary_tape_turnover_regime(account, tick, assessment)
        self._update_shared_reentry_recovery(account, tick)
        anchor = self.analyzer.last_anchor
        trend_assessment = self._assessment_for_account(
            account, tick, assessment,
        )
        context = self._decision_context(tick, account.policy)
        if (
            context.reference_source
                == "retained_intraday_working_reference"
            and abs(account.inventory - account.initial_inventory) > 1e-9
        ):
            # The confirmed correction concerns neutral inventory choosing
            # whether to quote either edge of a quiet wide corridor.  Once a
            # leg has filled, extra-inventory exits and customer-base-short
            # recovery keep the immutable parent valuation/risk path; a stale
            # retained centre must not strand an existing risk position.
            context = self._decision_context(
                tick,
                replace(
                    account.policy,
                    retain_intraday_reference_in_quiet_wide_market=False,
                ),
            )
        v11 = account.policy.enable_priority_v11_extensions
        confirmed_rise_recent = (
            self._confirmed_rise_is_recent(tick, account.policy) if v11 else False
        )
        desired_buy: tuple[float, float, float | None] | None = None
        desired_buy_boundary: float | None = None
        desired_buy_boundary_kind: str | None = None
        desired_buy_kind = "low_bid_reversion"
        adjacent_bid_cushion_decision: (
            AdjacentBidCushionDecision
            | JointCausalCorridorDecision
            | None
        ) = None
        joint_causal_corridor_decision: (
            JointCausalCorridorDecision | None
        ) = None
        isolated_top_bid_decision: IsolatedTopBidDecision | None = None
        session_resilient_entry_decision: (
            SessionResilientEntryDecision | None
        ) = None
        visible_downtrend_wall_price = None
        visible_downtrend_wall_bonds = 0.0
        falling_profitable_reentry_active = False
        support_collapse_capacity_pending = False
        inventory_deficit = max(
            0.0, account.initial_inventory - account.inventory
        )
        support_collapse_capacity_pending = (
            self._support_collapse_base_protection_active(account, tick)
        )
        inventory_turn_replenishment = min(
            account.pending_inventory_turn_quantity,
            max(0.0, account.maximum_inventory - account.inventory),
        )
        recovery_passive_quote = self._ordinary_recovery_allows_passive_quote(account, tick)
        in_entry_window = self._entry_window_for_policy(
            tick.market_time, account.policy, tick.market_date,
        ) and (not self._ordinary_risk_exit_needs_new_frame(account, tick)
               or recovery_passive_quote)
        if (
            context.reference_price > 0
            and in_entry_window
            and tick.bid1 > 0 and tick.ask1 > tick.bid1
        ):
            price = tick.bid1
            falling_profitable_reentry_cap = None
            ordinary_falling_reentry_active = (
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
            self._update_guarded_live_exit_reentry_latch(account, tick)
            stalled_extra_reentry_active = (
                account.policy.enable_stalled_extra_inventory_near_flat_exit
                and not self._shared_current_opportunity_reentry_allowed(account, tick)
                and inventory_deficit <= 1e-9
                and account.last_stalled_extra_exit_price > 0
                and 0 <= (
                    tick.market_ts_ms
                        - account.last_stalled_extra_exit_ts_ms
                ) <= (
                    account.policy
                        .stalled_extra_exit_reentry_cooldown_seconds * 1_000
                )
                and (
                    account.policy
                        .enable_live_priority_extra_inventory_exit_exposure
                    or (
                        account.policy
                            .enable_guarded_live_priority_extra_inventory_exit_exposure
                        and account.policy
                            .guarded_live_exit_records_stalled_reentry
                    )
                    or (
                        assessment.state in {"possible_fall", "falling"}
                        and not confirmed_rise_recent
                    )
                )
                and not (
                    account.policy
                        .guarded_live_exit_release_reentry_on_high_attack
                    and account.guarded_live_exit_reentry_released
                    and account.last_stalled_extra_exit_ts_ms
                        == account.last_guarded_live_exit_ts_ms
                    and abs(
                        account.last_stalled_extra_exit_price
                            - account.last_guarded_live_exit_price
                    ) <= 1e-9
                )
            )
            support_collapse_reentry_active = (
                self._support_collapse_reentry_restriction_active(
                    account, tick,
                )
                and inventory_deficit <= 1e-9
            )
            falling_profitable_reentry_active = (
                ordinary_falling_reentry_active
                or stalled_extra_reentry_active
                or support_collapse_reentry_active
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
            reentry_caps = []
            if ordinary_falling_reentry_active:
                reentry_caps.append(
                    account.last_falling_profitable_exit_price
                        - account.policy
                            .minimum_falling_profitable_reentry_improvement
                )
            if stalled_extra_reentry_active:
                reentry_caps.append(
                    account.last_stalled_extra_exit_price
                        - account.policy
                            .stalled_extra_exit_reentry_minimum_improvement
                )
            if support_collapse_reentry_active:
                reentry_caps.append(
                    account.last_support_collapse_exit_price
                        - account.policy
                            .support_collapse_reentry_minimum_improvement
                )
            if reentry_caps:
                falling_profitable_reentry_cap = min(reentry_caps)
                price = min(price, falling_profitable_reentry_cap)
            average_sale_price = None
            maximum_replenishment_price = None
            minimum_replenishment_edge = self.parameters.minimum_entry_edge
            planned_downtrend_replenishment = False
            planned_repeated_turn_replenishment = False
            planned_profitable_base_replenishment = False
            planned_dynamic_base_replenishment = False
            live_priority_base_replenishment = False
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
                    if (
                        account.policy
                            .enable_trend_price_discovery_base_replenishment
                        and trend_assessment.reference_source
                            == "trend_price_discovery"
                    ):
                        maximum_replenishment_price = min(
                            trend_assessment.reference_high,
                            average_sale_price
                                + account.policy
                                    .trend_base_replenishment_maximum_loss,
                        )
                    else:
                        maximum_replenishment_price = min(
                            context.reference_price
                                + self.parameters.fair_price_tolerance,
                            average_sale_price
                                + account.policy
                                    .dynamic_base_replenishment_maximum_loss,
                        )
                    isolated_top_bid_decision = (
                        self._isolated_top_bid_replenishment_decision(
                            account, tick,
                        )
                    )
                    dynamic_bid_price = (
                        isolated_top_bid_decision.reliable_bid_price
                        if isolated_top_bid_decision is not None
                        else tick.bid1
                    )
                    price = min(dynamic_bid_price, maximum_replenishment_price)
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
                account.policy
                    .enable_live_priority_base_replenishment_exposure
                and inventory_deficit > 1e-9
                and isolated_top_bid_decision is None
            ):
                isolated_top_bid_decision = (
                    self._isolated_top_bid_replenishment_decision(
                        account, tick,
                    )
                )
            live_priority_base_replenishment = (
                account.policy
                    .enable_live_priority_base_replenishment_exposure
                and inventory_deficit > 1e-9
                and isolated_top_bid_decision is None
            )
            if live_priority_base_replenishment:
                # A still-valid recovery order is execution, not a stale
                # valuation marker.  Keep it at the current inside bid; the
                # priority block below improves it one tick when that remains
                # passive.  If the recovery thesis becomes invalid, the
                # normal decision path withdraws the order instead of parking
                # it behind the book.
                # Market book prices are already legal exchange ticks, but
                # the upstream float can arrive a few ulps below the printed
                # value (for example 138.799 as 138.798999...).  Normalize to
                # the nearest legal tick before the generic buy-side floor so
                # a live best-bid quote is not accidentally moved back one
                # tick.
                price = _floor_to_tick(
                    tick.bid1 + self.parameters.price_tick * 0.5,
                    self.parameters.price_tick,
                )
                maximum_replenishment_price = None
            if (
                account.policy.enable_visible_wall_anchored_downtrend_entry
                and not account.policy.ignore_legacy_bid_wall_entry_caps
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
                not account.policy.ignore_legacy_bid_wall_entry_caps
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
            price = _floor_to_tick(
                price, self.parameters.price_tick,
                snap_grid_noise=(account.fill_mode == "priority"
                    and account.policy.normalize_native_priority_price_grid),
            )
            apply_opening_trade_constraint = (
                inventory_deficit <= 1e-9
                and not inventory_turn_plan_pending
            )
            unconstrained_extra_entry_reference = (
                self._ordinary_extra_entry_reference(
                    account, tick, context.reference_price,
                    context.reference_source,
                )
            )
            extra_entry_reference = self._ordinary_extra_entry_reference(
                account, tick, context.reference_price,
                context.reference_source,
                apply_opening_trade_constraint=(
                    apply_opening_trade_constraint
                ),
            )
            opening_trade_constrained = (
                apply_opening_trade_constraint
                and extra_entry_reference + 1e-9
                    < unconstrained_extra_entry_reference
            )
            fair_value_entry_edge = extra_entry_reference - price
            entry_edge = fair_value_entry_edge
            round_trip_safe = True
            if average_sale_price is not None:
                round_trip_safe = (
                    live_priority_base_replenishment
                    or
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
            ordinary_liquidity_corridor_ceiling = (
                self._ordinary_liquidity_corridor_buy_ceiling(
                    account, tick, assessment, price,
                )
                if (
                    inventory_deficit <= 1e-9
                    and not inventory_turn_plan_pending
                )
                else None
            )
            if ordinary_liquidity_corridor_ceiling is not None:
                # This is a separate liquidity-provision permission, not a
                # higher fair-value estimate.  The order remains an ordinary
                # passive low bid so the confirmed nested-support lifecycle
                # continues to apply on every refresh.
                entry_safe = True
            if (
                inventory_deficit <= 1e-9
                and not inventory_turn_plan_pending
                and fair_value_entry_edge
                    + self.parameters.fair_price_tolerance + 1e-9
                    >= self.parameters.minimum_entry_edge
            ):
                session_resilient_entry_decision = (
                    self._session_resilient_ordinary_entry(
                        account, tick, assessment, price,
                    )
                )
                if (
                    not entry_safe
                    and session_resilient_entry_decision is not None
                ):
                    # The same-day evidence replaces only the missing support
                    # permission.  It does not manufacture a fair-value edge,
                    # and the shared allocator may still prefer cash.
                    entry_safe = True
                    desired_buy_kind = "session_resilient_value_entry"
            if planned_base_replenishment or live_priority_base_replenishment:
                # Restoring a sold customer base reduces an existing economic
                # short; it is not a new extra position that must satisfy the
                # ordinary low-entry wall test.
                entry_safe = True
            if (
                v11
                and
                not entry_safe
                and not opening_trade_constrained
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
                and not account.policy.ignore_legacy_bid_wall_entry_caps
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
            candidate_cushion = self._adjacent_bid_cushion_entry(
                account, tick, assessment, context,
            )
            if (
                candidate_cushion is not None
                and (
                    not entry_safe
                    or candidate_cushion.price
                        > price + self.parameters.price_tick + 1e-9
                )
            ):
                price = candidate_cushion.price
                entry_safe = True
                desired_buy_kind = "adjacent_bid_cushion_entry"
                adjacent_bid_cushion_decision = candidate_cushion
            candidate_joint_corridor = (
                self._joint_causal_corridor_two_sided_quote(
                    account, tick, assessment, context,
                )
            )
            if candidate_joint_corridor is not None:
                # The same reviewed evidence must drive both legs.  Give this
                # identity precedence over an ordinary or tight-cushion bid at
                # the same price so the base offer below can use the exact
                # planned replenishment level and both orders remain auditable.
                price = candidate_joint_corridor.price
                entry_safe = True
                desired_buy_kind = "joint_causal_corridor_entry"
                adjacent_bid_cushion_decision = candidate_joint_corridor
                joint_causal_corridor_decision = candidate_joint_corridor
            if (
                entry_safe
                and desired_buy_kind == "low_bid_reversion"
                and inventory_deficit <= 1e-9
                and not inventory_turn_plan_pending
            ):
                nested_support_safe = (
                    self._ordinary_nested_bid_support_is_safe(
                        account.policy, tick, fair_value_entry_edge, price,
                    )
                )
                if not nested_support_safe:
                    session_resilient_entry_decision = (
                        self._session_resilient_ordinary_entry(
                            account, tick, assessment, price,
                        )
                    )
                    if session_resilient_entry_decision is None:
                        entry_safe = False
                    else:
                        desired_buy_kind = (
                            "session_resilient_value_entry"
                        )
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
            if live_priority_base_replenishment:
                opening_edge_safe = True
            capacity = max(0.0, account.maximum_inventory - account.inventory)
            affordable = self._affordable_buy_bonds(account, price)
            if inventory_deficit > 1e-9:
                desired_buy_kind = (
                    (
                        "isolated_top_bid_guarded_base_replenish"
                        if isolated_top_bid_decision is not None
                        else "dynamic_customer_base_replenish"
                    )
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
                desired_buy = (
                    price,
                    quantity,
                    (
                        joint_causal_corridor_decision.sell_price
                        if desired_buy_kind
                            == "joint_causal_corridor_entry"
                        and joint_causal_corridor_decision is not None
                        else (
                            session_resilient_entry_decision.exit_price
                            if desired_buy_kind
                                == "session_resilient_value_entry"
                            and session_resilient_entry_decision is not None
                            else None
                        )
                    ),
                )
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
                elif live_priority_base_replenishment:
                    desired_buy_boundary = price
                    desired_buy_boundary_kind = "live_priority_price"
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
                    if (
                        visible_downtrend_wall_price is not None
                        and not account.policy.ignore_legacy_bid_wall_entry_caps
                    ):
                        desired_buy_boundary = min(
                            desired_buy_boundary,
                            visible_downtrend_wall_price
                                + self.parameters
                                    .maximum_downtrend_wall_entry_premium,
                        )
                    if (
                        not account.policy.ignore_legacy_bid_wall_entry_caps
                        and
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
                if (
                    desired_buy_kind == "low_bid_reversion"
                    and ordinary_liquidity_corridor_ceiling is not None
                ):
                    desired_buy_boundary = min(
                        desired_buy_boundary,
                        ordinary_liquidity_corridor_ceiling,
                    )
                    desired_buy_boundary_kind = (
                        "liquidity_corridor_ceiling"
                    )
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
            retained_cushion = self._retain_adjacent_bid_cushion_entry(
                account, existing_buy, tick, assessment,
            )
            if retained_cushion is not None:
                desired_buy_kind = existing_buy.kind
                adjacent_bid_cushion_decision = retained_cushion
                desired_buy = (
                    existing_buy.limit_price,
                    existing_buy.remaining,
                    existing_buy.target_price,
                )
                desired_buy_boundary = existing_buy.price_boundary
            retained_joint_corridor = (
                self._retain_joint_causal_corridor_quote(
                    account, existing_buy, tick, assessment, context,
                )
            )
            if retained_joint_corridor is not None:
                desired_buy_kind = existing_buy.kind
                adjacent_bid_cushion_decision = retained_joint_corridor
                joint_causal_corridor_decision = retained_joint_corridor
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
            and not account.policy.ignore_legacy_bid_wall_entry_caps
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
        if (
            desired_buy is not None
            and account.policy
                .enable_live_priority_base_replenishment_exposure
            and inventory_deficit > 1e-9
        ):
            final_isolated_top_bid = (
                self._isolated_top_bid_replenishment_decision(account, tick)
            )
            if final_isolated_top_bid is None:
                live_price = _floor_to_tick(
                    tick.bid1 + self.parameters.price_tick * 0.5,
                    self.parameters.price_tick,
                )
                improved = live_price + self.parameters.price_tick
                if improved < tick.ask1:
                    live_price = improved
                desired_buy = (
                    live_price,
                    min(desired_buy[1], inventory_deficit),
                    None,
                )
                desired_buy_kind = "dynamic_customer_base_replenish"
                desired_buy_boundary = live_price
                desired_buy_boundary_kind = "live_priority_price"
                adjacent_bid_cushion_decision = None
                joint_causal_corridor_decision = None
                isolated_top_bid_decision = None
        support_collapse_redeploy = (
            self._support_collapse_capacity_redeploy_price(account, tick)
            if in_entry_window and context.reference_price > 0
            else None
        )
        if support_collapse_redeploy is not None:
            redeploy_price, redeploy_ceiling = support_collapse_redeploy
            desired_buy = (
                redeploy_price,
                min(
                    self.parameters.order_quantity_bonds,
                    max(0.0, account.maximum_inventory - account.inventory),
                ),
                None,
            )
            desired_buy_kind = "support_collapse_capacity_redeploy_entry"
            desired_buy_boundary = redeploy_ceiling
            desired_buy_boundary_kind = "support_collapse_reentry_ceiling"
            adjacent_bid_cushion_decision = None
            joint_causal_corridor_decision = None
            isolated_top_bid_decision = None
        if recovery_passive_quote and desired_buy_kind != "low_bid_reversion":
            # Only ordinary future quoting is newly allowed. Special entries
            # and dedicated redeployment plans keep their original frame rule.
            desired_buy = None
            adjacent_bid_cushion_decision = None
            joint_causal_corridor_decision = None
            isolated_top_bid_decision = None
        if desired_buy is not None and self._ordinary_entry_sell_pressure_blocks(
            account, tick, assessment, context, kind=desired_buy_kind,
            price=desired_buy[0], quantity=desired_buy[1],
        ):
            desired_buy = None
            # Bypass ordinary quote-retention grace on current adverse evidence.
            if account.buy_order is not None and account.buy_order.kind == "low_bid_reversion":
                self._cancel_order(
                    account, account.buy_order, tick,
                    "ordinary_entry_sell_pressure", persist,
                )
        self._replace_buy(
            account, tick, desired_buy, desired_buy_kind,
            price_boundary=desired_buy_boundary,
            price_boundary_kind=desired_buy_boundary_kind,
            market_state=assessment.state, persist=persist,
            adjacent_bid_cushion=adjacent_bid_cushion_decision,
            isolated_top_bid=isolated_top_bid_decision,
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
        joint_two_sided_quote_ready = (
            joint_causal_corridor_decision is not None
            and account.buy_order is not None
            and account.buy_order.kind == "joint_causal_corridor_entry"
            and abs(
                account.buy_order.limit_price
                    - joint_causal_corridor_decision.price
            ) <= 1e-9
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
        if (
            (
                context.reference_price > 0
                or account.policy
                    .enable_live_priority_extra_inventory_exit_exposure
            )
            and tick.ask1 > tick.bid1
        ):
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
                live_priority_extra_inventory_exit = (
                    account.policy
                        .enable_live_priority_extra_inventory_exit_exposure
                    and self
                        ._live_priority_extra_inventory_exit_enabled_for_lot(
                            account, lot,
                        )
                    and account.fill_mode == "priority"
                    and lot.entry_price is not None
                )
                if live_priority_extra_inventory_exit:
                    # Half a tick avoids binary-float subtraction turning an
                    # exact one-tick improvement into an accidental two-tick
                    # floor (for example 136.700 -> 136.698).
                    price = _floor_to_tick(
                        tick.ask1 - self.parameters.price_tick * 0.5,
                        self.parameters.price_tick,
                    )
                protected_live_exit_price = (
                    self._live_priority_extra_inventory_isolated_offer_price(
                        account, lot, tick, assessment, context,
                    )
                    if live_priority_extra_inventory_exit
                    else None
                )
                isolated_low_offer_turnover_hold = (
                    protected_live_exit_price is not None
                    if live_priority_extra_inventory_exit
                    else (
                        lot.entry_price is not None
                        and self._isolated_low_offer_turnover_hold(
                            account, lot, tick,
                        )
                    )
                )
                if isolated_low_offer_turnover_hold:
                    retained_sell = account.sell_orders.get(lot.db_id)
                    if protected_live_exit_price is not None:
                        price = protected_live_exit_price
                        desired_sell_kind = (
                            "live_priority_extra_inventory_isolated_hold"
                        )
                    elif retained_sell is not None:
                        price = retained_sell.limit_price
                        desired_sell_kind = retained_sell.kind
                full_inventory_capacity_release = (
                    account.policy.enable_full_inventory_capacity_release
                    and not isolated_low_offer_turnover_hold
                    and lot.entry_price is not None
                    and account.inventory + 1e-9
                        >= account.maximum_inventory
                    and assessment.state in {"possible_fall", "falling"}
                    and price - lot.entry_price + 1e-9 >= (
                        account.policy
                            .full_inventory_passive_exit_minimum_edge
                    )
                    and not confirmed_rise_recent
                )
                stalled_near_flat_release = (
                    self._stalled_extra_inventory_near_flat_exit_ready(
                        account, lot, tick, assessment,
                    )
                )
                support_collapse_capacity_release = (
                    self._support_collapse_capacity_release_ready(
                        account, lot, tick, assessment,
                    )
                )
                failed_breakout_sweep_release = (
                    account.policy.enable_strict_breakout_episode
                    and self.strict_breakout_failed
                    and lot.kind == "sweep_tail"
                    and lot.entry_price is not None
                    and price + (
                        account.policy
                            .full_inventory_active_exit_maximum_loss
                    ) + 1e-9 >= lot.entry_price
                )
                if live_priority_extra_inventory_exit:
                    desired_sell_kind = (
                        "live_priority_extra_inventory_isolated_hold"
                        if protected_live_exit_price is not None
                        else "live_priority_extra_inventory_exit"
                    )
                elif full_inventory_capacity_release:
                    desired_sell_kind = (
                        "full_inventory_capacity_release_exit"
                    )
                elif stalled_near_flat_release:
                    desired_sell_kind = (
                        "stalled_extra_inventory_near_flat_exit"
                    )
                elif support_collapse_capacity_release:
                    desired_sell_kind = (
                        "support_collapse_capacity_release_exit"
                    )
                elif failed_breakout_sweep_release:
                    desired_sell_kind = "failed_breakout_sweep_release"
                if (
                    lot.entry_price is not None
                    and context.breakout_support_strong
                    and (
                        not live_priority_extra_inventory_exit
                        or account.policy
                            .enable_guarded_live_priority_extra_inventory_exit_exposure
                    )
                    and not full_inventory_capacity_release
                    and not stalled_near_flat_release
                    and not support_collapse_capacity_release
                    and not failed_breakout_sweep_release
                ):
                    support_quote = (
                        context.breakout_support_price
                        - self.parameters.price_tick
                        if account.fill_mode == "priority"
                        else context.breakout_support_price
                    )
                    price = max(price, support_quote)
                if lot.entry_price is None:
                    if support_collapse_capacity_pending:
                        # The account has deliberately returned to neutral to
                        # reuse its extra 1,000-bond capacity at the lower
                        # corridor.  Selling the customer's opening base here
                        # would create a different short risk and contradict
                        # the release decision.
                        continue
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
                    if joint_two_sided_quote_ready:
                        assert joint_causal_corridor_decision is not None
                        price = joint_causal_corridor_decision.sell_price
                        desired_sell_kind = (
                            "joint_causal_corridor_base_sell"
                        )
                        repeated_turn_replenishment_price = (
                            joint_causal_corridor_decision.price
                        )
                    elif high_cluster_preposition is not None:
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
                    live_priority_extra_inventory_exit
                    and not account.policy
                        .enable_guarded_live_priority_extra_inventory_exit_exposure
                ):
                    # The actual bought T lot must remain exposed in the live
                    # sell-side first position.  Cost, a stale reference and
                    # fixed profit/loss thresholds cannot create an orderless
                    # interval; only the isolated-offer price selected above
                    # may keep it at the independently normal higher ladder.
                    pass
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
                    and not failed_breakout_sweep_release
                    and not stalled_near_flat_release
                    and not support_collapse_capacity_release
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
                    and not live_priority_extra_inventory_exit
                    and not full_inventory_capacity_release
                    and not stalled_near_flat_release
                    and not support_collapse_capacity_release
                    and not failed_breakout_sweep_release
                ):
                    continue
                elif (
                    not (
                        full_inventory_capacity_release
                        or stalled_near_flat_release
                        or support_collapse_capacity_release
                        or failed_breakout_sweep_release
                    )
                    and not live_priority_extra_inventory_exit
                    and not self._sell_is_reasonable(price, context)
                ):
                    continue
                price = _floor_to_tick(
                    price, self.parameters.price_tick,
                    snap_grid_noise=(account.fill_mode == "priority"
                        and account.policy.normalize_native_priority_price_grid),
                )
                opening_sell_edge = price - context.reference_price
                if lot.entry_price is not None:
                    opening_sell_edge = max(
                        opening_sell_edge,
                        price - lot.entry_price,
                    )
                if (
                    not live_priority_extra_inventory_exit
                    and not self.parameters.opening_edge_is_safe(
                        tick.market_date,
                        tick.market_time,
                        opening_sell_edge,
                    )
                ):
                    continue
                sell_price_boundary = price
                if live_priority_extra_inventory_exit:
                    sell_price_boundary = price
                elif stalled_near_flat_release:
                    assert lot.entry_price is not None
                    sell_price_boundary = _floor_to_tick(
                        lot.entry_price
                            - account.policy.stalled_extra_exit_maximum_loss,
                        self.parameters.price_tick,
                    )
                elif support_collapse_capacity_release:
                    # The initial trigger is bounded, but after committing to
                    # the release the order must keep following the descending
                    # offer.  Using the current price as the audited floor
                    # prevents a stale historical loss cap from cancelling the
                    # order while the same support-collapse episode continues.
                    sell_price_boundary = price
                elif (
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
                    if full_inventory_capacity_release:
                        sell_price_boundary = (
                            lot.entry_price
                            + account.policy
                                .full_inventory_passive_exit_minimum_edge
                        )
                    else:
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
                if (
                    (
                        not live_priority_extra_inventory_exit
                        or account.policy
                            .enable_guarded_live_priority_extra_inventory_exit_exposure
                    )
                    and self.parameters.opening_caution_is_active(
                        tick.market_date, tick.market_time,
                    )
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
                    protective_bid_floor_price=(
                        joint_causal_corridor_decision.floor_price
                        if desired_sell_kind
                            == "joint_causal_corridor_base_sell"
                        and joint_causal_corridor_decision is not None
                        else 0.0
                    ),
                    protective_bid_ceiling_price=(
                        joint_causal_corridor_decision.ceiling_price
                        if desired_sell_kind
                            == "joint_causal_corridor_base_sell"
                        and joint_causal_corridor_decision is not None
                        else 0.0
                    ),
                    protective_bid_entry_bonds=(
                        joint_causal_corridor_decision.entry_bonds
                        if desired_sell_kind
                            == "joint_causal_corridor_base_sell"
                        and joint_causal_corridor_decision is not None
                        else 0.0
                    ),
                    protective_bid_entry_edge=(
                        joint_causal_corridor_decision.entry_edge
                        if desired_sell_kind
                            == "joint_causal_corridor_base_sell"
                        and joint_causal_corridor_decision is not None
                        else 0.0
                    ),
                    joint_corridor_high_trade_bonds=(
                        joint_causal_corridor_decision.high_trade_bonds
                        if desired_sell_kind
                            == "joint_causal_corridor_base_sell"
                        and joint_causal_corridor_decision is not None
                        else 0.0
                    ),
                    joint_corridor_ask_supply_bonds=(
                        joint_causal_corridor_decision.ask_supply_bonds
                        if desired_sell_kind
                            == "joint_causal_corridor_base_sell"
                        and joint_causal_corridor_decision is not None
                        else 0.0
                    ),
                    joint_corridor_emergency_loss=(
                        joint_causal_corridor_decision.emergency_loss
                        if desired_sell_kind
                            == "joint_causal_corridor_base_sell"
                        and joint_causal_corridor_decision is not None
                        else 0.0
                    ),
                    joint_corridor_reward_risk=(
                        joint_causal_corridor_decision.reward_risk
                        if desired_sell_kind
                            == "joint_causal_corridor_base_sell"
                        and joint_causal_corridor_decision is not None
                        else 0.0
                    ),
                    joint_corridor_exceptional_support=(
                        joint_causal_corridor_decision.exceptional_support
                        if desired_sell_kind
                            == "joint_causal_corridor_base_sell"
                        and joint_causal_corridor_decision is not None
                        else False
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
                        or (
                            (account.policy.retain_guarded_live_exit_across_parent_order_gap
                             or account.policy.enable_causal_ordinary_inventory_turnover)
                            and order.kind in {
                                "live_priority_extra_inventory_exit",
                                "live_priority_extra_inventory_isolated_hold",
                            }
                            and tick.market_ts_ms > lot.opened_ms
                            and tick.ask1 > tick.bid1 > 0
                            and self
                                ._live_priority_extra_inventory_exit_enabled_for_lot(
                                    account, lot,
                                )
                            and self
                                ._guarded_live_exit_sell_side_repricing_active(
                                    account, tick, assessment,
                                )
                        )
                    )
                ):
                    continue
                self._cancel_order(account, order, tick, "exit_context_changed", persist)

        # Guarded shared-capital v0.11 is deliberately a last-resort exposure
        # repair, not an alternate exit engine.  Let the parent create, retain,
        # reprice or cancel every native sell intention first.  Only an ordinary
        # passive T lot that is still completely orderless receives this live
        # first-position fallback; active-value, sweep-tail and evidence-specific
        # lots keep their own native exits.
        if (
            (account.policy.enable_guarded_live_priority_extra_inventory_exit_exposure
             or account.policy.enable_causal_ordinary_inventory_turnover)
            and account.fill_mode == "priority"
            and tick.ask1 > tick.bid1 > 0
            and self._guarded_live_exit_sell_side_repricing_active(
                account, tick, assessment,
            )
        ):
            ordinary_exit_capacity = max(
                0.0, account.inventory - account.initial_inventory,
            )
            if account.policy.enable_causal_ordinary_inventory_turnover:
                ordinary_exit_capacity = max(0.0, ordinary_exit_capacity - sum(
                    order.remaining for lot_id, order in account.sell_orders.items()
                    if lot_id in account.lots
                    and account.lots[lot_id].entry_price is not None
                    and order.kind not in {
                        "live_priority_extra_inventory_exit",
                        "live_priority_extra_inventory_isolated_hold",
                    }
                ))
            for lot in list(account.lots.values()):
                if (
                    lot.remaining_quantity <= 1e-9
                    # The book that produced the buy fill is already consumed
                    # causal state.  Wait for one strictly later snapshot before
                    # deciding that the parent truly left an exit gap.
                    or tick.market_ts_ms <= lot.opened_ms
                    or not self
                        ._live_priority_extra_inventory_exit_enabled_for_lot(
                            account, lot,
                        )
                ):
                    continue
                existing = account.sell_orders.get(lot.db_id)
                retained_fallback = (
                    existing is not None
                    and (account.policy.retain_guarded_live_exit_across_parent_order_gap
                         or account.policy.enable_causal_ordinary_inventory_turnover)
                    and existing.kind in {
                        "live_priority_extra_inventory_exit",
                        "live_priority_extra_inventory_isolated_hold",
                    }
                )
                if existing is not None and not retained_fallback:
                    continue
                exit_quantity = lot.remaining_quantity
                if account.policy.enable_causal_ordinary_inventory_turnover:
                    exit_quantity = min(exit_quantity, ordinary_exit_capacity)
                    ordinary_exit_capacity -= exit_quantity
                    if exit_quantity <= 1e-9:
                        if retained_fallback:
                            self._cancel_order(account, existing, tick,
                                               "ordinary_neutral_capacity", persist)
                        continue
                protected_price = (
                    self._live_priority_extra_inventory_isolated_offer_price(
                        account, lot, tick, assessment, context,
                    )
                )
                price = protected_price
                desired_kind = "live_priority_extra_inventory_isolated_hold"
                if price is None:
                    price = _floor_to_tick(
                        tick.ask1 - self.parameters.price_tick * 0.5,
                        self.parameters.price_tick,
                    )
                    desired_kind = "live_priority_extra_inventory_exit"
                if price <= tick.bid1 + 1e-9:
                    # A one-tick inside market has no passive improvement
                    # price.  Stay exposed at the displayed offer instead of
                    # silently turning this fallback into an active sell.
                    price = _ceil_to_tick(
                        tick.ask1, self.parameters.price_tick,
                    )
                maximum_loss = (
                    account.policy.guarded_live_exit_maximum_loss
                )
                if (
                    maximum_loss is not None
                    and lot.entry_price is not None
                    and lot.entry_price - price
                        > maximum_loss + 1e-9
                ):
                    # This fallback keeps a normal executable sell intention
                    # alive; it is not permission to chase a discontinuous
                    # low offer indefinitely.  Native active and support-
                    # collapse risk routes remain free to act on their own
                    # complete evidence.
                    if retained_fallback:
                        assert existing is not None
                        self._cancel_order(
                            account, existing, tick,
                            "exit_context_changed", persist,
                        )
                    continue
                if (
                    retained_fallback
                    and existing is not None
                    and existing.kind == desired_kind
                    and abs(existing.limit_price - price) < 1e-9
                    and abs(existing.remaining - exit_quantity)
                        < 1e-9
                ):
                    continue
                if retained_fallback:
                    assert existing is not None
                    self._cancel_order(
                        account, existing, tick, "maker_reprice", persist,
                    )
                order = self._new_order(
                    account, tick, side="sell", kind=desired_kind,
                    lot_id=lot.db_id, price=price,
                    quantity=exit_quantity, queue_ahead=0.0,
                    target_price=price, price_boundary=price,
                    persist=persist,
                )
                account.sell_orders[lot.db_id] = order

        # V0.14 is a separate, narrow repair after every native and ordinary
        # exit. Other special lot lifecycles remain entirely with the parent.
        if (account.policy.enable_shared_resilient_exit_gap
                and account.fill_mode == "priority"
                and account.initial_inventory <= 1e-9
                and tick.ask1 > tick.bid1 > 0):
            capacity = max(0.0, account.inventory - sum(
                order.remaining for order in account.sell_orders.values()
            ))
            for lot in account.lots.values():
                if (lot.kind != "session_resilient_value_entry"
                        or lot.entry_price is None or lot.remaining_quantity <= 1e-9
                        or tick.market_ts_ms <= lot.opened_ms
                        or lot.db_id in account.sell_orders):
                    continue
                quantity = min(lot.remaining_quantity, capacity)
                if quantity <= 1e-9:
                    continue
                price, kind = self._shared_resilient_gap_price(account, lot, tick, assessment)
                account.sell_orders[lot.db_id] = self._new_order(
                    account, tick, side="sell", kind=kind, lot_id=lot.db_id,
                    price=price, quantity=quantity, queue_ahead=0.0,
                    target_price=price, price_boundary=price, persist=persist,
                )
                capacity -= quantity

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
        price_boundary_kind: str | None = None,
        market_state: str | None = None, persist: bool,
        adjacent_bid_cushion: (
            AdjacentBidCushionDecision
            | JointCausalCorridorDecision
            | None
        ) = None,
        isolated_top_bid: IsolatedTopBidDecision | None = None,
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
            price_boundary_kind=price_boundary_kind,
            persist=persist,
            exact_fill_uncertainty_buffer=exact_fill_uncertainty_buffer,
            queue_position_kind=queue_position_kind,
            protective_bid_floor_price=(
                adjacent_bid_cushion.floor_price
                if adjacent_bid_cushion is not None else 0.0
            ),
            protective_bid_ceiling_price=(
                adjacent_bid_cushion.ceiling_price
                if adjacent_bid_cushion is not None else 0.0
            ),
            protective_bid_entry_bonds=(
                adjacent_bid_cushion.entry_bonds
                if adjacent_bid_cushion is not None else 0.0
            ),
            protective_bid_entry_edge=(
                adjacent_bid_cushion.entry_edge
                if adjacent_bid_cushion is not None else 0.0
            ),
            joint_corridor_high_trade_bonds=(
                adjacent_bid_cushion.high_trade_bonds
                if isinstance(
                    adjacent_bid_cushion,
                    JointCausalCorridorDecision,
                )
                else 0.0
            ),
            joint_corridor_ask_supply_bonds=(
                adjacent_bid_cushion.ask_supply_bonds
                if isinstance(
                    adjacent_bid_cushion,
                    JointCausalCorridorDecision,
                )
                else 0.0
            ),
            joint_corridor_emergency_loss=(
                adjacent_bid_cushion.emergency_loss
                if isinstance(
                    adjacent_bid_cushion,
                    JointCausalCorridorDecision,
                )
                else 0.0
            ),
            joint_corridor_reward_risk=(
                adjacent_bid_cushion.reward_risk
                if isinstance(
                    adjacent_bid_cushion,
                    JointCausalCorridorDecision,
                )
                else 0.0
            ),
            joint_corridor_exceptional_support=(
                adjacent_bid_cushion.exceptional_support
                if isinstance(
                    adjacent_bid_cushion,
                    JointCausalCorridorDecision,
                )
                else False
            ),
            isolated_top_bid_price=(
                isolated_top_bid.isolated_price
                if isolated_top_bid is not None else 0.0
            ),
            isolated_top_bid_bonds=(
                isolated_top_bid.isolated_bonds
                if isolated_top_bid is not None else 0.0
            ),
            reliable_replenishment_bid_price=(
                isolated_top_bid.reliable_bid_price
                if isolated_top_bid is not None else 0.0
            ),
            near_ask_supply_bonds=(
                isolated_top_bid.near_ask_supply_bonds
                if isolated_top_bid is not None else 0.0
            ),
            isolated_near_ask_floor_price=(
                isolated_top_bid.near_ask_floor_price
                if isolated_top_bid is not None else 0.0
            ),
            isolated_near_ask_ceiling_price=(
                isolated_top_bid.near_ask_ceiling_price
                if isolated_top_bid is not None else 0.0
            ),
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
        price_boundary: float | None = None,
        price_boundary_kind: str | None = None, persist: bool,
        exact_fill_uncertainty_buffer: float = 0.0,
        repeated_turn_replenishment_price: float = 0.0,
        medium_wall_supported_base_short: bool = False,
        queue_position_kind: str | None = None,
        protective_bid_floor_price: float = 0.0,
        protective_bid_ceiling_price: float = 0.0,
        protective_bid_entry_bonds: float = 0.0,
        protective_bid_entry_edge: float = 0.0,
        joint_corridor_high_trade_bonds: float = 0.0,
        joint_corridor_ask_supply_bonds: float = 0.0,
        joint_corridor_emergency_loss: float = 0.0,
        joint_corridor_reward_risk: float = 0.0,
        joint_corridor_exceptional_support: bool = False,
        isolated_top_bid_price: float = 0.0,
        isolated_top_bid_bonds: float = 0.0,
        reliable_replenishment_bid_price: float = 0.0,
        near_ask_supply_bonds: float = 0.0,
        isolated_near_ask_floor_price: float = 0.0,
        isolated_near_ask_ceiling_price: float = 0.0,
        isolated_confirmed_ask_attack_bonds: float = 0.0,
    ) -> MakerOrder:
        price = _floor_to_tick(price, self.parameters.price_tick)
        if price_boundary is None:
            # Compatibility for direct test/research order construction: no
            # unreviewed chase range is implied, so the boundary is the order
            # price itself.  Every production decision path supplies a value.
            price_boundary = price
        if side == "buy" and price_boundary_kind == "live_priority_price":
            price_boundary = price
        elif side == "buy":
            price_boundary_kind = "buy_ceiling"
            price_boundary = max(
                price,
                _floor_to_tick(price_boundary, self.parameters.price_tick),
            )
        elif side == "sell":
            if price_boundary_kind is not None:
                raise ValueError(
                    "sell orders do not support a buy-side boundary kind"
                )
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
            "target_price": (
                target_price
                if kind in {
                    "sweep_tail", "joint_causal_corridor_entry",
                    "session_resilient_value_entry",
                }
                else None
            ),
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
                "protective_bid_floor_price": protective_bid_floor_price,
                "protective_bid_ceiling_price": protective_bid_ceiling_price,
                "protective_bid_entry_bonds": protective_bid_entry_bonds,
                "protective_bid_entry_edge": protective_bid_entry_edge,
                "joint_corridor_high_trade_bonds": (
                    joint_corridor_high_trade_bonds
                ),
                "joint_corridor_ask_supply_bonds": (
                    joint_corridor_ask_supply_bonds
                ),
                "joint_corridor_emergency_loss": (
                    joint_corridor_emergency_loss
                ),
                "joint_corridor_reward_risk": joint_corridor_reward_risk,
                "joint_corridor_exceptional_support": (
                    joint_corridor_exceptional_support
                ),
                "isolated_top_bid_price": isolated_top_bid_price,
                "isolated_top_bid_bonds": isolated_top_bid_bonds,
                "reliable_replenishment_bid_price": (
                    reliable_replenishment_bid_price
                ),
                "near_ask_supply_bonds": near_ask_supply_bonds,
                "isolated_near_ask_floor_price": (
                    isolated_near_ask_floor_price
                ),
                "isolated_near_ask_ceiling_price": (
                    isolated_near_ask_ceiling_price
                ),
                "isolated_confirmed_ask_attack_bonds": (
                    isolated_confirmed_ask_attack_bonds
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
            protective_bid_floor_price=protective_bid_floor_price,
            protective_bid_ceiling_price=protective_bid_ceiling_price,
            protective_bid_entry_bonds=protective_bid_entry_bonds,
            protective_bid_entry_edge=protective_bid_entry_edge,
            joint_corridor_high_trade_bonds=(
                joint_corridor_high_trade_bonds
            ),
            joint_corridor_ask_supply_bonds=(
                joint_corridor_ask_supply_bonds
            ),
            joint_corridor_emergency_loss=joint_corridor_emergency_loss,
            joint_corridor_reward_risk=joint_corridor_reward_risk,
            joint_corridor_exceptional_support=(
                joint_corridor_exceptional_support
            ),
            isolated_top_bid_price=isolated_top_bid_price,
            isolated_top_bid_bonds=isolated_top_bid_bonds,
            reliable_replenishment_bid_price=(
                reliable_replenishment_bid_price
            ),
            near_ask_supply_bonds=near_ask_supply_bonds,
            isolated_near_ask_floor_price=(
                isolated_near_ask_floor_price
            ),
            isolated_near_ask_ceiling_price=(
                isolated_near_ask_ceiling_price
            ),
            isolated_confirmed_ask_attack_bonds=(
                isolated_confirmed_ask_attack_bonds
            ),
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
        if (
            account.purpose == "standard"
            or account.policy.windfall_capacity_funded
        ):
            # The user's ordinary-account input is denominated in bonds:
            # 1,000 base bonds plus capacity for 1,000 additional bonds.
            # A stale CNY seed must not silently shrink that explicit capacity.
            return max(0.0, account.maximum_inventory - account.inventory)
        return max(0.0, account.cash / price)

    @staticmethod
    def _ensure_capacity_funding(
        account: MakerAccount, required_cash: float,
    ) -> None:
        if (
            not (
                account.purpose == "standard"
                or account.policy.windfall_capacity_funded
            )
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
    ) -> bool:
        if (
            self.buy_fill_guard is not None
            and not self.buy_fill_guard(
                account, tick, order, quantity, kind, reason,
            )
        ):
            self.store.update_maker_order(
                order.db_id,
                status="cancelled",
                updated_market_ts_ms=tick.market_ts_ms,
                filled_quantity=order.filled_quantity,
                queue_ahead=max(0.0, order.queue_ahead),
                cancel_reason="shared_capital_preferred_other_bond",
            )
            if (
                account.buy_order is not None
                and account.buy_order.db_id == order.db_id
            ):
                account.buy_order = None
            return False
        previous_inventory = account.inventory
        completed_sale_price = 0.0
        required_cash = quantity * order.limit_price
        self._ensure_capacity_funding(account, required_cash)
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
            support_collapse_turn_restored = min(
                inventory_turn_restored,
                account.pending_support_collapse_turn_quantity,
            )
            support_collapse_sale_value_restored = 0.0
            if support_collapse_turn_restored > 1e-9:
                support_average_sale_price = (
                    account.pending_support_collapse_turn_sale_value
                    / account.pending_support_collapse_turn_quantity
                    if account.pending_support_collapse_turn_sale_value > 0
                    else account.pending_inventory_turn_sale_value
                        / account.pending_inventory_turn_quantity
                )
                support_collapse_sale_value_restored = (
                    support_collapse_turn_restored
                    * support_average_sale_price
                )
            account.pending_support_collapse_turn_quantity = max(
                0.0,
                account.pending_support_collapse_turn_quantity
                    - support_collapse_turn_restored,
            )
            account.pending_support_collapse_turn_sale_value = max(
                0.0,
                account.pending_support_collapse_turn_sale_value
                    - support_collapse_sale_value_restored,
            )
            other_turn_restored = (
                inventory_turn_restored - support_collapse_turn_restored
            )
            other_turn_quantity = max(
                0.0,
                account.pending_inventory_turn_quantity
                    - support_collapse_turn_restored,
            )
            other_turn_sale_value = max(
                0.0,
                account.pending_inventory_turn_sale_value
                    - support_collapse_sale_value_restored,
            )
            other_turn_sale_value_restored = (
                other_turn_restored
                * other_turn_sale_value / other_turn_quantity
                if other_turn_restored > 1e-9
                and other_turn_quantity > 1e-9
                else 0.0
            )
            account.pending_inventory_turn_quantity = max(
                0.0,
                account.pending_inventory_turn_quantity
                    - inventory_turn_restored,
            )
            account.pending_inventory_turn_sale_value = max(
                0.0,
                account.pending_inventory_turn_sale_value
                    - support_collapse_sale_value_restored
                    - other_turn_sale_value_restored,
            )
            if account.pending_support_collapse_turn_quantity <= 1e-9:
                account.pending_support_collapse_turn_quantity = 0.0
                account.pending_support_collapse_turn_sale_value = 0.0
            if account.pending_inventory_turn_quantity <= 1e-9:
                account.pending_inventory_turn_quantity = 0.0
                account.pending_inventory_turn_sale_value = 0.0
        if restored > 1e-9 and account.joint_corridor_base_short_bonds > 1e-9:
            account.joint_corridor_base_short_bonds = max(
                0.0,
                account.joint_corridor_base_short_bonds - restored,
            )
            if account.joint_corridor_base_short_bonds <= 1e-9:
                account.joint_corridor_base_short_sell_price = 0.0
                account.joint_corridor_base_short_buy_price = 0.0
                account.joint_corridor_base_short_support_floor = 0.0
                account.joint_corridor_base_short_support_ceiling = 0.0
                account.joint_corridor_base_short_support_bonds = 0.0
                account.joint_corridor_base_short_ask_supply_bonds = 0.0
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
        if extra > 1e-9 and account.policy.ordinary_entry_sell_pressure_mode == "reentry_repricing":
            account.last_new_extra_entry_ts_ms = tick.market_ts_ms
        if extra > 1e-9:
            components.append((
                kind,
                extra,
                order.limit_price,
                (
                    target_price
                    if kind in {
                        "sweep_tail", "joint_causal_corridor_entry",
                        "session_resilient_value_entry",
                    }
                    else None
                ),
            ))
        for lot_kind, lot_quantity, entry_price, lot_target in components:
            protective_lot = lot_kind in {
                "adjacent_bid_cushion_entry",
                "joint_causal_corridor_entry",
            }
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
                protective_bid_floor_price=(
                    order.protective_bid_floor_price
                    if protective_lot else 0.0
                ),
                protective_bid_ceiling_price=(
                    order.protective_bid_ceiling_price
                    if protective_lot else 0.0
                ),
                protective_bid_entry_bonds=(
                    order.protective_bid_entry_bonds
                    if protective_lot else 0.0
                ),
                protective_bid_entry_edge=(
                    order.protective_bid_entry_edge
                    if protective_lot else 0.0
                ),
                protective_bid_last_bonds=(
                    order.protective_bid_entry_bonds
                    if protective_lot else 0.0
                ),
                protective_bid_last_ts_ms=(
                    tick.market_ts_ms
                    if protective_lot else 0
                ),
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
        if self.fill_observer is not None:
            self.fill_observer(
                account, tick, order, "buy", quantity, reason,
            )
        return True

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
        fresh_ordinary_risk_exit = (
            (account.policy.ordinary_risk_exit_uses_fresh_reentry
             or account.policy.enable_causal_ordinary_inventory_turnover)
            and account.fill_mode == "priority"
            and lot.kind == "low_bid_reversion"
            and lot.entry_price is not None
            and previous_inventory > account.initial_inventory + 1e-9
            and (
                order.kind in {
                    "stalled_extra_inventory_near_flat_exit",
                    "live_priority_extra_inventory_exit",
                    "live_priority_extra_inventory_isolated_hold",
                }
                or (
                    (account.policy.enable_ordinary_tape_turnover_regime
                     or account.policy.enable_causal_ordinary_inventory_turnover)
                    and order.kind in {
                        "full_inventory_capacity_release_exit",
                        "inventory_risk_exit",
                        "falling_profitable_bid_exit",
                    }
                )
            )
        )
        if fresh_ordinary_risk_exit:
            account.last_ordinary_risk_exit_ts_ms = tick.market_ts_ms
        shared_resilient_exit = (
            account.policy.enable_shared_resilient_exit_gap
            and order.kind in {"session_resilient_gap_exit", "session_resilient_isolated_hold"}
        )
        stalled_reentry_exit = not fresh_ordinary_risk_exit and (
            shared_resilient_exit
            or
            order.kind == "stalled_extra_inventory_near_flat_exit"
            or (
                order.kind in {
                    "live_priority_extra_inventory_exit",
                    "live_priority_extra_inventory_isolated_hold",
                }
                and (
                    not account.policy
                        .enable_guarded_live_priority_extra_inventory_exit_exposure
                    or account.policy
                        .guarded_live_exit_records_stalled_reentry
                )
            )
        )
        if (
            stalled_reentry_exit
            or (
                order.kind == "support_collapse_capacity_release_exit"
                and not account.policy
                    .enable_support_collapse_consistency_revision
            )
        ):
            account.last_stalled_extra_exit_price = order.limit_price
            account.last_stalled_extra_exit_ts_ms = tick.market_ts_ms
            if (account.policy.enable_shared_current_opportunity_reentry
                    and account.fill_mode == "priority"
                    and account.initial_inventory <= 1e-9
                    and stalled_reentry_exit
                    and lot.kind in {"low_bid_reversion", "session_resilient_value_entry"}):
                account.last_shared_reentry_exit_ts_ms = tick.market_ts_ms
                account.shared_reentry_recovery_ts_ms = 0
        if (
            order.kind in {
                "live_priority_extra_inventory_exit",
                "live_priority_extra_inventory_isolated_hold",
            }
            and account.policy
                .guarded_live_exit_release_reentry_on_high_attack
        ):
            account.last_guarded_live_exit_price = order.limit_price
            account.last_guarded_live_exit_ts_ms = tick.market_ts_ms
            account.guarded_live_exit_reentry_released = False
        if order.kind == "support_collapse_capacity_release_exit":
            account.last_support_collapse_exit_price = order.limit_price
            account.last_support_collapse_exit_ts_ms = tick.market_ts_ms
            if account.policy.enable_support_collapse_consistency_revision:
                account.last_support_collapse_entry_price = (
                    lot.entry_price or 0.0
                )
                account.support_collapse_extra_reentry_released = False
                account.support_collapse_base_short_released = False
        # A completed high-side execution ends the previous low-price sweep
        # episode. A later displayed discount is then a new causal opportunity.
        account.last_active_entry_price = None
        new_deficit = max(0.0, account.initial_inventory - account.inventory)
        old_deficit = max(0.0, account.initial_inventory - previous_inventory)
        added_deficit = max(0.0, new_deficit - old_deficit)
        support_collapse_turn = (
            account.policy.enable_support_collapse_consistency_revision
            and (
                order.kind == "support_collapse_capacity_release_exit"
                or lot.kind
                    == "support_collapse_capacity_redeploy_entry"
            )
        )
        if (
            (
                account.policy.enable_downtrend_turn_while_extra_inventory
                or support_collapse_turn
            )
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
            if (
                order.kind == "support_collapse_capacity_release_exit"
                and account.policy
                    .retire_recovered_support_collapse_pending_turn
            ):
                account.pending_support_collapse_turn_quantity += quantity
                account.pending_support_collapse_turn_sale_value += (
                    quantity * order.limit_price
                )
        if added_deficit > 1e-9:
            account.last_base_short_sale_ts_ms = tick.market_ts_ms
            account.base_short_rising_buy_sequence_bonds = 0.0
            account.replenishment_quantity += added_deficit
            account.replenishment_sale_value += added_deficit * order.limit_price
            if order.kind == "joint_causal_corridor_base_sell":
                previous_joint_bonds = (
                    account.joint_corridor_base_short_bonds
                )
                total_joint_bonds = previous_joint_bonds + added_deficit
                account.joint_corridor_base_short_sell_price = (
                    (
                        account.joint_corridor_base_short_sell_price
                        * previous_joint_bonds
                        + order.limit_price * added_deficit
                    )
                    / total_joint_bonds
                )
                account.joint_corridor_base_short_bonds = total_joint_bonds
                account.joint_corridor_base_short_buy_price = (
                    order.repeated_turn_replenishment_price
                )
                account.joint_corridor_base_short_support_floor = (
                    order.protective_bid_floor_price
                )
                account.joint_corridor_base_short_support_ceiling = (
                    order.protective_bid_ceiling_price
                )
                account.joint_corridor_base_short_support_bonds = (
                    order.protective_bid_entry_bonds
                )
                account.joint_corridor_base_short_ask_supply_bonds = (
                    order.joint_corridor_ask_supply_bonds
                )
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
        if self.fill_observer is not None:
            self.fill_observer(
                account, tick, order, "sell", quantity, reason,
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


class SharedCapitalPaperRuntime:
    """Persist one registered cash slot across the configured maker bonds."""

    def __init__(
        self, config: AppConfig, store: SQLiteStore, *,
        policy: MakerPolicyProfile,
    ) -> None:
        supported_model_ids = {
            SHARED_THOUSAND_POLICY_V01_CANDIDATE.model_id,
            SHARED_THOUSAND_POLICY_V013_CANDIDATE.model_id,
            SHARED_THOUSAND_POLICY_V016_R2_CANDIDATE.model_id,
        }
        if policy.model_id not in supported_model_ids:
            raise ValueError(
                f"unsupported shared-capital realtime model: {policy.model_id}"
            )

        self.source_config = config
        self.store = store
        self.policy = policy
        self.config = replace(
            config,
            maker_paper=replace(
                config.maker_paper,
                initial_inventory_bonds=0.0,
                additional_buying_capacity_bonds=1_000.0,
                maximum_inventory_bonds=1_000.0,
                initial_cash_cny=0.0,
                order_quantity_bonds=1_000.0,
                fill_modes=(),
                realtime_comparison_model_ids=(),
                super_windfall_enabled=False,
            ),
        )
        engine_class = MakerPaperEngine
        if policy.model_id == SHARED_THOUSAND_POLICY_V016_R2_CANDIDATE.model_id:
            from .shared_thousand_maker_v016_live import (
                ArrivalSharedAllocator, ArrivalSharedMakerEngine,
            )
            from .shared_thousand_maker_v013_research import AllocationParametersV03
            self.parameters = AllocationParametersV03()
            self.allocator_class = ArrivalSharedAllocator
            engine_class = ArrivalSharedMakerEngine
        elif policy.model_id == SHARED_THOUSAND_POLICY_V013_CANDIDATE.model_id:
            from .shared_thousand_maker_v013_research import (
                AllocationParametersV03,
                SharedThousandV013Allocator,
            )
            self.parameters = AllocationParametersV03()
            self.allocator_class = SharedThousandV013Allocator
        else:
            from .one_hand_maker_research import (
                AllocationParameters,
                OneHandSharedAllocator,
            )
            self.parameters = AllocationParameters(
                switch_minimum_score_advantage_cny=100.0,
                switch_relative_score_advantage=0.30,
                minimum_selection_dwell_seconds=60,
                unselected_tie_tolerance_cny=10.0,
            )
            self.allocator_class = OneHandSharedAllocator
        self.engines = {
            code: engine_class(
                self.config,
                store,
                bond_code=code,
                strategy_prefix=maker_strategy_prefix(config, code),
                priority_policy=policy,
                fill_modes=("priority",),
                include_windfall=False,
                strategy_ids_by_mode={
                    "priority": maker_comparison_strategy_id(
                        config, code, policy,
                    ),
                },
            )
            for code in configured_maker_bond_codes(config)
        }
        self.relevant_codes = {
            code
            for engine in self.engines.values()
            for code in (engine.bond_code, engine.stock_code)
        }
        self.market_date: str | None = None
        self.allocator: Any | None = None
        self._published_event_count = 0

    def _reset_date(self, market_date: str, *, clear: bool) -> None:
        for engine in self.engines.values():
            if clear:
                engine._clear_date(market_date)
            engine._start_date(market_date)
        self.allocator = self.allocator_class(
            self.engines,
            parameters=self.parameters,
            shared_capacity_bonds=1_000.0,
        )
        self.market_date = market_date
        self._published_event_count = 0

    def _merged_ticks(self, market_date: str) -> list[ReplayTick]:
        ticks_by_id: dict[int, ReplayTick] = {}
        for engine in self.engines.values():
            for tick in _load_ticks(
                self.store.connection,
                market_date,
                engine.bond_code,
                engine.stock_code,
                engine.parameters,
            ):
                ticks_by_id[tick.tick_id] = tick
        return sorted(
            ticks_by_id.values(),
            key=lambda tick: (tick.market_ts_ms, tick.tick_id),
        )

    def _publish_new_selection_events(self) -> None:
        if self.allocator is None:
            return
        new_events = self.allocator.events[self._published_event_count:]
        for event in new_events:
            self.store.app_event(
                "info",
                "shared_capital_selection_changed",
                "Shared-capital paper model changed its selected bond",
                {
                    "model_id": self.policy.model_id,
                    "market_date": self.market_date,
                    **event.public(),
                    "paper_only": True,
                },
            )
        self._published_event_count += len(new_events)

    def rebuild_date(self, market_date: date | str) -> None:
        date_text = (
            market_date.isoformat()
            if isinstance(market_date, date)
            else market_date
        )
        self._reset_date(date_text, clear=True)
        ticks = self._merged_ticks(date_text)
        for tick in ticks:
            self.allocator.on_replay_tick(tick)
            self._publish_new_selection_events()
        self.store.app_event(
            "info",
            "shared_capital_paper_rebuilt",
            "Shared-capital paper account rebuilt from recorded ticks",
            {
                "market_date": date_text,
                "model_id": self.policy.model_id,
                "bond_codes": list(self.engines),
                "ticks": len(ticks),
                "shared_capacity_bonds": 1_000.0,
                "paper_only": True,
            },
        )

    def on_recorded_tick(self, recorded: RecordedTick) -> None:
        if not recorded.is_new or recorded.tick.code not in self.relevant_codes:
            return
        tick = recorded.tick
        multiplier = (
            QMT_BONDS_PER_HAND if tick.code in self.engines else 1.0
        )
        self.on_replay_tick(ReplayTick(
            tick_id=recorded.tick_id,
            code=tick.code,
            market_ts_ms=tick.market_ts_ms,
            market_date=tick.market_datetime.date().isoformat(),
            market_time=tick.market_datetime.time().isoformat(
                timespec="milliseconds"
            ),
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
        ), persist=True)

    def on_replay_tick(self, tick: ReplayTick, *, persist: bool) -> None:
        if tick.code not in self.relevant_codes:
            return
        if self.market_date != tick.market_date or self.allocator is None:
            self._reset_date(tick.market_date, clear=False)
        # The live path is always persistent.  The argument is kept so the
        # portfolio presents one interface to independent and shared ledgers.
        self.allocator.on_replay_tick(tick)
        self._publish_new_selection_events()

    def runtime_summary(self) -> dict[str, Any]:
        allocator = self.allocator
        account_rows = [
            row
            for engine in self.engines.values()
            for row in engine.runtime_summary()["accounts"]
        ]
        aggregate_inventory = sum(
            account.inventory
            for engine in self.engines.values()
            for account in engine.accounts.values()
        )
        for row in account_rows:
            row.update({
                "shared_capital": True,
                "shared_capacity_bonds": 1_000.0,
                "shared_initial_cash_cny": round(
                    allocator.initial_cash_cny if allocator else 0.0, 2,
                ),
                "shared_cash_cny": round(
                    allocator.shared_cash_cny if allocator else 0.0, 2,
                ),
                "shared_selected_bond_code": (
                    allocator.selected_code if allocator else None
                ),
                "shared_aggregate_inventory_bonds": round(
                    aggregate_inventory, 1,
                ),
            })
        return {
            "enabled": self.config.maker_paper.enabled,
            "bond_codes": list(self.engines),
            "market_date": self.market_date,
            "fills_this_run": sum(
                engine.fills_this_run for engine in self.engines.values()
            ),
            "accounts": account_rows,
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
        comparison_policies = realtime_comparison_policies(config)
        shared_model_ids = {
            SHARED_THOUSAND_POLICY_V01_CANDIDATE.model_id,
            SHARED_THOUSAND_POLICY_V013_CANDIDATE.model_id,
            SHARED_THOUSAND_POLICY_V016_R2_CANDIDATE.model_id,
            DADAO_POLICY_V01_R2_CANDIDATE.model_id,
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
                for policy in comparison_policies
                if policy.model_id not in shared_model_ids
            )
            for code in configured_maker_bond_codes(config)
        }
        from .shared_thousand_maker_v016_live import ArrivalSharedPaperRuntime
        from .dadao_maker_live import DadaoPaperRuntime
        self.shared_capital_runtimes = tuple(
            (DadaoPaperRuntime
             if policy.model_id == DADAO_POLICY_V01_R2_CANDIDATE.model_id
             else ArrivalSharedPaperRuntime
             if policy.model_id == SHARED_THOUSAND_POLICY_V016_R2_CANDIDATE.model_id
             else SharedCapitalPaperRuntime)(config, store, policy=policy)
            for policy in comparison_policies
            if policy.model_id in shared_model_ids
        )

    def _independent_engines(self) -> tuple[MakerPaperEngine, ...]:
        return tuple(self.engines.values()) + tuple(
            engine
            for engines in self.comparison_engines.values()
            for engine in engines
        )

    def _all_engines(self) -> tuple[MakerPaperEngine, ...]:
        return self._independent_engines() + tuple(
            engine
            for runtime in self.shared_capital_runtimes
            for engine in runtime.engines.values()
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
        for engine in self._independent_engines():
            engine.rebuild_date(market_date, clear=True)
        for runtime in self.shared_capital_runtimes:
            runtime.rebuild_date(market_date)

    def on_recorded_tick(self, recorded: RecordedTick) -> None:
        code = recorded.tick.code
        matching_engines = tuple(
            engine for engine in self._independent_engines()
            if engine.stock_code == code or engine.bond_code == code
        )
        for engine in matching_engines:
            engine.on_recorded_tick(recorded)
        for runtime in self.shared_capital_runtimes:
            runtime.on_recorded_tick(recorded)

    def on_replay_tick(
        self, tick: ReplayTick, *, persist: bool,
        received_ts_ns: int | None = None,
    ) -> None:
        matching_engines = tuple(
            engine for engine in self._independent_engines()
            if engine.stock_code == tick.code or engine.bond_code == tick.code
        )
        for engine in matching_engines:
            engine.on_replay_tick(
                tick, persist=persist, received_ts_ns=received_ts_ns
            )
        for runtime in self.shared_capital_runtimes:
            runtime.on_replay_tick(tick, persist=persist)

    def runtime_summary(self) -> dict[str, Any]:
        summaries = [
            engine.runtime_summary() for engine in self._independent_engines()
        ] + [
            runtime.runtime_summary()
            for runtime in self.shared_capital_runtimes
        ]
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
