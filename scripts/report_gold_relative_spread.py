"""Add concise findings and daily cumulative PnL charts to the full matrix."""
from pathlib import Path
import base64
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from probe_gold_relative_spread import OUT,OLD,read


def main():
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen output')
    groups=read(OUT/'comparison.json');old=read(OLD/'comparison.json')
    plt.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei','DejaVu Sans'];plt.rcParams['axes.unicode_minus']=False
    fig,axes=plt.subplots(1,2,figsize=(12,4),constrained_layout=True)
    for ax,mode,label in zip(axes,['long','short'],['先做多','先做空']):
        for b,color,style in [(0,'#89919c','--'),(100,'#2575ac','-'),(150,'#c16e31','-'),(200,'#398669',':')]:
            s=old[f'{mode}_value_through0'] if not b else groups[f'{mode}_value_bps{b}_through0']
            ax.plot(range(5),np.r_[0,np.cumsum(s['daily'])],label='原8跳' if not b else f'{b/100:g}%',color=color,linestyle=style,marker='o',markersize=3)
        ax.set_title('估值耐心 · '+label,loc='left');ax.set_ylabel('累计研究净收益 / 元')
        ax.set_xticks(range(5),['起点','09-07','09-08','09-09','09-10']);ax.grid(alpha=.15);ax.legend()
    p=OUT/'估值耐心_百分比门槛累计收益.png';fig.savefig(p,dpi=150);plt.close(fig)
    image=base64.b64encode(p.read_bytes()).decode()
    block='''<h2>结果解读</h2><p>百分比门槛在这两张合约上没有带来一致的收益提升。趋势多头原−3222.40变1%下−511.00，但快撤多头原−375.40变−507.20，估值耐心多头原+761.20变−57.00。空头趋势原−1084.20变+379.20，估值耐心原+2413.60变+899.20：减少亏损机会，也减少可兑现的盈利机会。</p>
<p>1.5%和2%让大部分规则几乎没有正常闭环；零收益不等于没有开仓或风险。空头趋势在两门槛下均0正常循环、5笔排除尾单，其排除前毛浮值分别−3340/−3360元。估值耐心空头1%有12正常循环、5尾单，排除前毛浮值−2900元；严格穿价净收益+979.20，仍不代表现实平仓可获得该收益。</p>
<p>高门槛并非完全没利润：估值＋快撤＋主动退出空头1.5%/2%均+156.60，但各仅1个正常循环；无法凭这一笔认定有效。靠山三规则在三个门槛下均无成交。完整失败与零成交均保留下表。</p>
<p>C960四天达到1%/1.5%/2%的有效报价时间比例仅1.49%/0.26%/0.12%；C952为7.55%/1.84%/1.40%。因此先把1%保留作比较条件，继续找百分比价差和实际成交机会兼具的合约；不能把原样本中1.5%/2%的稀疏结果直接当成全黄金结论。</p>
<p><a href="黄金期权_其他合约百分比筛选.html">其他黄金合约的1%、1.5%、2%筛选表</a>使用09-11首30分钟的独立观察窗口，含认购与认沽，只是机会筛选，未进行这些新合约的收益回测。</p>'''
    block+='<img style="max-width:100%;width:1100px" alt="估值耐心普通成交，尾单排除口径，日终累计净收益" src="data:image/png;base64,'+image+'"><p>图为四个日终累计净收益点，不是收益率，也不体现全部盘中回撤；1.5%和2%的零收益曲线重合。详细盘中曲线在comparison.json。</p>'
    report=OUT/'黄金期权_相对价差门槛比较.html'
    text=report.read_text('utf-8');text=text.replace('<h2>普通成交</h2>',block+'<h2>普通成交</h2>',1);report.write_text(text,'utf-8')


if __name__=='__main__':main()
