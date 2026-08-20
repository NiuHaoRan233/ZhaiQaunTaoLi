from __future__ import annotations

import argparse
from pathlib import Path

from zhaiquant.opportunity_audit import (
    apply_manual_trade_reviews,
    load_tdx_trades,
)
from zhaiquant.tdx_tape import (
    _write_csv,
    _write_order_csv,
    _write_review_queue,
    apply_manual_order_reviews,
    load_order_events,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize manually reviewed TDX tape CSV")
    parser.add_argument("--kind", choices=("orders", "trades"), required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--remaining", type=Path, required=True)
    args = parser.parse_args()

    if args.kind == "orders":
        rows = load_order_events(args.input)
        reviewed, applied = apply_manual_order_reviews(rows, args.reviews)
        _write_order_csv(args.output, reviewed)
        _write_review_queue(args.remaining, reviewed, order=True)
    else:
        rows = load_tdx_trades(args.input)
        reviewed, applied = apply_manual_trade_reviews(rows, args.reviews)
        _write_csv(args.output, reviewed)
        _write_review_queue(args.remaining, reviewed, order=False)

    remaining = sum(row.review_required for row in reviewed)
    print(
        f"rows={len(reviewed)} applied={applied} remaining={remaining} "
        f"output={args.output}"
    )
    return 0 if remaining == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
