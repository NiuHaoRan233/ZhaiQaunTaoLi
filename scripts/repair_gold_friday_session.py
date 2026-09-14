"""Preserve provider timestamps and audit Friday after-midnight trade-date encoding."""
import shutil
import pandas as pd
from capture_gold_sessions import OUT,BASE
from probe_commodity_capital import ROOT,read,write,digest
from zhaiquant.gold_history_validation import timestamp


def main():
    from xtquant import xtdata
    import tomllib
    dest=OUT/'friday_calendar_revision';dest.mkdir(exist_ok=True)
    if (dest/'manifest.json').exists():return
    xtdata.enable_hello=False
    xtdata.connect(port=tomllib.loads((ROOT/'config.toml').read_text('utf-8-sig'))['qmt']['port'])
    lo=timestamp('20260914','000000');hi=timestamp('20260914','023000');shift=2*86400000
    evidence=[]
    for path in sorted((OUT/'20260911/night_inputs').glob('*.pkl')):
        code=path.stem
        xtdata.download_history_data(code,'tick','20260911210000','20260914023000')
        raw=xtdata.get_market_data_ex([],[code],period='tick',start_time='20260911210000',end_time='20260914023000',fill_data=False)[code]
        (dest/'raw').mkdir(exist_ok=True);raw.to_pickle(dest/'raw'/path.name)
        before=pd.read_pickle(path);after=raw[(raw.time>=lo)&(raw.time<hi)].copy()
        assert len(after)>0
        assert int(after.iloc[0].volume)>=int(before.iloc[-1].volume)
        assert float(after.iloc[0].amount)>=float(before.iloc[-1].amount)
        assert float(after.iloc[0].lastPrice)>0
        foriginal=after.iloc[0].to_dict()
        after['provider_time']=after.time;after.time=after.time-shift
        before=before.copy();before['provider_time']=before.time
        normalized=pd.concat([before,after]).sort_values('time',kind='stable')
        assert normalized.time.is_monotonic_increasing and not normalized.time.duplicated().any()
        (dest/'normalized').mkdir(exist_ok=True);normalized.to_pickle(dest/'normalized'/path.name)
        evidence.append(dict(code=code,before_last_time=int(before.time.iloc[-1]),after_first_provider_time=int(foriginal['time']),
            after_first_actual_time=int(after.time.iloc[0]),before_volume=int(before.volume.iloc[-1]),after_volume=int(foriginal['volume']),
            before_price=float(before.lastPrice.iloc[-1]),after_price=float(foriginal['lastPrice']),rows_added=len(after),rows=len(normalized),
            last_quote_age_seconds=(timestamp('20260912','023000')-int(normalized.time.max()))/1000))
    write(dest/'audit.json',dict(reason='Friday night after midnight encoded as Monday exchange trading date by provider; cumulative volume/amount continuous; weekend closed.',
        normalization='Only raw 2026-09-14 00:00 <= t < 02:30 shifted minus 2 calendar days; raw index and provider_time preserved.',evidence=evidence))
    coverage=read(OUT/'night_coverage.json')
    for row in coverage:
        if row['selection_date']=='20260911':
            f=pd.read_pickle(dest/'normalized'/f'{row["code"]}.pkl')
            row.update(rows=len(f),first_ts=int(f.time.min()),last_ts=int(f.time.max()),last_quote_age_seconds=(row['window_end']-int(f.time.max()))/1000)
    write(dest/'night_coverage.json',coverage)
    archive=dest/'provisional_incomplete_replay';archive.mkdir(exist_ok=True)
    for name in ('comparison.json','results.json','market_activity.json','verification.json','replay_plan.json'):
        shutil.copy2(OUT/name,archive/name)
    for folder in ('jobs','ledgers'):
        (archive/folder).mkdir(exist_ok=True)
        for p in (OUT/folder).glob('20260911_*'):
            shutil.move(str(p),str(archive/folder/p.name))
    write(dest/'manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in dest.rglob('*') if p.is_file()})
    print(evidence,flush=True)


if __name__=='__main__':main()
