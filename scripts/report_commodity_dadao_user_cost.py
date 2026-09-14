"""Daily/total PnL report for the user-specified commodity scenario."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import gzip,hashlib,html,json
from zhaiquant import commodity_dadao_research as engine
from report_commodity_dadao import table

ROOT=Path(__file__).resolve().parents[1];REPORT=ROOT/'广义套利/reports';OUT=REPORT/'dadao_user_f170_d0_result'
FOCUS=['ag2612P16000.SF','cu2611P114000.SF','PK612P8400.ZF','a2701-P-5500.DF','jm2701-P-1460.DF','SR703C5400.ZF','CF703P17400.ZF']
NAMES=dict(zip(FOCUS,['白银','铜','花生','黄大豆1号','焦煤','白糖','棉花']))
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def write(p,obj):p.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
def n(v):return '缺失' if v is None else f'{v:,.2f}'
def stamp(t):return datetime.fromtimestamp(t/1000,ZoneInfo('Asia/Shanghai')).strftime('%m-%d %H:%M:%S')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    OUT.mkdir(exist_ok=True);accounts={};prefix=0;manifest={};inputs={}
    for p in sorted(REPORT.glob('dadao_user_f170_d0_*/matrix.json')):
        s=read(p);assert not s['errors'];prefix+=s['prefix_checks'];inputs.update(s['inputs'])
        manifest[str(p.relative_to(ROOT))]=sha(p)
        for code,a in s['accounts'].items():
            f=p.parent/'accounts'/f'{code}.json.gz';r=json.loads(gzip.decompress(f.read_bytes()))
            assert r['summary']==a;engine.audit_result(r)
            assert abs(sum(d['pnl_cny'] for d in r['daily'])-a['pnl_cny'])<1e-8
            assert a['fee_per_side_cny']==1.7 and a['mode']=='last_d0'
            accounts[code]=r;manifest[str(f.relative_to(ROOT))]=sha(f)
    assert len(accounts)==64 and prefix==126
    plan=read(REPORT/'dadao_v1_followup_selection.json');dates=sorted(plan['dates']+['20260911'])
    old={a['code']:a for a in read(REPORT/'dadao_cont_v1_result/summary.json')['rows']}
    dm={c:{d['date']:d for d in r['daily']} for c,r in accounts.items()}
    totals=sorted([r['summary'] for r in accounts.values()],key=lambda a:-a['pnl_cny'])
    aggregate=[]
    for d in dates:
        known=[x[d]['pnl_cny'] for x in dm.values() if d in x]
        aggregate.append(dict(date=d,pnl_cny=sum(known),observed_accounts=len(known),missing_accounts=64-len(known)))
    total=sum(a['pnl_cny'] for a in totals);focus_total=sum(accounts[c]['summary']['pnl_cny'] for c in FOCUS)
    assert abs(sum(d['pnl_cny'] for d in aggregate)-total)<1e-8
    state=dict(model_id=totals[0]['model_id'],fee_per_side_cny=1.7,delay_ms=0,account_count=64,
        prefix_checks=prefix,dates=dates,positive=sum(a['pnl_cny']>0 for a in totals),negative=sum(a['pnl_cny']<0 for a in totals),
        total_independent_account_pnl_cny=total,total_initial_cash_cny=sum(a['initial_cash_cny'] for a in totals),
        seven_focus_sum_cny=focus_total,two_metal_sum_cny=sum(accounts[c]['summary']['pnl_cny'] for c in FOCUS[:2]),
        daily_aggregate=aggregate,rows=totals,daily_by_code=dm)
    write(OUT/'summary.json',state)
    md='''# 商品期权每日收益：单边1.7元、下单延迟0

按用户2026-09-12指定口径，从原始行情重新生成订单和成交。冻结64只名单保留，白银ag2612P16000与铜cu2611P114000继续作为重点研究对象；原八千多盈利的5元/500ms结果完整保留。参数变化可能改变成交与持仓路径，所以没有直接在旧收益上减手续费。

时间为2026-08-24至09-11，最多15交易日；每合约独立首日资金、零底仓、最多1手期权，现金和持仓跨日连续。买一买入→卖一卖出→再买，空间允许时改善一跳。0延迟指当帧决策后立即生效，不认领该帧此前成交；沿用末价最多1手证据。收益单位均为人民币元，已扣每手单边1.7元，每个完整回合费用3.4元。

每日收益是当日末账户净值减上一可观察日末净值，包含持仓买一盯市变化；首日减初始现金。不是仅当天卖出实现利润。只模拟日盘，保留隔夜持仓；缺失日不填0。9月11日曾用于选券，仍单列，不据此删除盈利回合。

## 七只重点：总收益

'''
    md+=table(['品种/合约','新口径总净收益','旧5元/500ms','初始资金','完整回合','累计手续费','末库存手','最大买一盯市回撤'],[
        [NAMES[c]+' '+c,n((a:=accounts[c]['summary'])['pnl_cny']),n(old[c]['pnl_cny']),n(a['initial_cash_cny']),a['complete_cycles'],n(a['fees_cny']),a['end_inventory'],n(a['max_drawdown_cny'])] for c in FOCUS])
    md+=f'\n\n白银与铜两独立账户合计净收益 **{n(state["two_metal_sum_cny"])}元**；七只重点独立账户合计 **{n(focus_total)}元**。这些是各自资金账户的算术和，不是共享一个资金槽的回放。\n\n## 七只重点：每日净收益\n\n'
    md+=table(['日期',*[NAMES[c] for c in FOCUS]],[[d,*[n(dm[c].get(d,{}).get('pnl_cny')) for c in FOCUS]] for d in dates]+
              [['合计',*[n(accounts[c]['summary']['pnl_cny']) for c in FOCUS]]])
    md+='\n\n## 白银与铜：每天收益及累计\n\n'
    md+=table(['日期','白银当日','白银累计','铜当日','铜累计','两者当日合计'],[
        [d,n(dm[FOCUS[0]][d]['pnl_cny']),n(dm[FOCUS[0]][d]['cumulative_pnl_cny']),n(dm[FOCUS[1]][d]['pnl_cny']),n(dm[FOCUS[1]][d]['cumulative_pnl_cny']),n(sum(dm[c][d]['pnl_cny'] for c in FOCUS[:2]))] for d in dates])
    md+='''\n\n## 铜为何从旧口径盈利转为亏损：一段关键路径

旧500ms/5元模型在09-10的14:54:51以一手21,920元买入，持到09-11的09:08:51以33,120元卖出，单个闭环扣费赚11,190元。新0延迟/1.7元在同一14:54:51以21,930元买入，卖单立即生效；14:54:51.5已有后续成交证据，新卖单按21,910元成交，该回合扣费亏23.40元，因而没有把这一手留到次日。旧延迟卖单在51.5秒才生效，不能认领生效前那半秒区间，随后继续持仓。原始QMT盘口和新旧订单/成交保存于copper_path_comparison.json。

所以此次变化主要涉及交易时点和持仓路径，不能解释成手续费降低反而让同一批交易亏损。两套记录均保留，尤其保留旧11,190元回合供用户研究；本次没有为保住它增加盈利才卖、隔夜持有或其他新规则。白银仍有09-10买入17,452.50元、09-11卖出23,490元的回合，扣费赚6,034.10元，详见下表。
'''
    for c in FOCUS[:2]:
        r=accounts[c];cycles=sorted(r['cycles'],key=lambda x:-x['net_cents']);rows=[]
        for rank,x in enumerate(cycles,1):
            rows.append([rank,stamp(x['entry_ts']),stamp(x['exit_ts']),n(x['entry_price_cents']/100),n((x['entry_price_cents']+x['gross_cents'])/100),n(x['net_cents']/100),n(x['duration_seconds']/3600)])
        md+=f'\n\n## {NAMES[c]} {c} 全部闭环：按净收益排序\n\n买卖金额按一手完整权利金列示；持有小时包含休市。未平仓另列在账户总表，不伪装成闭环。\n\n'
        md+=table(['排名','买入时间','卖出时间','买入金额元','卖出金额元','扣费净元','持有小时'],rows)
        write(OUT/f'{c}_review.json',dict(summary=r['summary'],daily=r['daily'],cycles=r['cycles'],fills=r['fills']))
    md+=f'\n\n## 全部64只总表\n\n主档{state["positive"]}盈、{state["negative"]}亏；全部独立账户净收益合计{n(total)}元，初始资金合计{n(state["total_initial_cash_cny"])}元。部分合约缺日，以下是已取得数据的算术汇总，不能冒充完整覆盖的共享组合。\n\n'
    md+=table(['合约','可取日','总净收益','完整回合','初始资金','手续费','末库存手'],[
        [a['code'],a['days'],n(a['pnl_cny']),a['complete_cycles'],n(a['initial_cash_cny']),n(a['fees_cny']),a['end_inventory']] for a in totals])
    md+='\n\n## 全部独立账户逐日合计\n\n'
    md+=table(['日期','已观察账户收益和','有数据账户','缺失账户'],[[d['date'],n(d['pnl_cny']),d['observed_accounts'],d['missing_accounts']] for d in aggregate])
    md+='\n\n## 各合约完整每日明细\n\n'
    for a in totals:
        c=a['code'];md+=f'\n\n### {c}\n\n'
        md+=table(['日期','当日净收益','累计净收益','当日成交次数','末库存手'],[
            [d,n((x:=dm[c].get(d,{})).get('pnl_cny')),n(x.get('cumulative_pnl_cny')),x.get('fill_count','缺失'),x.get('end_inventory','缺失')] for d in dates])
    md+='\n\n## 核验与保存\n\n64个账户现金/库存/费用独立重算通过，126次跨日前缀一致；所有委托到期时间等于建立时间，每笔按170分扣费，新参数独立ID及旧源码哈希均验证。原始911输入日哈希运行时检查，原结果不覆盖。完整订单、成交、曲线在dadao_user_f170_d0_0/1的accounts中，白银与铜的全部成交及闭环另存本目录review.json。QMT的L1推断成交仍是研究假设，日盘以外未模拟；当前1.7元和0延迟来自用户指定，不再沿用5元主假设。\n'
    (OUT/'每日与累计收益.md').write_text(md,encoding='utf-8')
    pieces=[];inside=False
    for line in md.splitlines():
        if line.startswith('|'):
            if line.startswith('| ---'):continue
            cells=[html.escape(x.strip()) for x in line.strip('|').split('|')]
            if not inside:pieces.append('<div class="scroll"><table><thead><tr>'+''.join('<th>'+x+'</th>' for x in cells)+'</tr></thead><tbody>');inside=True
            else:pieces.append('<tr>'+''.join('<td>'+x+'</td>' for x in cells)+'</tr>')
        else:
            if inside:pieces.append('</tbody></table></div>');inside=False
            if line.startswith('#'):
                level=len(line)-len(line.lstrip('#'));pieces.append(f'<h{level}>'+html.escape(line[level:].strip())+f'</h{level}>')
            elif line:pieces.append('<p>'+html.escape(line).replace('**','')+'</p>')
    if inside:pieces.append('</tbody></table></div>')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>商品期权每日收益 · 1.7元 / 0延迟</title><style>body{font:16px/1.65 system-ui,"Microsoft YaHei",sans-serif;max-width:1450px;margin:auto;padding:28px;background:#f6f5f0;color:#213e35}h2{margin-top:36px}p{max-width:1180px}.scroll{overflow:auto;background:white}table{border-collapse:collapse;white-space:nowrap;font-size:14px}th,td{text-align:right;padding:8px 12px;border-bottom:1px solid #dce3df}th{background:#e1eee6}td:first-child,th:first-child{text-align:left}</style><body><p><a href="每日与累计收益.md">完整文字报告</a> · <a href="summary.json">全部每日与累计数据</a></p>'''+''.join(pieces)+'</body></html>'
    (OUT/'每日与累计收益.html').write_text(page,encoding='utf-8')
    for p in [Path(__file__),ROOT/'scripts/probe_commodity_dadao_user_cost.py',*OUT.glob('*')]:
        if p.is_file() and p.name!='manifest.json':manifest[str(p.relative_to(ROOT))]=sha(p)
    write(OUT/'manifest.json',manifest)
    print(json.dumps({k:v for k,v in state.items() if k not in ['rows','daily_by_code','daily_aggregate']},ensure_ascii=False),flush=True)
    print(table(['日期','白银','铜'],[[d,*[n(dm[c][d]['pnl_cny']) for c in FOCUS[:2]]] for d in dates]))
    for c in FOCUS:print(c,accounts[c]['summary']['pnl_cny'],accounts[c]['summary']['complete_cycles'])

if __name__=='__main__':main()
