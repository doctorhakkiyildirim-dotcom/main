"""Yapilandirma yukleme ve dogrulama."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "exchange": {
        "id": "binance",
        "market_type": "future",
        "testnet": True,
        "quote": "USDT",
        "api_key": "",
        "api_secret": "",
    },
    "universe": {
        "mode": "auto",
        "manual_symbols": ["BTC/USDT", "ETH/USDT"],
        "top_n_by_volume": 40,
        "min_24h_volume_usd": 50_000_000,
        "exclude": ["USDC/USDT", "FDUSD/USDT", "TUSD/USDT", "DAI/USDT"],
    },
    "timeframes": {"signal": "4h", "trend": "1d", "history_bars": 400},
    "strategy": {
        "ema_fast": 20,
        "ema_slow": 50,
        "ema_trend": 200,
        "macd": [12, 26, 9],
        "macd_cross_lookback": 3,
        "rsi_period": 14,
        "rsi_long_band": [45, 70],
        "rsi_short_band": [30, 55],
        "adx_period": 14,
        "adx_min": 20.0,
        "atr_period": 14,
        "volume_ma": 20,
        "min_volume_ratio": 0.8,
        "allow_long": True,
        "allow_short": True,
        "min_score": 60.0,
    },
    "risk": {
        "equity_usd": 5.0,
        "risk_per_trade_pct": 20.0,
        "max_open_positions": 1,
        "margin_alloc_pct": 90.0,
        "atr_stop_mult": 2.5,
        "min_stop_pct": 2.0,
        "max_stop_pct": 12.0,
        "take_profit_r": 3.0,
        "breakeven_at_r": 1.0,
        "trail_start_r": 1.0,
        "trail_atr_mult": 3.0,
        "time_stop_bars": 42,
        "time_stop_min_r": 0.3,
        "max_leverage": 10,
        "liquidation_buffer": 0.6,
        "daily_max_loss_pct": 40.0,
        "cooldown_bars_after_loss": 6,
    },
    "fees": {
        "taker_pct": 0.05,
        "maker_pct": 0.02,
        "flat_fee_usd": 0.0,
        "slippage_pct": 0.03,
        "min_profit_to_fee_ratio": 4.0,
    },
    "execution": {
        "mode": "paper",
        "poll_seconds": 300,
        "confirm_on_closed_bar": True,
        "min_notional_usd": 5.0,
    },
    "notify": {
        "console": True,
        "sound": True,
        "logfile": "logs/signals.log",
        "telegram": {"enabled": False, "bot_token": "", "chat_id": ""},
    },
    "state_file": "state/state.json",
}

VALID_MODES = {"signal", "paper", "live"}


class ConfigError(Exception):
    """Yapilandirma hatasi."""


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _apply_env_overrides(cfg: dict) -> dict:
    """API anahtarlarini ortam degiskenlerinden al (dosyaya yazmaktan guvenli)."""
    prefix = str(cfg["exchange"]["id"]).upper()
    for env_name, path in (
        (f"{prefix}_API_KEY", ("exchange", "api_key")),
        (f"{prefix}_API_SECRET", ("exchange", "api_secret")),
        ("TELEGRAM_BOT_TOKEN", ("notify", "telegram", "bot_token")),
        ("TELEGRAM_CHAT_ID", ("notify", "telegram", "chat_id")),
    ):
        value = os.environ.get(env_name)
        if not value:
            continue
        node = cfg
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = value
    return cfg


def load_config(path: str | Path = "config.yaml") -> dict:
    """config.yaml'i oku, varsayilanlarla birlestir, dogrula."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"'{path}' bulunamadi. Once ornegi kopyala:\n"
            f"    cp config.example.yaml config.yaml"
        )
    with path.open("r", encoding="utf-8") as fh:
        user_cfg = yaml.safe_load(fh) or {}
    if not isinstance(user_cfg, dict):
        raise ConfigError(f"'{path}' gecerli bir YAML sozlugu degil.")

    cfg = _apply_env_overrides(_deep_merge(DEFAULTS, user_cfg))
    validate(cfg)
    return cfg


