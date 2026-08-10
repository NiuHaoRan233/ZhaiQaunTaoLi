from __future__ import annotations

from datetime import datetime
from pathlib import Path

from zhaiquant.config import (
    AppConfig,
    ConversionPrice,
    M0Config,
    PaperConfig,
    QmtConfig,
    StorageConfig,
)
from zhaiquant.types import SHANGHAI, Tick


def test_config(database: Path, *, models=("E1",), fill_modes=("queue",)) -> AppConfig:
    return AppConfig(
        path=database.parent / "config.toml",
        qmt=QmtConfig(),
        storage=StorageConfig(database=database, commit_every_rows=1),
        m0=M0Config(
            rolling_observations=3,
            minimum_observations=2,
            entry_discount=0.008,
            exit_discount=0.001,
            maximum_sync_seconds=3,
            earliest_entry="09:35:00",
            latest_entry="14:30:00",
            force_exit="14:55:00",
            maximum_holding_minutes=60,
            cooldown_minutes=10,
            trading_enabled=True,
            conversion_prices=(ConversionPrice(datetime(2020, 1, 1).date(), 21.20),),
        ),
        paper=PaperConfig(
            enabled=True,
            notional_cny=100_000,
            quantity=10,
            price_tick=0.01,
            maker_entry_wait_seconds=60,
            maker_exit_wait_seconds=180,
            execution_models=models,
            fill_modes=fill_modes,
        ),
    )


def make_tick(
    code: str,
    moment: datetime,
    *,
    last: float,
    bid: float,
    ask: float,
    volume: float = 1000,
    amount: float = 100_000,
    transactions: int = 100,
    level_volume: float = 100,
) -> Tick:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=SHANGHAI)
    market_ms = int(moment.timestamp() * 1000)
    payload = {
        "time": market_ms,
        "lastPrice": last,
        "open": last,
        "high": last,
        "low": last,
        "lastClose": last,
        "amount": amount,
        "volume": volume,
        "pvolume": volume,
        "tickvol": 0,
        "stockStatus": 3,
        "openInt": 0,
        "lastSettlementPrice": 0,
        "settlementPrice": 0,
        "transactionNum": transactions,
        "pe": 0,
        "askPrice": [ask + index * 0.01 for index in range(5)],
        "bidPrice": [bid - index * 0.01 for index in range(5)],
        "askVol": [level_volume] * 5,
        "bidVol": [level_volume] * 5,
    }
    return Tick.from_qmt(code, payload, market_ms * 1_000_000)
