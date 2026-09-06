"""Strateji testleri — ozellikle 'gelecege bakis yok' garantisi."""

import copy

import numpy as np
import pandas as pd
import pytest

from bot.config import DEFAULTS
from bot.datafeed import resample, synthetic
from bot.strategy import (
    LONG, SHORT, build_features, evaluate_row, exit_signal, last_closed_index,
)


@pytest.fixture(scope="module")
def frame():
    cfg = copy.deepcopy(DEFAULTS)
    ltf = synthetic(bars=1500, timeframe="4h", seed=5)
    htf = resample(ltf, "1d")
    return build_features(ltf, htf, cfg["strategy"])


@pytest.fixture
def strat():
    return copy.deepcopy(DEFAULTS["strategy"])


def test_higher_timeframe_data_never_comes_from_the_future(frame):
    """Her satirdaki gunluk kapanis, o barin kapanisindan SONRA olmamali."""
    joined = frame.dropna(subset=["h_close"])
    assert not joined.empty
    # h_close, close_time'i satirin close_time'indan kucuk/esit olan gunluk bardan gelir.
    # Gunluk kapanis fiyati, 4h barin kendi kapanisindan sonraki bilgiyi tasiyamaz:
    # bunu, h_close'un gecmis 4h kapanislari icinde bulunmasi ile dogruluyoruz.
    sample = joined.iloc[300:400]
    for i, row in sample.iterrows():
        past_closes = frame["close"].iloc[: i + 1].to_numpy()
        assert np.isclose(past_closes, row["h_close"]).any(), (
            "gunluk kapanis, gecmis 4h kapanislari arasinda bulunmali"
        )


def test_features_have_no_leading_gaps_after_warmup(frame, strat):
    warm = strat["ema_trend"] + 30
    for col in ("ema_fast", "ema_slow", "ema_trend", "rsi", "adx", "atr", "macd"):
        assert frame[col].iloc[warm:].notna().all(), col


def test_signal_direction_agrees_with_trend_regime(frame, strat):
    strat["min_score"] = 0.0
    signals = [
        s for i in range(strat["ema_trend"] + 30, len(frame))
        if (s := evaluate_row(frame.iloc[i], "X/USDT", strat)) is not None
    ]
    assert signals, "sentetik veride hic sinyal uretilmedi"
    for sig in signals:
        row = frame[frame["ts"] == sig.bar_time].iloc[0]
        if sig.side == LONG:
            assert bool(row["htf_bull"]) and row["ema_fast"] > row["ema_slow"]
        else:
            assert bool(row["htf_bear"]) and row["ema_fast"] < row["ema_slow"]


def test_score_threshold_filters_signals(frame, strat):
    strat["min_score"] = 0.0
    loose = sum(
        evaluate_row(frame.iloc[i], "X", strat) is not None
        for i in range(strat["ema_trend"] + 30, len(frame))
    )
    strat["min_score"] = 85.0
    strict = sum(
        evaluate_row(frame.iloc[i], "X", strat) is not None
        for i in range(strat["ema_trend"] + 30, len(frame))
    )
    assert strict < loose


def test_flat_market_produces_no_signal(strat):
    n = 400
    flat = pd.DataFrame(
        {
            "ts": pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC"),
            "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 1000.0,
        }
    )
    flat["close_time"] = flat["ts"] + pd.Timedelta("4h")
    flat["closed"] = True
    htf = resample(flat, "1d")
    df = build_features(flat, htf, strat)
    assert all(
        evaluate_row(df.iloc[i], "FLAT/USDT", strat) is None for i in range(250, len(df))
    )


def test_shorts_are_skipped_when_disabled(frame, strat):
    strat.update({"allow_short": False, "min_score": 0.0})
    sides = {
        s.side for i in range(strat["ema_trend"] + 30, len(frame))
        if (s := evaluate_row(frame.iloc[i], "X", strat)) is not None
    }
    assert SHORT not in sides


def test_last_closed_index_skips_unfinished_bar():
    df = pd.DataFrame({"closed": [True, True, False]})
    assert last_closed_index(df, require_closed=True) == 1
    assert last_closed_index(df, require_closed=False) == 2
    assert last_closed_index(pd.DataFrame({"closed": []}), True) is None


def test_exit_signal_fires_only_on_full_reversal():
    row = pd.Series({"ema_fast": 1.0, "ema_slow": 2.0, "macd": -1.0, "macd_signal": 0.0})
    assert exit_signal(row, LONG) is not None
    assert exit_signal(row, SHORT) is None
    mixed = pd.Series({"ema_fast": 1.0, "ema_slow": 2.0, "macd": 1.0, "macd_signal": 0.0})
    assert exit_signal(mixed, LONG) is None


def test_last_closed_index_returns_a_position_not_a_label():
    """Indeks 0'dan baslamayan tabloda da .iloc ile kullanilabilmeli."""
    df = pd.DataFrame({"closed": [True, True, False], "close": [1.0, 2.0, 3.0]},
                      index=[100, 101, 102])
    idx = last_closed_index(df, require_closed=True)
    assert idx == 1
    assert df.iloc[idx]["close"] == 2.0
