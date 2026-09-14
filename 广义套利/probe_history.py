"""Read-only history availability probe; persist raw evidence locally."""
import json
import sys
import tomllib
from pathlib import Path
from datetime import datetime
from xtquant import xtdata

sys.stdout.reconfigure(encoding='utf-8')
ROOT=Path(__file__).resolve().parents[1]
OUT=Path(__file__).resolve().parent/'data'
xtdata.enable_hello=False
xtdata.connect(port=tomllib.loads((ROOT/'config.toml').read_text(encoding='utf-8-sig'))['qmt']['port'])
for sector in ['广期所','能源中心','能源交易所']:
    a=xtdata.get_stock_list_in_sector(sector)
    print(sector,len(a),a[:4],flush=True)
for c in ['c2701-C-2400.DF','AP701C8000.ZF','cu2610C80000.SF']:
    for period in ['1d','tick']:
        print(c,period,'download',flush=True)
        start='20260907' if period=='1d' else '20260911090000'
        end='20260911150000'
        try:
            xtdata.download_history_data(c,period,start,end)
            f=xtdata.get_market_data_ex([], [c],period=period,start_time=start,end_time=end,fill_data=False).get(c)
            if f is None: print('None',flush=True);continue
            f.to_pickle(OUT/f'{c}_{period}_probe.pkl')
            print(f.shape, str(f.head(2).to_dict())[:2800],str(f.tail(1).to_dict())[:1700],flush=True)
        except Exception as e: print(type(e).__name__,str(e),flush=True)
