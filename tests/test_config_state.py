"""Yapilandirma dogrulama ve durum kaliciligi testleri."""

import copy
import json

import pytest
import yaml

from bot.config import DEFAULTS, ConfigError, load_config, validate
from bot.risk import ExitEvent, Position
from bot.state import State
from bot.strategy import LONG


def write_cfg(tmp_path, overrides=None):
    data = copy.deepcopy(DEFAULTS)
    if overrides:
        for path, value in overrides.items():
            node = data
            keys = path.split(".")
            for k in keys[:-1]:
                node = node[k]
            node[keys[-1]] = value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_missing_file_gives_actionable_message(tmp_path):
    with pytest.raises(ConfigError, match="config.example.yaml"):
        load_config(tmp_path / "yok.yaml")


def test_defaults_are_valid():
    validate(copy.deepcopy(DEFAULTS))


def test_user_values_override_defaults(tmp_path):
    cfg = load_config(write_cfg(tmp_path, {"risk.equity_usd": 250.0}))
    assert cfg["risk"]["equity_usd"] == 250.0
    assert cfg["strategy"]["ema_trend"] == DEFAULTS["strategy"]["ema_trend"]


def test_partial_config_is_filled_from_defaults(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"risk": {"equity_usd": 42.0}}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg["risk"]["equity_usd"] == 42.0
    assert cfg["risk"]["take_profit_r"] == DEFAULTS["risk"]["take_profit_r"]
    assert cfg["execution"]["mode"] == "paper"


def test_api_keys_come_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "anahtar")
    monkeypatch.setenv("BINANCE_API_SECRET", "gizli")
    cfg = load_config(write_cfg(tmp_path))
    assert cfg["exchange"]["api_key"] == "anahtar"
    assert cfg["exchange"]["api_secret"] == "gizli"


@pytest.mark.parametrize(
    "override, message",
    [
        ({"execution.mode": "turbo"}, "gecersiz"),
        ({"risk.equity_usd": 0}, "pozitif"),
        ({"risk.risk_per_trade_pct": 150}, "0 ile 100"),
        ({"risk.min_stop_pct": 20.0}, "kucuk olmali"),
        ({"risk.liquidation_buffer": 1.5}, "0 ile 1"),
        ({"strategy.macd": [26, 12, 9]}, "kucuk olmali"),
        ({"strategy.allow_long": False, "strategy.allow_short": False}, "ayni anda kapali"),
        ({"strategy.rsi_long_band": [70, 45]}, "alt < ust"),
        ({"timeframes.history_bars": 50}, "history_bars en az"),
    ],
)
def test_bad_values_are_rejected(tmp_path, override, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write_cfg(tmp_path, override))


def test_live_mode_requires_api_keys(tmp_path, monkeypatch):
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    with pytest.raises(ConfigError, match="API anahtari gerekli"):
        load_config(write_cfg(tmp_path, {"execution.mode": "live"}))


# --------------------------------------------------------------------- durum

def make_position():
    return Position(
        symbol="BTC/USDT", side=LONG, entry=100.0, qty=0.5, notional=50.0,
        leverage=5, margin=10.0, initial_stop=95.0, stop=95.0, take_profit=115.0,
        risk_per_unit=5.0, opened_at="2025-01-01T00:00:00+00:00",
        opened_bar="2025-01-01T00:00:00+00:00", peak=100.0,
    )


def test_state_survives_restart(tmp_path):
    path = tmp_path / "state.json"
    state = State(path)
    state.ensure_equity(5.0)
    pos = make_position()
    pos.stop = 103.0
    pos.trailing_active = True
    state.put_position(pos)
    state.save()

    reloaded = State(path)
    restored = reloaded.get_position("BTC/USDT")
    assert restored is not None
    assert restored.stop == 103.0            # iz suren stop unutulmadi
    assert restored.trailing_active is True
    assert reloaded.equity == 5.0


def test_recording_a_trade_updates_equity_and_daily_pnl(tmp_path):
    state = State(tmp_path / "state.json")
    state.ensure_equity(100.0)
    ev = ExitEvent("Kar al hedefi", 115.0, 3.0, 7.5, 0.5, 7.0)
    state.record_trade(make_position(), ev, "2025-03-04T10:00:00+00:00")
    assert state.equity == pytest.approx(107.0)
    assert state.day_pnl("2025-03-04") == pytest.approx(7.0)
    assert state.stats()["count"] == 1
    assert state.stats()["win_rate"] == 100.0


def test_corrupt_state_is_backed_up_not_silently_lost(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{bozuk json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="bozuk"):
        State(path)
    assert path.with_suffix(".corrupt").exists()


def test_save_is_atomic_and_valid_json(tmp_path):
    path = tmp_path / "state.json"
    state = State(path)
    state.ensure_equity(5.0)
    state.save()
    assert json.loads(path.read_text(encoding="utf-8"))["equity"] == 5.0
    assert not list(tmp_path.glob("*.tmp"))


def test_halt_and_resume(tmp_path):
    state = State(tmp_path / "state.json")
    assert not state.halted
    state.halt("gunluk zarar")
    assert state.halted
    state.resume()
    assert not state.halted


def test_cooldown_blocks_then_expires(tmp_path):
    state = State(tmp_path / "state.json")
    state.set_cooldown("BTC/USDT", "2025-01-02T00:00:00+00:00")
    assert state.in_cooldown("BTC/USDT", "2025-01-01T12:00:00+00:00")
    assert not state.in_cooldown("BTC/USDT", "2025-01-03T00:00:00+00:00")
    assert not state.in_cooldown("ETH/USDT", "2025-01-01T12:00:00+00:00")


# ----------------------------------------------------------- CSV yol acilimi

def test_csv_paths_accept_files_folders_and_wildcards(tmp_path):
    """PowerShell jokerleri acmadan gecirir; genisletmeyi bot yapmali."""
    from bot.cli import expand_csv_paths

    data = tmp_path / "data"
    data.mkdir()
    for name in ("SOL_USDT_4h.csv", "AVAX_USDT_4h.csv"):
        (data / name).write_text("timestamp,open,high,low,close,volume\n", encoding="utf-8")
    (data / "notlar.txt").write_text("csv degil", encoding="utf-8")

    from_folder = expand_csv_paths([str(data)])
    from_glob = expand_csv_paths([str(data / "*.csv")])
    from_files = expand_csv_paths([str(data / "SOL_USDT_4h.csv")])

    assert [p.name for p in from_folder] == ["AVAX_USDT_4h.csv", "SOL_USDT_4h.csv"]
    assert from_glob == from_folder
    assert [p.name for p in from_files] == ["SOL_USDT_4h.csv"]


def test_csv_paths_do_not_repeat_the_same_file(tmp_path):
    from bot.cli import expand_csv_paths

    path = tmp_path / "SOL_USDT_4h.csv"
    path.write_text("timestamp,open,high,low,close,volume\n", encoding="utf-8")
    assert len(expand_csv_paths([str(path), str(tmp_path / "*.csv"), str(tmp_path)])) == 1


def test_missing_csv_tells_the_user_how_to_get_data(tmp_path):
    from bot.cli import expand_csv_paths

    with pytest.raises(ConfigError, match="run.py fetch"):
        expand_csv_paths([str(tmp_path / "yok" / "*.csv")])
