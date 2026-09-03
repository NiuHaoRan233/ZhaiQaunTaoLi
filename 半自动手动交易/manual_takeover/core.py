from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Protocol


PRICE_TICK = Decimal("0.001")
ACTIVE_STATUS = "active"
TERMINAL_STATUSES = {"cancelled", "filled", "limit_reached", "error"}


class CommandValidationError(ValueError):
    """Raised when a manual takeover command is economically invalid."""


def _price(value: Any, label: str) -> Decimal:
    try:
        raw = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CommandValidationError(f"{label}必须是有效价格") from exc
    if not raw.is_finite() or raw <= 0:
        raise CommandValidationError(f"{label}必须大于0")
    normalized = raw.quantize(PRICE_TICK)
    if raw != normalized:
        raise CommandValidationError(f"{label}最多保留3位小数")
    return normalized


def _quantity(value: Any) -> int:
    try:
        quantity = int(value)
    except (TypeError, ValueError) as exc:
        raise CommandValidationError("委托数量必须是整数张") from exc
    if isinstance(value, float) and not value.is_integer():
        raise CommandValidationError("委托数量必须是整数张")
    if quantity <= 0:
        raise CommandValidationError("委托数量必须大于0张")
    if quantity % 10:
        raise CommandValidationError("交换债委托数量必须是10张的整数倍")
    return quantity


@dataclass(frozen=True)
class ManualTakeoverCommand:
    bond_code: str
    side: str
    quantity_bonds: int
    start_price: Decimal
    extreme_price: Decimal
    tick_size: Decimal = PRICE_TICK

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ManualTakeoverCommand":
        side = str(payload.get("side", "")).strip().lower()
        if side not in {"buy", "sell"}:
            raise CommandValidationError("方向必须是买入或卖出")
        bond_code = str(payload.get("bond_code", "")).strip().upper()
        if not bond_code:
            raise CommandValidationError("债券代码不能为空")
        start_price = _price(payload.get("start_price"), "起始价格")
        extreme_price = _price(payload.get("extreme_price"), "极限价格")
        if side == "buy" and extreme_price <= start_price:
            raise CommandValidationError("买入极限价必须严格高于起始价")
        if side == "sell" and extreme_price >= start_price:
            raise CommandValidationError("卖出极限价必须严格低于起始价")
        return cls(
            bond_code=bond_code,
            side=side,
            quantity_bonds=_quantity(payload.get("quantity_bonds")),
            start_price=start_price,
            extreme_price=extreme_price,
        )


@dataclass(frozen=True)
class MarketQuote:
    bond_code: str
    market_date: str
    market_time: str
    market_ts_ms: int
    bid_price: Decimal
    ask_price: Decimal

    @classmethod
    def create(
        cls,
        *,
        bond_code: str,
        market_date: str,
        market_time: str,
        market_ts_ms: int,
        bid_price: Any,
        ask_price: Any,
    ) -> "MarketQuote":
        return cls(
            bond_code=bond_code,
            market_date=market_date,
            market_time=market_time,
            market_ts_ms=int(market_ts_ms),
            bid_price=_price(bid_price, "买一价"),
            ask_price=_price(ask_price, "卖一价"),
        )

    def to_dict(self, now_ms: int | None = None) -> dict[str, Any]:
        current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        return {
            "bond_code": self.bond_code,
            "market_date": self.market_date,
            "market_time": self.market_time,
            "market_ts_ms": self.market_ts_ms,
            "bid_price": float(self.bid_price),
            "ask_price": float(self.ask_price),
            "age_seconds": round(max(0, current_ms - self.market_ts_ms) / 1000, 3),
        }


@dataclass(frozen=True)
class ExecutionReport:
    order_id: str
    status: str
    bond_code: str
    side: str
    limit_price: Decimal
    quantity_bonds: int
    filled_bonds: int = 0

    @property
    def remaining_bonds(self) -> int:
        return max(0, self.quantity_bonds - self.filled_bonds)


class ExecutionAdapter(Protocol):
    paper_only: bool
    mode: str

    def submit_limit(
        self,
        *,
        bond_code: str,
        side: str,
        limit_price: Decimal,
        quantity_bonds: int,
        client_task_id: str,
    ) -> ExecutionReport: ...

    def cancel(self, order_id: str) -> ExecutionReport: ...

    def get(self, order_id: str) -> ExecutionReport: ...


