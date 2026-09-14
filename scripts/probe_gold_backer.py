"""Replay declared gold research hypotheses against frozen four-day evidence."""
from pathlib import Path
from collections import Counter, defaultdict, deque
import bisect
import pandas as pd
from zhaiquant import gold_backer_research as m
from zhaiquant.gold_history_validation import Clock,timeline
from probe_commodity_capital import ROOT,WORK,read,write,pack,unpack,digest

BASE=WORK/'reports/gold_history_20260913_v1'
OUT=WORK/'reports/gold_backer_20260914_v1'
DATES=['20260907','20260908','20260909','20260910']
CODES=['au2610C960.SF','au2610C952.SF']
PARENT_KEYS={'trend':'v02_trend_long','switch':'v02_cancel_switch','value':'fusion_throttle_long'}


def add_fast(rows):
    history=deque();session=None;out=[]
    for kind,event,features,mid,future in rows:
        if kind==1:
            if event.session!=session: history.clear();session=event.session
            history.append(event)
            while history and history[0].source_ts<event.source_ts-4000:history.popleft()
        if features:
            features={d:dict(f) if f else None for d,f in features.items()}
            for f in features.values():
                if f:
                    old=next((x for x in reversed(history) if x.source_ts<=f['future_source_ts']-2000),None)
                    f['move2_cents']=f['delta']*(f['future_mid']-old.mid)*100000 if old and f['future_source_ts']-2000-old.source_ts<=2000 else None
        out.append((kind,event,features,mid,future))
    return out


def audit(r,complete=True):
    s=r['summary'];cash=round(s['initial_cash_cny']*100);inv=fees=gross=0
    actions=sorted([(f['ts'],0,f) for f in r['fills']]+[(x['ts'],1,x) for x in r['fee_adjustments']],key=lambda x:x[:2])
    for ts,kind,x in actions:
        if kind==1:cash+=x['amount_cents'];fees-=x['amount_cents'];continue
        d=1 if x['side']=='buy' else -1
        cash-=d*x['price_cents']+x['fee_cents'];inv+=d;fees+=x['fee_cents']
        assert cash==x['cash_cents'] and inv==x['inventory'] and inv in (-1,0,1) and cash>=0
        if x['kind']=='passive':
            assert x['created_ts']<ts and x['active_ts']<=x['source_previous_ts']
            assert x['source_strict_side']==('sell' if d==1 else 'buy')
            assert d*(x['price_cents']-x['source_last_cents'])>=int(s['strict_through'])
            assert x.get('cancel_ts') is None or x['cancel_ts']>x['source_previous_ts']
        if x['kind'] in ('risk_market_close','pre_break_market_close'):
            assert x['source_quote_ts']==ts and x['source_qty']>=1
            assert x['price_cents']==(x['source_bid'] if d==-1 else x['source_ask'])
    assert cash==round(s['end_cash_cny']*100) and fees==round(s['fees_cny']*100)
    assert sum(c['gross_cents'] for c in r['cycles'])==round(s['realized_gross_cny']*100)
    if complete:
        assert inv==0 and len(r['boundaries'])==3
        assert all(x['inventory']==0 and x['pending_order'] is None for x in r['boundaries'])
        assert sum(c['net_cents'] for c in r['cycles'])==cash-round(s['initial_cash_cny']*100)
    for c in r['cycles']:
        assert c['net_cents']==c['gross_cents']-c['fees_cents']
        if c['exit_kind']=='virtual_cost_close':assert c['gross_cents']==0


def replay(rows,code,policy,detail,clock,through=False,settlement='exclude',cut=None):
    a=m.Account(code,policy,detail['OptExercisePrice'],through,settlement=settlement)
    a.model=a.model.replace(m.FAMILY,m.FAMILY+'_'+clock.date)
    for kind,event,features,mid,future in rows:
        ts=event if kind==-1 else event.ts
        if cut is not None and ts>cut:break
        if kind==-1:a.boundary(event)
        elif kind==1:a.future_event(event,features)
        else:a.value.future=future;a.features=features;a.option(event)
    r=a.result();audit(r,cut is None)
    r['summary']['history_date']=clock.date
    return clock.restore(r)


def economics(r):
    def clean(x):
        if isinstance(x,dict):return {k:clean(v) for k,v in x.items() if k not in ('model_id','support_at_entry_order','move2_cents')}
        if isinstance(x,list):return [clean(v) for v in x]
        return x
    return {k:clean(r[k]) for k in ('orders','fills','cycles','cancels','curve')}


