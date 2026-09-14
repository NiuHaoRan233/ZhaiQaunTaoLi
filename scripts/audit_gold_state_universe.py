"""Check all listed gold option codes, including those without cached metadata."""
from pathlib import Path
import math
import pickle
import re
import tomllib
import pandas as pd
from zhaiquant import gold_state_research as engine
from probe_gold_state import OUT as BASE
from probe_commodity_capital import ROOT,WORK,read,write,digest

OUT=WORK/'reports/gold_state_universe_audit_20260913'


def main():
    from xtquant import xtdata
    OUT.mkdir(exist_ok=True)
    cat=read(WORK/'data/option_dashboard/catalog.json');months=read(BASE/'month_selection.json')
    prior=read(BASE/'selection.json');details=read(BASE/'catalog_terms.json')['details']
    codes=sorted({c for sec in cat['sectors'].values() for c in sec.get('options',[])
        if re.fullmatch(r'au\d{4}[CP]\d+\.SF',c)})
    excluded={};pool=[]
    for c in codes:
        m=re.fullmatch(r'(au\d{4})[CP](\d+)\.SF',c);future=m[1]+'.SF'
        if future not in months['selected']:excluded[c]='outside_top_two_future_months';continue
        if abs(math.log(months['states'][future]['mid']/float(m[2])))>engine.CRITERIA['maximum_absolute_log_moneyness']:
            excluded[c]='deep_moneyness';continue
        pool.append(c)
    missing=sorted(set(pool)-prior['rows'].keys())
    write(OUT/'audit_plan.json',dict(all_codes=codes,additional=missing,pool=pool,excluded=excluded,
        original_selection_sha256=digest(BASE/'selection.json'),criterion_source_sha256=digest(Path(engine.__file__)),
        rule='Same frozen first30min criteria; complete sector code inventory, never profit ranking.'))
    xtdata.enable_hello=False
    client=xtdata.connect(port=tomllib.loads((ROOT/'config.toml').read_text('utf-8-sig'))['qmt']['port'])
    assert client.is_connected()
    rows={c:prior['rows'][c] for c in pool if c in prior['rows']}
    for c in missing:
        d=xtdata.get_instrument_detail(c,True);details[c]=d
        if not d or d.get('OpenDate','99999999')>engine.DATE or d.get('ExpireDate','')<engine.DATE:
            rows[c]=dict(eligible=False,reasons=['unavailable_or_outside_listing_dates']);continue
        xtdata.download_history_data(c,'tick',engine.DATE+'090000',engine.DATE+'093000')
        f=xtdata.get_market_data_ex([],[c],period='tick',start_time=engine.DATE+'090000',end_time=engine.DATE+'093000',fill_data=False).get(c)
        if f is None or f.empty:rows[c]=dict(eligible=False,reasons=['data_unavailable']);continue
        (OUT/f'{c}.pkl').write_bytes(pickle.dumps(f,protocol=5))
        u=months['states'][d['OptUndlCode']+'.SF'];rows[c]=engine.state_metrics(f,d,u)
        assert rows[c]==engine.state_metrics(engine.prefix_frame(f),d,u)
        print('EXTRA',c,rows[c],flush=True)
    ranked=sorted([(c,m) for c,m in rows.items() if m['eligible']],key=engine.rank_key)
    selected=[c for c,m in ranked[:2]]
    write(OUT/'result.json',dict(status='passed' if selected==prior['selected'] else 'selection_changed',
        all_catalogue_codes=len(codes),preliminary_pool=len(pool),additional_metadata_checked=len(missing),
        eligible=len(ranked),ranked=[c for c,m in ranked],rows=rows,selected=selected,
        original_selected=prior['selected'],selection_unchanged=selected==prior['selected']))
    write(OUT/'details.json',{c:details[c] for c in missing})
    write(OUT/'manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.glob('*') if p.name!='manifest.json'})
    assert selected==prior['selected'],'Complete universe changes selection; register a new replay instead of hiding it'
    print('COMPLETE UNIVERSE VERIFIED',len(codes),len(pool),len(ranked),selected,flush=True)


if __name__=='__main__':main()
