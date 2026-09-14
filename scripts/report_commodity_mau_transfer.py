"""Standalone HTML, full daily data and static figures for the fixed transfer study."""
from pathlib import Path
from collections import Counter
import base64
import json
import html
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from probe_commodity_mau_transfer import OUT, ROOT, WORK
from probe_commodity_capital import read, write, unpack, digest

DEST=WORK/'reports/commodity_mau_transfer_report_20260913'
LABELS={'control':'原日内edge10','top_exit':'直接跟随卖价','gap':'买二及断档过滤',
        'reference':'自身参照过滤','cutoff':'14:55停止新开仓','combined':'组合过滤','combined_top':'组合＋跟随卖价',
        'depth_only':'仅要求买二可得','gap_when_observed':'仅过滤可见断档'}


def main():
    DEST.mkdir(exist_ok=True)
    data=read(OUT/'results.json');coverage=read(OUT/'coverage.json')
    source={k:unpack(OUT/f'{k}.json.gz') for k in data}
    audit=WORK/'reports/commodity_mau_depth_audit_20260913'
    extra=read(audit/'results.json');data.update(extra)
    source.update({k:unpack(audit/f'{k}.json.gz') for k in extra})
    for p in [OUT/'verification.json',OUT/'prefix_verification.json',audit/'verification.json']:
        assert read(p)['status']=='passed',p
    names={i['code']:i['name'] for i in read(WORK/'commodity_strategy/strategy_r2.json')['instruments']}
    diagnostics={}
    for key,r in source.items():
        market=[c for c in r['cycles'] if c.get('exit_kind')!='virtual_cost_close']
        positives=sorted([c['net_cents'] for c in market if c['net_cents']>0],reverse=True)
        reject=Counter()
        for a in r['accounts'].values():reject.update(a['summary']['rejection_frames'])
        diagnostics[key]=dict(normal_profitable_cycles=sum(c['net_cents']>0 for c in market),
            normal_losing_cycles=sum(c['net_cents']<0 for c in market),
            positive_cycle_net_cny=sum(max(0,c['net_cents']) for c in market)/100,
            negative_cycle_net_cny=sum(min(0,c['net_cents']) for c in market)/100,
            normal_net_without_best_three_cny=(sum(c['net_cents'] for c in market)-sum(positives[:3]))/100,
            early_net_cny=round(sum(d['pnl_cny'] for d in r['daily'] if d['date']<='20260904'),2),
            last_five_net_cny=round(sum(d['pnl_cny'] for d in r['daily'] if d['date']>='20260907'),2),
            traded_contracts=sum(a['summary']['fill_count']>0 for a in r['accounts'].values()),
            rejection_frames=dict(reject))
    dates=[d['date'] for d in data['control']['daily']]
    plt.rcParams['font.family']='Microsoft YaHei'
    plt.rcParams['axes.unicode_minus']=False
    fig,ax=plt.subplots(figsize=(13,6.4))
    for k,r in data.items():
        ax.plot(range(len(dates)+1),[0]+[d['cumulative_pnl_cny']/300000*100 for d in r['daily']],label=LABELS[k],linewidth=1.8)
    ax.axhline(0,color='#555',linewidth=.6)
    ax.set_xticks(range(1,len(dates)+1),[d[4:6]+'-'+d[6:] for d in dates],rotation=35)
    ax.set_ylabel('累计研究收益率 / %（本金30万元）')
    ax.set_title('mAu思路部分迁移 · 七组试验＋两组归因对照\n日末未平仓按成本虚拟退出，双边各1.70元；含全部64合约')
    ax.grid(alpha=.2);ax.legend(fontsize=9,ncol=3,loc='best');fig.tight_layout()
    chart=DEST/'黄金思路迁移日内收益曲线.png';fig.savefig(chart,dpi=160);plt.close(fig)
    fig,ax=plt.subplots(figsize=(13,5.6));x=np.arange(len(data));w=.25
    for off,field,label in [(-w,'market_cycle_net_cny','正常日内闭环净收益'),(0,'virtual_cycle_net_cny','虚拟闭环净收益（双边费用）'),(w,'quote_mark_pnl_before_virtual_close_cny','虚拟退出前买一浮盈亏')]:
        ax.bar(x+off,[r['summary'][field] for r in data.values()],w,label=label)
    ax.set_xticks(x,[LABELS[k] for k in data],rotation=15);ax.axhline(0,color='#555',lw=.6)
    ax.set_ylabel('人民币元');ax.set_title('闭环收益与尾仓原始浮盈亏分开核对');ax.legend(fontsize=9);ax.grid(axis='y',alpha=.2)
    fig.tight_layout();tails=DEST/'黄金思路迁移闭环与尾仓.png';fig.savefig(tails,dpi=160);plt.close(fig)
    payload=dict(models=data,labels=LABELS,names=names,dates=dates,coverage=coverage,diagnostics=diagnostics)
    cycle_paths={k:{(c['code'],c['entry_ts']):c for c in r['cycles']} for k,r in source.items()}
    base=cycle_paths['control'];attribution={}
    for key,path in cycle_paths.items():
        common=base.keys()&path.keys();removed=base.keys()-path.keys();added=path.keys()-base.keys()
        v=dict(same_entry_exit_net_change_cny=sum(path[c]['net_cents']-base[c]['net_cents'] for c in common)/100,
            removed_cycle_net_cny=sum(base[c]['net_cents'] for c in removed)/100,
            added_cycle_net_cny=sum(path[c]['net_cents'] for c in added)/100,
            same_entries=len(common),removed_entries=len(removed),added_entries=len(added))
        assert round((v['same_entry_exit_net_change_cny']-v['removed_cycle_net_cny']+v['added_cycle_net_cny'])*100)==round((data[key]['summary']['pnl_cny']-data['control']['summary']['pnl_cny'])*100)
        attribution[key]=v
    payload['cycle_path_attribution']=attribution
    write(DEST/'全部模型合约日度与诊断.json',payload)
    # Daily cash reconciliation, every observed contract-date retained as distinct from missing.
    for k,r in data.items():
        assert len(r['accounts'])==64
        for d in r['daily']:
            total=sum(round(v['pnl_cny']*100) for a in r['accounts'].values() for v in a['daily'] if v['date']==d['date'])
            assert total==round(d['pnl_cny']*100),(k,d)
        assert round(sum(d['pnl_cny'] for d in r['daily'])*100)==round(r['summary']['pnl_cny']*100)
    def money(x):return f'{x:,.2f}'
    overview=''
    for k,r in data.items():
        s=r['summary'];di=diagnostics[k]
        vals=[LABELS[k],money(s['pnl_cny']),f"{s['return_pct']:.4f}%",money(s['market_cycle_net_cny']),
            str(s['virtual_close_count']),money(s['virtual_roundtrip_fees_cny']),money(s['quote_mark_pnl_before_virtual_close_cny']),
            money(s['max_drawdown_cny']),money(di['early_net_cny']),money(di['last_five_net_cny']),str(di['traded_contracts'])]
        overview+='<tr>'+''.join(f'<td>{v}</td>' for v in vals)+'</tr>'
    daily=''
    for d in dates:
        daily+='<tr><td>'+d+'</td>'+''.join('<td>'+money(next(x['pnl_cny'] for x in r['daily'] if x['date']==d))+'</td>' for r in data.values())+'</tr>'
    def embed(p):return 'data:image/png;base64,'+base64.b64encode(p.read_bytes()).decode()
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>商品期权 · 黄金思路迁移日内回测</title><style>
body{font:15px/1.65 "Microsoft YaHei",sans-serif;color:#192738;background:#f5f7fa;margin:0}main{max-width:1540px;margin:auto;padding:32px}
h1{font-size:29px;margin:0 0 8px}h2{font-size:22px;margin-top:32px}p{max-width:1180px}.panel{background:white;padding:20px;border:1px solid #d9e2ec;border-radius:10px;margin-top:20px}.scroll{overflow:auto;max-height:650px}table{border-collapse:collapse;width:100%;white-space:nowrap;font-size:13px}td,th{padding:9px 11px;border-bottom:1px solid #e3e8ee;text-align:right}th{background:#eaf0f5;position:sticky;top:0}td:first-child,th:first-child{text-align:left;position:sticky;left:0;background:white}.pos{color:#a52928}.neg{color:#17765d}.note{color:#536579}select{font:inherit;padding:6px;margin:6px}img{width:100%;height:auto}a{color:#175bac}code{font-size:12px}footer{font-size:13px;color:#5f7080;margin-top:30px}
</style><main><h1>商品期权：借鉴黄金做市后的七组试验＋两组归因对照</h1>
<p class="note">2026-08-24—09-11 · 15个已查看交易日 · 64个期权 / 36品种 · 共享30万元 · 一合约最多1手 · 单边1.70元 · 额外下单延迟0毫秒</p>
<div class="panel"><b>收益口径</b><p>按用户约定，每日15:00将未平仓按该笔买入成本虚拟退出，毛盈亏归零、两侧手续费照扣，次日空仓，现金连续。这是研究结算，不能解释为市场可按成本平仓。正常日内闭环、虚拟退出费用和退出前最后买一浮盈亏分别展示。</p>
<p>参考固定mAu提交2908677：学习参照价优势、孤立买一过滤与被动出口。当前本地期货数据只覆盖32个期权对应标的的最近5日；本轮“自身参照”是期权历史中价，<b>没有实现跨市场公允估值</b>。买二可得947,600/2,914,415帧，缺失时gap组停止新开仓。</p></div>
<div class="panel"><b>结论：断档过滤改善了入场，但尚未证明可定版。</b><p>原模型−4,764.50元；仅要求买二可得−1,012.20元；仅过滤可见断档−2,826.80元；二者合用+925.50元（+0.3085%）。断档过滤在相同买二可得约束下改善1,937.70元，确实有研究价值；其余改善包含缺少买二而停止交易的市场选择效应。</p><p>盈利组前10日+1,062.30元，后5日−136.80元；剔除前三大盈利，正常闭环变为−5,626.90元。盘中最大回撤14,171.80元，虚拟退出前尾仓累计浮亏8,987.50元。它仍是需验证的研究候选，不能因总额转正就定为稳定可交易策略。</p></div>
<h2>总收益与风险</h2><div class="panel scroll"><table><thead><tr><th>版本</th><th>净收益/元</th><th>收益率</th><th>正常闭环净/元</th><th>虚拟退出笔</th><th>虚拟双边费/元</th><th>尾仓原浮盈亏/元</th><th>盘中最大回撤/元</th><th>前10日净/元</th><th>后5日净/元</th><th>有成交合约数</th></tr></thead><tbody>__OVERVIEW__</tbody></table></div>
<div class="panel"><img alt="七组累计收益率曲线" src="__CHART__"></div><div class="panel"><img alt="闭环与尾仓盈亏" src="__TAILS__"></div>
<h2>全部交易日的组合净收益</h2><div class="panel scroll"><table><thead><tr><th>日期</th>__DAILYHEAD__</tr></thead><tbody>__DAILY__</tbody></table></div>
<h2>所有品种、所有合约的日度明细</h2><p>负数仍显示，未成交日记0；没有原始输入的合约日记“缺失”。品种汇总是有数据合约的小计；有缺失合约时以＊标记，不能当作该品种完整覆盖。每组都保留64合约。</p>
<label>模型<select id="model"></select></label><label>层级<select id="level"><option value="product">全部36品种</option><option value="contract">全部64合约</option></select></label>
<div class="panel scroll"><table><thead id="detailhead"></thead><tbody id="detail"></tbody></table></div>
<h2>变化来自哪里</h2><p>下表为选中模型相对原edge10的合约净收益变化。买二过滤可能因为数据不全而停做某个市场；收益改善不能全归因于识别了孤立报价。稳定性诊断包含删去最赚钱三笔后的正常闭环收益，以及前后日期分段，均不属于新样本外验证。</p>
<div class="panel" id="diagnostic"></div><div class="panel scroll"><table><thead><tr><th>合约</th><th>品种</th><th>原模型净/元</th><th>本模型净/元</th><th>差额/元</th><th>正常闭环净/元</th><th>虚拟退出笔</th></tr></thead><tbody id="attribution"></tbody></table></div>
<h2>固定规则与核验</h2><p>原模型为edge10。直接跟随卖价仅取消原五分钟成本保护；gap要求有效买二，并过滤买一减买二≥max(2tick,当前价差)；reference用150/900秒自身中价EWMA及当前中价较低者，预热60秒/3帧，参照优势扣双边费后≥max(1tick,双边费)，10秒中价跌幅≥max(2tick,半价差)则暂停新买；cutoff于14:55起停止新买；两组组合按名称叠加。均在旧单先撮合之后判断新信号。</p>
<p>新增归因两组：“仅要求买二可得”允许可见孤立买一；“仅过滤可见断档”在买二缺失时继续沿用原规则。两组在观察首轮结果后固定，用于解释机制，不能包装为预先留出的验证组。</p><p>原模型经济路径精确一致；9组每帧资金/费用守恒，日末空仓；含日末结算的9月4日截断重跑通过。98项商品研究测试通过。首轮截断校验器直接比较内存tuple与JSON list曾失败，独立校验器统一JSON表示后保留所有经济和信号字段重跑通过，未修改冻结交易引擎。全部日期此前已经查看，本轮不能证明未来稳定盈利。L1末价推断成交尚未校准真实排队，报告没有声称实盘可得收益。</p>
<footer>原始逐笔、冻结合同、哈希、覆盖率及核验存于相邻commodity_mau_transfer_20260913目录。完整数据另见“全部模型合约日度与诊断.json”。旧模型及旧黄金+8,347.40元结果均保留。生成器：scripts/report_commodity_mau_transfer.py。</footer></main>
<script>const D=__DATA__;
const fmt=n=>Number(n).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
const cell=n=>n===null?'<td>缺失</td>':`<td class="${n>0?'pos':n<0?'neg':''}">${fmt(n)}</td>`;
const sel=document.getElementById('model');Object.keys(D.models).forEach(k=>sel.add(new Option(D.labels[k],k)));
function render(){let k=sel.value,r=D.models[k],product=document.getElementById('level').value==='product',groups={};
Object.entries(r.accounts).forEach(([code,a])=>{let name=product?D.names[code]:code;if(!groups[name])groups[name]=[];groups[name].push([code,a]);});
document.getElementById('detailhead').innerHTML='<tr><th>'+(product?'品种':'合约')+'</th><th>合计/元</th>'+D.dates.map(d=>'<th>'+d.slice(4,6)+'-'+d.slice(6)+'</th>').join('')+'</tr>';
document.getElementById('detail').innerHTML=Object.entries(groups).sort((a,b)=>a[0].localeCompare(b[0])).map(([name,arr])=>'<tr><td>'+name+'</td>'+cell(arr.reduce((t,[c,a])=>t+a.summary.pnl_cny,0))+D.dates.map(date=>{let rows=arr.map(([c,a])=>a.daily.find(x=>x.date===date)),known=rows.filter(Boolean);if(!known.length)return cell(null);let val=known.reduce((t,d)=>t+d.pnl_cny,0),s=cell(val);return known.length<rows.length?s.replace('</td>','＊</td>'):s;}).join('')+'</tr>').join('');
document.getElementById('attribution').innerHTML=Object.entries(r.accounts).sort((a,b)=>(b[1].summary.pnl_cny-D.models.control.accounts[b[0]].summary.pnl_cny)-(a[1].summary.pnl_cny-D.models.control.accounts[a[0]].summary.pnl_cny)).map(([c,a])=>{let s=a.summary,b=D.models.control.accounts[c].summary;return '<tr><td>'+c+'</td><td>'+D.names[c]+'</td>'+cell(b.pnl_cny)+cell(s.pnl_cny)+cell(s.pnl_cny-b.pnl_cny)+cell(s.market_cycle_net_cny)+'<td>'+s.virtual_close_count+'</td></tr>';}).join('');
let q=D.diagnostics[k];document.getElementById('diagnostic').innerHTML=`<b>${D.labels[k]}</b>：正常盈利闭环${q.normal_profitable_cycles}笔，亏损${q.normal_losing_cycles}笔；盈利合计${fmt(q.positive_cycle_net_cny)}元，亏损合计${fmt(q.negative_cycle_net_cny)}元。剔除前三大盈利后，正常闭环净${fmt(q.normal_net_without_best_three_cny)}元。<br>买二缺失拒绝${q.rejection_frames.bid2_unavailable||0}帧；实际孤立买一拒绝${q.rejection_frames.isolated_bid1||0}帧；自身参照预热拒绝${q.rejection_frames.own_reference_warmup||0}帧；参照优势不足${q.rejection_frames.own_reference_net_edge||0}帧；10秒逆向下跌${q.rejection_frames.own_adverse_10s||0}帧。拒绝帧数含重复判断，不等于独立交易机会数。`;}
sel.onchange=render;document.getElementById('level').onchange=render;render();</script></html>'''
    page=page.replace('__OVERVIEW__',overview).replace('__DAILYHEAD__',''.join('<th>'+LABELS[k]+'</th>' for k in data)).replace('__DAILY__',daily).replace('__CHART__',embed(chart)).replace('__TAILS__',embed(tails)).replace('__DATA__',json.dumps(payload,ensure_ascii=False,allow_nan=False).replace('</','<\\/'))
    target=DEST/'黄金思路迁移完整日内报告.html';target.write_text(page,'utf8')
    assert len(dates)==15 and len(names)==64 and len(set(names.values()))==36
    write(DEST/'report_qa.json',dict(status='passed',models=len(data),contracts=64,products=36,days=15,
        model_contract_observed_days={k:sum(len(a['daily']) for a in r['accounts'].values()) for k,r in data.items()},
        sums_reconcile=True,missing_preserved=True,browser_screenshot_qa=False))
    write(DEST/'artifact_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in DEST.glob('*') if p.name!='artifact_manifest.json'})
    print(target)


if __name__=='__main__':main()
