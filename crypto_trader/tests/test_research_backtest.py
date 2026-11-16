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

def test_single_asset_fills_next_open_and_stops_on_entry_candle(monkeypatch):
    from crypto_trader.research_backtest import STRATEGIES
    from crypto_trader.research_strategies import Signal
    from crypto_trader import research_backtest as rb
    data = candles(225)
    data[220][1:5] = [100,101,99,100]
    data[221][1:5] = [95,96,89,93]
    def once(rows):
        return Signal("ema_adx",100,90,120) if len(rows)==221 else None
    monkeypatch.setitem(STRATEGIES,"ema_adx",once)
    prices = []
    original = rb.PaperBroker.open_position
    def spy(self,symbol,market_price,stop_loss,take_profit,occurred_at=None):
        prices.append((market_price,occurred_at))
        return original(self,symbol,market_price,stop_loss,take_profit,occurred_at)
    monkeypatch.setattr(rb.PaperBroker,"open_position",spy)
    report = simulate("BTC/USDT",data,HOUR,"ema_adx")
    assert len(prices)==1
    assert prices[0][0]==95
    assert prices[0][1].startswith("1970-01-10T05:00:00")
    assert report["closed_trades"]==1
    assert report["return_pct"]<0

def test_single_asset_last_candle_signal_never_fills(monkeypatch):
    from crypto_trader.research_backtest import STRATEGIES
    from crypto_trader.research_strategies import Signal
    data=candles(225)
    def only_last(rows):
        price=float(rows[-1][4])
        return Signal("ema_adx",price,price*.9,price*1.1) if len(rows)==225 else None
    monkeypatch.setitem(STRATEGIES,"ema_adx",only_last)
    result=simulate("BTC/USDT",data,HOUR,"ema_adx")
    assert result["closed_trades"]==0
    assert result["return_pct"]==0
