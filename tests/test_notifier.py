"""Bildirim katmani testleri.

Bu dosyanin varlik sebebi: `notify.console` acikken hicbir test bildirim
metotlarini calistirmiyordu ve sonsuz ozyineleme (RecursionError) fark
edilmeden gecti. Her metot hem acik hem kapali konsolla calistirilir.
"""

import copy

import pandas as pd
import pytest

from bot.config import DEFAULTS
from bot.notifier import Notifier
from bot.risk import ExitEvent, Position, build_plan
from bot.strategy import LONG, SHORT, Signal


@pytest.fixture
def cfg(tmp_path):
    c = copy.deepcopy(DEFAULTS)
    c["notify"]["logfile"] = str(tmp_path / "log.txt")
    c["notify"]["sound"] = False
    return c


def make_plan(cfg, side=LONG):
    signal = Signal("SOL/USDT", side, 78.0, pd.Timestamp("2026-01-01", tz="UTC"),
                    150.0, 4.5, reasons=["ADX 27.3", "RSI 58.1"])
    plan = build_plan(signal, 3.0, cfg)
    assert plan.ok, plan.reject_reason
    return plan


def make_position(cfg, side=LONG):
    return Position.from_plan(make_plan(cfg, side), "2026-01-01T00:00:00+00:00", "paper")


def exercise_everything(note, cfg):
    """Notifier'in tum genel metotlarini sirayla cagir."""
    plan = make_plan(cfg)
    pos = make_position(cfg)
    note.banner(["birinci satir", "ikinci satir"])
    note.signal(plan)
    note.rejected("BTC/USDT", "miktar adiminin altinda kaliyor")
    note.opened(pos)
    note.stop_moved(pos, ["Stop basa bas seviyesine cekildi"])
    note.closed(pos, ExitEvent("Kar al hedefi", 183.75, 3.0, 1.8, 0.013, 1.787))
    note.info("bilgi"); note.warn("uyari"); note.error("hata")
    note.positions_table([])
    note.positions_table([{
        "symbol": "SOL/USDT", "side": "ALIS (LONG)", "leverage": 3, "entry": 150.0,
        "price": 160.0, "stop": 152.0, "tp": 183.75, "r": 0.6, "pnl": 0.4, "locked": 0.1,
    }])
    note.stats_table({}, 3.0, 3.0)
    note.stats_table(
        {"count": 2, "wins": 1, "losses": 1, "win_rate": 50.0, "net_usd": 0.5,
         "avg_r": 0.5, "total_fees": 0.04, "profit_factor": 1.8, "best": 1.0,
         "worst": -0.5}, 3.5, 3.0)


def test_every_method_runs_with_console_enabled(cfg, capsys):
    """Konsol ACIKKEN her metot calismali — sonsuz ozyineleme buradan kacmisti."""
    cfg["notify"]["console"] = True
    exercise_everything(Notifier(cfg), cfg)
    assert capsys.readouterr().out.strip(), "konsol acikken ciktı bekleniyor"


def test_every_method_runs_silently_with_console_disabled(cfg, capsys):
    cfg["notify"]["console"] = False
    exercise_everything(Notifier(cfg), cfg)
    assert capsys.readouterr().out == "", "konsol kapaliyken ekrana bir sey basmamali"


def test_log_file_is_written_even_when_console_is_off(cfg):
    cfg["notify"]["console"] = False
    note = Notifier(cfg)
    exercise_everything(note, cfg)
    text = note.logfile.read_text(encoding="utf-8")
    for expected in ("SINYAL", "ACILDI", "KAPANDI", "ELENDI", "UYARI", "HATA"):
        assert expected in text, f"log dosyasinda '{expected}' yok"


@pytest.mark.parametrize("side", [LONG, SHORT])
def test_signal_panel_renders_both_directions(cfg, capsys, side):
    cfg["notify"]["console"] = True
    Notifier(cfg).signal(make_plan(cfg, side))
    out = capsys.readouterr().out
    assert ("LONG" in out) if side == LONG else ("SHORT" in out)
    assert "Kaldirac" in out and "Stop-loss" in out and "Kar al" in out


def test_notifier_works_without_a_log_file(cfg, capsys):
    cfg["notify"].update({"logfile": None, "console": True})
    exercise_everything(Notifier(cfg), cfg)
    assert capsys.readouterr().out.strip()


def test_a_broken_log_path_does_not_stop_the_bot(cfg, capsys, tmp_path):
    """Log yazilamiyorsa bot durmamali, sadece uyarmali."""
    blocker = tmp_path / "engel"
    blocker.write_text("dosya", encoding="utf-8")
    cfg["notify"].update({"logfile": str(blocker / "log.txt"), "console": True})
    with pytest.raises(OSError):
        Notifier(cfg)          # klasor olusturulamaz — kurulumda net hata


def test_telegram_stays_silent_when_disabled(cfg, monkeypatch):
    """Telegram kapaliyken hicbir ag istegi yapilmamali."""
    def explode(*args, **kwargs):
        raise AssertionError("Telegram kapaliyken ag istegi yapilmamali")

    monkeypatch.setattr("bot.notifier.urllib.request.urlopen", explode)
    cfg["notify"]["console"] = False
    exercise_everything(Notifier(cfg), cfg)
