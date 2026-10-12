import numpy as np
import pandas as pd
import pytest

from analyzer.backtest import lookahead_check, run_backtest, summarize
from analyzer.config import RiskConfig, StrategyConfig
from analyzer.risk import plan_position
from analyzer.strategy import evaluate, prepare


def synthetic(n=3000, seed=1):
    """Náhodná procházka se střídajícími se trendy, 4h svíčky."""
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.choice([-0.002, 0.0, 0.002], size=n // 300 + 1), 300)[:n]
    ret = drift + rng.normal(0, 0.012, n)
    close = 30000 * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[close[0]], close[:-1]])
    spread = np.abs(rng.normal(0, 0.006, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    idx = pd.date_range("2021-01-01", periods=n, freq="4h", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1.0}, index=idx)


def test_position_risk_is_one_percent():
    rcfg = RiskConfig(risk_pct=1.0)
    plan = plan_position(1000, "long", 60000, 58000, 64000, rcfg)
    assert plan.ok
    assert plan.risk_usdt == pytest.approx(10.0, rel=1e-6)
    assert plan.liq_price < plan.stop
    assert plan.rr_net < 2.0  # poplatky ukrojí z ideálních 2R


def test_leverage_cap_reduces_size():
    rcfg = RiskConfig(risk_pct=1.0, max_leverage=2, max_margin_pct=10)
    plan = plan_position(1000, "long", 60000, 59900, 60200, rcfg)  # velmi těsný stop
    assert plan.leverage == 2
    assert plan.notional == pytest.approx(200)
    assert plan.risk_usdt < 10


def test_short_validation():
    with pytest.raises(ValueError):
        plan_position(1000, "short", 60000, 59000, 58000, RiskConfig())


def test_no_lookahead_bias():
    df = synthetic(1500)
    assert lookahead_check(df, StrategyConfig(), samples=60) == []


def test_daily_trend_uses_only_closed_days():
    df = synthetic(1500)
    p = prepare(df, StrategyConfig())
    daily_close = df["close"].resample("1D").last()
    ts = pd.Timestamp("2021-06-10 12:00", tz="UTC")
    assert p.loc[ts, "d_close"] == pytest.approx(daily_close.loc[pd.Timestamp("2021-06-09", tz="UTC")])


def test_backtest_runs_and_is_consistent():
    df = synthetic(3000)
    trades, curve = run_backtest(df, 1000.0)
    assert trades, "na trendujících datech by měly vzniknout obchody"
    s = summarize(trades, 1000.0)
    assert s["obchodů"] == len(trades)
    assert curve.iloc[-1] == pytest.approx(trades[-1].equity_after)
    for t in trades:
        assert t.entry_time < t.exit_time or t.exit_reason in ("stop", "cíl")
        if t.exit_reason == "stop":
            assert t.r < 0


def test_evaluate_reports_reasons():
    df = synthetic(1500)
    p = prepare(df, StrategyConfig())
    sig = evaluate(p, len(p) - 1, StrategyConfig())
    assert sig.checks and all(c.name for c in sig.checks)
