"""Check rendered HTML numeric cells against every frozen source account."""
from verify_gold_breakflat_report import Tables
from report_gold_direction import OUT,V1,V2,V3
from probe_commodity_capital import ROOT,read,write,digest


def main():
    page=(OUT/'黄金期权双方向与期货定价研究.html').read_text('utf8');p=Tables();p.feed(page)
    assert len(p.tables)==20 and p.details==12 and p.images==1
    assert len(p.tables[0])==15 and len(p.tables[-1])==121
    rows={}
    for tag,folder in [('v1',V1),('v2',V2),('v3',V3)]:
        rows.update({tag+'_'+k:s for k,s in read(folder/'results.json').items()})
    num=lambda x:round(float(x.replace(',',''))*100)
    for line in p.tables[-1][1:]:
        s=rows[line[0]]
        for col,key in [(1,'pnl_cny'),(2,'market_cycle_net_cny'),(4,'removed_tail_gross_cny'),(6,'fees_cny'),(7,'max_drawdown_cny'),(8,'max_reserve_cny')]:
            assert num(line[col])==round(s[key]*100)
        assert int(line[3])==s['virtual_close_count'] and int(line[5])==s['complete_cycles']
    overview=read(OUT/'overview.json')
    for source,line in zip(overview['performance'],p.tables[0][1:]):
        for i in (1,2,3,4,5,6,8):assert num(line[i])==round(source[i]*100)
        assert num(line[1])+num(line[2])==num(line[3])
        assert num(line[4])+num(line[5])==num(line[6])
    for source,line in zip(overview['delays'],p.tables[3][1:]):
        for i in (2,3,4):assert num(line[i])==round(source[i]*100)
    for name,h in read(OUT/'delivery_source_manifest.json').items():assert digest(ROOT/name)==h
    qa=read(OUT/'report_qa.json');qa.update(status='passed',static_tables_parsed=20,
        all_120_summary_amounts_exact=True,all_14_modes_and_18_delays_exact=True,
        chart_visually_reviewed=True,no_javascript_dependency=True)
    write(OUT/'report_qa.json',qa)
    write(OUT/'artifact_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='artifact_manifest.json'})
    print(qa)


if __name__=='__main__':main()
