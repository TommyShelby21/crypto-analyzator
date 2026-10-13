"""Všechna nastavitelná pravidla na jednom místě. Měň tady, ne v kódu."""
from dataclasses import dataclass


@dataclass
class StrategyConfig:
    """Trend following na denních svíčkách (otestováno 2021–2026 na BTC, ETH, SOL, HYPE).

    Vstup: denní close prorazí N-denní maximum (long) / minimum (short).
    Stop: stop_atr × ATR od vstupu – pro případ, že průraz hned selže.
    Výstup: žádný pevný cíl (pevné cíle vycházely ztrátově – usekávají velké pohyby).
    Zavírá se, až se otočí trend: close pod EMA20 A ZÁROVEŇ EMA20 klesá za posledních N dní.
    """
    entry_days: int = 20             # průraz N-denního maxima/minima
    atr_days: int = 20
    stop_atr: float = 2.0            # počáteční stop
    exit_ema: int = 20               # výstup: close pod/nad touto EMA...
    exit_slope_days: int = 5         # ...a EMA klesá/roste za posledních N dní
    allow_shorts: bool = True
    # režim trhu: bull (close nad rostoucí EMA200) = jen longy, bear = jen shorty, přechod = oba směry.
    # Test 2021-08 → 2026-10: průměr R na obchod vyšší v 6/8 (coin × období), obchodů o ~30 % méně.
    regime_ema: int = 200            # 0 = vypnuto
    regime_slope_days: int = 10


@dataclass
class MarketConfig:
    """Kontext trhu na 4h svíčkách pro `review` a `edge` (trend, režim, RSI, ATR)."""
    trend_ema: int = 50              # denní trend: cena nad/pod touto EMA...
    trend_slope_days: int = 5        # ...a EMA roste/klesá za posledních N dní
    # bull = cena nad rostoucí EMA200, bear = pod klesající, přechod = cena už prorazila, EMA ještě neotočila
    regime_ema: int = 200            # 0 = režim vypnutý
    regime_slope_days: int = 10
    regime_transition_risk: float = 0.5  # násobek rizika v přechodu (0 = v přechodu neobchodovat)
    fast_ema: int = 20               # EMA na 4h
    rsi_period: int = 14
    atr_period: int = 14


@dataclass
class ReviewConfig:
    """Posouzení vlastního nápadu (příkaz `review`)."""
    horizon_days: int = 7            # jak dlouho chceš obchod držet (časový stop)
    level_lookback: int = 180        # swingy pro stop z posledních N svíček (180 × 4h = 30 dní)
    zone_lookback: int = 540         # rezistence/supporty pro cíl z posledních N svíček (90 dní)
    zone_pct: float = 0.01           # swingy v pásmu 1 % ceny = jedna zóna
    pivot_width: int = 3             # swing = nejvyšší/nejnižší bod v okně ±N svíček
    swing_bars: int = 10             # + nejnižší low / nejvyšší high posledních N svíček
    stop_buffer_atr: float = 0.2     # stop kousek za swing
    min_stop_atr: float = 1.0        # na několikadenní držení je stop pod 1 ATR v šumu
    max_stop_atr: float = 4.0        # dál = vstup je natažený
    fallback_stop_atr: float = 2.0   # když žádný swing nevyhovuje
    target_r: float = 2.0            # výchozí cíl
    min_target_r: float = 1.5        # cíl před rezistencí jen když dá aspoň tolik R
    target_buffer_atr: float = 0.1   # cíl kousek před rezistencí
    max_extension_atr: float = 2.0   # cena dál od EMA20 (4h) = natažený pohyb
    rsi_band: float = 10             # „podobná situace“ = RSI ± tolik
    min_samples: int = 20            # min. nezávislých historických případů
    high_funding: float = 0.0003     # 0,03 %/8h = přeplněná strana


@dataclass
class RiskConfig:
    equity: float = 267.0            # velikost účtu v USDT (výchozí pro příkazy)
    market: str = "perp"             # "perp" (USDT perpetual futures, isolated) nebo "spot" (margin)
    # velikost pozice: pevný margin má přednost; jinak pevné riziko v USDT; jinak % účtu
    margin_usdt: float = 5.0         # vlastní peníze do pozice; pozice = margin × páka
    risk_usdt: float = 0.0           # pevná ztráta na stopu v USDT
    risk_pct: float = 1.0            # % účtu riskované na obchod
    max_risk_pct: float = 1.5        # strop ztráty na stopu (% účtu); minimum burzy smí pozici
                                     # zvětšit jen do tohoto stropu (BTC perp min. 0,001 BTC)
    leverage: float = 5.0            # páka, kterou nastavuješ na burze (0 = nejnižší potřebná)
    max_leverage: float = 5.0        # strop páky
    max_margin_pct: float = 50.0     # max % účtu vázané jako margin
    spot_taker_fee: float = 0.001    # Bybit spot taker 0,1 %
    borrow_apr: float = 0.10         # úrok z půjčky na spot marginu ~10 % p.a. (orientačně, ověř v aplikaci)
    taker_fee: float = 0.00055       # Bybit perp taker 0,055 % (ověř si aktuální sazbu)
    slippage: float = 0.0005         # 0,05 % skluz na vstupu i výstupu
    maint_margin_rate: float = 0.005 # udržovací margin (BTC/ETH na Bybitu ~0,5 %)
    default_funding_rate: float = 0.0001  # 0,01 % / 8h, když nejsou historická data
    liq_buffer_r: float = 1.0        # likvidace musí být aspoň 1R za stopem

    # --- ochrany (inspirováno "protections" ve Freqtrade) ---
    cooldown_after_losses: int = 3   # po N ztrátách v řadě pauza...
    cooldown_days: int = 2           # ...na N dní
    max_drawdown_halt_pct: float = 25.0  # při propadu o X % od maxima přestat obchodovat
