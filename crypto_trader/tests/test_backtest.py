from crypto_trader.backtest import run_backtest, run_walk_forward
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
    candles.pop(75)
    with pytest.raises(ValueError, match="gaps"):
        run_backtest("BTC/USDT", candles, HOUR)


def test_backtest_requires_warmup_history():
    import pytest
    with pytest.raises(ValueError, match="60 available candles"):
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


def test_backtest_warmup_does_not_create_pre_window_trades(monkeypatch):
    import crypto_trader.backtest as module

    seen_lengths = []

    class Decision:
        decision = "NO_TRADE"
        proposal = None

    def inspect_prefix(symbol, history):
        seen_lengths.append(len(history))
        return Decision()

    monkeypatch.setattr(module, "analyze_symbol", inspect_prefix)
    result = run_backtest("BTC/USDT", rows(), HOUR, start_index=70, end_index=90)
    assert seen_lengths == list(range(71, 91))
    assert result["data"]["warmup_candles"] == 70
    assert result["data"]["candles"] == 20
    assert len(result["equity_curve"]) == 20


def test_walk_forward_uses_contiguous_non_overlapping_test_folds(monkeypatch):
    import crypto_trader.backtest as module

    seen_lengths = []

    class Decision:
        decision = "NO_TRADE"
        proposal = None

    def inspect_prefix(symbol, history):
        seen_lengths.append(len(history))
        return Decision()

    monkeypatch.setattr(module, "analyze_symbol", inspect_prefix)
    result = run_walk_forward("BTC/USDT", rows(), HOUR, folds=2)

    assert result["fold_count"] == 2
    assert result["total_test_candles"] == 40
    assert [f["test_candles"] for f in result["folds"]] == [20, 20]
    assert result["folds"][0]["to_timestamp_ms"] < result["folds"][1]["from_timestamp_ms"]
    assert seen_lengths == list(range(61, 81)) + list(range(81, 101))



def test_backtest_reports_realized_trade_diagnostics(monkeypatch):
    import crypto_trader.backtest as module

    class Decision:
        decision = "NO_TRADE"
        proposal = None

    monkeypatch.setattr(module, "analyze_symbol", lambda symbol, history: Decision())

    class FakeBroker:
        def __init__(self, *args, **kwargs):
            pass

        def open_positions(self):
            return []

        def account(self):
            return {"cash": 100.0}

        def record_equity(self, *args, **kwargs):
            return {"equity": 100.0}

        def trade_history(self):
            return [
                {"side": "CLOSE", "pnl": -0.5, "reason": "stop_loss"},
                {"side": "CLOSE", "pnl": 1.0, "reason": "take_profit"},
                {"side": "CLOSE", "pnl": -0.5, "reason": "stop_loss"},
            ]

    monkeypatch.setattr(module, "PaperBroker", FakeBroker)
    result = module.run_backtest("BTC/USDT", rows(), HOUR, PaperConfig())
    diagnostics = result["trade_diagnostics"]
    assert diagnostics["winning_trades"] == 1
    assert diagnostics["losing_trades"] == 2
    assert diagnostics["average_win_to_loss_ratio"] == 2.0
    assert diagnostics["break_even_win_rate_pct"] == pytest.approx(33.333333)
    assert diagnostics["actual_win_rate_pct"] == pytest.approx(33.333333)
    assert diagnostics["loss_streak_max"] == 1
    assert diagnostics["by_exit_reason"]["stop_loss"]["trades"] == 2
