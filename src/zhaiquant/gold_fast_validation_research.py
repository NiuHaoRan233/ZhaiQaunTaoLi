"""Validate the fixed fast/slow lower-value mechanism, not optimize its parameters."""
from math import expm1
from . import gold_aligned_value_research as parent

FAMILY='probe_gold_fast_validation_20260913_v5'


class ValueState(parent.ValueState):
    def __init__(self,strike,fast_seconds):
        super().__init__(strike,'fast_lower');self.fast_seconds=fast_seconds

    def on_future(self,f):
        previous=self.fast_vol;previous_sample=self.sample_ts;session=self.session;n=len(self.calibrations)
        super().on_future(f)
        if session!=f.session:previous=None;previous_sample=None
        for c in self.calibrations[n:]:
            if previous is None:previous=c['raw_iv']
            else:previous+=-expm1(-(c['option_source_ts']-previous_sample)/(self.fast_seconds*1000))*(c['raw_iv']-previous)
            previous_sample=c['option_source_ts'];c['fast_iv']=previous
        if len(self.calibrations)>n:self.fast_vol=previous


class Account(parent.Account):
    def __init__(self,code,settlement,strike,fast_seconds=10,through=False):
        if fast_seconds not in (5,10,20):raise ValueError('Unregistered robustness case')
        super().__init__(code,'aligned_fast_lower',settlement,strike)
        self.value=ValueState(strike,fast_seconds);self.fast_seconds=fast_seconds;self.through=through;self.touch_rejections=0
        self.model=f'{FAMILY}_fast{fast_seconds}_through{int(through)}_{settlement}_{code}_independent_250000'

    def fill(self,ts,price,side,kind,e=None,feature=None):
        if self.through and kind=='passive' and e.last==price:
            self.touch_rejections+=1;return
        return super().fill(ts,price,side,kind,e,feature)

    def result(self):
        r=super().result();r['summary'].update(fast_seconds=self.fast_seconds,strict_through=self.through,
            rejected_touch_only_attempts=self.touch_rejections)
        return r
