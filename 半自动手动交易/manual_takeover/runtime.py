from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

from .core import (
    CommandValidationError,
    ExecutionReport,
    ManualTakeoverCommand,
    ManualTakeoverEngine,
    MarketQuote,
)


SUPPORTED_BONDS = {
    "132026.SH": "G三峡EB2",
    "132024.SH": "26江铜EB",
}


class DryRunExecutionAdapter:
    """In-memory executor that never reaches a broker or a trading terminal."""

    paper_only = True
    mode = "dry_run"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sequence = 0
        self._orders: dict[str, ExecutionReport] = {}

    def submit_limit(
        self,
        *,
        bond_code: str,
        side: str,
        limit_price: Decimal,
        quantity_bonds: int,
        client_task_id: str,
    ) -> ExecutionReport:
        del client_task_id
        with self._lock:
            self._sequence += 1
            order_id = f"DRY-{self._sequence:06d}"
            report = ExecutionReport(
                order_id=order_id,
                status="open",
                bond_code=bond_code,
                side=side,
                limit_price=limit_price,
                quantity_bonds=quantity_bonds,
            )
            self._orders[order_id] = report
            return report

    def cancel(self, order_id: str) -> ExecutionReport:
        with self._lock:
            report = self.get(order_id)
            if report.status in {"cancelled", "filled"}:
                return report
            cancelled = replace(report, status="cancelled")
            self._orders[order_id] = cancelled
            return cancelled

    def get(self, order_id: str) -> ExecutionReport:
        with self._lock:
            try:
                return self._orders[order_id]
            except KeyError as exc:
                raise RuntimeError(f"找不到干运行订单{order_id}") from exc

    def simulate_fill(self, order_id: str, filled_bonds: int) -> ExecutionReport:
        """Test-only hook used to exercise partial/final execution reports."""
        with self._lock:
            report = self.get(order_id)
            total = min(report.quantity_bonds, max(0, int(filled_bonds)))
            updated = replace(
                report,
                filled_bonds=total,
                status="filled" if total == report.quantity_bonds else "partial",
            )
            self._orders[order_id] = updated
            return updated


class JsonlAuditSink:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def __call__(self, event: dict[str, Any]) -> None:
        line = json.dumps(event, ensure_ascii=False, allow_nan=False)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


