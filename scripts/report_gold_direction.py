"""Self-contained two-contract directional research, including failed variants."""
from pathlib import Path
from datetime import datetime
import base64
import html
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from zhaiquant import gold_direction_research as g
from probe_gold_direction import OUT as V1,CODES
from probe_gold_direction_timing import OUT as V2
from probe_gold_long_refinement import OUT as V3
from report_gold_breakflat import table,clock,TZ
from probe_commodity_capital import ROOT,WORK,read,write,pack,unpack,digest

OUT=WORK/'reports/gold_direction_report_20260913'
NAMES=dict(long='纯买后卖',short='纯卖后买',long_trend='做多＋期货趋势',short_trend='做空＋期货趋势',
    fair_long='做多＋期货定价',fair_short='做空＋期货定价',fair_switch='按定价择边',
    fair_trend_switch='定价择边＋趋势',fair_risk_switch='定价择边＋趋势＋提前撤单（修订）',
    fair_top_switch='定价择边＋趋势＋提前撤单＋即时跟档退出（修订）',
    fair_long_trend='做多定价＋趋势',fair_long_risk='做多定价＋提前撤单',
    fair_long_trend_risk='做多定价＋趋势＋提前撤单',fair_long_top='做多定价＋提前撤单＋即时跟档退出')


def portfolio(rs):
    a=[]
    for r in rs:
        x=np.asarray(r['curve'],dtype=np.int64);a.append(np.column_stack((x[:,0],np.diff(x[:,1],prepend=0))))
    a=np.concatenate(a);a=a[np.argsort(a[:,0],kind='stable')]
    ts,idx=np.unique(a[:,0],return_index=True);pnl=np.cumsum(np.add.reduceat(a[:,1],idx));capital=2*g.CAPITAL
    peak=np.maximum.accumulate(np.maximum(capital+pnl,capital));dd=peak-capital-pnl
    assert int(pnl[-1])==sum(round(r['summary']['pnl_cny']*100) for r in rs)
    return dict(curve=np.column_stack((ts,pnl)).tolist(),pnl_cny=float(pnl[-1]/100),
        return_pct=float(pnl[-1]/capital*100),max_drawdown_cny=float(dd.max()/100),
        max_drawdown_pct=float((dd/peak*100).max()),initial_cash_cny=capital/100)


