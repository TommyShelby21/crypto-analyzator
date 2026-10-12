"""Všechna nastavitelná pravidla na jednom místě. Měň tady, ne v kódu."""
from dataclasses import dataclass


@dataclass
class StrategyConfig:
    # --- trend (denní graf) ---
    trend_ema: int = 50              # cena musí být nad/pod touto EMA
    trend_slope_days: int = 5        # EMA musí růst/klesat za posledních N dní

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
class RiskConfig:
    risk_pct: float = 1.0            # % účtu riskované na obchod
    max_leverage: float = 3.0        # strop páky
    max_margin_pct: float = 50.0     # max % účtu vázané jako margin
    taker_fee: float = 0.00055       # Bybit taker 0,055 % (ověř si aktuální sazbu)
    slippage: float = 0.0005         # 0,05 % skluz na vstupu i výstupu
    maint_margin_rate: float = 0.005 # udržovací margin (BTC/ETH na Bybitu ~0,5 %)
    default_funding_rate: float = 0.0001  # 0,01 % / 8h, když nejsou historická data
    liq_buffer_r: float = 1.0        # likvidace musí být aspoň 1R za stopem

    # --- ochrany (inspirováno "protections" ve Freqtrade) ---
    cooldown_after_losses: int = 3   # po N ztrátách v řadě pauza...
    cooldown_bars: int = 12          # ...na N svíček (12 × 4h = 2 dny)
    max_drawdown_halt_pct: float = 25.0  # při propadu o X % od maxima přestat obchodovat