def warning_audit(rows,r,clock):
    futures=[(event.ts-clock.shift,features) for kind,event,features,_,_ in rows if kind==1]
    times=[t for t,_ in futures];orders={x['id']:x for x in r['orders']}
    entries={f['ts']:f for f in r['fills'] if not f['closing']};result=[]
    for c in r['cycles']:
        if c['exit_kind']=='virtual_cost_close':continue
        fill=entries[c['entry_ts']];o=orders[fill['order_id']];start=bisect.bisect_right(times,o['created_ts'])
        stop=bisect.bisect_left(times,fill['ts']);warnings={}
        for t,features in futures[start:stop]:
            f=features[c['direction']]
            checks={'old_trend':not m.base.trend_ok(f,c['direction'],o['entry_spread'],2000),
                'fast':m.fast_bad(f,c['direction'],2000,o.get('feature'))[0]}
            for k,bad in checks.items():
                if bad and k not in warnings:warnings[k]=t
        row=dict(date=clock.date,code=r['summary']['code'],entry_ts=c['entry_ts'],entry_order_ts=o['created_ts'],
            net_cny=c['net_cents']/100,fill_previous_ts=fill['source_previous_ts'])
        for k in ('old_trend','fast'):
            t=warnings.get(k)
            row[k+'_category']='no_pre_fill_warning' if t is None else 'before_fill_interval' if t<=fill['source_previous_ts'] else 'inside_ambiguous_interval'
            row[k+'_warning_ts']=t
        result.append(row)
    return result


def main():
    OUT.mkdir(exist_ok=True);(OUT/'ledgers').mkdir(exist_ok=True)
    (OUT/'cost_ledgers').mkdir(exist_ok=True)
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen result')
    details=read(BASE/'catalog_terms.json')['details']
    preserved=read(BASE/'result_manifest.json')
    for rel,h in preserved.items():assert digest(ROOT/rel)==h,rel
    write(OUT/'plan.json',dict(family=m.FAMILY,policies={k:m.asdict(v) for k,v in m.POLICIES.items()},
        dates=DATES,codes=CODES,capital_cny_per_code=250000,fee_cny=1.7,latency_ms=0,
        settlements=['exclude','cost'],primary='exclude whole remaining break cycle including fees; normal closed losses retained',
        support='best bid>=10 contracts continuously for2s/3updates; improve one tick; half remaining plus at least one last-price trade evidence; disappearance separately classified',
        risk='delta-scaled 2s/10s move or change since order/entry<=-2ticks; futures-only cancellation; fresh-option-quote aggressive holding exit',
        limitation='Four reused development dates. No true queue, identified support order, or guaranteed execution.',
        parent_manifest_hash=digest(BASE/'result_manifest.json')))
    results={};warn=[];checks=[]
    for date in DATES:
        clock=Clock(date)
        for code in CODES:
            detail=details[code];folder=BASE/date/'full_inputs'
            es,fs,_,_=clock.inputs(pd.read_pickle(folder/f'{code}.pkl'),pd.read_pickle(folder/f'{detail["OptUndlCode"]}.SF.pkl'),code,detail)
            rows=add_fast(timeline(es,fs,detail['OptExercisePrice'],detail['ExpireDate'],clock))
            for through in (False,True):
                for policy in m.POLICIES:
                    key=f'{date}_{code}_{policy}_through{int(through)}'
                    r=replay(rows,code,policy,detail,clock,through)
                    pre=replay(rows,code,policy,detail,clock,through,cut=m.g.START+5*3600000)
                    for k,t in [('orders','created_ts'),('fills','ts'),('cycles','exit_ts'),('cancels','ts'),('fee_adjustments','ts'),('risk_events','ts')]:
                        assert pre[k]==[v for v in r[k] if v[t]<=clock.start+5*3600000],(key,k)
                    cost=replay(rows,code,policy,detail,clock,through,settlement='cost')
                    pack(OUT/'cost_ledgers'/f'{key}.json.gz',cost)
                    # High-capital comparison must verify identical execution, not assume it.
                    signature=lambda a:[(f['ts'],f['side'],f['price_cents'],f['kind']) for f in a['fills']]
                    assert signature(r)==signature(cost),key
                    r['summary']['cost_with_fees_pnl_cny']=cost['summary']['pnl_cny']
                    if policy in PARENT_KEYS:
                        market=replay(rows,code,policy,detail,clock,through,settlement='market')
                        old=unpack(BASE/date/'ledgers'/f'{code}_{PARENT_KEYS[policy]}_through{int(through)}.json.gz')
                        assert economics(market)==economics(old),(key,'parent reproduction')
                        r['summary']['old_actual_close_pnl_cny']=old['summary']['pnl_cny']
                    if policy=='trend' and not through:warn+=warning_audit(rows,r,clock)
                    pack(OUT/'ledgers'/f'{key}.json.gz',r);results[key]=r['summary']
                    checks.append(dict(key=key,prefix=True,accounting=True,cost_path_equal=True))
                print(date,code,'through',int(through),'done',flush=True)
    for rel,h in preserved.items():assert digest(ROOT/rel)==h,rel
    write(OUT/'results.json',results);write(OUT/'warning_audit.json',warn)
    write(OUT/'verification.json',dict(status='passed',accounts=len(results),checks=checks,
        parent_accounts_reproduced=48,flat_boundaries=3*len(results),old_manifest_files_unchanged=len(preserved)))
    sources=list((ROOT/'src/zhaiquant').glob('gold_*.py'))
    sources += [ROOT/'src/zhaiquant'/x for x in ['commodity_dadao_research.py','option_top_cycle_research.py','commodity_flow_strategy.py']]
    sources += [Path(__file__), ROOT/'tests/test_gold_backer_research.py']
    write(OUT/'source_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in sources})


if __name__=='__main__':main()
