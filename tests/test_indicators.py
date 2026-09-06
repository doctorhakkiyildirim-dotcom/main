"""Indikator dogruluk testleri."""

import numpy as np
import pandas as pd
import pytest

from bot import indicators as ind


@pytest.fixture
def ohlc():
    rng = np.random.default_rng(11)
    n = 500
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    high = close + rng.uniform(0.1, 2.0, n)
    low = close - rng.uniform(0.1, 2.0, n)
    return high, low, close


def test_ema_matches_manual_recursion():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    alpha = 2 / (3 + 1)
    expected = [1.0]
    for x in s[1:]:
        expected.append(alpha * x + (1 - alpha) * expected[-1])
    assert np.allclose(ind.ema(s, 3).to_numpy(), expected)


def test_rsi_stays_in_bounds(ohlc):
    _, _, close = ohlc
    values = ind.rsi(close, 14).dropna()
    assert values.between(0, 100).all()


def test_rsi_edge_cases():
    assert ind.rsi(pd.Series(np.arange(1, 60, dtype=float))).iloc[-1] == 100.0
    assert ind.rsi(pd.Series([100.0] * 60)).iloc[-1] == 50.0
    assert ind.rsi(pd.Series(np.arange(60, 1, -1, dtype=float))).iloc[-1] == 0.0


def test_macd_histogram_is_line_minus_signal(ohlc):
    _, _, close = ohlc
    out = ind.macd(close, 12, 26, 9)
    assert np.allclose(out["macd_hist"], out["macd"] - out["macd_signal"])


def test_atr_is_positive_and_bounded_by_range(ohlc):
    high, low, close = ohlc
    values = ind.atr(high, low, close, 14).dropna()
    assert (values > 0).all()
    assert values.max() <= ind.true_range(high, low, close).max()


def test_adx_within_zero_hundred(ohlc):
    high, low, close = ohlc
    out = ind.adx(high, low, close, 14).dropna()
    assert out["adx"].between(0, 100).all()
    assert out["plus_di"].between(0, 100).all()


def test_cross_helpers_detect_single_bar_event():
    fast = pd.Series([1.0, 1.0, 3.0, 3.0])
    slow = pd.Series([2.0, 2.0, 2.0, 2.0])
    assert ind.cross_up(fast, slow).tolist() == [False, False, True, False]
    assert ind.cross_down(slow, fast).tolist() == [False, False, True, False]


def test_bars_since_counts_from_last_true():
    flag = pd.Series([False, True, False, False, True, False])
    assert ind.bars_since(flag).tolist() == [10**6, 0, 1, 2, 0, 1]
