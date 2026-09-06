"""Komut satiri arayuzu — botun tum kullanimi buradan gecer."""

from __future__ import annotations

import argparse
import glob as globmod
import sys
from pathlib import Path

import ccxt
import pandas as pd
from rich.console import Console
from rich.table import Table

from . import __version__
from .backtest import BacktestResult, prepare_frames, run_backtest
from .config import ConfigError, load_config, summary_lines
from .configedit import ConfigEditError, apply_changes, read_scalar
from .datafeed import load_csv, resample, save_csv, synthetic
from .exchange import Exchange, ExchangeError
from .risk import ExitEvent, min_notional_for_fee_ratio, round_fees
from .state import State
from .strategy import build_features

console = Console()


# --------------------------------------------------------------------- yardim

def expand_csv_paths(patterns: list[str]) -> list[Path]:
    """Dosya, klasor veya joker (*) kaliplarini gercek dosya listesine cevir.

    PowerShell joker karakterleri programa ACMADAN aktarir, cmd.exe ise hic
    genisletmez. Bu yuzden genisletmeyi botun kendisi yapar; boylece
    `--csv data`, `--csv data/*.csv` ve `--csv a.csv b.csv` hepsi calisir.
    """
    found: list[Path] = []
    for pattern in patterns:
        candidate = Path(pattern)
        if candidate.is_dir():
            matches = sorted(candidate.glob("*.csv"))
        else:
            matches = sorted(Path(m) for m in globmod.glob(pattern))
        if not matches:
            raise ConfigError(
                f"'{pattern}' ile eslesen CSV dosyasi yok. "
                f"Once veriyi indir:  python run.py fetch"
            )
        found.extend(matches)

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _engine(args):
    """Motoru kur (gec ice aktarma: 'backtest' icin borsa baglantisi gerekmez)."""
    from .engine import Engine

    cfg = load_config(args.config)
    if getattr(args, "mode", None):
        cfg["execution"]["mode"] = args.mode
    if getattr(args, "equity", None):
        cfg["risk"]["equity_usd"] = float(args.equity)
    return Engine(cfg)


def _warn_if_testnet_data(cfg: dict) -> None:
    """Testnet fiyat verisi GERCEK piyasa degildir — analiz icin kullanilamaz.

    Testnet'in mum verisi seyrek ve yapaydir; oradan alinan backtest sonucu
    hicbir sey ifade etmez. Emir denemek icin testnet dogru yer, veri icin degil.
    """
    if cfg["exchange"]["testnet"]:
        console.print(
            "[bold yellow]DIKKAT:[/bold yellow] testnet acik. Testnet fiyat verisi "
            "gercek piyasa degildir; buradan cikan sonuc yanittir.\n"
            "config.yaml icinde [bold]testnet: false[/bold] yap "
            "(paper modda emir GONDERILMEZ, sadece gercek fiyat okunur).\n"
        )


def _confirm_live(cfg: dict, yes: bool) -> bool:
    """Gercek para modunda acik onay iste."""
    if cfg["execution"]["mode"] != "live" or cfg["exchange"]["testnet"]:
        return True
    if yes:
        return True
    console.print(
        "[bold red]DIKKAT: GERCEK PARA MODU.[/bold red] Bot kendi basina emir gonderecek."
    )
    answer = input("Devam etmek icin buyuk harfle ONAYLIYORUM yaz: ").strip()
    if answer != "ONAYLIYORUM":
        console.print("Iptal edildi.")
        return False
    return True


# -------------------------------------------------------------------- komutlar

def _ask(question: str, current: str | None) -> str:
    """Soruyu sor; bos birakilirsa mevcut deger korunur."""
    suffix = f" [{current}]" if current is not None else ""
    answer = input(f"  {question}{suffix}: ").strip()
    return answer or (current or "")


