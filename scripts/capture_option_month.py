"""Read-only MiniQMT history capture for the nine previously selected options."""
from pathlib import Path
import argparse
import json
import sys
import tomllib
from datetime import datetime
import pandas as pd
from xtquant import xtdata

from probe_option_top_cycle import ROOT,ARCHIVE,NAMES,digest


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    parser.add_argument('--start',default='20260810');parser.add_argument('--end',default='20260909')
    parser.add_argument('--codes',nargs='*',default=list(NAMES));args=parser.parse_args()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    xtdata.enable_hello=False
    port=tomllib.loads((ROOT/'config.toml').read_text(encoding='utf-8-sig'))['qmt']['port']
    client=xtdata.connect(port=port);assert client.is_connected()
    dates=[pd.Timestamp(t,unit='ms',tz='UTC').tz_convert('Asia/Shanghai').strftime('%Y%m%d')
           for t in xtdata.get_trading_dates('SH',args.start,args.end)]
    metadata=json.loads((ARCHIVE/'options_market_20260909_snapshot.json').read_text(encoding='utf-8'))['details']
    manifest_path=out/'capture_manifest.json'
    state=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else dict(
        start=args.start,end=args.end,trading_dates=dates,source='MiniQMT xtdata',port=port,
        captured_at=datetime.now().isoformat(),contracts={})
    assert state['start']==args.start and state['end']==args.end
    for c in args.codes:
        if c in state['contracts']:
            print(c,'already captured',flush=True);continue
        detail=metadata[c];listing=detail['OpenDate']
        first=max(args.start,listing)
        info=dict(code=c,name=NAMES[c],listing_date=listing,details=detail,requested_start=first,days={},errors=[])
        try:
            print(c,'download tick',first,args.end,flush=True)
            xtdata.download_history_data(c,'tick',first,args.end+'160000')
            print(c,'download daily bars',flush=True)
            xtdata.download_history_data(c,'1d',first,args.end)
            day=xtdata.get_market_data_ex([], [c],period='1d',start_time=first,end_time=args.end,fill_data=False).get(c)
            if day is not None:
                target=out/f'{c}_daily.pkl';assert not target.exists();day.to_pickle(target)
                info['daily_file']=target.name;info['daily_sha256']=digest(target);info['daily_rows']=len(day)
        except Exception as exc:
            info['errors'].append(dict(stage='download',error=str(exc)))
        # Read one day at a time so a month of five-depth object frames never resides in memory.
        for date in dates:
            if date<listing:
                info['days'][date]=dict(status='not_listed');continue
            try:
                f=xtdata.get_market_data_ex([], [c],period='tick',start_time=date,end_time=date+'160000',fill_data=False).get(c)
                if f is None or f.empty:
                    info['days'][date]=dict(status='missing',rows=0);continue
                actual=pd.to_datetime(f['time'],unit='ms',utc=True).dt.tz_convert('Asia/Shanghai').dt.strftime('%Y%m%d')
                assert set(actual)=={date},(date,set(actual))
                target=out/f'{c}_{date}.pkl';assert not target.exists();f.to_pickle(target)
                info['days'][date]=dict(status='received',rows=len(f),file=target.name,sha256=digest(target),
                                       first_ts=int(f.time.min()),last_ts=int(f.time.max()))
            except Exception as exc:
                info['days'][date]=dict(status='error',error=str(exc))
        state['contracts'][c]=info
        manifest_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        print(c,[(d,x['status'],x.get('rows',0)) for d,x in info['days'].items()],flush=True)
    print('Capture finished',len(state['contracts']),'contracts',len(dates),'requested dates',flush=True)


if __name__=='__main__':main()
