"""Offline v0.15: causal exit liquidity and first-entry slot opportunity cost.

These are transparent ranking proxies, not statistically calibrated forecasts.
Native v0.14 orders/exits and every older allocator remain unchanged.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace

from .maker_paper import SHARED_THOUSAND_POLICY_V015_CANDIDATE as POLICY
from .one_hand_maker_research import SHARED_THOUSAND_BONDS, replay_one_hand_day
from .shared_thousand_maker_v013_research import SharedThousandV013Allocator
from .shared_thousand_maker_v03_research import AllocationParametersV03, AllocationScoreV03
from .shared_thousand_maker_v014_research import CausalObserver, risk_metrics

MODEL_ID = POLICY.model_id
PARENT_MODEL_ID = POLICY.parent_model_id


@dataclass(frozen=True)
class AllocationParametersV015(AllocationParametersV03):
    exit_flow_half_life_seconds: float = 150.0
    session_exit_background_fraction: float = 0.25
    exit_flow_prior_capacity_fraction: float = 0.10
    waiting_memory_seconds: int = 300
    waiting_half_life_seconds: float = 150.0


@dataclass(frozen=True)
class AllocationScoreV015(AllocationScoreV03):
    parent_score_cny: float
    parent_exit_probability: float
    parent_expected_lock_seconds: float
    weighted_exit_buy_bonds: float
    exit_evidence_floor_v015: float
    session_exit_freshness_factor: float
    flow_lock_seconds: float
    pre_waiting_score_cny: float
    peer_waiting_value_cny: float = 0.0
    slot_waiting_cost_cny: float = 0.0


def liquidity_score_v015(*, score, engine, tick, parameters, quantity,
                        shared_capacity_bonds=SHARED_THOUSAND_BONDS):
    """Pure adjustment: never calls native decision logic or mutates an order."""
    p = parameters
    floor = max(score.entry_price, score.exit_price - p.exit_flow_price_band)
    weighted = sum(
        event.bonds * 2 ** (-(tick.market_ts_ms - event.market_ts_ms)
                           / (1_000 * p.exit_flow_half_life_seconds))
        for event in engine.analyzer.trade_evidence
        if 0 <= tick.market_ts_ms - event.market_ts_ms <= p.recent_flow_window_seconds * 1_000
        and event.side == 'buy' and event.price + 1e-9 >= floor
    )
    scale = max(p.minimum_evidence_scale_bonds,
                p.evidence_multiple * max(quantity, shared_capacity_bonds))
    inner_quality = min(1.0, score.inner_bid_support_bonds / scale)
    local_exit = min(score.exit_probability_without_session,
                     0.15 + 0.65 * weighted / (weighted + scale) + 0.20 * inner_quality)
    freshness = (p.session_exit_background_fraction
                 + (1 - p.session_exit_background_fraction)
                 * weighted / (weighted + shared_capacity_bonds))
    exit_bonus = max(0.0, score.session_exit_probability_bonus) * freshness
    exit_proxy = min(score.exit_probability_proxy, local_exit + exit_bonus)
    flow_lock = p.recent_flow_window_seconds * quantity / max(
        weighted, p.exit_flow_prior_capacity_fraction * shared_capacity_bonds)
    lock = min(p.maximum_expected_lock_seconds, max(
        p.minimum_expected_lock_seconds, flow_lock,
        p.base_expected_lock_seconds / max(exit_proxy, 0.05)))
    terminal = min(1.0, score.remaining_session_seconds / max(lock, 1.0))
    gain = score.gross_edge_per_bond * score.entry_probability_proxy * exit_proxy * terminal
    downside = score.downside_distance_per_bond * score.downside_risk_fraction
    downside = (downside + score.active_adverse_selection_penalty_per_bond
                + downside * p.terminal_lock_penalty_weight * (1 - terminal))
    raw = quantity * (gain - downside)
    value = raw * min(1.0, p.capital_time_reference_seconds / max(lock, 1.0))
    values = asdict(score)
    values.update(remaining_bonds=quantity, exit_probability_proxy=exit_proxy,
                  exit_probability_without_session=local_exit,
                  session_exit_probability_bonus=exit_proxy-local_exit,
                  expected_lock_seconds=lock, terminal_time_factor=terminal,
                  expected_gain_per_bond=gain, expected_downside_per_bond=downside,
                  raw_expected_value_cny=raw, capital_time_score_cny=value)
    return AllocationScoreV015(**values, parent_score_cny=score.score_cny,
        parent_exit_probability=score.exit_probability_proxy,
        parent_expected_lock_seconds=score.expected_lock_seconds,
        weighted_exit_buy_bonds=weighted, exit_evidence_floor_v015=floor,
        session_exit_freshness_factor=freshness, flow_lock_seconds=flow_lock,
        pre_waiting_score_cny=value)


class SharedThousandV015Allocator(SharedThousandV013Allocator):
    def __init__(self, engines, *, parameters=None, **kwargs):
        super().__init__(engines, parameters=parameters or AllocationParametersV015(), **kwargs)
        self.waiting_opportunities: dict[str, dict[int, float]] = {}
        self.v015_counts = Counter()
        self.v015_score_audit = []
        self.v015_fill_checks = []
        self._v015_audit_keys = set()
        self._v015_last_choice_scores = {}

    def _score(self, code, order, *, active_fill):
        previous_shadow = self.shadow_scores.get(code)
        base = super()._score(code, order, active_fill=active_fill)
        if base is None:
            return None
        tick = self.last_bond_ticks[code]
        fill_quantity = self._active_fill_quantities.get((code, order.db_id))
        quantity = min(order.remaining, fill_quantity) if fill_quantity is not None else order.remaining
        score = liquidity_score_v015(score=base, engine=self.engines[code], tick=tick,
            parameters=self.parameters, quantity=quantity,
            shared_capacity_bonds=self.shared_capacity_bonds)
        if not active_fill and fill_quantity is None:
            self.shadow_scores[code] = (tick.market_ts_ms, score)
        elif not active_fill:
            # A partial-fill check is not a new resting native intention.
            if previous_shadow is None:
                self.shadow_scores.pop(code, None)
            else:
                self.shadow_scores[code] = previous_shadow
        # Only real native quotes, not shadows/partial-fill guesses, teach the
        # waiting proxy. Full active offers are also legal observed opportunities.
        if fill_quantity is None or active_fill:
            memory = self.waiting_opportunities.setdefault(code, {})
            memory[tick.market_ts_ms] = max(0.0, score.pre_waiting_score_cny)
            cutoff = tick.market_ts_ms - self.parameters.waiting_memory_seconds * 1_000
            for ts in tuple(memory):
                if ts < cutoff:
                    del memory[ts]
        return score

    def _peer_waiting_value(self, code, now_ms):
        p = self.parameters
        return max((value * 2 ** (-(now_ms-ts) / (1_000*p.waiting_half_life_seconds))
                    for peer, memory in self.waiting_opportunities.items() if peer != code
                    for ts, value in memory.items()
                    if 0 <= now_ms-ts <= p.waiting_memory_seconds*1_000), default=0.0)

    def _choose(self, scores, *, market_ts_ms):
        held = bool(self._holdings())
        for code, score in tuple(scores.items()):
            if not isinstance(score, AllocationScoreV015):
                raise TypeError('v0.15 cannot compare an unadjusted shadow score')
            peer = 0.0 if held else self._peer_waiting_value(code, market_ts_ms)
            unused = max(0.0, 1-score.remaining_bonds/self.shared_capacity_bonds)
            charge = unused * peer * min(1.0, score.expected_lock_seconds
                                         / self.parameters.capital_time_reference_seconds)
            adjusted = replace(score, peer_waiting_value_cny=peer, slot_waiting_cost_cny=charge,
                               capital_time_score_cny=score.pre_waiting_score_cny-charge)
            scores[code] = adjusted  # Native selection event and decision audit agree.
            key = (code, market_ts_ms, score.order_id, score.active_fill,
                   score.remaining_bonds, score.shadow_intent)
            if key not in self._v015_audit_keys:
                self._v015_audit_keys.add(key)
                self.v015_counts['scores'] += 1
                if charge > 1e-9:
                    self.v015_counts['waiting_charge_scores'] += 1
                if score.pre_waiting_score_cny > 0 and adjusted.score_cny <= 0:
                    self.v015_counts['cash_after_waiting_charge'] += 1
                if len(self.v015_score_audit) < self.parameters.challenge_audit_limit:
                    self.v015_score_audit.append(dict(market_ts_ms=market_ts_ms,
                                                     score=adjusted.public()))
        self._v015_last_choice_scores = dict(scores)
        return super()._choose(scores, market_ts_ms=market_ts_ms)

    def _allow_buy_fill(self, code, account, tick, order, quantity, kind, reason):
        self._v015_last_choice_scores = {}
        allowed = super()._allow_buy_fill(code, account, tick, order, quantity, kind, reason)
        score = self._v015_last_choice_scores.get(code)
        self.v015_fill_checks.append(dict(code=code, market_ts_ms=tick.market_ts_ms,
            market_time=tick.market_time, quantity=quantity, allowed=allowed,
            score=score.public() if score is not None else None))
        return allowed

    def research_metrics(self):
        inherited = super().research_metrics()
        return dict(**inherited, allocation_v015_parameters=asdict(self.parameters),
                    allocation_v015_metrics=dict(self.v015_counts),
                    allocation_v015_score_audit=self.v015_score_audit,
                    allocation_v015_fill_checks=self.v015_fill_checks,
                    inherited_score_audits_are_pre_v015=True)


def replay_shared_thousand_v015_day(config, *, market_date, cutoff_time=None, observe=False):
    observer = CausalObserver() if observe else None
    result = replay_one_hand_day(config, market_date=market_date, priority_policy=POLICY,
        parameters=AllocationParametersV015(), shared_capacity_bonds=SHARED_THOUSAND_BONDS,
        allocator_class=SharedThousandV015Allocator, cutoff_time=cutoff_time, observer=observer)
    result['cutoff_time'] = cutoff_time
    result['risk_metrics'] = risk_metrics(result)
    if observer is not None:
        result['frames'] = observer.frames
    return result
