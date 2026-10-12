# Kontext projektu (pro Claude Code)

Komunikuj česky. Autor: Tomáš, fullstack vývojář, s tradingem začíná.

## Cíl
Pomocník pro rozhodování o vstupu do swing obchodů (držení několik dní) na
krypto futures – Bybit USDT perpetual. Nástroj NEOBCHODUJE sám a nepoužívá
API klíče s právem obchodovat. Postup: backtest → paper trading → malé reálné peníze.

## Rozhodnutí
- Trh: BTC a ETH (altcoiny zatím ne), futures, isolated margin.
- Riziko 1 % účtu na obchod, páka max 3×, likvidace aspoň 1R za stopem.
- Strategie: denní trend (EMA50 + sklon) → pullback k EMA20 na 4h → potvrzovací svíčka
  → RSI filtr → stop za swingem (0,5–3 ATR) → cíl 2R bez rezistence v cestě → časový stop 5 dní.
- Backtest je záměrně pesimistický: vstup na open další svíčky, stop má přednost před cílem,
  poplatky + skluz + funding.
- Inspirace z Freqtrade: lookahead analýza, protections (cooldown po ztrátách, drawdown halt),
  plus Monte Carlo a out-of-sample split.

## Struktura
- `analyzer/config.py` – všechny parametry
- `analyzer/data.py` – ccxt, cache do `data/`
- `analyzer/strategy.py` – `prepare()` indikátory, `evaluate()` checklist
- `analyzer/risk.py` – velikost pozice, páka, likvidace
- `analyzer/backtest.py` – backtest, `summarize`, `monte_carlo`, `lookahead_check`
- `analyzer/cli.py` – příkazy `check`, `backtest`, `lookahead`
- `tests/` – pytest na syntetických datech (offline)

## Pravidla pro úpravy
- Po každé změně strategie spustit `pytest -q` a `python -m analyzer lookahead BTC`.
- Hodnotit podle out-of-sample části (`--split`), ne podle celého období; hlídat přeučení.
- Žádné indikátory s `shift(-N)` ani agregace přes celý dataframe.

## Další krok
Spustit `python -m analyzer backtest BTC ETH --since 2021-01-01 --split 2024-06-01`
na reálných datech a podle průměru R v out-of-sample rozhodnout, jestli pravidla upravit.
Nápady na později: Telegram upozornění, vyhodnocení deníku (`journal.csv`), více timeframů.