def validate(cfg: dict) -> None:
    """Mantik hatalarini calistirmadan once yakala."""
    mode = cfg["execution"]["mode"]
    if mode not in VALID_MODES:
        raise ConfigError(f"execution.mode '{mode}' gecersiz. Sec: {sorted(VALID_MODES)}")

    risk = cfg["risk"]
    if risk["equity_usd"] <= 0:
        raise ConfigError("risk.equity_usd pozitif olmali.")
    if not 0 < risk["risk_per_trade_pct"] <= 100:
        raise ConfigError("risk.risk_per_trade_pct 0 ile 100 arasinda olmali.")
    if not 0 < risk["margin_alloc_pct"] <= 100:
        raise ConfigError("risk.margin_alloc_pct 0 ile 100 arasinda olmali.")
    if risk["min_stop_pct"] >= risk["max_stop_pct"]:
        raise ConfigError("risk.min_stop_pct, max_stop_pct'ten kucuk olmali.")
    if risk["take_profit_r"] <= 0:
        raise ConfigError("risk.take_profit_r pozitif olmali.")
    if risk["max_leverage"] < 1:
        raise ConfigError("risk.max_leverage en az 1 olmali.")
    if not 0 < risk["liquidation_buffer"] < 1:
        raise ConfigError("risk.liquidation_buffer 0 ile 1 arasinda olmali.")
    if risk["max_open_positions"] < 1:
        raise ConfigError("risk.max_open_positions en az 1 olmali.")

    strat = cfg["strategy"]
    if len(strat["macd"]) != 3:
        raise ConfigError("strategy.macd uc deger icermeli: [hizli, yavas, sinyal]")
    if strat["macd"][0] >= strat["macd"][1]:
        raise ConfigError("strategy.macd: hizli periyot yavas periyottan kucuk olmali.")
    if not strat["allow_long"] and not strat["allow_short"]:
        raise ConfigError("allow_long ve allow_short ayni anda kapali olamaz.")
    for band_name in ("rsi_long_band", "rsi_short_band"):
        band = strat[band_name]
        if len(band) != 2 or band[0] >= band[1]:
            raise ConfigError(f"strategy.{band_name} [alt, ust] ve alt < ust olmali.")

    tf = cfg["timeframes"]
    needed = max(strat["ema_trend"], strat["macd"][1] + strat["macd"][2]) + 50
    if tf["history_bars"] < needed:
        raise ConfigError(
            f"timeframes.history_bars en az {needed} olmali "
            f"(EMA{strat['ema_trend']} icin yeterli veri gerekiyor)."
        )

    if mode == "live":
        ex = cfg["exchange"]
        if not ex["api_key"] or not ex["api_secret"]:
            raise ConfigError(
                "live modu icin API anahtari gerekli. Ortam degiskenlerini ayarla:\n"
                f"    {str(ex['id']).upper()}_API_KEY / {str(ex['id']).upper()}_API_SECRET"
            )


def summary_lines(cfg: dict) -> list[str]:
    """Baslangicta ekrana basilacak ozet."""
    ex, risk, ex_cfg = cfg["exchange"], cfg["risk"], cfg["execution"]
    net = "TESTNET" if ex["testnet"] else "GERCEK PIYASA"
    return [
        f"Borsa       : {ex['id']} / {ex['market_type']} ({net})",
        f"Mod         : {ex_cfg['mode'].upper()}",
        f"Zaman dilimi: sinyal={cfg['timeframes']['signal']}  trend={cfg['timeframes']['trend']}",
        f"Sermaye     : {risk['equity_usd']:.2f} USDT",
        f"Islem riski : %{risk['risk_per_trade_pct']:.1f}  "
        f"(hedef {risk['take_profit_r']:.1f}R, maks kaldirac {risk['max_leverage']}x)",
    ]
