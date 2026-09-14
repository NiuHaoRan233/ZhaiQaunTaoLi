"""Auditable daily comparison, failures retained, no ranking by partial totals."""
from pathlib import Path
from collections import Counter
from html import escape
import base64
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from validate_gold_history import CASES
from capture_gold_history import OUT,DATES,FIXED
from probe_commodity_capital import ROOT,read,write,unpack,digest
from probe_gold_rule_ladder import portfolio

FOCUS=['ladder_s00_long','ladder_s00_short','ladder_spread8_long','ladder_spread8_short',
       'ladder_s10_long','simple_core_long','fusion_value_long','fusion_throttle_long',
       'fusion_trend_long','fusion_cancel_switch','v02_trend_long','v02_cancel_switch']


def combine(days):
    complete=all(d['status']=='complete' for d in days)
    curve=[];offset=0
    for day in days:
        if day['status']!='complete':break
        curve.extend([[t,p+offset] for t,p in day['curve']]);offset=curve[-1][1]
    dd=max((max(0,max(p for _,p in curve[:i+1]))-v for i,(_,v) in enumerate(curve)),default=0) if len(curve)<100 else None
    if curve:
        vals=np.array(curve)[:,1];dd=int((np.maximum.accumulate(np.maximum(vals,0))-vals).max())
    return dict(status='complete' if complete else 'incomplete',pnl_cny=sum(round(d['pnl_cny']*100) for d in days)/100 if complete else None,
        max_drawdown_cny=dd/100 if complete and dd is not None else None,
        cycles=sum(d['cycles'] for d in days) if complete else None,curve=curve,
        profitable_days=sum(d['pnl_cny']>0 for d in days if d['status']=='complete'),
        completed_days=sum(d['status']=='complete' for d in days),
        order_count=sum(d['order_count'] for d in days),reprice_cancel_count=sum(d['reprice_cancel_count'] for d in days),
        fees_cny=sum(round(d['fees_cny']*100) for d in days)/100,days=days)


def aggregate():
    ports={};accounts={};failure=[]
    for date in DATES:
        summaries=read(OUT/date/'results.json');accounts[date]=summaries
        assert len(summaries)==len(CASES)*4
        for key in CASES:
            for through in (False,True):
                rs=[unpack(OUT/date/'ledgers'/f'{code}_{key}_through{int(through)}.json.gz') for code in FIXED]
                ss=[r['summary'] for r in rs]
                good=all(s['status']=='complete' for s in ss)
                day=portfolio(rs) if good else dict(pnl_cny=None,max_drawdown_cny=None,cycles=sum(s['complete_cycles'] for s in ss),fees_cny=sum(s['fees_cny'] for s in ss),curve=[])
                day.update(date=date,status='complete' if good else 'failed_flatten',
                    codes=list(FIXED),order_count=sum(s['order_count'] for s in ss),
                    reprice_cancel_count=sum(s['reprice_cancel_count'] for s in ss))
                for s in ss:
                    if s['status']!='complete':failure.append(s)
                ports.setdefault(f'{key}_through{int(through)}',[]).append(day)
    totals={k:combine(v) for k,v in ports.items()}
    write(OUT/'portfolios.json',totals)
    write(OUT/'all_account_summaries.json',accounts)
    write(OUT/'flatten_failures.json',failure)
    small=read(OUT/'small_capital/results.json');small_ports={}
    for mode in ('daily_reset','carry_cash'):
        for through in (False,True):
            ds=[]
            for date in DATES:
                names=[f'{date}_{c}_{mode}_through{int(through)}' for c in FIXED]
                good=all(small[n]['status']=='complete' for n in names)
                if good:
                    rs=[unpack(OUT/'small_capital'/f'{n}.json.gz') for n in names]
                    d=portfolio(rs)
                else:d=dict(pnl_cny=None,max_drawdown_cny=None,cycles=0,fees_cny=0,curve=[])
                d.update(date=date,status='complete' if good else 'failed_or_blocked',
                    order_count=sum(small[n].get('order_count',0) for n in names),
                    reprice_cancel_count=sum(small[n].get('reprice_cancel_count',0) for n in names))
                ds.append(d)
            small_ports[f'{mode}_through{int(through)}']=combine(ds)
    write(OUT/'small_capital/portfolios.json',small_ports)
    return totals,accounts,failure,small_ports


def fmt(v):return '—' if v is None else f'{v:,.2f}'
def cell(v):return f'<td class="{"pos" if v is not None and v>0 else "neg" if v is not None and v<0 else ""}">{fmt(v)}</td>'


