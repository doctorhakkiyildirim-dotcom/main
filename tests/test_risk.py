"""Risk yonetimi testleri — botun para kaybettirmemesi icin en kritik kisim."""

import copy

import pandas as pd
import pytest

from bot.config import DEFAULTS
from bot.risk import (
    Position, build_plan, check_exit, min_notional_for_fee_ratio, round_fees,
)
from bot.strategy import LONG, SHORT, Signal


@pytest.fixture
def cfg():
    return copy.deepcopy(DEFAULTS)


def make_signal(side=LONG, price=100.0, atr=3.0):
    return Signal(symbol="TEST/USDT", side=side, score=80.0,
                  bar_time=pd.Timestamp("2025-01-01", tz="UTC"), price=price, atr=atr)


# ------------------------------------------------------------------ boyutlama

def test_plan_has_stop_and_target_on_correct_sides(cfg):
    long_plan = build_plan(make_signal(LONG), 100.0, cfg)
    assert long_plan.ok
    assert long_plan.stop < long_plan.entry < long_plan.take_profit

    short_plan = build_plan(make_signal(SHORT), 100.0, cfg)
    assert short_plan.ok
    assert short_plan.take_profit < short_plan.entry < short_plan.stop


def test_reward_is_take_profit_r_times_risk(cfg):
    plan = build_plan(make_signal(), 100.0, cfg)
    reward = abs(plan.take_profit - plan.entry)
    risk = abs(plan.entry - plan.stop)
    assert reward / risk == pytest.approx(cfg["risk"]["take_profit_r"], rel=1e-9)


def test_stop_distance_is_clamped_between_min_and_max(cfg):
    tight = build_plan(make_signal(atr=0.01), 100.0, cfg)   # cok dar ATR
    wide = build_plan(make_signal(atr=50.0), 100.0, cfg)    # cok genis ATR
    assert tight.stop_pct == pytest.approx(cfg["risk"]["min_stop_pct"])
    assert wide.stop_pct == pytest.approx(cfg["risk"]["max_stop_pct"])


@pytest.mark.parametrize("side", [LONG, SHORT])
@pytest.mark.parametrize("atr", [1.0, 2.0, 3.5, 6.0])
def test_stop_always_triggers_before_liquidation(cfg, side, atr):
    plan = build_plan(make_signal(side, atr=atr), 50.0, cfg)
    if not plan.ok:
        pytest.skip(plan.reject_reason)
    if side == LONG:
        assert plan.stop > plan.liq_price
    else:
        assert plan.stop < plan.liq_price


def test_leverage_never_exceeds_configured_or_exchange_cap(cfg):
    cfg["risk"]["max_leverage"] = 5
    plan = build_plan(make_signal(atr=1.0), 1000.0, cfg, exchange_max_leverage=3)
    if plan.ok:
        assert plan.leverage <= 3


def test_realised_risk_matches_target_when_size_is_not_clamped(cfg):
    equity = 5000.0
    plan = build_plan(make_signal(), equity, cfg)
    assert plan.ok
    assert plan.risk_pct == pytest.approx(cfg["risk"]["risk_per_trade_pct"], rel=1e-6)
    assert plan.risk_usd == pytest.approx(equity * 0.20, rel=1e-6)


def test_spot_market_forces_no_leverage(cfg):
    cfg["exchange"]["market_type"] = "spot"
    plan = build_plan(make_signal(), 500.0, cfg)
    assert plan.ok
    assert plan.leverage == 1


def test_tiny_account_below_exchange_minimum_is_rejected(cfg):
    plan = build_plan(make_signal(), 0.5, cfg, exchange_min_notional=20.0)
    assert not plan.ok
    assert "Sermaye yetersiz" in plan.reject_reason


# ---------------------------------------------------------------- masraflar

def test_flat_fee_blocks_tiny_positions(cfg):
    cfg["fees"]["flat_fee_usd"] = 3.0
    plan = build_plan(make_signal(), 5.0, cfg)
    assert not plan.ok
    assert "Masraf cok agir" in plan.reject_reason
    assert "USDT'lik pozisyon gerekir" in plan.reject_reason


def test_flat_fee_allows_large_enough_positions(cfg):
    cfg["fees"]["flat_fee_usd"] = 3.0
    plan = build_plan(make_signal(), 2000.0, cfg)
    assert plan.ok
    assert plan.fee_ratio >= cfg["fees"]["min_profit_to_fee_ratio"]
    assert plan.expected_net > 0


def _achieved_ratio(notional, stop_pct, tp_r, fees):
    return (notional * stop_pct * tp_r / 100.0) / round_fees(notional, fees)


