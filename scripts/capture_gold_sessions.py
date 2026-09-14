"""Read-only evening data AFTER the frozen morning selections."""
from pathlib import Path
from datetime import datetime,timedelta
import pandas as pd
from zhaiquant.gold_history_validation import timestamp
from probe_commodity_capital import ROOT,WORK,read,write,digest
from capture_gold_reselection import OUT as BASE,DATES

OUT=WORK/'reports/gold_sessions_20260914_v1'


def night_dates(date):
    return date,(datetime.strptime(date,'%Y%m%d')+timedelta(days=1)).strftime('%Y%m%d')


def main():
    from xtquant import xtdata
    import tomllib
    OUT.mkdir(exist_ok=True)
    if (OUT/'capture_manifest.json').exists():print('Night capture frozen');return
    xtdata.enable_hello=False
    port=tomllib.loads((ROOT/'config.toml').read_text('utf-8-sig'))['qmt']['port']
    assert xtdata.connect(port=port).is_connected()
    terms=read(BASE/'catalog_terms.json')['details'];coverage=[]
    write(OUT/'capture_plan.json',dict(dates=DATES,selection_source=str(BASE.relative_to(ROOT)),
        evening='same selection calendar date21:00 through next calendar date02:30',
        grouping='selection date; NOT exchange trading date',fixed_before_evening=True,
        night_valuation_resets=True,old_contracts_excluded=True,read_only=True))
    for date in DATES:
        _,nextdate=night_dates(date);lo=timestamp(date,'210000');hi=timestamp(nextdate,'023000')
        selection=read(BASE/date/'selection.json');assert selection['selection_end_exclusive']<lo
        codes={c for s in selection['scenarios'].values() for c in s['selected']}
        codes.update(terms[c]['OptUndlCode']+'.SF' for c in list(codes))
        dest=OUT/date/'night_inputs';dest.mkdir(parents=True,exist_ok=True)
        for code in sorted(codes):
            path=dest/f'{code}.pkl'
            if path.exists():f=pd.read_pickle(path)
            else:
                xtdata.download_history_data(code,'tick',date+'210000',nextdate+'023000')
                f=xtdata.get_market_data_ex([],[code],period='tick',start_time=date+'210000',end_time=nextdate+'023000',fill_data=False).get(code)
                if f is None:f=pd.DataFrame()
                if not f.empty:f=f[(f.time>=lo)&(f.time<hi)]
                f.to_pickle(path)
            coverage.append(dict(selection_date=date,code=code,window_start=lo,window_end=hi,rows=len(f),
                first_ts=int(f.time.min()) if len(f) else None,last_ts=int(f.time.max()) if len(f) else None,
                last_quote_age_seconds=(hi-int(f.time.max()))/1000 if len(f) else None))
        print('NIGHT_CAPTURE',date,'->',nextdate,len(codes),'codes',flush=True)
    write(OUT/'night_coverage.json',coverage)
    write(OUT/'capture_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.rglob('*') if p.is_file()})
    print('NIGHT_CAPTURE_FROZEN',len(coverage),flush=True)


if __name__=='__main__':main()
