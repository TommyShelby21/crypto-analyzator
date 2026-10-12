import numpy as np
import pandas as pd
import pytest

from analyzer.backtest import lookahead_check, run_backtest, summarize
from analyzer.config import RiskConfig, StrategyConfig
from analyzer.risk import plan_position
from analyzer.strategy import evaluate, prepare

PERP = dict(market="perp", margin_usdt=0)  # perp futures, velikost podle rizika


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
    rcfg = RiskConfig(**PERP, risk_pct=1.0, risk_usdt=0)
    plan = plan_position(1000, "long", 60000, 58000, 64000, rcfg)
    assert plan.ok
    assert plan.risk_usdt == pytest.approx(10.0, rel=1e-6)
    assert plan.liq_price < plan.stop
    assert plan.rr_net < 2.0  # poplatky ukrojí z ideálních 2R


def test_leverage_cap_reduces_size():
    rcfg = RiskConfig(**PERP, risk_pct=1.0, max_leverage=2, max_margin_pct=10)
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


# --- review (posouzení vlastního nápadu) ---

from analyzer.config import ReviewConfig
from analyzer.review import review, simulate, suggest_levels


def _bars(rows):
    """rows = [(open, high, low, close)], ATR natvrdo 10."""
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="4h", tz="UTC")
    p = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    p["atr"] = 10.0
    return p


def test_simulate_target_stop_and_tie():
    # signál na svíčce 0, vstup open 100 na svíčce 1, stop 1 ATR = 90, cíl 2R = 120
    p = _bars([(100, 100, 100, 100), (100, 105, 95, 104), (104, 121, 100, 118), (118, 119, 117, 118)])
    r, reason, held = simulate(p, np.array([0]), 1.0, 2.0, 3, 1)
    assert reason[0] == 1 and r[0] == 2.0 and held[0] == 2
    p = _bars([(100, 100, 100, 100), (100, 125, 85, 100), (100, 101, 99, 100), (100, 101, 99, 100)])
    r, reason, _ = simulate(p, np.array([0]), 1.0, 2.0, 3, 1)
    assert reason[0] == -1 and r[0] == -1.0          # stop i cíl v jedné svíčce = stop
    p = _bars([(100, 100, 100, 100), (100, 101, 99, 100), (80, 82, 78, 80), (80, 81, 79, 80)])
    r, reason, _ = simulate(p, np.array([0]), 1.0, 2.0, 3, 1)
    assert reason[0] == -1 and r[0] == pytest.approx(-2.0)  # gap přes stop = horší cena


def test_simulate_time_exit_short():
    p = _bars([(100, 100, 100, 100), (100, 101, 99, 100), (100, 101, 96, 97), (97, 98, 94, 95)])
    r, reason, _ = simulate(p, np.array([0]), 1.0, 2.0, 3, -1)
    assert reason[0] == 0 and r[0] == pytest.approx(0.5)


@pytest.mark.parametrize("direction", ["long", "short"])
def test_suggest_levels_order(direction):
    p = prepare(synthetic(1500), StrategyConfig())
    entry = float(p["close"].iloc[-1])
    lv = suggest_levels(p, direction, entry, ReviewConfig())
    if direction == "long":
        assert lv.stop < entry < lv.target
    else:
        assert lv.target < entry < lv.stop
    assert abs(entry - lv.stop) / p["atr"].iloc[-1] >= ReviewConfig().min_stop_atr - 1e-9


def test_review_uses_only_finished_history():
    df = synthetic(2500)
    p = prepare(df, StrategyConfig())
    rv = ReviewConfig(horizon_days=5)
    res = review(p, "long", 1000, StrategyConfig(), RiskConfig(), rv)
    assert res.analogs.rows and res.baseline.rows
    assert res.baseline.n <= len(p) - rv.horizon_days * 6
    assert any(row["r"] == res.chosen_r for row in res.analogs.rows)
    assert res.flags and res.plan is not None


def test_fixed_leverage_changes_margin_not_risk():
    p3 = plan_position(1000, "long", 60000, 58000, 64000, RiskConfig(**PERP, leverage=1))
    p5 = plan_position(1000, "long", 60000, 58000, 64000, RiskConfig(**PERP, leverage=5))
    assert p5.leverage == 5 and p5.ok
    assert p5.risk_usdt == pytest.approx(p3.risk_usdt)       # riziko určuje stop, ne páka
    assert p5.margin == pytest.approx(p3.margin / 5)
    assert 58000 - 2000 > p5.liq_price                        # likvidace aspoň 1R za stopem
    wide = plan_position(1000, "long", 60000, 52000, 76000, RiskConfig(**PERP, leverage=5))
    assert not wide.ok                                        # stop −13 % se s 5× nesnese


