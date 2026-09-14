"""Descriptive gold market comparison; no model or execution changes."""
from pathlib import Path
from bisect import bisect_right
from datetime import datetime
from zoneinfo import ZoneInfo
from html import escape
import base64
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from zhaiquant import gold_direction_research as g
from zhaiquant.commodity_dadao_research import load_frame
from zhaiquant.gold_history_validation import timestamp
from probe_commodity_capital import ROOT,WORK,read,write,digest,unpack,pack
from probe_gold_backer import BASE,DATES,CODES

OUT=WORK/'reports/gold_day_regimes_20260914_v1'
LEDGERS=WORK/'reports/gold_two_mode_20260914_v1'


def sessions(date,start='090000'):
    return [(timestamp(date,start),timestamp(date,'101500')),
            (timestamp(date,'103000'),timestamp(date,'113000')),
            (timestamp(date,'133000'),timestamp(date,'150000'))]


def quantile(values,weights,q):
    ix=np.argsort(values);v=np.asarray(values)[ix];w=np.asarray(weights)[ix]
    return float(v[np.searchsorted(np.cumsum(w),q*sum(w))])


def valid_future_frame(f):
    f=f.sort_values('time',kind='stable').drop_duplicates('time',keep='last')
    # Invalid rows remain in the stream, so a previous valid quote cannot pass through one.
    rows=[]
    for x in f.itertuples():
        ok=0<x.bidPrice[0]<=x.askPrice[0] and min(x.bidVol[0],x.askVol[0])>0
        rows.append((int(x.time),(x.bidPrice[0]+x.askPrice[0])/2 if ok else None))
    return rows


def summarize(date,code,terms,es,fr,start):
    spans=sessions(date,start);et=np.array([e.ts for e in es]);ft=np.array([t for t,p in fr])
    weighted=[];minute=[];ivs=[];segment_rows=[];volume=0;long_gaps=0
    for si,(lo,hi) in enumerate(spans):
        segment=[]
        for i,e in enumerate(es):
            if e.session!=si:continue
            following=es[i+1].ts if i+1<len(es) and es[i+1].session==si else hi
            left=max(lo,e.ts);right=min(hi,following,e.ts+60000)
            if right>left and g.valid(e):
                weighted.append((e,(right-left)/1000));segment.append(e)
                long_gaps += max(0,right-max(left,e.ts+2000))/1000
            if lo<=e.ts<hi:volume+=e.quantity
        grid=[]
        for ts in range(lo,hi,5000):
            idx=bisect_right(et,ts)-1
            if idx<0:continue
            e=es[idx]
            if e.session!=si or not g.valid(e) or ts-e.ts>2000:continue
            fi=bisect_right(ft,e.ts)-1
            future=fr[fi][1] if fi>=0 and e.ts-fr[fi][0]<=2000 else None
            mid=(e.bid+e.ask)/200000
            row=dict(ts=ts,quote_ts=e.ts,session=si,mid=mid,bid=e.bid/100000,ask=e.ask/100000,
                future=future,future_source_ts=fr[fi][0] if future is not None else None,
                iv=None,bid_iv=None,ask_iv=None,delta=None)
            if future is not None:
                maturity=max(1/365,(timestamp(terms['ExpireDate'],'150000')-e.ts)/(365*86400000))
                for name,price in [('iv',mid),('bid_iv',row['bid']),('ask_iv',row['ask'])]:
                    row[name]=g.implied_vol(price,future,terms['OptExercisePrice'],maturity)
                if row['iv'] is not None:
                    row['delta']=g.black_call(future,terms['OptExercisePrice'],maturity,row['iv'])[1]
            grid.append(row);ivs.append(row)
            if (ts-lo)%60000==0:minute.append(row)
        segment_rows.append(dict(session=si,start_ts=lo,end_ts=hi,
            mid_start=(segment[0].bid+segment[0].ask)/200000 if segment else None,
            mid_end=(segment[-1].bid+segment[-1].ask)/200000 if segment else None,
            future_start=next((p for t,p in fr if lo<=t<hi and p is not None),None),
            future_end=next((p for t,p in reversed(fr) if lo<=t<hi and p is not None),None)))
    ev=[e for e,w in weighted];w=np.array([w for e,w in weighted]);mid=np.array([(e.bid+e.ask)/200000 for e in ev]);spread=np.array([(e.ask-e.bid)/100000 for e in ev]);tick=np.array([(e.ask-e.bid)/2000 for e in ev])
    avg=lambda x:float(np.average(x,weights=w))
    ir=[x for x in ivs if x['iv'] is not None]
    returns=[];abs_moves=[];freturns=[]
    for a,b in zip(minute,minute[1:]):
        if a['session']==b['session'] and b['ts']-a['ts']==60000:
            returns.append(np.log(b['mid']/a['mid']));abs_moves.append(abs(b['mid']-a['mid']))
            if a['future'] is not None and b['future'] is not None:freturns.append(np.log(b['future']/a['future']))
    first,last=ev[0],ev[-1]
    iv_start=[x['iv'] for x in ir if x['ts']<spans[0][0]+300000]
    iv_end=[x['iv'] for x in ir if x['ts']>=spans[-1][1]-300000]
    total=sum(hi-lo for lo,hi in spans)/1000
    res=dict(date=date,code=code,window_start=start,
        option_start=(first.bid+first.ask)/200000,option_start_ts=first.ts,
        option_end=(last.bid+last.ask)/200000,option_end_ts=last.ts,
        average_mid=avg(mid),low_mid=float(mid.min()),high_mid=float(mid.max()),
        net_change_pct=100*((last.bid+last.ask)/(first.bid+first.ask)-1),
        average_spread=avg(spread),average_spread_ticks=avg(tick),median_spread_ticks=quantile(tick,w,.5),
        spread_p90_ticks=quantile(tick,w,.9),average_relative_spread_pct=avg(spread/mid*100),
        spread8_time_pct=float(w[tick>=8].sum()/w.sum()*100),
        average_bid_qty=avg([e.bid_qty for e in ev]),average_ask_qty=avg([e.ask_qty for e in ev]),
        volume_increment_contracts=volume,observed_seconds=float(w.sum()),time_coverage_pct=float(w.sum()/total*100),
        quote_age_over2_seconds=long_gaps,
        mean_iv_pct=float(np.mean([x['iv'] for x in ir])*100),
        iv_open5min_pct=float(np.mean(iv_start)*100) if iv_start else None,
        iv_end5min_pct=float(np.mean(iv_end)*100) if iv_end else None,
        mean_iv_bidask_width_pp=float(np.mean([x['ask_iv']-x['bid_iv'] for x in ir if x['bid_iv'] is not None and x['ask_iv'] is not None])*100),
        mean_delta=float(np.mean([x['delta'] for x in ir])),iv_samples=len(ir),iv_grid_slots=round(total/5),
        one_minute_rms_pct=float(np.sqrt(np.mean(np.square(returns)))*100),
        mean_abs_one_minute_move=float(np.mean(abs_moves)),
        future_one_minute_rms_pct=float(np.sqrt(np.mean(np.square(freturns)))*100),
        one_minute_pairs=len(returns),segments=segment_rows)
    assert 0<res['time_coverage_pct']<=100.000001
    assert abs(res['average_spread']/0.02-res['average_spread_ticks'])<1e-8
    return res,ivs


