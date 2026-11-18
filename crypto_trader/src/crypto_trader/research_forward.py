"""Precommitted forward-only paper evaluation; never a historical holdout.

The cutoff is fixed before observing subsequent candles. Warmup candles before
the cutoff may inform signals, but cannot contribute trades or PnL.
"""
from datetime import datetime, timezone

from .research_portfolio import UNIVERSE, portfolio_backtest
from .research_backtest import WARMUP
from .quality import validate_ohlcv

FORWARD_CUTOFF_UTC = "2026-11-18T00:00:00+00:00"
MIN_FORWARD_DAYS = 90
MIN_CLOSED_TRADES = 30


def evaluate_forward(data, timeframe_ms, *, cutoff_utc=FORWARD_CUTOFF_UTC):
    cutoff = datetime.fromisoformat(cutoff_utc)
    if cutoff.tzinfo is None or cutoff.utcoffset().total_seconds() != 0:
        raise ValueError("forward cutoff must be timezone-aware UTC")
    cutoff_ms = int(cutoff.timestamp() * 1000)
    configurations = (
        ("fixed_2", UNIVERSE[:2], None),
        ("fixed_5", UNIVERSE[:5], None),
        ("fixed_10", UNIVERSE, None),
        ("dynamic_3", UNIVERSE, 3),
        ("dynamic_5", UNIVERSE, 5),
    )
    results = {}
    for label, symbols, size in configurations:
        if not all(s in data for s in symbols):
            results[label] = {"status": "missing_symbols"}
            continue
        subset = {s: data[s] for s in symbols}
        # Reject malformed/misaligned histories even during collection.
        timestamps = []
        invalid = None
        for symbol, rows in subset.items():
            quality = validate_ohlcv(rows, timeframe_ms)
            if not quality.is_valid or quality.warnings:
                invalid = f"{symbol}: invalid/gapped candles"
                break
            timestamps.append(tuple(int(row[0]) for row in rows))
        if invalid is None and any(t != timestamps[0] for t in timestamps[1:]):
            invalid = "misaligned candle timestamps"
        if invalid:
            results[label] = {"status": "rejected", "reason": invalid}
            continue
        first = subset[symbols[0]]
        # No evaluation can use an incomplete candle or a pre-cutoff entry.
        # All market histories must have the same timestamps (validated by
        # portfolio_backtest before any result is accepted).
        start = next((i for i, row in enumerate(first)
                      if int(row[0]) >= cutoff_ms), len(first))
        if start < WARMUP["ema_adx"] or start >= len(first):
            results[label] = {"status": "awaiting_forward_candles"}
            continue
        # This duration is measured in fully closed bars, not wall-clock time.
        days = (int(first[-1][0]) + timeframe_ms - max(cutoff_ms, int(first[start][0]))) / 86400000
        if days < MIN_FORWARD_DAYS:
            results[label] = {"status": "collecting", "observed_days": round(days, 3),
                              "minimum_days": MIN_FORWARD_DAYS}
            continue
        report = portfolio_backtest(subset, timeframe_ms, start_index=start,
                                    selection_size=size)
        results[label] = {
            "status": "sufficient_observation" if report["closed_trades"] >= MIN_CLOSED_TRADES
                      else "insufficient_trades",
            "observed_days": round(days, 3),
            "minimum_trades": MIN_CLOSED_TRADES,
            "result": report,
        }
    return {"cutoff_utc": cutoff_utc, "precommitted": True,
            "note": "Forward paper-only; never optimized on post-cutoff observations. "
                    "Sufficient observation is not evidence of profitability.",
            "portfolios": results}
