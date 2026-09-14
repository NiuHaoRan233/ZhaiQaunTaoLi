"""Explain fixed commodity ablations without rewriting their accounts."""
from pathlib import Path
from collections import defaultdict
from statistics import median
import gzip,hashlib,html,json
from zhaiquant import commodity_selective_research as engine
from report_commodity_dadao import table

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'广义套利/reports/commodity_optimization_20260912'
LABELS={'control':'原循环','fee_edge':'费用空间','stable_entry':'稳定盘口入场','patient_exit':'有限等待退出',
        'day_flat':'日内清仓','combined':'稳定+等待+清仓','flow_entry':'双侧成交入场','flow_patient':'双侧成交+等待退出'}
FOCUS=['au2612C920.SF','au2612C840.SF','ag2612P16000.SF','cu2611P114000.SF','pt2612-C-448.GF',
       'CF703P17400.ZF','PK612P8400.ZF','jm2701-P-1460.DF','c2611-C-2300.DF','FG701C1000.ZF']
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def write(p,x):p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
def n(v):return '—' if v is None else f'{v:,.2f}'
def phase(date):return 'selection' if date=='20260911' else 'later' if date>='20260907' else 'development'

def main():
    accounts={};prefixes=equivalences=0;manifest={}
    for p in sorted(OUT.glob('part_*/matrix.json')):
        s=read(p);assert not s['errors'],s['errors'];prefixes+=s['prefix_checks'];equivalences+=s['baseline_equivalences']
        assert all(hashlib.sha256((ROOT/k).read_bytes()).hexdigest()==v for k,v in s['source_hashes'].items())
        for key,summary in s['accounts'].items():
            path=p.parent/'accounts'/f'{key}.json.gz'
            r=json.loads(gzip.decompress(path.read_bytes()));assert r['summary']==summary
            engine.source.audit_result(r);accounts[key]=r
            manifest[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
        manifest[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    assert len(accounts)==64*len(engine.VARIANTS),(len(accounts),'not complete')
    assert equivalences==64
    baseline={r['summary']['code']:r for r in accounts.values() if r['summary']['variant']=='control'}
    grouped=defaultdict(list)
    for r in accounts.values():grouped[r['summary']['variant']].append(r)
    variants=[];bycode=defaultdict(dict);dates=sorted(set(d['date'] for r in accounts.values() for d in r['daily']))
    daily=defaultdict(lambda:defaultdict(float))
    for variant in engine.VARIANTS:
        rs=grouped[variant];ss=[r['summary'] for r in rs]
        z=dict(variant=variant,label=LABELS[variant],contracts=len(rs),pnl_cny=sum(s['pnl_cny'] for s in ss),
            fees_cny=sum(s['fees_cny'] for s in ss),complete_cycles=sum(s['complete_cycles'] for s in ss),
            fills=sum(s['fill_count'] for s in ss),tail_contracts=sum(s['end_inventory'] for s in ss),
            open_cycle_cny=sum(s['open_cycle_contribution_cny'] for s in ss),
            realized_gross_cny=sum(s['realized_gross_cny'] for s in ss),
            positive=sum(s['pnl_cny']>0 for s in ss),negative=sum(s['pnl_cny']<0 for s in ss),
            zero_trade_contracts=sum(s['fill_count']==0 for s in ss),
            improved=sum(s['pnl_cny']>baseline[s['code']]['summary']['pnl_cny'] for s in ss),
            worsened=sum(s['pnl_cny']<baseline[s['code']]['summary']['pnl_cny'] for s in ss),
            max_account_drawdown_cny=max(s['max_drawdown_cny'] for s in ss),
            median_account_drawdown_cny=median(s['max_drawdown_cny'] for s in ss),
            holding_contract_hours=sum(s['holding_seconds'] for s in ss)/3600,
            active_exits=sum(s['active_exit_fills'] for s in ss),phases=dict(development=0.,later=0.,selection=0.))
        all_cycles=[c for r in rs for c in r['cycles']]
        top=sorted(all_cycles,key=lambda c:-c['net_cents'])
        z['without_best_three_pnl_cny']=z['pnl_cny']-sum(c['net_cents'] for c in top[:3])/100
        for r in rs:
            s=r['summary'];c=s['code'];b=baseline[c]['summary']
            x=dict(s);x['delta_cny']=s['pnl_cny']-b['pnl_cny'];x['phases']=dict(development=0.,later=0.,selection=0.)
            for d in r['daily']:
                z['phases'][phase(d['date'])]+=d['pnl_cny'];x['phases'][phase(d['date'])]+=d['pnl_cny']
                daily[d['date']][variant]+=d['pnl_cny']
            bycode[c][variant]=x
        assert abs(sum(z['phases'].values())-z['pnl_cny'])<1e-6
        z['delta_cny']=z['pnl_cny']-sum(r['summary']['pnl_cny'] for r in baseline.values())
        variants.append(z)
    state=dict(account_count=len(accounts),baseline_equivalences=equivalences,prefix_checks=prefixes,
               variants=variants,by_code=dict(bycode),daily=dict(daily),dates=dates)
    write(OUT/'optimization_summary.json',state)
    md='''# 商品期权：为什么赚亏，首轮怎么改

按用户指定每手单边1.7元、下单延迟0，保持冻结64只/最多1手/各自首日资金。原始基线和全部金银铜盈利线索保留。先独立解释旧收益，再登记少量单项及组合候选，从原始行情重新生成订单、成交与连续库存；没有筛掉事后亏损回合后直接相加。

首轮最值得继续验证的是“双侧成交+有限等待退出”：64独立账户收益和由−93,962.10变+21,839.70，642个完整回合，24只盈、25只亏、15只没有成交。剔除原选择日09-11仍+7,132.80；去掉全样本最大三个盈利闭环仍+6,064.90。它不是减少交易到零：有49只产生交易，但成交和占仓明显减少，未平仓5手的贡献−1,760.50已计入净值。

同时必须保留退化：原黄金au2612C920 +8,347.40变−247.20、铂+5,138.10变零成交，棉花和花生也少赚。它解决普通双侧周转的质量问题，却会错过部分安静盘口的特殊机会；不能按这次赢家名单设例外。原模型继续作为黄金/铂等研究对照，不被新候选覆盖。单纯低波动、大价差或统一日内清仓没有在本轮实现整体盈利。

独立原始窗口复核显示，新铜cu2612C104000的改善主要来自入场：原−15,163.40和−6,193.40元的回合在买单建立前缺一侧近期成交，新两候选当时空仓未买；其新最大四个赢轮均跨日、合计13,526.40元，仍不能称稳定盘内价差。该券只改入场赚13,952.20元，比加等待的13,105.60更好，回撤也较小。

等待退出的同入场正反例都保存：铜09-09 09:28:19买一手36,960元，立即跟卖一的路径半秒后36,940卖亏23.40，保护价未被后续成交打到所以保留，09:28:37有新量增证据后36,990卖赚26.60；另一笔09-02 09:47:06买25,390元，本可25,380卖亏13.40，等待路径最后24,510卖亏883.40。300秒只是最低卖价保护时限，解除后还需真实成交，不能保证300秒平仓。

## 旧循环的三种主要机制

第一，费用吃掉窄空间周转。玉米c2611-C-2300毛赚3,885元、费6,062.20，净亏2,177.20；玻璃毛赚3,410元却净亏1,088.20。买卖各改善一跳后才是拟交易空间，不能拿原始盘口价差直接当毛利；两跳盘口可能改善到同价，每轮白付3.4元。

第二，有价差但没有可靠的双侧周转。旧路径近300秒有买卖两侧末价证据的4,087笔买入归因+12,462.90，缺一侧的2,315笔−106,425；最差十个回合都缺至少一侧。相反，仅按大价差、低60秒漂移/波幅分组仍明显亏损。冷清报价不动可能只是无人交易，不代表低风险。此分组本身不是新策略回测；棉花等缺侧低买也有盈利，过滤会付出漏掉正确机会的代价。

第三，持仓方向与退出模式决定大回合。旧6,386闭环里，同日6,075轮净+12,641，跨日311轮净−98,895.90；剩余末尾未闭环贡献另计−7,707.20。黄金au2612C920同日+9,434.60、跨日−1,087.20；黄金au2612C840同日+19,718.40、跨日−44,140.40。白银ag2612P16000却依靠跨日赚钱，所以不能普遍认定隔夜错误。买入后当帧卖目标已低于成本的回合也是一类问题，但该信息不能倒推撤销已发生买入，只能研究后续退出。

成交后中点可能被撤档扭曲，不能据它断言卖得过低。例如黄金840某次卖出116,980元后，卖一突然撤到239,980元、中点大跳；中点分解的负卖出贡献不表示存在那个中点价格的买家。独立报告保留成交源前报价/创建报价与成交后报价的区别。

## 冻结的候选

费用空间：双侧改善后的毛空间≥双边费用+1跳。稳定盘口：费用门槛之外，至少60秒过去报价，60秒中点跌幅≤当前价差一半、300秒中点范围≤当前价差3倍。双侧成交入场：费用门槛之外，过去300秒同连续时段、新鲜有效证据内买卖方向各至少一次；方向仍是L1估计。

有限等待退出：费用过滤后，卖一目标暂时低于成本+双边费+1跳时保留该底线，最长300秒墙钟；若中点相对入场下移超过入场价差且持续30秒，则提前解除。日切/超时均恢复原卖一退出，允许亏损卖出。日内清仓：14:30不再新买、14:55按当前可见买一和深度主动卖出，缺当前买盘时留仓并记录，不能虚构平仓。

组合分别检查稳定+等待+清仓，以及双侧成交+等待，避免把全部改变的效果混为一个。所有条件只用当时已知信息，旧单先结算；不存在金银铜或赢家白名单。

## 全部64只独立账户对照

单位元。收益和是64个独立资金账户的算术和，不是共享资金组合。每只初始资金沿用真实保存基线，不因过滤而补资；缺失日期照旧。零成交合约数量单列，减少交易带来的少亏不自动算找到赚钱模式。

'''
    md+=table(['模型','净收益','相对原循环','闭环数','手续费','盈/亏合约','改善/退化','零成交','末仓手'],[
        [v['label'],n(v['pnl_cny']),n(v['delta_cny']),v['complete_cycles'],n(v['fees_cny']),f"{v['positive']}/{v['negative']}",f"{v['improved']}/{v['worsened']}",v['zero_trade_contracts'],v['tail_contracts']] for v in variants])
    md+='\n\n## 按时间分段与风险\n\n开发期08-24—09-04，后段09-07—09-10，09-11是原选择日。每天按连续净值差，后段自然继承此前策略持仓，未重置资金；所有日期此前已展示，因此不是未经查看的严格样本外。去前三回合为集中度诊断，不重新运行删回合策略。最大回撤是最差单个账户的日盘买一盯市回撤，不是组合回撤，夜间未观察风险可能更大。\n\n'
    md+=table(['模型','开发期','后段4日','选择日','去前三赚钱回合后','最差单账户回撤','中位账户回撤','占仓小时和'],[
        [v['label'],*[n(v['phases'][p]) for p in ['development','later','selection']],n(v['without_best_three_pnl_cny']),n(v['max_account_drawdown_cny']),n(v['median_account_drawdown_cny']),n(v['holding_contract_hours'])] for v in variants])
    md+='\n\n## 金银铜与代表正反例\n\n所有原盈利品种保留。这些结果不能被总收益遮盖。\n\n'
    md+=table(['合约',*[LABELS[v] for v in engine.VARIANTS]],[[c,*[n(bycode[c][v]['pnl_cny']) for v in engine.VARIANTS]] for c in FOCUS])
    md+='\n\n## 每日收益：全部独立账户和\n\n'
    md+=table(['日期',*[LABELS[v] for v in engine.VARIANTS]],[[d,*[n(daily[d][v]) for v in engine.VARIANTS]] for d in dates])
    md+='\n\n## 全部64只逐合约总收益\n\n'
    codes=sorted(bycode,key=lambda c:-bycode[c]['flow_entry']['pnl_cny'])
    md+=table(['合约',*[LABELS[v] for v in engine.VARIANTS]],[[c,*[n(bycode[c][v]['pnl_cny']) for v in engine.VARIANTS]] for c in codes])
    # Explicit losses/foregone winners, not only the largest positive deltas.
    md+='\n\n## 双侧成交两候选的主要改善与退化\n\n'
    for v in ['flow_entry','flow_patient']:
        ranked=sorted(bycode,key=lambda c:bycode[c][v]['delta_cny']);selected=ranked[:6]+ranked[-6:]
        md+='\n\n'+LABELS[v]+'：\n\n'
        md+=table(['合约','原循环','新候选','变化','新闭环','新末仓'],[[c,n(bycode[c]['control']['pnl_cny']),n(bycode[c][v]['pnl_cny']),n(bycode[c][v]['delta_cny']),bycode[c][v]['complete_cycles'],bycode[c][v]['end_inventory']] for c in selected])
    md+='\n\n## 双侧成交+等待退出：全部64只每日净收益\n\n日度是连续净值差，缺失数据不填0；后一个可见日期可能包含缺口期间持仓价格变化。表内仍是原冻结64只，未删除旧盈利合约。\n\n'
    daily_rows=[]
    for c in codes:
        r=accounts[c+'_flow_patient'];dm={d['date']:d for d in r['daily']}
        daily_rows.append([c,*[n(dm[d]['pnl_cny']) if d in dm else '缺失' for d in dates],n(r['summary']['pnl_cny'])])
    md+=table(['合约',*[d[4:6]+'-'+d[6:] for d in dates],'合计'],daily_rows)
    md+=f'''\n\n## 核验与范围

新核control对64份保存基线逐单/逐笔/逐帧净值及逐日收益经济等价；{len(accounts)}账户现金、库存、费用、尾仓重算，8只代表合约×8变体×2截断共{prefixes}个前缀检查。727项主工程测试通过，其中23项新独立正反测试。独立核验512账户/33,454成交/23,315,320帧/7,288日净值，316笔主动退出均回查原始同帧买一和深度，0错误。候选合同、源码与完整账户哈希归档，旧71项文件保持。独立原始入场特征审计和利润来源审计分别位于entry_quality、profit_sources文件；新候选独立原始盘口核验见verification.json。

0延迟不意味着已有买一/卖一队列为零。原始代表样本检查中，黄金920的全部买卖是改善后进入价差内部，并非都靠加入已有最优价；焦煤部分盈利与加入已有最优档的成交假设有关，需继续核实。夜盘、实际队列与冲击没有由这次回放补齐，未生产晋级。具体参数来自执行前的研究合同；后续再改决策必须另登记身份，不覆盖这批。
'''
    (OUT/'收益归因与优化结果.md').write_text(md,encoding='utf-8')
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
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>商品期权收益归因与优化</title><style>body{font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif;background:#f4f4ee;color:#24382f;max-width:1500px;margin:auto;padding:28px}h1{font-size:30px}h2{margin-top:35px;color:#126451}p{max-width:1200px}.scroll{overflow:auto;background:white;border:1px solid #d5e0d8}table{border-collapse:collapse;white-space:nowrap;font-size:14px}th,td{text-align:right;padding:9px 12px;border-bottom:1px solid #e0e7e2}th{background:#e1ebe3}td:first-child,th:first-child{text-align:left}input{padding:8px;width:340px;font:inherit;max-width:90%}</style><body><input id="search" aria-label="搜索合约或模型" placeholder="搜索合约或模型；清空看全部"><p><a href="收益归因与优化结果.md">全文</a> · <a href="optimization_summary.json">全部数据</a> · <a href="profit_sources.md">独立利润来源</a> · <a href="entry_quality.md">独立入场归因</a></p>'''+''.join(parts)+'''<script>document.querySelector('#search').addEventListener('input',e=>{let q=e.target.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q))})</script></body></html>'''
    (OUT/'收益归因与优化结果.html').write_text(page,encoding='utf-8')
    for p in [Path(__file__),Path(engine.__file__),OUT/'candidate_contract.json',OUT/'optimization_summary.json',OUT/'收益归因与优化结果.md',OUT/'收益归因与优化结果.html']:
        manifest[str(p.relative_to(ROOT))]=hashlib.sha256(p.read_bytes()).hexdigest()
    write(OUT/'result_manifest.json',manifest)
    for v in variants:print(v['variant'],n(v['pnl_cny']),v['phases'],'cycles',v['complete_cycles'],'better/worse',v['improved'],v['worsened'],flush=True)

if __name__=='__main__':main()
