"""Deterministic technical analysis used as the team's reproducible baseline."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Sequence


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    candles: int
    close: float
    ema_fast: float
    ema_slow: float
    rsi: float
    atr_pct: float
    trend: str
    momentum: str
    volatility: str


def _ema(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"need at least {period} values for EMA")
    alpha = 2.0 / (period + 1)
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _rsi(closes: Sequence[float], period: int = 14) -> float:
    if len(closes) <= period:
        raise ValueError(f"need more than {period} closes for RSI")
    changes = [b - a for a, b in zip(closes[:-1], closes[1:])]
    gains = [max(change, 0.0) for change in changes[-period:]]
    losses = [max(-change, 0.0) for change in changes[-period:]]
    avg_gain, avg_loss = sum(gains) / period, sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def analyze_candles(symbol: str, rows: Sequence[Sequence[float]]) -> MarketSnapshot:
    """Rows are chronological OHLCV records: timestamp, open, high, low, close, volume."""
    if len(rows) < 60:
        raise ValueError("at least 60 closed candles are required")
    closes = [float(row[4]) for row in rows]
    highs = [float(row[2]) for row in rows]
    lows = [float(row[3]) for row in rows]
    if any(not isfinite(v) or v <= 0 for v in closes + highs + lows):
        raise ValueError("prices must be finite and positive")
    fast, slow = _ema(closes, 20), _ema(closes, 50)
    rsi = _rsi(closes)
    true_ranges = []
    for i in range(1, len(rows)):
        true_ranges.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    atr = sum(true_ranges[-14:]) / min(14, len(true_ranges))
    close = closes[-1]
    spread = (fast / slow - 1.0) * 100
    trend = "bullish" if spread > 0.15 else "bearish" if spread < -0.15 else "sideways"
    momentum = "overbought" if rsi >= 70 else "oversold" if rsi <= 30 else "positive" if rsi >= 55 else "negative" if rsi <= 45 else "neutral"
    atr_pct = atr / close * 100
    volatility = "high" if atr_pct >= 3 else "low" if atr_pct <= 0.6 else "normal"
    return MarketSnapshot(symbol, len(rows), close, fast, slow, rsi, atr_pct, trend, momentum, volatility)
