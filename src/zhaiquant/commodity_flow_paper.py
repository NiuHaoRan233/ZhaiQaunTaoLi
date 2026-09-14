"""Isolated SQLite paper accounts and read-only QMT snapshot normalization."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
import hashlib
import json
import math
import os
import re
import sqlite3

from .commodity_flow_strategy import FAMILY, VARIANTS, FlowAccount
from .option_top_cycle_research import Event

SHANGHAI = timezone(timedelta(hours=8))
SCHEMA = 1


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def local_date(ts):
    return datetime.fromtimestamp(ts/1000, SHANGHAI).strftime("%Y%m%d")


def session_at(ts):
    d = datetime.fromtimestamp(ts/1000, SHANGHAI)
    seconds = d.hour*3600+d.minute*60+d.second+d.microsecond/1e6
    if d.weekday() >= 5:
        return None
    for n, (a, b) in enumerate(((540, 615), (630, 690), (810, 900))):
        if a*60 <= seconds < b*60:
            return int(d.strftime("%Y%m%d"))*10+n
    return None


def integer(value, name, minimum=0):
    if isinstance(value, bool):
        raise ValueError(name)
    x = Decimal(str(value))
    if not x.is_finite() or x != x.to_integral_value() or x < minimum:
        raise ValueError(name)
    return int(x)


def cents(value, unit):
    x = Decimal(str(value))*Decimal(str(unit))*100
    if not x.is_finite() or x < 0:
        raise ValueError("Invalid price")
    return int(x.to_integral_value())


def validate_config(config):
    if config.get("schema") != SCHEMA or config.get("family") != FAMILY:
        raise ValueError("Unknown strategy configuration")
    if (config.get("fee_cents"), config.get("delay_ms"), config.get("capacity")) != (170, 0, 1):
        raise ValueError("Fee/delay/capacity change requires a new strategy version")
    if config.get("variants") != list(VARIANTS):
        raise ValueError("Preserve the main and comparison identities")
    items = config.get("instruments", [])
    if not items or len({x["code"] for x in items}) != len(items):
        raise ValueError("Missing or duplicate instruments")
    for item in items:
        if not re.fullmatch(r"[A-Za-z0-9-]+\.(SF|DF|ZF|GF)", item["code"]):
            raise ValueError("Expected a supported commodity option code")
        if not re.search(r"[CP]-?\d+\.", item["code"]):
            raise ValueError("Only option contracts are supported")
        unit = Decimal(str(item["unit"]))
        tick = Decimal(str(item["price_tick"]))
        if not unit.is_finite() or not tick.is_finite() or min(unit, tick) <= 0:
            raise ValueError("Invalid contract terms")
        if unit*tick*100 != cents(tick, unit) or cents(tick, unit) <= 0:
            raise ValueError("Unsupported fractional-cent tick")
        integer(item["initial_cents"], "initial cash", 1)
        datetime.strptime(item["expiry"], "%Y%m%d")
    return config


def clean_tick(raw, spec):
    """Retain only known numeric QMT fields; reject malformed observations."""
    r = {"time": integer(raw["time"], "time", 1),
         "volume": integer(raw["volume"], "volume"),
         "amount": float(raw["amount"]), "lastPrice": float(raw["lastPrice"])}
    if not math.isfinite(r["amount"]) or r["amount"] < 0:
        raise ValueError("Invalid amount")
    cents(r["lastPrice"], spec["unit"])
    for price_key, qty_key in (("bidPrice", "bidVol"), ("askPrice", "askVol")):
        if len(raw[price_key]) == 0 or len(raw[qty_key]) == 0:
            raise ValueError("Missing top quote")
        r[price_key] = [float(raw[price_key][0])]
        r[qty_key] = [integer(raw[qty_key][0], qty_key)]
        p = cents(r[price_key][0], spec["unit"])
        if p % cents(spec["price_tick"], spec["unit"]):
            raise ValueError("Off-grid price")
    return r


def normalize(raw, previous, spec):
    unit = spec["unit"]
    ts = raw["time"]
    bid, ask = (cents(raw[k][0], unit) for k in ("bidPrice", "askPrice"))
    last = cents(raw["lastPrice"], unit)
    bq, aq = raw["bidVol"][0], raw["askVol"][0]
    session = session_at(ts)
    if session is None:
        raise ValueError("Outside day session")
    quantity, quality, side, strict = 0, False, "unknown", "unknown"
    prev_ts = ts
    if previous:
        prev_ts = previous["time"]
        dv = raw["volume"]-previous["volume"]
        da = round((raw["amount"]-previous["amount"])*100)
        pb, pa = (cents(previous[k][0], unit) for k in ("bidPrice", "askPrice"))
        pl = cents(previous["lastPrice"], unit)
        if dv > 0 and da >= -2 and last > 0 and session == session_at(prev_ts):
            quantity = 1
            if pa > 0 and last >= pa:
                side = "buy"
            elif pb > 0 and last <= pb:
                side = "sell"
            elif last > pl:
                side = "buy"
            elif last < pl:
                side = "sell"
            quality = (da > 0 and 0 < pb < pa and min(previous["bidVol"][0], previous["askVol"][0]) > 0
                       and 0 < ts-prev_ts <= 60000)
            if quality:
                strict = "buy" if last >= pa else "sell" if last <= pb else "unknown"
    return Event(ts, prev_ts, session, bid, ask, bq, aq, ((bid, bq),), ((ask, aq),),
                 last, quantity, 0, side, quality, strict)


@contextmanager
def writer_lock(path):
    """OS lock releases on crash. Stale PID files cannot unlock another writer."""
    p = Path(str(path)+".lock")
    p.parent.mkdir(parents=True, exist_ok=True)
    f = p.open("a+b")
    try:
        # Reading another writer's locked byte raises on Windows. stat is safe.
        if os.fstat(f.fileno()).st_size == 0:
            f.write(b"0"); f.flush()
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        f.close()
        raise RuntimeError("Another commodity paper writer is already running") from None
    try:
        yield
    finally:
        f.close()


class PaperStore:
    """One transaction contains the input, all two-model effects and snapshots.

    On failure reload committed state; recovery never resorts market timestamps.
    The caller holds writer_lock for the lifetime of this writer.
    """
    def __init__(self, path, config):
        self.config = validate_config(config)
        self.specs = {x["code"]: x for x in config["instruments"]}
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=10)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS inputs(seq INTEGER PRIMARY KEY, received_ms INTEGER NOT NULL,
                code TEXT, status TEXT NOT NULL, raw_json TEXT, event_json TEXT);
            CREATE TABLE IF NOT EXISTS accounts(code TEXT, variant TEXT, state TEXT NOT NULL,
                PRIMARY KEY(code,variant));
            CREATE TABLE IF NOT EXISTS previous(code TEXT PRIMARY KEY, raw_json TEXT);
            CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY, input_seq INTEGER NOT NULL,
                model_id TEXT NOT NULL, code TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS control(id INTEGER PRIMARY KEY CHECK(id=1),
                paused INTEGER NOT NULL, revision INTEGER NOT NULL);
            INSERT OR IGNORE INTO control VALUES(1,0,0);
        """)
        files = [Path(__file__), Path(__file__).with_name("commodity_flow_strategy.py"),
                 Path(__file__).with_name("commodity_flow_cli.py"),
                 Path(__file__).with_name("option_top_cycle_research.py")]
        contract = dict(config=config, source_hashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files})
        encoded = canonical(contract)
        saved = self.db.execute("SELECT value FROM meta WHERE key='contract'").fetchone()
        if saved and saved[0] != encoded:
            self.db.close()
            raise ValueError("Frozen paper contract/source changed; use a new registered identity/account")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('contract',?)", (encoded,))
            for code, item in self.specs.items():
                for variant in VARIANTS:
                    a = FlowAccount(code, variant, item["initial_cents"], cents(item["price_tick"], item["unit"]), "paper_arrival")
                    self.db.execute("INSERT OR IGNORE INTO accounts VALUES(?,?,?)", (code, variant, canonical(a.snapshot())))
        self._reload()

    def _reload(self):
        self.accounts = {}
        for code, variant, state in self.db.execute("SELECT code,variant,state FROM accounts"):
            item = self.specs[code]
            self.accounts[code, variant] = FlowAccount(code, variant, item["initial_cents"],
                cents(item["price_tick"], item["unit"]), "paper_arrival", json.loads(state))
        self.previous = {code: json.loads(raw) if raw else None
                         for code, raw in self.db.execute("SELECT code,raw_json FROM previous")}
        row = self.db.execute("SELECT paused,revision FROM control WHERE id=1").fetchone()
        self.paused, self.revision = bool(row[0]), row[1]

    def close(self):
        self.db.close()

    def _effects(self, seq, account, changes):
        for kind in ("orders", "fills", "cycles", "cancels"):
            for payload in changes.get(kind, []):
                self.db.execute("INSERT INTO ledger(input_seq,model_id,code,kind,payload) VALUES(?,?,?,?,?)",
                    (seq, account.model, account.code, kind, canonical(payload)))
        self.db.execute("UPDATE accounts SET state=? WHERE code=? AND variant=?",
                        (canonical(account.snapshot()), account.code, account.variant))

    def boundary(self, ts, reason):
        try:
            with self.db:
                seq = self.db.execute("INSERT INTO inputs(received_ms,status,raw_json) VALUES(?,?,?)",
                    (ts, reason, canonical(dict(entries_paused=self.paused,control_revision=self.revision)))).lastrowid
                for account in self.accounts.values():
                    self._effects(seq, account, {"cancels": account.pause(ts, reason)})
                self.db.execute("DELETE FROM previous")
                self.previous.clear()
        except Exception:
            self._reload()
            raise

    def refresh_control(self, ts):
        paused, revision = self.db.execute("SELECT paused,revision FROM control WHERE id=1").fetchone()
        if revision != self.revision:
            self.paused, self.revision = bool(paused), revision
            # Drop short-window evidence on either transition; exits remain allowed.
            self.boundary(ts, "entry_paused" if paused else "entry_resumed")

    def ingest(self, code, raw, received_ms):
        if code not in self.specs:
            raise ValueError("Unknown contract")
        self.refresh_control(received_ms)
        spec = self.specs[code]
        status, tick, e = "accepted", None, None
        try:
            tick = clean_tick(raw, spec)
            ts = tick["time"]
            today = local_date(received_ms)
            if local_date(ts) != today:
                status = "wrong_date"
            elif ts-received_ms > 1000:
                status = "future_tick"
            elif received_ms-ts > 5000:
                status = "stale_tick"
            elif session_at(ts) is None or session_at(received_ms) is None:
                status = "outside_session"
            elif session_at(ts) != session_at(received_ms):
                status = "session_arrival_mismatch"
            elif today > spec["expiry"]:
                status = "expired_unsettled"
            else:
                # Includes previously accepted invalid-book observations; no rollback.
                last = self.accounts[code, VARIANTS[0]].s["last_ts"]
                if last is not None and ts <= last:
                    status = "duplicate_tick" if ts == last else "out_of_order_tick"
                else:
                    e = normalize(tick, self.previous.get(code), spec)
        except (ValueError, KeyError, TypeError, IndexError, OverflowError, InvalidOperation):
            status = "malformed_tick"
        # A duplicate adds no evidence and does not cancel a valid existing intent.
        # Stale duplicates above still cancel, because freshness has expired.
        try:
            with self.db:
                seq = self.db.execute("INSERT INTO inputs(received_ms,code,status,raw_json,event_json) VALUES(?,?,?,?,?)",
                    (received_ms, code, status, canonical(tick) if tick else None,
                     canonical(asdict(e)) if e else None)).lastrowid
                for variant in VARIANTS:
                    account = self.accounts[code, variant]
                    if status == "accepted":
                        prior = self.previous.get(code)
                        reset = prior and (tick["volume"] < prior["volume"] or tick["amount"] < prior["amount"]-0.02)
                        reset_cancels = account.pause(received_ms, "cumulative_reset") if reset else []
                        changes = account.step(e, local_date(e.ts), decision_ts=max(received_ms, e.ts),
                                               allow_entry=not self.paused and today < spec["expiry"])
                        changes["cancels"] = reset_cancels+changes["cancels"]
                    elif status == "duplicate_tick":
                        changes = {}
                    else:
                        changes = {"cancels": account.pause(received_ms, status)}
                    self._effects(seq, account, changes)
                if status == "accepted":
                    self.previous[code] = tick
                elif status != "duplicate_tick":
                    self.previous[code] = None
                self.db.execute("INSERT OR REPLACE INTO previous VALUES(?,?)",
                                (code, canonical(self.previous.get(code))))
        except Exception:
            self._reload()
            raise
        return status


