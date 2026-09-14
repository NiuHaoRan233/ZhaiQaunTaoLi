"""Run the registered option top-cycle experiment against local archived ticks only."""
from pathlib import Path
import argparse
from dataclasses import asdict
import hashlib
import gzip
import json
import sys
import numpy as np
import pandas as pd
from zhaiquant.option_top_cycle_research import Event, run, MODES, FAMILY

ROOT=Path(__file__).resolve().parents[1]
ARCHIVE=ROOT/'债券活跃度观察/期权做市研究'
NAMES={
 '10012084.SHO':'500ETF沽9月8250','10012348.SHO':'500ETF购10月7500',
 '10012359.SHO':'500ETF沽10月8000','10011503.SHO':'科创板50购12月1600',
 '10011070.SHO':'300ETF沽9月5500','90007991.SZO':'沪深300ETF购10月4700',
 '90008021.SZO':'中证500ETF沽10月3300','10012379.SHO':'科创50沽10月1850',
 '90007929.SZO':'深证100ETF购3月3100'}

def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def load(code,details):
    path=ARCHIVE/f'options_liquidity_20260909_close_history_{code}.pkl'
    f=pd.read_pickle(path).sort_values('time',kind='stable').drop_duplicates('time',keep='last')
    unit=int(details.get('OptUnit') or details['VolumeMultiple'])
    tick=float(details['PriceTick'])
    assert unit==10000 and tick==.0001, 'This registered batch requires standard contracts.'
    times=f.time.to_numpy(dtype=np.int64)
    dt=pd.to_datetime(times,unit='ms',utc=True).tz_convert('Asia/Shanghai')
    assert set(dt.strftime('%Y-%m-%d'))=={'2026-09-09'}
    mins=np.asarray(dt.hour*60+dt.minute+dt.second/60+dt.microsecond/60000000)
    sessions=np.where((mins>=570)&(mins<690),0,np.where((mins>=780)&(mins<897),1,-1))
    prices=lambda values: tuple(int(round(float(p)*unit*100)) for p in values)
    events=[]; previous=None
    resets=0
    for i,r in enumerate(f.itertuples()):
        bid,ask=prices(r.bidPrice),prices(r.askPrice)
        last=int(round(r.lastPrice*unit*100))
        qty=tx=0;single=False;side=strict='unknown'
        if previous is not None:
            pr,pb,pa,pl,ps=previous
            dv=int(r.volume-pr.volume);tx=int(r.transactionNum-pr.transactionNum);da=float(r.amount-pr.amount)
            if dv<0 or tx<0 or da<-.02:
                resets+=1
            elif dv>0 and sessions[i]>=0 and sessions[i]==ps:
                qty=dv
                if pa[0]>0 and last>=pa[0]:side='buy'
                elif pb[0]>0 and last<=pb[0]:side='sell'
                elif last>pl:side='buy'
                elif last<pl:side='sell'
                single=tx==1 and abs(da*100-last*qty)<=2.01
                if (single and 0<pb[0]<pa[0] and pr.bidVol[0]>0 and pr.askVol[0]>0 and r.time-pr.time<=2000):
                    if last>=pa[0]:strict='buy'
                    elif last<=pb[0]:strict='sell'
        if sessions[i]>=0:
            events.append(Event(int(r.time),int(previous[0].time) if previous else int(r.time),int(sessions[i]),
                bid[0],ask[0],int(r.bidVol[0]),int(r.askVol[0]),
                tuple((p,int(q)) for p,q in zip(bid,r.bidVol) if p>0 and q>0),
                tuple((p,int(q)) for p,q in zip(ask,r.askVol) if p>0 and q>0),
                last,qty,max(0,tx),side,single,strict))
        previous=(r,bid,ask,last,int(sessions[i]))
    assert resets==0
    return events,dict(code=code,name=NAMES[code],unit=unit,price_tick=tick,tick_cents=round(tick*unit*100),
        sha256=digest(path),rows=len(f),continuous_frames=len(events),first_ts=events[0].ts,last_ts=events[-1].ts,
        volume_events=sum(e.quantity>0 for e in events),single_events=sum(e.single for e in events),
        strict_directed_events=sum(e.strict_side!='unknown' for e in events),cumulative_resets=resets)

def audit(result,initial_cents):
    s=result['summary'];cash=initial_cents;inv=0;fees=0;buy_cost=0;realized=0
    orders={o['id']:o for o in result['orders']}
    for f in result['fills']:
        o=orders[f['order_id']]
        assert f['model_id']==s['model_id'] and f['code']==s['code']
        assert f['ts']>=o['created_ts'] and f['quantity']<=o['quantity']
        if f['kind']=='passive':
            assert f['ts']>f['created_ts'] and f['active_ts']<=f['source_previous_ts']
            assert f['quantity']<=f['source_quantity']
            if s['mode']!='improve_l1':assert f['source_single'] and f['source_strict_side']!='unknown'
        q=f['quantity'];p=f['price_cents'];fee=f['fee_cents']
        fees+=fee
        if f['side']=='buy':
            assert inv==0
            cash-=q*p+fee;inv+=q;buy_cost=p
        else:
            assert inv>=q
            cash+=q*p-fee;inv-=q;realized+=q*(p-buy_cost)
        assert inv==f['inventory'] and cash==f['cash_cents'] and cash>=0
    assert inv==s['end_inventory'] and cash==round(s['end_cash_cny']*100)
    assert realized==round(s['realized_gross_cny']*100) and fees==round(s['fees_cny']*100)
    assert round(s['pnl_cny']*100)==realized+round(s['tail_gross_cny']*100)-fees

