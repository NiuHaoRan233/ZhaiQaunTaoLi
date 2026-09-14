"""Frozen full-path commodity ablations with exact saved-baseline comparison."""
from pathlib import Path
import argparse,gzip,hashlib,json,pickle
import pandas as pd
from zhaiquant import commodity_selective_research as engine
from zhaiquant import commodity_dadao_research as day
from probe_commodity_dadao import DATA,REPORT,write,digest

ROOT=Path(__file__).resolve().parents[1]
OUT=REPORT/'commodity_optimization_20260912'
PREFIX_CODES={'ag2612P16000.SF','cu2611P114000.SF','au2612C920.SF','au2612C840.SF',
              'CF703P17400.ZF','PK612P8400.ZF','c2611-C-2300.DF','jm2701-P-1460.DF'}

def read(p):return json.loads(p.read_text(encoding='utf-8'))
def unpack(p):return json.loads(gzip.decompress(p.read_bytes()))

def main():
    p=argparse.ArgumentParser();p.add_argument('--part',type=int,default=0);p.add_argument('--parts',type=int,default=2)
    p.add_argument('--codes',nargs='*');p.add_argument('--variants',nargs='*');p.add_argument('--smoke',action='store_true');a=p.parse_args()
    source={}
    for path in [REPORT/'dadao_v1_screen/matrix.json',*sorted(REPORT.glob('dadao_v1_validation_*/matrix.json'))]:
        source.update(read(path)['inputs'])
    plan=read(REPORT/'dadao_v1_followup_selection.json')
    folder=OUT/('smoke' if a.smoke else f'part_{a.part}')
    folder.mkdir(exist_ok=True);(folder/'accounts').mkdir(exist_ok=True)
    cache=OUT/'event_cache';cache.mkdir(exist_ok=True)
    hashes={str(Path(m.__file__).relative_to(ROOT)):digest(Path(m.__file__)) for m in [engine,day,day.parent]}
    frozen=OUT/'candidate_contract.json'
    contract=dict(family=engine.FAMILY,variants=list(engine.VARIANTS),fee_cents=170,delay_ms=0,code_list=plan['codes'],
                  source_hashes=hashes,comparison_dates=['20260907','20260908','20260909','20260910'],selection_date='20260911')
    if frozen.exists():assert read(frozen)==contract,'Frozen candidate changed'
    elif not a.smoke:write(frozen,contract)
    target=folder/'matrix.json'
    state=read(target) if target.exists() else dict(family=engine.FAMILY,source_hashes=hashes,accounts={},inputs={},errors={},prefix_checks=0,baseline_equivalences=0)
    assert state['source_hashes']==hashes
    codes=a.codes or plan['codes'][a.part::a.parts];variants=a.variants or engine.VARIANTS
    baseline_paths={p.stem.split('.json')[0]:p for p in REPORT.glob('dadao_user_f170_d0_*/accounts/*.json.gz')}
    for i,code in enumerate(codes):
        wanted=[code+'_'+v for v in variants]
        if all(k in state['accounts'] for k in wanted):continue
        try:
            ms=sorted([m for m in source.values() if m['code']==code],key=lambda m:m['date'])
            cache_path=cache/f'{code}.pkl';tag=dict(day_sha=digest(Path(day.__file__)),inputs=[(m['date'],m['sha256']) for m in ms])
            if cache_path.exists():
                with cache_path.open('rb') as f:stored=pickle.load(f)
                assert stored['tag']==tag;inputs=stored['inputs']
            else:
                inputs=[]
                for m in ms:
                    raw=DATA/f"{code}_{m['date']}_tick.pkl";assert digest(raw)==m['sha256']
                    inputs.append(day.load_frame(pd.read_pickle(raw),code=code,date=m['date'],detail=m['detail']))
                temp=cache_path.with_suffix('.tmp')
                with temp.open('wb') as f:pickle.dump(dict(tag=tag,inputs=inputs),f,protocol=5)
                temp.replace(cache_path)
            state['inputs'][code]=tag
            prepared=engine.prepare(inputs)
            for variant in variants:
                key=code+'_'+variant
                if key in state['accounts']:continue
                r=engine.run(inputs,code=code,variant=variant,prepared=prepared)
                if variant=='control':
                    saved=unpack(baseline_paths[code])
                    def economic(items):return [{k:v for k,v in x.items() if k!='model_id'} for x in items]
                    assert economic(r['fills'])==economic(saved['fills']),f'Control fills differ {code}'
                    assert economic(r['orders'])==economic(saved['orders']),f'Control orders differ {code}'
                    assert r['curve']==saved['curve'] or [list(x) for x in r['curve']]==saved['curve']
                    for f in ['pnl_cny','end_cash_cny','end_inventory','fees_cny','max_drawdown_cny','holding_seconds']:
                        assert r['summary'][f]==saved['summary'][f],f
                    assert r['daily']==saved['daily'];state['baseline_equivalences']+=1
                if code in PREFIX_CODES and len(inputs)>2:
                    for end in [len(inputs)//2,len(inputs)-1]:
                        pr=engine.run(inputs[:end],code=code,variant=variant);cutoff=inputs[end-1][0][-1].ts
                        assert pr['fills']==[f for f in r['fills'] if f['ts']<=cutoff]
                        assert pr['orders']==[o for o in r['orders'] if o['created_ts']<=cutoff]
                        state['prefix_checks']+=1
                s=r['summary'];s.update(name=ms[0]['detail'].get('ProductName') or code,
                    missing_dates=[d for d in plan['dates']+['20260911'] if d not in s['available_dates']])
                with gzip.open(folder/'accounts'/f'{key}.json.gz','wt',encoding='utf-8',compresslevel=1) as f:
                    json.dump(r,f,ensure_ascii=False,separators=(',',':'))
                state['accounts'][key]=s
            state['errors'].pop(code,None)
        except Exception as e:
            state['errors'][code]=str(e);print('ERROR',code,repr(e),flush=True)
            if a.smoke:raise
        write(target,state)
        print(i+1,'/',len(codes),code,'accounts',len(state['accounts']),flush=True)
    print('DONE',target,flush=True)

if __name__=='__main__':main()
