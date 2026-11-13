"""Chronological historical simulation isolated from the live paper account."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
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



def _trade_diagnostics(trades: list[dict]) -> dict:
    """Summarize realized outcomes without changing execution or strategy rules."""
    by_reason: dict[str, dict] = {}
    wins = [float(t["pnl"]) for t in trades if float(t["pnl"]) > 0]
    losses = [float(t["pnl"]) for t in trades if float(t["pnl"]) < 0]
    for trade in trades:
        reason = str(trade["reason"])
        item = by_reason.setdefault(reason, {"trades": 0, "net_pnl": 0.0})
        item["trades"] += 1
        item["net_pnl"] += float(trade["pnl"])
    average_win = mean(wins) if wins else 0.0
    average_loss = abs(mean(losses)) if losses else 0.0
    return {
        "by_exit_reason": by_reason,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "average_win": average_win,
        "average_loss": average_loss,
        "average_win_to_loss_ratio": average_win / average_loss if average_loss else None,
        "break_even_win_rate_pct": (
            average_loss / (average_loss + average_win) * 100
            if average_win + average_loss else None
        ),
        "actual_win_rate_pct": len(wins) / len(trades) * 100 if trades else 0.0,
        "loss_streak_max": _max_loss_streak(trades),
    }


def _max_loss_streak(trades: list[dict]) -> int:
    current = maximum = 0
    for trade in trades:
        if float(trade["pnl"]) < 0:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum


def _rsi_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 30:
        return "<30"
    if value < 45:
        return "30-45"
    if value < 55:
        return "45-55"
    if value < 70:
        return "55-70"
    return ">=70"


def _attach_trade_context(trade: dict, context: dict | None) -> dict:
    enriched = dict(trade)
    if context:
        enriched.update(context)
    return enriched


def _entry_diagnostics(trades: list[dict]) -> dict:
    """Group realized outcomes by the market state recorded at entry."""
    groups: dict[str, dict] = {}
    for trade in trades:
        snapshot = trade.get("entry_snapshot") or {}
        trend = str(snapshot.get("trend", "unknown"))
        rsi_bucket = _rsi_bucket(snapshot.get("rsi"))
        key = f"{trend}|RSI {rsi_bucket}"
        group = groups.setdefault(key, {
            "trades": 0, "wins": 0, "net_pnl": 0.0,
            "average_mfe_pct": [], "average_mae_pct": [],
        })
        pnl = float(trade["pnl"])
        group["trades"] += 1
        group["wins"] += int(pnl > 0)
        group["net_pnl"] += pnl
        if trade.get("max_favorable_excursion_pct") is not None:
            group["average_mfe_pct"].append(float(trade["max_favorable_excursion_pct"]))
        if trade.get("max_adverse_excursion_pct") is not None:
            group["average_mae_pct"].append(float(trade["max_adverse_excursion_pct"]))
    for group in groups.values():
        group["win_rate_pct"] = group["wins"] / group["trades"] * 100
        for field in ("average_mfe_pct", "average_mae_pct"):
            values = group[field]
            group[field] = mean(values) if values else None
    return groups


def run_backtest(
    symbol: str,
    rows: Sequence[Sequence[float]],
    timeframe_ms: int,
    config: PaperConfig | None = None,
    start_index: int = 0,
    end_index: int | None = None,
) -> dict:
    """Run one chronological test window with auditable entry/exit context.

    Indicators may use candles before start_index as warm-up, but no trades or
    equity observations are generated before it. end_index is exclusive.
    Decisions use no candle after the signal close; open positions are
    liquidated at the final candle close with normal costs. MFE/MAE only use
    candles after the entry close, avoiding entry-candle look-ahead.
    """
    end = len(rows) if end_index is None else end_index
    if end > len(rows) or end < 60:
        raise ValueError("backtest end_index must include at least 60 available candles")
    if start_index < 0 or start_index >= end:
        raise ValueError("start_index must identify a candle inside the backtest window")
    window = rows[:end]
    quality = validate_ohlcv(window, timeframe_ms)
    if not quality.is_valid:
        raise ValueError("invalid OHLCV data: " + "; ".join(quality.errors))
    if quality.warnings:
        raise ValueError("OHLCV data contains gaps: " + "; ".join(quality.warnings))
    cfg = config or PaperConfig()
    with tempfile.TemporaryDirectory(prefix="crypto-backtest-") as temp_dir:
        broker = PaperBroker(Path(temp_dir) / "isolated.sqlite3", cfg)
        curve: list[float] = []
        active_context: dict | None = None
        contexts_by_trade_id: dict[int, dict] = {}

        def save_latest_close_context() -> None:
            nonlocal active_context
            history = broker.trade_history()
            close_trade = next(
                (trade for trade in reversed(history) if trade["side"] == "CLOSE"),
                None,
            )
            if close_trade is not None and active_context is not None:
                contexts_by_trade_id[int(close_trade["id"])] = active_context
            active_context = None

        for index in range(start_index, end):
            candle = rows[index]
            close_ms = int(candle[0]) + timeframe_ms
            timestamp = datetime.fromtimestamp(close_ms / 1000, timezone.utc).isoformat()
            position = next((p for p in broker.open_positions()
                             if p["symbol"] == symbol), None)
            if position is not None:
                opened_ms = int(datetime.fromisoformat(
                    position["opened_at"].replace("Z", "+00:00")
                ).timestamp() * 1000)
                if close_ms > opened_ms:
                    if active_context is not None:
                        entry_price = float(active_context["entry_price"])
                        high, low = float(candle[2]), float(candle[3])
                        active_context["hold_bars"] += 1
                        active_context["max_favorable_excursion_pct"] = max(
                            float(active_context["max_favorable_excursion_pct"]),
                            (high / entry_price - 1) * 100,
                        )
                        active_context["max_adverse_excursion_pct"] = min(
                            float(active_context["max_adverse_excursion_pct"]),
                            (low / entry_price - 1) * 100,
                        )
                    broker.process_candle(
                        symbol, timestamp, float(candle[2]), float(candle[3]),
                        open_price=float(candle[1]),
                    )
                    if not broker.open_positions():
                        save_latest_close_context()
            elif index >= 59 and not broker.has_trade_activity(symbol, timestamp):
                decision = analyze_symbol(symbol, rows[:index + 1])
                if decision.decision == "WATCH" and decision.proposal:
                    proposal = decision.proposal
                    try:
                        opened = broker.open_position(
                            symbol, float(proposal["reference_price"]),
                            float(proposal["stop_loss"]), float(proposal["take_profit"]),
                            timestamp,
                        )
                        active_context = {
                            "entry_timestamp": timestamp,
                            "entry_price": float(opened["entry_price"]),
                            "entry_snapshot": dict(decision.snapshot),
                            "hold_bars": 0,
                            "max_favorable_excursion_pct": 0.0,
                            "max_adverse_excursion_pct": 0.0,
                        }
                    except ValueError:
                        pass
            if broker.open_positions():
                equity = broker.record_equity({symbol: float(candle[4])}, timestamp)["equity"]
            else:
                equity = broker.account()["cash"]
            curve.append(equity)

        final_candle = rows[end - 1]
        final_ms = int(final_candle[0]) + timeframe_ms
        final_time = datetime.fromtimestamp(final_ms / 1000, timezone.utc).isoformat()
        if broker.open_positions():
            if active_context is not None:
                entry_price = float(active_context["entry_price"])
                active_context["hold_bars"] += 1
                active_context["max_favorable_excursion_pct"] = max(
                    float(active_context["max_favorable_excursion_pct"]),
                    (float(final_candle[2]) / entry_price - 1) * 100,
                )
                active_context["max_adverse_excursion_pct"] = min(
                    float(active_context["max_adverse_excursion_pct"]),
                    (float(final_candle[3]) / entry_price - 1) * 100,
                )
            broker.close_position(symbol, float(final_candle[4]), "end_of_backtest", final_time)
            save_latest_close_context()
            curve[-1] = broker.account()["cash"]

        closed = [
            _attach_trade_context(t, contexts_by_trade_id.get(int(t.get("id", -1))))
            for t in broker.trade_history() if t["side"] == "CLOSE"
        ]
        result = _metrics(curve, closed, cfg.initial_balance)
        first, last = float(rows[start_index][4]), float(final_candle[4])
        entry = first * (1 + cfg.slippage_rate)
        qty = cfg.initial_balance / (entry * (1 + cfg.fee_rate))
        exit_fill = last * (1 - cfg.slippage_rate)
        benchmark_end = qty * exit_fill * (1 - cfg.fee_rate)
        result["buy_and_hold"] = {
            "ending_equity": benchmark_end, "net_pnl": benchmark_end - cfg.initial_balance,
            "return_pct": (benchmark_end / cfg.initial_balance - 1) * 100,
        }
        result["data"] = {
            "symbol": symbol, "candles": end - start_index,
            "warmup_candles": start_index, "total_candles_available": end,
            "from_timestamp_ms": int(rows[start_index][0]),
            "to_timestamp_ms": int(final_candle[0]), "timeframe_ms": timeframe_ms,
        }
        result["trades"] = closed
        result["trade_diagnostics"] = _trade_diagnostics(closed)
        result["entry_diagnostics"] = _entry_diagnostics(closed)
        result["equity_curve"] = curve
        result["execution_model"] = {
            "fee_rate": cfg.fee_rate, "slippage_rate": cfg.slippage_rate,
            "terminal_liquidation": True,
            "decision_timing": "signal at candle close; exits start on next candle",
            "excursion_measurement": "MFE/MAE use subsequent candle high/low only; entry candle excluded",
        }
        return result


def run_walk_forward(
    symbol: str,
    rows: Sequence[Sequence[float]],
    timeframe_ms: int,
    folds: int = 4,
    config: PaperConfig | None = None,
) -> dict:
    """Evaluate contiguous out-of-sample folds with expanding historical warm-up.

    Each fold starts with a fresh virtual account. Historical candles before a
    fold are available only to warm up indicators; they cannot create trades
    or affect that fold's starting equity.
    """
    if folds < 2:
        raise ValueError("walk-forward requires at least 2 folds")
    first_test_index = 60
    test_candles = len(rows) - first_test_index
    if test_candles < folds * 2:
        raise ValueError("not enough candles for requested walk-forward folds")
    boundaries = [
        first_test_index + (test_candles * i) // folds
        for i in range(folds + 1)
    ]
    results = []
    for fold_index in range(folds):
        start, end = boundaries[fold_index], boundaries[fold_index + 1]
        fold = run_backtest(
            symbol, rows[:end], timeframe_ms, config,
            start_index=start, end_index=end,
        )
        results.append({
            "fold": fold_index + 1,
            "warmup_candles": start,
            "test_candles": end - start,
            "from_timestamp_ms": fold["data"]["from_timestamp_ms"],
            "to_timestamp_ms": fold["data"]["to_timestamp_ms"],
            "return_pct": fold["return_pct"],
            "buy_and_hold_return_pct": fold["buy_and_hold"]["return_pct"],
            "closed_trades": fold["closed_trades"],
            "profit_factor": fold["profit_factor"],
            "max_drawdown_pct": fold["max_drawdown_pct"],
        })
    returns = [float(item["return_pct"]) for item in results]
    benchmarks = [float(item["buy_and_hold_return_pct"]) for item in results]
    return {
        "method": "expanding historical warm-up; contiguous out-of-sample test folds; fresh account per fold",
        "folds": results,
        "fold_count": folds,
        "total_test_candles": sum(item["test_candles"] for item in results),
        "total_closed_trades": sum(item["closed_trades"] for item in results),
        "mean_fold_return_pct": mean(returns),
        "median_fold_return_pct": median(returns),
        "mean_buy_and_hold_return_pct": mean(benchmarks),
        "profitable_folds": sum(value > 0 for value in returns),
        "folds_beating_buy_and_hold": sum(
            returns[i] > benchmarks[i] for i in range(folds)
        ),
    }
