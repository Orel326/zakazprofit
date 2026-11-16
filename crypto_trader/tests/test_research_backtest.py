"""Research simulator invariants; offline deterministic candles."""
from crypto_trader.research_backtest import evaluate, simulate

HOUR = 3600000

def candles(count=300):
    # Small deterministic upward drift, valid gap-free OHLCV.
    return [[i*HOUR, 100+i*.1, 101+i*.1, 99+i*.1, 100+i*.1, 10] for i in range(count)]

def test_donchian_runs_and_has_benchmark():
    report = simulate("BTC/USDT", candles(), HOUR, "donchian_atr")
    assert report["closed_trades"] >= 0
    assert report["ending_equity"] > 0
    assert "buy_and_hold_return_pct" in report

def test_ema_runs_without_future_data():
    history = candles()
    a = simulate("BTC/USDT", history[:250], HOUR, "ema_adx")
    b = simulate("BTC/USDT", history[:250], HOUR, "ema_adx")
    assert a["return_pct"] == b["return_pct"]

def test_folds_cover_test_range_without_overlap():
    report = evaluate("ETH/USDT", candles(), HOUR, "donchian_atr", folds=4)
    segments = report["folds"]
    assert len(segments) == 4
    assert all(segments[i]["to_timestamp_ms"] < segments[i+1]["from_timestamp_ms"]
               for i in range(3))
    assert report["profitable_folds"] <= 4

def test_flat_market_produces_no_trades():
    data = [[i*HOUR, 100, 101, 99, 100, 10] for i in range(300)]
    result = simulate("BTC/USDT", data, HOUR, "ema_adx")
    assert result["closed_trades"] == 0
    assert result["return_pct"] == 0
