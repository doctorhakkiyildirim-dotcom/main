"""Borsa erisim katmani — ccxt sarmalayicisi.

Piyasa verisi (mum, hacim) API anahtari GEREKTIRMEZ; anahtar sadece
`live` modunda emir gondermek icin gerekir.
"""

from __future__ import annotations

import math
import time
from typing import Any

import ccxt
import pandas as pd

TIMEFRAME_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000, "3d": 259_200_000,
    "1w": 604_800_000,
}

RETRYABLE = (
    ccxt.NetworkError,
    ccxt.ExchangeNotAvailable,
    ccxt.RequestTimeout,
    ccxt.DDoSProtection,
    ccxt.RateLimitExceeded,
)

# Bilgisayarin saati borsanin saatiyle uyusmadiginda donen hata kodlari
CLOCK_SKEW_CODES = ("-1021", "-1131", "-5028", "-4188")


def is_clock_skew(error: BaseException) -> bool:
    """Hata, bilgisayar saatinin kaymasindan mi kaynaklaniyor."""
    text = str(error)
    return any(code in text for code in CLOCK_SKEW_CODES) or "recvWindow" in text


class ExchangeError(Exception):
    """Borsa katmani hatasi."""


def timeframe_ms(timeframe: str) -> int:
    """Zaman dilimini milisaniyeye cevir."""
    if timeframe not in TIMEFRAME_MS:
        raise ExchangeError(f"Desteklenmeyen zaman dilimi: {timeframe}")
    return TIMEFRAME_MS[timeframe]


def timeframe_hours(timeframe: str) -> float:
    """Zaman dilimini saate cevir."""
    return timeframe_ms(timeframe) / 3_600_000


def retry(fn, *args, attempts: int = 4, base_delay: float = 2.0, **kwargs):
    """Ag hatalarinda ustel geri cekilme ile yeniden dene.

    Borsa kaynakli TUM hatalar `ExchangeError` olarak disari verilir; boylece
    ust katmanlarin ccxt'yi tanimasi gerekmez.
    """
    name = getattr(fn, "__name__", str(fn))
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except RETRYABLE as exc:
            last = exc
            if i == attempts - 1:
                break
            time.sleep(base_delay * (2**i))
        except ccxt.BaseError as exc:
            # Yeniden denemenin fayda etmeyecegi hatalar (yetersiz bakiye,
            # gecersiz emir, desteklenmeyen ozellik) — hemen bildir.
            raise ExchangeError(f"{name}: {exc}") from exc
    raise ExchangeError(f"{name} basarisiz ({attempts} deneme): {last}") from last


