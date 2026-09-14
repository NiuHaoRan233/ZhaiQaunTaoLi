"""Descriptive first-30-minute percentage screening; no profit-based selection."""
from pathlib import Path
import numpy as np
import pandas as pd
from probe_gold_relative_spread import OUT,WORK,ROOT,read,write,digest,table,fmt


def main():
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen output')
    original=WORK/'reports/gold_state_20260913_v1'
    audit=WORK/'reports/gold_state_universe_audit_20260913'
    state=read(audit/'result.json');out=[];inputs={}
    start=1789088400000;end=start+1800000
    for code,m in state['rows'].items():
        p=original/'inputs'/f'{code}.pkl'
        if not p.exists():p=audit/f'{code}.pkl'
        if not p.exists():continue
        inputs[str(p.relative_to(ROOT))]=digest(p)
        f=pd.read_pickle(p);f=f[(f.time>=start)&(f.time<end)].sort_values('time',kind='stable').drop_duplicates('time',keep='last')
        if len(f)<2:continue
        ts=f.time.to_numpy(dtype=np.int64)
        bid=np.rint(np.array([x[0] for x in f.bidPrice])*100000).astype(np.int64)
        ask=np.rint(np.array([x[0] for x in f.askPrice])*100000).astype(np.int64)
        bq=np.array([x[0] for x in f.bidVol]);aq=np.array([x[0] for x in f.askVol])
        valid=(bid>0)&(ask>bid)&(bq>0)&(aq>0)
        w=np.maximum(0,np.minimum(np.diff(ts,append=end),60000))*valid
        if w.sum()==0:continue
        dv=np.diff(f.volume.to_numpy(),prepend=f.volume.iloc[0]);da=np.diff(f.amount.to_numpy(),prepend=f.amount.iloc[0])
        last=np.rint(f.lastPrice.to_numpy()*100000).astype(np.int64)
        delta=np.diff(ts,prepend=ts[0]);evidence=(dv>0)&(da>0)&(delta>0)&(delta<=60000)&np.r_[False,valid[:-1]]
        buys=evidence&(last>=np.r_[ask[0],ask[:-1]]);sells=evidence&(last<=np.r_[bid[0],bid[:-1]])
        # Keep prior premium, maturity, depth, data and activity checks. The old
        # absolute executable-edge residence threshold is diagnostic, not a gate.
        reasons=[r for r in m.get('reasons',[]) if r!='few_net_edge_intervals']
        for bps in (100,150,200):
            ok=valid&(2*(ask-bid)*10000>=bps*(ask+bid));prior=np.r_[False,ok[:-1]]
            buy=int((buys&prior).sum());sell=int((sells&prior).sum())
            net_cny=(ask-bid-4000-340)/100  # both quote improvements and round-trip fee
            net_mean=float(np.average(net_cny[ok],weights=w[ok])) if w[ok].sum()>0 else None
            out.append(dict(code=code,bps=bps,original_state_checks_passed=not reasons,reasons=reasons,
                median_premium=m.get('median_premium'),volume_increment=m.get('volume_increment'),
                eligible_time_pct=float(w[ok].sum()/w.sum()*100),eligible_seconds=float(w[ok].sum()/1000),
                prior_eligible_buy_updates=buy,prior_eligible_sell_updates=sell,
                balanced_updates=min(buy,sell),old_net_edge_time_fraction=m.get('net_edge_time_fraction'),
                mean_static_net_space_cny_at_eligible_quotes=net_mean))
    ranked={str(b):sorted([s for s in out if s['bps']==b and s['original_state_checks_passed'] and s['eligible_seconds']>0],
        key=lambda s:(-s['balanced_updates'],-s['eligible_seconds'],s['code'])) for b in (100,150,200)}
    write(OUT/'universe_screen.json',dict(date='20260911',window='09:00 <= t < 09:30',
        catalog_codes=776,preexisting_state_pool=74,observed_codes=len(out)//3,
        selection_rule='descriptive ranking by minimum prior-eligible inferred buy/sell updates, then eligible seconds',
        no_new_contract_backtests=True,rows=out,ranked=ranked))
    write(OUT/'screen_input_manifest.json',inputs)
    h=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>黄金百分比价差候选合约</title><style>body{font-family:Microsoft YaHei;background:#f3f5f9;padding:24px}table{border-collapse:collapse;background:white}td,th{padding:10px;border-bottom:1px solid #ddd;text-align:right}.scroll{overflow:auto}p{line-height:1.8}</style>',
       '<h1>其他黄金合约：1%、1.5%、2%盘口机会筛选</h1>',
       '<p>使用已保存2026-09-11的09:00—09:30数据：776代码目录预筛后的74个主/次主力、实虚值范围候选。保留原权利金2—100元、到期日、有效深度和活跃度检查，去掉原绝对净价差时间门槛，直接统计百分比。以下为观察排序，不是新合约回测收益，也未证明能成交。</p>',
       '<p>“双边较少一侧更新数”取满足百分比的前帧之后、与其买/卖报价相容的成交更新数的较小值；仅L1推断，无法认定真实主动方向或成交队列。按该数降序、达标时间降序排序，各列前10。不是按全日收益选标的，也未使用09:30后的行情。</p>']
    for b in (100,150,200):
        h+=['<h2>'+f'{b/100:g}%'+'</h2>',table(['合约','中位权利金/元每克','首30分钟成交手数','价差达标时间','双边较少一侧更新数','买/卖更新','达标盘口平均扣费改善后空间/元每手'],[
            [s['code'],fmt(s['median_premium']),s['volume_increment'],f'{s["eligible_time_pct"]:.2f}%',s['balanced_updates'],f'{s["prior_eligible_buy_updates"]}/{s["prior_eligible_sell_updates"]}',fmt(s['mean_static_net_space_cny_at_eligible_quotes'])] for s in ranked[str(b)][:10]],str(b))]
    h+=['<p>高百分比可能来自低权利金，小额改善报价与手续费占比也会提高。以上保留原净空间指标在JSON作诊断，尚未以新合约替代四天对照样本。</p><p><a href="universe_screen.json">全部候选与未通过原因</a> · <a href="黄金期权_相对价差门槛比较.html">原双合约四日策略比较</a></p></html>']
    (OUT/'黄金期权_其他合约百分比筛选.html').write_text(''.join(h),'utf-8')
    for b,a in ranked.items():print(b,a[:3])


if __name__=='__main__':main()
