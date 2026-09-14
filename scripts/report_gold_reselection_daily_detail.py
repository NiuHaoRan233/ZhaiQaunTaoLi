"""Join frozen daily selections, per-contract PnL and raw day-session markets."""
from pathlib import Path
import numpy as np
import pandas as pd
from zhaiquant.commodity_dadao_research import load_frame
from zhaiquant import gold_direction_research as g
from zhaiquant.gold_history_validation import timestamp
from probe_gold_reselection import OUT as BASE,DATES,LABELS,ROOT,WORK,read,write,table,fmt
from probe_commodity_capital import digest,unpack
from verify_gold_relative_spread import Tables

OUT=WORK/'reports/gold_reselection_daily_detail_20260914_v1'


def market(es,date,start,bps):
    boundaries=[timestamp(date,t) for t in ('101500','113000','150000')]
    lo_limit=timestamp(date,start);weight=[];mid=[];spread=[];ratios=[];eligible=[]
    for i,e in enumerate(es):
        following=es[i+1].ts if i+1<len(es) and es[i+1].session==e.session else boundaries[e.session]
        lo=max(e.ts,lo_limit);hi=min(following,boundaries[e.session],e.ts+60000)
        if hi>lo and g.valid(e):
            weight.append(hi-lo);mid.append((e.bid+e.ask)/200000);spread.append((e.ask-e.bid)/100000)
            ratios.append(200*(e.ask-e.bid)/(e.ask+e.bid))
            eligible.append(2*(e.ask-e.bid)*10000>=bps*(e.ask+e.bid))
    w=np.array(weight);covered=float(w.sum())
    return dict(volume_contracts=sum(e.quantity for e in es if e.ts>=lo_limit),
        mean_mid=float(np.average(mid,weights=w)),mean_spread=float(np.average(spread,weights=w)),
        mean_relative_pct=float(np.average(ratios,weights=w)),eligible_time_pct=float(100*w[eligible].sum()/covered),
        observed_seconds=covered/1000)


