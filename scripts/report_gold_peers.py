"""Human-readable option linkage ablation, all paired daily results."""
import base64
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from probe_gold_peers import OUT,BASE,DATES,read,write,POLICIES,m,WORK,digest
from probe_gold_two_mode import table,fmt

LABELS={'baseline':'原策略','future_only':'增加期货十秒重估','peer_cancel':'增加期权联动开仓过滤及撤单','peer_exit':'联动再加主动退出'}
POLICY={'value':'估值耐心','backer_fast':'靠山快撤'}
MASK={'day':'完整日盘','pm':'仅13:30—15:00'}


def main():
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen')
    groups=read(OUT/'comparison.json');sels=read(OUT/'selections.json');coverage=read(OUT/'signal_coverage.json')
    h=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>黄金期权之间的联动预警</title><style>body{font-family:"Microsoft YaHei",sans-serif;background:#f3f5f9;color:#243249;margin:0}main{max-width:1550px;margin:auto;padding:24px}p{line-height:1.8}table{border-collapse:collapse;background:white;width:100%;font-size:13px}td,th{padding:9px;text-align:right;white-space:nowrap;border-bottom:1px solid #dce2eb}th{background:#e5edf8}td:first-child,th:first-child{text-align:left}.scroll{overflow:auto;max-height:700px}summary{cursor:pointer;padding:14px}img{max-width:100%}</style><main>',
      '<h1>期权间联动：能否增加有效的撤单预警？</h1>',
      '<p><b>初步结果：估值耐心先多的开仓过滤和撤单有改善，先空退化；直接强平更差。尚不能把联动信号作为统一强平条件。</b>五个开发日、1%价差门槛，沿用此前每日两个新选目标，日盘和仅下午分别运行；先多/先空各自独立。此轮聚焦估值耐心和靠山快撤两种已有策略，没有重新测试其余七种或1.5%/2%、夜盘。</p>',
      '<p>日盘估值先多：原1262.40元，新增期货十秒重估1532.60元，期权联动1696元，比期货对照多163.40元；严格成交952.60→1222.80→1506.20。普通最大回撤3436.90→3173.50→2930.10元。先空原4028→联动3641.60元；靠山快撤两侧联动撤单收益没有改变。主动退出让估值先多降至219元。</p>',
      '<h2>实际规则</h2><p>每天仅用09:00—09:30选三张同一标的、同一到期、同一认购/认沽的邻近期权。合格报价行比例≥90%、中位相对价差≤3%、可见成交量增量≥20手；在合格者的八个最近行权价中取前缀成交量前三，不用当日未来涨跌或收益挑选。目标及旧C960/C952排除。</p>',
      '<p>用期权盘口中价和不晚于该盘口的期货报价，反算各自Black隐含波动率。固定十秒前的目标IV锚：期货对照只更新期货价格；联动版本再加入各邻居过去十秒的IV变化，分别重算目标参考价格。至少两个邻居一致指向目标不利方向超过两跳（每手40元），且中位预测同样超过两跳，才暂停新开/申请撤单。目标已跟上、差距消失则解除；不直接复制不同价格期权的涨跌幅。</p>',
      '<p>十秒、两跳、三邻居、两票和两秒新鲜度都是首轮研究参数。目标、邻居现值/十秒锚与配对期货必须新鲜且同连续时段；不足两邻居时不加新动作，原策略照常。期货对照也限定在同样邻居数据完整的样本上，以隔离新增信息作用。该因子包含相对IV偏离，不能把每次报警都说成其他期权实际先跌。</p>',
      '<p>时间戳相同，先处理目标行情和原期货策略，再交付新联动信号。撤单保留此前聚合成交区间的歧义；风险平仓等下一目标有效帧，先结算旧区间再按当前对手价执行。半秒级快照并非逐笔/到达日志，不能证明同一帧内谁先谁后或真实排队成交。</p>',
      '<p>这轮为单合约交易的外部风控因子，不是多腿套利。行权价单调性、凸性、看涨看跌与期货的约束可作进一步校验；美式期权不能无条件套用欧式平价等式，盘口偏离也不等于费用后可同时成交套利。</p>',
      '<p>费用1.70元/边、额外延迟0、每合约最多1手不变。按用户研究口径排除休市未闭合循环及费用；保留尾仓对手价毛浮风险。估值先多原/期货/联动三版排除尾毛浮亏均3940元，不能把1696元当作强制清仓后可兑现收益。每合约25万元是研究预算，空头20%名义保证金代理非实际保证金。</p>']
    for strict in (0,1):
        rr=[]
        for mask in MASK:
            for policy in POLICIES:
                for mode,dl in [('long','先多'),('short','先空')]:
                    for variant in m.VARIANTS:
                        s=groups[f'{mask}_{policy}_{mode}_{variant}_through{strict}']
                        rr.append([MASK[mask],POLICY[policy],dl,LABELS[variant],*map(fmt,s['daily']),fmt(s['pnl_cny']),fmt(s['max_drawdown_cny']),s['normal_cycles'],s['virtual_cycles'],fmt(s['removed_tail_gross_cny']),s['peer_cancels'],s['peer_exits']])
        h+=['<h2>'+('普通' if not strict else '严格穿价')+'成交：全部日度</h2>',table(['时段','策略','方向','新增规则',*['09-'+d[-2:] for d in DATES],'五日合计','最大回撤','正常循环','尾单','排除尾毛浮值','新增撤单请求','新增主动退出'],rr,f'daily{strict}')]
    plt.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei'];plt.rcParams['axes.unicode_minus']=False
    fig,axes=plt.subplots(2,2,figsize=(12,7),constrained_layout=True)
    for i,policy in enumerate(POLICIES):
        for j,(mode,dl) in enumerate([('long','先多'),('short','先空')]):
            ax=axes[i,j]
            for variant in m.VARIANTS:
                s=groups[f'day_{policy}_{mode}_{variant}_through0'];ax.plot(range(6),np.r_[0,np.cumsum(s['daily'])],marker='o',ms=3,label=LABELS[variant])
            ax.set_title(POLICY[policy]+' · '+dl,loc='left');ax.set_ylabel('累计研究净收益 / 元');ax.set_xticks(range(6),['起点','09-07','09-08','09-09','09-10','09-11']);ax.grid(alpha=.15);ax.legend(fontsize=8)
    pic=OUT/'黄金期权联动预警累计收益.png';fig.savefig(pic,dpi=140);plt.close(fig)
    h+=['<h2>日盘累计净收益</h2><p>按日终点绘制，完整盘中回撤另见表。</p><img alt="黄金期权联动预警曲线" src="data:image/png;base64,'+base64.b64encode(pic.read_bytes()).decode()+'">']
    selrows=[]
    for date,values in sels.items():
        for code,s in values.items():
            cov=coverage[f'{date}_{code}'];candidates={r['code']:r for r in s['candidates']}
            for peer in s['peers']:
                r=candidates[peer];selrows.append([date,code,peer,r['volume'],f'{r["median_relative_spread"]*100:.3f}%',f'{cov["ready_fraction"]*100:.2f}%'])
    h+=['<h2>每天的目标与参照合约</h2><p>成交量和价差仅为选择时09:00—09:30前缀；可用比例按全日联合事件数量计算，非时间覆盖率。</p>',table(['日期','交易目标','只读参照','前半小时成交量/手','前缀中位相对价差','目标信号可用事件比例'],selrows,'neighbours')]
    source=WORK/'reports/gold_reselection_daily_detail_20260914_v1/daily_contract_details.json'
    market=[r for r in read(source) if r['bps']==100]
    write(OUT/'target_market_context.json',dict(source=str(source),source_sha256=digest(source),rows=market))
    account=read(OUT/'results.json');mr=[]
    for r in market:
        vals=[]
        for variant in m.VARIANTS:
            for mode in ('long','short'):
                key=f'{r["date"]}_{r["code"]}_day_value_{mode}_{variant}_through0';vals.append(fmt(account[key]['pnl_cny']))
        d=r['day'];mr.append([r['date'],r['code'],d['volume_contracts'],f'{d["mean_mid"]:.3f}',f'{d["mean_spread"]:.4f}',f'{d["mean_relative_pct"]:.3f}%',*vals])
    h+=['<h2>逐合约：估值耐心的收益与市场情况</h2><p>市场成交量为09:00—15:00日盘，不含夜盘；均价/绝对价差单位元每克，相对价差逐帧除中价再按有效驻留时间加权。策略仍09:30开始，原数据另存post0930口径。以下收益单位元，普通成交。</p>',
        table(['日期','目标合约','日盘成交量/手','均价','绝对价差','相对价差',*[LABELS[v]+dl for v in m.VARIANTS for dl in ('先多','先空')]],mr,'target_market')]
    a=read(OUT/'warning_attribution.json');ar=[]
    for key,stats in a.items():
        for kind in ('future_only','peer_cancel'):
            s=stats[kind];ar.append([key,LABELS[kind],s['ahead_losses'],s['ahead_winners'],fmt(s['baseline_net_of_flagged_cycles']),s['ambiguous']])
    h+=['<h2>原订单存续期间，提前报警对应什么交易？</h2><p>只核对原策略正常闭环中，从挂单创建到成交之前出现的报警：必须不晚于成交帧的前一快照才称明确在区间之前。未涵盖开仓之前就存在的报警，也未把“被标记的旧亏损”直接计为新策略收益；真正收益来自完整重跑。</p>',
        table(['原账户','预警来源','提前标记亏损笔数','提前标记盈利笔数','被标记旧交易净值','处于成交区间内不确定'],ar,'attribution'),
        '<p>估值先多联动在旧订单存续窗提前标记4笔亏损、2笔盈利；相对期货对照独有2笔亏损和1笔盈利。先空则标记1笔亏损、7笔盈利，说明同样报警会误伤盈利机会。统计只描述这五天，不证明稳定预测能力。</p>',
        '<p><a href="comparison.json">全部汇总</a> · <a href="results.json">逐合约账户</a> · <a href="baseline_warning_audit.json">逐笔预警审计</a> · <a href="verification.json">执行核验</a></p></main></html>']
    (OUT/'黄金期权_期权间联动预警对照.html').write_text(''.join(h),'utf-8')


if __name__=='__main__':main()