def _ask_yes_no(question: str, current: bool) -> bool:
    """Evet/hayir sorusu — 'e', 'evet', 'y', 'yes' olumlu sayilir."""
    default = "evet" if current else "hayir"
    while True:
        answer = input(f"  {question} (evet/hayir) [{default}]: ").strip().lower()
        if not answer:
            return current
        if answer in ("e", "evet", "y", "yes", "true"):
            return True
        if answer in ("h", "hayir", "hayır", "n", "no", "false"):
            return False
        console.print("    [yellow]Lutfen 'evet' veya 'hayir' yaz.[/yellow]")


def _ask_number(question: str, current: str | None, minimum: float, maximum: float) -> float:
    """Sayi sorusu — aralik disi ve gecersiz girdilerde tekrar sorar."""
    while True:
        raw = _ask(question, current).replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            console.print("    [yellow]Sayi yaz (ornek: 3 veya 3.5).[/yellow]")
            continue
        if not minimum <= value <= maximum:
            console.print(f"    [yellow]{minimum:g} ile {maximum:g} arasinda olmali.[/yellow]")
            continue
        return value


def cmd_setup(args) -> int:
    """Ayarlari soru-cevap ile yaz — config.yaml'i elle duzenlemeye gerek kalmasin."""
    path = Path(args.config)
    if not path.exists():
        example = Path("config.example.yaml")
        if not example.exists():
            console.print(f"[red]{path} yok ve config.example.yaml da bulunamadi.[/red]")
            return 1
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        console.print(f"[dim]{path} olusturuldu.[/dim]")

    original = path.read_text(encoding="utf-8")
    changes: dict[tuple[str, str], object] = {}
    flags_given = any(
        getattr(args, name) is not None
        for name in ("equity", "testnet", "mode", "risk", "max_leverage")
    )

    if flags_given:
        if args.equity is not None:
            changes[("risk", "equity_usd")] = float(args.equity)
        if args.testnet is not None:
            changes[("exchange", "testnet")] = args.testnet
        if args.mode is not None:
            changes[("execution", "mode")] = args.mode
        if args.risk is not None:
            changes[("risk", "risk_per_trade_pct")] = float(args.risk)
        if args.max_leverage is not None:
            changes[("risk", "max_leverage")] = int(args.max_leverage)
    else:
        console.print(
            "\n[bold cyan]AYARLAR[/bold cyan]  "
            "[dim](Enter'a basarsan mevcut deger kalir)[/dim]\n"
        )
        equity = _ask_number(
            "Hesabindaki gercek para (USDT)",
            read_scalar(original, "risk", "equity_usd"), 0.5, 1_000_000,
        )
        risk = _ask_number(
            "Islem basina risk yuzdesi",
            read_scalar(original, "risk", "risk_per_trade_pct"), 0.5, 100,
        )
        leverage = _ask_number(
            "En yuksek kaldirac",
            read_scalar(original, "risk", "max_leverage"), 1, 50,
        )

        console.print(
            "\n  [dim]Testnet, Binance'in sahte para ile emir denemek icin actigi "
            "AYRI bir sistemdir.\n"
            "  Fiyat verisi gercek piyasayi yansitmaz ve kendi ayri API "
            "anahtarlarini ister.\n"
            "  Gercek fiyatlarla calismak icin 'hayir' de.[/dim]"
        )
        testnet = _ask_yes_no(
            "Testnet kullanilsin mi",
            str(read_scalar(original, "exchange", "testnet")).lower() == "true",
        )

        console.print(
            "\n  [dim]signal = sadece uyari verir  |  paper = sanal islem "
            "(GERCEK EMIR YOK)  |  live = gercek para[/dim]"
        )
        current_mode = read_scalar(original, "execution", "mode") or "paper"
        while True:
            mode = _ask("Mod (signal/paper/live)", current_mode).lower()
            if mode in ("signal", "paper", "live"):
                break
            console.print("    [yellow]signal, paper veya live yaz.[/yellow]")

        changes = {
            ("risk", "equity_usd"): equity,
            ("risk", "risk_per_trade_pct"): risk,
            ("risk", "max_leverage"): int(leverage),
            ("exchange", "testnet"): testnet,
            ("execution", "mode"): mode,
        }

    if not changes:
        console.print("[yellow]Degistirilecek bir sey verilmedi.[/yellow]")
        return 0

    try:
        summary = apply_changes(path, changes)
    except ConfigEditError as exc:
        console.print(f"[red]Ayar yazilamadi:[/red] {exc}")
        return 1

    # Yazdiktan sonra dosya hala gecerli mi — degilse eski haline dondur
    try:
        cfg = load_config(path)
    except ConfigError as exc:
        path.write_text(original, encoding="utf-8")
        console.print(f"[red]Bu ayarlar gecersiz, degisiklik geri alindi:[/red] {exc}")
        return 1

    console.print("\n[green]Kaydedildi:[/green]")
    for line in summary or ["(degisiklik yok)"]:
        console.print(f"  {line}")
    console.print()
    for line in summary_lines(cfg):
        console.print(f"  {line}")

    if cfg["exchange"]["testnet"] and cfg["execution"]["mode"] != "live":
        console.print(
            "\n[yellow]Not:[/yellow] testnet acik kaldi — okunan fiyatlar gercek "
            "piyasa degil."
        )
    if cfg["execution"]["mode"] == "paper":
        console.print(
            "\n[dim]Mod 'paper': bot GERCEK EMIR GONDERMEZ, sadece sanal islem yapar.[/dim]"
        )
    console.print("\n[bold]Siradaki adim:[/bold] ayarlari-kontrol-et.bat  ->  baslat.bat")
    return 0