class SqliteQuoteProvider:
    """Read only the newest Level-1 best bid/ask from the maker market ledger."""

    def __init__(self, database: Path) -> None:
        self.database = Path(database)

    def latest(self, bond_code: str) -> MarketQuote | None:
        if not self.database.exists():
            raise FileNotFoundError(self.database)
        uri = f"file:{self.database.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute(
                """SELECT market_date,market_time,market_ts_ms,
                          bid_price_1,ask_price_1
                   FROM raw_ticks WHERE code=?
                   ORDER BY market_ts_ms DESC,id DESC LIMIT 1""",
                (bond_code,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        try:
            return MarketQuote.create(
                bond_code=bond_code,
                market_date=str(row["market_date"]),
                market_time=str(row["market_time"]),
                market_ts_ms=int(row["market_ts_ms"]),
                bid_price=row["bid_price_1"],
                ask_price=row["ask_price_1"],
            )
        except CommandValidationError:
            return None


class ManualTakeoverService:
    """Background quote monitor and HTTP-facing application service."""

    def __init__(
        self,
        quote_provider: SqliteQuoteProvider,
        *,
        executor: DryRunExecutionAdapter | None = None,
        audit_sink: Callable[[dict[str, Any]], None] | None = None,
        window_active: Callable[[], bool] = lambda: True,
        clock: Callable[[], float] = time.time,
        poll_interval_seconds: float = 0.3,
        maximum_quote_age_seconds: float = 10.0,
    ) -> None:
        self.quote_provider = quote_provider
        self.executor = executor or DryRunExecutionAdapter()
        if not self.executor.paper_only:
            raise RuntimeError("当前仓库禁止启用真实委托执行器")
        self.engine = ManualTakeoverEngine(
            self.executor, audit=audit_sink, clock=clock
        )
        self._window_active = window_active
        self._clock = clock
        self._poll_interval = max(0.1, float(poll_interval_seconds))
        self._maximum_quote_age = max(1.0, float(maximum_quote_age_seconds))
        self._quotes: dict[str, MarketQuote] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._window_was_active = self._window_active()

    def start_worker(self) -> None:
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            self._stop.clear()
            self._worker = threading.Thread(
                target=self._run,
                name="manual-takeover-dry-run",
                daemon=True,
            )
            self._worker.start()

    def shutdown(self) -> None:
        self._stop.set()
        worker = self._worker
        if worker and worker.is_alive():
            worker.join(timeout=2.0)

    def start(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        command = ManualTakeoverCommand.from_mapping(payload)
        if command.bond_code not in SUPPORTED_BONDS:
            raise CommandValidationError(f"暂不支持债券{command.bond_code}")
        if not self._window_active():
            raise CommandValidationError("当前不在工作日09:25—15:30手动接管窗口")
        quote = self._refresh_quote(command.bond_code)
        self._require_fresh_quote(quote)
        task = self.engine.start(command)
        return {"ok": True, "task": task, **self.status(refresh_quotes=False)}

    def cancel(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        task = self.engine.cancel(
            task_id=str(payload.get("task_id") or "") or None,
            bond_code=str(payload.get("bond_code") or "").upper() or None,
        )
        return {"ok": True, "task": task, **self.status(refresh_quotes=False)}

    def cancel_all(self) -> dict[str, Any]:
        tasks = self.engine.cancel_all()
        return {"ok": True, "cancelled": tasks, **self.status(refresh_quotes=False)}

    def status(self, *, refresh_quotes: bool = True) -> dict[str, Any]:
        active_window = self._window_active()
        if refresh_quotes and active_window:
            for bond_code in SUPPORTED_BONDS:
                try:
                    self._refresh_quote(bond_code)
                except (FileNotFoundError, sqlite3.Error):
                    continue
        now_ms = int(self._clock() * 1000)
        with self._lock:
            quotes = {
                code: quote.to_dict(now_ms)
                for code, quote in self._quotes.items()
            }
        engine_state = self.engine.snapshot()
        return {
            "paper_only": True,
            "broker_orders_enabled": False,
            "execution_mode": self.executor.mode,
            "execution_label": "干运行（不发送真实委托）",
            "window_active": active_window,
            "poll_interval_ms": round(self._poll_interval * 1000),
            "maximum_quote_age_seconds": self._maximum_quote_age,
            "supported_bonds": [
                {"code": code, "name": name}
                for code, name in SUPPORTED_BONDS.items()
            ],
            "quotes": quotes,
            **engine_state,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            active_window = self._window_active()
            if self._window_was_active and not active_window:
                self.engine.cancel_all("15:30交易窗口结束，自动撤销干运行委托")
            self._window_was_active = active_window
            if not active_window or not self.engine.has_active_tasks():
                self._stop.wait(1.0)
                continue
            for bond_code in self.engine.active_bond_codes():
                if self._stop.is_set():
                    break
                try:
                    quote = self._refresh_quote(bond_code)
                    self._require_fresh_quote(quote)
                    assert quote is not None
                    self.engine.on_quote(quote)
                except Exception as exc:
                    self.engine.fail_safe(bond_code, f"行情输入不可用：{exc}")
            self._stop.wait(self._poll_interval)

    def _refresh_quote(self, bond_code: str) -> MarketQuote | None:
        quote = self.quote_provider.latest(bond_code)
        if quote is not None:
            with self._lock:
                self._quotes[bond_code] = quote
        return quote

    def _require_fresh_quote(self, quote: MarketQuote | None) -> None:
        if quote is None:
            raise CommandValidationError("没有可用的买一/卖一行情")
        age_seconds = (self._clock() * 1000 - quote.market_ts_ms) / 1000
        if age_seconds < -2:
            raise CommandValidationError("行情时间晚于本机时钟，请先校准时间")
        if age_seconds > self._maximum_quote_age:
            raise CommandValidationError(
                f"行情已超过{self._maximum_quote_age:g}秒未更新"
            )
