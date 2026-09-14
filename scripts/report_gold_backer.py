"""Compact, auditable daily report for gold break-exclusion and risk research."""
import base64
from collections import Counter, defaultdict
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from probe_gold_backer import OUT,BASE,ROOT,DATES,CODES,m,audit,read,write,pack,unpack,digest
from probe_gold_rule_ladder import portfolio

LABELS={'trend':'原趋势主版','switch':'原估值择向增强','value':'原估值耐心多头',
    'trend_cancel':'主版＋期货事件撤单','trend_fast':'主版＋快风险撤单/过滤',
    'trend_exit':'快撤单＋主动风险退出','value_exit':'估值多头＋快撤/主动退出',
    'backer_entry':'靠山：仅稳定大买档入场','backer_guard':'靠山：加受损退出','backer_fast':'靠山：再加快撤单'}


def fmt(x):return f'{x:,.2f}'
def tm(ts):return datetime.fromtimestamp(ts/1000,ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M:%S.%f')[:-3]
def table(headers,rows,table_id=''):
    return '<div class="scroll"><table id="'+table_id+'"><thead><tr>'+''.join('<th>'+escape(str(x))+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table></div>'


def load():
    accounts={p.stem.replace('.json',''):unpack(p) for p in sorted((OUT/'ledgers').glob('*.json.gz'))}
    assert len(accounts)==160
    summary=read(OUT/'results.json')
    for k,r in accounts.items():assert r['summary']==summary[k];audit(r)
    return accounts


def main():
    accounts=load();groups={};pairs=[]
    for policy in LABELS:
        for strict in (False,True):
            rs=[r for r in accounts.values() if r['summary']['policy']==policy and r['summary']['strict_through']==strict]
            p=portfolio(rs);cs=[c for r in rs for c in r['cycles']];normal=[c for c in cs if c['exit_kind']!='virtual_cost_close']
            # Days reset their capital: never add the same slot four times.
            p['independent_minimum_quote_cash_cny']=sum(max(r['summary']['minimum_cash_for_all_entry_quotes_cny'] for r in rs if r['summary']['code']==code) for code in CODES)
            p.update(daily=[round(sum(r['summary']['pnl_cny'] for r in rs if r['summary']['history_date']==d),2) for d in DATES],
                normal_cycles=len(normal),virtual_cycles=len(cs)-len(normal),
                cost_with_fees_pnl_cny=round(sum(r['summary']['cost_with_fees_pnl_cny'] for r in rs),2),
                removed_tail_gross_cny=sum(r['summary']['removed_tail_gross_cny'] for r in rs),
                risk_closes=sum(r['summary']['risk_close_count'] for r in rs),
                normal_winners=sum(c['net_cents']>0 for c in normal),
                normal_losers=sum(c['net_cents']<0 for c in normal),
                worst_cycle_cny=min((c['net_cents']/100 for c in normal),default=0),
                max_entry_premium_cny=max((c['entry_price_cents']/100 for c in cs),default=0),
                entry_orders=sum(r['summary']['entry_order_count'] for r in rs))
            groups[f'{policy}_through{int(strict)}']=p
    for before,after in [('trend','trend_cancel'),('trend_cancel','trend_fast'),('trend_fast','trend_exit'),
                          ('value','value_exit'),('backer_entry','backer_guard'),('backer_guard','backer_fast')]:
        for strict in (0,1):
            removed=[];added=[];same_entry=[];retained=0
            for date in DATES:
                for code in CODES:
                    a=accounts[f'{date}_{code}_{before}_through{strict}'];b=accounts[f'{date}_{code}_{after}_through{strict}']
                    key=lambda c:tuple(c[k] for k in ('direction','entry_ts','entry_price_cents','exit_ts','exit_price_cents','exit_kind'))
                    ac={key(c):c for c in a['cycles']};bc={key(c):c for c in b['cycles']}
                    removed += [c for k,c in ac.items() if k not in bc]
                    added += [c for k,c in bc.items() if k not in ac]
                    retained += len(ac.keys()&bc.keys())
                    ek=lambda c:tuple(c[k] for k in ('direction','entry_ts','entry_price_cents'))
                    ae={ek(c):c for c in a['cycles']};be={ek(c):c for c in b['cycles']}
                    for k in ae.keys()&be.keys():
                        if ae[k]['net_cents']!=be[k]['net_cents']:
                            same_entry.append(dict(date=date,code=code,entry_ts=k[1],
                                before=ae[k]['net_cents']/100,after=be[k]['net_cents']/100,
                                difference=(be[k]['net_cents']-ae[k]['net_cents'])/100,
                                before_exit_ts=ae[k]['exit_ts'],after_exit_ts=be[k]['exit_ts']))
            delta=(sum(c['net_cents'] for c in added)-sum(c['net_cents'] for c in removed))/100
            assert abs(delta-(groups[f'{after}_through{strict}']['pnl_cny']-groups[f'{before}_through{strict}']['pnl_cny']))<.001
            pairs.append(dict(before=before,after=after,strict=bool(strict),delta=delta,retained=retained,
                removed_count=len(removed),removed_net=sum(c['net_cents'] for c in removed)/100,
                added_count=len(added),added_net=sum(c['net_cents'] for c in added)/100,
                same_entry=sorted(same_entry,key=lambda x:x['difference'])))
    write(OUT/'comparison.json',dict(groups=groups,pairs=pairs))
    plt.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei','DejaVu Sans']
    plt.rcParams['axes.unicode_minus']=False
    fig,axes=plt.subplots(1,2,figsize=(13,5.3),constrained_layout=True)
    for ax,strict in zip(axes,(0,1)):
        for p in ('trend','switch','trend_fast','trend_exit','backer_guard'):
            daily=groups[f'{p}_through{strict}']['daily'];vals=[0]
            for x in daily:vals.append(vals[-1]+x)
            ax.plot(range(5),vals,marker='o',label=LABELS[p])
        ax.axhline(0,color='#777',lw=.6);ax.set_xticks(range(5),['开始','09-07','09-08','09-09','09-10'])
        ax.set_title('普通成交假设' if not strict else '严格穿价成交假设',loc='left')
        ax.set_ylabel('累计研究净收益 / 元');ax.grid(alpha=.15);ax.legend(fontsize=8)
    chart=OUT/'四日收益对照.png';fig.savefig(chart,dpi=140);plt.close(fig)
    h=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>黄金：休市排除、快撤单与靠山保护</title>',
       '<style>body{font-family:"Microsoft YaHei",sans-serif;color:#20314a;background:#f3f6fa;margin:0}main{max-width:1320px;margin:auto;padding:28px}h1{font-size:28px}h2{margin-top:32px;font-size:21px}p{line-height:1.8}.card{background:white;padding:20px;border-radius:12px;border:1px solid #dae1eb}table{border-collapse:collapse;width:100%;background:white;font-size:13px}td,th{padding:10px;border-bottom:1px solid #dfe5ed;text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}th{background:#e6edf7}.scroll{overflow:auto;max-height:650px}img{width:100%;margin:15px 0}.note{color:#596d88;font-size:13px}summary{cursor:pointer;padding:15px;background:#e6edf7;margin:8px 0}a{color:#235da1}</style><main>',
       '<h1>黄金期权：休市排除、快撤单与靠山保护</h1><p class="note">2026-09-07至09-10｜au2610C960 / C952｜每合约最多一手｜单边1.70元｜额外下单延迟0｜仅日盘离线研究</p>',
       '<div class="card"><b>快撤单有效减少坏成交；机械主动止损在这四天反而损害收益。靠山模式已有可运行分支，但只有两笔成交。</b>',
       '<p>休市前正常开仓，边界仍未完成的循环按你的最新要求整笔排除，包含费用。旧主版因此从−3592.60变为−3222.40元；增加快撤单/过滤后为−375.40元，仍未转正。估值择向增强在排除口径下为+1622.40元，严格组+2612.60元，主要改善来自不再计入休市尾部平仓，不能说预测能力因此提升。</p>',
       '<p>靠山保护两笔合计−66.80元，比不保护少亏20元；其中一笔能卖给剩余5手买盘，另一笔等风险确认时原支撑已经消失。改善一跳买入再卖回支撑，至少要承担20元价差加3.40元双费。</p></div>',
       '<h2>1. 所有方案逐日比较</h2><p>单位：元，两合约合计。三个旧结构在新结算下完整重跑，再逐项加条件；没有按事后盈亏删除普通闭环。普通允许触价，严格要求成交末价穿过限价，均不代表真实队列。</p>']
    for strict in (0,1):
        rr=[]
        for p,label in LABELS.items():
            g=groups[f'{p}_through{strict}'];rr.append([label,*map(fmt,g['daily']),fmt(g['pnl_cny']),g['normal_cycles'],g['virtual_cycles'],g['risk_closes'],fmt(g['max_drawdown_cny'])])
        h += ['<h3>'+('普通' if not strict else '严格穿价')+'</h3>',table(['方案','09-07','09-08','09-09','09-10','四日合计','正常闭环','排除尾单','主动风险退出','盘中回撤'],rr,f'daily{strict}')]
    h += ['<p class="note">回撤保留持仓过程的可见对手盘盯市，边界排除时才归零；因此研究收益与持仓风险可能明显不同。每日资金重置，只累加研究损益，不冒充跨日连续资金实绩。</p>',
          '<img alt="四日累计研究收益" src="data:image/png;base64,'+base64.b64encode(chart.read_bytes()).decode()+'">',
          '<h2>2. 休市尾单究竟排除了什么</h2><p>10:15、11:30、15:00边界由独立时钟结算，最后5秒也可正常开仓。保留旧实际退出版本；新主统计排除整笔，另给出此前约定的成本归零但仍扣双费口径。下表尾仓浮盈亏仅是最后可见盘口参考，可能陈旧，不保证边界能成交。</p>']
    h += [table(['方案（普通）','旧实际休市退出','新整笔排除','成本归零仍扣费','排除尾单数','排除前尾仓毛浮盈亏'],[
        [LABELS[p],fmt(sum(r['summary']['old_actual_close_pnl_cny'] for r in accounts.values() if r['summary']['policy']==p and not r['summary']['strict_through'])),
         fmt(groups[f'{p}_through0']['pnl_cny']),fmt(groups[f'{p}_through0']['cost_with_fees_pnl_cny']),groups[f'{p}_through0']['virtual_cycles'],fmt(groups[f'{p}_through0']['removed_tail_gross_cny'])] for p in ('trend','switch','value')],'settlement')]
    tails=[]
    for r in accounts.values():
        if r['summary']['strict_through']:continue
        for c in r['cycles']:
            if c['exit_kind']=='virtual_cost_close':
                tails.append([LABELS[r['summary']['policy']],c['code'],tm(c['entry_ts']),tm(c['exit_ts']),
                    '多' if c['direction']==1 else '空',fmt(c['entry_price_cents']/100000),
                    fmt(c['quote_mark_gross_before_close_cents']/100),c['reference_quote_age_seconds'],fmt(c['excluded_original_fees_cents']/100)])
    h += ['<details><summary>逐笔排除清单（普通）</summary>',table(['方案','合约','开仓','边界','方向','开仓价/克','排除前毛浮盈亏','报价年龄秒','排除双费'],tails),'</details>',
          '<h2>3. 为什么期货没提前救下来</h2><p>原主版只在期权报价更新时检查过去10/60秒趋势，没有期货事件即时撤单；容忍幅度又随价差扩大。新快规则用固定两跳预算检查2/10秒变化及自挂单以来的delta映射变化，并在期货更新时请求撤单。成交区间内才到的警报不能回头删成交。</p>']
    warnings=read(OUT/'warning_audit.json');wr=[]
    for loss in (True,False):
        for method,label in [('old_trend','原10/60秒门槛'),('fast','固定两跳快规则')]:
            rows=[r for r in warnings if (r['net_cny']<0)==loss];count=Counter(x[method+'_category'] for x in rows)
            wr.append(['亏损' if loss else '非亏损',label,len(rows),count['before_fill_interval'],count['inside_ambiguous_interval'],count['no_pre_fill_warning']])
    h += [table(['原主版正常闭环','判定规则','总笔数','成交区间开始前已失效','区间内才失效','成交前未见失效'],wr,'warnings'),
          '<p>86笔亏损中，原门槛只有3笔能在成交区间开始前判无效；快规则增至29笔，但也会挡住11笔后来赚钱的交易。这是按旧挂单逐笔定位的风险信号机会，包含数据不可用时的停挂，不能当作独立预测命中率；最终收益必须看完整重放后出现的新路径。</p>',
          '<h2>4. 每加一条，究竟改变了哪些交易</h2>']
    h += [table(['改动','成交假设','保留原循环','移除原循环数','原循环净额','新增循环数','新增循环净额','总差额'],[
        [LABELS[x['before']]+' → '+LABELS[x['after']],'严格' if x['strict'] else '普通',x['retained'],x['removed_count'],fmt(x['removed_net']),x['added_count'],fmt(x['added_net']),fmt(x['delta'])] for x in pairs],'changes')]
    examples=next(x for x in pairs if x['before']=='trend_fast' and not x['strict'])['same_entry']
    h += ['<p>“移除/新增”按整个开平循环识别，同一笔开仓改了退出也会分别出现；不是把移除数全当作成功预测。主动退出版比快撤单版少2980.40元，9月10日从+1371.20变为−1072.20元，反复跨买卖差及过早认错抵消了避亏。</p>',
          '<details><summary>相同开仓、不同退出：最差与最好反例</summary>',table(['日期','合约','同一开仓','快撤单原收益','加入主动退出后','变化'],[
            [x['date'],x['code'],tm(x['entry_ts']),fmt(x['before']),fmt(x['after']),fmt(x['difference'])] for x in examples[:8]+examples[-4:]]),'</details>',
          '<h2>5. 靠山规则及全部交易</h2><p>当前买一至少10手，连续2秒至少3次报价都保持同价、量不少于10手，才在前方改善一跳买一手。成交后，若期货转坏且原档显示量不超过初始一半、同时已有至少一手末价成交佐证，则主动卖给当前买一；原档直接消失时单列撤档/跌穿风险。十手、两秒、两跳都是本轮试验参数。</p>',
          '<p>L1无法识别同一个“大哥”，也不能证明减少的五六手全是成交。每个聚合更新最多记一手末价佐证。主动出口取新期权快照当前报价，先结算旧区间，因此支持档可能已经跑掉；没有用原价保证止损。</p>']
    br=[]
    for r in accounts.values():
        if r['summary']['strict_through'] or r['summary']['policy']!='backer_guard':continue
        ev={x['entry_ts']:x for x in r['risk_events']}
        for c in r['cycles']:
            x=ev[c['entry_ts']];s=x['support']
            br.append([c['code'],tm(c['entry_ts']),fmt(c['entry_price_cents']/100000),s['initial_qty'],
                s['observed_qty'],s['trade_evidence_contracts'],tm(c['exit_ts']),fmt(c['exit_price_cents']/100000),fmt(c['net_cents']/100),
                '原支撑消失，卖更低买一' if s['observed_qty']==0 else '向剩余支撑卖出'])
    h += [table(['合约','买入时间','买入价/克','原支持量','退出时余量','末价成交佐证下限','卖出时间','卖出价/克','净收益','结果'],br,'backer'),
          '<p>两笔都来自C960；C952没有符合并成交的靠山机会。样本只有两笔，不能据少亏给它贴上安全或有效标签。加入快撤单后只剩一笔，净−43.40元。</p>',
          '<h2>6. 资金、逐合约日度与复现</h2><p>每份25万元只是复现此前标准账户的预算，最多仍一手。多头实际付出是期权报价×1000加开仓费；比如16.54元/克买一手付16,541.70元。增强版允许空头，20%期货名义额准备金只是研究代理，不是已核验的券商保证金，因此本报告不拿统一预算推销高低收益率。</p>',
          '<details><summary>160份账户：所有方案、日期、合约与两种成交假设</summary>',table(['日期','合约','方案','成交假设','净收益','正常循环','排除尾单','主动退出','持仓回撤','最大单笔权利金'],[
            [s['history_date'],s['code'],LABELS[s['policy']],'严格' if s['strict_through'] else '普通',fmt(s['pnl_cny']),s['market_cycles'],s['virtual_close_count'],s['risk_close_count'],fmt(s['max_drawdown_cny']),
             fmt(max((c['entry_price_cents']/100 for c in r['cycles']),default=0))] for r in accounts.values() for s in [r['summary']]],'accounts'),'</details>',
          '<p class="note">160份主账户、160份成本扣费对照；48份market父版订单/成交/撤单/曲线复现。160份14:00截断前缀、资金/手续费/库存/主动报价核验通过，85项黄金测试通过。保存每笔委托、成交、循环、费用排除调整与风险证据。使用的四天均是重复开发样本，未做新日期独立验证，也未接入模拟盘或券商。</p>',
          '<p><a href="comparison.json">日度、曲线与路径变化JSON</a> · <a href="warning_audit.json">逐笔提前预警审计</a> · <a href="verification.json">核验记录</a> · <a href="plan.json">固定研究参数</a></p></main></html>']
    path=OUT/'黄金期权_休市排除与靠山保护报告.html';path.write_text(''.join(h),'utf-8')
    write(OUT/'report_source_manifest.json',{str(__import__('pathlib').Path(__file__).relative_to(ROOT)):digest(__import__('pathlib').Path(__file__))})
    print(path)
    for p in LABELS:
        a=groups[p+'_through0'];b=groups[p+'_through1'];print(p,a['daily'],a['pnl_cny'],b['pnl_cny'])


if __name__=='__main__':main()
