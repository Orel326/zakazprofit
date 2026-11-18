"""Precommitted forward-window guardrails."""
from datetime import datetime, timezone

import pytest
from crypto_trader.research_forward import evaluate_forward, FORWARD_CUTOFF_UTC
from crypto_trader.research_portfolio import UNIVERSE

HOUR = 3600000
CUTOFF = int(datetime.fromisoformat(FORWARD_CUTOFF_UTC).timestamp() * 1000)


def candles(count, before=240):
    return {s: [[CUTOFF + (i-before)*HOUR, 100., 101., 99., 100., 1.]
                for i in range(count)] for s in UNIVERSE}


def test_forward_requires_new_observations():
    report = evaluate_forward(candles(240), HOUR)
    assert all(x["status"] == "awaiting_forward_candles"
               for x in report["portfolios"].values())


def test_forward_does_not_call_backtest_during_collection(monkeypatch):
    from crypto_trader import research_forward
    def forbidden(*args, **kwargs):
        raise AssertionError("must not backtest a too-short forward window")
    monkeypatch.setattr(research_forward, "portfolio_backtest", forbidden)
    report = evaluate_forward(candles(250), HOUR)
    assert all(x["status"] == "collecting"
               for x in report["portfolios"].values())


def test_forward_rejects_naive_or_non_utc_cutoff():
    with pytest.raises(ValueError, match="UTC"):
        evaluate_forward(candles(250), HOUR, cutoff_utc="2026-11-18T00:00:00")
    with pytest.raises(ValueError, match="UTC"):
        evaluate_forward(candles(250), HOUR, cutoff_utc="2026-11-18T03:00:00+03:00")


def test_forward_starts_after_cutoff_and_marks_low_trade_count(monkeypatch):
    from crypto_trader import research_forward
    calls = []
    def fake_backtest(subset, timeframe_ms, *, start_index, selection_size):
        calls.append((start_index, selection_size, len(subset)))
        return {"closed_trades": 0}
    monkeypatch.setattr(research_forward, "portfolio_backtest", fake_backtest)
    report = evaluate_forward(candles(240 + 91 * 24), HOUR)
    assert len(calls) == 5
    assert all(start == 240 for start, _, _ in calls)
    assert {size for _, size, _ in calls} == {None, 3, 5}
    assert all(x["status"] == "insufficient_trades"
               for x in report["portfolios"].values())


def test_forward_rejects_invalid_candles_during_collection():
    data = candles(250)
    data["BTC/USDT"][245][3] = -1.
    report = evaluate_forward(data, HOUR)
    assert report["portfolios"]["fixed_2"]["status"] == "rejected"
    assert report["portfolios"]["dynamic_3"]["status"] == "rejected"


def test_forward_rejects_misaligned_candles_during_collection():
    data = candles(250)
    for row in data["ETH/USDT"]:
        row[0] += HOUR
    report = evaluate_forward(data, HOUR)
    assert report["portfolios"]["fixed_2"]["status"] == "rejected"
    assert report["portfolios"]["dynamic_5"]["status"] == "rejected"
