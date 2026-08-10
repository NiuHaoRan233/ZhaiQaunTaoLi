from __future__ import annotations

import queue
import threading
import time
from typing import Any

from .config import AppConfig
from .types import Tick


class QmtFeed:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.queue: queue.Queue[Tick] = queue.Queue(maxsize=config.qmt.callback_queue_size)
        self.client = None
        self.subscriptions: list[int] = []
        self.dropped_callbacks = 0
        self.callback_errors = 0
        self._lock = threading.Lock()

    @property
    def codes(self) -> tuple[str, ...]:
        ordered = (
            self.config.qmt.stock_code,
            self.config.qmt.bond_code,
            *self.config.qmt.watch_codes,
        )
        return tuple(dict.fromkeys(ordered))

    def connect(self) -> None:
        from xtquant import xtdata

        xtdata.enable_hello = False
        self.client = xtdata.connect(port=self.config.qmt.port)
        if self.client is None or not self.client.is_connected():
            raise ConnectionError(f"MiniQMT connection failed on port {self.config.qmt.port}")

    def is_connected(self) -> bool:
        return bool(self.client and self.client.is_connected())

    def snapshot(self) -> list[Tick]:
        from xtquant import xtdata

        received = time.time_ns()
        data = xtdata.get_full_tick(list(self.codes))
        ticks = []
        for code in self.codes:
            payload = data.get(code)
            if isinstance(payload, dict):
                ticks.append(Tick.from_qmt(code, payload, received))
        return ticks

    def subscribe(self) -> list[int]:
        from xtquant import xtdata

        if not self.is_connected():
            raise ConnectionError("MiniQMT is not connected")
        for code in self.codes:
            seq = xtdata.subscribe_quote(
                code,
                period="tick",
                start_time="",
                end_time="",
                count=0,
                callback=self._callback,
            )
            if not isinstance(seq, int) or seq <= 0:
                raise RuntimeError(f"Quote subscription failed for {code}: {seq!r}")
            self.subscriptions.append(seq)
        return list(self.subscriptions)

    def _callback(self, payload: dict[str, Any]) -> None:
        received = time.time_ns()
        try:
            for code, rows in payload.items():
                if isinstance(rows, dict):
                    rows = [rows]
                if not isinstance(rows, (list, tuple)):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    tick = Tick.from_qmt(code, row, received)
                    try:
                        self.queue.put_nowait(tick)
                    except queue.Full:
                        with self._lock:
                            self.dropped_callbacks += 1
        except Exception:
            with self._lock:
                self.callback_errors += 1

    def get(self, timeout: float = 1.0) -> Tick | None:
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        from xtquant import xtdata

        for seq in self.subscriptions:
            try:
                xtdata.unsubscribe_quote(seq)
            except Exception:
                pass
        self.subscriptions.clear()
