from __future__ import annotations

import argparse
import bisect
import json
import mimetypes
import sqlite3
import sys
import threading
import time
from datetime import datetime, time as clock_time
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


APP_DIR = Path(__file__).resolve().parent
REPO_DIR = APP_DIR.parent
DEFAULT_DATABASE = REPO_DIR / "data" / "zhaiquant.sqlite3"
SOURCE_DIR = REPO_DIR / "src"
SHANGHAI_TZ = datetime.now().astimezone().tzinfo

BONDS = {
    "132026.SH": {"name": "G三峡EB2", "stock": "600900.SH"},
    "132024.SH": {"name": "26江铜EB", "stock": "600362.SH"},
}

MODEL_META = {
    "maker_priority_v1_1": {
        "short": "第一顺位 1.1",
        "status": "生产",
        "branch": "priority",
        "color": "gold",
        "note": "改善一厘的高频做T生产基线",
    },
    "maker_queue_v1_0": {
        "short": "排队 1.0",
        "status": "生产",
        "branch": "queue",
        "color": "blue",
        "note": "消耗真实显示前队的排队基线",
    },
    "maker_windfall_v1_0": {
        "short": "捡漏 1.0",
        "status": "试验",
        "branch": "windfall",
        "color": "lime",
        "note": "独立10张异常深价额度，退出规则待校准",
    },
    "maker_priority_v1_37_candidate": {
        "short": "第一顺位 1.37",
        "status": "候选·未晋级",
        "branch": "priority",
        "color": "violet",
        "note": "近墙低卖一主动低接候选",
    },
    "maker_priority_v1_43_candidate": {
        "short": "第一顺位 1.43",
        "status": "候选·未晋级",
        "branch": "priority",
        "color": "coral",
        "note": "即时可见卖墙扫尾恢复底仓候选",
    },
    "maker_queue_v1_17_candidate": {
        "short": "排队 1.17",
        "status": "候选·未晋级",
        "branch": "queue",
        "color": "teal",
        "note": "正常交易至15:30的排队候选",
    },
}

MODEL_ORDER = [
    "maker_priority_v1_1",
    "maker_queue_v1_0",
    "maker_windfall_v1_0",
    "maker_priority_v1_37_candidate",
    "maker_priority_v1_43_candidate",
    "maker_queue_v1_17_candidate",
]

