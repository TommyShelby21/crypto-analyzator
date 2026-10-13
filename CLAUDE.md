# Kontext projektu (pro Claude Code)

Komunikuj česky. Autor: Tomáš, fullstack vývojář, s tradingem začíná.

## Cíl
Pomocník pro rozhodování o vstupu do swing obchodů (držení několik dní) na
krypto futures – Bybit USDT perpetual. Nástroj NEOBCHODUJE sám a nepoužívá
API klíče s právem obchodovat. Postup: backtest → paper trading → malé reálné peníze.

## Rozhodnutí
- Trh: BTC a ETH (altcoiny zatím ne), Bybit USDT perpetual futures, isolated margin, páka 5×.
  (`--market spot` jako alternativa.)
- Účet 267 USDT. Velikost: pevný margin 5 USDT × 5 = pozice 25 USDT. U BTC je minimum burzy
  0,001 BTC (~83 USDT, margin ~17 USDT) – použije se, pokud ztráta na stopu nepřesáhne
  `max_risk_pct` = 1,5 % účtu, jinak se obchod zamítne. Likvidace aspoň 1R za stopem.
- Strategie: režim trhu (EMA200) → denní trend (EMA50 + sklon) → pullback k EMA20 na 4h
  → potvrzovací svíčka → RSI filtr → stop za swingem (0,5–3 ATR) → cíl 2R bez rezistence v cestě
  → časový stop 5 dní.
- Režim trhu: bull (cena nad rostoucí EMA200) = jen longy, bear = jen shorty, přechod (cena EMA200
  prorazila, ta ještě neotočila) = oba směry s rizikem ×0,5. Vypnutí: `regime_ema = 0`.
- Shorty NEVYPÍNAT (`allow_shorts` zůstává True) – v bear marketu jsou potřeba; směr řeší režim trhu.
- Cílem je indikátor „je tohle dobrý vstup?“ na základě více indikátorů, ne slepý automat.
- Data vždy až do dneška; split pro kontrolu na posledních 12 měsících (`--split 2025-10-01`),
  aktuální trh má rozhodující slovo.
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
- `analyzer/review.py` – posouzení vlastního nápadu: návrh stopu/cíle, historické analogie
- `analyzer/edge.py` – test indikátorů proti náhodnému vstupu (kvintily, hranice jen z dat před splitem)
- `analyzer/cli.py` – příkazy `check`, `backtest`, `lookahead`, `review`, `edge`
- `tests/` – pytest na syntetických datech (offline)

## Pravidla pro úpravy
- Po každé změně strategie spustit `pytest -q` a `python -m analyzer lookahead BTC`.
- Hodnotit podle out-of-sample části (`--split`), ne podle celého období; hlídat přeučení.
- Nový indikátor nejdřív porovnat s náhodným vstupem (podmíněná analýza), až pak do strategie.
- Žádné indikátory s `shift(-N)` ani agregace přes celý dataframe.

## Prostředí
- Systémový Python 3.9 (macOS) → `.venv/`; kód musí běžet na 3.9 (`from __future__ import annotations`).
- Git: https://github.com/TommyShelby21/crypto-analyzator (větev `main`).

## Zjištění (backtest 2021-01 → 2026-10, stav 2026-10-12)
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

## Další krok
1. Funding jako varování v `review`/`check` (přeplněná strana) – jediný kandidát s logikou i daty.
2. Další hypotézy s logikou: open interest (růst OI + pokles ceny), delší horizont držení
   (momentum v literatuře funguje spíš na týdny), likvidace. Vždy nejdřív `edge`, pak strategie.
3. Snížit náklady: vstup limitním příkazem (maker 0,02 %, bez skluzu).
4. Pokud se nic nenajde: používat nástroj hlavně na řízení rizika (stop, velikost, likvidace),
   ne na předpověď směru.
Nápady na později: Telegram upozornění, vyhodnocení deníku (`journal.csv`), více timeframů.
