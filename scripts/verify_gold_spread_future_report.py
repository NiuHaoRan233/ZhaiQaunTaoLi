"""Independent rendered-table and frozen-ledger reconciliation for0.2."""
from pathlib import Path
from verify_gold_rule_ladder_report import Tables
from report_gold_spread_future import OUT,V3,VAL,V4
from probe_commodity_capital import ROOT,WORK,read,write,unpack,digest


def main():
    for p,h in read(OUT/'source_manifest.json').items():assert digest(ROOT/p)==h
    data=read(OUT/'overview.json');p=Tables();p.feed((OUT/'黄金期权大价差做市0.2报告.html').read_text(encoding='utf-8'))
    expected=list(data['tables'].values())
    for rendered,rows in zip(p.tables,expected):
        assert rendered==[[f'{x:,.2f}' if isinstance(x,(int,float)) else str(x) for x in r] for r in rows]
    assert len(p.tables)==11 and p.images==1
    sources={'v3':read(V3/'results.json'),'val':read(VAL/'results.json'),'v4':read(V4/'results.json')}
    trades=unpack(OUT/'全部逐笔交易.json.gz');fields={1:'pnl_cny',2:'max_drawdown_cny',3:'complete_cycles',4:'order_count',5:'reprice_cancel_count',
        6:'fees_cny',7:'worst_cycle_cny',8:'profit_without_best3_cny',9:'future_cancel_requests',10:'ambiguous_cancel_fills'}
    for row in data['tables']['all_accounts']:
        tag,k=row[0].split('_',1);s=sources[tag][k]
        for j,name in fields.items():assert float(row[j])==s[name]
        cs=trades[row[0]];assert len(cs)==s['complete_cycles']
        assert sum(c['net_cents'] for c in cs)==round(s['pnl_cny']*100)
    j=7;visible=0
    for profile in ('trend_long','cancel_switch'):
        for code in ('au2610C960.SF','au2610C952.SF'):
            cs=trades[f'v4_{code}_{profile}_spread8_through0_delay0'];rows=p.tables[j];j+=1
            assert len(rows)==len(cs)
            for row,c in zip(rows,cs):
                assert float(row[2])==c['entry_price_cents']/100000
                assert float(row[4])==c['exit_price_cents']/100000
                assert round(float(row[5].replace(',',''))*100)==c['net_cents'];visible+=1
    repro=read(WORK/'reports/gold_intraday_v02_reproduction_20260911/verification.json')
    assert repro['status']=='passed' and repro['accounts']==8 and repro['full_dictionary_equality']
    qa=dict(status='passed',tables=len(p.tables),images=p.images,all248_account_amounts_verified=True,
        all_cycle_nets_verified=True,visible_trade_rows=visible,full_reproduction_accounts=8,
        browser_screenshot_qa=False,png_visual_review='pending')
    write(OUT/'report_qa.json',qa);print(qa)


if __name__=='__main__':main()
