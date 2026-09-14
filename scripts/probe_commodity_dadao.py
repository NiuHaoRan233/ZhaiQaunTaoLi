"""Run frozen commodity Dadao v1 against saved or read-only downloaded history."""
from pathlib import Path
from dataclasses import replace
from datetime import datetime
import argparse
import gzip
import hashlib
import json
import sys
import tomllib
import pandas as pd
from zhaiquant import commodity_dadao_research as engine

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'广义套利'
DATA=BASE/'data'
REPORT=BASE/'reports'


def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,obj):
    temp=p.with_suffix(p.suffix+'.tmp')
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temp.replace(p)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser();p.add_argument('--output',required=True)
    p.add_argument('--codes',nargs='*');p.add_argument('--dates',nargs='+',default=['20260911'])
    p.add_argument('--download',action='store_true');p.add_argument('--prefix-checks',action='store_true')
    args=p.parse_args();out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    (out/'accounts').mkdir(exist_ok=True)
    inv=json.loads(sorted(DATA.glob('commodity_inventory_*.json'))[-1].read_text(encoding='utf-8'))
    intra=json.loads((REPORT/'intraday_screen.json').read_text(encoding='utf-8'))
    details={r['code']:r['detail'] for r in intra['rows'].values()}
    sys.path.insert(0,str(BASE));from probe_commodity_options import OPTION
    codes=args.codes or sorted({r['code'] for r in intra['rows'].values() if OPTION.fullmatch(r['code'])})
    jobs=[(mode,500) for mode in engine.MODES]+[('last_d500',fee) for fee in [0,200,1000]]
    target=out/'matrix.json'
    frozen=digest(Path(engine.__file__))
    state=json.loads(target.read_text(encoding='utf-8')) if target.exists() else dict(
        family=engine.FAMILY,engine_sha256=frozen,parent_sha256=engine.PARENT_SHA256,
        started_at=datetime.now().isoformat(),accounts={},inputs={},errors={},prefix_checks=0,
        contract='Independent one-contract day accounts; latest-contract evidence; 0/2/5/10 CNY fee scenarios, not actual rates; no hedge or overnight liquidation.')
    if state['engine_sha256']!=frozen:raise ValueError('Frozen v1 source mismatch')
    xtdata=None
    for ci,code in enumerate(codes):
        detail=details.get(code) or inv['details'].get(code)
        for date in args.dates:
            key=f'{code}_{date}';path=DATA/f'{key}_tick.pkl'
            wanted=[f'{key}_{mode}_f{fee}' for mode,fee in jobs]
            if all(k in state['accounts'] for k in wanted):continue
            try:
                if not path.exists():
                    if not args.download:raise FileNotFoundError(path)
                    if xtdata is None:
                        from xtquant import xtdata
                        xtdata.enable_hello=False
                        xtdata.connect(port=tomllib.loads((ROOT/'config.toml').read_text(encoding='utf-8-sig'))['qmt']['port'])
                    if not detail:detail=xtdata.get_instrument_detail(code,True)
                    if detail and detail.get('OpenDate','0')>date:raise ValueError('Before instrument OpenDate')
                    xtdata.download_history_data(code,'tick',date+'090000',date+'153000')
                    frame=xtdata.get_market_data_ex([],[code],period='tick',start_time=date+'090000',end_time=date+'153000',fill_data=False).get(code)
                    if frame is None or frame.empty:raise ValueError('No history returned')
                    frame.to_pickle(path)
                else:frame=pd.read_pickle(path)
                events,unit_evidence,meta=engine.load_frame(frame,code=code,date=date,detail=detail)
                meta['sha256']=digest(path);meta['detail']=detail
                if key in state['inputs'] and state['inputs'][key]['sha256']!=meta['sha256']:
                    raise ValueError('Saved input changed')
                state['inputs'][key]=meta
                for mode,fee in jobs:
                    account=f'{key}_{mode}_f{fee}'
                    if account in state['accounts']:continue
                    kwargs=dict(code=code,date=date,mode=mode,fee_cents=fee,
                        initial_cents=meta['initial_cents'],tick_cents=meta['tick_cents'])
                    result=engine.run(events,unit_evidence,**kwargs)
                    engine.audit_result(result)
                    if args.prefix_checks and fee==500 and mode in ['last_d500','queue_d500']:
                        for end in [len(events)//3,len(events)*2//3]:
                            if end<1:continue
                            prefix=engine.run(events[:end],unit_evidence[:end],**kwargs);ts=events[end-1].ts
                            assert prefix['fills']==[x for x in result['fills'] if x['ts']<=ts]
                            assert prefix['orders']==[x for x in result['orders'] if x['created_ts']<=ts]
                            state['prefix_checks']+=1
                    with gzip.open(out/'accounts'/f'{account}.json.gz','wt',encoding='utf-8',compresslevel=1) as stream:
                        json.dump(result,stream,ensure_ascii=False,separators=(',',':'))
                    state['accounts'][account]=result['summary']
                state['errors'].pop(key,None)
            except Exception as exc:
                state['errors'][key]=dict(error=type(exc).__name__,message=str(exc))
                print('ERROR',key,str(exc),flush=True)
            state['updated_at']=datetime.now().isoformat();write(target,state)
        if ci%10==0 or ci==len(codes)-1:
            print(ci+1,'/',len(codes),code,'accounts',len(state['accounts']),'errors',len(state['errors']),flush=True)
    print('DONE',target,len(state['accounts']),'accounts',len(state['inputs']),'input days',flush=True)


if __name__=='__main__':main()
