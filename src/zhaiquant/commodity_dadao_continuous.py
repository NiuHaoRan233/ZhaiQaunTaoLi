"""Separate continuous-cash/position Dadao research identity, daytime quotes only."""
from dataclasses import replace
from collections import defaultdict
from . import commodity_dadao_research as day

FAMILY='probe_commodity_dadao_20260912_cont_v1'


def run(day_inputs, *, code, mode, fee_cents):
    """Carry inventory/cash; exchange breaks cancel orders, never positions.

    day_inputs contains sorted (events, unit_evidence, meta) tuples. Missing
    sessions cannot be reconstructed; caller reports gaps instead of filling them.
    """
    if not day_inputs:raise ValueError('Empty input')
    meta=day_inputs[0][2]
    unit,tick=meta['unit'],meta['tick_cents']
    events=[];flags=[];day_by_ts={}
    for ordinal,(es,us,m) in enumerate(day_inputs):
        if m['unit']!=unit or m['tick_cents']!=tick:raise ValueError('Contract terms changed')
        for e in es:
            events.append(replace(e,session=ordinal*10+e.session));day_by_ts[e.ts]=m['date']
        flags.extend(us)
    period=day_inputs[0][2]['date']+'_'+day_inputs[-1][2]['date']
    result=day.run(events,flags,code=code,date=period,mode=mode,fee_cents=fee_cents,
        initial_cents=meta['initial_cents'],tick_cents=tick)
    model=f'{FAMILY}_{mode}_q1_f{fee_cents}'
    s=result['summary'];s.update(model_id=model,account_id=model+'_'+code+'_'+period,
        days=len(day_inputs),first_date=day_inputs[0][2]['date'],last_date=day_inputs[-1][2]['date'],
        daytime_only=True,continuous_cash_and_inventory=True)
    for o in result['orders']:o['model_id']=model
    for f in result['fills']:
        f['model_id']=model;f['date']=day_by_ts[f['ts']]
    day.audit_result(result)
    endings={}
    for ts,pnl,inventory in result['curve']:endings[day_by_ts[ts]]=(ts,pnl,inventory)
    daily=[];previous=0
    fills=defaultdict(list)
    for f in result['fills']:fills[f['date']].append(f)
    for date,(ts,pnl,inventory) in endings.items():
        daily.append(dict(date=date,pnl_cny=(pnl-previous)/100,cumulative_pnl_cny=pnl/100,
            end_inventory=inventory,fill_count=len(fills[date]),last_ts=ts))
        previous=pnl
    assert abs(sum(d['pnl_cny'] for d in daily)-s['pnl_cny'])<1e-6
    s.update(positive_days=sum(d['pnl_cny']>0 for d in daily),negative_days=sum(d['pnl_cny']<0 for d in daily),
        zero_trade_days=sum(d['fill_count']==0 for d in daily),tail_days=sum(d['end_inventory']>0 for d in daily))
    result['daily']=daily
    return result
