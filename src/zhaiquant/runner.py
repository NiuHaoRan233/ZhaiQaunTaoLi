from __future__ import annotations

import logging
import os
import signal
import time
from datetime import datetime
from pathlib import Path

from .config import AppConfig
from .database import SQLiteStore
from .m0 import M0Engine, M0Observation
from .maker_paper import (
    MakerPaperEngine,
    MakerPaperPortfolio,
    configured_maker_bond_codes,
)
from .paper import PaperEngine
from .qmt_feed import QmtFeed
from .recorder import RecordedTick, TickRecorder
from .types import Tick
from .types import SHANGHAI


LOGGER = logging.getLogger("zhaiquant")


class LiveProcessLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError(
                f"Another live runner is already using {self.path}"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        self.handle = handle

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


class MarketProcessor:
    def __init__(
        self, config: AppConfig, store: SQLiteStore, *, enable_paper: bool = True,
        recover_paper: bool = False, deduplicate_ticks: bool = True,
        preload_m0_history: bool = True, synchronize_m0: bool = False,
    ) -> None:
        self.config = config
        self.store = store
        self.enable_paper = enable_paper
        self.recorder = TickRecorder(
            store, deduplicate=deduplicate_ticks,
            rebuild_changes=synchronize_m0,
        )
        self.m0 = M0Engine(
            config, store, preload_history=preload_m0_history,
            synchronize=synchronize_m0,
        )
        self.paper = PaperEngine(config, store, recover=recover_paper)
        maker_codes = configured_maker_bond_codes(config)
        self.maker_paper = (
            MakerPaperEngine(config, store, bond_code=maker_codes[0])
            if (
                len(maker_codes) == 1
                and not config.maker_paper.realtime_comparison_model_ids
            )
            else MakerPaperPortfolio(config, store)
        )
        self.processed_ticks = 0
        self.observations = 0
        self.last_observation: M0Observation | None = None
        self.last_observation_monotonic: float | None = None

    def process(self, tick: Tick) -> tuple[RecordedTick, M0Observation | None]:
        recorded = self.recorder.record(tick)
        if self.enable_paper:
            self.maker_paper.on_recorded_tick(recorded)
        if not recorded.is_new and not self.m0.synchronize:
            return recorded, None
        observation = self.m0.on_tick(recorded)
        self.processed_ticks += 1
        if observation is not None:
            self.observations += 1
            self.last_observation = observation
            self.last_observation_monotonic = time.monotonic()
            if self.enable_paper:
                self.paper.on_observation(observation)
            if observation.entry_signal:
                LOGGER.warning(
                    "M0 entry signal discount=%.4f%% bond_ask=%.3f fair=%.3f",
                    (observation.buy_discount or 0.0) * 100,
                    observation.bond.tick.ask1,
                    observation.fair_buy or 0.0,
                )
        return recorded, observation

    def runtime_status(self) -> dict[str, object]:
        observation = self.last_observation
        if observation is None:
            return {
                "market_state": "waiting",
                "paper": self.paper.runtime_summary(None),
                "maker_paper": self.maker_paper.runtime_summary(),
            }
        callback_age = max(
            0.0, time.monotonic() - (self.last_observation_monotonic or time.monotonic())
        )
        market_age = max(
            0.0, time.time() - observation.bond.tick.market_ts_ms / 1000
        )
        age_seconds = max(callback_age, market_age)
        market_state = (
            "live" if age_seconds <= self.config.qmt.stale_after_seconds else "stale"
        )
        return {
            "market_state": market_state,
            "market_age_seconds": round(age_seconds, 1),
            "market_time": observation.bond.tick.market_datetime.isoformat(),
            "bond_bid": observation.bond.tick.bid1,
            "bond_ask": observation.bond.tick.ask1,
            "stock_bid": observation.stock.tick.bid1,
            "stock_ask": observation.stock.tick.ask1,
            "premium": observation.premium_mid if observation.valid else None,
            "reference_premium": observation.reference_premium,
            "buy_discount": observation.buy_discount,
            "entry_discount": self.config.m0.entry_discount,
            "entry_gap": (
                self.config.m0.entry_discount - observation.buy_discount
                if observation.buy_discount is not None else None
            ),
            "warmup_count": observation.warmup_count,
            "minimum_observations": self.config.m0.minimum_observations,
            "observation_valid": observation.valid,
            "invalid_reason": observation.invalid_reason,
            "paper": self.paper.runtime_summary(observation.bond.tick),
            "maker_paper": self.maker_paper.runtime_summary(),
        }


class LiveRunner:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = SQLiteStore(config)
        self.feed = QmtFeed(config)
        self.processor = MarketProcessor(config, self.store)
        self.process_lock = LiveProcessLock(config.storage.database.parent / "live.lock")
        self.stop_requested = False
        self.last_heartbeat = 0.0

    def request_stop(self, *_args) -> None:
        self.stop_requested = True

    def run(self, *, duration_seconds: float | None = None) -> None:
        self.process_lock.acquire()
        try:
            self.processor.paper.recover()
            self.store.start_session()
            self.processor.maker_paper.rebuild_date(datetime.now(SHANGHAI).date())
        except Exception:
            self.store.close()
            self.process_lock.release()
            raise
        started = time.monotonic()
        status = "stopped"
        old_sigint = signal.signal(signal.SIGINT, self.request_stop)
        old_sigterm = signal.signal(signal.SIGTERM, self.request_stop)
        try:
            self.feed.connect()
            self.store.app_event(
                "info", "qmt_connected", "Connected to MiniQMT",
                {"port": self.config.qmt.port, "codes": self.feed.codes},
            )
            for tick in self.feed.snapshot():
                self.processor.process(tick)
            subscriptions = self.feed.subscribe()
            self.store.app_event(
                "info", "subscriptions_started", "Tick subscriptions started",
                {"subscription_ids": subscriptions},
            )
            LOGGER.info(
                "Live recorder started: port=%s database=%s strategies=%s",
                self.config.qmt.port,
                self.config.storage.database,
                ",".join(self.processor.paper.accounts),
            )

            while not self.stop_requested:
                if duration_seconds is not None and time.monotonic() - started >= duration_seconds:
                    break
                tick = self.feed.get(timeout=1.0)
                if tick is not None:
                    try:
                        self.processor.process(tick)
                    except Exception as exc:
                        LOGGER.exception("Tick processing failed")
                        self.store.app_event(
                            "error", "tick_processing_error", str(exc),
                            {"code": tick.code, "market_ts_ms": tick.market_ts_ms},
                        )
                self.store.flush()
                self._heartbeat()
                if not self.feed.is_connected():
                    raise ConnectionError("MiniQMT disconnected; runner stopped to avoid a silent data gap")
            status = "completed" if duration_seconds is not None else "stopped"
        except Exception as exc:
            status = "failed"
            self.store.app_event("critical", "runner_failed", str(exc))
            self.store.flush(force=True)
            raise
        finally:
            self.feed.close()
            self.store.end_session(status, self.feed.dropped_callbacks)
            self.store.close()
            self.process_lock.release()
            signal.signal(signal.SIGINT, old_sigint)
            signal.signal(signal.SIGTERM, old_sigterm)

    def _heartbeat(self) -> None:
        now = time.monotonic()
        if now - self.last_heartbeat < self.config.qmt.status_interval_seconds:
            return
        details = {
            "processed_ticks": self.processor.processed_ticks,
            "observations": self.processor.observations,
            "queue_size": self.feed.queue.qsize(),
            "dropped_callbacks": self.feed.dropped_callbacks,
            "callback_errors": self.feed.callback_errors,
            **self.processor.runtime_status(),
        }
        self.store.update_session_health(self.feed.dropped_callbacks)
        self.store.app_event("info", "heartbeat", "Runner heartbeat", details)
        LOGGER.info("MARKET %s", self._format_status(details))
        self.last_heartbeat = now

    @staticmethod
    def _format_status(details: dict[str, object]) -> str:
        paper = details["paper"]
        maker_paper = details["maker_paper"]
        health = (
            f"q={details['queue_size']} drop={details['dropped_callbacks']} "
            f"err={details['callback_errors']}"
        )
        paper_text = (
            f"orders={paper['open_orders']} pos={paper['open_positions']} "
            f"fills={paper['fills_this_run']} "
            f"pnl={paper['realized_pnl'] + paper['unrealized_pnl']:.2f} "
            f"exposure={paper['order_notional_cny'] + paper['position_notional_cny']:.0f}"
        )
        maker_accounts = maker_paper.get("accounts", [])
        maker_text = "maker=off"
        if maker_paper.get("enabled"):
            maker_text = "maker=" + ",".join(
                f"{item.get('bond_code', '')}:{item.get('model_id', item['fill_mode'])}:"
                f"inv{item['inventory']:.0f}/pnl{item['pnl']:.0f}"
                for item in maker_accounts
            )
        if details["market_state"] == "waiting":
            return f"WAITING | {paper_text} | {maker_text} | {health}"

        market_time = str(details["market_time"])[11:19]
        state = str(details["market_state"]).upper()
        if details["market_state"] == "stale":
            state += f"({details['market_age_seconds']:.0f}s)"
        prices = (
            f"bond={details['bond_bid']:.3f}/{details['bond_ask']:.3f} "
            f"stock={details['stock_bid']:.3f}/{details['stock_ask']:.3f}"
        )
        if details["buy_discount"] is None:
            model = (
                f"warmup={details['warmup_count']}/{details['minimum_observations']}"
                if details["observation_valid"] else f"invalid={details['invalid_reason']}"
            )
        else:
            model = (
                f"premium={details['premium'] * 100:.3f}% "
                f"ref={details['reference_premium'] * 100:.3f}% "
                f"discount={details['buy_discount'] * 100:.3f}% "
                f"target={details['entry_discount'] * 100:.3f}% "
                f"gap={details['entry_gap'] * 100:+.3f}%"
            )
        return (
            f"{market_time} {state} | {prices} | {model} | "
            f"{paper_text} | {maker_text} | {health}"
        )


def configure_logging(config: AppConfig, verbose: bool = False) -> Path:
    log_dir = config.path.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "zhaiquant.log"
    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(stream)
    root.addHandler(file_handler)
    return log_path
