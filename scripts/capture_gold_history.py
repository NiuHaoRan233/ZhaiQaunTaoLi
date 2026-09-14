"""Read-only historical prefix selection, frozen before any strategy replay."""
import re
import math
import argparse
import pandas as pd
from zhaiquant.gold_history_validation import Clock, FAMILY
from zhaiquant import gold_state_research as selector
from probe_commodity_capital import ROOT, WORK, read, write, digest

OUT=WORK/'reports/gold_history_20260913_v1'
DATES=['20260907','20260908','20260909','20260910']
FIXED=['au2610C960.SF','au2610C952.SF']


def main():
    from xtquant import xtdata
    import tomllib
    OUT.mkdir(exist_ok=True)
    if (OUT/'capture_manifest.json').exists():
        complete_capture(); return
    xtdata.enable_hello=False
    client=xtdata.connect(port=tomllib.loads((ROOT/'config.toml').read_text('utf-8-sig'))['qmt']['port'])
    assert client.is_connected()
    cat=read(WORK/'data/option_dashboard/catalog.json')
    universe={c for sec in cat['sectors'].values() for c in sec.get('options',[]) if re.fullmatch(r'au\d{4}C\d+\.SF',c)}
    universe.update(c for c in cat['details'] if re.fullmatch(r'au\d{4}(?:C\d+)?\.SF',c))
    details={c:cat['details'].get(c) or xtdata.get_instrument_detail(c,True) for c in sorted(universe)}
    write(OUT/'catalog_terms.json',dict(details=details,scope='Current catalogue calls, listing/expiry checked per date; expired historical inventory may be absent. No current quote ranks.'))
    write(OUT/'selection_plan.json',dict(family=FAMILY,dates=DATES,fixed=FIXED,criteria=selector.CRITERIA,
        scope='Calls only; exact prior state metrics and rank. First30min only. No profit selection or affordability reranking.',
        fee=1.7,delay=0,main_spread_ticks=8))
    for date in DATES:
        folder=OUT/date; folder.mkdir(exist_ok=True)
        clock=Clock(date)
        def fetch(codes, end, directory):
            directory.mkdir(exist_ok=True)
            for code in codes:
                path=directory/f'{code}.pkl'
                if path.exists(): continue
                # SDK bulk downloads can silently miss futures; per-code fallback.
                fs=xtdata.get_market_data_ex([],[code],period='tick',start_time=date+'090000',end_time=date+end,fill_data=False)
                frame=fs.get(code)
                if frame is None or frame.empty or int(frame.time.max()) < clock.start+(1800000 if end=='093000' else 21600000)-60000:
                    xtdata.download_history_data(code,'tick',date+'090000',date+end)
                    frame=xtdata.get_market_data_ex([],[code],period='tick',start_time=date+'090000',end_time=date+end,fill_data=False).get(code)
                if frame is None: frame=pd.DataFrame()
                frame.to_pickle(path)
        terms={c:d for c,d in details.items() if d and d.get('OpenDate','99999999')<=date<=d.get('ExpireDate','')}
        futures=sorted(c for c in terms if re.fullmatch(r'au\d{4}\.SF',c))
        fetch(futures,'093000',folder/'prefix')
        states={c:clock.future_state(pd.read_pickle(folder/'prefix'/f'{c}.pkl')) for c in futures}
        states={c:v for c,v in states.items() if v}
        ranked=sorted(states,key=lambda c:(-states[c]['open_interest'],-states[c]['observed_volume_increment'],c))
        assert len(ranked)>=2, (date,'missing underlying months')
        months=ranked[:2]
        pool=[c for c,d in terms.items() if re.fullmatch(r'au\d{4}C\d+\.SF',c) and d.get('OptUndlCode','')+'.SF' in months
              and abs(math.log(states[d['OptUndlCode']+'.SF']['mid']/d['OptExercisePrice']))<=selector.CRITERIA['maximum_absolute_log_moneyness']]
        print('PREFIX',date,months,len(pool),flush=True)
        for i in range(0,len(pool),8):
            batch=pool[i:i+8]
            missing=[c for c in batch if not (folder/'prefix'/f'{c}.pkl').exists()]
            if missing: xtdata.download_history_data2(missing,'tick',date+'090000',date+'093000')
            fetch(batch,'093000',folder/'prefix')
            print('PREFIX_PROGRESS',date,min(i+8,len(pool)),len(pool),flush=True)
        rows={}
        for code in pool:
            frame=pd.read_pickle(folder/'prefix'/f'{code}.pkl')
            rows[code]=clock.metrics(frame,terms[code],states[terms[code]['OptUndlCode']+'.SF']) if not frame.empty else dict(eligible=False,reasons=['data_unavailable'])
        ranks=sorted([(c,m) for c,m in rows.items() if m['eligible']],key=selector.rank_key)
        selection=clock.restore(dict(date=date,months=months,future_states=states,rows=rows,ranked=[c for c,m in ranks],
            selected=[c for c,m in ranks[:2]],selection_input_end_exclusive=selector.CUTOFF,selected_before_replay=True))
        write(folder/'selection.json',selection)
        chosen=sorted(set(FIXED+selection['selected']))
        required=sorted(set(chosen+[terms[c]['OptUndlCode']+'.SF' for c in chosen]))
        fetch(required,'150000',folder/'inputs')
        coverage={}
        for c in required:
            f=pd.read_pickle(folder/'inputs'/f'{c}.pkl')
            coverage[c]=dict(rows=len(f),first=int(f.time.min()) if len(f) else None,last=int(f.time.max()) if len(f) else None)
        write(folder/'coverage.json',coverage)
        print('SELECTED',date,selection['selected'],'COVERAGE',coverage,flush=True)
    write(OUT/'capture_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.rglob('*') if p.is_file() and p.name!='capture_manifest.json'})
    complete_capture()


def complete_capture():
    from xtquant import xtdata
    import tomllib
    if (OUT/'full_input_manifest.json').exists():
        print('Full inputs already frozen');return
    xtdata.enable_hello=False
    xtdata.connect(port=tomllib.loads((ROOT/'config.toml').read_text('utf-8-sig'))['qmt']['port'])
    details=read(OUT/'catalog_terms.json')['details']
    for date in DATES:
        folder=OUT/date; dest=folder/'full_inputs';dest.mkdir(exist_ok=True)
        selection=read(folder/'selection.json');clock=Clock(date)
        codes=sorted(set(FIXED+selection['selected']))
        futures=sorted({details[c]['OptUndlCode']+'.SF' for c in codes})
        xtdata.download_history_data2(codes,'tick',date+'090000',date+'150000')
        for c in futures:xtdata.download_history_data(c,'tick',date+'090000',date+'150000')
        frames=xtdata.get_market_data_ex([],codes+futures,period='tick',start_time=date+'090000',end_time=date+'150000',fill_data=False)
        coverage={}
        for code in codes+futures:
            f=frames[code];assert not f.empty,(date,code,'missing')
            f=f[(f.time>=clock.start)&(f.time<clock.start+21600000)]
            assert int(f.time.max())>=clock.start+21600000-5000,(date,code,'missing final5s')
            assert int(f.time.min())<=clock.start+5000,(date,code,'missing open')
            counts=[int(((f.time>=b-clock.shift-5000)&(f.time<b-clock.shift)).sum()) for b in __import__('zhaiquant.gold_direction_research',fromlist=['BOUNDARIES']).BOUNDARIES]
            f.to_pickle(dest/f'{code}.pkl')
            coverage[code]=dict(rows=len(f),first=int(f.time.min()),last=int(f.time.max()),prebreak_frames=counts)
            if code in codes:
                old=selection['rows'].get(code)
                if old:
                    # Full-day download must reproduce the previously frozen prefix selection.
                    u=clock.future_state(frames[details[code]['OptUndlCode']+'.SF'])
                    now=clock.restore(clock.metrics(f,details[code],u))
                    assert now==old,(date,code,'prefix changed after full download')
        write(folder/'full_coverage.json',coverage)
        print('FULL_VERIFIED',date,coverage,flush=True)
    write(OUT/'full_input_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.rglob('*') if p.is_file() and (p.parent.name=='full_inputs' or p.name in ('full_coverage.json','selection.json','catalog_terms.json'))})


if __name__=='__main__':main()
