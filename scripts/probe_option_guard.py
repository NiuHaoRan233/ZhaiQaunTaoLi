"""Replay registered option guard candidates using frozen local inputs only."""
from pathlib import Path
import argparse
from collections import defaultdict
import gzip
import json
import sys

from probe_option_top_cycle import ARCHIVE, ROOT, NAMES, load, audit, digest
from zhaiquant.option_guard_research import FAMILY, PROFILES, features, run
from zhaiquant.option_top_cycle_research import MODES

BASE = ARCHIVE / '大道至简初试_20260909_v1_run2'


def clean(value):
    if isinstance(value, dict): return {k:clean(v) for k,v in value.items() if k!='model_id'}
    if isinstance(value, (tuple,list)): return [clean(v) for v in value]
    return value


def audit_extra(result, events):
    audit(result,1_000_000)
    by_ts={e.ts:e for e in events}
    orders={o['id']:o for o in result['orders']}
    used=defaultdict(int)
    entry_ts=None
    for f in result['fills']:
        if f['side']=='buy':entry_ts=f['ts']
        if f['kind']=='risk_ioc':
            e=by_ts[f['ts']];o=orders[f['order_id']]
            assert f['side']=='sell' and f['ts']>entry_ts and f['ts']>=o['due_ts']
            assert o['intent']=='risk'
            used[(e.ts,f['price_cents'])]+=f['quantity']
            assert used[(e.ts,f['price_cents'])]<=sum(q for p,q in e.bids if p==f['price_cents'])
    equity=[0]+[x[1] for x in result['curve']]
    peak=dd=0
    for value in equity:
        peak=max(peak,value);dd=max(dd,peak-value)
    assert dd==round(result['summary']['max_drawdown_cny']*100)
    assert equity[-1]==round(result['summary']['pnl_cny']*100)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((BASE/'manifest_sha256.json').read_text(encoding='utf-8'))
    for filename,expected in manifest.items():assert digest(BASE/filename)==expected,filename
    base_matrix=json.loads((BASE/'matrix.json').read_text(encoding='utf-8'))
    for path,expected in base_matrix['source_hashes'].items():assert digest(ROOT/path)==expected,path
    details=json.loads((ARCHIVE/'options_market_20260909_snapshot.json').read_text(encoding='utf-8'))['details']
    sources=[Path(__file__),ROOT/'src/zhaiquant/option_guard_research.py',ROOT/'scripts/audit_option_cycle_losses.py',
             ROOT/'tests/test_option_guard_research.py',ROOT/'scripts/probe_option_top_cycle.py',
             ROOT/'src/zhaiquant/option_top_cycle_research.py']
    source_hashes={str(p.relative_to(ROOT)):digest(p) for p in sources}
    (out/'prerun_contract.json').write_text(json.dumps(dict(family=FAMILY,profiles=PROFILES,modes=MODES,
        fees_cents=[0,300,500],capacity=1,source_hashes=source_hashes,
        baseline_manifest_sha256=digest(BASE/'manifest_sha256.json')),ensure_ascii=False,indent=2),encoding='utf-8')
    summaries=[];metadata=[];prefix_count=baseline_count=0
    for code in NAMES:
        events,meta=load(code,details[code]);metadata.append(meta)
        assert meta['sha256']==next(m['sha256'] for m in base_matrix['metadata'] if m['code']==code)
        ff=features(events)
        for mode in MODES:
            for fee in [0,300,500]:
                old=json.load(gzip.open(BASE/f'{code}_{mode}_q1_f{fee}.json.gz','rt',encoding='utf-8'))
                for profile in PROFILES:
                    result=run(events,code=code,mode=mode,profile=profile,fee_cents=fee,feature_rows=ff)
                    audit_extra(result,events)
                    if profile=='control':
                        for key in ['orders','fills','cycles','curve']:assert clean(result[key])==clean(old[key]),(code,mode,fee,key)
                        for key,value in old['summary'].items():
                            if key not in ('model_id','name'):assert result['summary'][key]==value,(code,mode,fee,key)
                        baseline_count+=1
                    if fee==300 and mode in ('improve_l1','improve_single_d500'):
                        for end in [len(events)//3,2*len(events)//3]:
                            prefix=run(events[:end],code=code,mode=mode,profile=profile,fee_cents=fee)
                            ts=events[end-1].ts
                            for key,field in [('fills','ts'),('orders','created_ts')]:
                                assert prefix[key]==[x for x in result[key] if x[field]<=ts]
                            assert prefix['curve']==result['curve'][:end]
                            assert prefix['decisions']==[x for x in result['decisions'] if x[0]<=ts]
                            prefix_count+=1
                    s=result['summary'];s['name']=NAMES[code]
                    s['delta_pnl_cny']=s['pnl_cny']-old['summary']['pnl_cny']
                    s['delta_gross_cny']=(s['realized_gross_cny']+s['tail_gross_cny']-
                                           old['summary']['realized_gross_cny']-old['summary']['tail_gross_cny'])
                    s['saved_fees_cny']=old['summary']['fees_cny']-s['fees_cny']
                    assert abs(s['delta_pnl_cny']-s['delta_gross_cny']-s['saved_fees_cny'])<1e-8
                    buys={(f['ts'],f['price_cents']) for f in result['fills'] if f['side']=='buy'}
                    omitted=[c for c in old['cycles'] if (c['entry_ts'],c['entry_price_cents']) not in buys]
                    s['old_winning_entries_absent']=sum(c['net_cents']>0 for c in omitted)
                    s['old_losing_entries_absent']=sum(c['net_cents']<0 for c in omitted)
                    s['old_absent_cycle_net_cny']=sum(c['net_cents'] for c in omitted)/100
                    summaries.append(s)
                    with gzip.open(out/f'{code}_{profile}_{mode}_q1_f{fee}.json.gz','wt',encoding='utf-8',compresslevel=4) as stream:
                        json.dump(result,stream,ensure_ascii=False,separators=(',',':'))
        selected=[s for s in summaries if s['code']==code and s['mode']=='improve_single_d500' and s['fee_per_side_cny']==3]
        print(code,[(s['profile'],s['pnl_cny'],s['complete_cycles'],s['max_drawdown_cny']) for s in selected],flush=True)
    for path,expected in source_hashes.items():assert digest(ROOT/path)==expected,path
    for filename,expected in manifest.items():assert digest(BASE/filename)==expected,filename
    data=dict(family=FAMILY,date='2026-09-09',summaries=summaries,metadata=metadata,source_hashes=source_hashes,
        baseline_accounts_verified=baseline_count,baseline_files_verified=len(manifest),prefix_checks=prefix_count,
        audited_accounts=len(summaries),reference_tree='51a3093b6d08a0bc99098e5cacd6a1fd803e15d1',
        notes=['Development sample reuse, not out of sample.',
               'Each security has independent 10,000 CNY cash; sums are 90,000 CNY independent-account diagnostics.',
               'Omitted old entries describe path differences, not isolated causal PnL attribution.',
               'Own option midpoint is a risk signal, not an external fair value.',
               'L1, single, delays and queue are scenarios, not true execution or profit bounds.',
               'Per-side 3/5 CNY are hypothetical. All PnL includes tail bid marks.'])
    (out/'matrix.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print('VERIFIED',len(summaries),'accounts',baseline_count,'baseline paths',prefix_count,'prefixes',flush=True)


if __name__=='__main__':main()
