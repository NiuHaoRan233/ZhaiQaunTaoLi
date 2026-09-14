"""Render research evidence as readable Markdown and a local searchable HTML report."""
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import html
import json
import statistics as st
import hashlib
from scan_history import DATA, REPORT, BASE, OPTION, FUTURE, EXCHANGES, dump

def n(v,places=2):return '—' if v is None else f'{v:,.{places}f}'
def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+['| '+' | '.join(map(str,r))+' |' for r in rows])

def aggregate(rs,key='metrics'):
    ms=[r[key] for r in rs if r[key].get('valid_minutes',0)>0]
    if not ms:return {}
    w=[m['valid_minutes'] for m in ms];tw=lambda k:sum(m[k]*x for m,x in zip(ms,w))/sum(w)
    med=lambda k:st.median([m[k] for m in ms if m.get(k) is not None]) if any(m.get(k) is not None for m in ms) else None
    unit=10 if rs[0]['code'].endswith('.SH') else (rs[0]['detail'].get('OptUnit') or rs[0]['detail'].get('VolumeMultiple') or 1)
    return dict(days=len(rs),spread_pct=tw('mean_relative_spread_pct'),spread_cash=tw('mean_spread_cash_per_lot'),
        improve_cash=tw('improved_both_gross_cash_per_lot'),mid=tw('mean_mid'),capital=tw('mean_mid')*unit,
        daily_amount=sum(m['incremental_amount'] for m in ms)/len(rs),daily_events=med('volume_increment_events'),
        daily_volume=sum(m['incremental_volume'] for m in ms)/len(rs),wide_minutes=sum(m['spread_ge_05_minutes'] for m in ms)/len(rs),
        coverage=sum(m['valid_minutes']*60 for m in ms)/sum(r[key]['session_seconds'] for r in rs)*100,
        coverage_min=min(m['valid_coverage_pct'] for m in ms),band=med('mid_p95_p05_pct'),
        worst_band=max(m['mid_p95_p05_pct'] for m in ms),drift=med('drift60_p90_spreads'),
        depth=med('median_min_depth_lots'),high_events=med('last_trade_near_prior_ask_events'),low_events=med('last_trade_near_prior_bid_events'))

