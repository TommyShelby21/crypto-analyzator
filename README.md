# Trading analyzer – swing na krypto futures

Pomocník pro rozhodnutí o vstupu do obchodu na Bybit USDT perpetual futures.
**Neobchoduje sám a nepotřebuje API klíč.** Jen čte veřejná data, vyhodnotí
pravidla a spočítá velikost pozice.

## Instalace

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q              # testy běží offline
```

## Použití

```bash
# 1) Nejdřív ověř, že pravidla mají na historii kladné očekávání
python -m analyzer backtest BTC ETH SOL --equity 1000 --split 2025-10-01

# 2) Kontrola, že strategie nenahlíží do budoucnosti (po každé úpravě pravidel)
python -m analyzer lookahead BTC ETH

# 3) Jednou denně po 00:00 UTC: vstoupit? a mám otevřenou pozici držet, nebo zavřít?
python -m analyzer check BTC ETH SOL

# 4) Mám vlastní nápad („long BTC na týden“) – posuď ho
python -m analyzer review BTC long --days 7

# 5) Předpovídá indikátor něco? (vs. náhodný vstup, hranice skupin jen z dat před --split)
python -m analyzer edge BTC ETH SOL HYPE --split 2025-10-01
```

`review` sám navrhne stop (za nejbližší swing 1–4 ATR od ceny) a cíl (2R, nebo těsně
před rezistencí), spočítá pozici a ukáže, jak dopadly stejné obchody (stop a cíl v ATR)
v podobných historických situacích – po poplatcích, skluzu a fundingu. Na konci vypíše
body pro a proti. Parametry jsou v `ReviewConfig` v `analyzer/config.py`.

Užitečné přepínače: `--market perp|spot` (výchozí perp), `--margin-usdt 5` (vlastní peníze do pozice, pozice = margin × páka), `--leverage 5`, `--risk-usdt 5` nebo `--risk 1` (velikost podle ztráty na stopu místo marginu), `--equity` (výchozí 267), `--long-only`. U `review` navíc `--entry 82250` (limitní vstup) a `--levels 82000,87000` (tvoje úrovně z grafu – nástroj je použije pro stop i cíl).
Všechna pravidla a parametry jsou v `analyzer/config.py`.

## Strategie: trend following na denních svíčkách

1. **Vstup:** denní close nad 20denním maximem → long; pod 20denním minimem → short.
2. **Režim trhu:** bull (close nad rostoucí EMA200) = jen longy, bear = jen shorty, přechod = oba směry.
3. **Stop:** 2 ATR od vstupu – pojistka, když průraz hned selže. Zadej ho na burze se vstupem.
4. **Bez pevného cíle.** Zavírá se, až se otočí trend: **close pod EMA20 a zároveň EMA20 klesá
   za posledních 5 dní** (short obráceně). Samotný close pod EMA20 bývá jen pullback v trendu.

Proč takhle (backtest 2021–2026, BTC/ETH/SOL/HYPE): pevné cíle 2R/3R/5R vycházely ztrátově,
protože usekávají pár velkých obchodů (+10 až +46R), na kterých strategie stojí. Vyhrává jen
~35–40 % obchodů a od vrcholu se v průměru vrátí ~2R – to je cena za to, že vrchol nikdo nezná.
4h pullback strategie (EMA20, RSI, potvrzovací svíčka) neměla měřitelnou výhodu a byla smazána.

## Řízení rizika

- `check`/`review`: pevný margin 5 USDT × páka 5× (nastavení v `RiskConfig`), nebo `--risk 1` (% účtu).
- `backtest`: velikost podle rizika (`--risk`, výchozí 1 % účtu) a nejnižší potřebná páka.
- Likvidace musí být aspoň 1R za stopem – u širokých stopů (SOL, HYPE) sniž páku.
- Isolated margin.

## Co si vzal z open-source nástrojů

- **Freqtrade – lookahead analysis:** porovnání signálu na celých a useknutých datech.
- **Freqtrade – protections:** pauza po sérii ztrát a zastavení při velkém drawdownu.
- **Monte Carlo** zamíchání pořadí obchodů – realistický odhad drawdownu.
- **Out-of-sample** rozdělení (`--split`): pravidla laď jen na datech před datem,
  hodnoť podle období po něm.

## Jak číst výsledky backtestu

- Hlavní číslo je **průměr R** (očekávaná hodnota na obchod) v out-of-sample části. Pod ~0,15R
  to po realitě (horší plnění, chyby, emoce) nejspíš nebude vydělávat.
- Méně než ~50 obchodů = výsledek je skoro náhoda.
- Když výsledky výrazně zlepšíš laděním parametrů, ale out-of-sample se zhorší, je to přeučení.
- Backtest je záměrně pesimistický: vstup na open dalšího dne, gap přes stop = horší cena,
  poplatky + skluz + skutečný funding.
- Série 5–8 ztrát po sobě je u trend followingu normální.

## Deník

`check` zapisuje každé vyhodnocení do `journal.csv`. Po uzavření obchodu doplň
`taken`, `result_r` a `note`. Po pár měsících uvidíš, jestli se držíš systému
a jestli realita odpovídá backtestu.

> Nástroj nedává investiční doporučení. Obchodování futures s pákou může vést ke ztrátě celého vkladu.
