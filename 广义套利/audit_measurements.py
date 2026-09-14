"""Audit local quote evidence, units, increments and provenance without downloading."""
import hashlib
import json
import math
from collections import Counter
import numpy as np
import pandas as pd
from scan_history import DATA,REPORT,OPTION,measure,segments,dump

s=json.loads((REPORT/'intraday_screen.json').read_text(encoding='utf-8'))
daily=json.loads((DATA/'daily_20260907_20260911.json').read_text(encoding='utf-8'))
inv=json.loads(sorted(DATA.glob('commodity_inventory_*.json'))[-1].read_text(encoding='utf-8'))
out=dict(files={},issues=[],daily_unit_checks=0,daily_unit_exceptions=[],rows=0)
units={}
for c,d in inv['details'].items():
    m=OPTION.fullmatch(c)
    if m and d:units[(m[1],m[5])]=d.get('OptUnit') or d.get('VolumeMultiple')
for c,bars in daily['bars'].items():
    m=OPTION.fullmatch(c)
    if not m:continue
    unit=units.get((m[1],m[5]))
    if not unit:continue
    for b in bars:
        if b['volume']<=0 or b['amount']<=0 or b['low']<=0:continue
        avg=b['amount']/b['volume']/unit;out['daily_unit_checks']+=1
        if not b['low']-1e-5<=avg<=b['high']+1e-5:
            out['daily_unit_exceptions'].append(dict(code=c,date=b['index'],unit=unit,average=avg,low=b['low'],high=b['high']))
for key,r in s['rows'].items():
    path=DATA/f'{key}_tick.pkl';f=pd.read_pickle(path)
    out['rows']+=len(f)
    out['files'][path.name]=dict(rows=len(f),sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    date=r['date'];bond=r['code'].endswith('.SH');d=r['detail']
    # Fix EB report units explicitly: one hand is 10 bonds, never 1,000.
    if bond:
        r['metrics']=measure(f,date,d,True)
        r['sensitivity_300']=measure(f,date,d,True,cap=300)
        r['common']=measure(f,date,d,True,common=True)
    actual=pd.to_datetime(f.time,unit='ms',utc=True).dt.tz_convert('Asia/Shanghai').dt.strftime('%Y%m%d')
    if not (actual==date).all():out['issues'].append(dict(key=key,issue='unexpected date'))
    g=f.sort_values('time',kind='stable').drop_duplicates('time',keep='last')
    # Independent interval sum without the vectorized measure implementation.
    t=g.time.to_numpy(float)/1000;observed=0
    for a,b in segments(date,bond):
        ii=np.flatnonzero((t>=a)&(t<b))
        for j,i in enumerate(ii):
            row=g.iloc[i]
            if row.bidPrice[0]>0 and row.askPrice[0]>row.bidPrice[0] and row.bidVol[0]>0 and row.askVol[0]>0:
                observed+=min(60,(t[ii[j+1]] if j+1<len(ii) else b)-t[i])
    measured=r['metrics'].get('valid_minutes',0)*60
    if abs(observed-measured)>1e-5:out['issues'].append(dict(key=key,issue='coverage mismatch',actual=observed,reported=measured))
    for which in ['metrics','common','sensitivity_300']:
        m=r[which]
        if not 0<=m.get('valid_coverage_pct',0)<=100.00001:out['issues'].append(dict(key=key,issue='invalid coverage'))
        if m.get('spread_ge_05_minutes',0)>m.get('valid_minutes',0)+1e-8:out['issues'].append(dict(key=key,issue='wide exceeds valid'))
dump(REPORT/'intraday_screen.json',s)
out['status']=('passed_with_vendor_daily_warnings' if out['daily_unit_exceptions'] else 'passed') if not out['issues'] else 'failed'
dump(REPORT/'data_audit.json',out)
print(json.dumps({k:v for k,v in out.items() if k not in ['files','daily_unit_exceptions']} | {'daily_unit_exception_count':len(out['daily_unit_exceptions'])},ensure_ascii=False,indent=2),flush=True)
