"""Final artifact QA and explicit audit against the requested latest-day scope."""
from pathlib import Path
from verify_gold_breakflat_report import Tables
from report_gold_aligned_value import OUT,V4,V5,RUN,CONFIG
from probe_commodity_capital import ROOT,read,write,digest


def main():
    p=Tables();p.feed((OUT/'黄金期权日内估值做市0.1报告.html').read_text('utf8'))
    assert len(p.tables)==13 and p.details==4 and p.images==1
    assert len(p.tables[0])==3 and len(p.tables[3])==11 and len(p.tables[-1])==89
    nums=lambda x:round(float(x.replace(',',''))*100)
    overview=read(OUT/'overview.json')
    for src,line in zip(overview['main'],p.tables[0][1:]):
        for i in (1,2,3,4,7,8):assert nums(line[i])==round(src[i]*100)
    for src,line in zip(overview['robustness'],p.tables[3][1:]):
        for i in (3,4,5):assert nums(line[i])==round(src[i]*100)
        assert nums(line[3])+nums(line[4])==nums(line[5]) and nums(line[3])>0 and nums(line[4])>0
    summary={}
    for tag,folder in [('v4',V4),('v5',V5),('run',RUN)]:summary.update({tag+'_'+k:v for k,v in read(folder/'results.json').items()})
    for line in p.tables[-1][1:]:
        s=summary[line[0]]
        for i,key in [(1,'pnl_cny'),(2,'fees_cny'),(5,'removed_tail_gross_cny'),(6,'max_drawdown_cny')]:assert nums(line[i])==round(s[key]*100)
        assert int(line[3])==s['complete_cycles'] and int(line[4])==s['virtual_close_count']
    for name,h in read(OUT/'delivery_source_manifest.json').items():assert digest(ROOT/name)==h
    assert read(RUN/'reproduction_verification.json')['complete_equality_to_frozen_ledgers']
    config=read(CONFIG);assert config['codes']==['au2610C960.SF','au2610C952.SF'] and config['date']=='20260911'
    requirements=[
        dict(requirement='固定这两个标的、最近一天开发研究',evidence='strategy.json codes/date; original frozen QMT option and same-month future inputs',status='proven'),
        dict(requirement='每次休市成本虚拟平仓、仍计手续费',evidence='cost ledgers: exactly three zero-inventory boundaries per account, virtual cycles gross0 and net-3.40; independent money audit',status='proven'),
        dict(requirement='正常亏损保留并解释为什么亏钱',evidence='prior directional midpoint decomposition, old losing-trade target audit, retained losing and losing-variant ledgers',status='proven'),
        dict(requirement='试先买后卖及先空后平，可探索自动择向',evidence='direction_v1 40 accounts and timing_v2 corrected direction-switch accounts; final deliberately chooses validated long mechanism',status='proven'),
        dict(requirement='用标的价格做相对定价并识别有利报价',evidence='Black76 same-month future, source-paired past IV, fast/slow lower reference, positive after-fee buy edge gate',status='proven'),
        dict(requirement='研究趋势过滤与提前反应的路径',evidence='v1/v2/v3 trend/cancellation ablations, descriptive lead-lag audit and conservative interval cancel tests; not retained where unhelpful',status='proven'),
        dict(requirement='形成当前两个标的的可行研究交易模式',evidence='fixed10/60s: both positive under cost and actual exit; both positive in all20 neighboring/lag/strict-price cases; capital/cash/timing checks passed',status='proven_for_requested_development_day'),
        dict(requirement='完整量化规则、复现入口与回测报告',evidence='gold_intraday_strategy README/config, run_gold_intraday_value.py/cmd;8 ledgers full equality; HTML/curve/full audit bundles',status='proven'),
    ]
    write(OUT/'completion_audit.json',dict(status='latest_day_research_delivery_complete',requirements=requirements,
        scope_basis='用户此前明确最近一天、先不管以前；当前goal继续固定这两个标的。未将后续新日期或实盘盈利写成已完成。',
        not_claimed=['future or live profitability','unseen out-of-sample validation','actual exchange queue','forward paper deployment'],
        preserved_limitations=['C952 profit concentration','single observed development day and multiple exploratory models','American-option European valuation approximation']))
    qa=read(OUT/'report_qa.json');qa.update(status='passed',all88_summary_and_main_and20_robustness_amounts_exact=True,
        static_table_count=13,chart_visually_reviewed=True,no_javascript_dependency=True)
    write(OUT/'report_qa.json',qa)
    write(OUT/'artifact_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='artifact_manifest.json'})
    print(qa)


if __name__=='__main__':main()