@dataclass
class TakeoverTask:
    task_id: str
    command: ManualTakeoverCommand
    status: str
    created_at: str
    updated_at: str
    current_order_id: str | None = None
    current_price: Decimal | None = None
    current_order_filled_bonds: int = 0
    filled_bonds: int = 0
    reprice_count: int = 0
    last_competitor_price: Decimal | None = None
    message: str = ""
    unconfirmed_live_order: bool = False

    @property
    def remaining_bonds(self) -> int:
        return max(0, self.command.quantity_bonds - self.filled_bonds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "bond_code": self.command.bond_code,
            "side": self.command.side,
            "quantity_bonds": self.command.quantity_bonds,
            "filled_bonds": self.filled_bonds,
            "remaining_bonds": self.remaining_bonds,
            "start_price": float(self.command.start_price),
            "extreme_price": float(self.command.extreme_price),
            "tick_size": float(self.command.tick_size),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_order_id": self.current_order_id,
            "current_price": (
                float(self.current_price) if self.current_price is not None else None
            ),
            "reprice_count": self.reprice_count,
            "last_competitor_price": (
                float(self.last_competitor_price)
                if self.last_competitor_price is not None else None
            ),
            "message": self.message,
            "unconfirmed_live_order": self.unconfirmed_live_order,
        }


