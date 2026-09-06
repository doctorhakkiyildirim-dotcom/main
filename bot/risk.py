"""Risk yonetimi: pozisyon boyutu, kaldirac, stop-loss, kar al ve iz suren stop.

Buradaki tek kural: HER islemin, daha acilmadan once bilinen bir stop'u ve
bir kar hedefi vardir. Kaldirac keyfi secilmez — stop mesafesinden ve
likidasyon guvenlik payindan HESAPLANIR.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any

from .strategy import LONG, Signal


@dataclass
class TradePlan:
    """Acilmadan once hesaplanmis, tam tanimli islem plani."""

    symbol: str
    side: str
    score: float
    entry: float
    stop: float
    take_profit: float
    stop_pct: float
    tp_pct: float
    qty: float
    notional: float
    leverage: int
    margin: float
    risk_usd: float
    risk_pct: float
    liq_price: float
    fee_total: float
    expected_gross: float
    expected_net: float
    fee_ratio: float
    rr: float
    ok: bool = True
    reject_reason: str = ""
    warnings: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def side_tr(self) -> str:
        """Ekranda gosterilecek yon."""
        return "ALIS (LONG)" if self.side == LONG else "SATIS (SHORT)"

    def to_dict(self) -> dict[str, Any]:
        """Kayit/log icin sozluk."""
        return asdict(self)


def round_fees(notional: float, fees: dict) -> float:
    """Gidis-donus toplam masraf: komisyon + sabit ucret + kayma."""
    pct_fee = notional * fees["taker_pct"] / 100.0
    one_way = max(pct_fee, float(fees["flat_fee_usd"]))
    slippage = notional * fees["slippage_pct"] / 100.0
    return 2.0 * (one_way + slippage)


def min_notional_for_fee_ratio(stop_pct: float, tp_r: float, fees: dict) -> float:
    """Masraf/kar orani kurali icin gereken en kucuk pozisyon buyuklugu (USDT).

    Komisyon `max(yuzdesel, sabit)` seklinde alindigi icin iki rejim vardir:
      A) Kucuk pozisyonlarda sabit ucret baskindir -> masrafin ORANI dusmesi
         icin pozisyonun buyumesi gerekir.
      B) Buyuk pozisyonlarda yuzdesel komisyon baskindir -> oran artik
         buyuklukten bagimsizdir; ya bastan saglanir ya hic saglanmaz.
    Saglanamiyorsa sonsuz doner.
    """
    tp_frac = stop_pct / 100.0 * tp_r
    ratio = fees["min_profit_to_fee_ratio"]
    taker = fees["taker_pct"] / 100.0
    slip = fees["slippage_pct"] / 100.0
    flat = float(fees["flat_fee_usd"])

    # Rejim B kosulu: yuzdesel masraflar tek basina hedefi yemiyor mu?
    regime_b_ok = tp_frac >= ratio * 2.0 * (taker + slip)

    if flat <= 0:
        return 0.0 if regime_b_ok else math.inf

    # Iki rejimin sinir buyuklugu: yuzdesel komisyon sabit ucrete esitlendigi yer
    boundary = flat / taker if taker > 0 else math.inf

    denom = tp_frac - ratio * 2.0 * slip
    if denom > 0:
        needed = ratio * 2.0 * flat / denom
        if needed <= boundary:
            return needed

    return boundary if regime_b_ok else math.inf


def build_plan(
    signal: Signal,
    equity: float,
    cfg: dict,
    exchange_max_leverage: float = 20.0,
    exchange_min_notional: float = 0.0,
) -> TradePlan:
    """Sinyali, uygulanabilir bir islem planina cevir.

    Adimlar: stop mesafesi -> risk butcesi -> pozisyon buyuklugu ->
    likidasyon guvenligine gore kaldirac -> masraf kontrolu.
    """
    risk_cfg, fees, exec_cfg = cfg["risk"], cfg["fees"], cfg["execution"]
    entry = signal.price
    direction = signal.direction

    # 1) Stop mesafesi: ATR tabanli, alt/ust sinirlarla budanmis
    raw_stop_pct = risk_cfg["atr_stop_mult"] * signal.atr / entry * 100.0
    stop_pct = min(max(raw_stop_pct, risk_cfg["min_stop_pct"]), risk_cfg["max_stop_pct"])
    stop_frac = stop_pct / 100.0
    stop_price = entry * (1 - direction * stop_frac)

    # 2) Kar hedefi: riskin `take_profit_r` kati
    tp_r = risk_cfg["take_profit_r"]
    tp_pct = stop_pct * tp_r
    tp_price = entry * (1 + direction * tp_pct / 100.0)

    def reject(reason: str, **over: Any) -> TradePlan:
        base = dict(
            symbol=signal.symbol, side=signal.side, score=signal.score, entry=entry,
            stop=stop_price, take_profit=tp_price, stop_pct=stop_pct, tp_pct=tp_pct,
            qty=0.0, notional=0.0, leverage=1, margin=0.0, risk_usd=0.0, risk_pct=0.0,
            liq_price=0.0, fee_total=0.0, expected_gross=0.0, expected_net=0.0,
            fee_ratio=0.0, rr=tp_r, ok=False, reject_reason=reason,
            reasons=list(signal.reasons),
        )
        base.update(over)
        return TradePlan(**base)

    # 3) Risk butcesi ve buna karsilik gelen pozisyon buyuklugu
    risk_usd_target = equity * risk_cfg["risk_per_trade_pct"] / 100.0
    notional_by_risk = risk_usd_target / stop_frac

    # 4) Kaldirac tavani: config, borsa ve LIKIDASYON guvenlik payinin en dusugu.
    #    Kabaca likidasyon, fiyat teminatin tamamini yiyecek kadar (1/kaldirac)
    #    ters gittiginde olur; stop bundan cok once tetiklenmeli.
    lev_liq = risk_cfg["liquidation_buffer"] / stop_frac
    lev_cap = min(float(risk_cfg["max_leverage"]), float(exchange_max_leverage), lev_liq)
    if cfg["exchange"]["market_type"] != "future":
        lev_cap = 1.0
    if lev_cap < 1.0:
        return reject(
            f"Stop mesafesi (%{stop_pct:.2f}) likidasyon guvenligi icin fazla genis: "
            f"1x kaldiracta bile guvenli degil."
        )

    margin_budget = equity * risk_cfg["margin_alloc_pct"] / 100.0
    max_notional = margin_budget * lev_cap
    notional = min(notional_by_risk, max_notional)

    # 5) Borsanin en kucuk pozisyon sartina uy (kucuk hesaplarda kritik)
    warnings: list[str] = []
    floor_notional = max(float(exec_cfg["min_notional_usd"]), float(exchange_min_notional))
    if notional < floor_notional:
        if floor_notional > max_notional:
            return reject(
                f"Sermaye yetersiz: borsa en az {floor_notional:.2f} USDT'lik pozisyon "
                f"istiyor, guvenli tavan {max_notional:.2f} USDT."
            )
        notional = floor_notional
        warnings.append(
            f"Pozisyon, borsanin {floor_notional:.2f} USDT alt sinirina yukseltildi — "
            f"islem riski hedeflenenden yuksek."
        )

    # 6) Kaldirac: gereken buyuklugu teminat butcesinden cikar
    leverage = max(1, math.ceil(notional / margin_budget - 1e-9))
    if leverage > lev_cap:
        return reject(
            f"Gereken kaldirac {leverage}x, guvenli tavan {lev_cap:.1f}x "
            f"(stop %{stop_pct:.2f} genisliginde)."
        )
    margin = notional / leverage
    qty = notional / entry

    # 7) Gerceklesen risk (yukari yuvarlamalar sonrasi)
    risk_usd = notional * stop_frac
    risk_pct = risk_usd / equity * 100.0
    if risk_pct > risk_cfg["risk_per_trade_pct"] * 1.5 + 1e-9:
        warnings.append(
            f"Gercek risk %{risk_pct:.1f} — hedefin "
            f"(%{risk_cfg['risk_per_trade_pct']:.1f}) uzerinde."
        )

    # 8) Masraf kontrolu — uzun vadeli calismanin asil sebebi
    fee_total = round_fees(notional, fees)
    expected_gross = notional * tp_pct / 100.0
    expected_net = expected_gross - fee_total
    fee_ratio = expected_gross / fee_total if fee_total > 0 else math.inf

    if fee_ratio < fees["min_profit_to_fee_ratio"]:
        needed = min_notional_for_fee_ratio(stop_pct, tp_r, fees)
        hint = (
            f" Bu ucret yapisiyla en az ~{needed:.0f} USDT'lik pozisyon gerekir."
            if math.isfinite(needed) and needed > 0
            else " Yuzdesel komisyon+kayma tek basina hedefi yiyor; hedefi buyut."
        )
        return reject(
            f"Masraf cok agir: beklenen brut kar {expected_gross:.2f} USDT, "
            f"toplam masraf {fee_total:.2f} USDT (oran {fee_ratio:.2f}x, "
            f"gereken {fees['min_profit_to_fee_ratio']:.1f}x).{hint}",
            notional=notional, qty=qty, leverage=leverage, margin=margin,
            risk_usd=risk_usd, risk_pct=risk_pct, fee_total=fee_total,
            expected_gross=expected_gross, expected_net=expected_net, fee_ratio=fee_ratio,
        )

    liq_price = entry * (1 - direction / leverage)

    return TradePlan(
        symbol=signal.symbol, side=signal.side, score=signal.score, entry=entry,
        stop=stop_price, take_profit=tp_price, stop_pct=stop_pct, tp_pct=tp_pct,
        qty=qty, notional=notional, leverage=leverage, margin=margin,
        risk_usd=risk_usd, risk_pct=risk_pct, liq_price=liq_price,
        fee_total=fee_total, expected_gross=expected_gross, expected_net=expected_net,
        fee_ratio=fee_ratio, rr=tp_r, ok=True,
        warnings=warnings, reasons=list(signal.reasons),
    )


@dataclass
class Position:
    """Acik pozisyon ve iz suren stop durumu."""

    symbol: str
    side: str
    entry: float
    qty: float
    notional: float
    leverage: int
    margin: float
    initial_stop: float
    stop: float
    take_profit: float
    risk_per_unit: float
    opened_at: str
    opened_bar: str
    bars_held: int = 0
    peak: float = 0.0          # LONG icin en yuksek, SHORT icin en dusuk fiyat
    breakeven_done: bool = False
    trailing_active: bool = False
    entry_fee: float = 0.0
    mode: str = "paper"
    stop_order_id: str = ""
    tp_order_id: str = ""

    @property
    def direction(self) -> int:
        """LONG icin +1, SHORT icin -1."""
        return 1 if self.side == LONG else -1

    @property
    def side_tr(self) -> str:
        """Ekranda gosterilecek yon."""
        return "ALIS (LONG)" if self.side == LONG else "SATIS (SHORT)"

    def to_dict(self) -> dict[str, Any]:
        """Diske yazmak icin sozluk."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        """Diskten okunan sozlukten pozisyon kur."""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_plan(cls, plan: TradePlan, bar_time: str, mode: str) -> "Position":
        """Plandan pozisyon olustur."""
        fees_one_way = plan.fee_total / 2.0
        return cls(
            symbol=plan.symbol, side=plan.side, entry=plan.entry, qty=plan.qty,
            notional=plan.notional, leverage=plan.leverage, margin=plan.margin,
            initial_stop=plan.stop, stop=plan.stop, take_profit=plan.take_profit,
            risk_per_unit=abs(plan.entry - plan.stop),
            opened_at=bar_time, opened_bar=bar_time, peak=plan.entry,
            entry_fee=fees_one_way, mode=mode,
        )

    # ------------------------------------------------------------ olcumler

    def r_multiple(self, price: float) -> float:
        """Fiyatin kac 'R' (baslangic riski) kadar lehimize gittigi."""
        if self.risk_per_unit <= 0:
            return 0.0
        return self.direction * (price - self.entry) / self.risk_per_unit

    def unrealized_usd(self, price: float) -> float:
        """Anlik (komisyonsuz) kar/zarar, USDT."""
        return self.direction * (price - self.entry) * self.qty

    def breakeven_price(self, fees: dict) -> float:
        """Masraflar dahil basa bas fiyati."""
        cost_frac = round_fees(self.notional, fees) / max(self.notional, 1e-9)
        return self.entry * (1 + self.direction * cost_frac)

    # ------------------------------------------------------------ guncelleme

    def update_trailing(
        self, bar_high: float, bar_low: float, atr_value: float, cfg: dict
    ) -> list[str]:
        """Stop'u yalnizca LEHIMIZE olacak sekilde yukselt/indir.

        Once 1R'de basa bas, sonra ATR'ye dayali iz suren stop (chandelier).
        Stop asla geri gitmez — kardan zarara donmeyi bu engeller.
        """
        risk_cfg, fees = cfg["risk"], cfg["fees"]
        notes: list[str] = []

        favourable = bar_high if self.side == LONG else bar_low
        self.peak = (
            max(self.peak, favourable) if self.side == LONG else min(self.peak, favourable)
        )
        peak_r = self.r_multiple(self.peak)

        if not self.breakeven_done and peak_r >= risk_cfg["breakeven_at_r"]:
            be = self.breakeven_price(fees)
            if self.direction * (be - self.stop) > 0:
                self.stop = be
                notes.append(f"Stop basa bas seviyesine cekildi ({be:.6g}) — artik zarar yok")
            self.breakeven_done = True

        if peak_r >= risk_cfg["trail_start_r"] and atr_value > 0:
            trail = self.peak - self.direction * risk_cfg["trail_atr_mult"] * atr_value
            if self.direction * (trail - self.stop) > 0:
                old = self.stop
                self.stop = trail
                self.trailing_active = True
                notes.append(
                    f"Iz suren stop {old:.6g} -> {trail:.6g} "
                    f"(kilitlenen kar {self.r_multiple(trail):+.2f}R)"
                )
        return notes

    def locked_r(self) -> float:
        """Stop su an nerede tetiklenirse kac R kar/zarar kalir."""
        return self.r_multiple(self.stop)


@dataclass
class ExitEvent:
    """Pozisyon kapanisi."""

    reason: str
    price: float
    r_multiple: float
    gross_usd: float
    fee_usd: float
    net_usd: float


def check_exit(
    pos: Position, bar_high: float, bar_low: float, bar_close: float, cfg: dict,
    trend_broken: str | None = None,
) -> ExitEvent | None:
    """Bu barda pozisyon kapaniyor mu?

    Ayni bar hem stop hem hedefi gorduyse, KOTUMSER davranip stop kabul edilir.
    """
    risk_cfg, fees = cfg["risk"], cfg["fees"]

    if pos.side == LONG:
        hit_stop, hit_tp = bar_low <= pos.stop, bar_high >= pos.take_profit
    else:
        hit_stop, hit_tp = bar_high >= pos.stop, bar_low <= pos.take_profit

    price: float | None = None
    reason = ""
    if hit_stop:
        price, reason = pos.stop, ("Iz suren stop" if pos.trailing_active else "Stop-loss")
        if pos.breakeven_done and pos.locked_r() >= 0:
            reason = "Kar korumali stop"
    elif hit_tp:
        price, reason = pos.take_profit, "Kar al hedefi"
    elif trend_broken:
        price, reason = bar_close, trend_broken
    elif (
        pos.bars_held >= risk_cfg["time_stop_bars"]
        and pos.r_multiple(bar_close) < risk_cfg["time_stop_min_r"]
    ):
        price, reason = bar_close, (
            f"Sure doldu ({pos.bars_held} bar) ve islem ilerlemiyor"
        )

    if price is None:
        return None

    gross = pos.direction * (price - pos.entry) * pos.qty
    fee = round_fees(pos.notional, fees)
    return ExitEvent(
        reason=reason, price=price, r_multiple=pos.r_multiple(price),
        gross_usd=gross, fee_usd=fee, net_usd=gross - fee,
    )
