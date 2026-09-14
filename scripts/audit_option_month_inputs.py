"""Audit coverage and daily totals before looking at any monthly strategy profit."""
import argparse
import json
from pathlib import Path
import sys
import pandas as pd

from probe_option_top_cycle import ARCHIVE,NAMES,load,digest
from zhaiquant.option_history_replay import load_day,coverage


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('--input',required=True);args=parser.parse_args()
    root=Path(args.input);capture=json.loads((root/'capture_manifest.json').read_text(encoding='utf-8'))
    rows=[];today_checks=[]
    for c in NAMES:
        info=capture['contracts'][c];details=info['details']
        daily=pd.read_pickle(root/info['daily_file'])
        daydates=pd.to_datetime(daily.time,unit='ms',utc=True).dt.tz_convert('Asia/Shanghai').dt.strftime('%Y%m%d')
        daymap={d:r for d,r in zip(daydates,daily.itertuples())}
        for date,item in info['days'].items():
            meta=dict(code=c,name=NAMES[c],date=date,capture_status=item['status'],eligible=False,reasons=[])
            if item['status']!='received':
                meta['reasons']=[item['status']];rows.append(meta);continue
            path=root/item['file'];assert digest(path)==item['sha256']
            events,quality=load_day(pd.read_pickle(path),date,details)
            meta.update(quality);meta.update(coverage(events,date));meta['sha256']=item['sha256'];meta['file']=item['file']
            if quality['cumulative_resets']:meta['reasons'].append('cumulative_reset')
            if any(x<.95 for x in meta['session_coverage']):meta['reasons'].append('session_coverage_below_95pct')
            if any(x['start_gap_ms'] is None or x['start_gap_ms']>60000 or x['end_gap_ms']>60000 for x in meta['session_edge_gaps']):
                meta['reasons'].append('missing_session_edge')
            d=daymap.get(date)
            if d is None:meta['reasons'].append('missing_daily_crosscheck')
            else:
                meta.update(daily_volume=int(d.volume),daily_amount=float(d.amount),daily_volume_difference=quality['final_tick_volume']-int(d.volume),
                    daily_amount_difference=round(quality['final_tick_amount']-float(d.amount),6))
                if meta['daily_volume_difference']:meta['reasons'].append('daily_volume_mismatch')
                if abs(meta['daily_amount_difference'])>max(2,abs(float(d.amount))*1e-6):meta['reasons'].append('daily_amount_mismatch')
            if date=='20260909':
                frozen,_=load(c,details)
                identical=events==frozen
                today_checks.append(dict(code=c,events_equal=identical,frozen_count=len(frozen),download_count=len(events)))
                assert identical,(c,'today economic input changed')
            meta['eligible']=not meta['reasons'];rows.append(meta)
        cc=[r for r in rows if r['code']==c and r['capture_status']=='received']
        print(c,'received',len(cc),'eligible',sum(r['eligible'] for r in cc),
              'coverage min',round(min(r['valid_book_coverage'] for r in cc)*100,3),
              'issues',[(r['date'],r['reasons']) for r in cc if r['reasons']],flush=True)
    data=dict(start=capture['start'],end=capture['end'],trading_dates=capture['trading_dates'],rows=rows,
        today_input_checks=today_checks,coverage_contract='Each continuous session >=95% time-weighted valid book; carry at most30s, no lunch bridging; each session edges within60s; no cumulative reset; daily volume exact, amount within max(2CNY,1ppm).')
    (root/'input_audit.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print('AUDITED',len(rows),'cells',sum(r['eligible'] for r in rows),'eligible',flush=True)


if __name__=='__main__':main()
