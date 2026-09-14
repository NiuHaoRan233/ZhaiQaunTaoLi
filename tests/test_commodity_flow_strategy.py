from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from zhaiquant.commodity_flow_strategy import FAMILY, VARIANTS, FlowAccount
from zhaiquant.commodity_flow_paper import (PaperStore, SHANGHAI, audit_paper, clean_tick,
    local_date, normalize, read_report, session_at, set_entry_paused, validate_config, writer_lock)
from zhaiquant import commodity_selective_research as frozen
from zhaiquant.option_top_cycle_research import Event

START = int(datetime(2026,9,14,9,0,tzinfo=SHANGHAI).timestamp()*1000)
CODE = "c2611-C-2300.DF"


def event(sec, bid=10000, ask=11000, quantity=0, last=10000, side="sell", **kw):
    return replace(Event(START+int(sec*1000), START+int((sec-1)*1000), 0, bid, ask,
                  2, 2, ((bid,2),), ((ask,2),), last, quantity, 0, side, True, side), **kw)


def config():
    return dict(schema=1,family=FAMILY,variants=list(VARIANTS),fee_cents=170,delay_ms=0,capacity=1,
                instruments=[dict(code=CODE,unit=1,price_tick=1,expiry="20261110",initial_cents=1000000)])


def raw(sec, volume=0, amount=0, last=100, bid=100, ask=110):
    return dict(time=START+int(sec*1000),volume=volume,amount=amount,lastPrice=last,
                bidPrice=[bid],askPrice=[ask],bidVol=[2],askVol=[2])


def core_result(events, variant):
    a = FlowAccount(CODE,variant,1000000,100)
    rows = {k:[] for k in ("orders","fills","cycles","curve")}
    for e in events:
        d = a.step(e,local_date(e.ts))
        for key in ("orders","fills","cycles"):
            rows[key].extend(d[key])
        rows["curve"].append(d["curve"])
    return a, rows


def economics(items):
    return [{k:v for k,v in x.items() if k!="model_id"} for x in items]


