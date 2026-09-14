"""Capture latest completed daytime, freeze state selection, replay two gold options."""
from pathlib import Path
from dataclasses import replace
import argparse
import json
import math
import re
import pickle
import tomllib
import pandas as pd
from zhaiquant import gold_state_research as engine
from zhaiquant.commodity_dadao_research import load_frame
from probe_commodity_intraday import append
from probe_commodity_capital import ROOT,WORK,read,write,digest,pack,unpack

OUT=WORK/'reports/gold_state_20260913_v1'


def setup():
    OUT.mkdir(exist_ok=True);(OUT/'inputs').mkdir(exist_ok=True)
    sources=[Path(__file__),Path(engine.__file__)]+[ROOT/'src/zhaiquant'/f for f in [
        'commodity_mau_transfer_research.py','commodity_intraday_research.py','commodity_capital_research.py',
        'commodity_flow_strategy.py','commodity_dadao_research.py','option_top_cycle_research.py']]
    plan=dict(family=engine.FAMILY,date=engine.DATE,selection='09:00<=t<09:30 China',execution='09:30<=t<15:00 China',
        settlement='At15:00 close at entry cost virtually, both side fees retained, report prior bid floating PnL.',
        criteria=engine.CRITERIA,variants=['gap','control'],capital_per_code_cny=150000,total_scenario_capital_cny=300000,
        sources={str(p.relative_to(ROOT)):digest(p) for p in sources},
        no_profit_based_selection=True,scope='Current catalogue with OpenDate<=sample date and expiry after sample; instrument terms not historical quote ranks. Latest-day developmental sample, no unseen out-of-sample claim.')
    p=OUT/'plan.json'
    if p.exists():assert read(p)==plan,'Frozen plan changed'
    else:write(p,plan)
    return plan