def chart(totals,small):
    plt.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei','DejaVu Sans'];plt.rcParams['axes.unicode_minus']=False
    fig,axes=plt.subplots(2,1,figsize=(13,8.8),constrained_layout=True)
    colors=plt.get_cmap('tab10')
    selected=['ladder_spread8_long','ladder_s10_long','fusion_throttle_long','v02_trend_long','v02_cancel_switch']
    # Market-time curves compressed by day, preserving every intraday PnL point.
    from zhaiquant.gold_history_validation import timestamp
    for index,key in enumerate(selected):
        p=totals[key+'_through0'];curve=p['curve']
        if not curve:continue
        xs=[]
        for ts,v in curve:
            date=__import__('datetime').datetime.fromtimestamp(ts/1000,__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d')
            xs.append(DATES.index(date)+(ts-timestamp(date))/21600000)
        ys=np.array([v for _,v in curve])/100
        label=CASES[key]['label']+('（到首次失败前）' if p['status']!='complete' else '')
        axes[0].step(xs,ys,where='post',label=label,color=colors(index),linewidth=1.6)
        axes[1].step(xs,np.maximum.accumulate(np.maximum(ys,0))-ys,where='post',color=colors(index),linewidth=1.4)
    axes[0].set_title('前四交易日：原参数历史验证（每合约一手，普通成交）',loc='left',fontweight='bold')
    axes[0].set_ylabel('累计净收益 / 元');axes[1].set_ylabel('从此前峰值回撤 / 元')
    axes[1].set_xlabel('交易日（日内时间压缩；清仓失败后不拼接累计线）')
    for ax in axes:
        ax.axhline(0,color='#64748b',linewidth=.7);ax.grid(alpha=.18)
        ax.set_xticks([0,1,2,3,4],['9月7日开盘','9月8日开盘','9月9日开盘','9月10日开盘','9月10日收盘'])
    axes[0].legend(fontsize=9,loc='best')
    path=OUT/'黄金历史对比_收益与回撤.png';fig.savefig(path,dpi=150);plt.close(fig)
    return path


def make_report(totals,accounts,failures,small):
    pic=chart(totals,small)
    style='''body{font-family:"Microsoft YaHei",sans-serif;background:#f3f5f9;color:#19243b;margin:0}main{max-width:1500px;margin:auto;padding:32px}h1{font-size:28px}h2{margin-top:32px}.card{background:white;border:1px solid #dbe1eb;border-radius:10px;padding:20px;margin:18px 0}.note{color:#52627b;font-size:14px;line-height:1.8}.neg{color:#ad2831}.pos{color:#08745f}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid #e0e5ed;padding:9px;text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}thead{background:#eaf0fa;position:sticky;top:0}.scroll{overflow:auto;max-height:650px}summary{cursor:pointer;padding:12px;background:#eaf0fa;margin-top:9px}img{width:100%}a{color:#2159a6}.badge{padding:5px 10px;border-radius:5px;background:#eaf0fa}input{padding:10px;width:420px;max-width:90%;margin:12px 0}'''
    main=totals['v02_trend_long_through0'];enhanced=totals['v02_cancel_switch_through0']
    small_normal=small['carry_cash_through0'];small_strict=small['carry_cash_through1']
    html=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>黄金期权：前四交易日全部策略对比</title><style>',style,'</style><main><h1>黄金期权：前四交易日全部策略对比</h1>',
        '<p class="note">2026年9月7日至10日 · 74个结构版本 × 双合约 × 4日 × 2种成交假设 = 1,184个账户回放。它们共享行情，不能当成1,184份独立统计样本。</p>',
        f'<div class="card"><b>结论：0.2的单日盈利尚未稳定延续，不能据此认为方法已经有效。</b><p>相同25万元/合约研究资金下，0.2主版四日净收益<b>{fmt(main["pnl_cny"])}元</b>，增强版<b>{fmt(enhanced["pnl_cny"])}元</b>。4万元实际资金滚存的主版为<b>{fmt(small_normal["pnl_cny"])}元</b>，严格穿价为<b>{fmt(small_strict["pnl_cny"])}元</b>。</p><p>小资金与大资金的差异来自是否有现金挂出一手报价：权利金较贵时小资金会跳过交易。少参与避开了部分亏损，不等于原策略在同样交易机会下更稳健。当前应把资金约束、报价成交假设和清仓失败分别看清，再讨论优化。</p></div>',
        '<div class="card"><b>相对值得保留：8跳＋估值多头＋有限成本保护＋旧节流。</b><p>该已有组合（fusion_throttle_long）普通成交四日+761.20元、严格+425.00元，是本轮唯一两种成交口径完整四日都为正的结构版本。普通最大回撤2781.10元，前两日−968.80/−378.20元，后两日+503.00/+1605.20元；82/75笔闭环。它不含期货趋势门槛，也不含期货先动撤单，且旧节流会在原单仍满足估值优势时同时限制小幅追价和退价，不等同0.2只限追价。</p><p>这只是74版比较后的事后候选，不能把它再包装成独立验证的胜者，也不能把四日利润直接除以4万元。普通组仅51版完整四日，其中2版盈利；另一个估值多头+657.80元在严格组变为−155.40元。</p></div>',
        '<div class="card"><b>验证方法：冻结原规则，展示全部结果。</b><p>每日09:00—09:30筛选均选中au2610C960与au2610C952，故每日重选与固定原双合约的结果相同。没有按收益换合约，也没有用这四天重新挑参数。9月11日是已见开发日，不计入下面历史合计。</p>',
        '<p>单侧手续费1.70元，每份一手，额外下单延迟0；休市前5秒按有效对手报价平仓、计实际盈亏。普通成交需要旧单在聚合区间前已有效、推断对向成交及价格可达；严格组额外要求末价穿过挂单价。两组都不是真实交易所队列。</p>',
        '<p>结构比较统一25万元/合约，每天重置研究现金，只用于放开资金约束比较交易规则。累计曲线是每日净损益相加，不能解释成实际需要50万元。0.2主版另有两份2万元、现金逐日滚存的真实资金约束回放。</p>',
        f'<p class="neg">清仓失败账户：{len(failures)}份。C952在9月8日和9月9日的10:15前最后5秒没有行情更新；若当时持仓，保留失败和浮亏证据。失败日收益及含失败日的四日总收益显示“—”，不以部分收益替代。</p></div>']
    def table(keys,through,title):
        out=[f'<h2>{title}</h2><div class="scroll"><table><thead><tr><th>策略</th>']
        out += [f'<th>{d[4:6]}-{d[6:]}</th>' for d in DATES]
        out += ['<th>四日合计/元</th><th>全程最大回撤/元</th><th>完整交易日</th><th>闭环笔数</th><th>委托数*</th><th>改价撤单*</th></tr></thead><tbody>']
        for key in keys:
            p=totals[f'{key}_through{through}']
            out += [f'<tr data-case="{key}" data-through="{through}"><td title="{key}">{escape(CASES[key]["label"])}</td>']
            out += [cell(d['pnl_cny']) for d in p['days']]
            out += [cell(p['pnl_cny']),f'<td>{fmt(p["max_drawdown_cny"])}</td><td>{p["completed_days"]}/4</td><td>{p["cycles"] if p["cycles"] is not None else "—"}</td><td>{p["order_count"]}</td><td>{p["reprice_cancel_count"]}</td></tr>']
        out.append('</tbody></table></div>');return ''.join(out)
    html.append(table(FOCUS,0,'主要版本：普通成交，双合约每日合计'))
    html.append(table(FOCUS,1,'同样版本：严格穿价对照'))
    html += [f'<div class="card"><img alt="历史收益与回撤" src="data:image/png;base64,{base64.b64encode(pic.read_bytes()).decode()}"></div>',
        '<h2>0.2主版：4万元资金约束实跑</h2><p class="note">两份独立2万元资金槽。滚存版每天沿用前日实际收盘现金；对照版每日重置2万元。资金不足就少挂或不挂，不追加资金或改选便宜赢家。</p><table><thead><tr><th>资金/成交口径</th><th>9月7日</th><th>9月8日</th><th>9月9日</th><th>9月10日</th><th>合计/元</th><th>收益/初始4万元</th><th>最大回撤/元</th></tr></thead><tbody>']
    for mode in ('carry_cash','daily_reset'):
        for through in (0,1):
            p=small[f'{mode}_through{through}']
            html.append(f'<tr><td>{"现金滚存" if mode=="carry_cash" else "每日重置"} / {"严格" if through else "普通"}</td>')
            html.extend(cell(d['pnl_cny']) for d in p['days'])
            html += [cell(p['pnl_cny']),f'<td>{fmt(p["pnl_cny"]/400 if p["pnl_cny"] is not None else None)}%</td><td>{fmt(p["max_drawdown_cny"])}</td></tr>']
    html += ['</tbody></table>',table(list(CASES),0,'全部74个结构版本：普通成交'),table(list(CASES),1,'全部74个结构版本：严格穿价'),
        '<p class="note">*失败账户的委托/改价/费用仅统计停止前实际观察到的部分，不代表完整四日工作量。相同的6组纯价差简化重复实现只保留一份；fusion的纯多/纯空保留作为祖先等价对照。邻近延迟扫描未在这里重新寻优。</p>',
        '<h2>每个策略、每日、每张合约明细</h2><input id="filter" placeholder="搜索策略名或编号，例如 v02、s00、spread8"><div id="details">']
    for key,cfg in CASES.items():
        html += [f'<details data-search="{escape(key+cfg["label"])}"><summary>{escape(cfg["label"])} · {key}</summary>',
            '<div class="scroll"><table><thead><tr><th>日期 / 合约</th><th>普通净收益</th><th>普通回撤</th><th>普通闭环</th><th>普通改单</th><th>严格净收益</th><th>严格回撤</th><th>严格闭环</th><th>状态</th></tr></thead><tbody>']
        for date in DATES:
            for code in FIXED:
                a,b=[accounts[date][f'{code}_{key}_through{t}'] for t in (0,1)]
                state='完整' if a['status']==b['status']=='complete' else '失败：见停止前盯市'
                html += [f'<tr><td>{date[4:6]}-{date[6:]} / {code}</td>',cell(a['pnl_cny']),f'<td>{fmt(a["max_drawdown_cny"])}</td><td>{a["complete_cycles"]}</td><td>{a["reprice_cancel_count"]}</td>',
                    cell(b['pnl_cny']),f'<td>{fmt(b["max_drawdown_cny"])}</td><td>{b["complete_cycles"]}</td><td>{state}</td></tr>']
        html += ['</tbody></table></div></details>']
    html += ['</div><h2>清仓失败：原始风险保留</h2><p class="note">以下是停止前最后可用盘口的盯市值，可能已过时，不能当作边界可成交价格或当日收益。仍未平的仓位使当前版本不满足休市零敞口要求。</p><div class="scroll"><table><thead><tr><th>日期 / 合约 / 策略</th><th>成交假设</th><th>未平手数</th><th>停止前盯市净值变化/元</th></tr></thead><tbody>']
    for s in failures:
        html.append(f'<tr><td>{s["history_date"]} / {s["code"]} / {s["case_key"]}</td><td>{"严格" if s["strict_through"] else "普通"}</td><td>{s["end_inventory"]}</td><td>{fmt(s["observed_pnl_cny"])}</td></tr>')
    html += ['</tbody></table></div><h2>选约与验证范围</h2><p class="note">主次月按当日09:30前持仓量与成交增量排序，四天均为au2610、au2612。原状态筛选：7—120天到期、绝对对数价内外程度≤8%、权利金中位数2—100元/克、有效报价覆盖≥80%、买二覆盖≥80%、净空间合格时间≥50%、相对价差中位数≤5%、至少10手及10次成交更新、严格推断两侧至少各3次，按双向成交机会、净空间覆盖及比例排序。筛选只使用第一30分钟，完整下载后的前缀重算一致。选约不是全天8跳过滤，后者在交易时实时判断。</p>',
        '<p class="note">仅黄金认购、日盘、当前目录中历史已上市的合约；完整历史退市目录、夜盘、真实队列、未来前向数据均未覆盖。这四天是固定0.2后的历史外推，但项目早期可能看过部分行情，不能声称全项目完全未见样本外。Black76仍是相对估值近似；裸卖用20%期货名义加回购金额作为准备金代理，不是券商保证金。</p>',
        '<p class="note">新日期/真实到期日适配不修改旧引擎。9月11日8份0.2逐字段路径复现、100份原规则阶梯经济路径复现；历史截断前缀重放、资金/双费/成交方向/三次休市检查均留存。失败被显式保留，不能用测试通过掩盖策略失败。</p>',
        '<p><a href="all_account_summaries.json">全部账户摘要JSON</a> · <a href="portfolios.json">每日组合与逐点曲线JSON</a> · <a href="flatten_failures.json">清仓失败明细JSON</a> · <a href="replay_plan.json">冻结参数和源文件哈希</a></p>',
        '<script>document.getElementById("filter").addEventListener("input",e=>{const q=e.target.value.toLowerCase();document.querySelectorAll("#details details").forEach(d=>d.hidden=!d.dataset.search.toLowerCase().includes(q));});</script></main></html>']
    path=OUT/'黄金期权_前四交易日全部策略对比.html';path.write_text(''.join(html),'utf-8')
    return path


def main():
    totals,accounts,failures,small=aggregate()
    path=make_report(totals,accounts,failures,small)
    print('REPORT',path,flush=True)
    print('FAILURES',len(failures),Counter(s['case_key'] for s in failures),flush=True)
    for key in FOCUS:
        for through in (0,1):
            p=totals[f'{key}_through{through}'];print(key,through,[d['pnl_cny'] for d in p['days']],p['pnl_cny'],p['max_drawdown_cny'],flush=True)
    print('SMALL',{k:[d['pnl_cny'] for d in v['days']] for k,v in small.items()},flush=True)


if __name__=='__main__':main()
