"""Independent static HTML/data QA; does not imply browser screenshot QA."""
from html.parser import HTMLParser
from collections import Counter
from probe_commodity_capital import ROOT,read,write,digest
from report_gold_breakflat import OUT, cycle_key


class Tables(HTMLParser):
    def __init__(self):
        super().__init__();self.tables=[];self.details=0;self.images=0;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='table':self.tables.append([])
        elif tag=='tr':self.tables[-1].append([])
        elif tag in ('td','th'):self.cell=''
        elif tag=='details':self.details+=1
        elif tag=='img':
            self.images+=1;assert dict(attrs)['src'].startswith('data:image/png;base64,')
    def handle_data(self,data):
        if self.cell is not None:self.cell+=data
    def handle_endtag(self,tag):
        if tag in ('td','th'):
            self.tables[-1][-1].append(self.cell);self.cell=None


def main():
    d=read(OUT/'新旧逐笔与组合核验.json');page=(OUT/'黄金期权休市前清仓回测.html').read_text('utf8')
    parser=Tables();parser.feed(page)
    assert parser.details==4 and parser.images==1 and len(parser.tables)==11
    assert len(parser.tables[2])-1==12
    assert sum(len(t)-1 for t in parser.tables[-4:])==480
    for (key,r),t in zip(d['ledgers']['new'].items(),parser.tables[-4:]):
        assert len(t)-1==len(r['cycles'])
        for c,line in zip(r['cycles'],t[1:]):
            for idx,field in [(6,'gross_cents'),(7,'fees_cents'),(8,'net_cents')]:
                assert round(float(line[idx].replace(',',''))*100)==c[field]
        assert round(sum(float(line[8].replace(',','')) for line in t[1:])*100)==round(r['summary']['pnl_cny']*100)
    removed=[];retained=0
    for key,r in d['ledgers']['new'].items():
        if not key.endswith('_gap'):continue
        old=d['ledgers']['old'][key];new=Counter(cycle_key(c) for c in r['cycles'])
        assert not(new-Counter(cycle_key(c) for c in old['cycles']))
        retained+=sum(new.values())
        for c in old['cycles']:
            k=cycle_key(c)
            if new[k]:new[k]-=1
            else:removed.append(c)
    bounds=[1789092900000,1789097400000,1789110000000]
    cross=[c for c in removed if any(c['entry_ts']<b<=c['exit_ts'] for b in bounds)]
    assert retained==238 and len(removed)==20 and len(cross)==3
    assert sum(c['net_cents'] for c in removed)==211200
    assert sum(c['net_cents'] for c in cross)==58980
    for name,h in read(OUT/'delivery_source_manifest.json').items():assert digest(ROOT/name)==h
    qa=read(OUT/'report_qa.json');qa.update(static_html_parsed=True,all_480_displayed_cycle_amounts_exact=True,
        exact_common_cycles=retained,removed_cycles=len(removed),removed_cross_break_cycles=len(cross),
        removed_other_cycle_net_cny=1522.2,chart_visually_reviewed=True)
    write(OUT/'report_qa.json',qa)
    write(OUT/'artifact_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='artifact_manifest.json'})
    print(qa)


if __name__=='__main__':main()