def set_entry_paused(path, paused):
    """Only writes a control request; the running writer journals its application."""
    uri = Path(path).resolve().as_uri()+"?mode=rw"
    db = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        with db:
            db.execute("UPDATE control SET paused=?,revision=revision+1 WHERE id=1", (int(paused),))
    finally:
        db.close()


def read_report(path, asof_ms):
    uri = Path(path).resolve().as_uri()+"?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        db.execute("BEGIN")
        contract = json.loads(db.execute("SELECT value FROM meta WHERE key='contract'").fetchone()[0])
        rows = []
        for code, variant, payload in db.execute("SELECT code,variant,state FROM accounts ORDER BY variant,code"):
            state = json.loads(payload)
            a = FlowAccount(code, variant, state["initial"], state["tick"], "paper_arrival", state)
            rows.append(dict(summary=a.summary(asof_ms), daily=a.daily()))
        latest = db.execute("SELECT seq,received_ms,status FROM inputs ORDER BY seq DESC LIMIT 1").fetchone()
        counts = dict(db.execute("SELECT status,count(*) FROM inputs GROUP BY status"))
        control = db.execute("SELECT paused,revision FROM control WHERE id=1").fetchone()
        return dict(family=FAMILY, clock="paper_arrival", asof_ms=asof_ms,
                    contract_sha256=sha(contract), accounts=rows, latest_input=latest,
                    input_status_counts=counts, entries_paused=bool(control[0]), control_revision=control[1])
    finally:
        db.close()


