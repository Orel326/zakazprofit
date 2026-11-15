from crypto_trader.storage import (count_candles, count_team_decisions,
                                   save_candles, save_team_decision)


def test_save_and_upsert_are_idempotent(tmp_path):
    db = tmp_path / "market.sqlite3"
    rows = [[1_700_000_000_000, 100, 110, 90, 105, 2]]
    assert save_candles(db, "binance", "BTC/USDT", "1h", rows, 1_700_000_100_000) == 1
    assert save_candles(db, "binance", "BTC/USDT", "1h", rows, 1_700_000_200_000) == 1
    assert count_candles(db, "BTC/USDT", "1h") == 1


def test_team_decisions_are_persisted_and_upserted(tmp_path):
    db = tmp_path / "market.sqlite3"
    payload = {
        "decision": "NO_TRADE",
        "reason": "No setup",
        "snapshot": {"close": 100.0},
        "proposal": None,
        "risk_approved": False,
        "reports": [{"agent": "risk_manager", "status": "veto"}],
    }
    save_team_decision(db, "okx", "BTC/USDT", "1h", 123, payload)
    save_team_decision(db, "okx", "BTC/USDT", "1h", 123, payload)
    assert count_team_decisions(db, "BTC/USDT", "1h") == 1