def cmd_doctor(args) -> int:
    """Yapilandirmayi, baglantiyi ve ucret mantigini kontrol et."""
    cfg = load_config(args.config)
    console.print("[bold]1) Yapilandirma[/bold]  ... gecerli")
    for line in summary_lines(cfg):
        console.print(f"   {line}")

    fees, risk = cfg["fees"], cfg["risk"]
    stop_pct = (risk["min_stop_pct"] + risk["max_stop_pct"]) / 2
    needed = min_notional_for_fee_ratio(stop_pct, risk["take_profit_r"], fees)
    console.print("\n[bold]2) Masraf mantigi[/bold]")
    if fees["flat_fee_usd"] > 0:
        console.print(
            f"   Sabit ucret {fees['flat_fee_usd']:.2f} USDT/islem varsayiliyor.\n"
            f"   Ortalama stop %{stop_pct:.1f} ve {risk['take_profit_r']:.1f}R hedefle "
            f"anlamli en kucuk pozisyon: [bold]~{needed:.0f} USDT[/bold]"
        )
        margin_needed = needed / max(risk["max_leverage"], 1)
        console.print(
            f"   Bu, {risk['max_leverage']}x kaldiracta ~{margin_needed:.0f} USDT teminat demek."
        )
        if margin_needed > risk["equity_usd"]:
            console.print(
                f"   [bold red]Sermaye ({risk['equity_usd']:.2f} USDT) bunun icin yetersiz.[/bold red] "
                f"Bot bu ayarlarla dogru olarak hicbir islem acmayacak."
            )
    else:
        console.print(
            f"   Sadece yuzdesel komisyon (%{fees['taker_pct']}) + kayma "
            f"(%{fees['slippage_pct']}) — kucuk hesap icin uygun."
        )

    console.print("\n[bold]3) Borsa baglantisi[/bold]")
    try:
        ex = Exchange(cfg)
        symbols = ex.discover_universe()
        console.print(f"   Piyasa verisi OK — {len(symbols)} parite bulundu.")
        if symbols:
            probe = symbols[0]
            console.print(
                f"   Ornek {probe}: maks kaldirac {ex.max_leverage(probe):.0f}x, "
                f"min pozisyon {ex.min_notional(probe):.2f} USDT"
            )
            df = ex.fetch_ohlcv(probe, cfg["timeframes"]["signal"], 50)
            console.print(f"   Son bar: {df['ts'].iloc[-1]} (kapandi: {bool(df['closed'].iloc[-1])})")
    except ExchangeError as exc:
        console.print(f"   [red]Baglanti hatasi:[/red] {exc}")
        console.print("   Internet/VPN veya borsa erisimini kontrol et.")
        return 1

    console.print("\n[bold]4) API anahtari[/bold]")
    mode = cfg["execution"]["mode"]
    key_required = mode == "live"

    if not cfg["exchange"]["api_key"]:
        console.print("   Anahtar yok — 'signal' ve 'paper' modlari icin gerekli degil.")
    else:
        try:
            balance = ex.fetch_equity()
            console.print(f"   [green]Calisiyor.[/green] Bakiye: {balance:.2f} {cfg['exchange']['quote']}")
        except ExchangeError as exc:
            level = "red" if key_required else "yellow"
            console.print(f"   [{level}]Anahtar calismiyor:[/{level}] {exc}")
            for hint in _api_key_hints(cfg, str(exc)):
                console.print(f"   {hint}")
            if key_required:
                return 1
            console.print(
                f"   [dim]Su anki mod '{mode}' oldugu icin bu ENGEL DEGIL — "
                f"bot fiyat verisiyle calismaya devam eder.[/dim]"
            )

    console.print("\n[green]Kontroller tamamlandi.[/green]")
    return 0


