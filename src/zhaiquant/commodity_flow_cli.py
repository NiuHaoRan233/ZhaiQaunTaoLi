"""Run with python -m zhaiquant.commodity_flow_cli. Paper execution only."""
import argparse
from collections import Counter
from datetime import datetime
from html import escape
import json
from pathlib import Path
import time

from .commodity_flow_strategy import FAMILY, VARIANTS
from .commodity_flow_paper import (PaperStore, audit_paper, canonical, cents, read_report,
    session_at, set_entry_paused, validate_config, writer_lock)

ROOT = Path(__file__).resolve().parents[2]
AREA = ROOT / "广义套利"
CONFIG = AREA / "commodity_strategy" / "strategy_r2.json"
DB = AREA / "data" / "commodity_flow_v01_r2" / "paper.sqlite3"
REPORT = AREA / "reports" / "commodity_flow_v01_r2"


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    tmp.replace(path)


def initialize(path):
    base = AREA / "reports"
    frozen = json.loads((base / "commodity_optimization_20260912/candidate_contract.json").read_text(encoding="utf-8"))
    sources = {}
    for p in [base/"dadao_v1_screen/matrix.json", *sorted(base.glob("dadao_v1_validation_*/matrix.json"))]:
        for item in json.loads(p.read_text(encoding="utf-8"))["inputs"].values():
            code = item["code"]
            if code not in sources or sources[code]["date"] > item["date"]:
                sources[code] = item
    instruments = []
    for code in frozen["code_list"]:
        item = sources[code]
        detail = item["detail"]
        instruments.append(dict(code=code, name=detail.get("ProductName", code),
            unit=item["unit"], price_tick=item["price_tick"], expiry=str(detail["ExpireDate"]),
            initial_cents=item["initial_cents"], budget_source_date=item["date"]))
    config = dict(schema=1, family=FAMILY, variants=list(VARIANTS), fee_cents=170,
                  delay_ms=0, capacity=1, instruments=instruments,
                  universe="Frozen 64 contracts from 2026-09-12, not a winner whitelist")
    validate_config(config)
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != config:
            raise ValueError("Existing strategy config differs; not overwriting")
    else:
        write_json(path, config)
    return config


def qmt_connect(port):
    from xtquant import xtdata
    xtdata.enable_hello = False
    client = qmt_call(xtdata.connect, port=port)
    if not client or not qmt_call(client.is_connected):
        raise ConnectionError("QMT market connection unavailable")
    return xtdata, client


def qmt_call(function, *args, **kwargs):
    """The vendor raises plain Exception on transport failures as well."""
    try:
        return function(*args, **kwargs)
    except Exception as exc:
        raise ConnectionError(str(exc)) from exc


def check_terms(xt, config):
    errors = []
    for item in config["instruments"]:
        try:
            detail = qmt_call(xt.get_instrument_detail, item["code"], True) or {}
            unit = detail.get("OptUnit") or detail.get("VolumeMultiple")
            if (float(unit) != float(item["unit"]) or
                cents(detail["PriceTick"], unit) != cents(item["price_tick"], item["unit"]) or
                str(detail["ExpireDate"]) != item["expiry"]):
                errors.append(item["code"]+": contract terms changed")
        except (KeyError, TypeError, ValueError):
            errors.append(item["code"]+": missing contract terms")
    return errors


def table(headers, rows):
    def row(values, tag):
        return "<tr>"+"".join(f"<{tag}>{escape(str(v))}</{tag}>" for v in values)+"</tr>"
    return '<div class="scroll"><table><thead>'+row(headers,"th")+"</thead><tbody>"+"".join(row(r,"td") for r in rows)+"</tbody></table></div>"


