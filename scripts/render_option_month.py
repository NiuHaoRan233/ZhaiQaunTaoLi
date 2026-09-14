"""Build multi-day tables and an offline HTML date/contract replay explorer."""
import argparse
import json
from pathlib import Path

from probe_option_top_cycle import NAMES
from render_option_guard import MODE_LABELS
from zhaiquant.option_top_cycle_research import MODES


def aggregate(rows):
    if not rows:return dict(days=0,pnl=None,mean=None,positive=0,negative=0,flat=0,cycles=0,fees=0,gross=0,tail_days=0,
                            worst_day=None,worst_pnl=None,largest_drawdown=None,depth_exit=None,no_fills=0)
    worst=min(rows,key=lambda s:s['pnl_cny'])
    net=round(sum(s['pnl_cny'] for s in rows),2)
    liq=[s['hypothetical_tail_liquidation_pnl_cny'] for s in rows]
    return dict(days=len(rows),pnl=net,mean=round(net/len(rows),2),positive=sum(s['pnl_cny']>0 for s in rows),
        negative=sum(s['pnl_cny']<0 for s in rows),flat=sum(s['pnl_cny']==0 for s in rows),cycles=sum(s['complete_cycles'] for s in rows),
        fees=round(sum(s['fees_cny'] for s in rows),2),gross=round(sum(s['gross_including_tail_cny'] for s in rows),2),
        tail_days=sum(s['end_inventory']>0 for s in rows),worst_day=worst['market_date'],worst_pnl=worst['pnl_cny'],
        largest_drawdown=max(s['max_drawdown_cny'] for s in rows),depth_exit=round(sum(liq),2) if all(x is not None for x in liq) else None,
        no_fills=sum(s['fill_count']==0 for s in rows),arrival_cross_fills=sum(s['arrival_cross_fills'] for s in rows),
        fill_count=sum(s['fill_count'] for s in rows),completed_net=round(sum(s['completed_net_cny'] for s in rows),2),
        tail_net=round(sum(s['tail_including_open_fee_cny'] for s in rows),2),
        worst_cycle=min((s['worst_cycle_cny'] for s in rows if s['worst_cycle_cny'] is not None),default=None))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--input',required=True);args=parser.parse_args()
    out=Path(args.input);matrix=json.loads((out/'matrix.json').read_text(encoding='utf-8'));S=matrix['summaries'];coverage=matrix['coverage']
    windows={'prior':('20260810','20260908'),'all':('20260810','20260909'),'common':('20260827','20260908'),'development':('20260909','20260909')}
    def selected(c=None,m='improve_single_d500',p='entry',w='prior'):
        start,end=windows[w]
        return [s for s in S if (c is None or s['code']==c) and s['mode']==m and s['profile']==p and start<=s['market_date']<=end]
    groups=[]
    for window in windows:
        for c in NAMES:
            for mode in MODES:
                for profile in ['control','entry']:
                    groups.append(dict(window=window,code=c,mode=mode,profile=profile,**aggregate(selected(c,mode,profile,window))))
    def get(c,m='improve_single_d500',p='entry',w='prior'):
        return next(x for x in groups if x['code']==c and x['mode']==m and x['profile']==p and x['window']==w)
    def table(headers,rows):return '| '+' | '.join(headers)+' |\n|'+'|'.join('---' for _ in headers)+'|\n'+'\n'.join('| '+' | '.join(map(str,r))+' |' for r in rows)
    primary=table(['代码/名称','评分','此前天数','原循环净值','过滤净值','日均','盈/亏/平日','完整轮次','尾仓日','最差日/净值'],[
        [c+' '+n,'待评分',get(c)['days'],get(c,p='control')['pnl'],get(c)['pnl'],get(c)['mean'],
         f"{get(c)['positive']}/{get(c)['negative']}/{get(c)['flat']}",get(c)['cycles'],get(c)['tail_days'],
         str(get(c)['worst_day'])+' / '+str(get(c)['worst_pnl'])] for c,n in NAMES.items()])
    mode_table=table(['代码',*MODE_LABELS],[[c,*[get(c,m)['pnl'] for m in MODES]] for c in NAMES])
    common_table=table(['代码','共同窗口天数','净值','日均','盈/亏/平','尾仓日','9月9日开发日'],[
        [c,get(c,w='common')['days'],get(c,w='common')['pnl'],get(c,w='common')['mean'],
         f"{get(c,w='common')['positive']}/{get(c,w='common')['negative']}/{get(c,w='common')['flat']}",get(c,w='common')['tail_days'],
         get(c,w='development')['pnl']] for c in NAMES])
    costs=table(['代码','毛值含尾仓','费用','已完成回合净值','未完回合含开仓费','假想尾仓深度平仓后','最大单日盯市回撤','零成交日'],[
        [c,*[get(c)[k] for k in ['gross','fees','completed_net','tail_net','depth_exit','largest_drawdown','no_fills']]] for c in NAMES])
    bykey={(x['code'],x['date']):x for x in coverage}
    trends=[]
    for c in NAMES:
        rows=selected(c)
        down=[r for r in rows if bykey[(c,r['market_date'])]['day_mid_change_cny']<0]
        up=[r for r in rows if bykey[(c,r['market_date'])]['day_mid_change_cny']>=0]
        d=aggregate(down);u=aggregate(up)
        trends.append([c,d['days'],d['pnl'],d['tail_days'],u['days'],u['pnl'],u['tail_days']])
    trend_table=table(['代码','期权下跌日数','下跌日净值','下跌日尾仓','平/涨日数','平/涨日净值','平/涨日尾仓'],trends)
    history=aggregate(selected())
    daily=table(['日期','代码','原循环','过滤净值','毛值','手续费','完整轮次','尾仓','最大日内回撤','当日中点变化%'],[
        [s['market_date'],s['code'],next(x['pnl_cny'] for x in S if x['code']==s['code'] and x['market_date']==s['market_date'] and x['mode']==s['mode'] and x['profile']=='control'),
         s['pnl_cny'],s['gross_including_tail_cny'],s['fees_cny'],s['complete_cycles'],s['end_inventory'],s['max_drawdown_cny'],
         round(bykey[(s['code'],s['market_date'])]['day_mid_change_percent'],3)] for s in sorted(selected(w='all'),key=lambda s:(s['market_date'],s['code']))])
    availability=table(['代码','上市日','收到/通过日数','最早/最晚','最低日盘口覆盖'],[
        [c,'20260827' if get(c,w='all')['days']==10 else '窗口前已上市',
         str(sum(x['capture_status']=='received' for x in coverage if x['code']==c))+'/'+str(sum(x['eligible'] for x in coverage if x['code']==c)),
         min(x['date'] for x in coverage if x['code']==c and x['eligible'])+' / '+max(x['date'] for x in coverage if x['code']==c and x['eligible']),
         f"{min(x['valid_book_coverage'] for x in coverage if x['code']==c and x['eligible'])*100:.3f}%"] for c in NAMES])
    md=f'''# 固定大道至简期权v3近月验证：2026-08-10—09-09

**本轮没有通过多日盈利检验。** 已取得九券上市范围内全部历史tick：四券23天、五券10天，共142个品种日。未上市的65格单列，不填零。固定费用纠偏v3主候选及control，不调阈值、不重选证券，单边每张1.7元、固定质量余量6元，五执行档均重跑。

主比较使用此前未参与本轮参数构建的08-10—09-08；9月9日已用于开发，单独列示。所选九券来自9月9日观察后逆查历史，存在事后选券/存续偏差，不能称严格样本外。下表默认核验单笔＋500ms。收益含尾仓按买一估值，日账户独立，不是连续月度资本收益。

此前历史共{history['days']}个品种日，主候选描述性净值合计{history['pnl']}元、{history['positive']}盈/{history['negative']}亏/{history['flat']}平，{history['cycles']}完整回合，{history['tail_days']}个日末留仓。不同证券拥有各自1万元，历史长度不同，这一合计不是统一组合收益率。

参照：**132024.SH 江铜EB，100分（人工确认参照）；132026.SH G三峡EB2，100分（人工确认参照）**。九只期权均待评分，未建立专用综合评分。两债不参与本次期权收益统计。

## 结论及几个重要反例

- 此前133个品种日，主档从原循环−36117.7元改善至−6809.9元，减亏明显，但仍亏；过滤毛值已经−3993元，另扣费用2816.9元，不能继续只归因于收费偏高。已完成回合净亏6199.2元，未完回合含开仓费再拖累610.7元，亏损也不是只藏在尾仓。
- 九券中八券主档历史净亏。今天看起来较好的10012348，历史9日−924.7元，3盈6亏，五种执行档全部亏；9月4日一日−528元，持仓中点漂移−604.5元，入场/出场中点边际+64.5/+63元抵不住，费用51元。
- 10012084历史22日主档−3542.6元。8月27日−748.6元，29轮中20轮不到30秒，净亏488元；说明急速不利成交也能持续侵蚀收益，无需长时间扛单。10012359在8月27日则是6轮10分钟以上交易净亏651.4元，存在另一种较长占仓风险。时长含午休，中点分解是事后会计恒等式，不作为未来信号。
- 90007929主档唯一正值+166.6元，却22日只买入两次、0次完整回合、两天各留1张尾仓，20日零成交；+195.3/−28.7元均来自未平仓盯市。不能把它当作做市盈利赢家，主档没有一只同时拿出正累计与可重复闭环的证据。
- 粗L1档合计仍可+853.2元，但只保留核验单笔便变−4263.3，500ms/1秒/排队为−6809.9/−8097.5/−7423.8元。结果强烈依赖成交归属和时序，粗L1正值不足以支持晋级。10011070有些档近零或正，主档−53元，但22日12天尾仓、最差日内买一盯市回撤2597.5元，风险不等于接近零的累计净值。
- 10012084按期权自身中点分组：12个下跌日−3912.3元、10个平涨日+369.7元；10012359下跌日−1095.4、平涨日+310元。方向暴露确实与亏损相关，但10012348平涨日也亏389.5元，不能只加一个方向开关便认定解决。

结论是保留此版为基准，暂不认定有可用盈利优势，不在这批已看到的历史里继续按利润挑阈值/赢家。若继续研究，优先检验可因果使用的ETF/期权外部价格信号与真实执行，而非仅依赖显示价差；本次没有新增或上线这些规则。

## 数据覆盖

{availability}

23个交易日由本机MiniQMT交易日历确定。五只10月合约08-27上市。只读xtdata按本地配置端口58610下载，没有修改端口或接触交易接口。142收到日全部通过：每段连续竞价有效盘口时间覆盖≥95%，首尾各≤60秒缺口，报价最多延续30秒、不跨午休，无累计回退；最终tick成交量与日线完全一致，金额误差≤max(2元,1ppm)。最低整日覆盖98.729%，未以删除极宽报价改善均值。当前标准合约单位10000份、每跳1元；未接续到其他行权价或调整合约。

## 此前历史：固定筛选与原循环对比（元）

{primary}

老合约此前22天、新合约此前9天；日均分母包括合法零成交日，不包括未上市日。所有样本有效报价按初始1万元均可买一张，不是因初始资金买不起而普遍零成交；运行中仍按实际现金限制可买数量、未事后加资。初始现金可支付报价时间占比保存在coverage记录。盈日数不能代替盈亏幅度。

## 同一候选换执行口径（此前历史，元）

{mode_table}

L1归属、严格单笔筛选、500/1000ms快照边界延迟、原价可见前队独立回放；single/queue不是收益下界，延迟可能arrival_cross。下单延迟、竞争与隐藏/撤单队列仍未校准，尤其少回合正值须查看明细。

## 共同窗口与开发日分开

{common_table}

共同窗口08-27—09-08共9天，让全部九券在相同日期比较；09-09列只用于对照旧结果，不混称验证样本。

## 费用、尾仓和风险（此前历史，元）

{costs}

每个品种日从空仓1万元开始；日末保留的仓位按买一计入当日净值，下一研究日独立重置，不声称真实卖清、不测隔夜损益。因此日结果相加仅是独立日诊断。另按最后有效买盘五档足量可执行价计算假想清仓并补一边费用；超深度拒报完整清仓值。这一栏也是静态压力，不是实测日末成交。最大回撤为单个品种日全帧买一盯市最大值，不是组合/月度连续账户回撤。

## 期权自身下跌日是否更差

{trend_table}

上涨/下跌依据连续时段第一与最后有效盘口中点作事后分组，不参与决策，也不等于ETF标的涨跌。分组只说明样本关联，不能直接归因为趋势预测正确或错误。

## 验证与文件

新增5项日期/重置/首帧/时段覆盖测试，连同6项费用分离测试共11项通过；原冻结引擎未修改。9份09-09新历史输入逐事件与旧归档完全相同；90份09-09账户逐单/逐成交/资金曲线与保存v3完全相同。{matrix['audited_accounts']}个账户逐笔对账并从{matrix['independently_rebuilt_fill_rows']}条成交独立重建{matrix['independently_rebuilt_observations']}个资金/库存观察点，{matrix['prefix_checks']}个截断前缀一致；旧{matrix['frozen_baseline_files_verified']}份v3归档和所依赖旧源码哈希保持。

输入本地`期权做市研究/近月行情_20260810_20260909/`含原始按日pkl、日线、capture_manifest和input_audit；新结果`期权做市研究/大道至简近月验证_20260810_20260909_v3/`含prerun合同、matrix、1420份完整账户、按日图表及冻结清单。引擎`option_guard_cost_separated.py`，泛日期适配`option_history_replay.py`，入口`scripts/probe_option_month.py`；模型ID仍为不可变v3，订单、成交及账户增加真实market_date用于追溯。

本次没有新的用户交易原则、阈值/收费/退出改动或实时矩阵变化。未测连续跨夜、ETF对冲、真实L2成交和独立未来日期，不能按本月事后赢家直接晋级。
'''
    (out/'近月研究报告.md').write_text(md,encoding='utf-8')
    (out/'主档逐日明细.md').write_text('# 主档逐日明细\n\n参照：132024.SH 江铜EB、132026.SH G三峡EB2，均100分（人工确认参照）；下列期权全部待评分。日期2026-08-10—09-09，单边1.7元，每日独立1万元/1张。\n\n'+daily,encoding='utf-8')
    (out/'aggregate.json').write_text(json.dumps(dict(groups=groups,windows=windows),ensure_ascii=False,indent=2),encoding='utf-8')
    data=dict(summaries=S,coverage=coverage,groups=groups,windows=windows,codes=list(NAMES),names=NAMES,modes=list(MODES),labels=MODE_LABELS,dates=matrix['trading_dates'])
    (out/'summary_data.js').write_text('const DATA='+json.dumps(data,ensure_ascii=False,separators=(',',':'))+';',encoding='utf-8')
    page='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>期权近月固定规则验证</title><script src="../盘口曲线_20260909/plotly-3.1.0.min.js"></script><script src="summary_data.js"></script>