def _api_key_hints(cfg: dict, error: str) -> list[str]:
    """Anahtar hatasinin en olasi sebebini adres goster."""
    hints: list[str] = []
    key_error = "-2015" in error or "Invalid API-key" in error

    if key_error and cfg["exchange"]["testnet"]:
        hints.append(
            "[bold]En olasi sebep:[/bold] testnet acik ama GERCEK Binance anahtari kullaniyorsun."
        )
        hints.append(
            "Testnet'in kendi ayri anahtarlari var (testnet.binancefuture.com). "
            "Gercek hesabin anahtari orada gecmez."
        )
        hints.append(
            "Gercek piyasada calisacaksan config.yaml icinde [bold]testnet: false[/bold] yap."
        )
    elif key_error:
        hints.append("Kontrol et: anahtarda [bold]Enable Futures[/bold] izni acik mi?")
        hints.append(
            "Kontrol et: Binance'te IP kisiti varsa, bu bilgisayarin IP'si listede mi? "
            "Ev internetinde IP degisebilir — degistiginde anahtar calismayi birakir."
        )
        hints.append("Anahtari yeni olusturduysan aktif olmasi birkac dakika surebilir.")
    return hints


def cmd_scan(args) -> int:
    """Tek seferlik tarama — sinyalleri ekrana bas, islem acma."""
    engine = _engine(args)
    _warn_if_testnet_data(engine.cfg)
    engine.note.banner(summary_lines(engine.cfg) + ["Tek seferlik tarama"])
    symbols = args.symbols or None
    plans = engine.scan(symbols=symbols)
    if not plans:
        console.print("[yellow]Su an kriterlere uyan islem yok. Sabir da bir pozisyondur.[/yellow]")
        return 0
    for plan, _ in plans[: args.top]:
        engine.note.signal(plan)
    return 0


def cmd_run(args) -> int:
    """Surekli calisma."""
    engine = _engine(args)
    _warn_if_testnet_data(engine.cfg)
    if not _confirm_live(engine.cfg, args.yes):
        return 1
    engine.note.banner(summary_lines(engine.cfg))
    try:
        engine.run_forever()
    except KeyboardInterrupt:
        console.print("\n[cyan]Bot durduruldu. Acik pozisyonlar kayitli.[/cyan]")
    return 0


def cmd_status(args) -> int:
    """Acik pozisyonlar ve performans ozeti."""
    engine = _engine(args)
    rows = []
    for symbol, pos in engine.state.positions().items():
        try:
            price = engine.ex.last_price(symbol)
        except ExchangeError:
            price = pos.entry
        rows.append(
            {"symbol": symbol, "side": pos.side_tr, "leverage": pos.leverage,
             "entry": pos.entry, "price": price, "stop": pos.stop,
             "tp": pos.take_profit, "r": pos.r_multiple(price),
             "pnl": pos.unrealized_usd(price), "locked": pos.locked_r()}
        )
    engine.note.positions_table(rows)
    engine.note.stats_table(
        engine.state.stats(), engine.state.equity,
        engine.state.data.get("start_equity") or engine.state.equity,
    )
    if engine.state.halted:
        console.print(f"[yellow]Bot durdurulmus: {engine.state.data['halt_reason']}[/yellow]")
    return 0


