"""Kontext trhu na 4h svíčkách pro `review` a `edge`: denní trend, režim, EMA20, RSI, ATR.

Denní hodnoty se počítají z 4h dat (agregace na dny v UTC) a pro každou 4h
svíčku se použije jen den, který je už UZAVŘENÝ. Žádné nahlížení do budoucnosti.
"""
from __future__ import annotations

import pandas as pd

from .config import MarketConfig
from .indicators import atr, ema, rsi


def prepare_4h(df: pd.DataFrame, cfg: MarketConfig) -> pd.DataFrame:
    out = df.copy()

    daily = df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    d_ema = ema(daily["close"], cfg.trend_ema)
    d = pd.DataFrame({"d_close": daily["close"], "d_ema": d_ema,
                      "d_slope": d_ema - d_ema.shift(cfg.trend_slope_days)})
    if cfg.regime_ema:
        r_ema = ema(daily["close"], cfg.regime_ema)
        d["r_ema"] = r_ema
        d["r_slope"] = r_ema - r_ema.shift(cfg.regime_slope_days)
    d = d.shift(1)  # hodnota uzavřeného dne D platí až pro svíčky dne D+1
    day = out.index.floor("1D")
    for col in d.columns:
        out[col] = d[col].reindex(day).to_numpy()

    out["trend_up"] = (out["d_close"] > out["d_ema"]) & (out["d_slope"] > 0)
    out["trend_down"] = (out["d_close"] < out["d_ema"]) & (out["d_slope"] < 0)

    # režim: NaN (málo historie) = neznámý
    regime = pd.Series(None, index=out.index, dtype=object)
    if cfg.regime_ema:
        known = out["r_ema"].notna() & out["r_slope"].notna()
        above = out["d_close"] > out["r_ema"]
        rising = out["r_slope"] > 0
        regime[known & above & rising] = "bull"
        regime[known & ~above & ~rising] = "bear"
        regime[known & (above != rising)] = "přechod"
    out["regime"] = regime

    out["ema_fast"] = ema(out["close"], cfg.fast_ema)
    out["rsi"] = rsi(out["close"], cfg.rsi_period)
    out["atr"] = atr(out, cfg.atr_period)
    return out
