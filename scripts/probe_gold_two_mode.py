"""Complete paired fixed-long/fixed-short research; preserve the prior long ledgers."""
from pathlib import Path
from html import escape
import pandas as pd
from zhaiquant import gold_two_mode_research as engine
from probe_gold_backer import BASE,OUT as OLD,DATES,CODES,Clock,timeline,add_fast,audit,economics
from probe_commodity_capital import ROOT,WORK,read,write,pack,unpack,digest
from probe_gold_rule_ladder import portfolio

OUT=WORK/'reports/gold_two_mode_20260914_v1'
LABELS={'trend':'趋势基线','trend_cancel':'仅加及时撤单','trend_fast':'快过滤＋撤单',
    'trend_exit':'快过滤＋撤单＋主动退出','value':'估值耐心','value_exit':'估值＋快撤＋主动退出',
    'backer_entry':'靠山仅入场','backer_guard':'靠山加保护','backer_fast':'靠山再加快撤'}


def replay(rows, code, policy, terms, clock, direction, through, cut=None):
    a=engine.Account(code,policy,terms['OptExercisePrice'],direction,through)
    a.model=a.model.replace(engine.FAMILY,engine.FAMILY+'_'+clock.date)
    for kind,event,features,mid,future in rows:
        ts=event if kind==-1 else event.ts
        if cut is not None and ts>cut:break
        if kind==-1:a.boundary(event)
        elif kind==1:a.future_event(event,features)
        else:a.value.future=future;a.features=features;a.option(event)
    r=a.result();audit(r,cut is None)
    assert all(c['direction']==direction for c in r['cycles'])
    assert all(f['side']==('buy' if direction==1 else 'sell') for f in r['fills'] if not f['closing'])
    r['summary']['history_date']=clock.date
    return clock.restore(r)


def table(headers, rows, id=''):
    return '<div class="scroll"><table id="'+id+'"><thead><tr>'+''.join('<th>'+escape(str(x))+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table></div>'


def fmt(x):return f'{x:,.2f}'


def report(accounts):
    groups={}
    for direction in (1,-1):
        mode='long' if direction==1 else 'short'
        for policy in LABELS:
            for strict in (0,1):
                rs=[r for r in accounts.values() if r['summary']['fixed_direction']==direction and r['summary']['policy']==policy and r['summary']['strict_through']==bool(strict)]
                g=portfolio(rs)
                g['independent_minimum_quote_cash_cny']=sum(max(r['summary']['minimum_cash_for_all_entry_quotes_cny'] for r in rs if r['summary']['code']==c) for c in CODES)
                g.update(daily=[round(sum(r['summary']['pnl_cny'] for r in rs if r['summary']['history_date']==d),2) for d in DATES],
                    virtual_cycles=sum(r['summary']['virtual_close_count'] for r in rs),
                    normal_cycles=sum(r['summary']['market_cycles'] for r in rs),
                    removed_tail_gross_cny=sum(r['summary']['removed_tail_gross_cny'] for r in rs),
                    margin_breach_frames=sum(r['summary']['margin_breach_frames'] for r in rs))
                groups[f'{mode}_{policy}_through{strict}']=g
    write(OUT/'comparison.json',groups)
    h=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>黄金期权：先做多与先做空完整逐日比较</title>',
       '<style>body{font-family:"Microsoft YaHei",sans-serif;background:#f3f5f9;color:#243249;margin:0}main{max-width:1450px;margin:auto;padding:24px}p{line-height:1.8}h2{margin-top:30px}table{width:100%;border-collapse:collapse;background:white;font-size:13px}th,td{padding:10px;text-align:right;white-space:nowrap;border-bottom:1px solid #dae0e9}th:first-child,td:first-child{text-align:left}th{background:#e5edf8}.scroll{overflow:auto;max-height:680px}summary{cursor:pointer;padding:14px;background:#e5edf8}a{color:#2461a5}</style><main>',
       '<h1>先做多、先做空：完整逐日比较</h1>',
       '<p>两合约：au2610C960.SF、au2610C952.SF。每份最多一手，单边1.70元、额外延迟0，09:30开始、原始价差至少8跳。三个休市边界仍未闭合循环整笔排除（含费用），正常已平亏损保留。单位：元。</p>',
       '<p>先做多＝买入→卖出；先做空＝卖出→买回。两组独立资金账户，不是合并后的双向策略。空头对应期货上涨为逆向，卖一前一跳开仓、买一上一跳回补，追卖价的节流也按方向镜像；靠山空头对应稳定大卖档。原估值择向增强另列，不能代替固定空头。</p>']
    for strict in (0,1):
        h += ['<h2>'+('普通成交假设' if not strict else '严格穿价成交假设')+'</h2>']
        for mode,label in [('long','先做多：买→卖'),('short','先做空：卖→买')]:
            rr=[]
            for p,name in LABELS.items():
                g=groups[f'{mode}_{p}_through{strict}']
                rr.append([name,*map(fmt,g['daily']),fmt(g['pnl_cny']),g['normal_cycles'],g['virtual_cycles'],fmt(g['max_drawdown_cny']),fmt(g['removed_tail_gross_cny'])])
            h += ['<h3>'+label+'</h3>',table(['规则','09-07','09-08','09-09','09-10','合计','正常循环','排除尾单','盘中回撤','排除前尾仓毛值'],rr,f'{mode}{strict}')]
    switch=read(OLD/'results.json');rr=[]
    for strict in (0,1):
        a=[s for s in switch.values() if s['policy']=='switch' and s['strict_through']==bool(strict)]
        rr.append(['普通' if not strict else '严格',*[fmt(sum(s['pnl_cny'] for s in a if s['history_date']==d)) for d in DATES],fmt(sum(s['pnl_cny'] for s in a))])
    h += ['<h2>原估值择向增强（独立参考）</h2>',table(['成交假设','09-07','09-08','09-09','09-10','合计'],rr),
          '<p>该版按照当时估值与趋势选择方向，并非当天结束后挑更赚钱的一边。不能将上表多空日度取最大值拼成策略。</p>',
          '<details><summary>全部288账户：分日期、分合约、分方向、分规则</summary>',table(['日期','合约','模式','规则','假设','净收益','正常循环','排除尾单','盘中回撤'],[
            [s['history_date'],s['code'],'先做多' if s['fixed_direction']==1 else '先做空',LABELS[s['policy']],
             '严格' if s['strict_through'] else '普通',fmt(s['pnl_cny']),s['market_cycles'],s['virtual_close_count'],fmt(s['max_drawdown_cny'])]
            for r in accounts.values() for s in [r['summary']]],'accounts'),'</details>',
          '<p>资金每代码每天25万元只是与既有历史足额账户比较的研究预算，不是买卖一手所需资本。空头准备金沿用20%期货名义额代理，并非核实券商保证金。普通触价/严格穿价均不是实际排队；尾仓排除会排除现实风险，排除前可见报价毛值单独显示。</p>',
          '<p>四天均为重复开发数据。144多头账户与前轮冻结订单、成交、撤单、循环、曲线复现；144空头新路径及其14:00前缀完成，全部288账户资金/手续费/方向/边界核验。输入特征再做8组原始截断前缀检查。结果不代表已实盘验证或合并资金可达收益。</p>',
          '<p><a href="comparison.json">全部日度与曲线JSON</a> · <a href="results.json">288账户摘要</a> · <a href="verification.json">核验记录</a></p></main></html>']
    (OUT/'黄金期权_先做多先做空逐日完整对照.html').write_text(''.join(h),'utf-8')
    return groups