def cmd_close(args) -> int:
    """Bir pozisyonu elle kapat."""
    engine = _engine(args)
    pos = engine.state.get_position(args.symbol)
    if pos is None:
        console.print(f"[yellow]{args.symbol} icin acik pozisyon yok.[/yellow]")
        return 1
    try:
        price = engine.ex.last_price(args.symbol)
    except ExchangeError:
        price = pos.entry
    gross = pos.direction * (price - pos.entry) * pos.qty
    fee = round_fees(pos.notional, engine.cfg["fees"])
    engine.close_position(
        pos,
        ExitEvent("Elle kapatildi", price, pos.r_multiple(price), gross, fee, gross - fee),
    )
    return 0


def cmd_resume(args) -> int:
    """Gunluk zarar durdurmasini kaldir."""
    state = State(load_config(args.config)["state_file"])
    state.resume()
    state.save()
    console.print("[green]Durdurma kaldirildi. Bot yeni islem arayabilir.[/green]")
    return 0


def _print_backtest(result: BacktestResult, title: str, show_trades: int) -> None:
    """Backtest sonucunu tablo halinde bas."""
    s = result.summary()
    if not s["count"]:
        console.print(f"[yellow]{title}: hic islem acilmadi.[/yellow]")
        if s.get("rejected"):
            console.print("Elenme sebepleri:")
            for reason, count in sorted(s["rejected"].items(), key=lambda x: -x[1]):
                console.print(f"  {count:4d} x {reason}")
        return

    table = Table(title=title, header_style="bold cyan")
    table.add_column("Olcut")
    table.add_column("Deger", justify="right")
    rows = [
        ("Islem sayisi", f"{s['count']}"),
        ("Kazanan / Kaybeden", f"{s['wins']} / {s['losses']}"),
        ("Isabet orani", f"%{s['win_rate']:.1f}"),
        ("Ortalama R", f"{s['avg_r']:+.2f}"),
        ("Islem basi beklenti", f"{s['expectancy_usd']:+.3f} USDT"),
        ("Kar faktoru", f"{s['profit_factor']:.2f}"),
        ("Maks. geri cekilme", f"%{s['max_drawdown_pct']:.1f}"),
        ("Ort. tutma suresi", f"{s['avg_bars_held']:.1f} bar"),
        ("Toplam masraf", f"{s['total_fees']:.2f} USDT"),
        ("Baslangic -> Bitis", f"{s['start_equity']:.2f} -> {s['end_equity']:.2f} USDT"),
        ("Getiri", f"%{s['return_pct']:+.1f}"),
    ]
    for label, value in rows:
        table.add_row(label, value)
    console.print(table)

    if s.get("rejected"):
        console.print("[dim]Elenen adaylar:[/dim]")
        for reason, count in sorted(s["rejected"].items(), key=lambda x: -x[1])[:5]:
            console.print(f"[dim]  {count:4d} x {reason}[/dim]")

    if show_trades:
        tt = Table(title="ISLEMLER (son)", header_style="bold")
        for col in ("Parite", "Yon", "Giris", "Cikis", "Kald.", "R", "Net", "Bar", "Sebep"):
            tt.add_column(col)
        for t in result.trades[-show_trades:]:
            tt.add_row(
                t["symbol"], t["side"], f"{t['entry']:.6g}", f"{t['exit']:.6g}",
                f"{t['leverage']}x", f"{t['r_multiple']:+.2f}", f"{t['net_usd']:+.2f}",
                str(t["bars_held"]), t["reason"],
            )
        console.print(tt)