def main():
    OUT.mkdir(exist_ok=True);rows={};sources={};qa=[]
    for tag,folder in [('v1',V1),('v2',V2),('v3',V3)]:
        v=read(folder/'verification.json');assert v['status']=='passed';qa.append(v)
        for p,h in read(folder/'result_manifest.json').items():assert digest(ROOT/p)==h;sources[p]=h
        for key in read(folder/'results.json'):rows[tag+'_'+key]=unpack(folder/f'{key}.json.gz')
    def get(code,profile,settlement,delay=0):
        tag='v3' if profile.startswith('fair_long_') else 'v2' if profile in ('fair_risk_switch','fair_top_switch') or delay else 'v1'
        suffix=f'_d{delay}' if tag!='v1' else ''
        return rows[f'{tag}_{code}_{profile}_{settlement}{suffix}']
    performance=[];ports={};concentration=[]
    for p in NAMES:
        cs=[get(c,p,'cost')['summary'] for c in CODES];ms=[get(c,p,'market')['summary'] for c in CODES]
        ports[p]={s:portfolio([get(c,p,s) for c in CODES]) for s in ('cost','market')}
        performance.append([NAMES[p],cs[0]['pnl_cny'],cs[1]['pnl_cny'],sum(x['pnl_cny'] for x in cs),
            ms[0]['pnl_cny'],ms[1]['pnl_cny'],sum(x['pnl_cny'] for x in ms),
            str(sum(x['complete_cycles'] for x in ms)),ports[p]['market']['max_drawdown_cny']])
        for c in CODES:
            r=get(c,p,'market');nets=sorted([x['net_cents']/100 for x in r['cycles']],reverse=True)
            concentration.append([c,NAMES[p],r['summary']['pnl_cny'],r['summary']['pnl_cny']-sum(max(0,x) for x in nets[:3]),
                max(nets,default=0),min(nets,default=0),str(sum(x>0 for x in nets)),str(sum(x<0 for x in nets))])
    delayrows=[]
    for p in ('fair_long','fair_switch','fair_long_trend','fair_long_risk','fair_long_trend_risk','fair_long_top'):
        for delay in (0,500,1000):
            ss=[get(c,p,'market',delay)['summary'] for c in CODES]
            delayrows.append([NAMES[p],str(delay),ss[0]['pnl_cny'],ss[1]['pnl_cny'],sum(x['pnl_cny'] for x in ss)])
    attribution=read(WORK/'reports/gold_direction_attribution_20260913/attribution.json')
    lag=read(WORK/'reports/gold_direction_attribution_20260913/leadlag.json')
    ar=[]
    for c in CODES:
        for p in ('long','short','fair_long','fair_short','fair_switch'):
            m=attribution[f'{c}_{p}_market'];a=m['components_cny'];s=get(c,p,'market')['summary']
            assert abs(a['entry_edge_cents']+a['book_drift_cents']+a['exit_edge_cents']-s['fees_cny']-s['pnl_cny'])<1e-6
            ar.append([c,NAMES[p],a['entry_edge_cents'],a['book_drift_cents'],a['exit_edge_cents'],s['fees_cny'],s['pnl_cny'],
                a['fixed_entry_iv_futures_drift_cents'],a['residual_book_drift_cents']])
    lr=[[f"{a['option_lag_seconds']:+.1f}",f"{a['correlation']:.4f}",f"{b['correlation']:.4f}"] for a,b in zip(lag[CODES[0]],lag[CODES[1]])]
    plt.rcParams['font.family']='Microsoft YaHei';plt.rcParams['axes.unicode_minus']=False
    fig,axs=plt.subplots(2,1,figsize=(13,9),sharex=True)
    for p,color in [('long','#818c99'),('fair_long','#196bb0'),('fair_long_risk','#bf7919')]:
        a=ports[p]['market']['curve'];axs[0].step([datetime.fromtimestamp(x[0]/1000,TZ) for x in a],[x[1]/(2*g.CAPITAL)*100 for x in a],where='post',label=NAMES[p],color=color)
    a=ports['fair_long']['cost']['curve'];axs[0].step([datetime.fromtimestamp(x[0]/1000,TZ) for x in a],[x[1]/(2*g.CAPITAL)*100 for x in a],where='post',label='做多定价 · 成本虚拟结算',color='#196bb0',ls='--',alpha=.65)
    axs[0].set_title('2026-09-11 固定两份黄金期权：期货定价与买卖方向研究\n各25万元、合计50万元 · 双侧各1.70元 · 不持仓跨休市')
    axs[0].set_ylabel('组合累计收益率 / %')
    for c,color in zip(CODES,['#196bb0','#bf7919']):
        a=get(c,'fair_long','market')['curve'];axs[1].step([datetime.fromtimestamp(x[0]/1000,TZ) for x in a],[x[1]/100 for x in a],where='post',label=c+' · 做多定价实际计损',color=color)
    axs[1].set_ylabel('累计净盈亏 / 元')
    for ax in axs:
        ax.axhline(0,color='#888',lw=.5);ax.grid(alpha=.2);ax.legend(fontsize=9,ncol=2)
        for lo,hi in zip(g.BOUNDARIES[:2],g.SESSION_STARTS[1:]):ax.axvspan(datetime.fromtimestamp(lo/1000,TZ),datetime.fromtimestamp(hi/1000,TZ),color='#ddd',alpha=.3)
    axs[-1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M',tz=TZ));axs[-1].set_xlabel('北京时间；实线为休市前5秒按盘口计损，虚线为边界成本虚拟结算')
    fig.tight_layout();chart=OUT/'双方向与期货定价收益曲线.png';fig.savefig(chart,dpi=150);plt.close(fig)
    full=[];summary_rows=[];vrows=[];sessionrows=[]
    for key,r in rows.items():
        s=r['summary'];summary_rows.append([key,s['pnl_cny'],s['market_cycle_net_cny'],str(s['virtual_close_count']),s['removed_tail_gross_cny'],
            str(s['complete_cycles']),s['fees_cny'],s['max_drawdown_cny'],s['max_reserve_cny'],str(s['margin_breach_frames']),str(s['future_cancel_requests']),str(s['ambiguous_cancel_fills'])])
    for c in CODES:
        for p in NAMES:
            for settlement in ('cost','market'):
                r=get(c,p,settlement)
                for x in r['cycles']:
                    if x['exit_kind']=='virtual_cost_close':vrows.append([c,NAMES[p],clock(x['entry_ts']),clock(x['exit_ts']),
                        '多' if x['direction']==1 else '空',x['quote_mark_gross_before_close_cents']/100,x['net_cents']/100,x['reference_quote_age_seconds']])
                for i,(lo,hi) in enumerate(zip(g.SESSION_STARTS,g.BOUNDARIES)):
                    cs=[x for x in r['cycles'] if lo<=x['entry_ts']<hi]
                    sessionrows.append([c,NAMES[p],settlement,str(i+1),str(len(cs)),sum(x['net_cents'] for x in cs)/100])
                if p in ('fair_long','fair_long_risk','fair_long_trend_risk'):
                    cr=[[str(i+1),'多' if x['direction']==1 else '空',clock(x['entry_ts']),clock(x['exit_ts']),x['entry_price_cents']/100000,
                        x['exit_price_cents']/100000,x['net_cents']/100,x['duration_seconds'],x['exit_kind']] for i,x in enumerate(r['cycles'])]
                    full.append('<details><summary>'+html.escape(c+' / '+NAMES[p]+' / '+settlement)+f"：{len(cr)}笔</summary>"+
                        table(['序号','方向','开仓','平仓','开仓报价','平仓报价','净收益/元','持仓秒','退出类型'],cr)+'</details>')
    lead=ports['fair_long'];risk=ports['fair_long_risk']
    sections=[
        '<h1>固定双黄金：方向、定价、趋势与提前撤单</h1><p class="sub">2026年9月11日开发日 · au2610C960 / au2610C952 · 共120个独立情景账户，不能把不同情景收益相加</p>',
        f'<div class="callout"><b>已找到改善机制，尚未证明完整盈利模式。</b><p>做多＋期货定价：成本虚拟结算合计{lead["cost"]["pnl_cny"]:,.2f}元；实际计损对照{lead["market"]["pnl_cny"]:,.2f}元、收益率{lead["market"]["return_pct"]:.4f}%。两合约实际计损分别+653.00和+73.40元，但C952删去三笔最大盈利后−736.40元，仍是薄弱环节。</p><p>追加做多定价＋提前撤单：成本口径{risk["cost"]["pnl_cny"]:,.2f}元、实际计损{risk["market"]["pnl_cny"]:,.2f}元。下面保留所有改进与退化结果，不按单日最优数直接定版。</p></div>',
        '<h2>这次怎样交易</h2><p>初始每合约25万元，最多一手多仓或一手空仓，空仓时只挂一个有利方向；双侧各1.70元，额外下单延迟0。纯买后卖与纯卖后买都做了；双边择向按扣费后的理论价偏离更大的一边挂单。买单在买一改善一跳，卖单在卖一改善一跳；入场先检查净空间、近期双向成交与相应一二档断层。默认保留有限5分钟成本保护，另测跟档退出。</p><p>按你最新指定，成本研究在10:15、11:30、15:00将余仓按入场价虚拟退出，毛盈亏0、两侧手续费保留；正常亏损不删除。实际计损对照在休市前5秒停开，剩余多仓吃买一、空仓吃卖一，亏损与费用照计。两种模式各自重跑，价格和入场路径差异不能仅用尾仓浮亏相减替代。</p>',
        '<h2>定价、趋势与撤单的可复现规则</h2><ol><li>只使用同月au2610期货，不用十二月主力价格替代。用严格历史期权中价反推隐含波动率，按60秒时间常数平滑；先用旧波动率和最新已知期货价重估，再吸收当前期权报价。</li><li>Black-76零利率欧式近似用于相对估值。黄金期权美式条款、波动率曲面和真实合理价误差未消除，不能把模型差额等同无风险套利。</li><li>做多优势=参考价−买限价−双费；做空优势=卖限价−参考价−双费，均要求至少max(一跳，盘口价差四分之一)。同价优势相同优先多；仅有一边合格就只挂该边。</li><li>趋势过滤以delta换算期货过去10秒及60秒变动，若逆向超过max(两跳，盘口半价差)则禁止该方向。每小节重置并预热一分钟；不把午休前波动率或趋势直接带过来。</li><li>新增期货风险可触发撤单；若撤单发生在期权聚合成交区间内部，旧单仍可能成交，保守保留它。同时间戳按期权先、期货后处理，已处理的同刻信息可用于之后的撤单判断，不能回溯用于之前的下单。</li></ol>',
        '<h2>14种交易模式：两份合约全展示</h2>'+table(['模式','C960成本/元','C952成本/元','成本合计/元','C960实际/元','C952实际/元','实际合计/元','实际闭环数','实际组合最大回撤/元'],performance),
        '<img alt="定价和提前撤单收益率与两合约净值曲线" src="data:image/png;base64,'+base64.b64encode(chart.read_bytes()).decode()+'">',
        '<h2>为什么亏钱：逐笔中价分解</h2>'+table(['合约','模式','入场成交优势/元','持仓中价变化/元','退出成交优势/元','费用/元','净收益/元','固定入场IV期货变化/元','剩余中价变化/元'],ar),
        '<p>毛收益=方向×(入场中价−入场成交价)+方向×(退出中价−入场中价)+方向×(退出成交价−退出中价)。逐笔等式精确核对。后二列将持仓中价变化再拆成固定入场IV下的期货价格/时间变化与残差；残差包含IV、报价变化与模型误差，不能全部命名为波动率利润。例：C952纯做空两端优势合计790元，持仓中价逆向亏2,190元，费用380.80元后净−1,780.80元。</p>',
        '<h2>利润是否集中</h2>'+table(['合约','模式','实际净收益/元','删去前三盈利后/元','最大盈利/元','最大亏损/元','盈利笔数','亏损笔数'],concentration),
        '<h2>期货信号晚到0.5秒／1秒</h2>'+table(['模式','人为追加期货延迟/ms','C960实际/元','C952实际/元','合计/元'],delayrows),
        '<p>该项改变期货信号可用时刻，不是改变用户的下单延迟0；保留原始行情时间、仍按源报价年龄检查。延迟改变IV对齐和入场路径，收益可能非单调，不能挑500毫秒的高利润作为自动推荐参数。</p>',
        '<h2>期货是否领先期权</h2>'+table(['期权相对期货滞后/秒','C960收益变动相关系数','C952收益变动相关系数'],lr),
        '<p>按各连续小节500毫秒网格，使用已知报价，计算一秒中价变动。两份同时点相关约0.39/0.37，期权晚一秒相关约0.22/0.25，晚两秒已明显下降。这是单日描述性相关、窗口重叠且时钟到达顺序未知，不能证明真实订单有固定一秒的免费套利窗口。未用这些事后统计反向修改当日阈值。</p>',
        '<h2>所有成本虚拟尾仓</h2>'+table(['合约','模式','开仓','边界','方向','归零前毛浮盈亏/元','虚拟闭环净值/元','参考价年龄/秒'],vrows),
        '<h2>分小节表现</h2>'+table(['合约','模式','结算口径','小节','闭环数','净收益/元'],sessionrows),
        '<h2>做多定价候选逐笔（含改进对照）</h2>'+''.join(full),
        '<h2>120账户完整摘要与错误初版保留</h2>'+table(['账户','总净值/元','非虚拟闭环净值/元','虚拟次数','归零前浮盈亏/元','闭环数','费用/元','最大回撤/元','最大风险准备金/元','不足资金帧','提前撤单数','区间内撤单仍成交数'],summary_rows),
        '<p>v1主动撤单组将同刻已处理的期权校准错误判断为不可用，产生大量撤单和接近零交易，保留为错误记录；主表改用v2修订。v2只修复该时序，额外测试定价做多/择边的信号延迟。v3在定价做多的机制改善后，预先列出四种趋势/撤单/退出组合并保留全部延迟结果。旧版没有静默覆盖。</p>',
        '<h2>验证与尚未完成的部分</h2><p>120账户逐笔资金、正负持仓、费用、休市零仓及14:00独立截断前缀核验通过，共360个休市边界。30项黄金测试与98项既有商品测试通过。新纯做多在第一休市前与保存父版成交的时间、方向、价格及费用完全一致。新模型决策严格使用已知来源时间；两份完整期权与同月期货数据哈希保存，原文件保持。</p><p>卖方准备金按20%期货名义金额＋期权回购金额检查，是研究压力假设，不是已核实的券商保证金。每种模式两份合计50万元，不能将120个不同情景合并收益；旧15万元长仓账户保持。所有交易均为离线模拟。</p><p>尚缺真实到达顺序/队列验证、估值误差的稳健处理和C952盈利集中问题的解决；仅最新一个开发日，未声称样本外、年化或长期稳定。当前可继续研究做多定价机制，但完整可行模式仍需继续验证与改进。</p>'
    ]
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>黄金期权双方向与期货定价研究</title><style>body{margin:0;background:#f2f5f9;color:#193047;font:15px/1.75 "Microsoft YaHei",sans-serif}main{max-width:1500px;margin:auto;padding:32px}h1{font-size:29px}h2{margin-top:32px;font-size:21px}.sub{color:#607387}.callout{background:white;border-left:5px solid #bf7919;padding:20px}.scroll{overflow:auto;max-height:540px;background:white}table{width:100%;border-collapse:collapse;white-space:nowrap;font-size:13px}td,th{padding:9px;text-align:right;border-bottom:1px solid #dfe6ef}th{position:sticky;top:0;background:#e7eef5}td:first-child,th:first-child{text-align:left}details{padding:12px 0}summary{cursor:pointer;font-weight:bold}img{width:100%;margin:24px 0}</style><main>'+''.join(sections)+'</main></html>'
    target=OUT/'黄金期权双方向与期货定价研究.html';target.write_text(page,encoding='utf8')
    pack(OUT/'全部120账户与归因.json.gz',dict(accounts=rows,portfolios=ports,attribution=attribution,leadlag=lag))
    write(OUT/'overview.json',dict(performance=performance,delays=delayrows,concentration=concentration,
        portfolios={p:{s:{k:v for k,v in a.items() if k!='curve'} for s,a in ps.items()} for p,ps in ports.items()}))
    sources[str(Path(__file__).relative_to(ROOT))]=digest(Path(__file__))
    write(OUT/'delivery_source_manifest.json',sources)
    write(OUT/'report_qa.json',dict(accounts=len(rows),boundaries=sum(x['boundaries'] for x in qa),
        arithmetic_checks=True,browser_screenshot_qa=False,all_model_failures_retained=True))
    write(OUT/'artifact_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='artifact_manifest.json'})
    print(target)
    print('ZERO_DELAY_TABLE',performance)


if __name__=='__main__':main()
