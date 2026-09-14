"""Canonical JSON prefix verification; frozen runner compared nested tuples to saved lists."""
from pathlib import Path
import json
from zhaiquant import commodity_mau_transfer_research as engine
from probe_commodity_intraday import instruments,run_portfolios
from probe_commodity_mau_transfer import OUT,ROOT
from probe_commodity_capital import read,write,unpack,digest


def main():
    ps={p:engine.TransferPortfolio(instruments(),p,30000000) for p in engine.PROFILES}
    n=run_portfolios(ps,through='20260904');cut=engine.close_timestamp('20260904')
    for key,p in ps.items():
        # Only normalize serialization types, never remove any economic or signal field.
        r=json.loads(json.dumps(p.result(),allow_nan=False));saved=unpack(OUT/f'{key}.json.gz')
        assert r['fills']==[f for f in saved['fills'] if f['ts']<=cut],key
        assert r['orders']==[o for o in saved['orders'] if o['created_ts']<=cut],key
        assert r['daily']==[d for d in saved['daily'] if d['date']<='20260904'],key
    contract=read(OUT/'candidate_contract.json')
    for p,h in contract['source_hashes'].items():assert digest(ROOT/p)==h,p
    write(OUT/'prefix_verification.json',dict(status='passed',profiles=list(ps),through='20260904',frames=n,
        canonical_json_comparison=True,all_fill_and_order_fields_compared=True,
        correction='Frozen runner direct tuple/list comparison failed at nested entry_signal.bids; canonical JSON normalization only.',
        verifier_sha256=digest(Path(__file__))))
    write(OUT/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.is_file() and p.name!='result_manifest.json'})
    print('ALL SEVEN PREFIXES PASSED',flush=True)


if __name__=='__main__':main()
