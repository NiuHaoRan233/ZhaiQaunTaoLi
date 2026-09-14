"""Report the controlled cost correction; retain the rejected coupled comparison."""
import argparse
import gzip
import json
from pathlib import Path

from probe_option_top_cycle import ARCHIVE,NAMES
from render_option_guard import thin,MODE_LABELS
from zhaiquant.option_top_cycle_research import MODES

OLD=ARCHIVE/'大道至简过滤_20260909_v2'
COUPLED=ARCHIVE/'大道至简过滤_20260909_v2_f170'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--input',required=True);args=parser.parse_args()
    out=Path(args.input);matrix=json.loads((out/'matrix.json').read_text(encoding='utf-8'));S=matrix['summaries']
    def get(c,m='improve_single_d500'):return next(s for s in S if s['code']==c and s['mode']==m and s['profile']=='entry')
    def total(k,m='improve_single_d500'):return round(sum(get(c,m)[k] for c in NAMES),2)
    def table(headers,rows):return '| '+' | '.join(headers)+' |\n|'+'|'.join('---' for _ in headers)+'|\n'+'\n'.join('| '+' | '.join(map(str,r))+' |' for r in rows)
    main_table=table(['合约','评分','原3元','保持筛选/收1.7元','上轮放宽后/收1.7元','纯省费','完成轮次','尾仓'],[
        [c+' '+n,'待评分',get(c)['old_3cny_pnl'],get(c)['pnl_cny'],get(c)['coupled_170_pnl'],get(c)['fee_saving_cny'],get(c)['complete_cycles'],get(c)['end_inventory']] for c,n in NAMES.items()])
    mode_table=table(['执行情景','原筛选3元','同筛选1.7元','放宽筛选1.7元'],[
        [label,total('old_3cny_pnl',m),total('pnl_cny',m),total('coupled_170_pnl',m)] for m,label in zip(MODES,MODE_LABELS)])
    md=f'''# 期权费用纠偏：固定筛选标准，只修正手续费

2026-09-09。用户指出“过滤判断还改了反而更低了，说明你判断有问题”。这项质疑成立：上一轮将同一个费用参数同时用于现金收费和入场过滤，修正费率时放宽了实际入场边界；把这一条路径作为费用纠正的主结果，不符合保持筛选标准的比较目的。

此前主筛选条件为：拟挂卖价−拟挂买价−6元≥10元，且≥过去60秒中点高低差的一半。直接将费用改为1.7元后，减去的6元变成3.4元，使拟挂价差门槛从16降至13.4元，波动门槛也降低2.6元。名义上没改10元和波动倍率，实际允许的交易集合已经变了。“代码和阈值不变”不能解释成经济入场条件没变。

本次用新身份v3把质量余量固定600分，实际收费单边170分。重新从原始行情产生信号、订单和成交，不读取旧成交驱动策略，不用今天后来赚钱的回合反选入点。真实资金不足仍按1.7元现金条件处理，并有测试证明更低费率在资金临界时可以合法改变可买数量；本批1万元/1张未触发这种路径差异。

## 纠正后的结果

500ms主候选九个独立1万元账户，仍是52个完整回合、106张单边成交；毛值385元不变，费用从318降至180.2元，净值由+67变为**+204.8元**。这次已从行情完整重跑并逐笔确认，不再只是旧路径的代数估计。单边费用节省43.33%；新费用占这批毛值46.81%，这个比例仅描述当前样本。

上轮−196.1元保留为“放宽实际门槛后的另一模型路径”，不再用作同样筛选修正费用的主答案。保留旧门槛是在纠正比较条件，不证明该门槛更优。原判断高度敏感且只有一天开发样本，现有证据仍不足以称为稳健策略；本次没有为了结果增加止损、挑券或调阈值。

参照：**132024.SH 江铜EB，100分（人工确认参照）；132026.SH G三峡EB2，100分（人工确认参照）**。以下期权全部待评分，未建立专用综合评分。资金统计仅包括九个各自1万元账户（共9万元），非一个共享1万元账户；最多1张、零底仓、尾仓按买一计入。费用1.7元来自用户回忆，暂作每张每边总费用输入。

{main_table}

## 同筛选的执行压力

{mode_table}

1秒−17元、排队−30.2元仍未转正；今天改善不等于模型可靠。旧f300、耦合f170和本次v3都保留，三者不能混账。单笔、延迟、排队仍为L1快照情景，并非真实队列/成交证明，可能有arrival_cross，缺ETF对冲及多日证据。

## 验证

本次28项聚焦测试通过，含新增6项：收费修正不放行原拒绝盘口、订单一致且只扣实际费用、f300父版等价、真实现金可负担边界、截断前缀、身份参数分离。90个账户（entry/control×五档×九券）逐笔对账；90条路径与冻结原f300逐订单、逐成交时间/价格/数量/来源方式完全相同，逐帧决策拒绝原因也一致。所有1767940个曲线点满足原净值加累计节省费用；45个entry情景各两个截断前缀，共90个通过。旧1484份归档及旧源码保持。独立曲线审计和HTML核验记录另存本地。

实现`src/zhaiquant/option_guard_cost_separated.py`，新ID `probe_option_dadao_guard_20260909_v3_{{profile}}_{{mode}}_q1_b600_f170`；运行`scripts/probe_option_cost_separated.py --output <新目录>`，报告`scripts/render_option_cost_separated.py --input <该目录>`。本地归档`债券活跃度观察/期权做市研究/大道至简费用纠偏_20260909_v3/`保存合同、全明细和验证。原代码保持不可变，正式债券模型和九模型矩阵不变。
'''
    (out/'费用纠偏报告.md').write_text(md,encoding='utf-8')
    runs=[]
    for c in NAMES:
        for m in MODES:
            for kind,path in [('correct',out/f'{c}_entry_{m}_q1_b600_f170.json.gz'),
                              ('old',OLD/f'{c}_entry_{m}_q1_f300.json.gz'),
                              ('coupled',COUPLED/f'{c}_entry_{m}_q1_f170.json.gz')]:
                r=json.load(gzip.open(path,'rt',encoding='utf-8'))
                runs.append(dict(code=c,mode=m,kind=kind,curve=thin(r['curve']),fills=r['fills']))
    data=dict(summaries=S,runs=runs,modes=list(MODES),labels=MODE_LABELS,
              books=json.loads((ARCHIVE/'盘口曲线_20260909/绘图数据.json').read_text(encoding='utf-8')))
    (out/'report_data.js').write_text('const DATA='+json.dumps(data,ensure_ascii=False,separators=(',',':'))+';',encoding='utf-8')
    page='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>期权费用纠偏 · 固定筛选标准</title><script src="../盘口曲线_20260909/plotly-3.1.0.min.js"></script><script src="report_data.js"></script>
