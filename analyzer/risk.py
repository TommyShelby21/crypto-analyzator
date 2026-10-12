"""Velikost pozice, páka a likvidace pro USDT perpetual (lineární) kontrakty.

Klíčová myšlenka: velikost pozice určuje RIZIKO (vzdálenost ke stopu),
ne páka. Páka jen říká, kolik marginu na pozici vážeš.
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
    margin: float
    liq_price: float
    risk_usdt: float          # ztráta při stopu včetně poplatků a skluzu
    reward_usdt: float        # zisk při cíli po poplatcích a skluzu
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


def plan_position(equity: float, direction: str, entry: float, stop: float, target: float,
                  cfg: RiskConfig, risk_mult: float = 1.0) -> PositionPlan:
    if direction == "long" and not stop < entry < target:
        raise ValueError("Long: musí platit stop < vstup < cíl")
    if direction == "short" and not target < entry < stop:
        raise ValueError("Short: musí platit cíl < vstup < stop")

    cost = cfg.taker_fee + cfg.slippage          # na každou stranu obchodu
    loss_per_unit = abs(entry - stop) + cost * (entry + stop)
    gain_per_unit = abs(target - entry) - cost * (entry + target)

    risk_budget = equity * cfg.risk_pct / 100 * risk_mult
    qty = risk_budget / loss_per_unit
    notional = qty * entry
    warnings = []

    margin_cap = equity * cfg.max_margin_pct / 100
    leverage = max(1.0, math.ceil(notional / margin_cap * 10) / 10)
    if leverage > cfg.max_leverage:
        leverage = cfg.max_leverage
        notional = margin_cap * leverage
        qty = notional / entry
        warnings.append(f"Pozice zmenšena kvůli stropu páky {cfg.max_leverage:g}× – riskuješ méně než {cfg.risk_pct:g} %.")

    margin = notional / leverage
    liq = liquidation_price(entry, leverage, direction, cfg.maint_margin_rate)
    buffer = cfg.liq_buffer_r * abs(entry - stop)
    ok = True
    if direction == "long" and liq > stop - buffer:
        ok = False
        warnings.append("Likvidační cena je příliš blízko stopu – sniž páku.")
    if direction == "short" and liq < stop + buffer:
        ok = False
        warnings.append("Likvidační cena je příliš blízko stopu – sniž páku.")
    if gain_per_unit <= 0:
        ok = False
        warnings.append("Po poplatcích a skluzu obchod nedává zisk ani při dosažení cíle.")

    return PositionPlan(direction, entry, stop, target, qty, notional, leverage, margin, liq,
                        risk_usdt=qty * loss_per_unit, reward_usdt=qty * gain_per_unit,
                        warnings=warnings, ok=ok)
