"""Trading cost model, shared by back-tests (Phase 0) and the simulation (Phase 1).

The parameters are inherited from China A-shares (after the 2023-08-28 stamp-duty cut) and are not yet calibrated to Indian STT/brokerage:
- stamp duty: **sell side only** 0.05%
- commission: both sides, default 0.025%, minimum 5 per trade
- transfer fee: both sides, 0.001% of turnover
- slippage: configurable basis points (default 5bps); buy price up / sell price down, modelling impact cost

Slippage shows up in the actual fill price (fill_price) and isn't counted again in explicit fees; explicit fees = commission + stamp duty + transfer fee.
Cash change (cash_delta) is negative for buys and positive for sells, after all costs and slippage; P&L is the sum of both legs' cash_delta.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostConfig:
    """Cost parameters (configurable; defaults inherited from A-share retail trading)."""

    commission_rate: float = 0.00025   # commission rate (both sides) 0.025%
    min_commission: float = 5.0        # minimum commission per trade
    stamp_duty_rate: float = 0.0005    # stamp duty (sell only) 0.05%
    transfer_fee_rate: float = 0.00001  # transfer fee (both sides) 0.001%
    slippage_bps: float = 5.0          # slippage (basis points, both sides; 5bps = 0.05%)


@dataclass(frozen=True)
class Fill:
    """Net result of one fill (with the cost breakdown, for display and audit)."""

    side: str            # "buy" | "sell"
    price: float         # nominal price (signal/quote price, before slippage)
    fill_price: float    # actual fill price (with slippage)
    quantity: int
    gross: float         # actual turnover = fill_price * quantity
    commission: float
    stamp_duty: float
    transfer_fee: float
    slippage_cost: float  # slippage loss = |fill_price - price| * quantity (display only)
    explicit_fees: float  # explicit fees = commission + stamp_duty + transfer_fee
    friction: float       # total friction = explicit_fees + slippage_cost (display only)
    cash_delta: float     # cash change: negative for buy, positive for sell (after explicit fees; slippage is in fill_price)


class CostModel:
    """Trading cost calculator. Thread-agnostic; safe to share globally."""

    def __init__(self, config: CostConfig | None = None) -> None:
        self.cfg = config or CostConfig()

    def _apply_slippage(self, price: float, side: str) -> float:
        adj = price * self.cfg.slippage_bps / 10000.0
        return price + adj if side == "buy" else max(0.0, price - adj)

    def fill(self, side: str, price: float, quantity: int) -> Fill:
        """Cost and cash change of one fill.

        Args:
            side: "buy" or "sell"
            price: nominal price (before slippage)
            quantity: number of shares (positive integer)
        """
        side = (side or "").strip().lower()
        if side not in ("buy", "sell"):
            raise ValueError(f"side must be buy/sell, got {side!r}")
        qty = int(quantity)
        if qty <= 0 or price <= 0:
            raise ValueError(f"price/quantity must be positive, got price={price} qty={quantity}")

        fill_price = self._apply_slippage(price, side)
        gross = fill_price * qty
        commission = max(gross * self.cfg.commission_rate, self.cfg.min_commission)
        stamp_duty = gross * self.cfg.stamp_duty_rate if side == "sell" else 0.0
        transfer_fee = gross * self.cfg.transfer_fee_rate
        slippage_cost = abs(fill_price - price) * qty
        explicit_fees = commission + stamp_duty + transfer_fee

        if side == "buy":
            cash_delta = -(gross + explicit_fees)
        else:
            cash_delta = gross - explicit_fees

        return Fill(
            side=side,
            price=float(price),
            fill_price=round(fill_price, 6),
            quantity=qty,
            gross=round(gross, 4),
            commission=round(commission, 4),
            stamp_duty=round(stamp_duty, 4),
            transfer_fee=round(transfer_fee, 4),
            slippage_cost=round(slippage_cost, 4),
            explicit_fees=round(explicit_fees, 4),
            friction=round(explicit_fees + slippage_cost, 4),
            cash_delta=round(cash_delta, 4),
        )

    def round_trip_pnl(
        self, entry_price: float, exit_price: float, quantity: int
    ) -> dict:
        """P&L of one buy and one sell (after all costs). Handy for single-trade back-tests and reconciliation."""
        buy = self.fill("buy", entry_price, quantity)
        sell = self.fill("sell", exit_price, quantity)
        # Cash basis: buy outflow is -cash_delta (positive), sell inflow is cash_delta
        invested = -buy.cash_delta
        proceeds = sell.cash_delta
        pnl = proceeds - invested
        pnl_pct = (pnl / invested * 100.0) if invested > 0 else 0.0
        total_cost = buy.friction + sell.friction
        return {
            "entry_price": float(entry_price),
            "exit_price": float(exit_price),
            "quantity": int(quantity),
            "invested": round(invested, 4),
            "proceeds": round(proceeds, 4),
            "pnl": round(pnl, 4),
            "pnl_pct": round(pnl_pct, 4),
            "total_cost": round(total_cost, 4),
            "buy": buy,
            "sell": sell,
        }


# Global default instance (config can be overridden)
DEFAULT_COST_MODEL = CostModel()