def cmd_backtest(args) -> int:
    """Gecmis veriyle stratejiyi test et."""
    cfg = load_config(args.config)
    if args.equity:
        cfg["risk"]["equity_usd"] = float(args.equity)

    frames: dict[str, pd.DataFrame] = {}
    if args.csv:
        paths = expand_csv_paths(args.csv)
        console.print(f"[dim]{len(paths)} CSV dosyasi okunuyor[/dim]")
        for path in paths:
            ltf = load_csv(path, cfg["timeframes"]["signal"])
            htf = resample(ltf, cfg["timeframes"]["trend"])
            frames[path.stem] = build_features(ltf, htf, cfg["strategy"])
    else:
        _warn_if_testnet_data(cfg)
        ex = Exchange(cfg)
        symbols = args.symbols or ex.discover_universe()[: args.top]
        console.print(f"[dim]Veri cekiliyor: {', '.join(symbols)}[/dim]")
        try:
            frames = prepare_frames(ex, symbols, cfg, args.bars)
        except ExchangeError as exc:
            console.print(f"[red]Veri alinamadi:[/red] {exc}")
            console.print("Internet yoksa --csv ile kendi verinle test edebilirsin.")
            return 1

    result = run_backtest(frames, cfg, compound=not args.no_compound)
    span = ""
    first = next(iter(frames.values()))
    if not first.empty:
        span = f"  [{first['ts'].iloc[0].date()} -> {first['ts'].iloc[-1].date()}]"
    _print_backtest(result, f"BACKTEST — {len(frames)} parite{span}", args.trades)
    return 0


def cmd_fetch(args) -> int:
    """Gecmis mum verisini CSV olarak indir — sonra internetsiz backtest yapilir."""
    cfg = load_config(args.config)
    _warn_if_testnet_data(cfg)
    ex = Exchange(cfg)
    timeframe = cfg["timeframes"]["signal"]
    symbols = args.symbols or ex.discover_universe()[: args.top]
    out_dir = Path(args.out)

    console.print(f"[dim]{len(symbols)} parite, {timeframe}, {args.bars} bar -> {out_dir}/[/dim]")
    saved = 0
    for symbol in symbols:
        try:
            df = ex.fetch_ohlcv(symbol, timeframe, args.bars)
        except ExchangeError as exc:
            console.print(f"  [yellow]{symbol}: atlandi — {exc}[/yellow]")
            continue
        path = out_dir / f"{symbol.replace('/', '_')}_{timeframe}.csv"
        rows = save_csv(df, path)
        span = f"{df['ts'].iloc[0].date()} -> {df['ts'].iloc[-1].date()}"
        console.print(f"  {symbol:<14} {rows:>5} bar  [{span}]  {path}")
        saved += 1

    if not saved:
        console.print("[red]Hicbir parite indirilemedi.[/red]")
        return 1
    console.print(
        f"\n[green]{saved} dosya kaydedildi.[/green] Simdi internetsiz test edebilirsin:\n"
        f"  python run.py backtest --csv {out_dir}/*.csv"
    )
    return 0


def cmd_selftest(args) -> int:
    """Borsaya baglanmadan tum zinciri sentetik veriyle dogrula."""
    cfg = load_config(args.config) if Path(args.config).exists() else None
    if cfg is None:
        from .config import DEFAULTS
        import copy

        cfg = copy.deepcopy(DEFAULTS)
        console.print("[dim]config.yaml yok — varsayilan ayarlarla test ediliyor.[/dim]")

    cfg["fees"]["flat_fee_usd"] = 0.0  # sentetik testte sabit ucreti kapat
    frames = {}
    for i in range(args.symbols_count):
        ltf = synthetic(bars=args.bars, timeframe=cfg["timeframes"]["signal"], seed=100 + i)
        htf = resample(ltf, cfg["timeframes"]["trend"])
        frames[f"TEST{i + 1}/USDT"] = build_features(ltf, htf, cfg["strategy"])

    result = run_backtest(frames, cfg)
    _print_backtest(result, "SELFTEST (sentetik veri — piyasa degil)", args.trades)
    console.print(
        "\n[yellow]Not:[/yellow] Bu sonuc rastgele uretilmis fiyat serisine aittir; "
        "stratejinin karliligi hakkinda HICBIR sey soylemez. Sadece kodun uctan uca "
        "calistigini gosterir. Gercek deger icin 'backtest' komutunu kullan."
    )
    return 0