class Exchange:
    """Bot ile ccxt arasindaki ince katman."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        ex_cfg = cfg["exchange"]
        self.market_type = ex_cfg["market_type"]
        self.quote = ex_cfg["quote"]
        self.testnet = bool(ex_cfg["testnet"])

        if not hasattr(ccxt, ex_cfg["id"]):
            raise ExchangeError(f"ccxt '{ex_cfg['id']}' borsasini tanimiyor.")

        is_future = self.market_type == "future"
        params: dict[str, Any] = {
            "enableRateLimit": True,
            "options": {
                "defaultType": "future" if is_future else "spot",
                "adjustForTimeDifference": True,
                # Sadece ihtiyac duyulan piyasa turunu yukle: hem hizli, hem de
                # vadeli calisirken gereksiz spot ucuna istek atilmaz.
                "fetchMarkets": ["linear"] if is_future else ["spot"],
                # ccxt, load_markets() sirasinda ONCE fetch_currencies() cagirir ve
                # API anahtari tanimliysa bu IMZALI bir istektir. Bota para birimi
                # meta verisi hic gerekmiyor; kapatinca herkese acik veri, anahtar
                # varken de anahtarsizmis gibi calisir.
                "fetchCurrencies": False,
                # Saat farki toleransi (Binance varsayilani 5 sn, biz genis tutuyoruz)
                "recvWindow": 10_000,
                # ccxt, Binance vadeli testnet'ini varsayilan olarak engelliyor.
                # Testnet'te emir denemek isteyenler icin bilincli olarak aciyoruz.
                "disableFuturesSandboxWarning": True,
            },
        }
        if ex_cfg.get("api_key") and ex_cfg.get("api_secret"):
            params["apiKey"] = ex_cfg["api_key"]
            params["secret"] = ex_cfg["api_secret"]

        self.client = getattr(ccxt, ex_cfg["id"])(params)
        if self.testnet:
            if not self.client.has.get("sandbox", True):
                raise ExchangeError(f"{ex_cfg['id']} testnet desteklemiyor.")
            self.client.set_sandbox_mode(True)
        self._markets: dict[str, Any] | None = None
        self._time_synced = False
        self.time_offset_ms = 0.0

    # ------------------------------------------------------------------ saat

    def sync_time(self) -> float:
        """Borsanin saatiyle arasindaki farki olc ve sonraki isteklere uygula.

        Windows'ta saat birkac saniye kayabilir; Binance 1 saniyeden fazla
        ILERI olan istekleri reddeder (-1021). ccxt bu farki telafi edebilir,
        yeter ki bir kez olculsun.
        """
        self.client.load_time_difference()
        self._time_synced = True
        self.time_offset_ms = float(self.client.options.get("timeDifference") or 0.0)
        return self.time_offset_ms

    def _call(self, fn, *args, **kwargs):
        """Borsa cagrisi — saat kaymasinda saati esitleyip bir kez daha dener."""
        try:
            return retry(fn, *args, **kwargs)
        except ExchangeError as exc:
            if self._time_synced or not is_clock_skew(exc):
                raise
            try:
                self.sync_time()
            except (ccxt.BaseError, OSError) as sync_exc:
                raise ExchangeError(
                    f"Bilgisayarinin saati borsanin saatiyle uyusmuyor ve "
                    f"duzeltilemedi: {sync_exc}"
                ) from exc
            return retry(fn, *args, **kwargs)

    # ---------------------------------------------------------------- piyasa

    @property
    def markets(self) -> dict[str, Any]:
        """Piyasa listesini bir kez yukle ve onbellekte tut."""
        if self._markets is None:
            self._markets = self._call(self.client.load_markets)
        return self._markets

    def resolve_symbol(self, symbol: str) -> str:
        """'BTC/USDT' -> vadelide 'BTC/USDT:USDT' gibi gercek piyasa kimligi."""
        markets = self.markets
        if symbol in markets:
            return symbol
        if self.market_type == "future":
            base, _, quote = symbol.partition("/")
            quote = quote.split(":")[0] or self.quote
            candidate = f"{base}/{quote}:{quote}"
            if candidate in markets:
                return candidate
        raise ExchangeError(f"'{symbol}' bu borsada bulunamadi.")

    def market(self, symbol: str) -> dict:
        """Piyasa meta verisi (hassasiyet, limitler)."""
        return self.markets[self.resolve_symbol(symbol)]

    def max_leverage(self, symbol: str) -> float:
        """Borsanin bu parite icin izin verdigi maksimum kaldirac."""
        limits = self.market(symbol).get("limits", {}).get("leverage") or {}
        return float(limits.get("max") or 20)

    def min_notional(self, symbol: str) -> float:
        """Borsanin izin verdigi en kucuk pozisyon buyuklugu (USDT)."""
        limits = self.market(symbol).get("limits", {}).get("cost") or {}
        return float(limits.get("min") or 0.0)

    # ------------------------------------------------------------------ veri

    MAX_BARS_PER_CALL = 1000

    def _fetch_ohlcv_paged(self, market_symbol: str, timeframe: str, limit: int) -> list:
        """Istenen bar sayisina ulasana kadar ileri dogru sayfala."""
        per_call = min(limit, self.MAX_BARS_PER_CALL)
        if limit <= per_call:
            return self._call(self.client.fetch_ohlcv, market_symbol, timeframe, None, per_call)

        step = timeframe_ms(timeframe)
        now_ms = self.client.milliseconds()
        since = now_ms - limit * step
        rows: list = []
        while since < now_ms:
            chunk = self._call(self.client.fetch_ohlcv, market_symbol, timeframe, since, per_call)
            if not chunk:
                break
            rows.extend(chunk)
            next_since = chunk[-1][0] + step
            if next_since <= since:      # ilerleme yok — sonsuz donguyu onle
                break
            since = next_since
            if len(chunk) < per_call:    # borsada daha eski/yeni veri kalmadi
                break

        unique = {row[0]: row for row in rows}
        return [unique[ts] for ts in sorted(unique)][-limit:]

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """Mum verisi cek. Son bar HENUZ KAPANMAMIS olabilir — `closed` sutununa bak.

        Borsalar tek istekte ~1000 bar verir. Daha fazlasi istenirse veri
        sayfa sayfa cekilir; aksi halde backtest sessizce kisa veriyle calisirdi.
        """
        market_symbol = self.resolve_symbol(symbol)
        raw = self._fetch_ohlcv_paged(market_symbol, timeframe, limit)
        if not raw:
            raise ExchangeError(f"{symbol} {timeframe} icin veri gelmedi.")

        df = pd.DataFrame(
            raw, columns=["ts", "open", "high", "low", "close", "volume"]
        ).astype({c: "float64" for c in ("open", "high", "low", "close", "volume")})
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        step = pd.Timedelta(milliseconds=timeframe_ms(timeframe))
        df["close_time"] = df["ts"] + step
        now = pd.Timestamp.now(tz="UTC")
        df["closed"] = df["close_time"] <= now
        return df.reset_index(drop=True)

    def last_price(self, symbol: str) -> float:
        """Anlik fiyat."""
        ticker = self._call(self.client.fetch_ticker, self.resolve_symbol(symbol))
        price = ticker.get("last") or ticker.get("close")
        if price is None:
            raise ExchangeError(f"{symbol} icin fiyat alinamadi.")
        return float(price)

    def discover_universe(self) -> list[str]:
        """Taranacak coin listesini olustur (auto: hacme gore en likitler)."""
        uni = self.cfg["universe"]
        if uni["mode"] == "manual":
            return [s for s in uni["manual_symbols"] if s not in set(uni["exclude"])]

        tickers = self._call(self.client.fetch_tickers)
        excluded = set(uni["exclude"])
        rows: list[tuple[str, float]] = []
        for sym, tk in tickers.items():
            market = self.markets.get(sym) or {}
            if not market.get("active"):
                continue
            if self.market_type == "future" and not market.get("swap"):
                continue
            if self.market_type == "spot" and not market.get("spot"):
                continue
            if market.get("quote") != self.quote:
                continue
            plain = f"{market.get('base')}/{market.get('quote')}"
            if plain in excluded or market.get("base") in excluded:
                continue
            volume = tk.get("quoteVolume")
            if volume is None or float(volume) < uni["min_24h_volume_usd"]:
                continue
            rows.append((plain, float(volume)))

        rows.sort(key=lambda r: r[1], reverse=True)
        return [sym for sym, _ in rows[: uni["top_n_by_volume"]]]

    # ----------------------------------------------------------------- emir

    def fetch_equity(self) -> float:
        """Hesaptaki kullanilabilir teminat (USDT)."""
        bal = self._call(self.client.fetch_balance)
        total = (bal.get("total") or {}).get(self.quote)
        return float(total or 0.0)

    def round_amount(self, symbol: str, amount: float) -> float:
        """Miktari borsanin adim buyuklugune yuvarla (asagi).

        Pahali bir coini kucuk sermayeyle almaya calisirsan sonuc 0 cikabilir —
        cagiran taraf bunu kontrol etmeli.
        """
        try:
            return float(self.client.amount_to_precision(self.resolve_symbol(symbol), amount))
        except ccxt.BaseError as exc:
            raise ExchangeError(f"{symbol} miktar yuvarlanamadi: {exc}") from exc

    def round_price(self, symbol: str, price: float) -> float:
        """Fiyati borsanin tick buyuklugune yuvarla."""
        try:
            return float(self.client.price_to_precision(self.resolve_symbol(symbol), price))
        except ccxt.BaseError as exc:
            raise ExchangeError(f"{symbol} fiyat yuvarlanamadi: {exc}") from exc

    def set_leverage(self, symbol: str, leverage: int) -> None:
        """Kaldiraci ayarla (vadeli piyasa)."""
        if self.market_type != "future":
            return
        capped = max(1, min(int(leverage), int(self.max_leverage(symbol))))
        try:
            self._call(self.client.set_leverage, capped, self.resolve_symbol(symbol))
        except ExchangeError as exc:
            raise ExchangeError(f"{symbol} kaldirac ayarlanamadi: {exc}") from exc

    def market_order(self, symbol: str, side: str, amount: float, reduce_only: bool = False):
        """Piyasa emri gonder."""
        params = {"reduceOnly": True} if reduce_only and self.market_type == "future" else {}
        return retry(
            self.client.create_order,
            self.resolve_symbol(symbol), "market", side, amount, None, params,
        )

    def stop_order(self, symbol: str, side: str, amount: float, stop_price: float):
        """Koruyucu stop emri (pozisyonu kapatir)."""
        params = {"stopPrice": self.round_price(symbol, stop_price), "reduceOnly": True}
        return retry(
            self.client.create_order,
            self.resolve_symbol(symbol), "STOP_MARKET", side, amount, None, params,
        )

    def take_profit_order(self, symbol: str, side: str, amount: float, price: float):
        """Kar al emri (pozisyonu kapatir)."""
        params = {"stopPrice": self.round_price(symbol, price), "reduceOnly": True}
        return retry(
            self.client.create_order,
            self.resolve_symbol(symbol), "TAKE_PROFIT_MARKET", side, amount, None, params,
        )

    def cancel_all(self, symbol: str) -> None:
        """Bu paritedeki tum acik emirleri iptal et."""
        try:
            self._call(self.client.cancel_all_orders, self.resolve_symbol(symbol))
        except ExchangeError:
            pass  # acik emir yoksa borsa hata dondurebilir — sorun degil

    def open_position(self, symbol: str) -> dict | None:
        """Borsadaki gercek acik pozisyon (varsa)."""
        if self.market_type != "future":
            return None
        positions = self._call(self.client.fetch_positions, [self.resolve_symbol(symbol)])
        for pos in positions:
            contracts = float(pos.get("contracts") or 0)
            if not math.isclose(contracts, 0.0, abs_tol=1e-12):
                return pos
        return None
