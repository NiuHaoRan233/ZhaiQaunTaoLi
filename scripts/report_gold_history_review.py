"""Render forensic daily evidence and declare limits of post-hoc diagnostics."""
import base64
from html import escape
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from review_gold_history import OUT,BASE,DATES,FIXED,FOCUS,summarize
from probe_commodity_capital import read,write,unpack


def fmt(x):return '—' if x is None else f'{x:,.2f}'
def local(ts):return datetime.fromtimestamp(ts/1000,ZoneInfo('Asia/Shanghai'))


def charts(daily,examples):
    plt.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei','DejaVu Sans'];plt.rcParams['axes.unicode_minus']=False
    paths=[]
    fig,ax=plt.subplots(figsize=(12,5.8),constrained_layout=True)
    labels=['入场相对当帧中价','持仓中价变化','退出相对当帧中价','手续费']
    fields=['entry_edge','holding_mid_move','exit_edge','fees_cny'];positive=np.zeros(4);negative=np.zeros(4)
    for field,label,color in zip(fields,labels,['#07966f','#3974cb','#e7a33a','#858c9c']):
        vals=np.array([daily[f'{d}_fusion_throttle_long_through0'][field] for d in DATES]);vals=-vals if field=='fees_cny' else vals
        base=np.where(vals>=0,positive,negative)
        ax.bar(np.arange(4),vals,bottom=base,label=label,color=color,width=.58)
        positive+=np.maximum(vals,0);negative+=np.minimum(vals,0)
    nets=[daily[f'{d}_fusion_throttle_long_through0']['net_cny'] for d in DATES]
    ax.plot(range(4),nets,'ko-',label='最终净收益',linewidth=1.5)
    for i,net in enumerate(nets):ax.annotate(f'{net:+,.2f}',(i,net),xytext=(12,6),textcoords='offset points',weight='bold')
    ax.set_xticks(range(4),['9月7日','9月8日','9月9日','9月10日']);ax.axhline(0,color='#777',linewidth=.8)
    ax.set_ylabel('双合约合计 / 元');ax.set_title('相对较好组合：相似的报价空间，为何出现不同的最终结果',loc='left',weight='bold')
    ax.legend(ncols=3,fontsize=9,loc='upper left');ax.grid(axis='y',alpha=.15)
    p=OUT/'每日盈亏分解.png';fig.savefig(p,dpi=150);plt.close(fig);paths.append(p)
    crash=next(x for x in examples if x['trade']['case']=='v02_cancel_switch' and x['trade']['date']=='20260908' and x['trade']['net_cny']<-1000)
    fig,axes=plt.subplots(2,1,figsize=(12,6.5),sharex=True,constrained_layout=True)
    rs=[r for r in crash['books'] if r['time']>='11:28:50'];xs=[local(r['ts']) for r in rs];tr=crash['trade']
    axes[0].step(xs,[r['ask'] for r in rs],where='post',color='#c43b45',label='卖一：空头回补成本')
    axes[0].step(xs,[r['bid'] for r in rs],where='post',color='#16896c',label='买一')
    axes[0].scatter([local(tr['entry_ts']),local(tr['exit_ts'])],[tr['entry_price'],tr['exit_price']],c=['#344f92','#c43b45'],s=60,zorder=5)
    axes[0].annotate('卖出23.60',(local(tr['entry_ts']),tr['entry_price']),xytext=(-85,25),textcoords='offset points',arrowprops={'arrowstyle':'->'})
    axes[0].annotate('休市前买回25.50\n净亏1903.40元',(local(tr['exit_ts']),tr['exit_price']),xytext=(-165,-42),textcoords='offset points',arrowprops={'arrowstyle':'->'})
    axes[0].set_ylabel('期权报价 / 元每克');axes[0].set_title('9月8日 C952：期货变化很小，退出盘口却突然失去深度',loc='left',weight='bold');axes[0].legend()
    axes[1].step(xs,[r['future'] if r['future'] is not None else np.nan for r in rs],where='post',label='当时可见同月期货',color='#3974cb')
    axes[1].set_ylabel('期货价格 / 元每克');axes[1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S',tz=ZoneInfo('Asia/Shanghai')))
    axes[1].legend()
    for ax in axes:ax.grid(alpha=.2);ax.axvline(local(tr['entry_ts']),color='#555',alpha=.35,linestyle='--');ax.axvline(local(tr['exit_ts']),color='#555',alpha=.35,linestyle='--')
    p=OUT/'午休前退出盘口风险.png';fig.savefig(p,dpi=150);plt.close(fig);paths.append(p)
    selected=[next(x for x in examples if x['trade']['case']=='fusion_throttle_long' and x['trade']['date']=='20260907' and x['trade']['code']=='au2610C960.SF' and x['trade']['net_cny']<-500),
              next(x for x in examples if x['trade']['case']=='fusion_throttle_long' and x['trade']['date']=='20260910' and x['trade']['code']=='au2610C952.SF' and x['trade']['net_cny']>450)]
    fig,axes=plt.subplots(1,2,figsize=(13,4.7),constrained_layout=True)
    for ax,item in zip(axes,selected):
        tr=item['trade'];rs=[r for r in item['books'] if tr['entry_ts']<=r['ts']<=tr['exit_ts']]
        xs=[(r['ts']-tr['entry_ts'])/1000 for r in rs]
        ax.step(xs,[(r['bid']-tr['entry_price'])*1000-3.4 for r in rs],where='post',color='#3974cb',label='按当帧买一平仓的净损益')
        ax.scatter([tr['duration']],[tr['net_cny']],c='#181f39',s=65,zorder=5,label='实际被动成交净损益')
        ax.axhline(0,color='#777',linewidth=.8);ax.set_xlabel('买入后秒数');ax.set_ylabel('元 / 一手');ax.grid(alpha=.15)
        ax.set_title(f'{tr["date"][4:]} {tr["code"]}\n{tr["entry_time"][:8]}买入，最终{tr["net_cny"]:+.2f}元',loc='left');ax.legend(fontsize=8)
    p=OUT/'持续下跌与恢复的逐笔对照.png';fig.savefig(p,dpi=150);plt.close(fig);paths.append(p)
    return paths


def table(headers,rows):
    return '<div class="scroll"><table><thead><tr>'+''.join(f'<th>{escape(str(x))}</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join(f'<td>{escape(str(x))}</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table></div>'


def main():
    daily=read(OUT/'daily_attribution.json');accounts=read(OUT/'accounts.json');groups=read(OUT/'groups.json');rs=unpack(OUT/'enriched_cycles.json.gz');examples=unpack(OUT/'case_windows.json.gz')
    diag=read(OUT/'entry_cutoff60/portfolios.json');images=charts(daily,examples)
    style='body{font-family:"Microsoft YaHei",sans-serif;background:#f1f4f9;color:#1c2840;margin:0}main{max-width:1320px;margin:auto;padding:30px}p,li{line-height:1.85}.card{background:white;border:1px solid #d9e1ed;border-radius:10px;padding:22px;margin:18px 0}h2{margin-top:32px;font-size:22px}.note{font-size:13px;color:#5a6a82}table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:9px;border-bottom:1px solid #dfe5ee;text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}thead{background:#e8eef8}.scroll{overflow:auto;max-height:600px}img{width:100%;margin:18px 0}summary{padding:12px;background:#e8eef8;cursor:pointer;margin-top:8px}a{color:#245cac}'
    h=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>黄金期权：盈利日与亏损日逐笔复盘</title><style>',style,'</style><main><h1>黄金期权：盈利日与亏损日逐笔复盘</h1><p class="note">复盘日期：2026-09-07至09-10。读取冻结1184账户的92,786个已闭合循环，存在跨模型重复；12种重点结构逐笔展开。未修改旧策略或删除亏损。</p>',
       '<div class="card"><b>结论：不是单一的“波动大就亏、波动小就赚”。</b><p>差异来自三层：挂出报价后、成交前价差优势被重新定价消耗；成交后能否恢复到有利价格；临近休市时退出那一侧的报价是否还可用。趋势多头主要暴露入场质量和持仓方向问题，增强版额外暴露临近休市的平仓流动性尾部风险。</p><p>当前最清楚、已做实际回放核实的缺口，是允许在休市前很短时间内新开仓，却只预留5秒主动退出。提前60秒禁止新开仓的诊断版使增强组合从−574.60变为+1465.80元，严格组从+415.60变为+2456.00元；这是一项事后诊断，尚非前向验证或正式主版。</p></div>',
       '<h2>1. 盈利日和亏损日的钱，具体从哪里来</h2><p>先看相对较好的“8跳＋估值多头＋有限成本保护＋旧节流”。四天入场相对成交帧中价的优势都是正数，但9月7、8日的持仓变化和退出相对价差抵消了它；9月9、10日持仓变化转为正贡献。</p>']
    rows=[]
    for date in DATES:
        v=daily[f'{date}_fusion_throttle_long_through0'];rows.append([date,v['cycles'],fmt(v['entry_edge']),fmt(v['holding_mid_move']),fmt(v['exit_edge']),fmt(-v['fees_cny']),fmt(v['net_cny'])])
    h += [table(['日期','闭环','入场中价优势','持仓中价变化','退出中价优势','手续费','净收益'],rows),
        '<p class="note">精确记账恒等式：入场优势＋持仓中价变化＋退出优势−手续费＝净收益。盘口中价取聚合成交更新帧，不是交易所逐笔成交瞬间的中价。因此负的退出优势不等于“主动低价甩卖”：旧卖单成交后同帧报价上移，也会产生该数。下文区分实际主动平仓与这种报价重估。</p>',
        f'<img src="data:image/png;base64,{base64.b64encode(images[0].read_bytes()).decode()}" alt="每日盈亏归因">',
        '<h2>2. 大价差存在，但挂单等到成交时，优势已消耗了一大部分</h2><p>0.2趋势多头9月7日所有成交入场单，在各自挂出时合计拥有4820元的中价折让；等到实际成交更新帧，只剩730元，变化为−4090元。9月8日从2490元降为430元，变化为−2060元。这里衡量的是挂单等待期间的行情重估，不是手续费。</p><p>策略会在满足8跳的盘口中挂买，但真实成交不是随机抽样：价格向下变化时旧买单更容易被打中。10/60秒趋势只否决过去的明显逆向，不保证接下来的价格不会反转。它还按半个当前价差设容忍度，价差越宽，允许的逆向幅度越大。</p>',
        '<p>例如9月7日C952，09:47:06买21.38，09:50:27.5卖20.88，净亏503.40元。开仓信号里的过去10/60秒期货变动乘delta都是正数，约+181.64/+331.22元，仍然无法预测随后反转；这笔持仓期间固定入场IV估算的期货变动影响约−444.99元。整个持仓窗口都没有出现按买一扣双费后盈利的快照。</p>',
        '<h2>3. 盈利日不是完全靠黄金单边上涨，但持仓后的恢复明显更好</h2><p>相对较好组合9月7/8日，入场后60秒中价变动平均为−64.14/−16.96元；9月9/10日为+15.00/+87.00元。这个未来60秒仅用于事后解释，不是入场可用信号。9月10日盈利交易不少也先下跌、后恢复，不能据此直接做“一有浮亏就走”。</p>',
        '<p>固定每笔入场时反推的IV，把持仓中价变化进一步拆为期货价格变动、时间流逝和剩余部分：相对较好组合9月7/8日的期货变动影响合计约−2511.60/−1624.03元；9月9/10日约+127.71/+167.37元。后两天还有期权自身相对定价、波动率与盘口变化带来的正贡献。残差并非纯IV收益，不能把它当作已证明可交易的波动率套利。</p>',
        '<p>反例同样要保留：9月8日下午黄金期货约跌4.08元/克，但相对较好组合下午仍赚886元；当天亏损主要集中在10:30—11:30。它做的是短持仓局部周转，不能简单按全天涨跌或者“只做下午”选日。</p>',
        '<h2>4. 多数亏单没有“已经赚钱却忘了卖”的机会</h2><p>相对较好组合63笔盈利共6025.80元，19笔亏损共−5264.60元；平均每笔盈利约95.65元、亏损约277.08元。19笔亏损中只有1笔曾出现按有效买一平仓、扣双费后为正的快照。0.2主版89笔亏损中也只有8笔有这种快照。因此“止盈不及时”不是大多数亏损的主因。</p><p>但看到一帧可盈利买一也不代表能保证成交：这里仍使用显示一手、零额外延迟的报价回放，不是逐笔委托队列。持仓越久越容易亏是结果相关性，不能按真实最终持仓时长反向删交易。候选的亏损持有中位数117.5秒、盈利33.5秒；未来若研究超时规则，需要完整重放所有退出和再入场路径。</p>',
        f'<img src="data:image/png;base64,{base64.b64encode(images[2].read_bytes()).decode()}" alt="下跌与恢复案例">',
        '<h2>5. 9月8日增强版的大亏，主要是午休前退出盘口风险</h2><p>11:29:11，C952盘口23.38/23.62，策略挂23.60卖出；11:29:12成交，距午休48秒。11:29:40卖一扩大到25.22，11:29:51盘口已经是21.92/25.50。策略仍尝试挂买回补，直到11:29:56.5按卖一25.50强制买回，净亏1903.40元。</p><p>这期间同月期货从约959.30到959.23，反而略跌，按固定IV对这笔空头约是+39.81元的影响。亏损主要来自必须跨过极宽的回补卖价；当时有明确报价，所以这笔不是“缺报价清仓失败”，而是成功按规则清仓、但价格非常差。</p>',
        f'<img src="data:image/png;base64,{base64.b64encode(images[1].read_bytes()).decode()}" alt="休市前报价扩大">',
        '<p>这笔占增强版9月8日净亏2021.20元的约94%。入口只检查当前价差、估值和趋势，未要求持仓退出所需时间小于剩余连续交易时间；在挂单薄、接近休市的合约上，这是比手续费更大的风险。</p>',
        '<h2>6. 一项真正重放的诊断：提前60秒停新开仓，平仓仍为原5秒</h2><p>仅对0.2两个分支做这一处干预，固定8跳、同合约、每份25万元研究资金、每次一手、单边1.70元、零额外延迟，32个账户全部重跑；原单在聚合区间内可能已成交的情形仍计入。没有把未来亏损笔直接删掉。</p>']
    rows=[]
    for profile,label in [('trend_long','趋势多头'),('cancel_switch','增强择向')]:
        for t in (0,1):
            vals=[diag[f'{d}_{profile}_through{t}']['pnl_cny'] for d in DATES]
            old=read(BASE/'portfolios.json')[f'v02_{profile}_through{t}']['pnl_cny']
            rows.append([label+(' / 严格' if t else ' / 普通')]+[fmt(v) for v in vals]+[fmt(sum(vals)),fmt(old),fmt(sum(vals)-old)])
    h += [table(['诊断分支','9月7日','9月8日','9月9日','9月10日','新合计','原合计','变化'],rows),
        '<p>增强普通/严格都少6笔原净−2040.40元，无新添闭环，其余63/60笔完整保留。它不是只删除亏单：9月9日少赚113.20元，9月10日少赚156.60元。改进约93%来自那一笔1903.40元的午休损失，因此不能把60秒当作已经验证的最佳阈值。</p><p>趋势多头即使做同样修正，普通仍亏3135.20元、严格亏3940.80元。它的入场与持仓方向问题依然存在。原78个缺报价清仓失败也不由本次两个分支的诊断自动解决。</p>',
        '<h2>7. 已有规则的作用，不是简单地越多越好</h2>']
    pairs=read(OUT/'policy_pairs.json');pr=[]
    for label in ['只加有限成本保护','只加旧节流','估值择向只加趋势','只加期货先撤单','只加新版追价控制','趋势多头只加新版追价控制']:
        a=[x for x in pairs if x['label']==label and x['through']==0];b=[x for x in pairs if x['label']==label and x['through']==1]
        assert all(x['full_days'] for x in a+b)
        pr.append([label,fmt(sum(x['pnl_delta'] for x in a)),fmt(sum(x['pnl_delta'] for x in b))]+[fmt(sum(x['pnl_delta'] for x in a if x['date']==d)) for d in DATES])
    h += [table(['成对版本仅改此项','普通四日增量','严格四日增量','普通09-07','普通09-08','普通09-09','普通09-10'],pr),
        '<p>有限成本保护普通合计改善1144.20元，但9月8日反而减少333.20元；它能等待部分恢复，也会拖住真逆向交易。旧节流把估值多头改价撤单3411次降到2069次，普通仅多赚103.40元；减少改单是真的，但不是主要盈利来源。期货先撤单普通改善1168.40元，却在9月8/10日分别减少116.20/263元。</p><p>直接把0.2主版已成交交易按开仓时的估值门槛分组，不合格72笔共−1804.80元，合格67笔仍−1787.80元。加估值不能被说成单独解决所有亏损。再按入场前300秒趋势分组，趋势不逆向的57笔主版仍亏2353.80元，因此更长趋势也不是现成答案。这些只是持仓路径不变的描述分组，不是已完成策略过滤回测。</p>',
        '<h2>8. 下一步应验证什么</h2><ol><li>优先处理临近休市的新开仓与退出流动性：保留60秒诊断结果，在更多独立日期确认；单独设计有效报价和时钟触发，覆盖没有新tick的情况。</li><li>把开仓时看到的空间与成交前的价格重估分开：检查已有买单在期货/期权状态转坏时是否及时撤销，而不是只扩大原始价差阈值。</li><li>针对持续逆向持仓研究可执行退出代价和是否恢复的证据；保留会先浮亏再盈利的正例，不能仅按这几天最坏交易设止损。</li><li>继续保留普通/严格两种成交假设和真实资金约束。原4万元少做100笔原净−3760元的交易而避亏，不能把资金造成的筛选误作信号有效。</li></ol><p>本次已定位并用数据支持原因，完成一项诊断干预；没有把诊断版替换成正式主版，没有宣称未来盈利。</p>',
        '<h2>逐日分解与重点交易明细</h2><p class="note">12种结构全部保留。遇到旧账户清仓失败时，此处只展示已闭合交易的归因，不把其合计当作完整日收益。每笔未来窗口、最大可执行报价损益仅作复盘，不能输入当时交易决策。</p>']
    for key in FOCUS:
        h.append(f'<details><summary>{key}</summary>')
        tr=[]
        for date in DATES:
            for code in FIXED:
                for t in (0,1):
                    v=next(v for v in accounts.values() if (v['date'],v['code'],v['case'],v['through'])==(date,code,key,t))
                    tr.append([date,code,'严格' if t else '普通',v['status'],v['cycles']]+[fmt(v[f]) for f in ['entry_edge','holding_mid_move','exit_edge','fees_cny','net_cny']])
        h.append(table(['日期','合约','成交口径','账户状态','闭合笔数','入场优势','持仓变化','退出优势','手续费','已闭合净额'],tr));h.append('</details>')
    h.append('<h2>代表案例原始窗口</h2><p class="note">每个日期、合约及三个重点版本，取最大亏损/最大盈利的已闭合交易作窗口检查；不是随机样本，也不是只用案例代替全部交易统计。</p>')
    for item in examples:
        tr=item['trade'];h.append(f'<details><summary>{tr["date"]} {tr["code"]} {tr["case"]} {tr["entry_time"]} → {tr["exit_time"]}，{fmt(tr["net_cny"])}元</summary>')
        h.append(f'<p>方向{tr["direction"]}，开仓{tr["entry_price"]:.2f}，平仓{tr["exit_price"]:.2f}，持有{tr["duration"]:.1f}秒；买入或卖出时估值净优势{fmt(tr["signal_fair_edge"])}元，距休市约{tr["remaining_session_seconds"]:.1f}秒；退出原因{tr["exit_reason"]}。</p>')
        # A concise deterministic sample plus all state extremes; full window remains JSON.
        books=item['books'];indexes=set(range(0,len(books),max(1,len(books)//18)))
        indexes.update(i for i,r in enumerate(books) if r['ts'] in (tr['entry_ts'],tr['exit_ts']))
        indexes.update([min(range(len(books)),key=lambda i:books[i]['bid']),max(range(len(books)),key=lambda i:books[i]['ask'])])
        h.append(table(['时刻','买一','卖一','买一量','卖一量','当时同月期货'],[[r['time'],fmt(r['bid']),fmt(r['ask']),r['bid_qty'],r['ask_qty'],fmt(r['future'])] for i,r in enumerate(books) if i in indexes]));h.append('</details>')
    h += ['<p class="note">审计：92,786闭合循环的价格恒等式逐笔成立，含跨模型重复；1184旧账户源文件与结果哈希不变。38个闭环缺少满足2秒新鲜度的期货归因端点，未强行分配到IV；重点三个版本上述归因端点完整。32个诊断回放、96次休市空仓与前缀/资金/费用核验通过，71黄金测试通过。PNG目视与HTML数据核对，未做浏览器截图。</p>',
        '<p><a href="daily_attribution.json">每日价格分解</a> · <a href="accounts.json">全部1184账户归因</a> · <a href="policy_pairs.json">112组成对政策差异</a> · <a href="causal_diagnostic_groups.json">当时已知信号分组</a> · <a href="entry_cutoff60/portfolios.json">60秒停开诊断结果</a></p></main></html>']
    report=OUT/'黄金期权_盈利日与亏损日逐笔复盘.html';report.write_text(''.join(h),'utf-8')
    print(report,flush=True)


if __name__=='__main__':main()
