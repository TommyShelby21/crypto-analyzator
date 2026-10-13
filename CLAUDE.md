# Kontext projektu (pro Claude Code)

Komunikuj česky. Autor: Tomáš, fullstack vývojář, s tradingem začíná.

## Cíl
Pomocník pro rozhodování o vstupu do swing obchodů (držení několik dní) na
krypto futures – Bybit USDT perpetual. Nástroj NEOBCHODUJE sám a nepoužívá
API klíče s právem obchodovat. Postup: backtest → paper trading → malé reálné peníze.

## Rozhodnutí
- Trh: BTC, ETH, SOL, HYPE (HYPE má krátkou historii a zatím ~0R), Bybit USDT perpetual futures, isolated margin, páka 5×.
  (`--market spot` jako alternativa.)
- Účet 267 USDT. Velikost: pevný margin 5 USDT × 5 = pozice 25 USDT. U BTC je minimum burzy
  0,001 BTC (~83 USDT, margin ~17 USDT) – použije se, pokud ztráta na stopu nepřesáhne
  `max_risk_pct` = 1,5 % účtu, jinak se obchod zamítne. Likvidace aspoň 1R za stopem.
- Strategie (od 2026-10-13): trend following na DENNÍCH svíčkách. Vstup: close nad/pod 20denním
  max/min. Stop 2 ATR. Bez pevného cíle – výstup, když close pod EMA20 A EMA20 klesá za 5 dní
  (short zrcadlově). Režim EMA200: bull = jen longy, bear = jen shorty, přechod = oba směry.
- Pevné cíle NEPOUŽÍVAT – usekávají velké obchody, na kterých strategie stojí (testy níže).
- 4h pullback strategie smazána (žádná výhoda). 4h indikátory zůstaly jen jako kontext
  pro `review` a `edge` (`analyzer/market.py`).
- Shorty NEVYPÍNAT (`allow_shorts` zůstává True) – v bear marketu jsou potřeba; směr řeší režim trhu.
- Cílem je indikátor „je tohle dobrý vstup?“ na základě více indikátorů, ne slepý automat.
- Data vždy až do dneška; split pro kontrolu na posledních 12 měsících (`--split 2025-10-01`),
  aktuální trh má rozhodující slovo.
- Backtest je záměrně pesimistický: vstup na open dalšího dne, gap přes stop = open,
  poplatky + skluz + skutečný funding. Velikost podle rizika (% účtu), nejnižší potřebná páka.
- Inspirace z Freqtrade: lookahead analýza, protections (cooldown po ztrátách, drawdown halt),
  plus Monte Carlo a out-of-sample split.

## Struktura
- `analyzer/config.py` – všechny parametry
- `analyzer/data.py` – ccxt, cache do `data/`
- `analyzer/strategy.py` – denní trend following: `prepare()`, `entry_signal()`, `exit_signal()`
- `analyzer/market.py` – 4h kontext trhu (trend, režim, EMA20, RSI, ATR) pro `review` a `edge`
- `analyzer/risk.py` – velikost pozice, páka, likvidace
- `analyzer/backtest.py` – backtest, `summarize`, `monte_carlo`, `lookahead_check`
- `analyzer/review.py` – posouzení vlastního nápadu: návrh stopu/cíle, historické analogie
- `analyzer/edge.py` – test indikátorů proti náhodnému vstupu (kvintily, hranice jen z dat před splitem)
- `analyzer/cli.py` – příkazy `check`, `backtest`, `lookahead`, `review`, `edge`
- `tests/` – pytest na syntetických datech (offline)

## Pravidla pro úpravy
- Po každé změně strategie spustit `pytest -q` a `python -m analyzer lookahead BTC ETH SOL HYPE`.
- Hodnotit podle out-of-sample části (`--split`), ne podle celého období; hlídat přeučení.
- Nový indikátor nejdřív porovnat s náhodným vstupem (podmíněná analýza), až pak do strategie.
- Žádné indikátory s `shift(-N)` ani agregace přes celý dataframe.

## Prostředí
- Systémový Python 3.9 (macOS) → `.venv/`; kód musí běžet na 3.9 (`from __future__ import annotations`).
- Git: https://github.com/TommyShelby21/crypto-analyzator (větev `main`).

## Zjištění – stará 4h strategie (smazána 2026-10-13), stav 2026-10-12
- **Současná pravidla nemají měřitelnou výhodu.** Průměr R záporný na BTC i ETH (posl. 12 měs.
  cca −0,4 až −0,5R). Posledních 12 měsíců byl medvědí trh (BTC 114k → 58k → 83k).
- Shorty prodělávaly i v bear režimu (−0,5 až −0,7R) → problém není směr, ale vstup.
- Filtr režimu EMA200 sníží drawdown a pomůže v 2021–2025, ziskovou strategii z toho neudělá.
- MFE/MAE: stop NENÍ moc těsný (bez stopu jen 13–15 % stopnutých obchodů dojde na +2R).
  Vstup je špatně načasovaný: medián −0,35R proti nám v prvních 8 h (vstup na vrcholu odrazu).
  +1R dosáhne 57–65 % obchodů, +2R jen 31–37 %.
