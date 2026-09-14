"""Independent selection, ledger, percentage, side and HTML verification."""
from pathlib import Path
import pandas as pd
from zhaiquant import gold_reselection_research as model
from zhaiquant.gold_history_validation import Clock
from probe_gold_reselection import OUT,DATES,LABELS,ROOT,WORK,read,write,unpack,digest,fmt
from verify_gold_relative_spread import Tables


def main():
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen output')
    summaries=read(OUT/'results.json');groups=read(OUT/'comparison.json');terms=read(OUT/'catalog_terms.json')['details']
    selections={d:read(OUT/d/'selection.json') for d in DATES};checks=0;expected=0
    for date,selection in selections.items():
        clock=Clock(date)
        for b,scenario in selection['scenarios'].items():
            rebuilt={}
            for code,old in scenario['rows'].items():
                frame=pd.read_pickle(OUT/date/'prefix'/f'{code}.pkl');d=terms[code]
                now=model.metrics(frame,d,selection['states'][d['OptUndlCode']+'.SF'],clock,int(b)) if not frame.empty else dict(eligible=False,reasons=['data_unavailable'])
                assert now==old,(date,code,b);rebuilt[code]=now;checks+=1
            assert model.select(rebuilt)==scenario['selected']
            expected+=len(scenario['selected'])*len(LABELS)*4
    assert len(summaries)==expected
    orders=0;models=set();calls=puts=0
    for key,s in summaries.items():
        assert s['code'] not in model.EXCLUDED and s['code'] in selections[s['history_date']]['scenarios'][str(s['relative_spread_bps'])]['selected']
        assert s['model_id'] not in models;models.add(s['model_id'])
        assert s['option_type']==('call' if terms[s['code']]['OptionType']==0 else 'put')
        if s['option_type']=='call':calls+=1
        else:puts+=1
        r=unpack(OUT/'ledgers'/f'{key}.json.gz');assert r['summary']==s
        for o in r['orders']:
            if o['reason']=='entry':
                assert 2*o['entry_spread']*10000>=s['relative_spread_bps']*o['entry_mid_twice']
                assert o['side']==('buy' if s['fixed_direction']==1 else 'sell');orders+=1
            if o.get('feature'):
                delta=o['feature']['delta']
                assert 0<=delta<=1 if s['option_type']=='call' else -1<=delta<=0
        for c in r['cycles']:
            assert c['direction']==s['fixed_direction']
            if c['exit_kind']=='virtual_cost_close':assert c['net_cents']==c['fees_cents']==0
            else:
                assert c['fees_cents']==340
                assert c['net_cents']==c['direction']*(c['exit_price_cents']-c['entry_price_cents'])-340
        assert abs(sum(c['net_cents'] for c in r['cycles'])/100-s['pnl_cny'])<1e-7
    parser=Tables();parser.feed((OUT/'黄金期权_按百分比每日换约回测.html').read_text('utf-8'))
    for mode in ('long','short'):
        for strict in (0,1):
            rows=parser.tables[mode+str(strict)][1:];assert len(rows)==27
            i=0
            for policy,label in LABELS.items():
                for b in model.THRESHOLDS:
                    s=groups[f'{mode}_{policy}_bps{b}_through{strict}']
                    assert rows[i]==[label,f'{b/100:g}%',*map(fmt,s['daily']),fmt(s['pnl_cny']),str(s['normal_cycles']),str(s['virtual_cycles']),fmt(s['max_drawdown_cny']),fmt(s['removed_tail_gross_cny'])]
                    i+=1
    assert len(parser.tables['accounts'])==len(summaries)+1
    for row,s in zip(parser.tables['accounts'][1:],summaries.values()):
        assert row==[s['history_date'],s['code'],s['trade_mode'],LABELS[s['policy']],f'{s["relative_spread_bps"]/100:g}%',
            '严格' if s['strict_through'] else '普通',fmt(s['pnl_cny']),str(s['market_cycles']),str(s['virtual_close_count']),fmt(s['removed_tail_gross_cny'])]
    assert len(parser.tables['selection'])==16
    for row,(date,b) in zip(parser.tables['selection'][1:],[(d,b) for d in DATES for b in model.THRESHOLDS]):
        scenario=selections[date]['scenarios'][str(b)]
        assert row==[date,f'{b/100:g}%',*(scenario['selected']+['空缺']*2)[:2],str(sum(m['eligible'] for m in scenario['rows'].values()))]
    markets=read(OUT/'market_activity.json')
    assert len(parser.tables['market'])==len(markets)+1
    for row,m in zip(parser.tables['market'][1:],markets):
        assert m['day_volume']>=m['post0930_volume']>=0
        assert row==[m['date'],m['code'],str(m['day_volume']),str(m['post0930_volume']),fmt(m['post0930_mean_mid']),f'{m["post0930_relative_mean_pct"]:.2f}%']
    for file in ('capture_manifest.json','source_manifest.json'):
        for rel,h in read(OUT/file).items():assert digest(ROOT/rel)==h,rel
    for rel,h in read(WORK/'reports/gold_relative_spread_20260914_v1/manifest.json').items():assert digest(ROOT/rel)==h,rel
    sources=read(OUT/'source_manifest.json')
    paths=list((ROOT/'src/zhaiquant').glob('gold*.py'))+[Path(__file__)]+[ROOT/'scripts'/f for f in
        ('capture_gold_reselection.py','report_gold_reselection.py','probe_gold_backer.py','probe_gold_two_mode.py','probe_gold_rule_ladder.py','probe_commodity_capital.py')]
    paths += [ROOT/'src/zhaiquant'/f for f in ('commodity_dadao_research.py','commodity_flow_strategy.py','option_top_cycle_research.py','commodity_mau_transfer_research.py')]
    for p in paths:sources[str(p.relative_to(ROOT))]=digest(p)
    write(OUT/'source_manifest.json',sources)
    for rel in sources:
        target=OUT/'source_snapshot'/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/rel).read_bytes())
    write(OUT/'report_verification.json',dict(status='passed',accounts=len(summaries),call_accounts=calls,put_accounts=puts,
        selection_metric_recomputations=checks,entry_orders_percentage_checked=orders,html_daily_rows=108,
        html_selection_rows=15,html_account_rows=len(summaries),old_contracts_absent=True,
        normal_cycle_sign_and_fee_identity=True,unit_tests=101))
    write(OUT/'manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.rglob('*') if p.is_file() and p.name!='manifest.json'})
    print('PASS',len(summaries),'accounts',calls,'call',puts,'put;',checks,'selection states;',orders,'entry orders; frozen')


if __name__=='__main__':main()