<style>body{margin:0;background:#f1f5fa;color:#1b324e;font:15px 'Microsoft YaHei',sans-serif}main{max-width:1450px;margin:auto;padding:28px}h1{font-size:28px}p{line-height:1.8}.panel{background:white;border:1px solid #dce4ef;border-radius:10px;padding:18px;margin:16px 0}.callout{border-left:5px solid #276f9e}.muted,small{color:#62718a}select,button{padding:8px;font:inherit;margin:8px;border:1px solid #bccddd;background:white;border-radius:5px}table{width:100%;border-collapse:collapse;white-space:nowrap;font-size:13px}th,td{padding:11px;text-align:right;border-bottom:1px solid #e1e8f0}th:first-child,td:first-child{text-align:left}.scroll{overflow-x:auto}th{background:#f7f9fc}.pos{color:#147864}.neg{color:#b83e43}#book{height:440px}#equity{height:300px}a{color:#236d9a}</style></head><body><main>
<h1>期权费用纠偏 · 固定筛选标准</h1><p class="muted">2026-09-09　09:30—11:30 / 13:00—14:57 ｜ 每券独立1万元，最多1张，零底仓</p>
<div class="panel callout"><b>之前把“修正收费”混成了“放宽入场门槛”，比较口径有误。</b><p>现已保持原筛选标准，从行情重新产生订单，只按单边1.7元扣费。500ms九账户仍是52轮：毛值385元，费用180.2元，净值 <b>+204.8元</b>，原3元时为+67元。逐笔订单和成交已确认一致。</p><small>上轮−196.1元保留为放宽门槛后的独立对照。恢复同一筛选只纠正比较，不能证明过滤稳健；1秒/排队仍为−17/−30.2元。1.7元为用户回忆的暂定每张单边总成本。</small></div>
<p>参照：132024.SH 江铜EB，100分；132026.SH G三峡EB2，100分（人工确认参照）。期权均待评分，未建立专用综合标尺。</p>
<label>执行方式<select id="mode"></select></label><div id="matrix" class="panel scroll"></div>
<p class="muted">全部是主候选entry。九行是九个独立1万元账户，非一个共享账户；利润均含尾仓按买一估值。</p>
<label>合约<select id="contract"></select></label><button id="all">全天</button><button id="fit">适应价格</button><div id="stats" class="panel"></div>
<div class="panel"><div id="book"></div><div id="equity"></div><p class="muted">盘口蓝线买一、橙线卖一。灰色空心三角为原筛选3元的模拟成交；红▲/绿▼为同筛选1.7元，位置完全重合。资金线灰色原3元、蓝色纠偏1.7元、红色上轮放宽门槛1.7元。三角均为模拟成交。</p></div>
<details class="panel"><summary>具体修正</summary><p>保持原主候选拟挂卖价−拟挂买价≥16元，且≥过去60秒中点波幅一半＋6元；其余预热、急跌、买二断档条件保持。质量余量6元固定登记，实际费用每边1.7元另行扣现金，不再用收费输入自动改变筛选。资金不足仍按实际费率限制数量。本批所有订单/成交和原筛选一致，不是导入旧成交重计账。</p><p>旧f170曾把16元门槛降至13.4元，并放松波动条件，导致另一条路径；不能把它的亏损称为同样交易降低费用的结果。这里未增加风险退出、未按当天利润挑标的，也没有证明某个门槛最优。</p></details>
<p><a href="费用纠偏报告.md">完整报告与核验</a> · <a href="matrix.json">90账户结果</a> · <a href="../大道至简过滤_20260909_v2_f170/过滤对比.html">保留上轮耦合费用的对照</a></p>
</main><script>
const $=id=>document.getElementById(id),options={responsive:true,scrollZoom:true,displaylogo:false};DATA.modes.forEach((m,i)=>$('mode').add(new Option(DATA.labels[i],m)));DATA.books.forEach(b=>$('contract').add(new Option(b.code+' '+b.name,b.code)));$('mode').value='improve_single_d500';
function s(c){return DATA.summaries.find(x=>x.code===c&&x.mode===$('mode').value&&x.profile==='entry')}function r(c,k){return DATA.runs.find(x=>x.code===c&&x.mode===$('mode').value&&x.kind===k)}function n(x){return Number(x).toFixed(2)}function color(x){return `<span class="${x<0?'neg':'pos'}">${n(x)}</span>`}
function mins(ts){let d=new Date(ts+8*3600000);return d.getUTCHours()*60+d.getUTCMinutes()+d.getUTCSeconds()/60+d.getUTCMilliseconds()/60000}function clock(x){return String(Math.floor(x/60)).padStart(2,'0')+':'+String(Math.floor(x%60)).padStart(2,'0')+':'+String(Math.floor(x*60%60)).padStart(2,'0')}
function axis(){let ticks=[570,600,630,660,690,780,810,840,870,897];return {range:[570,897],tickvals:ticks,ticktext:ticks.map(x=>clock(x).slice(0,5))}}
function drawTable(){$('matrix').innerHTML='<table><thead><tr><th>代码／名称／评分</th><th>原筛选3元</th><th>同筛选1.7元</th><th>上轮放宽1.7元</th><th>毛值</th><th>原→新费用</th><th>完成轮次／尾仓</th></tr></thead><tbody>'+DATA.books.map(b=>{let x=s(b.code);return '<tr><td>'+b.code+' '+b.name+'<br><small>待评分</small></td>'+['old_3cny_pnl','pnl_cny','coupled_170_pnl','gross_including_tail_cny'].map(k=>'<td>'+color(x[k])+'</td>').join('')+'<td>'+n(x.old_3cny_fees)+' → '+n(x.fees_cny)+'</td><td>'+x.complete_cycles+' / '+x.end_inventory+'</td></tr>'}).join('')+'</tbody></table>'}
function points(curve){let x=[],y=[];curve.forEach((p,i)=>{if(i&&mins(p[0])>=780&&mins(curve[i-1][0])<690){x.push(null);y.push(null)}x.push(mins(p[0]));y.push(p[1]/100)});return {x,y}}
async function draw(){let c=$('contract').value,b=DATA.books.find(x=>x.code===c),v=s(c);$('stats').innerText=`原筛选 ${n(v.old_3cny_pnl)}元 → 只改收费 ${n(v.pnl_cny)}元 ｜ 省费 ${n(v.fee_saving_cny)}元 ｜ ${v.complete_cycles}轮，尾仓${v.end_inventory}张 ｜ 订单、成交、拒绝原因与原筛选一致`;
let traces=['bid','ask'].map(k=>({x:b.x,y:b[k],name:k==='bid'?'买一':'卖一',mode:'lines',line:{shape:'hv',width:1.2,color:k==='bid'?'#2676ad':'#d39232'},text:b.x.map(clock),hovertemplate:'%{text}<br>%{y:.4f}<extra>%{fullData.name}</extra>'}));
for(let kind of ['old','correct'])for(let side of ['buy','sell']){let ff=r(c,kind).fills.filter(f=>f.side===side);traces.push({x:ff.map(f=>mins(f.ts)),y:ff.map(f=>f.price_cents/1000000),name:(kind==='old'?'原筛选3元':'同筛选1.7元')+(side==='buy'?'买':'卖'),mode:'markers',marker:{size:kind==='old'?14:9,symbol:(side==='buy'?'triangle-up':'triangle-down')+(kind==='old'?'-open':''),color:kind==='old'?'#8994a6':side==='buy'?'#ce3d52':'#188e72'},text:ff.map(f=>clock(mins(f.ts))+' / '+f.kind),hovertemplate:'%{text}<br>%{y:.4f}<extra>%{fullData.name}</extra>'})}
await Plotly.newPlot('book',traces,{title:{text:c+' '+b.name},xaxis:axis(),yaxis:{title:{text:'元/份'},tickformat:'.4f'},margin:{t:45,b:40,l:70,r:20},legend:{orientation:'h'}},options);
await Plotly.newPlot('equity',[['old','原筛选3元','#8994a6'],['correct','同筛选1.7元','#246cb8'],['coupled','上轮放宽1.7元','#c96868']].map(([kind,name,color])=>({...points(r(c,kind).curve),name,mode:'lines',line:{color,width:1.6},hovertemplate:'%{y:.2f}元<extra>%{fullData.name}</extra>'})),{xaxis:axis(),yaxis:{title:{text:'净值 / 元'}},margin:{t:20,b:40,l:70,r:20},legend:{orientation:'h'}},options);window.reportReady=true}
function fit(){let [a,z]=$('book').layout.xaxis.range,b=DATA.books.find(x=>x.code===$('contract').value),lo=Infinity,hi=-Infinity;b.x.forEach((x,i)=>{if(x>=a&&x<=z)for(let k of ['bid','ask'])if(b[k][i]!==null){lo=Math.min(lo,b[k][i]);hi=Math.max(hi,b[k][i])}});if(isFinite(lo)){let p=Math.max(.0002,(hi-lo)*.05);Plotly.relayout('book',{'yaxis.range':[lo-p,hi+p]})}}
$('mode').onchange=()=>{drawTable();draw()};$('contract').onchange=draw;$('all').onclick=()=>{Plotly.relayout('book',{'xaxis.range':[570,897]});Plotly.relayout('equity',{'xaxis.range':[570,897]})};$('fit').onclick=fit;drawTable();draw();
</script></body></html>'''
    (out/'过滤对比.html').write_text(page,encoding='utf-8')
    (out/'html_syntax_check.js').write_text(page.rsplit('<script>',1)[1].split('</script>',1)[0],encoding='utf-8')
    print('Rendered controlled fee correction',flush=True)


if __name__=='__main__':main()
