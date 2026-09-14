"""Fixed-rule multi-day option replay. Independent daily accounts; no fitting."""
import argparse
import gzip
import json
from pathlib import Path
import sys
import pandas as pd

from probe_option_top_cycle import ROOT,ARCHIVE,NAMES,digest
from probe_option_guard import audit_extra
from render_option_guard import thin
from zhaiquant.option_guard_cost_separated import FAMILY,features,run
from zhaiquant.option_history_replay import load_day
from zhaiquant.option_top_cycle_research import MODES

BASE=ARCHIVE/'大道至简费用纠偏_20260909_v3'


def independent_equity(r,events):
    cash=1_000_000;inv=bid=i=holding=0;previous=None
    for e,point in zip(events,r['curve']):
        if previous is not None:holding+=inv*(e.ts-previous)
        while i<len(r['fills']) and r['fills'][i]['ts']==e.ts:
            f=r['fills'][i];q=f['quantity'];value=q*f['price_cents'];fee=f['fee_cents']
            assert fee==170*q
            cash+=value-fee if f['side']=='sell' else -value-fee
            inv+=q if f['side']=='buy' else -q;i+=1
        if 0<e.bid<e.ask and e.bid_qty>0 and e.ask_qty>0:bid=e.bid
        assert point==(e.ts,cash+inv*bid-1_000_000,inv)
        previous=e.ts
    assert i==len(r['fills']) and holding==round(r['summary']['inventory_contract_seconds']*1000)
    return len(r['curve']),len(r['fills'])


