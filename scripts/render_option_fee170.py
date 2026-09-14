"""Extend the frozen report with provisional fee170 and explicit path attribution."""
import argparse
import gzip
import json
from pathlib import Path

from probe_option_top_cycle import ROOT, ARCHIVE, NAMES, load, digest
from render_option_guard import thin, LABELS, MODE_LABELS
from audit_option_cycle_losses import analyze
from zhaiquant.option_guard_research import PROFILES
from zhaiquant.option_top_cycle_research import MODES

OLD=ARCHIVE/'大道至简过滤_20260909_v2'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--input',required=True);args=parser.parse_args()
    out=Path(args.input);matrix=json.loads((out/'matrix.json').read_text(encoding='utf-8'))
    S=matrix['summaries'];old=json.loads((OLD/'matrix.json').read_text(encoding='utf-8'))
    def get(c,p='entry',m='improve_single_d500'):
        return next(s for s in S if s['code']==c and s['profile']==p and s['mode']==m)
    def total(k,p='entry',m='improve_single_d500'):return round(sum(get(c,p,m)[k] for c in NAMES),2)
    def fmt(x):return f'{x:+.2f}'
    def table(headers,rows):return '| '+' | '.join(headers)+' |\n|'+'|'.join('---' for _ in headers)+'|\n'+'\n'.join('| '+' | '.join(map(str,r))+' |' for r in rows)
    rows=[]
    for c,n in NAMES.items():
        s=get(c)
        rows.append([c+' '+n,'待评分',fmt(s['old_3cny_pnl']),fmt(s['fixed_old_path_fee170_pnl']),fmt(s['pnl_cny']),
                     fmt(s['gross_including_tail_cny']),f"{s['fees_cny']:.2f}",s['complete_cycles'],s['end_inventory']])
    modes=table(['执行方式','原版1.7元净值','过滤旧3元净值','过滤旧路径仅换费率','过滤1.7元重跑净值','重跑手续费','重跑完整轮次'],[
        [label,fmt(total('pnl_cny','control',m)),fmt(total('old_3cny_pnl',m=m)),fmt(total('fixed_old_path_fee170_pnl',m=m)),
         fmt(total('pnl_cny',m=m)),f"{total('fees_cny',m=m):.2f}",int(total('complete_cycles',m=m))] for m,label in zip(MODES,MODE_LABELS)])
    main_table=table(['代码/名称','评分','原3元净值','旧路径改1.7元','1.7元重新跑','毛值含尾仓','手续费','轮次','尾仓'],rows)
    ablation=table(['代码',*[LABELS[p] for p in PROFILES]],[[c,*[fmt(get(c,p)['pnl_cny']) for p in PROFILES]] for c in NAMES])
    md=f'''# 期权大道至简：单边1.7元费用复算

2026-09-09。用户补充“期权成本是单边1.7我记得”，本轮按每合约张单边1.7元、往返3.4元暂计总成本；尚未经交割单核实具体包含项目。之前单边3元是Agent额外假设，旧结果保留，不作为本轮默认。只改费用输入，冻结策略和阈值，不因当天收益改模型。

主档：核验单笔＋500ms；各券独立1万元、最多1张、零底仓，尾仓按买一计入。九账户合计对应9万元独立账户，非一个1万元共享模型。连续竞价09:30—11:30/13:00—14:57，无ETF对冲。

参照：**132024.SH 江铜EB，100分（人工确认参照）；132026.SH G三峡EB2，100分（人工确认参照）**。期权全部待评分，尚无可核验的专用综合评分。本轮双债不参与期权资金统计。

## 费用与路径要分开看

- 同样一买一卖，费用6元降到3.4元，省2.6元，下降43.33%；未成交挂撤单未计费用。
- 保持原3元主候选的成交路径，只重计费用：九券净值从{fmt(total('old_3cny_pnl'))}到{fmt(total('fixed_old_path_fee170_pnl'))}元，纯省费{total('fixed_old_path_fee_saving_cny'):.2f}元。这是代数诊断，不是新的模拟账户。
- 费用进入入场判断，按1.7元重新运行后净值为{fmt(total('pnl_cny'))}元；相对仅改账务的旧路径，新增/改变路径影响{fmt(total('rerun_minus_fixed_old_path_cny'))}元。低费用会放行更多空间不足以通过旧门槛的机会，不能只在旧结果上减费用后宣称策略收益。
- 1.7元重跑的主候选毛值含尾仓{fmt(total('gross_including_tail_cny'))}元、费用{total('fees_cny'):.2f}元、完整回合{int(total('complete_cycles'))}、实际成交单边{int(total('filled_contract_sides'))}张次；净值恒等于毛值减费用。毛值很小或为负时，不用“费用/净利润”计算容易误导的占比。

## 全名单（元）

{main_table}

## 执行敏感性（九个独立账户的描述性合计）

{modes}

单筆/延迟/排队都是独立快照撮合情景，非真实成交证明或收益上下界；延迟仍可能到达跨价。降低费用不解决真实竞争、队列和延迟问题。该日已经用于研究，不能称为样本外。

## 每层过滤重新按1.7元计算（500ms、元）

{ablation}

原版control也按1.7元重新计现金。spread、trend、gap、entry、risk、entry5/entry15的规则沿冻结v2，不改变10/5/15元门槛或300秒/5秒风险参数。每份账户ID以f170结尾；旧f300等账户保持。300秒主动退出仍仅研究，不因新费率自动采用。

## 核验与复现

360个账户逐笔资金、库存、费用、尾仓及主动深度审计通过；45个control与原v1同1.7元重新运行的订单/成交/曲线完全相同，288个历史截断前缀一致；旧1103份归档哈希保持，旧引擎/加载器哈希不变。另用原独立曲线审计脚本重建所有新账户全帧现金和库存；详见本地`independent_curve_audit.json`。本轮未改交易引擎，因此复用已通过的38项原聚焦测试，新增费用以实际账本恒等式验证。

复跑：`scripts/probe_option_fee170.py --output <新目录>`；报告：`scripts/render_option_fee170.py --input <该目录>`；独立曲线核验：`scripts/verify_option_guard_curves.py --input <该目录>`。本地归档`债券活跃度观察/期权做市研究/大道至简过滤_20260909_v2_f170/`保留360份完整账户、matrix/prerun合同、报告和HTML、验证及哈希。旧1.7计费路径与新1.7重跑路径不能混在一起。

新增的用户信息只有“暂按每张单边1.7元”；没有新增交易原则、没有债券模型或实时矩阵变更。费率包含范围仍待交割单核验。
'''
    (out/'费用复算报告.md').write_text(md,encoding='utf-8')
    runs=[];diagnosis=[]
    details=json.loads((ARCHIVE/'options_market_20260909_snapshot.json').read_text(encoding='utf-8'))['details']
    for c in NAMES:
        events,_=load(c,details[c])
        for m in MODES:
            for p in PROFILES:
                r=json.load(gzip.open(out/f'{c}_{p}_{m}_q1_f170.json.gz','rt',encoding='utf-8'))
                runs.append(dict(code=c,mode=m,profile=p,curve=thin(r['curve']),fills=r['fills']))
                if p=='control' and m in ['improve_l1','improve_single_d500']:
                    d=analyze(events,r)
                    # Only fee-independent midpoint decomposition and actual-fee duration buckets are used.
                    diagnosis.append({k:d[k] for k in ['code','mode','summary','decomposition','worst']}|{
                        'groups':{k:v for k,v in d['groups'].items() if k.startswith('hold_')}})
    for s in S:
        b=get(s['code'],'control',s['mode'])
        s['delta_pnl_cny']=round(s['pnl_cny']-b['pnl_cny'],2)
        s['delta_gross_cny']=round(s['gross_including_tail_cny']-b['gross_including_tail_cny'],2)
        s['saved_fees_cny']=round(b['fees_cny']-s['fees_cny'],2)
    data=dict(summaries=S+old['summaries'],runs=runs,diagnosis=diagnosis,profiles=list(PROFILES),labels=LABELS,
              modes=list(MODES),modeLabels=MODE_LABELS,books=json.loads((ARCHIVE/'盘口曲线_20260909/绘图数据.json').read_text(encoding='utf-8')))
    (out/'report_data.js').write_text('const DATA='+json.dumps(data,ensure_ascii=False,separators=(',',':'))+';',encoding='utf-8')
    page=(OLD/'过滤对比.html').read_text(encoding='utf-8')
    start=page.index('<div class="panel callout">');end=page.index('</div>',start)+6
    callout=f'''<div class="panel callout"><b>费用暂按每张单边1.7元，往返3.4元。</b><p>原3元主候选的成交完全不变时，500ms九账户净值从{fmt(total('old_3cny_pnl'))}变为{fmt(total('fixed_old_path_fee170_pnl'))}元；连同过滤门槛一起按1.7元重跑，结果为{fmt(total('pnl_cny'))}元。下表把纯省费与交易路径变化分开。</p><small>1.7元来自你本次回忆，先作为暂定总费用。每券独立1万元/1张，九券对应9万元，收益含尾仓；此前3元是额外假设。今天仍是开发样本。</small></div>'''
    page=page[:start]+callout+page[end:]
    replacements={
        '期权做市 · 亏损诊断与过滤试验':'期权做市 · 单边1.7元复算',
        '<option value="3">单边3元</option>':'<option value="1.7" selected>单边1.7元（暂定）</option><option value="3">单边3元（旧假设）</option>',
        '图表固定单边3元':'图表固定单边1.7元',
        "m=$('mode').value,f=3":"m=$('mode').value,f=1.7",
        '<h2>看清每次买卖与资金变化</h2>':'<h2>费用下降：省费与路径变化</h2><p id="fee-label"></p><div id="fee-compare" class="panel scroll"></div><h2>看清每次买卖与资金变化（1.7元）</h2>',
        'function drawTable(){':'function drawOriginalTable(){',
        "$('profile').onchange=draw":"$('profile').onchange=()=>{drawTable();draw()}",
        '过滤研究报告.md':'费用复算报告.md',
        '1080账户':'360个新增账户',
        '完整报告：诊断、各层、费用与执行表':'完整报告：1.7元费用与路径拆分',
        '选择原L1或单笔500ms':'选择原L1或单笔500ms',
    }
    for a,b in replacements.items():
        assert a in page,a
        page=page.replace(a,b)
    extra='''function drawTable(){drawOriginalTable();let p=$('profile').value,m=$('mode').value;$('fee-label').innerText=DATA.labels[p]+' ／ '+DATA.modeLabels[DATA.modes.indexOf(m)]+'，下表固定对比原3元与新1.7元（单位：元）。旧路径重计仅是代数拆分。';let ks=['old_3cny_pnl','fixed_old_path_fee170_pnl','pnl_cny','rerun_minus_fixed_old_path_cny','fees_cny'];let rows=DATA.books.map(b=>{let s=summary(b.code,p);return '<tr><td>'+b.code+'<br><small>待评分</small></td>'+ks.map(k=>'<td>'+colored(s[k])+'</td>').join('')+'<td>'+s.old_3cny_complete_cycles+' → '+s.complete_cycles+'</td></tr>'});$('fee-compare').innerHTML='<table><thead><tr><th>代码／评分</th><th>旧3元净值</th><th>旧路径只换1.7元</th><th>1.7元重跑净值</th><th>路径额外影响</th><th>重跑费用</th><th>旧→新轮次</th></tr></thead><tbody>'+rows.join('')+'</tbody></table>'}
'''
    page=page.replace('drawTable();draw();\n</script>',extra+'drawTable();draw();\n</script>')
    assert 'function drawTable(){drawOriginalTable()' in page
    (out/'过滤对比.html').write_text(page,encoding='utf-8')
    (out/'html_syntax_check.js').write_text(page.rsplit('<script>',1)[1].split('</script>',1)[0],encoding='utf-8')
    print('Rendered fee170 report',len(runs),'graph paths',flush=True)


if __name__=='__main__':main()
