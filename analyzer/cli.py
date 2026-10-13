"""Příkazová řádka.

  python -m analyzer check BTC ETH --equity 800
  python -m analyzer backtest BTC --since 2021-01-01 --split 2024-06-01
  python -m analyzer lookahead BTC
  python -m analyzer review BTC long --days 7 --equity 800
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .backtest import lookahead_check, monte_carlo, run_backtest, summarize, trades_to_frame
from .config import ReviewConfig, RiskConfig, StrategyConfig
from .data import fetch_funding, fetch_market_limits, fetch_ohlcv
from .review import review
from .risk import plan_position
from .strategy import evaluate, prepare

ROOT = Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "journal.csv"


def _symbol(s: str) -> str:
    s = s.upper()
    return s if "/" in s else f"{s}/USDT:USDT"


def _limits(sym: str, rcfg) -> dict:
    """Limity objednávky na trhu, kde se bude reálně obchodovat (spot BTC/USDT nebo perp)."""
    return fetch_market_limits(sym.split(":")[0] if rcfg.market == "spot" else sym)


def _num(x: float, d: int = 2) -> str:
    return f"{x:,.{d}f}".replace(",", " ")


def _journal(candle, sym, decision, direction, plan, failed):
    new = not JOURNAL.exists()
    with JOURNAL.open("a", newline="", encoding="utf-8-sig") as f:  # -sig: Excel pozná UTF-8
        w = csv.writer(f)
        if new:
            w.writerow(["checked_at", "candle", "symbol", "decision", "direction", "entry", "stop", "target",
                        "failed_checks", "taken", "result_r", "note"])
        w.writerow([datetime.now(timezone.utc).isoformat(timespec="minutes"), candle, sym, decision,
                    direction or "", plan and round(plan.entry, 2), plan and round(plan.stop, 2),
                    plan and round(plan.target, 2), failed, "", "", ""])


def _print_plan(plan, sym: str, equity: float, rcfg, days: int | None = None, hold: float = 0.0):
    """Velikost pozice a co přesně zadat na Bybitu. hold = funding/úrok za `days` dní v USDT."""
    coin = sym.split("/")[0]
    qty = f"{plan.qty:.6f}".rstrip("0").rstrip(".")
    if not plan.liq_price:
        liq = "bez likvidace"
    elif abs(plan.liq_price / plan.entry - 1) > 1:
        liq = "likvidace prakticky nehrozí (cross margin kryje celý účet)"
    else:
        liq = f"likvidace ~{_num(plan.liq_price)}"
    print(f"  Pozice {qty} {coin} ({_num(plan.notional)} USDT), vlastní peníze {_num(plan.margin)} USDT"
          + (f", půjčka {_num(plan.borrowed)} USDT" if plan.borrowed else "") + f", {liq}")
    print(f"  Ztráta na stopu {_num(plan.risk_usdt)} USDT ({plan.risk_usdt / equity * 100:.2f} % účtu), "
          f"zisk na cíli {_num(plan.reward_usdt)} USDT (čisté R:R {plan.rr_net:.2f})")
    if plan.market == "spot":
        if plan.direction == "long":
            how = f"Spot: kup {qty} {coin} za ~{_num(plan.notional)} USDT"
            how += f" (s půjčkou {_num(plan.borrowed)} USDT)." if plan.borrowed else " – vlastními penězi, páka netřeba."
        else:
            how = f"Spot margin (páka {plan.leverage:g}×): prodej {qty} {coin} na půjčku (Margin trading zapnutý)."
        print(f"  Na Bybitu: {how} Stop {_num(plan.stop)} nastav hned jako TP/SL objednávku.")
        if plan.borrowed and days:
            print(f"  Úrok z půjčky za {days} dní ~{_num(hold)} USDT "
                  f"(počítáno s {rcfg.borrow_apr:.0%} p.a. – ověř aktuální sazbu v aplikaci)")
    else:
        print(f"  Na Bybitu: perp, isolated, páka {plan.leverage:g}×, množství {qty} {coin} "
              f"(zablokuje {_num(plan.margin)} USDT marginu). Stop {_num(plan.stop)} zadej hned se vstupem.")
        if days:
            print(f"  Funding za {days} dní ~{_num(abs(hold))} USDT "
                  f"({'platíš' if hold >= 0 else 'dostaneš'}, při dnešní sazbě)")


def cmd_check(args, scfg, rcfg):
    since = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    for raw in args.symbols:
        sym = _symbol(raw)
        df = fetch_ohlcv(sym, "4h", since=since, use_cache=False)
        p = prepare(df, scfg)
        sig = evaluate(p, len(p) - 1, scfg)
        print(f"\n{sym} – 4h – svíčka uzavřená {sig.time + pd.Timedelta('4h'):%Y-%m-%d %H:%M} UTC")
        for c in sig.checks:
            print(f"  {'✅' if c.passed else '❌'} {c.name}: {c.detail}")

        decision = "VSTUP" if sig.ok else ("ČEKAT" if sig.direction else "NIC")
        plan = None
        if sig.direction:
            plan = plan_position(args.equity, sig.direction, sig.entry, sig.stop, sig.target, rcfg,
                                 sig.risk_mult or 1.0, **_limits(sym, rcfg))
            if sig.ok and not plan.ok:
                decision = "ČEKAT"
        print(f"  → {decision}" + (f" ({sig.direction.upper()})" if sig.direction else ""))

        if plan:
            label = "Plán" if sig.ok else "Pokud se setup dokončí"
            print(f"  {label}: vstup ~{_num(plan.entry)} | SL {_num(plan.stop)} | TP {_num(plan.target)}")
            _print_plan(plan, sym, args.equity, rcfg)
            for w in plan.warnings:
                print(f"  ⚠️  {w}")

        _journal(sig.time, sym, decision, sig.direction, plan,
                 "; ".join(c.name for c in sig.checks if not c.passed))
    print(f"\nZapsáno do {JOURNAL.name} – doplň sloupce taken / result_r / note, až obchod uzavřeš.")


ICON = {"ok": "✅", "warn": "⚠️ ", "bad": "❌"}


def cmd_review(args, scfg, rcfg):
    rv = ReviewConfig()
    if args.days:
        rv.horizon_days = args.days
    sym = _symbol(args.symbol)
    df = fetch_ohlcv(sym, "4h", since=args.since)
    try:
        fr = fetch_funding(sym, since=(pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)).strftime("%Y-%m-%d"))
        funding_rate = float(fr.iloc[-9:].mean())  # průměr za poslední 3 dny
    except Exception as e:
        print(f"(funding se nepodařilo stáhnout: {e}; použiju {rcfg.default_funding_rate:.4%}/8h)")
        funding_rate = None
    rv_ = review(prepare(df, scfg), args.direction, args.equity, scfg, rcfg, rv, funding_rate, args.entry,
                 _limits(sym, rcfg), args.levels)
    lv, a, e = rv_.levels, rv_.atr, rv_.entry

    print(f"\n{sym} – {args.direction.upper()} na {rv.horizon_days} dní – "
          f"svíčka uzavřená {rv_.time + pd.Timedelta('4h'):%Y-%m-%d %H:%M} UTC")
    print(f"  Trh: denní trend {rv_.trend}, režim {rv_.regime or '–'}, RSI {rv_.rsi:.0f}, "
          f"ATR(4h) {_num(a)} ({a / e * 100:.1f} %), EMA{scfg.pullback_ema} 4h {_num(rv_.ema_pull)}, "
          f"funding {rv_.funding_rate:.4%}/8h")

    print("\n  Návrh")
    print(f"    Vstup   ~{_num(e)}" + ("" if args.entry else " (poslední close)"))
    print(f"    Stop     {_num(lv.stop)} ({(lv.stop / e - 1) * 100:+.1f} %, {abs(e - lv.stop) / a:.1f} ATR) – {lv.stop_note}")
    print(f"    Cíl      {_num(lv.target)} ({(lv.target / e - 1) * 100:+.1f} %) – {lv.target_note}")
    print(f"    Časový stop: po {rv.horizon_days} dnech zavřít, ať je výsledek jakýkoli")

    an = rv_.analogs
    print(f"\n  Historie od {df.index[0]:%Y-%m-%d}: stejný stop a cíl v ATR, podobné situace ({an.label})")
    print(f"  {an.n} svíček, ~{an.n_eff} nezávislých případů (sousední svíčky se překrývají)")
    print(f"    {'cíl':>6}  {'cíl dřív':>9}  {'stop dřív':>9}  {'ani jedno':>9}  {'průměr R':>9}  {'všechny situace':>16}")
    for row, base in zip(an.rows, rv_.baseline.rows):
        mark = "→" if row["r"] == rv_.chosen_r else " "
        print(f"  {mark} {row['r']:>4.2f}R  {row['target_pct']:>8.0f}%  {row['stop_pct']:>8.0f}%  "
              f"{row['time_pct']:>8.0f}%  {row['avg_r']:>+9.2f}  {base['avg_r']:>+15.2f}R")
    print("  (průměr R je po poplatcích, skluzu a fundingu/úroku; „všechny situace“ = bez ohledu na trend, pro srovnání)")

    plan = rv_.plan
    if plan:
        print()
        _print_plan(plan, sym, args.equity, rcfg, rv.horizon_days, rv_.hold_r * plan.risk_usdt)

    print("\n  Pro a proti")
    for fl in rv_.flags:
        print(f"    {ICON[fl.level]} {fl.text}")
    bad = sum(f.level == "bad" for f in rv_.flags)
    warn = sum(f.level == "warn" for f in rv_.flags)
    verdict = ("NEBRAT – " if bad else "ZVÁŽIT – " if warn > 2 else "DRŽÍ POHROMADĚ – ") + f"{bad}× ❌, {warn}× ⚠️"
    print(f"\n  → {verdict}")
    print("  Tohle není předpověď. Je to kontrola, jestli nápad dává smysl a kolik riskuješ.")

    _journal(rv_.time, sym, "NÁPAD", args.direction, plan,
             "; ".join(f.text for f in rv_.flags if f.level == "bad"))
    print(f"\nZapsáno do {JOURNAL.name} jako NÁPAD – doplň taken / result_r / note.")


def _print_stats(title, stats):
    print(f"\n=== {title} ===")
    for k, v in stats.items():
        print(f"  {k:<24} {v}")


def cmd_backtest(args, scfg, rcfg):
    for raw in args.symbols:
        sym = _symbol(raw)
        print(f"\nStahuji {sym} od {args.since} …")
        df = fetch_ohlcv(sym, "4h", since=args.since)
        try:
            funding = fetch_funding(sym, since=args.since)
        except Exception as e:  # funding není kritický
            print(f"  (funding se nepodařilo stáhnout: {e}; použiju {rcfg.default_funding_rate:.4%}/8h)")
            funding = None

        trades, curve = run_backtest(df, args.equity, scfg, rcfg, funding)
        hold = (df["close"].iloc[-1] / df["close"].iloc[0] - 1) * 100
        print(f"  Data: {df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d}, {len(df)} svíček. Buy & hold: {hold:+.0f} %")
        if curve.attrs.get("halted_at") is not None:
            print(f"  ⛔ Obchodování zastaveno {curve.attrs['halted_at']:%Y-%m-%d} (drawdown limit).")

        _print_stats(f"{sym} – celé období", summarize(trades, args.equity))
        if args.split:
            cut = pd.Timestamp(args.split, tz="UTC")
            ins = [t for t in trades if t.entry_time < cut]
            oos = [t for t in trades if t.entry_time >= cut]
            eq_oos = ins[-1].equity_after if ins else args.equity
            _print_stats(f"před {args.split} (na tom ladíš)", summarize(ins, args.equity))
            _print_stats(f"od {args.split} (out-of-sample – tohle se počítá)", summarize(oos, eq_oos))

        mc = monte_carlo(trades, args.equity, rcfg.risk_pct)
        if mc:
            _print_stats("Monte Carlo (5 000× zamíchané pořadí obchodů)", mc)

        out = ROOT / f"trades_{sym.split('/')[0]}.csv"
        if trades:
            trades_to_frame(trades).to_csv(out, index=False)
            print(f"\n  Obchody uloženy do {out.name}")


def cmd_lookahead(args, scfg, rcfg):
    for raw in args.symbols:
        sym = _symbol(raw)
        df = fetch_ohlcv(sym, "4h", since=args.since)
        bad = lookahead_check(df, scfg, samples=args.samples)
        if bad:
            print(f"{sym}: ❌ signál se liší na {len(bad)} svíčkách – strategie nahlíží do budoucnosti! Např. {bad[:3]}")
        else:
            print(f"{sym}: ✅ bez lookahead biasu ({args.samples} náhodných svíček)")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):  # Windows konzole (cp1250) neumí emoji
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="analyzer", description="Swing analyzátor pro BTC/ETH na Bybitu (spot s marginem nebo USDT perpetual).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def risk_args(sp):
        sp.add_argument("--equity", type=float, help="velikost účtu v USDT (výchozí z config.py)")
        sp.add_argument("--market", choices=["spot", "perp"], help="spot (výchozí) nebo perp futures")
        sp.add_argument("--margin-usdt", type=float, help="vlastní peníze do pozice, pozice = margin × páka (výchozí 5)")
        sp.add_argument("--risk-usdt", type=float, help="místo marginu: pevná ztráta na stopu v USDT")
        sp.add_argument("--risk", type=float, help="místo marginu: %% účtu ztracené na stopu")
        sp.add_argument("--leverage", type=float, help="páka nastavená na burze (výchozí 5, 0 = nejnižší potřebná)")
        sp.add_argument("--max-leverage", type=float, help="strop páky (výchozí 5)")

    def common(sp):
        sp.add_argument("symbols", nargs="+", help="např. BTC ETH nebo SOL/USDT:USDT")
        risk_args(sp)
        sp.add_argument("--long-only", action="store_true", help="neobchodovat shorty")

    sp = sub.add_parser("check", help="vyhodnotí poslední uzavřenou 4h svíčku")
    common(sp)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("backtest", help="otestuje pravidla na historii")
    common(sp)
    sp.add_argument("--since", default="2021-01-01")
    sp.add_argument("--split", help="datum pro out-of-sample, např. 2024-06-01")
    sp.set_defaults(func=cmd_backtest)

    sp = sub.add_parser("lookahead", help="ověří, že strategie nepoužívá budoucí data")
    common(sp)
    sp.add_argument("--since", default="2023-01-01")
    sp.add_argument("--samples", type=int, default=200)
    sp.set_defaults(func=cmd_lookahead)

    sp = sub.add_parser("review", help="posoudí tvůj nápad: navrhne stop a cíl, porovná s historií")
    sp.add_argument("symbol", help="např. BTC")
    sp.add_argument("direction", choices=["long", "short"])
    sp.add_argument("--days", type=int, help="jak dlouho chceš držet (výchozí 7)")
    sp.add_argument("--entry", type=float, help="plánovaná vstupní cena (výchozí poslední close)")
    sp.add_argument("--levels", type=lambda s: [float(x) for x in s.replace(" ", "").split(",") if x],
                    help="tvoje supporty/rezistence z grafu, např. 82000,87000")
    sp.add_argument("--since", default="2021-01-01", help="od kdy brát historii pro srovnání")
    risk_args(sp)
    sp.set_defaults(func=cmd_review)

    args = ap.parse_args(argv)
    scfg, rcfg = StrategyConfig(), RiskConfig()
    if args.equity is None:
        args.equity = rcfg.equity
    if args.market:
        rcfg.market = args.market
    if args.margin_usdt is not None:
        rcfg.margin_usdt = args.margin_usdt
    if args.risk_usdt is not None:
        rcfg.margin_usdt, rcfg.risk_usdt = 0, args.risk_usdt
    if args.risk is not None:
        rcfg.margin_usdt, rcfg.risk_usdt, rcfg.risk_pct = 0, 0, args.risk
    if args.max_leverage is not None:
        rcfg.max_leverage = args.max_leverage
    if args.leverage is not None:
        rcfg.leverage = args.leverage
        rcfg.max_leverage = max(rcfg.max_leverage, args.leverage)
    if getattr(args, "long_only", False):
        scfg.allow_shorts = False
    try:
        args.func(args, scfg, rcfg)
    except Exception as e:
        if type(e).__module__.startswith("ccxt"):
            raise SystemExit(f"Chyba při komunikaci s Bybitem: {type(e).__name__}: {str(e)[:200]}")
        raise


if __name__ == "__main__":
    main()
