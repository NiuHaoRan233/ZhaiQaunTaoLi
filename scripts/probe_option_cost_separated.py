"""Recompute fixed admission rules from market data, charging the corrected fee."""
import argparse
import gzip
import json
from pathlib import Path
import sys

from probe_option_top_cycle import ROOT, ARCHIVE, NAMES, load, digest
from probe_option_guard import clean,audit_extra
from zhaiquant.option_guard_cost_separated import FAMILY,features,run
from zhaiquant.option_top_cycle_research import MODES

OLD=ARCHIVE/'大道至简过滤_20260909_v2'
COUPLED=ARCHIVE/'大道至简过滤_20260909_v2_f170'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    locks={p:json.loads((p/'manifest_sha256.json').read_text(encoding='utf-8')) for p in [OLD,COUPLED]}
    for folder,manifest in locks.items():
        for name,value in manifest.items():assert digest(folder/name)==value,name
    prior=json.loads((OLD/'matrix.json').read_text(encoding='utf-8'))
    details=json.loads((ARCHIVE/'options_market_20260909_snapshot.json').read_text(encoding='utf-8'))['details']
    sources=[Path(__file__),ROOT/'src/zhaiquant/option_guard_cost_separated.py',ROOT/'src/zhaiquant/option_guard_research.py',
             ROOT/'scripts/probe_option_top_cycle.py',ROOT/'scripts/probe_option_guard.py',ROOT/'tests/test_option_guard_cost_separated.py']
    hashes={str(p.relative_to(ROOT)):digest(p) for p in sources}
    contract=dict(family=FAMILY,fee_cents=170,quality_buffer_cents=600,profiles=['control','entry'],modes=MODES,
                  initial_cents=1_000_000,capacity=1,source_hashes=hashes,
                  purpose='Preserve original admission boundaries; correct accounting fees only. No threshold optimization.')
    (out/'prerun_contract.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf-8')
    sums=[];metadata=[];prefixes=paths=curve_points=0
    for c in NAMES:
        events,meta=load(c,details[c]);metadata.append(meta);ff=features(events)
        assert meta['sha256']==next(m['sha256'] for m in prior['metadata'] if m['code']==c)
        for mode in MODES:
            for profile in ['control','entry']:
                r=run(events,code=c,mode=mode,profile=profile,fee_cents=170,quality_buffer_cents=600,feature_rows=ff)
                audit_extra(r,events)
                old=json.load(gzip.open(OLD/f'{c}_{profile}_{mode}_q1_f300.json.gz','rt',encoding='utf-8'))
                assert clean(r['orders'])==clean(old['orders']),(c,mode,profile,'orders')
                def physical(f):return {k:v for k,v in f.items() if k not in ['model_id','fee_cents','cash_cents']}
                assert [physical(f) for f in r['fills']]==[physical(f) for f in old['fills']],(c,mode,profile,'fills')
                assert r['decisions']==[(t,tuple(why)) for t,why in old['decisions']]
                units=0;i=0
                for newpoint,oldpoint in zip(r['curve'],old['curve']):
                    while i<len(r['fills']) and r['fills'][i]['ts']<=newpoint[0]:
                        units+=r['fills'][i]['quantity'];i+=1
                    assert newpoint[0]==oldpoint[0] and newpoint[2]==oldpoint[2]
                    assert newpoint[1]==oldpoint[1]+130*units
                curve_points+=len(r['curve']);paths+=1
                if profile=='entry':
                    for end in [len(events)//3,2*len(events)//3]:
                        part=run(events[:end],code=c,mode=mode,profile=profile,fee_cents=170,quality_buffer_cents=600)
                        ts=events[end-1].ts
                        for key,field in [('fills','ts'),('orders','created_ts')]:
                            assert part[key]==[x for x in r[key] if x[field]<=ts]
                        assert part['curve']==r['curve'][:end]
                        prefixes+=1
                coupled=json.load(gzip.open(COUPLED/f'{c}_{profile}_{mode}_q1_f170.json.gz','rt',encoding='utf-8'))
                s=r['summary'];b=old['summary']
                s.update(name=NAMES[c],old_3cny_pnl=b['pnl_cny'],old_3cny_fees=b['fees_cny'],
                         fee_saving_cny=round(b['fees_cny']-s['fees_cny'],2),coupled_170_pnl=coupled['summary']['pnl_cny'],
                         original_orders_and_fills_preserved=True,gross_including_tail_cny=s['realized_gross_cny']+s['tail_gross_cny'])
                assert abs(s['pnl_cny']-b['pnl_cny']-s['fee_saving_cny'])<1e-8
                sums.append(s)
                with gzip.open(out/f'{c}_{profile}_{mode}_q1_b600_f170.json.gz','wt',encoding='utf-8',compresslevel=4) as f:
                    json.dump(r,f,ensure_ascii=False,separators=(',',':'))
        print(c,[(s['mode'],s['pnl_cny'],s['complete_cycles']) for s in sums if s['code']==c and s['profile']=='entry'],flush=True)
    for name,value in hashes.items():assert digest(ROOT/name)==value,name
    for folder,manifest in locks.items():
        for name,value in manifest.items():assert digest(folder/name)==value,name
    data=dict(**contract,date='2026-09-09',summaries=sums,metadata=metadata,audited_accounts=len(sums),
              original_paths_verified=paths,prefix_checks=prefixes,equity_points_vs_parent=curve_points,
              old_files_verified=sum(map(len,locks.values())))
    (out/'matrix.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print('VERIFIED',len(sums),'accounts',paths,'old paths',prefixes,'prefixes',curve_points,'curve points',flush=True)


if __name__=='__main__':main()