def test_fixed_risk_usdt_and_exchange_step():
    rcfg = RiskConfig(**PERP, equity=267, risk_usdt=5)
    plan = plan_position(267, "long", 80000, 78000, 84000, rcfg)
    assert plan.risk_usdt == pytest.approx(5.0)
    plan = plan_position(267, "long", 80000, 78000, 84000, rcfg, qty_step=0.001, min_qty=0.001)
    assert plan.qty == pytest.approx(0.002)                   # 0,0023 → zaokrouhleno dolů
    assert plan.risk_usdt <= 5.0 and plan.ok


def test_min_qty_too_risky_is_rejected():
    rcfg = RiskConfig(**PERP, equity=267, risk_usdt=5)
    # stop 10 000 daleko: 0,001 BTC = ztráta ~10 USDT, tedy 2× víc než 5 USDT
    plan = plan_position(267, "long", 80000, 70000, 100000, rcfg, qty_step=0.001, min_qty=0.001)
    assert plan.qty == pytest.approx(0.001) and not plan.ok


SPOT_BTC = dict(qty_step=1e-6, min_qty=1e-6, min_cost=5.0)


def test_fixed_margin_spot_long_uses_own_money():
    rcfg = RiskConfig(market="spot", margin_usdt=5, leverage=5)
    plan = plan_position(267, "long", 80000, 77600, 84800, rcfg, **SPOT_BTC)   # stop −3 %
    assert plan.notional == pytest.approx(25, abs=0.1)        # 5 USDT × 5 (zaokrouhleno na krok)
    assert plan.borrowed == 0 and plan.liq_price == 0          # z vlastních 267 USDT, nic se nepůjčuje
    assert plan.risk_usdt == pytest.approx(25 * 0.03, rel=0.1)  # ~0,75 USDT + poplatky
    assert plan.ok


def test_fixed_margin_spot_short_borrows():
    rcfg = RiskConfig(market="spot", margin_usdt=5, leverage=5)
    plan = plan_position(267, "short", 80000, 82400, 75200, rcfg, **SPOT_BTC)
    assert plan.borrowed == pytest.approx(plan.notional)
    assert plan.liq_price > plan.stop and plan.ok


def test_fixed_margin_perp_btc_uses_exchange_minimum():
    rcfg = RiskConfig(market="perp", margin_usdt=5, leverage=5)
    limits = dict(qty_step=0.001, min_qty=0.001, min_cost=5)
    plan = plan_position(267, "long", 80000, 77600, 84800, rcfg, **limits)   # stop −3 %
    assert plan.qty == pytest.approx(0.001) and plan.ok        # 25 USDT < minimum → 0,001 BTC
    assert plan.risk_usdt < 267 * rcfg.max_risk_pct / 100      # ~2,6 USDT ≈ 1 % účtu
    wide = plan_position(267, "long", 80000, 75200, 89600, rcfg, **limits)   # stop −6 %
    assert not wide.ok                                         # ~5 USDT > strop 1,5 % účtu


def test_zones_skip_broken_levels_and_merge_touches():
    from analyzer.review import _zones
    # dva vrcholy ~110 (zóna, 2 dotyky), vrchol 104, který cena později prorazila closem 105
    highs = [100, 101, 110, 101, 100, 101, 109.5, 101, 100, 101, 104, 101, 100, 105, 102, 101, 100, 100]
    rows = [(h - 1, h, h - 3, h - 1) for h in highs]
    rows[13] = (103, 106, 102, 105)          # close 105 > 104 → úroveň 104 je prolomená
    p = _bars(rows)
    z = _zones(p, 100, 1, high=True, entry=101, zone_pct=0.01)
    assert (109.5, 110.0, 2) in z                 # dva vrcholy blízko sebe = jedna zóna
    assert all(104 not in (zz[0], zz[1]) for zz in z)


def test_user_level_blocks_target():
    p = prepare(synthetic(1500), StrategyConfig())
    entry = float(p["close"].iloc[-1])
    rv = ReviewConfig()
    base = suggest_levels(p, "long", entry, rv)
    wall = entry + 0.3 * (entry - base.stop)               # tvoje rezistence 0,3R nad vstupem
    lv = suggest_levels(p, "long", entry, rv, [wall])
    assert any("tvoje úroveň" in f.text and f.level == "warn" for f in lv.flags)
