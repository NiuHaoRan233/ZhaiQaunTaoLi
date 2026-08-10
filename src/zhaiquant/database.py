from __future__ import annotations

import json
import platform
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .config import AppConfig
from .types import SHANGHAI, Tick, TickChange


SCHEMA_VERSION = 1


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_info (
    version INTEGER NOT NULL,
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    run_id TEXT PRIMARY KEY,
    started_at_utc TEXT NOT NULL,
    ended_at_utc TEXT,
    host_name TEXT NOT NULL,
    app_version TEXT NOT NULL,
    qmt_port INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL,
    dropped_callbacks INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS raw_ticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    code TEXT NOT NULL,
    market_ts_ms INTEGER NOT NULL,
    received_ts_ns INTEGER NOT NULL,
    market_date TEXT NOT NULL,
    market_time TEXT NOT NULL,
    last_price REAL NOT NULL,
    open_price REAL NOT NULL,
    high_price REAL NOT NULL,
    low_price REAL NOT NULL,
    previous_close REAL NOT NULL,
    amount REAL NOT NULL,
    volume REAL NOT NULL,
    pvolume REAL NOT NULL,
    tick_volume REAL NOT NULL,
    stock_status INTEGER NOT NULL,
    open_interest REAL NOT NULL,
    last_settlement_price REAL NOT NULL,
    settlement_price REAL NOT NULL,
    transaction_count INTEGER NOT NULL,
    pe REAL NOT NULL,
    ask_price_1 REAL NOT NULL, ask_price_2 REAL NOT NULL, ask_price_3 REAL NOT NULL,
    ask_price_4 REAL NOT NULL, ask_price_5 REAL NOT NULL,
    bid_price_1 REAL NOT NULL, bid_price_2 REAL NOT NULL, bid_price_3 REAL NOT NULL,
    bid_price_4 REAL NOT NULL, bid_price_5 REAL NOT NULL,
    ask_volume_1 REAL NOT NULL, ask_volume_2 REAL NOT NULL, ask_volume_3 REAL NOT NULL,
    ask_volume_4 REAL NOT NULL, ask_volume_5 REAL NOT NULL,
    bid_volume_1 REAL NOT NULL, bid_volume_2 REAL NOT NULL, bid_volume_3 REAL NOT NULL,
    bid_volume_4 REAL NOT NULL, bid_volume_5 REAL NOT NULL,
    snapshot_hash TEXT NOT NULL,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_raw_ticks_code_market ON raw_ticks(code, market_ts_ms, id);
CREATE INDEX IF NOT EXISTS idx_raw_ticks_date_code ON raw_ticks(market_date, code, id);
CREATE INDEX IF NOT EXISTS idx_raw_ticks_received ON raw_ticks(received_ts_ns);
CREATE UNIQUE INDEX IF NOT EXISTS idx_raw_ticks_unique_snapshot
ON raw_ticks(code, market_ts_ms, snapshot_hash);

CREATE TABLE IF NOT EXISTS tick_changes (
    tick_id INTEGER PRIMARY KEY,
    previous_tick_id INTEGER,
    code TEXT NOT NULL,
    market_ts_ms INTEGER NOT NULL,
    volume_delta REAL NOT NULL,
    amount_delta REAL NOT NULL,
    transaction_delta INTEGER NOT NULL,
    inferred_side TEXT NOT NULL,
    side_confidence TEXT NOT NULL,
    last_price_changed INTEGER NOT NULL,
    best_bid_changed INTEGER NOT NULL,
    best_ask_changed INTEGER NOT NULL,
    spread REAL NOT NULL,
    midpoint REAL NOT NULL,
    book_change_json TEXT NOT NULL,
    FOREIGN KEY(tick_id) REFERENCES raw_ticks(id)
);
CREATE INDEX IF NOT EXISTS idx_tick_changes_trade ON tick_changes(code, market_ts_ms, volume_delta);

CREATE TABLE IF NOT EXISTS m0_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    bond_tick_id INTEGER NOT NULL,
    stock_tick_id INTEGER NOT NULL,
    market_ts_ms INTEGER NOT NULL,
    stock_market_ts_ms INTEGER NOT NULL,
    conversion_price REAL NOT NULL,
    parity_mid REAL NOT NULL,
    premium_mid REAL NOT NULL,
    reference_premium REAL,
    fair_buy REAL,
    buy_discount REAL,
    fair_sell REAL,
    exit_discount REAL,
    warmup_count INTEGER NOT NULL,
    is_entry_signal INTEGER NOT NULL,
    is_exit_signal INTEGER NOT NULL,
    valid INTEGER NOT NULL,
    invalid_reason TEXT,
    created_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_m0_market ON m0_observations(market_ts_ms, id);
CREATE INDEX IF NOT EXISTS idx_m0_signals ON m0_observations(is_entry_signal, is_exit_signal, market_ts_ms);
CREATE UNIQUE INDEX IF NOT EXISTS idx_m0_bond_tick ON m0_observations(bond_tick_id);

CREATE TABLE IF NOT EXISTS strategy_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    observation_id INTEGER NOT NULL,
    signal_type TEXT NOT NULL,
    market_ts_ms INTEGER NOT NULL,
    discount REAL NOT NULL,
    reference_price REAL NOT NULL,
    executable_price REAL NOT NULL,
    details_json TEXT NOT NULL,
    FOREIGN KEY(observation_id) REFERENCES m0_observations(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_signal_unique
ON strategy_signals(observation_id, signal_type);

CREATE TABLE IF NOT EXISTS paper_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    execution_model TEXT NOT NULL,
    fill_mode TEXT NOT NULL,
    signal_id INTEGER,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    status TEXT NOT NULL,
    created_market_ts_ms INTEGER NOT NULL,
    updated_market_ts_ms INTEGER NOT NULL,
    expires_market_ts_ms INTEGER,
    limit_price REAL,
    quantity REAL NOT NULL,
    filled_quantity REAL NOT NULL DEFAULT 0,
    average_fill_price REAL,
    queue_ahead REAL NOT NULL DEFAULT 0,
    cancel_reason TEXT,
    metadata_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paper_orders_open ON paper_orders(status, strategy_id, updated_market_ts_ms);

CREATE TABLE IF NOT EXISTS paper_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    order_id INTEGER NOT NULL,
    strategy_id TEXT NOT NULL,
    market_ts_ms INTEGER NOT NULL,
    received_ts_ns INTEGER NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    fill_reason TEXT NOT NULL,
    reference_tick_id INTEGER NOT NULL,
    FOREIGN KEY(order_id) REFERENCES paper_orders(id)
);

CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    status TEXT NOT NULL,
    quantity REAL NOT NULL,
    entry_market_ts_ms INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    entry_stock_price REAL NOT NULL,
    entry_signal_id INTEGER,
    exit_market_ts_ms INTEGER,
    exit_price REAL,
    exit_stock_price REAL,
    exit_reason TEXT,
    gross_return REAL,
    max_favorable_return REAL NOT NULL DEFAULT 0,
    max_adverse_return REAL NOT NULL DEFAULT 0,
    updated_market_ts_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_paper_positions_status ON paper_positions(status, strategy_id);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    market_ts_ms INTEGER NOT NULL,
    realized_pnl REAL NOT NULL,
    unrealized_pnl REAL NOT NULL,
    gross_return REAL NOT NULL,
    position_quantity REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS app_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    created_at_utc TEXT NOT NULL,
    level TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_events_time ON app_events(created_at_utc, level);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteStore:
    def __init__(self, config: AppConfig, *, run_id: str | None = None) -> None:
        self.config = config
        self.path = config.storage.database
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=30000")
        self.connection.executescript(SCHEMA_SQL)
        self._ensure_schema_version()
        self.run_id = run_id or str(uuid.uuid4())
        self.pending_rows = 0
        self.last_commit_monotonic = time.monotonic()

    def _ensure_schema_version(self) -> None:
        row = self.connection.execute("SELECT version FROM schema_info ORDER BY rowid DESC LIMIT 1").fetchone()
        if row is None:
            self.connection.execute(
                "INSERT INTO schema_info(version, applied_at_utc) VALUES (?, ?)",
                (SCHEMA_VERSION, _utc_now()),
            )
            self.connection.commit()
        elif row["version"] != SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema {row['version']} is incompatible with application schema {SCHEMA_VERSION}"
            )

    def start_session(self) -> None:
        self.connection.execute(
            """INSERT OR REPLACE INTO sessions(
                run_id, started_at_utc, host_name, app_version, qmt_port, config_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'running')""",
            (
                self.run_id, _utc_now(), platform.node(), __version__,
                self.config.qmt.port, self.config.to_json(),
            ),
        )
        self.connection.commit()

    def end_session(self, status: str, dropped_callbacks: int = 0) -> None:
        self.connection.execute(
            """UPDATE sessions SET ended_at_utc=?, status=?, dropped_callbacks=? WHERE run_id=?""",
            (_utc_now(), status, dropped_callbacks, self.run_id),
        )
        self.connection.commit()

    def insert_tick(self, tick: Tick) -> int:
        dt = tick.market_datetime
        values = [
            self.run_id, tick.code, tick.market_ts_ms, tick.received_ts_ns,
            dt.date().isoformat(), dt.time().isoformat(timespec="milliseconds"),
            tick.last_price, tick.open_price, tick.high_price, tick.low_price,
            tick.previous_close, tick.amount, tick.volume, tick.pvolume,
            tick.tick_volume, tick.stock_status, tick.open_interest,
            tick.last_settlement_price, tick.settlement_price,
            tick.transaction_count, tick.pe,
            *tick.ask_prices, *tick.bid_prices, *tick.ask_volumes, *tick.bid_volumes,
            tick.snapshot_hash,
            tick.raw_json if self.config.storage.store_raw_json else None,
        ]
        placeholders = ",".join("?" for _ in values)
        cursor = self.connection.execute(
            f"INSERT INTO raw_ticks VALUES (NULL,{placeholders})", values
        )
        self._changed(1)
        return int(cursor.lastrowid)

    def find_tick_id(self, tick: Tick) -> int | None:
        row = self.connection.execute(
            """SELECT id FROM raw_ticks
               WHERE code=? AND market_ts_ms=? AND snapshot_hash=? LIMIT 1""",
            (tick.code, tick.market_ts_ms, tick.snapshot_hash),
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def tick_exists(self, tick: Tick) -> bool:
        return self.find_tick_id(tick) is not None

    def insert_tick_change(self, tick_id: int, tick: Tick, change: TickChange) -> None:
        self.connection.execute(
            """INSERT INTO tick_changes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(tick_id) DO UPDATE SET
                 previous_tick_id=excluded.previous_tick_id,
                 code=excluded.code,
                 market_ts_ms=excluded.market_ts_ms,
                 volume_delta=excluded.volume_delta,
                 amount_delta=excluded.amount_delta,
                 transaction_delta=excluded.transaction_delta,
                 inferred_side=excluded.inferred_side,
                 side_confidence=excluded.side_confidence,
                 last_price_changed=excluded.last_price_changed,
                 best_bid_changed=excluded.best_bid_changed,
                 best_ask_changed=excluded.best_ask_changed,
                 spread=excluded.spread,
                 midpoint=excluded.midpoint,
                 book_change_json=excluded.book_change_json""",
            (
                tick_id, change.previous_tick_id, tick.code, tick.market_ts_ms,
                change.volume_delta, change.amount_delta, change.transaction_delta,
                change.inferred_side, change.side_confidence,
                int(change.last_price_changed), int(change.best_bid_changed), int(change.best_ask_changed),
                tick.spread, tick.midpoint, change.book_change_json,
            ),
        )
        self._changed(1)

    def insert_m0_observation(self, values: dict[str, Any]) -> int:
        columns = list(values)
        parameters = [values[column] for column in columns]
        update_columns = [column for column in columns if column != "bond_tick_id"]
        self.connection.execute(
            f"INSERT INTO m0_observations({','.join(columns)}) VALUES "
            f"({','.join('?' for _ in columns)}) "
            "ON CONFLICT(bond_tick_id) DO UPDATE SET "
            + ",".join(f"{column}=excluded.{column}" for column in update_columns),
            parameters,
        )
        row = self.connection.execute(
            "SELECT id FROM m0_observations WHERE bond_tick_id=?",
            (values["bond_tick_id"],),
        ).fetchone()
        self._changed(1)
        return int(row["id"])

    def insert_signal(self, values: dict[str, Any]) -> int:
        columns = list(values)
        self.connection.execute(
            f"INSERT INTO strategy_signals({','.join(columns)}) VALUES "
            f"({','.join('?' for _ in columns)}) "
            "ON CONFLICT(observation_id,signal_type) DO UPDATE SET "
            + ",".join(
                f"{column}=excluded.{column}"
                for column in columns if column not in {"observation_id", "signal_type"}
            ),
            [values[column] for column in columns],
        )
        row = self.connection.execute(
            "SELECT id FROM strategy_signals WHERE observation_id=? AND signal_type=?",
            (values["observation_id"], values["signal_type"]),
        ).fetchone()
        self._changed(1)
        return int(row["id"])

    def delete_signal(self, observation_id: int, signal_type: str) -> None:
        cursor = self.connection.execute(
            "DELETE FROM strategy_signals WHERE observation_id=? AND signal_type=?",
            (observation_id, signal_type),
        )
        if cursor.rowcount:
            self._changed(cursor.rowcount)

    def create_order(self, values: dict[str, Any]) -> int:
        columns = list(values)
        cursor = self.connection.execute(
            f"INSERT INTO paper_orders({','.join(columns)}) VALUES "
            f"({','.join('?' for _ in columns)})",
            [values[column] for column in columns],
        )
        self._changed(1)
        return int(cursor.lastrowid)

    def update_order(self, order_id: int, **values: Any) -> None:
        assignments = ",".join(f"{column}=?" for column in values)
        self.connection.execute(
            f"UPDATE paper_orders SET {assignments} WHERE id=?",
            [*values.values(), order_id],
        )
        self._changed(1)

    def insert_fill(self, values: dict[str, Any]) -> int:
        columns = list(values)
        cursor = self.connection.execute(
            f"INSERT INTO paper_fills({','.join(columns)}) VALUES "
            f"({','.join('?' for _ in columns)})",
            [values[column] for column in columns],
        )
        self._changed(1)
        return int(cursor.lastrowid)

    def create_position(self, values: dict[str, Any]) -> int:
        columns = list(values)
        cursor = self.connection.execute(
            f"INSERT INTO paper_positions({','.join(columns)}) VALUES "
            f"({','.join('?' for _ in columns)})",
            [values[column] for column in columns],
        )
        self._changed(1)
        return int(cursor.lastrowid)

    def update_position(self, position_id: int, **values: Any) -> None:
        assignments = ",".join(f"{column}=?" for column in values)
        self.connection.execute(
            f"UPDATE paper_positions SET {assignments} WHERE id=?",
            [*values.values(), position_id],
        )
        self._changed(1)

    def insert_equity(self, values: dict[str, Any]) -> None:
        columns = list(values)
        self.connection.execute(
            f"INSERT INTO equity_snapshots({','.join(columns)}) VALUES "
            f"({','.join('?' for _ in columns)})",
            [values[column] for column in columns],
        )
        self._changed(1)

    def app_event(self, level: str, event_type: str, message: str, details: Any = None) -> None:
        self.connection.execute(
            """INSERT INTO app_events(run_id, created_at_utc, level, event_type, message, details_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                self.run_id, _utc_now(), level, event_type, message,
                json.dumps(details or {}, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        self._changed(1)

    def recent_premiums(self, conversion_price: float, limit: int) -> list[float]:
        rows = self.connection.execute(
            """SELECT premium_mid FROM m0_observations
               WHERE valid=1 AND conversion_price=?
               ORDER BY market_ts_ms DESC, id DESC LIMIT ?""",
            (conversion_price, limit),
        ).fetchall()
        return [float(row["premium_mid"]) for row in reversed(rows)]

    def status_summary(self, market_date: str | None = None) -> dict[str, Any]:
        date_filter = market_date or datetime.now(SHANGHAI).date().isoformat()
        local_start = datetime.fromisoformat(date_filter).replace(tzinfo=SHANGHAI)
        start_ms = int(local_start.timestamp() * 1000)
        end_ms = int((local_start + timedelta(days=1)).timestamp() * 1000)
        tick_rows = self.connection.execute(
            """SELECT code, COUNT(*) AS count, MIN(market_ts_ms) AS first_ts,
                      MAX(market_ts_ms) AS last_ts, SUM(CASE WHEN raw_json IS NOT NULL THEN 1 ELSE 0 END) AS raw_count
               FROM raw_ticks WHERE market_date=? GROUP BY code""",
            (date_filter,),
        ).fetchall()
        signals = self.connection.execute(
            """SELECT signal_type, COUNT(*) AS count FROM strategy_signals
               WHERE market_ts_ms >= ? AND market_ts_ms < ? GROUP BY signal_type""",
            (start_ms, end_ms),
        ).fetchall()
        open_orders = self.connection.execute(
            "SELECT COUNT(*) AS count FROM paper_orders WHERE status IN ('open','partial')"
        ).fetchone()["count"]
        open_positions = self.connection.execute(
            "SELECT COUNT(*) AS count FROM paper_positions WHERE status='open'"
        ).fetchone()["count"]
        m0_quality = self.connection.execute(
            """SELECT COUNT(*) AS observations,
                      SUM(CASE WHEN valid=1 THEN 1 ELSE 0 END) AS valid,
                      SUM(CASE WHEN valid=0 THEN 1 ELSE 0 END) AS invalid,
                      MAX(market_ts_ms) AS last_ts
               FROM m0_observations WHERE market_ts_ms>=? AND market_ts_ms<?""",
            (start_ms, end_ms),
        ).fetchone()
        latest_session = self.connection.execute(
            """SELECT run_id,started_at_utc,ended_at_utc,status,dropped_callbacks
               FROM sessions ORDER BY started_at_utc DESC LIMIT 1"""
        ).fetchone()
        strategies = self.connection.execute(
            """SELECT strategy_id,
                      SUM(CASE WHEN status='closed' THEN 1 ELSE 0 END) AS closed_positions,
                      SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS open_positions,
                      COALESCE(SUM(CASE WHEN status='closed'
                        THEN quantity * (exit_price-entry_price) ELSE 0 END), 0) AS realized_pnl,
                      AVG(CASE WHEN status='closed' THEN gross_return END) AS average_gross_return,
                      SUM(CASE WHEN status='closed' AND gross_return>0 THEN 1 ELSE 0 END) AS winning_positions
               FROM paper_positions GROUP BY strategy_id ORDER BY strategy_id"""
        ).fetchall()
        return {
            "date": date_filter,
            "database": str(self.path),
            "ticks": [dict(row) for row in tick_rows],
            "signals": [dict(row) for row in signals],
            "open_orders": open_orders,
            "open_positions": open_positions,
            "m0_quality": dict(m0_quality),
            "latest_session": dict(latest_session) if latest_session else None,
            "paper_strategies": [dict(row) for row in strategies],
        }

    def update_session_health(self, dropped_callbacks: int) -> None:
        self.connection.execute(
            "UPDATE sessions SET dropped_callbacks=? WHERE run_id=?",
            (dropped_callbacks, self.run_id),
        )
        self._changed(1)

    def recover_paper_state(self, strategy_ids: Iterable[str], updated_market_ts_ms: int) -> dict[str, Any]:
        identifiers = tuple(strategy_ids)
        if not identifiers:
            return {"cancelled_orders": 0, "positions": [], "realized": {}, "last_exits": {}}
        placeholders = ",".join("?" for _ in identifiers)
        cursor = self.connection.execute(
            f"""UPDATE paper_orders SET status='cancelled', cancel_reason='restart_recovery',
                       updated_market_ts_ms=?
                WHERE status IN ('open','partial') AND strategy_id IN ({placeholders})""",
            (updated_market_ts_ms, *identifiers),
        )
        if cursor.rowcount:
            self._changed(cursor.rowcount)
        positions = self.connection.execute(
            f"""SELECT * FROM paper_positions
                WHERE status='open' AND strategy_id IN ({placeholders})
                ORDER BY id""",
            identifiers,
        ).fetchall()
        realized_rows = self.connection.execute(
            f"""SELECT strategy_id,
                       COALESCE(SUM(quantity * (exit_price - entry_price)), 0) AS pnl
                FROM paper_positions
                WHERE status='closed' AND strategy_id IN ({placeholders})
                GROUP BY strategy_id""",
            identifiers,
        ).fetchall()
        exit_rows = self.connection.execute(
            f"""SELECT strategy_id, MAX(exit_market_ts_ms) AS exit_ms
                FROM paper_positions
                WHERE status='closed' AND strategy_id IN ({placeholders})
                GROUP BY strategy_id""",
            identifiers,
        ).fetchall()
        return {
            "cancelled_orders": cursor.rowcount,
            "positions": positions,
            "realized": {row["strategy_id"]: float(row["pnl"]) for row in realized_rows},
            "last_exits": {row["strategy_id"]: int(row["exit_ms"]) for row in exit_rows if row["exit_ms"]},
        }

    def iter_raw_ticks(self, start_ms: int | None = None, end_ms: int | None = None) -> Iterable[sqlite3.Row]:
        clauses = []
        values: list[Any] = []
        if start_ms is not None:
            clauses.append("market_ts_ms>=?")
            values.append(start_ms)
        if end_ms is not None:
            clauses.append("market_ts_ms<=?")
            values.append(end_ms)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        yield from self.connection.execute(
            f"SELECT * FROM raw_ticks {where} ORDER BY market_ts_ms, received_ts_ns, id", values
        )

    def backup(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.flush(force=True)
        target = sqlite3.connect(destination)
        try:
            self.connection.backup(target)
        finally:
            target.close()

    def _changed(self, rows: int) -> None:
        self.pending_rows += rows
        self.flush()

    def flush(self, *, force: bool = False) -> None:
        elapsed = time.monotonic() - self.last_commit_monotonic
        if force or self.pending_rows >= self.config.storage.commit_every_rows or elapsed >= self.config.storage.commit_every_seconds:
            self.connection.commit()
            self.pending_rows = 0
            self.last_commit_monotonic = time.monotonic()

    def close(self) -> None:
        self.flush(force=True)
        self.connection.close()
