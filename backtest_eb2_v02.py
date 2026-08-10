from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from xtquant import xtdata


BOND = "132026.SH"
STOCK = "600900.SH"
TRAIN_START = "20260401"
TEST_START = pd.Timestamp("2026-07-10")
TEST_END = pd.Timestamp("2026-08-10")
MATURITY = pd.Timestamp("2027-05-24")


def conversion_price(day: pd.Timestamp) -> float:
    if day >= pd.Timestamp("2026-07-17"):
        return 21.20
    if day >= pd.Timestamp("2026-02-12"):
        return 21.99
    return 22.20


def first_level(value) -> float:
    try:
        return float(value[0])
    except (IndexError, TypeError, ValueError):
        return np.nan


def robust_sigma(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        return np.nan
    median = np.median(values)
    return 1.4826 * np.median(np.abs(values - median))


def ridge_fit(x: np.ndarray, y: np.ndarray, weights: np.ndarray, ridge: float = 0.5):
    mean = np.average(x, axis=0, weights=weights)
    scale = np.sqrt(np.average((x - mean) ** 2, axis=0, weights=weights))
    scale[scale < 1e-10] = 1.0
    z = (x - mean) / scale

    def solve(mask: np.ndarray):
        design = np.column_stack([np.ones(mask.sum()), z[mask]])
        sw = np.sqrt(weights[mask])
        lhs = design * sw[:, None]
        rhs = y[mask] * sw
        penalty = np.eye(lhs.shape[1]) * ridge
        penalty[0, 0] = 0.0
        return np.linalg.solve(lhs.T @ lhs + penalty, lhs.T @ rhs)

    mask = np.ones(len(y), dtype=bool)
    coef = solve(mask)
    fitted = np.column_stack([np.ones(len(z)), z]) @ coef
    resid = y - fitted
    sigma = robust_sigma(resid)
    if np.isfinite(sigma) and sigma > 0:
        mask = np.abs(resid - np.median(resid)) <= 3.0 * sigma
        coef = solve(mask)
    return coef, mean, scale, mask


def ridge_predict(x: np.ndarray, model) -> np.ndarray:
    coef, mean, scale, _ = model
    z = (x - mean) / scale
    return np.column_stack([np.ones(len(z)), z]) @ coef


def load_minute_data() -> pd.DataFrame:
    data = xtdata.get_market_data_ex(
        [], [BOND, STOCK], period="1m", start_time=TRAIN_START,
        end_time=TEST_END.strftime("%Y%m%d"), count=-1,
        dividend_type="none", fill_data=False,
    )
    frames = {}
    for code in (BOND, STOCK):
        frame = data[code].copy()
        frame["datetime"] = pd.to_datetime(frame.index.astype(str), format="%Y%m%d%H%M%S")
        frame = frame.set_index("datetime")
        frames[code] = frame[["close", "volume"]].rename(
            columns={"close": f"close_{code}", "volume": f"volume_{code}"}
        )
    merged = frames[BOND].join(frames[STOCK], how="inner")
    merged = merged[(merged[f"close_{BOND}"] > 0) & (merged[f"close_{STOCK}"] > 0)].copy()
    merged["day"] = merged.index.normalize()
    merged["xp"] = merged["day"].map(conversion_price)
    merged["parity"] = 100.0 * merged[f"close_{STOCK}"] / merged["xp"]
    merged["premium"] = merged[f"close_{BOND}"] / merged["parity"] - 1.0
    return merged


def load_daily_data() -> pd.DataFrame:
    data = xtdata.get_market_data_ex(
        [], [BOND, STOCK], period="1d", start_time=TRAIN_START,
        end_time=TEST_END.strftime("%Y%m%d"), count=-1,
        dividend_type="none", fill_data=False,
    )
    bond = data[BOND].copy()
    stock = data[STOCK].copy()
    bond.index = pd.to_datetime(bond.index, format="%Y%m%d")
    stock.index = pd.to_datetime(stock.index, format="%Y%m%d")
    daily = bond[["close", "low", "high"]].rename(
        columns={"close": "bond_close", "low": "bond_low", "high": "bond_high"}
    ).join(stock[["close", "low", "high"]].rename(
        columns={"close": "stock_close", "low": "stock_low", "high": "stock_high"}
    ))
    daily["xp"] = daily.index.map(conversion_price)
    daily["parity"] = 100.0 * daily["stock_close"] / daily["xp"]
    daily["stock_return"] = np.log(daily["stock_close"]).diff()
    daily["rv20"] = daily["stock_return"].rolling(20, min_periods=10).std() * np.sqrt(252)
    daily["rv20_prior"] = daily["rv20"].shift(1)

    counts = []
    for current_day in daily.index:
        history = daily.loc[:current_day].tail(30)
        thresholds = history["xp"] * 1.30
        counts.append(int((history["stock_close"] >= thresholds).sum()))
    daily["redeem_count_including_day"] = counts
    daily["redeem_count_prior"] = daily["redeem_count_including_day"].shift(1).fillna(0)
    return daily


def model_features(frame: pd.DataFrame, daily: pd.DataFrame) -> np.ndarray:
    day_values = frame["day"]
    rv = day_values.map(daily["rv20_prior"]).ffill().fillna(daily["rv20_prior"].median())
    redeem = day_values.map(daily["redeem_count_prior"]).fillna(0) / 15.0
    ttm = (MATURITY - day_values).dt.days / 365.0
    moneyness = np.log(frame["parity"] / 100.0)
    minute = frame.index.hour * 60 + frame.index.minute
    open_distance = np.minimum(np.abs(minute - 570), np.abs(minute - 780)) / 240.0
    return np.column_stack([
        moneyness,
        moneyness ** 2,
        rv.to_numpy(),
        redeem.to_numpy(),
        ttm.to_numpy(),
        open_distance.to_numpy(),
    ])


@dataclass
class DailyModel:
    day: pd.Timestamp
    p2_model: tuple
    p2_sigma: float
    beta: float
    p3_sigma: float
    previous_bond_close: float
    previous_parity: float
    redeem_count_prior: int


def train_models(minute: pd.DataFrame, daily: pd.DataFrame) -> dict[pd.Timestamp, DailyModel]:
    models = {}
    trading_days = sorted(minute["day"].unique())
    for raw_day in trading_days:
        day = pd.Timestamp(raw_day)
        if not (TEST_START <= day <= TEST_END):
            continue
        prior_days = [pd.Timestamp(x) for x in trading_days if x < raw_day]
        p2_days = prior_days[-60:]
        p3_days = prior_days[-10:]
        if len(p2_days) < 20 or len(p3_days) < 5:
            continue

        train2 = minute[minute["day"].isin(p2_days)].copy()
        train2 = train2[train2.index.minute % 5 == 0]
        # Remove obvious vendor spikes while retaining genuine tail observations.
        low, high = train2["premium"].quantile([0.005, 0.995])
        train2 = train2[train2["premium"].between(low, high)]
        x2 = model_features(train2, daily)
        y2 = train2["premium"].to_numpy()
        age = (day - train2["day"]).dt.days.to_numpy()
        weights = np.exp(-np.log(2) * age / 10.0)
        p2_model = ridge_fit(x2, y2, weights)
        p2_resid = y2 - ridge_predict(x2, p2_model)
        p2_sigma = robust_sigma(p2_resid)

        train3 = minute[minute["day"].isin(p3_days)].copy()
        train3 = train3[train3.index.minute % 5 == 0]
        rb = np.log(train3[f"close_{BOND}"]).groupby(train3["day"]).diff()
        rp = np.log(train3["parity"]).groupby(train3["day"]).diff()
        valid = rb.notna() & rp.notna() & (rp.abs() < 0.03) & (rb.abs() < 0.03)
        rbv, rpv = rb[valid].to_numpy(), rp[valid].to_numpy()
        beta0 = float(np.sum(rpv * rbv) / max(np.sum(rpv * rpv), 1e-12))
        resid0 = rbv - beta0 * rpv
        rsig = robust_sigma(resid0)
        keep = np.abs(resid0 - np.median(resid0)) <= max(3.0 * rsig, 1e-6)
        beta = float(np.sum(rpv[keep] * rbv[keep]) / max(np.sum(rpv[keep] ** 2), 1e-12))
        beta = float(np.clip(beta, 0.0, 1.5))

        previous_day = p3_days[-1]
        previous_bond_close = float(daily.loc[previous_day, "bond_close"])
        previous_parity = float(daily.loc[previous_day, "parity"])
        p3_errors = []
        for historical_day in p3_days[1:]:
            day_frame = train3[train3["day"] == historical_day]
            prior = daily.index[daily.index < historical_day][-1]
            anchor_bond = float(daily.loc[prior, "bond_close"])
            anchor_parity = float(daily.loc[prior, "parity"])
            predicted = anchor_bond * (day_frame["parity"] / anchor_parity) ** beta
            p3_errors.extend((day_frame[f"close_{BOND}"] / predicted - 1.0).to_numpy())
        p3_sigma = robust_sigma(np.asarray(p3_errors))

        models[day] = DailyModel(
            day=day,
            p2_model=p2_model,
            p2_sigma=float(p2_sigma),
            beta=beta,
            p3_sigma=float(p3_sigma),
            previous_bond_close=previous_bond_close,
            previous_parity=previous_parity,
            redeem_count_prior=int(daily.loc[day, "redeem_count_prior"]),
        )
    return models


def load_ticks(daily: pd.DataFrame) -> pd.DataFrame:
    data = xtdata.get_market_data_ex(
        [], [BOND, STOCK], period="tick", start_time=TEST_START.strftime("%Y%m%d"),
        end_time=TEST_END.strftime("%Y%m%d"), count=-1,
        dividend_type="none", fill_data=False,
    )
    frames = {}
    for code in (BOND, STOCK):
        frame = data[code].copy()
        frame["datetime"] = pd.to_datetime(frame["time"], unit="ms") + pd.Timedelta(hours=8)
        frame["bid"] = frame["bidPrice"].map(first_level)
        frame["ask"] = frame["askPrice"].map(first_level)
        frame["bid_volume"] = frame["bidVol"].map(first_level)
        frame["ask_volume"] = frame["askVol"].map(first_level)
        frame = frame.sort_values("datetime")
        frames[code] = frame[[
            "datetime", "lastPrice", "bid", "ask", "bid_volume", "ask_volume", "volume"
        ]].rename(columns={c: f"{c}_{code}" for c in [
            "lastPrice", "bid", "ask", "bid_volume", "ask_volume", "volume"
        ]})

    bond = frames[BOND]
    stock = frames[STOCK]
    merged = pd.merge_asof(
        bond, stock, on="datetime", direction="backward", tolerance=pd.Timedelta(seconds=3)
    ).dropna().copy()
    merged["day"] = merged["datetime"].dt.normalize()
    merged["clock"] = merged["datetime"].dt.strftime("%H:%M:%S")
    in_session = (
        merged["clock"].between("09:30:00", "11:30:00")
        | merged["clock"].between("13:00:00", "15:00:00")
    )
    merged = merged[in_session].copy()
    for code in (BOND, STOCK):
        merged = merged[
            (merged[f"bid_{code}"] > 0)
            & (merged[f"ask_{code}"] >= merged[f"bid_{code}"])
            & (merged[f"ask_{code}"] / merged[f"bid_{code}"] < 1.03)
        ]

    # Use only information available before the session to reject vendor-scale errors.
    previous_bond_close = merged["day"].map(daily["bond_close"].shift(1))
    previous_stock_close = merged["day"].map(daily["stock_close"].shift(1))
    bond_low = previous_bond_close * 0.80
    bond_high = previous_bond_close * 1.20
    stock_low = previous_stock_close * 0.80
    stock_high = previous_stock_close * 1.20
    merged = merged[
        merged[f"bid_{BOND}"].between(bond_low, bond_high)
        & merged[f"ask_{BOND}"].between(bond_low, bond_high)
        & merged[f"bid_{STOCK}"].between(stock_low, stock_high)
        & merged[f"ask_{STOCK}"].between(stock_low, stock_high)
    ].copy()
    return merged


def attach_fair_values(ticks: pd.DataFrame, daily: pd.DataFrame, models: dict) -> pd.DataFrame:
    parts = []
    for day, frame in ticks.groupby("day"):
        model = models.get(day)
        if model is None:
            continue
        frame = frame.copy()
        frame["xp"] = conversion_price(day)
        stock_mid = (frame[f"bid_{STOCK}"] + frame[f"ask_{STOCK}"]) / 2.0
        bond_mid = (frame[f"bid_{BOND}"] + frame[f"ask_{BOND}"]) / 2.0
        frame["parity"] = 100.0 * stock_mid / frame["xp"]
        minute_proxy = frame.set_index("datetime").resample("1min").last().ffill()
        minute_proxy["day"] = day
        minute_proxy["parity"] = 100.0 * (
            minute_proxy[f"bid_{STOCK}"] + minute_proxy[f"ask_{STOCK}"]
        ) / 2.0 / frame["xp"].iloc[0]
        features = model_features(minute_proxy, daily)
        minute_proxy["p2_premium"] = ridge_predict(features, model.p2_model)
        frame = pd.merge_asof(
            frame.sort_values("datetime"),
            minute_proxy[["p2_premium"]].reset_index().sort_values("datetime"),
            on="datetime", direction="backward", tolerance=pd.Timedelta(minutes=1),
        )
        frame["fair_p2"] = frame["parity"] * (1.0 + frame["p2_premium"])
        frame["fair_p3"] = model.previous_bond_close * (
            frame["parity"] / model.previous_parity
        ) ** model.beta
        frame["fair_low"] = frame[["fair_p2", "fair_p3"]].min(axis=1)
        frame["model_gap"] = (
            (frame["fair_p2"] - frame["fair_p3"]).abs() / frame["fair_low"]
        )
        residual_sigma = max(model.p2_sigma, model.p3_sigma)
        frame["residual_sigma"] = residual_sigma
        frame["threshold"] = max(0.008, 2.5 * residual_sigma)
        frame["discount"] = frame["fair_low"] / frame[f"ask_{BOND}"] - 1.0
        frame["exit_discount"] = frame["fair_low"] / frame[f"bid_{BOND}"] - 1.0
        frame["beta"] = model.beta
        frame["p2_sigma"] = model.p2_sigma
        frame["p3_sigma"] = model.p3_sigma
        frame["redeem_count_prior"] = model.redeem_count_prior
        frame["bond_mid"] = bond_mid
        parts.append(frame)
    return pd.concat(parts, ignore_index=True).sort_values("datetime")


def run_taker_backtest(
    frame: pd.DataFrame,
    use_dynamic_threshold: bool,
    use_redeem_gate: bool,
    threshold_multiplier: float = 2.5,
):
    trades = []
    position = None
    cooldown_until = pd.Timestamp.min
    for _, row in frame.iterrows():
        now = row["datetime"]
        if position is None:
            if now < cooldown_until:
                continue
            clock = now.strftime("%H:%M:%S")
            threshold = (
                max(0.008, threshold_multiplier * row["residual_sigma"])
                if use_dynamic_threshold else 0.008
            )
            allowed = (
                "09:35:00" <= clock <= "14:30:00"
                and row["model_gap"] <= 0.005
                and row["discount"] >= threshold
                and (not use_redeem_gate or row["redeem_count_prior"] < 15)
            )
            if allowed:
                position = {
                    "entry_time": now,
                    "entry_price": row[f"ask_{BOND}"],
                    "entry_discount": row["discount"],
                    "threshold": threshold,
                    "model_gap": row["model_gap"],
                    "redeem_count": row["redeem_count_prior"],
                }
            continue

        exit_price = row[f"bid_{BOND}"]
        pnl = exit_price / position["entry_price"] - 1.0
        held = (now - position["entry_time"]).total_seconds() / 60.0
        same_day = now.normalize() == position["entry_time"].normalize()
        reason = None
        if not same_day:
            reason = "overnight_guard"
        elif row["exit_discount"] <= 0.0015:
            reason = "converged"
        elif row["exit_discount"] >= position["entry_discount"] + 0.008:
            reason = "relative_stop"
        elif pnl <= -0.008:
            reason = "price_stop"
        elif held >= 60:
            reason = "timeout"
        elif now.strftime("%H:%M:%S") >= "14:55:00":
            reason = "eod"
        if reason:
            trades.append({
                **position,
                "exit_time": now,
                "exit_price": exit_price,
                "return": pnl,
                "minutes": held,
                "reason": reason,
            })
            position = None
            cooldown_until = now + pd.Timedelta(minutes=10)
    return pd.DataFrame(trades)


def summarize(name: str, trades: pd.DataFrame):
    if trades.empty:
        return {"model": name, "trades": 0}
    compounded = np.prod(1.0 + trades["return"]) - 1.0
    return {
        "model": name,
        "trades": len(trades),
        "days": trades["entry_time"].dt.normalize().nunique(),
        "win_rate": (trades["return"] > 0).mean(),
        "avg_return": trades["return"].mean(),
        "median_return": trades["return"].median(),
        "min_return": trades["return"].min(),
        "max_return": trades["return"].max(),
        "compounded": compounded,
        "avg_threshold": trades["threshold"].mean(),
        "avg_model_gap": trades["model_gap"].mean(),
    }


def main():
    xtdata.connect(port=58611)
    minute = load_minute_data()
    daily = load_daily_data()
    models = train_models(minute, daily)
    ticks = load_ticks(daily)
    valued = attach_fair_values(ticks, daily, models)

    variants = {
        "consensus_fixed_0.8": run_taker_backtest(valued, False, False),
        "consensus_dynamic_1.5x": run_taker_backtest(valued, True, False, 1.5),
        "consensus_dynamic_2.0x": run_taker_backtest(valued, True, False, 2.0),
        "consensus_dynamic_2.5x": run_taker_backtest(valued, True, False, 2.5),
        "consensus_dynamic_2.5x_redeem_gate": run_taker_backtest(valued, True, True, 2.5),
    }
    summaries = pd.DataFrame([summarize(name, trades) for name, trades in variants.items()])
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.width", 220)
    print("DAILY MODELS")
    model_rows = [{
        "day": day.date(), "beta": model.beta,
        "p2_sigma": model.p2_sigma, "p3_sigma": model.p3_sigma,
        "dynamic_threshold": max(0.008, 2.5 * max(model.p2_sigma, model.p3_sigma)),
        "redeem_count_prior": model.redeem_count_prior,
    } for day, model in models.items()]
    print(pd.DataFrame(model_rows).to_string(index=False, float_format=lambda x: f"{x:.4%}"))
    print("\nSUMMARY")
    print(summaries.to_string(index=False, float_format=lambda x: f"{x:.4%}"))
    for name, trades in variants.items():
        print(f"\n{name.upper()}")
        if trades.empty:
            print("no trades")
        else:
            cols = [
                "entry_time", "entry_price", "entry_discount", "threshold", "model_gap",
                "redeem_count", "exit_time", "exit_price", "return", "minutes", "reason",
            ]
            print(trades[cols].to_string(index=False, float_format=lambda x: f"{x:.4%}"))


if __name__ == "__main__":
    main()
