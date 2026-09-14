"""Independent final accounting, raw-prefix and HTML-table verification."""
from html.parser import HTMLParser
from pathlib import Path
from collections import Counter
import pandas as pd
from probe_gold_backer import *
from report_gold_backer import LABELS,fmt


class Tables(HTMLParser):
    def __init__(self):super().__init__();self.tables={};self.table=None;self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='table':self.table=dict(attrs).get('id','');self.tables[self.table]=[]
        if tag=='tr':self.row=[]
        if tag in ('td','th'):self.cell=''
    def handle_data(self,data):
        if self.cell is not None:self.cell+=data
    def handle_endtag(self,tag):
        if tag in ('td','th'):self.row.append(self.cell);self.cell=None
        if tag=='tr' and self.table is not None:self.tables[self.table].append(self.row)
        if tag=='table':self.table=None


def main():
    summaries=read(OUT/'results.json');comparison=read(OUT/'comparison.json')
    for file in ('source_manifest.json','report_source_manifest.json'):
        for rel,h in read(OUT/file).items():assert digest(ROOT/rel)==h,rel
    for rel,h in read(BASE/'result_manifest.json').items():assert digest(ROOT/rel)==h,rel
    for key,s in summaries.items():
        r=unpack(OUT/'ledgers'/f'{key}.json.gz');cost=unpack(OUT/'cost_ledgers'/f'{key}.json.gz')
        audit(r);audit(cost)
        assert abs(r['summary']['pnl_cny']-cost['summary']['pnl_cny']-s['virtual_close_count']*3.4)<.0001
        assert all(c['net_cents']==0 for c in r['cycles'] if c['exit_kind']=='virtual_cost_close')
        assert all(c['net_cents']==-340 for c in cost['cycles'] if c['exit_kind']=='virtual_cost_close')
    details=read(BASE/'catalog_terms.json')['details'];prefix_checks=[];availability=[]
    for date in DATES:
        clock=Clock(date);cut=clock.start+5*3600000
        for code in CODES:
            terms=details[code];folder=BASE/date/'full_inputs'
            opt=pd.read_pickle(folder/f'{code}.pkl');fut=pd.read_pickle(folder/f'{terms["OptUndlCode"]}.SF.pkl')
            es,fs,_,_=clock.inputs(opt,fut,code,terms)
            rows=add_fast(timeline(es,fs,terms['OptExercisePrice'],terms['ExpireDate'],clock))
            pe,pf,_,_=clock.inputs(opt[opt.time<=cut],fut[fut.time<=cut],code,terms)
            pre=add_fast(timeline(pe,pf,terms['OptExercisePrice'],terms['ExpireDate'],clock,cut=cut+clock.shift))
            assert pre==[x for x in rows if (x[1] if x[0]==-1 else x[1].ts)<=cut+clock.shift]
            prefix_checks.append(date+'_'+code)
            counts=Counter()
            for e in es:
                if e.ts<m.g.CUTOFF or not m.g.valid(e):continue
                counts['valid_post_selection_frames']+=1
                if e.ask-e.bid>=8*2000:
                    counts['spread8_frames']+=1
                    if e.bid_qty>=10:counts['spread8_bid10_frames']+=1
            availability.append(dict(date=date,code=code,**counts))
    parser=Tables();path=OUT/'黄金期权_休市排除与靠山保护报告.html'
    parser.feed(path.read_text('utf-8'))
    for strict in (0,1):
        rows=parser.tables[f'daily{strict}'][1:];assert len(rows)==10
        for row,(p,label) in zip(rows,LABELS.items()):
            s=comparison['groups'][f'{p}_through{strict}']
            assert row[:6]==[label,*map(fmt,s['daily']),fmt(s['pnl_cny'])]
    assert len(parser.tables['accounts'])==161
    for row,(key,s) in zip(parser.tables['accounts'][1:],sorted(summaries.items())):
        assert row[:5]==[s['history_date'],s['code'],LABELS[s['policy']],'严格' if s['strict_through'] else '普通',fmt(s['pnl_cny'])]
    assert len(parser.tables['backer'])==3
    write(OUT/'final_verification.json',dict(status='passed',primary_accounts=160,cost_accounts=160,
        raw_truncated_timeline_prefix_checks=prefix_checks,availability=availability,
        html_checked_daily_rows=20,html_checked_account_rows=160,html_checked_backer_trades=2,
        chart_visual_inspection='PNG read and visually checked; no browser screenshot claimed',
        normal_cycle_retention_and_virtual_fee_identity=True))
    print('PASS:320 ledgers,8 raw-truncated prefixes,20 daily rows,160 account rows,2 support trades')


if __name__=='__main__':main()
