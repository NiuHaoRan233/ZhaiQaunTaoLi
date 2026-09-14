"""Reproduce both frozen flow candidates through the new incremental runtime."""
from pathlib import Path
from collections import Counter
import gzip
import hashlib
import json
import pickle

from zhaiquant.commodity_flow_strategy import FAMILY, VARIANTS, FlowAccount
from zhaiquant.commodity_flow_cli import table, write_json
from zhaiquant.commodity_dadao_research import audit_result
from zhaiquant.commodity_selective_research import prepare

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'广义套利/reports/commodity_optimization_20260912'
OUT=ROOT/'广义套利/reports/commodity_strategy_20260913_r2'


def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def economic(items):return [{k:v for k,v in x.items() if k!='model_id'} for x in items]


def main():
    preserved=read(OUT/'predevelopment_manifest.json')
    for rel,expected in preserved.items():
        assert digest(ROOT/rel)==expected,rel
    frozen=read(OLD/'candidate_contract.json')
    for rel,expected in frozen['source_hashes'].items():assert digest(ROOT/rel)==expected,rel
    sources={}
    for name in ['commodity_flow_strategy.py','commodity_flow_paper.py','commodity_flow_cli.py']:
        p=ROOT/'src/zhaiquant'/name;sources[str(p.relative_to(ROOT))]=digest(p)
    contract=dict(family=FAMILY,source_hashes=sources,baseline_contract_sha256=digest(OLD/'candidate_contract.json'))
    target=OUT/'runtime_contract.json'
    if target.exists():assert read(target)==contract,'Runtime changed after validation freeze'
    else:write_json(target,contract)
    rows=[];counts=Counter();(OUT/'accounts').mkdir(exist_ok=True)
    for n,code in enumerate(frozen['code_list']):
        cache=pickle.loads((OLD/'event_cache'/f'{code}.pkl').read_bytes())
        for date,expected in cache['tag']['inputs']:
            assert digest(ROOT/'广义套利/data'/f'{code}_{date}_tick.pkl')==expected
            counts['input_days']+=1
        events,dates,meta=prepare(cache['inputs'])
        for variant in VARIANTS:
            candidates=list(OLD.glob(f'part_*/accounts/{code}_{variant}.json.gz'))
            assert len(candidates)==1
            saved=json.loads(gzip.decompress(candidates[0].read_bytes()))
            account=FlowAccount(code,variant,meta['initial_cents'],meta['tick_cents'])
            result={k:[] for k in ['orders','fills','cycles','curve']}
            for i,e in enumerate(events):
                if i in (len(events)//2,len(events)-1):
                    snapshot=json.loads(json.dumps(account.snapshot()))
                    restored=FlowAccount(code,variant,meta['initial_cents'],meta['tick_cents'],state=snapshot)
                    assert restored.snapshot()==account.snapshot()
                    expected_step=account.step(e,dates[e.ts]);actual_step=restored.step(e,dates[e.ts])
                    assert actual_step==expected_step and restored.snapshot()==account.snapshot()
                    account=restored;delta=actual_step;counts['restart_checks']+=1
                else:
                    delta=account.step(e,dates[e.ts])
                for k in ['orders','fills','cycles']:result[k].extend(delta[k])
                result['curve'].append(list(delta['curve']))
                assert result['curve'][-1]==saved['curve'][i],(code,variant,i)
            for key in ['orders','fills']:assert economic(result[key])==economic(saved[key]),(code,variant,key)
            assert result['cycles']==saved['cycles'],(code,variant,'cycles')
            assert account.daily()==saved['daily'],(code,variant,'daily')
            result.update(summary=account.summary(),daily=account.daily(),final_state=account.snapshot())
            for key in ['pnl_cny','fees_cny','end_cash_cny','end_inventory','max_drawdown_cny','holding_seconds']:
                assert result['summary'][key]==saved['summary'][key],(code,variant,key)
            audit_result(result)
            assert abs(sum(x['pnl_cny'] for x in result['daily'])-result['summary']['pnl_cny'])<1e-7
            with gzip.open(OUT/'accounts'/f'{code}_{variant}.json.gz','wt',encoding='utf-8',compresslevel=1) as f:
                json.dump(result,f,ensure_ascii=False,separators=(',',':'))
            rows.append(dict(summary=result['summary'],daily=result['daily']))
            counts.update(accounts=1,fills=len(result['fills']),curve_frames=len(result['curve']),account_days=len(result['daily']))
        print(f'{n+1}/64 {code} exact',flush=True)
    for rel,expected in preserved.items():assert digest(ROOT/rel)==expected,rel
    assert sources=={rel:digest(ROOT/rel) for rel in sources}
    totals={v:round(sum(x['summary']['pnl_cny'] for x in rows if x['summary']['mode']==v),2) for v in VARIANTS}
    assert totals==dict(flow_patient=21839.7,flow_entry=14803.4)
    verification=dict(status='passed',counts=dict(counts),preserved_files=len(preserved),totals=totals,
                      sources=sources,note='Historical reproduction only; not new out-of-sample or paper-arrival profit')
    write_json(OUT/'verification.json',verification)
    write_json(OUT/'replay_summary.json',dict(verification=verification,accounts=rows))
    dates=sorted({d['date'] for a in rows for d in a['daily']})
    content=['<h1>商品期权双侧成交策略0.1 · 完整历史复现</h1>',
             '<p>新增运行层精确复现旧两候选。没有调交易阈值，没有按赢家名单改变合约。以下是已见历史样本，不是新增前向成绩。</p>',
             '<p>64合约，每模型独立资金和1,698,000元；单边1.7元、历史0延迟、最多1手，连续现金库存。无夜盘，L1末价证据最多1手；49缺失格保留。</p>',
             '<p><a href="replay_summary.json">全部机器可读日度</a> · <a href="verification.json">经济等价与恢复核验</a></p>']
    content.append(table(['模型','历史净收益/元'],[[v,f'{p:,.2f}'] for v,p in totals.items()]))
    for v in VARIANTS:
        content.append(f'<h2>{v} · 全部64合约日度</h2>')
        data=[]
        for a in rows:
            s=a['summary']
            if s['mode']!=v:continue
            ds={d['date']:d['pnl_cny'] for d in a['daily']}
            data.append([s['code'],*[f'{ds[d]:,.2f}' if d in ds else '缺失' for d in dates],f"{s['pnl_cny']:,.2f}",s['complete_cycles'],s['end_inventory']])
        content.append(table(['合约',*dates,'合计/元','完整回合','尾仓/手'],data))
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>商品期权完整策略0.1历史复现</title><style>body{font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif;background:#f4f5ef;color:#20382e;padding:24px}h1,h2{color:#16664f}.scroll{overflow:auto;max-height:700px;border:1px solid #c9d8ce}table{border-collapse:collapse;background:white;white-space:nowrap}td,th{padding:9px 12px;text-align:right;border-bottom:1px solid #dee6df}th{position:sticky;top:0;background:#dce8df}td:first-child,th:first-child{position:sticky;left:0;text-align:left;background:#e9f1eb;z-index:1}th:first-child{z-index:2}</style><body>'+''.join(content)+'</body></html>'
    (OUT/'完整策略历史日度.html').write_text(page,encoding='utf-8')
    files=[OUT/'verification.json',OUT/'replay_summary.json',OUT/'完整策略历史日度.html',*sorted((OUT/'accounts').glob('*.json.gz'))]
    write_json(OUT/'result_manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in files})
    print(json.dumps(verification,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
