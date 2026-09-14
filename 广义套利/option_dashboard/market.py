"""Read-only commodity option catalog, arrival journal and causal L1 tape."""
from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import math
import queue
import re
import sqlite3
import threading
import time
import tomllib

BASE = Path(__file__).resolve().parents[1]
ROOT = BASE.parent
DATA = BASE / 'data' / 'option_dashboard'
TZ = timezone(timedelta(hours=8))
OPTION = re.compile(r'^([a-zA-Z]+)(\d{3,4})-?([CP])-?(\d+(?:\.\d+)?)\.(SF|DF|ZF|GF|INE)$')
FUTURE = re.compile(r'^([a-zA-Z]+)(\d{3,4})\.(SF|DF|ZF|GF|INE)$')
MARKETS = {'SF': '上期所', 'DF': '大商所', 'ZF': '郑商所', 'GF': '广期所', 'INE': '能源中心'}
SECTORS = ['上期所', '大商所', '郑商所', '广期所', 'GF', '能源中心', '能源交易所', 'INE']
# Exchange product names where this QMT build returns a code in ProductName.
NAME_FIXES = {'DF:j':'焦炭', 'DF:jm':'焦煤', 'GF:pd':'钯', 'GF:pt':'铂'}


def number(x, default=0):
    try:
        v = float(x)
        return v if math.isfinite(v) and abs(v) < 1e20 else default
    except (TypeError, ValueError):
        return default


def levels(row, key):
    a = row.get(key)
    if a is None:
        return [0.] * 5
    return [max(0, number(v)) for v in list(a)[:5]] + [0.] * max(0, 5-len(a))


def normalized(row):
    return dict(t=int(number(row.get('time'))), last=number(row.get('lastPrice')),
                volume=number(row.get('volume')), amount=number(row.get('amount')),
                oi=number(row.get('openInt')), close=number(row.get('lastClose')),
                bid=levels(row, 'bidPrice'), ask=levels(row, 'askPrice'),
                bv=levels(row, 'bidVol'), av=levels(row, 'askVol'))


def infer(previous, current):
    """One marker per positive cumulative volume increment, never per exchange trade.

    Previous displayed quotes only; no current/future quote or carried tick direction.
    A 60-second limit makes gaps explicit rather than guessing through stale books.
    """
    if previous is None:
        return None
    # The evening session starts a new futures trading cycle. Cumulative volume
    # may restart ABOVE the preceding day's close, so a negative delta alone is
    # insufficient to recognize this boundary. Never count its opening baseline.
    prior_cycle = datetime.fromtimestamp(previous['t']/1000, TZ) - timedelta(hours=18)
    current_cycle = datetime.fromtimestamp(current['t']/1000, TZ) - timedelta(hours=18)
    if prior_cycle.date() != current_cycle.date():
        return None
    delta = current['volume'] - previous['volume']
    if delta <= 0:
        return None
    dt = current['t'] - previous['t']
    side, reason = 'N', '成交位于前盘口价差内'
    b, a = previous['bid'][0], previous['ask'][0]
    if dt <= 0:
        reason = '同毫秒或乱序，方向未判定'
    elif dt > 60_000:
        reason = '前盘口超过60秒，方向未判定'
    elif not (0 < b < a and previous['bv'][0] > 0 and previous['av'][0] > 0):
        reason = '前盘口缺档、锁盘或交叉'
    elif current['last'] <= 0:
        reason = '成交价无效'
    elif current['last'] >= a - 1e-9:
        side, reason = 'B', '末笔成交价达到或超过此前卖一'
    elif current['last'] <= b + 1e-9:
        side, reason = 'S', '末笔成交价达到或低于此前买一'
    return dict(side=side, qty=delta, reason=reason, gap=dt > 60_000,
                amount=max(0, current['amount']-previous['amount']))


def tape(rows):
    points, previous = [], None
    counts = Counter()
    for row in rows:
        p = normalized(row)
        if p['t'] <= 0:
            continue
        p['event'] = infer(previous, p)
        p['reset'] = bool(previous and p['volume'] < previous['volume'])
        p['gap'] = bool(previous and p['t']-previous['t'] > 60_000)
        if p['event']:
            counts[p['event']['side']] += 1
            counts['volume'] += p['event']['qty']
        if p['reset']:
            counts['resets'] += 1
        points.append(p)
        previous = p
    return points, dict(counts)


