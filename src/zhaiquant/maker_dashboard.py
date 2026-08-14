from __future__ import annotations

import os
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any

from .maker import MakerAnalyzer, MakerParameters, _load_ticks
from .types import SHANGHAI


DASHBOARD_REFRESH_START = clock_time(9, 25)
DASHBOARD_REFRESH_END = clock_time(15, 30)


def dashboard_refresh_active(now: datetime) -> bool:
    """Continuous dashboards poll only during the requested weekday window."""
    local = now.astimezone(SHANGHAI)
    return (
        local.weekday() < 5
        and DASHBOARD_REFRESH_START <= local.time() < DASHBOARD_REFRESH_END
    )


def next_dashboard_refresh_start(now: datetime) -> datetime:
    """Return the next weekday 09:25 boundary in Shanghai time."""
    local = now.astimezone(SHANGHAI)
    candidate = local.replace(
        hour=DASHBOARD_REFRESH_START.hour,
        minute=DASHBOARD_REFRESH_START.minute,
        second=0,
        microsecond=0,
    )
    if local.weekday() >= 5 or local >= candidate:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


SIDE_LABELS = {"buy": "买入", "sell": "卖出"}
MODE_LABELS = {
    "priority": "第一顺位",
    "queue": "排队成交",
    "windfall": "超级捡漏",
}
KIND_LABELS = {
    "base": "期初底仓",
    "low_bid_reversion": "低价承接",
    "inventory_replenish": "库存回补",
    "sweep_tail": "扫尾跟随",
    "deep_discount_sweep": "深度折价主动买",
    "inventory_exit": "库存卖出",
    "inventory_risk_exit": "下行风险退出",
    "super_windfall": "超级捡漏",
}
REASON_LABELS = {
    "passive_buy": "被动承接",
    "passive_sell": "被动卖出",
    "active_tail_sweep": "主动扫尾",
    "active_deep_discount": "深度折价主动买",
    "active_downside_risk_exit": "下行风险主动退出",
    "super_windfall_buy": "超级捡漏买入",
}
STATE_LABELS = {
    "stable": "平稳",
    "possible_rise": "可能上升",
    "rising": "上升",
    "possible_fall": "可能下降",
    "falling": "下降",
}
REFERENCE_LABELS = {
    "previous_close": "昨日收盘（早盘临时锚）",
    "current_midpoint": "当前买卖中点（低置信）",
    "persistent_inside_market": "持续盘口区间",
    "intraday_trade_anchor": "当日成交锚",
    "large_buy_breakout_support": "大买单突破支撑",
}


def _display_width(value: str) -> int:
    return sum(
        2 if unicodedata.east_asian_width(character) in {"W", "F", "A"} else 1
        for character in value
    )


def _pad(value: object, width: int, *, right: bool = False) -> str:
    text = str(value)
    spaces = max(0, width - _display_width(text))
    return (" " * spaces + text) if right else (text + " " * spaces)


def _clock(market_ts_ms: int | float | None) -> str:
    if not market_ts_ms:
        return "--:--:--"
    return datetime.fromtimestamp(float(market_ts_ms) / 1000, SHANGHAI).strftime(
        "%H:%M:%S"
    )


def _quantity(value: int | float | None) -> str:
    number = float(value or 0)
    if abs(number - round(number)) < 1e-9:
        return f"{round(number):,.0f}"
    return f"{number:,.1f}"


def _price(value: int | float | None) -> str:
    return "---" if value is None else f"{float(value):.3f}"


