"""Explicit combined-removal candidates; do not edit frozen ladder v1."""
from dataclasses import replace
from . import gold_rule_ladder_research as parent

FAMILY='probe_gold_rule_simplification_20260913_v2'
FULL=parent.cases()['s10']['rules']
CASES={
    'core':dict(label='精简：价差＋保守估值＋有限成本保护',rules=replace(FULL,flow=False,second=False,gap=False)),
    'core_no_adverse':dict(label='精简再删逆向提前释放',rules=replace(FULL,flow=False,second=False,gap=False,adverse_release=False)),
    'simple_patient':dict(label='简单：价差＋有限成本保护，无估值',rules=parent.Rules(improve=True,net_spread=True,patient=True,adverse_release=True)),
    'simple_timeout':dict(label='简单：价差＋300秒成本保护，无估值',rules=parent.Rules(improve=True,net_spread=True,patient=True)),
    **{f'spread{n}':dict(label=f'仅改善报价＋原始价差≥{n}跳',rules=parent.Rules(improve=True,min_spread_ticks=n)) for n in (3,4,5,6,8,10)},
}


class Account(parent.Account):
    def __init__(self,code,case,direction,strike,capital_cny=250000,through=False):
        super().__init__(code,'s10',direction,strike,capital_cny,through)
        self.rules=CASES[case]['rules'];self.case=case
        self.model=f'{FAMILY}_{case}_{"long" if direction==1 else "short"}_market_{code}_capital{self.initial}_through{int(through)}'
