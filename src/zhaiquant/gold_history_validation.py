"""Date/expiry adapter for immutable gold research engines; no broker interface.

Only clock coordinates are normalized. Prices, cash, evidence and event ordering
are unchanged. Every public result restores the actual source timestamps.
"""
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from . import gold_direction_research as g
from . import gold_state_research as selector
from .gold_aligned_value_research import ValueState as ParentValue
from .commodity_dadao_research import load_frame

FAMILY = 'probe_gold_history_20260913_v1'
TZ = ZoneInfo('Asia/Shanghai')
DAY = 86400000


def timestamp(date, clock='090000'):
    return int(datetime.strptime(date+clock, '%Y%m%d%H%M%S').replace(tzinfo=TZ).timestamp()*1000)


class Clock:
    def __init__(self, date):
        self.date = date
        self.start = timestamp(date)
        self.shift = g.START-self.start

    def frame(self, frame):
        result = frame.copy()
        result['time'] = result['time']+self.shift
        return result

    def future_state(self, frame):
        return selector.future_state(self.frame(frame))

    def metrics(self, frame, detail, underlying):
        terms = dict(detail)
        expiry = datetime.strptime(detail['ExpireDate'], '%Y%m%d')
        terms['ExpireDate'] = (expiry+timedelta(milliseconds=self.shift)).strftime('%Y%m%d')
        return selector.state_metrics(self.frame(frame), terms, underlying)

    def inputs(self, options, futures, code, detail):
        if detail['OptionType'] != 0 or float(detail['OptUnit']) != 1000 or float(detail['PriceTick']) != .02:
            raise ValueError('Adapter supports gold calls with multiplier1000/tick0.02 only')
        options = options[(options.time >= self.start) & (options.time < self.start+21600000)]
        es, flags, meta = load_frame(options, code=code, date=self.date, detail=detail)
        es = [replace(e, ts=e.ts+self.shift, previous_ts=e.previous_ts+self.shift,
                      quantity=min(e.quantity, 1)) for e in es]
        fs = []
        for x in self.frame(futures).sort_values('time', kind='stable').drop_duplicates('time', keep='last').itertuples():
            session = next((i for i, (lo, hi) in enumerate(zip(g.SESSION_STARTS, g.BOUNDARIES)) if lo <= x.time < hi), None)
            if session is None or not (0 < x.bidPrice[0] <= x.askPrice[0] and min(x.bidVol[0], x.askVol[0]) > 0):
                continue
            fs.append(g.Future(int(x.time), int(x.time), session, (x.bidPrice[0]+x.askPrice[0])/2))
        return es, fs, flags, meta

    def restore(self, value, key=''):
        if isinstance(value, dict):
            return {k:self.restore(v, k) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            if key in ('curve', 'funding_demands'):
                return [[row[0]-self.shift, *row[1:]] for row in value]
            return [self.restore(v) for v in value]
        if value is not None and (key == 'ts' or key.endswith('_ts') or key == 'selection_input_end_exclusive'):
            return value-self.shift
        return value


class ValueState(ParentValue):
    def __init__(self, strike, expiry, clock):
        super().__init__(strike, 'fast_lower')
        self.expiry_ts = timestamp(expiry, '150000')+clock.shift

    def maturity(self, ts):
        return max(1/365, (self.expiry_ts-ts)/(365*DAY))


def timeline(es, fs, strike, expiry, clock, cut=None):
    value = ValueState(strike, expiry, clock)
    events = sorted([(e.ts,0,e) for e in es]+[(f.ts,1,f) for f in fs]+[(b,-1,b) for b in g.BOUNDARIES], key=lambda x:x[:2])
    rows = []
    for ts, kind, event in events:
        if cut is not None and ts > cut:
            break
        if kind == -1:
            rows.append((kind,event,None,None,None)); continue
        if kind == 1:
            value.on_future(event)
            low = value.cancellation_feature(ts)
        else:
            value.new_session(event.session)
            low = value.feature(ts)
        mid = high = None
        if low:
            high = dict(low); mid = dict(low)
            for feat, vol, name in ((high,max(value.vol,value.fast_vol),'fast_upper'), (mid,value.vol,'mid')):
                fair, delta = g.black_call(value.future.mid, strike, value.maturity(ts), vol)
                feat.update(fair_cents=fair*100000, delta=delta, vol=vol, reference_kind=name)
            for feat in (low, high, mid):
                for seconds in (10,60):
                    old = next((x for x in reversed(value.history) if x.source_ts <= value.future.source_ts-seconds*1000),None)
                    feat[f'move{seconds}_cents'] = feat['delta']*(value.future.mid-old.mid)*100000 if old and value.future.source_ts-seconds*1000-old.source_ts <= 2000 else None
        rows.append((kind,event,{1:low,-1:high},mid,value.future))
        if kind == 0:
            value.observe_option(event)
    return rows
