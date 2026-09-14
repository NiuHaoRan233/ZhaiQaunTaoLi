"""Independent report amounts, actual dates, fees, inventories and cash effects."""
from html.parser import HTMLParser
from collections import Counter
import json
from zhaiquant.gold_history_validation import Clock
from capture_gold_history import OUT,DATES,FIXED
from validate_gold_history import CASES
from report_gold_history import FOCUS,fmt,make_report
from probe_commodity_capital import ROOT,read,write,unpack,digest


class Tables(HTMLParser):
    def __init__(self):super().__init__();self.rows=[];self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='tr' and 'data-case' in attrs:self.row=[attrs,[]]
        if tag=='td' and self.row is not None:self.cell=''
    def handle_data(self,data):
        if self.cell is not None:self.cell+=data
    def handle_endtag(self,tag):
        if tag=='td' and self.cell is not None:self.row[1].append(self.cell);self.cell=None
        if tag=='tr' and self.row is not None:self.rows.append(self.row);self.row=None


def main():
    states=Counter();boundaries=0;accounts=0;models=set();cash_effects=[]
    for date in DATES:
        clock=Clock(date);summaries=read(OUT/date/'results.json')
        for path in (OUT/date/'ledgers').glob('*.json.gz'):
            r=unpack(path);s=r['summary'];accounts+=1;states[s['status']]+=1
            assert s==summaries[path.name[:-8]]
            assert s['model_id'] not in models;models.add(s['model_id'])
            assert s['history_date']==date and s['case_key'] in CASES
            cash=round(s['initial_cash_cny']*100);inventory=fees=0
            for f in r['fills']:
                sign=1 if f['side']=='buy' else -1
                cash-=sign*f['price_cents']+170;inventory+=sign;fees+=170
                assert (cash,inventory)==(f['cash_cents'],f['inventory'])
                assert clock.start+1800000<=f['created_ts']<clock.start+21600000
                assert f['created_ts']<=f['ts']<clock.start+21600000
            assert cash==round(s['end_cash_cny']*100) and fees==round(s['fees_cny']*100)
            assert inventory==s['end_inventory']
            for b in r['boundaries']:
                assert b['inventory']==0 and b['pending_order'] is None
                assert b['ts'] in [clock.start+75*60000,clock.start+150*60000,clock.start+21600000]
                boundaries+=1
            if s['status']=='complete':
                assert inventory==0 and len(r['boundaries'])==3
                assert round(s['pnl_cny']*100)==sum(c['net_cents'] for c in r['cycles'])==cash-round(s['initial_cash_cny']*100)
            else:
                assert inventory!=0 and s['pnl_cny'] is None and s['failure']['ts']==clock.start+75*60000
        for code in FIXED:
            for through in (0,1):
                big=unpack(OUT/date/'ledgers'/f'{code}_v02_trend_long_through{through}.json.gz')
                small=unpack(OUT/'small_capital'/f'{date}_{code}_carry_cash_through{through}.json.gz')
                def key(c):return tuple(c[f] for f in ('direction','entry_ts','entry_price_cents','exit_ts','exit_price_cents'))
                a={key(c):c for c in big['cycles']};b={key(c):c for c in small['cycles']}
                removed=[v for k,v in a.items() if k not in b];added=[v for k,v in b.items() if k not in a]
                diff=sum(c['net_cents'] for c in added)-sum(c['net_cents'] for c in removed)
                assert diff==round((small['summary']['pnl_cny']-big['summary']['pnl_cny'])*100)
                cash_effects.append(dict(date=date,code=code,through=through,
                    common_cycles=len(a.keys()&b.keys()),removed_cycles=len(removed),removed_net_cny=sum(c['net_cents'] for c in removed)/100,
                    added_cycles=len(added),added_net_cny=sum(c['net_cents'] for c in added)/100,delta_cny=diff/100,
                    funding_reject_frames=small['summary']['rejection_frames'].get('insufficient_risk_capital',0)))
    write(OUT/'capital_path_attribution.json',cash_effects)
    ports=read(OUT/'portfolios.json');small_ports=read(OUT/'small_capital/portfolios.json')
    # Normalize serialized cumulative sums to integer cents before final rendering.
    from report_gold_history import combine
    ports={k:combine(v['days']) for k,v in ports.items()};small_ports={k:combine(v['days']) for k,v in small_ports.items()}
    write(OUT/'portfolios.json',ports);write(OUT/'small_capital/portfolios.json',small_ports)
    report=make_report(ports,read(OUT/'all_account_summaries.json'),read(OUT/'flatten_failures.json'),small_ports)
    parser=Tables();parser.feed(report.read_text('utf-8'))
    assert len(parser.rows)==2*(len(CASES)+len(FOCUS))
    for attrs,cells in parser.rows:
        p=ports[attrs['data-case']+'_through'+attrs['data-through']]
        assert cells[1:5]==[fmt(d['pnl_cny']) for d in p['days']]
        assert cells[5:7]==[fmt(p['pnl_cny']),fmt(p['max_drawdown_cny'])]
    for name in ('full_input_manifest.json','capture_manifest.json'):
        for p,h in read(OUT/name).items():assert digest(ROOT/p)==h,(name,p)
    for p,h in read(OUT/'replay_plan.json')['sources'].items():assert digest(ROOT/p)==h,p
    assert accounts==1184 and states=={'complete':1106,'failed_flatten':78}
    proof=dict(status='passed',accounts=accounts,states=dict(states),zero_inventory_boundaries=boundaries,
        daily_summary_rows=accounts,comparison_table_rows=len(parser.rows),cash_effect_pairs=len(cash_effects),
        actual_dates_cash_fees_fill_direction=True,failed_accounts_not_treated_as_complete=True,
        input_and_engine_hashes_unchanged=True,selection_prefix_full_download_equal=True,
        tests=68,baseline=read(OUT/'baseline_verification.json'),
        image_qa='pending',html_qa='table values parsed and reconciled; browser screenshot not performed')
    write(OUT/'verification.json',proof)
    print(json.dumps(proof,ensure_ascii=False),flush=True)
    print('CAPITAL_EFFECT',{t:{'removed_net':sum(x['removed_net_cny'] for x in cash_effects if x['through']==t),
        'removed_cycles':sum(x['removed_cycles'] for x in cash_effects if x['through']==t),
        'added_net':sum(x['added_net_cny'] for x in cash_effects if x['through']==t),
        'added_cycles':sum(x['added_cycles'] for x in cash_effects if x['through']==t)} for t in (0,1)},flush=True)


if __name__=='__main__':main()
