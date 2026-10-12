"""Všechna nastavitelná pravidla na jednom místě. Měň tady, ne v kódu."""
from dataclasses import dataclass


@dataclass
class StrategyConfig:
    # --- trend (denní graf) ---
    trend_ema: int = 50              # cena musí být nad/pod touto EMA
    trend_slope_days: int = 5        # EMA musí růst/klesat za posledních N dní

    # --- režim trhu (pomalejší filtr nad denním trendem) ---
    # bull = cena nad rostoucí EMA -> jen longy; bear = cena pod klesající EMA -> jen shorty;
    # přechod = cena už EMA prorazila, ale ta ještě neotočila -> oba směry se sníženým rizikem
    regime_ema: int = 200            # 0 = režim vypnutý
    regime_slope_days: int = 10      # sklon EMA za posledních N dní
    regime_transition_risk: float = 0.5  # násobek rizika v přechodu (0 = v přechodu neobchodovat)

    # --- vstup (4h graf) ---
    pullback_ema: int = 20           # pullback k této EMA
    pullback_tolerance: float = 0.005  # low/high se musí přiblížit k EMA na 0,5 %
    pullback_lookback: int = 3       # pullback v posledních N svíčkách
    rsi_period: int = 14
    rsi_long_range: tuple = (40, 70)   # long jen když RSI v tomto pásmu
    rsi_short_range: tuple = (30, 60)
    allow_shorts: bool = True

    # --- stop-loss a cíl ---
    atr_period: int = 14
    swing_lookback: int = 10         # swingové minimum/maximum z posledních N svíček
    stop_buffer_atr: float = 0.2     # stop kousek za swing (v ATR)
    min_stop_atr: float = 0.5        # příliš těsný stop = šum -> obchod zamítnout
    max_stop_atr: float = 3.0        # příliš široký stop -> obchod zamítnout
    target_r: float = 2.0            # cíl v násobcích rizika
    resistance_lookback: int = 50    # rezistence/support z posledních N svíček
    max_hold_bars: int = 30          # časový stop (30 × 4h = 5 dní)


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
    cooldown_bars: int = 12          # ...na N svíček (12 × 4h = 2 dny)
    max_drawdown_halt_pct: float = 25.0  # při propadu o X % od maxima přestat obchodovat
