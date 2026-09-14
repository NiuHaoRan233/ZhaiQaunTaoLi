"""Bounded next ablation: combine the observed long-value mechanism with risk gates."""
from .gold_direction_timing_research import Account as Parent

FAMILY='probe_gold_long_refinement_20260913_v3'
PROFILES={
    'fair_long_trend':dict(direction='long',value=True,trend=True,proactive=False,patient=True),
    'fair_long_risk':dict(direction='long',value=True,trend=False,proactive=True,patient=True),
    'fair_long_trend_risk':dict(direction='long',value=True,trend=True,proactive=True,patient=True),
    'fair_long_top':dict(direction='long',value=True,trend=False,proactive=True,patient=False),
}


class Account(Parent):
    def __init__(self,code,profile,settlement,strike,tick=2000):
        if profile not in PROFILES:raise ValueError(profile)
        super().__init__(code,'fair_long',settlement,strike,tick)
        self.profile=profile;self.cfg=PROFILES[profile]
        self.model=f'{FAMILY}_{profile}_{settlement}_{code}_independent_250000'