- Změny cíle (1–3R) ani širšího stopu nedávají konzistentně kladný výsledek → neladit, přeučení.
- Podmíněná analýza všech svíček v trendu (vs. náhodný vstup):
  - náhodný vstup ve směru trendu = −0,09R ≈ jen náklady (poplatky + skluz ~0,08–0,1R/obchod),
  - signál strategie (všech 6 podmínek) je statisticky k nerozeznání od náhody,
  - pullback k EMA20 **škodí** (bez pullbacku lépe, konzistentně) → data naznačují spíš momentum,
  - close nad/pod EMA20 slabě pomáhá; RSI, stop ATR, potvrzovací svíčka ~nic; „volná cesta“ spíš škodí,
  - skóre (počet splněných podmínek) s výsledkem neroste → skóre z těchto indikátorů nefunguje.

- `review` – „podobné situace v historii“ vychází vždy ~0R. Není to chyba (na umělých datech s trendem
  ukáže +0,5 až +1,2R), ale podobnost se hledá podle trendu/režimu/RSI, které nic nepředpovídají.

## Test indikátorů (`python -m analyzer edge BTC ETH SOL HYPE`, stav 2026-10-13)
Long z každé 4h svíčky, stop 2 ATR, cíl 2R, max 5 dní, bez nákladů. Kvintily z dat před 2025-10-01.
Indikátory vybrané podle logiky (proč by měly fungovat), ne podle dat:
- ❌ momentum 20d/60d, pozice v 20denním kanálu, vzdálenost od EMA20, stlačená volatilita, objem,
  síla vs. BTC – žádný nemá konzistentní směr napříč coiny a obdobími (šum).
- ⚠️ funding 3d – jediný s konzistentním směrem: vysoký funding = horší long (kontrariánský,
  odpovídá logice přeplněných longů), medián −0,14R, ale statisticky slabé (1/6 významných).
- Rozdělení podle režimu: momentum v bull trhu spíš +, v bear spíš − (koupě síly v bull, prodej
  odrazů v bear), ale rozdíly malé (medián ±0,1R) a vzorky malé. Funding − v bull i bear.
- Náhodný vstup bez nákladů ≈ 0R ve všech obdobích → BTC/ETH/SOL/HYPE na 4h/5 dní jednoduché
  indikátory z ceny a objemu nepředpovídají.
- Funding na Bybitu: SOL od 2021-06, HYPE od 2024-12; SOL má mezeru ~39 dní a místy 2h intervaly.

## Trend following (denní, stav 2026-10-13)
Průzkum (skripty mimo repo, pak `analyzer backtest`): vstup průraz 20denního max/min, stop 2 ATR.
- Výstupy se stejným vstupem (BTC+ETH+SOL+HYPE, průměr R po nákladech):
  pevný cíl 2R −0,06, 3R −0,03, 5R −0,03, ½ na 2R + trailing −0,09 → pevné cíle ztrátové;
  kanál 10 dní +0,20, kanál 20 dní +0,43 (táhne SOL), close pod EMA20 +0,19,
  **close pod EMA20 + EMA20 klesá 5 dní +0,32** (kladné v obou obdobích, BTC/ETH/SOL), trailing 2 ATR +0,11;
  pojistka „3 ATR od maxima“ vždy zhoršila; stop na vstup po +1R nic nezlepší, prodlouží série ztrát.
- Pozor: „close pod EMA20“ už znamená klesající EMA za 1 den (matematicky) – proto sklon za 5 dní.
- Náhodné vstupy se stejnými výstupy: Donchian lepší než 57–92 % náhodných ve všech 8 kombinacích.
- `analyzer backtest` (1 % riziko, cooldown, skutečný funding, bez režimu): BTC +0,21R (před 0,14 / posl. 0,52),
  ETH +0,17 (0,12 / 0,37), SOL +0,49 (0,55 / 0,25), HYPE −0,08 (23 obchodů).
- Režim EMA200: průměr R/obchod vyšší v 6/8 (posl. 12 m.: BTC 0,88, ETH 1,39, SOL 0,72), o ~30 % méně
  obchodů; součet R před 09/2025 nižší (28,5 vs 37,8), posl. 12 m. vyšší (16,4 vs 11,0) → zapnuto.
- Jen longy: skvělé v bull 2021–2025, v posl. 12 m. (bear) BTC −0,26, ETH −0,41 → shorty potřeba.
- Pevná páka 5× u širokých stopů (SOL/HYPE 10–15 %) dá likvidaci před stopem → obchod se zamítne;
  backtest proto počítá s nejnižší potřebnou pákou.

## Další krok
1. Paper trading: `check` jednou denně po 00:00 UTC, zapisovat do deníku, po 2–3 měsících porovnat s backtestem.
2. Funding jako varování v `check` (přeplněná strana) – jediný kandidát z `edge` s logikou i daty.
3. Portfolio: víc coinů najednou = víc obchodů a hladší výsledky; řešit celkové riziko.
4. Snížit náklady: vstup limitním příkazem (maker 0,02 %, bez skluzu).
Nápady na později: Telegram upozornění (výstupní signál!), vyhodnocení deníku (`journal.csv`).
