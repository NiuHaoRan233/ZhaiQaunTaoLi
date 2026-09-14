"""Outcome-only attribution for selected adverse historical days and a sparse winner."""
import gzip
import json
from pathlib import Path
import pandas as pd

from probe_option_top_cycle import ARCHIVE
from audit_option_cycle_losses import analyze
from zhaiquant.option_history_replay import load_day

DATA=ARCHIVE/'近月行情_20260810_20260909'
OUT=ARCHIVE/'大道至简近月验证_20260810_20260909_v3'


def main():
    info=json.loads((DATA/'capture_manifest.json').read_text(encoding='utf-8'))
    cases=[]
    for code,date in [('10012084.SHO','20260827'),('10012348.SHO','20260904'),('10012359.SHO','20260827'),
                      ('90007929.SZO','20260827'),('90007929.SZO','20260828')]:
        events,_=load_day(pd.read_pickle(DATA/f'{code}_{date}.pkl'),date,info['contracts'][code]['details'])
        r=json.load(gzip.open(OUT/f'{code}_{date}_entry_improve_single_d500_q1_b600_f170.json.gz','rt',encoding='utf-8'))
        diagnosis=analyze(events,r)
        case=dict(code=code,date=date,summary=r['summary'],decomposition=diagnosis['decomposition'],
                  duration_groups={k:v for k,v in diagnosis['groups'].items() if k.startswith('hold_')},
                  worst=diagnosis['worst'],fills=r['fills'])
        cases.append(case)
        print(code,date,case['decomposition'],case['duration_groups'],flush=True)
        if code=='90007929.SZO':print('sparse fills',[(f['side'],f['kind'],f['source_quantity'],f['quantity']) for f in r['fills']],flush=True)
    (OUT/'case_attribution.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
