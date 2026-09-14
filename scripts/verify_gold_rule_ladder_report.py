"""Read rendered HTML tables back and reconcile against frozen ledger amounts."""
from html.parser import HTMLParser
from pathlib import Path
import math
from report_gold_rule_ladder import OUT,TITLE
from probe_gold_rule_ladder import OUT as V1
from probe_gold_rule_simplification import OUT as V2
from audit_gold_rule_ladder import OUT as AUDIT
from probe_commodity_capital import ROOT,read,write,unpack,digest


class Tables(HTMLParser):
    def __init__(self):super().__init__();self.tables=[];self.current=None;self.row=None;self.cell=None;self.images=0
    def handle_starttag(self,tag,attrs):
        if tag=='table':self.current=[]
        if tag=='tr':self.row=[]
        if tag=='td':self.cell=''
        if tag=='img':self.images+=1
    def handle_data(self,data):
        if self.cell is not None:self.cell+=data
    def handle_endtag(self,tag):
        if tag=='td':self.row.append(self.cell);self.cell=None
        if tag=='tr' and self.row:self.current.append(self.row);self.row=None
        if tag=='table':self.tables.append(self.current);self.current=None


def main():
    for folder,filename in [(V1,'result_manifest.json'),(V2,'result_manifest.json'),(AUDIT,'manifest.json')]:
        for p,h in read(folder/filename).items():assert digest(ROOT/p)==h
    path=OUT/(TITLE.replace('：','_')+'.html');text=path.read_text(encoding='utf-8');p=Tables();p.feed(text)
    overview=read(OUT/'overview.json');t=overview['tables']
    expected=[t['capital'],t['ladder']['1'],t['ladder']['-1'],t['removals'],t['simplification'],t['spreads'],t['finalists'],t['all_accounts'],t['trade_changes']]
    for actual,rows in zip(p.tables,expected):
        formatted=[[f'{v:,.2f}' if isinstance(v,(int,float)) else str(v) for v in row] for row in rows]
        assert actual==formatted
    # Independently map every summary table row to the persisted ledger summary.
    src={'v1':read(V1/'results.json'),'v2':read(V2/'results.json')}
    for row in t['all_accounts']:
        tag,key=row[0].split(' ',1);s=src[tag][key]
        for i,field in [(1,'pnl_cny'),(2,'realized_gross_cny'),(3,'fees_cny'),(5,'max_drawdown_cny'),
            (6,'worst_cycle_cny'),(7,'best_cycle_cny'),(8,'win_rate_pct'),(9,'median_hold_seconds'),
            (10,'max_hold_seconds'),(11,'peak_single_entry_premium_cny'),(12,'minimum_cash_for_all_entry_quotes_cny'),(13,'profit_without_best3_cny')]:
            assert row[i]==s[field]
        assert int(row[4])==s['complete_cycles']
    trades=unpack(OUT/'全部交易明细.json.gz')
    for tag,ss in src.items():
        for k,s in ss.items():
            cs=trades[tag+'_'+k]
            assert sum(c['net_cents'] for c in cs)==round(s['pnl_cny']*100)
            assert len(cs)==s['complete_cycles']
    # The eight visible full trade tables are also parsed, rather than counted only.
    j=9;visible=0
    for tag,case in [('v1','s00'),('v2','spread8'),('v1','s10'),('v2','core')]:
        for code in ('au2610C960.SF','au2610C952.SF'):
            cs=trades[f'{tag}_{code}_{case}_long_through0'];rows=p.tables[j];j+=1
            assert len(rows)==len(cs)
            for row,c in zip(rows,cs):
                assert float(row[1])==c['entry_price_cents']/100000
                assert float(row[3])==c['exit_price_cents']/100000
                assert round(float(row[6].replace(',',''))*100)==c['net_cents'];visible+=1
    assert len(t['all_accounts'])==224 and len(p.tables)==17 and p.images==2
    qa=dict(status='passed',rendered_tables=len(p.tables),embedded_images=p.images,summary_accounts=224,
        all_summary_fields_match_frozen_ledgers=True,all_cycle_money_reconciled=True,
        visible_trade_rows_checked=visible,source_hashes_unchanged=True,browser_screenshot_qa=False,
        png_visual_review='pending')
    write(OUT/'report_qa.json',qa);print(qa)


if __name__=='__main__':main()
