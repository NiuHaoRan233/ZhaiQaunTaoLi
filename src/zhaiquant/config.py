from __future__ import annotations

import json
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    pass


DEFAULT_MAKER_UNDERLYING_STOCK_CODES = {
    "132026.SH": "600900.SH",
    "132024.SH": "600362.SH",
}


@dataclass(frozen=True)
class ConversionPrice:
    effective_date: date
    price: float


@dataclass(frozen=True)
class QmtConfig:
    port: int = 58611
    bond_code: str = "132026.SH"
    stock_code: str = "600900.SH"
    watch_codes: tuple[str, ...] = ("132024.SH", "600362.SH")
    instrument_names: dict[str, str] = field(default_factory=lambda: {
        "132026.SH": "G三峡EB2",
        "132024.SH": "26江铜EB",
        "600362.SH": "江西铜业",
    })
    callback_queue_size: int = 100_000
    stale_after_seconds: float = 3.0
    status_interval_seconds: float = 15.0


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
    quantity_bonds: float = 100.0
    price_tick: float = 0.001
    maker_entry_wait_seconds: int = 60
    maker_exit_wait_seconds: int = 180
    standing_reprice_ticks: int = 5
    standing_reprice_seconds: float = 10.0
    execution_models: tuple[str, ...] = ("E1", "E2", "E3", "E4")
    fill_modes: tuple[str, ...] = ("optimistic", "queue", "conservative")


@dataclass(frozen=True)
class MakerPaperConfig:
    """Paper-only inventory account for the maker V0.1 model."""

    enabled: bool = False
    bond_codes: tuple[str, ...] = ()
    underlying_stock_codes: dict[str, str] = field(default_factory=lambda: dict(
        DEFAULT_MAKER_UNDERLYING_STOCK_CODES
    ))
    initial_inventory_bonds: float = 1_000.0
    additional_buying_capacity_bonds: float = 1_000.0
    maximum_inventory_bonds: float = 2_000.0
    initial_cash_cny: float = 136_800.0
    order_quantity_bonds: float = 1_000.0
    price_tick: float = 0.001
    fill_modes: tuple[str, ...] = ("priority", "queue")
    realtime_comparison_model_ids: tuple[str, ...] = ()
    earliest_entry: str = "09:20:00.000"
    latest_entry: str = "15:29:59.999"
    opening_caution_effective_date: str = "2026-08-21"
    opening_caution_end: str = "09:30:00.000"
    opening_caution_minimum_edge: float = 1.00
    super_windfall_enabled: bool = False
    super_windfall_quantity_bonds: float = 10.0
    super_windfall_credit_cny: float = 2_000.0


