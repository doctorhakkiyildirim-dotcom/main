"""Backtest butunlugu — muhasebe tutarli mi, gelecege bakis var mi."""

import copy

import pytest

from bot.backtest import run_backtest
from bot.config import DEFAULTS
from bot.datafeed import resample, synthetic
from bot.strategy import build_features


def make_frames(count=3, bars=2500, seed0=1, cfg=None):
    cfg = cfg or DEFAULTS
    frames = {}
    for i in range(count):
        ltf = synthetic(bars=bars, timeframe=cfg["timeframes"]["signal"], seed=seed0 + i)
        htf = resample(ltf, cfg["timeframes"]["trend"])
        frames[f"S{i}/USDT"] = build_features(ltf, htf, cfg["strategy"])
    return frames


@pytest.fixture(scope="module")
def cfg():
    c = copy.deepcopy(DEFAULTS)
    c["fees"]["flat_fee_usd"] = 0.0
    return c


@pytest.fixture(scope="module")
def result(cfg):
    return run_backtest(make_frames(cfg=cfg), cfg, start_equity=1000.0)


def test_backtest_produces_trades(result):
    assert result.summary()["count"] > 0


def test_equity_equals_start_plus_all_net_results(result):
    total = sum(t["net_usd"] for t in result.trades)
    assert result.end_equity == pytest.approx(result.start_equity + total, rel=1e-9)


def test_every_trade_accounts_for_fees(result):
    for t in result.trades:
        assert t["fee_usd"] > 0
        assert t["net_usd"] == pytest.approx(t["gross_usd"] - t["fee_usd"], rel=1e-9)


def test_losses_are_capped_near_one_r(result):
    """Stop calisiyorsa hicbir islem -1R'den belirgin kotu bitmemeli."""
    worst = min(t["r_multiple"] for t in result.trades)
    assert worst >= -1.05


def test_position_limit_is_respected(cfg):
    tight = copy.deepcopy(cfg)
    tight["risk"]["max_open_positions"] = 1
    res = run_backtest(make_frames(count=4, cfg=tight), tight, start_equity=1000.0)
    spans = sorted((t["opened_at"], t["closed_at"]) for t in res.trades)
    for (_, prev_close), (next_open, _) in zip(spans, spans[1:]):
        assert next_open >= prev_close    # ust uste binen pozisyon yok


def test_entry_price_is_the_bar_after_the_signal(cfg):
    """Giris, sinyal barinin KAPANISI degil, sonraki barin ACILISI olmali."""
    frames = make_frames(count=1, cfg=cfg)
    res = run_backtest(frames, cfg, start_equity=1000.0)
    assert res.trades
    df = next(iter(frames.values()))
    for t in res.trades[:10]:
        bar = df[df["ts"].astype(str) == t["opened_at"]]
        assert not bar.empty
        assert t["entry"] == pytest.approx(float(bar["open"].iloc[0]))


def test_compounding_changes_the_outcome(cfg):
    frames = make_frames(cfg=cfg)
    fixed = run_backtest(frames, cfg, start_equity=1000.0, compound=False)
    grown = run_backtest(frames, cfg, start_equity=1000.0, compound=True)
    assert fixed.end_equity != grown.end_equity


def test_flat_fee_stops_a_tiny_account_from_trading(cfg):
    pricey = copy.deepcopy(cfg)
    pricey["fees"]["flat_fee_usd"] = 3.0
    res = run_backtest(make_frames(cfg=pricey), pricey, start_equity=5.0)
    assert res.summary()["count"] == 0
    assert any("Masraf" in reason for reason in res.rejected)


def test_blown_account_stops_the_run(cfg):
    reckless = copy.deepcopy(cfg)
    reckless["risk"].update({"risk_per_trade_pct": 95.0, "max_leverage": 50,
                             "liquidation_buffer": 0.95, "daily_max_loss_pct": 100.0})
    res = run_backtest(make_frames(count=4, cfg=reckless), reckless, start_equity=10.0)
    assert res.end_equity >= 0.0


def test_empty_input_is_handled(cfg):
    res = run_backtest({}, cfg, start_equity=100.0)
    assert res.summary()["count"] == 0
    assert res.end_equity == 100.0


def test_saved_csv_can_be_read_back_identically(tmp_path):
    """fetch -> backtest --csv zinciri veriyi bozmadan tasimali."""
    from bot.datafeed import load_csv, save_csv

    original = synthetic(bars=300, timeframe="4h", seed=9)
    original.loc[original.index[-1], "closed"] = False   # yarim son bar

    path = tmp_path / "ALT_USDT_4h.csv"
    written = save_csv(original, path)
    assert written == 299, "kapanmamis bar yazilmamali"

    reloaded = load_csv(path, "4h")
    assert len(reloaded) == 299
    for col in ("open", "high", "low", "close", "volume"):
        assert reloaded[col].to_numpy() == pytest.approx(
            original[col].to_numpy()[:299], rel=1e-9
        )
    assert (reloaded["ts"].to_numpy() == original["ts"].to_numpy()[:299]).all()


def test_backtest_runs_on_reloaded_csv(tmp_path, cfg):
    """Diskten okunan veriyle backtest, bellekteki veriyle ayni sonucu vermeli."""
    from bot.datafeed import load_csv, save_csv

    raw = synthetic(bars=2500, timeframe="4h", seed=4)
    path = tmp_path / "ALT_USDT_4h.csv"
    save_csv(raw, path)

    from_disk = build_features(load_csv(path, "4h"), resample(load_csv(path, "4h"), "1d"),
                               cfg["strategy"])
    closed = raw[raw["closed"].astype(bool)].reset_index(drop=True)
    in_memory = build_features(closed, resample(closed, "1d"), cfg["strategy"])

    disk_res = run_backtest({"ALT/USDT": from_disk}, cfg, start_equity=100.0)
    mem_res = run_backtest({"ALT/USDT": in_memory}, cfg, start_equity=100.0)
    assert disk_res.summary()["count"] == mem_res.summary()["count"]
    assert disk_res.end_equity == pytest.approx(mem_res.end_equity, rel=1e-6)
