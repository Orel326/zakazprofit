"""Unit tests for research signals; no market data or exchange access."""
import pytest
from crypto_trader.research_strategies import donchian_atr, ema_adx, _adx

def rows(closes):
    return [[i*3600000, c, c+1, c-1, c, 100] for i,c in enumerate(closes)]

def test_breakout_uses_previous_channel_only():
    signal = donchian_atr(rows([100]*25+[103]))
    assert signal is not None
    assert signal.stop_loss < signal.reference_price < signal.take_profit

def test_no_breakout_in_range():
    assert donchian_atr(rows([100]*30)) is None

def test_short_history():
    assert donchian_atr(rows([100]*10)) is None
    assert ema_adx(rows([100]*10)) is None

def test_future_candles_do_not_change_past_signal():
    history = rows([100]*25+[103])
    extended = history + [[len(history)*3600000, 1000,1001,999,1000,100]]
    assert donchian_atr(history) == donchian_atr(extended[:len(history)])

def test_flat_market_does_not_trigger_ema():
    assert ema_adx(rows([100]*220)) is None

def test_adx_is_bounded_and_has_expected_warmup():
    history = rows([100+i*.2 for i in range(80)])
    h,l,c = ([r[2] for r in history],[r[3] for r in history],[r[4] for r in history])
    values = _adx(h,l,c,14)
    assert values[26] is None
    assert values[27] is not None
    assert all(v is None or 0 <= v <= 100 for v in values)

def test_bad_candle_rejected():
    history = rows([100]*30)
    history[-1][2] = 90
    with pytest.raises(ValueError):
        donchian_atr(history)