def audit_paper(path):
    """Read-only reconstruction from this account's own arrival journal.

    Rebuild decisions in sequence order, plus independent fill cash accounting.
    This verifies recovery data; it never writes/replaces account snapshots.
    """
    db = sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True,timeout=10)
    try:
        db.execute("BEGIN")
        contract = json.loads(db.execute("SELECT value FROM meta WHERE key='contract'").fetchone()[0])
        for name, expected in contract['source_hashes'].items():
            if hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() != expected:
                raise ValueError("Use the frozen runtime source for journal audit")
        specs = {x['code']:x for x in contract['config']['instruments']}
        accounts = {(c,v):FlowAccount(c,v,x['initial_cents'],cents(x['price_tick'],x['unit']),'paper_arrival')
                    for c,x in specs.items() for v in VARIANTS}
        expected = {}
        for seq,model,code,kind,payload in db.execute('SELECT input_seq,model_id,code,kind,payload FROM ledger ORDER BY id'):
            expected.setdefault((seq,model,code,kind),[]).append(json.loads(payload))
        previous, paused, events, frames = {}, False, 0, 0
        def check(seq,account,changes):
            for kind in ('orders','fills','cycles','cancels'):
                if changes.get(kind,[]) != expected.pop((seq,account.model,account.code,kind),[]):
                    raise ValueError(f'Journal effects differ: {seq}/{account.code}/{kind}')
        for seq,received,code,status,payload,encoded in db.execute('SELECT seq,received_ms,code,status,raw_json,event_json FROM inputs ORDER BY seq'):
            events += 1
            raw = json.loads(payload) if payload else None
            if code is None:
                paused = bool(raw['entries_paused'])
                for a in accounts.values():check(seq,a,{'cancels':a.pause(received,status)})
                previous.clear()
                continue
            if status == 'accepted':
                e = normalize(raw,previous.get(code),specs[code])
                if canonical(asdict(e)) != canonical(json.loads(encoded)):
                    raise ValueError(f'Normalized input differs: {seq}')
                prior = previous.get(code)
                reset = prior and (raw['volume']<prior['volume'] or raw['amount']<prior['amount']-0.02)
                for v in VARIANTS:
                    a=accounts[code,v]
                    cancels=a.pause(received,'cumulative_reset') if reset else []
                    changes=a.step(e,local_date(e.ts),decision_ts=max(received,e.ts),
                                   allow_entry=not paused and local_date(e.ts)<specs[code]['expiry'])
                    changes['cancels']=cancels+changes['cancels']
                    check(seq,a,changes);frames+=1
                previous[code]=raw
            elif status == 'duplicate_tick':
                for v in VARIANTS:check(seq,accounts[code,v],{})
            else:
                for v in VARIANTS:
                    a=accounts[code,v];check(seq,a,{'cancels':a.pause(received,status)})
                previous[code]=None
        if expected:
            raise ValueError('Ledger contains effects without causal input')
        saved = {(c,v):json.loads(s) for c,v,s in db.execute('SELECT code,variant,state FROM accounts')}
        for key,a in accounts.items():
            if a.snapshot()!=saved[key]:raise ValueError(f'Persisted state differs: {key}')
        ledgers={a.model+'|'+a.code:dict(cash=a.s['initial'],inventory=0,fees=0,count=0) for a in accounts.values()}
        for model,code,payload in db.execute("SELECT model_id,code,payload FROM ledger WHERE kind='fills' ORDER BY id"):
            f=json.loads(payload);s=ledgers[model+'|'+code]
            if f['quantity']!=1 or f['fee_cents']!=170:raise ValueError('Fill units/fee differ')
            if f['side']=='buy':
                if s['inventory']:raise ValueError('Added to a position')
                s['cash']-=f['price_cents']+170;s['inventory']=1
            else:
                if s['inventory']!=1:raise ValueError('Sold without a position')
                s['cash']+=f['price_cents']-170;s['inventory']=0
            s['fees']+=170;s['count']+=1
            if (s['cash'],s['inventory'])!=(f['cash_cents'],f['inventory']) or s['cash']<0:
                raise ValueError('Fill cash or inventory mismatch')
        for a in accounts.values():
            x=ledgers[a.model+'|'+a.code]
            if (x['cash'],x['inventory'],x['fees'])!=(a.s['cash'],a.s['inventory'],a.s['fees']):
                raise ValueError('Final independent accounting mismatch')
        return dict(status='passed',accounts=len(accounts),journal_inputs=events,replayed_account_frames=frames,
                    fill_sides=sum(x['count'] for x in ledgers.values()),contract_sha256=sha(contract))
    finally:
        db.close()
