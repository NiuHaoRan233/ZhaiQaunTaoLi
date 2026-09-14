"""Create the local HTML comparison and a durable research report from saved runs."""
from pathlib import Path
import argparse
import gzip
import hashlib
import html
import json

from probe_option_top_cycle import ARCHIVE, NAMES, ROOT
from zhaiquant.option_guard_research import PROFILES
from zhaiquant.option_top_cycle_research import MODES

LABELS = dict(control='原版对照',spread='仅扣费价差',trend='＋急跌过滤',gap='＋买盘断档',
              entry='＋波动过滤（主候选）',risk='＋风险退出',entry5='主候选下限5元',entry15='主候选下限15元')
MODE_LABELS = ['原L1／改善一跳','核验单笔／改善一跳','核验单笔＋500ms','核验单笔＋1000ms','原价排队＋500ms']


def table(headers, rows):
    return '<table><thead><tr>'+''.join('<th>'+html.escape(str(x))+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join(
        '<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table>'


def thin(curve):
    # Preserve min/max as well as final value in every ten-second bin and every inventory change.
    selected=set();bins={}
    for i,p in enumerate(curve):
        bucket=p[0]//10000
        if bucket not in bins: bins[bucket]=[i,i,i]
        b=bins[bucket]
        if p[1]<curve[b[0]][1]:b[0]=i
        if p[1]>curve[b[1]][1]:b[1]=i
        b[2]=i
        if i==0 or p[2]!=curve[i-1][2]:selected.add(i)
    for b in bins.values():selected.update(b)
    return [curve[i] for i in sorted(selected)]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--input',required=True);args=parser.parse_args()
    out=Path(args.input)
    matrix=json.loads((out/'matrix.json').read_text(encoding='utf-8'))
    S=matrix['summaries']
    def get(c,p='entry',m='improve_single_d500',f=3):
        return next(s for s in S if s['code']==c and s['profile']==p and s['mode']==m and s['fee_per_side_cny']==f)
    def mdtable(headers,rows):
        return '| '+' | '.join(headers)+' |\n|'+ '|'.join('---' for _ in headers)+'|\n'+'\n'.join('| '+' | '.join(str(x) for x in r)+' |' for r in rows)
    comparison=mdtable(['代码/名称','评分','原版净值','仅价差','主候选净值','加退出净值','主候选毛值','费用','回合','尾仓'],[
        [c+' '+n,'待评分',get(c,'control')['pnl_cny'],get(c,'spread')['pnl_cny'],get(c)['pnl_cny'],get(c,'risk')['pnl_cny'],
         get(c)['realized_gross_cny']+get(c)['tail_gross_cny'],get(c)['fees_cny'],get(c)['complete_cycles'],get(c)['end_inventory']] for c,n in NAMES.items()])
    ablations=mdtable(['代码',*[LABELS[p] for p in PROFILES]],[
        [c,*[get(c,p)['pnl_cny'] for p in PROFILES]] for c in NAMES])
    execution=mdtable(['代码',*[f'{x} 原→新' for x in MODE_LABELS]],[[c,*[
        str(get(c,'control',m)['pnl_cny'])+' → '+str(get(c,'entry',m)['pnl_cny']) for m in MODES]] for c in NAMES])
    risks=mdtable(['代码','原→新回撤','原→新最差回合','原→新持仓分钟','毛值改善','节约费用','原赢回合入点消失数'],[
        [c,*[str(get(c,'control')[k])+' → '+str(get(c)[k]) for k in ['max_drawdown_cny','worst_cycle_cny']],
         f"{get(c,'control')['holding_seconds']/60:.1f} → {get(c)['holding_seconds']/60:.1f}",
         get(c)['delta_gross_cny'],get(c)['saved_fees_cny'],get(c)['old_winning_entries_absent']] for c in NAMES])
    fees=mdtable(['代码','零费原→新','3元原→新','5元原→新'],[[c,*[
        str(get(c,'control',f=f)['pnl_cny'])+' → '+str(get(c,f=f)['pnl_cny']) for f in [0,3,5]]] for c in NAMES])
    diag=json.loads((ARCHIVE/'options_loss_diagnosis_20260909.json').read_text(encoding='utf-8'))
    md=f'''# 大道至简期权：亏损原因与过滤试验

日期2026-09-09，连续竞价09:30—11:30、13:00—14:57。每只独立1万元、零底仓、最多1张。以下默认核验单笔＋500ms、每张单边3元（费用假设，非用户真实费率）。净值包括尾仓按买一估值；未真实强平，无ETF对冲。所有候选参数在本轮完整回放前登记，但今天已经用于诊断，属于开发样本复用。

参照：**132024.SH 江铜EB，100分（人工确认参照）；132026.SH G三峡EB2，100分（人工确认参照）**。期权全部待评分，未建立可核验的期权综合评分。两债不参与本次期权资金结果。

## 结果判断

**过滤显著减亏，尚未形成稳健可交易优势。** 九个独立1万元账户的描述性合计（共9万元，非共享资金），原L1/单笔/500ms/1000ms/排队500ms由−2886/−1495/−2353/−2685/−1443元变为−39/+60/+67/−173/−64元。仅价差的500ms合计仍−1469元；再加急跌/断档分别−1678/−1634元，并非越多过滤越好。最后加近期波动尺度才到+67元，但这里是层叠比较，不能据此确认波动过滤单独的贡献。

- 10012084的500ms从−980变+184元，134轮降至21轮、回撤1367降至187元；净改善1164元中毛值改善486元、节省手续费678元。1秒变−126元，排队−168元；+184元中最大两轮为+134/+71元，拿掉这两轮其余合计−21元。42次成交中10次到达跨价，不能包装成稳定纯被动收益。
- 10012348是目前较值得继续验证的一只：五档均正，依次+186/+150/+363/+124/+74元；但500ms仅9轮，09:32:29—09:34:03的+148元及13:43:52—13:56:08的+122元占净值74.4%，18次成交中4次到达跨价。存在11:29:58—13:05:40跨午休仓，净亏19元；不是无方向风险。
- 10012359仍亏196元；10011070从−25变−154元，毛值反而少165元、只省费36元，原版8个赢轮的入点没有保留。10011503仍亏90元。三只主档零成交不等同于过滤后会赚钱。
- 统一风险退出不是改进结论：500ms九账户合计从+67降至−334元；10012348从+363降至+228元。其跨午休仓在午后首段按超时卖出亏107元，而entry被动退出亏19元；13:43那轮也被300秒退出截短。更早了结有时减小回撤，但会增加主动付价及截断回升。AUv8没有这项强平逻辑，不能把失败归到AU原规则。
- 5/10/15元下限的500ms合计+113/+67/+121元，1秒却−28/−173/−80元。没有通过小阈值邻域获得跨执行稳健盈利。实际费率输入改变入场门槛及路径，零费用反而更亏的情景不意味着提高真实费用能改善一条固定交易路径。

本轮只保存研究候选，不晋级实时矩阵。下一步最有价值的是以新日期预先固定参数复查，并补同步ETF/波动率与真实队列、接收时序证据；不能继续只在今天按利润挑标的和阈值。本批没有新增用户确认交易原则。

## 哪里亏

1. **不利趋势确实会吞掉价差，但不必长时间扛单。** 10012084原L1的156轮，中点会计分解：入场相对中点+247.5元、持仓中点变化−907.5元、退出相对中点+354元，合计毛亏306元；再扣936元费用，净亏1242元。小于30秒74轮净亏395元，30—120秒68轮净亏762元，2—10分钟14轮净亏85元，超过10分钟为零。最差轮09:38:35—09:39:47约72.5秒，净亏247元。
2. **买到时优势已不在、退出也可能落后。** 同券500ms路径的入场/持仓漂移/退出分解为−292/+302.5/−186.5元，毛亏176元、费用804元、净亏980元。该路径78次买入当帧中点已低于成交价。延迟和成交选择改变了持仓路径，不能把两种执行假设都笼统称为“扛趋势亏”。
3. **另一些合约确有数分钟或更长的占仓问题。** 10012348原L1，2—10分钟15轮净亏453元、超过10分钟2轮净亏162元；10012359原L1持仓中点漂移−516.5元。慢流动性也不自动有利：10011503原L1的两轮10分钟以上交易净亏85元。
4. **只排除窄价差不够。** 10012084原L1在创建买单时预计扣费空间≥10元的118轮仍净亏795元。报价宽可能伴随更大的未来波动，显示价差不是可锁定利润。原版已经亏损照卖，问题不是拒绝低于成本卖出，而是跟卖一等待时仍承担方向风险。

中点分解是成交当帧和退出当帧的事后会计恒等式，不证明真实合理价、不证明订单簿因果。按入场中点、趋势、到达跨价分组存在交叉，不能相加；同一桶内结果是关联证据。只有时长桶互斥。原诊断JSON保留逐轮记录。

## 借鉴AU的什么

已读取用户仓库树 `51a3093b6d08a0bc99098e5cacd6a1fd803e15d1` 的《Au一手利润优先版_归档记录_20260908.md》及v7过滤源码。AU v8使用沪金外部参考价＋300秒EWMA基差，60秒/3时点预热，价差、买一买二断档、10秒外部急动和扣费参考空间过滤；成交后仅反向一档平仓，**没有主动止损**。本批没有同步ETF外部输入，采用期权自身过去中点作风险尺度，不称独立合理价。未执行外部仓库的交易程序。

## 事先固定的逐层试验

- control：完全关闭新特征，逐笔对照原v1。
- spread：拟挂卖价减拟挂买价再扣双边手续费，至少剩10元。改善两边各一跳也扣除；原价排队不用扣两跳。
- trend：加60秒/3时点预热。10秒跌幅达到max(当前价差,5跳)，或60秒跌幅达到max(2倍价差,10跳)，暂停新买；历史锚点最多陈旧2秒，午后重置预热。
- gap：再加可见买二，买一买二距离不超过当前价差一半。
- entry（主候选）：再要求预计扣费空间≥过去60秒中点高低差一半，衡量显示空间能否覆盖近期波动。
- risk：沿主候选入场，另测持仓≥300秒或相对买价买一亏损≥max(3倍入场价差,30跳)连续观测5秒的锁定退出；按原档延迟到达时可见五档IOC，不承诺触发价成交。退出后60秒冷却。它是额外实验，并非AU原规则。
- entry5/entry15：只把主候选的固定空间下限改为5/15元，用来检查小范围阈值稳定性。不能从中挑当天最高值当标准。

以上金额均元/合约，每跳1元（报价0.0001元/份×10000份）。缺有效盘口沿原v1取消处理；过滤取消也有延迟，在途买单和旧单先结算，不能用新信号拒绝旧亏损成交。入场条件不阻止平仓。有效来源异常间隔超过2秒会重置5秒亏损确认；300秒持仓计墙钟，含午休，在下一个连续时段才执行。

## 完整主表

{comparison}

各券1万元互相独立。不能把九券合计当一个1万元共享账户收益；若引用跨券合计，只是9万元独立账户的描述，非实际组合及同期组合回撤。

## 每一层是否有效

{ablations}

## 执行情景稳不稳

{execution}

单笔只保留可核验方向的单笔增量，并非收益下界；L1不是逐笔真成交。延迟档可能arrival_cross；排队仅按可见前队和相容成交递减，无真实L2撤单/竞争。零成交、少回合和一段较长持仓的盈利不等于稳定做市。

## 改善来自少交费还是更好毛收益

{risks}

净改善严格等于毛值改善＋节约费用。原赢入点消失仅按原成交时间与价格核对，包含整个持仓路径变化，不能视为被单一过滤器独立剔除的盈利机会。回撤为全帧买一盯市，时长可能含午休。

## 费率压力（各费率重新运行）

{fees}

零费与有费版本的入场空间门槛也随费用变化，因此不只是原路径机械减费用。真实费率、真实排队和接收延迟仍待核验。本轮没有新增用户确认交易原则，也没有改变现有债券模型。

## 验证与复现

38项相关测试通过（12项新过滤/时序/主动退出测试＋26项既有测试）。{matrix['audited_accounts']}个独立账户逐笔重算现金、库存、费用、已实现与尾仓；主动退出核对到达时刻与五档深度，回撤从完整曲线重算。另用独立脚本从26,009条成交重建21,215,280个全帧资金和库存观察点，全部一致。{matrix['baseline_accounts_verified']}个control账户与保存v1逐笔订单/成交/曲线一致，{matrix['prefix_checks']}个截断前缀一致；{matrix['baseline_files_verified']}份旧归档哈希保持，旧源码哈希保持。真实市场撮合仍未被验证。

引擎 `src/zhaiquant/option_guard_research.py`；运行 `scripts/probe_option_guard.py --output <新目录>`；报告 `scripts/render_option_guard.py --input <该目录>`。同目录 `prerun_contract.json`、`matrix.json`、1080份账户json.gz及最终SHA256清单支持复核；旧v1保持独立。HTML图为单边3元，保存10秒桶极值/末值与每次库存变化，统计来自完整曲线。
'''
    (out/'过滤研究报告.md').write_text(md,encoding='utf-8')
    payload=[]
    for c in NAMES:
        for m in MODES:
            for p in PROFILES:
                r=json.load(gzip.open(out/f'{c}_{p}_{m}_q1_f300.json.gz','rt',encoding='utf-8'))
                payload.append(dict(code=c,mode=m,profile=p,curve=thin(r['curve']),fills=r['fills']))
    data=dict(summaries=S,runs=payload,books=json.loads((ARCHIVE/'盘口曲线_20260909/绘图数据.json').read_text(encoding='utf-8')),
              diagnosis=diag,profiles=list(PROFILES),labels=LABELS,modes=list(MODES),modeLabels=MODE_LABELS)
    (out/'report_data.js').write_text('const DATA='+json.dumps(data,ensure_ascii=False,separators=(',',':'))+';',encoding='utf-8')
    page='''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>期权做市 · 亏损诊断与过滤试验</title>
<script src="../盘口曲线_20260909/plotly-3.1.0.min.js"></script><script src="report_data.js"></script>
<style>*{box-sizing:border-box}body{margin:0;background:#f2f5fa;color:#1d3049;font:15px 'Microsoft YaHei',sans-serif}main{max-width:1500px;margin:auto;padding:28px}h1{font-size:28px;margin:0 0 10px}h2{font-size:20px;margin:24px 0 12px}p{line-height:1.8}small,.muted{color:#60718a}.panel{background:white;border:1px solid #dde5ef;border-radius:12px;padding:18px;margin:14px 0}.callout{border-left:5px solid #367ca0}select,button{font:inherit;background:white;border:1px solid #becdde;border-radius:5px;padding:8px;margin:4px 10px 4px 0}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;white-space:nowrap;font-size:13px}td,th{padding:10px;border-bottom:1px solid #e2e8f0;text-align:right}td:first-child,th:first-child{text-align:left}th{background:#f7f9fc}.negative{color:#bd3c3c}.positive{color:#14735e}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.metric{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.metric b{display:block;font-size:23px;padding-top:6px}#book{height:450px}#equity{height:270px}#decomp{height:270px}#duration{height:270px}details{line-height:1.8}a{color:#236b9b}@media(max-width:800px){main{padding:12px}.grid{grid-template-columns:1fr}.metric{grid-template-columns:1fr 1fr}}</style></head><body><main>
<h1>期权做市 · 亏损诊断与过滤试验</h1><div class="muted">2026-09-09　09:30—11:30 / 13:00—14:57　｜　每券独立1万元，最多1张，零底仓</div>
<div class="panel callout"><b>价差要覆盖费用，也要覆盖等待期间的价格变化。</b><p>10012084 原L1净亏1242元，其中936元是费用；持仓中点下移吃掉907.5元。142轮在两分钟内完成，合计净亏1157元，不能只归因于长时间扛单。下面直接比较原版、逐层过滤与独立风险退出。</p><small>借鉴AU v8的入场结构。AU原版无主动止损；本试验未取得同步ETF参考，过去期权中点只是风险信号。全部是今日开发样本，单边3元为假设，收益包含尾仓盯市。</small></div>
<p><b>双债参照：</b>132024.SH 江铜EB — 100分；132026.SH G三峡EB2 — 100分（人工确认参照）。本次期权均待评分，未建立专用综合标尺。</p>
<h2>全名单：执行方式与逐层结果</h2><label>执行 <select id="mode"></select></label><label>表格费用 <select id="fee"><option value="3">单边3元</option><option value="0">零费用</option><option value="5">单边5元</option></select></label><div id="matrix" class="panel scroll"></div>
<p class="muted">每行独立账户；主候选为“价差＋急跌＋断档＋波动”，没有按各券当天赢家切换。图表固定单边3元；上表可切费用。0成交只表示未交易。</p>
<h2>看清每次买卖与资金变化</h2><label>合约 <select id="contract"></select></label><label>候选 <select id="profile"></select></label><button id="all">全天</button><button id="worst">定位原版最差回合</button><button id="fit">适应当前价格</button>
<div class="panel"><div id="stats" class="metric"></div><p id="detail" class="muted"></p></div>
<div class="panel"><div id="book"></div><div id="equity"></div><small>蓝线买一，橙线卖一；红▲候选模拟买入，绿▼候选模拟卖出，紫色倒三角为主动风险退出。灰色空心三角为原版。悬停显示价格和成交方式；三角不是交易所真实买卖方向。资金图原版灰色、候选蓝色，拖框后两图同步。</small></div>
<div class="grid"><div class="panel"><h2>原版毛值与费用拆分</h2><div id="decomp"></div></div><div class="panel"><h2>原版亏盈按持仓时长分组</h2><div id="duration"></div></div></div>
<p class="muted">拆分仅保存原L1和单笔500ms；其他模式不套用。中点变化是事后会计归因，不是独立公允价，也不能用来事后取消订单。</p>
<details class="panel"><summary><b>过滤规则与边界</b></summary><p>仅价差：扣两边向内改善价和往返费用，剩余至少10元/张。急跌：预热60秒；10秒下跌达到max(价差,5跳)或60秒下跌达到max(2倍价差,10跳)暂停买入。断档：买二存在且买一买二间距≤价差一半。波动：剩余空间≥过去60秒中点高低差一半。下限5/15元是事先固定的参数邻域。</p><p>风险退出另测：持仓300秒，或买一相对成本亏损达到max(入场价差3倍,30跳)持续观测5秒，锁定退出，在原档延迟后按可见五档IOC；退出后冷却60秒。不会保证在触发价成交。过滤撤单也有延迟，先结算旧单，再处理新信息。旧单仍可能在撤单到达前买成；有仓卖出不受入场过滤压住。</p><p>无底仓卖空、无ETF对冲、无真实L2队列、未校准交易延迟/竞争。延迟限价可能跨价，单笔与排队情景均非收益上下界。尾仓未真实清仓，期权跳值1元/张；未把黄金元/克阈值直接搬来。</p></details>
<p><a href="过滤研究报告.md">完整报告：诊断、各层、费用与执行表</a> · <a href="matrix.json">1080账户统计和验证</a> · <a href="../大道至简初试_20260909_v1_run2/初试结果.html">保留的原版结果</a></p>
</main><script>
const $=id=>document.getElementById(id), opts={responsive:true,scrollZoom:true,displaylogo:false};
DATA.books.forEach(b=>$('contract').add(new Option(b.code+' '+b.name,b.code)));DATA.modes.forEach((m,i)=>$('mode').add(new Option(DATA.modeLabels[i],m)));DATA.profiles.forEach(p=>$('profile').add(new Option(DATA.labels[p],p)));$('mode').value='improve_single_d500';$('profile').value='entry';
function number(x){return x===null?'—':Number(x).toFixed(1)}function colored(x){return `<span class="${x<0?'negative':'positive'}">${number(x)}</span>`}
function summary(c,p,m=$('mode').value,f=3){return DATA.summaries.find(s=>s.code===c&&s.profile===p&&s.mode===m&&s.fee_per_side_cny===f)}
function run(c,p){return DATA.runs.find(r=>r.code===c&&r.profile===p&&r.mode===$('mode').value)}
function mins(ts){const d=new Date(ts+8*3600000);return d.getUTCHours()*60+d.getUTCMinutes()+d.getUTCSeconds()/60+d.getUTCMilliseconds()/60000}
function clock(x){return String(Math.floor(x/60)).padStart(2,'0')+':'+String(Math.floor(x%60)).padStart(2,'0')+':'+String(Math.floor(x*60%60)).padStart(2,'0')}
const ticks=[570,600,630,660,690,780,810,840,870,897];function axis(){return {range:[570,897],tickvals:ticks,ticktext:ticks.map(x=>clock(x).slice(0,5))}}
function drawTable(){let f=+$('fee').value;$('matrix').innerHTML='<table><thead><tr><th>合约／评分</th>'+DATA.profiles.map(p=>'<th>'+DATA.labels[p]+'</th>').join('')+'<th>主候选回合／尾仓</th></tr></thead><tbody>'+DATA.books.map(b=>'<tr><td>'+b.code+' '+b.name+'<br><small>待评分</small></td>'+DATA.profiles.map(p=>'<td>'+colored(summary(b.code,p,undefined,f).pnl_cny)+'</td>').join('')+'<td>'+summary(b.code,'entry',undefined,f).complete_cycles+' / '+summary(b.code,'entry',undefined,f).end_inventory+'</td></tr>').join('')+'</tbody></table>'}
function lineXY(curve){let x=[],y=[],text=[];curve.forEach((p,i)=>{if(i&&mins(p[0])>=780&&mins(curve[i-1][0])<690){x.push(null);y.push(null);text.push('')}x.push(mins(p[0]));y.push(p[1]/100);text.push(clock(mins(p[0]))+' / 库存'+p[2]+'张')});return {x,y,text}}
let syncing=false;
function sync(event,target){if(syncing)return;let a=event['xaxis.range[0]'],b=event['xaxis.range[1]'];if(a===undefined&&event['xaxis.range']) [a,b]=event['xaxis.range'];if(a!==undefined){syncing=true;Plotly.relayout(target,{'xaxis.range':[a,b]}).then(()=>{syncing=false})}}
async function draw(){let c=$('contract').value,p=$('profile').value,b=DATA.books.find(b=>b.code===c),r=run(c,p),old=run(c,'control'),s=summary(c,p),base=summary(c,'control');
$('stats').innerHTML=[['原版净值',base.pnl_cny],['候选净值',s.pnl_cny],['净改善',s.delta_pnl_cny],['最大回撤',s.max_drawdown_cny]].map(([k,v])=>'<div>'+k+'<b>'+colored(v)+'<small> 元</small></b></div>').join('');
$('detail').innerText=`完整回合 ${base.complete_cycles} → ${s.complete_cycles} ｜ 持仓 ${number(base.holding_seconds/60)} → ${number(s.holding_seconds/60)}分钟 ｜ 尾仓 ${s.end_inventory}张，毛浮动 ${s.tail_gross_cny}元 ｜ 费用 ${base.fees_cny} → ${s.fees_cny}元 ｜ 毛值改善 ${s.delta_gross_cny}元 ｜ 到达跨价 ${s.arrival_cross_fills}次，主动退出 ${s.risk_ioc_fills}次`;
let traces=['bid','ask'].map(k=>({x:b.x,y:b[k],name:k==='bid'?'买一':'卖一',mode:'lines',line:{color:k==='bid'?'#2675b5':'#d99730',width:1.1,shape:'hv'},text:b.x.map(clock),hovertemplate:'%{text}<br>%{y:.4f}<extra>%{fullData.name}</extra>'}));
for(let [rr,isOld] of [[old,true],[r,false]])for(let side of ['buy','sell']){let fs=rr.fills.filter(f=>f.side===side);traces.push({x:fs.map(f=>mins(f.ts)),y:fs.map(f=>f.price_cents/1000000),mode:'markers',name:(isOld?'原版':'候选')+(side==='buy'?'买入':'卖出'),marker:{color:fs.map(f=>isOld?'#8994a7':f.kind==='risk_ioc'?'#8d4cac':side==='buy'?'#d74052':'#1b9d76'),symbol:(side==='buy'?'triangle-up':'triangle-down')+(isOld?'-open':''),size:isOld?8:11},text:fs.map(f=>clock(mins(f.ts))+'<br>'+f.kind+' / '+f.quantity+'张'),hovertemplate:'%{text}<br>%{y:.4f}<extra>%{fullData.name}</extra>'})}
await Plotly.newPlot('book',traces,{title:c+' '+DATA.labels[p],xaxis:axis(),yaxis:{title:'元/份',tickformat:'.4f'},legend:{orientation:'h'},margin:{t:55,b:35,l:75,r:20},dragmode:'zoom'},opts);
await Plotly.newPlot('equity',[[old,'原版','#929eaf'],[r,'候选','#236dbe']].map(([v,name,color])=>({...lineXY(v.curve),name,mode:'lines',line:{color,width:1.6},hovertemplate:'%{text}<br>%{y:.1f}元<extra>%{fullData.name}</extra>'})),{xaxis:axis(),yaxis:{title:'扣费后元'},legend:{orientation:'h'},margin:{t:30,b:35,l:75,r:20}},opts);
$('book').on('plotly_relayout',e=>sync(e,'equity'));$('equity').on('plotly_relayout',e=>sync(e,'book'));
let d=DATA.diagnosis.find(d=>d.code===c&&d.mode===$('mode').value);
if(d){let k=d.decomposition;await Plotly.newPlot('decomp',[{type:'bar',x:['入场相对中点','持仓中点漂移','退出相对中点','尾仓毛值','费用'],y:[k.entry_half_capture_cny,k.holding_mid_drift_cny,k.exit_half_capture_cny,d.summary.tail_gross_cny,-d.summary.fees_cny],marker:{color:'#5588b0'}}],{margin:{t:10,b:55,l:60,r:15},yaxis:{title:'元'}},opts);let ks=['hold_lt_30s','hold_30_120s','hold_120_600s','hold_ge_600s'];await Plotly.newPlot('duration',[{type:'bar',x:['＜30秒','30—120秒','2—10分钟','≥10分钟'],y:ks.map(k=>d.groups[k].net_cny),text:ks.map(k=>d.groups[k].cycles+'轮'),marker:{color:'#9b799a'}}],{margin:{t:10,b:55,l:60,r:15},yaxis:{title:'已完成回合净元'}},opts)}else{for(let id of ['decomp','duration']){Plotly.purge(id);$(id).innerText='当前执行档未做此拆分；请选择原L1或单笔500ms。'}}window.reportReady=true}
function range(a,b){Plotly.relayout('book',{'xaxis.range':[a,b]});Plotly.relayout('equity',{'xaxis.range':[a,b]});fit(a,b)}
function fit(a,b){if(a===undefined)[a,b]=$('book').layout.xaxis.range;const book=DATA.books.find(x=>x.code===$('contract').value);let ys=[];book.x.forEach((x,i)=>{if(x>=a&&x<=b)for(let k of ['bid','ask'])if(book[k][i]!==null)ys.push(book[k][i])});for(let p of ['control',$('profile').value])for(let f of run(book.code,p).fills)if(mins(f.ts)>=a&&mins(f.ts)<=b)ys.push(f.price_cents/1000000);if(ys.length){let lo=Infinity,hi=-Infinity;for(let y of ys){lo=Math.min(lo,y);hi=Math.max(hi,y)}let pad=Math.max(.0002,(hi-lo)*.05);Plotly.relayout('book',{'yaxis.range':[lo-pad,hi+pad]})}}
$('mode').onchange=()=>{drawTable();draw()};$('fee').onchange=drawTable;$('contract').onchange=draw;$('profile').onchange=draw;$('all').onclick=()=>range(570,897);$('fit').onclick=()=>fit();$('worst').onclick=()=>{let d=DATA.diagnosis.find(d=>d.code===$('contract').value&&d.mode===$('mode').value);if(d&&d.worst.length){let w=d.worst[0];range(mins(w.entry_ts)-1,mins(w.entry_ts)+w.duration_seconds/60+1)}};drawTable();draw();
</script></body></html>'''
    (out/'过滤对比.html').write_text(page,encoding='utf-8')
    inline=page.rsplit('<script>',1)[1].split('</script>',1)[0]
    (out/'html_syntax_check.js').write_text(inline,encoding='utf-8')
    print('Rendered',len(payload),'graph paths',len(page),'HTML chars',flush=True)


if __name__=='__main__':main()
