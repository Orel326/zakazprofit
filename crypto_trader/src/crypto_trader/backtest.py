"""Chronological historical simulation isolated from the live paper account."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .paper import PaperBroker, PaperConfig
from .quality import validate_ohlcv
from .team import analyze_symbol


def _metrics(curve: list[float], trades: list[dict], initial: float) -> dict:
    ending = curve[-1] if curve else initial
    peak, drawdown = initial, 0.0
    for equity in curve:
        peak = max(peak, equity)
        if peak > 0:
            drawdown = max(drawdown, (peak - equity) / peak)
    pnls = [float(t["pnl"]) for t in trades]
    wins = [p for p in pnls if p > 0]
    gross_loss = abs(sum(p for p in pnls if p < 0))
    return {
        "initial_equity": initial, "ending_equity": ending,
        "net_pnl": ending - initial, "return_pct": (ending / initial - 1) * 100,
        "closed_trades": len(pnls),
        "win_rate_pct": len(wins) / len(pnls) * 100 if pnls else 0.0,
        "profit_factor": sum(wins) / gross_loss if gross_loss else None,
        "expectancy_per_trade": sum(pnls) / len(pnls) if pnls else 0.0,
        "max_drawdown_pct": drawdown * 100,
    }


def run_backtest(symbol: str, rows: Sequence[Sequence[float]], timeframe_ms: int,
                 config: PaperConfig | None = None, start_index: int = 59) -> dict:
    """Run chronologically; trading starts at start_index, decisions use closed prefixes.

    Any open position is liquidated at the final candle close with normal costs.
    Candles before start_index are indicator warm-up only and cannot generate trades.
    """
    if len(rows) < 60:
        raise ValueError("at least 60 candles are required for backtesting")
    if not isinstance(start_index, int) or start_index < 59 or start_index >= len(rows):
        raise ValueError("start_index must be an integer between 59 and the last candle index")
    quality = validate_ohlcv(rows, timeframe_ms)
    if not quality.is_valid:
        raise ValueError("invalid OHLCV data: " + "; ".join(quality.errors))
    if quality.warnings:
        raise ValueError("OHLCV data contains gaps: " + "; ".join(quality.warnings))
    cfg = config or PaperConfig()
    with tempfile.TemporaryDirectory(prefix="crypto-backtest-") as temp_dir:
        broker = PaperBroker(Path(temp_dir) / "isolated.sqlite3", cfg)
        curve: list[float] = []
        for index, candle in enumerate(rows):
            close_ms = int(candle[0]) + timeframe_ms
            timestamp = datetime.fromtimestamp(close_ms / 1000, timezone.utc).isoformat()
            position = next((p for p in broker.open_positions()
                             if p["symbol"] == symbol), None)
            if position is not None:
                opened_ms = int(datetime.fromisoformat(
                    position["opened_at"].replace("Z", "+00:00")
                ).timestamp() * 1000)
                if close_ms > opened_ms:
                    broker.process_candle(symbol, timestamp, float(candle[2]),
                                           float(candle[3]), open_price=float(candle[1]))
            elif index >= start_index and not broker.has_trade_activity(symbol, timestamp):
                decision = analyze_symbol(symbol, rows[:index + 1])
                if decision.decision == "WATCH" and decision.proposal:
                    proposal = decision.proposal
                    try:
                        broker.open_position(symbol, float(proposal["reference_price"]),
                                             float(proposal["stop_loss"]),
                                             float(proposal["take_profit"]), timestamp)
                    except ValueError:
                        pass
            if broker.open_positions():
                equity = broker.record_equity({symbol: float(candle[4])}, timestamp)["equity"]
            else:
                equity = broker.account()["cash"]
            curve.append(equity)

        final_ms = int(rows[-1][0]) + timeframe_ms
        final_time = datetime.fromtimestamp(final_ms / 1000, timezone.utc).isoformat()
        if broker.open_positions():
            broker.close_position(symbol, float(rows[-1][4]), "end_of_backtest", final_time)
            curve[-1] = broker.account()["cash"]

        closed = [t for t in broker.trade_history() if t["side"] == "CLOSE"]
        result = _metrics(curve, closed, cfg.initial_balance)
        first, last = float(rows[0][4]), float(rows[-1][4])
        entry = first * (1 + cfg.slippage_rate)
        qty = cfg.initial_balance / (entry * (1 + cfg.fee_rate))
        exit_fill = last * (1 - cfg.slippage_rate)
        benchmark_end = qty * exit_fill * (1 - cfg.fee_rate)
        result["buy_and_hold"] = {
            "ending_equity": benchmark_end, "net_pnl": benchmark_end - cfg.initial_balance,
            "return_pct": (benchmark_end / cfg.initial_balance - 1) * 100,
        }
        result["data"] = {"symbol": symbol, "candles": len(rows),
                          "from_timestamp_ms": int(rows[0][0]),
                          "to_timestamp_ms": int(rows[-1][0]), "timeframe_ms": timeframe_ms}
        result["trades"] = closed
        result["equity_curve"] = curve
        result["execution_model"] = {
            "fee_rate": cfg.fee_rate, "slippage_rate": cfg.slippage_rate,
            "terminal_liquidation": True,
            "decision_timing": "signal at candle close; exits start on next candle",
        }
        return result
