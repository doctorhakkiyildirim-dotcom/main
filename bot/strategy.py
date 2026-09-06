"""Swing (uzun vadeli) strateji: cok zaman dilimli trend takibi.

Mantik ozeti
------------
1. UST ZAMAN DILIMI (1d) rejimi belirler: EMA50 > EMA200 ve fiyat EMA200
   uzerinde ise sadece LONG bakariz; tersi ise sadece SHORT.
2. SINYAL ZAMAN DILIMI (4h) tetigi verir: EMA20/EMA50 dizilimi + MACD
   kesisiminin TAZE olmasi + RSI'nin asiri bolgede olmamasi + ADX ile
   trendin gercekten var olmasi.
3. Her aday 0-100 arasi puanlanir; bot en yuksek puanliyi secer.

Tum kosullar KAPANMIS bar uzerinde degerlendirilir; boylece "yeniden cizim"
(repaint) olmaz ve backtest ile canli sonuclar birbirini tutar.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ind

LONG = "long"
SHORT = "short"


@dataclass
class Signal:
    """Bir islem adayi."""

    symbol: str
    side: str
    score: float
    bar_time: pd.Timestamp
    price: float
    atr: float
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def direction(self) -> int:
        """LONG icin +1, SHORT icin -1."""
        return 1 if self.side == LONG else -1

    @property
    def side_tr(self) -> str:
        """Ekranda gosterilecek yon."""
        return "ALIS (LONG)" if self.side == LONG else "SATIS (SHORT)"


def build_features(
    ltf: pd.DataFrame, htf: pd.DataFrame, strat: dict
) -> pd.DataFrame:
    """Sinyal zaman dilimine ust zaman dilimi trendini ekleyerek indikator tablosu kur.

    Ust zaman dilimi degerleri, SADECE kapanisi gecmiste kalan barlardan
    alinir (merge_asof + close_time) — yani gelecege bakis (lookahead) yoktur.
    """
    fast, slow, sig = strat["macd"]
    df = ltf.copy()

    df["ema_fast"] = ind.ema(df["close"], strat["ema_fast"])
    df["ema_slow"] = ind.ema(df["close"], strat["ema_slow"])
    df["ema_trend"] = ind.ema(df["close"], strat["ema_trend"])
    macd_df = ind.macd(df["close"], fast, slow, sig)
    df[macd_df.columns] = macd_df
    df["rsi"] = ind.rsi(df["close"], strat["rsi_period"])
    df["atr"] = ind.atr(df["high"], df["low"], df["close"], strat["atr_period"])
    adx_df = ind.adx(df["high"], df["low"], df["close"], strat["adx_period"])
    df[adx_df.columns] = adx_df
    df["vol_ma"] = ind.sma(df["volume"], strat["volume_ma"])
    df["vol_ratio"] = df["volume"] / df["vol_ma"].replace(0.0, np.nan)
    df["atr_pct"] = df["atr"] / df["close"] * 100.0

    df["macd_up"] = ind.cross_up(df["macd"], df["macd_signal"])
    df["macd_down"] = ind.cross_down(df["macd"], df["macd_signal"])
    df["bars_since_up"] = ind.bars_since(df["macd_up"])
    df["bars_since_down"] = ind.bars_since(df["macd_down"])
    df["hist_slope"] = df["macd_hist"] - df["macd_hist"].shift(1)

    # --- ust zaman dilimi trendi -------------------------------------------
    h = htf.copy()
    h["h_ema_slow"] = ind.ema(h["close"], strat["ema_slow"])
    h["h_ema_trend"] = ind.ema(h["close"], strat["ema_trend"])
    h["h_close"] = h["close"]
    h["h_trend_slope"] = ind.slope_pct(h["h_ema_slow"], 10)
    h["h_rsi"] = ind.rsi(h["close"], strat["rsi_period"])
    htf_cols = ["close_time", "h_close", "h_ema_slow", "h_ema_trend", "h_trend_slope", "h_rsi"]

    df = pd.merge_asof(
        df.sort_values("close_time"),
        h[htf_cols].sort_values("close_time"),
        on="close_time",
        direction="backward",
    )

    df["htf_bull"] = (df["h_ema_slow"] > df["h_ema_trend"]) & (df["h_close"] > df["h_ema_trend"])
    df["htf_bear"] = (df["h_ema_slow"] < df["h_ema_trend"]) & (df["h_close"] < df["h_ema_trend"])
    return df


def _score(row: pd.Series, side: str, strat: dict) -> tuple[float, list[str]]:
    """Sinyal kalitesini 0-100 arasi puanla ve gerekcelerini yaz."""
    parts: list[tuple[str, float]] = []
    reasons: list[str] = []

    # 1) Trend gucu (ADX 20->40 arasi lineer, maks 25 puan)
    adx_val = float(row["adx"])
    parts.append(("adx", np.clip((adx_val - strat["adx_min"]) / 20.0, 0, 1) * 25))
    reasons.append(f"ADX {adx_val:.1f} (trend gucu)")

    # 2) Ust zaman dilimi egimi (maks 20 puan)
    slope = float(row["h_trend_slope"]) * (1 if side == LONG else -1)
    parts.append(("htf_slope", np.clip(slope / 8.0, 0, 1) * 20))
    reasons.append(f"Ust trend egimi %{float(row['h_trend_slope']):+.2f}")

    # 3) MACD ivmesi — histogram dogru yonde buyuyor mu (maks 20 puan)
    hist_dir = float(row["hist_slope"]) * (1 if side == LONG else -1)
    hist_norm = hist_dir / max(float(row["atr"]) * 0.1, 1e-9)
    parts.append(("macd", np.clip(hist_norm, 0, 1) * 20))
    reasons.append(f"MACD histogram {'gucleniyor' if hist_dir > 0 else 'zayifliyor'}")

    # 4) RSI konumu — bandin ortasina yakinlik iyi (maks 15 puan)
    band = strat["rsi_long_band"] if side == LONG else strat["rsi_short_band"]
    center = (band[0] + band[1]) / 2.0
    half = max((band[1] - band[0]) / 2.0, 1e-9)
    parts.append(("rsi", (1 - min(abs(float(row["rsi"]) - center) / half, 1.0)) * 15))
    reasons.append(f"RSI {float(row['rsi']):.1f}")

    # 5) Hacim teyidi (maks 10 puan)
    vol_ratio = float(row["vol_ratio"]) if pd.notna(row["vol_ratio"]) else 1.0
    parts.append(("volume", np.clip((vol_ratio - 0.8) / 0.7, 0, 1) * 10))
    reasons.append(f"Hacim ort.nin {vol_ratio:.2f} kati")

    # 6) Sinyal tazeligi — kesisim ne kadar yeniyse o kadar iyi (maks 10 puan)
    bars = float(row["bars_since_up"] if side == LONG else row["bars_since_down"])
    parts.append(("fresh", np.clip(1 - bars / max(strat["macd_cross_lookback"], 1), 0, 1) * 10))
    reasons.append(f"MACD kesisimi {int(bars)} bar once")

    return float(sum(v for _, v in parts)), reasons


def evaluate_row(
    row: pd.Series, symbol: str, strat: dict
) -> Signal | None:
    """Tek bir bar icin sinyal uret (yoksa None)."""
    required = ("ema_fast", "ema_slow", "ema_trend", "rsi", "adx", "atr", "h_ema_trend")
    if any(pd.isna(row.get(col)) for col in required):
        return None
    if float(row["atr"]) <= 0:
        return None
    if float(row["adx"]) < strat["adx_min"]:
        return None

    vol_ratio = float(row["vol_ratio"]) if pd.notna(row.get("vol_ratio")) else 1.0
    if vol_ratio < strat["min_volume_ratio"]:
        return None

    lookback = strat["macd_cross_lookback"]
    price = float(row["close"])

    long_ok = (
        strat["allow_long"]
        and bool(row["htf_bull"])
        and row["ema_fast"] > row["ema_slow"]
        and price > row["ema_slow"]
        and row["macd"] > row["macd_signal"]
        and float(row["bars_since_up"]) <= lookback
        and strat["rsi_long_band"][0] <= float(row["rsi"]) <= strat["rsi_long_band"][1]
    )
    short_ok = (
        strat["allow_short"]
        and bool(row["htf_bear"])
        and row["ema_fast"] < row["ema_slow"]
        and price < row["ema_slow"]
        and row["macd"] < row["macd_signal"]
        and float(row["bars_since_down"]) <= lookback
        and strat["rsi_short_band"][0] <= float(row["rsi"]) <= strat["rsi_short_band"][1]
    )
    if long_ok == short_ok:  # ikisi de yok (veya celiskili) -> islem yok
        return None

    side = LONG if long_ok else SHORT
    score, reasons = _score(row, side, strat)
    if score < strat["min_score"]:
        return None

    return Signal(
        symbol=symbol,
        side=side,
        score=score,
        bar_time=row["ts"],
        price=price,
        atr=float(row["atr"]),
        reasons=reasons,
        metrics={
            "rsi": float(row["rsi"]),
            "adx": float(row["adx"]),
            "macd_hist": float(row["macd_hist"]),
            "atr_pct": float(row["atr_pct"]),
            "vol_ratio": vol_ratio,
            "htf_slope": float(row["h_trend_slope"]) if pd.notna(row["h_trend_slope"]) else 0.0,
        },
    )


def last_closed_index(df: pd.DataFrame, require_closed: bool) -> int | None:
    """Degerlendirilecek son barin KONUMU (.iloc icin). Yoksa None.

    Etiket degil konum doner; boylece cagiran taraf indeksin sifirdan
    baslamasina bel baglamak zorunda kalmaz.
    """
    if df.empty:
        return None
    if not require_closed:
        return len(df) - 1
    positions = np.flatnonzero(df["closed"].to_numpy(dtype=bool))
    return int(positions[-1]) if positions.size else None


def exit_signal(row: pd.Series, side: str) -> str | None:
    """Trend bozuldu mu — stop/TP disinda erken cikis gerekcesi."""
    if any(pd.isna(row.get(col)) for col in ("ema_fast", "ema_slow", "macd", "macd_signal")):
        return None
    if side == LONG and row["ema_fast"] < row["ema_slow"] and row["macd"] < row["macd_signal"]:
        return "Trend bozuldu (EMA ve MACD asagi dondu)"
    if side == SHORT and row["ema_fast"] > row["ema_slow"] and row["macd"] > row["macd_signal"]:
        return "Trend bozuldu (EMA ve MACD yukari dondu)"
    return None