def reference_check(events):
    # Differential test against the unchanged archived fixed-contract comparison.
    import importlib.util
    from types import SimpleNamespace
    spec=importlib.util.spec_from_file_location('original_top_cycle',ROOT/'scripts/probe_top_cycle_comparison.py')
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    converted=[]
    for e in events:
        clock=pd.Timestamp(e.ts,unit='ms',tz='UTC').tz_convert('Asia/Shanghai').strftime('%H:%M:%S.%f')[:12]
        converted.append(SimpleNamespace(tick_id=e.ts,market_date='2026-09-09',market_time=clock,
            market_ts_ms=e.ts,code='132026.SH',bid1=e.bid/100000,ask1=e.ask/100000,
            last_price=e.last/100000,trade_bonds=e.quantity*1000,inferred_side=e.side))
    # 1000 synthetic bonds represent one contract; native 0.001 x 1000 = one CNY tick.
    params=SimpleNamespace(effective_earliest_entry_time=lambda day:'09:30:00.000',latest_entry_time='14:57:00.000')
    native=module.run_day(converted,variant='fixed_132026',initial_cash=10000,ready_ts=events[0].ts,params=params,stock_codes={})
    adapted=run(events,code='reference',mode='improve_l1',initial_cents=1_000_000,tick_cents=100)
    expected=[(f['market_ts_ms'],f['side'],round(f['price']*100000),f['quantity']//1000) for f in native['fills']]
    actual=[(f['ts'],f['side'],f['price_cents'],f['quantity']) for f in adapted['fills']]
    assert actual==expected, 'Original simple-loop economic path mismatch'
    assert all(f['quantity']==1000 for f in native['fills']), 'Fractional option in original mapped path'
    assert abs(adapted['summary']['pnl_cny']-native['trading_pnl'])<1e-6
    return dict(fills=len(actual),pnl_cny=adapted['summary']['pnl_cny'],pass_status=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    details=json.loads((ARCHIVE/'options_market_20260909_snapshot.json').read_text(encoding='utf-8'))['details']
    metadata=[];summaries=[];checks=[];references=[]
    for code in NAMES:
        events,meta=load(code,details[code]);metadata.append(meta)
        references.append(dict(code=code,**reference_check(events)))
        for capacity in [1,5]:
            for mode in MODES:
                for fee in [0,200,300,500]:
                    result=run(events,code=code,mode=mode,capacity=capacity,fee_cents=fee,
                        initial_cents=capacity*1_000_000,tick_cents=meta['tick_cents'])
                    audit(result,capacity*1_000_000)
                    s=result['summary'];s['name']=NAMES[code];summaries.append(s)
                    target=out/f'{code}_{mode}_q{capacity}_f{fee}.json.gz'
                    with gzip.open(target,'wt',encoding='utf-8',compresslevel=5) as stream:
                        json.dump(result,stream,ensure_ascii=False,separators=(',',':'))
                    if capacity==1 and fee==300:
                        for end in [len(events)//3,2*len(events)//3]:
                            prefix=run(events[:end],code=code,mode=mode,capacity=capacity,fee_cents=fee,
                                initial_cents=1_000_000,tick_cents=meta['tick_cents'])
                            ts=events[end-1].ts
                            assert prefix['fills']==[f for f in result['fills'] if f['ts']<=ts]
                            assert prefix['orders']==[o for o in result['orders'] if o['created_ts']<=ts]
                        checks.append(dict(code=code,mode=mode,prefixes=2,pass_status=True))
        print(code,[(s['mode'],s['pnl_cny'],s['complete_cycles'],s['end_inventory']) for s in summaries if s['code']==code and s['capacity']==1 and s['fee_per_side_cny']==3],flush=True)
    manifest=dict(family=FAMILY,date='2026-09-09',metadata=metadata,summaries=summaries,prefix_checks=checks,original_loop_checks=references,
        source_hashes={str(p.relative_to(ROOT)):digest(p) for p in [Path(__file__),ROOT/'src/zhaiquant/option_top_cycle_research.py',ROOT/'scripts/probe_top_of_book_cycle.py',ROOT/'scripts/probe_top_cycle_comparison.py']},
        fee_note='Per-side per-contract fees debited on every fill; 2/3/5 CNY are assumptions, not actual broker rates.',
        timing_note='Trade interval settled before requests due within that interval activate on current snapshot. Marketable arrivals use top visible opposite quote and may fill without a tape trade. Not a real arrival replay.',
        scope_note='Independent fixed contracts, no bond selector, no underlying hedge, no overnight or actual tail liquidation.')
    (out/'matrix.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('VERIFIED',len(summaries),'accounts',len(checks)*2,'prefixes',flush=True)

if __name__=='__main__':main()
