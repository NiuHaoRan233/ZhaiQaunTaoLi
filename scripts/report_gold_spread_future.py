"""Reviewable0.2 report: wide-spread baselines, fusion failures and quote control."""
from pathlib import Path
from datetime import datetime
import json,base64
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from probe_gold_spread_future import OUT as V3,CODES
from validate_gold_spread_future import OUT as VAL
from probe_gold_spread_quote_control import OUT as V4
from report_gold_breakflat import table,clock,TZ
from probe_commodity_capital import ROOT,WORK,read,write,pack,unpack,digest

OUT=WORK/'reports/gold_spread_future_report_20260913'
LABELS=dict(base_long='8跳简单买后卖',base_short='8跳简单卖后买',trend_long='加期货逆向过滤·多头',trend_short='加期货逆向过滤·空头',
    value_top_long='加估值·直接挂卖',value_top_short='加估值·直接挂买',value_long='加估值及成本保护·多头',value_short='加估值及成本保护·空头',
    value_switch='估值择向',trend_switch='估值择向＋趋势',cancel_switch='再加期货提前撤单',manage_switch='再加期货解除持仓保护',
    throttle_long='旧节流＋估值多头',throttle_switch='旧节流＋估值择向')


def main():
    OUT.mkdir(exist_ok=True);folders={'v3':V3,'val':VAL,'v4':V4};sources={};ss={};ports={}
    for tag,folder in folders.items():
        sources.update(read(folder/('result_manifest.json' if tag=='v3' else 'manifest.json')))
        ss[tag]=read(folder/'results.json');ports[tag]=read(folder/'portfolios.json')
    for p,h in sources.items():assert digest(ROOT/p)==h
    def key(profile,spread=8,through=0,delay=0):return f'{profile}_spread{spread}_through{through}_delay{delay}'
    def stats(tag,profile,spread=8,through=0,delay=0):
        k=key(profile,spread,through,delay);p=ports[tag][k];a=[ss[tag][c+'_'+k] for c in CODES]
        return p,a
    sections=[];tables={}
    rows=[]
    for profile,label in LABELS.items():
        p,a=stats('v3',profile);strict,_=stats('v3',profile,through=1)
        rows.append([label,a[0]['pnl_cny'],a[1]['pnl_cny'],p['pnl_cny'],p['max_drawdown_cny'],str(p['cycles']),
            str(sum(x['order_count'] for x in a)),str(sum(x['reprice_cancel_count'] for x in a)),strict['pnl_cny']])
    tables['fusion']=rows
    sections.append('<h2>固定8跳：先把简单循环与每个新增机制分开</h2>'+table(['模式','C960净收益','C952净收益','合计净收益','组合最大回撤','闭环数','新委托数','因改价撤单数','严格穿价净收益'],rows))
    sections.append('<p>同2026-09-11日盘、09:30起、每合约最多一手、双侧各1.70元、0额外下单延迟；每次休市前5秒实际对手价清余仓。基础成交还要求已挂单、可辨识单次成交、推断对侧及价格可达，不是所有挂单都成交。表中每种模式独立运行，不相加。</p>'
        '<p>原goal的估值/有限退出直接叠加到8跳，合计只剩76.20元，C952为−117元。因此不能把原先有效的条件机械相加。趋势多头929元/回撤330.20元，是简洁路线；增强择向需要另外审计期货先更新时旧单是否仍有效。</p>')
    cp=read(VAL/'cancel_path_changes.json');cr=[]
    for k,x in cp.items():cr.append([k,str(x['same_cycles']),str(x['removed_cycles']),x['removed_net_cny'],str(x['added_cycles']),x['added_net_cny'],x['delta_pnl_cny'],x['delta_drawdown_cny']])
    tables['cancellation']=cr
    sections.append('<h2>期货提前撤单具体改善在哪里</h2>'+table(['合约/成交口径','不变闭环','旧路径独有笔数','旧独有净值','新路径独有笔数','新独有净值','净收益变化','单合约回撤变化'],cr)+
        '<p>C952保留6笔相同交易，避免旧3笔合计亏损490.20元；严格口径避免430.20元。C960成交完全相同。模拟保守保留撤单落在聚合成交区间内的旧单可能成交，不能用新信息删掉已经发生的坏成交。期货只触发撤开仓单；新订单仍等下一笔可用期权盘口，不用旧报价伪造先知成交。</p>')
    quote=[]
    for profile in ('trend_long','cancel_switch'):
        for tag,label in [('v3','原换价'),('v4','仅追价节流')]:
            p,a=stats(tag,profile);z,_=stats(tag,profile,through=1)
            quote.append([LABELS[profile]+' '+label,p['pnl_cny'],p['max_drawdown_cny'],str(p['cycles']),
                str(sum(x['order_count'] for x in a)),str(sum(x['reprice_cancel_count'] for x in a)),
                str(sum(x['throttle_skips'] for x in a)),z['pnl_cny'],z['max_drawdown_cny']])
    tables['quote_control']=quote
    sections.append('<h2>最后只加一个换价规则：少追价，及时退价</h2>'+table(['模式','净收益','回撤','闭环','新委托数','因改价撤单数','保持旧价次数','严格穿价净收益','严格回撤'],quote)+
        '<p>仅开仓追价受限：朝成交方向的价格变化至少2跳、旧单至少存在1秒，才追新价；两条件须同时成立。旧单还须满足价差、趋势、被动性及该模型的估值条件。行情转坏撤单、向安全方向退价、平仓和休市清仓不受节流限制。暂时保留旧价会落后最新最优档，不能同时宣称一直排第一。</p>'
        '<p>主版651→584个新委托，改价撤单250→183；增强版856→793、改价236→177。仍有大量未成交委托；这些是订单数而不是成交笔数，不能宣称低频改单问题已完全解决。</p>')
    mainrows=[]
    for profile,label in [('trend_long','0.2简洁主版'),('cancel_switch','0.2多空增强对照')]:
        for through in (0,1):
            p,a=stats('v4',profile,through=through)
            mainrows.append([label+('·严格穿价' if through else ''),a[0]['pnl_cny'],a[1]['pnl_cny'],p['pnl_cny'],p['max_drawdown_cny'],str(p['cycles']),
                str(sum(x['long_cycles'] for x in a)),str(sum(x['short_cycles'] for x in a)),a[0]['profit_without_best3_cny'],a[1]['profit_without_best3_cny']])
    tables['main']=mainrows
    sections.append('<h2>交付0.2：简洁主版与多空增强分开</h2>'+table(['版本','C960','C952','合计','组合回撤','闭环','多头闭环','空头闭环','C960去3大盈利','C952去3大盈利'],mainrows)+
        '<p><b>简洁主版：</b>价差≥8跳→同月期货10/60秒不显著逆向→买一加一跳开多、卖一减一跳平多→只限制开仓追价。无估值优势门槛、无双向成交/第二档过滤、无成本底价保护。期货趋势以已知源时间变动×估计delta折算到期权金额，逆向超过max(两跳,半价差)才否决；它是条件过滤，不是确定性未来价格预测。</p>'
        '<p><b>增强对照：</b>先过8跳，再用同步期货和快慢IV保守估值判定做多/做空哪边有优势，合格侧再过趋势过滤；有仓先平，期货先更新时撤掉不合格旧开仓单，保留原有限退出及追价节流。额外按期货逆向解除持仓保护使利润减少140元、回撤略增，故未加入交付分支。</p>'
        '<p>主版各2万元实跑保持全部订单/成交/盈亏，合计4万元：949元/2.3725%，回撤330.20元/初始本金0.8255%。增强版含裸卖期权，继续用每份25万元及20%名义准备金研究代理；不把它称为真实所需保证金，不按4万元计算其收益率。用户mAu夜盘/1元门槛是原理类比，本轮没有声称测试期权夜盘。</p>')
    validation=[]
    for profile,label in [('trend_long','简洁主版'),('cancel_switch','增强对照')]:
        for spread in (6,8,10):
            for delay in (0,500,1000):
                p,a=stats('v4',profile,spread,0,delay);q,b=stats('v4',profile,spread,1,delay)
                validation.append([label,str(spread),str(delay),a[0]['pnl_cny'],a[1]['pnl_cny'],p['pnl_cny'],p['max_drawdown_cny'],b[0]['pnl_cny'],b[1]['pnl_cny'],q['pnl_cny'],q['max_drawdown_cny']])
    tables['validation']=validation
    sections.append('<h2>不挑最高利润：固定8跳、零延迟，保留邻近退化</h2>'+table(['模式','价差跳数','额外期货延迟/ms','C960','C952','合计','回撤','严格C960','严格C952','严格合计','严格回撤'],validation)+
        '<p>简洁主版6跳时C952在所有这里的延迟/成交对照中都亏，不能说参数全面稳定；固定8跳两份在0/500/1000ms与两成交证据均正，10跳亦正。增强对照36个合约×门槛×延迟×成交样本均正，仍只是相关的单日开发样本。继续固定8跳/0延迟，没有挑更赚钱的6跳或500ms改主参数。</p>')
    attr=read(VAL/'attribution.json');ar=[]
    for k,x in attr.items():ar.append([k,x['entry_edge_cny'],x['holding_drift_cny'],x['exit_edge_cny'],x['fees_cny'],x['net_cny']])
    tables['attribution']=ar
    sections.append('<h2>钱来自价差，还是持仓期间价格移动</h2>'+table(['v3原换价账户','入场相对中价优势','持仓中价移动','退出相对中价优势','手续费','净收益'],ar)+
        '<p>入场优势＋持仓中价移动＋退出优势−费用=净收益，逐笔按实际成交时已见盘口重建。这是路径归因，不是证明每部分独立可赚，也不把期权涨跌全部解释成期货方向。</p>')
    plt.rcParams['font.family']='Microsoft YaHei';plt.rcParams['axes.unicode_minus']=False
    fig,axs=plt.subplots(2,1,figsize=(13,9),sharex=True)
    for tag,profile,label,color in [('v3','base_long','8跳简单买卖','#bb8a43'),('v4','trend_long','0.2简洁主版','#167d87'),('v4','cancel_switch','0.2多空增强','#6560a0')]:
        p,a=stats(tag,profile);c=np.asarray(p['curve']);ts=[datetime.fromtimestamp(t/1000,TZ) for t in c[:,0]]
        axs[0].step(ts,c[:,1]/100,where='post',label=label,color=color)
        dd=np.maximum.accumulate(np.maximum(c[:,1],0))-c[:,1];axs[1].step(ts,dd/100,where='post',label=label,color=color)
    for ax in axs:ax.grid(alpha=.15);ax.legend()
    axs[0].set_ylabel('累计净收益 / 元');axs[1].set_ylabel('距此前高点回撤 / 元');axs[1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M',tz=TZ))
    fig.suptitle('黄金期权大价差做市0.2：固定8跳，期货过滤与少追价\n2026-09-11单日 · 每次休市前实际清仓 · 未验证真实队列',fontsize=15)
    fig.tight_layout();png=OUT/'黄金大价差0.2收益与回撤.png';fig.savefig(png,dpi=145);plt.close(fig)
    sections.append('<h2>收益曲线与回撤</h2><img alt="收益与回撤曲线" src="data:image/png;base64,'+base64.b64encode(png.read_bytes()).decode()+'">')
    allrows=[];cycles={}
    for tag,folder in folders.items():
        for k,s in ss[tag].items():
            r=unpack(folder/f'{k}.json.gz');cycles[tag+'_'+k]=r['cycles']
            allrows.append([tag+'_'+k,s['pnl_cny'],s['max_drawdown_cny'],str(s['complete_cycles']),str(s['order_count']),str(s['reprice_cancel_count']),
                s['fees_cny'],s['worst_cycle_cny'],s['profit_without_best3_cny'],str(s['future_cancel_requests']),str(s['ambiguous_cancel_fills'])])
    tables['all_accounts']=allrows
    sections.append('<details><summary>248份研究/验证账户全部结果（含重复核验）</summary>'+table(['账户','净收益','回撤','闭环','新委托','改价撤单','费用','最差一笔','去3大盈利','期货撤单','聚合区间内仍成交'],allrows)+'</details>')
    for profile in ('trend_long','cancel_switch'):
        for code in CODES:
            cs=cycles['v4_'+code+'_'+key(profile)]
            sections.append('<details><summary>'+profile+' '+code+'全部交易</summary>'+table(
                ['开仓时间','方向','开仓价/报价元','平仓时间','平仓价/报价元','净收益/元','持仓秒','退出'],
                [[clock(c['entry_ts']),'多' if c['direction']==1 else '空',f"{c['entry_price_cents']/100000:.2f}",clock(c['exit_ts']),f"{c['exit_price_cents']/100000:.2f}",c['net_cents']/100,c['duration_seconds'],c['exit_kind']] for c in cs])+'</details>')
    sections.append('<h2>完整规则、复现与核验</h2><p>策略包：广义套利/gold_intraday_strategy_v02/README.md。仓库根目录执行<code>.\\.venv\\Scripts\\python.exe -X utf8 scripts\\run_gold_intraday_v02.py</code>，重放固定两分支×双合约×两成交口径的8账本，与冻结字典逐字段一致，没有网络或交易接口。</p>'
        '<p>248研究/验证账户＋16小资金复现＋8交付复现，合计272份核验记录、816个空仓边界；重复账户不算新独立证据。资金/费用/库存/回撤与14:00截断前缀通过，旧基线经济等价，撤单因果时序和持仓路径归因检查通过。旧模型/来源哈希保留，不改实时矩阵。静态HTML表金额和曲线核验，未做浏览器截图。</p>'
        '<p>只有一个已见开发日，主版4/11笔、增强版5/6笔，不能因胜率或36格为正宣称稳定盈利。主版去各合约前三大盈利仍负，增强版样本同样小。真实队列、真实到达顺序、夜盘、新日期及券商卖方保证金均未验证。0.2是离线研究版本。</p>')
    overview=dict(tables=tables,research_accounts=len(allrows),small_accounts=16,reproduction_accounts=8,boundaries=816,
        selected_family='probe_gold_spread_quote_control_20260913_v4',main_profile='trend_long',enhanced_profile='cancel_switch',fixed_spread=8,
        main_cash_per_code=20000,main_pnl=949,main_return_pct=949/40000*100,main_dd=330.2)
    write(OUT/'overview.json',overview);pack(OUT/'全部逐笔交易.json.gz',cycles)
    css='body{font-family:"Microsoft YaHei",sans-serif;max-width:1500px;margin:auto;padding:30px;background:#f4f7f7;color:#26383a}p,li{line-height:1.85}h1{font-size:31px}h2{margin-top:36px;color:#176f79}table{border-collapse:collapse;width:100%;background:white;font-size:13px}td,th{padding:9px;border-bottom:1px solid #dce5e5;text-align:right;white-space:nowrap}th{background:#e3eded}td:first-child,th:first-child{text-align:left}.scroll{overflow:auto;max-height:650px}details{padding:15px;background:white;margin:15px 0}summary{cursor:pointer;font-weight:bold}img{width:100%}'
    doc='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>黄金期权大价差做市0.2</title><style>'+css+'</style><body><h1>黄金期权大价差做市0.2</h1><p><b>先限定价差，再用期货信息排除不利开仓；减少追价，不拖延撤单和平仓。</b></p>'+''.join(sections)+'</body></html>'
    path=OUT/'黄金期权大价差做市0.2报告.html';path.write_text(doc,encoding='utf-8')
    write(OUT/'source_manifest.json',{**sources,str(Path(__file__).relative_to(ROOT)):digest(Path(__file__))})
    print(path)


if __name__=='__main__':main()
