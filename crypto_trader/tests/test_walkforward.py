from crypto_trader.walkforward import run_walk_forward
from crypto_trader.paper import PaperConfig

HOUR = 3_600_000


def rows(count=900):
    out, price, start = [], 100.0, 1_800_000_000_000
    for i in range(count):
        price *= 1.0005
        out.append([start + i * HOUR, price * .999, price * 1.003,
                    price * .997, price, 1000.0])
    return out


def test_walk_forward_creates_sequential_out_of_sample_folds(monkeypatch):
    import crypto_trader.backtest as backtest

    seen = []

    class Decision:
        decision = "NO_TRADE"
        proposal = None

    def inspect_prefix(symbol, history):
        seen.append(len(history))
        return Decision()

    monkeypatch.setattr(backtest, "analyze_symbol", inspect_prefix)
    result = run_walk_forward(
        "BTC/USDT", rows(), HOUR, train_candles=600,
        test_candles=100, step_candles=100, config=PaperConfig()
    )
    assert [f["out_of_sample"]["candles"] for f in result["folds"]] == [100, 100, 100]
    assert [f["training_history"]["candles"] for f in result["folds"]] == [600, 700, 800]
    assert result["aggregate"]["folds"] == 3
    assert result["strategy_parameters_fitted"] is False
    assert min(seen) == 601
    assert max(seen) == 900


def test_walk_forward_does_not_create_trades_during_training(monkeypatch):
    import crypto_trader.backtest as backtest

    seen = []

    class Decision:
        decision = "NO_TRADE"
        proposal = None

    def inspect_prefix(symbol, history):
        seen.append(len(history))
        return Decision()

    monkeypatch.setattr(backtest, "analyze_symbol", inspect_prefix)
    result = run_walk_forward(
        "BTC/USDT", rows(650), HOUR, train_candles=600,
        test_candles=50, config=PaperConfig()
    )
    assert seen == list(range(601, 651))
    assert result["folds"][0]["out_of_sample"]["candles"] == 50


def test_walk_forward_rejects_insufficient_history():
    import pytest
    with pytest.raises(ValueError, match="need more than train_candles"):
        run_walk_forward("BTC/USDT", rows(60), HOUR, train_candles=60)


def test_walk_forward_rejects_invalid_window_settings():
    import pytest
    with pytest.raises(ValueError, match="train_candles"):
        run_walk_forward("BTC/USDT", rows(), HOUR, train_candles=59)
