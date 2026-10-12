"""Posouzení vlastního nápadu na obchod („long BTC na týden“).

Nástroj nic nepředpovídá. Navrhne logický stop a cíl, spočítá velikost pozice
a ukáže, jak dopadly podobné situace v minulosti – se stejnou vzdáleností
stopu a cíle v ATR a stejnou pesimistickou simulací jako backtest.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import ReviewConfig, RiskConfig, StrategyConfig
from .risk import PositionPlan, fee_rate, plan_position
from .strategy import Signal, evaluate

BARS_PER_DAY = 6  # 4h svíčky


@dataclass
class Flag:
    level: str      # "ok" / "warn" / "bad"
    text: str


@dataclass
class Levels:
    stop: float
    target: float
    stop_note: str
    target_note: str
    flags: list = field(default_factory=list)


@dataclass
class AnalogStats:
    label: str
    n: int                  # počet historických svíček
    n_eff: int              # nepřekrývajících se případů (sousední svíčky jsou skoro totéž)
    rows: list = field(default_factory=list)   # dict: r, target_pct, stop_pct, time_pct, avg_r


@dataclass
class Review:
    time: pd.Timestamp
    direction: str
    entry: float
    atr: float
    rsi: float
    ema_pull: float
    trend: str
    regime: str | None
    horizon_days: int
    levels: Levels
    analogs: AnalogStats
    baseline: AnalogStats
    chosen_r: float
    signal: Signal
    plan: PositionPlan | None
    funding_rate: float
    hold_r: float           # cena držení (funding / úrok z půjčky) za celý horizont v R
    flags: list = field(default_factory=list)


def _fmt(x: float) -> str:
    return f"{x:,.2f}".replace(",", " ")


def _zones(p: pd.DataFrame, lookback: int, width: int, high: bool, entry: float, zone_pct: float,
           extra: int | None = None) -> list[tuple[float, float, int]]:
    """Rezistence (high=True, nad cenou) nebo supporty (pod cenou) seskupené do zón.

    Swing = extrém v okně ±width svíček, za kterým už width svíček proběhlo.
    Swing, nad který (pod který) se cena od té doby uzavřela, je prolomený a nepočítá se.
    Swingy do zone_pct ceny od okraje zóny tvoří jednu zónu – víc dotyků = silnější úroveň.
    Vrací (bližší okraj, vzdálenější okraj, počet dotyků), od nejbližší zóny.
    """
    arr = p["high" if high else "low"].to_numpy()
    close = p["close"].to_numpy()
    last = len(arr) - 1
    pts = {k for k in range(max(width, last - lookback), last - width + 1)
           if arr[k] == (arr[k - width:k + width + 1].max() if high else arr[k - width:k + width + 1].min())}
    if extra is not None:
        pts.add(extra)
    levels = []
    for k in pts:
        lv = float(arr[k])
        after = close[k + 1:]
        if high and lv > entry and not (after > lv).any():
            levels.append(lv)
        elif not high and lv < entry and not (after < lv).any():
            levels.append(lv)
    levels.sort(key=lambda lv: abs(lv - entry))
    zones: list[list] = []
    for lv in levels:
        if zones and abs(lv - zones[-1][0]) <= zone_pct * entry:  # šířka zóny max zone_pct
            zones[-1][1] = lv
            zones[-1][2] += 1
        else:
            zones.append([lv, lv, 1])
    return [tuple(z) for z in zones]


def _zdesc(z: tuple) -> str:
    near, far, n = z
    lo, hi = sorted((near, far))
    if n == 0:
        return f"{_fmt(near)} (tvoje úroveň)"
    return f"{_fmt(lo)}–{_fmt(hi)} ({n}× dotyk)" if n > 1 else _fmt(near)


def suggest_levels(p: pd.DataFrame, direction: str, entry: float, rv: ReviewConfig,
                   user_levels: list[float] | None = None) -> Levels:
    """Stop za nejbližší neprolomenou swing zónou v rozumné vzdálenosti,
    cíl 2R nebo těsně před nejbližší rezistenční (supportní) zónou."""
    a = float(p["atr"].iloc[-1])
    long = direction == "long"
    sign = 1 if long else -1
    flags = []
    w, zp = rv.pivot_width, rv.zone_pct
    recent = len(p) - rv.swing_bars + int(np.argmin(p["low"].to_numpy()[-rv.swing_bars:]) if long
                                          else np.argmax(p["high"].to_numpy()[-rv.swing_bars:]))
    supports = _zones(p, rv.level_lookback, w, high=not long, entry=entry, zone_pct=zp, extra=recent)
    obstacles = _zones(p, rv.zone_lookback, w, high=long, entry=entry, zone_pct=zp)
    # tvoje úrovně z grafu (např. support po falešném průrazu, který pravidla berou jako prolomený)
    for lv in user_levels or ():
        (supports if (lv < entry) == long else obstacles).append((float(lv), float(lv), 0))
    supports.sort(key=lambda z: abs(z[0] - entry))
    obstacles.sort(key=lambda z: abs(z[0] - entry))

    word = "low" if long else "high"
    stop = None
    dist = lambda lv: abs(entry - (lv - sign * rv.stop_buffer_atr * a)) / a  # noqa: E731
    for i, z in enumerate(supports):  # od nejbližší zóny: celá zóna, jinak aspoň její bližší swing
        for lv, note in ((z[1], _zdesc(z)), (z[0], f"{_fmt(z[0])} (zóna sahá až k {_fmt(z[1])})")):
            if rv.min_stop_atr <= dist(lv) <= rv.max_stop_atr:
                stop, stop_note = lv - sign * rv.stop_buffer_atr * a, f"za swing {word} {note}"
                if i + 1 < len(supports):
                    nz = supports[i + 1]
                    stop_note += f"; další {_zdesc(nz)} ({dist(nz[1]):.1f} ATR)"
                break
        if stop is not None:
            break
    if stop is None:
        stop = entry - sign * rv.fallback_stop_atr * a
        far = [dist(z[0]) for z in supports if dist(z[0]) > rv.max_stop_atr]
        if far:
            stop_note = f"{rv.fallback_stop_atr:g} ATR (nejbližší swing je {min(far):.1f} ATR daleko)"
            flags.append(Flag("warn", f"Stop není za strukturou – nejbližší swing {word} je {min(far):.1f} ATR daleko, "
                                      "pohyb je natažený, lepší bývá počkat na pullback"))
        else:
            stop_note = f"{rv.fallback_stop_atr:g} ATR (žádný swing v rozumné vzdálenosti)"
            flags.append(Flag("warn", "Stop není za žádným swingem – jen podle volatility"))

    for z in supports:
        if z[2] == 0 and abs(entry - z[0]) <= a:
            where = "nad tvým supportem" if long else "pod tvou rezistencí"
            flags.append(Flag("ok", f"Vstup těsně {where} {_fmt(z[0])} – kupuješ u úrovně, ne uprostřed pohybu"
                              if long else f"Vstup těsně {where} {_fmt(z[0])} – prodáváš u úrovně"))

    risk = abs(entry - stop)
    base = entry + sign * rv.target_r * risk
    block = [z for z in obstacles if abs(z[0] - entry) < rv.target_r * risk]
    level_name = "rezistence" if long else "support"
    if not block:
        target = base
        nxt = f"nejbližší {level_name} {_zdesc(obstacles[0])}" if obstacles else f"žádná neprolomená {level_name}"
        target_note = f"{rv.target_r:g}R, volná cesta ({nxt})"
        flags.append(Flag("ok", f"Mezi vstupem a cílem {rv.target_r:g}R není {level_name}"))
    else:
        z = block[0]
        strong = z[2] != 1  # víc dotyků nebo tvoje úroveň
        strength = "silná " if strong else ""
        if long:
            before = "silnou rezistencí" if strong else "rezistencí"
        else:
            before = "silným supportem" if strong else "supportem"
        first_r = abs(z[0] - entry) / risk
        if first_r >= rv.min_target_r:
            target = z[0] - sign * rv.target_buffer_atr * a
            target_note = f"těsně před {before} {_zdesc(z)}, {first_r:.1f}R"
            flags.append(Flag("ok", f"Cíl je těsně před {before} {_zdesc(z)}, ne za ní"))
        else:
            target = base
            target_note = f"{rv.target_r:g}R – ale {strength}{level_name} {_zdesc(z)} leží už na {first_r:.1f}R"
            flags.append(Flag("warn", f"{(strength + level_name).capitalize()} {_zdesc(z)} je jen {first_r:.1f}R "
                                      "od vstupu – cena ji musí prorazit, než dojde na cíl"))
    return Levels(float(stop), float(target), stop_note, target_note, flags)


def simulate(p: pd.DataFrame, idx: np.ndarray, k_atr: float, mult: float, horizon: int, sign: int):
    """Pro každou svíčku v idx: vstup na open další svíčky, stop k_atr × ATR, cíl mult × riziko.
    Stejně pesimisticky jako backtest (stop má přednost, gap přes stop = open).
    Vrací (R hrubě, důvod: 1 cíl / -1 stop / 0 čas, počet držených svíček)."""
    o, h, l, c = (p[k].to_numpy() for k in ("open", "high", "low", "close"))
    a = p["atr"].to_numpy()
    e = idx + 1
    entry = o[e]
    risk = k_atr * a[idx]
    stop = entry - sign * risk
    target = entry + sign * mult * risk
    r = np.zeros(len(idx))
    reason = np.zeros(len(idx), dtype=int)
    held = np.full(len(idx), horizon)
    live = np.ones(len(idx), dtype=bool)
    for s in range(horizon):
        j = e + s
        if sign > 0:
            gap = (o[j] <= stop) if s > 0 else np.zeros(len(idx), bool)
            hit_stop, hit_tgt = l[j] <= stop, h[j] >= target
        else:
            gap = (o[j] >= stop) if s > 0 else np.zeros(len(idx), bool)
            hit_stop, hit_tgt = h[j] >= stop, l[j] <= target
        g = live & gap
        r[g] = sign * (o[j][g] - entry[g]) / risk[g]
        st = live & ~gap & hit_stop
        r[st] = -1.0
        tg = live & ~gap & ~hit_stop & hit_tgt
        r[tg] = mult
        reason[g | st] = -1
        reason[tg] = 1
        held[g | st | tg] = s + 1
        live &= ~(g | st | tg)
    last = c[e + horizon - 1]
    r[live] = sign * (last[live] - entry[live]) / risk[live]
    return r, reason, held


def _n_eff(idx: np.ndarray, horizon: int) -> int:
    n, nxt = 0, -1
    for i in idx:
        if i >= nxt:
            n += 1
            nxt = i + horizon
    return n


def analog_stats(p: pd.DataFrame, mask: np.ndarray, label: str, k_atr: float, mults: list, horizon: int,
                 sign: int, cost_frac: float, hold_8h: float) -> AnalogStats:
    """hold_8h = náklad držení za 8 h jako podíl pozice (funding pro daný směr / úrok)."""
    mask = mask.copy()
    mask[len(p) - horizon:] = False      # jen svíčky, u kterých je výsledek celý známý
    idx = np.flatnonzero(mask)
    st = AnalogStats(label, len(idx), _n_eff(idx, horizon))
    if not len(idx):
        return st
    stop_frac = k_atr * p["atr"].to_numpy()[idx] / p["open"].to_numpy()[idx + 1]
    for m in mults:
        r, reason, held = simulate(p, idx, k_atr, m, horizon, sign)
        net = r - cost_frac / stop_frac - hold_8h * held / 2 / stop_frac
        st.rows.append({"r": m, "target_pct": (reason == 1).mean() * 100, "stop_pct": (reason == -1).mean() * 100,
                        "time_pct": (reason == 0).mean() * 100, "avg_r": float(net.mean())})
    return st


def review(p: pd.DataFrame, direction: str, equity: float, scfg: StrategyConfig, rcfg: RiskConfig,
           rv: ReviewConfig, funding_rate: float | None = None, entry: float | None = None,
           limits: dict | None = None, user_levels: list[float] | None = None) -> Review:
    """limits = qty_step / min_qty / min_cost burzy (viz plan_position)."""
    """p = výstup `prepare()`; posuzuje se poslední uzavřená svíčka."""
    cur = p.iloc[-1]
    if pd.isna(cur["atr"]) or pd.isna(cur["d_ema"]):
        raise ValueError("Málo historie pro indikátory")
    long = direction == "long"
    sign = 1 if long else -1
    funding_rate = rcfg.default_funding_rate if funding_rate is None else funding_rate
    entry = float(cur["close"] if entry is None else entry)
    a = float(cur["atr"])
    horizon = rv.horizon_days * BARS_PER_DAY
    regime = cur["regime"] if isinstance(cur["regime"], str) else None
    trend = "nahoru" if cur["trend_up"] else "dolů" if cur["trend_down"] else "bez trendu"

    levels = suggest_levels(p, direction, entry, rv, user_levels)
    risk = abs(entry - levels.stop)
    k_atr = risk / a
    chosen_r = round(abs(levels.target - entry) / risk, 2)
    mults = sorted({1.0, 1.5, 2.0, 3.0, chosen_r})
    cost_frac = 2 * (fee_rate(rcfg) + rcfg.slippage)

    risk_mult = scfg.regime_transition_risk if regime == "přechod" else 1.0
    plan = plan_position(equity, direction, entry, levels.stop, levels.target, rcfg, risk_mult,
                         **(limits or {})) if risk_mult > 0 else None
    if rcfg.market == "spot":
        borrowed_frac = plan.borrowed / plan.notional if plan and plan.notional else 0.0
        hold_8h = rcfg.borrow_apr / 365 / 3 * borrowed_frac
    else:
        hold_8h = sign * funding_rate
    hold_r = hold_8h * 3 * rv.horizon_days * entry / risk

    # podobné situace: od nejpřísnějšího filtru, dokud je dost nezávislých případů
    valid = (p["atr"].notna() & p["d_ema"].notna() & p["d_slope"].notna()).to_numpy()
    same_trend = ((p["trend_up"] == cur["trend_up"]) & (p["trend_down"] == cur["trend_down"])).to_numpy()
    same_regime = (p["regime"] == regime).to_numpy() if regime else p["regime"].isna().to_numpy()
    rsi_near = ((p["rsi"] - cur["rsi"]).abs() <= rv.rsi_band).to_numpy()
    tdesc = f"trend {trend}"
    tiers = [
        (valid & same_trend & same_regime & rsi_near, f"{tdesc}, režim {regime or '–'}, RSI ±{rv.rsi_band:g}"),
        (valid & same_trend & same_regime, f"{tdesc}, režim {regime or '–'}"),
        (valid & same_trend, tdesc),
    ]
    args = (k_atr, mults, horizon, sign, cost_frac, hold_8h)
    baseline = analog_stats(p, valid, "všechny situace", *args)
    analogs = None
    for mask, label in tiers:
        st = analog_stats(p, mask, label, *args)
        if st.n_eff >= rv.min_samples:
            analogs = st
            break
    analogs = analogs or st

    sig = evaluate(p, len(p) - 1, scfg, entry=entry)

    # --- body pro a proti ---
    flags = []
    if (long and cur["trend_up"]) or (not long and cur["trend_down"]):
        flags.append(Flag("ok", f"Denní trend {trend} – jdeš s ním"))
    elif (long and cur["trend_down"]) or (not long and cur["trend_up"]):
        flags.append(Flag("bad", f"{direction.capitalize()} proti dennímu trendu ({trend})"))
    else:
        flags.append(Flag("warn", f"Denní trend není jasný (cena vs. EMA{scfg.trend_ema} a její sklon si odporují)"))

    if regime:
        if (long and regime == "bear") or (not long and regime == "bull"):
            flags.append(Flag("bad", f"Režim trhu {regime} (EMA{scfg.regime_ema}) – {direction} jde proti"))
        elif regime == "přechod":
            flags.append(Flag("warn", f"Režim trhu v přechodu – riziko sníženo na ×{risk_mult:g}"))
        else:
            flags.append(Flag("ok", f"Režim trhu {regime}"))

    if long and cur["rsi"] > 70:
        flags.append(Flag("warn", f"RSI {cur['rsi']:.0f} – překoupeno, kupuješ po silném růstu"))
    if not long and cur["rsi"] < 30:
        flags.append(Flag("warn", f"RSI {cur['rsi']:.0f} – přeprodáno, prodáváš po silném poklesu"))

    ext = sign * (entry - cur["ema_pull"]) / a
    if ext > rv.max_extension_atr:
        flags.append(Flag("warn", f"Cena je {ext:.1f} ATR od EMA{scfg.pullback_ema} (4h, {_fmt(cur['ema_pull'])}) – "
                                  "vstupuješ do nataženého pohybu, lepší vstup bývá u EMA"))
    elif ext < 0:
        flags.append(Flag("warn", f"Cena je {'pod' if long else 'nad'} EMA{scfg.pullback_ema} na 4h – krátkodobě jde proti tobě"))

    flags += levels.flags

    if rcfg.market == "perp" and sign * funding_rate >= rv.high_funding:
        flags.append(Flag("warn", f"Funding {funding_rate:.4%}/8h – {'longy' if long else 'shorty'} jsou přeplněné, "
                                  f"za {rv.horizon_days} dní tě to stojí ~{hold_r:.2f}R"))

    row = next(x for x in analogs.rows if x["r"] == chosen_r)
    hist = f"{row['avg_r']:+.2f}R průměrně, cíl dřív než stop v {row['target_pct']:.0f} % ({analogs.label})"
    if analogs.n_eff < rv.min_samples:
        flags.append(Flag("warn", f"Málo podobných situací v historii ({analogs.n_eff}) – statistika je slabá"))
    if row["avg_r"] < 0:
        flags.append(Flag("bad", f"Historicky podobné obchody prodělávaly: {hist}"))
    elif row["avg_r"] < 0.1:
        flags.append(Flag("warn", f"Historicky podobné obchody vycházely zhruba na nulu: {hist}"))
    else:
        flags.append(Flag("ok", f"Historicky podobné obchody vydělávaly: {hist}"))

    if sig.ok and sig.direction == direction:
        flags.append(Flag("ok", "Tvoje systematická pravidla dávají teď stejný signál"))
    else:
        failed = [c.name for c in sig.checks if not c.passed]
        why = ", ".join(failed) if sig.direction == direction else \
            (f"pravidla by šla {sig.direction}" if sig.direction else "pravidla teď nevidí směr")
        flags.append(Flag("warn", f"Systematická pravidla by teď nevstoupila ({why})"))

    if plan is None:
        flags.append(Flag("bad", "Podle nastavení se v tomhle režimu neobchoduje"))
    else:
        liq_pct = abs(plan.liq_price / entry - 1) * 100
        liq_r = abs(plan.liq_price - levels.stop) / risk
        if not plan.liq_price:
            flags.append(Flag("ok", "Spot za vlastní peníze – nic si nepůjčuješ, likvidace nehrozí"))
        elif plan.ok:
            flags.append(Flag("ok", f"Páka {plan.leverage:g}×: likvidace {liq_pct:.1f} % od vstupu, "
                                    f"{liq_r:.1f}R za stopem – stop přijde dřív"))
        for w in plan.warnings:
            flags.append(Flag("bad" if not plan.ok else "warn", w))

    return Review(p.index[-1], direction, entry, a, float(cur["rsi"]), float(cur["ema_pull"]), trend, regime,
                  rv.horizon_days, levels, analogs, baseline, chosen_r, sig, plan, funding_rate, hold_r, flags)
