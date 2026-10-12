"""Pravidla pro vstup: denní trend + pullback na 4h + potvrzovací svíčka.

Denní trend se počítá z 4h dat (agregace na dny v UTC) a pro každou 4h
svíčku se použije jen den, který je už UZAVŘENÝ. Žádné nahlížení do budoucnosti.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .indicators import atr, ema, rsi


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class Signal:
    time: pd.Timestamp
    direction: str | None          # "long" / "short" / None
    checks: list = field(default_factory=list)
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    risk_mult: float = 1.0         # < 1 v přechodném režimu trhu

    @property
    def ok(self) -> bool:
        return self.direction is not None and all(c.passed for c in self.checks)


def prepare(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """Přidá k 4h svíčkám všechny indikátory potřebné pro vyhodnocení."""
    out = df.copy()

    daily = df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    d_ema = ema(daily["close"], cfg.trend_ema)
    d_slope = d_ema - d_ema.shift(cfg.trend_slope_days)
    d = pd.DataFrame({
        "d_close": daily["close"], "d_ema": d_ema, "d_slope": d_slope,
    })
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

    # režim: NaN (málo historie) = bez omezení
    regime = pd.Series(None, index=out.index, dtype=object)
    if cfg.regime_ema:
        known = out["r_ema"].notna() & out["r_slope"].notna()
        above = out["d_close"] > out["r_ema"]
        rising = out["r_slope"] > 0
        regime[known & above & rising] = "bull"
        regime[known & ~above & ~rising] = "bear"
        regime[known & (above != rising)] = "přechod"
    out["regime"] = regime

    out["ema_pull"] = ema(out["close"], cfg.pullback_ema)
    out["rsi"] = rsi(out["close"], cfg.rsi_period)
    out["atr"] = atr(out, cfg.atr_period)
    out["recent_low"] = out["low"].rolling(cfg.pullback_lookback).min()
    out["recent_high"] = out["high"].rolling(cfg.pullback_lookback).max()
    out["swing_low"] = out["low"].rolling(cfg.swing_lookback).min()
    out["swing_high"] = out["high"].rolling(cfg.swing_lookback).max()
    out["res_high"] = out["high"].shift(1).rolling(cfg.resistance_lookback).max()
    out["sup_low"] = out["low"].shift(1).rolling(cfg.resistance_lookback).min()
    out["prev_high"] = out["high"].shift(1)
    out["prev_low"] = out["low"].shift(1)
    return out


def _fmt(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ")


def evaluate(p: pd.DataFrame, i: int, cfg: StrategyConfig, entry: float | None = None) -> Signal:
    """Vyhodnotí uzavřenou svíčku i. `entry` = předpokládaná vstupní cena
    (v backtestu open další svíčky, živě poslední close)."""
    r = p.iloc[i]
    t = p.index[i]
    if r[["d_ema", "d_slope", "ema_pull", "atr", "res_high"]].isna().any():
        return Signal(t, None, [Check("Dost historie pro indikátory", False)])

    regime = r["regime"] if isinstance(r["regime"], str) else None
    regime_check = None
    risk_mult = 1.0
    if regime is not None:
        rdist = (r["d_close"] / r["r_ema"] - 1) * 100
        rdesc = f"cena {rdist:+.1f} % vůči EMA{cfg.regime_ema}, EMA {'roste' if r['r_slope'] > 0 else 'klesá'}"
        if regime == "přechod":
            risk_mult = cfg.regime_transition_risk
            rdesc += f" – možná změna trendu, riziko ×{risk_mult:g}"
        regime_check = Check(f"Režim trhu: {regime}", risk_mult > 0, rdesc)

    if r["trend_up"]:
        direction = "long"
    elif r["trend_down"] and cfg.allow_shorts:
        direction = "short"
    else:
        dist = (r["d_close"] / r["d_ema"] - 1) * 100
        checks = [regime_check] if regime_check else []
        return Signal(t, None, checks + [Check("Denní trend", False,
                                               f"cena {dist:+.1f} % vůči EMA{cfg.trend_ema}, sklon {'↑' if r['d_slope'] > 0 else '↓'} – bez jasného trendu")])

    if (direction == "long" and regime == "bear") or (direction == "short" and regime == "bull"):
        regime_check.passed = False
        regime_check.detail += f" – {direction} proti režimu se nebere"

    entry = float(r["close"] if entry is None else entry)
    a = r["atr"]
    checks = [regime_check] if regime_check else []
    long = direction == "long"
    dist = (r["d_close"] / r["d_ema"] - 1) * 100
    checks.append(Check("Denní trend", True,
                        f"{'nahoru' if long else 'dolů'} (cena {dist:+.1f} % vůči EMA{cfg.trend_ema}, EMA {'roste' if long else 'klesá'})"))

    if long:
        touched = r["recent_low"] <= r["ema_pull"] * (1 + cfg.pullback_tolerance)
        held = r["close"] > r["ema_pull"]
        confirm = r["close"] > r["open"] and r["close"] > r["prev_high"]
        lo, hi = cfg.rsi_long_range
        stop = r["swing_low"] - cfg.stop_buffer_atr * a
        risk = entry - stop
        target = entry + cfg.target_r * risk
        level, level_name = r["res_high"], "rezistence"
        blocked = entry < level < target
    else:
        touched = r["recent_high"] >= r["ema_pull"] * (1 - cfg.pullback_tolerance)
        held = r["close"] < r["ema_pull"]
        confirm = r["close"] < r["open"] and r["close"] < r["prev_low"]
        lo, hi = cfg.rsi_short_range
        stop = r["swing_high"] + cfg.stop_buffer_atr * a
        risk = stop - entry
        target = entry - cfg.target_r * risk
        level, level_name = r["sup_low"], "support"
        blocked = target < level < entry

    checks.append(Check(f"Pullback k EMA{cfg.pullback_ema} (4h)", bool(touched),
                        f"EMA{cfg.pullback_ema} = {_fmt(r['ema_pull'])}"))
    checks.append(Check(f"Close {'nad' if long else 'pod'} EMA{cfg.pullback_ema}", bool(held),
                        f"close {_fmt(r['close'])}"))
    checks.append(Check("Potvrzovací svíčka", bool(confirm),
                        f"{'býčí' if long else 'medvědí'} svíčka za {'high' if long else 'low'} předchozí"))
    checks.append(Check(f"RSI v pásmu {lo}–{hi}", bool(lo <= r["rsi"] <= hi), f"RSI {r['rsi']:.0f}"))

    stop_atr = risk / a if a > 0 else np.nan
    checks.append(Check(f"Stop {cfg.min_stop_atr}–{cfg.max_stop_atr} ATR", bool(cfg.min_stop_atr <= stop_atr <= cfg.max_stop_atr),
                        f"stop {_fmt(stop)} ({risk / entry * 100:.1f} %, {stop_atr:.1f} ATR)"))
    checks.append(Check(f"Volná cesta k cíli {cfg.target_r:g}R", not blocked,
                        f"{level_name} {_fmt(level)}" + (" leží před cílem" if blocked else "")))

    return Signal(t, direction, checks, entry, float(stop), float(target), risk_mult)
