"""Observe original calls exactly once; preserve both positive and negative cases."""
import argparse
import json
from pathlib import Path

from zhaiquant.config import load_config
from zhaiquant.one_hand_maker_research import replay_one_hand_day, _only_account
from zhaiquant.maker_paper import MakerPaperEngine, SHARED_THOUSAND_POLICY_V014_CANDIDATE
from zhaiquant.shared_thousand_maker_v013_research import SharedThousandV013Allocator
from zhaiquant.shared_thousand_maker_v014_research import economic_fills, compact_order
from zhaiquant.shared_thousand_maker_v016_research import POLICY, CausalSharedMakerEngine, SharedThousandV016Allocator


class Audit:
    def __init__(self, windows):
        self.windows = windows
        self.trace = []

    def included(self, tick):
        return any(a <= tick.market_time[:8] <= b for a, b in self.windows)

    def __call__(self, allocator, tick):
        if tick is None:
            score = allocator._score
            allow = allocator._allow_buy_fill
            choose = allocator._choose
            def observed_score(code, order, *, active_fill):
                value = score(code, order, active_fill=active_fill)
                current = allocator.last_bond_ticks.get(code)
                if current and self.included(current):
                    self.trace.append(dict(event='score', time=current.market_time, code=code,
                        order=compact_order(order), active=active_fill,
                        value=value.public() if value else None))
                return value
            def observed_allow(code, account, tick, order, quantity, kind, reason):
                before = dict(selected=allocator.selected_code, cash=allocator.shared_cash_cny,
                    held=allocator._holdings(), order=compact_order(order))
                result = allow(code, account, tick, order, quantity, kind, reason)
                if self.included(tick):
                    self.trace.append(dict(event='settlement', time=tick.market_time, code=code,
                        before=before, quantity=quantity, reason=reason, allowed=result))
                return result
            def observed_choose(scores, *, market_ts_ms):
                result = choose(scores, market_ts_ms=market_ts_ms)
                self.latest_choice = {code: dict(value=s.score_cny, shadow=s.shadow_intent)
                                      for code, s in scores.items()}
                return result
            allocator._score, allocator._allow_buy_fill, allocator._choose = observed_score, observed_allow, observed_choose
            return
        if tick.code not in allocator.engines or not self.included(tick):
            return
        account = _only_account(allocator.engines[tick.code])
        self.trace.append(dict(event='after', time=tick.market_time, code=tick.code,
            tick_id=tick.tick_id, bids=tick.bids, asks=tick.asks,
            last=tick.last_price, trade=tick.trade_bonds, side=tick.inferred_side,
            order=compact_order(account.buy_order) if account else None,
            selected=allocator.selected_code, held=allocator._holdings(),
            choice=getattr(self, 'latest_choice', {})))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', required=True)
    p.add_argument('--worst-only', action='store_true')
    args = p.parse_args()
    path = Path(args.output)
    if path.exists():
        raise FileExistsError(path)
    result = {}
    cases = (
        ('2026-08-07', 'shared_v016_matrix_20260804_20260904.json', [('09:50:35','09:52:00'),('11:16:20','11:17:00')]),
        ('2026-09-07', 'shared_v016_target_20260907_144500.json', [('09:30:00','09:33:00'),('10:20:40','10:21:15')]),
    )
    if args.worst_only:
        cases = (('2026-08-31', 'shared_v016_matrix_20260804_20260904.json', [('09:42:35','09:44:20')]),)
    for day, filename, windows in cases:
        report = json.loads((Path('output/research')/filename).read_text(encoding='utf-8'))
        saved = next(c for c in report['cells'] if c['market_date'] == day)
        result[day] = {}
        for label, policy, engine, allocator in (
            ('parent', SHARED_THOUSAND_POLICY_V014_CANDIDATE, MakerPaperEngine, SharedThousandV013Allocator),
            ('candidate', POLICY, CausalSharedMakerEngine, SharedThousandV016Allocator),
        ):
            observer = Audit(windows)
            cell = replay_one_hand_day(load_config('config.toml'), market_date=day,
                priority_policy=policy, engine_class=engine, allocator_class=allocator,
                shared_capacity_bonds=1000, cutoff_time=report['cutoff_time'], observer=observer)
            assert economic_fills(cell) == economic_fills(saved[label]), (day, label, 'fills')
            assert cell['trading_pnl'] == saved[label]['trading_pnl'], (day, label, 'pnl')
            assert cell['order_counts'] == saved[label]['order_counts'], (day, label, 'orders')
            result[day][label] = dict(observation_exact=True, trace=observer.trace)
            print(day, label, 'exact', flush=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
