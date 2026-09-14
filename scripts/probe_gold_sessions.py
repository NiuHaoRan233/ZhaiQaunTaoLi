"""True masked and day+night replays, with frozen day-path reproduction."""
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor,as_completed
import pandas as pd
import numpy as np
from zhaiquant import gold_session_research as m
from zhaiquant.gold_callput_research import Clock
from probe_gold_backer import add_fast,audit,economics
from probe_gold_two_mode import LABELS
from probe_gold_rule_ladder import portfolio
from probe_commodity_capital import ROOT,WORK,read,write,pack,unpack,digest
from capture_gold_sessions import OUT,BASE,DATES


def select_rows(rows,mask):
    allowed=m.MASKS[mask];ends={m.ENDS[i] for i in allowed}
    return [r for r in rows if (r[1] in ends if r[0]==-1 else r[1].session in allowed)]


def replay(rows,code,policy,terms,clock,direction,bps,strict,mask,cut=None):
    a=m.Account(code,policy,terms['OptExercisePrice'],direction,bps,terms['OptionType'],mask,strict)
    a.model=a.model.replace(m.FAMILY,m.FAMILY+'_'+clock.date)
    if clock.date=='20260911':a.model+='_calendar_r2'
    for kind,e,features,mid,future in rows:
        ts=e if kind==-1 else e.ts
        if cut is not None and ts>cut:break
        if kind==-1:a.boundary(e)
        elif kind==1:a.future_event(e,features)
        else:a.value.future=future;a.features=features;a.option(e)
    r=a.result();audit(r,False)
    if cut is None:
        assert a.inventory==0 and len(r['boundaries'])==len(m.MASKS[mask])
        assert all(b['inventory']==0 and b['pending_order'] is None for b in r['boundaries'])
        assert round(r['summary']['pnl_cny']*100)==sum(c['net_cents'] for c in r['cycles'])
    for c in r['cycles']:assert not any(c['entry_ts']<end<c['exit_ts'] for end in a.boundaries)
    r['summary'].update(history_date=clock.date,selection_date=clock.date)
    return clock.restore(r)


def market(frame,clock,session):
    lo={0:m.g.CUTOFF,1:m.g.SESSION_STARTS[1],2:m.g.SESSION_STARTS[2],3:m.NIGHT_START}[session]-clock.shift
    hi=m.ENDS[session]-clock.shift
    f=frame.sort_values('time',kind='stable').drop_duplicates('time',keep='last')
    ts=f.time.to_numpy(dtype=np.int64);bid=np.array([x[0] for x in f.bidPrice]);ask=np.array([x[0] for x in f.askPrice])
    bq=np.array([x[0] for x in f.bidVol]);aq=np.array([x[0] for x in f.askVol]);valid=(bid>0)&(ask>bid)&(bq>0)&(aq>0)
    starts={0:m.g.START,1:m.g.SESSION_STARTS[1],2:m.g.SESSION_STARTS[2],3:m.NIGHT_START}
    in_session=(ts>=starts[session]-clock.shift)&(ts<hi)
    following=np.r_[ts[1:],hi];w=np.maximum(0,np.minimum(np.minimum(following,hi),ts+60000)-np.maximum(ts,lo))*valid*in_session
    mid=(bid+ask)/2;spread=ask-bid;ratio=np.divide(spread,mid,out=np.zeros_like(mid),where=mid>0)*100
    dv=np.diff(f.volume.to_numpy(),prepend=f.volume.iloc[0]);da=np.diff(f.amount.to_numpy(),prepend=f.amount.iloc[0])
    volume=np.where((dv>0)&(da>=-.02)&(f.lastPrice.to_numpy()>0)&in_session&np.r_[False,in_session[:-1]]&(ts>=lo),dv,0).sum()
    avg=lambda v:float(np.average(v,weights=w)) if w.sum() else None
    return dict(session=session,volume_contracts=int(volume),mean_mid=avg(mid),mean_spread=avg(spread),
        mean_relative_pct=avg(ratio),coverage_pct=float(w.sum()/(hi-lo)*100),
        eligible_time_pct={str(b):float(100*w[ratio>=b/100-1e-12].sum()/w.sum()) if w.sum() else None for b in (100,150,200)})


