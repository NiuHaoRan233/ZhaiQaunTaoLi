"""Read-only QMT capture: freeze daily percentage selections before replay."""
import re,math
from pathlib import Path
import pandas as pd
from zhaiquant import gold_reselection_research as model
from zhaiquant.gold_history_validation import Clock
from probe_commodity_capital import ROOT,WORK,read,write,digest

OUT=WORK/'reports/gold_reselection_20260914_v1'
DATES=['20260907','20260908','20260909','20260910','20260911']


def main():
    from xtquant import xtdata
    import tomllib
    OUT.mkdir(exist_ok=True)
    if (OUT/'capture_manifest.json').exists():print('Capture already frozen');return
    xtdata.enable_hello=False
    port=tomllib.loads((ROOT/'config.toml').read_text('utf-8-sig'))['qmt']['port']
    assert xtdata.connect(port=port).is_connected()
    cat=read(WORK/'data/option_dashboard/catalog.json')
    universe={c for s in cat['sectors'].values() for c in s.get('options',[]) if re.fullmatch(r'au\d{4}[CP]\d+\.SF',c)}
    universe.update(c for c in cat['details'] if re.fullmatch(r'au\d{4}(?:[CP]\d+)?\.SF',c))
    details={c:cat['details'].get(c) or xtdata.get_instrument_detail(c,True) for c in sorted(universe)}
    write(OUT/'catalog_terms.json',dict(details=details,scope='Current catalogue, listing/expiry checked per day; may omit expired historical inventory'))
    write(OUT/'plan.json',dict(family=model.FAMILY,dates=DATES,bps=model.THRESHOLDS,excluded=model.EXCLUDED,
        selection='Each day09:00-09:30;2 most balanced eligible flows, percentage and positive net-space time>=50%; each side>=3updates',
        universe='Calls and puts, historical top2 futures months,abs log moneyness<=.08,prior state quality gates',
        fee_one_side_cny=1.7,extra_delay_ms=0,settlement='exclude remaining break cycles including fees',
        execution='09:30-15:00, independent fixed long/short,9 inherited policies,ordinary/strict fills',
        source_hashes={str(p.relative_to(ROOT)):digest(p) for p in [Path(__file__),ROOT/'src/zhaiquant/gold_reselection_research.py']}))
    for date in DATES:
        clock=Clock(date);folder=OUT/date;folder.mkdir(exist_ok=True)
        def fetch(codes,end,directory):
            directory.mkdir(exist_ok=True)
            for code in codes:
                p=directory/f'{code}.pkl'
                if p.exists():continue
                # Frozen old local files may supply prefixes; never mutate them.
                candidates=[WORK/f'reports/gold_history_20260913_v1/{date}/prefix/{code}.pkl'] if end=='093000' else []
                if date=='20260911':candidates += [WORK/f'reports/gold_state_20260913_v1/inputs/{code}.pkl',WORK/f'reports/gold_state_universe_audit_20260913/{code}.pkl']
                source=next((x for x in candidates if x.exists()),None)
                f=pd.read_pickle(source) if source else None
                hi=clock.start+(1800000 if end=='093000' else 21600000)
                if f is None or f.empty or int(f.time.max())<hi-60000:
                    xtdata.download_history_data(code,'tick',date+'090000',date+end)
                    f=xtdata.get_market_data_ex([],[code],period='tick',start_time=date+'090000',end_time=date+end,fill_data=False).get(code)
                if f is None:f=pd.DataFrame()
                if not f.empty:f=f[(f.time>=clock.start)&(f.time<hi)]
                f.to_pickle(p)
        terms={c:d for c,d in details.items() if d and d.get('OpenDate','99999999')<=date<=d.get('ExpireDate','')}
        futures=sorted(c for c in terms if re.fullmatch(r'au\d{4}\.SF',c))
        fetch(futures,'093000',folder/'prefix')
        states={c:clock.future_state(pd.read_pickle(folder/'prefix'/f'{c}.pkl')) for c in futures if not pd.read_pickle(folder/'prefix'/f'{c}.pkl').empty}
        states={c:s for c,s in states.items() if s};months=sorted(states,key=lambda c:(-states[c]['open_interest'],-states[c]['observed_volume_increment'],c))[:2]
        assert len(months)==2
        pool=[c for c,d in terms.items() if re.fullmatch(r'au\d{4}[CP]\d+\.SF',c) and c not in model.EXCLUDED and
            d.get('OptUndlCode','')+'.SF' in months and abs(math.log(states[d['OptUndlCode']+'.SF']['mid']/d['OptExercisePrice']))<=.08]
        for i in range(0,len(pool),10):
            fetch(pool[i:i+10],'093000',folder/'prefix');print('PREFIX',date,min(i+10,len(pool)),len(pool),flush=True)
        selections={}
        for bps in model.THRESHOLDS:
            rows={}
            for code in pool:
                f=pd.read_pickle(folder/'prefix'/f'{code}.pkl');d=terms[code]
                rows[code]=model.metrics(f,d,states[d['OptUndlCode']+'.SF'],clock,bps) if not f.empty else dict(eligible=False,reasons=['data_unavailable'])
            selections[str(bps)]=dict(rows=rows,selected=model.select(rows))
        selection=dict(date=date,months=months,states=states,scenarios=selections,
            selection_end_exclusive=clock.start+1800000,excluded=model.EXCLUDED,no_profit_selection=True)
        target=folder/'selection.json'
        if target.exists():assert read(target)==selection,'Frozen selection changed'
        else:write(target,selection)
        print('SELECTED',date,{b:s['selected'] for b,s in selections.items()},flush=True)
        chosen=sorted({c for s in selections.values() for c in s['selected']})
        fetch(sorted(set(chosen+[terms[c]['OptUndlCode']+'.SF' for c in chosen])),'150000',folder/'full_inputs')
        coverage={}
        for code in chosen:
            f=pd.read_pickle(folder/'full_inputs'/f'{code}.pkl');d=terms[code]
            assert not f.empty and int(f.time.max())>=clock.start+21600000-60000,(date,code,'missing close')
            for bps in model.THRESHOLDS:
                now=model.metrics(f,d,states[d['OptUndlCode']+'.SF'],clock,bps)
                assert now==selections[str(bps)]['rows'][code],(date,code,'prefix changed')
            coverage[code]=dict(rows=len(f),first=int(f.time.min()),last=int(f.time.max()))
        write(folder/'coverage.json',coverage)
    write(OUT/'capture_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.rglob('*') if p.is_file()})
    print('CAPTURE FROZEN',flush=True)


if __name__=='__main__':main()