class FlowKernelTests(unittest.TestCase):
    def test_frozen_path_parity_and_cost_protection(self):
        events = [event(0,quantity=1),event(1,quantity=1,last=11000,side="buy"),
                  event(2,quantity=1),event(3,bid=9000,ask=10200),
                  event(4,bid=9000,ask=10200,quantity=1,last=10200,side="buy"),
                  event(5,quantity=1,last=11000,side="buy"),
                  event(6,quantity=1),event(61),event(121),event(181),event(241),
                  event(307,bid=8000,ask=9000),
                  event(308,bid=8000,ask=9000,quantity=1,last=9000,side="buy")]
        for variant in VARIANTS:
            old = frozen.run([(events,[],dict(initial_cents=1000000,tick_cents=100,unit=1,date="20260914"))],code=CODE,variant=variant)
            account, new = core_result(events,variant)
            for key in ("orders","fills"):
                self.assertEqual(economics(new[key]),economics(old[key]))
            self.assertEqual(new["cycles"],old["cycles"])
            self.assertEqual(new["curve"],old["curve"])
            self.assertEqual(account.daily(),old["daily"])
            self.assertEqual(account.summary()["pnl_cny"],old["summary"]["pnl_cny"])

    def test_restart_and_prefix_preserve_pending_order_and_patient_floor(self):
        events=[event(0,quantity=1),event(1,quantity=1,last=11000,side="buy"),event(2,quantity=1),
                event(3,bid=9000,ask=10200),event(4,quantity=1,last=11000,side="buy")]
        for variant in VARIANTS:
            whole, rows=core_result(events,variant)
            first, prefix=core_result(events[:3],variant)
            restored=FlowAccount(CODE,variant,1000000,100,state=json.loads(json.dumps(first.snapshot())))
            for e in events[3:]:restored.step(e,local_date(e.ts))
            self.assertEqual(restored.snapshot(),whole.snapshot())
            self.assertEqual(prefix['fills'],[f for f in rows['fills'] if f['ts']<=events[2].ts])

    def test_actual_arrival_cannot_claim_predecision_interval(self):
        a=FlowAccount(CODE,"flow_entry",1000000,100,"paper_arrival")
        a.step(event(0,quantity=1),"20260914",START+100)
        a.step(event(1,quantity=1,last=11000,side="buy"),"20260914",START+1100)
        self.assertEqual(a.step(event(2,quantity=1),"20260914",START+2100)['fills'],[])
        self.assertEqual(len(a.step(event(3,quantity=1),"20260914",START+3100)['fills']),1)

    def test_pause_keeps_inventory_and_exit_eligibility(self):
        a,_=core_result([event(0,quantity=1),event(1,quantity=1,last=11000,side="buy"),event(2,quantity=1)],"flow_entry")
        cash=a.s['cash'];a.pause(START+2500,"disconnected")
        self.assertEqual((a.s['cash'],a.s['inventory']),(cash,1))
        a.step(event(3),"20260914",allow_entry=False)
        d=a.step(event(4,quantity=1,last=11000,side="buy"),"20260914",allow_entry=False)
        self.assertEqual(d['fills'][0]['side'],'sell')
        self.assertIsNone(a.s['order'])

    def test_insufficient_cash_does_not_borrow_or_inject(self):
        a=FlowAccount(CODE,"flow_entry",100,100)
        a.step(event(0,quantity=1),"20260914")
        a.step(event(1,quantity=1,last=11000,side="buy"),"20260914")
        self.assertIsNone(a.s['order']);self.assertEqual(a.s['cash'],100)
        self.assertEqual(a.s['latest_reason'],'insufficient_cash')

    def test_invalid_identity_and_time_fail_closed(self):
        with self.assertRaises(ValueError):FlowAccount(CODE,'winner',100,100)
        a=FlowAccount(CODE,'flow_entry',1000000,100)
        with self.assertRaises(ValueError):a.step(event(0),'20260914',START-1)
        a.step(event(0),'20260914')
        with self.assertRaises(ValueError):a.step(event(0),'20260914')

    def test_stale_tail_and_daily_cash_identity(self):
        a,_=core_result([event(0,quantity=1),event(1,quantity=1,last=11000,side="buy"),event(2,quantity=1)],'flow_patient')
        a.pause(START+3000,'shutdown')
        s=a.summary(START+65000)
        self.assertTrue(s['stale_tail'])
        self.assertAlmostEqual(s['pnl_cny'],s['completed_cycle_net_cny']+s['open_cycle_contribution_cny'])
        self.assertAlmostEqual(sum(d['pnl_cny'] for d in a.daily()),s['pnl_cny'])


