from __future__ import annotations

import argparse
import bisect
import json
import math
import mimetypes
import sqlite3
import sys
import threading
import time
from datetime import datetime, time as clock_time, timezone
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
MANUAL_PROJECT_DIR = REPO_DIR / "半自动手动交易"
DEFAULT_MANUAL_AUDIT_LOG = (
    MANUAL_PROJECT_DIR / "runtime" / "manual_takeover_audit.jsonl"
)
SHANGHAI_TZ = datetime.now().astimezone().tzinfo

if str(MANUAL_PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(MANUAL_PROJECT_DIR))

from manual_takeover import (  # noqa: E402
    CommandValidationError,
    JsonlAuditSink,
    ManualTakeoverService,
    SqliteQuoteProvider,
)

BONDS = {
    "132026.SH": {"name": "G三峡EB2", "stock": "600900.SH"},
    "132024.SH": {"name": "26江铜EB", "stock": "600362.SH"},
}

MODEL_META = {
    "maker_priority_v1_1": {
        "branch": "priority",
        "color": "gold",
        "note": "改善一厘的高频做T生产基线",
    },
    "maker_queue_v1_0": {
        "branch": "queue",
        "color": "blue",
        "note": "消耗真实显示前队的排队基线",
    },
    "maker_windfall_v1_0": {
        "branch": "windfall",
        "color": "lime",
        "note": "独立10张异常深价额度，退出规则待校准",
    },
    "maker_windfall_v2_0_candidate": {
        "branch": "windfall",
        "color": "lime",
        "note": "独立1,000张风险块，主动吃大漏并在有利断层预埋",
    },
    "maker_priority_v1_37_candidate": {
        "branch": "priority",
        "color": "violet",
        "note": "近墙低卖一主动低接候选",
    },
    "maker_priority_v1_43_candidate": {
        "branch": "priority",
        "color": "coral",
        "note": "即时可见卖墙扫尾恢复底仓候选",
    },
    "maker_priority_v1_44": {
        "branch": "priority",
        "color": "coral",
        "note": "相邻厚买簇先买后卖与动态止错",
    },
    "maker_priority_v1_45": {
        "branch": "priority",
        "color": "coral",
        "note": "孤岛买一保护与卖墙攻击回补",
    },
    "maker_priority_v1_46": {
        "branch": "priority",
        "color": "coral",
        "note": "高侧成交、双边深度与止损联合走廊",
    },
    "maker_priority_v1_47": {
        "branch": "priority",
        "color": "coral",
        "note": "静默宽盘口保留盘中中枢并继续双边估值",
    },
    "maker_priority_v1_48_candidate": {
        "branch": "priority",
        "color": "coral",
        "note": "突破回挂失效、孤立深折价与满仓容量释放",
    },
    "maker_priority_v1_49_candidate": {
        "branch": "priority",
        "color": "coral",
        "note": "高侧失效近保本回中性与未污染错价簇",
    },
    "maker_priority_v1_49_candidate_r2": {
        "branch": "priority",
        "color": "coral",
        "note": "第一顺位1.49：孤立低卖簇不冒充急买盘",
    },
    "maker_priority_v1_50_candidate": {
        "branch": "priority",
        "color": "coral",
        "note": "第一顺位1.50：有效底仓回补持续暴露在实时第一顺位",
    },
    "maker_priority_v2_1_candidate": {
        "branch": "priority",
        "color": "coral",
        "note": "第一顺位2.1：强趋势价格发现与经济空头快速回补",
    },
    "maker_queue_v1_17_candidate": {
        "branch": "queue",
        "color": "teal",
        "note": "正常交易至15:30的排队候选",
    },
    "maker_queue_v1_18_candidate": {
        "branch": "queue",
        "color": "blue",
        "note": "第二档队首执行候选",
    },
}

