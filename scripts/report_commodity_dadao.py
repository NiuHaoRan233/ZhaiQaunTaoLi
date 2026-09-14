"""Combine immutable day accounts into a transparent commodity discovery report."""
from collections import defaultdict
from pathlib import Path
import html,json,statistics as stats
from datetime import datetime

ROOT=Path(__file__).resolve().parents[1]
REPORT=ROOT/'广义套利/reports'
OUT=REPORT/'dadao_v1_result'


def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
        ['| '+' | '.join(str(v) for v in r)+' |' for r in rows])


def number(x,places=1):return '—' if x is None else f'{x:,.{places}f}'


def aggregate(rows):
    if not rows:return {}
    totals=['pnl_cny','realized_gross_cny','tail_gross_cny','fees_cny','complete_cycles',
        'winning_cycles','losing_cycles','filled_contract_sides','fill_count',
        'arrival_cross_fills','completed_cycle_net_cny','open_cycle_contribution_cny','holding_seconds']
    a={k:sum(r[k] for r in rows) for k in totals}
    a.update(days=len(rows),positive_days=sum(r['pnl_cny']>0 for r in rows),
        negative_days=sum(r['pnl_cny']<0 for r in rows),zero_trade_days=sum(r['fill_count']==0 for r in rows),
        tail_days=sum(r['end_inventory']>0 for r in rows),stale_tail_days=sum(r['stale_tail'] for r in rows),
        worst_day=min(r['pnl_cny'] for r in rows),best_day=max(r['pnl_cny'] for r in rows),
        max_day_drawdown=max(r['max_drawdown_cny'] for r in rows),
        median_daily_initial_cash=stats.median(r['initial_cash_cny'] for r in rows),
        longest_cycle_seconds=max((r['longest_cycle_seconds'] or 0 for r in rows),default=0),
        mean_filled_premium=sum(r['turnover_cny'] for r in rows)/a['filled_contract_sides'] if a['filled_contract_sides'] else None)
    return a


