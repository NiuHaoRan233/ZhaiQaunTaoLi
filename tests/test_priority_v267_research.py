from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from zhaiquant.maker_paper import (
    PRIORITY_POLICY_FIRST_POSITION_V266_R3_CANDIDATE as PARENT,
    PRIORITY_POLICY_FIRST_POSITION_V267_CANDIDATE as CANDIDATE,
    _floor_to_tick,
)
from zhaiquant.types import SHANGHAI
from . import test_priority_v264_research as helpers
from .test_priority_v264_research import _tick, _assessment, _context


class PriorityV267ResearchTests(unittest.TestCase):
    _engine = helpers.PriorityV264ResearchTests._engine

    def test_profile_only_enables_native_priority_grid_normalization(self):
        self.assertEqual(CANDIDATE, replace(PARENT, model_id=CANDIDATE.model_id,
            model_version=CANDIDATE.model_version, parent_model_id=PARENT.model_id,
            normalize_native_priority_price_grid=True))
        self.assertFalse(PARENT.normalize_native_priority_price_grid)

    def test_only_float_grid_noise_is_snapped_and_real_subticks_still_floor(self):
        self.assertEqual(_floor_to_tick(138.09799999999998,.001),138.097)
        self.assertEqual(_floor_to_tick(138.09799999999998,.001,snap_grid_noise=True),138.098)
        self.assertEqual(_floor_to_tick(136.61599999999999,.001,snap_grid_noise=True),136.616)
        for price, expected in ((138.0979,138.097),(138.0979999,138.097),
                                (138.0980001,138.098),(138.098,138.098)):
            self.assertEqual(_floor_to_tick(price,.001,snap_grid_noise=True),expected)
        self.assertEqual(_floor_to_tick(.29999999999999993,.01,snap_grid_noise=True),.30)

    def test_actual_native_sell_quotes_improve_exactly_one_tick(self):
        moment = datetime(2026,9,4,10,30,tzinfo=SHANGHAI)
        prices={}
        for policy in (PARENT,CANDIDATE):
            with tempfile.TemporaryDirectory() as tmp:
                engine,store=self._engine(Path(tmp)/'paper.sqlite3',policy)
                try:
                    engine._start_date(moment.date().isoformat())
                    account,lot=helpers.PriorityV264ResearchTests._open_extra_lot(
                        engine,_tick(1,moment-timedelta(seconds=30),bid=137.44,ask=137.62))
                    tick=_tick(2,moment,bid=137.8,ask=138.099)
                    context=replace(_context(spread=.299),reference_price=138.2,bid_support_bonds=25_000)
                    with patch.object(engine,'_decision_context',return_value=context), \
                         patch.object(engine,'_confirmed_rise_is_recent',return_value=False):
                        engine._refresh_orders(account,tick,replace(_assessment(),reference_price=138.2),persist=True)
                    self.assertIn(lot.db_id,account.sell_orders)
                    prices[policy.model_id]=account.sell_orders[lot.db_id].limit_price
                    self.assertEqual(account.inventory,2_000)
                finally:
                    store.close()
        self.assertEqual(prices[PARENT.model_id],138.097)
        self.assertEqual(prices[CANDIDATE.model_id],138.098)

    def test_raw_feed_bid_noise_does_not_erase_first_position_improvement(self):
        moment=datetime(2026,9,4,10,30,tzinfo=SHANGHAI)
        prices={}
        for policy in (PARENT,CANDIDATE):
            with tempfile.TemporaryDirectory() as tmp:
                engine,store=self._engine(Path(tmp)/'paper.sqlite3',policy)
                try:
                    engine._start_date(moment.date().isoformat())
                    engine.observed_market_trade=True
                    account=next(iter(engine.accounts.values()))
                    tick=_tick(1,moment,bid=136.61499999999998,ask=137.5)
                    context=replace(_context(spread=.885),reference_price=137.5,bid_support_bonds=25_000)
                    with patch.object(engine,'_decision_context',return_value=context), \
                         patch.object(engine,'_confirmed_rise_is_recent',return_value=False):
                        engine._refresh_orders(account,tick,replace(_assessment(),reference_price=137.5),persist=True)
                    self.assertIsNotNone(account.buy_order)
                    prices[policy.model_id]=account.buy_order.limit_price
                    self.assertEqual(account.inventory,1_000)
                finally:
                    store.close()
        self.assertEqual(prices[PARENT.model_id],136.615)
        self.assertEqual(prices[CANDIDATE.model_id],136.616)
