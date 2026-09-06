"""Borsa katmani testleri — ag baglantisi olmadan."""

import copy

import ccxt
import pytest

from bot.config import DEFAULTS
from bot.exchange import (
    Exchange, ExchangeError, retry, timeframe_hours, timeframe_ms,
)


def test_timeframe_conversions():
    assert timeframe_ms("4h") == 4 * 3_600_000
    assert timeframe_hours("1d") == 24.0
    with pytest.raises(ExchangeError, match="Desteklenmeyen"):
        timeframe_ms("7h")


def test_retry_gives_up_after_attempts_and_wraps_the_error():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        raise ccxt.NetworkError("baglanti koptu")

    with pytest.raises(ExchangeError, match="3 deneme"):
        retry(flaky, attempts=3, base_delay=0.0)
    assert calls["n"] == 3


def test_retry_succeeds_on_a_later_attempt():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ccxt.RequestTimeout("yavas")
        return "tamam"

    assert retry(flaky, attempts=4, base_delay=0.0) == "tamam"


def test_non_retryable_errors_fail_fast_as_exchange_error():
    calls = {"n": 0}

    def broke():
        calls["n"] += 1
        raise ccxt.InsufficientFunds("bakiye yetersiz")

    with pytest.raises(ExchangeError, match="bakiye yetersiz"):
        retry(broke, attempts=4, base_delay=0.0)
    assert calls["n"] == 1, "tekrar denemenin faydasi olmayan hata tekrarlanmamali"


def test_futures_setup_loads_only_linear_markets_on_the_futures_testnet():
    ex = Exchange(copy.deepcopy(DEFAULTS))
    assert ex.client.options["defaultType"] == "future"
    assert ex.client.options["fetchMarkets"] == ["linear"]
    assert "testnet.binancefuture.com" in ex.client.urls["api"]["fapiPublic"]


def test_spot_setup_loads_only_spot_markets():
    cfg = copy.deepcopy(DEFAULTS)
    cfg["exchange"].update({"market_type": "spot", "testnet": False})
    ex = Exchange(cfg)
    assert ex.client.options["fetchMarkets"] == ["spot"]
    assert ex.client.options["defaultType"] == "spot"


def test_unknown_exchange_is_reported_clearly():
    cfg = copy.deepcopy(DEFAULTS)
    cfg["exchange"]["id"] = "yokboyle"
    with pytest.raises(ExchangeError, match="tanimiyor"):
        Exchange(cfg)


# ------------------------------------------------------------- sayfalama

class _PagingClient:
    """Tek istekte en fazla `page` bar veren sahte borsa."""

    def __init__(self, total=3500, timeframe="4h", page=1000):
        self.step = timeframe_ms(timeframe)
        self.now = 1_800_000_000_000
        self.start = self.now - total * self.step
        self.page = page
        self.calls = 0

    def milliseconds(self):
        return self.now

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        self.calls += 1
        limit = min(limit or self.page, self.page)
        begin = self.now - limit * self.step if since is None else since
        t = max(begin, self.start)
        rows = []
        while t < self.now and len(rows) < limit:
            rows.append([t, 1.0, 2.0, 0.5, 1.5, 100.0])
            t += self.step
        return rows


def paging_exchange(**kwargs):
    ex = Exchange.__new__(Exchange)
    ex.client = _PagingClient(**kwargs)
    return ex


def test_small_request_uses_a_single_call():
    ex = paging_exchange()
    assert len(ex._fetch_ohlcv_paged("X", "4h", 800)) == 800
    assert ex.client.calls == 1


def test_large_request_is_paged_until_complete():
    ex = paging_exchange(total=4000)
    rows = ex._fetch_ohlcv_paged("X", "4h", 2500)
    stamps = [r[0] for r in rows]
    assert len(rows) == 2500
    assert ex.client.calls > 1
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps), "sayfalar arasinda tekrar eden bar var"


def test_paging_stops_when_history_runs_out():
    ex = paging_exchange(total=1200)
    rows = ex._fetch_ohlcv_paged("X", "4h", 5000)
    assert 0 < len(rows) <= 1200
    assert ex.client.calls < 10, "veri bitince donguden cikmali"


# --------------------------------------------------- saat kaymasi (-1021)

def test_clock_skew_errors_are_recognised():
    from bot.exchange import is_clock_skew

    skew = '{"code":-1021,"msg":"Timestamp for this request was 1000ms ahead"}'
    assert is_clock_skew(Exception(skew))
    assert is_clock_skew(Exception("recvWindow"))
    assert not is_clock_skew(Exception('{"code":-2015,"msg":"Invalid API-key"}'))
    assert not is_clock_skew(Exception("baglanti koptu"))


class _SkewedClient:
    """Saat esitlenene kadar -1021 doner."""

    def __init__(self):
        self.options = {}
        self.calls = 0
        self.synced = False

    def load_time_difference(self):
        self.synced = True
        self.options["timeDifference"] = -1500

    def work(self):
        self.calls += 1
        if not self.synced:
            raise ccxt.BadRequest('binance {"code":-1021,"msg":"Timestamp ahead"}')
        return "tamam"


def skew_exchange():
    ex = Exchange.__new__(Exchange)
    ex.client = _SkewedClient()
    ex._time_synced = False
    ex.time_offset_ms = 0.0
    return ex


def test_clock_skew_is_fixed_automatically_and_the_call_succeeds():
    ex = skew_exchange()
    assert ex._call(ex.client.work) == "tamam"
    assert ex.client.synced, "saat esitlenmis olmali"
    assert ex.time_offset_ms == -1500


def test_time_is_synced_only_once_per_session():
    ex = skew_exchange()
    ex._call(ex.client.work)
    before = ex.client.calls
    ex.client.synced = False        # borsa yine -1021 dondurursa
    with pytest.raises(ExchangeError):
        ex._call(ex.client.work)    # ikinci kez esitlemeye calismamali
    assert ex.client.calls == before + 1


def test_other_errors_are_not_treated_as_clock_skew():
    ex = skew_exchange()

    def refuses():
        raise ccxt.AuthenticationError("Invalid API-key")

    with pytest.raises(ExchangeError, match="Invalid API-key"):
        ex._call(refuses)
    assert not ex.client.synced, "ilgisiz hatada saat esitlemeye kalkmamali"


# ------------------------------------------- gizli (imzali) cagri yapilmamasi

def test_currency_metadata_fetch_is_disabled():
    """ccxt load_markets() icinde ONCE fetch_currencies() cagirir ve API anahtari
    varsa bu IMZALI bir istektir. Bota gerekmiyor; acik kalirsa saat kaymasi
    veya eksik sapi izni yuzunden PIYASA VERISI bile alinamaz."""
    ex = Exchange(copy.deepcopy(DEFAULTS))
    assert ex.client.options["fetchCurrencies"] is False


def test_market_data_needs_no_signed_request_even_with_api_keys():
    """Anahtar tanimliyken de veri yolu tamamen herkese acik kalmali."""
    cfg = copy.deepcopy(DEFAULTS)
    cfg["exchange"].update({"api_key": "x" * 16, "api_secret": "y" * 16})
    ex = Exchange(cfg)
    assert ex.client.options["fetchCurrencies"] is False
    assert ex.client.fetch_currencies() == {}


def test_receive_window_is_generous_enough_for_slow_machines():
    ex = Exchange(copy.deepcopy(DEFAULTS))
    assert ex.client.options["recvWindow"] >= 10_000