class ManualTakeoverEngine:
    """Thread-safe one-tick price-priority state machine.

    The engine deliberately treats a quote equal to our current limit as our own
    top-of-book presence.  It only reprices when the visible best price is
    strictly ahead, preventing a live adapter from chasing its own order.
    """

    def __init__(
        self,
        executor: ExecutionAdapter,
        *,
        audit: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.time,
        event_limit: int = 500,
    ) -> None:
        if not getattr(executor, "paper_only", False):
            raise RuntimeError("当前仓库只允许纸面/干运行执行适配器")
        self.executor = executor
        self._audit = audit
        self._clock = clock
        self._lock = threading.RLock()
        self._tasks: dict[str, TakeoverTask] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=event_limit)
        self._event_sequence = 0

    def start(self, command: ManualTakeoverCommand) -> dict[str, Any]:
        with self._lock:
            if self._blocking_for_bond(command.bond_code):
                raise CommandValidationError(
                    f"{command.bond_code}已有活动任务或尚未确认撤销的订单"
                )
            task_id = f"MT-{uuid.uuid4().hex[:12].upper()}"
            now = self._iso_now()
            task = TakeoverTask(
                task_id=task_id,
                command=command,
                status=ACTIVE_STATUS,
                created_at=now,
                updated_at=now,
                message="已按起始价建立干运行委托",
            )
            self._tasks[task_id] = task
            try:
                report = self.executor.submit_limit(
                    bond_code=command.bond_code,
                    side=command.side,
                    limit_price=command.start_price,
                    quantity_bonds=command.quantity_bonds,
                    client_task_id=task_id,
                )
                self._adopt_order(task, report)
            except Exception as exc:
                self._fail(task, f"起始委托失败：{exc}", unconfirmed=False)
                raise RuntimeError(task.message) from exc
            self._emit(
                task,
                "task_started",
                "启动追价",
                price=command.start_price,
                quantity_bonds=command.quantity_bonds,
            )
            return task.to_dict()

    def on_quote(self, quote: MarketQuote) -> dict[str, Any] | None:
        with self._lock:
            task = self._active_for_bond(quote.bond_code)
            if task is None:
                return None
            if not self._reconcile(task):
                return task.to_dict()
            if task.current_price is None:
                self._fail(task, "活动任务缺少当前委托价格", unconfirmed=True)
                return task.to_dict()

            if task.command.side == "buy":
                competitor = quote.bid_price
                if competitor <= task.current_price:
                    return task.to_dict()
                target = competitor + task.command.tick_size
                boundary_hit = target >= task.command.extreme_price
            else:
                competitor = quote.ask_price
                if competitor >= task.current_price:
                    return task.to_dict()
                target = competitor - task.command.tick_size
                boundary_hit = target <= task.command.extreme_price

            task.last_competitor_price = competitor
            task.updated_at = self._iso_now()
            if boundary_hit:
                self._stop_at_limit(task, competitor, target)
                return task.to_dict()
            self._replace(task, target, competitor)
            return task.to_dict()

    def cancel(self, *, task_id: str | None = None, bond_code: str | None = None,
               reason: str = "用户一键撤销") -> dict[str, Any]:
        with self._lock:
            task = self._resolve_active(task_id=task_id, bond_code=bond_code)
            if task is None:
                raise CommandValidationError("没有可撤销的活动任务")
            self._cancel_task(task, reason)
            return task.to_dict()

    def cancel_all(self, reason: str = "用户全部撤销") -> list[dict[str, Any]]:
        with self._lock:
            cancellable = [
                task for task in self._tasks.values()
                if task.status == ACTIVE_STATUS or task.unconfirmed_live_order
            ]
            for task in cancellable:
                self._cancel_task(task, reason)
            return [task.to_dict() for task in cancellable]

    def fail_safe(self, bond_code: str, reason: str) -> dict[str, Any] | None:
        """Cancel an active paper order when its causal market input is unsafe."""
        with self._lock:
            task = self._active_for_bond(bond_code)
            if task is None:
                return None
            try:
                self._cancel_current(task)
                task.status = "error"
                task.message = reason
                task.unconfirmed_live_order = False
                self._emit(task, "safety_stop", "安全停止", alert=True, detail=reason)
            except Exception as exc:
                self._fail(
                    task,
                    f"{reason}；撤单确认失败：{exc}",
                    unconfirmed=True,
                )
            return task.to_dict()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda item: item.created_at)
            return {
                "tasks": [task.to_dict() for task in tasks],
                "events": list(reversed(self._events)),
            }

    def has_active_tasks(self) -> bool:
        with self._lock:
            return any(task.status == ACTIVE_STATUS for task in self._tasks.values())

    def active_bond_codes(self) -> list[str]:
        with self._lock:
            return [
                task.command.bond_code
                for task in self._tasks.values()
                if task.status == ACTIVE_STATUS
            ]

    def _replace(
        self, task: TakeoverTask, target: Decimal, competitor: Decimal
    ) -> None:
        old_price = task.current_price
        try:
            self._cancel_current(task)
            if task.remaining_bonds <= 0:
                self._complete_filled(task)
                return
            report = self.executor.submit_limit(
                bond_code=task.command.bond_code,
                side=task.command.side,
                limit_price=target,
                quantity_bonds=task.remaining_bonds,
                client_task_id=task.task_id,
            )
            self._adopt_order(task, report)
        except Exception as exc:
            self._fail(
                task,
                f"改价失败：{exc}",
                unconfirmed=task.current_order_id is not None,
            )
            return
        task.reprice_count += 1
        task.message = "已撤旧单并领先外部最优价一厘"
        self._emit(
            task,
            "order_repriced",
            "比价改价",
            price=target,
            previous_price=old_price,
            competitor_price=competitor,
            quantity_bonds=task.remaining_bonds,
        )

    def _stop_at_limit(
        self, task: TakeoverTask, competitor: Decimal, required_target: Decimal
    ) -> None:
        try:
            self._cancel_current(task)
        except Exception as exc:
            self._fail(
                task,
                f"触及极限价但撤单确认失败：{exc}",
                unconfirmed=True,
            )
            return
        if task.remaining_bonds <= 0:
            self._complete_filled(task)
            return
        task.status = "limit_reached"
        task.message = "继续领先将触及极限价，原单已撤销，任务停止"
        task.unconfirmed_live_order = False
        self._emit(
            task,
            "limit_reached",
            "触及极限并撤销",
            alert=True,
            competitor_price=competitor,
            required_price=required_target,
            extreme_price=task.command.extreme_price,
            quantity_bonds=task.remaining_bonds,
        )

    def _cancel_task(self, task: TakeoverTask, reason: str) -> None:
        try:
            self._cancel_current(task)
        except Exception as exc:
            self._fail(task, f"撤单确认失败：{exc}", unconfirmed=True)
            return
        if task.remaining_bonds <= 0:
            self._complete_filled(task)
            return
        task.status = "cancelled"
        task.message = reason
        task.unconfirmed_live_order = False
        self._emit(
            task,
            "task_cancelled",
            "任务已撤销",
            detail=reason,
            quantity_bonds=task.remaining_bonds,
        )

    def _cancel_current(self, task: TakeoverTask) -> None:
        if task.current_order_id is None:
            return
        order_id = task.current_order_id
        report = self.executor.cancel(order_id)
        self._sync_report(task, report)
        if report.status == "filled" or task.remaining_bonds <= 0:
            task.current_order_id = None
            task.current_price = None
            return
        if report.status != "cancelled":
            raise RuntimeError(f"订单{order_id}未确认撤销，当前状态{report.status}")
        task.current_order_id = None
        task.current_price = None
        task.current_order_filled_bonds = 0

    def _reconcile(self, task: TakeoverTask) -> bool:
        if task.current_order_id is None:
            return False
        try:
            report = self.executor.get(task.current_order_id)
        except Exception as exc:
            self._fail(task, f"委托回报读取失败：{exc}", unconfirmed=True)
            return False
        self._sync_report(task, report)
        if report.status == "filled" or task.remaining_bonds <= 0:
            self._complete_filled(task)
            return False
        if report.status not in {"open", "partial"}:
            self._fail(task, f"委托异常终态：{report.status}", unconfirmed=False)
            return False
        return True

    def _sync_report(self, task: TakeoverTask, report: ExecutionReport) -> None:
        newly_filled = max(0, report.filled_bonds - task.current_order_filled_bonds)
        task.filled_bonds = min(
            task.command.quantity_bonds, task.filled_bonds + newly_filled
        )
        task.current_order_filled_bonds = report.filled_bonds
        task.updated_at = self._iso_now()

    def _adopt_order(self, task: TakeoverTask, report: ExecutionReport) -> None:
        if report.status not in {"open", "partial"}:
            raise RuntimeError(f"新委托未进入活动状态：{report.status}")
        task.current_order_id = report.order_id
        task.current_price = report.limit_price
        task.current_order_filled_bonds = report.filled_bonds
        task.updated_at = self._iso_now()
        task.unconfirmed_live_order = False

    def _complete_filled(self, task: TakeoverTask) -> None:
        task.status = "filled"
        task.message = "委托已全部成交，追价任务结束"
        task.current_order_id = None
        task.current_price = None
        task.unconfirmed_live_order = False
        self._emit(
            task,
            "task_filled",
            "委托全部成交",
            alert=True,
            quantity_bonds=task.filled_bonds,
        )

    def _fail(self, task: TakeoverTask, message: str, *, unconfirmed: bool) -> None:
        task.status = "error"
        task.message = message
        task.updated_at = self._iso_now()
        task.unconfirmed_live_order = unconfirmed
        self._emit(
            task,
            "execution_error",
            "执行异常",
            alert=True,
            detail=message,
        )

    def _emit(
        self,
        task: TakeoverTask,
        event_type: str,
        label: str,
        *,
        alert: bool = False,
        **details: Any,
    ) -> None:
        self._event_sequence += 1
        task.updated_at = self._iso_now()
        event = {
            "event_id": self._event_sequence,
            "ts": int(self._clock() * 1000),
            "time": datetime.fromtimestamp(self._clock()).strftime("%H:%M:%S"),
            "task_id": task.task_id,
            "bond_code": task.command.bond_code,
            "side": task.command.side,
            "event_type": event_type,
            "label": label,
            "alert": alert,
            "paper_only": True,
        }
        for key, value in details.items():
            event[key] = float(value) if isinstance(value, Decimal) else value
        self._events.append(event)
        if self._audit is not None:
            self._audit(dict(event))

    def _active_for_bond(self, bond_code: str) -> TakeoverTask | None:
        return next(
            (
                task
                for task in reversed(list(self._tasks.values()))
                if task.command.bond_code == bond_code and task.status == ACTIVE_STATUS
            ),
            None,
        )

    def _blocking_for_bond(self, bond_code: str) -> TakeoverTask | None:
        return next(
            (
                task
                for task in reversed(list(self._tasks.values()))
                if task.command.bond_code == bond_code
                and (task.status == ACTIVE_STATUS or task.unconfirmed_live_order)
            ),
            None,
        )

    def _resolve_active(
        self, *, task_id: str | None, bond_code: str | None
    ) -> TakeoverTask | None:
        if task_id:
            task = self._tasks.get(task_id)
            return task if task and (
                task.status == ACTIVE_STATUS or task.unconfirmed_live_order
            ) else None
        if bond_code:
            return self._blocking_for_bond(bond_code)
        return None

    def _iso_now(self) -> str:
        return datetime.fromtimestamp(self._clock(), tz=timezone.utc).isoformat()