def test_min_notional_for_fee_ratio_is_exactly_the_break_even_size(cfg):
    cfg["fees"]["flat_fee_usd"] = 3.0
    stop_pct, tp_r = 7.5, cfg["risk"]["take_profit_r"]
    target = cfg["fees"]["min_profit_to_fee_ratio"]

    needed = min_notional_for_fee_ratio(stop_pct, tp_r, cfg["fees"])
    assert _achieved_ratio(needed, stop_pct, tp_r, cfg["fees"]) == pytest.approx(target, rel=1e-6)
    # Bir tik altinda kural saglanmamali, ustunde saglanmali
    assert _achieved_ratio(needed * 0.9, stop_pct, tp_r, cfg["fees"]) < target
    assert _achieved_ratio(needed * 1.1, stop_pct, tp_r, cfg["fees"]) > target


def test_min_notional_is_zero_when_only_percentage_fees_apply(cfg):
    cfg["fees"]["flat_fee_usd"] = 0.0
    assert min_notional_for_fee_ratio(7.5, 3.0, cfg["fees"]) == 0.0


def test_min_notional_is_infinite_when_percentage_fees_eat_the_target(cfg):
    cfg["fees"].update({"flat_fee_usd": 3.0, "taker_pct": 5.0, "slippage_pct": 1.0})
    assert min_notional_for_fee_ratio(1.0, 1.0, cfg["fees"]) == float("inf")


def test_round_fees_counts_both_legs(cfg):
    cfg["fees"].update({"taker_pct": 0.05, "slippage_pct": 0.0, "flat_fee_usd": 0.0})
    assert round_fees(1000.0, cfg["fees"]) == pytest.approx(1.0)


# --------------------------------------------------------------- iz suren stop

def make_position(cfg, side=LONG, entry=100.0, stop=95.0):
    plan = build_plan(make_signal(side, price=entry, atr=2.0), 1000.0, cfg)
    pos = Position.from_plan(plan, "2025-01-01T00:00:00+00:00", "test")
    pos.stop = stop
    pos.initial_stop = stop
    pos.risk_per_unit = abs(entry - stop)
    pos.take_profit = entry + (entry - stop) * 3 * pos.direction
    return pos


def test_stop_moves_to_breakeven_after_one_r(cfg):
    pos = make_position(cfg)
    pos.update_trailing(bar_high=105.0, bar_low=99.0, atr_value=1.0, cfg=cfg)
    assert pos.breakeven_done
    assert pos.stop >= pos.entry
    assert pos.locked_r() >= 0


def test_trailing_stop_only_ratchets_upward_for_long(cfg):
    pos = make_position(cfg)
    pos.update_trailing(120.0, 110.0, 2.0, cfg)
    after_run = pos.stop
    pos.update_trailing(112.0, 108.0, 2.0, cfg)   # fiyat geri geldi
    assert pos.stop == after_run                   # stop ASLA geri gitmez


def test_trailing_stop_only_ratchets_downward_for_short(cfg):
    pos = make_position(cfg, side=SHORT, entry=100.0, stop=105.0)
    pos.update_trailing(90.0, 80.0, 2.0, cfg)
    after_run = pos.stop
    pos.update_trailing(92.0, 88.0, 2.0, cfg)
    assert pos.stop == after_run
    assert pos.stop < pos.entry


def test_breakeven_price_covers_fees(cfg):
    pos = make_position(cfg)
    be = pos.breakeven_price(cfg["fees"])
    assert be > pos.entry
    gross = (be - pos.entry) * pos.qty
    assert gross == pytest.approx(round_fees(pos.notional, cfg["fees"]), rel=1e-6)


# --------------------------------------------------------------------- cikis

def test_stop_wins_when_bar_touches_both_stop_and_target(cfg):
    pos = make_position(cfg)
    ev = check_exit(pos, bar_high=200.0, bar_low=1.0, bar_close=150.0, cfg=cfg)
    assert ev is not None
    assert ev.price == pos.stop
    assert ev.r_multiple == pytest.approx(-1.0, abs=1e-9)


def test_take_profit_exit_is_reported(cfg):
    pos = make_position(cfg)
    ev = check_exit(pos, bar_high=pos.take_profit + 1, bar_low=99.0, bar_close=115.0, cfg=cfg)
    assert ev is not None and ev.reason == "Kar al hedefi"
    assert ev.r_multiple == pytest.approx(3.0, rel=1e-6)


def test_no_exit_inside_the_range(cfg):
    pos = make_position(cfg)
    assert check_exit(pos, 104.0, 96.0, 100.0, cfg) is None


def test_time_stop_closes_stagnant_trade(cfg):
    pos = make_position(cfg)
    pos.bars_held = cfg["risk"]["time_stop_bars"]
    ev = check_exit(pos, 101.0, 99.5, 100.2, cfg)
    assert ev is not None and "Sure doldu" in ev.reason


def test_trend_break_exit_uses_close_price(cfg):
    pos = make_position(cfg)
    ev = check_exit(pos, 101.0, 99.0, 100.5, cfg, trend_broken="Trend bozuldu")
    assert ev is not None and ev.price == 100.5


def test_net_pnl_is_gross_minus_fees(cfg):
    pos = make_position(cfg)
    ev = check_exit(pos, pos.take_profit + 1, 99.0, 115.0, cfg)
    assert ev.net_usd == pytest.approx(ev.gross_usd - ev.fee_usd)
