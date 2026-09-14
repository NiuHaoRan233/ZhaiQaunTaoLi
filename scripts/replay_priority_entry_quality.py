"""Read-only first-position entry-quality experiments with frozen baseline checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

from zhaiquant.config import load_config, maker_underlying_stock_code
from zhaiquant.database import SQLiteStore
from zhaiquant.maker import _load_ticks
from zhaiquant.maker_paper import MakerPaperEngine, REALTIME_COMPARISON_POLICIES
from replay_priority_candidate_matrix import _inventory_exposure_metrics, _inventory_and_cash_path, _customer_base_short_path


class EntryAuditEngine(MakerPaperEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.blocked_frames = []
        self.audit_fills = []

    def _record_fill(self, account, tick, order, lot_id, side, price, quantity, reason, received_ts_ns):
        super()._record_fill(account, tick, order, lot_id, side, price, quantity, reason, received_ts_ns)
        self.audit_fills.append(dict(market_time=tick.market_time[:8], side=side,
            price=price, quantity_bonds=quantity, fill_reason=reason,
            inventory_after_bonds=account.inventory))

    def _ordinary_entry_sell_pressure_blocks(self, account, tick, assessment, context, **kwargs):
        blocked = super()._ordinary_entry_sell_pressure_blocks(account, tick, assessment, context, **kwargs)
        if blocked:
            after_exit = tuple(event for event in self.analyzer.trade_evidence
                if account.last_ordinary_risk_exit_ts_ms < event.market_ts_ms <= tick.market_ts_ms)
            self.blocked_frames.append(dict(time=tick.market_time, timestamp=tick.market_ts_ms,
                last_risk_exit_timestamp=account.last_ordinary_risk_exit_ts_ms,
                last_new_extra_entry_timestamp=account.last_new_extra_entry_ts_ms,
                post_exit_buy_bonds=sum(e.bonds for e in after_exit if e.side == 'buy'),
                post_exit_sell_bonds=sum(e.bonds for e in after_exit if e.side == 'sell'),
                post_exit_unknown_bonds=sum(e.bonds for e in after_exit if e.side == 'unknown'),
                post_exit_trades=[dict(timestamp=e.market_ts_ms,price=e.price,bonds=e.bonds,side=e.side)
                                  for e in after_exit],
                inventory=account.inventory, bid=tick.bid1, ask=tick.ask1,
                bids=tick.bids, asks=tick.asks, proposed=kwargs,
                state=assessment.state, short_ask_change=assessment.short_ask_change,
                recent_buy_bonds=assessment.recent_buy_bonds,
                recent_sell_bonds=assessment.recent_sell_bonds,
                context=asdict(context), preceding_fill_count=account.fills))
        return blocked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='config.toml')
    parser.add_argument('--baseline', default='output/research/priority_v265_matrix_20260804_20260904.json')
    parser.add_argument('--models', nargs='+', default=['maker_priority_v2_65_candidate',
        'maker_priority_v2_66_candidate', 'maker_priority_v2_66_candidate_r2'])
    parser.add_argument('--dates', nargs='*')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    baseline_path = Path(args.baseline)
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    selected = [c for c in baseline['cells'] if not args.dates or c['market_date'] in args.dates]
    results = []
    with sqlite3.connect(f'file:{config.storage.database.resolve().as_posix()}?mode=ro', uri=True) as source:
        source.row_factory = sqlite3.Row
        for cell in selected:
            day, code = cell['market_date'], cell['bond_code']
            variants = {}
            ticks = None
            for mid in args.models:
                policy = REALTIME_COMPARISON_POLICIES[mid]
                with tempfile.TemporaryDirectory() as temp:
                    isolated = replace(config, storage=replace(config.storage,
                        database=Path(temp)/'paper.sqlite3'))
                    store = SQLiteStore(isolated)
                    store.start_session()
                    try:
                        engine = EntryAuditEngine(isolated, store, bond_code=code,
                            priority_policy=policy, fill_modes=('priority',),
                            include_windfall=False)
                        if ticks is None:
                            ticks = tuple(_load_ticks(source, day, code,
                                maker_underlying_stock_code(config, code), engine.parameters))
                        for tick in ticks:
                            engine.on_replay_tick(tick, persist=False)
                        account = next(iter(engine.accounts.values()))
                        fills = engine.audit_fills
                        data = {key:getattr(account,key) for key in ['initial_inventory',
                            'initial_cash','inventory','cash','trading_pnl']}
                        variants[mid] = dict(account=data, fills=fills,
                            path_bounds=_inventory_and_cash_path(data,fills),
                            base_short_path=_customer_base_short_path(data,fills),
                            short_metrics=_inventory_exposure_metrics(data,fills,direction='short'),
                            extra_metrics=_inventory_exposure_metrics(data,fills,direction='extra'),
                            blocked_frames=engine.blocked_frames)
                        if mid == baseline['candidate_model_id']:
                            if fills != cell['candidate_fills']:
                                first = next(((a,b) for a,b in zip(fills,cell['candidate_fills']) if a!=b), None)
                                raise AssertionError((day,code,len(fills),len(cell['candidate_fills']),first))
                            assert abs(data['trading_pnl']-cell['candidate_trading_pnl'])<1e-7
                            assert data['inventory']==cell['candidate_terminal_inventory']
                    finally:
                        store.close()
            results.append(dict(date=day,code=code,variants=variants))
            print(day, code, ' '.join(f"{mid}: {v['account']['trading_pnl']:.2f} ({len(v['fills'])})"
                                     for mid,v in variants.items()), flush=True)
    payload = dict(source_readonly=True, baseline_sha256=hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
        profiles={mid:asdict(REALTIME_COMPARISON_POLICIES[mid]) for mid in args.models},
        cells=results)
    output = Path(args.output)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
