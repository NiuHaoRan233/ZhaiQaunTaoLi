"""Final fixed two-option development report and reproducible research strategy0.1."""
from pathlib import Path
from datetime import datetime
import base64
import html
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from zhaiquant import gold_direction_research as g
from probe_gold_direction import CODES,OUT as OLD
from probe_gold_aligned_value import OUT as V4
from probe_gold_fast_validation import OUT as V5
from run_gold_intraday_value import OUT as RUN,CONFIG
from report_gold_direction import portfolio
from report_gold_breakflat import table,clock,TZ
from probe_commodity_capital import ROOT,WORK,read,write,pack,unpack,digest

OUT=WORK/'reports/gold_aligned_report_20260913'
NAMES=dict(aligned_long='同步中价IV',aligned_bid_band='同步买一IV下界',aligned_fast_lower='快慢IV取低（主方案）',
    aligned_fixed_take='固定初始卖出目标',aligned_fair_take='参考价限制卖出目标',aligned_fast_fair_take='快慢IV低值＋参考价卖出')


def main():
    OUT.mkdir(exist_ok=True);sources={};allrows={}
    for tag,folder in [('v4',V4),('v5',V5),('run',RUN)]:
        for p,h in read(folder/('result_manifest.json' if tag!='run' else 'artifact_manifest.json')).items():assert digest(ROOT/p)==h;sources[p]=h
        for key in read(folder/'results.json'):allrows[tag+'_'+key]=unpack(folder/f'{key}.json.gz')
    assert read(V4/'verification.json')['status']==read(V5/'verification.json')['status']==read(RUN/'reproduction_verification.json')['status']=='passed'
    get=lambda c,s,fast=10,delay=0,through=False:allrows[f'v5_{c}_fast{fast}_through{int(through)}_{s}_d{delay}']
    v4=lambda c,p,s:allrows[f'v4_{c}_{p}_{s}_d0']
    ps={s:portfolio([get(c,s) for c in CODES]) for s in ('cost','market')}
    first=[]
    for c in CODES:
        a,b=get(c,'cost')['summary'],get(c,'market')['summary']
        first.append([c,a['pnl_cny'],b['pnl_cny'],b['realized_gross_cny'],b['fees_cny'],str(b['complete_cycles']),
            str(a['virtual_close_count']),a['removed_tail_gross_cny'],b['max_drawdown_cny']])
    compare=[]
    for p,label in NAMES.items():
        cs=[v4(c,p,'cost')['summary'] for c in CODES];ms=[v4(c,p,'market')['summary'] for c in CODES]
        compare.append([label,cs[0]['pnl_cny'],cs[1]['pnl_cny'],ms[0]['pnl_cny'],ms[1]['pnl_cny'],sum(x['pnl_cny'] for x in ms)])
    sensitivity=[]
    for fast in (5,10,20):
        for delay in (0,500,1000):
            ss=[get(c,'market',fast,delay)['summary'] for c in CODES]
            sensitivity.append([str(fast),str(delay),'末价触及可用',ss[0]['pnl_cny'],ss[1]['pnl_cny'],sum(x['pnl_cny'] for x in ss)])
    strict=[get(c,'market',through=True)['summary'] for c in CODES]
    sensitivity.append(['10','0','要求严格穿价',strict[0]['pnl_cny'],strict[1]['pnl_cny'],sum(x['pnl_cny'] for x in strict)])
    assert all(line[i]>0 for line in sensitivity for i in (3,4))
    qa4=read(V4/'verification.json');pairs=qa4['aligned_iv_invariant_to_availability_delay']
    pairrows=[[x['code'],str(x['delay_ms']),str(x['identical_iv_pairs']),'配对原IV/快慢IV完全一致，仅可用时间后移'] for x in pairs]
    audit_dir=WORK/'reports/gold_aligned_audit_20260913';exit_audit=read(audit_dir/'exit_target_audit.json');changes=read(audit_dir/'fast_reference_path_changes.json')
    change_rows=[[c,str(x['common_cycles']),str(x['removed_cycles']),x['removed_net_cny'],str(x['added_cycles']),x['added_net_cny'],x['pnl_change_cny']] for c,x in changes.items()]
    concentration=[];sessions=[];boundaries=[];cycles_html=[]
    for c in CODES:
        r=get(c,'market');nets=sorted([x['net_cents']/100 for x in r['cycles']],reverse=True)
        concentration.append([c,str(len(nets)),str(sum(x>0 for x in nets)),str(sum(x<0 for x in nets)),sum(nets),sum(nets)-sum(nets[:3]),max(nets),min(nets)])
        for s in ('cost','market'):
            r=get(c,s)
            for i,(lo,hi) in enumerate(zip(g.SESSION_STARTS,g.BOUNDARIES)):
                cs=[x for x in r['cycles'] if lo<=x['entry_ts']<hi]
                sessions.append([c,s,str(i+1),str(len(cs)),sum(x['net_cents'] for x in cs)/100])
            for b in r['boundaries']:boundaries.append([c,s,clock(b['ts']),str(b['inventory']),'无'])
            cr=[[str(i+1),clock(x['entry_ts']),clock(x['exit_ts']),x['entry_price_cents']/100000,x['exit_price_cents']/100000,
                x['net_cents']/100,x['duration_seconds'],x['exit_kind']] for i,x in enumerate(r['cycles'])]
            cycles_html.append('<details><summary>'+html.escape(c+' / '+s)+f"：{len(cr)}笔</summary>"+
                table(['序号','买入','卖出','买入报价','卖出报价','净值/元','持仓秒','退出类型'],cr)+'</details>')
    plt.rcParams['font.family']='Microsoft YaHei';plt.rcParams['axes.unicode_minus']=False
    fig,axs=plt.subplots(3,1,figsize=(13,11),sharex=True,gridspec_kw={'height_ratios':[2,2,1]})
    oldps=portfolio([unpack(OLD/f'{c}_fair_long_market.json.gz') for c in CODES])
    for name,a,color,style in [('旧定价多头 · 实际计损',oldps,'#929ba5','-'),('0.1 · 实际计损',ps['market'],'#176bae','-'),('0.1 · 成本虚拟结算',ps['cost'],'#176bae','--')]:
        xs=[datetime.fromtimestamp(x[0]/1000,TZ) for x in a['curve']]
        axs[0].step(xs,[x[1]/(2*g.CAPITAL)*100 for x in a['curve']],where='post',label=name,color=color,ls=style)
    axs[0].set_title('黄金期权日内估值做市0.1 · 2026-09-11开发日\n固定10秒/60秒IV取低 · 每份25万元 · 单边1.70元 · 额外下单/期货信号延迟0')
    axs[0].set_ylabel('组合累计收益率 / %')
    for c,color in zip(CODES,['#176bae','#bb7523']):
        a=get(c,'market')['curve'];xs=[datetime.fromtimestamp(x[0]/1000,TZ) for x in a]
        axs[1].step(xs,[x[1]/100 for x in a],where='post',label=c,color=color)
        axs[2].step(xs,[x[2] for x in a],where='post',label=c,color=color,alpha=.7)
    axs[1].set_ylabel('实际计损净盈亏 / 元');axs[2].set_ylabel('实际计损库存 / 手');axs[2].set_yticks([0,1]);axs[2].set_ylim(-.1,1.3)
    for ax in axs:
        ax.axhline(0,color='#666',lw=.5);ax.grid(alpha=.2);ax.legend(fontsize=9,ncol=2,loc='lower left')
        for lo,hi in zip(g.BOUNDARIES[:2],g.SESSION_STARTS[1:]):ax.axvspan(datetime.fromtimestamp(lo/1000,TZ),datetime.fromtimestamp(hi/1000,TZ),color='#ddd',alpha=.3)
    axs[-1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M',tz=TZ));axs[-1].set_xlabel('北京时间；灰区为休市，库存为0。成本虚拟结算为研究假设，不是交易所成交。')
    fig.tight_layout();chart=OUT/'黄金日内估值0.1收益率与库存.png';fig.savefig(chart,dpi=150);plt.close(fig)
    complete_rows=[]
    for key,r in allrows.items():
        s=r['summary'];complete_rows.append([key,s['pnl_cny'],s['fees_cny'],str(s['complete_cycles']),str(s['virtual_close_count']),s['removed_tail_gross_cny'],s['max_drawdown_cny']])
    sections=[
        '<h1>黄金期权日内估值做市0.1</h1><p class="sub">固定au2610C960 / au2610C952 · 最新一个已结束日盘2026-09-11 · 可复现的离线研究策略</p>',
        f'<div class="callout"><b>本轮限定的单日双标的研究形成了可复现模式。</b><p>固定10/60秒快慢IV低值参考、先买后卖、有限等待退出。成本虚拟结算合计<b>{ps["cost"]["pnl_cny"]:,.2f}元</b>；实际计损对照<b>{ps["market"]["pnl_cny"]:,.2f}元</b>，合计50万元收益率{ps["market"]["return_pct"]:.4f}%，盘中最大回撤{ps["market"]["max_drawdown_cny"]:,.2f}元。主参数保持最初的10/60秒，未改选测试中最赚钱的参数。</p><p>两合约在3种快周期×3种期货信号延迟及严格穿价对照的20个实际计损合约情景均为正。这支持当前开发样本中的机制可行性，不是对未来交易收益的保证；新日期、真实到达顺序与队列仍需独立前向验证。</p></div>',
        '<h2>主方案日度结果</h2>'+table(['合约','成本研究净值/元','实际计损净值/元','实际毛值/元','实际费用/元','实际闭环数','成本虚拟次数','成本归零前浮盈亏/元','实际最大回撤/元'],first),
        '<p>每合约25万元独立现金、一手上限，每种情景合计50万元；不是把所有测试账户相加。成本主口径在10:15/11:30/15:00将余仓按入场价虚拟退出、毛盈亏0、仍收双侧各1.70元，正常亏损保留。实际对照提前5秒停买，用当时有效买一卖出；边界无库存。成本尾仓原浮亏总920元，而两套结果差100元，因退出时点/价格和潜在后续路径不同，不能简单相减当同路径盈亏。</p>',
        '<img alt="0.1收益率、两合约盈亏与休市库存曲线" src="data:image/png;base64,'+base64.b64encode(chart.read_bytes()).decode()+'">',
        '<h2>为什么这次改善</h2><p>旧模型将刚到的期权报价配上当时已知的期货价；延迟变化会混入IV配对误差。新校准等待期货原行情时间覆盖期权后，才使用原时间不晚于期权的价格配对，并将校准可用时间记在真正收到数据之后。快慢IV取低则在短期IV已经下降时，避免较慢参考仍偏高而误判便宜。</p>',
        table(['合约','与同步中价版相同闭环','被取消/替换旧闭环','旧部分净值/元','新增闭环','新增部分净值/元','总改善/元'],change_rows),
        '<p>C952保留35笔完全相同交易，去掉/替换12笔旧闭环净−540.80元，同时新增1笔亏−63.40元，净改善477.40元；并没有把新增亏损藏掉。两端优势仍可能被逆向行情吞没，因此限定持仓与休市清零仍保留。</p>',
        '<h2>退出假说的反证与六种对照</h2><p>核查上一轮定价多头全部亏损交易，两合约均未出现“初始获利目标已具备严格成交证据却被错过”的案例。固定或压低卖出目标没有解决这些亏损，主要减少盈利交易的收益；故0.1保留原有限等待出口，不采用改低目标的版本。</p>'+table(['对照','C960成本/元','C952成本/元','C960实际/元','C952实际/元','实际合计/元'],compare),
        '<h2>邻近参数与执行证据验证</h2>'+table(['快IV时间常数/秒','期货额外信号延迟/ms','被动撮合证据','C960实际/元','C952实际/元','合计/元'],sensitivity),
        '<p>慢IV固定60秒，主快IV固定10秒。0.5/1秒只改变期货信号可用时刻，原始市场时间不变；不是改用户下单延迟。严格穿价要求末价越过限价，碰到限价不计成交；它是更严格的历史撮合对照，不能冒称已模拟真实队列。两种结算、全部亏损和参数结果都保留。</p>',
        '<h2>校准不再被人为信号延迟改变</h2>'+table(['合约','可用延迟/ms','共同校准样本','独立核验'],pairrows),
        '<p>配对价格、原始IV、10秒/60秒平滑IV与买一IV在共同样本逐字段一致；校准的可用时刻按追加延迟后移。该核验解决配对误差，不消除真正的报价陈旧风险；任何交易都只能使用当时已知的校准和期货报价。</p>',
        '<h2>仍保留的集中度和风险</h2>'+table(['合约','实际闭环','盈利笔数','亏损笔数','净值/元','去3最大盈利后/元','最大盈利/元','最大亏损/元'],concentration),
        '<p>C952去除最大三笔盈利仍为负，收益比C960集中。仅一个已经用于开发的日期，不称样本外、不年化；不据20个相关情景均正给统计置信保证。Black-76欧式近似用于美式黄金期权的相对参考，模型仍有估值误差。这里只完成当前限定样本的研究策略交付，实盘适用性需要新的独立证据。</p>',
        '<h2>完整可执行规则与复现</h2><ol><li>09:00—09:30观察，此后每小节先预热至少60秒，盘口/期货源不完整时不新开仓。</li><li>源时间配对校准IV，分别10秒/60秒EWMA取低；以同月au2610期货和剩余期限重估。期货报价源年龄≤2秒。</li><li>买价=买一＋0.02；扣双侧改善及3.40元费用后的原盘口空间至少一跳20元；最近300秒有严格推断双向成交，有效买二且一二档断层小于max(两跳,当前价差)。</li><li>参考价−买限价−双费≥max(一跳,盘口价差四分之一)才挂买。只做多，每合约一手。</li><li>买入后挂卖一−一跳；在前300秒且没有持续30秒逆向超过入场价差时，保留覆盖双费和一跳收益的成本底价；达到时间或风险释放条件后取消底价，继续被动跟档。旧单先撮合，新信息不能回溯擦掉成交。</li><li>三个休市边界主成本虚拟结算，独立提前5秒实际价格对照；无有效买一的实际清仓失败会报错。</li></ol><p>双击广义套利目录的“复现黄金期权日内策略0.1.cmd”，或从仓库根目录运行<code>.\\.venv\\Scripts\\python.exe -X utf8 scripts\\run_gold_intraday_value.py</code>。配置在<code>广义套利/gold_intraday_strategy/strategy.json</code>，改变参数需另立模型。该入口只重放已冻结09-11输入，没有网络、券商订单或实时模拟盘接入。</p>',
        '<h2>12个主方案空仓边界</h2>'+table(['合约','结算','边界时间','库存/手','未成交委托'],boundaries),
        '<h2>分小节收益</h2>'+table(['合约','结算','小节','闭环数','净收益/元'],sessions),
        '<h2>主方案全部逐笔</h2>'+''.join(cycles_html),
        '<h2>全部88份新研究与复现账本摘要</h2>'+table(['账户','净值/元','费用/元','闭环','虚拟次数','虚拟前浮盈亏/元','最大回撤/元'],complete_rows),
        '<h2>验收</h2><p>v4同步/退出40账户、v5稳健性40账户及交付程序8份复现账本，全部资金/费用/库存重建通过，264边界空仓。v4/v5各自14:00截断重跑订单、成交、撤单、闭环、校准前缀一致。交付8份账本与冻结主/严格成交版本全字段相同；40项黄金测试及98项既有商品测试通过。原v1—v3全部结果保留，未改实时矩阵。</p><p class="sub">源码、配置、原始输入、全部账本和报告哈希已保存；HTML静态表金额逐格核验，PNG目视检查。没有浏览器截图验收，也没有实盘成交验证。先卖后买、自动择向和趋势/主动撤单探索的结果在上一轮完整报告中保留。</p>'
    ]
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>黄金期权日内估值做市0.1报告</title><style>body{margin:0;background:#f2f5f9;color:#193047;font:15px/1.75 "Microsoft YaHei",sans-serif}main{max-width:1500px;margin:auto;padding:32px}h1{font-size:29px}h2{margin-top:32px;font-size:21px}.sub{color:#607387}.callout{background:white;border-left:5px solid #176bae;padding:20px}.scroll{overflow:auto;max-height:540px;background:white}table{width:100%;border-collapse:collapse;white-space:nowrap;font-size:13px}td,th{padding:9px;text-align:right;border-bottom:1px solid #dfe6ef}th{position:sticky;top:0;background:#e7eef5}td:first-child,th:first-child{text-align:left}details{padding:12px 0}summary{cursor:pointer;font-weight:bold}img{width:100%;margin:24px 0}</style><main>'+''.join(sections)+'</main></html>'
    target=OUT/'黄金期权日内估值做市0.1报告.html';target.write_text(page,encoding='utf8')
    pack(OUT/'全部88账本与研究证据.json.gz',dict(accounts=allrows,portfolios=ps,exit_audit=exit_audit,changes=changes,calibration_verification=pairs))
    write(OUT/'overview.json',dict(main=first,comparisons=compare,robustness=sensitivity,concentration=concentration,
        portfolios={s:{k:v for k,v in p.items() if k!='curve'} for s,p in ps.items()},positive_contract_robustness_cases=20))
    for p in (Path(__file__),CONFIG,ROOT/'scripts/run_gold_intraday_value.py'):sources[str(p.relative_to(ROOT))]=digest(p)
    write(OUT/'delivery_source_manifest.json',sources)
    write(OUT/'report_qa.json',dict(accounts=len(allrows),boundaries=len(allrows)*3,all_economic_sums_checked=True,
        browser_screenshot_qa=False,scope='latest single development day; no live profitability claim'))
    write(OUT/'artifact_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='artifact_manifest.json'})
    print(target);print({s:{k:v for k,v in p.items() if k!='curve'} for s,p in ps.items()})


if __name__=='__main__':main()
