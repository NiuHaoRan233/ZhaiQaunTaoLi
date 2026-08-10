from __future__ import annotations

import json
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ConversionPrice:
    effective_date: date
    price: float


@dataclass(frozen=True)
class QmtConfig:
    port: int = 58611
    bond_code: str = "132026.SH"
    stock_code: str = "600900.SH"
    watch_codes: tuple[str, ...] = ("132024.SH",)
    callback_queue_size: int = 100_000
    stale_after_seconds: float = 3.0


@dataclass(frozen=True)
class StorageConfig:
    database: Path = Path("data/zhaiquant.sqlite3")
    commit_every_rows: int = 100
    commit_every_seconds: float = 1.0
    store_raw_json: bool = True


@dataclass(frozen=True)
class M0Config:
    rolling_observations: int = 1200
    minimum_observations: int = 600
    entry_discount: float = 0.008
    exit_discount: float = 0.001
    maximum_sync_seconds: float = 3.0
    earliest_entry: str = "09:35:00"
    latest_entry: str = "14:30:00"
    force_exit: str = "14:55:00"
    maximum_holding_minutes: int = 60
    cooldown_minutes: int = 10
    trading_enabled: bool = True
    conversion_prices: tuple[ConversionPrice, ...] = field(default_factory=lambda: (
        ConversionPrice(date(2020, 1, 1), 22.20),
        ConversionPrice(date(2026, 2, 12), 21.99),
        ConversionPrice(date(2026, 7, 17), 21.20),
    ))

    def conversion_price_for(self, target: date) -> float:
        eligible = [item for item in self.conversion_prices if item.effective_date <= target]
        if not eligible:
            raise ConfigError(f"No conversion price configured for {target.isoformat()}")
        return eligible[-1].price


@dataclass(frozen=True)
class PaperConfig:
    enabled: bool = True
    notional_cny: float = 100_000.0
    quantity: float = 100.0
    price_tick: float = 0.001
    maker_entry_wait_seconds: int = 60
    maker_exit_wait_seconds: int = 180
    execution_models: tuple[str, ...] = ("E1", "E2", "E3", "E4")
    fill_modes: tuple[str, ...] = ("optimistic", "queue", "conservative")


@dataclass(frozen=True)
class AppConfig:
    path: Path
    qmt: QmtConfig
    storage: StorageConfig
    m0: M0Config
    paper: PaperConfig

    def to_json(self) -> str:
        data = asdict(self)
        data["path"] = str(self.path)
        data["storage"]["database"] = str(self.storage.database)
        for item in data["m0"]["conversion_prices"]:
            item["effective_date"] = item["effective_date"].isoformat()
        return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a TOML table")
    return value


