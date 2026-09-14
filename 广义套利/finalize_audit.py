"""Independent local checks and observed two-sided windows; never simulate fills."""
from collections import Counter, defaultdict
from datetime import datetime
import json
import sys
import numpy as np
import pandas as pd
from scan_history import BASE, DATA, REPORT, OPTION, dump, segments, daily_rows, select

CORE = ['SR611P5700.ZF', 'SR611C4900.ZF', 'CF703P17400.ZF',
        'CF703P17200.ZF', 'cu2611P114000.SF', 'cu2612P100000.SF']


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    inv = json.loads(sorted(DATA.glob('commodity_inventory_*.json'))[-1].read_text(encoding='utf-8'))
    daily = json.loads((DATA/'daily_20260907_20260911.json').read_text(encoding='utf-8'))
    screen = json.loads((REPORT/'intraday_screen.json').read_text(encoding='utf-8'))
    audit = json.loads((REPORT/'data_audit.json').read_text(encoding='utf-8'))
    follow = json.loads((REPORT/'followup_selection.json').read_text(encoding='utf-8'))
    exceptions = audit['daily_unit_exceptions']
    # Retain raw evidence. Removing inconsistent OHLC/amount bars is a sensitivity
    # test, not a declaration of which field in the vendor data is wrong.
    suspect = {(r['code'], r['date']) for r in exceptions}
    clean = dict(daily, bars={c: [b for b in bars if (c,b['index']) not in suspect]
                             for c,bars in daily['bars'].items()})
    clean['bars'] = {c: b for c,b in clean['bars'].items() if b}
    before = select(daily_rows(inv, daily))
    after = select(daily_rows(inv, clean))
    result = dict(captured_at=datetime.now().isoformat(),
        daily_checks=audit['daily_unit_checks'], daily_exception_count=len(exceptions),
        exception_market_counts=dict(Counter(r['code'].split('.')[-1] for r in exceptions)),
        shortlist_exceptions=[r for r in exceptions if r['code'] in follow['codes']],
        removed_from_screen=sorted(set(before)-set(after)), added_to_screen=sorted(set(after)-set(before)),
        core_retained_after_excluding_suspect_bars={c: c in after for c in CORE},
        underlying_comparison=[], windows={}, all_windows=[])
    by = defaultdict(list)
    for r in screen['rows'].values():
        by[r['code']].append(r)
    for c in CORE+['132024.SH','132026.SH']:
        rows = []
        for r in sorted(by[c], key=lambda r:r['date']):
            date = r['date']
            f = pd.read_pickle(DATA/f'{c}_{date}_tick.pkl').sort_values('time',kind='stable').drop_duplicates('time',keep='last')
            t=f.time.to_numpy(float)/1000
            bid=np.array([x[0] for x in f.bidPrice],float)
            ask=np.array([x[0] for x in f.askPrice],float)
            bv=np.array([x[0] for x in f.bidVol],float)
            av=np.array([x[0] for x in f.askVol],float)
            valid=(bid>0)&(ask>bid)&(bv>0)&(av>0)
            dv=np.r_[0,np.diff(f.volume.to_numpy(float))]
            lp=f.lastPrice.to_numpy(float)
            for a,b in segments(date,common=True):
                for start in np.arange(a,b,300):
                    stop=min(start+300,b)
                    ii=np.flatnonzero((t>=start)&(t<stop))
                    if not len(ii):continue
                    weights=np.minimum(np.r_[t[ii[1:]],stop]-t[ii],60)*valid[ii]
                    coverage=sum(weights)
                    if coverage<=0:continue
                    mid=(ask[ii]+bid[ii])/2; spread=ask[ii]-bid[ii]
                    vv=weights>0
                    meanmid=float(np.average(mid[vv],weights=weights[vv]))
                    meanspread=float(np.average(spread[vv],weights=weights[vv]))
                    wide_seconds=sum(weights[vv&(spread/np.maximum(mid,1e-9)>=.005)])
                    high=low=wide_high=wide_low=events=0
                    for i in ii:
                        if i==0 or t[i-1]<a or dv[i]<=0:continue
                        events+=1
                        if not valid[i-1] or t[i]-t[i-1]>60:continue
                        wide=(ask[i-1]-bid[i-1])/((ask[i-1]+bid[i-1])/2)>=.005
                        high+=int(lp[i]>=ask[i-1]);low+=int(lp[i]<=bid[i-1])
                        wide_high+=int(wide and lp[i]>=ask[i-1])
                        wide_low+=int(wide and lp[i]<=bid[i-1])
                    rows.append(dict(code=c,date=date,start=pd.Timestamp(start,unit='s',tz='UTC').tz_convert('Asia/Shanghai').strftime('%H:%M:%S'),
                        coverage_seconds=float(coverage),mean_mid=meanmid,mean_spread=meanspread,
                        spread_pct=float(np.average(spread[vv]/mid[vv]*100,weights=weights[vv])),
                        wide_seconds=float(wide_seconds),events=events,high=high,low=low,
                        wide_high=wide_high,wide_low=wide_low,
                        mid_range_spreads=float((max(mid[vv])-min(mid[vv]))/meanspread)))
        qualified=[r for r in rows if r['coverage_seconds']>=240 and r['wide_seconds']>=150]
        twosided=[r for r in qualified if r['wide_high']>0 and r['wide_low']>0]
        result['all_windows'].extend(rows)
        result['windows'][c]=dict(observed_bins=len(rows),eligible_wide_bins=len(qualified),
            two_sided_wide_bins=len(twosided),
            days_with_two_sided_wide_bins=len({r['date'] for r in twosided}),
            median_two_sided_mid_range_spreads=float(np.median([r['mid_range_spreads'] for r in twosided])) if twosided else None,
            # Deterministic chronological examples, not the best-looking outcome.
            first_examples=twosided[:2],
            largest_motion_example=max(twosided,key=lambda r:r['mid_range_spreads']) if twosided else None)
        if c in CORE:
            detail=by[c][-1]['detail']
            from scan_history import EXCHANGES
            u=detail['OptUndlCode']+'.'+EXCHANGES[detail['OptUndlMarket']]
            urs=by[u]
            means=lambda rr:float(np.median([x['common']['mid_p95_p05_pct'] for x in rr if 'mid_p95_p05_pct' in x['common']]))
            result['underlying_comparison'].append(dict(code=c,underlying=u,
                option_typical_band_pct=means(by[c]),underlying_typical_band_pct=means(urs),
                expiry=detail.get('ExpireDate')))
    result['status']='passed_with_vendor_daily_warnings' if not audit['issues'] and not result['shortlist_exceptions'] else 'needs_review'
    dump(REPORT/'final_audit.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ['all_windows','windows']},ensure_ascii=False,indent=2))
    print('windows',json.dumps({c:{k:v for k,v in r.items() if 'example' not in k} for c,r in result['windows'].items()},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
