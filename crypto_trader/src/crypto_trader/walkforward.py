"""Rolling-origin out-of-sample evaluation for the fixed trading rules.

This project currently has no fitted strategy parameters. The preceding history is
therefore an indicator warm-up/training context, not a parameter-optimization set.
Only candles in each test interval may create trades.
"""
from __future__ import annotations

from statistics import mean, median
from typing import Sequence

from .backtest import run_backtest
from .paper import PaperConfig
from .quality import validate_ohlcv


def run_walk_forward(
    symbol: str,
    rows: Sequence[Sequence[float]],
    timeframe_ms: int,
    train_candles: int = 500,
    test_candles: int = 168,
    step_candles: int | None = None,
    config: PaperConfig | None = None,
) -> dict:
    """Evaluate consecutive unseen test windows with an expanding history prefix."""
    step = test_candles if step_candles is None else step_candles
    if not isinstance(train_candles, int) or train_candles < 60:
        raise ValueError("train_candles must be an integer >= 60")
    if not isinstance(test_candles, int) or test_candles < 1:
        raise ValueError("test_candles must be a positive integer")
    if not isinstance(step, int) or step < 1:
        raise ValueError("step_candles must be a positive integer")
    if len(rows) <= train_candles:
        raise ValueError(
            f"need more than train_candles ({train_candles}) rows; received {len(rows)}"
        )
    quality = validate_ohlcv(rows, timeframe_ms)
    if not quality.is_valid:
        raise ValueError("invalid OHLCV data: " + "; ".join(quality.errors))
    if quality.warnings:
        raise ValueError("OHLCV data contains gaps: " + "; ".join(quality.warnings))

    cfg = config or PaperConfig()
    folds: list[dict] = []
    test_start = train_candles
    fold_number = 1
    while test_start < len(rows):
        test_end = min(test_start + test_candles, len(rows))
        # Expanding prefix: indicators see all history up to each test candle.
        # run_backtest blocks all entries before test_start.
        prefix = rows[:test_end]
        result = run_backtest(
            symbol, prefix, timeframe_ms, cfg, start_index=test_start
        )
        test_rows = rows[test_start:test_end]
        first, last = float(test_rows[0][4]), float(test_rows[-1][4])
        entry = first * (1 + cfg.slippage_rate)
        quantity = cfg.initial_balance / (entry * (1 + cfg.fee_rate))
        exit_fill = last * (1 - cfg.slippage_rate)
        benchmark_end = quantity * exit_fill * (1 - cfg.fee_rate)
        benchmark = {
            "ending_equity": benchmark_end,
            "net_pnl": benchmark_end - cfg.initial_balance,
            "return_pct": (benchmark_end / cfg.initial_balance - 1) * 100,
        }
        folds.append({
            "fold": fold_number,
            "training_history": {
                "candles": test_start,
                "from_timestamp_ms": int(rows[0][0]),
                "through_timestamp_ms": int(rows[test_start - 1][0]),
            },
            "out_of_sample": {
                "candles": test_end - test_start,
                "from_timestamp_ms": int(test_rows[0][0]),
                "through_timestamp_ms": int(test_rows[-1][0]),
            },
            "metrics": {
                key: result[key] for key in (
                    "initial_equity", "ending_equity", "net_pnl", "return_pct",
                    "closed_trades", "win_rate_pct", "profit_factor",
                    "expectancy_per_trade", "max_drawdown_pct"
                )
            },
            "buy_and_hold": benchmark,
            "trades": result["trades"],
            "equity_curve": result["equity_curve"][test_start:test_end],
        })
        fold_number += 1
        test_start += step

    returns = [float(f["metrics"]["return_pct"]) for f in folds]
    drawdowns = [float(f["metrics"]["max_drawdown_pct"]) for f in folds]
    return {
        "symbol": symbol,
        "method": "rolling_origin_expanding_history_fixed_strategy",
        "strategy_parameters_fitted": False,
        "limitations": [
            "The strategy currently has no trainable parameters; training windows provide indicator history only.",
            "Each fold starts with a fresh virtual balance; fold returns are not compounded.",
            "OHLCV execution remains candle-based and cannot model intrabar order, spread, depth, or latency."
        ],
        "settings": {
            "timeframe_ms": timeframe_ms,
            "initial_balance": cfg.initial_balance,
            "fee_rate": cfg.fee_rate,
            "slippage_rate": cfg.slippage_rate,
            "initial_training_history_candles": train_candles,
            "test_candles": test_candles,
            "step_candles": step,
        },
        "aggregate": {
            "folds": len(folds),
            "total_closed_trades": sum(f["metrics"]["closed_trades"] for f in folds),
            "mean_fold_return_pct": mean(returns),
            "median_fold_return_pct": median(returns),
            "worst_fold_return_pct": min(returns),
            "mean_fold_max_drawdown_pct": mean(drawdowns),
            "worst_fold_max_drawdown_pct": max(drawdowns),
        },
        "folds": folds,
    }