class NormalizeTests(unittest.TestCase):
    def test_last_contract_only_and_previous_book_side(self):
        spec=config()['instruments'][0]
        e=normalize(raw(1,20,2200,110),raw(0),spec)
        self.assertEqual((e.quantity,e.single,e.strict_side),(1,True,'buy'))
        middle=normalize(raw(1,1,105,105),raw(0),spec)
        self.assertEqual(middle.strict_side,'unknown')

    def test_gap_reset_and_session_do_not_infer_trades(self):
        spec=config()['instruments'][0]
        for newer,older in [(raw(61,1,110,110),raw(0)),(raw(1,0,0,110),raw(0,1,100)),
                            (raw(5400,2,210,110),raw(5399,1,100))]:
            self.assertFalse(normalize(newer,older,spec).single)

    def test_bad_grid_nonfinite_and_negative_quantity_rejected(self):
        spec=config()['instruments'][0]
        for tick in [raw(0,bid=100.1),raw(0,amount=float('nan')),raw(0,last=float('inf')),
                     dict(raw(0),bidVol=[-1]),dict(raw(0),askPrice=[])]:
            with self.assertRaises((ValueError,IndexError)):clean_tick(tick,spec)

    def test_weekends_and_session_boundaries(self):
        for sec in (4500,5399,9000,16199,21600):self.assertIsNone(session_at(START+sec*1000))
        for sec in (0,4499,5400,8999,16200,21599):self.assertIsNotNone(session_at(START+sec*1000))
        self.assertIsNone(session_at(START-86400000))

    def test_contract_configuration_strict(self):
        for field,value in [('fee_cents',0),('delay_ms',500),('capacity',2)]:
            c=config();c[field]=value
            with self.assertRaises(ValueError):validate_config(c)
        c=config();c['instruments'][0]['code']='../../evil.SF'
        with self.assertRaises(ValueError):validate_config(c)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'paper.db'
        self.cfg=config();self.store=PaperStore(self.path,self.cfg)

    def tearDown(self):
        self.store.close();self.tmp.cleanup()

    def ingest(self,tick,lag=0):
        return self.store.ingest(CODE,tick,tick['time']+lag)

    def warm(self):
        self.ingest(raw(0));self.ingest(raw(1,1,110,110));self.ingest(raw(2,2,210,100))

    def test_duplicate_and_out_of_order_do_not_double_fill(self):
        self.warm();self.ingest(raw(3,3,310,100))
        a=self.store.accounts[CODE,'flow_entry'];snapshot=a.snapshot()
        self.assertEqual(self.ingest(raw(3,3,310,100)),'duplicate_tick')
        self.assertEqual(a.snapshot(),snapshot)
        self.assertEqual(self.ingest(raw(2,2,210,100),lag=2000),'out_of_order_tick')
        self.assertEqual(a.s['fill_count'],1);self.assertEqual(a.s['inventory'],1)

    def test_stale_missing_future_and_wrong_day_never_fill(self):
        self.warm()
        self.assertEqual(self.ingest(raw(3,3,310,100),lag=5001),'stale_tick')
        self.assertIsNone(self.store.accounts[CODE,'flow_entry'].s['order'])
        self.assertEqual(self.store.ingest(CODE,{},START+4000),'malformed_tick')
        self.assertEqual(self.ingest(raw(5),lag=-1001),'future_tick')
        self.assertEqual(self.ingest(raw(6),lag=86400000),'wrong_date')
        self.assertEqual(self.store.accounts[CODE,'flow_entry'].s['fill_count'],0)

    def test_disconnect_and_restart_preserve_funds_but_clear_evidence(self):
        self.warm();self.ingest(raw(3,3,310,100))
        state=self.store.accounts[CODE,'flow_patient'].snapshot()
        self.store.close();self.store=PaperStore(self.path,self.cfg)
        self.assertEqual(self.store.accounts[CODE,'flow_patient'].snapshot(),state)
        self.store.boundary(START+3500,'startup')
        a=self.store.accounts[CODE,'flow_patient']
        self.assertEqual((a.s['cash'],a.s['inventory']),(state['cash'],1))
        self.assertEqual(a.s['flow'],[]);self.assertIsNone(a.s['order'])
        self.ingest(raw(4,4,420,110))
        self.assertEqual(a.s['fill_count'],1)

    def test_transaction_rollback_restores_memory_and_retry_once(self):
        self.warm()
        before=self.store.accounts[CODE,'flow_patient'].snapshot()
        self.store.db.executescript("CREATE TRIGGER fail_fill BEFORE INSERT ON ledger WHEN NEW.kind='fills' BEGIN SELECT RAISE(ABORT,'test crash'); END;")
        with self.assertRaises(sqlite3.DatabaseError):self.ingest(raw(3,3,310,100))
        self.assertEqual(self.store.accounts[CODE,'flow_patient'].snapshot(),before)
        self.store.db.execute('DROP TRIGGER fail_fill');self.store.db.commit()
        self.ingest(raw(3,3,310,100))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM ledger WHERE kind='fills'").fetchone()[0],2)

    def test_paused_entry_is_durable_and_does_not_cancel_owned_exit_right(self):
        self.warm();self.ingest(raw(3,3,310,100))
        set_entry_paused(self.path,True)
        self.ingest(raw(4,4,420,110))
        self.ingest(raw(5,5,530,110))
        for a in self.store.accounts.values():
            self.assertEqual(a.s['inventory'],0);self.assertIsNone(a.s['order'])
        self.assertTrue(read_report(self.path,START+5000)['entries_paused'])
        set_entry_paused(self.path,False);self.ingest(raw(6,6,630,100))
        self.assertEqual(self.store.accounts[CODE,'flow_entry'].s['flow'],[])

    def test_expiry_day_no_new_buy_and_expired_inventory_not_erased(self):
        # Expiry guard acts on the stored specification, never on future returns.
        self.warm();self.ingest(raw(3,3,310,100))
        self.store.specs[CODE]['expiry']='20260914'
        self.ingest(raw(4,4,420,110))
        for a in self.store.accounts.values():self.assertIsNone(a.s['order'])
        self.store.specs[CODE]['expiry']='20260915'
        self.ingest(raw(5,5,520,100));self.ingest(raw(6,6,630,110));self.ingest(raw(7,7,730,100))
        held=self.store.accounts[CODE,'flow_entry'].s['inventory']
        self.assertEqual(held,1)
        tick=raw(2*86400,8,840,110)
        self.assertEqual(self.ingest(tick),'expired_unsettled')
        self.assertEqual(self.store.accounts[CODE,'flow_entry'].s['inventory'],held)

    def test_cumulative_reset_cancels_old_intent_and_warms_again(self):
        self.warm();self.assertIsNotNone(self.store.accounts[CODE,'flow_entry'].s['order'])
        self.ingest(raw(3,0,0,100))
        a=self.store.accounts[CODE,'flow_entry']
        self.assertIsNone(a.s['order']);self.assertEqual(a.s['flow'],[])
        self.assertEqual(a.s['fill_count'],0)

    def test_full_arrival_journal_rebuild_including_control_and_restart(self):
        self.store.boundary(START,'startup')
        self.warm();self.ingest(raw(3,3,310,100))
        set_entry_paused(self.path,True)
        self.ingest(raw(4,4,420,110));self.ingest(raw(5,5,530,110))
        self.store.boundary(START+5500,'disconnected')
        self.ingest(raw(6,6,630,100))
        self.ingest(raw(6,6,630,100))
        self.store.boundary(START+6500,'shutdown')
        result=audit_paper(self.path)
        self.assertEqual(result['status'],'passed');self.assertEqual(result['fill_sides'],4)

    def test_arrival_audit_detects_ledger_tampering(self):
        self.warm();self.ingest(raw(3,3,310,100))
        self.store.db.execute("UPDATE ledger SET payload='{}' WHERE kind='fills'")
        self.store.db.commit()
        with self.assertRaises(ValueError):audit_paper(self.path)

    def test_config_hash_prevents_silent_account_reuse(self):
        changed=deepcopy(self.cfg);changed['instruments'][0]['initial_cents']+=100
        with self.assertRaisesRegex(ValueError,'Frozen paper contract'):PaperStore(self.path,changed)

    def test_reader_does_not_create_missing_database(self):
        p=self.path.with_name('missing.db')
        with self.assertRaises(sqlite3.OperationalError):read_report(p,START)
        self.assertFalse(p.exists())

    def test_os_single_writer_lock(self):
        with writer_lock(self.path):
            with self.assertRaises(RuntimeError):
                with writer_lock(self.path):pass
        with writer_lock(self.path):pass


