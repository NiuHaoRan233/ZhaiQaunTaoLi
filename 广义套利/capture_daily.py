"""Download recent daily bars for the entire enumerated commodity universe."""
from pathlib import Path
from datetime import datetime
import json
import sys
import tomllib
from xtquant import xtdata

sys.stdout.reconfigure(encoding='utf-8')
BASE=Path(__file__).resolve().parent
OUT=BASE/'data'
xtdata.enable_hello=False
xtdata.connect(port=tomllib.loads((BASE.parent/'config.toml').read_text(encoding='utf-8-sig'))['qmt']['port'])
inventory=json.loads(sorted(OUT.glob('commodity_inventory_*.json'))[-1].read_text(encoding='utf-8'))
codes=set()
for v in inventory['sectors'].values(): codes.update(v['options']+v['futures'])
codes=sorted(codes)+['132024.SH','132026.SH']
target=OUT/'daily_20260907_20260911.json'
state=json.loads(target.read_text(encoding='utf-8')) if target.exists() else dict(source='MiniQMT xtdata',start='20260907',end='20260911',bars={},errors=[],completed=[])
todo=[c for c in codes if c not in state['completed']]
for i in range(0,len(todo),500):
    batch=todo[i:i+500]
    print('download',i,'/',len(todo),flush=True)
    try:
        xtdata.download_history_data2(batch,'1d',state['start'],state['end'])
        frames=xtdata.get_market_data_ex([],batch,period='1d',start_time=state['start'],end_time=state['end'],fill_data=False)
        for c,f in frames.items():
            if f is not None and not f.empty:
                state['bars'][c]=f.reset_index().to_dict('records')
        state['completed'].extend(batch)
    except Exception as e:
        state['errors'].append(dict(batch=batch,error=str(e)))
        print('ERROR',str(e),flush=True)
    state['captured_at']=datetime.now().isoformat()
    target.write_text(json.dumps(state,ensure_ascii=False,default=str),encoding='utf-8')
    print('received',len(state['bars']),'completed',len(state['completed']),flush=True)
print('DONE',len(state['bars']),flush=True)
