from crypto_trader.storage import count_candles, save_candles


def test_save_and_upsert_are_idempotent(tmp_path):
    db = tmp_path / "market.sqlite3"
    rows = [[1_700_000_000_000, 100, 110, 90, 105, 2]]
    assert save_candles(db, "binance", "BTC/USDT", "1h", rows, 1_700_000_100_000) == 1
    assert save_candles(db, "binance", "BTC/USDT", "1h", rows, 1_700_000_200_000) == 1
    assert count_candles(db, "BTC/USDT", "1h") == 1