<style>body{margin:0;background:#f2f5fa;color:#21364e;font:15px 'Microsoft YaHei',sans-serif}main{max-width:1500px;margin:auto;padding:26px}h1{font-size:27px}h2{font-size:21px}p{line-height:1.8}.panel{background:white;padding:18px;border:1px solid #dce5ef;border-radius:11px;margin:16px 0}.callout{border-left:5px solid #2e7397}.muted,small{color:#66778f}select,button{font:inherit;padding:8px;border:1px solid #bacbdd;border-radius:5px;background:white;margin:6px}table{width:100%;border-collapse:collapse;white-space:nowrap;font-size:13px}td,th{padding:10px;border-bottom:1px solid #e2e8f1;text-align:right}th{background:#f7f9fc}td:first-child,th:first-child{text-align:left}.scroll{overflow-x:auto}.pos{color:#147961}.neg{color:#bd3e48}#heatmap{height:440px}#daily{height:280px}#book{height:430px}#equity{height:280px}a{color:#226b9b}.toolbar{position:sticky;top:0;background:#f2f5fa;z-index:5;padding:5px 0}</style></head><body><main>
<h1>期权近月验证 · 固定筛选与单边1.7元</h1><p class="muted">2026-08-10—09-09 ｜ 四券23天，五券自8月27日上市后10天 ｜ 142个有效品种日</p>
<div class="panel callout"><b>多日复查没有通过：过滤减少亏损，尚无稳定盈利证据。</b><p>此前历史500ms档八券净亏，唯一正值来自两次未平仓盯市，0个完整回合。默认看8月10日至9月8日；9月9日已用于开发，单列。固定原筛选和1.7元费用，九只合约按9月9日观察后逆查，仍有事后选券偏差。每券每日独立1万元/零底仓/最多1张，收益含尾仓，未模拟连续跨夜账户。</p><p id="headline"></p></div>
<p>人工参照：132024.SH 江铜EB，100分；132026.SH G三峡EB2，100分。期权均待评分，未建立专用综合评分；两债不参与此处期权收益。</p>
<div class="toolbar"><label>窗口<select id="window"><option value="prior">此前历史：8/10—9/8</option><option value="common">共同窗口：8/27—9/8</option><option value="all">全部日期：含9/9开发日</option><option value="development">仅9/9开发日</option></select></label><label>执行<select id="mode"></select></label><label>合约<select id="contract"></select></label></div>
<div id="matrix" class="panel scroll"></div><p class="muted">未上市或缺失不当零收益；日均按各券有效日数。日内最大回撤为最差单日值，不是月度组合回撤。各行独占1万元。</p>
<h2>每天赚亏在哪里：点击格子看当天</h2><div class="panel"><div id="heatmap"></div><div id="daily"></div></div>
<h2>逐日盘口、模拟成交与盈亏</h2><label>日期<select id="date"></select></label><button id="worst">该券窗口内最差日</button><button id="all">当天全程</button><button id="fit">适应价格</button><div id="stats" class="panel"></div><div class="panel"><div id="book"></div><div id="equity"></div><p class="muted">蓝线买一、橙线卖一；红▲/绿▼为过滤模型模拟买卖，灰色空心三角为原循环。资金线蓝色过滤、灰色原循环。点击上方热图换日期；期权报价元/份，一张10000份。</p></div>
<details class="panel"><summary>固定规则、数据与账户边界</summary><p>拟挂卖价−拟挂买价≥16元，且≥过去60秒中点波幅一半＋6元；保留60秒预热、急跌和买二断档过滤。质量余量固定，实际每边1.7元只用于现金与费用，不随新历史优化阈值。无ETF对冲，无主动止损新增。</p><p>09:30—11:30 / 13:00—14:57。142品种日通过量额和覆盖核验；最低日盘口时间覆盖98.729%，报价统计最多续30秒。L1/单笔/延迟/排队都是模拟情景，延迟可能跨价，不是实际成交证明。日末尾仓只按买一估值；每个研究日独立开始，不声称已真实清仓或延续上日资金。</p></details>
<p><a href="近月研究报告.md">完整近月报告</a> · <a href="主档逐日明细.md">500ms逐日完整明细</a> · <a href="matrix.json">全部1420账户及覆盖</a></p>
</main><script>
const $=id=>document.getElementById(id),options={responsive:true,scrollZoom:true,displaylogo:false};DATA.modes.forEach((m,i)=>$('mode').add(new Option(DATA.labels[i],m)));DATA.codes.forEach(c=>$('contract').add(new Option(c+' '+DATA.names[c],c)));DATA.dates.forEach(d=>$('date').add(new Option(d,d)));$('mode').value='improve_single_d500';$('date').value='20260908';let activeDay=null,requestedKey='',scriptNode=null;
function number(x){return x===null||x===undefined?'—':Number(x).toFixed(2)}function color(x){return `<span class="${x<0?'neg':'pos'}">${number(x)}</span>`}function group(c,p='entry'){return DATA.groups.find(g=>g.code===c&&g.profile===p&&g.mode===$('mode').value&&g.window===$('window').value)}function row(c,d,p='entry'){return DATA.summaries.find(s=>s.code===c&&s.market_date===d&&s.profile===p&&s.mode===$('mode').value)}function dates(){let [a,b]=DATA.windows[$('window').value];return DATA.dates.filter(d=>d>=a&&d<=b)}
function drawTable(){let gg=DATA.codes.map(c=>group(c)),old=DATA.codes.map(c=>group(c,'control'));$('headline').innerText=`当前窗口 ${dates()[0]}—${dates().at(-1)}，${DATA.labels[DATA.modes.indexOf($('mode').value)]}：有效品种日 ${gg.reduce((a,g)=>a+g.days,0)}，原循环合计 ${number(old.reduce((a,g)=>a+(g.pnl||0),0))}元 → 过滤 ${number(gg.reduce((a,g)=>a+(g.pnl||0),0))}元；尾仓 ${gg.reduce((a,g)=>a+g.tail_days,0)}个品种日。合计仅作独立日描述。`;
$('matrix').innerHTML='<table><thead><tr><th>代码／名称／评分</th><th>天数</th><th>原循环净值</th><th>过滤净值</th><th>日均</th><th>盈/亏/平日</th><th>回合</th><th>尾仓日</th><th>最差日净值</th><th>最大日内回撤</th></tr></thead><tbody>'+DATA.codes.map(c=>{let g=group(c),b=group(c,'control');return '<tr><td>'+c+' '+DATA.names[c]+'<br><small>待评分</small></td><td>'+g.days+'</td><td>'+color(b.pnl)+'</td><td>'+color(g.pnl)+'</td><td>'+color(g.mean)+'</td><td>'+g.positive+'/'+g.negative+'/'+g.flat+'</td><td>'+g.cycles+'</td><td>'+g.tail_days+'</td><td>'+color(g.worst_pnl)+'</td><td>'+number(g.largest_drawdown)+'</td></tr>'}).join('')+'</tbody></table>'}
async function overview(){drawTable();let ds=dates(),z=DATA.codes.map(c=>ds.map(d=>row(c,d)?.pnl_cny??null)),max=1;for(let ys of z)for(let v of ys)if(v!==null)max=Math.max(max,Math.abs(v));await Plotly.newPlot('heatmap',[{type:'heatmap',x:ds,y:DATA.codes,z,zmin:-max,zmax:max,zmid:0,colorscale:[[0,'#bd4755'],[.5,'#f5f7fa'],[1,'#258775']],hoverongaps:false,hovertemplate:'%{y}<br>%{x}<br>净值 %{z:.2f}元<extra></extra>'}],{title:{text:'过滤模型每日净值（空白＝未上市/不可用）'},xaxis:{type:'category',tickangle:-35},yaxis:{autorange:'reversed'},margin:{t:45,b:75,l:110,r:35}},options);$('heatmap').on('plotly_click',e=>{let p=e.points[0];if(p.z===null)return;$('contract').value=p.y;$('date').value=p.x;drawDaily();loadDay()});await drawDaily()}
async function drawDaily(){let c=$('contract').value,ds=dates();await Plotly.newPlot('daily',['control','entry'].map(p=>({x:ds,y:ds.map(d=>row(c,d,p)?.pnl_cny??null),type:'bar',name:p==='entry'?'过滤':'原循环',marker:{color:p==='entry'?'#3579ae':'#a8b1be'}})),{barmode:'group',title:{text:c+' 每日净值'},xaxis:{type:'category'},yaxis:{title:{text:'元'}},margin:{t:40,b:55,l:70,r:20},legend:{orientation:'h'}},options)}
function mins(ts){let d=new Date(ts+8*3600000);return d.getUTCHours()*60+d.getUTCMinutes()+d.getUTCSeconds()/60+d.getUTCMilliseconds()/60000}function clock(x){return String(Math.floor(x/60)).padStart(2,'0')+':'+String(Math.floor(x%60)).padStart(2,'0')}function axis(){let t=[570,600,630,660,690,780,810,840,870,897];return {range:[570,897],tickvals:t,ticktext:t.map(clock)}}
function loadDay(){let c=$('contract').value,d=$('date').value;requestedKey=c+'_'+d;window.dayReady=false;let q=DATA.coverage.find(x=>x.code===c&&x.date===d);if(!q?.eligible){activeDay=null;$('stats').innerText=q?.capture_status==='not_listed'?'该合约当日尚未上市，没有填作零收益。':'该日数据不可用于完整日比较。';for(let id of ['book','equity']){Plotly.purge(id);$(id).innerText='无可回放行情'}window.dayReady=true;return}if(activeDay&&activeDay.code===c&&activeDay.date===d){drawDay();return}scriptNode?.remove();scriptNode=document.createElement('script');scriptNode.src='plots/'+requestedKey+'.js';scriptNode.onerror=()=>{$('stats').innerText='本地按日图表读取失败';window.dayReady=true};document.body.appendChild(scriptNode)}
window.receiveDay=data=>{if(data.code+'_'+data.date!==requestedKey)return;activeDay=data;drawDay()};
async function drawDay(){let d=activeDay,c=d.code,s=row(c,d.date),b=row(c,d.date,'control'),q=DATA.coverage.find(x=>x.code===c&&x.date===d.date);$('stats').innerText=`${c} ${d.date} ｜ 原循环 ${number(b.pnl_cny)} → 过滤 ${number(s.pnl_cny)}元 ｜ 毛 ${number(s.gross_including_tail_cny)}，费 ${number(s.fees_cny)} ｜ ${s.complete_cycles}轮，尾仓${s.end_inventory}张 ｜ 最大回撤 ${number(s.max_drawdown_cny)}元 ｜ 盘口覆盖 ${number(q.valid_book_coverage*100)}% ｜ 当日中点变化 ${number(q.day_mid_change_percent)}%`;
let traces=['bid','ask'].map(k=>({x:d.book.x,y:d.book[k],name:k==='bid'?'买一':'卖一',mode:'lines',line:{color:k==='bid'?'#2775ac':'#d28e2c',shape:'hv',width:1.2},text:d.book.x.map(x=>x===null?'':clock(x)),hovertemplate:'%{text}<br>%{y:.4f}<extra>%{fullData.name}</extra>'}));
for(let p of ['control','entry'])for(let side of ['buy','sell']){let rr=d.runs.find(x=>x.profile===p&&x.mode===$('mode').value),ff=rr.fills.filter(f=>f.side===side);traces.push({x:ff.map(f=>mins(f.ts)),y:ff.map(f=>f.price_cents/1000000),name:(p==='entry'?'过滤':'原循环')+(side==='buy'?'买':'卖'),mode:'markers',marker:{symbol:(side==='buy'?'triangle-up':'triangle-down')+(p==='control'?'-open':''),size:p==='entry'?10:7,color:p==='control'?'#9ba5b4':side==='buy'?'#cc3c53':'#198e71'},text:ff.map(f=>clock(mins(f.ts))+' '+f.kind),hovertemplate:'%{text}<br>%{y:.4f}<extra>%{fullData.name}</extra>'})}
await Plotly.newPlot('book',traces,{title:{text:c+' '+d.date},xaxis:axis(),yaxis:{title:{text:'元/份'},tickformat:'.4f'},margin:{t:40,b:40,l:70,r:20},legend:{orientation:'h'}},options);
await Plotly.newPlot('equity',['control','entry'].map(p=>{let rr=d.runs.find(x=>x.profile===p&&x.mode===$('mode').value),x=[],y=[];rr.curve.forEach((v,i)=>{if(i&&mins(v[0])>=780&&mins(rr.curve[i-1][0])<690){x.push(null);y.push(null)}x.push(mins(v[0]));y.push(v[1]/100)});return {x,y,name:p==='entry'?'过滤':'原循环',mode:'lines',line:{color:p==='entry'?'#3076ad':'#9aa5b6'}}}),{xaxis:axis(),yaxis:{title:{text:'净元'}},margin:{t:20,b:40,l:70,r:20},legend:{orientation:'h'}},options);window.dayReady=true;window.reportReady=true}
function fit(){if(!activeDay)return;let [a,b]=$('book').layout.xaxis.range,lo=Infinity,hi=-Infinity;activeDay.book.x.forEach((x,i)=>{if(x!==null&&x>=a&&x<=b)for(let k of ['bid','ask'])if(activeDay.book[k][i]!==null){lo=Math.min(lo,activeDay.book[k][i]);hi=Math.max(hi,activeDay.book[k][i])}});if(isFinite(lo)){let p=Math.max(.0002,(hi-lo)*.05);Plotly.relayout('book',{'yaxis.range':[lo-p,hi+p]})}}
$('window').onchange=overview;$('mode').onchange=()=>{overview();loadDay()};$('contract').onchange=()=>{drawDaily();loadDay()};$('date').onchange=loadDay;$('worst').onclick=()=>{let g=group($('contract').value);if(g.worst_day){$('date').value=g.worst_day;loadDay()}};$('all').onclick=()=>{if(activeDay){Plotly.relayout('book',{'xaxis.range':[570,897]});Plotly.relayout('equity',{'xaxis.range':[570,897]})}};$('fit').onclick=fit;overview();loadDay();
</script></body></html>'''
    (out/'近月验证.html').write_text(page,encoding='utf-8')
    (out/'html_syntax_check.js').write_text(page.rsplit('<script>',1)[1].split('</script>',1)[0],encoding='utf-8')
    print('Rendered monthly report',len(groups),'aggregate rows',flush=True)


if __name__=='__main__':main()