def report(db_path, folder, now=None):
    now = now if now is not None else time.time_ns()//1_000_000
    data = read_report(db_path, now)
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder/"paper_status.json", data)
    totals = {}
    for v in VARIANTS:
        chosen = [a for a in data["accounts"] if a["summary"]["mode"] == v]
        totals[v] = dict(pnl=round(sum(a["summary"]["pnl_cny"] for a in chosen),2),
                         fills=sum(a["summary"]["fill_count"] for a in chosen),
                         positions=sum(a["summary"]["end_inventory"] for a in chosen),
                         initial=round(sum(a["summary"]["initial_cash_cny"] for a in chosen),2))
    parts = ["<h1>商品期权双侧成交策略0.1 · 纸面账户</h1>",
             "<p>主策略：双侧成交＋有限等待；对照：双侧成交入场。单边1.7元，额外延迟0，每合约最多1手。仅使用QMT只读行情。</p>",
             "<p>每个模型的64个资金账户独立，不共享现金；页面收益含尾仓，买一估值陈旧时明确标注。历史21,839.70元属于旧研究样本，不是这里的前向成绩。</p>",
             f"<p>生成时间：{datetime.fromtimestamp(now/1000).isoformat(timespec='seconds')}；开仓控制：{'暂停' if data['entries_paused'] else '允许（仍须满足行情与策略条件）'}；最新接收：{escape(str(data['latest_input']))}</p>",
             "<p><a href='paper_status.json'>完整状态和日度JSON</a> · 此页为静态快照，运行“查看商品期权策略日报.cmd”刷新。</p>"]
    parts.append(table(["模型","初始资金和/元","净收益和/元","成交单边数","尾仓/手"],
                       [[v,f"{x['initial']:,.2f}",f"{x['pnl']:,.2f}",x['fills'],x['positions']] for v,x in totals.items()]))
    reasons = {"waiting_for_market":"等待新行情", "wrong_date":"行情日期不是今天", "stale_tick":"行情已陈旧",
               "outside_session":"日盘时段外", "no_recent_two_sided_flow":"近5分钟缺双侧成交", "insufficient_net_edge":"费用后空间不足",
               "current_top":"维护盘口委托", "bounded_cost_exit":"有限等待卖出", "entry_disabled":"禁止新开仓",
               "shutdown":"已停止，持仓保留", "startup":"重启后等待新证据", "disconnected":"QMT断开",
               "expired_unsettled":"合约已到期，待研究结算", "insufficient_cash":"资金不足一手",
               "invalid_book":"无效双侧盘口", "entry_paused":"已暂停开仓", "entry_resumed":"恢复后等待新证据"}
    dates = sorted({d["date"] for a in data["accounts"] for d in a["daily"]})
    for v in VARIANTS:
        chosen = [a for a in data["accounts"] if a["summary"]["mode"] == v]
        parts.append(f"<h2>{v} · 全部合约</h2>")
        rows = []
        for a in chosen:
            s = a["summary"]
            rows.append([s["code"], f"{s['pnl_cny']:,.2f}",f"{s['fees_cny']:,.2f}",s["end_inventory"],
                         s["fill_count"], "陈旧" if s["stale_tail"] else "无持仓" if not s["end_inventory"] else "有效",
                         reasons.get(s["latest_reason"],s["latest_reason"]),s["pending_order"]["side"] if s["pending_order"] else "无委托"])
        parts.append(table(["合约","净收益/元","费用/元","尾仓/手","成交数","尾仓估值","当前判断","纸面委托"], rows))
        parts.append("<h2>全部合约日度净值变化</h2>")
        daily_rows = []
        for a in chosen:
            ds = {d["date"]:d for d in a["daily"]}
            daily_rows.append([a["summary"]["code"], *[f"{ds[d]['pnl_cny']:,.2f}" if d in ds else "缺失/未开始" for d in dates]])
        parts.append(table(["合约", *(dates or ["暂无日盘行情"])],
                           daily_rows if dates else [[a["summary"]["code"],"等待首个有效日盘输入"] for a in chosen]))
    html = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>商品期权策略0.1纸面日报</title><style>body{font:16px/1.6 system-ui,"Microsoft YaHei",sans-serif;color:#20362e;background:#f4f5f0;padding:24px;max-width:1500px;margin:auto}h1,h2{color:#155b49}.scroll{overflow:auto;max-height:650px;margin:18px 0;border:1px solid #cad7cf}table{border-collapse:collapse;background:white;white-space:nowrap;width:100%}th,td{padding:8px 12px;text-align:right;border-bottom:1px solid #dde5df}th{position:sticky;top:0;background:#dce9df}td:first-child,th:first-child{text-align:left;position:sticky;left:0;background:#edf3ee}p{max-width:1200px}</style><body>'+"".join(parts)+"</body></html>"
    tmp = folder/"纸面策略日报.html.tmp"
    tmp.write_text(html,encoding="utf-8"); tmp.replace(folder/"纸面策略日报.html")
    return dict(totals=totals, report=str(folder/"纸面策略日报.html"), latest_input=data["latest_input"])


def run_paper(args, config):
    sequences, xt, store = [], None, None
    with writer_lock(args.db):
        store = PaperStore(args.db, config)
        try:
            store.boundary(time.time_ns()//1_000_000, "startup")
            connection = None
            last_report = 0
            sleeping = False
            while True:
                now = time.time_ns()//1_000_000
                store.refresh_control(now)
                if not args.once and session_at(now) is None:
                    if not sleeping:
                        store.boundary(now, "outside_session")
                        print(canonical(report(args.db,args.output)),flush=True)
                        sleeping = True
                    time.sleep(5)
                    continue
                sleeping = False
                try:
                    if connection is None or not qmt_call(connection.is_connected):
                        store.boundary(now,"disconnected")
                        if xt:
                            for seq in sequences:
                                try: xt.unsubscribe_quote(seq)
                                except Exception: pass
                        sequences = []
                        xt, connection = qmt_connect(args.port)
                        errors = check_terms(xt,config)
                        if errors:
                            raise ValueError("; ".join(errors))
                        for item in config["instruments"]:
                            seq = qmt_call(xt.subscribe_quote, item["code"], period="tick", count=0)
                            if seq is None or seq < 0:
                                raise ConnectionError("Subscription failed: "+item["code"])
                            sequences.append(seq)
                    codes = [x["code"] for x in config["instruments"]]
                    ticks = qmt_call(xt.get_full_tick, codes)
                    if not isinstance(ticks, dict):
                        raise ConnectionError("QMT returned no quote dictionary")
                    counts = Counter()
                    for code in codes:
                        # Each account's decision timestamp is sampled at processing time.
                        counts[store.ingest(code,ticks.get(code,{}),time.time_ns()//1_000_000)] += 1
                    if args.once or time.monotonic()-last_report >= 30:
                        print(canonical(dict(input_status=dict(counts),**report(args.db,args.output))),flush=True)
                        last_report = time.monotonic()
                except (ConnectionError, OSError, RuntimeError) as exc:
                    store.boundary(time.time_ns()//1_000_000,"disconnected")
                    connection = None
                    print(canonical(dict(status="disconnected",detail=str(exc))),flush=True)
                    if args.once:
                        raise
                    time.sleep(5)
                if args.once:
                    break
                time.sleep(0.5)
        finally:
            if store:
                try:
                    store.boundary(time.time_ns()//1_000_000,"shutdown")
                    report(args.db,args.output)
                finally:
                    store.close()
            if xt:
                for seq in sequences:
                    try: xt.unsubscribe_quote(seq)
                    except Exception: pass


def main():
    p = argparse.ArgumentParser(description="商品期权双侧成交策略0.1，只读QMT纸面账户")
    p.add_argument("command",choices=("init","doctor","paper","report","audit","pause-entry","resume-entry"))
    p.add_argument("--config",type=Path,default=CONFIG)
    p.add_argument("--db",type=Path,default=DB)
    p.add_argument("--output",type=Path,default=REPORT)
    p.add_argument("--port",type=int,default=58611)
    p.add_argument("--once",action="store_true",help="只读取一次当前快照，不使用历史补成交")
    args = p.parse_args()
    if args.command == "init":
        cfg = initialize(args.config)
        print(canonical(dict(config=str(args.config),contracts=len(cfg["instruments"]),
            initial_cash_per_model_cny=sum(x["initial_cents"] for x in cfg["instruments"])/100)))
        return
    if args.command == "report":
        print(canonical(report(args.db,args.output))); return
    if args.command == "audit":
        result = audit_paper(args.db)
        write_json(args.output/"paper_audit.json",result)
        print(canonical(result)); return
    if args.command in ("pause-entry","resume-entry"):
        set_entry_paused(args.db,args.command=="pause-entry")
        print("开仓控制请求已保存；运行进程会记录生效，已有库存仍可退出。")
        return
    config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
    if args.command == "doctor":
        xt, connection = qmt_connect(args.port)
        errors = check_terms(xt,config)
        print(canonical(dict(connected=connection.is_connected(),port=args.port,
            contracts=len(config["instruments"]),terms_errors=errors,orders_enabled=False)))
        if errors:
            raise SystemExit(1)
        return
    try:
        run_paper(args,config)
    except KeyboardInterrupt:
        print("纸面策略已停止，现金和持仓已保留。")


if __name__ == "__main__":
    main()
