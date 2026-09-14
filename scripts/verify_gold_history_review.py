"""Reconcile forensic tables and diagnostic cash paths without rerunning policies."""
from pathlib import Path
from html.parser import HTMLParser
from collections import defaultdict
from review_gold_history import OUT,BASE,DATES,FIXED
from probe_commodity_capital import ROOT,read,write,unpack,digest
from report_gold_history_review import fmt


class Parser(HTMLParser):
    def __init__(self):super().__init__();self.tables=[];self.table=None;self.row=None;self.cell=None
    def handle_starttag(self,t,attrs):
        if t=='table':self.table=[]
        if t=='tr':self.row=[]
        if t=='td':self.cell=''
    def handle_data(self,d):
        if self.cell is not None:self.cell+=d
    def handle_endtag(self,t):
        if t=='td':self.row.append(self.cell);self.cell=None
        if t=='tr' and self.row:self.table.append(self.row)
        if t=='table':self.tables.append(self.table);self.table=None


def main():
    report=OUT/'黄金期权_盈利日与亏损日逐笔复盘.html';parser=Parser();parser.feed(report.read_text('utf-8'))
    daily=read(OUT/'daily_attribution.json');acs=read(OUT/'accounts.json');total_cycles=sum(x['cycles'] for x in acs.values())
    assert total_cycles==92786 and len(acs)==1184
    for v in acs.values():
        assert v['invalid_books']==0
        assert abs(v['entry_edge']+v['holding_mid_move']+v['exit_edge']-v['fees_cny']-v['net_cny'])<1e-6
        if v['status']=='complete':assert abs(v['net_cny']-v['expected_pnl'])<1e-6
    for cells,date in zip(parser.tables[0],DATES):
        v=daily[f'{date}_fusion_throttle_long_through0']
        assert cells==[date,str(v['cycles']),fmt(v['entry_edge']),fmt(v['holding_mid_move']),fmt(v['exit_edge']),fmt(-v['fees_cny']),fmt(v['net_cny'])]
    diag=read(OUT/'entry_cutoff60/portfolios.json');old=read(BASE/'portfolios.json')
    ix=0
    for profile,label in [('trend_long','趋势多头'),('cancel_switch','增强择向')]:
        for t in (0,1):
            cells=parser.tables[1][ix];ix+=1;vals=[diag[f'{d}_{profile}_through{t}']['pnl_cny'] for d in DATES];before=old[f'v02_{profile}_through{t}']['pnl_cny']
            assert cells[1:]==[fmt(v) for v in vals]+[fmt(sum(vals)),fmt(before),fmt(sum(vals)-before)]
    for cells in parser.tables[2]:
        label=cells[0];ps=[x for x in read(OUT/'policy_pairs.json') if x['label']==label]
        assert cells[1]==fmt(sum(p['pnl_delta'] for p in ps if p['through']==0))
        assert cells[2]==fmt(sum(p['pnl_delta'] for p in ps if p['through']==1))
    appendix=parser.tables[3:15];checked_rows=0
    for table_index,table in enumerate(appendix):
        assert len(table)==16
        for cells in table:
            checked_rows+=1;date,code,fill,state=cells[:4]
            # Appendix table order follows FOCUS; derive the case by its table index.
            from review_gold_history import FOCUS
            case=FOCUS[table_index]
            s=next(v for v in acs.values() if (v['date'],v['code'],v['through'],v['case'])==(date,code,int(fill=='严格'),case))
            assert state==s['status']
            assert cells[5:]==[fmt(s[f]) for f in ['entry_edge','holding_mid_move','exit_edge','fees_cny','net_cny']]
    accounts=0;removed=defaultdict(list);added=defaultdict(list);boundary_count=0
    for path in (OUT/'entry_cutoff60').glob('*.json.gz'):
        r=unpack(path);s=r['summary'];accounts+=1;cash=25000000;pos=0;fees=0
        for fill in r['fills']:
            d=1 if fill['side']=='buy' else -1;cash-=d*fill['price_cents']+170;pos+=d;fees+=170
            assert (cash,pos)==(fill['cash_cents'],fill['inventory'])
        assert pos==0 and cash-25000000==round(s['pnl_cny']*100)==sum(c['net_cents'] for c in r['cycles'])
        assert fees==round(s['fees_cny']*100)
        assert len(r['boundaries'])==3 and all(b['inventory']==0 and b['pending_order'] is None for b in r['boundaries']);boundary_count+=3
        if s['profile']=='cancel_switch':
            t=int(s['strict_through']);removed[t].extend(r['diagnostic_diff']['removed']);added[t].extend(r['diagnostic_diff']['added'])
    for t in (0,1):assert len(removed[t])==6 and not added[t] and sum(c['net_cents'] for c in removed[t])==-204040
    assert accounts==32 and boundary_count==96
    for p,h in read(BASE/'result_manifest.json').items():assert digest(ROOT/p)==h,p
    for p,h in read(OUT/'entry_cutoff60/manifest.json').items():assert digest(ROOT/p)==h,p
    proof=dict(status='passed',frozen_accounts=1184,closed_cycles_with_repetition=total_cycles,
        missing_fresh_future_endpoints=sum(v['missing_future'] for v in acs.values()),
        price_identities=True,full_and_stopped_accounts_separated=True,
        diagnostic_accounts=accounts,diagnostic_flat_boundaries=boundary_count,enhanced_removed_cycles_per_fill_assumption=6,
        enhanced_removed_net_cny=-2040.4,enhanced_added_cycles=0,
        checked_primary_tables=3,checked_account_table_rows=checked_rows,
        gold_tests=71,old_hashes_unchanged=True,
        image_qa='3 PNGs visually checked; font issue corrected',html_qa='table values parsed; no browser screenshot')
    write(OUT/'verification.json',proof)
    scripts=['review_gold_history.py','review_gold_history_cases.py','probe_gold_break_entry_diagnostic.py','report_gold_history_review.py','verify_gold_history_review.py']
    sources=[ROOT/'scripts'/f for f in scripts]+[ROOT/'tests/test_gold_break_entry_diagnostic.py']
    write(OUT/'sources.json',{str(p.relative_to(ROOT)):digest(p) for p in sources})
    write(OUT/'manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.rglob('*') if p.is_file() and p!=OUT/'manifest.json'})
    print(proof,flush=True)


if __name__=='__main__':main()
