"""Záměrně pesimistický backtest trend followingu na denních svíčkách.

- Signál se vyhodnotí na uzavřeném dni, vstup na open DALŠÍHO dne.
- Stop během dne; gap přes stop = výstup na open (horší cena).
- Výstupní signál (otočení trendu) na close, výstup na open dalšího dne.
- Poplatky + skluz na obou stranách, funding každých 8 h.
- Vždy jen jedna otevřená pozice na coin.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .config import RiskConfig, StrategyConfig
from .risk import plan_position
from .strategy import entry_signal, exit_signal, prepare

DAY = pd.Timedelta("1D")


@dataclass
class Trade:
    direction: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry: float
    exit: float
    stop: float
    qty: float
    leverage: float
    exit_reason: str
    fees: float
    funding: float
    pnl: float
    r: float
    mfe_r: float            # nejlepší dosažený zisk v R (kolik se pak vrátilo)
    equity_after: float


def _funding_times(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    first = start.floor("8h") + pd.Timedelta("8h")
    return pd.date_range(first, end, freq="8h") if first <= end else pd.DatetimeIndex([])


def run_backtest(df: pd.DataFrame, equity: float = 1000.0,
                 scfg: StrategyConfig | None = None, rcfg: RiskConfig | None = None,
                 funding: pd.Series | None = None) -> tuple[list[Trade], pd.Series]:
    """df = 4h svíčky (složí se na denní). Velikost pozice podle rizika (% účtu), ne pevného marginu,
    aby výsledky v R a % byly srovnatelné."""
    scfg = scfg or StrategyConfig()
    rcfg = rcfg or RiskConfig(margin_usdt=0, risk_usdt=0)
    p = prepare(df, scfg)
    o, h, l, c = (p[k].to_numpy() for k in ("open", "high", "low", "close"))
    idx = p.index
    trades: list[Trade] = []
    curve = {idx[0]: equity}
    fee = rcfg.taker_fee

    i, n = 0, len(p)
    peak, losing_streak, halted_at = equity, 0, None
    while i < n - 1:
        sig = entry_signal(p, i, scfg, entry=o[i + 1])
        if not sig.ok:
            i += 1
            continue
        plan = plan_position(equity, sig.direction, sig.entry, sig.stop, None, rcfg, sig.risk_mult)
        if not plan.ok or plan.qty <= 0:
            i += 1
            continue

        long = plan.direction == "long"
        e = i + 1
        best = plan.entry
        exit_px, reason, j = None, None, e
        for j in range(e, n):
            if long and o[j] <= plan.stop and j > e:
                exit_px, reason = o[j], "stop (gap)"
            elif not long and o[j] >= plan.stop and j > e:
                exit_px, reason = o[j], "stop (gap)"
            elif (long and l[j] <= plan.stop) or (not long and h[j] >= plan.stop):
                exit_px, reason = plan.stop, "stop"
            if exit_px is not None:
                break
            best = max(best, h[j]) if long else min(best, l[j])
            done, _ = exit_signal(p, j, plan.direction, scfg)
            if done and j + 1 < n:
                j += 1
                exit_px, reason = o[j], "otočení trendu"
                break
        if exit_px is None:
            exit_px, reason = c[j], "otevřený"     # pozice drží do konce dat – ocení se na posledním close

        slip = rcfg.slippage
        entry_fill = plan.entry * (1 + slip if long else 1 - slip)
        exit_fill = exit_px * (1 - slip if long else 1 + slip)
        gross = (exit_fill - entry_fill) * plan.qty * (1 if long else -1)
        fees = fee * plan.qty * (entry_fill + exit_fill)

        # výstup na otočení trendu = open dne j; stop během dne j se počítá pesimisticky s fundingem celého dne
        ftimes = _funding_times(idx[e], idx[j] if reason == "otočení trendu" else idx[j] + DAY)
        if funding is not None and len(funding):
            rates = funding.reindex(ftimes, method="ffill").fillna(rcfg.default_funding_rate)
        else:
            rates = pd.Series(rcfg.default_funding_rate, index=ftimes)
        fund = float(rates.sum()) * plan.qty * plan.entry * (1 if long else -1)

        pnl = gross - fees - fund
        equity += pnl
        mfe = abs(best - plan.entry) * plan.qty / plan.risk_usdt
        trades.append(Trade(plan.direction, idx[e], idx[j], entry_fill, exit_fill, plan.stop, plan.qty,
                            plan.leverage, reason, fees, fund, pnl, pnl / plan.risk_usdt, mfe, equity))
        curve[idx[j]] = equity
        if equity <= 0 or reason == "otevřený":
            halted_at = idx[j] if equity <= 0 else None
            break
        i = j  # na dni výstupu se může hned vyhodnotit nový signál (vstup až další den)

        # ochrany
        peak = max(peak, equity)
        if (1 - equity / peak) * 100 >= rcfg.max_drawdown_halt_pct:
            halted_at = idx[j]
            break
        losing_streak = losing_streak + 1 if pnl <= 0 else 0
        if rcfg.cooldown_after_losses and losing_streak >= rcfg.cooldown_after_losses:
            i += rcfg.cooldown_days
            losing_streak = 0

    s = pd.Series(curve).sort_index()
    s.attrs["halted_at"] = halted_at
    return trades, s


def monte_carlo(trades: list[Trade], start_equity: float, risk_pct: float,
                runs: int = 5000, seed: int = 42) -> dict:
    """Zamíchá pořadí výsledků (v R) a ukáže, jak špatně to mohlo dopadnout
    jen kvůli smůle v pořadí obchodů."""
    if len(trades) < 10:
        return {}
    rng = np.random.default_rng(seed)
    r = np.array([t.r for t in trades])
    dds, finals = [], []
    for _ in range(runs):
        seq = rng.permutation(r)
        eq = start_equity * np.cumprod(1 + seq * risk_pct / 100)
        eq = np.concatenate([[start_equity], eq])
        peak = np.maximum.accumulate(eq)
        dds.append(((eq - peak) / peak).min() * 100)
        finals.append((eq[-1] / start_equity - 1) * 100)
    dds, finals = np.array(dds), np.array(finals)
    return {
        "medián max drawdown %": round(float(np.median(dds)), 1),
        "5% nejhorší max drawdown %": round(float(np.percentile(dds, 5)), 1),
        "pravděpodobnost ztráty %": round(float((finals < 0).mean() * 100), 1),
    }


def lookahead_check(df: pd.DataFrame, scfg: StrategyConfig | None = None,
                    samples: int = 200, seed: int = 0) -> list[pd.Timestamp]:
    """Inspirováno `freqtrade lookahead-analysis`: spočítá vstupní i výstupní signál
    na celých datech a znovu na datech useknutých na konci daného dne. Pokud se liší,
    strategie nahlíží do budoucnosti. Vrací seznam dní, kde se liší."""
    scfg = scfg or StrategyConfig()
    full = prepare(df, scfg)
    rng = np.random.default_rng(seed)
    start = max(scfg.entry_days, scfg.atr_days, scfg.exit_ema + scfg.exit_slope_days, scfg.regime_ema) + 2
    cand = np.arange(start, len(full) - 1)
    bad = []

    def state(p, i):
        sig = entry_signal(p, i, scfg)
        ex = [exit_signal(p, i, d, scfg)[0] for d in ("long", "short")]
        return sig.direction, sig.ok, [c.passed for c in sig.checks], ex

    for i in sorted(rng.choice(cand, size=min(samples, len(cand)), replace=False)):
        day = full.index[i]
        cut = prepare(df[df.index < day + DAY], scfg)
        if cut.index[-1] != day or state(full, i) != state(cut, len(cut) - 1):
            bad.append(day)
    return bad


def summarize(trades: list[Trade], start_equity: float, curve: pd.Series | None = None) -> dict:
    if not trades:
        return {"obchodů": 0}
    r = np.array([t.r for t in trades])
    pnl = np.array([t.pnl for t in trades])
    eq = np.concatenate([[start_equity], start_equity + np.cumsum(pnl)])
    peak = np.maximum.accumulate(eq)
    max_dd = ((eq - peak) / peak).min() * 100
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    losing_streak = streak = 0
    for x in r:
        streak = streak + 1 if x <= 0 else 0
        losing_streak = max(losing_streak, streak)
    return {
        "obchodů": len(trades),
        "long / short": f"{sum(t.direction == 'long' for t in trades)} / {sum(t.direction == 'short' for t in trades)}",
        "úspěšnost %": round(len(wins) / len(trades) * 100, 1),
        "průměr R": round(r.mean(), 2),
        "medián R": round(float(np.median(r)), 2),
        "profit factor": round(wins.sum() / -losses.sum(), 2) if losses.sum() < 0 else float("inf"),
        "nejdelší série ztrát": losing_streak,
        "max drawdown %": round(max_dd, 1),
        "výnos %": round((eq[-1] / start_equity - 1) * 100, 1),
        "poplatky": round(sum(t.fees for t in trades), 2),
        "funding": round(sum(t.funding for t in trades), 2),
        "konečný účet": round(eq[-1], 2),
    }


def trades_to_frame(trades: list[Trade]) -> pd.DataFrame:
    return pd.DataFrame([asdict(t) for t in trades])