def _duration(seconds: int | float) -> str:
    total = max(0, round(float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _opening_strategy(side: str, lot_kind: str | None) -> str:
    if side == "sell":
        return "高卖后回补"
    return {
        "low_bid_reversion": "低价承接",
        "inventory_replenish": "库存回补",
        "sweep_tail": "扫尾跟随",
        "deep_discount_sweep": "深度折价主动买",
    }.get(lot_kind or "", KIND_LABELS.get(lot_kind or "", "买入后高卖"))


def _flow_strategy(fill: dict[str, Any]) -> str:
    if fill.get("fill_reason") == "active_downside_risk_exit":
        return "下行风险主动退出"
    kind = fill.get("lot_kind")
    if fill["side"] == "buy":
        return _opening_strategy("buy", kind)
    return {
        "base": "底仓高卖",
        "low_bid_reversion": "低价承接退出",
        "inventory_replenish": "回补仓卖出",
        "sweep_tail": "扫尾策略退出",
        "deep_discount_sweep": "深度折价退出",
    }.get(kind or "", "库存卖出")


def _mode_title(fill_mode: str) -> str:
    if fill_mode == "priority":
        return "第一顺位（乐观成交假设：改善一厘抢到第一名）"
    if fill_mode == "queue":
        return "排队成交（较保守成交假设：先消耗前方队列）"
    if fill_mode == "windfall":
        return "超级捡漏（独立一手虚拟额度）"
    return MODE_LABELS.get(fill_mode, fill_mode)


def _strategy_label(strategy_id: str, fill_mode: str | None = None) -> str:
    if strategy_id.endswith("_super_windfall"):
        return "超级捡漏"
    mode = fill_mode or strategy_id.rsplit("_", 1)[-1]
    return MODE_LABELS.get(mode, strategy_id)


def build_daily_trades(
    fills: list[dict[str, Any]], accounts: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Pair fills around the daily base inventory without double-counting.

    A sell below the 1,000-bond base opens a sell-then-buy cycle; a buy above
    the base opens a buy-then-sell cycle. Closing fills are matched FIFO. One
    row represents one opening fill and can contain several partial exits.
    """
    account_by_strategy = {
        account["strategy_id"]: account for account in accounts
    }
    completed: list[dict[str, Any]] = []
    unfinished: list[dict[str, Any]] = []

    for strategy_id, account in account_by_strategy.items():
        baseline = float(account["initial_inventory"])
        inventory = baseline
        open_buys: list[dict[str, Any]] = []
        open_sells: list[dict[str, Any]] = []
        strategy_fills = sorted(
            (fill for fill in fills if fill["strategy_id"] == strategy_id),
            key=lambda fill: (fill["market_ts_ms"], fill["id"]),
        )
        lot_aware = bool(strategy_fills) and all(
            fill.get("lot_id") is not None for fill in strategy_fills
        )
        buy_legs_by_lot: dict[int, list[dict[str, Any]]] = {}

        def open_leg(fill: dict[str, Any], side: str, quantity: float) -> None:
            if quantity <= 1e-9:
                return
            leg = {
                "strategy_id": strategy_id,
                "fill_mode": account["fill_mode"],
                "strategy": _opening_strategy(side, fill.get("lot_kind")),
                "direction": "买→卖" if side == "buy" else "卖→买",
                "open_side": side,
                "open_fill_id": fill["id"],
                "open_ts_ms": int(fill["market_ts_ms"]),
                "open_price": float(fill["price"]),
                "quantity": quantity,
                "remaining": quantity,
                "closed_quantity": 0.0,
                "close_value": 0.0,
                "gross_pnl": 0.0,
                "close_ts_ms": None,
                "close_fill_ids": [],
                "close_details": [],
            }
            (open_buys if side == "buy" else open_sells).append(leg)

        def close_legs(
            queue: list[dict[str, Any]], fill: dict[str, Any], quantity: float
        ) -> float:
            available = quantity
            while available > 1e-9 and queue:
                leg = queue[0]
                matched = min(available, leg["remaining"])
                close_price = float(fill["price"])
                leg["remaining"] -= matched
                leg["closed_quantity"] += matched
                leg["close_value"] += matched * close_price
                leg["close_ts_ms"] = int(fill["market_ts_ms"])
                leg["close_fill_ids"].append(int(fill["id"]))
                leg["close_details"].append({
                    "fill_id": int(fill["id"]),
                    "market_ts_ms": int(fill["market_ts_ms"]),
                    "price": close_price,
                    "quantity": matched,
                })
                if leg["open_side"] == "buy":
                    leg["gross_pnl"] += matched * (
                        close_price - leg["open_price"]
                    )
                else:
                    leg["gross_pnl"] += matched * (
                        leg["open_price"] - close_price
                    )
                available -= matched
                if leg["remaining"] <= 1e-9:
                    leg["close_price"] = (
                        leg["close_value"] / leg["closed_quantity"]
                    )
                    leg["holding_seconds"] = max(
                        0.0,
                        (leg["close_ts_ms"] - leg["open_ts_ms"]) / 1000,
                    )
                    completed.append(leg)
                    queue.pop(0)
            return available

        for fill in strategy_fills:
            quantity = float(fill["quantity"])
            if lot_aware:
                lot_kind = fill.get("lot_kind")
                lot_id = int(fill["lot_id"])
                if fill["side"] == "buy" and lot_kind == "base":
                    unmatched = close_legs(open_sells, fill, quantity)
                    if unmatched > 1e-9:
                        open_leg(fill, "buy", unmatched)
                elif fill["side"] == "buy":
                    open_leg(fill, "buy", quantity)
                    buy_legs_by_lot.setdefault(lot_id, []).append(open_buys[-1])
                elif lot_kind == "base":
                    open_leg(fill, "sell", quantity)
                else:
                    queue = buy_legs_by_lot.setdefault(lot_id, [])
                    unmatched = close_legs(queue, fill, quantity)
                    open_buys[:] = [
                        leg for leg in open_buys
                        if leg["remaining"] > 1e-9
                    ]
                    if unmatched > 1e-9:
                        open_leg(fill, "sell", unmatched)
                inventory += quantity if fill["side"] == "buy" else -quantity
                continue

            # Compatibility path for legacy rows without a lot id.
            if fill["side"] == "buy":
                closing = min(quantity, max(0.0, baseline - inventory))
                unmatched = close_legs(open_sells, fill, closing)
                if unmatched > 1e-9:
                    open_leg(fill, "buy", unmatched)
                opening = quantity - closing
                open_leg(fill, "buy", opening)
                inventory += quantity
            else:
                closing = min(quantity, max(0.0, inventory - baseline))
                unmatched = close_legs(open_buys, fill, closing)
                if unmatched > 1e-9:
                    open_leg(fill, "sell", unmatched)
                opening = quantity - closing
                open_leg(fill, "sell", opening)
                inventory -= quantity

        for leg in (*open_buys, *open_sells):
            if leg["remaining"] > 1e-9:
                unfinished.append(leg)

    completed.sort(key=lambda leg: (leg["close_ts_ms"], leg["open_ts_ms"]))
    unfinished.sort(key=lambda leg: (leg["open_ts_ms"], leg["strategy_id"]))
    return completed, unfinished


class MakerDashboardReader:
    """Read-only view of the live maker simulation stored in SQLite."""

    def __init__(
        self, database: Path, *, stock_code: str = "600900.SH",
        parameters: MakerParameters | None = None,
    ) -> None:
        self.database = database.resolve()
        self.stock_code = stock_code
        self.parameters = parameters or MakerParameters()
        if not self.database.exists():
            raise RuntimeError(f"数据库不存在: {self.database}")
        uri = self.database.as_uri() + "?mode=ro"
        self.connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA query_only=ON")
        self.connection.execute("PRAGMA busy_timeout=2000")

    def close(self) -> None:
        self.connection.close()

    def snapshot(
        self, market_date: str, bond_code: str, *, recent_fills: int = 16,
        strategy_ids: tuple[str, ...] | None = None,
        include_assessment: bool = True,
    ) -> dict[str, Any]:
        connection = self.connection
        strategy_filter = ""
        strategy_values: tuple[str, ...] = ()
        if strategy_ids is not None:
            placeholders = ",".join("?" for _ in strategy_ids) or "NULL"
            strategy_filter = f" AND strategy_id IN ({placeholders})"
            strategy_values = strategy_ids
        connection.execute("BEGIN")
        try:
            market = connection.execute(
                """SELECT id,market_ts_ms,market_time,last_price,previous_close,
                          ask_price_1,ask_price_2,ask_price_3,ask_price_4,ask_price_5,
                          bid_price_1,bid_price_2,bid_price_3,bid_price_4,bid_price_5,
                          ask_volume_1,ask_volume_2,ask_volume_3,ask_volume_4,ask_volume_5,
                          bid_volume_1,bid_volume_2,bid_volume_3,bid_volume_4,bid_volume_5
                   FROM raw_ticks WHERE market_date=? AND code=?
                   ORDER BY market_ts_ms DESC,id DESC LIMIT 1""",
                (market_date, bond_code),
            ).fetchone()
            assessment = None
            if include_assessment and market is not None:
                replay_ticks = _load_ticks(
                    connection, market_date, bond_code,
                    self.stock_code, self.parameters,
                )
                analyzer = MakerAnalyzer(
                    bond_code, self.stock_code, self.parameters,
                )
                latest_bond_tick = None
                for replay_tick in replay_ticks:
                    analyzer.on_tick(replay_tick)
                    if replay_tick.code == bond_code:
                        latest_bond_tick = replay_tick
                if latest_bond_tick is not None:
                    assessment = analyzer.assess_market(
                        latest_bond_tick, latest_bond_tick.previous_close,
                    ).public()
            account_filter = strategy_filter.replace(
                "strategy_id", "a.strategy_id"
            )
            accounts = connection.execute(
                f"""SELECT a.*,m.model_id,m.model_version,m.parent_model_id
                   FROM maker_paper_accounts AS a
                   LEFT JOIN maker_paper_model_assignments AS m
                     ON m.market_date=a.market_date
                    AND m.strategy_id=a.strategy_id
                   WHERE a.market_date=?{account_filter}
                   ORDER BY a.fill_mode,a.strategy_id""",
                (market_date, *strategy_values),
            ).fetchall()
            orders = connection.execute(
                f"""SELECT * FROM maker_paper_orders
                   WHERE market_date=? AND status IN ('open','partial')
                   {strategy_filter}
                   ORDER BY strategy_id,side,limit_price,id""",
                (market_date, *strategy_values),
            ).fetchall()
            lots = connection.execute(
                f"""SELECT * FROM maker_paper_lots
                   WHERE market_date=? AND status='open' AND remaining_quantity>0
                   {strategy_filter}
                   ORDER BY strategy_id,opened_market_ts_ms,id""",
                (market_date, *strategy_values),
            ).fetchall()
            fill_filter = strategy_filter.replace("strategy_id", "f.strategy_id")
            fills = connection.execute(
                f"""SELECT f.*,l.kind AS lot_kind
                   FROM maker_paper_fills AS f
                   LEFT JOIN maker_paper_lots AS l ON l.id=f.lot_id
                   WHERE f.market_date=?{fill_filter}
                   ORDER BY f.market_ts_ms DESC,f.id DESC""",
                (market_date, *strategy_values),
            ).fetchall()
            session = connection.execute(
                """SELECT run_id,started_at_utc,ended_at_utc,status,dropped_callbacks
                   FROM sessions ORDER BY started_at_utc DESC LIMIT 1"""
            ).fetchone()
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return {
            "market_date": market_date,
            "bond_code": bond_code,
            "market": dict(market) if market else None,
            "assessment": assessment,
            "accounts": [dict(row) for row in accounts],
            "orders": [dict(row) for row in orders],
            "lots": [dict(row) for row in lots],
            "fills": [dict(row) for row in fills],
            "session": dict(session) if session else None,
        }

    def fill_marker(
        self, market_date: str,
        strategy_ids: tuple[str, ...] | None = None,
    ) -> tuple[int, int]:
        """Return a cheap marker used to detect new paper fills."""
        strategy_filter = ""
        strategy_values: tuple[str, ...] = ()
        if strategy_ids is not None:
            placeholders = ",".join("?" for _ in strategy_ids) or "NULL"
            strategy_filter = f" AND strategy_id IN ({placeholders})"
            strategy_values = strategy_ids
        row = self.connection.execute(
            f"""SELECT COUNT(*) AS fill_count,COALESCE(MAX(id),0) AS latest_id
                FROM maker_paper_fills
                WHERE market_date=?{strategy_filter}""",
            (market_date, *strategy_values),
        ).fetchone()
        return int(row["fill_count"]), int(row["latest_id"])


def _market_state(snapshot: dict[str, Any], now: datetime) -> tuple[str, str]:
    market = snapshot["market"]
    if market is None:
        return "无行情", "尚未收到该债券行情"
    market_date = snapshot["market_date"]
    if market_date != now.date().isoformat():
        return "历史", f"历史交易日 {market_date}"
    age = max(0.0, now.timestamp() - market["market_ts_ms"] / 1000)
    clock = now.strftime("%H:%M:%S")
    session = snapshot.get("session") or {}
    running = session.get("status") == "running" and session.get("ended_at_utc") is None
    if "11:30:00" < clock < "13:00:00":
        return "午间休市", f"末笔距今 {age:.0f} 秒"
    if clock < "09:25:00":
        return "盘前", f"末笔距今 {age:.0f} 秒"
    if clock >= "15:30:00":
        return "已收盘", f"末笔距今 {age:.0f} 秒"
    if running and age <= 10:
        return "实时", f"延迟约 {age:.1f} 秒"
    if running:
        return "行情停滞", f"末笔距今 {age:.0f} 秒"
    return "采集未运行", f"末笔距今 {age:.0f} 秒"


def _inventory_direction(account: dict[str, Any]) -> str:
    difference = float(account["inventory"]) - float(account["initial_inventory"])
    if difference > 1e-9:
        return f"加仓 {_quantity(difference)}"
    if difference < -1e-9:
        return f"待回补 {_quantity(-difference)}"
    return "底仓持平"


def _book_lines(market: dict[str, Any] | None) -> list[str]:
    if market is None:
        return ["  暂无盘口数据"]
    lines = []
    # MiniQMT bond book volume is in hands; one hand is 10 bonds.
    for level in range(5, 0, -1):
        lines.append(
            f"  卖{level}  {_price(market[f'ask_price_{level}'])}  "
            f"{_pad(_quantity(market[f'ask_volume_{level}'] * 10), 10, right=True)} 张"
        )
    lines.append("  " + "-" * 29)
    for level in range(1, 6):
        lines.append(
            f"  买{level}  {_price(market[f'bid_price_{level}'])}  "
            f"{_pad(_quantity(market[f'bid_volume_{level}'] * 10), 10, right=True)} 张"
        )
    return lines


def _confidence_label(value: float | int | None) -> str:
    confidence = float(value or 0.0)
    if confidence >= 0.70:
        return "高"
    if confidence >= 0.45:
        return "中"
    return "低"


def _trader_thinking_lines(snapshot: dict[str, Any]) -> list[str]:
    assessment = snapshot.get("assessment")
    market = snapshot.get("market")
    lines = [
        "[交易员思考与应对预案]  因果纸面判断，供复盘纠正，不发送真实委托",
    ]
    if not assessment or not market:
        lines.append("  暂无足够行情生成合理价和趋势判断")
        return lines

    score = int(assessment["state_score"])
    score_text = f"{score:+d}"
    lines.append(
        f"  合理定价 {_price(assessment['reference_price'])}  "
        f"合理区间 {_price(assessment['reference_low'])}—"
        f"{_price(assessment['reference_high'])}  "
        f"来源 {REFERENCE_LABELS.get(assessment['reference_source'], assessment['reference_source'])}  "
        f"参考置信度 {_confidence_label(assessment['reference_confidence'])}"
    )
    lines.append(
        f"  当前状态 {STATE_LABELS.get(assessment['state'], assessment['state'])}  "
        f"方向评分 {score_text}  "
        f"状态置信度 {_confidence_label(assessment['state_confidence'])}"
    )
    lines.append("  判断依据：")
    for evidence in assessment.get("evidence") or ["方向证据不足"]:
        lines.append(f"    - {evidence}")

    accounts = snapshot.get("accounts") or []
    account = next(
        (item for item in accounts if item.get("fill_mode") == "priority"),
        accounts[0] if accounts else None,
    )
    if account is not None:
        difference = (
            float(account["inventory"]) - float(account["initial_inventory"])
        )
        if difference > 1e-9:
            inventory_text = (
                f"已有额外仓{_quantity(difference)}张，优先随当前合理区维护退出"
            )
        elif difference < -1e-9:
            inventory_text = (
                f"底仓缺口{_quantity(-difference)}张，需要按当前趋势安排回补"
            )
        else:
            inventory_text = "底仓持平，仍有现金时可等待下一次低接"
        lines.append(f"  库存判断：{inventory_text}")

        account_orders = [
            order for order in snapshot.get("orders") or []
            if order["strategy_id"] == account["strategy_id"]
        ]
        if account_orders:
            descriptions = []
            for order in account_orders:
                remaining = float(order["quantity"]) - float(order["filled_quantity"])
                descriptions.append(
                    f"{SIDE_LABELS.get(order['side'], order['side'])}"
                    f"{KIND_LABELS.get(order['kind'], order['kind'])}"
                    f" {_quantity(remaining)}@{_price(order['limit_price'])}"
                )
            lines.append("  当前准备：" + "；".join(descriptions))
        else:
            lines.append("  当前准备：暂无有效模拟挂单，等待新的价格或趋势证据")

    windfall_orders = [
        order for order in snapshot.get("orders") or []
        if order["strategy_id"].endswith("_super_windfall")
    ]
    if windfall_orders:
        order = windfall_orders[0]
        remaining = float(order["quantity"]) - float(order["filled_quantity"])
        lines.append(
            "  超级捡漏：独立额度预埋"
            f"{_quantity(remaining)}张@{_price(order['limit_price'])}，"
            "不占普通做T库存和现金"
        )

    spread = float(market["ask_price_1"]) - float(market["bid_price_1"])
    state = assessment["state"]
    if state == "stable":
        if spread >= 0.20:
            plan = "盘口平稳且价差充足，围绕买一承接、卖一退出，持续周转"
        else:
            plan = "盘口平稳但价差偏小，减少无效成交，等待空间重新打开"
    elif state == "possible_rise":
        plan = "提高买入积极度，允许在更新后的合理区补库存，同时防范假突破"
    elif state == "rising":
        plan = "优先保证正确库存；合理区即可买，强势证据连续时不被上一笔盈亏束缚"
    elif state == "possible_fall":
        plan = "下调合理价假设、减少追买，观察卖压是否继续确认"
    else:
        plan = "按下降状态处理，降低买入积极度并及时调整额外库存的退出报价"
    lines.append(f"  当前思路：{plan}")

    if float(assessment.get("largest_ask_gap") or 0.0) >= 0.15:
        upward = (
            "若近端卖墙被真实快速吃尽并向上断档：有货就抬到新卖一附近；"
            "无货且只剩小尾量、库存有容量时才考虑扫尾"
        )
    else:
        upward = (
            "若主动买入持续、买一和卖一同步上移：提高合理价并逐步提高买入积极度"
        )
    downward = (
        "若卖一连续下压、低价主动卖出增加或买一后退：下调合理区、取消追价，"
        "按新盘口重新安排退出和低接"
    )
    lines.append(f"  向上应对：{upward}")
    lines.append(f"  向下应对：{downward}")
    return lines


def render_dashboard(snapshot: dict[str, Any], *, now: datetime | None = None) -> str:
    now = now or datetime.now(SHANGHAI)
    market = snapshot["market"]
    state, state_detail = _market_state(snapshot, now)
    latest_actions: dict[str, dict[str, Any]] = {}
    for fill in snapshot["fills"]:
        latest_actions.setdefault(fill["strategy_id"], fill)
    bond_name = str(snapshot.get("bond_name") or "").strip()
    bond_label = (
        f"{bond_name}（{snapshot['bond_code']}）"
        if bond_name else str(snapshot["bond_code"])
    )
    title = (
        f"{bond_label} 做市模拟盘实时看板  "
        "[纯模拟 / 只读SQLite / 不发送券商订单]"
    )

    lines = [
        title,
        "=" * 78,
        f"系统时间 {now.strftime('%Y-%m-%d %H:%M:%S')}  "
        f"交易日 {snapshot['market_date']}  状态 {state} ({state_detail})",
    ]
    if market:
        spread = float(market["ask_price_1"]) - float(market["bid_price_1"])
        lines.append(
            f"债券 {bond_label}  行情时间 {market['market_time'][:8]}  "
            f"最新 {_price(market['last_price'])}  "
            f"买一/卖一 {_price(market['bid_price_1'])}/{_price(market['ask_price_1'])}  "
            f"价差 {spread:.3f}"
        )
    else:
        lines.append(f"债券 {bond_label}  暂无行情")

    lines.extend(["", "[五档盘口]  数量单位：张", *_book_lines(market), ""])
    lines.extend([*_trader_thinking_lines(snapshot), ""])
    if snapshot["accounts"]:
        assumptions = {
            (
                float(account["initial_inventory"]),
                float(account["initial_cash"]),
                float(account["maximum_inventory"]),
            )
            for account in snapshot["accounts"]
        }
        if len(assumptions) == 1:
            initial_inventory, initial_cash, maximum_inventory = assumptions.pop()
            lines.append(
                "[每日账户假设] "
                f"每个口径开盘重置为底仓 {_quantity(initial_inventory)} 张 + "
                f"现金 {initial_cash:,.2f} 元；最大库存 {_quantity(maximum_inventory)} 张"
            )
            lines.append("")
        else:
            lines.append(
                "[每日账户假设] 普通做T账户与超级捡漏独立额度分开核算，"
                "库存、现金和收益不得合并"
            )
            lines.append("")
    lines.append("[账户与持仓方向]  盈亏为模型盯市毛收益，未扣费用")
    lines.append(
        "  "
        + _pad("口径", 10)
        + _pad("当前库存", 12, right=True)
        + "  "
        + _pad("相对底仓", 16)
        + _pad("现金", 15, right=True)
        + _pad("盯市盈亏", 14, right=True)
        + _pad("成交", 8, right=True)
        + "  最近动作"
    )
    if not snapshot["accounts"]:
        lines.append("  尚无做市模拟账户；请确认 [maker_paper] enabled=true。")
    for account in snapshot["accounts"]:
        action = latest_actions.get(account["strategy_id"])
        action_text = "无"
        if action:
            action_text = (
                f"{_clock(action['market_ts_ms'])} "
                f"{SIDE_LABELS.get(action['side'], action['side'])}"
                f"{_quantity(action['quantity'])}@{_price(action['price'])}"
            )
        lines.append(
            "  "
            + _pad(_strategy_label(
                account["strategy_id"], account["fill_mode"]
            ), 10)
            + _pad(_quantity(account["inventory"]), 12, right=True)
            + "  "
            + _pad(_inventory_direction(account), 16)
            + _pad(f"{account['cash']:,.2f}", 15, right=True)
            + _pad(f"{account['trading_pnl']:+,.2f}", 14, right=True)
            + _pad(_quantity(account["fills"]), 8, right=True)
            + "  "
            + action_text
        )

    lines.extend(["", "[当前模拟挂单]"])
    if not snapshot["orders"]:
        deficits = [
            account for account in snapshot["accounts"]
            if float(account["inventory"]) + 1e-9
                < float(account["initial_inventory"])
        ]
        if deficits:
            lines.append("  无当前挂单；以下账户正在等待有效盘口或回补时段：")
            for account in deficits:
                missing = float(account["initial_inventory"]) - float(account["inventory"])
                lines.append(
                    f"  {_strategy_label(account['strategy_id'], account['fill_mode'])} "
                    f"待回补 {_quantity(missing)} 张"
                )
        else:
            lines.append("  无当前挂单")
    else:
        lines.append("  口径       方向  类型       价格      剩余数量    前方队列    目标价")
        for order in snapshot["orders"]:
            remaining = float(order["quantity"]) - float(order["filled_quantity"])
            lines.append(
                f"  {_pad(_strategy_label(order['strategy_id']), 10)}"
                f" {_pad(SIDE_LABELS.get(order['side'], order['side']), 5)}"
                f" {_pad(KIND_LABELS.get(order['kind'], order['kind']), 10)}"
                f" {_pad(_price(order['limit_price']), 9, right=True)}"
                f" {_pad(_quantity(remaining), 12, right=True)}"
                f" {_pad(_quantity(order['queue_ahead']), 11, right=True)}"
                f" {_pad(_price(order['target_price']), 9, right=True)}"
            )

    lines.extend(["", "[当前持仓批次]"])
    if not snapshot["lots"]:
        lines.append("  无持仓批次")
    else:
        lines.append("  口径       批次类型   开仓时间   成本价     剩余数量    目标价")
        for lot in snapshot["lots"]:
            lines.append(
                f"  {_pad(_strategy_label(lot['strategy_id']), 10)}"
                f" {_pad(KIND_LABELS.get(lot['kind'], lot['kind']), 10)}"
                f" {_pad(_clock(lot['opened_market_ts_ms']), 10)}"
                f" {_pad(_price(lot['entry_price']), 10, right=True)}"
                f" {_pad(_quantity(lot['remaining_quantity']), 12, right=True)}"
                f" {_pad(_price(lot['target_price']), 9, right=True)}"
            )

    completed_trades, unfinished_trades = build_daily_trades(
        snapshot["fills"], snapshot["accounts"]
    )
    lines.extend([
        "",
        "=" * 78,
        "[以下模拟账户彼此独立，分开展示，收益不能相加]",
    ])
    for account in snapshot["accounts"]:
        strategy_id = account["strategy_id"]
        account_trades = [
            trade for trade in completed_trades
            if trade["strategy_id"] == strategy_id
        ]
        account_unfinished = [
            trade for trade in unfinished_trades
            if trade["strategy_id"] == strategy_id
        ]
        account_fills = sorted(
            (
                fill for fill in snapshot["fills"]
                if fill["strategy_id"] == strategy_id
            ),
            key=lambda fill: (fill["market_ts_ms"], fill["id"]),
        )
        wins = sum(trade["gross_pnl"] > 0 for trade in account_trades)
        losses = sum(trade["gross_pnl"] < 0 for trade in account_trades)
        flats = len(account_trades) - wins - losses
        closed_pnl = sum(trade["gross_pnl"] for trade in account_trades)

        lines.extend([
            "",
            "#" * 78,
            f"模拟账户：{_mode_title(account['fill_mode'])}；"
            f"模型 {account.get('model_id') or '历史未登记'}",
            f"今日账户结果：盯市毛收益 {account['trading_pnl']:+,.2f}元；"
            f"完整交易 {len(account_trades)}笔（盈利{wins} / 亏损{losses} / 持平{flats}）；"
            f"闭环毛利润 {closed_pnl:+,.2f}元；"
            f"当前库存 {_quantity(account['inventory'])}张（{_inventory_direction(account)}）",
            "",
            "[本账户：今日完整交易]",
        ])
        if not account_trades:
            lines.append("  今日尚无已闭环交易")
        else:
            lines.append(
                "  序号  策略          方向   数量   开仓时间  开仓价   "
                "平仓时间  平仓均价  持有时长   毛利润"
            )
            for index, trade in enumerate(account_trades, 1):
                pnl_text = f"{trade['gross_pnl']:+,.2f}"
                lines.append(
                    f"  {index:>3}  {_pad(trade['strategy'], 13)}"
                    f" {_pad(trade['direction'], 7)}"
                    f" {_pad(_quantity(trade['quantity']), 7, right=True)}"
                    f"  {_clock(trade['open_ts_ms'])}"
                    f" {_pad(_price(trade['open_price']), 9, right=True)}"
                    f"  {_clock(trade['close_ts_ms'])}"
                    f" {_pad(_price(trade['close_price']), 10, right=True)}"
                    f"  {_duration(trade['holding_seconds'])}"
                    f" {_pad(pnl_text, 11, right=True)}"
                )
                if len(trade["close_details"]) > 1:
                    details = " + ".join(
                        f"{_clock(detail['market_ts_ms'])} "
                        f"{_quantity(detail['quantity'])}@{_price(detail['price'])}"
                        for detail in trade["close_details"]
                    )
                    lines.append(f"       平仓成交明细: {details}")

        lines.extend(["", "[本账户：今日尚未闭环交易]"])
        if not account_unfinished:
            lines.append("  无；当前所有库存偏离均已完成买卖配对。")
        else:
            lines.append("  策略          状态       开仓时间  开仓价   原数量  未闭环数量")
            for trade in account_unfinished:
                status = "待卖出" if trade["open_side"] == "buy" else "待回补"
                lines.append(
                    f"  {_pad(trade['strategy'], 13)}"
                    f" {_pad(status, 10)}"
                    f" {_clock(trade['open_ts_ms'])}"
                    f" {_pad(_price(trade['open_price']), 9, right=True)}"
                    f" {_pad(_quantity(trade['quantity']), 9, right=True)}"
                    f" {_pad(_quantity(trade['remaining']), 12, right=True)}"
                )

        lines.extend(["", "[本账户：今日全部成交流水]"])
        if not account_fills:
            lines.append("  今日尚无模拟成交")
        else:
            lines.append("  序号  时间      策略           方向    数量       价格     成交后库存")
            for index, fill in enumerate(account_fills, 1):
                lines.append(
                    f"  {index:>3}  {_clock(fill['market_ts_ms'])}  "
                    f"{_pad(_flow_strategy(fill), 15)}"
                    f" {_pad(SIDE_LABELS.get(fill['side'], fill['side']), 6)}"
                    f" {_pad(_quantity(fill['quantity']), 10, right=True)}"
                    f" {_pad(_price(fill['price']), 10, right=True)}"
                    f" {_pad(_quantity(fill['inventory_after']), 12, right=True)}"
                )

    session = snapshot.get("session") or {}
    lines.extend([
        "",
        "-" * 78,
        f"采集进程状态: {session.get('status', '未知')}  "
        f"回调丢失: {session.get('dropped_callbacks', '未知')}  "
        "09:25-15:30内有新成交立即刷新、无成交每分钟刷新；"
        "其余时间休眠。按 Ctrl+C 退出看板（不停止后台采集）。",
    ])
    return "\n".join(lines)


def _enable_ansi() -> None:
    if os.name != "nt" or not sys.stdout.isatty():
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except (AttributeError, OSError):
        pass


def run_dashboard(
    database: Path,
    bond_code: str,
    market_date: str,
    *,
    stock_code: str = "600900.SH",
    parameters: MakerParameters | None = None,
    bond_name: str | None = None,
    interval_seconds: float = 60.0,
    once: bool = False,
    recent_fills: int = 16,
    strategy_ids: tuple[str, ...] | None = None,
    follow_current_date: bool = True,
) -> int:
    if interval_seconds <= 0:
        raise RuntimeError("刷新间隔必须大于0秒")
    if recent_fills <= 0:
        raise RuntimeError("最近成交条数必须大于0")
    _enable_ansi()
    reader = MakerDashboardReader(
        database, stock_code=stock_code, parameters=parameters,
    )
    try:
        if once:
            snapshot = reader.snapshot(
                market_date, bond_code, recent_fills=recent_fills,
                strategy_ids=strategy_ids,
            )
            snapshot["bond_name"] = bond_name
            print(render_dashboard(snapshot), flush=True)
            return 0

        last_fill_marker: tuple[int, int] | None = None
        last_rendered = 0.0
        inactive_notice_shown = False
        while True:
            now = datetime.now(SHANGHAI)
            if not dashboard_refresh_active(now):
                if last_fill_marker is None and not inactive_notice_shown:
                    next_start = next_dashboard_refresh_start(now)
                    print(
                        "看板当前休眠：连续刷新时段为工作日09:25-15:30；"
                        f"下次恢复 {next_start.strftime('%Y-%m-%d %H:%M')}。"
                        "按 Ctrl+C 可退出。",
                        flush=True,
                    )
                    inactive_notice_shown = True
                wait_seconds = max(
                    1.0,
                    (next_dashboard_refresh_start(now) - now).total_seconds(),
                )
                time.sleep(min(60.0, wait_seconds))
                continue

            inactive_notice_shown = False
            if follow_current_date:
                current_date = now.date().isoformat()
                if market_date != current_date:
                    market_date = current_date
                    last_fill_marker = None
                    last_rendered = 0.0
            fill_marker = reader.fill_marker(market_date, strategy_ids)
            now_monotonic = time.monotonic()
            fill_changed = (
                last_fill_marker is not None and fill_marker != last_fill_marker
            )
            should_render = (
                last_fill_marker is None or fill_changed
                or now_monotonic - last_rendered >= interval_seconds
            )
            if should_render:
                snapshot = reader.snapshot(
                    market_date, bond_code, recent_fills=recent_fills,
                    strategy_ids=strategy_ids,
                )
                snapshot["bond_name"] = bond_name
                output = render_dashboard(snapshot)
                if sys.stdout.isatty():
                    print("\x1b[2J\x1b[H", end="")
                print(output, flush=True)
                last_rendered = now_monotonic
                fills = snapshot["fills"]
                fill_marker = (
                    len(fills), int(fills[0]["id"]) if fills else 0
                )
            last_fill_marker = fill_marker
            # Poll quietly to show a new fill promptly; without a fill the
            # visible console updates only at interval_seconds.
            time.sleep(min(1.0, interval_seconds))
    except KeyboardInterrupt:
        print("\n看板已退出；后台行情采集不受影响。")
        return 0
    finally:
        reader.close()
