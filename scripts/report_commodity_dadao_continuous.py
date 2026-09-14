"""Audit and report saved continuous accounts; never alter frozen execution."""
from pathlib import Path
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
import gzip, hashlib, html, json
import pandas as pd
from zhaiquant import commodity_dadao_research as day
from zhaiquant import commodity_dadao_continuous as continuous
from report_commodity_dadao import table, number

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'广义套利'; REPORT=BASE/'reports'; OUT=REPORT/'dadao_cont_v1_result'
FOCUS=['PK612P8400.ZF','a2701-P-5500.DF','jm2701-P-1460.DF','SR703C5400.ZF',
       'CF703P17400.ZF','ag2612P16000.SF','cu2611P114000.SF','AP701P7200.ZF']
def read(p): return json.loads(p.read_text(encoding='utf-8'))
def unpack(p): return json.loads(gzip.decompress(p.read_bytes()))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def stamp(ts): return datetime.fromtimestamp(ts/1000,ZoneInfo('Asia/Shanghai')).isoformat()
def write(p,s): p.write_text(json.dumps(s,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

def main():
    OUT.mkdir(exist_ok=True)
    daily=read(REPORT/'dadao_v1_result/summary.json')
    inputs={}; day_paths={}
    for p in [REPORT/'dadao_v1_screen/matrix.json',*sorted(REPORT.glob('dadao_v1_validation_*/matrix.json'))]:
        s=read(p);inputs.update(s['inputs'])
        for key in s['accounts']: day_paths[key]=p.parent/'accounts'/f'{key}.json.gz'
    rows={}; full={}; prefixes=0; manifests={}; raw_verified=set()
    for p in sorted(REPORT.glob('dadao_cont_v1_*/matrix.json')):
        s=read(p); assert not s['errors'];prefixes+=s['prefix_checks'];manifests[str(p.relative_to(ROOT))]=sha(p)
        assert s['engine_sha256']==sha(Path(continuous.__file__))
        assert s['day_engine_sha256']==sha(Path(day.__file__))
        for code,items in s['inputs'].items():
            for m in items:
                raw=BASE/'data'/f"{code}_{m['date']}_tick.pkl"
                assert sha(raw)==m['sha256'];raw_verified.add(str(raw))
        for key,a in s['accounts'].items():
            path=p.parent/'accounts'/f'{key}.json.gz';r=unpack(path)
            assert r['summary']==a; day.audit_result(r)
            assert abs(sum(d['pnl_cny'] for d in r['daily'])-a['pnl_cny'])<1e-8
            manifests[str(path.relative_to(ROOT))]=sha(path);rows[key]=a
            if a['mode']=='last_d500' and a['fee_per_side_cny']==5:full[a['code']]=r
    assert len(rows)==384 and len(full)==64
    assert prefixes==sum(2 for r in full.values() if r['summary']['days']>2)
    intra=read(REPORT/'intraday_screen.json')['rows']
    quote={r['code']:r['metrics'] for r in intra.values() if r['date']=='20260911'}
    summaries=[]
    for code,r in full.items():
        a=dict(r['summary']);a['name']=inputs[f"{code}_{a['first_date']}"]['detail'].get('ProductName',code)
        a['before_selection_pnl_cny']=sum(d['pnl_cny'] for d in r['daily'] if d['date']<'20260911')
        a['selection_day_increment_cny']=sum(d['pnl_cny'] for d in r['daily'] if d['date']=='20260911')
        a['comparisons']={f'{m}_f{f}':rows[f'{code}_{m}_f{f}'] for m,f in
                         [('last_d500',0),('last_d500',200),('last_d500',1000),('last_d1000',500),('queue_d500',500)]}
        cycles=r['cycles'];fs={f['ts']:f for f in r['fills']}
        best=sorted(cycles,key=lambda c:-c['net_cents'])
        a['best_cycle_net_cny']=best[0]['net_cents']/100 if best else 0
        a['best_three_net_cny']=sum(c['net_cents'] for c in best[:3])/100
        a['pnl_without_best_three_cny']=a['pnl_cny']-a['best_three_net_cny']
        overnight=[c for c in cycles if stamp(c['entry_ts'])[:10]!=stamp(c['exit_ts'])[:10]]
        crossed=[c for c in cycles if any(fs[t]['kind']=='arrival_cross' for t in [c['entry_ts'],c['exit_ts']])]
        a['overnight_cycles']=len(overnight);a['overnight_cycle_net_cny']=sum(c['net_cents'] for c in overnight)/100
        a['crossed_cycles']=len(crossed);a['crossed_cycle_net_cny']=sum(c['net_cents'] for c in crossed)/100
        a['quote_20260911']=quote.get(code)
        a['daily_account_comparison']=next(x['all'] for x in daily['rows'] if x['code']==code)
        summaries.append(a)
    summaries.sort(key=lambda a:-a['pnl_cny'])
    bycode={a['code']:a for a in summaries}
    # Rebuild representative raw windows independently and compare one-day accounts.
    windows=[];equivalences=[]
    for code in FOCUS:
        r=full[code];a=r['summary'];date=a['first_date'];meta=inputs[f'{code}_{date}']
        es,flags,m=day.load_frame(pd.read_pickle(BASE/'data'/f'{code}_{date}_tick.pkl'),code=code,date=date,detail=meta['detail'])
        one=continuous.run([(es,flags,m)],code=code,mode='last_d500',fee_cents=500)
        saved=unpack(day_paths[f'{code}_{date}_last_d500_f500'])
        for field in ['pnl_cny','end_cash_cny','end_inventory','max_drawdown_cny','complete_cycles','fees_cny']:
            assert one['summary'][field]==saved['summary'][field]
        def economic_fills(r):return [{k:v for k,v in f.items() if k not in ['model_id','date']} for f in r['fills']]
        assert economic_fills(one)==economic_fills(saved)
        equivalences.append(dict(code=code,date=date,fills=len(one['fills'])))
        chosen=[max(r['cycles'],key=lambda c:c['net_cents']),min(r['cycles'],key=lambda c:c['net_cents'])]
        stamps={c[t] for c in chosen for t in ['entry_ts','exit_ts']}
        cross=next((f for f in r['fills'] if f['kind']=='arrival_cross'),None)
        if cross:stamps.add(cross['ts'])
        frames={}
        for f in [f for f in r['fills'] if f['ts'] in stamps]:
            date=f['date'];meta=inputs[f'{code}_{date}'];unit=meta['unit']
            if date not in frames:
                frames[date]=pd.read_pickle(BASE/'data'/f'{code}_{date}_tick.pkl').sort_values('time',kind='stable').drop_duplicates('time',keep='last').reset_index(drop=True)
            fr=frames[date];idx=int(fr.index[fr.time==f['ts']][0]);cur=fr.iloc[idx];prev=fr.iloc[idx-1]
            pc=lambda p:day.price_cents(p,unit)
            if f['kind']=='passive':
                assert int(prev.time)==f['source_previous_ts'] and 0<cur.time-prev.time<=60000
                assert cur.volume>prev.volume and cur.amount>prev.amount
                assert pc(cur.lastPrice)==f['source_last_cents']
                assert 0<prev.bidPrice[0]<prev.askPrice[0] and min(prev.bidVol[0],prev.askVol[0])>0
                if f['side']=='buy':assert cur.lastPrice<=prev.bidPrice[0] and pc(cur.lastPrice)<=f['price_cents']
                else:assert cur.lastPrice>=prev.askPrice[0] and pc(cur.lastPrice)>=f['price_cents']
            else:
                assert f['price_cents']==pc(cur.askPrice[0] if f['side']=='buy' else cur.bidPrice[0])
                assert (cur.askVol[0] if f['side']=='buy' else cur.bidVol[0])>=f['quantity']
            def snap(x):return dict(time=stamp(int(x.time)),bid=float(x.bidPrice[0]),ask=float(x.askPrice[0]),
                bid_quantity=int(x.bidVol[0]),ask_quantity=int(x.askVol[0]),last=float(x.lastPrice),volume=int(x.volume),amount=float(x.amount))
            windows.append(dict(code=code,fill=f,unit=unit,window=[snap(fr.iloc[j]) for j in range(max(0,idx-2),min(len(fr),idx+2))]))
        print('audited',code,flush=True)
    state=dict(accounts=len(rows),contracts=len(full),verified_raw_input_days=len(raw_verified),prefix_checks=prefixes,
        positive=sum(a['pnl_cny']>0 for a in summaries),negative=sum(a['pnl_cny']<0 for a in summaries),
        one_day_equivalences=equivalences,raw_fill_windows=len(windows),rows=summaries)
    write(OUT/'summary.json',state);write(OUT/'raw_fill_audit.json',windows)
    md='''# 商品期权大道至简：连续持仓探索结果 · 2026-09-12

找到几组值得继续看盘口的线索，尚未找到像双债一样低波动、对成交假设也稳定的成熟做市对象。优先观察花生PK612P8400、黄大豆1号a2701-P-5500；焦煤jm2701-P-1460与白糖SR703C5400作近持平对照。棉花CF703P17400保留，但延迟和薄买盘风险明显。白银ag2612P16000利润较多，需先查少数跨日回合；铜cu2611P114000对延迟不稳。这里的研究优先级包含事后观察，不能当作已冻结的自动选券规则。

QMT四所目录64个品种、23,252个合约中，343只已有09-11盘口做第一轮回放；冻结64只、36个品种后追溯08-24至09-10，再连同09-11形成最多15个交易日。能源中心未取到、夜盘未模拟，历史回溯有选券偏差，不能称全市场全时段或严格样本外。

固定一只合约、零底仓、最多1手期权：空仓挂买一，买成挂卖一，卖清再买；空间允许时两侧向内改善一跳，不设盈利才卖的条件。主表为末价最多1手成交证据、500ms快照延迟、假设每手单边5元。首个有效卖一决定初始现金，只注资一次，现金与持仓跨日连续；日盘结束撤单，夜间无模拟交易或对冲。尾仓按最后有效买一估值，收盘薄买盘造成的回撤照计。每只账户独立，不是共享组合。

## 优先观察与执行敏感性

净盈亏均为人民币元、已扣假设费用；所有费用与执行情景重新运行。原价排队同时改变报价位置和队列假设，不是仅增加固定滑点，也不是可信上下界。它只按识别到的末价1手扣前队，未给撤单让位信用。

'''
    headers=['品种 / 合约','日数','5元净','闭环','1000ms净','排队净 / 闭环','最大买一盯市回撤']
    def mainrow(a):
        b=a['comparisons'];q=b['queue_d500_f500']
        return [a['name']+' '+a['code'],a['days'],number(a['pnl_cny']),a['complete_cycles'],number(b['last_d1000_f500']['pnl_cny']),f"{number(q['pnl_cny'])} / {q['complete_cycles']}",number(a['max_drawdown_cny'])]
    md+=table(headers,[mainrow(bycode[c]) for c in FOCUS])
    md+='\n\n进一步拆解后，所有八只重点对象去掉各自最赚钱的三个回合都转为净亏。这是利润集中风险，不代表这些回合不真实。花生截至09-10仍赚382.5元，但含到达跨价的回合贡献887.5元，超过总净利580元；黄大豆与焦煤在09-10分别亏175元和577元，正值依赖筛选日的恢复。白银8,345元中，跨日回合净8,082.5元，09-11当日增量8,060元；铜09-11增量12,245元，且延迟从500ms变为1000ms后总净值由+8,580变−720元。故白银/铜不能当作平稳赚点差的强证据，花生/黄大豆也只列观察线索。\n'
    md+='\n\n## 资金、费用与利润来源\n\n“去前三回合”是事后集中度诊断，不是剔除它们重新回放。跨日闭环净值包含价差与方向变化，不能全部叫价差收入。9月11日列为同一连续账户当日净值增量，包含前日持仓的变化，未另行重置。\n\n'
    md+=table(['合约','初始现金','2元净','10元净','截至09-10净','09-11增量','去前三回合净','跨日回合净 / 数','跨价回合净 / 数','最长闭环小时','尾仓手'],[
        [c,number((a:=bycode[c])['initial_cash_cny']),number(a['comparisons']['last_d500_f200']['pnl_cny']),number(a['comparisons']['last_d500_f1000']['pnl_cny']),
         number(a['before_selection_pnl_cny']),number(a['selection_day_increment_cny']),number(a['pnl_without_best_three_cny']),
         f"{number(a['overnight_cycle_net_cny'])} / {a['overnight_cycles']}",f"{number(a['crossed_cycle_net_cny'])} / {a['crossed_cycles']}",number(a['longest_cycle_seconds']/3600),a['end_inventory']] for c in FOCUS])
    md+='\n\n## 对应盘口：09-11日盘快照观察\n\n相对价差以中点为分母；时间加权有效报价最多延续60秒。成交额是日盘增量，不是全交易日名义本金。中点波幅为时间加权95/5分位差比率，可能包含撤单对中点的影响，不能当纯标的波动率。\n\n'
    md+=table(['合约','有效覆盖%','平均价差%','≥0.5%分钟','平均每手价差元','日盘权利金成交额万元','中点95/5波幅%','60秒漂移90分位%'],[
        [c,*[number((q:=bycode[c]['quote_20260911'])[k]) for k in ['valid_coverage_pct','mean_relative_spread_pct','spread_ge_05_minutes','mean_spread_cash_per_lot']],
         number(q['incremental_amount']/10000),number(q['mid_p95_p05_pct']),number(q['drift60_p90_pct'])] for c in FOCUS])
    md+='\n\n## 为什么同时保留独立日账户\n\n原日账户先归档10,710个账户、1,190个输入日。剔除筛选日后，64只均未出现正的日账户累计结果（其中PF仅有筛选日数据）。连续账户另登记cont_v1，改变的是资金/持仓跨日合同，没有修改原价差循环来追求利润。独立日尾仓盯市之和与连续现金账户不是同一经济对象，不能直接称策略优化收益。\n\n棉花CF703P17400在09-09的14:26买入一手成本5,265元，尾盘买一377×5=1,885元、卖一1110×5，末价1052×5。−3,385元尾仓差额来自真实宽盘口；等待后日有成交才能恢复，不能用末价或中间价抹掉，也不能忽略夜间持有风险。\n\n'
    md+=table(['合约','独立日累计净（含09-11）','连续净','日账户尾仓日','连续最大回撤'],[
        [c,number((a:=bycode[c])['daily_account_comparison']['pnl_cny']),number(a['pnl_cny']),a['daily_account_comparison']['tail_days'],number(a['max_drawdown_cny'])] for c in FOCUS])
    md+=f'\n\n## 完整64只结果\n\n连续账户{state["accounts"]}个，主档{state["positive"]}盈、{state["negative"]}亏。PF612C8900只有1天，不能作为多日少亏线索；bu等缺失日期见summary.json。金/铂少量盈利伴随稀少闭环与很大回撤，未列为低波动优先对象。\n\n'
    md+=table(headers,[mainrow(a) for a in summaries])
    md+=f'''\n\n## 核验与限制

384个账户全部重新核算现金、库存、费用、闭环与尾仓，{len(raw_verified)}份原始输入日重新核对哈希；连续前缀{prefixes}次通过（63只多于2日各2次，PF只有1日不适用）。8只代表合约的首日连续核与归档日账户逐笔经济字段一致，另从原始行情检查{len(windows)}个成交窗口，包含每只最好/最差回合及存在的跨价到达。具体前后盘口、原始量额及乘数保存于raw_fill_audit.json。主工程704项测试通过，日志dadao_tests_final.log。独立日验证缺49格，不能计零；原日报告与失败记录保留。

L1末价量增只能支持有限的成交推断，不能证明真实交易所逐笔方向、队列或我方必成交；延迟后跨价按当时可见对手一档成交，另行标记，没有把它算成纯被动。费用0/2/5/10元是假设，需查实际合约与券商收费。未建模夜盘、到期行权、冲击及实际柜台时延，跨日持有不能视作低风险。回撤仅覆盖已存日盘报价，可能低估夜间风险。

下一步应固定观察名单，采集新的日期与夜盘，核对实际收费和订单到达/撤单/队列；先解释利润是否来自可重复的双侧成交，再考虑新模型的合理价、单边风险和退出规则。少亏只是值得研究，不证明调整后必赚。本轮没有改动债券十模型矩阵。
'''
    (OUT/'商品期权连续持仓探索.md').write_text(md,encoding='utf-8')
    parts=[];inside=False
    for line in md.splitlines():
        if line.startswith('|'):
            if line.startswith('| ---'):continue
            cells=[html.escape(c.strip()) for c in line.strip('|').split('|')]
            if not inside:parts.append('<div class="scroll"><table><thead><tr>'+''.join('<th>'+c+'</th>' for c in cells)+'</tr></thead><tbody>');inside=True
            else:parts.append('<tr>'+''.join('<td>'+c+'</td>' for c in cells)+'</tr>')
        else:
            if inside:parts.append('</tbody></table></div>');inside=False
            if line.startswith('# '):parts.append('<h1>'+html.escape(line[2:])+'</h1>')
            elif line.startswith('## '):parts.append('<h2>'+html.escape(line[3:])+'</h2>')
            elif line:parts.append('<p>'+html.escape(line)+'</p>')
    if inside:parts.append('</tbody></table></div>')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>商品期权连续持仓探索</title><style>body{font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif;background:#f5f4ef;color:#24332f;margin:auto;padding:28px;max-width:1500px}h1{font-size:30px}h2{margin-top:36px;color:#126451}p{max-width:1150px}.scroll{max-width:100%;overflow:auto;background:white;border:1px solid #d6dfd9}table{border-collapse:collapse;white-space:nowrap;font-size:14px}th,td{text-align:right;padding:9px 11px;border-bottom:1px solid #e5e8e3}th{background:#e0ebe3}td:first-child,th:first-child{text-align:left}input{font:inherit;padding:8px;width:320px;max-width:90%}</style><body><input id="search" aria-label="搜索合约或品种" placeholder="搜索合约或品种"><p><a href="商品期权连续持仓探索.md">完整文字报告</a> · <a href="summary.json">账户与敏感性数据</a> · <a href="raw_fill_audit.json">原始盘口核验</a></p>'''+''.join(parts)+'''<script>document.querySelector('#search').addEventListener('input',e=>{const q=e.target.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q))})</script></body></html>'''
    (OUT/'商品期权连续持仓探索.html').write_text(page,encoding='utf-8')
    for p in [Path(__file__),Path(day.__file__),Path(continuous.__file__),ROOT/'src/zhaiquant/option_top_cycle_research.py',*OUT.glob('*.json'),*OUT.glob('*.md'),*OUT.glob('*.html')]:
        if p.name!='manifest.json':manifests[str(p.relative_to(ROOT))]=sha(p)
    write(OUT/'manifest.json',manifests)
    print('DONE',state['positive'],state['negative'],'prefixes',prefixes,'windows',len(windows),flush=True)
    for c in FOCUS:
        a=bycode[c];print(c,'pre',a['before_selection_pnl_cny'],'minus_top3',a['pnl_without_best_three_cny'],'overnight',a['overnight_cycle_net_cny'],'cross',a['crossed_cycle_net_cny'])

if __name__=='__main__':main()
