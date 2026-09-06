"""Teknik indikatorler — saf pandas/numpy, harici TA kutuphanesi gerekmez.

Tum fonksiyonlar Wilder / standart tanimlari kullanir ve TradingView ile
ayni sonuclari verir (EMA tabanli MACD, Wilder ATR/RSI/ADX).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Ussel hareketli ortalama."""
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Basit hareketli ortalama."""
    return series.rolling(period, min_periods=period).mean()


def _wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder yumusatmasi (RSI/ATR/ADX icin)."""
    return series.ewm(alpha=1.0 / period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Goreli Guc Endeksi (0-100)."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = _wilder(gain, period)
    avg_loss = _wilder(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # Kenar durumlar: hic dusus yoksa RSI 100, hic hareket yoksa notr 50.
    out = out.where(avg_loss != 0.0, 100.0)
    return out.where(~((avg_gain == 0.0) & (avg_loss == 0.0)), 50.0)


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> pd.DataFrame:
    """MACD cizgisi, sinyal cizgisi ve histogram."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    return pd.DataFrame(
        {
            "macd": macd_line,
            "macd_signal": signal_line,
            "macd_hist": macd_line - signal_line,
        }
    )


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Gercek aralik (True Range)."""
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Ortalama Gercek Aralik — stop mesafesi ve pozisyon boyutu icin."""
    return _wilder(true_range(high, low, close), period)


def adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.DataFrame:
    """ADX / +DI / -DI — trendin gucunu olcer (yatay piyasayi eler)."""
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=high.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=high.index
    )

    atr_ = _wilder(true_range(high, low, close), period).replace(0.0, np.nan)
    plus_di = 100.0 * _wilder(plus_dm, period) / atr_
    minus_di = 100.0 * _wilder(minus_dm, period) / atr_

    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    return pd.DataFrame(
        {"adx": _wilder(dx.fillna(0.0), period), "plus_di": plus_di, "minus_di": minus_di}
    )


def slope_pct(series: pd.Series, lookback: int = 10) -> pd.Series:
    """Son `lookback` bardaki yuzde degisim — trend egimi olcusu."""
    past = series.shift(lookback)
    return (series - past) / past.abs().replace(0.0, np.nan) * 100.0


def rolling_extreme(series: pd.Series, period: int, mode: str) -> pd.Series:
    """Donchian tipi en yuksek/en dusuk."""
    if mode == "high":
        return series.rolling(period, min_periods=1).max()
    if mode == "low":
        return series.rolling(period, min_periods=1).min()
    raise ValueError("mode 'high' veya 'low' olmali")


def cross_up(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """fast, slow'u yukari kesti mi (bu barda)."""
    return (fast > slow) & (fast.shift(1) <= slow.shift(1))


def cross_down(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """fast, slow'u asagi kesti mi (bu barda)."""
    return (fast < slow) & (fast.shift(1) >= slow.shift(1))


def bars_since(flag: pd.Series) -> pd.Series:
    """Son True degerinden bu yana gecen bar sayisi (hic yoksa cok buyuk sayi)."""
    idx = pd.Series(np.arange(len(flag)), index=flag.index)
    last_true = idx.where(flag.fillna(False).astype(bool)).ffill()
    return (idx - last_true).fillna(10**6)
