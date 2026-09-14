"""Report immutable pre-break replay alongside its actual saved parent."""
import base64
import html
import json
from pathlib import Path
from collections import Counter
from datetime import datetime
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from probe_gold_breakflat import OUT as BASE
from probe_gold_state import OUT as OLD
from report_gold_state import aggregate, TZ
from probe_commodity_capital import ROOT, WORK, read, write, unpack, digest

OUT=WORK/'reports/gold_breakflat_report_20260913'


def clock(ts):
    return datetime.fromtimestamp(ts/1000,TZ).strftime('%H:%M:%S.%f')[:-3] if ts is not None else '—'


def fmt(x):
    return f'{x:,.2f}' if isinstance(x,(int,float)) else html.escape(str(x))


def table(head, rows):
    return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+html.escape(x)+'</th>' for x in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+fmt(x)+'</td>' for x in r)+'</tr>' for r in rows)+'</tbody></table></div>'


def cycle_key(c):
    return (c['entry_ts'],c['exit_ts'],c['entry_price_cents'],c['gross_cents'],c['net_cents'])


def display_curve(a):
    # All four ledgers proved flat: extend the final unchanged cash value to15:00.
    return a+[[1789110000000,*a[-1][1:]]] if a[-1][0]<1789110000000 else a


def main():
    OUT.mkdir(exist_ok=True)
    sources={}
    for folder in (BASE,OLD):
        for name,h in read(folder/'result_manifest.json').items():
            assert digest(ROOT/name)==h
            sources[name]=h
    verification=read(BASE/'verification.json');assert verification['status']=='passed'
    selection=read(OLD/'selection.json');codes=selection['selected'];profiles=['gap','control']
    rows={v:{c+'_'+p:unpack(folder/f'{c}_{p}.json.gz') for c in codes for p in profiles}
          for v,folder in [('old',OLD),('new',BASE)]}
    ports={v:{p:aggregate([rs[c+'_'+p] for c in codes]) for p in profiles} for v,rs in rows.items()}
    results=[];stats={};boundaries=[];diffs=[];session_rows=[]
    for code in codes:
        for p in profiles:
            key=code+'_'+p;r=rows['new'][key];s=r['summary'];old=rows['old'][key];os=old['summary']
            assert round(s['pnl_cny']*100)==sum(x['net_cents'] for x in r['cycles'])
            pos=[x['net_cents']/100 for x in r['cycles'] if x['net_cents']>0]
            neg=[x['net_cents']/100 for x in r['cycles'] if x['net_cents']<0]
            stats[key]=dict(win_rate_pct=len(pos)/len(r['cycles'])*100,mean_win_cny=float(np.mean(pos)),
                mean_loss_cny=float(np.mean(neg)),largest_loss_cny=min(neg),
                longest_hold_seconds=max(x['duration_seconds'] for x in r['cycles']))
            label='断档过滤' if p=='gap' else '无断档过滤对照'
            results.append([code,label,os['pnl_cny'],s['realized_gross_cny'],s['fees_cny'],s['pnl_cny'],
                s['pnl_cny']-os['pnl_cny'],f"{s['pnl_cny']/150000*100:.4f}%",str(s['complete_cycles']),
                str(s['active_close_count']),s['max_drawdown_cny']])
            for b in r['boundary_checks']:
                fs=[f for f in r['fills'] if f['ts']<b['ts']]
                inv=sum(1 if f['side']=='buy' else -1 for f in fs)
                cutoff_inv=sum(1 if f['side']=='buy' else -1 for f in fs if f['ts']<b['ts']-60000)
                assert inv==b['inventory']==0
                boundaries.append([code,label,clock(b['ts']),str(cutoff_inv),str(inv),
                    clock(fs[-1]['ts']) if fs else '—',clock(b['last_event_ts'])])
            a=Counter(cycle_key(c) for c in old['cycles']);b=Counter(cycle_key(c) for c in r['cycles']);common=a&b
            removed=a-common;added=b-common
            oldnet=sum(k[-1]*n for k,n in removed.items())/100
            newnet=sum(k[-1]*n for k,n in added.items())/100
            assert round((newnet-oldnet)*100)==round((s['pnl_cny']-os['pnl_cny'])*100)
            diffs.append([code,label,str(sum(common.values())),str(sum(removed.values())),oldnet,
                str(sum(added.values())),newnet,newnet-oldnet])
            for session,end in [('第一小节',1789092900000),('第二小节',1789097400000),('下午',1789110000000)]:
                start={'第一小节':1789090200000,'第二小节':1789093800000,'下午':1789104600000}[session]
                cs=[c for c in r['cycles'] if start<=c['entry_ts']<end]
                assert all(c['exit_ts']<end for c in cs)
                session_rows.append([code,label,session,str(len(cs)),sum(c['gross_cents'] for c in cs)/100,
                    sum(c['fees_cents'] for c in cs)/100,sum(c['net_cents'] for c in cs)/100])
    plt.rcParams['font.family']='Microsoft YaHei';plt.rcParams['axes.unicode_minus']=False
    fig,axes=plt.subplots(3,1,figsize=(13,11),sharex=True,gridspec_kw={'height_ratios':[2,2,1]})
    for v,color in [('old','#8993a2'),('new','#1666ac')]:
        a=display_curve(ports[v]['gap']['curve']);axes[0].step([datetime.fromtimestamp(x[0]/1000,TZ) for x in a],
            [x[1]/30000000*100 for x in a],where='post',color=color,lw=1.5,
            label=('原版（允许跨休市）' if v=='old' else '新版（休市前空仓）')+f"  {ports[v]['gap']['return_pct']:+.4f}%")
    axes[0].set_ylabel('组合累计收益率 / %');axes[0].set_title('2026-09-11 黄金期权：休市前清仓后的完整重跑\n断档过滤主策略 · 每合约15万元，共30万元 · 单边1.70元 · 额外延迟0')
    for code,color in zip(codes,['#146cb0','#ce7921']):
        a=display_curve(rows['new'][code+'_gap']['curve']);xs=[datetime.fromtimestamp(x[0]/1000,TZ) for x in a]
        axes[1].step(xs,[x[1]/100 for x in a],where='post',color=color,label=code)
        axes[2].step(xs,[x[2] for x in a],where='post',color=color,label=code,alpha=.75)
    axes[1].set_ylabel('新版累计净盈亏 / 元');axes[2].set_ylabel('新版库存 / 手');axes[2].set_yticks([0,1]);axes[2].set_ylim(-.15,1.3)
    for ax in axes:
        ax.axhline(0,color='#666',lw=.6);ax.grid(alpha=.15);ax.legend(loc='lower left',fontsize=9,ncol=2)
        for lo,hi in [(1789092900000,1789093800000),(1789097400000,1789104600000)]:
            ax.axvspan(datetime.fromtimestamp(lo/1000,TZ),datetime.fromtimestamp(hi/1000,TZ),color='#ddd',alpha=.35)
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M',tz=TZ))
    axes[-1].set_xlabel('北京时间；灰区为休市。阶梯估值，不对休市价格作线性插值。')
    fig.tight_layout();chart=OUT/'休市前清仓收益率与库存.png';fig.savefig(chart,dpi=150);plt.close(fig)
    audit_path=WORK/'reports/gold_break_exposure_audit_20260913/cross_break_positions.json'
    jumps=read(audit_path);sources[str(audit_path.relative_to(ROOT))]=digest(audit_path)
    jump_rows=[[j['code'],j['break_name'],j['entry'],j['exit'],j['float_before_break_cny'],j['reopen_mark_jump_cny'],j['net_cycle_cny']] for j in jumps if j['profile']=='gap']
    portfolio_rows=[[('断档过滤主策略' if p=='gap' else '无断档过滤对照'),
        ports['old'][p]['pnl_cny'],ports['new'][p]['pnl_cny'],f"{ports['new'][p]['return_pct']:.4f}%",
        ports['old'][p]['max_drawdown_cny'],ports['new'][p]['max_drawdown_cny'],f"{ports['new'][p]['max_drawdown_pct']:.4f}%"] for p in profiles]
    stat_rows=[[k,'%.2f%%'%s['win_rate_pct'],s['mean_win_cny'],s['mean_loss_cny'],s['largest_loss_cny'],s['longest_hold_seconds']] for k,s in stats.items()]
    full=[]
    for key,r in rows['new'].items():
        cs=[[str(i+1),clock(c['entry_ts']),clock(c['exit_ts']),c['entry_price_cents']/100000,
            (c['entry_price_cents']+c['gross_cents'])/100000,c['duration_seconds'],c['gross_cents']/100,
            c['fees_cents']/100,c['net_cents']/100,'休市前主动卖出' if c.get('exit_kind')=='pre_break_active_sell' else '正常被动成交'] for i,c in enumerate(r['cycles'])]
        full.append('<details><summary>'+html.escape(key)+f"：{len(cs)}笔完整交易</summary>"+
            table(['序号','买入时间','卖出时间','买入报价','卖出报价','持仓秒','毛收益/元','费用/元','净收益/元','退出方式'],cs)+'</details>')
    sections=[
        '<h1>黄金期权：休市前清仓回测</h1><p class="sub">2026年9月11日 · 固定同两个合约与筛选条件 · 四账户独立重跑</p>',
        '<div class="callout"><b>风险约束通过，盈利检验未通过。</b><p>断档过滤主策略由+782.80元变为−1,329.20元（−0.4431%）；无断档过滤对照为−1,622.80元。四账户的三个休市边界均为空仓，零跨休市持仓、零成本虚拟结算。</p><p>本样本提前停止开仓后，已有持仓均通过正常被动成交退出，实际主动清仓次数为0。主动出口由边界测试验证，不能把本次历史结果说成实测了主动成交能力。</p></div>',
        '<h2>已确认规则与本轮参数</h2><p>10:15、11:30、15:00前均不能留敞口。10:10／11:25／14:55起停止新买并撤余下买单；10:14／11:29／14:59起，剩余一手按新到达盘口的有效买一（至少一手）主动模拟卖出，盈亏和单边1.70元照计。原已挂订单先按因果成交证据处理，不能回溯删除已成交买单。</p><p>提前5分钟和1分钟是本轮统一实施参数，没有按盈利寻优。若窗口内无可用买一，回测明确报错，不能拿成本或复市价伪造休市前平仓。日盘范围未扩展到夜盘。</p>',
        '<h2>合约日度结果</h2>'+table(['合约','模式','原净收益/元','新毛收益/元','新费用/元','新净收益/元','净变化/元','新收益率','闭环数','主动退出数','新最大回撤/元'],results),
        '<h2>组合收益与回撤</h2>'+table(['模式','原净收益/元','新净收益/元','新收益率','原最大回撤/元','新最大回撤/元','新最大回撤率'],portfolio_rows),
        '<p>每合约独立15万元、最大一手；每种模式合计30万元。组合回撤将同一时间的净值变化合并后计算，不把两合约最大回撤直接相加。收益率分母是初始资金，回撤率分母是此前净值峰值。</p>',
        '<img alt="新旧主策略收益率、新版两合约盈亏及库存阶梯图" src="data:image/png;base64,'+base64.b64encode(chart.read_bytes()).decode()+'">',
        '<h2>12个休市边界逐项核验</h2>'+table(['合约','模式','休市时间','清仓窗口前库存','休市库存','此前最后成交','此前最后行情'],boundaries),
        '<h2>旧版跳价证据</h2>'+table(['合约','休市','买入','卖出','休市前浮盈亏/元','复市买一跳变贡献/元','整笔净收益/元'],jump_rows),
        '<p>C952午休一笔11:29:49买入，午休前浮亏800元；复市买一跳升贡献2,060元，整笔净赚1,476.60元。两笔上午休市跳变合计−1,100元，三笔跳变净贡献+960元。这只是旧持仓路径的盯市归因，不能拿旧利润简单减960元当新回测。</p>',
        '<h2>新旧完整交易路径差异</h2>'+table(['合约','模式','完全相同闭环数','仅旧版闭环数','仅旧版净值/元','仅新版闭环数','仅新版净值/元','新减旧/元'],diffs),
        '<p>按买入时间、卖出时间、成交价与净收益严格匹配。本次主策略保留的238笔与原版全部一致；少做20笔、原净收益共2,112元，其中3笔跨休市净赚589.80元，另外17笔原本在休市前能完成、净赚1,522.20元。后者是提前5分钟停止开仓牺牲的机会，不能把全部收益下降说成取消跨休市收益。5分钟不是已经验证最优的参数；未来可以固定清仓约束后另立版本研究缩短停开窗口，本轮不据结果重选参数。</p>',
        '<h2>新版分交易小节损益</h2>'+table(['合约','模式','交易小节','闭环数','毛收益/元','费用/元','净收益/元'],session_rows),
        '<h2>新版盈亏结构</h2>'+table(['账户','胜率','平均盈利/元','平均亏损/元','最大单笔亏损/元','最长持仓秒'],stat_rows),
        '<p>C960毛收益仅20元，费用438.60元；C952毛收益已经亏540元，再扣费用370.60元。亏损既有费用磨损，也有持仓期间的不利价格变化，当前入场和退出规则还不能证明稳定赚取价差。后续应在这一真实计损约束下研究，不恢复跨休市风险来美化收益。</p>',
        '<h2>全部新版逐笔交易</h2>'+''.join(full),
        '<h2>验证与适用范围</h2><p>14项黄金测试及98项既有商品测试通过；四账户逐笔现金、库存和费用重建，12边界独立复核；10:10前与保存父版订单/成交经济一致；独立截断重跑到14:00订单/成交前缀一致。父版85个源与结果文件哈希保持。历史样本无主动退出，主动亏损、无买一、晚到有效买一和旧单优先由新增8项测试覆盖。</p><p>选择仍只用09:00—09:30，09:30后交易；选择的au2610C960/C952均为十月合约对应的虚值认购，期权9月23日到期，风险相关。只有一个开发日；被动成交使用L1末价一手证据，未显式模拟真实排队。0额外延迟是用户设定，主动卖出是按显示买一的零额外滑点模拟，不是券商成交保证。只读离线研究，未进入实时矩阵。</p><p class="sub">原始摘要继承父类的latest_reason=day_closed_at_entry_cost文本标签，本版平日末只在库存为0时调用父类记账；该旧标签不代表发生了成本退出。以settlement、virtual_cost_close_allowed=false、实际逐笔及12边界记录为准。冻结原件保留，报告未静默改写它。</p>'
    ]
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>黄金期权休市前清仓回测</title><style>body{margin:0;background:#f2f5f9;color:#193047;font:15px/1.75 "Microsoft YaHei",sans-serif}main{max-width:1480px;margin:auto;padding:32px}h1{font-size:29px}h2{margin-top:32px;font-size:21px}.sub{color:#607387}.callout{background:#fff;border-left:5px solid #ce7921;padding:20px}img{width:100%;background:#fff;margin:24px 0}.scroll{overflow:auto;max-height:580px;background:white}table{width:100%;border-collapse:collapse;white-space:nowrap;font-size:13px}td,th{padding:9px;text-align:right;border-bottom:1px solid #e3e9f1}th{position:sticky;top:0;background:#e7eef5}td:first-child,th:first-child{text-align:left}details{padding:15px 0}summary{cursor:pointer;font-weight:bold}</style><main>'+''.join(sections)+'</main></html>'
    target=OUT/'黄金期权休市前清仓回测.html';target.write_text(page,encoding='utf8')
    payload=dict(date='20260911',portfolios=ports,statistics=stats,ledgers=rows,verification=verification,
        boundary_audit=boundaries,cycle_differences=diffs,session_results=session_rows,old_break_exposures=jumps)
    write(OUT/'新旧逐笔与组合核验.json',payload)
    sources[str(Path(__file__).relative_to(ROOT))]=digest(Path(__file__))
    write(OUT/'delivery_source_manifest.json',sources)
    write(OUT/'report_qa.json',dict(status='passed',account_count=4,boundary_count=12,
        displayed_new_cycles=sum(len(r['cycles']) for r in rows['new'].values()),
        active_exit_count=sum(r['summary']['active_close_count'] for r in rows['new'].values()),
        no_javascript_required=True,browser_screenshot_qa=False,
        legacy_day_close_reason_disclosed=True,cash_fee_and_difference_totals_checked=True))
    write(OUT/'artifact_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='artifact_manifest.json'})
    print(json.dumps(dict(report=str(target),portfolios={v:{p:{k:x for k,x in a.items() if k!='curve'} for p,a in ps.items()} for v,ps in ports.items()},statistics=stats),ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