KIND_LABELS = {
    "simple_top_cycle": "买卖一循环",
    "simple_passive_buy": "买一被动买入",
    "simple_passive_sell": "卖一被动卖出",
    "base": "期初底仓",
    "low_bid_reversion": "低价承接",
    "inventory_replenish": "客户底仓回补",
    "sweep_tail": "扫尾跟随",
    "deep_discount_sweep": "深度折价主动买",
    "inventory_exit": "库存卖出",
    "inventory_risk_exit": "下行风险退出",
    "failed_breakout_sweep_release": "失败突破扫尾释放",
    "full_inventory_capacity_release_exit": "满仓容量释放",
    "active_full_inventory_capacity_release": "满仓容量主动释放",
    "stalled_extra_inventory_near_flat_exit": "高侧失效近保本回中性",
    "adjacent_bid_cushion_entry": "相邻厚买簇低接",
    "adjacent_bid_cushion_risk_exit": "保护买簇受损退出",
    "joint_causal_corridor_entry": "联合走廊低侧买入",
    "joint_causal_corridor_base_sell": "联合走廊底仓高卖",
    "joint_causal_corridor_risk_exit": "联合走廊承托受损退出",
    "active_joint_causal_corridor_risk_exit": "联合走廊承托受损退出",
    "isolated_top_bid_guarded_base_replenish": "孤岛买一保护回补",
    "isolated_top_bid_wall_attack_base_replenish": "卖墙受攻击主动回补",
    "active_isolated_top_bid_sell_wall_attack_replenishment": (
        "卖墙受攻击主动回补"
    ),
    "active_stock_accelerated_trend_base_replenishment": (
        "正股极强且债券吃墙，主动恢复底仓"
    ),
    "active_bond_confirmed_trend_base_replenishment": (
        "债券上涨确认，主动恢复底仓"
    ),
    "super_windfall": "超级捡漏",
    "super_windfall_active": "超级捡漏主动吃单",
    "active_super_windfall_buy": "超级捡漏主动买入",
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
    "retained_intraday_working_reference": "盘中成交中枢（静默期低置信保留）",
    "trend_price_discovery": "强趋势当前价格发现",
}

QUIET_REFERENCE_MODEL_IDS = {
    "maker_priority_v1_47",
    "maker_priority_v1_48_candidate",
    "maker_priority_v1_49_candidate",
    "maker_priority_v1_49_candidate_r2",
    "maker_priority_v1_50_candidate",
}
QUIET_REFERENCE_HALF_WIDTH = 0.20
QUIET_REFERENCE_EARLIEST_TIME = "14:45:00.000"
QUIET_REFERENCE_MINIMUM_SECONDS = 600
QUIET_REFERENCE_MINIMUM_SPREAD = 0.40


def _json_loads(value: str | None) -> dict[str, Any]:
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


BASELINE_MODEL_IDS = {
    "priority": "maker_priority_v1_1",
    "queue": "maker_queue_v1_0",
}


def model_ids_from_session_config(config_json: str | None) -> tuple[str, ...] | None:
    """Read the maker matrix actually loaded by a paper-simulation session."""
    config = _json_loads(config_json)
    maker = config.get("maker_paper")
    if not isinstance(maker, dict):
        return None
    if not maker.get("enabled", False):
        return ()

    model_ids = [
        BASELINE_MODEL_IDS[mode]
        for mode in maker.get("fill_modes", [])
        if mode in BASELINE_MODEL_IDS
    ]
    if maker.get("super_windfall_enabled", False):
        model_ids.append(str(
            maker.get("super_windfall_model_id") or "maker_windfall_v1_0"
        ))
    configured_comparisons = maker.get("realtime_comparison_model_ids", [])
    if isinstance(configured_comparisons, list):
        model_ids.extend(
            str(model_id) for model_id in configured_comparisons if model_id
        )
    return tuple(dict.fromkeys(model_ids))


def _session_started_ts_ms(value: Any) -> int | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def simulation_session_model_ids(
    connection: sqlite3.Connection, *, target_ts_ms: int | None = None,
) -> tuple[str, ...] | None:
    """Return the model matrix from the relevant simulator run, in run order."""
    try:
        rows = connection.execute(
            """SELECT started_at_utc,config_json
               FROM sessions ORDER BY started_at_utc DESC"""
        ).fetchall()
    except sqlite3.OperationalError:
        return None
    for row in rows:
        started_ts_ms = _session_started_ts_ms(row["started_at_utc"])
        if target_ts_ms is not None and (
            started_ts_ms is None or started_ts_ms > target_ts_ms
        ):
            continue
        model_ids = model_ids_from_session_config(row["config_json"])
        if model_ids is not None:
            return model_ids
    return None


