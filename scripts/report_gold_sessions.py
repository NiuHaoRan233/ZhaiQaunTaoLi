"""Day/night and daytime-mask comparisons with complete paired rules."""
from pathlib import Path
import base64
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from probe_gold_sessions import OUT,BASE,DATES,read,write,m,LABELS
from probe_gold_two_mode import table,fmt

MASK_LABELS={'day':'完整日盘','am1':'仅09:30—10:15','am2':'仅10:30—11:30','pm':'仅13:30—15:00','night':'仅21:00—次日02:30','both':'日盘＋当晚夜盘'}


def main():
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen output')
    groups=read(OUT/'comparison.json');market=read(OUT/'market_activity.json')
    h=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>黄金期权交易时段比较</title><style>body{font-family:"Microsoft YaHei",sans-serif;background:#f3f5f9;color:#243249;margin:0}main{max-width:1550px;margin:auto;padding:24px}p{line-height:1.8}table{border-collapse:collapse;background:white;width:100%;font-size:13px}td,th{padding:9px;text-align:right;white-space:nowrap;border-bottom:1px solid #dce2eb}th{background:#e5edf8}td:first-child,th:first-child{text-align:left}.scroll{overflow:auto;max-height:700px}summary{cursor:pointer;padding:14px}img{max-width:100%}</style><main>',
       '<h1>相同新合约与策略，只改变允许交易时段</h1>',
       '<p>沿用09-07至09-11五个日期、1%/1.5%/2%三组每日冻结新合约及九个策略，旧C960/C952排除。对照完整日盘、三段日盘单独交易、夜盘、日盘加夜盘。先买后卖和先卖后买独立，不叠加成双向资金账户。每合约1手，单边1.70元、额外延迟0。</p>',
       '<p><b>日期按选约日归并：</b>例如09-07夜盘指09-07 21:00至09-08 02:30，用09-07早上已确定的代码；并非交易所09-07交易日的前一夜。09-11周五夜盘跨至09-12周六，交易所归属09-14。这样避免用早上选约回溯交易此前夜盘。</p>',
       '<p>日盘仍09:30开始。夜盘21:00开始，估值按原规则预热并检查新鲜度，没有再用夜盘后续信息重选代码。每天固定合约到晚上可能已不处于早上的最佳状态，本轮用于隔离时段影响。真实休市10:15/11:30/15:00/02:30按用户研究口径整笔排除未平尾单含费；午夜00:00不中断、不平仓、不重置成交量。</p>',
       '<p>“日盘＋夜盘”是同一账户连续运行到次日02:30的真实回放，日盘三段亦单独重跑。全部日盘路径需复现旧账本；日盘分段循环拼接及日夜循环拼接需与完整运行一致。普通/严格都是L1证据成交假设，不是真实排队。以下净收益单位元，不包含被排除的尾仓毛浮风险。</p>']
    h+=['<h2>先看结论</h2><p><b>时段限制会明显改变结果；本轮更值得继续验证的是下午，不是直接增加夜盘。</b>1%靠山快撤仅做下午，先多/先空五日分别309.40/365.20元，四日盈利、一日零闭环，均无排除尾单；严格成交为469.40/265.20元。完整日盘对应415.40/47.80元，下午过滤改善了空头和两侧回撤，但多头总利润下降。</p>',
        '<p>1.5%估值耐心仅下午为342.60/582.20元，完整日盘为−291.40/544.80元；1.5%靠山快撤下午两侧仍亏，2%机会稀少。不能把“下午好”外推到所有门槛与规则，也不能把五个开发日当成样本外验证。</p>',
        '<h2>1%仅下午：逐日净收益</h2>',table(['选约日期','估值耐心先多','估值耐心先空','靠山快撤先多','靠山快撤先空'],[
            [d,*[fmt(groups[f'pm_{mode}_{policy}_bps100_through0']['daily'][i]) for policy in ('value','backer_fast') for mode in ('long','short')]] for i,d in enumerate(DATES)],'afternoon_daily')]
    for b in (100,150,200):
        rr=[]
        for policy in ('value','backer_fast'):
            for mask,label in MASK_LABELS.items():
                l=groups[f'{mask}_long_{policy}_bps{b}_through0'];s=groups[f'{mask}_short_{policy}_bps{b}_through0']
                rr.append([LABELS[policy],label,fmt(l['pnl_cny']),fmt(s['pnl_cny']),l['normal_cycles'],s['normal_cycles'],
                    l['virtual_cycles'],s['virtual_cycles'],fmt(l['removed_tail_gross_cny']),fmt(s['removed_tail_gross_cny'])])
        h+=['<h2>'+f'{b/100:g}%：估值耐心与靠山快撤'+'</h2>',table(['策略','允许时段','先多收益','先空收益','先多正常循环','先空正常循环','先多尾单','先空尾单','先多尾仓原毛值','先空尾仓原毛值'],rr,f'overview{b}')]
    plt.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei','DejaVu Sans'];plt.rcParams['axes.unicode_minus']=False
    fig,axes=plt.subplots(2,2,figsize=(12,7),constrained_layout=True)
    for i,policy in enumerate(('value','backer_fast')):
        for j,(mode,label) in enumerate((('long','先做多'),('short','先做空'))):
            ax=axes[i,j]
            for mask,color in [('day','#237aad'),('night','#bc7137'),('both','#39936b')]:
                s=groups[f'{mask}_{mode}_{policy}_bps100_through0']
                ax.plot(range(6),np.r_[0,np.cumsum(s['daily'])],color=color,label=MASK_LABELS[mask],marker='o',ms=3)
            ax.set_title('1% '+LABELS[policy]+' · '+label,loc='left');ax.set_ylabel('累计研究净收益 / 元')
            ax.set_xticks(range(6),['起点','09-07','09-08','09-09','09-10','09-11']);ax.grid(alpha=.15);ax.legend()
    pic=OUT/'黄金期权_日夜时段累计收益.png';fig.savefig(pic,dpi=140);plt.close(fig)
    h+=['<h2>按选约日期累计收益</h2><p>日终点累计净收益，包含对应当晚至次日02:30；图不体现完整盘中回撤。</p><img alt="黄金期权日盘夜盘累计收益" src="data:image/png;base64,'+base64.b64encode(pic.read_bytes()).decode()+'">']
    for b in (100,150,200):
        for strict in (0,1):
            rr=[]
            for policy,name in LABELS.items():
                for mask,label in MASK_LABELS.items():
                    for mode,dl in (('long','先多'),('short','先空')):
                        s=groups[f'{mask}_{mode}_{policy}_bps{b}_through{strict}']
                        rr.append([name,label,dl,*map(fmt,s['daily']),fmt(s['pnl_cny']),s['normal_cycles'],s['virtual_cycles'],fmt(s['max_drawdown_cny']),fmt(s['removed_tail_gross_cny'])])
            h+=['<details><summary>'+f'{b/100:g}% '+('普通' if not strict else '严格穿价')+'：九规则全部逐日对照</summary>',
                table(['策略','时段','方向','09-07','09-08','09-09','09-10','09-11','总收益','正常循环','尾单','最大回撤','尾仓原毛值'],rr,f'daily{b}_{strict}'),'</details>']
    h+=['<h2>每张合约在各时段的行情</h2><p>成交量为对应时段可观测累计增量，单位手；不跨真实休市累计，夜盘跨午夜连续。均价/绝对价差单位元每克，按有效报价驻留时间加权，最长60秒、下一帧中断。覆盖不足的市场均值不能代表完整时段。</p>',
        table(['选约日期','合约','时段','成交量/手','均价','平均绝对价差','平均相对价差','有效报价时间覆盖','≥1%时间','≥1.5%时间','≥2%时间'],[
            [r['date'],r['code'],MASK_LABELS[('am1','am2','pm','night')[r['session']]],r['volume_contracts'],fmt(r['mean_mid']) if r['mean_mid'] is not None else '缺失',
             f'{r["mean_spread"]:.4f}' if r['mean_spread'] is not None else '缺失',f'{r["mean_relative_pct"]:.2f}%' if r['mean_relative_pct'] is not None else '缺失',f'{r["coverage_pct"]:.2f}%',
             *[f'{r["eligible_time_pct"][str(b)]:.2f}%' if r['eligible_time_pct'][str(b)] is not None else '缺失' for b in (100,150,200)]] for r in market],'market'),
        '<h2>夜盘数据末尾覆盖</h2><p>末报价距离02:30的秒数越大，排除尾仓的浮值参考越陈旧；不把陈旧盘口当作02:30可以成交的价格。</p>',
        '<p>周五后半夜被QMT标成周一00:00—02:30；经期货和全部四张期权的累计量额连续性核验，将这部分回放时钟减两天。原始时间戳、初次不完整结果与修正审计均保留。周五修正账户使用calendar_r2标识。</p>',
        table(['选约日期','代码','原始帧数','末帧距离02:30/秒'],[[r['selection_date'],r['code'],r['rows'],fmt(r['last_quote_age_seconds']) if r['last_quote_age_seconds'] is not None else '缺失'] for r in read(OUT/'friday_calendar_revision/night_coverage.json')],'coverage'),
        '<p>夜盘330分钟，三个日盘交易窗口合计195分钟，原始总收益还受时长、交易次数和合约状态变化影响，不能把总收益比直接当成每单位风险优势。所有结果仍是五个开发日期；不按事后每天最优时段拼接策略。</p>',
        '<p><a href="comparison.json">全部时段与日度汇总</a> · <a href="results.json">逐合约账户摘要</a> · <a href="market_activity.json">时段成交量和价差</a> · <a href="verification.json">执行核验</a></p></main></html>']
    (OUT/'黄金期权_日盘夜盘及分时段比较.html').write_text(''.join(h),'utf-8')


if __name__=='__main__':main()
