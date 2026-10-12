"""Velikost pozice, páka a likvidace – spot (s marginem) i USDT perpetual.

Dva způsoby, jak určit velikost:
- pevný margin (výchozí): pozice = margin × páka, riziko = kolik ztratíš na stopu
- pevné riziko: pozice = riziko / vzdálenost stopu, páka jen určuje vázaný margin

Spot long do velikosti účtu se platí vlastními penězi – nic se nepůjčuje,
takže nehrozí likvidace ani úrok. Spot short = půjčené BTC/ETH, platí se úrok.
"""
from dataclasses import dataclass, field
import math

from .config import RiskConfig


@dataclass
class PositionPlan:
    direction: str
    entry: float
    stop: float
    target: float
    qty: float
    notional: float
    leverage: float
    margin: float             # vlastní peníze vázané v pozici
    liq_price: float          # 0 = likvidace nehrozí
    risk_usdt: float          # ztráta při stopu včetně poplatků a skluzu
    reward_usdt: float        # zisk při cíli po poplatcích a skluzu
    market: str = "perp"
    borrowed: float = 0.0     # půjčená hodnota v USDT (spot margin)
    warnings: list = field(default_factory=list)
    ok: bool = True

    @property
    def rr_net(self) -> float:
        return self.reward_usdt / self.risk_usdt if self.risk_usdt else 0.0


def liquidation_price(entry: float, leverage: float, direction: str, mmr: float) -> float:
    """Zjednodušený odhad pro isolated margin (bez poplatků za uzavření)."""
    if direction == "long":
        return entry * (1 - 1 / leverage + mmr)
    return entry * (1 + 1 / leverage - mmr)


def fee_rate(cfg: RiskConfig) -> float:
    return cfg.spot_taker_fee if cfg.market == "spot" else cfg.taker_fee


def plan_position(equity: float, direction: str, entry: float, stop: float, target: float,
                  cfg: RiskConfig, risk_mult: float = 1.0, qty_step: float | None = None,
                  min_qty: float | None = None, min_cost: float | None = None) -> PositionPlan:
    if direction == "long" and not stop < entry < target:
        raise ValueError("Long: musí platit stop < vstup < cíl")
    if direction == "short" and not target < entry < stop:
        raise ValueError("Short: musí platit cíl < vstup < stop")

    spot = cfg.market == "spot"
    cost = fee_rate(cfg) + cfg.slippage          # na každou stranu obchodu
    loss_per_unit = abs(entry - stop) + cost * (entry + stop)
    gain_per_unit = abs(target - entry) - cost * (entry + target)
    warnings = []
    leverage = min(cfg.leverage or 1.0, cfg.max_leverage)

    if cfg.margin_usdt:
        qty = cfg.margin_usdt * risk_mult * leverage / entry
    else:
        risk_budget = (cfg.risk_usdt or equity * cfg.risk_pct / 100) * risk_mult
        qty = risk_budget / loss_per_unit
        if not cfg.leverage:  # nejnižší páka, se kterou se margin vejde
            margin_cap = equity * cfg.max_margin_pct / 100
            leverage = min(max(1.0, math.ceil(qty * entry / margin_cap * 10) / 10), cfg.max_leverage)
    intended = qty * loss_per_unit               # riziko, se kterým se počítalo
    budget_txt = f"{intended:.2f} USDT ({intended / equity * 100:.1f} % účtu)"
    if intended > equity * cfg.max_risk_pct / 100:
        warnings.append(f"Ztráta na stopu {budget_txt} je nad stropem {cfg.max_risk_pct:g} % účtu – zvaž menší pozici.")

    margin_cap = equity * cfg.max_margin_pct / 100
    if qty * entry > margin_cap * leverage:
        qty = margin_cap * leverage / entry
        warnings.append(f"Pozice zmenšena – při páce {leverage:g}× by margin přesáhl {cfg.max_margin_pct:g} % účtu.")

    ok = True
    if qty_step:  # burza bere jen násobky kroku a má minimum množství i hodnoty
        floor = max(min_qty or 0.0, qty_step, (min_cost or 0.0) / entry)
        floor = math.ceil(floor / qty_step - 1e-9) * qty_step
        rounded = math.floor(qty / qty_step + 1e-9) * qty_step
        if rounded < floor:
            rounded = floor
            real = rounded * loss_per_unit
            msg = (f"Minimum burzy je {floor:g} ({floor * entry:.0f} USDT) – pozice zvětšena, na stopu ztratíš "
                   f"{real:.2f} USDT ({real / equity * 100:.1f} % účtu) místo {budget_txt}")
            if real > equity * cfg.max_risk_pct / 100:
                ok = False
                msg += (f" – víc než strop {cfg.max_risk_pct:g} % účtu, stop je na minimum burzy moc daleko"
                        + ("" if spot else " (nebo zkus --market spot)"))
            if not ok or real > intended * 1.2:  # drobné zvětšení nehlásit
                warnings.append(msg + ".")
        elif rounded < qty * 0.8:
            up = rounded + qty_step
            warnings.append(f"Zaokrouhleno na krok burzy {qty_step:g}: riskuješ {rounded * loss_per_unit:.2f} USDT "
                            f"místo {intended:.2f} ({up:g} by bylo {up * loss_per_unit:.2f} USDT).")
        qty = rounded
    notional = qty * entry

    if spot:
        # long: kupuješ za vlastní USDT, půjčka jen nad zůstatek účtu; short: půjčuješ si celé BTC/ETH
        borrowed = max(0.0, notional - equity) if direction == "long" else notional
        margin = notional - borrowed if direction == "long" else notional / leverage
        if borrowed:  # cross margin: likvidace, až ztráta sežere celý účet
            room = (equity - cfg.maint_margin_rate * notional) / qty
            liq = max(0.0, entry - room) if direction == "long" else entry + room
        else:
            liq = 0.0
    else:
        borrowed = 0.0
        margin = notional / leverage
        liq = liquidation_price(entry, leverage, direction, cfg.maint_margin_rate)

    buffer = cfg.liq_buffer_r * abs(entry - stop)
    if liq and ((direction == "long" and liq > stop - buffer) or (direction == "short" and liq < stop + buffer)):
        ok = False
        warnings.append("Likvidační cena je příliš blízko stopu – sniž páku.")
    if gain_per_unit <= 0:
        ok = False
        warnings.append("Po poplatcích a skluzu obchod nedává zisk ani při dosažení cíle.")

    return PositionPlan(direction, entry, stop, target, qty, notional, leverage, margin, liq,
                        risk_usdt=qty * loss_per_unit, reward_usdt=qty * gain_per_unit,
                        market=cfg.market, borrowed=borrowed, warnings=warnings, ok=ok)
