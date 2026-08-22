from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .config import ConfigError, load_config
from .database import SQLiteStore
from .qmt_feed import QmtFeed
from .runner import LiveRunner, MarketProcessor, configure_logging


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="zhaiquant",
        description="MiniQMT tick recorder and M0 paper-trading engine",
    )
    parser.add_argument("--config", default="config.toml", help="TOML configuration path")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init-config", help="Create config.toml from config.example.toml")
    commands.add_parser("doctor", help="Check configuration, database and MiniQMT connectivity")
    commands.add_parser("snapshot", help="Store one current snapshot for all configured instruments")
    run = commands.add_parser("run", help="Record live ticks and run paper strategies")
    run.add_argument(
        "--duration-seconds", type=float, default=None,
        help="Stop automatically after N seconds; useful for a smoke test",
    )
    status = commands.add_parser("status", help="Show database collection and paper status")
    status.add_argument("--date", default=None, help="Market date in YYYY-MM-DD")
    maker = commands.add_parser(
        "maker-report",
        help="Replay recorded Level 1 data through the maker V0.1 research model",
    )
    maker.add_argument("--date", default=None, help="Market date in YYYY-MM-DD; default latest")
    maker.add_argument("--output", default=None, help="Optional JSON report destination")
    tape = commands.add_parser(
        "tdx-extract-trades",
        help="OCR verified 通达信 full-day 逐笔成交 screenshots",
    )
    tape.add_argument("--input-dir", required=True, help="Directory containing 逐笔成交 PNG pages")
    tape.add_argument("--date", required=True, help="Actual market date in YYYY-MM-DD")
    tape.add_argument("--code", required=True, help="Security code, for example 132026.SH")
    tape.add_argument("--output-dir", required=True, help="Ignored local structured-data directory")
    orders = commands.add_parser(
        "tdx-extract-orders",
        help="OCR verified 通达信 full-day 逐笔委托 screenshots",
    )
    orders.add_argument("--input-dir", required=True, help="Directory containing 逐笔委托 PNG pages")
    orders.add_argument("--date", required=True, help="Actual market date in YYYY-MM-DD")
    orders.add_argument("--code", required=True, help="Security code, for example 132026.SH")
    orders.add_argument("--output-dir", required=True, help="Ignored local structured-data directory")
    audit = commands.add_parser(
        "tdx-opportunity-report",
        help="Enumerate every positive ordered B/S tape pair and readable local turns",
    )
    audit.add_argument("--trades", required=True, help="Deduplicated TDX trade CSV")
    audit.add_argument(
        "--reviews", default=None,
        help="Optional manually verified correction sidecar for review_required rows",
    )
    audit.add_argument("--output", required=True, help="JSON market-opportunity report")
    inventory_path = commands.add_parser(
        "tdx-inventory-path",
        help="Write a non-reusing hindsight inventory-path liquidity ceiling",
    )
    inventory_path.add_argument("--trades", required=True, help="Deduplicated TDX trade CSV")
    inventory_path.add_argument(
        "--reviews", default=None,
        help="Optional manually verified correction sidecar for review_required rows",
    )
    inventory_path.add_argument("--output", required=True, help="JSON inventory-path report")
    inventory_path.add_argument(
        "--initial-hands", type=int, default=100,
        help="Opening base inventory in hands; default 100 hands = 1,000 bonds",
    )
    inventory_path.add_argument(
        "--maximum-hands", type=int, default=200,
        help="Maximum inventory in hands; default 200 hands = 2,000 bonds",
    )
    inventory_path.add_argument(
        "--terminal-hands", type=int, default=None,
        help=(
            "Optional required closing inventory in hands; default leaves the "
            "terminal exposure unconstrained and marks it at the last tape price"
        ),
    )
    queue_audit = commands.add_parser(
        "maker-queue-audit",
        help="Align queue-model orders with reviewed TDX trades and order events",
    )
    queue_audit.add_argument("--trades", required=True, help="Deduplicated TDX trade CSV")
    queue_audit.add_argument(
        "--trade-reviews", default=None,
        help="Optional manually verified trade correction sidecar",
    )
    queue_audit.add_argument("--orders", required=True, help="Deduplicated TDX order-event CSV")
    queue_audit.add_argument(
        "--order-reviews", default=None,
        help="Optional manually verified order-event correction sidecar",
    )
    queue_audit.add_argument("--date", required=True, help="Market date in YYYY-MM-DD")
    queue_audit.add_argument("--code", required=True, help="Maker bond code")
    queue_audit.add_argument("--output", required=True, help="JSON queue audit report")
    compare = commands.add_parser(
        "maker-opportunity-audit",
        help="Read-only replay registered maker models against a TDX opportunity report",
    )
    compare.add_argument("--opportunities", required=True, help="TDX market-opportunity JSON")
    compare.add_argument("--date", required=True, help="Market date in YYYY-MM-DD")
    compare.add_argument("--code", required=True, help="Maker bond code")
    compare.add_argument("--output", required=True, help="JSON audit report")
    dashboard = commands.add_parser(
        "maker-console",
        aliases=["maker-dashboard"],
        help="Show the live, read-only maker paper-trading dashboard",
    )
    dashboard.add_argument("--date", default=None, help="Market date in YYYY-MM-DD; default today")
    dashboard.add_argument(
        "--bond-code", default=None,
        help="Maker bond code; default qmt.bond_code",
    )
    dashboard.add_argument(
        "--interval", type=float, default=60.0,
        help="Maximum visible refresh interval without a fill; default 60 seconds",
    )
    dashboard.add_argument("--recent-fills", type=int, default=16, help="Number of recent fills to show")
    dashboard.add_argument("--once", action="store_true", help="Print one dashboard snapshot and exit")
    backup = commands.add_parser("backup", help="Create a transactionally consistent SQLite backup")
    backup.add_argument("--output", required=True, help="Destination .sqlite3 path")
    backfill = commands.add_parser("backfill", help="Download and store recent QMT tick history without paper fills")
    backfill.add_argument("--days", type=int, default=7, help="Calendar lookback days, default 7")
    backfill.add_argument("--start", default=None, help="Explicit start date YYYYMMDD")
    backfill.add_argument("--end", default=None, help="Explicit end date YYYYMMDD, default today")
    backfill.add_argument("--no-download", action="store_true", help="Read only already-cached QMT history")
    return parser


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def init_config(target: Path) -> int:
    if target.exists():
        print(f"Configuration already exists: {target}", file=sys.stderr)
        return 2
    source = Path(__file__).resolve().parents[2] / "config.example.toml"
    if not source.exists():
        source = Path.cwd() / "config.example.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    print(f"Created {target}")
    return 0