def job(date,code,bps_list):
    done=OUT/'jobs'/f'{date}_{code}.json'
    if done.exists():return str(done)
    clock=Clock(date);terms=read(BASE/'catalog_terms.json')['details'][code]
    folder=BASE/date/'full_inputs';nf=OUT/date/'night_inputs'
    if date=='20260911':nf=OUT/'friday_calendar_revision/normalized'
    opt=pd.read_pickle(folder/f'{code}.pkl');fut=pd.read_pickle(folder/f'{terms["OptUndlCode"]}.SF.pkl')
    noct=pd.read_pickle(nf/f'{code}.pkl');nfu=pd.read_pickle(nf/f'{terms["OptUndlCode"]}.SF.pkl')
    es,fs,_,_=clock.inputs(opt,fut,code,terms);ne,nfs,meta=m.night_inputs(noct,nfu,clock,terms)
    raw=m.timeline(es+ne,fs+nfs,terms['OptExercisePrice'],terms['ExpireDate'],clock,terms['OptionType'])
    rows=add_fast(raw);nightcut=m.NIGHT_START+int(2.5*3600000);actual_cut=nightcut-clock.shift
    pe,pf,_=m.night_inputs(noct[noct.time<=actual_cut],nfu[nfu.time<=actual_cut],clock,terms)
    pre=add_fast(m.timeline(es+pe,fs+pf,terms['OptExercisePrice'],terms['ExpireDate'],clock,terms['OptionType'],nightcut))
    assert pre==[r for r in rows if (r[1] if r[0]==-1 else r[1].ts)<=nightcut]
    by_mask={mask:select_rows(rows,mask) for mask in m.MASKS}
    summaries={};checks=0;reproductions=0;decompositions=0
    cuts={'day':m.g.START+5*3600000,'am1':m.g.START+3600000,'am2':m.g.START+2*3600000,
        'pm':m.g.START+5*3600000,'night':nightcut,'both':nightcut}
    for bps in bps_list:
        for d in (1,-1):
            mode='long' if d==1 else 'short'
            for strict in (0,1):
                for policy in LABELS:
                    results={}
                    for mask,stream in by_mask.items():
                        r=replay(stream,code,policy,terms,clock,d,bps,bool(strict),mask)
                        pr=replay(stream,code,policy,terms,clock,d,bps,bool(strict),mask,cuts[mask])
                        cut=cuts[mask]-clock.shift
                        for field,t in [('orders','created_ts'),('fills','ts'),('cycles','exit_ts'),('cancels','ts'),('risk_events','ts'),('fee_adjustments','ts')]:
                            assert pr[field]==[v for v in r[field] if v[t]<=cut],(date,code,policy,mask,field)
                        checks+=1;results[mask]=r
                        key=f'{date}_{code}_{mode}_{policy}_bps{bps}_through{strict}_{mask}'
                        pack(OUT/'ledgers'/f'{key}.json.gz',r);summaries[key]=r['summary']
                    old=unpack(BASE/'ledgers'/f'{date}_{code}_{mode}_{policy}_bps{bps}_through{strict}.json.gz')
                    assert economics(results['day'])==economics(old),(date,code,policy,'day parent')
                    reproductions+=1
                    # Validate actual independent-session paths, not retrospective winner selection.
                    norm=lambda r:sorted((c['entry_ts'],c['exit_ts'],c['entry_price_cents'],c['exit_price_cents'],c['net_cents'],c['exit_kind']) for c in r['cycles'])
                    assert norm(results['day'])==sorted(v for mask in ('am1','am2','pm') for v in norm(results[mask]))
                    assert norm(results['both'])==sorted(norm(results['day'])+norm(results['night']))
                    decompositions+=1
    write(done,dict(summaries=summaries,checks=checks,day_reproductions=reproductions,decompositions=decompositions,
        night_meta=meta,markets=[dict(date=date,code=code,**market(opt if i<3 else noct,clock,i)) for i in range(4)]))
    return str(done)