@dataclass(frozen=True)
class AppConfig:
    path: Path
    qmt: QmtConfig
    storage: StorageConfig
    m0: M0Config
    paper: PaperConfig
    maker_paper: MakerPaperConfig = field(default_factory=MakerPaperConfig)

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
    maker_paper_data = _section(data, "maker_paper")

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

    instrument_names_data = qmt_data.get("instrument_names", {
        "132026.SH": "G三峡EB2",
        "132024.SH": "26江铜EB",
        "600362.SH": "江西铜业",
    })
    if not isinstance(instrument_names_data, dict):
        raise ConfigError("qmt.instrument_names must be a TOML table")

    qmt = QmtConfig(
        port=int(qmt_data.get("port", 58611)),
        bond_code=str(qmt_data.get("bond_code", "132026.SH")),
        stock_code=str(qmt_data.get("stock_code", "600900.SH")),
        watch_codes=tuple(str(item) for item in qmt_data.get(
            "watch_codes", ["132024.SH", "600362.SH"]
        )),
        instrument_names={
            str(code): str(name) for code, name in instrument_names_data.items()
        },
        callback_queue_size=int(qmt_data.get("callback_queue_size", 100_000)),
        stale_after_seconds=float(qmt_data.get("stale_after_seconds", 3.0)),
        status_interval_seconds=float(qmt_data.get("status_interval_seconds", 15.0)),
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
        quantity_bonds=float(paper_data.get(
            "quantity_bonds", paper_data.get("quantity", 100.0)
        )),
        price_tick=float(paper_data.get("price_tick", 0.001)),
        maker_entry_wait_seconds=int(paper_data.get("maker_entry_wait_seconds", 60)),
        maker_exit_wait_seconds=int(paper_data.get("maker_exit_wait_seconds", 180)),
        standing_reprice_ticks=int(paper_data.get("standing_reprice_ticks", 5)),
        standing_reprice_seconds=float(paper_data.get("standing_reprice_seconds", 10.0)),
        execution_models=tuple(str(item).upper() for item in paper_data.get(
            "execution_models", ["E1", "E2", "E3", "E4"]
        )),
        fill_modes=tuple(str(item).lower() for item in paper_data.get(
            "fill_modes", ["optimistic", "queue", "conservative"]
        )),
    )
    maker_bond_codes = tuple(str(item) for item in maker_paper_data.get(
        "bond_codes", [qmt.bond_code]
    ))
    underlying_stock_codes_data = maker_paper_data.get(
        "underlying_stock_codes"
    )
    if underlying_stock_codes_data is None:
        underlying_stock_codes = {
            code: (
                qmt.stock_code if code == qmt.bond_code
                else DEFAULT_MAKER_UNDERLYING_STOCK_CODES.get(code, "")
            )
            for code in maker_bond_codes
        }
    elif not isinstance(underlying_stock_codes_data, dict):
        raise ConfigError(
            "maker_paper.underlying_stock_codes must be a TOML table"
        )
    else:
        underlying_stock_codes = {
            str(code): str(stock_code)
            for code, stock_code in underlying_stock_codes_data.items()
        }

    maker_paper = MakerPaperConfig(
        enabled=bool(maker_paper_data.get("enabled", False)),
        bond_codes=maker_bond_codes,
        underlying_stock_codes=underlying_stock_codes,
        initial_inventory_bonds=float(maker_paper_data.get(
            "initial_inventory_bonds", 1_000.0
        )),
        additional_buying_capacity_bonds=float(maker_paper_data.get(
            "additional_buying_capacity_bonds", 1_000.0
        )),
        maximum_inventory_bonds=float(maker_paper_data.get(
            "maximum_inventory_bonds", 2_000.0
        )),
        initial_cash_cny=float(maker_paper_data.get(
            "initial_cash_cny", 136_800.0
        )),
        order_quantity_bonds=float(maker_paper_data.get(
            "order_quantity_bonds", 1_000.0
        )),
        price_tick=float(maker_paper_data.get("price_tick", 0.001)),
        fill_modes=tuple(str(item).lower() for item in maker_paper_data.get(
            "fill_modes", ["priority", "queue"]
        )),
        realtime_comparison_model_ids=tuple(
            str(item) for item in maker_paper_data.get(
                "realtime_comparison_model_ids", []
            )
        ),
        earliest_entry=str(maker_paper_data.get(
            "earliest_entry", "09:20:00.000"
        )),
        latest_entry=str(maker_paper_data.get(
            "latest_entry", "15:29:59.999"
        )),
        opening_caution_effective_date=str(maker_paper_data.get(
            "opening_caution_effective_date", "2026-08-21"
        )),
        opening_caution_end=str(maker_paper_data.get(
            "opening_caution_end", "09:30:00.000"
        )),
        opening_caution_minimum_edge=float(maker_paper_data.get(
            "opening_caution_minimum_edge", 1.00
        )),
        super_windfall_enabled=bool(maker_paper_data.get(
            "super_windfall_enabled", False
        )),
        super_windfall_quantity_bonds=float(maker_paper_data.get(
            "super_windfall_quantity_bonds", 10.0
        )),
        super_windfall_credit_cny=float(maker_paper_data.get(
            "super_windfall_credit_cny", 2_000.0
        )),
    )
    _validate(qmt, storage, m0, paper, maker_paper)
    return AppConfig(config_path, qmt, storage, m0, paper, maker_paper)


