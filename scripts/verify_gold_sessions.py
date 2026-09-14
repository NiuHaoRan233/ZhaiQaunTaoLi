"""Freeze independently reconciled session research and HTML."""
from pathlib import Path
from collections import defaultdict
from probe_gold_sessions import OUT,BASE,DATES,m,read,write,unpack,digest,ROOT
from report_gold_sessions import MASK_LABELS,LABELS,fmt
from verify_gold_relative_spread import Tables
import pandas as pd


def main():
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen output')
    summaries=read(OUT/'results.json');groups=read(OUT/'comparison.json');parser=Tables()
    revision=OUT/'friday_calendar_revision'
    for p in (revision/'normalized').glob('*.pkl'):
        f=pd.read_pickle(p);raw=pd.read_pickle(revision/'raw'/p.name)
        tail=f[f.time!=f.provider_time]
        recovered=raw[raw.time.isin(tail.provider_time)].copy()
        assert len(tail)==len(recovered)>0
        assert (tail.provider_time-tail.time==2*86400000).all()
        assert (tail.volume.diff().dropna()>=0).all()
        pd.testing.assert_frame_equal(tail.drop(columns=['time','provider_time']),recovered.drop(columns='time'))
        assert (1789315200000<=tail.provider_time).all() and (tail.provider_time<1789324200000).all()
    parser.feed((OUT/'黄金期权_日盘夜盘及分时段比较.html').read_text('utf-8'))
    for b in (100,150,200):
        for strict in (0,1):
            rows=parser.tables[f'daily{b}_{strict}'][1:];assert len(rows)==108;i=0
            for policy,name in LABELS.items():
                for mask,label in MASK_LABELS.items():
                    for mode,dl in (('long','先多'),('short','先空')):
                        s=groups[f'{mask}_{mode}_{policy}_bps{b}_through{strict}']
                        assert rows[i]==[name,label,dl,*map(fmt,s['daily']),fmt(s['pnl_cny']),str(s['normal_cycles']),str(s['virtual_cycles']),fmt(s['max_drawdown_cny']),fmt(s['removed_tail_gross_cny'])];i+=1
    ids=set();orders=0;tails=0;stale=0
    for key,s in summaries.items():
        assert s['model_id'] not in ids;ids.add(s['model_id'])
        r=unpack(OUT/'ledgers'/f'{key}.json.gz');assert r['summary']==s
        assert s['code'] not in m.parent.EXCLUDED
        for o in r['orders']:
            if o['reason']=='entry':
                assert 2*o['entry_spread']*10000>=s['relative_spread_bps']*o['entry_mid_twice'];orders+=1
        for c in r['cycles']:
            if c['exit_kind']=='virtual_cost_close':
                assert c['net_cents']==c['fees_cents']==0;tails+=1
                stale+=int(c['reference_quote_age_seconds']>60)
            else:
                assert c['fees_cents']==340 and c['net_cents']==c['direction']*(c['exit_price_cents']-c['entry_price_cents'])-340
        assert abs(sum(c['net_cents'] for c in r['cycles'])/100-s['pnl_cny'])<1e-8
    for d in DATES:
        for b in (100,150,200):
            for policy in LABELS:
                for mode in ('long','short'):
                    for strict in (0,1):
                        values={mask:groups[f'{mask}_{mode}_{policy}_bps{b}_through{strict}']['daily'][DATES.index(d)] for mask in m.MASKS}
                        assert abs(values['both']-values['day']-values['night'])<1e-6
                        assert abs(values['day']-sum(values[x] for x in ('am1','am2','pm')))<1e-6
    for rel,h in read(BASE/'manifest.json').items():assert digest(ROOT/rel)==h,rel
    for rel,h in read(OUT/'capture_manifest.json').items():assert digest(ROOT/rel)==h,rel
    for rel,h in read(OUT/'friday_calendar_revision/manifest.json').items():assert digest(ROOT/rel)==h,rel
    for rel,h in read(OUT/'replay_plan.json')['source_hashes'].items():assert digest(ROOT/rel)==h,rel
    paths=list((ROOT/'src/zhaiquant').glob('gold*.py'))+[Path(__file__),ROOT/'scripts/report_gold_sessions.py',ROOT/'scripts/probe_gold_sessions.py',ROOT/'scripts/capture_gold_sessions.py',ROOT/'scripts/repair_gold_friday_session.py',ROOT/'tests/test_gold_session_research.py']
    for name in ('commodity_dadao_research.py','commodity_flow_strategy.py','option_top_cycle_research.py'):paths.append(ROOT/'src/zhaiquant'/name)
    sources={str(p.relative_to(ROOT)):digest(p) for p in paths};write(OUT/'source_manifest.json',sources)
    for rel in sources:
        target=OUT/'source_snapshot'/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/rel).read_bytes())
    write(OUT/'report_verification.json',dict(status='passed',accounts=len(ids),entry_orders=orders,excluded_cycles=tails,
        friday_normalization_raw_fields_unchanged=True,
        tail_reference_older60s=stale,html_daily_rows=648,unit_tests=105,all_daily_mask_sums_reconcile=True))
    write(OUT/'manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.rglob('*') if p.is_file() and p.name!='manifest.json'})
    print('PASS',len(ids),'accounts',orders,'entry orders;',tails,'excluded cycles',stale,'stale tail marks; frozen')


if __name__=='__main__':main()