def capture(plan):
    from xtquant import xtdata
    xtdata.enable_hello=False
    port=tomllib.loads((ROOT/'config.toml').read_text('utf-8-sig'))['qmt']['port']
    c=xtdata.connect(port=port);assert c.is_connected()
    cat=read(WORK/'data/option_dashboard/catalog.json')
    details={k:d for k,d in cat['details'].items() if re.fullmatch(r'au\d{4}(?:[CP]\d+)?\.SF',k)
        and (not d.get('OpenDate') or d['OpenDate']<=engine.DATE) and d.get('ExpireDate','99999999')>=engine.DATE}
    write(OUT/'catalog_terms.json',dict(captured_at=cat['captured_at'],details=details))
    futures=sorted(c for c in details if re.fullmatch(r'au\d{4}\.SF',c))
    def fetch(codes,end='093000'):
        missing=[c for c in codes if not (OUT/'inputs'/f'{c}.pkl').exists()]
        if missing:
            print('DOWNLOAD',len(missing),missing,flush=True)
            xtdata.download_history_data2(missing,'tick',engine.DATE+'090000',engine.DATE+end)
            frames=xtdata.get_market_data_ex([],missing,period='tick',start_time=engine.DATE+'090000',end_time=engine.DATE+end,fill_data=False)
            for code in missing:
                f=frames.get(code)
                if f is None or f.empty:
                    print('MISSING',code,flush=True);continue
                (OUT/'inputs'/f'{code}.pkl').write_bytes(pickle.dumps(f,protocol=5))
                print('SAVED',code,len(f),flush=True)
    fetch(futures)
    states={c:engine.future_state(pd.read_pickle(OUT/'inputs'/f'{c}.pkl')) for c in futures if (OUT/'inputs'/f'{c}.pkl').exists()}
    states={c:v for c,v in states.items() if v is not None}
    ranked=sorted(states,key=lambda c:(-states[c]['open_interest'],-states[c]['observed_volume_increment'],c))
    assert len(ranked)>=2,'Insufficient historical underlying months'
    chosen_months=ranked[:2]
    write(OUT/'month_selection.json',dict(states=states,ranked=ranked,selected=chosen_months))
    print('MONTHS',chosen_months,states,flush=True)
    pool=[];excluded={}
    for code,d in details.items():
        if not re.fullmatch(r'au\d{4}[CP]\d+\.SF',code):continue
        future=d.get('OptUndlCode','')+'.SF'
        if future not in chosen_months:excluded[code]='outside_top_two_future_months';continue
        if not d.get('OptExercisePrice') or abs(math.log(states[future]['mid']/d['OptExercisePrice']))>engine.CRITERIA['maximum_absolute_log_moneyness']:
            excluded[code]='deep_moneyness';continue
        pool.append(code)
    write(OUT/'capture_pool.json',dict(codes=sorted(pool),excluded=excluded,catalogue_option_count=len(pool)+len(excluded)))
    for i in range(0,len(pool),8):fetch(sorted(pool)[i:i+8],end='150000')
    write(OUT/'input_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in (OUT/'inputs').glob('*.pkl')})


def select(plan):
    details=read(OUT/'catalog_terms.json')['details'];months=read(OUT/'month_selection.json');rows={}
    for code in read(OUT/'capture_pool.json')['codes']:
        p=OUT/'inputs'/f'{code}.pkl'
        if not p.exists():rows[code]=dict(eligible=False,reasons=['data_unavailable']);continue
        frame=pd.read_pickle(p);d=details[code];u=months['states'][d['OptUndlCode']+'.SF']
        m=engine.state_metrics(frame,d,u)
        assert m==engine.state_metrics(engine.prefix_frame(frame),d,u),'Future rows affect selection'
        rows[code]=m
    ranked=sorted([(c,m) for c,m in rows.items() if m['eligible']],key=engine.rank_key)
    result=dict(rows=rows,ranked=[c for c,m in ranked],selected=[c for c,m in ranked[:2]],
        selected_before_backtest=True,selection_input_end_exclusive=engine.CUTOFF,
        future_input_truncation_identical=True)
    target=OUT/'selection.json'
    if target.exists():assert read(target)==result
    else:write(target,result)
    print('SELECTED',json.dumps({c:rows[c] for c in result['selected']},ensure_ascii=False),flush=True)
    assert len(result['selected'])==2,'Fewer than two passed fixed filter; do not tune against profit'


def replay(plan):
    selection=read(OUT/'selection.json');assert len(selection['selected'])==2
    selected_hash=digest(OUT/'selection.json');details=read(OUT/'catalog_terms.json')['details'];summaries={}
    for code in selection['selected']:
        f=pd.read_pickle(OUT/'inputs'/f'{code}.pkl')
        # SDK may return boundary rows; use only this natural day before loading.
        f=f[(f.time>=engine.START)&(f.time<engine.END)]
        events,flags,meta=load_frame(f,code=code,date=engine.DATE,detail=details[code])
        events=[replace(e,quantity=min(e.quantity,1)) for e in events if e.ts>=engine.CUTOFF]
        for profile in plan['variants']:
            a=engine.GoldAccount(code,profile,meta['tick_cents']);rows={k:[] for k in ['orders','fills','cycles','curve']}
            for e in events:append(rows,a.step(e,engine.DATE))
            append(rows,a.close_day(engine.DATE));rows.update(summary=a.summary(),daily=a.daily(),input_meta=meta)
            assert sum(c['net_cents'] for c in rows['cycles'])==round(rows['summary']['pnl_cny']*100)
            assert sum(x['fee_cents'] for x in rows['fills'])==round(rows['summary']['fees_cny']*100)
            assert all(o['created_ts']>=engine.CUTOFF for o in rows['orders'])
            assert all(c['entry_ts']<c['exit_ts']<=engine.END for c in rows['cycles'])
            assert rows['summary']['end_inventory']==0
            # Independent reconstruction of cash; virtual fills remain explicitly typed.
            cash=15000000;inventory=0
            for fill in rows['fills']:
                cash+=fill['price_cents']*(1 if fill['side']=='sell' else -1)-170
                inventory+=1 if fill['side']=='buy' else -1
                assert cash==fill['cash_cents'] and inventory==fill['inventory']
                if fill['side']=='buy':assert fill['entry_signal']['ts']<fill['ts']
            assert cash==a.s['cash'] and inventory==0
            pack(OUT/f'{code}_{profile}.json.gz',rows)
            summaries[f'{code}_{profile}']={k:rows[k] for k in ['summary','daily']}
            print('RESULT',code,profile,rows['summary'],flush=True)
            # Fresh chronological prefix, no virtual settlement before actual15:00.
            cut=engine.START+4*3600000
            b=engine.GoldAccount(code,profile,meta['tick_cents']);prefix={k:[] for k in ['orders','fills','cycles','curve']}
            for e in events:
                if e.ts>cut:break
                append(prefix,b.step(e,engine.DATE))
            for key,time in [('fills','ts'),('orders','created_ts')]:
                assert json.loads(json.dumps(prefix[key]))==[x for x in json.loads(json.dumps(rows[key])) if x[time]<=cut]
    assert digest(OUT/'selection.json')==selected_hash
    for p,h in plan['sources'].items():assert digest(ROOT/p)==h
    for p,h in read(OUT/'input_manifest.json').items():assert digest(ROOT/p)==h
    write(OUT/'results.json',summaries)
    write(OUT/'verification.json',dict(status='passed',selected_contracts=selection['selected'],accounts=4,
        selection_before_replay=True,selection_hash_unchanged=True,selection_uses_prefix_only=True,
        account_cash_and_fees_reconstructed=True,all_end_inventory_zero=True,
        execution_prefix_replayed_through='13:00',no_broker_orders=True))
    write(OUT/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.rglob('*') if p.is_file() and p.name!='result_manifest.json'})


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage',choices=['capture','select','replay','all'],default='all');a=parser.parse_args();plan=setup()
    if a.stage in ['all','capture']:capture(plan)
    if a.stage in ['all','select']:select(plan)
    if a.stage in ['all','replay']:replay(plan)


if __name__=='__main__':main()