def load_config(path: str | Path = "config.toml") -> AppConfig:
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise ConfigError(
            f"Configuration not found: {config_path}. "
            "Copy config.example.toml to config.toml first."
        )
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    qmt_data = _section(data, "qmt")
    storage_data = _section(data, "storage")
    m0_data = _section(data, "m0")
    paper_data = _section(data, "paper")

    database = Path(storage_data.get("database", "data/zhaiquant.sqlite3"))
    if not database.is_absolute():
        database = (config_path.parent / database).resolve()

    conversion_rows = m0_data.get("conversion_prices", [])
    conversion_prices = []
    for row in conversion_rows:
        try:
            conversion_prices.append(ConversionPrice(
                effective_date=date.fromisoformat(str(row["effective_date"])),
                price=float(row["price"]),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid m0.conversion_prices row: {row!r}") from exc
    if not conversion_prices:
        conversion_prices = list(M0Config().conversion_prices)
    conversion_prices.sort(key=lambda item: item.effective_date)

    qmt = QmtConfig(
        port=int(qmt_data.get("port", 58611)),
        bond_code=str(qmt_data.get("bond_code", "132026.SH")),
        stock_code=str(qmt_data.get("stock_code", "600900.SH")),
        watch_codes=tuple(str(item) for item in qmt_data.get("watch_codes", ["132024.SH"])),
        callback_queue_size=int(qmt_data.get("callback_queue_size", 100_000)),
        stale_after_seconds=float(qmt_data.get("stale_after_seconds", 3.0)),
    )
    storage = StorageConfig(
        database=database,
        commit_every_rows=int(storage_data.get("commit_every_rows", 100)),
        commit_every_seconds=float(storage_data.get("commit_every_seconds", 1.0)),
        store_raw_json=bool(storage_data.get("store_raw_json", True)),
    )
    m0 = M0Config(
        rolling_observations=int(m0_data.get("rolling_observations", 1200)),
        minimum_observations=int(m0_data.get("minimum_observations", 600)),
        entry_discount=float(m0_data.get("entry_discount", 0.008)),
        exit_discount=float(m0_data.get("exit_discount", 0.001)),
        maximum_sync_seconds=float(m0_data.get("maximum_sync_seconds", 3.0)),
        earliest_entry=str(m0_data.get("earliest_entry", "09:35:00")),
        latest_entry=str(m0_data.get("latest_entry", "14:30:00")),
        force_exit=str(m0_data.get("force_exit", "14:55:00")),
        maximum_holding_minutes=int(m0_data.get("maximum_holding_minutes", 60)),
        cooldown_minutes=int(m0_data.get("cooldown_minutes", 10)),
        trading_enabled=bool(m0_data.get("trading_enabled", True)),
        conversion_prices=tuple(conversion_prices),
    )
    paper = PaperConfig(
        enabled=bool(paper_data.get("enabled", True)),
        notional_cny=float(paper_data.get("notional_cny", 100_000.0)),
        quantity=float(paper_data.get("quantity", 100.0)),
        price_tick=float(paper_data.get("price_tick", 0.001)),
        maker_entry_wait_seconds=int(paper_data.get("maker_entry_wait_seconds", 60)),
        maker_exit_wait_seconds=int(paper_data.get("maker_exit_wait_seconds", 180)),
        execution_models=tuple(str(item).upper() for item in paper_data.get(
            "execution_models", ["E1", "E2", "E3", "E4"]
        )),
        fill_modes=tuple(str(item).lower() for item in paper_data.get(
            "fill_modes", ["optimistic", "queue", "conservative"]
        )),
    )
    _validate(qmt, storage, m0, paper)
    return AppConfig(config_path, qmt, storage, m0, paper)


def _validate(qmt: QmtConfig, storage: StorageConfig, m0: M0Config, paper: PaperConfig) -> None:
    if qmt.port <= 0:
        raise ConfigError("qmt.port must be positive")
    if not qmt.bond_code or not qmt.stock_code or qmt.bond_code == qmt.stock_code:
        raise ConfigError("qmt bond_code and stock_code must be distinct")
    if any(not code for code in qmt.watch_codes):
        raise ConfigError("qmt.watch_codes cannot contain blank codes")
    if m0.minimum_observations > m0.rolling_observations:
        raise ConfigError("m0.minimum_observations cannot exceed rolling_observations")
    if not 0 < m0.entry_discount < 0.2 or not 0 <= m0.exit_discount < m0.entry_discount:
        raise ConfigError("m0 entry/exit discounts are invalid")
    if paper.quantity <= 0 or paper.notional_cny <= 0 or paper.price_tick <= 0:
        raise ConfigError("paper quantity, notional and price_tick must be positive")
    if not set(paper.execution_models).issubset({"E1", "E2", "E3", "E4"}):
        raise ConfigError("paper.execution_models may only contain E1/E2/E3/E4")
    if not set(paper.fill_modes).issubset({"optimistic", "queue", "conservative"}):
        raise ConfigError("paper.fill_modes contains an unknown mode")
    storage.database.parent.mkdir(parents=True, exist_ok=True)
