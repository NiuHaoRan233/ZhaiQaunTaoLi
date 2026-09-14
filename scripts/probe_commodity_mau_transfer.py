"""Seven predeclared mAu-inspired ablations, same frozen 64-contract input universe."""
from pathlib import Path
from collections import Counter
import pickle
import argparse
from zhaiquant import commodity_mau_transfer_research as engine
from probe_commodity_intraday import instruments, run_portfolios
from probe_commodity_capital import ROOT, WORK, OLD, read, write, digest, pack, unpack

OUT=WORK/'reports/commodity_mau_transfer_20260913'
PARENT=WORK/'reports/commodity_intraday_20260913'


def economic(rows):
    ignored={'model_id','portfolio_model_id','entry_signal'}
    return [{k:v for k,v in r.items() if k not in ignored} for r in rows]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--prefix',action='store_true');args=parser.parse_args()
    OUT.mkdir(exist_ok=True)
    sources=[Path(engine.__file__),Path(__file__),ROOT/'src/zhaiquant/commodity_intraday_research.py',
        ROOT/'src/zhaiquant/commodity_capital_research.py',ROOT/'src/zhaiquant/commodity_flow_strategy.py',
        ROOT/'scripts/probe_commodity_intraday.py',ROOT/'scripts/probe_commodity_capital.py',
        ROOT/'src/zhaiquant/commodity_selective_research.py']
    contract=dict(family=engine.FAMILY,profiles=engine.PROFILES,capital_cny=300000,
        parent='probe_commodity_intraday_20260913_v1_edge10_shared_300000_cost_close',
        reference_commit='290867798c02b33f5b284f83a8d81e528cf42878',
        source_hashes={str(p.relative_to(ROOT)):digest(p) for p in sources},
        code_universe=sorted(instruments()),fee_cents=170,delay_ms=0,
        gap_rule='Require bid2; reject bid1-bid2 >= max(2 ticks, ask1-bid1).',
        reference_rule='Own option midpoint EWMA tau150/900 seconds, min with current mid; warm60s/3 observations; edge less two fees >= max(tick,2 fees); reject 10s fall >= max(2 ticks,half spread).',
        cutoff_rule='Reject new buys during final300 seconds; settle old orders first.',
        evaluation='All dates previously seen; no untouched holdout; keep all seven variants and all64 contracts.',
        cross_market_reference_implemented=False)
    if (OUT/'candidate_contract.json').exists():assert read(OUT/'candidate_contract.json')==contract
    else:write(OUT/'candidate_contract.json',contract)
    if args.prefix:
        ps={p:engine.TransferPortfolio(instruments(),p,30000000) for p in engine.PROFILES}
        n=run_portfolios(ps,through='20260904')
        for key,p in ps.items():
            r=p.result();saved=unpack(OUT/f'{key}.json.gz');cut=engine.close_timestamp('20260904')
            assert r['fills']==[f for f in saved['fills'] if f['ts']<=cut],key
            assert r['orders']==[o for o in saved['orders'] if o['created_ts']<=cut],key
            assert r['daily']==[d for d in saved['daily'] if d['date']<='20260904'],key
        write(OUT/'prefix_verification.json',dict(status='passed',profiles=list(ps),through='20260904',frames=n))
    else:
        preserved=read(PARENT/'result_manifest.json')
        for p,h in preserved.items():assert digest(ROOT/p)==h,p
        coverage={};tags={};totals=Counter()
        for code in contract['code_universe']:
            cachepath=OLD/'event_cache'/f'{code}.pkl';cache=pickle.loads(cachepath.read_bytes())
            tags[code]=dict(cache_sha256=digest(cachepath),raw=cache['tag'])
            c=Counter()
            for events,_,meta in cache['inputs']:
                c['observed_days']+=1
                for e in events:
                    c['events']+=1;c['bid2']+=len(e.bids)>1;c['ask2']+=len(e.asks)>1
            coverage[code]=dict(c);totals.update(c)
        write(OUT/'input_manifest.json',tags);write(OUT/'coverage.json',dict(total=dict(totals),contracts=coverage))
        ps={p:engine.TransferPortfolio(instruments(),p,30000000) for p in engine.PROFILES}
        n=run_portfolios(ps);compact={}
        for key,p in ps.items():
            r=p.result()
            assert all(c['entry_ts']//86400000==c['exit_ts']//86400000 for c in r['cycles'])
            assert round(r['summary']['fees_cny']*100)==sum(f['fee_cents'] for f in r['fills'])
            assert all(f['entry_signal'] is not None and f['entry_signal']['ts']<f['ts'] for f in r['fills'] if f['side']=='buy')
            pack(OUT/f'{key}.json.gz',r);compact[key]={k:r[k] for k in ['summary','daily','accounts']}
            print('RESULT',key,r['summary'],flush=True)
        old=unpack(PARENT/'shared/edge10_300000.json.gz');control=unpack(OUT/'control.json.gz')
        for k in ['fills','orders']:assert economic(old[k])==economic(control[k]),k
        for k in ['cycles','daily','curve']:assert old[k]==control[k],k
        for p,h in preserved.items():assert digest(ROOT/p)==h,p
        for p,h in contract['source_hashes'].items():assert digest(ROOT/p)==h,p
        write(OUT/'results.json',compact)
        write(OUT/'verification.json',dict(status='passed',frames=n,profiles=7,contracts=64,
            original_control_exact=True,all_cycles_intraday=True,all_daily_inventories_zero=True,
            fees_cash_reconcile=True,entry_signals_predate_fills=True,preserved_files=len(preserved)))
    write(OUT/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.is_file() and p.name!='result_manifest.json'})


if __name__=='__main__':main()
