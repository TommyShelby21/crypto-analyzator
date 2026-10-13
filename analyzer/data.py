"""Stahování svíček a funding rate z Bybitu (USDT perpetual futures) přes ccxt.

Používá jen veřejná data, žádný API klíč. Data se cachují do ./data/*.csv,
takže opakovaný backtest nestahuje všechno znovu.
"""
from pathlib import Path
import time

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TF_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def _exchange(types=("linear",)):
    import ccxt  # import až tady, aby šly testy spustit i bez ccxt
    ex = ccxt.bybit({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    ex.options["fetchMarkets"]["types"] = list(types)  # jen potřebné trhy, rychlejší start
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

    ex = _exchange()
    cached = None
    if use_cache and path.exists():
        cached = pd.read_csv(path, index_col=0, parse_dates=True)
        if cached.index.tz is None:
            cached.index = cached.index.tz_localize("UTC")
        first_ms = int(cached.index[0].timestamp() * 1000) if len(cached) else None
        # trh mohl začít až po `since` (ETH perp od 2021-03-15) – pak je cache platná,
        # když burza před první uloženou svíčkou nic nemá
        valid = first_ms is not None and (
            first_ms <= since_ms or
            (ex.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=1) or [[first_ms]])[0][0] >= first_ms)
        if valid:
            since_ms = int(cached.index[-1].timestamp() * 1000) + TF_MS[timeframe]
        else:
            cached = None

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


def _funding_batch(ex, symbol: str, cursor: int) -> list:
    """Bybit vrací prázdno, když `since` leží před vznikem trhu (SOL, HYPE) – posouvá se po oknech."""
    now_ms = ex.milliseconds()
    while cursor < now_ms:
        batch = ex.fetch_funding_rate_history(symbol, since=cursor, limit=200)
        if batch:
            return batch
        cursor += 200 * 8 * 3_600_000
    return []


def fetch_funding(symbol: str = "BTC/USDT:USDT", since: str = "2021-01-01",
                  use_cache: bool = True) -> pd.Series:
    """Historie funding rate (sazba za 8h interval), index = čas vyúčtování."""
    DATA_DIR.mkdir(exist_ok=True)
    path = _cache_path("funding", symbol)
    since_ms = int(pd.Timestamp(since, tz="UTC").timestamp() * 1000)

    ex = _exchange()
    cached = None
    if use_cache and path.exists():
        cached = pd.read_csv(path, index_col=0, parse_dates=True)["rate"]
        if cached.empty:
            cached.index = pd.DatetimeIndex([], tz="UTC")
        elif cached.index.tz is None:
            cached.index = cached.index.tz_localize("UTC")
        first_ms = int(cached.index[0].timestamp() * 1000) if len(cached) else None
        # trh mohl začít až po `since` – cache platí, když burza před první uloženou hodnotou nic nemá
        valid = first_ms is not None and (
            first_ms <= since_ms + 86_400_000 or
            (_funding_batch(ex, symbol, since_ms) or [{"timestamp": first_ms}])[0]["timestamp"] >= first_ms)
        if valid:
            since_ms = int(cached.index[-1].timestamp() * 1000) + 1
        else:
            cached = None

    rows, cursor = [], since_ms
    while True:
        batch = _funding_batch(ex, symbol, cursor)
        if not batch:
            break
        rows.extend((b["timestamp"], b["fundingRate"]) for b in batch)
        nxt = batch[-1]["timestamp"] + 1
        # kratší dávka neznamená konec – u nového trhu začíná data uprostřed okna
        if nxt <= cursor or nxt > ex.milliseconds() - 8 * 3_600_000:
            break
        cursor = nxt

    new = pd.Series({pd.Timestamp(t, unit="ms", tz="UTC"): r for t, r in rows}, name="rate", dtype=float)
    if new.empty:
        new.index = pd.DatetimeIndex([], tz="UTC")
    s = pd.concat([cached, new]) if cached is not None else new
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s.rename("rate").to_frame().to_csv(path)
    return s[s.index >= pd.Timestamp(since, tz="UTC")]


def fetch_market_limits(symbol: str = "BTC/USDT:USDT") -> dict:
    """Krok a minima objednávky. Perp BTC: krok 0,001 BTC; spot BTC/USDT: krok 0,000001, min. 5 USDT."""
    ex = _exchange(("linear",) if ":" in symbol else ("spot",))
    ex.load_markets()
    m = ex.market(symbol)
    return {"qty_step": m["precision"]["amount"], "min_qty": m["limits"]["amount"]["min"],
            "min_cost": m["limits"]["cost"]["min"]}
