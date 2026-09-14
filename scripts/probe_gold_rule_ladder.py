"""Rule-by-rule gold ablation and independently reconciled cash requirements."""
from pathlib import Path
from dataclasses import asdict
from math import ceil
import json
import numpy as np
from zhaiquant import gold_direction_research as g
from zhaiquant.gold_aligned_value_research import ValueState
from zhaiquant import gold_rule_ladder_research as engine
from probe_gold_direction import inputs, CODES
from probe_commodity_capital import ROOT, WORK, read, write, digest, pack, unpack

OUT=WORK/'reports/gold_rule_ladder_20260913_v1'


def timeline(es,fs,strike,through=None):
    """Build snapshots in causal order; never query later samples for an old decision."""
    value=ValueState(strike,'fast_lower'); rows=[]
    events=sorted([(e.ts,0,e) for e in es]+[(f.ts,1,f) for f in fs]+[(b,-1,b) for b in g.BOUNDARIES],key=lambda x:x[:2])
    for ts,kind,e in events:
        if through is not None and ts>through: break
        if kind==1: value.on_future(e); continue
        if kind==-1: rows.append((kind,e,None,None,None,None)); continue
        value.new_session(e.session); low=value.feature(ts); mid=upper=None
        if low:
            mid=dict(low); upper=dict(low)
            for feature,vol,reference in ((mid,value.vol,'mid'),(upper,max(value.vol,value.fast_vol),'fast_upper')):
                fair,delta=g.black_call(value.future.mid,value.strike,value.maturity(ts),vol)
                feature.update(fair_cents=fair*100000,delta=delta,vol=vol,reference_kind=reference)
        rows.append((kind,e,low,mid,upper,value.future))
        value.observe_option(e)
    return rows,value.calibrations


def replay(rows,code,case,direction,strike,capital=250000,through=False):
    a=engine.Account(code,case,direction,strike,capital,through)
    for kind,e,low,mid,upper,future in rows:
        if kind==-1: a.boundary(e)
        else:
            a.value.future=future
            a.value.current=(low if direction==1 else upper) if a.rules.fast else mid
            a.option(e)
    return a.result()


def economic(r):
    def clean(x):
        if isinstance(x,dict): return {k:clean(v) for k,v in x.items() if k not in ('model_id','cash_cents')}
        if isinstance(x,list): return [clean(y) for y in x]
        return x
    return {k:clean(r[k]) for k in ('orders','fills','cycles','cancels','curve','boundaries')}


def audit(r,complete=True):
    s=r['summary']; initial=round(s['initial_cash_cny']*100); cash=initial; inv=fees=gross=0; basis=0
    orders={o['id']:o for o in r['orders']}; peak_premium=0; min_cash=cash
    for f in r['fills']:
        d=1 if f['side']=='buy' else -1; p=f['price_cents']; o=orders[f['order_id']]
        assert f['model_id']==o['model_id']==s['model_id']
        if inv: assert inv==-d; gross+=inv*(p-basis)
        else: basis=p; peak_premium=max(peak_premium,p)
        inv+=d; cash-=d*p+g.FEE; fees+=g.FEE; min_cash=min(min_cash,cash)
        assert (cash,inv)==(f['cash_cents'],f['inventory']) and cash>=0 and inv in (-1,0,1)
        if f['kind']=='passive':
            assert f['active_ts']<=f['source_previous_ts'] and f['created_ts']<f['ts']
            assert f['source_single'] and f['source_quantity']==1
            assert f['source_strict_side']==('sell' if d==1 else 'buy')
            assert f['source_last_cents']<=p if d==1 else f['source_last_cents']>=p
            if s['strict_through']: assert f['source_last_cents']!=p
        else:
            assert f['kind']=='pre_break_market_close'
            assert p==(f['source_bid'] if d==-1 else f['source_ask']) and f['source_qty']>=1
            assert any(b-5000<=f['ts']<b for b in g.BOUNDARIES)
        feat=o.get('feature')
        if feat:
            assert feat['vol_source_ts']<o['created_ts'] and feat['future_source_ts']<o['created_ts']
            assert feat['future_available_ts']<=o['created_ts']
    assert fees==round(s['fees_cny']*100) and gross==round(s['realized_gross_cny']*100)
    if complete:
        assert inv==s['end_inventory']==0 and cash-initial==sum(c['net_cents'] for c in r['cycles'])
        assert len(r['boundaries'])==3 and all(b['inventory']==0 and b['pending_order'] is None for b in r['boundaries'])
    for c in r['cycles']: assert not any(c['entry_ts']<b<c['exit_ts'] for b in g.BOUNDARIES)
    nets=[c['net_cents']/100 for c in r['cycles']]
    durations=[c['duration_seconds'] for c in r['cycles']]
    s.update(peak_single_entry_premium_cny=peak_premium/100,
        minimum_cash_for_fills_only_cny=(initial-min_cash)/100,
        worst_cycle_cny=min(nets,default=0),best_cycle_cny=max(nets,default=0),
        profit_without_best3_cny=sum(nets)-sum(sorted([x for x in nets if x>0],reverse=True)[:3]),
        win_rate_pct=100*sum(x>0 for x in nets)/len(nets) if nets else 0,
        median_hold_seconds=float(np.median(durations)) if durations else 0,
        max_hold_seconds=max(durations,default=0))


