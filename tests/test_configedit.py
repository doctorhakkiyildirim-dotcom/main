"""Ayar duzenleme testleri.

Amac: kullanicinin config.yaml'i elle acmasina gerek kalmamasi — ve ayar
degisirken dosyadaki aciklama satirlarinin kaybolmamasi.
"""

import copy

import pytest
import yaml

from bot.cli import build_parser, cmd_setup
from bot.config import DEFAULTS
from bot.configedit import ConfigEditError, apply_changes, format_value, read_scalar, set_scalar

SAMPLE = """# Ust aciklama
exchange:
  id: binance              # borsa
  testnet: true            # sahte para
  quote: USDT

risk:
  equity_usd: 5.0          # baslangic sermayen
  risk_per_trade_pct: 20.0
  max_leverage: 10

execution:
  mode: paper              # signal | paper | live
"""


# ------------------------------------------------------------------ bicimleme

@pytest.mark.parametrize(
    "value, expected",
    [(True, "true"), (False, "false"), (3, "3"), (3.5, "3.5"), ("paper", "paper")],
)
def test_values_are_written_in_yaml_form(value, expected):
    assert format_value(value) == expected


def test_values_that_would_break_yaml_are_quoted():
    assert format_value("a # b").startswith('"')
    assert format_value(" bosluklu ").startswith('"')


# -------------------------------------------------------------- satir degisme

def test_changing_a_value_keeps_the_comment_on_that_line():
    out = set_scalar(SAMPLE, "exchange", "testnet", False)
    assert "testnet: false            # sahte para" in out


def test_changing_a_value_keeps_every_other_line_intact():
    out = set_scalar(SAMPLE, "risk", "equity_usd", 3.0)
    assert "# Ust aciklama" in out
    assert "id: binance              # borsa" in out
    assert out.count("\n") == SAMPLE.count("\n")


def test_result_is_still_valid_yaml_with_the_new_value():
    out = set_scalar(set_scalar(SAMPLE, "exchange", "testnet", False),
                     "risk", "equity_usd", 3.0)
    data = yaml.safe_load(out)
    assert data["exchange"]["testnet"] is False
    assert data["risk"]["equity_usd"] == 3.0
    assert data["execution"]["mode"] == "paper"


def test_same_key_in_another_section_is_not_touched():
    text = "a:\n  mode: one\n\nb:\n  mode: two\n"
    out = set_scalar(text, "b", "mode", "three")
    assert yaml.safe_load(out) == {"a": {"mode": "one"}, "b": {"mode": "three"}}


def test_windows_line_endings_are_preserved():
    """Notepad ile acilan dosya bozulmamali."""
    crlf = SAMPLE.replace("\n", "\r\n")
    out = set_scalar(crlf, "risk", "equity_usd", 3.0)
    assert out.count("\r\n") == crlf.count("\r\n")
    assert "\r\r" not in out and "\n\n" not in out
    assert yaml.safe_load(out)["risk"]["equity_usd"] == 3.0


def test_missing_setting_is_reported_clearly():
    with pytest.raises(ConfigEditError, match="risk.yok_boyle"):
        set_scalar(SAMPLE, "risk", "yok_boyle", 1)


def test_reading_back_the_current_value():
    assert read_scalar(SAMPLE, "exchange", "testnet") == "true"
    assert read_scalar(SAMPLE, "risk", "equity_usd") == "5.0"
    assert read_scalar(SAMPLE, "risk", "yok_boyle") is None


def test_a_failing_change_leaves_the_file_untouched(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(SAMPLE, encoding="utf-8")
    with pytest.raises(ConfigEditError):
        apply_changes(path, {("risk", "equity_usd"): 3.0, ("risk", "yok"): 1})
    assert path.read_text(encoding="utf-8") == SAMPLE


# ------------------------------------------------------------- ayarla komutu

def write_full_config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(copy.deepcopy(DEFAULTS), sort_keys=False), encoding="utf-8")
    return path


def run_setup(path, argv):
    args = build_parser().parse_args(["-c", str(path), "ayarla", *argv])
    return cmd_setup(args)


def test_setup_writes_the_values_it_is_given(tmp_path, capsys):
    path = write_full_config(tmp_path)
    assert run_setup(path, ["--equity", "3", "--no-testnet", "--mode", "paper"]) == 0

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data["risk"]["equity_usd"] == 3.0
    assert data["exchange"]["testnet"] is False
    assert data["execution"]["mode"] == "paper"
    assert "GERCEK EMIR GONDERMEZ" in capsys.readouterr().out


def test_setup_only_changes_what_was_asked_for(tmp_path):
    path = write_full_config(tmp_path)
    before = yaml.safe_load(path.read_text(encoding="utf-8"))
    run_setup(path, ["--equity", "42"])

    after = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert after["risk"]["equity_usd"] == 42.0
    after["risk"]["equity_usd"] = before["risk"]["equity_usd"]
    assert after == before


def test_setup_rejects_and_restores_on_invalid_values(tmp_path, capsys):
    path = write_full_config(tmp_path)
    original = path.read_text(encoding="utf-8")
    assert run_setup(path, ["--risk", "500"]) == 1
    assert path.read_text(encoding="utf-8") == original
    assert "geri alindi" in capsys.readouterr().out


def test_setup_warns_when_testnet_stays_on(tmp_path, capsys):
    path = write_full_config(tmp_path)
    run_setup(path, ["--testnet", "--mode", "paper"])
    assert "gercek piyasa degil" in capsys.readouterr().out


def test_setup_creates_the_file_from_the_example_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.example.yaml").write_text(
        yaml.safe_dump(copy.deepcopy(DEFAULTS), sort_keys=False), encoding="utf-8")
    target = tmp_path / "config.yaml"
    assert run_setup(target, ["--equity", "3"]) == 0
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["risk"]["equity_usd"] == 3.0
