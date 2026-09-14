"""Select a bounded, diverse follow-up set and inspect all five saved dates."""
from pathlib import Path
from datetime import datetime
from collections import Counter
import json
import subprocess
import sys
from scan_history import EXCHANGES, OPTION, DATA, REPORT, dump

sys.stdout.reconfigure(encoding='utf-8')
s=json.loads((REPORT/'intraday_screen.json').read_text(encoding='utf-8'))
rs=[]
for r in s['rows'].values():
    if r['date']!='20260911' or not OPTION.fullmatch(r['code']):continue
    m=r['metrics'];expiry=r['detail'].get('ExpireDate','0')
    if m.get('valid_coverage_pct',0)>=60 and m.get('volume_increment_events',0)>=30 and m.get('incremental_amount',0)>=100000 and expiry>='20260926':rs.append(r)
rs.sort(key=lambda r:r['metrics']['mid_p95_p05_pct'])
counts=Counter();chosen=[]
for r in rs:
    product=OPTION.fullmatch(r['code'])[1]
    if counts[product]>=2:continue
    counts[product]+=1;chosen.append(r)
    if len(chosen)>=20:break
extra='m2701-P-3050.DF_20260911'
if extra in s['rows']:chosen.append(s['rows'][extra])
codes=[r['code'] for r in chosen]+['132024.SH','132026.SH']
under=[]
for r in chosen:
    d=r['detail'];u=d.get('OptUndlCode','');e=EXCHANGES.get(d.get('OptUndlMarket',''))
    if u and e:under.append(u+'.'+e)
dump(REPORT/'followup_selection.json',dict(codes=codes,underlyings=sorted(set(under)),
    reason='09-11有效覆盖>=60%、成交量增量事件>=30、日盘金额增量>=10万元、到期至少15天，按盘口中点P95-P05幅度排序，每品种最多2只、共20只；另加豆粕低金额对照与双债。是探索抽样，不是交易门槛或样本外验证。'))
for selected,dates in [(codes,['20260907','20260908','20260909','20260910']),
    (sorted(set(under)),['20260907','20260908','20260909','20260910','20260911']),
    (['hc2701C3350.SF','hc2701P3250.SF','ss2612C14000.SF','ss2612P13800.SF'],['20260910'])]:
    subprocess.run([sys.executable,str(Path(__file__).with_name('scan_history.py')),'--dates',*dates,'--codes',*selected],check=True)
