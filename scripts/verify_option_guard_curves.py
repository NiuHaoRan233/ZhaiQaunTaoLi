"""Independently reconstruct every saved equity observation from fills and books."""
import argparse
from collections import defaultdict
import gzip
import json
from pathlib import Path

from probe_option_top_cycle import ARCHIVE, load, NAMES


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--input',required=True);args=parser.parse_args()
    out=Path(args.input)
    details=json.loads((ARCHIVE/'options_market_20260909_snapshot.json').read_text(encoding='utf-8'))['details']
    counts=dict(accounts=0,observations=0,fill_rows=0)
    for c in NAMES:
        events,_=load(c,details[c])
        for path in out.glob(c+'_*.json.gz'):
            r=json.load(gzip.open(path,'rt',encoding='utf-8'))
            fills=defaultdict(list)
            for f in r['fills']:fills[f['ts']].append(f)
            inv=0;cash=1_000_000;bid=0;holding=0;previous=None
            assert len(r['curve'])==len(events)
            for e,point in zip(events,r['curve']):
                if previous is not None:holding+=inv*(e.ts-previous)
                for f in fills[e.ts]:
                    qty=f['quantity'];cost=qty*f['price_cents'];fee=f['fee_cents']
                    cash+=cost-fee if f['side']=='sell' else -cost-fee
                    inv+=qty if f['side']=='buy' else -qty
                if 0<e.bid<e.ask and e.bid_qty>0 and e.ask_qty>0:bid=e.bid
                assert point==[e.ts,cash+inv*bid-1_000_000,inv],(path.name,e.ts)
                previous=e.ts
            assert holding==round(r['summary']['inventory_contract_seconds']*1000)
            assert cash==round(r['summary']['end_cash_cny']*100)
            counts['accounts']+=1;counts['observations']+=len(events);counts['fill_rows']+=len(r['fills'])
        print(c,'verified',flush=True)
    (out/'independent_curve_audit.json').write_text(json.dumps(counts,indent=2),encoding='utf-8')
    print(counts,flush=True)


if __name__=='__main__':main()
