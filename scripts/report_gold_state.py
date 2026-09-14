"""Deliver fixed state filters, two selected contracts and full latest-day ledgers."""
from pathlib import Path
import json
import base64
import html
from datetime import datetime,timezone,timedelta
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from probe_gold_state import OUT as BASE
from probe_commodity_capital import ROOT,WORK,read,write,unpack,digest

OUT=WORK/'reports/gold_state_report_20260913'
TZ=timezone(timedelta(hours=8))


def aggregate(rows):
    arrays=[]
    for r in rows:
        a=np.asarray(r['curve'],dtype=np.int64)
        arrays.append(np.column_stack((a[:,0],np.diff(a[:,1],prepend=0))))
    a=np.concatenate(arrays);a=a[np.argsort(a[:,0],kind='stable')]
    ts,idx=np.unique(a[:,0],return_index=True);pnl=np.cumsum(np.add.reduceat(a[:,1],idx))
    peak=np.maximum.accumulate(np.maximum(30000000+pnl,30000000));dd=peak-30000000-pnl
    return dict(curve=np.column_stack((ts,pnl)).tolist(),pnl_cny=float(pnl[-1]/100),
        return_pct=float(pnl[-1]/30000000*100),max_drawdown_cny=float(dd.max()/100),
        max_drawdown_pct=float(np.max(dd/peak*100)),initial_cash_cny=300000)


