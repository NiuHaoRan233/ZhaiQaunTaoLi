"""Read-only economic attribution of the frozen 1.7 CNY / zero-delay accounts.

No new strategy is executed. Future markouts and holding categories are explicitly
descriptive labels, never selection inputs. Original account and tick hashes are
recorded, and all midpoint identities are checked against saved actual fills.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "广义套利/reports"
DATA = ROOT / "广义套利/data"
OUT = REPORT / "commodity_optimization_20260912"
TZ = ZoneInfo("Asia/Shanghai")
FOCUS = ["au2612C920.SF", "au2612C840.SF", "ag2612P16000.SF",
         "cu2611P114000.SF", "PK612P8400.ZF", "CF703P17400.ZF",
         "jm2701-P-1460.DF", "a2701-P-5500.DF", "c2611-C-2300.DF",
         "SR703C5400.ZF", "pt2612-C-448.GF"]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp(ts):
    return datetime.fromtimestamp(ts / 1000, TZ).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def calendar(ts):
    return stamp(ts)[:10]


def group_cycles(cycles):
    positive = sorted((c["net_cents"] / 100 for c in cycles if c["net_cents"] > 0), reverse=True)
    return dict(count=len(cycles), gross_cny=round(sum(c["gross_cents"] for c in cycles) / 100, 2),
                fees_cny=round(sum(c["fees_cents"] for c in cycles) / 100, 2),
                net_cny=round(sum(c["net_cents"] for c in cycles) / 100, 2),
                winning=sum(c["net_cents"] > 0 for c in cycles),
                losing=sum(c["net_cents"] < 0 for c in cycles),
                positive_net_cny=round(sum(positive), 2),
                negative_net_cny=round(sum(c["net_cents"] for c in cycles if c["net_cents"] < 0) / 100, 2),
                best_three_net_cny=round(sum(positive[:3]), 2),
                best_three_share_positive=round(sum(positive[:3]) / sum(positive), 6) if positive else None)


def duration_group(cycle):
    if calendar(cycle["entry_ts"]) != calendar(cycle["exit_ts"]):
        return "overnight"
    t = cycle["duration_seconds"]
    return "same_le_5s" if t <= 5 else "same_5_60s" if t <= 60 else "same_1_5m" if t <= 300 else "same_5_30m" if t <= 1800 else "same_gt_30m"


def table(headers, rows):
    return "| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n" + "\n".join("| " + " | ".join(str(v) for v in row) + " |" for row in rows)


def n(value):
    return "缺失" if value is None else f"{value:,.2f}"


def market_frame(path, unit):
    raw = pd.read_pickle(path).sort_values("time", kind="stable").drop_duplicates("time", keep="last")
    f = pd.DataFrame({"ts": raw.time.to_numpy(dtype=np.int64)})
    for column, field in (("bid", "bidPrice"), ("ask", "askPrice"), ("bid2", "bidPrice"), ("ask2", "askPrice")):
        index = 1 if column.endswith("2") else 0
        f[column] = np.rint(raw[field].map(lambda a: a[index]).to_numpy(dtype=float) * unit * 100)
    f["bid_qty"] = raw.bidVol.map(lambda a: a[0]).to_numpy(dtype=float)
    f["ask_qty"] = raw.askVol.map(lambda a: a[0]).to_numpy(dtype=float)
    f["last"] = np.rint(raw.lastPrice.to_numpy(dtype=float) * unit * 100)
    dt = pd.to_datetime(f.ts, unit="ms", utc=True).dt.tz_convert("Asia/Shanghai")
    minute = dt.dt.hour * 60 + dt.dt.minute + dt.dt.second / 60
    f["session"] = np.select([(minute >= 540) & (minute < 615), (minute >= 630) & (minute < 690),
                              (minute >= 810) & (minute < 900)], [0, 1, 2], default=-1)
    f["valid"] = (f.bid > 0) & (f.ask > f.bid) & (f.bid_qty > 0) & (f.ask_qty > 0) & (f.session >= 0)
    f["mid"] = ((f.bid + f.ask) / 2).where(f.valid)
    f["spread"] = (f.ask - f.bid).where(f.valid)
    f["range60"] = np.nan
    f["change60"] = np.nan
    f["count60"] = 0
    for session in (0, 1, 2):
        ix = f.index[(f.session == session) & f.valid]
        vals = pd.Series(f.loc[ix, "mid"].to_numpy(), index=pd.to_datetime(f.loc[ix, "ts"].to_numpy(), unit="ms"))
        roll = vals.rolling("60s", min_periods=3)
        f.loc[ix, "range60"] = (roll.max() - roll.min()).to_numpy()
        f.loc[ix, "count60"] = vals.rolling("60s").count().to_numpy()
        times = f.loc[ix, "ts"].to_numpy()
        prior = np.searchsorted(times, times - 60000, side="left")
        f.loc[ix, "change60"] = vals.to_numpy() - vals.to_numpy()[prior]
    return f


def main():
    OUT.mkdir(exist_ok=True)
    source_summary = read(REPORT / "dadao_user_f170_d0_result/summary.json")
    details = {v["code"]: v["detail"] for v in read(REPORT / "intraday_screen.json")["rows"].values()}
    paths, inputs, manifest = {}, {}, {}
    for part in (0, 1):
        path = REPORT / f"dadao_user_f170_d0_{part}/matrix.json"
        matrix = read(path)
        assert not matrix["errors"]
        manifest[str(path.relative_to(ROOT))] = sha(path)
        inputs.update(matrix["inputs"])
        for code in matrix["accounts"]:
            paths[code] = path.parent / "accounts" / f"{code}.json.gz"
    all_cycles, rows, fills_out, daily_tails, errors = [], [], [], [], []
    verified_inputs = 0
    for index, (code, account_path) in enumerate(sorted(paths.items()), 1):
        account = json.loads(gzip.decompress(account_path.read_bytes()))
        manifest[str(account_path.relative_to(ROOT))] = sha(account_path)
        s, cycles = account["summary"], account["cycles"]
        assert s["fee_per_side_cny"] == 1.7 and s["mode"] == "last_d0"
        assert abs(s["pnl_cny"] - s["realized_gross_cny"] - s["tail_gross_cny"] + s["fees_cny"]) < 1e-7
        assert sum(c["gross_cents"] for c in cycles) == round(s["realized_gross_cny"] * 100)
        assert sum(f["fee_cents"] for f in account["fills"]) == round(s["fees_cny"] * 100)
        unit = details[code].get("OptUnit") or details[code]["VolumeMultiple"]
        tick = int(round(details[code]["PriceTick"] * unit * 100))
        stats = {**s, "unit": unit, "tick_cash_cny": tick / 100,
                 "groups": {}, "quote_decomposition": {}, "previous_quote_decomposition": {}, "raw_cycle_count": 0}
        enriched = {}
        for inp in inputs[code]:
            date = inp["date"]
            raw_path = DATA / f"{code}_{date}_tick.pkl"
            raw_sha = sha(raw_path)
            assert raw_sha == inp["sha256"], str(raw_path)
            manifest[str(raw_path.relative_to(ROOT))] = raw_sha
            verified_inputs += 1
            frame = market_frame(raw_path, unit)
            times = frame.ts.to_numpy()

            def quote_at(ts):
                pos = int(np.searchsorted(times, ts, side="right") - 1)
                if pos < 0 or int(times[pos]) != ts:
                    return None
                v = frame.iloc[pos]
                return dict(ts=int(v.ts), valid=bool(v.valid), bid_cny=float(v.bid / 100), ask_cny=float(v.ask / 100),
                            midpoint_cny=float(v.mid / 100) if v.valid else None,
                            spread_cny=float(v.spread / 100) if v.valid else None,
                            last_cny=float(v["last"] / 100), bid_quantity=float(v.bid_qty), ask_quantity=float(v.ask_qty),
                            bid_gap_cny=float((v.bid - v.bid2) / 100) if v.bid2 > 0 else None,
                            past60_range_cny=float(v.range60 / 100) if pd.notna(v.range60) else None,
                            past60_change_cny=float(v.change60 / 100) if pd.notna(v.change60) else None,
                            past60_quote_count=int(v.count60), session=int(v.session))

            for fill in account["fills"]:
                if fill["date"] != date:
                    continue
                current = quote_at(fill["ts"])
                creation = quote_at(fill["created_ts"])
                prior = quote_at(fill["source_previous_ts"])
                item = {**fill, "time": stamp(fill["ts"]), "creation_quote": creation,
                        "previous_quote": prior, "fill_quote": current}
                if creation and creation["valid"]:
                    gross_quote = creation["spread_cny"]
                    if gross_quote > tick / 100:
                        gross_quote -= 2 * tick / 100
                    item["created_planned_round_gross_cny"] = round(gross_quote, 4)
                    item["created_planned_round_net_cny"] = round(gross_quote - 3.4, 4)
                if current and current["valid"]:
                    mid = current["midpoint_cny"]
                    price = fill["price_cents"] / 100
                    item["edge_to_fill_mid_cny"] = mid - price if fill["side"] == "buy" else price - mid
                    if fill["side"] == "buy":
                        ask_target = current["ask_cny"] - (tick / 100 if current["spread_cny"] > tick / 100 else 0)
                        item["immediate_sell_target_net_cny"] = round(ask_target - price - 3.4, 4)
                    for seconds in (30, 60, 300):
                        p = int(np.searchsorted(times, fill["ts"] + seconds * 1000, side="left"))
                        if p < len(frame):
                            later = frame.iloc[p]
                            if later.valid and later.session == current["session"] and int(later.ts) - fill["ts"] <= (seconds + 5) * 1000:
                                item[f"future_{seconds}s_mid_change_cny"] = float(later.mid / 100 - mid)
                enriched[(fill["side"], fill["ts"])] = item
                fills_out.append(item)
            daily = next(d for d in account["daily"] if d["date"] == date)
            if daily["end_inventory"]:
                valid = frame[(frame.ts <= daily["last_ts"]) & frame.valid]
                if not valid.empty:
                    v = valid.iloc[-1]
                    daily_tails.append(dict(code=code, date=date, daily_pnl_cny=daily["pnl_cny"],
                                            quote_ts=int(v.ts), quote_age_seconds=(daily["last_ts"] - int(v.ts)) / 1000,
                                            bid_cny=float(v.bid / 100), ask_cny=float(v.ask / 100), last_cny=float(v["last"] / 100),
                                            spread_cny=float(v.spread / 100), bid_quantity=float(v.bid_qty),
                                            last_minus_bid_cny=float((v["last"] - v.bid) / 100)))
        code_cycles = []
        for c in cycles:
            entry = enriched[("buy", c["entry_ts"])]
            exit_ = enriched[("sell", c["exit_ts"])]
            item = {**c, "code": code, "entry_time": stamp(c["entry_ts"]), "exit_time": stamp(c["exit_ts"]),
                    "group": duration_group(c), "entry_fill": entry, "exit_fill": exit_}
            eq, xq = entry["fill_quote"], exit_["fill_quote"]
            if eq and xq and eq["valid"] and xq["valid"]:
                item["entry_edge_cny"] = eq["midpoint_cny"] - c["entry_price_cents"] / 100
                item["held_mid_drift_cny"] = xq["midpoint_cny"] - eq["midpoint_cny"]
                item["exit_edge_cny"] = exit_["price_cents"] / 100 - xq["midpoint_cny"]
                assert abs(sum(item[k] for k in ("entry_edge_cny", "held_mid_drift_cny", "exit_edge_cny")) - c["gross_cents"] / 100) < 1e-6
                stats["raw_cycle_count"] += 1
            else:
                errors.append(dict(code=code, entry_ts=c["entry_ts"], reason="invalid contemporaneous midpoint; excluded only from midpoint decomposition"))
            eq, xq = entry["previous_quote"], exit_["previous_quote"]
            if eq and xq and eq["valid"] and xq["valid"]:
                item["previous_entry_edge_cny"] = eq["midpoint_cny"] - c["entry_price_cents"] / 100
                item["previous_held_mid_drift_cny"] = xq["midpoint_cny"] - eq["midpoint_cny"]
                item["previous_exit_edge_cny"] = exit_["price_cents"] / 100 - xq["midpoint_cny"]
                assert abs(sum(item[k] for k in ("previous_entry_edge_cny", "previous_held_mid_drift_cny", "previous_exit_edge_cny")) - c["gross_cents"] / 100) < 1e-6
            code_cycles.append(item)
        for tag in ("same_le_5s", "same_5_60s", "same_1_5m", "same_5_30m", "same_gt_30m", "overnight"):
            stats["groups"][tag] = group_cycles([c for c in code_cycles if c["group"] == tag])
        same = [c for c in code_cycles if c["group"] != "overnight"]
        stats["groups"]["all_same_day"] = group_cycles(same)
        stats["all_cycles"] = group_cycles(code_cycles)
        stats["without_best_three_cny"] = round(s["pnl_cny"] - stats["all_cycles"]["best_three_net_cny"], 2)
        stats["gross_positive_but_fee_loss"] = s["pnl_cny"] < 0 <= s["realized_gross_cny"] + s["tail_gross_cny"]
        buys = [c["entry_fill"] for c in code_cycles]
        stats["buy_planned_nonpositive_count"] = sum(c.get("created_planned_round_net_cny", 1) <= 0 for c in buys)
        stats["buy_immediate_exit_underwater_count"] = sum(c.get("immediate_sell_target_net_cny", 1) < 0 for c in buys)
        stats["negative_gross_cycles"] = sum(c["gross_cents"] < 0 for c in cycles)
        for k in ("entry_edge_cny", "held_mid_drift_cny", "exit_edge_cny"):
            stats["quote_decomposition"][k] = round(sum(c[k] for c in code_cycles if k in c), 4)
            prev_key = "previous_" + k
            stats["previous_quote_decomposition"][k] = round(sum(c[prev_key] for c in code_cycles if prev_key in c), 4)
        stats["previous_decomposition_by_hold"] = {
            tag: {k: round(sum(c["previous_" + k] for c in code_cycles if "previous_" + k in c and (c["group"] == "overnight") == overnight), 4)
                  for k in ("entry_edge_cny", "held_mid_drift_cny", "exit_edge_cny")}
            for tag, overnight in (("same_day", False), ("overnight", True))}
        stats["largest_cycles"] = [{k: c[k] for k in ("entry_time", "exit_time", "gross_cents", "net_cents", "duration_seconds", "group")} for c in sorted(code_cycles, key=lambda c: abs(c["net_cents"]), reverse=True)[:6]]
        rows.append(stats)
        all_cycles.extend(code_cycles)
        print(f"{index}/{len(paths)} {code} net={s['pnl_cny']} cycles={len(cycles)} raw={stats['raw_cycle_count']}", flush=True)
    total = {k: round(sum(r[k] for r in rows), 2) for k in ("pnl_cny", "realized_gross_cny", "tail_gross_cny", "fees_cny")}
    assert abs(total["pnl_cny"] - source_summary["total_independent_account_pnl_cny"]) < 1e-7
    total["groups"] = {tag: group_cycles([c for c in all_cycles if c["group"] == tag]) for tag in ("same_le_5s", "same_5_60s", "same_1_5m", "same_5_30m", "same_gt_30m", "overnight")}
    total["all_same_day"] = group_cycles([c for c in all_cycles if c["group"] != "overnight"])
    total["quote_decomposition"] = {k: round(sum(r["quote_decomposition"][k] for r in rows), 4) for k in ("entry_edge_cny", "held_mid_drift_cny", "exit_edge_cny")}
    total["raw_cycle_count"] = sum(r["raw_cycle_count"] for r in rows)
    total["fee_loss_codes"] = [r["code"] for r in rows if r["gross_positive_but_fee_loss"]]
    feature_groups = {}
    for name, predicate in {
        "entry_planned_net_nonpositive": lambda c: c["entry_fill"].get("created_planned_round_net_cny", 1) <= 0,
        "entry_planned_net_positive": lambda c: c["entry_fill"].get("created_planned_round_net_cny", 0) > 0,
        "immediate_ask_target_underwater": lambda c: c["entry_fill"].get("immediate_sell_target_net_cny", 1) < 0,
        "immediate_ask_target_nonnegative": lambda c: c["entry_fill"].get("immediate_sell_target_net_cny", -1) >= 0,
        "past60_down_more_than_planned_width": lambda c: bool((q := c["entry_fill"]["creation_quote"]) and q.get("past60_change_cny") is not None and q["past60_change_cny"] < -max(0, c["entry_fill"].get("created_planned_round_gross_cny", 0))),
    }.items():
        chosen = [c for c in all_cycles if predicate(c)]
        feature_groups[name] = group_cycles(chosen)
        feature_groups[name]["same_day"] = group_cycles([c for c in chosen if c["group"] != "overnight"])
        feature_groups[name]["overnight"] = group_cycles([c for c in chosen if c["group"] == "overnight"])
    payload = dict(model_id=source_summary["model_id"], totals=total, rows=rows, feature_groups=feature_groups,
                   verified_inputs=verified_inputs, verified_accounts=len(rows), midpoint_exclusions=errors,
                   daily_tail_quotes=daily_tails, cycles=all_cycles,
                   boundary="Descriptive attribution only; overnight labels, future markouts and winning-cycle removals are not executable strategies. Fixed 64 selected on 2026-09-11; no night execution.")
    (OUT / "profit_sources.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    write_report(payload)
    manifest[str(Path(__file__).relative_to(ROOT))] = sha(Path(__file__))
    for output in (OUT / "profit_sources.json", OUT / "profit_sources.md"):
        manifest[str(output.relative_to(ROOT))] = sha(output)
    (OUT / "profit_sources_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(totals=total, features=feature_groups, verified_inputs=verified_inputs, excluded=len(errors)), ensure_ascii=False), flush=True)


def write_report(p):
    total, rows = p["totals"], p["rows"]
    index = {r["code"]: r for r in rows}
    md = "# 商品期权赚钱与亏钱来源：冻结旧账户的独立审计\n\n"
    md += "统一单边1.7元、零延迟、每合约最多1手、连续资金库存，只日盘。这里只分解已保存成交，不改变任何交易路径。中点只是归因坐标，不能用来替代真实买一盯市；跨日、盈利集中度及后续价格均为事后描述，不能当未来可知的过滤。\n\n"
    md += f"64只净收益 **{n(total['pnl_cny'])}元 = 闭环毛收益{n(total['realized_gross_cny'])} + 期末尾仓毛收益{n(total['tail_gross_cny'])} − 手续费{n(total['fees_cny'])}**。原始{p['verified_inputs']}输入日哈希逐一核对，64个账户经济恒等式全部成立。\n\n"
    md += "## 持有时间揭示了两种不同生意\n\n同日闭环合计净+12,641元；跨日闭环合计净−98,895.90元。后一类同时可能是被动等待、真实方向暴露及夜间缺少交易机会，不能把持仓结果事后删去当作日内新策略收益。\n\n"
    md += table(["持有组", "闭环", "闭环毛元", "手续费元", "闭环净元", "正回合", "负回合"], [[tag, v["count"], n(v["gross_cny"]), n(v["fees_cny"]), n(v["net_cny"]), v["winning"], v["losing"]] for tag, v in total["groups"].items()])
    md += "\n\n## 全部64只分解\n\n同日/跨日两列均已扣完整回合费用；尾仓毛尚未扣末笔买入费，所以两列加尾仓毛不直接等于最终净收益。完整恒等式使用前三个毛/费用列。\n\n"
    md += table(["合约", "净元", "闭环毛", "尾仓毛", "费", "闭环", "同日净", "跨日净", "去前三赚轮后", "创建时空间≤费轮数"], [[r["code"], n(r["pnl_cny"]), n(r["realized_gross_cny"]), n(r["tail_gross_cny"]), n(r["fees_cny"]), r["complete_cycles"], n(r["groups"]["all_same_day"]["net_cny"]), n(r["groups"]["overnight"]["net_cny"]), n(r["without_best_three_cny"]), r["buy_planned_nonpositive_count"]] for r in sorted(rows, key=lambda r: -r["pnl_cny"])])
    md += "\n\n## 原始盘口归因与因果可见特征\n\n以成交时有效买卖中点分解：买入优势=入场中点−成交买价，持仓漂移=出场中点−入场中点，卖出优势=成交卖价−出场中点。三者严格相加等于闭环毛利，但中点在宽盘口中未必是可成交合理价。以下剔除无效双侧帧仅影响这一分解，不影响账户收益。\n\n"
    md += table(["合约", "有效闭环/全部", "买入优势元", "持仓中点漂移元", "卖出优势元", "买后立即卖目标已亏轮"], [[c, f"{(r:=index[c])['raw_cycle_count']}/{r['complete_cycles']}", *[n(r["quote_decomposition"][k]) for k in ("entry_edge_cny", "held_mid_drift_cny", "exit_edge_cny")], r["buy_immediate_exit_underwater_count"]] for c in FOCUS])
    md += "\n\n**不能把黄金840的−91,330元卖出中点差直接说成卖得太低。** 09-11 13:32:26.5该合约卖出116,980元/手，上一帧正常买卖为112,320/117,000；成交后卖一突然撤到239,980，买一只是112,480，中点虚跳到176,230。于是单轮的坐标分解出现−59,250元卖出差，但这并不代表当时可卖176,230；该轮真实净赚5,876.60元。买一、卖一、成交价和前帧都保存在JSON。\n\n以下改用每笔成交证据的**前一帧**中点再做恒等式，避免把成交后单侧撤单当成利润机会；它仍是归因坐标，不是可成交公允价：\n\n"
    md += table(["合约", "组", "买入相对前帧中点", "两个前帧间中点变化", "卖出相对前帧中点", "合计毛元"], [[c, tag, *[n((v:=index[c]["previous_decomposition_by_hold"][tag])[k]) for k in ("entry_edge_cny", "held_mid_drift_cny", "exit_edge_cny")], n(sum(v.values()))] for c in FOCUS for tag in ("same_day", "overnight")])
    md += "\n\n创建时拟买卖净空间是当前可见ask/bid按旧一跳改善法扣往返3.4元，并未使用后来成交。它仅描述旧成交的分组，新过滤会改写持仓与成交路径，不能直接相减当作回测提升。\n\n"
    md += table(["旧成交特征组", "闭环", "净元", "同日净", "跨日净"], [[k, v["count"], n(v["net_cny"]), n(v["same_day"]["net_cny"]), n(v["overnight"]["net_cny"])] for k, v in p["feature_groups"].items()])
    md += "\n\n其中买后立即卖目标已低于成本的2,490轮净亏60,778.50元。这一状态只有成交之后才知道，**不可反向拒绝已经成交的买单**。同样，拟买卖空间为正的5,005轮仍净亏84,979.50元，说明价差过滤本身不解决跨日方向风险。\n\n"
    md += "## 赚钱和亏钱的具体理由\n\n"
    md += "- **黄金920确实存在较大的日内成交价差机会。** 08-24 10:02:42.5以101,360元/手买，10:10:13以105,980卖，净赚4,616.60元；入场前买卖101,340/105,000，出场前104,200/106,000，两边捕获与有利移动共同贡献。39轮里日内净赚9,434.60，跨日净亏1,087.20。它并非全靠隔夜赚钱，但也有08-28→08-31亏15,663.40的大回合。\n"
    md += "- **同到期黄金840的亏损主要来自两笔大持仓下跌。** 08-27买165,220，09-01卖131,980，净亏33,243.40；09-01买132,120，09-02卖110,560，净亏21,563.40。两轮合亏54,806.80；它的日内闭环反而净赚19,718.40。手续费仅103.70，显然不能归因手续费。\n"
    md += "- **白银16000的最大赢家来自跨日重定价。** 09-10买17,452.50，09-11卖23,490，净赚6,034.10；这笔比账户最终+3,543.30还大。日内全部净亏774.40，跨日全部净赚4,296.90。仍有研究价值，但应把价差收益与跨日风险分别研究。\n"
    md += "- **铜114000当前口径的亏损并非单纯卖早。** 日内净亏3,658.40，跨日净亏380.40。两个前帧中点坐标下，日内两边捕获12,380元，被15,780元下压吞掉。09-09 11:28:45买25,900，14:08:08卖24,460，净亏1,443.40，入场前60秒中点已下移270元，而可见价差只有60元；这是可以进一步测试的因果风险例子，并不是所有亏轮都有这么明显的提前信号。\n"
    md += "- **玉米2300主要是交易太碎、费用吞掉毛利。** 1,783轮毛赚3,885元、平均每轮2.18元，小于3.40元往返费；总费6,062.20，最后亏2,177.20。苹果AP701P7200、玻璃FG701C1000、豆粕m2701-P-3050也属于总毛非负但费后负。其他大多数亏损即使免手续费也没转正。\n"
    md += "- **花生与棉花有小规模正循环，也仍夹杂方向收益。** 花生50轮+835，去前三个赢轮仍+40.20；日内+163.80、跨日+671.20。其最大日内一轮2,120→2,397.50净+274.10，持有近5小时，不能全部称瞬间价差。棉花85轮+601，日内+568.20、跨日+32.80；但9月3日及9日尾盘薄买盘形成数千元盯市回撤，后续恢复并不证明可忽略这些退出风险。\n\n"
    md += "\n\n## 关键合约原始回合\n\n黄金两执行价不共享账户：920的获利与840的亏损不能仅用同一标的行情解释，需要看买价、退出价和实际持仓区间。所有金银铜合约均保留。\n\n"
    for code in FOCUS:
        r = index[code]
        md += f"### {code}\n\n净{n(r['pnl_cny'])}元，同日{n(r['groups']['all_same_day']['net_cny'])}元，跨日{n(r['groups']['overnight']['net_cny'])}元。去掉最大三个盈利闭环后的账户值{n(r['without_best_three_cny'])}元，只作集中度压力描述，不删除真实盈利。\n\n"
        md += table(["入场", "出场", "净元", "持仓小时", "组"], [[c["entry_time"], c["exit_time"], n(c["net_cents"] / 100), n(c["duration_seconds"] / 3600), c["group"]] for c in r["largest_cycles"]])
        md += "\n\n"
    md += "## 收盘薄买盘：保持真实盯市，同时识别报价位置\n\n末价可能是旧成交，买一可能是真实很薄的退出深度；两者都不冒充无风险价值。下表是已持仓日尾的买一与末价差最大例子，需与随后真实成交共同复核。\n\n"
    md += table(["合约", "日期", "当日净", "买一元/手", "卖一元/手", "末价元/手", "买一手数", "末价−买一"], [[d["code"], d["date"], n(d["daily_pnl_cny"]), n(d["bid_cny"]), n(d["ask_cny"]), n(d["last_cny"]), d["bid_quantity"], n(d["last_minus_bid_cny"])] for d in sorted(p["daily_tail_quotes"], key=lambda d: -d["last_minus_bid_cny"])[:20]])
    md += "\n\n## 有因果依据的少量候选方向\n\n1. 报价质量与费用分离：把当前一跳改善后的实际拟买卖空间至少覆盖3.4元往返费作为独立入场对照，现金记账仍按1.7元。先研究不赚钱的机械循环，不能按过去净收益赢家白名单筛选。\n2. 近期波幅/下压与空间比较：只有当前可见过去窗口变化才可入条件；宽价差若被持续价格下移吞掉，并不是低波动收益。需要完整重跑及反例，不直接把持仓漂移负的历史回合删掉。\n3. 日内与跨日交易模式分开：测试有真实盘口可执行的时间退出/尾盘停开新仓，代价是可能丢失白银等隔夜盈利。不能用事后持仓标签计算虚构改善，也不能把末价当平仓价。\n4. 成交后低卖原因独立检验：立即卖目标已跌破成本的回合可研究报价在跌势中的跟随方式，但永久成本价保本卖会增加锁仓，不能无条件规定不亏不卖。\n\n64只与原始盈利回合全部保留。本报告不运行新策略、不改旧模型、不声称任何候选已经有效。\n"
    (OUT / "profit_sources.md").write_text(md, encoding="utf-8")


if __name__ == "__main__":
    main()
