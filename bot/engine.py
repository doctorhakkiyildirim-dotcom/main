"""Bot motoru: tara -> sinyal uret -> pozisyon ac -> stop'u takip et -> kapat.

Tum kararlar KAPANMIS bar uzerinde alinir. Ayni bar iki kez islenmez;
bot yeniden baslatilsa bile durum dosyasindan kaldigi yerden devam eder.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from .exchange import Exchange, ExchangeError, timeframe_hours
from .notifier import Notifier
from .risk import ExitEvent, Position, TradePlan, build_plan, check_exit, round_fees
from .state import State, utcnow_iso
from .strategy import LONG, build_features, evaluate_row, exit_signal, last_closed_index


class Engine:
    """Stratejiyi, riski, borsayi ve durumu bir araya getiren orkestrator."""

    def __init__(self, cfg: dict, exchange: Exchange | None = None,
                 notifier: Notifier | None = None):
        self.cfg = cfg
        self.mode = cfg["execution"]["mode"]
        self.ex = exchange or Exchange(cfg)
        self.note = notifier or Notifier(cfg)
        self.state = State(cfg["state_file"])
        self.state.ensure_equity(cfg["risk"]["equity_usd"])
        self._universe: list[str] = []
        self._universe_ts: float = 0.0

    # ------------------------------------------------------------- yardimci

    def universe(self, max_age_s: int = 3600) -> list[str]:
        """Taranacak parite listesi (saatte bir tazelenir)."""
        if not self._universe or time.time() - self._universe_ts > max_age_s:
            self._universe = self.ex.discover_universe()
            self._universe_ts = time.time()
            self.note.info(f"Tarama listesi: {len(self._universe)} parite")
        return self._universe

    def load_frames(self, symbol: str) -> pd.DataFrame | None:
        """Sinyal ve trend zaman dilimlerini cek, indikatorleri hesapla."""
        tf = self.cfg["timeframes"]
        try:
            ltf = self.ex.fetch_ohlcv(symbol, tf["signal"], tf["history_bars"])
            htf = self.ex.fetch_ohlcv(symbol, tf["trend"], tf["history_bars"])
        except ExchangeError as exc:
            self.note.warn(f"{symbol} verisi alinamadi: {exc}")
            return None
        if len(ltf) < self.cfg["strategy"]["ema_trend"] + 10:
            return None
        return build_features(ltf, htf, self.cfg["strategy"])

    def _equity(self) -> float:
        """Kullanilacak sermaye: live'da borsadan, digerlerinde durum dosyasindan."""
        if self.mode == "live":
            try:
                balance = self.ex.fetch_equity()
                if balance > 0:
                    self.state.equity = balance
            except ExchangeError as exc:
                self.note.warn(f"Bakiye okunamadi, kayitli deger kullaniliyor: {exc}")
        return self.state.equity

    def _bar_key(self, symbol: str) -> str:
        return f"{symbol}|{self.cfg['timeframes']['signal']}"

    def _seen_bar(self, symbol: str, bar_time: pd.Timestamp) -> bool:
        """Bu bar daha once islendi mi (ayni sinyali tekrarlamamak icin)."""
        seen = self.state.data.setdefault("last_bar", {})
        return seen.get(self._bar_key(symbol)) == bar_time.isoformat()

    def _mark_bar(self, symbol: str, bar_time: pd.Timestamp) -> None:
        self.state.data.setdefault("last_bar", {})[self._bar_key(symbol)] = (
            bar_time.isoformat()
        )

    # -------------------------------------------------------------- tarama

    def scan(self, symbols: list[str] | None = None,
             skip_seen: bool = False) -> list[tuple[TradePlan, pd.DataFrame]]:
        """Tum evreni tara, gecerli islem planlarini puana gore sirali dondur."""
        equity = self._equity()
        results: list[tuple[TradePlan, pd.DataFrame]] = []
        open_symbols = set(self.state.data["positions"])

        for symbol in symbols if symbols is not None else self.universe():
            if symbol in open_symbols:
                continue
            df = self.load_frames(symbol)
            if df is None:
                continue

            idx = last_closed_index(df, self.cfg["execution"]["confirm_on_closed_bar"])
            if idx is None:
                continue
            row = df.iloc[idx]

            signal = evaluate_row(row, symbol, self.cfg["strategy"])
            if signal is None:
                continue
            if skip_seen and self._seen_bar(symbol, row["ts"]):
                continue
            if self.state.in_cooldown(symbol, utcnow_iso()):
                self.note.rejected(symbol, "zararli islem sonrasi bekleme suresinde")
                continue

            plan = build_plan(
                signal, equity, self.cfg,
                exchange_max_leverage=self.ex.max_leverage(symbol),
                exchange_min_notional=self.ex.min_notional(symbol),
            )
            if not plan.ok:
                self.note.rejected(symbol, plan.reject_reason)
                continue
            results.append((plan, df))

        results.sort(key=lambda item: item[0].score, reverse=True)
        return results

    # ------------------------------------------------------------ pozisyon

    def open_position(self, plan: TradePlan, bar_time: str) -> Position | None:
        """Plani uygula: paper modda sanal, live modda gercek emir."""
        pos = Position.from_plan(plan, bar_time, self.mode)

        if self.mode == "live":
            try:
                self.ex.set_leverage(plan.symbol, plan.leverage)
                qty = self.ex.round_amount(plan.symbol, plan.qty)
                if qty <= 0:
                    self.note.warn(f"{plan.symbol}: miktar borsanin adimina yuvarlaninca 0 oldu.")
                    return None
                side = "buy" if plan.side == LONG else "sell"
                order = self.ex.market_order(plan.symbol, side, qty)
                filled = float(order.get("average") or order.get("price") or plan.entry)

                # Gerceklesen fiyata gore stop/hedefi yeniden hizala
                drift = filled / plan.entry
                pos.entry = filled
                pos.qty = qty
                pos.notional = filled * qty
                pos.stop = plan.stop * drift
                pos.initial_stop = pos.stop
                pos.take_profit = plan.take_profit * drift
                pos.risk_per_unit = abs(pos.entry - pos.stop)
                pos.peak = filled

                close_side = "sell" if plan.side == LONG else "buy"
                stop_order = self.ex.stop_order(plan.symbol, close_side, qty, pos.stop)
                tp_order = self.ex.take_profit_order(plan.symbol, close_side, qty, pos.take_profit)
                pos.stop_order_id = str(stop_order.get("id") or "")
                pos.tp_order_id = str(tp_order.get("id") or "")
            except ExchangeError as exc:
                self.note.error(f"{plan.symbol} emri gonderilemedi: {exc}")
                return None

        self.state.put_position(pos)
        self.state.save()
        self.note.opened(pos)
        return pos

    def close_position(self, pos: Position, ev: ExitEvent) -> None:
        """Pozisyonu kapat, gecmise yaz, gerekirse bekleme suresi koy."""
        if self.mode == "live":
            try:
                self.ex.cancel_all(pos.symbol)
                close_side = "sell" if pos.side == LONG else "buy"
                self.ex.market_order(pos.symbol, close_side, pos.qty, reduce_only=True)
            except ExchangeError as exc:
                self.note.error(
                    f"{pos.symbol} kapatilamadi: {exc} — BORSADAN ELLE KAPAT."
                )

        self.state.record_trade(pos, ev, utcnow_iso())
        self.state.drop_position(pos.symbol)

        if ev.net_usd <= 0:
            hours = timeframe_hours(self.cfg["timeframes"]["signal"])
            until = datetime.now(timezone.utc) + timedelta(
                hours=hours * self.cfg["risk"]["cooldown_bars_after_loss"]
            )
            self.state.set_cooldown(pos.symbol, until.isoformat(timespec="seconds"))

        self.state.save()
        self.note.closed(pos, ev)
        self._check_daily_limit()

    def _check_daily_limit(self) -> None:
        """Gunluk zarar siniri asildiysa yeni islem acmayi durdur."""
        limit = self.state.data["start_equity"] * self.cfg["risk"]["daily_max_loss_pct"] / 100.0
        if -self.state.day_pnl() >= limit and not self.state.halted:
            self.state.halt(
                f"Gunluk zarar siniri asildi ({self.state.day_pnl():+.2f} USDT). "
                f"Yeni islem acilmayacak — 'resume' komutuyla devam ettir."
            )
            self.state.save()
            self.note.warn(self.state.data["halt_reason"])

    # ------------------------------------------------- acik pozisyon takibi

    def manage_positions(self) -> None:
        """Her acik pozisyon icin stop'u guncelle ve cikis kosullarini kontrol et."""
        for symbol, pos in self.state.positions().items():
            df = self.load_frames(symbol)
            if df is None:
                continue
            idx = last_closed_index(df, True)
            if idx is None:
                continue
            row = df.iloc[idx]

            # Live modda borsa stop/hedefi zaten doldurmus olabilir
            if self.mode == "live" and self._reconcile_live(pos, float(row["close"])):
                continue

            new_bar = not self._seen_bar(symbol, row["ts"])
            if new_bar:
                pos.bars_held += 1
                # Bari hemen isaretle: pozisyon bu barda kapansa bile ayni bar
                # icinde yeni bir giris sinyali uretilmesin.
                self._mark_bar(symbol, row["ts"])

            notes = pos.update_trailing(
                float(row["high"]), float(row["low"]), float(row["atr"]), self.cfg
            )
            if notes:
                self.note.stop_moved(pos, notes)
                if self.mode == "live":
                    self._replace_stop(pos)

            trend_broken = exit_signal(row, pos.side)
            ev = check_exit(
                pos, float(row["high"]), float(row["low"]), float(row["close"]),
                self.cfg, trend_broken,
            )
            if ev:
                self.close_position(pos, ev)
            else:
                self.state.put_position(pos)
                self.state.save()

    def _reconcile_live(self, pos: Position, price: float) -> bool:
        """Borsada pozisyon kalmadiysa kaydi kapat (stop/hedef dolmus demektir)."""
        try:
            live_pos = self.ex.open_position(pos.symbol)
        except ExchangeError as exc:
            self.note.warn(f"{pos.symbol} pozisyon durumu okunamadi: {exc}")
            return False
        if live_pos is not None:
            return False

        gross = pos.direction * (price - pos.entry) * pos.qty
        fee = round_fees(pos.notional, self.cfg["fees"])
        self.close_position(
            pos,
            ExitEvent(
                reason="Borsada kapandi (stop veya hedef emri doldu)", price=price,
                r_multiple=pos.r_multiple(price), gross_usd=gross, fee_usd=fee,
                net_usd=gross - fee,
            ),
        )
        return True

    def _replace_stop(self, pos: Position) -> None:
        """Iz suren stop degistiginde borsadaki stop emrini yenile."""
        try:
            self.ex.cancel_all(pos.symbol)
            close_side = "sell" if pos.side == LONG else "buy"
            stop_order = self.ex.stop_order(pos.symbol, close_side, pos.qty, pos.stop)
            tp_order = self.ex.take_profit_order(
                pos.symbol, close_side, pos.qty, pos.take_profit
            )
            pos.stop_order_id = str(stop_order.get("id") or "")
            pos.tp_order_id = str(tp_order.get("id") or "")
        except ExchangeError as exc:
            self.note.error(
                f"{pos.symbol} stop emri guncellenemedi: {exc} — borsadan kontrol et."
            )

    # ------------------------------------------------------------- dongu

    def tick(self) -> None:
        """Bir tam dongu: once acik pozisyonlar, sonra yeni firsatlar."""
        self.manage_positions()

        if self.state.halted:
            self.note.info(f"Bot durduruldu: {self.state.data['halt_reason']}")
            return

        free_slots = self.cfg["risk"]["max_open_positions"] - len(self.state.data["positions"])
        if free_slots <= 0:
            self.note.info("Pozisyon limiti dolu — yeni islem aranmiyor.")
            return

        for plan, df in self.scan(skip_seen=True):
            if free_slots <= 0:
                break
            self.note.signal(plan)
            idx = last_closed_index(df, self.cfg["execution"]["confirm_on_closed_bar"])
            bar_time = df.iloc[idx]["ts"]
            self._mark_bar(plan.symbol, bar_time)

            if self.mode == "signal":
                self.state.save()
                continue
            if self.open_position(plan, bar_time.isoformat()):
                free_slots -= 1

        self.state.save()

    def run_forever(self) -> None:
        """Surekli calisma modu — CMD'de acik birakilir."""
        poll = int(self.cfg["execution"]["poll_seconds"])
        self.note.banner(
            [f"{len(self.universe())} parite taranacak", f"Dongu araligi: {poll} saniye",
             "Durdurmak icin Ctrl+C"]
        )
        while True:
            started = time.time()
            try:
                self.note.info(f"--- Tarama: {utcnow_iso()} ---")
                self.tick()
            except KeyboardInterrupt:
                raise
            except ExchangeError as exc:
                self.note.error(f"Borsa hatasi: {exc}")
            except Exception as exc:  # dongu hicbir hatada olmemeli
                self.note.error(f"Beklenmeyen hata: {type(exc).__name__}: {exc}")
            time.sleep(max(5.0, poll - (time.time() - started)))
