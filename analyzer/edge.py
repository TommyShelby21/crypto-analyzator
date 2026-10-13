"""Test indikátorů: předpovídá indikátor něco, nebo je to šum?

Pro každou 4h svíčku se nasimuluje long se stopem k ATR, cílem v R a časovým
stopem (stejně pesimisticky jako backtest). Hodnoty indikátoru se rozdělí do
5 skupin (kvintilů) – hranice jen z dat PŘED splitem, aby se nic nenaladilo
na období, podle kterého se hodnotí. Indikátor „funguje“, když se výsledek
mezi skupinami liší stejným směrem na všech coinech i v obou obdobích.

Short je zrcadlo longu: když nejvyšší skupina dává long −0,3R, short tam dává
zhruba +0,3R (minus náklady).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .review import BARS_PER_DAY, simulate


@dataclass
class Indicator:
    name: str
    desc: str
    logic: str          # proč by měl fungovat – bez důvodu indikátor netestujeme
    alts_only: bool = False


INDICATORS = [
    Indicator("momentum_20d", "výnos za posledních 20 dní",
              "trendy v kryptu přetrvávají (FOMO, pomalé šíření informací, likvidace shortů)"),
    Indicator("momentum_60d", "výnos za posledních 60 dní", "totéž na delším horizontu"),
    Indicator("kanal_20d", "pozice v 20denním rozpětí (0 = u minima, 1 = u maxima)",
              "průraz maxima spouští stopy shortů a čekající nákupy (breakout)"),
    Indicator("od_ema20_atr", "vzdálenost close od EMA20 (4h) v ATR",
              "pullback vs. momentum – vstup do běžícího pohybu, nebo na návratu?"),
    Indicator("volatilita", "ATR vůči mediánu ATR za 90 dní (< 1 = stlačená)",
              "po klidu přichází prudký pohyb – spíš velikost než směr"),
    Indicator("funding_3d", "průměrný funding za 3 dny",
              "vysoký funding = přeplněné longy na páce, hrozí likvidační kaskáda (kontrariánský)"),
    Indicator("objem", "objem za 24 h vůči průměru 30 dní",
              "pohyb s vysokým objemem má za sebou skutečné peníze"),
    Indicator("sila_vs_btc", "výnos za 20 dní minus výnos BTC",
              "kapitál rotuje do coinů, které vedou", alts_only=True),
]


def compute_indicators(p: pd.DataFrame, funding: pd.Series | None = None,
                       btc_close: pd.Series | None = None) -> pd.DataFrame:
    """p = výstup `prepare_4h()`. Hodnota na svíčce i používá jen data známá při jejím uzavření."""
    d = BARS_PER_DAY
    c = p["close"]
    out = pd.DataFrame(index=p.index)
    out["momentum_20d"] = c / c.shift(20 * d) - 1
    out["momentum_60d"] = c / c.shift(60 * d) - 1
    hi, lo = p["high"].rolling(20 * d).max(), p["low"].rolling(20 * d).min()
    out["kanal_20d"] = (c - lo) / (hi - lo)
    out["od_ema20_atr"] = (c - p["ema_fast"]) / p["atr"]
    out["volatilita"] = p["atr"] / p["atr"].rolling(90 * d).median()
    out["objem"] = p["volume"].rolling(d).sum() / p["volume"].rolling(30 * d).sum() * 30

    out["funding_3d"] = np.nan
    if funding is not None and len(funding):
        f = funding.sort_index().rolling(9).mean()                 # 9 vyúčtování po 8 h = 3 dny
        closes = p.index + pd.Timedelta("4h")                      # funding známý při uzavření svíčky
        pos = f.index.searchsorted(closes, side="right") - 1
        vals = np.where(pos >= 0, f.to_numpy()[np.clip(pos, 0, None)], np.nan)
        stale = (closes - f.index[np.clip(pos, 0, None)]) > pd.Timedelta("1D")
        out["funding_3d"] = np.where(stale, np.nan, vals)

    out["sila_vs_btc"] = np.nan
    if btc_close is not None:
        b = btc_close.reindex(p.index)
        out["sila_vs_btc"] = out["momentum_20d"] - (b / b.shift(20 * d) - 1)
    return out


def outcomes(p: pd.DataFrame, k_atr: float, target_r: float, horizon: int) -> pd.DataFrame:
    """Výsledek longu ze svíčky i (vstup open i+1) v R bez nákladů + největší pohyb v %.
    Tady se do budoucnosti dívat SMÍ – je to výsledek, ne indikátor."""
    n = len(p)
    idx = np.flatnonzero(p["atr"].notna().to_numpy())
    idx = idx[idx < n - horizon]
    r, _, _ = simulate(p, idx, k_atr, target_r, horizon, +1)
    h, l, o = (p[k].to_numpy() for k in ("high", "low", "open"))
    entry = o[idx + 1]
    win = idx[:, None] + 1 + np.arange(horizon)
    move = np.maximum(h[win].max(axis=1) / entry - 1, 1 - l[win].min(axis=1) / entry) * 100
    out = pd.DataFrame(index=p.index, columns=["R", "move_pct"], dtype=float)
    out.iloc[idx, 0] = r
    out.iloc[idx, 1] = move
    return out


def quintiles(df: pd.DataFrame, col: str, split: pd.Timestamp) -> pd.Series:
    """Skupina 1–5 podle hranic spočítaných jen z dat před splitem."""
    train = df.loc[df.index < split, col].dropna()
    if len(train) < 200:
        return pd.Series(np.nan, index=df.index)
    edges = np.quantile(train, [0.2, 0.4, 0.6, 0.8])
    q = np.searchsorted(edges, df[col].to_numpy(), side="right") + 1.0
    q[df[col].isna().to_numpy()] = np.nan
    return pd.Series(q, index=df.index)


def table(rows: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """rows: sloupce sym, per, q, R, move_pct. Vrací průměr R po skupinách + Q5−Q1 a nejistotu."""
    def stats(g):
        r = g["R"]
        return pd.Series({"R": r.mean(), "n": len(r), "move": g["move_pct"].median()})
    t = rows.groupby(["sym", "per", "q"]).apply(stats)
    piv = t["R"].unstack("q")
    n = t["n"].unstack("q")
    sd = rows.groupby(["sym", "per", "q"])["R"].std().unstack("q")
    n_eff = (n / (horizon / 3)).clip(lower=1)                      # překrývající se svíčky
    piv["Q5−Q1"] = piv.get(5.0) - piv.get(1.0)
    piv["±2SE"] = 2 * np.sqrt(sd.get(5.0) ** 2 / n_eff.get(5.0) + sd.get(1.0) ** 2 / n_eff.get(1.0))
    piv["move_Q1"] = t["move"].unstack("q").get(1.0)
    piv["move_Q5"] = t["move"].unstack("q").get(5.0)
    return piv


def verdict(piv: pd.DataFrame) -> tuple[str, str]:
    s = piv["Q5−Q1"].dropna()
    if s.empty:
        return "–", "málo dat"
    pos, neg = (s > 0).sum(), (s < 0).sum()
    same = max(pos, neg)
    sign = "+" if pos >= neg else "−"
    med = s.median()                     # medián – jeden malý vzorek s extrémem nepřetáhne výsledek
    signif = (s.abs() > piv.loc[s.index, "±2SE"]).sum()
    txt = f"{sign} v {same}/{len(s)} případech, medián {med:+.2f}R, významných {signif}/{len(s)}"
    if same == len(s) and abs(med) >= 0.15:
        return "✅", txt
    if same >= len(s) - 1 and abs(med) >= 0.1:
        return "⚠️", txt
    return "❌", txt
