"""Research-only execution primitive. No exchange or live trading interfaces."""
from dataclasses import dataclass, field
from math import isfinite

DAY = 86400000

@dataclass
class Ledger:
    cash: float
    commission: float
    slippage: float
    positions: dict = field(default_factory=dict, init=False)
    trades: list = field(default_factory=list, init=False)
    costs: float = field(default=0.0, init=False)

    def __post_init__(self):
        if not isfinite(self.cash) or self.cash <= 0:
            raise ValueError("invalid capital")
        if not all(isfinite(x) and 0 <= x < 1 for x in (self.commission, self.slippage)):
            raise ValueError("invalid costs")

    def execute(self, symbol, signal_day, row, quantity):
        """Signed units; reject missing next open, overspending and shorting.

        Sizing and correlation checks belong to the causal portfolio controller.
        No implicit liquidation or partial fills.
        """
        if row is None or row[0] != signal_day + DAY:
            raise ValueError("missing next open")
        opening = row[1]
        if not isfinite(opening) or opening <= 0 or not isfinite(quantity) or quantity == 0:
            raise ValueError("invalid order")
        held, basis = self.positions.get(symbol, (0.0, 0.0))
        price = opening * (1 + self.slippage if quantity > 0 else 1 - self.slippage)
        fee = abs(quantity) * price * self.commission
        cash_after = self.cash - quantity * price - fee
        if cash_after < 0 or held + quantity < 0:
            raise ValueError("cash or inventory limit")
        pnl = None
        if quantity > 0:
            basis += quantity * price + fee
        else:
            released = basis * (-quantity / held)
            pnl = -quantity * price - fee - released
            basis -= released
        self.cash = cash_after
        self.costs += fee + abs(quantity) * abs(price - opening)
        if held + quantity == 0:
            self.positions.pop(symbol, None)
        else:
            self.positions[symbol] = (held + quantity, basis)
        self.trades.append(dict(symbol=symbol, signal_day=signal_day,
                                fill_day=row[0], quantity=quantity, price=price,
                                fee=fee, realized_pnl=pnl))
        return pnl

    def equity(self, day, candles):
        """Mark only with actual same-day closes; missing marks invalidate fold."""
        total = self.cash
        for symbol, (units, _) in self.positions.items():
            row = candles.get(symbol)
            if row is None or row[0] != day or not isfinite(row[4]) or row[4] <= 0:
                raise ValueError("missing mark")
            total += units * row[4]
        return total