def main():
    OUT.mkdir(exist_ok=True)
    assert read(BASE/'verification.json')['status']=='passed'
    audit=read(WORK/'reports/gold_state_universe_audit_20260913/result.json');assert audit['selection_unchanged']
    selection=read(BASE/'selection.json');codes=selection['selected'];rows={};metrics={}
    for code in codes:
        for profile in ['gap','control']:
            key=code+'_'+profile;r=unpack(BASE/f'{key}.json.gz');rows[key]=r
            cycles=r['cycles'];pos=[c['net_cents']/100 for c in cycles if c['net_cents']>0];neg=[c['net_cents']/100 for c in cycles if c['net_cents']<0]
            s=r['summary'];metrics[key]=dict(**s,return_pct=s['pnl_cny']/150000*100,
                win_rate_pct=len(pos)/len(cycles)*100 if cycles else 0,
                mean_win_cny=float(np.mean(pos)) if pos else 0,mean_loss_cny=float(np.mean(neg)) if neg else 0,
                largest_win_cny=max(pos) if pos else 0,largest_loss_cny=min(neg) if neg else 0,
                without_best_three_cny=round(s['pnl_cny']-sum(sorted(pos,reverse=True)[:3]),2),
                median_duration_seconds=float(np.median([c['duration_seconds'] for c in cycles])) if cycles else 0,
                longest_duration_seconds=max((c['duration_seconds'] for c in cycles),default=0))
    portfolios={p:aggregate([rows[c+'_'+p] for c in codes]) for p in ['gap','control']}
    for p,a in portfolios.items():assert round(a['pnl_cny']*100)==sum(round(rows[c+'_'+p]['summary']['pnl_cny']*100) for c in codes)
    plt.rcParams['font.family']='Microsoft YaHei';plt.rcParams['axes.unicode_minus']=False
    fig,ax=plt.subplots(figsize=(12.8,6))
    for c in codes:
        a=rows[c+'_gap']['curve'];ax.step([datetime.fromtimestamp(x[0]/1000,TZ) for x in a],[x[1]/100 for x in a],label=c+' · 现有策略',linewidth=1.4,where='post')
    for p in ['gap','control']:
        a=portfolios[p]['curve'];ax.step([datetime.fromtimestamp(x[0]/1000,TZ) for x in a],[x[1]/100 for x in a],
            label='合计 · '+('现有断档过滤' if p=='gap' else '不加断档过滤对照'),linewidth=2,linestyle='-' if p=='gap' else '--',where='post')
    ax.axhline(0,color='#666',lw=.7);ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M',tz=TZ))
    ax.set_title('黄金状态筛选后：09:30—15:00净值路径\n2026-09-11 · 首30分钟选约 · 单边1.70元 · 每合约最多1手')
    ax.set_ylabel('累计净盈亏 / 元（持仓按最后有效买一估值）');ax.set_xlabel('北京时间；无新报价时沿用最后估值，休市不插值')
    ax.grid(alpha=.2);ax.legend(fontsize=9,ncol=2);fig.tight_layout()
    chart=OUT/'两份黄金期权日内净值曲线.png';fig.savefig(chart,dpi=160);plt.close(fig)
    payload=dict(date='20260911',selection=selection,full_universe_audit=audit,metrics=metrics,
        portfolios=portfolios,ledgers=rows,plan=read(BASE/'plan.json'),months=read(BASE/'month_selection.json'))
    write(OUT/'全部筛选与逐笔回测.json',payload)
    fmt=lambda x:f'{x:,.2f}'
    overview=''
    for code in codes:
        m=selection['rows'][code];overview+='<tr>'+''.join('<td>'+str(v)+'</td>' for v in [code,'十月 / 次主力',
            m['days_to_expiry'],'虚值',fmt(m['underlying_mid']),fmt(m['strike']),fmt(m['median_premium']),
            m['volume_increment'],f"{m['strict_buy_updates']}/{m['strict_sell_updates']}",
            f"{m['median_spread']/.02:.0f}",fmt(m['median_net_edge_cny']),f"{m['net_edge_time_fraction']*100:.1f}%"] )+'</tr>'
    performance=''
    for code in codes:
        for profile in ['gap','control']:
            m=metrics[code+'_'+profile];vals=[code,'现有策略' if profile=='gap' else '无断档过滤对照',
                fmt(m['realized_gross_cny']),fmt(m['fees_cny']),fmt(m['pnl_cny']),f"{m['return_pct']:.4f}%",
                m['market_closed_cycles'],f"{m['win_rate_pct']:.1f}%",fmt(m['max_drawdown_cny']),m['virtual_close_count'],fmt(m['quote_mark_pnl_before_virtual_close_cny'])]
            performance+='<tr>'+''.join('<td>'+str(v)+'</td>' for v in vals)+'</tr>'
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>黄金期权状态筛选与双合约首日回测</title>
<style>body{font:15px/1.7 "Microsoft YaHei",sans-serif;background:#f3f6fa;color:#172a3b;margin:0}main{max-width:1480px;margin:auto;padding:32px}h1{font-size:28px;margin:0}h2{font-size:21px;margin-top:30px}.panel{background:white;border:1px solid #dce4ed;padding:20px;margin-top:20px;border-radius:10px}.scroll{overflow:auto;max-height:600px}table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap}td,th{padding:9px 12px;text-align:right;border-bottom:1px solid #e3e9f0}th{background:#eaf0f7;position:sticky;top:0}td:first-child,th:first-child{text-align:left}p{max-width:1200px}.note{color:#596c7e}img{width:100%}select{font:inherit;padding:6px;margin:8px}.positive{color:#ac2929}.negative{color:#168167}code{font-size:12px}</style>
<main><h1>黄金期权：状态筛选与双合约首日回测</h1><p class="note">2026年9月11日日盘 · 09:00—09:30筛选，09:30—15:00回放 · 每合约15万元独立现金、每组共30万元 · 最大各1手</p>
<div class="panel"><b>先选约，后看收益。</b><p>主策略选出au2610C960与au2610C952，净收益分别−182.80元、+965.60元，合计<b>+782.80元</b>，组合收益率0.2609%，盘中最大回撤__PORTFOLIODD__元。258笔均为正常日内闭环，无日末虚拟退出，也没有尾仓浮亏被成本结算抹去。无断档过滤对照合计+902.40元，保留该结果，不据当日利润切换策略。</p><p>只用最新一个已结束日盘，未回看旧日期挑参数。仍为开发样本：当前目录不等于完整历史上市目录，两份收益也不能证明以后稳定。L1末价证据、零额外延迟和未显式模拟排队的假设仍在。</p></div>
<h2>筛选怎样构建</h2><div class="panel"><ol><li>从当前目录776份沪金期权代码检查，使用合约上市/到期字段限制样本日期。用09:30之前的标的期货持仓量排序：十二月主力、十月次主力。</li><li>保留这两个月、绝对log(F/K)≤0.08的合约，共74份；核验到期剩余7—120天、权利金中位数2—100元。期限和价格门槛为本轮固定研究假设，不是永久最佳参数。</li><li>首30分钟有效连续双边报价覆盖≥80%，有效买二时长≥80%；扣除两侧改善一跳与双边3.40元费用后，满足原策略最低空间的时间≥50%；相对价差中位数≤5%。</li><li>首30分钟可观察成交增量≥10手、成交增量快照≥10次，严格推断买卖两侧各≥3次。静止报价仍可有效，未把报价不变当作无效。</li><li>通过后按较少一侧的成交更新次数降序，再按可做价差时间比例、单位权利金净空间、代码依次排序，固定取前两个。排序不用回测利润。目录中原先缺元数据的8份已补查，选择结果不变。</li></ol><p>认购实虚值用log(F/K)，认沽符号相反；|log(F/K)|≤1%记近平值。这里先用期限和实虚值状态筛选，没有声称已经实现IV估计或delta对冲。两份恰好均为十月认购，相邻行权价风险相关。</p></div>
<h2>09:30时选中的两个状态</h2><div class="panel scroll"><table><thead><tr><th>合约</th><th>月份状态</th><th>距到期/天</th><th>实虚值</th><th>标的期货中价</th><th>行权价</th><th>权利金中位数</th><th>首30分钟量/手</th><th>买/卖更新次数</th><th>价差中位/跳</th><th>静态扣费空间/元</th><th>可做价差时间</th></tr></thead><tbody>__OVERVIEW__</tbody></table></div>
<p class="note">截图关注的au2612C1000也通过：首30分钟129手，36/45次双侧更新，净空间中位216.60元；按预先规定的成交机会优先排序列第12，本轮只取前两名。它仍保留在候选表，没有因收益未知而删除。十月au2610C1000因大部分时段空间不足未通过。</p>
<h2>实际回放结果</h2><div class="panel scroll"><table><thead><tr><th>合约</th><th>版本</th><th>毛收益/元</th><th>费用/元</th><th>净收益/元</th><th>收益率</th><th>正常闭环</th><th>胜率</th><th>盘中最大回撤/元</th><th>虚拟退出</th><th>退出前尾仓浮盈亏/元</th></tr></thead><tbody>__PERFORMANCE__</tbody></table></div>
<div class="panel"><img alt="两合约和对照组合净值路径" src="__CHART__"></div>
<div class="panel"><b>第一轮已看到的差异</b><p>C960毛收益300元，手续费482.80元，净亏182.80元；113笔盈利平均46.87元，29笔亏损平均−188.92元，高胜率仍无法保证净赚。C952毛收益1360元，费用394.40元，净赚965.60元；但最大一笔净赚1476.60元，删去该笔后为负，仍要研究收益集中性。这里先保留原有策略，没有对其中一个单独调参。</p><p>最长持仓包含午间休市，时长按自然时钟计算；C952最长7213秒。单日频繁成交并不意味着库存风险已解决。以下逐笔表可核查进出时间、利润和挂单价格。</p></div>
<h2>全部初筛合约与拒绝原因</h2><div class="panel scroll"><table><thead><tr><th>合约</th><th>通过</th><th>量增/手</th><th>买/卖更新</th><th>权利金中位</th><th>净空间/元</th><th>可做价差时间</th><th>拒绝原因</th></tr></thead><tbody id="candidates"></tbody></table></div>
<h2>逐笔交易与委托证据</h2><label>合约<select id="code"></select></label><label>版本<select id="profile"><option value="gap">现有断档过滤策略</option><option value="control">无断档过滤对照</option></select></label><div class="panel scroll"><table><thead><tr><th>序号</th><th>买入时间</th><th>卖出时间</th><th>买入报价</th><th>卖出报价</th><th>持仓秒</th><th>毛利润/元</th><th>费用/元</th><th>净利润/元</th><th>退出类型</th></tr></thead><tbody id="cycles"></tbody></table></div>
<h2>成交与核验口径</h2><p>交易继承现有规则：最近5分钟推断双侧成交、足够扣费空间、有效买二与孤立买一过滤；买一改善一跳买入，卖一改善一跳退出，保留有限5分钟成本保护及原释放条件。仅之前已挂出的单可被后到达的成交证据撮合，每帧最多用末笔价格的一手证据；新行情先撮合旧单再判断新挂单。没有主动卖空。</p><p>15:00尾仓按用户要求以入场成本虚拟退出、双边各扣1.70元；本次四份账户实际没有触发该规则。首30分钟未交易，选择时已结束；删除09:30之后所有输入，选择结果保持。四账户独立现金/手续费重建、日末库存归零、13:00截断重跑通过。98项既有商品研究测试＋6项新增状态筛选测试通过。完整原始数据、拒绝原因、逐笔和源码哈希另存本地。</p><p class="note">数据下载读取config中的58610端口。使用QMT只读行情，没有导入或调用券商交易接口。主图限定日盘，与此前截图“自然日全天含夜盘”统计范围不同。所有旧策略和结果保持。</p></main>
<script>const D=__DATA__;const $=id=>document.getElementById(id);const fmt=n=>n===undefined||n===null||!Number.isFinite(Number(n))?'—':Number(n).toLocaleString('zh-CN',{maximumFractionDigits:2,minimumFractionDigits:2});const time=t=>new Date(t).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false});
const reasons={outside_top_two_future_months:'非主/次主力月',deep_moneyness:'实虚值距离过大',premium_outside_band:'权利金超范围',quote_coverage:'连续报价覆盖不足',bid2_coverage:'买二覆盖不足',few_net_edge_intervals:'净空间达标时间不足',excessive_relative_spread:'相对价差过宽',few_contracts_traded:'量增不足',few_trade_updates:'成交更新不足',insufficient_two_sided_flow:'双向成交不足',short_or_long_expiry:'期限超范围',no_recent_option_quote:'边界报价不足',data_unavailable:'行情缺失',volume_reset:'累计量重置',no_valid_weight:'无有效报价时长',insufficient_prefix_data:'前30分钟数据不足'};
const ranked=D.full_universe_audit.ranked;const cs=Object.keys(D.full_universe_audit.rows).sort((a,b)=>{let ai=ranked.indexOf(a),bi=ranked.indexOf(b);return (ai<0?10000:ai)-(bi<0?10000:bi)||a.localeCompare(b);});
$('candidates').innerHTML=cs.map(c=>{let m=D.full_universe_audit.rows[c];return '<tr><td>'+c+'</td><td>'+(m.eligible?'通过':'未通过')+'</td><td>'+fmt(m.volume_increment)+'</td><td>'+fmt(m.strict_buy_updates)+' / '+fmt(m.strict_sell_updates)+'</td><td>'+fmt(m.median_premium)+'</td><td>'+fmt(m.median_net_edge_cny)+'</td><td>'+fmt(m.net_edge_time_fraction*100)+'%</td><td>'+m.reasons.map(r=>reasons[r]||r).join('、')+'</td></tr>';}).join('');
D.selection.selected.forEach(c=>$('code').add(new Option(c,c)));
function render(){let r=D.ledgers[$('code').value+'_'+$('profile').value];$('cycles').innerHTML=r.cycles.map((c,i)=>'<tr><td>'+(i+1)+'</td><td>'+time(c.entry_ts)+'</td><td>'+time(c.exit_ts)+'</td><td>'+fmt(c.entry_price_cents/100000)+'</td><td>'+fmt((c.entry_price_cents+c.gross_cents)/100000)+'</td><td>'+fmt(c.duration_seconds)+'</td><td>'+fmt(c.gross_cents/100)+'</td><td>'+fmt(c.fees_cents/100)+'</td><td class="'+(c.net_cents>0?'positive':'negative')+'">'+fmt(c.net_cents/100)+'</td><td>'+(c.exit_kind==='virtual_cost_close'?'成本虚拟退出':'正常被动成交')+'</td></tr>').join('');}
$('code').onchange=render;$('profile').onchange=render;render();</script></html>'''
    page=page.replace('__OVERVIEW__',overview).replace('__PERFORMANCE__',performance).replace('__PORTFOLIODD__',fmt(portfolios['gap']['max_drawdown_cny'])).replace('__CHART__','data:image/png;base64,'+base64.b64encode(chart.read_bytes()).decode()).replace('__DATA__',json.dumps(payload,ensure_ascii=False,allow_nan=False).replace('</','<\\/'))
    target=OUT/'黄金状态筛选与双合约首日回测.html';target.write_text(page,'utf8')
    write(OUT/'report_qa.json',dict(status='passed',accounts=4,candidate_rows=len(audit['rows']),
        source_universe=776,cycles={k:len(r['cycles']) for k,r in rows.items()},all_sums_reconcile=True,
        selected_match_full_catalogue_audit=True,browser_screenshot_qa=False))
    write(OUT/'artifact_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='artifact_manifest.json'})
    print(target)


if __name__=='__main__':main()
