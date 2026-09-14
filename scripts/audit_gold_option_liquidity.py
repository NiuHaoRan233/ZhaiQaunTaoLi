"""Read-only local gold-option liquidity audit; exclude opening cumulative night volume."""
from pathlib import Path
import hashlib
import json
import pickle
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'广义套利'
OUT=WORK/'reports/gold_option_liquidity_audit_20260913'


def audit(path):
    df=pickle.loads(path.read_bytes()).sort_values('time').drop_duplicates('time',keep='last')
    ms=df.time.to_numpy(dtype='int64');seconds=(ms//1000+8*3600)%86400
    sid=np.full(len(ms),-1)
    for i,(a,b) in enumerate([(9*3600,10*3600+15*60),(10*3600+30*60,11*3600+30*60),(13*3600+30*60,15*3600)]):
        sid[(seconds>=a)&(seconds<=b)]=i
    mask=sid>=0;df=df.iloc[np.flatnonzero(mask)];ms=ms[mask];sid=sid[mask]
    vol=df.volume.to_numpy();raw_dv=np.diff(vol,prepend=vol[0]);dv=np.maximum(raw_dv,0)
    assert not (raw_dv<0).any(),'Cumulative reset requires separate treatment'
    bid=np.array([a[0] for a in df.bidPrice]);ask=np.array([a[0] for a in df.askPrice])
    bq=np.array([a[0] for a in df.bidVol]);aq=np.array([a[0] for a in df.askVol])
    valid=(bid>0)&(ask>bid)&(bq>0)&(aq>0)
    dt=np.diff(ms,append=ms[-1])/1000;dt[np.r_[sid[1:]!=sid[:-1],True]]=0
    w=dt*valid;spread=ask-bid
    ratio=np.divide(2*spread,ask+bid,out=np.zeros_like(spread),where=ask+bid>0)*100
    def wm(v):
        idx=np.argsort(v);cum=np.cumsum(w[idx])
        return float(v[idx][np.searchsorted(cum,cum[-1]/2)]) if cum[-1]>0 else None
    assert dv.sum()==vol[-1]-vol[0]
    return dict(date=path.stem.split('_')[1],day_volume_increment=int(dv.sum()),
        trade_update_frames=int((dv>0).sum()),median_spread=round(wm(spread),4),
        median_spread_pct=round(wm(ratio),3),
        existing_edge_rule_spread_time_pct=round(float(w[spread*1000-40-3.4 >= 20-1e-7].sum()/w.sum()*100),2),
        wide_ge_half_pct_time_pct=round(w[ratio>=.5].sum()/w.sum()*100,2),
        book_coverage_pct=round(w.sum()/13500*100,2),last_cumulative_volume=int(vol[-1]),
        first_cumulative_volume=int(vol[0]))


def main():
    OUT.mkdir(exist_ok=True);rows={};hashes={}
    for code in sorted(set(p.name.split('_')[0] for p in (WORK/'data').glob('au*_tick.pkl'))):
        if 'C' not in code and 'P' not in code:continue
        paths=sorted((WORK/'data').glob(code+'_*_tick.pkl'))
        rs=[audit(p) for p in paths];rows[code]=rs
        hashes.update({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
        print(code,'days',len(rs),'avg_day_volume',round(np.mean([r['day_volume_increment'] for r in rs]),2),
            'median_day_volume',float(np.median([r['day_volume_increment'] for r in rs])),
            'avg_trade_frames',round(np.mean([r['trade_update_frames'] for r in rs]),1),
            'avg_daily_median_spread_pct',round(np.mean([r['median_spread_pct'] for r in rs]),3),
            'LAST',rs[-1])
    (OUT/'measurements.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),'utf8')
    (OUT/'manifest.json').write_text(json.dumps(dict(inputs=hashes,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        semantics='Observed daytime cumulative increments; first snapshot excluded because it includes night trades. Positive-volume snapshot count is not exchange transaction count. Spread percent denominator is midpoint; time weighting holds quote until next snapshot inside the same session.'),ensure_ascii=False,indent=2),'utf8')


if __name__=='__main__':main()
