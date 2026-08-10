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
