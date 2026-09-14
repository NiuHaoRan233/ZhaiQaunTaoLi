"""Fee-only extension of frozen option models, using the user's provisional 1.7 CNY."""
import argparse
import gzip
import json
from pathlib import Path
import sys

from probe_option_top_cycle import ROOT, ARCHIVE, NAMES, load, digest
from probe_option_guard import clean, audit_extra
from zhaiquant.option_guard_research import FAMILY, PROFILES, features, run
from zhaiquant.option_top_cycle_research import MODES, run as baseline

OLD=ARCHIVE/'大道至简过滤_20260909_v2'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    frozen=json.loads((OLD/'manifest_sha256.json').read_text(encoding='utf-8'))
    for name,value in frozen.items():assert digest(OLD/name)==value,name
    old_matrix=json.loads((OLD/'matrix.json').read_text(encoding='utf-8'))
    for name,value in old_matrix['source_hashes'].items():assert digest(ROOT/name)==value,name
    sources=[Path(__file__),ROOT/'src/zhaiquant/option_guard_research.py',ROOT/'src/zhaiquant/option_top_cycle_research.py',
             ROOT/'scripts/probe_option_top_cycle.py',ROOT/'scripts/probe_option_guard.py']
    hashes={str(p.relative_to(ROOT)):digest(p) for p in sources}
    contract=dict(family=FAMILY,fee_cents=170,profiles=PROFILES,modes=MODES,initial_cents=1_000_000,capacity=1,
        fee_status='User recalled 1.7 CNY per contract per side; provisional all-in input, not verified broker statement.',
        source_hashes=hashes,old_manifest_sha256=digest(OLD/'manifest_sha256.json'))
    (out/'prerun_contract.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf-8')
    details=json.loads((ARCHIVE/'options_market_20260909_snapshot.json').read_text(encoding='utf-8'))['details']
    summaries=[];metadata=[];prefixes=references=0
    for code in NAMES:
        events,meta=load(code,details[code]);metadata.append(meta);ff=features(events)
        assert meta['sha256']==next(m['sha256'] for m in old_matrix['metadata'] if m['code']==code)
        for mode in MODES:
            original=baseline(events,code=code,mode=mode,fee_cents=170)
            audit_extra(original,events)
            for profile in PROFILES:
                result=run(events,code=code,mode=mode,profile=profile,fee_cents=170,feature_rows=ff)
                audit_extra(result,events)
                if profile=='control':
                    for key in ['orders','fills','cycles','curve']:assert clean(result[key])==clean(original[key]),(code,mode,key)
                    references+=1
                if mode in ['improve_l1','improve_single_d500']:
                    for end in [len(events)//3,2*len(events)//3]:
                        part=run(events[:end],code=code,mode=mode,profile=profile,fee_cents=170)
                        ts=events[end-1].ts
                        for key,field in [('fills','ts'),('orders','created_ts')]:
                            assert part[key]==[x for x in result[key] if x[field]<=ts]
                        assert part['curve']==result['curve'][:end]
                        prefixes+=1
                old=json.load(gzip.open(OLD/f'{code}_{profile}_{mode}_q1_f300.json.gz','rt',encoding='utf-8'))
                s=result['summary'];s['name']=NAMES[code];b=old['summary']
                # Algebraic old-path attribution only. This is not another simulated account.
                fixed=round((round(b['pnl_cny']*100)+130*b['filled_contract_sides'])/100,2)
                fill_path=lambda rows:[(f['ts'],f['side'],f['price_cents'],f['quantity'],f['kind']) for f in rows]
                same_actions=fill_path(result['fills'])==fill_path(old['fills'])
                s.update(old_3cny_pnl=b['pnl_cny'],old_3cny_fees=b['fees_cny'],
                         fixed_old_path_fee170_pnl=fixed,fixed_old_path_fee_saving_cny=round(1.3*b['filled_contract_sides'],2),
                         rerun_minus_fixed_old_path_cny=round(s['pnl_cny']-fixed,2),
                         delta_to_3cny=round(s['pnl_cny']-b['pnl_cny'],2),same_fills_as_fee300=same_actions,
                         old_3cny_complete_cycles=b['complete_cycles'],old_3cny_fill_sides=b['filled_contract_sides'],
                         gross_including_tail_cny=s['realized_gross_cny']+s['tail_gross_cny'])
                summaries.append(s)
                with gzip.open(out/f'{code}_{profile}_{mode}_q1_f170.json.gz','wt',encoding='utf-8',compresslevel=4) as f:
                    json.dump(result,f,ensure_ascii=False,separators=(',',':'))
        print(code,[(s['profile'],s['pnl_cny'],s['fees_cny'],s['complete_cycles']) for s in summaries if s['code']==code and s['mode']=='improve_single_d500'],flush=True)
    for name,value in hashes.items():assert digest(ROOT/name)==value,name
    for name,value in frozen.items():assert digest(OLD/name)==value,name
    data=dict(**contract,date='2026-09-09',summaries=summaries,metadata=metadata,audited_accounts=len(summaries),
              baseline_accounts_verified=references,prefix_checks=prefixes,old_files_verified=len(frozen))
    (out/'matrix.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print('VERIFIED',len(summaries),'accounts',references,'baseline references',prefixes,'prefixes',flush=True)


if __name__=='__main__':main()