KIND_LABELS = {
    "base": "期初底仓",
    "low_bid_reversion": "低价承接",
    "inventory_replenish": "客户底仓回补",
    "sweep_tail": "扫尾跟随",
    "deep_discount_sweep": "深度折价主动买",
    "inventory_exit": "库存卖出",
    "inventory_risk_exit": "下行风险退出",
    "super_windfall": "超级捡漏",
    "dynamic_customer_base_replenish": "动态底仓回补",
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


def _json_loads(value: str | None) -> dict[str, Any]:
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def _clock(ts_ms: int | float | None) -> str:
    if not ts_ms:
        return "--:--:--"
    return datetime.fromtimestamp(float(ts_ms) / 1000).strftime("%H:%M:%S")


def refresh_window_active(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    return now.weekday() < 5 and clock_time(9, 25) <= now.time() < clock_time(15, 30)


def _read_only_connection(database: Path) -> sqlite3.Connection:
    uri = database.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=2.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=2000")
    return connection


def _decorate_assessment(assessment: dict[str, Any]) -> dict[str, Any]:
    assessment = dict(assessment)
    assessment["state_label"] = STATE_LABELS.get(
        assessment.get("state"), assessment.get("state", "--")
    )
    assessment["reference_source_label"] = REFERENCE_LABELS.get(
        assessment.get("reference_source"),
        assessment.get("reference_source", "--"),
    )
    assessment["evidence"] = [
        text for text in assessment.get("evidence", [])
        if isinstance(text, str) and "�" not in text
    ]
    return assessment


@lru_cache(maxsize=16)
def _assessment_timeline(
    database_path: str,
    database_mtime_ns: int,
    market_date: str,
    bond_code: str,
) -> tuple[tuple[int, ...], tuple[dict[str, Any], ...]]:
    """Build one causal assessment per bond tick and cache the trading day."""
    del database_mtime_ns  # Used only to invalidate the cache when SQLite changes.
    if str(SOURCE_DIR) not in sys.path:
        sys.path.insert(0, str(SOURCE_DIR))
    from zhaiquant.maker import MakerAnalyzer, MakerParameters, _load_ticks

    database = Path(database_path)
    parameters = MakerParameters()
    connection = _read_only_connection(database)
    try:
        ticks = _load_ticks(
            connection, market_date, bond_code, BONDS[bond_code]["stock"], parameters
        )
    finally:
        connection.close()
    analyzer = MakerAnalyzer(bond_code, BONDS[bond_code]["stock"], parameters)
    timestamps: list[int] = []
    assessments: list[dict[str, Any]] = []
    for tick in ticks:
        analyzer.on_tick(tick)
        if tick.code == bond_code:
            timestamps.append(int(tick.market_ts_ms))
            assessments.append(_decorate_assessment(
                analyzer.assess_market(tick, tick.previous_close).public()
            ))
    return tuple(timestamps), tuple(assessments)


def _assessment_at(
    database: Path, market_date: str, bond_code: str, target_ts_ms: int
) -> dict[str, Any] | None:
    try:
        timestamps, assessments = _assessment_timeline(
            str(database.resolve()), database.stat().st_mtime_ns, market_date, bond_code
        )
        index = bisect.bisect_right(timestamps, target_ts_ms) - 1
        return dict(assessments[index]) if index >= 0 else None
    except Exception:
        return None


def _fallback_assessment(market: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    valid_midpoints = [
        (float(item["bid"]) + float(item["ask"])) / 2
        for item in history[-30:]
        if float(item["bid"] or 0) > 0 and float(item["ask"] or 0) > 0
    ]
    reference = (
        sorted(valid_midpoints)[len(valid_midpoints) // 2]
        if valid_midpoints
        else float(market["previous_close"])
    )
    return {
        "reference_price": round(reference, 3),
        "reference_low": round(reference - 0.015, 3),
        "reference_high": round(reference + 0.015, 3),
        "reference_source": "persistent_inside_market" if valid_midpoints else "previous_close",
        "reference_source_label": "近期盘口中点（展示降级计算）" if valid_midpoints else REFERENCE_LABELS["previous_close"],
        "reference_confidence": 0.28,
        "state": "stable",
        "state_label": "平稳",
        "state_score": 0,
        "state_confidence": 0.30,
        "recent_buy_bonds": 0,
        "recent_sell_bonds": 0,
        "evidence": ["策略分析器不可用，当前只展示只读盘口统计。"],
    }


def _account_view(row: sqlite3.Row, market: dict[str, Any]) -> dict[str, Any]:
    model_id = row["model_id"] or row["strategy_id"]
    meta = MODEL_META.get(
        model_id,
        {
            "short": model_id,
            "status": "历史",
            "branch": row["fill_mode"],
            "color": "blue",
            "note": "已登记纸面模型",
        },
    )
    inventory = float(row["inventory"])
    initial = float(row["initial_inventory"])
    mark = float(market["last_price"])
    cash = float(row["cash"])
    initial_cash = float(row["initial_cash"])
    mark_pnl = cash + inventory * mark - (initial_cash + initial * float(market["previous_close"]))
    return {
        "strategy_id": row["strategy_id"],
        "model_id": model_id,
        "model_version": row["model_version"],
        "parent_model_id": row["parent_model_id"],
        "fill_mode": row["fill_mode"],
        "inventory": inventory,
        "initial_inventory": initial,
        "maximum_inventory": float(row["maximum_inventory"]),
        "customer_base_short": max(0.0, initial - inventory),
        "extra_inventory": max(0.0, inventory - initial),
        "cash": cash,
        "trading_pnl": float(row["trading_pnl"]),
        "mark_pnl": mark_pnl,
        "fills": int(row["fills"]),
        "last_market_time": _clock(row["last_market_ts_ms"]),
        **meta,
    }


def load_replay_metadata(database: Path, bond_code: str) -> dict[str, Any]:
    if bond_code not in BONDS:
        raise ValueError(f"unsupported bond: {bond_code}")
    connection = _read_only_connection(database)
    try:
        rows = connection.execute(
            """SELECT r.market_date,COUNT(*) AS tick_count,
                      MIN(r.market_ts_ms) AS start_ts_ms,
                      MAX(r.market_ts_ms) AS end_ts_ms,
                      EXISTS(
                          SELECT 1 FROM maker_paper_model_assignments m
                          WHERE m.market_date=r.market_date AND m.bond_code=r.code
                      ) AS has_accounts
               FROM raw_ticks r WHERE r.code=?
               GROUP BY r.market_date,r.code ORDER BY r.market_date DESC""",
            (bond_code,),
        ).fetchall()
    finally:
        connection.close()
    dates = [
        {
            "date": row["market_date"],
            "tick_count": int(row["tick_count"]),
            "start_ts_ms": int(row["start_ts_ms"]),
            "end_ts_ms": int(row["end_ts_ms"]),
            "start_time": _clock(row["start_ts_ms"]),
            "end_time": _clock(row["end_ts_ms"]),
            "has_accounts": bool(row["has_accounts"]),
        }
        for row in rows
    ]
    return {
        "bond": {"code": bond_code, **BONDS[bond_code]},
        "dates": dates,
        "paper_only": True,
        "database_read_only": True,
    }


def load_snapshot(
    database: Path,
    bond_code: str,
    *,
    market_date: str | None = None,
    target_ts_ms: int | None = None,
    action_model_id: str | None = None,
) -> dict[str, Any]:
    if bond_code not in BONDS:
        raise ValueError(f"unsupported bond: {bond_code}")
    if not database.exists():
        raise FileNotFoundError(database)

    connection = _read_only_connection(database)
    try:
        if market_date is None:
            market_date_row = connection.execute(
                "SELECT MAX(market_date) AS market_date FROM raw_ticks WHERE code=?",
                (bond_code,),
            ).fetchone()
            market_date = market_date_row["market_date"] if market_date_row else None
        if not market_date:
            raise RuntimeError(f"{bond_code} 没有行情")

        bounds = connection.execute(
            """SELECT MIN(market_ts_ms) AS start_ts_ms,
                      MAX(market_ts_ms) AS end_ts_ms,COUNT(*) AS tick_count
               FROM raw_ticks WHERE market_date=? AND code=?""",
            (market_date, bond_code),
        ).fetchone()
        if not bounds or bounds["start_ts_ms"] is None:
            raise RuntimeError(f"{bond_code} 在 {market_date} 没有行情")
        start_ts_ms = int(bounds["start_ts_ms"])
        end_ts_ms = int(bounds["end_ts_ms"])
        requested_ts_ms = int(target_ts_ms) if target_ts_ms is not None else end_ts_ms
        effective_ts_ms = min(end_ts_ms, max(start_ts_ms, requested_ts_ms))
        replay_mode = target_ts_ms is not None

        market_row = connection.execute(
            """SELECT * FROM raw_ticks
               WHERE market_date=? AND code=? AND market_ts_ms<=?
               ORDER BY market_ts_ms DESC,id DESC LIMIT 1""",
            (market_date, bond_code, effective_ts_ms),
        ).fetchone()
        market = dict(market_row)

        history_cutoff_ts_ms = max(start_ts_ms, effective_ts_ms - 3_600_000)
        history_rows = connection.execute(
            """SELECT market_ts_ms,last_price,bid_price_1,ask_price_1
               FROM raw_ticks WHERE market_date=? AND code=?
                 AND market_ts_ms BETWEEN ? AND ?
               ORDER BY market_ts_ms DESC,id DESC""",
            (market_date, bond_code, history_cutoff_ts_ms, effective_ts_ms),
        ).fetchall()
        history = [
            {
                "ts": int(row["market_ts_ms"]),
                "time": _clock(row["market_ts_ms"]),
                "last": float(row["last_price"]),
                "bid": float(row["bid_price_1"]),
                "ask": float(row["ask_price_1"]),
            }
            for row in reversed(history_rows)
        ]

        accounts_rows = connection.execute(
            """SELECT a.*,m.model_id,m.model_version,m.parent_model_id,m.bond_code
               FROM maker_paper_accounts AS a
               JOIN maker_paper_model_assignments AS m
                 ON m.market_date=a.market_date AND m.strategy_id=a.strategy_id
               WHERE a.market_date=? AND m.bond_code=?""",
            (market_date, bond_code),
        ).fetchall()
        accounts = [_account_view(row, market) for row in accounts_rows]
        accounts.sort(
            key=lambda item: MODEL_ORDER.index(item["model_id"])
            if item["model_id"] in MODEL_ORDER else 999
        )
        strategy_ids = [item["strategy_id"] for item in accounts]
        if strategy_ids:
            placeholders = ",".join("?" for _ in strategy_ids)
            fill_rows_ascending = connection.execute(
                f"""SELECT * FROM maker_paper_fills
                    WHERE market_date=? AND strategy_id IN ({placeholders})
                      AND market_ts_ms<=?
                    ORDER BY market_ts_ms,id""",
                (market_date, *strategy_ids, effective_ts_ms),
            ).fetchall()
            order_rows = connection.execute(
                f"""SELECT * FROM maker_paper_orders
                    WHERE market_date=? AND strategy_id IN ({placeholders})
                      AND created_market_ts_ms<=?
                    ORDER BY created_market_ts_ms DESC,id DESC""",
                (market_date, *strategy_ids, effective_ts_ms),
            ).fetchall()
        else:
            order_rows, fill_rows_ascending = [], []

        latest_fill_by_strategy: dict[str, sqlite3.Row] = {}
        fill_count_by_strategy: dict[str, int] = {}
        filled_by_order: dict[int, float] = {}
        latest_fill_ts_by_order: dict[int, int] = {}
        for row in fill_rows_ascending:
            strategy_id = str(row["strategy_id"])
            latest_fill_by_strategy[strategy_id] = row
            fill_count_by_strategy[strategy_id] = fill_count_by_strategy.get(strategy_id, 0) + 1
            if row["order_id"] is not None:
                order_id = int(row["order_id"])
                filled_by_order[order_id] = filled_by_order.get(order_id, 0.0) + float(row["quantity"])
                latest_fill_ts_by_order[order_id] = int(row["market_ts_ms"])

        reconstructed_rows: list[dict[str, Any]] = []
        for row in accounts_rows:
            account = dict(row)
            latest_fill = latest_fill_by_strategy.get(str(row["strategy_id"]))
            account["cash"] = (
                float(latest_fill["cash_after"]) if latest_fill else float(row["initial_cash"])
            )
            account["inventory"] = (
                float(latest_fill["inventory_after"])
                if latest_fill else float(row["initial_inventory"])
            )
            account["fills"] = fill_count_by_strategy.get(str(row["strategy_id"]), 0)
            account["last_market_ts_ms"] = (
                int(latest_fill["market_ts_ms"]) if latest_fill else 0
            )
            inventory_delta = float(account["inventory"]) - float(row["initial_inventory"])
            if inventory_delta > 1e-9:
                mark = float(market["bid_price_1"])
            elif inventory_delta < -1e-9:
                mark = float(market["ask_price_1"])
            else:
                bid, ask = float(market["bid_price_1"]), float(market["ask_price_1"])
                mark = (bid + ask) / 2 if bid > 0 and ask > 0 else max(bid, ask)
            account["trading_pnl"] = (
                float(account["cash"]) - float(row["initial_cash"]) + inventory_delta * mark
            )
            reconstructed_rows.append(account)
        accounts = [_account_view(row, market) for row in reconstructed_rows]
        accounts.sort(
            key=lambda item: MODEL_ORDER.index(item["model_id"])
            if item["model_id"] in MODEL_ORDER else 999
        )

        model_by_strategy = {item["strategy_id"]: item["model_id"] for item in accounts}
        all_orders: list[dict[str, Any]] = []
        for row in order_rows:
            data = dict(row)
            metadata = _json_loads(data.pop("metadata_json", None))
            model_id = model_by_strategy.get(data["strategy_id"], metadata.get("model_id"))
            order_id = int(data["id"])
            filled_at_target = min(
                float(data["quantity"]), filled_by_order.get(order_id, 0.0)
            )
            terminal_ts_ms = int(data["updated_market_ts_ms"])
            if effective_ts_ms < terminal_ts_ms:
                status_at_target = "partial" if filled_at_target > 1e-9 else "open"
                data["cancel_reason"] = None
                # SQLite keeps only the final queue estimate; exposing it here would
                # leak a value learned after the replay cutoff.
                data["queue_ahead"] = None
                visible_update_ts_ms = max(
                    int(data["created_market_ts_ms"]),
                    latest_fill_ts_by_order.get(order_id, 0),
                )
            else:
                status_at_target = str(data["status"])
                visible_update_ts_ms = terminal_ts_ms
            data["status"] = status_at_target
            data["filled_quantity"] = filled_at_target
            data.update(
                model_id=model_id,
                model_short=MODEL_META.get(model_id, {}).get("short", model_id),
                model_color=MODEL_META.get(model_id, {}).get("color", "blue"),
                kind_label=KIND_LABELS.get(data["kind"], data["kind"]),
                time=_clock(visible_update_ts_ms),
                remaining=max(0.0, float(data["quantity"]) - filled_at_target),
                paper_only=True,
            )
            all_orders.append(data)

        open_orders = [item for item in all_orders if item["status"] in {"open", "partial"}]
        lifecycle = all_orders[:24]
        fills = []
        for row in reversed(fill_rows_ascending[-40:]):
            data = dict(row)
            model_id = model_by_strategy.get(data["strategy_id"])
            data.update(
                model_id=model_id,
                model_short=MODEL_META.get(model_id, {}).get("short", model_id),
                model_color=MODEL_META.get(model_id, {}).get("color", "blue"),
                time=_clock(data["market_ts_ms"]),
                reason_label=KIND_LABELS.get(data["fill_reason"], data["fill_reason"]),
            )
            fills.append(data)

        market_trade_rows = connection.execute(
            """SELECT r.market_ts_ms,r.market_time,r.last_price,
                      c.volume_delta,c.transaction_delta,c.inferred_side,
                      c.side_confidence
               FROM tick_changes c JOIN raw_ticks r ON r.id=c.tick_id
               WHERE r.market_date=? AND r.code=? AND c.market_ts_ms<=?
                 AND c.volume_delta>0
               ORDER BY c.market_ts_ms DESC,c.tick_id DESC LIMIT 60""",
            (market_date, bond_code, effective_ts_ms),
        ).fetchall()
        market_trades = [
            {
                "ts": int(row["market_ts_ms"]),
                "time": _clock(row["market_ts_ms"]),
                "price": float(row["last_price"]),
                "quantity": float(row["volume_delta"]) * 10,
                "transactions": int(row["transaction_delta"]),
                "inferred_side": str(row["inferred_side"] or "unknown"),
                "side_confidence": str(row["side_confidence"] or "none"),
                "side_is_inferred": True,
            }
            for row in market_trade_rows
        ]

        latest_change = connection.execute(
            """SELECT c.* FROM tick_changes c
               JOIN raw_ticks r ON r.id=c.tick_id
               WHERE r.market_date=? AND r.code=? AND c.market_ts_ms<=?
               ORDER BY c.market_ts_ms DESC,c.tick_id DESC LIMIT 1""",
            (market_date, bond_code, effective_ts_ms),
        ).fetchone()
        session = connection.execute(
            """SELECT status,started_at_utc,ended_at_utc,dropped_callbacks
               FROM sessions ORDER BY started_at_utc DESC LIMIT 1"""
        ).fetchone()
    finally:
        connection.close()

    book = {"asks": [], "bids": []}
    for level in range(5, 0, -1):
        book["asks"].append(
            {
                "level": level,
                "price": float(market[f"ask_price_{level}"]),
                "quantity": float(market[f"ask_volume_{level}"]) * 10,
            }
        )
    for level in range(1, 6):
        book["bids"].append(
            {
                "level": level,
                "price": float(market[f"bid_price_{level}"]),
                "quantity": float(market[f"bid_volume_{level}"]) * 10,
            }
        )

    compact_market = {
        "market_date": market_date,
        "market_time": market["market_time"],
        "market_ts_ms": int(market["market_ts_ms"]),
        "last_price": float(market["last_price"]),
        "open_price": float(market["open_price"]),
        "high_price": float(market["high_price"]),
        "low_price": float(market["low_price"]),
        "previous_close": float(market["previous_close"]),
        "bid1": float(market["bid_price_1"]),
        "ask1": float(market["ask_price_1"]),
        "spread": round(float(market["ask_price_1"]) - float(market["bid_price_1"]), 3),
        "change": round(float(market["last_price"]) - float(market["previous_close"]), 3),
        "change_pct": round(
            (float(market["last_price"]) / float(market["previous_close"]) - 1) * 100, 3
        ) if float(market["previous_close"]) else 0,
    }

    assessment = _assessment_at(database, market_date, bond_code, effective_ts_ms)
    if assessment is None:
        assessment = _fallback_assessment(compact_market, history)

    order_by_strategy: dict[str, list[dict[str, Any]]] = {}
    for order in open_orders:
        order_by_strategy.setdefault(order["strategy_id"], []).append(order)
    for account in accounts:
        account["orders"] = order_by_strategy.get(account["strategy_id"], [])
        if account["orders"]:
            first = account["orders"][0]
            verb = "买入" if first["side"] == "buy" else "卖出"
            account["action"] = f"{verb} {first['limit_price']:.3f} × {first['remaining']:,.0f}张"
            account["reason"] = first["kind_label"]
        else:
            account["action"] = "观望 / 无活动订单"
            account["reason"] = "当前条件不足或已收盘"

    actions: list[dict[str, Any]] = []
    for order in all_orders:
        if action_model_id is not None and order["model_id"] != action_model_id:
            continue
        base_event = {
            "bond_code": bond_code,
            "model_id": order["model_id"],
            "model_short": order["model_short"],
            "model_color": order["model_color"],
            "order_id": int(order["id"]),
            "side": order["side"],
            "price": float(order["limit_price"]),
            "kind": order["kind"],
            "kind_label": order["kind_label"],
            "paper_only": True,
        }
        created_ts_ms = int(order["created_market_ts_ms"])
        actions.append({
            **base_event,
            "event_type": "submit",
            "event_label": "挂出买单" if order["side"] == "buy" else "挂出卖单",
            "ts": created_ts_ms,
            "time": _clock(created_ts_ms),
            "quantity": float(order["quantity"]),
            "detail": order["kind_label"],
        })
        terminal_ts_ms = int(order["updated_market_ts_ms"])
        if (
            terminal_ts_ms <= effective_ts_ms
            and terminal_ts_ms > created_ts_ms
            and str(order["status"]) in {"cancelled", "expired", "filled"}
        ):
            terminal_type = str(order["status"])
            if terminal_type in {"cancelled", "expired"}:
                event_type = "cancel"
                event_label = "撤销买单" if order["side"] == "buy" else "撤销卖单"
            elif terminal_type == "filled":
                event_type = "complete"
                event_label = "买单完成" if order["side"] == "buy" else "卖单完成"
            actions.append({
                **base_event,
                "event_type": event_type,
                "event_label": event_label,
                "ts": terminal_ts_ms,
                "time": _clock(terminal_ts_ms),
                "quantity": (
                    float(order["filled_quantity"])
                    if terminal_type == "filled" else float(order["remaining"])
                ),
                "detail": order.get("cancel_reason") or order["kind_label"],
            })

    fill_groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in fill_rows_ascending:
        row_model_id = model_by_strategy.get(row["strategy_id"])
        if action_model_id is not None and row_model_id != action_model_id:
            continue
        key = (
            row["strategy_id"], row["order_id"], row["market_ts_ms"],
            row["side"], row["price"], row["fill_reason"],
        )
        if key not in fill_groups:
            model_id = row_model_id
            fill_groups[key] = {
                "bond_code": bond_code,
                "model_id": model_id,
                "model_short": MODEL_META.get(model_id, {}).get("short", model_id),
                "model_color": MODEL_META.get(model_id, {}).get("color", "blue"),
                "order_id": int(row["order_id"]) if row["order_id"] is not None else None,
                "side": row["side"],
                "price": float(row["price"]),
                "kind": row["fill_reason"],
                "kind_label": KIND_LABELS.get(row["fill_reason"], row["fill_reason"]),
                "event_type": "fill",
                "event_label": "买入成交" if row["side"] == "buy" else "卖出成交",
                "ts": int(row["market_ts_ms"]),
                "time": _clock(row["market_ts_ms"]),
                "quantity": 0.0,
                "detail": KIND_LABELS.get(row["fill_reason"], row["fill_reason"]),
                "paper_only": True,
            }
        fill_groups[key]["quantity"] += float(row["quantity"])
    actions.extend(fill_groups.values())
    actions.sort(key=lambda item: (int(item["ts"]), item["event_type"]), reverse=True)

    proposals = []
    proposed_models: set[str] = set()
    for order in open_orders:
        if (
            order["model_id"] == "maker_windfall_v1_0"
            or order["model_id"] in proposed_models
        ):
            continue
        proposed_models.add(order["model_id"])
        proposals.append(
            {
                "id": f"SIM-{bond_code}-{order['id']}",
                "model_id": order["model_id"],
                "model_short": order["model_short"],
                "model_color": order["model_color"],
                "side": order["side"],
                "price": order["limit_price"],
                "quantity": order["remaining"],
                "reason": order["kind_label"],
                "created": _clock(order["created_market_ts_ms"]),
                "expires": "盘口实质变化即失效",
                "paper_only": True,
            }
        )
        if len(proposals) >= 3:
            break

    now = datetime.now()
    window_active = refresh_window_active(now) and not replay_mode
    session_data = dict(session) if session else {}
    latest_change_data = dict(latest_change) if latest_change else {}
    return {
        "source": "sqlite-read-only",
        "mode": "replay" if replay_mode else "live",
        "paper_only": True,
        "approval_writes_database": False,
        "bond": {"code": bond_code, **BONDS[bond_code]},
        "market": compact_market,
        "book": book,
        "history": history,
        "assessment": assessment,
        "accounts": accounts,
        "open_orders": open_orders,
        "lifecycle": lifecycle,
        "fills": fills,
        "market_trades": market_trades,
        "actions": actions,
        "proposals": proposals,
        "latest_change": latest_change_data,
        "session": session_data,
        "refresh": {
            "active": window_active,
            "label": (
                "历史模拟回看 · 因果数据截断"
                if replay_mode else
                ("盘中自动刷新" if window_active else "收盘快照 · 数据库轮询已暂停")
            ),
            "served_at": now.isoformat(timespec="seconds"),
        },
        "replay": {
            "active": replay_mode,
            "requested_ts_ms": requested_ts_ms,
            "effective_ts_ms": effective_ts_ms,
            "start_ts_ms": start_ts_ms,
            "end_ts_ms": end_ts_ms,
            "tick_count": int(bounds["tick_count"]),
            "causal_cutoff": True,
        },
    }


class SnapshotCache:
    def __init__(self, database: Path) -> None:
        self.database = database
        self._lock = threading.Lock()
        self._items: dict[tuple[str, str | None], tuple[float, dict[str, Any]]] = {}

    def get(self, bond_code: str, action_model_id: str | None = None) -> dict[str, Any]:
        now = time.monotonic()
        active = refresh_window_active()
        key = (bond_code, action_model_id)
        with self._lock:
            cached = self._items.get(key)
            # Outside the refresh window, reuse the last screen indefinitely.
            if cached and (not active or now - cached[0] < 2.5):
                result = dict(cached[1])
                result["refresh"] = dict(result["refresh"])
                result["refresh"]["active"] = active
                result["refresh"]["label"] = (
                    "盘中自动刷新" if active else "收盘快照 · 数据库轮询已暂停"
                )
                return result
            snapshot = load_snapshot(
                self.database, bond_code, action_model_id=action_model_id
            )
            self._items[key] = (now, snapshot)
            return snapshot

    def warm_once(self) -> None:
        for bond_code in BONDS:
            try:
                self.get(bond_code, "maker_priority_v1_1")
            except Exception:
                continue


class DashboardHandler(BaseHTTPRequestHandler):
    cache: SnapshotCache

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/replay/meta":
            query = parse_qs(parsed.query)
            bond_code = query.get("bond", ["132026.SH"])[0]
            try:
                self._send_json(load_replay_metadata(self.cache.database, bond_code))
            except (ValueError, FileNotFoundError, RuntimeError) as exc:
                self._send_json(
                    {"error": str(exc), "paper_only": True},
                    status=HTTPStatus.BAD_REQUEST,
                )
            return
        if parsed.path == "/api/replay/snapshot":
            query = parse_qs(parsed.query)
            bond_code = query.get("bond", ["132026.SH"])[0]
            action_model_id = query.get("model", [None])[0]
            market_date = query.get("date", [None])[0]
            raw_ts = query.get("ts", [None])[0]
            try:
                if not market_date or raw_ts is None:
                    raise ValueError("回看需要 date 和 ts 参数")
                payload = load_snapshot(
                    self.cache.database,
                    bond_code,
                    market_date=market_date,
                    target_ts_ms=int(raw_ts),
                    action_model_id=action_model_id,
                )
                self._send_json(payload)
            except (ValueError, FileNotFoundError, RuntimeError) as exc:
                self._send_json(
                    {"error": str(exc), "paper_only": True},
                    status=HTTPStatus.BAD_REQUEST,
                )
            except Exception as exc:
                self._send_json(
                    {"error": str(exc), "paper_only": True},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return
        if parsed.path == "/api/snapshot":
            query = parse_qs(parsed.query)
            bond_code = query.get("bond", ["132026.SH"])[0]
            action_model_id = query.get("model", [None])[0]
            try:
                payload = self.cache.get(bond_code, action_model_id)
                self._send_json(payload)
            except Exception as exc:
                self._send_json(
                    {"error": str(exc), "paper_only": True},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return
        path = parsed.path.strip("/") or "index.html"
        target = (APP_DIR / path).resolve()
        if APP_DIR not in target.parents and target != APP_DIR:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        if getattr(self.server, "quiet", False):
            return
        super().log_message(fmt, *args)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="只读实盘决策看板")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    cache = SnapshotCache(args.database)
    cache.warm_once()
    DashboardHandler.cache = cache
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    server.quiet = args.quiet  # type: ignore[attr-defined]
    print(f"实盘决策看板：http://{args.host}:{args.port}")
    print(f"行情账本：{args.database.resolve()}（只读）")
    print("审批操作：仅浏览器内存模拟，不写数据库，不发送委托")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
