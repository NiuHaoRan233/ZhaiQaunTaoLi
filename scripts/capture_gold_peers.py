"""Freeze liquid neighbours using only the old morning prefix; QMT read only."""
from pathlib import Path
import numpy as np
import pandas as pd
from probe_commodity_capital import ROOT,WORK,read,write,digest
from capture_gold_reselection import OUT as BASE,DATES
from zhaiquant.gold_history_validation import Clock

OUT=WORK/'reports/gold_peer_risk_20260914_v1'


def main():
    from xtquant import xtdata
    import tomllib
    OUT.mkdir(exist_ok=True)
    if (OUT/'capture_manifest.json').exists():return
    terms=read(BASE/'catalog_terms.json')['details'];all_selections={}
    write(OUT/'plan.json',dict(family='probe_gold_peer_risk_20260914_v1',dates=DATES,bps=100,
        policies=['value','backer_fast'],masks=['day','pm'],variants=['baseline','future_only','peer_cancel','peer_exit'],
        directions=['long','short'],strict=[0,1],lookback_ms=10000,freshness_ms=2000,warning_ticks=2,
        neighbours='Same underlying/expiry/type; prefix valid quotes>=90%, median spread<=3%,volume increment>=20; closest8 then top3 volume',
        source_manifest=digest(BASE/'manifest.json'),research_only=True,all_ties_target_before_new_signal=True))
    xtdata.enable_hello=False;xtdata.connect(port=tomllib.loads((ROOT/'config.toml').read_text('utf-8-sig'))['qmt']['port'])
    for date in DATES:
        clock=Clock(date);selected=read(BASE/date/'selection.json')['scenarios']['100']['selected'];ds={}
        for target in selected:
            d=terms[target];candidates=[]
            for p in (BASE/date/'prefix').glob('*.pkl'):
                c=p.stem;tr=terms.get(c)
                if not tr or c in (target,'au2610C960.SF','au2610C952.SF'):continue
                if any(tr.get(k)!=d[k] for k in ('OptUndlCode','ExpireDate','OptionType')):continue
                f=pd.read_pickle(p);f=f[(f.time>=clock.start)&(f.time<clock.start+1800000)]
                if len(f)<2:continue
                bid=np.array(f.bidPrice.tolist())[:,0];ask=np.array(f.askPrice.tolist())[:,0]
                good=(bid>0)&(ask>bid)&(np.array(f.bidVol.tolist())[:,0]>0)&(np.array(f.askVol.tolist())[:,0]>0)
                volume=int(np.maximum(0,np.diff(f.volume)).sum())
                spread=float(np.median(2*(ask[good]-bid[good])/(ask[good]+bid[good]))) if good.any() else 1
                if good.mean()<.9 or spread>.03 or volume<20:continue
                candidates.append(dict(code=c,strike=tr['OptExercisePrice'],distance=abs(tr['OptExercisePrice']-d['OptExercisePrice']),
                    volume=volume,valid_fraction=float(good.mean()),median_relative_spread=spread,prefix_sha256=digest(p)))
            near=sorted(candidates,key=lambda r:(r['distance'],r['code']))[:8]
            peers=[r['code'] for r in sorted(near,key=lambda r:(-r['volume'],r['distance'],r['code']))[:3]]
            ds[target]=dict(peers=peers,candidates=candidates,nearest8=near,selection_end_exclusive=clock.start+1800000)
        folder=OUT/date;folder.mkdir(exist_ok=True);write(folder/'selection.json',ds);all_selections[date]=ds
        codes={c for v in ds.values() for c in v['peers']};dest=folder/'inputs';dest.mkdir(exist_ok=True)
        for code in sorted(codes):
            p=dest/f'{code}.pkl'
            if p.exists():continue
            old=BASE/date/'full_inputs'/p.name
            if old.exists():f=pd.read_pickle(old)
            else:
                xtdata.download_history_data(code,'tick',date+'090000',date+'150000')
                f=xtdata.get_market_data_ex([],[code],period='tick',start_time=date+'090000',end_time=date+'150000',fill_data=False)[code]
            f=f[(f.time>=clock.start)&(f.time<clock.start+21600000)]
            assert len(f)>0
            f.to_pickle(p)
        print(date,{c:v['peers'] for c,v in ds.items()},flush=True)
    write(OUT/'selections.json',all_selections)
    write(OUT/'capture_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.rglob('*') if p.is_file()})


if __name__=='__main__':main()
