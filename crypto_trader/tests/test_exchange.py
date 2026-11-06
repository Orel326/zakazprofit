from crypto_trader.exchange import fetch_closed_ohlcv


class FakeExchange:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe, since, limit):
        self.calls.append(since)
        return [row for row in self.rows if row[0] >= since][:limit]


def test_fetch_excludes_unclosed_candle():
    hour = 3_600_000
    rows = [[0, 100, 110, 90, 105, 1], [hour, 105, 115, 100, 112, 2]]
    result = fetch_closed_ohlcv(FakeExchange(rows), "BTC/USDT", "1h", 0, now_ms=hour + 10)
    assert [row[0] for row in result] == [0]


def test_fetch_rejects_unknown_symbol():
    try:
        fetch_closed_ohlcv(FakeExchange([]), "DOGE/USDT", "1h", 0, now_ms=10_000_000)
    except ValueError as exc:
        assert "Unsupported symbol" in str(exc)
    else:
        raise AssertionError("Expected unsupported symbol to be rejected")
