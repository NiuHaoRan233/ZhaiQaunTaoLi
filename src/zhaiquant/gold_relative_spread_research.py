"""Percentage entry gates on frozen paired gold policies; offline research only."""
from dataclasses import replace
from . import gold_two_mode_research as parent

FAMILY = 'probe_gold_relative_spread_20260914_v1'
THRESHOLDS = (100, 150, 200)


def qualifies(bid, ask, bps):
    """Exact inclusive (ask-bid)/mid test; integer cents and basis points."""
    return bid > 0 and ask > bid and 2 * (ask-bid) * 10000 >= bps * (ask+bid)


class Account(parent.Account):
    def __init__(self, code, policy, strike, direction, bps, through=False, capital=250000):
        if bps not in THRESHOLDS:
            raise ValueError('Expected 100, 150 or 200 basis points')
        super().__init__(code, policy, strike, direction, through, capital)
        self.bps = bps
        # Remove BOTH the candidate floor and inherited quote-control safety floor.
        # issue() below enforces the percentage gate before the inherited throttle.
        self.rules = replace(self.rules, min_spread_ticks=0)
        self.spread = 0
        self.model = self.model.replace(parent.FAMILY, FAMILY + f'_bps{bps}')

    def candidate(self, e, feature):
        # Called only after the previous aggregate interval's fills are settled.
        if not qualifies(e.bid, e.ask, self.bps):
            return None, 'relative_spread'
        return super().candidate(e, feature)

    def issue(self, ts, side, price, reason, e, feature=None):
        if reason == 'entry' and not qualifies(e.bid, e.ask, self.bps):
            self.cancel(ts, 'relative_spread')
            return
        super().issue(ts, side, price, reason, e, feature)

    def result(self):
        r = super().result()
        r['summary'].pop('spread_ticks', None)
        r['summary'].update(relative_spread_bps=self.bps,
            relative_spread_formula='2*(ask-bid)/(ask+bid)',
            absolute_entry_spread_floor_ticks=None, prototype_family=parent.FAMILY)
        return r
