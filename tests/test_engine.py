"""Motor testleri — sahte bir borsayla tum dongu (tarama -> acilis -> takip -> kapanis)."""

import copy

import pytest

from bot.config import DEFAULTS
from bot.datafeed import resample, synthetic
from bot.engine import Engine
from bot.notifier import Notifier


class FakeExchange:
    """Ag olmadan calisan, sentetik veri servis eden sahte borsa."""

    def __init__(self, symbols, bars=1200, timeframe="4h", trend_tf="1d"):
        self.data = {}
        for i, sym in enumerate(symbols):
            ltf = synthetic(bars=bars, timeframe=timeframe, seed=21 + i)
            ltf["closed"] = True
            htf = resample(ltf, trend_tf)
            htf["closed"] = True
            self.data[sym] = {timeframe: ltf, trend_tf: htf}
        self.cursor = 400          # gorunur bar sayisi
        self.orders = []

    def _visible(self, symbol, timeframe):
        df = self.data[symbol][timeframe]
        if timeframe == "1d":
            cutoff = self.data[symbol]["4h"]["close_time"].iloc[self.cursor - 1]
            return df[df["close_time"] <= cutoff].reset_index(drop=True)
        return df.iloc[: self.cursor].reset_index(drop=True)

    def advance(self, bars=1):
        self.cursor += bars

    # --- Engine'in kullandigi arayuz ------------------------------------
    def discover_universe(self):
        return list(self.data)

    def fetch_ohlcv(self, symbol, timeframe, limit):
        return self._visible(symbol, timeframe).tail(limit).reset_index(drop=True)

    def max_leverage(self, symbol):
        return 20.0

    def min_notional(self, symbol):
        return 5.0

    def last_price(self, symbol):
        return float(self._visible(symbol, "4h")["close"].iloc[-1])

    def fetch_equity(self):
        return 1000.0

    def open_position(self, symbol):
        return None


@pytest.fixture
def cfg(tmp_path):
    c = copy.deepcopy(DEFAULTS)
    c["state_file"] = str(tmp_path / "state.json")
    c["notify"]["logfile"] = str(tmp_path / "log.txt")
    c["notify"]["sound"] = False
    c["notify"]["console"] = False
    c["execution"]["mode"] = "paper"
    c["fees"]["flat_fee_usd"] = 0.0
    c["risk"]["equity_usd"] = 1000.0
    return c


def build(cfg, symbols=("AAA/USDT", "BBB/USDT", "CCC/USDT")):
    ex = FakeExchange(list(symbols))
    return Engine(cfg, exchange=ex, notifier=Notifier(cfg)), ex


def run_until_trade(engine, ex, max_steps=600):
    for _ in range(max_steps):
        engine.tick()
        if engine.state.data["positions"] or engine.state.data["closed_trades"]:
            return True
        ex.advance()
    return False


def test_engine_opens_a_position_in_paper_mode(cfg):
    engine, ex = build(cfg)
    assert run_until_trade(engine, ex), "sentetik veride hic islem acilmadi"


def test_position_is_persisted_and_reloaded(cfg):
    engine, ex = build(cfg)
    assert run_until_trade(engine, ex)
    if not engine.state.data["positions"]:
        pytest.skip("islem ayni adimda kapandi")

    symbol = next(iter(engine.state.data["positions"]))
    stop = engine.state.get_position(symbol).stop

    fresh, _ = build(cfg)
    reloaded = fresh.state.get_position(symbol)
    assert reloaded is not None and reloaded.stop == stop


def test_open_positions_never_exceed_the_limit(cfg):
    cfg["risk"]["max_open_positions"] = 1
    engine, ex = build(cfg)
    for _ in range(400):
        engine.tick()
        assert len(engine.state.data["positions"]) <= 1
        ex.advance()


def test_signal_mode_alerts_without_opening_positions(cfg):
    cfg["execution"]["mode"] = "signal"
    engine, ex = build(cfg)
    for _ in range(400):
        engine.tick()
        ex.advance()
    assert engine.state.data["positions"] == {}
    assert engine.state.data["closed_trades"] == []


def test_the_same_bar_never_fires_twice(cfg):
    engine, ex = build(cfg)
    plans = engine.scan(skip_seen=True)
    if not plans:
        pytest.skip("bu adimda sinyal yok")
    symbol = plans[0][0].symbol
    engine._mark_bar(symbol, plans[0][1]["ts"].iloc[-1])
    again = [p for p, _ in engine.scan(skip_seen=True)]
    assert symbol not in [p.symbol for p in again]


def test_daily_loss_limit_halts_new_entries(cfg):
    cfg["risk"]["daily_max_loss_pct"] = 0.001
    engine, ex = build(cfg)
    for _ in range(600):
        engine.tick()
        if engine.state.data["closed_trades"]:
            break
        ex.advance()
    if not engine.state.data["closed_trades"]:
        pytest.skip("test suresinde islem kapanmadi")
    if engine.state.day_pnl() < 0:
        assert engine.state.halted


def test_trailing_stop_only_improves_over_the_life_of_a_trade(cfg):
    engine, ex = build(cfg)
    seen: dict[str, list[float]] = {}
    for _ in range(500):
        engine.tick()
        for symbol, pos in engine.state.positions().items():
            key = f"{symbol}|{pos.opened_at}"
            history = seen.setdefault(key, [])
            if history:
                moved = (pos.stop - history[-1]) * pos.direction
                assert moved >= -1e-9, f"{symbol}: stop geri gitti"
            history.append(pos.stop)
        ex.advance()
    assert seen, "hic pozisyon izlenmedi"


def test_closed_trades_balance_the_equity(cfg):
    engine, ex = build(cfg)
    for _ in range(600):
        engine.tick()
        ex.advance()
    trades = engine.state.data["closed_trades"]
    if not trades:
        pytest.skip("islem kapanmadi")
    expected = 1000.0 + sum(t["net_usd"] for t in trades)
    assert engine.state.equity == pytest.approx(expected, rel=1e-9)
