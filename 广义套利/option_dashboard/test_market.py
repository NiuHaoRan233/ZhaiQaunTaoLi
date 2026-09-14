import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock
from urllib.request import urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer

from market import Market, date_bounds, normalized, tape
from server import make_handler


def tick(t=1000, volume=10, last=100, bid=99, ask=101, bv=4, av=6):
    return dict(time=t, volume=volume, lastPrice=last, amount=volume*100,
                bidPrice=[bid], askPrice=[ask], bidVol=[bv], askVol=[av])


class TapeTests(unittest.TestCase):
    def test_uses_prior_book_even_when_current_book_moved(self):
        p,s=tape([tick(), tick(2000,11,101,bid=103,ask=105)])
        self.assertEqual(p[-1]['event']['side'],'B')
        self.assertEqual(s['B'],1)

    def test_sell_mid_and_all_events_retained(self):
        p,s=tape([tick(),tick(2000,15,99),tick(3000,18,100),tick(4000,20,101)])
        self.assertEqual([x['event']['side'] for x in p if x['event']],['S','N','B'])
        self.assertEqual(s['volume'],10)
        self.assertEqual(len(p),4)

    def test_first_frame_and_volume_reset_never_invent_trades(self):
        p,s=tape([tick(volume=500),tick(2000,2),tick(3000,3,last=101)])
        self.assertIsNone(p[0]['event'])
        self.assertTrue(p[1]['reset'])
        self.assertIsNone(p[1]['event'])
        self.assertEqual(s['volume'],1)

    def test_stale_same_millisecond_crossed_and_missing_are_neutral(self):
        for first,second in [(tick(),tick(62000,11,101)),(tick(),tick(1000,11,101)),
                             (tick(bv=0),tick(2000,11,101)),(tick(bid=101,ask=101),tick(2000,11,101))]:
            with self.subTest(first=first,second=second):
                p,_=tape([first,second]);self.assertEqual(p[-1]['event']['side'],'N')

    def test_quantity_only_quotes_and_prefix_causality(self):
        rows=[tick(),tick(2000,10,bv=20),tick(3000,11,last=101),tick(4000,12,last=99)]
        full,_=tape(rows)
        self.assertIsNone(full[1]['event'])
        for n in range(1,len(rows)+1):
            self.assertEqual(tape(rows[:n])[0],full[:n])

    def test_nonfinite_values_cleaned(self):
        p=normalized(dict(time=1,lastPrice=float('nan'),bidPrice=[float('inf')],askVol=None))
        self.assertEqual(p['last'],0)
        self.assertEqual(p['bid'],[0]*5)
        json.dumps(p,allow_nan=False)

    def test_natural_day_is_shanghai_midnight(self):
        a,b=date_bounds('2026-09-11')
        self.assertEqual(b-a,86400000)
        self.assertEqual(a,1789056000000)

    def test_evening_volume_baseline_can_exceed_day_volume(self):
        a,_=date_bounds('2026-09-11')
        p,s=tape([tick(a+15*3600000,100),tick(a+21*3600000,200,last=101),
                  tick(a+21*3600000+1000,201,last=101)])
        self.assertIsNone(p[1]['event'])
        self.assertEqual(s['volume'],1)

    def test_after_midnight_same_night_session_is_continuous(self):
        a,_=date_bounds('2026-09-11')
        p,s=tape([tick(a-1000,100),tick(a,101,last=101)])
        self.assertEqual(s['B'],1)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.m=Market(Path(self.tmp.name))
        self.m.codes={'ru2610P18000.SF'};self.m.futures={'ru2610.SF'}
        self.m._build_catalog()

    def tearDown(self):
        self.tmp.cleanup()

    def test_duplicate_tickvol_ignored_but_depth_changes_retained(self):
        row=tick()
        self.m.callback({'ru2610P18000.SF':row})
        self.m.callback({'ru2610P18000.SF':{**row,'tickvol':999}})
        self.m.callback({'ru2610P18000.SF':{**row,'bidVol':[8]}})
        self.assertEqual(self.m.pending.qsize(),2)

    def test_local_recording_recovers_after_restart_and_filters_date(self):
        a,b=date_bounds('2026-09-11')
        rows=[tick(a+1000),tick(a+2000,11,101),tick(b+1000)]
        with closing(sqlite3.connect(self.m.db_path)) as db:
            db.executemany('INSERT INTO arrivals(code,t,received,raw) VALUES (?,?,?,?)', [('ru2610P18000.SF',r['time'],r['time']/1000,json.dumps(r)) for r in rows])
            db.commit()
        # Use a cached full-day file to avoid existing research fallback.
        path=Path(self.tmp.name)/'ru2610P18000.SF_2026-09-11.json'
        path.write_text(json.dumps([rows[0]]),encoding='utf-8')
        d=self.m.history('ru2610P18000.SF','2026-09-11')
        self.assertEqual(len(d['points']),2)
        self.assertEqual(d['stats']['B'],1)

    def test_main_month_uses_future_open_interest(self):
        self.m.codes.add('ru2611P18000.SF');self.m._build_catalog()
        self.m.quotes['ru2610.SF']={**tick(), 'openInt':100}
        self.m.quotes['ru2611.SF']={**tick(), 'openInt':200}
        chain=self.m.chain('SF:ru')
        self.assertEqual(chain['ranks'][0]['month'],'2611')
        self.assertIn('标的期货',chain['rank_basis'])

    def test_latest_date_does_not_follow_future_qmt_timestamps(self):
        self.m.quotes={'ru2610P18000.SF':tick(date_bounds('2099-01-01')[0])}
        d=self.m.catalog()
        self.assertEqual(d['status']['future_timestamps'],1)
        self.assertNotEqual(d['default_date'],'2099-01-01')

    def test_next_day_initial_snapshot_not_inserted_into_yesterdays_tape(self):
        a,b=date_bounds('2026-09-11')
        old=tick(a+1000)
        with closing(sqlite3.connect(self.m.db_path)) as db:
            db.execute('INSERT INTO arrivals(code,t,received,raw) VALUES (?,?,?,?)',
                       ('ru2610P18000.SF',old['time'],b/1000+3600,json.dumps(old)))
            db.commit()
        d=self.m.history('ru2610P18000.SF','2026-09-11')
        self.assertEqual(d['points'],[])

    def test_distinct_same_millisecond_depth_survives_history_overlap(self):
        a,_=date_bounds('2026-09-11');one=tick(a+1000);two=tick(a+1000,bv=9)
        (Path(self.tmp.name)/'ru2610P18000.SF_2026-09-11.json').write_text(json.dumps([one]),encoding='utf-8')
        with closing(sqlite3.connect(self.m.db_path)) as db:
            for row in (one,two):
                db.execute('INSERT INTO arrivals(code,t,received,raw) VALUES (?,?,?,?)',
                           ('ru2610P18000.SF',row['time'],row['time']/1000,json.dumps(row)))
            db.commit()
        d=self.m.history('ru2610P18000.SF','2026-09-11')
        self.assertEqual(len(d['points']),2)
        self.assertEqual(d['points'][-1]['bv'][0],9)

    def test_failed_refresh_preserves_valid_history_cache(self):
        a,_=date_bounds('2026-09-11')
        (Path(self.tmp.name)/'ru2610P18000.SF_2026-09-11.json').write_text(json.dumps([tick(a+1000)]),encoding='utf-8')
        self.m.client=Mock();self.m.client.is_connected.return_value=True
        self.m.xt=Mock();self.m.xt.download_history_data.side_effect=ConnectionError('test disconnect')
        d=self.m.history('ru2610P18000.SF','2026-09-11',refresh=True)
        self.assertEqual(len(d['points']),1)
        self.assertTrue(any('保留已有' in s for s in d['messages']))

    def test_http_contract_and_reject_path_injection(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(self.m))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            with urlopen(base+'/api/catalog') as r:
                self.assertEqual(json.load(r)['status']['contracts'],1)
            with self.assertRaises(HTTPError) as exc:
                urlopen(base+'/api/history?code=../../config.toml&date=2026-09-11')
            self.assertEqual(exc.exception.code,400)
            with self.assertRaises(HTTPError) as exc:
                urlopen(base+'/../config.toml')
            self.assertEqual(exc.exception.code,404)
        finally:
            server.shutdown();server.server_close();thread.join()


if __name__=='__main__':
    unittest.main()
