from pathlib import Path

import pytest

from crypto_trader.paper import PaperBroker, PaperConfig


def broker(tmp_path: Path, **overrides) -> PaperBroker:
    values = {"initial_balance": 100.0, "fee_rate": 0.001,
              "slippage_rate": 0.0005, "risk_per_trade": 0.005,
              "max_total_open_risk": 0.02, "max_open_positions": 2,
              "daily_loss_limit": 0.02}
    values.update(overrides)
    return PaperBroker(tmp_path / "paper.sqlite3", PaperConfig(**values))


def test_account_is_persistent_and_initializes_only_once(tmp_path):
    first = broker(tmp_path)
    first.open_position("BTC/USDT", 100, 95, 110, "2026-11-01T10:00:00+00:00")
    second = broker(tmp_path, initial_balance=99_000)
    assert second.account()["initial_balance"] == 100
    assert len(second.open_positions()) == 1


def test_open_and_close_apply_fees_and_slippage(tmp_path):
    paper = broker(tmp_path)
    opened = paper.open_position("BTC/USDT", 100, 95, 110, "2026-11-01T10:00:00+00:00")
    assert opened["entry_price"] > 100
    assert opened["entry_fee"] > 0
    closed = paper.close_position("BTC/USDT", 110, "manual", "2026-11-01T11:00:00+00:00")
    assert closed["exit_price"] < 110
    assert closed["net_pnl"] > 0
    assert paper.open_positions() == []
    assert [t["side"] for t in paper.trade_history()] == ["LONG", "CLOSE"]


def test_stop_loss_wins_if_stop_and_target_hit_same_candle(tmp_path):
    paper = broker(tmp_path)
    paper.open_position("ETH/USDT", 100, 95, 110, "2026-11-01T10:00:00+00:00")
    result = paper.process_candle("ETH/USDT", "2026-11-01T11:00:00+00:00", 112, 94)
    assert result is not None
    assert result["reason"] == "stop_loss"
    assert result["exit_price"] < 95


def test_gap_through_stop_fills_at_open_not_stop(tmp_path):
    paper = broker(tmp_path)
    paper.open_position("BTC/USDT", 100, 95, 110, "2026-11-01T10:00:00+00:00")
    result = paper.process_candle(
        "BTC/USDT", "2026-11-01T11:00:00+00:00",
        high=101, low=89, open_price=90,
    )
    assert result["reason"] == "stop_loss_gap"
    assert result["exit_price"] < 90


def test_favorable_gap_target_is_filled_at_target(tmp_path):
    paper = broker(tmp_path)
    paper.open_position("ETH/USDT", 100, 95, 110, "2026-11-01T10:00:00+00:00")
    result = paper.process_candle(
        "ETH/USDT", "2026-11-01T11:00:00+00:00",
        high=120, low=115, open_price=115,
    )
    assert result["reason"] == "take_profit"
    assert result["exit_price"] < 110


def test_rejects_duplicate_symbol_and_unsupported_symbol(tmp_path):
    paper = broker(tmp_path)
    paper.open_position("BTC/USDT", 100, 95, 110)
    with pytest.raises(ValueError, match="already open"):
        paper.open_position("BTC/USDT", 101, 96, 111)
    with pytest.raises(ValueError, match="unsupported"):
        paper.open_position("DOGE/USDT", 1, 0.9, 1.1)


def test_rejects_invalid_stop_and_missing_mark_price(tmp_path):
    paper = broker(tmp_path)
    with pytest.raises(ValueError, match="stop_loss"):
        paper.open_position("BTC/USDT", 100, 105, 110)
    paper.open_position("BTC/USDT", 100, 95, 110)
    with pytest.raises(ValueError, match="missing mark price"):
        paper.record_equity({})


def test_max_open_positions_is_enforced(tmp_path):
    paper = broker(tmp_path, max_open_positions=1, max_total_open_risk=0.1)
    paper.open_position("BTC/USDT", 100, 95, 110)
    with pytest.raises(ValueError, match="maximum open positions"):
        paper.open_position("ETH/USDT", 100, 95, 110)
