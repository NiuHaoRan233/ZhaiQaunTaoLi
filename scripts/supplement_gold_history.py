"""Actual two20k-slot replay, both daily-reset and continuously carried cash."""
import pandas as pd
from zhaiquant.gold_history_validation import Clock, timeline
from validate_gold_history import replay
from capture_gold_history import OUT,DATES,FIXED
from probe_commodity_capital import read,write,pack


def main():
    dest=OUT/'small_capital';dest.mkdir(exist_ok=True)
    if (dest/'results.json').exists():return
    details=read(OUT/'catalog_terms.json')['details'];results={}
    cash={(code,through):20000 for code in FIXED for through in (False,True)}
    stopped=set()
    for date in DATES:
        clock=Clock(date);folder=OUT/date
        assert set(read(folder/'selection.json')['selected'])==set(FIXED),'Separate slot-mapping plan required if selections diverge'
        for code in FIXED:
            detail=details[code];f=pd.read_pickle(folder/'full_inputs'/f'{code}.pkl')
            fu=pd.read_pickle(folder/'full_inputs'/f'{detail["OptUndlCode"]}.SF.pkl')
            es,fs,_,_=clock.inputs(f,fu,code,detail)
            rows=timeline(es,fs,detail['OptExercisePrice'],detail['ExpireDate'],clock)
            for through in (False,True):
                for mode in ('daily_reset','carry_cash'):
                    key=f'{date}_{code}_{mode}_through{int(through)}'
                    if mode=='carry_cash' and (code,through) in stopped:
                        results[key]=dict(status='blocked_after_flatten_failure',history_date=date,code=code,pnl_cny=None)
                        continue
                    capital=20000 if mode=='daily_reset' else cash[(code,through)]
                    r=replay(rows,code,'v02_trend_long',detail,clock,through,capital)
                    r['summary'].update(funding_mode=mode)
                    r=clock.restore(r);pack(dest/f'{key}.json.gz',r);results[key]=r['summary']
                    if mode=='carry_cash':
                        if r['summary']['status']=='complete':cash[(code,through)]=r['summary']['end_cash_cny']
                        else:stopped.add((code,through))
                    print('SMALL',date,code,through,mode,capital,r['summary']['pnl_cny'],r['summary']['status'],flush=True)
    write(dest/'results.json',results)


if __name__=='__main__':main()
