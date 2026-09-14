"""Market activity and new-contract daily curves; no strategy decisions."""
import base64
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from zhaiquant.commodity_dadao_research import load_frame
from zhaiquant import gold_direction_research as g
from probe_gold_reselection import OUT,DATES,read,write,table,fmt
from zhaiquant.gold_history_validation import timestamp


def main():
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen output')
    terms=read(OUT/'catalog_terms.json')['details'];markets=[]
    for date in DATES:
        selection=read(OUT/date/'selection.json');codes=sorted({c for s in selection['scenarios'].values() for c in s['selected']})
        for code in codes:
            f=pd.read_pickle(OUT/date/'full_inputs'/f'{code}.pkl')
            es,_,_=load_frame(f,code=code,date=date,detail=terms[code])
            boundaries=[timestamp(date,t) for t in ('101500','113000','150000')]
            weights=[];spreads=[];mids=[]
            for i,e in enumerate(es):
                next_ts=es[i+1].ts if i+1<len(es) and es[i+1].session==e.session else boundaries[e.session]
                lo=max(e.ts,timestamp(date,'093000'));hi=min(next_ts,boundaries[e.session],e.ts+60000)
                if hi>lo and g.valid(e):
                    weights.append(hi-lo);spreads.append(2*(e.ask-e.bid)/(e.ask+e.bid)*100);mids.append((e.ask+e.bid)/200000)
            row=dict(date=date,code=code,day_volume=sum(e.quantity for e in es),
                post0930_volume=sum(e.quantity for e in es if e.ts>=timestamp(date,'093000')),
                post0930_mean_mid=float(np.average(mids,weights=weights)),
                post0930_relative_mean_pct=float(np.average(spreads,weights=weights)))
            markets.append(row)
    write(OUT/'market_activity.json',markets)
    groups=read(OUT/'comparison.json')
    plt.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei','DejaVu Sans'];plt.rcParams['axes.unicode_minus']=False
    fig,axes=plt.subplots(3,2,figsize=(12,10),constrained_layout=True)
    for i,(policy,label) in enumerate([('trend','趋势基线'),('trend_fast','快过滤＋撤单'),('value','估值耐心')]):
        for j,(mode,direction) in enumerate([('long','先做多期权'),('short','先做空期权')]):
            ax=axes[i,j]
            for b,color in [(100,'#2477af'),(150,'#cc7631'),(200,'#33916d')]:
                s=groups[f'{mode}_{policy}_bps{b}_through0']
                ax.plot(range(6),np.r_[0,np.cumsum(s['daily'])],color=color,marker='o',ms=3,label=f'{b/100:g}%')
            ax.set_title(label+' · '+direction,loc='left');ax.set_xticks(range(6),['起点','09-07','09-08','09-09','09-10','09-11']);ax.grid(alpha=.15);ax.legend();ax.set_ylabel('累计研究净收益 / 元')
    p=OUT/'新合约_三组规则多空累计收益.png';fig.savefig(p,dpi=135);plt.close(fig)
    block='''<h2>换约后的结果</h2><p>这次使用9张不同的新合约，按日按门槛选择，旧两张完全没有交易。估值耐心版1%先做多+1262.40、先做空+4028.00；1.5%为−291.40/+544.80；2%为−206.80/+76.60。严格穿价对应1%+952.60/+4031.80。不是所有规则都盈利：1%趋势多头−5883.20、空头−1144.40，宽价差和更活跃本身不足以保证兑现收益。</p>
<p>1%估值耐心空头80正常循环、4排除尾单，排除前毛浮值−1340元；多头64正常循环、8尾单，原毛浮值−3940元。空头利润集中在09-10的+3875.40，其中C976一笔+2556.60；不能仅看五日总收益认定稳定。</p>
<p>2%仅09-07选满两张、09-10选中一张，另外三天未选中。三天空缺并非没有2%价差：首30分钟达标时间至少一半的分别有22/16/22张，但都缺少本轮要求的双侧有效成交更新，且多数报价或活动覆盖不足。筛选条件原样保留，不看到结果后放松。</p>'''
    block+='<h2>新合约日终累计收益</h2><p>普通成交、尾单排除口径；空缺日期记零。图是五个日终累计净收益点，盘中回撤在表格另列，不是收益率。</p><img style="width:1150px;max-width:100%" alt="新合约三组规则多空累计研究净收益" src="data:image/png;base64,'+base64.b64encode(p.read_bytes()).decode()+'">'
    block+='<h2>新合约实际日盘活动</h2><p>日盘成交手数为09:00—15:00可观测累计成交增量，不含夜盘；均价与平均相对价差是09:30后的有效报价驻留时间加权结果，下一帧中断、最长60秒、不跨休市。这是回测后的市场描述，不参与选约。</p>'
    block+=table(['日期','完整合约','日盘成交手数','09:30后成交手数','09:30后均价/元每克','09:30后平均相对价差'],[
        [m['date'],m['code'],m['day_volume'],m['post0930_volume'],fmt(m['post0930_mean_mid']),f'{m["post0930_relative_mean_pct"]:.2f}%'] for m in markets],'market')
    report=OUT/'黄金期权_按百分比每日换约回测.html';text=report.read_text('utf-8')
    text=text.replace('<h2>普通成交</h2>',block+'<h2>普通成交</h2>',1);report.write_text(text,'utf-8')


if __name__=='__main__':main()