def main():
    OUT.mkdir(exist_ok=True)
    files=[REPORT/'dadao_v1_screen/matrix.json']+sorted(REPORT.glob('dadao_v1_validation_*/matrix.json'))
    accounts={};inputs={};errors={};prefixes=0
    for p in files:
        s=json.loads(p.read_text(encoding='utf-8'));prefixes+=s['prefix_checks']
        for key,r in s['accounts'].items():
            if key in accounts:assert accounts[key]==r
            accounts[key]=r
        inputs.update(s['inputs']);errors.update(s['errors'])
    plan=json.loads((REPORT/'dadao_v1_followup_selection.json').read_text(encoding='utf-8'))
    pending=[c+'_'+d for c in plan['codes'] for d in plan['dates']
             if c+'_'+d not in inputs and c+'_'+d not in errors]
    grouped=defaultdict(list)
    for r in accounts.values():grouped[(r['code'],r['mode'],r['fee_per_side_cny'])].append(r)
    summaries=[]
    for code in plan['codes']:
        core=grouped.get((code,'last_d500',5),[])
        if not core:continue
        item=dict(code=code,screen=aggregate([r for r in core if r['date']=='20260911']),
            validation=aggregate([r for r in core if r['date']!='20260911']),all=aggregate(core),comparisons={})
        d=next(v['detail'] for v in inputs.values() if v['code']==code)
        item['name']=d.get('ProductName') or d.get('InstrumentName') or code
        item['expiry']=d.get('ExpireDate')
        for mode,fee in [('coarse',5),('last_d0',5),('last_d500',0),('last_d500',2),
            ('last_d500',10),('last_d1000',5),('queue_d500',5),('unit_d500',5)]:
            item['comparisons'][f'{mode}_f{fee}']=aggregate([r for r in grouped[(code,mode,fee)] if r['date']!='20260911'])
        a=item['validation'];slow=item['comparisons']['last_d1000_f5']
        if not a:label='待追溯'
        elif a['days']<8 or a['complete_cycles']<10 or a['stale_tail_days']:label='数据或回合不足'
        elif a['pnl_cny']>=0 and slow['pnl_cny']>=0:label='盈利线索，继续复核'
        elif a['mean_filled_premium'] and min(a['pnl_cny'],slow['pnl_cny'])>=-.02*a['mean_filled_premium']:label='少亏/近持平线索'
        elif a['pnl_cny']>0:label='延迟敏感'
        else:label='本轮亏损明显'
        item['classification']=label;summaries.append(item)
    summaries.sort(key=lambda r:(r['classification']!='盈利线索，继续复核',r['classification']!='少亏/近持平线索',-(r['validation'].get('pnl_cny',-1e99))))
    broad=[r for r in accounts.values() if r['date']=='20260911' and r['mode']=='last_d500' and r['fee_per_side_cny']==5]
    state=dict(created_at=datetime.now().isoformat(),account_count=len(accounts),input_days=len(inputs),
        prefix_checks=prefixes,pending=pending,errors=errors,rows=summaries,
        broad=dict(contracts=len(broad),positive=sum(r['pnl_cny']>0 for r in broad),negative=sum(r['pnl_cny']<0 for r in broad),
        zero_pnl=sum(r['pnl_cny']==0 for r in broad),zero_trades=sum(r['fill_count']==0 for r in broad)))
    (OUT/'summary.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    text=f'''# 商品期权大道至简首轮试验 · 2026-09-12

先用同一极简循环扫343只商品期权，再冻结64只、36个品种，向前追溯2026-08-24至09-10的14个交易日。09-11是筛选日，以下主表剔除它。更早日期仍存在事后选券偏差，不是严格样本外。

09-11主口径单边5元假设：盈利{state['broad']['positive']}只、亏损{state['broad']['negative']}只、零盈亏{state['broad']['zero_pnl']}只，其中{state['broad']['zero_trades']}只完全没成交。零成交不算找到可用品种。

当前归档{len(accounts):,}个独立账户、{len(inputs):,}个合约日；{prefixes:,}次截断前缀一致。未处理{len(pending)}格，数据/执行错误或缺失{len(errors)}格，详见summary.json，不能把缺失日算零收益。

## 多日主表（剔除09-11）

每合约/日零底仓、最多1手，空仓买一、买成卖一、卖清再买；能改善时向内一跳。主口径最多使用末价上1手成交证据，500ms在快照边界处理，假设每手单边5元。费用已扣、尾仓按最后有效买一估值；没有假装日终真实清仓或隔夜连续资金。

“盈利线索”仅表示至少8日、10个闭环、无陈旧尾仓，500ms及1000ms均非负；“少亏”线索另以两档损失均不超过平均成交一手权利金的2%作透明观察尺度。这些不是验收标准、交易过滤或优化后必赚的证明。

'''
    headers=['合约','品种','日数','净盈亏元','1000ms元','原价排队元','闭环','盈利/亏损日','尾仓日','最大单日回撤元','判断']
    rows=[]
    for r in summaries:
        a=r['validation'];b=r['comparisons']
        if not a:continue
        rows.append([r['code'],r['name'],a['days'],number(a['pnl_cny']),number(b['last_d1000_f5'].get('pnl_cny')),
            number(b['queue_d500_f5'].get('pnl_cny')),a['complete_cycles'],f"{a['positive_days']}/{a['negative_days']}",
            a['tail_days'],number(a['max_day_drawdown']),r['classification']])
    text+=table(headers,rows)
    text+='\n\n## 费用、尾仓及资金尺度\n\n仍剔除09-11。0/2/5/10元均为每手单边情景，不是实际商品期权费率。每个费用情景都重新运行并逐笔扣现金，不能只按另一条路径事后减费。原价排队只按可识别末价1手减前队，未推定撤单让位，因此属于不同成交情景，零成交排队收益不构成稳健证明。\n\n'
    text+=table(['合约','零费元','单边2元净','单边5元净','单边10元净','已闭环净元','未完回合贡献元','典型日初现金元','成交一手均价元','最长闭环分钟'],[
        [r['code'],number(r['comparisons']['last_d500_f0'].get('pnl_cny')),number(r['comparisons']['last_d500_f2'].get('pnl_cny')),
         number(r['validation'].get('pnl_cny')),number(r['comparisons']['last_d500_f10'].get('pnl_cny')),
         number(r['validation'].get('completed_cycle_net_cny')),number(r['validation'].get('open_cycle_contribution_cny')),
         number(r['validation'].get('median_daily_initial_cash')),number(r['validation'].get('mean_filled_premium')),
         number(r['validation'].get('longest_cycle_seconds',0)/60)] for r in summaries if r['validation']])
    text+='\n\n## 限制与下一步\n\n商品期权成交笔数字段为0，不能把末价一手证据叫作交易所逐笔方向。方向由相隔不超过60秒的前有效盘口推断；限价单生效前区间不认领，延迟到达后若跨价按可见对手一档成交，另有arrival_cross标志。柜台时延、同价队列和冲击尚未实测；本轮无标的对冲、无到期行权或跨日资金管理。缺失能源中心及夜盘未被该研究补齐。\n\n这是先找到值得看盘口和进一步校准的品种。应先拆盈利来源、尾仓及少数极端回合，再固定规则检验未用于选择的新日期。不能用本轮结果宣称“少亏所以再优化必赚”。原债券模型、ETF算例与实时矩阵保持独立。\n'
    path=OUT/'商品期权大道至简试验.md';path.write_text(text,encoding='utf-8')
    parts=[];inside=False
    for line in text.splitlines():
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
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>商品期权大道至简试验</title><style>body{font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif;background:#f5f4ef;color:#24332f;margin:auto;padding:28px;max-width:1600px}h1{font-size:30px}h2{margin-top:36px;color:#126451}p{max-width:1100px}input{padding:10px;max-width:90%;width:360px;border:1px solid #aabbb0;border-radius:5px}.scroll{max-width:100%;overflow:auto;background:#fff;border:1px solid #d6dfd9}table{border-collapse:collapse;white-space:nowrap;font-size:14px}th,td{text-align:right;padding:9px 11px;border-bottom:1px solid #e5e8e3}th{background:#e0ebe3}td:first-child,td:nth-child(2),td:last-child{text-align:left}</style><body><input id="search" aria-label="搜索合约或品种" placeholder="输入合约或品种筛选"><p><a href="商品期权大道至简试验.md">完整报告</a> · <a href="summary.json">数据与缺失明细</a></p>'''+''.join(parts)+'''<script>document.getElementById('search').addEventListener('input',e=>{const q=e.target.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q));});</script></body></html>'''
    (OUT/'商品期权大道至简试验.html').write_text(page,encoding='utf-8')
    print(json.dumps(state['broad']),len(summaries),'candidates; pending',len(pending),'errors',len(errors))
    for r in summaries[:12]:print(r['code'],r['classification'],r['validation'].get('pnl_cny'),r['validation'].get('complete_cycles'))


if __name__=='__main__':main()