def table(headers,rows,id=''):
    return '<div class="scroll"><table id="'+id+'"><thead><tr>'+''.join('<th>'+escape(str(x))+'</th>' for x in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows)+'</tbody></table></div>'


def main():
    OUT.mkdir(exist_ok=True)
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen output')
    details=read(BASE/'catalog_terms.json')['details'];frozen=read(BASE/'result_manifest.json');inputs={};results=[];series={}
    for date in DATES:
        for code in CODES:
            terms=details[code];folder=BASE/date/'full_inputs'
            p=folder/f'{code}.pkl';fp=folder/f'{terms["OptUndlCode"]}.SF.pkl'
            for path in [p,fp]:
                rel=str(path.relative_to(ROOT));assert digest(path)==frozen[rel];inputs[rel]=digest(path)
            raw=pd.read_pickle(p);lo=timestamp(date);raw=raw[(raw.time>=lo)&(raw.time<timestamp(date,'150000'))]
            es,_,_=load_frame(raw,code=code,date=date,detail=terms);fr=valid_future_frame(pd.read_pickle(fp))
            for start in ['090000','093000']:
                res,s=summarize(date,code,terms,es,fr,start);results.append(res)
                if start=='090000':series[date+'_'+code]=s
            print(date,code,'complete',flush=True)
    write(OUT/'metrics.json',results);write(OUT/'input_manifest.json',inputs)
    pack(OUT/'five_second_series.json.gz',series)
    # Link the current long-mode trades to the observed market regime, without filtering them.
    attribution=[]
    for date in DATES:
        for code in CODES:
            for policy in ['trend','trend_fast','value']:
                r=unpack(LEDGERS/'ledgers'/f'{date}_{code}_long_{policy}_through0.json.gz')
                normal=[c for c in r['cycles'] if c['exit_kind']!='virtual_cost_close']
                for session,limits in enumerate(sessions(date)):
                    cs=[c for c in normal if limits[0]<=c['entry_ts']<limits[1]]
                    attribution.append(dict(date=date,code=code,policy=policy,session=session,cycles=len(cs),
                        net_cny=sum(c['net_cents'] for c in cs)/100,winners=sum(c['net_cents']>0 for c in cs)))
    write(OUT/'long_session_pnl.json',attribution)
    plt.rcParams['font.sans-serif']=['Microsoft YaHei','SimHei','DejaVu Sans'];plt.rcParams['axes.unicode_minus']=False
    fig,axes=plt.subplots(2,2,figsize=(13,7),sharex=True,constrained_layout=True)
    colors=['#cc5948','#dc9a36','#238a9e','#3778ca']
    for j,code in enumerate(CODES):
        for date,color in zip(DATES,colors):
            s=series[date+'_'+code]
            for si in range(3):
                part=[x for x in s if x['session']==si]
                x=[(r['ts']-timestamp(date))/3600000 for r in part]
                axes[0,j].plot(x,[r['mid'] for r in part],color=color,lw=.9,label=date[4:6]+'-'+date[6:] if si==0 else None)
                axes[1,j].plot(x,[r['iv']*100 if r['iv'] is not None else np.nan for r in part],color=color,lw=.9)
        axes[0,j].set_title(code,loc='left');axes[0,j].set_ylabel('期权盘口中价 / 元每克');axes[1,j].set_ylabel('中价隐含波动率 / 年化%')
        axes[0,j].legend(ncol=2);axes[1,j].set_xticks([0,1,2,3,4,5,6],['09:00','10:00','11:00','12:00','13:00','14:00','15:00'])
        for ax in axes[:,j]:ax.grid(alpha=.15);ax.axvline(.5,color='#999',linestyle='--',lw=.6)
    pic=OUT/'四天价格与隐含波动率.png';fig.savefig(pic,dpi=140);plt.close(fig)
    h=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>黄金四天行情差异</title><style>body{font-family:"Microsoft YaHei",sans-serif;background:#f3f5f9;color:#243249}main{max-width:1420px;margin:auto;padding:22px}p{line-height:1.8}table{width:100%;border-collapse:collapse;background:white;font-size:13px}td,th{padding:9px;text-align:right;white-space:nowrap;border-bottom:1px solid #dae0e9}th:first-child,td:first-child{text-align:left}th{background:#e5edf8}.scroll{overflow:auto}img{width:100%}summary{cursor:pointer;padding:12px}</style><main><h1>9月7、8日与9月9、10日：行情究竟有什么区别</h1>',
       '<p>固定C960/C952两个认购。所有价格是买卖一中价，单位元/克；平均价差按有效报价驻留时间加权，跨休市不累计，单报价最多延续60秒。不是历史末价，平均价差也不是每笔实际能赚到的空间。</p>',
       '<h2>最明显的区别是方向，短时波动和价差没有同样划分赚亏</h2>',
       '<p>策略09:30开始，不能把之前的行情直接算成策略持仓影响。在09:30以后，C960四天首尾分别−2.38%、−14.62%、+20.80%、+8.89%；C952为−3.83%、−14.08%、+20.74%、+8.12%。同月期货分别−1.83、−6.61、+6.44、+2.38元/克。前两天方向总体不利于先买后卖，后两天更容易在买入后的恢复/抬升中完成卖出。</p>',
       '<p>但这不是单凭当天涨跌就能解释全部利润。9月8日估值耐心多头在下午期货下跌约4元/克时仍赚886元；9月10日原趋势主版前两段分别−127.20/−90.60，下午+964.80才使全天转正。必须看实际开仓后的小段路径、成交前是否被逆向选择以及退出成交机会。</p>',
       '<p>C952在09:30后的平均价差四天为9.21/7.54/6.29/8.20跳：9月7日最宽却亏，9月9日最窄却赚。C960在9月8、9日均约4.3跳，但盈亏相反。宽价差只是候选空间，不是可兑现利润。9月9日达到8跳的时间更少，交易次数也会减少。</p>',
       '<p>后两天平均隐含波动率反而更高：C960约27.14%/27.54%，前两天25.75%/26.03%；C952约26.36%/26.93%，前两天25.20%/25.46%。这不是实际波动小。策略区间一分钟中价变动均方根，C960四天约0.85%/0.84%/0.91%/0.90%，C952约0.85%/0.81%/0.85%/0.88%，没有前两天明显更躁的证据。IV水平高低与IV当日变化不同：9月8日IV也上升，仍抵不过期货下跌。</p>',
       '<p>9月9日同一期权价格明显更低，是前夜黄金下移后合约更偏虚值，随后日盘反弹；不能把它当作永远稳定的固定合约属性，也不能据四天就确认低权利金或高IV是盈利过滤器。当前结果支持继续研究当时可见的方向、入场后恢复与报价风险，而不支持事后按日涨跌选交易。</p>']
    for start,label in [('090000','全日盘09:00—15:00'),('093000','策略观察区间09:30—15:00')]:
        a=[r for r in results if r['window_start']==start]
        h+=['<h2>'+label+'</h2>',table(['日期','合约','起始→末尾中价','均价','中价低—高','首尾涨跌','平均价差/跳','相对价差','≥8跳时间','平均IV','1分钟实际波动','可观测成交手数'],[
            [r['date'],r['code'],f'{r["option_start"]:.2f} → {r["option_end"]:.2f}',f'{r["average_mid"]:.2f}',f'{r["low_mid"]:.2f}—{r["high_mid"]:.2f}',f'{r["net_change_pct"]:+.2f}%',
             f'{r["average_spread"]:.3f} / {r["average_spread_ticks"]:.2f}',f'{r["average_relative_spread_pct"]:.2f}%',f'{r["spread8_time_pct"]:.1f}%',f'{r["mean_iv_pct"]:.2f}%',
             f'{r["one_minute_rms_pct"]:.3f}%',r['volume_increment_contracts']] for r in a],start)]
    h+=['<p>IV由每5秒的新鲜期权中价及不晚于该期权源时刻、相差≤2秒的同月期货反推，按实际2026-09-23到期日、Black认购近似估计。是年化报价隐含波动，不是当天实际涨跌。1分钟实际波动是连续时段相邻新鲜分钟中价对数变动的均方根，不年化、不跨休市连接，包含盘口噪声。两种波动率不能直接比大小。</p>',
        '<img alt="四天价格与隐含波动率" src="data:image/png;base64,'+base64.b64encode(pic.read_bytes()).decode()+'">',
        '<h2>期货及期权的三个连续时段</h2>',table(['日期','合约','时段','期货起→末','期货变化','期权中价起→末','期权变化'],[
            [r['date'],r['code'],['09:00—10:15','10:30—11:30','13:30—15:00'][s['session']],
             f'{s["future_start"]:.2f} → {s["future_end"]:.2f}',f'{s["future_end"]-s["future_start"]:+.2f}',
             f'{s["mid_start"]:.2f} → {s["mid_end"]:.2f}',f'{s["mid_end"]-s["mid_start"]:+.2f}']
            for r in results if r['window_start']=='090000' for s in r['segments']]),
        '<h2>同一时段做多实际研究收益</h2><p>按开仓所在时段分组，只含正常闭环；尾单已依用户口径整笔排除。不是把全天涨跌当作当时已经知道的开仓信号。</p>',table(['日期','合约','多头规则','时段','正常循环','盈利数','净收益'],[
            [r['date'],r['code'],r['policy'],r['session']+1,r['cycles'],r['winners'],f'{r["net_cny"]:.2f}'] for r in attribution]),
        '<h2>口径与覆盖</h2>',table(['日期','合约','09:00报价时间覆盖','IV样本/计划','IV买卖报价平均宽度/百分点','首5分钟IV→末5分钟IV','平均Delta'],[
            [r['date'],r['code'],f'{r["time_coverage_pct"]:.3f}%',f'{r["iv_samples"]}/{r["iv_grid_slots"]}',f'{r["mean_iv_bidask_width_pp"]:.3f}',f'{r["iv_open5min_pct"]:.2f}% → {r["iv_end5min_pct"]:.2f}%',f'{r["mean_delta"]:.3f}']
            for r in results if r['window_start']=='090000']),
        '<p>前两天下跌、后两天上涨，以及IV与价差变化属于事后市场描述，不证明任何单变量是因果开关。样本只有四天；没有改策略、筛掉交易或把全天结果提前用于开仓。盘口均价与极值会受宽档影响，特别是C952；详细指标、分时曲线与分段盈亏用于交叉复核。</p><p><a href="metrics.json">全部原始指标JSON</a> · <a href="long_session_pnl.json">分时段策略损益</a></p></main></html>']
    (OUT/'黄金期权_四天行情差异.html').write_text(''.join(h),'utf-8')
    for r in results:
        if r['window_start']=='090000':print({k:v for k,v in r.items() if k!='segments'})
    write(OUT/'source_manifest.json',{str(Path(__file__).relative_to(ROOT)):digest(Path(__file__))})


if __name__=='__main__':main()
