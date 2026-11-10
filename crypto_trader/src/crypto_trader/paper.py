"""Persistent spot paper broker. This module never sends exchange orders."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ALLOWED_SYMBOLS = {"BTC/USDT", "ETH/USDT"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    initial_balance REAL NOT NULL,
    cash REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL UNIQUE,
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_fee REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    opened_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('LONG', 'CLOSE')),
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    fee REAL NOT NULL,
    pnl REAL,
    reason TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_equity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cash REAL NOT NULL,
    position_value REAL NOT NULL,
    equity REAL NOT NULL,
    occurred_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class PaperConfig:
    initial_balance: float = 100.0
    fee_rate: float = 0.001
    slippage_rate: float = 0.0005
    risk_per_trade: float = 0.005
    max_total_open_risk: float = 0.015
    max_open_positions: int = 2
    daily_loss_limit: float = 0.02


class PaperBroker:
    """Long-only, spot-style simulator with persistent account and trade journal."""

    def __init__(self, database_path: Path, config: PaperConfig | None = None):
        self.database_path = Path(database_path)
        self.config = config or PaperConfig()
        if self.config.initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        if not 0 <= self.config.fee_rate < 0.1:
            raise ValueError("fee_rate must be in [0, 0.1)")
        if not 0 <= self.config.slippage_rate < 0.1:
            raise ValueError("slippage_rate must be in [0, 0.1)")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)
            db.execute(
                """INSERT OR IGNORE INTO paper_account
                   (id, initial_balance, cash, created_at) VALUES (1, ?, ?, ?)""",
                (self.config.initial_balance, self.config.initial_balance, self._now()),
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _validate_price(price: float) -> None:
        if price <= 0:
            raise ValueError("price must be positive")

    def account(self) -> dict[str, float]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM paper_account WHERE id=1").fetchone()
            positions = db.execute("SELECT quantity, entry_price FROM paper_positions").fetchall()
        position_value = sum(float(p["quantity"]) * float(p["entry_price"]) for p in positions)
        return {
            "initial_balance": float(row["initial_balance"]),
            "cash": float(row["cash"]),
            "position_value_at_entry": position_value,
            "equity_at_entry_marks": float(row["cash"]) + position_value,
        }

    def open_position(
        self,
        symbol: str,
        market_price: float,
        stop_loss: float,
        take_profit: float,
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        if symbol not in ALLOWED_SYMBOLS:
            raise ValueError(f"unsupported symbol: {symbol}")
        self._validate_price(market_price)
        if not 0 < stop_loss < market_price < take_profit:
            raise ValueError("require 0 < stop_loss < market_price < take_profit")
        timestamp = occurred_at or self._now()
        fill = market_price * (1 + self.config.slippage_rate)
        with self._connect() as db:
            account = db.execute("SELECT * FROM paper_account WHERE id=1").fetchone()
            positions = db.execute("SELECT * FROM paper_positions").fetchall()
            if any(p["symbol"] == symbol for p in positions):
                raise ValueError(f"position already open for {symbol}")
            if len(positions) >= self.config.max_open_positions:
                raise ValueError("maximum open positions reached")
            cash = float(account["cash"])
            equity = cash + sum(float(p["quantity"]) * float(p["entry_price"]) for p in positions)
            if equity <= 0:
                raise ValueError("account equity is not positive")
            today = timestamp[:10]
            daily = db.execute(
                "SELECT COALESCE(SUM(pnl), 0) AS pnl FROM paper_trades "
                "WHERE side='CLOSE' AND substr(occurred_at,1,10)=?",
                (today,),
            ).fetchone()
            if float(daily["pnl"]) <= -self.config.initial_balance * self.config.daily_loss_limit:
                raise ValueError("daily loss limit reached")
            risk_budget = equity * self.config.risk_per_trade
            quantity = risk_budget / (fill - stop_loss)
            cost = quantity * fill
            entry_fee = cost * self.config.fee_rate
            if cost + entry_fee > cash:
                quantity = cash / (fill * (1 + self.config.fee_rate))
                cost = quantity * fill
                entry_fee = cost * self.config.fee_rate
            if quantity <= 0:
                raise ValueError("insufficient virtual cash")
            existing_risk = sum(
                float(p["quantity"]) * max(0.0, float(p["entry_price"]) - float(p["stop_loss"]))
                for p in positions
            )
            new_risk = quantity * max(0.0, fill - stop_loss)
            if existing_risk + new_risk > equity * self.config.max_total_open_risk:
                raise ValueError("maximum combined open risk would be exceeded")
            db.execute(
                "UPDATE paper_account SET cash=cash-? WHERE id=1", (cost + entry_fee,)
            )
            db.execute(
                """INSERT INTO paper_positions
                   (symbol,quantity,entry_price,entry_fee,stop_loss,take_profit,opened_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (symbol, quantity, fill, entry_fee, stop_loss, take_profit, timestamp),
            )
            db.execute(
                """INSERT INTO paper_trades
                   (symbol,side,quantity,price,fee,pnl,reason,occurred_at)
                   VALUES (?, 'LONG', ?, ?, ?, NULL, 'signal', ?)""",
                (symbol, quantity, fill, entry_fee, timestamp),
            )
        return {"symbol": symbol, "quantity": quantity, "entry_price": fill,
                "entry_fee": entry_fee, "stop_loss": stop_loss, "take_profit": take_profit}

    def close_position(
        self, symbol: str, market_price: float, reason: str = "signal",
        occurred_at: str | None = None,
    ) -> dict[str, Any]:
        self._validate_price(market_price)
        timestamp = occurred_at or self._now()
        fill = market_price * (1 - self.config.slippage_rate)
        with self._connect() as db:
            position = db.execute(
                "SELECT * FROM paper_positions WHERE symbol=?", (symbol,)
            ).fetchone()
            if position is None:
                raise ValueError(f"no open position for {symbol}")
            quantity = float(position["quantity"])
            proceeds = quantity * fill
            exit_fee = proceeds * self.config.fee_rate
            entry_cost = quantity * float(position["entry_price"])
            pnl = proceeds - exit_fee - entry_cost - float(position["entry_fee"])
            db.execute("UPDATE paper_account SET cash=cash+? WHERE id=1",
                       (proceeds - exit_fee,))
            db.execute(
                """INSERT INTO paper_trades
                   (symbol,side,quantity,price,fee,pnl,reason,occurred_at)
                   VALUES (?, 'CLOSE', ?, ?, ?, ?, ?, ?)""",
                (symbol, quantity, fill, exit_fee, pnl, reason, timestamp),
            )
            db.execute("DELETE FROM paper_positions WHERE symbol=?", (symbol,))
        return {"symbol": symbol, "quantity": quantity, "exit_price": fill,
                "exit_fee": exit_fee, "net_pnl": pnl, "reason": reason}

    def process_candle(
        self, symbol: str, timestamp: str, high: float, low: float
    ) -> dict[str, Any] | None:
        """Apply stop/target using OHLC extremes; if both hit, assume stop first."""
        with self._connect() as db:
            position = db.execute(
                "SELECT * FROM paper_positions WHERE symbol=?", (symbol,)
            ).fetchone()
        if position is None:
            return None
        stop, target = float(position["stop_loss"]), float(position["take_profit"])
        if low <= stop:
            return self.close_position(symbol, stop, "stop_loss", timestamp)
        if high >= target:
            return self.close_position(symbol, target, "take_profit", timestamp)
        return None

    def record_equity(self, prices: dict[str, float], occurred_at: str | None = None) -> dict[str, float]:
        with self._connect() as db:
            account = db.execute("SELECT cash FROM paper_account WHERE id=1").fetchone()
            positions = db.execute("SELECT symbol,quantity FROM paper_positions").fetchall()
            value = 0.0
            for position in positions:
                symbol = position["symbol"]
                if symbol not in prices:
                    raise ValueError(f"missing mark price for {symbol}")
                self._validate_price(prices[symbol])
                value += float(position["quantity"]) * prices[symbol]
            cash = float(account["cash"])
            equity = cash + value
            db.execute(
                "INSERT INTO paper_equity (cash,position_value,equity,occurred_at) VALUES (?,?,?,?)",
                (cash, value, equity, occurred_at or self._now()),
            )
        return {"cash": cash, "position_value": value, "equity": equity}

    def open_positions(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM paper_positions ORDER BY opened_at").fetchall()
        return [dict(row) for row in rows]

    def trade_history(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM paper_trades ORDER BY id").fetchall()
        return [dict(row) for row in rows]