def _validate(
    qmt: QmtConfig, storage: StorageConfig, m0: M0Config,
    paper: PaperConfig, maker_paper: MakerPaperConfig,
) -> None:
    if qmt.port <= 0:
        raise ConfigError("qmt.port must be positive")
    if qmt.status_interval_seconds <= 0:
        raise ConfigError("qmt.status_interval_seconds must be positive")
    if not qmt.bond_code or not qmt.stock_code or qmt.bond_code == qmt.stock_code:
        raise ConfigError("qmt bond_code and stock_code must be distinct")
    if any(not code for code in qmt.watch_codes):
        raise ConfigError("qmt.watch_codes cannot contain blank codes")
    if any(not code or not name for code, name in qmt.instrument_names.items()):
        raise ConfigError("qmt.instrument_names cannot contain blank codes or names")
    if m0.minimum_observations > m0.rolling_observations:
        raise ConfigError("m0.minimum_observations cannot exceed rolling_observations")
    if not 0 < m0.entry_discount < 0.2 or not 0 <= m0.exit_discount < m0.entry_discount:
        raise ConfigError("m0 entry/exit discounts are invalid")
    if paper.quantity_bonds <= 0 or paper.notional_cny <= 0 or paper.price_tick <= 0:
        raise ConfigError("paper quantity, notional and price_tick must be positive")
    if paper.quantity_bonds % 10 != 0:
        raise ConfigError("paper.quantity_bonds must be a multiple of 10 bonds")
    if paper.standing_reprice_ticks <= 0 or paper.standing_reprice_seconds < 0:
        raise ConfigError("paper standing reprice limits are invalid")
    if not set(paper.execution_models).issubset({"E1", "E2", "E3", "E4"}):
        raise ConfigError("paper.execution_models may only contain E1/E2/E3/E4")
    if not set(paper.fill_modes).issubset({"optimistic", "queue", "conservative"}):
        raise ConfigError("paper.fill_modes contains an unknown mode")
    maker_values = (
        maker_paper.initial_inventory_bonds,
        maker_paper.additional_buying_capacity_bonds,
        maker_paper.maximum_inventory_bonds,
        maker_paper.initial_cash_cny,
        maker_paper.order_quantity_bonds,
        maker_paper.price_tick,
    )
    if any(value <= 0 for value in maker_values):
        raise ConfigError("maker_paper inventory, cash, quantity and price_tick must be positive")
    if maker_paper.initial_inventory_bonds > maker_paper.maximum_inventory_bonds:
        raise ConfigError("maker_paper initial inventory cannot exceed maximum inventory")
    expected_maximum = (
        maker_paper.initial_inventory_bonds
        + maker_paper.additional_buying_capacity_bonds
    )
    if abs(maker_paper.maximum_inventory_bonds - expected_maximum) > 1e-9:
        raise ConfigError(
            "maker_paper.maximum_inventory_bonds must equal "
            "initial_inventory_bonds + additional_buying_capacity_bonds"
        )
    if any(value % 10 != 0 for value in (
        maker_paper.initial_inventory_bonds,
        maker_paper.additional_buying_capacity_bonds,
        maker_paper.maximum_inventory_bonds,
        maker_paper.order_quantity_bonds,
    )):
        raise ConfigError("maker_paper bond quantities must be multiples of 10")
    if not set(maker_paper.fill_modes).issubset({"priority", "queue"}):
        raise ConfigError("maker_paper.fill_modes may only contain priority/queue")
    supported_realtime_comparison_models = {
        "maker_priority_v1_37_candidate",
        "maker_priority_v1_42_candidate",
        "maker_priority_v1_43_candidate",
        "maker_priority_v1_44_candidate",
        "maker_queue_v1_13_candidate",
        "maker_queue_v1_17_candidate",
        "maker_queue_v1_18_candidate",
    }
    comparison_models = maker_paper.realtime_comparison_model_ids
    if len(set(comparison_models)) != len(comparison_models):
        raise ConfigError(
            "maker_paper.realtime_comparison_model_ids cannot contain duplicates"
        )
    unknown_comparison_models = (
        set(comparison_models) - supported_realtime_comparison_models
    )
    if unknown_comparison_models:
        raise ConfigError(
            "maker_paper.realtime_comparison_model_ids contains unsupported "
            f"models: {sorted(unknown_comparison_models)}"
        )
    maker_bond_codes = maker_paper.bond_codes or (qmt.bond_code,)
    if any(not code for code in maker_bond_codes):
        raise ConfigError("maker_paper.bond_codes cannot contain blank codes")
    if len(set(maker_bond_codes)) != len(maker_bond_codes):
        raise ConfigError("maker_paper.bond_codes cannot contain duplicates")
    underlying_stock_codes = maker_paper.underlying_stock_codes
    if any(
        not bond_code or not stock_code
        for bond_code, stock_code in underlying_stock_codes.items()
    ):
        raise ConfigError(
            "maker_paper.underlying_stock_codes cannot contain blank codes"
        )
    missing_underlying_mappings = (
        set(maker_bond_codes) - set(underlying_stock_codes)
    )
    if missing_underlying_mappings:
        raise ConfigError(
            "maker_paper.underlying_stock_codes must map every maker bond: "
            f"{sorted(missing_underlying_mappings)}"
        )
    if underlying_stock_codes.get(qmt.bond_code) != qmt.stock_code:
        raise ConfigError(
            "the primary maker bond must map to qmt.stock_code"
        )
    if set(underlying_stock_codes.values()) & set(maker_bond_codes):
        raise ConfigError(
            "maker_paper underlying stock codes cannot be maker bond codes"
        )
    recorded_codes = {
        qmt.bond_code, qmt.stock_code, *qmt.watch_codes,
    }
    missing_codes = set(maker_bond_codes) - recorded_codes
    if missing_codes:
        raise ConfigError(
            "maker_paper.bond_codes must be included in qmt.bond_code or "
            f"qmt.watch_codes: {sorted(missing_codes)}"
        )
    if qmt.stock_code in maker_bond_codes:
        raise ConfigError("maker_paper.bond_codes cannot include qmt.stock_code")
    missing_stock_codes = (
        {underlying_stock_codes[code] for code in maker_bond_codes}
        - recorded_codes
    )
    if missing_stock_codes:
        raise ConfigError(
            "maker_paper underlying stocks must be included in qmt.stock_code "
            f"or qmt.watch_codes: {sorted(missing_stock_codes)}"
        )
    if maker_paper.earliest_entry >= maker_paper.latest_entry:
        raise ConfigError("maker_paper entry window is invalid")
    try:
        date.fromisoformat(maker_paper.opening_caution_effective_date)
    except ValueError as exc:
        raise ConfigError(
            "maker_paper opening caution effective date is invalid"
        ) from exc
    if not (
        maker_paper.earliest_entry < maker_paper.opening_caution_end
        <= maker_paper.latest_entry
        and maker_paper.opening_caution_minimum_edge > 0
    ):
        raise ConfigError("maker_paper opening caution policy is invalid")
    if (
        maker_paper.super_windfall_quantity_bonds <= 0
        or maker_paper.super_windfall_quantity_bonds % 10 != 0
        or maker_paper.super_windfall_credit_cny <= 0
    ):
        raise ConfigError(
            "maker_paper super windfall quantity must be a positive multiple "
            "of 10 bonds and credit must be positive"
        )
    storage.database.parent.mkdir(parents=True, exist_ok=True)


def maker_underlying_stock_code(config: AppConfig, bond_code: str) -> str:
    """Return the causally paired stock for one maker bond."""

    try:
        return config.maker_paper.underlying_stock_codes[bond_code]
    except KeyError as exc:
        raise ConfigError(
            f"No maker_paper underlying stock configured for {bond_code}"
        ) from exc