def aggregate(summaries):
    indexed=defaultdict(list)
    for k,s in summaries.items():indexed[(s['session_mask'],s['trade_mode'],s['policy'],s['relative_spread_bps'],s['strict_through'])].append(k)
    groups={}
    for (mask,mode,policy,b,strict),keys in indexed.items():
        rs=[unpack(OUT/'ledgers'/f'{k}.json.gz') for k in keys];g=portfolio(rs)
        key=f'{mask}_{mode}_{policy}_bps{b}_through{int(strict)}'
        pack(OUT/'curves'/f'{key}.json.gz',g.pop('curve'))
        subset=[summaries[k] for k in keys]
        g['independent_minimum_quote_cash_cny']=max(sum(s['minimum_cash_for_all_entry_quotes_cny'] for s in subset if s['history_date']==d) for d in DATES)
        g.update(daily=[round(sum(s['pnl_cny'] for s in subset if s['history_date']==d),2) for d in DATES],
            normal_cycles=sum(s['market_cycles'] for s in subset),virtual_cycles=sum(s['virtual_close_count'] for s in subset),
            removed_tail_gross_cny=sum(s['removed_tail_gross_cny'] for s in subset))
        groups[key]=g
    write(OUT/'comparison.json',groups)
    return groups


def main():
    if (OUT/'manifest.json').exists():raise RuntimeError('Frozen output')
    for name in ('jobs','ledgers','curves'):(OUT/name).mkdir(exist_ok=True)
    for manifest in (BASE/'manifest.json',OUT/'capture_manifest.json',OUT/'friday_calendar_revision/manifest.json'):
        for rel,h in read(manifest).items():assert digest(ROOT/rel)==h,rel
    sources=[Path(__file__),ROOT/'src/zhaiquant/gold_session_research.py',ROOT/'tests/test_gold_session_research.py']
    write(OUT/'replay_plan.json',dict(family=m.FAMILY,masks=m.MASKS,dates=DATES,policies=list(LABELS),
        selection_source_hash=digest(BASE/'manifest.json'),capital_cny_per_code=250000,extra_delay_ms=0,
        fee_one_side_cny=1.7,settlement='exclude at each real session end including fees; midnight continuous',
        source_hashes={str(p.relative_to(ROOT)):digest(p) for p in sources}))
    tasks=[]
    for date in DATES:
        selected=defaultdict(list)
        for b,s in read(BASE/date/'selection.json')['scenarios'].items():
            for c in s['selected']:selected[c].append(int(b))
        tasks += [(date,c,bs) for c,bs in sorted(selected.items())]
    paths=[]
    with ProcessPoolExecutor(max_workers=3) as pool:
        jobs={pool.submit(job,*t):t for t in tasks}
        for f in as_completed(jobs):
            paths.append(f.result());print('FINISHED',jobs[f][0],jobs[f][1],len(paths),'/',len(tasks),flush=True)
    all_s={};markets=[];counts=defaultdict(int)
    for path in paths:
        r=read(Path(path));all_s.update(r['summaries']);markets+=r['markets']
        for k in ('checks','day_reproductions','decompositions'):counts[k]+=r[k]
    all_s=dict(sorted(all_s.items()));write(OUT/'results.json',all_s);write(OUT/'market_activity.json',sorted(markets,key=lambda x:(x['date'],x['code'],x['session'])))
    groups=aggregate(all_s)
    write(OUT/'verification.json',dict(status='passed',accounts=len(all_s),**counts,night_raw_truncation_prefixes=len(tasks),
        boundaries=sum(len(m.MASKS[s['session_mask']]) for s in all_s.values()),old_frozen_files_unchanged=len(read(BASE/'manifest.json'))))
    for rel,h in read(BASE/'manifest.json').items():assert digest(ROOT/rel)==h,rel
    for mask in m.MASKS:
        for mode in ('long','short'):
            for p in ('value','backer_fast'):
                s=groups[f'{mask}_{mode}_{p}_bps100_through0']
                print(mask,mode,p,s['daily'],s['pnl_cny'],'cycles',s['normal_cycles'],'tails',s['virtual_cycles'],s['removed_tail_gross_cny'])


if __name__=='__main__':main()