def main():
    OUT.mkdir(exist_ok=True)
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen report')
    frozen=read(BASE/'manifest.json')
    for rel,h in frozen.items():assert digest(ROOT/rel)==h,rel
    terms=read(BASE/'catalog_terms.json')['details'];summaries=read(BASE/'results.json');comparison=read(BASE/'comparison.json')
    oldmarket={(m['date'],m['code']):m for m in read(BASE/'market_activity.json')}
    rows=[];all_rules=[];daily=[];cache={};ledger_checks=0
    for date in DATES:
        selection=read(BASE/date/'selection.json')
        for b in (100,150,200):
            scenario=selection['scenarios'][str(b)]
            for code in scenario['selected']:
                if (date,code) not in cache:
                    f=pd.read_pickle(BASE/date/'full_inputs'/f'{code}.pkl')
                    cache[(date,code)]=load_frame(f,code=code,date=date,detail=terms[code])[0]
                es=cache[(date,code)];day=market(es,date,'090000',b);post=market(es,date,'093000',b)
                om=oldmarket[(date,code)]
                assert day['volume_contracts']==om['day_volume'] and post['volume_contracts']==om['post0930_volume']
                assert abs(post['mean_relative_pct']-om['post0930_relative_mean_pct'])<1e-10
                by_rule={}
                for policy in LABELS:
                    by_strict={}
                    for strict in (0,1):
                        pair={}
                        for mode in ('long','short'):
                            key=f'{date}_{code}_{mode}_{policy}_bps{b}_through{strict}'
                            s=summaries[key];r=unpack(BASE/'ledgers'/f'{key}.json.gz')
                            assert r['summary']==s
                            assert abs(sum(c['net_cents'] for c in r['cycles'])/100-s['pnl_cny'])<1e-8
                            pair[mode]=dict(pnl_cny=s['pnl_cny'],normal_cycles=s['market_cycles'],
                                excluded_cycles=s['virtual_close_count'],removed_tail_gross_cny=s['removed_tail_gross_cny'],
                                max_drawdown_cny=s['max_drawdown_cny'],fees_cny=s['fees_cny'])
                            ledger_checks+=1
                        by_strict[str(strict)]=pair
                    by_rule[policy]=by_strict
                common=dict(date=date,bps=b,code=code,day=day,post0930=post,
                    selection=scenario['rows'][code],value=by_rule['value'])
                rows.append(common);all_rules.append(dict(common,policies=by_rule))
            for mode in ('long','short'):
                a=[r for r in rows if r['date']==date and r['bps']==b]
                pnl=round(sum(r['value']['0'][mode]['pnl_cny'] for r in a),2)
                assert abs(pnl-comparison[f'{mode}_value_bps{b}_through0']['daily'][DATES.index(date)])<1e-7
                daily.append(dict(date=date,bps=b,mode=mode,pnl_cny=pnl,contracts=scenario['selected'],
                    day_volume_contracts=sum(r['day']['volume_contracts'] for r in a)))
    write(OUT/'daily_contract_details.json',rows);write(OUT/'all_policy_details.json',all_rules);write(OUT/'daily_groups.json',daily)
    h=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>新合约逐日收益、成交量和价差</title>',
       '<style>body{font-family:"Microsoft YaHei",sans-serif;background:#f3f5f9;color:#243249;margin:0}main{max-width:1550px;margin:auto;padding:24px}p{line-height:1.8}table{border-collapse:collapse;background:white;width:100%;font-size:14px}td,th{padding:10px;text-align:right;white-space:nowrap;border-bottom:1px solid #dce2eb}th{background:#e5edf8;position:sticky;top:0}td:first-child,th:first-child{text-align:left}.scroll{overflow:auto;max-height:750px}summary{padding:16px;cursor:pointer}a{color:#2461a5}</style><main>',
       '<h1>每张新合约：每日收益、成交量与价差</h1>',
       '<p>主表沿用刚才的“估值耐心”版、普通成交假设。先做多＝买期权后卖出，先做空＝卖期权后买回，两组独立账户。单边手续费1.70元，额外延迟0，09:30后交易。三个休市边界未闭环尾单整笔排除含费用，正常亏损保留；尾单数量和排除前浮风险在后表。</p>',
       '<p>主表市场成交量、均价、价差统一为09:00—15:00日盘，不含夜盘。成交量单位为合约手数，不是策略成交笔数。价格及绝对价差为元/克；黄金一手1000克，绝对价差×1000为一手原始毛空间。相对价差＝(卖一−买一)/中价，逐帧计算后按有效报价驻留时间加权，下一帧中断、最长60秒、不跨休市。</p>',
       '<p>开仓门槛与全天均值不同：先在09:00—09:30筛选处于相应状态的合约，09:30后仅在当时价差达标时新挂开仓单。因此全天平均价差可低于所属组的门槛。重复出现在不同门槛组的同一合约，市场成交量相同，策略交易路径和收益可以不同，不能把市场成交量重复加总。</p>']
    main_tables={}
    for b in (100,150,200):
        a=[r for r in rows if r['bps']==b];rr=[]
        for date in DATES:
            subset=[r for r in a if r['date']==date]
            if not subset:rr.append([date,'无合格合约','—','—','—','—','0.00','0.00']);continue
            for r in subset:
                m=r['day'];v=r['value']['0']
                rr.append([date,r['code'],m['volume_contracts'],fmt(m['mean_mid']),f'{m["mean_spread"]:.4f}',f'{m["mean_relative_pct"]:.2f}%',fmt(v['long']['pnl_cny']),fmt(v['short']['pnl_cny'])])
            rr.append([date,'当日组小计',sum(r['day']['volume_contracts'] for r in subset),'—','—','—',
                fmt(sum(r['value']['0']['long']['pnl_cny'] for r in subset)),fmt(sum(r['value']['0']['short']['pnl_cny'] for r in subset))])
        rr.append(['五日','收益合计','—','—','—','—',fmt(sum(r['value']['0']['long']['pnl_cny'] for r in a)),fmt(sum(r['value']['0']['short']['pnl_cny'] for r in a))])
        main_tables[str(b)]=[[str(x) for x in row] for row in rr]
        h+=['<h2>'+f'{b/100:g}%组'+'</h2>',table(['日期','完整合约','日盘成交量/手','日盘均价','平均绝对价差','平均相对价差','先做多净收益/元','先做空净收益/元'],rr,str(b))]
    h+=['<h2>实际策略时段：09:30后盘口和成交机会</h2>',table(['日期','门槛','合约','09:30后成交量/手','均价','平均绝对价差','平均相对价差','价差达标时间占比','选约首30分钟达标时间'],[
        [r['date'],f'{r["bps"]/100:g}%',r['code'],r['post0930']['volume_contracts'],fmt(r['post0930']['mean_mid']),
         f'{r["post0930"]["mean_spread"]:.4f}',f'{r["post0930"]["mean_relative_pct"]:.2f}%',f'{r["post0930"]["eligible_time_pct"]:.2f}%',f'{100*r["selection"]["relative_time_fraction"]:.2f}%'] for r in rows],'post'),
        '<h2>每张合约正常循环、排除尾单和严格成交对照</h2>',table(['日期','门槛','合约','方向','普通净收益','严格净收益','普通正常循环','普通排除尾单','普通尾单原毛浮值','普通盘中回撤'],[
            [r['date'],f'{r["bps"]/100:g}%',r['code'],'先做多' if mode=='long' else '先做空',fmt(v['pnl_cny']),fmt(r['value']['1'][mode]['pnl_cny']),v['normal_cycles'],v['excluded_cycles'],fmt(v['removed_tail_gross_cny']),fmt(v['max_drawdown_cny'])]
            for r in rows for mode in ('long','short') for v in [r['value']['0'][mode]]],'risk'),
        '<p>零收益可能是没成交，也可能存在被排除尾单；上表分别展示。普通与严格均为L1回测成交假设，不是真实排队。当天平均价差不是每笔实际赚到的价差。</p>',
        '<details><summary>其他八组规则：逐日逐合约、多空收益</summary>',table(['日期','门槛','合约','规则','先多普通','先空普通','先多严格','先空严格'],[
            [r['date'],f'{r["bps"]/100:g}%',r['code'],name,fmt(p['0']['long']['pnl_cny']),fmt(p['0']['short']['pnl_cny']),fmt(p['1']['long']['pnl_cny']),fmt(p['1']['short']['pnl_cny'])]
            for r in all_rules for policy,name in LABELS.items() if policy!='value' for p in [r['policies'][policy]]],'others'),'</details>',
        '<p><a href="daily_contract_details.json">全部主表数据及口径</a> · <a href="all_policy_details.json">九规则完整数据</a> · <a href="../gold_reselection_20260914_v1/黄金期权_按百分比每日换约回测.html">原冻结回测与曲线</a></p></main></html>']
    path=OUT/'黄金期权_每日合约收益成交量价差.html';path.write_text(''.join(h),'utf-8')
    parser=Tables();parser.feed(path.read_text('utf-8'))
    for b,expected in main_tables.items():assert parser.tables[b][1:]==expected
    assert len(parser.tables['post'])==24 and len(parser.tables['risk'])==47 and len(parser.tables['others'])==185
    for rel,h in frozen.items():assert digest(ROOT/rel)==h,rel
    write(OUT/'verification.json',dict(status='passed',contract_scenario_rows=len(rows),unique_contract_days=len(cache),
        frozen_ledgers_reconciled=ledger_checks,old_frozen_files_unchanged=len(frozen),daily_group_sums=30,
        main_html_rows=sum(len(v) for v in main_tables.values()),post_window_rows=23,risk_rows=46,other_policy_rows=184,
        full_day_volume_matches_prior=True,post0930_relative_mean_matches_prior=True))
    write(OUT/'source_manifest.json',{str(Path(__file__).relative_to(ROOT)):digest(Path(__file__))})
    write(OUT/'manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.is_file() and p.name!='manifest.json'})
    for b in (100,150,200):
        print('GROUP',b)
        for r in rows:
            if r['bps']==b:
                m=r['day'];v=r['value']['0']
                print(r['date'],r['code'],m['volume_contracts'],f'{m["mean_mid"]:.2f}',f'{m["mean_spread"]:.4f}',f'{m["mean_relative_pct"]:.2f}%',v['long']['pnl_cny'],v['short']['pnl_cny'])
    print('VERIFIED',len(rows),'contract rows',ledger_checks,'frozen ledgers')


if __name__=='__main__':main()
