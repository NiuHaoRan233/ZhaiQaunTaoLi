"""Freeze a diverse follow-up list from the completed one-day broad screen."""
from collections import defaultdict
from pathlib import Path
import hashlib
import json
import re

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'广义套利'/'reports'
source=REPORT/'dadao_v1_screen'/'matrix.json'
s=json.loads(source.read_text(encoding='utf-8'))
old=json.loads((REPORT/'followup_selection.json').read_text(encoding='utf-8'))['codes']
quality=json.loads((REPORT/'intraday_screen.json').read_text(encoding='utf-8'))['rows']
rs={r['code']:r for r in s['accounts'].values() if r['mode']=='last_d500' and r['fee_per_side_cny']==5}
if len(rs)!=343:raise ValueError('Broad screen is not complete')
slow={r['code']:r for r in s['accounts'].values() if r['mode']=='last_d1000' and r['fee_per_side_cny']==5}
groups=defaultdict(list)
for code,r in rs.items():
    q=quality[code+'_20260911'];m=q['metrics'];d=q['detail']
    unit=d.get('OptUnit') or d.get('VolumeMultiple')
    premium=m.get('mean_mid',0)*unit if unit else 0
    if (r['complete_cycles']<2 or r['stale_tail'] or m.get('valid_coverage_pct',0)<60
        or d.get('ExpireDate','0')<'20260926' or premium<=0):continue
    worst=min(r['pnl_cny'],slow[code]['pnl_cny'])
    if worst < -max(100,premium*.005):continue
    product=re.match('[A-Za-z]+',code)[0]
    groups[product].append(dict(code=code,product=product,pnl_cny=r['pnl_cny'],
        slow_pnl_cny=slow[code]['pnl_cny'],cycles=r['complete_cycles'],
        reference_premium_cny=premium,rank_value=worst/premium))
chosen=[c for c in old if not c.endswith('.SH')]
reasons={c:'First-round quote shortlist retained, including losers' for c in chosen}
for product,rows in groups.items():rows.sort(key=lambda r:(r['rank_value'],r['cycles']),reverse=True)
ordered=sorted((rows[0] for rows in groups.values()),key=lambda r:r['rank_value'],reverse=True)
ordered+=sorted((r for rows in groups.values() for r in rows[1:3]),key=lambda r:r['rank_value'],reverse=True)
for r in ordered:
    if r['code'] not in chosen:
        chosen.append(r['code']);reasons[r['code']]='Two-delay small-profit/near-flat screen with >=2 rounds; exploratory and selected in hindsight'
    if len(chosen)>=64:break
payload=dict(screen_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),codes=chosen,
    reasons=reasons,eligible_by_product=dict(groups),
    dates=['20260824','20260825','20260826','20260827','20260828','20260831',
           '20260901','20260902','20260903','20260904','20260907','20260908','20260909','20260910'],
    note='Historical follow-up, selected after seeing 09-11. No out-of-sample claim; no model/fee thresholds changed.')
out=REPORT/'dadao_v1_followup_selection.json'
if out.exists():raise FileExistsError('Frozen selection already exists')
out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
print('selected',len(chosen),'products',len({re.match('[A-Za-z]+',c)[0] for c in chosen}))
print('\n'.join(chosen))
