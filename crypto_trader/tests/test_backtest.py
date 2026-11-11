from crypto_trader.backtest import run_backtest
from crypto_trader.paper import PaperConfig

HOUR = 3_600_000


def rows(count=100):
    out, price, start = [], 100.0, 1_800_000_000_000
    for i in range(count):
        price *= 1.002
        out.append([start + i * HOUR, price * .999, price * 1.003,
                    price * .997, price, 1000.0])
    return out


def test_backtest_reports_metrics_and_benchmark_in_isolated_simulation():
    result = run_backtest("BTC/USDT", rows(), HOUR, PaperConfig())
    assert result["data"]["candles"] == 100
    assert len(result["equity_curve"]) == 100
    assert "max_drawdown_pct" in result and "profit_factor" in result
    assert "buy_and_hold" in result
    assert result["execution_model"]["terminal_liquidation"] is True


def test_backtest_rejects_gaps():
    import pytest
    candles = rows()
    candles[75][0] += HOUR
    with pytest.raises(ValueError, match="gaps"):
        run_backtest("BTC/USDT", candles, HOUR)


def test_backtest_requires_warmup_history():
    import pytest
    with pytest.raises(ValueError, match="60 candles"):
        run_backtest("BTC/USDT", rows(59), HOUR)



def test_backtest_analysis_receives_only_closed_prefix(monkeypatch):
    import crypto_trader.backtest as module

    seen_lengths = []

    class Decision:
        decision = "NO_TRADE"
        proposal = None

    def inspect_prefix(symbol, history):
        seen_lengths.append(len(history))
        assert len(history) <= 100
        return Decision()

    monkeypatch.setattr(module, "analyze_symbol", inspect_prefix)
    module.run_backtest("BTC/USDT", rows(), HOUR, PaperConfig())
    assert seen_lengths == list(range(60, 101))
