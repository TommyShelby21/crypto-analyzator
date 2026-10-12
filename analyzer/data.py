"""Stahování svíček a funding rate z Bybitu (USDT perpetual futures) přes ccxt.

Používá jen veřejná data, žádný API klíč. Data se cachují do ./data/*.csv,
takže opakovaný backtest nestahuje všechno znovu.
"""
from pathlib import Path
import time

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TF_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def _exchange():
    import ccxt  # import až tady, aby šly testy spustit i bez ccxt
    ex = ccxt.bybit({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    ex.options["fetchMarkets"]["types"] = ["linear"]  # jen USDT perpetuals, rychlejší start
    return ex


def _cache_path(kind: str, symbol: str, timeframe: str = "") -> Path:
    safe = symbol.replace("/", "-").replace(":", "_")
    return DATA_DIR / f"{kind}_{safe}{'_' + timeframe if timeframe else ''}.csv"


def fetch_ohlcv(symbol: str = "BTC/USDT:USDT", timeframe: str = "4h",
                since: str = "2021-01-01", use_cache: bool = True) -> pd.DataFrame:
    """Vrátí DataFrame s indexem v UTC a sloupci open/high/low/close/volume.
    Poslední (ještě neuzavřená) svíčka je vždy vynechána."""
    DATA_DIR.mkdir(exist_ok=True)
    path = _cache_path("ohlcv", symbol, timeframe)
    since_ms = int(pd.Timestamp(since, tz="UTC").timestamp() * 1000)

    cached = None
    if use_cache and path.exists():
        cached = pd.read_csv(path, index_col=0, parse_dates=True)
        if cached.index.tz is None:
            cached.index = cached.index.tz_localize("UTC")
        if len(cached) and cached.index[0] <= pd.Timestamp(since, tz="UTC"):
            since_ms = int(cached.index[-1].timestamp() * 1000) + TF_MS[timeframe]
        else:
            cached = None

    ex = _exchange()
    rows, cursor = [], since_ms
    now_ms = ex.milliseconds()
    while cursor < now_ms:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=cursor, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        cursor = batch[-1][0] + TF_MS[timeframe]
        if len(batch) < 1000:
            break

    new = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    new.index = pd.to_datetime(new.pop("ts"), unit="ms", utc=True)
    df = pd.concat([cached, new]) if cached is not None else new
    df = df[~df.index.duplicated(keep="last")].sort_index()

    # vyhodit neuzavřenou svíčku
    last_close_ms = df.index[-1].timestamp() * 1000 + TF_MS[timeframe]
    if last_close_ms > time.time() * 1000:
        df = df.iloc[:-1]

    df.to_csv(path)
    return df[df.index >= pd.Timestamp(since, tz="UTC")]


def fetch_funding(symbol: str = "BTC/USDT:USDT", since: str = "2021-01-01",
                  use_cache: bool = True) -> pd.Series:
    """Historie funding rate (sazba za 8h interval), index = čas vyúčtování."""
    DATA_DIR.mkdir(exist_ok=True)
    path = _cache_path("funding", symbol)
    since_ms = int(pd.Timestamp(since, tz="UTC").timestamp() * 1000)

    cached = None
    if use_cache and path.exists():
        cached = pd.read_csv(path, index_col=0, parse_dates=True)["rate"]
        if cached.index.tz is None:
            cached.index = cached.index.tz_localize("UTC")
        if len(cached) and cached.index[0] <= pd.Timestamp(since, tz="UTC") + pd.Timedelta("1D"):
            since_ms = int(cached.index[-1].timestamp() * 1000) + 1
        else:
            cached = None

    ex = _exchange()
    rows, cursor = [], since_ms
    while True:
        batch = ex.fetch_funding_rate_history(symbol, since=cursor, limit=200)
        if not batch:
            break
        rows.extend((b["timestamp"], b["fundingRate"]) for b in batch)
        nxt = batch[-1]["timestamp"] + 1
        if nxt <= cursor or len(batch) < 200:
            break
        cursor = nxt

    new = pd.Series({pd.Timestamp(t, unit="ms", tz="UTC"): r for t, r in rows}, name="rate", dtype=float)
    s = pd.concat([cached, new]) if cached is not None else new
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s.rename("rate").to_frame().to_csv(path)
    return s[s.index >= pd.Timestamp(since, tz="UTC")]
