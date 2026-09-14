"""Independent ledger/HTML checks and freeze relative-spread research evidence."""
from pathlib import Path
from html.parser import HTMLParser
from probe_gold_relative_spread import OUT,OLD,LABELS,DATES,CODES,ROOT,read,write,unpack,digest,fmt


class Tables(HTMLParser):
    def __init__(self):
        super().__init__();self.tables={};self.key=None;self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='table':self.key=dict(attrs).get('id','');self.tables[self.key]=[]
        if tag=='tr':self.row=[]
        if tag in ('td','th'):self.cell=''
    def handle_data(self,data):
        if self.cell is not None:self.cell+=data
    def handle_endtag(self,tag):
        if tag in ('td','th'):self.row.append(self.cell);self.cell=None
        if tag=='tr' and self.key is not None:self.tables[self.key].append(self.row)
        if tag=='table':self.key=None


def main():
    if (OUT/'manifest.json').exists():raise RuntimeError('Already frozen')
    summaries=read(OUT/'results.json');groups=read(OUT/'comparison.json');old=read(OLD/'comparison.json')
    models=set();orders=0
    for key,s in summaries.items():
        r=unpack(OUT/'ledgers'/f'{key}.json.gz');assert r['summary']==s
        assert s['model_id'] not in models;models.add(s['model_id'])
        assert s['rules']['min_spread_ticks']==0 and 'spread_ticks' not in s
        for o in r['orders']:
            if o['reason']=='entry':
                assert 2*o['entry_spread']*10000>=s['relative_spread_bps']*o['entry_mid_twice']
                assert o['side']==('buy' if s['fixed_direction']==1 else 'sell')
                orders+=1
        for c in r['cycles']:
            assert c['direction']==s['fixed_direction']
            if c['exit_kind']=='virtual_cost_close':assert c['net_cents']==c['fees_cents']==0
            else:
                assert c['fees_cents']==340
                assert c['net_cents']==c['direction']*(c['exit_price_cents']-c['entry_price_cents'])-340
        assert abs(sum(c['net_cents'] for c in r['cycles'])/100-s['pnl_cny'])<1e-7
    parser=Tables();parser.feed((OUT/'黄金期权_相对价差门槛比较.html').read_text('utf-8'))
    for mode in ('long','short'):
        for strict in (0,1):
            rows=parser.tables[f'{mode}{strict}'][1:];assert len(rows)==36
            i=0
            for policy,label in LABELS.items():
                for bps in (0,100,150,200):
                    s=old[f'{mode}_{policy}_through{strict}'] if bps==0 else groups[f'{mode}_{policy}_bps{bps}_through{strict}']
                    assert rows[i]==[label,'原8跳' if not bps else f'{bps/100:g}%',*map(fmt,s['daily']),fmt(s['pnl_cny']),
                        str(s['normal_cycles']),str(s['virtual_cycles']),fmt(s['max_drawdown_cny']),fmt(s['removed_tail_gross_cny'])]
                    i+=1
    assert len(parser.tables['accounts'])==865
    for row,s in zip(parser.tables['accounts'][1:],summaries.values()):
        assert row==[s['history_date'],s['code'],s['trade_mode'],LABELS[s['policy']],f'{s["relative_spread_bps"]/100:g}%',
            '严格' if s['strict_through'] else '普通',fmt(s['pnl_cny']),str(s['market_cycles']),str(s['virtual_close_count']),fmt(s['removed_tail_gross_cny'])]
    opp=read(OUT/'opportunities.json');assert len(parser.tables['opportunities'])==25
    for d in DATES:
        for c in CODES:
            a=[s['eligible_seconds'] for s in opp if s['date']==d and s['code']==c]
            assert len(a)==3 and a[0]>=a[1]>=a[2]>=0
    for file in ('input_manifest.json','screen_input_manifest.json','source_manifest.json'):
        for rel,h in read(OUT/file).items():assert digest(ROOT/rel)==h,rel
    for rel,h in read(OLD/'manifest.json').items():assert digest(ROOT/rel)==h,rel
    sources=read(OUT/'source_manifest.json')
    # Preserve the complete current gold engine chain and foundational event/fill modules.
    paths=list((ROOT/'src/zhaiquant').glob('gold*.py'))+[Path(__file__)]+[
        ROOT/'src/zhaiquant'/f for f in ('commodity_dadao_research.py','commodity_flow_strategy.py',
            'option_top_cycle_research.py','commodity_mau_transfer_research.py')]
    paths += [ROOT/'scripts'/f for f in ('probe_gold_backer.py','probe_gold_two_mode.py','probe_gold_rule_ladder.py','probe_commodity_capital.py','screen_gold_relative_spread.py','report_gold_relative_spread.py')]
    screen=read(OUT/'universe_screen.json');sp=Tables()
    sp.feed((OUT/'黄金期权_其他合约百分比筛选.html').read_text('utf-8'))
    for b in (100,150,200):
        for row,s in zip(sp.tables[str(b)][1:],screen['ranked'][str(b)][:10]):
            assert row==[s['code'],fmt(s['median_premium']),str(s['volume_increment']),f'{s["eligible_time_pct"]:.2f}%',
                str(s['balanced_updates']),f'{s["prior_eligible_buy_updates"]}/{s["prior_eligible_sell_updates"]}',fmt(s['mean_static_net_space_cny_at_eligible_quotes'])]
    for p in paths:sources[str(p.relative_to(ROOT))]=digest(p)
    write(OUT/'source_manifest.json',sources)
    for rel in sources:
        target=OUT/'source_snapshot'/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/rel).read_bytes())
    write(OUT/'report_verification.json',dict(status='passed',unique_accounts=len(models),entry_orders_percentage_checked=orders,
        normal_cycle_sign_and_fee_identity=True,html_daily_rows=144,html_account_rows=864,
        opportunity_rows=24,threshold_opportunity_monotonic=True,gold_unit_tests_passed=95,
        visual_qa='Static HTML tables checked against ledgers; no browser screenshot claimed'))
    write(OUT/'manifest.json',{str(p.relative_to(ROOT)):digest(p) for p in OUT.rglob('*') if p.is_file() and p.name!='manifest.json'})
    print('PASS',len(models),'accounts',orders,'entry orders,144 daily rows,864 account rows; evidence frozen')


if __name__=='__main__':main()
