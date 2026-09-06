"""Bildirim katmani: CMD ciktisi, ses, dosya logu ve (istege bagli) Telegram."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .risk import ExitEvent, Position, TradePlan
from .state import utcnow_iso

console = Console()


class Notifier:
    """Sinyalleri ve islem olaylarini kullaniciya ulastirir."""

    def __init__(self, cfg: dict):
        self.cfg = cfg["notify"]
        self.mode = cfg["execution"]["mode"]
        self.to_console = bool(self.cfg.get("console", True))
        self.logfile = Path(self.cfg["logfile"]) if self.cfg.get("logfile") else None
        if self.logfile:
            self.logfile.parent.mkdir(parents=True, exist_ok=True)

    def _print(self, *args, **kwargs) -> None:
        """notify.console kapaliysa ekrana hicbir sey basma (log yine tutulur)."""
        if self.to_console:
            console.print(*args, **kwargs)

    # ------------------------------------------------------------- yardimci

    def _log(self, text: str) -> None:
        """Zaman damgali satiri log dosyasina ekle."""
        if not self.logfile:
            return
        try:
            with self.logfile.open("a", encoding="utf-8") as fh:
                fh.write(f"{utcnow_iso()}  {text}\n")
        except OSError as exc:
            console.print(f"[yellow]Log yazilamadi: {exc}[/yellow]")

    def _beep(self) -> None:
        """CMD'de dikkat cekmek icin bip."""
        if self.to_console and self.cfg.get("sound"):
            sys.stdout.write("\a")
            sys.stdout.flush()

    def _telegram(self, text: str) -> None:
        """Istege bagli Telegram bildirimi (varsayilan kapali)."""
        tg = self.cfg.get("telegram") or {}
        if not tg.get("enabled") or not tg.get("bot_token") or not tg.get("chat_id"):
            return
        url = f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage"
        payload = urllib.parse.urlencode(
            {"chat_id": tg["chat_id"], "text": text, "parse_mode": "HTML"}
        ).encode()
        try:
            with urllib.request.urlopen(url, data=payload, timeout=10) as resp:
                resp.read()
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            console.print(f"[yellow]Telegram bildirimi gonderilemedi: {exc}[/yellow]")

    def info(self, text: str) -> None:
        """Bilgi satiri."""
        self._print(f"[dim]{text}[/dim]")

    def warn(self, text: str) -> None:
        """Uyari satiri."""
        self._print(f"[yellow]UYARI:[/yellow] {text}")
        self._log(f"UYARI  {text}")

    def error(self, text: str) -> None:
        """Hata satiri."""
        self._print(f"[bold red]HATA:[/bold red] {text}")
        self._log(f"HATA   {text}")

    def banner(self, lines: list[str]) -> None:
        """Acilis kutusu."""
        self._print(
            Panel("\n".join(lines), title="SWING SINYAL BOTU", border_style="cyan")
        )

    # -------------------------------------------------------------- sinyal

    def signal(self, plan: TradePlan) -> None:
        """Islem planini genis kutu halinde goster — 'su coini su kaldiracla gir'."""
        colour = "green" if plan.side == "long" else "red"
        body = Table.grid(padding=(0, 2))
        body.add_column(style="bold")
        body.add_column()

        rows = [
            ("Yon", f"[{colour}]{plan.side_tr}[/{colour}]"),
            ("Kaldirac", f"[bold yellow]{plan.leverage}x[/bold yellow]"),
            ("Giris", f"{plan.entry:.6g}"),
            ("Stop-loss", f"[red]{plan.stop:.6g}[/red]  (%{plan.stop_pct:.2f})"),
            ("Kar al", f"[green]{plan.take_profit:.6g}[/green]  (%{plan.tp_pct:.2f})"),
            ("Pozisyon", f"{plan.notional:.2f} USDT  ({plan.qty:.8g} adet)"),
            ("Teminat", f"{plan.margin:.2f} USDT"),
            ("Risk", f"{plan.risk_usd:.2f} USDT  (sermayenin %{plan.risk_pct:.1f}'i)"),
            ("Likidasyon", f"{plan.liq_price:.6g}  (stop cok once tetiklenir)"),
            ("Beklenen", f"brut {plan.expected_gross:.2f} / net {plan.expected_net:.2f} USDT"),
            ("Masraf", f"{plan.fee_total:.3f} USDT  (kar/masraf {plan.fee_ratio:.1f}x)"),
            ("Risk/Odul", f"1 : {plan.rr:.1f}"),
            ("Puan", f"{plan.score:.0f}/100"),
        ]
        for label, value in rows:
            body.add_row(label, value)
        if plan.reasons:
            body.add_row("Gerekce", " | ".join(plan.reasons))
        for warning in plan.warnings:
            body.add_row("[yellow]Dikkat[/yellow]", f"[yellow]{warning}[/yellow]")

        title = f"SINYAL — {plan.symbol}  ({self.mode.upper()})"
        self._print(Panel(body, title=title, border_style=colour))
        self._beep()
        self._log(f"SINYAL {json.dumps(plan.to_dict(), default=str, ensure_ascii=False)}")
        self._telegram(
            f"<b>{plan.symbol} {plan.side_tr}</b>\n"
            f"Giris {plan.entry:.6g} | Kaldirac {plan.leverage}x\n"
            f"Stop {plan.stop:.6g} | Hedef {plan.take_profit:.6g}\n"
            f"Pozisyon {plan.notional:.2f} USDT, risk {plan.risk_usd:.2f} USDT"
        )

    def rejected(self, symbol: str, reason: str) -> None:
        """Elenen aday — neden girilmedigini acikca soyle."""
        self._print(f"[dim]  {symbol}: atlandi — {reason}[/dim]")
        self._log(f"ELENDI {symbol}: {reason}")

    # -------------------------------------------------------------- islem

    def opened(self, pos: Position) -> None:
        """Pozisyon acildi."""
        text = (
            f"ACILDI {pos.symbol} {pos.side_tr} @ {pos.entry:.6g} "
            f"| {pos.leverage}x | {pos.notional:.2f} USDT "
            f"| stop {pos.stop:.6g} | hedef {pos.take_profit:.6g}"
        )
        self._print(f"[bold cyan]{text}[/bold cyan]")
        self._log(text)
        self._telegram(text)

    def stop_moved(self, pos: Position, notes: list[str]) -> None:
        """Iz suren stop guncellendi."""
        for note in notes:
            text = f"{pos.symbol}: {note}"
            self._print(f"[magenta]{text}[/magenta]")
            self._log(text)
            self._telegram(text)

    def closed(self, pos: Position, ev: ExitEvent) -> None:
        """Pozisyon kapandi."""
        won = ev.net_usd > 0
        colour = "green" if won else "red"
        text = (
            f"KAPANDI {pos.symbol} {pos.side_tr} @ {ev.price:.6g} — {ev.reason} "
            f"| {ev.r_multiple:+.2f}R | brut {ev.gross_usd:+.2f} "
            f"| masraf {ev.fee_usd:.2f} | NET {ev.net_usd:+.2f} USDT"
        )
        self._print(f"[bold {colour}]{text}[/bold {colour}]")
        self._beep()
        self._log(text)
        self._telegram(text)

    # -------------------------------------------------------------- tablo

    def positions_table(self, rows: list[dict]) -> None:
        """Acik pozisyonlar tablosu."""
        if not rows:
            self._print("[dim]Acik pozisyon yok.[/dim]")
            return
        table = Table(title="ACIK POZISYONLAR", header_style="bold cyan")
        for col in ("Parite", "Yon", "Kald.", "Giris", "Fiyat", "Stop",
                    "Hedef", "R", "K/Z (USDT)", "Kilitli"):
            table.add_column(col)
        for r in rows:
            pnl = r["pnl"]
            table.add_row(
                r["symbol"], r["side"], f"{r['leverage']}x", f"{r['entry']:.6g}",
                f"{r['price']:.6g}", f"{r['stop']:.6g}", f"{r['tp']:.6g}",
                f"{r['r']:+.2f}",
                Text(f"{pnl:+.2f}", style="green" if pnl >= 0 else "red"),
                f"{r['locked']:+.2f}R",
            )
        self._print(table)

    def stats_table(self, stats: dict, equity: float, start_equity: float) -> None:
        """Performans ozeti."""
        if not stats.get("count"):
            self._print("[dim]Henuz kapanmis islem yok.[/dim]")
        else:
            table = Table(title="PERFORMANS", header_style="bold cyan")
            table.add_column("Olcut")
            table.add_column("Deger", justify="right")
            table.add_row("Islem sayisi", str(stats["count"]))
            table.add_row("Kazanan / Kaybeden", f"{stats['wins']} / {stats['losses']}")
            table.add_row("Isabet orani", f"%{stats['win_rate']:.1f}")
            table.add_row("Ortalama R", f"{stats['avg_r']:+.2f}")
            table.add_row("Kar faktoru", f"{stats['profit_factor']:.2f}")
            table.add_row("Toplam masraf", f"{stats['total_fees']:.2f} USDT")
            table.add_row("En iyi / En kotu", f"{stats['best']:+.2f} / {stats['worst']:+.2f}")
            table.add_row("Net sonuc", f"{stats['net_usd']:+.2f} USDT")
            self._print(table)
        change = (equity / start_equity - 1) * 100 if start_equity else 0.0
        self._print(
            f"Sermaye: [bold]{equity:.2f} USDT[/bold] "
            f"(baslangic {start_equity:.2f} — %{change:+.1f})"
        )
