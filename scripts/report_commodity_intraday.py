"""Report user-defined cost virtual settlement without disguising it as fills."""
from collections import defaultdict,Counter
from html.parser import HTMLParser
from pathlib import Path
import base64
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

from probe_commodity_intraday import OUT,ROOT,WORK,read,write,digest,unpack
from report_commodity_flow_backtest import table,money
from report_commodity_capital import NAMES

DEST=WORK/'reports/commodity_intraday_report_20260913'

def label(k):
    p,c=k.rsplit('_',1);return f'{NAMES[p]} / {int(c)/10000:g}万元'

def main():
    DEST.mkdir(exist_ok=True)
    verify=read(OUT/'verification.json');assert verify['status']=='passed'
    shared=read(OUT/'shared_summary.json')['results']
    independent=read(OUT/'independent_summary.json')['results']
    cfg={v['code']:v for v in read(WORK/'commodity_strategy/strategy_r2.json')['instruments']}
    dates=[d['date'] for d in shared['base_300000']['daily']]
    virtuals={};daily_parts={};all_cycles={}
    for key,a in shared.items():
        r=unpack(OUT/'shared'/f'{key}.json.gz');s=a['summary']
        virtuals[key]=[f for f in r['fills'] if f['kind']=='virtual_cost_close']
        all_cycles[key]=r['cycles']
        ds={d:dict(date=d,market_net_cny=0,virtual_net_cny=0,virtual_count=0,
            floating_before_virtual_cny=0) for d in dates}
        for c in r['cycles']:
            # All exits are daytime China; datetime conversion ensures explicit date semantics.
            from datetime import datetime,timezone,timedelta
            date=datetime.fromtimestamp(c['exit_ts']/1000,timezone(timedelta(hours=8))).strftime('%Y%m%d')
            if c.get('exit_kind')=='virtual_cost_close':
                ds[date]['virtual_net_cny']+=c['net_cents']/100
                ds[date]['virtual_count']+=1
                ds[date]['floating_before_virtual_cny']+=c['quote_mark_pnl_before_close_cents']/100
            else:ds[date]['market_net_cny']+=c['net_cents']/100
        daily_parts[key]=[ds[d] for d in dates]
        assert s['tail_contracts']==0 and s['overnight_cycles']==0
        assert abs(s['market_cycle_net_cny']+s['virtual_cycle_net_cny']-s['pnl_cny'])<1e-7
        for i,d in enumerate(a['daily']):
            assert abs(ds[d['date']]['market_net_cny']+ds[d['date']]['virtual_net_cny']-d['pnl_cny'])<1e-7
            d['return_pct']=d['cumulative_pnl_cny']/s['initial_cash_cny']*100
            d['nav']=1+d['return_pct']/100
        net=np.array([0]+[d['cumulative_pnl_cny'] for d in a['daily']]);peak=np.maximum.accumulate(s['initial_cash_cny']+net)
        a['daily_dd_pct']=((peak-s['initial_cash_cny']-net)/peak*100).tolist()
        s['early_net_cny']=round(sum(d['pnl_cny'] for d in a['daily'] if d['date']<='20260904'),2)
        s['later_net_cny']=round(sum(d['pnl_cny'] for d in a['daily'] if '20260907'<=d['date']<='20260910'),2)
        s['final_day_net_cny']=a['daily'][-1]['pnl_cny']
        assert len(a['accounts'])==64 and sum(len(v['daily']) for v in a['accounts'].values())==911
        assert all(d['end_inventory']==0 for v in a['accounts'].values() for d in v['daily'])
    write(DEST/'all_daily_and_metrics.json',dict(shared=shared,independent=independent,dates=dates,
        daily_decomposition=daily_parts,rule='User-specified entry-cost virtual liquidation, fees retained; not market-executable liquidation PnL.'))
    write(DEST/'virtual_close_details.json',virtuals)
    font_manager.fontManager.addfont('C:/Windows/Fonts/msyh.ttc')
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10,
        'axes.spines.top':False,'axes.spines.right':False,'axes.edgecolor':'#d4dfe6','text.color':'#23374a',
        'xtick.color':'#526b80','ytick.color':'#526b80','savefig.facecolor':'#f5f8fb'})
    x=np.arange(16);ticks=['起点']+[d[4:6]+'/'+d[6:] for d in dates]
    profiles=list(NAMES);colors=['#476dab','#27998b','#00766f','#bd8c38','#a277ae','#ce7181']
    fig,axes=plt.subplots(2,1,figsize=(13,8.5),layout='constrained')
    fig.suptitle('商品期权｜仅日内、收盘按买入成本虚拟结算',x=.035,ha='left',fontsize=20,fontweight='bold')
    for p,col in zip(profiles,colors):
        k=p+'_300000';a=shared[k]
        axes[0].plot(x,[0]+[d['return_pct'] for d in a['daily']],lw=2,color=col,label=NAMES[p]+f'  {a["summary"]["return_pct"]:+.2f}%')
        axes[1].plot(x,-np.array(a['daily_dd_pct']),lw=1.8,color=col)
    for ax in axes:
        ax.set_xticks(x,ticks,fontsize=9);ax.grid(axis='y',alpha=.18);ax.axhline(0,color='#a6b5c3',lw=.7)
        ax.set_xlim(0,15.4)
    axes[0].set_title('2026/08/24—09/11 · 各30万元共享本金 · 单边1.70元手续费 · 每合约最多1手',loc='left',fontsize=10,pad=12)
    axes[0].set_ylabel('成本虚拟结算累计研究收益率（%）');axes[0].legend(frameon=False,ncol=2,loc='lower left',fontsize=9)
    axes[1].set_ylabel('日末结算净值回撤（%）')
    axes[1].set_xlabel('虚拟平仓：尾仓按本笔买入成本卖出、毛盈亏归零、仍扣手续费；每天结束库存为0。\n这不是按收盘盘口可实现的收益；虚拟结算前浮盈亏另列。图为日末采样，报告另列盘中回撤。',fontsize=9,labelpad=12)
    fig.savefig(DEST/'日内成本虚拟结算收益曲线.png',dpi=170);plt.close(fig)
    key='edge10_300000';parts=daily_parts[key]
    fig,axes=plt.subplots(2,1,figsize=(13,8),layout='constrained')
    fig.suptitle('净空间≥10元 / 30万元｜正常闭环与虚拟结算拆分',x=.035,ha='left',fontsize=20)
    vals=np.array([d['market_net_cny'] for d in parts]);fees=np.array([d['virtual_net_cny'] for d in parts])
    axes[0].bar(x[1:]-.18,vals,width=.36,color='#00766f',label='正常日内闭环净收益（含双侧费用）')
    axes[0].bar(x[1:]+.18,fees,width=.36,color='#cc7180',label='日末成本虚拟闭环净收益（仅亏双侧费用）')
    axes[0].legend(frameon=False,fontsize=9);axes[0].set_ylabel('当日闭环净收益（元）')
    tail=np.array([d['floating_before_virtual_cny'] for d in parts])
    axes[1].bar(x[1:],tail,color=['#00766f' if v>=0 else '#cc7180' for v in tail],width=.65)
    axes[1].set_ylabel('成本虚拟平仓前的最后买一浮盈亏（元）')
    axes[1].set_xlabel('下图浮盈亏在用户指定的成本结算中归零；它不是已成交利润，也不是一套按买一强平的独立回测。',fontsize=9,labelpad=12)
    for ax in axes:
        ax.set_xticks(x,ticks,fontsize=9);ax.grid(axis='y',alpha=.18);ax.axhline(0,color='#a6b5c3',lw=.7);ax.set_xlim(0,15.5)
    fig.savefig(DEST/'日内闭环与尾仓浮盈亏.png',dpi=170);plt.close(fig)
    b=['''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>商品期权日内成本虚拟结算回测</title><style>
    body{margin:0;background:#f5f8fb;color:#23374a;font:15px/1.75 "Microsoft YaHei",sans-serif}main{max-width:1300px;margin:auto;padding:30px 24px}h1{font-size:30px}h2{font-size:22px}.card{background:white;border:1px solid #dce5ed;border-radius:12px;padding:22px;margin:24px 0}.note{background:#fff4dd;border-left:4px solid #d4a23f;padding:18px}.scroll{max-height:650px;overflow:auto}table{border-collapse:collapse;width:100%;white-space:nowrap;font-size:13px}th,td{padding:9px 11px;border-bottom:1px solid #e1eaf1;text-align:right}th{position:sticky;top:0;background:#e7f0f6}th:first-child,td:first-child{text-align:left}tr:nth-child(even){background:#f7fafc}img{width:100%}details{margin:18px 0}summary{font-weight:bold;cursor:pointer}a{color:#00766f}small,.muted{color:#61788d}nav{display:flex;flex-wrap:wrap;gap:20px}@media(max-width:700px){main{padding:15px 10px}.card{padding:14px}h1{font-size:25px}}
    </style><main><small>INTRADAY ONLY · USER-DEFINED COST SETTLEMENT</small><h1>商品期权：日内成本虚拟结算完整回测</h1><p>2026年8月24日—9月11日｜15个交易日｜64合约 / 36品种｜日末零库存｜单边1.70元 / 0额外延迟</p><nav><a href="#result">结果</a><a href="#curve">曲线</a><a href="#daily">每日拆分</a><a href="#all">全部合约</a><a href="#rule">规则与核验</a></nav>''']
    b.append('<p class="note">用户确认的规则：收盘未平仓按该笔买入开仓价虚拟卖出，毛盈亏归零，仍扣买卖双侧手续费；次日空仓，现金连续。以下均为此指定口径的研究收益，虚拟结算不能当成有市场成交证据的平仓。</p>')
    b.append('<section class="card" id="result"><h2>六套旧规则在新的日内口径下表现</h2>'+table(['30万元共享方案','研究净收益/元','研究收益率','正常日内闭环净收益/元','虚拟平仓笔数','虚拟闭环双边费用/元','盘中最大回撤'],[[NAMES[p],money(shared[p+'_300000']['summary']['pnl_cny']),f'{shared[p+"_300000"]["summary"]["return_pct"]:.3f}%',money(shared[p+'_300000']['summary']['market_cycle_net_cny']),shared[p+'_300000']['summary']['virtual_close_count'],money(shared[p+'_300000']['summary']['virtual_roundtrip_fees_cny']),f'{shared[p+"_300000"]["summary"]["max_drawdown_pct"]:.3f}%'] for p in profiles]))
    if all(shared[p+'_300000']['summary']['pnl_cny']<0 for p in profiles):
        b.append('<p><strong>结论：旧六套条件均未通过当前日内盈利目标。</strong>即使日末将未平仓毛盈亏归零，正常日内闭环本身仍整体亏损。之前依赖跨日利润推荐的共享候选，不能继续作为日内有效策略推荐。</p>')
    b.append('<details><summary>全部9个共享模型，包括本金对照</summary>'+table(['方案','研究净收益/元','收益率','盘中回撤/元','虚拟结算前浮盈亏合计/元','其中报价陈旧的虚拟平仓笔数'],[[label(k),money(a['summary']['pnl_cny']),f'{a["summary"]["return_pct"]:.3f}%',money(a['summary']['max_drawdown_cny']),money(a['summary']['quote_mark_pnl_before_virtual_close_cny']),a['summary']['stale_virtual_closes']] for k,a in shared.items()])+'</details>')
    b.append('<details><summary>六套独立预算对照（每组169.8万元）</summary>'+table(['规则','研究净收益/元','前10日/元','后4日/元','最后一天/元','正常闭环净收益/元','虚拟结算前浮盈亏/元'],[[NAMES[p]]+[money(a['summary'][k]) for k in ['pnl_cny','early_pnl_cny','later_pnl_cny','final_day_pnl_cny','market_cycle_net_cny','quote_mark_pnl_before_virtual_close_cny']] for p,a in independent.items()])+'</details></section>')
    b.append('<section class="card" id="curve"><h2>收益曲线和虚拟结算影响</h2>')
    for name in ['日内成本虚拟结算收益曲线.png','日内闭环与尾仓浮盈亏.png']:
        b.append('<img alt="'+name[:-4]+'" src="data:image/png;base64,'+base64.b64encode((DEST/name).read_bytes()).decode()+'">')
    b.append('</section><section class="card" id="daily"><h2>每日净收益与结算拆分</h2>'+table(['日期']+[label(k) for k in shared],[[d]+[money(a['daily'][i]['pnl_cny']) for a in shared.values()] for i,d in enumerate(dates)]))
    for k in ['base_300000','edge10_300000']:
        b.append('<details open><summary>'+label(k)+'：每日利润从哪里来</summary>'+table(['日期','正常日内闭环/元','虚拟闭环净收益/元','虚拟平仓笔数','研究日收益/元','成本结算前浮盈亏/元'],[[d['date'],money(d['market_net_cny']),money(d['virtual_net_cny']),d['virtual_count'],money(shared[k]['daily'][i]['pnl_cny']),money(d['floating_before_virtual_cny'])] for i,d in enumerate(daily_parts[k])])+'</details>')
    b.append('<p>虚拟闭环净收益 = −3.40元 × 虚拟平仓笔数，已包含该笔开仓手续费，不再重复扣一次。正常闭环净收益也已经扣买卖两侧费用。两者相加等于日度研究净收益。</p></section>')
    b.append('<section class="card" id="all"><h2>全部合约日度与品种贡献</h2><p>—表示缺失行情日期；0.00表示该有记录日期净变化为零。每个模型911有效合约日、49个缺失格。所有有记录日结算后库存均为0。</p>')
    b.append('<details open><summary>全部64合约总收益，30万元六种规则对照</summary>'+table(['合约','品种']+[NAMES[p] for p in profiles],[[c,cfg[c]['name']]+[money(shared[p+'_300000']['accounts'][c]['summary']['pnl_cny']) for p in profiles] for c in sorted(cfg)])+'</details>')
    for k,a in shared.items():
        rows=[]
        for c,v in sorted(a['accounts'].items()):
            ds={d['date']:d['pnl_cny'] for d in v['daily']}
            rows.append([c,cfg[c]['name'],money(v['summary']['pnl_cny'])]+[money(ds[d]) if d in ds else '—' for d in dates])
        b.append('<details><summary>'+label(k)+'：64合约完整日度</summary>'+table(['合约','品种','全期/元']+dates,rows,'contract-matrix')+'</details>')
    for k in ['base_300000','edge10_300000']:
        groups=defaultdict(list)
        for c in cfg:groups[cfg[c]['name']].append(c)
        rows=[]
        for name,codes in sorted(groups.items()):
            ds={c:{d['date']:d['pnl_cny'] for d in shared[k]['accounts'][c]['daily']} for c in codes}
            rows.append([name,len(codes),money(sum(shared[k]['accounts'][c]['summary']['pnl_cny'] for c in codes))]+[money(sum(ds[c].get(d,0) for c in codes)) if any(d in ds[c] for c in codes) else '—' for d in dates])
        b.append('<details><summary>'+label(k)+'：全部36品种日度</summary>'+table(['品种','合约数','全期/元']+dates,rows,'product-matrix')+'</details>')
    b.append('</section><section class="card" id="rule"><h2>完整规则与核验</h2><ol><li>正常日内报价、证据、有限成本保护和开仓过滤沿用原六个版本。日末15:00才对剩余仓位按本笔买入成本虚拟结算。没有使用后日价格，没有保留隔夜库存。</li><li>未成交委托日末取消，无额外费用；已买未卖的仓位新增1.70元卖出费用，连同此前买入费该闭环净−3.40元。已正常卖出的闭环保留实际模拟价差损益。</li><li>现金不重置，共享钱包买单先冻结资金，交易与结算均核对余额。低本金导致的资金拒绝和后续新交易均实际重放。子账本预算仅作单合约上限，不另行注资。</li><li>384独立账户、9共享模型完成；169.8万元base与独立账户全部64合约的日度和成交路径等价；base/edge10共享30万元重新从起点跑至9月4日，包含日末虚拟结算的委托、成交及日度前缀一致。</li><li>778项测试通过，其中10项新测试覆盖浮亏/浮盈尾仓成本结算、双侧费用、正常获利闭环保留、撤单不收费、防重复结算、次日现金连续、缺失日期、共享资金日末归零和正确收盘时刻。</li></ol>')
    b.append('<p class="note">正常盘口撮合也仍是历史L1推断，不是实盘交易。成本虚拟平仓前的浮盈亏已完整列出；若改按盘口平仓，资金和后续成交会改变，不能仅减去这项浮盈亏就冒充另一套完整回测。本轮没有修改纸面运行配置或启动实盘。</p><p><a href="all_daily_and_metrics.json">全部日度与指标JSON</a> · <a href="virtual_close_details.json">每笔虚拟平仓及结算前浮盈亏</a> · <a href="report_qa.json">核验清单</a>。本HTML图表内嵌，可离线打开。</p></section></main></html>')
    page=''.join(b);(DEST/'日内成本虚拟结算完整回测报告.html').write_text(page,'utf-8')
    class Check(HTMLParser):
        def __init__(self):super().__init__();self.t=None;self.tables=[];self.images=0
        def handle_starttag(self,t,attrs):
            a=dict(attrs)
            if t=='table':self.t=[a.get('class'),0]
            if t=='tr' and self.t is not None:self.t[1]+=1
            if t=='img':self.images+=1;assert a['src'].startswith('data:image/png;base64,')
        def handle_endtag(self,t):
            if t=='table':self.tables.append(self.t);self.t=None
    check=Check();check.feed(page)
    assert [n for c,n in check.tables if c=='contract-matrix']==[65]*9
    assert [n for c,n in check.tables if c=='product-matrix']==[37]*2
    for p,h in read(OUT/'result_manifest.json').items():assert digest(ROOT/p)==h,p
    qa=dict(status='passed',strategy_verification=verify,tests=778,new_tests=10,
        contract_matrices=9,product_matrices=2,embedded_pngs=check.images,
        decomposition='Every daily total equals normal intraday cycle net plus virtual cycle net',
        no_overnight_inventory=True,source_hashes_unchanged=True,browser_screenshot_review='not performed')
    write(DEST/'report_qa.json',qa)
    write(DEST/'artifact_manifest.json',{p.name:digest(p) for p in DEST.iterdir() if p.is_file() and p.name!='artifact_manifest.json'})
    print(json.dumps({k:a['summary'] for k,a in shared.items()},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
