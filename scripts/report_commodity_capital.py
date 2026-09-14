"""Standalone report for frozen capital and entry-quality experiments."""
from pathlib import Path
from collections import defaultdict
from html.parser import HTMLParser
import base64
import hashlib
import json
import math
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

from probe_commodity_capital import ROOT,WORK,OUT,read,write,digest,unpack
from report_commodity_flow_backtest import table,money

EXT=WORK/'reports/commodity_capital_extension_20260913'
DEST=WORK/'reports/commodity_improved_backtest_20260913'
NAMES={'base':'原主策略','edge_fee':'净空间≥3.40元','edge10':'净空间≥10元',
       'flow2':'双侧各≥2次','fresh60':'双侧窗口60秒','edge_flow2':'净空间＋双侧各2次'}

def label(key):
    p,c=key.rsplit('_',1)
    return f'{NAMES[p]} · {int(c)/10000:g}万元'

def main():
    DEST.mkdir(exist_ok=True)
    first=read(OUT/'independent_summary.json')
    shared=read(OUT/'shared_summary.json')
    extra=read(EXT/'summary.json')
    assert read(EXT/'prefix_verification.json')['status']=='passed'
    results={**shared['results'],**extra['results']}
    dates=[d['date'] for d in results['base_1698000']['daily']]
    instruments={v['code']:v for v in read(WORK/'commodity_strategy/strategy_r2.json')['instruments']}
    for key,a in results.items():
        s=a['summary'];daily=a['daily'];capital=s['initial_cash_cny']
        assert abs(sum(d['pnl_cny'] for d in daily)-s['pnl_cny'])<1e-7
        assert abs(sum(v['summary']['pnl_cny'] for v in a['accounts'].values())-s['pnl_cny'])<1e-7
        for d in daily:
            d['return_pct']=d['cumulative_pnl_cny']/capital*100
            d['nav']=1+d['return_pct']/100
        for phase,start,end in [('early','20260824','20260904'),('later','20260907','20260910'),('last','20260911','20260911')]:
            s[phase+'_net_cny']=round(sum(d['pnl_cny'] for d in daily if start<=d['date']<=end),2)
        path=(EXT/f'{key}.json.gz') if key.startswith('edge10_') else OUT/'shared'/f'{key}.json.gz'
        ledger=unpack(path)
        profits=sorted([c['net_cents'] for c in ledger['cycles'] if c['net_cents']>0],reverse=True)
        s['without_top_three_cny']=round(s['pnl_cny']-sum(profits[:3])/100,2)
        s['overnight_cycle_net_cny']=round(s['completed_cycle_net_cny']-s['same_day_cycle_net_cny'],2)
        s['cycle_win_rate_pct']=sum(c['net_cents']>0 for c in ledger['cycles'])/max(1,len(ledger['cycles']))*100
        net=np.array([0]+[d['cumulative_pnl_cny'] for d in daily]);peak=np.maximum.accumulate(capital+net)
        s['daily_max_drawdown_pct']=float(np.max((peak-capital-net)/peak*100))
        a['daily_dd_pct']=((peak-capital-net)/peak*100).tolist()
        assert sum(f['fee_cents'] for f in ledger['fills'])==round(s['fees_cny']*100)
        assert len(a['accounts'])==64 and sum(len(v['daily']) for v in a['accounts'].values())==911
    write(DEST/'all_results.json',dict(independent=first,shared=results,dates=dates,names=NAMES,
        qualification='Seen-history offline research only. Not new forward profit. Extension edge10 selected after first-round results.'))
    focus=['base_1698000','base_300000','edge10_300000','edge10_200000']
    colors=['#8995a5','#517bb8','#007c77','#bf8b37']
    font_manager.fontManager.addfont('C:/Windows/Fonts/msyh.ttc')
    plt.rcParams.update({'font.family':'Microsoft YaHei','axes.unicode_minus':False,'font.size':10,
        'axes.spines.top':False,'axes.spines.right':False,'axes.edgecolor':'#d2dce5','text.color':'#20344b',
        'xtick.color':'#546b80','ytick.color':'#546b80','savefig.facecolor':'#f6f8fb'})
    x=np.arange(16);ticks=['起点']+[d[4:6]+'/'+d[6:] for d in dates]
    fig,axes=plt.subplots(2,1,figsize=(13,8.6),layout='constrained')
    fig.suptitle('商品期权第二轮｜共享资金后的收益与回撤',x=.035,ha='left',fontsize=21,fontweight='bold')
    for key,col in zip(focus,colors):
        s=results[key]['summary'];y=[0]+[d['return_pct'] for d in results[key]['daily']]
        axes[0].plot(x,y,color=col,lw=2.2,marker='o',ms=3,label=label(key)+f'  {s["return_pct"]:+.2f}%')
        axes[1].plot(x,-np.array(results[key]['daily_dd_pct']),color=col,lw=2)
    axes[0].legend(frameon=False,loc='upper left',fontsize=9)
    axes[0].set_title('2026/08/24—09/11 · 15个交易日 · 单边1.70元 / 0额外延迟 / 每合约最多1手',loc='left',fontsize=10,pad=12)
    axes[0].set_ylabel('累计净收益率（%）');axes[1].set_ylabel('日末净值回撤（%）')
    for ax in axes:
        ax.set_xticks(x,ticks,fontsize=9);ax.set_xlim(0,15.4);ax.grid(axis='y',alpha=.18);ax.axhline(0,color='#adb8c2',lw=.7)
        ax.axvspan(14.5,15.4,color='#e8cb80',alpha=.16)
    axes[1].set_xlabel('真实共享初始现金；买单接受时冻结价款与手续费。图为日末采样，报告另列盘中最大回撤。\n仍为已查看的历史样本；共享资金提高收益率，也会提高同等金额亏损占本金的比例。',fontsize=9,labelpad=12)
    fig.savefig(DEST/'共享资金收益率与回撤.png',dpi=170);plt.close(fig)
    fig,ax=plt.subplots(figsize=(12,5.6),layout='constrained')
    for key,col in zip(focus,colors):
        ax.plot(x,[0]+[d['cumulative_pnl_cny'] for d in results[key]['daily']],color=col,lw=2,label=label(key))
    ax.set_title('人民币累计净收益｜区分交易利润改善与本金缩减',loc='left',fontsize=18,pad=16)
    ax.set_xticks(x,ticks,fontsize=9);ax.set_ylabel('累计净收益（元）');ax.grid(axis='y',alpha=.2);ax.legend(frameon=False,loc='upper left');ax.axhline(0,color='#adb8c2',lw=.7)
    fig.savefig(DEST/'人民币累计净收益.png',dpi=170);plt.close(fig)
    b=['''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>商品期权 · 第二轮收益提升报告</title><style>
    body{margin:0;background:#f5f8fb;color:#20344b;font:15px/1.75 "Microsoft YaHei",sans-serif}main{max-width:1300px;margin:auto;padding:32px 24px}h1{font-size:32px}h2{font-size:22px;margin-top:0}.card{background:white;border:1px solid #dce5ec;border-radius:12px;padding:24px;margin:24px 0}.note{background:#fff5dd;padding:18px;border-left:4px solid #d0a33d}.muted{color:#61768b}.scroll{max-height:650px;overflow:auto}table{border-collapse:collapse;width:100%;white-space:nowrap;font-size:13px}td,th{padding:9px 12px;text-align:right;border-bottom:1px solid #e4ebf1}td:first-child,th:first-child{text-align:left}th{position:sticky;top:0;background:#eaf1f6}tr:nth-child(even){background:#f8fafc}img{width:100%}details{margin:18px 0}summary{cursor:pointer;font-weight:bold}a{color:#007c77}nav{display:flex;gap:20px;flex-wrap:wrap}small{color:#61768b}@media(max-width:700px){main{padding:15px 10px}.card{padding:14px}h1{font-size:27px}}
    </style><main><small>COMMODITY OPTIONS · CAPITAL & ENTRY RESEARCH</small><h1>第二轮收益提升：共享资金＋入场净空间</h1><p class="muted">2026年8月24日—9月11日｜64合约 / 36品种｜已扣每手单边1.70元手续费｜报告生成2026-09-13</p><nav><a href="#results">主要结果</a><a href="#curve">曲线</a><a href="#hypotheses">全部试验</a><a href="#daily">每日收益</a><a href="#contracts">全部合约与品种</a><a href="#audit">规则与核验</a></nav>''']
    b.append('<section class="card" id="results"><h2>主要结果</h2>'+table(['方案','真正初始资金/元','净收益/元','净收益率','盘中最大回撤','盘中回撤金额/元'],[[label(k),money(results[k]['summary']['initial_cash_cny']),money(results[k]['summary']['pnl_cny']),f'{results[k]["summary"]["return_pct"]:.4f}%',f'{results[k]["summary"]["max_drawdown_pct"]:.4f}%',money(results[k]['summary']['max_drawdown_cny'])] for k in focus]))
    candidate=results['edge10_300000']['summary']
    b.append('<p class="note">研究上优先保留30万元、净空间至少10元的版本继续观察，而非按百分比最高选择最小本金。它的交易利润与回撤变化见上表；20万元作资金更紧张的对照。收益率提高的大头来自资金复用，不能把全部提升解释为交易优势增强。日内闭环仍可能亏损，后文完整拆分隔夜收益。</p></section><section class="card" id="curve"><h2>收益率、回撤与人民币利润</h2>')
    for name in ['共享资金收益率与回撤.png','人民币累计净收益.png']:
        b.append('<img alt="'+name[:-4]+'" src="data:image/png;base64,'+base64.b64encode((DEST/name).read_bytes()).decode()+'">')
    b.append('</section><section class="card" id="hypotheses"><h2>全部试验，包含失败的过滤条件</h2><p>下表六组均保留169.8万元独立账户预算，先看交易利润本身是否改善。入场过滤在旧单撮合之后执行，不会追溯取消已经成立的成交。</p>')
    b.append(table(['独立账户规则','净收益/元','较旧主策略/元','前10日/元','后4日/元','9月11日/元','盘中回撤金额/元','日内闭环净收益/元'],[[NAMES[k],money(a['summary']['pnl_cny']),money(a['summary']['pnl_cny']-21839.7),money(a['summary']['early_pnl_cny']),money(a['summary']['later_pnl_cny']),money(a['summary']['final_day_pnl_cny']),money(a['summary']['max_drawdown_cny']),money(a['summary']['same_day_cycle_net_cny'])] for k,a in first['results'].items()]))
    b.append('<p>首轮按执行前写明的“前10日净收益减去该阶段最大3笔盈利闭环”排序，flow2进入共享测试，但它后段亏损且全期利润显著下降，因此保留为失败对照。edge10在独立全期利润和回撤上改善，随后追加20万/30万/50万元共享测试，独立保存追加合同；这是看过结果后的进一步研究，没有冒充事先唯一选中的策略。</p>')
    b.append(table(['全部共享方案','本金/元','净收益/元','收益率','盘中回撤率','费用/元','完成闭环','资金不足帧数'],[[label(k),money(a['summary']['initial_cash_cny']),money(a['summary']['pnl_cny']),f'{a["summary"]["return_pct"]:.3f}%',f'{a["summary"]["max_drawdown_pct"]:.3f}%',money(a['summary']['fees_cny']),a['summary']['cycles'],a['summary']['funding_rejection_frames']] for k,a in results.items()]))
    b.append('<p class="muted">“资金不足帧数”是行情帧上的报价拒绝次数，不能当成错过的成交数。峰值挂单冻结额＋持仓成本可能超过初始资金，因为已实现盈利可以继续使用；每帧实际现金始终非负并覆盖全部买单冻结额。</p></section>')
    b.append('<section class="card" id="daily"><h2>每日净收益：本金和交易规则同时展示</h2>'+table(['日期']+[label(k) for k in focus],[[d]+[money(results[k]['daily'][i]['pnl_cny']) for k in focus] for i,d in enumerate(dates)]))
    b.append('<details><summary>全部12个共享方案每日合计</summary>'+table(['日期']+[label(k) for k in results],[[d]+[money(results[k]['daily'][i]['pnl_cny']) for k in results] for i,d in enumerate(dates)])+'</details>')
    b.append('<details><summary>全部6种独立账户规则每日合计</summary>'+table(['日期']+[NAMES[k] for k in first['results']],[[d]+[money(first['results'][k]['daily'][i]['pnl_cny']) for k in first['results']] for i,d in enumerate(dates)])+'</details>')
    b.append('<h3>盈利来源与集中度</h3>'+table(['拆分/元']+[label(k) for k in focus],[[name]+[money(results[k]['summary'][field]) for k in focus] for name,field in [('前10日','early_net_cny'),('后4日','later_net_cny'),('9月11日','last_net_cny'),('日内已闭环','same_day_cycle_net_cny'),('跨日已闭环','overnight_cycle_net_cny'),('未平仓净贡献','open_cycle_contribution_cny'),('扣除最大3笔盈利后的诊断值','without_top_three_cny')]]))
    b.append('<p>日内闭环＋跨日闭环＋未平仓净贡献＝总净收益。跨日收益不是纯盘口价差；去掉最大盈利只是集中度诊断，不能冒充策略重跑。15日样本不做年化收益承诺。</p></section>')
    b.append('<section class="card" id="contracts"><h2>全部64合约及36品种日度</h2><p>每个共享模型都包含原64合约，资金限制改变可参与时点，不按最终盈亏删除合约。—是缺失日，不代表零收益。下列每个模型64×15表均保留49个缺失格；子账本列出利润贡献，不将原虚拟预算再算成实际本金。</p>')
    baseline=read(WORK/'reports/commodity_backtest_20260913/backtest_data.json')
    b.append('<details open><summary>全部合约总收益：旧盈利路径与新组合</summary>'+table(['合约','品种','原循环/元']+[label(k) for k in focus],[[c,instruments[c]['name'],money(baseline['contracts'][c]['variants']['control']['summary']['pnl_cny'])]+[money(results[k]['accounts'][c]['summary']['pnl_cny']) for k in focus] for c in sorted(instruments)])+'</details>')
    for key,a in results.items():
        rows=[]
        for code in sorted(instruments):
            account=a['accounts'][code];ds={d['date']:d['pnl_cny'] for d in account['daily']}
            rows.append([code,instruments[code]['name'],money(account['summary']['pnl_cny'])]+[money(ds[d]) if d in ds else '—' for d in dates])
        b.append('<details><summary>'+label(key)+'：64合约每日净收益</summary>'+table(['合约','品种','全期/元']+dates,rows,'contract-matrix')+'</details>')
    for key in focus:
        groups=defaultdict(list)
        for code in instruments:groups[instruments[code]['name']].append(code)
        rows=[]
        for name,codes in sorted(groups.items()):
            ds={c:{d['date']:d['pnl_cny'] for d in results[key]['accounts'][c]['daily']} for c in codes}
            values=[sum(ds[c].get(d,0) for c in codes) if any(d in ds[c] for c in codes) else None for d in dates]
            rows.append([name,len(codes),money(sum(results[key]['accounts'][c]['summary']['pnl_cny'] for c in codes))]+[money(v) if v is not None else '—' for v in values])
        b.append('<details><summary>'+label(key)+'：36品种每日净收益</summary>'+table(['品种','合约数','全期/元']+dates,rows,'product-matrix')+'</details>')
    b.append('</section><section class="card" id="audit"><h2>执行合同、验证与局限</h2><ol><li>每模型一个固定初始钱包，单合约最多买入1手，不借款。报价时冻结买价＋1.70元，撤单解冻，成交扣款，卖出回笼资金。保留旧每合约预算作为虚拟上限，不额外注资。</li><li>按全部合约市场时间重放；相同时刻合约代码升序处理，所有同刻更新结束后计算回撤。这是明确的模拟分配规则，尚未验证真实接收顺序。</li><li>退出继续沿用原300秒有限成本保护及持续风险提前解除。没有通过延长死扛、跳过亏损平仓或增加手数提高成绩。</li><li>原169.8万元base共享对照逐笔复现原64账户成交及日度；所有资金模型每帧检查现金、挂单冻结和净值守恒。base及edge10的30万元方案从初始现金另跑至9月4日，委托、成交、日报与完整路径前缀一致。</li><li>768项工程测试通过，包含12项新测试：先旧单撮合后新过滤、双侧证据时窗、费用不足、挂单冻结、重报超资、卖出回款、跨日连续、时段撤单和同刻回撤等。</li></ol>')
    b.append('<p class="note">所有结果为已查看历史日盘L1数据上的离线研究，不是新前向盈利。真实排队、冲击、夜盘风险及行权仍未验证；缺失数据沿用最后估值，因此盘中回撤也可能低估未观测风险。本轮没有修改原纸面策略配置、启动交易或改变债券矩阵。</p><p><a href="all_results.json">全部指标与日度JSON</a> · <a href="report_qa.json">核验清单</a>。两张曲线已内嵌，HTML可单文件离线查看。</p></section></main></html>')
    page=''.join(b);(DEST/'收益提升完整回测报告.html').write_text(page,'utf-8')
    class Check(HTMLParser):
        def __init__(self):super().__init__();self.current=None;self.matrices=[];self.images=0
        def handle_starttag(self,t,attrs):
            a=dict(attrs)
            if t=='table':self.current=[a.get('class'),0]
            if t=='tr' and self.current is not None:self.current[1]+=1
            if t=='img':self.images+=1;assert a['src'].startswith('data:image/png;base64,')
        def handle_endtag(self,t):
            if t=='table':self.matrices.append(self.current);self.current=None
    check=Check();check.feed(page)
    assert [n for c,n in check.matrices if c=='contract-matrix']==[65]*len(results)
    assert [n for c,n in check.matrices if c=='product-matrix']==[37]*len(focus)
    assert check.images==2
    preserved=read(OUT/'preserved_result_manifest.json')
    for p,h in preserved.items():assert digest(ROOT/p)==h,p
    for directory in [OUT,EXT]:
        for p,h in read(directory/'result_manifest.json').items():assert digest(ROOT/p)==h,p
    qa=dict(status='passed',independent_accounts=384,shared_portfolios=len(results),contracts_per_portfolio=64,
        observed_contract_days_per_portfolio=911,missing_cells_per_portfolio=49,shared_input_frames=shared['frames'],
        preserved_old_result_files=len(preserved),new_tests=12,full_suite_tests=768,
        prefix_verification=read(EXT/'prefix_verification.json'),html_contract_matrices=len(results),html_product_matrices=len(focus),
        cash_and_reservation_invariants='checked every processed event',source_hashes='all original and new result manifests verified',
        browser_screenshot_review='not performed; no claim of browser screenshot QA')
    write(DEST/'report_qa.json',qa)
    write(DEST/'artifact_manifest.json',{p.name:digest(p) for p in DEST.iterdir() if p.is_file() and p.name!='artifact_manifest.json'})
    print(json.dumps({k:results[k]['summary'] for k in focus},ensure_ascii=False,indent=2))
    print('QA',json.dumps(qa,ensure_ascii=False))

if __name__=='__main__':main()
