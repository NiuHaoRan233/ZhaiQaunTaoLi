"""Read-only matched baseline warning audit; outcomes never fed into strategy."""
from bisect import bisect_left,bisect_right
from collections import defaultdict
import numpy as np
from probe_gold_peers import OUT,DATES,read,write,unpack


def bad(s,d,kind):
    if not s['ready']:return False
    prices=[s['future_fair_cents']] if kind=='future_only' else [p['fair_cents'] for p in s['peers']]
    moves=[d*(p-s['target_mid_cents']) for p in prices]
    return (moves[0]<=-4000) if kind=='future_only' else sum(x<=-4000 for x in moves)>=2 and float(np.median(moves))<=-4000


def main():
    summaries=read(OUT/'results.json');rows=[];cache={}
    for key,s in summaries.items():
        if s['peer_variant']!='baseline' or s['strict_through']:continue
        date=s['history_date'];code=s['code'];d=s['fixed_direction'];k=(date,code,d)
        if k not in cache:
            sig=unpack(OUT/'signals'/f'{date}_{code}.json.gz')
            cache[k]={kind:[p['ts'] for p in sig if bad(p,d,kind)] for kind in ('future_only','peer_cancel')}
        r=unpack(OUT/'ledgers'/f'{key}.json.gz');orders={o['id']:o for o in r['orders']}
        fills={f['ts']:f for f in r['fills'] if not f['closing']}
        for c in r['cycles']:
            if c['exit_kind']=='virtual_cost_close':continue
            fill=fills[c['entry_ts']];o=orders[fill['order_id']]
            row=dict(date=date,code=code,mask=s['session_mask'],policy=s['policy'],direction=s['trade_mode'],
                entry_ts=c['entry_ts'],order_ts=o['created_ts'],fill_previous_ts=fill['source_previous_ts'],net_cny=c['net_cents']/100)
            for kind in ('future_only','peer_cancel'):
                times=cache[k][kind];i=bisect_left(times,o['created_ts']);warning=times[i] if i<len(times) and times[i]<c['entry_ts'] else None
                row[kind+'_warning_ts']=warning
                row[kind+'_category']='none' if warning is None else 'before_interval' if warning<=fill['source_previous_ts'] else 'inside_ambiguous_interval'
            rows.append(row)
    write(OUT/'baseline_warning_audit.json',rows)
    groups=defaultdict(list)
    for r in rows:groups[(r['mask'],r['policy'],r['direction'])].append(r)
    result={}
    for key,rr in groups.items():
        stats={}
        for kind in ('future_only','peer_cancel'):
            ahead=[r for r in rr if r[kind+'_category']=='before_interval']
            stats[kind]=dict(ahead_losses=sum(r['net_cny']<0 for r in ahead),ahead_winners=sum(r['net_cny']>0 for r in ahead),
                baseline_net_of_flagged_cycles=round(sum(r['net_cny'] for r in ahead),2),
                ambiguous=sum(r[kind+'_category']=='inside_ambiguous_interval' for r in rr))
        extra=[r for r in rr if r['peer_cancel_category']=='before_interval' and r['future_only_category']!='before_interval']
        stats['peer_extra']=dict(losses=sum(r['net_cny']<0 for r in extra),winners=sum(r['net_cny']>0 for r in extra),
            baseline_net=round(sum(r['net_cny'] for r in extra),2))
        result['_'.join(key)]=stats
    write(OUT/'warning_attribution.json',result)
    print(result)


if __name__=='__main__':main()
