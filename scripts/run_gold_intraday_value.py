"""One-command reproduction of the frozen research strategy0.1 and its checks."""
from pathlib import Path
import argparse
from probe_gold_direction import OUT as V1,inputs,CODES,audit
from probe_gold_direction_timing import OUT as V2
from probe_gold_long_refinement import OUT as V3
from probe_gold_aligned_value import OUT as V4
from probe_gold_fast_validation import OUT as V5,replay
from probe_commodity_capital import ROOT,WORK,read,write,pack,unpack,digest

CONFIG=WORK/'gold_intraday_strategy/strategy.json'
OUT=WORK/'reports/gold_intraday_value_0_1_20260911'
EXPECTED_CONFIG={
    'schema_version':1,'public_name':'黄金期权日内估值做市0.1',
    'family':'probe_gold_fast_validation_20260913_v5','date':'20260911',
    'codes':['au2610C960.SF','au2610C952.SF'],'capital_per_code_cny':250000,
    'max_contracts':1,'fee_per_side_cny':1.7,'order_delay_ms':0,'future_signal_delay_ms':0,
    'fast_iv_seconds':10,'slow_iv_seconds':60,'direction':'long_only',
    'reference':'source_aligned_min_fast_slow_iv','exit':'bounded_patient',
    'primary_settlement':'entry_cost_virtual_at_every_break',
    'market_comparison_close_before_seconds':5,'run_strict_through_comparison':True,
    'execution':'offline_replay_only',
}


def validate_config(cfg):
    if cfg!=EXPECTED_CONFIG:raise ValueError('Frozen strategy configuration differs; changed decisions require a new registered model')


def main():
    parser=argparse.ArgumentParser(description='复现黄金期权日内估值做市0.1，只有本地模拟，不发送订单。')
    parser.add_argument('--config',type=Path,default=CONFIG);args=parser.parse_args();cfg=read(args.config)
    validate_config(cfg)
    assert cfg['codes']==list(CODES) and cfg['fast_iv_seconds']==10 and cfg['slow_iv_seconds']==60
    assert cfg['date']=='20260911' and cfg['fee_per_side_cny']==1.7 and cfg['execution']=='offline_replay_only'
    checked={}
    for folder in (V1,V2,V3,V4,V5):
        manifest=read(folder/'result_manifest.json');plan=read(folder/'plan.json')
        for p,h in {**manifest,**plan.get('sources',{}),**plan.get('inputs',{})}.items():
            assert digest(ROOT/p)==h,(p,'frozen dependency changed');checked[p]=h
    OUT.mkdir(exist_ok=True);summaries={}
    for code in CODES:
        es,fs,detail=inputs(code,0)
        for through in (False,True):
            for settlement in ('cost','market'):
                key=f'{code}_fast10_through{int(through)}_{settlement}_d0'
                r=replay(es,fs,code,settlement,detail,10,through,0);audit(r)
                assert r==unpack(V5/f'{key}.json.gz'),key
                pack(OUT/f'{key}.json.gz',r);summaries[key]=r['summary']
                print(code,'严格穿价' if through else '正常末价证据',settlement,
                    f"净收益{r['summary']['pnl_cny']:.2f}元，闭环{r['summary']['complete_cycles']}笔",flush=True)
    write(OUT/'results.json',summaries)
    write(OUT/'reproduction_verification.json',dict(status='passed',configuration_sha256=digest(args.config),
        source_sha256=digest(Path(__file__)),account_count=8,boundary_count=24,
        complete_equality_to_frozen_ledgers=True,independent_cash_fees_verified=True,
        checked_files=len(checked),no_broker_or_market_connection=True))
    write(OUT/'source_manifest.json',checked)
    write(OUT/'artifact_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='artifact_manifest.json'})
    print('复现完成：',OUT)


if __name__=='__main__':main()