def main():
    OUT.mkdir(exist_ok=True);(OUT/'ledgers').mkdir(exist_ok=True)
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen result')
    preserved=read(OLD/'manifest.json')
    for rel,h in preserved.items():assert digest(ROOT/rel)==h,rel
    write(OUT/'plan.json',dict(family=engine.FAMILY,directions=[1,-1],policies=list(LABELS),
        settlement='exclude pending break cycle including fees',capital_per_code_cny=250000,
        fees_one_side_cny=1.7,extra_latency_ms=0,spread_ticks=8,
        no_parameter_search=True,long_parent_manifest_hash=digest(OLD/'manifest.json')))
    terms=read(BASE/'catalog_terms.json')['details'];accounts={};prefixes=[]
    for date in DATES:
        clock=Clock(date)
        for code in CODES:
            d=terms[code];folder=BASE/date/'full_inputs'
            opt=pd.read_pickle(folder/f'{code}.pkl');fut=pd.read_pickle(folder/f'{d["OptUndlCode"]}.SF.pkl')
            es,fs,_,_=clock.inputs(opt,fut,code,d)
            rows=add_fast(timeline(es,fs,d['OptExercisePrice'],d['ExpireDate'],clock))
            cut=clock.start+5*3600000;internal_cut=cut+clock.shift
            pe,pf,_,_=clock.inputs(opt[opt.time<=cut],fut[fut.time<=cut],code,d)
            pre=add_fast(timeline(pe,pf,d['OptExercisePrice'],d['ExpireDate'],clock,cut=internal_cut))
            assert pre==[x for x in rows if (x[1] if x[0]==-1 else x[1].ts)<=internal_cut]
            for direction in (1,-1):
                for strict in (0,1):
                    for policy in LABELS:
                        r=replay(rows,code,policy,d,clock,direction,bool(strict))
                        if direction==1:
                            old=unpack(OLD/'ledgers'/f'{date}_{code}_{policy}_through{strict}.json.gz')
                            assert economics(r)==economics(old),(date,code,policy,strict,'long parent')
                        else:
                            pr=replay(pre,code,policy,d,clock,direction,bool(strict),cut=internal_cut)
                            for k,t in [('orders','created_ts'),('fills','ts'),('cycles','exit_ts'),('cancels','ts'),('risk_events','ts'),('fee_adjustments','ts')]:
                                assert pr[k]==[v for v in r[k] if v[t]<=cut],(date,code,policy,k)
                            prefixes.append(r['summary']['model_id'])
                        key=f'{date}_{code}_{r["summary"]["trade_mode"]}_{policy}_through{strict}'
                        pack(OUT/'ledgers'/f'{key}.json.gz',r);accounts[key]=r
                print(date,code,'long' if direction==1 else 'short','complete',flush=True)
    for rel,h in preserved.items():assert digest(ROOT/rel)==h,rel
    write(OUT/'results.json',{k:r['summary'] for k,r in accounts.items()})
    groups=report(accounts)
    write(OUT/'verification.json',dict(status='passed',accounts=288,long_parent_reproductions=144,
        short_execution_prefixes=len(prefixes),raw_truncated_timeline_prefixes=8,flat_boundaries=864,
        old_frozen_files_unchanged=len(preserved),prefix_model_ids=prefixes))
    write(OUT/'source_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in
        [Path(engine.__file__),Path(__file__),ROOT/'tests/test_gold_two_mode_research.py']})
    for mode in ['long','short']:
        for p in LABELS:
            a=groups[f'{mode}_{p}_through0'];b=groups[f'{mode}_{p}_through1']
            print(mode,p,a['daily'],a['pnl_cny'],'strict',b['pnl_cny'])


if __name__=='__main__':main()
