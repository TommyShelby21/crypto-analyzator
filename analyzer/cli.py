"""Příkazová řádka.

  python -m analyzer check BTC ETH --equity 800
  python -m analyzer backtest BTC --since 2021-01-01 --split 2024-06-01
  python -m analyzer lookahead BTC
"""
import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .backtest import lookahead_check, monte_carlo, run_backtest, summarize, trades_to_frame
from .config import RiskConfig, StrategyConfig
from .data import fetch_funding, fetch_ohlcv
from .risk import plan_position
from .strategy import evaluate, prepare

ROOT = Path(__file__).resolve().parent.parent
JOURNAL = ROOT / "journal.csv"


def _symbol(s: str) -> str:
    s = s.upper()
    return s if "/" in s else f"{s}/USDT:USDT"


def _num(x: float, d: int = 2) -> str:
    return f"{x:,.{d}f}".replace(",", " ")


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
            plan = plan_position(args.equity, sig.direction, sig.entry, sig.stop, sig.target, rcfg)
            if sig.ok and not plan.ok:
                decision = "ČEKAT"
        print(f"  → {decision}" + (f" ({sig.direction.upper()})" if sig.direction else ""))

        if plan:
            label = "Plán" if sig.ok else "Pokud se setup dokončí"
            print(f"  {label}: vstup ~{_num(plan.entry)} | SL {_num(plan.stop)} | TP {_num(plan.target)}")
            print(f"  Pozice {plan.qty:.4f} ({_num(plan.notional)} USDT), páka {plan.leverage:g}×, "
                  f"margin {_num(plan.margin)} USDT, likvidace ~{_num(plan.liq_price)}")
            print(f"  Riziko {_num(plan.risk_usdt)} USDT, zisk při cíli {_num(plan.reward_usdt)} USDT "
                  f"(čisté R:R {plan.rr_net:.2f})")
            for w in plan.warnings:
                print(f"  ⚠️  {w}")

        new = not JOURNAL.exists()
        with JOURNAL.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["checked_at", "candle", "symbol", "decision", "direction", "entry", "stop", "target",
                            "failed_checks", "taken", "result_r", "note"])
            w.writerow([datetime.now(timezone.utc).isoformat(timespec="minutes"), sig.time, sym, decision,
                        sig.direction or "", plan and round(plan.entry, 2), plan and round(plan.stop, 2),
                        plan and round(plan.target, 2),
                        "; ".join(c.name for c in sig.checks if not c.passed), "", "", ""])
    print(f"\nZapsáno do {JOURNAL.name} – doplň sloupce taken / result_r / note, až obchod uzavřeš.")


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
    ap = argparse.ArgumentParser(prog="analyzer", description="Swing analyzátor pro krypto futures (Bybit USDT perpetual).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("symbols", nargs="+", help="např. BTC ETH nebo SOL/USDT:USDT")
        sp.add_argument("--equity", type=float, default=1000, help="velikost účtu v USDT")
        sp.add_argument("--risk", type=float, help="%% účtu na obchod (výchozí 1)")
        sp.add_argument("--max-leverage", type=float, help="strop páky (výchozí 3)")
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

    args = ap.parse_args(argv)
    scfg, rcfg = StrategyConfig(), RiskConfig()
    if args.risk is not None:
        rcfg.risk_pct = args.risk
    if args.max_leverage is not None:
        rcfg.max_leverage = args.max_leverage
    if args.long_only:
        scfg.allow_shorts = False
    try:
        args.func(args, scfg, rcfg)
    except Exception as e:
        if type(e).__module__.startswith("ccxt"):
            raise SystemExit(f"Chyba při komunikaci s Bybitem: {type(e).__name__}: {str(e)[:200]}")
        raise


if __name__ == "__main__":
    main()
