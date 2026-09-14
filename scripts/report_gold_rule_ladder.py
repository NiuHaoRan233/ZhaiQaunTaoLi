"""Detailed static report: every added rule, reverse removals, capital and trades."""
from pathlib import Path
from datetime import datetime
import base64
import html
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from zhaiquant import gold_rule_ladder_research as engine
from zhaiquant import gold_rule_ladder_simplification as simple
from probe_gold_rule_ladder import OUT as V1,CODES
from probe_gold_rule_simplification import OUT as V2
from audit_gold_rule_ladder import OUT as AUDIT
from probe_commodity_capital import ROOT,WORK,read,write,unpack,pack,digest
from report_gold_breakflat import table,clock,TZ

OUT=WORK/'reports/gold_rule_ladder_report_20260913'
TITLE='黄金期权：资金核对与逐条规则拆解'


def main():
    OUT.mkdir(exist_ok=True)
    for folder,name in [(V1,'result_manifest.json'),(V2,'result_manifest.json'),(AUDIT,'manifest.json')]:
        for p,h in read(folder/name).items():assert digest(ROOT/p)==h
    summaries={'v1':read(V1/'results.json'),'v2':read(V2/'results.json')}
    ports={'v1':read(V1/'portfolios.json'),'v2':read(V2/'portfolios.json')}
    funds=read(V1/'capital_validation.json');audit=read(AUDIT/'verification.json')
    deltas=read(AUDIT/'step_trade_deltas.json');alltrades={}
    def key(code,case,d,through=0):return f'{code}_{case}_{"long" if d==1 else "short"}_through{through}'
    def s(tag,code,case,d=1,t=0):return summaries[tag][key(code,case,d,t)]
    def p(tag,case,d=1,t=0):return ports[tag][f'{case}_{d}_{t}']
    sections=[];exports={};fundrows=[]
    for code in CODES:
        x=s('v1',code,'s10');v=funds[code+'_exact_min'];test=funds[code+'_twenty_thousand']
        fundrows.append([code,x['peak_single_entry_premium_cny'],x['max_reserve_cny'],
            x['minimum_cash_for_fills_only_cny'],v['capital_cny'],test['capital_cny'],x['pnl_cny'],
            x['pnl_cny']/20000*100,'全部订单/成交/曲线相同'])
    sections.append('<h2>1．50万元是旧预设余额，不是买方所需本金</h2>'+table(
        ['合约','单次最高实付权利金/元','最高买单冻结需求/元','仅重建已成交现金下界/元','保持全部报价的初始资金/元','实跑资金/元','净收益/元','按2万元收益率/%','核验'],fundrows))
    exports['capital']=fundrows
    sections.append(f'<p>旧完整版实际最高同时持仓的已付权利金为<b>{audit["original_full_peak_simultaneously_held_premium_cny"]:,.2f}元</b>，按相同毫秒两账户成交汇总后计算，不含未成交买单冻结。两账户各自保持所有报价所需初始资金合计<b>28,304.00元</b>；这个下界利用了当日已实现利润的循环使用，是事后资金核算，不是未来每天的充分本金。</p>'
        '<p>每份2万元、合计4万元已实际重跑：原完整0.1净收益1,257.80元，收益率3.1445%，最大回撤828.50元，回撤/初始资金2.07125%。利润没有增加，只是改用经验证足够的资金分母。资金再少0.01元会改变部分未成交挂单，但本日已成交与净收益未变，不能把“全部报价下界”误叫唯一盈利门槛。</p>'
        '<p>买入期权支付的是权利金：报价×1000，例如13.32元/克对应一手13,320元。卖出开仓不同，需要保证金；本研究继续用“20%期货名义金额＋回购权利金”的旧风险准备金假设，只用于一致比较，未取得实际券商保证金标准，不能将空头研究占资当成真实交易门槛。</p>')
    descriptions=[
        'S00只做原买一买入、原卖一卖出；空头顺序反过来。没有价差/成交方向/第二档/估值/成本保护筛选。',
        'S01只改报价：当价差大于一跳，买价提高一跳、卖价降低一跳；一跳盘口不跨价。这样更容易排到前面，也直接消耗两跳空间。',
        'S02加净价差门槛：原价差−两侧报价改善−3.40元≥20元。这两个合约等价于原价差至少4跳，即报价差≥0.08元。',
        'S03加过去300秒严格推断双向成交；推断来自Level1快照，非交易所逐笔方向标记。',
        'S04只要求买入侧存在有效买二，空头要求卖二；S05再加一二档距离小于max(两跳,原价差)。',
        'S06加成本保护：多头卖价不低于开仓成本＋双费＋一跳收益，空头反向；300秒到期解除保护，继续被动退出，不是300秒强制止损。',
        'S07只增加提前解除保护：中价相对入场不利超过当时价差且持续30秒；它可能止住损失，也可能提前实现亏损。',
        'S08先单独增加估值数据可用性：每小节预热60秒、至少3次校准、期货源年龄≤2秒、校准年龄≤60秒；此时仍不按估值大小过滤。',
        'S09再要求60秒IV参考价给出扣费优势≥max(一跳,盘口价差四分之一)。用已收到的同月期货重新定价；期权/期货按源时间匹配，绝不回填旧交易。',
        'S10只改变波动率参考：多头用10/60秒IV较低值，空头用较高值以保守卖出。多头这一行完整复现旧0.1的订单、成交、撤单与盈亏曲线。']
    sections.append('<h2>2．固定比较规则与逐步增加的条件</h2><p>固定2026-09-11、同两份合约、09:30起交易、每份最多一手、双侧各1.70元、0额外延迟。10:15/11:30/15:00前5秒停开并按对手价清余仓；无报价则失败，不成本归零。所有行都具备有效盘口/现金约束，不把这些基本执行条件当作赚钱过滤器。</p><ol>'+''.join('<li>'+x+'</li>' for x in descriptions)+'</ol>')
    ladder_tables={}
    for d,name in [(1,'先买后卖'),(-1,'先卖后买')]:
        rows=[];last=None
        for case,label,_ in engine.STEPS:
            q=p('v1',case,d);strict=p('v1',case,d,1)
            rows.append([case.upper()+' '+label,s('v1',CODES[0],case,d)['pnl_cny'],s('v1',CODES[1],case,d)['pnl_cny'],
                q['pnl_cny'],q['pnl_cny']-last['pnl_cny'] if last else '—',q['max_drawdown_cny'],
                q['max_drawdown_cny']-last['max_drawdown_cny'] if last else '—',str(q['cycles']),q['fees_cny'],strict['pnl_cny'],strict['max_drawdown_cny']])
            last=q
        ladder_tables[str(d)]=rows
        sections.append(f'<h2>3{ "A" if d==1 else "B" }．{name}：每加一条的收益与风险</h2>'+table(
            ['累积规则','C960净收益','C952净收益','组合净收益','较上步收益变化','组合最大回撤','较上步回撤变化','闭环次数','手续费','严格穿价净收益','严格穿价回撤'],rows))
    exports['ladder']=ladder_tables
    sections.append('<p>金额单位均为人民币元；回撤增量为正表示风险增大。上述每步都重新运行完整交易路径，不能把少成交的笔数直接当成“过滤掉的亏损”。这些增量依赖添加顺序，下面用反向删除检验。</p>')
    removals=[]
    for case,cfg in engine.cases().items():
        if cfg['group']!='removal':continue
        q=p('v1',case);baseline=p('v1','s10');qs=p('v1',case,-1)
        removals.append([cfg['label'],s('v1',CODES[0],case)['pnl_cny'],s('v1',CODES[1],case)['pnl_cny'],q['pnl_cny'],
            q['pnl_cny']-baseline['pnl_cny'],q['max_drawdown_cny'],q['max_drawdown_cny']-baseline['max_drawdown_cny'],str(q['cycles']),qs['pnl_cny']])
    exports['removals']=removals
    sections.append('<h2>4．从完整版单独删掉一个条件</h2>'+table(['删除条件','多头C960','多头C952','多头合计','相对完整版收益变化','多头回撤','回撤变化','多头闭环','空头合计'],removals))
    simple_rows=[]
    for case in ('simple_patient','simple_timeout','core','core_no_adverse'):
        q=p('v2',case);z=p('v2',case,1,1)
        simple_rows.append([simple.CASES[case]['label'],s('v2',CODES[0],case)['pnl_cny'],s('v2',CODES[1],case)['pnl_cny'],
            q['pnl_cny'],q['max_drawdown_cny'],str(q['cycles']),z['pnl_cny'],z['max_drawdown_cny'],p('v2',case,-1)['pnl_cny']])
    exports['simplification']=simple_rows
    sections.append('<h2>5．联合删除，实际验证精简版</h2>'+table(['模式','多头C960','多头C952','多头合计','多头回撤','闭环','严格穿价收益','严格穿价回撤','空头合计'],simple_rows)+
        '<p>精简版core同时删掉双向成交、第二档和断层过滤，保留“价差＋保守估值＋有限成本保护”。它是新研究身份，未覆盖原0.1。单项删除无影响不代表以后无用；联合删除的收益也必须实跑，不能简单把各项增量相加。</p>')
    spread_rows=[]
    for n in (3,4,5,6,8,10):
        c=f'spread{n}';q=p('v2',c);a=p('v2',c,1,1);z=p('v2',c,-1);b=p('v2',c,-1,1)
        spread_rows.append([str(n),f'{n*.02:.2f}',n*20-40-3.4,s('v2',CODES[0],c)['pnl_cny'],s('v2',CODES[1],c)['pnl_cny'],
            q['pnl_cny'],q['max_drawdown_cny'],str(q['cycles']),a['pnl_cny'],z['pnl_cny'],z['max_drawdown_cny'],b['pnl_cny']])
    exports['spreads']=spread_rows
    sections.append('<h2>6．只用一个价差门槛，能不能做？</h2>'+table(['至少几跳','报价差至少/元','静态扣费空间/手','多头C960','多头C952','多头合计','多头回撤','多头次数','严格多头','空头合计','空头回撤','严格空头'],spread_rows)+
        '<p>此表只有报价改善＋价差门槛，没有估值、双向流量、第二档或成本保护。8跳是本次展示后观察到的候选，不是事前证明的最优阈值。全部3/4/5/6/8/10跳与两方向都保留，不能只展示8跳赢家；邻近阈值和两个合约的表现不同。</p>')
    finalists=[]
    for tag,c,label in [('v1','s00','原买一卖一极简'),('v2','spread8','只加8跳价差门槛'),('v1','s10','旧完整0.1'),('v2','core','精简估值版')]:
        q=p(tag,c);a=p(tag,c,1,1);ss=[s(tag,code,c) for code in CODES]
        finalists.append([label,q['pnl_cny'],q['pnl_cny']/40000*100,q['max_drawdown_cny'],str(q['cycles']),a['pnl_cny'],a['max_drawdown_cny'],
            ss[0]['profit_without_best3_cny'],ss[1]['profit_without_best3_cny']])
    exports['finalists']=finalists
    sections.append('<h2>7．四个重点多头版本：均已验证每份2万元不改交易路径</h2>'+table(
        ['模式','净收益/元','4万元收益率/%','最大回撤/元','闭环','严格穿价净收益','严格回撤','C960去前三大盈利','C952去前三大盈利'],finalists)+
        '<p>原买一卖一收益依赖触价成交假设，价格严格穿过时结果明显改变。严格穿价仍不是排队模型，成交概率、真实撤改单延迟都未知。当前更值得并列观察的是“8跳极简”和“精简估值”；前者条件少但交易少且阈值敏感，后者保留定价和退出逻辑。没有根据这一个开发日自动晋级任何实时账户。</p>')
    plt.rcParams['font.family']='Microsoft YaHei';plt.rcParams['axes.unicode_minus']=False
    fig,axs=plt.subplots(2,1,figsize=(14,9),sharex=True)
    xs=np.arange(11)
    for d,label,color in [(1,'先买后卖','#187b84'),(-1,'先卖后买','#bd7047')]:
        axs[0].plot(xs,[p('v1',f's{i:02d}',d)['pnl_cny'] for i in xs],marker='o',label=label,color=color)
        axs[1].plot(xs,[p('v1',f's{i:02d}',d)['max_drawdown_cny'] for i in xs],marker='o',label=label,color=color)
    axs[0].axhline(0,color='#777',lw=.6);axs[0].set_ylabel('扣费净收益 / 元');axs[1].set_ylabel('组合最大回撤 / 元')
    axs[1].set_xticks(xs,[f'S{i:02d}' for i in xs]);axs[1].set_xlabel('累积步骤；精确新增规则见表格，资金预设不参与收益金额比较')
    for ax in axs:ax.grid(alpha=.15);ax.legend()
    fig.suptitle('黄金期权逐条加规则：增加复杂度并不保证改善\n2026-09-11 · 同合约/费用/休市清仓/基础成交口径',fontsize=16)
    fig.tight_layout();chart1=OUT/'逐条规则收益与回撤.png';fig.savefig(chart1,dpi=140);plt.close(fig)
    fig,axs=plt.subplots(2,1,figsize=(14,9),sharex=True)
    for tag,c,label,color,style in [('v1','s00','原档极简','#999999','-'),('v1','s00','原档极简·严格穿价','#999999','--'),
        ('v2','spread8','8跳极简','#c67e25','-'),('v1','s10','完整0.1','#6b65a1','-'),('v2','core','精简估值','#177d87','-')]:
        a=np.asarray(p(tag,c,1,int(style=='--'))['curve']);times=[datetime.fromtimestamp(t/1000,TZ) for t in a[:,0]]
        axs[0].step(times,a[:,1]/100,where='post',label=label,color=color,ls=style)
        if c!='s00':axs[1].step(times,(np.maximum.accumulate(np.maximum(a[:,1],0))-a[:,1])/100,where='post',label=label,color=color)
    for ax in axs:ax.grid(alpha=.15);ax.legend(ncol=2)
    axs[0].set_ylabel('累计扣费净收益 / 元');axs[1].set_ylabel('距此前高点回撤 / 元');axs[1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M',tz=TZ))
    fig.suptitle('简单与复杂：比较收益路径，也检查成交假设\n三个休市边界全部空仓；仅一个开发日',fontsize=16)
    fig.tight_layout();chart2=OUT/'简单与估值策略收益曲线.png';fig.savefig(chart2,dpi=140);plt.close(fig)
    sections.append('<h2>8．收益路径与风险图</h2>'+''.join('<img alt="'+x.stem+'" src="data:image/png;base64,'+base64.b64encode(x.read_bytes()).decode()+'">' for x in (chart1,chart2)))
    detailrows=[];deltarows=[]
    for tag,folder in [('v1',V1),('v2',V2)]:
        for k,x in summaries[tag].items():
            r=unpack(folder/f'{k}.json.gz')
            detailrows.append([tag+' '+k,x['pnl_cny'],x['realized_gross_cny'],x['fees_cny'],str(x['complete_cycles']),x['max_drawdown_cny'],
                x['worst_cycle_cny'],x['best_cycle_cny'],x['win_rate_pct'],x['median_hold_seconds'],x['max_hold_seconds'],
                x['peak_single_entry_premium_cny'],x['minimum_cash_for_all_entry_quotes_cny'],x['profit_without_best3_cny'],str(x['active_close_count'])])
            # Preserve all cycles in a compact downloadable file, including losses.
            alltrades[tag+'_'+k]=r['cycles']
    for k,v in deltas.items():deltarows.append([k,str(v['same_cycles']),str(v['removed_cycles']),v['removed_net_cny'],str(v['added_cycles']),v['added_net_cny'],v['delta_pnl_cny'],v['delta_drawdown_cny']])
    exports['all_accounts']=detailrows;exports['trade_changes']=deltarows
    sections.append('<h2>9．每个合约、方向、成交假设的全部明细</h2><details><summary>展开224份账户：收益、风险、占资、持仓时长</summary>'+table(
        ['账户','净收益','毛收益','手续费','闭环','最大回撤','最差一笔','最好一笔','胜率/%','持仓中位/秒','最长持仓/秒','最高单笔权利金','报价资金下界/空头为研究代理','去前三大盈利','主动平仓次数'],detailrows)+'</details>'+
        '<details><summary>展开80组相邻步骤的交易路径变化</summary>'+table(['账户/新增步骤','完全相同闭环','旧路径独有笔数','旧独有净收益','新路径独有笔数','新独有净收益','净收益变化','回撤变化'],deltarows)+'</details>')
    for tag,c,label in [('v1','s00','原档极简'),('v2','spread8','8跳极简'),('v1','s10','完整0.1'),('v2','core','精简估值')]:
        for code in CODES:
            r=alltrades[tag+'_'+key(code,c,1)]
            sections.append('<details><summary>'+label+' '+code+'：全部'+str(len(r))+'笔买卖</summary>'+table(
                ['买入时间','买价/报价元','卖出时间','卖价/报价元','毛收益/元','手续费/元','净收益/元','持仓/秒','退出方式'],
                [[clock(x['entry_ts']),f"{x['entry_price_cents']/100000:.2f}",clock(x['exit_ts']),f"{x['exit_price_cents']/100000:.2f}",
                  x['gross_cents']/100,x['fees_cents']/100,x['net_cents']/100,x['duration_seconds'],x['exit_kind']] for x in r])+'</details>')
    sections.append('<h2>10．证据与范围</h2><p>144份逐条/删除/价差账户＋80份联合简化/严格价差账户，共224份；其中24份用于跨版本价差基线复现，不能称224个独立新策略。另有18份小资金复现，合计726个空仓边界。逐笔资金、双费、库存、最大回撤重建以及80组增量归因通过；全部研究账户14:00截断重放的订单/成交/撤单/闭环前缀一致，完整版多头完整复现旧0.1经济路径。</p>'
        '<p>所有研究只用一个已经用于开发的日期，多个阈值和条件被反复比较，没有样本外置信度。旧队列未知，Black-76欧式近似美式黄金期权；空头风险准备金不是券商保证金。规则变少不意味着风险消失。</p>'
        '<p>同目录保存overview.json、全部交易明细.json.gz及report_qa.json；原始订单/撤单/资金需求账本在../gold_rule_ladder_20260913_v1和../gold_rule_simplification_20260913_v2。源码与输入SHA256均冻结。静态HTML无需JavaScript，未做浏览器截图验收。</p>')
    overview=dict(tables=exports,scope='2026-09-11 only',accounts=224,capital_replays=18,boundaries=726,
        full_40000_return_pct=1257.8/40000*100,core_40000_return_pct=p('v2','core')['pnl_cny']/40000*100,
        full_minimum_independent_initial_cash_cny=28304,peak_simultaneous_paid_premium_cny=audit['original_full_peak_simultaneously_held_premium_cny'])
    write(OUT/'overview.json',overview);pack(OUT/'全部交易明细.json.gz',alltrades)
    css='body{font-family:"Microsoft YaHei",sans-serif;background:#f4f6f6;color:#243237;max-width:1600px;margin:auto;padding:32px}h1{font-size:32px}h2{margin-top:40px;color:#126d76}p,li{line-height:1.8}table{border-collapse:collapse;width:100%;background:white;font-size:13px}th,td{padding:9px 12px;border-bottom:1px solid #dbe2e3;text-align:right;white-space:nowrap}th{background:#e5eeee;position:sticky;top:0}th:first-child,td:first-child{text-align:left}.scroll{overflow:auto;max-height:650px}img{width:100%;background:white;margin:15px 0}details{background:#fff;padding:15px;margin:12px 0}summary{cursor:pointer;font-weight:bold}a{color:#126d76}'
    document=('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>'+TITLE+'</title><style>'+css+'</style><body><h1>'+TITLE+'</h1><p>2026-09-11日盘开发样本 · C960/C952 · 实际对手价清仓 · 2026-09-13研究交付</p>'+
        '<p><b>先纠正资金：买方每份2万元、合计4万元已重跑通过。再拆规则：从原档循环到完整版，逐条展示增益与退化，并保留真正只靠价差的简单候选。</b></p>'+''.join(sections)+'</body></html>')
    path=OUT/(TITLE.replace('：','_')+'.html');path.write_text(document,encoding='utf-8')
    write(OUT/'artifact_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name not in ('artifact_manifest.json','report_qa.json')})
    print(path)
    print(json.dumps(dict(finalists=finalists,capital=fundrows),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
