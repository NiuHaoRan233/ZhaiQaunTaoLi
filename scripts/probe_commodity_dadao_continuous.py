"""Run a frozen follow-up selection with continuous cash and positions."""
from pathlib import Path
import argparse,gzip,hashlib,json,sys
import pandas as pd
from zhaiquant import commodity_dadao_research as day
from zhaiquant import commodity_dadao_continuous as continuous
from probe_commodity_dadao import ROOT,DATA,REPORT,write,digest


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser();p.add_argument('--part',type=int,required=True);p.add_argument('--parts',type=int,default=2)
    args=p.parse_args()
    plan=json.loads((REPORT/'dadao_v1_followup_selection.json').read_text(encoding='utf-8'))
    sources=[REPORT/'dadao_v1_screen/matrix.json']+sorted(REPORT.glob('dadao_v1_validation_*/matrix.json'))
    inp={}
    for path in sources:inp.update(json.loads(path.read_text(encoding='utf-8'))['inputs'])
    out=REPORT/f'dadao_cont_v1_{args.part}';out.mkdir(exist_ok=True);(out/'accounts').mkdir(exist_ok=True)
    target=out/'matrix.json';sha=digest(Path(continuous.__file__));dsha=digest(Path(day.__file__))
    state=json.loads(target.read_text(encoding='utf-8')) if target.exists() else dict(family=continuous.FAMILY,
        engine_sha256=sha,day_engine_sha256=dsha,accounts={},inputs={},errors={},prefix_checks=0)
    assert state['engine_sha256']==sha and state['day_engine_sha256']==dsha
    jobs=[('last_d500',fee) for fee in [0,200,500,1000]]+[('last_d1000',500),('queue_d500',500)]
    codes=plan['codes'][args.part::args.parts]
    for i,code in enumerate(codes):
        try:
            wanted=[f'{code}_{mode}_f{fee}' for mode,fee in jobs]
            if all(k in state['accounts'] for k in wanted):continue
            ms=sorted([m for m in inp.values() if m['code']==code],key=lambda m:m['date'])
            inputs=[]
            for m in ms:
                path=DATA/f"{code}_{m['date']}_tick.pkl"
                assert digest(path)==m['sha256']
                inputs.append(day.load_frame(pd.read_pickle(path),code=code,date=m['date'],detail=m['detail']))
            state['inputs'][code]=[dict(date=m['date'],sha256=m['sha256']) for m in ms]
            for mode,fee in jobs:
                key=f'{code}_{mode}_f{fee}'
                if key in state['accounts']:continue
                result=continuous.run(inputs,code=code,mode=mode,fee_cents=fee)
                # Through whole earlier days: no future-day prices/funding are used.
                if mode=='last_d500' and fee==500 and len(inputs)>2:
                    for end in [len(inputs)//2,len(inputs)-1]:
                        pr=continuous.run(inputs[:end],code=code,mode=mode,fee_cents=fee)
                        cutoff=inputs[end-1][0][-1].ts
                        assert pr['fills']==[f for f in result['fills'] if f['ts']<=cutoff]
                        assert pr['orders']==[o for o in result['orders'] if o['created_ts']<=cutoff]
                        state['prefix_checks']+=1
                s=result['summary'];s['available_dates']=[m['date'] for m in ms]
                s['missing_dates']=[d for d in plan['dates']+['20260911'] if d not in s['available_dates']]
                with gzip.open(out/'accounts'/f'{key}.json.gz','wt',encoding='utf-8',compresslevel=1) as f:
                    json.dump(result,f,ensure_ascii=False,separators=(',',':'))
                state['accounts'][key]=s
            state['errors'].pop(code,None)
        except Exception as e:
            state['errors'][code]=str(e);print('ERROR',code,type(e).__name__,str(e),flush=True)
        write(target,state)
        if i%5==0 or i==len(codes)-1:print(i+1,'/',len(codes),'accounts',len(state['accounts']),'errors',len(state['errors']),flush=True)
    print('DONE',target,flush=True)


if __name__=='__main__':main()