def order_price_boundary_view(
    metadata: dict[str, Any], side: str,
) -> tuple[float | None, str | None, str]:
    """Expose only a boundary frozen by the order's causal decision frame."""
    boundary_kind = metadata.get("price_boundary_kind")
    boundary_value = metadata.get("price_boundary")
    try:
        price_boundary = (
            float(boundary_value)
            if boundary_value is not None and float(boundary_value) > 0
            else None
        )
    except (TypeError, ValueError):
        price_boundary = None
    if price_boundary is not None and boundary_kind not in {
        "buy_ceiling", "sell_floor", "live_priority_price",
    }:
        boundary_kind = "buy_ceiling" if side == "buy" else "sell_floor"
    boundary_label = (
        "最高买价" if boundary_kind == "buy_ceiling"
        else "最低卖价" if boundary_kind == "sell_floor"
        else "当前跟随价" if boundary_kind == "live_priority_price"
        else "极限价"
    )
    return price_boundary, boundary_kind, boundary_label


def _clock(ts_ms: int | float | None) -> str:
    if not ts_ms:
        return "--:--:--"
    return datetime.fromtimestamp(float(ts_ms) / 1000).strftime("%H:%M:%S")


def valid_chart_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only real, positive transaction prices for the intraday chart."""
    result: list[dict[str, Any]] = []
    for item in history:
        try:
            price = float(item["last"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(price) and price > 0:
            result.append(item)
    return result


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
    from zhaiquant.maker import (
        MakerAnalyzer,
        MakerParameters,
        _load_ticks,
        trend_price_discovery_assessment,
    )

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
    last_market_trade_ts_ms = 0
    for tick in ticks:
        analyzer.on_tick(tick)
        if tick.code == bond_code:
            if tick.trade_bonds > 0:
                last_market_trade_ts_ms = int(tick.market_ts_ms)
            timestamps.append(int(tick.market_ts_ms))
            base_assessment = analyzer.assess_market(
                tick, tick.previous_close,
            )
            assessment = _decorate_assessment(base_assessment.public())
            assessment["_trend_assessment"] = _decorate_assessment(
                trend_price_discovery_assessment(
                    base_assessment, tick, parameters,
                    stock_extremely_strong=(
                        analyzer.stock_is_extremely_strong()
                    ),
                ).public()
            )
            # The strategy's model-specific late-session reference permission
            # depends on causal trade silence and clock time.  Preserve those
            # two inputs in the cached timeline, then remove them before the
            # public assessment is returned.
            assessment["_market_time"] = tick.market_time
            assessment["_last_market_trade_ts_ms"] = last_market_trade_ts_ms
            assessments.append(assessment)
    return tuple(timestamps), tuple(assessments)


def _assessment_at(
    database: Path, market_date: str, bond_code: str, target_ts_ms: int,
    *, model_id: str | None = None, bid1: float = 0.0, ask1: float = 0.0,
) -> dict[str, Any] | None:
    try:
        timestamps, assessments = _assessment_timeline(
            str(database.resolve()), database.stat().st_mtime_ns, market_date, bond_code
        )
        index = bisect.bisect_right(timestamps, target_ts_ms) - 1
        if index < 0:
            return None
        current = dict(assessments[index])
        if (
            model_id == "maker_priority_v2_1_candidate"
            and isinstance(current.get("_trend_assessment"), dict)
        ):
            market_time = current.get("_market_time")
            last_trade_ts = current.get("_last_market_trade_ts_ms")
            current = dict(current["_trend_assessment"])
            current["_market_time"] = market_time
            current["_last_market_trade_ts_ms"] = last_trade_ts
        last_market_trade_ts_ms = int(
            current.get("_last_market_trade_ts_ms") or 0
        )
        if (
            model_id in QUIET_REFERENCE_MODEL_IDS
            and current.get("reference_source") == "previous_close"
            and str(current.get("_market_time") or "")
                >= QUIET_REFERENCE_EARLIEST_TIME
            and last_market_trade_ts_ms > 0
            and timestamps[index] - last_market_trade_ts_ms
                >= QUIET_REFERENCE_MINIMUM_SECONDS * 1_000
            and bid1 > 0
            and ask1 > bid1
            and ask1 - bid1 + 1e-9 >= QUIET_REFERENCE_MINIMUM_SPREAD
        ):
            prior = next(
                (
                    dict(item) for item in reversed(assessments[:index])
                    if item.get("reference_source") != "previous_close"
                ),
                None,
            )
            if prior is not None:
                reference = float(prior.get("reference_price") or 0.0)
                if bid1 - 0.015 <= reference <= ask1 + 0.015:
                    prior_low = float(prior.get("reference_low") or reference)
                    prior_high = float(prior.get("reference_high") or reference)
                    current.update({
                        "reference_price": round(reference, 3),
                        "reference_low": round(
                            max(
                                min(prior_low, reference),
                                reference - QUIET_REFERENCE_HALF_WIDTH,
                            ),
                            3,
                        ),
                        "reference_high": round(
                            min(
                                max(prior_high, reference),
                                reference + QUIET_REFERENCE_HALF_WIDTH,
                            ),
                            3,
                        ),
                        "reference_source": (
                            "retained_intraday_working_reference"
                        ),
                        "reference_source_label": REFERENCE_LABELS[
                            "retained_intraday_working_reference"
                        ],
                        "reference_confidence": min(
                            0.35,
                            float(prior.get("reference_confidence") or 0.0),
                        ),
                        "evidence": [
                            "尾盘成交转稀但当前宽盘口仍包住盘中中枢；"
                            "保留低置信盘中估值，昨收不重新接管定价。",
                            *(current.get("evidence") or []),
                        ],
                    })
        current.pop("_market_time", None)
        current.pop("_last_market_trade_ts_ms", None)
        current.pop("_trend_assessment", None)
        return current
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


def _default_model_meta(
    model_id: str, model_version: Any, fill_mode: str,
) -> dict[str, str]:
    family = {
        "priority": "第一顺位",
        "queue": "排队",
        "windfall": "超级捡漏",
    }.get(fill_mode)
    if model_id == "maker_dadao_v0_1_candidate_r2":
        short = "大道至简0.1（实时修订）"
    elif model_id == "maker_shared_1000_v0_1_candidate":
        short = "千张第一顺位0.1"
    elif model_id == "maker_shared_1000_v0_13_candidate":
        short = "千张第一顺位0.13"
    elif model_id == "maker_shared_1000_v0_16_candidate_r2":
        short = "千张第一顺位0.16（实时修订）"
    elif model_id == "maker_priority_v1_49_candidate_r2":
        short = "第一顺位1.49"
    elif model_id == "maker_priority_v1_50_candidate":
        short = "第一顺位1.50"
    elif model_id == "maker_priority_v2_1_candidate":
        short = "第一顺位2.1"
    elif model_id in {
        "maker_priority_v2_5_candidate",
        "maker_priority_v2_5_candidate_r2",
    }:
        short = "第一顺位2.5"
    elif model_id in {
        "maker_priority_v2_51_candidate_r2",
        "maker_priority_v2_51_candidate_r3",
    }:
        short = "第一顺位2.51"
    elif model_id in {
        "maker_priority_v2_52_candidate",
        "maker_priority_v2_52_candidate_r2",
    }:
        short = "第一顺位2.52"
    elif model_id in {
        "maker_priority_v2_6_candidate",
        "maker_priority_v2_6_candidate_r2",
        "maker_priority_v2_6_candidate_r3",
    }:
        short = "第一顺位2.6"
    elif model_id == "maker_priority_v2_63_candidate":
        short = "第一顺位2.63"
    elif model_id == "maker_priority_v2_70_candidate_r2":
        short = "第一顺位2.70"
    else:
        version = str(model_version or "").removesuffix("-candidate")
        short = (
            f"{family}{version}"
            if family and version
            else (family or model_id)
        )
    return {
        "short": short,
        "status": "模拟盘",
        "branch": (
            "大道至简" if model_id == "maker_dadao_v0_1_candidate_r2" else "千张第一顺位"
            if model_id in {
                "maker_shared_1000_v0_1_candidate",
                "maker_shared_1000_v0_13_candidate",
                "maker_shared_1000_v0_16_candidate_r2",
            }
            else fill_mode
        ),
        "color": "blue",
        "note": "来自模拟盘运行配置",
    }


def _account_view(row: sqlite3.Row, market: dict[str, Any]) -> dict[str, Any]:
    model_id = row["model_id"] or row["strategy_id"]
    default_meta = _default_model_meta(
        model_id, row["model_version"], row["fill_mode"],
    )
    meta = {**default_meta, **MODEL_META.get(model_id, {})}
    # Keep registry IDs internal. Human-facing names match the simulator
    # console's Chinese branch/version convention for every model generation.
    meta["short"] = default_meta["short"]
    meta["status"] = "模拟盘"
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


def closing_pnl_by_fill(
    fill_rows: list[sqlite3.Row] | list[dict[str, Any]],
    account_rows: list[sqlite3.Row] | list[dict[str, Any]],
) -> dict[int, float]:
    """Return gross realized PnL allocated to each causally available close fill."""
    if not fill_rows or not account_rows:
        return {}
    if str(SOURCE_DIR) not in sys.path:
        sys.path.insert(0, str(SOURCE_DIR))
    from zhaiquant.maker_dashboard import build_daily_trades

    fills = [dict(row) for row in fill_rows]
    accounts = [dict(row) for row in account_rows]
    completed, unfinished = build_daily_trades(fills, accounts)
    result: dict[int, float] = {}
    for leg in (*completed, *unfinished):
        direction = 1.0 if leg["open_side"] == "buy" else -1.0
        open_price = float(leg["open_price"])
        for detail in leg["close_details"]:
            fill_id = int(detail["fill_id"])
            pnl = (
                float(detail["quantity"])
                * (float(detail["price"]) - open_price)
                * direction
            )
            result[fill_id] = result.get(fill_id, 0.0) + pnl
    return result


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
        causal_history = [
            {
                "ts": int(row["market_ts_ms"]),
                "time": _clock(row["market_ts_ms"]),
                "last": float(row["last_price"]),
                "bid": float(row["bid_price_1"]),
                "ask": float(row["ask_price_1"]),
            }
            for row in reversed(history_rows)
        ]
        history = valid_chart_history(causal_history)

        accounts_rows = connection.execute(
            """SELECT a.*,m.model_id,m.model_version,m.parent_model_id,m.bond_code
               FROM maker_paper_accounts AS a
               JOIN maker_paper_model_assignments AS m
                 ON m.market_date=a.market_date AND m.strategy_id=a.strategy_id
               WHERE a.market_date=? AND m.bond_code=?""",
            (market_date, bond_code),
        ).fetchall()
        model_order = simulation_session_model_ids(
            connection,
            target_ts_ms=effective_ts_ms if replay_mode else None,
        )
        if model_order is not None:
            # Accounts deliberately survive same-day model replacements. The
            # simulator session says which ledgers belonged to this snapshot.
            accounts_rows = [
                row for row in accounts_rows if row["model_id"] in model_order
            ]
        else:
            model_order = tuple(row["model_id"] for row in accounts_rows)
        model_rank = {
            model_id: index for index, model_id in enumerate(model_order)
        }
        accounts = [_account_view(row, market) for row in accounts_rows]
        accounts.sort(
            key=lambda item: model_rank.get(item["model_id"], len(model_rank))
        )
        strategy_ids = [item["strategy_id"] for item in accounts]
        if strategy_ids:
            placeholders = ",".join("?" for _ in strategy_ids)
            fill_rows_ascending = connection.execute(
                f"""SELECT f.*,l.kind AS lot_kind
                    FROM maker_paper_fills AS f
                    LEFT JOIN maker_paper_lots AS l ON l.id=f.lot_id
                    WHERE f.market_date=? AND f.strategy_id IN ({placeholders})
                      AND f.market_ts_ms<=?
                    ORDER BY f.market_ts_ms,f.id""",
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
            key=lambda item: model_rank.get(item["model_id"], len(model_rank))
        )

        account_by_strategy = {item["strategy_id"]: item for item in accounts}
        model_by_strategy = {
            strategy_id: item["model_id"]
            for strategy_id, item in account_by_strategy.items()
        }
        pnl_strategy_ids = {
            strategy_id for strategy_id, model_id in model_by_strategy.items()
            if action_model_id is None or model_id == action_model_id
        }
        closing_pnl = closing_pnl_by_fill(
            [row for row in fill_rows_ascending if row["strategy_id"] in pnl_strategy_ids],
            [row for row in accounts_rows if row["strategy_id"] in pnl_strategy_ids],
        )
        all_orders: list[dict[str, Any]] = []
        for row in order_rows:
            data = dict(row)
            metadata = _json_loads(data.pop("metadata_json", None))
            model_id = model_by_strategy.get(data["strategy_id"], metadata.get("model_id"))
            model_account = account_by_strategy.get(data["strategy_id"], {})
            (
                price_boundary, boundary_kind, boundary_label,
            ) = order_price_boundary_view(
                metadata, str(data["side"]),
            )
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
                model_short=model_account.get("short", "模拟盘模型"),
                model_color=model_account.get("color", "blue"),
                kind_label=KIND_LABELS.get(data["kind"], data["kind"]),
                price_boundary=price_boundary,
                price_boundary_kind=boundary_kind,
                price_boundary_label=boundary_label,
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
            model_account = account_by_strategy.get(data["strategy_id"], {})
            data.update(
                model_id=model_id,
                model_short=model_account.get("short", "模拟盘模型"),
                model_color=model_account.get("color", "blue"),
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

    assessment = _assessment_at(
        database, market_date, bond_code, effective_ts_ms,
        model_id=action_model_id,
        bid1=float(market["bid_price_1"]),
        ask1=float(market["ask_price_1"]),
    )
    if assessment is None:
        # Quote-only snapshots before the first transaction remain useful to the
        # fallback assessment, but their zero last price must never reach the chart.
        assessment = _fallback_assessment(compact_market, causal_history)

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
            model_account = account_by_strategy.get(row["strategy_id"], {})
            fill_groups[key] = {
                "bond_code": bond_code,
                "model_id": model_id,
                "model_short": model_account.get("short", "模拟盘模型"),
                "model_color": model_account.get("color", "blue"),
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
                "is_closing": False,
                "realized_pnl": 0.0,
            }
        fill_groups[key]["quantity"] += float(row["quantity"])
        fill_id = int(row["id"])
        if fill_id in closing_pnl:
            fill_groups[key]["is_closing"] = True
            fill_groups[key]["realized_pnl"] += closing_pnl[fill_id]
    for action in fill_groups.values():
        action["realized_pnl"] = round(float(action["realized_pnl"]), 2)
    actions.extend(fill_groups.values())
    actions.sort(key=lambda item: (int(item["ts"]), item["event_type"]), reverse=True)

    proposals = []
    proposed_models: set[str] = set()
    for order in open_orders:
        if (
            str(order["model_id"]).startswith("maker_windfall_")
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
        "model_source": {
            "kind": "simulator-session-config",
            "model_ids": list(model_order),
        },
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

    def get(
        self,
        bond_code: str,
        action_model_id: str | None = None,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        now = time.monotonic()
        active = refresh_window_active()
        key = (bond_code, action_model_id)
        with self._lock:
            cached = self._items.get(key)
            # Outside the refresh window, reuse the last screen indefinitely.
            if cached and not force_refresh and (
                not active or now - cached[0] < 2.5
            ):
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
    manual_service: ManualTakeoverService | None = None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/manual/status":
            if self.manual_service is None:
                self._send_json(
                    {"error": "手动接管服务尚未启动", "paper_only": True},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            try:
                self._send_json(self.manual_service.status())
            except Exception as exc:
                self._send_json(
                    {"error": str(exc), "paper_only": True},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return
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
            force_refresh = query.get("fresh", ["0"])[0].lower() in {
                "1", "true", "yes",
            }
            try:
                payload = self.cache.get(
                    bond_code,
                    action_model_id,
                    force_refresh=force_refresh,
                )
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

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        routes = {
            "/api/manual/start": "start",
            "/api/manual/cancel": "cancel",
            "/api/manual/cancel-all": "cancel_all",
        }
        operation = routes.get(parsed.path)
        if operation is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if self.manual_service is None:
            self._send_json(
                {"error": "手动接管服务尚未启动", "paper_only": True},
                status=HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        try:
            payload = self._read_json_body()
            if operation == "start":
                result = self.manual_service.start(payload)
            elif operation == "cancel":
                result = self.manual_service.cancel(payload)
            else:
                result = self.manual_service.cancel_all()
            self._send_json(result)
        except (CommandValidationError, ValueError) as exc:
            self._send_json(
                {"error": str(exc), "paper_only": True},
                status=HTTPStatus.BAD_REQUEST,
            )
        except Exception as exc:
            self._send_json(
                {"error": str(exc), "paper_only": True},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _read_json_body(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ValueError("手动接管接口只接受application/json")
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Content-Length无效") from exc
        if length < 0 or length > 65_536:
            raise ValueError("请求内容过大")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("请求必须是UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("请求JSON必须是对象")
        return payload

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
    parser.add_argument(
        "--manual-audit-log", type=Path, default=DEFAULT_MANUAL_AUDIT_LOG
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    cache = SnapshotCache(args.database)
    cache.warm_once()
    manual_service = ManualTakeoverService(
        SqliteQuoteProvider(args.database),
        audit_sink=JsonlAuditSink(args.manual_audit_log),
        window_active=refresh_window_active,
    )
    manual_service.start_worker()
    DashboardHandler.cache = cache
    DashboardHandler.manual_service = manual_service
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    server.quiet = args.quiet  # type: ignore[attr-defined]
    print(f"实盘决策看板：http://{args.host}:{args.port}")
    print(f"行情账本：{args.database.resolve()}（只读）")
    print("手动接管：干运行追价状态机，不发送真实委托")
    print(f"手动接管审计：{args.manual_audit_log.resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        manual_service.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
