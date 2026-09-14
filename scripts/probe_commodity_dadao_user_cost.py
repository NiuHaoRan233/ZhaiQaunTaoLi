"""User-specified 1.70 CNY/side, zero-delay continuous commodity rerun."""
from pathlib import Path
import argparse,gzip,json
import pandas as pd
from zhaiquant import commodity_dadao_research as day
from zhaiquant import commodity_dadao_continuous as continuous
from probe_commodity_dadao import DATA,REPORT,write,digest

MODEL='probe_commodity_dadao_20260912_cont_v1_last_d0_q1_f170'

def main():
    p=argparse.ArgumentParser();p.add_argument('--part',type=int,required=True);args=p.parse_args()
    plan=json.loads((REPORT/'dadao_v1_followup_selection.json').read_text(encoding='utf-8'))
    source={}
    for path in [REPORT/'dadao_v1_screen/matrix.json',*sorted(REPORT.glob('dadao_v1_validation_*/matrix.json'))]:
        source.update(json.loads(path.read_text(encoding='utf-8'))['inputs'])
    out=REPORT/f'dadao_user_f170_d0_{args.part}';out.mkdir(exist_ok=True);(out/'accounts').mkdir(exist_ok=True)
    target=out/'matrix.json'
    hashes={str(Path(m.__file__).name):digest(Path(m.__file__)) for m in [day,continuous,day.parent]}
    state=json.loads(target.read_text(encoding='utf-8')) if target.exists() else dict(model_id=MODEL,
        source_hashes=hashes,fee_per_side_cny=1.7,delay_ms=0,accounts={},inputs={},errors={},prefix_checks=0)
    assert state['source_hashes']==hashes and state['model_id']==MODEL
    codes=plan['codes'][args.part::2]
    for i,code in enumerate(codes):
        if code in state['accounts']:continue
        try:
            ms=sorted([m for m in source.values() if m['code']==code],key=lambda m:m['date']);inputs=[]
            for m in ms:
                raw=DATA/f"{code}_{m['date']}_tick.pkl";assert digest(raw)==m['sha256']
                inputs.append(day.load_frame(pd.read_pickle(raw),code=code,date=m['date'],detail=m['detail']))
            r=continuous.run(inputs,code=code,mode='last_d0',fee_cents=170)
            assert r['summary']['model_id']==MODEL
            assert all(o['due_ts']==o['created_ts'] for o in r['orders'])
            assert all(f['fee_cents']==170*f['quantity'] for f in r['fills'])
            assert r['summary']['arrival_cross_fills']==0
            if len(inputs)>2:
                for end in [len(inputs)//2,len(inputs)-1]:
                    pr=continuous.run(inputs[:end],code=code,mode='last_d0',fee_cents=170)
                    cutoff=inputs[end-1][0][-1].ts
                    assert pr['fills']==[f for f in r['fills'] if f['ts']<=cutoff]
                    assert pr['orders']==[o for o in r['orders'] if o['created_ts']<=cutoff]
                    state['prefix_checks']+=1
            s=r['summary'];s['available_dates']=[m['date'] for m in ms]
            s['missing_dates']=[d for d in plan['dates']+['20260911'] if d not in s['available_dates']]
            s['name']=ms[0]['detail'].get('ProductName') or code
            with gzip.open(out/'accounts'/f'{code}.json.gz','wt',encoding='utf-8',compresslevel=1) as f:
                json.dump(r,f,ensure_ascii=False,separators=(',',':'))
            state['accounts'][code]=s
            state['inputs'][code]=[dict(date=m['date'],sha256=m['sha256']) for m in ms]
            state['errors'].pop(code,None)
        except Exception as e:
            state['errors'][code]=str(e);print('ERROR',code,repr(e),flush=True)
        write(target,state)
        print(i+1,'/',len(codes),code,'done',len(state['accounts']),flush=True)
    print('DONE',target,flush=True)

if __name__=='__main__':main()