def portfolio(rs):
    changes=[]
    for r in rs:
        a=np.array(r['curve'],dtype=np.int64); changes.extend(zip(a[:,0],np.diff(a[:,1],prepend=0)))
    changes.sort(); combined={}
    for ts,change in changes: combined[ts]=combined.get(ts,0)+change
    times=list(combined); pnl=np.cumsum(list(combined.values())); dd=np.maximum.accumulate(np.maximum(pnl,0))-pnl
    return dict(pnl_cny=float(pnl[-1]/100),max_drawdown_cny=float(dd.max()/100),
        cycles=sum(r['summary']['complete_cycles'] for r in rs),fees_cny=sum(r['summary']['fees_cny'] for r in rs),
        independent_minimum_quote_cash_cny=sum(r['summary']['minimum_cash_for_all_entry_quotes_cny'] for r in rs),
        curve=[[int(t),int(p)] for t,p in zip(times,pnl)])


def main():
    OUT.mkdir(exist_ok=True)
    if (OUT/'result_manifest.json').exists(): raise RuntimeError('Frozen output exists; use a new family for modifications')
    cs=engine.cases(); sources=[Path(__file__),Path(engine.__file__),ROOT/'src/zhaiquant/gold_aligned_value_research.py',
        ROOT/'src/zhaiquant/gold_direction_research.py',ROOT/'src/zhaiquant/gold_direction_timing_research.py',
        ROOT/'src/zhaiquant/commodity_dadao_research.py',ROOT/'src/zhaiquant/option_top_cycle_research.py',
        ROOT/'src/zhaiquant/commodity_flow_strategy.py',ROOT/'scripts/probe_gold_direction.py']
    frozen=read(WORK/'reports/gold_fast_validation_20260913_v5/result_manifest.json')
    for p,h in frozen.items(): assert digest(ROOT/p)==h
    plan=dict(family=engine.FAMILY,date=g.DATE,codes=CODES,fee_cny=1.7,delay_ms=0,
        cases={k:dict(label=v['label'],group=v['group'],rules=asdict(v['rules'])) for k,v in cs.items()},
        settlement='market only; stop and close5s before each of3breaks',common_start='09:30; contract selection held fixed',
        short_reserve='inherited20% future notional plus premium research proxy; NOT exchange/broker margin',
        short_fast='upper10/60s IV for conservative sale; long uses lower',
        source_hashes={str(p.relative_to(ROOT)):digest(p) for p in sources},
        inputs={p:digest(ROOT/p) for p in [
            '广义套利/reports/gold_state_20260913_v1/catalog_terms.json',
            '广义套利/reports/gold_state_20260913_v1/inputs/au2610C960.SF.pkl',
            '广义套利/reports/gold_state_20260913_v1/inputs/au2610C952.SF.pkl',
            '广义套利/reports/gold_direction_20260913_v1/inputs/au2610.SF.pkl']})
    write(OUT/'plan.json',plan); summaries={}; pairs={}; capital_results={}; validations=[]
    for code in CODES:
        es,fs,detail=inputs(code); strike=float(detail['OptExercisePrice']); rows,cal=timeline(es,fs,strike)
        prefix,_=timeline(es,fs,strike,g.START+5*3600000)
        assert prefix==[x for x in rows if (x[1] if x[0]==-1 else x[1].ts)<=g.START+5*3600000]
        for case,cfg in cs.items():
            for direction in (1,-1):
                for through in ([False,True] if cfg['group']=='ladder' else [False]):
                    key=f'{code}_{case}_{"long" if direction==1 else "short"}_through{int(through)}'
                    r=replay(rows,code,case,direction,strike,through=through); audit(r)
                    pre=replay(prefix,code,case,direction,strike,through=through)
                    for field,t in [('orders','created_ts'),('fills','ts'),('cycles','exit_ts'),('cancels','ts')]:
                        assert pre[field]==[x for x in r[field] if x[t]<=g.START+5*3600000],(key,field)
                    pack(OUT/f'{key}.json.gz',r); summaries[key]=r['summary']
                    pairs.setdefault((case,direction,through),[]).append(r)
                    if case=='s10' and direction==1:
                        old=unpack(WORK/f'reports/gold_fast_validation_20260913_v5/{code}_fast10_through{int(through)}_market_d0.json.gz')
                        assert economic(r)==economic(old),'Final long must reproduce frozen0.1'
                        validations.append(key)
                        if not through:
                            minimum=r['summary']['minimum_cash_for_all_entry_quotes_cny']
                            for label,cash in [('exact_min',minimum),('below_min',round(minimum-.01,2)),('twenty_thousand',20000)]:
                                low=replay(rows,code,case,direction,strike,capital=cash); audit(low)
                                same=economic(low)==economic(r)
                                assert same if label!='below_min' else not same
                                capital_results[code+'_'+label]=dict(capital_cny=cash,same_orders_fills_curve=same,summary=low['summary'])
                                pack(OUT/f'{code}_capital_{label}.json.gz',low)
                    print(key,round(r['summary']['pnl_cny'],2),r['summary']['complete_cycles'],flush=True)
        pack(OUT/f'{code}_calibrations.json.gz',cal)
    ports={f'{k}_{d}_{int(t)}':portfolio(v) for (k,d,t),v in pairs.items()}
    write(OUT/'results.json',summaries);write(OUT/'portfolios.json',ports);write(OUT/'capital_validation.json',capital_results)
    for p,h in {**frozen,**plan['source_hashes'],**plan['inputs']}.items(): assert digest(ROOT/p)==h
    write(OUT/'verification.json',dict(status='passed',accounts=len(summaries),capital_replays=len(capital_results),
        zero_boundaries=3*(len(summaries)+len(capital_results)),all_cash_fees_and_prefixes=True,
        final_long_equal_to_frozen=validations,causal_shared_features=True,parent_hashes_unchanged=True))
    write(OUT/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.is_file() and p.name!='result_manifest.json'})


if __name__=='__main__': main()
