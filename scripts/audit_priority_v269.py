"""Freeze and verify the direct v2.63 development baseline for v2.69."""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from zhaiquant.maker_paper import REALTIME_COMPARISON_POLICIES


SOURCES = (
    'output/research/priority_v264r2_matrix_20260804_20260903.json',
    'output/research/priority_v264r2_full_20260904.json',
)
MANIFEST = 'output/research/priority_v269_predevelopment_manifest.json'
BASE = 'maker_priority_v2_63_candidate'
NEW = 'maker_priority_v2_69_candidate'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    path = Path(path)
    if path.exists():
        raise FileExistsError('Preserve prior evidence: '+str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print(str(path),sha(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('freeze','verify'))
    parser.add_argument('--matrix')
    parser.add_argument('--target')
    parser.add_argument('--output')
    args=parser.parse_args()
    if args.action=='freeze':
        assert NEW not in REALTIME_COMPARISON_POLICIES, 'Freeze before registering new code'
        save(MANIFEST,dict(baseline_model_id=BASE,
            profiles={k:asdict(v) for k,v in REALTIME_COMPARISON_POLICIES.items()},
            sha256={p:sha(p) for p in (*SOURCES,'src/zhaiquant/maker_paper.py',
                'config.toml','config.example.toml')},
            baseline_sources=list(SOURCES)))
        return
    if not args.matrix or not args.target or not args.output:
        parser.error('verify requires --matrix, --target and --output')
    manifest=read(MANIFEST)
    for path,digest in manifest['sha256'].items():
        if Path(path).name!='maker_paper.py':
            assert sha(path)==digest,path
    for model,profile in manifest['profiles'].items():
        current=json.loads(json.dumps(asdict(REALTIME_COMPARISON_POLICIES[model])))
        assert all(current[k]==v for k,v in profile.items()),model
        assert not current['enable_causal_ordinary_inventory_turnover'],model
    current=json.loads(json.dumps(asdict(REALTIME_COMPARISON_POLICIES[NEW])))
    expected=dict(manifest['profiles'][BASE],model_id=NEW,model_version='2.69-candidate',
                  parent_model_id=BASE,enable_causal_ordinary_inventory_turnover=True)
    # Later schema additions must remain disabled for this frozen candidate.
    expected.update(recognize_consumed_recovered_support=False,
                    quote_on_current_ordinary_recovery=False,
                    allow_horizontal_recovery_quote_with_bid_retreat=False)
    assert current==expected,'Candidate is not a one-control direct child of saved 2.63'
    baseline={}
    for source in SOURCES:
        report=read(source)
        assert report['parent_model_id']==BASE
        for cell in report['cells']:
            key=(cell['market_date'],cell['bond_code'])
            assert key not in baseline
            baseline[key]=cell
    matrix=read(args.matrix)
    assert matrix['parent_model_id']==BASE and matrix['candidate_model_id']==NEW
    cells=matrix['cells']
    assert {(c['market_date'],c['bond_code']) for c in cells}==baseline.keys()
    for cell in cells:
        saved=baseline[(cell['market_date'],cell['bond_code'])]
        for field in ('fills','fill_count','order_count','terminal_inventory','trading_pnl',
                      'path_bounds','weighted_customer_base_short_metrics','extra_inventory_metrics'):
            assert cell['parent_'+field]==saved['parent_'+field],(cell['market_date'],cell['bond_code'],field)
    assert all(c['queue_branch_identical'] and c['windfall_branch_identical'] for c in cells)
    target=read(args.target)
    case=next(c for c in target['cells'] if c['code']=='132026.SH' and c['date']=='2026-09-07')
    variant=case['variants'][NEW]
    fills=variant['fills']
    assert any(f['market_time']=='10:21:01' and f['side']=='buy' and
               abs(f['price']-136.707)<1e-9 for f in fills)
    assert any(f['market_time']=='10:21:10' and f['side']=='sell' and
               abs(f['price']-136.699)<1e-9 and f['inventory_after_bonds']==1000 for f in fills)
    checked=0
    for f in variant['frames']:
        if any(start<=f['time']<end for start,end in (
            ('10:22:52','10:24:49'),('10:28:10','10:28:46'))):
            assert sum(o['quantity'] for o in f['orders'] if o['side']=='sell')>=max(0,f['inventory']-1000),f['time']
            checked+=1
        if '10:29:00'<=f['time']<='10:32:22':
            assert f['inventory']==1000 and not f['orders'],f['time']
    daily=[]
    for day in sorted({c['market_date'] for c in cells}):
        row=dict(date=day,baseline_total=0,candidate_total=0,bonds={})
        for c in cells:
            if c['market_date']!=day:continue
            row['baseline_total']+=c['parent_trading_pnl']
            row['candidate_total']+=c['candidate_trading_pnl']
            row['bonds'][c['bond_code']]={k:c[k] for k in (
                'parent_trading_pnl','candidate_trading_pnl','trading_pnl_delta',
                'parent_terminal_inventory','candidate_terminal_inventory')}
        row['delta']=row['candidate_total']-row['baseline_total']
        daily.append(row)
    save(args.output,dict(baseline_model_id=BASE,candidate_model_id=NEW,
        baseline_saved_cells_exact=len(cells),old_profiles_unchanged=len(manifest['profiles']),
        direct_parent_profile_verified=True,target_exit_frames_checked=checked,
        branch_isolation=matrix['branch_isolation'],daily=daily,
        totals_by_code=matrix['totals_by_code'],
        sha256={p:sha(p) for p in (*SOURCES,MANIFEST,args.matrix,args.target,
                                 'src/zhaiquant/maker_paper.py')}))
    print(json.dumps(matrix['totals_by_code'],ensure_ascii=False))


if __name__=='__main__':
    main()
