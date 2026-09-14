"""Audit floating-point loss of a price tick without changing replay behavior."""
from __future__ import annotations

import inspect
import json
import sys
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from unittest.mock import patch

from zhaiquant import maker_paper
from replay_priority_entry_quality import main


def run():
    original = maker_paper._floor_to_tick
    events = []

    def audited(value, price_tick, **kwargs):
        actual = original(value, price_tick, **kwargs)
        step = Decimal(str(price_tick))
        nearest = float((Decimal(str(value))/step).to_integral_value(rounding=ROUND_HALF_EVEN)*step)
        if abs(value-nearest) < 1e-9 and nearest-actual > price_tick*.9:
            frame = inspect.currentframe().f_back
            local = frame.f_locals
            account, tick = local.get('account'), local.get('tick')
            if account is not None and tick is not None and frame.f_code.co_name == '_refresh_orders':
                events.append(dict(date=tick.market_date, code=tick.code, time=tick.market_time,
                    model=account.policy.model_id, line=frame.f_lineno,
                    raw=value, actual=actual, grid_price=nearest,
                    bid=tick.bid1, ask=tick.ask1,
                    desired_sell_kind=local.get('desired_sell_kind'),
                    desired_buy_kind=local.get('desired_buy_kind'),
                    inventory=account.inventory))
            del frame
        return actual

    with patch.object(maker_paper, '_floor_to_tick', audited):
        main()
    output = Path(sys.argv[sys.argv.index('--output')+1])
    data = json.loads(output.read_text(encoding='utf-8'))
    data['price_grid_audit'] = events
    output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Price-grid rounding events:',len(events),flush=True)


if __name__ == '__main__':
    run()
