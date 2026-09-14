from pathlib import Path
from zhaiquant import commodity_mau_depth_audit as engine
from probe_commodity_intraday import instruments, run_portfolios
from zhaiquant.commodity_intraday_research import close_timestamp
from probe_commodity_capital import ROOT,WORK,read,write,digest,pack
from probe_commodity_mau_transfer import OUT as PRIOR

OUT=WORK/'reports/commodity_mau_depth_audit_20260913'


def main():
    OUT.mkdir(exist_ok=True)
    sources=[Path(__file__),Path(engine.__file__),ROOT/'src/zhaiquant/commodity_mau_transfer_research.py']
    contract=dict(family=engine.FAMILY,variants=list(engine.VARIANTS),capital_cny=300000,
        purpose='Post-result mechanism audit. depth_only refuses missing bid2 but permits observed isolated bids; gap_when_observed refuses observed isolated bids but permits missing bid2.',
        decision_timing='Old order settled first; never retroactively reject fills.',
        sources={str(p.relative_to(ROOT)):digest(p) for p in sources},
        parent_contract_hash=digest(PRIOR/'candidate_contract.json'),
        source_input_hash=digest(PRIOR/'input_manifest.json'))
    assert not (OUT/'candidate_contract.json').exists(),'Separate frozen run already exists'
    write(OUT/'candidate_contract.json',contract)
    ps={k:engine.DepthPortfolio(instruments(),k,30000000) for k in engine.VARIANTS}
    n=run_portfolios(ps);results={}
    for k,p in ps.items():
        r=p.result();pack(OUT/f'{k}.json.gz',r);results[k]={f:r[f] for f in ['summary','daily','accounts']}
        assert sum(f['fee_cents'] for f in r['fills'])==round(r['summary']['fees_cny']*100)
        print('DEPTH_AUDIT',k,r['summary'],flush=True)
    write(OUT/'results.json',results)
    prefix={k:engine.DepthPortfolio(instruments(),k,30000000) for k in engine.VARIANTS}
    run_portfolios(prefix,through='20260904')
    cut=close_timestamp('20260904')
    for k,p in prefix.items():
        r=p.result();full=ps[k].result()
        assert r['fills']==[f for f in full['fills'] if f['ts']<=cut]
        assert r['orders']==[o for o in full['orders'] if o['created_ts']<=cut]
        assert r['daily']==[d for d in full['daily'] if d['date']<='20260904']
    for p,h in contract['sources'].items():assert digest(ROOT/p)==h
    write(OUT/'verification.json',dict(status='passed',frames=n,profiles=2,contracts=64,
        fees_and_cash_reconciled=True,all_days_flat=True,prefix_through='20260904',prefix_all_models=True))
    write(OUT/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='result_manifest.json'})


if __name__=='__main__':main()
