"""Trend following na denních svíčkách.

Vstup:  denní close nad N-denním maximem (long) / pod N-denním minimem (short).
Stop:   stop_atr × ATR od vstupu – pojistka, když průraz hned selže.
Výstup: bez pevného cíle. Zavírá se, až se otočí trend: close pod EMA20
        A ZÁROVEŇ EMA20 klesá za posledních N dní (short zrcadlově).

Denní svíčky se skládají ze 4h dat (UTC). Nedokončený den se zahazuje,
maximum/minimum je z PŘEDCHOZÍCH N dní – žádné nahlížení do budoucnosti.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .indicators import atr, ema


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
    risk_mult: float = 1.0

    @property
    def ok(self) -> bool:
        return self.direction is not None and all(c.passed for c in self.checks)


def to_daily(df: pd.DataFrame) -> pd.DataFrame:
    """4h svíčky -> denní (UTC). Den bez poslední svíčky 20:00 ještě není uzavřený."""
    d = df.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                               "volume": "sum"}).dropna(subset=["close"])
    if len(df) and df.index[-1].hour != 20:
        d = d.iloc[:-1]
    return d


def prepare(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """df = 4h svíčky. Vrací denní svíčky s indikátory."""
    p = to_daily(df)
    p["atr"] = atr(p, cfg.atr_days)
    p["hi"] = p["high"].rolling(cfg.entry_days).max().shift(1)
    p["lo"] = p["low"].rolling(cfg.entry_days).min().shift(1)
    p["ema"] = ema(p["close"], cfg.exit_ema)
    p["ema_slope"] = p["ema"] - p["ema"].shift(cfg.exit_slope_days)
    regime = pd.Series(None, index=p.index, dtype=object)
    if cfg.regime_ema:
        r = ema(p["close"], cfg.regime_ema)
        rs = r - r.shift(cfg.regime_slope_days)
        known = r.notna() & rs.notna()
        regime[known & (p["close"] > r) & (rs > 0)] = "bull"
        regime[known & (p["close"] < r) & (rs < 0)] = "bear"
        regime[known & regime.isna()] = "přechod"
    p["regime"] = regime
    return p


def _fmt(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ")


def entry_signal(p: pd.DataFrame, i: int, cfg: StrategyConfig, entry: float | None = None) -> Signal:
    """Vyhodnotí uzavřený den i. `entry` = předpokládaná vstupní cena
    (v backtestu open dalšího dne, živě poslední close)."""
    r = p.iloc[i]
    t = p.index[i]
    if pd.isna(r["hi"]) or pd.isna(r["lo"]) or pd.isna(r["atr"]):
        return Signal(t, None, [Check("Dost historie pro indikátory", False)])

    n = cfg.entry_days
    c = r["close"]
    if c > r["hi"]:
        direction = "long"
    elif c < r["lo"] and cfg.allow_shorts:
        direction = "short"
    else:
        up, down = (r["hi"] / c - 1) * 100, (1 - r["lo"] / c) * 100
        return Signal(t, None, [Check(f"Průraz {n}denního maxima/minima", False,
                                      f"close {_fmt(c)}; long při close nad {_fmt(r['hi'])} (+{up:.1f} %)"
                                      + (f", short pod {_fmt(r['lo'])} (−{down:.1f} %)" if cfg.allow_shorts else ""))])

    long = direction == "long"
    level = r["hi"] if long else r["lo"]
    checks = [Check(f"Průraz {n}denního {'maxima' if long else 'minima'}", True,
                    f"close {_fmt(c)} {'nad' if long else 'pod'} {_fmt(level)}")]
    regime = r["regime"] if isinstance(r["regime"], str) else None
    if regime:
        against = (long and regime == "bear") or (not long and regime == "bull")
        checks.append(Check(f"Režim trhu: {regime}", not against,
                            f"{direction} proti režimu se nebere" if against else f"EMA{cfg.regime_ema}"))

    entry = float(c if entry is None else entry)
    risk = cfg.stop_atr * r["atr"]
    stop = entry - risk if long else entry + risk
    return Signal(t, direction, checks, entry, float(stop))


def exit_signal(p: pd.DataFrame, i: int, direction: str, cfg: StrategyConfig) -> tuple[bool, list]:
    """Má se otevřená pozice zavřít po uzavření dne i? Obě podmínky musí platit."""
    r = p.iloc[i]
    long = direction == "long"
    side = "pod" if long else "nad"
    crossed = r["close"] < r["ema"] if long else r["close"] > r["ema"]
    turned = r["ema_slope"] < 0 if long else r["ema_slope"] > 0
    checks = [
        Check(f"Close {side} EMA{cfg.exit_ema}", bool(crossed), f"close {_fmt(r['close'])}, EMA {_fmt(r['ema'])}"),
        Check(f"EMA{cfg.exit_ema} {'klesá' if long else 'roste'} za {cfg.exit_slope_days} dní", bool(turned),
              f"změna {r['ema_slope'] / r['ema'] * 100:+.1f} %"),
    ]
    return bool(crossed and turned and not np.isnan(r["ema_slope"])), checks