def date_bounds(date):
    start = datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=TZ)
    return int(start.timestamp()*1000), int((start+timedelta(days=1)).timestamp()*1000)


class Market:
    def __init__(self, data=DATA):
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data / 'arrivals.sqlite3'
        self.lock, self.qmt_lock = threading.RLock(), threading.RLock()
        self.xt = self.client = None
        self.codes, self.futures, self.details, self.quotes = set(), set(), {}, {}
        self.products, self.contracts, self.product_map = [], {}, {}
        self.sectors, self.cache = {}, OrderedDict()
        self.pending = queue.Queue(maxsize=250_000)
        self.stop = threading.Event()
        self.status = dict(connected=False, phase='正在读取本地目录', errors=[], subscribed=[],
                           received=0, written=0, dropped=0, last_callback=None,
                           catalog_at=None, recording_since=datetime.now(TZ).isoformat())
        self.subscriptions = []
        self.history_locks = {}
        self.threads = []
        self.last_fingerprints = {}
        self._init_db()
        self._load_catalog()

    def _init_db(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS arrivals (id INTEGER PRIMARY KEY, code TEXT NOT NULL, t INTEGER NOT NULL, received REAL NOT NULL, raw TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS arrivals_code_time ON arrivals(code,t,id)')
            db.commit()

    def error(self, message):
        with self.lock:
            self.status['errors'] = (self.status['errors'] + [dict(at=datetime.now(TZ).isoformat(), message=str(message))])[-12:]

    def _load_catalog(self):
        paths = sorted((BASE/'data').glob('commodity_inventory_*.json'))
        saved = self.data/'catalog.json'
        if saved.exists():
            paths.append(saved)
        if not paths:
            return
        payload = json.loads(paths[-1].read_text(encoding='utf-8'))
        self.details = payload.get('details', {})
        self.quotes = payload.get('ticks', {})
        self.sectors = payload.get('sectors', {})
        for s in self.sectors.values():
            self.codes.update(s.get('options', []))
            self.futures.update(c for c in s.get('futures', []) if FUTURE.fullmatch(c))
        self.status['catalog_at'] = payload.get('captured_at')
        self._build_catalog()

    def _build_catalog(self):
        names = {}
        for c, d in self.details.items():
            m = OPTION.fullmatch(c)
            if m and d and d.get('ProductName'):
                names[m[5]+':'+m[1]] = d['ProductName'].replace('期权', '')
        names.update(NAME_FIXES)
        products = {}
        contracts = {}
        for code in sorted(self.codes):
            m = OPTION.fullmatch(code)
            if not m:
                continue
            product, month, side, strike, market = m.groups()
            key = market+':'+product
            d = self.details.get(code) or {}
            item = dict(code=code, product=key, symbol=product, month=month, side=side,
                        strike=float(strike), market=market, name=names.get(key, product),
                        expiry=d.get('ExpireDate', ''), unit=number(d.get('OptUnit') or d.get('VolumeMultiple')),
                        tick=number(d.get('PriceTick')),
                        underlying=product+month+'.'+market)
            contracts[code] = item
            p = products.setdefault(key, dict(id=key, name=names.get(key, product), symbol=product,
                                              market=market, count=0, months=set()))
            p['count'] += 1
            p['months'].add(month)
        for p in products.values():
            p['months'] = sorted(p['months'])
        with self.lock:
            self.contracts = contracts
            self.product_map = products
            self.products = sorted(products.values(), key=lambda p:(p['market'], p['symbol'].lower()))

    def start(self):
        self.threads = [threading.Thread(target=self._writer, daemon=True, name='option-arrival-writer'),
                        threading.Thread(target=self._connect_loop, daemon=True, name='option-qmt')]
        for thread in self.threads:
            thread.start()

    def close(self):
        self.stop.set()
        if self.xt:
            for seq in self.subscriptions:
                try:
                    self.xt.unsubscribe_quote(seq)
                except Exception as exc:
                    self.error(f'取消行情订阅：{exc}')
        for thread in self.threads:
            thread.join(timeout=10)

    def _connect_loop(self):
        from xtquant import xtdata
        xtdata.enable_hello = False
        self.xt = xtdata
        port = tomllib.loads((ROOT/'config.toml').read_text(encoding='utf-8-sig'))['qmt']['port']
        while not self.stop.is_set():
            try:
                if not self.client or not self.client.is_connected():
                    with self.lock:
                        self.status.update(connected=False, phase='连接 QMT 行情服务', subscribed=[])
                    with self.qmt_lock:
                        self.client = xtdata.connect(port=port)
                    if not self.client or not self.client.is_connected():
                        raise ConnectionError('QMT 未连接')
                    for seq in self.subscriptions:
                        try:
                            xtdata.unsubscribe_quote(seq)
                        except Exception:
                            pass
                    self.subscriptions = []
                    self.status.update(connected=True, phase='刷新全市场目录和快照')
                    self.refresh_catalog()
                    # Subscribe each known market separately so failed coverage stays visible.
                    for market in MARKETS:
                        if not any(c.endswith('.'+market) for c in self.codes):
                            continue
                        with self.qmt_lock:
                            seq = xtdata.subscribe_whole_quote([market], callback=self.callback)
                        if not isinstance(seq, int) or seq <= 0:
                            self.error(f'{market} 全推订阅失败：{seq}')
                        else:
                            self.subscriptions.append(seq)
                            self.status['subscribed'].append(market)
                    self.status['phase'] = '行情已连接 · 全市场快照记录中'
                # Refresh newly listed contracts once a day. No trade interface is used.
                elif self.status['catalog_at'] and self.status['catalog_at'][:10] != datetime.now(TZ).strftime('%Y-%m-%d'):
                    self.refresh_catalog()
            except Exception as exc:
                self.status.update(connected=False, phase='QMT 断开 · 保留历史，30秒后重连')
                self.client = None
                self.error(exc)
            self.stop.wait(30)

    def refresh_catalog(self):
        codes, futures, sectors = set(), set(), {}
        for sector in SECTORS:
            try:
                with self.qmt_lock:
                    raw = self.xt.get_stock_list_in_sector(sector)
                opts = [c for c in raw if OPTION.fullmatch(c)]
                futs = [c for c in raw if FUTURE.fullmatch(c) and 1 <= int(FUTURE.fullmatch(c)[2][-2:]) <= 12]
                sectors[sector] = dict(options=opts, futures=futs, raw_count=len(raw))
                codes.update(opts)
                futures.update(futs)
            except Exception as exc:
                self.error(f'{sector} 目录：{exc}')
        if not codes:
            raise RuntimeError('QMT 商品期权目录为空，保留已有目录')
        # Refresh representative metadata, preserving already saved detailed records.
        reps = {}
        for c in sorted(codes):
            m = OPTION.fullmatch(c)
            reps.setdefault((m[1], m[5]), c)
        for c in reps.values():
            with self.qmt_lock:
                d = self.xt.get_instrument_detail(c, True)
            if d:
                self.details[c] = d
        self.codes, self.futures, self.sectors = codes, futures, sectors
        self._build_catalog()
        all_codes = sorted(codes|futures)
        for offset in range(0, len(all_codes), 500):
            with self.qmt_lock:
                quotes = self.xt.get_full_tick(all_codes[offset:offset+500])
            self.callback(quotes, initial=True)
        stamp = datetime.now(TZ).isoformat()
        self.status['catalog_at'] = stamp
        with self.lock:
            payload = dict(captured_at=stamp, sectors=sectors, details=self.details.copy(), ticks=self.quotes.copy())
        tmp = self.data/'catalog.tmp'
        tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding='utf-8')
        tmp.replace(self.data/'catalog.json')

    def callback(self, payload, initial=False):
        if self.stop.is_set():
            return
        now = time.time()
        try:
            for code, raw_rows in payload.items():
                if code not in self.codes and code not in self.futures:
                    continue
                for row in ([raw_rows] if isinstance(raw_rows, dict) else raw_rows):
                    if not isinstance(row, dict) or number(row.get('time')) <= 0:
                        continue
                    with self.lock:
                        old = self.quotes.get(code)
                        if not old or number(old.get('time')) > now*1000 or number(row.get('time')) >= number(old.get('time')):
                            self.quotes[code] = row
                    if code not in self.codes:
                        continue
                    # tickvol is a mutable historical field, not exchange trade count.
                    clean = {k:v for k,v in row.items() if k not in ('tickvol', 'timetag')}
                    raw = json.dumps(clean, ensure_ascii=False, sort_keys=True, default=lambda x:x.item())
                    fingerprint = hashlib.sha256(raw.encode()).digest()
                    with self.lock:
                        if self.last_fingerprints.get(code) == fingerprint:
                            continue
                        self.last_fingerprints[code] = fingerprint
                    try:
                        self.pending.put_nowait((code, int(row['time']), now, raw))
                        self.status['received'] += 1
                    except queue.Full:
                        self.status['dropped'] += 1
            if not initial:
                self.status['last_callback'] = datetime.now(TZ).isoformat()
        except Exception as exc:
            self.error(f'行情回调：{exc}')

    def _writer(self):
        db = sqlite3.connect(self.db_path)
        try:
            while not self.stop.is_set() or not self.pending.empty():
                batch = []
                try:
                    batch.append(self.pending.get(timeout=.5))
                except queue.Empty:
                    continue
                while len(batch) < 2000:
                    try:
                        batch.append(self.pending.get_nowait())
                    except queue.Empty:
                        break
                try:
                    db.executemany('INSERT INTO arrivals(code,t,received,raw) VALUES (?,?,?,?)', batch)
                    db.commit()
                    self.status['written'] += len(batch)
                except Exception as exc:
                    db.rollback()
                    self.status['dropped'] += len(batch)
                    self.error(f'本地记录失败：{exc}')
        finally:
            db.close()

    def catalog(self):
        with self.lock:
            stamps = [int(number(t.get('time'))) for c,t in self.quotes.items() if c in self.codes and number(t.get('time')) > 0]
            now_ms = int(time.time()*1000)
            latest = max((t for t in stamps if t <= now_ms), default=0)
            s = dict(self.status)
            s.update(products=len(self.products), contracts=len(self.contracts),
                     quoted=sum(1 for c in self.codes if number(self.quotes.get(c, {}).get('time')) > 0),
                     two_sided=sum(1 for c in self.codes if (q:=normalized(self.quotes.get(c, {})))['bid'][0]>0 and q['ask'][0]>0 and q['bv'][0]>0 and q['av'][0]>0),
                     latest=latest, future_timestamps=sum(t>now_ms for t in stamps),
                     missing=[name for market,name in MARKETS.items() if not any(p['market']==market for p in self.products)],
                     pending=self.pending.qsize())
            return dict(status=s, products=self.products, default_date=datetime.fromtimestamp(latest/1000, TZ).strftime('%Y-%m-%d') if latest else datetime.now(TZ).strftime('%Y-%m-%d'))

    def chain(self, product):
        with self.lock:
            if product not in self.product_map:
                raise ValueError('未知品种')
            p = self.product_map[product]
            items = []
            for c in self.contracts.values():
                if c['product'] == product:
                    items.append({**c, 'quote': normalized(self.quotes.get(c['code'], {}))})
            ranks = []
            for month in p['months']:
                future = p['symbol']+month+'.'+p['market']
                q = normalized(self.quotes.get(future, {}))
                ranks.append(dict(month=month, code=future, oi=q['oi'], volume=q['volume'], quote=q))
            have_futures = any(r['oi']>0 for r in ranks)
            if not have_futures:
                for r in ranks:
                    r['oi'] = sum(i['quote']['oi'] for i in items if i['month']==r['month'])
                    r['volume'] = sum(i['quote']['volume'] for i in items if i['month']==r['month'])
            ranks.sort(key=lambda r:(-r['oi'], -r['volume'],r['month']))
            return dict(product=p, items=items, ranks=ranks,
                        rank_basis='标的期货持仓量，成交量次序' if have_futures else '缺少标的期货持仓：按该月期权持仓合计排序',
                        quote_scope='最新快照（时间逐合约显示），与主图历史日期独立')

    def search(self, query):
        query = query.casefold().strip()
        if not query:
            return []
        return [c for c in self.contracts.values() if query in (c['code']+' '+c['name']).casefold()][:100]

    def history(self, code, date, refresh=False):
        if code not in self.contracts:
            raise ValueError('未知商品期权合约')
        date_bounds(date)
        with self.lock:
            history_lock = self.history_locks.setdefault((code,date), threading.Lock())
        with history_lock:
            return self._history(code, date, refresh)

    def _history(self, code, date, refresh=False):
        start_ms, end_ms = date_bounds(date)
        today = datetime.now(TZ).strftime('%Y-%m-%d')
        if date > today:
            raise ValueError('不能请求未来日期')
        key = (code, date)
        cache_path = self.data / f'{code}_{date}.json'
        messages, rows, source = [], [], ''
        with self.lock:
            cached = self.cache.get(key)
        if cached and not refresh and ('QMT历史' in cached[1] or not (self.client and self.client.is_connected())):
            rows, source = cached
        elif cache_path.exists() and not refresh:
            rows = json.loads(cache_path.read_text(encoding='utf-8'))
            source = 'QMT历史缓存 · 自然日'
        elif self.client and self.client.is_connected():
            stamp = date.replace('-', '')
            try:
                with self.qmt_lock:
                    self.xt.download_history_data(code, 'tick', stamp+'000000', stamp+'235959')
                    frame = self.xt.get_market_data_ex([], [code], period='tick', start_time=stamp+'000000', end_time=stamp+'235959', fill_data=False).get(code)
                    detail = self.xt.get_instrument_detail(code, True)
                if detail:
                    self.details[code] = detail
                if frame is not None and not frame.empty:
                    # Stable sort keeps distinct quotes at the same millisecond.
                    frame = frame.sort_values('time', kind='stable')
                    rows = json.loads(frame.to_json(orient='records'))
                    cache_path.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
                source = 'QMT历史下载 · 自然日含夜盘'
            except Exception as exc:
                messages.append('QMT历史请求失败：'+str(exc))
        if not rows and cache_path.exists():
            rows = json.loads(cache_path.read_text(encoding='utf-8'))
            source = 'QMT历史缓存 · 自然日'
            messages.append('本次未取得新的历史数据，保留已有QMT缓存。')
        if not rows:
            # Existing research captures are explicitly day-only, not a full day claim.
            old = BASE/'data'/f'{code}_{date.replace("-", "")}_tick.pkl'
            if old.exists():
                import pandas as pd
                rows = json.loads(pd.read_pickle(old).sort_values('time', kind='stable').to_json(orient='records'))
                source = '本地研究日盘缓存（不含夜盘）'
                messages.append('当前使用日盘研究缓存；连接QMT后点“重取历史”补齐可用夜盘。')
        rows = [r for r in rows if start_ms <= number(r.get('time')) < end_ms]
        if rows:
            with self.lock:
                self.cache[key] = (rows, source)
                self.cache.move_to_end(key)
                while len(self.cache) > 12:
                    self.cache.popitem(last=False)
        cutoff = max((number(r.get('time')) for r in rows), default=start_ms-1)
        with closing(sqlite3.connect(self.db_path)) as db:
            live = db.execute('SELECT raw FROM arrivals WHERE code=? AND t>=? AND t<? AND t<=received*1000+60000 AND received>=? AND received<? ORDER BY t,id',
                              (code, max(start_ms, cutoff), end_ms, start_ms/1000-60, end_ms/1000+60)).fetchall()
        if live:
            overlap = {json.dumps(normalized(r),sort_keys=True) for r in rows if number(r.get('time'))==cutoff}
            additions = []
            for r in live:
                row = json.loads(r[0])
                if number(row.get('time')) == cutoff and json.dumps(normalized(row),sort_keys=True) in overlap:
                    continue
                additions.append(row)
            rows = rows + additions
            source = (source+' + ' if source else '')+'本地接收记录'
        points, stats = tape(rows)
        if not points:
            messages.append('该合约在所选自然日未返回盘口；这不代表全天没有交易。')
        elif points[-1]['t'] == points[0]['t']:
            messages.append('只返回一个时点，尚不足以还原分时；可重取历史核验。')
        d = self.details.get(code) or {}
        meta = {**self.contracts[code], 'expiry': d.get('ExpireDate',''),
                'unit':number(d.get('OptUnit') or d.get('VolumeMultiple')), 'tick':number(d.get('PriceTick'))}
        with self.lock:
            latest = normalized(self.quotes.get(code, {}))
        return dict(code=code, date=date, meta=meta, points=points, stats=stats,
                    latest=latest, source=source, messages=messages,
                    semantics='L1成交量增事件；箭头按末笔成交价相对此前60秒内有效盘口推断，不是交易所逐笔方向。')
