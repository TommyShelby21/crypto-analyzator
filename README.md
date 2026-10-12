# Trading analyzer – swing na krypto futures

Pomocník pro rozhodnutí o vstupu do obchodu na Bybit USDT perpetual futures.
**Neobchoduje sám a nepotřebuje API klíč.** Jen čte veřejná data, vyhodnotí
pravidla a spočítá velikost pozice.

## Instalace

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest -q              # 7 testů, běží offline
```

## Použití

```bash
# 1) Nejdřív ověř, že pravidla mají na historii kladné očekávání
python -m analyzer backtest BTC ETH --equity 1000 --since 2021-01-01 --split 2024-06-01

# 2) Kontrola, že strategie nenahlíží do budoucnosti (po každé úpravě pravidel)
python -m analyzer lookahead BTC

# 3) Denní/4h kontrola: mám vstoupit?
python -m analyzer check BTC ETH

# 4) Mám vlastní nápad („long BTC na týden“) – posuď ho
python -m analyzer review BTC long --days 7
```

`review` sám navrhne stop (za nejbližší swing 1–4 ATR od ceny) a cíl (2R, nebo těsně
před rezistencí), spočítá pozici a ukáže, jak dopadly stejné obchody (stop a cíl v ATR)
v podobných historických situacích – po poplatcích, skluzu a fundingu. Na konci vypíše
body pro a proti. Parametry jsou v `ReviewConfig` v `analyzer/config.py`.

Užitečné přepínače: `--market perp|spot` (výchozí perp), `--margin-usdt 5` (vlastní peníze do pozice, pozice = margin × páka), `--leverage 5`, `--risk-usdt 5` nebo `--risk 1` (velikost podle ztráty na stopu místo marginu), `--equity` (výchozí 267), `--long-only`. U `review` navíc `--entry 82250` (limitní vstup) a `--levels 82000,87000` (tvoje úrovně z grafu – nástroj je použije pro stop i cíl).
Všechna pravidla a parametry jsou v `analyzer/config.py`.

## Strategie (výchozí)

1. **Denní trend:** cena nad EMA50 a EMA50 roste (short obráceně). Bere se jen uzavřený den.
2. **Pullback na 4h** k EMA20 během posledních 3 svíček a close zpět nad EMA20.
3. **Potvrzovací svíčka:** býčí a uzavře nad high předchozí svíčky.
4. **RSI** v pásmu 40–70 (short 30–60).
5. **Stop** pod swingovým minimem z 10 svíček; musí být 0,5–3 ATR daleko.
6. **Cíl 2R**, obchod se zamítne, když mezi vstupem a cílem leží rezistence.
7. **Časový stop** 5 dní.

## Řízení rizika

- Velikost pozice = 1 % účtu / vzdálenost ke stopu (včetně poplatků a skluzu).
- Páka se volí co nejnižší, strop 3×; likvidace musí být aspoň 1R za stopem.
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
- Backtest je záměrně pesimistický (stop má přednost před cílem ve stejné svíčce).

## Deník

`check` zapisuje každé vyhodnocení do `journal.csv`. Po uzavření obchodu doplň
`taken`, `result_r` a `note`. Po pár měsících uvidíš, jestli se držíš systému
a jestli realita odpovídá backtestu.

> Nástroj nedává investiční doporučení. Obchodování futures s pákou může vést ke ztrátě celého vkladu.
