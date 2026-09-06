"""Borsa disi veri kaynaklari: CSV dosyasi ve sentetik (test) verisi.

CSV, internet kisitli bir makinede veya kendi indirdigin veriyle backtest
yapmak icin kullanilir. Sentetik veri ise botun mantigini borsaya
baglanmadan dogrulamak (selftest) icindir.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .exchange import timeframe_ms

REQUIRED = ("open", "high", "low", "close", "volume")


def _finalise(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Ortak sutunlari (close_time, closed) ekle."""
    step = pd.Timedelta(milliseconds=timeframe_ms(timeframe))
    df = df.sort_values("ts").reset_index(drop=True)
    df["close_time"] = df["ts"] + step
    df["closed"] = df["close_time"] <= pd.Timestamp.now(tz="UTC")
    return df


def load_csv(path: str | Path, timeframe: str) -> pd.DataFrame:
    """OHLCV CSV oku.

    Beklenen sutunlar: timestamp (veya ts/date/time), open, high, low, close, volume.
    Zaman damgasi ms, saniye ya da metin tarih olabilir.
    """
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]

    ts_col = next((c for c in ("timestamp", "ts", "date", "time", "open_time")
                   if c in df.columns), None)
    if ts_col is None:
        raise ValueError(f"{path}: zaman sutunu bulunamadi (timestamp/date/time).")
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: eksik sutunlar: {missing}")

    raw = df[ts_col]
    if pd.api.types.is_numeric_dtype(raw):
        unit = "ms" if float(raw.iloc[0]) > 1e11 else "s"
        df["ts"] = pd.to_datetime(raw, unit=unit, utc=True)
    else:
        df["ts"] = pd.to_datetime(raw, utc=True, format="mixed")

    out = df[["ts", *REQUIRED]].astype({c: "float64" for c in REQUIRED})
    return _finalise(out, timeframe)


def resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Kucuk zaman dilimini buyuge cevir (or. 4h -> 1d)."""
    rule = pd.Timedelta(milliseconds=timeframe_ms(timeframe))
    agg = df.set_index("ts").resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    return _finalise(agg.reset_index(), timeframe)


def synthetic(
    bars: int = 3000, timeframe: str = "4h", seed: int = 42,
    start_price: float = 100.0, trend_strength: float = 0.55,
) -> pd.DataFrame:
    """Trendli + yatay donemleri olan sahte fiyat serisi uret.

    Gercek piyasa degildir; yalnizca kod yolunun (sinyal -> pozisyon ->
    iz suren stop -> cikis) uctan uca calistigini dogrulamak icindir.
    """
    rng = np.random.default_rng(seed)
    step_ms = timeframe_ms(timeframe)

    # Rejim anahtarlamali rastgele yuruyus: bir sure trend, bir sure yatay
    drift = np.zeros(bars)
    i = 0
    while i < bars:
        length = int(rng.integers(60, 260))
        regime = rng.random()
        if regime < trend_strength:
            slope = rng.normal(0, 1) * 0.0016
        else:
            slope = 0.0
        drift[i : i + length] = slope
        i += length

    vol = 0.012 * (1 + 0.5 * rng.random(bars))
    returns = drift + rng.normal(0, 1, bars) * vol
    close = start_price * np.exp(np.cumsum(returns))

    body = np.abs(rng.normal(0, 1, bars)) * vol * close
    high = close + body * rng.random(bars)
    low = close - body * rng.random(bars)
    open_ = np.concatenate([[start_price], close[:-1]])
    high = np.maximum.reduce([high, open_, close])
    low = np.minimum.reduce([low, open_, close])
    volume = rng.lognormal(mean=10.0, sigma=0.4, size=bars)

    end = pd.Timestamp.now(tz="UTC").floor("h")
    ts = pd.date_range(end=end, periods=bars, freq=pd.Timedelta(milliseconds=step_ms))
    df = pd.DataFrame(
        {"ts": ts, "open": open_, "high": high, "low": low, "close": close,
         "volume": volume}
    )
    return _finalise(df, timeframe)
