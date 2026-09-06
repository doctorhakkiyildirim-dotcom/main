"""Kalici durum: acik pozisyonlar, kapanmis islemler, gunluk zarar takibi.

Durum JSON olarak diske yazilir; bot kapanip acilsa bile pozisyonlarini
ve iz suren stop seviyelerini unutmaz.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .risk import Position


def utcnow_iso() -> str:
    """Su anki UTC zamani ISO metin olarak."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class State:
    """Bot durumunun diskteki temsili."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict[str, Any] = {
            "created_at": utcnow_iso(),
            "equity": None,
            "start_equity": None,
            "positions": {},
            "closed_trades": [],
            "cooldown": {},
            "daily": {},
            "halted": False,
            "halt_reason": "",
        }
        self.load()

    # ------------------------------------------------------------------ I/O

    def load(self) -> None:
        """Diskten oku (yoksa varsayilan bos durum)."""
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                self.data.update(json.load(fh))
        except (json.JSONDecodeError, OSError) as exc:
            backup = self.path.with_suffix(".corrupt")
            self.path.replace(backup)
            raise RuntimeError(
                f"Durum dosyasi bozuk ({exc}); '{backup}' olarak yedeklendi. "
                f"Bot temiz durumla devam edecek."
            ) from exc

    def save(self) -> None:
        """Atomik yaz — yazma sirasinda bot kapansa bile dosya bozulmaz."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2, ensure_ascii=False, default=str)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------- sermaye

    def ensure_equity(self, equity: float) -> None:
        """Ilk calistirmada baslangic sermayesini kaydet."""
        if self.data.get("equity") is None:
            self.data["equity"] = float(equity)
            self.data["start_equity"] = float(equity)

    @property
    def equity(self) -> float:
        """Guncel sermaye."""
        return float(self.data.get("equity") or 0.0)

    @equity.setter
    def equity(self, value: float) -> None:
        self.data["equity"] = float(value)

    # ----------------------------------------------------------- pozisyonlar

    def positions(self) -> dict[str, Position]:
        """Acik pozisyonlar."""
        return {s: Position.from_dict(d) for s, d in self.data["positions"].items()}

    def get_position(self, symbol: str) -> Position | None:
        """Belirli bir paritedeki acik pozisyon."""
        raw = self.data["positions"].get(symbol)
        return Position.from_dict(raw) if raw else None

    def put_position(self, pos: Position) -> None:
        """Pozisyonu kaydet/guncelle."""
        self.data["positions"][pos.symbol] = pos.to_dict()

    def drop_position(self, symbol: str) -> None:
        """Pozisyonu kaldir."""
        self.data["positions"].pop(symbol, None)

    # -------------------------------------------------------------- islemler

    def record_trade(self, pos: Position, exit_event, closed_at: str) -> None:
        """Kapanan islemi gecmise yaz ve sermayeyi guncelle."""
        self.data["closed_trades"].append(
            {
                "symbol": pos.symbol,
                "side": pos.side,
                "entry": pos.entry,
                "exit": exit_event.price,
                "qty": pos.qty,
                "notional": pos.notional,
                "leverage": pos.leverage,
                "reason": exit_event.reason,
                # Para alanlari YUVARLANMAZ: islem defteri ile bakiye
                # birbirini kurusuna kadar tutmali.
                "r_multiple": exit_event.r_multiple,
                "gross_usd": exit_event.gross_usd,
                "fee_usd": exit_event.fee_usd,
                "net_usd": exit_event.net_usd,
                "opened_at": pos.opened_at,
                "closed_at": closed_at,
                "bars_held": pos.bars_held,
                "mode": pos.mode,
            }
        )
        self.equity = self.equity + exit_event.net_usd
        day = closed_at[:10]
        self.data["daily"][day] = self.data["daily"].get(day, 0.0) + exit_event.net_usd

    def day_pnl(self, day: str | None = None) -> float:
        """Belirtilen gunun (varsayilan bugun) net kar/zarari."""
        day = day or utcnow_iso()[:10]
        return float(self.data["daily"].get(day, 0.0))

    # ------------------------------------------------------------- bekleme

    def set_cooldown(self, symbol: str, until_iso: str) -> None:
        """Zararli islemden sonra bu paritede bekleme suresi koy."""
        self.data["cooldown"][symbol] = until_iso

    def in_cooldown(self, symbol: str, now_iso: str) -> bool:
        """Bu parite bekleme suresinde mi."""
        until = self.data["cooldown"].get(symbol)
        return bool(until and now_iso < until)

    # ------------------------------------------------------------- durdurma

    def halt(self, reason: str) -> None:
        """Botu yeni islem acmaktan alikoy."""
        self.data["halted"] = True
        self.data["halt_reason"] = reason

    def resume(self) -> None:
        """Durdurmayi kaldir."""
        self.data["halted"] = False
        self.data["halt_reason"] = ""

    @property
    def halted(self) -> bool:
        """Bot durdurulmus mu."""
        return bool(self.data.get("halted"))

    # ---------------------------------------------------------------- ozet

    def stats(self) -> dict[str, Any]:
        """Kapanmis islemlerden performans ozeti."""
        trades = self.data["closed_trades"]
        if not trades:
            return {"count": 0}
        nets = [t["net_usd"] for t in trades]
        wins = [n for n in nets if n > 0]
        losses = [n for n in nets if n <= 0]
        gross_win, gross_loss = sum(wins), abs(sum(losses))
        return {
            "count": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(trades) * 100.0,
            "net_usd": sum(nets),
            "avg_r": sum(t["r_multiple"] for t in trades) / len(trades),
            "total_fees": sum(t["fee_usd"] for t in trades),
            "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
            "best": max(nets),
            "worst": min(nets),
        }
