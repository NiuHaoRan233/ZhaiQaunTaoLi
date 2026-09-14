"""Read-only QMT inventory and quote coverage audit for commodity options."""
from pathlib import Path
from datetime import datetime
from collections import Counter
import json
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent / 'data'
SECTORS = ['上期所', '大商所', '郑商所', '广期所', 'GF', '能源中心', 'INE']
OPTION = re.compile(r'^([a-zA-Z]+)(\d{3,4})-?([CP])-?(\d+(?:\.\d+)?)\.(SF|DF|ZF|GF|INE)$')
FUTURE = re.compile(r'^([a-zA-Z]+)(\d{3,4})\.(SF|DF|ZF|GF|INE)$')

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    from xtquant import xtdata
    OUT.mkdir(exist_ok=True)
    xtdata.enable_hello = False
    port = tomllib.loads((ROOT / 'config.toml').read_text(encoding='utf-8-sig'))['qmt']['port']
    client = xtdata.connect(port=port)
    assert client.is_connected()
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    payload = dict(captured_at=datetime.now().isoformat(), source='MiniQMT xtdata', sectors={}, errors=[])
    for label, call in [('authorized_markets', xtdata.get_authorized_market_list), ('markets', xtdata.get_markets)]:
        try: payload[label] = call()
        except Exception as e: payload['errors'].append(dict(stage=label, error=str(e)))
    codes = set()
    for sector in SECTORS:
        raw = xtdata.get_stock_list_in_sector(sector)
        opts = sorted(c for c in raw if OPTION.fullmatch(c))
        futs = sorted(c for c in raw if FUTURE.fullmatch(c))
        payload['sectors'][sector] = dict(raw_count=len(raw), options=opts, futures=futs,
            products=dict(Counter(OPTION.fullmatch(c)[1] for c in opts)))
        codes.update(opts + futs)
        print(sector, 'options', len(opts), 'futures', len(futs), flush=True)
    codes = sorted(codes) + ['132024.SH', '132026.SH']
    payload['ticks'] = {}
    for offset in range(0, len(codes), 500):
        batch = codes[offset:offset+500]
        try: payload['ticks'].update(xtdata.get_full_tick(batch))
        except Exception as e: payload['errors'].append(dict(stage='full_tick',offset=offset,error=str(e)))
        if offset % 5000 == 0: print('quotes', offset, '/', len(codes), flush=True)
    payload['details'] = {}
    representatives = {}
    for c in codes:
        m = OPTION.fullmatch(c)
        if m: representatives.setdefault((m[1],m[5]), c)
    samples = sorted(set(representatives.values()) | {c for c,t in payload['ticks'].items() if t.get('volume',0)>0})
    for c in samples:
        try: payload['details'][c] = xtdata.get_instrument_detail(c, True)
        except Exception as e: payload['errors'].append(dict(stage='detail',code=c,error=str(e)))
    path = OUT / f'commodity_inventory_{stamp}.json'
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    summary = dict(file=str(path), authorized_markets=payload.get('authorized_markets'),
        requested=len(codes),returned=len(payload['ticks']),
        positive_time=sum(t.get('time',0)>0 for t in payload['ticks'].values()),
        positive_volume=sum(t.get('volume',0)>0 for t in payload['ticks'].values()),
        time_examples=dict(Counter(str(t.get('timetag'))[:8] for t in payload['ticks'].values())),
        detail_samples={c:{k:v for k,v in (d or {}).items() if k in ['InstrumentName','ProductName','OptUnit','OptUndlCode','ExpireDate']} for c,d in list(payload['details'].items())[:3]},errors=payload['errors'][:8])
    print(json.dumps(summary,ensure_ascii=False,indent=2,default=str),flush=True)

if __name__ == '__main__': main()