def doctor(config_path: str) -> int:
    config = load_config(config_path)
    store = SQLiteStore(config)
    feed = QmtFeed(config)
    result = {
        "config": str(config.path),
        "database": str(config.storage.database),
        "database_schema": "ok",
        "qmt_port": config.qmt.port,
        "codes": list(feed.codes),
        "qmt_connected": False,
        "snapshots": [],
    }
    try:
        feed.connect()
        result["qmt_connected"] = feed.is_connected()
        for tick in feed.snapshot():
            result["snapshots"].append({
                "code": tick.code,
                "market_time": tick.market_datetime.isoformat(),
                "last_price": tick.last_price,
                "bid1": tick.bid1,
                "ask1": tick.ask1,
                "valid_book": tick.valid_book,
            })
    finally:
        feed.close()
        store.close()
    _print(result)
    return 0 if result["qmt_connected"] and len(result["snapshots"]) == len(feed.codes) else 1


def snapshot(config_path: str) -> int:
    config = load_config(config_path)
    store = SQLiteStore(config)
    feed = QmtFeed(config)
    store.start_session()
    try:
        feed.connect()
        processor = MarketProcessor(config, store, enable_paper=False)
        output = []
        for tick in feed.snapshot():
            recorded, observation = processor.process(tick)
            output.append({
                "tick_id": recorded.tick_id,
                "code": tick.code,
                "market_time": tick.market_datetime.isoformat(),
                "last_price": tick.last_price,
                "bid1": tick.bid1,
                "ask1": tick.ask1,
                "m0_observation_id": observation.observation_id if observation else None,
                "m0_warmup_count": observation.warmup_count if observation else None,
            })
        store.end_session("snapshot")
        _print(output)
        return 0
    except Exception:
        store.end_session("failed")
        raise
    finally:
        feed.close()
        store.close()