# ------------------------------------------------------------------- argparse

def build_parser() -> argparse.ArgumentParser:
    """Komut satiri secenekleri."""
    parser = argparse.ArgumentParser(
        prog="swingbot",
        description="Uzun vadeli (swing) kripto sinyal ve islem botu",
    )
    parser.add_argument("--version", action="version", version=f"swingbot {__version__}")
    parser.add_argument("-c", "--config", default="config.yaml", help="yapilandirma dosyasi")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ayarla", aliases=["setup"],
                       help="ayarlari soru-cevap ile yaz (config.yaml'i elle acmadan)")
    p.add_argument("--equity", type=float, help="sermaye (USDT)")
    p.add_argument("--risk", type=float, help="islem basina risk yuzdesi")
    p.add_argument("--max-leverage", type=int, dest="max_leverage")
    p.add_argument("--mode", choices=["signal", "paper", "live"])
    testnet_group = p.add_mutually_exclusive_group()
    testnet_group.add_argument("--testnet", dest="testnet", action="store_true", default=None)
    testnet_group.add_argument("--no-testnet", dest="testnet", action="store_false", default=None)
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser("doctor", help="ayarlari ve baglantiyi kontrol et")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("scan", help="tek seferlik tarama, sinyalleri goster")
    p.add_argument("--symbols", nargs="*", help="sadece bu pariteleri tara")
    p.add_argument("--top", type=int, default=5, help="kac sinyal gosterilsin")
    p.add_argument("--mode", choices=["signal", "paper", "live"])
    p.add_argument("--equity", type=float)
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("run", help="surekli calis (asil kullanim)")
    p.add_argument("--mode", choices=["signal", "paper", "live"])
    p.add_argument("--equity", type=float)
    p.add_argument("--yes", action="store_true", help="live modda onay sorma")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("status", help="acik pozisyonlar ve performans")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("close", help="bir pozisyonu elle kapat")
    p.add_argument("symbol")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("resume", help="gunluk zarar durdurmasini kaldir")
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("backtest", help="gecmis veriyle test et")
    p.add_argument("--symbols", nargs="*")
    p.add_argument("--top", type=int, default=8, help="auto listeden kac parite")
    p.add_argument("--bars", type=int, default=2000, help="sinyal zaman diliminde bar sayisi")
    p.add_argument("--equity", type=float)
    p.add_argument("--csv", nargs="*",
                   help="borsa yerine CSV kullan: dosya, klasor veya joker (data, data/*.csv)")
    p.add_argument("--trades", type=int, default=0, help="son N islemi listele")
    p.add_argument("--no-compound", action="store_true", help="sabit pozisyon boyutu")
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("fetch", help="gecmis veriyi CSV olarak indir")
    p.add_argument("--symbols", nargs="*")
    p.add_argument("--top", type=int, default=10, help="auto listeden kac parite")
    p.add_argument("--bars", type=int, default=6000, help="indirilecek bar sayisi")
    p.add_argument("--out", default="data", help="cikti klasoru")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("selftest", help="borsasiz, sentetik veriyle kod testi")
    p.add_argument("--bars", type=int, default=3000)
    p.add_argument("--symbols-count", type=int, default=4, dest="symbols_count")
    p.add_argument("--trades", type=int, default=8)
    p.set_defaults(func=cmd_selftest)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Giris noktasi."""
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        console.print(f"[bold red]Yapilandirma hatasi:[/bold red] {exc}")
        return 2
    except ExchangeError as exc:
        console.print(f"[bold red]Borsa hatasi:[/bold red] {exc}")
        return 3
    except ccxt.BaseError as exc:
        console.print(f"[bold red]Borsa hatasi ({type(exc).__name__}):[/bold red] {exc}")
        return 3
    except KeyboardInterrupt:
        console.print("\nCikildi.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
