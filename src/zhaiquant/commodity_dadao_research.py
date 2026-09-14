"""Immutable v1 commodity adaptation of the archived fixed-contract Dadao loop.

Read-only research. One contract maximum. Last-contract evidence is NOT an
exchange aggressor flag or a single-trade-count field. Never imports xttrader.
"""
from dataclasses import dataclass, replace
from decimal import Decimal
import hashlib
from pathlib import Path
import numpy as np
import pandas as pd
from . import option_top_cycle_research as parent

FAMILY = 'probe_commodity_dadao_20260912_v1'
PARENT_SHA256 = '5a4011bb002ed75ba23d42c62dd20147422ac0dbc5324a2821293c9d5a954062'
MODE_MAP = dict(coarse='improve_l1', last_d0='improve_single',
                last_d500='improve_single_d500', last_d1000='improve_single_d1000',
                queue_d500='queue_single_d500', unit_d500='improve_single_d500')
MODES = tuple(MODE_MAP)


def price_cents(price, unit):
    return int((Decimal(str(price))*Decimal(str(unit))*100).to_integral_value())


def load_frame(frame, *, code, date, detail):
    """Derive evidence in market-time order, within continuous day sessions only."""
    unit = 10 if code.endswith('.SH') else detail.get('OptUnit') or detail.get('VolumeMultiple')
    tick = detail.get('PriceTick')
    if not unit or not tick: raise ValueError('Missing unit/price tick')
    tick_cents = price_cents(tick, unit)
    if tick_cents <= 0: raise ValueError('Sub-cent tick needs a separate model identity')
    f=frame.sort_values('time',kind='stable').drop_duplicates('time',keep='last')
    times=f.time.to_numpy(dtype=np.int64)
    dt=pd.to_datetime(times,unit='ms',utc=True).tz_convert('Asia/Shanghai')
    if set(dt.strftime('%Y%m%d'))!={date}:raise ValueError('Unexpected calendar date')
    minute=np.asarray(dt.hour*60+dt.minute+dt.second/60+dt.microsecond/60_000_000)
    spans=[(570,690),(780,930)] if code.endswith('.SH') else [(540,615),(630,690),(810,900)]
    sessions=np.full(len(f),-1)
    for j,(a,b) in enumerate(spans):sessions[(minute>=a)&(minute<b)]=j
    events=[]; unit_evidence=[];previous=None;resets=amount_mismatch=0
    for i,r in enumerate(f.itertuples()):
        bids=tuple((price_cents(p,unit),int(q)) for p,q in zip(r.bidPrice,r.bidVol) if p>0 and q>0)
        asks=tuple((price_cents(p,unit),int(q)) for p,q in zip(r.askPrice,r.askVol) if p>0 and q>0)
        bid=price_cents(r.bidPrice[0],unit); ask=price_cents(r.askPrice[0],unit)
        last=price_cents(r.lastPrice,unit)
        qty=tx=0; side=strict='unknown'; quality=unit_ok=False
        if previous is not None:
            pr,pbid,pask,plast,ps=previous
            dv=int(r.volume-pr.volume); da=round(float(r.amount-pr.amount)*100)
            tx=max(0,int(r.transactionNum-pr.transactionNum))
            if dv<0 or da < -2:resets+=1
            elif dv>0 and last>0 and sessions[i]>=0 and sessions[i]==ps:
                qty=dv
                if pask>0 and last>=pask:side='buy'
                elif pbid>0 and last<=pbid:side='sell'
                elif last>plast:side='buy'
                elif last<plast:side='sell'
                # At least one contract traded at the last reported price. Limit
                # usable volume to one downstream. Never allocate dv at that price.
                quality=(da>0 and 0<pbid<pask and pr.bidVol[0]>0 and pr.askVol[0]>0
                         and 0<r.time-pr.time<=60_000)
                if quality:
                    if last>=pask:strict='buy'
                    elif last<=pbid:strict='sell'
                unit_ok=quality and dv==1 and abs(da-last)<=2
                if dv==1 and abs(da-last)>2:amount_mismatch+=1
        if sessions[i]>=0:
            events.append(parent.Event(int(r.time),int(previous[0].time) if previous else int(r.time),
                int(sessions[i]),bid,ask,int(r.bidVol[0]),int(r.askVol[0]),bids,asks,
                last,qty,tx,side,quality,strict))
            unit_evidence.append(unit_ok)
        previous=(r,bid,ask,last,int(sessions[i]))
    if not events:raise ValueError('No day-session events')
    first=next((e for e in events if 0<e.bid<e.ask and e.bid_qty>0 and e.ask_qty>0),None)
    if first is None:raise ValueError('No valid two-sided quote')
    # Causal opening budget, identical across fees/modes; no future high-price funding.
    initial=max(1_000_000, ((first.ask*5//4+2000+99_999)//100_000)*100_000)
    meta=dict(code=code,date=date,unit=unit,tick_cents=tick_cents,price_tick=tick,
        initial_cents=initial,raw_rows=len(frame),events=len(events),
        volume_events=sum(e.quantity>0 for e in events),
        usable_last_events=sum(e.single and e.strict_side!='unknown' for e in events),
        unit_increment_events=sum(unit_evidence),amount_mismatch_unit_events=amount_mismatch,
        cumulative_resets=resets,first_ts=events[0].ts,last_ts=events[-1].ts,
        trade_count_available=bool(f.transactionNum.max()>0))
    return events,unit_evidence,meta


def run(events, unit_evidence, *, code, date, mode, fee_cents, initial_cents, tick_cents):
    if hashlib.sha256(Path(parent.__file__).read_bytes()).hexdigest()!=PARENT_SHA256:
        raise ValueError('Archived execution dependency changed; register a new research ID')
    if mode not in MODE_MAP:raise ValueError(mode)
    converted=events if mode=='coarse' else [replace(e, quantity=min(1,e.quantity),
        single=unit_evidence[i] if mode=='unit_d500' else e.single) for i,e in enumerate(events)]
    result=parent.run(converted,code=code,mode=MODE_MAP[mode],capacity=1,
        fee_cents=fee_cents,initial_cents=initial_cents,tick_cents=tick_cents)
    model_id=f'{FAMILY}_{mode}_q1_f{fee_cents}'
    s=result['summary']; s.update(model_id=model_id,mode=mode,date=date,
        account_id=f'{model_id}_{code}_{date}',evidence='aggregate_L1' if mode=='coarse' else 'latest_contract_only')
    for o in result['orders']:o['model_id']=model_id
    for f in result['fills']:
        f['model_id']=model_id
        f['source_last_contract_evidence']=f.pop('source_single')
    s['last_quote_age_seconds']=(events[-1].ts-s['last_quote_ts'])/1000 if s['last_quote_ts'] else None
    s['stale_tail']=bool(s['end_inventory'] and (s['last_quote_age_seconds'] is None or s['last_quote_age_seconds']>60))
    s['completed_cycle_net_cny']=sum(c['net_cents'] for c in result['cycles'])/100
    s['open_cycle_contribution_cny']=s['pnl_cny']-s['completed_cycle_net_cny']
    s['breakeven_fee_cny_per_side']=(s['realized_gross_cny']+s['tail_gross_cny'])/s['filled_contract_sides'] if s['filled_contract_sides'] else None
    return result


def audit_result(result):
    """Independently reconstruct cash, inventory, costs and passive timing."""
    s=result['summary'];cash=round(s['initial_cash_cny']*100);inv=fees=basis=realized=0
    orders={o['id']:o for o in result['orders']}
    for f in result['fills']:
        o=orders[f['order_id']];q=f['quantity'];p=f['price_cents'];fee=f['fee_cents']
        assert o['model_id']==f['model_id']==s['model_id']
        assert f['ts']>=o['due_ts'] and 0<q<=o['quantity']<=1
        if f['kind']=='passive':
            assert o['created_ts']<f['ts'] and f['active_ts']<=f['source_previous_ts']
            assert q<=f['source_quantity']
            if s['mode']!='coarse':
                assert f['source_last_contract_evidence'] and f['source_quantity']==1
                assert f['source_strict_side'] in ['buy','sell']
        if f['side']=='buy':
            assert inv==0
            cash-=q*p+fee;inv+=q;basis=q*p
        else:
            assert q==inv
            cash+=q*p-fee;inv-=q;realized+=q*p-basis;basis=0
        fees+=fee
        assert cash==f['cash_cents'] and inv==f['inventory'] and cash>=0
    assert cash==round(s['end_cash_cny']*100) and inv==s['end_inventory']
    assert fees==round(s['fees_cny']*100) and realized==round(s['realized_gross_cny']*100)
    assert abs(s['pnl_cny']-(s['realized_gross_cny']+s['tail_gross_cny']-s['fees_cny']))<1e-7
    assert abs(s['pnl_cny']-s['completed_cycle_net_cny']-s['open_cycle_contribution_cny'])<1e-7
    return True