def backfill(config_path: str, *, days: int, start: str | None, end: str | None, download: bool) -> int:
    from xtquant import xtdata

    config = load_config(config_path)
    end_date = datetime.strptime(end, "%Y%m%d").date() if end else datetime.now().date()
    start_date = datetime.strptime(start, "%Y%m%d").date() if start else end_date - timedelta(days=days)
    start_text = start_date.strftime("%Y%m%d")
    end_text = end_date.strftime("%Y%m%d")
    xtdata.enable_hello = False
    xtdata.connect(port=config.qmt.port)
    codes = QmtFeed(config).codes
    if download:
        for code in codes:
            xtdata.download_history_data(code, "tick", start_time=start_text, end_time=end_text)
    frames = xtdata.get_market_data_ex(
        [], list(codes), period="tick",
        start_time=start_text, end_time=end_text, count=-1,
        dividend_type="none", fill_data=False,
    )

    from .types import Tick

    ticks = []
    code_priority = {code: index for index, code in enumerate(codes)}
    for code, frame in frames.items():
        for _, row in frame.iterrows():
            payload = row.to_dict()
            market_ms = int(payload.get("time", 0))
            ticks.append(Tick.from_qmt(code, payload, market_ms * 1_000_000))
    ticks.sort(key=lambda item: (item.market_ts_ms, code_priority.get(item.code, 9)))

    store = SQLiteStore(config)
    store.start_session()
    processor = MarketProcessor(
        config, store, enable_paper=False, deduplicate_ticks=True,
        preload_m0_history=False, synchronize_m0=True,
    )
    inserted = skipped = 0
    try:
        for tick in ticks:
            if store.tick_exists(tick):
                skipped += 1
            else:
                inserted += 1
            processor.process(tick)
        store.app_event(
            "info", "backfill_completed", "Historical tick backfill completed",
            {"start": start_text, "end": end_text, "inserted": inserted, "skipped": skipped},
        )
        store.end_session("backfill")
    except Exception:
        store.end_session("failed")
        raise
    finally:
        store.close()
    _print({
        "start": start_text,
        "end": end_text,
        "download": download,
        "source_rows": len(ticks),
        "inserted": inserted,
        "skipped_existing": skipped,
        "database": str(config.storage.database),
    })
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config_path = Path(args.config).expanduser().resolve()
    try:
        if args.command == "init-config":
            return init_config(config_path)
        config = load_config(config_path)
        configure_logging(config, args.verbose)
        if args.command == "doctor":
            return doctor(str(config.path))
        if args.command == "snapshot":
            return snapshot(str(config.path))
        if args.command == "backfill":
            return backfill(
                str(config.path), days=args.days, start=args.start, end=args.end,
                download=not args.no_download,
            )
        if args.command == "run":
            LiveRunner(config).run(duration_seconds=args.duration_seconds)
            return 0
        if args.command == "maker-report":
            from .maker import generate_maker_report, write_report

            report = generate_maker_report(
                config.storage.database,
                args.date,
                config.qmt.bond_code,
                config.qmt.stock_code,
            )
            if args.output:
                destination = Path(args.output).expanduser().resolve()
                write_report(report, destination)
                _print({
                    "report": str(destination),
                    "date": report["date"],
                    "summary": report["summary"],
                })
            else:
                _print(report)
            return 0
        if args.command == "tdx-extract-trades":
            from .tdx_tape import extract_trade_screenshots, write_trade_extraction

            raw, deduplicated, summary = extract_trade_screenshots(
                Path(args.input_dir).expanduser().resolve(),
                market_date=args.date,
                code=args.code,
            )
            output_dir = Path(args.output_dir).expanduser().resolve()
            write_trade_extraction(output_dir, raw, deduplicated, summary)
            _print({"output_dir": str(output_dir), **summary})
            return 0
        if args.command == "tdx-extract-orders":
            from .tdx_tape import extract_order_screenshots, write_order_extraction

            raw, deduplicated, summary = extract_order_screenshots(
                Path(args.input_dir).expanduser().resolve(),
                market_date=args.date,
                code=args.code,
            )
            output_dir = Path(args.output_dir).expanduser().resolve()
            write_order_extraction(output_dir, raw, deduplicated, summary)
            _print({"output_dir": str(output_dir), **summary})
            return 0
        if args.command == "tdx-opportunity-report":
            from .opportunity_audit import (
                apply_manual_trade_reviews,
                discover_theoretical_pairs,
                load_tdx_trades,
                summarize_local_turns,
                write_opportunity_report,
            )

            trades_path = Path(args.trades).expanduser().resolve()
            trades = load_tdx_trades(trades_path)
            reviews_path = (
                Path(args.reviews).expanduser().resolve() if args.reviews else None
            )
            manual_review_rows = 0
            if reviews_path is not None:
                trades, manual_review_rows = apply_manual_trade_reviews(
                    trades, reviews_path,
                )
            pairs = discover_theoretical_pairs(trades)
            local_turns = summarize_local_turns(trades, pairs)
            destination = Path(args.output).expanduser().resolve()
            outputs = write_opportunity_report(
                destination,
                trades_path=trades_path,
                pairs=pairs,
                local_turns=local_turns,
                excluded_review_rows=sum(item.review_required for item in trades),
                manual_review_rows=manual_review_rows,
                manual_reviews_path=reviews_path,
            )
            _print({
                **outputs,
                "theoretical_pairs": len(pairs),
                "buy_then_sell_pairs": sum(
                    item.direction == "buy_then_sell" for item in pairs
                ),
                "sell_then_buy_pairs": sum(
                    item.direction == "sell_then_buy" for item in pairs
                ),
                "local_turns": len(local_turns),
                "minimum_edge": None,
                "manual_review_rows": manual_review_rows,
            })
            return 0
        if args.command == "tdx-inventory-path":
            from .opportunity_audit import (
                apply_manual_trade_reviews,
                load_tdx_trades,
                optimize_nonoverlapping_inventory_path,
                write_inventory_path_report,
            )

            trades_path = Path(args.trades).expanduser().resolve()
            trades = load_tdx_trades(trades_path)
            reviews_path = (
                Path(args.reviews).expanduser().resolve() if args.reviews else None
            )
            manual_review_rows = 0
            if reviews_path is not None:
                trades, manual_review_rows = apply_manual_trade_reviews(
                    trades, reviews_path,
                )
            inventory_path = optimize_nonoverlapping_inventory_path(
                trades,
                initial_inventory_hands=args.initial_hands,
                maximum_inventory_hands=args.maximum_hands,
                terminal_inventory_hands=args.terminal_hands,
            )
            destination = Path(args.output).expanduser().resolve()
            outputs = write_inventory_path_report(
                destination,
                trades_path=trades_path,
                inventory_path=inventory_path,
                manual_review_rows=manual_review_rows,
                manual_reviews_path=reviews_path,
            )
            _print({
                **outputs,
                "gross_cash_profit": inventory_path.gross_cash_profit,
                "buy_hands": inventory_path.buy_hands,
                "sell_hands": inventory_path.sell_hands,
                "actions": len(inventory_path.actions),
                "causal_signal": False,
            })
            return 0
        if args.command == "maker-queue-audit":
            from .opportunity_audit import (
                apply_manual_trade_reviews,
                audit_queue_orders,
                load_tdx_trades,
                replay_registered_models_readonly,
                write_queue_order_audit,
            )
            from .tdx_tape import apply_manual_order_reviews, load_order_events

            trades_path = Path(args.trades).expanduser().resolve()
            trades = load_tdx_trades(trades_path)
            trade_reviews_path = (
                Path(args.trade_reviews).expanduser().resolve()
                if args.trade_reviews else None
            )
            trade_manual_review_rows = 0
            if trade_reviews_path is not None:
                trades, trade_manual_review_rows = apply_manual_trade_reviews(
                    trades, trade_reviews_path,
                )
            order_events_path = Path(args.orders).expanduser().resolve()
            order_events = load_order_events(order_events_path)
            order_reviews_path = (
                Path(args.order_reviews).expanduser().resolve()
                if args.order_reviews else None
            )
            order_manual_review_rows = 0
            if order_reviews_path is not None:
                order_events, order_manual_review_rows = apply_manual_order_reviews(
                    order_events, order_reviews_path,
                )
            replay = replay_registered_models_readonly(
                config, market_date=args.date, bond_code=args.code,
            )
            audits = audit_queue_orders(replay, trades, order_events)
            destination = Path(args.output).expanduser().resolve()
            outputs = write_queue_order_audit(
                destination,
                trades_path=trades_path,
                order_events_path=order_events_path,
                replay=replay,
                audits=audits,
                trade_manual_review_rows=trade_manual_review_rows,
                order_manual_review_rows=order_manual_review_rows,
            )
            status_counts = {
                status: sum(item.execution_status == status for item in audits)
                for status in sorted({item.execution_status for item in audits})
            }
            _print({
                **outputs,
                "audited_queue_cohorts": len(audits),
                "audited_model_orders": sum(
                    item.cohort_order_count for item in audits
                ),
                "execution_statuses": status_counts,
                "layer_2_causal_mode_in_status": "not_evaluated",
                "live_database_mutated": False,
            })
            return 0
        if args.command == "maker-opportunity-audit":
            from .opportunity_audit import (
                compare_model_capture,
                load_opportunity_report,
                replay_registered_models_readonly,
                write_model_opportunity_audit,
            )

            opportunity_source = Path(args.opportunities).expanduser().resolve()
            opportunity_payload, opportunities = load_opportunity_report(
                opportunity_source
            )
            replay = replay_registered_models_readonly(
                config, market_date=args.date, bond_code=args.code,
            )
            comparisons = compare_model_capture(opportunities, replay)
            destination = Path(args.output).expanduser().resolve()
            write_model_opportunity_audit(
                destination,
                opportunity_source=opportunity_source,
                opportunity_definition=opportunity_payload["definition"],
                replay=replay,
                comparisons=comparisons,
            )
            captured = {
                strategy: sum(
                    branch["capture_status"] == "best_pair_both_legs_matched"
                    for item in comparisons
                    for branch in item["branch_results"]
                    if branch["strategy_id"] == strategy
                )
                for strategy in {
                    branch["strategy_id"]
                    for item in comparisons
                    for branch in item["branch_results"]
                }
            }
            _print({
                "report": str(destination),
                "local_turns": len(comparisons),
                "best_pair_both_legs_matched_by_strategy": captured,
                "accounts": replay["accounts"],
                "live_database_mutated": False,
            })
            return 0
        if args.command in {"maker-console", "maker-dashboard"}:
            from .maker import MakerParameters
            from .maker_dashboard import run_dashboard
            from .maker_paper import (
                configured_maker_bond_codes,
                maker_strategy_ids,
            )

            market_date = args.date or datetime.now().date().isoformat()
            bond_code = args.bond_code or config.qmt.bond_code
            if bond_code not in configured_maker_bond_codes(config):
                raise ConfigError(
                    f"Maker paper is not enabled for {bond_code}; configured: "
                    f"{list(configured_maker_bond_codes(config))}"
                )
            return run_dashboard(
                config.storage.database,
                bond_code,
                market_date,
                stock_code=config.qmt.stock_code,
                underlying_stock_codes=(
                    config.maker_paper.underlying_stock_codes
                ),
                parameters=MakerParameters(
                    order_quantity_bonds=(
                        config.maker_paper.order_quantity_bonds
                    ),
                    price_tick=config.maker_paper.price_tick,
                    earliest_entry_time=config.maker_paper.earliest_entry,
                    latest_entry_time=config.maker_paper.latest_entry,
                    opening_caution_effective_date=(
                        config.maker_paper.opening_caution_effective_date
                    ),
                    opening_caution_end_time=(
                        config.maker_paper.opening_caution_end
                    ),
                    opening_caution_minimum_edge=(
                        config.maker_paper.opening_caution_minimum_edge
                    ),
                ),
                bond_name=config.qmt.instrument_names.get(bond_code),
                interval_seconds=args.interval,
                once=args.once,
                recent_fills=args.recent_fills,
                strategy_ids=maker_strategy_ids(config, bond_code),
                follow_current_date=args.date is None,
            )
        store = SQLiteStore(config)
        try:
            if args.command == "status":
                _print(store.status_summary(args.date))
                return 0
            if args.command == "backup":
                destination = Path(args.output).expanduser().resolve()
                store.backup(destination)
                _print({"backup": str(destination), "created_at": datetime.now().isoformat()})
                return 0
        finally:
            store.close()
    except (ConfigError, ConnectionError, RuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