class QmtLifecycleTests(unittest.TestCase):
    def test_vendor_plain_exception_is_a_recoverable_transport_error(self):
        from zhaiquant.commodity_flow_cli import qmt_call
        with self.assertRaisesRegex(ConnectionError,'vendor disconnect'):
            qmt_call(Mock(side_effect=Exception('vendor disconnect')))

    def test_terms_mismatch_and_missing_terms_fail_closed(self):
        from zhaiquant.commodity_flow_cli import check_terms
        xt=Mock()
        xt.get_instrument_detail.return_value=dict(OptUnit=1,PriceTick=1,ExpireDate='20261110')
        self.assertEqual(check_terms(xt,config()),[])
        xt.get_instrument_detail.return_value=dict(OptUnit=10,PriceTick=1,ExpireDate='20261110')
        self.assertEqual(len(check_terms(xt,config())),1)
        xt.get_instrument_detail.return_value={}
        self.assertEqual(len(check_terms(xt,config())),1)

    def test_once_and_report_close_connections_and_replay_cleanly(self):
        from zhaiquant import commodity_flow_cli as cli
        with tempfile.TemporaryDirectory() as tmp:
            args=SimpleNamespace(db=Path(tmp)/'paper.db',output=Path(tmp)/'report',once=True,port=58611)
            xt,connection=Mock(),Mock()
            connection.is_connected.return_value=True
            xt.subscribe_quote.return_value=7;xt.get_full_tick.return_value={CODE:raw(0)}
            with patch.object(cli,'qmt_connect',return_value=(xt,connection)),patch.object(cli,'check_terms',return_value=[]),patch.object(cli.time,'time_ns',return_value=START*1000000),patch('builtins.print'):
                cli.run_paper(args,config())
            xt.unsubscribe_quote.assert_called_once_with(7)
            self.assertTrue((args.output/'纸面策略日报.html').exists())
            self.assertEqual(audit_paper(args.db)['status'],'passed')
            self.assertEqual(read_report(args.db,START)['latest_input'][2],'shutdown')

    def test_disconnect_retries_then_accepts_only_fresh_new_input(self):
        from zhaiquant import commodity_flow_cli as cli
        with tempfile.TemporaryDirectory() as tmp:
            args=SimpleNamespace(db=Path(tmp)/'paper.db',output=Path(tmp)/'report',once=False,port=58611)
            xt,connection=Mock(),Mock();connection.is_connected.return_value=True
            xt.subscribe_quote.return_value=7;xt.get_full_tick.return_value={CODE:raw(0)}
            def sleep(seconds):
                if seconds==0.5 and xt.get_full_tick.called:raise KeyboardInterrupt()
            with patch.object(cli,'qmt_connect',side_effect=[ConnectionError('offline'),(xt,connection)]) as connect,patch.object(cli,'check_terms',return_value=[]),patch.object(cli.time,'time_ns',return_value=START*1000000),patch.object(cli.time,'sleep',side_effect=sleep),patch('builtins.print'):
                with self.assertRaises(KeyboardInterrupt):cli.run_paper(args,config())
                self.assertEqual(connect.call_count,2)
            result=audit_paper(args.db)
            self.assertEqual(result['fill_sides'],0)
            self.assertEqual(result['replayed_account_frames'],2)

    def test_nontrading_window_sleeps_without_qmt_poll(self):
        from zhaiquant import commodity_flow_cli as cli
        with tempfile.TemporaryDirectory() as tmp:
            args=SimpleNamespace(db=Path(tmp)/'paper.db',output=Path(tmp)/'report',once=False,port=58611)
            sunday=START-86400000
            with patch.object(cli,'qmt_connect') as connect,patch.object(cli.time,'time_ns',return_value=sunday*1000000),patch.object(cli.time,'sleep',side_effect=KeyboardInterrupt()),patch('builtins.print'):
                with self.assertRaises(KeyboardInterrupt):cli.run_paper(args,config())
                connect.assert_not_called()
            self.assertEqual(audit_paper(args.db)['fill_sides'],0)


if __name__=='__main__':unittest.main()
