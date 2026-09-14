"""v0.15 r2: preserve causal full-sweep demand and passive order plans."""
from dataclasses import asdict, dataclass, replace

from .maker_paper import SHARED_THOUSAND_POLICY_V015_R2_CANDIDATE as POLICY
from .one_hand_maker_research import _only_account
from .shared_thousand_maker_v02_research import SharedThousandV02Allocator
from .shared_thousand_maker_v013_research import score_buy_intent_v013
from .shared_thousand_maker_v015_research import AllocationScoreV015, SharedThousandV015Allocator

MODEL_ID = POLICY.model_id
PARENT_MODEL_ID = POLICY.parent_model_id


@dataclass(frozen=True)
class AllocationScoreV015R2(AllocationScoreV015):
    planned_remaining_bonds: float = 0.0
    full_consumed_live_attack: bool = False


def full_consumed_live_attack(engine, tick, order, quantity, active_fill, capacity):
    if not active_fill or order.kind not in {'sweep_tail', 'deep_discount_sweep'}:
        return False
    if abs(tick.ask1-order.limit_price) > 1e-9:
        return False
    low = sum(bonds for price, bonds in tick.asks if bonds > 0 and price <= order.limit_price+1e-9)
    if low <= 0 or quantity+1e-9 < low:
        return False
    if not any(bonds > 0 and price > order.limit_price+1e-9 for price, bonds in tick.asks):
        return False
    return sum(e.bonds for e in engine.analyzer.trade_evidence
        if e.market_ts_ms == tick.market_ts_ms and e.side == 'buy'
        and abs(e.price-order.limit_price) <= 0.015+1e-9) >= capacity-1e-9


class SharedThousandV015R2Allocator(SharedThousandV015Allocator):
    def _score(self, code, order, *, active_fill):
        score = super()._score(code, order, active_fill=active_fill)
        if score is None:
            return None
        tick = self.last_bond_ticks[code]
        quantity = self._active_fill_quantities.get((code, order.db_id))
        full_attack = full_consumed_live_attack(self.engines[code], tick, order,
            score.remaining_bonds, active_fill, self.shared_capacity_bonds)
        values = asdict(score)
        if full_attack:
            # Pure score calculation, not an extra native decision/entry call.
            parent = score_buy_intent_v013(bond_code=code, engine=self.engines[code],
                account=_only_account(self.engines[code]), order=order, tick=tick,
                parameters=self.parameters, active_fill=active_fill, fill_quantity=quantity,
                shared_capacity_bonds=self.shared_capacity_bonds)
            for name in ('exit_probability_proxy', 'exit_probability_without_session',
                         'session_exit_probability_bonus', 'expected_lock_seconds',
                         'terminal_time_factor', 'expected_gain_per_bond', 'expected_downside_per_bond'):
                values[name] = getattr(parent, name)
            ratio = score.remaining_bonds/max(parent.remaining_bonds, 1e-9)
            values.update(raw_expected_value_cny=parent.raw_expected_value_cny*ratio,
                          capital_time_score_cny=parent.score_cny*ratio,
                          pre_waiting_score_cny=parent.score_cny*ratio)
        revised = AllocationScoreV015R2(**values,
            planned_remaining_bonds=score.remaining_bonds if active_fill else order.remaining,
            full_consumed_live_attack=full_attack)
        if not active_fill and quantity is None:
            self.shadow_scores[code] = (tick.market_ts_ms, revised)
        if quantity is None or active_fill:
            self.waiting_opportunities[code][tick.market_ts_ms] = max(0.0, revised.pre_waiting_score_cny)
        return revised

    def _choose(self, scores, *, market_ts_ms):
        held = bool(self._holdings())
        for code, score in tuple(scores.items()):
            if not isinstance(score, AllocationScoreV015R2):
                raise TypeError('r2 cannot compare an unadjusted shadow score')
            peer = 0.0 if held else self._peer_waiting_value(code, market_ts_ms)
            unused = max(0.0, 1-score.planned_remaining_bonds/self.shared_capacity_bonds)
            charge = unused*peer*min(1.0, score.expected_lock_seconds/self.parameters.capital_time_reference_seconds)
            adjusted = replace(score, peer_waiting_value_cny=peer, slot_waiting_cost_cny=charge,
                               capital_time_score_cny=score.pre_waiting_score_cny-charge)
            scores[code] = adjusted
            key = (code, market_ts_ms, score.order_id, score.active_fill,
                   score.remaining_bonds, score.shadow_intent)
            if key not in self._v015_audit_keys:
                self._v015_audit_keys.add(key)
                self.v015_counts['scores'] += 1
                if charge > 1e-9:
                    self.v015_counts['waiting_charge_scores'] += 1
                if score.pre_waiting_score_cny > 0 and adjusted.score_cny <= 0:
                    self.v015_counts['cash_after_waiting_charge'] += 1
                if score.full_consumed_live_attack:
                    self.v015_counts['full_consumed_live_attack_scores'] += 1
                if len(self.v015_score_audit) < self.parameters.challenge_audit_limit:
                    self.v015_score_audit.append(dict(market_ts_ms=market_ts_ms, score=adjusted.public()))
        self._v015_last_choice_scores = dict(scores)
        return SharedThousandV02Allocator._choose(self, scores, market_ts_ms=market_ts_ms)