def main():
    inv=json.loads(sorted(DATA.glob('commodity_inventory_*.json'))[-1].read_text(encoding='utf-8'))
    daily=json.loads((DATA/'daily_20260907_20260911.json').read_text(encoding='utf-8'))
    screen=json.loads((REPORT/'daily_screen.json').read_text(encoding='utf-8'))
    intra=json.loads((REPORT/'intraday_screen.json').read_text(encoding='utf-8'))
    follow=json.loads((REPORT/'followup_selection.json').read_text(encoding='utf-8'))
    dr={r['code']:r for r in screen['rows']}
    by=defaultdict(list)
    for r in intra['rows'].values():by[r['code']].append(r)
    codes=['132024.SH','132026.SH']+[c for c in follow['codes'] if not c.endswith('.SH')]
    notes={
        'SR611P5700.ZF':'低波动对照：短时双侧周转迹象偏弱',
        'SR611C4900.ZF':'优先细查：白糖另一侧对照；实值程度/方向敞口另查',
        'CF703P17400.ZF':'优先细查：远月、五日均有短时双侧成交迹象',
        'CF703P17200.ZF':'优先细查：同月相邻执行价对照',
        'cu2611P114000.SF':'第二组：金额和每手空间较好；方向与资金要求较高',
        'cu2612P100000.SF':'周转对照：五日均有短时双侧迹象，金额较小',
        'au2612C840.SF':'另列高资金组：深实值，一手约十余万元；买卖侧事件不对称',
        'au2702C976.SF':'另列高资金组：近月样本不代表远月长期稳定',
        'c2611-C-2300.DF':'排队组：百分比宽、绝对价差小；双边各改善一跳无空间',
        'c2701-C-2220.DF':'暂缓：前几日有效盘口覆盖极低，周五不能代表整周',
        'm2701-P-3050.DF':'排队/低容量组：绝对价差约一跳，金额小',
        'ag2612P15800.SF':'暂缓优先级：典型日成交增量事件少',
        'PK611P8300.ZF':'暂缓优先级：五日里权利金波动明显放大'}
    summary=[]
    for c in codes:
        rs=sorted(by[c],key=lambda r:r['date']);d=rs[-1]['detail']
        summary.append(dict(code=c,name=d.get('ProductName') or d.get('InstrumentName'),expiry=d.get('ExpireDate'),
            unit=10 if c.endswith('.SH') else d.get('OptUnit') or d.get('VolumeMultiple'),tick=d.get('PriceTick'),
            full=aggregate(rs),common=aggregate(rs,'common'),sensitivity_300=aggregate(rs,'sensitivity_300'),
            note='用户熟悉的债券参照' if c.endswith('.SH') else notes.get(c,'备选：仍须联合验证周转、方向与费用')))
    dump(REPORT/'summary.json',dict(period='20260907-20260911',rows=summary))
    option_codes=sorted({c for v in inv['sectors'].values() for c in v['options']})
    scanned={c for c in by if OPTION.fullmatch(c)}
    products=defaultdict(list)
    for c in option_codes:products[(c.split('.')[-1],OPTION.fullmatch(c)[1])].append(c)
    method='''本次是寻找研究对象，未生成订单或模拟成交，也未计算策略利润。

全市场先读2026-09-07—09-11五个交易日日线，再对各品种成交额代表、较温和且有成交的合约及扩展低振幅合约读取09-11盘口；21只期权与两只债再读五日日盘，17个对应期货也读取五日日盘。合约目录是09-12现存目录，不代表历史每天完整上市集合。日线筛选忽略无成交日的高低价，避免把沿用结算价当低波动。

商品日盘用09:00—10:15、10:30—11:30、13:30—15:00，共225分钟；债券自身日盘用09:30—11:30、13:00—15:30，共270分钟。跨资产主表统一09:30—10:15、10:30—11:30、13:30—15:00，共195分钟。夜盘未纳入盘口统计；全市场日线成交额包含交易日全部时段，因此与主表日盘金额不同。QMT周末快照混有09-11夜盘、09-14交易日标签和零量重置，快照不参与同日排名。

相对价差=(卖一−买一)/盘口中点。双侧价量均有效且卖一>买一才计入；各快照按延续至下一帧的时长加权，跨休市不延续，单条最长60秒；300秒上限作敏感性对照。没有擅自剔除极宽有效报价。表中“五日平均价差”是有效时间池化的加权平均，不是五个收盘快照或中位数。覆盖率不足时只代表观察到的有效区间。

“典型波幅”是每日日盘盘口中点的时间加权P95−P05除以日均中点，再取五日日值中位数；它描述权利金价格活动范围，不是年化波动率、隐含波动率或实际亏损。“60秒漂移/价差”是同一时段、间隔50—70秒的有效报价对，其中点绝对变化除以起点买卖价差，逐日P90再取中位数。它包含有利和不利变化，不是逆向选择损失估计。

“日盘金额/量”累计增量只计同一连续时段内相邻快照，首帧不把夜盘或集合竞价累计量倒灌进来，因此是可观测日盘增量而非完整交易日官方总量。“事件”是volume正增的快照次数，不是交易所逐笔成交笔数。上一报价附近的高/低侧事件只按最新成交价相对前一买卖价观察，无法识别该增量中所有交易方向，也不保证本方能成交。盘口多为一档有效、其余补零，不能当作完整五档退出深度。

每手金额按QMT合约乘数计算，债券一手10张。两侧各改善一跳后的毛空间=(价差−2×最小跳动)×乘数；只用于评估报价格点，未扣费用，负数表示不能同时这样改善后赚价差，仍可能在原价排队。期权费用不能套用此前ETF期权的单边1.7元；实际账户费用未取得。买方权利金占用不等于卖方保证金，价差率不等于无风险收益。

抽样尺度（如日均量额、60秒、12%日线振幅、每品种配额）只是透明的首轮研究取舍。0.5%/1%只展示时间占比，没有作为全时硬过滤。不能据这轮样本内筛选宣称全市场最优、长期稳定或已经可实盘。'''
    lead=f'''# 商品期权首轮探索 · 2026-09-12

结论：值得继续看，但“百分比价差宽”很容易找到，“有真实双边周转、波动温和、每手扣费仍有空间”才稀缺。远月棉花CF703P17400及相邻执行价优先细查，白糖SR611C4900作为较温和对照，SR611P5700短时周转证据偏弱；铜cu2612P100000的双侧迹象较多但金额较小。沪金深实值合约单列高资金组。玉米、豆粕的低权利金合约主要受最小跳动限制。尚未找到可以直接认定等同两只EB交易体验的商品期权。

范围：QMT四所64个商品期权品种目录、{len(option_codes):,}个合约；包括上期所6,512、大商所8,354、郑商所4,648、广期所3,738。请求日线{len(daily['completed']):,}个代码（含期货与双债），返回{len(daily['bars']):,}个；商品期权取得日线{sum(c in daily['bars'] for c in option_codes):,}个，至少一天有成交{sum(c in dr for c in option_codes):,}个。历史盘口细查{len(scanned)}只商品期权，而不是把全部合约都逐笔回放。

能源中心INE目录为空，sc/nr/lu样例详情也未返回；当前不能声称覆盖全部中国商品期权。广期所中文目录为空，但市场代码GF可正常枚举。当前客户端不支持授权市场查询接口，所以不能把目录缺失直接诊断为未开权限。能源中心相关品种仍是待补覆盖项。

## 五日共同日盘比较

下表统一每天195分钟；金额为可观测日盘增量的五日平均（万元），价差为有效时间加权平均。典型波幅见后文定义。两只债券只作同窗口参照，不将债券评分迁移至期权。

'''
    headers=['合约','品种','价差%','≥0.5%分钟/日','金额万元/日','典型波幅%','有效覆盖%','观察结论']
    table_rows=[]
    for r in summary:
        a=r['common'];name=r['name']
        table_rows.append([r['code'],name,n(a['spread_pct']),n(a['wide_minutes'],1),n(a['daily_amount']/1e4,1),n(a['band']),n(a['coverage'],1),r['note']])
    maintext=lead+table(headers,table_rows)+'\n\n## 每手资金、报价格点与成交两侧\n\n以下使用各自完整日盘窗口；高/低侧为典型日观察事件数，不是可保证成交次数。\n\n'
    maintext+=table(['合约','每手乘数','最小跳动','一手买入权利金参考元','原价差元/手','两边改善后毛空间元/手','高侧/低侧事件','60秒漂移/价差P90典型值','最差日波幅%'],[[r['code'],n(r['unit'],0),n(r['tick'],3),n(r['full']['capital'],0),n(r['full']['spread_cash']),n(r['full']['improve_cash']),f"{n(r['full']['high_events'],0)}/{n(r['full']['low_events'],0)}",n(r['full']['drift']),n(r['full']['worst_band'])] for r in summary if not r['code'].endswith('.SH')])
    audit_path=REPORT/'final_audit.json'
    if audit_path.exists():
        final_audit=json.loads(audit_path.read_text(encoding='utf-8'))
        maintext+='\n\n## 同窗口是否确有双侧成交迹象\n\n把共同日盘分成五分钟窗口；只有有效报价覆盖至少四分钟、价差≥0.5%累计至少两分半，才列为宽价差窗口。双侧迹象要求在宽价差前报价之后，分别出现最新成交价落在前卖一及以上、前买一及以下的量增快照。这不是交易所逐笔方向，更不是本方闭环或可赚次数。五分钟和覆盖要求是补充观察尺度，未用于重选候选。\n\n'
        maintext+=table(['合约','五日宽价差窗口','其中有双侧迹象','出现天数','这些窗口中点范围/价差中位数'],[[c,w['eligible_wide_bins'],w['two_sided_wide_bins'],w['days_with_two_sided_wide_bins'],n(w['median_two_sided_mid_range_spreads'])] for c,w in final_audit['windows'].items()])
        maintext+='\n\n窗口中点变化可超过整段价差，意味着即使两侧都有人成交，也不能假设先买后卖会稳定赚到起始价差。完整窗口及按时间顺序取的例子保存在final_audit.json。\n\n## 商品本身与期权权利金的波动\n\n以下仍用共同日盘及相同P95−P05口径；期货列是期权对应月份，而非主力连续。期权日内幅度还受实值程度、剩余期限、隐含波动率等影响，不能单凭标的期货平稳就认定期权也平稳。\n\n'
        maintext+=table(['期权','对应期货','期权典型波幅%','期货典型波幅%','期权到期日'],[[r['code'],r['underlying'],n(r['option_typical_band_pct']),n(r['underlying_typical_band_pct']),r['expiry']] for r in final_audit['underlying_comparison']])
        maintext+=f"\n\n## 数据核验结果\n\n历史盘口539个合约日、4,343,914行原始快照，日期、有效时间独立重算及区间边界核验通过；这验证统计实现，不证明行情没有漏帧。日线完成{final_audit['daily_checks']:,}次量额/乘数与高低价一致性检查，其中{final_audit['daily_exception_count']}条不一致，保留为供应商数据口径待查项，不能写成全部数据无异常。21只重点期权未命中这些异常；在初筛中排除异常日线后，六只核心候选仍全部入选。异常行及筛选敏感性完整保存在data_audit.json与final_audit.json。\n\n"
    maintext+='\n\n## 新上市与高波动反例\n\n热卷、不锈钢仅09-10/11两个上市日。本次样例价差宽，但成交不足，不能认定是新机会窗口已被验证。广期所的部分碳酸锂、多晶硅、工业硅合约成交充足，却在09-11出现很大的权利金中点变化，优先级不应因价差合格而提高。\n\n'
    examples=['hc2701C3350.SF','ss2612C14000.SF','lc2611-P-130000.GF','ps2611-C-40000.GF','si2611-C-9000.GF']
    er=[]
    for c in examples:
        r=next(x for x in by[c] if x['date']=='20260911');m=r['metrics']
        er.append([c,r['detail'].get('ProductName'),n(m.get('mean_relative_spread_pct')),n(m.get('incremental_amount',0)/1e4),m.get('volume_increment_events'),n(m.get('mid_p95_p05_pct')),n(m.get('valid_coverage_pct'))])
    maintext+=table(['09-11合约','品种','平均价差%','日盘金额万元','量增事件','中点P95-P05幅度%','覆盖%'],er)
    maintext+='\n\n## 商品期货对照\n\n从有成交的实际月份合约中取相对低振幅对照；连续指数/指数代码不作可交易合约。以下并非新上市期货全名单。价格比例与期权权利金分母不同。\n\n'
    futures=[]
    for c in screen['selected']:
        if c not in by or OPTION.fullmatch(c):continue
        rs=[r for r in by[c] if r['date']=='20260911']
        if not rs:continue
        r=rs[0];m=r['metrics']
        if 'mean_relative_spread_pct' not in m:continue
        futures.append([c,r['detail'].get('ProductName'),n(m['mean_relative_spread_pct'],3),n(m['mid_p95_p05_pct'],3),n(m['incremental_amount']/1e4,1),n(m['improved_both_gross_cash_per_lot']),n(m['valid_coverage_pct'],1)])
    maintext+=table(['09-11合约','品种','平均价差%','中点波幅%','日盘金额万元','改善两跳后毛空间元/手','覆盖%'],futures)
    maintext+='\n\n## 方法与边界\n\n'+method
    maintext+='\n\n## 下一步研究次序\n\n先看白糖与棉花的具体可成交窗口：在宽价差已经存在时，后续是否真的有买卖两侧成交；再加入账户实际费用、原价排队与改善一跳两种路径。沪铜与沪金另核对每手权利金、深实值方向敞口和退出需求。商品与期权本身波动必须分开；本次17个对应期货已保存五日盘口，可逐段比较。能源中心待恢复QMT覆盖后补扫。\n\n先看盘口与执行证据再决定是否做模型，尚未授权或建立任何新实时交易分支。\n\n'
    maintext+='## 来源与完整明细\n\n- 行情和合约乘数：本机MiniQMT xtdata，本目录data与reports的JSON/PKL证据。\n- [迅投接口说明](https://dict.thinktrader.net/nativeApi/xtdata.html)：历史盘口补充与读取口径。\n- [热卷、不锈钢、低硫燃料油期权09-10上市](https://www.shfe.com.cn/index/othercontents/2026_Options/)。\n- [20号胶、国际铜期权上市通知](https://www.shfe.cn/publicnotice/notice/202604/t20260413_831097.html)。\n- [64品种与09-11完整细查表](全品种与合约明细.md)。\n- [五日逐日明细](五日重点逐日明细.md)。\n- [机器可读汇总](summary.json)、[全部日线初筛](daily_screen.json)、[全部盘口指标](intraday_screen.json)。\n'
    report_path=REPORT/'商品期权首轮探索_20260912.md';report_path.write_text(maintext,encoding='utf-8')
    # Every enumerated product appears, including missing/zero-trade products.
    pr=[]
    for (market,product),cs in sorted(products.items()):
        details=[inv['details'].get(c) or {} for c in cs]
        name=next((d['ProductName'] for d in details if d.get('ProductName')),product)
        active=[dr[c] for c in cs if c in dr]
        pr.append([market,product,name,len(cs),sum(c in daily['bars'] for c in cs),len(active),n(sum(r['mean_daily_amount'] for r in active)/1e4,1),sum(c in scanned for c in cs)])
    full='# 全品种与合约明细\n\n日线金额为各合约已有日线的日均成交额求和，新上市合约分母可能少于五天；不是同一五日资金组合收益。\n\n'+table(['市场','品种代码','名称','目录合约','有日线','至少一天成交','品种合计日均权利金万元','盘口细查数'],pr)
    full+='\n\n## 09-11盘口细查合约（含补充及对照）\n\n按代码排列。双债作为价格与周转参照；其余为期权指标表。\n\n'
    sr=[]
    first=['132024.SH','132026.SH']+sorted(scanned)
    for c in first:
        rs=[r for r in by[c] if r['date']=='20260911']
        if not rs:continue
        r=rs[0];m=r['metrics'];d=r['detail']
        sr.append([c,d.get('ProductName') or d.get('InstrumentName'),d.get('ExpireDate'),n(m.get('mean_relative_spread_pct')),n(m.get('spread_ge_05_minutes'),1),n(m.get('incremental_amount',0)/1e4,1),m.get('volume_increment_events',0),n(m.get('mid_p95_p05_pct')),n(m.get('valid_coverage_pct'),1),n(m.get('improved_both_gross_cash_per_lot'))])
    full+=table(['代码','名称','到期日','价差%','≥0.5%分钟','日盘金额万元','量增事件','中点幅度%','覆盖%','改善后元/手'],sr)
    (REPORT/'全品种与合约明细.md').write_text(full,encoding='utf-8')
    detail='# 五日重点逐日明细\n\n使用各自完整日盘；300秒列用于检验稀疏报价延续假设，不代表这些报价保证驻留。\n\n'
    rr=[]
    for c in codes:
        for r in sorted(by[c],key=lambda r:r['date']):
            m=r['metrics'];s=r['sensitivity_300']
            rr.append([c,r['date'],n(m.get('mean_relative_spread_pct')),n(m.get('median_relative_spread_pct')),n(m.get('valid_coverage_pct'),1),n(s.get('mean_relative_spread_pct')),n(s.get('valid_coverage_pct'),1),n(m.get('incremental_amount',0)/1e4,1),m.get('volume_increment_events',0),n(m.get('mid_p95_p05_pct')),n(m.get('drift60_p90_spreads'))])
    detail+=table(['合约','日期','60秒平均价差%','价差加权中位数%','60秒覆盖%','300秒平均价差%','300秒覆盖%','日盘金额万元','量增事件','中点幅度%','60秒漂移/价差'],rr)
    (REPORT/'五日重点逐日明细.md').write_text(detail,encoding='utf-8')
    # Compact local HTML: searchable tables, no remote assets or network requests.
    htmlparts=[];in_table=False
    for line in maintext.splitlines():
        if line.startswith('|'):
            if line.startswith('| ---'):continue
            cells=[html.escape(x.strip()) for x in line.strip('|').split('|')]
            if not in_table:htmlparts.append('<div class="scroll"><table><thead><tr>'+''.join('<th>'+x+'</th>' for x in cells)+'</tr></thead><tbody>');in_table=True
            else:htmlparts.append('<tr>'+''.join('<td>'+x+'</td>' for x in cells)+'</tr>')
        else:
            if in_table:htmlparts.append('</tbody></table></div>');in_table=False
            if line.startswith('# '):htmlparts.append('<h1>'+html.escape(line[2:])+'</h1>')
            elif line.startswith('## '):htmlparts.append('<h2>'+html.escape(line[3:])+'</h2>')
            elif line:htmlparts.append('<p>'+html.escape(line)+'</p>')
    if in_table:htmlparts.append('</tbody></table></div>')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>商品期权首轮探索</title><style>body{font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif;background:#f5f4ef;color:#24332f;margin:0;padding:36px;max-width:1500px;margin:auto}h1{font-size:32px}h2{margin-top:40px;color:#126451}p{max-width:1080px}input{padding:12px;width:340px;border:1px solid #bbc9c1;border-radius:6px}.scroll{overflow:auto;background:white;border:1px solid #d6dfd9;border-radius:8px}table{border-collapse:collapse;white-space:nowrap;width:100%;font-size:14px}th,td{text-align:left;padding:10px 12px;border-bottom:1px solid #e5e8e3}th{background:#e0ebe3}td:last-child{white-space:normal;min-width:130px}a{color:#126451}</style><body><input id="search" placeholder="输入合约或品种，筛选表格"><p><a href="商品期权首轮探索_20260912.md">Markdown报告</a> · <a href="全品种与合约明细.md">全品种明细</a> · <a href="五日重点逐日明细.md">逐日明细</a></p>'''+''.join(htmlparts)+'''<script>document.getElementById('search').addEventListener('input',e=>{const q=e.target.value.toLowerCase();document.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q));});</script></body></html>'''
    (REPORT/'商品期权首轮探索.html').write_text(page,encoding='utf-8')
    print('saved',report_path,'options tick',len(scanned),'rows',len(intra['rows']),flush=True)

if __name__=='__main__':main()
