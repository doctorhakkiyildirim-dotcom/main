"""Gecmise donuk test.

Canli botla AYNI strateji, risk ve masraf kodunu kullanir; tek fark verinin
gecmisten gelmesidir. Gelecege bakis yoktur:
  * sinyal KAPANMIS barda uretilir,
  * islem BIR SONRAKI barin ACILISINDA girilir,
  * ayni bar hem stop hem hedefi gorduyse STOP kabul edilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd

from .risk import Position, build_plan, check_exit
from .strategy import build_features, evaluate_row, exit_signal


@dataclass
class BacktestResult:
    """Test sonuclari."""

    trades: list[dict] = field(default_factory=list)
    equity_curve: list[tuple[pd.Timestamp, float]] = field(default_factory=list)
    start_equity: float = 0.0
    end_equity: float = 0.0
    rejected: dict[str, int] = field(default_factory=dict)

    def summary(self) -> dict:
        """Ozet metrikler."""
        n = len(self.trades)
        if n == 0:
            return {"count": 0, "start_equity": self.start_equity,
                    "end_equity": self.end_equity, "rejected": self.rejected}

        nets = np.array([t["net_usd"] for t in self.trades], dtype=float)
        rs = np.array([t["r_multiple"] for t in self.trades], dtype=float)
        wins, losses = nets[nets > 0], nets[nets <= 0]
        gross_win, gross_loss = float(wins.sum()), float(abs(losses.sum()))

        curve = np.array([e for _, e in self.equity_curve], dtype=float)
        if curve.size:
            peak = np.maximum.accumulate(curve)
            drawdown = (peak - curve) / np.where(peak == 0, 1, peak) * 100.0
            max_dd = float(drawdown.max())
        else:
            max_dd = 0.0

        return {
            "count": n,
            "wins": int((nets > 0).sum()),
            "losses": int((nets <= 0).sum()),
            "win_rate": float((nets > 0).mean() * 100.0),
            "net_usd": float(nets.sum()),
            "avg_r": float(rs.mean()),
            "expectancy_usd": float(nets.mean()),
            "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
            "total_fees": float(sum(t["fee_usd"] for t in self.trades)),
            "max_drawdown_pct": max_dd,
            "avg_bars_held": float(np.mean([t["bars_held"] for t in self.trades])),
            "best": float(nets.max()),
            "worst": float(nets.min()),
            "start_equity": self.start_equity,
            "end_equity": self.end_equity,
            "return_pct": (self.end_equity / self.start_equity - 1) * 100.0
            if self.start_equity else 0.0,
            "rejected": self.rejected,
        }


class _SymbolFeed:
    """Tek bir paritenin indikator tablosu ve zaman -> satir indeksi haritasi."""

    def __init__(self, symbol: str, df: pd.DataFrame):
        self.symbol = symbol
        self.df = df.reset_index(drop=True)
        self.index_of = {ts: i for i, ts in enumerate(self.df["ts"])}


def run_backtest(
    frames: dict[str, pd.DataFrame],
    cfg: dict,
    start_equity: float | None = None,
    compound: bool = True,
) -> BacktestResult:
    """Cok pariteli, tek ortak sermayeli portfoy testi.

    `frames`: parite -> `build_features` ciktisi.
    `compound`: True ise pozisyon boyutu buyuyen sermayeye gore hesaplanir.
    """
    equity = float(start_equity if start_equity is not None else cfg["risk"]["equity_usd"])
    result = BacktestResult(start_equity=equity, end_equity=equity)

    feeds = {s: _SymbolFeed(s, df) for s, df in frames.items() if not df.empty}
    if not feeds:
        return result

    all_ts = sorted({ts for feed in feeds.values() for ts in feed.df["ts"]})
    max_open = int(cfg["risk"]["max_open_positions"])
    warmup = int(cfg["strategy"]["ema_trend"]) + 10

    positions: dict[str, Position] = {}
    pending: dict[str, int] = {}   # parite -> sinyal barinin indeksi (girisi sonraki bar acar)
    cooldown_until: dict[str, pd.Timestamp] = {}
    bar_step = pd.Timedelta(hours=_tf_hours(cfg["timeframes"]["signal"]))

    for now in all_ts:
        sizing_equity = equity if compound else result.start_equity

        # 1) Bekleyen girisleri bu barin ACILISINDA uygula
        for symbol, signal_i in list(pending.items()):
            feed = feeds[symbol]
            i = feed.index_of.get(now)
            if i is None:
                continue
            del pending[symbol]
            # Giris SADECE sinyal barindan hemen sonraki barda gecerlidir
            if i != signal_i + 1 or len(positions) >= max_open or symbol in positions:
                continue
            entry_price = float(feed.df.iloc[i]["open"])
            signal = evaluate_row(feed.df.iloc[signal_i], symbol, cfg["strategy"])
            if signal is None:
                continue
            signal.price = entry_price          # giris fiyati: sonraki barin acilisi
            plan = build_plan(signal, sizing_equity, cfg)
            if not plan.ok:
                key = plan.reject_reason.split(":")[0][:60]
                result.rejected[key] = result.rejected.get(key, 0) + 1
                continue
            positions[symbol] = Position.from_plan(plan, str(now), "backtest")

        # 2) Acik pozisyonlari yonet
        for symbol in list(positions):
            feed = feeds[symbol]
            i = feed.index_of.get(now)
            if i is None:
                continue
            pos = positions[symbol]
            row = feed.df.iloc[i]
            high, low, close = float(row["high"]), float(row["low"]), float(row["close"])

            trend_broken = exit_signal(row, pos.side)
            ev = check_exit(pos, high, low, close, cfg, trend_broken)
            if ev is None:
                pos.update_trailing(high, low, float(row["atr"]), cfg)
                pos.bars_held += 1
                continue

            equity += ev.net_usd
            result.trades.append(
                {
                    "symbol": symbol, "side": pos.side, "entry": pos.entry,
                    "exit": ev.price, "leverage": pos.leverage, "notional": pos.notional,
                    "reason": ev.reason, "r_multiple": ev.r_multiple,
                    "gross_usd": ev.gross_usd, "fee_usd": ev.fee_usd,
                    "net_usd": ev.net_usd, "opened_at": pos.opened_at,
                    "closed_at": str(now), "bars_held": pos.bars_held,
                }
            )
            del positions[symbol]
            if ev.net_usd <= 0:
                cooldown_until[symbol] = now + bar_step * cfg["risk"]["cooldown_bars_after_loss"]
            if equity <= 0:
                result.equity_curve.append((now, 0.0))
                result.end_equity = 0.0
                return result

        # 3) Yeni sinyal ara (bir sonraki barin acilisinda girilmek uzere)
        if len(positions) + len(pending) < max_open:
            candidates: list[tuple[float, str, int]] = []
            for symbol, feed in feeds.items():
                if symbol in positions or symbol in pending:
                    continue
                if now < cooldown_until.get(symbol, now):
                    continue
                i = feed.index_of.get(now)
                if i is None or i < warmup or i + 1 >= len(feed.df):
                    continue
                signal = evaluate_row(feed.df.iloc[i], symbol, cfg["strategy"])
                if signal is not None:
                    candidates.append((signal.score, symbol, i))
            candidates.sort(reverse=True)
            for _, symbol, signal_i in candidates[: max_open - len(positions) - len(pending)]:
                pending[symbol] = signal_i

        result.equity_curve.append((now, equity))

    result.end_equity = equity
    return result


def _tf_hours(timeframe: str) -> float:
    """Zaman dilimini saate cevir (backtest bekleme suresi icin)."""
    from .exchange import timeframe_hours

    return timeframe_hours(timeframe)


def prepare_frames(
    ex, symbols: Iterable[str], cfg: dict, bars: int
) -> dict[str, pd.DataFrame]:
    """Borsadan veri cekip indikator tablolarini kur."""
    tf = cfg["timeframes"]
    htf_bars = max(200, int(bars * _tf_hours(tf["signal"]) / _tf_hours(tf["trend"])) + 250)
    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        ltf = ex.fetch_ohlcv(symbol, tf["signal"], bars)
        htf = ex.fetch_ohlcv(symbol, tf["trend"], htf_bars)
        out[symbol] = build_features(ltf, htf, cfg["strategy"])
    return out