def book_plot(events,date):
    base=pd.Timestamp(date,tz='Asia/Shanghai').value//10**6
    x=[];bid=[];ask=[];session=None
    for e in events:
        if session is not None and session!=e.session:x.append(None);bid.append(None);ask.append(None)
        good=0<e.bid<e.ask and e.bid_qty>0 and e.ask_qty>0
        x.append((e.ts-base)/60000);bid.append(e.bid/1_000_000 if good else None);ask.append(e.ask/1_000_000 if good else None)
        session=e.session
    return dict(x=x,bid=bid,ask=ask)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('--input',required=True);parser.add_argument('--output',required=True);args=parser.parse_args()
    source=Path(args.input);out=Path(args.output);out.mkdir(parents=True,exist_ok=False);(out/'plots').mkdir()
    audit=json.loads((source/'input_audit.json').read_text(encoding='utf-8'))
    capture=json.loads((source/'capture_manifest.json').read_text(encoding='utf-8'))
    frozen=json.loads((BASE/'manifest_sha256.json').read_text(encoding='utf-8'))
    for name,value in frozen.items():assert digest(BASE/name)==value,name
    old_sources=json.loads((BASE/'source_manifest.json').read_text(encoding='utf-8'))
    for name,value in old_sources.items():assert digest(ROOT/name)==value,name
    files=[Path(__file__),ROOT/'src/zhaiquant/option_history_replay.py',ROOT/'src/zhaiquant/option_guard_cost_separated.py',
           ROOT/'scripts/audit_option_month_inputs.py',ROOT/'scripts/capture_option_month.py',
           ROOT/'tests/test_option_history_replay.py',ROOT/'scripts/probe_option_guard.py',ROOT/'scripts/render_option_guard.py']
    hashes={str(p.relative_to(ROOT)):digest(p) for p in files}
    contract=dict(model_family=FAMILY,date_start=audit['start'],date_end=audit['end'],profiles=['control','entry'],
        modes=MODES,fee_cents=170,quality_buffer_cents=600,capacity=1,initial_cash_cents=1_000_000,
        daily_account_policy='Independent zero-inventory 10000CNY accounts each day; tail bid-marked, not carried or actually closed.',
        selection_policy='The nine contracts chosen on 20260909 are reviewed backwards; earlier dates not used in this parameter construction, but not unbiased OOS.',
        input_audit_sha256=digest(source/'input_audit.json'),capture_manifest_sha256=digest(source/'capture_manifest.json'),source_hashes=hashes)
    (out/'prerun_contract.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf-8')
    sums=[];prefixes=reference_count=observations=fill_count=0
    for c in NAMES:
        details=capture['contracts'][c]['details']
        days=[m for m in audit['rows'] if m['code']==c and m['eligible']]
        for meta in days:
            date=meta['date'];path=source/meta['file'];assert digest(path)==meta['sha256']
            events,_=load_day(pd.read_pickle(path),date,details);ff=features(events);graphs=[]
            for mode in MODES:
                for profile in ['control','entry']:
                    r=run(events,code=c,mode=mode,profile=profile,fee_cents=170,quality_buffer_cents=600,feature_rows=ff)
                    audit_extra(r,events)
                    points,fills=independent_equity(r,events);observations+=points;fill_count+=fills
                    if date=='20260909':
                        old=json.load(gzip.open(BASE/f'{c}_{profile}_{mode}_q1_b600_f170.json.gz','rt',encoding='utf-8'))
                        for key in ['orders','fills','cycles']:
                            assert r[key]==old[key],(c,mode,profile,key)
                        assert [list(x) for x in r['curve']]==old['curve']
                        reference_count+=1
                    if profile=='entry' and mode in ['improve_single_d500','improve_single_d1000']:
                        for end in [len(events)//3,2*len(events)//3]:
                            part=run(events[:end],code=c,mode=mode,profile=profile,fee_cents=170,quality_buffer_cents=600)
                            ts=events[end-1].ts
                            for key,field in [('fills','ts'),('orders','created_ts')]:
                                assert part[key]==[x for x in r[key] if x[field]<=ts]
                            assert part['curve']==r['curve'][:end]
                            prefixes+=1
                    s=r['summary'];s.update(name=NAMES[c],market_date=date,source_sha256=meta['sha256'],
                        gross_including_tail_cny=s['realized_gross_cny']+s['tail_gross_cny'],
                        completed_net_cny=sum(x['net_cents'] for x in r['cycles'])/100)
                    s['tail_including_open_fee_cny']=round(s['pnl_cny']-s['completed_net_cny'],2)
                    for key in ['orders','fills']:
                        for item in r[key]:item['market_date']=date
                    sums.append(s)
                    graphs.append(dict(mode=mode,profile=profile,curve=thin(r['curve']),fills=r['fills']))
                    with gzip.open(out/f'{c}_{date}_{profile}_{mode}_q1_b600_f170.json.gz','wt',encoding='utf-8',compresslevel=4) as stream:
                        json.dump(r,stream,ensure_ascii=False,separators=(',',':'))
            plot=dict(code=c,date=date,name=NAMES[c],book=book_plot(events,date),runs=graphs)
            (out/'plots'/f'{c}_{date}.js').write_text('window.receiveDay('+json.dumps(plot,ensure_ascii=False,separators=(',',':'))+');',encoding='utf-8')
        ss=[s for s in sums if s['code']==c and s['profile']=='entry' and s['mode']=='improve_single_d500' and s['market_date']<'20260909']
        print(c,'prior_days',len(ss),'net',round(sum(s['pnl_cny'] for s in ss),2),'positive',sum(s['pnl_cny']>0 for s in ss),
              'negative',sum(s['pnl_cny']<0 for s in ss),'tail_days',sum(s['end_inventory']>0 for s in ss),flush=True)
    for name,value in hashes.items():assert digest(ROOT/name)==value,name
    for name,value in frozen.items():assert digest(BASE/name)==value,name
    data=dict(**contract,trading_dates=audit['trading_dates'],summaries=sums,coverage=audit['rows'],
        audited_accounts=len(sums),prior_single_day_paths_verified=reference_count,prefix_checks=prefixes,
        independently_rebuilt_observations=observations,independently_rebuilt_fill_rows=fill_count,
        frozen_baseline_files_verified=len(frozen),received_contract_days=sum(m['capture_status']=='received' for m in audit['rows']),
        eligible_contract_days=sum(m['eligible'] for m in audit['rows']))
    (out/'matrix.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print('VERIFIED',len(sums),'accounts',prefixes,'prefixes',observations,'equity points',flush=True)


if __name__=='__main__':main()
